from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootResolver
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseGuardError
from mediasync_home.application.version_objects import (
    EMPTY_DIRECTORY_QUARANTINE_ROLE,
    OLD_TARGET_VERSION_ROLE,
    VersionObjectManifest,
    VersionObjectManifestError,
    parse_version_object_manifest,
)
from mediasync_home.application.version_retention import (
    VersionRetentionDeleteReceipt,
    VersionRetentionPermitValidator,
    VersionRetentionWorkItem,
)
from mediasync_home.domain.capabilities import MutationPermit


_OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class VersionRetentionDeletionError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class LocalVersionRetentionDeletionAdapter:
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        reparse_guard: LocalReparseGuard | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._reparse_guard = reparse_guard or LocalReparseGuard()

    def verify_retained_version(
        self,
        *,
        permit_validator: VersionRetentionPermitValidator,
        permit: MutationPermit,
        item: VersionRetentionWorkItem,
    ) -> None:
        permit_validator.assert_mutation_permit_current(permit)
        _require_permit_binding(item=item, permit=permit)
        payload_path, manifest_path = self._object_paths(item=item, permit=permit)
        manifest = _load_bound_manifest(manifest_path=manifest_path, item=item)
        _require_payload_matches_manifest(
            payload_path=payload_path,
            manifest=manifest,
        )

    def delete_retained_version(
        self,
        *,
        permit_validator: VersionRetentionPermitValidator,
        permit: MutationPermit,
        item: VersionRetentionWorkItem,
        resuming_delete_intent: bool,
    ) -> VersionRetentionDeleteReceipt:
        permit_validator.assert_mutation_permit_current(permit)
        _require_permit_binding(item=item, permit=permit)
        payload_path, manifest_path = self._object_paths(item=item, permit=permit)
        payload_exists = payload_path.exists()
        manifest_exists = manifest_path.exists()
        if not payload_exists and not manifest_exists:
            if not resuming_delete_intent:
                raise VersionRetentionDeletionError(
                    "VERSION_RETENTION_OBJECT_PAIR_MISSING",
                    "Reconcile the retained version before recording it as deleted.",
                )
            return _receipt(item)
        if not manifest_exists:
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_MANIFEST_MISSING",
                "Do not delete an object whose ownership manifest is missing.",
            )
        manifest = _load_bound_manifest(manifest_path=manifest_path, item=item)
        if payload_exists:
            _require_payload_matches_manifest(payload_path=payload_path, manifest=manifest)
            try:
                if manifest.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE:
                    payload_path.rmdir()
                else:
                    payload_path.unlink()
            except OSError as exc:
                raise VersionRetentionDeletionError(
                    "VERSION_RETENTION_PAYLOAD_DELETE_FAILED",
                    "Retry retained-version expiry after checking endpoint permissions.",
                ) from exc
        elif not resuming_delete_intent:
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_PAYLOAD_MISSING",
                "Reconcile the retained version because its payload disappeared before deletion.",
            )
        try:
            manifest_path.unlink()
        except OSError as exc:
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_MANIFEST_DELETE_FAILED",
                "Retry retained-version expiry from its recorded delete intent.",
            ) from exc
        if payload_path.exists() or manifest_path.exists():
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_DELETE_POSTCONDITION_FAILED",
                "Reconcile the object pair because expiry could not verify deletion.",
            )
        return _receipt(item)

    def _object_paths(
        self,
        *,
        item: VersionRetentionWorkItem,
        permit: MutationPermit,
    ) -> tuple[Path, Path]:
        object_id = item.record.version_object_id
        if _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_OBJECT_ID_INVALID",
                "Reconcile the retained-version catalog identifier before expiry.",
            )
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=permit.resource_key,
                endpoint_id=item.record.target_endpoint_id,
                endpoint_revision_id=item.record.target_endpoint_revision_id,
            )
        except EndpointLeaseUnavailable as exc:
            raise VersionRetentionDeletionError(exc.validation_code, exc.next_action) from exc
        if root is None:
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_ENDPOINT_ROOT_MISSING",
                "Reconnect the exact endpoint revision before retained-version expiry.",
            )
        try:
            resolved_root = self._reparse_guard.resolve_existing_root(
                Path(root),
                missing_code="VERSION_RETENTION_ENDPOINT_ROOT_MISSING",
                missing_next_action="Reconnect the endpoint before retained-version expiry.",
                reparse_code="VERSION_RETENTION_ENDPOINT_ROOT_REPARSE_UNSUPPORTED",
                reparse_next_action="Revalidate the endpoint root before retained-version expiry.",
            )
            if item.record.object_role not in {
                OLD_TARGET_VERSION_ROLE,
                EMPTY_DIRECTORY_QUARANTINE_ROLE,
            }:
                raise VersionRetentionDeletionError(
                    "VERSION_RETENTION_OBJECT_ROLE_INVALID",
                    "Reconcile the retained recovery-object role before expiry.",
                )
            object_root = (
                resolved_root
                / ".mediasync"
                / "objects"
                / (
                    "quarantine"
                    if item.record.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE
                    else "versions"
                )
            )
            self._reparse_guard.require_resolved_under_root(
                root=resolved_root,
                path=object_root,
                strict=True,
                escape_code="VERSION_RETENTION_OBJECT_STORE_INVALID",
                escape_next_action="Revalidate the endpoint object store before expiry.",
            )
        except ReparseGuardError as exc:
            raise VersionRetentionDeletionError(exc.validation_code, exc.next_action) from exc
        return (
            object_root / f"{object_id}.payload",
            object_root / f"{object_id}.manifest.json",
        )


def _load_bound_manifest(
    *,
    manifest_path: Path,
    item: VersionRetentionWorkItem,
) -> VersionObjectManifest:
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_MANIFEST_TYPE_INVALID",
            "Do not delete an object whose manifest is not a regular file.",
        )
    try:
        manifest = parse_version_object_manifest(manifest_path.read_text(encoding="utf-8"))
    except (OSError, VersionObjectManifestError) as exc:
        code = getattr(exc, "validation_code", "VERSION_RETENTION_MANIFEST_INVALID")
        raise VersionRetentionDeletionError(
            str(code),
            "Reconcile the retained-version manifest before expiry.",
        ) from exc
    record = item.record
    try:
        original_fingerprint = json.loads(record.original_fingerprint_json)
    except json.JSONDecodeError as exc:
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_CATALOG_FINGERPRINT_INVALID",
            "Reconcile the cataloged retained-version fingerprint before expiry.",
        ) from exc
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
        or manifest.retention_policy != record.retention_policy
        or manifest.created_utc != record.created_utc
        or manifest.retention_until_utc != record.retention_until_utc
        or manifest.manifest_hash != record.manifest_hash
        or manifest.fingerprint_json
        != json.dumps(original_fingerprint, sort_keys=True, separators=(",", ":"))
    ):
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_MANIFEST_BINDING_MISMATCH",
            "Do not delete a version whose manifest differs from the immutable plan.",
        )
    return manifest


def _require_payload_matches_manifest(
    *,
    payload_path: Path,
    manifest: VersionObjectManifest,
) -> None:
    if manifest.object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE:
        if payload_path.is_symlink() or not payload_path.is_dir():
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_PAYLOAD_TYPE_INVALID",
                "Do not delete a quarantine path that is not a directory.",
            )
        try:
            if next(payload_path.iterdir(), None) is not None:
                raise VersionRetentionDeletionError(
                    "VERSION_RETENTION_QUARANTINE_NOT_EMPTY",
                    "Do not delete a quarantined directory whose contents changed.",
                )
        except OSError as exc:
            raise VersionRetentionDeletionError(
                "VERSION_RETENTION_PAYLOAD_READ_FAILED",
                "Retry after checking access to the quarantined directory.",
            ) from exc
        return
    if not payload_path.is_file() or payload_path.is_symlink():
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_PAYLOAD_TYPE_INVALID",
            "Do not delete a retained-version path that is not a regular file.",
        )
    if manifest.fingerprint_byte_count is None or manifest.fingerprint_content_hash is None:
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_MANIFEST_FINGERPRINT_INVALID",
            "Reconcile the retained-version manifest before expiry.",
        )
    byte_count, content_hash = _fingerprint_file(payload_path)
    if (
        byte_count != manifest.fingerprint_byte_count
        or content_hash != manifest.fingerprint_content_hash
    ):
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_PAYLOAD_FINGERPRINT_MISMATCH",
            "Do not delete retained bytes that differ from their immutable manifest.",
        )


def _fingerprint_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                byte_count += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_PAYLOAD_READ_FAILED",
            "Retry after checking access to the retained version payload.",
        ) from exc
    return byte_count, digest.hexdigest()


def _require_permit_binding(
    *,
    item: VersionRetentionWorkItem,
    permit: MutationPermit,
) -> None:
    if (
        permit.run_id != f"version-retention:{item.plan_id}"
        or permit.run_target_id != f"version-retention:{item.plan_id}:{item.ordinal}"
        or permit.endpoint_id != item.record.target_endpoint_id
        or permit.endpoint_revision_id != item.record.target_endpoint_revision_id
        or permit.endpoint_generation != item.record.endpoint_generation
        or permit.owner_installation_id != item.record.owner_installation_id
        or permit.ownership_epoch != item.record.ownership_epoch
        or permit.resource_key != f"endpoint:{item.record.target_endpoint_id}"
    ):
        raise VersionRetentionDeletionError(
            "VERSION_RETENTION_PERMIT_MISMATCH",
            "Reacquire the exact endpoint lease before retained-version expiry.",
        )


def _receipt(item: VersionRetentionWorkItem) -> VersionRetentionDeleteReceipt:
    return VersionRetentionDeleteReceipt(
        plan_id=item.plan_id,
        version_object_id=item.record.version_object_id,
        manifest_hash=item.record.manifest_hash,
    )
