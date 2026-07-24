from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.recovery_operations import RecoveryOperation, RecoveryOperationPhase
from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    StartedRunTarget,
    complete_run_target_success,
)


MAX_RECOVERY_RESUME_STARTUP_LIMIT = 1000


class RecoveryResumeViolation(ValueError):
    pass


class RecoveryResumeAction(str, Enum):
    TARGET_COMPLETED = "TARGET_COMPLETED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RecoveryResumeStartupRequest:
    reconciler_instance_id: str
    limit: int = MAX_RECOVERY_RESUME_STARTUP_LIMIT


@dataclass(frozen=True)
class RecoveryResumeFinding:
    action: RecoveryResumeAction
    run_id: str
    run_target_id: str
    operation_ids: tuple[str, ...]
    validation_codes: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class RecoveryResumeStartupReport:
    reconciler_instance_id: str
    scanned: int
    findings: tuple[RecoveryResumeFinding, ...]

    @property
    def completed_run_target_ids(self) -> tuple[str, ...]:
        return tuple(
            finding.run_target_id
            for finding in self.findings
            if finding.action is RecoveryResumeAction.TARGET_COMPLETED
        )

    @property
    def blocked_run_target_ids(self) -> tuple[str, ...]:
        return tuple(
            finding.run_target_id
            for finding in self.findings
            if finding.action is RecoveryResumeAction.BLOCKED
        )


class RecoveryResumeOperationStore(Protocol):
    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


def resume_catalog_recorded_run_targets_after_startup(
    request: RecoveryResumeStartupRequest,
    *,
    runs: RunStore,
    recovery_operations: RecoveryResumeOperationStore,
) -> RecoveryResumeStartupReport:
    validate_recovery_resume_startup_request(request)

    scanned_operations = recovery_operations.list_operations_in_phase(
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        limit=request.limit,
    )
    findings: list[RecoveryResumeFinding] = []
    seen_targets: set[tuple[str, str]] = set()
    for operation in scanned_operations:
        key = (operation.run_id, operation.run_target_id)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        findings.append(
            _resume_catalog_recorded_target(
                operation=operation,
                runs=runs,
                recovery_operations=recovery_operations,
            )
        )

    return RecoveryResumeStartupReport(
        reconciler_instance_id=request.reconciler_instance_id,
        scanned=len(scanned_operations),
        findings=tuple(findings),
    )


def validate_recovery_resume_startup_request(request: RecoveryResumeStartupRequest) -> None:
    if not request.reconciler_instance_id.strip():
        raise RecoveryResumeViolation("RECOVERY_RESUME_REQUIRES_RECONCILER")
    if request.limit < 1:
        raise RecoveryResumeViolation("RECOVERY_RESUME_LIMIT_MUST_BE_POSITIVE")
    if request.limit > MAX_RECOVERY_RESUME_STARTUP_LIMIT:
        raise RecoveryResumeViolation("RECOVERY_RESUME_LIMIT_TOO_LARGE")


def _resume_catalog_recorded_target(
    *,
    operation: RecoveryOperation,
    runs: RunStore,
    recovery_operations: RecoveryResumeOperationStore,
) -> RecoveryResumeFinding:
    run = runs.load_started_run(operation.run_id)
    if run is None:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_RUN_NOT_FOUND",
            next_action="Keep recovery mode active until the catalog run row is restored.",
        )
    target = next(
        (item for item in run.targets if item.run_target_id == operation.run_target_id),
        None,
    )
    if target is None:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_RUN_TARGET_NOT_FOUND",
            next_action="Keep recovery mode active until the catalog run target row is restored.",
        )
    if run.state in {
        RunState.COMPLETED,
        RunState.COMPLETED_WITH_WARNINGS,
    } or target.state in {
        RunTargetState.SUCCEEDED,
        RunTargetState.SUCCEEDED_WITH_WARNINGS,
    }:
        return RecoveryResumeFinding(
            action=RecoveryResumeAction.ALREADY_TERMINAL,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_ids=(operation.operation_id,),
            validation_codes=(),
            next_action="Catalog-recorded recovery target is already terminal.",
        )
    if run.state is not RunState.EXECUTING or target.state is not RunTargetState.EXECUTING:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_TARGET_NOT_EXECUTING",
            next_action="Review run state before completing catalog-recorded recovery work.",
        )

    cataloged_operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        limit=max(target.planned_operations + 1, 1),
    )
    operation_ids = tuple(item.operation_id for item in cataloged_operations)
    if len(cataloged_operations) != target.planned_operations:
        return RecoveryResumeFinding(
            action=RecoveryResumeAction.BLOCKED,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_ids=operation_ids,
            validation_codes=("RECOVERY_RESUME_CATALOG_RECORDED_COUNT_MISMATCH",),
            next_action="Reconcile remaining recovery operations before completing the run target.",
        )
    mismatch = next(
        (
            item
            for item in cataloged_operations
            if _operation_target_binding_mismatch(operation=item, target=target)
        ),
        None,
    )
    if mismatch is not None:
        return RecoveryResumeFinding(
            action=RecoveryResumeAction.BLOCKED,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_ids=operation_ids,
            validation_codes=("RECOVERY_RESUME_OPERATION_TARGET_BINDING_MISMATCH",),
            next_action="Reconcile lease and ownership evidence before completing the run target.",
        )

    completed_bytes = sum(_operation_byte_count(item) for item in cataloged_operations)
    outcome = complete_run_target_success(
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        runs=runs,
        completed_operations=len(cataloged_operations),
        completed_bytes=completed_bytes,
    )
    if not outcome.completed:
        return RecoveryResumeFinding(
            action=RecoveryResumeAction.BLOCKED,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_ids=operation_ids,
            validation_codes=outcome.validation_codes,
            next_action=outcome.next_action,
        )
    return RecoveryResumeFinding(
        action=RecoveryResumeAction.TARGET_COMPLETED,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_ids=operation_ids,
        validation_codes=(),
        next_action=outcome.next_action,
    )


def _operation_target_binding_mismatch(
    *,
    operation: RecoveryOperation,
    target: StartedRunTarget,
) -> bool:
    return (
        operation.phase is not RecoveryOperationPhase.CATALOG_RECORDED
        or operation.target_endpoint_id != target.endpoint_id
        or operation.target_endpoint_revision_id != target.endpoint_revision_id
        or operation.lease_resource_key != target.lease_resource_key
        or operation.lease_id != target.last_lease_id
        or operation.ownership_epoch != target.last_ownership_epoch
        or operation.fencing_token != target.last_fencing_token
        or target.required_owner_installation_id
        not in (None, operation.owner_installation_id)
        or target.required_ownership_epoch not in (None, operation.ownership_epoch)
    )


def _operation_byte_count(operation: RecoveryOperation) -> int:
    for raw_payload in (
        operation.expected_final_fingerprint_json,
        operation.expected_staging_fingerprint_json,
    ):
        if raw_payload is None:
            continue
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        byte_count = payload.get("byte_count")
        if isinstance(byte_count, int) and byte_count >= 0:
            return byte_count
    return 0


def _blocked(
    *,
    operation: RecoveryOperation,
    validation_code: str,
    next_action: str,
) -> RecoveryResumeFinding:
    return RecoveryResumeFinding(
        action=RecoveryResumeAction.BLOCKED,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_ids=(operation.operation_id,),
        validation_codes=(validation_code,),
        next_action=next_action,
    )
