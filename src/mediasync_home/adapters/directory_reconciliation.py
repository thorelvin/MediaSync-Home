from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointRootDescriptor,
    EndpointRootDescriptorResolver,
)
from mediasync_home.application.directory_artifacts import directory_artifact_matches
from mediasync_home.application.directory_metadata import (
    DirectoryMetadataCatalogStore,
    DirectoryMetadataError,
    directory_metadata_catalog_record,
    parse_directory_metadata,
)
from mediasync_home.application.directory_reconciliation import (
    DirectoryRecoveryEvidenceState,
    DirectoryRecoveryObservation,
    DirectoryRecoveryObservationPort,
)
from mediasync_home.application.directory_recovery import (
    DirectoryRecoveryKind,
    DirectoryRecoveryOperation,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
)
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)
from mediasync_home.application.version_objects import (
    EMPTY_DIRECTORY_QUARANTINE_ROLE,
    VersionObjectManifestError,
    parse_version_object_manifest,
    require_version_object_manifest_binding,
)


_OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class RecoveryOperationLookup(Protocol):
    def load_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> RecoveryOperation | None: ...


class LocalDirectoryRecoveryObservationAdapter(DirectoryRecoveryObservationPort):
    def __init__(
        self,
        *,
        root_resolver: EndpointRootDescriptorResolver,
        recovery_operations: RecoveryOperationLookup,
        metadata_catalog: DirectoryMetadataCatalogStore,
    ) -> None:
        self._root_resolver = root_resolver
        self._recovery_operations = recovery_operations
        self._metadata_catalog = metadata_catalog

    def observe_directory_recovery(
        self,
        operation: DirectoryRecoveryOperation,
    ) -> DirectoryRecoveryObservation:
        generic = self._recovery_operations.load_operation(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
        )
        catalog_terminal = self._catalog_terminal(operation, generic)
        root_result = self._validated_root(operation)
        if isinstance(root_result, DirectoryRecoveryObservation):
            return replace(
                root_result,
                catalog_terminal_recorded=catalog_terminal,
            )
        root, final_path = root_result
        if operation.kind is DirectoryRecoveryKind.CREATE:
            observed = self._observe_create(operation, final_path)
        elif operation.kind is DirectoryRecoveryKind.METADATA:
            observed = self._observe_metadata(operation, final_path)
        elif operation.kind is DirectoryRecoveryKind.QUARANTINE:
            observed = self._observe_quarantine(
                operation=operation,
                generic=generic,
                root=root,
                final_path=final_path,
            )
        else:
            observed = self._observe_restore(
                operation=operation,
                generic=generic,
                root=root,
                final_path=final_path,
            )
        return replace(
            observed,
            catalog_terminal_recorded=catalog_terminal,
        )

    def _validated_root(
        self,
        operation: DirectoryRecoveryOperation,
    ) -> tuple[Path, Path] | DirectoryRecoveryObservation:
        try:
            descriptor = self._root_resolver.resolve_endpoint_root_descriptor(
                resource_key=f"endpoint:{operation.target_endpoint_id}",
                endpoint_id=operation.target_endpoint_id,
                endpoint_revision_id=operation.target_endpoint_revision_id,
            )
        except EndpointLeaseUnavailable:
            return _unavailable("DIRECTORY_RECOVERY_ENDPOINT_ROOT_UNAVAILABLE")
        if descriptor is None:
            return _unavailable("DIRECTORY_RECOVERY_ENDPOINT_ROOT_UNKNOWN")
        try:
            root = Path(descriptor.root).resolve(strict=True)
        except OSError:
            return _unavailable("DIRECTORY_RECOVERY_ENDPOINT_ROOT_UNAVAILABLE")
        if root.is_symlink() or not root.is_dir():
            return _conflict("DIRECTORY_RECOVERY_ENDPOINT_ROOT_CONFLICT")
        marker_conflict = _validate_endpoint_binding(
            operation=operation,
            descriptor=descriptor,
            root=root,
        )
        if marker_conflict is not None:
            return marker_conflict
        try:
            relative = parse_endpoint_relative_path(operation.final_relative_path)
        except SafePathViolation:
            return _conflict("DIRECTORY_RECOVERY_FINAL_PATH_INVALID")
        current = root
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                return _conflict("DIRECTORY_RECOVERY_FINAL_PATH_REPARSE_CONFLICT")
            if current.exists() and not current.is_dir():
                return _conflict("DIRECTORY_RECOVERY_FINAL_PARENT_TYPE_CONFLICT")
        final_path = root.joinpath(*relative.parts)
        if final_path.is_symlink():
            return _conflict("DIRECTORY_RECOVERY_FINAL_PATH_REPARSE_CONFLICT")
        return root, final_path

    def _catalog_terminal(
        self,
        operation: DirectoryRecoveryOperation,
        generic: RecoveryOperation | None,
    ) -> bool:
        if operation.kind is DirectoryRecoveryKind.METADATA:
            desired = operation.desired_metadata_json
            if desired is None:
                return False
            expected = directory_metadata_catalog_record(operation, desired)
            return (
                self._metadata_catalog.load_directory_metadata(operation.recovery_id)
                == expected
            )
        if generic is None:
            return False
        if operation.kind is DirectoryRecoveryKind.RESTORE:
            return generic.phase is RecoveryOperationPhase.CANCELLED
        return generic.phase in {
            RecoveryOperationPhase.CATALOG_RECORDED,
            RecoveryOperationPhase.CLEANED,
        }

    @staticmethod
    def _observe_create(
        operation: DirectoryRecoveryOperation,
        final_path: Path,
    ) -> DirectoryRecoveryObservation:
        if not final_path.exists():
            return _not_applied("DIRECTORY_CREATE_TARGET_ABSENT")
        if directory_artifact_matches(
            final_path,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_id=operation.operation_id,
            final_relative_path=operation.final_relative_path,
        ):
            return _applied("DIRECTORY_CREATE_IDENTITY_RECONCILED")
        return _conflict("DIRECTORY_CREATE_TARGET_IDENTITY_CONFLICT")

    @staticmethod
    def _observe_metadata(
        operation: DirectoryRecoveryOperation,
        final_path: Path,
    ) -> DirectoryRecoveryObservation:
        if not final_path.is_dir():
            return _conflict("DIRECTORY_METADATA_TARGET_TYPE_CONFLICT")
        desired = operation.desired_metadata_json
        if desired is None:
            return _conflict("DIRECTORY_METADATA_DESIRED_EVIDENCE_REQUIRED")
        try:
            modified_ns = parse_directory_metadata(desired)
            observed_ns = final_path.stat(follow_symlinks=False).st_mtime_ns
        except (OSError, DirectoryMetadataError):
            return _unavailable("DIRECTORY_METADATA_EVIDENCE_UNAVAILABLE")
        if observed_ns == modified_ns:
            return _applied("DIRECTORY_METADATA_APPLY_RECONCILED")
        return _not_applied("DIRECTORY_METADATA_RETRY_REQUIRED")

    def _observe_quarantine(
        self,
        *,
        operation: DirectoryRecoveryOperation,
        generic: RecoveryOperation | None,
        root: Path,
        final_path: Path,
    ) -> DirectoryRecoveryObservation:
        evidence = _quarantine_evidence(
            operation=operation,
            generic=generic,
            root=root,
        )
        if isinstance(evidence, DirectoryRecoveryObservation):
            if (
                evidence.evidence_state
                is DirectoryRecoveryEvidenceState.NOT_APPLIED
                and final_path.is_dir()
                and _is_empty_directory(final_path)
            ):
                return evidence
            if evidence.evidence_state is DirectoryRecoveryEvidenceState.NOT_APPLIED:
                return _conflict("DIRECTORY_QUARANTINE_SOURCE_STATE_CONFLICT")
            return evidence
        object_id = evidence
        if final_path.is_dir():
            return _conflict("DIRECTORY_QUARANTINE_SOURCE_STILL_PRESENT")
        if final_path.exists() and not final_path.is_file():
            return _conflict("DIRECTORY_QUARANTINE_REPLACEMENT_TYPE_CONFLICT")
        return _applied(
            "DIRECTORY_QUARANTINE_OBJECT_RECONCILED",
            managed_object_id=object_id,
        )

    def _observe_restore(
        self,
        *,
        operation: DirectoryRecoveryOperation,
        generic: RecoveryOperation | None,
        root: Path,
        final_path: Path,
    ) -> DirectoryRecoveryObservation:
        evidence = _quarantine_evidence(
            operation=operation,
            generic=generic,
            root=root,
        )
        if isinstance(evidence, DirectoryRecoveryObservation):
            return (
                _conflict("DIRECTORY_RESTORE_QUARANTINE_EVIDENCE_MISSING")
                if evidence.evidence_state
                is DirectoryRecoveryEvidenceState.NOT_APPLIED
                else evidence
            )
        if not final_path.exists():
            return _not_applied("DIRECTORY_RESTORE_TARGET_ABSENT")
        if final_path.is_dir() and _is_empty_directory(final_path):
            return _applied(
                "DIRECTORY_RESTORE_POSTCONDITION_RECONCILED",
                managed_object_id=evidence,
            )
        return _conflict("DIRECTORY_RESTORE_TARGET_CONFLICT")


def _validate_endpoint_binding(
    *,
    operation: DirectoryRecoveryOperation,
    descriptor: EndpointRootDescriptor,
    root: Path,
) -> DirectoryRecoveryObservation | None:
    try:
        marker = json.loads(
            (root / ".mediasync" / "endpoint.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return _conflict("DIRECTORY_RECOVERY_ENDPOINT_MARKER_CONFLICT")
    if not isinstance(marker, dict):
        return _conflict("DIRECTORY_RECOVERY_ENDPOINT_MARKER_CONFLICT")
    expected_pairs = (
        (marker.get("endpoint_id"), operation.target_endpoint_id),
        (marker.get("owner_installation_id"), operation.owner_installation_id),
        (marker.get("ownership_epoch"), operation.ownership_epoch),
        (marker.get("control_area_id"), descriptor.control_area_id),
        (marker.get("root_identity_hash"), descriptor.root_identity_hash),
        (marker.get("marker_checksum"), descriptor.marker_checksum),
    )
    if any(expected is not None and observed != expected for observed, expected in expected_pairs):
        return _conflict("DIRECTORY_RECOVERY_ENDPOINT_BINDING_CONFLICT")
    return None


def _quarantine_evidence(
    *,
    operation: DirectoryRecoveryOperation,
    generic: RecoveryOperation | None,
    root: Path,
) -> str | DirectoryRecoveryObservation:
    object_id = operation.managed_object_id or operation.operation_id
    if _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
        return _conflict("DIRECTORY_QUARANTINE_OBJECT_ID_INVALID")
    object_root = root / ".mediasync" / "objects" / "quarantine"
    if object_root.is_symlink():
        return _conflict("DIRECTORY_QUARANTINE_OBJECT_ROOT_REPARSE_CONFLICT")
    payload = object_root / f"{object_id}.payload"
    manifest_path = object_root / f"{object_id}.manifest.json"
    if payload.is_symlink() or manifest_path.is_symlink():
        return _conflict("DIRECTORY_QUARANTINE_OBJECT_REPARSE_CONFLICT")
    payload_exists = payload.exists()
    manifest_exists = manifest_path.exists()
    if not payload_exists and not manifest_exists:
        return _not_applied("DIRECTORY_QUARANTINE_OBJECT_ABSENT")
    if not payload_exists or not manifest_exists or generic is None:
        return _conflict("DIRECTORY_QUARANTINE_OBJECT_INCOMPLETE")
    if not payload.is_dir() or not _is_empty_directory(payload):
        return _conflict("DIRECTORY_QUARANTINE_PAYLOAD_CONFLICT")
    try:
        manifest = parse_version_object_manifest(
            manifest_path.read_text(encoding="utf-8")
        )
        bound_generic = replace(generic, quarantine_object_id=object_id)
        require_version_object_manifest_binding(manifest, operation=bound_generic)
    except (OSError, VersionObjectManifestError):
        return _conflict("DIRECTORY_QUARANTINE_MANIFEST_CONFLICT")
    if (
        manifest.object_role != EMPTY_DIRECTORY_QUARANTINE_ROLE
        or manifest.fingerprint_json != operation.expected_precondition_json
    ):
        return _conflict("DIRECTORY_QUARANTINE_BINDING_CONFLICT")
    return object_id


def _is_empty_directory(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    except OSError:
        return False
    return False


def _not_applied(validation_code: str) -> DirectoryRecoveryObservation:
    return DirectoryRecoveryObservation(
        evidence_state=DirectoryRecoveryEvidenceState.NOT_APPLIED,
        validation_code=validation_code,
    )


def _applied(
    validation_code: str,
    *,
    managed_object_id: str | None = None,
) -> DirectoryRecoveryObservation:
    return DirectoryRecoveryObservation(
        evidence_state=DirectoryRecoveryEvidenceState.APPLIED,
        validation_code=validation_code,
        managed_object_id=managed_object_id,
    )


def _unavailable(validation_code: str) -> DirectoryRecoveryObservation:
    return DirectoryRecoveryObservation(
        evidence_state=DirectoryRecoveryEvidenceState.UNAVAILABLE,
        validation_code=validation_code,
    )


def _conflict(validation_code: str) -> DirectoryRecoveryObservation:
    return DirectoryRecoveryObservation(
        evidence_state=DirectoryRecoveryEvidenceState.CONFLICT,
        validation_code=validation_code,
    )
