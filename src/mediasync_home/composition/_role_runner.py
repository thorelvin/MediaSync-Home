from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from mediasync_home.adapters.runtime_policy import current_process_runtime_policy
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole


Emit = Callable[[str], None]
ROOT = Path(__file__).resolve().parents[3]


def run_role(
    role: ProcessRole,
    argv: Sequence[str] | None = None,
    *,
    emit: Emit | None = None,
) -> int:
    del argv
    output = emit or print
    output(
        startup_status(
            role,
            runtime_policy=current_process_runtime_policy(ROOT),
        ).to_json()
    )
    return 0
