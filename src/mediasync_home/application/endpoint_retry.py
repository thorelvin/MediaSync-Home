from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mediasync_home.application.clocks import ClockPort


ENDPOINT_RETRY_BASE_MS = 5_000
ENDPOINT_RETRY_MAX_MS = 300_000
ENDPOINT_RETRY_JITTER_FLOOR_PERCENT = 80


class EndpointRetryViolation(ValueError):
    pass


@dataclass(frozen=True)
class EndpointRetryTiming:
    observed_utc: str
    retry_not_before_utc: str
    backoff_ms: int
    deadline_monotonic_ns: int


@dataclass(frozen=True)
class _ActiveDeadline:
    retry_not_before_utc: str
    backoff_ms: int
    deadline_monotonic_ns: int


def endpoint_retry_backoff_ms(
    *,
    run_id: str,
    run_target_id: str,
    attempt_no: int,
) -> int:
    if not run_id.strip() or not run_target_id.strip():
        raise EndpointRetryViolation("ENDPOINT_RETRY_REQUIRES_TARGET_IDENTITY")
    if attempt_no < 1:
        raise EndpointRetryViolation("ENDPOINT_RETRY_ATTEMPT_MUST_BE_POSITIVE")

    exponent = min(attempt_no - 1, 31)
    nominal_ms = min(ENDPOINT_RETRY_BASE_MS * (1 << exponent), ENDPOINT_RETRY_MAX_MS)
    floor_ms = nominal_ms * ENDPOINT_RETRY_JITTER_FLOOR_PERCENT // 100
    jitter_span_ms = nominal_ms - floor_ms
    digest = hashlib.sha256(
        f"{run_id}\0{run_target_id}\0{attempt_no}".encode("utf-8")
    ).digest()
    jitter_ms = int.from_bytes(digest[:8], byteorder="big") % (jitter_span_ms + 1)
    return floor_ms + jitter_ms


class MonotonicEndpointRetryScheduler:
    def __init__(self, clock: ClockPort) -> None:
        self._clock = clock
        self._deadlines: dict[int, _ActiveDeadline] = {}

    def plan(self, *, backoff_ms: int) -> EndpointRetryTiming:
        _validate_backoff(backoff_ms)
        observed = _parse_utc(self._clock.utc_now())
        started_monotonic_ns = _monotonic_now(self._clock)
        retry_not_before = observed + timedelta(milliseconds=backoff_ms)
        return EndpointRetryTiming(
            observed_utc=_format_utc(observed),
            retry_not_before_utc=_format_utc(retry_not_before),
            backoff_ms=backoff_ms,
            deadline_monotonic_ns=started_monotonic_ns + backoff_ms * 1_000_000,
        )

    def activate(self, *, event_id: int, timing: EndpointRetryTiming) -> None:
        _validate_event_id(event_id)
        _validate_backoff(timing.backoff_ms)
        _parse_utc(timing.retry_not_before_utc)
        self._deadlines[event_id] = _ActiveDeadline(
            retry_not_before_utc=timing.retry_not_before_utc,
            backoff_ms=timing.backoff_ms,
            deadline_monotonic_ns=timing.deadline_monotonic_ns,
        )

    def is_due(
        self,
        *,
        event_id: int,
        backoff_ms: int,
        retry_not_before_utc: str,
    ) -> bool:
        _validate_event_id(event_id)
        _validate_backoff(backoff_ms)
        retry_not_before = _parse_utc(retry_not_before_utc)
        now_monotonic_ns = _monotonic_now(self._clock)
        active = self._deadlines.get(event_id)
        if active is None or (
            active.backoff_ms != backoff_ms
            or active.retry_not_before_utc != retry_not_before_utc
        ):
            now_utc = _parse_utc(self._clock.utc_now())
            remaining_ms = math.ceil(
                max(0.0, (retry_not_before - now_utc).total_seconds() * 1_000)
            )
            remaining_ms = min(backoff_ms, remaining_ms)
            active = _ActiveDeadline(
                retry_not_before_utc=retry_not_before_utc,
                backoff_ms=backoff_ms,
                deadline_monotonic_ns=now_monotonic_ns + remaining_ms * 1_000_000,
            )
            self._deadlines[event_id] = active
        return now_monotonic_ns >= active.deadline_monotonic_ns

    def discard(self, *, event_id: int) -> None:
        self._deadlines.pop(event_id, None)


def _validate_event_id(event_id: int) -> None:
    if isinstance(event_id, bool) or event_id < 1:
        raise EndpointRetryViolation("ENDPOINT_RETRY_EVENT_ID_INVALID")


def _validate_backoff(backoff_ms: int) -> None:
    if (
        isinstance(backoff_ms, bool)
        or backoff_ms < 1
        or backoff_ms > ENDPOINT_RETRY_MAX_MS
    ):
        raise EndpointRetryViolation("ENDPOINT_RETRY_BACKOFF_INVALID")


def _monotonic_now(clock: ClockPort) -> int:
    value = clock.monotonic_ns()
    if value < 0:
        raise EndpointRetryViolation("ENDPOINT_RETRY_MONOTONIC_CLOCK_INVALID")
    return value


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if not normalized.endswith("Z"):
        raise EndpointRetryViolation("ENDPOINT_RETRY_UTC_INVALID")
    try:
        parsed = datetime.fromisoformat(f"{normalized[:-1]}+00:00")
    except ValueError as exc:
        raise EndpointRetryViolation("ENDPOINT_RETRY_UTC_INVALID") from exc
    if parsed.tzinfo is None:
        raise EndpointRetryViolation("ENDPOINT_RETRY_UTC_INVALID")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
