from __future__ import annotations

import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from blake3 import blake3

from mediasync_home.adapters.reparse_guard import (
    LocalFilesystemReparsePathProbe,
    ReparseGuardError,
    ReparseInspection,
    ReparsePathProbe,
)
from mediasync_home.application.endpoint_classification import (
    EndpointControlAreaClassification,
    EndpointControlAreaClassifier,
    EndpointControlAreaState,
    EndpointMarkerEvidence,
)


SUPPORTED_CONTROL_SCHEMA_VERSION = 4
CONTROL_DIRECTORY_NAME = ".mediasync"
ENDPOINT_MARKER_NAME = "endpoint.json"
CANONICALIZATION_ALGORITHM = "JCS-RFC8785"
BLAKE3_ALGORITHM = "BLAKE3-256"
APPLICATION_NAME = "MediaSync Home"
MAX_MARKER_BYTES = 64 * 1024
MAX_OWNERSHIP_RECORD_BYTES = 64 * 1024
MAX_ROOT_ENTRIES = 100_000
MAX_CONTROL_ENTRIES = 10_000

_LOWER_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_CURRENT_REQUIRED_FIELDS = frozenset(
    {
        "control_schema_version",
        "endpoint_id",
        "control_area_id",
        "owner_installation_id",
        "ownership_epoch",
        "ownership_mode",
        "root_identity_hash_algorithm",
        "root_identity_hash",
        "latest_ownership_record",
        "created_utc",
        "updated_utc",
        "canonicalization_algorithm",
        "marker_checksum_algorithm",
        "marker_checksum",
        "application",
    }
)
_CURRENT_OPTIONAL_FIELDS = frozenset({"expected_volume_id", "expected_share"})
_KNOWN_CONTROL_CHILDREN = frozenset(
    {
        ENDPOINT_MARKER_NAME,
        "ownership",
        "locks",
        "installations",
    }
)


class LocalEndpointClassificationError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class _DuplicateJsonKey(ValueError):
    pass


class LocalEndpointControlAreaClassifier(EndpointControlAreaClassifier):
    def __init__(
        self,
        *,
        probe: ReparsePathProbe | None = None,
        max_root_entries: int = MAX_ROOT_ENTRIES,
        max_control_entries: int = MAX_CONTROL_ENTRIES,
    ) -> None:
        if max_root_entries < 1 or max_control_entries < 1:
            raise ValueError("endpoint classifier entry limits must be positive")
        self._probe = probe or LocalFilesystemReparsePathProbe()
        self._max_root_entries = max_root_entries
        self._max_control_entries = max_control_entries

    def classify_control_area(
        self,
        root: Path,
        *,
        local_installation_id: str,
    ) -> EndpointControlAreaClassification:
        root = Path(root)
        _require_uuid(local_installation_id, field="local_installation_id")
        root_inspection = self._inspect_required_directory(
            root,
            missing_code="ENDPOINT_CLASSIFICATION_ROOT_MISSING",
            invalid_code="ENDPOINT_CLASSIFICATION_ROOT_INVALID",
        )
        root_identity_hash = _root_identity_hash(root_inspection)
        aliases = self._find_control_area_aliases(root)
        if not aliases:
            return _classification(
                root,
                EndpointControlAreaState.ABSENT,
                "ENDPOINT_CONTROL_AREA_ABSENT",
            )
        if len(aliases) != 1 or aliases[0] != CONTROL_DIRECTORY_NAME:
            return _classification(
                root,
                EndpointControlAreaState.CASE_ALIAS_COLLISION,
                "ENDPOINT_CONTROL_AREA_CASE_ALIAS_COLLISION",
            )

        control_dir = root / CONTROL_DIRECTORY_NAME
        control_inspection = self._inspect_optional(control_dir)
        if (
            control_inspection is None
            or control_inspection.is_reparse_point
            or not _is_directory_without_following(control_dir)
        ):
            return _classification(
                root,
                EndpointControlAreaState.UNKNOWN_NONEMPTY_DIRECTORY,
                "ENDPOINT_CONTROL_AREA_NOT_ORDINARY_DIRECTORY",
            )

        control_names = self._bounded_entry_names(
            control_dir,
            limit=self._max_control_entries,
            limit_code="ENDPOINT_CONTROL_AREA_ENTRY_LIMIT_EXCEEDED",
        )
        marker_aliases = tuple(
            name
            for name in control_names
            if name.casefold() == ENDPOINT_MARKER_NAME.casefold()
        )
        if not marker_aliases:
            if not control_names:
                return _classification(
                    root,
                    EndpointControlAreaState.UNKNOWN_EMPTY_DIRECTORY,
                    "ENDPOINT_CONTROL_AREA_UNKNOWN_EMPTY",
                )
            if any(
                name.casefold() in _KNOWN_CONTROL_CHILDREN for name in control_names
            ):
                return _classification(
                    root,
                    EndpointControlAreaState.PARTIAL_CONTROL_AREA,
                    "ENDPOINT_CONTROL_AREA_PARTIAL_WITHOUT_MARKER",
                )
            return _classification(
                root,
                EndpointControlAreaState.UNKNOWN_NONEMPTY_DIRECTORY,
                "ENDPOINT_CONTROL_AREA_UNKNOWN_NONEMPTY",
            )
        if len(marker_aliases) != 1 or marker_aliases[0] != ENDPOINT_MARKER_NAME:
            return _classification(
                root,
                EndpointControlAreaState.CORRUPT_MARKER,
                "ENDPOINT_MARKER_CASE_ALIAS_COLLISION",
            )

        marker_path = control_dir / ENDPOINT_MARKER_NAME
        try:
            payload = self._read_json_object(
                marker_path,
                maximum_bytes=MAX_MARKER_BYTES,
                invalid_code="ENDPOINT_MARKER_INVALID",
            )
            marker = _validate_marker_payload(payload)
        except LocalEndpointClassificationError as exc:
            return _classification(
                root,
                EndpointControlAreaState.CORRUPT_MARKER,
                exc.validation_code,
            )

        if marker.root_identity_hash != root_identity_hash:
            return _classification(
                root,
                EndpointControlAreaState.PARTIAL_CONTROL_AREA,
                "ENDPOINT_ROOT_IDENTITY_MISMATCH",
                marker=marker,
            )
        ownership_path = control_dir / marker.latest_ownership_record
        try:
            ownership_record = self._read_json_object(
                ownership_path,
                maximum_bytes=MAX_OWNERSHIP_RECORD_BYTES,
                invalid_code="ENDPOINT_OWNERSHIP_RECORD_INVALID",
            )
            _validate_ownership_record(ownership_record, marker)
        except LocalEndpointClassificationError as exc:
            return _classification(
                root,
                EndpointControlAreaState.PARTIAL_CONTROL_AREA,
                exc.validation_code,
                marker=marker,
            )

        if marker.control_schema_version > SUPPORTED_CONTROL_SCHEMA_VERSION:
            return _classification(
                root,
                EndpointControlAreaState.VALID_READ_ONLY_NEWER_SCHEMA,
                "ENDPOINT_CONTROL_SCHEMA_NEWER_READ_ONLY",
                marker=marker,
            )
        if marker.control_schema_version < SUPPORTED_CONTROL_SCHEMA_VERSION:
            return _classification(
                root,
                EndpointControlAreaState.PARTIAL_CONTROL_AREA,
                "ENDPOINT_CONTROL_SCHEMA_OLDER_REQUIRES_RECOVERY",
                marker=marker,
            )
        if not self._current_control_layout_is_complete(control_dir):
            return _classification(
                root,
                EndpointControlAreaState.PARTIAL_CONTROL_AREA,
                "ENDPOINT_CONTROL_AREA_REQUIRED_DIRECTORY_MISSING",
                marker=marker,
            )
        if marker.owner_installation_id != local_installation_id:
            return _classification(
                root,
                EndpointControlAreaState.VALID_FOREIGN,
                "ENDPOINT_CONTROL_AREA_FOREIGN_OWNER",
                marker=marker,
            )
        return _classification(
            root,
            EndpointControlAreaState.VALID_OWNED,
            "ENDPOINT_CONTROL_AREA_VALID_OWNED",
            marker=marker,
        )

    def _find_control_area_aliases(self, root: Path) -> tuple[str, ...]:
        names = self._bounded_entry_names(
            root,
            limit=self._max_root_entries,
            limit_code="ENDPOINT_ROOT_ENTRY_LIMIT_EXCEEDED",
        )
        return tuple(
            name
            for name in names
            if name.casefold() == CONTROL_DIRECTORY_NAME.casefold()
        )

    def _bounded_entry_names(
        self,
        directory: Path,
        *,
        limit: int,
        limit_code: str,
    ) -> tuple[str, ...]:
        names: list[str] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(names) >= limit:
                        raise LocalEndpointClassificationError(
                            limit_code,
                            "Reduce the endpoint directory entry count before retrying classification.",
                        )
                    names.append(entry.name)
        except LocalEndpointClassificationError:
            raise
        except OSError as exc:
            raise LocalEndpointClassificationError(
                "ENDPOINT_CLASSIFICATION_ENUMERATION_FAILED",
                "Restore read access to the endpoint directory before retrying classification.",
            ) from exc
        return tuple(names)

    def _current_control_layout_is_complete(self, control_dir: Path) -> bool:
        for name in ("ownership", "locks", "installations"):
            inspection = self._inspect_optional(control_dir / name)
            if (
                inspection is None
                or inspection.is_reparse_point
                or not _is_directory_without_following(control_dir / name)
            ):
                return False
        return True

    def _inspect_required_directory(
        self,
        path: Path,
        *,
        missing_code: str,
        invalid_code: str,
    ) -> ReparseInspection:
        inspection = self._inspect_optional(path)
        if inspection is None:
            raise LocalEndpointClassificationError(
                missing_code,
                "Restore the selected endpoint root before retrying classification.",
            )
        if inspection.is_reparse_point or not _is_directory_without_following(path):
            raise LocalEndpointClassificationError(
                invalid_code,
                "Select an ordinary non-reparse directory as the endpoint root.",
            )
        return inspection

    def _inspect_optional(self, path: Path) -> ReparseInspection | None:
        try:
            inspection = self._probe.inspect_path(path)
        except ReparseGuardError as exc:
            raise LocalEndpointClassificationError(
                exc.validation_code,
                exc.next_action,
            ) from exc
        if not inspection.exists:
            return None
        return inspection

    def _read_json_object(
        self,
        path: Path,
        *,
        maximum_bytes: int,
        invalid_code: str,
    ) -> dict[str, object]:
        inspection = self._inspect_optional(path)
        if inspection is None or inspection.is_reparse_point:
            raise LocalEndpointClassificationError(
                invalid_code,
                "Restore an ordinary checksummed control file before retrying classification.",
            )
        try:
            file_stat = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > maximum_bytes:
                raise LocalEndpointClassificationError(
                    invalid_code,
                    "Restore a bounded ordinary control file before retrying classification.",
                )
            data = path.read_bytes()
        except LocalEndpointClassificationError:
            raise
        except OSError as exc:
            raise LocalEndpointClassificationError(
                invalid_code,
                "Restore readable control metadata before retrying classification.",
            ) from exc
        if len(data) > maximum_bytes:
            raise LocalEndpointClassificationError(
                invalid_code,
                "Restore a bounded control file before retrying classification.",
            )
        try:
            payload = json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise LocalEndpointClassificationError(
                invalid_code,
                "Restore canonical JSON control metadata before retrying classification.",
            ) from exc
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) for key in payload
        ):
            raise LocalEndpointClassificationError(
                invalid_code,
                "Restore a JSON object control record before retrying classification.",
            )
        return cast(dict[str, object], payload)


def local_root_identity_hash(
    root: Path,
    *,
    probe: ReparsePathProbe | None = None,
) -> str:
    active_probe = probe or LocalFilesystemReparsePathProbe()
    try:
        inspection = active_probe.inspect_path(Path(root))
    except ReparseGuardError as exc:
        raise LocalEndpointClassificationError(
            exc.validation_code, exc.next_action
        ) from exc
    if (
        not inspection.exists
        or inspection.is_reparse_point
        or inspection.identity is None
        or not _is_directory_without_following(Path(root))
    ):
        raise LocalEndpointClassificationError(
            "ENDPOINT_ROOT_IDENTITY_UNAVAILABLE",
            "Select an accessible ordinary directory before deriving endpoint identity.",
        )
    return _root_identity_hash(inspection)


def endpoint_marker_checksum(marker: Mapping[str, object]) -> str:
    material = dict(marker)
    material.pop("marker_checksum", None)
    try:
        canonical = json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalEndpointClassificationError(
            "ENDPOINT_MARKER_CANONICALIZATION_FAILED",
            "Restore marker fields supported by the canonical JSON contract.",
        ) from exc
    return blake3(canonical).hexdigest()


def load_validated_endpoint_marker_payload(
    root: Path,
    *,
    probe: ReparsePathProbe | None = None,
) -> dict[str, object]:
    """Read the strict marker payload after the caller has acquired its endpoint lock."""
    classifier = LocalEndpointControlAreaClassifier(probe=probe)
    payload = classifier._read_json_object(
        Path(root) / CONTROL_DIRECTORY_NAME / ENDPOINT_MARKER_NAME,
        maximum_bytes=MAX_MARKER_BYTES,
        invalid_code="ENDPOINT_MARKER_INVALID",
    )
    _validate_marker_payload(payload)
    return payload


def _validate_marker_payload(payload: dict[str, object]) -> EndpointMarkerEvidence:
    schema_version = _positive_int(payload.get("control_schema_version"))
    if schema_version is None:
        raise _invalid_marker()
    if not _CURRENT_REQUIRED_FIELDS.issubset(payload):
        raise _invalid_marker()
    if schema_version <= SUPPORTED_CONTROL_SCHEMA_VERSION and (
        set(payload) - _CURRENT_REQUIRED_FIELDS - _CURRENT_OPTIONAL_FIELDS
    ):
        raise _invalid_marker()

    endpoint_id = _uuid_field(payload, "endpoint_id")
    control_area_id = _uuid_field(payload, "control_area_id")
    owner_installation_id = _uuid_field(payload, "owner_installation_id")
    ownership_epoch = _positive_int(payload.get("ownership_epoch"))
    if ownership_epoch is None:
        raise _invalid_marker()
    if payload.get("ownership_mode") != "EXCLUSIVE_WRITER":
        raise _invalid_marker()
    if payload.get("root_identity_hash_algorithm") != BLAKE3_ALGORITHM:
        raise _invalid_marker()
    root_identity_hash = _hash_field(payload, "root_identity_hash")
    latest_ownership_record = payload.get("latest_ownership_record")
    if latest_ownership_record != f"ownership/epoch-{ownership_epoch:08d}.json":
        raise _invalid_marker()
    created_utc = _date_time_field(payload, "created_utc")
    updated_utc = _date_time_field(payload, "updated_utc")
    if updated_utc < created_utc:
        raise _invalid_marker()
    if payload.get("canonicalization_algorithm") != CANONICALIZATION_ALGORITHM:
        raise _invalid_marker()
    if payload.get("marker_checksum_algorithm") != BLAKE3_ALGORITHM:
        raise _invalid_marker()
    marker_checksum = _hash_field(payload, "marker_checksum")
    if payload.get("application") != APPLICATION_NAME:
        raise _invalid_marker()
    _optional_bounded_string(payload, "expected_volume_id", maximum_length=512)
    _optional_bounded_string(payload, "expected_share", maximum_length=2048)
    actual_checksum = endpoint_marker_checksum(payload)
    if not hmac.compare_digest(marker_checksum, actual_checksum):
        raise LocalEndpointClassificationError(
            "ENDPOINT_MARKER_CHECKSUM_MISMATCH",
            "Use recovery or controlled adoption; do not repair a tampered marker automatically.",
        )
    return EndpointMarkerEvidence(
        control_schema_version=schema_version,
        endpoint_id=endpoint_id,
        control_area_id=control_area_id,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        root_identity_hash_algorithm=BLAKE3_ALGORITHM,
        root_identity_hash=root_identity_hash,
        marker_checksum_algorithm=BLAKE3_ALGORITHM,
        marker_checksum=marker_checksum,
        latest_ownership_record=latest_ownership_record,
    )


def _validate_ownership_record(
    payload: Mapping[str, object],
    marker: EndpointMarkerEvidence,
) -> None:
    if (
        payload.get("endpoint_id") != marker.endpoint_id
        or payload.get("owner_installation_id") != marker.owner_installation_id
        or payload.get("ownership_epoch") != marker.ownership_epoch
        or not isinstance(payload.get("event"), str)
    ):
        raise LocalEndpointClassificationError(
            "ENDPOINT_OWNERSHIP_RECORD_MISMATCH",
            "Recover the immutable ownership record before trusting the endpoint marker.",
        )
    _date_time_field(payload, "created_utc")


def _classification(
    root: Path,
    state: EndpointControlAreaState,
    reason_code: str,
    *,
    marker: EndpointMarkerEvidence | None = None,
) -> EndpointControlAreaClassification:
    return EndpointControlAreaClassification(
        root=root,
        state=state,
        reason_codes=(reason_code,),
        marker=marker,
    )


def _root_identity_hash(inspection: ReparseInspection) -> str:
    if inspection.identity is None:
        raise LocalEndpointClassificationError(
            "ENDPOINT_ROOT_IDENTITY_UNAVAILABLE",
            "Retry after the endpoint root exposes stable local handle identity.",
        )
    material = json.dumps(
        {
            "kind": inspection.identity.kind,
            "value": inspection.identity.value,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return blake3(material).hexdigest()


def _is_directory_without_following(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode)
    except OSError:
        return False


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateJsonKey(key)
        payload[key] = value
    return payload


def _reject_json_constant(value: str) -> object:
    raise ValueError(value)


def _require_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise LocalEndpointClassificationError(
            "ENDPOINT_CLASSIFICATION_INSTALLATION_ID_INVALID",
            f"Configure a UUID {field} before classifying endpoint ownership.",
        )
    try:
        UUID(value)
    except ValueError as exc:
        raise LocalEndpointClassificationError(
            "ENDPOINT_CLASSIFICATION_INSTALLATION_ID_INVALID",
            f"Configure a UUID {field} before classifying endpoint ownership.",
        ) from exc
    return value


def _uuid_field(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise _invalid_marker()
    try:
        UUID(value)
    except ValueError as exc:
        raise _invalid_marker() from exc
    return value


def _hash_field(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or _LOWER_HEX_256.fullmatch(value) is None:
        raise _invalid_marker()
    return value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _date_time_field(payload: Mapping[str, object], field: str) -> datetime:
    value = payload.get(field)
    if not isinstance(value, str):
        raise _invalid_marker()
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _invalid_marker() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid_marker()
    return parsed


def _optional_bounded_string(
    payload: Mapping[str, object],
    field: str,
    *,
    maximum_length: int,
) -> None:
    if field not in payload or payload[field] is None:
        return
    value = payload[field]
    if not isinstance(value, str) or len(value) > maximum_length:
        raise _invalid_marker()


def _invalid_marker() -> LocalEndpointClassificationError:
    return LocalEndpointClassificationError(
        "ENDPOINT_MARKER_SCHEMA_INVALID",
        "Use recovery or controlled adoption; do not repair invalid marker fields automatically.",
    )
