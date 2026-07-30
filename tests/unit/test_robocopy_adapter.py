from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.robocopy import (
    ROBOCOPY_CONSERVATIVE_COMMAND_LINE_LIMIT,
    RobocopyBatchManifestEntry,
    RobocopyConfigurationError,
    RobocopyStagingTransferAdapter,
    RobocopyTransferProfile,
    ResolvedSystemExecutable,
    WindowsSystemExecutableResolver,
    build_robocopy_batch_manifest,
    build_robocopy_directory_manifest_command_plan,
    build_robocopy_result,
    publish_robocopy_batch_inbox,
    build_robocopy_single_file_command_plan,
    classify_robocopy_exit_code,
    decode_robocopy_exit_code,
    validate_robocopy_command_line,
    write_robocopy_batch_manifest,
)
from mediasync_home.adapters.staging import LocalFileStagingError
from mediasync_home.adapters.windows_argv import build_windows_command_line
from mediasync_home.application.process_supervision import (
    ChildContainmentPolicy,
    HandleInheritancePolicy,
    ProcessLaunchViolation,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_single_file_command_plan_uses_contained_transfer_policy(
    tmp_path: Path,
) -> None:
    resolved = _resolved_executable(tmp_path)
    source_file = tmp_path / "source root" / "Pictures" / "A file.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"payload")
    inbox = tmp_path / "control" / "inbox" / "object-a"
    work_root = tmp_path / "control" / "robocopy"
    log_path = tmp_path / "control" / "logs" / "object-a.log"

    plan = build_robocopy_single_file_command_plan(
        executable=resolved,
        source_file=source_file,
        staging_inbox=inbox,
        log_path=log_path,
        working_directory=work_root,
        working_directory_root=work_root,
        profile=RobocopyTransferProfile(switches=("/E", "/R:1", "/W:1")),
    )

    assert plan.parsed_argv == plan.argv
    assert plan.launch_plan.executable == resolved.executable_path
    assert plan.launch_plan.arguments[:3] == (
        str(source_file.parent),
        str(inbox),
        source_file.name,
    )
    assert plan.launch_plan.containment_policy is (
        ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT
    )
    assert plan.launch_plan.handle_inheritance_policy is (
        HandleInheritancePolicy.EXPLICIT_EMPTY_HANDLE_LIST
    )
    assert plan.launch_plan.inherited_handles == ()
    assert set(dict(plan.launch_plan.environment)) == {"SystemRoot", "TEMP", "TMP"}
    assert len(plan.command_line_sha256) == 64


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_command_line_rejects_forbidden_switch_after_serialization(
    tmp_path: Path,
) -> None:
    executable = _resolved_executable(tmp_path).executable_path
    command_line = build_windows_command_line(
        (
            str(executable),
            r"C:\source",
            r"C:\staging",
            "/PURGE",
        )
    )

    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_FORBIDDEN_SWITCH"):
        validate_robocopy_command_line(command_line, executable_path=executable)


def test_robocopy_profile_rejects_forbidden_typed_switch(tmp_path: Path) -> None:
    source_file = tmp_path / "source" / "a.txt"
    source_file.parent.mkdir()
    source_file.write_text("payload", encoding="utf-8")

    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_FORBIDDEN_SWITCH"):
        build_robocopy_single_file_command_plan(
            executable=_resolved_executable(tmp_path),
            source_file=source_file,
            staging_inbox=tmp_path / "staging",
            log_path=tmp_path / "logs" / "batch.log",
            working_directory=tmp_path / "work",
            working_directory_root=tmp_path / "work",
            profile=RobocopyTransferProfile(switches=("/E", "/MOVE")),
        )


@pytest.mark.parametrize("success_max_exit_code", [-1, 8, 16])
def test_robocopy_profile_rejects_invalid_success_threshold(
    tmp_path: Path,
    success_max_exit_code: int,
) -> None:
    with pytest.raises(
        RobocopyConfigurationError,
        match="ROBOCOPY_PROFILE_SUCCESS_MAX_EXIT_CODE_INVALID",
    ):
        build_robocopy_batch_manifest(
            batch_id="batch-a",
            source_parent=tmp_path / "source",
            staging_inbox=tmp_path / "inbox",
            log_path=tmp_path / "log.txt",
            entries=(
                _manifest_entry(
                    operation_id="op-a",
                    staging_object_id="object-a",
                    source_file_name="A.jpg",
                    payload_path=tmp_path / "staging" / "object-a.payload",
                    payload=b"image",
                ),
            ),
            profile=RobocopyTransferProfile(success_max_exit_code=success_max_exit_code),
        )


def test_robocopy_profile_rejects_non_positive_timeout(tmp_path: Path) -> None:
    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_PROFILE_TIMEOUT_INVALID"):
        build_robocopy_batch_manifest(
            batch_id="batch-a",
            source_parent=tmp_path / "source",
            staging_inbox=tmp_path / "inbox",
            log_path=tmp_path / "log.txt",
            entries=(
                _manifest_entry(
                    operation_id="op-a",
                    staging_object_id="object-a",
                    source_file_name="A.jpg",
                    payload_path=tmp_path / "staging" / "object-a.payload",
                    payload=b"image",
                ),
            ),
            profile=RobocopyTransferProfile(timeout_seconds=0),
        )


@pytest.mark.parametrize(
    (
        "exit_code",
        "category",
        "copied",
        "extras_reported",
        "mismatches_reported",
        "failed",
    ),
    [
        (-1, "INVALID", False, False, False, True),
        (0, "NON_FATAL", False, False, False, False),
        (1, "NON_FATAL", True, False, False, False),
        (2, "NON_FATAL", False, True, False, False),
        (3, "NON_FATAL", True, True, False, False),
        (4, "NON_FATAL", False, False, True, False),
        (7, "NON_FATAL", True, True, True, False),
        (8, "FATAL", False, False, False, True),
        (15, "FATAL", True, True, True, True),
    ],
)
def test_robocopy_exit_code_decoder_records_result_flags(
    exit_code: int,
    category: str,
    copied: bool,
    extras_reported: bool,
    mismatches_reported: bool,
    failed: bool,
) -> None:
    classification = decode_robocopy_exit_code(exit_code)

    assert classify_robocopy_exit_code(exit_code) == category
    assert classification.category == category
    assert classification.copied is copied
    assert classification.extras_reported is extras_reported
    assert classification.mismatches_reported is mismatches_reported
    assert classification.failed is failed


def test_robocopy_batch_manifest_is_canonical_and_immutable(tmp_path: Path) -> None:
    source_parent = tmp_path / "source" / "Pictures"
    inbox = tmp_path / "work" / "inbox" / "batch-a"
    log_path = tmp_path / "work" / "logs" / "batch-a.log"
    manifest_path = tmp_path / "work" / "manifests" / "batch-a.manifest.json"
    entry = _manifest_entry(
        operation_id="op-a",
        staging_object_id="object-a",
        source_file_name="A.jpg",
        payload_path=tmp_path / "staging" / "object-a.payload",
        payload=b"image",
    )

    manifest = build_robocopy_batch_manifest(
        batch_id="batch-a",
        source_parent=source_parent,
        staging_inbox=inbox,
        log_path=log_path,
        entries=(entry,),
        profile=RobocopyTransferProfile(switches=("/E", "/R:1", "/W:1")),
    )
    write_robocopy_batch_manifest(manifest=manifest, manifest_path=manifest_path)
    write_robocopy_batch_manifest(manifest=manifest, manifest_path=manifest_path)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["canonical_manifest_hash"] == manifest.manifest_hash
    assert payload["entries"][0]["operation_id"] == "op-a"
    assert payload["entries"][0]["payload_name"] == "object-a.payload"
    assert payload["profile_hash"] == manifest.profile_hash
    manifest_path.write_text("changed", encoding="utf-8")
    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_MANIFEST_CONFLICT"):
        write_robocopy_batch_manifest(manifest=manifest, manifest_path=manifest_path)


def test_robocopy_batch_manifest_rejects_duplicate_or_unsafe_file_names(
    tmp_path: Path,
) -> None:
    source_parent = tmp_path / "source"
    inbox = tmp_path / "inbox"
    log_path = tmp_path / "log.txt"
    entry = _manifest_entry(
        operation_id="op-a",
        staging_object_id="object-a",
        source_file_name="A.jpg",
        payload_path=tmp_path / "object-a.payload",
        payload=b"image",
    )

    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_MANIFEST_DUPLICATE_SOURCE_NAME"):
        build_robocopy_batch_manifest(
            batch_id="batch-a",
            source_parent=source_parent,
            staging_inbox=inbox,
            log_path=log_path,
            entries=(
                entry,
                _manifest_entry(
                    operation_id="op-b",
                    staging_object_id="object-b",
                    source_file_name="A.jpg",
                    payload_path=tmp_path / "object-b.payload",
                    payload=b"other",
                ),
            ),
        )
    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_MANIFEST_SOURCE_NAME_UNSAFE"):
        build_robocopy_batch_manifest(
            batch_id="batch-a",
            source_parent=source_parent,
            staging_inbox=inbox,
            log_path=log_path,
            entries=(
                _manifest_entry(
                    operation_id="op-c",
                    staging_object_id="object-c",
                    source_file_name="*.jpg",
                    payload_path=tmp_path / "object-c.payload",
                    payload=b"other",
                ),
            ),
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_directory_manifest_command_plan_uses_exact_file_list(
    tmp_path: Path,
) -> None:
    source_parent = tmp_path / "source root" / "Pictures"
    inbox = tmp_path / "work" / "inbox" / "batch-a"
    log_path = tmp_path / "work" / "logs" / "batch-a.log"
    manifest_path = tmp_path / "work" / "manifests" / "batch-a.manifest.json"
    manifest = build_robocopy_batch_manifest(
        batch_id="batch-a",
        source_parent=source_parent,
        staging_inbox=inbox,
        log_path=log_path,
        entries=(
            _manifest_entry(
                operation_id="op-a",
                staging_object_id="object-a",
                source_file_name="A file.jpg",
                payload_path=tmp_path / "staging" / "object-a.payload",
                payload=b"a",
            ),
            _manifest_entry(
                operation_id="op-b",
                staging_object_id="object-b",
                source_file_name="B.jpg",
                payload_path=tmp_path / "staging" / "object-b.payload",
                payload=b"b",
            ),
        ),
        profile=RobocopyTransferProfile(switches=("/E", "/R:1", "/W:1")),
    )

    plan = build_robocopy_directory_manifest_command_plan(
        executable=_resolved_executable(tmp_path),
        manifest=manifest,
        manifest_path=manifest_path,
        working_directory=tmp_path / "work",
        working_directory_root=tmp_path / "work",
        profile=RobocopyTransferProfile(switches=("/E", "/R:1", "/W:1")),
    )

    assert plan.file_names == ("A file.jpg", "B.jpg")
    assert plan.batch_manifest_hash == manifest.manifest_hash
    assert plan.manifest_path == manifest_path
    assert plan.launch_plan.arguments[:4] == (
        str(source_parent),
        str(inbox),
        "A file.jpg",
        "B.jpg",
    )
    assert len(build_windows_command_line(plan.argv)) <= ROBOCOPY_CONSERVATIVE_COMMAND_LINE_LIMIT
    result = build_robocopy_result(command_plan=plan, exit_code=7)
    assert result.category == "NON_FATAL"
    assert result.copied is True
    assert result.extras_reported is True
    assert result.mismatches_reported is True
    assert result.failed is False
    assert result.arguments_hash == plan.command_line_sha256
    assert result.manifest_hash == manifest.manifest_hash
    assert len(result.environment_hash) == 64
    assert result.executable_path == plan.executable.executable_path


def test_robocopy_directory_manifest_command_plan_rejects_conservative_limit(
    tmp_path: Path,
) -> None:
    source_parent = tmp_path / "source"
    entries = tuple(
        _manifest_entry(
            operation_id=f"op-{index}",
            staging_object_id=f"object-{index}",
            source_file_name=f"{index:04d}-{'x' * 40}.jpg",
            payload_path=tmp_path / "staging" / f"object-{index}.payload",
            payload=b"x",
        )
        for index in range(520)
    )
    manifest = build_robocopy_batch_manifest(
        batch_id="batch-a",
        source_parent=source_parent,
        staging_inbox=tmp_path / "inbox",
        log_path=tmp_path / "log.txt",
        entries=entries,
    )

    with pytest.raises(RobocopyConfigurationError, match="ROBOCOPY_COMMAND_LINE_TOO_LONG"):
        build_robocopy_directory_manifest_command_plan(
            executable=_resolved_executable(tmp_path),
            manifest=manifest,
            manifest_path=tmp_path / "manifest.json",
            working_directory=tmp_path / "work",
            working_directory_root=tmp_path / "work",
        )


def test_publish_robocopy_batch_inbox_requires_exact_manifest_contents(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "A.jpg").write_bytes(b"image")
    (inbox / "extra.jpg").write_bytes(b"extra")
    manifest = build_robocopy_batch_manifest(
        batch_id="batch-a",
        source_parent=tmp_path / "source",
        staging_inbox=inbox,
        log_path=tmp_path / "log.txt",
        entries=(
            _manifest_entry(
                operation_id="op-a",
                staging_object_id="object-a",
                source_file_name="A.jpg",
                payload_path=tmp_path / "staging" / "object-a.payload",
                payload=b"image",
            ),
        ),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        publish_robocopy_batch_inbox(manifest)

    assert exc_info.value.validation_code == "STAGING_MANIFEST_MISMATCH"
    assert not (tmp_path / "staging" / "object-a.payload").exists()


def test_publish_robocopy_batch_inbox_publishes_all_manifest_entries(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "A.jpg").write_bytes(b"image-a")
    (inbox / "B.jpg").write_bytes(b"image-b")
    manifest = build_robocopy_batch_manifest(
        batch_id="batch-a",
        source_parent=tmp_path / "source",
        staging_inbox=inbox,
        log_path=tmp_path / "log.txt",
        entries=(
            _manifest_entry(
                operation_id="op-a",
                staging_object_id="object-a",
                source_file_name="A.jpg",
                payload_path=tmp_path / "staging" / "object-a.payload",
                payload=b"image-a",
            ),
            _manifest_entry(
                operation_id="op-b",
                staging_object_id="object-b",
                source_file_name="B.jpg",
                payload_path=tmp_path / "staging" / "object-b.payload",
                payload=b"image-b",
            ),
        ),
    )

    publish_robocopy_batch_inbox(manifest)

    assert (tmp_path / "staging" / "object-a.payload").read_bytes() == b"image-a"
    assert (tmp_path / "staging" / "object-b.payload").read_bytes() == b"image-b"
    assert not inbox.exists()


def test_windows_system_executable_resolver_rejects_escaped_final_path(tmp_path: Path) -> None:
    system_dir = tmp_path / "System32"
    system_dir.mkdir()
    (system_dir / "Robocopy.exe").write_bytes(b"exe")
    escaped = tmp_path / "Other" / "Robocopy.exe"
    escaped.parent.mkdir()
    escaped.write_bytes(b"other")

    resolver = WindowsSystemExecutableResolver(
        api=_FakeSystemExecutableApi(system_dir=system_dir, final_path=str(escaped))
    )

    with pytest.raises(
        RobocopyConfigurationError,
        match="SYSTEM_EXECUTABLE_ESCAPED_SYSTEM_DIRECTORY",
    ):
        resolver.resolve("Robocopy.exe")


def test_windows_system_executable_resolver_records_hash_and_final_path(
    tmp_path: Path,
) -> None:
    system_dir = tmp_path / "System32"
    system_dir.mkdir()
    executable = system_dir / "Robocopy.exe"
    executable.write_bytes(b"exe")

    resolved = WindowsSystemExecutableResolver(
        api=_FakeSystemExecutableApi(system_dir=system_dir, final_path=str(executable))
    ).resolve("Robocopy.exe")

    assert resolved.executable_path == executable
    assert resolved.final_path == str(executable)
    assert resolved.sha256 == hashlib.sha256(b"exe").hexdigest()
    assert resolved.file_version == "1.2.3.4"


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_starts_contained_process_and_publishes_payload(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    staging_root = tmp_path / "staging"
    work_root = tmp_path / "work"
    supervisor = _FakeRobocopySupervisor(exit_code=1)
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=staging_root,
        robocopy_work_root=work_root,
        process_supervisor=supervisor,
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
        profile=RobocopyTransferProfile(timeout_seconds=5.0),
    )

    evidence = adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert evidence.transfer_state == "ROBOCOPY_EXIT_1_COPIED_TRANSFERRED_TO_STAGING"
    assert (staging_root / "object-a.payload").read_bytes() == b"image-bytes"
    manifest_payload = json.loads(
        (work_root / "manifests" / "object-a.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_payload["batch_kind"] == "DIRECTORY_MANIFEST"
    assert manifest_payload["entries"][0]["operation_id"] == "op-a"
    assert manifest_payload["entries"][0]["payload_name"] == "object-a.payload"
    assert supervisor.launch_plans[0].containment_policy is (
        ChildContainmentPolicy.TRANSFER_CHILD_REQUIRES_SUSPENDED_JOB_OBJECT
    )
    assert "Pictures/A.jpg" not in " ".join(supervisor.launch_plans[0].arguments)
    assert supervisor.process is not None
    assert supervisor.process.closed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_reuses_existing_matching_payload(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    (staging_root / "object-a.payload").write_bytes(b"image-bytes")
    supervisor = _FakeRobocopySupervisor(exit_code=1)
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=staging_root,
        robocopy_work_root=tmp_path / "work",
        process_supervisor=supervisor,
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    evidence = adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert evidence.transfer_state == "ROBOCOPY_TRANSFERRED_EXISTING_MATCH"
    assert supervisor.launch_plans == []


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_times_out_and_terminates_child(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    supervisor = _FakeRobocopySupervisor(exit_code=None)
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=supervisor,
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
        profile=RobocopyTransferProfile(timeout_seconds=0.25),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert exc_info.value.validation_code == "ROBOCOPY_TRANSFER_TIMED_OUT"
    assert supervisor.process is not None
    assert supervisor.process.terminated_exit_code == 98
    assert supervisor.process.closed is True
    assert not (tmp_path / "work" / "inbox" / "object-a").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_clears_inbox_after_containment_failure(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    supervisor = _RejectingRobocopySupervisor()
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=supervisor,
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert exc_info.value.validation_code == "ROBOCOPY_PROCESS_CONTAINMENT_FAILED"
    assert supervisor.launch_plans
    assert not (tmp_path / "work" / "inbox" / "object-a").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_rejects_fatal_exit_code(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    supervisor = _FakeRobocopySupervisor(exit_code=8)
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=supervisor,
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert exc_info.value.validation_code == "ROBOCOPY_TRANSFER_FAILED"
    assert not (tmp_path / "work" / "inbox" / "object-a").exists()

    supervisor.exit_code = 1
    evidence = adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert evidence.transfer_state == "ROBOCOPY_EXIT_1_COPIED_TRANSFERRED_TO_STAGING"
    assert (tmp_path / "staging" / "object-a.payload").read_bytes() == b"image-bytes"


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_rejects_invalid_negative_exit_code(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=_FakeRobocopySupervisor(exit_code=-1),
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert exc_info.value.validation_code == "ROBOCOPY_TRANSFER_FAILED"
    assert not (tmp_path / "staging" / "object-a.payload").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_records_nonfatal_extra_exit_flag(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=_FakeRobocopySupervisor(exit_code=3),
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    evidence = adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert (
        evidence.transfer_state
        == "ROBOCOPY_EXIT_3_COPIED_EXTRAS_REPORTED_TRANSFERRED_TO_STAGING"
    )
    assert (tmp_path / "staging" / "object-a.payload").read_bytes() == b"image-bytes"


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_blocks_extra_inbox_content_despite_nonfatal_exit(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=_FakeRobocopySupervisor(
            exit_code=3,
            extra_inbox_files={"unexpected.tmp": b"extra"},
        ),
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert exc_info.value.validation_code == "STAGING_MANIFEST_MISMATCH"
    assert not (tmp_path / "staging" / "object-a.payload").exists()
    assert (tmp_path / "work" / "inbox" / "object-a" / "unexpected.tmp").read_bytes() == b"extra"


@pytest.mark.skipif(os.name != "nt", reason="Windows argv parsing requires Windows")
def test_robocopy_staging_transfer_rejects_changed_source_bytes(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_file = source_root / "Pictures" / "A.jpg"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"image-bytes")
    target_root.mkdir()
    adapter = RobocopyStagingTransferAdapter(
        root_resolver=_RootResolver(source_root=source_root, target_root=target_root),
        staging_root=tmp_path / "staging",
        robocopy_work_root=tmp_path / "work",
        process_supervisor=_FakeRobocopySupervisor(exit_code=1, copied_payload=b"changed"),
        executable_resolver=_FakeExecutableResolver(_resolved_executable(tmp_path)),
    )

    with pytest.raises(LocalFileStagingError) as exc_info:
        adapter.transfer_to_staging(_operation(source_file.read_bytes()))

    assert exc_info.value.validation_code == "ROBOCOPY_STAGING_SOURCE_CHANGED"
    assert not (tmp_path / "work" / "inbox" / "object-a").exists()


class _FakeSystemExecutableApi:
    def __init__(self, *, system_dir: Path, final_path: str) -> None:
        self._system_dir = system_dir
        self._final_path = final_path

    def get_system_directory(self) -> Path:
        return self._system_dir

    def get_final_path(self, path: Path) -> str:
        return self._final_path

    def get_file_version(self, path: Path) -> str:
        return "1.2.3.4"


class _FakeExecutableResolver:
    def __init__(self, resolved: ResolvedSystemExecutable) -> None:
        self._resolved = resolved

    def resolve(self, requested_name: str) -> ResolvedSystemExecutable:
        assert requested_name == "Robocopy.exe"
        return self._resolved


class _RejectingRobocopySupervisor:
    def __init__(self) -> None:
        self.launch_plans: list[object] = []

    def start(self, plan: object) -> "_FakeRobocopyProcess":
        self.launch_plans.append(plan)
        raise ProcessLaunchViolation("CHILD_PROCESS_CONTAINMENT_FAILED")


class _FakeRobocopySupervisor:
    def __init__(
        self,
        *,
        exit_code: int | None,
        copied_payload: bytes | None = None,
        extra_inbox_files: dict[str, bytes] | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.copied_payload = copied_payload
        self.extra_inbox_files = extra_inbox_files or {}
        self.launch_plans: list[object] = []
        self.process: _FakeRobocopyProcess | None = None

    def start(self, plan: object) -> "_FakeRobocopyProcess":
        assert hasattr(plan, "arguments")
        self.launch_plans.append(plan)
        arguments = getattr(plan, "arguments")
        source_parent = Path(arguments[0])
        staging_inbox = Path(arguments[1])
        file_name = str(arguments[2])
        if self.exit_code is not None:
            payload = self.copied_payload
            if payload is None:
                payload = (source_parent / file_name).read_bytes()
            (staging_inbox / file_name).write_bytes(payload)
            for extra_name, extra_payload in self.extra_inbox_files.items():
                (staging_inbox / extra_name).write_bytes(extra_payload)
        self.process = _FakeRobocopyProcess(exit_code=self.exit_code)
        return self.process


class _FakeRobocopyProcess:
    def __init__(self, *, exit_code: int | None) -> None:
        self.exit_code = exit_code
        self.terminated_exit_code: int | None = None
        self.closed = False

    def wait(self, *, timeout_seconds: float | None = None) -> int | None:
        return self.exit_code

    def terminate(self, *, exit_code: int = 1) -> None:
        self.terminated_exit_code = exit_code

    def close(self) -> None:
        self.closed = True


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


def _operation(source_payload: bytes) -> RecoveryOperation:
    operation = planned_recovery_operation(
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
        source_endpoint_id="source-a",
        source_endpoint_revision_id="source-rev-a",
        source_relative_path="Pictures/A.jpg",
    )
    return replace(
        operation,
        expected_source_fingerprint_json=json.dumps(
            _fingerprint(source_payload),
            sort_keys=True,
            separators=(",", ":"),
        ),
        staging_object_id="object-a",
    )


def _resolved_executable(tmp_path: Path) -> ResolvedSystemExecutable:
    system_dir = tmp_path / "System32"
    system_dir.mkdir(exist_ok=True)
    executable = system_dir / "Robocopy.exe"
    executable.write_bytes(b"robocopy")
    return ResolvedSystemExecutable(
        requested_name="Robocopy.exe",
        system_directory=system_dir,
        executable_path=executable,
        final_path=str(executable),
        sha256=hashlib.sha256(b"robocopy").hexdigest(),
        file_version="1.2.3.4",
    )


def _manifest_entry(
    *,
    operation_id: str,
    staging_object_id: str,
    source_file_name: str,
    payload_path: Path,
    payload: bytes,
) -> RobocopyBatchManifestEntry:
    return RobocopyBatchManifestEntry(
        operation_id=operation_id,
        staging_object_id=staging_object_id,
        source_file_name=source_file_name,
        source_relative_path=f"Pictures/{source_file_name}",
        final_relative_path=f"Pictures/{source_file_name}",
        payload_path=payload_path,
        expected_byte_count=len(payload),
        expected_content_hash=hashlib.sha256(payload).hexdigest(),
    )


def _fingerprint(payload: bytes) -> dict[str, object]:
    return {"byte_count": len(payload), "content_hash": hashlib.sha256(payload).hexdigest()}
