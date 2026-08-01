from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationKind,
)
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)


STAGING_OBJECT_MANIFEST_SCHEMA_VERSION = 1
STAGING_OBJECT_MANIFEST_HASH_ALGORITHM = "SHA-256"
STAGING_OBJECT_MANIFEST_CANONICALIZATION = "JSON_SORT_KEYS_COMPACT_UTF8_V1"
_OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "object_role",
        "staging_object_id",
        "operation_id",
        "operation_kind",
        "run_id",
        "run_target_id",
        "target_endpoint_id",
        "target_endpoint_revision_id",
        "endpoint_generation",
        "source_endpoint_id",
        "source_endpoint_revision_id",
        "source_relative_path",
        "final_relative_path",
        "payload_name",
        "fingerprint",
        "manifest_hash_algorithm",
        "canonicalization",
        "manifest_hash",
    }
)


class StagingObjectManifestError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass(frozen=True, slots=True)
class StagingObjectManifest:
    staging_object_id: str
    operation_id: str
    operation_kind: RecoveryOperationKind
    run_id: str
    run_target_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    endpoint_generation: int
    source_endpoint_id: str | None
    source_endpoint_revision_id: str | None
    source_relative_path: str | None
    final_relative_path: str
    payload_name: str
    fingerprint_byte_count: int
    fingerprint_content_hash: str
    manifest_hash: str
    canonical_json: str


def staging_object_manifest_from_operation(
    operation: RecoveryOperation,
) -> StagingObjectManifest:
    if operation.staging_object_id is None:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_OBJECT_ID_MISSING")
    fingerprint = _fingerprint_from_json(operation.expected_source_fingerprint_json)
    return create_staging_object_manifest(
        staging_object_id=operation.staging_object_id,
        operation_id=operation.operation_id,
        operation_kind=operation.operation_kind,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        endpoint_generation=operation.endpoint_generation,
        source_endpoint_id=operation.source_endpoint_id,
        source_endpoint_revision_id=operation.source_endpoint_revision_id,
        source_relative_path=operation.source_relative_path,
        final_relative_path=operation.final_relative_path,
        fingerprint=fingerprint,
    )


def create_staging_object_manifest(
    *,
    staging_object_id: str,
    operation_id: str,
    operation_kind: RecoveryOperationKind,
    run_id: str,
    run_target_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    source_endpoint_id: str | None,
    source_endpoint_revision_id: str | None,
    source_relative_path: str | None,
    final_relative_path: str,
    fingerprint: Mapping[str, object],
) -> StagingObjectManifest:
    _require_object_id(staging_object_id)
    _require_nonempty(operation_id)
    _require_nonempty(run_id)
    _require_nonempty(run_target_id)
    _require_nonempty(target_endpoint_id)
    _require_nonempty(target_endpoint_revision_id)
    if (
        not isinstance(endpoint_generation, int)
        or isinstance(endpoint_generation, bool)
        or endpoint_generation < 1
    ):
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_ENDPOINT_GENERATION_INVALID"
        )
    _require_optional_binding(source_endpoint_id, source_endpoint_revision_id)
    canonical_source_path = (
        None
        if source_relative_path is None
        else _canonical_relative_path(source_relative_path)
    )
    canonical_final_path = _canonical_relative_path(final_relative_path)
    byte_count, content_hash = _fingerprint_values(fingerprint)
    body: dict[str, object] = {
        "schema_version": STAGING_OBJECT_MANIFEST_SCHEMA_VERSION,
        "object_role": "STAGING",
        "staging_object_id": staging_object_id,
        "operation_id": operation_id,
        "operation_kind": operation_kind.value,
        "run_id": run_id,
        "run_target_id": run_target_id,
        "target_endpoint_id": target_endpoint_id,
        "target_endpoint_revision_id": target_endpoint_revision_id,
        "endpoint_generation": endpoint_generation,
        "source_endpoint_id": source_endpoint_id,
        "source_endpoint_revision_id": source_endpoint_revision_id,
        "source_relative_path": canonical_source_path,
        "final_relative_path": canonical_final_path,
        "payload_name": f"{staging_object_id}.payload",
        "fingerprint": {
            "byte_count": byte_count,
            "content_hash": content_hash,
        },
        "manifest_hash_algorithm": STAGING_OBJECT_MANIFEST_HASH_ALGORITHM,
        "canonicalization": STAGING_OBJECT_MANIFEST_CANONICALIZATION,
    }
    manifest_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    payload = {**body, "manifest_hash": manifest_hash}
    canonical_json = _canonical_json(payload)
    return _manifest_from_valid_payload(payload, canonical_json=canonical_json)


def parse_staging_object_manifest(raw_manifest: str) -> StagingObjectManifest:
    try:
        payload = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_JSON_INVALID"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_SHAPE_INVALID")
    canonical_json = _canonical_json(payload)
    if raw_manifest != canonical_json:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_NOT_CANONICAL")
    if payload.get("schema_version") != STAGING_OBJECT_MANIFEST_SCHEMA_VERSION:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_SCHEMA_UNSUPPORTED")
    if payload.get("object_role") != "STAGING":
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_ROLE_INVALID")
    if payload.get("manifest_hash_algorithm") != STAGING_OBJECT_MANIFEST_HASH_ALGORITHM:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_HASH_ALGORITHM_INVALID"
        )
    if payload.get("canonicalization") != STAGING_OBJECT_MANIFEST_CANONICALIZATION:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_CANONICALIZATION_INVALID"
        )
    manifest_hash = payload.get("manifest_hash")
    if not isinstance(manifest_hash, str) or _HASH_PATTERN.fullmatch(manifest_hash) is None:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_HASH_INVALID")
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    expected_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    if manifest_hash != expected_hash:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_HASH_MISMATCH")
    return _manifest_from_valid_payload(payload, canonical_json=canonical_json)


def require_staging_object_manifest_binding(
    manifest: StagingObjectManifest,
    *,
    staging_object_id: str,
    run_id: str,
    run_target_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    final_relative_path: str,
    operation_kind: RecoveryOperationKind,
    fingerprint_content_hash: str,
    operation_id: str | None = None,
) -> None:
    expected_final_path = _canonical_relative_path(final_relative_path)
    if (
        manifest.staging_object_id != staging_object_id
        or manifest.run_id != run_id
        or manifest.run_target_id != run_target_id
        or manifest.target_endpoint_id != target_endpoint_id
        or manifest.target_endpoint_revision_id != target_endpoint_revision_id
        or manifest.endpoint_generation != endpoint_generation
        or manifest.final_relative_path != expected_final_path
        or manifest.operation_kind is not operation_kind
        or manifest.fingerprint_content_hash != fingerprint_content_hash
        or (operation_id is not None and manifest.operation_id != operation_id)
    ):
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_BINDING_MISMATCH"
        )


def _manifest_from_valid_payload(
    payload: Mapping[str, object],
    *,
    canonical_json: str,
) -> StagingObjectManifest:
    staging_object_id = _require_string(payload.get("staging_object_id"))
    _require_object_id(staging_object_id)
    operation_id = _require_string(payload.get("operation_id"))
    run_id = _require_string(payload.get("run_id"))
    run_target_id = _require_string(payload.get("run_target_id"))
    target_endpoint_id = _require_string(payload.get("target_endpoint_id"))
    target_endpoint_revision_id = _require_string(
        payload.get("target_endpoint_revision_id")
    )
    endpoint_generation = payload.get("endpoint_generation")
    if (
        not isinstance(endpoint_generation, int)
        or isinstance(endpoint_generation, bool)
        or endpoint_generation < 1
    ):
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_ENDPOINT_GENERATION_INVALID"
        )
    try:
        operation_kind = RecoveryOperationKind(
            _require_string(payload.get("operation_kind"))
        )
    except ValueError as exc:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_OPERATION_KIND_INVALID"
        ) from exc
    source_endpoint_id = _optional_string(payload.get("source_endpoint_id"))
    source_endpoint_revision_id = _optional_string(
        payload.get("source_endpoint_revision_id")
    )
    _require_optional_binding(source_endpoint_id, source_endpoint_revision_id)
    source_relative_path = _optional_string(payload.get("source_relative_path"))
    if (
        source_relative_path is not None
        and _canonical_relative_path(source_relative_path) != source_relative_path
    ):
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_SOURCE_PATH_NOT_CANONICAL"
        )
    final_relative_path = _require_string(payload.get("final_relative_path"))
    if _canonical_relative_path(final_relative_path) != final_relative_path:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_FINAL_PATH_NOT_CANONICAL"
        )
    payload_name = _require_string(payload.get("payload_name"))
    if payload_name != f"{staging_object_id}.payload":
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_PAYLOAD_NAME_INVALID")
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    byte_count, content_hash = _fingerprint_values(fingerprint)
    manifest_hash = _require_string(payload.get("manifest_hash"))
    return StagingObjectManifest(
        staging_object_id=staging_object_id,
        operation_id=operation_id,
        operation_kind=operation_kind,
        run_id=run_id,
        run_target_id=run_target_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        endpoint_generation=endpoint_generation,
        source_endpoint_id=source_endpoint_id,
        source_endpoint_revision_id=source_endpoint_revision_id,
        source_relative_path=source_relative_path,
        final_relative_path=final_relative_path,
        payload_name=payload_name,
        fingerprint_byte_count=byte_count,
        fingerprint_content_hash=content_hash,
        manifest_hash=manifest_hash,
        canonical_json=canonical_json,
    )


def _fingerprint_from_json(raw_fingerprint: str | None) -> Mapping[str, object]:
    if raw_fingerprint is None:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_FINGERPRINT_MISSING"
        )
    try:
        fingerprint = json.loads(raw_fingerprint)
    except json.JSONDecodeError as exc:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_FINGERPRINT_INVALID"
        ) from exc
    if not isinstance(fingerprint, dict):
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_FINGERPRINT_INVALID"
        )
    return fingerprint


def _fingerprint_values(fingerprint: Mapping[str, object]) -> tuple[int, str]:
    if set(fingerprint) != {"byte_count", "content_hash"}:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    byte_count = fingerprint.get("byte_count")
    content_hash = fingerprint.get("content_hash")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
        or not isinstance(content_hash, str)
        or _HASH_PATTERN.fullmatch(content_hash) is None
    ):
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    return byte_count, content_hash


def _canonical_relative_path(value: str) -> str:
    try:
        return "/".join(parse_endpoint_relative_path(value).parts)
    except SafePathViolation as exc:
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_RELATIVE_PATH_INVALID"
        ) from exc


def _require_object_id(value: str) -> None:
    if _OBJECT_ID_PATTERN.fullmatch(value) is None:
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_OBJECT_ID_INVALID")


def _require_nonempty(value: str) -> None:
    if not value.strip():
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_IDENTIFIER_INVALID")


def _require_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StagingObjectManifestError("STAGING_OBJECT_MANIFEST_IDENTIFIER_INVALID")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _require_string(value)


def _require_optional_binding(
    endpoint_id: str | None,
    endpoint_revision_id: str | None,
) -> None:
    if (endpoint_id is None) != (endpoint_revision_id is None):
        raise StagingObjectManifestError(
            "STAGING_OBJECT_MANIFEST_SOURCE_BINDING_INVALID"
        )


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
