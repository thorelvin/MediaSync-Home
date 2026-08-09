from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.directory_recovery import (
    SUCCESS_PATH_BY_KIND,
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
    DirectoryRecoveryState,
    DirectoryRecoveryStore,
    DirectoryRecoveryTransition,
)
from mediasync_home.domain.capabilities import MutationPermit


class DirectoryMetadataError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True, slots=True)
class DirectoryMetadataPreparationReceipt:
    recovery_id: str
    observed_metadata_json: str


@dataclass(frozen=True, slots=True)
class DirectoryMetadataApplyReceipt:
    recovery_id: str
    applied_metadata_json: str


@dataclass(frozen=True, slots=True)
class DirectoryMetadataCatalogRecord:
    recovery_id: str
    operation_id: str
    run_id: str
    run_target_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    final_relative_path: str
    desired_metadata_json: str
    applied_metadata_json: str
    metadata_hash: str


class DirectoryChildrenTerminalPort(Protocol):
    def directory_children_are_terminal(
        self,
        operation: DirectoryRecoveryOperation,
    ) -> bool: ...


class DirectoryMetadataMutationPort(Protocol):
    def prepare_directory_metadata(
        self,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryMetadataPreparationReceipt: ...

    def apply_directory_metadata(
        self,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryMetadataApplyReceipt: ...

    def verify_directory_metadata(
        self,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryMetadataApplyReceipt: ...


class DirectoryMetadataCatalogStore(Protocol):
    def record_directory_metadata(
        self,
        record: DirectoryMetadataCatalogRecord,
    ) -> DirectoryMetadataCatalogRecord: ...

    def load_directory_metadata(
        self,
        recovery_id: str,
    ) -> DirectoryMetadataCatalogRecord | None: ...


def apply_directory_metadata_lifecycle(
    *,
    permit: MutationPermit,
    operation: DirectoryRecoveryOperation,
    directory_recovery: DirectoryRecoveryStore,
    children: DirectoryChildrenTerminalPort,
    mutation: DirectoryMetadataMutationPort,
    catalog: DirectoryMetadataCatalogStore,
    process_instance_id: str,
) -> DirectoryRecoveryOperation:
    if operation.kind is not DirectoryRecoveryKind.METADATA:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_KIND_REQUIRED",
            "Load a directory metadata recovery operation before applying metadata.",
        )
    desired_metadata_json = operation.desired_metadata_json
    if desired_metadata_json is None:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_DESIRED_EVIDENCE_REQUIRED",
            "Record canonical desired directory metadata before applying it.",
        )
    _validate_permit_binding(permit=permit, operation=operation)
    path = SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.METADATA]
    operation = _reload(directory_recovery, operation.recovery_id)

    if operation.state is path[0]:
        if not children.directory_children_are_terminal(operation):
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_CHILDREN_NOT_TERMINAL",
                "Finish all child operations before applying parent directory metadata.",
            )
        operation = _transition(
            store=directory_recovery,
            operation=operation,
            next_state=path[1],
            process_instance_id=process_instance_id,
            payload={"children_terminal": True},
        )
    if operation.state is path[1]:
        preparation = mutation.prepare_directory_metadata(permit, operation)
        _validate_receipt_binding(operation.recovery_id, preparation.recovery_id)
        operation = _transition(
            store=directory_recovery,
            operation=operation,
            next_state=path[2],
            process_instance_id=process_instance_id,
            payload={"observed_metadata_json": preparation.observed_metadata_json},
        )
    if operation.state is path[2]:
        operation = _transition(
            store=directory_recovery,
            operation=operation,
            next_state=path[3],
            process_instance_id=process_instance_id,
            payload={"desired_metadata_json": desired_metadata_json},
        )
    if operation.state is path[3]:
        applied = mutation.apply_directory_metadata(permit, operation)
        _validate_receipt_binding(operation.recovery_id, applied.recovery_id)
        operation = _transition(
            store=directory_recovery,
            operation=operation,
            next_state=path[4],
            process_instance_id=process_instance_id,
            payload={"applied_metadata_json": applied.applied_metadata_json},
        )
    if operation.state is path[4]:
        verified = mutation.verify_directory_metadata(permit, operation)
        _validate_receipt_binding(operation.recovery_id, verified.recovery_id)
        operation = _transition(
            store=directory_recovery,
            operation=operation,
            next_state=path[5],
            process_instance_id=process_instance_id,
            payload={"verified_metadata_json": verified.applied_metadata_json},
        )
    if operation.state is path[5]:
        verified = mutation.verify_directory_metadata(permit, operation)
        record = directory_metadata_catalog_record(
            operation,
            verified.applied_metadata_json,
        )
        recorded = catalog.record_directory_metadata(record)
        if recorded != record:
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_CATALOG_CONFLICT",
                "Reconcile catalog metadata evidence before retrying.",
            )
        operation = _transition(
            store=directory_recovery,
            operation=operation,
            next_state=path[6],
            process_instance_id=process_instance_id,
            payload={"metadata_hash": record.metadata_hash},
        )
    return operation


def canonical_directory_metadata(*, modified_ns: int) -> str:
    if isinstance(modified_ns, bool) or modified_ns < 0:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_MODIFIED_TIME_INVALID",
            "Capture a nonnegative directory modified time before planning metadata.",
        )
    return json.dumps(
        {"modified_ns": modified_ns},
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_directory_metadata(payload: str) -> int:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_EVIDENCE_INVALID",
            "Refresh directory metadata evidence before applying it.",
        ) from exc
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"modified_ns"}
        or not isinstance(parsed["modified_ns"], int)
        or isinstance(parsed["modified_ns"], bool)
        or parsed["modified_ns"] < 0
        or canonical_directory_metadata(modified_ns=parsed["modified_ns"]) != payload
    ):
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_EVIDENCE_INVALID",
            "Refresh directory metadata evidence before applying it.",
        )
    return int(parsed["modified_ns"])


def _transition(
    *,
    store: DirectoryRecoveryStore,
    operation: DirectoryRecoveryOperation,
    next_state: DirectoryRecoveryState,
    process_instance_id: str,
    payload: dict[str, object],
) -> DirectoryRecoveryOperation:
    path = SUCCESS_PATH_BY_KIND[DirectoryRecoveryKind.METADATA]
    if next_state not in path:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_STATE_INVALID",
            "Reload the directory metadata recovery operation.",
        )
    updated = store.transition_directory_recovery_operation(
        DirectoryRecoveryTransition(
            recovery_id=operation.recovery_id,
            expected_state=operation.state,
            next_state=next_state,
            process_instance_id=process_instance_id,
            payload=payload,
        )
    )
    if updated is None:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_PHASE_CONFLICT",
            "Reload directory metadata recovery state before retrying.",
        )
    return updated


def _reload(
    store: DirectoryRecoveryStore,
    recovery_id: str,
) -> DirectoryRecoveryOperation:
    operation = store.load_directory_recovery_operation(recovery_id)
    if operation is None:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_OPERATION_NOT_FOUND",
            "Record the directory metadata recovery operation before applying it.",
        )
    return operation


def directory_metadata_catalog_record(
    operation: DirectoryRecoveryOperation,
    applied_metadata_json: str,
) -> DirectoryMetadataCatalogRecord:
    desired = operation.desired_metadata_json
    if desired is None or desired != applied_metadata_json:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_VERIFICATION_MISMATCH",
            "Reapply directory metadata and verify the exact modified time.",
        )
    material = json.dumps(
        {
            "applied_metadata_json": applied_metadata_json,
            "desired_metadata_json": desired,
            "recovery_id": operation.recovery_id,
            "schema_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return DirectoryMetadataCatalogRecord(
        recovery_id=operation.recovery_id,
        operation_id=operation.operation_id,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        final_relative_path=operation.final_relative_path,
        desired_metadata_json=desired,
        applied_metadata_json=applied_metadata_json,
        metadata_hash=hashlib.sha256(material.encode("utf-8")).hexdigest(),
    )


def _validate_permit_binding(
    *,
    permit: MutationPermit,
    operation: DirectoryRecoveryOperation,
) -> None:
    if (
        permit.run_id != operation.run_id
        or permit.run_target_id != operation.run_target_id
        or permit.endpoint_id != operation.target_endpoint_id
        or permit.endpoint_revision_id != operation.target_endpoint_revision_id
        or permit.owner_installation_id != operation.owner_installation_id
        or permit.ownership_epoch != operation.ownership_epoch
    ):
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_PERMIT_MISMATCH",
            "Reacquire the target endpoint lease before applying directory metadata.",
        )


def _validate_receipt_binding(expected: str, observed: str) -> None:
    if expected != observed:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_RECEIPT_MISMATCH",
            "Reload directory metadata recovery state before continuing.",
        )
