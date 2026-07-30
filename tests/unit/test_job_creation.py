from __future__ import annotations

import pytest

from mediasync_home.application.job_creation import (
    CreateStandardBackupJobCommand,
    JobCreationPayloadError,
    SealedStandardBackupJob,
    StandardBackupJobCatalog,
    StandardBackupJobIdFactory,
    StandardBackupJobIds,
    create_standard_backup_job_from_draft,
    evaluate_standard_backup_job_creation,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_drafts import JobDraftStore, StandardBackupJobDraft


class InMemoryJobDraftStore(JobDraftStore):
    def __init__(self) -> None:
        self._drafts: dict[str, StandardBackupJobDraft] = {}

    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None:
        self._drafts[draft.draft_id] = draft

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None:
        return self._drafts.get(draft_id)


class InMemoryStandardBackupJobCatalog(StandardBackupJobCatalog):
    def __init__(self) -> None:
        self.jobs: dict[str, SealedStandardBackupJob] = {}
        self.idempotency_keys: dict[str, str] = {}

    def save_standard_backup_job(self, job: SealedStandardBackupJob) -> None:
        self.jobs[job.job_id] = job
        self.idempotency_keys[job.idempotency_key] = job.job_id

    def load_standard_backup_job(self, job_id: str) -> SealedStandardBackupJob | None:
        return self.jobs.get(job_id)

    def list_active_standard_backup_jobs(self) -> tuple[SealedStandardBackupJob, ...]:
        return tuple(self.jobs[job_id] for job_id in sorted(self.jobs))

    def load_standard_backup_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SealedStandardBackupJob | None:
        job_id = self.idempotency_keys.get(idempotency_key)
        if job_id is None:
            return None
        return self.jobs[job_id]


class FixedStandardBackupJobIdFactory(StandardBackupJobIdFactory):
    def __init__(
        self,
        ids: tuple[StandardBackupJobIds, ...] = (
            StandardBackupJobIds(
                job_id="job-a",
                job_revision_id="job-rev-a",
                filter_set_id="filter-a",
            ),
        ),
    ) -> None:
        self.calls = 0
        self._ids = ids

    def new_standard_backup_job_ids(self) -> StandardBackupJobIds:
        ids = self._ids[min(self.calls, len(self._ids) - 1)]
        self.calls += 1
        return ids


def test_parse_create_standard_backup_job_command_requires_draft_id() -> None:
    with pytest.raises(JobCreationPayloadError, match="CREATE_STANDARD_BACKUP_JOB_REQUIRES_DRAFT_ID"):
        parse_create_standard_backup_job_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={},
        )


def test_parse_create_standard_backup_job_command_accepts_reviewed_inline_draft() -> None:
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={
            "draft_id": "draft-a",
            "draft": {
                "draft_id": "draft-a",
                "schema_version": 1,
                "source_name": "Pictures",
                "source_path_label": "C:/Users/Ada/Pictures",
                "targets": [
                    {
                        "name": "USB 1",
                        "path_label": "E:/Backup",
                        "independent_device_id": None,
                    }
                ],
            },
        },
    )

    assert command.inline_draft is not None
    assert command.inline_draft.draft_id == "draft-a"
    assert command.inline_draft.source_path_label == "C:/Users/Ada/Pictures"
    assert command.inline_draft.targets[0].path_label == "E:/Backup"
    assert command.inline_draft.can_create() is True


def test_parse_create_standard_backup_job_command_rejects_inline_draft_id_mismatch() -> None:
    with pytest.raises(
        JobCreationPayloadError,
        match="CREATE_STANDARD_BACKUP_JOB_DRAFT_ID_MISMATCH",
    ):
        parse_create_standard_backup_job_command(
            request_id="request-a",
            idempotency_key="idempotency-a",
            payload={
                "draft_id": "draft-a",
                "draft": {
                    "draft_id": "draft-b",
                    "source_name": "Pictures",
                    "source_path_label": "C:/Users/Ada/Pictures",
                    "targets": [],
                },
            },
        )


def test_job_creation_readiness_reports_missing_draft() -> None:
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-missing"},
    )

    readiness = evaluate_standard_backup_job_creation(
        command=command,
        drafts=InMemoryJobDraftStore(),
    )

    assert readiness.draft_found is False
    assert readiness.draft_valid is False
    assert readiness.validation_codes == ("DRAFT_NOT_FOUND",)


def test_job_creation_readiness_reports_incomplete_draft() -> None:
    store = InMemoryJobDraftStore()
    store.save_standard_backup_draft(StandardBackupJobDraft.new("draft-a"))
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-a"},
    )

    readiness = evaluate_standard_backup_job_creation(command=command, drafts=store)

    assert readiness.draft_found is True
    assert readiness.draft_valid is False
    assert readiness.validation_codes == (
        "SOURCE_REQUIRED",
        "SOURCE_LABEL_REQUIRED",
        "TARGET_REQUIRED",
    )


def test_job_creation_readiness_accepts_complete_draft_but_keeps_creation_disabled() -> None:
    store = InMemoryJobDraftStore()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup")
    )
    store.save_standard_backup_draft(draft)
    command = parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-a"},
    )

    readiness = evaluate_standard_backup_job_creation(command=command, drafts=store)

    assert readiness.draft_found is True
    assert readiness.draft_valid is True
    assert readiness.validation_codes == ()
    assert readiness.next_action == "Backup job creation is recognized but disabled in the 0B local preview."


def test_job_creation_readiness_blocks_overlapping_roots() -> None:
    store = InMemoryJobDraftStore()
    draft = (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="Nested target", path_label="C:/Users/Ada/Pictures/Phone")
    )
    store.save_standard_backup_draft(draft)

    readiness = evaluate_standard_backup_job_creation(command=_create_command(), drafts=store)

    assert readiness.draft_found is True
    assert readiness.draft_valid is False
    assert readiness.validation_codes == ("TARGET_ROOT_OVERLAPS_SOURCE",)


def test_create_standard_backup_job_persists_sealed_revision_from_valid_draft() -> None:
    drafts = InMemoryJobDraftStore()
    catalog = InMemoryStandardBackupJobCatalog()
    id_factory = FixedStandardBackupJobIdFactory()
    drafts.save_standard_backup_draft(_complete_draft())
    command = _create_command()

    outcome = create_standard_backup_job_from_draft(
        command=command,
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )

    assert outcome.created is True
    assert outcome.idempotent_replay is False
    assert outcome.readiness.draft_valid is True
    assert outcome.readiness.next_action == "Standard backup job revision is persisted; plan sealing is pending."
    assert outcome.job is not None
    assert outcome.job.job_id == "job-a"
    assert outcome.job.job_revision_id == "job-rev-a"
    assert outcome.job.filter_set_id == "filter-a"
    assert outcome.job.draft_id == "draft-a"
    assert outcome.job.command_request_id == "request-a"
    assert outcome.job.idempotency_key == "idempotency-a"
    assert outcome.job.source_path_label == "C:/Users/Ada/Pictures"
    assert [target.name for target in outcome.job.targets] == ["USB 1"]
    assert catalog.load_standard_backup_job("job-a") == outcome.job
    assert id_factory.calls == 1


def test_create_standard_backup_job_does_not_persist_invalid_draft() -> None:
    drafts = InMemoryJobDraftStore()
    catalog = InMemoryStandardBackupJobCatalog()
    id_factory = FixedStandardBackupJobIdFactory()
    drafts.save_standard_backup_draft(StandardBackupJobDraft.new("draft-a"))

    outcome = create_standard_backup_job_from_draft(
        command=_create_command(),
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )

    assert outcome.created is False
    assert outcome.job is None
    assert outcome.readiness.validation_codes == (
        "SOURCE_REQUIRED",
        "SOURCE_LABEL_REQUIRED",
        "TARGET_REQUIRED",
    )
    assert catalog.jobs == {}
    assert id_factory.calls == 0


def test_create_standard_backup_job_does_not_persist_overlapping_roots() -> None:
    drafts = InMemoryJobDraftStore()
    catalog = InMemoryStandardBackupJobCatalog()
    id_factory = FixedStandardBackupJobIdFactory()
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="Nested target", path_label="C:/Users/Ada/Pictures/Phone")
    )

    outcome = create_standard_backup_job_from_draft(
        command=_create_command(),
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )

    assert outcome.created is False
    assert outcome.job is None
    assert outcome.readiness.validation_codes == ("TARGET_ROOT_OVERLAPS_SOURCE",)
    assert catalog.jobs == {}
    assert id_factory.calls == 0


def test_create_standard_backup_job_replays_existing_idempotency_key() -> None:
    drafts = InMemoryJobDraftStore()
    catalog = InMemoryStandardBackupJobCatalog()
    id_factory = FixedStandardBackupJobIdFactory()
    drafts.save_standard_backup_draft(_complete_draft())
    command = _create_command()

    first = create_standard_backup_job_from_draft(
        command=command,
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )
    second = create_standard_backup_job_from_draft(
        command=command,
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )

    assert first.created is True
    assert second.created is False
    assert second.idempotent_replay is True
    assert second.job == first.job
    assert id_factory.calls == 1


def test_create_standard_backup_job_blocks_overlap_with_existing_writable_root() -> None:
    drafts = InMemoryJobDraftStore()
    catalog = InMemoryStandardBackupJobCatalog()
    id_factory = FixedStandardBackupJobIdFactory()
    drafts.save_standard_backup_draft(_complete_draft())
    first = create_standard_backup_job_from_draft(
        command=_create_command(),
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )
    assert first.created is True
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-b")
        .with_source(name="Camera", path_label="D:/Camera")
        .with_added_target(name="Nested target", path_label="E:/Backup/Phone")
    )

    blocked = create_standard_backup_job_from_draft(
        command=parse_create_standard_backup_job_command(
            request_id="request-b",
            idempotency_key="idempotency-b",
            payload={"draft_id": "draft-b"},
        ),
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )

    assert blocked.created is False
    assert blocked.job is None
    assert blocked.readiness.validation_codes == ("STANDARD_BACKUP_JOB_ROOT_OVERLAPS_EXISTING_JOB",)
    assert id_factory.calls == 1


def test_create_standard_backup_job_allows_source_only_overlap_with_existing_job() -> None:
    drafts = InMemoryJobDraftStore()
    catalog = InMemoryStandardBackupJobCatalog()
    id_factory = FixedStandardBackupJobIdFactory(
        (
            StandardBackupJobIds(
                job_id="job-a",
                job_revision_id="job-rev-a",
                filter_set_id="filter-a",
            ),
            StandardBackupJobIds(
                job_id="job-b",
                job_revision_id="job-rev-b",
                filter_set_id="filter-b",
            ),
        )
    )
    drafts.save_standard_backup_draft(_complete_draft())
    first = create_standard_backup_job_from_draft(
        command=_create_command(),
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )
    assert first.created is True
    drafts.save_standard_backup_draft(
        StandardBackupJobDraft.new("draft-b")
        .with_source(name="Pictures child", path_label="C:/Users/Ada/Pictures/Phone")
        .with_added_target(name="USB 2", path_label="F:/Backup")
    )

    second = create_standard_backup_job_from_draft(
        command=parse_create_standard_backup_job_command(
            request_id="request-b",
            idempotency_key="idempotency-b",
            payload={"draft_id": "draft-b"},
        ),
        drafts=drafts,
        catalog=catalog,
        id_factory=id_factory,
    )

    assert second.created is True
    assert second.job is not None
    assert second.job.job_id == "job-b"
    assert id_factory.calls == 2


def _complete_draft() -> StandardBackupJobDraft:
    return (
        StandardBackupJobDraft.new("draft-a")
        .with_source(name="Pictures", path_label="C:/Users/Ada/Pictures")
        .with_added_target(name="USB 1", path_label="E:/Backup", independent_device_id="disk-a")
    )


def _create_command() -> CreateStandardBackupJobCommand:
    return parse_create_standard_backup_job_command(
        request_id="request-a",
        idempotency_key="idempotency-a",
        payload={"draft_id": "draft-a"},
    )
