"""Run slow work (scan, online lookup, copying) off the GUI thread."""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    """fn(worker, *args) runs in the thread pool; it may call worker.report() and check worker.cancelled()."""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.setAutoDelete(False)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def report(self, i: int, n: int, text: str = "") -> None:
        self.signals.progress.emit(i, n, text)

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self, *self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001 - shown to the user
            log.exception("background task failed")
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.signals.finished.emit(result)
