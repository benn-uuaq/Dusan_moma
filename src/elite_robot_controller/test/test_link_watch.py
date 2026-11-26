"""로봇 세 채널(29999 · 30001 · 502) 끊김 감지 · 30001 자동 재연결 (2026-11-24).

현장: 로봇이 30001(Primary)을 닫았는데 노드는 수신 예외를 삼켜 몰랐다. Modbus 는 살아 있어 「연결됨」으로 남았고,
홈 · 조그 스크립트가 「Broken pipe」로 계속 실패했다. 이제 끊김을 보면 알리고(link_detail) 그 채널만 다시 붙는다.
"""

import socket
import threading
import time

from elite_robot_controller.robot.robot_control_node import RobotControlNode
from elite_robot_controller.robot.robot_driver import Robot_29999, Robot_30001


class Server:
    """접속을 받기만 하는 가짜 로봇 포트. close_clients() 로 로봇이 연결을 닫은 것을 흉내 낸다."""

    def __init__(self, greeting=b""):
        self.greeting = greeting
        self.clients = []
        self.received = b""
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self._srv.settimeout(0.1)
        self.port = self._srv.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                continue
            if self.greeting:
                conn.sendall(self.greeting)
            conn.settimeout(0.05)
            self.clients.append(conn)
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        while not self._stop:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            self.received += data

    def close_clients(self):
        for conn in self.clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
                conn.close()
            except OSError:
                pass
        self.clients = []

    def stop(self):
        self._stop = True
        self.close_clients()
        self._srv.close()


def _wait(predicate, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_primary_notices_the_robot_closing_the_connection():
    server = Server()
    try:
        primary = Robot_30001("127.0.0.1", server.port)
        assert primary.connect_30001() and primary.alive
        assert _wait(lambda: server.clients)
        server.close_clients()
        assert _wait(lambda: (primary.get_data(), not primary.alive)[1]), "EOF 를 보면 끊긴 것으로 안다"
        assert "닫았습니다" in primary.last_error
        assert primary.send_script("textmsg(1)") is False
        assert primary.connect_30001() and primary.send_script("textmsg(2)")
        assert _wait(lambda: b"textmsg(2)" in server.received)
    finally:
        server.stop()


def test_dashboard_check_alive_sees_a_closed_socket():
    server = Server(greeting=b"Connected: Elite Dashboard Server\n")
    try:
        dash = Robot_29999("127.0.0.1", server.port)
        assert dash.connect_29999() and dash.check_alive()
        assert _wait(lambda: server.clients)
        server.close_clients()
        assert _wait(lambda: not dash.check_alive())
        assert "29999" in dash.last_error
        assert dash.reconnect() and dash.check_alive()
    finally:
        server.stop()


class _Pub:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg.data)


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, text):
        self.lines.append(text)

    warn = warning = error = info


def _node(primary, dash):
    node = RobotControlNode.__new__(RobotControlNode)
    node.robot_ip = "127.0.0.1"
    node.robot_primary, node.robot_dash = primary, dash
    node._logger = _Logger()
    node.get_logger = lambda: node._logger
    node.pub_link_detail, node.pub_connected = _Pub(), _Pub()
    node._want_connected = True
    node._links = {"dashboard": True, "primary": True, "modbus": True}
    node.connected = True
    node._last_dash_check = 0.0
    node._last_partial_retry = 0.0
    return node


def test_node_reports_which_port_dropped_and_reconnects_only_that_one():
    primary_srv, dash_srv = Server(), Server(greeting=b"Connected\n")
    try:
        primary = Robot_30001("127.0.0.1", primary_srv.port)
        dash = Robot_29999("127.0.0.1", dash_srv.port)
        assert primary.connect_30001() and dash.connect_29999()
        node = _node(primary, dash)
        assert _wait(lambda: primary_srv.clients)
        primary_srv.close_clients()
        assert _wait(lambda: (primary.get_data(), not primary.alive)[1])
        node._watch_links()
        assert node.connected is False and node._links["modbus"] is True
        assert node.pub_link_detail.sent[-1].startswith("끊김 30001(Primary)")
        assert node.pub_connected.sent[-1] is False, "하나라도 끊기면 연결 안 됨"
        node._last_partial_retry = 0.0
        node._watch_links()                            # 다음 주기 — 30001 만 다시 붙인다
        assert node._links["primary"] and node.connected is True
        assert node.pub_link_detail.sent[-1] == "다시 연결됨 30001(Primary)"
    finally:
        primary_srv.stop()
        dash_srv.stop()


def test_send_script_reconnects_once_and_resends():
    server = Server()
    try:
        primary = Robot_30001("127.0.0.1", server.port)
        assert primary.connect_30001()
        node = _node(primary, Robot_29999("127.0.0.1", 1))
        assert _wait(lambda: server.clients)
        server.close_clients()
        assert _wait(lambda: (primary.get_data(), not primary.alive)[1])
        assert node._send_script("movej([0,0,0,0,0,0])") is True, "끊겨 있으면 다시 붙어 보낸다"
        assert _wait(lambda: b"movej" in server.received)
    finally:
        server.stop()
