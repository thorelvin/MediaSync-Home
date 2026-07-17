from __future__ import annotations

from collections.abc import Sequence

from mediasync_home.composition._role_runner import Emit
from mediasync_home.composition.ui import run_ui


def main(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    return run_ui(argv, emit=emit)
