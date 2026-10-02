"""ERUT 전송 계층 — 봉투·구독·생존 감시 (if-0.4 탭2)."""

import json
import time

from smr_operator_ui.services.erut_client import (
    ERUT_SILENCE_S, ErutClient, ErutConfig,
)


class FakePaho:
    def __init__(self):
        self.published: list[tuple[str, dict, int, bool]] = []
        self.subscribed: list[str] = []

    def publish(self, topic, text, qos=1, retain=False):
        self.published.append((topic, json.loads(text), qos, retain))

    def subscribe(self, topic, qos=1):
        self.subscribed.append(topic)


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")
        self.retain = False


def _client(qtbot):
    client = ErutClient(ErutConfig(device_id="robot1"))
    paho = FakePaho()
    client._client = paho
    client._connected = True
    return client, paho


def test_connection_settings_follow_the_draft(qtbot):
    """클라이언트 ID 는 로봇마다 고정, keepalive 10~20초(탭2 초안 「접속」)."""
    config = ErutConfig(device_id="robot7")

    assert config.client_id == "3s-robot7"
    assert ErutConfig(device_id="robot7").client_id == config.client_id
    assert 10 <= config.keep_alive <= 20


def test_requests_are_subscribed_with_one_wildcard(qtbot):
    """모르는 동작에도 501 로 답하려면 한 줄로 받아야 한다(탭2 17행)."""
    client, paho = _client(qtbot)
    client._on_connect(paho, None, None, 0)

    assert "doosan/robot/req/#" in paho.subscribed
    assert "erut/status" in paho.subscribed


def test_unknown_action_reaches_the_session(qtbot):
    client, _paho = _client(qtbot)
    got: list = []
    client.request_received.connect(lambda a, c: got.append((a, c)))

    client._on_message(None, None, Msg("doosan/robot/req/teleport",
                                       {"timestamp": 1, "content": {"req_id": "x"}}))

    assert got == [("teleport", {"req_id": "x"})]


def test_mc_topics_on_the_same_prefix_are_left_alone(qtbot):
    """사내 MC 규격(job_cmd 등)은 남의 요청이다 — 501 로 답하면 안 된다."""
    client, _paho = _client(qtbot)
    got: list = []
    client.request_received.connect(lambda a, c: got.append(a))

    client._on_message(None, None, Msg("doosan/robot/req/job_cmd",
                                       {"timestamp": "1", "content": {"req_id": "x"}}))
    # MC reset 은 이름이 같지만 content 래퍼가 없다.
    client._on_message(None, None, Msg("doosan/robot/req/reset",
                                       {"timestamp": "1", "request": True}))

    assert got == []


def test_status_is_flat_and_drops_empty_fields(qtbot):
    client, paho = _client(qtbot)
    client.publish_status("online", activity="idle", calibrated=True,
                          job_id=None, battery=None)

    topic, payload, qos, retain = paho.published[-1]
    assert topic == "erut/robot1/evt/status"
    assert (qos, retain) == (1, True)
    assert "content" not in payload
    assert payload["state"] == "online" and payload["activity"] == "idle"
    assert "job_id" not in payload and "battery" not in payload


def test_error_carries_cleared_inside_content(qtbot):
    client, paho = _client(qtbot)
    client.publish_error("E2001", "DRIVE_ERROR", "stop", "manual", cleared=True,
                         job_id="jb1")

    _topic, payload, _qos, _retain = paho.published[-1]
    assert payload["code"] == "E2001" and payload["message"] == "DRIVE_ERROR"
    assert payload["content"] == {"level": "stop", "recovery": "manual",
                                  "cleared": True, "job_id": "jb1"}


def test_contact_and_info_are_retained(qtbot):
    client, paho = _client(qtbot)
    client.publish_contact("attached", "jb1")
    client.publish_info({"interface_version": "0.4"})

    contact, info = paho.published[-2], paho.published[-1]
    assert contact[0] == "erut/robot1/evt/contact" and contact[3] is True
    assert contact[1]["content"] == {"state": "attached", "job_id": "jb1"}
    assert info[0] == "erut/robot1/evt/info" and info[3] is True


def test_silent_bridge_is_treated_as_offline(qtbot):
    """유언이 안 떠도(브릿지가 굳어도) 20초 넘게 조용하면 없는 것으로 본다."""
    client, _paho = _client(qtbot)
    changes: list[bool] = []
    client.erut_online_changed.connect(changes.append)

    client._on_message(None, None, Msg("erut/status", {"timestamp": 1, "state": "online"}))
    client._check_erut_silence()
    assert changes == [True]

    client._erut_seen = time.monotonic() - ERUT_SILENCE_S - 1
    client._check_erut_silence()
    assert changes == [True, False]


def test_home_is_retained_with_state_in_content(qtbot):
    client, paho = _client(qtbot)
    client.publish_home("deployed")

    topic, payload, qos, retain = paho.published[-1]
    assert topic == "erut/robot1/evt/home"
    assert (qos, retain) == (1, True)
    assert payload["content"] == {"state": "deployed"}


def test_account_is_used_and_hidden_from_repr(qtbot):
    """ERUT 가 준 계정으로 붙는다. 비밀번호는 로그·repr 에 찍히지 않는다."""
    config = ErutConfig(host="10.0.0.1", port=42883, username="erut-3s", password="secret")

    assert config.username == "erut-3s"
    assert "secret" not in repr(config)


def test_bridge_clock_skew_is_reported(qtbot):
    """ERUT 는 timestamp 가 10초 넘게 어긋난 메시지를 버린다 — 5초 넘으면 알린다."""
    client, _paho = _client(qtbot)
    skews: list[float] = []
    warnings: list[str] = []
    client.clock_skew_measured.connect(skews.append)
    client.error_occurred.connect(warnings.append)

    now_ms = int(time.time() * 1000)
    client._on_message(None, None, Msg("erut/status", {"timestamp": now_ms + 1400, "state": "online"}))
    assert abs(skews[-1] - 1.4) < 0.5
    assert warnings == []

    client._on_message(None, None, Msg("erut/status", {"timestamp": now_ms + 8000, "state": "online"}))
    assert warnings and "NTP" in warnings[-1]


def test_retained_status_is_not_used_for_the_clock(qtbot):
    """retain 으로 온 것은 예전 값이다 — 시각차를 재면 엉뚱한 경고가 난다."""
    client, _paho = _client(qtbot)
    skews: list[float] = []
    client.clock_skew_measured.connect(skews.append)
    msg = Msg("erut/status", {"timestamp": 1, "state": "online"})
    msg.retain = True

    client._on_message(None, None, msg)

    assert skews == []
