from __future__ import annotations

from mediasync_home.application.staging_retry import (
    STAGING_RETRY_MAX_MS,
    MonotonicStagingRetryScheduler,
    staging_retry_backoff_ms,
)
from tests.support.fake_clock import FakeClock


def test_staging_retry_backoff_is_deterministic_jittered_and_bounded() -> None:
    first = staging_retry_backoff_ms(
        run_id="run-a",
        operation_id="operation-a",
        attempt_no=1,
    )
    second = staging_retry_backoff_ms(
        run_id="run-a",
        operation_id="operation-a",
        attempt_no=2,
    )
    capped = staging_retry_backoff_ms(
        run_id="run-a",
        operation_id="operation-a",
        attempt_no=100,
    )

    assert 800 <= first <= 1_000
    assert 1_600 <= second <= 2_000
    assert 24_000 <= capped <= STAGING_RETRY_MAX_MS
    assert first == staging_retry_backoff_ms(
        run_id="run-a",
        operation_id="operation-a",
        attempt_no=1,
    )
    assert first != staging_retry_backoff_ms(
        run_id="run-a",
        operation_id="operation-b",
        attempt_no=1,
    )


def test_live_staging_retry_deadline_ignores_wall_clock_changes() -> None:
    clock = FakeClock()
    scheduler = MonotonicStagingRetryScheduler(clock)
    timing = scheduler.plan(backoff_ms=1_000)
    scheduler.activate(run_id="run-a", operation_id="operation-a", timing=timing)

    clock.set_utc("2036-07-31T00:00:00.000Z")
    assert not scheduler.is_due(
        run_id="run-a",
        operation_id="operation-a",
        backoff_ms=timing.backoff_ms,
        retry_not_before_utc=timing.retry_not_before_utc,
    )
    clock.advance_monotonic_ms(999)
    clock.set_utc("2016-07-31T00:00:00.000Z")
    assert not scheduler.is_due(
        run_id="run-a",
        operation_id="operation-a",
        backoff_ms=timing.backoff_ms,
        retry_not_before_utc=timing.retry_not_before_utc,
    )
    clock.advance_monotonic_ms(1)
    assert scheduler.is_due(
        run_id="run-a",
        operation_id="operation-a",
        backoff_ms=timing.backoff_ms,
        retry_not_before_utc=timing.retry_not_before_utc,
    )


def test_restart_reconciles_staging_retry_utc_once_then_uses_monotonic_time() -> None:
    clock = FakeClock(
        utc_now_value="2026-07-31T00:00:00.400Z",
        monotonic_ns_value=10_000_000,
    )
    scheduler = MonotonicStagingRetryScheduler(clock)

    assert not scheduler.is_due(
        run_id="run-a",
        operation_id="operation-a",
        backoff_ms=1_000,
        retry_not_before_utc="2026-07-31T00:00:01.000Z",
    )
    clock.set_utc("2036-07-31T00:00:00.000Z")
    clock.advance_monotonic_ms(599)
    assert not scheduler.is_due(
        run_id="run-a",
        operation_id="operation-a",
        backoff_ms=1_000,
        retry_not_before_utc="2026-07-31T00:00:01.000Z",
    )
    clock.advance_monotonic_ms(1)
    assert scheduler.is_due(
        run_id="run-a",
        operation_id="operation-a",
        backoff_ms=1_000,
        retry_not_before_utc="2026-07-31T00:00:01.000Z",
    )
