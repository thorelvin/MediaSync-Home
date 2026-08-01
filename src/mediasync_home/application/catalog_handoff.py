from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class CatalogHandoffError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True)
class RetainedVersionCatalogHandoff:
    version_object_id: str
    job_id: str
    job_revision_id: str
    endpoint_generation: int
    owner_installation_id: str
    ownership_epoch: int
    original_fingerprint_json: str
    created_utc: str
    retention_policy: str
    retention_until_utc: str
    manifest_hash: str


@dataclass(frozen=True)
class FinalFileCatalogHandoff:
    handoff_id: str
    run_id: str
    run_target_id: str
    operation_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    final_relative_path: str
    content_hash: str
    lease_id: str
    fencing_token: int
    effect_kind: str = "COPY_NEW_FINAL_FILE"
    retained_version: RetainedVersionCatalogHandoff | None = None


@dataclass(frozen=True)
class CatalogHandoffOutcome:
    handoff: FinalFileCatalogHandoff
    recovery_operation: RecoveryOperation
    idempotent_replay: bool


class CatalogHandoffReconciliationStatus(str, Enum):
    RECOVERED = "RECOVERED"
    PENDING_CATALOG = "PENDING_CATALOG"
    AMBIGUOUS = "AMBIGUOUS"
    PHASE_CONFLICT = "PHASE_CONFLICT"


@dataclass(frozen=True)
class CatalogHandoffReconciliationItem:
    run_id: str
    operation_id: str
    handoff_id: str
    status: CatalogHandoffReconciliationStatus
    validation_code: str | None = None


@dataclass(frozen=True)
class CatalogHandoffReconciliationReport:
    scanned: int
    recovered: tuple[CatalogHandoffReconciliationItem, ...]
    pending: tuple[CatalogHandoffReconciliationItem, ...]
    ambiguous: tuple[CatalogHandoffReconciliationItem, ...]

    @property
    def should_block_mutating_readiness(self) -> bool:
        return bool(self.ambiguous)


class FinalFileCatalogHandoffStore(Protocol):
    def record_final_file_handoff(
        self,
        handoff: FinalFileCatalogHandoff,
    ) -> FinalFileCatalogHandoff: ...

    def load_final_file_handoff(self, handoff_id: str) -> FinalFileCatalogHandoff | None: ...


class CatalogHandoffRecoveryOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


def record_catalog_handoff_after_final_verification(
    *,
    run_id: str,
    operation_id: str,
    content_hash: str,
    recovery_operations: RecoveryOperationStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
) -> CatalogHandoffOutcome:
    _validate_process_instance_id(process_instance_id)
    operation = recovery_operations.load_operation(run_id=run_id, operation_id=operation_id)
    if operation is None:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_OPERATION_NOT_FOUND",
            "Load the recovery operation before catalog handoff.",
        )

    if operation.phase is RecoveryOperationPhase.CATALOG_RECORDED:
        handoff = _handoff_from_recorded_operation(operation=operation, content_hash=content_hash)
        recorded = catalog_handoffs.record_final_file_handoff(handoff)
        return CatalogHandoffOutcome(
            handoff=recorded,
            recovery_operation=operation,
            idempotent_replay=True,
        )
    if operation.phase is not RecoveryOperationPhase.FINAL_VERIFIED:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_REQUIRES_FINAL_VERIFIED",
            "Verify the final file before recording the catalog handoff.",
        )

    handoff = _handoff_from_operation(operation=operation, content_hash=content_hash)
    recorded = catalog_handoffs.record_final_file_handoff(handoff)
    updated = recovery_operations.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=RecoveryOperationPhase.FINAL_VERIFIED,
        next_phase=RecoveryOperationPhase.CATALOG_RECORDED,
        process_instance_id=process_instance_id,
        payload=_handoff_payload(recorded),
        catalog_handoff_id=recorded.handoff_id,
    )
    if updated is None:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RECOVERY_PHASE_CONFLICT",
            "Reconcile the persisted catalog handoff with recovery state before retrying.",
        )
    return CatalogHandoffOutcome(
        handoff=recorded,
        recovery_operation=updated,
        idempotent_replay=False,
    )


def reconcile_catalog_handoffs_after_startup(
    *,
    recovery_operations: CatalogHandoffRecoveryOperationStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
    limit: int = 100,
) -> CatalogHandoffReconciliationReport:
    _validate_process_instance_id(process_instance_id)
    if limit < 1:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RECONCILIATION_REQUIRES_LIMIT",
            "Run startup catalog handoff reconciliation with a positive bounded limit.",
        )

    recovered: list[CatalogHandoffReconciliationItem] = []
    pending: list[CatalogHandoffReconciliationItem] = []
    ambiguous: list[CatalogHandoffReconciliationItem] = []
    operations = recovery_operations.list_operations_in_phase(
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        limit=limit,
    )
    for operation in operations:
        handoff_id = _handoff_id(operation)
        handoff = catalog_handoffs.load_final_file_handoff(handoff_id)
        if handoff is None:
            pending.append(
                _reconciliation_item(
                    operation=operation,
                    handoff_id=handoff_id,
                    status=CatalogHandoffReconciliationStatus.PENDING_CATALOG,
                    validation_code="CATALOG_HANDOFF_NOT_COMMITTED",
                )
            )
            continue

        mismatch = _catalog_handoff_reconciliation_mismatch(
            operation=operation,
            handoff=handoff,
        )
        if mismatch is not None:
            ambiguous.append(
                _reconciliation_item(
                    operation=operation,
                    handoff_id=handoff_id,
                    status=CatalogHandoffReconciliationStatus.AMBIGUOUS,
                    validation_code=mismatch,
                )
            )
            continue

        updated = recovery_operations.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=RecoveryOperationPhase.FINAL_VERIFIED,
            next_phase=RecoveryOperationPhase.CATALOG_RECORDED,
            process_instance_id=process_instance_id,
            payload=_handoff_payload(handoff),
            catalog_handoff_id=handoff.handoff_id,
        )
        if updated is None:
            ambiguous.append(
                _reconciliation_item(
                    operation=operation,
                    handoff_id=handoff_id,
                    status=CatalogHandoffReconciliationStatus.PHASE_CONFLICT,
                    validation_code="CATALOG_HANDOFF_RECONCILIATION_PHASE_CONFLICT",
                )
            )
            continue
        recovered.append(
            _reconciliation_item(
                operation=updated,
                handoff_id=handoff.handoff_id,
                status=CatalogHandoffReconciliationStatus.RECOVERED,
            )
        )

    return CatalogHandoffReconciliationReport(
        scanned=len(operations),
        recovered=tuple(recovered),
        pending=tuple(pending),
        ambiguous=tuple(ambiguous),
    )


def validate_final_file_catalog_handoff(handoff: FinalFileCatalogHandoff) -> None:
    if not _non_empty(
        handoff.handoff_id,
        handoff.run_id,
        handoff.run_target_id,
        handoff.operation_id,
        handoff.target_endpoint_id,
        handoff.target_endpoint_revision_id,
        handoff.lease_id,
        handoff.effect_kind,
    ):
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_REQUIRES_IDENTIFIERS",
            "Record final-file catalog handoff with all immutable identifiers.",
        )
    if handoff.effect_kind not in {"COPY_NEW_FINAL_FILE", "CREATE_DIRECTORY"}:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_EFFECT_UNSUPPORTED",
            "Use a dedicated catalog handoff type for this operation effect.",
        )
    if handoff.fencing_token < 1:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_REQUIRES_FENCING_TOKEN",
            "Record a positive fencing token with the catalog handoff.",
        )
    if HASH_PATTERN.fullmatch(handoff.content_hash) is None:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_REQUIRES_CONTENT_HASH",
            "Record a lowercase SHA-256 final content hash with the catalog handoff.",
        )
    if not _valid_relative_path(handoff.final_relative_path):
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_REQUIRES_RELATIVE_FINAL_PATH",
            "Record only endpoint-relative final paths in the catalog handoff.",
        )
    if handoff.retained_version is not None:
        _validate_retained_version_handoff(handoff.retained_version)


def _handoff_from_recorded_operation(
    *,
    operation: RecoveryOperation,
    content_hash: str,
) -> FinalFileCatalogHandoff:
    if operation.catalog_handoff_id is None or not operation.catalog_handoff_id.strip():
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_ID_MISSING",
            "Reconcile recovery state before replaying catalog handoff.",
        )
    return _handoff_from_operation(
        operation=operation,
        content_hash=content_hash,
        handoff_id=operation.catalog_handoff_id,
    )


def _handoff_from_operation(
    *,
    operation: RecoveryOperation,
    content_hash: str,
    handoff_id: str | None = None,
) -> FinalFileCatalogHandoff:
    handoff = FinalFileCatalogHandoff(
        handoff_id=handoff_id or _handoff_id(operation),
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        final_relative_path=operation.final_relative_path,
        content_hash=content_hash,
        lease_id=operation.lease_id,
        fencing_token=operation.fencing_token,
        effect_kind=_effect_kind(operation),
        retained_version=_retained_version_from_operation(operation),
    )
    validate_final_file_catalog_handoff(handoff)
    return handoff


def _handoff_id(operation: RecoveryOperation) -> str:
    role = (
        "final-directory"
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY
        else "final-file"
    )
    return f"{role}:{operation.run_id}:{operation.operation_id}"


def _effect_kind(operation: RecoveryOperation) -> str:
    if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
        return "CREATE_DIRECTORY"
    return "COPY_NEW_FINAL_FILE"


def _handoff_payload(handoff: FinalFileCatalogHandoff) -> Mapping[str, object]:
    return {
        "catalog_effect_kind": handoff.effect_kind,
        "catalog_handoff_id": handoff.handoff_id,
        "content_hash": handoff.content_hash,
        "final_relative_path": handoff.final_relative_path,
        "retained_version_manifest_hash": (
            None
            if handoff.retained_version is None
            else handoff.retained_version.manifest_hash
        ),
        "retained_version_object_id": (
            None
            if handoff.retained_version is None
            else handoff.retained_version.version_object_id
        ),
    }


def _retained_version_from_operation(
    operation: RecoveryOperation,
) -> RetainedVersionCatalogHandoff | None:
    if operation.version_object_id is None:
        return None
    required = (
        operation.job_id,
        operation.job_revision_id,
        operation.expected_target_fingerprint_json,
        operation.version_created_utc,
        operation.retention_policy,
        operation.version_retention_until_utc,
        operation.version_manifest_hash,
    )
    if not all(isinstance(value, str) and bool(value.strip()) for value in required):
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_METADATA_MISSING",
            "Reconcile the preserved version manifest evidence before catalog handoff.",
        )
    fingerprint_json = _canonical_fingerprint_json(
        operation.expected_target_fingerprint_json or ""
    )
    retained = RetainedVersionCatalogHandoff(
        version_object_id=operation.version_object_id,
        job_id=operation.job_id or "",
        job_revision_id=operation.job_revision_id or "",
        endpoint_generation=operation.endpoint_generation,
        owner_installation_id=operation.owner_installation_id,
        ownership_epoch=operation.ownership_epoch,
        original_fingerprint_json=fingerprint_json,
        created_utc=operation.version_created_utc or "",
        retention_policy=operation.retention_policy or "",
        retention_until_utc=operation.version_retention_until_utc or "",
        manifest_hash=operation.version_manifest_hash or "",
    )
    _validate_retained_version_handoff(retained)
    return retained


def _validate_retained_version_handoff(
    retained: RetainedVersionCatalogHandoff,
) -> None:
    if not _non_empty(
        retained.version_object_id,
        retained.job_id,
        retained.job_revision_id,
        retained.owner_installation_id,
        retained.created_utc,
        retained.retention_until_utc,
    ):
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_IDENTIFIERS_MISSING",
            "Record all immutable retained-version identifiers in the catalog handoff.",
        )
    if retained.endpoint_generation < 1 or retained.ownership_epoch < 1:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_NUMBERS_INVALID",
            "Record positive endpoint generation and ownership epoch for the retained version.",
        )
    if retained.retention_policy != "THIRTY_DAYS":
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_POLICY_INVALID",
            "Use the sealed supported retention policy for the retained version.",
        )
    if HASH_PATTERN.fullmatch(retained.manifest_hash) is None:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_MANIFEST_HASH_INVALID",
            "Record the canonical version manifest hash before catalog handoff.",
        )
    _canonical_fingerprint_json(retained.original_fingerprint_json)


def _canonical_fingerprint_json(raw_fingerprint: str) -> str:
    try:
        fingerprint = json.loads(raw_fingerprint)
    except json.JSONDecodeError as exc:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_FINGERPRINT_INVALID",
            "Record a canonical original fingerprint for the retained version.",
        ) from exc
    if not isinstance(fingerprint, dict) or set(fingerprint) != {
        "byte_count",
        "content_hash",
    }:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_FINGERPRINT_INVALID",
            "Record a canonical original fingerprint for the retained version.",
        )
    byte_count = fingerprint.get("byte_count")
    content_hash = fingerprint.get("content_hash")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
        or not isinstance(content_hash, str)
        or HASH_PATTERN.fullmatch(content_hash) is None
    ):
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_FINGERPRINT_INVALID",
            "Record a canonical original fingerprint for the retained version.",
        )
    canonical = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    if canonical != raw_fingerprint:
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_RETAINED_VERSION_FINGERPRINT_NOT_CANONICAL",
            "Record the canonical original fingerprint for the retained version.",
        )
    return canonical


def _catalog_handoff_reconciliation_mismatch(
    *,
    operation: RecoveryOperation,
    handoff: FinalFileCatalogHandoff,
) -> str | None:
    try:
        validate_final_file_catalog_handoff(handoff)
    except CatalogHandoffError as exc:
        return exc.validation_code

    expected = _handoff_from_operation(
        operation=operation,
        content_hash=handoff.content_hash,
        handoff_id=handoff.handoff_id,
    )
    if handoff != expected:
        return "CATALOG_HANDOFF_RECONCILIATION_PAYLOAD_MISMATCH"
    return None


def _reconciliation_item(
    *,
    operation: RecoveryOperation,
    handoff_id: str,
    status: CatalogHandoffReconciliationStatus,
    validation_code: str | None = None,
) -> CatalogHandoffReconciliationItem:
    return CatalogHandoffReconciliationItem(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        handoff_id=handoff_id,
        status=status,
        validation_code=validation_code,
    )


def _validate_process_instance_id(process_instance_id: str) -> None:
    if not process_instance_id.strip():
        raise CatalogHandoffError(
            "CATALOG_HANDOFF_REQUIRES_PROCESS_INSTANCE",
            "Bind catalog handoff to the Engine Host process instance.",
        )


def _non_empty(*values: str) -> bool:
    return all(value.strip() for value in values)


def _valid_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or normalized.startswith("//")
        or WINDOWS_DRIVE_PATTERN.match(normalized)
    ):
        return False
    parts = tuple(normalized.split("/"))
    return all(part not in {"", ".", ".."} and ":" not in part for part in parts)
