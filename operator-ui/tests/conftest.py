"""테스트 공통 설정.

가장 중요한 것은 **설정 저장소 격리**다. OperatorWindow 는 살아 있는 동안
설정을 저장하는데, 그대로 두면 테스트가 운영자의 실제 설정 파일
(`~/.config/smr-operator-ui/settings.json`)을 덮어써 현장 값이 날아간다.

설정 파일은 **테스트마다 따로** 준다(2026-11-24). 세션에 하나만 쓰면 앞 테스트의 창이
저장한 값(로봇 IP 등)을 다음 테스트 창이 비동기로 불러와, 테스트가 넣은 값을 덮어써
순서에 따라 실패했다(test_robot_auto_connect). 폴더는 세션 동안 지우지 않으므로 뒤늦게
끝난 저장 작업자도 자기 테스트의 파일에만 쓴다.

시험이 드물게 세그폴트로 죽던 원인은 경로가 아니라 **이벤트 전달 도중의 순환 GC** 였다 —
`smr_operator_ui.gc_guard` 참고. 여기서는 각 테스트가 끝날 때 남은 창을 안전한 지점에서
지운다(`pytest_runtest_teardown`).
"""

import gc
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_settings_file(tmp_path_factory):
    """설정 · 기록 저장을 세션 전용 임시 위치로 돌린다(설정 파일은 테스트마다 다시 잡는다)."""
    path = tmp_path_factory.mktemp("operator_settings") / "settings.json"
    previous = {
        "SMR_SETTINGS_FILE": os.environ.get("SMR_SETTINGS_FILE"),
        "SMR_DATABASE_URL": os.environ.get("SMR_DATABASE_URL"),
        "SMR_DATA_DIR": os.environ.get("SMR_DATA_DIR"),
        "SMR_ROBOT_CONFIG_DIR": os.environ.get("SMR_ROBOT_CONFIG_DIR"),
    }
    os.environ["SMR_SETTINGS_FILE"] = str(path)
    # 운영 기록도 마찬가지 — 안 막으면 테스트가 실제 데이터 저장 위치
    # (D:/SMR/Data 등)에 가짜 작업기록·알람을 쌓는다.
    os.environ["SMR_DATA_DIR"] = str(tmp_path_factory.mktemp("operator_data"))
    # 기준 위치 파일(robot_task/config/reference_poses.json)도 — 저장소에 든
    # 파일이라, 안 막으면 읽을 때 옛 형식을 옮겨 저장하며 고쳐 쓴다.
    os.environ["SMR_ROBOT_CONFIG_DIR"] = str(tmp_path_factory.mktemp("robot_config"))
    # DB 가 잡혀 있으면 그쪽으로 가 버리므로 파일 저장소를 쓰게 비워 둔다.
    os.environ.pop("SMR_DATABASE_URL", None)
    yield path
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def isolated_settings_per_test(isolated_settings_file, tmp_path_factory, monkeypatch):
    """이 테스트만의 설정 파일 — 앞 테스트가 저장한 값이 흘러들지 않는다."""
    path = tmp_path_factory.mktemp("settings") / "settings.json"
    monkeypatch.setenv("SMR_SETTINGS_FILE", str(path))
    return path


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    """테스트가 끝나면 남은 창을 이벤트 전달 밖(안전한 지점)에서 지운다.

    pytest-qt 는 창에 deleteLater 만 걸고 processEvents 를 부르는데, 이벤트 루프 밖에서
    건 deleteLater 는 processEvents 로는 처리되지 않는다 — 닫힌 창이 다음에 누가 이벤트
    루프를 돌릴 때까지 살아 남고, 파이썬 쪽은 순환 참조로만 남는다. 그런 창을 GC 가 이벤트
    전달 도중에 거두면 Qt 가 지워진 위젯에 이벤트를 넘겨 죽었다. pytest-qt 의 정리가 모두
    끝난 뒤(이 래퍼가 바깥쪽이다) 미뤄 둔 삭제를 처리하고 순환 참조를 거둔다.
    """
    yield
    from PyQt6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
