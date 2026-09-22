"""연결 설정 화면의 브로커 주소가 실제 접속에 반영되는지.

현장에서는 RCS 가 내부망이 아니라 허브 건너편 브로커에 붙는다. 예전에는
주소를 환경변수(SMR_MQTT_HOST)로만 정할 수 있어, 화면에서 고쳐도 접속은
127.0.0.1 그대로였다.
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import ErutConfig, MqttConfig


def _window(qtbot):
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    return window


def test_connection_screen_has_broker_fields(qtbot) -> None:
    window = _window(qtbot)
    values = window.screens["connection"].values()

    for name in ("MQTT Broker 주소", "MQTT 포트", "MQTT Client ID",
                 "ERUT Broker 주소", "ERUT 포트", "ERUT 장치 ID"):
        assert name in values, name
    window.close()


def test_saved_addresses_reach_both_brokers(qtbot) -> None:
    window = _window(qtbot)
    conn = window.screens["connection"]
    conn.apply_values({
        "MQTT Broker 주소": "10.20.30.40", "MQTT 포트": 8883,
        "MQTT Client ID": "rcs-1", "MQTT Keep Alive": 45, "MQTT TLS 사용": True,
        "ERUT Broker 주소": "10.20.30.41", "ERUT 포트": 1884,
        "ERUT 장치 ID": "robot9",
    })

    window._sync_broker_endpoints()

    assert window.mqtt_server.config.host == "10.20.30.40"
    assert window.mqtt_server.config.port == 8883
    assert window.mqtt_server.config.client_id == "rcs-1"
    assert window.mqtt_server.config.keep_alive == 45
    assert window.mqtt_server.config.tls is True
    assert window.erut.config.host == "10.20.30.41"
    assert window.erut.config.port == 1884
    assert window.erut.config.device_id == "robot9"
    # ERUT 토픽도 장치 ID 를 따라간다.
    assert window.erut.res_topic() == "erut/robot9/res"
    window.close()


def test_blank_client_id_keeps_the_current_one(qtbot) -> None:
    window = _window(qtbot)
    before = window.mqtt_server.config.client_id
    conn = window.screens["connection"]
    conn.apply_values({"MQTT Broker 주소": "10.0.0.9", "MQTT Client ID": ""})

    window._sync_broker_endpoints()

    assert window.mqtt_server.config.client_id == before
    assert window.mqtt_server.config.host == "10.0.0.9"
    window.close()


def test_loading_saved_settings_applies_them(qtbot) -> None:
    """저장된 값을 불러올 때도 반영돼야 한다(예전엔 이 경로가 죽어 있었다)."""
    window = _window(qtbot)

    window._apply_stored_settings("connection", {
        "MQTT Broker 주소": "192.168.50.7", "MQTT 포트": 1883,
        "ERUT Broker 주소": "192.168.50.8", "ERUT 포트": 1883,
        "ERUT 장치 ID": "robot1",
    })

    assert window.mqtt_server.config.host == "192.168.50.7"
    assert window.erut.config.host == "192.168.50.8"
    window.close()


def test_same_address_does_not_reconnect(qtbot) -> None:
    """같은 값으로 저장하면 접속을 건드리지 않는다."""
    window = _window(qtbot)
    calls: list[str] = []
    window.mqtt_server.stop = lambda: calls.append("stop")
    window.mqtt_server.start = lambda: calls.append("start")

    assert window.mqtt_server.apply_config(window.mqtt_server.config) is False
    assert calls == []
    window.close()


def test_changing_the_address_while_connected_reconnects(qtbot) -> None:
    window = _window(qtbot)
    calls: list[str] = []
    server = window.mqtt_server
    server._client = object()          # 붙어 있는 상태로 둔다
    server.stop = lambda: calls.append("stop")
    server.start = lambda: calls.append("start")

    assert server.apply_config(MqttConfig(host="10.1.1.1", client_id="x")) is True
    assert calls == ["stop", "start"]
    assert server.config.host == "10.1.1.1"

    erut_calls: list[str] = []
    window.erut._client = object()
    window.erut.stop = lambda: erut_calls.append("stop")
    window.erut.start = lambda: erut_calls.append("start")
    assert window.erut.apply_config(ErutConfig(host="10.1.1.2", client_id="y")) is True
    assert erut_calls == ["stop", "start"]
    window.close()
