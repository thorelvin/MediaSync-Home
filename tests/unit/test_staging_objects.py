from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

import pytest

from mediasync_home.application.recovery_operations import RecoveryOperationKind
from mediasync_home.application.staging_objects import (
    StagingObjectManifestError,
    StagingObjectManifest,
    create_staging_object_manifest,
    parse_staging_object_manifest,
    require_staging_object_manifest_binding,
)


def test_staging_object_manifest_round_trips_canonical_binding() -> None:
    manifest = _manifest()

    parsed = parse_staging_object_manifest(manifest.canonical_json)

    assert parsed == manifest
    require_staging_object_manifest_binding(
        parsed,
        staging_object_id="operation-a",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        final_relative_path=r"Photos\image.jpg",
        operation_kind=RecoveryOperationKind.COPY_NEW,
        fingerprint_content_hash="a" * 64,
        fingerprint_byte_count=5,
        operation_id="operation-a",
    )


def test_staging_object_manifest_rejects_tampered_self_hash() -> None:
    manifest = _manifest()
    payload = json.loads(manifest.canonical_json)
    payload["endpoint_generation"] = 2
    tampered = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(StagingObjectManifestError) as exc_info:
        parse_staging_object_manifest(tampered)

    assert exc_info.value.validation_code == "STAGING_OBJECT_MANIFEST_HASH_MISMATCH"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda payload: payload.update({"unexpected": True}),
            "STAGING_OBJECT_MANIFEST_SHAPE_INVALID",
        ),
        (
            lambda payload: payload.update({"payload_name": "other.payload"}),
            "STAGING_OBJECT_MANIFEST_PAYLOAD_NAME_INVALID",
        ),
        (
            lambda payload: payload.update({"final_relative_path": "../escape"}),
            "STAGING_OBJECT_MANIFEST_RELATIVE_PATH_INVALID",
        ),
    ],
)
def test_staging_object_manifest_rejects_unknown_or_altered_fields(
    mutate: Callable[[dict[str, object]], None],
    expected_code: str,
) -> None:
    manifest = _manifest()
    payload = json.loads(manifest.canonical_json)
    mutate(payload)
    altered = _canonical_with_recomputed_hash(payload)

    with pytest.raises(StagingObjectManifestError) as exc_info:
        parse_staging_object_manifest(altered)

    assert exc_info.value.validation_code == expected_code


def test_staging_object_manifest_rejects_noncanonical_json() -> None:
    manifest = _manifest()
    noncanonical = json.dumps(json.loads(manifest.canonical_json), indent=2)

    with pytest.raises(StagingObjectManifestError) as exc_info:
        parse_staging_object_manifest(noncanonical)

    assert exc_info.value.validation_code == "STAGING_OBJECT_MANIFEST_NOT_CANONICAL"


@pytest.mark.parametrize(
    "binding_override",
    [
        {"endpoint_generation": 2},
        {"final_relative_path": "Photos/other.jpg"},
        {"fingerprint_content_hash": "b" * 64},
        {"fingerprint_byte_count": 6},
        {"target_endpoint_revision_id": "target-rev-b"},
    ],
)
def test_staging_object_manifest_rejects_binding_mismatch(
    binding_override: dict[str, object],
) -> None:
    manifest = _manifest()
    binding: dict[str, object] = {
        "staging_object_id": "operation-a",
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "target_endpoint_id": "target-a",
        "target_endpoint_revision_id": "target-rev-a",
        "endpoint_generation": 1,
        "final_relative_path": "Photos/image.jpg",
        "operation_kind": RecoveryOperationKind.COPY_NEW,
        "fingerprint_content_hash": "a" * 64,
        "fingerprint_byte_count": 5,
        "operation_id": "operation-a",
    }
    binding.update(binding_override)

    with pytest.raises(StagingObjectManifestError) as exc_info:
        require_staging_object_manifest_binding(manifest, **binding)  # type: ignore[arg-type]

    assert exc_info.value.validation_code == "STAGING_OBJECT_MANIFEST_BINDING_MISMATCH"


def _manifest() -> StagingObjectManifest:
    return create_staging_object_manifest(
        staging_object_id="operation-a",
        operation_id="operation-a",
        operation_kind=RecoveryOperationKind.COPY_NEW,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        source_endpoint_id="source-a",
        source_endpoint_revision_id="source-rev-a",
        source_relative_path=r"Photos\image.jpg",
        final_relative_path=r"Photos\image.jpg",
        fingerprint={"byte_count": 5, "content_hash": "a" * 64},
    )


def _canonical_with_recomputed_hash(payload: dict[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    canonical_body = json.dumps(body, sort_keys=True, separators=(",", ":"))
    payload["manifest_hash"] = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
