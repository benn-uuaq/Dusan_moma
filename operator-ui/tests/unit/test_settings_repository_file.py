"""로컬 JSON 설정 저장소 회귀 테스트.

PostgreSQL(SMR_DATABASE_URL)이 없는 환경에서는 예전에 저장이 통째로 실패해서
UI를 껐다 켜면 로봇 IP가 매번 기본값으로 돌아갔다.
"""

import json

from smr_operator_ui.services.settings_repository_file import JsonFileSettingsRepository


def test_saved_values_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "settings.json"
    JsonFileSettingsRepository(path).save("connection", {"협동로봇 IP": "192.168.1.101"})

    # 새 인스턴스 = 프로그램을 다시 켠 상황.
    assert JsonFileSettingsRepository(path).load("connection") == {
        "협동로봇 IP": "192.168.1.101"
    }


def test_scopes_do_not_overwrite_each_other(tmp_path) -> None:
    path = tmp_path / "settings.json"
    repo = JsonFileSettingsRepository(path)
    repo.save("connection", {"협동로봇 IP": "10.0.0.1"})
    repo.save("cobot", {"작업 속도": 150})

    assert repo.load("connection") == {"협동로봇 IP": "10.0.0.1"}
    assert repo.load("cobot") == {"작업 속도": 150}


def test_missing_file_reads_as_empty(tmp_path) -> None:
    """설정 파일이 아직 없으면 기본값으로 시작해야 한다(오류가 아니다)."""
    assert JsonFileSettingsRepository(tmp_path / "none.json").load("connection") == {}


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path) -> None:
    """저장 도중 죽어도 설정이 통째로 날아가지 않게 원자적으로 바꿔친다."""
    path = tmp_path / "settings.json"
    repo = JsonFileSettingsRepository(path)
    repo.save("connection", {"협동로봇 IP": "10.0.0.1"})
    repo.save("connection", {"협동로봇 IP": "10.0.0.2"})

    assert json.loads(path.read_text(encoding="utf-8"))["connection"] == {
        "협동로봇 IP": "10.0.0.2"
    }
    assert list(tmp_path.iterdir()) == [path]
