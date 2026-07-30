from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import mediasync_home.adapters.local_host_locator as local_host_locator_module
from mediasync_home.adapters.local_host_locator import (
    build_local_engine_host_descriptor_for_user,
    clear_stale_local_engine_host_publication,
    default_local_preview_state_root,
    load_matching_live_local_engine_host_publication,
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


def test_local_host_locator_adapter_clears_only_matching_stale_publication(
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
    newer_publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
        mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        state_root=state_root,
        process_id=9999,
    )

    publish_local_engine_host_publication(newer_publication)
    assert clear_stale_local_engine_host_publication(publication) is False
    assert load_local_engine_host_publication(state_root) == newer_publication

    assert clear_stale_local_engine_host_publication(newer_publication) is True
    assert load_local_engine_host_publication(state_root) is None


def test_load_matching_live_publication_accepts_unknown_process_liveness(
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
        state_root=tmp_path / "state",
        process_id=4321,
    )
    process_probe = _ProcessProbe(is_running=None)
    publish_local_engine_host_publication(publication)

    assert (
        load_matching_live_local_engine_host_publication(
            descriptor,
            process_probe=process_probe,
        )
        == publication
    )
    assert process_probe.process_ids == [4321]
    assert load_local_engine_host_publication(tmp_path / "state") == publication


def test_load_matching_live_publication_clears_dead_matching_publication(
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
        state_root=tmp_path / "state",
        process_id=4321,
    )
    process_probe = _ProcessProbe(is_running=False)
    publish_local_engine_host_publication(publication)

    assert (
        load_matching_live_local_engine_host_publication(
            descriptor,
            process_probe=process_probe,
        )
        is None
    )
    assert process_probe.process_ids == [4321]
    assert load_local_engine_host_publication(tmp_path / "state") is None


def test_load_matching_live_publication_keeps_mismatched_dead_publication(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    descriptor = build_local_engine_host_descriptor(
        installation_id="local-dev",
        user_scope_hash=USER_HASH,
        state_root=state_root,
    )
    other_descriptor = build_local_engine_host_descriptor(
        installation_id="other-dev",
        user_scope_hash=USER_HASH,
        state_root=state_root,
    )
    publication = build_local_engine_host_publication(
        installation_id=other_descriptor.installation_id,
        pipe_name=other_descriptor.pipe_name,
        mutex_name=other_descriptor.mutex_name,
        state_root=state_root,
        process_id=4321,
    )
    process_probe = _ProcessProbe(is_running=False)
    publish_local_engine_host_publication(publication)

    assert (
        load_matching_live_local_engine_host_publication(
            descriptor,
            process_probe=process_probe,
        )
        is None
    )
    assert process_probe.process_ids == []
    assert load_local_engine_host_publication(state_root) == publication


@pytest.mark.skipif(os.name != "nt", reason="Windows process liveness probe requires Windows")
def test_local_process_liveness_probe_reports_current_process_live() -> None:
    probe = local_host_locator_module.LocalProcessLivenessProbe()

    assert probe.is_process_running(os.getpid()) is True


def test_local_host_locator_adapter_returns_none_when_state_root_is_missing(
    tmp_path: Path,
) -> None:
    assert load_local_engine_host_publication(tmp_path / "missing-state-root") is None


def test_local_host_locator_adapter_rejects_guard_reported_missing_root_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_host_locator_module,
        "LocalReparseGuard",
        lambda: _RejectingReparseGuard(),
    )

    with pytest.raises(HostLocatorViolation, match="REPARSE"):
        load_local_engine_host_publication(tmp_path / "missing-state-root")


def test_local_host_locator_adapter_rejects_reparse_publication_file(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    publication_path = local_engine_host_publication_path(state_root)
    _symlink_or_skip(target, publication_path)

    with pytest.raises(HostLocatorViolation, match="REPARSE"):
        load_local_engine_host_publication(state_root)


def test_local_host_locator_adapter_rejects_guard_reported_publication_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    local_engine_host_publication_path(state_root).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        local_host_locator_module,
        "LocalReparseGuard",
        lambda: _RejectingReparseGuard(),
    )

    with pytest.raises(HostLocatorViolation, match="REPARSE"):
        load_local_engine_host_publication(state_root)


def test_local_host_locator_publish_refuses_reparse_publication_file(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("do-not-touch", encoding="utf-8")
    publication_path = local_engine_host_publication_path(state_root)
    _symlink_or_skip(target, publication_path)
    publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
        mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        state_root=state_root,
        process_id=4321,
    )

    with pytest.raises(HostLocatorViolation, match="REPARSE"):
        publish_local_engine_host_publication(publication)

    assert target.read_text(encoding="utf-8") == "do-not-touch"
    assert publication_path.is_symlink()


def test_local_host_locator_publish_rejects_guard_reported_publication_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
        mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        state_root=state_root,
        process_id=4321,
    )
    monkeypatch.setattr(
        local_host_locator_module,
        "LocalReparseGuard",
        lambda: _RejectingReparseGuard(),
    )

    with pytest.raises(HostLocatorViolation, match="REPARSE"):
        publish_local_engine_host_publication(publication)

    assert not local_engine_host_publication_path(state_root).exists()


def test_local_host_locator_publish_preserves_existing_temp_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    publication = build_local_engine_host_publication(
        installation_id="local-dev",
        pipe_name="MediaSyncHome-0B-1234567890abcdef12345678",
        mutex_name="Local\\MediaSyncHome-0B-1234567890abcdef12345678",
        state_root=state_root,
        process_id=4321,
    )
    state_root.mkdir()
    temp_path = state_root / "engine-host.locator.json.777.fixedtmp.tmp"
    temp_path.write_text("do-not-delete", encoding="utf-8")

    monkeypatch.setattr(local_host_locator_module.os, "getpid", lambda: 777)
    monkeypatch.setattr(
        local_host_locator_module,
        "uuid4",
        lambda: _FixedUuid("fixedtmp"),
    )

    with pytest.raises(FileExistsError):
        publish_local_engine_host_publication(publication)

    assert temp_path.read_text(encoding="utf-8") == "do-not-delete"


class _FixedUuid:
    def __init__(self, hex_value: str) -> None:
        self.hex = hex_value


class _ProcessProbe:
    def __init__(self, *, is_running: bool | None) -> None:
        self._is_running = is_running
        self.process_ids: list[int] = []

    def is_process_running(self, process_id: int) -> bool | None:
        self.process_ids.append(process_id)
        return self._is_running


class _RejectingReparseGuard:
    def reject_reparse_chain(self, **kwargs: object) -> object:
        del kwargs
        raise local_host_locator_module.ReparseGuardError(
            "HOST_LOCATOR_PUBLICATION_REPARSE_UNSUPPORTED",
            "remove reparse path",
        )


def _symlink_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable on this host: {exc}")
