from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.application.runs import RunState, RunTargetState
from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


class BackupSetupStep(str, Enum):
    SOURCE = "source"
    TARGETS = "targets"
    DEFAULTS = "defaults"
    REVIEW = "review"


class ActivityState(str, Enum):
    INACTIVE = "inactive"
    CHECKING = "checking"
    COPYING = "copying"
    VERIFYING = "verifying"
    PAUSED = "paused"
    RESTORING = "restoring"


class AttentionState(str, Enum):
    BLOCKED = "blocked"
    NEEDS_ATTENTION = "needs_attention"
    WAITING = "waiting"
    NORMAL = "normal"


class FreshnessState(str, Enum):
    UP_TO_DATE = "up_to_date"
    LAST_BACKED_UP = "last_backed_up"
    NEVER_CHECKED = "never_checked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BackupTargetDraft:
    name: str
    path_label: str
    independent_device_id: str | None = None


@dataclass(frozen=True)
class BackupSetupDraft:
    source_name: str | None = None
    source_path_label: str | None = None
    targets: tuple[BackupTargetDraft, ...] = ()

    @classmethod
    def empty(cls) -> "BackupSetupDraft":
        return cls()


@dataclass(frozen=True)
class BackupDefaultsViewState:
    behavior_label: str = "Oppdater backup"
    included_files_label: str = "Alle brukerfiler"
    verification_label: str = "Standard kontroll"
    retention_label: str = "Tidligere versjoner beholdes i 30 dager"
    extra_files_label: str = "Ekstra filer på målet beholdes"
    performance_label: str = "Auto - anbefalt"

    def summary(self) -> tuple[str, ...]:
        return (
            self.behavior_label,
            self.included_files_label,
            self.verification_label,
            self.retention_label,
            self.extra_files_label,
            self.performance_label,
        )


@dataclass(frozen=True)
class BackupSetupStepViewState:
    step: BackupSetupStep
    number: int
    title: str
    current: bool
    complete: bool


@dataclass(frozen=True)
class StandardBackupSetupViewState:
    steps: tuple[BackupSetupStepViewState, ...]
    current_step: BackupSetupStep
    source_label: str
    target_label: str
    configured_targets: int
    max_targets: int
    defaults: BackupDefaultsViewState
    primary_action_label: str
    can_continue: bool
    can_create: bool
    review_lines: tuple[str, ...]


@dataclass(frozen=True)
class TargetStatusViewState:
    name: str
    activity_label: str
    attention_label: str
    freshness_label: str
    recommended_action: str
    independent_device_id: str | None = None


@dataclass(frozen=True)
class BackupJobStatusViewState:
    title: str
    activity_label: str
    attention_label: str
    configured_target_count: int
    independent_device_count: int
    target_statuses: tuple[TargetStatusViewState, ...]
    recommended_action: str


@dataclass(frozen=True)
class BackupJobDetailTargetViewState:
    name: str
    path_label: str
    independent_device_id: str | None = None
    registration_state: str | None = None


@dataclass(frozen=True)
class BackupJobDetailViewState:
    job_id: str | None
    title: str
    source_label: str
    revision_label: str
    target_summary_label: str
    defaults_summary_label: str
    target_lines: tuple[str, ...]
    read_model_available: bool
    found: bool
    plan_summary_label: str = "Ingen plan ennå."
    plan_id: str | None = None
    plan_checksum: str | None = None
    plan_state: str | None = None
    plan_runnable: bool = False


@dataclass(frozen=True)
class BackupOverviewViewState:
    setup: StandardBackupSetupViewState
    job_status: BackupJobStatusViewState
    read_model_available: bool
    has_more_jobs: bool
    selected_job_id: str | None = None


@dataclass(frozen=True)
class ActivityOverviewViewState:
    job_status: BackupJobStatusViewState | None
    read_model_available: bool
    has_more_runs: bool
    latest_plan_id: str | None = None


STEP_TITLES = {
    BackupSetupStep.SOURCE: "Hva vil du beskytte?",
    BackupSetupStep.TARGETS: "Hvor vil du ha kopier?",
    BackupSetupStep.DEFAULTS: "Hvordan skal backupen fungere?",
    BackupSetupStep.REVIEW: "Kontroller og opprett",
}
STEP_ORDER = (
    BackupSetupStep.SOURCE,
    BackupSetupStep.TARGETS,
    BackupSetupStep.DEFAULTS,
    BackupSetupStep.REVIEW,
)


def build_standard_backup_setup_state(
    draft: BackupSetupDraft,
    *,
    current_step: BackupSetupStep = BackupSetupStep.SOURCE,
) -> StandardBackupSetupViewState:
    if current_step not in STEP_ORDER:
        raise ValueError(f"unknown backup setup step: {current_step}")

    can_continue = _can_continue(draft, current_step)
    can_create = _can_create(draft, current_step)
    source_label = draft.source_path_label or "Ingen kilde valgt"
    target_label = _target_label(draft.targets)
    defaults = BackupDefaultsViewState()
    return StandardBackupSetupViewState(
        steps=_build_steps(draft, current_step),
        current_step=current_step,
        source_label=source_label,
        target_label=target_label,
        configured_targets=len(draft.targets),
        max_targets=3,
        defaults=defaults,
        primary_action_label=(
            "Opprett og registrer" if current_step is BackupSetupStep.REVIEW else "Fortsett"
        ),
        can_continue=can_continue,
        can_create=can_create,
        review_lines=_review_lines(draft, defaults),
    )


def setup_draft_from_job_draft(draft: StandardBackupJobDraft) -> BackupSetupDraft:
    return BackupSetupDraft(
        source_name=draft.source_name,
        source_path_label=draft.source_path_label,
        targets=tuple(
            BackupTargetDraft(
                name=target.name,
                path_label=target.path_label,
                independent_device_id=target.independent_device_id,
            )
            for target in draft.targets
        ),
    )


def build_standard_backup_setup_state_from_job_draft(
    draft: StandardBackupJobDraft,
    *,
    current_step: BackupSetupStep = BackupSetupStep.SOURCE,
) -> StandardBackupSetupViewState:
    return build_standard_backup_setup_state(
        setup_draft_from_job_draft(draft),
        current_step=current_step,
    )


def build_backup_job_status_state(
    *,
    title: str,
    activity: ActivityState,
    attention: AttentionState,
    target_statuses: tuple[TargetStatusViewState, ...],
    recommended_action: str,
) -> BackupJobStatusViewState:
    return BackupJobStatusViewState(
        title=title,
        activity_label=_activity_label(activity),
        attention_label=_attention_label(attention),
        configured_target_count=len(target_statuses),
        independent_device_count=len(
            {target.independent_device_id for target in target_statuses if target.independent_device_id}
        ),
        target_statuses=target_statuses,
        recommended_action=recommended_action,
    )


def target_status(
    *,
    name: str,
    activity: ActivityState,
    attention: AttentionState,
    freshness: FreshnessState,
    recommended_action: str,
    independent_device_id: str | None = None,
) -> TargetStatusViewState:
    return TargetStatusViewState(
        name=name,
        activity_label=_activity_label(activity),
        attention_label=_attention_label(attention),
        freshness_label=_freshness_label(freshness),
        recommended_action=recommended_action,
        independent_device_id=independent_device_id,
    )


def empty_backup_job_status_state() -> BackupJobStatusViewState:
    return build_backup_job_status_state(
        title="Ingen backupjobb",
        activity=ActivityState.INACTIVE,
        attention=AttentionState.WAITING,
        target_statuses=(),
        recommended_action="Opprett backup når kilde og mål er klare.",
    )


def empty_backup_overview_state() -> BackupOverviewViewState:
    return BackupOverviewViewState(
        setup=build_standard_backup_setup_state(BackupSetupDraft.empty()),
        job_status=empty_backup_job_status_state(),
        read_model_available=False,
        has_more_jobs=False,
    )


def empty_backup_job_detail_state() -> BackupJobDetailViewState:
    return BackupJobDetailViewState(
        job_id=None,
        title="Ingen lagret backupjobb",
        source_label="Opprett eller velg en backupjobb.",
        revision_label="Ingen aktiv revisjon",
        target_summary_label="Ingen mål konfigurert",
        defaults_summary_label="Standardvalg ikke lastet",
        target_lines=(),
        read_model_available=False,
        found=False,
    )


def backup_overview_from_response(response: IpcResponse | None) -> BackupOverviewViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_backup_overview_state()
    overview = response.payload.get("backup_overview")
    if not isinstance(overview, dict):
        return empty_backup_overview_state()

    draft = _setup_draft_from_payload(overview.get("draft"))
    jobs = overview.get("jobs")
    job_payloads = tuple(item for item in jobs if isinstance(item, dict)) if isinstance(jobs, list) else ()
    selected_job_id = _required_text(job_payloads[0].get("job_id")) if job_payloads else None
    return BackupOverviewViewState(
        setup=build_standard_backup_setup_state(
            draft or BackupSetupDraft.empty(),
            current_step=BackupSetupStep.REVIEW if draft is not None and draft.targets else BackupSetupStep.SOURCE,
        ),
        job_status=_job_status_from_payload(job_payloads[0]) if job_payloads else empty_backup_job_status_state(),
        read_model_available=bool(overview.get("read_model_available", False)),
        has_more_jobs=bool(overview.get("has_more", False)),
        selected_job_id=selected_job_id,
    )


def backup_job_detail_from_response(response: IpcResponse | None) -> BackupJobDetailViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_backup_job_detail_state()
    detail = response.payload.get("backup_job_detail")
    if not isinstance(detail, dict):
        return empty_backup_job_detail_state()

    job_id = _required_text(detail.get("job_id"))
    read_model_available = bool(detail.get("read_model_available", False))
    found = bool(detail.get("found", False))
    job = detail.get("job")
    if not read_model_available:
        return empty_backup_job_detail_state()
    if not found or not isinstance(job, dict):
        return BackupJobDetailViewState(
            job_id=job_id,
            title="Jobben finnes ikke",
            source_label="Ingen aktiv jobbrevisjon funnet.",
            revision_label=f"Jobb: {job_id or 'ukjent'}",
            target_summary_label="Ingen mål konfigurert",
            defaults_summary_label="Standardvalg ikke lastet",
            target_lines=(),
            read_model_available=True,
            found=False,
        )
    return _job_detail_from_payload(job, job_id=job_id)


def activity_overview_from_response(response: IpcResponse | None) -> ActivityOverviewViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return ActivityOverviewViewState(
            job_status=None,
            read_model_available=False,
            has_more_runs=False,
        )
    overview = response.payload.get("activity_overview")
    if not isinstance(overview, dict):
        return ActivityOverviewViewState(
            job_status=None,
            read_model_available=False,
            has_more_runs=False,
        )

    runs = overview.get("runs")
    run_payloads = tuple(item for item in runs if isinstance(item, dict)) if isinstance(runs, list) else ()
    return ActivityOverviewViewState(
        job_status=_activity_status_from_run_payload(run_payloads[0]) if run_payloads else None,
        read_model_available=bool(overview.get("read_model_available", False)),
        has_more_runs=bool(overview.get("has_more", False)),
        latest_plan_id=_required_text(run_payloads[0].get("plan_id")) if run_payloads else None,
    )


def _build_steps(
    draft: BackupSetupDraft,
    current_step: BackupSetupStep,
) -> tuple[BackupSetupStepViewState, ...]:
    current_index = STEP_ORDER.index(current_step)
    return tuple(
        BackupSetupStepViewState(
            step=step,
            number=index + 1,
            title=STEP_TITLES[step],
            current=step is current_step,
            complete=_step_complete(draft, step) and index < current_index,
        )
        for index, step in enumerate(STEP_ORDER)
    )


def _step_complete(draft: BackupSetupDraft, step: BackupSetupStep) -> bool:
    if step is BackupSetupStep.SOURCE:
        return bool(draft.source_path_label)
    if step is BackupSetupStep.TARGETS:
        return 1 <= len(draft.targets) <= 3
    if step is BackupSetupStep.DEFAULTS:
        return True
    return bool(draft.source_path_label) and 1 <= len(draft.targets) <= 3


def _can_continue(draft: BackupSetupDraft, step: BackupSetupStep) -> bool:
    if step is BackupSetupStep.REVIEW:
        return _can_create(draft, step)
    return _step_complete(draft, step)


def _can_create(draft: BackupSetupDraft, step: BackupSetupStep) -> bool:
    return step is BackupSetupStep.REVIEW and _step_complete(draft, BackupSetupStep.REVIEW)


def _target_label(targets: tuple[BackupTargetDraft, ...]) -> str:
    if not targets:
        return "Ingen mål valgt"
    if len(targets) == 1:
        return f"1 mål: {targets[0].name}"
    return f"{len(targets)} mål: " + ", ".join(target.name for target in targets)


def _review_lines(
    draft: BackupSetupDraft,
    defaults: BackupDefaultsViewState,
) -> tuple[str, ...]:
    source = draft.source_path_label or "Kilde mangler"
    targets = _target_label(draft.targets)
    return (source, targets, *defaults.summary())


def _activity_label(activity: ActivityState) -> str:
    return {
        ActivityState.INACTIVE: "Inaktiv",
        ActivityState.CHECKING: "Kontrollerer",
        ActivityState.COPYING: "Kopierer",
        ActivityState.VERIFYING: "Verifiserer",
        ActivityState.PAUSED: "Pauset",
        ActivityState.RESTORING: "Gjenoppretter",
    }[activity]


def _attention_label(attention: AttentionState) -> str:
    return {
        AttentionState.BLOCKED: "Blokkert",
        AttentionState.NEEDS_ATTENTION: "Trenger oppmerksomhet",
        AttentionState.WAITING: "Venter",
        AttentionState.NORMAL: "Normal",
    }[attention]


def _freshness_label(freshness: FreshnessState) -> str:
    return {
        FreshnessState.UP_TO_DATE: "Oppdatert",
        FreshnessState.LAST_BACKED_UP: "Sist sikkerhetskopiert",
        FreshnessState.NEVER_CHECKED: "Aldri kontrollert",
        FreshnessState.UNKNOWN: "Ukjent",
    }[freshness]


def _setup_draft_from_payload(payload: object) -> BackupSetupDraft | None:
    if not isinstance(payload, dict):
        return None
    targets_payload = payload.get("targets")
    targets: list[BackupTargetDraft] = []
    if isinstance(targets_payload, list):
        for item in targets_payload:
            if not isinstance(item, dict):
                continue
            name = _required_text(item.get("name"))
            path_label = _required_text(item.get("path_label"))
            if name is None or path_label is None:
                continue
            targets.append(
                BackupTargetDraft(
                    name=name,
                    path_label=path_label,
                    independent_device_id=_optional_text(item.get("independent_device_id")),
                )
            )
    return BackupSetupDraft(
        source_name=_optional_text(payload.get("source_name")),
        source_path_label=_optional_text(payload.get("source_path_label")),
        targets=tuple(targets),
    )


def _job_status_from_payload(payload: dict[object, object]) -> BackupJobStatusViewState:
    targets_payload = payload.get("targets")
    targets: list[TargetStatusViewState] = []
    if isinstance(targets_payload, list):
        for item in targets_payload:
            if not isinstance(item, dict):
                continue
            name = _required_text(item.get("name"))
            if name is None:
                continue
            targets.append(
                target_status(
                    name=name,
                    activity=ActivityState.INACTIVE,
                    attention=AttentionState.WAITING,
                    freshness=FreshnessState.UNKNOWN,
                    recommended_action="Venter pÃ¥ analyse og kjÃ¸ring.",
                    independent_device_id=_optional_text(item.get("independent_device_id")),
                )
            )
    title = _required_text(payload.get("title")) or _required_text(payload.get("source_name")) or "Backupjobb"
    return build_backup_job_status_state(
        title=title,
        activity=ActivityState.INACTIVE,
        attention=AttentionState.WAITING,
        target_statuses=tuple(targets),
        recommended_action="Kontroller backupen nÃ¥r analysefunksjonen er tilgjengelig.",
    )


def _job_detail_from_payload(payload: dict[object, object], *, job_id: str | None) -> BackupJobDetailViewState:
    targets = _target_details_from_payload(payload.get("targets"))
    configured_target_count = _non_negative_int(payload.get("configured_target_count")) or len(targets)
    independent_device_count = _non_negative_int(payload.get("independent_device_count"))
    if independent_device_count is None:
        independent_device_count = len(
            {target.independent_device_id for target in targets if target.independent_device_id}
        )
    job_revision_id = _required_text(payload.get("job_revision_id"))
    filter_set_id = _required_text(payload.get("filter_set_id"))
    initial_plan = payload.get("initial_plan")
    plan_payload = initial_plan if isinstance(initial_plan, dict) else {}
    return BackupJobDetailViewState(
        job_id=_required_text(payload.get("job_id")) or job_id,
        title=(
            _required_text(payload.get("title"))
            or _required_text(payload.get("source_name"))
            or "Backupjobb"
        ),
        source_label=(
            _required_text(payload.get("source_path_label"))
            or _required_text(payload.get("source_name"))
            or "Kilde mangler"
        ),
        revision_label=_revision_label(job_revision_id, filter_set_id),
        target_summary_label=(
            f"{_count_label(configured_target_count, 'mål', 'mål')} / "
            f"{_count_label(independent_device_count, 'uavhengig enhet', 'uavhengige enheter')}"
        ),
        defaults_summary_label=_defaults_summary_from_payload(payload.get("defaults")),
        target_lines=tuple(_target_detail_line(target) for target in targets),
        read_model_available=True,
        found=True,
        plan_summary_label=_initial_plan_summary(plan_payload),
        plan_id=_optional_text(plan_payload.get("plan_id")),
        plan_checksum=_optional_text(plan_payload.get("plan_checksum")),
        plan_state=_optional_text(plan_payload.get("state")),
        plan_runnable=plan_payload.get("plan_runnable") is True,
    )


def _initial_plan_summary(payload: dict[object, object]) -> str:
    state = _optional_text(payload.get("state"))
    if state is None:
        return "Ingen plan ennå."
    reason_code = _required_text(payload.get("reason_code")) or "ukjent årsak"
    if state == "NO_CHANGES":
        return "Ingen endringer"
    if state == "BLOCKED":
        return f"Plan venter: {reason_code}"
    if state == "FAILED":
        return f"Planlegging feilet: {reason_code}"
    if state != "SEALED":
        return f"Planstatus: {state}"
    operation_count = _non_negative_int(payload.get("operation_count")) or 0
    planned_bytes = _non_negative_int(payload.get("planned_bytes")) or 0
    plan_id = _required_text(payload.get("plan_id")) or "ukjent plan"
    operation_word = "operasjon" if operation_count == 1 else "operasjoner"
    readiness = (
        "Klar til kontroll"
        if payload.get("plan_runnable") is True
        else "Kun forhåndsvisning"
    )
    return (
        f"{operation_count} {operation_word} fra {plan_id}. · "
        f"{planned_bytes} B · {readiness}"
    )


def _target_details_from_payload(payload: object) -> tuple[BackupJobDetailTargetViewState, ...]:
    if not isinstance(payload, list):
        return ()
    targets: list[BackupJobDetailTargetViewState] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = _required_text(item.get("name"))
        path_label = _required_text(item.get("path_label"))
        if name is None or path_label is None:
            continue
        targets.append(
            BackupJobDetailTargetViewState(
                name=name,
                path_label=path_label,
                independent_device_id=_optional_text(item.get("independent_device_id")),
                registration_state=_optional_text(item.get("registration_state")),
            )
        )
    return tuple(targets)


def _target_detail_line(target: BackupJobDetailTargetViewState) -> str:
    registration_label = {
        "WRITABLE_READY": "Skrivbar og registrert",
        "READ_ONLY_READY": "Skrivebeskyttet",
        "REGISTRATION_PENDING": "Registrering venter",
        "BLOCKED": "Blokkert",
    }.get(target.registration_state or "")
    base = f"{target.name}: {target.path_label}"
    return base if registration_label is None else f"{base} · {registration_label}"


def _revision_label(job_revision_id: str | None, filter_set_id: str | None) -> str:
    if job_revision_id and filter_set_id:
        return f"Revisjon: {job_revision_id} - Filter: {filter_set_id}"
    if job_revision_id:
        return f"Revisjon: {job_revision_id}"
    return "Ingen aktiv revisjon"


def _count_label(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _defaults_summary_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return "Standardvalg ikke lastet"
    labels = (
        _enum_label(payload.get("behavior"), {"UPDATE_BACKUP": "Oppdater backup"}),
        _enum_label(payload.get("file_selection"), {"ALL_USER_FILES": "Alle brukerfiler"}),
        _enum_label(payload.get("verification"), {"STANDARD": "Standard kontroll"}),
    )
    return " - ".join(label for label in labels if label)


def _enum_label(value: object, labels: dict[str, str]) -> str | None:
    text = _required_text(value)
    if text is None:
        return None
    return labels.get(text, text)


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _activity_status_from_run_payload(payload: dict[object, object]) -> BackupJobStatusViewState:
    state = _run_state(payload.get("state"))
    targets_payload = payload.get("targets")
    targets: list[TargetStatusViewState] = []
    if isinstance(targets_payload, list):
        for item in targets_payload:
            if not isinstance(item, dict):
                continue
            target_state = _run_target_state(item.get("state"))
            endpoint_id = _required_text(item.get("endpoint_id")) or "mål"
            targets.append(
                target_status(
                    name=endpoint_id,
                    activity=_target_activity(target_state),
                    attention=_target_attention(target_state),
                    freshness=_target_freshness(target_state),
                    recommended_action=_target_next_action(target_state),
                    independent_device_id=endpoint_id,
                )
            )
    run_id = _required_text(payload.get("run_id")) or "ukjent"
    return build_backup_job_status_state(
        title=f"Siste kjøring: {run_id}",
        activity=_run_activity(state),
        attention=_run_attention(state),
        target_statuses=tuple(targets),
        recommended_action=_run_next_action(state),
    )


def _run_state(value: object) -> RunState:
    if isinstance(value, str):
        try:
            return RunState(value)
        except ValueError:
            return RunState.CREATED
    return RunState.CREATED


def _run_target_state(value: object) -> RunTargetState:
    if isinstance(value, str):
        try:
            return RunTargetState(value)
        except ValueError:
            return RunTargetState.PENDING
    return RunTargetState.PENDING


def _run_activity(state: RunState) -> ActivityState:
    return {
        RunState.CREATED: ActivityState.CHECKING,
        RunState.QUEUED: ActivityState.CHECKING,
        RunState.PREFLIGHT: ActivityState.CHECKING,
        RunState.EXECUTING: ActivityState.COPYING,
        RunState.PAUSING: ActivityState.PAUSED,
        RunState.PAUSED: ActivityState.PAUSED,
        RunState.COMPLETED: ActivityState.INACTIVE,
        RunState.COMPLETED_WITH_WARNINGS: ActivityState.INACTIVE,
        RunState.PARTIAL_FAILURE: ActivityState.INACTIVE,
        RunState.FAILED: ActivityState.INACTIVE,
        RunState.CANCELLED: ActivityState.INACTIVE,
        RunState.BLOCKED_BY_SAFETY: ActivityState.INACTIVE,
        RunState.RECOVERY_REQUIRED: ActivityState.RESTORING,
    }[state]


def _run_attention(state: RunState) -> AttentionState:
    if state in {RunState.FAILED, RunState.BLOCKED_BY_SAFETY, RunState.RECOVERY_REQUIRED}:
        return AttentionState.BLOCKED
    if state in {RunState.COMPLETED_WITH_WARNINGS, RunState.PARTIAL_FAILURE, RunState.CANCELLED}:
        return AttentionState.NEEDS_ATTENTION
    if state in {RunState.CREATED, RunState.QUEUED, RunState.PREFLIGHT, RunState.PAUSING, RunState.PAUSED}:
        return AttentionState.WAITING
    return AttentionState.NORMAL


def _run_next_action(state: RunState) -> str:
    if state is RunState.QUEUED:
        return "Venter på lokal 0B-kjøringsmotor."
    if state is RunState.PREFLIGHT:
        return "Kontrollerer mål før lease og revalidering."
    if state is RunState.EXECUTING:
        return "Følg fremdrift per mål."
    if state in {RunState.FAILED, RunState.BLOCKED_BY_SAFETY, RunState.RECOVERY_REQUIRED}:
        return "Se gjennom blokkeringen før ny kjøring."
    if state in {RunState.COMPLETED, RunState.COMPLETED_WITH_WARNINGS}:
        return "Kontroller resultatet før neste backup."
    return "Vent på neste statusoppdatering."


def _target_activity(state: RunTargetState) -> ActivityState:
    if state in {RunTargetState.ACQUIRING_LEASE, RunTargetState.REVALIDATING}:
        return ActivityState.CHECKING
    if state is RunTargetState.EXECUTING:
        return ActivityState.COPYING
    if state is RunTargetState.PAUSED:
        return ActivityState.PAUSED
    if state is RunTargetState.RECOVERY_REQUIRED:
        return ActivityState.RESTORING
    return ActivityState.INACTIVE


def _target_attention(state: RunTargetState) -> AttentionState:
    if state in {RunTargetState.FAILED, RunTargetState.BLOCKED, RunTargetState.RECOVERY_REQUIRED}:
        return AttentionState.BLOCKED
    if state in {
        RunTargetState.WAITING_FOR_ENDPOINT,
        RunTargetState.NEEDS_REVIEW,
        RunTargetState.SUCCEEDED_WITH_WARNINGS,
        RunTargetState.CANCELLED,
    }:
        return AttentionState.NEEDS_ATTENTION
    if state in {RunTargetState.PENDING, RunTargetState.ACQUIRING_LEASE, RunTargetState.REVALIDATING}:
        return AttentionState.WAITING
    return AttentionState.NORMAL


def _target_freshness(state: RunTargetState) -> FreshnessState:
    if state is RunTargetState.SUCCEEDED:
        return FreshnessState.UP_TO_DATE
    if state is RunTargetState.SUCCEEDED_WITH_WARNINGS:
        return FreshnessState.LAST_BACKED_UP
    return FreshnessState.UNKNOWN


def _target_next_action(state: RunTargetState) -> str:
    if state is RunTargetState.PENDING:
        return "Venter på målbehandling."
    if state in {RunTargetState.ACQUIRING_LEASE, RunTargetState.REVALIDATING}:
        return "Kontrollerer måltilgang."
    if state is RunTargetState.EXECUTING:
        return "Kopiering pågår."
    if state in {RunTargetState.FAILED, RunTargetState.BLOCKED, RunTargetState.RECOVERY_REQUIRED}:
        return "Se gjennom målfeilen."
    return "Ingen handling kreves nå."


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _required_text(value: object) -> str | None:
    return _optional_text(value)
