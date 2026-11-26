"""설정 저장이 한꺼번에 몰려도 다 남는다 (2026-11-25).

JSON 저장소는 「파일 읽기 → 범위 고치기 → 다시 쓰기」라, 여러 스레드가 동시에 하면 늦게 쓴 쪽이 먼저 쓴 범위를
지웠다 — 운전 모드를 불러오면 여러 범위를 한꺼번에 저장한다. 저장은 한 줄로 들어온 순서대로 한다.
"""

import json

from smr_operator_ui.services.settings_repository_file import JsonFileSettingsRepository
from smr_operator_ui.services.settings_service import SettingsService


def test_burst_of_saves_keeps_every_scope_and_the_last_value(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    service = SettingsService(repository=JsonFileSettingsRepository(path))
    saved: list[str] = []
    service.saved.connect(saved.append)
    scopes = [f"scope{i}" for i in range(20)]
    for i, scope in enumerate(scopes):
        service.save(scope, {"value": i})
    for value in range(5):                              # 같은 범위를 거듭 — 나중 값이 남아야 한다
        service.save("mode_current", {"slot": value})
    qtbot.waitUntil(lambda: len(saved) == len(scopes) + 5, timeout=10000)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert all(data[scope] == {"value": i} for i, scope in enumerate(scopes))
    assert data["mode_current"] == {"slot": 4}
