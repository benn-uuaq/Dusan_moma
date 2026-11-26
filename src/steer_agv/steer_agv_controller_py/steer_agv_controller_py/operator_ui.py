#!/usr/bin/env python3
"""사용자가 연결하고 버튼을 누를 때만 기존 컨트롤러에 요청하는 조작 패널."""

from time import monotonic
from math import isfinite

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from PyQt5 import QtCore, QtWidgets as W
from vehicle_interfaces.msg import RobotStatus
from vehicle_interfaces.srv import ManualCommand, RobotControl, SetJob, SetMvLift
from .dm_labels import LABELS
from .input_keypad import InputKeypad
from rcl_interfaces.msg import ParameterDescriptor
from .status_publisher import JOB_QOS


DIAG_FIELDS = {
    "connection_generation": "TCP 연결 성공 횟수", "life": "PLC LIFE",
    "mode": "운전 모드 (DM001)", "state": "PLC 상태 (DM002)",
    "error_code": "PLC 오류 코드", "estop": "비상정지",
    "outrig": "아웃트리거 고정", "auto_ready": "자동 운전 준비",
    "feedback_speed_mmps": "주행 속도 피드백 (DM032, mm/s)",
    "obstacle_stop": "장애물 정지", "obstacle_slow": "장애물 감속",
    "battery_soc_percent": "배터리 잔량 (%)", "charging": "충전 중",
}
STATUS_FIELDS = {"lift_h": "리프트 높이 (m)", "sen1_dist": "센서 1 거리 (m)",
                 "sen2_dist": "센서 2 거리 (m)"}


class Panel(W.QWidget):
    def __init__(self, debug=None, job_feedback_topic="/vehicle/last_job_request"):
        super().__init__()
        self.job_feedback_topic = job_feedback_topic
        self.debug = debug
        self.setWindowTitle("Steer AGV · 조작 패널")
        layout = W.QVBoxLayout(self)
        self.node = None
        self.clients = {}
        self.pending = {}
        self.toggle = W.QPushButton("UI 연결 ON")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.connect_ui)
        layout.addWidget(self.toggle)
        layout.addWidget(W.QLabel("연결 OFF는 화면 연결만 종료합니다. 주행 정지는 STOP을 사용하세요."))
        self.vehicle = W.QLabel("차량 상태 —")
        self.plc = W.QLabel("PLC 진단 —")
        layout.addWidget(self.vehicle)
        layout.addWidget(self.plc)
        tabs = W.QTabWidget()
        layout.addWidget(tabs)
        controls = W.QWidget()
        tabs.addTab(controls, "조작")
        layout = W.QVBoxLayout(controls)
        feedback = W.QWidget()
        tabs.addTab(feedback, "PLC 수신 데이터")
        self.dm_tables = W.QTabWidget()
        tabs.addTab(self.dm_tables, "PLC 전체 DM")
        self.dm_cells = {}
        for title, start, stop in (("PLC → PC · 수신", 0, 100), ("PC → PLC · 송신", 100, 128)):
            table = W.QTableWidget(stop-start, 3 if start == 0 else 4)
            headers = ["주소", "신호 (PLC 단위)", "원시 워드"]
            if start:
                headers.append("PLC 쓰기 응답")
            table.setHorizontalHeaderLabels(headers)
            table.horizontalHeader().setSectionResizeMode(1, W.QHeaderView.Stretch)
            table.verticalHeader().hide()
            table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
            for row, addr in enumerate(range(start, stop)):
                table.setItem(row, 0, W.QTableWidgetItem(f"DM{addr:03}"))
                table.setItem(row, 1, W.QTableWidgetItem(LABELS.get(addr, "예약")))
                for col, key in enumerate((["RX"] if start == 0 else ["TX", "ACK"]), start=2):
                    cell = W.QTableWidgetItem("—")
                    table.setItem(row, col, cell)
                    self.dm_cells[f"{key}/{addr}"] = cell
            self.dm_tables.addTab(table, title)
        if debug is not None:
            self.params_view = W.QPlainTextEdit()
            self.params_view.setReadOnly(True)
            tabs.addTab(self.params_view, "컨트롤러 인자")
            self.debug_views = {}
            for key, title in (("rcs", "RCS 토픽·서비스 로그"), ("plc", "PLC 송수신 로그"), ("drive", "주행 오류 로그")):
                view = W.QPlainTextEdit()
                view.setReadOnly(True)
                view.setMaximumBlockCount(2000)
                view.setLineWrapMode(W.QPlainTextEdit.NoWrap)
                tabs.addTab(view, title)
                self.debug_views[key] = view
            self.previous_params = None
        form = W.QGridLayout(feedback)
        self.readouts = {}
        for index, (key, title) in enumerate({**DIAG_FIELDS, **STATUS_FIELDS}.items()):
            row, column = divmod(index, 2)
            field = W.QLineEdit()
            field.setReadOnly(True)
            field.setPlaceholderText("—")
            form.addWidget(W.QLabel(title), row, 2 * column)
            form.addWidget(field, row, 2 * column + 1)
            self.readouts[key] = field
        form.setRowStretch(form.rowCount(), 1)
        self.feedback_available = False
        self.commands = W.QGroupBox("컨트롤러 명령")
        layout.addWidget(self.commands)
        grid = W.QGridLayout(self.commands)
        self.commands.setEnabled(False)
        self.keypad = None
        self.inputs = {}
        self.job_fields = {}
        grid.addWidget(W.QLabel("직접 입력"), 0, 1)
        self.receipt = W.QLabel("최근 작업 요청 · 수신 대기")
        grid.addWidget(self.receipt, 0, 2, 1, 2)
        for row, (key, label, value) in enumerate((
                ("job_id", "작업 ID", ""), ("set_dist", "목표 거리 (m, 후진은 음수)", ""),
                ("set_speed", "주행 속도 (m/s)", "0.1"), ("radius", "기준 반경 (m)", ""),
                ("standoff", "이격 거리 (m)", "0"), ("set_height", "리프트 목표 높이 (m)", ""),
                ("lift_speed", "리프트 속도 (m/s)", "0.05"))):
            grid.addWidget(W.QLabel(label), row+1, 0)
            self.inputs[key] = W.QLineEdit(value)
            self.inputs[key].setAccessibleName(label)
            self.inputs[key].installEventFilter(self)
            grid.addWidget(self.inputs[key], row+1, 1)
            field = W.QLineEdit()
            field.setReadOnly(True)
            field.setPlaceholderText("—")
            grid.addWidget(field, row+1, 2, 1, 2)
            self.job_fields[key] = field
        actions = [
            ("작업 등록", SetJob, {}), ("RUN", RobotControl, {"state": "RUNNING"}),
            ("PAUSE", RobotControl, {"state": "PAUSE"}), ("STOP", RobotControl, {"state": "STOP"}),
            ("작업 취소", RobotControl, {"job_cancel": 1}), ("RESET", RobotControl, {"reset": 1}),
            ("FINISH", RobotControl, {"state": "FINISH"}),
            ("리프트 이동", SetMvLift, {}),
        ]
        # 수동 버튼은 클릭당 한 번 전송한다. 유효 시간과 인터록은 컨트롤러가 처리한다.
        for label, field in (("전진", "cmd_mv_fwd"), ("후진", "cmd_mv_rear"),
                             ("좌회전", "cmd_trn_left"), ("우회전", "cmd_trn_right")):
            actions.append((label, ManualCommand, {field: True}))
        actions.extend([("아웃트리거 고정", ManualCommand, {"cmd_outrg_set": 2}),
                        ("아웃트리거 해제", ManualCommand, {"cmd_outrg_set": 1})])
        for i, (label, kind, fields) in enumerate(actions):
            btn = W.QPushButton(label)
            btn.clicked.connect(lambda _, k=kind, f=fields, l=label: self.send(l, k, f))
            grid.addWidget(btn, 8+i//4, i%4)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(100)
        layout.addWidget(self.log)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(20)
        if debug is not None:
            self.toggle.setChecked(True)

    def eventFilter(self, field, event):
        if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
            if self.keypad is None or self.keypad.field is not field:
                if self.keypad is not None:
                    self.keypad.reject()
                self.keypad = InputKeypad(field, field is self.inputs["job_id"], self)
                self.keypad.finished.connect(self.keypad_closed)
            # 비모달로 열어 키패드가 떠 있어도 STOP 등 조작 버튼을 사용할 수 있다.
            self.keypad.show()
            self.keypad.raise_()
            self.keypad.activateWindow()
        return super().eventFilter(field, event)

    def keypad_closed(self, result):
        self.keypad = None

    def connect_ui(self, enabled):
        if enabled:
            self.node = rclpy.create_node("steer_agv_operator_ui", use_global_arguments=self.debug is None)
            self.last_vehicle = self.last_plc = 0.0
            self.last_dm = 0.0
            topic = self.node.declare_parameter("job_feedback_topic", self.job_feedback_topic,
                                                ParameterDescriptor(read_only=True)).value
            self.node.create_subscription(DiagnosticStatus, topic, self.received_job, JOB_QOS)
            self.node.create_subscription(DiagnosticArray, "/vehicle/plc_dm", self.dm_feedback, 1)
            self.node.create_subscription(RobotStatus, "/vehicle/robot_status", self.status, 1)
            self.node.create_subscription(DiagnosticArray, "/diagnostics", self.diagnostic, 10)
            for kind, name in ((SetJob, "set_job"), (SetMvLift, "lift_control"),
                               (ManualCommand, "manual_command"), (RobotControl, "robot_control")):
                self.clients[kind] = self.node.create_client(kind, "/vehicle/"+name)
        elif self.node:
            if self.keypad is not None:
                self.keypad.reject()
            # UI가 소유한 노드만 해제한다. 이미 접수된 작업은 컨트롤러가 관리한다.
            if self.pending:
                self.log.appendPlainText("연결 해제 · 대기 중인 명령의 결과 미확인")
            self.node.destroy_node()
            self.node = None
            self.clients.clear()
            self.pending.clear()
            self.vehicle.setText("차량 상태 —")
            self.plc.setText("PLC 진단 —")
            self.feedback_available = False
            self.clear_fields(self.readouts)
            self.show_words({})
            self.receipt.setText("최근 작업 요청 · 수신 대기")
            for field in self.job_fields.values():
                field.clear()
        self.toggle.setText("UI 연결 OFF" if enabled else "UI 연결 ON")
        self.commands.setEnabled(enabled)

    def status(self, msg):
        self.last_vehicle = monotonic()
        # 컨트롤러는 유효한 PLC 값으로 상태를 채운 경우에만 hold를 설정한다.
        for key in STATUS_FIELDS:
            value = (f"{getattr(msg, key):.3f}" if self.feedback_available
                     and msg.hold in ("SET", "RELEASE") and msg.error_code != 701 else "")
            self.readouts[key].setText(value)
        self.vehicle.setText(f"차량 {msg.state}   |   오류 {msg.error_code} {msg.error_msg}\n"
                             f"작업 {msg.job_id}   목표 {msg.set_dist:.3f} m   이동 {msg.mv_dist:.3f} m   속도 {msg.speed:.3f} mm/s")

    def received_job(self, report):
        """실제 서비스에 도착한 요청값과 결과를 함께 표시한다."""
        values = {entry.key: entry.value for entry in report.values}
        lift = report.name == "/vehicle/lift_control"
        if lift:
            values["lift_speed"] = values.pop("set_speed", "")
        for key, field in self.job_fields.items():
            field.setText(values.get(key, ""))
        verdict = "수락" if report.level == DiagnosticStatus.OK else "거부"
        self.receipt.setText(f"최근 {'리프트' if lift else '주행'} 요청 · {verdict}")
        self.receipt.setToolTip(report.message)
        self.log.appendPlainText(f"{report.name} · {values.get('job_id', '')} · {verdict} · {report.message}")

    def diagnostic(self, msg):
        for item in msg.status:
            if item.hardware_id == "steer_agv_plc":
                self.last_plc = monotonic()
                self.plc.setText("PLC · " + item.message)
                values = {entry.key: entry.value for entry in item.values}
                self.feedback_available = "life" in values
                for key in DIAG_FIELDS:
                    self.readouts[key].setText(values.get(key, ""))
                if not self.feedback_available:
                    self.clear_fields(STATUS_FIELDS)

    def clear_fields(self, keys):
        """오래된 값은 지우고, 미수신과 실제 측정값 0을 구분한다."""
        for key in keys:
            self.readouts[key].clear()

    def dm_feedback(self, msg):
        for status in msg.status:
            if status.hardware_id == "steer_agv_plc":
                self.last_dm = monotonic()
                self.show_words({entry.key: entry.value for entry in status.values})

    def show_words(self, values):
        for key, cell in self.dm_cells.items():
            value = values.get(key, "—")
            if cell.text() != value:
                cell.setText(value)

    def send(self, label, kind, fields):
        client = self.clients[kind]
        if not client.service_is_ready():
            self.log.appendPlainText(label + " · 컨트롤러 서비스 연결 대기")
            return
        try:
            values = dict(fields)
            if kind in (SetJob, SetMvLift):
                values["job_id"] = self.inputs["job_id"].text().strip()
                if not values["job_id"]:
                    raise ValueError("작업 ID를 입력하세요.")
                keys = ("set_dist", "set_speed", "radius", "standoff") if kind is SetJob else ("set_height", "lift_speed")
                for key in keys:
                    value = float(self.inputs[key].text())
                    if not isfinite(value):
                        raise ValueError("유한한 수를 입력하세요.")
                    values["set_speed" if key == "lift_speed" else key] = value
            future = client.call_async(kind.Request(**values))
            self.pending[future] = (label, monotonic(), client)
            self.log.appendPlainText(label + " · 요청 전송")
        except (ValueError, TypeError, RuntimeError) as exc:
            self.log.appendPlainText(f"{label} · {exc}")

    def poll(self):
        if not rclpy.ok():
            self.close()
            return
        if self.node is None:
            return
        # Qt 이벤트 루프를 막지 않고 구독과 서비스 응답을 처리한다.
        if self.debug is not None:
            params, lines = self.debug.take()
            if params != self.previous_params:
                self.previous_params = params
                self.params_view.setPlainText("\n".join(f"{k} = {v}" for k, v in sorted(params.items())))
            for key, batch in lines.items():
                if batch:
                    self.debug_views[key].appendPlainText("\n".join(batch))
        rclpy.spin_once(self.node, timeout_sec=0)
        now = monotonic()
        if self.last_dm and now-self.last_dm > 1:
            self.show_words({})
            self.last_dm = 0.0
        if now-self.last_vehicle > 1:
            self.vehicle.setText("차량 상태 수신 대기")
            self.clear_fields(STATUS_FIELDS)
        if now-self.last_plc > 1:
            self.plc.setText("PLC 진단 수신 대기")
            self.feedback_available = False
            self.clear_fields(self.readouts)
        for future, (label, sent, client) in list(self.pending.items()):
            if future.done():
                try:
                    response = future.result()
                    result = ("수락" if response.success else "거부") + " · " + response.message
                except Exception as exc:
                    result = str(exc)
            elif now-sent > 5:
                client.remove_pending_request(future)
                result = "응답 시간 초과 · 실행 여부 미확인"
            else:
                continue
            self.log.appendPlainText(f"{label} · {result}")
            del self.pending[future]

    def closeEvent(self, event):
        self.timer.stop()
        self.connect_ui(False)
        event.accept()


def main():
    rclpy.init()
    try:
        app = W.QApplication([])
        panel = Panel()
        panel.show()
        app.exec_()
    finally:
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
