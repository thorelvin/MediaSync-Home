from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class OperationAttemptViewState:
    attempt_number: int
    state: str
    finished_utc: str
    bytes_transferred: int
    error_code: str | None
    transfer_state: str | None
    assurance_level: str | None
    durability_level: str | None


@dataclass(frozen=True)
class OperationOutcomeViewState:
    final_state: str
    completed_utc: str
    bytes_transferred: int
    transfer_state: str
    assurance_level: str
    hash_evidence_kind: str | None
    durability_level: str
    error_code: str | None


@dataclass(frozen=True)
class OperationAuditViewState:
    run_id: str | None
    operation_id: str | None
    read_model_available: bool
    found: bool
    run_target_id: str | None = None
    target_relative_path: str | None = None
    attempts: tuple[OperationAttemptViewState, ...] = ()
    outcome: OperationOutcomeViewState | None = None


def empty_operation_audit_state(
    *,
    run_id: str | None = None,
    operation_id: str | None = None,
) -> OperationAuditViewState:
    return OperationAuditViewState(
        run_id=run_id,
        operation_id=operation_id,
        read_model_available=False,
        found=False,
    )


def operation_audit_from_response(
    response: IpcResponse | None,
) -> OperationAuditViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_operation_audit_state()
    payload = response.payload.get("operation_audit")
    if not isinstance(payload, dict):
        return empty_operation_audit_state()

    run_id = _optional_text(payload.get("run_id"))
    operation_id = _optional_text(payload.get("operation_id"))
    available = bool(payload.get("read_model_available", False))
    found = bool(payload.get("found", False))
    if not available or not found:
        return OperationAuditViewState(
            run_id=run_id,
            operation_id=operation_id,
            read_model_available=available,
            found=False,
        )

    attempts_payload = payload.get("attempts")
    attempts = (
        tuple(
            attempt
            for item in attempts_payload
            if isinstance(item, dict)
            and (attempt := _attempt_from_payload(item)) is not None
        )
        if isinstance(attempts_payload, list)
        else ()
    )
    outcome_payload = payload.get("outcome")
    outcome = (
        _outcome_from_payload(outcome_payload)
        if isinstance(outcome_payload, dict)
        else None
    )
    return OperationAuditViewState(
        run_id=run_id,
        operation_id=operation_id,
        read_model_available=True,
        found=True,
        run_target_id=_optional_text(payload.get("run_target_id")),
        target_relative_path=_optional_text(payload.get("target_relative_path")),
        attempts=attempts,
        outcome=outcome,
    )


def _attempt_from_payload(
    payload: dict[object, object],
) -> OperationAttemptViewState | None:
    attempt_number = _positive_int(payload.get("attempt_number"))
    state = _optional_text(payload.get("state"))
    finished_utc = _optional_text(payload.get("finished_utc"))
    bytes_transferred = _non_negative_int(payload.get("bytes_transferred"))
    if (
        attempt_number is None
        or state is None
        or finished_utc is None
        or bytes_transferred is None
    ):
        return None
    return OperationAttemptViewState(
        attempt_number=attempt_number,
        state=state,
        finished_utc=finished_utc,
        bytes_transferred=bytes_transferred,
        error_code=_optional_text(payload.get("error_code")),
        transfer_state=_optional_text(payload.get("transfer_state")),
        assurance_level=_optional_text(payload.get("assurance_level")),
        durability_level=_optional_text(payload.get("durability_level")),
    )


def _outcome_from_payload(
    payload: dict[object, object],
) -> OperationOutcomeViewState | None:
    final_state = _optional_text(payload.get("final_state"))
    completed_utc = _optional_text(payload.get("completed_utc"))
    bytes_transferred = _non_negative_int(payload.get("bytes_transferred"))
    transfer_state = _optional_text(payload.get("transfer_state"))
    assurance_level = _optional_text(payload.get("assurance_level"))
    durability_level = _optional_text(payload.get("durability_level"))
    if (
        final_state is None
        or completed_utc is None
        or bytes_transferred is None
        or transfer_state is None
        or assurance_level is None
        or durability_level is None
    ):
        return None
    return OperationOutcomeViewState(
        final_state=final_state,
        completed_utc=completed_utc,
        bytes_transferred=bytes_transferred,
        transfer_state=transfer_state,
        assurance_level=assurance_level,
        hash_evidence_kind=_optional_text(payload.get("hash_evidence_kind")),
        durability_level=durability_level,
        error_code=_optional_text(payload.get("error_code")),
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _positive_int(value: object) -> int | None:
    parsed = _non_negative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
