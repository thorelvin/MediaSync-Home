from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointClassificationError,
    LocalEndpointControlAreaClassifier,
    endpoint_marker_checksum,
    local_root_identity_hash,
)
from mediasync_home.application.endpoint_classification import EndpointControlAreaState


def test_local_classifier_covers_all_documented_control_area_states(
    tmp_path: Path,
) -> None:
    classifier = LocalEndpointControlAreaClassifier()
    owner = str(uuid4())
    observed: set[EndpointControlAreaState] = set()

    absent = _root(tmp_path, "absent")
    observed.add(classifier.classify_control_area(absent, local_installation_id=owner).state)

    unknown_empty = _root(tmp_path, "unknown-empty")
    (unknown_empty / ".mediasync").mkdir()
    observed.add(
        classifier.classify_control_area(
            unknown_empty,
            local_installation_id=owner,
        ).state
    )

    unknown_nonempty = _root(tmp_path, "unknown-nonempty")
    (unknown_nonempty / ".mediasync").mkdir()
    (unknown_nonempty / ".mediasync" / "family-photo.txt").write_text(
        "user content",
        encoding="utf-8",
    )
    observed.add(
        classifier.classify_control_area(
            unknown_nonempty,
            local_installation_id=owner,
        ).state
    )

    alias = _root(tmp_path, "alias")
    (alias / ".MEDIASYNC").mkdir()
    observed.add(classifier.classify_control_area(alias, local_installation_id=owner).state)

    partial = _root(tmp_path, "partial")
    (partial / ".mediasync" / "locks").mkdir(parents=True)
    observed.add(classifier.classify_control_area(partial, local_installation_id=owner).state)

    corrupt = _root(tmp_path, "corrupt")
    (corrupt / ".mediasync").mkdir()
    (corrupt / ".mediasync" / "endpoint.json").write_text(
        "{bad-json",
        encoding="utf-8",
    )
    observed.add(classifier.classify_control_area(corrupt, local_installation_id=owner).state)

    valid = _root(tmp_path, "valid")
    _write_control_area(valid, owner_installation_id=owner)
    owned = classifier.classify_control_area(valid, local_installation_id=owner)
    foreign = classifier.classify_control_area(
        valid,
        local_installation_id=str(uuid4()),
    )
    observed.add(owned.state)
    observed.add(foreign.state)

    newer = _root(tmp_path, "newer")
    _write_control_area(
        newer,
        owner_installation_id=owner,
        schema_version=5,
        extra_marker_fields={"future_field": "preserved in checksum"},
    )
    observed.add(classifier.classify_control_area(newer, local_installation_id=owner).state)

    assert observed == set(EndpointControlAreaState)
    assert owned.exclude_from_snapshot is True
    assert owned.mutating_allowed is True
    assert foreign.exclude_from_snapshot is True
    assert foreign.mutating_allowed is False


def test_unknown_control_content_is_never_excluded_or_mutation_ready(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path, "unknown")
    control = root / ".mediasync"
    control.mkdir()
    user_file = control / "family-photo.txt"
    user_file.write_bytes(b"not product metadata")
    before = user_file.read_bytes()

    result = LocalEndpointControlAreaClassifier().classify_control_area(
        root,
        local_installation_id=str(uuid4()),
    )

    assert result.state is EndpointControlAreaState.UNKNOWN_NONEMPTY_DIRECTORY
    assert result.exclude_from_snapshot is False
    assert result.mutating_allowed is False
    assert user_file.read_bytes() == before


def test_marker_checksum_tamper_is_corrupt_and_root_identity_drift_is_partial(
    tmp_path: Path,
) -> None:
    owner = str(uuid4())
    tampered_root = _root(tmp_path, "tampered")
    marker_path = _write_control_area(
        tampered_root,
        owner_installation_id=owner,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["owner_installation_id"] = str(uuid4())
    marker_path.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")

    tampered = LocalEndpointControlAreaClassifier().classify_control_area(
        tampered_root,
        local_installation_id=owner,
    )

    assert tampered.state is EndpointControlAreaState.CORRUPT_MARKER
    assert tampered.reason_codes == ("ENDPOINT_MARKER_CHECKSUM_MISMATCH",)

    drift_root = _root(tmp_path, "identity-drift")
    drift_marker_path = _write_control_area(
        drift_root,
        owner_installation_id=owner,
    )
    drift_marker = json.loads(drift_marker_path.read_text(encoding="utf-8"))
    drift_marker["root_identity_hash"] = "0" * 64
    drift_marker["marker_checksum"] = endpoint_marker_checksum(drift_marker)
    drift_marker_path.write_text(
        json.dumps(drift_marker, sort_keys=True),
        encoding="utf-8",
    )

    drift = LocalEndpointControlAreaClassifier().classify_control_area(
        drift_root,
        local_installation_id=owner,
    )

    assert drift.state is EndpointControlAreaState.PARTIAL_CONTROL_AREA
    assert drift.reason_codes == ("ENDPOINT_ROOT_IDENTITY_MISMATCH",)


def test_ownership_record_mismatch_blocks_valid_classification(tmp_path: Path) -> None:
    owner = str(uuid4())
    root = _root(tmp_path, "ownership-mismatch")
    marker_path = _write_control_area(root, owner_installation_id=owner)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    record_path = root / ".mediasync" / marker["latest_ownership_record"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["owner_installation_id"] = str(uuid4())
    record_path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

    result = LocalEndpointControlAreaClassifier().classify_control_area(
        root,
        local_installation_id=owner,
    )

    assert result.state is EndpointControlAreaState.PARTIAL_CONTROL_AREA
    assert result.reason_codes == ("ENDPOINT_OWNERSHIP_RECORD_MISMATCH",)
    assert result.marker is not None


def test_duplicate_marker_key_and_oversized_marker_fail_closed(tmp_path: Path) -> None:
    owner = str(uuid4())
    duplicate_root = _root(tmp_path, "duplicate")
    marker_path = _write_control_area(
        duplicate_root,
        owner_installation_id=owner,
    )
    marker_text = marker_path.read_text(encoding="utf-8")
    marker_path.write_text(
        marker_text.replace(
            '"application": "MediaSync Home"',
            '"application": "MediaSync Home", "application": "MediaSync Home"',
        ),
        encoding="utf-8",
    )

    duplicate = LocalEndpointControlAreaClassifier().classify_control_area(
        duplicate_root,
        local_installation_id=owner,
    )

    assert duplicate.state is EndpointControlAreaState.CORRUPT_MARKER
    assert duplicate.reason_codes == ("ENDPOINT_MARKER_INVALID",)

    oversized_root = _root(tmp_path, "oversized")
    oversized_marker = _write_control_area(
        oversized_root,
        owner_installation_id=owner,
    )
    oversized_marker.write_bytes(b" " * (64 * 1024 + 1))

    oversized = LocalEndpointControlAreaClassifier().classify_control_area(
        oversized_root,
        local_installation_id=owner,
    )

    assert oversized.state is EndpointControlAreaState.CORRUPT_MARKER
    assert oversized.reason_codes == ("ENDPOINT_MARKER_INVALID",)


def test_classifier_requires_uuid_installation_id_and_existing_ordinary_root(
    tmp_path: Path,
) -> None:
    classifier = LocalEndpointControlAreaClassifier()
    root = _root(tmp_path, "root")

    with pytest.raises(
        LocalEndpointClassificationError,
        match="ENDPOINT_CLASSIFICATION_INSTALLATION_ID_INVALID",
    ):
        classifier.classify_control_area(root, local_installation_id="local-dev")

    with pytest.raises(
        LocalEndpointClassificationError,
        match="ENDPOINT_CLASSIFICATION_ROOT_MISSING",
    ):
        classifier.classify_control_area(
            tmp_path / "missing",
            local_installation_id=str(uuid4()),
        )


def _root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def _write_control_area(
    root: Path,
    *,
    owner_installation_id: str,
    schema_version: int = 4,
    extra_marker_fields: dict[str, object] | None = None,
) -> Path:
    endpoint_id = str(uuid4())
    control_area_id = str(uuid4())
    ownership_epoch = 1
    control = root / ".mediasync"
    ownership = control / "ownership"
    ownership.mkdir(parents=True)
    (control / "locks").mkdir()
    (control / "installations").mkdir()
    ownership_record = {
        "endpoint_id": endpoint_id,
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
        "created_utc": "2026-07-30T12:00:00Z",
        "event": "OWNER_REGISTERED",
    }
    ownership_path = ownership / "epoch-00000001.json"
    ownership_path.write_text(
        json.dumps(ownership_record, sort_keys=True),
        encoding="utf-8",
    )
    marker: dict[str, object] = {
        "control_schema_version": schema_version,
        "endpoint_id": endpoint_id,
        "control_area_id": control_area_id,
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
        "ownership_mode": "EXCLUSIVE_WRITER",
        "expected_volume_id": None,
        "expected_share": None,
        "root_identity_hash_algorithm": "BLAKE3-256",
        "root_identity_hash": local_root_identity_hash(root),
        "latest_ownership_record": "ownership/epoch-00000001.json",
        "created_utc": "2026-07-30T12:00:00Z",
        "updated_utc": "2026-07-30T12:00:00Z",
        "canonicalization_algorithm": "JCS-RFC8785",
        "marker_checksum_algorithm": "BLAKE3-256",
        "application": "MediaSync Home",
    }
    marker.update(extra_marker_fields or {})
    marker["marker_checksum"] = endpoint_marker_checksum(marker)
    marker_path = control / "endpoint.json"
    marker_path.write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )
    return marker_path
