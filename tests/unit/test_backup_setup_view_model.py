from __future__ import annotations

from mediasync_home.application.job_drafts import StandardBackupJobDraft
from mediasync_home.ipc.protocol import IpcResponse
from mediasync_home.presentation.view_models.backup_setup import (
    ActivityState,
    AttentionState,
    BackupSetupDraft,
    BackupSetupStep,
    BackupTargetDraft,
    FreshnessState,
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
    assert state.primary_action_label == "Opprett og kontroller endringer"
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
