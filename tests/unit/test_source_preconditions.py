from __future__ import annotations

import json

import pytest

from mediasync_home.application.source_preconditions import (
    SourceFilePrecondition,
    SourceFilePreconditionError,
)


def test_source_file_precondition_round_trips_canonical_json() -> None:
    precondition = SourceFilePrecondition(
        snapshot_id="snapshot-a",
        snapshot_entry_id="entry-a",
        relative_path="Pictures/A.jpg",
        size_bytes=128,
        identity_fingerprint_hash="a" * 64,
    )

    encoded = precondition.to_json()

    assert SourceFilePrecondition.from_json(encoded) == precondition
    assert json.loads(encoded)["schema_version"] == 1


@pytest.mark.parametrize(
    "payload",
    (
        None,
        "not-json",
        "[]",
        '{"schema_version":2}',
    ),
)
def test_source_file_precondition_rejects_incomplete_or_unknown_evidence(
    payload: str | None,
) -> None:
    with pytest.raises(SourceFilePreconditionError):
        SourceFilePrecondition.from_json(payload)
