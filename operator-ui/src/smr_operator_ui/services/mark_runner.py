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

**격자 지정 마킹**(사내 MC `mark_cmd`)은 점에 자리가 이미 정해져 온다 —
`amr_mm`(차량 위치 = 그 열 격자의 원점 쪽 끝), `lift_mm`(그 행 격자의 아래
끝 높이), `u`·`v`(격자 안 좌표: 원점부터 호를 따라 u, 아래에서 위로 v). 이
값이 있으면 위의 가운데 맞춤 대신 그대로 쓴다.

**마커는 ERUT 것이다**(미니 PC 가 쏜다). 로봇은 마킹 자리에 붙으면 290 = 11
로 알리고 **서서 기다린다** — 마커가 쏘는 동안 팔이 떠나면 엉뚱한 자리에
찍힌다. 이 순간 `point_reached(id)` 를 내고(ERUT 에 evt/mark_ready), 마킹이
끝났다는 `point_marked(id, marked)` 가 오면 대기 해제(278 = 1)를 보내 로봇을
홈으로 돌린다. `hold=False` 로 돌리면(사내 MC 의 격자 지정 마킹처럼 바깥에
확인할 마커가 없을 때) 붙자마자 바로 푼다.

다 끝나면 스캔 태스크를 다시 불러 두고 `finished(marked, failed)` 를 낸다.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

#: 로봇 마킹 태스크의 상태값(레지스터 290). dus_mark_point / dus_home 이 쓴다.
MARK_STATE_AT_POINT = 11    # 마킹 자리에 붙어 대기 해제(278)를 기다린다
MARK_STATE_DONE = 12        # 홈 도착 = 이 점 끝
MARK_STATE_FAILED = 9       # 벽 접촉 실패 등으로 멈춤


class MarkRunner(QObject):
    """마킹 점들을 차례로 돈다. 장비는 전부 주입받는다(더미든 실물이든)."""

    activity = pyqtSignal(str)
    #: 모든 점을 끝냈다. (마킹한 점 id 들, 실패한 점 id 들)
    finished = pyqtSignal(list, list)
    #: 로봇이 마킹 자리에 붙어 기다린다 (점 id). ERUT 에 evt/mark_ready 로 알린다.
    point_reached = pyqtSignal(str)
    #: 점으로 가거나 물러나던 로봇을 그 자리에 세운다 / 잇는다(일시정지·재개).
    robot_pause_requested = pyqtSignal()
    robot_resume_requested = pyqtSignal()

    #: 점 하나에 주는 시간 [ms]. 프로브 3점 + 이동 + 홈이라 넉넉히.
    POINT_TIMEOUT_MS = 300_000

    def __init__(self, amr, lift, outrigger, retractor,
                 send_target: Callable[[float, float], object],
                 start_mark_task: Callable[[], object],
                 restore_scan_task: Callable[[], object],
                 release_hold: Callable[[], object] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._amr, self._lift = amr, lift
        self._outrigger, self._retractor = outrigger, retractor
        self._send_target = send_target
        self._start_mark_task = start_mark_task
        self._restore_scan_task = restore_scan_task
        # 마킹 자리 대기를 푼다(레지스터 278 = 1). 로봇은 이게 올 때까지 선다.
        self._release_hold = release_hold or (lambda: None)
        self._hold = True
        self._at_point = False
        self._point_ok = True
        self._points: list[dict] = []
        self._index = -1
        self._cell_w = self._cell_h = 0.0
        self._step = ""
        self._saw_busy = False
        self._marked: list[str] = []
        self._failed: list[str] = []
        # 일시정지(if-0.7 마킹 ② ⑥). 멈춘 동안 도착한 장비는 재개 때 이어 받는다.
        self._paused = False
        self._arrival_during_pause = ""
        self._timer_left_ms = 0
        #: 차량을 다른 자리로 옮기기 전 준비(리프트 내리기·아웃트리거 풀기)를
        #: 하고 나서 넘긴 함수를 부르는 훅. 앱이 넣는다. 없으면 바로 옮긴다.
        self.prepare_drive: Callable[[Callable[[], object]], object] | None = None
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

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def step(self) -> str:
        """지금 기다리는 단계 — amr · secure · lift · robot · marking · retract."""
        return self._step

    @property
    def waiting_point(self) -> str:
        """마킹 자리에서 마킹 끝을 기다리는 점 id. 아니면 빈 문자열."""
        if self.running and self._step == "marking":
            return str(self._point().get("id", ""))
        return ""

    def progress(self) -> tuple[list[str], list[str]]:
        """(마킹한 점, 못 한 점 + 아직 안 간 점). 도중에 멈출 때 완료에 싣는다."""
        done = set(self._marked) | set(self._failed)
        rest = [str(p.get("id")) for p in self._points if str(p.get("id")) not in done]
        return list(self._marked), list(self._failed) + rest

    def point_marked(self, point_id: str, marked: bool = True) -> bool:
        """마킹이 끝났다(ERUT req/mark_next). 대기를 풀어 로봇을 돌려보낸다."""
        if self._paused or self.waiting_point != str(point_id):
            return False
        self._point_ok = bool(marked)
        self._step = "robot"
        self._release_hold()
        self.activity.emit(
            f"마킹 {point_id}: {'찍음' if marked else '못 찍음'} — 로봇이 물러납니다.")
        return True

    def start(self, points: list[dict], cell_w_mm: float, cell_h_mm: float,
              hold: bool = True) -> None:
        """점 목록을 받아 첫 점부터 돈다. 점은 {id, x, y} (mm).

        `hold` 가 참이면 점마다 마킹 자리에서 `point_marked` 를 기다린다.
        """
        self._hold = bool(hold)
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
        self._paused = False
        self._arrival_during_pause = ""
        if was_running:
            self._restore_scan_task()
            self.activity.emit("마킹을 중단했습니다.")

    # ------------------------------------------------------------ 일시정지
    def pause(self) -> None:
        """그 자리에 선다(if-0.7 마킹 ② ⑥).

        * 점에서 마킹을 기다리는 중이면 로봇은 이미 서 있다 — 그대로 둔다.
        * 로봇이 점으로 가거나 물러나는 중이면 로봇 태스크를 그 자리에 세운다.
        * 차량·리프트·아웃트리거는 가던 곳까지 가서 선다. 그 도착은 재개 때 잇는다.
        점마다 주는 시간도 멈춘다.
        """
        if not self.running or self._paused:
            return
        self._paused = True
        if self._timer.isActive():
            self._timer_left_ms = max(1, self._timer.remainingTime())
            self._timer.stop()
        if self._step == "robot":
            self.robot_pause_requested.emit()
        self.activity.emit("마킹을 그 자리에 일시정지했습니다.")

    def resume(self) -> None:
        """멈춘 자리에서 잇는다. 점에서 기다리던 중이면 mark_ready 를 다시 낸다."""
        if not self.running or not self._paused:
            return
        self._paused = False
        if self._timer_left_ms:
            self._timer.start(self._timer_left_ms)
            self._timer_left_ms = 0
        self.activity.emit("마킹을 이어 갑니다.")
        arrived, self._arrival_during_pause = self._arrival_during_pause, ""
        if arrived:
            self._advance(arrived)
        elif self._step == "robot":
            self.robot_resume_requested.emit()
        elif self._step == "marking":
            # 기다리던 점을 다시 알린다 — ERUT 가 그 점을 찍고 mark_next 를 보낸다.
            self.point_reached.emit(self.waiting_point)

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
        if "amr_mm" in pt:
            x0 = float(pt["amr_mm"])
        else:
            x0 = float(pt["x"]) - self._cell_w / 2.0
        self._step = "amr"
        self._timer.start(self.POINT_TIMEOUT_MS)
        self.activity.emit(
            f"마킹 {pt['id']} ({self._index + 1}/{len(self._points)}): 차량 정렬")

        def drive() -> None:
            self._amr.move_to(x0, " mm", label=f"마킹 {pt['id']} ({x0:.0f} mm)")

        if self.prepare_drive is not None and abs(x0 - float(self._amr.position)) > 1.0:
            # 다른 자리로 간다 — 리프트를 내리고 아웃트리거를 푼 뒤에 달린다.
            self.prepare_drive(drive)
        else:
            drive()

    def _advance(self, arrived: str) -> None:
        """장비 하나가 도착했다. 지금 기다리던 단계면 다음으로 넘어간다."""
        if not self.running or arrived != self._step:
            return
        if self._paused:
            self._arrival_during_pause = arrived
            return
        pt = self._point()
        if arrived == "amr":
            self._step = "secure"
            self._outrigger.move_to(1, " 고정")
        elif arrived == "secure":
            self._step = "lift"
            self._lift.move_to(float(pt["lift_mm"] if "lift_mm" in pt else pt["y"]), " mm")
        elif arrived == "lift":
            self._step = "robot"
            self._saw_busy = False
            self._at_point = False
            self._point_ok = True
            # 점을 격자 가운데·원점 높이에 뒀으므로 로봇 목표는 (W/2, 0).
            # 격자 지정 마킹이면 격자 안 좌표(u, v)를 그대로 준다.
            self._send_target(float(pt.get("u", self._cell_w / 2.0)), float(pt.get("v", 0.0)))
            self._start_mark_task()
            self.activity.emit(f"마킹 {pt['id']}: 로봇이 프로브 후 마킹 자리로 갑니다.")
        elif arrived == "retract":
            self._next_point()

    def handle_scan_state(self, values: list[int]) -> None:
        """로봇 상태(290)로 이 점이 어디까지 왔는지 본다."""
        if not self.running or self._step not in ("robot", "marking") or not values:
            return
        state = int(values[0])
        if state not in (MARK_STATE_DONE, MARK_STATE_FAILED):
            # 로봇이 이번 점을 실제로 시작했다 — 지난 점의 12 가 남아 있어도
            # 이제부터의 12 만 인정한다.
            self._saw_busy = True
            if state == MARK_STATE_AT_POINT and not self._at_point:
                self._reached_point()
            return
        if not self._saw_busy:
            return
        self._finish_point(ok=(state == MARK_STATE_DONE and self._point_ok))

    def _reached_point(self) -> None:
        """로봇이 마킹 자리에 붙어 섰다(290 = 11)."""
        self._at_point = True
        pt_id = str(self._point().get("id", self._index + 1))
        if not self._hold:
            # 바깥에 확인할 마커가 없다 — 바로 풀어 준다.
            self._release_hold()
            return
        self._step = "marking"
        self.activity.emit(f"마킹 {pt_id}: 자리에 붙었습니다 — 마킹 끝을 기다립니다.")
        self.point_reached.emit(pt_id)

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
        if self._step == "marking":
            # 로봇이 자리에서 기다리고 있다 — 풀어 줘야 홈으로 돌아간다.
            self._release_hold()
        self._failed.append(pt_id)
        self._step = "retract"
        self._retractor.move_to(1, " 복귀")
