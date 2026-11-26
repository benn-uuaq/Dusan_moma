"""순환 GC 를 GUI 스레드의 안전한 지점에서만 돌린다 (2026-11-24).

파이썬의 자동 순환 GC 는 "객체를 일정 수 이상 만들었을 때" 그 자리에서 돈다. 이 앱은
앱 전체 파이썬 이벤트 필터(`keypad.WheelGuard`)를 걸어 두므로, **Qt 가 이벤트 하나를
전달하는 도중**에도 파이썬 코드가 돌고, 그 안에서 GC 가 돌 수 있다.

GC 가 파이썬이 소유한 Qt 객체(부모 없는 창 · QObject)를 거두면, 같은 스레드에서는 PyQt 가
C++ 객체를 **그 자리에서** 지운다. 그것이 지금 이벤트를 받는 위젯(또는 그 조상)이면 필터가
돌아온 뒤 Qt 가 이미 해제된 객체의 `event()` 를 불러 프로세스가 죽는다
(`QApplicationPrivate::notify_helper` 에서 세그폴트 — 파이썬 스택에는 이벤트를 돌린
함수만 남는다). 시험에서 드물게 죽던 원인이 이것이다.

그래서 자동 GC 를 끄고, GUI 스레드의 타이머에서만(이벤트 전달 중이 아닌 곳) 같은
세대 기준으로 거둔다. 다른 스레드(ROS 실행기 · 설정 저장 · 확인 스레드)에서도 GC 가
돌지 않게 된다. PyQt · pyqtgraph · calibre 가 쓰는 방식과 같다.
"""

from __future__ import annotations

import gc

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication


class GarbageCollector(QObject):
    """자동 GC 를 끄고 `INTERVAL_MS` 마다 GUI 스레드에서 세대별로 거둔다."""

    INTERVAL_MS = 1000

    def __init__(self, parent: QObject | None = None, interval_ms: int = INTERVAL_MS) -> None:
        super().__init__(parent)
        # 자동 GC 와 같은 문턱(기본 700 · 10 · 10)으로 판단한다.
        self.threshold = gc.get_threshold()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)
        self._timer.start(int(interval_ms))

    def check(self) -> int:
        """문턱을 넘은 가장 높은 세대까지 한 번에 거둔다. 거둔 객체 수를 돌려준다.

        1 초마다 부르면 0세대는 거의 매번, 1세대는 약 11 초, 전체(2세대)는 약 2 분마다 돈다.
        """
        count0, count1, count2 = gc.get_count()
        threshold0, threshold1, threshold2 = self.threshold
        if count0 <= threshold0:
            return 0
        generation = 0
        if count1 > threshold1:
            generation = 1
            if count2 > threshold2:
                generation = 2
        return gc.collect(generation)


def install_gc_guard(app: QApplication | None = None) -> GarbageCollector | None:
    """앱에 GarbageCollector 를 한 번만 건다. 여러 번 불러도 된다."""
    app = app or QApplication.instance()
    if app is None:
        return None
    guard = getattr(app, "_smr_gc_guard", None)
    if guard is None:
        guard = GarbageCollector(app)
        app._smr_gc_guard = guard
    return guard
