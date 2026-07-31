from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.hash_evidence import (
    CurrentReadHashEvidence,
    compatible_current_read_hashes,
)
from mediasync_home.application.plans import (
    MUTATING_OPERATION_TYPES,
    PlanDependency,
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.snapshots import SnapshotFileEntry
from mediasync_home.application.source_preconditions import SourceFilePrecondition


class InitialBackupPlanningError(ValueError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True, slots=True)
class InitialBackupPlanningEndpoint:
    endpoint_id: str
    endpoint_revision_id: str
    endpoint_generation: int
    snapshot_id: str
    snapshot_checksum: str
    root_case_context_hash: str
    root_case_mode: str
    capabilities_hash: str
    entries: tuple[SnapshotFileEntry, ...]
    role: PlanEndpointRole
    hash_evidence: tuple[CurrentReadHashEvidence, ...] = ()
    target_ordinal: int | None = None
    required_owner_installation_id: str | None = None
    required_ownership_epoch: int | None = None
    control_schema_version: int | None = None


@dataclass(frozen=True, slots=True)
class InitialBackupPlanBuild:
    plan: SealedPlan | None
    state: str
    reason_code: str
    next_action: str


@dataclass(frozen=True, slots=True)
class InitialBackupPlanMaterializationResult:
    job_id: str
    job_revision_id: str
    analysis_id: str | None
    plan_id: str | None
    plan_checksum: str | None
    state: str
    reason_code: str
    operation_count: int
    planned_bytes: int
    plan_runnable: bool
    idempotent_replay: bool
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "analysis_id": self.analysis_id,
            "plan_id": self.plan_id,
            "plan_checksum": self.plan_checksum,
            "state": self.state,
            "reason_code": self.reason_code,
            "operation_count": self.operation_count,
            "planned_bytes": self.planned_bytes,
            "plan_runnable": self.plan_runnable,
            "idempotent_replay": self.idempotent_replay,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class InitialBackupPlanRefreshReport:
    sealed_plan_count: int
    reused_plan_count: int
    no_changes_count: int
    blocked_job_count: int
    failed_job_count: int
    results: tuple[InitialBackupPlanMaterializationResult, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "sealed_plan_count": self.sealed_plan_count,
            "reused_plan_count": self.reused_plan_count,
            "no_changes_count": self.no_changes_count,
            "blocked_job_count": self.blocked_job_count,
            "failed_job_count": self.failed_job_count,
            "results": [result.to_dict() for result in self.results],
        }


class InitialBackupPlanIdFactory(Protocol):
    def new_initial_backup_plan_id(self) -> str: ...


class InitialBackupPlanRefresher(Protocol):
    def refresh_initial_backup_plans(
        self,
        *,
        observed_utc: str,
        job_id: str | None = None,
        force: bool = False,
    ) -> InitialBackupPlanRefreshReport: ...


def build_initial_backup_plan(
    *,
    plan_id: str,
    analysis_id: str,
    job_id: str,
    job_revision_id: str,
    endpoints: tuple[InitialBackupPlanningEndpoint, ...],
) -> InitialBackupPlanBuild:
    source = _single_endpoint(endpoints, PlanEndpointRole.SOURCE)
    targets = tuple(
        endpoint
        for endpoint in endpoints
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
    )
    if source is None:
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_REQUIRES_SINGLE_SOURCE",
            "Refresh the source snapshot before planning changes.",
        )
    if not targets:
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_REQUIRES_WRITABLE_TARGET",
            "Add and register at least one writable target before planning changes.",
        )

    operations: list[PlanOperation] = []
    dependencies: list[PlanDependency] = []
    plan_endpoints: list[PlanEndpoint] = [_plan_endpoint(source)]
    for target in sorted(
        targets,
        key=lambda endpoint: (
            -1 if endpoint.target_ordinal is None else endpoint.target_ordinal,
            endpoint.endpoint_id,
        ),
    ):
        target_operations, target_dependencies = _plan_target(
            plan_id=plan_id,
            source=source,
            target=target,
            first_sequence_no=len(operations) + 1,
        )
        operations.extend(target_operations)
        dependencies.extend(target_dependencies)
        mutating_operations = tuple(
            operation
            for operation in target_operations
            if operation.operation_type in MUTATING_OPERATION_TYPES
            and operation.risk_level is not PlanRiskLevel.BLOCKED
        )
        plan_endpoints.append(
            _plan_endpoint(
                target,
                planned_operations=len(mutating_operations),
                planned_bytes=sum(
                    operation.planned_bytes for operation in mutating_operations
                ),
            )
        )

    if not operations:
        return InitialBackupPlanBuild(
            plan=None,
            state="NO_CHANGES",
            reason_code="INITIAL_BACKUP_PLAN_NO_CHANGES",
            next_action="The source and target directory structures require no changes.",
        )

    plan = seal_plan(
        plan_id=plan_id,
        analysis_id=analysis_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        endpoints=tuple(plan_endpoints),
        operations=tuple(operations),
        dependencies=tuple(dependencies),
        execution_policy="MANUAL_REVIEW_REQUIRED",
    )
    blocked = plan.risk_summary.get("highest") == PlanRiskLevel.BLOCKED.value
    return InitialBackupPlanBuild(
        plan=plan,
        state="SEALED",
        reason_code=(
            "INITIAL_BACKUP_PLAN_BLOCKED"
            if blocked
            else "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW"
        ),
        next_action=(
            "Resolve blocked path or type conflicts and refresh the analysis."
            if blocked
            else "Review the sealed change plan before starting the backup."
        ),
    )


def endpoint_capabilities_hash(payload: dict[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def initial_backup_plan_runnable(plan: SealedPlan) -> bool:
    return plan.risk_summary.get("highest") != PlanRiskLevel.BLOCKED.value


def _plan_target(
    *,
    plan_id: str,
    source: InitialBackupPlanningEndpoint,
    target: InitialBackupPlanningEndpoint,
    first_sequence_no: int,
) -> tuple[tuple[PlanOperation, ...], tuple[PlanDependency, ...]]:
    if target.root_case_mode not in {"CASE_SENSITIVE", "CASE_INSENSITIVE"}:
        raise InitialBackupPlanningError(
            "INITIAL_BACKUP_PLAN_TARGET_CASE_CONTEXT_UNKNOWN",
            "Refresh the target snapshot case evidence before planning changes.",
        )
    source_entries = _entries_by_comparison_key(
        source.entries,
        role="SOURCE",
        target_case_mode=target.root_case_mode,
    )
    target_entries = _entries_by_comparison_key(
        target.entries,
        role="TARGET",
        target_case_mode=target.root_case_mode,
    )
    target_descendant_counts = _descendant_counts(
        target.entries,
        target_case_mode=target.root_case_mode,
    )
    source_hash_evidence = _hash_evidence_by_entry_id(source)
    target_hash_evidence = _hash_evidence_by_entry_id(target)
    operations: list[PlanOperation] = []
    directory_operations: dict[str, str] = {}
    for entry in sorted(
        source.entries,
        key=lambda item: (
            0 if item.object_type == "directory" else 1,
            item.relative_path.count("/"),
            item.comparison_key,
            item.relative_path,
        ),
    ):
        target_comparison_key = _target_comparison_key(
            entry.relative_path,
            target.root_case_mode,
        )
        operation = _plan_entry(
            plan_id=plan_id,
            target_endpoint_id=target.endpoint_id,
            target_ordinal=target.target_ordinal,
            source_entry=entry,
            target_entry=target_entries.get(target_comparison_key),
            source_snapshot_id=source.snapshot_id,
            target_snapshot_id=target.snapshot_id,
            source_hash_evidence=source_hash_evidence.get(entry.entry_id),
            target_hash_evidence=(
                None
                if target_entries.get(target_comparison_key) is None
                else target_hash_evidence.get(
                    target_entries[target_comparison_key].entry_id
                )
            ),
            target_comparison_key=target_comparison_key,
            target_descendant_count=target_descendant_counts.get(target_comparison_key, 0),
            sequence_no=first_sequence_no + len(operations),
        )
        if operation is None:
            continue
        operations.append(operation)
        if (
            operation.operation_type is PlanOperationType.CREATE_DIRECTORY
            and operation.risk_level is not PlanRiskLevel.BLOCKED
        ):
            directory_operations[target_comparison_key] = operation.operation_id
    dependencies = _directory_dependencies(
        source_entries=source_entries,
        operations=tuple(operations),
        directory_operations=directory_operations,
        target_case_mode=target.root_case_mode,
    )
    return tuple(operations), dependencies


def _single_endpoint(
    endpoints: tuple[InitialBackupPlanningEndpoint, ...],
    role: PlanEndpointRole,
) -> InitialBackupPlanningEndpoint | None:
    matches = tuple(endpoint for endpoint in endpoints if endpoint.role is role)
    return matches[0] if len(matches) == 1 else None


def _entries_by_comparison_key(
    entries: tuple[SnapshotFileEntry, ...],
    *,
    role: str,
    target_case_mode: str,
) -> dict[str, SnapshotFileEntry]:
    result: dict[str, SnapshotFileEntry] = {}
    for entry in entries:
        comparison_key = _target_comparison_key(
            entry.relative_path,
            target_case_mode,
        )
        if comparison_key in result:
            raise InitialBackupPlanningError(
                f"INITIAL_BACKUP_PLAN_{role}_CASE_COLLISION",
                "Resolve case-colliding paths before planning backup changes.",
            )
        result[comparison_key] = entry
    return result


def _descendant_counts(
    entries: tuple[SnapshotFileEntry, ...],
    *,
    target_case_mode: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for parent in entries:
        parent_key = _target_comparison_key(
            parent.relative_path,
            target_case_mode,
        )
        prefix = f"{parent_key}/"
        counts[parent_key] = sum(
            _target_comparison_key(
                child.relative_path,
                target_case_mode,
            ).startswith(prefix)
            for child in entries
        )
    return counts


def _plan_entry(
    *,
    plan_id: str,
    target_endpoint_id: str,
    target_ordinal: int | None,
    source_entry: SnapshotFileEntry,
    target_entry: SnapshotFileEntry | None,
    source_snapshot_id: str,
    target_snapshot_id: str,
    source_hash_evidence: CurrentReadHashEvidence | None,
    target_hash_evidence: CurrentReadHashEvidence | None,
    target_comparison_key: str,
    target_descendant_count: int,
    sequence_no: int,
) -> PlanOperation | None:
    if source_entry.object_type == "directory":
        if target_entry is None:
            return _operation(
                plan_id=plan_id,
                target_endpoint_id=target_endpoint_id,
                target_ordinal=target_ordinal,
                entry=source_entry,
                target_comparison_key=target_comparison_key,
                sequence_no=sequence_no,
                operation_type=PlanOperationType.CREATE_DIRECTORY,
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                reason_code="CREATE_MISSING_DIRECTORY",
                risk_level=PlanRiskLevel.LOW,
            )
        if target_entry.object_type == "directory":
            return None
        return _blocked_operation(
            plan_id=plan_id,
            target_endpoint_id=target_endpoint_id,
            target_ordinal=target_ordinal,
            entry=source_entry,
            target_comparison_key=target_comparison_key,
            sequence_no=sequence_no,
            reason_code="SOURCE_DIRECTORY_TARGET_TYPE_CONFLICT",
        )

    if source_entry.object_type != "file":
        return _blocked_operation(
            plan_id=plan_id,
            target_endpoint_id=target_endpoint_id,
            target_ordinal=target_ordinal,
            entry=source_entry,
            target_comparison_key=target_comparison_key,
            sequence_no=sequence_no,
            reason_code="SOURCE_ENTRY_TYPE_UNSUPPORTED",
        )
    if target_entry is None:
        return _operation(
            plan_id=plan_id,
            target_endpoint_id=target_endpoint_id,
            target_ordinal=target_ordinal,
            entry=source_entry,
            target_comparison_key=target_comparison_key,
            sequence_no=sequence_no,
            operation_type=PlanOperationType.COPY_NEW,
            target_precondition_kind=TargetPreconditionKind.ABSENT,
            reason_code="COPY_NEW",
            risk_level=PlanRiskLevel.LOW,
            planned_bytes=source_entry.size_bytes or 0,
            source_snapshot_id=source_snapshot_id,
        )
    if target_entry.object_type == "file":
        if (
            source_entry.size_bytes is not None
            and source_entry.size_bytes == target_entry.size_bytes
            and compatible_current_read_hashes(
                source_hash_evidence,
                target_hash_evidence,
                left_snapshot_id=source_snapshot_id,
                left_entry_id=source_entry.entry_id,
                right_snapshot_id=target_snapshot_id,
                right_entry_id=target_entry.entry_id,
                expected_size=source_entry.size_bytes,
            )
            and source_hash_evidence is not None
            and target_hash_evidence is not None
            and source_hash_evidence.content_hash
            == target_hash_evidence.content_hash
        ):
            return None
        return _operation(
            plan_id=plan_id,
            target_endpoint_id=target_endpoint_id,
            target_ordinal=target_ordinal,
            entry=source_entry,
            target_comparison_key=target_comparison_key,
            sequence_no=sequence_no,
            operation_type=PlanOperationType.COPY_NEW,
            target_precondition_kind=TargetPreconditionKind.MATCH_FINGERPRINT,
            reason_code="REPLACE_WITH_VERSION",
            risk_level=PlanRiskLevel.MEDIUM,
            planned_bytes=source_entry.size_bytes or 0,
            source_snapshot_id=source_snapshot_id,
        )
    if target_entry.object_type == "directory" and target_descendant_count == 0:
        return _operation(
            plan_id=plan_id,
            target_endpoint_id=target_endpoint_id,
            target_ordinal=target_ordinal,
            entry=source_entry,
            target_comparison_key=target_comparison_key,
            sequence_no=sequence_no,
            operation_type=PlanOperationType.COPY_NEW,
            target_precondition_kind=TargetPreconditionKind.DIRECTORY_EMPTY,
            reason_code="REPLACE_EMPTY_DIRECTORY_WITH_FILE",
            risk_level=PlanRiskLevel.HIGH,
            planned_bytes=source_entry.size_bytes or 0,
            source_snapshot_id=source_snapshot_id,
        )
    return _blocked_operation(
        plan_id=plan_id,
        target_endpoint_id=target_endpoint_id,
        target_ordinal=target_ordinal,
        entry=source_entry,
        target_comparison_key=target_comparison_key,
        sequence_no=sequence_no,
        reason_code="SOURCE_FILE_TARGET_TYPE_CONFLICT",
    )


def _operation(
    *,
    plan_id: str,
    target_endpoint_id: str,
    target_ordinal: int | None,
    entry: SnapshotFileEntry,
    target_comparison_key: str,
    sequence_no: int,
    operation_type: PlanOperationType,
    target_precondition_kind: TargetPreconditionKind,
    reason_code: str,
    risk_level: PlanRiskLevel,
    planned_bytes: int = 0,
    source_snapshot_id: str | None = None,
) -> PlanOperation:
    phase = 10 if operation_type is PlanOperationType.CREATE_DIRECTORY else 20
    target_order = -1 if target_ordinal is None else target_ordinal
    source_relative_path: str | None = None
    source_precondition_json: str | None = None
    if operation_type is PlanOperationType.COPY_NEW:
        if (
            source_snapshot_id is None
            or entry.size_bytes is None
            or entry.identity_fingerprint_hash is None
        ):
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_SOURCE_PRECONDITION_MISSING",
                "Refresh the source snapshot before planning file copies.",
            )
        source_relative_path = entry.relative_path
        source_precondition_json = SourceFilePrecondition(
            snapshot_id=source_snapshot_id,
            snapshot_entry_id=entry.entry_id,
            relative_path=entry.relative_path,
            size_bytes=entry.size_bytes,
            identity_fingerprint_hash=entry.identity_fingerprint_hash,
        ).to_json()
    return PlanOperation(
        operation_id=_operation_id(
            plan_id,
            target_endpoint_id,
            entry.relative_path,
            reason_code,
        ),
        operation_type=operation_type,
        sequence_no=sequence_no,
        execution_phase=phase,
        stable_order_key=(
            f"{phase:03d}:{target_order:04d}:{target_endpoint_id}:"
            f"{target_comparison_key}:{entry.relative_path}"
        ),
        target_precondition_kind=target_precondition_kind,
        reason_code=reason_code,
        risk_level=risk_level,
        target_endpoint_id=target_endpoint_id,
        target_relative_path=entry.relative_path,
        source_relative_path=source_relative_path,
        source_precondition_json=source_precondition_json,
        planned_bytes=planned_bytes,
    )


def _blocked_operation(
    *,
    plan_id: str,
    target_endpoint_id: str,
    target_ordinal: int | None,
    entry: SnapshotFileEntry,
    target_comparison_key: str,
    sequence_no: int,
    reason_code: str,
) -> PlanOperation:
    return _operation(
        plan_id=plan_id,
        target_endpoint_id=target_endpoint_id,
        target_ordinal=target_ordinal,
        entry=entry,
        target_comparison_key=target_comparison_key,
        sequence_no=sequence_no,
        operation_type=PlanOperationType.BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN,
        target_precondition_kind=TargetPreconditionKind.NONE,
        reason_code=reason_code,
        risk_level=PlanRiskLevel.BLOCKED,
    )


def _operation_id(
    plan_id: str,
    target_endpoint_id: str,
    relative_path: str,
    reason_code: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{plan_id}\0{target_endpoint_id}\0{relative_path}\0{reason_code}"
        ).encode("utf-8")
    ).hexdigest()
    return f"op-{digest[:24]}"


def _directory_dependencies(
    *,
    source_entries: dict[str, SnapshotFileEntry],
    operations: tuple[PlanOperation, ...],
    directory_operations: dict[str, str],
    target_case_mode: str,
) -> tuple[PlanDependency, ...]:
    dependencies: set[tuple[str, str]] = set()
    for operation in operations:
        path = operation.target_relative_path
        if path is None:
            continue
        parent_key = _parent_comparison_key(path, target_case_mode)
        while parent_key is not None:
            parent_operation_id = directory_operations.get(parent_key)
            if parent_operation_id is not None and parent_operation_id != operation.operation_id:
                dependencies.add((parent_operation_id, operation.operation_id))
                break
            parent_entry = source_entries.get(parent_key)
            parent_key = (
                None
                if parent_entry is None
                else _parent_comparison_key(
                    parent_entry.relative_path,
                    target_case_mode,
                )
            )
    return tuple(
        PlanDependency(
            before_operation_id=before,
            after_operation_id=after,
        )
        for before, after in sorted(dependencies)
    )


def _parent_comparison_key(
    relative_path: str,
    target_case_mode: str,
) -> str | None:
    parent_path = relative_path.rsplit("/", 1)[0] if "/" in relative_path else None
    if parent_path is None:
        return None
    return _target_comparison_key(parent_path, target_case_mode)


def _target_comparison_key(relative_path: str, target_case_mode: str) -> str:
    components = relative_path.replace("\\", "/").split("/")
    if target_case_mode == "CASE_INSENSITIVE":
        components = [component.casefold() for component in components]
    return "/".join(components)


def _hash_evidence_by_entry_id(
    endpoint: InitialBackupPlanningEndpoint,
) -> dict[str, CurrentReadHashEvidence]:
    result: dict[str, CurrentReadHashEvidence] = {}
    entry_ids = {entry.entry_id for entry in endpoint.entries}
    for evidence in endpoint.hash_evidence:
        if (
            evidence.snapshot_id != endpoint.snapshot_id
            or evidence.endpoint_id != endpoint.endpoint_id
            or evidence.entry_id not in entry_ids
            or evidence.entry_id in result
        ):
            raise InitialBackupPlanningError(
                "INITIAL_BACKUP_PLAN_HASH_EVIDENCE_INVALID",
                "Refresh current file hash evidence before planning changes.",
            )
        result[evidence.entry_id] = evidence
    return result


def _plan_endpoint(
    endpoint: InitialBackupPlanningEndpoint,
    *,
    planned_operations: int = 0,
    planned_bytes: int = 0,
) -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id=endpoint.endpoint_id,
        endpoint_revision_id=endpoint.endpoint_revision_id,
        endpoint_generation=endpoint.endpoint_generation,
        snapshot_id=endpoint.snapshot_id,
        role=endpoint.role,
        capabilities_hash=endpoint.capabilities_hash,
        root_case_context_hash=endpoint.root_case_context_hash,
        target_ordinal=endpoint.target_ordinal,
        required_owner_installation_id=endpoint.required_owner_installation_id,
        required_ownership_epoch=endpoint.required_ownership_epoch,
        control_schema_version=endpoint.control_schema_version,
        planned_operations=planned_operations,
        planned_bytes=planned_bytes,
    )
