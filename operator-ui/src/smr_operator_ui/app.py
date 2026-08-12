"""애플리케이션 초기화, 최상위 화면 전환, 서비스 연결을 담당한다."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from importlib.resources import files
from math import isfinite
from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from smr_operator_ui.components import ConnectionBadge
from smr_operator_ui.screens import (
    CobotJogScreen, CobotManualScreen, CobotSettingsScreen, ConnectionSettingsScreen,
    ErrorLogScreen, IOStatusScreen, LogFilesScreen, MainScreen, ManualScreen,
    ModeSlotsScreen, RunScreen, SettingsMenuScreen, SystemSettingsScreen,
    UTSettingsScreen,
)
from smr_operator_ui.services import (
    InspectionSimulator,
    MqttServer,
    MqttTopics,
    RosStatusClient,
    SettingsService,
)
from smr_operator_ui.services.reference_poses import (
    load_reference_poses,
    save_reference_poses,
)
from smr_operator_ui.styles import load_stylesheet

# 저장 문구에 쓰는 축 이름. 홈은 관절, 시작 포즈는 TCP 좌표다.
_JOINT_LABELS = ("J1", "J2", "J3", "J4", "J5", "J6")
_POSE_LABELS = ("X", "Y", "Z", "RX", "RY", "RZ")


class TopBar(QFrame):
    """제품 정보와 시스템 요약 상태를 항상 표시하는 상단 바."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(68)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 6, 20, 6)
        layout.setSpacing(14)
        brand = QLabel("3S-Robotics")
        brand.setObjectName("Brand")
        product = QLabel("SMR 비파괴 검사 시스템  |  Operator Console")
        product.setObjectName("Product")
        layout.addWidget(brand)
        layout.addWidget(product)
        layout.addStretch()
        self.clock = QLabel()
        self.clock.setObjectName("TopMeta")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.clock)
        for name in ("PLC", "AMR", "Cobot", "UT"):
            layout.addWidget(ConnectionBadge(name))
        battery = QLabel("배터리  85%")
        battery.setObjectName("Product")
        layout.addWidget(battery)
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._update_clock()

    def _update_clock(self) -> None:
        """운영자에게 표시되는 현재 날짜와 시각을 갱신한다."""
        now = datetime.now()
        self.clock.setText(now.strftime("날짜  %Y-%m-%d (%a)\n시간  %H:%M:%S"))


class OperatorWindow(QMainWindow):
    """화면 스택을 소유하고 UI·시뮬레이터·설정 서비스를 조정한다."""

    def __init__(
        self,
        mqtt_server: MqttServer | None = None,
        *,
        start_mqtt: bool = True,
        start_ros: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("3S-Robotics | SMR Operator Console")
        self.resize(1280, 720)
        self.setMinimumSize(1280, 720)
        root = QWidget()
        root.setObjectName("AppRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(TopBar())
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.simulator = InspectionSimulator(self)
        self.settings_service = SettingsService(self)
        self.mqtt_server = mqtt_server or MqttServer(parent=self)
        self.main_screen = MainScreen()
        # 화면 키를 탐색 시그널에도 사용하여, 화면 전환 로직이 구체적인
        # QWidget 인스턴스에 직접 의존하지 않게 한다.
        self.cobot_manual_screen = CobotManualScreen()
        self.cobot_jog_screen = CobotJogScreen()
        self.screens = {
            "main": self.main_screen,
            "manual": ManualScreen(), "run": RunScreen(),
            "settings": SettingsMenuScreen(), "io": IOStatusScreen(),
            "connection": ConnectionSettingsScreen(),
            "system": SystemSettingsScreen(), "ut": UTSettingsScreen(),
            "cobot": CobotSettingsScreen(), "errors": ErrorLogScreen(),
            "logs": LogFilesScreen(), "modes": ModeSlotsScreen(),
            "cobot_manual": self.cobot_manual_screen,
            "cobot_jog": self.cobot_jog_screen,
        }
        self._current_screen_key = "main"
        self._navigation_history: list[str] = []
        for screen in self.screens.values():
            self.stack.addWidget(screen)
            if hasattr(screen, "navigate"):
                screen.navigate.connect(self.navigate)
            if hasattr(screen, "back_requested"):
                screen.back_requested.connect(self.navigate_back)

        self.main_screen.start_requested.connect(self.simulator.start_cycle)
        self.main_screen.pause_requested.connect(self.simulator.toggle_pause)
        self.main_screen.manual_requested.connect(lambda: self.navigate("manual"))
        self.main_screen.settings_requested.connect(lambda: self.navigate("settings"))
        self.main_screen.target_dimensions_changed.connect(
            lambda diameter, height: self.settings_service.save(
                "inspection_target", {"diameter_m": diameter, "height_m": height}
            )
        )
        self.main_screen.work_area_changed.connect(
            lambda width, height, scan_h, overlap: self.settings_service.save(
                "work_area", {"width_mm": width, "height_mm": height,
                              "scan_h_mm": scan_h, "overlap_mm": overlap}
            )
        )
        self.simulator.snapshot_changed.connect(self.main_screen.update_snapshot)
        self.simulator.activity.connect(self.main_screen.show_activity)
        self.main_screen.update_snapshot(self.simulator.snapshot)

        # FormScreen 기반 화면만 settings_scope를 제공한다. 이 조회표를 한 번
        # 구성해 두면 서비스 콜백이 결과를 전달할 화면을 빠르게 찾을 수 있다.
        self._settings_screens = {
            screen.settings_scope: screen
            for screen in self.screens.values()
            if hasattr(screen, "settings_scope")
        }
        for screen in self._settings_screens.values():
            screen.save_requested.connect(self.settings_service.save)
        self.settings_service.loaded.connect(self._apply_stored_settings)
        self.settings_service.saved.connect(self._mark_settings_saved)
        self.settings_service.failed.connect(self._show_settings_error)
        for scope in (*self._settings_screens.keys(), "inspection_target", "work_area"):
            self.settings_service.load(scope)

        # 기준 위치의 원본은 로봇 쪽 설정 파일이다. 레지스터는 휘발성이라
        # 연결될 때마다 이 값을 다시 올린다.
        self._reference_poses: dict[str, dict] = {}
        self._load_reference_poses()

        # Paho 네트워크 스레드에서 수신한 명령은 Qt 시그널을 통해 GUI
        # 스레드의 이 처리기로 전달된다.
        # 로봇 자세는 robot_control_node가 Modbus에서 읽어 발행한다. 화면은
        # ROS 실행기 스레드가 아닌 GUI 스레드에서 시그널로 값을 받는다.
        self.ros_status = RosStatusClient(parent=self)
        self.ros_status.tcp_pose_changed.connect(self._show_tcp_pose)
        self.ros_status.tcp_pose_zero_changed.connect(self._show_tcp_pose_zero)
        self.ros_status.robot_mode_changed.connect(
            lambda _code, name: self.cobot_manual_screen.apply_status({"robot_mode": name})
        )
        self.ros_status.control_method_changed.connect(
            lambda _code, name: self.cobot_manual_screen.apply_status({"control_method": name})
        )
        self.ros_status.operation_mode_changed.connect(
            lambda _code, name: self.cobot_manual_screen.apply_status({"operation_mode": name})
        )
        self.ros_status.alarm_received.connect(self.cobot_manual_screen.add_alarm)
        self.ros_status.joint_position_changed.connect(self._remember_joint)
        self.ros_status.command_result.connect(self._show_command_result)
        self.ros_status.connected_changed.connect(self.cobot_manual_screen.set_connected)
        self.ros_status.connected_changed.connect(self._restore_robot_settings)
        self.cobot_jog_screen.jog_pressed.connect(self._send_jog)
        self.cobot_jog_screen.jog_released.connect(self._stop_jog)
        self.cobot_jog_screen.command_requested.connect(self._save_reference_pose)
        self.cobot_manual_screen.command_requested.connect(self._handle_cobot_command)
        self.screens["cobot"].save_requested.connect(self._send_linear_speed)
        self.cobot_jog_screen.set_enabled_commands(set(self._available_writes()))
        self.ros_status.error_occurred.connect(self._show_ros_error)
        if start_ros:
            self.ros_status.start()

        self.mqtt_server.command_received.connect(self._handle_mqtt_command)
        self.mqtt_server.connected_changed.connect(
            self._show_mqtt_connection_state
        )
        self.mqtt_server.error_occurred.connect(self._show_mqtt_error)
        if start_mqtt:
            self.mqtt_server.start()

    def _apply_stored_settings(self, scope: str, values: dict) -> None:
        """DB 조회 결과를 해당 설정 범위의 소유 화면으로 전달한다."""
        if scope == "inspection_target":
            if "diameter_m" in values and "height_m" in values:
                self.main_screen.orbit_view.set_target_dimensions(
                    float(values["diameter_m"]), float(values["height_m"])
                )
            return
        if scope == "work_area":
            keys = ("width_mm", "height_mm", "scan_h_mm", "overlap_mm")
            if all(k in values for k in keys):
                self.main_screen.set_work_area(*(float(values[k]) for k in keys))
            return
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.apply_values(values)
            if scope == "connection":
                self._sync_cobot_endpoint()

    def _sync_cobot_endpoint(self) -> None:
        """연결 설정의 협동로봇 주소를 Cobot 수동 제어 화면에 반영한다."""
        values = self.screens["connection"].values()
        ip = str(values.get("협동로봇 IP", "")).strip()
        if ip:
            self.cobot_manual_screen.set_endpoint(ip)

    def _mark_settings_saved(self, scope: str) -> None:
        """PostgreSQL 저장 완료 후 해당 화면의 상태를 갱신한다."""
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.mark_saved()
            if scope == "connection":
                self._sync_cobot_endpoint()
        elif scope == "inspection_target":
            self.main_screen.show_activity("검사 대상 크기를 PostgreSQL에 저장했습니다.")

    def _show_settings_error(self, scope: str, message: str) -> None:
        """Python 스택 추적을 노출하지 않고 저장소 오류를 표시한다."""
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.show_storage_error(message)
        elif scope == "inspection_target":
            self.main_screen.show_activity(f"설정 저장소 오류: {message}")

    def _handle_mqtt_command(
        self,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        """수신 MQTT 명령을 검사대상 설정과 검사 사이클에 반영한다."""
        if topic == MqttTopics.JOB_COMMAND:
            self._apply_mqtt_job_info(payload)
            return

        if topic != MqttTopics.MC_COMMAND:
            return

        amr_command = payload.get("amr")
        if amr_command == "run":
            self.simulator.start_cycle()
        elif amr_command in {"stop", "ems"}:
            # MQTT 명령은 재전송될 수 있으므로 토글이 아닌 멱등적인
            # 일시정지 API를 사용한다.
            self.simulator.pause_cycle()

    def _apply_mqtt_job_info(self, payload: dict[str, Any]) -> None:
        """Job 치수를 mm에서 m로 변환해 화면과 저장소에 반영한다."""
        try:
            job_info = payload["job_info"]
            diameter_mm = float(str(job_info["diameter"]).strip())
            height_mm = float(str(job_info["height"]).strip())
            thickness_mm = float(str(job_info["thickness"]).strip())
            target_distance_mm = float(
                str(job_info["target_distance"]).strip()
            )
            values_mm = (
                diameter_mm,
                height_mm,
                thickness_mm,
                target_distance_mm,
            )
            if any(not isfinite(value) or value <= 0 for value in values_mm):
                raise ValueError("검사대상 치수는 0보다 큰 유한한 값이어야 합니다.")
        except (KeyError, TypeError, ValueError) as exc:
            self.main_screen.show_activity(
                f"MQTT Job 정보 적용 실패: {exc}"
            )
            return

        diameter_m = diameter_mm / 1000.0
        height_m = height_mm / 1000.0
        self.main_screen.set_target_dimensions(diameter_m, height_m)
        self.settings_service.save(
            "inspection_target",
            {
                "job_id": str(payload.get("job_id", "")),
                "diameter_m": diameter_m,
                "height_m": height_m,
                "thickness_m": thickness_mm / 1000.0,
                "target_distance_m": target_distance_mm / 1000.0,
            },
        )
        self.main_screen.show_activity(
            "MQTT Job 정보를 검사대상 설정에 적용했습니다. "
            f"(지름 {diameter_m:.2f} m, 높이 {height_m:.2f} m)"
        )

    def _show_mqtt_connection_state(self, connected: bool) -> None:
        """MQTT Broker 연결 상태를 메인 화면 활동 문구로 표시한다."""
        message = (
            "MQTT Broker에 연결되었습니다."
            if connected
            else "MQTT Broker 연결이 종료되었습니다."
        )
        self.main_screen.show_activity(message)

    def _remember_joint(self, values: list) -> None:
        """조그 화면 표시와 기준 위치 저장에 쓸 관절값을 기억한다."""
        self._last_joint = list(values)
        self.cobot_jog_screen.apply_joint_position(values)

    def _show_tcp_pose(self, values: list) -> None:
        """현재 TCP 자세를 수동 제어와 조그 화면에 함께 표시한다."""
        self._last_tcp_pose = list(values)
        formatted = self._format_pose(values)
        self.cobot_manual_screen.apply_tcp(formatted)
        self.cobot_jog_screen.apply_position(formatted)

    def _show_tcp_pose_zero(self, values: list) -> None:
        """원점 기준 상대 자세를 수동 제어 화면과 사각형 작업 모델에 표시한다."""
        self.cobot_manual_screen.apply_zero_point(self._format_pose(values))
        if len(values) >= 3:
            # 로봇 태스크(dus_init.script)가 베이스 좌표계로 cur-zero를 낸다.
            # 가로 = -Y(오른쪽 +), 세로 = +Z(위 +). register_map.txt 4항 참고.
            self.main_screen.apply_wall_position(-values[1], values[2])

    @staticmethod
    def _format_pose(values: list) -> dict[str, str]:
        """[X, Y, Z, Rx, Ry, Rz] 배열을 화면이 쓰는 성분별 문자열로 바꾼다.

        단위는 항목명에 이미 표시되므로 값에는 붙이지 않는다.
        """
        axes = ("x", "y", "z", "rx", "ry", "rz")
        return {axis: f"{value:.1f}" for axis, value in zip(axes, values)}

    # 저장 버튼과 실제 저장 항목, 그리고 표시에 쓸 축 이름.
    # 홈은 movej로 가므로 관절값을, 시작 포즈는 TCP 좌표를 쓴다.
    POSE_TARGETS = {
        "save_home_pose": ("home_joint", _JOINT_LABELS),
        "save_start_pose": ("start_pose", _POSE_LABELS),
    }

    def _save_reference_pose(self, command: str) -> None:
        """현재 자세를 기준 위치로 기록하고 로봇 레지스터에도 쓴다."""
        target, labels = self.POSE_TARGETS.get(command, (None, ()))
        if target is None:
            return
        values = (
            getattr(self, "_last_joint", None) if target == "home_joint"
            else getattr(self, "_last_tcp_pose", None)
        )
        if not values:
            self.cobot_jog_screen.show_result("로봇에서 현재 값을 아직 받지 못했습니다.")
            return

        entry = {"values": [float(v) for v in values],
                 "saved_at": datetime.now().isoformat(timespec="seconds")}
        self._reference_poses[target] = entry
        self._show_saved_pose(command, entry)
        if save_reference_poses({target: entry["values"]}) is None:
            self.cobot_jog_screen.show_result("기준 위치 파일을 저장하지 못했습니다.")
        self.ros_status.send_pose(target, values)

    def _show_saved_pose(self, command: str, entry: dict) -> None:
        """저장된 값을 축 이름과 함께 보여준다."""
        _target, labels = self.POSE_TARGETS[command]
        values = entry.get("values") or []
        text = "  ".join(f"{name} {value:.1f}" for name, value in zip(labels, values))
        stamp = str(entry.get("saved_at", "")).replace("T", " ")[:16]
        self.cobot_jog_screen.set_saved_pose(command, f"{text}\n{stamp}" if text else "")

    def _load_reference_poses(self) -> None:
        """파일에 남아 있는 기준 위치를 읽어 화면에 표시한다."""
        self._reference_poses = load_reference_poses()
        for command, (target, _labels) in self.POSE_TARGETS.items():
            entry = self._reference_poses.get(target)
            if isinstance(entry, dict) and entry.get("values"):
                self._show_saved_pose(command, entry)

    def _restore_robot_settings(self, connected: bool) -> None:
        """로봇에 붙으면 저장해 둔 값을 레지스터에 다시 쓴다.

        레지스터는 전원을 내리면 사라진다. 파일에 남은 기준 위치와 설정
        화면의 속도를 다시 올려야 태스크가 같은 값으로 동작한다.
        """
        if not connected:
            return

        restored = []
        for target, entry in self._reference_poses.items():
            values = entry.get("values") if isinstance(entry, dict) else None
            if values and self.ros_status.send_pose(target, values):
                restored.append(target)

        cobot = self.screens["cobot"]
        for value, name in ((cobot.linear_speed(), "linear_speed"),
                            (cobot.speed_ratio(), "speed_ratio")):
            if self.ros_status.send_value(name, int(value)):
                restored.append(name)

        if restored:
            self.cobot_manual_screen.activity_label.setText(
                f"저장된 설정을 로봇에 다시 적용했습니다: {', '.join(restored)}"
            )

    def _available_writes(self) -> list[str]:
        """주소가 정해져 실제로 보낼 수 있는 명령 이름을 모은다."""
        names = ("jog_joint", "jog_tcp", "save_home_pose", "save_start_pose",
                 "move_home", "linear_speed")
        return [name for name in names if self.ros_status.writable(name)]

    def _send_jog(self, kind: str, axis: int, direction: int) -> None:
        """조그 시작을 알린다. 노드가 30001로 speedj/speedl을 보낸다."""
        self.ros_status.send_jog(kind, axis, direction)

    def _stop_jog(self, kind: str, _axis: int) -> None:
        """버튼에서 손을 떼면 즉시 멈춘다. 노드가 29999 stop을 쓴다."""
        self.ros_status.send_jog(kind, 0, 0)

    def _handle_cobot_command(self, command: str) -> None:
        """수동 제어 화면의 명령을 robot/dashboard/* 서비스로 보낸다."""
        if command in self.ros_status.COMMAND_SERVICES:
            self.ros_status.call_command(command)
        else:
            self.cobot_manual_screen.activity_label.setText(
                f"'{command}'에 연결된 명령이 없습니다."
            )

    def _send_linear_speed(self, scope: str, values: dict) -> None:
        """Cobot 설정을 저장할 때 작업 속도를 로봇에도 반영한다."""
        if scope != "cobot":
            return
        for field, name in (
            (CobotSettingsScreen.SPEED_FIELD, "linear_speed"),
            (CobotSettingsScreen.RATIO_FIELD, "speed_ratio"),
        ):
            value = values.get(field)
            if value is not None and self.ros_status.writable(name):
                self.ros_status.send_value(name, int(value))

    # 위치 저장은 조그 화면에서, 나머지는 수동 제어 화면에서 요청한다.
    _JOG_COMMANDS = ("save_home_pose", "save_start_pose")

    def _show_command_result(self, name: str, success: bool, message: str) -> None:
        """명령 결과를 요청한 화면의 안내 문구로 보여준다."""
        text = message if success else f"실패: {message}"
        if name in self._JOG_COMMANDS:
            self.cobot_jog_screen.show_result(text)
        else:
            self.cobot_manual_screen.activity_label.setText(text)

    def _show_ros_error(self, message: str) -> None:
        """ROS 수신 오류를 메인 화면에 간단한 운영 메시지로 표시한다."""
        self.main_screen.show_activity(f"ROS 오류: {message}")

    def _show_mqtt_error(self, message: str) -> None:
        """MQTT 오류를 메인 화면에 간단한 운영 메시지로 표시한다."""
        self.main_screen.show_activity(f"MQTT 오류: {message}")

    def navigate(self, key: str) -> None:
        """새 화면을 열고 이전 화면 복귀를 위해 현재 화면을 기록한다."""
        screen = self.screens.get(key)
        if screen is None or key == self._current_screen_key:
            return

        if key == "main":
            # 메인 화면은 탐색의 기준점이므로 이전 경로를 남기지 않는다.
            self._navigation_history.clear()
        else:
            self._navigation_history.append(self._current_screen_key)

        self._current_screen_key = key
        self.stack.setCurrentWidget(screen)

    def navigate_back(self) -> None:
        """탐색 기록이 있으면 가장 최근에 방문한 화면으로 돌아간다."""
        if not self._navigation_history:
            return
        key = self._navigation_history.pop()
        screen = self.screens.get(key)
        if screen is not None:
            if key == "main":
                # 이전 버튼으로 메인에 도착한 경우에도 오래된 경로를 제거한다.
                self._navigation_history.clear()
            self._current_screen_key = key
            self.stack.setCurrentWidget(screen)

    def closeEvent(self, event) -> None:  # noqa: N802
        """창 종료 전에 MQTT 네트워크 루프와 ROS 구독을 정리한다."""
        self.mqtt_server.stop()
        self.ros_status.stop()
        super().closeEvent(event)


def create_application(argv: list[str] | None = None) -> QApplication:
    """QApplication을 생성하거나 재사용하고 공통 글꼴과 QSS를 적용한다."""
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("SMR Operator UI")
    app.setStyle("Fusion")
    font_path = files("smr_operator_ui.resources").joinpath("malgun.ttf")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setStyleSheet(load_stylesheet())
    return app


def main() -> int:
    """Qt 이벤트 루프를 실행하고 프로세스 종료 코드를 반환한다."""
    if "--offscreen" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_application()
    window = OperatorWindow()
    window.show()
    return app.exec()
