from __future__ import annotations

from collections.abc import Callable, Sequence

from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole


Emit = Callable[[str], None]


def run_role(
    role: ProcessRole,
    argv: Sequence[str] | None = None,
    *,
    emit: Emit | None = None,
) -> int:
    del argv
    output = emit or print
    output(startup_status(role).to_json())
    return 0

