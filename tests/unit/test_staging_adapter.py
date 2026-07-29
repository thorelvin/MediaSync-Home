from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.staging import (
    LocalFileStagingError,
    LocalFileStagingTransferAdapter,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
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

    assert json.loads(evidence.fingerprint_json) == _fingerprint(b"old-image")


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
