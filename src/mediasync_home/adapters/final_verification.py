from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootResolver
from mediasync_home.adapters.reparse_guard import (
    LocalReparseGuard,
    ReparseGuard,
    ReparseGuardError,
)
from mediasync_home.application.ports import FinalArtifactVerificationEvidence
from mediasync_home.application.recovery_operations import RecoveryOperation
from mediasync_home.application.safe_paths import SafePathViolation, parse_endpoint_relative_path


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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
    ) -> None:
        self._root_resolver = root_resolver
        self._reparse_guard = reparse_guard or LocalReparseGuard()

    def verify_final_artifact(
        self,
        operation: RecoveryOperation,
    ) -> FinalArtifactVerificationEvidence:
        expected = _expected_fingerprint(operation)
        final_path = self._final_path(operation)
        if not final_path.is_file() or final_path.is_symlink():
            raise FinalArtifactVerificationError(
                "FINAL_ARTIFACT_VERIFY_FILE_MISSING",
                "Reacquire the endpoint lease and inspect final filesystem state manually.",
            )
        actual = _fingerprint_file(final_path)
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
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_EXPECTED_FINGERPRINT_INVALID",
            "Refresh recovery fingerprint evidence before final verification.",
        ) from exc
    if not isinstance(payload, dict):
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_EXPECTED_FINGERPRINT_INVALID",
            "Refresh recovery fingerprint evidence before final verification.",
        )
    content_hash = payload.get("content_hash")
    byte_count = payload.get("byte_count")
    if not isinstance(content_hash, str) or HASH_PATTERN.fullmatch(content_hash) is None:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_EXPECTED_FINGERPRINT_INVALID",
            "Refresh recovery fingerprint evidence before final verification.",
        )
    if not isinstance(byte_count, int) or byte_count < 0:
        raise FinalArtifactVerificationError(
            "FINAL_ARTIFACT_VERIFY_EXPECTED_FINGERPRINT_INVALID",
            "Refresh recovery fingerprint evidence before final verification.",
        )
    return {"byte_count": byte_count, "content_hash": content_hash}


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
