#!/usr/bin/env python3
"""루프백 TCP 서버와 DM 편집기를 한 Qt 이벤트 루프에서 실행한다."""
import re
import signal
import struct
from time import monotonic_ns

import rclpy
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from PyQt5 import QtCore, QtGui, QtNetwork, QtWidgets as W

from steer_agv_controller_py.dm_labels import LABELS


class Registers:
    def __init__(self):
        self.words = [0] * 128
        self.words[1] = 2  # 초기 운전 모드: 자동, 상태: 정지
        self.auto_life = True
        self.life_at = monotonic_ns()
        self.motion_at = self.life_at
        self.simulate_drive = True
        self.distances = [0.0] * 4  # mm, 내부 적분은 float64로 유지한다.

    def tick(self):
        now = monotonic_ns()
        steps = (now - self.life_at) // 1_000_000
        if self.auto_life:
            self.words[0] = (self.words[0] + steps) & 0xffff
        self.life_at += steps * 1_000_000
        dt = (now - self.motion_at) / 1e9
        self.motion_at = now
        if self.simulate_drive:
            self.drive_step(dt)

    def drive_step(self, dt):
        """직진 시험 모델: 속도 지령을 네 휠의 속도 및 누적거리로 돌려준다."""
        w = self.words
        cmd = w[103]
        blocked = w[1] != 2 or w[4] or w[30] or ((w[50] | w[51]) & 1)
        running = cmd == 1 and not blocked and not w[25] and w[2] not in (8, 16)
        if w[2] not in (8, 16):
            w[2] = 1 if running else cmd if not blocked and cmd in (2, 4, 32) else 0
        w[3] = int(running)
        if w[2] == 2:
            w[25] = 1
        elif w[2] == 32:
            w[25] = 0
        speed = w[106] if w[106] < 32768 else w[106] - 65536
        speed = speed if running else 0
        w[32] = speed & 0xffff
        for i in range(4):
            self.distances[i] += speed * dt
            distance = round(self.distances[i]) & 0xffffffff
            w[5+2*i:7+2*i] = [distance & 0xffff, distance >> 16]
            w[13+2*i:15+2*i] = struct.unpack("<HH", struct.pack("<i", speed))
            w[21+i] = 0

    def set_drive_simulation(self, enabled):
        self.tick()
        if enabled:
            for i in range(4):
                value, = struct.unpack("<i", struct.pack("<HH", *self.words[5+2*i:7+2*i]))
                self.distances[i] = float(value)
        self.simulate_drive = enabled

    def reply(self, request):
        """RD/RDS/WR/WRS의 UINT16 요청을 검증한 뒤 한 번에 반영한다."""
        self.tick()
        fields = request.split()
        if len(fields) < 2:
            return b"E0"
        op, dm, *args = fields
        match = re.fullmatch(rb"DM([0-9]{1,3})(?:\.U)?", dm)
        if not match or op not in (b"RD", b"RDS", b"WR", b"WRS"):
            return b"E0"
        start = int(match[1])
        if any(not arg.isdigit() or len(arg) > 5 for arg in args):
            return b"E0"
        if op in (b"RDS", b"WRS"):
            if not args:
                return b"E0"
            count = int(args.pop(0))
        else:
            count = 1
        if count < 1 or start + count > len(self.words):
            return b"E1"
        if op in (b"RD", b"RDS"):
            if args:
                return b"E0"
            return b" ".join(b"%05d" % w for w in self.words[start:start+count])
        values = list(map(int, args))
        if len(values) != count or any(w > 65535 for w in values):
            return b"E1"
        if start <= 103 < start + count and values[103-start] not in (0, 1, 2, 4, 16, 32):
            return b"E1"
        self.words[start:start+count] = values
        return b"OK"


class TestPlcPanel(W.QWidget):
    def __init__(self, node, port):
        super().__init__()
        self.node = node
        self.bank = Registers()
        self.server = QtNetwork.QTcpServer(self)
        self.server.newConnection.connect(self.accept)
        if not self.server.listen(QtNetwork.QHostAddress.LocalHost, port):
            raise RuntimeError(f"127.0.0.1:{port} · {self.server.errorString()}")
        self.clients = {}
        self.last_request = "대기"
        self.setWindowTitle("Steer AGV · 테스트 PLC")
        layout = W.QVBoxLayout(self)
        self.status = W.QLabel()
        layout.addWidget(self.status)
        life = W.QCheckBox("DM000 LIFE 자동 증가 (1 ms)")
        life.setChecked(True)
        life.toggled.connect(self.toggle_life)
        layout.addWidget(life)
        motion = W.QCheckBox("자동 주행 모의 · RUN/속도 지령으로 상태와 직진 엔코더 갱신")
        motion.setChecked(True)
        motion.toggled.connect(self.toggle_motion)
        layout.addWidget(motion)
        layout.addWidget(W.QLabel("입력 후 Enter로 적용 · UINT16 원시 워드 · DINT는 L/H 두 주소 사용"))
        self.tabs = W.QTabWidget()
        layout.addWidget(self.tabs)
        self.items = []
        self.editors = []
        for title, addresses in (("피드백 DM000–049", range(50)),
                                 ("피드백 DM050–099", range(50, 100)),
                                 ("명령 DM100–127", range(100, 128))):
            table = W.QTableWidget(len(addresses), 4)
            table.setHorizontalHeaderLabels(["주소", "신호 (PLC 단위)", "현재 워드", "입력 (Enter)"])
            table.horizontalHeader().setSectionResizeMode(1, W.QHeaderView.Stretch)
            table.verticalHeader().hide()
            table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
            for row, addr in enumerate(addresses):
                table.setItem(row, 0, W.QTableWidgetItem(f"DM{addr:03}"))
                table.setItem(row, 1, W.QTableWidgetItem(LABELS.get(addr, "예약")))
                item = W.QTableWidgetItem()
                table.setItem(row, 2, item)
                self.items.append(item)
                editor = W.QLineEdit()
                editor.setValidator(QtGui.QIntValidator(0, 65535, editor))
                editor.returnPressed.connect(lambda a=addr, e=editor: self.apply_word(a, e))
                table.setCellWidget(row, 3, editor)
                self.editors.append(editor)
            self.tabs.addTab(table, title)
        self.editors[0].setEnabled(False)
        self.update_motion_editors(True)
        self.resize(800, 600)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)
        self.refresh()

    def toggle_life(self, enabled):
        self.bank.tick()
        self.bank.auto_life = enabled
        self.editors[0].setEnabled(not enabled)

    def update_motion_editors(self, enabled):
        for addr in (2, 3, *range(5, 25)):
            self.editors[addr].setEnabled(not enabled)

    def toggle_motion(self, enabled):
        self.bank.set_drive_simulation(enabled)
        self.update_motion_editors(enabled)

    def apply_word(self, address, editor):
        if editor.hasAcceptableInput():
            if address == 103 and int(editor.text()) not in (0, 1, 2, 4, 16, 32):
                W.QMessageBox.warning(self, "잘못된 명령", "DM103 허용값: 0, 1, 2, 4, 16, 32")
                return
            self.bank.words[address] = int(editor.text())
            editor.clear()
            self.refresh()

    def accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self.clients[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self.receive(s))
            sock.disconnected.connect(lambda s=sock: self.disconnect_client(s))
            if sock.bytesAvailable():
                self.receive(sock)

    def disconnect_client(self, sock):
        self.clients.pop(sock, None)
        if not self.clients and self.bank.simulate_drive:
            self.bank.tick()
            self.bank.words[103] = self.bank.words[106] = 0
            self.bank.drive_step(0.0)
        sock.deleteLater()

    def receive(self, sock):
        # TCP 분할 수신 및 한 번에 들어오는 여러 CR 종료 요청을 모두 처리한다.
        buf = self.clients[sock]
        buf.extend(bytes(sock.readAll()))
        while b"\r" in buf:
            end = buf.index(13)
            if end > 2048:
                sock.abort()
                return
            frame = bytes(buf[:end])
            del buf[:end+1]
            result = self.bank.reply(frame)
            sock.write(result + b"\r\n")
            self.last_request = frame.decode("ascii", "replace")[:80]
        if len(buf) > 2048:
            sock.abort()

    def refresh(self):
        if not rclpy.ok():
            self.close()
            return
        rclpy.spin_once(self.node, timeout_sec=0)
        self.bank.tick()
        for item, word in zip(self.items, self.bank.words):
            if item.text() != str(word):
                item.setText(str(word))
        self.status.setText(f"127.0.0.1:{self.server.serverPort()}   |   연결 {len(self.clients)}개"
                            f"   |   최근 요청 {self.last_request}")

    def closeEvent(self, event):
        self.timer.stop()
        self.server.close()
        for sock in list(self.clients):
            sock.abort()
        event.accept()


def main():
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = Node("steer_agv_test_plc")
    try:
        port = node.declare_parameter("port", 8501).value
        if not 1 <= port <= 65535:
            raise ValueError("port must be in 1..65535")
        app = W.QApplication([])
        panel = TestPlcPanel(node, port)
        signal.signal(signal.SIGINT, lambda *_: panel.close())
        signal.signal(signal.SIGTERM, lambda *_: panel.close())
        panel.show()
        app.exec_()
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
