from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping, Protocol

from mediasync_home.application.source_preconditions import (
    SourceFilePrecondition,
    SourceFilePreconditionError,
)


PLAN_SCHEMA_VERSION = 1
OPERATION_SCHEMA_VERSION = 4
PLANNER_VERSION = "0B-plan-sealer-skeleton"
PLAN_CHECKSUM_ALGORITHM = "SHA-256"
PLAN_SERIALIZER_VERSION = "0B-CANONICAL-JSON-V1"
MAX_PLAN_OPERATION_PAGE_LIMIT = 1000
MAX_PLAN_ENDPOINT_PAGE_LIMIT = 100


class PlanSealViolation(ValueError):
    pass


class PlanOperationType(str, Enum):
    COPY_NEW = "COPY_NEW"
    REPLACE_CHANGED = "REPLACE_CHANGED"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    SKIP_IDENTICAL = "SKIP_IDENTICAL"
    DEFER_AUTOMATION_POLICY = "DEFER_AUTOMATION_POLICY"
    BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN = "BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN"


class TargetPreconditionKind(str, Enum):
    ABSENT = "ABSENT"
    MATCH_FINGERPRINT = "MATCH_FINGERPRINT"
    DIRECTORY_EMPTY = "DIRECTORY_EMPTY"
    NONE = "NONE"


class PlanRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"


class PlanEndpointRole(str, Enum):
    SOURCE = "SOURCE"
    TARGET_WRITABLE = "TARGET_WRITABLE"
    TARGET_READONLY = "TARGET_READONLY"


MUTATING_OPERATION_TYPES = {
    PlanOperationType.COPY_NEW,
    PlanOperationType.CREATE_DIRECTORY,
}


@dataclass(frozen=True)
class PlanOperation:
    operation_id: str
    operation_type: PlanOperationType
    sequence_no: int
    execution_phase: int
    stable_order_key: str
    target_precondition_kind: TargetPreconditionKind
    reason_code: str
    risk_level: PlanRiskLevel
    target_endpoint_id: str | None = None
    target_relative_path: str | None = None
    source_relative_path: str | None = None
    source_precondition_json: str | None = None
    deferred_operation_type: PlanOperationType | None = None
    planned_bytes: int = 0


@dataclass(frozen=True)
class PlanDependency:
    before_operation_id: str
    after_operation_id: str


@dataclass(frozen=True)
class PlanEndpoint:
    endpoint_id: str
    endpoint_revision_id: str
    snapshot_id: str
    role: PlanEndpointRole
    capabilities_hash: str
    root_case_context_hash: str
    endpoint_generation: int
    target_ordinal: int | None = None
    required_owner_installation_id: str | None = None
    required_ownership_epoch: int | None = None
    control_schema_version: int | None = None
    planned_operations: int = 0
    planned_bytes: int = 0


@dataclass(frozen=True)
class PlanEndpointCursor:
    role: PlanEndpointRole
    target_ordinal: int | None
    endpoint_id: str


@dataclass(frozen=True)
class PlanEndpointPageQuery:
    plan_id: str
    limit: int
    after: PlanEndpointCursor | None = None


@dataclass(frozen=True)
class PlanEndpointReadModel:
    endpoint_id: str
    endpoint_revision_id: str
    snapshot_id: str
    role: PlanEndpointRole
    target_ordinal: int | None
    capabilities_hash: str
    root_case_context_hash: str
    endpoint_generation: int
    required_owner_installation_id: str | None
    required_ownership_epoch: int | None
    control_schema_version: int | None
    planned_operations: int
    planned_bytes: int


@dataclass(frozen=True)
class PlanEndpointPage:
    plan_id: str
    endpoints: tuple[PlanEndpointReadModel, ...]
    next_cursor: PlanEndpointCursor | None
    has_more: bool


@dataclass(frozen=True)
class PlanOperationCursor:
    execution_phase: int
    stable_order_key: str
    operation_id: str


@dataclass(frozen=True)
class PlanOperationPageQuery:
    plan_id: str
    limit: int
    after: PlanOperationCursor | None = None
    target_endpoint_id: str | None = None
    risk_levels: tuple[PlanRiskLevel, ...] = ()


@dataclass(frozen=True)
class PlanOperationReadModel:
    operation_id: str
    operation_type: PlanOperationType
    sequence_no: int
    execution_phase: int
    stable_order_key: str
    target_precondition_kind: TargetPreconditionKind
    reason_code: str
    risk_level: PlanRiskLevel
    target_relative_path: str | None
    planned_bytes: int
    target_endpoint_id: str | None = None
    deferred_operation_type: PlanOperationType | None = None


@dataclass(frozen=True)
class PlanOperationPage:
    plan_id: str
    operations: tuple[PlanOperationReadModel, ...]
    next_cursor: PlanOperationCursor | None
    has_more: bool
    risk_counts: Mapping[str, int] = field(default_factory=dict)
    highest_risk: PlanRiskLevel | None = None
    target_endpoint_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SealedPlan:
    plan_id: str
    analysis_id: str
    job_id: str
    job_revision_id: str
    endpoints: tuple[PlanEndpoint, ...]
    operations: tuple[PlanOperation, ...]
    dependencies: tuple[PlanDependency, ...]
    planner_version: str
    plan_schema_version: int
    operation_schema_version: int
    execution_policy: str
    checksum_algorithm: str
    serializer_version: str
    plan_checksum: str
    risk_summary: Mapping[str, object]
    operation_count: int
    planned_bytes: int
    immutable: bool = True
    parent_plan_id: str | None = None


class PlanStore(Protocol):
    def save_sealed_plan(self, plan: SealedPlan) -> None: ...

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None: ...


class PlanOperationReadModelStore(Protocol):
    def page_plan_operations(self, query: PlanOperationPageQuery) -> PlanOperationPage: ...


class PlanEndpointReadModelStore(Protocol):
    def page_plan_endpoints(self, query: PlanEndpointPageQuery) -> PlanEndpointPage: ...


def seal_plan(
    *,
    plan_id: str,
    analysis_id: str,
    job_id: str,
    job_revision_id: str,
    operations: tuple[PlanOperation, ...],
    endpoints: tuple[PlanEndpoint, ...] = (),
    dependencies: tuple[PlanDependency, ...] = (),
    parent_plan_id: str | None = None,
    execution_policy: str = "MANUAL_LOCAL_PREVIEW",
    risk_summary: Mapping[str, object] | None = None,
) -> SealedPlan:
    _validate_plan_identity(
        plan_id=plan_id,
        analysis_id=analysis_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        execution_policy=execution_policy,
    )
    ordered_endpoints = tuple(
        sorted(
            endpoints,
            key=lambda endpoint: (
                endpoint.role.value,
                -1 if endpoint.target_ordinal is None else endpoint.target_ordinal,
                endpoint.endpoint_id,
            ),
        )
    )
    bound_operations = _bind_single_target_operations(
        operations=operations,
        endpoints=ordered_endpoints,
    )
    ordered_operations = tuple(
        sorted(bound_operations, key=lambda operation: operation.sequence_no)
    )
    ordered_dependencies = tuple(
        sorted(
            dependencies,
            key=lambda dependency: (
                dependency.before_operation_id,
                dependency.after_operation_id,
            ),
        )
    )
    _validate_endpoints(ordered_endpoints)
    _validate_operations(ordered_operations, ordered_endpoints)
    _validate_dependencies(ordered_operations, ordered_dependencies)
    plan_risk_summary = dict(risk_summary or _risk_summary(ordered_operations))
    operation_count = len(ordered_operations)
    planned_bytes = sum(operation.planned_bytes for operation in ordered_operations)
    canonical_payload = _canonical_payload(
        plan_id=plan_id,
        analysis_id=analysis_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        parent_plan_id=parent_plan_id,
        planner_version=PLANNER_VERSION,
        plan_schema_version=PLAN_SCHEMA_VERSION,
        operation_schema_version=OPERATION_SCHEMA_VERSION,
        execution_policy=execution_policy,
        checksum_algorithm=PLAN_CHECKSUM_ALGORITHM,
        serializer_version=PLAN_SERIALIZER_VERSION,
        immutable=True,
        risk_summary=plan_risk_summary,
        endpoints=ordered_endpoints,
        operations=ordered_operations,
        dependencies=ordered_dependencies,
        operation_count=operation_count,
        planned_bytes=planned_bytes,
    )
    return SealedPlan(
        plan_id=plan_id,
        analysis_id=analysis_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        parent_plan_id=parent_plan_id,
        endpoints=ordered_endpoints,
        operations=ordered_operations,
        dependencies=ordered_dependencies,
        planner_version=PLANNER_VERSION,
        plan_schema_version=PLAN_SCHEMA_VERSION,
        operation_schema_version=OPERATION_SCHEMA_VERSION,
        execution_policy=execution_policy,
        checksum_algorithm=PLAN_CHECKSUM_ALGORITHM,
        serializer_version=PLAN_SERIALIZER_VERSION,
        plan_checksum=_checksum(canonical_payload),
        risk_summary=plan_risk_summary,
        operation_count=operation_count,
        planned_bytes=planned_bytes,
    )


def verify_plan_checksum(plan: SealedPlan) -> bool:
    canonical_payload = _canonical_payload(
        plan_id=plan.plan_id,
        analysis_id=plan.analysis_id,
        job_id=plan.job_id,
        job_revision_id=plan.job_revision_id,
        parent_plan_id=plan.parent_plan_id,
        planner_version=plan.planner_version,
        plan_schema_version=plan.plan_schema_version,
        operation_schema_version=plan.operation_schema_version,
        execution_policy=plan.execution_policy,
        checksum_algorithm=plan.checksum_algorithm,
        serializer_version=plan.serializer_version,
        immutable=plan.immutable,
        risk_summary=plan.risk_summary,
        endpoints=plan.endpoints,
        operations=plan.operations,
        dependencies=plan.dependencies,
        operation_count=plan.operation_count,
        planned_bytes=plan.planned_bytes,
    )
    return plan.plan_checksum == _checksum(canonical_payload)


def validate_plan_operation_page_query(query: PlanOperationPageQuery) -> None:
    if not query.plan_id.strip():
        raise PlanSealViolation("PLAN_OPERATION_READ_REQUIRES_PLAN_ID")
    if query.limit < 1:
        raise PlanSealViolation("PLAN_OPERATION_READ_LIMIT_MUST_BE_POSITIVE")
    if query.limit > MAX_PLAN_OPERATION_PAGE_LIMIT:
        raise PlanSealViolation("PLAN_OPERATION_READ_LIMIT_TOO_LARGE")
    if query.target_endpoint_id is not None and not query.target_endpoint_id.strip():
        raise PlanSealViolation("PLAN_OPERATION_READ_TARGET_MUST_NOT_BE_BLANK")
    if len(query.risk_levels) > len(PlanRiskLevel):
        raise PlanSealViolation("PLAN_OPERATION_READ_RISK_FILTER_TOO_LARGE")
    if len(set(query.risk_levels)) != len(query.risk_levels):
        raise PlanSealViolation("PLAN_OPERATION_READ_RISK_FILTER_DUPLICATED")
    if query.after is not None:
        if query.after.execution_phase < 0:
            raise PlanSealViolation("PLAN_OPERATION_READ_CURSOR_PHASE_MUST_BE_NON_NEGATIVE")
        if not query.after.stable_order_key.strip():
            raise PlanSealViolation("PLAN_OPERATION_READ_CURSOR_REQUIRES_STABLE_ORDER_KEY")
        if not query.after.operation_id.strip():
            raise PlanSealViolation("PLAN_OPERATION_READ_CURSOR_REQUIRES_OPERATION_ID")


def validate_plan_endpoint_page_query(query: PlanEndpointPageQuery) -> None:
    if not query.plan_id.strip():
        raise PlanSealViolation("PLAN_ENDPOINT_READ_REQUIRES_PLAN_ID")
    if query.limit < 1:
        raise PlanSealViolation("PLAN_ENDPOINT_READ_LIMIT_MUST_BE_POSITIVE")
    if query.limit > MAX_PLAN_ENDPOINT_PAGE_LIMIT:
        raise PlanSealViolation("PLAN_ENDPOINT_READ_LIMIT_TOO_LARGE")
    if query.after is None:
        return
    if query.after.target_ordinal is not None and query.after.target_ordinal < 0:
        raise PlanSealViolation("PLAN_ENDPOINT_READ_CURSOR_TARGET_ORDINAL_MUST_BE_NON_NEGATIVE")
    if not query.after.endpoint_id.strip():
        raise PlanSealViolation("PLAN_ENDPOINT_READ_CURSOR_REQUIRES_ENDPOINT_ID")


def _validate_plan_identity(
    *,
    plan_id: str,
    analysis_id: str,
    job_id: str,
    job_revision_id: str,
    execution_policy: str,
) -> None:
    for name, value in (
        ("plan_id", plan_id),
        ("analysis_id", analysis_id),
        ("job_id", job_id),
        ("job_revision_id", job_revision_id),
        ("execution_policy", execution_policy),
    ):
        if not value.strip():
            raise PlanSealViolation(f"PLAN_REQUIRES_{name.upper()}")


def _bind_single_target_operations(
    *,
    operations: tuple[PlanOperation, ...],
    endpoints: tuple[PlanEndpoint, ...],
) -> tuple[PlanOperation, ...]:
    writable_targets = tuple(
        endpoint
        for endpoint in endpoints
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
    )
    if len(writable_targets) != 1:
        return operations
    target_endpoint_id = writable_targets[0].endpoint_id
    return tuple(
        operation
        if operation.target_endpoint_id is not None
        else replace(operation, target_endpoint_id=target_endpoint_id)
        for operation in operations
    )


def _validate_operations(
    operations: tuple[PlanOperation, ...],
    endpoints: tuple[PlanEndpoint, ...],
) -> None:
    if not operations:
        raise PlanSealViolation("PLAN_REQUIRES_OPERATIONS")
    writable_endpoint_ids = {
        endpoint.endpoint_id
        for endpoint in endpoints
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
    }
    operation_ids: set[str] = set()
    sequence_numbers: set[int] = set()
    for operation in operations:
        if not operation.operation_id.strip():
            raise PlanSealViolation("PLAN_OPERATION_REQUIRES_ID")
        if operation.operation_id in operation_ids:
            raise PlanSealViolation("PLAN_OPERATION_IDS_MUST_BE_UNIQUE")
        operation_ids.add(operation.operation_id)
        if operation.sequence_no < 0:
            raise PlanSealViolation("PLAN_OPERATION_SEQUENCE_MUST_BE_NON_NEGATIVE")
        if operation.sequence_no in sequence_numbers:
            raise PlanSealViolation("PLAN_OPERATION_SEQUENCE_MUST_BE_UNIQUE")
        sequence_numbers.add(operation.sequence_no)
        if operation.execution_phase < 0:
            raise PlanSealViolation("PLAN_OPERATION_PHASE_MUST_BE_NON_NEGATIVE")
        if not operation.stable_order_key.strip():
            raise PlanSealViolation("PLAN_OPERATION_REQUIRES_STABLE_ORDER_KEY")
        if not operation.reason_code.strip():
            raise PlanSealViolation("PLAN_OPERATION_REQUIRES_REASON_CODE")
        if operation.planned_bytes < 0:
            raise PlanSealViolation("PLAN_OPERATION_BYTES_MUST_BE_NON_NEGATIVE")
        if operation.target_endpoint_id is not None:
            if not operation.target_endpoint_id.strip():
                raise PlanSealViolation("PLAN_OPERATION_TARGET_ENDPOINT_MUST_NOT_BE_BLANK")
            if operation.target_endpoint_id not in writable_endpoint_ids:
                raise PlanSealViolation("PLAN_OPERATION_TARGET_ENDPOINT_NOT_WRITABLE")
        elif operation.operation_type in MUTATING_OPERATION_TYPES and writable_endpoint_ids:
            raise PlanSealViolation("MUTATING_PLAN_OPERATION_REQUIRES_TARGET_ENDPOINT")
        _validate_deferred_operation(operation)
        _validate_target_precondition(operation)
        _validate_source_precondition(operation)


def _validate_endpoints(endpoints: tuple[PlanEndpoint, ...]) -> None:
    endpoint_ids: set[str] = set()
    target_ordinals: set[int] = set()
    for endpoint in endpoints:
        _validate_endpoint_text(endpoint.endpoint_id, "PLAN_ENDPOINT_REQUIRES_ID")
        _validate_endpoint_text(endpoint.endpoint_revision_id, "PLAN_ENDPOINT_REQUIRES_REVISION_ID")
        _validate_endpoint_text(endpoint.snapshot_id, "PLAN_ENDPOINT_REQUIRES_SNAPSHOT_ID")
        _validate_endpoint_text(endpoint.capabilities_hash, "PLAN_ENDPOINT_REQUIRES_CAPABILITIES_HASH")
        _validate_endpoint_text(endpoint.root_case_context_hash, "PLAN_ENDPOINT_REQUIRES_CASE_CONTEXT_HASH")
        if endpoint.endpoint_id in endpoint_ids:
            raise PlanSealViolation("PLAN_ENDPOINT_IDS_MUST_BE_UNIQUE")
        endpoint_ids.add(endpoint.endpoint_id)
        if endpoint.planned_operations < 0:
            raise PlanSealViolation("PLAN_ENDPOINT_OPERATIONS_MUST_BE_NON_NEGATIVE")
        if endpoint.planned_bytes < 0:
            raise PlanSealViolation("PLAN_ENDPOINT_BYTES_MUST_BE_NON_NEGATIVE")
        if endpoint.endpoint_generation < 1:
            raise PlanSealViolation("PLAN_ENDPOINT_REQUIRES_GENERATION")
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE:
            _validate_writable_target_endpoint(endpoint, target_ordinals)
        elif endpoint.target_ordinal is not None and endpoint.target_ordinal < 0:
            raise PlanSealViolation("PLAN_ENDPOINT_TARGET_ORDINAL_MUST_BE_NON_NEGATIVE")


def _validate_writable_target_endpoint(
    endpoint: PlanEndpoint,
    target_ordinals: set[int],
) -> None:
    if endpoint.target_ordinal is None or endpoint.target_ordinal < 0:
        raise PlanSealViolation("WRITABLE_PLAN_TARGET_REQUIRES_TARGET_ORDINAL")
    if endpoint.target_ordinal in target_ordinals:
        raise PlanSealViolation("PLAN_TARGET_ORDINALS_MUST_BE_UNIQUE")
    target_ordinals.add(endpoint.target_ordinal)
    _validate_endpoint_text(
        endpoint.required_owner_installation_id,
        "WRITABLE_PLAN_TARGET_REQUIRES_OWNER",
    )
    if endpoint.required_ownership_epoch is None or endpoint.required_ownership_epoch < 1:
        raise PlanSealViolation("WRITABLE_PLAN_TARGET_REQUIRES_OWNERSHIP_EPOCH")
    if endpoint.control_schema_version is None or endpoint.control_schema_version < 1:
        raise PlanSealViolation("WRITABLE_PLAN_TARGET_REQUIRES_CONTROL_SCHEMA_VERSION")


def _validate_endpoint_text(value: str | None, reason: str) -> None:
    if value is None or not value.strip():
        raise PlanSealViolation(reason)


def _validate_target_precondition(operation: PlanOperation) -> None:
    requires_target_precondition = (
        operation.operation_type in MUTATING_OPERATION_TYPES
        or operation.operation_type is PlanOperationType.DEFER_AUTOMATION_POLICY
    )
    if requires_target_precondition:
        if operation.target_precondition_kind is TargetPreconditionKind.NONE:
            raise PlanSealViolation("MUTATING_PLAN_OPERATION_REQUIRES_TARGET_PRECONDITION")
        if operation.target_relative_path is None or not operation.target_relative_path.strip():
            raise PlanSealViolation("MUTATING_PLAN_OPERATION_REQUIRES_TARGET_RELATIVE_PATH")
    if operation.target_relative_path is not None and _looks_absolute_path(operation.target_relative_path):
        raise PlanSealViolation("PLAN_OPERATION_TARGET_PATH_MUST_BE_RELATIVE")


def _validate_source_precondition(operation: PlanOperation) -> None:
    effective_type = (
        operation.deferred_operation_type
        if operation.operation_type is PlanOperationType.DEFER_AUTOMATION_POLICY
        else operation.operation_type
    )
    if effective_type not in {
        PlanOperationType.COPY_NEW,
        PlanOperationType.REPLACE_CHANGED,
    }:
        if operation.source_relative_path is not None or operation.source_precondition_json is not None:
            raise PlanSealViolation("NONCOPY_PLAN_OPERATION_HAS_SOURCE_PRECONDITION")
        return
    if operation.source_relative_path is None or not operation.source_relative_path.strip():
        raise PlanSealViolation("COPY_PLAN_OPERATION_REQUIRES_SOURCE_PATH")
    if _looks_absolute_path(operation.source_relative_path):
        raise PlanSealViolation("PLAN_OPERATION_SOURCE_PATH_MUST_BE_RELATIVE")
    try:
        precondition = SourceFilePrecondition.from_json(operation.source_precondition_json)
    except SourceFilePreconditionError as exc:
        raise PlanSealViolation(exc.validation_code) from exc
    if (
        precondition.relative_path != operation.source_relative_path
        or precondition.size_bytes != operation.planned_bytes
    ):
        raise PlanSealViolation("PLAN_OPERATION_SOURCE_PRECONDITION_MISMATCH")


def _validate_deferred_operation(operation: PlanOperation) -> None:
    if operation.operation_type is PlanOperationType.DEFER_AUTOMATION_POLICY:
        if operation.deferred_operation_type not in {
            PlanOperationType.COPY_NEW,
            PlanOperationType.REPLACE_CHANGED,
            PlanOperationType.CREATE_DIRECTORY,
        }:
            raise PlanSealViolation("DEFERRED_PLAN_OPERATION_TYPE_INVALID")
        return
    if operation.deferred_operation_type is not None:
        raise PlanSealViolation("NONDEFERRED_PLAN_OPERATION_HAS_DEFERRED_TYPE")


def _looks_absolute_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("/") or ":" in normalized.split("/", 1)[0]


def _validate_dependencies(
    operations: tuple[PlanOperation, ...],
    dependencies: tuple[PlanDependency, ...],
) -> None:
    operation_ids = {operation.operation_id for operation in operations}
    edges: dict[str, set[str]] = {operation_id: set() for operation_id in operation_ids}
    for dependency in dependencies:
        if dependency.before_operation_id == dependency.after_operation_id:
            raise PlanSealViolation("PLAN_DEPENDENCY_CANNOT_REFERENCE_SELF")
        if dependency.before_operation_id not in operation_ids or dependency.after_operation_id not in operation_ids:
            raise PlanSealViolation("PLAN_DEPENDENCY_REQUIRES_EXISTING_OPERATIONS")
        edges[dependency.before_operation_id].add(dependency.after_operation_id)
    _reject_dependency_cycles(edges)


def _reject_dependency_cycles(edges: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(operation_id: str) -> None:
        if operation_id in visited:
            return
        if operation_id in visiting:
            raise PlanSealViolation("PLAN_DEPENDENCIES_MUST_BE_ACYCLIC")
        visiting.add(operation_id)
        for after_operation_id in edges[operation_id]:
            visit(after_operation_id)
        visiting.remove(operation_id)
        visited.add(operation_id)

    for operation_id in edges:
        visit(operation_id)


def _risk_summary(operations: tuple[PlanOperation, ...]) -> dict[str, object]:
    counts: dict[str, int] = {risk.value: 0 for risk in PlanRiskLevel}
    for operation in operations:
        counts[operation.risk_level.value] += 1
    highest = PlanRiskLevel.LOW.value
    for risk in (
        PlanRiskLevel.BLOCKED,
        PlanRiskLevel.HIGH,
        PlanRiskLevel.MEDIUM,
        PlanRiskLevel.LOW,
    ):
        if counts[risk.value]:
            highest = risk.value
            break
    return {"counts": counts, "highest": highest}


def _canonical_payload(
    *,
    plan_id: str,
    analysis_id: str,
    job_id: str,
    job_revision_id: str,
    parent_plan_id: str | None,
    planner_version: str,
    plan_schema_version: int,
    operation_schema_version: int,
    execution_policy: str,
    checksum_algorithm: str,
    serializer_version: str,
    immutable: bool,
    risk_summary: Mapping[str, object],
    endpoints: tuple[PlanEndpoint, ...],
    operations: tuple[PlanOperation, ...],
    dependencies: tuple[PlanDependency, ...],
    operation_count: int,
    planned_bytes: int,
) -> dict[str, object]:
    return {
        "analysis_id": analysis_id,
        "checksum_algorithm": checksum_algorithm,
        "dependencies": [
            {
                "after_operation_id": dependency.after_operation_id,
                "before_operation_id": dependency.before_operation_id,
            }
            for dependency in dependencies
        ],
        "execution_policy": execution_policy,
        "immutable": immutable,
        "job_id": job_id,
        "job_revision_id": job_revision_id,
        "operation_count": operation_count,
        "operation_schema_version": operation_schema_version,
        "endpoints": [_endpoint_payload(endpoint) for endpoint in endpoints],
        "operations": [
            _operation_payload(
                operation,
                include_target_endpoint=operation_schema_version >= 2,
                include_source_precondition=operation_schema_version >= 3,
                include_deferred_operation_type=operation_schema_version >= 4,
            )
            for operation in operations
        ],
        "parent_plan_id": parent_plan_id,
        "planned_bytes": planned_bytes,
        "plan_id": plan_id,
        "plan_schema_version": plan_schema_version,
        "planner_version": planner_version,
        "risk_summary": risk_summary,
        "serializer_version": serializer_version,
    }


def _operation_payload(
    operation: PlanOperation,
    *,
    include_target_endpoint: bool,
    include_source_precondition: bool,
    include_deferred_operation_type: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "execution_phase": operation.execution_phase,
        "operation_id": operation.operation_id,
        "operation_type": operation.operation_type.value,
        "planned_bytes": operation.planned_bytes,
        "reason_code": operation.reason_code,
        "risk_level": operation.risk_level.value,
        "sequence_no": operation.sequence_no,
        "stable_order_key": operation.stable_order_key,
        "target_precondition_kind": operation.target_precondition_kind.value,
        "target_relative_path": operation.target_relative_path,
    }
    if include_target_endpoint:
        payload["target_endpoint_id"] = operation.target_endpoint_id
    if include_source_precondition:
        payload["source_relative_path"] = operation.source_relative_path
        payload["source_precondition_json"] = operation.source_precondition_json
    if include_deferred_operation_type:
        payload["deferred_operation_type"] = (
            None
            if operation.deferred_operation_type is None
            else operation.deferred_operation_type.value
        )
    return payload


def _endpoint_payload(endpoint: PlanEndpoint) -> dict[str, object]:
    return {
        "capabilities_hash": endpoint.capabilities_hash,
        "control_schema_version": endpoint.control_schema_version,
        "endpoint_id": endpoint.endpoint_id,
        "endpoint_generation": endpoint.endpoint_generation,
        "endpoint_revision_id": endpoint.endpoint_revision_id,
        "planned_bytes": endpoint.planned_bytes,
        "planned_operations": endpoint.planned_operations,
        "required_owner_installation_id": endpoint.required_owner_installation_id,
        "required_ownership_epoch": endpoint.required_ownership_epoch,
        "role": endpoint.role.value,
        "root_case_context_hash": endpoint.root_case_context_hash,
        "snapshot_id": endpoint.snapshot_id,
        "target_ordinal": endpoint.target_ordinal,
    }


def _checksum(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
