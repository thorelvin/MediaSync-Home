from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping

from mediasync_home.application.host_locator import (
    LocalEngineHostDescriptor,
    build_local_engine_host_descriptor,
    validate_installation_id,
)


def build_local_engine_host_descriptor_for_user(
    *,
    installation_id: str,
    user_scope_hash: str,
    state_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LocalEngineHostDescriptor:
    return build_local_engine_host_descriptor(
        installation_id=installation_id,
        user_scope_hash=user_scope_hash,
        state_root=state_root
        if state_root is not None
        else default_local_preview_state_root(installation_id, environ=environ),
    )


def default_local_preview_state_root(
    installation_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    validate_installation_id(installation_id)
    env = environ or os.environ
    base = env.get("LOCALAPPDATA")
    if base is None or not base.strip():
        base_path = Path.home() / "AppData" / "Local"
    else:
        base_path = Path(base)
    return (
        base_path.expanduser().resolve()
        / "MediaSyncHome"
        / "0b-local-preview"
        / installation_id
    )
