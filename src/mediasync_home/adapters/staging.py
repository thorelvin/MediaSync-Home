from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootResolver
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.run_staging import (
    SourceStabilityEvidence,
    SourceValidationEvidence,
    StagingAllocation,
    StagingDurabilityEvidence,
    StagingTransferEvidence,
    StagingVerificationEvidence,
    TargetPreconditionEvidence,
)
from mediasync_home.application.safe_paths import SafePathViolation, parse_endpoint_relative_path
from mediasync_home.domain.capabilities import MutationPermit


OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class LocalFileStagingError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class LocalFileStagingTransferAdapter:
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        staging_root: Path | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._staging_root = None if staging_root is None else Path(staging_root)

    def validate_source_file(self, operation: RecoveryOperation) -> SourceValidationEvidence:
        source_path = self._source_path(operation)
        if not source_path.is_file() or source_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_FILE_MISSING",
                "Refresh analysis because the planned source file is no longer readable.",
            )
        fingerprint = _fingerprint_file(source_path)
        return SourceValidationEvidence(
            fingerprint_json=_canonical_json(fingerprint),
            hash_evidence_kind="SHA256_CURRENT_SOURCE_FILE",
        )

    def bind_source_stability(self, operation: RecoveryOperation) -> SourceStabilityEvidence:
        content_hash = _expected_content_hash(
            operation.expected_source_fingerprint_json,
            validation_code="LOCAL_STAGING_REQUIRES_SOURCE_FINGERPRINT",
        )
        return SourceStabilityEvidence(
            guard_kind="POST_TRANSFER_HASH_ONLY",
            guard_evidence_hash=content_hash,
        )

    def validate_target_precondition(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence:
        if operation.target_precondition_kind is RecoveryTargetPreconditionKind.ABSENT:
            return self._validate_absent_target_precondition(permit=permit, operation=operation)
        if operation.target_precondition_kind is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
            return self._validate_match_fingerprint_target_precondition(
                permit=permit,
                operation=operation,
            )
        if operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
            return self._validate_directory_empty_target_precondition(
                permit=permit,
                operation=operation,
            )
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_PRECONDITION_UNSUPPORTED",
            "Use a specialized staging path for this target precondition.",
        )

    def _validate_absent_target_precondition(
        self,
        *,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence:
        final_path = self._target_final_path(permit=permit, operation=operation)
        if not final_path.parent.is_dir():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_PARENT_MISSING",
                "Create and verify the final parent directory before staging this operation.",
            )
        _reject_symlink_in_path(root=self._target_root(permit=permit, operation=operation), path=final_path.parent)
        if final_path.exists() or final_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_EXISTS",
                "Refresh analysis or use the replace/version flow before staging this operation.",
            )
        return TargetPreconditionEvidence(fingerprint_json=_canonical_json({"kind": "ABSENT"}))

    def _validate_match_fingerprint_target_precondition(
        self,
        *,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence:
        final_path = self._target_final_path(permit=permit, operation=operation)
        if not final_path.parent.is_dir():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_PARENT_MISSING",
                "Create and verify the final parent directory before staging this operation.",
            )
        _reject_symlink_in_path(root=self._target_root(permit=permit, operation=operation), path=final_path.parent)
        if not final_path.is_file() or final_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_MATCH_REQUIRES_FILE",
                "Refresh analysis because the target to replace is no longer a regular file.",
            )
        observed = _fingerprint_file(final_path)
        if operation.expected_target_fingerprint_json is not None:
            expected = _expected_target_fingerprint(operation.expected_target_fingerprint_json)
            if observed != expected:
                raise LocalFileStagingError(
                    "LOCAL_STAGING_TARGET_FINGERPRINT_MISMATCH",
                    "Refresh analysis because the target changed before staging.",
                )
        return TargetPreconditionEvidence(fingerprint_json=_canonical_json(observed))

    def _validate_directory_empty_target_precondition(
        self,
        *,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence:
        final_path = self._target_final_path(permit=permit, operation=operation)
        if not final_path.parent.is_dir():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_PARENT_MISSING",
                "Create and verify the final parent directory before staging this operation.",
            )
        target_root = self._target_root(permit=permit, operation=operation)
        _reject_symlink_in_path(root=target_root, path=final_path)
        if not final_path.is_dir() or final_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_DIRECTORY_EMPTY_REQUIRES_DIRECTORY",
                "Refresh analysis because the target is no longer an empty directory.",
            )
        try:
            next(final_path.iterdir())
        except StopIteration:
            return TargetPreconditionEvidence(
                fingerprint_json=_canonical_json(
                    {
                        "entry_count": 0,
                        "kind": RecoveryTargetPreconditionKind.DIRECTORY_EMPTY.value,
                    }
                )
            )
        except OSError as exc:
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_DIRECTORY_EMPTY_READ_FAILED",
                "Refresh analysis because the target directory contents cannot be proven empty.",
            ) from exc
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_DIRECTORY_NOT_EMPTY",
            "Refresh analysis because the target directory is no longer empty.",
        )

    def allocate_staging_object(self, operation: RecoveryOperation) -> StagingAllocation:
        object_id = operation.staging_object_id or operation.operation_id
        if OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise LocalFileStagingError(
                "LOCAL_STAGING_REQUIRES_SAFE_OBJECT_ID",
                "Use an opaque staging object id before transferring source content.",
            )
        self._staging_root_for(operation).mkdir(parents=True, exist_ok=True)
        return StagingAllocation(staging_object_id=object_id)

    def transfer_to_staging(self, operation: RecoveryOperation) -> StagingTransferEvidence:
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        payload_path = self._staging_payload_path(operation)
        if payload_path.exists():
            existing = _fingerprint_file(payload_path)
            if existing == expected:
                return StagingTransferEvidence(transfer_state="TRANSFERRED_EXISTING_MATCH")
            raise LocalFileStagingError(
                "LOCAL_STAGING_EXISTING_PAYLOAD_MISMATCH",
                "Discard the stale staging payload and retry the transfer.",
            )

        source_path = self._source_path(operation)
        temp_path = payload_path.with_name(f".{payload_path.name}.{uuid4().hex}.tmp")
        try:
            observed = _copy_file_with_hash(source=source_path, destination=temp_path)
            if observed != expected:
                raise LocalFileStagingError(
                    "LOCAL_STAGING_SOURCE_CHANGED",
                    "Refresh analysis because the source file changed during transfer.",
                )
            os.replace(temp_path, payload_path)
        finally:
            temp_path.unlink(missing_ok=True)
        return StagingTransferEvidence(transfer_state="TRANSFERRED_TO_STAGING")

    def ensure_staging_durable(self, operation: RecoveryOperation) -> StagingDurabilityEvidence:
        payload_path = self._staging_payload_path(operation)
        if not payload_path.is_file() or payload_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_PAYLOAD_MISSING",
                "Retry the staging transfer before durability verification.",
            )
        with payload_path.open("ab") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        return StagingDurabilityEvidence(durability_state="FILE_FSYNC_COMPLETED")

    def verify_staging_artifact(self, operation: RecoveryOperation) -> StagingVerificationEvidence:
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        payload_path = self._staging_payload_path(operation)
        staging_fingerprint = _fingerprint_file(payload_path)
        if staging_fingerprint != expected:
            raise LocalFileStagingError(
                "LOCAL_STAGING_HASH_MISMATCH",
                "Restage the source file before publishing commit intent.",
            )
        current_source = _fingerprint_file(self._source_path(operation))
        if current_source != expected:
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_CHANGED_AFTER_TRANSFER",
                "Refresh analysis because the source file changed after transfer.",
            )
        fingerprint_json = _canonical_json(staging_fingerprint)
        return StagingVerificationEvidence(
            fingerprint_json=fingerprint_json,
            final_fingerprint_json=fingerprint_json,
            assurance_level="STAGING_HASH_MATCHES_POST_TRANSFER_SOURCE_HASH",
        )

    def _source_path(self, operation: RecoveryOperation) -> Path:
        if (
            operation.source_endpoint_id is None
            or operation.source_endpoint_revision_id is None
            or operation.source_relative_path is None
        ):
            raise LocalFileStagingError(
                "LOCAL_STAGING_REQUIRES_SOURCE_BINDING",
                "Plan copy operations with an explicit source endpoint and relative path.",
            )
        root = self._resolve_root(
            resource_key=f"endpoint:{operation.source_endpoint_id}",
            endpoint_id=operation.source_endpoint_id,
            endpoint_revision_id=operation.source_endpoint_revision_id,
        )
        path = root.joinpath(*_relative_parts(operation.source_relative_path))
        _reject_symlink_in_path(root=root, path=path.parent)
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_ESCAPES_ROOT",
                "Refresh endpoint adoption before reading source content.",
            ) from exc
        return path

    def _target_root(self, *, permit: MutationPermit, operation: RecoveryOperation) -> Path:
        return self._resolve_root(
            resource_key=permit.resource_key,
            endpoint_id=operation.target_endpoint_id,
            endpoint_revision_id=operation.target_endpoint_revision_id,
        )

    def _target_final_path(self, *, permit: MutationPermit, operation: RecoveryOperation) -> Path:
        root = self._target_root(permit=permit, operation=operation)
        final_path = root.joinpath(*_relative_parts(operation.final_relative_path))
        try:
            final_path.resolve(strict=False).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_ESCAPES_ROOT",
                "Refresh endpoint adoption before staging final-path content.",
            ) from exc
        return final_path

    def _staging_payload_path(self, operation: RecoveryOperation) -> Path:
        if operation.staging_object_id is None or not operation.staging_object_id.strip():
            raise LocalFileStagingError(
                "LOCAL_STAGING_OBJECT_NOT_ALLOCATED",
                "Allocate an opaque staging object before transfer.",
            )
        if OBJECT_ID_PATTERN.fullmatch(operation.staging_object_id) is None:
            raise LocalFileStagingError(
                "LOCAL_STAGING_REQUIRES_SAFE_OBJECT_ID",
                "Use an opaque staging object id before transferring source content.",
            )
        return self._staging_root_for(operation) / f"{operation.staging_object_id}.payload"

    def _staging_root_for(self, operation: RecoveryOperation) -> Path:
        if self._staging_root is not None:
            return self._staging_root
        target_root = self._resolve_root(
            resource_key=f"endpoint:{operation.target_endpoint_id}",
            endpoint_id=operation.target_endpoint_id,
            endpoint_revision_id=operation.target_endpoint_revision_id,
        )
        return target_root / ".mediasync" / "objects" / "staging"

    def _resolve_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path:
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=resource_key,
                endpoint_id=endpoint_id,
                endpoint_revision_id=endpoint_revision_id,
            )
        except EndpointLeaseUnavailable:
            raise
        if root is None:
            raise LocalFileStagingError(
                "LOCAL_STAGING_ENDPOINT_ROOT_UNKNOWN",
                "Register endpoint roots before staging run-target operations.",
            )
        try:
            return root.resolve(strict=True)
        except OSError as exc:
            raise LocalFileStagingError(
                "LOCAL_STAGING_ENDPOINT_ROOT_MISSING",
                "Ensure the source and target endpoint roots are reachable before staging.",
            ) from exc


def _relative_parts(value: str) -> tuple[str, ...]:
    try:
        return parse_endpoint_relative_path(value).parts
    except SafePathViolation as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_REQUIRES_RELATIVE_PATH",
            "Refresh analysis so staged operations use endpoint-relative paths.",
        ) from exc


def _reject_symlink_in_path(*, root: Path, path: Path) -> None:
    current = root
    try:
        relative_parts = path.resolve(strict=False).relative_to(root).parts
    except ValueError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_PATH_ESCAPES_ROOT",
            "Refresh endpoint adoption before staging this path.",
        ) from exc
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_REPARSE_UNSUPPORTED",
                "Revalidate paths with a production ReparseGuard before staging.",
            )


def _expected_fingerprint(raw_payload: str | None) -> dict[str, object]:
    if raw_payload is None:
        raise LocalFileStagingError(
            "LOCAL_STAGING_REQUIRES_SOURCE_FINGERPRINT",
            "Validate the source file before transfer.",
        )
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_FINGERPRINT_INVALID",
            "Refresh source validation before transfer.",
        ) from exc
    if not isinstance(payload, dict):
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_FINGERPRINT_INVALID",
            "Refresh source validation before transfer.",
        )
    content_hash = payload.get("content_hash")
    byte_count = payload.get("byte_count")
    if not isinstance(content_hash, str) or len(content_hash) != 64:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_FINGERPRINT_INVALID",
            "Refresh source validation before transfer.",
        )
    if not isinstance(byte_count, int) or byte_count < 0:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_FINGERPRINT_INVALID",
            "Refresh source validation before transfer.",
        )
    return {"byte_count": byte_count, "content_hash": content_hash}


def _expected_content_hash(raw_payload: str | None, *, validation_code: str) -> str:
    fingerprint = _expected_fingerprint(raw_payload)
    content_hash = fingerprint["content_hash"]
    if not isinstance(content_hash, str):
        raise LocalFileStagingError(validation_code, "Refresh source validation before transfer.")
    return content_hash


def _expected_target_fingerprint(raw_payload: str) -> dict[str, object]:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_FINGERPRINT_INVALID",
            "Refresh target precondition evidence before staging.",
        ) from exc
    if not isinstance(payload, dict):
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_FINGERPRINT_INVALID",
            "Refresh target precondition evidence before staging.",
        )
    content_hash = payload.get("content_hash")
    byte_count = payload.get("byte_count")
    if not isinstance(content_hash, str) or len(content_hash) != 64:
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_FINGERPRINT_INVALID",
            "Refresh target precondition evidence before staging.",
        )
    if not isinstance(byte_count, int) or byte_count < 0:
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_FINGERPRINT_INVALID",
            "Refresh target precondition evidence before staging.",
        )
    return {"byte_count": byte_count, "content_hash": content_hash}


def _copy_file_with_hash(*, source: Path, destination: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    byte_count = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return {"byte_count": byte_count, "content_hash": digest.hexdigest()}


def _fingerprint_file(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
    return {"byte_count": byte_count, "content_hash": digest.hexdigest()}


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
