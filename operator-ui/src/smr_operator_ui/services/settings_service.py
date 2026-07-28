"""동기 PostgreSQL 저장소를 비동기로 호출하는 Qt 서비스."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from .settings_repository import PostgreSQLSettingsRepository


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
        """작업을 실행하고 예외를 실패 시그널로 변환한다."""
        try:
            self.signals.succeeded.emit(self.operation())
        except Exception as exc:  # 사용자에게 표시할 서비스 오류로 변환한다.
            self.signals.failed.emit(str(exc))


class SettingsService(QObject):
    """Qt 시그널을 통해 비동기 설정 조회와 저장 기능을 제공한다."""

    loaded = pyqtSignal(str, dict)
    saved = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repository = PostgreSQLSettingsRepository()
        self._pool = QThreadPool.globalInstance()
        self._workers: set[_DatabaseWorker] = set()

    def load(self, scope: str) -> None:
        """하나의 논리적 설정 범위에 속한 값을 모두 불러온다."""
        self._run(
            scope,
            lambda: self._repository.load(scope),
            lambda result: self.loaded.emit(scope, result),
        )

    def save(self, scope: str, values: dict[str, Any]) -> None:
        """이벤트 루프를 막지 않고 하나의 설정 범위를 저장한다."""
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
