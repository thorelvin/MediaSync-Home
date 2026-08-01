from __future__ import annotations

from dataclasses import replace

import pytest

from mediasync_home.application.version_retention import (
    RetainedVersionRecord,
    RetainedVersionState,
    VersionRetentionError,
    VersionRetentionPlan,
    create_version_retention_plan,
    plan_due_retained_versions,
)


def test_version_retention_plan_is_canonical_and_order_independent() -> None:
    later = replace(_record(), version_object_id="version-b", row_version=2)
    earlier = _record()

    first = create_version_retention_plan(
        plan_id="retention-a",
        cutoff_utc="2026-09-01T00:00:00.000Z",
        created_utc="2026-09-01T00:00:01.000Z",
        candidates=(later, earlier),
    )
    second = create_version_retention_plan(
        plan_id="retention-a",
        cutoff_utc="2026-09-01T00:00:00.000Z",
        created_utc="2026-09-01T00:00:01.000Z",
        candidates=(earlier, later),
    )

    assert first == second
    assert [item.version_object_id for item in first.candidates] == [
        "version-a",
        "version-b",
    ]
    assert len(first.manifest_hash) == 64
    assert first.manifest_json.endswith('"schema_version":2}')


def test_version_retention_planning_excludes_active_recovery_reference() -> None:
    due = (_record(), replace(_record(), version_object_id="version-b", row_version=2))
    store = _Store(due)
    references = _References(blocked={"version-b"})

    outcome = plan_due_retained_versions(
        plan_id="retention-a",
        cutoff_utc="2026-09-01T00:00:00.000Z",
        created_utc="2026-09-01T00:00:01.000Z",
        versions=store,
        recovery_references=references,
    )

    assert outcome.plan is not None
    assert [item.version_object_id for item in outcome.plan.candidates] == ["version-a"]
    assert outcome.excluded[0].version_object_id == "version-b"
    assert outcome.excluded[0].validation_code == "VERSION_RETENTION_RECOVERY_REFERENCE_ACTIVE"
    assert store.recorded == [outcome.plan]


def test_version_retention_planning_does_not_create_empty_plan() -> None:
    store = _Store((_record(),))

    outcome = plan_due_retained_versions(
        plan_id="retention-a",
        cutoff_utc="2026-09-01T00:00:00.000Z",
        created_utc="2026-09-01T00:00:01.000Z",
        versions=store,
        recovery_references=_References(blocked={"version-a"}),
    )

    assert outcome.plan is None
    assert outcome.scanned == 1
    assert store.recorded == []


def test_version_retention_plan_refuses_not_due_candidate() -> None:
    with pytest.raises(VersionRetentionError) as exc_info:
        create_version_retention_plan(
            plan_id="retention-a",
            cutoff_utc="2026-08-01T00:00:00.000Z",
            created_utc="2026-08-01T00:00:01.000Z",
            candidates=(_record(),),
        )

    assert exc_info.value.validation_code == "VERSION_RETENTION_PLAN_CANDIDATE_NOT_DUE"


class _Store:
    def __init__(self, due: tuple[RetainedVersionRecord, ...]) -> None:
        self._due = due
        self.recorded: list[VersionRetentionPlan] = []

    def list_due_retained_versions(
        self,
        *,
        cutoff_utc: str,
        limit: int,
    ) -> tuple[RetainedVersionRecord, ...]:
        return self._due[:limit]

    def create_version_retention_plan(
        self,
        plan: VersionRetentionPlan,
    ) -> VersionRetentionPlan:
        self.recorded.append(plan)
        return plan


class _References:
    def __init__(self, *, blocked: set[str]) -> None:
        self._blocked = blocked

    def released_reference_validation_code(
        self,
        record: RetainedVersionRecord,
    ) -> str | None:
        if record.version_object_id in self._blocked:
            return "VERSION_RETENTION_RECOVERY_REFERENCE_ACTIVE"
        return None


def _record() -> RetainedVersionRecord:
    return RetainedVersionRecord(
        version_object_id="version-a",
        handoff_id="final-file:run-a:operation-a",
        run_id="run-a",
        run_target_id="run-target-a",
        operation_id="operation-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=2,
        owner_installation_id="owner-a",
        ownership_epoch=3,
        final_relative_path="Photos/image.jpg",
        original_fingerprint_json='{"byte_count":9,"content_hash":"' + ("a" * 64) + '"}',
        created_utc="2026-08-01T00:00:00.000Z",
        retention_policy="THIRTY_DAYS",
        retention_until_utc="2026-08-31T00:00:00.000Z",
        manifest_hash="b" * 64,
        state=RetainedVersionState.RETAINED,
        row_version=1,
    )
