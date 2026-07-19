from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from mediasync_home.application.ports import FinalCommitPort
from mediasync_home.domain.capabilities import MutationPermit


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/mediasync_home"


def _python_files(relative: str) -> Iterable[Path]:
    return sorted((PACKAGE / relative).rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_0b_repository_skeleton_has_separate_role_entrypoints() -> None:
    expected = {
        "bootstrap.py",
        "processes/launcher_main.py",
        "processes/engine_host_main.py",
        "processes/trigger_client_main.py",
        "processes/ui_main.py",
        "composition/launcher.py",
        "composition/engine_host.py",
        "composition/trigger_client.py",
        "composition/ui.py",
        "application/ports.py",
        "domain/capabilities.py",
        "ipc/protocol.py",
        "presentation/__init__.py",
        "adapters/__init__.py",
    }

    missing = [relative for relative in sorted(expected) if not (PACKAGE / relative).is_file()]

    assert missing == []


def test_process_entrypoints_are_thin_composition_delegates() -> None:
    forbidden = {
        "sqlite3",
        "subprocess",
        "PySide6",
        "mediasync_home.adapters",
        "mediasync_home.application",
        "mediasync_home.domain",
        "mediasync_home.ipc",
        "mediasync_home.presentation",
    }

    for path in sorted((PACKAGE / "processes").glob("*_main.py")):
        imports = _imports(path)
        assert not imports & forbidden, f"{path} imports forbidden modules {imports & forbidden}"
        assert any(
            imported.startswith("mediasync_home.composition") for imported in imports
        ), f"{path} must delegate to a composition root"


def test_domain_application_presentation_boundaries_are_clean() -> None:
    forbidden_by_layer = {
        "domain": {
            "PySide6",
            "sqlite3",
            "subprocess",
            "mediasync_home.adapters",
            "mediasync_home.application",
            "mediasync_home.presentation",
        },
        "application": {
            "PySide6",
            "sqlite3",
            "subprocess",
            "mediasync_home.adapters",
            "mediasync_home.presentation",
        },
        "presentation": {
            "sqlite3",
            "subprocess",
            "mediasync_home.adapters",
        },
    }

    for layer, forbidden in forbidden_by_layer.items():
        for path in _python_files(layer):
            imports = _imports(path)
            assert not imports & forbidden, f"{path} imports forbidden modules {imports & forbidden}"


def test_no_generic_write_filesystem_port_in_application_or_domain() -> None:
    forbidden_names = {"filesystem.py", "file_system.py", "write_filesystem.py"}
    offenders = [
        path.relative_to(ROOT).as_posix()
        for layer in ("application", "domain")
        for path in _python_files(layer)
        if path.name.lower() in forbidden_names
    ]

    assert offenders == []


def test_final_commit_port_requires_opaque_mutation_permit() -> None:
    annotations = FinalCommitPort.commit_verified_artifact.__annotations__

    assert annotations["permit"] == "MutationPermit"
    assert annotations["artifact"] == "VerifiedStagingArtifact"

    try:
        MutationPermit()
    except TypeError as exc:
        assert "live lease adapter" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("MutationPermit should not be directly constructible")

    try:
        class ForgedPermit(MutationPermit):
            pass
    except TypeError as exc:
        assert "cannot be subclassed" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("MutationPermit should not be subclassable")
