from __future__ import annotations

import hashlib
import json
import os


def stable_file_identity_hash(value: os.stat_result) -> str:
    payload = {
        "attributes": int(getattr(value, "st_file_attributes", 0)),
        "birthtime_ns": int(getattr(value, "st_birthtime_ns", 0)),
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": int(value.st_mode),
        "modified_ns": int(value.st_mtime_ns),
        "size_bytes": int(value.st_size),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
