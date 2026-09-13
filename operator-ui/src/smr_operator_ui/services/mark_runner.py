"""ERUT 마킹 요청을 점마다 차량·리프트·로봇으로 풀어 돈다.

ERUT 는 결함 자리를 검사면 좌표(x = 원주 전개, y = 높이 — area 와 같은 기준)
로 준다. 점 하나마다 이렇게 한다.

  1. 차량을 x - W/2 로 옮긴다      점이 격자 **가운데**(호 중앙)에 오게
  2. 아웃트리거 고정
  3. 리프트를 y 로 올린다          점이 로봇 원점 **높이**에 오게
  4. 로봇: 마킹 태스크를 틀어 프로브 3점 -> 마킹 자리 -> 홈 (290 == 12)
  5. 안전 위치로 물러난다

점을 격자 가운데·원점 높이에 두는 이유: 그러면 로봇 쪽 목표가
(u, v) = (W/2, 0) 이 되어 **가운데 프로브가 이미 닿아 본 자리**와 같다.
호 계산이 틀릴 여지가 가장 적다. (로봇 스크립트는 임의의 u, v 도 받는다.)

다 끝나면 스캔 태스크를 다시 불러 두고 `finished(marked, failed)` 를 낸다.
마킹 동작 자체(스프레이/마커)는 아직 TODO 다 — 로봇은 그 자리에 붙었다가
홈으로 돌아올 뿐이다.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

#: 로봇 마킹 태스크의 상태값(레지스터 290). dus_mark_point / dus_home 이 쓴다.
MARK_STATE_DONE = 12        # 홈 도착 = 이 점 끝
MARK_STATE_FAILED = 9       # 벽 접촉 실패 등으로 멈춤


class MarkRunner(QObject):
    """마킹 점들을 차례로 돈다. 장비는 전부 주입받는다(더미든 실물이든)."""

    activity = pyqtSignal(str)
    #: 모든 점을 끝냈다. (마킹한 점 id 들, 실패한 점 id 들)
    finished = pyqtSignal(list, list)

    #: 점 하나에 주는 시간 [ms]. 프로브 3점 + 이동 + 홈이라 넉넉히.
    POINT_TIMEOUT_MS = 300_000

    def __init__(self, amr, lift, outrigger, retractor,
                 send_target: Callable[[float, float], object],
                 start_mark_task: Callable[[], object],
                 restore_scan_task: Callable[[], object],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._amr, self._lift = amr, lift
        self._outrigger, self._retractor = outrigger, retractor
        self._send_target = send_target
        self._start_mark_task = start_mark_task
        self._restore_scan_task = restore_scan_task
        self._points: list[dict] = []
        self._index = -1
        self._cell_w = self._cell_h = 0.0
        self._step = ""
        self._saw_busy = False
        self._marked: list[str] = []
        self._failed: list[str] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        amr.arrived.connect(lambda: self._advance("amr"))
        outrigger.arrived.connect(lambda: self._advance("secure"))
        lift.arrived.connect(lambda: self._advance("lift"))
        retractor.arrived.connect(lambda: self._advance("retract"))

    @property
    def running(self) -> bool:
        return 0 <= self._index < len(self._points)

    def start(self, points: list[dict], cell_w_mm: float, cell_h_mm: float) -> None:
        """점 목록을 받아 첫 점부터 돈다. 점은 {id, x, y} (mm)."""
        self._points = list(points)
        self._cell_w, self._cell_h = float(cell_w_mm), float(cell_h_mm)
        self._marked, self._failed = [], []
        self._index = -1
        self.activity.emit(f"마킹 시작: {len(self._points)}점")
        self._next_point()

    def cancel(self) -> None:
        """마킹을 접는다(abort). 완료(finished)는 내지 않는다.

        도중이었으면 로봇에 마킹 태스크가 올라가 있으므로 **스캔 태스크로
        되돌려 둔다** — 안 그러면 다음 prepare 가 play 할 때 마킹 태스크가
        돈다.
        """
        was_running = self.running
        self._timer.stop()
        self._index = len(self._points)
        self._step = ""
        if was_running:
            self._restore_scan_task()
            self.activity.emit("마킹을 중단했습니다.")

    # ------------------------------------------------------------ 점 하나
    def _point(self) -> dict:
        return self._points[self._index]

    def _next_point(self) -> None:
        self._index += 1
        if self._index >= len(self._points):
            self._timer.stop()
            self._step = ""
            self._restore_scan_task()
            self.activity.emit(
                f"마킹 끝 — 성공 {len(self._marked)}, 실패 {len(self._failed)}")
            self.finished.emit(list(self._marked), list(self._failed))
            return
        pt = self._point()
        x0 = float(pt["x"]) - self._cell_w / 2.0
        self._step = "amr"
        self._timer.start(self.POINT_TIMEOUT_MS)
        self.activity.emit(
            f"마킹 {pt['id']} ({self._index + 1}/{len(self._points)}): 차량 정렬")
        self._amr.move_to(x0, " mm", label=f"마킹 {pt['id']} ({x0:.0f} mm)")

    def _advance(self, arrived: str) -> None:
        """장비 하나가 도착했다. 지금 기다리던 단계면 다음으로 넘어간다."""
        if not self.running or arrived != self._step:
            return
        pt = self._point()
        if arrived == "amr":
            self._step = "secure"
            self._outrigger.move_to(1, " 고정")
        elif arrived == "secure":
            self._step = "lift"
            self._lift.move_to(float(pt["y"]), " mm")
        elif arrived == "lift":
            self._step = "robot"
            self._saw_busy = False
            # 점을 격자 가운데·원점 높이에 뒀으므로 로봇 목표는 (W/2, 0).
            self._send_target(self._cell_w / 2.0, 0.0)
            self._start_mark_task()
            self.activity.emit(f"마킹 {pt['id']}: 로봇이 프로브 후 마킹 자리로 갑니다.")
        elif arrived == "retract":
            self._next_point()

    def handle_scan_state(self, values: list[int]) -> None:
        """로봇 상태(290)로 이 점이 끝났는지 본다."""
        if not self.running or self._step != "robot" or not values:
            return
        state = int(values[0])
        if state not in (MARK_STATE_DONE, MARK_STATE_FAILED):
            # 로봇이 이번 점을 실제로 시작했다 — 지난 점의 12 가 남아 있어도
            # 이제부터의 12 만 인정한다.
            self._saw_busy = True
            return
        if not self._saw_busy:
            return
        self._finish_point(ok=(state == MARK_STATE_DONE))

    def _finish_point(self, ok: bool) -> None:
        pt_id = str(self._point().get("id", self._index + 1))
        (self._marked if ok else self._failed).append(pt_id)
        self.activity.emit(f"마킹 {pt_id}: {'완료' if ok else '실패'} — 안전 위치로")
        self._step = "retract"
        self._retractor.move_to(1, " 복귀")

    def _on_timeout(self) -> None:
        if not self.running:
            return
        pt_id = str(self._point().get("id", self._index + 1))
        self.activity.emit(f"마킹 {pt_id}: 시간 초과 — 실패로 넘깁니다.")
        self._failed.append(pt_id)
        self._step = "retract"
        self._retractor.move_to(1, " 복귀")
