from __future__ import annotations

import json
import re
from dataclasses import dataclass


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_FILE_PRECONDITION_SCHEMA_VERSION = 1


class SourceFilePreconditionError(ValueError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True, slots=True)
class SourceFilePrecondition:
    snapshot_id: str
    snapshot_entry_id: str
    relative_path: str
    size_bytes: int
    identity_fingerprint_hash: str
    schema_version: int = SOURCE_FILE_PRECONDITION_SCHEMA_VERSION
    object_type: str = "file"

    def __post_init__(self) -> None:
        if (
            not self.snapshot_id.strip()
            or not self.snapshot_entry_id.strip()
            or not self.relative_path.strip()
        ):
            raise SourceFilePreconditionError(
                "SOURCE_PRECONDITION_IDENTITY_INVALID",
                "Refresh the source snapshot before starting this backup.",
            )
        if (
            self.schema_version != SOURCE_FILE_PRECONDITION_SCHEMA_VERSION
            or self.object_type != "file"
            or self.size_bytes < 0
            or HASH_PATTERN.fullmatch(self.identity_fingerprint_hash) is None
        ):
            raise SourceFilePreconditionError(
                "SOURCE_PRECONDITION_EVIDENCE_INVALID",
                "Refresh the source snapshot before starting this backup.",
            )

    def to_json(self) -> str:
        return json.dumps(
            {
                "identity_fingerprint_hash": self.identity_fingerprint_hash,
                "object_type": self.object_type,
                "relative_path": self.relative_path,
                "schema_version": self.schema_version,
                "size_bytes": self.size_bytes,
                "snapshot_entry_id": self.snapshot_entry_id,
                "snapshot_id": self.snapshot_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str | None) -> SourceFilePrecondition:
        if payload is None:
            raise SourceFilePreconditionError(
                "SOURCE_PRECONDITION_MISSING",
                "Refresh analysis to bind the source file before starting this backup.",
            )
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SourceFilePreconditionError(
                "SOURCE_PRECONDITION_INVALID",
                "Refresh analysis to bind the source file before starting this backup.",
            ) from exc
        if not isinstance(value, dict):
            raise SourceFilePreconditionError(
                "SOURCE_PRECONDITION_INVALID",
                "Refresh analysis to bind the source file before starting this backup.",
            )
        try:
            return cls(
                snapshot_id=_required_text(value.get("snapshot_id")),
                snapshot_entry_id=_required_text(value.get("snapshot_entry_id")),
                relative_path=_required_text(value.get("relative_path")),
                size_bytes=_required_int(value.get("size_bytes")),
                identity_fingerprint_hash=_required_text(
                    value.get("identity_fingerprint_hash")
                ),
                schema_version=_required_int(value.get("schema_version")),
                object_type=_required_text(value.get("object_type")),
            )
        except (TypeError, ValueError) as exc:
            raise SourceFilePreconditionError(
                "SOURCE_PRECONDITION_INVALID",
                "Refresh analysis to bind the source file before starting this backup.",
            ) from exc


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source precondition text is invalid")
    return value


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("source precondition integer is invalid")
    return value
