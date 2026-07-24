from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from mediasync_home.application.catalog_handoff import (
    CatalogHandoffError,
    FinalFileCatalogHandoffStore,
    record_catalog_handoff_after_final_verification,
)
from mediasync_home.application.ports import FinalArtifactVerificationEvidence
from mediasync_home.application.recovery_operations import RecoveryOperation, RecoveryOperationPhase
from mediasync_home.application.recovery_operations import RecoveryOperationMetadata, RecoveryOperationStore
from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    StartedRunTarget,
    complete_run_target_success,
)


MAX_RECOVERY_RESUME_STARTUP_LIMIT = 1000
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RecoveryResumeViolation(ValueError):
    pass


class RecoveryResumeAction(str, Enum):
    FINAL_REVERIFIED = "FINAL_REVERIFIED"
    CATALOG_HANDOFF_RECORDED = "CATALOG_HANDOFF_RECORDED"
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


class RecoveryResumeCatalogHandoffOperationStore(
    RecoveryResumeOperationStore,
    RecoveryOperationStore,
    Protocol,
):
    pass


class FinalArtifactVerificationPort(Protocol):
    def verify_final_artifact(
        self,
        operation: RecoveryOperation,
    ) -> FinalArtifactVerificationEvidence: ...


def resume_recovery_operations_after_startup(
    request: RecoveryResumeStartupRequest,
    *,
    runs: RunStore,
    recovery_operations: RecoveryResumeCatalogHandoffOperationStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    final_verifier: FinalArtifactVerificationPort | None = None,
) -> RecoveryResumeStartupReport:
    validate_recovery_resume_startup_request(request)

    findings: list[RecoveryResumeFinding] = []
    scanned = 0
    remaining = request.limit
    if final_verifier is not None:
        for phase in (
            RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
            RecoveryOperationPhase.FILESYSTEM_APPLIED,
            RecoveryOperationPhase.FINAL_DURABLE,
        ):
            if remaining < 1:
                break
            operations = recovery_operations.list_operations_in_phase(
                phase=phase,
                limit=remaining,
            )
            scanned += len(operations)
            remaining -= len(operations)
            for operation in operations:
                findings.append(
                    _resume_final_filesystem_verification(
                        operation=operation,
                        runs=runs,
                        recovery_operations=recovery_operations,
                        final_verifier=final_verifier,
                        process_instance_id=request.reconciler_instance_id,
                    )
                )

    if remaining < 1:
        return RecoveryResumeStartupReport(
            reconciler_instance_id=request.reconciler_instance_id,
            scanned=scanned,
            findings=tuple(findings),
        )

    final_verified_operations = recovery_operations.list_operations_in_phase(
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        limit=remaining,
    )
    scanned += len(final_verified_operations)
    remaining -= len(final_verified_operations)
    for operation in final_verified_operations:
        findings.append(
            _resume_final_verified_catalog_handoff(
                operation=operation,
                runs=runs,
                recovery_operations=recovery_operations,
                catalog_handoffs=catalog_handoffs,
                process_instance_id=request.reconciler_instance_id,
            )
        )

    if remaining > 0:
        catalog_recorded_report = resume_catalog_recorded_run_targets_after_startup(
            RecoveryResumeStartupRequest(
                reconciler_instance_id=request.reconciler_instance_id,
                limit=remaining,
            ),
            runs=runs,
            recovery_operations=recovery_operations,
        )
        scanned += catalog_recorded_report.scanned
        findings.extend(catalog_recorded_report.findings)

    return RecoveryResumeStartupReport(
        reconciler_instance_id=request.reconciler_instance_id,
        scanned=scanned,
        findings=tuple(findings),
    )


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
            if _operation_target_binding_mismatch(
                operation=item,
                target=target,
                expected_phase=RecoveryOperationPhase.CATALOG_RECORDED,
            )
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


def _resume_final_filesystem_verification(
    *,
    operation: RecoveryOperation,
    runs: RunStore,
    recovery_operations: RecoveryResumeCatalogHandoffOperationStore,
    final_verifier: FinalArtifactVerificationPort,
    process_instance_id: str,
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
    if run.state is not RunState.EXECUTING or target.state is not RunTargetState.EXECUTING:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_TARGET_NOT_EXECUTING",
            next_action="Review run state before re-verifying final filesystem evidence.",
        )
    if operation.phase not in {
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
        RecoveryOperationPhase.FINAL_DURABLE,
    }:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_FINAL_REVERIFY_PHASE_UNSUPPORTED",
            next_action="Use the recovery resume phase handler matching the operation state.",
        )
    if _operation_target_binding_mismatch(
        operation=operation,
        target=target,
        expected_phase=operation.phase,
    ):
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_OPERATION_TARGET_BINDING_MISMATCH",
            next_action="Reconcile lease and ownership evidence before re-verifying final state.",
        )

    try:
        evidence = final_verifier.verify_final_artifact(operation)
        verified = _transition_to_final_verified(
            operation=operation,
            recovery_operations=recovery_operations,
            process_instance_id=process_instance_id,
            evidence=evidence,
        )
    except (ValueError, RuntimeError) as exc:
        return RecoveryResumeFinding(
            action=RecoveryResumeAction.BLOCKED,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_ids=(operation.operation_id,),
            validation_codes=(_exception_validation_code(exc),),
            next_action=_exception_next_action(
                exc,
                "Reverify final filesystem state before retrying startup recovery resume.",
            ),
        )

    if verified is None:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_FINAL_REVERIFY_PHASE_CONFLICT",
            next_action="Reload recovery state before retrying final verification.",
        )
    return RecoveryResumeFinding(
        action=RecoveryResumeAction.FINAL_REVERIFIED,
        run_id=verified.run_id,
        run_target_id=verified.run_target_id,
        operation_ids=(verified.operation_id,),
        validation_codes=(),
        next_action="Final filesystem state is reverified; continue catalog handoff recovery.",
    )


def _transition_to_final_verified(
    *,
    operation: RecoveryOperation,
    recovery_operations: RecoveryResumeCatalogHandoffOperationStore,
    process_instance_id: str,
    evidence: FinalArtifactVerificationEvidence,
) -> RecoveryOperation | None:
    current = operation
    if current.phase is RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED:
        applied = recovery_operations.record_operation_phase_transition(
            run_id=current.run_id,
            operation_id=current.operation_id,
            expected_phase=current.phase,
            next_phase=RecoveryOperationPhase.FILESYSTEM_APPLIED,
            process_instance_id=process_instance_id,
            payload={
                "final_relative_path": current.final_relative_path,
                "resume_reason": "STARTUP_FINAL_ARTIFACT_REVERIFIED_AFTER_PRECONDITION",
            },
        )
        if applied is None:
            return None
        current = applied

    if current.phase is RecoveryOperationPhase.FILESYSTEM_APPLIED:
        durable = recovery_operations.record_operation_phase_transition(
            run_id=current.run_id,
            operation_id=current.operation_id,
            expected_phase=current.phase,
            next_phase=RecoveryOperationPhase.FINAL_DURABLE,
            process_instance_id=process_instance_id,
            payload={"durability_state": "STARTUP_FINAL_REVERIFY"},
        )
        if durable is None:
            return None
        current = durable

    return recovery_operations.record_operation_phase_transition(
        run_id=current.run_id,
        operation_id=current.operation_id,
        expected_phase=current.phase,
        next_phase=RecoveryOperationPhase.FINAL_VERIFIED,
        process_instance_id=process_instance_id,
        payload={"fingerprint_json": evidence.fingerprint_json},
        operation_metadata=RecoveryOperationMetadata(
            expected_final_fingerprint_json=evidence.fingerprint_json,
        ),
    )


def _resume_final_verified_catalog_handoff(
    *,
    operation: RecoveryOperation,
    runs: RunStore,
    recovery_operations: RecoveryResumeCatalogHandoffOperationStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
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
    if run.state is not RunState.EXECUTING or target.state is not RunTargetState.EXECUTING:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_TARGET_NOT_EXECUTING",
            next_action="Review run state before recording a final-verified catalog handoff.",
        )
    if _operation_target_binding_mismatch(
        operation=operation,
        target=target,
        expected_phase=RecoveryOperationPhase.FINAL_VERIFIED,
    ):
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_OPERATION_TARGET_BINDING_MISMATCH",
            next_action="Reconcile lease and ownership evidence before recording catalog handoff.",
        )

    content_hash = _operation_content_hash(operation)
    if content_hash is None:
        return _blocked(
            operation=operation,
            validation_code="RECOVERY_RESUME_FINAL_VERIFIED_REQUIRES_CONTENT_HASH",
            next_action="Reverify final filesystem state before recording catalog handoff.",
        )

    try:
        outcome = record_catalog_handoff_after_final_verification(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            content_hash=content_hash,
            recovery_operations=recovery_operations,
            catalog_handoffs=catalog_handoffs,
            process_instance_id=process_instance_id,
        )
    except (CatalogHandoffError, ValueError, RuntimeError) as exc:
        return RecoveryResumeFinding(
            action=RecoveryResumeAction.BLOCKED,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_ids=(operation.operation_id,),
            validation_codes=(_exception_validation_code(exc),),
            next_action=_exception_next_action(
                exc,
                "Reconcile catalog handoff state before retrying startup recovery resume.",
            ),
        )

    return RecoveryResumeFinding(
        action=RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_ids=(outcome.recovery_operation.operation_id,),
        validation_codes=(),
        next_action="Catalog handoff is recorded; complete catalog-recorded recovery work.",
    )


def _operation_target_binding_mismatch(
    *,
    operation: RecoveryOperation,
    target: StartedRunTarget,
    expected_phase: RecoveryOperationPhase,
) -> bool:
    return (
        operation.phase is not expected_phase
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


def _operation_content_hash(operation: RecoveryOperation) -> str | None:
    for payload in _operation_fingerprint_payloads(operation):
        content_hash = payload.get("content_hash")
        if isinstance(content_hash, str) and HASH_PATTERN.fullmatch(content_hash) is not None:
            return content_hash
    return None


def _operation_byte_count(operation: RecoveryOperation) -> int:
    for payload in _operation_fingerprint_payloads(operation):
        byte_count = payload.get("byte_count")
        if isinstance(byte_count, int) and byte_count >= 0:
            return byte_count
    return 0


def _operation_fingerprint_payloads(
    operation: RecoveryOperation,
) -> tuple[Mapping[str, object], ...]:
    payloads: list[Mapping[str, object]] = []
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
        if isinstance(payload, dict):
            payloads.append(payload)
    return tuple(payloads)


def _exception_validation_code(exc: Exception) -> str:
    validation_code = getattr(exc, "validation_code", None)
    if isinstance(validation_code, str) and validation_code.strip():
        return validation_code
    message = str(exc)
    if message.strip():
        return message
    return type(exc).__name__


def _exception_next_action(exc: Exception, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback


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
