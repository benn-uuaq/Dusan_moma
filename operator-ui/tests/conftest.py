"""테스트 공통 설정.

가장 중요한 것은 **설정 저장소 격리**다. OperatorWindow 는 살아 있는 동안
설정을 저장하는데, 그대로 두면 테스트가 운영자의 실제 설정 파일
(`~/.config/smr-operator-ui/settings.json`)을 덮어써 현장 값이 날아간다.

경로는 테스트마다가 아니라 **세션에 하나**만 쓴다. 설정 저장은 백그라운드
스레드에서 도는데, 테스트가 끝날 때마다 임시 폴더를 지우면 뒤늦게 깨어난
작업자가 이미 없어진 경로를 건드려 프로세스가 죽는다.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_settings_file(tmp_path_factory):
    """설정 저장을 세션 전용 임시 파일로 돌린다."""
    path = tmp_path_factory.mktemp("operator_settings") / "settings.json"
    previous = {
        "SMR_SETTINGS_FILE": os.environ.get("SMR_SETTINGS_FILE"),
        "SMR_DATABASE_URL": os.environ.get("SMR_DATABASE_URL"),
        "SMR_DATA_DIR": os.environ.get("SMR_DATA_DIR"),
    }
    os.environ["SMR_SETTINGS_FILE"] = str(path)
    # 운영 기록도 마찬가지 — 안 막으면 테스트가 실제 데이터 저장 위치
    # (D:/SMR/Data 등)에 가짜 작업기록·알람을 쌓는다.
    os.environ["SMR_DATA_DIR"] = str(tmp_path_factory.mktemp("operator_data"))
    # DB 가 잡혀 있으면 그쪽으로 가 버리므로 파일 저장소를 쓰게 비워 둔다.
    os.environ.pop("SMR_DATABASE_URL", None)
    yield path
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
