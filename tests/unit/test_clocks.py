from __future__ import annotations

import pytest

from mediasync_home.application.clocks import (
    ClockViolation,
    MonotonicClaimExpired,
    MonotonicClaimWindow,
)
from tests.support.fake_clock import FakeClock


def test_monotonic_claim_window_uses_monotonic_time_only_for_expiry() -> None:
    clock = FakeClock()
    window = MonotonicClaimWindow.start(clock, ttl_ms=30_000)

    clock.set_utc("2099-01-01T00:00:00.000Z")
    window.assert_active(clock)
    clock.set_utc("1999-01-01T00:00:00.000Z")
    clock.advance_monotonic_ms(29_999)
    window.assert_active(clock)

    clock.advance_monotonic_ms(1)
    with pytest.raises(MonotonicClaimExpired, match="MONOTONIC_CLAIM_DEADLINE_EXPIRED"):
        window.assert_active(clock)


def test_monotonic_claim_window_validates_ttl_and_utc_audit_value() -> None:
    with pytest.raises(ClockViolation, match="MONOTONIC_CLAIM_TTL_MUST_BE_POSITIVE"):
        MonotonicClaimWindow.start(FakeClock(), ttl_ms=0)
    with pytest.raises(ClockViolation, match="UTC_CLOCK_MUST_RETURN_RFC3339_Z"):
        MonotonicClaimWindow.start(
            FakeClock(utc_now_value="2026-07-31T00:00:00+00:00"),
            ttl_ms=1,
        )
