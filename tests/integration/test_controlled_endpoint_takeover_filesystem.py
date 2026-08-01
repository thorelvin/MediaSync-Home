from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediasync_home.adapters.endpoint_leases import (
    LocalEndpointLeaseAuthority,
    Win32EndpointLockOpener,
)
from mediasync_home.adapters.endpoint_takeover import LocalEndpointTakeoverFilesystem
from mediasync_home.adapters.local_endpoint_classifier import (
    LocalEndpointControlAreaClassifier,
)
from mediasync_home.adapters.writable_endpoint_registration import (
    LocalWritableEndpointControlAreaProvisioner,
)
from mediasync_home.application.endpoint_takeover import (
    EndpointTakeoverCandidate,
    EndpointTakeoverError,
    PreparedEndpointTakeover,
)
from mediasync_home.application.endpoint_classification import EndpointControlAreaState
from mediasync_home.application.runs import EndpointLeaseRequest
from mediasync_home.application.writable_endpoint_registration import (
    WritableEndpointRegistrationCandidate,
    WritableEndpointTargetIds,
)


FOREIGN_OWNER = "11111111-1111-4111-8111-111111111111"
LOCAL_OWNER = "22222222-2222-4222-8222-222222222222"
ENDPOINT_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_REVISION_ID = "44444444-4444-4444-8444-444444444444"
RESULTING_REVISION_ID = "55555555-5555-4555-8555-555555555555"
CONTROL_AREA_ID = "66666666-6666-4666-8666-666666666666"
REGISTRATION_INTENT_ID = "77777777-7777-4777-8777-777777777777"
TAKEOVER_INTENT_ID = "88888888-8888-4888-8888-888888888888"


def test_controlled_takeover_preserves_foreign_namespace_and_advances_epoch(
    tmp_path: Path,
) -> None:
    candidate = _foreign_candidate(tmp_path)
    root = tmp_path / "target"
    old_marker = json.loads(
        (root / ".mediasync" / "endpoint.json").read_text(encoding="utf-8")
    )
    foreign_payload = (
        root
        / ".mediasync"
        / "installations"
        / _namespace(FOREIGN_OWNER)
        / "objects"
        / "keep.bin"
    )
    foreign_payload.write_bytes(b"foreign-data")
    filesystem = LocalEndpointTakeoverFilesystem()

    prepared = _prepare(filesystem, candidate)
    filesystem.apply_prepared_takeover(prepared, intent_id=TAKEOVER_INTENT_ID)

    classification = LocalEndpointControlAreaClassifier().classify_control_area(
        root,
        local_installation_id=LOCAL_OWNER,
    )
    new_marker = json.loads(
        (root / ".mediasync" / "endpoint.json").read_text(encoding="utf-8")
    )
    assert classification.state is EndpointControlAreaState.VALID_OWNED
    assert classification.marker is not None
    assert classification.marker.owner_installation_id == LOCAL_OWNER
    assert classification.marker.ownership_epoch == 2
    assert new_marker["created_utc"] == old_marker["created_utc"]
    assert new_marker["updated_utc"] == "2026-08-01T10:01:00Z"
    assert foreign_payload.read_bytes() == b"foreign-data"
    assert (root / ".mediasync" / prepared.takeover_record_path).is_file()
    assert (root / ".mediasync" / prepared.ownership_record_path).is_file()
    stale_attempt = LocalEndpointLeaseAuthority(
        target_roots={"target": root},
        token_store=_TokenStore(),
    ).acquire_endpoint_lease(
        EndpointLeaseRequest(
            run_id="run-old",
            run_target_id="target-old",
            endpoint_id=ENDPOINT_ID,
            endpoint_revision_id=SOURCE_REVISION_ID,
            resource_key="target",
            required_owner_installation_id=FOREIGN_OWNER,
            required_ownership_epoch=1,
        )
    )
    assert stale_attempt.acquired is False
    assert stale_attempt.validation_codes == ("ENDPOINT_OWNER_MISMATCH",)


def test_controlled_takeover_rejects_live_foreign_recovery(tmp_path: Path) -> None:
    candidate = _foreign_candidate(tmp_path)
    recovery = (
        tmp_path
        / "target"
        / ".mediasync"
        / "installations"
        / _namespace(FOREIGN_OWNER)
        / "recovery"
        / "run-a"
    )
    recovery.mkdir()
    (recovery / "segment-000000.intent.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(EndpointTakeoverError) as exc_info:
        _prepare(LocalEndpointTakeoverFilesystem(), candidate)

    assert exc_info.value.validation_code == "OWNERSHIP_RECOVERY_REQUIRED"
    assert not (
        tmp_path / "target" / ".mediasync" / f"ownership/epoch-{2:08d}.json"
    ).exists()


def test_controlled_takeover_rejects_contended_mutation_lock(tmp_path: Path) -> None:
    candidate = _foreign_candidate(tmp_path)
    lock_path = tmp_path / "target" / ".mediasync" / "locks" / "mutation.lock"
    lock = Win32EndpointLockOpener().acquire_exclusive_lock(lock_path)
    try:
        with pytest.raises(EndpointTakeoverError) as exc_info:
            _prepare(LocalEndpointTakeoverFilesystem(), candidate)
    finally:
        lock.close()

    assert exc_info.value.validation_code == "ENDPOINT_LEASE_UNAVAILABLE"
    assert exc_info.value.retryable is True


@pytest.mark.parametrize("published_marker", [False, True])
def test_controlled_takeover_resumes_after_filesystem_crash_windows(
    tmp_path: Path,
    published_marker: bool,
) -> None:
    candidate = _foreign_candidate(tmp_path)
    root = tmp_path / "target"
    control = root / ".mediasync"
    filesystem = LocalEndpointTakeoverFilesystem()
    prepared = _prepare(filesystem, candidate)
    _write_prepared_control_records(control, prepared)
    if published_marker:
        (control / "endpoint.json").write_text(
            f"{prepared.marker_payload_json}\n",
            encoding="utf-8",
        )

    filesystem.apply_prepared_takeover(prepared, intent_id=TAKEOVER_INTENT_ID)

    classification = LocalEndpointControlAreaClassifier().classify_control_area(
        root,
        local_installation_id=LOCAL_OWNER,
    )
    assert classification.state is EndpointControlAreaState.VALID_OWNED
    assert classification.marker is not None
    assert classification.marker.marker_checksum == prepared.marker_checksum


def _foreign_candidate(tmp_path: Path) -> EndpointTakeoverCandidate:
    root = tmp_path / "target"
    root.mkdir()
    registration = LocalWritableEndpointControlAreaProvisioner()
    registered = registration.prepare_new_control_area(
        WritableEndpointRegistrationCandidate(
            job_id="job-a",
            job_revision_id="revision-a",
            target_ordinal=1,
            endpoint_id=ENDPOINT_ID,
            endpoint_revision_id=SOURCE_REVISION_ID,
            endpoint_generation=1,
            display_name="Target",
            root_uri=root.as_uri(),
        ),
        intent_id=REGISTRATION_INTENT_ID,
        target_ids=WritableEndpointTargetIds(
            target_ordinal=1,
            endpoint_revision_id="99999999-9999-4999-8999-999999999999",
            control_area_id=CONTROL_AREA_ID,
        ),
        owner_installation_id=FOREIGN_OWNER,
        created_utc="2026-08-01T10:00:00Z",
    )
    registration.apply_prepared_control_area(
        registered,
        intent_id=REGISTRATION_INTENT_ID,
    )
    return EndpointTakeoverCandidate(
        job_id="job-a",
        job_revision_id="revision-a",
        target_ordinal=1,
        endpoint_id=ENDPOINT_ID,
        endpoint_revision_id=SOURCE_REVISION_ID,
        endpoint_generation=1,
        display_name="Target",
        root_uri=root.as_uri(),
        control_area_id=CONTROL_AREA_ID,
        foreign_owner_installation_id=FOREIGN_OWNER,
        foreign_ownership_epoch=1,
        root_identity_hash_algorithm=registered.root_identity_hash_algorithm,
        root_identity_hash=registered.root_identity_hash,
        marker_checksum_algorithm=registered.marker_checksum_algorithm,
        marker_checksum=registered.marker_checksum,
    )


def _prepare(
    filesystem: LocalEndpointTakeoverFilesystem,
    candidate: EndpointTakeoverCandidate,
) -> PreparedEndpointTakeover:
    return filesystem.prepare_controlled_takeover(
        candidate,
        intent_id=TAKEOVER_INTENT_ID,
        resulting_endpoint_revision_id=RESULTING_REVISION_ID,
        owner_installation_id=LOCAL_OWNER,
        created_utc="2026-08-01T10:01:00Z",
    )


def _write_prepared_control_records(
    control: Path,
    prepared: PreparedEndpointTakeover,
) -> None:
    for relative, payload in (
        (prepared.takeover_record_path, prepared.takeover_payload_json),
        (prepared.ownership_record_path, prepared.ownership_payload_json),
    ):
        path = control / relative
        path.write_bytes(f"{payload}\n".encode())


def _namespace(owner_installation_id: str) -> str:
    return owner_installation_id.replace("-", "")[:12]


class _TokenStore:
    def allocate_next_fencing_token(
        self,
        *,
        resource_key: str,
        ownership_epoch: int,
    ) -> int:
        del resource_key, ownership_epoch
        return 1
