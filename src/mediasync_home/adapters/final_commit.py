from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    VerifiedStagingArtifact,
)
from mediasync_home.application.safe_paths import SafePathViolation, parse_endpoint_relative_path
from mediasync_home.domain.capabilities import MutationPermit


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
LAB_MARKER_NAME = ".mediasync_test_root"


class FinalCommitAdapterError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class MutationPermitValidator(Protocol):
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None: ...


class LabNoOverwriteFinalCommitAdapter(FinalCommitPort):
    def __init__(
        self,
        *,
        target_root: Path,
        staging_root: Path,
        permit_validator: MutationPermitValidator,
    ) -> None:
        self._target_root = Path(target_root)
        self._staging_root = Path(staging_root)
        self._permit_validator = permit_validator

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_lab_marker(self._target_root, permit.run_id)
        staging_payload = _staging_payload_path(self._staging_root, artifact)
        final_path = _final_path(self._target_root, artifact)
        if _hash_file(staging_payload) != artifact.content_hash:
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_STAGING_HASH_MISMATCH",
                "Restage and verify the artifact before attempting final commit.",
            )
        _link_verified_payload_without_overwrite(
            staging_payload=staging_payload,
            final_path=final_path,
            content_hash=artifact.content_hash,
        )
        return CommitReceipt(
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path,
        )


def _require_lab_marker(target_root: Path, run_id: str) -> None:
    marker_path = target_root / LAB_MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_REQUIRES_TEST_ROOT_MARKER",
            "Use a dedicated lab target root with a matching .mediasync_test_root marker.",
        ) from exc
    if not isinstance(marker, dict) or marker.get("run_id") != run_id:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_TEST_ROOT_RUN_MISMATCH",
            "Use a lab target marker bound to the current run before mutating final paths.",
        )


def _staging_payload_path(staging_root: Path, artifact: VerifiedStagingArtifact) -> Path:
    if OBJECT_ID_PATTERN.fullmatch(artifact.object_id) is None:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_REQUIRES_SAFE_OBJECT_ID",
            "Restage the artifact with an opaque object id before final commit.",
        )
    if HASH_PATTERN.fullmatch(artifact.content_hash) is None:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_REQUIRES_CONTENT_HASH",
            "Verify the staging artifact and provide a lowercase SHA-256 content hash.",
        )
    try:
        root = staging_root.resolve(strict=True)
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_STAGING_ROOT_MISSING",
            "Create the staging root before final commit.",
        ) from exc
    payload = root / f"{artifact.object_id}.payload"
    if not payload.is_file() or payload.is_symlink():
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_STAGING_PAYLOAD_MISSING",
            "Restage and verify the artifact before final commit.",
        )
    return payload


def _final_path(target_root: Path, artifact: VerifiedStagingArtifact) -> Path:
    parts = _relative_path_parts(artifact.relative_path.value)
    try:
        root = target_root.resolve(strict=True)
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_TARGET_ROOT_MISSING",
            "Create the marked lab target root before final commit.",
        ) from exc
    final_path = root.joinpath(*parts)
    parent = final_path.parent
    if not parent.is_dir():
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_TARGET_PARENT_MISSING",
            "Create and verify the final parent directory before commit.",
        )
    _reject_symlink_in_path(root=root, relative_parts=parts[:-1])
    try:
        parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_TARGET_ESCAPES_ROOT",
            "Resolve the final path through a validated endpoint root before commit.",
        ) from exc
    if final_path.exists() or final_path.is_symlink():
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_TARGET_EXISTS",
            "Use the replace/version commit flow for existing targets; this adapter only inserts new files.",
        )
    return final_path


def _relative_path_parts(value: str) -> tuple[str, ...]:
    try:
        return parse_endpoint_relative_path(value).parts
    except SafePathViolation as exc:
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_REQUIRES_RELATIVE_PATH",
            "Provide an endpoint-relative final path before commit.",
        ) from exc


def _reject_symlink_in_path(*, root: Path, relative_parts: tuple[str, ...]) -> None:
    current = root
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_REPARSE_UNSUPPORTED",
                "Revalidate the final path chain before committing through reparse points.",
            )


def _link_verified_payload_without_overwrite(
    *,
    staging_payload: Path,
    final_path: Path,
    content_hash: str,
) -> None:
    temp_path = final_path.parent / f".{final_path.name}.{uuid4().hex}.mediasync-commit.tmp"
    try:
        _copy_file_durable(source=staging_payload, destination=temp_path)
        if _hash_file(temp_path) != content_hash:
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_TEMP_HASH_MISMATCH",
                "Restage and verify the artifact before attempting final commit.",
            )
        try:
            os.link(temp_path, final_path)
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_TARGET_EXISTS",
                "Use the replace/version commit flow for existing targets; this adapter only inserts new files.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_NO_OVERWRITE_UNSUPPORTED",
                "Use an endpoint profile with proven same-volume no-overwrite insert support.",
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _copy_file_durable(*, source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("xb") as writer:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
