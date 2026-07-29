from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    LocalEndpointLease,
    LocalEndpointLeaseAuthority,
    MutationPermitIssueError,
)
from mediasync_home.adapters.final_commit import (
    FinalCommitAdapterError,
    LabNoOverwriteFinalCommitAdapter,
    LocalResolvingFinalCommitAdapter,
    LocalVersionedReplaceFinalCommitAdapter,
)
from mediasync_home.application.ports import RelativePath, VerifiedStagingArtifact
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.runs import EndpointLeaseRequest


def test_lab_final_commit_inserts_verified_staging_payload_without_overwrite(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="object-a", relative_path="Photos/image.jpg", payload=b"image")

    receipt = fixture.adapter.commit_verified_artifact(permit, artifact)

    assert receipt.operation_id == "object-a"
    assert receipt.final_relative_path == artifact.relative_path
    assert (fixture.target_root / "Photos" / "image.jpg").read_bytes() == b"image"
    assert (fixture.staging_root / "object-a.payload").read_bytes() == b"image"


def test_lab_final_commit_requires_matching_test_root_marker(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    (fixture.target_root / ".mediasync_test_root").write_text(
        json.dumps({"run_id": "other-run"}),
        encoding="utf-8",
    )
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="object-a", relative_path="Photos/image.jpg", payload=b"image")

    with pytest.raises(FinalCommitAdapterError) as exc_info:
        fixture.adapter.commit_verified_artifact(permit, artifact)

    assert exc_info.value.validation_code == "LAB_FINAL_COMMIT_TEST_ROOT_RUN_MISMATCH"
    assert not (fixture.target_root / "Photos" / "image.jpg").exists()


def test_lab_final_commit_never_overwrites_existing_target(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"existing")
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="object-a", relative_path="Photos/image.jpg", payload=b"new")

    with pytest.raises(FinalCommitAdapterError) as exc_info:
        fixture.adapter.commit_verified_artifact(permit, artifact)

    assert exc_info.value.validation_code == "LAB_FINAL_COMMIT_TARGET_EXISTS"
    assert final.read_bytes() == b"existing"


def test_lab_final_commit_revalidates_staging_hash(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    permit = fixture.lease.issue_mutation_permit()
    artifact = VerifiedStagingArtifact(
        object_id="object-a",
        relative_path=RelativePath("Photos/image.jpg"),
        content_hash="0" * 64,
    )
    (fixture.staging_root / "object-a.payload").write_bytes(b"unexpected")

    with pytest.raises(FinalCommitAdapterError) as exc_info:
        fixture.adapter.commit_verified_artifact(permit, artifact)

    assert exc_info.value.validation_code == "LAB_FINAL_COMMIT_STAGING_HASH_MISMATCH"
    assert not (fixture.target_root / "Photos" / "image.jpg").exists()


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute.txt",
        "C:/absolute.txt",
        "Photos/../secret.txt",
        "Photos//image.jpg",
        "Photos/name:stream.jpg",
        "Photos/CON.txt",
        "Photos/trailing-dot.",
        "Photos/trailing-space ",
    ],
)
def test_lab_final_commit_rejects_unsafe_relative_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    fixture = _commit_fixture(tmp_path)
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="object-a", relative_path=relative_path, payload=b"image")

    with pytest.raises(FinalCommitAdapterError) as exc_info:
        fixture.adapter.commit_verified_artifact(permit, artifact)

    assert exc_info.value.validation_code == "LAB_FINAL_COMMIT_REQUIRES_RELATIVE_PATH"


def test_lab_final_commit_rejects_lost_lease_before_touching_final_tree(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    permit = fixture.lease.issue_mutation_permit()
    fixture.handle.lose()
    artifact = fixture.stage(object_id="object-a", relative_path="Photos/image.jpg", payload=b"image")

    with pytest.raises(MutationPermitIssueError) as exc_info:
        fixture.adapter.commit_verified_artifact(permit, artifact)

    assert exc_info.value.validation_code == "MUTATION_PERMIT_LEASE_LOST"
    assert not (fixture.target_root / "Photos" / "image.jpg").exists()


def test_local_versioned_replace_preserves_old_target_then_replaces_with_verified_payload(
    tmp_path: Path,
) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"old-image")
    artifact = fixture.stage(object_id="operation-a", relative_path="Photos/image.jpg", payload=b"new-image")
    operation = _replace_operation(fixture, expected_target_payload=b"old-image")
    adapter = LocalVersionedReplaceFinalCommitAdapter(
        target_root=fixture.target_root,
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()

    preservation = adapter.preserve_old_target(permit, operation)
    receipt = adapter.commit_verified_artifact(permit, artifact)

    version_payload = fixture.target_root / ".mediasync" / "objects" / "versions" / "operation-a.payload"
    version_manifest = (
        fixture.target_root / ".mediasync" / "objects" / "versions" / "operation-a.manifest.json"
    )
    assert preservation.version_object_id == "operation-a"
    assert preservation.final_relative_path == RelativePath("Photos/image.jpg")
    assert receipt.final_relative_path == artifact.relative_path
    assert final.read_bytes() == b"new-image"
    assert version_payload.read_bytes() == b"old-image"
    manifest = json.loads(version_manifest.read_text(encoding="utf-8"))
    assert manifest["object_role"] == "OLD_TARGET_VERSION"
    assert manifest["operation_id"] == "operation-a"
    assert manifest["fingerprint"] == {
        "byte_count": len(b"old-image"),
        "content_hash": _sha256(b"old-image"),
    }


def test_local_versioned_replace_rejects_target_drift_after_old_target_preserved(
    tmp_path: Path,
) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"old-image")
    artifact = fixture.stage(object_id="operation-a", relative_path="Photos/image.jpg", payload=b"new-image")
    operation = _replace_operation(fixture, expected_target_payload=b"old-image")
    adapter = LocalVersionedReplaceFinalCommitAdapter(
        target_root=fixture.target_root,
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    adapter.preserve_old_target(permit, operation)
    final.write_bytes(b"edited-after-preserve")

    with pytest.raises(FinalCommitAdapterError) as exc_info:
        adapter.commit_verified_artifact(permit, artifact)

    assert exc_info.value.validation_code == "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE"
    assert final.read_bytes() == b"edited-after-preserve"
    assert (
        fixture.target_root / ".mediasync" / "objects" / "versions" / "operation-a.payload"
    ).read_bytes() == b"old-image"


def test_local_versioned_replace_completes_when_final_missing_after_old_target_preserved(
    tmp_path: Path,
) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"old-image")
    artifact = fixture.stage(object_id="operation-a", relative_path="Photos/image.jpg", payload=b"new-image")
    operation = _replace_operation(fixture, expected_target_payload=b"old-image")
    adapter = LocalVersionedReplaceFinalCommitAdapter(
        target_root=fixture.target_root,
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    adapter.preserve_old_target(permit, operation)
    final.unlink()

    receipt = adapter.commit_verified_artifact(permit, artifact)

    assert receipt.final_relative_path == artifact.relative_path
    assert final.read_bytes() == b"new-image"
    assert (
        fixture.target_root / ".mediasync" / "objects" / "versions" / "operation-a.payload"
    ).read_bytes() == b"old-image"


def test_local_versioned_replace_restores_old_target_when_final_missing(
    tmp_path: Path,
) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"old-image")
    operation = _replace_operation(fixture, expected_target_payload=b"old-image")
    adapter = LocalVersionedReplaceFinalCommitAdapter(
        target_root=fixture.target_root,
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    preservation = adapter.preserve_old_target(permit, operation)
    preserved_operation = replace(
        operation,
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        version_object_id=preservation.version_object_id,
    )
    final.unlink()

    receipt = adapter.restore_old_target(permit, preserved_operation)

    assert receipt.operation_id == "operation-a"
    assert receipt.final_relative_path == RelativePath("Photos/image.jpg")
    assert final.read_bytes() == b"old-image"


def test_local_versioned_replace_restore_never_overwrites_changed_final(
    tmp_path: Path,
) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"old-image")
    operation = _replace_operation(fixture, expected_target_payload=b"old-image")
    adapter = LocalVersionedReplaceFinalCommitAdapter(
        target_root=fixture.target_root,
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    preservation = adapter.preserve_old_target(permit, operation)
    preserved_operation = replace(
        operation,
        phase=RecoveryOperationPhase.OLD_TARGET_PRESERVED,
        version_object_id=preservation.version_object_id,
    )
    final.write_bytes(b"edited-after-preserve")

    with pytest.raises(FinalCommitAdapterError) as exc_info:
        adapter.restore_old_target(permit, preserved_operation)

    assert exc_info.value.validation_code == "LOCAL_REPLACE_OLD_TARGET_RESTORE_TARGET_EXISTS"
    assert final.read_bytes() == b"edited-after-preserve"


def test_local_resolving_final_commit_inserts_without_lab_marker(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    (fixture.target_root / ".mediasync_test_root").unlink()
    adapter = LocalResolvingFinalCommitAdapter(
        root_resolver=_RootResolver(target_root=fixture.target_root),
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="operation-a", relative_path="Photos/image.jpg", payload=b"image")

    receipt = adapter.commit_verified_artifact(permit, artifact)

    assert receipt.operation_id == "operation-a"
    assert receipt.final_relative_path == artifact.relative_path
    assert (fixture.target_root / "Photos" / "image.jpg").read_bytes() == b"image"


def test_local_resolving_final_commit_preserves_then_replaces(tmp_path: Path) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.write_bytes(b"old-image")
    adapter = LocalResolvingFinalCommitAdapter(
        root_resolver=_RootResolver(target_root=fixture.target_root),
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="operation-a", relative_path="Photos/image.jpg", payload=b"new-image")
    operation = _replace_operation(fixture, expected_target_payload=b"old-image")

    preservation = adapter.preserve_old_target(permit, operation)
    receipt = adapter.commit_verified_artifact(permit, artifact)

    assert preservation.version_object_id == "operation-a"
    assert receipt.final_relative_path == artifact.relative_path
    assert final.read_bytes() == b"new-image"
    assert (
        fixture.target_root / ".mediasync" / "objects" / "versions" / "operation-a.payload"
    ).read_bytes() == b"old-image"


def test_local_resolving_final_commit_quarantines_empty_directory_then_inserts_file(
    tmp_path: Path,
) -> None:
    fixture = _commit_fixture(tmp_path)
    final = fixture.target_root / "Photos" / "image.jpg"
    final.mkdir()
    adapter = LocalResolvingFinalCommitAdapter(
        root_resolver=_RootResolver(target_root=fixture.target_root),
        staging_root=fixture.staging_root,
        permit_validator=fixture.lease,
    )
    permit = fixture.lease.issue_mutation_permit()
    artifact = fixture.stage(object_id="operation-a", relative_path="Photos/image.jpg", payload=b"image")
    operation = _directory_empty_operation(fixture)

    preservation = adapter.preserve_old_target(permit, operation)
    receipt = adapter.commit_verified_artifact(permit, artifact)

    quarantine_payload = fixture.target_root / ".mediasync" / "objects" / "quarantine" / "operation-a.payload"
    quarantine_manifest = (
        fixture.target_root
        / ".mediasync"
        / "objects"
        / "quarantine"
        / "operation-a.manifest.json"
    )
    assert preservation.version_object_id is None
    assert preservation.quarantine_object_id == "operation-a"
    assert preservation.fingerprint_json == '{"entry_count":0,"kind":"DIRECTORY_EMPTY"}'
    assert receipt.final_relative_path == artifact.relative_path
    assert final.is_file()
    assert final.read_bytes() == b"image"
    assert quarantine_payload.is_dir()
    manifest = json.loads(quarantine_manifest.read_text(encoding="utf-8"))
    assert manifest["object_role"] == "EMPTY_DIRECTORY_QUARANTINE"
    assert manifest["operation_id"] == "operation-a"
    assert manifest["fingerprint"] == {
        "entry_count": 0,
        "kind": "DIRECTORY_EMPTY",
    }


class _CommitFixture:
    def __init__(
        self,
        *,
        target_root: Path,
        staging_root: Path,
        lease: LocalEndpointLease,
        handle: "_FakeHandle",
    ) -> None:
        self.target_root = target_root
        self.staging_root = staging_root
        self.lease = lease
        self.handle = handle
        self.adapter = LabNoOverwriteFinalCommitAdapter(
            target_root=target_root,
            staging_root=staging_root,
            permit_validator=lease,
        )

    def stage(
        self,
        *,
        object_id: str,
        relative_path: str,
        payload: bytes,
    ) -> VerifiedStagingArtifact:
        (self.staging_root / f"{object_id}.payload").write_bytes(payload)
        return VerifiedStagingArtifact(
            object_id=object_id,
            relative_path=RelativePath(relative_path),
            content_hash=_sha256(payload),
        )


def _commit_fixture(tmp_path: Path) -> _CommitFixture:
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    (target_root / ".mediasync" / "locks").mkdir(parents=True)
    (target_root / "Photos").mkdir()
    staging_root.mkdir()
    (target_root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(
            {
                "endpoint_id": "target-a",
                "owner_installation_id": "owner-a",
                "ownership_epoch": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (target_root / ".mediasync_test_root").write_text(
        json.dumps({"run_id": "run-a"}),
        encoding="utf-8",
    )
    handle = _FakeHandle(target_root / ".mediasync" / "locks" / "mutation.lock")
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": target_root},
        token_store=_FakeTokenStore(42),
        lock_opener=_FakeOpener(handle),
    )
    attempt = authority.acquire_endpoint_lease(_request())
    assert attempt.lease is not None
    return _CommitFixture(
        target_root=target_root,
        staging_root=staging_root,
        lease=cast(LocalEndpointLease, attempt.lease),
        handle=handle,
    )


def _request() -> EndpointLeaseRequest:
    return EndpointLeaseRequest(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        resource_key="endpoint:target-a",
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _replace_operation(
    fixture: _CommitFixture,
    *,
    expected_target_payload: bytes,
) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id=fixture.lease.run_id,
            run_target_id=fixture.lease.run_target_id,
            operation_id="operation-a",
            target_endpoint_id=fixture.lease.endpoint_id,
            target_endpoint_revision_id=fixture.lease.endpoint_revision_id,
            endpoint_generation=1,
            owner_installation_id=fixture.lease.owner_installation_id,
            ownership_epoch=fixture.lease.ownership_epoch,
            lease_id=fixture.lease.lease_id,
            lease_resource_key=fixture.lease.resource_key,
            fencing_token=fixture.lease.fencing_token,
            final_relative_path="Photos/image.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.MATCH_FINGERPRINT,
        ),
        phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        intent_segment_id="segment-a",
        intent_ordinal=0,
        staging_object_id="operation-a",
        expected_target_fingerprint_json=json.dumps(
            {
                "byte_count": len(expected_target_payload),
                "content_hash": _sha256(expected_target_payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _directory_empty_operation(fixture: _CommitFixture) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id=fixture.lease.run_id,
            run_target_id=fixture.lease.run_target_id,
            operation_id="operation-a",
            target_endpoint_id=fixture.lease.endpoint_id,
            target_endpoint_revision_id=fixture.lease.endpoint_revision_id,
            endpoint_generation=1,
            owner_installation_id=fixture.lease.owner_installation_id,
            ownership_epoch=fixture.lease.ownership_epoch,
            lease_id=fixture.lease.lease_id,
            lease_resource_key=fixture.lease.resource_key,
            fencing_token=fixture.lease.fencing_token,
            final_relative_path="Photos/image.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.DIRECTORY_EMPTY,
        ),
        phase=RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        intent_segment_id="segment-a",
        intent_ordinal=0,
        staging_object_id="operation-a",
        expected_target_fingerprint_json=json.dumps(
            {
                "entry_count": 0,
                "kind": "DIRECTORY_EMPTY",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


class _FakeHandle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.closed = False
        self.alive = True

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return self.alive and not self.closed

    def lose(self) -> None:
        self.alive = False


class _FakeOpener:
    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle

    def acquire_exclusive_lock(self, lock_path: Path) -> _FakeHandle:
        if lock_path != self._handle.path:
            raise EndpointLeaseUnavailable(
                "ENDPOINT_LEASE_UNAVAILABLE",
                "Use the expected lock path for this fixture.",
            )
        return self._handle


class _FakeTokenStore:
    def __init__(self, token: int) -> None:
        self._token = token

    def allocate_next_fencing_token(self, *, resource_key: str, ownership_epoch: int) -> int:
        return self._token


class _RootResolver:
    def __init__(self, *, target_root: Path) -> None:
        self._target_root = target_root

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        if (
            resource_key == "endpoint:target-a"
            and endpoint_id == "target-a"
            and endpoint_revision_id == "target-rev-a"
        ):
            return self._target_root
        return None
