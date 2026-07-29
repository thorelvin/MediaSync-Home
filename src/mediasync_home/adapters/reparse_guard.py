from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


class ReparseGuardError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True)
class ReparseInspection:
    path: Path
    exists: bool
    is_reparse_point: bool


@dataclass(frozen=True)
class ReparseGuardEvidence:
    root: Path
    checked_path: Path
    inspected_paths: tuple[Path, ...]


class ReparsePathProbe(Protocol):
    def inspect_path(self, path: Path) -> ReparseInspection: ...


class ReparseGuard(Protocol):
    def resolve_existing_root(
        self,
        root: Path,
        *,
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
    ) -> Path: ...

    def reject_reparse_chain(
        self,
        *,
        root: Path,
        relative_parts: tuple[str, ...],
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
        allow_missing_suffix: bool = False,
    ) -> ReparseGuardEvidence: ...

    def require_resolved_under_root(
        self,
        *,
        root: Path,
        path: Path,
        strict: bool,
        escape_code: str,
        escape_next_action: str,
    ) -> None: ...


class LocalFilesystemReparsePathProbe:
    def inspect_path(self, path: Path) -> ReparseInspection:
        try:
            stat_result = path.lstat()
        except FileNotFoundError:
            return ReparseInspection(path=path, exists=False, is_reparse_point=False)
        except OSError as exc:
            raise ReparseGuardError(
                "REPARSE_GUARD_INSPECTION_FAILED",
                "Retry after the filesystem path can be inspected without errors.",
            ) from exc
        attributes = int(getattr(stat_result, "st_file_attributes", 0))
        return ReparseInspection(
            path=path,
            exists=True,
            is_reparse_point=bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
            or stat.S_ISLNK(stat_result.st_mode),
        )


class LocalReparseGuard:
    def __init__(self, *, probe: ReparsePathProbe | None = None) -> None:
        self._probe = probe or LocalFilesystemReparsePathProbe()

    def resolve_existing_root(
        self,
        root: Path,
        *,
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
    ) -> Path:
        inspection = self._probe.inspect_path(root)
        if not inspection.exists:
            raise ReparseGuardError(missing_code, missing_next_action)
        if inspection.is_reparse_point:
            raise ReparseGuardError(reparse_code, reparse_next_action)
        try:
            return root.resolve(strict=True)
        except OSError as exc:
            raise ReparseGuardError(missing_code, missing_next_action) from exc

    def reject_reparse_chain(
        self,
        *,
        root: Path,
        relative_parts: tuple[str, ...],
        missing_code: str,
        missing_next_action: str,
        reparse_code: str,
        reparse_next_action: str,
        allow_missing_suffix: bool = False,
    ) -> ReparseGuardEvidence:
        inspected_paths: list[Path] = []
        root_inspection = self._probe.inspect_path(root)
        if not root_inspection.exists:
            raise ReparseGuardError(missing_code, missing_next_action)
        if root_inspection.is_reparse_point:
            raise ReparseGuardError(reparse_code, reparse_next_action)
        inspected_paths.append(root)

        current = root
        for part in relative_parts:
            current = current / part
            inspection = self._probe.inspect_path(current)
            if not inspection.exists:
                if allow_missing_suffix:
                    break
                raise ReparseGuardError(missing_code, missing_next_action)
            if inspection.is_reparse_point:
                raise ReparseGuardError(reparse_code, reparse_next_action)
            inspected_paths.append(current)
        return ReparseGuardEvidence(
            root=root,
            checked_path=current,
            inspected_paths=tuple(inspected_paths),
        )

    def require_resolved_under_root(
        self,
        *,
        root: Path,
        path: Path,
        strict: bool,
        escape_code: str,
        escape_next_action: str,
    ) -> None:
        try:
            path.resolve(strict=strict).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ReparseGuardError(escape_code, escape_next_action) from exc
