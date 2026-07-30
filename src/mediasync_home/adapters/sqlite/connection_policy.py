from __future__ import annotations

import sqlite3
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
    for statement in policy.pragma_statements():
        connection.execute(statement)


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


def _is_unc_path(path: Path) -> bool:
    anchor = path.anchor
    return anchor.startswith("\\\\") or anchor.startswith("//")
