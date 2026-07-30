from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Protocol


MIB = 1024 * 1024
GIB = 1024 * MIB


class StateCapacityStatus(str, Enum):
    READY = "READY"
    SOFT_QUOTA = "SOFT_QUOTA"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True, slots=True)
class StateCapacityPolicy:
    soft_quota_bytes: int = 4 * GIB
    hard_stop_quota_bytes: int = 8 * GIB
    minimum_free_space_bytes: int = 1 * GIB
    internal_backup_reserve_bytes: int = 512 * MIB

    def __post_init__(self) -> None:
        if self.soft_quota_bytes < 1:
            raise ValueError("STATE_CAPACITY_SOFT_QUOTA_MUST_BE_POSITIVE")
        if self.hard_stop_quota_bytes <= self.soft_quota_bytes:
            raise ValueError("STATE_CAPACITY_HARD_QUOTA_MUST_EXCEED_SOFT_QUOTA")
        if self.minimum_free_space_bytes < 0:
            raise ValueError("STATE_CAPACITY_MINIMUM_FREE_SPACE_NEGATIVE")
        if self.internal_backup_reserve_bytes < 0:
            raise ValueError("STATE_CAPACITY_BACKUP_RESERVE_NEGATIVE")


@dataclass(frozen=True, slots=True)
class StateGrowthEstimate:
    estimated_catalog_growth_bytes: int = 0
    estimated_recovery_growth_bytes: int = 0
    estimated_hash_cache_growth_bytes: int = 0
    estimated_log_growth_bytes: int = 0

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.estimated_catalog_growth_bytes,
                self.estimated_recovery_growth_bytes,
                self.estimated_hash_cache_growth_bytes,
                self.estimated_log_growth_bytes,
            )
        ):
            raise ValueError("STATE_CAPACITY_GROWTH_ESTIMATE_NEGATIVE")

    @property
    def total_bytes(self) -> int:
        return (
            self.estimated_catalog_growth_bytes
            + self.estimated_recovery_growth_bytes
            + self.estimated_hash_cache_growth_bytes
            + self.estimated_log_growth_bytes
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "estimated_catalog_growth_bytes": self.estimated_catalog_growth_bytes,
            "estimated_recovery_growth_bytes": self.estimated_recovery_growth_bytes,
            "estimated_hash_cache_growth_bytes": self.estimated_hash_cache_growth_bytes,
            "estimated_log_growth_bytes": self.estimated_log_growth_bytes,
            "estimated_local_growth_bytes": self.total_bytes,
        }


@dataclass(frozen=True, slots=True)
class StateCapacityObservation:
    state_size_bytes: int
    local_free_space_bytes: int
    measurement_complete: bool
    scanned_entry_count: int
    measurement_error_code: str | None = None

    def __post_init__(self) -> None:
        if self.state_size_bytes < 0 or self.local_free_space_bytes < 0:
            raise ValueError("STATE_CAPACITY_OBSERVATION_NEGATIVE")
        if self.scanned_entry_count < 0:
            raise ValueError("STATE_CAPACITY_SCANNED_ENTRY_COUNT_NEGATIVE")
        if self.measurement_complete and self.measurement_error_code is not None:
            raise ValueError("STATE_CAPACITY_COMPLETE_MEASUREMENT_HAS_ERROR")
        if not self.measurement_complete and not self.measurement_error_code:
            raise ValueError("STATE_CAPACITY_INCOMPLETE_MEASUREMENT_REQUIRES_ERROR")


class StateCapacityProbe(Protocol):
    def measure(self) -> StateCapacityObservation: ...


@dataclass(frozen=True, slots=True)
class StateCapacityReport:
    status: StateCapacityStatus
    reason_code: str
    observation: StateCapacityObservation
    growth_estimate: StateGrowthEstimate
    projected_state_size_bytes: int
    required_local_free_space_bytes: int
    policy: StateCapacityPolicy
    sqlite_full_store: str | None = None

    @property
    def allows_new_analysis_and_transfers(self) -> bool:
        return self.status is not StateCapacityStatus.HARD_STOP

    @property
    def recommended_action(self) -> str:
        if self.reason_code == "STATE_CAPACITY_SQLITE_FULL":
            return "FREE_LOCAL_STATE_SPACE_AND_RESTART_ENGINE_HOST"
        if self.reason_code == "STATE_CAPACITY_SOFT_QUOTA":
            return "CLEAN_NON_AUTHORITATIVE_CACHE_AND_LOGS"
        if self.status is StateCapacityStatus.HARD_STOP:
            return "FREE_OR_REPAIR_LOCAL_STATE_STORAGE"
        return "NONE"

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": "LOCAL_APPDATA_STATE",
            "status": self.status.value,
            "reason_code": self.reason_code,
            "recommended_action": self.recommended_action,
            "allows_new_analysis_and_transfers": self.allows_new_analysis_and_transfers,
            "state_size_bytes": self.observation.state_size_bytes,
            "local_free_space_bytes": self.observation.local_free_space_bytes,
            "measurement_complete": self.observation.measurement_complete,
            "measurement_error_code": self.observation.measurement_error_code,
            "scanned_entry_count": self.observation.scanned_entry_count,
            "projected_state_size_bytes": self.projected_state_size_bytes,
            "required_local_free_space_bytes": self.required_local_free_space_bytes,
            "soft_quota_bytes": self.policy.soft_quota_bytes,
            "hard_stop_quota_bytes": self.policy.hard_stop_quota_bytes,
            "minimum_free_space_bytes": self.policy.minimum_free_space_bytes,
            "internal_backup_reserve_bytes": self.policy.internal_backup_reserve_bytes,
            "sqlite_full_store": self.sqlite_full_store,
            **self.growth_estimate.to_dict(),
        }


class StateCapacityGate:
    def __init__(
        self,
        *,
        probe: StateCapacityProbe,
        policy: StateCapacityPolicy | None = None,
    ) -> None:
        self._probe = probe
        self._policy = policy or StateCapacityPolicy()
        self._lock = RLock()
        self._sqlite_full_store: str | None = None
        self._latest_report: StateCapacityReport | None = None

    def evaluate(self, estimate: StateGrowthEstimate) -> StateCapacityReport:
        observation = self._measure()
        with self._lock:
            report = _capacity_report(
                policy=self._policy,
                observation=observation,
                estimate=estimate,
                sqlite_full_store=self._sqlite_full_store,
            )
            self._latest_report = report
            return report

    def latch_sqlite_full(self, store: str) -> StateCapacityReport:
        normalized_store = store.strip().lower()
        if not normalized_store:
            raise ValueError("STATE_CAPACITY_SQLITE_FULL_STORE_REQUIRED")
        observation = self._measure()
        with self._lock:
            if self._sqlite_full_store is None:
                self._sqlite_full_store = normalized_store
            estimate = (
                StateGrowthEstimate()
                if self._latest_report is None
                else self._latest_report.growth_estimate
            )
            report = _capacity_report(
                policy=self._policy,
                observation=observation,
                estimate=estimate,
                sqlite_full_store=self._sqlite_full_store,
            )
            self._latest_report = report
            return report

    def latest_report(self) -> StateCapacityReport:
        with self._lock:
            report = self._latest_report
        if report is not None:
            return report
        return self.evaluate(StateGrowthEstimate())

    def _measure(self) -> StateCapacityObservation:
        try:
            return self._probe.measure()
        except Exception:
            return StateCapacityObservation(
                state_size_bytes=0,
                local_free_space_bytes=0,
                measurement_complete=False,
                scanned_entry_count=0,
                measurement_error_code="STATE_CAPACITY_PROBE_FAILED",
            )


def startup_state_growth_estimate() -> StateGrowthEstimate:
    return StateGrowthEstimate(
        estimated_catalog_growth_bytes=32 * MIB,
        estimated_recovery_growth_bytes=16 * MIB,
        estimated_hash_cache_growth_bytes=16 * MIB,
        estimated_log_growth_bytes=8 * MIB,
    )


def snapshot_analysis_growth_estimate(
    *,
    endpoint_count: int,
    entry_count: int | None = None,
    coverage_count: int = 0,
    issue_count: int = 0,
) -> StateGrowthEstimate:
    if endpoint_count < 1:
        raise ValueError("STATE_CAPACITY_ANALYSIS_ENDPOINT_COUNT_MUST_BE_POSITIVE")
    if entry_count is not None and entry_count < 0:
        raise ValueError("STATE_CAPACITY_ANALYSIS_ENTRY_COUNT_NEGATIVE")
    if coverage_count < 0 or issue_count < 0:
        raise ValueError("STATE_CAPACITY_ANALYSIS_AUXILIARY_COUNT_NEGATIVE")

    if entry_count is None:
        catalog_growth = endpoint_count * 64 * MIB
        hash_cache_growth = endpoint_count * 32 * MIB
    else:
        catalog_growth = (
            endpoint_count * 16 * MIB
            + entry_count * 1536
            + coverage_count * 1024
            + issue_count * 2048
        )
        hash_cache_growth = entry_count * 512
    return StateGrowthEstimate(
        estimated_catalog_growth_bytes=catalog_growth,
        estimated_recovery_growth_bytes=endpoint_count * 4 * MIB,
        estimated_hash_cache_growth_bytes=hash_cache_growth,
        estimated_log_growth_bytes=endpoint_count * 8 * MIB,
    )


def run_execution_growth_estimate() -> StateGrowthEstimate:
    return StateGrowthEstimate(
        estimated_catalog_growth_bytes=64 * MIB,
        estimated_recovery_growth_bytes=128 * MIB,
        estimated_hash_cache_growth_bytes=0,
        estimated_log_growth_bytes=32 * MIB,
    )


def _capacity_report(
    *,
    policy: StateCapacityPolicy,
    observation: StateCapacityObservation,
    estimate: StateGrowthEstimate,
    sqlite_full_store: str | None,
) -> StateCapacityReport:
    projected_size = observation.state_size_bytes + estimate.total_bytes
    required_free = (
        estimate.total_bytes
        + policy.internal_backup_reserve_bytes
        + policy.minimum_free_space_bytes
    )
    if sqlite_full_store is not None:
        status = StateCapacityStatus.HARD_STOP
        reason_code = "STATE_CAPACITY_SQLITE_FULL"
    elif not observation.measurement_complete:
        status = StateCapacityStatus.HARD_STOP
        reason_code = observation.measurement_error_code or "STATE_CAPACITY_MEASUREMENT_INCOMPLETE"
    elif projected_size >= policy.hard_stop_quota_bytes:
        status = StateCapacityStatus.HARD_STOP
        reason_code = "STATE_CAPACITY_HARD_QUOTA"
    elif observation.local_free_space_bytes < required_free:
        status = StateCapacityStatus.HARD_STOP
        reason_code = "STATE_CAPACITY_LOCAL_FREE_SPACE_LOW"
    elif projected_size >= policy.soft_quota_bytes:
        status = StateCapacityStatus.SOFT_QUOTA
        reason_code = "STATE_CAPACITY_SOFT_QUOTA"
    else:
        status = StateCapacityStatus.READY
        reason_code = "STATE_CAPACITY_READY"
    return StateCapacityReport(
        status=status,
        reason_code=reason_code,
        observation=observation,
        growth_estimate=estimate,
        projected_state_size_bytes=projected_size,
        required_local_free_space_bytes=required_free,
        policy=policy,
        sqlite_full_store=sqlite_full_store,
    )
