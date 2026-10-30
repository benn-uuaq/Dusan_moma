"""Windows 단독판 실행기 — ERUT · 차량(AGV) · PLC 없이 로봇 + TPAC.

    python -m smr_operator_ui.standalone        (소스에서)
    SMR-RCS-Standalone.exe                      (PyInstaller 로 묶은 것)

하는 일
  1. 설정 · 기준 위치 · 실행 기록을 **실행 파일 옆의 「SMR-RCS-설정」 폴더**에 둔다.
     exe 와 그 폴더를 함께 옮기면 설정도 따라간다(전시회 PC 를 바꿔도 USB 로
     들고 다닌다). 거기에 쓸 수 없거나 소스에서 돌리면 %APPDATA%\\SMR-RCS.
  2. ROS 대역(liteross)을 깔고, 로봇 제어 노드 패키지(elite_robot_controller)를
     찾을 수 있게 한다.
  3. 단독판 표시를 켠 뒤 RCS 를 띄운다 — ERUT 는 열지 않는다.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

#: 실행 파일과 같은 자리에 만드는 설정 폴더 이름.
SETTINGS_DIR_NAME = "SMR-RCS-설정"


def _appdata_root() -> Path:
    """소스에서 돌릴 때, 또는 exe 옆에 쓸 수 없을 때 쓰는 사용자 폴더(예전 판의 자리)."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "SMR-RCS"


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _user_root() -> Path:
    """설정 · 기준 위치 · 실행 기록을 둘 폴더.

    묶은 실행 파일이면 exe 와 같은 자리의 「SMR-RCS-설정」. Program Files 처럼 그
    자리에 쓸 수 없으면 %APPDATA%\\SMR-RCS 로 물러난다 — 설정을 못 남기는 것보다 낫다.
    """
    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / SETTINGS_DIR_NAME
        if _writable(portable):
            return portable
    return _appdata_root()


def _adopt_old_settings(root: Path) -> bool:
    """예전 판이 %APPDATA%\\SMR-RCS 에 저장한 설정을 처음 한 번 가져온다.

    새 폴더에 settings.json 이 아직 없을 때만 — 이미 쓰고 있는 설정을 덮지 않는다.
    로봇 IP · 태스크 선택 · 저장한 위치를 다시 넣지 않아도 되게 한다.
    """
    old = _appdata_root()
    if root == old or (root / "settings.json").exists():
        return False
    try:
        if not (old / "settings.json").is_file():
            return False
        shutil.copy2(old / "settings.json", root / "settings.json")
        if (old / "robot_config").is_dir() and not (root / "robot_config").exists():
            shutil.copytree(old / "robot_config", root / "robot_config")
    except OSError:
        return False        # 못 가져오면 기본값으로 시작한다 — 실행은 막지 않는다
    return True


def _bundle_root() -> Path | None:
    """PyInstaller 로 묶었으면 풀린 폴더."""
    root = getattr(sys, "_MEIPASS", None)
    return Path(root) if root else None


def _ensure_robot_package() -> None:
    """로봇 제어 노드 패키지를 import 할 수 있게 한다.

    묶은 실행 파일에는 함께 들어 있다. 소스에서 돌릴 때는 워크스페이스의
    src/elite_robot_controller, 또는 배포 폴더 source 의 robot 을 경로에 넣는다
    (standalone_build/build_standalone.ps1 이 그렇게 모은다).
    """
    try:
        import elite_robot_controller  # noqa: F401
        return
    except ImportError:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        for candidate in (parent / "src" / "elite_robot_controller", parent / "robot"):
            if (candidate / "elite_robot_controller").is_dir():
                sys.path.insert(0, str(candidate))
                return


def _setup_logging(root: Path) -> Path:
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "rcs_standalone.log"
    logging.basicConfig(
        filename=str(path), encoding="utf-8", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return path


def prepare() -> Path:
    """앱을 import 하기 전에 할 일. 사용자 폴더를 돌려준다."""
    root = _user_root()
    root.mkdir(parents=True, exist_ok=True)
    adopted = _adopt_old_settings(root)
    os.environ.setdefault("SMR_SETTINGS_FILE", str(root / "settings.json"))
    os.environ.setdefault("SMR_ROBOT_CONFIG_DIR", str(root / "robot_config"))
    # DB 설정이 남아 있어도 단독판은 파일에만 저장한다.
    os.environ.pop("SMR_DATABASE_URL", None)
    _setup_logging(root)
    if adopted:
        logging.getLogger("standalone").info("예전 설정을 가져왔습니다 — %s", _appdata_root())

    from smr_operator_ui.standalone import liteross
    liteross.install()
    _ensure_robot_package()
    from smr_operator_ui import edition
    edition.enable_standalone()
    return root


def main() -> int:
    root = prepare()
    logging.getLogger("standalone").info("단독판 시작 — 사용자 폴더 %s", root)
    if "--offscreen" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from smr_operator_ui.app import OperatorWindow, create_application

    app = create_application()
    window = OperatorWindow(start_ros=True, start_erut=False)
    window.show()
    if "--smoke" in sys.argv:
        # 빌드 확인용: 몇 초 띄웠다가 스스로 닫는다.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(int(os.environ.get("SMR_SMOKE_MS", "6000")), window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
