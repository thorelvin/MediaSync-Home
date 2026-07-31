from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediasync_home.adapters import endpoint_leases
from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointRootDescriptor,
    FencingTokenAllocationError,
    LocalEndpointLeaseAuthority,
    LocalResolvingEndpointLeaseAuthority,
    MutationPermitIssueError,
    ResourceLeaseRegistrationError,
    Win32EndpointLockHandle,
    Win32EndpointLockOpener,
)
from mediasync_home.application.runs import EndpointLeaseRequest
from mediasync_home.domain.capabilities import MutationPermit


def test_local_endpoint_lease_authority_opens_lock_and_allocates_fencing_token(
    tmp_path: Path,
) -> None:
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


def test_local_resolving_endpoint_lease_authority_resolves_root_before_acquiring(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    opener = _FakeOpener(handle)
    token_store = _FakeTokenStore(42)
    resolver = _FakeRootResolver({"endpoint:target-a": root})
    authority = LocalResolvingEndpointLeaseAuthority(
        root_resolver=resolver,
        token_store=token_store,
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is True
    assert attempt.lease is not None
    assert attempt.lease.lock_path == root / ".mediasync" / "locks" / "mutation.lock"
    assert resolver.requests == [("endpoint:target-a", "target-a", "target-rev-a")]
    assert opener.paths == [root / ".mediasync" / "locks" / "mutation.lock"]
    assert token_store.requests == [("endpoint:target-a", 1)]


def test_local_resolving_endpoint_lease_authority_validates_resolved_identity(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(
        tmp_path,
        control_area_id="control-a",
        root_identity_hash_algorithm="BLAKE3-256",
        root_identity_hash="a" * 64,
        marker_checksum_algorithm="BLAKE3-256",
        marker_checksum="b" * 64,
    )
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    opener = _FakeOpener(handle)
    token_store = _FakeTokenStore(42)
    resolver = _FakeRootDescriptorResolver(
        {
            "endpoint:target-a": EndpointRootDescriptor(
                root=root,
                endpoint_generation=7,
                control_area_id="control-a",
                root_identity_hash_algorithm="BLAKE3-256",
                root_identity_hash="a" * 64,
                owner_installation_id="owner-a",
                ownership_epoch=1,
                marker_checksum_algorithm="BLAKE3-256",
                marker_checksum="b" * 64,
            )
        }
    )
    authority = LocalResolvingEndpointLeaseAuthority(
        root_resolver=resolver,
        token_store=token_store,
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is True
    assert attempt.lease is not None
    assert attempt.lease.endpoint_generation == 7
    assert attempt.lease.issue_mutation_permit().endpoint_generation == 7
    assert resolver.descriptor_requests == [
        ("endpoint:target-a", "target-a", "target-rev-a")
    ]
    assert resolver.path_requests == []
    assert opener.paths == [root / ".mediasync" / "locks" / "mutation.lock"]
    assert token_store.requests == [("endpoint:target-a", 1)]


def test_local_resolving_endpoint_lease_authority_reports_unknown_resolved_root(
    tmp_path: Path,
) -> None:
    opener = _FakeOpener(_FakeHandle(tmp_path / "unused.lock"))
    authority = LocalResolvingEndpointLeaseAuthority(
        root_resolver=_FakeRootResolver({}),
        token_store=_FakeTokenStore(42),
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_LEASE_RESOURCE_UNKNOWN",)
    assert opener.paths == []


def test_local_resolving_endpoint_lease_authority_returns_resolver_rejection(
    tmp_path: Path,
) -> None:
    opener = _FakeOpener(_FakeHandle(tmp_path / "unused.lock"))
    authority = LocalResolvingEndpointLeaseAuthority(
        root_resolver=_RejectingRootResolver(),
        token_store=_FakeTokenStore(42),
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_ROOT_URI_UNSUPPORTED",)
    assert opener.paths == []


def test_local_endpoint_lease_authority_registers_durable_resource_lease(
    tmp_path: Path,
) -> None:
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
        "endpoint_generation": 1,
        "lease_mode": "EXCLUSIVE",
        "os_lock_kind": "LOCAL_OS_HANDLE",
    }
    assert handle.closed is False

    attempt.lease.release()

    assert handle.closed is True
    assert resource_store.releases == [attempt.lease.lease_id]


def test_local_endpoint_lease_authority_rejects_root_identity_mismatch(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(
        tmp_path,
        root_identity_hash_algorithm="BLAKE3-256",
        root_identity_hash="b" * 64,
    )
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    token_store = _FakeTokenStore(42)
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        target_identities={
            "endpoint:target-a": EndpointRootDescriptor(
                root=root,
                endpoint_generation=1,
                root_identity_hash_algorithm="BLAKE3-256",
                root_identity_hash="a" * 64,
            )
        },
        token_store=token_store,
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_ROOT_IDENTITY_MISMATCH",)
    assert token_store.requests == []
    assert handle.closed is True


def test_local_endpoint_lease_authority_reconciles_stale_durable_lease_after_lock_acquired(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    resource_store = _FakeResourceLeaseStore(42, stale_active_lease_ids=("lease-old",))
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        resource_lease_store=resource_store,
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is True
    assert attempt.lease is not None
    assert resource_store.reconciliations == [("endpoint:target-a", "target-a")]
    assert resource_store.reconciled_lease_ids == ["lease-old"]
    assert resource_store.events[0] == "reconcile:endpoint:target-a:target-a"
    assert resource_store.events[1].startswith("register:")
    assert handle.closed is False


def test_local_endpoint_lease_authority_closes_lock_when_stale_lease_reconciliation_fails(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        resource_lease_store=_ReconcileFailingResourceLeaseStore(),
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_RESOURCE_LEASE_ACTIVE_CONFLICT",)
    assert handle.closed is True


def test_local_endpoint_lease_issues_current_mutation_permit(tmp_path: Path) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=_FakeTokenStore(42),
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is True
    assert attempt.lease is not None
    assert attempt.lease.live is True
    permit = attempt.lease.issue_mutation_permit()
    attempt.lease.assert_mutation_permit_current(permit)
    assert isinstance(permit, MutationPermit)
    assert permit.lease_id == attempt.lease.lease_id
    assert permit.resource_key == "endpoint:target-a"
    assert permit.owner_installation_id == "owner-a"
    assert permit.ownership_epoch == 1
    assert permit.fencing_token == 42
    assert permit.run_id == "run-a"
    assert permit.run_target_id == "run-a-target-0000"
    assert permit.endpoint_id == "target-a"
    assert permit.endpoint_generation == 1
    assert permit.endpoint_revision_id == "target-rev-a"
    with pytest.raises(TypeError, match="not serializable"):
        permit.__reduce__()


def test_local_endpoint_lease_rejects_new_permit_after_release(tmp_path: Path) -> None:
    root = _endpoint_root(tmp_path)
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=_FakeTokenStore(42),
        lock_opener=_FakeOpener(
            _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
        ),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.lease is not None
    attempt.lease.release()
    with pytest.raises(MutationPermitIssueError) as exc_info:
        attempt.lease.issue_mutation_permit()
    assert exc_info.value.validation_code == "MUTATION_PERMIT_LEASE_RELEASED"


def test_local_endpoint_lease_rejects_new_permit_after_lock_loss(
    tmp_path: Path,
) -> None:
    root = _endpoint_root(tmp_path)
    handle = _FakeHandle(root / ".mediasync" / "locks" / "mutation.lock")
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=_FakeTokenStore(42),
        lock_opener=_FakeOpener(handle),
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.lease is not None
    handle.lose()
    assert attempt.lease.live is False
    with pytest.raises(MutationPermitIssueError) as exc_info:
        attempt.lease.issue_mutation_permit()
    assert exc_info.value.validation_code == "MUTATION_PERMIT_LEASE_LOST"


def test_local_endpoint_lease_rejects_permit_from_other_lease(tmp_path: Path) -> None:
    root_a = _endpoint_root(tmp_path / "a")
    root_b = _endpoint_root(tmp_path / "b", endpoint_id="target-b")
    authority = LocalEndpointLeaseAuthority(
        target_roots={
            "endpoint:target-a": root_a,
            "endpoint:target-b": root_b,
        },
        token_store=_FakeTokenStore(42),
        lock_opener=_MultiHandleOpener(),
    )

    attempt_a = authority.acquire_endpoint_lease(_request())
    attempt_b = authority.acquire_endpoint_lease(
        _request(
            run_target_id="run-a-target-0001",
            endpoint_id="target-b",
            endpoint_revision_id="target-rev-b",
            resource_key="endpoint:target-b",
        )
    )

    assert attempt_a.lease is not None
    assert attempt_b.lease is not None
    permit_b = attempt_b.lease.issue_mutation_permit()
    with pytest.raises(MutationPermitIssueError) as exc_info:
        attempt_a.lease.assert_mutation_permit_current(permit_b)
    assert exc_info.value.validation_code == "MUTATION_PERMIT_LEASE_MISMATCH"


def test_local_endpoint_lease_authority_reports_unknown_resource_without_opening(
    tmp_path: Path,
) -> None:
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


def test_local_endpoint_lease_authority_requires_existing_control_area(
    tmp_path: Path,
) -> None:
    root = tmp_path / "target"
    root.mkdir()
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


def test_local_endpoint_lease_authority_reports_unavailable_root_before_control_checks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "disconnected-target"
    opener = _FakeOpener(_FakeHandle(root / ".mediasync" / "locks" / "mutation.lock"))
    authority = LocalEndpointLeaseAuthority(
        target_roots={"endpoint:target-a": root},
        token_store=_FakeTokenStore(1),
        lock_opener=opener,
    )

    attempt = authority.acquire_endpoint_lease(_request())

    assert attempt.acquired is False
    assert attempt.validation_codes == ("ENDPOINT_ROOT_UNAVAILABLE",)
    assert opener.paths == []


def test_local_endpoint_lease_authority_releases_lock_when_owner_mismatches(
    tmp_path: Path,
) -> None:
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


def test_local_endpoint_lease_authority_requires_token_or_resource_store(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="requires a token or resource lease store"):
        LocalEndpointLeaseAuthority(
            target_roots={"endpoint:target-a": tmp_path},
            lock_opener=_FakeOpener(_FakeHandle(tmp_path / "mutation.lock")),
        )


def test_win32_endpoint_lock_opener_reports_non_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(endpoint_leases.os, "name", "posix")

    with pytest.raises(EndpointLeaseUnavailable) as exc_info:
        Win32EndpointLockOpener().acquire_exclusive_lock(tmp_path / "mutation.lock")

    assert exc_info.value.validation_code == "ENDPOINT_LEASE_REQUIRES_WINDOWS"


def test_win32_endpoint_lock_handle_probes_file_handle_liveness(tmp_path: Path) -> None:
    probed_handles: list[int] = []

    def probe_file_handle(handle_value: int) -> bool:
        probed_handles.append(handle_value)
        return True

    handle = Win32EndpointLockHandle(
        path=tmp_path / "mutation.lock",
        handle_value=1234,
        _probe_file_handle=probe_file_handle,
    )

    assert handle.is_alive() is True
    assert probed_handles == [1234]


def test_win32_endpoint_lock_handle_latches_probe_loss(tmp_path: Path) -> None:
    probe_results = [False, True]

    def probe_file_handle(handle_value: int) -> bool:
        assert handle_value == 1234
        return probe_results.pop(0)

    handle = Win32EndpointLockHandle(
        path=tmp_path / "mutation.lock",
        handle_value=1234,
        _probe_file_handle=probe_file_handle,
    )

    assert handle.is_alive() is False
    assert handle.is_alive() is False
    assert probe_results == [True]


class _FakeHandle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.closed = False
        self.alive = True

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return self.alive and not self.closed

    def lose(self) -> None:
        self.alive = False


class _FakeOpener:
    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle
        self.paths: list[Path] = []

    def acquire_exclusive_lock(self, lock_path: Path) -> _FakeHandle:
        self.paths.append(lock_path)
        return self._handle


class _MultiHandleOpener:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def acquire_exclusive_lock(self, lock_path: Path) -> _FakeHandle:
        self.paths.append(lock_path)
        return _FakeHandle(lock_path)


class _UnavailableOpener:
    def acquire_exclusive_lock(self, lock_path: Path) -> _FakeHandle:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_LEASE_UNAVAILABLE",
            "Wait for the current endpoint writer to release the mutation lock.",
        )


class _FakeRootResolver:
    def __init__(self, roots: dict[str, Path]) -> None:
        self._roots = roots
        self.requests: list[tuple[str, str, str]] = []

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        self.requests.append((resource_key, endpoint_id, endpoint_revision_id))
        return self._roots.get(resource_key)


class _FakeRootDescriptorResolver:
    def __init__(self, descriptors: dict[str, EndpointRootDescriptor]) -> None:
        self._descriptors = descriptors
        self.descriptor_requests: list[tuple[str, str, str]] = []
        self.path_requests: list[tuple[str, str, str]] = []

    def resolve_endpoint_root_descriptor(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> EndpointRootDescriptor | None:
        self.descriptor_requests.append(
            (resource_key, endpoint_id, endpoint_revision_id)
        )
        return self._descriptors.get(resource_key)

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        self.path_requests.append((resource_key, endpoint_id, endpoint_revision_id))
        descriptor = self._descriptors.get(resource_key)
        if descriptor is None:
            return None
        return descriptor.root


class _RejectingRootResolver:
    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_ROOT_URI_UNSUPPORTED",
            "Use a local file endpoint root before acquiring a local mutation lease.",
        )


class _FakeTokenStore:
    def __init__(self, token: int) -> None:
        self._token = token
        self.requests: list[tuple[str, int]] = []

    def allocate_next_fencing_token(
        self, *, resource_key: str, ownership_epoch: int
    ) -> int:
        self.requests.append((resource_key, ownership_epoch))
        return self._token


class _FailingTokenStore:
    def allocate_next_fencing_token(
        self, *, resource_key: str, ownership_epoch: int
    ) -> int:
        raise FencingTokenAllocationError(
            "ENDPOINT_FENCING_TOKEN_UNAVAILABLE",
            "Retry after recovery storage is writable.",
        )


class _FakeResourceLeaseStore:
    def __init__(
        self, token: int, stale_active_lease_ids: tuple[str, ...] = ()
    ) -> None:
        self._token = token
        self._stale_active_lease_ids = list(stale_active_lease_ids)
        self.registrations: list[dict[str, object]] = []
        self.reconciliations: list[tuple[str, str]] = []
        self.reconciled_lease_ids: list[str] = []
        self.releases: list[str] = []
        self.events: list[str] = []

    def reconcile_stale_active_resource_lease_after_lock_acquired(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
    ) -> tuple[str, ...]:
        self.reconciliations.append((resource_key, endpoint_id))
        self.events.append(f"reconcile:{resource_key}:{endpoint_id}")
        lease_ids = tuple(self._stale_active_lease_ids)
        self.reconciled_lease_ids.extend(lease_ids)
        self._stale_active_lease_ids.clear()
        return lease_ids

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
        self.events.append(f"register:{lease_id}")
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
    def reconcile_stale_active_resource_lease_after_lock_acquired(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
    ) -> tuple[str, ...]:
        return ()

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


class _ReconcileFailingResourceLeaseStore:
    def reconcile_stale_active_resource_lease_after_lock_acquired(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
    ) -> tuple[str, ...]:
        raise ResourceLeaseRegistrationError(
            "ENDPOINT_RESOURCE_LEASE_ACTIVE_CONFLICT",
            "Review the active endpoint lease before reconciling stale local lock state.",
        )

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
        raise AssertionError("registration must not run when reconciliation fails")

    def release_resource_lease(self, *, lease_id: str) -> None:
        raise AssertionError("release must not be called when reconciliation fails")


def _request(
    *,
    run_target_id: str = "run-a-target-0000",
    endpoint_id: str = "target-a",
    endpoint_revision_id: str = "target-rev-a",
    resource_key: str = "endpoint:target-a",
) -> EndpointLeaseRequest:
    return EndpointLeaseRequest(
        run_id="run-a",
        run_target_id=run_target_id,
        endpoint_id=endpoint_id,
        endpoint_revision_id=endpoint_revision_id,
        resource_key=resource_key,
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
    )


def _endpoint_root(
    tmp_path: Path,
    *,
    owner_installation_id: str = "owner-a",
    ownership_epoch: int = 1,
    endpoint_id: str = "target-a",
    control_area_id: str | None = None,
    root_identity_hash_algorithm: str | None = None,
    root_identity_hash: str | None = None,
    marker_checksum_algorithm: str | None = None,
    marker_checksum: str | None = None,
) -> Path:
    root = tmp_path / "target"
    lock_dir = root / ".mediasync" / "locks"
    lock_dir.mkdir(parents=True)
    marker = {
        "endpoint_id": endpoint_id,
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
    }
    if control_area_id is not None:
        marker["control_area_id"] = control_area_id
    if root_identity_hash_algorithm is not None:
        marker["root_identity_hash_algorithm"] = root_identity_hash_algorithm
    if root_identity_hash is not None:
        marker["root_identity_hash"] = root_identity_hash
    if marker_checksum_algorithm is not None:
        marker["marker_checksum_algorithm"] = marker_checksum_algorithm
    if marker_checksum is not None:
        marker["marker_checksum"] = marker_checksum
    (root / ".mediasync" / "endpoint.json").write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )
    return root
