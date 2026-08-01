from __future__ import annotations

import pytest

from mediasync_home.application.file_object_fingerprints import (
    FileObjectFingerprintError,
    canonical_file_object_fingerprint,
    canonical_file_object_fingerprint_json,
    file_object_fingerprint_from_json,
    has_named_stream_inventory,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def test_file_object_fingerprint_canonicalizes_named_stream_inventory() -> None:
    fingerprint = canonical_file_object_fingerprint(
        {
            "byte_count": 7,
            "content_hash": HASH_A,
            "named_streams": [
                {"name": ":zeta:$DATA", "byte_count": 2, "content_hash": HASH_C},
                {"name": ":alpha:$DATA", "byte_count": 1, "content_hash": HASH_B},
            ],
        },
        require_named_stream_inventory=True,
    )

    assert fingerprint["named_streams"] == [
        {"name": ":alpha:$DATA", "byte_count": 1, "content_hash": HASH_B},
        {"name": ":zeta:$DATA", "byte_count": 2, "content_hash": HASH_C},
    ]
    encoded = canonical_file_object_fingerprint_json(fingerprint)
    assert file_object_fingerprint_from_json(
        encoded,
        require_named_stream_inventory=True,
    ) == fingerprint
    assert has_named_stream_inventory(fingerprint) is True


def test_file_object_fingerprint_preserves_legacy_primary_only_evidence() -> None:
    legacy = canonical_file_object_fingerprint(
        {"byte_count": 7, "content_hash": HASH_A}
    )

    assert legacy == {"byte_count": 7, "content_hash": HASH_A}
    assert has_named_stream_inventory(legacy) is False
    with pytest.raises(
        FileObjectFingerprintError,
        match="FILE_OBJECT_FINGERPRINT_STREAM_INVENTORY_MISSING",
    ):
        canonical_file_object_fingerprint(
            legacy,
            require_named_stream_inventory=True,
        )


@pytest.mark.parametrize(
    "named_streams",
    [
        [
            {"name": "::$DATA", "byte_count": 1, "content_hash": HASH_B},
        ],
        [
            {"name": ":same:$DATA", "byte_count": 1, "content_hash": HASH_B},
            {"name": ":SAME:$DATA", "byte_count": 1, "content_hash": HASH_C},
        ],
    ],
)
def test_file_object_fingerprint_rejects_unsafe_or_duplicate_stream_names(
    named_streams: list[dict[str, object]],
) -> None:
    with pytest.raises(FileObjectFingerprintError):
        canonical_file_object_fingerprint(
            {
                "byte_count": 7,
                "content_hash": HASH_A,
                "named_streams": named_streams,
            }
        )
