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


def test_job_command_requires_all_job_info_fields() -> None:
    valid_payload = {
        "timestamp": "1784727779111",
        "job_id": "jb00000001",
        "job_info": {
            "diameter": "2500",
            "height": "6000",
            "thickness": "500",
            "target_distance": "8560",
        },
    }
    validate_command_payload(MqttTopics.JOB_COMMAND, valid_payload)

    del valid_payload["job_info"]["height"]
    with pytest.raises(MqttPayloadError, match="height"):
        validate_command_payload(MqttTopics.JOB_COMMAND, valid_payload)


def test_timestamp_must_be_epoch_millisecond_string() -> None:
    with pytest.raises(MqttPayloadError, match="timestamp"):
        validate_command_payload(
            MqttTopics.EMS,
            {"timestamp": 1784727779111, "request": "true"},
        )
