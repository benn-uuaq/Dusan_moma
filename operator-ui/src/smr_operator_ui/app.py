"""애플리케이션 초기화, 최상위 화면 전환, 서비스 연결을 담당한다."""

from __future__ import annotations

from dataclasses import replace

import math
import os
import platform
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from importlib.resources import files
from math import ceil, pi
from typing import Any

from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtGui import QFontDatabase, QGuiApplication
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
from smr_operator_ui.screens.records_screens import POSE_NAMES
from smr_operator_ui.screens import (
    CobotJogScreen, CobotManualScreen, CobotSettingsScreen, ConnectionSettingsScreen,
    ErrorLogScreen, IOStatusScreen, LogFilesScreen, MainScreen, ManualScreen,
    ModeSlotsScreen, RunScreen, SettingsMenuScreen, SystemSettingsScreen,
    TpacBridgeScreen, VehicleSettingsScreen,
)
from smr_operator_ui.gc_guard import install_gc_guard
from smr_operator_ui.keypad import install_wheel_guard
from smr_operator_ui.state import CyclePhase
from smr_operator_ui.components.speed_bar import SPEED_MAX, SPEED_MIN
from smr_operator_ui.services import (
    DummyMotionAdapter,
    MarkRunner,
    ErutClient,
    ErutSession,
    GridPlan,
    InspectionSimulator,
    JobSequencer,
    RobotNodeSupervisor,
    RosStatusClient,
    SequencerState,
    SettingsService,
    cell_label,
)
from smr_operator_ui.services import calibration
from smr_operator_ui.services.data_recorder import DataRecorder
from smr_operator_ui.services.motion_adapters import SwitchableMotion
from smr_operator_ui.services.agv_node_supervisor import AgvNodeSupervisor
from smr_operator_ui.services.link_probe import LinkProbe, ping, probe_tcp
from smr_operator_ui.services.settings_repository_file import JsonFileSettingsRepository
from smr_operator_ui.services.vehicle_adapters import (
    VehicleAmrAdapter, VehicleFinishAdapter, VehicleLiftAdapter, VehicleOutriggerAdapter,
)
from smr_operator_ui.services.vehicle_client import PLC_COMM_LOST, VehicleClient, explain_reject
from smr_operator_ui.services.job_sequencer import (
    PROBE_COVERAGE_MM, SCAN_ROW_LIMIT, CellStatus, scan_row_heights,
)
from smr_operator_ui.services.erut_session import (
    SHAPE_CYLINDER,
    SHAPE_PLATE,
    CONTACT_LEAD_MS,
    HOME_BUSY_TEXT,
    MSG_AREA_APPLY_PENDING,
    MSG_ARC_LIMIT_CLAMPED,
    MSG_HOLDER_MISMATCH,
    SCAN_SPEED_MAX,
)
from smr_operator_ui.services.ros_status_client import TASK_STATE_NAMES
from smr_operator_ui.services.robot_vnc import open_robot_vnc
from smr_operator_ui.services.reference_poses import (
    delete_reference_pose,
    load_reference_poses,
    restore_reference_poses,
    save_reference_poses,
)
from smr_operator_ui.styles import load_stylesheet

# 「작업 속도」(스캔 외 이동 movel 속도, 레지스터 306)의 상한 [mm/s]. 로봇 안전 설정의
# TCP 속도 한계(maxTcpSpeed 0.25 m/s)와 같다 — 넘게 시키면 컨트롤러가 안전 정지한다.
# v6 태스크는 이 값을 스캔 외 이동(v_move)에 쓰고 같은 값으로 자른다. ㄹ자 스캔은 따로
# 100 mm/s(SCAN_SPEED_MAX)에 속도 비율을 곱해 간다(2026-11-04). v5 태스크는 306 을
# 벽 접근 · 이동 공용 속도(v_l)로 받아 100 mm/s 로 자른다.
MAX_LINEAR_SPEED_MM_S = 250
# 한 구간 세로의 상한 [mm]. 리프트를 한 높이에 세운 채 로봇 팔이 위아래로
# 훑을 수 있는 범위다(2026-10-06 결정). 넘는 구간은 받지 않는다 — ERUT 는 400
# area_height, RCS '검사 시작'은 시작 전에 막는다. 모재 전체 높이는 리프트로
# 구간을 올려 가며 채우므로 이 값과 상관없다.
MAX_AREA_HEIGHT_MM = 1200

# 로봇 태스크를 stop 한 뒤 play 하기까지 두는 간격. 컨트롤러가 태스크를
# 정리할 시간을 주지 않으면 play 가 거부된다.
ROBOT_RESTART_DELAY_MS = 1200
#: 스캔을 틀고 이만큼 지나도 로봇 태스크가 실행(500 = 1)으로 안 바뀌면 출발 실패로
#: 본다 [ms]. play 가 거부됐거나(태스크 파일 없음 · 로컬 제어) 로봇이 안 붙었을 때다.
#: 그대로 두면 ERUT 는 running · 진행률 0 으로 완료를 끝없이 기다린다(Q-29).
ROBOT_START_TIMEOUT_MS = 20_000

#: 기본 태스크 버전. Cobot 설정의 '태스크 선택'으로 바꾼다(dusan_v6 / v5 / v4).
DEFAULT_TASK_VERSION = "dusan_v6"


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
# 저장된 연결 설정이 끝내 안 오면 이만큼 기다린 뒤 기본값으로 ERUT 에 붙는다 [ms].
ERUT_START_FALLBACK_MS = 5000

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
    SequencerState.READY: CyclePhase.INSPECTING,         # 3 (로봇 시작 대기)
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


#: 1024x768(4:3) 화면의 글자·버튼·여백 배율(2026-10-16). app.qss 의 px 값은
#: 1280x720 시절 그대로 두고 여기서 한꺼번에 줄인다 — 글자 20px -> 16px,
#: 버튼 높이 64px -> 51px, 오른쪽 제어 패널 340px -> 272px, 속도 바 112px -> 90px.
#: 창이 커지면(전체화면 등) 이 값에 창 배율을 곱한다(OperatorWindow._apply_ui_scale).
UI_BASE_SCALE = 0.8


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


def _call_later(owner: QObject, delay_ms: int, slot) -> None:
    """잠시 뒤 한 번 부른다 — owner 의 자식 타이머로 건다.

    QTimer.singleShot 은 창이 닫혀도 남아 닫힌 뒤에 불릴 수 있다(시험에서
    드물게 프로세스가 죽었다). 자식 타이머는 창의 closeEvent 가 함께 세우고,
    owner 가 없어지면 같이 없어진다.
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(slot)
    timer.timeout.connect(timer.deleteLater)
    timer.start(int(delay_ms))


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
        # AMR 은 차량 상태(online)를 받는다. UT 표시는 실제 통신이 없어 뺐다
        # (2026-10-07 — 초음파 장비와는 TPAC 로 붙는다). ERUT 는 맨 끝에 둔다.
        # Cobot은 ros_status의
        # connected_changed 신호로 실제 상태를 받으므로 TPAC과 마찬가지로
        # "연결 안 됨"에서 시작해, 실제 연결이 확인돼야 초록으로 바뀐다
        # (버그 리포트: "Cobot 실제로 연결 안되어 있는데 연결됨으로 뜸").
        # AMR = 차량(주행 · 리프트 · 아웃트리거). PLC 통신은 차량(AMR) 노드가 맡는다 — PLC 표시는
        # 차량 상태로 본다: 상태가 안 오면 「모름」, 차량 노드가 오류 701(PLC_COMM_LOST)을 내면 「연결 실패」
        # (2026-11-20 다시 넣음. 11-17 에 뺀 것은 실제 신호 없이 늘 「연결됨」인 자리표시자였다).
        amr_badge = ConnectionBadge("AMR")
        self.badges["AMR"] = amr_badge
        layout.addWidget(amr_badge)
        plc_badge = ConnectionBadge("PLC", initial_connected=False)
        plc_badge.set_state("모름")
        self.badges["PLC"] = plc_badge
        layout.addWidget(plc_badge)
        cobot_badge = ConnectionBadge("Cobot", initial_connected=False)
        self.badges["Cobot"] = cobot_badge
        layout.addWidget(cobot_badge)
        # TPAC은 실제 외부 장비라 상태를 그대로 표시한다. 마찬가지로
        # 처음엔 "연결 안 됨"에서 시작해, 실제로 값을 읽어가야 초록으로 바뀐다.
        tpac_badge = ConnectionBadge("TPAC", initial_connected=False)
        self.badges["TPAC"] = tpac_badge
        layout.addWidget(tpac_badge)
        # ERUT — RCS 가 ERUT MQTT 에 붙어 있는가(외부 통신은 이것 하나다).
        erut_badge = ConnectionBadge("ERUT", initial_connected=False)
        self.badges["ERUT"] = erut_badge
        layout.addWidget(erut_badge)
        for badge in self.badges.values():
            for label in badge.findChildren(QLabel):
                self._register_scalable(label, 20)
        # 차량이 battery_pct 를 알려 주면 그 값(2026-11-17). 모르면 「-」.
        battery = QLabel("배터리  -")
        battery.setObjectName("Product")
        layout.addWidget(battery)
        self._register_scalable(battery, 20)
        self.battery_label = battery
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._update_clock()
        # 창이 뜨는 도중에 오는 첫 resizeEvent는 QSS 폰트 적용이 아직
        # 안 끝난 시점이라 sizeHint 계산이 살짝 부정확할 때가 있다.
        # 이벤트 루프가 레이아웃/스타일 적용을 마친 다음 한 번 더
        # 재보정해 첫 화면부터 정확히 맞게 한다.
        _call_later(self, 0, self._rescale_to_fit)

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

    #: 요일 표시. strftime 의 %a 는 PC 지역 설정마다 다르게 나와 고정해 둔다.
    _WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def _update_clock(self) -> None:
        """운영자에게 표시되는 현재 날짜와 시각을 갱신한다.

        한글은 strftime 형식 문자열에 넣지 않는다 — Windows 의 strftime 은 형식을
        「유니코드를 지원하지 않는 프로그램용 언어」(시스템 로캘) 코드페이지로 바꾸는데,
        그게 한국어가 아닌 PC 에서는 「날」을 못 바꿔 RCS 가 켜지지도 않았다
        (UnicodeEncodeError: 'locale' codec, 2026-10-27 다른 PC 에서 확인).
        """
        now = datetime.now()
        self.clock.setText(f"날짜  {now:%Y-%m-%d} ({self._WEEKDAYS[now.weekday()]})\n"
                           f"시간  {now:%H:%M:%S}")


def _saved_connection_values() -> dict:
    """저장된 연결 설정을 바로 읽는다(화면 설정 읽기는 비동기라 ROS · 노드보다 늦다). 못 읽으면 빈 dict.

    DB 저장소(SMR_DATABASE_URL)를 쓰면 읽지 않는다.
    """
    if os.environ.get("SMR_DATABASE_URL", ""):
        return {}
    try:
        return dict(JsonFileSettingsRepository().load("connection") or {})
    except Exception:  # noqa: BLE001 — 설정을 못 읽어도 RCS 는 뜬다
        return {}


def saved_robot_endpoint() -> tuple[str, int] | None:
    """저장된 협동로봇 IP · Modbus 포트 — 로봇 제어 노드를 처음부터 그 주소로 띄운다(2026-11-24)."""
    values = _saved_connection_values()
    ip = str(values.get("협동로봇 IP", "") or "").strip()
    if not ip:
        return None
    try:
        port = int(values.get("Modbus 포트") or 502)
    except (TypeError, ValueError):
        port = 502
    return ip, port


def apply_ros_domain_setting() -> str:
    """연결 설정의 「ROS 도메인 ID」(0~232)를 ROS 가 뜨기 전에 ROS_DOMAIN_ID 로 넣는다(2026-11-24).

    설정 읽기는 비동기라 ROS 보다 늦다 — 그래서 설정 파일에서 바로 읽는다. 비어 있으면 환경(셸의 export)
    그대로다. DB 저장소(SMR_DATABASE_URL)를 쓰면 읽지 않는다. 넣은 값(없으면 빈 글자)을 돌려준다.
    """
    values = _saved_connection_values()
    raw = str(values.get("ROS 도메인 ID", "") or "").strip()
    if raw.isdigit() and 0 <= int(raw) <= 232:
        os.environ["ROS_DOMAIN_ID"] = raw
        return raw
    return ""


#: Fast DDS 전송 설정(한 데이터그램 1400 B + 공유 메모리) — WSL 미러 네트워크에서 같은 PC 의 노드끼리 통하게.
DDS_PROFILE = Path(__file__).resolve().parent / "config" / "fastdds_profile.xml"


def apply_dds_profile() -> str:
    """ROS 가 뜨기 전에 Fast DDS 전송 설정을 건다(2026-11-24). 건 파일 경로(안 걸었으면 빈 글자)를 돌려준다.

    WSL 미러 네트워크에서 127.0.0.1 로 가는 1.4 KB 넘는 UDP 가 사라져 RCS 가 로봇 · AGV 노드를 못 봤다
    (config/fastdds_profile.xml 설명). 셸에서 이미 다른 파일을 걸었거나 Fast DDS 가 아닌 RMW 를 쓰면 손대지 않는다.
    RCS 가 띄우는 로봇 · AGV 제어 노드도 이 환경을 물려받는다.
    """
    if os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE", ""):
        return ""
    if os.environ.get("RMW_IMPLEMENTATION", "") not in ("", "rmw_fastrtps_cpp"):
        return ""
    if not DDS_PROFILE.is_file():
        return ""
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(DDS_PROFILE)
    return str(DDS_PROFILE)


class OperatorWindow(QMainWindow):
    """화면 스택을 소유하고 UI·시뮬레이터·설정 서비스를 조정한다."""

    # 1024x768(4:3)이 창의 최소 크기이자 UI 배율의 기준이다(2026-10-16, 예전
    # 1280x720). 기준 크기에서 글자·버튼은 UI_BASE_SCALE 배다. 전체화면 등으로
    # 창이 커지면 폭/높이 중 더 여유 없는 쪽 비율로 전체 QSS를 같이 키운다
    # ("전체 화면과 작은 화면 비율이 유지가 되었으면 좋겠음").
    _UI_SCALE_REFERENCE_W = 1024
    _UI_SCALE_REFERENCE_H = 768
    # 창이 커진 만큼 글자·버튼을 그대로 키우면(배율 = 창 배수) 전체화면에서
    # 너무 커진다 — 1920x1080 이면 1.5배라 글자가 눈에 띄게 굵어졌다. 커지긴
    # 하되 창 배수보다 완만하게 따라가도록 눌러 준다.
    _UI_SCALE_DAMPING = 0.45
    _UI_SCALE_MAX = 1.35
    # 창 안쪽이 기준보다 작으면(제목 표시줄 · 작업 표시줄을 빼면 1024x768 이 안 남는
    # 화면) 그 비율만큼 그대로 줄여 화면 아래가 잘리지 않게 한다(2026-11-03). 글자가
    # 읽기 어렵게 작아지지 않도록 기준의 이 비율까지만 줄인다.
    _UI_SCALE_MIN = 0.8
    # 창 안쪽의 최소 크기. 모든 화면이 잘리지 않는 것을 재서 정했다 — 1024x768 모니터에서
    # 최대화하면 안쪽이 1024 x 690~700 정도 남는다(Windows 작업 표시줄 · GNOME 상단 막대).
    # 폭은 테두리가 있는 창(1024 - 16)까지, 높이는 그보다 넉넉히 낮게.
    _MIN_WIDTH = 1008
    _MIN_HEIGHT = 640
    # 창 크기(resize)는 안쪽 크기다. 제목 표시줄(최소화 · 전체화면 · 닫기)과 테두리가
    # 이만큼 더 붙는다고 보고, 화면이 그만큼을 못 담으면 최대화로 띄운다(show_fitted).
    _FRAME_ALLOWANCE_W = 16
    _FRAME_ALLOWANCE_H = 48

    def __init__(
        self,
        *,
        start_ros: bool = True,
        start_erut: bool = True,
        restore_mode: bool | None = None,
    ) -> None:
        super().__init__()
        # 켤 때 마지막 운전 모드를 다시 불러오는가(2026-11-25). 기본은 실제 실행(ROS 켬)일 때만 —
        # 시험은 세션 공용 설정 파일을 쓰므로 앞 시험의 운전 모드가 뒤 시험에 끼어들지 않게.
        self._restore_mode_on_start = bool(start_ros) if restore_mode is None else bool(restore_mode)
        # 연결 설정의 「ROS 도메인 ID」 — ROS 가 뜨기 전에 환경에 넣는다(로봇 · AGV 노드도 물려받는다).
        self._ros_started = bool(start_ros)
        if start_ros:
            apply_ros_domain_setting()
            apply_dds_profile()
        # 입력값은 클릭해서 키패드·키보드로만 바꾼다 — 휠로는 안 바뀐다.
        install_wheel_guard()
        # 앱 전체 파이썬 필터가 이벤트마다 돌므로, 자동 GC 가 이벤트 전달 도중에 Qt 객체를
        # 지우지 못하게 GUI 스레드 타이머에서만 거둔다(gc_guard — 시험이 드물게 죽던 원인).
        install_gc_guard()
        self.setWindowTitle("3S-Robotics | SMR Operator Console")
        self.resize(self._UI_SCALE_REFERENCE_W, self._UI_SCALE_REFERENCE_H)
        # 최소 크기를 1024x768 로 묶으면 그 크기의 모니터에서는 제목 표시줄 · 작업
        # 표시줄만큼 아래가 화면 밖으로 잘렸다. 줄어든 만큼 UI 배율을 낮춰 맞춘다.
        self.setMinimumSize(self._MIN_WIDTH, self._MIN_HEIGHT)
        self._base_qss = load_stylesheet()
        # 지금 적용된 배율. create_application 이 기준 배율로 QSS 를 걸어 둔다.
        self._current_ui_scale = UI_BASE_SCALE
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
            "system": SystemSettingsScreen(),
            "cobot": CobotSettingsScreen(), "vehicle": VehicleSettingsScreen(),
            "errors": ErrorLogScreen(),
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

        # '검사 시작'은 실제 순회를 시작한다(차량·
        # 리프트·로봇). 예전에는 데모 사이클(simulator.start_cycle)에만 이어져
        # 안전 순서 표시만 돌고 장비는 하나도 움직이지 않았다.
        self.main_screen.start_requested.connect(self._start_inspection)
        self.main_screen.measure_requested.connect(self._start_local_calibration)
        self.main_screen.pause_requested.connect(self._toggle_pause)
        self.main_screen.stop_requested.connect(self._stop_inspection)
        self.main_screen.home_requested.connect(self._request_home)
        self.main_screen.rest_requested.connect(self._request_rest)
        self.main_screen.alarm_reset_requested.connect(self._reset_alarms)
        self.main_screen.settings_requested.connect(lambda: self.navigate("settings"))
        self.main_screen.target_dimensions_changed.connect(self._on_target_dimensions)
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
        # 팔 받침대 상태(레지스터 360, 2026-11-26) — 0 모름 · 1 올리는 중 · 2 받침대 위 · 3 되돌리는 중 · 4 ready.
        self._arm_park = 0
        # 홈 명령이 받침대 되돌리기 태스크로 갔다 — 끝나면 스캔 태스크를 다시 불러 둔다.
        self._unpark_sent = False
        # 받침대 거치(주행 전 자동 · 「로봇 거치」)를 보냈다.
        self._rest_sent = False
        # 로봇 태스크가 끝나기를 기다리는 차량·리프트 이동. (문구, 실행 함수, 종류 drive · lift · robot)
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
        # 지금 운전 모드 · 지난번 운전 모드(2026-11-25). 켤 때 설정을 다 불러온 뒤 지난번 운전 모드를 다시 적용한다.
        self._current_mode: dict | None = None
        self._last_mode: dict | None = None
        startup_scopes = (*self._settings_screens.keys(), "inspection_target",
                          "work_area", "robot_task", "mode_slots", "scan_speed", "mode_current",
                          "tpac_bridge")
        self._initial_scopes = set(startup_scopes)
        self.settings_service.loaded.connect(lambda scope, _values: self._on_initial_scope(scope))
        self.settings_service.failed.connect(lambda scope, _message: self._on_initial_scope(scope))
        for scope in startup_scopes:
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
        # 전원·브레이크 카드의 단계 안내와 브레이크 해제 버튼(IDLE 에서만).
        self.ros_status.robot_mode_changed.connect(self.cobot_manual_screen.set_robot_mode)
        self.ros_status.connected_changed.connect(
            lambda connected: None if connected else self.cobot_manual_screen.set_robot_mode(-1, ""))
        self.ros_status.control_method_changed.connect(
            lambda _code, name: self.cobot_manual_screen.apply_status({"control_method": name})
        )
        self.ros_status.operation_mode_changed.connect(
            lambda _code, name: self.cobot_manual_screen.apply_status({"operation_mode": name})
        )
        self.ros_status.alarm_received.connect(lambda text: self._add_alarm("E9201", text))
        self.ros_status.alarm_received.connect(self._handle_robot_alarm)
        self.ros_status.joint_position_changed.connect(self._remember_joint)
        self.ros_status.command_result.connect(self._show_command_result)
        self.ros_status.connected_changed.connect(self.cobot_manual_screen.set_connected)
        self.ros_status.connected_changed.connect(self._restore_robot_settings)
        # 상단 바 Cobot 배지도 같은 신호로 실제 연결 여부를 반영한다
        # (이전엔 항상 "연결됨"으로 고정된 자리표시자였다).
        self.ros_status.connected_changed.connect(self._show_cobot_link)
        # 로봇 안전정지 — 화면에만 보인다(외부로 내보내지 않는다, 2026-11-17).
        self.ros_status.safety_stop_changed.connect(self._on_robot_safety_stop)
        self.ros_status.estop_changed.connect(self._on_robot_estop)
        # 안전정지 · 안전재설정 입력(Config IO CI0~CI3) — 모니터링(2026-11-17).
        self.ros_status.config_digital_in_changed.connect(self._on_config_inputs)
        # robot_control_node가 이미 로봇에 Modbus로 붙어 있는데 TPAC 화면에서
        # 또 수동으로 연결을 누르게 하는 건 불합리하다 — 그 노드의 연결
        # 여부를 그대로 따라가게 한다. "외부에 제공" 서버는 로봇 읽기가
        # 성공하면 TpacBridgeScreen 안에서 알아서 켠다.
        tpac_screen = self.screens["tpac_bridge"]
        self.ros_status.connected_changed.connect(tpac_screen.on_robot_link_changed)
        tpac_screen.tpac_link_changed.connect(self.top_bar.badges["TPAC"].set_connected)
        # TPAC 주소 배치(ELITE / UR 매핑 · 포즈 · 평면 · Odometer · Cscan 주소)는 바꾸면 저장한다(2026-11-25).
        tpac_screen.server_settings_changed.connect(
            lambda values: self.settings_service.save("tpac_bridge", dict(values)))
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
        # ERUT 접속 · 해제(2026-11-26) — 해제하고 값을 바꾼 뒤 다시 접속한다.
        conn_screen.erut_connect_requested.connect(self._erut_connect_now)
        conn_screen.erut_disconnect_requested.connect(self._erut_disconnect_now)
        # 로봇 연결 명령이 실패하면 「연결 중」을 푼다(자동 재시도는 다시 건다).
        self.ros_status.command_result.connect(self._on_robot_connect_result)
        conn_screen.vnc_requested.connect(self._open_robot_vnc)
        self.ros_status.connected_changed.connect(conn_screen.set_link_state)
        self.ros_status.connected_changed.connect(self._announce_robot_link)
        self.ros_status.link_detail_changed.connect(self._on_robot_link_detail)
        self.cobot_jog_screen.jog_pressed.connect(self._send_jog)
        self.cobot_jog_screen.jog_released.connect(self._stop_jog)
        self.cobot_jog_screen.command_requested.connect(self._save_reference_pose)
        self.cobot_jog_screen.move_requested.connect(self._goto_reference_pose)
        self.cobot_jog_screen.delete_requested.connect(self._delete_reference_pose)
        self.cobot_manual_screen.command_requested.connect(self._handle_cobot_command)
        self.screens["cobot"].save_requested.connect(self._send_linear_speed)
        self.screens["vehicle"].save_requested.connect(self._on_vehicle_settings_saved)
        self.screens["cobot"].eoat_changed.connect(self._apply_eoat_choice)
        self.screens["cobot"].task_refresh_requested.connect(
            lambda: self.ros_status.call_command("task_status")
        )
        # 화면을 열 때마다 손으로 새로고침을 누르게 하는 대신, 로봇이
        # 연결될 때 한 번 자동으로 물어 채워 둔다.
        self.robot_node = RobotNodeSupervisor(self)
        self.robot_node.activity.connect(self.main_screen.show_activity)
        self.robot_node.duplicate_nodes.connect(self.cobot_manual_screen.add_alarm)
        self.ros_status.connected_changed.connect(
            lambda connected: self.ros_status.call_command("task_status") if connected else None
        )
        self.ros_status.connected_changed.connect(self._update_jog_enabled)
        # 작업 영역을 실시간으로 밀어 주려면 연결·태스크 상태를 알아야 한다.
        self.ros_status.connected_changed.connect(self._on_robot_link_changed)
        self.ros_status.task_state_changed.connect(self._on_robot_task_state)
        self.ros_status.at_home_changed.connect(self._on_robot_at_home)
        self.ros_status.arm_park_changed.connect(self._on_arm_park)
        # I/O 화면은 모니터링만 한다(2026-11-12) — 밀착 센서 · 안전 입력은 레지스터 1(_on_config_inputs),
        # 차량 값은 차량 상태.
        self._update_jog_enabled(False)
        self.ros_status.speed_scale_changed.connect(self._show_speed_scale)
        self.ros_status.error_occurred.connect(self._show_ros_error)
        if start_ros:
            self.ros_status.start()
            # 프로그램 하나만 켜면 되도록 로봇 제어 노드도 UI 가 데리고 있는다.
            # 이미 떠 있으면(터미널에서 따로 띄웠거나) 그대로 쓴다. 연결 설정의 로봇 주소로 띄운다
            # (2026-11-24 — 주소 없이 띄워 노드가 기본값 192.168.227.134 에만 붙으려 했다).
            self._sync_cobot_endpoint()
            saved = saved_robot_endpoint()
            if saved:
                # 화면 설정은 아직 안 불러왔다(비동기) — 저장 파일의 주소로 띄운다.
                self.robot_node.robot_ip, self.robot_node.modbus_port = saved
            self.robot_node.start(already_running=self.ros_status.node_is_running())

        self._connect_sequencer()
        # 외부 통신은 ERUT(MQTT) 하나다 — 사내 MC MQTT 는 2026-10-07 에 뺐다.
        # 시험은 start_erut=False 로 네트워크를 열지 않는다.
        self._connect_erut(start_erut)
        # 운영 기록(작업·스캔 좌표·알람이벤트·통신)을 데이터 저장 위치에 남긴다.
        self._setup_data_recorder()
        # 오류 로그·로그 파일·운전 모드 화면을 기록기와 설정에 잇는다.
        self._setup_record_screens()
        # 상단바·요약 칸은 QSS 가 아니라 스스로 글자 크기를 잡는다 — 기준 배율을
        # 처음부터 넘겨 둔다(창이 기준 크기면 _apply_ui_scale 이 다시 부르지 않는다).
        self._apply_widget_scale(self._current_ui_scale)

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
        if raw >= 1.0:
            # 창 배수를 그대로 쓰지 않고 눌러서 반영한다(_UI_SCALE_DAMPING 참고).
            scale = min(1.0 + (raw - 1.0) * self._UI_SCALE_DAMPING, self._UI_SCALE_MAX)
        else:
            # 기준보다 작은 창은 그 비율 그대로 줄인다 — 눌러서 반영하면 내용이 창보다
            # 커져 아래가 잘린다(_UI_SCALE_MIN 참고).
            scale = max(raw, self._UI_SCALE_MIN)
        scale *= UI_BASE_SCALE
        if abs(scale - self._current_ui_scale) < 0.02:
            return
        self._current_ui_scale = scale
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(_scale_stylesheet(self._base_qss, scale))
        self._apply_widget_scale(scale)

    def show_fitted(self) -> None:
        """제목 표시줄과 작업 표시줄까지 넣어 화면 안에 들어오게 띄운다.

        창 크기 1024x768 은 **안쪽** 크기라, 그 크기의 모니터에서는 제목 표시줄
        (최소화 · 전체화면 · 닫기)과 작업 표시줄만큼 아래가 화면 밖으로 잘렸다
        (2026-11-03). 쓸 수 있는 영역(작업 표시줄 제외)이 1024x768 과 창 테두리를
        못 담으면 최대화로 띄운다 — 창 전체가 그 영역에 들어오고, UI 는 남은 크기에
        맞춰 줄어든다(_apply_ui_scale). 여유가 있으면 1024x768 그대로 가운데에 둔다.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.show()
            return
        area = screen.availableGeometry()
        need_w = self._UI_SCALE_REFERENCE_W + self._FRAME_ALLOWANCE_W
        need_h = self._UI_SCALE_REFERENCE_H + self._FRAME_ALLOWANCE_H
        if area.width() < need_w or area.height() < need_h:
            self.showMaximized()
            return
        self.move(area.x() + (area.width() - need_w) // 2,
                  area.y() + (area.height() - need_h) // 2)
        self.show()

    def _apply_widget_scale(self, scale: float) -> None:
        """QSS 밖에서 글자 크기를 직접 잡는 위젯에 배율을 넘긴다."""
        # 상단바는 배지 개수가 많아 넓은 화면에서도 자체적으로 다시 줄일
        # 필요가 있을 수 있어, 전체 QSS를 다시 적용한 *다음*에 불러야
        # 상단바의 세밀한 계산이 QSS 재적용으로 덮이지 않는다.
        self.top_bar.set_global_scale(scale)
        # 요약 칸 값들도 같은 배율을 받는다 — 스타일시트가 키운 만큼
        # 기준을 올려야 전체화면에서 나머지 글자와 어울린다.
        self.main_screen.set_global_scale(scale)

    def _connect_erut(self, start: bool) -> None:
        """ERUT 표준 인터페이스(if-0.4)를 로봇·차량과 잇는다.

        배터리·충전과 캘리브레이션의 총 둘레는 차량 자료가 아직 없어 싣지
        않는다(docs/todo.md). 나머지는 실제 장비가 움직인다.
        """
        self.erut_session = ErutSession(self.erut, self.sequencer, parent=self)
        self.erut_session.activity.connect(self.main_screen.show_activity)
        self.erut.activity.connect(self.main_screen.show_activity)
        self.erut.error_occurred.connect(self.main_screen.show_activity)
        # 상단 ERUT 표시 — ERUT MQTT 에 붙었는가(접속 ID 충돌이면 그 사유).
        self.erut.connected_changed.connect(self._show_erut_link)
        self.erut.link_halted.connect(self._show_erut_link)
        self.erut.link_halted.connect(self.cobot_manual_screen.add_alarm)
        self._show_erut_link()

        # ERUT 요청 → 로봇 (진짜)
        self.erut_session.job_requested.connect(self._start_erut_job)
        self.erut_session.pause_requested.connect(self.sequencer.pause)
        self.erut_session.robot_stop_requested.connect(self._stop_robot_scan)
        # 가상 차량·리프트 값을 ERUT 응답에 실어 보낸다 (규격 탭5).
        self.erut_session.motion_state = self.motion_state
        # ERUT 의 홈 요청: 동작 중이면 세션이 409 + 사유로 거절하고,
        # 쉬고 있으면 여기로 온다.
        self.erut_session.robot_busy = self._robot_busy
        # 로봇 위치(호 위 거리·줄 높이·진행률)와 홈 플래그도 응답에 싣는다.
        self.erut_session.position_state = self._erut_position
        self.erut_session.max_area_width = self._max_area_width
        self.erut_session.max_area_height = lambda: float(MAX_AREA_HEIGHT_MM)
        self.erut_session.home_within_ms = self._home_within_ms
        self.erut_session.calibrate_within_ms = (
            lambda: int(self.screens["vehicle"].calibration_time_s() * 1000))
        # 차량 배터리 · 충전 — 차량이 알려 줄 때만 ERUT 에 싣는다(2026-11-17).
        self.erut_session.battery_state = self._battery_fields
        self.erut_session.area_diameter = (
            lambda: self.main_screen.orbit_view.target_dimensions()[0] * 1000.0)
        self.erut_session.side_pitch_blocker = self._side_pitch_blocker
        self.erut_session.motion_remain = self._segment_remain_mm
        self.erut_session.zone_reach_blocker = self._zone_reach_blocker
        self.erut_session.mark_waiting_point = lambda: self.mark_runner.waiting_point
        self.erut_session.pause_settled = self._pause_settled
        self.erut_session.pause_holds_position = self._pause_holds_position
        self.erut_session.errors_cleared.connect(self._home_after_reset)
        self.erut_session.fold_requested.connect(lambda: self._home_after_reset([]))
        self.erut_session.can_fold = self._can_fold
        self.erut_session.at_home = self._erut_at_home
        self.erut_session.home_requested.connect(self._send_home_for_erut)
        self.erut_session.job_dropped.connect(self._drop_erut_job)
        self.ros_status.scan_arc_changed.connect(self._on_scan_arc)
        self.ros_status.scan_state_changed.connect(self._track_erut_contact)
        # 로봇이 다음에 움직일 방향 → ERUT evt/motion (if-0.11). 홀더 채널 수는 견주기만.
        self.ros_status.scan_segment_changed.connect(self._on_scan_segment)
        self.erut_session.holder_reported.connect(self._check_holder_channels)
        self.ros_status.command_result.connect(self._on_home_command_result)
        self.ros_status.command_result.connect(self._on_pause_command_result)
        self.ros_status.command_result.connect(self._clear_home_pending_on_failure)
        self.ros_status.command_result.connect(self._note_unpark_reply)
        self.ros_status.command_result.connect(self._on_rest_command_result)
        # 캘리브레이션: 모재 치수를 받아 작업 영역을 고치고 로봇 3점 측정을 돌린다.
        self.erut_session.calibration_requested.connect(self._run_calibration)
        self.erut_session.calibration_stop_requested.connect(self._pause_calibration_lap)
        self.erut_session.calibration_resume_requested.connect(self._resume_calibration_lap)
        self.ros_status.probe_result_changed.connect(self._on_probe_result)
        self.ros_status.probe_poses_changed.connect(self._on_probe_poses)
        # 원점에서 멈춰 선 로봇을 ERUT 의 "작업 시작"으로 풀어 준다.
        self.erut_session.scan_go_requested.connect(self._release_scan_gate)
        # 마킹: 점마다 차량 -> 고정 -> 리프트 -> 로봇(마킹 태스크) -> 복귀.
        self.mark_runner = MarkRunner(
            self.amr, self.lift, self.outrigger, self.retractor,
            send_target=lambda u, v: self.ros_status.send_pose("mark_target", [u, v]),
            start_mark_task=self._start_mark_task,
            restore_scan_task=lambda: self.ros_status.call_command("load_scan_task"),
            # 로봇은 마킹 자리(290 = 11)에서 이게 올 때까지 선다(레지스터 278).
            release_hold=lambda: self.ros_status.send_value("mark_go", 1),
            parent=self,
        )
        # 점이 로봇 중심(호 가운데)에 오게 차량을 세운다 — 구역과 같은 환산.
        self.mark_runner.vehicle_x = self._vehicle_x
        self.mark_runner.activity.connect(self.main_screen.show_activity)
        self.mark_runner.finished.connect(self.erut_session.finish_mark)
        # 마커는 ERUT 것이다: 자리에 붙으면 evt/mark_ready, mark_next 가 오면 다음 점.
        self.mark_runner.point_reached.connect(self.erut_session.mark_point_ready)
        self.erut_session.mark_next_requested.connect(self.mark_runner.point_marked)
        self.erut_session.mark_stop_requested.connect(self._stop_erut_marking)
        self.erut_session.mark_pause_requested.connect(self.mark_runner.pause)
        self.erut_session.mark_resume_requested.connect(self._resume_erut_marking)
        self.mark_runner.robot_pause_requested.connect(self._pause_mark_robot)
        self.mark_runner.robot_resume_requested.connect(self._resume_mark_robot)
        # 다른 자리로 갈 때는 리프트를 내리고 아웃트리거를 푼 뒤에 달린다.
        self.mark_runner.prepare_drive = lambda drive: self._prepare_to_drive(
            drive, guard=self._drive_guard_for_mark)
        self.erut_session.mark_requested.connect(self._start_marking)
        self.ros_status.scan_state_changed.connect(self.mark_runner.handle_scan_state)
        self.erut_session.resume_requested.connect(self.sequencer.resume)
        self.erut_session.abort_requested.connect(self._abort_job)
        self.erut_session.reset_requested.connect(
            lambda: self._zero_scan_outputs("ERUT 초기화"))
        self.erut_session.reset_requested.connect(self._reset_vehicle_error)
        self.erut_session.target_reported.connect(self._keep_erut_target)
        # ERUT 스캔 속도는 ㄹ자 스캔에만 건다(307) — 스캔 밖 이동은 느려지지 않는다.
        self.erut_session.speed_requested.connect(self._apply_scan_speed_ratio)

        # 로봇 진행 → ERUT
        self.sequencer.cell_changed.connect(self.erut_session.on_cell_changed)
        # 구간을 다 돌면 로봇이 홈에 들어간 뒤(마지막 높이 구간이면 리프트 0 까지) ERUT 에 완료(2026-11-24).
        self.sequencer.job_complete.connect(self._on_zone_complete)

        # 저장된 연결 설정을 읽은 **뒤에** 붙는다(_apply_stored_settings ·
        # _show_settings_error). 설정 읽기는 비동기라, 여기서 바로 붙으면 기본값
        # 127.0.0.1:1883 에 먼저 붙었다가 다시 붙는다 — 그 PC 의 1883 에 다른
        # 브로커가 있으면 엉뚱한 접속 거부가 뜨고(2026-10-07 리눅스 PC rc=3),
        # 있으면 거기에 online 을 흘린다. 설정 읽기가 끝내 안 오면 기본값으로 붙는다.
        self._erut_start_pending = bool(start)
        if start:
            _call_later(self, ERUT_START_FALLBACK_MS, self._start_erut_link)

    def _show_erut_link(self, *_args) -> None:
        """상단 ERUT 표시를 지금 접속 상태로 맞춘다(2026-10-14 — 두 가지만).

          연결됨      ERUT MQTT 에 붙었다
          연결 안 됨  붙지 않았다(접속 정보 비어 있음 · 주소 · 계정 · 네트워크)
          ID 중복     같은 장치 ID 의 다른 RCS 와 번갈아 끊겨 다시 붙기를 멈췄다
        """
        conn = getattr(self, "screens", {}).get("connection")
        if conn is not None and hasattr(conn, "set_erut_state"):
            conn.set_erut_state(self.erut.enabled, self.erut.is_connected, self.erut.halted)
        badge = self.top_bar.badges.get("ERUT")
        if badge is None:
            return
        if self.erut.halted:
            badge.set_state("ID 중복", "StatusDanger")
        else:
            badge.set_connected(self.erut.is_connected)

    def _erut_connect_now(self) -> None:
        """연결 설정 「접속」 — 화면의 ERUT 값으로 지금 붙는다(저장 전 값이어도)."""
        self._sync_broker_endpoints()
        if getattr(self, "_erut_start_pending", False):
            self._start_erut_link()
        elif not self.erut.is_connected:
            self.erut.start()
        self._show_erut_link()

    def _erut_disconnect_now(self) -> None:
        """연결 설정 「해제」 — ERUT 에서 끊고 다시 붙지 않는다(「접속」을 누를 때까지). 붙는 중이어도 멈춘다."""
        self._erut_start_pending = False
        self.erut.stop()
        self.main_screen.show_activity("ERUT 접속을 해제했습니다 — 값을 바꾼 뒤 「접속」을 누르면 다시 붙습니다.")
        self._show_erut_link()

    def _start_erut_link(self) -> None:
        """ERUT 브로커 접속을 한 번만 연다(연결 설정을 읽은 뒤)."""
        if not getattr(self, "_erut_start_pending", False):
            return
        self._erut_start_pending = False
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
        if h <= 0:
            h = self.main_screen.rect_view.work_area()[2]
        return h

    def _fill_from_local_setup(self, plan):
        """ERUT 가 안 준 장비 값(반지름·EOAT)을 RCS 설정으로 채운다.

        기준 규격 20260812 의 prepare/start 에는 area 와 scan 만 있다.
        반지름(바깥 지름 / 2)·검사장비 종류는 prepare/start 에 없는 **우리 쪽 장비 값**이라
        ERUT 가 보내지 않는다. 그대로 두면 0 이 되어 로봇이 굽은 벽을
        평면으로 보고 직선으로 훑고(반지름 0), 프로브 커버도 모른다
        (EOAT 0). 게다가 _send_work_area 가 그 0 을 **설정에 저장**까지
        해서 다음 작업에서도 틀린 값이 남았다.
        """
        extra = self._work_area_extra
        changes = {}
        if plan.radius <= 0:
            # 벽 반지름은 검사 대상 **바깥 지름**의 절반 하나뿐이다(2026-10-19 — 두께는
            # 쓰지 않는다). 0 으로 보내면 로봇 태스크가 "ARC IS ZERO" 로 멈춘다 —
            # 평면 스캔은 없다.
            radius = self._outer_radius_mm()
            if radius > 0:
                changes["radius"] = radius
        if plan.eoat_probes <= 0 and float(extra.get("eoat_type", 0) or 0) > 0:
            changes["eoat_probes"] = int(float(extra["eoat_type"]))
        # 상승 높이는 설정이 아니다(2026-11-06) — ERUT 줄 간격은 좌·우 끝 이동높이
        # (if-0.12)로 로봇에 따로 가고(356 · 357), 홀더 유효 높이(263)는 장착 홀더의
        # 기본값(PROBE_COVERAGE_MM)이다.
        return replace(plan, **changes) if changes else plan

    def _start_erut_job(self, plan, prepare_only: bool = False) -> None:
        """ERUT start 로 구간을 시작한다 — 차량 이동 → 아웃트리거 고정 → 리프트 → 로봇.

        prepare 는 값만 확인하고 아무것도 움직이지 않으므로(2026-10-14) ERUT 는
        늘 `prepare_only=False` 로 부른다. 시퀀서의 READY 대기(hold_before_scan)는
        쓰지 않는다.
        """
        plan = self._fill_from_local_setup(plan)
        scan_h_mm = self._scan_band_mm(plan)
        # 로봇이 이번 구역에서 3점 측정을 시작할 때까지 290~299 는 지난 구역 값이다 —
        # 진행률 100 · 끝 줄 위치를 그대로 내보내지 않는다(if-0.12 탭11 Q-48 ②).
        self._contact_armed = False
        self.simulator.begin_external(plan.column_count)
        # 구간 하나 = job 하나. prepare 가 오면 차량을 구간 x 로, 리프트를
        # 구간 y 로 정렬한 뒤 로봇이 프로브 3점을 잡고 원점에 선다.
        # 펜던트에 무엇이 올라가 있든 **체크한 판**의 스캔 태스크로 돈다.
        # 작업마다 한 번 — 셀마다 부르는 play 는 그대로 play 만 한다.
        self._push_task_paths()
        self._load_scan_task()
        self._record_job("ERUT", self.erut_session.job_id, plan)
        self._erut_layout_plan = plan
        self.sequencer.start(plan, scan_h_mm, base_lift_mm=plan.lift_offset_mm,
                             move_first=True, hold_before_scan=prepare_only)

    def _abort_job(self) -> None:
        """ERUT 중단 요청(작업자 검사 종료). 멈추고 벽에서 물러나 홈으로 간다.

        규격 탭1: abort = 검사 마감, "로봇을 대기 상태로 되돌림". pause 처럼
        그 자리에서 이어갈 일이 없으므로 제자리에 두지 않는다 — 프로브가
        벽에 눌린 채로 있으면 다음 차량 이동을 못 한다. 홈 이동은 TCP -Z 로
        먼저 물러난 뒤 올라가므로(노드 move_home) 곡면을 긁지 않는다.
        """
        self._cancel_play()                 # 걸려 있던 play 가 뒤늦게 가지 않게
        self._pending_motions.clear()       # 미뤄 둔 차량·리프트 이동도 버린다
        self._paused_motions = []           # 일시정지로 세워 둔 이동의 목표도 버린다
        self._auto_home_sent = False
        self._motion_wait_timer.stop()
        self._zero_scan_outputs("ERUT 정지")
        self._cancel_measured_lap()          # 먼저 — 아래가 한 바퀴 기록을 지운다
        self._end_local_calibration("ERUT 정지")
        self._calibration_lap = None
        self.main_screen.set_pause_override(None)
        self.sequencer.stop()
        for adapter in (self.lift, self.amr, self.outrigger, self.retractor, self._vehicle_finish):
            adapter.cancel()
        self.simulator.stop_cycle()
        # 마킹 중이었으면 마킹을 접고 스캔 태스크로 되돌린다(cancel 이 한다).
        self.mark_runner.cancel()
        # 원점 대기 표시를 푼다. 로봇은 290 = 7 을 들고 멈추므로 표시가 남아
        # query 가 계속 ready 로 답했다.
        self._origin_waiting = False
        # 팔이 받침대 위여도 홈으로 간다(2026-11-26 — 받침대에서 되돌린다. 차량이 고정돼야 움직인다).
        self.main_screen.show_activity("작업을 중단했습니다 — 팔을 받침대에서 들어 홈으로 갑니다."
                                       if self._arm_on_rest() else
                                       "작업을 중단했습니다 — 벽에서 물러나 홈으로 갑니다.")
        # 정지·태스크 교체가 먼저 처리되도록 잠깐 뒤에 홈을 보낸다.
        _call_later(self, ABORT_HOME_DELAY_MS, self._send_home)
        # 로봇이 홈에 들어간 뒤 리프트를 0 으로(2026-11-24 — ERUT 정지). 홈은 위에서 보냈다.
        if self.lift.position > self.DRIVE_TOLERANCE_MM:
            self._auto_home_sent = True
            self.main_screen.show_activity("로봇이 홈에 들어간 뒤 리프트를 0 mm 로 내립니다.")
            self._defer_until_robot_idle(
                "정지 뒤 리프트 0 mm", lambda: self.lift.move_to(0.0, " mm", label="정지 뒤 0 mm"))

    # ---- ERUT 구간 마감 (2026-11-24) ------------------------------------------------
    #: 구역 위 끝이 모재 높이에서 이 안 [mm] 이면 마지막 높이 구간으로 본다.
    LAST_ZONE_TOLERANCE_MM = 1.0

    def _zone_top_mm(self) -> float | None:
        """지금 ERUT 구역의 위 끝 [mm, 모재 아래 끝부터]. 모르면 None."""
        plan = self.erut_session._plan
        if plan is None:
            return None
        rows = max(int(plan.row_count or 1), 1)
        return float(plan.origin_y) + rows * float(plan.cell_height) - (rows - 1) * float(plan.pitch_y or 0.0)

    def _is_last_height_zone(self) -> bool:
        """구역 위 끝이 모재 높이(검사 대상 높이 — ERUT geometry.height)에 닿거나 넘으면 마지막 높이 구간이다.

        ERUT start 에는 「마지막 높이」 표시가 없어 이렇게 판단한다(2026-11-24 사용자 결정).
        """
        top = self._zone_top_mm()
        height_mm = self.main_screen.orbit_view.target_dimensions()[1] * 1000.0
        return top is not None and height_mm > 0 and top >= height_mm - self.LAST_ZONE_TOLERANCE_MM

    def _on_zone_complete(self) -> None:
        """구간을 다 돌았다 — 로봇이 홈에 들어간 뒤 ERUT 에 완료를 낸다(2026-11-24).

        마지막 높이 구간이면 다음 일이 리프트 하강이라 로봇 홈 → 리프트 0 mm 까지 마친 뒤 낸다. 그동안 ERUT
        에는 running 이다(완료 전). 로봇은 구간 끝 플래그(290 = 5)를 먼저 쓰고 스스로 홈으로 간다 — 예전에는
        그 플래그를 보자마자 완료를 냈다.
        """
        session = self.erut_session
        if not session._start_req_id:
            session.on_job_complete()          # ERUT 구간이 아니다 — 낼 것이 없다
            return
        last = self._is_last_height_zone()
        note = " · 리프트를 0 mm 로 내린 뒤" if last and self.lift.position > self.DRIVE_TOLERANCE_MM else ""
        self.main_screen.show_activity(f"구간 검사를 마쳤습니다 — 로봇이 홈에 들어간 뒤{note} ERUT 에 완료를 알립니다.")

        def after_home() -> None:
            if not session._start_req_id:
                return                             # 그사이 정지 · 장애로 닫혔다
            if last and self.lift.position > self.DRIVE_TOLERANCE_MM:
                self.main_screen.show_activity("마지막 높이 구간 — 리프트를 0 mm 로 내린 뒤 ERUT 에 완료를 알립니다.")
                self._once(self.lift.arrived, session.on_job_complete)
                self.lift.move_to(0.0, " mm", label="마지막 높이 구간 뒤 0 mm")
                return
            session.on_job_complete()

        self._defer_until_robot_idle("ERUT 구간 완료 통보", after_home)

    def _connect_sequencer(self) -> None:
        """격자 순회를 로봇·리프트·AMR·화면·MQTT에 잇는다.

        시퀀서는 어느 셀인지만 판단하고, 실제 동작은 전부 시그널로 넘긴다.
        나중에 실제 PLC/AMR이 생기면 더미 어댑터만 갈아끼우면 된다.
        """
        seq = self.sequencer
        seq.activity.connect(self.main_screen.show_activity)
        self.lift.activity.connect(self.main_screen.show_activity)
        self.amr.activity.connect(self.main_screen.show_activity)
        self.outrigger.activity.connect(self.main_screen.show_activity)
        self.retractor.activity.connect(self.main_screen.show_activity)
        self.lift.position_changed.connect(self._show_motion_position)
        self.amr.position_changed.connect(self._show_motion_position)

        # 이동 요청 → 더미 어댑터 → 도착 신호 → 시퀀서
        # 구역 아래 끝 높이를 받는다 — 로봇 팔이 닿으면 리프트를 그대로 두고 팔을 든다
        # (_move_lift_for_cell, 2026-11-09).
        seq.lift_target_requested.connect(
            lambda mm: self._defer_until_robot_idle(
                f"리프트 {mm:,.0f} mm", lambda: self._move_lift_for_cell(mm)))
        # AMR 은 시퀀서가 **열 번호**로 부르는데, 바깥에는 이동 거리(mm)를
        # 알려야 한다(ERUT 규격 탭5 progress.moved). 열 간격으로 환산해
        # 어댑터에는 거리를 주고, 화면 문구에만 열 번호를 남긴다.
        seq.amr_move_requested.connect(
            lambda column: self._defer_until_robot_idle(
                f"{column}구역 차량 이동", lambda: self._move_amr_to_column(column), kind="drive"))
        self.lift.arrived.connect(seq.lift_arrived)
        # 일시정지 뒤 장비가 다 섰는지 다시 본다(if-0.6 — paused 는 선 뒤에만).
        for motion in (self.lift, self.amr, self.outrigger, self.retractor):
            motion.arrived.connect(self._on_pause_tick)
        seq.state_changed.connect(self._on_pause_tick)
        seq.state_changed.connect(self._resume_waiting_drive)
        self.ros_status.task_state_changed.connect(self._on_pause_tick)
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
        seq.robot_pause_requested.connect(self._pause_robot_scan)
        seq.motion_pause_requested.connect(self._pause_motions)
        seq.motion_resume_requested.connect(self._resume_motions)
        seq.robot_resume_requested.connect(self._resume_robot_scan)
        self.ros_status.scan_state_changed.connect(seq.handle_scan_state)
        self.ros_status.scan_state_changed.connect(self._handle_probe_error)
        self.ros_status.scan_state_changed.connect(self._handle_alive)
        self.ros_status.scan_state_changed.connect(self._handle_origin_wait)
        self.ros_status.scan_state_changed.connect(self._handle_vehicle_wait)
        self.ros_status.scan_state_changed.connect(self._report_probe_fit)

        # 화면
        seq.state_changed.connect(self._show_sequencer_state)
        seq.cell_changed.connect(self._show_sequencer_cell)
        seq.job_complete.connect(self._finish_job)

    def _show_motion_position(self, _value: float) -> None:
        """가상 차량·리프트가 움직일 때마다 화면 값을 갱신한다."""
        self.main_screen.set_motion_values(self.lift.position, self.amr.position)

    def _move_amr_to_column(self, column: int) -> None:
        """열 번호를 이동 거리로 바꿔 AMR 더미에 넘긴다."""
        plan = self.sequencer.plan
        pitch = plan.column_pitch if plan is not None else 0.0
        if plan is not None and plan is getattr(self, "_erut_layout_plan", None):
            if self.main_screen.orbit_view.target_shape()[0] == SHAPE_PLATE:
                # 곡판: 차량은 캘리브레이션 때 판 가운데 QR 에 정렬한 자리에 그대로 선다 —
                # 구역은 판 가운데 기준이라(erut_session) 리프트 · 로봇만 움직인다(2026-11-18).
                distance = self.amr.position
            else:
                # ERUT 구역: 로봇 중심이 구역 가운데에 오게 선다 — 로봇이 거기서 좌우로
                # 가로/2 씩 훑어 start.x ~ end.x 를 덮는다(0~150 구역이면 차량 75).
                distance = self._vehicle_x(plan.section_center_x)
        else:
            # RCS 격자: 1구역(지금 자리 0)에서 열 간격(가로 - 겹침)씩 간다.
            distance = max(0, int(column) - 1) * pitch
        label = f"{column}구역 ({distance:.0f} mm)"
        if abs(distance - self.amr.position) <= self.DRIVE_TOLERANCE_MM:
            # 같은 세로 줄(바로 위·아래 구간) — 차량은 그대로 두고 리프트만 옮긴다.
            self.amr.move_to(distance, " mm", label=label)
            return
        self._prepare_to_drive(
            lambda: self.amr.move_to(distance, " mm", label=label),
            guard=self._drive_guard_for_job)

    #: 이보다 가까우면 차량은 안 움직인다고 본다 [mm].
    DRIVE_TOLERANCE_MM = 1.0

    def _vehicle_x(self, surface_x: float) -> float:
        """검사면 x [mm] → 차량 주행 거리 [mm] (캘리브레이션 QR 영점에서).

        차량 위치는 로봇 중심(호 가운데)의 x 다. 둘레를 넘은 x 는 이음매를 지나
        감긴 자리로 접고(한 바퀴를 더 돌지 않는다), 캘리브레이션 한 바퀴로 잰
        궤적 배율을 곱한다 — 차량은 모재에서 떨어진 바깥 궤적을 돌아 같은 각도라도
        더 달린다(한 바퀴 실측 ÷ π × 지름, 재기 전에는 1).
        """
        x = float(surface_x)
        shape, width_m = self.main_screen.orbit_view.target_shape()
        if shape == SHAPE_PLATE:
            # 곡판은 이음매가 없고, 차량이 정렬하는 QR 이 판 가운데(x = 판 가로/2)다 — 차량 위치
            # 0 이 판 가운데다(2026-11-18). 스캔 때는 차량을 움직이지 않는다(_move_amr_to_column).
            x -= width_m * 500.0
        else:
            circumference = math.pi * self.main_screen.orbit_view.target_dimensions()[0] * 1000.0
            if circumference > 0:
                x %= circumference
        return x * self._travel_scale()

    def _prepare_to_drive(self, drive, guard=None) -> None:
        """차량이 달리기 전에 리프트를 내리고 아웃트리거를 푼 뒤 drive() 한다.

        옆 세로 줄로 갈 때 순서: 리프트 0 mm(아웃트리거가 고정된 채 — 차량은
        고정돼 있어야 리프트를 움직인다) → 아웃트리거 해제 → 주행. 도착하면
        시퀀서가 다시 고정(1단계) → 리프트를 그 구간 높이로(2단계) 한다.
        예전에는 리프트를 올린 채, 아웃트리거를 고정한 채 차량을 달렸다.

        실제 차량이면 아웃트리거 해제 대신 **작업 끝**(RobotControl state "FINISH")을 보낸다 — 차량은
        도착하면 스스로 HOLD(아웃트리거 고정)하고 이 신호를 기다리며, 받으면 홀딩을 풀고 아웃트리거를 올려
        이동할 수 있는 상태(FINISH)가 된다. 아웃트리거가 이미 올라가 있어도 보낸다. 다만 PAUSE 로 세워 둔
        주행이 있으면(재개) 보내지 않는다 — FINISH 가 그 작업을 끝내 버린다.

        `guard()` 는 단계마다 "go"/"wait"/"drop" 을 돌려준다 — 그 사이 작업이
        일시정지되면 기다렸다 재개 때 잇고, 정지되면 버린다.
        """
        steps = []
        if self.lift.position > self.DRIVE_TOLERANCE_MM:
            steps.append((self.lift, "주행 전에 리프트를 내립니다.",
                          lambda: self.lift.move_to(0.0, " mm", label="주행 전 0 mm")))
        # 리프트가 0 mm 에 선 뒤(아웃트리거가 고정된 채) 팔을 받침대에 올린다(2026-11-26).
        steps.append((None, "", None))
        if self.amr.active == "vehicle" and not self._vehicle_amr.paused:
            steps.append((self._vehicle_finish,
                          "주행 전에 차량에 작업 끝(finish)을 보냅니다 — 홀딩 해제 · 아웃트리거 올림.",
                          lambda: self._vehicle_finish.move_to(0)))
        elif self.amr.active != "vehicle" and self.outrigger.position >= 1:
            steps.append((self.outrigger, "주행 전에 아웃트리거를 풉니다.",
                          lambda: self.outrigger.move_to(0, " 해제")))

        def run(index: int) -> None:
            state = guard() if guard else "go"
            if state == "drop":
                return
            if state == "wait":
                self._drive_waiting = lambda: run(index)
                return
            if index >= len(steps):
                drive()
                return
            motion, text, start = steps[index]
            if motion is None:
                self._rest_before_drive(lambda: run(index + 1))
                return
            self.main_screen.show_activity(text)
            self._once(motion.arrived, lambda: run(index + 1))
            start()

        run(0)

    def _rest_before_drive(self, then) -> None:
        """주행 전에 팔을 받침대에 올린 뒤 then() — 로봇이 있는 장비면 **언제나**(2026-11-26).

        못 올리면(위치 미저장 · 태스크 없음 · 리프트가 0 mm 아님) 달리지 않는다(_rest_failed · E2002).
        로봇이 없는 구성(홈 플래그를 한 번도 못 봄)만 그냥 달린다.
        """
        if self._arm_on_rest() or not self._rest_required():
            then()
            return
        self._defer_until_robot_idle("주행 전 팔 받침대 거치", then, kind="rest")

    def _drive_guard_for_job(self) -> str:
        state = self.sequencer.state
        if state is SequencerState.MOVING_AMR:
            return "go"
        if state is SequencerState.PAUSED and self.sequencer.resume_state is SequencerState.MOVING_AMR:
            return "wait"
        return "drop"

    def _pause_motions(self) -> None:
        """일시정지 — 구간 이동 중인 차량 · 오르내리는 리프트 · 아웃트리거를 그 자리에 세운다.

        예전에는 가던 곳까지 가고 도착만 미뤘다(2026-11-12 — 일시정지면 바로 서야 한다). 목표를
        기억해 두었다가 재개하면 같은 목표로 다시 보낸다 — 닿으면 하던 순서(시퀀서 · 주행 준비)가
        이어진다. 정지는 목표를 버린다(_stop_job_locally · _abort_job).
        """
        held = []
        for motion in (self.amr, self.lift, self.outrigger):
            if motion.moving:
                held.append((motion, motion.target))
                motion.pause()
        self._paused_motions = held
        if held:
            self.main_screen.show_activity("일시정지 — 움직이던 차량 · 리프트를 그 자리에 세웠습니다.")

    def _resume_motions(self) -> None:
        """재개 — 세워 둔 차량 · 리프트를 같은 목표로 다시 보내고, 미뤄 둔 이동도 잇는다."""
        held, self._paused_motions = getattr(self, "_paused_motions", []), []
        for motion, target in held:
            motion.move_to(target, label=f"{target:g} (재개)")
        if held:
            self.main_screen.show_activity("재개 — 세워 둔 차량 · 리프트가 가던 곳으로 다시 갑니다.")
        if self._pending_motions:
            self._run_pending_motions()
            if self._pending_motions and not self._motion_wait_timer.isActive():
                self._motion_wait_timer.start(self._robot_wait_ms())

    def _motions_on_hold(self) -> bool:
        """일시정지 중이라 차량 · 리프트를 새로 움직이면 안 되는가."""
        return self.sequencer.state is SequencerState.PAUSED

    def _resume_waiting_drive(self, *_args) -> None:
        """일시정지로 멈춰 둔 주행 준비를 재개 때 잇는다."""
        waiting = getattr(self, "_drive_waiting", None)
        if waiting is None:
            return
        if self.sequencer.state is SequencerState.PAUSED or self.mark_runner.paused:
            return
        self._drive_waiting = None
        waiting()

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

    def _on_target_dimensions(self, diameter_m: float, height_m: float) -> None:
        """검사 대상 바깥 지름 · 높이가 바뀌었다 — 저장하고 로봇 반지름(260)도 맞춘다.

        로봇 반지름은 바깥 지름의 절반 하나뿐이라(2026-10-19), 지름을 고치면 작업
        영역을 다시 보낸다. 로봇과 끊겨 있거나 태스크가 돌면 붙은 뒤 · 끝난 뒤에 보낸다.
        """
        shape, width_m = self.main_screen.orbit_view.target_shape()
        self.settings_service.save(
            "inspection_target", {"diameter_m": diameter_m, "height_m": height_m,
                                  "shape": shape, "width_m": width_m})
        # 차량 주행 배율도 모재 지름에 따라 바뀐다(차량 설정 화면 표시).
        self.screens["vehicle"].set_target_diameter(float(diameter_m) * 1000.0)
        width, height, scan_h, overlap = self.main_screen.rect_view.work_area()
        self._send_work_area(width, height, scan_h, overlap, **self._work_area_extra)

    def _set_target_dimensions(self, diameter_m: float, height_m: float) -> None:
        """검사 대상 바깥 지름 · 높이를 바꾼다(ERUT calibrate 등) — 화면 · 저장 · 로봇."""
        self.main_screen.orbit_view.set_target_dimensions(diameter_m, height_m)
        self._on_target_dimensions(diameter_m, height_m)

    def _set_target_geometry(self, geometry: dict) -> None:
        """검사 대상을 ERUT geometry{shape, diameter, height[, width]} [mm] 로 바꾼다.

        곡판(curved_plate)은 판 가로(width)도 남긴다 — 구역 배치 · 차량 이동이 감기지 않는다.
        높이가 0 이면 지금 높이를 그대로 둔다.
        """
        _old_d, old_h = self.main_screen.orbit_view.target_dimensions()
        height = float(geometry.get("height") or 0.0)
        shape = str(geometry.get("shape") or SHAPE_CYLINDER)
        width_m = float(geometry.get("width") or 0.0) / 1000.0 if shape == SHAPE_PLATE else 0.0
        self.main_screen.orbit_view.set_target_shape(shape, width_m)
        self._set_target_dimensions(float(geometry.get("diameter") or 0.0) / 1000.0,
                                    height / 1000.0 if height > 0 else old_h)

    def _target_geometry(self) -> dict:
        """지금 RCS 검사 대상 — ERUT 와 같은 꼴 {shape, diameter, height[, width]} [mm]."""
        diameter_m, height_m = self.main_screen.orbit_view.target_dimensions()
        shape, width_m = self.main_screen.orbit_view.target_shape()
        geometry = {"shape": shape, "diameter": diameter_m * 1000.0, "height": height_m * 1000.0}
        if shape == SHAPE_PLATE:
            geometry["width"] = width_m * 1000.0
        return geometry

    def _target_length_mm(self) -> float:
        """검사면 x 의 길이 [mm] — 원통은 둘레 π × 지름, 곡판은 판 가로(감기지 않는다)."""
        geometry = self._target_geometry()
        if geometry["shape"] == SHAPE_PLATE:
            return float(geometry.get("width") or 0.0)
        return math.pi * geometry["diameter"]

    def _outer_radius_mm(self) -> float:
        """벽 바깥 반지름 [mm] = 검사 대상 **바깥 지름** / 2. 모르면 0.

        2026-10-19 부터 RCS 는 바깥 지름 하나만 가지고 작업한다 — 두께는 쓰지 않는다.
        로봇이 훑는 면이 바깥면이라 호 · 3점 측정 · 좌우 한계가 모두 이 반지름이다.
        ERUT 의 diameter 도 외경이다(if-0.10 탭5 77행). 화면 「검사 대상」에서 고치거나
        ERUT calibrate 가 오면 바뀐다.
        """
        diameter_m, _height_m = self.main_screen.orbit_view.target_dimensions()
        return max(float(diameter_m), 0.0) * 500.0

    def _max_area_width(self) -> float:
        """로봇이 한 번에 훑을 수 있는 최대 구간 가로(호 길이) [mm]. 모르면 0.

        현 한계(MAX_PROBE_CHORD_MM, 700 mm)와 벽 바깥 반지름으로 정한다.
        """
        return max_safe_arc_mm(self._outer_radius_mm())

    def _clamp_work_width(self, width_mm: float, radius_mm: float,
                          eoat_w_mm: float) -> float:
        """호 길이를 충돌 안전 한계로 자르고, 잘렸으면 알린다.

        호를 길게 잡을수록 좌우 프로브 사이 **현**이 길어지는데, 굽은 벽에서
        이 값이 한계를 넘으면 프로브가 벽에 닿는다. 로봇은 자기 안전 한계
        안에서만 움직이므로, 그보다 긴 값을 그대로 그리면 **로봇이 따라올 수
        없는 경로를 화면에만 그리게 된다**(마커가 경로 끝에 못 닿는다).
        """
        # 한계는 로봇 팔이 좌우로 갈 수 있는 현 길이다(700 mm) — EOAT 를
        # 골랐든 안 골랐든 같다. 예전에는 EOAT 가 없으면 자르지 않아, 호
        # 1000 mm 가 그대로 나가 로봇이 왼쪽 끝 자세를 못 풀고 멈췄다.
        limit = max_safe_arc_mm(radius_mm, eoat_w_mm)
        if limit <= 0 or width_mm <= limit:
            return width_mm

        chord = probe_chord_mm(width_mm - eoat_w_mm, radius_mm)
        message = (f"작업 호 길이 {width_mm:.0f} mm 는 최대 작업 길이를 넘습니다 — "
                   f"로봇 좌우 이동 거리가 {chord:.0f} mm 로 한계"
                   f"({MAX_PROBE_CHORD_MM:.0f} mm)를 초과합니다. "
                   f"가능한 최대는 {limit:.0f} mm 이며, 그 값으로 진행합니다.")
        self.main_screen.show_activity(message)
        self._add_alarm("M2001", message)
        # 장애가 아니다 — 줄여서 그대로 진행하므로 알림(evt/message)으로 낸다.
        # ERUT 가 시킨 일이 있을 때만(ERUT 구간은 넘으면 앞에서 400 으로 거절한다).
        if self.erut_session.has_work():
            self.erut_session.notify(
                *MSG_ARC_LIMIT_CLAMPED,
                f"구간 폭 {width_mm:.0f} mm 가 최대 작업 폭을 넘어 "
                f"최대값 {limit:.0f} mm 로 줄여 진행합니다.")
        return limit

    def _send_work_area(
        self, width_mm: float, height_mm: float, scan_h_mm: float,
        overlap_mm: float, radius_mm: float = 0.0,
        eoat_w_mm: float = 0.0, eoat_h_mm: float = 0.0,
        eoat_type: float = 0.0,
        pitch_left: float | None = None, pitch_right: float | None = None,
    ) -> None:
        """셀 치수와 호 정보를 로봇(레지스터 256~264)과 저장소에 반영한다.

        좌 · 우 끝 상승(`pitch_left` · `pitch_right`, if-0.12)을 주지 않으면(None) 지금
        값을 그대로 잇는다 — ERUT 에서 마지막으로 받은 값이 RCS 「검사 시작」까지 남는다
        (2026-11-12). 0 이상 두 값이면 그 값으로, 음수 · 합 0 이면 「자동」(고르게 나눔)으로.

        `width_mm` 은 **호 길이**다(현이 아니다). 로봇이 반지름과 함께
        현을 계산해 호 세 점을 만든다. `radius_mm` 은 벽 바깥 반지름이고, 주지
        않으면(0) 검사 대상 바깥 지름의 절반을 쓴다(`_outer_radius_mm`).
        두께는 쓰지 않는다 — 로봇 261 에는 늘 0 이 간다(2026-10-19).

        반지름은 0.1mm 단위로 보낸다. 반지름이 834.6 처럼 소수라
        정수 mm 로는 호 모양이 눈에 띄게 틀어지기 때문이다. 레지스터는
        16bit 라 3276.7mm 까지 담긴다.
        """
        if radius_mm <= 0:
            radius_mm = self._outer_radius_mm()
        # 다이얼로그는 너비/높이/스캐너/겹침만 묻는다. EOAT 값은 여기서
        # 기억해 뒀다가 그때 이어 붙인다(_apply_work_area_edit 참고).
        self._work_area_extra = {
            "eoat_w_mm": eoat_w_mm, "eoat_h_mm": eoat_h_mm,
            "eoat_type": eoat_type,
        }
        self._show_eoat_choice(eoat_type)
        width_mm = self._clamp_work_width(width_mm, radius_mm, eoat_w_mm)
        side = self._set_side_pitch(pitch_left, pitch_right)
        self.main_screen.set_work_area(width_mm, height_mm, scan_h_mm, overlap_mm,
                                       eoat_w_mm, radius_mm)
        self.settings_service.save(
            "work_area",
            {"width_mm": width_mm, "height_mm": height_mm,
             "scan_h_mm": scan_h_mm, "overlap_mm": overlap_mm,
             "eoat_w_mm": eoat_w_mm, "eoat_h_mm": eoat_h_mm,
             "eoat_type": eoat_type,
             "pitch_left_mm": side[0] if side else None,
             "pitch_right_mm": side[1] if side else None},
        )
        self._push_work_area_to_robot(width_mm, height_mm, scan_h_mm, overlap_mm,
                                      radius_mm, eoat_w_mm, eoat_h_mm, eoat_type,
                                      *(side if side else (-1.0, -1.0)))

    #: 지금 작업 계획의 좌 · 우 끝 상승 [mm] — ERUT 에서 받았거나 화면에서 넣은 값. None = 자동.
    _side_pitch: tuple[float, float] | None = None

    def _set_side_pitch(self, pitch_left, pitch_right) -> tuple[float, float] | None:
        """좌 · 우 끝 상승을 정한다 — None 이면 지금 값 그대로. 정한 값을 돌려준다."""
        if pitch_left is not None and pitch_right is not None:
            left, right = float(pitch_left), float(pitch_right)
            self._side_pitch = ((left, right) if left >= 0 and right >= 0 and left + right > 0
                                else None)
        self.main_screen.set_side_pitch(self._side_pitch)
        return self._side_pitch

    # 로봇 컨트롤러가 주는 태스크 상태(레지스터 500). 1 = 실행 중.
    _TASK_STATE_RUNNING = 1
    _TASK_STATE_STOPPED = 3
    #: 29999 pause 로 세운 태스크. 팔은 벽에 붙은 채 그 자리에 서 있고, play 가
    #: 오면 거기서 잇는다 — 실행 중과 똑같이 취급한다(차량·리프트·홈 금지).
    _TASK_STATE_PAUSED = 2
    # 작업 영역 중 다이얼로그가 안 묻는 값들(EOAT). 화면에서 고칠 때 이어 붙인다.
    # 반지름은 여기 두지 않는다 — 늘 검사 대상 바깥 지름에서 구한다.
    _work_area_extra: dict = {
        "eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0,
    }

    def _apply_work_area_edit(self, width_mm: float, height_mm: float,
                              scan_h_mm: float, overlap_mm: float,
                              pitch_left: float | None = None,
                              pitch_right: float | None = None) -> None:
        """화면에서 고친 작업 영역을 저장하고 **로봇에도 보낸다**.

        예전에는 저장만 해서, 겹침을 바꿔도 로봇은 옛 값으로 계속 돌았다.
        게다가 네 항목만 저장해 반지름·EOAT 가 통째로 지워졌고, 다음에
        불러올 때 반지름 0(=평면)으로 로봇에 내려가 굽은 벽을 직선으로
        훑을 뻔했다. 다이얼로그가 안 묻는 값은 지금 값을 그대로 잇는다.
        """
        extra = dict(self._work_area_extra)
        self._send_work_area(width_mm, height_mm, scan_h_mm, overlap_mm, **extra,
                             pitch_left=pitch_left, pitch_right=pitch_right)

    def _work_area_block_reason(self) -> str:
        """지금 작업 영역을 로봇에 못 보내는 이유. 보낼 수 있으면 빈 문자열.

        도는 중에 작업 계획을 바꾸면 안 된다 — 진행 중인 검사가 중간에
        다른 격자로 바뀐다. Modbus 가 끊겨 있으면 애초에 쓸 통로가 없다.
        어느 쪽이든 보류해 뒀다가 조건이 풀리면 자동으로 다시 보낸다.
        """
        if not getattr(self, "_robot_link_up", False):
            return "로봇 Modbus 연결이 없습니다"
        if self._robot_task_running():
            return "로봇 태스크가 실행 중입니다"
        return ""

    def _push_work_area_to_robot(
        self, width_mm: float, height_mm: float, scan_h_mm: float,
        overlap_mm: float, radius_mm: float,
        eoat_w_mm: float, eoat_h_mm: float, eoat_type: float = 0.0,
        pitch_left: float = -1.0, pitch_right: float = -1.0,
    ) -> None:
        """작업 영역을 로봇 레지스터(256~264)에 쓰고 param_src(266)를 세운다.

        값은 **아홉 개**여야 한다 — 하나라도 모자라면 노드가 통째로
        거부한다(레지스터 264 = EOAT 종류). 260 은 벽 바깥 반지름, 261(두께)은
        늘 0 이다 — 로봇 태스크가 반지름에 두께를 더하므로 0 이어야 바깥
        반지름 그대로 훑는다(2026-10-19, 바깥 지름만 쓴다).

        보낼 수 없는 상황이면(태스크 실행 중·연결 없음) 조용히 넘어가지 않고
        사유를 알린다. 안 그러면 화면 값과 로봇 값이 말없이 어긋난다.
        """
        # 소수가 나오는 자리(스캐너 높이·반지름·EOAT)는 0.1mm 단위로
        # 보낸다 — 커버 167.5, 반지름 834.6 처럼 정수 mm 로 반올림하면
        # 호 모양과 줄 간격이 눈에 띄게 틀어진다. 로봇 쪽 dus_init 이 같은
        # 자리에 0.1 을 곱해 되돌린다. 16bit 라 3276.7mm 까지 담긴다.
        values = [width_mm, height_mm, round(scan_h_mm * 10), overlap_mm,
                  round(radius_mm * 10), 0,
                  round(eoat_w_mm * 10), round(eoat_h_mm * 10), eoat_type]
        # ERUT 좌·우 끝 이동높이 [0.1 mm] → 356 · 357 (if-0.12). 없으면 0 · 0 이고 로봇은
        # 예전처럼 상승 제한(263)으로 구역 세로를 고르게 나눈다. 구역 높이보다 큰
        # 상승은 어차피 위 끝에서 잘리므로 높이로 줄여 16bit 칸에 담는다.
        # 줄 높이는 그 값으로 정해진다 — 로봇 태스크와 같은 규칙으로 미리 구해 화면 ·
        # ERUT 위치 계산에 쓴다(job_sequencer.scan_row_heights).
        if pitch_left >= 0 and pitch_right >= 0:
            top = max(float(height_mm), 0.0)
            self._pending_scan_pitch = [round(min(pitch_left, top) * 10),
                                        round(min(pitch_right, top) * 10)]
            self._robot_row_heights = scan_row_heights(
                round(top), self._pending_scan_pitch[0] / 10, self._pending_scan_pitch[1] / 10)
        else:
            self._pending_scan_pitch = [0, 0]
            self._robot_row_heights = None
        self.main_screen.rect_view.set_row_heights(self._robot_row_heights)
        self._pending_work_area = values
        # 로봇이 다음 play 에서 읽을 값. 스캔 시작 전에 0 이 없는지 본다.
        self._robot_work_area = values

        reason = self._work_area_block_reason()
        if reason:
            message = (f"작업 영역을 로봇에 보내지 못했습니다 — {reason}"
                       f"{self._task_state_note()}. "
                       f"조건이 풀리면 자동으로 다시 보냅니다.")
            self.main_screen.show_activity(message)
            self._add_alarm("M2002", message)
            # 장애가 아니다 — 조건이 풀리면 스스로 다시 보내므로 알림으로 낸다.
            # ERUT 에는 내부 통신 사정(연결·태스크 상태)을 싣지 않는다. ERUT 가
            # 시킨 일이 있을 때만 알린다 — 프로그램을 켤 때마다 저장된 작업 영역을
            # 다시 보내다 막혀 나가던 것은 ERUT 와 상관없는 소음이었다(ERUT Q-09).
            if self.erut_session.has_work():
                self.erut_session.notify(
                    *MSG_AREA_APPLY_PENDING,
                    "구간 설정을 아직 반영하지 못했습니다. 준비되면 자동으로 다시 반영합니다.")
            return

        self._send_scan_pitch()
        if self.ros_status.send_pose("work_area", values):
            self._pending_work_area = None
            self.main_screen.show_activity("작업 영역을 로봇에 반영했습니다.")

    def _send_scan_pitch(self) -> None:
        """좌·우 끝 이동높이(356 · 357)를 작업 영역보다 먼저 보낸다.

        0 · 0(ERUT 값 없음)인데 제어 노드가 그 칸을 모르면(옛 노드) 보낼 것이 없다 —
        옛 노드로도 RCS '검사 시작'은 그대로 돈다.
        """
        values = getattr(self, "_pending_scan_pitch", None) or [0, 0]
        if not any(values) and not self.ros_status.writable("scan_pitch"):
            return
        self.ros_status.send_pose("scan_pitch", values)

    #: 좌·우 끝 이동높이(356 · 357)를 읽는 로봇 태스크 판.
    SIDE_PITCH_TASKS = frozenset({"dusan_v6"})

    def _side_pitch_blocker(self) -> str:
        """ERUT 좌·우 이동높이를 지금 못 쓰면 그 까닭(→ 400 E6001 detail). 쓰면 ""."""
        version = getattr(self, "_task_version", "") or DEFAULT_TASK_VERSION
        if version not in self.SIDE_PITCH_TASKS:
            return (f"로봇 태스크 {version} 는 좌·우 끝 이동높이(pitch_left · pitch_right)를 "
                    f"쓰지 못합니다 — {', '.join(sorted(self.SIDE_PITCH_TASKS))} 로 바꿔야 합니다")
        if self.ros_status.lacks_write("scan_pitch"):
            return ("로봇 제어 노드가 좌·우 끝 이동높이 칸(레지스터 356 · 357)을 모릅니다 — "
                    "RCS PC 에서 colcon build 를 다시 해야 합니다")
        return ""

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
        self._send_scan_pitch()
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
        """태스크가 살아 있는가 — 실행 중이거나 **일시정지**(그 자리에 서 있음)."""
        return getattr(self, "_robot_task_state", 0) in (
            self._TASK_STATE_RUNNING, self._TASK_STATE_PAUSED)

    def _home_interlock_active(self) -> bool:
        """홈 위치까지 확인해야 하는 상황인가.

        홈 플래그(276)를 **한 번이라도 1 로 받아 본 뒤에만** 건다. 로봇에
        올라가 있는 태스크가 이 값을 쓰는지 RCS 는 미리 알 수 없다 —
        v4·v5 는 쓰지만, 예전에 올려 둔 판은 안 쓴다. 안 쓰는 판이면
        레지스터가 계속 0 이라 그대로 걸면 차량이 영영 못 움직인다.
        1 을 한 번 본 뒤부터는 그 값을 믿는다(그때부터는 0 = 홈 밖).
        """
        return bool(getattr(self, "_home_flag_seen", False))

    # ---- 팔 받침대 (레지스터 360, 2026-11-26) ---------------------------------------------
    #: 로봇 받침대 태스크(dus6_park / dus6_unpark)가 360 에 쓰는 값.
    ARM_PARKING, ARM_PARKED, ARM_UNPARKING, ARM_READY, ARM_UNTAUGHT = 1, 2, 3, 4, 9
    #: 홈 명령이 되돌리기 태스크로 갔다는 노드 응답 표시(robot_control_node.UNPARK_MARK).
    UNPARK_MARK = "받침대 되돌리기"
    #: 받침대에서 홈으로 되돌리는 데 100 % 속도에서 걸리는 가장 긴 시간 [ms] — 직선 구간이 느리다
    #: (받침대에 닿는 구간 20 mm/s). 홈 거두기 한도 · 미뤄 둔 이동 대기에 더한다.
    UNPARK_WITHIN_MS_AT_FULL_SPEED = 90_000

    def _arm_on_park_path(self) -> bool:
        """팔이 받침대 길(올리는 중 · 받침대 위 · 되돌리는 중)에 있는가 — 홈 플래그가 서 있으면 아니다."""
        return (getattr(self, "_arm_park", 0) in (self.ARM_PARKING, self.ARM_PARKED, self.ARM_UNPARKING)
                and not getattr(self, "_robot_at_home", False))

    def _arm_on_rest(self) -> bool:
        """팔이 받침대 위에 내려져 쉬고 있는가(360 = 2, 태스크 멈춤) — 차량이 주행해도 되는 자세."""
        return (getattr(self, "_arm_park", 0) == self.ARM_PARKED
                and not getattr(self, "_robot_at_home", False)
                and not self._robot_task_running())

    def _arm_block_reason(self, kind: str = "lift") -> str:
        """팔 자세 때문에 지금 움직이면 안 되는 이유(태스크 실행 여부는 빼고). 괜찮으면 빈 문자열.

        kind "drive"(차량 주행 · 아웃트리거 해제 · QR 정렬)는 홈이거나 **받침대 위**면 된다.
        "lift"(리프트) · "robot"(로봇 작업)은 홈에서만 — 받침대 위에서 리프트가 움직이면 팔이
        받침대에 걸리고, 스캔 태스크는 홈에서 나가는 길(대기 위치)로 받침대를 끌고 간다.
        """
        if kind == "rest":
            # 주행 직전 — 받침대 위여야 한다(무조건, 2026-11-26). 거치는 리프트 0 mm 에서만 된다.
            if self._arm_on_rest():
                return ""
            if self._rest_required():
                if not self._lift_at_zero():
                    return "리프트가 0 mm 가 아니라 팔을 받침대에 올릴 수 없습니다"
                return "로봇 팔을 받침대에 올리기 전입니다"
            kind = "drive"
        if kind == "drive" and self._arm_on_rest():
            return ""
        if self._arm_on_park_path():
            if getattr(self, "_arm_park", 0) == self.ARM_PARKED:
                return "로봇 팔이 받침대 위에 있습니다"
            return "로봇 팔이 받침대로 오가는 중입니다"
        if self._home_interlock_active() and not self._robot_at_home:
            return "로봇이 홈 위치에 있지 않습니다"
        return ""

    def _robot_motion_block_reason(self, kind: str = "lift") -> str:
        """지금 차량·리프트를 움직이면 안 되는 이유. 움직여도 되면 빈 문자열.

        kind 는 _arm_block_reason 참고 — 차량 주행("drive")만 받침대 위에서 허가한다.
        """
        if self._robot_task_running():
            return "로봇 태스크가 실행 중입니다"
        return self._arm_block_reason(kind)

    #: 거치는 리프트 0 mm 에서만 된다(2026-11-26) — 리프트 도착 허용 오차(5 mm) 안이면 0 으로 본다.
    REST_LIFT_TOLERANCE_MM = 5.0

    def _rest_required(self) -> bool:
        """차량이 달리기 전에 팔을 받침대에 올려야 하는가 — 로봇이 있는 장비면 언제나(2026-11-26).

        홈 플래그를 한 번이라도 받은(인터락이 걸린) 장비다. 로봇이 없는 구성만 예전처럼 그냥 달린다.
        로봇 연결이 끊겨 있어도 올려야 한다 — 못 올리면 달리지 않는다.
        """
        return self._home_interlock_active()

    def _lift_at_zero(self) -> bool:
        return self.lift.position <= self.REST_LIFT_TOLERANCE_MM

    def _send_rest(self) -> None:
        """팔을 홈에서 받침대 위로 — 노드가 받침대 태스크(dusan_v6_park)를 튼다."""
        self._rest_sent = True
        self.main_screen.show_activity("로봇 팔을 받침대에 올립니다(거치).")
        self.ros_status.call_command("rest")

    def _request_rest(self) -> None:
        """메인 화면 「로봇 거치」 — 로봇이 쉬고 있을 때만 받침대로 보낸다(홈 · 받침대 길에서)."""
        if self._robot_busy():
            self.main_screen.show_activity(f"거치 거절 — {HOME_BUSY_TEXT}")
            return
        if self._arm_on_rest():
            self.main_screen.show_activity("팔이 이미 받침대 위에 있습니다.")
            return
        if not getattr(self, "_robot_at_home", False) and not self._arm_on_park_path():
            self.main_screen.show_activity("거치는 홈에서만 합니다 — 먼저 「로봇 홈」을 누르세요.")
            return
        if not self._lift_at_zero():
            self.main_screen.show_activity(
                f"거치는 리프트 0 mm 에서만 합니다 — 지금 리프트 {self.lift.position:,.0f} mm.")
            return
        self._send_rest()

    def _on_rest_command_result(self, name: str, ok: bool, message: str) -> None:
        """거치 명령을 노드가 거절했다(태스크 없음 등) — 차량을 움직이지 않는다."""
        if name != "rest" or ok:
            return
        self._rest_failed(f"받침대 거치를 못 했습니다 — {message}")

    def _rest_failed(self, text: str) -> None:
        """거치 실패 — 주행 전 거치를 기다리던 이동은 버리고 E2002(이동 안전 자세 실패)로 멈춘다.

        받침대 위가 아니면 차량을 움직이지 않는다(2026-11-26 「무조건 거치」) — 예전처럼 홈 자세로 달리지 않는다.
        """
        self._rest_sent = False
        self._auto_rest_sent = False
        self._add_alarm("E9206", text)
        waiting = [entry for entry in self._pending_motions if entry[2] == "rest"]
        if not waiting:
            self.main_screen.show_activity(text)
            return
        self._pending_motions = [entry for entry in self._pending_motions if entry[2] != "rest"]
        if not self._pending_motions:
            self._motion_wait_timer.stop()
        self.main_screen.show_activity(f"{text} — 받침대 위가 아니라 차량을 움직이지 않습니다.")
        self._raise_home_failed(f"주행 전 받침대 거치를 못 해 차량 이동을 멈췄습니다 — {text}")

    def _robot_wait_ms(self) -> int:
        """미뤄 둔 이동이 로봇을 기다리는 한도 — 받침대로 오가는 중이면 그만큼 더 기다린다."""
        if getattr(self, "_unpark_sent", False) or getattr(self, "_rest_sent", False):
            return self.ROBOT_TASK_WAIT_MS + self._unpark_extra_ms()
        return self.ROBOT_TASK_WAIT_MS

    def _unpark_extra_ms(self) -> int:
        percent = int(getattr(self, "_speed_scale_percent", 0) or 100)
        percent = max(SPEED_MIN, min(SPEED_MAX, percent))
        return int(self.UNPARK_WITHIN_MS_AT_FULL_SPEED * 100 / percent)

    def _defer_until_robot_idle(self, label: str, run, kind: str = "lift") -> None:
        """로봇이 홈에서 쉬고 있을 때만 차량·리프트를 움직인다.

        로봇은 한 셀을 마칠 때 **완료 플래그(290 = 5)를 먼저 쓰고 그 다음에
        홈으로 이동**한다(dus_finish -> 홈). 플래그만 보고 리프트를 올리면
        로봇이 아직 움직이는 중에 발판이 올라간다. 두 가지를 다 보고서야
        움직인다.
          * 태스크 상태(레지스터 500)가 실행 중이 아니다
          * 홈 위치 플래그(레지스터 276)가 1 이다 — 홈을 벗어나면 0 이므로
            펜던트에서 직접 돌리거나 조그로 빼낸 경우도 걸린다
        차량 주행(kind "drive")은 팔이 받침대 위(360 = 2)여도 된다(2026-11-26). 리프트 · 로봇 작업은
        홈에서만이라, 팔이 받침대 위면 먼저 홈으로 보내고(노드가 되돌리기 태스크를 돌린다) 기다린다.
        """
        if self._motions_on_hold():
            # 일시정지 중 — 재개할 때 한다(_resume_motions).
            self._pending_motions.append((label, run, kind))
            return
        reason = self._robot_motion_block_reason(kind)
        if not reason:
            run()
            return
        self._pending_motions.append((label, run, kind))
        if kind == "rest":
            self.main_screen.show_activity(f"{label} — 팔을 받침대에 올린 뒤 주행합니다.")
        else:
            self.main_screen.show_activity(f"{reason} — {label}은(는) 로봇이 홈에서 멈춘 뒤에 합니다.")
        self._auto_arm_action(kind, label)
        if not self._motion_wait_timer.isActive():
            self._motion_wait_timer.start(self._robot_wait_ms())

    def _auto_arm_action(self, kind: str, label: str) -> None:
        """미뤄 둔 이동을 위해 팔을 스스로 옮긴다 — 주행 직전이면 받침대로, 아니면 홈으로.

        이동하기 전에 스스로 홈으로 간다(ERUT if-0.5) — 태스크가 끝났는데 팔이
        펴져 있으면(캘리브레이션 뒤·조그 뒤 등) 기다려도 저절로 접히지 않는다.
        주행 직전(kind "rest")이면 홈에 선 뒤 받침대에 올린다(2026-11-26).
        """
        if self._robot_task_running():
            return
        if (kind == "rest" and self._rest_required() and self._lift_at_zero()
                and getattr(self, "_robot_at_home", False) and not self._arm_on_park_path()):
            if not getattr(self, "_auto_rest_sent", False):
                self._auto_rest_sent = True
                self._send_rest()
                if self._motion_wait_timer.isActive():
                    self._motion_wait_timer.start(self._robot_wait_ms())
            return
        if kind == "rest" and self._arm_on_park_path() and getattr(self, "_arm_park", 0) != self.ARM_PARKED:
            return                                   # 받침대로 오가다 멈췄다 — 손으로 맞춰야 한다
        if not getattr(self, "_auto_home_sent", False) and not getattr(self, "_robot_at_home", False):
            self._auto_home_sent = True
            if self._arm_on_park_path():
                self.main_screen.show_activity(f"{label} 전에 팔을 받침대에서 홈으로 되돌립니다.")
            else:
                self.main_screen.show_activity(f"{label} 전에 로봇을 홈으로 거둡니다.")
            self._send_home()

    def _pending_block_reason(self) -> str:
        """미뤄 둔 이동 중 아직 못 하는 것의 이유(첫 번째). 다 해도 되면 빈 문자열."""
        for _label, _run, kind in self._pending_motions:
            reason = self._robot_motion_block_reason(kind)
            if reason:
                return reason
        return ""

    def _run_pending_motions(self, timed_out: bool = False) -> None:
        if not self._pending_motions:
            self._motion_wait_timer.stop()
            return
        if self._motions_on_hold():
            # 일시정지 중에는 미뤄 둔 이동을 하지 않는다 — 재개 때 잇는다.
            self._motion_wait_timer.stop()
            return
        reason = self._pending_block_reason()
        if reason and not timed_out:
            # 아직 로봇이 움직인다 — 더 기다린다. 홈에 막 닿았으면 다음 동작(받침대 거치)을 건다.
            label, _run, kind = self._pending_motions[0]
            self._auto_arm_action(kind, label)
            return
        if timed_out and any(self._arm_block_reason(kind) for _l, _r, kind in self._pending_motions):
            # 팔이 펴진 채로는 차량·리프트를 움직이지 않는다(ERUT if-0.5 — 이동
            # 중에는 home 이어야 한다). 예전에는 한도가 지나면 그대로 움직였다.
            pending, self._pending_motions = self._pending_motions, []
            self._auto_home_sent = False
            self._auto_rest_sent = False
            labels = ", ".join(label for label, _run, _kind in pending)
            self.main_screen.show_activity(
                f"로봇이 홈 자세가 아니라 {labels}을(를) 하지 않습니다.")
            # 먼저 홈으로 보냈는데도 안 왔다 — 표준 E2002 HOME_FAILED(if-0.8).
            self._raise_home_failed(
                f"로봇 팔이 이동 안전 자세(홈)가 아니라 이동을 멈췄습니다 — {labels}")
            return
        pending, self._pending_motions = self._pending_motions, []
        self._auto_home_sent = False
        self._auto_rest_sent = False
        self._motion_wait_timer.stop()
        if timed_out:
            self.main_screen.show_activity(
                f"{reason or '로봇 태스크가 실행 중입니다'} — "
                f"{self.ROBOT_TASK_WAIT_MS // 1000}초가 지나 그대로 진행합니다: "
                + ", ".join(label for label, _run, _kind in pending))
        for _label, run, _kind in pending:
            run()

    def _on_robot_at_home(self, at_home: bool) -> None:
        """홈 위치 플래그(276)가 바뀌었다. 홈에 닿으면 미뤄 둔 이동을 한다."""
        self._robot_at_home = bool(at_home)
        if not self._robot_at_home:
            self._home_pending_saw_low = True
        elif getattr(self, "_home_move_pending", False) and getattr(
                self, "_home_pending_saw_low", False):
            # 노드는 홈 이동 전에 276 을 0 으로 내린다 — 내려갔다 다시 선 것이 도착이다.
            self._home_fold_done()
        if self._robot_at_home:
            # 이 값을 쓰는 태스크가 올라가 있다는 뜻 — 이제부터 믿는다.
            self._home_flag_seen = True
            if getattr(self, "_erut_home_armed", False):
                self._erut_home_armed = False
                self.erut_session.home_arrived()
        elif self._home_interlock_active() and any(m.moving for m in (self.amr, self.lift)):
            # 이동 중에 팔이 펴지면 스스로 멈춘다(ERUT if-0.5 탭2 30행).
            self._stop_motions_arm_left("차량·리프트가 움직이는 중에 로봇이 홈을 벗어나 이동을 멈췄습니다")
        # ERUT evt/home 을 바로 바꿔 낸다(바뀔 때만 나간다).
        self.erut_session.refresh_status()
        self._reload_scan_after_unpark()
        self._run_pending_motions()
        self._sync_manual_interlock()

    def _stop_motions_arm_left(self, detail: str) -> None:
        """차량 · 리프트가 움직이는 중에 팔이 이동 안전 자세(홈 · 받침대 위)를 벗어났다 — 세운다(E2003)."""
        for motion in (self.amr, self.lift):
            motion.cancel()
        self._add_alarm("E2003", detail)
        self.erut_session.raise_error({
            # if-0.10 표준 번호(예전 3S E9303 ROBOT_LEFT_HOME_WHILE_MOVING, 탭11 Q-33).
            "code": "E2003", "name": "HOME_LOST_WHILE_MOVING",
            "level": "stop", "recovery": "manual",
            "detail": detail,
        })

    def _on_arm_park(self, state: int) -> None:
        """팔 받침대 상태(360)가 바뀌었다(2026-11-26).

        받침대 위(2)면 차량 주행만 허가한다. 주행 중에 팔이 받침대를 떠나면(되돌리기를 펜던트에서
        튼 경우 등) 홈을 떠난 것처럼 세운다.
        """
        before = getattr(self, "_arm_park", 0)
        self._arm_park = int(state)
        if self._arm_park == self.ARM_PARKED:
            self._rest_sent = False
        elif self._arm_park == self.ARM_UNTAUGHT and getattr(self, "_rest_sent", False):
            self._rest_failed("받침대 자세(Park_*_joint)가 펜던트에 저장되어 있지 않아 거치를 못 했습니다")
        if (before == self.ARM_PARKED and self._arm_park != self.ARM_PARKED
                and not getattr(self, "_robot_at_home", False) and self.amr.moving):
            self._stop_motions_arm_left("차량이 주행하는 중에 로봇 팔이 받침대를 벗어나 이동을 멈췄습니다")
        self.erut_session.refresh_status()
        self._run_pending_motions()
        self._sync_manual_interlock()

    def _note_unpark_reply(self, name: str, ok: bool, message: str) -> None:
        """노드가 홈 명령을 받침대 되돌리기 태스크로 돌렸다 — 끝나면 스캔 태스크를 다시 불러 둔다.

        360 이 0(전원을 껐다 켬)이어도 노드는 관절값으로 받침대 자세를 알아본다 — 응답으로도 본다.
        """
        if name == "home" and ok and self.UNPARK_MARK in str(message):
            self._unpark_sent = True
            if self._motion_wait_timer.isActive():
                self._motion_wait_timer.start(self._robot_wait_ms())

    def _reload_scan_after_unpark(self) -> None:
        """되돌리기 태스크가 홈에서 끝났다 — 펜던트에 그 태스크가 올라가 있으니 스캔 태스크를 다시 불러 둔다."""
        if not getattr(self, "_unpark_sent", False):
            return
        if not getattr(self, "_robot_at_home", False) or self._robot_task_running():
            return
        self._unpark_sent = False
        self.main_screen.show_activity("팔을 받침대에서 홈으로 되돌렸습니다 — 스캔 태스크를 다시 불러 둡니다.")
        self._load_scan_task()

    def _on_robot_task_state(self, state: int) -> None:
        self._robot_task_state = int(state)
        # 태스크가 돌면 팔은 홈이 아니다 — ERUT evt/home 을 바로 맞춘다.
        self.erut_session.refresh_status()
        self._watch_scan_task(self._robot_task_state)
        if not self._robot_task_running():
            # 태스크가 끝났다 — 홈 이동까지 마친 시점이다. 미뤄 둔 것을 한다.
            self._reload_scan_after_unpark()
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
        if not self._work_area_ready_for_scan():
            return
        if self._arm_on_park_path():
            # 받침대 위에서 스캔 태스크를 틀면 홈에서 나가는 길(대기 위치)로 받침대를 끌고 간다 —
            # 먼저 되돌리고(홈 명령 → 받침대 되돌리기 태스크) 홈에서 튼다(2026-11-26).
            self._defer_until_robot_idle("로봇 스캔 시작", self._start_robot_scan, kind="robot")
            return
        # 지난 셀의 스캔 허가(267)가 남아 있으면 로봇이 원점에서 서지 않고
        # 그냥 지나간다 — 프로브 확인을 건너뛴 채 훑게 된다. 태스크는 시작할
        # 때 이 칸을 지우지 않으므로 여기서 지운다.
        self.ros_status.send_value("scan_go", 0)
        # 이번 play 에서 3점 측정을 볼 때까지는 290 의 옛 값을 접촉으로 치지 않는다.
        self._contact_armed = False
        # 이번 play 에서 태스크가 도는 걸 볼 때까지는 멈춤(3)을 장애로 안 본다.
        self._scan_task_seen_running = False
        # 원격 제어 모드가 아니면 컨트롤러가 play/stop 을 모두 거부한다
        # ("not supported in local control mode"). 펜던트를 만지면 로컬로
        # 돌아가므로 셀마다 켜 준다.
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")
        self._schedule_play()
        self._last_scan_command_failure = ""
        # 다시 틀면(재개 · 다음 셀) 앞서 건 확인은 버린다 — 번호로 가린다.
        self._start_watch_id = getattr(self, "_start_watch_id", 0) + 1
        watch_id = self._start_watch_id
        _call_later(self, ROBOT_RESTART_DELAY_MS + ROBOT_START_TIMEOUT_MS,
                    lambda: self._check_robot_started(watch_id))

    def _check_robot_started(self, watch_id: int) -> None:
        """스캔을 틀었는데 로봇 태스크가 끝내 돌지 않았으면 장애로 끝낸다(Q-29).

        play 응답으로 성공을 가리지 않는다 — 로봇이 레지스터 500 에 실행(1)을
        써야 출발한 것이다. 출발 전에 일시정지·정지됐으면(순회가 스캔 중이
        아니면) 따지지 않는다. 장애(E9203)로 올리면 ERUT 작업은 evt/complete
        500 으로 끝나고 브릿지가 작업자에게 알린다.
        """
        if (watch_id != getattr(self, "_start_watch_id", 0)
                or self.sequencer.state is not SequencerState.SCANNING
                or getattr(self, "_scan_task_seen_running", False)):
            return
        failure = getattr(self, "_last_scan_command_failure", "")
        cause = f" — {failure}" if failure else (
            " — 로봇이 연결돼 있는지, 펜던트에 태스크 파일이 있는지, 원격 제어 모드인지 확인하세요")
        text = (f"로봇 태스크가 {ROBOT_START_TIMEOUT_MS // 1000}초 안에 시작되지 않았습니다"
                f"{cause}")
        self.main_screen.show_activity(text)
        self._add_alarm("E9203", text)
        self.erut_session.raise_error({
            "code": "E9203", "name": "ROBOT_START_FAILED", "detail": text,
            "level": "stop", "recovery": "reset_required",
        })
        if self.sequencer.state is SequencerState.SCANNING:
            self.sequencer.stop()

    def _work_area_ready_for_scan(self) -> bool:
        """호 길이(256)·반지름(260)이 0 이면 스캔 태스크를 틀지 않는다.

        태스크는 둘 중 하나라도 0 이면 펜던트에 "ARC IS ZERO" 팝업을 띄우고
        멈춘다(dus5_init → dus5_probe_l). 반지름은 캘리브레이션(모재 지름)이나
        작업 지시가 와야 정해지고, 프로그램을 막 켰을 때는 0 이다. 로봇까지
        가서 멈추게 두지 말고 여기서 사유를 알리고 작업을 접는다.
        """
        values = getattr(self, "_robot_work_area", None)
        if values is None:
            return True
        missing = [name for name, value in (("호 길이(가로)", values[0]),
                                            ("반지름", values[4]))
                   if float(value) <= 0]
        if not missing:
            return True
        text = (f"작업 영역의 {'·'.join(missing)}이(가) 0 이라 스캔을 시작하지 않습니다"
                " — 캘리브레이션(모재 지름)과 구간 설정을 먼저 받아야 합니다.")
        self.main_screen.show_activity(text)
        self._add_alarm("E9304", text)
        self.erut_session.raise_error({
            "code": "E9304", "name": "WORK_AREA_NOT_SET", "level": "stop",
            "recovery": "manual", "detail": text,
        })
        self.sequencer.stop()
        return False

    def _schedule_play(self) -> None:
        """잠시 뒤 play 를 보낸다. abort 가 오면 취소된다(_cancel_play).

        예전에는 QTimer.singleShot 으로 걸어 두어 되돌릴 수 없었다. 시작 직후
        abort 가 오면 그 뒤에 play 가 날아가 **멈춘 로봇이 다시 출발**했다.
        """
        if getattr(self, "_robot_safety_stop", False):
            # 안전재설정 전에는 로봇이 출발하지 못한다 — 풀리면 그때 보낸다(_on_robot_safety_stop).
            self._play_after_safety = True
            self.main_screen.show_activity("로봇 안전정지 중 — 안전재설정되면 로봇을 출발시킵니다.")
            return
        # 출발 직전에 스캔 줄 속도(307)를 지금 값으로 다시 써 둔다 — 그사이 무엇이 덮었어도
        # 이번 구간은 ERUT 속도(ERUT 작업) · 속도 바 값(RCS 작업)으로 돈다(2026-11-11).
        self._sync_scan_ratio(alarm=True)
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
        self._play_after_safety = False      # 안전재설정을 기다리던 출발도 버린다

    def _start_marking(self, points: list) -> None:
        """ERUT 마킹 점들을 돈다. 격자 크기는 지금 작업 영역을 쓴다.

        마커가 ERUT 것이라 점마다 자리에서 서서 mark_next 를 기다린다(hold).
        """
        width, height, _scan_h, _overlap = self.main_screen.rect_view.work_area()
        self.mark_runner.start(points, width, height, hold=True)

    def _drive_guard_for_mark(self) -> str:
        runner = self.mark_runner
        if not runner.running:
            return "drop"
        return "wait" if runner.paused else "go"

    def _resume_erut_marking(self) -> None:
        """ERUT 마킹 재개 — 멈춘 자리에서 잇는다(기다리던 점이면 mark_ready 다시)."""
        self.mark_runner.resume()
        self._resume_waiting_drive()

    def _pause_mark_robot(self) -> None:
        """점으로 가거나 물러나던 로봇을 그 자리에 세운다(29999 pause)."""
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("pause")

    def _resume_mark_robot(self) -> None:
        """세워 둔 마킹 태스크를 그 자리에서 잇는다(29999 play)."""
        if getattr(self, "_robot_task_state", None) == self._TASK_STATE_PAUSED:
            self.ros_status.call_command("remote_control_on")
            self.ros_status.call_command("play")

    def _stop_erut_marking(self) -> None:
        """ERUT 마킹을 도중에 접는다(일시정지·장애). 찍은 점까지는 완료에 싣는다."""
        marked, failed = self.mark_runner.progress()
        self.mark_runner.cancel()
        self._stop_robot_scan()
        self.erut_session.finish_mark(marked, failed, code=500)

    # ---- ERUT 에 싣는 로봇 위치 · 접촉 · 홈 ------------------------------------
    #: 탐촉자가 벽에 붙어 있는 로봇 상태(290): 원점 대기 · 적심 · 스캔.
    _CONTACT_STATES = frozenset({7, 8, 6})
    #: ㄹ자 스캔 중인 로봇 상태(290).
    _SCAN_STATE_SCANNING = 6

    def _arc_position_mm(self) -> float | None:
        """ㄹ자 스캔 중(290 = 6) 지금 줄의 호 위 거리 [mm] (레지스터 286). 아니면 None.

        로봇은 원점 기준 좌표(280~285)를 스캔 중에만 내고 그 밖에는 0 을 쏘지만, 286 은
        호가 잡혀 있으면 원점 복귀 · 후퇴 중에도 낸다 — 스캔 중일 때만 써야 점이 세로 0 에
        가로만 떠 있지 않는다. 호가 안 잡혔으면(0) None.
        """
        state = getattr(self, "_last_scan_state", None) or []
        if not state or int(state[0]) != self._SCAN_STATE_SCANNING:
            return None
        arc = getattr(self, "_scan_arc", None) or []
        if arc and arc[0] > 0:
            return arc[0] / 10.0
        return None

    def _on_scan_arc(self, values: list) -> None:
        self._scan_arc = [int(v) for v in values]
        self._watch_press(self._scan_arc)

    # ---- ERUT 이동 방향 알림 (evt/motion · if-0.11) -------------------------------
    def _on_scan_segment(self, segment: int) -> None:
        """로봇 태스크가 다음 스캔 구간(277)을 세웠다 — 움직이기 전에 ERUT 에 알린다."""
        self.erut_session.set_scan_segment(segment, self._segment_remain_mm(segment))

    def _segment_remain_mm(self, segment: int) -> float | None:
        """그 구간에서 움직일 남은 거리 [mm] (evt/motion remain_mm, 선택). 모르면 None.

        줄(1 전진 · 3 후진)은 로봇에 보낸 호 길이(256)에서 지금 호 위치(286, 원점부터
        0.1 mm)를 뺀다 — 전진은 원점에서 멀어지고 후진은 원점으로 온다. 줄 바꿈(2)은
        로봇이 정한 줄 간격(296, 0.1 mm)이다.
        """
        if segment in (1, 3):
            area = getattr(self, "_robot_work_area", None) or []
            length = float(area[0]) if area else 0.0
            if length <= 0:
                return None
            arc = getattr(self, "_scan_arc", None) or []
            pos = min(max(arc[0] / 10.0, 0.0), length) if arc else (0.0 if segment == 1 else length)
            return length - pos if segment == 1 else pos
        if segment == 2:
            state = getattr(self, "_last_scan_state", None) or []
            # 좌·우 끝 이동높이(if-0.12)면 이번 상승 = 다음 줄 높이 - 지금 줄 높이.
            # 로봇은 줄을 끝내고 291(끝낸 줄 수)을 올린 뒤 상승한다.
            heights = getattr(self, "_robot_row_heights", None)
            row = int(state[1]) if len(state) > 1 else 0
            if heights and 1 <= row < len(heights):
                rise = heights[row] - heights[row - 1]
                return round(rise, 1) if rise > 0 else None
            pitch = state[6] / 10.0 if len(state) > 6 else 0.0
            return pitch if pitch > 0 else None
        return None

    def _check_holder_channels(self, channels: int) -> None:
        """ERUT 가 알린 홀더 채널 수(holder.channel_count)로 장착 홀더를 맞춘다(2026-11-26).

        5 · 8 이면 Cobot 설정 「장착 홀더」를 그 값으로 바로 바꾸고 로봇(264)에 보낸다 — ERUT 계획이
        먼저다. 예전에는 견주기만 하고 RCS 설정으로 움직였다. 작업 · 로봇 태스크가 도는 중이거나
        5 · 8 이 아니면(맞는 홀더가 없다) 바꾸지 않고 작업자와 ERUT 에 알린다(M2003).
        """
        probes = int(float(self._work_area_extra.get("eoat_type") or 0))
        if channels == probes:
            return
        choices = {value for value, _text in CobotSettingsScreen.EOAT_CHOICES if value}
        if channels in choices and not self._job_running() and not self._robot_task_running():
            self._apply_eoat_choice(channels)
            self._show_eoat_choice(channels)
            self.main_screen.show_activity(
                f"ERUT 가 보낸 홀더 채널 수 {channels} 로 장착 홀더를 {channels}축 홀더로 바꿨습니다.")
            return
        mounted = f"{probes}축 홀더" if probes else "노그리퍼(툴 플랜지)"
        text = (f"ERUT 홀더 채널 수 {channels} 가 RCS 의 장착 홀더 {mounted} 와 다릅니다 — "
                f"로봇은 RCS 설정({mounted})으로 움직입니다. 홀더 설정을 확인하세요.")
        self.main_screen.show_activity(text)
        self._add_alarm("M2003", text)
        self.erut_session.notify(*MSG_HOLDER_MISMATCH, text,
                                 channel_count=channels, eoat_type=probes)

    # ---- 스캔 중 눌림 유지 (E3002) ---------------------------------------------------
    #: scan_arc(286~289)의 3번째 값 = 레지스터 288 — 스캔 중 눌림 상태.
    #: 0 감시 안 함 · 1 적정 · 2 덜 눌림(눌림 센서가 떨어짐) · 3 너무 눌림.
    _PRESS_STATE_INDEX = 2
    _PRESS_LOST = 2
    #: 스캔 중 눌림 센서가 이만큼 넘게 **계속** 떨어져 있으면 E3002 PROBE_CONTACT_LOST [ms]
    #: (2026-10-19). 잠깐 떨어지면 로봇 태스크가 TCP +Z 로 다시 눌러 잡는다
    #: (dus5_pass_r/l) — 그건 장애가 아니다.
    PRESS_LOST_MS = 1000

    def _watch_press(self, values: list) -> None:
        """눌림 센서가 떨어지면 시간을 잰다. 다시 붙으면 처음부터 다시 잰다.

        scan_arc 는 값이 바뀔 때만 오므로(떨어진 채 로봇이 서 있으면 더 안 온다)
        타이머로 잰다.
        """
        timer = getattr(self, "_press_timer", None)
        if timer is None:
            timer = self._press_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self.PRESS_LOST_MS)
            timer.timeout.connect(self._press_lost_too_long)
        state = values[self._PRESS_STATE_INDEX] if len(values) > self._PRESS_STATE_INDEX else 0
        if state == self._PRESS_LOST:
            if not timer.isActive() and not getattr(self, "_press_lost_reported", False):
                timer.start()
            return
        timer.stop()
        self._press_lost_reported = False

    def _press_lost_too_long(self) -> None:
        """눌림 센서가 PRESS_LOST_MS 넘게 떨어져 있다 — E3002 로 세운다."""
        arc = getattr(self, "_scan_arc", None) or []
        if len(arc) <= self._PRESS_STATE_INDEX or arc[self._PRESS_STATE_INDEX] != self._PRESS_LOST:
            return
        self._press_lost_reported = True
        if "E3002" in self.erut_session.error_codes():
            return                      # 로봇 태스크가 먼저 알렸다(299 = 6)
        text = (f"스캔 중 눌림 센서가 {self.PRESS_LOST_MS / 1000:g}초 넘게 떨어져 멈췄습니다"
                " — 탐촉자가 벽에서 떨어졌습니다")
        self.main_screen.show_activity(text)
        self._add_alarm("E3002", text)
        self.erut_session.raise_error({
            "code": "E3002", "name": "PROBE_CONTACT_LOST",
            "level": "stop", "recovery": "manual", "probe": "center", "detail": text,
        })

    def _row_pitch_01mm(self, state: list) -> int:
        """줄 간격(296)을 0.1 mm 로. 옛 태스크(정수 mm)가 올라가 있어도 맞게 읽는다.

        2026-10-06 부터 태스크는 296 을 0.1 mm 로 쓴다. 로봇에 태스크를 다시 올리기
        전에는 mm 로 와서 10 배 틀린다 — 작업 영역 높이 ÷ (줄 수 - 1) 과 비교해
        더 가까운 쪽 단위로 읽는다.
        """
        raw = int(state[6]) if len(state) > 6 else 0
        rows = int(state[2]) if len(state) > 2 else 0
        area = getattr(self, "_robot_work_area", None)
        if raw > 0 and rows > 1 and area and float(area[1]) > 0:
            expected = float(area[1]) / (rows - 1)
            if abs(raw - expected) < abs(raw / 10.0 - expected):
                return raw * 10          # 옛 태스크 — mm 로 왔다
        return raw

    def _erut_position(self) -> dict:
        """격자 안 로봇 위치와 진행률 (ERUT pos·progress 용).

        가로 = 이번 줄에서 원점부터 호를 따라 간 거리(286, 0.1mm), 세로 = 지금 줄의
        높이. 줄 높이는 좌·우 끝 이동높이(if-0.12)면 그 규칙으로 구한 높이, 아니면
        줄 번호(291) x 줄 간격(296, 0.1mm)이다. 진행률은 로봇이 내는 298, 스캔한
        거리는 287(누적 mm).

        로봇은 다음 구역의 3점 측정을 시작하기 전까지 지난 구역 값(진행률 100 · 끝 줄)을
        들고 있다. 이번 구역에서 로봇이 출발한 걸 볼 때까지(_contact_armed) 진행률은
        싣지 않고 위치는 구역 원점으로 둔다(if-0.12 탭11 Q-48 ②).
        """
        if not getattr(self, "_contact_armed", False):
            return {"arc_mm": 0.0, "row_mm": 0.0, "progress": None, "scanned_mm": None}
        arc = getattr(self, "_scan_arc", None) or []
        state = getattr(self, "_last_scan_state", None) or []
        row = int(state[1]) if len(state) > 1 else 0
        rows = int(state[2]) if len(state) > 2 else 0
        # 291 은 끝낸 줄 수라 마지막 줄을 끝내면 줄 수와 같아진다 — 그대로 곱하면 위
        # 끝보다 한 줄 위(y 229 / 구역 200)가 나갔다(Q-48 ③). 끝 줄에 묶는다.
        heights = getattr(self, "_robot_row_heights", None)
        if heights:
            row_mm = heights[min(max(row, 0), len(heights) - 1)]
        else:
            if rows > 0:
                row = min(row, rows - 1)
            # 296 은 0.1 mm 단위다(2026-10-06 — 정수 mm 면 29.6 이 30 이 되어 줄마다
            # 0.4 mm 씩 위로 밀렸다).
            row_mm = row * self._row_pitch_01mm(state) / 10.0
        return {
            "arc_mm": (arc[0] / 10.0) if arc else 0.0,
            "row_mm": row_mm,
            # 290~299 의 9번째(298)가 진행률이다. 예전엔 8번째(297, 스캔한 경로
            # 0.1 단위)를 읽어 1 m 만 가도 100 이 됐다(ERUT Q-02, 2026-10-02).
            "progress": int(state[8]) if len(state) > 8 else None,
            "scanned_mm": float(arc[1]) if len(arc) > 1 and arc[1] > 0 else None,
        }

    #: 이번 play 에서 로봇이 3점 측정·원점 복귀를 시작했다는 표시(290 = 2, 3).
    _CONTACT_ARM_STATES = frozenset({2, 3})

    def _track_erut_contact(self, values: list) -> None:
        """탐촉자 접촉을 ERUT 에 알린다 — ERUT 가 이것으로 물을 켜고 끈다.

        로봇은 태스크를 멈춰도 290 에 마지막 상태(원점 대기·적심·스캔)를 들고
        있다. 그대로 믿으면 abort 뒤 다음 구역의 start 를 받자마자, 팔이 펴지기도
        전에 attached 가 나가 물이 켜졌다(ERUT Q-18, 2026-10-06). 그래서 RCS 가
        로봇을 출발시킨 뒤 이번 play 에서 3점 측정·원점 복귀(2·3)를 본 다음에만
        접촉으로 친다. 멈추면(정지·abort·장애) 다시 막는다.
        """
        if not values:
            return
        self._last_scan_state = list(values)
        state = int(values[0])
        if (state in self._CONTACT_ARM_STATES
                and getattr(self, "_robot_task_state", 0) == self._TASK_STATE_RUNNING):
            self._contact_armed = True
        attached = getattr(self, "_contact_armed", False) and state in self._CONTACT_STATES
        self.erut_session.set_contact(attached)

    def _drop_erut_job(self) -> None:
        """준비해 둔(또는 장애로 실패한) ERUT 구간을 접는다. 홈으로는 안 보낸다."""
        self._origin_waiting = False
        self.sequencer.stop()

    def _erut_at_home(self) -> bool | None:
        """ERUT 에 home 으로 알려도 되는가 — 접혀 있고 **쉬고 있을 때만**.

        홈 플래그(276) 하나만 믿으면 안 된다(ERUT Q-01·Q-05, 2026-10-02):
        스캔 중 탐촉자가 붙어 있는데도 home 이 나갔고, abort 뒤 홈으로 접는
        중인데도 query 에 home 이 나갔다(플래그가 내려가기 전 0.5초). 그래서
        아래 중 하나라도 맞으면 deployed 다(접는 중 포함 deployed — 탭2 14행).
          * 홈 플래그가 1 이 아니다
          * 로봇 태스크가 실행 중이거나 일시정지다 — 팔이 움직이거나 벽 앞이다
          * 탐촉자가 붙어 있다(evt/contact attached)
          * 홈으로 보내 놓고 아직 닿은 걸 못 봤다
        """
        if self._arm_on_rest() and not getattr(self, "_home_move_pending", False):
            # 받침대 위도 이동 안전 자세다(2026-11-26) — 차량이 그 자세로 이동한다.
            return True
        at_home = getattr(self, "_robot_at_home", None)
        if at_home is not True:
            return at_home
        if self._robot_task_running():
            return False
        if getattr(self.erut_session, "_contact", None):
            return False
        if getattr(self, "_home_move_pending", False):
            return False
        return True

    def _home_after_reset(self, codes: list) -> None:
        """초기화로 장애가 풀리면 펴져 있는 팔을 스스로 홈으로 거둔다.

        쉬는 동안(idle)에는 팔을 접어 둔다는 규칙(if-0.5/0.6 탭2 30행)을
        비상정지·장애 뒤에도 지키려는 것이다 — 그때는 하던 구간이 끝난 것으로
        처리돼 팔이 벽 앞에 그대로 남는다. 규격도 초기화 때 장치가 스스로 하는
        복구 동작(원점 복귀 등)을 허용한다(탭4 reset). 사람이 가까이 있으면
        차량 라이다가 막아 로봇이 움직이지 못한다.

        안 하는 때: 이어 갈 작업을 멈춰 둔 채다(일시정지는 제자리 — 이어 가려면
        그 자리를 지켜야 한다) · 작업이나 로봇 태스크가 돌고 있다 · 이미 홈이다 ·
        로봇과 연결이 없다.
        """
        if self.erut_session.home_state() == "home":
            return
        if not getattr(self, "_robot_link_up", False):
            self.main_screen.show_activity(
                "초기화했지만 로봇과 연결이 없어 팔을 홈으로 거두지 못했습니다.")
            return
        if not self._can_fold():
            return
        what = f"({', '.join(codes)}) " if codes else ""
        self.main_screen.show_activity(f"초기화{what}— 펴져 있는 팔을 홈으로 거둡니다.")
        self._send_home()

    def _can_fold(self) -> bool:
        """초기화로 팔을 거둘 수 있는가 — 돌거나 멈춰 둔 작업이 없고 로봇과 붙어 있다.

        일시정지는 제자리(이어 가려면 그 자리를 지켜야 한다), 작업 중이면 그
        작업이 팔을 쓴다 — 둘 다 reset 은 200 으로 아무것도 안 한다(if-0.8 탭9).
        """
        if (self.sequencer.state is SequencerState.PAUSED
                or getattr(self.erut_session, "_paused_work", "")):
            return False
        if self._job_running() or self._robot_task_running():
            return False
        return bool(getattr(self, "_robot_link_up", False))

    def _send_home_for_erut(self) -> None:
        """ERUT 홈 요청 — 보내고, 로봇이 실제로 홈에 닿으면 완료를 낸다.

        팔이 받침대 위여도 홈으로 간다(2026-11-26) — 노드가 받침대 되돌리기 태스크를 돌린다. 거치는 RCS 가
        안에서만 다룬다(차량 이동 전에 스스로 올린다).
        """
        self._erut_home_armed = False
        self._send_home()

    def _clear_home_pending_on_failure(self, name: str, ok: bool, message: str) -> None:
        """홈 명령의 결과. 받았으면 그때부터 도착을 살피고, 거절되면 E2002."""
        if name != "home" or not getattr(self, "_home_move_pending", False):
            return
        if ok:
            self._home_acked_at = time.monotonic()
            return
        self._home_move_pending = False
        if getattr(self, "_home_watch_timer", None) is not None:
            self._home_watch_timer.stop()
        self._raise_home_failed(f"로봇이 홈 명령을 거절했습니다 — {message}")
        self.erut_session.refresh_status()

    def _on_home_command_result(self, name: str, ok: bool, message: str) -> None:
        """노드가 홈 스크립트를 받았다. 그 뒤로 홈 플래그가 서면 도착이다.

        노드는 홈 이동을 보내기 **전에** 276 을 0 으로 내리므로, 이 응답 뒤에
        보는 1 은 새로 도착한 것이다(이미 홈에 있어도 한 번 내렸다 올린다).
        """
        if name != "home" or not getattr(self.erut_session, "_home_req_id", ""):
            return
        if not ok:
            self.erut_session.finish_home(False, message)
            return
        self._erut_home_armed = True
        if getattr(self, "_robot_at_home", False) and not self._robot_task_running():
            _call_later(self, 1500, self._check_erut_home)

    def _check_erut_home(self) -> None:
        if getattr(self, "_erut_home_armed", False) and getattr(self, "_robot_at_home", False):
            self._erut_home_armed = False
            self.erut_session.home_arrived()

    def _start_mark_task(self) -> None:
        """마킹 태스크로 바꿔 끼우고 튼다.

        스캔 때처럼 원격 제어를 켜고 세운 뒤, 29999 `task -p` 로 마킹
        태스크를 불러와 play 한다. 다 끝나면 MarkRunner 가 스캔 태스크를
        다시 불러 둔다. 팔이 받침대 길에 있으면 먼저 되돌린 뒤 튼다(2026-11-26).
        """
        if self._arm_on_park_path():
            self._defer_until_robot_idle("마킹 시작", self._start_mark_task, kind="robot")
            return
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")
        # 마킹은 시작 자세 높이 그대로 한다 — 스캔 때 들어 둔 팔 높이(358)를 지운다.
        self._send_scan_z_offset(0)
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
        self._paused_motions = []              # 일시정지로 세워 둔 이동의 목표도 버린다
        self._auto_home_sent = False
        self._motion_wait_timer.stop()
        if self._job_running():
            self._zero_scan_outputs("RCS 정지")
        # 캘리브레이션 중이면 차량만 세우고 접는다(ERUT calibrate 는 아래에서 실패로 알린다).
        if self._calibration_in_progress():
            self._cancel_measured_lap()
            self._calibration_lap = None
            self.main_screen.set_pause_override(None)
        self._end_local_calibration("정지")
        self.simulator.stop_cycle()
        self.sequencer.stop()
        self.mark_runner.cancel()
        for adapter in (self.lift, self.amr, self.outrigger, self.retractor, self._vehicle_finish):
            adapter.cancel()
        self._origin_waiting = False
        self.erut_session.interrupt_active_job()

    def _robot_busy(self) -> bool:
        """로봇이 동작 중인가 — 이때는 홈 명령을 거절한다.

        둘 중 하나면 동작 중으로 본다.
          * RCS 작업이 도는 중 (스캔 순회·마킹, 일시정지 포함)
          * 로봇 태스크가 실행 중·일시정지 (레지스터 500 == 1, 2) — 프로브·원점 대기·
            ㄹ자·마킹 어느 단계든, 펜던트에서 직접 튼 경우도 여기 걸린다.
        """
        return self._job_running() or self._robot_task_running()

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
        # 팔이 받침대 길에 있으면 노드가 받침대 되돌리기 태스크를 돌린다(2026-11-26) — 끝나면 스캔
        # 태스크를 다시 불러 둔다(_reload_scan_after_unpark). 관절값으로만 알아본 경우는 응답으로 안다.
        self._unpark_sent = self._arm_on_park_path()
        self.main_screen.show_activity("로봇 팔을 받침대에서 들어 홈으로 되돌립니다." if self._unpark_sent
                                       else "로봇을 홈으로 보냅니다.")
        self._park_position()
        # 닿은 걸 볼 때까지 ERUT 에는 deployed(접는 중)로 낸다.
        self._home_move_pending = True
        self._home_pending_saw_low = not getattr(self, "_robot_at_home", False)
        self._home_sent_at = time.monotonic()
        self._home_acked_at = None
        if getattr(self, "_home_watch_timer", None) is None:
            self._home_watch_timer = QTimer(self)
            self._home_watch_timer.setInterval(self.HOME_WATCH_MS)
            self._home_watch_timer.timeout.connect(self._watch_home_fold)
        self._home_watch_timer.start()
        self.erut_session.refresh_status()
        self.ros_status.call_command("home")

    #: 접기를 살피는 주기 [ms].
    HOME_WATCH_MS = 500
    #: 홈 명령을 노드가 받은 뒤 이만큼 지나야 홈 플래그를 믿는다 [s] — 노드는
    #: 움직이기 전에 플래그를 내리는데 RCS 는 10 Hz 로 읽어 그 순간을 놓칠 수 있다.
    HOME_SETTLE_S = 1.0

    def _watch_home_fold(self) -> None:
        """보낸 홈 이동이 끝났는지 본다. 끝나면 「접는 중」을 풀고, 시간을 넘기면 E2002.

        예전에는 홈 플래그가 0 → 1 로 바뀌는 것만 도착으로 봤다. 이미 홈에 있던
        팔이면(일시정지 직후 abort 등) 그 변화를 못 봐 deployed 로 굳었다(ERUT
        Q-19, 2026-10-06 16:29). 이제는 노드가 명령을 받은 뒤 잠시 지나 홈 플래그가
        1 이고 태스크가 쉬고 팔이 서 있으면 도착이다.
        """
        if not getattr(self, "_home_move_pending", False):
            self._home_watch_timer.stop()
            return
        now = time.monotonic()
        acked = getattr(self, "_home_acked_at", None)
        if (acked is not None and now - acked >= self.HOME_SETTLE_S
                and getattr(self, "_robot_at_home", False)
                and not self._robot_task_running() and self._robot_still()):
            self._home_fold_done()
            return
        within_s = self._home_within_ms() / 1000.0
        if getattr(self, "_unpark_sent", False):
            within_s += self._unpark_extra_ms() / 1000.0     # 받침대에서 되돌리기는 더 오래 걸린다
        if now - getattr(self, "_home_sent_at", now) > within_s:
            self._raise_home_failed(
                f"{within_s:.0f}초 안에 이동 안전 자세(home)로 거두지 못했습니다")

    def _home_fold_done(self) -> None:
        self._home_move_pending = False
        if getattr(self, "_home_watch_timer", None) is not None:
            self._home_watch_timer.stop()
        if "E2002" in self.erut_session.error_codes():
            self.erut_session.clear_error("E2002")
        self.erut_session.refresh_status()
        self._reload_scan_after_unpark()

    def _raise_home_failed(self, detail: str) -> None:
        """표준 장애 E2002 HOME_FAILED (if-0.8). home 이 되면 같은 code 로 풀린다.

        「아직 접는 중」 과 「접기가 멈춤」 을 ERUT 가 가릴 수 있게 한다. 초기화(reset)
        하면 다시 거둔다.
        """
        if "E2002" in self.erut_session.error_codes():
            return
        self.main_screen.show_activity(f"E2002 HOME_FAILED — {detail}")
        self._add_alarm("E2002", f"홈 거두기 실패 — {detail}")
        self.erut_session.raise_error({
            "code": "E2002", "name": "HOME_FAILED", "level": "stop",
            "recovery": "reset_required", "detail": detail,
        })

    def _zero_scan_outputs(self, reason: str) -> None:
        """정지 · 초기화 — TPAC 에 내보내는 스캔 좌표를 0 으로 비우고 화면 점도 원점에 둔다.

        스캔 도중 로봇 태스크가 서면 280~287 에 마지막 스캔 좌표가 굳어 TPAC 에 그대로
        나갔다(2026-11-12). 다음 작업이 시작되면(로봇 진행 상태가 바뀌면) 저절로 풀린다.
        """
        self.screens["tpac_bridge"].hold_scan_zero(reason)
        self._park_position()

    def _park_position(self) -> None:
        """현재 위치를 영점에 세우고, 로봇이 다시 좌표를 낼 때까지
        (생존 카운터가 움직일 때까지) 굳은 좌표를 무시한다."""
        self._position_stale = True
        self.main_screen.rect_view.park_position()

    # ---- 일시정지가 실제로 섰는가 · 자리를 지키는가 (if-0.6) ------------------
    #: 이만큼 안 움직이면 팔이 선 것으로 본다 [mm, 한 번 받을 때(10 Hz)].
    TCP_STILL_MM = 0.2
    #: 이만큼 가만히 있어야 섰다고 본다 [s].
    STILL_FOR_S = 0.5
    #: 선 뒤로 이만큼 넘게 옮겨지면 「밀렸다」 — 그 자리로 못 돌아간다 [mm].
    PUSHED_TCP_MM = 2.0
    PUSHED_MOTION_MM = 5.0
    #: 차량이 섰다고 볼 상태값(vehicle/robot_status 의 state).
    _VEHICLE_STOPPED = ("PAUSE", "PAUSED", "STOP", "HOLD", "FINISH")

    @staticmethod
    def _tcp_distance(a: list, b: list) -> float:
        if len(a) < 3 or len(b) < 3:
            return 0.0
        return math.dist([float(v) for v in a[:3]], [float(v) for v in b[:3]])

    def _robot_still(self) -> bool:
        """팔이 멈춰 있는가. 자세를 한 번도 못 받았으면(ROS 없음) 섰다고 본다."""
        moved_at = getattr(self, "_tcp_moved_at", None)
        return moved_at is None or time.monotonic() - moved_at >= self.STILL_FOR_S

    def _pause_settled(self) -> bool:
        """일시정지 뒤 장비가 실제로 다 섰는가 — 섰을 때만 activity=paused (if-0.6).

        팔: 태스크가 실행 중(1)이 아니고 TCP 가 가만히 있다(pause 를 받아도
        감속하는 동안은 움직인다). 차량: 더미면 이동이 끝났고, 실제 차량이면
        차량이 보고한 상태가 PAUSED/STOP/HOLD 다. 리프트·아웃트리거: 이동 중이
        아니다(일시정지면 그 자리에 세운다 — _pause_motions, 2026-11-12).
        """
        holding_at_point = self.mark_runner.paused and self.mark_runner.step == "marking"
        if (getattr(self, "_robot_task_state", 0) == self._TASK_STATE_RUNNING
                and not holding_at_point):
            # 마킹 점에서 기다리는 중이면 태스크는 돌지만(278 대기) 팔은 서 있다.
            return False
        if not self._robot_still():
            return False
        if any(m.moving for m in (self.lift, self.outrigger, self.retractor)):
            return False
        # ERUT 정지 뒤 로봇 홈을 기다리는 동작(리프트 0)이 남아 있으면 아직 다 선 것이 아니다(2026-11-24).
        # 일시정지 때 미뤄 둔 것은 재개 때 하므로 세지 않는다.
        if self._pending_motions and self.erut_session.stopping:
            return False
        if self.amr.active == "vehicle":
            state = (self.vehicle.last_status or {}).get("state")
            if self.amr.moving and state not in self._VEHICLE_STOPPED:
                return False
            if state == "RUNNING":
                return False
        elif self.amr.moving:
            return False
        return True

    def _pause_anchor_now(self) -> tuple:
        return (self.amr.position, self.lift.position,
                list(getattr(self, "_last_tcp_pose", None) or []))

    def _on_pause_tick(self, *_args) -> None:
        """일시정지 중이면 선 순간의 자리를 잡아 두고 ERUT 상태를 맞춘다.

        자세(10 Hz)·태스크 상태·차량 상태·이동 도착마다 불린다. evt/status 는
        값이 바뀔 때만 나가므로 자주 불러도 된다.
        """
        if getattr(self.erut_session, "stopping", False):
            # abort 뒤 서는 중 — 다 서는 순간 idle 을 바로 낸다(if-0.10 탭11 Q-35).
            self.erut_session.refresh_status()
        paused = (self.sequencer.state is SequencerState.PAUSED
                  or getattr(self.erut_session, "_paused_work", ""))
        if not paused:
            self._pause_anchor = None
            return
        anchor = getattr(self, "_pause_anchor", None)
        if self._pause_settled():
            if anchor is None:
                self._pause_anchor = self._pause_anchor_now()
            elif not anchor[2] and getattr(self, "_last_tcp_pose", None):
                # 선 자리를 잡을 때 자세가 아직 없었다 — 처음 받은 자세로 채운다.
                self._pause_anchor = (anchor[0], anchor[1], list(self._last_tcp_pose))
        self.erut_session.refresh_status()

    def _pause_holds_position(self) -> bool:
        """선 자리를 그대로 지키고 있는가 — False 면 resumable=false (if-0.6).

        * 스캔 중 멈췄는데 태스크가 일시정지(2)가 아니다 — pause 를 거절해
          stop 했거나, 펜던트에서 멈췄다. 그 자리에서 이을 수 없다.
          (play 를 보내기 전에 멈춘 것은 아직 아무것도 안 했으니 괜찮다.)
        * 선 뒤로 팔(TCP)이나 차량·리프트가 옮겨졌다 — 밀렸다.
        """
        if (self.sequencer.state is SequencerState.PAUSED
                and self.sequencer.resume_state is SequencerState.SCANNING
                and not getattr(self, "_paused_before_play", False)
                and getattr(self, "_robot_task_state", 0) != self._TASK_STATE_PAUSED):
            return False
        if (self.mark_runner.paused and self.mark_runner.step == "robot"
                and getattr(self, "_robot_task_state", 0) != self._TASK_STATE_PAUSED):
            # 점으로 가던 로봇을 세웠는데 태스크가 일시정지가 아니다 — 못 잇는다.
            return False
        anchor = getattr(self, "_pause_anchor", None)
        if anchor is None:
            return True
        amr, lift, tcp = anchor
        now_amr, now_lift, now_tcp = self._pause_anchor_now()
        if abs(now_amr - amr) > self.PUSHED_MOTION_MM or abs(now_lift - lift) > self.PUSHED_MOTION_MM:
            return False
        if tcp and now_tcp and self._tcp_distance(tcp, now_tcp) > self.PUSHED_TCP_MM:
            return False
        return True

    def _pause_robot_scan(self) -> None:
        """스캔 중 일시정지 — 로봇 태스크를 29999 `pause` 로 그 자리에 세운다.

        stop 이 아니다. stop 은 태스크를 끝내 버려 재개가 처음(3점 측정)부터
        다시 시작된다. pause 는 줄 한가운데서도 그 자리에 서고, 재개(`play`)
        하면 멈춘 곳에서 잇는다.

        play 를 아직 안 보냈으면(시작 직후 대기 중) 태스크는 이미 서 있다 —
        play 만 거둬 두고 재개 때 처음부터 튼다.
        """
        timer = getattr(self, "_play_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
            self._resume_needs_restart = True
            self._paused_before_play = True
            self.main_screen.show_activity("일시정지 — 로봇은 아직 출발 전입니다.")
            return
        self._resume_needs_restart = False
        self._paused_before_play = False
        self._pause_sent = True
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("pause")
        self.main_screen.show_activity("로봇을 그 자리에 일시정지합니다(pause).")

    def _resume_robot_scan(self) -> None:
        """일시정지한 자리에서 잇는다 — 29999 `play`.

        태스크가 일시정지(500 = 2)여야 그 자리에서 이어진다. 그 사이 펜던트·
        장애 등으로 태스크가 **중지**됐으면 이어 갈 자리가 없으므로 구간을
        처음부터 다시 튼다(알리고 한다).
        """
        if getattr(self, "_resume_needs_restart", False):
            self._resume_needs_restart = False
            self._start_robot_scan()
            return
        state = getattr(self, "_robot_task_state", None)
        if state == self._TASK_STATE_PAUSED:
            self._sync_scan_ratio(alarm=True)
            self.ros_status.call_command("remote_control_on")
            self.ros_status.call_command("play")
            self.main_screen.show_activity("재개 — 로봇이 멈춘 자리에서 잇습니다(play).")
            return
        if state == self._TASK_STATE_RUNNING:
            # pause 가 안 먹었다(그동안 계속 돌았다). 더 할 것 없다.
            self.main_screen.show_activity("재개 — 로봇 태스크가 이미 돌고 있습니다.")
            return
        text = (f"재개 — 로봇 태스크가 일시정지 상태가 아니라"
                f"{self._task_state_note()} 멈춘 자리에서 이을 수 없습니다. "
                f"{self.sequencer.current_cell()} 구간을 처음부터 다시 합니다.")
        self.main_screen.show_activity(text)
        self._add_alarm("E9208", text)
        self._start_robot_scan()

    def _on_pause_command_result(self, name: str, ok: bool, message: str) -> None:
        """pause 를 로봇이 거절하면 세우기만은 반드시 한다 — stop 으로 대신한다.

        이 경우 태스크가 끝나므로 재개는 구간을 처음부터 다시 한다.
        """
        if name != "pause" or not getattr(self, "_pause_sent", False):
            return
        self._pause_sent = False
        if ok:
            return
        text = f"로봇이 일시정지(pause)를 거절했습니다 — {message}. 대신 정지(stop)합니다."
        self.main_screen.show_activity(text)
        self._add_alarm("E9207", text)
        self._resume_needs_restart = True
        self._stop_robot_scan()

    def _stop_robot_scan(self) -> None:
        """로봇 태스크를 멈춘다. 장애·정지·abort 가 여기로 모인다.

        순회(시퀀서)를 멈추는 것만으로는 로봇이 서지 않는다 — 로봇은 자기
        태스크를 계속 돌리기 때문이다. 장애가 로봇 자신이 아니라 차량·리프트·
        배터리 쪽에서 나도 팔은 계속 벽을 훑게 되므로, 멈춤은 반드시 로봇까지
        내려가야 한다.

        여기는 **끝내는** 멈춤(장애·정지·abort)이다. 스캔 중 일시정지는
        `_pause_robot_scan()` 이 29999 `pause` 로 그 자리에 세우고, 재개는
        `play` 로 잇는다. 원격 제어 모드가 아니면 컨트롤러가 stop 을 거부하므로
        (`not supported in local control mode`) 먼저 켜 준다.

        같은 정지가 여러 경로로 겹쳐 들어올 수 있는데(장애 + 순회 일시정지),
        `stop` 은 멱등이라 여러 번 나가도 문제없다.
        """
        self.ros_status.call_command("remote_control_on")
        self.ros_status.call_command("stop")
        # 멈춘 로봇의 290 은 옛 값이다 — 다음 출발 전까지 접촉으로 치지 않는다.
        self._contact_armed = False
        self.erut_session.set_contact(False)

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
        self._show_erut_layout()

    def _erut_layout(self):
        """ERUT 구간의 모재 전체 배치 — (열 수, 층 수, 열, 층, 이 층에서 끝낸 열들).

        열 수 = 둘레(π × 모재 지름) / (가로 - 겹침), 층 수 = 높이 / (세로 - 겹침)
        (둘 다 올림). ERUT 작업이 아니거나 모재 크기를 모르면 None.
        """
        plan = self.sequencer.plan
        if plan is None or plan is not getattr(self, "_erut_layout_plan", None):
            return None
        _diameter_m, height_m = self.main_screen.orbit_view.target_dimensions()
        circumference = self._target_length_mm()       # 곡판은 판 가로
        if circumference <= 0 or plan.cell_width <= 0:
            return None
        columns = max(1, math.ceil(circumference / plan.column_pitch - 1e-6))
        rows = max(1, math.ceil(height_m * 1000.0 / plan.lift_pitch - 1e-6))
        column, row = plan.section_column, plan.section_row
        # ERUT 계획이 먼저다 — 모재 높이로 센 층 수보다 위 구역도 한다(2026-11-26). 표시도 그만큼 늘린다.
        rows = max(rows, row + 1)
        done = {c for c, r in getattr(self, "_erut_done_sections", set()) if r == row}
        return columns, rows, column, row, done

    def _show_erut_layout(self) -> None:
        """ERUT 구간을 모재 전체에서의 자리로 보여 준다.

        ERUT 는 구간을 하나씩 보내므로 시퀀서 안에서는 늘 1/1 이다. 그대로
        그리면 원주 전체가 한 칸으로 칠해진다. 둘레(π × 모재 지름)를 area 가로
        길이로 잘라 그리고(구간끼리는 겹침만큼 덜 떨어진다), 지금 구간을 실제
        자리에, 층은 높이 / (세로 - 겹침) 으로 센다.
        """
        layout = self._erut_layout()
        if layout is None:
            return
        columns, rows, column, row, done = layout
        plan = self.sequencer.plan
        circumference = self._target_length_mm()       # 곡판은 판 가로 — 링의 판 몫에 그린다
        view = self.main_screen.orbit_view
        view.set_sections(circumference, plan.cell_width, plan.column_pitch,
                          column % columns, done)
        self.main_screen.set_grid_position(column + 1, columns, row + 1, rows)
        self.main_screen.set_total_cells(columns)
        self.main_screen.set_work_cell_label(
            f"{cell_label(column, row)} (구간 {column + 1}/{columns} · 층 {row + 1}/{rows})")

    def _show_sequencer_state(self, _state_name: str) -> None:
        """시퀀서 상태가 바뀔 때마다 화면 진행 단계를 맞춘다."""
        self._sync_cycle_display()
        self._show_erut_layout()

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
        phase = _SEQUENCER_PHASES.get(seq.state, CyclePhase.INSPECTING)
        layout = self._erut_layout()
        if layout is not None:
            # ERUT 구간은 시퀀서 안에서 늘 1/1 이다 — 모재 전체에서의 번호와
            # 이 층에서 끝낸 구간 수로 보여 준다(원주 진행률).
            columns, _rows, column, _row, done = layout
            self.simulator.apply_external_state(
                phase, current_segment=column + 1, completed_segments=len(done),
                total_segments=columns)
            return
        column = seq.cell_ordinal() - 1
        column_index = column // plan.row_count if plan.row_count else 0
        self.simulator.apply_external_state(
            phase,
            current_segment=column_index + 1,
            completed_segments=column_index,
        )

    def _finish_job(self) -> None:
        """전체 격자를 다 돌면 검사 사이클도 함께 멈춘다."""
        plan = self.sequencer.plan
        if plan is not None and plan is getattr(self, "_erut_layout_plan", None):
            # ERUT 구간 하나를 끝냈다 — 원주 표시에서 완료로 칠한다.
            done = getattr(self, "_erut_done_sections", set())
            done.add((plan.section_column, plan.section_row))
            self._erut_done_sections = done
            self._show_erut_layout()
        self.simulator.stop_cycle()
        self.main_screen.show_activity("전체 격자 스캔을 완료했습니다.")

    def _apply_stored_settings(self, scope: str, values: dict) -> None:
        """DB 조회 결과를 해당 설정 범위의 소유 화면으로 전달한다."""
        if scope == "mode_current":
            last = values if isinstance(values, dict) else {}
            if last.get("slot"):
                self._last_mode = {"slot": int(last["slot"]), "name": str(last.get("name", ""))}
                self.main_screen.set_current_mode(f"슬롯 {last['slot']} · {last.get('name', '')} (불러오는 중)")
            return
        if scope == "tpac_bridge":
            if values:
                self.screens["tpac_bridge"].apply_server_settings(values)
            return
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
                # 모양 · 판 가로(2026-11-18)는 예전 저장값에 없다 — 없으면 원통이다.
                self.main_screen.orbit_view.set_target_shape(
                    str(values.get("shape") or SHAPE_CYLINDER), float(values.get("width_m") or 0.0))
                self.main_screen.orbit_view.set_target_dimensions(
                    float(values["diameter_m"]), float(values["height_m"])
                )
            return
        if scope == "work_area":
            keys = ("width_mm", "height_mm", "scan_h_mm", "overlap_mm")
            if all(k in values for k in keys):
                eoat_w_mm = values.get("eoat_w_mm")
                # 반지름은 저장값이 아니라 검사 대상 바깥 지름에서 구한다(옛 설정의
                # radius_mm · thickness_mm 은 읽지 않는다 — 2026-10-19). 검사 대상은
                # 작업 영역보다 먼저 불러온다.
                radius_mm = self._outer_radius_mm()
                self.main_screen.set_work_area(
                    *(float(values[k]) for k in keys),
                    eoat_w_mm=float(eoat_w_mm) if eoat_w_mm is not None else None,
                    radius_mm=radius_mm,
                )
                # 화면만 맞추면 로봇은 예전 레지스터 값으로 계속 돈다
                # (겹침을 바꿨는데 로봇이 옛 행 수로 도는 원인이었다).
                # 저장된 값을 로봇에도 그대로 밀어 준다.
                self._work_area_extra = {
                    "eoat_w_mm": float(values.get("eoat_w_mm") or 0.0),
                    "eoat_h_mm": float(values.get("eoat_h_mm") or 0.0),
                    "eoat_type": float(values.get("eoat_type") or 0.0),
                }
                scan_h_mm = float(values["scan_h_mm"])
                # 홀더 유효 높이는 기본값이다 — 예전 설정(스캔 상승 높이 제한, 2026-11-06 에
                # 뺐다)으로 줄여 저장된 값이 남아 있으면 기본값으로 되돌린다.
                probes = int(self._work_area_extra["eoat_type"])
                if probes in PROBE_COVERAGE_MM:
                    cover_w, cover_h = PROBE_COVERAGE_MM[probes]
                    self._work_area_extra.update(eoat_w_mm=cover_w, eoat_h_mm=cover_h)
                    scan_h_mm = cover_h
                    self.main_screen.set_work_area(
                        float(values["width_mm"]), float(values["height_mm"]), scan_h_mm,
                        float(values["overlap_mm"]), eoat_w_mm=cover_w, radius_mm=radius_mm)
                self._show_eoat_choice(values.get("eoat_type"))
                # 마지막 작업 계획의 좌 · 우 끝 상승(ERUT 또는 화면 입력, 2026-11-12).
                left, right = values.get("pitch_left_mm"), values.get("pitch_right_mm")
                side = self._set_side_pitch(
                    *((left, right) if left is not None and right is not None else (-1, -1)))
                self._push_work_area_to_robot(
                    float(values["width_mm"]), float(values["height_mm"]),
                    scan_h_mm, float(values["overlap_mm"]),
                    radius_mm,
                    self._work_area_extra["eoat_w_mm"],
                    self._work_area_extra["eoat_h_mm"],
                    self._work_area_extra["eoat_type"],
                    *(side if side else (-1.0, -1.0)),
                )
            return
        if scope == "scan_speed":
            ratio = values.get("ratio")
            if isinstance(ratio, (int, float)) and SPEED_MIN <= int(ratio) <= SPEED_MAX:
                self._local_scan_ratio = int(ratio)
                self._local_scan_source = str(values.get("source") or "속도 바")
            return
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.apply_values(values)
        if scope == "system":
            self._apply_system_settings(scope, values)
        if scope in ("vehicle", "inspection_target"):
            self._apply_vehicle_settings()
        # 예전에는 이 줄들이 위 if 안에 들어가 있어 한 번도 돌지 않았다 —
        # 저장된 차량 제어 방식과 브로커 주소가 시작할 때 반영되지 않았다.
        if scope == "connection":
            self._sync_cobot_endpoint()
            self._apply_vehicle_mode()
            self._sync_broker_endpoints()
            self._start_erut_link()
            # 저장된 로봇 주소로 저절로 붙는다(「연결」을 안 눌러도).
            self._auto_connect_robot()
            # AGV 제어 노드(내장) — ROS 탐색이 한 바퀴 돈 뒤에 띄운다(다른 PC 의 노드가 이미 있으면 안 띄운다).
            _call_later(self, self.AGV_NODE_START_DELAY_MS, self._apply_agv_node)

    #: 로봇 화면을 여는 함수(시험에서 바꿔 끼운다).
    _vnc_opener = staticmethod(open_robot_vnc)

    def _open_robot_vnc(self) -> None:
        """「로봇 VNC 열기」 — 티치 펜던트 대신 로봇 화면을 VNC 로 연다(2026-11-05).

        Windows · WSL 은 RealVNC Viewer, 우분투는 Remmina. 연결 설정 입력칸의 IP 를 쓴다.
        """
        screen = self.screens["connection"]
        _ok, message = self._vnc_opener(screen.robot_ip())
        screen.set_link_message(message)
        self.main_screen.show_activity(message)

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
        self._robot_manual_disconnect = False
        conn.set_link_message(f"{ip} 로 연결하는 중…")
        if not self.ros_status.set_robot_endpoint(ip, port):
            conn.set_link_message(
                "로봇 제어 노드가 응답하지 않습니다. 노드가 떠 있는지 확인하세요.")
            return
        self._robot_endpoint_sent = (ip, port)
        if not getattr(self, "_robot_link_up", False):
            conn.set_connecting(True)        # 붙는 동안 [연결 해제]로 시도를 멈출 수 있다(2026-11-26)
        self._handle_cobot_command("connect")
        tpac_screen = self.screens["tpac_bridge"]
        if tpac_screen.poller is None:
            tpac_screen._start_robot()

    def _on_robot_connect_result(self, name: str, ok: bool, message: str) -> None:
        """로봇 연결 명령이 실패했다 — 「연결 중」을 푼다. 자동 재연결이 다시 시도하면 다시 건다."""
        if name == "connect" and not ok and not getattr(self, "_robot_link_up", False):
            self.screens["connection"].set_connecting(False)

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
            detail = self._recent_link_loss()
            text = (f"로봇 연결이 끊겼습니다{f'({detail})' if detail else ''} — 연결 가능해지면 자동으로 "
                    "다시 연결합니다.")
            if not self._robot_link_alarm_recent() and not self._robot_link_quiet():
                self._add_alarm("E9205", text)
                self._robot_link_alarm_at = time.monotonic()
        conn.set_link_message(text)
        self.main_screen.show_activity(text)

    # ---- 로봇 포트별 끊김 알림 (2026-11-24) -----------------------------------------
    #: 같은 끊김을 두 번 알람으로 올리지 않는 시간 [s] — 포트 알림과 연결 상태 알림이 거의 같이 온다.
    ROBOT_LINK_ALARM_GAP_S = 3.0

    #: ROS 도메인을 바꿔 노드를 다시 띄우는 동안 로봇 끊김을 알람으로 올리지 않는 시간 [s](2026-11-26).
    ROBOT_LINK_QUIET_S = 30.0

    def _robot_link_quiet(self) -> bool:
        """일부러 다시 붙이는 중(ROS 도메인 바꿈)이라 끊김 알람을 띄우지 않는가."""
        return time.monotonic() < getattr(self, "_robot_link_quiet_until", 0.0)

    def _robot_link_alarm_recent(self) -> bool:
        return time.monotonic() - getattr(self, "_robot_link_alarm_at", -1e9) < self.ROBOT_LINK_ALARM_GAP_S

    def _recent_link_loss(self) -> str:
        """방금 온 「끊김 …」 알림의 내용(포트 · 까닭). 없으면 빈 글자."""
        text = getattr(self, "_robot_link_detail", "")
        if text.startswith("끊김") and time.monotonic() - getattr(self, "_robot_link_detail_at", -1e9) \
                < self.ROBOT_LINK_ALARM_GAP_S:
            return text[len("끊김"):].strip()
        return ""

    def _on_robot_link_detail(self, text: str) -> None:
        """로봇 노드가 알린 포트별 끊김 · 재연결 — 29999 · 30001 · 502 중 하나라도 끊기면 알린다."""
        text = str(text or "").strip()
        if not text or text == getattr(self, "_robot_link_detail", ""):
            return                                     # 붙으려다 실패하는 동안 같은 글이 되풀이된다
        self._robot_link_detail = text
        self._robot_link_detail_at = time.monotonic()
        if getattr(self, "_robot_disconnect_requested", False) or getattr(self, "_robot_manual_disconnect", False):
            return                                     # 작업자가 끊었다
        conn = self.screens["connection"]
        if text.startswith("끊김"):
            message = f"로봇 연결 끊김 — {text[len('끊김'):].strip()} · 자동으로 다시 연결합니다."
            conn.set_link_message(message)
            self.main_screen.show_activity(message)
            # 붙어 있다가 끊긴 때만 알람(처음 붙으려다 실패하는 것은 연결 화면 글로만).
            if (getattr(self, "_robot_link_up", False) and not self._robot_link_alarm_recent()
                    and not self._robot_link_quiet()):
                self._add_alarm("E9205", message)
                self._robot_link_alarm_at = time.monotonic()
        elif text.startswith("다시 연결됨"):
            message = f"로봇 {text[len('다시 연결됨'):].strip()} 다시 연결됐습니다."
            conn.set_link_message(message)
            self.main_screen.show_activity(message)

    def _disconnect_robot(self) -> None:
        """연결한 경로를 같이 끊는다."""
        self._robot_disconnect_requested = True
        self._robot_manual_disconnect = True
        tpac_screen = self.screens["tpac_bridge"]
        if tpac_screen.poller is not None:
            tpac_screen._stop_robot()
        conn = self.screens["connection"]
        trying = getattr(conn, "_connecting", False)
        conn.set_connecting(False)
        self._handle_cobot_command("disconnect")
        conn.set_link_message("연결 시도를 멈췄습니다 — 값을 고친 뒤 다시 「연결」하세요." if trying
                              else "연결을 끊었습니다.")

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
        node = getattr(self, "robot_node", None)
        if node is not None:
            # 다음에 띄울 때 이 주소로 띄운다. 떠 있는 노드에는 연결할 때 넘긴다(_connect_robot).
            node.robot_ip = ip
            node.modbus_port = int(port or 502)

    #: 연결 설정을 불러온 뒤 로봇 제어 노드가 뜨기를 기다리며 자동 연결을 다시 해 보는 횟수 · 간격 [ms].
    ROBOT_AUTO_CONNECT_TRIES = 20
    ROBOT_AUTO_CONNECT_RETRY_MS = 1000

    def _auto_connect_robot(self, attempt: int = 0) -> None:
        """저장된 로봇 주소로 저절로 붙는다(2026-11-24) — 「연결」을 누르지 않아도.

        연결 설정을 불러왔을 때 · 저장했을 때 부른다. 노드가 아직 안 떴으면(막 띄웠다) 잠깐씩 기다렸다
        다시 해 본다. 이미 같은 주소로 붙어 있거나 작업자가 「연결 해제」를 눌렀으면 하지 않는다.
        """
        if not getattr(self, "_ros_started", False) or getattr(self, "_robot_manual_disconnect", False):
            return
        conn = self.screens["connection"]
        target = (conn.robot_ip(), conn.robot_port())
        if not target[0]:
            return
        if getattr(self, "_robot_link_up", False) and getattr(self, "_robot_endpoint_sent", None) == target:
            return
        if not self.ros_status.robot_params_ready():
            if attempt < self.ROBOT_AUTO_CONNECT_TRIES:
                _call_later(self, self.ROBOT_AUTO_CONNECT_RETRY_MS,
                            lambda: self._auto_connect_robot(attempt + 1))
            else:
                conn.set_link_message("로봇 제어 노드가 응답하지 않습니다 — 노드가 떠 있는지 확인하고 「연결」을 누르세요.")
            return
        self._connect_robot()

    def _sync_broker_endpoints(self) -> None:
        """연결 설정의 ERUT 접속 정보를 실제 접속에 반영한다.

        주소를 코드나 환경변수가 아니라 화면에서 정할 수 있어야 한다. 접속 중이면
        끊고 새 값으로 다시 붙는다(값이 그대로면 그냥 둔다). 주소나 장치 ID 를
        비우면 끊고 붙지 않는다 — 비워 두는 것도 허용한다(RCS 단독, 2026-10-14).
        """
        erut = self.screens["connection"].erut_endpoint()
        # 접속 ID 는 장치 ID 로 정한다(3s-<장치 ID>) — 장치 ID 를 바꾸면 같이 바뀐다.
        config = replace(
            self.erut.config, client_id="",
            host=erut["host"], port=erut["port"], device_id=erut["device_id"],
            username=erut["username"], password=erut["password"],
        )
        was_halted = self.erut.halted
        if not self.erut.apply_config(config):
            return
        if not (erut["host"] and erut["device_id"]):
            self.main_screen.show_activity(
                "ERUT 접속 주소 · 장치 ID 가 비어 있어 ERUT 에 붙지 않습니다.")
        else:
            self.main_screen.show_activity(
                f"ERUT({erut['host']}:{erut['port']}, 장치 {erut['device_id']}) 에 "
                + ("다시 붙어 봅니다." if was_halted and config == self.erut.config
                   else "접속하도록 바꿨습니다."))
        self._show_erut_link()

    def _mark_settings_saved(self, scope: str) -> None:
        """저장 완료 후 해당 화면의 상태를 갱신한다."""
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.mark_saved()
            if scope == "connection":
                self._apply_ros_domain()
                self._sync_cobot_endpoint()
                self._apply_vehicle_mode()
                self._sync_broker_endpoints()
                self._apply_agv_node()
                self._auto_connect_robot()
        elif scope == "inspection_target":
            self.main_screen.show_activity("검사 대상 크기를 저장했습니다.")

    def _show_settings_error(self, scope: str, message: str) -> None:
        """Python 스택 추적을 노출하지 않고 저장소 오류를 표시한다."""
        if scope == "connection":
            # 저장된 주소를 못 읽었으면 화면의 기본값으로라도 붙는다.
            self._start_erut_link()
        screen = self._settings_screens.get(scope)
        if screen is not None:
            screen.show_storage_error(message)
        elif scope == "inspection_target":
            self.main_screen.show_activity(f"설정 저장소 오류: {message}")

    def _begin_grid_job(self, grid, scan_h_mm: float, source: str = "RCS",
                        job_id: str = "") -> None:
        """RCS '검사 시작'의 격자 순회를 시작한다(ERUT 구역은 _start_erut_job).

        체크한 판(센서/논센서)의 스캔 태스크를 먼저 불러 두고, 화면 진행
        표시는 시퀀서가 주도하게 한다(데모 타이머로 혼자 앞서 나가지 않게).
        """
        grid = self._fill_from_local_setup(grid)
        # RCS 격자는 열 수로 똑같이 나눠 그린다.
        self._erut_layout_plan = None
        self.main_screen.orbit_view.clear_sections()
        self.simulator.begin_external(grid.column_count)
        self._push_task_paths()
        self._load_scan_task()
        self._record_job(source, job_id, grid)
        self.sequencer.start(grid, scan_h_mm)

    #: RCS 격자 한계 — 열 1~12, 행 A~F(화면 표시 한계).
    _MAX_COLUMNS = 12
    _MAX_ROWS = 6

    def _start_inspection(self) -> None:
        """RCS '검사 시작' — RCS 설정으로 격자 순회를 시작한다.

        순회에 필요한 값은 RCS 가 가진 값으로 채운다.
          셀 크기·겹침  : 작업 영역 설정 (Modbus 256~259 로 나가는 값)
          반지름          : 검사 대상 바깥 지름 / 2 (두께는 쓰지 않는다)
          EOAT            : 작업 영역의 장비 값
          열·행 수      : 검사 대상 원주 / 열 간격, 높이 / 행 간격 (올림)
                         — 12열, 6행으로 자른다.
        겹침은 격자끼리의 겹침이라 가로·세로 이동량에서 뺀다.
        """
        if (self.sequencer.state not in (
                SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED)
                or self.mark_runner.running):
            self.main_screen.show_activity("이미 작업 중입니다 — 끝나거나 정지한 뒤 시작하세요.")
            return
        blocker = self._vehicle_start_blocker()
        if blocker:
            self.main_screen.show_activity(f"검사 시작 거절 — {blocker}")
            self._add_alarm("E9307", blocker)
            return
        width, height, _scan_h, overlap = self.main_screen.rect_view.work_area()
        if height > MAX_AREA_HEIGHT_MM:
            text = (f"작업 영역 세로 {height:g} mm 는 한 구간 최대 {MAX_AREA_HEIGHT_MM} mm 를"
                    " 넘어 시작하지 않습니다 — 작업 영역 세로를 줄이세요.")
            self.main_screen.show_activity(text)
            self._add_alarm("E9404", text)
            return
        if self._side_pitch:
            rows_needed = len(scan_row_heights(round(height), *self._side_pitch)) - 1
            if rows_needed > SCAN_ROW_LIMIT:
                text = (f"좌·우 끝 상승 {self._side_pitch[0]:g}·{self._side_pitch[1]:g} mm 로는 "
                        f"높이 {height:g} mm 에 줄이 {SCAN_ROW_LIMIT} 개를 넘어 시작하지 않습니다 — "
                        "작업 영역에서 상승 높이를 키우거나 0 (자동)으로 두세요.")
                self.main_screen.show_activity(text)
                self._add_alarm("E9404", text)
                return
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
            radius=self._outer_radius_mm(),
            eoat_probes=int(float(extra.get("eoat_type", 0) or 0)),
            # 줄 높이도 마지막 작업 계획(ERUT 에서 받았거나 화면에서 넣은 좌 · 우 끝 상승) 그대로.
            pitch_left=self._side_pitch[0] if self._side_pitch else None,
            pitch_right=self._side_pitch[1] if self._side_pitch else None,
        )
        side = (f", 좌·우 끝 상승 {self._side_pitch[0]:g}·{self._side_pitch[1]:g} mm"
                if self._side_pitch else "")
        scan, scan_source = self._scan_ratio_now()
        self.main_screen.show_activity(
            f"RCS 검사 시작: {columns}열 x {rows}행 (셀 {width:.0f} x {height:.0f} mm, "
            f"겹침 {overlap:g} mm{side}, 스캔 속도 {scan} % · {scan_source})")
        self._begin_grid_job(grid, self._scan_band_mm(grid), source="RCS")

    def _toggle_pause(self) -> None:
        """RCS '일시정지' — 실제 순회·로봇도 같이 멈추고 다시 이어간다.

        ERUT pause/resume 과 같다. 순회가 안 돌 때는 예전처럼 데모
        사이클만 토글한다.

        캘리브레이션(RCS 「계측 시작」 · ERUT calibrate) 중에는 **차량**을 세우고 잇는다 — 검사
        중에는 지금처럼 로봇 팔(태스크 pause · play)이다(2026-11-12).
        """
        if self._calibration_in_progress():
            self._toggle_calibration_pause()
            return
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
        self._speed_ratio_sent = percent
        self._apply_global_speed(percent)
        # 사람이 바꾼 속도다 — 다음 RCS 작업의 스캔 속도도 이 값이 된다(ERUT 값을 바꾼다).
        self._remember_scan_ratio(percent, source)
        # 전체 비율은 29999 로만 간다 — 스캔 줄(307)은 따로 맞춘다. ERUT 작업 중이면
        # ERUT 속도를 그대로 지킨다(2026-11-11, 예전에는 바가 307 까지 덮었다).
        _sent, scan, scan_source = self._sync_scan_ratio()
        note = (f" — ERUT 작업 중이라 스캔 줄은 ERUT 속도 {scan} % 그대로입니다."
                if scan_source == "ERUT" else "")
        self.main_screen.show_activity(f"{source} 속도 비율 {percent} % 를 로봇에 보냈습니다.{note}")

    def _apply_scan_speed_ratio(self, percent: int) -> None:
        """ERUT 가 보낸 스캔 속도[%]를 ㄹ자 스캔에만 건다 — 레지스터 307 (2026-11-04).

        예전에는 로봇 전체 속도 비율(29999 `speed -v`)로 걸어 3점 측정 · 대기 위치 ·
        홈 이동까지 같이 느려졌다. 이제 스캔 줄(servoj 호)과 줄 바꿈만 이 비율을 따르고,
        스캔 밖 이동은 「작업 속도」 · 펜던트 이동 속도(v_move)로 간다.

        ERUT 작업이 걸려 있는 동안은 이 값이 307 의 주인이다 — 속도 바 · 설정 저장 ·
        재연결 복원이 덮지 않고, play 직전에 다시 써 둔다(`_sync_scan_ratio`, 2026-11-11).
        """
        percent = int(percent)
        if not SPEED_MIN <= percent <= SPEED_MAX:
            self.main_screen.show_activity(
                f"스캔 속도 비율은 {SPEED_MIN}~{SPEED_MAX} % 여야 합니다: {percent}")
            return
        self._erut_scan_ratio = percent
        # 마지막으로 받은 ERUT 속도를 남긴다 — RCS 「검사 시작」도 이 속도로 돈다(2026-11-12).
        self._remember_scan_ratio(percent, "ERUT")
        # 이 신호는 ERUT 구역을 받아 들이는 도중에 온다 — 세션이 아직 일을 등록하기 전일 수
        # 있으므로 「지금 ERUT 작업 중인가」를 따지지 않고 이 값을 바로 쓴다.
        sent = self._send_scan_ratio(percent, "ERUT")
        mm_s = SCAN_SPEED_MAX * percent // 100
        if sent:
            self.main_screen.show_activity(
                f"ERUT 스캔 속도 {percent} % ({mm_s} mm/s)를 로봇에 보냈습니다 — 스캔 줄에만 걸립니다.")
        else:
            self._add_alarm("E9209",
                f"ERUT 스캔 속도 {percent} % ({mm_s} mm/s)를 로봇에 보내지 못했습니다 — "
                f"제어 노드 · 로봇 연결을 확인하세요. 스캔은 로봇에 남은 속도로 돕니다.")

    def _scan_ratio_now(self) -> tuple[int, str]:
        """ㄹ자 스캔 줄에 걸어야 할 속도 비율과 그 출처.

        ERUT 작업(준비 · 구간)이 걸려 있으면 ERUT 가 준 값. 아니면 마지막으로 정해진 값 —
        ERUT 에서 받았거나 사람이 속도 바 · 설정으로 바꾼 것 중 나중 것(2026-11-12, 저장해
        두어 다시 켜도 남는다). 그것도 없으면 RCS 가 보낸 전체 속도 비율.
        """
        erut = getattr(self, "_erut_scan_ratio", None)
        if erut is not None and self.erut_session.has_work():
            return int(erut), "ERUT"
        local = getattr(self, "_local_scan_ratio", None)
        if local is not None:
            source = getattr(self, "_local_scan_source", "속도 바")
            return int(local), ("ERUT 마지막" if source == "ERUT" else source)
        bar = getattr(self, "_speed_ratio_sent", None)
        if bar is None:
            bar = self.screens["cobot"].speed_ratio()
        return int(bar), "속도 바"

    def _remember_scan_ratio(self, percent: int, source: str) -> None:
        """다음 작업에 쓸 스캔 줄 속도를 남긴다 — ERUT 값이든 사람이 넣은 값이든 나중 것."""
        self._local_scan_ratio = int(percent)
        self._local_scan_source = str(source)
        self.settings_service.save("scan_speed", {"ratio": int(percent), "source": str(source)})

    def _keep_erut_target(self, geometry: dict) -> None:
        """ERUT 구역의 모재 모양 · 크기를 RCS 검사 대상으로 남긴다(다를 때만, 2026-11-12)."""
        old = self._target_geometry()
        changed = old["shape"] != geometry.get("shape") or any(
            abs(float(old.get(key) or 0.0) - float(geometry.get(key) or 0.0)) > 0.5
            for key in ("diameter", "height", "width"))
        if changed:
            self._set_target_geometry(geometry)

    def _send_scan_ratio(self, percent: int, source: str) -> bool:
        """307 에 쓰고 화면의 「스캔 줄」 표시를 맞춘다. 보냈는지 돌려준다."""
        sent = self.ros_status.send_value("scan_speed_ratio", int(percent))
        self.main_screen.set_scan_speed(int(percent), SCAN_SPEED_MAX * int(percent) / 100,
                                        source, sent)
        return sent

    def _sync_scan_ratio(self, alarm: bool = False) -> tuple[bool, int, str]:
        """스캔 줄 속도(307)를 지금 맞는 값으로 다시 쓴다 — (보냈나, 비율, 출처).

        전체 비율을 바꾼 뒤 · 재연결 복원 뒤 · 로봇 play 직전에 부른다. `alarm` 이면 못
        보냈을 때 알람을 띄운다(ERUT 값이 걸려야 하는 자리).
        """
        percent, source = self._scan_ratio_now()
        sent = self._send_scan_ratio(percent, source)
        if not sent and alarm:
            self._add_alarm("E9209",
                f"스캔 속도 {percent} % ({source})를 로봇에 보내지 못했습니다 — "
                f"제어 노드 · 로봇 연결을 확인하세요.")
        return sent, percent, source

    def _show_speed_scale(self, percent: int) -> None:
        """로봇이 실제로 쓰고 있는 속도 비율을 화면에 표시한다.

        펜던트에서 직접 바꿔도 이 값으로 들어온다. Modbus 레지스터 17을
        노드가 읽어 발행한 것이다.
        """
        self.main_screen.set_speed_scale(percent)
        self._speed_scale_percent = int(percent)
        self._apply_global_speed(percent)
        # 접는 시간이 속도 비율을 따라 바뀐다 — ERUT 자기소개의 home_within_ms 를 맞춘다.
        self.erut_session.refresh_info()

    def _apply_global_speed(self, percent: int) -> None:
        """전체 속도 비율 — 코봇(29999 speed -v)과 같은 비율을 차량 주행 · 리프트 속도에도 건다(2026-11-26).

        차량 설정의 주행 속도 · 리프트 속도에 곱해 다음 SetJob · 리프트 명령부터 쓴다(가던 이동은 그대로).
        """
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return
        if not SPEED_MIN <= percent <= SPEED_MAX:
            return
        self._global_speed_percent = percent
        for adapter in (getattr(self, "_vehicle_amr", None), getattr(self, "_vehicle_lift", None)):
            if adapter is not None:
                adapter.speed_scale = percent / 100.0

    #: 속도 비율 100 % 에서 펴진 자세 → home 이 가장 오래 걸릴 때 [ms].
    #: 시험 기록 최대 약 17초(2026-10-02·06)에 여유를 둔 값.
    HOME_WITHIN_MS_AT_FULL_SPEED = 20_000

    def _home_within_ms(self) -> int:
        """지금 속도 비율에서 home 으로 접는 데 가장 오래 걸리는 시간 [ms].

        접는 동작도 로봇 속도 비율을 그대로 따른다(접을 때 따로 올리지 않는다
        — 2026-10-06 결정). 그래서 100 % 기준값을 비율로 나눠 늘린다. 비율을
        모르면(로봇 값 수신 전) 100 % 로 본다.
        """
        percent = int(getattr(self, "_speed_scale_percent", 0) or 100)
        percent = max(SPEED_MIN, min(SPEED_MAX, percent))
        ms = self.HOME_WITHIN_MS_AT_FULL_SPEED * 100 / percent
        return int(-(-ms // 1000) * 1000)        # 초 단위로 올림

    def _remember_joint(self, values: list) -> None:
        """조그 화면 표시와 기준 위치 저장에 쓸 관절값을 기억한다."""
        self._last_joint = list(values)
        self.cobot_jog_screen.apply_joint_position(values)

    def _show_tcp_pose(self, values: list) -> None:
        """현재 TCP 자세를 수동 제어와 조그 화면에 함께 표시한다."""
        previous = getattr(self, "_last_tcp_pose", None)
        if previous is None or self._tcp_distance(previous, values) > self.TCP_STILL_MM:
            self._tcp_moved_at = time.monotonic()
        self._last_tcp_pose = list(values)
        self._on_pause_tick()
        formatted = self._format_pose(values)
        self.cobot_manual_screen.apply_tcp(formatted)
        self.cobot_jog_screen.apply_position(formatted)

    def _show_tcp_pose_zero(self, values: list) -> None:
        """원점 기준 상대 자세(cur-zero)를 화면에 표시한다.

        검사 좌표는 이 값이다. 베이스 프레임 좌표(`tcp_pose`)는 모니터링용이다.
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
                # ㄹ자 줄 위에서는 가로를 로봇이 내는 **호 위 거리**(286)로 그린다 — 경로 폭이 곧
                # 호 길이라 점이 선을 따라간다. 베이스 Y 변위(현)는 호 위 거리에 비례하지 않아
                # 줄 가운데서 점이 앞서거나 처졌다(2026-11-09). 세로는 원점 기준 높이 그대로.
                along_arc = self._arc_position_mm()
                if along_arc is not None:
                    self.main_screen.apply_wall_position(along_arc, values[2], along_arc=True)
                else:
                    self.main_screen.apply_wall_position(values[1], values[2])

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
        "save_standby_pose": ("standby_joint", _JOINT_LABELS),
        "save_start_pose": ("start_pose", _POSE_LABELS),
        # 홀더·EOAT 를 떼고 다는 자세(2026-11-05). 홈에서 movej 로만 오간다.
        "save_assembly_pose": ("assembly_joint", _JOINT_LABELS),
    }

    #: 관절값으로 저장하는 기준 위치(나머지는 TCP pose).
    _JOINT_TARGETS = ("home_joint", "standby_joint", "assembly_joint")

    #: 로봇 TCP 를 툴 플랜지로 맞춘 뒤 TCP 좌표(384~389)가 새로 들어올 때까지 [ms].
    FLANGE_SETTLE_MS = 600

    def _save_reference_pose(self, command: str) -> None:
        """현재 자세를 기준 위치로 기록하고 로봇 레지스터에도 쓴다.

        저장하는 위치는 모두 **툴 플랜지 TCP [0, 0, 0, 0, 0, 0]** 기준이다(2026-11-09) — 홀더
        오프셋과 무관하다. 관절값(홈 · 스캔 대기 · 조립 위치)은 TCP 와 상관없고, TCP 좌표로
        저장하는 시작 포즈(그리퍼 접촉 위치)는 로봇 TCP 를 먼저 플랜지로 맞추고 잠시 뒤 읽는다
        — 태스크가 홀더 끝점 TCP 로 움직이다 멈추면 그 TCP 가 남아 있을 수 있다.
        """
        target, _labels = self.POSE_TARGETS.get(command, (None, ()))
        if target is None:
            return
        if target not in self._JOINT_TARGETS:
            if self._robot_task_running():
                self.cobot_jog_screen.show_result(
                    "로봇 태스크가 도는 중에는 그리퍼 접촉 위치를 저장하지 않습니다 — 멈춘 뒤 저장하세요.")
                return
            if self.ros_status.call_command("flange_tcp"):
                self.cobot_jog_screen.show_result("로봇 TCP 를 툴 플랜지로 맞춘 뒤 저장합니다.")
                QTimer.singleShot(self.FLANGE_SETTLE_MS,
                                  lambda: self._store_reference_pose(command, target))
                return
        self._store_reference_pose(command, target)

    def _store_reference_pose(self, command: str, target: str) -> None:
        values = (
            getattr(self, "_last_joint", None) if target in self._JOINT_TARGETS
            else getattr(self, "_last_tcp_pose", None)
        )
        if not values:
            self.cobot_jog_screen.show_result("로봇에서 현재 값을 아직 받지 못했습니다.")
            return

        entry = {"values": [float(v) for v in values],
                 "saved_at": datetime.now().isoformat(timespec="seconds")}
        self._reference_poses[target] = entry
        self._show_saved_pose(command, entry)
        if target == "start_pose":
            self._sync_contact_height()
        if save_reference_poses({target: entry["values"]}) is None:
            self.cobot_jog_screen.show_result("기준 위치 파일을 저장하지 못했습니다.")
        self.ros_status.send_pose(target, values)

    #: 위치 카드 「이동」이 부르는 노드 명령(홈 · 조립 위치는 기존 경로를 탄다).
    _GOTO_SERVICES = {"save_standby_pose": "move_standby", "save_start_pose": "move_gripper"}

    def _goto_reference_pose(self, command: str) -> None:
        """조그 화면 위치 카드의 「이동」(2026-11-12).

        홈은 메인 「로봇 홈」, 조립 위치는 수동 제어 「조립 위치 이동」과 같은 길이다.
        스캔 대기 위치는 홈 이동처럼 벽에서 물러나 movej, 그리퍼 접촉 위치는 태스크처럼
        대기 위치를 거쳐 접촉 위치 **앞**(probe_dist)까지만 간다 — 마지막은 조그로 본다.
        로봇이 동작 중이거나 차량 · 리프트가 움직이면 보내지 않는다.
        """
        target, _labels = self.POSE_TARGETS.get(command, (None, ()))
        if target is None:
            return
        if command == "save_home_pose":
            self._request_home()
            self.cobot_jog_screen.show_result(self.main_screen.activity_label.text())
            return
        if command == "save_assembly_pose":
            self._request_assembly()
            self.cobot_jog_screen.show_result(self.main_screen.activity_label.text())
            return
        name = "스캔 대기 위치" if command == "save_standby_pose" else "그리퍼 접촉 위치 앞"
        refuse = ""
        if self._robot_busy():
            refuse = "로봇이 동작 중이므로 이동할 수 없습니다 — 정지한 뒤 누르세요."
        elif any(m.moving for m in (self.amr, self.lift)):
            refuse = "차량·리프트가 움직이는 중이라 팔을 움직이지 않습니다."
        if refuse:
            self.cobot_jog_screen.show_result(f"{name} 이동 거절 — {refuse}")
            self.main_screen.show_activity(f"{name} 이동 거절 — {refuse}")
            return
        self.main_screen.show_activity(f"로봇을 {name}(으)로 보냅니다.")
        self.cobot_jog_screen.show_result(f"로봇을 {name}(으)로 보냅니다.")
        self.ros_status.call_command(self._GOTO_SERVICES[command])

    def _delete_reference_pose(self, command: str) -> None:
        """조그 화면 위치 카드의 「삭제」(2026-11-12) — 파일 · 화면 · 로봇 레지스터에서 지운다.

        로봇에는 그 칸을 0 으로 쓴다 — 태스크와 노드는 0 인 칸을 「저장 안 함」으로 보고
        펜던트에서 티칭한 값(Home_joint · Standby_joint · gripper_target_pose)을 쓴다.
        조립 위치는 노드가 잊는다. 태스크가 도는 중에는 지우지 않는다.
        """
        target, _labels = self.POSE_TARGETS.get(command, (None, ()))
        if target is None:
            return
        name = self.cobot_jog_screen.title_of(command)
        if not self._reference_poses.get(target):
            self.cobot_jog_screen.show_result(f"「{name}」은(는) 저장된 값이 없습니다.")
            return
        if self._robot_task_running():
            self.cobot_jog_screen.show_result(
                f"로봇 태스크가 도는 중에는 「{name}」을(를) 지우지 않습니다 — 멈춘 뒤 지우세요.")
            return
        self._reference_poses.pop(target, None)
        self.cobot_jog_screen.set_saved_pose(command, "")
        if target == "start_pose":
            self._sync_contact_height()
        saved = delete_reference_pose(target) is not None
        sent = self.ros_status.send_pose(target, [0] * 6)
        notes = []
        if not saved:
            notes.append("기준 위치 파일을 고치지 못했습니다")
        if not sent:
            notes.append("로봇에 연결되어 있지 않아 다음 연결 때 비웁니다")
        tail = f" ({' · '.join(notes)})" if notes else ""
        self.cobot_jog_screen.show_result(
            f"「{name}」을(를) 지웠습니다 — 로봇은 펜던트에서 티칭한 값을 씁니다.{tail}")

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
        self._sync_contact_height()

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
        # RCS 에 없는(지운) 위치는 로봇 칸을 비운다 — 연결이 끊긴 사이 지웠으면 레지스터에
        # 옛 값이 남아 로봇이 지운 위치로 갔다(2026-11-12). 0 = 펜던트 값을 쓴다.
        for target, _labels in self.POSE_TARGETS.values():
            if not self._reference_poses.get(target):
                self.ros_status.send_pose(target, [0] * 6)

        cobot = self.screens["cobot"]
        # 속도 바의 mm/s 는 ㄹ자 스캔 속도로 보인다(100 % = 100 mm/s). 스캔 외 이동
        # 속도(작업 속도)는 따로라 바의 기준이 아니다(2026-11-04).
        self.main_screen.set_base_speed(SCAN_SPEED_MAX)
        for value, name in ((cobot.linear_speed(), "linear_speed"),
                            (cobot.speed_ratio(), "speed_ratio")):
            if self.ros_status.send_value(name, int(value)):
                restored.append(name)
        self._speed_ratio_sent = int(cobot.speed_ratio())
        # 스캔 줄 속도(307)는 전체 비율과 따로 — ERUT 작업 중이면 ERUT 값을 다시 쓴다.
        # 예전에는 재연결 복원이 307 을 저장해 둔 비율로 덮어 ERUT 속도가 소리 없이 풀렸다.
        if self._sync_scan_ratio(alarm=self.erut_session.has_work())[0]:
            restored.append("scan_speed_ratio")

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
        names = ("save_home_pose", "save_standby_pose", "save_start_pose",
                 "move_home", "linear_speed")
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
        if command == "move_assembly":
            self._request_assembly()
            return
        if command in self.ros_status.COMMAND_SERVICES:
            self.ros_status.call_command(command)
        else:
            self.cobot_manual_screen.activity_label.setText(
                f"'{command}'에 연결된 명령이 없습니다."
            )

    def _request_assembly(self) -> None:
        """「조립 위치 이동」 — 홈에서 조립 위치로 movej (2026-11-05).

        홀더·EOAT 를 떼고 달 때만 쓴다. 홈과 조립 위치 사이는 movej 한 번이라
        **홈에 있을 때만** 보낸다(노드도 한 번 더 확인한다). 돌아올 때는 메인
        화면 「로봇 홈」 — 노드가 조립 위치를 알아보고 movej 홈만 한다.
        """
        refuse = None
        if self._robot_busy():
            refuse = "로봇이 동작 중이므로 조립 위치로 갈 수 없습니다."
        elif not self._reference_poses.get("assembly_joint"):
            refuse = "조립 위치가 저장되어 있지 않습니다. 조그 화면에서 「조립 위치 저장」을 먼저 하세요."
        elif not self._robot_at_home:
            refuse = "홈에서만 조립 위치로 갈 수 있습니다. 먼저 메인 화면 「로봇 홈」으로 보내세요."
        if refuse:
            self.main_screen.show_activity(f"조립 위치 이동 거절 — {refuse}")
            self.cobot_manual_screen.activity_label.setText(refuse)
            return
        self.main_screen.show_activity("로봇을 조립 위치로 보냅니다(movej).")
        self.ros_status.call_command("move_assembly")

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
                    f"작업 속도(스캔 외 이동)를 안전 상한 {MAX_LINEAR_SPEED_MM_S} mm/s 로 제한했습니다."
                )
            self.ros_status.send_value("linear_speed", capped)

        # 속도 비율은 29999로 실시간 반영되는 경로를 탄다.
        ratio = values.get(CobotSettingsScreen.RATIO_FIELD)
        if ratio is not None:
            self._apply_speed_ratio(ratio, source="설정")

    def _show_eoat_choice(self, eoat_type) -> None:
        """Cobot 설정의 「장착 EOAT」 목록을 지금 값에 맞춘다(신호 없이)."""
        cobot = getattr(self, "screens", {}).get("cobot")
        if cobot is not None:
            cobot.set_eoat_type(eoat_type)

    def _apply_eoat_choice(self, probes: int) -> None:
        """Cobot 설정에서 장착 EOAT(0 · 5 · 8)를 골랐다 — 저장하고 로봇에 바로 보낸다.

        ERUT 규격에는 EOAT 종류 칸이 없다(if-0.9). 예전에는 MC 시험 도구가 보냈는데
        MC 를 빼면서 고를 곳이 없어져, 저장된 0 이 그대로 로봇 264 로 가 RCS 로 돌리면
        EOAT 오프셋(eoat5_offset 등)이 안 걸렸다(2026-10-14). 그래서 RCS 설정에서 고른다.

        값은 작업 영역 설정(work_area.eoat_type)에 둔다 — 작업을 시작할 때
        _fill_from_local_setup 이 여기서 채운다. 유효 커버(eoat_w/h)와 스캐너 밴드(258)도
        같이 바꾼다(세로 = Cobot 설정의 스캔 상승 높이 제한). 작업 중이면 지금 구간의
        로봇 값이 중간에 바뀌므로 받지 않고 목록을 되돌린다.
        """
        current = int(float(self._work_area_extra.get("eoat_type") or 0))
        names = dict(CobotSettingsScreen.EOAT_CHOICES)
        probes = int(probes)
        if probes == current:
            return
        if self._job_running() or self._robot_task_running():
            self._show_eoat_choice(current)
            message = "작업 중에는 장착 홀더를 바꿀 수 없습니다 — 작업을 마친 뒤 바꾸세요."
            self.main_screen.show_activity(message)
            self._add_alarm("E9405", message)
            return
        extra = dict(self._work_area_extra)
        extra["eoat_type"] = float(probes)
        width, height, scan_h, overlap = self.main_screen.rect_view.work_area()
        if probes in PROBE_COVERAGE_MM:
            cover_w, cover_h = PROBE_COVERAGE_MM[probes]
            extra["eoat_w_mm"] = cover_w
            extra["eoat_h_mm"] = cover_h
            # EOAT 를 골랐으면 스캐너 밴드(258)도 그 세로 커버다(_scan_band_mm).
            scan_h = extra["eoat_h_mm"]
        else:
            extra["eoat_w_mm"] = 0.0
            extra["eoat_h_mm"] = 0.0
        self._send_work_area(width, height, scan_h, overlap, **extra)
        self.main_screen.show_activity(
            f"장착 홀더를 {names.get(probes, probes)}(으)로 바꿨습니다 — 로봇 264 = {probes}.")

    # 위치 저장은 조그 화면에서, 나머지는 수동 제어 화면에서 요청한다.
    _JOG_COMMANDS = ("save_home_pose", "save_standby_pose", "save_start_pose",
                     "move_standby", "move_gripper")

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
                self._add_alarm("E9206", f"로봇 {name} 실패: {message}")
                # 출발 확인(_check_robot_started)이 까닭으로 싣는다.
                self._last_scan_command_failure = f"로봇 {name} 실패: {message}"
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
    #: 299 값 → (ERUT 장애 code, name, 화면 문구, error 에 더 싣는 칸).
    #: 탐촉자가 벽에 안 닿는 것은 어느 장비에나 생기므로 공통 번호 E3001
    #: PROBE_CONTACT_FAILED 에 어느 탐촉자인지(probe)를 싣는다(if-0.9 탭11 Q-26 —
    #: ERUT 제안). 스캔 중 접촉을 잃음 E3002 · 마킹 자리 접촉 실패 E3003 도 if-0.10
    #: 표준 번호다(Q-33 — 예전 3S E9106 · E9105). 둘 다 가운데 눌림 센서로 본다.
    #: 나머지는 3S 제조사 대역 E9xxx 다. 문구는 detail 로 싣는다.
    _PROBE_ERROR_MESSAGES = {
        1: ("E3001", "PROBE_CONTACT_FAILED", "센터 프로브 벽 접촉 실패", {"probe": "center"}),
        2: ("E3001", "PROBE_CONTACT_FAILED", "좌측(원점) 프로브 벽 접촉 실패", {"probe": "left"}),
        3: ("E3001", "PROBE_CONTACT_FAILED", "우측 프로브 벽 접촉 실패", {"probe": "right"}),
        4: ("E9104", "ARC_ZERO", "호 길이/반지름이 0 — 작업 영역 값을 확인하세요", {}),
        5: ("E3003", "MARKER_CONTACT_FAILED", "마킹 자리에서 벽 접촉을 찾지 못했습니다",
            {"probe": "center"}),
        # v5 (servoj 스캔) — 호를 도는 동안 눌림을 계속 확인한다.
        6: ("E3002", "PROBE_CONTACT_LOST", "스캔 중 접촉(눌림)을 유지하지 못해 멈췄습니다 — 보정 한계(press_max) 초과",
            {"probe": "center"}),
        7: ("E9107", "SCAN_IK_FAILED", "스캔 호 경로에 역기구학 해가 없어 멈췄습니다 — 로봇 위치·자세를 확인하세요", {}),
        # v5 인터락 — 차량 고정 확인(레지스터 309)을 기다리다 시간이 넘었다.
        8: ("E9108", "VEHICLE_HOLD_TIMEOUT", "차량 고정 확인을 못 받아 로봇이 멈췄습니다 — 아웃트리거 고정·차량 정지를 확인하세요", {}),
        # v6 — 줄 수가 펜던트 Loop 한도(200)를 넘었다. RCS 가 ERUT 요청을 먼저 거르므로
        # 보통은 펜던트에서 값을 직접 바꿨을 때만 난다.
        9: ("E9109", "SCAN_ROWS_OVER_LIMIT", "줄 수가 로봇 한도(200 줄)를 넘어 멈췄습니다 — 구역 높이·이동높이를 확인하세요", {}),
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
                err_code, err_name, _text, _fields = self._PROBE_ERROR_MESSAGES[prev_code]
                # 발생 때와 같은 code 에 cleared=true (if-0.3 부터).
                self.erut_session.raise_error({
                    "code": err_code, "name": err_name, "cleared": True,
                })
            return

        err_code, err_name, text, fields = self._PROBE_ERROR_MESSAGES.get(
            code, (f"E91{code:02d}", "ROBOT_TASK_STOPPED", "로봇 태스크가 멈췄습니다", {}))
        self.main_screen.show_activity(f"{text} (레지스터 299={code})")
        self._add_alarm(err_code, text)
        if err_code in self.erut_session.error_codes():
            # 같은 장애를 이미 알렸다 — 예: 눌림이 1초 넘게 떨어져 RCS 가 E3002 를
            # 낸 뒤 로봇 태스크도 보정 한계로 멈췄다(299 = 6). 두 번 내지 않는다.
            return
        self.erut_session.raise_error({
            "code": err_code, "name": err_name,
            "level": "stop", "recovery": "manual", "detail": text, **fields,
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

    #: 스캔 상태(290)에서 오류를 뜻하는 값.
    _STATE_ERROR = 9
    #: scan_state 에서 zero_ok(294)·probe_error(299) 자리.
    _ZERO_OK_INDEX = 4
    _PROBE_ERROR_INDEX = 9

    def _on_probe_result(self, values: list) -> None:
        """3점 측정 결과(300~305)를 들고 있는다."""
        self._probe_result = [int(v) for v in values]

    def _on_probe_poses(self, values: list) -> None:
        """접촉 자세(330~355)를 들고 있는다."""
        self._probe_poses = [int(v) for v in values]

    # ---- ERUT 캘리브레이션 = 차량 QR 정렬 + 아웃트리거 고정 (2026-11-24) -------------------------
    def _run_calibration(self, geometry: dict) -> None:
        """ERUT 캘리브레이션: 차량이 QR 마커에서 좌표계 원점을 잡는다 — geometry{shape, diameter, height[, width]} [mm].

        한 바퀴는 **작업자가 수동으로** 돈다(2026-11-24). 그 뒤 차량을 QR 앞에 가져다 두면 기다린다. calibrate 가
        오면 RCS 는 차량에 QR 정렬만 시키고(measure_lap mode ALIGN — 방향 · 거리), 끝나면 HOLD(아웃트리거 고정)한 뒤
        결과(evt/complete)를 낸다 — 그다음 prepare 를 받는다. 한 바퀴 측정(LAP)은 쓰지 않는다. 원통 · 곡판 같다
        (곡판은 QR 이 판 가운데이고 스캔 때 차량이 더 움직이지 않는다). 좌표 x 는 규격대로 원통은 QR(원점),
        곡판은 판 왼쪽 끝이 0 이다.

        로봇의 3점 측정은 여기서 하지 않는다 — 구간 검사(start) 안에서 한다.
        순서: 모재 지름으로 작업 영역 반지름을 고친다 → 리프트를 내리고 작업 끝(FINISH) → 로봇이 홈인지 확인
        → QR 정렬 → 아웃트리거 고정 → 결과. 한도는 차량 설정 「정렬 최대 시간」(ERUT 에 알리는 calibrate 시간도
        같다). 캘리브레이션 중에는 일시정지 · 정지가 차량을 맡는다.
        """
        # 좌표계를 새로 잡는다 — 앞서 끝낸 구간 표시는 지운다.
        self._erut_done_sections = set()
        # ERUT 의 diameter 는 **바깥 지름**이다(곡판은 바깥면 반경 × 2). 검사 대상 바깥 지름을
        # 바꾸면 로봇 반지름도 그 절반이 된다(_outer_radius_mm) — 두께는 쓰지 않는다.
        self._set_target_geometry(geometry)
        diameter_mm = float(geometry.get("diameter") or 0.0)
        plate = geometry.get("shape") == SHAPE_PLATE
        self.main_screen.set_pause_override(False)
        limit_s = int(self.screens["vehicle"].align_time_s())
        self.erut_session.refresh_info()
        self._calibration_lap = {"mode": "ALIGN", "plate": plate, "start": self.amr.position,
                                 "limit_s": limit_s,
                                 # 한 바퀴는 작업자가 돌아 RCS 가 잰 둘레가 없다 — 원통은 π × 바깥 지름을 싣는다.
                                 "circumference_mm": 0.0 if plate else math.pi * diameter_mm}
        self.main_screen.show_activity(
            f"캘리브레이션: {'곡판' if plate else '원통'}(지름 {diameter_mm:g} mm) — 차량이 "
            f"{'판 가운데 QR' if plate else 'QR'} 마커에 맞춰 정렬한 뒤 아웃트리거를 고정합니다"
            f"(한 바퀴는 작업자가 미리 돕니다 · 최대 {limit_s} s).")
        self._drive_calibration_lap()

    def _drive_calibration_lap(self) -> None:
        """리프트를 내리고 작업 끝(FINISH)을 보낸 뒤, 로봇이 홈에 서면 QR 정렬."""
        if not self._calibration_active():
            return
        self._prepare_to_drive(
            lambda: self._defer_until_robot_idle("캘리브레이션 QR 정렬", self._start_calibration_lap,
                                                 kind="drive"),
            # 일시정지·abort 면 버린다(calibrating 이 거짓) — 재개가 처음부터 다시 부른다.
            guard=lambda: "go" if self._calibration_active() else "drop")

    def _start_calibration_lap(self) -> None:
        lap = getattr(self, "_calibration_lap", None)
        if lap is None or not self._calibration_active():
            return
        if self.amr.active == "vehicle":
            if self.vehicle.supports("measure_lap"):
                self._start_measured_lap(lap)
                return
            # 실제 차량인데 정렬 명령이 없다 — 원점을 잡았다고 할 수 없다.
            self._calibration_lap = None
            self._finish_calibration(
                False, "차량 노드에 QR 정렬(measure_lap ALIGN)이 없어 원점을 잡지 못했습니다")
            return
        # 차량 없는 모의(더미 차량) — 놓인 자리를 원점(QR)으로 치고 아웃트리거를 고정한다.
        self._secure_after_align(lap, "놓인 자리를 원점(QR)으로 잡았습니다(차량 없음 · 모의)")

    # ---- QR 정렬 (MeasureLap mode ALIGN) ------------------------------------------------
    def _lap_timer(self) -> QTimer:
        timer = getattr(self, "_measured_lap_timer", None)
        if timer is None:
            timer = self._measured_lap_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._measured_lap_timeout)
        return timer

    @staticmethod
    def _lap_name(_lap: dict | None = None) -> str:
        """작업자 알림에 쓰는 이름 — 캘리브레이션은 QR 정렬 하나다(한 바퀴는 작업자가 돈다)."""
        return "QR 정렬"

    def _start_measured_lap(self, lap: dict) -> None:
        """measure_lap(mode ALIGN) — 진행 · 결과는 차량 상태의 lap_* 로 본다(_track_measured_lap)."""
        before = self.vehicle.last_status
        # 보내기 전 상태 — 그때 이미 DONE 이던(지난 정렬의) 결과를 이번 것으로 잘못 보지 않게.
        lap.update(measured=True, job_id=self.vehicle.new_job_id("ALN"), running_seen=False, paused=False,
                   accepted=False, vehicle_paused=False, job_before=str(before.get("job_id") or ""),
                   lap_before=str(before.get("lap_state") or ""), left_before=False)
        limit_s = int(lap.get("limit_s") or 0)
        if limit_s:
            self._lap_timer().start(limit_s * 1000)
        self.main_screen.show_activity(
            "캘리브레이션 QR 정렬 — 차량이 QR 마커에 맞춰 방향 · 거리를 맞춥니다"
            + (f", 최대 {limit_s} s" if limit_s else ""))
        # 주행(SetJob)과 같이 **저장한** 차량 설정 값을 쓴다 — 입력칸의 저장 안 한 값이 아니라(2026-11-24).
        amr = self._vehicle_amr
        if not self.vehicle.measure_lap(lap["job_id"], amr.effective_speed_mps, amr.standoff_m, 0.0,
                                        mode="ALIGN"):
            self._fail_measured_lap("QR 정렬(measure_lap ALIGN)을 보내지 못했습니다")

    def _track_measured_lap(self, status: dict) -> None:
        """QR 정렬 진행 · 결과(lap_state)를 본다.

        2026-11-24 현장: AGV 가 정렬 명령을 받자마자 DONE 을 냈는데 RCS 가 반응하지 않았다 — 예전에는
        RUNNING 을 한 번 봐야 DONE 을 믿었다. 이제는
          · status.job_id 가 이번 정렬 job_id 면 그대로 믿는다(RUNNING 없이 바로 DONE 이어도).
          · job_id 가 비었거나 보내기 전 작업의 것이면(AGV 가 job_id 를 안 바꾸는 경우) 명령이 받아들여진 뒤의
            DONE 을 믿는다 — 다만 보내기 전에 이미 DONE 이었으면 그 값이 한 번 바뀐 뒤에만.
          · 다른 새 job_id 면 이번 정렬 것이 아니다.
        """
        lap = getattr(self, "_calibration_lap", None)
        if not lap or not lap.get("measured") or lap.get("paused"):
            return
        job = str(status.get("job_id") or "")
        mine = job == lap.get("job_id")
        if job and not mine and job != lap.get("job_before"):
            return
        state = str(status.get("lap_state") or "")
        if state == "RUNNING":
            lap["running_seen"] = True
        if state != lap.get("lap_before"):
            lap["left_before"] = True
        if state not in ("DONE", "FAILED"):
            return
        fresh = mine or lap.get("running_seen") or (
            lap.get("accepted") and (lap.get("left_before") or lap.get("lap_before") not in ("DONE", "FAILED")))
        if not fresh:
            return
        if state == "FAILED":
            self._fail_measured_lap(status.get("lap_error") or "차량이 QR 정렬을 마치지 못했습니다")
        elif not status.get("lap_origin_ok", False):
            self._fail_measured_lap("QR 마커에 맞춰 서지 못했습니다(lap_origin_ok 거짓)")
        else:
            self._finish_measured_lap(float(status.get("lap_path_m", 0.0)) * 1000.0)

    def _on_measure_lap_result(self, name: str, ok: bool, message: str) -> None:
        lap = getattr(self, "_calibration_lap", None)
        if name != "measure_lap" or not lap or not lap.get("measured"):
            return
        if not ok:
            self._fail_measured_lap(f"차량이 QR 정렬 명령을 거절했습니다 — {message}")
            return
        lap["accepted"] = True
        # 답보다 상태가 먼저 왔을 수 있다(받자마자 끝낸 AGV) — 받아들여진 지금 마지막 상태를 다시 본다.
        self._track_measured_lap(self.vehicle.last_status)

    def _finish_measured_lap(self, moved_mm: float) -> None:
        lap = self._calibration_lap
        self._lap_timer().stop()
        lap["measured"] = False                    # 정렬은 끝 — 이제 고정
        self._secure_after_align(lap, f"QR 마커에 맞춰 정렬했습니다(정렬하며 {moved_mm:.0f} mm 움직임)")

    def _secure_after_align(self, lap: dict, detail: str) -> None:
        """QR 정렬이 끝났다 — HOLD(아웃트리거 고정) 뒤 결과를 낸다. 그다음 prepare 를 받는다(2026-11-24).

        그 자리(QR)가 원점이라 차량 위치를 0 으로 맞춘다. 아웃트리거가 이미 고정돼 있으면(차량이 정렬 끝에
        스스로 HOLD 했으면) 바로 끝낸다. 실제 차량은 거리 0 작업 → HOLD 로 고정한다(VehicleOutriggerAdapter).
        원통의 total_length_mm 는 잰 값이 없으니 π × 바깥 지름, 곡판은 싣지 않는다.
        고정 중 일시정지되면 고정은 마저 하고(안전 쪽) 결과는 재개 때 낸다(_resume_calibration_lap).
        """
        lap["securing"] = True
        lap["detail"] = detail
        self.amr.reset(0.0)

        def done() -> None:
            if getattr(self, "_calibration_lap", None) is not lap:
                return
            lap["secured"] = True
            if self._calibration_active():
                self._complete_secured_calibration(lap)

        if self.outrigger.position >= 1 and not self.outrigger.moving:
            done()
            return
        self.main_screen.show_activity("캘리브레이션 — 아웃트리거를 고정합니다(HOLD).")
        self._once(self.outrigger.arrived, done)
        if not self.outrigger.moving:
            self.outrigger.move_to(1, " 고정")

    def _complete_secured_calibration(self, lap: dict) -> None:
        self._calibration_lap = None
        self._finish_calibration(True, f"{lap.get('detail', 'QR 정렬')} · 아웃트리거 고정", 0.0,
                                 total_length_mm=lap.get("circumference_mm") or 0.0)

    def _fail_measured_lap(self, reason: str) -> None:
        self._lap_timer().stop()
        self._calibration_lap = None
        self._finish_calibration(False, f"캘리브레이션 QR 정렬 실패 — {reason}")

    def _measured_lap_timeout(self) -> None:
        lap = getattr(self, "_calibration_lap", None)
        if not lap or not lap.get("measured"):
            return
        self._stop_measured_lap_vehicle()
        self._fail_measured_lap(f"정렬 최대 시간 {int(lap.get('limit_s') or 0)} s 안에 끝나지 않았습니다")

    def _stop_measured_lap_vehicle(self) -> None:
        """정렬을 접을 때 차량을 세운다 — 움직이는 중일 때만, 작업도 버린다(job_cancel).

        차량이 FINISH 에 서 있으면 job_cancel 없는 STOP 은 「FINISH waits for a new job or job_cancel」로
        거절된다(2026-11-24 현장 알림). 서 있으면 보낼 것이 없다.
        """
        if self.vehicle.last_status.get("state") in ("RUNNING", "PAUSE"):
            self.vehicle.control("STOP", job_cancel=True)

    def _cancel_measured_lap(self) -> None:
        """정지 · ERUT 정지 — 돌던 한 바퀴 측정을 세운다(차량에 STOP)."""
        lap = getattr(self, "_calibration_lap", None)
        if lap and lap.get("measured") and lap.get("job_id"):
            self._lap_timer().stop()
            self._stop_measured_lap_vehicle()

    # ---- 차량 안전 · 상태 칸 (2026-11-17) ---------------------------------------------
    def _on_vehicle_safety(self, status: dict) -> None:
        """비상정지 · 접근 감지에 반응한다(바뀔 때만). PLC 는 차량 노드가 맡는다 — RCS 는 보지 않는다."""
        estop = bool(status.get("estop", False))
        if estop != getattr(self, "_vehicle_estop", False):
            self._vehicle_estop = estop
            self._update_estop()
        obstacle = bool(status.get("obstacle_stop", False))
        if obstacle != getattr(self, "_vehicle_obstacle", False):
            self._vehicle_obstacle = obstacle
            if obstacle:
                paused = False
                if self.sequencer.state not in (SequencerState.IDLE, SequencerState.DONE,
                                                SequencerState.STOPPED, SequencerState.PAUSED):
                    self.sequencer.pause()
                    paused = True
                elif self._calibration_in_progress() and not self._calibration_paused():
                    self._toggle_calibration_pause()
                    paused = True
                text = ("접근 감지로 차량이 섰습니다"
                        + (" — 작업을 일시정지했습니다. 주변을 확인하고 「재개」하세요." if paused else "."))
                self.main_screen.show_activity(text)
                self._add_alarm("E9305", text)
            else:
                self.main_screen.show_activity("접근 감지가 풀렸습니다 — 주변을 확인하고 재개하세요.")
        self._on_vehicle_error(status)

    # ---- 비상정지 — 차량(PLC) · 로봇 컨트롤러 어느 쪽이든 (2026-11-18) ------------------------
    def _on_robot_estop(self, pressed: bool) -> None:
        """로봇 컨트롤러 비상정지(robot/status/estop). 차량과 같이 서는 것은 안전 회로가 맡는다.

        AGV 노드(2026-11-23)는 <ns>/robot_estop 을 **자기가 내는**(PLC 비상정지) 토픽으로 쓴다 — RCS 는 더 이상
        그 토픽에 내지 않는다(같은 이름에 뜻이 다른 두 발행자가 생긴다).
        """
        self._robot_estop = bool(pressed)
        self._update_estop()

    def _estop_sources(self) -> list[str]:
        sources = []
        if getattr(self, "_vehicle_estop", False):
            sources.append("차량(PLC)")
        if getattr(self, "_robot_estop", False):
            sources.append("로봇 컨트롤러")
        return sources

    def _update_estop(self) -> None:
        """비상정지 — 어느 쪽 버튼이 눌려도 로봇 · 차량 · 작업을 세우고 ERUT 에 E1002 하나를 낸다.

        차량(PLC) 비상정지는 robot_status.estop, 로봇 컨트롤러 비상정지는 robot/status/estop 로 온다.
        실제로 같이 서는 것은 안전 회로(PLC ↔ 로봇 컨트롤러 비상정지 직결)가 맡고, 이 처리는 작업을 접고
        알리는 쪽이다(ERUT R-18). 둘 다 풀려야 E1002 를 푼다.
        """
        sources = self._estop_sources()
        robot = getattr(self, "_robot_estop", False)
        self.screens["io"].set_value("R_ESTOP", "눌림" if robot else "정상", "정지" if robot else "정상")
        self._show_cobot_link(getattr(self, "_cobot_link", True))
        before = getattr(self, "_estop_active", [])
        self._estop_active = sources
        if sources == before:
            return
        names = " · ".join(sources)
        if sources and not before:
            text = f"비상정지 — {names} 비상정지가 눌렸습니다. 로봇 · 차량 · 작업을 세웁니다."
            self.main_screen.show_activity(text)
            self._add_alarm("E1002", text)
            self.erut_session.raise_error({
                "code": "E1002", "name": "E_STOP", "level": "estop",
                "recovery": "reset_required", "detail": f"{names} 비상정지가 눌렸습니다"})
            self._cancel_measured_lap()
            self._stop_job_locally()
            self._stop_robot_scan()
            return
        if sources:
            self.main_screen.show_activity(f"비상정지 — {names} 비상정지가 아직 눌려 있습니다.")
            return
        if "E1002" in self.erut_session.error_codes():
            self.erut_session.clear_error("E1002")
        self.main_screen.show_activity("비상정지가 모두 풀렸습니다 — 확인 뒤 다시 시작하세요.")

    def _on_vehicle_error(self, status: dict) -> None:
        """차량 오류(error_code ≠ 0)가 생기거나 풀렸다 — RCS_AGV 인터페이스 「차량 오류 번호」(2026-11-19).

        움직이던 동작이면 그 동작이 실패로 끝나며 이미 E2001 이 났다(_on_vehicle_problem). 서 있을 때
        생긴 오류는 작업 중(구역 검사 · 마킹 · 캘리브레이션)이면 E2001 로 작업을 세우고 — 예를 들어
        로봇이 스캔하는 동안 아웃트리거 접지가 풀렸다 — 쉬는 중이면 알람 목록에만 올린다.
        """
        code = int(status.get("error_code", 0) or 0)
        before = getattr(self, "_vehicle_error_code", 0)
        if code == before:
            return
        self._vehicle_error_code = code
        if not code:
            self.main_screen.show_activity("차량 오류가 풀렸습니다.")
            return
        text = f"차량 오류 {code} {status.get('error_msg', '')}".strip()
        if "E2001" in self.erut_session.error_codes() or any(
                m.moving and getattr(m, "active", "") == "vehicle"
                for m in (self.amr, self.lift, self.outrigger)):
            return                       # 실제 차량이 움직이던 동작의 실패로 이미 알렸다(또는 곧 알린다)
        self.main_screen.show_activity(text)
        if (self._job_running() or self.erut_session.has_work()
                or self._calibration_in_progress()):
            self._on_vehicle_problem(text)
        else:
            self._add_alarm("E2001", f"차량: {text}")

    def _reset_vehicle_error(self) -> None:
        """알람 리셋 · ERUT reset — 차량이 오류 상태면 차량에도 리셋(robot_control reset = 1)을 보낸다."""
        status = self.vehicle.last_status if self.vehicle.online else {}
        if int(status.get("error_code", 0) or 0) or status.get("state") == "ERROR":
            self.vehicle.control(reset=True)
            self.main_screen.show_activity("차량에 오류 리셋을 보냈습니다.")

    def _show_vehicle_extras(self, status: dict) -> None:
        """상단 배터리, ERUT 자기소개(배터리 기능)를 차량 상태로 맞춘다."""
        battery = status.get("battery_pct") if status else None
        if battery is None:
            self.top_bar.battery_label.setText("배터리  -")
        else:
            charging = " (충전 중)" if status.get("charging") else ""
            self.top_bar.battery_label.setText(f"배터리  {int(battery)}%{charging}")
        known = battery is not None
        if known != getattr(self, "_battery_known", False):
            self._battery_known = known
            self.erut_session.refresh_info()        # 기능 목록에 battery · charging 을 넣고 뺀다

    def _battery_fields(self) -> dict:
        """ERUT 에 싣는 배터리 칸 — 차량이 알려 줄 때만(모르면 빈 사전: 규격은 칸을 빼라고 한다)."""
        status = self.vehicle.last_status if self.vehicle.online else {}
        if "battery_pct" not in status:
            return {}
        return {"battery": int(status["battery_pct"]), "charging": bool(status.get("charging", False))}

    def _on_vehicle_start_button(self) -> None:
        """차량 「작업 시작」 버튼(<ns>/start_request) — RCS 「계측 시작」과 같은 순서로 돈다."""
        self.main_screen.show_activity("차량 「작업 시작」 버튼 — 계측(캘리브레이션)을 시작합니다.")
        self._start_local_calibration()

    def _calibration_active(self) -> bool:
        """캘리브레이션 주행 중인가(일시정지 아님) — ERUT calibrate 또는 RCS 「계측 시작」."""
        return self.erut_session.calibrating or (
            getattr(self, "_local_calibration", False)
            and not getattr(self, "_local_calibration_paused", False))

    def _calibration_in_progress(self) -> bool:
        """캘리브레이션이 걸려 있는가(일시정지 포함)."""
        return (getattr(self, "_local_calibration", False)
                or bool(self.erut_session._calibrate_req_id))

    def _calibration_paused(self) -> bool:
        if getattr(self, "_local_calibration", False):
            return getattr(self, "_local_calibration_paused", False)
        return self.erut_session.calibration_paused

    def _toggle_calibration_pause(self) -> None:
        """캘리브레이션 중 「일시정지」 · 「재개」 — 차량을 세우고, 남은 거리를 이어 간다."""
        if self._calibration_paused():
            if getattr(self, "_local_calibration", False):
                self._local_calibration_paused = False
            else:
                self.erut_session.resume_calibration_locally()
            self._resume_calibration_lap()
            return
        if getattr(self, "_local_calibration", False):
            self._local_calibration_paused = True
        else:
            self.erut_session.pause_calibration_locally()
        self._pause_calibration_lap()

    def _start_local_calibration(self) -> None:
        """주요 제어 「계측 시작」 — 캘리브레이션(QR 정렬 + 아웃트리거 고정)만 RCS 에서 따로 한다(2026-11-12).

        ERUT calibrate 와 같은 순서다: 리프트를 내리고 작업 끝(FINISH), 로봇이 홈에 선 뒤 차량이 QR 마커에
        맞춰 정렬하고 아웃트리거를 고정한다(2026-11-24 — 한 바퀴는 작업자가 미리 돈다). 검사는 하지 않는다. ERUT 에는 calibrating 으로 보이고 끝날 때까지 이동 명령을 받지 않지만, 결과
        (evt/complete)는 내지 않고 ERUT 의 원점(calibrated)도 바꾸지 않는다 — ERUT 가 시킨 일이 아니다.
        「정지」로 멈춘다.
        """
        geometry = self._target_geometry()
        diameter_m = geometry["diameter"] / 1000.0
        plate = geometry["shape"] == SHAPE_PLATE
        refuse = ""
        if self._calibration_active():
            refuse = "이미 캘리브레이션 중입니다"
        elif self._job_running() or self.erut_session.has_work():
            refuse = "작업 중입니다 — 끝나거나 정지한 뒤 누르세요"
        elif self._robot_task_running():
            refuse = "로봇 태스크가 도는 중입니다"
        elif any(m.moving for m in (self.amr, self.lift)):
            refuse = "차량·리프트가 움직이는 중입니다"
        elif diameter_m <= 0:
            refuse = "검사 대상 지름이 없습니다 — 검사 대상에서 바깥 지름을 넣으세요"
        else:
            refuse = self._vehicle_start_blocker()
        if refuse:
            self.main_screen.show_activity(f"계측 시작 거절 — {refuse}")
            return
        self._local_calibration = True
        self.erut_session.begin_local_calibration()
        self.main_screen.show_activity(
            f"계측 시작 — 차량이 {'판 가운데 QR' if plate else 'QR'} 마커에 맞춰 정렬하고 아웃트리거를 고정합니다"
            "(한 바퀴는 작업자가 미리 돕니다). 멈추려면 「정지」.")
        self._run_calibration(geometry)

    def _end_local_calibration(self, reason: str = "") -> None:
        """RCS 「계측 시작」을 끝낸다(완료 · 실패 · 정지)."""
        if not getattr(self, "_local_calibration", False):
            return
        self._local_calibration = False
        self._local_calibration_paused = False
        self._calibration_lap = None
        self.main_screen.set_pause_override(None)
        self.erut_session.end_local_calibration()
        if reason:
            self.main_screen.show_activity(f"계측을 멈췄습니다 — {reason}")

    def _pause_calibration_lap(self) -> None:
        """캘리브레이션 주행 일시정지 — 차량을 세운다. 재개하면 남은 거리를 간다."""
        lap = getattr(self, "_calibration_lap", None)
        if lap and lap.get("measured") and lap.get("job_id"):
            # 한 바퀴 측정은 끊으면 처음부터라 PAUSE · RUNNING 으로 세우고 잇는다.
            lap["paused"] = True
            timer = self._lap_timer()
            lap["remaining_ms"] = max(timer.remainingTime(), 0) if timer.isActive() else 0
            timer.stop()
            # 차량이 정렬 중일 때만 세운다 — FINISH 등에 서 있으면 PAUSE 는 거절된다.
            lap["vehicle_paused"] = self.vehicle.last_status.get("state") == "RUNNING"
            if lap["vehicle_paused"]:
                self.vehicle.control("PAUSE")
            self.main_screen.set_pause_override(True)
            self.main_screen.show_activity(
                f"캘리브레이션 {self._lap_name(lap)}을 멈췄습니다 — 「재개」로 이어 갑니다.")
            return
        if lap and lap.get("securing"):
            self.main_screen.set_pause_override(True)
            self.main_screen.show_activity("캘리브레이션을 멈췄습니다 — 아웃트리거 고정은 마저 하고, 「재개」로 끝냅니다.")
            return
        self.amr.pause()
        self.main_screen.set_pause_override(True)
        self.main_screen.show_activity("캘리브레이션을 멈췄습니다 — 「재개」로 QR 정렬부터 다시 합니다.")

    def _resume_calibration_lap(self) -> None:
        lap = getattr(self, "_calibration_lap", None)
        if lap and lap.get("securing"):
            # 정렬은 끝났고 고정 중이었다 — 다시 정렬하지 않는다(FINISH 가 고정을 풀어 버린다).
            self.main_screen.set_pause_override(False)
            if lap.get("secured"):
                self._complete_secured_calibration(lap)
            else:
                self.main_screen.show_activity("캘리브레이션을 이어 갑니다 — 아웃트리거 고정을 마칩니다.")
            return
        if lap and lap.get("measured") and lap.get("paused"):
            lap["paused"] = False
            if lap.get("remaining_ms"):
                self._lap_timer().start(int(lap["remaining_ms"]))
            if lap.pop("vehicle_paused", False):
                self.vehicle.control("RUNNING")
            self.main_screen.set_pause_override(False)
            self._track_measured_lap(self.vehicle.last_status)    # 멈춘 사이 끝났을 수 있다
            self.main_screen.show_activity(f"캘리브레이션 {self._lap_name(lap)}을 이어 갑니다.")
            return
        self.main_screen.set_pause_override(False)
        self.main_screen.show_activity("캘리브레이션을 이어 갑니다 — QR 정렬부터 다시 합니다.")
        self._drive_calibration_lap()

    @staticmethod
    def _once(signal, slot) -> None:
        """시그널이 한 번 오면 slot 을 부르고 연결을 끊는다."""
        def fire(*_args):
            try:
                signal.disconnect(fire)
            except TypeError:
                pass
            slot()
        signal.connect(fire)

    def _finish_calibration(self, ok: bool, detail: str, error_mm: float = 0.0,
                            total_length_mm: float | None = None) -> None:
        """결과를 ERUT 에 낸다. 로봇은 주행 내내 홈에 있었으므로 그대로 둔다.

        RCS 「계측 시작」이면 화면에만 알린다 — ERUT 가 시킨 일이 아니다.
        """
        self.main_screen.set_pause_override(None)
        if getattr(self, "_local_calibration", False):
            self._end_local_calibration()
            text = f"계측 {'완료' if ok else '실패'} — {detail}"
            self.main_screen.show_activity(text)
            if not ok:
                self._add_alarm("E9310", text)
            return
        self.main_screen.show_activity(f"캘리브레이션 결과 — {detail}")
        if not ok:
            self._add_alarm("E1004", f"캘리브레이션 실패 — {detail}")
        self.erut_session.finish_calibration(ok, error_mm, detail,
                                             total_length_mm=total_length_mm)

    # ---- 구간 검사(start) 안의 로봇 3점 측정 ------------------------------------
    def _report_probe_fit(self, values: list) -> None:
        """로봇이 3점 측정을 마치고 시작점에 섰을 때 잰 벽을 기록에 남긴다.

        ERUT 에 따로 보내는 값은 아니다(규격에 자리가 없다). 입력 반지름과
        잰 벽이 크게 다르면 작업자가 알 수 있게 진행 알림에 남긴다.
        """
        if not values or not self.erut_session._start_req_id:
            return
        at_origin = (int(values[self._SCAN_STATE_INDEX]) == self._STATE_AT_ORIGIN
                     and len(values) > self._ZERO_OK_INDEX
                     and int(values[self._ZERO_OK_INDEX]))
        if at_origin == getattr(self, "_probe_fit_reported", False):
            return
        self._probe_fit_reported = at_origin
        if not at_origin:
            return
        result = calibration.evaluate(
            getattr(self, "_probe_poses", []),
            self._expected_wall_radius_mm(),
            # 논센서 판은 눌림 센서가 없어 접촉 수(305)가 늘 0 이다 — 계산한 접점을
            # 그대로 쓰므로 접촉 수는 보지 않는다.
            None if getattr(self, "_nosensor", False) else getattr(self, "_probe_result", []),
        )
        self.main_screen.show_activity(f"3점 측정 — {result.describe()}")

    def _expected_wall_radius_mm(self) -> float:
        """로봇이 호를 그릴 때 쓰는 반지름 = 벽 바깥 반지름(바깥 지름 / 2)."""
        return self._outer_radius_mm()

    def _handle_origin_wait(self, values: list) -> None:
        """로봇이 시작점(원점)에 붙어 서면 적심 → 스캔으로 넘어가게 풀어 준다.

        ERUT 작업이면 접촉(evt/contact attached)을 알리고 ERUT 가 물을 켤 시간을
        준 뒤(CONTACT_LEAD_MS) 세션이 푼다. RCS 에서 시작한 작업이면 같은
        시간 뒤 여기서 바로 푼다 — 예전에는 사내 MC 의 probe_ack 를 기다렸다
        (사내 MC 는 2026-10-07 에 뺐다). 10Hz 로 같은 값이 계속 들어오므로
        **상태가 바뀔 때만** 본다.
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
        if not waiting:
            self.erut_session.clear_at_origin()
            return
        if self.erut_session.has_work():
            self.main_screen.show_activity(
                "로봇이 시작점에 붙었습니다 — ERUT 에 접촉을 알리고 잠시 뒤 적심으로 넘어갑니다.")
            self.erut_session.notify_at_origin()
            return
        self.main_screen.show_activity(
            f"로봇이 시작점에 붙었습니다 — {CONTACT_LEAD_MS / 1000:g}초 뒤 적심으로 넘어갑니다.")
        _call_later(self, CONTACT_LEAD_MS, self._release_local_origin)

    def _release_local_origin(self) -> None:
        """RCS 에서 시작한 작업의 원점 대기를 푼다 — 그 사이 멈췄으면 풀지 않는다."""
        if (getattr(self, "_origin_waiting", False)
                and self.sequencer.state is SequencerState.SCANNING):
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
            self._add_alarm("E9209",
                "스캔 시작 허가를 보내지 못했습니다 — 로봇 연결을 확인하세요.")
        return sent

    # ---- 로봇 안전정지 (PLC — 차량 이동 중, 2026-11-17) ---------------------------------
    #: 안전정지가 풀린 뒤에도 이만큼은 로봇 알람을 ERUT 로 보내지 않는다 [s] — 상태 · 알람 도착 순서 차이.
    SAFETY_STOP_GRACE_S = 3.0

    def _safety_label(self) -> str:
        return "재설정 대기" if getattr(self, "_safety_waiting_reset", False) else "안전정지"

    def _show_cobot_link(self, connected: bool) -> None:
        self._cobot_link = bool(connected)
        badge = self.top_bar.badges["Cobot"]
        if connected and getattr(self, "_robot_estop", False):
            badge.set_state("비상정지", "StatusDanger")
        elif connected and getattr(self, "_robot_safety_stop", False):
            badge.set_state(self._safety_label(), "StatusWarn")
        else:
            badge.set_connected(bool(connected))

    def _on_robot_safety_stop(self, stopped: bool) -> None:
        """로봇 상태(30001)로 본 안전정지 — 노드가 낸다. 입력(CI0~CI3)과 함께 판단한다."""
        self._safety_from_state = bool(stopped)
        self._update_safety_stop()

    def _on_config_inputs(self, bits: int) -> None:
        """레지스터 1(xxxxTTTTCCCCCCCC) — Config 입력 · 툴 입력. I/O 화면에 그대로 보인다.

        CI0 · CI1 중 하나라도 켜지면 안전정지, 그 신호가 꺼진 뒤 CI2 · CI3 중 하나라도 켜지면
        안전재설정(안전 IO 라 2채널씩). RCS 는 보고 표시만 한다. 툴 입력(비트 8~)은 밀착 센서다.
        """
        self.screens["io"].set_bits("robot_config", "IN", bits)
        stop_in, reset_in = bool(bits & 0b0011), bool(bits & 0b1100)
        if stop_in:
            self._safety_from_io = True
            self._safety_waiting_reset = False
        elif getattr(self, "_safety_from_io", False):
            if reset_in:
                self._safety_from_io = False
                self._safety_waiting_reset = False
            else:
                self._safety_waiting_reset = True        # 정지 신호는 꺼졌다 — 재설정을 기다린다
        self._update_safety_stop()

    def _update_safety_stop(self) -> None:
        """로봇 안전정지 — 차량이 움직이는 동안 PLC 가 걸고, 서면 안전재설정으로 푼다.

        비상정지가 아니다. RCS 는 상단 Cobot 램프 · I/O 화면 · 알림으로 **보이기만** 하고 ERUT 에는
        아무것도 보내지 않는다(그 사이 로봇 알람도 싣지 않는다). 안전정지 중에는 로봇을
        출발(play)시키지 않고, 풀리면 미뤄 둔 출발을 한다. 로봇 상태(30001) · 입력(CI0~CI3)
        어느 쪽이든 정지면 정지로 본다.
        """
        stopped = (getattr(self, "_safety_from_state", False)
                   or getattr(self, "_safety_from_io", False))
        io = self.screens["io"]
        if stopped:
            io.set_value("SAFETY", self._safety_label(),
                         "대기" if getattr(self, "_safety_waiting_reset", False) else "정지")
        else:
            io.set_value("SAFETY", "정상", "정상")
        if stopped == getattr(self, "_robot_safety_stop", False):
            self._show_cobot_link(getattr(self, "_cobot_link", True))     # 정지 ↔ 재설정 대기 글자
            return
        self._robot_safety_stop = stopped
        self._safety_stop_changed_at = time.monotonic()
        self._show_cobot_link(getattr(self, "_cobot_link", True))
        if stopped:
            moving = any(m.moving for m in (self.amr, self.lift, self.outrigger))
            self.main_screen.show_activity(
                "로봇 안전정지 — " + ("차량 이동 중 PLC 가 건 정지입니다. " if moving else "")
                + "안전재설정되면 풀립니다.")
            return
        self.main_screen.show_activity("로봇 안전정지가 풀렸습니다(안전재설정).")
        if getattr(self, "_play_after_safety", False):
            self._play_after_safety = False
            self._schedule_play()

    def _in_safety_stop_window(self) -> bool:
        """안전정지 중이거나 막 풀렸거나, 차량 · 리프트가 움직이는 중인가 — 로봇 알람을 밖에 안 낸다."""
        if getattr(self, "_robot_safety_stop", False):
            return True
        if time.monotonic() - getattr(self, "_safety_stop_changed_at", -1e9) < self.SAFETY_STOP_GRACE_S:
            return True
        return any(m.moving for m in (self.amr, self.lift, self.outrigger))

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
        # 알람 스트림은 그 순간의 기록이라 「풀렸다」가 따로 오지 않는다. 경고로
        # 내고, 같은 알람이 잠잠해지면 스스로 해제를 보낸다(recovery=auto) —
        # 예전엔 manual 로 내고 풀지 않아 errors[] 에 계속 남았다(ERUT Q-03).
        # 태스크를 멈추게 한 알람이면 E9202 가 따로 stop 으로 나간다.
        self._last_robot_alarm = (time.monotonic(), str(text))
        if self._in_safety_stop_window():
            # 차량 이동 중 PLC 안전정지에 딸린 알람 — 화면 알람 목록에만 남고 ERUT 로는 안 보낸다.
            return
        self.erut_session.raise_error({
            "code": "E9201", "name": "ROBOT_ALARM", "detail": text,
            "level": "warning", "recovery": "auto",
        })
        timer = getattr(self, "_robot_alarm_clear_timer", None)
        if timer is None:
            timer = self._robot_alarm_clear_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self.erut_session.clear_error("E9201"))
        timer.start(self.ROBOT_ALARM_CLEAR_MS)

    #: 로봇 알람(E9201) 경고를 스스로 푸는 때 — 같은 알람이 이만큼 잠잠하면 [ms].
    ROBOT_ALARM_CLEAR_MS = 10_000
    #: 스캔 중 태스크가 멈춘 것을 장애로 보기 전에 기다리는 시간 [ms]. 정상 완료면
    #: 로봇이 완료(290 = 5)를 먼저 쓰고 홈으로 간 뒤 멈추므로 그 사이 넘어간다.
    TASK_STOP_GRACE_MS = 1_500

    def _watch_scan_task(self, state: int) -> None:
        """스캔 중 로봇 태스크가 스스로 멈췄는지 본다 — 멈췄으면 E9202(stop).

        역기구학 해 없음·특이점처럼 컨트롤러가 태스크를 세우는 오류는 299 에
        사유가 안 남는다. 그대로 두면 RCS 는 스캔 중인 줄 알고 기다리고, ERUT 는
        완료(evt/complete)를 영영 못 받는다(ERUT Q-08, 2026-10-02 15:07). 이번
        play 에서 태스크가 도는 걸 본 뒤에 멈춘(3) 것만 본다 — 셀을 시작할 때
        RCS 가 보내는 stop 은 거르기 위해서다.
        """
        if self.sequencer.state is not SequencerState.SCANNING:
            return
        if getattr(self, "_unpark_sent", False) or getattr(self, "_rest_sent", False):
            return                       # 받침대 태스크가 도는 중이다 — 스캔 태스크가 아니다
        if state == self._TASK_STATE_RUNNING:
            self._scan_task_seen_running = True
            return
        if state != self._TASK_STATE_STOPPED or not getattr(self, "_scan_task_seen_running", False):
            return
        _call_later(self, self.TASK_STOP_GRACE_MS, self._report_scan_task_stopped)

    def _report_scan_task_stopped(self) -> None:
        if (self.sequencer.state is not SequencerState.SCANNING
                or getattr(self, "_robot_task_state", 0) != self._TASK_STATE_STOPPED
                or not getattr(self, "_scan_task_seen_running", False)):
            return
        self._scan_task_seen_running = False
        alarm = getattr(self, "_last_robot_alarm", None)
        cause = ""
        if alarm is not None and time.monotonic() - alarm[0] < 30.0:
            cause = f" — 로봇 알람: {alarm[1]}"
        text = f"스캔 중 로봇 태스크가 멈췄습니다{cause}"
        self.main_screen.show_activity(text)
        self._add_alarm("E9202", text)
        self.erut_session.raise_error({
            "code": "E9202", "name": "ROBOT_TASK_STOPPED", "detail": text,
            "level": "stop", "recovery": "reset_required",
        })
        if self.sequencer.state is SequencerState.SCANNING:
            self.sequencer.stop()

    def _add_alarm(self, code: str, text: str) -> None:
        """RCS 알람 목록에 번호를 붙여 올린다 — 번호는 docs/RCS_알람_목록 과 같다(2026-11-19).

        ERUT 로 보내는 detail 에는 번호를 붙이지 않는다(error.code 가 따로 있다). 운영 기록
        (알람이벤트)의 코드 칸에도 같은 번호가 남는다.
        """
        self.cobot_manual_screen.add_alarm(text, code)

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
        self._reset_vehicle_error()
        if cleared:
            self.main_screen.show_activity(f"장애 해제: {', '.join(cleared)}")

    def _show_ros_error(self, message: str) -> None:
        """ROS 수신 오류를 메인 화면에 간단한 운영 메시지로 표시한다."""
        self.main_screen.show_activity(f"ROS 오류: {message}")

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
        self.cobot_manual_screen.alarm_added.connect(
            lambda text, code: rec.log_event("알람", text, code=code))
        self.erut_session.error_published.connect(self._record_erut_error)
        self.erut_session.error_cleared.connect(
            lambda code, message: self.data_recorder.log_event("해제", message, code=code))
        self.erut_session.message_published.connect(
            lambda code, text: rec.log_event("알림", text, code=code))
        # 통신 (MQTT 수신 스레드에서도 불린다 — 기록기가 잠금으로 막는다)
        self.erut.traffic = lambda d, topic, payload: rec.comms(d, "ERUT", topic, payload)
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

    #: 차량 주행 속도 [m/s] 처음 값(0.1, 2026-11-12). SetJob.set_speed 로 싣는다 — 차량 설정
    #: 「차량 주행 속도」가 덮는다.
    VEHICLE_SPEED_MPS = 0.1

    # ---- 차량 설정 (2026-11-09) ----------------------------------------------------
    def _apply_vehicle_settings(self) -> None:
        """차량 설정을 반영한다 — 주행 속도 · 수동 화면 리프트 상한 · 배율 표시."""
        vehicle = self.screens["vehicle"]
        amr = getattr(self, "_vehicle_amr", None)
        if amr is not None:
            amr.speed_mps = vehicle.speed_mm_s() / 1000.0
            amr.standoff_m = vehicle.standoff_mm() / 1000.0
        lift = getattr(self, "_vehicle_lift", None)
        if lift is not None:
            lift.speed_mps = vehicle.lift_speed_mm_s() / 1000.0
        lift_max = int(vehicle.lift_max_mm())
        if lift_max > 0:
            self.screens["manual"].lift_target.setMaximum(lift_max)
        vehicle.set_target_diameter(self.main_screen.orbit_view.target_dimensions()[0] * 1000.0)

    def _on_vehicle_settings_saved(self, scope: str, _values: dict) -> None:
        if scope == "vehicle":
            amr = getattr(self, "_vehicle_amr", None)
            before = (amr.speed_mps, amr.standoff_m) if amr is not None else None
            self._apply_vehicle_settings()
            # 리프트 한계 높이는 AGV 노드 max_lift_height 로도 간다 — 바뀌었으면 노드를 다시 띄운다(작업 중이면 안 함).
            if hasattr(self, "agv_node"):
                self._apply_agv_node()
            if amr is not None and before != (amr.speed_mps, amr.standoff_m):
                # AGV 노드에는 값만 바꾸는 명령이 없다 — 주행 작업(SetJob)마다 실어 보낸다(2026-11-24 검토).
                moving = " 지금 달리는(멈춰 둔) 작업은 처음 받은 값 그대로 끝까지 갑니다." if (
                    self.amr.moving or getattr(amr, "paused", False)) else ""
                self.main_screen.show_activity(
                    f"차량 설정 저장 — 주행 속도 {amr.speed_mps * 1000:,.0f} mm/s · 모재 거리 유지 "
                    f"{amr.standoff_m * 1000:,.0f} mm 는 다음 주행 작업(SetJob)부터 차량에 실려 갑니다.{moving}")
            # 정렬 최대 시간이 바뀌었으면 ERUT 자기소개를 다시 낸다(calibrate 시간).
            self.erut_session.refresh_info()

    def _travel_scale(self) -> float:
        """모재 표면 거리 → 차량 주행 거리 배율 (R + 거리 유지 + 차량 폭/2) ÷ R."""
        diameter_mm = self.main_screen.orbit_view.target_dimensions()[0] * 1000.0
        return self.screens["vehicle"].travel_scale(diameter_mm)

    #: 시작 높이를 올려(358) 팔로 스캔할 수 있는 로봇 태스크 판.
    ARM_RAISE_TASKS = frozenset({"dusan_v6"})

    def _can_raise_arm(self) -> bool:
        version = getattr(self, "_task_version", "") or DEFAULT_TASK_VERSION
        return (version in self.ARM_RAISE_TASKS
                and not self.ros_status.lacks_write("scan_z_offset"))

    def _plan_lift(self, bottom_mm: float, height_mm: float) -> tuple[float, float, str]:
        """구역 [아래 끝, 아래 끝 + 세로] 을 훑을 (리프트 높이, 팔 올림 높이, 못 하는 까닭).

        높이 0 은 모재 아래 끝 = 리프트 최하단에서 그리퍼를 붙인 그리퍼 접촉 위치다(모재는 받침대 위,
        로봇은 리프트 위). 그 위치는 로봇 베이스에서 「그리퍼 접촉 위치 높이」(아래면 음수)에 있고,
        팔은 베이스 기준 「상승 한계」까지 오른다 — 리프트가 L 이면 모재 높이 L 에서 「상승 한계 −
        접촉 위치 높이」 만큼 위까지 닿는다(2026-11-23 — 예전에는 손으로 넣는 베이스 높이를 더했다). 그래서
          * 지금 리프트 높이에서 팔만 들어 닿으면 리프트는 그대로 두고 팔을 그만큼 든다
            (주행 전에 리프트를 0 으로 내리므로 낮은 구역은 리프트를 올리지 않는다).
          * 아니면 예전처럼 리프트를 구역 아래 끝으로 올린다. 리프트 한계 높이를 넘으면
            한계까지 올리고 나머지를 팔로 든다.
        팔을 못 드는 태스크(v6 전)면 늘 리프트로 구역 아래 끝까지 간다.
        """
        vehicle = self.screens["vehicle"]
        reach = vehicle.arm_reach_mm()
        lift_max = vehicle.lift_max_mm()
        bottom = max(float(bottom_mm), 0.0)
        top = bottom + max(float(height_mm), 0.0)
        if not self._can_raise_arm():
            if bottom > lift_max + 0.5:
                return lift_max, 0.0, (f"구역 아래 끝 {bottom:,.0f} mm 가 리프트 한계 높이 "
                                       f"{lift_max:,.0f} mm 를 넘습니다")
            return bottom, 0.0, ""
        current = max(float(self.lift.position), 0.0)
        if current <= bottom + 0.5 and top <= current + reach + 0.5:
            return current, max(bottom - current, 0.0), ""
        lift = min(bottom, lift_max)
        offset = bottom - lift
        if offset + (top - bottom) > reach + 0.5:
            return lift, 0.0, self._top_limit_text(top)
        return lift, offset, ""

    def _top_limit_text(self, top: float) -> str:
        """최대 작업 가능 높이를 넘었다 — 로봇 쪽에는 이 검사가 없고 RCS 가 본다(2026-11-09)."""
        vehicle = self.screens["vehicle"]
        return (f"구역 위 끝 {top:,.0f} mm 가 최대 작업 가능 높이 {vehicle.top_limit_mm():,.0f} mm "
                f"(리프트 한계 {vehicle.lift_max_mm():,.0f} + 로봇팔 상승 한계 {vehicle.reach_mm():,.0f} "
                f"− 그리퍼 접촉 위치 높이 {vehicle.contact_height_mm():,.0f})를 넘습니다")

    def _zone_reach_blocker(self, bottom_mm: float, height_mm: float) -> str:
        """ERUT 구역을 리프트 · 팔로 닿을 수 없으면 그 까닭(→ 400 E6003). 지금 리프트와 무관하다."""
        vehicle = self.screens["vehicle"]
        bottom = max(float(bottom_mm), 0.0)
        top = bottom + max(float(height_mm), 0.0)
        if self._can_raise_arm():
            if top > vehicle.top_limit_mm() + 0.5:
                return self._top_limit_text(top)
            return ""
        if bottom > vehicle.lift_max_mm() + 0.5:
            return (f"구역 아래 끝 {bottom:,.0f} mm 가 리프트 한계 높이 "
                    f"{vehicle.lift_max_mm():,.0f} mm 를 넘습니다")
        return ""

    def _send_scan_z_offset(self, offset_mm: float) -> None:
        """팔을 들어 스캔할 높이를 로봇에 쓴다(358). 0 인데 노드가 칸을 모르면 보낼 것이 없다."""
        value = int(round(max(float(offset_mm), 0.0)))
        self._scan_z_offset_mm = value
        if value == 0 and not self.ros_status.writable("scan_z_offset"):
            return
        self.ros_status.send_value("scan_z_offset", value)

    def _send_scan_y_base(self, bottom_mm: float) -> None:
        """이번 구역 아래 끝의 모재 높이 [mm] 를 로봇에 쓴다(359, 2026-11-23).

        로봇은 「이 값 + 지금 줄 높이」가 태스크 변수 up_clear_h 안이면 줄 바꿈에서 벽에서 up_clear_d
        만큼 물러섰다 오른다 — 받침대 위에 뜬 모재 아래 끝에 스캐너 홀더가 걸리지 않게. 노드가 칸을
        모르면(옛 판) 보내지 않는다 — 그러면 로봇은 0 으로 보고 구역마다 아래쪽을 물러서며 오른다.
        """
        value = int(round(max(float(bottom_mm), 0.0)))
        self._scan_y_base_mm = value
        if self.ros_status.writable("scan_y_base"):
            self.ros_status.send_value("scan_y_base", value)

    def _sync_contact_height(self) -> None:
        """저장한 그리퍼 접촉 위치의 Z(로봇 베이스 기준 mm)를 차량 설정의 「그리퍼 접촉 위치 높이」로 쓴다."""
        vehicle = self.screens.get("vehicle") if hasattr(self, "screens") else None
        if vehicle is None:
            return
        entry = self._reference_poses.get("start_pose")
        values = entry.get("values") if isinstance(entry, dict) else None
        vehicle.set_saved_contact_height(float(values[2]) if values and len(values) > 2 else None)

    def _move_lift_for_cell(self, bottom_mm: float) -> None:
        """이번 구역 높이로 — 팔이 닿으면 리프트는 그대로 두고 팔을 든다(_plan_lift)."""
        plan = self.sequencer.plan
        height = float(plan.cell_height) if plan is not None else 0.0
        lift, offset, reason = self._plan_lift(bottom_mm, height)
        if reason:
            self._on_vehicle_problem(f"리프트 · 로봇 높이: {reason}")
            return
        self._send_scan_z_offset(offset)
        self._send_scan_y_base(bottom_mm)
        if offset > 0:
            self.main_screen.show_activity(
                f"리프트 {lift:,.0f} mm 에서 로봇이 팔을 {offset:,.0f} mm 들어 스캔합니다"
                f"(구역 {bottom_mm:,.0f}~{bottom_mm + height:,.0f} mm).")
        self.lift.move_to(lift, " mm")

    def _setup_vehicle(self) -> None:
        """ROS 차량 제어 노드와 붙는다(vehicle_interfaces). 없으면 더미로만 돈다."""
        self.vehicle = VehicleClient(self.ros_status, parent=self)
        self._vehicle_amr = VehicleAmrAdapter(self.vehicle, self.VEHICLE_SPEED_MPS, parent=self)
        # 차량 설정이 먼저 불러와졌으면 그 속도를 쓴다.
        self._vehicle_amr.speed_mps = self.screens["vehicle"].speed_mm_s() / 1000.0
        # SetJob.radius = 목표 대상(모재) 반지름 [m] — 보낼 때마다 검사 대상 바깥 지름 / 2 를 묻는다.
        self._vehicle_amr.radius_source = lambda: self._outer_radius_mm() / 1000.0
        self.amr.add_backend("vehicle", self._vehicle_amr)
        # 리프트 — 자동 동작 중에는 SetMvLift(lift_control), 속도는 차량 설정 「리프트 속도」.
        self._vehicle_lift = VehicleLiftAdapter(
            self.vehicle, self.screens["vehicle"].lift_speed_mm_s() / 1000.0, parent=self)
        self.lift.add_backend("vehicle", self._vehicle_lift)
        # 아웃트리거 — 자동 모드에서는 고정 = 거리 0 작업(도착 HOLD), 해제 = FINISH. 작업 값은 AMR 과 같다.
        vehicle_outrigger = VehicleOutriggerAdapter(self.vehicle, parent=self)
        vehicle_outrigger.job_values = self._vehicle_amr.job_values
        self.outrigger.add_backend("vehicle", vehicle_outrigger)
        # 그 자리 작업 끝(RobotControl state "FINISH") — 차량이 홀딩을 풀고 아웃트리거를 올린다.
        # 다음 주행 전에 보낸다(_prepare_to_drive). 더미 차량에는 없다.
        self._vehicle_finish = VehicleFinishAdapter(self.vehicle, parent=self)
        self._vehicle_finish.activity.connect(self.main_screen.show_activity)
        self._vehicle_hinted = False
        for motion in (self.amr, self.lift, self.outrigger, self._vehicle_finish):
            motion.problem.connect(self._on_vehicle_problem)
        self.vehicle.online_changed.connect(self._apply_vehicle_mode)
        self.vehicle.online_changed.connect(self._hint_vehicle_available)
        # AGV 제어 노드(AGV 담당 steer_agv) — 내장이면 RCS 가 자식으로 띄운다(2026-11-24).
        self.agv_node = AgvNodeSupervisor(self)
        self.agv_node.external_present = lambda: self.vehicle.online
        self.agv_node.activity.connect(self.main_screen.show_activity)
        self.agv_node.state_changed.connect(lambda _text: self._refresh_receiver())
        # 받는 노드(내부 프로그램 · 외부 PC) 표시 · 외부 PC 확인 · PLC 포트 확인 (2026-11-24).
        connection = self.screens["connection"]
        self._agv_off_reason = ""
        self.link_probe = LinkProbe(self)
        self.link_probe.finished.connect(self._on_probe_finished)
        connection.plc_check_requested.connect(self._check_plc_port)
        connection.external_check_requested.connect(self._check_external_pc)
        connection.agv_node_mode_changed.connect(lambda _mode: self._refresh_receiver())
        self.vehicle.online_changed.connect(lambda _online: self._refresh_receiver())
        self._receiver_timer = QTimer(self)
        self._receiver_timer.setInterval(self.RECEIVER_REFRESH_MS)
        self._receiver_timer.timeout.connect(self._refresh_receiver)
        self._receiver_timer.start()
        # 수동 제어 화면 — ManualCommand(리프트 포함) / ResetOdometry / RobotControl 로 보낸다.
        manual = self.screens["manual"]
        manual.manual_requested.connect(lambda fields: self.vehicle.manual(**fields))
        manual.lift_requested.connect(self._manual_lift)
        manual.odometry_reset_requested.connect(self._reset_vehicle_odometry)
        # 수동 화면 「정지」(리프트 카드) — STOP 이면 AGV 노드가 리프트를 그 자리 높이에 붙잡는다(halt_lift).
        manual.stop_requested.connect(lambda: self.vehicle.control("STOP"))
        manual.reset_requested.connect(lambda: self.vehicle.control(reset=True))
        self.vehicle.status_changed.connect(manual.set_status)
        # I/O 화면의 차량 항목(비상정지 · 접근 감지 · 아웃트리거 · 리프트 · 수평 · 상태 등).
        self.vehicle.status_changed.connect(self.screens["io"].set_vehicle_status)
        self.vehicle.online_changed.connect(
            lambda online: None if online else self.screens["io"].set_vehicle_status(None))
        self.vehicle.status_changed.connect(self._on_pause_tick)
        self.vehicle.command_result.connect(self._show_vehicle_command_result)
        # 2026-11-17 에 더한 칸 — 비상정지 · 접근 감지 · 배터리 · 한 바퀴 측정 · 작업 시작 버튼.
        self.vehicle.status_changed.connect(self._on_vehicle_safety)
        self.vehicle.status_changed.connect(self._show_vehicle_extras)
        self.vehicle.online_changed.connect(
            lambda online: None if online else self._show_vehicle_extras({}))
        self.vehicle.status_changed.connect(self._track_measured_lap)
        self.vehicle.command_result.connect(self._on_measure_lap_result)
        self.vehicle.start_requested.connect(self._on_vehicle_start_button)
        # PLC 연결 — 차량 상태로 본다(상단 PLC · 연결 설정), 끊기면 「PLC 다시 연결」 = 차량 리셋(2026-11-20).
        self.vehicle.status_changed.connect(self._show_plc_status)
        self.vehicle.online_changed.connect(lambda online: None if online else self._show_plc_status(None))
        self.vehicle.command_result.connect(self._on_plc_reconnect_result)
        self.screens["connection"].plc_reconnect_requested.connect(self._request_plc_reconnect)
        # 목록에서 고르는 즉시 적용한다(저장은 값을 남길 뿐이다).
        self.screens["connection"].vehicle_mode_changed.connect(
            lambda _text: self._apply_vehicle_mode())
        self.screens["connection"].vehicle_mode_changed.connect(lambda _text: self._apply_agv_node())
        self.sequencer.state_changed.connect(self._sync_manual_interlock)
        # 작업 일시정지 · 재개 때 차량은 _pause_motions · _resume_motions 가 맡는다(STOP → 같은 목표로
        # 다시 보냄, 2026-11-12). 예전에는 여기서 PAUSED/RUNNING 도 보내 차량에 명령이 겹쳐 나갔다.
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
        # 2026-11-17 에 더한 칸 — 모르면(칸 없음) 조건에서 뺀다.
        return (status.get("hold") == "SET"
                and not int(status.get("error_code", 0) or 0)
                and status.get("state") in ("STOP", "HOLD")
                and not status.get("estop", False)
                and status.get("level_ok", True))

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

    # ---- AGV 제어 노드 (내장, 2026-11-24) -------------------------------------------
    #: 연결 설정을 읽은 뒤 이만큼 기다렸다 띄운다 [ms] — ROS 탐색이 돌아야 다른 PC 의 노드가 보인다.
    AGV_NODE_START_DELAY_MS = 3000

    def _apply_ros_domain(self) -> None:
        """저장한 「ROS 도메인 ID」를 바로 적용한다 — RCS 를 다시 켜지 않아도 된다(2026-11-26).

        ROS 연결(로봇 상태 · 차량 클라이언트)을 새 도메인으로 다시 열고, RCS 가 띄운 로봇 제어 노드 ·
        AGV 제어 노드도 새 도메인으로 다시 띄운다(자식 프로세스는 환경 ROS_DOMAIN_ID 를 물려받는다).
        작업 중이거나 차량이 움직이면 미룬다 — 표시에 「멈춘 뒤 다시 저장」이 남는다. 칸을 비우면
        지금 도메인을 그대로 둔다(셸 환경으로는 되돌릴 수 없다).
        """
        screen = self.screens["connection"]
        wanted = screen.ros_domain_setting()
        current = os.environ.get("ROS_DOMAIN_ID", "") or "0"
        valid = wanted.isdigit() and 0 <= int(wanted) <= 232
        if not self._ros_started or not valid or wanted == current:
            screen.set_ros_domain_state()
            return
        if self._job_running() or any(m.moving for m in (self.amr, self.lift, self.outrigger)):
            screen.set_ros_domain_state(pending=True)
            self.main_screen.show_activity(
                f"작업 중이라 ROS 도메인을 {current} → {wanted} 로 바꾸지 않았습니다 — 멈춘 뒤 다시 저장하세요.")
            return
        os.environ["ROS_DOMAIN_ID"] = wanted
        # 노드를 다시 띄우는 동안 로봇이 잠깐 끊긴다 — 알람 없이 붙인다(2026-11-26).
        self._robot_link_quiet_until = time.monotonic() + self.ROBOT_LINK_QUIET_S
        self.ros_status.stop()
        self.ros_status.start()
        notes = []
        node = self.robot_node
        if getattr(node, "_process", None) is not None:
            node.stop()
            node.start()
            notes.append("로봇 제어 노드")
        elif node.local_node_pids():
            notes.append("따로 띄운 로봇 제어 노드는 그대로 — 그 터미널에서 새 도메인으로 다시 띄우세요")
        agv = getattr(self, "agv_node", None)
        if agv is not None and agv.running:
            agv.restart()
            notes.append("AGV 제어 노드")
        screen.set_ros_domain_state()
        tail = f" — 다시 띄움: {', '.join(notes)}" if notes else ""
        self.main_screen.show_activity(f"ROS 도메인을 {current} → {wanted} 로 바꿔 다시 붙었습니다{tail}.")
        # 차량 노드처럼 로봇도 그냥 다시 붙인다 — 저장 뒤 이어서 부르는 _auto_connect_robot 이 새 노드가 뜨기를
        # 기다렸다 붙는다(작업자가 「연결 해제」해 둔 경우는 빼고). 붙었던 주소 기억은 지워 다시 붙게 한다.
        self._robot_endpoint_sent = None

    def _apply_agv_node(self) -> None:
        """연결 설정대로 AGV 제어 노드를 켜고 끈다 — 차량 제어 「ROS 차량 노드 고정」 + 「RCS 에서 실행」일 때만.

        PLC 주소 · 포트 · 쓰기 · 조작 패널이 바뀌면 다시 띄운다. 작업 중 · 차량이 움직이는 중이면 바꾸지 않는다.
        """
        screen = self.screens["connection"]
        node = self.agv_node
        host, port = screen.plc_endpoint()
        lift_mm = self.screens["vehicle"].lift_max_mm()
        config = (host, port, screen.plc_writes(), screen.agv_panel(), lift_mm / 1000.0 if lift_mm > 0 else None)
        changed = config != (node.plc_host, node.plc_port, node.writes, node.panel, node.max_lift_m)
        want = (self._ros_started and self._vehicle_mode() == "vehicle"
                and screen.agv_node_mode() == "internal")
        busy = self._job_running() or any(m.moving for m in (self.amr, self.lift, self.outrigger))
        if node.running and (not want or changed) and busy:
            self.main_screen.show_activity("작업 중이라 AGV 제어 노드를 바꾸지 않았습니다 — 멈춘 뒤 다시 저장하세요.")
            return
        node.plc_host, node.plc_port, node.writes, node.panel, node.max_lift_m = config
        if not want:
            if node.running:
                node.stop()
                self.main_screen.show_activity("AGV 제어 노드를 껐습니다.")
            if screen.agv_node_mode() == "external":
                why = "외부 — 다른 PC · 따로 띄운 노드에 붙습니다"
            elif not self._ros_started:
                why = "꺼짐 — ROS 없이 실행"
            else:
                why = "꺼짐 — 차량 제어가 「ROS 차량 노드 고정」일 때만 RCS 가 띄웁니다"
            self._agv_off_reason = why
            self._refresh_receiver()
            return
        self._agv_off_reason = ""
        if node.running:
            if changed:
                node.restart()
            self._refresh_receiver()
            return
        node.start()
        self._refresh_receiver()

    # ---- 받는 노드 · 외부 PC 확인 · PLC 포트 확인 (2026-11-24) ----------------------------------
    #: 받는 노드 표시를 다시 보는 간격 [ms] — 이 PC 의 프로세스 목록 · ROS 발행자를 본다.
    RECEIVER_REFRESH_MS = 3000

    def _receiver_status(self) -> tuple[str, str]:
        """지금 RCS 명령을 받는 AGV 노드가 어디인지 — (글, 색 이름).

        고른 위치(RCS 내부 프로그램 · 외부 PC)와 실제를 맞대 본다. 차량 상태가 오는데 이 PC 에 차량 노드
        프로세스가 없으면 외부 PC 다. 같은 도메인에 차량 노드가 둘 이상이면 명령이 둘 다로 가므로 알린다.
        """
        screen = self.screens["connection"]
        node = self.agv_node
        domain = os.environ.get("ROS_DOMAIN_ID", "") or "0"
        if not self._ros_started:
            return "없음 — ROS 없이 실행", "Muted"
        mine = node.pid
        local = AgvNodeSupervisor.local_vehicle_nodes(exclude=mine)
        local_text = ", ".join(f"{name} pid {pid}" for pid, name in local)
        online = self.vehicle.online
        publishers = self.vehicle.status_publishers() if online else []
        many = (f" · 같은 도메인에 차량 노드가 {len(publishers)}개({', '.join(publishers)}) — 명령이 모두에게 갑니다"
                if len(publishers) > 1 else "")
        if screen.agv_node_mode() == "external":
            ip = screen.external_pc_address()
            where = f"외부 PC{f' {ip}' if ip else ''}"
            if online and not local and mine is None:
                return f"{where} — 차량 상태 수신 중 (ROS 도메인 {domain}){many}", "StatusWarn" if many else "StatusGood"
            if online:
                return (f"이 PC 의 노드({local_text or f'pid {mine}'})가 받습니다 — 외부 PC 가 아닙니다{many}",
                        "StatusWarn")
            if self.vehicle.status_publishers():
                return f"{where} — {self._status_unreadable_text()}", "StatusDanger"
            return f"{where} 노드가 안 보입니다 — 「외부 PC 확인」을 누르세요 (ROS 도메인 {domain})", "StatusDanger"
        if mine is not None:
            text = f"RCS 내부 프로그램 (pid {mine}, PLC {node.endpoint()})"
            if online:
                return text + f" — 차량 상태 수신 중{many}", "StatusWarn" if many else "StatusGood"
            return text + " — 아직 차량 상태가 안 옵니다(PLC 연결 확인)", "StatusWarn"
        if online:
            if local:
                return f"이 PC 의 다른 노드({local_text})가 받습니다 — RCS 내부 프로그램은 띄우지 않았습니다{many}", \
                    "StatusWarn"
            return ("외부 PC 의 노드가 받습니다 — 같은 ROS 도메인에 이미 있어 RCS 내부 프로그램을 띄우지 않았습니다"
                    f"(「외부 PC」로 바꾸거나 그 노드를 끄세요){many}", "StatusWarn")
        reason = self._agv_off_reason or node.state
        return f"없음 — {reason}", "Muted" if self._agv_off_reason else "StatusDanger"

    def _refresh_receiver(self) -> None:
        screen = self.screens.get("connection")
        if screen is None or not hasattr(self, "agv_node"):
            return
        text, kind = self._receiver_status()
        screen.set_receiver(text, kind)

    def _check_plc_port(self) -> None:
        """「PLC 포트 확인」 — 노드와 따로, 이 PC 에서 PLC 주소:포트로 TCP 접속만 해 본다."""
        host, port = self.screens["connection"].plc_endpoint()
        if self.link_probe.run("plc", lambda: probe_tcp(host, port)):
            self.screens["connection"].set_plc_check(f"확인 중 — {host}:{port} …")

    def _check_external_pc(self) -> None:
        """「외부 PC 확인」 — 외부 PC 주소로 ping 하고, ROS 로 그 PC 의 노드가 보이는지 함께 알린다."""
        screen = self.screens["connection"]
        ip = screen.external_pc_address()
        if not ip:
            screen.set_external_check("외부 PC 주소를 넣으세요(예: 192.168.11.13).", "StatusWarn")
            return
        if self.link_probe.run("external", lambda: ping(ip)):
            screen.set_external_check(f"확인 중 — {ip} …")

    def _on_probe_finished(self, name: str, result) -> None:
        screen = self.screens["connection"]
        if name == "plc":
            screen.set_plc_check(f"● {result.text}", "StatusGood" if result.ok else "StatusDanger")
            self.main_screen.show_activity(f"PLC 포트 확인 — {result.text}")
            return
        if name != "external":
            return
        domain = os.environ.get("ROS_DOMAIN_ID", "") or "0"
        local = AgvNodeSupervisor.local_vehicle_nodes(exclude=self.agv_node.pid)
        if not result.ok:
            text, kind = result.text, "StatusDanger"
        elif self.vehicle.online and not local:
            text, kind = f"{result.text} · ROS 로 그 PC 의 노드가 보입니다(차량 상태 수신 중)", "StatusGood"
        elif self.vehicle.online:
            names = ", ".join(f"{name} pid {pid}" for pid, name in local)
            text, kind = f"{result.text} · 차량 상태는 들어오지만 이 PC 의 노드({names})가 내는 것입니다", "StatusWarn"
        elif self.vehicle.status_publishers():
            text, kind = f"{result.text} · {self._status_unreadable_text()}", "StatusWarn"
        else:
            hints = [f"그 PC 의 ROS_DOMAIN_ID 가 {domain} 인지", "ROS_LOCALHOST_ONLY 가 1 이 아닌지"]
            if "microsoft" in platform.release().lower():
                hints.append("이 PC(WSL)의 Windows Hyper-V 방화벽이 들어오는 UDP 를 막지 않는지(docs/run_guide.md)")
            hints.append("그 PC 도 fastdds_profile.xml 을 쓰는지")
            text = (f"{result.text} · 길은 있지만 ROS 로 그 PC 의 노드가 안 보입니다 — 확인: "
                    + " · ".join(f"{i}) {h}" for i, h in enumerate(hints, 1)))
            kind = "StatusWarn"
        screen.set_external_check(f"● {text}", kind)
        self.main_screen.show_activity(f"외부 PC 확인 — {text}")

    def _status_unreadable_text(self) -> str:
        """차량 상태를 내는 노드는 ROS 로 보이는데 상태가 안 들어올 때 — 메시지 정의가 다르다(2026-11-24).

        이 PC 의 RobotStatus.msg 에 lift_state 를 더한 뒤 외부 PC 의 옛 정의와 어긋나 상태를 못 읽었다(서비스는 된다).
        """
        names = ", ".join(self.vehicle.status_publishers())
        return (f"ROS 로 차량 노드({names})는 보이는데 차량 상태(RobotStatus)를 읽지 못합니다 — 두 PC 의 "
                "vehicle_interfaces 메시지 정의가 다릅니다(lift_state 등). 양쪽에서 `ros2 interface show "
                "vehicle_interfaces/msg/RobotStatus` 를 비교하고, 같은 RobotStatus.msg 로 다시 빌드 · 노드를 다시 띄우세요")

    def _vehicle_offline_text(self) -> str:
        if self.vehicle.status_publishers():
            return f"차량 상태({self.vehicle.topic('robot_status')})가 들어오지 않습니다 — {self._status_unreadable_text()}."
        domain = os.environ.get("ROS_DOMAIN_ID", "") or "0"
        return (f"차량 상태({self.vehicle.topic('robot_status')})가 들어오지 않습니다 — AGV 제어 노드: "
                f"{self.agv_node.state}. RCS 의 ROS 도메인은 {domain} 입니다 — 따로 띄운 노드는 같은 "
                "ROS_DOMAIN_ID 여야 보입니다(연결 설정 「AGV 제어 노드」를 「RCS 에서 실행」으로 두면 RCS 가 띄웁니다).")

    def _vehicle_start_blocker(self) -> str:
        """차량으로 움직여야 하는데 차량이 안 보이면 그 까닭 — 시작하면 첫 차량 동작에서 멈추므로 시작하지 않는다."""
        if self._vehicle_mode() != "vehicle" and self.amr.active != "vehicle":
            return ""
        if not self.vehicle.available:
            return ("차량 인터페이스를 쓸 수 없습니다 — ROS 워크스페이스를 소싱하고 RCS 를 다시 띄우세요"
                    " (source install/setup.bash).")
        if not self.vehicle.online:
            return self._vehicle_offline_text()
        return ""

    def _show_vehicle_link(self, online: bool) -> None:
        # 상단 AMR 은 차량 상태가 실제로 들어오는지만 보여 준다(PLC 는 차량 노드가 맡는다).
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
        self._add_alarm("E9309", message)

    # ---- PLC 연결 (2026-11-20) -----------------------------------------------------
    # AGV 판 인터페이스에는 PLC 전용 필드 · 서비스가 없다 — 차량 상태로 본다. 상태가 안 오면 모름,
    # 오류 701(PLC_COMM_LOST)이면 연결 실패, 그 밖에는 연결됨(차량 노드가 PLC 값을 싣고 있다).
    PLC_BADGE = {"CONNECTED": ("연결됨", "StatusGood"), "FAILED": ("연결 실패", "StatusDanger")}

    @staticmethod
    def _plc_state_of(status: dict | None) -> str:
        """차량 상태 → "CONNECTED" · "FAILED" · ""(모름)."""
        if not status:
            return ""
        return "FAILED" if int(status.get("error_code", 0) or 0) == PLC_COMM_LOST else "CONNECTED"

    def _show_plc_status(self, status: dict | None) -> None:
        """상단 PLC · 연결 설정의 PLC 연결을 차량 상태로 맞춘다. None = 차량 상태가 안 온다."""
        state = self._plc_state_of(status)
        error = str((status or {}).get("error_msg", "") or "") if state == "FAILED" else ""
        text, kind = self.PLC_BADGE.get(state, ("모름", "StatusWarn"))
        self.top_bar.badges["PLC"].set_state(text, kind)
        connection = self.screens["connection"]
        connection.set_plc_state(state, error)
        connection.set_plc_reconnect_enabled(
            state == "FAILED",
            "차량 상태가 들어오지 않습니다 — PLC 는 차량 노드를 거쳐서만 봅니다." if not state
            else "PLC 가 연결돼 있습니다.")
        before = getattr(self, "_plc_state", None)
        self._plc_state = state
        if state == before:
            return
        if not state:
            connection.set_plc_message("차량 상태가 들어오지 않습니다 — PLC 는 차량 노드를 거쳐서만 봅니다.")
        elif state == "FAILED":
            connection.set_plc_message("")
            self.main_screen.show_activity(
                f"PLC 연결 실패{' — ' + error if error else ''}. 연결 설정의 「PLC 다시 연결」로 다시 시도할 수 있습니다.")
        elif before == "FAILED":
            connection.set_plc_message("PLC 가 다시 연결됐습니다.")
            self.main_screen.show_activity("PLC 가 다시 연결됐습니다.")
        else:
            connection.set_plc_message("")

    def _request_plc_reconnect(self) -> None:
        """「PLC 다시 연결」 — 차량에 리셋(RobotControl reset=1)을 보낸다.

        AGV 노드는 끊긴 PLC 에 1 초마다 스스로 다시 붙어 보지만, 701 은 걸어 두고(latched) 리셋을 받아야
        푼다. PLC 가 아직 안 붙었으면 리셋을 "reset cause still active" 로 거절한다 — 붙은 뒤 다시 누른다.
        결과는 command_result(robot_control)로 온다(_on_plc_reconnect_result)."""
        connection = self.screens["connection"]
        if getattr(self, "_plc_state", None) != "FAILED":
            connection.set_plc_message("PLC 가 끊겨 있지 않습니다 — 다시 연결할 것이 없습니다.")
            return
        self._plc_reconnect_pending = True
        connection.set_plc_message("PLC 다시 연결 — 차량에 리셋을 보냅니다…")
        self.vehicle.control(reset=True)

    def _on_plc_reconnect_result(self, name: str, ok: bool, message: str) -> None:
        if name != "robot_control" or not getattr(self, "_plc_reconnect_pending", False):
            return
        self._plc_reconnect_pending = False
        if ok:
            text = ("PLC 다시 연결 — 차량이 리셋을 받았습니다. 다시 붙으면 위 상태가 「연결됨」으로 바뀝니다"
                    + (f" ({message})" if message else ""))
        elif "cause still active" in message:
            text = (f"PLC 다시 연결을 못 했습니다 — PLC 가 아직 붙지 않았습니다({message}). 차량 노드가 스스로 "
                    "다시 붙어 봅니다 — PLC 를 확인하고 잠시 뒤 다시 누르세요.")
        else:
            text = f"PLC 다시 연결을 못 했습니다 — {message}"
        self.screens["connection"].set_plc_message(text)
        self.main_screen.show_activity(text)

    def _manual_lift(self, height_m: float) -> None:
        """수동 제어 화면의 리프트 — ManualCommand(cmd_mv_lift 1, lift_height). 속도는 PLC 가 정한다."""
        self._last_manual_lift_mm = height_m * 1000.0          # 거절되면 안내에 쓴다
        if self.vehicle.manual_lift(height_m):
            self.main_screen.show_activity(f"[차량] 리프트(수동) → {height_m * 1000:,.0f} mm")

    def _reset_vehicle_odometry(self, x: float, y: float, yaw: float) -> None:
        """수동 제어 화면 「위치 다시 맞추기」 — ResetOdometry(x m, y m, yaw rad)."""
        self._odometry_pose = (x, y, yaw)
        self.vehicle.reset_odometry(x, y, yaw)

    #: 같은 거절을 다시 알리지 않는 시간 [s] — 수동 조그는 누르는 동안 0.2 s 마다 다시 보낸다.
    VEHICLE_REJECT_REPEAT_S = 3.0
    #: 수동 제어 화면에도 거절 까닭을 보이는 명령.
    _MANUAL_SCREEN_COMMANDS = ("manual_command", "reset_odometry", "robot_control", "lift_control")

    def _show_vehicle_command_result(self, name: str, ok: bool, message: str) -> None:
        if not ok:
            lift_max = self.screens["vehicle"].lift_max_mm()
            reason = explain_reject(message, lift_max)
            asked = getattr(self, "_last_manual_lift_mm", None)
            if "lift height out of range" in message and name == "manual_command" and asked is not None:
                reason += f" — 요청 {asked:,.0f} mm"
                if 0 <= asked <= lift_max:
                    # 한계 안인데 거절 — AGV 노드가 옛 상한(노드 기본 1,500 mm)으로 떠 있다.
                    reason += (f". 한계 안인데 거절됐습니다 — AGV 노드가 옛 상한으로 떠 있습니다. RCS 를 다시 켜거나 "
                               f"차량 설정 「리프트 한계 높이」를 저장하면 {lift_max:,.0f} mm 로 다시 띄웁니다"
                               "(외부 PC 노드면 그 실행 명령에 max_lift_height 를 붙이세요)")
            if name in self._MANUAL_SCREEN_COMMANDS:
                self.screens["manual"].show_result(f"차량이 거절했습니다 — {reason}")
            key = (name, message)
            now = time.monotonic()
            last = getattr(self, "_vehicle_reject_last", (None, 0.0))
            if last[0] == key and now - last[1] < self.VEHICLE_REJECT_REPEAT_S:
                return
            self._vehicle_reject_last = (key, now)
            note = f" ({message})" if reason != message else ""
            self.main_screen.show_activity(f"차량 {name} 거절: {reason}{note}")
            return
        if name in self._MANUAL_SCREEN_COMMANDS:
            self.screens["manual"].show_result("")
        if name == "reset_odometry":
            x, y, yaw = getattr(self, "_odometry_pose", (0.0, 0.0, 0.0))
            self.main_screen.show_activity(
                f"[차량] 위치를 x {x:.3f} m · y {y:.3f} m · yaw {math.degrees(yaw):.1f}° 로 맞췄습니다.")

    def _sync_manual_interlock(self, *_args) -> None:
        """로봇이 동작 중이거나 홈을 벗어나 있으면 수동 주행·리프트를 잠근다.

        태스크가 멈춰 있어도 팔이 벽 앞에 뻗어 있으면(홈 플래그 0) 차량을
        움직이면 안 된다. 잠겨 있으면 먼저 로봇을 홈으로 보낸다.
        """
        job = "작업이 진행 중입니다" if self._job_running() else ""
        # 팔이 받침대 위면 주행 · 아웃트리거 해제는 되고 리프트만 잠근다(2026-11-26).
        drive = self._robot_motion_block_reason("drive") or job
        lift = self._robot_motion_block_reason("lift") or job
        self.screens["manual"].set_interlock(bool(drive), drive, lift_reason=lift)

    def _on_vehicle_problem(self, message: str) -> None:
        """차량이 도착을 못 냈다 — 장애로 올린다(작업을 멈추고 로봇도 세운다).

        차량 · 리프트 · 아웃트리거가 움직이지 못한 것은 표준 E2001 DRIVE_ERROR 다
        (구동 · 이동 대역 — if-0.9 탭5 119행). 예전 3S 코드는 E9301 이었다.
        """
        self.erut_session.raise_error({
            "code": "E2001", "name": "DRIVE_ERROR", "level": "stop",
            "recovery": "manual", "detail": f"차량 동작 실패 — {message}",
        })
        self._add_alarm("E2001", f"차량: {message}")
        # 「계측 시작」 주행이 실패했다 — 계측을 접는다. ERUT 캘리브레이션은 장애(stop)로 세션이 접는다.
        self._end_local_calibration(f"차량 동작 실패 — {message}")
        if not self.erut_session._calibrate_req_id:
            self.main_screen.set_pause_override(None)

    def _record_job(self, source: str, job_id: str, plan) -> None:
        self.data_recorder.begin_job(source, job_id, plan, nosensor=self._nosensor)
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
            self._add_alarm("E9308",
                "차량 인터페이스를 쓸 수 없습니다 — ROS 워크스페이스를 소싱하고 RCS 를 다시 띄우세요"
                " (source install/setup.bash).")
        elif not self.vehicle.online:
            self._add_alarm("E9307", self._vehicle_offline_text() + " 이대로 두면 첫 동작에서 멈춥니다.")

    def _record_cell_status(self, label: str, status: str) -> None:
        if status == CellStatus.EXECUTING.value:
            self.data_recorder.cell_started(label)
        elif status == CellStatus.COMPLETED.value:
            self.data_recorder.cell_finished(label, "완료")

    def _record_sequencer_state(self, name: str) -> None:
        if name == SequencerState.STOPPED.name:
            self.data_recorder.job_stopped("중단")

    def _record_erut_error(self, code: str, message: str, level: str) -> None:
        self.data_recorder.log_event("장애", message, code=code, level=level)

    # ------------------------------------------------------------ 기록 화면
    def _setup_record_screens(self) -> None:
        errors, logs, modes = self.screens["errors"], self.screens["logs"], self.screens["modes"]
        errors.bind(self.data_recorder, self.erut_session.error_codes)
        errors.clear_requested.connect(self._clear_errors)
        self.data_recorder.event_logged.connect(errors.on_event_logged)
        self.erut_session.error_published.connect(errors.refresh_active)
        self.erut_session.error_cleared.connect(errors.refresh_active)
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
    #: 슬롯에 넣는 폼 화면 — 불러올 때 이 순서로 적용한다(2026-11-24: 연결 · 시스템 · 차량 설정도 넣는다.
    #: 예전에는 장비 고유값이라 뺐다). 연결이 먼저라야 차량 · 로봇 값이 맞는 장비로 간다.
    MODE_FORM_SCOPES = ("system", "connection", "vehicle", "cobot")
    #: 폼에 있어도 슬롯에서 빼는 항목 — 태스크 판은 robot_task 에 따로 있다.
    MODE_SKIP_FIELDS = ("태스크 판",)

    def _mode_snapshot(self) -> dict:
        """지금 작업 조건을 한데 묶는다(작업 영역·검사 대상·Cobot·태스크)."""
        width, height, scan_h, overlap = self.main_screen.rect_view.work_area()
        area = {"width_mm": width, "height_mm": height, "scan_h_mm": scan_h,
                "overlap_mm": overlap, **self._work_area_extra}
        target = self.settings_service.known("inspection_target")
        diameter_m, height_m = self.main_screen.orbit_view.target_dimensions()
        shape, width_m = self.main_screen.orbit_view.target_shape()
        target.update({"diameter_m": diameter_m, "height_m": height_m,
                       "shape": shape, "width_m": width_m})
        snap = {"work_area": area, "inspection_target": target,
                "robot_task": {"nosensor": self._nosensor, "task_version": self._task_version}}
        for scope in self.MODE_FORM_SCOPES:
            values = self._settings_screens[scope].values()
            for key in self.MODE_SKIP_FIELDS:      # 태스크 판은 robot_task 에 있다
                values.pop(key, None)
            snap[scope] = values
        # 로봇 기준 위치(홈 · 스캔 대기 · 그리퍼 접촉 · 조립) — 조그 화면에 저장한 값(2026-11-24).
        snap["reference_poses"] = {
            target: {"values": [float(v) for v in entry["values"]], "saved_at": entry.get("saved_at", "")}
            for target, _labels in self.POSE_TARGETS.values()
            if isinstance(entry := self._reference_poses.get(target), dict) and entry.get("values")}
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
        self._set_current_mode(slot, name)
        missing = [label for key, label in POSE_NAMES if key not in snap.get("reference_poses", {})]
        if missing:
            # 이 슬롯을 불러와도 빠진 위치는 그때의 값이 그대로 남는다 — 모재마다 다른 그리퍼 접촉 위치가 특히(2026-11-25).
            modes.show_status(f"슬롯 {slot}에 '{name}'을(를) 저장했습니다 — 저장 안 된 기준 위치: {' · '.join(missing)}"
                              "(조그 화면에서 저장한 뒤 다시 저장하세요. 불러와도 그 위치는 바뀌지 않습니다).",
                              "StatusWarn")
        else:
            modes.show_status(f"슬롯 {slot}에 '{name}'을(를) 저장했습니다(기준 위치 4개 포함).", "StatusGood")
        self.data_recorder.log_event("정보", f"운전 모드 저장: 슬롯 {slot} '{name}'")

    def _clear_mode_slot(self, slot: int) -> None:
        if self._current_mode and self._current_mode.get("slot") == slot:
            self._set_current_mode(None, "")
        self._mode_slots[str(slot)] = {}
        self._mode_slots_touched = True
        self.settings_service.save("mode_slots", self._mode_slots)
        modes = self.screens["modes"]
        modes.set_slots(self._mode_slots)
        modes.show_status(f"슬롯 {slot}을(를) 비웠습니다.")

    def _load_mode_slot(self, slot: int, startup: bool = False) -> None:
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
        poses = snap.get("reference_poses") or {}
        if poses and self._robot_task_running():
            modes.show_status("로봇 태스크가 도는 중에는 기준 위치가 든 운전 모드를 불러오지 않습니다 — 멈춘 뒤 "
                              "불러오세요.", "StatusDanger")
            return
        domain_before = self.screens["connection"].ros_domain_setting()
        # 0) 시스템 · 연결 · 차량 · Cobot 폼 — 저장 버튼을 누른 것과 같다(MODE_FORM_SCOPES 순서). 연결이 바뀌면
        #    ERUT · 로봇 · AGV 노드가 저장할 때처럼 다시 붙고, Cobot 속도는 로봇으로 간다.
        for scope in self.MODE_FORM_SCOPES:
            values = snap.get(scope)
            if values:
                screen = self._settings_screens[scope]
                screen.apply_values(dict(values))
                screen.save_requested.emit(scope, screen.values())
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
        # 3) 태스크 — 저장하고 경로를 넘기고, 한가하면 그 스캔 태스크를 불러온다
        if task:
            self._apply_task_choice(self._task_version, "운전 모드 태스크")
        # 4) 로봇 기준 위치 — 파일 · 화면 · 로봇 레지스터(조그 화면 「저장」과 같다)
        restored = self._restore_mode_poses(poses)
        note = f" · 기준 위치 {restored}개({' · '.join(self._pose_names(poses))})" if restored else ""
        kept = [name for key, name in POSE_NAMES if key not in poses and self._reference_poses.get(key)]
        if kept:
            # 받침대 높이가 다른 모재로 바꿀 때 가장 위험하다 — 앞 모재의 값이 남는다(2026-11-25).
            note += f" · 슬롯에 없어 지금 값을 그대로 둔 위치: {' · '.join(kept)}"
        if self.screens["connection"].ros_domain_setting() != domain_before:
            note += " · ROS 도메인 ID 가 바뀌어 새 도메인으로 다시 붙습니다"
        modes.show_status(f"슬롯 {slot} '{name}'을(를) 불러왔습니다{note}.", "StatusWarn" if kept else "StatusGood")
        self._set_current_mode(slot, name)
        self.main_screen.show_activity(
            f"{'지난번 ' if startup else ''}운전 모드 '{name}'을(를) {'다시 ' if startup else ''}불러왔습니다{note}.")

    # ---- 지금 운전 모드 (2026-11-25) -------------------------------------------------
    def _set_current_mode(self, slot: int | None, name: str) -> None:
        """지금 운전 모드를 메인 화면에 보이고, 다음에 켤 때 다시 불러오도록 남긴다."""
        self._current_mode = {"slot": int(slot), "name": str(name)} if slot else None
        self.main_screen.set_current_mode(f"슬롯 {slot} · {name}" if slot else "")
        self.settings_service.save("mode_current", dict(self._current_mode or {"slot": 0, "name": ""}))

    def _on_initial_scope(self, scope: str) -> None:
        """켤 때 불러오는 설정이 다 오면 지난번 운전 모드를 다시 적용한다(한 번)."""
        pending = getattr(self, "_initial_scopes", None)
        if not pending or scope not in pending:
            return
        pending.discard(scope)
        if not pending:
            self._restore_last_mode()

    def _restore_last_mode(self) -> None:
        last = self._last_mode
        if not last:
            return
        slot, name = last["slot"], last.get("name", "")
        if not self._restore_mode_on_start:
            self.main_screen.set_current_mode(f"슬롯 {slot} · {name}")
            return
        if not self._mode_slots.get(str(slot)):
            self.main_screen.set_current_mode("")
            self.main_screen.show_activity(f"지난번 운전 모드(슬롯 {slot} · {name})가 비어 있어 불러오지 않았습니다.")
            return
        self._load_mode_slot(slot, startup=True)
        if not self._current_mode:
            self.main_screen.set_current_mode(
                f"슬롯 {slot} · {name} (불러오지 못함 — 설정 / 로그 › 운전 모드 저장에서 다시 불러오세요)")

    @staticmethod
    def _pose_names(poses: dict) -> list[str]:
        return [name for key, name in POSE_NAMES if key in poses]

    def _restore_mode_poses(self, poses: dict) -> int:
        """슬롯의 기준 위치를 되살린다 — 슬롯에 있는 것만(없는 위치는 지우지 않는다). 되살린 개수."""
        entries = {}
        for command, (target, _labels) in self.POSE_TARGETS.items():
            entry = poses.get(target)
            values = entry.get("values") if isinstance(entry, dict) else None
            if not values or len(values) != 6:
                continue
            entry = {"values": [float(v) for v in values], "saved_at": str(entry.get("saved_at") or "")}
            entries[target] = entry
            self._reference_poses[target] = entry
            self._show_saved_pose(command, entry)
            self.ros_status.send_pose(target, entry["values"])
        if not entries:
            return 0
        if "start_pose" in entries:
            self._sync_contact_height()
        if restore_reference_poses(entries) is None:
            self.main_screen.show_activity("운전 모드의 기준 위치를 파일에 저장하지 못했습니다(화면 · 로봇에는 넣었습니다).")
        return len(entries)

    def _show_recorder_problem(self, message: str) -> None:
        self.main_screen.show_activity(f"운영 기록 문제: {message}")
        self._add_alarm("E9506", message)

    def closeEvent(self, event) -> None:  # noqa: N802
        """창 종료 전에 ERUT 접속과 ROS 구독을 정리한다."""
        # ERUT 도 닫는다 — offline 을 직접 남기고 paho 스레드를 거둔다. 안 하면
        # 창이 사라진 뒤에도 스레드가 신호를 쏴 프로세스가 죽는다.
        self.erut.stop()
        self.ros_status.stop()
        self.data_recorder.close()
        self.screens["tpac_bridge"].shutdown()
        # 우리가 띄운 노드만 거둔다(따로 띄운 노드는 남의 것이다).
        self.robot_node.stop()
        self.agv_node.stop()
        # 걸어 둔 지연 동작(play·홈·대기 해제 등)도 거둔다 — 창이 닫힌 뒤
        # 로봇에 명령이 나가면 안 되고, 사라진 객체를 부르다 프로세스가 죽는다.
        for timer in self.findChildren(QTimer):
            timer.stop()
        super().closeEvent(event)


def create_application(argv: list[str] | None = None) -> QApplication:
    """QApplication을 생성하거나 재사용하고 공통 글꼴과 QSS를 적용한다."""
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("SMR Operator UI")
    app.setStyle("Fusion")
    font_path = files("smr_operator_ui.resources").joinpath("malgun.ttf")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    # 1024x768 기준 크기로 줄여 건다(UI_BASE_SCALE).
    app.setStyleSheet(_scale_stylesheet(load_stylesheet(), UI_BASE_SCALE))
    install_wheel_guard(app)
    return app


def main() -> int:
    """Qt 이벤트 루프를 실행하고 프로세스 종료 코드를 반환한다."""
    if "--offscreen" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = create_application()
    window = OperatorWindow()
    window.show_fitted()
    return app.exec()
