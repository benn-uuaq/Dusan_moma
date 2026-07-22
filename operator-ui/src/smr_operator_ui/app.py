from __future__ import annotations

import os
import sys
from datetime import datetime
from importlib.resources import files

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
    CobotSettingsScreen, ErrorLogScreen, IOStatusScreen, LogFilesScreen,
    MainScreen, ManualScreen, ModeSlotsScreen, RunScreen, SettingsMenuScreen,
    SystemSettingsScreen, UTSettingsScreen,
)
from smr_operator_ui.services import InspectionSimulator, SettingsService
from smr_operator_ui.styles import load_stylesheet


class TopBar(QFrame):
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
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        now = datetime.now()
        self.clock.setText(now.strftime("날짜  %Y-%m-%d (%a)\n시간  %H:%M:%S  Asia/Seoul"))


class OperatorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("3S-Robotics | SMR Operator Console")
        self.resize(1280, 720)
        self.setMinimumSize(1024, 576)
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
        self.main_screen = MainScreen()
        self.screens = {
            "main": self.main_screen,
            "manual": ManualScreen(), "run": RunScreen(),
            "settings": SettingsMenuScreen(), "io": IOStatusScreen(),
            "system": SystemSettingsScreen(), "ut": UTSettingsScreen(),
            "cobot": CobotSettingsScreen(), "errors": ErrorLogScreen(),
            "logs": LogFilesScreen(), "modes": ModeSlotsScreen(),
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
        self.simulator.snapshot_changed.connect(self.main_screen.update_snapshot)
        self.simulator.activity.connect(self.main_screen.show_activity)
        self.main_screen.update_snapshot(self.simulator.snapshot)

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
        for scope in (*self._settings_screens.keys(), "inspection_target"):
            self.settings_service.load(scope)

    def _apply_stored_settings(self, scope: str, values: dict) -> None:
        if scope == "inspection_target":
            if "diameter_m" in values and "height_m" in values:
                self.main_screen.orbit_view.set_target_dimensions(
                    float(values["diameter_m"]), float(values["height_m"])
                )
            return
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.apply_values(values)

    def _mark_settings_saved(self, scope: str) -> None:
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.mark_saved()
        elif scope == "inspection_target":
            self.main_screen.show_activity("검사 대상 크기를 PostgreSQL에 저장했습니다.")

    def _show_settings_error(self, scope: str, message: str) -> None:
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.show_storage_error(message)
        elif scope == "inspection_target":
            self.main_screen.show_activity(f"설정 저장소 오류: {message}")

    def navigate(self, key: str) -> None:
        screen = self.screens.get(key)
        if screen is None or key == self._current_screen_key:
            return
        self._navigation_history.append(self._current_screen_key)
        self._current_screen_key = key
        self.stack.setCurrentWidget(screen)

    def navigate_back(self) -> None:
        if not self._navigation_history:
            return
        key = self._navigation_history.pop()
        screen = self.screens.get(key)
        if screen is not None:
            self._current_screen_key = key
            self.stack.setCurrentWidget(screen)


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("SMR Operator UI")
    app.setStyle("Fusion")
    font_path = files("smr_operator_ui.resources").joinpath("malgun.ttf")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setStyleSheet(load_stylesheet())
    return app


def main() -> int:
    if "--offscreen" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_application()
    window = OperatorWindow()
    window.show()
    return app.exec()
