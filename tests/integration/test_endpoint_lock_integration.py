from __future__ import annotations

import os
from pathlib import Path

import pytest

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, Win32EndpointLockOpener


@pytest.mark.skipif(os.name != "nt", reason="Win32 endpoint locks require Windows")
def test_win32_endpoint_lock_opener_keeps_mutation_lock_exclusive(tmp_path: Path) -> None:
    lock_path = tmp_path / "target" / ".mediasync" / "locks" / "mutation.lock"
    lock_path.parent.mkdir(parents=True)
    opener = Win32EndpointLockOpener()

    first = opener.acquire_exclusive_lock(lock_path)
    try:
        with pytest.raises(EndpointLeaseUnavailable) as exc_info:
            opener.acquire_exclusive_lock(lock_path)
        assert exc_info.value.validation_code == "ENDPOINT_LEASE_UNAVAILABLE"
    finally:
        first.close()

    second = opener.acquire_exclusive_lock(lock_path)
    second.close()
