"""운영자가 변경하는 UI 설정값을 PostgreSQL에 저장한다."""

from __future__ import annotations

import os
from typing import Any


class PostgreSQLSettingsRepository:
    """운영 설정을 JSONB 값으로 PostgreSQL에 저장한다."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.getenv("SMR_DATABASE_URL", "")

    def _connect(self):
        """환경변수로 받은 DSN을 사용해 새 DB 연결을 연다."""
        if not self._dsn:
            raise RuntimeError("SMR_DATABASE_URL 환경변수가 설정되지 않았습니다.")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 드라이버 psycopg가 설치되지 않았습니다.") from exc
        return psycopg.connect(self._dsn)

    def ensure_schema(self) -> None:
        """여러 작업자가 동시에 실행되어도 안전하게 설정 테이블을 만든다."""
        with self._connect() as connection, connection.cursor() as cursor:
            # 시작 시 여러 설정 범위를 병렬로 불러오므로 트랜잭션 범위의
            # advisory lock을 사용해 테이블 생성 구간만 직렬화한다.
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
        """하나의 설정 범위에 속한 JSONB 값을 변환하여 반환한다."""
        self.ensure_schema()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT key, value FROM operator_settings WHERE scope = %s ORDER BY key",
                (scope,),
            )
            return {key: value for key, value in cursor.fetchall()}

    def save(self, scope: str, values: dict[str, Any]) -> None:
        """한 트랜잭션에서 새 값을 추가하고 기존 키는 갱신한다."""
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
                # JSONB를 사용하면 설정 화면마다 별도 열을 만들지 않고도
                # 문자열, 불리언, 정수, 실수 자료형을 유지할 수 있다.
                [(scope, key, Jsonb(value)) for key, value in values.items()],
            )
