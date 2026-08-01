from __future__ import annotations

import pytest

from mediasync_home.application.named_streams import (
    NamedStreamInspection,
    NamedStreamRecord,
    NamedStreamState,
)


def test_named_stream_inspection_requires_state_specific_evidence() -> None:
    assert NamedStreamInspection(state=NamedStreamState.NONE).error_code is None
    assert (
        NamedStreamInspection(
            state=NamedStreamState.PRESENT,
            observed_named_stream_count=1,
            named_streams=(
                NamedStreamRecord(stream_name=":metadata:$DATA", size_bytes=8),
            ),
        ).observed_named_stream_count
        == 1
    )
    assert (
        NamedStreamInspection(
            state=NamedStreamState.UNKNOWN,
            error_code="ENUMERATION_FAILED",
        ).error_code
        == "ENUMERATION_FAILED"
    )

    with pytest.raises(ValueError, match="NAMED_STREAM_NONE_EVIDENCE_INVALID"):
        NamedStreamInspection(
            state=NamedStreamState.NONE,
            observed_named_stream_count=1,
        )
    with pytest.raises(ValueError, match="NAMED_STREAM_PRESENT_EVIDENCE_INVALID"):
        NamedStreamInspection(state=NamedStreamState.PRESENT)
    with pytest.raises(ValueError, match="NAMED_STREAM_UNKNOWN_EVIDENCE_INVALID"):
        NamedStreamInspection(state=NamedStreamState.UNKNOWN)


def test_named_stream_records_require_canonical_safe_names_and_order() -> None:
    with pytest.raises(ValueError, match="NAMED_STREAM_NAME_INVALID"):
        NamedStreamRecord(stream_name="::$DATA", size_bytes=1)
    with pytest.raises(ValueError, match="NAMED_STREAM_SIZE_INVALID"):
        NamedStreamRecord(stream_name=":metadata:$DATA", size_bytes=-1)
    with pytest.raises(ValueError, match="NAMED_STREAM_RECORDS_NOT_CANONICAL"):
        NamedStreamInspection(
            state=NamedStreamState.PRESENT,
            observed_named_stream_count=2,
            named_streams=(
                NamedStreamRecord(stream_name=":zeta:$DATA", size_bytes=1),
                NamedStreamRecord(stream_name=":alpha:$DATA", size_bytes=1),
            ),
        )
