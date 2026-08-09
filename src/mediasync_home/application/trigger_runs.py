from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.application.command_receipts import CommandEffectTransaction
from mediasync_home.application.cross_store_handoffs import (
    RunStartCrossStoreCoordinator,
)
from mediasync_home.application.backup_analysis import (
    BackupAnalysisRequest,
    BackupAnalysisRequestState,
    BackupAnalysisRequestStore,
)
from mediasync_home.application.job_lifecycle import JobLifecycleState
from mediasync_home.application.job_read_models import (
    StandardBackupJobDetailReadModelStore,
)
from mediasync_home.application.plans import PlanStore
from mediasync_home.application.runs import (
    RunIdFactory,
    RunStartOutcome,
    RunStore,
    StartRunCommand,
    start_run_from_sealed_plan,
)
from mediasync_home.application.schedules import (
    ScheduleStore,
    ScheduleTriggerResolutionKind,
    resolve_schedule_for_trigger,
)
from mediasync_home.application.trigger_occurrences import (
    EnqueueTriggerOccurrenceCommand,
    TERMINAL_TRIGGER_OCCURRENCE_STATES,
    TriggerOccurrence,
    TriggerOccurrenceStore,
    build_trigger_occurrence,
)


@dataclass(frozen=True)
class TriggerRunEnqueueOutcome:
    enqueued: bool
    deduplicated: bool
    compacted: bool
    schedule_resolution_kind: ScheduleTriggerResolutionKind
    validation_codes: tuple[str, ...]
    next_action: str
    occurrence: TriggerOccurrence | None = None
    run_start: RunStartOutcome | None = None
    analysis_request: BackupAnalysisRequest | None = None


def enqueue_trigger_occurrence_analysis(
    *,
    command: EnqueueTriggerOccurrenceCommand,
    installation_id: str,
    schedules: ScheduleStore,
    occurrences: TriggerOccurrenceStore,
    jobs: StandardBackupJobDetailReadModelStore,
    analysis_requests: BackupAnalysisRequestStore,
) -> TriggerRunEnqueueOutcome:
    resolution = resolve_schedule_for_trigger(
        schedules=schedules,
        schedule_id=command.schedule_id,
        schedule_revision_hash=command.schedule_revision_hash,
    )
    if resolution.kind is not ScheduleTriggerResolutionKind.READY:
        return _schedule_not_ready(resolution.kind)

    schedule = resolution.schedule
    if schedule is None:
        return _schedule_not_ready(ScheduleTriggerResolutionKind.NOT_FOUND)
    if schedule.trigger_type is not command.delivery.trigger_kind:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=False,
            compacted=False,
            schedule_resolution_kind=resolution.kind,
            validation_codes=("TRIGGER_SCHEDULE_KIND_MISMATCH",),
            next_action=(
                "Refresh the trigger registration because the schedule trigger "
                "kind changed."
            ),
        )
    job = jobs.load_standard_backup_job_detail(schedule.job_id)
    if job is None:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=False,
            compacted=False,
            schedule_resolution_kind=resolution.kind,
            validation_codes=("TRIGGER_JOB_NOT_FOUND",),
            next_action="Refresh or remove the schedule for the missing backup job.",
        )
    if job.lifecycle_state is not JobLifecycleState.ACTIVE:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=False,
            compacted=False,
            schedule_resolution_kind=resolution.kind,
            validation_codes=("TRIGGER_JOB_ARCHIVED",),
            next_action="Reactivate the backup job before automation can run.",
        )

    registration = occurrences.record_received(
        build_trigger_occurrence(
            installation_id=installation_id,
            job_id=schedule.job_id,
            command=command,
        )
    )
    occurrence = registration.occurrence
    if occurrence.state in TERMINAL_TRIGGER_OCCURRENCE_STATES:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=registration.deduplicated,
            compacted=registration.compacted,
            schedule_resolution_kind=resolution.kind,
            validation_codes=("TRIGGER_OCCURRENCE_ALREADY_TERMINAL",),
            next_action=(
                "Treat the trigger occurrence as already complete and do not queue "
                "another analysis."
            ),
            occurrence=occurrence,
        )
    analysis_request = analysis_requests.enqueue_backup_analysis(
        BackupAnalysisRequest(
            request_id=occurrence.occurrence_id,
            command_idempotency_key=occurrence.first_delivery_id,
            job_id=job.job_id,
            job_revision_id=job.job_revision_id,
            state=BackupAnalysisRequestState.QUEUED,
            requested_utc=command.delivery.observed_start_utc,
            start_when_safe=True,
        )
    )
    return TriggerRunEnqueueOutcome(
        enqueued=True,
        deduplicated=registration.deduplicated,
        compacted=registration.compacted,
        schedule_resolution_kind=resolution.kind,
        validation_codes=(),
        next_action="A fresh backup check is queued for this trigger occurrence.",
        occurrence=occurrence,
        analysis_request=analysis_request,
    )


def enqueue_trigger_occurrence_run(
    *,
    command: EnqueueTriggerOccurrenceCommand,
    installation_id: str,
    schedules: ScheduleStore,
    occurrences: TriggerOccurrenceStore,
    plans: PlanStore,
    runs: RunStore,
    id_factory: RunIdFactory,
    run_start_cross_store_coordinator: RunStartCrossStoreCoordinator | None = None,
    run_start_transaction: CommandEffectTransaction | None = None,
) -> TriggerRunEnqueueOutcome:
    resolution = resolve_schedule_for_trigger(
        schedules=schedules,
        schedule_id=command.schedule_id,
        schedule_revision_hash=command.schedule_revision_hash,
    )
    if resolution.kind is not ScheduleTriggerResolutionKind.READY:
        return _schedule_not_ready(resolution.kind)

    schedule = resolution.schedule
    if schedule is None:
        return _schedule_not_ready(ScheduleTriggerResolutionKind.NOT_FOUND)
    if schedule.trigger_type is not command.delivery.trigger_kind:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=False,
            compacted=False,
            schedule_resolution_kind=resolution.kind,
            validation_codes=("TRIGGER_SCHEDULE_KIND_MISMATCH",),
            next_action="Refresh the trigger registration because the schedule trigger kind changed.",
        )

    registration = occurrences.record_received(
        build_trigger_occurrence(
            installation_id=installation_id,
            job_id=schedule.job_id,
            command=command,
        )
    )
    occurrence = registration.occurrence
    if occurrence.state in TERMINAL_TRIGGER_OCCURRENCE_STATES:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=registration.deduplicated,
            compacted=registration.compacted,
            schedule_resolution_kind=resolution.kind,
            validation_codes=("TRIGGER_OCCURRENCE_ALREADY_TERMINAL",),
            next_action="Treat the trigger occurrence as already complete and do not enqueue another run.",
            occurrence=occurrence,
        )

    def prepare_run() -> RunStartOutcome:
        run_start = start_run_from_sealed_plan(
            command=StartRunCommand(
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
                plan_id=schedule.plan_id,
                plan_checksum=schedule.plan_checksum,
                run_idempotency_key=occurrence.occurrence_id,
                trigger_occurrence_id=occurrence.occurrence_id,
            ),
            plans=plans,
            runs=runs,
            id_factory=id_factory,
            defer_until_recovery_bound=(
                run_start_cross_store_coordinator is not None
            ),
        )
        if run_start.run is not None and run_start_cross_store_coordinator is not None:
            run_start_cross_store_coordinator.prepare_run_start(
                run_start.run,
                transition_command_receipt=False,
            )
        return run_start

    if run_start_cross_store_coordinator is not None:
        if run_start_transaction is None:
            return TriggerRunEnqueueOutcome(
                enqueued=False,
                deduplicated=registration.deduplicated,
                compacted=registration.compacted,
                schedule_resolution_kind=resolution.kind,
                validation_codes=("TRIGGER_RUN_HANDOFF_NOT_CONFIGURED",),
                next_action="Configure the catalog transaction before starting trigger runs.",
                occurrence=occurrence,
            )
        run_start = run_start_transaction.run(prepare_run)
        if run_start.run is not None:
            run_start_cross_store_coordinator.advance_run_start(run_start.run.run_id)
    else:
        run_start = prepare_run()
    if run_start.run is None:
        return TriggerRunEnqueueOutcome(
            enqueued=False,
            deduplicated=registration.deduplicated,
            compacted=registration.compacted,
            schedule_resolution_kind=resolution.kind,
            validation_codes=(
                "TRIGGER_RUN_PRECONDITION_FAILED",
                *run_start.readiness.validation_codes,
            ),
            next_action=run_start.readiness.next_action,
            occurrence=occurrence,
            run_start=run_start,
        )

    updated_occurrence = occurrences.mark_run_enqueued(
        deduplication_key=occurrence.deduplication_key,
        run_id=run_start.run.run_id,
    )
    return TriggerRunEnqueueOutcome(
        enqueued=True,
        deduplicated=registration.deduplicated,
        compacted=registration.compacted,
        schedule_resolution_kind=resolution.kind,
        validation_codes=(),
        next_action="Trigger occurrence is bound to a queued run.",
        occurrence=updated_occurrence,
        run_start=run_start,
    )


def _schedule_not_ready(kind: ScheduleTriggerResolutionKind) -> TriggerRunEnqueueOutcome:
    return TriggerRunEnqueueOutcome(
        enqueued=False,
        deduplicated=False,
        compacted=False,
        schedule_resolution_kind=kind,
        validation_codes=(f"TRIGGER_SCHEDULE_{kind.value}",),
        next_action="Refresh the schedule desired state before accepting this trigger delivery.",
    )
