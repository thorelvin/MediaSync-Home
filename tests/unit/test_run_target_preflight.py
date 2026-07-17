from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.runs import (
    RunState,
    RunStore,
    RunTargetState,
    RunTriggerType,
    StartedRun,
    StartedRunTarget,
    begin_next_run_target_preflight,
)


def test_begin_next_run_target_preflight_claims_pending_target() -> None:
    runs = _InMemoryRunStore(_queued_run())

    outcome = begin_next_run_target_preflight(run_id="run-a", runs=runs)

    loaded = runs.load_started_run("run-a")
    assert outcome.claimed is True
    assert outcome.validation_codes == ()
    assert outcome.target is not None
    assert outcome.target.state is RunTargetState.ACQUIRING_LEASE
    assert loaded is not None
    assert loaded.state is RunState.PREFLIGHT
    assert loaded.targets[0].state is RunTargetState.ACQUIRING_LEASE


def test_begin_next_run_target_preflight_requires_existing_run() -> None:
    outcome = begin_next_run_target_preflight(run_id="missing-run", runs=_InMemoryRunStore(None))

    assert outcome.claimed is False
    assert outcome.validation_codes == ("RUN_NOT_FOUND",)


def test_begin_next_run_target_preflight_requires_ready_run_state() -> None:
    runs = _InMemoryRunStore(replace(_queued_run(), state=RunState.EXECUTING))

    outcome = begin_next_run_target_preflight(run_id="run-a", runs=runs)

    assert outcome.claimed is False
    assert outcome.validation_codes == ("RUN_NOT_READY_FOR_TARGET_PREFLIGHT",)


def test_begin_next_run_target_preflight_requires_pending_target() -> None:
    run = replace(
        _queued_run(),
        targets=(replace(_target(), state=RunTargetState.ACQUIRING_LEASE),),
    )

    outcome = begin_next_run_target_preflight(run_id="run-a", runs=_InMemoryRunStore(run))

    assert outcome.claimed is False
    assert outcome.validation_codes == ("RUN_HAS_NO_PENDING_TARGETS",)


def test_begin_next_run_target_preflight_requires_lease_resource_key() -> None:
    run = replace(_queued_run(), targets=(replace(_target(), lease_resource_key=None),))

    outcome = begin_next_run_target_preflight(run_id="run-a", runs=_InMemoryRunStore(run))

    assert outcome.claimed is False
    assert outcome.run_target_id == "run-a-target-0000"
    assert outcome.validation_codes == ("RUN_TARGET_REQUIRES_LEASE_RESOURCE_KEY",)


def test_begin_next_run_target_preflight_reports_claim_conflict() -> None:
    runs = _InMemoryRunStore(_queued_run(), conflict=True)

    outcome = begin_next_run_target_preflight(run_id="run-a", runs=runs)

    assert outcome.claimed is False
    assert outcome.validation_codes == ("RUN_TARGET_PREFLIGHT_CLAIM_CONFLICT",)


class _InMemoryRunStore(RunStore):
    def __init__(self, run: StartedRun | None, *, conflict: bool = False) -> None:
        self.run = run
        self.conflict = conflict

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
        run = self.load_started_run(run_id)
        if run is None:
            return None
        return next((target for target in run.targets if target.state is RunTargetState.PENDING), None)

    def begin_run_target_preflight(
        self,
        *,
        run_id: str,
        run_target_id: str,
    ) -> StartedRunTarget | None:
        if self.conflict:
            return None
        run = self.load_started_run(run_id)
        if run is None or run.state not in {RunState.QUEUED, RunState.PREFLIGHT}:
            return None
        updated_targets: list[StartedRunTarget] = []
        claimed: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.PENDING:
                claimed = replace(target, state=RunTargetState.ACQUIRING_LEASE)
                updated_targets.append(claimed)
            else:
                updated_targets.append(target)
        if claimed is None:
            return None
        self.run = replace(run, state=RunState.PREFLIGHT, targets=tuple(updated_targets))
        return claimed

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
        run = self.load_started_run(run_id)
        if run is None or run.state is not RunState.PREFLIGHT:
            return None
        updated_targets: list[StartedRunTarget] = []
        recorded: StartedRunTarget | None = None
        for target in run.targets:
            if target.run_target_id == run_target_id and target.state is RunTargetState.ACQUIRING_LEASE:
                recorded = replace(
                    target,
                    state=RunTargetState.REVALIDATING,
                    last_lease_id=lease_id,
                    last_ownership_epoch=ownership_epoch,
                    last_fencing_token=fencing_token,
                )
                updated_targets.append(recorded)
            else:
                updated_targets.append(target)
        if recorded is None:
            return None
        self.run = replace(run, targets=tuple(updated_targets))
        return recorded


def _queued_run() -> StartedRun:
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
        state=RunState.QUEUED,
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
        state=RunTargetState.PENDING,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        lease_resource_key="endpoint:target-a",
        planned_operations=1,
        planned_bytes=128,
    )
