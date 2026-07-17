from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
            "Opprett og kontroller endringer" if current_step is BackupSetupStep.REVIEW else "Fortsett"
        ),
        can_continue=can_continue,
        can_create=can_create,
        review_lines=_review_lines(draft, defaults),
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
