from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from threading import Lock, Thread
from time import monotonic
from typing import Generic, TypeVar

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

BackgroundClientFactory = Callable[[], object | None]
BackgroundOperation = Callable[[object], object]
BackgroundResultHandler = Callable[[object], None]
BackgroundErrorHandler = Callable[[Exception], None]
UiUpdateHandler = Callable[[object], None]

_ContextT = TypeVar("_ContextT")
_PageT = TypeVar("_PageT")


@dataclass(frozen=True)
class _QueuedQuery:
    key: str
    token: int
    operation: BackgroundOperation
    on_result: BackgroundResultHandler
    on_error: BackgroundErrorHandler | None


@dataclass(frozen=True)
class _PendingUiUpdate:
    value: object
    apply: UiUpdateHandler


@dataclass(frozen=True)
class _PrefetchedPage(Generic[_ContextT, _PageT]):
    context: _ContextT
    page: _PageT


class BoundedPagePrefetchCache(Generic[_ContextT, _PageT]):
    """Keeps at most one speculative page bound to its exact request context."""

    def __init__(self) -> None:
        self._entry: _PrefetchedPage[_ContextT, _PageT] | None = None

    @property
    def count(self) -> int:
        return 0 if self._entry is None else 1

    def store(self, *, context: _ContextT, page: _PageT) -> None:
        self._entry = _PrefetchedPage(context=context, page=page)

    def take(self, *, context: _ContextT) -> _PageT | None:
        entry = self._entry
        self._entry = None
        if entry is None or entry.context != context:
            return None
        return entry.page

    def clear(self) -> None:
        self._entry = None


class _WorkerSignals(QObject):
    finished = Signal(int, object, object)


class _WorkerClientState:
    def __init__(self, factory: BackgroundClientFactory) -> None:
        self._factory = factory
        self._client: object | None = None
        self._lock = Lock()

    def get(self) -> object:
        with self._lock:
            if self._client is None:
                self._client = self._factory()
            if self._client is None:
                raise RuntimeError("background query client is unavailable")
            return self._client

    def discard(self, client: object | None) -> None:
        with self._lock:
            if self._client is client:
                self._client = None


class BackgroundQueryController(QObject):
    """Runs reconstructible GUI reads on one bounded latest-wins worker."""

    def __init__(
        self,
        *,
        client_factory: BackgroundClientFactory,
        max_pending: int = 4,
        parent: QObject | None = None,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        super().__init__(parent)
        self._client_state = _WorkerClientState(client_factory)
        self._max_pending = max_pending
        self._next_token = 1
        self._active: _QueuedQuery | None = None
        self._pending: dict[str, _QueuedQuery] = {}
        self._pending_order: deque[str] = deque()
        self._latest_tokens: dict[str, int] = {}
        self._closed = False

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def submit(
        self,
        *,
        key: str,
        operation: BackgroundOperation,
        on_result: BackgroundResultHandler,
        on_error: BackgroundErrorHandler | None = None,
    ) -> bool:
        normalized_key = key.strip()
        if self._closed or not normalized_key:
            return False
        if (
            self._active is not None
            and normalized_key not in self._pending
            and len(self._pending) >= self._max_pending
        ):
            return False

        token = self._next_token
        self._next_token += 1
        query = _QueuedQuery(
            key=normalized_key,
            token=token,
            operation=operation,
            on_result=on_result,
            on_error=on_error,
        )
        self._latest_tokens[normalized_key] = token
        if self._active is None:
            if normalized_key in self._pending:
                del self._pending[normalized_key]
                self._pending_order.remove(normalized_key)
            self._start(query)
            return True
        if normalized_key not in self._pending:
            self._pending_order.append(normalized_key)
        self._pending[normalized_key] = query
        return True

    def cancel(self, key: str) -> None:
        normalized_key = key.strip()
        self._latest_tokens.pop(normalized_key, None)
        if normalized_key in self._pending:
            del self._pending[normalized_key]
            self._pending_order.remove(normalized_key)

    def cancel_all(self) -> None:
        self._latest_tokens.clear()
        self._pending.clear()
        self._pending_order.clear()

    def close(self) -> None:
        self._closed = True
        self.cancel_all()

    def _start(self, query: _QueuedQuery) -> None:
        self._active = query
        signals = _WorkerSignals()
        signals.finished.connect(
            self._on_worker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        client_state = self._client_state

        def execute() -> None:
            result: object = None
            error: Exception | None = None
            client: object | None = None
            try:
                client = client_state.get()
                result = query.operation(client)
            except Exception as exc:
                client_state.discard(client)
                error = exc
            signals.finished.emit(query.token, result, error)

        Thread(
            target=execute,
            name=f"mediasync-gui-query-{query.key}",
            daemon=True,
        ).start()

    @Slot(int, object, object)
    def _on_worker_finished(
        self,
        token: int,
        result: object,
        error: object,
    ) -> None:
        query = self._active
        self._active = None
        try:
            if (
                not self._closed
                and query is not None
                and query.token == token
                and self._latest_tokens.get(query.key) == token
            ):
                if isinstance(error, Exception):
                    if query.on_error is not None:
                        query.on_error(error)
                else:
                    query.on_result(result)
        finally:
            self._start_next_pending()

    def _start_next_pending(self) -> None:
        if self._closed or self._active is not None:
            return
        while self._pending_order:
            key = self._pending_order.popleft()
            query = self._pending.pop(key, None)
            if query is not None:
                self._start(query)
                return


class CommandSubmissionController(QObject):
    """Runs one non-reconstructible GUI command on a dedicated worker."""

    def __init__(
        self,
        *,
        client_factory: BackgroundClientFactory,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client_state = _WorkerClientState(client_factory)
        self._next_token = 1
        self._active: _QueuedQuery | None = None
        self._closed = False

    @property
    def active(self) -> bool:
        return self._active is not None

    def submit(
        self,
        *,
        name: str,
        operation: BackgroundOperation,
        on_result: BackgroundResultHandler,
        on_error: BackgroundErrorHandler | None = None,
    ) -> bool:
        normalized_name = name.strip()
        if self._closed or self._active is not None or not normalized_name:
            return False
        token = self._next_token
        self._next_token += 1
        command = _QueuedQuery(
            key=normalized_name,
            token=token,
            operation=operation,
            on_result=on_result,
            on_error=on_error,
        )
        self._active = command
        self._start(command)
        return True

    def close(self) -> None:
        self._closed = True

    def _start(self, command: _QueuedQuery) -> None:
        signals = _WorkerSignals()
        signals.finished.connect(
            self._on_worker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        client_state = self._client_state

        def execute() -> None:
            result: object = None
            error: Exception | None = None
            client: object | None = None
            try:
                client = client_state.get()
                result = command.operation(client)
            except Exception as exc:
                client_state.discard(client)
                error = exc
            signals.finished.emit(command.token, result, error)

        Thread(
            target=execute,
            name=f"mediasync-gui-command-{command.key}",
            daemon=True,
        ).start()

    @Slot(int, object, object)
    def _on_worker_finished(
        self,
        token: int,
        result: object,
        error: object,
    ) -> None:
        command = self._active
        self._active = None
        if self._closed or command is None or command.token != token:
            return
        if isinstance(error, Exception):
            if command.on_error is not None:
                command.on_error(error)
            return
        command.on_result(result)


class UiUpdateCoalescer(QObject):
    """Applies only the latest reconstructible update per bounded channel."""

    def __init__(
        self,
        *,
        interval_ms: int = 250,
        max_channels: int = 16,
        parent: QObject | None = None,
    ) -> None:
        if interval_ms < 1:
            raise ValueError("interval_ms must be positive")
        if max_channels < 1:
            raise ValueError("max_channels must be positive")
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._max_channels = max_channels
        self._pending: dict[str, _PendingUiUpdate] = {}
        self._last_flush: float | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def submit(
        self,
        *,
        channel: str,
        value: object,
        apply: UiUpdateHandler,
    ) -> bool:
        normalized_channel = channel.strip()
        if not normalized_channel:
            return False
        if (
            normalized_channel not in self._pending
            and len(self._pending) >= self._max_channels
        ):
            return False
        self._pending[normalized_channel] = _PendingUiUpdate(value=value, apply=apply)
        if not self._timer.isActive():
            self._schedule_next_flush()
        return True

    def cancel(self, channel: str) -> None:
        self._pending.pop(channel.strip(), None)
        if not self._pending:
            self._timer.stop()

    def cancel_all(self) -> None:
        self._pending.clear()
        self._timer.stop()

    def _schedule_next_flush(self) -> None:
        delay_ms = 0
        if self._last_flush is not None:
            elapsed_ms = (monotonic() - self._last_flush) * 1000
            delay_ms = max(0, ceil(self._interval_ms - elapsed_ms))
        self._timer.start(delay_ms)

    @Slot()
    def _flush(self) -> None:
        if not self._pending:
            return
        updates = tuple(self._pending.values())
        self._pending.clear()
        self._last_flush = monotonic()
        for update in updates:
            update.apply(update.value)
        if self._pending:
            self._schedule_next_flush()
