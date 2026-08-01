from __future__ import annotations

from mediasync_home.application.history_read_models import (
    HistoryActivityFilter,
    HistoryActivityKind,
    HistoryActivitySummary,
    HistoryTargetSummary,
    HistoryTimelineCursor,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient


class _HistoryStore:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                int,
                HistoryTimelineCursor | None,
                int,
                HistoryActivityFilter,
                str | None,
            ]
        ] = []

    def list_recent_history_activities(
        self,
        *,
        limit: int,
        after: HistoryTimelineCursor | None,
        offset: int,
        activity_filter: HistoryActivityFilter,
        job_id: str | None,
    ) -> tuple[HistoryActivitySummary, ...]:
        self.calls.append((limit, after, offset, activity_filter, job_id))
        return (_activity(),)


def test_history_ipc_requires_handshake_and_rejects_invalid_filter() -> None:
    client = _client(_service(_HistoryStore()))

    before_handshake = client.query_history_timeline()
    client.connect()
    invalid = client.query_history_timeline(activity_filter="RUNS")

    assert before_handshake.reason is IpcReason.HANDSHAKE_REQUIRED
    assert invalid.reason is IpcReason.INVALID_FRAME


def test_engine_client_queries_bounded_history_after_handshake_retry() -> None:
    store = _HistoryStore()
    engine_client = EngineClient(_client(_service(store)))

    response = engine_client.get_history_timeline(
        activity_filter="BACKUPS",
        job_id="job-a",
        limit=1,
        offset=25,
    )

    assert response.status is IpcStatus.ACCEPTED
    assert store.calls == [
        (2, None, 25, HistoryActivityFilter.BACKUPS, "job-a"),
    ]
    timeline = response.payload["history_timeline"]
    assert timeline["activity_filter"] == "BACKUPS"
    assert timeline["job_id"] == "job-a"
    assert timeline["activities"][0]["activity_id"] == "run-a"


def test_engine_client_forwards_typed_history_cursor() -> None:
    store = _HistoryStore()
    engine_client = EngineClient(_client(_service(store)))
    cursor = {
        "cursor_version": 1,
        "started_utc": "2026-07-20T12:00:00.000Z",
        "activity_kind": "BACKUP",
        "activity_id": "run-z",
    }

    response = engine_client.get_history_timeline(limit=1, after=cursor)

    assert response.status is IpcStatus.ACCEPTED
    assert store.calls == [
        (
            2,
            HistoryTimelineCursor(
                started_utc="2026-07-20T12:00:00.000Z",
                activity_kind=HistoryActivityKind.BACKUP,
                activity_id="run-z",
            ),
            0,
            HistoryActivityFilter.ALL,
            None,
        )
    ]


def test_history_ipc_rejects_cursor_with_offset() -> None:
    client = _client(_service(_HistoryStore()))
    client.connect()

    response = client.query_history_timeline(
        after={
            "cursor_version": 1,
            "started_utc": "2026-07-20T12:00:00.000Z",
            "activity_kind": "BACKUP",
            "activity_id": "run-z",
        },
        offset=0,
    )

    assert response.reason is IpcReason.INVALID_FRAME


def _service(store: _HistoryStore) -> EngineHostIpcService:
    return EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash="same-user",
            expected_session_id=42,
        ),
        history_timeline_read_store=store,
    )


def _client(service: EngineHostIpcService) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=service,
        identity=VerifiedClientIdentity(
            user_sid_hash="same-user",
            session_id=42,
            is_remote=False,
            transport="history-ipc-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id="77777777-7777-4777-8777-777777777777",
    )


def _activity() -> HistoryActivitySummary:
    return HistoryActivitySummary(
        activity_id="run-a",
        activity_kind=HistoryActivityKind.BACKUP,
        job_id="job-a",
        job_revision_id="job-rev-a",
        job_title="Pictures",
        run_id="run-a",
        plan_id="plan-a",
        state="COMPLETED",
        started_utc="2026-07-20T12:00:00.000Z",
        finished_utc="2026-07-20T12:01:00.000Z",
        planned_operations=1,
        completed_operations=1,
        planned_bytes=128,
        completed_bytes=128,
        warning_count=0,
        error_count=0,
        trigger_type="MANUAL_LOCAL_PREVIEW",
        targets=(
            HistoryTargetSummary(
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
                state="SUCCEEDED",
                planned_operations=1,
                completed_operations=1,
                planned_bytes=128,
                completed_bytes=128,
                warning_count=0,
                error_count=0,
            ),
        ),
    )
