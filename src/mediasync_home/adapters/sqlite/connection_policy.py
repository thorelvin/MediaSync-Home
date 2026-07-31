from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SqliteStore(str, Enum):
    CATALOG = "catalog"
    RECOVERY = "recovery"


class SqliteConnectionPurpose(str, Enum):
    CATALOG_BULK_WRITER = "catalog-bulk-writer"
    CATALOG_CRITICAL_WRITER = "catalog-critical-writer"
    CATALOG_READER = "catalog-reader"
    RECOVERY_WRITER = "recovery-writer"
    RECOVERY_READER = "recovery-reader"


class SqliteFailureKind(str, Enum):
    BUSY = "BUSY"
    BUSY_SNAPSHOT = "BUSY_SNAPSHOT"
    FULL = "FULL"
    CORRUPT = "CORRUPT"
    NOT_A_DATABASE = "NOT_A_DATABASE"
    READ_ONLY = "READ_ONLY"
    IO = "IO"
    UNKNOWN = "UNKNOWN"


class SqlitePolicyViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StateStoreLayout:
    root: Path
    catalog: Path
    recovery: Path


@dataclass(frozen=True, slots=True)
class SqlitePragma:
    name: str
    value: str | int

    def statement(self) -> str:
        return f"PRAGMA {self.name} = {self.value}"


@dataclass(frozen=True, slots=True)
class SqliteConnectionPolicy:
    store: SqliteStore
    purpose: SqliteConnectionPurpose
    database_path: Path
    writable: bool
    pragmas: tuple[SqlitePragma, ...]
    allow_attach: bool = False
    allow_shared_cache: bool = False
    allow_extension_loading: bool = False
    allow_cross_store_transaction: bool = False

    def pragma_map(self) -> dict[str, str | int]:
        return {pragma.name.lower(): pragma.value for pragma in self.pragmas}

    def pragma_statements(self) -> tuple[str, ...]:
        return tuple(pragma.statement() for pragma in self.pragmas)


_BOOLEAN_PRAGMAS = {
    "defer_foreign_keys",
    "foreign_keys",
    "ignore_check_constraints",
    "legacy_alter_table",
    "query_only",
    "recursive_triggers",
    "trusted_schema",
    "writable_schema",
}
_INTEGER_PRAGMAS = {
    "busy_timeout",
    "cache_size",
    "wal_autocheckpoint",
}
_SENSITIVE_PRAGMA_DEFAULTS: dict[str, str | int] = {
    "defer_foreign_keys": "OFF",
    "ignore_check_constraints": "OFF",
    "legacy_alter_table": "OFF",
    "recursive_triggers": "OFF",
    "writable_schema": "OFF",
}


def build_state_store_layout(root: Path) -> StateStoreLayout:
    if not root.is_absolute():
        raise SqlitePolicyViolation("STATE_ROOT_MUST_BE_ABSOLUTE")
    if _is_unc_path(root):
        raise SqlitePolicyViolation("STATE_ROOT_MUST_BE_LOCAL")

    catalog = root / "catalog.sqlite"
    recovery = root / "recovery.sqlite"
    if catalog == recovery:
        raise SqlitePolicyViolation("STATE_STORES_MUST_BE_SEPARATE_FILES")
    return StateStoreLayout(root=root, catalog=catalog, recovery=recovery)


def catalog_bulk_writer_policy(database_path: Path) -> SqliteConnectionPolicy:
    return _policy(
        store=SqliteStore.CATALOG,
        purpose=SqliteConnectionPurpose.CATALOG_BULK_WRITER,
        database_path=database_path,
        writable=True,
        pragmas=(
            _pragma("foreign_keys", "ON"),
            _pragma("trusted_schema", "OFF"),
            _pragma("journal_mode", "WAL"),
            _pragma("synchronous", "NORMAL"),
            _pragma("busy_timeout", 5000),
            _pragma("cache_size", -65536),
            _pragma("wal_autocheckpoint", 1000),
        ),
    )


def catalog_critical_writer_policy(database_path: Path) -> SqliteConnectionPolicy:
    return _policy(
        store=SqliteStore.CATALOG,
        purpose=SqliteConnectionPurpose.CATALOG_CRITICAL_WRITER,
        database_path=database_path,
        writable=True,
        pragmas=(
            _pragma("foreign_keys", "ON"),
            _pragma("trusted_schema", "OFF"),
            _pragma("journal_mode", "WAL"),
            _pragma("synchronous", "FULL"),
            _pragma("busy_timeout", 5000),
            _pragma("cache_size", -65536),
            _pragma("wal_autocheckpoint", 1000),
        ),
    )


def catalog_reader_policy(database_path: Path) -> SqliteConnectionPolicy:
    return _policy(
        store=SqliteStore.CATALOG,
        purpose=SqliteConnectionPurpose.CATALOG_READER,
        database_path=database_path,
        writable=False,
        pragmas=(
            _pragma("foreign_keys", "ON"),
            _pragma("trusted_schema", "OFF"),
            _pragma("busy_timeout", 5000),
            _pragma("query_only", "ON"),
        ),
    )


def recovery_writer_policy(database_path: Path) -> SqliteConnectionPolicy:
    return _policy(
        store=SqliteStore.RECOVERY,
        purpose=SqliteConnectionPurpose.RECOVERY_WRITER,
        database_path=database_path,
        writable=True,
        pragmas=(
            _pragma("foreign_keys", "ON"),
            _pragma("trusted_schema", "OFF"),
            _pragma("journal_mode", "WAL"),
            _pragma("synchronous", "FULL"),
            _pragma("busy_timeout", 5000),
            _pragma("wal_autocheckpoint", 100),
        ),
    )


def recovery_reader_policy(database_path: Path) -> SqliteConnectionPolicy:
    return _policy(
        store=SqliteStore.RECOVERY,
        purpose=SqliteConnectionPurpose.RECOVERY_READER,
        database_path=database_path,
        writable=False,
        pragmas=(
            _pragma("foreign_keys", "ON"),
            _pragma("trusted_schema", "OFF"),
            _pragma("busy_timeout", 5000),
            _pragma("query_only", "ON"),
        ),
    )


def validate_sqlite_connection_policy(policy: SqliteConnectionPolicy) -> None:
    if not policy.database_path.is_absolute():
        raise SqlitePolicyViolation("DATABASE_PATH_MUST_BE_ABSOLUTE")
    if _is_unc_path(policy.database_path):
        raise SqlitePolicyViolation("DATABASE_PATH_MUST_BE_LOCAL")
    if len(policy.pragma_map()) != len(policy.pragmas):
        raise SqlitePolicyViolation("DUPLICATE_SQLITE_PRAGMA")
    if policy.allow_attach:
        raise SqlitePolicyViolation("SQLITE_ATTACH_FORBIDDEN")
    if policy.allow_shared_cache:
        raise SqlitePolicyViolation("SQLITE_SHARED_CACHE_FORBIDDEN")
    if policy.allow_extension_loading:
        raise SqlitePolicyViolation("SQLITE_EXTENSION_LOADING_FORBIDDEN")
    if policy.allow_cross_store_transaction:
        raise SqlitePolicyViolation("CROSS_STORE_TRANSACTION_FORBIDDEN")

    pragmas = policy.pragma_map()
    if pragmas.get("foreign_keys") != "ON":
        raise SqlitePolicyViolation("SQLITE_FOREIGN_KEYS_REQUIRED")
    if pragmas.get("trusted_schema") != "OFF":
        raise SqlitePolicyViolation("SQLITE_TRUSTED_SCHEMA_MUST_BE_OFF")
    if not policy.writable and pragmas.get("query_only") != "ON":
        raise SqlitePolicyViolation("SQLITE_READ_CONNECTION_MUST_BE_QUERY_ONLY")
    if policy.store is SqliteStore.RECOVERY and policy.writable:
        _require_pragma(pragmas, "synchronous", "FULL", "RECOVERY_WRITES_REQUIRE_FULL_SYNC")
        _require_pragma(pragmas, "wal_autocheckpoint", 100, "RECOVERY_WAL_CHECKPOINT_MUST_BE_SMALL")
    if policy.purpose is SqliteConnectionPurpose.CATALOG_CRITICAL_WRITER:
        _require_pragma(pragmas, "synchronous", "FULL", "CATALOG_CRITICAL_WRITES_REQUIRE_FULL_SYNC")
    if policy.purpose is SqliteConnectionPurpose.CATALOG_BULK_WRITER:
        _require_pragma(pragmas, "synchronous", "NORMAL", "CATALOG_BULK_WRITES_USE_NORMAL_SYNC")


def apply_sqlite_connection_policy(
    connection: sqlite3.Connection,
    policy: SqliteConnectionPolicy,
) -> None:
    validate_sqlite_connection_policy(policy)
    try:
        connection.enable_load_extension(False)
    except AttributeError:
        pass
    except sqlite3.NotSupportedError:
        pass
    _enable_defensive_mode_when_supported(connection)
    for statement in policy.pragma_statements():
        connection.execute(statement)
    verify_applied_sqlite_connection_policy(connection, policy)
    connection.set_authorizer(_policy_authorizer(policy))


def verify_applied_sqlite_connection_policy(
    connection: sqlite3.Connection,
    policy: SqliteConnectionPolicy,
) -> None:
    validate_sqlite_connection_policy(policy)
    _verify_database_binding(connection, policy)
    for name, expected in _guarded_pragma_values(policy).items():
        actual = _read_pragma(connection, name)
        if _canonical_pragma_value(name, actual) != _canonical_pragma_value(name, expected):
            raise SqlitePolicyViolation(f"SQLITE_PRAGMA_DRIFT_{name.upper()}")


def classify_sqlite_error(error: sqlite3.Error) -> SqliteFailureKind:
    error_name = getattr(error, "sqlite_errorname", None)
    if isinstance(error_name, str):
        return classify_sqlite_error_name(error_name)
    return classify_sqlite_error_message(str(error))


def classify_sqlite_exception(error: BaseException) -> SqliteFailureKind:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.Error):
            failure_kind = classify_sqlite_error(current)
            if failure_kind is not SqliteFailureKind.UNKNOWN:
                return failure_kind
        current = current.__cause__ or current.__context__
    return SqliteFailureKind.UNKNOWN


def classify_sqlite_error_name(error_name: str) -> SqliteFailureKind:
    normalized = error_name.upper()
    if normalized == "SQLITE_BUSY_SNAPSHOT":
        return SqliteFailureKind.BUSY_SNAPSHOT
    if normalized.startswith("SQLITE_BUSY") or normalized.startswith("SQLITE_LOCKED"):
        return SqliteFailureKind.BUSY
    if normalized.startswith("SQLITE_FULL"):
        return SqliteFailureKind.FULL
    if normalized.startswith("SQLITE_CORRUPT"):
        return SqliteFailureKind.CORRUPT
    if normalized.startswith("SQLITE_NOTADB"):
        return SqliteFailureKind.NOT_A_DATABASE
    if normalized.startswith("SQLITE_READONLY"):
        return SqliteFailureKind.READ_ONLY
    if normalized.startswith("SQLITE_IOERR"):
        return SqliteFailureKind.IO
    return SqliteFailureKind.UNKNOWN


def classify_sqlite_error_message(message: str) -> SqliteFailureKind:
    normalized = message.lower()
    if "database is locked" in normalized or "database table is locked" in normalized:
        return SqliteFailureKind.BUSY
    if "database or disk is full" in normalized:
        return SqliteFailureKind.FULL
    if "database disk image is malformed" in normalized:
        return SqliteFailureKind.CORRUPT
    if "file is not a database" in normalized:
        return SqliteFailureKind.NOT_A_DATABASE
    if "readonly database" in normalized or "read-only database" in normalized:
        return SqliteFailureKind.READ_ONLY
    if "disk i/o error" in normalized:
        return SqliteFailureKind.IO
    return SqliteFailureKind.UNKNOWN


def _policy(
    *,
    store: SqliteStore,
    purpose: SqliteConnectionPurpose,
    database_path: Path,
    writable: bool,
    pragmas: tuple[SqlitePragma, ...],
) -> SqliteConnectionPolicy:
    policy = SqliteConnectionPolicy(
        store=store,
        purpose=purpose,
        database_path=database_path,
        writable=writable,
        pragmas=pragmas,
    )
    validate_sqlite_connection_policy(policy)
    return policy


def _pragma(name: str, value: str | int) -> SqlitePragma:
    return SqlitePragma(name=name, value=value)


def _require_pragma(
    pragmas: dict[str, str | int],
    name: str,
    expected: str | int,
    reason: str,
) -> None:
    if pragmas.get(name) != expected:
        raise SqlitePolicyViolation(reason)


def _enable_defensive_mode_when_supported(connection: sqlite3.Connection) -> None:
    setconfig = getattr(connection, "setconfig", None)
    defensive_option = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)
    if callable(setconfig) and isinstance(defensive_option, int):
        setconfig(defensive_option, True)


def _guarded_pragma_values(policy: SqliteConnectionPolicy) -> dict[str, str | int]:
    expected = dict(_SENSITIVE_PRAGMA_DEFAULTS)
    expected.update(policy.pragma_map())
    expected["query_only"] = "OFF" if policy.writable else "ON"
    return expected


def _policy_authorizer(
    policy: SqliteConnectionPolicy,
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    guarded_values = _guarded_pragma_values(policy)

    def authorize(
        action_code: int,
        argument_1: str | None,
        argument_2: str | None,
        database_name: str | None,
        trigger_or_view: str | None,
    ) -> int:
        del database_name, trigger_or_view
        if action_code in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
            return sqlite3.SQLITE_DENY
        if action_code != sqlite3.SQLITE_PRAGMA or argument_1 is None:
            return sqlite3.SQLITE_OK
        name = argument_1.lower()
        if argument_2 is None or name not in guarded_values:
            return sqlite3.SQLITE_OK
        if _canonical_pragma_value(name, argument_2) == _canonical_pragma_value(
            name,
            guarded_values[name],
        ):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


def _verify_database_binding(
    connection: sqlite3.Connection,
    policy: SqliteConnectionPolicy,
) -> None:
    rows = connection.execute("PRAGMA database_list").fetchall()
    attached = [str(row[1]) for row in rows if str(row[1]) not in {"main", "temp"}]
    if attached:
        raise SqlitePolicyViolation("SQLITE_ATTACHED_DATABASE_FORBIDDEN")
    main_rows = [row for row in rows if str(row[1]) == "main"]
    if len(main_rows) != 1:
        raise SqlitePolicyViolation("SQLITE_MAIN_DATABASE_MISSING")
    actual_path = str(main_rows[0][2])
    if not actual_path:
        raise SqlitePolicyViolation("SQLITE_DATABASE_PATH_MISMATCH")
    actual = os.path.normcase(str(Path(actual_path).resolve()))
    expected = os.path.normcase(str(policy.database_path.resolve()))
    if actual != expected:
        raise SqlitePolicyViolation("SQLITE_DATABASE_PATH_MISMATCH")


def _read_pragma(connection: sqlite3.Connection, name: str) -> object:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None:
        raise SqlitePolicyViolation(f"SQLITE_PRAGMA_UNAVAILABLE_{name.upper()}")
    return row[0]


def _canonical_pragma_value(name: str, value: object) -> object:
    normalized_name = name.lower()
    if normalized_name == "journal_mode":
        return str(value).lower()
    if normalized_name == "synchronous":
        synchronous_values = {
            "OFF": 0,
            "NORMAL": 1,
            "FULL": 2,
            "EXTRA": 3,
        }
        normalized = str(value).upper()
        return synchronous_values.get(normalized, _integer_or_string(value))
    if normalized_name in _BOOLEAN_PRAGMAS:
        normalized = str(value).upper()
        if normalized in {"ON", "TRUE", "YES"}:
            return 1
        if normalized in {"OFF", "FALSE", "NO"}:
            return 0
        return _integer_or_string(value)
    if normalized_name in _INTEGER_PRAGMAS:
        return _integer_or_string(value)
    return str(value)


def _integer_or_string(value: object) -> object:
    try:
        return int(str(value))
    except ValueError:
        return str(value).upper()


def _is_unc_path(path: Path) -> bool:
    anchor = path.anchor
    return anchor.startswith("\\\\") or anchor.startswith("//")
