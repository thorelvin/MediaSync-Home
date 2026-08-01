from __future__ import annotations

import json
from pathlib import Path

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootResolver
from mediasync_home.adapters.file_object_fingerprints import (
    LocalFileObjectFingerprintAdapter,
    LocalFileObjectFingerprintError,
)
from mediasync_home.adapters.reparse_guard import (
    LocalReparseGuard,
    ReparseGuard,
    ReparseGuardError,
)
from mediasync_home.application.ports import FinalArtifactVerificationEvidence
from mediasync_home.application.file_object_fingerprints import (
    FileObjectFingerprintError,
    file_object_fingerprint_from_json,
    has_named_stream_inventory,
)
from mediasync_home.application.directory_artifacts import directory_artifact_fingerprint, directory_artifact_matches
from mediasync_home.application.recovery_operations import RecoveryOperation, RecoveryOperationKind
from mediasync_home.application.safe_paths import SafePathViolation, parse_endpoint_relative_path


class FinalArtifactVerificationError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class LocalFinalArtifactVerificationAdapter:
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        reparse_guard: ReparseGuard | None = None,
        file_object_fingerprints: LocalFileObjectFingerprintAdapter | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._reparse_guard = reparse_guard or LocalReparseGuard()
        self._file_object_fingerprints = (
            file_object_fingerprints or LocalFileObjectFingerprintAdapter()
        )

    def verify_final_artifact(
        self,
        operation: RecoveryOperation,
    ) -> FinalArtifactVerificationEvidence:
        expected = _expected_fingerprint(operation)
        final_path = self._final_path(operation)
        if operation.operation_kind is RecoveryOperationKind.CREATE_DIRECTORY:
            if not directory_artifact_matches(
                final_path,
                run_id=operation.run_id,
                run_target_id=operation.run_target_id,
                operation_id=operation.operation_id,
                final_relative_path=operation.final_relative_path,
            ):
                raise FinalArtifactVerificationError(
                    "FINAL_ARTIFACT_VERIFY_DIRECTORY_MARKER_MISMATCH",
                    "Reacquire the endpoint lease and inspect the created directory state.",
                )
            actual_directory = directory_artifact_fingerprint(
                run_id=operation.run_id,
                run_target_id=operation.run_target_id,
                operation_id=operation.operation_id,
                final_relative_path=operation.final_relative_path,
            )
            if actual_directory != expected:
                raise FinalArtifactVerificationError(
                    "FINAL_ARTIFACT_VERIFY_DIRECTORY_FINGERPRINT_MISMATCH",
                    "Reload recovery evidence before cataloging the created directory.",
                )
            return FinalArtifactVerificationEvidence(
                fingerprint_json=_canonical_json(actual_directory)
            )
        if not final_path.is_file() or final_path.is_symlink():
            raise FinalArtifactVerificationError(
                "FINAL_ARTIFACT_VERIFY_FILE_MISSING",
                "Reacquire the endpoint lease and inspect final filesystem state manually.",
            )
        try:
            actual = self._file_object_fingerprints.fingerprint(final_path)
        except LocalFileObjectFingerprintError as exc:
            raise FinalArtifactVerificationError(
                exc.validation_code,
                "Reverify final filesystem state after every data stream is readable.",
            ) from exc
        if not has_named_stream_inventory(expected):
            actual = {
                "byte_count": actual["byte_count"],
                "content_hash": actual["content_hash"],
            }
        if actual != expected:
            raise FinalArtifactVerificationError(
                "FINAL_ARTIFACT_VERIFY_FINGERPRINT_MISMATCH",
                "Reverify final filesystem state before catalog handoff.",
            )
        return FinalArtifactVerificationEvidence(fingerprint_json=_canonical_json(actual))

    def _final_path(self, operation: RecoveryOperation) -> Path:
        root = self._resolve_target_root(operation)
        final_path = root.joinpath(*_relative_parts(operation.final_relative_path))
        _reject_reparse_in_path(
            guard=self._reparse_guard,
            root=root,
            path=final_path.parent,
        )
        try:
            final_path.resolve(strict=False).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise FinalArtifactVerificationError(
                "FINAL_ARTIFACT_VERIFY_PATH_ESCAPES_ROOT",
                "Refresh endpoint adoption before verifying final artifact state.",
            ) from exc
        return final_path

    def _resolve_target_root(self, operation: RecoveryOperation) -> Path:
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=operation.lease_resource_key,
                endpoint_id=operation.target_endpoint_id,
                endpoint_revision_id=operation.target_endpoint_revision_id,
            )
        except EndpointLeaseUnavailable:
            raise
        if root is None:
            raise FinalArtifactVerificationError(
                "FINAL_ARTIFACT_VERIFY_ENDPOINT_ROOT_UNKNOWN",
                "Register endpoint roots before verifying final artifact state.",
            )
        try:
            return self._reparse_guard.resolve_existing_root(
                root,
                missing_code="FINAL_ARTIFACT_VERIFY_ENDPOINT_ROOT_MISSING",
                missing_next_action=(
                    "Ensure the target endpoint root is reachable before recovery resume."
                ),
                reparse_code="FINAL_ARTIFACT_VERIFY_ENDPOINT_ROOT_REPARSE_UNSUPPORTED",
                reparse_next_action=(
                    "Revalidate endpoint adoption before verifying through this root."
                ),
            )
        except ReparseGuardError as exc:
            raise FinalArtifactVerificationError(
                exc.validation_code,
                exc.next_action,
            ) from exc


def _expected_fingerprint(operation: RecoveryOperation) -> dict[str, object]:
    raw_payload = operation.expected_final_fingerprint_json or operation.expected_staging_fingerprint_json
    if raw_payload is None:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_REQUIRES_EXPECTED_FINGERPRINT",
            "Reverify final filesystem state with explicit fingerprint evidence.",
        )
    try:
        return file_object_fingerprint_from_json(raw_payload)
    except FileObjectFingerprintError as exc:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_EXPECTED_FINGERPRINT_INVALID",
            "Refresh recovery fingerprint evidence before final verification.",
        ) from exc


def _relative_parts(value: str) -> tuple[str, ...]:
    try:
        return parse_endpoint_relative_path(value).parts
    except SafePathViolation as exc:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_REQUIRES_RELATIVE_PATH",
            "Refresh analysis so recovery operations use endpoint-relative final paths.",
        ) from exc


def _reject_reparse_in_path(*, guard: ReparseGuard, root: Path, path: Path) -> None:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError as exc:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_PATH_ESCAPES_ROOT",
            "Refresh endpoint adoption before verifying final artifact state.",
        ) from exc
    try:
        guard.reject_reparse_chain(
            root=root,
            relative_parts=relative_parts,
            missing_code="FINAL_ARTIFACT_VERIFY_PATH_CHAIN_MISSING",
            missing_next_action="Refresh recovery state because the final path chain changed.",
            reparse_code="FINAL_ARTIFACT_VERIFY_REPARSE_UNSUPPORTED",
            reparse_next_action=(
                "Revalidate paths with a production ReparseGuard before recovery resume."
            ),
        )
        guard.require_resolved_under_root(
            root=root,
            path=path,
            strict=True,
            escape_code="FINAL_ARTIFACT_VERIFY_PATH_ESCAPES_ROOT",
            escape_next_action="Refresh endpoint adoption before verifying final artifact state.",
        )
    except ReparseGuardError as exc:
        raise FinalArtifactVerificationError(exc.validation_code, exc.next_action) from exc


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
