from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.process_supervisor import Win32JobObjectTransferSupervisor
from mediasync_home.adapters.robocopy import (
    RobocopyBatchManifestEntry,
    RobocopyStagingTransferAdapter,
    RobocopyTransferProfile,
    WindowsSystemExecutableResolver,
    build_robocopy_batch_manifest,
    build_robocopy_directory_manifest_command_plan,
    classify_robocopy_exit_code,
    write_robocopy_batch_manifest,
)
from mediasync_home.adapters.file_identity import stable_file_identity_hash
from mediasync_home.application.process_supervision import (
    ChildContainmentPolicy,
    HandleInheritancePolicy,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.source_preconditions import SourceFilePrecondition


pytestmark = pytest.mark.skipif(os.name != "nt", reason="live Robocopy evidence requires Windows")


def test_live_robocopy_adapter_runs_contained_and_publishes_manifested_payload(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    staging_root = tmp_path / "staging"
    work_root = tmp_path / "work"
    source_file = source_root / "Pictures" / "A live file.txt"
    payload = b"live robocopy payload\n"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(payload)
    sibling_directory = source_file.parent / "must-not-be-copied"
    sibling_directory.mkdir()
    (sibling_directory / "unselected.txt").write_text("excluded", encoding="utf-8")
    target_root.mkdir()
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=staging_root,
        robocopy_work_root=work_root,
        profile=RobocopyTransferProfile(timeout_seconds=15.0),
    )

    evidence = adapter.transfer_to_staging(_operation(source_file))

    assert evidence.transfer_state == "ROBOCOPY_EXIT_1_COPIED_TRANSFERRED_TO_STAGING"
    assert (staging_root / "object-live.payload").read_bytes() == payload
    manifest_path = work_root / "manifests" / "object-live.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["batch_kind"] == "DIRECTORY_MANIFEST"
    assert manifest["canonical_manifest_hash"]
    assert manifest["entries"][0]["payload_name"] == "object-live.payload"
    assert not (work_root / "inbox" / "object-live").exists()
    assert not (work_root / "quarantine").exists()
    assert (work_root / "logs" / "object-live.robocopy.log").is_file()


def test_live_robocopy_missing_source_exits_fatal_under_transfer_supervisor(
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "missing-source"
    staging_inbox = tmp_path / "inbox" / "batch-live"
    work_root = tmp_path / "work"
    log_path = tmp_path / "logs" / "batch-live.robocopy.log"
    manifest_path = tmp_path / "manifests" / "batch-live.manifest.json"
    staging_inbox.mkdir(parents=True)
    work_root.mkdir()
    log_path.parent.mkdir()
    manifest = build_robocopy_batch_manifest(
        batch_id="batch-live",
        source_parent=missing_source,
        staging_inbox=staging_inbox,
        log_path=log_path,
        entries=(
            RobocopyBatchManifestEntry(
                operation_id="op-live",
                staging_object_id="object-live",
                source_file_name="missing.txt",
                source_relative_path="missing.txt",
                final_relative_path="missing.txt",
                payload_path=tmp_path / "staging" / "object-live.payload",
                expected_byte_count=1,
                expected_content_hash=hashlib.sha256(b"x").hexdigest(),
            ),
        ),
        profile=RobocopyTransferProfile(timeout_seconds=15.0),
    )
    write_robocopy_batch_manifest(manifest=manifest, manifest_path=manifest_path)
    plan = build_robocopy_directory_manifest_command_plan(
        executable=WindowsSystemExecutableResolver().resolve("Robocopy.exe"),
        manifest=manifest,
        manifest_path=manifest_path,
        working_directory=work_root,
        working_directory_root=work_root,
        profile=RobocopyTransferProfile(timeout_seconds=15.0),
    )

    process = Win32JobObjectTransferSupervisor().start(plan.launch_plan)
    try:
        exit_code = process.wait(timeout_seconds=15.0)
    finally:
        process.close()

    assert exit_code is not None
    assert classify_robocopy_exit_code(exit_code) == "FATAL"
    assert plan.launch_plan.containment_policy is (
        ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT
    )
    assert plan.launch_plan.handle_inheritance_policy is (
        HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST
    )
    assert plan.launch_plan.inherited_handles == ()
    assert not (tmp_path / "staging" / "object-live.payload").exists()
    assert log_path.is_file()


class _RootResolver:
    def __init__(self, *, source_root: Path, target_root: Path) -> None:
        self._source_root = source_root
        self._target_root = target_root

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
        if (
            resource_key == "endpoint:target-a"
            and endpoint_id == "target-a"
            and endpoint_revision_id == "target-rev-a"
        ):
            return self._target_root
        return None


def _operation(source_file: Path) -> RecoveryOperation:
    source_payload = source_file.read_bytes()
    operation = planned_recovery_operation(
        run_id="run-live",
        run_target_id="run-live-target-0000",
        operation_id="op-live",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        lease_resource_key="endpoint:target-a",
        fencing_token=1,
        final_relative_path="Pictures/A live file.txt",
        target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
        source_endpoint_id="source-a",
        source_endpoint_revision_id="source-rev-a",
        source_relative_path="Pictures/A live file.txt",
        planned_bytes=len(source_payload),
        source_precondition_json=SourceFilePrecondition(
            snapshot_id="source-snapshot-live",
            snapshot_entry_id="source-entry-live",
            relative_path="Pictures/A live file.txt",
            size_bytes=len(source_payload),
            identity_fingerprint_hash=stable_file_identity_hash(source_file.stat()),
        ).to_json(),
    )
    return replace(
        operation,
        expected_source_fingerprint_json=json.dumps(
            {
                "byte_count": len(source_payload),
                "content_hash": hashlib.sha256(source_payload).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        staging_object_id="object-live",
    )
