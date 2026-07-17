from __future__ import annotations

from collections.abc import Sequence

from mediasync_home.composition._role_runner import Emit
from mediasync_home.composition.trigger_client import run_trigger_client


def main(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    return run_trigger_client(argv, emit=emit)

