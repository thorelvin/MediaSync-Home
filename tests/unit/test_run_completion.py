from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_completion import (
    RunTargetCompletionOperationStore,
    complete_run_target_after_catalog_handoffs,
    complete_run_target_after_terminal_recovery,
)
from mediasync_home.application.runs import (
    RunState,
    RunTargetState,
    RunWarningCompletionStore,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_complete_run_target_after_catalog_handoffs_counts_cataloged_operations() -> (
    None
):
    runs = _InMemoryRunStore(_executing_run())

    outcome = complete_run_target_after_catalog_handoffs(
        permit=_permit(),
        runs=runs,
        recovery_operations=_FakeRecoveryOperationStore((_operation(),)),
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.run_completed is True
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.completed_operations == 1
    assert outcome.target.completed_bytes == 128
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED


def test_complete_run_target_after_catalog_handoffs_counts_cleaned_operations() -> None:
    runs = _InMemoryRunStore(_executing_run())

    outcome = complete_run_target_after_catalog_handoffs(
        permit=_permit(),
        runs=runs,
        recovery_operations=_FakeRecoveryOperationStore(
            (_operation(phase=RecoveryOperationPhase.CLEANED),)
        ),
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.run_completed is True
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.completed_operations == 1
    assert outcome.target.completed_bytes == 128
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED


def test_complete_run_target_after_catalog_handoffs_requires_all_catalog_handoffs() -> (
    None
):
    outcome = complete_run_target_after_catalog_handoffs(
        permit=_permit(),
        runs=_InMemoryRunStore(_executing_run()),
        recovery_operations=_FakeRecoveryOperationStore(()),
    )

    assert outcome.completed is False
    assert outcome.validation_codes == ("RUN_TARGET_COMPLETION_COUNTS_MISMATCH",)


def test_complete_run_target_after_catalog_handoffs_rejects_target_permit_mismatch() -> (
    None
):
    target = replace(_target(), last_fencing_token=99)

    outcome = complete_run_target_after_catalog_handoffs(
        permit=_permit(),
        runs=_InMemoryRunStore(_executing_run(targets=(target,))),
        recovery_operations=_FakeRecoveryOperationStore((_operation(),)),
    )

    assert outcome.completed is False
    assert outcome.validation_codes == ("RUN_TARGET_COMPLETION_PERMIT_MISMATCH",)


def test_complete_run_target_after_catalog_handoffs_accepts_prior_lease_completion() -> (
    None
):
    outcome = complete_run_target_after_catalog_handoffs(
        permit=_permit(),
        runs=_InMemoryRunStore(_executing_run()),
        recovery_operations=_FakeRecoveryOperationStore(
            (_operation(lease_id="other-lease"),)
        ),
    )

    assert outcome.completed is True
    assert outcome.validation_codes == ()


def test_complete_run_target_after_catalog_handoffs_rejects_operation_ownership_mismatch() -> (
    None
):
    outcome = complete_run_target_after_catalog_handoffs(
        permit=_permit(),
        runs=_InMemoryRunStore(_executing_run()),
        recovery_operations=_FakeRecoveryOperationStore(
            (_operation(ownership_epoch=2),)
        ),
    )

    assert outcome.completed is False
    assert outcome.validation_codes == (
        "RUN_TARGET_COMPLETION_OPERATION_PERMIT_MISMATCH",
    )


def test_complete_run_target_after_terminal_recovery_marks_user_decision_required() -> (
    None
):
    runs = _InMemoryRunStore(_executing_run())

    outcome = complete_run_target_after_terminal_recovery(
        permit=_permit(),
        runs=runs,
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.USER_DECISION_REQUIRED,
                    last_error_code="LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",
                ),
            )
        ),
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.RECOVERY_REQUIRED
    assert loaded is not None
    assert loaded.state is RunState.RECOVERY_REQUIRED
    assert loaded.error_count == 1


def test_complete_run_target_after_terminal_recovery_cancels_restored_old_target() -> (
    None
):
    runs = _InMemoryRunStore(_executing_run())

    outcome = complete_run_target_after_terminal_recovery(
        permit=_permit(),
        runs=runs,
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.CANCELLED,
                    last_error_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED",
                ),
            )
        ),
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.CANCELLED
    assert loaded is not None
    assert loaded.state is RunState.CANCELLED
    assert loaded.error_count == 1


def test_complete_run_target_after_terminal_recovery_marks_skipped_file_warning() -> (
    None
):
    runs = _InMemoryRunStore(_executing_run())

    outcome = complete_run_target_after_terminal_recovery(
        permit=_permit(),
        runs=runs,
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.SKIPPED,
                    last_error_code="LOCAL_STAGING_SOURCE_FILE_UNREADABLE",
                ),
            )
        ),
    )

    loaded = runs.load_started_run("run-a")
    assert outcome.completed is True
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.SUCCEEDED_WITH_WARNINGS
    assert loaded is not None
    assert loaded.state is RunState.COMPLETED_WITH_WARNINGS
    assert loaded.warning_count == 1


def test_complete_run_target_after_terminal_recovery_rejects_unknown_cancel_reason() -> (
    None
):
    outcome = complete_run_target_after_terminal_recovery(
        permit=_permit(),
        runs=_InMemoryRunStore(_executing_run()),
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.CANCELLED,
                    last_error_code="UNRECOGNIZED_CANCEL_REASON",
                ),
            )
        ),
    )

    assert outcome.completed is False
    assert outcome.validation_codes == (
        "RUN_TARGET_TERMINAL_RECOVERY_CANCEL_REASON_UNSUPPORTED",
    )


def test_complete_run_target_after_terminal_recovery_rejects_mixed_cancel_reasons() -> (
    None
):
    outcome = complete_run_target_after_terminal_recovery(
        permit=_permit(),
        runs=_InMemoryRunStore(_executing_run()),
        recovery_operations=_FakeRecoveryOperationStore(
            (
                _operation(
                    phase=RecoveryOperationPhase.CANCELLED,
                    last_error_code="RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED",
                ),
                replace(
                    _operation(
                        phase=RecoveryOperationPhase.CANCELLED,
                        last_error_code="UNRECOGNIZED_CANCEL_REASON",
                    ),
                    operation_id="op-b",
                ),
            )
        ),
    )

    assert outcome.completed is False
    assert outcome.validation_codes == (
        "RUN_TARGET_TERMINAL_RECOVERY_CANCEL_REASON_UNSUPPORTED",
    )


class _FakeRecoveryOperationStore(RunTargetCompletionOperationStore):
    def __init__(self, operations: tuple[RecoveryOperation, ...]) -> None:
        self.operations = operations

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
            for operation in sorted(self.operations, key=lambda item: item.operation_id)
            if operation.run_id == run_id
            and operation.run_target_id == run_target_id
            and operation.phase is phase
        )[:limit]


class _InMemoryRunStore(RunWarningCompletionStore):
    def __init__(self, run: StartedRun | None) -> None:
        self.run = run

    def save_started_run(self, run: StartedRun) -> None:
        self.run = run

    def load_started_run(self, run_id: str) -> StartedRun | None:
        if self.run is not None and self.run.run_id == run_id:
            return self.run
        return None

    def load_started_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> StartedRun | None:
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
        updated_targets: list[StartedRunTarget] = []
        completed: StartedRunTarget | None = None
        for target in run.targets:
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.EXECUTING
            ):
                if (
                    target.planned_operations != completed_operations
                    or target.planned_bytes != completed_bytes
                ):
                    return None
                completed = replace(
                    target,
                    state=RunTargetState.SUCCEEDED,
                    completed_operations=completed_operations,
                    completed_bytes=completed_bytes,
                )
                updated_targets.append(completed)
            else:
                updated_targets.append(target)
        if completed is None:
            return None
        run_state = (
            RunState.COMPLETED
            if all(
                target.state is RunTargetState.SUCCEEDED for target in updated_targets
            )
            else RunState.EXECUTING
        )
        self.run = replace(run, state=run_state, targets=tuple(updated_targets))
        return self.run

    def record_run_target_succeeded_with_warnings(
        self,
        *,
        run_id: str,
        run_target_id: str,
        completed_operations: int,
        completed_bytes: int,
        skipped_operations: int,
        skipped_bytes: int,
        last_error_code: str,
    ) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.EXECUTING:
            return None
        updated_targets: list[StartedRunTarget] = []
        completed: StartedRunTarget | None = None
        for target in run.targets:
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.EXECUTING
            ):
                if (
                    target.planned_operations
                    != completed_operations + skipped_operations
                    or target.planned_bytes != completed_bytes + skipped_bytes
                ):
                    return None
                completed = replace(
                    target,
                    state=RunTargetState.SUCCEEDED_WITH_WARNINGS,
                    completed_operations=completed_operations,
                    completed_bytes=completed_bytes,
                )
                updated_targets.append(completed)
            else:
                updated_targets.append(target)
        if completed is None:
            return None
        self.run = replace(
            run,
            state=RunState.COMPLETED_WITH_WARNINGS,
            targets=tuple(updated_targets),
            warning_count=run.warning_count + skipped_operations,
        )
        return self.run

    def record_run_target_recovery_required(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None:
        return self._record_terminal_target(
            run_id=run_id,
            run_target_id=run_target_id,
            target_state=RunTargetState.RECOVERY_REQUIRED,
            run_state=RunState.RECOVERY_REQUIRED,
        )

    def record_run_target_cancelled(
        self,
        *,
        run_id: str,
        run_target_id: str,
        last_error_code: str,
    ) -> StartedRun | None:
        return self._record_terminal_target(
            run_id=run_id,
            run_target_id=run_target_id,
            target_state=RunTargetState.CANCELLED,
            run_state=RunState.CANCELLED,
        )

    def _record_terminal_target(
        self,
        *,
        run_id: str,
        run_target_id: str,
        target_state: RunTargetState,
        run_state: RunState,
    ) -> StartedRun | None:
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.EXECUTING:
            return None
        updated_targets: list[StartedRunTarget] = []
        completed: StartedRunTarget | None = None
        for target in run.targets:
            if (
                target.run_target_id == run_target_id
                and target.state is RunTargetState.EXECUTING
            ):
                completed = replace(target, state=target_state)
                updated_targets.append(completed)
            else:
                updated_targets.append(target)
        if completed is None:
            return None
        self.run = replace(
            run,
            state=run_state,
            targets=tuple(updated_targets),
            error_count=run.error_count + 1,
        )
        return self.run


def _executing_run(
    *,
    targets: tuple[StartedRunTarget, ...] | None = None,
) -> StartedRun:
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
        targets=targets or (_target(),),
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


def _operation(
    *,
    lease_id: str = "lease-a",
    ownership_epoch: int = 1,
    phase: RecoveryOperationPhase = RecoveryOperationPhase.CATALOG_RECORDED,
    last_error_code: str | None = None,
) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id="op-a",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=ownership_epoch,
            lease_id=lease_id,
            lease_resource_key="endpoint:target-a",
            fencing_token=42,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
            planned_bytes=128,
        ),
        phase=phase,
        staging_object_id="op-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
        catalog_handoff_id="final-file:run-a:op-a",
        expected_final_fingerprint_json='{"byte_count":128,"content_hash":"'
        + ("a" * 64)
        + '"}',
        last_error_code=last_error_code,
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=42,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
