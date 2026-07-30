from __future__ import annotations

import os
from pathlib import Path

import pytest

from mediasync_home.adapters.reparse_guard import (
    FileIdentityEvidence,
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


def test_reparse_guard_records_existing_path_identity_evidence(tmp_path: Path) -> None:
    root = tmp_path / "root"
    parent = root / "Pictures"
    parent.mkdir(parents=True)
    guard = LocalReparseGuard()

    evidence = guard.reject_reparse_chain(
        root=root,
        relative_parts=("Pictures",),
        missing_code="CHAIN_MISSING",
        missing_next_action="refresh",
        reparse_code="CHAIN_REPARSE",
        reparse_next_action="revalidate",
    )

    assert evidence.inspected_paths == (root, parent)
    assert len(evidence.inspected_identities) == 2
    assert all(identity.value for identity in evidence.inspected_identities)
    expected_kind = (
        "WIN32_HANDLE_VOLUME_FILE_ID" if os.name == "nt" else "POSIX_LSTAT_DEVICE_INODE"
    )
    assert {identity.kind for identity in evidence.inspected_identities} == {expected_kind}


@pytest.mark.skipif(os.name != "nt", reason="handle final-path proof is Windows-specific")
def test_reparse_guard_rejects_handle_final_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    parent = root / "Pictures"
    parent.mkdir(parents=True)
    guard = LocalReparseGuard(
        probe=_FinalPathProbe(
            {
                root: "\\\\?\\C:\\endpoint-root",
                parent: "\\\\?\\C:\\other-root\\Pictures",
            }
        )
    )

    with pytest.raises(ReparseGuardError) as exc_info:
        guard.require_resolved_under_root(
            root=root,
            path=parent,
            strict=True,
            escape_code="PATH_ESCAPED",
            escape_next_action="refresh endpoint",
        )

    assert exc_info.value.validation_code == "PATH_ESCAPED"


@pytest.mark.skipif(os.name != "nt", reason="handle final-path proof is Windows-specific")
def test_reparse_guard_accepts_handle_final_path_under_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    parent = root / "Pictures"
    parent.mkdir(parents=True)
    guard = LocalReparseGuard(
        probe=_FinalPathProbe(
            {
                root: "\\\\?\\C:\\endpoint-root",
                parent: "\\\\?\\C:\\endpoint-root\\Pictures",
            }
        )
    )

    guard.require_resolved_under_root(
        root=root,
        path=parent,
        strict=True,
        escape_code="PATH_ESCAPED",
        escape_next_action="refresh endpoint",
    )


@pytest.mark.skipif(os.name != "nt", reason="handle final-path proof is Windows-specific")
def test_reparse_guard_rejects_reparse_swap_after_clean_chain_inspection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    parent = root / "Pictures"
    parent.mkdir(parents=True)
    guard = LocalReparseGuard(
        probe=_ReparseSwapProbe(
            root=root,
            swapped_path=parent,
            root_final_path="\\\\?\\C:\\endpoint-root",
            clean_final_path="\\\\?\\C:\\endpoint-root\\Pictures",
        )
    )

    evidence = guard.reject_reparse_chain(
        root=root,
        relative_parts=("Pictures",),
        missing_code="CHAIN_MISSING",
        missing_next_action="refresh",
        reparse_code="CHAIN_REPARSE",
        reparse_next_action="revalidate",
    )

    assert evidence.inspected_paths == (root, parent)
    with pytest.raises(ReparseGuardError) as exc_info:
        guard.require_resolved_under_root(
            root=root,
            path=parent,
            strict=True,
            escape_code="PATH_ESCAPED",
            escape_next_action="refresh endpoint",
        )

    assert exc_info.value.validation_code == "PATH_ESCAPED"


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


class _FinalPathProbe:
    def __init__(self, final_paths: dict[Path, str]) -> None:
        self._final_paths = {
            path.resolve(strict=False): final_path for path, final_path in final_paths.items()
        }

    def inspect_path(self, path: Path) -> ReparseInspection:
        resolved = path.resolve(strict=False)
        final_path = self._final_paths.get(resolved)
        return ReparseInspection(
            path=path,
            exists=final_path is not None,
            is_reparse_point=False,
            identity=(
                None
                if final_path is None
                else FileIdentityEvidence(kind="WIN32_HANDLE_VOLUME_FILE_ID", value=final_path)
            ),
            final_path=final_path,
        )


class _ReparseSwapProbe:
    def __init__(
        self,
        *,
        root: Path,
        swapped_path: Path,
        root_final_path: str,
        clean_final_path: str,
    ) -> None:
        self._root = root.resolve(strict=False)
        self._swapped_path = swapped_path.resolve(strict=False)
        self._root_final_path = root_final_path
        self._clean_final_path = clean_final_path
        self._calls: dict[Path, int] = {}

    def inspect_path(self, path: Path) -> ReparseInspection:
        resolved = path.resolve(strict=False)
        self._calls[resolved] = self._calls.get(resolved, 0) + 1
        if resolved == self._root:
            return ReparseInspection(
                path=path,
                exists=True,
                is_reparse_point=False,
                identity=FileIdentityEvidence(
                    kind="WIN32_HANDLE_VOLUME_FILE_ID",
                    value=self._root_final_path,
                ),
                final_path=self._root_final_path,
            )
        if resolved == self._swapped_path:
            swapped = self._calls[resolved] > 1
            return ReparseInspection(
                path=path,
                exists=True,
                is_reparse_point=swapped,
                identity=FileIdentityEvidence(
                    kind="WIN32_HANDLE_VOLUME_FILE_ID",
                    value=self._clean_final_path,
                ),
                final_path=self._clean_final_path,
            )
        return ReparseInspection(path=path, exists=False, is_reparse_point=False)
