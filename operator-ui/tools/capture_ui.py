import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402

from smr_operator_ui.app import OperatorWindow, create_application  # noqa: E402
from smr_operator_ui.state import CyclePhase  # noqa: E402


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("ui-capture.png")
    screen_key = sys.argv[2] if len(sys.argv) > 2 else "main"
    app = create_application(["capture-ui"])
    window = OperatorWindow(start_ros=False)
    window.resize(1280, 720)
    if screen_key == "main":
        demo_cycle = replace(
            window.simulator.snapshot.cycle,
            current_segment=3,
            completed_segments=2,
            phase=CyclePhase.INSPECTING,
            running=True,
        )
        window.simulator.snapshot = replace(window.simulator.snapshot, cycle=demo_cycle)
        window.main_screen.update_snapshot(window.simulator.snapshot)
    window.navigate(screen_key)
    window.show()
    app.processEvents()
    image = QImage(window.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    window.render(painter)
    painter.end()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(output))
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
