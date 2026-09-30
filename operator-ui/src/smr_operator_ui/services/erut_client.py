"""ERUT Robot Service(로봇 브릿지)와 주고받는 MQTT 전송 계층.

규격은 `mqtt_test/ERUT_검사로봇_MQTT_표준인터페이스_if-0.4.xlsx` 다(탭1~5·8~10.
탭6·7 은 ERUT 내부 규격이라 3S 와 무관하다). 기존 `MqttServer`가
다루는 `doosan/robot/req/{mc_cmd,job_cmd,...}` 와는 **봉투 구조가 다르다.**

    ERUT   : {"timestamp": 1786500000000, "content": {...}}   timestamp 는 숫자
    doosan : {"timestamp": "1786500000000", ...}              timestamp 는 문자열

같은 `doosan/robot/req/` 접두어를 쓰지만 동작명이 갈리고 규격도 달라서,
둘을 한 클래스에 섞지 않고 접속부터 따로 둔다. 이쪽이 협력사로 나가는 규격이다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# ERUT 가 보내오는 동작. 토픽 끝부분으로 구분한다.
# 앞의 9개가 표준(if-0.4 탭5 action)이다. 뒤는 표준에 아직 없는 것:
#   home      — 홈 이동. 3S 가 요청해 ERUT 가 다음 판에 넣기로 했다.
#   mark_next — 마커가 ERUT 것일 때 다음 점으로 가라는 신호(탭2 초안 「마킹 주체」).
ACTIONS = (
    "calibrate", "prepare", "start", "pause", "resume",
    "abort", "reset", "mark", "query",
    "home", "mark_next",
)

#: 같은 `doosan/robot/req/` 아래에 오는 **사내 MC 규격** 토픽. ERUT 요청이
#: 아니므로 여기서는 못 본 척한다(501 로 답하면 안 된다 — 남의 요청이다).
#: reset 은 두 규격이 같은 이름을 쓰는데, MC 쪽은 content 래퍼가 없어 걸러진다.
MC_ACTIONS = frozenset({
    "mc_cmd", "job_cmd", "job_clear", "ems", "speed", "probe_ack", "mark_cmd",
})

# 활동 상태 8값 (if-0.4 탭5 activity). evt/status 의 state 는 online/offline 만 쓴다.
ACTIVITIES = ("idle", "calibrating", "preparing", "ready", "running",
              "paused", "error", "estop")

REQ_PREFIX = "doosan/robot/req/"
ERUT_STATUS = "erut/status"
#: 브릿지가 5초마다 내는 erut/status 가 이만큼 끊기면 없는 것으로 본다(탭2 18행).
#: 브릿지 프로그램이 멈춰 있으면(꺼지지는 않아) 유언이 안 뜨기 때문이다.
ERUT_SILENCE_S = 20.0

# 시험용 주입 토픽. 실제 장애 수집이 아직 없어서, 알람/에러 시험 도구가
# 이걸로 찔러 주면 RCS 가 **규격대로 evt/error 를 발행**한다.
# 실제 장애 수집(PLC·로봇 알람)이 붙으면 이 토픽은 빼도 된다.
TEST_INJECT = "3s/test/inject/error"


def utc_ms() -> int:
    """ERUT 규격의 timestamp — UTC 밀리초 **숫자**."""
    return int(time.time() * 1000)


@dataclass(frozen=True)
class ErutConfig:
    """브로커 접속 정보. 탭2 초안 「접속」: MQTT 3.1.1 · 클라이언트 ID 는 로봇마다
    하나로 고정 · keepalive 10~20초 · cleanSession=true."""

    host: str = "127.0.0.1"
    port: int = 1883
    device_id: str = "robot1"
    keep_alive: int = 15
    client_id: str = ""

    def __post_init__(self) -> None:
        # 실행할 때마다 바뀌면 브로커가 매번 새 장비로 본다 — 장치 ID 로 고정한다.
        if not self.client_id:
            object.__setattr__(self, "client_id", f"3s-{self.device_id}")


class ErutClient(QObject):
    """ERUT Topic 송수신. 프로토콜 판단은 하지 않고 봉투만 씌우고 벗긴다."""

    # (action, content) — 받은 요청. 판단은 ErutSession 이 한다.
    request_received = pyqtSignal(str, object)
    # ERUT 자신의 생존 상태 (erut/status). offline 이면 진행 중 작업을 멈춰야 한다.
    erut_online_changed = pyqtSignal(bool)
    # 시험 도구가 넣어 준 장애. RCS 가 이걸 받아 evt/error 를 발행한다.
    test_error_injected = pyqtSignal(object)
    connected_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    activity = pyqtSignal(str)

    def __init__(self, config: ErutConfig | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config or ErutConfig()
        self._client: Any | None = None
        self._connected = False
        self._erut_online: bool | None = None
        # 마지막으로 erut/status 를 받은 때. 20초 넘게 조용하면 offline 으로 본다.
        self._erut_seen = 0.0
        self._silence_timer = QTimer(self)
        self._silence_timer.setInterval(1000)
        self._silence_timer.timeout.connect(self._check_erut_silence)

    # ------------------------------------------------------------ 토픽
    @property
    def device_id(self) -> str:
        return self.config.device_id

    def res_topic(self) -> str:
        return f"erut/{self.device_id}/res"

    def evt_topic(self, name: str) -> str:
        return f"erut/{self.device_id}/evt/{name}"

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------ 연결
    def start(self) -> None:
        if self._client is not None:
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            self.error_occurred.emit(f"paho-mqtt 가 없습니다: {exc}")
            return

        # 끊긴 동안 쌓인 옛 요청을 다시 붙자마자 받지 않게 cleanSession=true.
        kwargs: dict[str, Any] = {"client_id": self.config.client_id,
                                  "clean_session": True}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(**kwargs)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # 접속이 끊기면 브로커가 대신 offline 을 남긴다. retain 을 꼭 건다 —
        # 안 걸면 전원이 나간 뒤 새로 붙은 쪽이 마지막 online 을 믿는다(탭0).
        # LWT 는 1회성이라 프로그램이 굳은 경우는 못 잡으므로 evt/status 를
        # 5초 안팎마다 다시 낸다 — 상대는 이 갱신이 끊기는 것으로 굳음을 안다.
        client.will_set(
            self.evt_topic("status"),
            payload=json.dumps({"timestamp": utc_ms(), "state": "offline"},
                               ensure_ascii=False, separators=(",", ":")),
            qos=1, retain=True,
        )
        try:
            client.connect_async(self.config.host, self.config.port,
                                 self.config.keep_alive)
            client.loop_start()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"ERUT 브로커 연결 실패: {exc}")
            return
        self._client = client
        self._silence_timer.start()

    def apply_config(self, config: ErutConfig) -> bool:
        """ERUT 브로커 접속 정보를 바꾼다. 붙어 있으면 다시 붙는다.

        같은 값이면 아무것도 하지 않는다 — 다시 붙을 때마다 evt/status 를
        새로 내보내게 되고, ERUT 쪽에서는 RCS 가 껐다 켜진 것으로 보인다.
        """
        if config == self.config:
            return False
        running = self._client is not None
        if running:
            self.stop()
        self.config = config
        if running:
            self.start()
        return True

    def stop(self) -> None:
        self._silence_timer.stop()
        client, self._client = self._client, None
        if client is None:
            return
        # 정상 종료는 offline 을 직접 발행한다 (LWT 는 비정상 종료용).
        try:
            self._publish(self.evt_topic("status"),
                          {"timestamp": utc_ms(), "state": "offline"}, retain=True,
                          client=client)
            client.disconnect()
            client.loop_stop()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"ERUT 연결 종료 실패: {exc}")
        finally:
            self._set_connected(False)

    # ------------------------------------------------------------ 통신 기록
    #: (방향, 토픽, 원문) 을 받는 함수. app.py 가 운영 기록에 잇는다.
    #: MQTT 수신 스레드에서도 불린다.
    traffic: Any = None

    def _note_traffic(self, direction: str, topic: str, payload: Any) -> None:
        if self.traffic is None:
            return
        try:
            self.traffic(direction, topic, payload)
        except Exception:  # noqa: BLE001 — 기록 실패가 통신을 막으면 안 된다
            pass

    # ------------------------------------------------------------ 발행
    def _publish(self, topic: str, payload: dict, qos: int = 1,
                 retain: bool = False, client: Any = None) -> bool:
        client = client or self._client
        if client is None or not self._connected:
            return False
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            client.publish(topic, text, qos=qos, retain=retain)
            self._note_traffic("발신", topic, text)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"ERUT 발행 실패 ({topic}): {exc}")
            return False
        return True

    def publish_res(self, req_id: str, action: str, code: int,
                    message: str, **extra) -> bool:
        """요청 응답. 1초 이내에 보내야 한다."""
        content = {"req_id": req_id, "action": action}
        content.update(extra)
        return self._publish(self.res_topic(), {
            "timestamp": utc_ms(), "code": code,
            "message": message, "content": content,
        })

    def publish_event(self, name: str, req_id: str, action: str,
                      code: int = 200, message: str = "OK", **extra) -> bool:
        """ready / complete / error 처럼 code 를 갖는 이벤트."""
        content = {"req_id": req_id, "action": action}
        content.update(extra)
        return self._publish(self.evt_topic(name), {
            "timestamp": utc_ms(), "code": code,
            "message": message, "content": content,
        })

    def publish_progress(self, req_id: str, action: str, **extra) -> bool:
        """진행률. QoS 0 이라 유실돼도 검사 판정에 영향이 없다."""
        content = {"req_id": req_id, "action": action}
        content.update(extra)
        return self._publish(self.evt_topic("progress"),
                             {"timestamp": utc_ms(), "content": content}, qos=0)

    def publish_status(self, state: str = "online", **fields) -> bool:
        """장치 상태 (탭2 11행). **모든 칸이 맨 바깥**이다(content 래퍼 없음).

        state 는 online/offline 만 — 유언(LWT)이 쓰는 칸이라 활동 상태는
        `activity` 로 따로 싣는다. 값이 None 인 칸은 싣지 않는다 — 배터리가
        없는 장비는 battery 를 0 으로 채우지 말고 빼야 한다(탭5 56행).
        """
        payload: dict[str, Any] = {"timestamp": utc_ms(), "state": state}
        payload.update({k: v for k, v in fields.items() if v is not None})
        return self._publish(self.evt_topic("status"), payload, qos=1, retain=True)

    def publish_contact(self, state: str, job_id: str) -> bool:
        """탐촉자 접촉 (탭2 12행, probe_contact). ERUT 가 이것으로 물을 켜고 끈다.

        retain 이라 앞 구역 것이 남는다 — job_id 를 꼭 싣는다(받는 쪽이 견준다).
        """
        return self._publish(self.evt_topic("contact"), {
            "timestamp": utc_ms(),
            "content": {"state": state, "job_id": job_id},
        }, qos=1, retain=True)

    def publish_info(self, content: dict) -> bool:
        """장비 자기소개 (탭8 evt/info). 접속 시 + 바뀔 때, retain."""
        return self._publish(self.evt_topic("info"),
                             {"timestamp": utc_ms(), "content": dict(content)},
                             qos=1, retain=True)

    def publish_message(self, code: str, message: str, text: str,
                        **extra) -> bool:
        """안내 알림 (규격 20260914 evt/message). 장애가 아니다.

        evt/error 와 달리 발생·해제 쌍이 없고 ERUT 동작을 바꾸지 않는다 —
        text 를 표시·기록만 한다. 봉투는 evt/error 와 같게 `code`(Mxxxx)·
        `message` 가 최상위, 보여 줄 문장 `text` 는 content 안이다.
        요청 때문에 낸 알림이면 extra 로 req_id·action 을 싣는다.
        """
        content = {"text": text}
        content.update(extra)
        return self._publish(self.evt_topic("message"), {
            "timestamp": utc_ms(), "code": code,
            "message": message, "content": content,
        })

    def publish_error(self, code: str, message: str, level: str,
                      recovery: str, cleared: bool = False, **extra) -> bool:
        """장애·위험 통보. 요청 실패는 res 로 보내고 여기 쓰지 않는다.

        봉투가 res/complete 와 같다 — `code`·`message` 는 **최상위**이고
        `level`·`recovery`·`cleared`·`detail` 은 content 안이다 (탭2 10행).
        해제는 발생 때와 **같은 code** 에 `cleared=true` 다(if-0.3 부터). code
        끝에 `-CLEAR` 를 붙이던 방식은 받는 쪽이 새 장애로 읽는다.
        """
        content = {"level": level, "recovery": recovery, "cleared": bool(cleared)}
        content.update(extra)
        return self._publish(self.evt_topic("error"), {
            "timestamp": utc_ms(), "code": code,
            "message": message, "content": content,
        })

    # ------------------------------------------------------------ 콜백
    def _on_connect(self, client, _userdata, _flags, rc) -> None:  # noqa: ANN001
        if rc != 0:
            self.error_occurred.emit(f"ERUT 브로커 연결 거부 (rc={rc})")
            return
        # 한 줄로 받는다(탭2 17행) — 모르는 동작에도 501 로 답해야 해서다.
        # 아는 것만 구독하면 모르는 동작은 아예 안 들어와 브릿지가 세 번
        # 다시 보낸 뒤 통신 오류로 본다.
        client.subscribe(f"{REQ_PREFIX}#", qos=1)
        client.subscribe(ERUT_STATUS, qos=1)
        client.subscribe(TEST_INJECT, qos=1)
        self._set_connected(True)

    def _on_disconnect(self, _client, _userdata, rc) -> None:  # noqa: ANN001
        self._set_connected(False)

    def _on_message(self, _client, _userdata, msg) -> None:  # noqa: ANN001
        self._note_traffic("수신", msg.topic, msg.payload)
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.error_occurred.emit(f"ERUT 페이로드 해석 실패 ({msg.topic}): {exc}")
            return

        if msg.topic == TEST_INJECT:
            if isinstance(payload, dict):
                self.test_error_injected.emit(payload)
            return

        if msg.topic == ERUT_STATUS:
            online = str(payload.get("state", "")).lower() == "online"
            if online:
                self._erut_seen = time.monotonic()
            self._set_erut_online(online)
            return

        if not msg.topic.startswith(REQ_PREFIX):
            return
        action = msg.topic[len(REQ_PREFIX):]
        if action in MC_ACTIONS or not isinstance(payload, dict):
            return
        # 모르는 동작도 세션까지 보낸다 — 세션이 501 NOT_IMPLEMENTED 로 답한다.
        content = payload.get("content")
        if not isinstance(content, dict):
            self.error_occurred.emit(f"ERUT 요청에 content 가 없습니다: {action}")
            return
        self.request_received.emit(action, content)

    def _set_erut_online(self, online: bool) -> None:
        if online != self._erut_online:
            self._erut_online = online
            self.erut_online_changed.emit(online)

    def _check_erut_silence(self) -> None:
        """5초마다 오던 erut/status 가 20초 넘게 끊기면 브릿지 없음으로 본다."""
        if self._erut_online and time.monotonic() - self._erut_seen > ERUT_SILENCE_S:
            self.activity.emit(
                f"ERUT 브릿지 상태가 {ERUT_SILENCE_S:.0f}초 넘게 오지 않습니다 — 없는 것으로 봅니다.")
            self._set_erut_online(False)

    def _set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        self.connected_changed.emit(connected)
        self.activity.emit(
            "ERUT 브로커에 연결되었습니다." if connected else "ERUT 브로커 연결이 끊겼습니다."
        )
