from __future__ import annotations

from pathlib import Path

import pytest

from mediasync_home.adapters.reparse_guard import (
    LocalReparseGuard,
    ReparseGuardError,
    ReparseInspection,
)


def test_reparse_guard_rejects_reparse_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    reparse_parent = root / "Pictures"
    reparse_parent.mkdir(parents=True)
    guard = LocalReparseGuard(probe=_OverlayProbe(reparse_paths={reparse_parent}))

    with pytest.raises(ReparseGuardError) as exc_info:
        guard.reject_reparse_chain(
            root=root,
            relative_parts=("Pictures", "A.jpg"),
            missing_code="CHAIN_MISSING",
            missing_next_action="refresh",
            reparse_code="CHAIN_REPARSE",
            reparse_next_action="revalidate",
        )

    assert exc_info.value.validation_code == "CHAIN_REPARSE"


def test_reparse_guard_rejects_reparse_endpoint_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    guard = LocalReparseGuard(probe=_OverlayProbe(reparse_paths={root}))

    with pytest.raises(ReparseGuardError) as exc_info:
        guard.resolve_existing_root(
            root,
            missing_code="ROOT_MISSING",
            missing_next_action="create root",
            reparse_code="ROOT_REPARSE",
            reparse_next_action="adopt root again",
        )

    assert exc_info.value.validation_code == "ROOT_REPARSE"


def test_reparse_guard_allows_clean_missing_suffix_for_control_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    guard = LocalReparseGuard(probe=_OverlayProbe(reparse_paths=set()))

    evidence = guard.reject_reparse_chain(
        root=root,
        relative_parts=(".mediasync", "objects", "versions"),
        missing_code="CHAIN_MISSING",
        missing_next_action="refresh",
        reparse_code="CHAIN_REPARSE",
        reparse_next_action="revalidate",
        allow_missing_suffix=True,
    )

    assert evidence.inspected_paths == (root,)
    assert evidence.checked_path == root / ".mediasync"


class _OverlayProbe:
    def __init__(self, *, reparse_paths: set[Path]) -> None:
        self._reparse_paths = {path.resolve(strict=False) for path in reparse_paths}

    def inspect_path(self, path: Path) -> ReparseInspection:
        resolved = path.resolve(strict=False)
        return ReparseInspection(
            path=path,
            exists=path.exists() or path.is_symlink(),
            is_reparse_point=resolved in self._reparse_paths,
        )
