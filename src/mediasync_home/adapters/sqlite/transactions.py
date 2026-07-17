from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar

from mediasync_home.application.command_receipts import CommandEffectTransaction


_T = TypeVar("_T")


class SqliteImmediateTransactionRunner(CommandEffectTransaction):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def run(self, work: Callable[[], _T]) -> _T:
        if self._connection.in_transaction:
            return work()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            result = work()
            self._connection.execute("COMMIT")
            return result
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
