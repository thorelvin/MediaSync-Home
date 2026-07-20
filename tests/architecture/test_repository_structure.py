from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from mediasync_home.application.ports import FinalCommitPort
from mediasync_home.domain.capabilities import MutationPermit


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/mediasync_home"
TESTS = ROOT / "tests"
FORBIDDEN_ROBOCOPY_FLAGS = {"/MIR", "/MOV", "/MOVE", "/PURGE"}
DYNAMIC_CODE_BUILTINS = {"__import__", "compile", "eval", "exec"}


def _python_files(relative: str) -> Iterable[Path]:
    return sorted((PACKAGE / relative).rglob("*.py"))


def _python_tree(root: Path) -> Iterable[Path]:
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = _syntax_tree(path)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _syntax_tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _production_package_files() -> Iterable[Path]:
    return _python_tree(PACKAGE)


def _relative_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _string_literals(path: Path) -> Iterable[str]:
    for node in ast.walk(_syntax_tree(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _literal_tokens(value: str) -> set[str]:
    separators = ",;[](){}"
    normalized = value.upper()
    for separator in separators:
        normalized = normalized.replace(separator, " ")
    return set(normalized.split())


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


def test_python_code_does_not_import_forbidden_object_serializers() -> None:
    forbidden = {"pickle", "marshal"}
    offenders = [
        path.relative_to(ROOT).as_posix()
        for root in (PACKAGE, TESTS)
        for path in _python_tree(root)
        if _imports(path) & forbidden
    ]

    assert offenders == []


def test_production_code_does_not_embed_forbidden_robocopy_flags() -> None:
    offenders = [
        f"{_relative_path(path)}: {sorted(_literal_tokens(value) & FORBIDDEN_ROBOCOPY_FLAGS)}"
        for path in _production_package_files()
        for value in _string_literals(path)
        if _literal_tokens(value) & FORBIDDEN_ROBOCOPY_FLAGS
    ]

    assert offenders == []


def test_production_code_does_not_pass_shell_true() -> None:
    offenders: list[str] = []
    for path in _production_package_files():
        for node in ast.walk(_syntax_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "shell":
                    continue
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    offenders.append(f"{_relative_path(path)}:{keyword.lineno}")

    assert offenders == []


def test_production_code_does_not_call_dynamic_python_execution() -> None:
    offenders: list[str] = []
    for path in _production_package_files():
        for node in ast.walk(_syntax_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in DYNAMIC_CODE_BUILTINS:
                offenders.append(f"{_relative_path(path)}:{node.lineno}:{node.func.id}")

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
