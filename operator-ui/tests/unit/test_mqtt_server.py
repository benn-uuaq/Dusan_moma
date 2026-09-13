"""MQTT Topic 설정과 Command Payload 검증 테스트."""

import pytest

from smr_operator_ui.services.mqtt_server import (
    MqttConfig,
    MqttPayloadError,
    MqttServer,
    MqttTopics,
    validate_command_payload,
)


def test_default_config_matches_topic_document() -> None:
    config = MqttConfig(client_id="operator-ui-test")

    assert config.host == "127.0.0.1"
    assert config.port == 1883
    assert config.qos == 1
    assert config.clean_session is True
    assert config.tls is False
    assert config.max_reconnect_attempts == 10


def test_reconnect_stops_after_configured_failures(qtbot) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.disconnect_calls = 0
            self.loop_stop_calls = 0

        def disconnect(self) -> None:
            self.disconnect_calls += 1

        def loop_stop(self) -> None:
            self.loop_stop_calls += 1

    service = MqttServer(
        MqttConfig(
            client_id="operator-ui-test",
            max_reconnect_attempts=10,
        )
    )
    fake_client = FakeClient()
    service._client = fake_client

    for _ in range(10):
        service._on_connect_fail(fake_client, None)

    assert service._client is None
    assert fake_client.disconnect_calls == 1
    assert fake_client.loop_stop_calls == 1


def test_mc_command_accepts_one_target_only() -> None:
    validate_command_payload(
        MqttTopics.MC_COMMAND,
        {"timestamp": "1784727720000", "amr": "stop"},
    )


def test_mc_command_rejects_unknown_value() -> None:
    with pytest.raises(MqttPayloadError, match="허용되지 않은 AMR"):
        validate_command_payload(
            MqttTopics.MC_COMMAND,
            {"timestamp": "1784727720000", "amr": "unknown"},
        )


def test_request_command_uses_string_boolean() -> None:
    validate_command_payload(
        MqttTopics.RESET,
        {"timestamp": "1784727779111", "request": "true"},
    )

    with pytest.raises(MqttPayloadError, match="request"):
        validate_command_payload(
            MqttTopics.RESET,
            {"timestamp": "1784727779111", "request": True},
        )


def _valid_job_command_payload() -> dict:
    """job_cmd 유효 페이로드(전체 작업 시작). 원통을 편 직사각형을 열(AMR
    정차 구역) × 행(리프트 높이)으로 나눈 계획(plan)이 job_info와 함께 온다.
    지금 몇 번째 셀인지는 UI/로봇이 자동으로 순회하므로 담지 않고, 스캐너
    유효높이(scan_h)는 장비 고유값이라 수신 측 로컬 설정을 쓴다."""
    return {
        "timestamp": "1784727779111",
        "job_id": "jb00000001",
        "job_info": {
            "diameter": "2500",
            "height": "6000",
            "target_distance": "8560",
        },
        "plan": {
            "column_count": "12",
            "row_count": "6",
            "cell_width": "600",
            "cell_height": "800",
            "overlap": "20",
        },
    }


def test_job_command_requires_all_job_info_fields() -> None:
    valid_payload = _valid_job_command_payload()
    validate_command_payload(MqttTopics.JOB_COMMAND, valid_payload)

    del valid_payload["job_info"]["height"]
    with pytest.raises(MqttPayloadError, match="height"):
        validate_command_payload(MqttTopics.JOB_COMMAND, valid_payload)


def test_job_command_requires_plan_block() -> None:
    """격자 분할 계획(plan) 없이는 어떻게 나눠 스캔할지 알 수 없다."""
    payload = _valid_job_command_payload()
    del payload["plan"]
    with pytest.raises(MqttPayloadError, match="plan"):
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_job_command_requires_all_plan_fields() -> None:
    payload = _valid_job_command_payload()
    del payload["plan"]["cell_height"]
    with pytest.raises(MqttPayloadError, match="cell_height"):
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_publish_job_command_builds_plan_payload() -> None:
    """publish_job_command()가 만드는 페이로드 자체가 검증을 통과해야 한다."""
    server = MqttServer()
    sent: list[tuple[str, dict]] = []
    server.publish = lambda topic, payload, **_: sent.append((topic, payload)) or True  # type: ignore[method-assign]

    ok = server.publish_job_command(
        "jb00000002",
        diameter=2500, height=6000, target_distance=8560,
        column_count=12, row_count=6, cell_width=600, cell_height=800, overlap=20,
    )

    assert ok is True
    topic, payload = sent[0]
    assert topic == MqttTopics.JOB_COMMAND
    assert payload["plan"]["column_count"] == "12"
    assert payload["plan"]["cell_height"] == "800"
    # 원통 전체 높이(6000)가 셀 높이로 새어 들어가면 안 된다.
    assert payload["plan"]["cell_height"] != payload["job_info"]["height"]
    validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_timestamp_must_be_epoch_millisecond_string() -> None:
    with pytest.raises(MqttPayloadError, match="timestamp"):
        validate_command_payload(
            MqttTopics.EMS,
            {"timestamp": 1784727779111, "request": "true"},
        )


def test_publish_tcp_tags_zero_pose_with_cell() -> None:
    """제로점 좌표는 격자 이름과 함께 나가야 한다.

    좌표만으로는 원통 어디를 잰 값인지 알 수 없다. 스캐너 관리 시스템이
    위치를 복원하려면 `1A`, `12F` 같은 격자 이름이 함께 필요하다.
    """
    server = MqttServer()
    sent: list[tuple[str, dict]] = []
    server.publish = lambda topic, payload, **_: sent.append((topic, payload)) or True  # type: ignore[method-assign]

    # [X, Y, Z, Rx, Ry, Rz], 위치는 mm 회전은 mrad.
    ok = server.publish_tcp([120.5, -85.2, 1208.0, 0.0, 0.0, -1214.0], "7C")

    assert ok is True
    topic, payload = sent[0]
    assert topic == MqttTopics.TCP
    assert payload["cell"] == "7C"
    assert payload["x"] == "120.5"
    assert payload["z"] == "1208.0"
    # 회전은 mrad -> radian으로 바꿔 보낸다.
    assert payload["yaw"] == "-1.214"


def test_publish_tcp_rejects_short_pose() -> None:
    """성분이 모자란 자세로 엉뚱한 좌표를 내보내지 않는다."""
    server = MqttServer()
    with pytest.raises(MqttPayloadError, match="6개"):
        server.publish_tcp([1.0, 2.0, 3.0], "1A")


def test_publish_job_state_uses_cell_as_job_id() -> None:
    server = MqttServer()
    sent: list[tuple[str, dict]] = []
    server.publish = lambda topic, payload, **_: sent.append((topic, payload)) or True  # type: ignore[method-assign]

    assert server.publish_job_state("12F", "completed") is True
    topic, payload = sent[0]
    assert topic == MqttTopics.JOB_STATE
    assert payload["job_id"] == "12F"
    assert payload["state"] == "completed"


def test_publish_job_state_rejects_unknown_state() -> None:
    server = MqttServer()
    with pytest.raises(MqttPayloadError, match="허용되지 않은"):
        server.publish_job_state("1A", "finished")


def test_speed_command_accepts_valid_range() -> None:
    validate_command_payload(
        MqttTopics.SPEED, {"timestamp": "1784727779111", "speed": "40"}
    )


def test_speed_command_rejects_out_of_range() -> None:
    """로봇 컨트롤러가 받는 범위는 2~100 이다."""
    for bad in ("1", "101", "0", "-5"):
        with pytest.raises(MqttPayloadError, match="범위"):
            validate_command_payload(
                MqttTopics.SPEED, {"timestamp": "1784727779111", "speed": bad}
            )


def test_speed_command_rejects_non_integer() -> None:
    with pytest.raises(MqttPayloadError, match="정수"):
        validate_command_payload(
            MqttTopics.SPEED, {"timestamp": "1784727779111", "speed": "빠르게"}
        )


def test_publish_speed_builds_valid_payload() -> None:
    server = MqttServer()
    sent: list[tuple[str, dict]] = []
    server.publish = lambda topic, payload, **_: sent.append((topic, payload)) or True  # type: ignore[method-assign]

    assert server.publish_speed(55) is True
    topic, payload = sent[0]
    assert topic == MqttTopics.SPEED
    assert payload["speed"] == "55"
    validate_command_payload(MqttTopics.SPEED, payload)


def test_probe_ack_payload_is_validated() -> None:
    """프로브 확인도 다른 명령과 같은 형식 검사를 받는다.

    timestamp 는 **문자열**이어야 한다 — 정수로 보내면 거부된다.
    예전에는 시뮬레이터가 정수로 보내 알람만 뜨고 게이트는 안 풀렸다.
    """
    from smr_operator_ui.services.mqtt_server import (
        MqttPayloadError, MqttTopics, validate_command_payload,
    )

    validate_command_payload(
        MqttTopics.PROBE_ACK, {"timestamp": "1786500000000", "pressed": True})
    validate_command_payload(
        MqttTopics.PROBE_ACK, {"timestamp": "1786500000000", "pressed": "false"})

    with pytest.raises(MqttPayloadError):
        validate_command_payload(
            MqttTopics.PROBE_ACK, {"timestamp": 1786500000000, "pressed": True})
    with pytest.raises(MqttPayloadError):
        validate_command_payload(
            MqttTopics.PROBE_ACK, {"timestamp": "1786500000000", "pressed": "maybe"})
