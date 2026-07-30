from __future__ import annotations

from dataclasses import dataclass

import pytest

from mediasync_home.application.state_capacity import (
    GIB,
    MIB,
    StateCapacityGate,
    StateCapacityObservation,
    StateCapacityPolicy,
    StateCapacityStatus,
    StateGrowthEstimate,
    snapshot_analysis_growth_estimate,
)


@dataclass
class _Probe:
    observation: StateCapacityObservation

    def measure(self) -> StateCapacityObservation:
        return self.observation


def _observation(
    *,
    state_size: int = 100 * MIB,
    free_space: int = 20 * GIB,
    complete: bool = True,
    error_code: str | None = None,
) -> StateCapacityObservation:
    return StateCapacityObservation(
        state_size_bytes=state_size,
        local_free_space_bytes=free_space,
        measurement_complete=complete,
        scanned_entry_count=4,
        measurement_error_code=error_code,
    )


def _policy() -> StateCapacityPolicy:
    return StateCapacityPolicy(
        soft_quota_bytes=1 * GIB,
        hard_stop_quota_bytes=2 * GIB,
        minimum_free_space_bytes=256 * MIB,
        internal_backup_reserve_bytes=128 * MIB,
    )


@pytest.mark.parametrize(
    "policy",
    [
        StateCapacityPolicy(
            soft_quota_bytes=1,
            hard_stop_quota_bytes=2,
            minimum_free_space_bytes=0,
            internal_backup_reserve_bytes=0,
        ),
    ],
)
def test_state_capacity_policy_accepts_ordered_non_negative_limits(
    policy: StateCapacityPolicy,
) -> None:
    assert policy.hard_stop_quota_bytes > policy.soft_quota_bytes


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"soft_quota_bytes": 0}, "STATE_CAPACITY_SOFT_QUOTA_MUST_BE_POSITIVE"),
        (
            {"soft_quota_bytes": 10, "hard_stop_quota_bytes": 10},
            "STATE_CAPACITY_HARD_QUOTA_MUST_EXCEED_SOFT_QUOTA",
        ),
        ({"minimum_free_space_bytes": -1}, "STATE_CAPACITY_MINIMUM_FREE_SPACE_NEGATIVE"),
        ({"internal_backup_reserve_bytes": -1}, "STATE_CAPACITY_BACKUP_RESERVE_NEGATIVE"),
    ],
)
def test_state_capacity_policy_rejects_invalid_limits(
    kwargs: dict[str, int],
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        StateCapacityPolicy(**kwargs)


def test_capacity_gate_reports_ready_and_separates_local_growth_components() -> None:
    gate = StateCapacityGate(probe=_Probe(_observation()), policy=_policy())
    estimate = StateGrowthEstimate(
        estimated_catalog_growth_bytes=10,
        estimated_recovery_growth_bytes=20,
        estimated_hash_cache_growth_bytes=30,
        estimated_log_growth_bytes=40,
    )

    report = gate.evaluate(estimate)

    assert report.status is StateCapacityStatus.READY
    assert report.allows_new_analysis_and_transfers is True
    assert report.projected_state_size_bytes == 100 * MIB + 100
    assert report.to_dict()["scope"] == "LOCAL_APPDATA_STATE"
    assert report.to_dict()["estimated_local_growth_bytes"] == 100
    assert "target_free_space_bytes" not in report.to_dict()


def test_capacity_gate_soft_quota_allows_work_without_discarding_evidence() -> None:
    gate = StateCapacityGate(
        probe=_Probe(_observation(state_size=900 * MIB)),
        policy=_policy(),
    )

    report = gate.evaluate(
        StateGrowthEstimate(estimated_catalog_growth_bytes=200 * MIB)
    )

    assert report.status is StateCapacityStatus.SOFT_QUOTA
    assert report.reason_code == "STATE_CAPACITY_SOFT_QUOTA"
    assert report.recommended_action == "CLEAN_NON_AUTHORITATIVE_CACHE_AND_LOGS"
    assert report.allows_new_analysis_and_transfers is True


def test_capacity_gate_hard_quota_blocks_new_analysis_and_transfers() -> None:
    gate = StateCapacityGate(
        probe=_Probe(_observation(state_size=1900 * MIB)),
        policy=_policy(),
    )

    report = gate.evaluate(
        StateGrowthEstimate(estimated_catalog_growth_bytes=200 * MIB)
    )

    assert report.status is StateCapacityStatus.HARD_STOP
    assert report.reason_code == "STATE_CAPACITY_HARD_QUOTA"
    assert report.allows_new_analysis_and_transfers is False


def test_capacity_gate_reserves_growth_backup_and_minimum_free_space() -> None:
    estimate = StateGrowthEstimate(estimated_catalog_growth_bytes=300 * MIB)
    gate = StateCapacityGate(
        probe=_Probe(_observation(free_space=683 * MIB)),
        policy=_policy(),
    )

    report = gate.evaluate(estimate)

    assert report.required_local_free_space_bytes == 684 * MIB
    assert report.reason_code == "STATE_CAPACITY_LOCAL_FREE_SPACE_LOW"
    assert report.allows_new_analysis_and_transfers is False


def test_capacity_gate_fails_closed_when_measurement_is_incomplete() -> None:
    gate = StateCapacityGate(
        probe=_Probe(
            _observation(
                complete=False,
                error_code="STATE_CAPACITY_SCAN_LIMIT_EXCEEDED",
            )
        ),
        policy=_policy(),
    )

    report = gate.evaluate(StateGrowthEstimate())

    assert report.status is StateCapacityStatus.HARD_STOP
    assert report.reason_code == "STATE_CAPACITY_SCAN_LIMIT_EXCEEDED"


def test_sqlite_full_latch_survives_later_healthy_measurements() -> None:
    probe = _Probe(_observation())
    gate = StateCapacityGate(probe=probe, policy=_policy())
    assert gate.evaluate(StateGrowthEstimate()).status is StateCapacityStatus.READY

    full_report = gate.latch_sqlite_full("recovery")
    probe.observation = _observation(free_space=100 * GIB)
    later_report = gate.evaluate(StateGrowthEstimate())

    assert full_report.reason_code == "STATE_CAPACITY_SQLITE_FULL"
    assert (
        full_report.recommended_action
        == "FREE_LOCAL_STATE_SPACE_AND_RESTART_ENGINE_HOST"
    )
    assert full_report.sqlite_full_store == "recovery"
    assert later_report.status is StateCapacityStatus.HARD_STOP
    assert later_report.sqlite_full_store == "recovery"


def test_snapshot_growth_estimate_replaces_baseline_with_scanned_counts() -> None:
    baseline = snapshot_analysis_growth_estimate(endpoint_count=2)
    measured = snapshot_analysis_growth_estimate(
        endpoint_count=2,
        entry_count=1_000,
        coverage_count=20,
        issue_count=3,
    )

    assert baseline.total_bytes > 0
    assert measured.estimated_catalog_growth_bytes == (
        32 * MIB + 1_000 * 1536 + 20 * 1024 + 3 * 2048
    )
    assert measured.estimated_hash_cache_growth_bytes == 1_000 * 512
