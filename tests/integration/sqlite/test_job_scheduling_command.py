from __future__ import annotations

from pathlib import Path

from mediasync_home.application.job_drafts import DraftTarget, StandardBackupJobDraft
from mediasync_home.application.runtime_status import local_writable_status
from mediasync_home.application.trigger_occurrences import (
    TriggerCommandName,
    TriggerDeliveryContext,
    TriggerKind,
    build_enqueue_trigger_occurrence_payload,
    payload_hash,
)
from mediasync_home.composition.engine_host import build_engine_host_runtime
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcStatus
from mediasync_home.presentation.engine_client import EngineClient


SCHEDULE_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
SCHEDULE_IDEMPOTENCY_KEY = "22222222-2222-4222-8222-222222222222"


def test_daily_schedule_command_is_atomic_durable_and_replayable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    source = tmp_path / "source"
    target = tmp_path / "target"
    executable = tmp_path / "MediaSyncHome.exe"
    source.mkdir()
    target.mkdir()
    executable.touch()
    (source / "photo.txt").write_text("real file content", encoding="utf-8")
    runtime = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        installation_id="schedule-integration-test",
        task_scheduler_executable_path=str(executable),
        task_scheduler_time_zone_id="W. Europe Standard Time",
    )
    try:
        client = _client(runtime.service)
        created = client.create_standard_backup_job(
            draft=StandardBackupJobDraft(
                draft_id="33333333-3333-4333-8333-333333333333",
                source_name="Pictures",
                source_path_label=str(source),
                targets=(DraftTarget(name="Target", path_label=str(target)),),
            ),
            request_id="44444444-4444-4444-8444-444444444444",
            idempotency_key="55555555-5555-4555-8555-555555555555",
        )
        assert created.status is IpcStatus.ACCEPTED
        created_job = created.payload["job"]
        assert isinstance(created_job, dict)
        job_id = created_job["job_id"]
        job_revision_id = created_job["job_revision_id"]
        assert isinstance(job_id, str)
        assert isinstance(job_revision_id, str)

        checked = client.check_backup(
            job_id=job_id,
            request_id="66666666-6666-4666-8666-666666666666",
            idempotency_key="77777777-7777-4777-8777-777777777777",
            start_when_safe=False,
        )
        assert checked.status is IpcStatus.ACCEPTED
        completed_analysis = runtime.run_backup_analysis_cycle()
        assert completed_analysis is not None
        detail = _detail_job(client, job_id)
        initial_plan = detail["initial_plan"]
        assert isinstance(initial_plan, dict)
        assert initial_plan["state"] == "SEALED"
        assert initial_plan["plan_runnable"] is True
        lifecycle_row_version = detail["lifecycle_row_version"]
        assert isinstance(lifecycle_row_version, int)

        configured = client.configure_daily_backup_schedule(
            job_id=job_id,
            expected_job_revision_id=job_revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            expected_schedule_row_version=0,
            enabled=True,
            local_time="21:30",
            request_id=SCHEDULE_REQUEST_ID,
            idempotency_key=SCHEDULE_IDEMPOTENCY_KEY,
        )

        assert configured.status is IpcStatus.ACCEPTED
        assert configured.payload["configured"] is True
        assert configured.payload["reconciliation_state"] == "PENDING"
        schedule = configured.payload["schedule"]
        assert isinstance(schedule, dict)
        assert schedule["plan_id"] == initial_plan["plan_id"]
        assert schedule["plan_checksum"] == initial_plan["plan_checksum"]
        assert schedule["time_zone_id"] == "W. Europe Standard Time"
        assert schedule["task_logon_type"] == "INTERACTIVE_TOKEN"
        assert schedule["run_only_when_logged_on"] is True
        assert runtime.catalog_connection is not None
        assert _count(runtime.catalog_connection, "schedules") == 1
        assert _count(runtime.catalog_connection, "external_resource_state") == 1
        assert _schedule_receipt_state(runtime.catalog_connection) == "SUCCEEDED"

        (source / "after-schedule.txt").write_text(
            "fresh scheduled content",
            encoding="utf-8",
        )
        delivery_id = "88888888-8888-4888-8888-888888888888"
        trigger_payload = build_enqueue_trigger_occurrence_payload(
            schedule_id=str(schedule["schedule_id"]),
            schedule_revision_hash=str(schedule["desired_definition_hash"]),
            delivery=TriggerDeliveryContext(
                delivery_id=delivery_id,
                observed_start_utc="2026-08-02T21:30:00.000Z",
                scheduled_slot_utc="2026-08-02T21:30:00.000Z",
                trigger_kind=TriggerKind.SCHEDULED_TIME,
                task_definition_hash=str(schedule["desired_definition_hash"]),
            ),
        )
        trigger_client = _trigger_client(runtime.service)
        triggered = trigger_client.submit_command(
            TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
            request_id=delivery_id,
            idempotency_key=delivery_id,
            payload=trigger_payload,
            payload_hash=payload_hash(trigger_payload),
        )
        assert triggered.status is IpcStatus.ACCEPTED
        assert triggered.payload["analysis_request"]["state"] == "QUEUED"
        occurrence_payload = triggered.payload["occurrence"]
        assert isinstance(occurrence_payload, dict)
        occurrence_id = occurrence_payload["occurrence_id"]
        assert isinstance(occurrence_id, str)

        scheduled_analysis = runtime.run_backup_analysis_cycle()
        assert scheduled_analysis is not None
        assert scheduled_analysis.state.value == "SUCCEEDED", (
            scheduled_analysis.reason_code
        )
        assert scheduled_analysis.reason_code == "BACKUP_ANALYSIS_SAFE_RUN_QUEUED"
        assert scheduled_analysis.started_run_id is not None
        assert scheduled_analysis.plan_id != schedule["plan_id"]
        occurrence_store = runtime.service.trigger_occurrence_store
        assert occurrence_store is not None
        occurrence = occurrence_store.load_trigger_occurrence(occurrence_id)
        assert occurrence is not None
        assert occurrence.state.value == "RUN_ENQUEUED"
        assert occurrence.run_id == scheduled_analysis.started_run_id

        executor = runtime.run_executor_cycle(max_steps=100)
        assert executor.steps_attempted > 0
        assert runtime.run_executor_queue_store is not None
        completed_run = runtime.run_executor_queue_store.load_started_run(
            scheduled_analysis.started_run_id
        )
        assert completed_run is not None
        assert completed_run.state.value == "COMPLETED", executor
        assert completed_run.trigger_occurrence_id == occurrence_id
        terminal_occurrence = occurrence_store.load_trigger_occurrence(occurrence_id)
        assert terminal_occurrence is not None
        assert terminal_occurrence.state.value == "SUCCEEDED"
        assert terminal_occurrence.run_id == completed_run.run_id
        assert terminal_occurrence.terminal_effect_hash is not None
        assert len(terminal_occurrence.terminal_effect_hash) == 64
        assert (target / "after-schedule.txt").read_text(encoding="utf-8") == (
            "fresh scheduled content"
        )
    finally:
        runtime.close()

    restarted = build_engine_host_runtime(
        authorization=_authorization(),
        service_status=local_writable_status(ProcessRole.ENGINE_HOST),
        state_root=state_root,
        installation_id="schedule-integration-test",
        task_scheduler_executable_path=str(executable),
        task_scheduler_time_zone_id="W. Europe Standard Time",
    )
    try:
        client = _client(restarted.service)
        detail = _detail_job(client, job_id)
        persisted = detail["automation_schedule"]
        assert isinstance(persisted, dict)
        assert persisted["enabled"] is True
        assert persisted["daily_local_time"] == "21:30"
        assert persisted["row_version"] == 1
        assert persisted["reconciliation_state"] == "PENDING"

        replay = client.configure_daily_backup_schedule(
            job_id=job_id,
            expected_job_revision_id=job_revision_id,
            expected_lifecycle_row_version=lifecycle_row_version,
            expected_schedule_row_version=0,
            enabled=True,
            local_time="21:30",
            request_id=SCHEDULE_REQUEST_ID,
            idempotency_key=SCHEDULE_IDEMPOTENCY_KEY,
        )

        assert replay.status is IpcStatus.ACCEPTED
        assert replay.payload["idempotent_replay"] is True
        assert restarted.catalog_connection is not None
        assert _count(restarted.catalog_connection, "schedules") == 1
        assert _count(restarted.catalog_connection, "external_resource_state") == 1
    finally:
        restarted.close()


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
            transport="job-scheduling-integration-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    connected = ipc_client.connect()
    assert connected.status is IpcStatus.ACCEPTED
    return EngineClient(ipc_client)


def _trigger_client(service: object) -> InProcessIpcClient:
    ipc_client = InProcessIpcClient(
        service=service,  # type: ignore[arg-type]
        identity=VerifiedClientIdentity(
            user_sid_hash="same-user",
            session_id=42,
            is_remote=False,
            transport="scheduled-trigger-integration-test",
        ),
        role=ProcessRole.TRIGGER_CLIENT,
        client_instance_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    connected = ipc_client.connect()
    assert connected.status is IpcStatus.ACCEPTED
    return ipc_client


def _detail_job(client: EngineClient, job_id: str) -> dict[str, object]:
    response = client.get_backup_job_detail(job_id=job_id)
    assert response.status is IpcStatus.ACCEPTED
    detail = response.payload["backup_job_detail"]
    assert isinstance(detail, dict)
    job = detail["job"]
    assert isinstance(job, dict)
    return job


def _count(connection: object, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()  # type: ignore[attr-defined]
    assert row is not None
    return int(row[0])


def _schedule_receipt_state(connection: object) -> str:
    row = connection.execute(  # type: ignore[attr-defined]
        """
        SELECT state
        FROM command_receipts
        WHERE idempotency_key = ?
        """,
        (SCHEDULE_IDEMPOTENCY_KEY,),
    ).fetchone()
    assert row is not None
    return str(row[0])
