from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from mediasync_home.application.host_locator import (
    LOCAL_ENGINE_HOST_PUBLICATION_FILENAME,
    HostLocatorViolation,
    LocalEngineHostDescriptor,
    LocalEngineHostPublication,
    build_local_engine_host_descriptor,
    local_engine_host_publication_from_payload,
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


def local_engine_host_publication_path(state_root: Path) -> Path:
    return state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME


def publish_local_engine_host_publication(
    publication: LocalEngineHostPublication,
) -> Path:
    path = local_engine_host_publication_path(publication.state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(publication.to_payload(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return path


def load_local_engine_host_publication(
    state_root: Path,
) -> LocalEngineHostPublication | None:
    path = local_engine_host_publication_path(state_root)
    if not path.is_file():
        return None
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise HostLocatorViolation("HOST_LOCATOR_PUBLICATION_NOT_OBJECT")
    return local_engine_host_publication_from_payload(
        {str(key): value for key, value in raw_payload.items()}
    )
