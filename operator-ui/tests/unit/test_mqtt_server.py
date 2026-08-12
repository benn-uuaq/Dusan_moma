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
    """job_cmd 유효 페이로드. 원통이 커서 AMR 구역 + Cobot 격자로 나눠
    스캔하므로 job_info 외에 grid 블록도 함께 필요하다."""
    return {
        "timestamp": "1784727779111",
        "job_id": "jb00000001",
        "job_info": {
            "diameter": "2500",
            "height": "6000",
            "thickness": "500",
            "target_distance": "8560",
        },
        "grid": {
            "cell_id": "A0",
            "segment_index": "1",
            "segment_count": "12",
            "grid_index": "0",
            "grid_count": "9",
            "width": "600",
            "height": "800",
            "scan_h": "150",
            "overlap": "20",
        },
    }


def test_job_command_requires_all_job_info_fields() -> None:
    valid_payload = _valid_job_command_payload()
    validate_command_payload(MqttTopics.JOB_COMMAND, valid_payload)

    del valid_payload["job_info"]["height"]
    with pytest.raises(MqttPayloadError, match="height"):
        validate_command_payload(MqttTopics.JOB_COMMAND, valid_payload)


def test_job_command_requires_grid_block() -> None:
    """원통이 너무 커서 격자(grid) 정보 없이는 어느 칸을 스캔할지 알 수 없다."""
    payload = _valid_job_command_payload()
    del payload["grid"]
    with pytest.raises(MqttPayloadError, match="grid"):
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_job_command_requires_all_grid_fields() -> None:
    payload = _valid_job_command_payload()
    del payload["grid"]["segment_count"]
    with pytest.raises(MqttPayloadError, match="segment_count"):
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_job_command_requires_nonempty_cell_id() -> None:
    payload = _valid_job_command_payload()
    payload["grid"]["cell_id"] = "  "
    with pytest.raises(MqttPayloadError, match="cell_id"):
        validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_publish_job_command_builds_grid_payload() -> None:
    """publish_job_command()가 만드는 페이로드 자체가 검증을 통과해야 한다."""
    server = MqttServer()
    sent: list[tuple[str, dict]] = []
    server.publish = lambda topic, payload, **_: sent.append((topic, payload)) or True  # type: ignore[method-assign]

    ok = server.publish_job_command(
        "jb00000002",
        diameter=2500, height=6000, thickness=500, target_distance=8560,
        cell_id="B3", segment_index=2, segment_count=12,
        grid_index=3, grid_count=9,
        grid_width=600, grid_height=800, scan_h=150, overlap=20,
    )

    assert ok is True
    topic, payload = sent[0]
    assert topic == MqttTopics.JOB_COMMAND
    assert payload["grid"]["cell_id"] == "B3"
    assert payload["grid"]["segment_index"] == "2"
    validate_command_payload(MqttTopics.JOB_COMMAND, payload)


def test_timestamp_must_be_epoch_millisecond_string() -> None:
    with pytest.raises(MqttPayloadError, match="timestamp"):
        validate_command_payload(
            MqttTopics.EMS,
            {"timestamp": 1784727779111, "request": "true"},
        )
