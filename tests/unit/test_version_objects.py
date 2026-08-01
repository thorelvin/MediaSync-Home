from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.version_objects import (
    VersionObjectManifestError,
    parse_version_object_manifest,
    require_version_object_manifest_binding,
    version_object_manifest_from_operation,
)


def test_version_manifest_binds_retention_and_round_trips_canonically() -> None:
    operation = _operation()

    manifest = version_object_manifest_from_operation(
        operation,
        created_utc="2026-08-01T10:15:30.125Z",
    )
    parsed = parse_version_object_manifest(manifest.canonical_json)

    assert parsed == manifest
    assert parsed.job_id == "job-a"
    assert parsed.job_revision_id == "job-rev-a"
    assert parsed.retention_policy == "THIRTY_DAYS"
    assert parsed.retention_until_utc == "2026-08-31T10:15:30.125Z"
    assert parsed.fingerprint_byte_count == 9
    assert parsed.fingerprint_content_hash == "a" * 64
    require_version_object_manifest_binding(parsed, operation=operation)


def test_version_manifest_rejects_tampered_self_hash() -> None:
    manifest = version_object_manifest_from_operation(
        _operation(),
        created_utc="2026-08-01T10:15:30.125Z",
    )
    payload = json.loads(manifest.canonical_json)
    payload["retention_until_utc"] = "2027-08-31T10:15:30.125Z"
    tampered = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(VersionObjectManifestError) as exc_info:
        parse_version_object_manifest(tampered)

    assert exc_info.value.validation_code == "VERSION_OBJECT_MANIFEST_HASH_MISMATCH"


def test_version_manifest_rejects_noncanonical_json() -> None:
    manifest = version_object_manifest_from_operation(
        _operation(),
        created_utc="2026-08-01T10:15:30.125Z",
    )

    with pytest.raises(VersionObjectManifestError) as exc_info:
        parse_version_object_manifest(manifest.canonical_json + "\n")

    assert exc_info.value.validation_code == "VERSION_OBJECT_MANIFEST_NOT_CANONICAL"


def test_version_manifest_binding_rejects_different_job_revision() -> None:
    operation = _operation()
    manifest = version_object_manifest_from_operation(
        operation,
        created_utc="2026-08-01T10:15:30.125Z",
    )

    with pytest.raises(VersionObjectManifestError) as exc_info:
        require_version_object_manifest_binding(
            manifest,
            operation=replace(operation, job_revision_id="job-rev-b"),
        )

    assert exc_info.value.validation_code == "VERSION_OBJECT_MANIFEST_BINDING_MISMATCH"


def _operation() -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-target-a",
            operation_id="operation-a",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=3,
            owner_installation_id="owner-a",
            ownership_epoch=4,
            lease_id="lease-a",
            lease_resource_key="endpoint:target-a",
            fencing_token=5,
            final_relative_path="Photos/image.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
            job_id="job-a",
            job_revision_id="job-rev-a",
            retention_policy="THIRTY_DAYS",
        ),
        expected_target_fingerprint_json=json.dumps(
            {"byte_count": 9, "content_hash": "a" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
