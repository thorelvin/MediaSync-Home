from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


_MAX_PROFILE_JSON_BYTES = 32_768


class EndpointCapabilityProbeScope(str, Enum):
    READ_ONLY = "read_only"
    CONTROLLED_WRITABLE = "controlled_writable"


class CaseMode(str, Enum):
    INSENSITIVE = "insensitive"
    SENSITIVE = "sensitive"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class FileIdReliability(str, Enum):
    STABLE = "stable"
    HINT = "hint"
    UNAVAILABLE = "unavailable"


class LockScope(str, Enum):
    LOCAL_MACHINE = "local_machine"
    REMOTE_SHARE_OBSERVED = "remote_share_observed"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class SourceReadGuardLevel(str, Enum):
    DENY_WRITE_AND_DELETE = "deny_write_and_delete"
    STABILITY_HANDLE_ONLY = "stability_handle_only"
    POST_TRANSFER_HASH_ONLY = "post_transfer_hash_only"
    UNAVAILABLE = "unavailable"


class DurabilityLevel(str, Enum):
    FILE_FLUSH_CONFIRMED = "file_flush_confirmed"
    REMOTE_ACK_ONLY = "remote_ack_only"
    BEST_EFFORT = "best_effort"
    UNKNOWN = "unknown"


class EndpointCapabilityEvidenceError(ValueError):
    pass


class EndpointCapabilityProbeError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True, slots=True)
class EndpointCapabilities:
    probe_scope: EndpointCapabilityProbeScope
    filesystem_name: str | None
    maximum_file_size: int | None
    maximum_component_length: int | None
    maximum_path_length: int | None
    timestamp_precision_ns: int
    default_case_mode: CaseMode
    supports_per_directory_case_query: bool
    supports_reparse_inspection: bool
    supports_final_path_resolution: bool
    supports_directory_identity_handles: bool
    supports_atomic_rename: bool
    supports_no_overwrite_insert: bool
    supports_atomic_replace: bool
    supports_file_flush: bool
    supports_write_through_move: bool
    durability_level: DurabilityLevel
    lock_scope: LockScope
    supports_exclusive_control_lock: bool
    source_read_guard_level: SourceReadGuardLevel
    supports_file_ids: bool
    file_id_reliability: FileIdReliability
    supports_birthtime: bool
    supports_attributes: bool
    supports_named_streams: bool
    supports_sparse_files: bool
    supports_hardlinks: bool
    supports_encryption: bool
    supports_long_paths: bool
    is_network: bool
    is_removable: bool
    likely_rotational: bool | None
    profile_schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_schema_version": self.profile_schema_version,
            "probe_scope": self.probe_scope.value,
            "filesystem_name": self.filesystem_name,
            "maximum_file_size": self.maximum_file_size,
            "maximum_component_length": self.maximum_component_length,
            "maximum_path_length": self.maximum_path_length,
            "timestamp_precision_ns": self.timestamp_precision_ns,
            "default_case_mode": self.default_case_mode.value,
            "supports_per_directory_case_query": self.supports_per_directory_case_query,
            "supports_reparse_inspection": self.supports_reparse_inspection,
            "supports_final_path_resolution": self.supports_final_path_resolution,
            "supports_directory_identity_handles": self.supports_directory_identity_handles,
            "supports_atomic_rename": self.supports_atomic_rename,
            "supports_no_overwrite_insert": self.supports_no_overwrite_insert,
            "supports_atomic_replace": self.supports_atomic_replace,
            "supports_file_flush": self.supports_file_flush,
            "supports_write_through_move": self.supports_write_through_move,
            "durability_level": self.durability_level.value,
            "lock_scope": self.lock_scope.value,
            "supports_exclusive_control_lock": self.supports_exclusive_control_lock,
            "source_read_guard_level": self.source_read_guard_level.value,
            "supports_file_ids": self.supports_file_ids,
            "file_id_reliability": self.file_id_reliability.value,
            "supports_birthtime": self.supports_birthtime,
            "supports_attributes": self.supports_attributes,
            "supports_named_streams": self.supports_named_streams,
            "supports_sparse_files": self.supports_sparse_files,
            "supports_hardlinks": self.supports_hardlinks,
            "supports_encryption": self.supports_encryption,
            "supports_long_paths": self.supports_long_paths,
            "is_network": self.is_network,
            "is_removable": self.is_removable,
            "likely_rotational": self.likely_rotational,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    def capabilities_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, payload: str) -> EndpointCapabilities:
        if len(payload.encode("utf-8")) > _MAX_PROFILE_JSON_BYTES:
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_PROFILE_TOO_LARGE"
            )
        try:
            decoded: Any = json.loads(payload)
            if not isinstance(decoded, dict) or set(decoded) != set(_PROFILE_FIELDS):
                raise ValueError
            profile = cls(
                profile_schema_version=_required_int(decoded["profile_schema_version"]),
                probe_scope=EndpointCapabilityProbeScope(
                    _required_str(decoded["probe_scope"])
                ),
                filesystem_name=_optional_str(decoded["filesystem_name"]),
                maximum_file_size=_optional_nonnegative_int(decoded["maximum_file_size"]),
                maximum_component_length=_optional_nonnegative_int(
                    decoded["maximum_component_length"]
                ),
                maximum_path_length=_optional_nonnegative_int(
                    decoded["maximum_path_length"]
                ),
                timestamp_precision_ns=_required_int(decoded["timestamp_precision_ns"]),
                default_case_mode=CaseMode(_required_str(decoded["default_case_mode"])),
                supports_per_directory_case_query=_required_bool(
                    decoded["supports_per_directory_case_query"]
                ),
                supports_reparse_inspection=_required_bool(
                    decoded["supports_reparse_inspection"]
                ),
                supports_final_path_resolution=_required_bool(
                    decoded["supports_final_path_resolution"]
                ),
                supports_directory_identity_handles=_required_bool(
                    decoded["supports_directory_identity_handles"]
                ),
                supports_atomic_rename=_required_bool(decoded["supports_atomic_rename"]),
                supports_no_overwrite_insert=_required_bool(
                    decoded["supports_no_overwrite_insert"]
                ),
                supports_atomic_replace=_required_bool(decoded["supports_atomic_replace"]),
                supports_file_flush=_required_bool(decoded["supports_file_flush"]),
                supports_write_through_move=_required_bool(
                    decoded["supports_write_through_move"]
                ),
                durability_level=DurabilityLevel(
                    _required_str(decoded["durability_level"])
                ),
                lock_scope=LockScope(_required_str(decoded["lock_scope"])),
                supports_exclusive_control_lock=_required_bool(
                    decoded["supports_exclusive_control_lock"]
                ),
                source_read_guard_level=SourceReadGuardLevel(
                    _required_str(decoded["source_read_guard_level"])
                ),
                supports_file_ids=_required_bool(decoded["supports_file_ids"]),
                file_id_reliability=FileIdReliability(
                    _required_str(decoded["file_id_reliability"])
                ),
                supports_birthtime=_required_bool(decoded["supports_birthtime"]),
                supports_attributes=_required_bool(decoded["supports_attributes"]),
                supports_named_streams=_required_bool(decoded["supports_named_streams"]),
                supports_sparse_files=_required_bool(decoded["supports_sparse_files"]),
                supports_hardlinks=_required_bool(decoded["supports_hardlinks"]),
                supports_encryption=_required_bool(decoded["supports_encryption"]),
                supports_long_paths=_required_bool(decoded["supports_long_paths"]),
                is_network=_required_bool(decoded["is_network"]),
                is_removable=_required_bool(decoded["is_removable"]),
                likely_rotational=_optional_bool(decoded["likely_rotational"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_PROFILE_INVALID"
            ) from exc
        if profile.profile_schema_version != 1:
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_PROFILE_SCHEMA_UNSUPPORTED"
            )
        if profile.canonical_json() != payload:
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_PROFILE_NOT_CANONICAL"
            )
        return profile


@dataclass(frozen=True, slots=True)
class EndpointCapabilityEvidence:
    profile_json: str
    capabilities_hash: str

    @classmethod
    def from_profile(cls, profile: EndpointCapabilities) -> EndpointCapabilityEvidence:
        return cls(
            profile_json=profile.canonical_json(),
            capabilities_hash=profile.capabilities_hash(),
        )

    def validated_profile(
        self,
        *,
        expected_scope: EndpointCapabilityProbeScope | None = None,
    ) -> EndpointCapabilities:
        if len(self.capabilities_hash) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.capabilities_hash
        ):
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_HASH_INVALID"
            )
        profile = EndpointCapabilities.from_json(self.profile_json)
        if self.capabilities_hash != profile.capabilities_hash():
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_HASH_MISMATCH"
            )
        if expected_scope is not None and profile.probe_scope is not expected_scope:
            raise EndpointCapabilityEvidenceError(
                "ENDPOINT_CAPABILITY_PROBE_SCOPE_MISMATCH"
            )
        return profile


class EndpointCapabilitiesProbe(Protocol):
    def probe_read_only(self, root: Path) -> EndpointCapabilityEvidence: ...

    def probe_controlled_writable(
        self,
        root: Path,
        *,
        probe_directory: Path,
        probe_token: str,
    ) -> EndpointCapabilityEvidence: ...


_PROFILE_FIELDS = tuple(EndpointCapabilities.__dataclass_fields__)


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return _required_str(value)


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError
    return value


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError
    return value


def _required_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError
