from __future__ import annotations

from dataclasses import dataclass

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


def enqueue_trigger_occurrence_run(
    *,
    command: EnqueueTriggerOccurrenceCommand,
    installation_id: str,
    schedules: ScheduleStore,
    occurrences: TriggerOccurrenceStore,
    plans: PlanStore,
    runs: RunStore,
    id_factory: RunIdFactory,
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
    )
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
