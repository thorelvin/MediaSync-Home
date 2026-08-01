from __future__ import annotations

from pathlib import Path

from mediasync_home.application.job_drafts import (
    DraftTarget,
    StandardBackupJobDraft,
)
from mediasync_home.application.runtime_status import local_writable_status
from mediasync_home.composition.engine_host import build_engine_host_runtime
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcStatus
from mediasync_home.presentation.engine_client import EngineClient


REQUEST_ID = "11111111-1111-4111-8111-111111111111"
IDEMPOTENCY_KEY = "22222222-2222-4222-8222-222222222222"


def test_job_edit_command_rebinds_target_queues_check_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    source = tmp_path / "source"
    first_target = tmp_path / "target-one"
    second_target = tmp_path / "target-two"
    source.mkdir()
    first_target.mkdir()
    second_target.mkdir()
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        installation_id="local-edit-test",
    )
    try:
        client = _client(runtime.service)
        created = client.create_standard_backup_job(
            draft=StandardBackupJobDraft(
                draft_id="33333333-3333-4333-8333-333333333333",
                source_name="Pictures",
                source_path_label=str(source),
                targets=(
                    DraftTarget(name="Target one", path_label=str(first_target)),
                ),
            ),
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="55555555-5555-4555-8555-555555555555",
        )
        assert created.status is IpcStatus.ACCEPTED
        created_job = created.payload["job"]
        assert isinstance(created_job, dict)
        job_id = created_job["job_id"]
        expected_revision_id = created_job["job_revision_id"]
        assert isinstance(job_id, str)
        assert isinstance(expected_revision_id, str)
        detail = client.get_backup_job_detail(job_id=job_id)
        detail_payload = detail.payload["backup_job_detail"]
        assert isinstance(detail_payload, dict)
        detail_job = detail_payload["job"]
        assert isinstance(detail_job, dict)
        lifecycle_row_version = detail_job["lifecycle_row_version"]
        assert isinstance(lifecycle_row_version, int)

        edited = client.update_standard_backup_job(
            job_id=job_id,
            expected_job_revision_id=expected_revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            draft=StandardBackupJobDraft(
                draft_id="66666666-6666-4666-8666-666666666666",
                source_name="Pictures renamed",
                source_path_label=str(source),
                targets=(
                    DraftTarget(name="Target two", path_label=str(second_target)),
                ),
            ),
            check_after_save=True,
            request_id=REQUEST_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

        assert edited.status is IpcStatus.ACCEPTED
        edit = edited.payload["job_edit"]
        edited_job = edited.payload["job"]
        registration = edited.payload["writable_endpoint_registration"]
        analysis = edited.payload["analysis_request"]
        assert isinstance(edit, dict)
        assert isinstance(edited_job, dict)
        assert isinstance(registration, dict)
        assert isinstance(analysis, dict)
        assert edit["saved"] is True
        assert edit["requires_full_check"] is True
        assert edit["check_queued"] is True
        assert edit["changed_fields"] == ["name", "targets"]
        assert registration["completed"] is True
        assert analysis["job_revision_id"] == edited_job["job_revision_id"]
        assert analysis["state"] == "QUEUED"
        assert (second_target / ".mediasync" / "endpoint.json").is_file()
        assert runtime.catalog_connection is not None
        revision_count = _count(runtime.catalog_connection, "job_revisions")
        analysis_count = _count(runtime.catalog_connection, "backup_analysis_requests")

        replay = client.update_standard_backup_job(
            job_id=job_id,
            expected_job_revision_id=expected_revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            draft=StandardBackupJobDraft(
                draft_id="66666666-6666-4666-8666-666666666666",
                source_name="Pictures renamed",
                source_path_label=str(source),
                targets=(
                    DraftTarget(name="Target two", path_label=str(second_target)),
                ),
            ),
            check_after_save=True,
            request_id=REQUEST_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )

        assert replay.status is IpcStatus.ACCEPTED
        assert replay.payload["job_edit"]["idempotent_replay"] is True
        assert _count(runtime.catalog_connection, "job_revisions") == revision_count
        assert (
            _count(runtime.catalog_connection, "backup_analysis_requests")
            == analysis_count
        )
    finally:
        runtime.close()

    restarted = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        installation_id="local-edit-test",
    )
    try:
        client = _client(restarted.service)
        detail = client.get_backup_job_detail(job_id=job_id)
        detail_payload = detail.payload["backup_job_detail"]
        assert isinstance(detail_payload, dict)
        detail_job = detail_payload["job"]
        assert isinstance(detail_job, dict)
        assert detail_job["source_name"] == "Pictures renamed"
        assert detail_job["targets"][0]["path_label"] == str(second_target)
        replay = client.update_standard_backup_job(
            job_id=job_id,
            expected_job_revision_id=expected_revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            draft=StandardBackupJobDraft(
                draft_id="66666666-6666-4666-8666-666666666666",
                source_name="Pictures renamed",
                source_path_label=str(source),
                targets=(
                    DraftTarget(name="Target two", path_label=str(second_target)),
                ),
            ),
            check_after_save=True,
            request_id=REQUEST_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        )
        assert replay.status is IpcStatus.ACCEPTED
        assert replay.payload["job_edit"]["idempotent_replay"] is True
        assert restarted.catalog_connection is not None
        assert _count(restarted.catalog_connection, "job_revisions") == revision_count
        assert (
            _count(restarted.catalog_connection, "backup_analysis_requests")
            == analysis_count
        )
    finally:
        restarted.close()


def test_job_edit_command_rejects_safety_changes_during_active_run(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "photo.txt").write_text("content", encoding="utf-8")
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        installation_id="local-active-edit-test",
    )
    try:
        client = _client(runtime.service)
        created = client.create_standard_backup_job(
            draft=StandardBackupJobDraft(
                draft_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                source_name="Pictures",
                source_path_label=str(source),
                targets=(DraftTarget(name="Target", path_label=str(target)),),
            ),
            request_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            idempotency_key="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        )
        assert created.status is IpcStatus.ACCEPTED
        job = created.payload["job"]
        assert isinstance(job, dict)
        job_id = job["job_id"]
        revision_id = job["job_revision_id"]
        assert isinstance(job_id, str)
        assert isinstance(revision_id, str)
        detail = client.get_backup_job_detail(job_id=job_id)
        detail_result = detail.payload["backup_job_detail"]
        assert isinstance(detail_result, dict)
        detail_job = detail_result["job"]
        assert isinstance(detail_job, dict)
        plan = detail_job["initial_plan"]
        assert isinstance(plan, dict)
        plan_id = plan["plan_id"]
        plan_checksum = plan["plan_checksum"]
        lifecycle_row_version = detail_job["lifecycle_row_version"]
        assert isinstance(plan_id, str)
        assert isinstance(plan_checksum, str)
        assert isinstance(lifecycle_row_version, int)
        started = client.start_backup(
            plan_id=plan_id,
            plan_checksum=plan_checksum,
            request_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            idempotency_key="ffffffff-ffff-4fff-8fff-ffffffffffff",
        )
        assert started.status is IpcStatus.ACCEPTED
        assert runtime.catalog_connection is not None
        revision_count = _count(runtime.catalog_connection, "job_revisions")

        edited = client.update_standard_backup_job(
            job_id=job_id,
            expected_job_revision_id=revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            draft=StandardBackupJobDraft(
                draft_id="12121212-1212-4212-8212-121212121212",
                source_name="Pictures",
                source_path_label=str(source),
                targets=(
                    DraftTarget(
                        name="Target changed",
                        path_label=str(tmp_path / "other-target"),
                    ),
                ),
            ),
            check_after_save=False,
            request_id="13131313-1313-4313-8313-131313131313",
            idempotency_key="14141414-1414-4414-8414-141414141414",
        )

        assert edited.status is IpcStatus.REJECTED
        assert edited.payload["job_edit"]["validation_code"] == (
            "STANDARD_BACKUP_JOB_ACTIVE_RUN"
        )
        assert _count(runtime.catalog_connection, "job_revisions") == revision_count
    finally:
        runtime.close()


def test_job_edit_command_can_save_changes_without_queueing_check(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    source = tmp_path / "source"
    first_target = tmp_path / "target-one"
    second_target = tmp_path / "target-two"
    source.mkdir()
    first_target.mkdir()
    second_target.mkdir()
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        installation_id="local-save-edit-test",
    )
    try:
        client = _client(runtime.service)
        created = client.create_standard_backup_job(
            draft=StandardBackupJobDraft(
                draft_id="15151515-1515-4515-8515-151515151515",
                source_name="Pictures",
                source_path_label=str(source),
                targets=(
                    DraftTarget(name="Target one", path_label=str(first_target)),
                ),
            ),
            request_id="16161616-1616-4616-8616-161616161616",
            idempotency_key="17171717-1717-4717-8717-171717171717",
        )
        assert created.status is IpcStatus.ACCEPTED
        job = created.payload["job"]
        assert isinstance(job, dict)
        job_id = job["job_id"]
        revision_id = job["job_revision_id"]
        assert isinstance(job_id, str)
        assert isinstance(revision_id, str)
        detail = client.get_backup_job_detail(job_id=job_id)
        detail_result = detail.payload["backup_job_detail"]
        assert isinstance(detail_result, dict)
        detail_job = detail_result["job"]
        assert isinstance(detail_job, dict)
        lifecycle_row_version = detail_job["lifecycle_row_version"]
        assert isinstance(lifecycle_row_version, int)

        edited = client.update_standard_backup_job(
            job_id=job_id,
            expected_job_revision_id=revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            draft=StandardBackupJobDraft(
                draft_id="18181818-1818-4818-8818-181818181818",
                source_name="Pictures",
                source_path_label=str(source),
                targets=(
                    DraftTarget(name="Target two", path_label=str(second_target)),
                ),
            ),
            check_after_save=False,
            request_id="19191919-1919-4919-8919-191919191919",
            idempotency_key="20202020-2020-4020-8020-202020202020",
        )

        assert edited.status is IpcStatus.ACCEPTED
        edit = edited.payload["job_edit"]
        assert isinstance(edit, dict)
        assert edit["saved"] is True
        assert edit["requires_full_check"] is True
        assert edit["check_queued"] is False
        assert edit["validation_code"] == "STANDARD_BACKUP_JOB_UPDATED_NEEDS_CHECK"
        assert "analysis_request" not in edited.payload
        assert runtime.catalog_connection is not None
        assert _count(runtime.catalog_connection, "backup_analysis_requests") == 0
    finally:
        runtime.close()


def _authorization() -> ClientAuthorizationPolicy:
    return ClientAuthorizationPolicy(
        expected_user_sid_hash="same-user",
        expected_session_id=42,
    )


def _client(service: object) -> EngineClient:
    ipc_client = InProcessIpcClient(
        service=service,  # type: ignore[arg-type]
        identity=VerifiedClientIdentity(
            user_sid_hash="same-user",
            session_id=42,
            is_remote=False,
            transport="job-edit-integration-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    connected = ipc_client.connect()
    assert connected.status is IpcStatus.ACCEPTED
    return EngineClient(ipc_client)


def _count(connection: object, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()  # type: ignore[attr-defined]
    assert row is not None
    return int(row[0])
