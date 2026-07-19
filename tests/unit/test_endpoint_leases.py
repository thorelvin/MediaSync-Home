from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediasync_home.adapters import endpoint_leases
from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    FencingTokenAllocationError,
    LocalEndpointLeaseAuthority,
    ResourceLeaseRegistrationError,
    Win32EndpointLockOpener,
)
from mediasync_home.application.runs import EndpointLeaseRequest


def test_local_endpoint_lease_authority_opens_lock_and_allocates_fencing_token(tmp_path: Path) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    opener = _FakeOpener(handle)
    token_store = _FakeTokenStore(42)
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=token_store,
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is True
    assert attempt.validation_codes == ()
    assert attempt.lease is not None
    assert attempt.lease.owner_installation_id == "owner-a"
    assert attempt.lease.ownership_epoch == 1
    assert attempt.lease.fencing_token == 42
    assert tuple(opener.paths) == (root / ".mediasync" / "locks" / "mutation.lock",)
    assert tuple(token_store.requests) == (("endpoint:target-a", 1),)
    assert handle.closed is False

    attempt.lease.release()

    assert handle.closed is True


def test_local_endpoint_lease_authority_registers_durable_resource_lease(tmp_path: Path) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    resource_store = _FakeResourceLeaseStore(42)
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        resource_lease_store=resource_store,
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is True
    assert attempt.lease is not None
    assert attempt.lease.fencing_token == 42
    assert len(resource_store.registrations) == 1
    assert resource_store.registrations[0] == {
        "lease_id": attempt.lease.lease_id,
        "resource_key": "endpoint:target-a",
        "owner_instance_id": "owner-a",
        "ownership_epoch": 1,
        "run_id": "run-a",
        "run_target_id": "run-a-target-0000",
        "endpoint_id": "target-a",
        "endpoint_generation": None,
        "lease_mode": "EXCLUSIVE",
        "os_lock_kind": "LOCAL_OS_HANDLE",
    }
    assert handle.closed is False

    attempt.lease.release()

    assert handle.closed is True
    assert resource_store.releases == [attempt.lease.lease_id]


def test_local_endpoint_lease_authority_reports_unknown_resource_without_opening(tmp_path: Path) -> None:
    opener = _FakeOpener(_FakeHandle(tmp_path / "unused.lock"))
    authority = LocalEndpointLeaseAuthority(
        target_roots={},
        token_store=_FakeTokenStore(1),
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_LEASE_RESOURCE_UNKNOWN",)
    assert opener.paths == []


def test_local_endpoint_lease_authority_requires_existing_control_area(tmp_path: Path) -> None:
    root = tmp_path / "target"
    opener = _FakeOpener(_FakeHandle(root / ".mediasync" / "locks" / "mutation.lock"))
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=_FakeTokenStore(1),
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_CONTROL_AREA_MISSING",)
    assert opener.paths == []


def test_local_endpoint_lease_authority_releases_lock_when_owner_mismatches(tmp_path: Path) -> None:
    root = _endpoint_root(tmp_path, owner_installation_id="owner-b")
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    token_store = _FakeTokenStore(42)
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=token_store,
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_OWNER_MISMATCH",)
    assert handle.closed is True
    assert token_store.requests == []


def test_local_endpoint_lease_authority_releases_lock_when_token_allocation_fails(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=_FailingTokenStore(),
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_FENCING_TOKEN_UNAVAILABLE",)
    assert handle.closed is True


def test_local_endpoint_lease_authority_releases_lock_when_resource_registration_fails(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        resource_lease_store=_FailingResourceLeaseStore(),
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_RESOURCE_LEASE_PERSISTENCE_FAILED",)
    assert handle.closed is True


def test_local_endpoint_lease_authority_reports_busy_lock_without_token_allocation(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    token_store = _FakeTokenStore(42)
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=token_store,
        lock_opener=_UnavailableOpener(),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_LEASE_UNAVAILABLE",)
    assert token_store.requests == []


def test_local_endpoint_lease_authority_requires_token_or_resource_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires a token or resource lease store"):
        LocalEndpointLeaseAuthority(
            target_roots={"endpoint:target-a": tmp_path},
            lock_opener=_FakeOpener(_FakeHandle(tmp_path / "mutation.lock")),
        )


def test_win32_endpoint_lock_opener_reports_non_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(endpoint_leases.os, "name", "posix")

    with pytest.raises(EndpointLeaseUnavailable) as exc_info:
        Win32EndpointLockOpener().acquire_exclusive_lock(tmp_path / "mutation.lock")

    assert exc_info.value.validation_code == "ENDPOINT_LEASE_REQUIRES_WINDOWS"


class _FakeHandle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeOpener:
    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle
        self.paths: list[Path] = []

    def acquire_exclusive_lock(self, lock_path: Path) -> _FakeHandle:
        self.paths.append(lock_path)
        return self._handle


class _UnavailableOpener:
    def acquire_exclusive_lock(self, lock_path: Path) -> _FakeHandle:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_LEASE_UNAVAILABLE",
            "Wait for the current endpoint writer to release the mutation lock.",
        )


class _FakeTokenStore:
    def __init__(self, token: int) -> None:
        self._token = token
        self.requests: list[tuple[str, int]] = []

    def allocate_next_fencing_token(self, *, resource_key: str, ownership_epoch: int) -> int:
        self.requests.append((resource_key, ownership_epoch))
        return self._token


class _FailingTokenStore:
    def allocate_next_fencing_token(self, *, resource_key: str, ownership_epoch: int) -> int:
        raise FencingTokenAllocationError(
            "ENDPOINT_FENCING_TOKEN_UNAVAILABLE",
            "Retry after recovery storage is writable.",
        )


class _FakeResourceLeaseStore:
    def __init__(self, token: int) -> None:
        self._token = token
        self.registrations: list[dict[str, object]] = []
        self.releases: list[str] = []

    def register_acquired_resource_lease(
        self,
        *,
        lease_id: str,
        resource_key: str,
        owner_instance_id: str,
        ownership_epoch: int,
        run_id: str,
        run_target_id: str,
        endpoint_id: str,
        endpoint_generation: int | None,
        lease_mode: str,
        os_lock_kind: str,
    ) -> int:
        self.registrations.append(
            {
                "lease_id": lease_id,
                "resource_key": resource_key,
                "owner_instance_id": owner_instance_id,
                "ownership_epoch": ownership_epoch,
                "run_id": run_id,
                "run_target_id": run_target_id,
                "endpoint_id": endpoint_id,
                "endpoint_generation": endpoint_generation,
                "lease_mode": lease_mode,
                "os_lock_kind": os_lock_kind,
            }
        )
        return self._token

    def release_resource_lease(self, *, lease_id: str) -> None:
        self.releases.append(lease_id)


class _FailingResourceLeaseStore:
    def register_acquired_resource_lease(
        self,
        *,
        lease_id: str,
        resource_key: str,
        owner_instance_id: str,
        ownership_epoch: int,
        run_id: str,
        run_target_id: str,
        endpoint_id: str,
        endpoint_generation: int | None,
        lease_mode: str,
        os_lock_kind: str,
    ) -> int:
        raise ResourceLeaseRegistrationError(
            "ENDPOINT_RESOURCE_LEASE_PERSISTENCE_FAILED",
            "Retry after recovery storage is writable.",
        )

    def release_resource_lease(self, *, lease_id: str) -> None:
        raise AssertionError("release must not be called when registration fails")


def _request() -> EndpointLeaseRequest:
    return EndpointLeaseRequest(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        resource_key="endpoint:target-a",
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
    )


def _endpoint_root(
    tmp_path: Path,
    *,
    owner_installation_id: str = "owner-a",
    ownership_epoch: int = 1,
    endpoint_id: str = "target-a",
) -> Path:
    root = tmp_path / "target"
    lock_dir = root / ".mediasync" / "locks"
    lock_dir.mkdir(parents=True)
    marker = {
        "endpoint_id": endpoint_id,
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
    }
    (root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )
    return root
