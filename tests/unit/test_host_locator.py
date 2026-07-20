from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mediasync_home.adapters.local_host_locator import (
    build_local_engine_host_descriptor_for_user,
    default_local_preview_state_root,
    load_local_engine_host_publication,
    local_engine_host_publication_path,
    publish_local_engine_host_publication,
)
from mediasync_home.application.host_locator import (
    LOCAL_ENGINE_HOST_PUBLICATION_FILENAME,
    HostLocatorViolation,
    build_local_engine_host_descriptor,
    build_local_engine_host_publication,
    local_engine_host_publication_matches_descriptor,
    local_engine_host_publication_from_payload,
    validate_local_preview_pipe_name,
    validate_local_preview_mutex_name,
)


USER_HASH = "a" * 64


def test_local_engine_host_descriptor_derives_stable_same_user_names() -> None:
    state_root = Path("C:/Users/Ada/AppData/Local/MediaSyncHome/0b-local-preview/local-dev")
    descriptor = build_local_engine_host_descriptor(
        installation_id="local-dev",
        user_scope_hash=USER_HASH.upper(),
        state_root=state_root,
    )
    expected_key = hashlib.sha256(f"{USER_HASH}:local-dev".encode("utf-8")).hexdigest()[:24]

    assert descriptor.installation_id == "local-dev"
    assert descriptor.user_scope_hash == USER_HASH
    assert descriptor.locator_key == expected_key
    assert descriptor.pipe_name == f"MediaSyncHome-0B-{expected_key}"
    assert descriptor.mutex_name == f"Local\\MediaSyncHome-0B-{expected_key}"
    assert descriptor.state_root == state_root
    assert descriptor.to_payload() == {
        "installation_id": "local-dev",
        "locator_key": expected_key,
        "mutex_name": f"Local\\MediaSyncHome-0B-{expected_key}",
        "pipe_name": f"MediaSyncHome-0B-{expected_key}",
        "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        "state_root": str(state_root),
    }


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"installation_id": "", "user_scope_hash": USER_HASH}, "HOST_LOCATOR_INVALID"),
        ({"installation_id": "../dev", "user_scope_hash": USER_HASH}, "HOST_LOCATOR_INVALID"),
        ({"installation_id": "local dev", "user_scope_hash": USER_HASH}, "HOST_LOCATOR_INVALID"),
        (
            {"installation_id": "a" * 65, "user_scope_hash": USER_HASH},
            "HOST_LOCATOR_INVALID",
        ),
        ({"installation_id": "local-dev", "user_scope_hash": "not-a-hash"}, "USER_SCOPE"),
        (
            {
                "installation_id": "local-dev",
                "user_scope_hash": USER_HASH,
                "state_root": Path("relative"),
            },
            "STATE_ROOT_MUST_BE_ABSOLUTE",
        ),
        (
            {
                "installation_id": "local-dev",
                "user_scope_hash": USER_HASH,
                "state_root": Path("//server/share/MediaSyncHome"),
            },
            "STATE_ROOT_MUST_BE_LOCAL",
        ),
    ],
)
def test_local_engine_host_descriptor_rejects_ambiguous_locator_inputs(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(HostLocatorViolation, match=reason):
        build_local_engine_host_descriptor(**kwargs)  # type: ignore[arg-type]


def test_default_local_preview_state_root_uses_local_appdata() -> None:
    root = default_local_preview_state_root(
        "local-dev",
        environ={"LOCALAPPDATA": "C:/Users/Ada/AppData/Local"},
    )

    assert root == Path("C:/Users/Ada/AppData/Local/MediaSyncHome/0b-local-preview/local-dev")


def test_local_host_locator_adapter_binds_default_state_root_to_descriptor() -> None:
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id="local-dev",
        user_scope_hash=USER_HASH,
        environ={"LOCALAPPDATA": "C:/Users/Ada/AppData/Local"},
    )

    assert descriptor.state_root == Path(
        "C:/Users/Ada/AppData/Local/MediaSyncHome/0b-local-preview/local-dev"
    )


def test_local_preview_mutex_name_validation_allows_only_locator_names() -> None:
    validate_local_preview_mutex_name("Local\\MediaSyncHome-0B-1234567890abcdef12345678")

    with pytest.raises(HostLocatorViolation, match="HOST_LOCATOR_INVALID_MUTEX_NAME"):
        validate_local_preview_mutex_name("Global\\MediaSyncHome-0B-1234567890abcdef12345678")


def test_local_preview_pipe_name_validation_allows_only_locator_names() -> None:
    validate_local_preview_pipe_name("MediaSyncHome-0B-1234567890abcdef12345678")

    with pytest.raises(HostLocatorViolation, match="HOST_LOCATOR_INVALID_PIPE_NAME"):
        validate_local_preview_pipe_name("MediaSyncHome-local-dev-1234567890abcdef12345678")


def test_local_engine_host_publication_payload_binds_pipe_mutex_and_process(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
        mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        state_root=state_root,
        process_id=4321,
    )

    assert publication.to_payload() == {
        "installation_id": "local-dev",
        "locator_key": "1234567890abcdef12345678",
        "mutex_name": "Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        "pipe_name": "MediaSyncHome-0B-1234567890abcdef12345678",
        "process_id": 4321,
        "schema_version": 1,
        "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        "state_root": str(state_root),
        "status": "STARTING",
    }
    assert local_engine_host_publication_from_payload(publication.to_payload()) == publication


def test_local_engine_host_publication_matches_only_same_descriptor(
    tmp_path: Path,
) -> None:
    descriptor = build_local_engine_host_descriptor(
        installation_id="local-dev",
        user_scope_hash=USER_HASH,
        state_root=tmp_path / "state",
    )
    publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=descriptor.state_root or tmp_path / "state",
        process_id=4321,
    )
    other_descriptor = build_local_engine_host_descriptor(
        installation_id="other-dev",
        user_scope_hash=USER_HASH,
        state_root=tmp_path / "state",
    )

    assert local_engine_host_publication_matches_descriptor(publication, descriptor) is True
    assert local_engine_host_publication_matches_descriptor(publication, other_descriptor) is False


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (
            {
                "installation_id": "local-dev",
                "pipe_name": "MediaSyncHome-0B-1234567890abcdef12345678",
                "mutex_name": "Local\\MediaSyncHome-0B-ffffffffffffffffffffffff",
                "process_id": 4321,
            },
            "PIPE_MUTEX_MISMATCH",
        ),
        (
            {
                "installation_id": "local-dev",
                "pipe_name": "MediaSyncHome-other-1234567890abcdef12345678",
                "mutex_name": "Local\\MediaSyncHome-0B-1234567890abcdef12345678",
                "process_id": 4321,
            },
            "INVALID_PIPE_NAME",
        ),
        (
            {
                "installation_id": "local-dev",
                "pipe_name": "MediaSyncHome-0B-1234567890abcdef12345678",
                "mutex_name": "Global\\MediaSyncHome-0B-1234567890abcdef12345678",
                "process_id": 4321,
            },
            "INVALID_MUTEX_NAME",
        ),
        (
            {
                "installation_id": "local-dev",
                "pipe_name": "MediaSyncHome-0B-1234567890abcdef12345678",
                "mutex_name": "Local\\MediaSyncHome-0B-1234567890abcdef12345678",
                "process_id": 0,
            },
            "INVALID_PROCESS_ID",
        ),
    ],
)
def test_local_engine_host_publication_rejects_ambiguous_inputs(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(HostLocatorViolation, match=reason):
        build_local_engine_host_publication(
            state_root=Path("C:/Users/Ada/AppData/Local/MediaSyncHome/0b-local-preview/local-dev"),
            **kwargs,
        )  # type: ignore[arg-type]


def test_local_engine_host_publication_rejects_relative_state_root() -> None:
    with pytest.raises(HostLocatorViolation, match="STATE_ROOT_MUST_BE_ABSOLUTE"):
        build_local_engine_host_publication(
            installation_id="local-dev",
            pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
            mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
            state_root=Path("relative"),
            process_id=4321,
        )


def test_local_host_locator_adapter_publishes_roundtrippable_record(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
        mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        state_root=state_root,
        process_id=4321,
    )

    path = publish_local_engine_host_publication(publication)

    assert path == state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME
    assert path == local_engine_host_publication_path(state_root)
    assert json.loads(path.read_text(encoding="utf-8")) == publication.to_payload()
    assert load_local_engine_host_publication(state_root) == publication
