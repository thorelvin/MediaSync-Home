from __future__ import annotations

import pytest

from mediasync_home.application.hash_evidence import (
    CURRENT_READ_HASH_ALGORITHM,
    CURRENT_READ_HASH_SCHEMA_VERSION,
    CurrentReadHashEvidence,
    HashEvidenceKind,
)
from mediasync_home.application.initial_backup_planning import (
    DestructivePlanningEvidence,
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


@pytest.mark.parametrize(
    ("target_object_type", "target_size"),
    (
        ("file", 2),
        ("directory", None),
    ),
)
def test_incomplete_scan_coverage_blocks_every_supported_replacement(
    target_object_type: str,
    target_size: int | None,
) -> None:
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
                scan_coverage_complete=False,
            ),
            _endpoint(
                role=PlanEndpointRole.TARGET_WRITABLE,
                endpoint_id="target-a",
                target_ordinal=1,
                entries=(
                    _entry(
                        "Archive",
                        "archive",
                        target_object_type,
                        size=target_size,
                    ),
                ),
            ),
        ),
    )

    assert result.plan is not None
    assert initial_backup_plan_runnable(result.plan) is False
    assert result.plan.operation_count == 1
    operation = result.plan.operations[0]
    assert operation.operation_type is (
        PlanOperationType.BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN
    )
    assert operation.target_precondition_kind is TargetPreconditionKind.NONE
    assert operation.reason_code == "DESTRUCTIVE_SCAN_COVERAGE_INCOMPLETE"


def test_endpoint_identity_mismatch_blocks_replacement() -> None:
    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _endpoint(
                role=PlanEndpointRole.SOURCE,
                endpoint_id="source-a",
                entries=(_entry("Readme.txt", "readme.txt", "file", size=5),),
            ),
            _endpoint(
                role=PlanEndpointRole.TARGET_WRITABLE,
                endpoint_id="target-a",
                target_ordinal=1,
                entries=(_entry("Readme.txt", "readme.txt", "file", size=2),),
                identity_matches_endpoint_revision=False,
            ),
        ),
    )

    assert result.plan is not None
    assert initial_backup_plan_runnable(result.plan) is False
    assert result.plan.operations[0].reason_code == (
        "DESTRUCTIVE_ENDPOINT_IDENTITY_MISMATCH"
    )


def test_unsafe_destructive_evidence_still_allows_no_overwrite_copy_new() -> None:
    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(
            _endpoint(
                role=PlanEndpointRole.SOURCE,
                endpoint_id="source-a",
                entries=(_entry("New.txt", "new.txt", "file", size=5),),
                scan_coverage_complete=False,
                identity_matches_endpoint_revision=False,
            ),
            _endpoint(
                role=PlanEndpointRole.TARGET_WRITABLE,
                endpoint_id="target-a",
                target_ordinal=1,
                entries=(),
                scan_coverage_complete=False,
                identity_matches_endpoint_revision=False,
            ),
        ),
    )

    assert result.plan is not None
    assert initial_backup_plan_runnable(result.plan) is True
    operation = result.plan.operations[0]
    assert operation.operation_type is PlanOperationType.COPY_NEW
    assert operation.target_precondition_kind is TargetPreconditionKind.ABSENT
    assert operation.reason_code == "COPY_NEW"


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


def test_current_read_hash_evidence_aggregates_identical_file_as_no_change() -> None:
    source_entry = _entry("Readme.txt", "readme.txt", "file", size=7)
    target_entry = _entry("Readme.txt", "readme.txt", "file", size=7)
    source = _endpoint(
        role=PlanEndpointRole.SOURCE,
        endpoint_id="source-a",
        entries=(source_entry,),
        hash_evidence=(
            _hash_evidence("source-a", source_entry, content_hash="a" * 64),
        ),
    )
    target = _endpoint(
        role=PlanEndpointRole.TARGET_WRITABLE,
        endpoint_id="target-a",
        target_ordinal=1,
        entries=(target_entry,),
        hash_evidence=(
            _hash_evidence("target-a", target_entry, content_hash="a" * 64),
        ),
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


def test_current_read_hash_evidence_keeps_changed_same_size_file_for_review() -> None:
    source_entry = _entry("Readme.txt", "readme.txt", "file", size=7)
    target_entry = _entry("Readme.txt", "readme.txt", "file", size=7)
    source = _endpoint(
        role=PlanEndpointRole.SOURCE,
        endpoint_id="source-a",
        entries=(source_entry,),
        hash_evidence=(
            _hash_evidence("source-a", source_entry, content_hash="a" * 64),
        ),
    )
    target = _endpoint(
        role=PlanEndpointRole.TARGET_WRITABLE,
        endpoint_id="target-a",
        target_ordinal=1,
        entries=(target_entry,),
        hash_evidence=(
            _hash_evidence("target-a", target_entry, content_hash="b" * 64),
        ),
    )

    result = build_initial_backup_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(source, target),
    )

    assert result.plan is not None
    assert result.plan.operations[0].reason_code == "REPLACE_WITH_VERSION"
    assert result.plan.risk_summary["highest"] == PlanRiskLevel.MEDIUM.value


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
    hash_evidence: tuple[CurrentReadHashEvidence, ...] = (),
    scan_coverage_complete: bool = True,
    identity_matches_endpoint_revision: bool = True,
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
        destructive_evidence=DestructivePlanningEvidence(
            scan_coverage_complete=scan_coverage_complete,
            snapshot_identity_matches_endpoint_revision=(
                identity_matches_endpoint_revision
            ),
        ),
        entries=entries,
        role=role,
        hash_evidence=hash_evidence,
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
        birthtime_ns=1_000 if object_type in {"file", "directory"} else None,
        identity_fingerprint_hash="a" * 64 if object_type == "file" else None,
    )


def _hash_evidence(
    endpoint_id: str,
    entry: SnapshotFileEntry,
    *,
    content_hash: str,
) -> CurrentReadHashEvidence:
    return CurrentReadHashEvidence(
        snapshot_id=f"{endpoint_id}-snapshot",
        entry_id=entry.entry_id,
        endpoint_id=endpoint_id,
        content_hash=content_hash,
        size_bytes=entry.size_bytes or 0,
        algorithm=CURRENT_READ_HASH_ALGORITHM,
        hash_schema_version=CURRENT_READ_HASH_SCHEMA_VERSION,
        evidence_kind=HashEvidenceKind.CURRENT_READ_HASH,
        read_started_fingerprint_hash="c" * 64,
        read_completed_fingerprint_hash="c" * 64,
        computed_utc="2026-07-31T10:00:00Z",
    )
