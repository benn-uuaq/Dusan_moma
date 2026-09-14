"""ERUT(스테이션)와 주고받는 MQTT 전송 계층.

규격은 `mqtt_test/ERUT-3S_MQTT_인터페이스_*.xlsx` 다. 기존 `MqttServer`가
다루는 `doosan/robot/req/{mc_cmd,job_cmd,...}` 와는 **봉투 구조가 다르다.**

    ERUT   : {"timestamp": 1786500000000, "content": {...}}   timestamp 는 숫자
    doosan : {"timestamp": "1786500000000", ...}              timestamp 는 문자열

같은 `doosan/robot/req/` 접두어를 쓰지만 동작명이 갈리고 규격도 달라서,
둘을 한 클래스에 섞지 않고 접속부터 따로 둔다. 이쪽이 협력사로 나가는 규격이다.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

# ERUT 가 보내오는 동작. 토픽 끝부분으로 구분한다.
# 앞의 9개가 규격 20260812 판이고, "home" 은 20260914 판에 추가했다
# (탭4 E-1: 동작 중이면 409 BUSY + evt/message).
ACTIONS = (
    "calibrate", "prepare", "start", "pause", "resume",
    "abort", "reset", "mark", "query",
    "home",
)

# 장치 상태 7값 (탭5 값 정의)
STATES = ("idle", "calibrating", "ready", "running", "paused", "error", "estop")

REQ_PREFIX = "doosan/robot/req/"
ERUT_STATUS = "erut/status"

# 시험용 주입 토픽. 실제 장애 수집이 아직 없어서, 알람/에러 시험 도구가
# 이걸로 찔러 주면 RCS 가 **규격대로 evt/error 를 발행**한다.
# 실제 장애 수집(PLC·로봇 알람)이 붙으면 이 토픽은 빼도 된다.
TEST_INJECT = "3s/test/inject/error"


def utc_ms() -> int:
    """ERUT 규격의 timestamp — UTC 밀리초 **숫자**."""
    return int(time.time() * 1000)


@dataclass(frozen=True)
class ErutConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    device_id: str = "robot1"
    keep_alive: int = 30
    client_id: str = ""

    def __post_init__(self) -> None:
        if not self.client_id:
            object.__setattr__(self, "client_id", f"3s-{uuid.uuid4().hex[:8]}")


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

        kwargs: dict[str, Any] = {"client_id": self.config.client_id}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(**kwargs)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # 접속이 끊기면 브로커가 대신 offline 을 남긴다(retained).
        # 다만 LWT 는 1회성이라, 프로그램이 굳은 경우는 못 잡는다 —
        # 그래서 evt/status 를 주기적으로 다시 내보낸다(규격 30~60초).
        # 상대는 이 갱신이 끊기는 것으로 굳음을 판정한다.
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

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        # 정상 종료는 offline 을 직접 발행한다 (LWT 는 비정상 종료용).
        try:
            self.publish_status("offline")
            client.disconnect()
            client.loop_stop()
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"ERUT 연결 종료 실패: {exc}")
        finally:
            self._set_connected(False)

    # ------------------------------------------------------------ 발행
    def _publish(self, topic: str, payload: dict, qos: int = 1,
                 retain: bool = False) -> bool:
        if self._client is None or not self._connected:
            return False
        try:
            self._client.publish(
                topic,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                qos=qos, retain=retain,
            )
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

    def publish_status(self, state: str, battery: int | None = None,
                       charging: bool | None = None) -> bool:
        """접속 생존 상태. retained 라 늦게 붙은 쪽도 현재 값을 받는다.

        content 래퍼가 없는 평평한 구조다 (탭5 값 정의).
        """
        payload: dict[str, Any] = {"timestamp": utc_ms(), "state": state}
        if battery is not None:
            payload["battery"] = battery
        if charging is not None:
            payload["charging"] = charging
        return self._publish(self.evt_topic("status"), payload, qos=1, retain=True)

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
                      recovery: str, **extra) -> bool:
        """장애·위험 통보. 요청 실패는 res 로 보내고 여기 쓰지 않는다.

        봉투가 res/complete 와 같다 — `code`·`message` 는 **최상위**이고
        `level`·`recovery`·`detail` 은 content 안이다 (규격 탭4 예시).
        해제 통보는 코드에 `-CLEAR` 를 붙여 보낸다.
        """
        content = {"level": level, "recovery": recovery}
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
        for action in ACTIONS:
            client.subscribe(f"{REQ_PREFIX}{action}", qos=1)
        client.subscribe(ERUT_STATUS, qos=1)
        client.subscribe(TEST_INJECT, qos=1)
        self._set_connected(True)

    def _on_disconnect(self, _client, _userdata, rc) -> None:  # noqa: ANN001
        self._set_connected(False)

    def _on_message(self, _client, _userdata, msg) -> None:  # noqa: ANN001
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
            if online != self._erut_online:
                self._erut_online = online
                self.erut_online_changed.emit(online)
            return

        if not msg.topic.startswith(REQ_PREFIX):
            return
        action = msg.topic[len(REQ_PREFIX):]
        if action not in ACTIONS:
            return
        content = payload.get("content")
        if not isinstance(content, dict):
            self.error_occurred.emit(f"ERUT 요청에 content 가 없습니다: {action}")
            return
        self.request_received.emit(action, content)

    def _set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        self.connected_changed.emit(connected)
        self.activity.emit(
            "ERUT 브로커에 연결되었습니다." if connected else "ERUT 브로커 연결이 끊겼습니다."
        )
