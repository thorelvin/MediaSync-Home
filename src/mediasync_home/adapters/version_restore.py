from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointRootResolver,
)
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseGuardError
from mediasync_home.application.safe_paths import SafePathViolation, parse_endpoint_relative_path
from mediasync_home.application.version_objects import (
    EMPTY_DIRECTORY_QUARANTINE_ROLE,
    OLD_TARGET_VERSION_ROLE,
    VersionObjectManifest,
    VersionObjectManifestError,
    parse_version_object_manifest,
)
from mediasync_home.application.version_restore import (
    VersionRestoreApplyReceipt,
    VersionRestoreInspectionReceipt,
    VersionRestoreOperation,
    VersionRestorePermitValidator,
    VersionRestoreRollbackReceipt,
    VersionRestoreState,
    canonical_fingerprint_json,
)
from mediasync_home.application.version_restore_rollback import (
    VersionRestoreRollbackDeleteReceipt,
    VersionRestoreRollbackOperation,
    VersionRestoreRollbackPermitValidator,
    VersionRestoreRollbackState,
    VersionRestoreUndoApplyReceipt,
    VersionRestoreUndoInspectionReceipt,
)
from mediasync_home.domain.capabilities import MutationPermit


_OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class VersionRestoreFilesystemError(RuntimeError):
    def __init__(
        self,
        validation_code: str,
        next_action: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action
        self.retryable = retryable


class LocalRetainedVersionRestoreAdapter:
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        reparse_guard: LocalReparseGuard | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._reparse_guard = reparse_guard or LocalReparseGuard()

    def inspect_restore(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreInspectionReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_endpoint_permit_binding(operation=operation, permit=permit)
        root, final_path = self._root_and_final_path(operation=operation, permit=permit)
        historical = self._load_historical_version(
            root=root,
            operation=operation,
        )
        current = (
            _current_final_fingerprint(final_path)
            if operation.record.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE
            else _require_regular_file_fingerprint(
                final_path,
                missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
                invalid_code="VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
                read_code="VERSION_RESTORE_CURRENT_FINAL_READ_FAILED",
            )
        )
        if current is None:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_CURRENT_FINAL_MISSING",
                "Review the restore paths before retrying.",
            )
        historical_json = _fingerprint_json(historical)
        current_json = _fingerprint_json(current)
        return VersionRestoreInspectionReceipt(
            historical_fingerprint_json=historical_json,
            current_final_fingerprint_json=current_json,
            already_current=current_json == historical_json,
        )

    def preserve_current_final(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreRollbackReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_journaled_permit_binding(operation=operation, permit=permit)
        if operation.state is not VersionRestoreState.INTENT_RECORDED:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_PRESERVE_STATE_INVALID",
                "Reload the restore journal before preserving the current final.",
            )
        expected_current = _fingerprint_from_json(
            operation.current_final_fingerprint_json,
            "VERSION_RESTORE_CURRENT_FINGERPRINT_MISSING",
        )
        root, final_path = self._root_and_final_path(operation=operation, permit=permit)
        self._load_historical_version(root=root, operation=operation)
        observed_current = _require_regular_file_fingerprint(
            final_path,
            missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
            invalid_code="VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
            read_code="VERSION_RESTORE_CURRENT_FINAL_READ_FAILED",
        )
        if observed_current != expected_current:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_CURRENT_FINAL_CHANGED_BEFORE_PRESERVE",
                "Review the final file because it changed after restore intent was recorded.",
            )
        payload_path, manifest_path = self._rollback_paths(
            root=root,
            operation=operation,
            create=True,
        )
        manifest = _rollback_manifest(
            operation=operation,
            fingerprint=expected_current,
        )
        _preserve_payload(
            source=final_path,
            destination=payload_path,
            expected=expected_current,
        )
        _write_canonical_manifest(manifest_path=manifest_path, manifest=manifest)
        return VersionRestoreRollbackReceipt(
            rollback_object_id=operation.rollback_object_id,
            current_final_fingerprint_json=_fingerprint_json(expected_current),
            manifest_hash=str(manifest["manifest_hash"]),
        )

    def apply_historical_version(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreApplyReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_journaled_permit_binding(operation=operation, permit=permit)
        if operation.state is not VersionRestoreState.CURRENT_FINAL_PRESERVED:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_APPLY_STATE_INVALID",
                "Reload the restore journal before applying the historical version.",
            )
        expected_current = _fingerprint_from_json(
            operation.current_final_fingerprint_json,
            "VERSION_RESTORE_CURRENT_FINGERPRINT_MISSING",
        )
        root, final_path = self._root_and_final_path(operation=operation, permit=permit)
        historical = self._load_historical_version(root=root, operation=operation)
        self._require_rollback_object(
            root=root,
            operation=operation,
            expected=expected_current,
        )
        historical_json = _fingerprint_json(historical)
        if operation.record.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE:
            _apply_empty_directory_restore(
                final_path=final_path,
                expected_current=expected_current,
            )
        else:
            observed = _require_regular_file_fingerprint(
                final_path,
                missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
                invalid_code="VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
                read_code="VERSION_RESTORE_CURRENT_FINAL_READ_FAILED",
            )
            if observed == historical:
                return VersionRestoreApplyReceipt(
                    historical_fingerprint_json=historical_json
                )
            if observed != expected_current:
                raise VersionRestoreFilesystemError(
                    "VERSION_RESTORE_CURRENT_FINAL_CHANGED_BEFORE_APPLY",
                    "Review the final file and rollback object before resuming restore.",
                )
            source_payload, _ = _retained_object_paths(
                root=root,
                object_id=operation.record.version_object_id,
                object_role=operation.record.object_role,
            )
            _replace_with_verified_payload(
                source=source_payload,
                final_path=final_path,
                expected=historical,
            )
        return VersionRestoreApplyReceipt(
            historical_fingerprint_json=historical_json
        )

    def verify_restored_final(
        self,
        *,
        permit_validator: VersionRestorePermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreOperation,
    ) -> VersionRestoreApplyReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_journaled_permit_binding(operation=operation, permit=permit)
        if operation.state is not VersionRestoreState.HISTORICAL_APPLIED:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_VERIFY_STATE_INVALID",
                "Reload the restore journal before verifying the final file.",
            )
        root, final_path = self._root_and_final_path(operation=operation, permit=permit)
        historical = self._load_historical_version(root=root, operation=operation)
        observed = _restored_final_fingerprint(
            final_path=final_path,
            object_role=operation.record.object_role,
        )
        if observed != historical:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_FINAL_FINGERPRINT_MISMATCH",
                "Keep the rollback object protected and review the final file.",
            )
        return VersionRestoreApplyReceipt(
            historical_fingerprint_json=_fingerprint_json(historical)
        )

    def inspect_restore_undo(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> VersionRestoreUndoInspectionReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_rollback_lifecycle_permit_binding(
            operation=operation,
            permit=permit,
            action="undo",
            journaled=False,
        )
        root, final_path = self._root_and_final_path(
            operation=operation.restore,
            permit=permit,
        )
        rollback = _fingerprint_from_json(
            operation.rollback_fingerprint_json,
            "VERSION_RESTORE_UNDO_ROLLBACK_FINGERPRINT_INVALID",
        )
        expected_final = _fingerprint_from_json(
            operation.expected_restored_final_fingerprint_json,
            "VERSION_RESTORE_UNDO_EXPECTED_FINAL_FINGERPRINT_INVALID",
        )
        self._require_rollback_object(
            root=root,
            operation=operation.restore,
            expected=rollback,
        )
        observed = _current_final_fingerprint(final_path)
        if observed not in (expected_final, rollback):
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_FINAL_CHANGED",
                "Keep the rollback object and review the final file before undoing.",
            )
        return VersionRestoreUndoInspectionReceipt(
            current_final_fingerprint_json=_fingerprint_json(observed),
            rollback_fingerprint_json=_fingerprint_json(rollback),
            already_undone=observed == rollback,
        )

    def apply_restore_undo(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> VersionRestoreUndoApplyReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_rollback_lifecycle_permit_binding(
            operation=operation,
            permit=permit,
            action="undo",
            journaled=True,
        )
        if operation.state is not VersionRestoreRollbackState.UNDO_INTENT_RECORDED:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_APPLY_STATE_INVALID",
                "Reload the rollback lifecycle before applying undo.",
            )
        root, final_path = self._root_and_final_path(
            operation=operation.restore,
            permit=permit,
        )
        rollback = _fingerprint_from_json(
            operation.rollback_fingerprint_json,
            "VERSION_RESTORE_UNDO_ROLLBACK_FINGERPRINT_INVALID",
        )
        expected_final = _fingerprint_from_json(
            operation.expected_restored_final_fingerprint_json,
            "VERSION_RESTORE_UNDO_EXPECTED_FINAL_FINGERPRINT_INVALID",
        )
        self._require_rollback_object(
            root=root,
            operation=operation.restore,
            expected=rollback,
        )
        observed = _current_final_fingerprint(final_path, allow_missing=True)
        if observed != rollback:
            if observed is not None and observed != expected_final:
                raise VersionRestoreFilesystemError(
                    "VERSION_RESTORE_UNDO_FINAL_CHANGED_BEFORE_APPLY",
                    "Keep the rollback object and review the changed final file.",
                )
            rollback_payload, _ = self._rollback_paths(
                root=root,
                operation=operation.restore,
                create=False,
            )
            if operation.restore.record.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE:
                _replace_empty_directory_with_verified_file(
                    source=rollback_payload,
                    final_path=final_path,
                    expected=rollback,
                )
            else:
                _replace_with_verified_payload(
                    source=rollback_payload,
                    final_path=final_path,
                    expected=rollback,
                )
        return VersionRestoreUndoApplyReceipt(
            rollback_fingerprint_json=_fingerprint_json(rollback)
        )

    def verify_restore_undo(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> VersionRestoreUndoApplyReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_rollback_lifecycle_permit_binding(
            operation=operation,
            permit=permit,
            action="undo",
            journaled=True,
        )
        if operation.state is not VersionRestoreRollbackState.UNDO_APPLIED:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_VERIFY_STATE_INVALID",
                "Reload the rollback lifecycle before verifying undo.",
            )
        _, final_path = self._root_and_final_path(
            operation=operation.restore,
            permit=permit,
        )
        rollback = _fingerprint_from_json(
            operation.rollback_fingerprint_json,
            "VERSION_RESTORE_UNDO_ROLLBACK_FINGERPRINT_INVALID",
        )
        observed = _require_regular_file_fingerprint(
            final_path,
            missing_code="VERSION_RESTORE_UNDO_FINAL_MISSING_AFTER_APPLY",
            invalid_code="VERSION_RESTORE_UNDO_FINAL_TYPE_INVALID_AFTER_APPLY",
            read_code="VERSION_RESTORE_UNDO_FINAL_READ_FAILED_AFTER_APPLY",
        )
        if observed != rollback:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_FINAL_FINGERPRINT_MISMATCH",
                "Keep the rollback evidence and review the final file.",
            )
        return VersionRestoreUndoApplyReceipt(
            rollback_fingerprint_json=_fingerprint_json(rollback)
        )

    def verify_restore_rollback_for_expiry(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
    ) -> None:
        permit_validator.assert_mutation_permit_current(permit)
        _require_rollback_lifecycle_permit_binding(
            operation=operation,
            permit=permit,
            action="expiry",
            journaled=False,
        )
        root = self._root_for_operation(
            operation=operation.restore,
            permit=permit,
        )
        rollback = _fingerprint_from_json(
            operation.rollback_fingerprint_json,
            "VERSION_RESTORE_ROLLBACK_FINGERPRINT_INVALID",
        )
        self._require_rollback_object(
            root=root,
            operation=operation.restore,
            expected=rollback,
        )

    def delete_restore_rollback(
        self,
        *,
        permit_validator: VersionRestoreRollbackPermitValidator,
        permit: MutationPermit,
        operation: VersionRestoreRollbackOperation,
        resuming_delete_intent: bool,
    ) -> VersionRestoreRollbackDeleteReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_rollback_lifecycle_permit_binding(
            operation=operation,
            permit=permit,
            action="expiry",
            journaled=True,
        )
        if operation.state is not VersionRestoreRollbackState.EXPIRY_INTENT_RECORDED:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_DELETE_STATE_INVALID",
                "Reload the rollback lifecycle before deleting expired evidence.",
            )
        root = self._root_for_operation(
            operation=operation.restore,
            permit=permit,
        )
        payload_path, manifest_path = self._rollback_paths(
            root=root,
            operation=operation.restore,
            create=False,
        )
        payload_exists = payload_path.exists() or payload_path.is_symlink()
        manifest_exists = manifest_path.exists() or manifest_path.is_symlink()
        if not payload_exists and not manifest_exists:
            if not resuming_delete_intent:
                raise VersionRestoreFilesystemError(
                    "VERSION_RESTORE_ROLLBACK_PAIR_MISSING_BEFORE_INTENT",
                    "Review the rollback lifecycle before recording expiry.",
                )
            return _rollback_delete_receipt(operation)
        if not payload_exists and manifest_exists and resuming_delete_intent:
            rollback = _fingerprint_from_json(
                operation.rollback_fingerprint_json,
                "VERSION_RESTORE_ROLLBACK_FINGERPRINT_INVALID",
            )
            self._require_rollback_manifest(
                manifest_path=manifest_path,
                operation=operation.restore,
                expected=rollback,
            )
            try:
                manifest_path.unlink()
            except OSError as exc:
                raise VersionRestoreFilesystemError(
                    "VERSION_RESTORE_ROLLBACK_DELETE_FAILED",
                    "Retry expiry after checking endpoint permissions.",
                    retryable=True,
                ) from exc
            if manifest_path.exists() or manifest_path.is_symlink():
                raise VersionRestoreFilesystemError(
                    "VERSION_RESTORE_ROLLBACK_DELETE_POSTCONDITION_FAILED",
                    "Review the rollback object because expiry was incomplete.",
                )
            return _rollback_delete_receipt(operation)
        if not payload_exists or not manifest_exists:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_PAIR_PARTIAL",
                "Keep expiry blocked and review the partial rollback object.",
            )
        rollback = _fingerprint_from_json(
            operation.rollback_fingerprint_json,
            "VERSION_RESTORE_ROLLBACK_FINGERPRINT_INVALID",
        )
        self._require_rollback_object(
            root=root,
            operation=operation.restore,
            expected=rollback,
        )
        try:
            payload_path.unlink()
            manifest_path.unlink()
        except OSError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_DELETE_FAILED",
                "Retry expiry after checking endpoint permissions.",
                retryable=True,
            ) from exc
        if payload_path.exists() or manifest_path.exists():
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_DELETE_POSTCONDITION_FAILED",
                "Review the rollback object because expiry was incomplete.",
            )
        return _rollback_delete_receipt(operation)

    def _root_and_final_path(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
    ) -> tuple[Path, Path]:
        resolved_root = self._root_for_operation(
            operation=operation,
            permit=permit,
        )
        try:
            relative = parse_endpoint_relative_path(operation.record.final_relative_path)
            self._reparse_guard.reject_reparse_chain(
                root=resolved_root,
                relative_parts=relative.parts,
                missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
                missing_next_action="Review the final path before restoring.",
                reparse_code="VERSION_RESTORE_FINAL_REPARSE_UNSUPPORTED",
                reparse_next_action="Review the final path before restoring.",
            )
        except SafePathViolation as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_FINAL_PATH_INVALID",
                "Review the cataloged endpoint-relative path before restoring.",
            ) from exc
        except ReparseGuardError as exc:
            raise VersionRestoreFilesystemError(
                exc.validation_code,
                exc.next_action,
                retryable=exc.validation_code.endswith("_MISSING"),
            ) from exc
        return resolved_root, resolved_root.joinpath(*relative.parts)

    def _root_for_operation(
        self,
        *,
        operation: VersionRestoreOperation,
        permit: MutationPermit,
    ) -> Path:
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=permit.resource_key,
                endpoint_id=operation.record.target_endpoint_id,
                endpoint_revision_id=operation.record.target_endpoint_revision_id,
            )
        except EndpointLeaseUnavailable as exc:
            raise VersionRestoreFilesystemError(
                exc.validation_code,
                exc.next_action,
                retryable=True,
            ) from exc
        if root is None:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ENDPOINT_ROOT_MISSING",
                "Reconnect the exact endpoint revision before restoring.",
                retryable=True,
            )
        try:
            return self._reparse_guard.resolve_existing_root(
                Path(root),
                missing_code="VERSION_RESTORE_ENDPOINT_ROOT_MISSING",
                missing_next_action="Reconnect the endpoint before restoring.",
                reparse_code="VERSION_RESTORE_ENDPOINT_ROOT_REPARSE_UNSUPPORTED",
                reparse_next_action="Revalidate the endpoint root before restoring.",
            )
        except ReparseGuardError as exc:
            raise VersionRestoreFilesystemError(
                exc.validation_code,
                exc.next_action,
                retryable=exc.validation_code.endswith("_MISSING"),
            ) from exc

    def _load_historical_version(
        self,
        *,
        root: Path,
        operation: VersionRestoreOperation,
    ) -> dict[str, object]:
        object_root = (
            root
            / ".mediasync"
            / "objects"
            / (
                "quarantine"
                if operation.record.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE
                else "versions"
            )
        )
        try:
            self._reparse_guard.require_resolved_under_root(
                root=root,
                path=object_root,
                strict=True,
                escape_code="VERSION_RESTORE_HISTORICAL_STORE_INVALID",
                escape_next_action="Revalidate the retained-version object store.",
            )
        except ReparseGuardError as exc:
            raise VersionRestoreFilesystemError(
                exc.validation_code,
                exc.next_action,
            ) from exc
        payload_path, manifest_path = _retained_object_paths(
            root=root,
            object_id=operation.record.version_object_id,
            object_role=operation.record.object_role,
        )
        manifest = _load_bound_version_manifest(
            manifest_path=manifest_path,
            operation=operation,
        )
        expected = _fingerprint_from_json(
            manifest.fingerprint_json,
            "VERSION_RESTORE_HISTORICAL_FINGERPRINT_INVALID",
        )
        observed = (
            _require_empty_directory_fingerprint(
                payload_path,
                missing_code="VERSION_RESTORE_HISTORICAL_PAYLOAD_MISSING",
                invalid_code="VERSION_RESTORE_HISTORICAL_PAYLOAD_TYPE_INVALID",
                not_empty_code="VERSION_RESTORE_HISTORICAL_DIRECTORY_NOT_EMPTY",
            )
            if operation.record.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE
            else _require_regular_file_fingerprint(
                payload_path,
                missing_code="VERSION_RESTORE_HISTORICAL_PAYLOAD_MISSING",
                invalid_code="VERSION_RESTORE_HISTORICAL_PAYLOAD_TYPE_INVALID",
                read_code="VERSION_RESTORE_HISTORICAL_PAYLOAD_READ_FAILED",
            )
        )
        if observed != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_HISTORICAL_PAYLOAD_MISMATCH",
                "Keep the version protected and review its payload and manifest.",
            )
        return expected

    def _rollback_paths(
        self,
        *,
        root: Path,
        operation: VersionRestoreOperation,
        create: bool,
    ) -> tuple[Path, Path]:
        if _OBJECT_ID_PATTERN.fullmatch(operation.rollback_object_id) is None:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_OBJECT_ID_INVALID",
                "Review the restore journal before accessing rollback evidence.",
            )
        object_root = root / ".mediasync" / "objects" / "restores"
        try:
            objects_root = root / ".mediasync" / "objects"
            self._reparse_guard.require_resolved_under_root(
                root=root,
                path=objects_root,
                strict=True,
                escape_code="VERSION_RESTORE_OBJECT_STORE_INVALID",
                escape_next_action="Revalidate the endpoint object store before restoring.",
            )
            if create:
                object_root.mkdir(exist_ok=True)
            self._reparse_guard.require_resolved_under_root(
                root=root,
                path=object_root,
                strict=True,
                escape_code="VERSION_RESTORE_ROLLBACK_STORE_INVALID",
                escape_next_action="Revalidate the restore rollback store.",
            )
        except OSError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_STORE_CREATE_FAILED",
                "Retry after checking endpoint control-area permissions.",
                retryable=True,
            ) from exc
        except ReparseGuardError as exc:
            raise VersionRestoreFilesystemError(
                exc.validation_code,
                exc.next_action,
            ) from exc
        return (
            object_root / f"{operation.rollback_object_id}.payload",
            object_root / f"{operation.rollback_object_id}.manifest.json",
        )

    def _require_rollback_object(
        self,
        *,
        root: Path,
        operation: VersionRestoreOperation,
        expected: dict[str, object],
    ) -> None:
        payload_path, manifest_path = self._rollback_paths(
            root=root,
            operation=operation,
            create=False,
        )
        self._require_rollback_manifest(
            manifest_path=manifest_path,
            operation=operation,
            expected=expected,
        )

        observed = _require_regular_file_fingerprint(
            payload_path,
            missing_code="VERSION_RESTORE_ROLLBACK_PAYLOAD_MISSING",
            invalid_code="VERSION_RESTORE_ROLLBACK_PAYLOAD_TYPE_INVALID",
            read_code="VERSION_RESTORE_ROLLBACK_PAYLOAD_READ_FAILED",
        )
        if observed != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_PAYLOAD_MISMATCH",
                "Keep the restore blocked and review the rollback payload.",
            )

    @staticmethod
    def _require_rollback_manifest(
        *,
        manifest_path: Path,
        operation: VersionRestoreOperation,
        expected: dict[str, object],
    ) -> None:
        manifest = _load_rollback_manifest(
            manifest_path=manifest_path,
            operation=operation,
        )
        if manifest.get("manifest_hash") != operation.rollback_manifest_hash:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_MANIFEST_HASH_MISMATCH",
                "Keep the restore blocked and review the rollback manifest.",
            )
        if manifest.get("fingerprint") != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_MANIFEST_FINGERPRINT_MISMATCH",
                "Keep the restore blocked and review the rollback manifest.",
            )


def _require_endpoint_permit_binding(
    *,
    operation: VersionRestoreOperation,
    permit: MutationPermit,
) -> None:
    if (
        permit.run_id != f"version-restore:{operation.restore_id}"
        or permit.run_target_id != f"version-restore:{operation.restore_id}"
        or permit.endpoint_id != operation.record.target_endpoint_id
        or permit.endpoint_revision_id != operation.record.target_endpoint_revision_id
        or permit.endpoint_generation != operation.record.endpoint_generation
        or permit.owner_installation_id != operation.record.owner_installation_id
        or permit.ownership_epoch != operation.record.ownership_epoch
        or permit.resource_key != f"endpoint:{operation.record.target_endpoint_id}"
    ):
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_PERMIT_MISMATCH",
            "Reacquire the exact endpoint lease before restoring.",
        )


def _require_journaled_permit_binding(
    *,
    operation: VersionRestoreOperation,
    permit: MutationPermit,
) -> None:
    _require_endpoint_permit_binding(operation=operation, permit=permit)
    if (
        operation.lease_id != permit.lease_id
        or operation.fencing_token != permit.fencing_token
    ):
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_JOURNALED_PERMIT_MISMATCH",
            "Refresh restore intent under the current endpoint lease.",
        )


def _require_rollback_lifecycle_permit_binding(
    *,
    operation: VersionRestoreRollbackOperation,
    permit: MutationPermit,
    action: str,
    journaled: bool,
) -> None:
    record = operation.restore.record
    if (
        permit.run_id != f"version-restore-{action}:{operation.restore_id}"
        or permit.run_target_id
        != f"version-restore-{action}:{operation.restore_id}"
        or permit.endpoint_id != record.target_endpoint_id
        or permit.endpoint_revision_id != record.target_endpoint_revision_id
        or permit.endpoint_generation != record.endpoint_generation
        or permit.owner_installation_id != record.owner_installation_id
        or permit.ownership_epoch != record.ownership_epoch
        or permit.resource_key != f"endpoint:{record.target_endpoint_id}"
    ):
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_PERMIT_MISMATCH",
            "Reacquire the exact endpoint lease for the rollback lifecycle.",
        )
    if journaled and (
        operation.lease_id != permit.lease_id
        or operation.fencing_token != permit.fencing_token
    ):
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_JOURNALED_PERMIT_MISMATCH",
            "Refresh the rollback lifecycle under the current endpoint lease.",
        )


def _retained_object_paths(
    *,
    root: Path,
    object_id: str,
    object_role: str,
) -> tuple[Path, Path]:
    if _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_HISTORICAL_OBJECT_ID_INVALID",
            "Review the cataloged retained-version identifier before restoring.",
        )
    if object_role not in {OLD_TARGET_VERSION_ROLE, EMPTY_DIRECTORY_QUARANTINE_ROLE}:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_HISTORICAL_OBJECT_ROLE_INVALID",
            "Review the cataloged retained recovery-object role before restoring.",
        )
    object_root = (
        root
        / ".mediasync"
        / "objects"
        / (
            "quarantine"
            if object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE
            else "versions"
        )
    )
    return (
        object_root / f"{object_id}.payload",
        object_root / f"{object_id}.manifest.json",
    )


def _load_bound_version_manifest(
    *,
    manifest_path: Path,
    operation: VersionRestoreOperation,
) -> VersionObjectManifest:
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_HISTORICAL_MANIFEST_MISSING",
            "Keep the version protected and review its manifest.",
        )
    try:
        manifest = parse_version_object_manifest(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_HISTORICAL_MANIFEST_READ_FAILED",
            "Retry after checking access to the retained manifest.",
            retryable=True,
        ) from exc
    except VersionObjectManifestError as exc:
        raise VersionRestoreFilesystemError(
            exc.validation_code,
            "Keep the version protected and review its manifest.",
        ) from exc
    record = operation.record
    expected_fingerprint = _fingerprint_from_json(
        record.original_fingerprint_json,
        "VERSION_RESTORE_CATALOG_FINGERPRINT_INVALID",
    )
    if (
        manifest.version_object_id != record.version_object_id
        or manifest.object_role != record.object_role
        or manifest.operation_id != record.operation_id
        or manifest.run_id != record.run_id
        or manifest.run_target_id != record.run_target_id
        or manifest.job_id != record.job_id
        or manifest.job_revision_id != record.job_revision_id
        or manifest.target_endpoint_id != record.target_endpoint_id
        or manifest.target_endpoint_revision_id != record.target_endpoint_revision_id
        or manifest.endpoint_generation != record.endpoint_generation
        or manifest.owner_installation_id != record.owner_installation_id
        or manifest.ownership_epoch != record.ownership_epoch
        or manifest.final_relative_path != record.final_relative_path.replace("\\", "/")
        or manifest.created_utc != record.created_utc
        or manifest.retention_policy != record.retention_policy
        or manifest.retention_until_utc != record.retention_until_utc
        or manifest.manifest_hash != record.manifest_hash
        or manifest.fingerprint_json != _fingerprint_json(expected_fingerprint)
    ):
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_HISTORICAL_MANIFEST_BINDING_MISMATCH",
            "Keep the version protected and review its immutable binding.",
        )
    return manifest


def _rollback_manifest(
    *,
    operation: VersionRestoreOperation,
    fingerprint: dict[str, object],
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "object_role": "VERSION_RESTORE_ROLLBACK",
        "restore_id": operation.restore_id,
        "rollback_object_id": operation.rollback_object_id,
        "source_version_object_id": operation.record.version_object_id,
        "target_endpoint_id": operation.record.target_endpoint_id,
        "target_endpoint_revision_id": operation.record.target_endpoint_revision_id,
        "endpoint_generation": operation.record.endpoint_generation,
        "owner_installation_id": operation.record.owner_installation_id,
        "ownership_epoch": operation.record.ownership_epoch,
        "final_relative_path": operation.record.final_relative_path.replace("\\", "/"),
        "payload_name": f"{operation.rollback_object_id}.payload",
        "fingerprint": fingerprint,
        "created_utc": operation.created_utc,
        "retention_until_utc": operation.rollback_retention_until_utc,
        "manifest_hash_algorithm": "SHA-256",
        "canonicalization": "JSON_SORT_KEYS_COMPACT_UTF8_V1",
    }
    manifest_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    return {**body, "manifest_hash": manifest_hash}


def _load_rollback_manifest(
    *,
    manifest_path: Path,
    operation: VersionRestoreOperation,
) -> dict[str, object]:
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_MANIFEST_MISSING",
            "Keep the restore blocked and review the rollback object.",
        )
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_MANIFEST_INVALID",
            "Keep the restore blocked and review the rollback manifest.",
        ) from exc
    expected = _rollback_manifest(
        operation=operation,
        fingerprint=_fingerprint_from_json(
            operation.current_final_fingerprint_json,
            "VERSION_RESTORE_CURRENT_FINGERPRINT_MISSING",
        ),
    )
    if not isinstance(manifest, dict) or raw != _canonical_json(manifest) or manifest != expected:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_MANIFEST_BINDING_MISMATCH",
            "Keep the restore blocked and review the rollback manifest.",
        )
    return manifest


def _write_canonical_manifest(
    *,
    manifest_path: Path,
    manifest: Mapping[str, object],
) -> None:
    canonical = _canonical_json(manifest)
    if manifest_path.exists():
        try:
            existing = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_MANIFEST_READ_FAILED",
                "Retry after checking access to the rollback manifest.",
                retryable=True,
            ) from exc
        if existing != canonical:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_MANIFEST_CONFLICT",
                "Keep the restore blocked and review the rollback manifest.",
            )
        return
    temp_path = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            handle.write(canonical)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, manifest_path)
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_MANIFEST_WRITE_FAILED",
            "Retry after checking endpoint control-area permissions.",
            retryable=True,
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _preserve_payload(
    *,
    source: Path,
    destination: Path,
    expected: dict[str, object],
) -> None:
    if destination.exists() or destination.is_symlink():
        observed = _require_regular_file_fingerprint(
            destination,
            missing_code="VERSION_RESTORE_ROLLBACK_PAYLOAD_MISSING",
            invalid_code="VERSION_RESTORE_ROLLBACK_PAYLOAD_TYPE_INVALID",
            read_code="VERSION_RESTORE_ROLLBACK_PAYLOAD_READ_FAILED",
        )
        if observed != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_ROLLBACK_PAYLOAD_CONFLICT",
                "Keep the restore blocked and review the rollback payload.",
            )
        return
    temp_path = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        _copy_file_durable(source=source, destination=temp_path)
        if _fingerprint_file(temp_path) != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_CURRENT_FINAL_CHANGED_DURING_PRESERVE",
                "Review the current final before retrying restore.",
            )
        os.replace(temp_path, destination)
    except VersionRestoreFilesystemError:
        raise
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ROLLBACK_PAYLOAD_WRITE_FAILED",
            "Retry after checking endpoint control-area permissions.",
            retryable=True,
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _replace_with_verified_payload(
    *,
    source: Path,
    final_path: Path,
    expected: dict[str, object],
) -> None:
    temp_path = final_path.with_name(
        f".{final_path.name}.{uuid4().hex}.mediasync-version-restore.tmp"
    )
    try:
        _copy_file_durable(source=source, destination=temp_path)
        if _fingerprint_file(temp_path) != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_TEMP_FINGERPRINT_MISMATCH",
                "Keep the rollback object and review the historical payload.",
            )
        os.replace(temp_path, final_path)
    except VersionRestoreFilesystemError:
        raise
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_ATOMIC_REPLACE_FAILED",
            "Retry from the journal after checking final-path permissions.",
            retryable=True,
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _apply_empty_directory_restore(
    *,
    final_path: Path,
    expected_current: dict[str, object],
) -> None:
    if final_path.is_symlink():
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
            "Review the final path before restoring the quarantined directory.",
        )
    if final_path.is_dir():
        _require_empty_directory_fingerprint(
            final_path,
            missing_code="VERSION_RESTORE_FINAL_MISSING_AFTER_APPLY",
            invalid_code="VERSION_RESTORE_FINAL_TYPE_INVALID_AFTER_APPLY",
            not_empty_code="VERSION_RESTORE_FINAL_DIRECTORY_NOT_EMPTY",
        )
        return
    if final_path.exists():
        observed = _require_regular_file_fingerprint(
            final_path,
            missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
            invalid_code="VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
            read_code="VERSION_RESTORE_CURRENT_FINAL_READ_FAILED",
        )
        if observed != expected_current:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_CURRENT_FINAL_CHANGED_BEFORE_APPLY",
                "Review the final file and rollback object before resuming restore.",
            )
        try:
            final_path.unlink()
        except OSError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_DIRECTORY_REPLACE_REMOVE_FAILED",
                "Retry from the restore journal after checking final-path permissions.",
                retryable=True,
            ) from exc
    try:
        final_path.mkdir()
    except FileExistsError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_DIRECTORY_TARGET_REAPPEARED",
            "Review the final path before retrying the quarantined-directory restore.",
        ) from exc
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_DIRECTORY_CREATE_FAILED",
            "Retry from the restore journal after checking final-path permissions.",
            retryable=True,
        ) from exc


def _replace_empty_directory_with_verified_file(
    *,
    source: Path,
    final_path: Path,
    expected: dict[str, object],
) -> None:
    if final_path.is_symlink():
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_UNDO_FINAL_TYPE_INVALID",
            "Review the final path before undoing the directory restore.",
        )
    if final_path.is_dir():
        _require_empty_directory_fingerprint(
            final_path,
            missing_code="VERSION_RESTORE_UNDO_FINAL_MISSING",
            invalid_code="VERSION_RESTORE_UNDO_FINAL_TYPE_INVALID",
            not_empty_code="VERSION_RESTORE_UNDO_FINAL_DIRECTORY_NOT_EMPTY",
        )
        try:
            final_path.rmdir()
        except OSError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_DIRECTORY_REMOVE_FAILED",
                "Retry from the undo journal after checking final-path permissions.",
                retryable=True,
            ) from exc
    elif final_path.exists():
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_UNDO_FINAL_CHANGED_BEFORE_APPLY",
            "Keep the rollback object and review the changed final path.",
        )
    temp_path = final_path.with_name(
        f".{final_path.name}.{uuid4().hex}.mediasync-quarantine-undo.tmp"
    )
    try:
        _copy_file_durable(source=source, destination=temp_path)
        if _fingerprint_file(temp_path) != expected:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_TEMP_FINGERPRINT_MISMATCH",
                "Keep the rollback object and review its payload.",
            )
        try:
            os.link(temp_path, final_path)
        except FileExistsError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_TARGET_REAPPEARED",
                "Review the final path before retrying undo.",
            ) from exc
        except OSError as exc:
            raise VersionRestoreFilesystemError(
                "VERSION_RESTORE_UNDO_NO_OVERWRITE_FAILED",
                "Retry from the undo journal after checking endpoint support.",
                retryable=True,
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _copy_file_durable(*, source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as source_handle, destination.open("xb") as target_handle:
            while chunk := source_handle.read(1024 * 1024):
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_COPY_FAILED",
            "Retry after checking source and destination access.",
            retryable=True,
        ) from exc


def _current_final_fingerprint(
    path: Path,
    *,
    allow_missing: bool = False,
) -> dict[str, object] | None:
    if not path.exists() and not path.is_symlink():
        if allow_missing:
            return None
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_CURRENT_FINAL_MISSING",
            "Review the restore paths before retrying.",
        )
    if path.is_dir() and not path.is_symlink():
        return _require_empty_directory_fingerprint(
            path,
            missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
            invalid_code="VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
            not_empty_code="VERSION_RESTORE_CURRENT_FINAL_DIRECTORY_NOT_EMPTY",
        )
    return _require_regular_file_fingerprint(
        path,
        missing_code="VERSION_RESTORE_CURRENT_FINAL_MISSING",
        invalid_code="VERSION_RESTORE_CURRENT_FINAL_TYPE_INVALID",
        read_code="VERSION_RESTORE_CURRENT_FINAL_READ_FAILED",
    )


def _restored_final_fingerprint(
    *,
    final_path: Path,
    object_role: str,
) -> dict[str, object]:
    if object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE:
        return _require_empty_directory_fingerprint(
            final_path,
            missing_code="VERSION_RESTORE_FINAL_MISSING_AFTER_APPLY",
            invalid_code="VERSION_RESTORE_FINAL_TYPE_INVALID_AFTER_APPLY",
            not_empty_code="VERSION_RESTORE_FINAL_DIRECTORY_NOT_EMPTY",
        )
    return _require_regular_file_fingerprint(
        final_path,
        missing_code="VERSION_RESTORE_FINAL_MISSING_AFTER_APPLY",
        invalid_code="VERSION_RESTORE_FINAL_TYPE_INVALID_AFTER_APPLY",
        read_code="VERSION_RESTORE_FINAL_READ_FAILED_AFTER_APPLY",
    )


def _require_empty_directory_fingerprint(
    path: Path,
    *,
    missing_code: str,
    invalid_code: str,
    not_empty_code: str,
) -> dict[str, object]:
    if not path.exists():
        raise VersionRestoreFilesystemError(
            missing_code,
            "Review the restore paths before retrying.",
        )
    if path.is_symlink() or not path.is_dir():
        raise VersionRestoreFilesystemError(
            invalid_code,
            "Review the restore paths before retrying.",
        )
    try:
        if next(path.iterdir(), None) is not None:
            raise VersionRestoreFilesystemError(
                not_empty_code,
                "Do not replace a directory whose contents changed.",
            )
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            "VERSION_RESTORE_DIRECTORY_READ_FAILED",
            "Retry after checking directory access.",
            retryable=True,
        ) from exc
    return {"entry_count": 0, "kind": "DIRECTORY_EMPTY"}


def _require_regular_file_fingerprint(
    path: Path,
    *,
    missing_code: str,
    invalid_code: str,
    read_code: str,
) -> dict[str, object]:
    if not path.exists():
        raise VersionRestoreFilesystemError(
            missing_code,
            "Review the restore paths before retrying.",
        )
    if path.is_symlink() or not path.is_file():
        raise VersionRestoreFilesystemError(
            invalid_code,
            "Review the restore paths before retrying.",
        )
    try:
        return _fingerprint_file(path)
    except OSError as exc:
        raise VersionRestoreFilesystemError(
            read_code,
            "Retry after checking file access.",
            retryable=True,
        ) from exc


def _fingerprint_file(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            byte_count += len(chunk)
            digest.update(chunk)
    return {"byte_count": byte_count, "content_hash": digest.hexdigest()}


def _fingerprint_from_json(
    raw: str | None,
    validation_code: str,
) -> dict[str, object]:
    if raw is None:
        raise VersionRestoreFilesystemError(
            validation_code,
            "Reload the restore journal before continuing.",
        )
    try:
        canonical = canonical_fingerprint_json(raw)
        value = json.loads(canonical)
    except (ValueError, json.JSONDecodeError) as exc:
        raise VersionRestoreFilesystemError(
            validation_code,
            "Reload the restore journal before continuing.",
        ) from exc
    assert isinstance(value, dict)
    return value


def _fingerprint_json(fingerprint: Mapping[str, object]) -> str:
    return canonical_fingerprint_json(_canonical_json(fingerprint))


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _rollback_delete_receipt(
    operation: VersionRestoreRollbackOperation,
) -> VersionRestoreRollbackDeleteReceipt:
    return VersionRestoreRollbackDeleteReceipt(
        restore_id=operation.restore_id,
        rollback_object_id=operation.rollback_object_id,
        manifest_hash=operation.rollback_manifest_hash,
    )
