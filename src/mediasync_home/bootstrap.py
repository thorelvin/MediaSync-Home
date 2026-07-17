from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.processes import engine_host_main, launcher_main, trigger_client_main, ui_main


Emit = Callable[[str], None]


ROLE_ENTRYPOINTS = {
    ProcessRole.LAUNCHER: launcher_main.main,
    ProcessRole.ENGINE_HOST: engine_host_main.main,
    ProcessRole.TRIGGER_CLIENT: trigger_client_main.main,
    ProcessRole.GUI: ui_main.main,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaSync Home role bootstrap")
    parser.add_argument(
        "--role",
        choices=[role.value for role in ProcessRole],
        default=ProcessRole.LAUNCHER.value,
        help="process role to start; defaults to launcher",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, emit: Emit | None = None) -> int:
    args = build_parser().parse_args(argv)
    role = ProcessRole(args.role)
    return ROLE_ENTRYPOINTS[role]([], emit=emit)

