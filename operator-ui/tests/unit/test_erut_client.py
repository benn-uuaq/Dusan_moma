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

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        pass


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


def test_error_is_an_error_object_inside_content(qtbot):
    """if-0.9: 맨 바깥은 timestamp 뿐 — code · message 는 없어졌다(탭2 10행)."""
    client, paho = _client(qtbot)
    client.publish_error({"code": "E2001", "name": "DRIVE_ERROR", "level": "stop",
                          "recovery": "manual", "cleared": True}, job_id="jb1")

    _topic, payload, _qos, _retain = paho.published[-1]
    assert set(payload) == {"timestamp", "content"}
    assert payload["content"] == {"job_id": "jb1", "error": {
        "code": "E2001", "name": "DRIVE_ERROR", "level": "stop",
        "recovery": "manual", "cleared": True}}


def test_answers_carry_status_code_and_error_only_when_refused(qtbot):
    """결과는 맨 바깥 status_code, 문제는 content.error — 없으면 칸을 뺀다(탭0 10행)."""
    client, paho = _client(qtbot)
    client.publish_res("r1", "start", 202)
    client.publish_res("r2", "start", 409, {"code": "E6101", "name": "BUSY"})
    client.publish_event("complete", "r1", "start", job_id="jb1")

    accepted, refused, done = (p for _t, p, _q, _r in paho.published[-3:])
    assert accepted["status_code"] == 202 and "error" not in accepted["content"]
    assert "code" not in accepted and "message" not in accepted
    assert refused["status_code"] == 409
    assert refused["content"]["error"] == {"code": "E6101", "name": "BUSY"}
    assert done["status_code"] == 200 and done["content"]["job_id"] == "jb1"


def test_request_carries_the_time_it_was_sent(qtbot):
    """늦게 온 일 요청(E6005)을 가리려고 보낸 시각을 세션까지 넘긴다."""
    client, _paho = _client(qtbot)
    got: list[tuple] = []
    client.request_received.connect(lambda a, c, t: got.append((a, c["req_id"], t)))

    client._on_message(None, None, Msg("doosan/robot/req/start", {
        "timestamp": 1786500000000, "content": {"req_id": "r1"}}))
    client._on_message(None, None, Msg("doosan/robot/req/query", {
        "content": {"req_id": "r2"}}))

    assert got == [("start", "r1", 1786500000000), ("query", "r2", None)]


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


def test_retained_requests_are_dropped(qtbot):
    """브로커에 남은(retain) 요청은 붙을 때마다 다시 온다 — 실행하지 않는다."""
    client, _paho = _client(qtbot)
    got: list = []
    warnings: list[str] = []
    client.request_received.connect(lambda a, c: got.append(a))
    client.error_occurred.connect(warnings.append)
    msg = Msg("doosan/robot/req/calibrate",
              {"timestamp": 0, "content": {"req_id": "cal-001", "diameter": 1690}})
    msg.retain = True

    client._on_message(None, None, msg)

    assert got == []
    assert warnings and "retain" in warnings[-1]


def test_refusal_names_the_broker_and_what_to_check(qtbot):
    """거절 코드만 보여 주면 현장에서 무엇을 고칠지 모른다(2026-10-07 리눅스 PC rc=3).

    어느 주소·포트가 거절했는지와 그 코드의 뜻을 같이 알린다. paho 는 거절당해도
    계속 다시 붙어 보므로 같은 거절은 한 번만 알린다.
    """
    client = ErutClient(ErutConfig(host="10.1.2.3", port=1883, device_id="robot1"))
    told: list[str] = []
    client.error_occurred.connect(told.append)

    client._on_connect(FakePaho(), None, None, 3)
    client._on_connect(FakePaho(), None, None, 3)

    assert len(told) == 1, "같은 거절을 재접속마다 되풀이했다"
    assert "10.1.2.3:1883" in told[0] and "rc=3" in told[0]
    assert "다른 MQTT 서버" in told[0]

    client._on_connect(FakePaho(), None, None, 5)
    assert len(told) == 2 and "ERUT 계정" in told[1]


def test_refusal_is_told_again_after_a_successful_connect(qtbot):
    client = ErutClient(ErutConfig(device_id="robot1"))
    told: list[str] = []
    client.error_occurred.connect(told.append)

    client._on_connect(FakePaho(), None, None, 5)
    client._on_connect(FakePaho(), None, None, 0)
    client._on_connect(FakePaho(), None, None, 5)

    assert len(told) == 2


def test_same_client_id_fight_is_detected_and_this_side_backs_off(qtbot):
    """같은 장치 ID 로 다른 RCS 가 붙으면 브로커가 번갈아 끊는다(2026-10-12).

    붙자마자 끊기기를 연달아 세 번 겪으면 이쪽이 물러난다 — 다시 붙지 않고,
    offline 도 내지 않는다(상대 RCS 의 online 을 덮어쓰지 않게).
    """
    client, paho = _client(qtbot)
    halted: list[str] = []
    client.link_halted.connect(halted.append)

    for _ in range(3):
        client._on_connect(paho, None, None, 0)
        client._on_disconnect(paho, None, 7)          # 붙자마자 끊김(MQTT_ERR_CONN_LOST)
    qtbot.waitUntil(lambda: client.halted, timeout=2000)

    assert halted and "robot1" in halted[0]
    assert client._client is None, "물러났는데 계속 다시 붙는다"
    assert not any(t.endswith("evt/status") for t, *_ in paho.published), \
        "물러나면서 offline 을 내 상대의 online 을 덮었다"


def test_a_long_session_resets_the_fight_count(qtbot):
    """오래 붙어 있다가 끊긴 것은 충돌이 아니다(네트워크 · 브로커 재시작)."""
    client, paho = _client(qtbot)
    for _ in range(2):
        client._on_connect(paho, None, None, 0)
        client._on_disconnect(paho, None, 7)
    client._on_connect(paho, None, None, 0)
    client._connected_at -= 60.0                       # 1분 붙어 있었다
    client._on_disconnect(paho, None, 7)
    client._on_connect(paho, None, None, 0)
    client._on_disconnect(paho, None, 7)
    qtbot.wait(50)

    assert client.halted is False


def test_saving_the_settings_retries_after_backing_off(qtbot):
    client = ErutClient(ErutConfig(device_id="robot1"))
    client._halted = True
    client._enabled = True                           # 앱이 붙으라고 했다
    calls: list[str] = []
    client.start = lambda: calls.append("start")

    assert client.apply_config(client.config) is True
    assert calls == ["start"]



def test_contact_without_a_job_leaves_out_job_id(qtbot):
    """작업이 없으면 job_id 칸을 뺀다 — 빈 글자는 규격에 없는 값이다(if-0.10 Q-41)."""
    client, paho = _client(qtbot)
    client.publish_contact("detached", "")
    assert paho.published[-1][1]["content"] == {"state": "detached"}
    client.publish_contact("attached", "jb1")
    assert paho.published[-1][1]["content"] == {"state": "attached", "job_id": "jb1"}
