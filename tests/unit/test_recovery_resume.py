from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import pytest

from mediasync_home.application.catalog_handoff import FinalFileCatalogHandoff
from mediasync_home.application.ports import FinalArtifactVerificationEvidence
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationMetadata,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.recovery_resume import (
    RecoveryResumeAction,
    RecoveryResumeStartupRequest,
    RecoveryResumeViolation,
    resume_catalog_recorded_run_targets_after_startup,
    resume_recovery_operations_after_startup,
    validate_recovery_resume_startup_request,
)
from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)


def test_recovery_resume_completes_catalog_recorded_run_target() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_catalog_recorded_operation(),))

    report = resume_catalog_recorded_run_targets_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
    )

    loaded = runs.load_started_run("run-a")
    assert report.scanned == 1
    assert report.completed_run_target_ids == ("run-a-target-0000",)
    assert report.blocked_run_target_ids == ()
    assert report.findings[0].action is RecoveryResumeAction.TARGET_COMPLETED
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert loaded.targets[0].state is RunTargetState.SUCCEEDED
    assert loaded.targets[0].completed_operations == 1
    assert loaded.targets[0].completed_bytes == 128


def test_recovery_resume_is_idempotent_for_already_completed_target() -> None:
    completed_target = replace(
        _target(),
        state=RunTargetState.SUCCEEDED,
        completed_operations=1,
        completed_bytes=128,
    )
    runs = _RunStore(replace(_run(), state=RunState.COMPLETED, targets=(completed_target,)))

    report = resume_catalog_recorded_run_targets_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=_RecoveryOperationStore((_catalog_recorded_operation(),)),
    )

    assert report.scanned == 1
    assert report.completed_run_target_ids == ()
    assert report.blocked_run_target_ids == ()
    assert report.findings[0].action is RecoveryResumeAction.ALREADY_TERMINAL


def test_recovery_resume_blocks_on_binding_mismatch() -> None:
    runs = _RunStore(replace(_run(), targets=(replace(_target(), last_fencing_token=41),)))

    report = resume_catalog_recorded_run_targets_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=_RecoveryOperationStore((_catalog_recorded_operation(),)),
    )

    assert report.scanned == 1
    assert report.completed_run_target_ids == ()
    assert report.blocked_run_target_ids == ("run-a-target-0000",)
    assert report.findings[0].action is RecoveryResumeAction.BLOCKED
    assert report.findings[0].validation_codes == (
        "RECOVERY_RESUME_OPERATION_TARGET_BINDING_MISMATCH",
    )


def test_recovery_resume_records_final_verified_catalog_handoff_then_completes_target() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_final_verified_operation(),))
    catalog_handoffs = _CatalogHandoffStore()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
    assert report.scanned == 2
    assert tuple(finding.action for finding in report.findings) == (
        RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
        RecoveryResumeAction.TARGET_COMPLETED,
    )
    assert report.completed_run_target_ids == ("run-a-target-0000",)
    assert report.blocked_run_target_ids == ()
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert loaded.targets[0].state is RunTargetState.SUCCEEDED
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert operation.catalog_handoff_id == "final-file:run-a:op-a"
    assert handoff is not None
    assert handoff.content_hash == "a" * 64


def test_recovery_resume_reverifies_filesystem_applied_then_completes_target() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_filesystem_applied_operation(),))
    catalog_handoffs = _CatalogHandoffStore()
    final_verifier = _FinalVerifier()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
        final_verifier=final_verifier,
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert report.scanned == 3
    assert tuple(finding.action for finding in report.findings) == (
        RecoveryResumeAction.FINAL_REVERIFIED,
        RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
        RecoveryResumeAction.TARGET_COMPLETED,
    )
    assert final_verifier.verified_operation_ids == ("op-a",)
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert operation.expected_final_fingerprint_json == _fingerprint_json()


def test_recovery_resume_reverifies_commit_preconditions_then_completes_target() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_commit_preconditions_operation(),))
    catalog_handoffs = _CatalogHandoffStore()
    final_verifier = _FinalVerifier()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
        final_verifier=final_verifier,
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert report.scanned == 3
    assert tuple(finding.action for finding in report.findings) == (
        RecoveryResumeAction.FINAL_REVERIFIED,
        RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
        RecoveryResumeAction.TARGET_COMPLETED,
    )
    assert final_verifier.verified_operation_ids == ("op-a",)
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert operation.expected_final_fingerprint_json == _fingerprint_json()


def test_recovery_resume_reverifies_old_target_preserved_then_completes_target() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_old_target_preserved_operation(),))
    catalog_handoffs = _CatalogHandoffStore()
    final_verifier = _FinalVerifier()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
        final_verifier=final_verifier,
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert report.scanned == 3
    assert tuple(finding.action for finding in report.findings) == (
        RecoveryResumeAction.FINAL_REVERIFIED,
        RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
        RecoveryResumeAction.TARGET_COMPLETED,
    )
    assert final_verifier.verified_operation_ids == ("op-a",)
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
    assert operation.version_object_id == "version-a"
    assert operation.expected_final_fingerprint_json == _fingerprint_json()


def test_recovery_resume_blocks_when_final_reverification_fails() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_filesystem_applied_operation(),))
    catalog_handoffs = _CatalogHandoffStore()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
        final_verifier=_FinalVerifier(failure_code="FINAL_ARTIFACT_VERIFY_FINGERPRINT_MISMATCH"),
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert report.scanned == 1
    assert report.completed_run_target_ids == ()
    assert report.blocked_run_target_ids == ("run-a-target-0000",)
    assert report.findings[0].action is RecoveryResumeAction.BLOCKED
    assert report.findings[0].validation_codes == (
        "FINAL_ARTIFACT_VERIFY_FINGERPRINT_MISMATCH",
    )
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.FILESYSTEM_APPLIED


def test_recovery_resume_blocks_old_target_preserved_when_final_reverification_fails() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore((_old_target_preserved_operation(),))
    catalog_handoffs = _CatalogHandoffStore()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
        final_verifier=_FinalVerifier(failure_code="FINAL_ARTIFACT_VERIFY_MISSING"),
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert report.scanned == 1
    assert report.completed_run_target_ids == ()
    assert report.blocked_run_target_ids == ("run-a-target-0000",)
    assert report.findings[0].action is RecoveryResumeAction.BLOCKED
    assert report.findings[0].validation_codes == ("FINAL_ARTIFACT_VERIFY_MISSING",)
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.OLD_TARGET_PRESERVED
    assert operation.version_object_id == "version-a"


def test_recovery_resume_blocks_final_verified_without_content_hash() -> None:
    runs = _RunStore(_run())
    recovery_operations = _RecoveryOperationStore(
        (replace(_final_verified_operation(), expected_final_fingerprint_json=None),)
    )
    catalog_handoffs = _CatalogHandoffStore()

    report = resume_recovery_operations_after_startup(
        RecoveryResumeStartupRequest(reconciler_instance_id="host-b"),
        runs=runs,
        recovery_operations=recovery_operations,
        catalog_handoffs=catalog_handoffs,
    )

    loaded = runs.load_started_run("run-a")
    operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
    assert report.scanned == 1
    assert report.completed_run_target_ids == ()
    assert report.blocked_run_target_ids == ("run-a-target-0000",)
    assert report.findings[0].action is RecoveryResumeAction.BLOCKED
    assert report.findings[0].validation_codes == (
        "RECOVERY_RESUME_FINAL_VERIFIED_REQUIRES_CONTENT_HASH",
    )
    assert catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a") is None
    assert loaded is not None
    assert loaded.state is RunState.EXECUTING
    assert operation is not None
    assert operation.phase is RecoveryOperationPhase.FINAL_VERIFIED


@pytest.mark.parametrize(
    ("startup_request", "error_code"),
    [
        (
            RecoveryResumeStartupRequest(reconciler_instance_id=" "),
            "RECOVERY_RESUME_REQUIRES_RECONCILER",
        ),
        (
            RecoveryResumeStartupRequest(reconciler_instance_id="host-b", limit=0),
            "RECOVERY_RESUME_LIMIT_MUST_BE_POSITIVE",
        ),
    ],
)
def test_recovery_resume_validates_request(
    startup_request: RecoveryResumeStartupRequest,
    error_code: str,
) -> None:
    with pytest.raises(RecoveryResumeViolation, match=error_code):
        validate_recovery_resume_startup_request(startup_request)


class _RecoveryOperationStore:
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self._operations = list(operations)

    def record_planned_operation(
        self,
        operation: RecoveryOperation,
        *,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> RecoveryOperation:
        self._operations.append(operation)
        return operation

    def record_operation_phase_transition(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_phase: RecoveryOperationPhase,
        next_phase: RecoveryOperationPhase,
        process_instance_id: str,
        payload: Mapping[str, object] | None = None,
        intent_segment_id: str | None = None,
        intent_ordinal: int | None = None,
        catalog_handoff_id: str | None = None,
        operation_metadata: RecoveryOperationMetadata | None = None,
    ) -> RecoveryOperation | None:
        if not process_instance_id.strip():
            return None
        for index, operation in enumerate(self._operations):
            if (
                operation.run_id == run_id
                and operation.operation_id == operation_id
                and operation.phase is expected_phase
            ):
                if next_phase is RecoveryOperationPhase.CATALOG_RECORDED and catalog_handoff_id is None:
                    return None
                updated = replace(
                    operation,
                    phase=next_phase,
                    catalog_handoff_id=catalog_handoff_id
                    if catalog_handoff_id is not None
                    else operation.catalog_handoff_id,
                    expected_final_fingerprint_json=operation_metadata.expected_final_fingerprint_json
                    if operation_metadata is not None
                    and operation_metadata.expected_final_fingerprint_json is not None
                    else operation.expected_final_fingerprint_json,
                )
                self._operations[index] = updated
                return updated
        return None

    def load_operation(self, *, run_id: str, operation_id: str) -> RecoveryOperation | None:
        return next(
            (
                operation
                for operation in self._operations
                if operation.run_id == run_id and operation.operation_id == operation_id
            ),
            None,
        )

    def list_operations_in_phase(
        self,
        *,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in self._operations
            if operation.phase is phase
        )[:limit]

    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]:
        return tuple(
            operation
            for operation in self._operations
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _CatalogHandoffStore:
    def __init__(self) -> None:
        self._handoffs: dict[str, FinalFileCatalogHandoff] = {}

    def record_final_file_handoff(
        self,
        handoff: FinalFileCatalogHandoff,
    ) -> FinalFileCatalogHandoff:
        existing = self._handoffs.get(handoff.handoff_id)
        if existing is not None:
            if existing != handoff:
                raise ValueError("CATALOG_HANDOFF_IDEMPOTENCY_CONFLICT")
            return existing
        self._handoffs[handoff.handoff_id] = handoff
        return handoff

    def load_final_file_handoff(self, handoff_id: str) -> FinalFileCatalogHandoff | None:
        return self._handoffs.get(handoff_id)


class _FinalVerifier:
    def __init__(self, *, failure_code: str | None = None) -> None:
        self._failure_code = failure_code
        self.verified_operation_ids: tuple[str, ...] = ()

    def verify_final_artifact(
        self,
        operation: RecoveryOperation,
    ) -> FinalArtifactVerificationEvidence:
        self.verified_operation_ids = (*self.verified_operation_ids, operation.operation_id)
        if self._failure_code is not None:
            raise RuntimeError(self._failure_code)
        return FinalArtifactVerificationEvidence(fingerprint_json=_fingerprint_json())


class _RunStore(RunStore):
    def __init__(self, run: StartedRun | None) -> None:
        self.run = run

    def save_started_run(self, run: StartedRun) -> None:
        self.run = run

    def load_started_run(self, run_id: str) -> StartedRun | None:
        if self.run is not None and self.run.run_id == run_id:
            return self.run
        return None

    def load_started_run_by_idempotency_key(self, idempotency_key: str) -> StartedRun | None:
        if self.run is not None and self.run.idempotency_key == idempotency_key:
            return self.run
        return None

    def load_next_pending_run_target(self, run_id: str) -> StartedRunTarget | None:
        return None

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        return None

    def record_run_target_lease_acquired(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        return None

    def record_run_target_execution_started(
        self,
        *,
        run_id: str,
        run_target_id: str,
        lease_id: str,
        owner_installation_id: str,
        ownership_epoch: int,
        fencing_token: int,
    ) -> StartedRunTarget | None:
        return None

    def record_run_target_succeeded(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
    ) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.EXECUTING:
            return None
        target = next(
            (item for item in run.targets if item.run_target_id == run_target_id),
            None,
        )
        if target is None or target.state is not RunTargetState.EXECUTING:
            return None
        if target.planned_operations != completed_operations or target.planned_bytes != completed_bytes:
            return None
        updated_target = replace(
            target,
            state=RunTargetState.SUCCEEDED,
            completed_operations=completed_operations,
            completed_bytes=completed_bytes,
        )
        self.run = replace(run, state=RunState.COMPLETED, targets=(updated_target,))
        return self.run


def _run() -> StartedRun:
    return StartedRun(
        run_id="run-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        plan_id="plan-a",
        command_request_id="request-a",
        idempotency_key="idempotency-a",
        command_receipt_id="idempotency-a",
        logical_run_group_id="run-group-a",
        trigger_type=RunTriggerType.MANUAL_LOCAL_PREVIEW,
        state=RunState.EXECUTING,
        app_version="0B-dev",
        plan_checksum="a" * 64,
        planned_operations=1,
        planned_bytes=128,
        targets=(_target(),),
    )


def _target() -> StartedRunTarget:
    return StartedRunTarget(
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state=RunTargetState.EXECUTING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        last_lease_id="lease-a",
        last_ownership_epoch=1,
        last_fencing_token=42,
        planned_operations=1,
        planned_bytes=128,
    )


def _catalog_recorded_operation() -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id="op-a",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id="lease-a",
            lease_resource_key="endpoint:target-a",
            fencing_token=42,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
            job_id="job-a",
            job_revision_id="job-rev-a",
            retention_policy="THIRTY_DAYS",
        ),
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        staging_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        catalog_handoff_id="final-file:run-a:op-a",
        expected_final_fingerprint_json='{"byte_count":128,"content_hash":"' + ("a" * 64) + '"}',
    )


def _final_verified_operation() -> RecoveryOperation:
    return replace(
        _catalog_recorded_operation(),
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        catalog_handoff_id=None,
    )


def _filesystem_applied_operation() -> RecoveryOperation:
    return replace(
        _catalog_recorded_operation(),
        phase=RecoveryOperationPhase.FILESYSTEM_APPLIED,
        catalog_handoff_id=None,
    )


def _commit_preconditions_operation() -> RecoveryOperation:
    return replace(
        _catalog_recorded_operation(),
        phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        catalog_handoff_id=None,
    )


def _old_target_preserved_operation() -> RecoveryOperation:
    return replace(
        _catalog_recorded_operation(),
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        catalog_handoff_id=None,
        target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        expected_target_fingerprint_json=_fingerprint_json(),
        version_object_id="version-a",
        version_created_utc="2026-08-01T00:00:00.000Z",
        version_retention_until_utc="2026-08-31T00:00:00.000Z",
        version_manifest_hash="c" * 64,
    )


def _fingerprint_json() -> str:
    return '{"byte_count":128,"content_hash":"' + ("a" * 64) + '"}'
