"""연결 확인 — PLC 포트(TCP 접속) · ping (2026-11-24)."""

import shutil
import socket

import pytest

from smr_operator_ui.services.link_probe import ping, probe_tcp


def test_open_port_is_reported_open_and_closed_port_refused():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        result = probe_tcp("127.0.0.1", port)
        assert result.ok and result.kind == "open" and f"127.0.0.1:{port}" in result.text
    finally:
        server.close()
    closed = probe_tcp("127.0.0.1", port, timeout_s=1.0)
    assert not closed.ok and closed.kind == "refused" and "포트를 받지 않습니다" in closed.text


def test_bad_addresses_are_refused_before_connecting():
    assert probe_tcp("", 8504).kind == "bad_address"
    assert probe_tcp("192.168.11.10", 0).kind == "bad_address"
    assert probe_tcp("1.2.3.4; rm", 8504).kind == "bad_address"
    assert ping("").kind == "bad_address"


@pytest.mark.skipif(shutil.which("ping") is None, reason="ping 이 없다")
def test_ping_localhost_replies():
    result = ping("127.0.0.1")
    assert result.ok and result.kind == "reply"
