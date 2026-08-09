from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointRootResolver,
)
from mediasync_home.adapters.final_commit import require_endpoint_marker
from mediasync_home.application.directory_metadata import (
    DirectoryMetadataApplyReceipt,
    DirectoryMetadataError,
    DirectoryMetadataMutationPort,
    DirectoryMetadataPreparationReceipt,
    canonical_directory_metadata,
    parse_directory_metadata,
)
from mediasync_home.application.directory_recovery import DirectoryRecoveryOperation
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)
from mediasync_home.domain.capabilities import MutationPermit


class DirectoryMetadataPermitValidator(Protocol):
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None: ...


class LocalDirectoryMetadataAdapter(DirectoryMetadataMutationPort):
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        permit_validator: DirectoryMetadataPermitValidator,
    ) -> None:
        self._root_resolver = root_resolver
        self._permit_validator = permit_validator

    def prepare_directory_metadata(
        self,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryMetadataPreparationReceipt:
        path = self._validated_path(permit=permit, operation=operation)
        parse_directory_metadata(_desired_metadata(operation))
        return DirectoryMetadataPreparationReceipt(
            recovery_id=operation.recovery_id,
            observed_metadata_json=_observed_metadata(path),
        )

    def apply_directory_metadata(
        self,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryMetadataApplyReceipt:
        path = self._validated_path(permit=permit, operation=operation)
        modified_ns = parse_directory_metadata(_desired_metadata(operation))
        try:
            current = path.stat(follow_symlinks=False)
            os.utime(
                path,
                ns=(current.st_atime_ns, modified_ns),
            )
        except OSError as exc:
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_APPLY_FAILED",
                "Keep recovery state and retry after confirming the directory is writable.",
            ) from exc
        return DirectoryMetadataApplyReceipt(
            recovery_id=operation.recovery_id,
            applied_metadata_json=_observed_metadata(path),
        )

    def verify_directory_metadata(
        self,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryMetadataApplyReceipt:
        path = self._validated_path(permit=permit, operation=operation)
        desired = _desired_metadata(operation)
        observed = _observed_metadata(path)
        if observed != desired:
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_VERIFICATION_MISMATCH",
                "Reapply directory metadata after child operations are terminal.",
            )
        return DirectoryMetadataApplyReceipt(
            recovery_id=operation.recovery_id,
            applied_metadata_json=observed,
        )

    def _validated_path(
        self,
        *,
        permit: MutationPermit,
        operation: DirectoryRecoveryOperation,
    ) -> Path:
        self._permit_validator.assert_mutation_permit_current(permit)
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=permit.resource_key,
                endpoint_id=operation.target_endpoint_id,
                endpoint_revision_id=operation.target_endpoint_revision_id,
            )
        except EndpointLeaseUnavailable:
            raise
        if root is None:
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_ENDPOINT_ROOT_UNKNOWN",
                "Register the target endpoint root before applying directory metadata.",
            )
        try:
            resolved_root = Path(root).resolve(strict=True)
            relative = parse_endpoint_relative_path(operation.final_relative_path)
        except (OSError, SafePathViolation) as exc:
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_PATH_INVALID",
                "Refresh the target endpoint path before applying directory metadata.",
            ) from exc
        require_endpoint_marker(
            resolved_root,
            permit,
            validation_code="DIRECTORY_METADATA_ENDPOINT_MARKER_MISMATCH",
            missing_code="DIRECTORY_METADATA_ENDPOINT_MARKER_MISSING",
        )
        current = resolved_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise DirectoryMetadataError(
                    "DIRECTORY_METADATA_REPARSE_UNSUPPORTED",
                    "Refresh analysis because directory metadata paths cannot cross links.",
                )
        if not current.is_dir():
            raise DirectoryMetadataError(
                "DIRECTORY_METADATA_TARGET_TYPE_CONFLICT",
                "Refresh analysis because the metadata target is not a directory.",
            )
        return current


def _desired_metadata(operation: DirectoryRecoveryOperation) -> str:
    if operation.desired_metadata_json is None:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_DESIRED_EVIDENCE_REQUIRED",
            "Record desired directory metadata before applying it.",
        )
    return operation.desired_metadata_json


def _observed_metadata(path: Path) -> str:
    try:
        modified_ns = path.stat(follow_symlinks=False).st_mtime_ns
    except OSError as exc:
        raise DirectoryMetadataError(
            "DIRECTORY_METADATA_READ_FAILED",
            "Retry after confirming the target directory remains reachable.",
        ) from exc
    return canonical_directory_metadata(modified_ns=modified_ns)
