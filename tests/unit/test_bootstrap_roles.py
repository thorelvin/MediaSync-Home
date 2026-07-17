from __future__ import annotations

import json

import pytest

from mediasync_home import bootstrap
from mediasync_home.domain.process_roles import ProcessRole


@pytest.mark.parametrize("role", list(ProcessRole))
def test_bootstrap_dispatches_each_process_role(role: ProcessRole) -> None:
    output: list[str] = []

    exit_code = bootstrap.main(["--role", role.value], emit=output.append)

    assert exit_code == 0
    assert len(output) == 1
    payload = json.loads(output[0])
    assert payload | {"runtime_policy": None} == {
        "application": "MediaSync Home",
        "mutations_enabled": False,
        "protocol_version": 1,
        "ready": True,
        "role": role.value,
        "runtime_policy": None,
        "schema_version": 1,
        "scope": "0B_NON_MUTATING_LOCAL_PREVIEW",
    }
    assert payload["runtime_policy"]["evaluated"] is True
    assert isinstance(payload["runtime_policy"]["reasons"], list)


def test_bootstrap_defaults_to_launcher_role() -> None:
    output: list[str] = []

    exit_code = bootstrap.main([], emit=output.append)

    assert exit_code == 0
    assert json.loads(output[0])["role"] == ProcessRole.LAUNCHER.value
