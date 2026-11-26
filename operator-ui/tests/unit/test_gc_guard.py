"""순환 GC 는 GUI 스레드 타이머에서만 돈다 (2026-11-24 — 시험이 드물게 세그폴트로 죽던 원인).

앱 전체 파이썬 이벤트 필터(WheelGuard) 안에서 자동 GC 가 돌면, 순환 참조로만 남은 창을 그
자리에서 지워 Qt 가 해제된 위젯에 이벤트를 넘겼다(QApplicationPrivate::notify_helper).
"""

import gc
import subprocess
import sys
import textwrap
from pathlib import Path

from smr_operator_ui.gc_guard import GarbageCollector, install_gc_guard

SRC = Path(__file__).resolve().parents[2] / "src"


def test_guard_turns_automatic_gc_off_and_is_installed_once(qapp) -> None:
    guard = install_gc_guard(qapp)
    assert isinstance(guard, GarbageCollector)
    assert install_gc_guard(qapp) is guard
    assert not gc.isenabled()


def test_window_installs_the_guard(qtbot, qapp) -> None:
    from smr_operator_ui.app import OperatorWindow

    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    assert isinstance(getattr(qapp, "_smr_gc_guard", None), GarbageCollector)
    assert not gc.isenabled()
    window.close()


def test_allocations_do_not_collect_until_the_timer_checks(qapp) -> None:
    guard = install_gc_guard(qapp)
    runs: list[int] = []

    def note(phase, info):
        if phase == "start":
            runs.append(info["generation"])

    gc.callbacks.append(note)
    try:
        cycles = []
        for _ in range(5000):          # 자동 GC 라면 0세대 문턱(700)을 몇 번이고 넘는다
            node = []
            node.append(node)
            cycles.append(node)
        del cycles, node
        assert runs == [], "자동 GC 가 돌았다 — 이벤트 전달 도중에도 돌 수 있다"
        assert guard.check() >= 5000   # 타이머(안전한 지점)에서 거둔다
    finally:
        gc.callbacks.remove(note)


def test_garbage_window_is_not_freed_while_its_event_is_delivered() -> None:
    """원래 크래시를 그대로 만든다 — 필터 안에서 할당이 쌓여도 창이 지워지지 않아야 한다."""
    script = textwrap.dedent(f"""
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, {str(SRC)!r})
        from PyQt6.QtCore import QCoreApplication, QEvent, QObject
        from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QWidget
        from smr_operator_ui.gc_guard import install_gc_guard
        from smr_operator_ui.keypad import install_wheel_guard

        app = QApplication([])

        class Allocate(QObject):
            armed = False
            def eventFilter(self, watched, event):
                if Allocate.armed and event.type() == QEvent.Type.User:
                    Allocate.armed = False
                    junk = []
                    for _ in range(100000):
                        junk.append([None]); junk[-1][0] = junk[-1]
                return False

        allocate = Allocate()
        app.installEventFilter(allocate)
        install_wheel_guard(app)
        install_gc_guard(app)

        class Window(QMainWindow):
            def __init__(self):
                super().__init__()
                body = QWidget(self)
                self.setCentralWidget(body)
                self.labels = [QLabel(str(i), body) for i in range(20)]
                self.callback = lambda: self.isVisible()      # 창 ↔ 람다 순환

        for _ in range(3):
            window = Window()
            window.show()
            app.processEvents()
            window.close()
            QCoreApplication.postEvent(window.labels[5], QEvent(QEvent.Type.User))
            del window
            Allocate.armed = True
            app.processEvents()
        print("ok")
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", (done.returncode, done.stderr[-2000:])
