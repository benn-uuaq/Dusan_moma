from __future__ import annotations

import os
from typing import Any


class PostgreSQLSettingsRepository:
    """Persist operator settings in PostgreSQL using JSONB values."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.getenv("SMR_DATABASE_URL", "")

    def _connect(self):
        if not self._dsn:
            raise RuntimeError("SMR_DATABASE_URL 환경변수가 설정되지 않았습니다.")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 드라이버 psycopg가 설치되지 않았습니다.") from exc
        return psycopg.connect(self._dsn)

    def ensure_schema(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('smr_operator_ui_schema'))")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_settings (
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (scope, key)
                )
                """
            )

    def load(self, scope: str) -> dict[str, Any]:
        self.ensure_schema()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT key, value FROM operator_settings WHERE scope = %s ORDER BY key",
                (scope,),
            )
            return {key: value for key, value in cursor.fetchall()}

    def save(self, scope: str, values: dict[str, Any]) -> None:
        self.ensure_schema()
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO operator_settings (scope, key, value, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (scope, key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                """,
                [(scope, key, Jsonb(value)) for key, value in values.items()],
            )
