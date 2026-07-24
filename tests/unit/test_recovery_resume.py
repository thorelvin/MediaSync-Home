from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.recovery_resume import (
    RecoveryResumeAction,
    RecoveryResumeStartupRequest,
    RecoveryResumeViolation,
    resume_catalog_recorded_run_targets_after_startup,
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
        self._operations = operations

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
        ),
        phase=RecoveryOperationPhase.CATALOG_RECORDED,
        staging_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        catalog_handoff_id="final-file:run-a:op-a",
        expected_final_fingerprint_json='{"byte_count":128,"content_hash":"' + ("a" * 64) + '"}',
    )
