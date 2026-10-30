"""단독판 실행기 — 설정 폴더는 실행 파일 옆(「SMR-RCS-설정」)에 둔다."""

import sys

from smr_operator_ui.standalone import launcher


def _frozen_exe(monkeypatch, folder):
    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(folder / "SMR-RCS-Standalone.exe"))


def test_frozen_exe_keeps_settings_next_to_itself(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _frozen_exe(monkeypatch, tmp_path / "TPAC지원")
    root = launcher._user_root()
    assert root == (tmp_path / "TPAC지원" / launcher.SETTINGS_DIR_NAME).resolve()
    assert root.is_dir()


def test_source_run_and_unwritable_folder_use_appdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert launcher._user_root() == tmp_path / "appdata" / "SMR-RCS"

    _frozen_exe(monkeypatch, tmp_path / "Program Files")
    monkeypatch.setattr(launcher, "_writable", lambda folder: False)
    assert launcher._user_root() == tmp_path / "appdata" / "SMR-RCS"


def test_old_appdata_settings_are_adopted_once(monkeypatch, tmp_path) -> None:
    old = tmp_path / "appdata" / "SMR-RCS"
    (old / "robot_config").mkdir(parents=True)
    (old / "settings.json").write_text('{"connection": {"협동로봇 IP": "192.168.1.101"}}',
                                       encoding="utf-8")
    (old / "robot_config" / "reference_poses.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "TPAC지원" / launcher.SETTINGS_DIR_NAME
    root.mkdir(parents=True)

    assert launcher._adopt_old_settings(root) is True
    assert "192.168.1.101" in (root / "settings.json").read_text(encoding="utf-8")
    assert (root / "robot_config" / "reference_poses.json").is_file()

    # 새 폴더에 설정이 생긴 뒤에는 예전 값으로 덮지 않는다.
    (root / "settings.json").write_text('{"connection": {"협동로봇 IP": "10.0.0.5"}}',
                                        encoding="utf-8")
    assert launcher._adopt_old_settings(root) is False
    assert "10.0.0.5" in (root / "settings.json").read_text(encoding="utf-8")


def test_robot_package_is_found_in_the_portable_source_layout(monkeypatch, tmp_path) -> None:
    """배포 폴더 source(smr_operator_ui 옆 robot\\elite_robot_controller)에서도 소스로 돈다."""
    import builtins

    source = tmp_path / "source"
    (source / "smr_operator_ui" / "standalone").mkdir(parents=True)
    (source / "robot" / "elite_robot_controller").mkdir(parents=True)
    fake_launcher = source / "smr_operator_ui" / "standalone" / "launcher.py"
    fake_launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "__file__", str(fake_launcher))
    real_import = builtins.__import__

    def no_robot(name, *args, **kwargs):
        if name == "elite_robot_controller":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_robot)
    monkeypatch.setattr(sys, "path", list(sys.path))
    launcher._ensure_robot_package()
    assert sys.path[0] == str(source / "robot")
