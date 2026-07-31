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
        "schema_version": 2,
        "scope": "0B_NON_MUTATING_LOCAL_PREVIEW",
    }
    assert payload["runtime_policy"]["evaluated"] is True
    assert isinstance(payload["runtime_policy"]["reasons"], list)


def test_bootstrap_defaults_to_launcher_role() -> None:
    output: list[str] = []

    exit_code = bootstrap.main([], emit=output.append)

    assert exit_code == 0
    assert json.loads(output[0])["role"] == ProcessRole.LAUNCHER.value


def test_bootstrap_protocol_trigger_invocation_routes_to_trigger_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_trigger_client_main(argv: object, *, emit: object | None = None) -> int:
        captured["argv"] = argv
        captured["emit"] = emit
        return 17

    monkeypatch.setitem(
        bootstrap.ROLE_ENTRYPOINTS,
        ProcessRole.TRIGGER_CLIENT,
        fake_trigger_client_main,
    )
    output: list[str] = []

    exit_code = bootstrap.main(
        [
            "--enqueue-trigger-occurrence",
            "--schedule-id",
            "schedule-a",
            "--schedule-revision-hash",
            "a" * 64,
        ],
        emit=output.append,
    )

    assert exit_code == 17
    assert captured == {
        "argv": [
            "--enqueue-trigger-occurrence",
            "--schedule-id",
            "schedule-a",
            "--schedule-revision-hash",
            "a" * 64,
        ],
        "emit": output.append,
    }


def test_bootstrap_local_preview_host_invocation_routes_to_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_launcher_main(argv: object, *, emit: object | None = None) -> int:
        captured["argv"] = argv
        captured["emit"] = emit
        return 29

    monkeypatch.setitem(bootstrap.ROLE_ENTRYPOINTS, ProcessRole.LAUNCHER, fake_launcher_main)
    output: list[str] = []

    exit_code = bootstrap.main(
        ["--local-preview-host", "--pipe-name", "pipe-a"],
        emit=output.append,
    )

    assert exit_code == 29
    assert captured == {
        "argv": ["--local-preview-host", "--pipe-name", "pipe-a"],
        "emit": output.append,
    }


def test_bootstrap_explicit_role_wins_over_protocol_trigger_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_launcher_main(argv: object, *, emit: object | None = None) -> int:
        captured["argv"] = argv
        captured["emit"] = emit
        return 19

    monkeypatch.setitem(bootstrap.ROLE_ENTRYPOINTS, ProcessRole.LAUNCHER, fake_launcher_main)
    output: list[str] = []

    exit_code = bootstrap.main(
        ["--role", "launcher", "--enqueue-trigger-occurrence"],
        emit=output.append,
    )

    assert exit_code == 19
    assert captured == {
        "argv": ["--enqueue-trigger-occurrence"],
        "emit": output.append,
    }
