from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar

from mediasync_home.adapters.sqlite.connection_policy import (
    SqliteFailureKind,
    classify_sqlite_exception,
)
from mediasync_home.application.command_receipts import (
    CommandEffectStorageFailure,
    CommandEffectTransaction,
)


_T = TypeVar("_T")


class SqliteImmediateTransactionRunner(CommandEffectTransaction):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        failure_observer: Callable[[SqliteFailureKind], None] | None = None,
    ) -> None:
        self._connection = connection
        self._failure_observer = failure_observer

    def run(self, work: Callable[[], _T]) -> _T:
        if self._connection.in_transaction:
            return work()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            result = work()
            self._connection.execute("COMMIT")
            return result
        except Exception as exc:
            _rollback(self._connection)
            failure_kind = classify_sqlite_exception(exc)
            if failure_kind is SqliteFailureKind.UNKNOWN:
                raise
            if self._failure_observer is not None:
                self._failure_observer(failure_kind)
            raise CommandEffectStorageFailure(
                _failure_error_code(failure_kind),
                retryable=failure_kind
                in {SqliteFailureKind.BUSY, SqliteFailureKind.BUSY_SNAPSHOT},
            ) from exc


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _failure_error_code(failure_kind: SqliteFailureKind) -> str:
    if failure_kind is SqliteFailureKind.NOT_A_DATABASE:
        return "SQLITE_NOTADB"
    return f"SQLITE_{failure_kind.value}"
