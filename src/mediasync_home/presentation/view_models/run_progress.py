from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class RunTargetProgressViewState:
    endpoint_id: str
    state: str
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int


@dataclass(frozen=True)
class RunProgressViewState:
    run_id: str | None
    job_id: str | None
    state: str | None
    terminal: bool
    sequence_no: int | None
    planned_operations: int
    completed_operations: int
    planned_bytes: int
    completed_bytes: int
    warning_count: int
    error_count: int
    targets: tuple[RunTargetProgressViewState, ...] = ()
    read_model_available: bool = False
    run_found: bool = False

    @property
    def active(self) -> bool:
        return self.run_found and not self.terminal


def empty_run_progress_state() -> RunProgressViewState:
    return RunProgressViewState(
        run_id=None,
        job_id=None,
        state=None,
        terminal=False,
        sequence_no=None,
        planned_operations=0,
        completed_operations=0,
        planned_bytes=0,
        completed_bytes=0,
        warning_count=0,
        error_count=0,
    )


def run_progress_from_response(
    response: IpcResponse | None,
    *,
    previous: RunProgressViewState | None = None,
) -> RunProgressViewState:
    fallback = previous or empty_run_progress_state()
    if response is None or response.status is IpcStatus.REJECTED:
        return fallback
    result = response.payload.get("run_progress")
    if not isinstance(result, dict):
        return fallback
    available = bool(result.get("read_model_available", False))
    found = bool(result.get("run_found", False))
    snapshot = result.get("snapshot")
    if snapshot is None and found and previous is not None:
        return previous
    if not isinstance(snapshot, dict):
        return RunProgressViewState(
            **{
                **empty_run_progress_state().__dict__,
                "run_id": _text(result.get("run_id")),
                "read_model_available": available,
                "run_found": found,
            }
        )

    run_id = _text(snapshot.get("run_id"))
    job_id = _text(snapshot.get("job_id"))
    state = _text(snapshot.get("state"))
    sequence_no = _non_negative_int(snapshot.get("sequence_no"))
    if run_id is None or job_id is None or state is None or sequence_no is None:
        return fallback
    targets_payload = snapshot.get("targets")
    targets = tuple(
        target
        for item in targets_payload
        if isinstance(item, dict)
        and (target := _target_from_payload(item)) is not None
    ) if isinstance(targets_payload, list) else ()
    return RunProgressViewState(
        run_id=run_id,
        job_id=job_id,
        state=state,
        terminal=bool(snapshot.get("terminal", False)),
        sequence_no=sequence_no,
        planned_operations=_non_negative_int(snapshot.get("planned_operations")) or 0,
        completed_operations=_non_negative_int(snapshot.get("completed_operations")) or 0,
        planned_bytes=_non_negative_int(snapshot.get("planned_bytes")) or 0,
        completed_bytes=_non_negative_int(snapshot.get("completed_bytes")) or 0,
        warning_count=_non_negative_int(snapshot.get("warning_count")) or 0,
        error_count=_non_negative_int(snapshot.get("error_count")) or 0,
        targets=targets,
        read_model_available=available,
        run_found=found,
    )


def _target_from_payload(payload: dict[object, object]) -> RunTargetProgressViewState | None:
    endpoint_id = _text(payload.get("endpoint_id"))
    state = _text(payload.get("state"))
    if endpoint_id is None or state is None:
        return None
    return RunTargetProgressViewState(
        endpoint_id=endpoint_id,
        state=state,
        planned_operations=_non_negative_int(payload.get("planned_operations")) or 0,
        completed_operations=_non_negative_int(payload.get("completed_operations")) or 0,
        planned_bytes=_non_negative_int(payload.get("planned_bytes")) or 0,
        completed_bytes=_non_negative_int(payload.get("completed_bytes")) or 0,
        warning_count=_non_negative_int(payload.get("warning_count")) or 0,
        error_count=_non_negative_int(payload.get("error_count")) or 0,
    )


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
