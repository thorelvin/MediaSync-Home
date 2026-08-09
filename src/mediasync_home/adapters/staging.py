from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointRootResolver,
)
from mediasync_home.adapters.file_identity import stable_file_identity_hash
from mediasync_home.adapters.file_object_fingerprints import (
    LocalFileObjectFingerprintAdapter,
    LocalFileObjectFingerprintError,
)
from mediasync_home.adapters.reparse_guard import (
    LocalReparseGuard,
    ReparseGuard,
    ReparseGuardError,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.directory_artifacts import (
    DIRECTORY_MARKER_NAME,
    directory_artifact_fingerprint,
    directory_artifact_matches,
    directory_marker_bytes,
)
from mediasync_home.application.file_object_fingerprints import (
    FileObjectFingerprintError,
    canonical_file_object_fingerprint_json,
    file_object_fingerprint_from_json,
    has_named_stream_inventory,
)
from mediasync_home.application.run_staging import (
    RunTargetEndpointWaitRequired,
    SourceStabilityEvidence,
    SourceValidationEvidence,
    StagingAllocation,
    StagingDurabilityEvidence,
    StagingTransferEvidence,
    StagingVerificationEvidence,
    TargetPreconditionEvidence,
)
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)
from mediasync_home.application.source_preconditions import (
    SourceFilePrecondition,
    SourceFilePreconditionError,
)
from mediasync_home.application.staging_objects import (
    StagingObjectManifestError,
    staging_object_manifest_from_operation,
)
from mediasync_home.domain.capabilities import MutationPermit


OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
NETWORK_INTERRUPTED_NEXT_ACTION = (
    "Reconnect the unavailable endpoint; MediaSync will retry this target "
    "after fresh preflight."
)
_WINDOWS_ENDPOINT_INTERRUPTION_CODES = frozenset(
    {
        21,  # ERROR_NOT_READY
        53,  # ERROR_BAD_NETPATH
        54,  # ERROR_NETWORK_BUSY
        55,  # ERROR_DEV_NOT_EXIST
        59,  # ERROR_UNEXP_NET_ERR
        64,  # ERROR_NETNAME_DELETED
        67,  # ERROR_BAD_NET_NAME
        121,  # ERROR_SEM_TIMEOUT
        1167,  # ERROR_DEVICE_NOT_CONNECTED
        1201,  # ERROR_CONNECTION_UNAVAIL
        1225,  # ERROR_CONNECTION_REFUSED
        1231,  # ERROR_NETWORK_UNREACHABLE
        1232,  # ERROR_HOST_UNREACHABLE
        1233,  # ERROR_PROTOCOL_UNREACHABLE
        1234,  # ERROR_PORT_UNREACHABLE
        1235,  # ERROR_REQUEST_ABORTED
        1236,  # ERROR_CONNECTION_ABORTED
        2250,  # ERROR_NOT_CONNECTED
    }
)


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
        reparse_guard: ReparseGuard | None = None,
        file_object_fingerprints: LocalFileObjectFingerprintAdapter | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._staging_root = None if staging_root is None else Path(staging_root)
        self._reparse_guard = reparse_guard or LocalReparseGuard()
        self._file_object_fingerprints = (
            file_object_fingerprints or LocalFileObjectFingerprintAdapter()
        )

    def validate_source_file(
        self, operation: RecoveryOperation
    ) -> SourceValidationEvidence:
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            return SourceValidationEvidence(
                fingerprint_json=_canonical_json(_directory_fingerprint(operation)),
                hash_evidence_kind="SHA256_DIRECTORY_RECOVERY_MARKER",
            )
        source_path = self._source_path(operation)
        if not source_path.is_file() or source_path.is_symlink():
            self._raise_endpoint_wait_if_unavailable(
                operation,
                include_source=True,
            )
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_FILE_MISSING",
                "Refresh analysis because the planned source file is no longer readable.",
            )
        try:
            primary_fingerprint = _fingerprint_source_file(
                source_path,
                precondition=_source_precondition(operation),
            )
            fingerprint = self._file_object_fingerprints.fingerprint(source_path)
            if (
                fingerprint.get("byte_count") != primary_fingerprint["byte_count"]
                or fingerprint.get("content_hash")
                != primary_fingerprint["content_hash"]
            ):
                raise LocalFileStagingError(
                    "LOCAL_STAGING_SOURCE_CHANGED_DURING_STREAM_INVENTORY",
                    "Refresh analysis because the source changed during validation.",
                )
            self._validate_source_identity(operation, source_path)
        except LocalFileStagingError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise
        except LocalFileObjectFingerprintError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise LocalFileStagingError(
                exc.validation_code,
                "Retry after every source data stream becomes readable and stable.",
            ) from exc
        return SourceValidationEvidence(
            fingerprint_json=canonical_file_object_fingerprint_json(fingerprint),
            hash_evidence_kind="SHA256_CURRENT_SOURCE_FILE_OBJECT_STREAMS_V1",
        )

    def bind_source_stability(
        self, operation: RecoveryOperation
    ) -> SourceStabilityEvidence:
        if operation.operation_kind is RecoveryOperationKind.COPY_NEW:
            precondition = _source_precondition(operation)
            return SourceStabilityEvidence(
                guard_kind="PLAN_IDENTITY_AND_OPEN_READ_FSTAT_V1",
                guard_evidence_hash=precondition.identity_fingerprint_hash,
            )
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
            return self._validate_absent_target_precondition(
                permit=permit, operation=operation
            )
        if (
            operation.target_precondition_kind
            is RecoveryTargetPreconditionKind.MATCH_FINGERPRINT
        ):
            return self._validate_match_fingerprint_target_precondition(
                permit=permit,
                operation=operation,
            )
        if (
            operation.target_precondition_kind
            is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY
        ):
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
            self._raise_endpoint_wait_if_unavailable(operation)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_PARENT_MISSING",
                "Create and verify the final parent directory before staging this operation.",
            )
        _reject_reparse_in_path(
            guard=self._reparse_guard,
            root=self._target_root(permit=permit, operation=operation),
            path=final_path.parent,
        )
        if final_path.exists() or final_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_EXISTS",
                "Refresh analysis or use the replace/version flow before staging this operation.",
            )
        return TargetPreconditionEvidence(
            fingerprint_json=_canonical_json({"kind": "ABSENT"})
        )

    def _validate_match_fingerprint_target_precondition(
        self,
        *,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> TargetPreconditionEvidence:
        final_path = self._target_final_path(permit=permit, operation=operation)
        if not final_path.parent.is_dir():
            self._raise_endpoint_wait_if_unavailable(operation)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_PARENT_MISSING",
                "Create and verify the final parent directory before staging this operation.",
            )
        _reject_reparse_in_path(
            guard=self._reparse_guard,
            root=self._target_root(permit=permit, operation=operation),
            path=final_path.parent,
        )
        if not final_path.is_file() or final_path.is_symlink():
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_MATCH_REQUIRES_FILE",
                "Refresh analysis because the target to replace is no longer a regular file.",
            )
        try:
            observed = self._file_object_fingerprints.fingerprint(final_path)
        except LocalFileObjectFingerprintError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc.__cause__)
            raise LocalFileStagingError(
                exc.validation_code,
                "Refresh analysis after every target data stream becomes readable.",
            ) from exc
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_FINGERPRINT_READ_FAILED",
                "Refresh analysis after the target file becomes readable.",
            ) from exc
        if operation.expected_target_fingerprint_json is not None:
            expected = _expected_target_fingerprint(
                operation.expected_target_fingerprint_json
            )
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
            self._raise_endpoint_wait_if_unavailable(operation)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_PARENT_MISSING",
                "Create and verify the final parent directory before staging this operation.",
            )
        target_root = self._target_root(permit=permit, operation=operation)
        _reject_reparse_in_path(
            guard=self._reparse_guard,
            root=target_root,
            path=final_path,
        )
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
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_DIRECTORY_EMPTY_READ_FAILED",
                "Refresh analysis because the target directory contents cannot be proven empty.",
            ) from exc
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_DIRECTORY_NOT_EMPTY",
            "Refresh analysis because the target directory is no longer empty.",
        )

    def allocate_staging_object(
        self, operation: RecoveryOperation
    ) -> StagingAllocation:
        object_id = operation.staging_object_id or operation.operation_id
        if OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise LocalFileStagingError(
                "LOCAL_STAGING_REQUIRES_SAFE_OBJECT_ID",
                "Use an opaque staging object id before transferring source content.",
            )
        try:
            self._staging_root_for(operation).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_ALLOCATION_FAILED",
                "Retry after the staging storage becomes writable.",
            ) from exc
        return StagingAllocation(staging_object_id=object_id)

    def transfer_to_staging(
        self, operation: RecoveryOperation
    ) -> StagingTransferEvidence:
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            return self._transfer_directory_to_staging(operation)
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        payload_path = self._staging_payload_path(operation)
        self._ensure_staging_manifest(operation)
        if payload_path.exists():
            try:
                existing = self._file_object_fingerprints.fingerprint(payload_path)
            except (OSError, LocalFileObjectFingerprintError) as exc:
                self._raise_endpoint_wait_if_unavailable(operation, error=exc)
                raise LocalFileStagingError(
                    "LOCAL_STAGING_EXISTING_PAYLOAD_READ_FAILED",
                    "Retry after the staging payload becomes readable.",
                ) from exc
            if existing == expected:
                return StagingTransferEvidence(
                    transfer_state="TRANSFERRED_EXISTING_MATCH"
                )
            raise LocalFileStagingError(
                "LOCAL_STAGING_EXISTING_PAYLOAD_MISMATCH",
                "Discard the stale staging payload and retry the transfer.",
            )

        source_path = self._source_path(operation)
        temp_path = payload_path.with_name(f".{payload_path.name}.{uuid4().hex}.tmp")
        try:
            try:
                observed = _copy_file_with_hash(
                    source=source_path,
                    destination=temp_path,
                    precondition=_source_precondition(operation),
                )
                if observed != {
                    "byte_count": expected["byte_count"],
                    "content_hash": expected["content_hash"],
                }:
                    raise LocalFileStagingError(
                        "LOCAL_STAGING_SOURCE_CHANGED",
                        "Refresh analysis because the source file changed during transfer.",
                    )
                self._file_object_fingerprints.copy_named_streams(
                    source=source_path,
                    destination=temp_path,
                    expected_fingerprint=expected,
                )
                if self._file_object_fingerprints.fingerprint(temp_path) != expected:
                    raise LocalFileStagingError(
                        "LOCAL_STAGING_FILE_OBJECT_MISMATCH",
                        "Refresh analysis because the source file object changed during transfer.",
                    )
                os.replace(temp_path, payload_path)
            finally:
                temp_path.unlink(missing_ok=True)
        except LocalFileStagingError:
            raise
        except LocalFileObjectFingerprintError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise LocalFileStagingError(
                exc.validation_code,
                "Retry after every source and staging data stream becomes available.",
            ) from exc
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc,
                include_source=True,
            )
            raise LocalFileStagingError(
                "LOCAL_STAGING_TRANSFER_FAILED",
                "Retry after source and staging storage become readable and writable.",
            ) from exc
        return StagingTransferEvidence(transfer_state="TRANSFERRED_TO_STAGING")

    def ensure_staging_durable(
        self, operation: RecoveryOperation
    ) -> StagingDurabilityEvidence:
        self._ensure_staging_manifest(operation)
        payload_path = self._staging_payload_path(operation)
        try:
            if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
                if not _directory_payload_matches(payload_path, operation):
                    raise LocalFileStagingError(
                        "LOCAL_STAGING_DIRECTORY_PAYLOAD_MISSING",
                        "Retry directory staging before durability verification.",
                    )
                marker_path = payload_path / DIRECTORY_MARKER_NAME
                with marker_path.open("ab") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                return StagingDurabilityEvidence(
                    durability_state="DIRECTORY_MARKER_FILE_FSYNC_COMPLETED"
                )
            if not payload_path.is_file() or payload_path.is_symlink():
                raise LocalFileStagingError(
                    "LOCAL_STAGING_PAYLOAD_MISSING",
                    "Retry the staging transfer before durability verification.",
                )
            with payload_path.open("ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            self._file_object_fingerprints.flush_named_streams(
                path=payload_path,
                expected_fingerprint=_expected_fingerprint(
                    operation.expected_source_fingerprint_json
                ),
            )
            return StagingDurabilityEvidence(
                durability_state="FILE_OBJECT_STREAMS_FSYNC_COMPLETED"
            )
        except LocalFileStagingError:
            raise
        except LocalFileObjectFingerprintError as exc:
            raise LocalFileStagingError(
                exc.validation_code,
                "Retry after every staging data stream becomes writable.",
            ) from exc
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_DURABILITY_FAILED",
                "Retry after the staging storage becomes responsive.",
            ) from exc

    def verify_staging_artifact(
        self, operation: RecoveryOperation
    ) -> StagingVerificationEvidence:
        self._ensure_staging_manifest(operation)
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            return self._verify_staging_directory(operation)
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        payload_path = self._staging_payload_path(operation)
        try:
            staging_fingerprint = self._file_object_fingerprints.fingerprint(
                payload_path
            )
        except (OSError, LocalFileObjectFingerprintError) as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_VERIFICATION_FAILED",
                "Retry after the staging artifact becomes readable.",
            ) from exc
        if staging_fingerprint != expected:
            raise LocalFileStagingError(
                "LOCAL_STAGING_HASH_MISMATCH",
                "Restage the source file before publishing commit intent.",
            )
        try:
            current_primary = _fingerprint_source_file(
                self._source_path(operation),
                precondition=_source_precondition(operation),
            )
            current_source = self._file_object_fingerprints.fingerprint(
                self._source_path(operation)
            )
            if (
                current_source.get("byte_count") != current_primary["byte_count"]
                or current_source.get("content_hash")
                != current_primary["content_hash"]
            ):
                raise LocalFileStagingError(
                    "LOCAL_STAGING_SOURCE_CHANGED_AFTER_TRANSFER",
                    "Refresh analysis because the source changed after transfer.",
                )
        except LocalFileStagingError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise
        except LocalFileObjectFingerprintError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise LocalFileStagingError(
                exc.validation_code,
                "Retry after every source data stream becomes readable and stable.",
            ) from exc
        if current_source != expected:
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_CHANGED_AFTER_TRANSFER",
                "Refresh analysis because the source file changed after transfer.",
            )
        fingerprint_json = canonical_file_object_fingerprint_json(
            staging_fingerprint
        )
        return StagingVerificationEvidence(
            fingerprint_json=fingerprint_json,
            final_fingerprint_json=fingerprint_json,
            assurance_level=(
                "NAMED_STREAMS_VERIFIED"
                if has_named_stream_inventory(staging_fingerprint)
                else "STAGING_HASH_MATCHES_POST_TRANSFER_SOURCE_HASH"
            ),
        )

    def _transfer_directory_to_staging(
        self,
        operation: RecoveryOperation,
    ) -> StagingTransferEvidence:
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        if expected != _directory_fingerprint(operation):
            raise LocalFileStagingError(
                "LOCAL_STAGING_DIRECTORY_FINGERPRINT_MISMATCH",
                "Reload the planned directory operation before staging it.",
            )
        payload_path = self._staging_payload_path(operation)
        self._ensure_staging_manifest(operation)
        if payload_path.exists() or payload_path.is_symlink():
            if _directory_payload_matches(payload_path, operation):
                return StagingTransferEvidence(
                    transfer_state="TRANSFERRED_EXISTING_MATCH"
                )
            raise LocalFileStagingError(
                "LOCAL_STAGING_EXISTING_DIRECTORY_PAYLOAD_MISMATCH",
                "Discard the stale directory staging payload and retry.",
            )

        temp_path = payload_path.with_name(f".{payload_path.name}.{uuid4().hex}.tmp")
        try:
            try:
                temp_path.mkdir()
                marker_path = temp_path / DIRECTORY_MARKER_NAME
                marker_path.write_bytes(
                    directory_marker_bytes(
                        run_id=operation.run_id,
                        run_target_id=operation.run_target_id,
                        operation_id=operation.operation_id,
                        final_relative_path=operation.final_relative_path,
                    )
                )
                with marker_path.open("ab") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, payload_path)
            finally:
                if temp_path.exists():
                    shutil.rmtree(temp_path)
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TRANSFER_FAILED",
                "Retry after the staging storage becomes writable.",
            ) from exc
        return StagingTransferEvidence(
            transfer_state="DIRECTORY_TRANSFERRED_TO_STAGING"
        )

    def _verify_staging_directory(
        self,
        operation: RecoveryOperation,
    ) -> StagingVerificationEvidence:
        payload_path = self._staging_payload_path(operation)
        if not _directory_payload_matches(payload_path, operation):
            raise LocalFileStagingError(
                "LOCAL_STAGING_DIRECTORY_MARKER_MISMATCH",
                "Restage the directory marker before publishing commit intent.",
            )
        fingerprint_json = _canonical_json(_directory_fingerprint(operation))
        return StagingVerificationEvidence(
            fingerprint_json=fingerprint_json,
            final_fingerprint_json=fingerprint_json,
            assurance_level="STAGING_DIRECTORY_MARKER_VERIFIED",
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
        _reject_reparse_in_path(
            guard=self._reparse_guard,
            root=root,
            path=path.parent,
        )
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=True))
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc,
                include_source=True,
            )
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_ESCAPES_ROOT",
                "Refresh endpoint adoption before reading source content.",
            ) from exc
        except ValueError as exc:
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_ESCAPES_ROOT",
                "Refresh endpoint adoption before reading source content.",
            ) from exc
        return path

    def _validate_source_identity(
        self,
        operation: RecoveryOperation,
        source_path: Path,
    ) -> None:
        precondition = _source_precondition(operation)
        try:
            with source_path.open("rb", buffering=0) as stream:
                _require_source_identity(
                    os.fstat(stream.fileno()),
                    precondition,
                    path_stat=source_path.stat(follow_symlinks=False),
                )
        except LocalFileStagingError:
            raise
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc,
                include_source=True,
            )
            raise LocalFileStagingError(
                "LOCAL_STAGING_SOURCE_FILE_UNREADABLE",
                "Close applications using the file and refresh the backup analysis.",
            ) from exc

    def _target_root(
        self, *, permit: MutationPermit, operation: RecoveryOperation
    ) -> Path:
        return self._resolve_root(
            resource_key=permit.resource_key,
            endpoint_id=operation.target_endpoint_id,
            endpoint_revision_id=operation.target_endpoint_revision_id,
        )

    def _target_final_path(
        self, *, permit: MutationPermit, operation: RecoveryOperation
    ) -> Path:
        root = self._target_root(permit=permit, operation=operation)
        final_path = root.joinpath(*_relative_parts(operation.final_relative_path))
        try:
            final_path.resolve(strict=False).relative_to(root.resolve(strict=True))
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_ESCAPES_ROOT",
                "Refresh endpoint adoption before staging final-path content.",
            ) from exc
        except ValueError as exc:
            raise LocalFileStagingError(
                "LOCAL_STAGING_TARGET_ESCAPES_ROOT",
                "Refresh endpoint adoption before staging final-path content.",
            ) from exc
        return final_path

    def _staging_payload_path(self, operation: RecoveryOperation) -> Path:
        if (
            operation.staging_object_id is None
            or not operation.staging_object_id.strip()
        ):
            raise LocalFileStagingError(
                "LOCAL_STAGING_OBJECT_NOT_ALLOCATED",
                "Allocate an opaque staging object before transfer.",
            )
        if OBJECT_ID_PATTERN.fullmatch(operation.staging_object_id) is None:
            raise LocalFileStagingError(
                "LOCAL_STAGING_REQUIRES_SAFE_OBJECT_ID",
                "Use an opaque staging object id before transferring source content.",
            )
        return (
            self._staging_root_for(operation) / f"{operation.staging_object_id}.payload"
        )

    def _staging_manifest_path(self, operation: RecoveryOperation) -> Path:
        if (
            operation.staging_object_id is None
            or OBJECT_ID_PATTERN.fullmatch(operation.staging_object_id) is None
        ):
            raise LocalFileStagingError(
                "LOCAL_STAGING_OBJECT_NOT_ALLOCATED",
                "Allocate an opaque staging object before publishing its manifest.",
            )
        return (
            self._staging_root_for(operation)
            / f"{operation.staging_object_id}.manifest.json"
        )

    def _ensure_staging_manifest(self, operation: RecoveryOperation) -> None:
        manifest_path = self._staging_manifest_path(operation)
        try:
            expected = staging_object_manifest_from_operation(operation).canonical_json
        except StagingObjectManifestError as exc:
            raise LocalFileStagingError(
                exc.validation_code,
                "Reload the journaled staging operation before publishing its manifest.",
            ) from exc
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_MANIFEST_WRITE_FAILED",
                "Retry after the staging manifest store becomes writable.",
            ) from exc
        if manifest_path.exists() or manifest_path.is_symlink():
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise LocalFileStagingError(
                    "LOCAL_STAGING_MANIFEST_INVALID",
                    "Enter recovery because the staging manifest path is not a regular file.",
                )
            try:
                existing = manifest_path.read_text(encoding="utf-8")
            except OSError as exc:
                self._raise_endpoint_wait_if_unavailable(operation, error=exc)
                raise LocalFileStagingError(
                    "LOCAL_STAGING_MANIFEST_INVALID",
                    "Enter recovery because the staging manifest cannot be read.",
                ) from exc
            if existing != expected:
                raise LocalFileStagingError(
                    "LOCAL_STAGING_MANIFEST_CONFLICT",
                    "Enter recovery because the staging object belongs to different operation evidence.",
                )
            return

        temp_path = manifest_path.with_name(
            f".{manifest_path.name}.{uuid4().hex}.tmp"
        )
        try:
            try:
                with temp_path.open("x", encoding="utf-8") as handle:
                    handle.write(expected)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, manifest_path)
            finally:
                temp_path.unlink(missing_ok=True)
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_MANIFEST_WRITE_FAILED",
                "Retry after the staging manifest store becomes writable.",
            ) from exc

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
            return self._reparse_guard.resolve_existing_root(
                root,
                missing_code="LOCAL_STAGING_ENDPOINT_ROOT_MISSING",
                missing_next_action=(
                    "Ensure the source and target endpoint roots are reachable before staging."
                ),
                reparse_code="LOCAL_STAGING_ENDPOINT_ROOT_REPARSE_UNSUPPORTED",
                reparse_next_action="Revalidate endpoint adoption before staging through this root.",
            )
        except ReparseGuardError as exc:
            if exc.validation_code == "LOCAL_STAGING_ENDPOINT_ROOT_MISSING":
                self._raise_network_interrupted()
            raise LocalFileStagingError(
                exc.validation_code,
                exc.next_action,
            ) from exc

    def _raise_endpoint_wait_if_unavailable(
        self,
        operation: RecoveryOperation,
        *,
        error: BaseException | None = None,
        include_source: bool = False,
    ) -> None:
        if _is_endpoint_interruption_error(error):
            self._raise_network_interrupted()

        bindings = [
            (
                f"endpoint:{operation.target_endpoint_id}",
                operation.target_endpoint_id,
                operation.target_endpoint_revision_id,
            )
        ]
        if (
            include_source
            and operation.source_endpoint_id is not None
            and operation.source_endpoint_revision_id is not None
        ):
            bindings.append(
                (
                    f"endpoint:{operation.source_endpoint_id}",
                    operation.source_endpoint_id,
                    operation.source_endpoint_revision_id,
                )
            )
        for resource_key, endpoint_id, endpoint_revision_id in bindings:
            try:
                root = self._root_resolver.resolve_endpoint_root(
                    resource_key=resource_key,
                    endpoint_id=endpoint_id,
                    endpoint_revision_id=endpoint_revision_id,
                )
            except EndpointLeaseUnavailable as exc:
                if exc.validation_code == "ENDPOINT_ROOT_UNAVAILABLE":
                    self._raise_network_interrupted()
                raise
            if root is None:
                continue
            try:
                root_stat = Path(root).stat()
            except OSError:
                self._raise_network_interrupted()
            if not stat.S_ISDIR(root_stat.st_mode):
                continue

    @staticmethod
    def _raise_network_interrupted() -> None:
        raise RunTargetEndpointWaitRequired(
            reason_code="NETWORK_INTERRUPTED",
            next_action=NETWORK_INTERRUPTED_NEXT_ACTION,
        )


def _is_endpoint_interruption_error(error: BaseException | None) -> bool:
    current = error
    while current is not None:
        if isinstance(current, OSError):
            winerror = getattr(current, "winerror", None)
            if isinstance(winerror, int):
                return winerror in _WINDOWS_ENDPOINT_INTERRUPTION_CODES
            if isinstance(current.errno, int):
                return current.errno in _WINDOWS_ENDPOINT_INTERRUPTION_CODES
        current = current.__cause__
    return False


def _relative_parts(value: str) -> tuple[str, ...]:
    try:
        return parse_endpoint_relative_path(value).parts
    except SafePathViolation as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_REQUIRES_RELATIVE_PATH",
            "Refresh analysis so staged operations use endpoint-relative paths.",
        ) from exc


def _reject_reparse_in_path(
    *,
    guard: ReparseGuard,
    root: Path,
    path: Path,
    validation_code: str = "LOCAL_STAGING_REPARSE_UNSUPPORTED",
    next_action: str = "Revalidate paths with a production ReparseGuard before staging.",
) -> None:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_PATH_ESCAPES_ROOT",
            "Refresh endpoint adoption before staging this path.",
        ) from exc
    try:
        guard.reject_reparse_chain(
            root=root,
            relative_parts=relative_parts,
            missing_code="LOCAL_STAGING_PATH_CHAIN_MISSING",
            missing_next_action="Refresh analysis because the staging path chain changed.",
            reparse_code=validation_code,
            reparse_next_action=next_action,
        )
        guard.require_resolved_under_root(
            root=root,
            path=path,
            strict=True,
            escape_code="LOCAL_STAGING_PATH_ESCAPES_ROOT",
            escape_next_action="Refresh endpoint adoption before staging this path.",
        )
    except ReparseGuardError as exc:
        raise LocalFileStagingError(exc.validation_code, exc.next_action) from exc


def _expected_fingerprint(raw_payload: str | None) -> dict[str, object]:
    if raw_payload is None:
        raise LocalFileStagingError(
            "LOCAL_STAGING_REQUIRES_SOURCE_FINGERPRINT",
            "Validate the source file before transfer.",
        )
    try:
        return file_object_fingerprint_from_json(raw_payload)
    except FileObjectFingerprintError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_FINGERPRINT_INVALID",
            "Refresh source validation before transfer.",
        ) from exc


def _expected_content_hash(raw_payload: str | None, *, validation_code: str) -> str:
    fingerprint = _expected_fingerprint(raw_payload)
    content_hash = fingerprint["content_hash"]
    if not isinstance(content_hash, str):
        raise LocalFileStagingError(
            validation_code, "Refresh source validation before transfer."
        )
    return content_hash


def _expected_target_fingerprint(raw_payload: str) -> dict[str, object]:
    try:
        return file_object_fingerprint_from_json(raw_payload)
    except FileObjectFingerprintError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_TARGET_FINGERPRINT_INVALID",
            "Refresh target precondition evidence before staging.",
        ) from exc


def _copy_file_with_hash(
    *,
    source: Path,
    destination: Path,
    precondition: SourceFilePrecondition,
) -> dict[str, object]:
    digest = hashlib.sha256()
    byte_count = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        _require_source_identity(os.fstat(reader.fileno()), precondition)
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
        _require_source_identity(os.fstat(reader.fileno()), precondition)
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


def _fingerprint_source_file(
    path: Path,
    *,
    precondition: SourceFilePrecondition,
) -> dict[str, object]:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("rb", buffering=0) as handle:
            _require_source_identity(
                os.fstat(handle.fileno()),
                precondition,
                path_stat=path.stat(follow_symlinks=False),
            )
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                byte_count += len(chunk)
            _require_source_identity(
                os.fstat(handle.fileno()),
                precondition,
                path_stat=path.stat(follow_symlinks=False),
            )
    except LocalFileStagingError:
        raise
    except OSError as exc:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_FILE_UNREADABLE",
            "Close applications using the file and refresh the backup analysis.",
        ) from exc
    return {"byte_count": byte_count, "content_hash": digest.hexdigest()}


def _source_precondition(operation: RecoveryOperation) -> SourceFilePrecondition:
    try:
        precondition = SourceFilePrecondition.from_json(
            operation.source_precondition_json
        )
    except SourceFilePreconditionError as exc:
        raise LocalFileStagingError(exc.validation_code, exc.next_action) from exc
    if (
        operation.source_relative_path != precondition.relative_path
        or operation.planned_bytes != precondition.size_bytes
    ):
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_PRECONDITION_MISMATCH",
            "Refresh analysis before copying this source file.",
        )
    return precondition


def _require_source_identity(
    value: os.stat_result,
    precondition: SourceFilePrecondition,
    *,
    path_stat: os.stat_result | None = None,
) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_TYPE_CHANGED",
            "Refresh analysis because the source is no longer a regular file.",
        )
    if int(value.st_size) != precondition.size_bytes:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_SIZE_CHANGED",
            "Refresh analysis because the source file size changed.",
        )
    if stable_file_identity_hash(value) == precondition.identity_fingerprint_hash:
        return
    if (
        path_stat is not None
        and stable_file_identity_hash(path_stat)
        == precondition.identity_fingerprint_hash
        and _same_file_object_identity(value, path_stat)
    ):
        return
    raise LocalFileStagingError(
        "LOCAL_STAGING_SOURCE_IDENTITY_CHANGED",
        "Refresh analysis because the source file changed after it was scanned.",
    )


def _same_file_object_identity(
    opened: os.stat_result,
    path_stat: os.stat_result,
) -> bool:
    return (
        stat.S_IFMT(opened.st_mode) == stat.S_IFMT(path_stat.st_mode)
        and int(opened.st_size) == int(path_stat.st_size)
        and int(opened.st_mtime_ns) == int(path_stat.st_mtime_ns)
        and int(opened.st_dev) == int(path_stat.st_dev)
        and int(opened.st_ino) == int(path_stat.st_ino)
        and int(getattr(opened, "st_birthtime_ns", 0))
        == int(getattr(path_stat, "st_birthtime_ns", 0))
        and int(getattr(opened, "st_file_attributes", 0))
        == int(getattr(path_stat, "st_file_attributes", 0))
    )


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _directory_fingerprint(operation: RecoveryOperation) -> dict[str, object]:
    return directory_artifact_fingerprint(
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        final_relative_path=operation.final_relative_path,
    )


def _directory_payload_matches(path: Path, operation: RecoveryOperation) -> bool:
    return directory_artifact_matches(
        path,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        final_relative_path=operation.final_relative_path,
    )
