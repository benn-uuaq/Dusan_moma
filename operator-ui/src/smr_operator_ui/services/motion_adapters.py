"""리프트와 AMR 이동을 대신하는 더미 어댑터.

실제 PLC와 차량이 아직 없어서 이동 명령을 받으면 잠시 뒤 "도착했다"고
답해 주기만 한다. 덕분에 장비 없이도 `1A`부터 `12F`까지 전체 순회를
끝까지 돌려볼 수 있다.

`JobSequencer`는 이 어댑터와 시그널로만 이어져 있으므로, 나중에 실제
PLC/AMR 경로가 생기면 **시퀀서를 고치지 않고 이 파일만 갈아끼우면 된다.**
같은 시그널(`arrived`)만 내주면 된다.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class DummyMotionAdapter(QObject):
    """이동 요청을 받으면 정해진 시간 뒤 도착을 알리는 더미.

    도착만 알리는 게 아니라 **가는 동안의 위치도 만들어 낸다.** 바깥(ERUT)
    은 리프트 높이와 AMR 이동 거리를 화면에 표시하는데, 값이 목표로 툭
    건너뛰면 진행 중인지 선 건지 알 수 없다. 그래서 이동 시간 동안 시작
    위치에서 목표까지 일정한 속도로 채워 준다.

    실제 PLC/AMR 이 붙으면 이 파일만 갈아끼우면 된다 — `arrived` 와
    `position` 만 같은 뜻으로 내주면 시퀀서도 화면도 그대로 돈다.
    """

    arrived = pyqtSignal()
    activity = pyqtSignal(str)
    #: 이동 중 위치가 바뀔 때마다 나간다. 단위는 move_to 에 넘긴 것과 같다.
    position_changed = pyqtSignal(float)

    #: 위치를 만들어 내는 주기 [ms]. 표시용이라 촘촘할 필요가 없다.
    TICK_MS = 100

    def __init__(self, name: str, travel_ms: int = 900,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._travel_ms = max(1, travel_ms)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(travel_ms)
        self._timer.timeout.connect(self._on_arrived)
        # 가는 동안 위치를 채우는 시계. 도착하면 멈춘다.
        self._ticker = QTimer(self)
        self._ticker.setInterval(self.TICK_MS)
        self._ticker.timeout.connect(self._on_tick)
        self._pending = ""
        self._from = 0.0
        self._target = 0.0
        self._position = 0.0
        self._elapsed_ms = 0

    @property
    def position(self) -> float:
        """지금 위치. 이동 중이면 시작과 목표 사이의 값이다."""
        return self._position

    @property
    def target(self) -> float:
        """마지막으로 받은 목표 위치."""
        return self._target

    @property
    def moving(self) -> bool:
        return self._timer.isActive()

    def move_to(self, target: float | int, unit: str = "",
                label: str = "") -> None:
        """이동을 시작한다. 이미 이동 중이면 지금 위치에서 새 목표로 간다."""
        self._from = self._position
        self._target = float(target)
        self._elapsed_ms = 0
        self._pending = label or f"{target:g}{unit}"
        self.activity.emit(f"[더미] {self._name} → {self._pending} 이동 중")
        self._timer.start()
        self._ticker.start()

    def cancel(self) -> None:
        """진행 중인 이동을 취소한다. 도착 신호를 내지 않는다."""
        self._timer.stop()
        self._ticker.stop()

    def reset(self, position: float = 0.0) -> None:
        """위치를 되돌린다. 새 작업을 시작할 때 원점을 다시 잡는 용도."""
        self.cancel()
        self._from = self._target = self._position = float(position)
        self.position_changed.emit(self._position)

    def _on_tick(self) -> None:
        self._elapsed_ms += self.TICK_MS
        ratio = min(1.0, self._elapsed_ms / self._travel_ms)
        self._position = self._from + (self._target - self._from) * ratio
        self.position_changed.emit(self._position)

    def _on_arrived(self) -> None:
        self._ticker.stop()
        self._position = self._target
        self.position_changed.emit(self._position)
        self.activity.emit(f"[더미] {self._name} {self._pending} 도착")
        self.arrived.emit()


class SwitchableMotion(QObject):
    """더미와 실제 장비를 바꿔 끼우는 자리.

    시퀀서·마킹 순회·ERUT 응답은 이 객체 하나만 본다. 연결 설정의
    '차량 제어'로 뒤의 장비(backend)를 고르고, 지금 고른 쪽의 시그널만
    바깥으로 넘긴다. 움직이는 중에는 바꾸지 않는다.
    """

    arrived = pyqtSignal()
    activity = pyqtSignal(str)
    position_changed = pyqtSignal(float)
    #: 실제 장비가 도착을 못 낸 이유(오류·거절·시간 초과). 더미는 내지 않는다.
    problem = pyqtSignal(str)

    def __init__(self, backends: dict, active: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backends: dict = {}
        self._active = active
        for name, backend in backends.items():
            self.add_backend(name, backend)

    def add_backend(self, name: str, backend) -> None:
        """나중에 준비되는 장비(ROS 차량 등)를 붙인다. 고르기 전에는 조용하다."""
        self._backends[name] = backend
        backend.arrived.connect(lambda n=name: self._forward(n, self.arrived))
        backend.activity.connect(lambda text, n=name: self._forward(n, self.activity, text))
        backend.position_changed.connect(
            lambda value, n=name: self._forward(n, self.position_changed, value))
        if hasattr(backend, "problem"):
            backend.problem.connect(lambda text, n=name: self._forward(n, self.problem, text))

    def _forward(self, name: str, signal, *args) -> None:
        if name == self._active:
            signal.emit(*args)

    @property
    def active(self) -> str:
        return self._active

    @property
    def backend(self):
        return self._backends[self._active]

    def select(self, name: str) -> bool:
        """뒤의 장비를 바꾼다. 없는 이름이거나 움직이는 중이면 False."""
        if name not in self._backends:
            return False
        if name == self._active:
            return True
        if self.backend.moving:
            return False
        self._active = name
        self.position_changed.emit(self.backend.position)
        return True

    # ---- DummyMotionAdapter 와 같은 모양 -------------------------------------
    @property
    def position(self) -> float:
        return self.backend.position

    @property
    def target(self) -> float:
        return self.backend.target

    @property
    def moving(self) -> bool:
        return self.backend.moving

    def move_to(self, target, unit: str = "", label: str = "") -> None:
        self.backend.move_to(target, unit, label=label)

    def cancel(self) -> None:
        self.backend.cancel()

    def reset(self, position: float = 0.0) -> None:
        self.backend.reset(position)
