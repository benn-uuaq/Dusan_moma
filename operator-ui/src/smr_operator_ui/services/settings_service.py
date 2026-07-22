from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from .settings_repository import PostgreSQLSettingsRepository


class _WorkerSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)


class _DatabaseWorker(QRunnable):
    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.operation())
        except Exception as exc:  # Converted to a user-facing service error.
            self.signals.failed.emit(str(exc))


class SettingsService(QObject):
    loaded = pyqtSignal(str, dict)
    saved = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repository = PostgreSQLSettingsRepository()
        self._pool = QThreadPool.globalInstance()
        self._workers: set[_DatabaseWorker] = set()

    def load(self, scope: str) -> None:
        self._run(
            scope,
            lambda: self._repository.load(scope),
            lambda result: self.loaded.emit(scope, result),
        )

    def save(self, scope: str, values: dict[str, Any]) -> None:
        self._run(
            scope,
            lambda: self._repository.save(scope, values),
            lambda _result: self.saved.emit(scope),
        )

    def _run(self, scope: str, operation: Callable[[], Any], success: Callable[[Any], None]) -> None:
        worker = _DatabaseWorker(operation)
        self._workers.add(worker)
        worker.signals.succeeded.connect(success)
        worker.signals.succeeded.connect(lambda _result, item=worker: self._workers.discard(item))
        worker.signals.failed.connect(lambda message: self.failed.emit(scope, message))
        worker.signals.failed.connect(lambda _message, item=worker: self._workers.discard(item))
        self._pool.start(worker)
