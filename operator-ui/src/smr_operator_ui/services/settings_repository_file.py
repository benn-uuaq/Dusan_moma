"""운영 설정을 로컬 JSON 파일에 저장한다.

PostgreSQL이 준비되지 않은 현장(그리고 개발 PC)에서도 **다시 켰을 때 설정이
남아 있어야** 한다. 예전에는 `SMR_DATABASE_URL`이 없으면 저장이 통째로
실패해서, 로봇 IP를 고쳐도 UI를 껐다 켜면 매번 기본값으로 되돌아갔다.

DB가 붙어 있으면 그쪽이 우선이고(여러 대가 설정을 공유해야 하므로),
이 파일 저장소는 DB가 없을 때만 쓰인다 — `SettingsService` 참고.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def default_settings_path() -> Path:
    """설정 파일 위치. 환경변수로 덮어쓸 수 있다."""
    override = os.getenv("SMR_SETTINGS_FILE", "")
    if override:
        return Path(override)
    base = os.getenv("XDG_CONFIG_HOME", "")
    root = Path(base) if base else Path.home() / ".config"
    return root / "smr-operator-ui" / "settings.json"


class JsonFileSettingsRepository:
    """설정 범위(scope)별로 묶어 하나의 JSON 파일에 담는다."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def _read_all(self) -> dict[str, dict[str, Any]]:
        try:
            with self._path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            # 파일이 깨졌다고 UI가 못 뜨면 안 된다. 기본값으로 시작하고
            # 다음 저장 때 정상 내용으로 덮어쓴다.
            raise RuntimeError(f"설정 파일을 읽을 수 없습니다: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def load(self, scope: str) -> dict[str, Any]:
        """하나의 설정 범위를 읽는다. 없으면 빈 값(=기본값 사용)이다."""
        values = self._read_all().get(scope, {})
        return dict(values) if isinstance(values, dict) else {}

    def save(self, scope: str, values: dict[str, Any]) -> None:
        """한 범위를 갱신해 파일 전체를 다시 쓴다.

        같은 파일을 쓰는 도중 프로세스가 죽어 설정이 통째로 날아가지 않도록
        임시 파일에 먼저 쓰고 원자적으로 바꿔치기한다.
        """
        data = self._read_all()
        data[scope] = dict(values)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self._path.parent,
            prefix=self._path.name, suffix=".tmp", delete=False,
        )
        try:
            with handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(handle.name, self._path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
