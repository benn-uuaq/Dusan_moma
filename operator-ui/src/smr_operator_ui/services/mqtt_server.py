"""MQTT Broker 연결과 로봇 Topic 송수신을 담당하는 Qt 서비스."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class MqttTopics:
    """docs/mqtt_topic_form.md에 정의된 Topic 모음."""

    MC_COMMAND = "doosan/robot/req/mc_cmd"
    RESET = "doosan/robot/req/reset"
    EMS = "doosan/robot/req/ems"
    JOB_CLEAR = "doosan/robot/req/job_clear"
    JOB_COMMAND = "doosan/robot/req/job_cmd"
    SPEED = "doosan/robot/req/speed"
    # 원점에서 프로브 눌림 확인을 받고 스캔을 시작해도 된다는 회신 (MC -> RCS).
    # ERUT 규격의 req/start 게이트와 같은 자리이고, 사내 MC 규격 쪽 통로다.
    PROBE_ACK = "doosan/robot/req/probe_ack"

    ROBOT_STATE = "doosan/robot/robot_state"
    ERROR = "doosan/robot/error"
    TCP = "doosan/robot/tcp"
    JOB_STATE = "doosan/robot/job_state"
    # 로봇이 원점에 서서 프로브 확인을 기다린다 / 풀렸다 (RCS -> MC).
    # ERUT 규격의 evt/ready(stage=at_origin)와 같은 뜻이다.
    PROBE_GATE = "doosan/robot/probe_gate"

    MC_COMMAND_RESPONSE = "doosan/robot/resp/mc_cmd"
    RESET_RESPONSE = "doosan/robot/resp/reset"
    EMS_RESPONSE = "doosan/robot/resp/ems"
    JOB_CLEAR_RESPONSE = "doosan/robot/resp/job_clear"
    JOB_COMMAND_RESPONSE = "doosan/robot/resp/job_cmd"

    HEARTBEAT = "Heartbeat/robot"
    LAST_WILL = "Dead/robot"

    COMMANDS = (
        MC_COMMAND,
        RESET,
        EMS,
        JOB_CLEAR,
        JOB_COMMAND,
        SPEED,
        PROBE_ACK,
    )
    STATUSES = (
        ROBOT_STATE,
        ERROR,
        TCP,
        JOB_STATE,
        PROBE_GATE,
    )
    RESPONSES = (
        MC_COMMAND_RESPONSE,
        RESET_RESPONSE,
        EMS_RESPONSE,
        JOB_CLEAR_RESPONSE,
        JOB_COMMAND_RESPONSE,
    )
    SUBSCRIPTIONS = (*COMMANDS, *STATUSES, *RESPONSES, HEARTBEAT)


@dataclass(frozen=True, slots=True)
class MqttConfig:
    """Broker 접속 설정.

    환경 변수가 없으면 mqtt_topic_form.md의 개발 기본값을 사용한다.
    """

    host: str = "127.0.0.1"
    port: int = 1883
    client_id: str = ""
    keep_alive: int = 60
    qos: int = 1
    clean_session: bool = True
    tls: bool = False
    username: str | None = None
    password: str | None = None
    reconnect_min_delay: int = 1
    reconnect_max_delay: int = 30
    max_reconnect_attempts: int = 10
    command_max_age_ms: int = 10 * 60 * 1000
    heartbeat_timeout_ms: int = 10 * 1000

    def __post_init__(self) -> None:
        """비어 있는 Client ID에 실행별 고유값을 부여한다."""
        if not self.client_id:
            object.__setattr__(
                self,
                "client_id",
                f"smr-operator-ui-{uuid.uuid4().hex[:8]}",
            )

    @classmethod
    def from_environment(cls) -> "MqttConfig":
        """환경 변수에서 접속 정보를 읽어 설정 객체를 만든다."""
        return cls(
            host=os.getenv("SMR_MQTT_HOST", "127.0.0.1"),
            port=_env_int("SMR_MQTT_PORT", 1883),
            client_id=os.getenv("SMR_MQTT_CLIENT_ID", ""),
            keep_alive=_env_int("SMR_MQTT_KEEP_ALIVE", 60),
            qos=_env_int("SMR_MQTT_QOS", 1),
            clean_session=_env_bool("SMR_MQTT_CLEAN_SESSION", True),
            tls=_env_bool("SMR_MQTT_TLS", False),
            username=os.getenv("SMR_MQTT_USERNAME") or None,
            password=os.getenv("SMR_MQTT_PASSWORD") or None,
            max_reconnect_attempts=_env_int(
                "SMR_MQTT_MAX_RECONNECT_ATTEMPTS", 10
            ),
        )


class MqttPayloadError(ValueError):
    """수신 Payload가 Topic 명세를 위반했을 때 발생하는 오류."""


# 로봇 동작 속도 비율[%]의 허용 범위. 로봇 컨트롤러가 정한 값이다.
SPEED_MIN, SPEED_MAX = 2, 100

# `job_state`의 상태 값. 셀 하나가 거치는 세 단계다.
# `services/job_sequencer.py`의 `CellStatus`와 값이 같아야 한다.
JOB_STATES = frozenset({"waiting", "executing", "completed"})


class MqttServer(QObject):
    """Paho MQTT를 Qt 시그널 방식으로 제공하는 비동기 서비스.

    이름은 프로젝트 요청에 맞춰 ``MqttServer``를 사용하지만, 실제 MQTT
    Broker는 별도로 실행되며 이 클래스는 Broker에 연결하는 Client 역할을 한다.
    """

    connected_changed = pyqtSignal(bool)
    message_received = pyqtSignal(str, object)
    command_received = pyqtSignal(str, object)
    status_received = pyqtSignal(str, object)
    response_received = pyqtSignal(str, object)
    heartbeat_received = pyqtSignal(object)
    robot_online_changed = pyqtSignal(bool)
    published = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)
    _retry_limit_reached = pyqtSignal()

    def __init__(
        self,
        config: MqttConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config or MqttConfig.from_environment()
        self._client: Any | None = None
        self._connected = False
        self._robot_online = False
        self._last_heartbeat_monotonic: float | None = None
        self._latest_command_timestamp: dict[str, int] = {}
        self._reconnect_failures = 0
        self._retry_stop_requested = False
        self._retry_limit_reached.connect(self._stop_after_retry_limit)

        # Heartbeat가 10초 이상 수신되지 않으면 로봇 오프라인 상태로 전환한다.
        self._heartbeat_watchdog = QTimer(self)
        self._heartbeat_watchdog.setInterval(1000)
        self._heartbeat_watchdog.timeout.connect(self._check_heartbeat)
        self._heartbeat_watchdog.start()

    @property
    def is_connected(self) -> bool:
        """현재 Broker 연결 여부를 반환한다."""
        return self._connected

    def start(self) -> None:
        """Broker 연결을 시작하고 Paho 네트워크 루프를 실행한다."""
        if self._client is not None:
            return

        try:
            self._reconnect_failures = 0
            self._retry_stop_requested = False
            client = self._create_client()
            self._client = client
            client.connect_async(
                self.config.host,
                self.config.port,
                self.config.keep_alive,
            )
            client.loop_start()
        except Exception as exc:
            self._client = None
            self.error_occurred.emit(f"MQTT 연결 시작 실패: {exc}")

    def stop(self) -> None:
        """정상 연결 종료 후 Paho 네트워크 루프를 정리한다."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.disconnect()
            client.loop_stop()
        except Exception as exc:
            self.error_occurred.emit(f"MQTT 연결 종료 실패: {exc}")
        finally:
            self._set_connected(False)

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

    def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        qos: int | None = None,
        retain: bool = False,
    ) -> bool:
        """사전 형태의 Payload를 UTF-8 JSON으로 발행한다."""
        if self._client is None or not self._connected:
            self.error_occurred.emit("MQTT Broker에 연결되지 않았습니다.")
            return False

        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            result = self._client.publish(
                topic,
                encoded,
                qos=self.config.qos if qos is None else qos,
                retain=retain,
            )
            if int(result.rc) != 0:
                raise RuntimeError(f"Paho 오류 코드 {result.rc}")
            self._note_traffic("발신", topic, encoded)
        except (TypeError, ValueError, RuntimeError) as exc:
            self.error_occurred.emit(f"MQTT 발행 실패 ({topic}): {exc}")
            return False

        timestamp = str(payload.get("timestamp", ""))
        self.published.emit(topic, timestamp)
        return True

    def publish_mc_command(
        self,
        *,
        amr: str | None = None,
        cobot: str | None = None,
    ) -> bool:
        """AMR 또는 Cobot 동작 명령을 발행한다."""
        payload: dict[str, Any] = {"timestamp": _utc_epoch_ms()}
        if amr is not None:
            payload["amr"] = amr
        if cobot is not None:
            payload["cobot"] = cobot
        validate_command_payload(MqttTopics.MC_COMMAND, payload)
        return self.publish(MqttTopics.MC_COMMAND, payload)

    def publish_reset(self, request: bool = True) -> bool:
        """알람 리셋 요청을 발행한다."""
        return self._publish_request(MqttTopics.RESET, request)

    def publish_ems(self, request: bool = True) -> bool:
        """비상 정지 요청을 발행한다."""
        return self._publish_request(MqttTopics.EMS, request)

    def publish_job_clear(self, request: bool = True) -> bool:
        """현재 Job 중단 및 삭제 요청을 발행한다."""
        return self._publish_request(MqttTopics.JOB_CLEAR, request)

    def publish_job_command(
        self,
        job_id: str,
        *,
        diameter: str | int | float,
        height: str | int | float,
        target_distance: str | int | float,
        column_count: str | int,
        row_count: str | int,
        cell_width: str | int | float,
        cell_height: str | int | float,
        overlap: str | int | float,
    ) -> bool:
        """전체 작업 시작 명령을 발행한다 (검사 대상 + 격자 분할 계획).

        원통을 편 직사각형을 격자로 나눈다. 열(`column_count`)은 AMR이
        정차하는 원주 구역, 행(`row_count`)은 리프트 높이다. Cobot은 셀
        하나(`cell_width` × `cell_height`)만 ㄹ자로 스캔한다 — 원통 전체
        높이를 한 번에 훑는 게 아니다.

        셀을 하나씩 순회하는 것은 이 명령 이후 UI/로봇 쪽이 자동으로
        진행하므로, 지금 몇 번째 셀인지는 담지 않는다. 스캐너 유효높이
        (`scan_h`)는 장비 고유값이라 여기 없고 수신 측 로컬 설정을 쓴다.
        """
        payload = {
            "timestamp": _utc_epoch_ms(),
            "job_id": job_id,
            "job_info": {
                "diameter": str(diameter).strip(),
                "height": str(height).strip(),
                "target_distance": str(target_distance).strip(),
            },
            "plan": {
                "column_count": str(column_count).strip(),
                "row_count": str(row_count).strip(),
                "cell_width": str(cell_width).strip(),
                "cell_height": str(cell_height).strip(),
                "overlap": str(overlap).strip(),
            },
        }
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)
        return self.publish(MqttTopics.JOB_COMMAND, payload)

    def publish_speed(self, percent: int) -> bool:
        """로봇 전체 동작 속도 비율[%]을 바꾸라고 요청한다. 2~100."""
        payload = {"timestamp": _utc_epoch_ms(), "speed": str(int(percent))}
        validate_command_payload(MqttTopics.SPEED, payload)
        return self.publish(MqttTopics.SPEED, payload)

    def publish_job_state(self, cell_id: str, state: str) -> bool:
        """셀 하나의 진행 상태를 외부(MC)에 알린다.

        `job_id`에는 `1A`, `12F`처럼 격자 이름이 들어간다. 원통을 편
        직사각형을 열(AMR 정차 구역) × 행(리프트 높이)으로 나눈 좌표이며,
        외부는 이걸로 어느 영역이 끝났는지 추적한다.
        """
        if state not in JOB_STATES:
            raise MqttPayloadError(
                f"허용되지 않은 Job 상태입니다: {state!r} "
                f"(가능: {', '.join(sorted(JOB_STATES))})"
            )
        return self.publish(
            MqttTopics.JOB_STATE,
            {
                "timestamp": _utc_epoch_ms(),
                "job_id": str(cell_id).strip(),
                "state": state,
            },
        )

    def publish_probe_gate(self, waiting: bool, cell_id: str = "") -> bool:
        """원점에서 프로브 확인을 기다리는지 외부(MC)에 알린다.

        로봇은 3점 측정을 마치고 원점에 서면 멈춰서 기다린다(레지스터 290 = 7).
        프로브가 벽에 제대로 눌렸는지는 로봇이 알 수 없어서, 확인은 바깥이
        한다. 확인이 끝나면 `req/probe_ack` 로 회신해 주면 로봇이 적심(비비기)
        후 스캔으로 넘어간다.

        ERUT 규격에서는 이 자리가 `evt/ready`(stage=at_origin) -> `req/start`
        다. 사내 MC 규격에는 대응하는 동작이 없어 통로를 따로 둔다.
        """
        return self.publish(
            MqttTopics.PROBE_GATE,
            {
                "timestamp": _utc_epoch_ms(),
                "state": "waiting" if waiting else "released",
                "job_id": str(cell_id).strip(),
            },
        )

    def publish_tcp(self, values: list, cell_id: str = "") -> bool:
        """제로점 기준 TCP 좌표를 격자 번호와 함께 외부(MC)에 알린다.

        이 값이 스캐너 관리 시스템으로 나가는 실제 데이터다. 좌표만으로는
        원통 어디인지 알 수 없으므로 지금 스캔 중인 격자 이름(`1A`, `12F`)을
        함께 싣는다. 격자를 아는 것은 `JobSequencer` 뿐이라 이 결합은
        RCS에서만 할 수 있다.

        베이스 프레임 좌표(레지스터 384~389)는 모니터링용이라 여기 오지
        않는다. 여기 오는 값은 제로점 기준(280~285)이다.
        """
        if len(values) < 6:
            raise MqttPayloadError(
                f"TCP 자세는 6개 성분이 필요합니다: {len(values)}개"
            )
        x, y, z, _rx, _ry, rz = (float(value) for value in values[:6])
        return self.publish(
            MqttTopics.TCP,
            {
                "timestamp": _utc_epoch_ms(),
                "cell": str(cell_id).strip(),
                "x": f"{x:.1f}",
                "y": f"{y:.1f}",
                "z": f"{z:.1f}",
                # 회전은 mrad으로 들어오므로 규격의 radian으로 바꾼다.
                "yaw": f"{rz / 1000.0:.3f}",
            },
            qos=0,
            retain=False,
        )

    def publish_heartbeat(self, status: str = "ONLINE") -> bool:
        """필요할 때 로봇 Heartbeat Topic에 상태를 발행한다."""
        return self.publish(
            MqttTopics.HEARTBEAT,
            {"timestamp": _utc_epoch_ms(), "status": status},
            qos=1,
            retain=False,
        )

    def _publish_request(self, topic: str, request: bool) -> bool:
        # 문서 예제와 호환되도록 boolean을 JSON 문자열로 전송한다.
        payload = {
            "timestamp": _utc_epoch_ms(),
            "request": "true" if request else "false",
        }
        validate_command_payload(topic, payload)
        return self.publish(topic, payload)

    def _create_client(self) -> Any:
        """Paho Client를 만들고 콜백·인증·재연결 정책을 설정한다."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError(
                "paho-mqtt가 설치되지 않았습니다. 프로젝트 의존성을 설치해 주세요."
            ) from exc

        kwargs: dict[str, Any] = {
            "client_id": self.config.client_id,
            "clean_session": self.config.clean_session,
            "protocol": mqtt.MQTTv311,
        }
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        client = mqtt.Client(**kwargs)
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(
            min_delay=self.config.reconnect_min_delay,
            max_delay=self.config.reconnect_max_delay,
        )
        client.will_set(
            MqttTopics.LAST_WILL,
            payload=json.dumps(
                {"status": "OFFLINE", "reason": "unexpected_disconnect"},
                separators=(",", ":"),
            ),
            qos=2,
            retain=True,
        )
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        if self.config.tls:
            client.tls_set()
        return client

    def _on_connect(
        self,
        client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        """연결 성공 시 명세에 포함된 Topic을 모두 구독한다."""
        code = _reason_code_int(reason_code)
        if code != 0:
            self._set_connected(False)
            self._record_reconnect_failure(
                client,
                f"MQTT Broker 연결 거부: {reason_code}",
            )
            return

        self._reconnect_failures = 0
        self._retry_stop_requested = False

        subscriptions = [
            (topic, self.config.qos) for topic in MqttTopics.SUBSCRIPTIONS
        ]
        result, _message_id = client.subscribe(subscriptions)
        if int(result) != 0:
            self.error_occurred.emit(f"MQTT Topic 구독 실패: Paho 오류 코드 {result}")
            return
        self._set_connected(True)

    def _on_disconnect(self, _client: Any, _userdata: Any, *args: Any) -> None:
        """Broker 연결 종료를 Qt에 알린다."""
        self._set_connected(False)
        # 정상 종료가 아닌 경우의 Reason Code는 Paho 버전에 따라 위치가 다르므로
        # 연결 상태만 갱신하고 재연결은 Paho의 네트워크 루프에 맡긴다.

    def _on_connect_fail(self, client: Any, _userdata: Any) -> None:
        """TCP 연결 실패를 재접속 횟수에 포함한다."""
        self._record_reconnect_failure(client, "MQTT Broker 연결 실패")

    def _record_reconnect_failure(self, client: Any, reason: str) -> None:
        """재접속 실패를 세고 10회 도달 시 네트워크 루프를 종료한다."""
        if client is not self._client or self._retry_stop_requested:
            return
        limit = max(1, self.config.max_reconnect_attempts)
        self._reconnect_failures += 1
        attempt = self._reconnect_failures
        if attempt >= limit:
            self._retry_stop_requested = True
            self.error_occurred.emit(
                f"{reason} ({attempt}/{limit}). 자동 재접속을 종료합니다."
            )
            self._retry_limit_reached.emit()
            return
        self.error_occurred.emit(f"{reason} ({attempt}/{limit})")

    def _stop_after_retry_limit(self) -> None:
        """Paho 콜백 스레드 밖에서 안전하게 연결 루프를 정리한다."""
        self.stop()

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        """수신 JSON을 검증하고 Topic 종류에 맞는 Qt 시그널을 발생시킨다."""
        topic = str(message.topic)
        self._note_traffic("수신", topic, bytes(message.payload))
        try:
            text = bytes(message.payload).decode("utf-8")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise MqttPayloadError("Payload 최상위 값은 JSON object여야 합니다.")

            if topic in MqttTopics.COMMANDS:
                validate_command_payload(topic, payload)
                self._accept_command_timestamp(topic, payload)

            self.message_received.emit(topic, payload)
            if topic in MqttTopics.COMMANDS:
                self.command_received.emit(topic, payload)
            elif topic in MqttTopics.STATUSES:
                self.status_received.emit(topic, payload)
            elif topic in MqttTopics.RESPONSES:
                self.response_received.emit(topic, payload)
            elif topic == MqttTopics.HEARTBEAT:
                self._mark_heartbeat(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, MqttPayloadError) as exc:
            self.error_occurred.emit(f"MQTT 메시지 거부 ({topic}): {exc}")

    def _accept_command_timestamp(
        self,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        """10분이 지난 명령과 동일하거나 더 오래된 명령을 거부한다."""
        timestamp = _timestamp_int(payload)
        now_ms = int(time.time() * 1000)
        if timestamp < now_ms - self.config.command_max_age_ms:
            raise MqttPayloadError("유효시간 10분이 지난 명령입니다.")

        previous = self._latest_command_timestamp.get(topic)
        if previous is not None and timestamp <= previous:
            raise MqttPayloadError("중복되었거나 이전 timestamp의 명령입니다.")
        self._latest_command_timestamp[topic] = timestamp

    def _mark_heartbeat(self, payload: dict[str, Any]) -> None:
        """Heartbeat 수신 시 로봇 온라인 상태와 마지막 수신 시각을 갱신한다."""
        self._last_heartbeat_monotonic = time.monotonic()
        if not self._robot_online:
            self._robot_online = True
            self.robot_online_changed.emit(True)
        self.heartbeat_received.emit(payload)

    def _check_heartbeat(self) -> None:
        """Heartbeat 미수신 시간이 기준을 넘으면 오프라인으로 전환한다."""
        if not self._robot_online or self._last_heartbeat_monotonic is None:
            return
        elapsed_ms = (
            time.monotonic() - self._last_heartbeat_monotonic
        ) * 1000
        if elapsed_ms >= self.config.heartbeat_timeout_ms:
            self._robot_online = False
            self.robot_online_changed.emit(False)

    def _set_connected(self, connected: bool) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        self.connected_changed.emit(connected)


def validate_command_payload(topic: str, payload: dict[str, Any]) -> None:
    """Topic별 Command Payload가 mqtt_topic_form.md 형식인지 검사한다."""
    _timestamp_int(payload)

    if topic == MqttTopics.MC_COMMAND:
        amr = payload.get("amr")
        cobot = payload.get("cobot")
        if amr is None and cobot is None:
            raise MqttPayloadError("amr 또는 cobot 명령이 하나 이상 필요합니다.")
        if amr is not None and amr not in {"run", "stop", "ems", "lift"}:
            raise MqttPayloadError(f"허용되지 않은 AMR 명령입니다: {amr!r}")
        if cobot is not None and cobot not in {"run", "stop", "ems", "home"}:
            raise MqttPayloadError(f"허용되지 않은 Cobot 명령입니다: {cobot!r}")
        return

    if topic == MqttTopics.SPEED:
        raw = payload.get("speed")
        if not isinstance(raw, str) or not raw.strip():
            raise MqttPayloadError("비어 있지 않은 문자열 speed가 필요합니다.")
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise MqttPayloadError(f"speed는 정수여야 합니다: {raw!r}") from exc
        if not SPEED_MIN <= value <= SPEED_MAX:
            raise MqttPayloadError(
                f"speed는 {SPEED_MIN}~{SPEED_MAX} 범위여야 합니다: {value}"
            )
        return

    if topic == MqttTopics.PROBE_ACK:
        pressed = payload.get("pressed")
        if isinstance(pressed, str):
            pressed = pressed.strip().lower()
            if pressed not in {"true", "false", "1", "0", "yes", "no", "ok"}:
                raise MqttPayloadError(f"허용되지 않은 pressed 값입니다: {pressed!r}")
        elif not isinstance(pressed, bool):
            raise MqttPayloadError("pressed 는 참/거짓이어야 합니다.")
        return

    if topic in {MqttTopics.RESET, MqttTopics.EMS, MqttTopics.JOB_CLEAR}:
        if "request" not in payload:
            raise MqttPayloadError("request 필드가 필요합니다.")
        if payload["request"] not in {"true", "false", None}:
            raise MqttPayloadError(
                "request는 문자열 'true', 'false' 또는 null이어야 합니다."
            )
        return

    if topic == MqttTopics.JOB_COMMAND:
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise MqttPayloadError("비어 있지 않은 문자열 job_id가 필요합니다.")
        job_info = payload.get("job_info")
        if not isinstance(job_info, dict):
            raise MqttPayloadError("job_info는 JSON object여야 합니다.")
        required = ("diameter", "height", "target_distance")
        missing = [
            name
            for name in required
            if not isinstance(job_info.get(name), str)
            or not job_info[name].strip()
        ]
        if missing:
            raise MqttPayloadError(
                f"job_info 필드가 없거나 비어 있습니다: {', '.join(missing)}"
            )

        # plan: 원통을 편 직사각형의 격자 분할 계획이다. 열=AMR 정차 구역,
        # 행=리프트 높이이며 Cobot은 셀 하나만 ㄹ자로 스캔한다. 셀을 하나씩
        # 순회하는 것은 UI/로봇 쪽 책임이라 지금 몇 번째 셀인지는 담지 않는다.
        # 스캐너 유효높이(scan_h)는 장비 고유값이라 여기 없다.
        plan = payload.get("plan")
        if not isinstance(plan, dict):
            raise MqttPayloadError("plan은 JSON object여야 합니다.")
        plan_required = (
            "column_count", "row_count", "cell_width", "cell_height", "overlap",
        )
        plan_missing = [
            name
            for name in plan_required
            if not isinstance(plan.get(name), str) or not plan[name].strip()
        ]
        if plan_missing:
            raise MqttPayloadError(
                f"plan 필드가 없거나 비어 있습니다: {', '.join(plan_missing)}"
            )
        return

    raise MqttPayloadError(f"정의되지 않은 Command Topic입니다: {topic}")


def _timestamp_int(payload: dict[str, Any]) -> int:
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.isdigit():
        raise MqttPayloadError(
            "timestamp는 UTC Unix Epoch 밀리초 문자열이어야 합니다."
        )
    return int(timestamp)


def _utc_epoch_ms() -> str:
    return str(int(time.time() * 1000))


def _reason_code_int(reason_code: Any) -> int:
    try:
        return int(reason_code)
    except (TypeError, ValueError):
        return int(getattr(reason_code, "value", -1))


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
