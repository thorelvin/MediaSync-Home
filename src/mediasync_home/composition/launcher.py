from __future__ import annotations

from collections.abc import Sequence

from mediasync_home.composition._role_runner import Emit, run_role
from mediasync_home.domain.process_roles import ProcessRole


def run_launcher(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    return run_role(ProcessRole.LAUNCHER, argv, emit=emit)

