from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ClockViolation(ValueError):
    pass


class MonotonicClaimExpired(ClockViolation):
    pass


class ClockPort(Protocol):
    def utc_now(self) -> str: ...

    def monotonic_ns(self) -> int: ...


@dataclass(frozen=True)
class MonotonicClaimWindow:
    started_utc: str
    started_monotonic_ns: int
    deadline_monotonic_ns: int
    ttl_ms: int

    @classmethod
    def start(
        cls,
        clock: ClockPort,
        *,
        ttl_ms: int,
    ) -> MonotonicClaimWindow:
        if ttl_ms <= 0:
            raise ClockViolation("MONOTONIC_CLAIM_TTL_MUST_BE_POSITIVE")
        started_monotonic_ns = clock.monotonic_ns()
        if started_monotonic_ns < 0:
            raise ClockViolation("MONOTONIC_CLOCK_MUST_BE_NON_NEGATIVE")
        started_utc = clock.utc_now()
        if not started_utc.strip() or not started_utc.endswith("Z"):
            raise ClockViolation("UTC_CLOCK_MUST_RETURN_RFC3339_Z")
        return cls(
            started_utc=started_utc,
            started_monotonic_ns=started_monotonic_ns,
            deadline_monotonic_ns=started_monotonic_ns + ttl_ms * 1_000_000,
            ttl_ms=ttl_ms,
        )

    def assert_active(self, clock: ClockPort) -> None:
        if clock.monotonic_ns() >= self.deadline_monotonic_ns:
            raise MonotonicClaimExpired("MONOTONIC_CLAIM_DEADLINE_EXPIRED")
