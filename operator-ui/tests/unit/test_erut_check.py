"""ERUT 브로커 점검 도구(run_rcs.sh 가 켜기 전에 부른다)."""

from smr_operator_ui import erut_check


def test_missing_values_fall_back_to_the_screen_defaults() -> None:
    ep = erut_check.endpoint({})

    assert (ep["host"], ep["port"], ep["device_id"]) == ("127.0.0.1", 1883, "robot1")
    assert ep["username"] == "" and ep["password"] == ""


def test_saved_values_are_used_as_rcs_would() -> None:
    ep = erut_check.endpoint({"ERUT Broker 주소": " 10.0.0.9 ", "ERUT 포트": 18884,
                              "ERUT 장치 ID": "robot2", "ERUT 계정": "acct",
                              "ERUT 비밀번호": "pw"})

    assert (ep["host"], ep["port"], ep["device_id"]) == ("10.0.0.9", 18884, "robot2")
    assert (ep["username"], ep["password"]) == ("acct", "pw")


def _fake(monkeypatch, values, tcp="", connack=(0, "")):
    monkeypatch.setattr(erut_check, "load_connection", lambda: (values, "test"))
    monkeypatch.setattr(erut_check, "tcp_reachable", lambda host, port: tcp)
    monkeypatch.setattr(erut_check, "mqtt_connack", lambda ep: connack)
    monkeypatch.setattr(erut_check, "local_listener", lambda port: "")


SAVED = {"ERUT Broker 주소": "10.0.0.9", "ERUT 포트": 18884,
         "ERUT 계정": "acct", "ERUT 비밀번호": "secret-pw"}


def test_refusal_is_explained_and_fails(monkeypatch, capsys) -> None:
    _fake(monkeypatch, SAVED, connack=(3, ""))

    assert erut_check.main() == 1
    out = capsys.readouterr().out
    assert "rc=3" in out and "다른 브로커" in out
    assert "secret-pw" not in out, "비밀번호가 화면에 찍혔다"


def test_unreachable_broker_fails_without_mqtt(monkeypatch, capsys) -> None:
    _fake(monkeypatch, SAVED, tcp="ConnectionRefusedError")

    assert erut_check.main() == 2
    assert "닿지 않습니다" in capsys.readouterr().out


def test_unsaved_address_is_pointed_out(monkeypatch, capsys) -> None:
    _fake(monkeypatch, {})

    assert erut_check.main() == 0
    out = capsys.readouterr().out
    assert "저장된 ERUT 브로커 주소가 없어" in out
    assert "로컬 시험용" in out


def test_good_connection_passes(monkeypatch, capsys) -> None:
    _fake(monkeypatch, SAVED, connack=(0, "mosquitto version 2.1.2"))

    assert erut_check.main() == 0
    assert "[확인]" in capsys.readouterr().out
