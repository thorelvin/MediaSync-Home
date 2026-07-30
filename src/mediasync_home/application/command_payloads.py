from __future__ import annotations

import json

from blake3 import blake3


def canonical_command_payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return blake3(canonical.encode("utf-8")).hexdigest()
