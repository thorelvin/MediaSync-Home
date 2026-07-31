from __future__ import annotations

import hashlib
import json
from pathlib import Path


DIRECTORY_MARKER_NAME = ".mediasync-created-directory.json"


def directory_marker_bytes(
    *,
    run_id: str,
    run_target_id: str,
    operation_id: str,
    final_relative_path: str,
) -> bytes:
    payload = {
        "final_relative_path": final_relative_path.replace("\\", "/"),
        "object_role": "CREATED_DIRECTORY_RECOVERY_MARKER",
        "operation_id": operation_id,
        "run_id": run_id,
        "run_target_id": run_target_id,
        "schema_version": 1,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def directory_artifact_fingerprint(
    *,
    run_id: str,
    run_target_id: str,
    operation_id: str,
    final_relative_path: str,
) -> dict[str, object]:
    marker = directory_marker_bytes(
        run_id=run_id,
        run_target_id=run_target_id,
        operation_id=operation_id,
        final_relative_path=final_relative_path,
    )
    return {
        "byte_count": 0,
        "content_hash": hashlib.sha256(marker).hexdigest(),
    }


def directory_artifact_matches(
    path: Path,
    *,
    run_id: str,
    run_target_id: str,
    operation_id: str,
    final_relative_path: str,
) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        entries = tuple(path.iterdir())
    except OSError:
        return False
    if len(entries) != 1:
        return False
    marker_path = entries[0]
    if (
        marker_path.name != DIRECTORY_MARKER_NAME
        or marker_path.is_symlink()
        or not marker_path.is_file()
    ):
        return False
    expected = directory_marker_bytes(
        run_id=run_id,
        run_target_id=run_target_id,
        operation_id=operation_id,
        final_relative_path=final_relative_path,
    )
    try:
        return marker_path.read_bytes() == expected
    except OSError:
        return False
