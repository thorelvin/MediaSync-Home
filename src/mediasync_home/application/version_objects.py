from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from mediasync_home.application.recovery_operations import RecoveryOperation
from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)


VERSION_OBJECT_MANIFEST_SCHEMA_VERSION = 1
VERSION_OBJECT_MANIFEST_HASH_ALGORITHM = "SHA-256"
VERSION_OBJECT_MANIFEST_CANONICALIZATION = "JSON_SORT_KEYS_COMPACT_UTF8_V1"
THIRTY_DAY_RETENTION_POLICY = "THIRTY_DAYS"
OLD_TARGET_VERSION_ROLE = "OLD_TARGET_VERSION"
EMPTY_DIRECTORY_QUARANTINE_ROLE = "EMPTY_DIRECTORY_QUARANTINE"
_OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "object_role",
        "version_object_id",
        "operation_id",
        "run_id",
        "run_target_id",
        "job_id",
        "job_revision_id",
        "target_endpoint_id",
        "target_endpoint_revision_id",
        "endpoint_generation",
        "owner_installation_id",
        "ownership_epoch",
        "final_relative_path",
        "payload_name",
        "fingerprint",
        "created_utc",
        "retention_policy",
        "retention_until_utc",
        "manifest_hash_algorithm",
        "canonicalization",
        "manifest_hash",
    }
)


class VersionObjectManifestError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass(frozen=True, slots=True)
class VersionObjectManifest:
    object_role: str
    version_object_id: str
    operation_id: str
    run_id: str
    run_target_id: str
    job_id: str
    job_revision_id: str
    target_endpoint_id: str
    target_endpoint_revision_id: str
    endpoint_generation: int
    owner_installation_id: str
    ownership_epoch: int
    final_relative_path: str
    payload_name: str
    fingerprint_json: str
    fingerprint_byte_count: int | None
    fingerprint_content_hash: str | None
    fingerprint_entry_count: int | None
    created_utc: str
    retention_policy: str
    retention_until_utc: str
    manifest_hash: str
    canonical_json: str


def version_object_manifest_from_operation(
    operation: RecoveryOperation,
    *,
    created_utc: str,
) -> VersionObjectManifest:
    if operation.version_object_id is not None:
        object_id = operation.version_object_id
    else:
        object_id = operation.operation_id
    if operation.job_id is None or operation.job_revision_id is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_JOB_BINDING_MISSING")
    if operation.retention_policy is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_RETENTION_POLICY_MISSING")
    fingerprint = _fingerprint_from_json(operation.expected_target_fingerprint_json)
    return create_version_object_manifest(
        version_object_id=object_id,
        operation_id=operation.operation_id,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        job_id=operation.job_id,
        job_revision_id=operation.job_revision_id,
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        endpoint_generation=operation.endpoint_generation,
        owner_installation_id=operation.owner_installation_id,
        ownership_epoch=operation.ownership_epoch,
        final_relative_path=operation.final_relative_path,
        fingerprint=fingerprint,
        created_utc=created_utc,
        retention_policy=operation.retention_policy,
    )


def quarantine_object_manifest_from_operation(
    operation: RecoveryOperation,
    *,
    created_utc: str,
) -> VersionObjectManifest:
    if operation.quarantine_object_id is not None:
        object_id = operation.quarantine_object_id
    else:
        object_id = operation.operation_id
    if operation.job_id is None or operation.job_revision_id is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_JOB_BINDING_MISSING")
    if operation.retention_policy is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_RETENTION_POLICY_MISSING")
    fingerprint = _fingerprint_from_json(operation.expected_target_fingerprint_json)
    return create_quarantine_object_manifest(
        version_object_id=object_id,
        operation_id=operation.operation_id,
        run_id=operation.run_id,
        run_target_id=operation.run_target_id,
        job_id=operation.job_id,
        job_revision_id=operation.job_revision_id,
        target_endpoint_id=operation.target_endpoint_id,
        target_endpoint_revision_id=operation.target_endpoint_revision_id,
        endpoint_generation=operation.endpoint_generation,
        owner_installation_id=operation.owner_installation_id,
        ownership_epoch=operation.ownership_epoch,
        final_relative_path=operation.final_relative_path,
        fingerprint=fingerprint,
        created_utc=created_utc,
        retention_policy=operation.retention_policy,
    )


def create_quarantine_object_manifest(
    *,
    version_object_id: str,
    operation_id: str,
    run_id: str,
    run_target_id: str,
    job_id: str,
    job_revision_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    owner_installation_id: str,
    ownership_epoch: int,
    final_relative_path: str,
    fingerprint: Mapping[str, object],
    created_utc: str,
    retention_policy: str,
) -> VersionObjectManifest:
    return _create_manifest(
        object_role=EMPTY_DIRECTORY_QUARANTINE_ROLE,
        version_object_id=version_object_id,
        operation_id=operation_id,
        run_id=run_id,
        run_target_id=run_target_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        endpoint_generation=endpoint_generation,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        final_relative_path=final_relative_path,
        fingerprint=fingerprint,
        created_utc=created_utc,
        retention_policy=retention_policy,
    )


def create_version_object_manifest(
    *,
    version_object_id: str,
    operation_id: str,
    run_id: str,
    run_target_id: str,
    job_id: str,
    job_revision_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    owner_installation_id: str,
    ownership_epoch: int,
    final_relative_path: str,
    fingerprint: Mapping[str, object],
    created_utc: str,
    retention_policy: str,
) -> VersionObjectManifest:
    return _create_manifest(
        object_role=OLD_TARGET_VERSION_ROLE,
        version_object_id=version_object_id,
        operation_id=operation_id,
        run_id=run_id,
        run_target_id=run_target_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        endpoint_generation=endpoint_generation,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        final_relative_path=final_relative_path,
        fingerprint=fingerprint,
        created_utc=created_utc,
        retention_policy=retention_policy,
    )


def _create_manifest(
    *,
    object_role: str,
    version_object_id: str,
    operation_id: str,
    run_id: str,
    run_target_id: str,
    job_id: str,
    job_revision_id: str,
    target_endpoint_id: str,
    target_endpoint_revision_id: str,
    endpoint_generation: int,
    owner_installation_id: str,
    ownership_epoch: int,
    final_relative_path: str,
    fingerprint: Mapping[str, object],
    created_utc: str,
    retention_policy: str,
) -> VersionObjectManifest:
    _require_object_id(version_object_id)
    for value in (
        operation_id,
        run_id,
        run_target_id,
        job_id,
        job_revision_id,
        target_endpoint_id,
        target_endpoint_revision_id,
        owner_installation_id,
    ):
        _require_nonempty(value)
    _require_positive_int(endpoint_generation)
    _require_positive_int(ownership_epoch)
    canonical_path = _canonical_relative_path(final_relative_path)
    canonical_fingerprint = _canonical_fingerprint(object_role, fingerprint)
    created = _parse_utc(created_utc)
    if retention_policy != THIRTY_DAY_RETENTION_POLICY:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_RETENTION_POLICY_INVALID")
    retention_until_utc = _format_utc(created + timedelta(days=30))
    body: dict[str, object] = {
        "schema_version": VERSION_OBJECT_MANIFEST_SCHEMA_VERSION,
        "object_role": object_role,
        "version_object_id": version_object_id,
        "operation_id": operation_id,
        "run_id": run_id,
        "run_target_id": run_target_id,
        "job_id": job_id,
        "job_revision_id": job_revision_id,
        "target_endpoint_id": target_endpoint_id,
        "target_endpoint_revision_id": target_endpoint_revision_id,
        "endpoint_generation": endpoint_generation,
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
        "final_relative_path": canonical_path,
        "payload_name": f"{version_object_id}.payload",
        "fingerprint": canonical_fingerprint,
        "created_utc": _format_utc(created),
        "retention_policy": retention_policy,
        "retention_until_utc": retention_until_utc,
        "manifest_hash_algorithm": VERSION_OBJECT_MANIFEST_HASH_ALGORITHM,
        "canonicalization": VERSION_OBJECT_MANIFEST_CANONICALIZATION,
    }
    manifest_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    payload = {**body, "manifest_hash": manifest_hash}
    return _manifest_from_valid_payload(payload, canonical_json=_canonical_json(payload))


def parse_version_object_manifest(raw_manifest: str) -> VersionObjectManifest:
    try:
        payload = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_JSON_INVALID") from exc
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_SHAPE_INVALID")
    canonical_json = _canonical_json(payload)
    if raw_manifest != canonical_json:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_NOT_CANONICAL")
    if payload.get("schema_version") != VERSION_OBJECT_MANIFEST_SCHEMA_VERSION:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_SCHEMA_UNSUPPORTED")
    if payload.get("object_role") not in {
        OLD_TARGET_VERSION_ROLE,
        EMPTY_DIRECTORY_QUARANTINE_ROLE,
    }:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_ROLE_INVALID")
    if payload.get("manifest_hash_algorithm") != VERSION_OBJECT_MANIFEST_HASH_ALGORITHM:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_HASH_ALGORITHM_INVALID")
    if payload.get("canonicalization") != VERSION_OBJECT_MANIFEST_CANONICALIZATION:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_CANONICALIZATION_INVALID")
    manifest_hash = payload.get("manifest_hash")
    if not isinstance(manifest_hash, str) or _HASH_PATTERN.fullmatch(manifest_hash) is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_HASH_INVALID")
    body = {key: value for key, value in payload.items() if key != "manifest_hash"}
    expected_hash = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    if manifest_hash != expected_hash:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_HASH_MISMATCH")
    return _manifest_from_valid_payload(payload, canonical_json=canonical_json)


def require_version_object_manifest_binding(
    manifest: VersionObjectManifest,
    *,
    operation: RecoveryOperation,
) -> None:
    expected_role = (
        EMPTY_DIRECTORY_QUARANTINE_ROLE
        if operation.quarantine_object_id is not None
        else OLD_TARGET_VERSION_ROLE
    )
    expected_object_id = (
        operation.quarantine_object_id
        if expected_role == EMPTY_DIRECTORY_QUARANTINE_ROLE
        else operation.version_object_id
    ) or operation.operation_id
    if (
        manifest.version_object_id != expected_object_id
        or manifest.object_role != expected_role
        or manifest.operation_id != operation.operation_id
        or manifest.run_id != operation.run_id
        or manifest.run_target_id != operation.run_target_id
        or manifest.job_id != operation.job_id
        or manifest.job_revision_id != operation.job_revision_id
        or manifest.target_endpoint_id != operation.target_endpoint_id
        or manifest.target_endpoint_revision_id != operation.target_endpoint_revision_id
        or manifest.endpoint_generation != operation.endpoint_generation
        or manifest.owner_installation_id != operation.owner_installation_id
        or manifest.ownership_epoch != operation.ownership_epoch
        or manifest.final_relative_path != _canonical_relative_path(operation.final_relative_path)
        or manifest.retention_policy != operation.retention_policy
    ):
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_BINDING_MISMATCH")


def _manifest_from_valid_payload(
    payload: Mapping[str, object],
    *,
    canonical_json: str,
) -> VersionObjectManifest:
    version_object_id = _require_string(payload.get("version_object_id"))
    _require_object_id(version_object_id)
    operation_id = _require_string(payload.get("operation_id"))
    run_id = _require_string(payload.get("run_id"))
    run_target_id = _require_string(payload.get("run_target_id"))
    job_id = _require_string(payload.get("job_id"))
    job_revision_id = _require_string(payload.get("job_revision_id"))
    target_endpoint_id = _require_string(payload.get("target_endpoint_id"))
    target_endpoint_revision_id = _require_string(payload.get("target_endpoint_revision_id"))
    endpoint_generation = _required_positive_int(payload.get("endpoint_generation"))
    owner_installation_id = _require_string(payload.get("owner_installation_id"))
    ownership_epoch = _required_positive_int(payload.get("ownership_epoch"))
    final_relative_path = _require_string(payload.get("final_relative_path"))
    if _canonical_relative_path(final_relative_path) != final_relative_path:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_PATH_NOT_CANONICAL")
    payload_name = _require_string(payload.get("payload_name"))
    if payload_name != f"{version_object_id}.payload":
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_PAYLOAD_NAME_INVALID")
    object_role = _require_string(payload.get("object_role"))
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    canonical_fingerprint = _canonical_fingerprint(object_role, fingerprint)
    byte_count = canonical_fingerprint.get("byte_count")
    content_hash = canonical_fingerprint.get("content_hash")
    entry_count = canonical_fingerprint.get("entry_count")
    created_utc = _format_utc(_parse_utc(_require_string(payload.get("created_utc"))))
    retention_policy = _require_string(payload.get("retention_policy"))
    if retention_policy != THIRTY_DAY_RETENTION_POLICY:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_RETENTION_POLICY_INVALID")
    retention_until_utc = _format_utc(
        _parse_utc(_require_string(payload.get("retention_until_utc")))
    )
    expected_retention = _format_utc(_parse_utc(created_utc) + timedelta(days=30))
    if retention_until_utc != expected_retention:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_RETENTION_WINDOW_INVALID")
    return VersionObjectManifest(
        object_role=object_role,
        version_object_id=version_object_id,
        operation_id=operation_id,
        run_id=run_id,
        run_target_id=run_target_id,
        job_id=job_id,
        job_revision_id=job_revision_id,
        target_endpoint_id=target_endpoint_id,
        target_endpoint_revision_id=target_endpoint_revision_id,
        endpoint_generation=endpoint_generation,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        final_relative_path=final_relative_path,
        payload_name=payload_name,
        fingerprint_json=_canonical_json(canonical_fingerprint),
        fingerprint_byte_count=byte_count if isinstance(byte_count, int) else None,
        fingerprint_content_hash=content_hash if isinstance(content_hash, str) else None,
        fingerprint_entry_count=entry_count if isinstance(entry_count, int) else None,
        created_utc=created_utc,
        retention_policy=retention_policy,
        retention_until_utc=retention_until_utc,
        manifest_hash=_require_string(payload.get("manifest_hash")),
        canonical_json=canonical_json,
    )


def _fingerprint_from_json(raw_fingerprint: str | None) -> Mapping[str, object]:
    if raw_fingerprint is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_MISSING")
    try:
        fingerprint = json.loads(raw_fingerprint)
    except json.JSONDecodeError as exc:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_INVALID") from exc
    if not isinstance(fingerprint, dict):
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    return fingerprint


def _fingerprint_values(fingerprint: Mapping[str, object]) -> tuple[int, str]:
    if set(fingerprint) != {"byte_count", "content_hash"}:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    byte_count = fingerprint.get("byte_count")
    content_hash = fingerprint.get("content_hash")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
        or not isinstance(content_hash, str)
        or _HASH_PATTERN.fullmatch(content_hash) is None
    ):
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_INVALID")
    return byte_count, content_hash


def _canonical_fingerprint(
    object_role: str,
    fingerprint: Mapping[str, object],
) -> dict[str, object]:
    if object_role == OLD_TARGET_VERSION_ROLE:
        byte_count, content_hash = _fingerprint_values(fingerprint)
        return {"byte_count": byte_count, "content_hash": content_hash}
    if object_role == EMPTY_DIRECTORY_QUARANTINE_ROLE:
        if (
            set(fingerprint) != {"entry_count", "kind"}
            or fingerprint.get("kind") != "DIRECTORY_EMPTY"
            or fingerprint.get("entry_count") != 0
        ):
            raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_FINGERPRINT_INVALID")
        return {"entry_count": 0, "kind": "DIRECTORY_EMPTY"}
    raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_ROLE_INVALID")


def _canonical_relative_path(value: str) -> str:
    try:
        return "/".join(parse_endpoint_relative_path(value).parts)
    except SafePathViolation as exc:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_PATH_INVALID") from exc


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_TIME_INVALID") from exc
    if parsed.utcoffset() != timedelta(0):
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _required_positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_NUMBER_INVALID")
    return value


def _require_positive_int(value: int) -> None:
    _required_positive_int(value)


def _require_object_id(value: str) -> None:
    if _OBJECT_ID_PATTERN.fullmatch(value) is None:
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_OBJECT_ID_INVALID")


def _require_nonempty(value: str) -> None:
    if not value.strip():
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_IDENTIFIER_INVALID")


def _require_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VersionObjectManifestError("VERSION_OBJECT_MANIFEST_IDENTIFIER_INVALID")
    return value


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
