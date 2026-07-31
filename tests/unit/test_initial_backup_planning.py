from __future__ import annotations

import pytest

from mediasync_home.application.initial_backup_planning import (
    InitialBackupPlanningEndpoint,
    InitialBackupPlanningError,
    build_initial_backup_plan,
    endpoint_capabilities_hash,
    initial_backup_plan_runnable,
)
from mediasync_home.application.plans import (
    PlanEndpointRole,
    PlanOperationType,
    PlanRiskLevel,
    TargetPreconditionKind,
    verify_plan_checksum,
)
from mediasync_home.application.snapshots import SnapshotFileEntry


def test_initial_backup_plan_orders_directories_and_conservative_file_changes() -> None:
    source = _endpoint(
        role=PlanEndpointRole.SOURCE,
        endpoint_id="source-a",
        entries=(
            _entry("Photos", "photos", "directory"),
            _entry("Readme.txt", "readme.txt", "file", size=7),
            _entry("Photos/New.jpg", "photos/new.jpg", "file", size=128),
        ),
    )
    target = _endpoint(
        role=PlanEndpointRole.TARGET_WRITABLE,
        endpoint_id="target-a",
        target_ordinal=1,
        entries=(
            _entry("Readme.txt", "readme.txt", "file", size=3),
            _entry("Keep-only.txt", "keep-only.txt", "file", size=11),
        ),
    )

    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(source, target),
    )

    assert result.state == "SEALED"
    assert result.reason_code == "INITIAL_BACKUP_PLAN_READY_FOR_REVIEW"
    assert result.plan is not None
    assert verify_plan_checksum(result.plan) is True
    assert [
        (
            operation.operation_type,
            operation.target_precondition_kind,
            operation.target_relative_path,
        )
        for operation in result.plan.operations
    ] == [
        (
            PlanOperationType.CREATE_DIRECTORY,
            TargetPreconditionKind.ABSENT,
            "Photos",
        ),
        (
            PlanOperationType.COPY_NEW,
            TargetPreconditionKind.MATCH_FINGERPRINT,
            "Readme.txt",
        ),
        (
            PlanOperationType.COPY_NEW,
            TargetPreconditionKind.ABSENT,
            "Photos/New.jpg",
        ),
    ]
    assert len(result.plan.dependencies) == 1
    assert result.plan.dependencies[0].after_operation_id == result.plan.operations[2].operation_id
    writable_target = next(
        endpoint
        for endpoint in result.plan.endpoints
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
    )
    assert writable_target.planned_operations == 3
    assert writable_target.planned_bytes == 135
    assert result.plan.risk_summary["highest"] == PlanRiskLevel.MEDIUM.value
    assert initial_backup_plan_runnable(result.plan) is True


def test_initial_backup_plan_blocks_nonempty_directory_file_conflict() -> None:
    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _endpoint(
                role=PlanEndpointRole.SOURCE,
                endpoint_id="source-a",
                entries=(_entry("Archive", "archive", "file", size=5),),
            ),
            _endpoint(
                role=PlanEndpointRole.TARGET_WRITABLE,
                endpoint_id="target-a",
                target_ordinal=1,
                entries=(
                    _entry("Archive", "archive", "directory"),
                    _entry("Archive/old.txt", "archive/old.txt", "file", size=2),
                ),
            ),
        ),
    )

    assert result.plan is not None
    assert result.reason_code == "INITIAL_BACKUP_PLAN_BLOCKED"
    assert result.plan.risk_summary["highest"] == PlanRiskLevel.BLOCKED.value
    assert result.plan.operations[0].operation_type is (
        PlanOperationType.BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN
    )
    assert result.plan.operations[0].reason_code == "SOURCE_FILE_TARGET_TYPE_CONFLICT"


def test_initial_backup_plan_reports_no_changes_for_matching_directory_structure() -> None:
    source = _endpoint(
        role=PlanEndpointRole.SOURCE,
        endpoint_id="source-a",
        entries=(_entry("Empty", "empty", "directory"),),
    )
    target = _endpoint(
        role=PlanEndpointRole.TARGET_WRITABLE,
        endpoint_id="target-a",
        target_ordinal=1,
        entries=(_entry("Empty", "empty", "directory"),),
    )

    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(source, target),
    )

    assert result.plan is None
    assert result.state == "NO_CHANGES"
    assert result.reason_code == "INITIAL_BACKUP_PLAN_NO_CHANGES"


def test_initial_backup_plan_rejects_case_collision_in_sealed_input() -> None:
    with pytest.raises(
        InitialBackupPlanningError,
        match="INITIAL_BACKUP_PLAN_SOURCE_CASE_COLLISION",
    ):
        build_initial_backup_plan(
            plan_id="plan-a",
            analysis_id="analysis-a",
            job_id="job-a",
            job_revision_id="job-rev-a",
            endpoints=(
                _endpoint(
                    role=PlanEndpointRole.SOURCE,
                    endpoint_id="source-a",
                    entries=(
                        _entry("A.txt", "a.txt", "file", size=1),
                        _entry("a.txt", "a.txt-source-sensitive", "file", size=1),
                    ),
                ),
                _endpoint(
                    role=PlanEndpointRole.TARGET_WRITABLE,
                    endpoint_id="target-a",
                    target_ordinal=1,
                    entries=(),
                ),
            ),
        )


def test_endpoint_capabilities_hash_is_canonical() -> None:
    assert endpoint_capabilities_hash({"b": True, "a": 1}) == (
        endpoint_capabilities_hash({"a": 1, "b": True})
    )


def test_file_only_initial_plan_is_runnable() -> None:
    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _endpoint(
                role=PlanEndpointRole.SOURCE,
                endpoint_id="source-a",
                entries=(_entry("A.txt", "a.txt", "file", size=1),),
            ),
            _endpoint(
                role=PlanEndpointRole.TARGET_WRITABLE,
                endpoint_id="target-a",
                target_ordinal=1,
                entries=(),
            ),
        ),
    )

    assert result.plan is not None
    assert initial_backup_plan_runnable(result.plan) is True


def test_initial_backup_plan_builds_independent_operations_for_each_target() -> None:
    source = _endpoint(
        role=PlanEndpointRole.SOURCE,
        endpoint_id="source-a",
        entries=(
            _entry("Photos", "photos", "directory"),
            _entry("Photos/A.jpg", "photos/a.jpg", "file", size=9),
        ),
    )
    target_a = _endpoint(
        role=PlanEndpointRole.TARGET_WRITABLE,
        endpoint_id="target-a",
        target_ordinal=1,
        entries=(_entry("Photos", "photos", "directory"),),
    )
    target_b = _endpoint(
        role=PlanEndpointRole.TARGET_WRITABLE,
        endpoint_id="target-b",
        target_ordinal=2,
        entries=(),
    )

    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(target_b, source, target_a),
    )

    assert result.plan is not None
    assert [
        (
            operation.target_endpoint_id,
            operation.operation_type,
            operation.target_relative_path,
        )
        for operation in result.plan.operations
    ] == [
        ("target-a", PlanOperationType.COPY_NEW, "Photos/A.jpg"),
        ("target-b", PlanOperationType.CREATE_DIRECTORY, "Photos"),
        ("target-b", PlanOperationType.COPY_NEW, "Photos/A.jpg"),
    ]
    writable_endpoints = {
        endpoint.endpoint_id: endpoint
        for endpoint in result.plan.endpoints
        if endpoint.role is PlanEndpointRole.TARGET_WRITABLE
    }
    assert writable_endpoints["target-a"].planned_operations == 1
    assert writable_endpoints["target-a"].planned_bytes == 9
    assert writable_endpoints["target-b"].planned_operations == 2
    assert writable_endpoints["target-b"].planned_bytes == 9
    assert len(result.plan.dependencies) == 1
    before = result.plan.dependencies[0].before_operation_id
    after = result.plan.dependencies[0].after_operation_id
    operations = {
        operation.operation_id: operation
        for operation in result.plan.operations
    }
    assert operations[before].target_endpoint_id == "target-b"
    assert operations[before].operation_type is PlanOperationType.CREATE_DIRECTORY
    assert operations[after].target_endpoint_id == "target-b"
    assert operations[after].operation_type is PlanOperationType.COPY_NEW
    assert verify_plan_checksum(result.plan) is True


def _endpoint(
    *,
    role: PlanEndpointRole,
    endpoint_id: str,
    entries: tuple[SnapshotFileEntry, ...],
    target_ordinal: int | None = None,
) -> InitialBackupPlanningEndpoint:
    writable = role is PlanEndpointRole.TARGET_WRITABLE
    return InitialBackupPlanningEndpoint(
        endpoint_id=endpoint_id,
        endpoint_revision_id=f"{endpoint_id}-revision",
        endpoint_generation=2 if writable else 1,
        snapshot_id=f"{endpoint_id}-snapshot",
        snapshot_checksum="1" * 64,
        root_case_context_hash="2" * 64,
        root_case_mode="CASE_INSENSITIVE",
        capabilities_hash="3" * 64,
        entries=entries,
        role=role,
        target_ordinal=target_ordinal,
        required_owner_installation_id="owner-a" if writable else None,
        required_ownership_epoch=1 if writable else None,
        control_schema_version=4 if writable else None,
    )


def _entry(
    path: str,
    comparison_key: str,
    object_type: str,
    *,
    size: int | None = None,
) -> SnapshotFileEntry:
    return SnapshotFileEntry(
        entry_id=f"entry-{path}",
        relative_path=path,
        comparison_key=comparison_key,
        object_type=object_type,
        size_bytes=size,
    )
