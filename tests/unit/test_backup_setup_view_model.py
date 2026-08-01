from __future__ import annotations

import pytest

from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.backup_setup import (
    ActivityState,
    AttentionState,
    BackupSetupDraft,
    BackupSetupStep,
    BackupTargetDraft,
    FreshnessState,
    activity_overview_from_response,
    backup_job_detail_from_response,
    backup_overview_from_response,
    build_backup_job_status_state,
    build_standard_backup_setup_state,
    build_standard_backup_setup_state_from_job_draft,
    target_status,
)


def test_standard_backup_setup_has_four_steps_and_safe_defaults() -> None:
    state = build_standard_backup_setup_state(BackupSetupDraft.empty())

    assert [step.number for step in state.steps] == [1, 2, 3, 4]
    assert [step.title for step in state.steps] == [
        "Hva vil du beskytte?",
        "Hvor vil du ha kopier?",
        "Hvordan skal backupen fungere?",
        "Kontroller og opprett",
    ]
    assert state.defaults.summary() == (
        "Oppdater backup",
        "Alle brukerfiler",
        "Standard kontroll",
        "Tidligere versjoner beholdes i 30 dager",
        "Ekstra filer på målet beholdes",
        "Auto - anbefalt",
    )


def test_standard_backup_setup_blocks_creation_until_source_and_target_exist() -> None:
    empty = build_standard_backup_setup_state(BackupSetupDraft.empty())
    source_only = build_standard_backup_setup_state(
        BackupSetupDraft(source_name="Pictures", source_path_label="C:/Users/Ada/Pictures"),
        current_step=BackupSetupStep.REVIEW,
    )

    assert empty.can_continue is False
    assert empty.can_create is False
    assert source_only.can_create is False


def test_standard_backup_setup_allows_review_with_one_to_three_targets() -> None:
    draft = BackupSetupDraft(
        source_name="Pictures",
        source_path_label="C:/Users/Ada/Pictures",
        targets=(
            BackupTargetDraft(name="USB 1", path_label="E:/Backup"),
            BackupTargetDraft(name="USB 2", path_label="F:/Backup"),
            BackupTargetDraft(name="NAS", path_label="//nas/photo-backup"),
        ),
    )

    state = build_standard_backup_setup_state(draft, current_step=BackupSetupStep.REVIEW)

    assert state.configured_targets == 3
    assert state.max_targets == 3
    assert state.can_continue is True
    assert state.can_create is True
    assert state.primary_action_label == "Opprett og registrer"
    assert state.review_lines[:2] == (
        "C:/Users/Ada/Pictures",
        "3 mål: USB 1, USB 2, NAS",
    )


def test_standard_backup_setup_can_render_application_job_draft() -> None:
    draft = (
        StandardBackupJobDraft.new("draft-1")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )

    state = build_standard_backup_setup_state_from_job_draft(
        draft,
        current_step=BackupSetupStep.REVIEW,
    )

    assert state.source_label == "C:/Users/Ada/Pictures"
    assert state.target_label == "1 mål: USB 1"
    assert state.can_create is True
    assert state.review_lines[:2] == ("C:/Users/Ada/Pictures", "1 mål: USB 1")


def test_backup_job_status_keeps_activity_attention_and_freshness_separate() -> None:
    usb = target_status(
        name="USB 1",
        activity=ActivityState.COPYING,
        attention=AttentionState.NORMAL,
        freshness=FreshnessState.LAST_BACKED_UP,
        recommended_action="Vent til kopiering er ferdig.",
        independent_device_id="disk-a",
    )
    nas = target_status(
        name="NAS",
        activity=ActivityState.INACTIVE,
        attention=AttentionState.NEEDS_ATTENTION,
        freshness=FreshnessState.NEVER_CHECKED,
        recommended_action="Koble til NAS.",
        independent_device_id="nas-a",
    )

    state = build_backup_job_status_state(
        title="Bilder",
        activity=ActivityState.COPYING,
        attention=AttentionState.NEEDS_ATTENTION,
        target_statuses=(usb, nas),
        recommended_action="Følg opp NAS etter aktiv kopiering.",
    )

    assert state.activity_label == "Kopierer"
    assert state.attention_label == "Trenger oppmerksomhet"
    assert state.independent_device_count == 2
    assert [target.freshness_label for target in state.target_statuses] == [
        "Sist sikkerhetskopiert",
        "Aldri kontrollert",
    ]
    assert state.recommended_action == "Følg opp NAS etter aktiv kopiering."


def test_backup_overview_view_model_renders_draft_and_first_job_summary() -> None:
    response = IpcResponse.accepted(
        {
            "backup_overview": {
                "read_model_available": True,
                "has_more": False,
                "draft": {
                    "draft_id": "draft-a",
                    "source_name": "Pictures",
                    "source_path_label": "C:/Users/Ada/Pictures",
                    "targets": [
                        {
                            "name": "USB 1",
                            "path_label": "E:/Backup",
                            "independent_device_id": "disk-a",
                        }
                    ],
                },
                "jobs": [
                    {
                        "job_id": "job-a",
                        "title": "Pictures",
                        "source_name": "Pictures",
                        "source_path_label": "C:/Users/Ada/Pictures",
                        "targets": [
                            {
                                "name": "USB 1",
                                "path_label": "E:/Backup",
                                "independent_device_id": "disk-a",
                            }
                        ],
                    }
                ],
            }
        }
    )

    state = backup_overview_from_response(response)

    assert state.read_model_available is True
    assert state.setup.source_label == "C:/Users/Ada/Pictures"
    assert state.setup.target_label == "1 mål: USB 1"
    assert state.setup.can_create is True
    assert state.job_status.title == "Pictures"
    assert state.job_status.configured_target_count == 1
    assert state.job_status.target_statuses[0].freshness_label == "Ukjent"
    assert state.selected_job_id == "job-a"
    assert len(state.jobs) == 1
    assert state.jobs[0].job_id == "job-a"
    assert state.jobs[0].title == "Pictures"
    assert state.jobs[0].source_label == "C:/Users/Ada/Pictures"
    assert state.jobs[0].target_summary_label == "1 mål / 1 uavhengig enhet"
    assert state.limit == 10
    assert state.offset == 0


def test_backup_overview_view_model_preserves_bounded_job_page_metadata() -> None:
    response = IpcResponse.accepted(
        {
            "backup_overview": {
                "read_model_available": True,
                "limit": 2,
                "offset": 2,
                "has_more": True,
                "draft": None,
                "jobs": [
                    {
                        "job_id": "job-c",
                        "title": "Documents",
                        "source_path_label": "C:/Users/Ada/Documents",
                        "configured_target_count": 2,
                        "independent_device_count": 1,
                        "targets": [],
                    },
                    {
                        "title": "Missing identifier",
                        "source_path_label": "C:/ignored",
                        "targets": [],
                    },
                ],
            }
        }
    )

    state = backup_overview_from_response(response)

    assert state.read_model_available is True
    assert state.limit == 2
    assert state.offset == 2
    assert state.has_more_jobs is True
    assert [job.job_id for job in state.jobs] == ["job-c"]
    assert state.jobs[0].target_summary_label == "2 mål / 1 uavhengig enhet"
    assert state.selected_job_id == "job-c"


def test_backup_job_detail_view_model_renders_exact_job_revision() -> None:
    response = IpcResponse.accepted(
        {
            "backup_job_detail": {
                "job_id": "job-a",
                "read_model_available": True,
                "found": True,
                "job": {
                    "job_id": "job-a",
                    "job_revision_id": "job-rev-a",
                    "filter_set_id": "filter-a",
                    "title": "Pictures",
                    "source_name": "Pictures",
                    "source_path_label": "C:/Users/Ada/Pictures",
                    "configured_target_count": 1,
                    "independent_device_count": 1,
                    "defaults": {
                        "behavior": "UPDATE_BACKUP",
                        "file_selection": "ALL_USER_FILES",
                        "verification": "STANDARD",
                        "retention": "THIRTY_DAYS",
                        "extra_files": "KEEP_ON_TARGET",
                        "performance": "AUTO",
                    },
                    "targets": [
                        {
                            "name": "USB 1",
                            "path_label": "E:/Backup",
                            "independent_device_id": "disk-a",
                        }
                    ],
                    "initial_plan": {
                        "state": "SEALED",
                        "reason_code": "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW",
                        "analysis_id": "analysis-a",
                        "plan_id": "plan-a",
                        "plan_checksum": "a" * 64,
                        "operation_count": 3,
                        "planned_bytes": 256,
                        "plan_runnable": False,
                        "next_action": "Review the plan.",
                    },
                    "latest_analysis_request": {
                        "request_id": "request-a",
                        "state": "RUNNING",
                        "requested_utc": "2026-07-31T10:00:00Z",
                        "reason_code": None,
                        "analysis_id": None,
                        "plan_id": None,
                        "row_version": 2,
                    },
                },
            }
        }
    )

    state = backup_job_detail_from_response(response)

    assert state.read_model_available is True
    assert state.found is True
    assert state.job_id == "job-a"
    assert state.job_revision_id == "job-rev-a"
    assert state.writable_target_registration_required is False
    assert state.title == "Pictures"
    assert state.source_label == "C:/Users/Ada/Pictures"
    assert state.revision_label == "Revisjon: job-rev-a - Filter: filter-a"
    assert state.target_summary_label == "1 mål / 1 uavhengig enhet"
    assert state.defaults_summary_label == "Oppdater backup - Alle brukerfiler - Standard kontroll"
    assert state.target_lines == ("USB 1: E:/Backup",)
    assert state.plan_id == "plan-a"
    assert state.plan_checksum == "a" * 64
    assert state.plan_state == "SEALED"
    assert state.analysis_id == "analysis-a"
    assert state.analysis_request_id == "request-a"
    assert state.analysis_request_state == "RUNNING"
    assert state.plan_summary_label == (
        "3 operasjoner fra plan-a. · 256 B · Kun forhåndsvisning"
    )


@pytest.mark.parametrize(
    ("registration_states", "registration_required"),
    (
        (("REGISTRATION_PENDING",), True),
        (("REGISTRATION_PENDING", "REGISTRATION_PENDING"), True),
        (("WRITABLE_READY",), False),
        (("REGISTRATION_PENDING", "WRITABLE_READY"), False),
        ((), False),
    ),
)
def test_backup_job_detail_requires_explicit_registration_only_for_all_pending_targets(
    registration_states: tuple[str, ...],
    registration_required: bool,
) -> None:
    response = IpcResponse.accepted(
        {
            "backup_job_detail": {
                "job_id": "job-a",
                "read_model_available": True,
                "found": True,
                "job": {
                    "job_id": "job-a",
                    "job_revision_id": "job-rev-a",
                    "title": "Pictures",
                    "targets": [
                        {
                            "name": f"Target {index}",
                            "path_label": f"E:/Backup-{index}",
                            "registration_state": state,
                        }
                        for index, state in enumerate(registration_states, start=1)
                    ],
                },
            }
        }
    )

    state = backup_job_detail_from_response(response)

    assert state.job_revision_id == "job-rev-a"
    assert state.writable_target_registration_required is registration_required


def test_backup_job_detail_view_model_handles_missing_read_model() -> None:
    response = IpcResponse.accepted(
        {
            "backup_job_detail": {
                "job_id": "job-a",
                "read_model_available": False,
                "found": False,
                "job": None,
            }
        }
    )

    state = backup_job_detail_from_response(response)

    assert state.read_model_available is False
    assert state.found is False
    assert state.title == "Ingen lagret backupjobb"


def test_backup_job_detail_view_model_handles_not_found_job() -> None:
    response = IpcResponse.accepted(
        {
            "backup_job_detail": {
                "job_id": "job-missing",
                "read_model_available": True,
                "found": False,
                "job": None,
            }
        }
    )

    state = backup_job_detail_from_response(response)

    assert state.read_model_available is True
    assert state.found is False
    assert state.job_id == "job-missing"
    assert state.title == "Jobben finnes ikke"
    assert state.revision_label == "Jobb: job-missing"


def test_activity_overview_view_model_renders_latest_run_status() -> None:
    response = IpcResponse.accepted(
        {
            "activity_overview": {
                "read_model_available": True,
                "has_more": True,
                "runs": [
                    {
                        "run_id": "run-a",
                        "job_id": "job-a",
                        "job_revision_id": "job-rev-a",
                        "plan_id": "plan-a",
                        "state": "PREFLIGHT",
                        "trigger_type": "MANUAL_LOCAL_PREVIEW",
                        "started_utc": "2026-07-20T12:00:00.000Z",
                        "finished_utc": None,
                        "planned_operations": 1,
                        "planned_bytes": 128,
                        "warning_count": 0,
                        "error_count": 0,
                        "targets": [
                            {
                                "run_target_id": "run-a-target-0000",
                                "endpoint_id": "target-a",
                                "endpoint_revision_id": "target-rev-a",
                                "state": "REVALIDATING",
                                "planned_operations": 1,
                                "completed_operations": 0,
                                "planned_bytes": 128,
                                "completed_bytes": 0,
                                "warning_count": 0,
                                "error_count": 0,
                                "last_success_utc": "2026-07-19T12:05:00.000Z",
                            }
                        ],
                    }
                ],
            }
        }
    )

    state = activity_overview_from_response(response)

    assert state.read_model_available is True
    assert state.has_more_runs is True
    assert state.latest_plan_id == "plan-a"
    assert state.job_status is not None
    assert state.job_status.title == "Siste kjøring: run-a"
    assert state.job_status.activity_label == "Kontrollerer"
    assert state.job_status.attention_label == "Venter"
    assert state.job_status.target_statuses[0].activity_label == "Kontrollerer"
    assert state.job_status.target_statuses[0].freshness_label == (
        "Sist sikkerhetskopiert"
    )
    assert state.job_status.target_statuses[0].last_success_utc == (
        "2026-07-19T12:05:00.000Z"
    )
    assert state.job_status.target_statuses[0].recommended_action == (
        "Kontrollerer måltilgang."
    )


@pytest.mark.parametrize(
    ("target_state", "expected_action"),
    (
        ("PENDING", "Venter på målbehandling."),
        ("ACQUIRING_LEASE", "Kontrollerer måltilgang."),
        ("REVALIDATING", "Kontrollerer måltilgang."),
        ("WAITING_FOR_ENDPOINT", "Kontroller målet og prøv igjen."),
        ("EXECUTING", "Kopiering pågår."),
        ("PAUSED", "Fortsett backupen når du er klar."),
        ("NEEDS_REVIEW", "Se gjennom målresultatet."),
        ("SUCCEEDED", "Ingen handling kreves nå."),
        ("SUCCEEDED_WITH_WARNINGS", "Se gjennom målresultatet."),
        ("CANCELLED", "Kjør backupen på nytt når målet er klart."),
        ("FAILED", "Se gjennom målfeilen."),
        ("BLOCKED", "Se gjennom målfeilen."),
        ("RECOVERY_REQUIRED", "Se gjennom målfeilen."),
    ),
)
def test_activity_overview_maps_target_specific_next_actions(
    target_state: str,
    expected_action: str,
) -> None:
    response = IpcResponse.accepted(
        {
            "activity_overview": {
                "read_model_available": True,
                "runs": [
                    {
                        "run_id": "run-a",
                        "job_id": "job-a",
                        "state": "EXECUTING",
                        "targets": [
                            {
                                "endpoint_id": "target-a",
                                "state": target_state,
                            }
                        ],
                    }
                ],
            }
        }
    )

    state = activity_overview_from_response(response)

    assert state.job_status is not None
    assert state.job_status.target_statuses[0].recommended_action == expected_action
