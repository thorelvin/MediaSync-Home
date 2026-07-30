from __future__ import annotations

import pytest

from mediasync_home.application.progress_read_models import (
    MAX_PROGRESS_SNAPSHOT_TARGETS,
    ProgressSnapshotQueryError,
    RunProgressSnapshot,
    RunTargetProgressSnapshot,
    query_run_progress,
)
from mediasync_home.application.runs import RunState, RunTargetState


class _ProgressStore:
    def __init__(self, snapshot: RunProgressSnapshot | None) -> None:
        self.snapshot = snapshot
        self.requested_run_ids: list[str] = []

    def load_run_progress_snapshot(self, run_id: str) -> RunProgressSnapshot | None:
        self.requested_run_ids.append(run_id)
        return self.snapshot


def test_query_run_progress_returns_authoritative_snapshot() -> None:
    store = _ProgressStore(_snapshot(sequence_no=7))

    result = query_run_progress(
        run_progress_store=store,
        run_id=" run-a ",
        after_sequence_no=5,
    )

    assert result.read_model_available is True
    assert result.run_found is True
    assert result.changed is True
    assert result.sequence_reset is False
    assert result.snapshot is store.snapshot
    assert store.requested_run_ids == ["run-a"]


def test_query_run_progress_omits_unchanged_snapshot() -> None:
    result = query_run_progress(
        run_progress_store=_ProgressStore(_snapshot(sequence_no=7)),
        run_id="run-a",
        after_sequence_no=7,
    )

    assert result.run_found is True
    assert result.changed is False
    assert result.sequence_reset is False
    assert result.snapshot is None


def test_query_run_progress_marks_sequence_reset_after_state_restore() -> None:
    result = query_run_progress(
        run_progress_store=_ProgressStore(_snapshot(sequence_no=3)),
        run_id="run-a",
        after_sequence_no=9,
    )

    assert result.changed is True
    assert result.sequence_reset is True
    assert result.snapshot is not None
    assert result.snapshot.sequence_no == 3


def test_query_run_progress_distinguishes_unavailable_and_missing() -> None:
    unavailable = query_run_progress(
        run_progress_store=None,
        run_id="run-a",
    )
    missing = query_run_progress(
        run_progress_store=_ProgressStore(None),
        run_id="run-a",
    )

    assert unavailable.read_model_available is False
    assert unavailable.run_found is False
    assert missing.read_model_available is True
    assert missing.run_found is False
    assert missing.changed is False


@pytest.mark.parametrize(
    ("run_id", "after_sequence_no"),
    [
        (" ", None),
        ("run-a", -1),
        ("run-a", True),
    ],
)
def test_query_run_progress_rejects_invalid_request(
    run_id: str,
    after_sequence_no: int | None,
) -> None:
    with pytest.raises(ProgressSnapshotQueryError):
        query_run_progress(
            run_progress_store=_ProgressStore(None),
            run_id=run_id,
            after_sequence_no=after_sequence_no,
        )


def test_run_progress_snapshot_rejects_unbounded_targets() -> None:
    target = _target()

    with pytest.raises(ProgressSnapshotQueryError, match="RUN_PROGRESS_TARGET_LIMIT_EXCEEDED"):
        _snapshot(
            sequence_no=1,
            targets=(target,) * (MAX_PROGRESS_SNAPSHOT_TARGETS + 1),
        )


def _snapshot(
    *,
    sequence_no: int,
    targets: tuple[RunTargetProgressSnapshot, ...] | None = None,
) -> RunProgressSnapshot:
    snapshot_targets = (_target(),) if targets is None else targets
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
        targets=snapshot_targets,
    )


def _target() -> RunTargetProgressSnapshot:
    return RunTargetProgressSnapshot(
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
