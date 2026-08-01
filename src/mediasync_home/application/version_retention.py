from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from mediasync_home.application.runs import (
    EndpointLeaseAuthority,
    EndpointLeaseRequest,
    LiveEndpointLease,
)
from mediasync_home.domain.capabilities import MutationPermit


VERSION_RETENTION_PLAN_SCHEMA_VERSION = 1
VERSION_RETENTION_PLAN_HASH_ALGORITHM = "SHA-256"
VERSION_RETENTION_PLAN_CANONICALIZATION = "JSON_SORT_KEYS_COMPACT_UTF8_V1"
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RetainedVersionState(str, Enum):
    RETAINED = "RETAINED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"
    BLOCKED = "BLOCKED"


class VersionRetentionPlanState(str, Enum):
    PLANNED = "PLANNED"
    APPLYING = "APPLYING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class VersionRetentionItemState(str, Enum):
    PLANNED = "PLANNED"
    DELETE_INTENT_RECORDED = "DELETE_INTENT_RECORDED"
    FILESYSTEM_DELETED = "FILESYSTEM_DELETED"
    DELETED = "DELETED"
    BLOCKED = "BLOCKED"


class VersionRetentionError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass(frozen=True, slots=True)
class RetainedVersionRecord:
    version_object_id: str
    handoff_id: str
    run_id: str
    run_target_id: str
    operation_id: str
    job_id: str
    job_revision_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    endpoint_generation: int
    owner_installation_id: str
    ownership_epoch: int
    final_relative_path: str
    original_fingerprint_json: str
    created_utc: str
    retention_policy: str
    retention_until_utc: str
    manifest_hash: str
    state: RetainedVersionState
    row_version: int


@dataclass(frozen=True, slots=True)
class VersionRetentionPlan:
    plan_id: str
    cutoff_utc: str
    created_utc: str
    candidates: tuple[RetainedVersionRecord, ...]
    manifest_hash: str
    manifest_json: str
    state: VersionRetentionPlanState = VersionRetentionPlanState.PLANNED


@dataclass(frozen=True, slots=True)
class VersionRetentionExclusion:
    version_object_id: str
    validation_code: str


@dataclass(frozen=True, slots=True)
class VersionRetentionPlanningOutcome:
    plan: VersionRetentionPlan | None
    scanned: int
    excluded: tuple[VersionRetentionExclusion, ...]


@dataclass(frozen=True, slots=True)
class VersionRetentionWorkItem:
    plan_id: str
    plan_manifest_hash: str
    ordinal: int
    state: VersionRetentionItemState
    expected_object_row_version: int
    record: RetainedVersionRecord


@dataclass(frozen=True, slots=True)
class VersionRetentionDeleteReceipt:
    plan_id: str
    version_object_id: str
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class VersionRetentionApplyOutcome:
    idle: bool
    deleted: bool
    plan_id: str | None
    version_object_id: str | None
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True, slots=True)
class VersionRetentionMaintenanceOutcome:
    planning: VersionRetentionPlanningOutcome
    apply: VersionRetentionApplyOutcome


class VersionRetentionRecoveryReferencePort(Protocol):
    def released_reference_validation_code(
        self,
        record: RetainedVersionRecord,
    ) -> str | None: ...


class VersionRetentionPlanStore(Protocol):
    def list_due_retained_versions(
        self,
        *,
        cutoff_utc: str,
        limit: int,
    ) -> tuple[RetainedVersionRecord, ...]: ...

    def create_version_retention_plan(
        self,
        plan: VersionRetentionPlan,
    ) -> VersionRetentionPlan: ...


class VersionRetentionExecutionStore(Protocol):
    def load_next_version_retention_item(self) -> VersionRetentionWorkItem | None: ...

    def record_version_delete_intent(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
        event_utc: str,
    ) -> VersionRetentionWorkItem: ...

    def record_version_filesystem_deleted(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
        receipt: VersionRetentionDeleteReceipt,
        event_utc: str,
    ) -> VersionRetentionWorkItem: ...

    def complete_version_retention_item(
        self,
        *,
        item: VersionRetentionWorkItem,
        event_utc: str,
    ) -> VersionRetentionWorkItem: ...

    def block_version_retention_plan(
        self,
        *,
        item: VersionRetentionWorkItem,
        validation_code: str,
        event_utc: str,
    ) -> None: ...


class VersionRetentionStore(
    VersionRetentionPlanStore,
    VersionRetentionExecutionStore,
    Protocol,
):
    pass


class VersionRetentionPermitValidator(Protocol):
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None: ...


class VersionRetentionDeletionPort(Protocol):
    def verify_retained_version(
        self,
        *,
        permit_validator: VersionRetentionPermitValidator,
        permit: MutationPermit,
        item: VersionRetentionWorkItem,
    ) -> None: ...

    def delete_retained_version(
        self,
        *,
        permit_validator: VersionRetentionPermitValidator,
        permit: MutationPermit,
        item: VersionRetentionWorkItem,
        resuming_delete_intent: bool,
    ) -> VersionRetentionDeleteReceipt: ...


def plan_due_retained_versions(
    *,
    plan_id: str,
    cutoff_utc: str,
    created_utc: str,
    versions: VersionRetentionPlanStore,
    recovery_references: VersionRetentionRecoveryReferencePort,
    limit: int = 100,
) -> VersionRetentionPlanningOutcome:
    if not plan_id.strip():
        raise VersionRetentionError("VERSION_RETENTION_PLAN_ID_MISSING")
    if limit < 1 or limit > 1000:
        raise VersionRetentionError("VERSION_RETENTION_PLAN_LIMIT_INVALID")
    _require_utc(cutoff_utc)
    _require_utc(created_utc)
    due = versions.list_due_retained_versions(cutoff_utc=cutoff_utc, limit=limit)
    selected: list[RetainedVersionRecord] = []
    excluded: list[VersionRetentionExclusion] = []
    for record in due:
        _validate_retained_version_record(record)
        validation_code = recovery_references.released_reference_validation_code(record)
        if validation_code is None:
            selected.append(record)
        else:
            excluded.append(
                VersionRetentionExclusion(
                    version_object_id=record.version_object_id,
                    validation_code=validation_code,
                )
            )
    if not selected:
        return VersionRetentionPlanningOutcome(
            plan=None,
            scanned=len(due),
            excluded=tuple(excluded),
        )
    plan = create_version_retention_plan(
        plan_id=plan_id,
        cutoff_utc=cutoff_utc,
        created_utc=created_utc,
        candidates=tuple(selected),
    )
    recorded = versions.create_version_retention_plan(plan)
    if recorded != plan:
        raise VersionRetentionError("VERSION_RETENTION_PLAN_PERSISTENCE_MISMATCH")
    return VersionRetentionPlanningOutcome(
        plan=recorded,
        scanned=len(due),
        excluded=tuple(excluded),
    )


def maintain_version_retention(
    *,
    plan_id: str,
    event_utc: str,
    versions: VersionRetentionStore,
    recovery_references: VersionRetentionRecoveryReferencePort,
    leases: EndpointLeaseAuthority,
    deletion: VersionRetentionDeletionPort,
    limit: int = 100,
) -> VersionRetentionMaintenanceOutcome:
    current = apply_next_version_retention_item(
        versions=versions,
        recovery_references=recovery_references,
        leases=leases,
        deletion=deletion,
        event_utc=event_utc,
    )
    if not current.idle:
        return VersionRetentionMaintenanceOutcome(
            planning=VersionRetentionPlanningOutcome(
                plan=None,
                scanned=0,
                excluded=(),
            ),
            apply=current,
        )
    planning = plan_due_retained_versions(
        plan_id=plan_id,
        cutoff_utc=event_utc,
        created_utc=event_utc,
        versions=versions,
        recovery_references=recovery_references,
        limit=limit,
    )
    if planning.plan is None:
        return VersionRetentionMaintenanceOutcome(planning=planning, apply=current)
    return VersionRetentionMaintenanceOutcome(
        planning=planning,
        apply=apply_next_version_retention_item(
            versions=versions,
            recovery_references=recovery_references,
            leases=leases,
            deletion=deletion,
            event_utc=event_utc,
        ),
    )


def apply_next_version_retention_item(
    *,
    versions: VersionRetentionExecutionStore,
    recovery_references: VersionRetentionRecoveryReferencePort,
    leases: EndpointLeaseAuthority,
    deletion: VersionRetentionDeletionPort,
    event_utc: str,
) -> VersionRetentionApplyOutcome:
    _require_utc(event_utc)
    item = versions.load_next_version_retention_item()
    if item is None:
        return VersionRetentionApplyOutcome(
            idle=True,
            deleted=False,
            plan_id=None,
            version_object_id=None,
            validation_codes=(),
            next_action="No planned retained version is waiting for expiry.",
        )
    if item.state is VersionRetentionItemState.FILESYSTEM_DELETED:
        completed = versions.complete_version_retention_item(
            item=item,
            event_utc=event_utc,
        )
        return _deleted_outcome(completed)
    if item.state not in {
        VersionRetentionItemState.PLANNED,
        VersionRetentionItemState.DELETE_INTENT_RECORDED,
    }:
        return _blocked_outcome(
            item,
            "VERSION_RETENTION_ITEM_STATE_INVALID",
            "Reconcile the retention journal before retrying expiry.",
        )
    recovery_code = recovery_references.released_reference_validation_code(item.record)
    if recovery_code is not None:
        versions.block_version_retention_plan(
            item=item,
            validation_code=recovery_code,
            event_utc=event_utc,
        )
        return _blocked_outcome(
            item,
            recovery_code,
            "The retention plan was blocked because recovery still protects this version.",
        )
    attempt = leases.acquire_endpoint_lease(
        EndpointLeaseRequest(
            run_id=f"version-retention:{item.plan_id}",
            run_target_id=f"version-retention:{item.plan_id}:{item.ordinal}",
            endpoint_id=item.record.target_endpoint_id,
            endpoint_revision_id=item.record.target_endpoint_revision_id,
            resource_key=f"endpoint:{item.record.target_endpoint_id}",
            required_owner_installation_id=item.record.owner_installation_id,
            required_ownership_epoch=item.record.ownership_epoch,
        )
    )
    if not attempt.acquired or attempt.lease is None:
        return VersionRetentionApplyOutcome(
            idle=False,
            deleted=False,
            plan_id=item.plan_id,
            version_object_id=item.record.version_object_id,
            validation_codes=attempt.validation_codes
            or ("VERSION_RETENTION_ENDPOINT_LEASE_UNAVAILABLE",),
            next_action=attempt.next_action,
        )
    lease = attempt.lease
    try:
        validator = _permit_validator(lease)
        permit = lease.issue_mutation_permit()
        resuming = item.state is VersionRetentionItemState.DELETE_INTENT_RECORDED
        try:
            if not resuming:
                deletion.verify_retained_version(
                    permit_validator=validator,
                    permit=permit,
                    item=item,
                )
            item = versions.record_version_delete_intent(
                item=item,
                permit=permit,
                event_utc=event_utc,
            )
        except (ValueError, RuntimeError) as exc:
            validation_code = _error_code(exc)
            if not resuming:
                versions.block_version_retention_plan(
                    item=item,
                    validation_code=validation_code,
                    event_utc=event_utc,
                )
            return _blocked_outcome(
                item,
                validation_code,
                (
                    "The journaled deletion needs reconciliation before it can resume."
                    if resuming
                    else "The retention plan was blocked before filesystem deletion."
                ),
            )
        try:
            receipt = deletion.delete_retained_version(
                permit_validator=validator,
                permit=permit,
                item=item,
                resuming_delete_intent=resuming,
            )
            _validate_delete_receipt(item=item, receipt=receipt)
        except (ValueError, RuntimeError) as exc:
            validation_code = _error_code(exc)
            return _blocked_outcome(
                item,
                validation_code,
                "Retained-version deletion remains journaled and can be reconciled safely.",
            )
        deleted = versions.record_version_filesystem_deleted(
            item=item,
            permit=permit,
            receipt=receipt,
            event_utc=event_utc,
        )
        completed = versions.complete_version_retention_item(
            item=deleted,
            event_utc=event_utc,
        )
        return _deleted_outcome(completed)
    finally:
        lease.release()


def create_version_retention_plan(
    *,
    plan_id: str,
    cutoff_utc: str,
    created_utc: str,
    candidates: tuple[RetainedVersionRecord, ...],
) -> VersionRetentionPlan:
    if not plan_id.strip():
        raise VersionRetentionError("VERSION_RETENTION_PLAN_ID_MISSING")
    _require_utc(cutoff_utc)
    _require_utc(created_utc)
    if not candidates:
        raise VersionRetentionError("VERSION_RETENTION_PLAN_REQUIRES_CANDIDATES")
    ordered = tuple(sorted(candidates, key=lambda item: item.version_object_id))
    if len({item.version_object_id for item in ordered}) != len(ordered):
        raise VersionRetentionError("VERSION_RETENTION_PLAN_CANDIDATES_DUPLICATED")
    for record in ordered:
        _validate_retained_version_record(record)
        if record.state is not RetainedVersionState.RETAINED:
            raise VersionRetentionError("VERSION_RETENTION_PLAN_CANDIDATE_NOT_RETAINED")
        if record.retention_until_utc > cutoff_utc:
            raise VersionRetentionError("VERSION_RETENTION_PLAN_CANDIDATE_NOT_DUE")
    body: dict[str, object] = {
        "schema_version": VERSION_RETENTION_PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "cutoff_utc": cutoff_utc,
        "created_utc": created_utc,
        "candidate_count": len(ordered),
        "candidates": [
            {
                "version_object_id": item.version_object_id,
                "handoff_id": item.handoff_id,
                "run_id": item.run_id,
                "operation_id": item.operation_id,
                "job_id": item.job_id,
                "job_revision_id": item.job_revision_id,
                "target_endpoint_id": item.target_endpoint_id,
                "target_endpoint_revision_id": item.target_endpoint_revision_id,
                "endpoint_generation": item.endpoint_generation,
                "owner_installation_id": item.owner_installation_id,
                "ownership_epoch": item.ownership_epoch,
                "final_relative_path": item.final_relative_path,
                "retention_until_utc": item.retention_until_utc,
                "manifest_hash": item.manifest_hash,
                "expected_row_version": item.row_version,
            }
            for item in ordered
        ],
        "hash_algorithm": VERSION_RETENTION_PLAN_HASH_ALGORITHM,
        "canonicalization": VERSION_RETENTION_PLAN_CANONICALIZATION,
    }
    manifest_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    manifest = {**body, "manifest_hash": manifest_hash}
    return VersionRetentionPlan(
        plan_id=plan_id,
        cutoff_utc=cutoff_utc,
        created_utc=created_utc,
        candidates=ordered,
        manifest_hash=manifest_hash,
        manifest_json=_canonical_json(manifest),
    )


def _validate_retained_version_record(record: RetainedVersionRecord) -> None:
    values = (
        record.version_object_id,
        record.handoff_id,
        record.run_id,
        record.run_target_id,
        record.operation_id,
        record.job_id,
        record.job_revision_id,
        record.target_endpoint_id,
        record.target_endpoint_revision_id,
        record.owner_installation_id,
        record.final_relative_path,
        record.created_utc,
        record.retention_until_utc,
    )
    if not all(value.strip() for value in values):
        raise VersionRetentionError("RETAINED_VERSION_RECORD_IDENTIFIERS_INVALID")
    if record.endpoint_generation < 1 or record.ownership_epoch < 1 or record.row_version < 1:
        raise VersionRetentionError("RETAINED_VERSION_RECORD_NUMBERS_INVALID")
    if record.retention_policy != "THIRTY_DAYS":
        raise VersionRetentionError("RETAINED_VERSION_RECORD_POLICY_INVALID")
    if _HASH_PATTERN.fullmatch(record.manifest_hash) is None:
        raise VersionRetentionError("RETAINED_VERSION_RECORD_MANIFEST_HASH_INVALID")
    _require_utc(record.created_utc)
    _require_utc(record.retention_until_utc)


def _require_utc(value: str) -> None:
    if not value.endswith("Z") or len(value) < 20:
        raise VersionRetentionError("VERSION_RETENTION_TIME_INVALID")


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _permit_validator(lease: LiveEndpointLease) -> VersionRetentionPermitValidator:
    validator = getattr(lease, "assert_mutation_permit_current", None)
    if not callable(validator):
        raise VersionRetentionError("VERSION_RETENTION_LEASE_VALIDATOR_MISSING")
    return cast(VersionRetentionPermitValidator, lease)


def _validate_delete_receipt(
    *,
    item: VersionRetentionWorkItem,
    receipt: VersionRetentionDeleteReceipt,
) -> None:
    if (
        receipt.plan_id != item.plan_id
        or receipt.version_object_id != item.record.version_object_id
        or receipt.manifest_hash != item.record.manifest_hash
    ):
        raise VersionRetentionError("VERSION_RETENTION_DELETE_RECEIPT_MISMATCH")


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    if str(exc).strip():
        return str(exc)
    return type(exc).__name__


def _blocked_outcome(
    item: VersionRetentionWorkItem,
    validation_code: str,
    next_action: str,
) -> VersionRetentionApplyOutcome:
    return VersionRetentionApplyOutcome(
        idle=False,
        deleted=False,
        plan_id=item.plan_id,
        version_object_id=item.record.version_object_id,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _deleted_outcome(item: VersionRetentionWorkItem) -> VersionRetentionApplyOutcome:
    return VersionRetentionApplyOutcome(
        idle=False,
        deleted=True,
        plan_id=item.plan_id,
        version_object_id=item.record.version_object_id,
        validation_codes=(),
        next_action="Retained version expiry is filesystem-verified and journaled.",
    )
