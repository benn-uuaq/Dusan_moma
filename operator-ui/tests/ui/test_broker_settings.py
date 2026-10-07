"""연결 설정 화면의 ERUT 브로커 주소가 실제 접속에 반영되는지.

현장에서는 RCS 가 내부망이 아니라 허브 건너편(나중에는 랜선으로 ERUT 쪽 PC)
브로커에 붙는다. 외부 통신은 ERUT 브로커 하나다 — 사내 MC MQTT 는
2026-10-07 에 뺐다.
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import ErutConfig


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    return window


def test_connection_screen_has_only_the_erut_broker(qtbot) -> None:
    window = _window(qtbot)
    values = window.screens["connection"].values()

    for name in ("ERUT Broker 주소", "ERUT 포트", "ERUT 장치 ID",
                 "ERUT 계정", "ERUT 비밀번호"):
        assert name in values, name
    assert not any(name.startswith("MQTT") for name in values), "사내 MC 브로커 칸이 남았다"
    window.close()


def test_saved_address_reaches_the_erut_broker(qtbot) -> None:
    window = _window(qtbot)
    conn = window.screens["connection"]
    conn.apply_values({
        "ERUT Broker 주소": "10.20.30.41", "ERUT 포트": 1884,
        "ERUT 장치 ID": "robot9", "ERUT 계정": "erut-3s", "ERUT 비밀번호": "pw",
    })

    window._sync_broker_endpoints()

    assert window.erut.config.host == "10.20.30.41"
    assert window.erut.config.port == 1884
    assert window.erut.config.device_id == "robot9"
    # ERUT 가 정해 준 계정으로 붙는다.
    assert (window.erut.config.username, window.erut.config.password) == ("erut-3s", "pw")
    # ERUT 토픽도 장치 ID 를 따라간다.
    assert window.erut.res_topic() == "erut/robot9/res"
    window.close()


def test_loading_saved_settings_applies_them(qtbot) -> None:
    """저장된 값을 불러올 때도 반영돼야 한다(예전엔 이 경로가 죽어 있었다)."""
    window = _window(qtbot)

    window._apply_stored_settings("connection", {
        "ERUT Broker 주소": "192.168.50.8", "ERUT 포트": 1883,
        "ERUT 장치 ID": "robot1",
    })

    assert window.erut.config.host == "192.168.50.8"
    window.close()


def test_changing_the_address_while_connected_reconnects(qtbot) -> None:
    window = _window(qtbot)
    calls: list[str] = []
    window.erut._client = object()          # 붙어 있는 상태로 둔다
    window.erut.stop = lambda: calls.append("stop")
    window.erut.start = lambda: calls.append("start")

    assert window.erut.apply_config(window.erut.config) is False, "같은 값이면 그대로 둔다"
    assert window.erut.apply_config(ErutConfig(host="10.1.1.2", client_id="y")) is True
    assert calls == ["stop", "start"]
    window.close()


def test_erut_password_is_masked_on_screen(qtbot) -> None:
    window = _window(qtbot)
    field = window.screens["connection"].field("ERUT 비밀번호")

    assert field.echoMode() == field.EchoMode.Password
    window.close()


def test_erut_connects_only_after_the_saved_settings_are_read(qtbot, monkeypatch) -> None:
    """설정을 읽기 전에 붙으면 기본값 127.0.0.1:1883 에 먼저 붙는다.

    그 PC 의 1883 에 다른 브로커가 있으면 엉뚱한 접속 거부가 뜬다(2026-10-07
    리눅스 PC rc=3). 저장된 연결 설정이 반영된 뒤 한 번만 붙어야 한다.
    """
    from smr_operator_ui.services import ErutClient, ErutSession

    hosts: list[str] = []
    monkeypatch.setattr(ErutClient, "start", lambda self: hosts.append(self.config.host))
    monkeypatch.setattr(ErutSession, "start", lambda self: None)
    window = OperatorWindow(start_ros=False, start_erut=True)
    qtbot.addWidget(window)
    window._erut_start_pending = True      # 비동기 설정 읽기가 먼저 끝났을 수 있다
    hosts.clear()

    window._apply_stored_settings("connection", {
        "ERUT Broker 주소": "10.20.30.41", "ERUT 포트": 18884,
    })
    window._apply_stored_settings("connection", {"ERUT Broker 주소": "10.20.30.41"})

    assert hosts == ["10.20.30.41"], "설정 전 기본값으로 붙었거나 두 번 붙었다"
    window.close()


def test_erut_still_connects_when_the_settings_cannot_be_read(qtbot, monkeypatch) -> None:
    from smr_operator_ui.services import ErutClient, ErutSession

    hosts: list[str] = []
    monkeypatch.setattr(ErutClient, "start", lambda self: hosts.append(self.config.host))
    monkeypatch.setattr(ErutSession, "start", lambda self: None)
    window = OperatorWindow(start_ros=False, start_erut=True)
    qtbot.addWidget(window)
    window._erut_start_pending = True
    hosts.clear()

    window._show_settings_error("connection", "설정 파일을 읽을 수 없습니다")

    assert hosts == ["127.0.0.1"]
    window.close()


def test_erut_is_not_started_from_the_constructor(qtbot, monkeypatch) -> None:
    from smr_operator_ui.services import ErutClient, ErutSession

    hosts: list[str] = []
    monkeypatch.setattr(ErutClient, "start", lambda self: hosts.append(self.config.host))
    monkeypatch.setattr(ErutSession, "start", lambda self: None)
    window = OperatorWindow(start_ros=False, start_erut=True)
    qtbot.addWidget(window)

    assert hosts == [], "설정을 읽기 전에 ERUT 에 붙었다"
    window.close()
