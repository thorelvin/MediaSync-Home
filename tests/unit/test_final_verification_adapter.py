from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.final_verification import (
    FinalArtifactVerificationError,
    LocalFinalArtifactVerificationAdapter,
)
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseInspection
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.directory_artifacts import (
    DIRECTORY_MARKER_NAME,
    directory_artifact_fingerprint,
    directory_marker_bytes,
)


def test_local_final_artifact_verifier_returns_matching_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "target"
    (root / "Pictures").mkdir(parents=True)
    payload = b"image"
    (root / "Pictures" / "A.jpg").write_bytes(payload)
    verifier = LocalFinalArtifactVerificationAdapter(root_resolver=_RootResolver(root))

    evidence = verifier.verify_final_artifact(_operation(payload=payload))

    assert evidence.fingerprint_json == json.dumps(
        {"byte_count": len(payload), "content_hash": _sha256(payload)},
        sort_keys=True,
        separators=(",", ":"),
    )


def test_local_final_artifact_verifier_rejects_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "target"
    (root / "Pictures").mkdir(parents=True)
    (root / "Pictures" / "A.jpg").write_bytes(b"changed")
    verifier = LocalFinalArtifactVerificationAdapter(root_resolver=_RootResolver(root))

    with pytest.raises(FinalArtifactVerificationError) as exc_info:
        verifier.verify_final_artifact(_operation(payload=b"image"))

    assert exc_info.value.validation_code == "FINAL_ARTIFACT_VERIFY_FINGERPRINT_MISMATCH"


def test_local_final_artifact_verifier_rejects_unsafe_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    verifier = LocalFinalArtifactVerificationAdapter(root_resolver=_RootResolver(root))

    with pytest.raises(FinalArtifactVerificationError) as exc_info:
        verifier.verify_final_artifact(
            replace(_operation(payload=b"image"), final_relative_path="../A.jpg")
        )

    assert exc_info.value.validation_code == "FINAL_ARTIFACT_VERIFY_REQUIRES_RELATIVE_PATH"


def test_local_final_artifact_verifier_rejects_reparse_parent(tmp_path: Path) -> None:
    root = tmp_path / "target"
    reparse_parent = root / "Pictures"
    reparse_parent.mkdir(parents=True)
    (reparse_parent / "A.jpg").write_bytes(b"image")
    verifier = LocalFinalArtifactVerificationAdapter(
        root_resolver=_RootResolver(root),
        reparse_guard=LocalReparseGuard(probe=_OverlayProbe(reparse_paths={reparse_parent})),
    )

    with pytest.raises(FinalArtifactVerificationError) as exc_info:
        verifier.verify_final_artifact(_operation(payload=b"image"))

    assert exc_info.value.validation_code == "FINAL_ARTIFACT_VERIFY_REPARSE_UNSUPPORTED"


def test_local_final_artifact_verifier_accepts_owned_directory_marker(tmp_path: Path) -> None:
    root = tmp_path / "target"
    final_path = root / "Pictures"
    final_path.mkdir(parents=True)
    operation = replace(
        _operation(payload=b""),
        operation_kind=RecoveryOperationKind.CREATE_DIRECTORY,
        final_relative_path="Pictures",
    )
    (final_path / DIRECTORY_MARKER_NAME).write_bytes(
        directory_marker_bytes(
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_id=operation.operation_id,
            final_relative_path=operation.final_relative_path,
        )
    )
    operation = replace(
        operation,
        expected_final_fingerprint_json=json.dumps(
            directory_artifact_fingerprint(
                run_id=operation.run_id,
                run_target_id=operation.run_target_id,
                operation_id=operation.operation_id,
                final_relative_path=operation.final_relative_path,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )

    evidence = LocalFinalArtifactVerificationAdapter(
        root_resolver=_RootResolver(root)
    ).verify_final_artifact(operation)

    assert json.loads(evidence.fingerprint_json)["byte_count"] == 0


class _RootResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        assert resource_key == "endpoint:target-a"
        assert endpoint_id == "target-a"
        assert endpoint_revision_id == "target-rev-a"
        return self._root


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


def _operation(*, payload: bytes) -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
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
            fencing_token=42,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        ),
        phase=RecoveryOperationPhase.FILESYSTEM_APPLIED,
        expected_final_fingerprint_json=json.dumps(
            {"byte_count": len(payload), "content_hash": _sha256(payload)},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
