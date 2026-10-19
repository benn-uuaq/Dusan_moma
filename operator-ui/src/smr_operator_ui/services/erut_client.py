"""ERUT Robot Service(로봇 브릿지)와 주고받는 MQTT 전송 계층.

규격은 `mqtt_test/MQTT_인터페이스/ERUT_검사로봇_MQTT_표준인터페이스_if-0.9.xlsx` 다
(탭1~5·8~10. 탭6·7 은 ERUT 내부 규격이라 3S 와 무관하다). RCS 의 외부 통신은
이 브로커 하나다 — 사내 MC MQTT(`MqttServer`)는 2026-10-07 에 뺐다.

    요청         : {"timestamp": 1786500000000, "content": {...}}   timestamp 는 숫자
    답(res 등)   : {"timestamp": …, "status_code": 202, "content": {…, "error": {…}}}
    evt/error    : {"timestamp": …, "content": {"job_id": …, "error": {…, "cleared": false}}}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# ERUT 가 보내오는 동작. 토픽 끝부분으로 구분한다.
# 앞의 9개가 표준(if-0.5 탭5 action)이다. 뒤는 표준에 없는 3S 확장:
#   home      — 홈 이동 명령. if-0.5 는 홈 **확인 신호**(evt/home)만 넣었다.
#   mark_next — 마커가 ERUT 것일 때 다음 점으로 가라는 신호(탭2 초안 「마킹 주체」).
ACTIONS = (
    "calibrate", "prepare", "start", "pause", "resume",
    "abort", "reset", "mark", "query",
    "home", "mark_next",
)

#: 같은 `doosan/robot/req/` 아래에 오는 **옛 사내 MC 규격** 토픽. RCS 는 MC 를
#: 뺐지만 같은 브로커에 남은 MC 도구가 보낼 수 있다 — ERUT 요청이 아니므로
#: 못 본 척한다(501 로 답하면 안 된다 — 남의 요청이다).
#: reset 은 두 규격이 같은 이름을 쓰는데, MC 쪽은 content 래퍼가 없어 걸러진다.
MC_ACTIONS = frozenset({
    "mc_cmd", "job_cmd", "job_clear", "ems", "speed", "probe_ack", "mark_cmd",
})

# 활동 상태 9값 (if-0.8 탭5 activity). evt/status 의 state 는 online/offline 만 쓴다.
ACTIVITIES = ("idle", "calibrating", "preparing", "ready", "running",
              "marking", "paused", "error", "estop")       # marking: if-0.8 (9값)

REQ_PREFIX = "doosan/robot/req/"
ERUT_STATUS = "erut/status"
#: 브릿지가 5초마다 내는 erut/status 가 이만큼 끊기면 없는 것으로 본다(탭2 18행).
#: 브릿지 프로그램이 멈춰 있으면(꺼지지는 않아) 유언이 안 뜨기 때문이다.
ERUT_SILENCE_S = 20.0
#: 붙은 지 이 시간 안에 끊기기를 CONFLICT_FLAPS 번 연달아 겪으면, 같은 접속 ID 로
#: 다른 RCS(다른 PC)가 붙어 서로 밀어내는 것으로 본다 [s]. 브로커는 같은 ID 가 새로
#: 붙으면 먼저 붙은 쪽을 끊고, 끊긴 쪽은 곧 다시 붙어 상대를 끊는다 — 2026-10-12
#: 이 PC 와 리눅스 PC 의 RCS 가 둘 다 robot1 로 붙어 1초마다 번갈아 끊겼다.
SHORT_SESSION_S = 5.0
CONFLICT_FLAPS = 3
#: 시각차 경고 기준 [s]. ERUT 는 timestamp 가 10초 넘게 어긋난 메시지를 버린다 —
#: 그 절반에서 미리 알린다(장비 시각을 NTP 에 맞춰야 한다).
CLOCK_SKEW_WARN_S = 5.0

# 시험용 주입 토픽. 실제 장애 수집이 아직 없어서, 알람/에러 시험 도구가
# 이걸로 찔러 주면 RCS 가 **규격대로 evt/error 를 발행**한다.
# 실제 장애 수집(PLC·로봇 알람)이 붙으면 이 토픽은 빼도 된다.
TEST_INJECT = "3s/test/inject/error"

#: MQTT 3.1.1 CONNACK 거부 코드 → 뜻과 확인할 것. 화면 알림과 점검 도구
#: (`python3 -m smr_operator_ui.erut_check`)가 같이 쓴다.
#: ERUT 시험 브로커는 mosquitto 다(2026-10-07 `$SYS/broker/version` 2.1.2).
#: mosquitto 는 계정 오류에 5 를 보내고 3 은 보내지 않는다(실측·소스 확인) —
#: 3 이 오면 거의 늘 **다른 브로커**에 붙은 것이다.
CONNACK_HINTS = {
    1: "MQTT 3.1.1 을 받지 않는 곳입니다 — 연결 설정의 ERUT 주소·포트가 맞는지 확인하세요",
    2: "클라이언트 ID 를 거부했습니다 — 연결 설정의 ERUT 장치 ID 를 확인하세요",
    3: ("'서버 사용 불가'로 거절됐습니다. ERUT(mosquitto)는 이 값을 보내지 않습니다 — "
        "주소·포트가 ERUT 가 아닌 다른 MQTT 서버를 가리키는지 연결 설정의 ERUT 주소·포트를 "
        "확인하세요"),
    4: "계정 이름·비밀번호 형식이 틀렸습니다 — 연결 설정의 ERUT 계정·비밀번호를 확인하세요",
    5: "권한이 없습니다 — 연결 설정의 ERUT 계정·비밀번호가 ERUT 가 알려 준 값과 같은지 확인하세요",
}


def connack_hint(rc: int) -> str:
    """CONNACK 거부 코드의 뜻. 모르는 값이면 코드만 적는다."""
    return CONNACK_HINTS.get(int(rc), f"알 수 없는 거부 코드 {rc}")


def utc_ms() -> int:
    """ERUT 규격의 timestamp — UTC 밀리초 **숫자**."""
    return int(time.time() * 1000)


@dataclass(frozen=True)
class ErutConfig:
    """브로커 접속 정보. 탭2 초안 「접속」: MQTT 3.1.1 · 클라이언트 ID 는 로봇마다
    하나로 고정 · keepalive 10~20초 · cleanSession=true."""

    # 비어 있으면 ERUT 에 붙지 않는다(RCS 단독) — 접속 정보는 ERUT 가 알려 준다.
    host: str = ""
    port: int = 1883
    device_id: str = ""
    keep_alive: int = 15
    client_id: str = ""
    # 브로커 계정. ERUT 가 정해 알려 준다(탭0 초안 「브로커 접속 정보」). 비워 두면 익명.
    username: str = ""
    password: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        # 실행할 때마다 바뀌면 브로커가 매번 새 장비로 본다 — 장치 ID 로 고정한다.
        if not self.client_id:
            object.__setattr__(self, "client_id", f"3s-{self.device_id}")


class ErutClient(QObject):
    """ERUT Topic 송수신. 프로토콜 판단은 하지 않고 봉투만 씌우고 벗긴다."""

    # (action, content, timestamp) — 받은 요청과 브릿지가 보낸 시각(UTC ms, 없으면
    # None). 판단은 ErutSession 이 한다 — 늦게 온 일 요청은 E6005 로 거절한다.
    request_received = pyqtSignal(str, object, object)
    # ERUT 자신의 생존 상태 (erut/status). offline 이면 진행 중 작업을 멈춰야 한다.
    erut_online_changed = pyqtSignal(bool)
    # 브릿지 시각 - 우리 시각 [s]. erut/status 를 받을 때마다 잰다.
    clock_skew_measured = pyqtSignal(float)
    # 시험 도구가 넣어 준 장애. RCS 가 이걸 받아 evt/error 를 발행한다.
    test_error_injected = pyqtSignal(object)
    connected_changed = pyqtSignal(bool)
    # 같은 접속 ID 충돌로 다시 붙기를 멈췄다 (사람이 읽을 사유). 화면 배지가 쓴다.
    link_halted = pyqtSignal(str)
    # paho 스레드에서 충돌을 알아채면 Qt 스레드에서 멈추도록 넘기는 내부 신호.
    _conflict_seen = pyqtSignal()
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
        # 마지막으로 알린 접속 거부 코드. 같은 거부를 재접속마다 되풀이하지 않는다.
        self._refused_rc: int | None = None
        # 같은 접속 ID 충돌 감지 — 붙은 시각, 짧게 끊긴 횟수, 멈췄는가.
        self._connected_at = 0.0
        self._short_sessions = 0
        self._halted = False
        # 앱이 ERUT 에 붙으라고 했는가(start). 접속 정보가 비어 있으면 붙지 않고
        # 기다리다가, 값이 들어오면(apply_config) 그때 붙는다.
        self._enabled = False
        self._conflict_seen.connect(self._halt_for_conflict)
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

    @property
    def erut_online(self) -> bool:
        """ERUT 브릿지가 살아 있는가(erut/status online 을 받고 20초 안)."""
        return bool(self._erut_online)

    @property
    def halted(self) -> bool:
        """같은 접속 ID 충돌로 다시 붙기를 멈춘 상태인가."""
        return self._halted

    # ------------------------------------------------------------ 연결
    def start(self) -> None:
        self._enabled = True
        if self._client is not None:
            return
        if not self.config.host or not self.config.device_id:
            # 접속 정보가 비어 있다 — RCS 단독으로 돈다(2026-10-14). 값이 들어오면
            # apply_config 가 다시 부른다.
            self._set_connected(False)
            self.activity.emit("ERUT 접속 주소 · 장치 ID 가 비어 있어 ERUT 에 붙지 않습니다.")
            return
        self._refused_rc = None
        self._halted = False
        self._short_sessions = 0
        self._connected_at = 0.0
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            self.error_occurred.emit(f"paho-mqtt 가 없습니다: {exc}")
            return

        # 끊긴 동안 쌓인 옛 요청을 다시 붙자마자 받지 않게 cleanSession=true.
        # 규격은 MQTT 3.1.1 이다(ERUT 접속 정보 2026-10-01).
        kwargs: dict[str, Any] = {"client_id": self.config.client_id,
                                  "clean_session": True,
                                  "protocol": mqtt.MQTTv311}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(**kwargs)
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password or None)
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
            self.error_occurred.emit(f"ERUT 접속 실패: {exc}")
            return
        self._client = client
        self._silence_timer.start()

    def apply_config(self, config: ErutConfig) -> bool:
        """ERUT 브로커 접속 정보를 바꾼다. 붙어 있으면 다시 붙는다.

        같은 값이면 아무것도 하지 않는다 — 다시 붙을 때마다 evt/status 를
        새로 내보내게 되고, ERUT 쪽에서는 RCS 가 껐다 켜진 것으로 보인다.
        """
        if config == self.config and not self._halted:
            return False
        # 충돌로 멈춰 있었으면 값이 같아도 다시 붙는다(설정 저장 = 다시 시도).
        if self._client is not None:
            self._shutdown()
        self.config = config
        if self._enabled:
            self.start()
        return True

    def stop(self, announce_offline: bool = True) -> None:
        """ERUT 연결을 끊고 다시 붙지 않는다(창을 닫을 때)."""
        self._enabled = False
        self._shutdown(announce_offline)

    def _shutdown(self, announce_offline: bool = True) -> None:
        """지금 연결만 끊는다 — 접속 정보를 바꿀 때 · 충돌로 물러날 때."""
        self._silence_timer.stop()
        client, self._client = self._client, None
        if client is None:
            return
        # 정상 종료는 offline 을 직접 발행한다 (LWT 는 비정상 종료용). 충돌로
        # 물러날 때는 내지 않는다 — 같은 토픽에 붙어 있는 상대 RCS 의 online 을
        # 덮어쓴다.
        try:
            if announce_offline:
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

    @staticmethod
    def _answer_payload(req_id: str, action: str, status_code: int,
                        error: dict | None, extra: dict) -> dict:
        """요청의 답(res · evt/ready · evt/complete · evt/mark_ready) 봉투 (if-0.9).

        결과는 맨 바깥 `status_code`(HTTP 원래 뜻) 하나다 — if-0.8 의 맨 바깥
        code · message 는 없어졌다. 2xx 가 아니면 content.error{code, name,
        detail} 가 반드시 있고, 문제가 없으면 칸을 뺀다(null 을 쓰지 않는다).
        """
        content: dict[str, Any] = {"req_id": req_id, "action": action}
        content.update(extra)
        if error:
            content["error"] = dict(error)
        return {"timestamp": utc_ms(), "status_code": int(status_code),
                "content": content}

    def publish_res(self, req_id: str, action: str, status_code: int,
                    error: dict | None = None, **extra) -> bool:
        """요청 응답. 1초 이내에 보내야 한다."""
        return self._publish(self.res_topic(), self._answer_payload(
            req_id, action, status_code, error, extra))

    def publish_event(self, name: str, req_id: str, action: str,
                      status_code: int = 200, error: dict | None = None,
                      **extra) -> bool:
        """ready / complete / mark_ready — 요청의 끝을 알리는 이벤트."""
        return self._publish(self.evt_topic(name), self._answer_payload(
            req_id, action, status_code, error, extra))

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

        retain 이라 앞 구역 것이 남는다 — 작업 중이면 job_id 를 꼭 싣는다(받는 쪽이
        견준다). 작업이 없으면 칸을 뺀다 — 빈 글자("")는 규격에 없는 값이다
        (if-0.10 탭11 Q-41: 10/12 기록 1만 2,980건 중 7,161건이 "" 였다).
        """
        content: dict[str, Any] = {"state": state}
        if job_id:
            content["job_id"] = job_id
        return self._publish(self.evt_topic("contact"), {
            "timestamp": utc_ms(), "content": content,
        }, qos=1, retain=True)

    def publish_home(self, state: str) -> bool:
        """이동 안전 자세 (if-0.5 탭2 14행). home / deployed.

        붙을 때 + 바뀔 때만 보낸다(주기 재전송 없음), QoS 1 · retain.
        """
        return self._publish(self.evt_topic("home"), {
            "timestamp": utc_ms(), "content": {"state": state},
        }, qos=1, retain=True)

    def publish_info(self, content: dict) -> bool:
        """장비 자기소개 (탭8 evt/info). 접속 시 + 바뀔 때, retain."""
        return self._publish(self.evt_topic("info"),
                             {"timestamp": utc_ms(), "content": dict(content)},
                             qos=1, retain=True)

    def publish_message(self, code: str, name: str, text: str,
                        **extra) -> bool:
        """안내 알림 evt/message (3S 확장 — 표준에 없고 ERUT 는 읽지 않는다, Q-09).

        장애가 아니다 — 발생·해제 쌍이 없고 ERUT 동작을 바꾸지 않는다. if-0.9
        의 다른 메시지처럼 맨 바깥에는 timestamp 뿐이고 code(Mxxxx) · name ·
        보여 줄 문장 text 는 content 안이다. 요청 때문에 낸 알림이면 extra 로
        req_id·action 을 싣는다.
        """
        content = {"code": code, "name": name, "text": text}
        content.update(extra)
        return self._publish(self.evt_topic("message"),
                             {"timestamp": utc_ms(), "content": content})

    def publish_error(self, error: dict, job_id: str = "") -> bool:
        """장애·위험 통보 evt/error (if-0.9 탭2 10행). 요청 실패는 res 로 낸다.

        맨 바깥은 timestamp 뿐이고 content 안에 job_id(해당 시)와
        error{code, name, level, recovery, cleared, detail(선택)} 가 있다 —
        if-0.8 의 맨 바깥 code · message 는 없어졌다. 해제는 발생 때와 **같은
        error.code** 에 cleared=true 다(code 끝에 -CLEAR 를 붙이지 않는다).
        """
        content: dict[str, Any] = {}
        if job_id:
            content["job_id"] = job_id
        content["error"] = dict(error)
        return self._publish(self.evt_topic("error"),
                             {"timestamp": utc_ms(), "content": content})

    # ------------------------------------------------------------ 콜백
    def _on_connect(self, client, _userdata, _flags, rc) -> None:  # noqa: ANN001
        if rc != 0:
            # paho 는 거절당해도 계속 다시 붙어 본다 — 같은 거절은 한 번만 알린다.
            if rc != self._refused_rc:
                self._refused_rc = rc
                self.error_occurred.emit(
                    f"ERUT({self.config.host}:{self.config.port}) 접속 거부 "
                    f"(rc={rc}) — {connack_hint(rc)}.")
            return
        self._refused_rc = None
        # 한 줄로 받는다(탭2 17행) — 모르는 동작에도 501 로 답해야 해서다.
        # 아는 것만 구독하면 모르는 동작은 아예 안 들어와 브릿지가 세 번
        # 다시 보낸 뒤 통신 오류로 본다.
        client.subscribe(f"{REQ_PREFIX}#", qos=1)
        client.subscribe(ERUT_STATUS, qos=1)
        client.subscribe(TEST_INJECT, qos=1)
        self._connected_at = time.monotonic()
        self._set_connected(True)

    def _on_disconnect(self, client, _userdata, rc) -> None:  # noqa: ANN001
        self._set_connected(False)
        if rc == 0 or client is not self._client:
            return                      # 우리가 끊었다(stop · 설정 변경)
        lived = time.monotonic() - self._connected_at if self._connected_at else None
        self._connected_at = 0.0
        if lived is not None and lived < SHORT_SESSION_S:
            self._short_sessions += 1
        else:
            self._short_sessions = 0
        if self._short_sessions >= CONFLICT_FLAPS:
            self._conflict_seen.emit()   # Qt 스레드에서 멈춘다

    def _halt_for_conflict(self) -> None:
        """같은 접속 ID 로 붙은 다른 RCS 와 번갈아 끊기고 있다 — 다시 붙기를 멈춘다.

        둘 다 계속 다시 붙으면 ERUT 는 1~2초마다 offline/online 을 받고 요청이
        어느 쪽에 갈지 모른다. 이쪽이 물러나면 상대는 붙은 채로 남는다. 연결
        설정을 저장하면(apply_config) 다시 붙어 본다.
        """
        if self._client is None or self._halted:
            return
        self._shutdown(announce_offline=False)
        self._halted = True
        reason = (f"같은 장치 ID({self.config.device_id})로 다른 RCS 가 ERUT 에 붙어 있어 "
                  "서로 연결을 끊고 있습니다. 이 RCS 는 다시 붙지 않습니다 — 다른 PC 의 RCS 를 "
                  "끄거나 장치 ID 를 바꾼 뒤, 연결 설정을 저장하면 다시 붙습니다.")
        self.error_occurred.emit(reason)
        self.link_halted.emit(reason)

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
            # retain 으로 온 것은 예전 값이라 시각차를 재지 않는다.
            if not getattr(msg, "retain", False):
                self._note_skew(payload.get("timestamp"))
            self._set_erut_online(online)
            return

        if not msg.topic.startswith(REQ_PREFIX):
            return
        if getattr(msg, "retain", False):
            # 브로커에 남아 있던(retain) 요청이다 — 지금 누가 보낸 것이 아니라
            # 붙을 때마다 다시 온다. 실행하면 다시 붙을 때마다 캘리브레이션·
            # 구간이 또 돈다(2026-10-06 시험: retain 으로 남은 query 가 재접속마다
            # 들어왔다). 규격도 요청은 retain 하지 않고, retain 으로 온 요청은
            # 버린다(if-0.6 탭6).
            self.error_occurred.emit(
                f"ERUT 에 남아 있던(retain) 요청을 버렸습니다: {msg.topic}"
                " — 요청은 retain 없이 보내야 합니다.")
            return
        action = msg.topic[len(REQ_PREFIX):]
        if action in MC_ACTIONS or not isinstance(payload, dict):
            return
        # 모르는 동작도 세션까지 보낸다 — 세션이 501 NOT_IMPLEMENTED 로 답한다.
        content = payload.get("content")
        if not isinstance(content, dict):
            self.error_occurred.emit(f"ERUT 요청에 content 가 없습니다: {action}")
            return
        timestamp = payload.get("timestamp")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            timestamp = None
        self.request_received.emit(action, content, timestamp)

    def _note_skew(self, timestamp: Any) -> None:
        """브릿지가 붙인 timestamp 와 우리 시각의 차이를 잰다.

        ERUT 는 timestamp 가 10초 넘게 어긋난 메시지를 버린다 — 우리 시계가 틀리면
        멀쩡한 응답이 조용히 사라진다. 5초를 넘으면 한 번 알린다(다시 들어오면 해제).
        네트워크 지연이 섞이므로 1초 안팎의 차이는 정상이다.
        """
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            return
        skew = timestamp / 1000.0 - time.time()
        self.clock_skew_measured.emit(skew)
        over = abs(skew) > CLOCK_SKEW_WARN_S
        if over != getattr(self, "_skew_warned", False):
            self._skew_warned = over
            if over:
                self.error_occurred.emit(
                    f"ERUT 브릿지와 시각이 {skew:+.1f}초 어긋납니다 — 10초를 넘으면 ERUT 가 "
                    "우리 메시지를 버립니다. 이 PC 시각을 인터넷 시간(NTP)에 맞추세요.")
            else:
                self.activity.emit(f"ERUT 브릿지와 시각차가 정상으로 돌아왔습니다({skew:+.1f}초).")

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
            "ERUT 에 연결되었습니다." if connected else "ERUT 연결이 끊겼습니다."
        )
