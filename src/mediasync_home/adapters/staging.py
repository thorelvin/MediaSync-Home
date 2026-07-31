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
    ) -> None:
        self._root_resolver = root_resolver
        self._staging_root = None if staging_root is None else Path(staging_root)
        self._reparse_guard = reparse_guard or LocalReparseGuard()

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
            fingerprint = _fingerprint_source_file(
                source_path,
                precondition=_source_precondition(operation),
            )
        except LocalFileStagingError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise
        return SourceValidationEvidence(
            fingerprint_json=_canonical_json(fingerprint),
            hash_evidence_kind="SHA256_CURRENT_SOURCE_FILE",
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
            observed = _fingerprint_file(final_path)
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
        if payload_path.exists():
            try:
                existing = _fingerprint_file(payload_path)
            except OSError as exc:
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
                if observed != expected:
                    raise LocalFileStagingError(
                        "LOCAL_STAGING_SOURCE_CHANGED",
                        "Refresh analysis because the source file changed during transfer.",
                    )
                os.replace(temp_path, payload_path)
            finally:
                temp_path.unlink(missing_ok=True)
        except LocalFileStagingError:
            raise
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
            return StagingDurabilityEvidence(durability_state="FILE_FSYNC_COMPLETED")
        except LocalFileStagingError:
            raise
        except OSError as exc:
            self._raise_endpoint_wait_if_unavailable(operation, error=exc)
            raise LocalFileStagingError(
                "LOCAL_STAGING_DURABILITY_FAILED",
                "Retry after the staging storage becomes responsive.",
            ) from exc

    def verify_staging_artifact(
        self, operation: RecoveryOperation
    ) -> StagingVerificationEvidence:
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            return self._verify_staging_directory(operation)
        expected = _expected_fingerprint(operation.expected_source_fingerprint_json)
        payload_path = self._staging_payload_path(operation)
        try:
            staging_fingerprint = _fingerprint_file(payload_path)
        except OSError as exc:
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
            current_source = _fingerprint_source_file(
                self._source_path(operation),
                precondition=_source_precondition(operation),
            )
        except LocalFileStagingError as exc:
            self._raise_endpoint_wait_if_unavailable(
                operation,
                error=exc.__cause__,
                include_source=True,
            )
            raise
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
                _require_source_identity(os.fstat(stream.fileno()), precondition)
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
        raise LocalFileStagingError(
            validation_code, "Refresh source validation before transfer."
        )
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
            _require_source_identity(os.fstat(handle.fileno()), precondition)
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                byte_count += len(chunk)
            _require_source_identity(os.fstat(handle.fileno()), precondition)
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
    if stable_file_identity_hash(value) != precondition.identity_fingerprint_hash:
        raise LocalFileStagingError(
            "LOCAL_STAGING_SOURCE_IDENTITY_CHANGED",
            "Refresh analysis because the source file changed after it was scanned.",
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
