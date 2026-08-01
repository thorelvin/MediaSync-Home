from __future__ import annotations

import os
import inspect
from pathlib import Path

import pytest

from mediasync_home.adapters.file_identity import file_birthtime_ns, stable_file_identity_hash
from mediasync_home.adapters import file_identity
from mediasync_home.adapters.local_snapshot_scanner import (
    LocalFilesystemSnapshotScanner,
)
from mediasync_home.adapters.reparse_guard import (
    LocalFilesystemReparsePathProbe,
    ReparseInspection,
)
from mediasync_home.application.snapshot_scanning import DirectoryCaseContext


class _FixedCaseModeProbe:
    def __init__(self, case_mode: str = "CASE_INSENSITIVE") -> None:
        self._case_mode = case_mode

    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext:
        del path
        return DirectoryCaseContext(
            case_mode=self._case_mode,
            evidence="FIXED_TEST_CASE_MODE_V1",
            error_code=(
                "CASE_MODE_UNAVAILABLE" if self._case_mode == "UNKNOWN" else None
            ),
        )


class _NestedDirectoryReparseProbe:
    def __init__(self, nested: Path) -> None:
        self._nested = nested
        self._delegate = LocalFilesystemReparsePathProbe()

    def inspect_path(self, path: Path) -> ReparseInspection:
        if path == self._nested:
            return ReparseInspection(
                path=path,
                exists=True,
                is_reparse_point=True,
                final_path=str(path),
            )
        return self._delegate.inspect_path(path)


def test_birthtime_adapter_never_uses_ctime_as_creation_time() -> None:
    assert "st_ctime" not in inspect.getsource(file_identity)


def test_local_snapshot_scanner_enumerates_nested_tree_deterministically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    nested = root / "Photos" / "Empty"
    nested.mkdir(parents=True)
    (root / "Readme.txt").write_text("hello", encoding="utf-8")
    (root / "Photos" / "Image.jpg").write_bytes(b"image")
    scanner = LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe(),
    )

    first = scanner.scan(
        root,
        snapshot_id="snapshot-a",
        exclude_control_area=False,
    )
    replay = scanner.scan(
        root,
        snapshot_id="snapshot-a",
        exclude_control_area=False,
    )

    assert first.complete is True
    assert first == replay
    assert [(entry.relative_path, entry.object_type, entry.size_bytes) for entry in first.entries] == [
        ("Photos", "directory", None),
        ("Readme.txt", "file", 5),
        ("Photos/Empty", "directory", None),
        ("Photos/Image.jpg", "file", 5),
    ]
    assert all(
        entry.identity_fingerprint_hash is not None
        for entry in first.entries
        if entry.object_type == "file"
    )
    assert all(
        entry.identity_fingerprint_hash is None
        for entry in first.entries
        if entry.object_type != "file"
    )
    assert all(
        entry.birthtime_ns is not None and entry.birthtime_ns > 0
        for entry in first.entries
        if entry.object_type in {"file", "directory"}
    )
    assert {
        entry.relative_path: entry.birthtime_ns
        for entry in first.entries
        if entry.object_type == "file"
    } == {
        "Readme.txt": file_birthtime_ns(root / "Readme.txt"),
        "Photos/Image.jpg": file_birthtime_ns(root / "Photos" / "Image.jpg"),
    }
    identities = {
        entry.relative_path: entry.identity_fingerprint_hash
        for entry in first.entries
        if entry.object_type == "file"
    }
    assert identities == {
        "Readme.txt": stable_file_identity_hash((root / "Readme.txt").stat()),
        "Photos/Image.jpg": stable_file_identity_hash(
            (root / "Photos" / "Image.jpg").stat()
        ),
    }
    assert [item.relative_path for item in first.coverage] == [
        ".",
        "Photos",
        "Photos/Empty",
    ]
    assert all(item.coverage_state == "COMPLETE" for item in first.coverage)
    assert all(item.case_mode == "CASE_INSENSITIVE" for item in first.coverage)
    assert first.issues == ()


def test_local_snapshot_scanner_excludes_only_validated_control_area(
    tmp_path: Path,
) -> None:
    root = tmp_path / "target"
    control = root / ".mediasync"
    control.mkdir(parents=True)
    (control / "endpoint.json").write_text("{}", encoding="utf-8")
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    scanner = LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe(),
    )

    included = scanner.scan(
        root,
        snapshot_id="snapshot-included",
        exclude_control_area=False,
    )
    excluded = scanner.scan(
        root,
        snapshot_id="snapshot-excluded",
        exclude_control_area=True,
    )

    assert [entry.relative_path for entry in included.entries] == [
        ".mediasync",
        "keep.txt",
        ".mediasync/endpoint.json",
    ]
    assert [entry.relative_path for entry in excluded.entries] == ["keep.txt"]
    assert included.control_area_excluded is False
    assert excluded.control_area_excluded is True
    assert included.complete is True
    assert excluded.complete is True


def test_local_snapshot_scanner_records_entry_limit_as_blocking(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")

    scan = LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe(),
        max_entries=1,
    ).scan(
        root,
        snapshot_id="snapshot-limited",
        exclude_control_area=False,
    )

    assert scan.complete is False
    assert [item.coverage_state for item in scan.coverage] == ["CANCELLED"]
    assert [issue.error_code for issue in scan.issues] == [
        "SNAPSHOT_ENTRY_LIMIT_EXCEEDED"
    ]


def test_local_snapshot_scanner_blocks_unknown_case_context(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()

    scan = LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe("UNKNOWN"),
    ).scan(
        root,
        snapshot_id="snapshot-unknown-case",
        exclude_control_area=False,
    )

    assert scan.complete is False
    assert scan.coverage[0].coverage_state == "CASE_CONTEXT_UNKNOWN"
    assert scan.coverage[0].case_probe_error == "CASE_MODE_UNAVAILABLE"
    assert scan.issues[0].error_code == "CASE_MODE_UNAVAILABLE"


def test_local_snapshot_scanner_does_not_traverse_reparse_points(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    scan = LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe(),
    ).scan(
        root,
        snapshot_id="snapshot-reparse",
        exclude_control_area=False,
    )

    assert scan.complete is False
    assert [entry.relative_path for entry in scan.entries] == ["linked"]
    assert all("secret.txt" not in entry.relative_path for entry in scan.entries)
    assert scan.entries[0].object_type == "reparse"
    assert scan.issues[0].error_code == "SNAPSHOT_REPARSE_POINT_BLOCKED"
    if os.name != "nt":
        assert all(item.relative_path != "linked" for item in scan.coverage)


def test_local_snapshot_scanner_revalidates_queued_directory_before_traversal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "secret.txt").write_text("secret", encoding="utf-8")

    scan = LocalFilesystemSnapshotScanner(
        path_probe=_NestedDirectoryReparseProbe(nested),
        case_mode_probe=_FixedCaseModeProbe(),
    ).scan(
        root,
        snapshot_id="snapshot-swapped",
        exclude_control_area=False,
    )

    assert scan.complete is False
    assert [entry.relative_path for entry in scan.entries] == ["nested"]
    assert [item.coverage_state for item in scan.coverage] == [
        "COMPLETE",
        "REPARSE_BLOCKED",
    ]
    assert scan.issues[0].error_code == "SNAPSHOT_REPARSE_POINT_BLOCKED"
