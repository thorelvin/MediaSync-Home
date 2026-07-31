from __future__ import annotations

from mediasync_home.application.endpoint_retry import (
    ENDPOINT_RETRY_MAX_MS,
    MonotonicEndpointRetryScheduler,
    endpoint_retry_backoff_ms,
)
from tests.support.fake_clock import FakeClock


def test_endpoint_retry_backoff_is_deterministic_jittered_and_bounded() -> None:
    first = endpoint_retry_backoff_ms(
        run_id="run-a",
        run_target_id="target-a",
        attempt_no=1,
    )
    second = endpoint_retry_backoff_ms(
        run_id="run-a",
        run_target_id="target-a",
        attempt_no=2,
    )
    capped = endpoint_retry_backoff_ms(
        run_id="run-a",
        run_target_id="target-a",
        attempt_no=100,
    )

    assert 4_000 <= first <= 5_000
    assert 8_000 <= second <= 10_000
    assert 240_000 <= capped <= ENDPOINT_RETRY_MAX_MS
    assert first == endpoint_retry_backoff_ms(
        run_id="run-a",
        run_target_id="target-a",
        attempt_no=1,
    )
    assert first != endpoint_retry_backoff_ms(
        run_id="run-a",
        run_target_id="target-b",
        attempt_no=1,
    )


def test_live_retry_deadline_ignores_wall_clock_changes() -> None:
    clock = FakeClock()
    scheduler = MonotonicEndpointRetryScheduler(clock)
    timing = scheduler.plan(backoff_ms=5_000)
    scheduler.activate(event_id=1, timing=timing)

    clock.set_utc("2036-07-31T00:00:00.000Z")
    assert not scheduler.is_due(
        event_id=1,
        backoff_ms=timing.backoff_ms,
        retry_not_before_utc=timing.retry_not_before_utc,
    )
    clock.advance_monotonic_ms(4_999)
    clock.set_utc("2016-07-31T00:00:00.000Z")
    assert not scheduler.is_due(
        event_id=1,
        backoff_ms=timing.backoff_ms,
        retry_not_before_utc=timing.retry_not_before_utc,
    )
    clock.advance_monotonic_ms(1)
    assert scheduler.is_due(
        event_id=1,
        backoff_ms=timing.backoff_ms,
        retry_not_before_utc=timing.retry_not_before_utc,
    )


def test_restart_reconciles_persisted_utc_once_then_uses_monotonic_time() -> None:
    clock = FakeClock(
        utc_now_value="2026-07-31T00:00:02.000Z",
        monotonic_ns_value=10_000_000,
    )
    scheduler = MonotonicEndpointRetryScheduler(clock)

    assert not scheduler.is_due(
        event_id=7,
        backoff_ms=5_000,
        retry_not_before_utc="2026-07-31T00:00:05.000Z",
    )
    clock.set_utc("2036-07-31T00:00:00.000Z")
    clock.advance_monotonic_ms(2_999)
    assert not scheduler.is_due(
        event_id=7,
        backoff_ms=5_000,
        retry_not_before_utc="2026-07-31T00:00:05.000Z",
    )
    clock.advance_monotonic_ms(1)
    assert scheduler.is_due(
        event_id=7,
        backoff_ms=5_000,
        retry_not_before_utc="2026-07-31T00:00:05.000Z",
    )
