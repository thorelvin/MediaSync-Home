from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.writable_endpoint_registration import (
    LocalWritableEndpointControlAreaProvisioner,
)
from mediasync_home.application.endpoint_classification import EndpointControlAreaState
from mediasync_home.application.endpoint_capabilities import EndpointCapabilityProbeScope
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCandidate,
    WritableEndpointRegistrationError,
    WritableEndpointTargetIds,
)


def test_local_registration_creates_verified_owned_control_area(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    owner_id = str(uuid4())
    endpoint_id = str(uuid4())
    provisioner = LocalWritableEndpointControlAreaProvisioner()
    intent_id = str(uuid4())
    prepared = provisioner.prepare_new_control_area(
        _candidate(root, endpoint_id=endpoint_id),
        intent_id=intent_id,
        target_ids=WritableEndpointTargetIds(
            target_ordinal=1,
            endpoint_revision_id=str(uuid4()),
            control_area_id=str(uuid4()),
        ),
        owner_installation_id=owner_id,
        created_utc="2026-07-31T10:00:00Z",
    )
    capability_evidence = provisioner.apply_prepared_control_area(
        prepared, intent_id=intent_id
    )

    classification = LocalEndpointControlAreaClassifier().classify_control_area(
        root,
        local_installation_id=owner_id,
    )
    assert classification.state is EndpointControlAreaState.VALID_OWNED
    assert classification.marker is not None
    assert classification.marker.endpoint_id == endpoint_id
    assert classification.marker.control_area_id == prepared.control_area_id
    assert classification.marker.marker_checksum == prepared.marker_checksum
    assert not tuple((root / ".mediasync").rglob("*.probe"))
    profile = capability_evidence.validated_profile(
        expected_scope=EndpointCapabilityProbeScope.CONTROLLED_WRITABLE
    )
    assert profile.supports_atomic_rename
    assert profile.supports_no_overwrite_insert
    assert profile.supports_atomic_replace
    assert profile.supports_file_flush
    assert profile.supports_write_through_move

    marker = json.loads((root / ".mediasync" / "endpoint.json").read_text(encoding="utf-8"))
    assert marker["control_schema_version"] == 4
    assert marker["ownership_epoch"] == 1
    assert (
        root
        / ".mediasync"
        / "ownership"
        / "epoch-00000001.json"
    ).is_file()
    assert (root / ".mediasync" / "locks").is_dir()
    assert (root / ".mediasync" / "installations").is_dir()

    provisioner.apply_prepared_control_area(prepared, intent_id=intent_id)
    assert not tuple(root.glob(".mediasync-register-*"))


def test_local_registration_resumes_exact_partial_staging(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    owner_id = str(uuid4())
    intent_id = str(uuid4())
    provisioner = LocalWritableEndpointControlAreaProvisioner()
    prepared = provisioner.prepare_new_control_area(
        _candidate(root),
        intent_id=intent_id,
        target_ids=WritableEndpointTargetIds(
            target_ordinal=1,
            endpoint_revision_id=str(uuid4()),
            control_area_id=str(uuid4()),
        ),
        owner_installation_id=owner_id,
        created_utc="2026-07-31T10:00:00Z",
    )
    staging = root / f".mediasync-register-{intent_id}-1"
    staging.mkdir()
    (staging / "ownership").mkdir()

    provisioner.apply_prepared_control_area(prepared, intent_id=intent_id)

    assert (root / ".mediasync" / "endpoint.json").is_file()
    assert not staging.exists()


def test_local_registration_never_adopts_unknown_control_content(tmp_path: Path) -> None:
    root = tmp_path / "target"
    control = root / ".mediasync"
    control.mkdir(parents=True)
    user_file = control / "notes.txt"
    user_file.write_text("do not touch", encoding="utf-8")
    provisioner = LocalWritableEndpointControlAreaProvisioner()

    with pytest.raises(
        WritableEndpointRegistrationError,
        match="WRITABLE_ENDPOINT_CONTROL_AREA_UNKNOWN_NONEMPTY_DIRECTORY",
    ):
        provisioner.prepare_new_control_area(
            _candidate(root),
            intent_id=str(uuid4()),
            target_ids=WritableEndpointTargetIds(
                target_ordinal=1,
                endpoint_revision_id=str(uuid4()),
                control_area_id=str(uuid4()),
            ),
            owner_installation_id=str(uuid4()),
            created_utc="2026-07-31T10:00:00Z",
        )

    assert user_file.read_text(encoding="utf-8") == "do not touch"


def test_local_registration_blocks_unexpected_private_staging_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "target"
    root.mkdir()
    owner_id = str(uuid4())
    intent_id = str(uuid4())
    provisioner = LocalWritableEndpointControlAreaProvisioner()
    prepared = provisioner.prepare_new_control_area(
        _candidate(root),
        intent_id=intent_id,
        target_ids=WritableEndpointTargetIds(
            target_ordinal=1,
            endpoint_revision_id=str(uuid4()),
            control_area_id=str(uuid4()),
        ),
        owner_installation_id=owner_id,
        created_utc="2026-07-31T10:00:00Z",
    )
    staging = root / f".mediasync-register-{intent_id}-1"
    staging.mkdir()
    unexpected = staging / "unknown.txt"
    unexpected.write_text("preserve", encoding="utf-8")

    with pytest.raises(
        WritableEndpointRegistrationError,
        match="WRITABLE_ENDPOINT_REGISTRATION_STAGING_UNSAFE",
    ):
        provisioner.apply_prepared_control_area(prepared, intent_id=intent_id)

    assert unexpected.read_text(encoding="utf-8") == "preserve"
    assert not (root / ".mediasync").exists()


def _candidate(
    root: Path,
    *,
    endpoint_id: str | None = None,
) -> WritableEndpointRegistrationCandidate:
    return WritableEndpointRegistrationCandidate(
        job_id=str(uuid4()),
        job_revision_id=str(uuid4()),
        target_ordinal=1,
        endpoint_id=endpoint_id or str(uuid4()),
        endpoint_revision_id=str(uuid4()),
        endpoint_generation=1,
        display_name="Target",
        root_uri=root.as_uri(),
    )
