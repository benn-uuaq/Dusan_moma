"""애플리케이션 초기화, 최상위 화면 전환, 서비스 연결을 담당한다."""

from __future__ import annotations

from dataclasses import replace

import os
import re
import sys
from datetime import datetime
from importlib.resources import files
from math import ceil, isfinite, pi
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
from smr_operator_ui.components.rect_work_view import (
    MAX_PROBE_CHORD_MM, max_safe_arc_mm, probe_chord_mm,
)
from smr_operator_ui.screens import (
    CobotJogScreen, CobotManualScreen, CobotSettingsScreen, ConnectionSettingsScreen,
    ErrorLogScreen, IOStatusScreen, LogFilesScreen, MainScreen, ManualScreen,
    ModeSlotsScreen, RunScreen, SettingsMenuScreen, SystemSettingsScreen,
    TpacBridgeScreen, UTSettingsScreen,
)
from smr_operator_ui.keypad import install_wheel_guard
from smr_operator_ui.state import CyclePhase
from smr_operator_ui.services.mqtt_server import SPEED_MAX, SPEED_MIN
from smr_operator_ui.services import (
    DummyMotionAdapter,
    MarkRunner,
    ErutClient,
    ErutSession,
    GridPlan,
    InspectionSimulator,
    JobSequencer,
    MqttServer,
    MqttTopics,
    RobotNodeSupervisor,
    RosStatusClient,
    SequencerState,
    SettingsService,
)
from smr_operator_ui.services.data_recorder import DataRecorder
from smr_operator_ui.services.motion_adapters import SwitchableMotion
from smr_operator_ui.services.vehicle_adapters import (
    VehicleAmrAdapter, VehicleLiftAdapter, VehicleOutriggerAdapter,
)
from smr_operator_ui.services.vehicle_client import VehicleClient
from smr_operator_ui.services.job_sequencer import CellStatus
from smr_operator_ui.services.erut_session import (
    HOME_BUSY_TEXT,
    MSG_AREA_APPLY_PENDING,
    MSG_ARC_LIMIT_CLAMPED,
)
from smr_operator_ui.services.ros_status_client import TASK_STATE_NAMES
from smr_operator_ui.services.reference_poses import (
    load_reference_poses,
    save_reference_poses,
)
from smr_operator_ui.styles import load_stylesheet

# TCP 직선 속도의 안전 상한 [mm/s]. 로봇 태스크(dus_init.script)도 같은
# 값으로 자르지만, 넘는 값을 애초에 보내지 않는다.
MAX_LINEAR_SPEED_MM_S = 100

# 로봇 태스크를 stop 한 뒤 play 하기까지 두는 간격. 컨트롤러가 태스크를
# 정리할 시간을 주지 않으면 play 가 거부된다.
ROBOT_RESTART_DELAY_MS = 1200

#: 기본 태스크 버전. Cobot 설정의 '태스크 선택'으로 바꾼다(dusan_v4 / dusan_v5).
DEFAULT_TASK_VERSION = "dusan_v4"


def robot_task_paths(version: str, nosensor: bool) -> tuple[str, str]:
    """(스캔 태스크, 마킹 태스크) 경로. 29999 `task -p` 에 붙는다.

    로봇 컨트롤러 안의 폴더는 `Dusan/<버전>` 이고, 파일 이름도 버전으로
    시작한다(robot_task/dusan_task_v4, dusan_task_v5 와 같은 구성).
      센서판   : <버전>.task,               <버전>_mark.task
      논센서판 : <버전>_nosensor_seq.task,  <버전>_nosensor_mark.task
    현장에서 실제 경로를 한 번 확인해야 한다(펜던트의 태스크 목록 기준).
    """
    folder = f"Dusan/{version}"
    if nosensor:
        return (f"{folder}/{version}_nosensor_seq.task",
                f"{folder}/{version}_nosensor_mark.task")
    return f"{folder}/{version}.task", f"{folder}/{version}_mark.task"
# abort 뒤 홈을 보내기까지 [ms]. 정지·태스크 교체가 먼저 처리되게 둔다.
ABORT_HOME_DELAY_MS = 500

# 시퀀서 상태를 화면의 "안전 순서" 5단계에 대응시킨다.
# 5단계는 **구간(열)마다** 반복된다: 정지·고정 → 수평 보정 → Cobot 검사
# → 안전 위치 → 다음 구간 이동. 한 열 안에서 리프트로 셀을 옮기는 것은
# 3단계(검사) 안에서 일어나므로 표시가 뒤로 가지 않는다.
_SEQUENCER_PHASES = {
    SequencerState.IDLE: CyclePhase.IDLE,
    SequencerState.SECURING: CyclePhase.SECURING,        # 1
    SequencerState.LEVELING: CyclePhase.LEVELING,        # 2
    # 셀과 셀 사이 리프트 이동은 아직 3단계(검사) 안이다. 2단계로 되돌리면
    # 화면이 3 → 2 → 3 으로 뒤로 간다.
    SequencerState.MOVING_LIFT: CyclePhase.INSPECTING,   # 3
    SequencerState.SCANNING: CyclePhase.INSPECTING,      # 3
    SequencerState.RETRACTING: CyclePhase.RETRACTING,    # 4
    SequencerState.MOVING_AMR: CyclePhase.MOVING,        # 5
    SequencerState.PAUSED: CyclePhase.PAUSED,
    SequencerState.DONE: CyclePhase.COMPLETE,
    SequencerState.STOPPED: CyclePhase.IDLE,
}

# 저장 문구에 쓰는 축 이름. 홈은 관절, 시작 포즈는 TCP 좌표다.
_JOINT_LABELS = ("J1", "J2", "J3", "J4", "J5", "J6")
_POSE_LABELS = ("X", "Y", "Z", "RX", "RY", "RZ")

_PX_RE = re.compile(r"(-?\d+)px")


def _scale_stylesheet(base_qss: str, scale: float) -> str:
    """QSS 안의 모든 px 값(글자 크기·여백·버튼 높이 등)을 같은 비율로 늘린다.

    전체화면처럼 창이 커져도 화면을 채우는 상자·그래픽만 커지고 글자·여백은
    그대로라 작은 창과 비율이 안 맞았다("전체 화면과 작은 화면 비율이 유지가
    되었으면 좋겠음", "작업 영역 아래의 글자는 전체 화면에 맞춰 조금 커지게").
    폰트 크기뿐 아니라 버튼 높이·여백까지 전부 같은 비율로 늘려야 실제로
    "화면 비율이 유지"된 것처럼 보인다.
    """
    def _scale_one(match: re.Match[str]) -> str:
        base = int(match.group(1))
        scaled = round(base * scale)
        if scaled == 0 and base != 0:
            # 0px로 반올림되면 (예: 음수 마진, 얇은 테두리) 그 값이
            # 아예 사라진 것처럼 보인다 — 원래 부호를 살려 최소 1(또는
            # -1)로 둔다.
            scaled = 1 if base > 0 else -1
        return f"{scaled}px"

    return _PX_RE.sub(_scale_one, base_qss)


class TopBar(QFrame):
    """제품 정보와 시스템 요약 상태를 항상 표시하는 상단 바.

    브랜드+부제+날짜/시간+배지 5개+배터리를 기본 글자 크기 그대로 다 늘어놓으면
    1280px 폭보다 훨씬 넓어져(배지만 5개, 각각 두 줄) 창 폭이 좁을 때 글자가
    잘렸다("상단부 글자와 날짜 잘리는 문제"). 위젯 하나하나를 눌러 찌그러뜨리는
    대신, 실제로 필요한 폭과 지금 창 폭을 비교해 부족한 만큼 전체 글자 크기를
    비례해서 줄인다("화면 비율에 맞춰서 글자 크기가 줄어들어야 할듯" — 사용자
    제안 그대로).
    """

    _MIN_FONT_SCALE = 0.55

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(68)
        # OperatorWindow가 창 크기에 맞춰 넘겨주는 전체 UI 배율. 여기에
        # 좁을 때만 작동하는 자체 축소 로직(shrink-to-fit)이 더해진다.
        self._global_scale = 1.0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 6, 20, 6)
        layout.setSpacing(14)
        # (라벨, 기본 폭(px)) — resizeEvent에서 이 기본값에 배율을 곱해 되돌린다.
        self._scalable_labels: list[tuple[QLabel, int]] = []
        brand = QLabel("3S-Robotics")
        brand.setObjectName("Brand")
        layout.addWidget(brand)
        layout.addStretch()
        self.clock = QLabel()
        self.clock.setObjectName("TopMeta")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.clock)
        self._register_scalable(brand, 26)
        self._register_scalable(self.clock, 20)
        self.badges: dict[str, ConnectionBadge] = {}
        # PLC/AMR/UT는 아직 실제 연결 신호가 붙어 있지 않은 자리표시자라
        # 항상 "연결됨"으로 둔다(placeholder). Cobot은 ros_status의
        # connected_changed 신호로 실제 상태를 받으므로 TPAC과 마찬가지로
        # "연결 안 됨"에서 시작해, 실제 연결이 확인돼야 초록으로 바뀐다
        # (버그 리포트: "Cobot 실제로 연결 안되어 있는데 연결됨으로 뜸").
        for name in ("PLC", "AMR"):
            badge = ConnectionBadge(name)
            self.badges[name] = badge
            layout.addWidget(badge)
        cobot_badge = ConnectionBadge("Cobot", initial_connected=False)
        self.badges["Cobot"] = cobot_badge
        layout.addWidget(cobot_badge)
        ut_badge = ConnectionBadge("UT")
        self.badges["UT"] = ut_badge
        layout.addWidget(ut_badge)
        # TPAC은 실제 외부 장비라 상태를 그대로 표시한다. 마찬가지로
        # 처음엔 "연결 안 됨"에서 시작해, 실제로 값을 읽어가야 초록으로 바뀐다.
        tpac_badge = ConnectionBadge("TPAC", initial_connected=False)
        self.badges["TPAC"] = tpac_badge
        layout.addWidget(tpac_badge)
        for badge in self.badges.values():
            for label in badge.findChildren(QLabel):
                self._register_scalable(label, 20)
        battery = QLabel("배터리  85%")
        battery.setObjectName("Product")
        layout.addWidget(battery)
        self._register_scalable(battery, 20)
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._update_clock()
        # 창이 뜨는 도중에 오는 첫 resizeEvent는 QSS 폰트 적용이 아직
        # 안 끝난 시점이라 sizeHint 계산이 살짝 부정확할 때가 있다.
        # 이벤트 루프가 레이아웃/스타일 적용을 마친 다음 한 번 더
        # 재보정해 첫 화면부터 정확히 맞게 한다.
        QTimer.singleShot(0, self._rescale_to_fit)

    def _register_scalable(self, label: QLabel, base_px: int) -> None:
        """resizeEvent에서 배율을 적용할 대상으로 라벨을 등록한다."""
        self._scalable_labels.append((label, base_px))

    def set_global_scale(self, scale: float) -> None:
        """OperatorWindow가 창 크기 비율에 맞춰 계산한 전체 UI 배율을 받는다.

        전체화면에서 나머지 화면 글자가 커지는데 상단바만 그대로면 어색해
        보인다. 높이도 같이 늘려야 커진 두 줄(날짜/시간)이 안 잘린다.
        """
        self._global_scale = scale
        self.setFixedHeight(round(68 * scale))
        self._rescale_to_fit()

    def _apply_font_scale(self, scale: float) -> None:
        # 창이 닫히는 중이면 Qt 가 라벨을 이미 지웠는데 이 목록에는 남아
        # 있을 수 있다. 그대로 만지면 RuntimeError 로 죽으므로(종료 중
        # 크래시) 살아 있는 것만 남기고 넘어간다.
        alive = []
        for label, base_px in self._scalable_labels:
            try:
                font = label.font()
                font.setPixelSize(max(11, round(base_px * scale)))
                label.setFont(font)
                # setFont만으로는 레이아웃이 캐시해 둔 sizeHint가 갱신되지
                # 않아, 그대로 두면 폰트를 줄여도 sizeHint()가 이전 값을
                # 그대로 돌려준다 — 새 글자 크기 기준으로 다시 재도록 한다.
                label.updateGeometry()
            except RuntimeError:
                continue
            alive.append((label, base_px))
        self._scalable_labels = alive
        # 배지 폭은 글자 폭으로 계산해 고정해 두므로, 글자 크기가 바뀌면
        # 다시 재야 한다(안 그러면 줄인 글자에 옛 폭이 남는다).
        for badge in getattr(self, "badges", {}).values():
            try:
                badge.sync_width()
            except RuntimeError:
                continue

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale_to_fit()

    def _rescale_to_fit(self) -> None:
        """실제 필요한 폭을 기본 글자 크기로 재보고, 지금 폭에 맞춰 줄인다.

        배지 안쪽 여백·라벨 사이 spacing 등 글자 크기와 무관하게 고정된
        폭이 섞여 있어, natural/available 비율 그대로 한 번만 줄이면
        고정폭 비중만큼 살짝 못 미쳐 여전히 넘친다. 몇 번 다시 재서
        좁혀 가면 빠르게 수렴한다.
        """
        # 창을 닫는 중에 예약된 호출이 뒤늦게 들어오면 이 위젯이 이미
        # 지워져 있을 수 있다. 그때 만지면 프로세스가 죽는다.
        try:
            available = self.width()
            if available <= 0:
                return
            self._apply_font_scale(self._global_scale)
            self.layout().invalidate()
            scale = self._global_scale
            for _ in range(8):
                natural = self.layout().sizeHint().width()
                if natural <= available:
                    break
                scale *= available / natural
                if scale <= self._MIN_FONT_SCALE:
                    scale = self._MIN_FONT_SCALE
                    self._apply_font_scale(scale)
                    self.layout().invalidate()
                    break
                self._apply_font_scale(scale)
                self.layout().invalidate()
            self.layout().activate()
        except RuntimeError:
            return

    def _update_clock(self) -> None:
        """운영자에게 표시되는 현재 날짜와 시각을 갱신한다."""
        now = datetime.now()
        self.clock.setText(now.strftime("날짜  %Y-%m-%d (%a)\n시간  %H:%M:%S"))


class OperatorWindow(QMainWindow):
    """화면 스택을 소유하고 UI·시뮬레이터·설정 서비스를 조정한다."""

    # 1280x720이 창의 최소 크기이자 UI 배율의 기준(1.0)이다. 전체화면 등으로
    # 창이 커지면 폭/높이 중 더 여유 없는 쪽 비율로 전체 QSS를 같이 키운다
    # ("전체 화면과 작은 화면 비율이 유지가 되었으면 좋겠음").
    _UI_SCALE_REFERENCE_W = 1280
    _UI_SCALE_REFERENCE_H = 720
    # 창이 커진 만큼 글자·버튼을 그대로 키우면(배율 = 창 배수) 전체화면에서
    # 너무 커진다 — 1920x1080 이면 1.5배라 글자가 눈에 띄게 굵어졌다. 커지긴
    # 하되 창 배수보다 완만하게 따라가도록 눌러 준다.
    _UI_SCALE_DAMPING = 0.45
    _UI_SCALE_MAX = 1.35

    def __init__(
        self,
        mqtt_server: MqttServer | None = None,
        *,
        start_mqtt: bool = True,
        start_ros: bool = True,
        start_erut: bool | None = None,
    ) -> None:
        super().__init__()
        # 입력값은 클릭해서 키패드·키보드로만 바꾼다 — 휠로는 안 바뀐다.
        install_wheel_guard()
        self.setWindowTitle("3S-Robotics | SMR Operator Console")
        self.resize(1280, 720)
        self.setMinimumSize(1280, 720)
        self._base_qss = load_stylesheet()
        self._current_ui_scale = 1.0
        root = QWidget()
        root.setObjectName("AppRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.top_bar = TopBar()
        layout.addWidget(self.top_bar)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.simulator = InspectionSimulator(self)
        self.settings_service = SettingsService(self)
        self.mqtt_server = mqtt_server or MqttServer(parent=self)
        # 격자 순회. 리프트/AMR은 실제 장비가 없어 더미 어댑터가 대신한다.
        self.sequencer = JobSequencer(self)
        # ERUT(스테이션)와의 통신. doosan/* 규격과 봉투가 달라 접속을 따로 둔다.
        self.erut = ErutClient(parent=self)
        # 차량 쪽(리프트·AMR·아웃트리거)은 더미와 ROS 차량을 바꿔 끼운다
        # (연결 설정의 '차량 제어'). ROS 차량 쪽은 ROS 노드가 생긴 뒤 붙인다.
        self.lift = SwitchableMotion(
            {"dummy": DummyMotionAdapter("리프트", parent=self)}, "dummy", parent=self)
        self.amr = SwitchableMotion(
            {"dummy": DummyMotionAdapter("AMR", parent=self)}, "dummy", parent=self)
        self.outrigger = SwitchableMotion(
            {"dummy": DummyMotionAdapter("아웃트리거", travel_ms=700, parent=self)},
            "dummy", parent=self)
        # 안전 위치 = 로봇이 물러나는 동작이라 차량과 상관없다(아직 더미).
        self.retractor = DummyMotionAdapter("안전 위치", travel_ms=700, parent=self)
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
            "tpac_bridge": TpacBridgeScreen(),
        }
        self._current_screen_key = "main"
        self._navigation_history: list[str] = []
        for screen in self.screens.values():
            self.stack.addWidget(screen)
            if hasattr(screen, "navigate"):
                screen.navigate.connect(self.navigate)
            if hasattr(screen, "back_requested"):
                screen.back_requested.connect(self.navigate_back)

        # '검사 시작'은 MQTT job_cmd 와 **똑같이** 실제 순회를 시작한다(차량·
        # 리프트·로봇). 예전에는 데모 사이클(simulator.start_cycle)에만 이어져
        # 안전 순서 표시만 돌고 장비는 하나도 움직이지 않았다.
        self.main_screen.start_requested.connect(self._start_inspection)
        self.main_screen.pause_requested.connect(self._toggle_pause)
        self.main_screen.stop_requested.connect(self._stop_inspection)
        self.main_screen.home_requested.connect(self._request_home)
        self.main_screen.alarm_reset_requested.connect(self._reset_alarms)
        self.main_screen.settings_requested.connect(lambda: self.navigate("settings"))
        self.main_screen.target_dimensions_changed.connect(
            lambda diameter, height: self.settings_service.save(
                "inspection_target", {"diameter_m": diameter, "height_m": height}
            )
        )
        self.main_screen.speed_changed.connect(
            lambda percent: self._apply_speed_ratio(percent, source="속도 바")
        )
        self.main_screen.work_area_changed.connect(self._apply_work_area_edit)
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
        # 태스크 판(센서/논센서). 기본은 센서판 — 불러온 값이 있으면 덮는다.
        self._nosensor = False
        self._task_version = DEFAULT_TASK_VERSION
        # 운전 모드 슬롯 {"1": {...}, ...}. 비운 슬롯은 {} 로 둔다(DB 저장소는
        # 키를 지우지 않고 덮어쓰기만 하므로).
        self._mode_slots: dict[str, dict] = {}
        # 로봇이 홈에 있는지(레지스터 276). None = 아직 받은 적 없음.
        self._robot_at_home: bool | None = None
        # 홈 플래그를 1 로 받아 본 적이 있는가 — 그때부터 인터락에 넣는다.
        self._home_flag_seen = False
        # 로봇 태스크가 끝나기를 기다리는 차량·리프트 이동. (문구, 실행 함수)
        self._pending_motions: list = []
        self._motion_wait_timer = QTimer(self)
        self._motion_wait_timer.setSingleShot(True)
        self._motion_wait_timer.timeout.connect(lambda: self._run_pending_motions(True))
        # 켤 때 불러오기는 비동기라, 그 전에 슬롯을 저장·비웠으면 늦게 온
        # 옛 값이 방금 바꾼 슬롯을 덮는다. 바꾼 뒤에는 불러온 값을 버린다.
        self._mode_slots_touched = False
        cobot_screen = self.screens.get("cobot")
        if cobot_screen is not None:
            cobot_screen.nosensor_changed.connect(self._set_nosensor)
            cobot_screen.task_version_changed.connect(self._set_task_version)
        for scope in (*self._settings_screens.keys(), "inspection_target",
                      "work_area", "robot_task", "mode_slots"):
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
        self._setup_vehicle()
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
        self.ros_status.alarm_received.connect(self._handle_robot_alarm)
        self.ros_status.joint_position_changed.connect(self._remember_joint)
        self.ros_status.command_result.connect(self._show_command_result)
        self.ros_status.connected_changed.connect(self.cobot_manual_screen.set_connected)
        self.ros_status.connected_changed.connect(self._restore_robot_settings)
        # 상단 바 Cobot 배지도 같은 신호로 실제 연결 여부를 반영한다
        # (이전엔 항상 "연결됨"으로 고정된 자리표시자였다).
        self.ros_status.connected_changed.connect(self.top_bar.badges["Cobot"].set_connected)
        # robot_control_node가 이미 로봇에 Modbus로 붙어 있는데 TPAC 화면에서
        # 또 수동으로 연결을 누르게 하는 건 불합리하다 — 그 노드의 연결
        # 여부를 그대로 따라가게 한다. "외부에 제공" 서버는 로봇 읽기가
        # 성공하면 TpacBridgeScreen 안에서 알아서 켠다.
        tpac_screen = self.screens["tpac_bridge"]
        self.ros_status.connected_changed.connect(tpac_screen.on_robot_link_changed)
        tpac_screen.tpac_link_changed.connect(self.top_bar.badges["TPAC"].set_connected)
        # 설정을 아직 못 불러왔더라도 시작할 때 한 번은 맞춰 둔다.
        self._sync_cobot_endpoint()

        # 로봇 IP/포트는 "연결 설정" 한 곳에서만 입력하고, **값이 바뀌는 즉시**
        # 다른 화면으로 퍼뜨린다. 예전에는 PostgreSQL 저장에 성공했을 때만
        # 반영해서, DB가 없는 환경(SMR_DATABASE_URL 미설정)에서는 주소를
        # 고쳐도 다른 화면이 옛 주소를 그대로 들고 있었다.
        conn_screen = self.screens["connection"]
        ip_field = conn_screen.field(conn_screen.ROBOT_IP_FIELD)
        if ip_field is not None:
            ip_field.textChanged.connect(lambda _text: self._sync_cobot_endpoint())
        port_field = conn_screen.field(conn_screen.ROBOT_PORT_FIELD)
        if port_field is not None:
            port_field.valueChanged.connect(lambda _value: self._sync_cobot_endpoint())
        # 연결/연결 해제도 이 화면에서만 한다.
        conn_screen.connect_requested.connect(self._connect_robot)
        conn_screen.disconnect_requested.connect(self._disconnect_robot)
        self.ros_status.connected_changed.connect(conn_screen.set_link_state)
        self.ros_status.connected_changed.connect(self._announce_robot_link)
        self.cobot_jog_screen.jog_pressed.connect(self._send_jog)
        self.cobot_jog_screen.jog_released.connect(self._stop_jog)
        self.cobot_jog_screen.command_requested.connect(self._save_reference_pose)
        self.cobot_manual_screen.command_requested.connect(self._handle_cobot_command)
        self.screens["cobot"].save_requested.connect(self._send_linear_speed)
        self.screens["cobot"].task_refresh_requested.connect(
            lambda: self.ros_status.call_command("task_status")
        )
        # 화면을 열 때마다 손으로 새로고침을 누르게 하는 대신, 로봇이
        # 연결될 때 한 번 자동으로 물어 채워 둔다.
        self.robot_node = RobotNodeSupervisor(self)
        self.robot_node.activity.connect(self.main_screen.show_activity)
        self.ros_status.connected_changed.connect(
            lambda connected: self.ros_status.call_command("task_status") if connected else None
        )
        self.ros_status.connected_changed.connect(self._update_jog_enabled)
        # 작업 영역을 실시간으로 밀어 주려면 연결·태스크 상태를 알아야 한다.
        self.ros_status.connected_changed.connect(self._on_robot_link_changed)
        self.ros_status.task_state_changed.connect(self._on_robot_task_state)
        self.ros_status.at_home_changed.connect(self._on_robot_at_home)
        self._update_jog_enabled(False)
        self.ros_status.speed_scale_changed.connect(self._show_speed_scale)
        self.ros_status.error_occurred.connect(self._show_ros_error)
        if start_ros:
            self.ros_status.start()
            # 프로그램 하나만 켜면 되도록 로봇 제어 노드도 UI 가 데리고 있는다.
            # 이미 떠 있으면(터미널에서 따로 띄웠거나) 그대로 쓴다.
            self.robot_node.start(already_running=self.ros_status.node_is_running())

        self.mqtt_server.command_received.connect(self._handle_mqtt_command)
        self.mqtt_server.connected_changed.connect(
            self._show_mqtt_connection_state
        )
        self.mqtt_server.error_occurred.connect(self._show_mqtt_error)
        if start_mqtt:
            self.mqtt_server.start()

        self._connect_sequencer()
        # ERUT 도 MQTT 접속이라 별도로 끄지 않으면 start_mqtt 를 따라간다.
        # 테스트가 start_mqtt=False 로 부를 때 네트워크를 열지 않게 하기 위함이다.
        self._connect_erut(start_mqtt if start_erut is None else start_erut)
        # 운영 기록(작업·스캔 좌표·알람이벤트·통신)을 데이터 저장 위치에 남긴다.
        self._setup_data_recorder()
        # 오류 로그·로그 파일·운전 모드 화면을 기록기와 설정에 잇는다.
        self._setup_record_screens()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_ui_scale()

    def _apply_ui_scale(self) -> None:
        """창 크기(폭·높이 중 여유 없는 쪽 기준)에 맞춰 전체 UI 배율을 다시 잰다.

        전체화면으로 커져도 글자·버튼·여백이 그대로면 상자만 커 보여 작은
        창과 비율이 안 맞고, 특히 작업 영역 아래 안내 문구 등은 상대적으로
        더 작아 보였다. QSS의 모든 px 값을 같은 비율로 다시 적용해 전체가
        함께 커지게 한다.
        """
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        raw = min(width / self._UI_SCALE_REFERENCE_W, height / self._UI_SCALE_REFERENCE_H)
        # 창 배수를 그대로 쓰지 않고 눌러서 반영한다(_UI_SCALE_DAMPING 참고).
        scale = 1.0 + (raw - 1.0) * self._UI_SCALE_DAMPING
        scale = max(1.0, min(scale, self._UI_SCALE_MAX))
        if abs(scale - self._current_ui_scale) < 0.02:
            return
        self._current_ui_scale = scale
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(_scale_stylesheet(self._base_qss, scale))
        # 상단바는 배지 개수가 많아 넓은 화면에서도 자체적으로 다시 줄일
        # 필요가 있을 수 있어, 전체 QSS를 다시 적용한 *다음*에 불러야
        # 상단바의 세밀한 계산이 QSS 재적용으로 덮이지 않는다.
        self.top_bar.set_global_scale(scale)
        # 요약 칸 값들도 같은 배율을 받는다 — 스타일시트가 키운 만큼
        # 기준을 올려야 전체화면에서 나머지 글자와 어울린다.
        self.main_screen.set_global_scale(scale)

    def _connect_erut(self, start: bool) -> None:
        """ERUT 요청을 로봇 순회와 잇는다.

        지금은 로봇만 진짜다 — start 계열만 실제로 로봇을 움직이고,
        캘리브레이션·마킹·배터리는 `ErutSession` 이 시험용으로 답한다.
        """
        self.erut_session = ErutSession(self.erut, self.sequencer, parent=self)
        self.erut_session.activity.connect(self.main_screen.show_activity)
        self.erut.activity.connect(self.main_screen.show_activity)
        self.erut.error_occurred.connect(self.main_screen.show_activity)

        # ERUT 요청 → 로봇 (진짜)
        self.erut_session.job_requested.connect(self._start_erut_job)
        self.erut_session.pause_requested.connect(self.sequencer.pause)
        self.erut_session.robot_stop_requested.connect(self._stop_robot_scan)
        # 가상 차량·리프트 값을 ERUT 응답에 실어 보낸다 (규격 탭5).
        self.erut_session.motion_state = self.motion_state
        # ERUT 의 홈 요청: 동작 중이면 세션이 409 + 사유로 거절하고,
        # 쉬고 있으면 여기로 온다.
        self.erut_session.robot_busy = self._robot_busy
        self.erut_session.home_requested.connect(self._send_home)
        # 원점에서 멈춰 선 로봇을 ERUT 의 "작업 시작"으로 풀어 준다.
        self.erut_session.scan_go_requested.connect(self._release_scan_gate)
        # 마킹: 점마다 차량 -> 고정 -> 리프트 -> 로봇(마킹 태스크) -> 복귀.
        self.mark_runner = MarkRunner(
            self.amr, self.lift, self.outrigger, self.retractor,
            send_target=lambda u, v: self.ros_status.send_pose("mark_target", [u, v]),
            start_mark_task=self._start_mark_task,
            restore_scan_task=lambda: self.ros_status.call_command("load_scan_task"),
            parent=self,
        )
        self.mark_runner.activity.connect(self.main_screen.show_activity)
        self.mark_runner.finished.connect(self.erut_session.finish_mark)
        self.erut_session.mark_requested.connect(self._start_marking)
        self.ros_status.scan_state_changed.connect(self.mark_runner.handle_scan_state)
        self.erut_session.resume_requested.connect(self.sequencer.resume)
        self.erut_session.abort_requested.connect(self._abort_job)
        self.erut_session.speed_requested.connect(
            lambda percent: self._apply_speed_ratio(percent, source="ERUT")
        )

        # 로봇 진행 → ERUT
        self.sequencer.cell_changed.connect(self.erut_session.on_cell_changed)
        self.sequencer.job_complete.connect(self.erut_session.on_job_complete)

        if start:
            self.erut.start()
            self.erut_session.start()

    def _scan_band_mm(self, plan) -> float:
        """한 줄이 덮는 세로 밴드 높이 [mm].

        EOAT 를 골랐으면 **그 유효 세로 커버가 곧 스캐너 밴드**다 — 몸통도,
        프로브 뭉치 전체 크기도 아니라 **한 번 지날 때 실제로 검사되는
        범위**다(5축 30, 8축 167.5 — services/job_sequencer 의
        PROBE_COVERAGE_MM). 그래서 이 값이 곧 ㄹ자 up 동작의 최대 상승량이
        된다. 로봇도 같은 규칙을 쓴다(dus_init 의 `if eoat_h > 0:
        scan_h = eoat_h`).

        EOAT 를 안 골랐으면 예전처럼 로컬 설정의 스캐너 높이를 쓴다.
        """
        _w, h = plan.eoat_size
        if h > 0:
            return h
        return self.main_screen.rect_view.work_area()[2]

    def _fill_from_local_setup(self, plan):
        """ERUT 가 안 준 장비 값(반지름·두께·EOAT)을 RCS 설정으로 채운다.

        기준 규격 20260812 의 prepare/start 에는 area 와 scan 만 있다.
        반지름·두께·검사장비 종류는 규격에 없는 **우리 쪽 장비 값**이라
        ERUT 가 보내지 않는다. 그대로 두면 0 이 되어 로봇이 굽은 벽을
        평면으로 보고 직선으로 훑고(반지름 0), 프로브 커버도 모른다
        (EOAT 0). 게다가 _send_work_area 가 그 0 을 **설정에 저장**까지
        해서 다음 작업에서도 틀린 값이 남았다.
        """
        extra = self._work_area_extra
        changes = {}
        if plan.radius <= 0 and float(extra.get("radius_mm", 0) or 0) > 0:
            changes["radius"] = float(extra["radius_mm"])
        if plan.thickness <= 0 and float(extra.get("thickness_mm", 0) or 0) > 0:
            changes["thickness"] = float(extra["thickness_mm"])
        if plan.eoat_probes <= 0 and float(extra.get("eoat_type", 0) or 0) > 0:
            changes["eoat_probes"] = int(float(extra["eoat_type"]))
        return replace(plan, **changes) if changes else plan

    def _start_erut_job(self, plan) -> None:
        """ERUT 가 준 계획으로 격자 순회를 시작한다. 로봇이 실제로 움직인다."""
        plan = self._fill_from_local_setup(plan)
        scan_h_mm = self._scan_band_mm(plan)
        self.simulator.begin_external(plan.column_count)
        # 구간 하나 = job 하나. prepare 가 오면 차량을 구간 x 로, 리프트를
        # 구간 y 로 정렬한 뒤 로봇이 프로브 3점을 잡고 원점에 선다.
        # 펜던트에 무엇이 올라가 있든 **체크한 판**의 스캔 태스크로 돈다.
        # 작업마다 한 번 — 셀마다 부르는 play 는 그대로 play 만 한다.
        self._push_task_paths()
        self._load_scan_task()
        self._record_job("ERUT", self.erut_session.job_id, plan)
        self.sequencer.start(plan, scan_h_mm, base_lift_mm=plan.origin_y,
                             move_first=True)

    def _abort_job(self) -> None:
        """ERUT 중단 요청(작업자 검사 종료). 멈추고 벽에서 물러나 홈으로 간다.

        규격 탭1: abort = 검사 마감, "로봇을 대기 상태로 되돌림". pause 처럼
        그 자리에서 이어갈 일이 없으므로 제자리에 두지 않는다 — 프로브가
        벽에 눌린 채로 있으면 다음 차량 이동을 못 한다. 홈 이동은 TCP -Z 로
        먼저 물러난 뒤 올라가므로(노드 move_home) 곡면을 긁지 않는다.
        """
        self._cancel_play()                 # 걸려 있던 play 가 뒤늦게 가지 않게
        self._pending_motions.clear()       # 미뤄 둔 차량·리프트 이동도 버린다
        self._motion_wait_timer.stop()
        self.sequencer.stop()
        for adapter in (self.lift, self.amr, self.outrigger, self.retractor):
            adapter.cancel()
        self.simulator.stop_cycle()
        # 마킹 중이었으면 마킹을 접고 스캔 태스크로 되돌린다(cancel 이 한다).
        self.mark_runner.cancel()
        # 원점 대기 표시를 푼다. 로봇은 290 = 7 을 들고 멈추므로 표시가 남아
        # query 가 계속 ready 로 답했다.
        self._origin_waiting = False
        self.main_screen.show_activity("작업을 중단했습니다 — 벽에서 물러나 홈으로 갑니다.")
        # 정지·태스크 교체가 먼저 처리되도록 잠깐 뒤에 홈을 보낸다.
        QTimer.singleShot(ABORT_HOME_DELAY_MS, self._send_home)

    def _connect_sequencer(self) -> None:
        """격자 순회를 로봇·리프트·AMR·화면·MQTT에 잇는다.

        시퀀서는 어느 셀인지만 판단하고, 실제 동작은 전부 시그널로 넘긴다.
        나중에 실제 PLC/AMR이 생기면 더미 어댑터만 갈아끼우면 된다.
        """
        seq = self.sequencer
        seq.activity.connect(self.main_screen.show_activity)
        self.lift.activity.connect(self.main_screen.show_activity)
        self.amr.activity.connect(self.main_screen.show_activity)
        self.lift.position_changed.connect(self._show_motion_position)
        self.amr.position_changed.connect(self._show_motion_position)
        self.outrigger.activity.connect(self.main_screen.show_activity)
        self.retractor.activity.connect(self.main_screen.show_activity)

        # 이동 요청 → 더미 어댑터 → 도착 신호 → 시퀀서
        seq.lift_target_requested.connect(
            lambda mm: self._defer_until_robot_idle(
                f"리프트 {mm:,.0f} mm", lambda: self.lift.move_to(mm, " mm")))
        # AMR 은 시퀀서가 **열 번호**로 부르는데, 바깥에는 이동 거리(mm)를
        # 알려야 한다(ERUT 규격 탭5 progress.moved). 열 간격으로 환산해
        # 어댑터에는 거리를 주고, 화면 문구에만 열 번호를 남긴다.
        seq.amr_move_requested.connect(
            lambda column: self._defer_until_robot_idle(
                f"{column}구역 차량 이동", lambda: self._move_amr_to_column(column)))
        self.lift.arrived.connect(seq.lift_arrived)
        self.amr.arrived.connect(seq.amr_arrived)
        # 아웃트리거 고정(1단계)과 안전 위치 복귀(4단계)도 아직 더미다.
        seq.secure_requested.connect(lambda: self.outrigger.move_to(1, " 고정"))
        seq.retract_requested.connect(lambda: self.retractor.move_to(1, " 복귀"))
        self.outrigger.arrived.connect(seq.secured)
        self.retractor.arrived.connect(seq.retracted)

        # 로봇: 셀 치수는 한 번만, play는 셀마다.
        seq.work_area_requested.connect(self._send_work_area)
        seq.robot_start_requested.connect(self._start_robot_scan)
        seq.robot_stop_requested.connect(self._stop_robot_scan)
        self.ros_status.scan_state_changed.connect(seq.handle_scan_state)
        self.ros_status.scan_state_changed.connect(self._handle_probe_error)
        self.ros_status.scan_state_changed.connect(self._handle_alive)
        self.ros_status.scan_state_changed.connect(self._handle_origin_wait)
        self.ros_status.scan_state_changed.connect(self._handle_vehicle_wait)

        # 화면과 외부 MQTT
        seq.state_changed.connect(self._show_sequencer_state)
        seq.cell_changed.connect(self._show_sequencer_cell)
        seq.cell_status_changed.connect(self._publish_cell_status)
        seq.job_complete.connect(self._finish_job)

    def _show_motion_position(self, _value: float) -> None:
        """가상 차량·리프트가 움직일 때마다 화면 값을 갱신한다."""
        self.main_screen.set_motion_values(self.lift.position, self.amr.position)

    def _move_amr_to_column(self, column: int) -> None:
        """열 번호를 이동 거리로 바꿔 AMR 더미에 넘긴다."""
        plan = self.sequencer.plan
        pitch = plan.column_pitch if plan is not None else 0.0
        origin = plan.origin_x if plan is not None else 0.0
        # 구간 원점(ERUT area.start.x)에서 열 간격만큼 더 간다.
        distance = origin + max(0, int(column) - 1) * pitch
        self.amr.move_to(distance, " mm", label=f"{column}구역 ({distance:.0f} mm)")

    def motion_state(self) -> dict:
        """가상 차량·리프트의 현재 값. ERUT 응답에 실어 나간다.

        규격 탭5 에 이미 자리가 있는 값들이다 — query 응답의 `lift_height`
        (리프트 있는 장치만, 화면 표시용)와 evt/progress 의 `moved`(mm,
        선택, 표시용). 새 필드를 만드는 게 아니라 비어 있던 자리를 채운다.

        실제 PLC/AMR 이 붙으면 DummyMotionAdapter 만 갈아끼우면 되고
        여기는 그대로다.
        """
        return {
            "lift_height": round(self.lift.position, 1),
            "moved": round(self.amr.position, 1),
        }

    def _clamp_work_width(self, width_mm: float, radius_mm: float,
                          thickness_mm: float, eoat_w_mm: float) -> float:
        """호 길이를 충돌 안전 한계로 자르고, 잘렸으면 알린다.

        호를 길게 잡을수록 좌우 프로브 사이 **현**이 길어지는데, 굽은 벽에서
        이 값이 한계를 넘으면 프로브가 벽에 닿는다. 로봇은 자기 안전 한계
        안에서만 움직이므로, 그보다 긴 값을 그대로 그리면 **로봇이 따라올 수
        없는 경로를 화면에만 그리게 된다**(마커가 경로 끝에 못 닿는다).
        """
        # 한계의 근거는 **좌우 프로브 사이** 거리다. EOAT 를 안 고르면
        # (eoat_w = 0) 옆으로 벌어진 프로브 자체가 없어 이 제약이 성립하지
        # 않는다 — 그때는 자르지 않는다.
        if eoat_w_mm <= 0:
            return width_mm
        limit = max_safe_arc_mm(radius_mm, thickness_mm, eoat_w_mm)
        if limit <= 0 or width_mm <= limit:
            return width_mm

        chord = probe_chord_mm(width_mm - eoat_w_mm, radius_mm, thickness_mm)
        message = (f"작업 호 길이 {width_mm:.0f} mm 는 최대 작업 길이를 넘습니다 — "
                   f"로봇 좌우 이동 거리가 {chord:.0f} mm 로 한계"
                   f"({MAX_PROBE_CHORD_MM:.0f} mm)를 초과합니다. "
                   f"가능한 최대는 {limit:.0f} mm 이며, 그 값으로 진행합니다.")
        self.main_screen.show_activity(message)
        self.cobot_manual_screen.add_alarm(message)
        # 장애가 아니다 — 줄여서 그대로 진행하므로 알림(evt/message)으로 낸다.
        self.erut_session.notify(
            *MSG_ARC_LIMIT_CLAMPED,
            f"구간 폭 {width_mm:.0f} mm 가 최대 작업 폭을 넘어 "
            f"최대값 {limit:.0f} mm 로 줄여 진행합니다.")
        return limit

    def _send_work_area(
        self, width_mm: float, height_mm: float, scan_h_mm: float,
        overlap_mm: float, radius_mm: float = 0.0, thickness_mm: float = 0.0,
        eoat_w_mm: float = 0.0, eoat_h_mm: float = 0.0,
        eoat_type: float = 0.0,
    ) -> None:
        """셀 치수와 호 정보를 로봇(레지스터 256~261)과 저장소에 반영한다.

        `width_mm` 은 **호 길이**다(현이 아니다). 로봇이 반지름과 함께
        현을 계산해 호 세 점을 만든다.

        반지름·두께만 0.1mm 단위로 보낸다. 반지름이 834.6 처럼 소수라
        정수 mm 로는 호 모양이 눈에 띄게 틀어지기 때문이다. 레지스터는
        16bit 라 3276.7mm 까지 담긴다.
        """
        # 다이얼로그는 너비/높이/스캐너/겹침만 묻는다. 나머지는 여기서
        # 기억해 뒀다가 그때 이어 붙인다(_apply_work_area_edit 참고).
        self._work_area_extra = {
            "radius_mm": radius_mm, "thickness_mm": thickness_mm,
            "eoat_w_mm": eoat_w_mm, "eoat_h_mm": eoat_h_mm,
            "eoat_type": eoat_type,
        }
        width_mm = self._clamp_work_width(width_mm, radius_mm, thickness_mm, eoat_w_mm)
        self.main_screen.set_work_area(width_mm, height_mm, scan_h_mm, overlap_mm,
                                       eoat_w_mm, radius_mm, thickness_mm)
        # 검사 대상 원(orbit_view)의 지름에는 두께를 안 섞고 따로 보여준다.
        self.main_screen.orbit_view.set_target_thickness_mm(thickness_mm)
        self.settings_service.save(
            "work_area",
            {"width_mm": width_mm, "height_mm": height_mm,
             "scan_h_mm": scan_h_mm, "overlap_mm": overlap_mm,
             "radius_mm": radius_mm, "thickness_mm": thickness_mm,
             "eoat_w_mm": eoat_w_mm, "eoat_h_mm": eoat_h_mm,
             "eoat_type": eoat_type},
        )
        self._push_work_area_to_robot(width_mm, height_mm, scan_h_mm, overlap_mm,
                                      radius_mm, thickness_mm, eoat_w_mm, eoat_h_mm,
                                      eoat_type)

    # 로봇 컨트롤러가 주는 태스크 상태(레지스터 500). 1 = 실행 중.
    _TASK_STATE_RUNNING = 1
    # 작업 영역 중 다이얼로그가 안 묻는 값들. 화면에서 고칠 때 이어 붙인다.
    _work_area_extra: dict = {
        "radius_mm": 0.0, "thickness_mm": 0.0,
        "eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0,
    }

    def _apply_work_area_edit(self, width_mm: float, height_mm: float,
                              scan_h_mm: float, overlap_mm: float) -> None:
        """화면에서 고친 작업 영역을 저장하고 **로봇에도 보낸다**.

        예전에는 저장만 해서, 겹침을 바꿔도 로봇은 옛 값으로 계속 돌았다.
        게다가 네 항목만 저장해 반지름·두께·EOAT 가 통째로 지워졌고, 다음에
        불러올 때 반지름 0(=평면)으로 로봇에 내려가 굽은 벽을 직선으로
        훑을 뻔했다. 다이얼로그가 안 묻는 값은 지금 값을 그대로 잇는다.
        """
        extra = dict(self._work_area_extra)
        self._send_work_area(width_mm, height_mm, scan_h_mm, overlap_mm, **extra)

    def _work_area_block_reason(self) -> str:
        """지금 작업 영역을 로봇에 못 보내는 이유. 보낼 수 있으면 빈 문자열.

        도는 중에 작업 계획을 바꾸면 안 된다 — 진행 중인 검사가 중간에
        다른 격자로 바뀐다. Modbus 가 끊겨 있으면 애초에 쓸 통로가 없다.
        어느 쪽이든 보류해 뒀다가 조건이 풀리면 자동으로 다시 보낸다.
        """
        if not getattr(self, "_robot_link_up", False):
            return "로봇 Modbus 연결이 없습니다"
        state = getattr(self, "_robot_task_state", None)
        if state == self._TASK_STATE_RUNNING:
            return "로봇 태스크가 실행 중입니다"
        return ""

    def _push_work_area_to_robot(
        self, width_mm: float, height_mm: float, scan_h_mm: float,
        overlap_mm: float, radius_mm: float, thickness_mm: float,
        eoat_w_mm: float, eoat_h_mm: float, eoat_type: float = 0.0,
    ) -> None:
        """작업 영역을 로봇 레지스터(256~264)에 쓰고 param_src(266)를 세운다.

        값은 **아홉 개**여야 한다 — 하나라도 모자라면 노드가 통째로
        거부한다(레지스터 264 = EOAT 종류).

        보낼 수 없는 상황이면(태스크 실행 중·연결 없음) 조용히 넘어가지 않고
        사유를 알린다. 안 그러면 화면 값과 로봇 값이 말없이 어긋난다.
        """
        # 소수가 나오는 자리(스캐너 높이·반지름·두께·EOAT)는 0.1mm 단위로
        # 보낸다 — 커버 167.5, 반지름 834.6 처럼 정수 mm 로 반올림하면
        # 호 모양과 줄 간격이 눈에 띄게 틀어진다. 로봇 쪽 dus_init 이 같은
        # 자리에 0.1 을 곱해 되돌린다. 16bit 라 3276.7mm 까지 담긴다.
        values = [width_mm, height_mm, round(scan_h_mm * 10), overlap_mm,
                  round(radius_mm * 10), round(thickness_mm * 10),
                  round(eoat_w_mm * 10), round(eoat_h_mm * 10), eoat_type]
        self._pending_work_area = values

        reason = self._work_area_block_reason()
        if reason:
            message = (f"작업 영역을 로봇에 보내지 못했습니다 — {reason}"
                       f"{self._task_state_note()}. "
                       f"조건이 풀리면 자동으로 다시 보냅니다.")
            self.main_screen.show_activity(message)
            self.cobot_manual_screen.add_alarm(message)
            # 장애가 아니다 — 조건이 풀리면 스스로 다시 보내므로 알림으로 낸다.
            # ERUT 에는 내부 통신 사정(연결·태스크 상태)을 싣지 않는다.
            self.erut_session.notify(
                *MSG_AREA_APPLY_PENDING,
                "구간 설정을 아직 반영하지 못했습니다. 준비되면 자동으로 다시 반영합니다.")
            return

        if self.ros_status.send_pose("work_area", values):
            self._pending_work_area = None
            self.main_screen.show_activity("작업 영역을 로봇에 반영했습니다.")

    def _task_state_note(self) -> str:
        """막힌 이유를 따질 수 있게 로봇이 보고한 태스크 상태를 덧붙인다.

        화면에서는 "실행 중이 아닌데 왜 안 나가냐"로 보이는데 로봇이 1 을
        주고 있는 경우가 있어, 값을 그대로 보여 줘야 어디가 어긋났는지
        가려낼 수 있다.
        """
        state = getattr(self, "_robot_task_state", None)
        if state is None:
            return " (로봇 태스크 상태 수신 전)"
        return f" (로봇 보고 태스크 상태 = {state}: {TASK_STATE_NAMES.get(state, '알 수 없음')})"

    def _retry_pending_work_area(self) -> None:
        """보류해 둔 작업 영역이 있으면 조건이 풀렸을 때 다시 보낸다."""
        values = getattr(self, "_pending_work_area", None)
        if values is None or self._work_area_block_reason():
            return
        if self.ros_status.send_pose("work_area", values):
            self._pending_work_area = None
            self.main_screen.show_activity("작업 영역을 로봇에 반영했습니다.")


    def _on_robot_link_changed(self, connected: bool) -> None:
        self._robot_link_up = bool(connected)
        self._retry_pending_work_area()
        if connected:
            # 노드가 다시 뜨면 파라미터가 기본값(센서판)으로 돌아가 있다.
            self._push_task_paths()

    def _task_paths(self) -> tuple[str, str]:
        """지금 고른 버전·판의 (스캔, 마킹) 태스크 경로."""
        return robot_task_paths(getattr(self, "_task_version", DEFAULT_TASK_VERSION),
                                bool(getattr(self, "_nosensor", False)))

    def _save_robot_task(self) -> None:
        """태스크 판·버전을 함께 저장한다.

        파일 저장소는 범위(robot_task)를 통째로 바꿔 쓰므로, 하나만 저장하면
        다른 하나가 지워진다. 늘 둘 다 넣는다.
        """
        self.settings_service.save("robot_task", {
            "nosensor": bool(getattr(self, "_nosensor", False)),
            "task_version": getattr(self, "_task_version", DEFAULT_TASK_VERSION),
        })

    def _push_task_paths(self) -> bool:
        """고른 판의 태스크 경로를 로봇 노드 파라미터로 넘긴다."""
        scan, mark = self._task_paths()
        return self.ros_status.set_task_paths(scan, mark)

    def _set_nosensor(self, on: bool) -> None:
        """Cobot 설정의 '논센서 태스크' 체크가 바뀌었다.

        저장하고, 노드에 경로를 넘기고, 작업 중이 아니면 **바로 그 판의
        스캔 태스크를 불러온다** — 펜던트에 무엇이 올라가 있든 체크한 판이
        돌게 하려고. 작업 중이면 지금 구간을 흔들지 않고 다음 작업부터 쓴다.
        """
        self._nosensor = bool(on)
        self._apply_task_choice("논센서판" if self._nosensor else "센서판", "태스크 판")

    def _set_task_version(self, version: str) -> None:
        """Cobot 설정의 '태스크 선택'이 바뀌었다 (dusan_v4 / dusan_v5).

        태스크 판 체크와 같다 — 저장하고, 노드에 경로를 넘기고, 작업 중이
        아니면 바로 그 버전의 스캔 태스크를 불러온다.
        """
        self._task_version = str(version)
        self._apply_task_choice(self._task_version, "태스크")

    def _apply_task_choice(self, label: str, what: str) -> None:
        self._save_robot_task()
        pushed = self._push_task_paths()
        busy = (self.sequencer.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED)
            or self.mark_runner.running)
        if not pushed:
            self.main_screen.show_activity(
                f"{what}을(를) {label}(으)로 저장했습니다 — 로봇 노드가 연결되면 적용됩니다.")
            return
        if busy:
            self.main_screen.show_activity(
                f"{what}을(를) {label}(으)로 바꿨습니다 — 다음 작업부터 적용됩니다.")
            return
        scan, _mark = self._task_paths()
        self.main_screen.show_activity(f"스캔 태스크를 불러옵니다: {scan}")
        self._load_scan_task()

    def _load_scan_task(self) -> None:
        """고른 판의 스캔 태스크를 로봇에 불러온다(정지 -> task -p)."""
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")
        self.ros_status.call_command("load_scan_task")

    #: 로봇 태스크가 끝나기를 기다리는 한도 [ms]. 넘으면 그냥 진행한다
    #: (루프판처럼 태스크가 스스로 안 끝나는 구성에서 멈춰 있지 않게).
    ROBOT_TASK_WAIT_MS = 30_000

    def _robot_task_running(self) -> bool:
        return getattr(self, "_robot_task_state", 0) == self._TASK_STATE_RUNNING

    def _home_interlock_active(self) -> bool:
        """홈 위치까지 확인해야 하는 상황인가.

        홈 플래그(276)를 **한 번이라도 1 로 받아 본 뒤에만** 건다. 로봇에
        올라가 있는 태스크가 이 값을 쓰는지 RCS 는 미리 알 수 없다 —
        v4·v5 는 쓰지만, 예전에 올려 둔 판은 안 쓴다. 안 쓰는 판이면
        레지스터가 계속 0 이라 그대로 걸면 차량이 영영 못 움직인다.
        1 을 한 번 본 뒤부터는 그 값을 믿는다(그때부터는 0 = 홈 밖).
        """
        return bool(getattr(self, "_home_flag_seen", False))

    def _robot_motion_block_reason(self) -> str:
        """지금 차량·리프트를 움직이면 안 되는 이유. 움직여도 되면 빈 문자열."""
        if self._robot_task_running():
            return "로봇 태스크가 실행 중입니다"
        if self._home_interlock_active() and not self._robot_at_home:
            return "로봇이 홈 위치에 있지 않습니다"
        return ""

    def _defer_until_robot_idle(self, label: str, run) -> None:
        """로봇이 홈에서 쉬고 있을 때만 차량·리프트를 움직인다.

        로봇은 한 셀을 마칠 때 **완료 플래그(290 = 5)를 먼저 쓰고 그 다음에
        홈으로 이동**한다(dus_finish -> 홈). 플래그만 보고 리프트를 올리면
        로봇이 아직 움직이는 중에 발판이 올라간다. 두 가지를 다 보고서야
        움직인다.
          * 태스크 상태(레지스터 500)가 실행 중이 아니다
          * 홈 위치 플래그(레지스터 276)가 1 이다 — 홈을 벗어나면 0 이므로
            펜던트에서 직접 돌리거나 조그로 빼낸 경우도 걸린다
        """
        reason = self._robot_motion_block_reason()
        if not reason:
            run()
            return
        self._pending_motions.append((label, run))
        self.main_screen.show_activity(f"{reason} — {label}은(는) 로봇이 홈에서 멈춘 뒤에 합니다.")
        if not self._motion_wait_timer.isActive():
            self._motion_wait_timer.start(self.ROBOT_TASK_WAIT_MS)

    def _run_pending_motions(self, timed_out: bool = False) -> None:
        if not self._pending_motions:
            self._motion_wait_timer.stop()
            return
        reason = self._robot_motion_block_reason()
        if reason and not timed_out:
            return                      # 아직 로봇이 움직인다 — 더 기다린다
        pending, self._pending_motions = self._pending_motions, []
        self._motion_wait_timer.stop()
        if timed_out:
            self.main_screen.show_activity(
                f"{reason or '로봇 태스크가 실행 중입니다'} — "
                f"{self.ROBOT_TASK_WAIT_MS // 1000}초가 지나 그대로 진행합니다: "
                + ", ".join(label for label, _ in pending))
        for label, run in pending:
            run()

    def _on_robot_at_home(self, at_home: bool) -> None:
        """홈 위치 플래그(276)가 바뀌었다. 홈에 닿으면 미뤄 둔 이동을 한다."""
        self._robot_at_home = bool(at_home)
        if self._robot_at_home:
            # 이 값을 쓰는 태스크가 올라가 있다는 뜻 — 이제부터 믿는다.
            self._home_flag_seen = True
        self._run_pending_motions()
        self._sync_manual_interlock()

    def _on_robot_task_state(self, state: int) -> None:
        self._robot_task_state = int(state)
        if not self._robot_task_running():
            # 태스크가 끝났다 — 홈 이동까지 마친 시점이다. 미뤄 둔 것을 한다.
            self._run_pending_motions()
        self._sync_manual_interlock()
        self.cobot_manual_screen.apply_status({
            "task_state": TASK_STATE_NAMES.get(self._robot_task_state,
                                               f"알 수 없음({self._robot_task_state})"),
        })
        self._retry_pending_work_area()

    def _start_robot_scan(self) -> None:
        """로봇 태스크를 멈췄다가 제로점에서 다시 재생한다.

        **stop 없이 play만 보내면 안 된다.** 로봇 태스크는 한 셀을 끝낸 뒤에도
        RUNNING 상태로 남아 있어서, play를 다시 보내면 컨트롤러가
        `Failed to execute: play`로 거부한다. 게다가 `dus_init`은 **태스크가
        시작될 때 한 번만** 작업 영역(레지스터 256~259)을 읽으므로, 멈췄다
        켜지 않으면 이전 값으로 계속 돈다.

        play 명령의 응답값으로 성공을 판단하지 않는다. 실제 진행 여부는
        로봇이 레지스터에 쓰는 상태(`robot/status/scan_state`)로만 본다.
        """
        # 원격 제어 모드가 아니면 컨트롤러가 play/stop 을 모두 거부한다
        # ("not supported in local control mode"). 펜던트를 만지면 로컬로
        # 돌아가므로 셀마다 켜 준다.
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")
        self._schedule_play()

    def _schedule_play(self) -> None:
        """잠시 뒤 play 를 보낸다. abort 가 오면 취소된다(_cancel_play).

        예전에는 QTimer.singleShot 으로 걸어 두어 되돌릴 수 없었다. 시작 직후
        abort 가 오면 그 뒤에 play 가 날아가 **멈춘 로봇이 다시 출발**했다.
        """
        if not hasattr(self, "_play_timer"):
            self._play_timer = QTimer(self)
            self._play_timer.setSingleShot(True)
            self._play_timer.timeout.connect(
                lambda: self.ros_status.call_command("play"))
        self._play_timer.start(ROBOT_RESTART_DELAY_MS)

    def _cancel_play(self) -> None:
        timer = getattr(self, "_play_timer", None)
        if timer is not None:
            timer.stop()

    def _start_marking(self, points: list) -> None:
        """ERUT 마킹 점들을 돈다. 격자 크기는 지금 작업 영역을 쓴다."""
        width, height, _scan_h, _overlap = self.main_screen.rect_view.work_area()
        self.mark_runner.start(points, width, height)

    def _start_mark_task(self) -> None:
        """마킹 태스크로 바꿔 끼우고 튼다.

        스캔 때처럼 원격 제어를 켜고 세운 뒤, 29999 `task -p` 로 마킹
        태스크를 불러와 play 한다. 다 끝나면 MarkRunner 가 스캔 태스크를
        다시 불러 둔다.
        """
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")
        self.ros_status.call_command("load_mark_task")
        self._schedule_play()

    def _stop_inspection(self) -> None:
        """주요 제어의 '정지' 버튼. 화면 시뮬레이터와 실제 순회·로봇을 모두 멈춘다.

        시뮬레이터는 데모 사이클(장비 없이 화면만 도는 경로)이고 순회는
        실제 MQTT/ERUT 작업이다 — 둘 중 어느 쪽이 돌고 있어도 이 하나로
        정지된다. `sequencer.stop()`이 `robot_stop_requested`를 내보내
        `_stop_robot_scan()`까지 이어지므로 로봇도 같이 선다.
        """
        self._stop_job_locally()

    def _job_running(self) -> bool:
        """순회나 마킹이 돌고 있는가(일시정지 포함)."""
        return (self.sequencer.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED)
            or self.mark_runner.running)

    def _stop_job_locally(self) -> None:
        """작업자가 RCS 에서 작업을 끊는다(정지 버튼·홈 이동).

        순회·마킹·더미 장비를 모두 멈추고, 걸려 있던 play 도 취소한다.
        ERUT 가 시킨 작업이었으면 **ERUT 에 실패로 알린다** — 안 알리면
        ERUT 는 오지 않을 complete 를 계속 기다린다(규격 탭2).
        """
        self._cancel_play()
        self._pending_motions.clear()          # 미뤄 둔 차량·리프트 이동도 버린다
        self._motion_wait_timer.stop()
        self.simulator.stop_cycle()
        self.sequencer.stop()
        self.mark_runner.cancel()
        for adapter in (self.lift, self.amr, self.outrigger, self.retractor):
            adapter.cancel()
        self._origin_waiting = False
        self.erut_session.interrupt_active_job()

    def _robot_busy(self) -> bool:
        """로봇이 동작 중인가 — 이때는 홈 명령을 거절한다.

        둘 중 하나면 동작 중으로 본다.
          * RCS 작업이 도는 중 (스캔 순회·마킹, 일시정지 포함)
          * 로봇 태스크가 실행 중 (레지스터 500 == 1) — 프로브·원점 대기·
            ㄹ자·마킹 어느 단계든, 펜던트에서 직접 튼 경우도 여기 걸린다.
        """
        return (self._job_running()
                or getattr(self, "_robot_task_state", 0) == self._TASK_STATE_RUNNING)

    def _request_home(self) -> None:
        """RCS 의 '로봇 홈' — 로봇이 쉬고 있을 때만 홈으로 보낸다.

        스캔·프로브·마킹 등 **로봇이 동작 중이면 보내지 않고** 로그에만
        "현재 로봇이 동작 중이므로 홈 이동이 불가합니다." 를 남긴다(팝업
        없음). 작업 도중 홈으로 가면 그 작업이 깨지므로, 먼저 '정지'로
        멈춘 뒤 누른다. ERUT 에서 온 홈 요청은 세션이 같은 문장으로 409
        응답한다(규격 20260914 탭4 E-1).
        """
        if self._robot_busy():
            self.main_screen.show_activity(f"홈 이동 거절 — {HOME_BUSY_TEXT}")
            self.cobot_manual_screen.activity_label.setText(HOME_BUSY_TEXT)
            return
        self._send_home()

    def _send_home(self) -> None:
        """홈 이동 자체는 노드가 한다: 태스크 정지 -> TCP -Z 로 물러남(홈보다
        뒤로는 안 감) -> 홈 높이로 상승 -> moveJ.

        현재 위치 표시는 영점으로 되돌린다. 태스크가 멈추면 로봇이 좌표를
        더 쓰지 않아 마지막 스캔 좌표가 그대로 남는데, 그걸 계속 그리면
        홈에 가 있는 로봇이 벽 위 어딘가에 있는 것처럼 보인다.
        """
        self.main_screen.show_activity("로봇을 홈으로 보냅니다.")
        self._park_position()
        self.ros_status.call_command("home")

    def _park_position(self) -> None:
        """현재 위치를 영점에 세우고, 로봇이 다시 좌표를 낼 때까지
        (생존 카운터가 움직일 때까지) 굳은 좌표를 무시한다."""
        self._position_stale = True
        self.main_screen.rect_view.park_position()

    def _stop_robot_scan(self) -> None:
        """로봇 태스크를 멈춘다. 장애·일시정지·정지가 모두 여기로 모인다.

        순회(시퀀서)를 멈추는 것만으로는 로봇이 서지 않는다 — 로봇은 자기
        태스크를 계속 돌리기 때문이다. 장애가 로봇 자신이 아니라 차량·리프트·
        배터리 쪽에서 나도 팔은 계속 벽을 훑게 되므로, 멈춤은 반드시 로봇까지
        내려가야 한다.

        `pause` 가 아니라 `stop` 을 쓴다. 재개는 `_start_robot_scan()` 이
        제로점부터 다시 play 하는 방식이라, 태스크를 중간에 붙들고 있을
        이유가 없다. 원격 제어 모드가 아니면 컨트롤러가 stop 을 거부하므로
        (`not supported in local control mode`) 먼저 켜 준다.

        같은 정지가 여러 경로로 겹쳐 들어올 수 있는데(장애 + 순회 일시정지),
        `stop` 은 멱등이라 여러 번 나가도 문제없다.
        """
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")

    def _show_sequencer_cell(
        self, column: int, row: int, label: str, ordinal: int
    ) -> None:
        """현재 셀을 메인 화면(AMR 위치 + 격자 이름표)에 반영한다."""
        plan = self.sequencer.plan
        total = plan.total_cells if plan is not None else 0
        self.main_screen.set_work_cell_label(f"{label} ({ordinal}/{total})")
        if plan is not None:
            self.main_screen.set_grid_position(
                column + 1, plan.column_count, row + 1, plan.row_count)
            # "총 구간 수"는 MQTT(ERUT)가 보낸 열 수 × 행 수를 그대로 따른다.
            self.main_screen.set_total_cells(total)
        self._sync_cycle_display()

    def _show_sequencer_state(self, _state_name: str) -> None:
        """시퀀서 상태가 바뀔 때마다 화면 진행 단계를 맞춘다."""
        self._sync_cycle_display()

    def _sync_cycle_display(self) -> None:
        """화면의 구간/단계 표시를 시퀀서의 실제 진행에 맞춘다.

        예전에는 `InspectionSimulator`가 자체 타이머로 단계를 넘겼는데,
        그러면 로봇이 아직 첫 셀에 있어도 화면만 마지막 구간까지 가버린다.
        진행 표시의 출처는 시퀀서 하나뿐이어야 한다.
        """
        seq = self.sequencer
        plan = seq.plan
        if plan is None:
            return
        column = seq.cell_ordinal() - 1
        column_index = column // plan.row_count if plan.row_count else 0
        self.simulator.apply_external_state(
            _SEQUENCER_PHASES.get(seq.state, CyclePhase.INSPECTING),
            current_segment=column_index + 1,
            completed_segments=column_index,
        )

    def _publish_cell_status(self, cell_id: str, state: str) -> None:
        """셀 진행 상태를 외부(MC)에 알린다. job_id가 곧 격자 이름이다."""
        try:
            self.mqtt_server.publish_job_state(cell_id, state)
        except Exception as exc:  # noqa: BLE001 - 발행 실패로 순회를 멈추지 않는다.
            self.main_screen.show_activity(f"Job 상태 발행 실패: {exc}")

    def _finish_job(self) -> None:
        """전체 격자를 다 돌면 검사 사이클도 함께 멈춘다."""
        self.simulator.stop_cycle()
        self.main_screen.show_activity("전체 격자 스캔을 완료했습니다.")

    def _apply_stored_settings(self, scope: str, values: dict) -> None:
        """DB 조회 결과를 해당 설정 범위의 소유 화면으로 전달한다."""
        if scope == "mode_slots":
            if self._mode_slots_touched:
                return
            self._mode_slots = {str(k): dict(v or {}) for k, v in (values or {}).items()}
            self.screens["modes"].set_slots(self._mode_slots)
            return
        if scope == "robot_task":
            self._nosensor = bool(values.get("nosensor", False))
            self._task_version = str(values.get("task_version", DEFAULT_TASK_VERSION))
            cobot_screen = self.screens.get("cobot")
            if cobot_screen is not None:
                cobot_screen.set_nosensor(self._nosensor)
                cobot_screen.set_task_version(self._task_version)
                # 목록에 없는 값이었으면 화면이 기본값으로 둔다 — 그 값을 따른다.
                self._task_version = cobot_screen.task_version()
            self._push_task_paths()
            return
        if scope == "inspection_target":
            if "diameter_m" in values and "height_m" in values:
                self.main_screen.orbit_view.set_target_dimensions(
                    float(values["diameter_m"]), float(values["height_m"])
                )
            return
        if scope == "work_area":
            keys = ("width_mm", "height_mm", "scan_h_mm", "overlap_mm")
            if all(k in values for k in keys):
                eoat_w_mm = values.get("eoat_w_mm")
                self.main_screen.set_work_area(
                    *(float(values[k]) for k in keys),
                    eoat_w_mm=float(eoat_w_mm) if eoat_w_mm is not None else None,
                    radius_mm=float(values["radius_mm"]) if values.get("radius_mm") is not None else None,
                    thickness_mm=float(values["thickness_mm"]) if values.get("thickness_mm") is not None else None,
                )
                thickness_mm = values.get("thickness_mm")
                if thickness_mm is not None:
                    self.main_screen.orbit_view.set_target_thickness_mm(float(thickness_mm))
                # 화면만 맞추면 로봇은 예전 레지스터 값으로 계속 돈다
                # (겹침을 바꿨는데 로봇이 옛 행 수로 도는 원인이었다).
                # 저장된 값을 로봇에도 그대로 밀어 준다.
                self._work_area_extra = {
                    "radius_mm": float(values.get("radius_mm") or 0.0),
                    "thickness_mm": float(values.get("thickness_mm") or 0.0),
                    "eoat_w_mm": float(values.get("eoat_w_mm") or 0.0),
                    "eoat_h_mm": float(values.get("eoat_h_mm") or 0.0),
                    "eoat_type": float(values.get("eoat_type") or 0.0),
                }
                self._push_work_area_to_robot(
                    float(values["width_mm"]), float(values["height_mm"]),
                    float(values["scan_h_mm"]), float(values["overlap_mm"]),
                    float(values.get("radius_mm") or 0.0),
                    float(values.get("thickness_mm") or 0.0),
                    float(values.get("eoat_w_mm") or 0.0),
                    float(values.get("eoat_h_mm") or 0.0),
                    float(values.get("eoat_type") or 0.0),
                )
            return
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.apply_values(values)
        if scope == "system":
            self._apply_system_settings(scope, values)
            if scope == "connection":
                self._sync_cobot_endpoint()
                self._apply_vehicle_mode()

    def _connect_robot(self) -> None:
        """"연결 설정" 화면의 주소로 로봇에 붙는다.

        붙어야 하는 경로가 둘이다 — 대시보드(로봇 제어)와 TPAC 브리지의
        Modbus 폴러. 예전에는 화면마다 따로 눌러야 했는데, 같은 로봇에
        같은 주소로 붙는 것이라 한 번에 같이 건다.

        **주소를 먼저 제어 노드에 심고** 연결한다. 예전에는 노드를 띄울 때
        읽은 주소로만 붙어서, 화면에 적힌 IP와 실제 붙은 IP가 달랐다.
        """
        conn = self.screens["connection"]
        ip, port = conn.robot_ip(), conn.robot_port()
        conn.set_link_message(f"{ip} 로 연결하는 중…")
        if not self.ros_status.set_robot_endpoint(ip, port):
            conn.set_link_message(
                "로봇 제어 노드가 응답하지 않습니다. 노드가 떠 있는지 확인하세요.")
            return
        self._handle_cobot_command("connect")
        tpac_screen = self.screens["tpac_bridge"]
        if tpac_screen.poller is None:
            tpac_screen._start_robot()

    def _announce_robot_link(self, connected: bool) -> None:
        """운영자가 끊은 게 아닌데 연결이 바뀌면 알린다(끊김 / 자동 재연결)."""
        was = getattr(self, "_robot_link_announced", None)
        self._robot_link_announced = bool(connected)
        if was is None or was == bool(connected):
            return
        conn = self.screens["connection"]
        if connected:
            text = "로봇에 다시 연결됐습니다."
        elif getattr(self, "_robot_disconnect_requested", False):
            self._robot_disconnect_requested = False
            return
        else:
            text = "로봇 연결이 끊겼습니다 — 연결 가능해지면 자동으로 다시 연결합니다."
            self.cobot_manual_screen.add_alarm(text)
        conn.set_link_message(text)
        self.main_screen.show_activity(text)

    def _disconnect_robot(self) -> None:
        """연결한 경로를 같이 끊는다."""
        self._robot_disconnect_requested = True
        tpac_screen = self.screens["tpac_bridge"]
        if tpac_screen.poller is not None:
            tpac_screen._stop_robot()
        self._handle_cobot_command("disconnect")
        self.screens["connection"].set_link_message("연결을 끊었습니다.")

    def _sync_cobot_endpoint(self) -> None:
        """연결 설정의 협동로봇 주소를 로봇 주소가 필요한 모든 화면에 반영한다.

        로봇 IP는 "연결 설정" 한 곳에서만 입력한다 — 예전에는 Cobot 수동
        제어, TPAC 설정이 각자 IP 칸을 따로 갖고 있어 같은 로봇 주소를
        화면마다 다시 입력해야 했고, 한 곳만 고치면 서로 어긋났다.
        """
        values = self.screens["connection"].values()
        ip = str(values.get("협동로봇 IP", "")).strip()
        port = values.get("Modbus 포트")
        if ip:
            self.cobot_manual_screen.set_endpoint(ip)
        self.screens["tpac_bridge"].set_robot_endpoint(ip, port)

    def _mark_settings_saved(self, scope: str) -> None:
        """저장 완료 후 해당 화면의 상태를 갱신한다."""
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.mark_saved()
            if scope == "connection":
                self._sync_cobot_endpoint()
                self._apply_vehicle_mode()
        elif scope == "inspection_target":
            self.main_screen.show_activity("검사 대상 크기를 저장했습니다.")

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
        """수신 MQTT 명령을 검사대상 설정과 검사 사이클에 반영한다.

        외부(MC)에서 오는 명령은 전체 작업 시작(`job_cmd`)/일시정지·정지
        (`mc_cmd`)/중단(`job_clear`) 뿐이다. 구역(segment)과 격자(grid)를
        하나씩 순회하는 자동 진행은 여기서 다루지 않고 UI/로봇 쪽이 맡는다.
        """
        if topic == MqttTopics.JOB_COMMAND:
            # job_cmd 자체가 "전체 작업 시작" 명령이다. 대상 치수와 ㄹ자 스캔
            # 값을 반영한 뒤 검사 사이클을 시작한다.
            self._apply_mqtt_job_info(payload)
            return

        if topic == MqttTopics.SPEED:
            self._apply_speed_ratio(payload.get("speed"), source="MQTT")
            return

        if topic == MqttTopics.PROBE_ACK:
            self._handle_probe_ack(payload)
            return

        if topic == MqttTopics.JOB_CLEAR:
            # 전체 작업 정지(중단). RCS '정지' 버튼과 같은 경로로 순회·마킹·
            # 로봇 태스크까지 멈춘다 — 예전에는 순회만 멈춰 로봇 태스크가 계속
            # 돌았고, 그 뒤 홈 명령이 "동작 중"으로 거절됐다.
            self._stop_job_locally()
            self.main_screen.show_activity("MQTT 요청으로 작업을 정지했습니다.")
            return

        if topic != MqttTopics.MC_COMMAND:
            return

        amr_command = payload.get("amr")
        cobot_command = payload.get("cobot")
        if cobot_command == "home":
            # RCS '로봇 홈' 버튼과 같다 — 동작 중이면 거절하고 로그에만 남긴다.
            self._request_home()
            return
        if amr_command == "run" or cobot_command == "run":
            # 일시정지 후 재개도 이 명령을 그대로 쓴다.
            self.simulator.start_cycle()
            self.sequencer.resume()
        elif amr_command in {"stop", "ems"} or cobot_command in {"stop", "ems"}:
            self.sequencer.pause()
            # MQTT 명령은 재전송될 수 있으므로 토글이 아닌 멱등적인
            # 일시정지 API를 사용한다. ems(비상정지)도 우선 일시정지로 반영하고,
            # 실제 하드웨어 비상정지는 `doosan/robot/req/ems` 토픽이 담당한다.
            self.simulator.pause_cycle()

    def _apply_mqtt_job_info(self, payload: dict[str, Any]) -> None:
        """Job 치수를 mm에서 m로 변환해 화면과 저장소에 반영한다."""
        try:
            job_info = payload["job_info"]
            diameter_mm = float(str(job_info["diameter"]).strip())
            height_mm = float(str(job_info["height"]).strip())
            target_distance_mm = float(
                str(job_info["target_distance"]).strip()
            )
            # 지름·높이는 실제 치수라 0 이면 성립하지 않는다.
            for name, value in (("지름", diameter_mm), ("높이", height_mm)):
                if not isfinite(value) or value <= 0:
                    raise ValueError(
                        f"{name}는 0보다 큰 값이어야 합니다 (받은 값 {value:g}).")
            # 이동거리도 실제로 있어야 하는 값이다 (차량이 검사 대상까지
            # 가는 거리). 다만 예전에는 지름·높이와 뭉뚱그려
            # "검사대상 치수는 0보다 큰 값이어야" 라고만 해서, 화면만 보고는
            # 셋 중 무엇이 0 인지 알 수 없었다 — 그래서 이름을 붙여 준다.
            if not isfinite(target_distance_mm) or target_distance_mm <= 0:
                raise ValueError(
                    f"이동거리는 0보다 큰 값이어야 합니다 "
                    f"(받은 값 {target_distance_mm:g}).")
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
                "target_distance_m": target_distance_mm / 1000.0,
            },
        )
        self.main_screen.show_activity(
            "MQTT Job 정보를 검사대상 설정에 적용했습니다. "
            f"(지름 {diameter_m:.2f} m, 높이 {height_m:.2f} m)"
        )
        # 작업기록에 적을 job_id. 격자 순회를 시작할 때 쓴다.
        self._mc_job_id = str(payload.get("job_id", "")).strip()
        self._apply_mqtt_plan(payload.get("plan"))

    def _apply_mqtt_plan(self, plan: Any) -> None:
        """작업 계획(격자 분할)을 반영하고 첫 셀(1A)부터 작업을 시작한다.

        원통을 편 직사각형을 격자로 나눈다. 열(1~12)은 AMR이 정차하는 원주
        구역, 행(A~F)은 리프트 높이다. **ㄹ자는 셀 하나(`cell_width` ×
        `cell_height`)만 그린다.** 원통 전체 높이(`job_info.height`)를 셀
        높이로 쓰면 안 된다 — 그건 로봇이 한 번에 닿지 못하는 높이다.

        스캐너 유효높이(`scan_h`)는 장비 고유값이라 MQTT로 받지 않고 로컬
        설정(작업 영역 대화상자에서 입력한 값)을 그대로 쓴다.
        `plan`이 없으면(옛 payload 등) 화면 갱신과 사이클 시작 모두 건너뛴다.
        자세한 모델은 `docs/grid_sequencer_design.md` 참고.
        """
        if not isinstance(plan, dict):
            return
        try:
            overlap = float(str(plan["overlap"]).strip())
            grid = GridPlan(
                column_count=int(str(plan["column_count"]).strip()),
                row_count=int(str(plan["row_count"]).strip()),
                cell_width=float(str(plan["cell_width"]).strip()),
                cell_height=float(str(plan["cell_height"]).strip()),
                # 받는 겹침은 **격자끼리**의 겹침이다 — 한 격자 안 ㄹ자 줄
                # 겹침이 아니다. 이 값만큼 차량과 리프트가 덜 이동해서
                # 옆·위 격자와 겹치고, 로봇은 그 겹친 자리에서 다시 영점을
                # 잡는다(ERUT 화면의 "오버랩 간격"과 같은 값이다).
                # 가로·세로 모두 같은 값을 쓴다 — 규격에 하나로 온다.
                # 격자 안 줄 간격은 로봇이 프로브 커버로 스스로 정하므로
                # scan_overlap 은 건드리지 않는다.
                scan_overlap=0.0, pitch_x=overlap, pitch_y=overlap,
                # 로봇이 호를 계산하는 데 쓴다. 없으면 0 -> 평면(직선 스캔).
                # cell_width 는 **호 길이**로 해석한다(현이 아니다).
                radius=float(str(plan.get("radius", 0)).strip() or 0),
                thickness=float(str(plan.get("thickness", 0)).strip() or 0),
                # 검사장비 종류. 프로브 축 수(5 또는 8)로 고른다.
                eoat_probes=int(float(str(plan.get("eoat", 0)).strip() or 0)),
            )
            if grid.column_count <= 0 or grid.row_count <= 0:
                raise ValueError("열/행 수는 1 이상이어야 합니다.")
            # EOAT 를 골랐으면 그 세로가 스캐너 밴드가 된다.
            scan_h_mm = self._scan_band_mm(grid)
            # 어느 값이 문제인지 짚어 준다 — 뭉뚱그리면 화면만 보고는
            # 무엇을 고쳐야 할지 알 수 없다.
            for name, value in (("셀 가로", grid.cell_width),
                                ("셀 세로", grid.cell_height),
                                ("스캐너 높이", scan_h_mm)):
                if not isfinite(value) or value <= 0:
                    raise ValueError(
                        f"{name}는 0보다 큰 값이어야 합니다 (받은 값 {value:g}).")
            # 겹침은 0 이어도 된다(격자를 딱 붙여 놓는 경우). 다만 격자보다
            # 크면 차량·리프트가 뒤로 가거나 제자리를 맴돈다.
            if not isfinite(overlap) or overlap < 0:
                raise ValueError("겹침은 0 이상이어야 합니다.")
            if overlap >= min(grid.cell_width, grid.cell_height):
                raise ValueError(
                    f"겹침 {overlap:g} mm 가 격자({grid.cell_width:g} x "
                    f"{grid.cell_height:g} mm)보다 큽니다.")
        except (KeyError, TypeError, ValueError) as exc:
            self.main_screen.show_activity(f"MQTT 작업 계획 적용 실패: {exc}")
            return

        # job_cmd 자체가 "전체 작업 시작" 명령이다. 화면의 진행 표시는
        # 시퀀서가 주도한다 — 데모 타이머로 혼자 앞서 나가면 실제 로봇이
        # 아직 1A에 있는데도 화면만 12구역까지 가버린다.
        self._begin_grid_job(grid, scan_h_mm, source="MC",
                             job_id=getattr(self, "_mc_job_id", ""))

    def _begin_grid_job(self, grid, scan_h_mm: float, source: str = "MC",
                        job_id: str = "") -> None:
        """격자 순회를 시작한다. MQTT job_cmd 와 RCS '검사 시작'이 함께 쓴다.

        체크한 판(센서/논센서)의 스캔 태스크를 먼저 불러 두고, 화면 진행
        표시는 시퀀서가 주도하게 한다(데모 타이머로 혼자 앞서 나가지 않게).
        """
        self.simulator.begin_external(grid.column_count)
        self._push_task_paths()
        self._load_scan_task()
        self._record_job(source, job_id, grid)
        self.sequencer.start(grid, scan_h_mm)

    #: 사내 MC 규격의 격자 한계 — 열 1~12, 행 A~F.
    _MAX_COLUMNS = 12
    _MAX_ROWS = 6

    def _start_inspection(self) -> None:
        """RCS '검사 시작' — MQTT job_cmd 와 같은 순회를 RCS 설정으로 시작한다.

        job_cmd 가 실어 오는 값을 RCS 가 가진 값으로 채운다.
          셀 크기·겹침  : 작업 영역 설정 (Modbus 256~259 로 나가는 값)
          반지름·두께·EOAT : 작업 영역의 장비 값
          열·행 수      : 검사 대상 원주 / 열 간격, 높이 / 행 간격 (올림)
                         — 사내 MC 규격 한계(12열, 6행)로 자른다.
        겹침은 격자끼리의 겹침이라 가로·세로 이동량에서 뺀다(job_cmd 와 같다).
        """
        if (self.sequencer.state not in (
                SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED)
                or self.mark_runner.running):
            self.main_screen.show_activity("이미 작업 중입니다 — 끝나거나 정지한 뒤 시작하세요.")
            return
        width, height, _scan_h, overlap = self.main_screen.rect_view.work_area()
        diameter_m, target_h_m = self.main_screen.orbit_view.target_dimensions()
        column_pitch = max(width - overlap, 1.0)
        row_pitch = max(height - overlap, 1.0)
        columns = int(ceil(pi * diameter_m * 1000.0 / column_pitch))
        rows = int(ceil(target_h_m * 1000.0 / row_pitch))
        columns = min(max(columns, 1), self._MAX_COLUMNS)
        rows = min(max(rows, 1), self._MAX_ROWS)
        extra = self._work_area_extra
        grid = GridPlan(
            column_count=columns, row_count=rows,
            cell_width=width, cell_height=height,
            scan_overlap=0.0, pitch_x=overlap, pitch_y=overlap,
            radius=float(extra.get("radius_mm", 0) or 0),
            thickness=float(extra.get("thickness_mm", 0) or 0),
            eoat_probes=int(float(extra.get("eoat_type", 0) or 0)),
        )
        self.main_screen.show_activity(
            f"RCS 검사 시작: {columns}열 x {rows}행 (셀 {width:.0f} x {height:.0f} mm, "
            f"겹침 {overlap:g} mm)")
        self._begin_grid_job(grid, self._scan_band_mm(grid), source="RCS")

    def _toggle_pause(self) -> None:
        """RCS '일시정지' — 실제 순회·로봇도 같이 멈추고 다시 이어간다.

        MQTT mc_cmd 의 stop/run 과 같다. 순회가 안 돌 때는 예전처럼 데모
        사이클만 토글한다.
        """
        state = self.sequencer.state
        if state is SequencerState.PAUSED:
            self.simulator.start_cycle()
            self.sequencer.resume()
        elif state not in (SequencerState.IDLE, SequencerState.DONE,
                           SequencerState.STOPPED):
            self.sequencer.pause()
            self.simulator.pause_cycle()
        else:
            self.simulator.toggle_pause()

    def _apply_speed_ratio(self, raw: Any, source: str = "UI") -> None:
        """로봇 전체 동작 속도 비율[%]을 로봇에 보낸다. 2~100.

        노드가 29999 `speed -v` 로 실시간 반영한다. 돌고 있는 동작에도
        바로 먹으므로 스캔 중에 줄여도 된다.
        """
        try:
            percent = int(str(raw).strip())
        except (TypeError, ValueError):
            self.main_screen.show_activity(f"{source} 속도 값을 읽지 못했습니다: {raw!r}")
            return
        if not SPEED_MIN <= percent <= SPEED_MAX:
            self.main_screen.show_activity(
                f"속도 비율은 {SPEED_MIN}~{SPEED_MAX} % 여야 합니다: {percent}"
            )
            return
        self.ros_status.send_value("speed_ratio", percent)
        self.main_screen.show_activity(f"{source} 속도 비율 {percent} % 를 로봇에 보냈습니다.")

    def _show_speed_scale(self, percent: int) -> None:
        """로봇이 실제로 쓰고 있는 속도 비율을 화면에 표시한다.

        펜던트에서 직접 바꿔도 이 값으로 들어온다. Modbus 레지스터 17을
        노드가 읽어 발행한 것이다.
        """
        self.main_screen.set_speed_scale(percent)

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
        """원점 기준 상대 자세를 화면에 표시하고 외부(MC)로 내보낸다.

        이 좌표가 스캐너 관리 시스템으로 나가는 실제 데이터다. 베이스 프레임
        좌표(`tcp_pose`)는 모니터링용이라 내보내지 않는다.
        """
        self.cobot_manual_screen.apply_zero_point(self._format_pose(values))
        if len(values) >= 3:
            # 로봇 태스크(dus_init.script)가 베이스 좌표계로 cur-zero를 낸다.
            # v3부터 원점이 Y+ 에서 Y- 로 바뀌어(dus_probe_l.script), 첫 패스가
            # Y- 에서 Y+ 로 움직인다 — cur-zero(rel_y)를 부호 그대로 넘긴다.
            # 화면에서 어느 쪽이 되는지는 RectWorkView.to_px()가 정한다(원점을
            # 오른쪽 끝에 그리도록 뒤집어 둠). 가로 = Y, 세로 = +Z(위 +).
            # register_map.txt 4항 참고.
            # 현재 위치는 **항상** 그린다. 스캔 중이 아니면 로봇이 0 을
            # 보내므로 점이 영점에 가만히 서 있게 된다 — 표시가 생겼다
            # 없어졌다 하는 것보다 그쪽이 읽기 쉽고, 같은 값을 받는 TPAC
            # 쪽과도 어긋나지 않는다.
            # 홈 이동·태스크 정지 뒤에는 레지스터에 마지막 스캔 좌표가 굳어
            # 남는다 — 생존 카운터가 다시 움직일 때까지는 그리지 않는다.
            if not getattr(self, "_position_stale", False):
                self.main_screen.apply_wall_position(values[1], values[2])
        self._publish_tcp(values)

    def _publish_tcp(self, values: list) -> None:
        """제로점 좌표에 현재 격자 이름을 붙여 발행한다.

        좌표만으로는 원통 어디인지 알 수 없다. 격자를 아는 것은 시퀀서
        뿐이므로 여기서 둘을 합친다. 10 Hz로 나가는 값이라 발행 실패를
        화면 문구로 쏟아내지 않고, 스캔도 멈추지 않는다.
        """
        try:
            self.mqtt_server.publish_tcp(values, self.sequencer.current_cell())
        except Exception:  # noqa: BLE001 - 발행 실패로 스캔을 멈추지 않는다.
            pass

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
        # 속도 바가 % 를 mm/s 로 환산할 기준값도 함께 맞춘다.
        self.main_screen.set_base_speed(
            min(int(cobot.linear_speed()), MAX_LINEAR_SPEED_MM_S)
        )
        for value, name in ((cobot.linear_speed(), "linear_speed"),
                            (cobot.speed_ratio(), "speed_ratio")):
            if self.ros_status.send_value(name, int(value)):
                restored.append(name)

        if restored:
            self.cobot_manual_screen.activity_label.setText(
                f"저장된 설정을 로봇에 다시 적용했습니다: {', '.join(restored)}"
            )

    def _available_writes(self) -> list[str]:
        """주소가 정해져 실제로 보낼 수 있는 명령 이름을 모은다.

        조그(jog_joint/jog_tcp)는 여기 넣지 않는다 — Modbus 레지스터가
        아니라 30001 소켓의 speedj/speedl로 나가므로 레지스터 주소 유무와
        무관하다. 조그 버튼의 활성화는 `_update_jog_enabled()`가 로봇
        연결 여부로 따로 관리한다.
        """
        names = ("save_home_pose", "save_start_pose", "move_home", "linear_speed")
        return [name for name in names if self.ros_status.writable(name)]

    def _update_jog_enabled(self, connected: bool) -> None:
        """로봇 연결 여부로 조그 버튼을 잠그거나 연다.

        레지스터 주소로 잠그면(예전 방식) 조그는 레지스터를 안 쓰므로
        항상 잠긴 채로 남는다 — 실제 연결과 무관한 기준이었다.
        """
        names = set(self._available_writes())
        if connected:
            names |= {"jog_joint", "jog_tcp"}
        self.cobot_jog_screen.set_enabled_commands(names)

    def _send_jog(self, kind: str, axis: int, direction: int) -> None:
        """조그 시작을 알린다. 노드가 30001로 speedj/speedl을 보낸다."""
        self.ros_status.send_jog(kind, axis, direction)

    def _stop_jog(self, kind: str, _axis: int) -> None:
        """버튼에서 손을 떼면 즉시 멈춘다. 노드가 29999 stop을 쓴다."""
        self.ros_status.send_jog(kind, 0, 0)

    def _handle_cobot_command(self, command: str) -> None:
        """수동 제어 화면의 명령을 robot/dashboard/* 서비스로 보낸다."""
        if command == "home":
            # 메인 화면 '로봇 홈'과 같은 규칙 — 로봇이 동작 중이면 무시한다.
            self._request_home()
            return
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
        # 작업 속도(movel v)는 운영 기준상 100 mm/s 를 넘길 수 없다(가속도 400 mm/s^2). 로봇
        # 태스크도 같은 값으로 자르지만, 넘는 값을 아예 보내지 않는다.
        speed = values.get(CobotSettingsScreen.SPEED_FIELD)
        if speed is not None and self.ros_status.writable("linear_speed"):
            capped = min(int(speed), MAX_LINEAR_SPEED_MM_S)
            if capped != int(speed):
                self.main_screen.show_activity(
                    f"작업 속도를 안전 상한 {MAX_LINEAR_SPEED_MM_S} mm/s 로 제한했습니다."
                )
            self.ros_status.send_value("linear_speed", capped)
            self.main_screen.set_base_speed(capped)

        # 속도 비율은 29999로 실시간 반영되는 경로를 탄다.
        ratio = values.get(CobotSettingsScreen.RATIO_FIELD)
        if ratio is not None:
            self._apply_speed_ratio(ratio, source="설정")

    # 위치 저장은 조그 화면에서, 나머지는 수동 제어 화면에서 요청한다.
    _JOG_COMMANDS = ("save_home_pose", "save_start_pose")

    #: 스캔을 띄우고 멈추는 명령. 실패가 메인 화면에도 보여야 한다.
    _SCAN_COMMANDS = ("play", "stop", "remote_control_on",
                      "load_mark_task", "load_scan_task", "home")

    def _show_command_result(self, name: str, success: bool, message: str) -> None:
        """명령 결과를 요청한 화면의 안내 문구로 보여준다."""
        text = message if success else f"실패: {message}"
        if name in self._JOG_COMMANDS:
            self.cobot_jog_screen.show_result(text)
        elif name in ("connect", "disconnect"):
            # 연결 설정 화면에서 누른 경우 결과가 거기 보여야 한다.
            self.screens["connection"].set_link_message(text)
            self.cobot_manual_screen.activity_label.setText(text)
        elif name in ("load_scan_task", "load_mark_task") and success:
            self.screens["cobot"].set_task_status(message)
            self.cobot_manual_screen.activity_label.setText(text)
        elif name in self._SCAN_COMMANDS:
            # 스캔을 띄우는 명령이다. 실패하면 **작업 중인 사람이 보는 곳**
            # 에도 띄운다 — 예전에는 코봇 수동 화면에만 남아서, 메인 화면
            # 에서는 "로봇 스캔을 시작합니다" 만 뜨고 로봇은 가만히 있는데
            # 이유를 알 수 없었다.
            self.cobot_manual_screen.activity_label.setText(text)
            if not success:
                self.main_screen.show_activity(f"로봇 {name} 실패: {message}")
                self.cobot_manual_screen.add_alarm(f"로봇 {name} 실패: {message}")
        elif name == "task_status":
            # _execute_dash_cmd가 "[task -s] Result: ..." 로 감싸 보내므로
            # 표시용으로는 결과값만 뽑아 보여준다.
            if success:
                text = message.split("Result: ", 1)[-1]
            self.screens["cobot"].set_task_status(text)
        else:
            self.cobot_manual_screen.activity_label.setText(text)

    # scan_state 의 4번째 값(레지스터 293) — 로봇의 발행 스레드가 주기마다
    # 1 씩 올리는 생존 카운터다. 태스크가 멈추면 이 값이 그대로 굳는다.
    # 그때 마지막 좌표를 화면에 남겨 두면 지금도 거기 있는 것처럼 보이므로,
    # 잠시 안 바뀌면 현재 위치를 대기 자리로 되돌린다.
    _ALIVE_INDEX = 3
    _ALIVE_STALL_LIMIT = 10        # 10Hz 기준 약 1 초

    def _handle_alive(self, values: list) -> None:
        """로봇이 좌표 발행을 멈췄는지 생존 카운터로 본다."""
        if len(values) <= self._ALIVE_INDEX:
            return
        alive = int(values[self._ALIVE_INDEX])
        if alive != getattr(self, "_last_alive", None):
            if getattr(self, "_last_alive", None) is not None:
                self._position_stale = False    # 다시 좌표를 낸다
            self._last_alive = alive
            self._alive_stall = 0
            return
        self._alive_stall = getattr(self, "_alive_stall", 0) + 1
        if self._alive_stall == self._ALIVE_STALL_LIMIT:
            self._park_position()

    # scan_state(레지스터 290~299)의 10번째 값 — 센서판 probe_c/l/r 이
    # 벽 접촉을 못 찾고 halt() 하기 직전에 남긴다(config/modbus_registers.json
    # 참고). 0=정상, 그 외는 실패 코드.
    _PROBE_ERROR_INDEX = 9
    _PROBE_ERROR_MESSAGES = {
        1: ("E-PROBE-C", "센터 프로브 벽 접촉 실패"),
        2: ("E-PROBE-L", "좌측(원점) 프로브 벽 접촉 실패"),
        3: ("E-PROBE-R", "우측 프로브 벽 접촉 실패"),
        4: ("E-ARC-ZERO", "호 길이/반지름이 0 — 작업 영역 값을 확인하세요"),
        # v5 (servoj 스캔) — 호를 도는 동안 눌림을 계속 확인한다.
        6: ("E-SCAN-PRESS", "스캔 중 접촉(눌림)을 유지하지 못해 멈췄습니다 — 보정 한계(press_max) 초과"),
        7: ("E-SCAN-IK", "스캔 호 경로에 역기구학 해가 없어 멈췄습니다 — 로봇 위치·자세를 확인하세요"),
        # v5 인터락 — 차량 고정 확인(레지스터 309)을 기다리다 시간이 넘었다.
        8: ("E-VEHICLE-HOLD", "차량 고정 확인을 못 받아 로봇이 멈췄습니다 — 아웃트리거 고정·차량 정지를 확인하세요"),
    }

    def _handle_probe_error(self, values: list) -> None:
        """작업면(벽) 감지 실패를 알람 목록 + MQTT evt/error 로 내보낸다.

        로봇이 halt() 전에 레지스터 299에 코드를 남기고, 다시 dus_init이
        돌 때만 0으로 되돌린다 — 그래서 값이 **바뀔 때만** 알린다(같은
        코드가 10Hz로 계속 들어와도 알람이 반복해서 쌓이지 않게).
        erut_session.raise_error()는 이미 있는 경로를 그대로 쓴다 — level이
        "stop"이면 로봇도 같이 세우고(halt()로 이미 서 있어 중복이지만
        멱등하다), MQTT evt/error 로 나간다.
        """
        if len(values) <= self._PROBE_ERROR_INDEX:
            return
        code = int(values[self._PROBE_ERROR_INDEX])
        prev_code = getattr(self, "_last_probe_error_code", 0)
        if code == prev_code:
            return
        self._last_probe_error_code = code

        if code == 0:
            if prev_code in self._PROBE_ERROR_MESSAGES:
                err_code, _ = self._PROBE_ERROR_MESSAGES[prev_code]
                self.erut_session.raise_error({
                    "code": f"{err_code}-CLEAR", "message": "작업면 감지 실패 해제",
                    "level": "warning", "recovery": "auto",
                })
            return

        err_code, message = self._PROBE_ERROR_MESSAGES.get(
            code, (f"E-PROBE-{code}", "작업면 감지 실패"))
        self.main_screen.show_activity(f"{message} (레지스터 299={code})")
        self.cobot_manual_screen.add_alarm(message)
        self.erut_session.raise_error({
            "code": err_code, "message": message,
            "level": "stop", "recovery": "manual",
            "detail": "로봇이 계산된 위치에서 벽 접촉을 찾지 못해 정지했습니다.",
        })

    # 스캔 진행 상태(290)에서 "원점 도착, 시작 신호 대기"를 뜻하는 값.
    # dus_goto_zero.script 가 세우고, 레지스터 267 에 1 이 들어오면 푼다.
    _SCAN_STATE_INDEX = 0
    _STATE_AT_ORIGIN = 7
    #: 로봇이 차량 고정 확인(레지스터 309)을 기다리는 중 (v5 dus5_init/goto_zero).
    _STATE_WAIT_VEHICLE = 14

    def _handle_vehicle_wait(self, values: list) -> None:
        """로봇이 차량 고정을 기다리며 서 있으면 한 번 알린다.

        이 상태로 오래 서 있으면 원인을 알기 어렵다 — 아웃트리거가 안
        고정됐거나, 차량 상태가 안 들어와 RCS 가 309 를 못 세운 것이다.
        """
        if not values:
            return
        waiting = int(values[self._SCAN_STATE_INDEX]) == self._STATE_WAIT_VEHICLE
        if waiting == getattr(self, "_robot_waiting_vehicle", False):
            return
        self._robot_waiting_vehicle = waiting
        if waiting:
            self.main_screen.show_activity(
                "로봇이 차량 고정 확인을 기다립니다 — 아웃트리거 고정·차량 정지를 확인하세요.")

    def _handle_origin_wait(self, values: list) -> None:
        """로봇이 원점에 도착해 멈춰 서면 ERUT 에 알린다.

        프로브가 벽에 제대로 붙었는지는 로봇이 알 수 없어서, 원점에서
        한 번 멈춰 세우고 ERUT 의 확인을 받는다. 10Hz 로 같은 값이 계속
        들어오므로 **상태가 바뀔 때만** 알린다.
        """
        if not values:
            return
        # 원점 대기는 **스캔 구간이 도는 중일 때만** 뜻이 있다. 로봇은 멈춰도
        # 290 = 7 을 그대로 들고 있어서, 이걸 안 거르면 abort·정지 뒤에도
        # 대기로 다시 잡혀 evt/ready 가 또 나간다.
        waiting = (int(values[self._SCAN_STATE_INDEX]) == self._STATE_AT_ORIGIN
                   and self.sequencer.state is SequencerState.SCANNING)
        if waiting == getattr(self, "_origin_waiting", False):
            return
        self._origin_waiting = waiting
        if self.mqtt_server is not None:
            self.mqtt_server.publish_probe_gate(
                waiting, self.sequencer.current_cell())
        if not waiting:
            self.erut_session.clear_at_origin()
            return
        self.main_screen.show_activity(
            "로봇이 원점에 도착했습니다 — 프로브 확인(ERUT 의 작업 시작 또는"
            " MQTT probe_ack)을 기다립니다.")
        self.erut_session.notify_at_origin()

    def _handle_probe_ack(self, payload: dict) -> None:
        """바깥(MC)에서 온 프로브 눌림 확인을 받아 로봇을 풀어 준다.

        `pressed` 가 참이면 스캔으로 넘어가고, 거짓이면 풀지 않고 알람만
        남긴다 — 프로브가 안 붙은 채로 훑으면 검사가 성립하지 않는다.
        `reason` 을 같이 주면 그대로 보여 준다.
        """
        pressed = payload.get("pressed")
        if isinstance(pressed, str):
            pressed = pressed.strip().lower() in ("true", "1", "ok", "yes")
        if not getattr(self, "_origin_waiting", False):
            self.main_screen.show_activity(
                "프로브 확인을 받았지만 로봇이 원점 대기 중이 아닙니다 — 무시합니다.")
            return
        if not pressed:
            reason = str(payload.get("reason", "") or "프로브 눌림 확인 실패")
            self.cobot_manual_screen.add_alarm(f"프로브 확인 거부: {reason}")
            self.main_screen.show_activity(
                f"프로브 확인이 거부되었습니다 — {reason}. 원점에서 계속 대기합니다.")
            return
        self.main_screen.show_activity("프로브 눌림 확인을 받았습니다 — 적심 후 스캔.")
        self._release_scan_gate()

    def _release_scan_gate(self) -> bool:
        """스캔 시작 허가(레지스터 267 = 1)를 로봇에 보낸다.

        로봇은 이 값을 보고 적심(비비기) -> 스캔으로 넘어가며, 통과하면서
        스스로 0 으로 되돌린다 — 다음 사이클에 지난 허가가 남아 확인 없이
        통과하는 것을 막기 위해서다.
        """
        sent = self.ros_status.send_value("scan_go", 1)
        if sent:
            self._origin_waiting = False
            self.erut_session.clear_at_origin()
            self.main_screen.show_activity("스캔 시작 허가를 보냈습니다 — 적심 후 스캔.")
        else:
            self.cobot_manual_screen.add_alarm(
                "스캔 시작 허가를 보내지 못했습니다 — 로봇 연결을 확인하세요.")
        return sent

    def _handle_robot_alarm(self, text: str) -> None:
        """로봇(30001 포트) 실시간 알람 스트림을 MQTT evt/error 로도 내보낸다.

        ROS 쪽 AlarmManager가 이미 짧은 시간 창(기본 2초) 안에서만 중복을
        누르고 그 창이 지나 다시 발생한 알람은 새로 흘려보내므로, 여기서는
        받은 그대로 한 번 raise_error 한다. probe_error(레지스터 299)와
        달리 이 알람은 레지스터에 남아 계속 폴링되는 '상태'가 아니라
        그 순간 로봇이 찍어 보낸 로그성 이벤트라서 "-CLEAR" 짝이 따로
        필요 없다 — cobot_manual_screen.add_alarm 쪽 목록 표시와는 별개로,
        여기서는 MQTT 전달만 담당한다.
        """
        self.erut_session.raise_error({
            "code": "E-ROBOT-ALARM", "message": text,
            "level": "warning", "recovery": "manual",
        })

    def _reset_alarms(self) -> None:
        """화면에 남은 알림·알람을 지우고, 걸려 있던 장애도 해제한다.

        예전에는 장애 통보가 뜨면 지울 방법이 없어 문구가 계속 남았다.
        ERUT 가 `req/reset` 을 보냈을 때와 같은 해제를 운영자가 화면에서도
        할 수 있게 한다 — 현장에서 스테이션 조작을 기다릴 수 없다.
        """
        cleared = self.erut_session.clear_errors()
        for code in cleared:
            self.data_recorder.log_event("해제", "운영자 알람 리셋", code=code)
        self.cobot_manual_screen.set_alarms([])
        self.main_screen.clear_activity()
        if cleared:
            self.main_screen.show_activity(f"장애 해제: {', '.join(cleared)}")

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
        # 기록을 보여 주는 화면은 열 때마다 파일을 다시 읽는다.
        if key in ("errors", "logs"):
            screen.refresh()

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

    # ------------------------------------------------------------ 운영 기록
    # 시스템 설정의 두 항목 이름. 저장 위치 아래에 기록을 남기고, 보존
    # 기간이 지난 파일은 지운다(services/data_recorder.py).
    DATA_DIR_FIELD = "데이터 저장 위치"
    RETENTION_FIELD = "로그 보존 기간"

    def _setup_data_recorder(self) -> None:
        """네 가지 운영 기록(작업·스캔 좌표·알람이벤트·통신)을 잇는다."""
        system = self.screens["system"]
        self.data_recorder = rec = DataRecorder(
            root_value=system.field(self.DATA_DIR_FIELD).text(),
            retention_days=system.field(self.RETENTION_FIELD).value(),
            parent=self,
        )
        rec.problem.connect(self._show_recorder_problem)
        # 알람·이벤트
        self.main_screen.activity_shown.connect(lambda text: rec.log_event("정보", text))
        self.cobot_manual_screen.alarm_added.connect(lambda text: rec.log_event("알람", text))
        self.erut_session.error_published.connect(self._record_erut_error)
        self.erut_session.message_published.connect(
            lambda code, text: rec.log_event("알림", text, code=code))
        # 통신 (MQTT 수신 스레드에서도 불린다 — 기록기가 잠금으로 막는다)
        self.erut.traffic = lambda d, topic, payload: rec.comms(d, "ERUT", topic, payload)
        self.mqtt_server.traffic = self._record_mc_traffic
        # 작업기록·스캔 좌표
        self.sequencer.cell_status_changed.connect(self._record_cell_status)
        self.sequencer.state_changed.connect(self._record_sequencer_state)
        self.mark_runner.finished.connect(rec.mark_finished)
        self.ros_status.scan_state_changed.connect(
            lambda values: rec.set_robot_state(int(values[0])) if values else None)
        self.ros_status.tcp_pose_zero_changed.connect(rec.scan_point)
        # 설정 화면에서 저장하면 바로 반영한다.
        system.save_requested.connect(self._apply_system_settings)
        system.field(self.DATA_DIR_FIELD).setToolTip(f"실제 저장 폴더: {rec.root}")
        rec.purge_old()

    def _apply_system_settings(self, scope: str, values: dict) -> None:
        """저장 위치·보존 기간을 기록기에 넣는다(불러올 때·저장할 때)."""
        if scope != "system" or not hasattr(self, "data_recorder"):
            return
        rec = self.data_recorder
        before = rec.root
        if self.DATA_DIR_FIELD in values:
            rec.set_root(str(values[self.DATA_DIR_FIELD]))
        if self.RETENTION_FIELD in values:
            rec.set_retention_days(values[self.RETENTION_FIELD])
        self.screens["system"].field(self.DATA_DIR_FIELD).setToolTip(
            f"실제 저장 폴더: {rec.root}")
        if rec.root != before:
            self.main_screen.show_activity(f"운영 기록 저장 위치: {rec.root}")

    def _record_mc_traffic(self, direction: str, topic: str, payload) -> None:
        # MC 접속은 우리가 내는 상태·응답 토픽도 구독한다 — 그 수신은 방금
        # 보낸 것이 되돌아온 것이라 두 번 적지 않는다.
        if direction == "수신" and topic in (*MqttTopics.STATUSES, *MqttTopics.RESPONSES):
            return
        self.data_recorder.comms(direction, "MC", topic, payload)

    # ------------------------------------------------------------ 차량
    #: 차량 주행 속도 [m/s]. SetJob.set_speed 로 싣는다.
    VEHICLE_SPEED_MPS = 0.2

    def _setup_vehicle(self) -> None:
        """ROS 차량 제어 노드와 붙는다(vehicle_interfaces). 없으면 더미로만 돈다."""
        self.vehicle = VehicleClient(self.ros_status, parent=self)
        self._vehicle_amr = VehicleAmrAdapter(self.vehicle, self.VEHICLE_SPEED_MPS, parent=self)
        self.amr.add_backend("vehicle", self._vehicle_amr)
        self.lift.add_backend("vehicle", VehicleLiftAdapter(self.vehicle, parent=self))
        self.outrigger.add_backend("vehicle", VehicleOutriggerAdapter(self.vehicle, parent=self))
        self._vehicle_hinted = False
        for motion in (self.amr, self.lift, self.outrigger):
            motion.problem.connect(self._on_vehicle_problem)
        self.vehicle.online_changed.connect(self._apply_vehicle_mode)
        self.vehicle.online_changed.connect(self._hint_vehicle_available)
        # 수동 제어 화면 — ManualCommand / RobotControl 로 보낸다.
        manual = self.screens["manual"]
        manual.manual_requested.connect(lambda fields: self.vehicle.manual(**fields))
        manual.stop_requested.connect(lambda: self.vehicle.control("STOP"))
        manual.reset_requested.connect(lambda: self.vehicle.control(reset=True))
        self.vehicle.status_changed.connect(manual.set_status)
        self.vehicle.command_result.connect(self._show_vehicle_command_result)
        # 목록에서 고르는 즉시 적용한다(저장은 값을 남길 뿐이다).
        self.screens["connection"].vehicle_mode_changed.connect(
            lambda _text: self._apply_vehicle_mode())
        self.sequencer.state_changed.connect(self._sync_manual_interlock)
        self.sequencer.state_changed.connect(self._vehicle_follow_pause)
        self._show_vehicle_link(False)      # 시작할 때부터 '더미'라고 보이게
        # 차량 고정 확인(레지스터 309)을 로봇에 계속 알린다 — 로봇은 이
        # 값이 1 이어야 움직인다(dus5_init / dus5_goto_zero).
        self._vehicle_ready_ticks = 0
        self._vehicle_ready_timer = QTimer(self)
        self._vehicle_ready_timer.setInterval(self.VEHICLE_READY_MS)
        self._vehicle_ready_timer.timeout.connect(self._push_vehicle_ready)
        self._vehicle_ready_timer.start()

    #: 차량 고정 확인을 다시 재는 주기 [ms].
    VEHICLE_READY_MS = 500
    #: 값이 그대로여도 다시 보내는 간격(위 주기의 배수). 로봇 노드가 다시
    #: 뜨면 레지스터가 0 으로 돌아가 있어 되풀이가 필요하다.
    VEHICLE_READY_REPEAT = 10

    def _vehicle_secured(self) -> bool:
        """차량 쪽이 굳어 있는가 — 로봇이 팔을 뻗어도 되는 상태인가.

        아웃트리거 고정 + 차량 정지 + 리프트 정지, 셋을 다 본다. 실제
        차량이면 차량이 주는 상태로, 더미면 어댑터 상태로 판단한다.
        """
        if any(m.moving for m in (self.amr, self.lift, self.outrigger)):
            return False
        if self.outrigger.position < 1:
            return False                    # 아웃트리거가 고정돼 있지 않다
        if self.amr.active != "vehicle":
            return True                     # 더미는 여기까지로 본다
        status = self.vehicle.last_status
        if not self.vehicle.online or not status:
            return False                    # 상태가 안 오면 확인할 수 없다
        return (status.get("hold") == "SET"
                and not int(status.get("error_code", 0) or 0)
                and status.get("state") in ("STOP", "HOLD"))

    def _push_vehicle_ready(self) -> None:
        self._vehicle_ready_ticks += 1
        force = self._vehicle_ready_ticks % self.VEHICLE_READY_REPEAT == 0
        self.ros_status.send_vehicle_ready(self._vehicle_secured(), force=force)

    def _vehicle_mode(self) -> str:
        """설정값 — "auto" / "dummy" / "vehicle"."""
        screen = self.screens["connection"]
        field = screen.field("차량 제어")
        return screen.VEHICLE_MODES.get(field.currentText(), "auto") if field else "auto"

    def _wanted_backend(self) -> str:
        """지금 써야 할 장비. 자동이면 차량 상태가 들어오는지로 고른다."""
        mode = self._vehicle_mode()
        if mode != "auto":
            return mode
        return "vehicle" if self.vehicle.online else "dummy"

    def _apply_vehicle_mode(self) -> None:
        """연결 설정의 '차량 제어'를 반영한다. 움직이는 중이면 다음에 바꾼다."""
        mode = self._wanted_backend()
        if self.amr.active == mode:
            self._show_vehicle_link(self.vehicle.online)
            return
        motions = (self.amr, self.lift, self.outrigger)
        # 셋이 늘 같은 쪽을 보게 한다 — 하나라도 움직이는 중이면 아무것도 안 바꾼다.
        if any(m.moving for m in motions) or self._job_running():
            self.main_screen.show_activity(
                "차량이 움직이거나 작업 중이라 차량 제어를 바꾸지 않았습니다 — 멈춘 뒤 다시 저장하세요.")
            return
        for m in motions:
            m.select(mode)
        auto = " (자동)" if self._vehicle_mode() == "auto" else ""
        if mode == "vehicle":
            note = "" if self.vehicle.available else " — ROS·vehicle_interfaces 가 없어 명령을 보낼 수 없습니다"
            self.main_screen.show_activity(
                f"차량 제어{auto}: 차량 노드로 움직입니다 ({self.vehicle.topic('robot_status')}){note}")
        else:
            self.main_screen.show_activity(
                f"차량 제어{auto}: 더미 — 차량·리프트·아웃트리거는 실제로 움직이지 않습니다.")
        self._show_vehicle_link(self.vehicle.online)

    def _show_vehicle_link(self, online: bool) -> None:
        # 상단 AMR 은 차량 상태가 실제로 들어오는지만 보여 준다.
        self.top_bar.badges["AMR"].set_connected(bool(online))
        vehicle = self.amr.active == "vehicle"
        manual = self.screens["manual"]
        if not vehicle:
            manual.set_available(False, "차량 제어가 '더미'입니다 — 연결 설정에서 'ROS 차량 노드'로 바꾸면 쓸 수 있습니다.")
        elif not online:
            manual.set_available(False, f"차량 상태({self.vehicle.topic('robot_status')})가 들어오지 않습니다 — 차량 제어 노드를 확인하세요.")
        else:
            manual.set_available(True)

    def _hint_vehicle_available(self, online: bool) -> None:
        """더미인데 차량 상태가 들어오면 한 번 알려 준다.

        차량 노드는 떠 있는데 RCS 가 더미인 채로 작업을 돌리면, 화면 순서만
        흐르고 차량은 가만히 있는다. 그 상황을 먼저 짚어 준다.
        """
        if not online or self.amr.active == "vehicle" or getattr(self, "_vehicle_hinted", False):
            return
        self._vehicle_hinted = True
        message = (f"차량 상태({self.vehicle.topic('robot_status')})가 들어오는데 차량 제어가 "
                   "'더미'로 고정돼 있습니다 — 연결 설정에서 '자동'이나 'ROS 차량 노드 고정'으로 바꾸세요.")
        self.main_screen.show_activity(message)
        self.cobot_manual_screen.add_alarm(message)

    def _show_vehicle_command_result(self, name: str, ok: bool, message: str) -> None:
        if not ok:
            self.main_screen.show_activity(f"차량 {name} 거절: {message}")

    def _vehicle_follow_pause(self, name: str) -> None:
        """작업 일시정지·재개를 달리는 차량에도 전한다(PAUSED / RUNNING).

        더미는 멈출 필요가 없지만 실제 차량은 일시정지 중에 계속 가면 안 된다.
        """
        if self.amr.active != "vehicle" or not self.amr.moving:
            self._vehicle_paused = False
            return
        if name == SequencerState.PAUSED.name:
            self._vehicle_paused = True
            self.vehicle.control("PAUSED")
        elif getattr(self, "_vehicle_paused", False):
            self._vehicle_paused = False
            self.vehicle.control("RUNNING")

    def _sync_manual_interlock(self, *_args) -> None:
        """로봇이 동작 중이거나 홈을 벗어나 있으면 수동 주행·리프트를 잠근다.

        태스크가 멈춰 있어도 팔이 벽 앞에 뻗어 있으면(홈 플래그 0) 차량을
        움직이면 안 된다. 잠겨 있으면 먼저 로봇을 홈으로 보낸다.
        """
        reason = self._robot_motion_block_reason()
        if not reason and self._job_running():
            reason = "작업이 진행 중입니다"
        self.screens["manual"].set_interlock(bool(reason), reason)

    def _on_vehicle_problem(self, message: str) -> None:
        """차량이 도착을 못 냈다 — 장애로 올린다(작업을 멈추고 로봇도 세운다)."""
        self.erut_session.raise_error({
            "code": "E-VEHICLE", "message": "차량 동작 실패", "level": "stop",
            "recovery": "manual", "detail": message,
        })
        self.cobot_manual_screen.add_alarm(f"차량: {message}")

    def _set_vehicle_totals(self, plan) -> None:
        """SetJob 의 total_distance / total_height (작업 전체 크기, m)."""
        try:
            cols, rows = int(plan.column_count), int(plan.row_count)
            w, h = float(plan.cell_width), float(plan.cell_height)
            px, py = float(plan.pitch_x or 0.0), float(plan.pitch_y or 0.0)
        except (AttributeError, TypeError, ValueError):
            return
        total_d = (max(cols - 1, 0) * max(w - px, 0.0) + w) / 1000.0
        total_h = (max(rows - 1, 0) * max(h - py, 0.0) + h) / 1000.0
        self._vehicle_amr.totals = (total_d, total_h)

    def _record_job(self, source: str, job_id: str, plan) -> None:
        self.data_recorder.begin_job(source, job_id, plan, nosensor=self._nosensor)
        self._set_vehicle_totals(plan)
        self._warn_vehicle_mode()

    def _warn_vehicle_mode(self) -> None:
        """작업을 시작할 때 차량이 실제로 붙어 있는지 알려 준다.

        더미인 채로 작업을 돌리면 화면 순서는 다 흐르는데 차량은 가만히
        있는다 — 무엇이 잘못됐는지 알기 어려우므로 시작할 때 못박아 둔다.
        """
        if self.amr.active != "vehicle":
            self.main_screen.show_activity(
                "차량 제어가 '더미'입니다 — 차량·리프트·아웃트리거는 실제로 움직이지 않습니다"
                " (연결 설정 > 차량 제어).")
            return
        if not self.vehicle.available:
            self.cobot_manual_screen.add_alarm(
                "차량 인터페이스를 쓸 수 없습니다 — ROS 워크스페이스를 소싱하고 RCS 를 다시 띄우세요"
                " (source install/setup.bash).")
        elif not self.vehicle.online:
            self.cobot_manual_screen.add_alarm(
                f"차량 상태({self.vehicle.topic('robot_status')})가 들어오지 않습니다 —"
                " 차량 제어 노드를 확인하세요. 이대로 두면 첫 동작에서 멈춥니다.")

    def _record_cell_status(self, label: str, status: str) -> None:
        if status == CellStatus.EXECUTING.value:
            self.data_recorder.cell_started(label)
        elif status == CellStatus.COMPLETED.value:
            self.data_recorder.cell_finished(label, "완료")

    def _record_sequencer_state(self, name: str) -> None:
        if name == SequencerState.STOPPED.name:
            self.data_recorder.job_stopped("중단")

    def _record_erut_error(self, code: str, message: str, level: str) -> None:
        if code.endswith("-CLEAR"):
            self.data_recorder.log_event("해제", message, code=code[:-len("-CLEAR")], level=level)
        else:
            self.data_recorder.log_event("장애", message, code=code, level=level)

    # ------------------------------------------------------------ 기록 화면
    def _setup_record_screens(self) -> None:
        errors, logs, modes = self.screens["errors"], self.screens["logs"], self.screens["modes"]
        errors.bind(self.data_recorder, self.erut_session.error_codes)
        errors.clear_requested.connect(self._clear_errors)
        self.data_recorder.event_logged.connect(errors.on_event_logged)
        self.erut_session.error_published.connect(errors.refresh_active)
        logs.bind(self.data_recorder)
        logs.system_settings_requested.connect(lambda: self.navigate("system"))
        modes.load_requested.connect(self._load_mode_slot)
        modes.save_requested.connect(self._save_mode_slot)
        modes.clear_requested.connect(self._clear_mode_slot)

    def _clear_errors(self, codes: list) -> None:
        """오류 로그 화면의 해제. 빈 목록이면 전체 해제(= 메인 화면 알람 리셋)."""
        if not codes:
            self._reset_alarms()
        else:
            for code in codes:
                if self.erut_session.clear_error(str(code)):
                    self.data_recorder.log_event("해제", "운영자가 오류 로그에서 해제", code=str(code))
            self.main_screen.show_activity(f"오류 해제: {', '.join(map(str, codes))}")
        self.screens["errors"].refresh_active()

    # ---- 운전 모드 슬롯 --------------------------------------------------------
    #: 슬롯에 넣는 폼 화면. 연결·시스템 설정은 장비 고유값이라 넣지 않는다.
    MODE_FORM_SCOPES = ("cobot", "ut")
    #: 폼에 있어도 슬롯에서 빼는 항목 — 작업 조건이 아니라 장비 연결값이다.
    MODE_SKIP_FIELDS = ("태스크 판", "UT 주소", "통신 포트")

    def _mode_snapshot(self) -> dict:
        """지금 작업 조건을 한데 묶는다(작업 영역·검사 대상·Cobot·UT·태스크)."""
        width, height, scan_h, overlap = self.main_screen.rect_view.work_area()
        area = {"width_mm": width, "height_mm": height, "scan_h_mm": scan_h,
                "overlap_mm": overlap, **self._work_area_extra}
        target = self.settings_service.known("inspection_target")
        diameter_m, height_m = self.main_screen.orbit_view.target_dimensions()
        target.update({"diameter_m": diameter_m, "height_m": height_m})
        snap = {"work_area": area, "inspection_target": target,
                "robot_task": {"nosensor": self._nosensor, "task_version": self._task_version}}
        for scope in self.MODE_FORM_SCOPES:
            values = self._settings_screens[scope].values()
            for key in self.MODE_SKIP_FIELDS:      # 태스크 판은 robot_task 에 있다
                values.pop(key, None)
            snap[scope] = values
        return snap

    def _save_mode_slot(self, slot: int, name: str) -> None:
        snap = self._mode_snapshot()
        snap["name"] = name
        snap["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._mode_slots[str(slot)] = snap
        self._mode_slots_touched = True
        self.settings_service.save("mode_slots", self._mode_slots)
        modes = self.screens["modes"]
        modes.set_slots(self._mode_slots)
        modes.show_status(f"슬롯 {slot}에 '{name}'을(를) 저장했습니다.", "StatusGood")
        self.data_recorder.log_event("정보", f"운전 모드 저장: 슬롯 {slot} '{name}'")

    def _clear_mode_slot(self, slot: int) -> None:
        self._mode_slots[str(slot)] = {}
        self._mode_slots_touched = True
        self.settings_service.save("mode_slots", self._mode_slots)
        modes = self.screens["modes"]
        modes.set_slots(self._mode_slots)
        modes.show_status(f"슬롯 {slot}을(를) 비웠습니다.")

    def _load_mode_slot(self, slot: int) -> None:
        """슬롯의 작업 조건을 적용하고 저장한다. 작업 중에는 받지 않는다."""
        modes = self.screens["modes"]
        if self._job_running():
            modes.show_status("작업 중에는 운전 모드를 불러올 수 없습니다 — 끝나거나 정지한 뒤 불러오세요.",
                              "StatusDanger")
            return
        snap = dict(self._mode_slots.get(str(slot)) or {})
        if not snap:
            modes.show_status(f"슬롯 {slot}은(는) 비어 있습니다.", "StatusWarn")
            return
        name = snap.get("name", f"슬롯 {slot}")
        # 1) 작업 영역·검사 대상 — 저장하고, 화면·로봇에 적용(로봇 전송은 기존 규칙대로)
        for scope in ("work_area", "inspection_target"):
            values = snap.get(scope)
            if values:
                self.settings_service.save(scope, dict(values))
                self._apply_stored_settings(scope, dict(values))
        # 2) 태스크 버전·판 — 화면만 먼저 맞춘다(불러오기는 맨 끝에 한 번)
        task = snap.get("robot_task") or {}
        cobot = self.screens["cobot"]
        if task:
            self._nosensor = bool(task.get("nosensor", False))
            cobot.set_nosensor(self._nosensor)
            cobot.set_task_version(str(task.get("task_version", DEFAULT_TASK_VERSION)))
            self._task_version = cobot.task_version()
        # 3) Cobot·UT 폼 — 저장 버튼을 누른 것과 같다(Cobot 은 속도가 로봇으로 간다)
        for scope in self.MODE_FORM_SCOPES:
            values = snap.get(scope)
            if values:
                screen = self._settings_screens[scope]
                screen.apply_values(dict(values))
                screen.save_requested.emit(scope, screen.values())
        # 4) 태스크 — 저장하고 경로를 넘기고, 한가하면 그 스캔 태스크를 불러온다
        if task:
            self._apply_task_choice(self._task_version, "운전 모드 태스크")
        modes.show_status(f"슬롯 {slot} '{name}'을(를) 불러왔습니다.", "StatusGood")
        self.main_screen.show_activity(f"운전 모드 '{name}'을(를) 불러왔습니다.")

    def _show_recorder_problem(self, message: str) -> None:
        self.main_screen.show_activity(f"운영 기록 문제: {message}")
        self.cobot_manual_screen.add_alarm(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        """창 종료 전에 MQTT 네트워크 루프와 ROS 구독을 정리한다."""
        self.mqtt_server.stop()
        self.ros_status.stop()
        self.data_recorder.close()
        self.screens["tpac_bridge"].shutdown()
        # 우리가 띄운 노드만 거둔다(따로 띄운 노드는 남의 것이다).
        self.robot_node.stop()
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
    install_wheel_guard(app)
    return app


def main() -> int:
    """Qt 이벤트 루프를 실행하고 프로세스 종료 코드를 반환한다."""
    if "--offscreen" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_application()
    window = OperatorWindow()
    window.show()
    return app.exec()
