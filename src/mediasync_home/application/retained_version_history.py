from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


DEFAULT_RETAINED_VERSION_LIMIT = 10
MAX_RETAINED_VERSION_LIMIT = 25
MAX_RETAINED_VERSION_IDENTIFIER_LENGTH = 256
MAX_RETAINED_VERSION_TIMESTAMP_LENGTH = 64
RETAINED_VERSION_CURSOR_VERSION = 1


class RetainedVersionHistoryError(ValueError):
    pass


class VersionRestoreCommandName(str, Enum):
    PROTECT_RETAINED_VERSION_FOR_RESTORE = "PROTECT_RETAINED_VERSION_FOR_RESTORE"
    RESTORE_RETAINED_VERSION = "RESTORE_RETAINED_VERSION"
    UNDO_RETAINED_VERSION_RESTORE = "UNDO_RETAINED_VERSION_RESTORE"


@dataclass(frozen=True, slots=True)
class RetainedVersionCursor:
    created_utc: str
    version_object_id: str

    @classmethod
    def from_summary(cls, summary: "RetainedVersionSummary") -> "RetainedVersionCursor":
        return cls(
            created_utc=summary.created_utc,
            version_object_id=summary.version_object_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "cursor_version": RETAINED_VERSION_CURSOR_VERSION,
            "created_utc": self.created_utc,
            "version_object_id": self.version_object_id,
        }


@dataclass(frozen=True, slots=True)
class RetainedVersionSummary:
    version_object_id: str
    run_id: str
    operation_id: str
    job_id: str
    target_endpoint_id: str
    final_relative_path: str
    created_utc: str
    retention_until_utc: str
    state: str
    row_version: int
    hold_id: str | None = None
    hold_reason: str | None = None
    hold_created_utc: str | None = None
    restore_id: str | None = None
    restore_state: str | None = None
    restore_validation_code: str | None = None
    restore_created_utc: str | None = None
    restore_completed_utc: str | None = None
    rollback_state: str | None = None
    rollback_retention_until_utc: str | None = None
    rollback_validation_code: str | None = None

    @property
    def protected_for_restore(self) -> bool:
        return self.hold_id is not None

    @property
    def restorable(self) -> bool:
        return self.state == "RETAINED"

    @property
    def restore_pending(self) -> bool:
        return self.restore_state in {
            "REQUESTED",
            "INTENT_RECORDED",
            "CURRENT_FINAL_PRESERVED",
            "HISTORICAL_APPLIED",
            "FINAL_VERIFIED",
        }

    @property
    def restore_undo_available(self) -> bool:
        return self.restore_state == "COMPLETED" and self.rollback_state == "AVAILABLE"

    @property
    def restore_undo_pending(self) -> bool:
        return self.rollback_state in {
            "UNDO_REQUESTED",
            "UNDO_INTENT_RECORDED",
            "UNDO_APPLIED",
            "UNDO_VERIFIED",
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "version_object_id": self.version_object_id,
            "run_id": self.run_id,
            "operation_id": self.operation_id,
            "job_id": self.job_id,
            "target_endpoint_id": self.target_endpoint_id,
            "final_relative_path": self.final_relative_path,
            "created_utc": self.created_utc,
            "retention_until_utc": self.retention_until_utc,
            "state": self.state,
            "row_version": self.row_version,
            "restorable": self.restorable,
            "protected_for_restore": self.protected_for_restore,
            "hold_id": self.hold_id,
            "hold_reason": self.hold_reason,
            "hold_created_utc": self.hold_created_utc,
            "restore_id": self.restore_id,
            "restore_state": self.restore_state,
            "restore_pending": self.restore_pending,
            "restore_validation_code": self.restore_validation_code,
            "restore_created_utc": self.restore_created_utc,
            "restore_completed_utc": self.restore_completed_utc,
            "rollback_state": self.rollback_state,
            "rollback_retention_until_utc": self.rollback_retention_until_utc,
            "rollback_validation_code": self.rollback_validation_code,
            "restore_undo_available": self.restore_undo_available,
            "restore_undo_pending": self.restore_undo_pending,
        }


@dataclass(frozen=True, slots=True)
class RetainedVersionPage:
    run_id: str
    limit: int
    has_more: bool
    read_model_available: bool
    next_cursor: RetainedVersionCursor | None = None
    versions: tuple[RetainedVersionSummary, ...] = ()

    @classmethod
    def unavailable(cls, *, run_id: str, limit: int) -> "RetainedVersionPage":
        return cls(
            run_id=run_id,
            limit=limit,
            has_more=False,
            read_model_available=False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "limit": self.limit,
            "has_more": self.has_more,
            "read_model_available": self.read_model_available,
            "next_cursor": (
                None if self.next_cursor is None else self.next_cursor.to_dict()
            ),
            "versions": [version.to_dict() for version in self.versions],
        }


class RetainedVersionReadModelStore(Protocol):
    def list_retained_versions_for_run(
        self,
        *,
        run_id: str,
        limit: int,
        after: RetainedVersionCursor | None,
    ) -> tuple[RetainedVersionSummary, ...]: ...


@dataclass(frozen=True, slots=True)
class ProtectRetainedVersionForRestoreCommand:
    request_id: str
    idempotency_key: str
    version_object_id: str
    expected_row_version: int
    explicit_confirmation: bool


@dataclass(frozen=True, slots=True)
class VersionRestoreProtectionOutcome:
    protected: bool
    validation_code: str
    next_action: str
    version: RetainedVersionSummary | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "protected": self.protected,
            "validation_code": self.validation_code,
            "next_action": self.next_action,
            "idempotent_replay": self.idempotent_replay,
        }
        if self.version is not None:
            payload["version"] = self.version.to_dict()
        return payload


class VersionRestoreProtectionStore(Protocol):
    def protect_retained_version_for_restore(
        self,
        *,
        command: ProtectRetainedVersionForRestoreCommand,
        created_utc: str,
    ) -> VersionRestoreProtectionOutcome: ...


@dataclass(frozen=True, slots=True)
class RestoreRetainedVersionCommand:
    request_id: str
    idempotency_key: str
    version_object_id: str
    expected_row_version: int
    explicit_confirmation: bool


@dataclass(frozen=True, slots=True)
class VersionRestoreRequestOutcome:
    scheduled: bool
    validation_code: str
    next_action: str
    restore_id: str | None = None
    state: str | None = None
    version: RetainedVersionSummary | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "scheduled": self.scheduled,
            "validation_code": self.validation_code,
            "next_action": self.next_action,
            "idempotent_replay": self.idempotent_replay,
        }
        if self.restore_id is not None:
            payload["restore_id"] = self.restore_id
        if self.state is not None:
            payload["state"] = self.state
        if self.version is not None:
            payload["version"] = self.version.to_dict()
        return payload


class VersionRestoreRequestStore(Protocol):
    def request_retained_version_restore(
        self,
        *,
        command: RestoreRetainedVersionCommand,
        created_utc: str,
    ) -> VersionRestoreRequestOutcome: ...


@dataclass(frozen=True, slots=True)
class UndoRetainedVersionRestoreCommand:
    request_id: str
    idempotency_key: str
    restore_id: str
    version_object_id: str
    expected_row_version: int
    explicit_confirmation: bool


@dataclass(frozen=True, slots=True)
class VersionRestoreUndoRequestOutcome:
    scheduled: bool
    validation_code: str
    next_action: str
    restore_id: str | None = None
    state: str | None = None
    version: RetainedVersionSummary | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "scheduled": self.scheduled,
            "validation_code": self.validation_code,
            "next_action": self.next_action,
            "idempotent_replay": self.idempotent_replay,
        }
        if self.restore_id is not None:
            payload["restore_id"] = self.restore_id
        if self.state is not None:
            payload["state"] = self.state
        if self.version is not None:
            payload["version"] = self.version.to_dict()
        return payload


class VersionRestoreUndoRequestStore(Protocol):
    def request_retained_version_restore_undo(
        self,
        *,
        command: UndoRetainedVersionRestoreCommand,
        created_utc: str,
    ) -> VersionRestoreUndoRequestOutcome: ...


def query_retained_versions(
    *,
    version_store: RetainedVersionReadModelStore | None,
    run_id: str,
    limit: int | None = None,
    after: dict[str, object] | None = None,
) -> RetainedVersionPage:
    normalized_run_id = _required_identifier(run_id, "RUN_ID")
    page_limit = DEFAULT_RETAINED_VERSION_LIMIT if limit is None else int(limit)
    if page_limit < 1 or page_limit > MAX_RETAINED_VERSION_LIMIT:
        raise RetainedVersionHistoryError("RETAINED_VERSION_LIMIT_OUT_OF_RANGE")
    normalized_after = _normalize_cursor(after)
    if version_store is None:
        return RetainedVersionPage.unavailable(
            run_id=normalized_run_id,
            limit=page_limit,
        )
    rows = version_store.list_retained_versions_for_run(
        run_id=normalized_run_id,
        limit=page_limit + 1,
        after=normalized_after,
    )
    versions = rows[:page_limit]
    has_more = len(rows) > page_limit
    return RetainedVersionPage(
        run_id=normalized_run_id,
        limit=page_limit,
        has_more=has_more,
        read_model_available=True,
        next_cursor=(
            RetainedVersionCursor.from_summary(versions[-1])
            if has_more and versions
            else None
        ),
        versions=versions,
    )


def parse_protect_retained_version_for_restore_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> ProtectRetainedVersionForRestoreCommand:
    if set(payload) != {
        "version_object_id",
        "expected_row_version",
        "explicit_confirmation",
    }:
        raise RetainedVersionHistoryError("VERSION_RESTORE_PROTECTION_PAYLOAD_INVALID")
    version_object_id = _required_identifier(
        payload.get("version_object_id"),
        "VERSION_OBJECT_ID",
    )
    row_version = payload.get("expected_row_version")
    if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 1:
        raise RetainedVersionHistoryError(
            "VERSION_RESTORE_PROTECTION_ROW_VERSION_INVALID"
        )
    if payload.get("explicit_confirmation") is not True:
        raise RetainedVersionHistoryError(
            "VERSION_RESTORE_PROTECTION_CONFIRMATION_REQUIRED"
        )
    return ProtectRetainedVersionForRestoreCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        version_object_id=version_object_id,
        expected_row_version=row_version,
        explicit_confirmation=True,
    )


def parse_restore_retained_version_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> RestoreRetainedVersionCommand:
    if set(payload) != {
        "version_object_id",
        "expected_row_version",
        "explicit_confirmation",
    }:
        raise RetainedVersionHistoryError("VERSION_RESTORE_PAYLOAD_INVALID")
    version_object_id = _required_identifier(
        payload.get("version_object_id"),
        "VERSION_OBJECT_ID",
    )
    row_version = payload.get("expected_row_version")
    if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 1:
        raise RetainedVersionHistoryError("VERSION_RESTORE_ROW_VERSION_INVALID")
    if payload.get("explicit_confirmation") is not True:
        raise RetainedVersionHistoryError("VERSION_RESTORE_CONFIRMATION_REQUIRED")
    return RestoreRetainedVersionCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        version_object_id=version_object_id,
        expected_row_version=row_version,
        explicit_confirmation=True,
    )


def parse_undo_retained_version_restore_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> UndoRetainedVersionRestoreCommand:
    if set(payload) != {
        "restore_id",
        "version_object_id",
        "expected_row_version",
        "explicit_confirmation",
    }:
        raise RetainedVersionHistoryError("VERSION_RESTORE_UNDO_PAYLOAD_INVALID")
    restore_id = _required_identifier(payload.get("restore_id"), "RESTORE_ID")
    version_object_id = _required_identifier(
        payload.get("version_object_id"),
        "VERSION_OBJECT_ID",
    )
    row_version = payload.get("expected_row_version")
    if isinstance(row_version, bool) or not isinstance(row_version, int) or row_version < 1:
        raise RetainedVersionHistoryError("VERSION_RESTORE_UNDO_ROW_VERSION_INVALID")
    if payload.get("explicit_confirmation") is not True:
        raise RetainedVersionHistoryError(
            "VERSION_RESTORE_UNDO_CONFIRMATION_REQUIRED"
        )
    return UndoRetainedVersionRestoreCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        restore_id=restore_id,
        version_object_id=version_object_id,
        expected_row_version=row_version,
        explicit_confirmation=True,
    )


def _normalize_cursor(value: dict[str, object] | None) -> RetainedVersionCursor | None:
    if value is None:
        return None
    if set(value) != {"cursor_version", "created_utc", "version_object_id"}:
        raise RetainedVersionHistoryError("RETAINED_VERSION_CURSOR_FIELDS_INVALID")
    version = value.get("cursor_version")
    if isinstance(version, bool) or version != RETAINED_VERSION_CURSOR_VERSION:
        raise RetainedVersionHistoryError("RETAINED_VERSION_CURSOR_VERSION_INVALID")
    created_utc = value.get("created_utc")
    if (
        not isinstance(created_utc, str)
        or not created_utc.strip()
        or len(created_utc.strip()) > MAX_RETAINED_VERSION_TIMESTAMP_LENGTH
    ):
        raise RetainedVersionHistoryError("RETAINED_VERSION_CURSOR_VALUE_INVALID")
    return RetainedVersionCursor(
        created_utc=created_utc.strip(),
        version_object_id=_required_identifier(
            value.get("version_object_id"),
            "CURSOR_VERSION_OBJECT_ID",
        ),
    )


def _required_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise RetainedVersionHistoryError(f"RETAINED_VERSION_{field_name}_INVALID")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_RETAINED_VERSION_IDENTIFIER_LENGTH:
        raise RetainedVersionHistoryError(f"RETAINED_VERSION_{field_name}_INVALID")
    return normalized
