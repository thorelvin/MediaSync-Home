from __future__ import annotations

import json
import re
from typing import Mapping

from mediasync_home.application.named_streams import NamedStreamRecord


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_NAMED_STREAM_FINGERPRINTS = 64


class FileObjectFingerprintError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


def canonical_file_object_fingerprint(
    fingerprint: Mapping[str, object],
    *,
    require_named_stream_inventory: bool = False,
) -> dict[str, object]:
    allowed_keys = {"byte_count", "content_hash"}
    keys = set(fingerprint)
    if keys not in (allowed_keys, allowed_keys | {"named_streams"}):
        raise FileObjectFingerprintError("FILE_OBJECT_FINGERPRINT_SHAPE_INVALID")
    byte_count = _byte_count(fingerprint.get("byte_count"))
    content_hash = _content_hash(fingerprint.get("content_hash"))
    if "named_streams" not in fingerprint:
        if require_named_stream_inventory:
            raise FileObjectFingerprintError(
                "FILE_OBJECT_FINGERPRINT_STREAM_INVENTORY_MISSING"
            )
        return {"byte_count": byte_count, "content_hash": content_hash}

    raw_streams = fingerprint.get("named_streams")
    if not isinstance(raw_streams, list) or len(raw_streams) > MAX_NAMED_STREAM_FINGERPRINTS:
        raise FileObjectFingerprintError(
            "FILE_OBJECT_FINGERPRINT_STREAMS_INVALID"
        )
    streams: list[dict[str, object]] = []
    seen_names: set[str] = set()
    for raw_stream in raw_streams:
        if not isinstance(raw_stream, dict) or set(raw_stream) != {
            "name",
            "byte_count",
            "content_hash",
        }:
            raise FileObjectFingerprintError(
                "FILE_OBJECT_FINGERPRINT_STREAM_INVALID"
            )
        name = raw_stream.get("name")
        try:
            record = NamedStreamRecord(
                stream_name=name if isinstance(name, str) else "",
                size_bytes=_byte_count(raw_stream.get("byte_count")),
            )
        except ValueError as exc:
            raise FileObjectFingerprintError(
                "FILE_OBJECT_FINGERPRINT_STREAM_INVALID"
            ) from exc
        comparison_name = record.stream_name.casefold()
        if comparison_name in seen_names:
            raise FileObjectFingerprintError(
                "FILE_OBJECT_FINGERPRINT_STREAM_NAMES_NOT_UNIQUE"
            )
        seen_names.add(comparison_name)
        streams.append(
            {
                "name": record.stream_name,
                "byte_count": record.size_bytes,
                "content_hash": _content_hash(raw_stream.get("content_hash")),
            }
        )
    streams.sort(key=lambda item: str(item["name"]).casefold())
    return {
        "byte_count": byte_count,
        "content_hash": content_hash,
        "named_streams": streams,
    }


def file_object_fingerprint_from_json(
    raw: str,
    *,
    require_named_stream_inventory: bool = False,
) -> dict[str, object]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FileObjectFingerprintError(
            "FILE_OBJECT_FINGERPRINT_JSON_INVALID"
        ) from exc
    if not isinstance(decoded, dict):
        raise FileObjectFingerprintError("FILE_OBJECT_FINGERPRINT_SHAPE_INVALID")
    canonical = canonical_file_object_fingerprint(
        decoded,
        require_named_stream_inventory=require_named_stream_inventory,
    )
    if raw != canonical_file_object_fingerprint_json(canonical):
        raise FileObjectFingerprintError("FILE_OBJECT_FINGERPRINT_NOT_CANONICAL")
    return canonical


def canonical_file_object_fingerprint_json(
    fingerprint: Mapping[str, object],
) -> str:
    canonical = canonical_file_object_fingerprint(fingerprint)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def has_named_stream_inventory(fingerprint: Mapping[str, object]) -> bool:
    return "named_streams" in fingerprint


def file_object_fingerprints_match(
    observed: Mapping[str, object],
    expected: Mapping[str, object],
) -> bool:
    canonical_observed = canonical_file_object_fingerprint(observed)
    canonical_expected = canonical_file_object_fingerprint(expected)
    if has_named_stream_inventory(canonical_expected):
        return canonical_observed == canonical_expected
    return (
        canonical_observed["byte_count"] == canonical_expected["byte_count"]
        and canonical_observed["content_hash"] == canonical_expected["content_hash"]
    )


def named_stream_fingerprints(
    fingerprint: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    canonical = canonical_file_object_fingerprint(
        fingerprint,
        require_named_stream_inventory=True,
    )
    streams = canonical["named_streams"]
    if not isinstance(streams, list):
        raise AssertionError("canonical named stream inventory is not a list")
    return tuple(streams)


def _byte_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FileObjectFingerprintError("FILE_OBJECT_FINGERPRINT_BYTES_INVALID")
    return value


def _content_hash(value: object) -> str:
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise FileObjectFingerprintError("FILE_OBJECT_FINGERPRINT_HASH_INVALID")
    return value
