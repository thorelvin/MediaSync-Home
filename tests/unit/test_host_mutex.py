from __future__ import annotations

import os
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 mutex adapter is Windows-only")


if os.name == "nt":
    from mediasync_home.adapters.host_mutex import EngineHostMutexError, LocalEngineHostMutex


def test_local_engine_host_mutex_rejects_second_owner_until_released() -> None:
    name = f"Local\\MediaSyncHome-0B-{uuid4().hex[:24]}"
    first = LocalEngineHostMutex.acquire(name)
    try:
        with pytest.raises(EngineHostMutexError) as exc_info:
            LocalEngineHostMutex.acquire(name)
        assert exc_info.value.validation_code == "ENGINE_HOST_ALREADY_RUNNING"
    finally:
        first.close()

    second = LocalEngineHostMutex.acquire(name)
    second.close()
