from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootResolver
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseGuardError
from mediasync_home.adapters.system_clock import SystemClock
from mediasync_home.adapters.windows_durability import move_path_write_through
from mediasync_home.application.clocks import ClockPort
from mediasync_home.application.ports import (
    CommitReceipt,
    FinalCommitPort,
    OldTargetPreservationReceipt,
    OldTargetRestoreReceipt,
    RecoveryObjectCleanupReceipt,
    RelativePath,
    VerifiedStagingArtifact,
)
from mediasync_home.application.directory_artifacts import (
    DIRECTORY_MARKER_NAME,
    directory_artifact_fingerprint,
    directory_artifact_matches,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
)
from mediasync_home.application.safe_paths import SafePathViolation, parse_endpoint_relative_path
from mediasync_home.application.staging_objects import (
    StagingObjectManifestError,
    parse_staging_object_manifest,
    require_staging_object_manifest_binding,
)
from mediasync_home.application.version_objects import (
    EMPTY_DIRECTORY_QUARANTINE_ROLE,
    VersionObjectManifest,
    VersionObjectManifestError,
    parse_version_object_manifest,
    quarantine_object_manifest_from_operation,
    require_version_object_manifest_binding,
    version_object_manifest_from_operation,
)
from mediasync_home.domain.capabilities import MutationPermit


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
LAB_MARKER_NAME = ".mediasync_test_root"
DEFAULT_REPARSE_GUARD = LocalReparseGuard()


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
        return _flushed_file_commit_receipt(
            artifact=artifact,
            final_path=final_path,
            write_through_move_used=True,
        )


class LocalVersionedReplaceFinalCommitAdapter(FinalCommitPort):
    def __init__(
        self,
        *,
        target_root: Path,
        staging_root: Path,
        permit_validator: MutationPermitValidator,
        clock: ClockPort | None = None,
    ) -> None:
        self._target_root = Path(target_root)
        self._staging_root = Path(staging_root)
        self._permit_validator = permit_validator
        self._clock = clock or SystemClock()

    def preserve_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetPreservationReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_marker(
            self._target_root,
            permit,
            validation_code="LOCAL_REPLACE_FINAL_COMMIT_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_REPLACE_FINAL_COMMIT_ENDPOINT_MARKER_MISSING",
        )
        _validate_replace_operation_binding(operation=operation, permit=permit)
        if operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
            return self._preserve_empty_directory_target(operation)
        if operation.target_precondition_kind is not RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_MATCH_FINGERPRINT_PRECONDITION",
                "Use versioned replacement only for operations with a matching target fingerprint.",
            )
        if operation.version_object_id not in (None, operation.operation_id):
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_DETERMINISTIC_VERSION_OBJECT_ID",
                "Use the operation id as the local version object id for this preview adapter.",
            )

        expected = _expected_fingerprint(
            operation.expected_target_fingerprint_json,
            validation_code="LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_EXPECTED_TARGET_FINGERPRINT",
            next_action="Refresh analysis before replacing an existing target.",
        )
        final_path = _replace_final_path(self._target_root, operation.final_relative_path)
        if not final_path.is_file() or final_path.is_symlink():
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_TARGET_MISSING",
                "Refresh analysis because the target to replace is no longer a regular file.",
            )
        observed = _fingerprint_file(final_path)
        if observed != expected:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_TARGET_FINGERPRINT_MISMATCH",
                "Refresh analysis because the target changed before replacement.",
            )

        version_object_id = operation.operation_id
        version_payload, version_manifest = _version_object_paths(
            target_root=self._target_root,
            version_object_id=version_object_id,
        )
        if version_manifest.exists():
            manifest = _load_version_manifest(
                manifest_path=version_manifest,
                operation=operation,
            )
            manifest_fingerprint = _version_manifest_fingerprint(manifest)
            if manifest_fingerprint != expected:
                raise FinalCommitAdapterError(
                    "LOCAL_REPLACE_FINAL_COMMIT_VERSION_MANIFEST_MISMATCH",
                    "Enter recovery because the preserved version manifest differs from the target.",
                )
            _require_version_payload_matches_manifest(
                version_payload=version_payload,
                fingerprint=manifest_fingerprint,
            )
            return _version_preservation_receipt(operation=operation, manifest=manifest)
        _preserve_version_payload(
            source=final_path,
            destination=version_payload,
            expected_fingerprint=expected,
        )
        try:
            manifest = version_object_manifest_from_operation(
                operation,
                created_utc=self._clock.utc_now(),
            )
        except VersionObjectManifestError as exc:
            raise FinalCommitAdapterError(
                exc.validation_code,
                "Refresh the sealed job retention binding before preserving an old target.",
            ) from exc
        _write_version_manifest(
            manifest_path=version_manifest,
            canonical_manifest=manifest.canonical_json,
        )
        return _version_preservation_receipt(operation=operation, manifest=manifest)

    def _preserve_empty_directory_target(
        self,
        operation: RecoveryOperation,
    ) -> OldTargetPreservationReceipt:
        if operation.quarantine_object_id not in (None, operation.operation_id):
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_REQUIRES_DETERMINISTIC_OBJECT_ID",
                "Use the operation id as the local quarantine object id for this preview adapter.",
            )
        expected = _expected_empty_directory_precondition(
            operation.expected_target_fingerprint_json,
            validation_code="LOCAL_DIRECTORY_QUARANTINE_REQUIRES_EMPTY_DIRECTORY_EVIDENCE",
            next_action="Revalidate the empty-directory target precondition before quarantine.",
        )
        quarantine_object_id = operation.operation_id
        quarantine_payload, quarantine_manifest = _quarantine_object_paths(
            target_root=self._target_root,
            quarantine_object_id=quarantine_object_id,
        )
        final_path = _directory_empty_final_path(self._target_root, operation.final_relative_path)
        try:
            manifest = quarantine_object_manifest_from_operation(
                operation,
                created_utc=self._clock.utc_now(),
            )
        except VersionObjectManifestError as exc:
            raise FinalCommitAdapterError(
                exc.validation_code,
                "Refresh the sealed job retention binding before quarantining a directory.",
            ) from exc

        if quarantine_payload.exists():
            _validate_existing_quarantine_payload(
                payload_path=quarantine_payload,
                final_path=final_path,
            )
            _write_quarantine_manifest(manifest_path=quarantine_manifest, manifest=manifest)
            return OldTargetPreservationReceipt(
                operation_id=operation.operation_id,
                final_relative_path=RelativePath(operation.final_relative_path),
                quarantine_object_id=quarantine_object_id,
                fingerprint_json=_canonical_json(expected),
                version_created_utc=manifest.created_utc,
                version_retention_until_utc=manifest.retention_until_utc,
                version_manifest_hash=manifest.manifest_hash,
            )

        if final_path.is_symlink() or not final_path.is_dir():
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_TARGET_TYPE_CHANGED",
                "Refresh analysis because the target is no longer an empty directory.",
            )
        _require_empty_directory(final_path)
        try:
            os.replace(final_path, quarantine_payload)
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_MOVE_FAILED",
                "Enter recovery and inspect final path plus quarantine object postconditions.",
            ) from exc
        _write_quarantine_manifest(manifest_path=quarantine_manifest, manifest=manifest)
        return OldTargetPreservationReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            quarantine_object_id=quarantine_object_id,
            fingerprint_json=_canonical_json(expected),
            version_created_utc=manifest.created_utc,
            version_retention_until_utc=manifest.retention_until_utc,
            version_manifest_hash=manifest.manifest_hash,
        )

    def restore_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetRestoreReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_marker(
            self._target_root,
            permit,
            validation_code="LOCAL_REPLACE_OLD_TARGET_RESTORE_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_REPLACE_OLD_TARGET_RESTORE_ENDPOINT_MARKER_MISSING",
        )
        _validate_replace_operation_binding(operation=operation, permit=permit)
        if operation.phase is not RecoveryOperationPhase.OLD_TARGET_PRESERVED:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_REQUIRES_PRESERVED_PHASE",
                "Restore old target bytes only after the old target has been preserved.",
            )
        if operation.target_precondition_kind is RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
            return self._restore_empty_directory_target(operation)
        if operation.target_precondition_kind is not RecoveryTargetPreconditionKind.MATCH_FINGERPRINT:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_REQUIRES_MATCH_FINGERPRINT_PRECONDITION",
                "Restore old target bytes only for versioned replacements.",
            )
        if operation.version_object_id is None or not operation.version_object_id.strip():
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_REQUIRES_VERSION_OBJECT",
                "Recover or reconcile the preserved old target version object before restore.",
            )

        version_payload, version_manifest = _version_object_paths(
            target_root=self._target_root,
            version_object_id=operation.version_object_id,
            create=False,
        )
        manifest = _load_version_manifest(
            manifest_path=version_manifest,
            operation=operation,
        )
        expected_old = _version_manifest_fingerprint(manifest)
        if not version_payload.is_file() or version_payload.is_symlink():
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_VERSION_PAYLOAD_MISSING",
                "Recover or reconcile the preserved old target payload before restore.",
            )
        if _fingerprint_file(version_payload) != expected_old:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_VERSION_PAYLOAD_MISMATCH",
                "Enter recovery because the preserved old target no longer matches its manifest.",
            )

        final_path = _replace_final_path(self._target_root, operation.final_relative_path)
        if final_path.is_symlink() or (final_path.exists() and not final_path.is_file()):
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_TARGET_TYPE_CHANGED",
                "Inspect the final target path before restoring old target bytes.",
            )
        fingerprint_json = _canonical_json(expected_old)
        if final_path.is_file():
            if _fingerprint_file(final_path) == expected_old:
                return OldTargetRestoreReceipt(
                    operation_id=operation.operation_id,
                    final_relative_path=RelativePath(operation.final_relative_path),
                    fingerprint_json=fingerprint_json,
                )
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_TARGET_EXISTS",
                "Do not restore over an existing final file; inspect preserved and final bytes manually.",
            )

        _restore_version_payload_without_overwrite(
            version_payload=version_payload,
            final_path=final_path,
            expected_fingerprint=expected_old,
        )
        return OldTargetRestoreReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            fingerprint_json=fingerprint_json,
        )

    def _restore_empty_directory_target(
        self,
        operation: RecoveryOperation,
    ) -> OldTargetRestoreReceipt:
        if operation.quarantine_object_id is None or not operation.quarantine_object_id.strip():
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_RESTORE_REQUIRES_QUARANTINE_OBJECT",
                "Recover or reconcile the quarantined empty directory before restore.",
            )

        quarantine_payload, quarantine_manifest = _quarantine_object_paths(
            target_root=self._target_root,
            quarantine_object_id=operation.quarantine_object_id,
            create=False,
        )
        manifest = _load_quarantine_manifest(
            manifest_path=quarantine_manifest,
            expected_operation_id=operation.operation_id,
            expected_quarantine_object_id=operation.quarantine_object_id,
            expected_final_relative_path=operation.final_relative_path,
        )
        expected = _manifest_empty_directory_precondition(
            manifest,
            validation_code="LOCAL_DIRECTORY_QUARANTINE_RESTORE_MANIFEST_INVALID",
            next_action="Reload recovery state before restoring the quarantined directory.",
        )
        if operation.expected_target_fingerprint_json is not None:
            bound_expected = _expected_empty_directory_precondition(
                operation.expected_target_fingerprint_json,
                validation_code="LOCAL_DIRECTORY_QUARANTINE_RESTORE_EVIDENCE_MISMATCH",
                next_action="Reload recovery state before restoring the quarantined directory.",
            )
            if bound_expected != expected:
                raise FinalCommitAdapterError(
                    "LOCAL_DIRECTORY_QUARANTINE_RESTORE_EVIDENCE_MISMATCH",
                    "Reload recovery state before restoring the quarantined directory.",
                )
        if manifest.get("payload_name") != quarantine_payload.name:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_RESTORE_MANIFEST_MISMATCH",
                "Reload recovery state before restoring the quarantined directory.",
            )
        if quarantine_payload.is_symlink() or not quarantine_payload.is_dir():
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_RESTORE_PAYLOAD_INVALID",
                "Enter recovery because the quarantine object path is not an empty directory.",
            )
        _require_empty_directory(
            quarantine_payload,
            read_failed_code="LOCAL_DIRECTORY_QUARANTINE_RESTORE_PAYLOAD_READ_FAILED",
            not_empty_code="LOCAL_DIRECTORY_QUARANTINE_RESTORE_PAYLOAD_NOT_EMPTY",
            next_action="Enter recovery because the quarantine object contents changed.",
        )

        final_path = _directory_empty_final_path(self._target_root, operation.final_relative_path)
        fingerprint_json = _canonical_json(expected)
        if final_path.exists() or final_path.is_symlink():
            if final_path.is_symlink() or not final_path.is_dir():
                raise FinalCommitAdapterError(
                    "LOCAL_DIRECTORY_QUARANTINE_RESTORE_TARGET_TYPE_CHANGED",
                    "Inspect the final target path before restoring the empty directory.",
                )
            _require_empty_directory(
                final_path,
                read_failed_code="LOCAL_DIRECTORY_QUARANTINE_RESTORE_TARGET_READ_FAILED",
                not_empty_code="LOCAL_DIRECTORY_QUARANTINE_RESTORE_TARGET_NOT_EMPTY",
                next_action="Do not restore over an existing final directory with contents.",
            )
            return OldTargetRestoreReceipt(
                operation_id=operation.operation_id,
                final_relative_path=RelativePath(operation.final_relative_path),
                fingerprint_json=fingerprint_json,
            )

        try:
            final_path.mkdir()
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_RESTORE_TARGET_REAPPEARED",
                "Reload recovery state because the final target reappeared during restore.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_RESTORE_CREATE_FAILED",
                "Enter recovery and inspect the final path before retrying directory restore.",
            ) from exc
        return OldTargetRestoreReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            fingerprint_json=fingerprint_json,
        )

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_marker(
            self._target_root,
            permit,
            validation_code="LOCAL_REPLACE_FINAL_COMMIT_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_REPLACE_FINAL_COMMIT_ENDPOINT_MARKER_MISSING",
        )
        staging_payload = _replace_staging_payload_path(self._staging_root, artifact)
        final_path = _replace_final_path(self._target_root, artifact.relative_path.value)
        version_payload, version_manifest = _version_object_paths(
            target_root=self._target_root,
            version_object_id=artifact.object_id,
            create=False,
        )
        manifest = _load_version_manifest(
            manifest_path=version_manifest,
            expected_operation_id=artifact.object_id,
            expected_final_relative_path=artifact.relative_path.value,
            permit=permit,
        )
        expected_old = _version_manifest_fingerprint(manifest)
        if not version_payload.is_file() or version_payload.is_symlink():
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_PAYLOAD_MISSING",
                "Preserve the old target before replacing the final file.",
            )
        if _fingerprint_file(version_payload) != expected_old:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_PAYLOAD_MISMATCH",
                "Enter recovery because the preserved old target no longer matches its manifest.",
            )
        if _hash_file(staging_payload) != artifact.content_hash:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_STAGING_HASH_MISMATCH",
                "Restage and verify the artifact before attempting replacement.",
            )
        if final_path.is_symlink() or (final_path.exists() and not final_path.is_file()):
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_TARGET_TYPE_CHANGED_AFTER_PRESERVE",
                "Enter recovery because the final target path is no longer a regular file or absent.",
            )
        if final_path.is_file():
            if _fingerprint_file(final_path) != expected_old:
                raise FinalCommitAdapterError(
                    "LOCAL_REPLACE_FINAL_COMMIT_TARGET_CHANGED_AFTER_PRESERVE",
                    "Refresh analysis because the final target changed after old-target preservation.",
                )
            _replace_with_verified_payload(
                staging_payload=staging_payload,
                final_path=final_path,
                content_hash=artifact.content_hash,
            )
            return _flushed_file_commit_receipt(
                artifact=artifact,
                final_path=final_path,
                write_through_move_used=True,
            )
        _insert_replacement_payload_without_overwrite(
            staging_payload=staging_payload,
            final_path=final_path,
            content_hash=artifact.content_hash,
        )
        return _flushed_file_commit_receipt(
            artifact=artifact,
            final_path=final_path,
            write_through_move_used=True,
        )

    def cleanup_recovery_objects(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> RecoveryObjectCleanupReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_marker(
            self._target_root,
            permit,
            validation_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_ENDPOINT_MARKER_MISSING",
        )
        _validate_replace_operation_binding(operation=operation, permit=permit)
        if operation.phase is not RecoveryOperationPhase.CATALOG_RECORDED:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_REQUIRES_CATALOG_RECORDED",
                "Clean recovery objects only after catalog handoff is recorded.",
            )
        if operation.target_precondition_kind is not RecoveryTargetPreconditionKind.DIRECTORY_EMPTY:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_REQUIRES_DIRECTORY_EMPTY",
                "Use quarantine cleanup only for directory-empty target preconditions.",
            )
        if operation.quarantine_object_id is None or not operation.quarantine_object_id.strip():
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_REQUIRES_QUARANTINE_OBJECT",
                "Recover or reconcile the quarantine object id before cleanup.",
            )

        _cleanup_empty_directory_quarantine_object(
            target_root=self._target_root,
            operation=operation,
            quarantine_object_id=operation.quarantine_object_id,
        )
        return RecoveryObjectCleanupReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            cleaned_object_ids=(operation.quarantine_object_id,),
        )


def _insert_replacement_payload_without_overwrite(
    *,
    staging_payload: Path,
    final_path: Path,
    content_hash: str,
) -> None:
    temp_path = final_path.parent / f".{final_path.name}.{uuid4().hex}.mediasync-replace-missing.tmp"
    try:
        _copy_file_durable(source=staging_payload, destination=temp_path)
        if _hash_file(temp_path) != content_hash:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_TEMP_HASH_MISMATCH",
                "Restage and verify the artifact before attempting replacement.",
            )
        try:
            move_path_write_through(
                temp_path,
                final_path,
                replace_existing=False,
            )
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_TARGET_REAPPEARED",
                "Reload recovery state because the final target reappeared during replacement.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_WRITE_THROUGH_MOVE_FAILED",
                "Keep recovery state and re-probe target write-through move support.",
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _restore_version_payload_without_overwrite(
    *,
    version_payload: Path,
    final_path: Path,
    expected_fingerprint: dict[str, object],
) -> None:
    temp_path = final_path.parent / f".{final_path.name}.{uuid4().hex}.mediasync-restore-old.tmp"
    try:
        _copy_file_durable(source=version_payload, destination=temp_path)
        if _fingerprint_file(temp_path) != expected_fingerprint:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_TEMP_MISMATCH",
                "Recover or reconcile the preserved old target payload before restore.",
            )
        try:
            move_path_write_through(
                temp_path,
                final_path,
                replace_existing=False,
            )
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_TARGET_REAPPEARED",
                "Reload recovery state because the final target reappeared during restore.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_OLD_TARGET_RESTORE_WRITE_THROUGH_MOVE_FAILED",
                "Keep recovery state and re-probe target write-through move support.",
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


class LocalResolvingFinalCommitAdapter(FinalCommitPort):
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        permit_validator: MutationPermitValidator,
        staging_root: Path | None = None,
        clock: ClockPort | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._permit_validator = permit_validator
        self._staging_root = None if staging_root is None else Path(staging_root)
        self._clock = clock or SystemClock()

    def preserve_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetPreservationReceipt:
        target_root = self._target_root(
            permit=permit,
            endpoint_id=operation.target_endpoint_id,
            endpoint_revision_id=operation.target_endpoint_revision_id,
        )
        staging_fingerprint = _expected_fingerprint(
            operation.expected_staging_fingerprint_json,
            validation_code="LOCAL_FINAL_COMMIT_STAGING_FINGERPRINT_MISSING",
            next_action="Restage and verify the operation before preserving its target.",
        )
        _require_staging_manifest_binding(
            staging_root=self._staging_root_for(target_root),
            permit=permit,
            staging_object_id=_required_staging_object_id(operation),
            final_relative_path=operation.final_relative_path,
            operation_kind=operation.operation_kind,
            fingerprint_content_hash=str(staging_fingerprint["content_hash"]),
            fingerprint_byte_count=cast(int, staging_fingerprint["byte_count"]),
            operation_id=operation.operation_id,
        )
        return self._replace_adapter(target_root).preserve_old_target(permit, operation)

    def restore_old_target(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> OldTargetRestoreReceipt:
        target_root = self._target_root(
            permit=permit,
            endpoint_id=operation.target_endpoint_id,
            endpoint_revision_id=operation.target_endpoint_revision_id,
        )
        return self._replace_adapter(target_root).restore_old_target(permit, operation)

    def cleanup_recovery_objects(
        self,
        permit: MutationPermit,
        operation: RecoveryOperation,
    ) -> RecoveryObjectCleanupReceipt:
        target_root = self._target_root(
            permit=permit,
            endpoint_id=operation.target_endpoint_id,
            endpoint_revision_id=operation.target_endpoint_revision_id,
        )
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_marker(
            target_root,
            permit,
            validation_code="LOCAL_STAGING_CLEANUP_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_STAGING_CLEANUP_ENDPOINT_MARKER_MISSING",
        )
        _validate_replace_operation_binding(operation=operation, permit=permit)
        if operation.phase is not RecoveryOperationPhase.CATALOG_RECORDED:
            raise FinalCommitAdapterError(
                "LOCAL_STAGING_CLEANUP_REQUIRES_CATALOG_RECORDED",
                "Clean staging objects only after catalog handoff is recorded.",
            )
        cleaned_object_ids: list[str] = []
        if operation.staging_object_id is not None and operation.staging_object_id.strip():
            cleaned_object_ids.append(
                _cleanup_staging_object(
                    staging_root=self._staging_root_for(target_root),
                    permit=permit,
                    operation=operation,
                )
            )
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            final_receipt = self._cleanup_created_directory_marker(
                permit=permit,
                operation=operation,
                target_root=target_root,
            )
            cleaned_object_ids.extend(final_receipt.cleaned_object_ids)
        return RecoveryObjectCleanupReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            cleaned_object_ids=tuple(dict.fromkeys(cleaned_object_ids)),
        )

    def commit_verified_artifact(
        self,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
    ) -> CommitReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        target_root = self._target_root(
            permit=permit,
            endpoint_id=permit.endpoint_id,
            endpoint_revision_id=permit.endpoint_revision_id,
        )
        staging_root = self._staging_root_for(target_root)
        _require_staging_manifest_binding(
            staging_root=staging_root,
            permit=permit,
            staging_object_id=artifact.object_id,
            final_relative_path=artifact.relative_path.value,
            operation_kind=artifact.operation_kind,
            fingerprint_content_hash=artifact.content_hash,
        )
        if artifact.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            return self._commit_new_directory(
                permit=permit,
                artifact=artifact,
                target_root=target_root,
                staging_root=staging_root,
            )
        if _version_manifest_exists(target_root=target_root, object_id=artifact.object_id):
            return self._replace_adapter(target_root).commit_verified_artifact(permit, artifact)
        return self._commit_new_file(
            permit=permit,
            artifact=artifact,
            target_root=target_root,
            staging_root=staging_root,
        )

    def _commit_new_file(
        self,
        *,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
        target_root: Path,
        staging_root: Path,
    ) -> CommitReceipt:
        _require_endpoint_marker(
            target_root,
            permit,
            validation_code="LOCAL_FINAL_COMMIT_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_FINAL_COMMIT_ENDPOINT_MARKER_MISSING",
        )
        staging_payload = _local_staging_payload_path(staging_root, artifact)
        final_path = _local_final_path(target_root, artifact.relative_path.value)
        if final_path.exists() or final_path.is_symlink():
            raise FinalCommitAdapterError(
                "LOCAL_FINAL_COMMIT_TARGET_EXISTS",
                "Use the replace/version commit flow for existing targets.",
            )
        if _hash_file(staging_payload) != artifact.content_hash:
            raise FinalCommitAdapterError(
                "LOCAL_FINAL_COMMIT_STAGING_HASH_MISMATCH",
                "Restage and verify the artifact before final commit.",
            )
        _link_local_payload_without_overwrite(
            staging_payload=staging_payload,
            final_path=final_path,
            content_hash=artifact.content_hash,
        )
        return _flushed_file_commit_receipt(
            artifact=artifact,
            final_path=final_path,
            write_through_move_used=True,
        )

    def _commit_new_directory(
        self,
        *,
        permit: MutationPermit,
        artifact: VerifiedStagingArtifact,
        target_root: Path,
        staging_root: Path,
    ) -> CommitReceipt:
        _require_endpoint_marker(
            target_root,
            permit,
            validation_code="LOCAL_DIRECTORY_COMMIT_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_DIRECTORY_COMMIT_ENDPOINT_MARKER_MISSING",
        )
        expected = directory_artifact_fingerprint(
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path.value,
        )
        if expected["content_hash"] != artifact.content_hash:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_COMMIT_ARTIFACT_HASH_MISMATCH",
                "Reload the verified directory artifact before final commit.",
            )
        final_path = _local_final_path(target_root, artifact.relative_path.value)
        if final_path.exists() or final_path.is_symlink():
            if directory_artifact_matches(
                final_path,
                run_id=permit.run_id,
                run_target_id=permit.run_target_id,
                operation_id=artifact.object_id,
                final_relative_path=artifact.relative_path.value,
            ):
                return _flushed_directory_commit_receipt(
                    artifact=artifact,
                    final_path=final_path,
                    write_through_move_used=False,
                )
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_COMMIT_TARGET_EXISTS",
                "Refresh analysis because the planned directory path is no longer absent.",
            )
        staging_payload = _local_staging_directory_payload_path(staging_root, artifact)
        if not directory_artifact_matches(
            staging_payload,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=artifact.object_id,
            final_relative_path=artifact.relative_path.value,
        ):
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_COMMIT_STAGING_MARKER_MISMATCH",
                "Restage and verify the directory marker before final commit.",
            )
        try:
            move_path_write_through(
                staging_payload,
                final_path,
                replace_existing=False,
            )
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_COMMIT_TARGET_REAPPEARED",
                "Reload recovery state because the final directory appeared during commit.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_COMMIT_WRITE_THROUGH_MOVE_FAILED",
                "Keep recovery state and inspect the staged and final directory marker state.",
            ) from exc
        return _flushed_directory_commit_receipt(
            artifact=artifact,
            final_path=final_path,
            write_through_move_used=True,
        )

    def _cleanup_created_directory_marker(
        self,
        *,
        permit: MutationPermit,
        operation: RecoveryOperation,
        target_root: Path,
    ) -> RecoveryObjectCleanupReceipt:
        self._permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_marker(
            target_root,
            permit,
            validation_code="LOCAL_DIRECTORY_CLEANUP_ENDPOINT_MARKER_MISMATCH",
            missing_code="LOCAL_DIRECTORY_CLEANUP_ENDPOINT_MARKER_MISSING",
        )
        if operation.phase is not RecoveryOperationPhase.CATALOG_RECORDED:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_CLEANUP_REQUIRES_CATALOG_RECORDED",
                "Remove the directory recovery marker only after catalog handoff.",
            )
        final_path = _local_final_path(target_root, operation.final_relative_path)
        if not directory_artifact_matches(
            final_path,
            run_id=operation.run_id,
            run_target_id=operation.run_target_id,
            operation_id=operation.operation_id,
            final_relative_path=operation.final_relative_path,
        ):
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_CLEANUP_MARKER_MISMATCH",
                "Enter recovery because the created directory marker no longer matches.",
            )
        try:
            (final_path / DIRECTORY_MARKER_NAME).unlink()
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_CLEANUP_MARKER_REMOVE_FAILED",
                "Retry cleanup after confirming the created directory is writable.",
            ) from exc
        return RecoveryObjectCleanupReceipt(
            operation_id=operation.operation_id,
            final_relative_path=RelativePath(operation.final_relative_path),
            cleaned_object_ids=(operation.operation_id,),
        )

    def _replace_adapter(self, target_root: Path) -> LocalVersionedReplaceFinalCommitAdapter:
        return LocalVersionedReplaceFinalCommitAdapter(
            target_root=target_root,
            staging_root=self._staging_root_for(target_root),
            permit_validator=self._permit_validator,
            clock=self._clock,
        )

    def _staging_root_for(self, target_root: Path) -> Path:
        if self._staging_root is not None:
            return self._staging_root
        return target_root / ".mediasync" / "objects" / "staging"

    def _target_root(
        self,
        *,
        permit: MutationPermit,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path:
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=permit.resource_key,
                endpoint_id=endpoint_id,
                endpoint_revision_id=endpoint_revision_id,
            )
        except EndpointLeaseUnavailable:
            raise
        if root is None:
            raise FinalCommitAdapterError(
                "LOCAL_FINAL_COMMIT_ENDPOINT_ROOT_UNKNOWN",
                "Register endpoint roots before applying final filesystem changes.",
            )
        return _resolve_existing_root(
            root,
            missing_code="LOCAL_FINAL_COMMIT_TARGET_ROOT_MISSING",
            missing_next_action="Ensure the target endpoint root is reachable before final commit.",
            reparse_code="LOCAL_FINAL_COMMIT_TARGET_ROOT_REPARSE_UNSUPPORTED",
            reparse_next_action="Revalidate endpoint adoption before committing through this root.",
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


def _require_staging_manifest_binding(
    *,
    staging_root: Path,
    permit: MutationPermit,
    staging_object_id: str,
    final_relative_path: str,
    operation_kind: RecoveryOperationKind,
    fingerprint_content_hash: str,
    fingerprint_byte_count: int | None = None,
    operation_id: str | None = None,
) -> None:
    if OBJECT_ID_PATTERN.fullmatch(staging_object_id) is None:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_MANIFEST_OBJECT_ID_INVALID",
            "Restage the operation with a bounded opaque staging object id.",
        )
    root = _resolve_existing_root(
        staging_root,
        missing_code="LOCAL_FINAL_COMMIT_STAGING_ROOT_MISSING",
        missing_next_action="Restore the verified staging object before final commit.",
        reparse_code="LOCAL_FINAL_COMMIT_STAGING_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the staging object root before final commit.",
    )
    manifest_path = root / f"{staging_object_id}.manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_MANIFEST_MISSING",
            "Restage and verify the operation before final commit.",
        )
    try:
        manifest_raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_MANIFEST_UNREADABLE",
            "Retry after the staging manifest becomes readable.",
        ) from exc
    try:
        manifest = parse_staging_object_manifest(manifest_raw)
    except StagingObjectManifestError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_MANIFEST_INVALID",
            "Enter recovery because the staging manifest failed canonical validation.",
        ) from exc
    try:
        require_staging_object_manifest_binding(
            manifest,
            staging_object_id=staging_object_id,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            target_endpoint_id=permit.endpoint_id,
            target_endpoint_revision_id=permit.endpoint_revision_id,
            endpoint_generation=permit.endpoint_generation,
            final_relative_path=final_relative_path,
            operation_kind=operation_kind,
            fingerprint_content_hash=fingerprint_content_hash,
            fingerprint_byte_count=fingerprint_byte_count,
            operation_id=operation_id,
        )
    except StagingObjectManifestError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_MANIFEST_MISMATCH",
            "Enter recovery because the staging manifest is bound to different operation evidence.",
        ) from exc


def _required_staging_object_id(operation: RecoveryOperation) -> str:
    if operation.staging_object_id is None or not operation.staging_object_id.strip():
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_OBJECT_ID_MISSING",
            "Restage and verify the operation before preserving its target.",
        )
    return operation.staging_object_id


def _cleanup_staging_object(
    *,
    staging_root: Path,
    permit: MutationPermit,
    operation: RecoveryOperation,
) -> str:
    staging_object_id = _required_staging_object_id(operation)
    expected = _expected_fingerprint(
        operation.expected_staging_fingerprint_json,
        validation_code="LOCAL_STAGING_CLEANUP_FINGERPRINT_MISSING",
        next_action="Reload the catalog-recorded operation before staging cleanup.",
    )
    root = _resolve_existing_root(
        staging_root,
        missing_code="LOCAL_STAGING_CLEANUP_ROOT_MISSING",
        missing_next_action="Restore the managed staging root before cleanup.",
        reparse_code="LOCAL_STAGING_CLEANUP_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the managed staging root before cleanup.",
    )
    payload_path = root / f"{staging_object_id}.payload"
    manifest_path = root / f"{staging_object_id}.manifest.json"
    payload_exists = payload_path.exists() or payload_path.is_symlink()
    manifest_exists = manifest_path.exists() or manifest_path.is_symlink()
    if not payload_exists and not manifest_exists:
        return staging_object_id
    if not manifest_exists:
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_MANIFEST_MISSING",
            "Enter recovery because a staging payload exists without its manifest.",
        )
    _require_staging_manifest_binding(
        staging_root=root,
        permit=permit,
        staging_object_id=staging_object_id,
        final_relative_path=operation.final_relative_path,
        operation_kind=operation.operation_kind,
        fingerprint_content_hash=str(expected["content_hash"]),
        fingerprint_byte_count=cast(int, expected["byte_count"]),
        operation_id=operation.operation_id,
    )
    if payload_exists:
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            _cleanup_staging_directory_payload(
                payload_path=payload_path,
                operation=operation,
            )
        else:
            _cleanup_staging_file_payload(
                payload_path=payload_path,
                expected=expected,
            )
    try:
        manifest_path.unlink()
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_MANIFEST_REMOVE_FAILED",
            "Retry cleanup after the staging manifest becomes removable.",
        ) from exc
    return staging_object_id


def _cleanup_staging_file_payload(
    *,
    payload_path: Path,
    expected: dict[str, object],
) -> None:
    if payload_path.is_symlink() or not payload_path.is_file():
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_PAYLOAD_INVALID",
            "Enter recovery because the staging payload is not a regular file.",
        )
    try:
        observed = _fingerprint_file(payload_path)
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_PAYLOAD_READ_FAILED",
            "Retry cleanup after the staging payload becomes readable.",
        ) from exc
    if observed != expected:
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_PAYLOAD_MISMATCH",
            "Enter recovery because the staging payload changed after verification.",
        )
    try:
        payload_path.unlink()
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_PAYLOAD_REMOVE_FAILED",
            "Retry cleanup after the staging payload becomes removable.",
        ) from exc


def _cleanup_staging_directory_payload(
    *,
    payload_path: Path,
    operation: RecoveryOperation,
) -> None:
    marker_matches = directory_artifact_matches(
        payload_path,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        operation_id=operation.operation_id,
        final_relative_path=operation.final_relative_path,
    )
    if not marker_matches:
        if payload_path.is_symlink() or not payload_path.is_dir():
            raise FinalCommitAdapterError(
                "LOCAL_STAGING_CLEANUP_DIRECTORY_PAYLOAD_MISMATCH",
                "Enter recovery because the staged directory payload changed after verification.",
            )
        try:
            entries = tuple(payload_path.iterdir())
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_STAGING_CLEANUP_DIRECTORY_PAYLOAD_READ_FAILED",
                "Retry cleanup after the staged directory payload becomes readable.",
            ) from exc
        if entries:
            raise FinalCommitAdapterError(
                "LOCAL_STAGING_CLEANUP_DIRECTORY_PAYLOAD_MISMATCH",
                "Enter recovery because the staged directory marker changed after verification.",
            )
    try:
        if marker_matches:
            (payload_path / DIRECTORY_MARKER_NAME).unlink()
        payload_path.rmdir()
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_STAGING_CLEANUP_DIRECTORY_PAYLOAD_REMOVE_FAILED",
            "Retry cleanup after the staged directory marker becomes removable.",
        ) from exc


def _require_endpoint_marker(
    target_root: Path,
    permit: MutationPermit,
    *,
    validation_code: str,
    missing_code: str,
) -> None:
    marker_path = target_root / ".mediasync" / "endpoint.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalCommitAdapterError(
            missing_code,
            "Adopt the target endpoint and write a valid endpoint marker before mutating final paths.",
        ) from exc
    if not isinstance(marker, dict):
        raise FinalCommitAdapterError(
            validation_code,
            "Adopt the target endpoint and write a valid endpoint marker before mutating final paths.",
        )
    if (
        marker.get("endpoint_id") != permit.endpoint_id
        or marker.get("owner_installation_id") != permit.owner_installation_id
        or marker.get("ownership_epoch") != permit.ownership_epoch
    ):
        raise FinalCommitAdapterError(
            validation_code,
            "Reacquire the endpoint lease for the currently adopted target endpoint.",
        )


def _resolve_existing_root(
    root: Path,
    *,
    missing_code: str,
    missing_next_action: str,
    reparse_code: str,
    reparse_next_action: str,
) -> Path:
    try:
        return DEFAULT_REPARSE_GUARD.resolve_existing_root(
            root,
            missing_code=missing_code,
            missing_next_action=missing_next_action,
            reparse_code=reparse_code,
            reparse_next_action=reparse_next_action,
        )
    except ReparseGuardError as exc:
        raise FinalCommitAdapterError(exc.validation_code, exc.next_action) from exc


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
    root = _resolve_existing_root(
        staging_root,
        missing_code="LAB_FINAL_COMMIT_STAGING_ROOT_MISSING",
        missing_next_action="Create the staging root before final commit.",
        reparse_code="LAB_FINAL_COMMIT_STAGING_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the staging root before final commit.",
    )
    payload = root / f"{artifact.object_id}.payload"
    if not payload.is_file() or payload.is_symlink():
        raise FinalCommitAdapterError(
            "LAB_FINAL_COMMIT_STAGING_PAYLOAD_MISSING",
            "Restage and verify the artifact before final commit.",
        )
    return payload


def _replace_staging_payload_path(
    staging_root: Path,
    artifact: VerifiedStagingArtifact,
) -> Path:
    if OBJECT_ID_PATTERN.fullmatch(artifact.object_id) is None:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_SAFE_OBJECT_ID",
            "Restage the artifact with an opaque object id before final commit.",
        )
    if HASH_PATTERN.fullmatch(artifact.content_hash) is None:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_CONTENT_HASH",
            "Verify the staging artifact and provide a lowercase SHA-256 content hash.",
        )
    root = _resolve_existing_root(
        staging_root,
        missing_code="LOCAL_REPLACE_FINAL_COMMIT_STAGING_ROOT_MISSING",
        missing_next_action="Create the staging root before final commit.",
        reparse_code="LOCAL_REPLACE_FINAL_COMMIT_STAGING_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the staging root before replacement.",
    )
    payload = root / f"{artifact.object_id}.payload"
    if not payload.is_file() or payload.is_symlink():
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_STAGING_PAYLOAD_MISSING",
            "Restage and verify the artifact before final commit.",
        )
    return payload


def _local_staging_payload_path(staging_root: Path, artifact: VerifiedStagingArtifact) -> Path:
    if OBJECT_ID_PATTERN.fullmatch(artifact.object_id) is None:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_REQUIRES_SAFE_OBJECT_ID",
            "Restage the artifact with an opaque object id before final commit.",
        )
    if HASH_PATTERN.fullmatch(artifact.content_hash) is None:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_REQUIRES_CONTENT_HASH",
            "Verify the staging artifact and provide a lowercase SHA-256 content hash.",
        )
    root = _resolve_existing_root(
        staging_root,
        missing_code="LOCAL_FINAL_COMMIT_STAGING_ROOT_MISSING",
        missing_next_action="Create the staging root before final commit.",
        reparse_code="LOCAL_FINAL_COMMIT_STAGING_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the staging root before final commit.",
    )
    payload = root / f"{artifact.object_id}.payload"
    if not payload.is_file() or payload.is_symlink():
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_STAGING_PAYLOAD_MISSING",
            "Restage and verify the artifact before final commit.",
        )
    return payload


def _local_staging_directory_payload_path(
    staging_root: Path,
    artifact: VerifiedStagingArtifact,
) -> Path:
    if OBJECT_ID_PATTERN.fullmatch(artifact.object_id) is None:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_COMMIT_REQUIRES_SAFE_OBJECT_ID",
            "Restage the directory with an opaque object id before final commit.",
        )
    root = _resolve_existing_root(
        staging_root,
        missing_code="LOCAL_DIRECTORY_COMMIT_STAGING_ROOT_MISSING",
        missing_next_action="Create the staging root before directory commit.",
        reparse_code="LOCAL_DIRECTORY_COMMIT_STAGING_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the staging root before directory commit.",
    )
    payload = root / f"{artifact.object_id}.payload"
    if payload.is_symlink() or not payload.is_dir():
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_COMMIT_STAGING_PAYLOAD_MISSING",
            "Restage and verify the directory before final commit.",
        )
    return payload


def _final_path(target_root: Path, artifact: VerifiedStagingArtifact) -> Path:
    parts = _relative_path_parts(artifact.relative_path.value)
    root = _resolve_existing_root(
        target_root,
        missing_code="LAB_FINAL_COMMIT_TARGET_ROOT_MISSING",
        missing_next_action="Create the marked lab target root before final commit.",
        reparse_code="LAB_FINAL_COMMIT_TARGET_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the lab target root before final commit.",
    )
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


def _local_final_path(target_root: Path, relative_path: str) -> Path:
    parts = _relative_path_parts_for_adapter(
        relative_path,
        validation_code="LOCAL_FINAL_COMMIT_REQUIRES_RELATIVE_PATH",
        next_action="Provide an endpoint-relative final path before commit.",
    )
    root = _resolve_existing_root(
        target_root,
        missing_code="LOCAL_FINAL_COMMIT_TARGET_ROOT_MISSING",
        missing_next_action="Create the target endpoint root before final commit.",
        reparse_code="LOCAL_FINAL_COMMIT_TARGET_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the target endpoint root before final commit.",
    )
    final_path = root.joinpath(*parts)
    parent = final_path.parent
    if not parent.is_dir():
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_TARGET_PARENT_MISSING",
            "Create and verify the final parent directory before commit.",
        )
    _reject_symlink_in_path(
        root=root,
        relative_parts=parts[:-1],
        validation_code="LOCAL_FINAL_COMMIT_REPARSE_UNSUPPORTED",
        next_action="Revalidate the final path chain before committing through reparse points.",
    )
    try:
        parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_COMMIT_TARGET_ESCAPES_ROOT",
            "Resolve the final path through a validated endpoint root before commit.",
        ) from exc
    return final_path


def _replace_final_path(target_root: Path, relative_path: str) -> Path:
    parts = _relative_path_parts_for_adapter(
        relative_path,
        validation_code="LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_RELATIVE_PATH",
        next_action="Provide an endpoint-relative final path before replacement.",
    )
    root = _resolve_existing_root(
        target_root,
        missing_code="LOCAL_REPLACE_FINAL_COMMIT_TARGET_ROOT_MISSING",
        missing_next_action="Create the target endpoint root before replacement.",
        reparse_code="LOCAL_REPLACE_FINAL_COMMIT_TARGET_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the target endpoint root before replacement.",
    )
    final_path = root.joinpath(*parts)
    parent = final_path.parent
    if not parent.is_dir():
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_TARGET_PARENT_MISSING",
            "Create and verify the final parent directory before replacement.",
        )
    _reject_symlink_in_path(root=root, relative_parts=parts[:-1])
    try:
        parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_TARGET_ESCAPES_ROOT",
            "Resolve the final path through a validated endpoint root before replacement.",
        ) from exc
    return final_path


def _relative_path_parts(value: str) -> tuple[str, ...]:
    return _relative_path_parts_for_adapter(
        value,
        validation_code="LAB_FINAL_COMMIT_REQUIRES_RELATIVE_PATH",
        next_action="Provide an endpoint-relative final path before commit.",
    )


def _relative_path_parts_for_adapter(
    value: str,
    *,
    validation_code: str,
    next_action: str,
) -> tuple[str, ...]:
    try:
        return parse_endpoint_relative_path(value).parts
    except SafePathViolation as exc:
        raise FinalCommitAdapterError(
            validation_code,
            next_action,
        ) from exc


def _reject_symlink_in_path(
    *,
    root: Path,
    relative_parts: tuple[str, ...],
    validation_code: str = "LAB_FINAL_COMMIT_REPARSE_UNSUPPORTED",
    next_action: str = "Revalidate the final path chain before committing through reparse points.",
    allow_missing_suffix: bool = False,
) -> None:
    try:
        DEFAULT_REPARSE_GUARD.reject_reparse_chain(
            root=root,
            relative_parts=relative_parts,
            missing_code="LOCAL_FINAL_COMMIT_PATH_CHAIN_MISSING",
            missing_next_action="Refresh analysis because the final path chain changed.",
            reparse_code=validation_code,
            reparse_next_action=next_action,
            allow_missing_suffix=allow_missing_suffix,
        )
        DEFAULT_REPARSE_GUARD.require_resolved_under_root(
            root=root,
            path=root.joinpath(*relative_parts),
            strict=not allow_missing_suffix,
            escape_code="LOCAL_FINAL_COMMIT_PATH_ESCAPES_ROOT",
            escape_next_action="Resolve the final path through a validated endpoint root.",
        )
    except ReparseGuardError as exc:
        raise FinalCommitAdapterError(exc.validation_code, exc.next_action) from exc


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
            move_path_write_through(
                temp_path,
                final_path,
                replace_existing=False,
            )
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_TARGET_EXISTS",
                "Use the replace/version commit flow for existing targets; this adapter only inserts new files.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LAB_FINAL_COMMIT_WRITE_THROUGH_MOVE_FAILED",
                "Keep recovery state and re-probe target write-through move support.",
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _link_local_payload_without_overwrite(
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
                "LOCAL_FINAL_COMMIT_TEMP_HASH_MISMATCH",
                "Restage and verify the artifact before attempting final commit.",
            )
        try:
            move_path_write_through(
                temp_path,
                final_path,
                replace_existing=False,
            )
        except FileExistsError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_FINAL_COMMIT_TARGET_EXISTS",
                "Use the replace/version commit flow for existing targets.",
            ) from exc
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_FINAL_COMMIT_WRITE_THROUGH_MOVE_FAILED",
                "Keep recovery state and re-probe target write-through move support.",
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _replace_with_verified_payload(
    *,
    staging_payload: Path,
    final_path: Path,
    content_hash: str,
) -> None:
    temp_path = final_path.parent / f".{final_path.name}.{uuid4().hex}.mediasync-replace.tmp"
    try:
        _copy_file_durable(source=staging_payload, destination=temp_path)
        if _hash_file(temp_path) != content_hash:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_TEMP_HASH_MISMATCH",
                "Restage and verify the artifact before attempting replacement.",
            )
        try:
            move_path_write_through(
                temp_path,
                final_path,
                replace_existing=True,
            )
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_WRITE_THROUGH_MOVE_FAILED",
                "Keep recovery state and inspect final, staging and version-object postconditions.",
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _validate_replace_operation_binding(
    *,
    operation: RecoveryOperation,
    permit: MutationPermit,
) -> None:
    if (
        operation.run_id != permit.run_id
        or operation.run_target_id != permit.run_target_id
        or operation.target_endpoint_id != permit.endpoint_id
        or operation.target_endpoint_revision_id != permit.endpoint_revision_id
        or operation.owner_installation_id != permit.owner_installation_id
        or operation.ownership_epoch != permit.ownership_epoch
        or operation.lease_id != permit.lease_id
        or operation.lease_resource_key != permit.resource_key
        or operation.fencing_token != permit.fencing_token
    ):
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_PERMIT_MISMATCH",
            "Reacquire the endpoint lease before preserving an old target.",
        )


def _version_object_paths(
    *,
    target_root: Path,
    version_object_id: str,
    create: bool = True,
) -> tuple[Path, Path]:
    if OBJECT_ID_PATTERN.fullmatch(version_object_id) is None:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_REQUIRES_SAFE_VERSION_OBJECT_ID",
            "Use an opaque version object id before preserving an old target.",
        )
    root = _resolve_existing_root(
        target_root,
        missing_code="LOCAL_REPLACE_FINAL_COMMIT_TARGET_ROOT_MISSING",
        missing_next_action="Create the target endpoint root before replacement.",
        reparse_code="LOCAL_REPLACE_FINAL_COMMIT_TARGET_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the target endpoint root before replacement.",
    )
    relative_parts = (".mediasync", "objects", "versions")
    _reject_symlink_in_path(root=root, relative_parts=relative_parts, allow_missing_suffix=True)
    version_root = root.joinpath(*relative_parts)
    if create:
        version_root.mkdir(parents=True, exist_ok=True)
    try:
        version_root.resolve(strict=create).relative_to(root)
    except (OSError, ValueError) as exc:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_VERSION_STORE_ESCAPES_ROOT",
            "Revalidate the endpoint control area before preserving an old target.",
        ) from exc
    return (
        version_root / f"{version_object_id}.payload",
        version_root / f"{version_object_id}.manifest.json",
    )


def _quarantine_object_paths(
    *,
    target_root: Path,
    quarantine_object_id: str,
    create: bool = True,
) -> tuple[Path, Path]:
    if OBJECT_ID_PATTERN.fullmatch(quarantine_object_id) is None:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_REQUIRES_SAFE_OBJECT_ID",
            "Use an opaque quarantine object id before preserving a directory.",
        )
    root = _resolve_existing_root(
        target_root,
        missing_code="LOCAL_DIRECTORY_QUARANTINE_TARGET_ROOT_MISSING",
        missing_next_action="Create the target endpoint root before directory quarantine.",
        reparse_code="LOCAL_DIRECTORY_QUARANTINE_TARGET_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the target endpoint root before directory quarantine.",
    )
    relative_parts = (".mediasync", "objects", "quarantine")
    _reject_symlink_in_path(
        root=root,
        relative_parts=relative_parts,
        validation_code="LOCAL_DIRECTORY_QUARANTINE_REPARSE_UNSUPPORTED",
        next_action="Revalidate the endpoint control area before preserving a directory.",
        allow_missing_suffix=True,
    )
    quarantine_root = root.joinpath(*relative_parts)
    if create:
        quarantine_root.mkdir(parents=True, exist_ok=True)
    try:
        quarantine_root.resolve(strict=create).relative_to(root)
    except (OSError, ValueError) as exc:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_STORE_ESCAPES_ROOT",
            "Revalidate the endpoint control area before preserving a directory.",
        ) from exc
    return (
        quarantine_root / f"{quarantine_object_id}.payload",
        quarantine_root / f"{quarantine_object_id}.manifest.json",
    )


def _version_manifest_exists(*, target_root: Path, object_id: str) -> bool:
    _, manifest_path = _version_object_paths(
        target_root=target_root,
        version_object_id=object_id,
        create=False,
    )
    return manifest_path.is_file() and not manifest_path.is_symlink()


def _directory_empty_final_path(target_root: Path, relative_path: str) -> Path:
    parts = _relative_path_parts_for_adapter(
        relative_path,
        validation_code="LOCAL_DIRECTORY_QUARANTINE_REQUIRES_RELATIVE_PATH",
        next_action="Provide an endpoint-relative final path before directory quarantine.",
    )
    root = _resolve_existing_root(
        target_root,
        missing_code="LOCAL_DIRECTORY_QUARANTINE_TARGET_ROOT_MISSING",
        missing_next_action="Create the target endpoint root before directory quarantine.",
        reparse_code="LOCAL_DIRECTORY_QUARANTINE_TARGET_ROOT_REPARSE_UNSUPPORTED",
        reparse_next_action="Revalidate the target endpoint root before directory quarantine.",
    )
    final_path = root.joinpath(*parts)
    parent = final_path.parent
    if not parent.is_dir():
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_TARGET_PARENT_MISSING",
            "Create and verify the final parent directory before directory quarantine.",
        )
    _reject_symlink_in_path(
        root=root,
        relative_parts=parts,
        validation_code="LOCAL_DIRECTORY_QUARANTINE_REPARSE_UNSUPPORTED",
        next_action="Revalidate the final path chain before directory quarantine.",
        allow_missing_suffix=True,
    )
    try:
        parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_TARGET_ESCAPES_ROOT",
            "Resolve the final path through a validated endpoint root before directory quarantine.",
        ) from exc
    return final_path


def _validate_existing_quarantine_payload(*, payload_path: Path, final_path: Path) -> None:
    if payload_path.is_symlink() or not payload_path.is_dir():
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_PAYLOAD_INVALID",
            "Enter recovery because the quarantine object path is not a directory.",
        )
    _require_empty_directory(payload_path)
    if final_path.exists() or final_path.is_symlink():
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_DUPLICATE_TARGET",
            "Enter recovery because both final path and quarantine object exist.",
        )


def _require_empty_directory(
    path: Path,
    *,
    read_failed_code: str = "LOCAL_DIRECTORY_QUARANTINE_READ_FAILED",
    not_empty_code: str = "LOCAL_DIRECTORY_QUARANTINE_TARGET_NOT_EMPTY",
    next_action: str = "Refresh analysis because the directory contents cannot be proven empty.",
) -> None:
    try:
        next(path.iterdir())
    except StopIteration:
        return
    except OSError as exc:
        raise FinalCommitAdapterError(
            read_failed_code,
            next_action,
        ) from exc
    raise FinalCommitAdapterError(
        not_empty_code,
        next_action,
    )


def _write_quarantine_manifest(
    *,
    manifest_path: Path,
    manifest: VersionObjectManifest,
) -> None:
    payload = manifest.canonical_json
    if manifest_path.exists():
        try:
            existing = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_MANIFEST_INVALID",
                "Enter recovery because the quarantine manifest cannot be read.",
            ) from exc
        if existing != payload:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_MANIFEST_CONFLICT",
                "Enter recovery because the existing quarantine manifest differs from this operation.",
            )
        return
    temp_path = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, manifest_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _load_quarantine_manifest(
    *,
    manifest_path: Path,
    expected_operation_id: str,
    expected_quarantine_object_id: str,
    expected_final_relative_path: str,
    missing_code: str = "LOCAL_DIRECTORY_QUARANTINE_RESTORE_MANIFEST_MISSING",
    invalid_code: str = "LOCAL_DIRECTORY_QUARANTINE_RESTORE_MANIFEST_INVALID",
    mismatch_code: str = "LOCAL_DIRECTORY_QUARANTINE_RESTORE_MANIFEST_MISMATCH",
    missing_next_action: str = "Recover or reconcile the quarantine manifest before restoring the directory.",
    invalid_next_action: str = "Enter recovery because the quarantine manifest is invalid.",
    mismatch_next_action: str = "Reload recovery state before restoring the quarantined directory.",
) -> dict[str, object]:
    try:
        raw_manifest = manifest_path.read_text(encoding="utf-8")
        parsed = parse_version_object_manifest(raw_manifest)
        manifest = cast(dict[str, object], json.loads(raw_manifest))
    except OSError as exc:
        raise FinalCommitAdapterError(
            missing_code,
            missing_next_action,
        ) from exc
    except (json.JSONDecodeError, VersionObjectManifestError) as exc:
        raise FinalCommitAdapterError(
            invalid_code,
            invalid_next_action,
        ) from exc
    if (
        parsed.object_role != EMPTY_DIRECTORY_QUARANTINE_ROLE
        or parsed.operation_id != expected_operation_id
        or parsed.version_object_id != expected_quarantine_object_id
        or parsed.final_relative_path != _relative_path(expected_final_relative_path)
    ):
        raise FinalCommitAdapterError(
            mismatch_code,
            mismatch_next_action,
        )
    return manifest


def _cleanup_empty_directory_quarantine_object(
    *,
    target_root: Path,
    operation: RecoveryOperation,
    quarantine_object_id: str,
) -> None:
    quarantine_payload, quarantine_manifest = _quarantine_object_paths(
        target_root=target_root,
        quarantine_object_id=quarantine_object_id,
        create=False,
    )
    payload_exists = quarantine_payload.exists() or quarantine_payload.is_symlink()
    manifest_exists = quarantine_manifest.exists() or quarantine_manifest.is_symlink()
    if not payload_exists and not manifest_exists:
        return
    if not manifest_exists:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_MISSING",
            "Recover or reconcile the quarantine manifest before cleanup.",
        )
    if quarantine_manifest.is_symlink() or not quarantine_manifest.is_file():
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_INVALID",
            "Enter recovery because the quarantine manifest path is not a regular file.",
        )

    manifest = _load_quarantine_manifest(
        manifest_path=quarantine_manifest,
        expected_operation_id=operation.operation_id,
        expected_quarantine_object_id=quarantine_object_id,
        expected_final_relative_path=operation.final_relative_path,
        missing_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_MISSING",
        invalid_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_INVALID",
        mismatch_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_MISMATCH",
        missing_next_action="Recover or reconcile the quarantine manifest before cleanup.",
        invalid_next_action="Enter recovery because the quarantine manifest is invalid.",
        mismatch_next_action="Reload recovery state before cleaning the quarantined directory.",
    )
    expected = _manifest_empty_directory_precondition(
        manifest,
        validation_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_INVALID",
        next_action="Reload recovery state before cleaning the quarantined directory.",
    )
    if operation.expected_target_fingerprint_json is not None:
        bound_expected = _expected_empty_directory_precondition(
            operation.expected_target_fingerprint_json,
            validation_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_EVIDENCE_MISMATCH",
            next_action="Reload recovery state before cleaning the quarantined directory.",
        )
        if bound_expected != expected:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_EVIDENCE_MISMATCH",
                "Reload recovery state before cleaning the quarantined directory.",
            )
    if manifest.get("payload_name") != quarantine_payload.name:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_MISMATCH",
            "Reload recovery state before cleaning the quarantined directory.",
        )

    if payload_exists:
        if quarantine_payload.is_symlink() or not quarantine_payload.is_dir():
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_PAYLOAD_INVALID",
                "Enter recovery because the quarantine object path is not an empty directory.",
            )
        _require_empty_directory(
            quarantine_payload,
            read_failed_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_PAYLOAD_READ_FAILED",
            not_empty_code="LOCAL_DIRECTORY_QUARANTINE_CLEANUP_PAYLOAD_NOT_EMPTY",
            next_action="Do not cleanup a quarantine payload whose contents changed.",
        )
        try:
            quarantine_payload.rmdir()
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_PAYLOAD_REMOVE_FAILED",
                "Retry cleanup after inspecting the quarantine payload.",
            ) from exc

    try:
        quarantine_manifest.unlink(missing_ok=True)
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_DIRECTORY_QUARANTINE_CLEANUP_MANIFEST_REMOVE_FAILED",
            "Retry cleanup after inspecting the quarantine manifest.",
        ) from exc


def _manifest_empty_directory_precondition(
    manifest: dict[str, object],
    *,
    validation_code: str,
    next_action: str,
) -> dict[str, object]:
    raw_fingerprint = manifest.get("fingerprint")
    if not isinstance(raw_fingerprint, dict):
        raise FinalCommitAdapterError(validation_code, next_action)
    if raw_fingerprint.get("kind") != RecoveryTargetPreconditionKind.DIRECTORY_EMPTY.value:
        raise FinalCommitAdapterError(validation_code, next_action)
    if raw_fingerprint.get("entry_count") != 0:
        raise FinalCommitAdapterError(validation_code, next_action)
    return {
        "entry_count": 0,
        "kind": RecoveryTargetPreconditionKind.DIRECTORY_EMPTY.value,
    }


def _preserve_version_payload(
    *,
    source: Path,
    destination: Path,
    expected_fingerprint: dict[str, object],
) -> None:
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_PAYLOAD_INVALID",
                "Enter recovery because the version object path is not a regular file.",
            )
        if _fingerprint_file(destination) != expected_fingerprint:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_PAYLOAD_MISMATCH",
                "Enter recovery because the existing version object differs from the old target.",
            )
        return
    temp_path = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        _copy_file_durable(source=source, destination=temp_path)
        if _fingerprint_file(temp_path) != expected_fingerprint:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_HASH_MISMATCH",
                "Refresh analysis because the target changed while preserving the old version.",
            )
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _version_manifest_fingerprint(
    manifest: VersionObjectManifest,
) -> dict[str, object]:
    return {
        "byte_count": manifest.fingerprint_byte_count,
        "content_hash": manifest.fingerprint_content_hash,
    }


def _require_version_payload_matches_manifest(
    *,
    version_payload: Path,
    fingerprint: dict[str, object],
) -> None:
    if not version_payload.is_file() or version_payload.is_symlink():
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_VERSION_PAYLOAD_MISSING",
            "Enter recovery because the preserved old target payload is missing.",
        )
    if _fingerprint_file(version_payload) != fingerprint:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_VERSION_PAYLOAD_MISMATCH",
            "Enter recovery because the preserved old target no longer matches its manifest.",
        )


def _version_preservation_receipt(
    *,
    operation: RecoveryOperation,
    manifest: VersionObjectManifest,
) -> OldTargetPreservationReceipt:
    return OldTargetPreservationReceipt(
        operation_id=operation.operation_id,
        final_relative_path=RelativePath(operation.final_relative_path),
        version_object_id=manifest.version_object_id,
        fingerprint_json=_canonical_json(_version_manifest_fingerprint(manifest)),
        version_created_utc=manifest.created_utc,
        version_retention_until_utc=manifest.retention_until_utc,
        version_manifest_hash=manifest.manifest_hash,
    )


def _write_version_manifest(*, manifest_path: Path, canonical_manifest: str) -> None:
    if manifest_path.exists():
        try:
            existing = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_MANIFEST_INVALID",
                "Enter recovery because the version manifest cannot be read.",
            ) from exc
        if existing != canonical_manifest:
            raise FinalCommitAdapterError(
                "LOCAL_REPLACE_FINAL_COMMIT_VERSION_MANIFEST_CONFLICT",
                "Enter recovery because the existing version manifest differs from this operation.",
            )
        return
    temp_path = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            handle.write(canonical_manifest)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, manifest_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _load_version_manifest(
    *,
    manifest_path: Path,
    operation: RecoveryOperation | None = None,
    expected_operation_id: str | None = None,
    expected_final_relative_path: str | None = None,
    permit: MutationPermit | None = None,
) -> VersionObjectManifest:
    try:
        raw_manifest = manifest_path.read_text(encoding="utf-8")
        manifest = parse_version_object_manifest(raw_manifest)
        if operation is not None:
            require_version_object_manifest_binding(manifest, operation=operation)
    except (OSError, VersionObjectManifestError) as exc:
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_VERSION_MANIFEST_MISSING",
            "Preserve the old target before replacing the final file.",
        ) from exc
    if operation is None and (
        expected_operation_id is None
        or expected_final_relative_path is None
        or permit is None
        or manifest.version_object_id != expected_operation_id
        or manifest.operation_id != expected_operation_id
        or manifest.run_id != permit.run_id
        or manifest.run_target_id != permit.run_target_id
        or manifest.target_endpoint_id != permit.endpoint_id
        or manifest.target_endpoint_revision_id != permit.endpoint_revision_id
        or manifest.endpoint_generation != permit.endpoint_generation
        or manifest.owner_installation_id != permit.owner_installation_id
        or manifest.ownership_epoch != permit.ownership_epoch
        or manifest.final_relative_path != _relative_path(expected_final_relative_path)
    ):
        raise FinalCommitAdapterError(
            "LOCAL_REPLACE_FINAL_COMMIT_VERSION_MANIFEST_MISMATCH",
            "Reload recovery state before retrying replacement.",
        )
    return manifest


def _manifest_fingerprint(
    manifest: dict[str, object],
    *,
    validation_code: str,
    next_action: str,
) -> dict[str, object]:
    raw_fingerprint = manifest.get("fingerprint")
    if not isinstance(raw_fingerprint, dict):
        raise FinalCommitAdapterError(validation_code, next_action)
    content_hash = raw_fingerprint.get("content_hash")
    byte_count = raw_fingerprint.get("byte_count")
    if not isinstance(content_hash, str) or HASH_PATTERN.fullmatch(content_hash) is None:
        raise FinalCommitAdapterError(validation_code, next_action)
    if not isinstance(byte_count, int) or byte_count < 0:
        raise FinalCommitAdapterError(validation_code, next_action)
    return {"byte_count": byte_count, "content_hash": content_hash}


def _expected_fingerprint(
    raw_payload: str | None,
    *,
    validation_code: str,
    next_action: str,
) -> dict[str, object]:
    if raw_payload is None:
        raise FinalCommitAdapterError(validation_code, next_action)
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise FinalCommitAdapterError(validation_code, next_action) from exc
    if not isinstance(payload, dict):
        raise FinalCommitAdapterError(validation_code, next_action)
    content_hash = payload.get("content_hash")
    byte_count = payload.get("byte_count")
    if not isinstance(content_hash, str) or HASH_PATTERN.fullmatch(content_hash) is None:
        raise FinalCommitAdapterError(validation_code, next_action)
    if not isinstance(byte_count, int) or byte_count < 0:
        raise FinalCommitAdapterError(validation_code, next_action)
    return {"byte_count": byte_count, "content_hash": content_hash}


def _expected_empty_directory_precondition(
    raw_payload: str | None,
    *,
    validation_code: str,
    next_action: str,
) -> dict[str, object]:
    if raw_payload is None:
        raise FinalCommitAdapterError(validation_code, next_action)
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise FinalCommitAdapterError(validation_code, next_action) from exc
    if not isinstance(payload, dict):
        raise FinalCommitAdapterError(validation_code, next_action)
    if payload.get("kind") != RecoveryTargetPreconditionKind.DIRECTORY_EMPTY.value:
        raise FinalCommitAdapterError(validation_code, next_action)
    if payload.get("entry_count") != 0:
        raise FinalCommitAdapterError(validation_code, next_action)
    return {
        "entry_count": 0,
        "kind": RecoveryTargetPreconditionKind.DIRECTORY_EMPTY.value,
    }


def _copy_file_durable(*, source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("xb") as writer:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())


def _flushed_file_commit_receipt(
    *,
    artifact: VerifiedStagingArtifact,
    final_path: Path,
    write_through_move_used: bool,
) -> CommitReceipt:
    _flush_committed_file(final_path)
    return CommitReceipt(
        operation_id=artifact.object_id,
        final_relative_path=artifact.relative_path,
        durability_state=(
            "LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED"
            if write_through_move_used
            else "LOCAL_FILE_FLUSH_CONFIRMED"
        ),
        file_flush_succeeded=True,
        write_through_move_used=write_through_move_used,
    )


def _flushed_directory_commit_receipt(
    *,
    artifact: VerifiedStagingArtifact,
    final_path: Path,
    write_through_move_used: bool,
) -> CommitReceipt:
    _flush_committed_file(final_path / DIRECTORY_MARKER_NAME)
    return CommitReceipt(
        operation_id=artifact.object_id,
        final_relative_path=artifact.relative_path,
        durability_state=(
            "LOCAL_DIRECTORY_MARKER_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED"
            if write_through_move_used
            else "LOCAL_DIRECTORY_MARKER_FLUSH_CONFIRMED_ENTRY_UNCONFIRMED"
        ),
        file_flush_succeeded=True,
        write_through_move_used=write_through_move_used,
    )


def _flush_committed_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_DURABILITY_FILE_MISSING",
            "Enter recovery and inspect the committed final object before retrying.",
        )
    try:
        with path.open("r+b", buffering=0) as handle:
            os.fsync(handle.fileno())
    except OSError as exc:
        raise FinalCommitAdapterError(
            "LOCAL_FINAL_FILE_FLUSH_FAILED",
            "Keep recovery state and retry final-file durability verification.",
        ) from exc


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def _relative_path(value: str) -> str:
    return value.replace("\\", "/")
