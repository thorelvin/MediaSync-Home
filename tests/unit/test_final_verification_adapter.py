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
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
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
