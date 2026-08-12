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

    ROBOT_STATE = "doosan/robot/robot_state"
    ERROR = "doosan/robot/error"
    TCP = "doosan/robot/tcp"
    JOB_STATE = "doosan/robot/job_state"

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
    )
    STATUSES = (
        ROBOT_STATE,
        ERROR,
        TCP,
        JOB_STATE,
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
        thickness: str | int | float,
        target_distance: str | int | float,
        cell_id: str,
        segment_index: str | int,
        segment_count: str | int,
        grid_index: str | int,
        grid_count: str | int,
        grid_width: str | int | float,
        grid_height: str | int | float,
        scan_h: str | int | float,
        overlap: str | int | float,
    ) -> bool:
        """검사 대상과 격자 위치 정보가 포함된 새 Job 명령을 발행한다.

        원통이 커서 AMR 원주 구역(segment)과 Cobot 세로 격자(grid)로 나눠
        스캔한다. cell_id는 보통 "A0"처럼 구역 문자 + 격자 번호다.
        """
        payload = {
            "timestamp": _utc_epoch_ms(),
            "job_id": job_id,
            "job_info": {
                "diameter": str(diameter).strip(),
                "height": str(height).strip(),
                "thickness": str(thickness).strip(),
                "target_distance": str(target_distance).strip(),
            },
            "grid": {
                "cell_id": str(cell_id).strip(),
                "segment_index": str(segment_index).strip(),
                "segment_count": str(segment_count).strip(),
                "grid_index": str(grid_index).strip(),
                "grid_count": str(grid_count).strip(),
                "width": str(grid_width).strip(),
                "height": str(grid_height).strip(),
                "scan_h": str(scan_h).strip(),
                "overlap": str(overlap).strip(),
            },
        }
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)
        return self.publish(MqttTopics.JOB_COMMAND, payload)

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
        if cobot is not None and cobot not in {"run", "stop", "ems"}:
            raise MqttPayloadError(f"허용되지 않은 Cobot 명령입니다: {cobot!r}")
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
        required = ("diameter", "height", "thickness", "target_distance")
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

        # grid: 원통이 커서 AMR 원주 구역 + Cobot 세로 격자로 나눠 스캔하기
        # 위한 정보다. cell_id만 문자열이고 나머지는 job_info와 같은 규칙으로
        # 숫자를 담은 문자열이다.
        grid = payload.get("grid")
        if not isinstance(grid, dict):
            raise MqttPayloadError("grid는 JSON object여야 합니다.")
        if not isinstance(grid.get("cell_id"), str) or not grid["cell_id"].strip():
            raise MqttPayloadError("grid.cell_id가 없거나 비어 있습니다.")
        grid_required = (
            "segment_index", "segment_count", "grid_index", "grid_count",
            "width", "height", "scan_h", "overlap",
        )
        grid_missing = [
            name
            for name in grid_required
            if not isinstance(grid.get(name), str) or not grid[name].strip()
        ]
        if grid_missing:
            raise MqttPayloadError(
                f"grid 필드가 없거나 비어 있습니다: {', '.join(grid_missing)}"
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
