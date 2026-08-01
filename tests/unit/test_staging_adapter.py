from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import mediasync_home.adapters.staging as staging_module
from mediasync_home.adapters.staging import (
    LocalFileStagingError,
    LocalFileStagingTransferAdapter,
)
from mediasync_home.adapters.file_identity import stable_file_identity_hash
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseInspection
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.directory_artifacts import DIRECTORY_MARKER_NAME
from mediasync_home.application.source_preconditions import SourceFilePrecondition
from mediasync_home.application.run_staging import RunTargetEndpointWaitRequired
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


def test_local_staging_binds_match_fingerprint_target_precondition(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    (target_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures" / "A.jpg").write_bytes(b"old-image")
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
    )

    evidence = adapter.validate_target_precondition(
        _permit(),
        _operation(RecoveryTargetPreconditionKind.MATCH_FINGERPRINT),
    )

    assert json.loads(evidence.fingerprint_json) == {
        **_fingerprint(b"old-image"),
        "named_streams": [],
    }


def test_local_staging_rejects_match_fingerprint_target_drift(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    (target_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures" / "A.jpg").write_bytes(b"old-image")
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
    )
    operation = replace(
        _operation(RecoveryTargetPreconditionKind.MATCH_FINGERPRINT),
        expected_target_fingerprint_json=_fingerprint_json(b"previous-image"),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.validate_target_precondition(_permit(), operation)

    assert exc_info.value.validation_code == "LOCAL_STAGING_TARGET_FINGERPRINT_MISMATCH"


def test_local_staging_rejects_match_fingerprint_missing_target(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    (target_root / "Pictures").mkdir(parents=True)
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.validate_target_precondition(
            _permit(),
            _operation(RecoveryTargetPreconditionKind.MATCH_FINGERPRINT),
        )

    assert exc_info.value.validation_code == "LOCAL_STAGING_TARGET_MATCH_REQUIRES_FILE"


def test_local_staging_rejects_reparse_target_parent(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    reparse_parent = target_root / "Pictures"
    reparse_parent.mkdir(parents=True)
    (reparse_parent / "A.jpg").write_bytes(b"old-image")
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
        reparse_guard=LocalReparseGuard(probe=_OverlayProbe(reparse_paths={reparse_parent})),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.validate_target_precondition(
            _permit(),
            _operation(RecoveryTargetPreconditionKind.MATCH_FINGERPRINT),
        )

    assert exc_info.value.validation_code == "LOCAL_STAGING_REPARSE_UNSUPPORTED"


def test_local_staging_binds_directory_empty_target_precondition(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    (target_root / "Pictures" / "A.jpg").mkdir(parents=True)
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
    )

    evidence = adapter.validate_target_precondition(
        _permit(),
        _operation(RecoveryTargetPreconditionKind.DIRECTORY_EMPTY),
    )

    assert json.loads(evidence.fingerprint_json) == {
        "entry_count": 0,
        "kind": "DIRECTORY_EMPTY",
    }


def test_local_staging_rejects_non_empty_directory_target_precondition(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target"
    (target_root / "Pictures" / "A.jpg").mkdir(parents=True)
    (target_root / "Pictures" / "A.jpg" / "child.txt").write_text("child", encoding="utf-8")
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.validate_target_precondition(
            _permit(),
            _operation(RecoveryTargetPreconditionKind.DIRECTORY_EMPTY),
        )

    assert exc_info.value.validation_code == "LOCAL_STAGING_TARGET_DIRECTORY_NOT_EMPTY"


def test_local_staging_materializes_verified_directory_marker(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    target_root.mkdir()
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_RootResolver(target_root=target_root),
        staging_root=staging_root,
    )
    operation = replace(
        _operation(RecoveryTargetPreconditionKind.ABSENT),
        operation_kind=RecoveryOperationKind.CREATE_DIRECTORY,
        final_relative_path="Pictures",
    )

    source = adapter.validate_source_file(operation)
    operation = replace(operation, expected_source_fingerprint_json=source.fingerprint_json)
    stability = adapter.bind_source_stability(operation)
    assert stability.guard_evidence_hash == json.loads(source.fingerprint_json)["content_hash"]
    adapter.validate_target_precondition(_permit(), operation)
    allocation = adapter.allocate_staging_object(operation)
    operation = replace(operation, staging_object_id=allocation.staging_object_id)
    adapter.transfer_to_staging(operation)
    adapter.ensure_staging_durable(operation)
    verification = adapter.verify_staging_artifact(operation)

    payload = staging_root / "op-a.payload"
    manifest_path = staging_root / "op-a.manifest.json"
    assert payload.is_dir()
    assert tuple(path.name for path in payload.iterdir()) == (DIRECTORY_MARKER_NAME,)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["object_role"] == "STAGING"
    assert manifest["operation_kind"] == "CREATE_DIRECTORY"
    assert manifest["payload_name"] == "op-a.payload"
    assert manifest["final_relative_path"] == "Pictures"
    assert verification.fingerprint_json == source.fingerprint_json


def test_local_staging_copies_only_plan_bound_source_identity(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_SourceAndTargetRootResolver(
            source_root=source_root,
            target_root=target_root,
        ),
        staging_root=staging_root,
    )
    operation = _source_operation(source_file)

    validation = adapter.validate_source_file(operation)
    operation = replace(
        operation,
        expected_source_fingerprint_json=validation.fingerprint_json,
    )
    stability = adapter.bind_source_stability(operation)
    allocation = adapter.allocate_staging_object(operation)
    operation = replace(operation, staging_object_id=allocation.staging_object_id)
    adapter.transfer_to_staging(operation)
    verification = adapter.verify_staging_artifact(operation)

    assert stability.guard_kind == "PLAN_IDENTITY_AND_OPEN_READ_FSTAT_V1"
    assert stability.guard_evidence_hash == stable_file_identity_hash(source_file.stat())
    assert (staging_root / f"{allocation.staging_object_id}.payload").read_bytes() == b"image-bytes"
    manifest_path = staging_root / f"{allocation.staging_object_id}.manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    manifest_hash = manifest.pop("manifest_hash")
    canonical_without_hash = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    assert manifest_hash == hashlib.sha256(
        canonical_without_hash.encode("utf-8")
    ).hexdigest()
    assert manifest["object_role"] == "STAGING"
    assert manifest["staging_object_id"] == allocation.staging_object_id
    assert manifest["source_relative_path"] == "Pictures/A.jpg"
    assert manifest["final_relative_path"] == "Pictures/A.jpg"
    assert manifest["payload_name"] == f"{allocation.staging_object_id}.payload"
    assert str(source_file) not in manifest_text
    assert verification.fingerprint_json == validation.fingerprint_json


def test_local_staging_rejects_conflicting_immutable_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_SourceAndTargetRootResolver(
            source_root=source_root,
            target_root=target_root,
        ),
        staging_root=staging_root,
    )
    operation = _source_operation(source_file)
    validation = adapter.validate_source_file(operation)
    operation = replace(
        operation,
        expected_source_fingerprint_json=validation.fingerprint_json,
        staging_object_id=operation.operation_id,
    )
    adapter.transfer_to_staging(operation)
    (staging_root / "op-a.manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.ensure_staging_durable(operation)

    assert exc_info.value.validation_code == "LOCAL_STAGING_MANIFEST_CONFLICT"


def test_local_staging_rejects_source_changed_after_sealed_analysis(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    operation = _source_operation(source_file)
    original_mtime_ns = source_file.stat().st_mtime_ns
    source_file.write_bytes(b"other-bytes")
    os.utime(
        source_file,
        ns=(source_file.stat().st_atime_ns, original_mtime_ns + 1_000_000_000),
    )
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_SourceAndTargetRootResolver(
            source_root=source_root,
            target_root=target_root,
        )
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.validate_source_file(operation)

    assert exc_info.value.validation_code == "LOCAL_STAGING_SOURCE_IDENTITY_CHANGED"


def test_local_staging_network_loss_discards_partial_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_SourceAndTargetRootResolver(
            source_root=source_root,
            target_root=target_root,
        ),
        staging_root=staging_root,
    )
    operation = _source_operation(source_file)
    validation = adapter.validate_source_file(operation)
    allocation = adapter.allocate_staging_object(operation)
    operation = replace(
        operation,
        expected_source_fingerprint_json=validation.fingerprint_json,
        staging_object_id=allocation.staging_object_id,
    )

    def interrupt_copy(**kwargs: object) -> dict[str, object]:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        destination.write_bytes(b"partial")
        error = OSError("network name deleted")
        error.winerror = 64  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(staging_module, "_copy_file_with_hash", interrupt_copy)

    with pytest.raises(RunTargetEndpointWaitRequired) as exc_info:
        adapter.transfer_to_staging(operation)

    assert exc_info.value.reason_code == "NETWORK_INTERRUPTED"
    assert not (staging_root / "op-a.payload").exists()
    assert tuple(staging_root.glob("*.tmp")) == ()


def test_local_staging_permission_failure_remains_a_file_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = LocalFileStagingTransferAdapter(
        root_resolver=_SourceAndTargetRootResolver(
            source_root=source_root,
            target_root=target_root,
        ),
        staging_root=staging_root,
    )
    operation = _source_operation(source_file)
    validation = adapter.validate_source_file(operation)
    allocation = adapter.allocate_staging_object(operation)
    operation = replace(
        operation,
        expected_source_fingerprint_json=validation.fingerprint_json,
        staging_object_id=allocation.staging_object_id,
    )

    def reject_copy(**kwargs: object) -> dict[str, object]:
        raise PermissionError(13, "permission denied")

    monkeypatch.setattr(staging_module, "_copy_file_with_hash", reject_copy)

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(operation)

    assert exc_info.value.validation_code == "LOCAL_STAGING_TRANSFER_FAILED"


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


class _SourceAndTargetRootResolver(_RootResolver):
    def __init__(self, *, source_root: Path, target_root: Path) -> None:
        super().__init__(target_root=target_root)
        self._source_root = source_root

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        if (
            resource_key == "endpoint:source-a"
            and endpoint_id == "source-a"
            and endpoint_revision_id == "source-rev-a"
        ):
            return self._source_root
        return super().resolve_endpoint_root(
            resource_key=resource_key,
            endpoint_id=endpoint_id,
            endpoint_revision_id=endpoint_revision_id,
        )


class _OverlayProbe:
    def __init__(self, *, reparse_paths: set[Path]) -> None:
        self._reparse_paths = {path.resolve(strict=False) for path in reparse_paths}

    def inspect_path(self, path: Path) -> ReparseInspection:
        resolved = path.resolve(strict=False)
        return ReparseInspection(
            path=path,
            exists=path.exists() or path.is_symlink(),
            is_reparse_point=resolved in self._reparse_paths,
        )


def _operation(target_precondition_kind: RecoveryTargetPreconditionKind) -> RecoveryOperation:
    return planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="op-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Pictures/A.jpg",
        target_precondition_kind=target_precondition_kind,
    )


def _source_operation(source_file: Path) -> RecoveryOperation:
    size_bytes = source_file.stat().st_size
    return planned_recovery_operation(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        operation_id="op-a",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Pictures/A.jpg",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        planned_bytes=size_bytes,
        source_endpoint_id="source-a",
        source_endpoint_revision_id="source-rev-a",
        source_relative_path="Pictures/A.jpg",
        source_precondition_json=SourceFilePrecondition(
            snapshot_id="snapshot-a",
            snapshot_entry_id="entry-a",
            relative_path="Pictures/A.jpg",
            size_bytes=size_bytes,
            identity_fingerprint_hash=stable_file_identity_hash(source_file.stat()),
        ).to_json(),
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )


def _fingerprint_json(payload: bytes) -> str:
    return json.dumps(_fingerprint(payload), sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: bytes) -> dict[str, object]:
    return {"byte_count": len(payload), "content_hash": hashlib.sha256(payload).hexdigest()}
