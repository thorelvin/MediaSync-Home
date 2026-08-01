from __future__ import annotations

import json

import pytest

from mediasync_home.application.endpoint_capabilities import (
    CaseMode,
    DurabilityLevel,
    EndpointCapabilities,
    EndpointCapabilityEvidence,
    EndpointCapabilityEvidenceError,
    EndpointCapabilityProbeScope,
    FileIdReliability,
    LockScope,
    SourceReadGuardLevel,
)


def test_endpoint_capability_evidence_is_canonical_and_hash_bound() -> None:
    profile = _profile()
    evidence = EndpointCapabilityEvidence.from_profile(profile)

    assert evidence.validated_profile() == profile
    assert len(evidence.capabilities_hash) == 64
    assert evidence.profile_json.startswith('{"default_case_mode":')


def test_endpoint_capability_evidence_rejects_tamper_and_wrong_scope() -> None:
    evidence = EndpointCapabilityEvidence.from_profile(_profile())
    payload = json.loads(evidence.profile_json)
    payload["supports_atomic_replace"] = False
    tampered = EndpointCapabilityEvidence(
        profile_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        capabilities_hash=evidence.capabilities_hash,
    )

    with pytest.raises(
        EndpointCapabilityEvidenceError,
        match="ENDPOINT_CAPABILITY_HASH_MISMATCH",
    ):
        tampered.validated_profile()
    with pytest.raises(
        EndpointCapabilityEvidenceError,
        match="ENDPOINT_CAPABILITY_PROBE_SCOPE_MISMATCH",
    ):
        evidence.validated_profile(expected_scope=EndpointCapabilityProbeScope.READ_ONLY)


def test_endpoint_capability_evidence_rejects_oversized_profile_and_bad_digest() -> None:
    evidence = EndpointCapabilityEvidence.from_profile(_profile())

    with pytest.raises(
        EndpointCapabilityEvidenceError,
        match="ENDPOINT_CAPABILITY_PROFILE_TOO_LARGE",
    ):
        EndpointCapabilities.from_json(" " * 32_769)
    with pytest.raises(
        EndpointCapabilityEvidenceError,
        match="ENDPOINT_CAPABILITY_HASH_INVALID",
    ):
        EndpointCapabilityEvidence(
            profile_json=evidence.profile_json,
            capabilities_hash="G" * 64,
        ).validated_profile()


def _profile() -> EndpointCapabilities:
    return EndpointCapabilities(
        probe_scope=EndpointCapabilityProbeScope.CONTROLLED_WRITABLE,
        filesystem_name="NTFS",
        maximum_file_size=None,
        maximum_component_length=255,
        maximum_path_length=32_760,
        timestamp_precision_ns=100,
        default_case_mode=CaseMode.INSENSITIVE,
        supports_per_directory_case_query=True,
        supports_reparse_inspection=True,
        supports_final_path_resolution=True,
        supports_directory_identity_handles=True,
        supports_atomic_rename=True,
        supports_no_overwrite_insert=True,
        supports_atomic_replace=True,
        supports_file_flush=True,
        supports_write_through_move=False,
        durability_level=DurabilityLevel.FILE_FLUSH_CONFIRMED,
        lock_scope=LockScope.LOCAL_MACHINE,
        supports_exclusive_control_lock=False,
        source_read_guard_level=SourceReadGuardLevel.POST_TRANSFER_HASH_ONLY,
        supports_file_ids=True,
        file_id_reliability=FileIdReliability.STABLE,
        supports_birthtime=True,
        supports_attributes=True,
        supports_named_streams=True,
        supports_sparse_files=True,
        supports_hardlinks=True,
        supports_encryption=True,
        supports_long_paths=True,
        is_network=False,
        is_removable=False,
        likely_rotational=None,
    )
