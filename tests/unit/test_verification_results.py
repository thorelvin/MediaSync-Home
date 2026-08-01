from __future__ import annotations

import pytest

from mediasync_home.application.verification_results import (
    AssuranceLevel,
    DurabilityState,
    TransferState,
    canonical_assurance_level,
    canonical_durability_state,
    canonical_transfer_state,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TRANSFERRED_TO_STAGING", TransferState.TRANSFERRED),
        ("ROBOCOPY_EXIT_1_COPIED_TRANSFERRED_TO_STAGING", TransferState.TRANSFERRED),
        ("NOT_TRANSFERRED", TransferState.FAILED),
        (None, TransferState.NOT_STARTED),
    ],
)
def test_transfer_evidence_maps_without_upgrading_unknown_claims(
    raw: str | None,
    expected: TransferState,
) -> None:
    assert (
        canonical_transfer_state(raw, fallback=TransferState.NOT_STARTED)
        is expected
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FULL_HASH", AssuranceLevel.PRIMARY_STREAM_HASH_VERIFIED),
        (
            "STAGING_HASH_MATCHES_POST_TRANSFER_SOURCE_HASH",
            AssuranceLevel.PRIMARY_STREAM_HASH_VERIFIED,
        ),
        ("STAGING_DIRECTORY_MARKER_VERIFIED", AssuranceLevel.MANIFEST_VERIFIED),
        ("NOT_RECORDED", AssuranceLevel.NONE),
        (None, AssuranceLevel.NONE),
    ],
)
def test_assurance_evidence_maps_to_the_supported_claim_only(
    raw: str | None,
    expected: AssuranceLevel,
) -> None:
    assert canonical_assurance_level(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED",
            DurabilityState.WRITE_THROUGH_REQUEST_CONFIRMED,
        ),
        (
            "LOCAL_DIRECTORY_MARKER_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED",
            DurabilityState.WRITE_THROUGH_REQUEST_CONFIRMED,
        ),
        ("FILE_FSYNC_COMPLETED", DurabilityState.LOCAL_FILE_FLUSH_CONFIRMED),
        ("DURABLE", DurabilityState.UNKNOWN),
        (None, DurabilityState.NOT_REQUESTED),
    ],
)
def test_durability_evidence_never_upgrades_an_ambiguous_claim(
    raw: str | None,
    expected: DurabilityState,
) -> None:
    assert (
        canonical_durability_state(raw, fallback=DurabilityState.NOT_REQUESTED)
        is expected
    )
