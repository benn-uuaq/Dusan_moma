"""설정 저장소(PostgreSQL 또는 로컬 JSON 파일)를 비동기로 호출하는 Qt 서비스."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from .settings_repository import PostgreSQLSettingsRepository
from .settings_repository_file import JsonFileSettingsRepository


class _WorkerSignals(QObject):
    """DB 작업자 하나가 소유하는 스레드 간 결과 전달 채널."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)


class _DatabaseWorker(QRunnable):
    """블로킹 저장소 작업 하나를 GUI 스레드 밖에서 실행한다."""

    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = _WorkerSignals()

    def run(self) -> None:
        """작업을 실행하고 예외를 실패 시그널로 변환한다.

        프로그램을 닫는 중이면 결과를 받을 쪽(_WorkerSignals)이 이미 지워져
        있을 수 있다. 그때 emit 하면 RuntimeError 가 나고, 스레드에서 터지는
        예외라 프로세스가 죽기도 한다 — 조용히 접는다.
        """
        try:
            result = self.operation()
        except Exception as exc:  # 사용자에게 표시할 서비스 오류로 변환한다.
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass
            return
        try:
            self.signals.succeeded.emit(result)
        except RuntimeError:
            pass


class SettingsService(QObject):
    """Qt 시그널을 통해 비동기 설정 조회와 저장 기능을 제공한다."""

    loaded = pyqtSignal(str, dict)
    saved = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None, repository=None) -> None:
        super().__init__(parent)
        # DB가 준비된 현장에서는 여러 대가 설정을 공유해야 하니 PostgreSQL을
        # 쓰고, DSN이 없으면 로컬 JSON 파일에 저장한다. 예전에는 DSN이 없으면
        # 저장이 통째로 실패해서 **껐다 켜면 매번 기본값**으로 돌아갔다.
        if repository is not None:
            self._repository = repository
        elif os.getenv("SMR_DATABASE_URL", ""):
            self._repository = PostgreSQLSettingsRepository()
        else:
            self._repository = JsonFileSettingsRepository()
        self._pool = QThreadPool.globalInstance()
        self._workers: set[_DatabaseWorker] = set()
        # 범위별로 마지막에 불러오거나 저장한 값. 운전 모드 슬롯이 "지금
        # 설정"을 한데 묶을 때 쓴다 — 저장소를 다시 읽지 않아도 된다.
        self._known: dict[str, dict[str, Any]] = {}

    def known(self, scope: str) -> dict[str, Any]:
        """이 범위의 마지막 값(불러온 값 또는 저장한 값). 없으면 빈 사전."""
        return dict(self._known.get(scope, {}))

    def load(self, scope: str) -> None:
        """하나의 논리적 설정 범위에 속한 값을 모두 불러온다."""
        self._run(
            scope,
            lambda: self._repository.load(scope),
            lambda result: self._remember_and_emit(scope, result),
        )

    def _remember_and_emit(self, scope: str, result: dict[str, Any]) -> None:
        self._known[scope] = dict(result or {})
        self.loaded.emit(scope, result)

    def save(self, scope: str, values: dict[str, Any]) -> None:
        """이벤트 루프를 막지 않고 하나의 설정 범위를 저장한다."""
        self._known[scope] = dict(values)
        self._run(
            scope,
            lambda: self._repository.save(scope, values),
            lambda _result: self.saved.emit(scope),
        )

    def _run(self, scope: str, operation: Callable[[], Any], success: Callable[[Any], None]) -> None:
        """작업을 제출하고 완료 시그널이 올 때까지 QRunnable을 유지한다."""
        worker = _DatabaseWorker(operation)
        # QThreadPool이 QRunnable을 실행하는 동안 Python이 작업자를 먼저
        # 정리하지 않도록 강한 참조를 유지한다.
        self._workers.add(worker)
        worker.signals.succeeded.connect(success)
        worker.signals.succeeded.connect(lambda _result, item=worker: self._workers.discard(item))
        worker.signals.failed.connect(lambda message: self.failed.emit(scope, message))
        worker.signals.failed.connect(lambda _message, item=worker: self._workers.discard(item))
        self._pool.start(worker)
