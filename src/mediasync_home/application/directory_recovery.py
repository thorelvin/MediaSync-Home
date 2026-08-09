from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol, TypeAlias

from mediasync_home.generated.contract_types import (
    DirectoryCreateState,
    DirectoryMetadataState,
    DirectoryQuarantineState,
    DirectoryRestoreState,
)


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class DirectoryRecoveryViolation(ValueError):
    pass


class DirectoryRecoveryKind(str, Enum):
    CREATE = "CREATE"
    METADATA = "METADATA"
    QUARANTINE = "QUARANTINE"
    RESTORE = "RESTORE"


DirectoryRecoveryState: TypeAlias = (
    DirectoryCreateState
    | DirectoryMetadataState
    | DirectoryQuarantineState
    | DirectoryRestoreState
)


INITIAL_STATE_BY_KIND: Mapping[DirectoryRecoveryKind, DirectoryRecoveryState] = {
    DirectoryRecoveryKind.CREATE: DirectoryCreateState.DIRECTORY_PLANNED,
    DirectoryRecoveryKind.METADATA: DirectoryMetadataState.DIRECTORY_METADATA_PLANNED,
    DirectoryRecoveryKind.QUARANTINE: (
        DirectoryQuarantineState.DIRECTORY_QUARANTINE_PLANNED
    ),
    DirectoryRecoveryKind.RESTORE: DirectoryRestoreState.DIRECTORY_RESTORE_PLANNED,
}

SUCCESS_PATH_BY_KIND: Mapping[
    DirectoryRecoveryKind,
    tuple[DirectoryRecoveryState, ...],
] = {
    DirectoryRecoveryKind.CREATE: (
        DirectoryCreateState.DIRECTORY_PLANNED,
        DirectoryCreateState.DIRECTORY_PARENT_VALIDATED,
        DirectoryCreateState.DIRECTORY_CREATE_INTENT_RECORDED,
        DirectoryCreateState.DIRECTORY_CREATED,
        DirectoryCreateState.DIRECTORY_IDENTITY_VERIFIED,
        DirectoryCreateState.DIRECTORY_CATALOG_RECORDED,
    ),
    DirectoryRecoveryKind.METADATA: (
        DirectoryMetadataState.DIRECTORY_METADATA_PLANNED,
        DirectoryMetadataState.CHILDREN_TERMINAL,
        DirectoryMetadataState.METADATA_PRECONDITION_VALIDATED,
        DirectoryMetadataState.METADATA_INTENT_RECORDED,
        DirectoryMetadataState.METADATA_APPLIED,
        DirectoryMetadataState.METADATA_VERIFIED,
        DirectoryMetadataState.DIRECTORY_CATALOG_RECORDED,
    ),
    DirectoryRecoveryKind.QUARANTINE: (
        DirectoryQuarantineState.DIRECTORY_QUARANTINE_PLANNED,
        DirectoryQuarantineState.DIRECTORY_EMPTY_REVALIDATED,
        DirectoryQuarantineState.QUARANTINE_INTENT_RECORDED,
        DirectoryQuarantineState.DIRECTORY_OBJECT_PRESERVED,
        DirectoryQuarantineState.SOURCE_PATH_REMOVED,
        DirectoryQuarantineState.QUARANTINE_CATALOG_RECORDED,
    ),
    DirectoryRecoveryKind.RESTORE: (
        DirectoryRestoreState.DIRECTORY_RESTORE_PLANNED,
        DirectoryRestoreState.RESTORE_TARGET_ABSENT_REVALIDATED,
        DirectoryRestoreState.RESTORE_INTENT_RECORDED,
        DirectoryRestoreState.DIRECTORY_RESTORED,
        DirectoryRestoreState.DIRECTORY_RESTORE_VERIFIED,
        DirectoryRestoreState.RESTORE_CATALOG_RECORDED,
    ),
}

CONFLICT_STATE_BY_KIND: Mapping[DirectoryRecoveryKind, DirectoryRecoveryState] = {
    DirectoryRecoveryKind.CREATE: DirectoryCreateState.DIRECTORY_TYPE_CONFLICT,
    DirectoryRecoveryKind.METADATA: DirectoryMetadataState.DIRECTORY_METADATA_CONFLICT,
    DirectoryRecoveryKind.QUARANTINE: (
        DirectoryQuarantineState.DIRECTORY_QUARANTINE_CONFLICT
    ),
    DirectoryRecoveryKind.RESTORE: DirectoryRestoreState.DIRECTORY_RESTORE_CONFLICT,
}


@dataclass(frozen=True, slots=True)
class DirectoryRecoveryOperation:
    recovery_id: str
    operation_id: str
    run_id: str
    run_target_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    owner_installation_id: str
    ownership_epoch: int
    kind: DirectoryRecoveryKind
    state: DirectoryRecoveryState
    final_relative_path: str
    expected_precondition_json: str | None = None
    desired_metadata_json: str | None = None
    managed_object_id: str | None = None
    last_error_code: str | None = None
    event_sequence: int = 0
    event_hash: str | None = None
    row_version: int = 1


@dataclass(frozen=True, slots=True)
class DirectoryRecoveryTransition:
    recovery_id: str
    expected_state: DirectoryRecoveryState
    next_state: DirectoryRecoveryState
    process_instance_id: str
    payload: Mapping[str, object]
    managed_object_id: str | None = None
    last_error_code: str | None = None


class DirectoryRecoveryStore(Protocol):
    def record_directory_recovery_operation(
        self,
        operation: DirectoryRecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> DirectoryRecoveryOperation: ...

    def transition_directory_recovery_operation(
        self,
        transition: DirectoryRecoveryTransition,
    ) -> DirectoryRecoveryOperation | None: ...

    def load_directory_recovery_operation(
        self,
        recovery_id: str,
    ) -> DirectoryRecoveryOperation | None: ...

    def list_unresolved_directory_recovery_operations(
        self,
        *,
        limit: int,
    ) -> tuple[DirectoryRecoveryOperation, ...]: ...

    def list_conflicted_directory_recovery_operations(
        self,
        *,
        limit: int,
    ) -> tuple[DirectoryRecoveryOperation, ...]: ...


def planned_directory_recovery_operation(
    *,
    recovery_id: str,
    operation_id: str,
    run_id: str,
    run_target_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    owner_installation_id: str,
    ownership_epoch: int,
    kind: DirectoryRecoveryKind,
    final_relative_path: str,
    expected_precondition_json: str | None = None,
    desired_metadata_json: str | None = None,
) -> DirectoryRecoveryOperation:
    operation = DirectoryRecoveryOperation(
        recovery_id=recovery_id,
        operation_id=operation_id,
        run_id=run_id,
        run_target_id=run_target_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        kind=kind,
        state=INITIAL_STATE_BY_KIND[kind],
        final_relative_path=final_relative_path,
        expected_precondition_json=expected_precondition_json,
        desired_metadata_json=desired_metadata_json,
    )
    validate_directory_recovery_operation(operation)
    return operation


def directory_recovery_id(
    *,
    run_id: str,
    operation_id: str,
    kind: DirectoryRecoveryKind,
) -> str:
    material = canonical_directory_recovery_payload(
        {
            "kind": kind.value,
            "operation_id": operation_id,
            "run_id": run_id,
            "schema_version": 1,
        }
    )
    return f"directory-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def validate_directory_recovery_operation(operation: DirectoryRecoveryOperation) -> None:
    for value in (
        operation.recovery_id,
        operation.operation_id,
        operation.run_id,
        operation.run_target_id,
        operation.target_endpoint_id,
        operation.target_endpoint_revision_id,
        operation.owner_installation_id,
    ):
        if not value.strip() or value != value.strip():
            raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_BINDING_INVALID")
    _validate_relative_path(operation.final_relative_path)
    if operation.ownership_epoch < 1:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_OWNERSHIP_EPOCH_INVALID")
    allowed_states = set(SUCCESS_PATH_BY_KIND[operation.kind]) | {
        CONFLICT_STATE_BY_KIND[operation.kind]
    }
    if operation.state not in allowed_states:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_STATE_KIND_MISMATCH")
    if operation.state is CONFLICT_STATE_BY_KIND[operation.kind] and (
        operation.last_error_code is None
        or not operation.last_error_code.strip()
        or operation.last_error_code != operation.last_error_code.strip()
    ):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_CONFLICT_REQUIRES_ERROR")
    if operation.managed_object_id is not None and (
        not operation.managed_object_id.strip()
        or operation.managed_object_id != operation.managed_object_id.strip()
    ):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_MANAGED_OBJECT_INVALID")
    if operation.event_sequence < 0 or operation.row_version < 1:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_SEQUENCE_INVALID")
    if (operation.event_sequence == 0) != (operation.event_hash is None):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_EVENT_HASH_MISSING")
    if operation.event_hash is not None and not HASH_PATTERN.fullmatch(
        operation.event_hash
    ):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_EVENT_HASH_INVALID")
    for payload in (
        operation.expected_precondition_json,
        operation.desired_metadata_json,
    ):
        if payload is not None:
            _canonical_json_object(payload)


def validate_directory_recovery_transition(
    operation: DirectoryRecoveryOperation,
    transition: DirectoryRecoveryTransition,
) -> None:
    validate_directory_recovery_operation(operation)
    if transition.recovery_id != operation.recovery_id:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_TRANSITION_BINDING_MISMATCH")
    if transition.expected_state is not operation.state:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_TRANSITION_STATE_MISMATCH")
    if (
        not transition.process_instance_id.strip()
        or transition.process_instance_id != transition.process_instance_id.strip()
    ):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_TRANSITION_REQUIRES_PROCESS")
    canonical_directory_recovery_payload(transition.payload)
    if directory_recovery_is_terminal(operation):
        raise DirectoryRecoveryViolation(
            "DIRECTORY_RECOVERY_TERMINAL_STATE_CANNOT_TRANSITION"
        )
    success_path = SUCCESS_PATH_BY_KIND[operation.kind]
    conflict = CONFLICT_STATE_BY_KIND[operation.kind]
    if transition.next_state is conflict:
        if (
            transition.last_error_code is None
            or not transition.last_error_code.strip()
            or transition.last_error_code != transition.last_error_code.strip()
        ):
            raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_CONFLICT_REQUIRES_ERROR")
        return
    try:
        index = success_path.index(operation.state)
    except ValueError as exc:
        raise DirectoryRecoveryViolation(
            "DIRECTORY_RECOVERY_TERMINAL_STATE_CANNOT_TRANSITION"
        ) from exc
    if index + 1 >= len(success_path) or success_path[index + 1] is not transition.next_state:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_INVALID_STATE_TRANSITION")


def directory_recovery_is_terminal(operation: DirectoryRecoveryOperation) -> bool:
    return operation.state in {
        SUCCESS_PATH_BY_KIND[operation.kind][-1],
        CONFLICT_STATE_BY_KIND[operation.kind],
    }


def canonical_directory_recovery_payload(payload: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            dict(payload),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_PAYLOAD_INVALID") from exc


def _canonical_json_object(payload: str) -> str:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_EVIDENCE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_EVIDENCE_JSON_INVALID")
    canonical = json.dumps(
        value,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if canonical != payload:
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_EVIDENCE_NOT_CANONICAL")
    return canonical


def _validate_relative_path(value: str) -> None:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or WINDOWS_DRIVE_PATTERN.match(normalized)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise DirectoryRecoveryViolation("DIRECTORY_RECOVERY_RELATIVE_PATH_INVALID")
