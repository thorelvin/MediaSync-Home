from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.progress_read_models import (
    MAX_PROGRESS_SNAPSHOT_TARGETS,
    RunProgressSnapshot,
    RunTargetProgressSnapshot,
)
from mediasync_home.application.runs import RunState, RunTargetState
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    MAX_PROGRESS_EVENT_BYTES,
    IpcReason,
    IpcStatus,
    encode_frame,
)
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient


class _MutableProgressStore:
    def __init__(self, snapshot: RunProgressSnapshot | None) -> None:
        self.snapshot = snapshot

    def load_run_progress_snapshot(self, run_id: str) -> RunProgressSnapshot | None:
        if self.snapshot is None or self.snapshot.run_id != run_id:
            return None
        return self.snapshot


def test_run_progress_query_requires_prior_handshake() -> None:
    response = _client(_service(_MutableProgressStore(_snapshot(2)))).query_run_progress(
        run_id="run-a"
    )

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.HANDSHAKE_REQUIRED


def test_run_progress_query_rejects_invalid_sequence() -> None:
    client = _client(_service(_MutableProgressStore(_snapshot(2))))
    client.connect()

    response = client.query_run_progress(run_id="run-a", after_sequence_no=-1)

    assert response.status is IpcStatus.REJECTED
    assert response.reason is IpcReason.INVALID_FRAME


def test_run_progress_reconnect_is_unchanged_then_observes_new_sequence() -> None:
    store = _MutableProgressStore(_snapshot(2))
    service = _service(store)
    first_client = _client(
        service,
        client_instance_id="55555555-5555-4555-8555-555555555555",
    )
    first_client.connect()
    initial = first_client.query_run_progress(run_id="run-a")
    sequence_no = initial.payload["run_progress"]["snapshot"]["sequence_no"]

    reconnecting_client = _client(
        service,
        client_instance_id="66666666-6666-4666-8666-666666666666",
    )
    engine_client = EngineClient(reconnecting_client)
    unchanged = engine_client.get_run_progress(
        run_id="run-a",
        after_sequence_no=sequence_no,
    )
    store.snapshot = replace(_snapshot(3), completed_operations=1, completed_bytes=128)
    changed = engine_client.get_run_progress(
        run_id="run-a",
        after_sequence_no=sequence_no,
    )

    assert unchanged.status is IpcStatus.ACCEPTED
    assert unchanged.payload["run_progress"]["changed"] is False
    assert unchanged.payload["run_progress"]["snapshot"] is None
    assert changed.status is IpcStatus.ACCEPTED
    assert changed.payload["run_progress"]["changed"] is True
    assert changed.payload["run_progress"]["snapshot"]["sequence_no"] == 3


def test_maximum_valid_progress_snapshot_fits_progress_frame_limit() -> None:
    long_id = "x" * 256
    target = RunTargetProgressSnapshot(
        run_target_id=long_id,
        endpoint_id=long_id,
        endpoint_revision_id=long_id,
        state=RunTargetState.EXECUTING,
        planned_operations=1,
        completed_operations=0,
        planned_bytes=1,
        completed_bytes=0,
        warning_count=0,
        error_count=0,
    )
    snapshot = RunProgressSnapshot(
        run_id=long_id,
        job_id=long_id,
        job_revision_id=long_id,
        plan_id=long_id,
        sequence_no=1,
        state=RunState.EXECUTING,
        terminal=False,
        started_utc="2026-07-31T00:00:00.000Z",
        finished_utc=None,
        planned_operations=MAX_PROGRESS_SNAPSHOT_TARGETS,
        completed_operations=0,
        planned_bytes=MAX_PROGRESS_SNAPSHOT_TARGETS,
        completed_bytes=0,
        warning_count=0,
        error_count=0,
        targets=(target,) * MAX_PROGRESS_SNAPSHOT_TARGETS,
    )
    service = _service(_MutableProgressStore(snapshot))
    client = _client(service)
    client.connect()

    response = client.query_run_progress(run_id=long_id)
    encoded = encode_frame(response.to_dict(), limit=MAX_PROGRESS_EVENT_BYTES)

    assert response.status is IpcStatus.ACCEPTED
    assert len(encoded) <= MAX_PROGRESS_EVENT_BYTES


def _service(store: _MutableProgressStore) -> EngineHostIpcService:
    return EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash="same-user",
            expected_session_id=42,
        ),
        run_progress_snapshot_store=store,
    )


def _client(
    service: EngineHostIpcService,
    *,
    client_instance_id: str = "44444444-4444-4444-8444-444444444444",
) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=service,
        identity=VerifiedClientIdentity(
            user_sid_hash="same-user",
            session_id=42,
            is_remote=False,
            transport="progress-ipc-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id=client_instance_id,
    )


def _snapshot(sequence_no: int) -> RunProgressSnapshot:
    target = RunTargetProgressSnapshot(
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        state=RunTargetState.EXECUTING,
        planned_operations=1,
        completed_operations=0,
        planned_bytes=128,
        completed_bytes=0,
        warning_count=0,
        error_count=0,
    )
    return RunProgressSnapshot(
        run_id="run-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        plan_id="plan-a",
        sequence_no=sequence_no,
        state=RunState.EXECUTING,
        terminal=False,
        started_utc="2026-07-31T00:00:00.000Z",
        finished_utc=None,
        planned_operations=1,
        completed_operations=0,
        planned_bytes=128,
        completed_bytes=0,
        warning_count=0,
        error_count=0,
        targets=(target,),
    )
