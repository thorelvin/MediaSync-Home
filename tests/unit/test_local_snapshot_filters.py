from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from mediasync_home.adapters.local_snapshot_scanner import (
    LocalFilesystemSnapshotScanner,
)
from mediasync_home.application.file_filters import (
    FileFilterPolicy,
    FileFilterRule,
    FileFilterSession,
    FilterAction,
    FilterRuleKind,
)
from mediasync_home.application.named_streams import (
    NamedStreamInspection,
    NamedStreamProbe,
    NamedStreamState,
)
from mediasync_home.application.snapshot_scanning import (
    DirectoryCaseContext,
    FilesystemSnapshotScan,
)


class _FixedCaseModeProbe:
    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext:
        del path
        return DirectoryCaseContext(
            case_mode="CASE_INSENSITIVE",
            evidence="FIXED_TEST_CASE_MODE_V1",
        )


class _RecordingNamedStreamProbe:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def inspect_named_streams(self, path: Path) -> NamedStreamInspection:
        self.paths.append(path)
        return NamedStreamInspection(state=NamedStreamState.NONE)


def _policy(*rules: FileFilterRule, defaults: bool = False) -> FileFilterPolicy:
    return FileFilterPolicy(
        include_default_exclusions=defaults,
        rules=rules,
    )


def _scan(
    root: Path,
    policy: FileFilterPolicy | None = None,
    *,
    named_stream_probe: NamedStreamProbe | None = None,
    filter_session_factory: Callable[
        [FileFilterPolicy], FileFilterSession
    ] = FileFilterSession,
    max_entries: int = 100_000,
) -> FilesystemSnapshotScan:
    return LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe(),
        named_stream_probe=named_stream_probe,
        filter_session_factory=filter_session_factory,
        max_entries=max_entries,
    ).scan(
        root,
        snapshot_id="snapshot-filtered",
        exclude_control_area=False,
        filter_policy=policy,
    )


def test_default_filter_exclusions_are_applied_before_cataloging(tmp_path: Path) -> None:
    root = tmp_path / "source"
    (root / "$RECYCLE.BIN").mkdir(parents=True)
    (root / "$RECYCLE.BIN" / "deleted.txt").write_text("deleted", encoding="utf-8")
    (root / "System Volume Information").mkdir()
    (root / "System Volume Information" / "index.dat").write_text(
        "index", encoding="utf-8"
    )
    (root / "Thumbs.DB").write_text("thumbs", encoding="utf-8")
    (root / "desktop.ini").write_text("desktop", encoding="utf-8")
    (root / "scratch.TMP").write_text("temp", encoding="utf-8")
    (root / "~$document.docx").write_text("office", encoding="utf-8")
    (root / ".mediasync").mkdir()
    (root / ".mediasync" / "ordinary.txt").write_text("ordinary", encoding="utf-8")
    (root / "keep.txt").write_text("keep", encoding="utf-8")

    scan = _scan(root)

    assert [entry.relative_path for entry in scan.entries] == [
        ".mediasync",
        "keep.txt",
        ".mediasync/ordinary.txt",
    ]
    assert [item.relative_path for item in scan.coverage] == [".", ".mediasync"]
    assert scan.complete is True


def test_later_explicit_include_overrides_a_default_exclusion(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "Thumbs.db").write_text("wanted", encoding="utf-8")
    policy = _policy(
        FileFilterRule(
            "include-thumbs",
            FilterAction.INCLUDE,
            FilterRuleKind.FILE_NAME_GLOB,
            "Thumbs.db",
        ),
        defaults=True,
    )

    scan = _scan(root, policy)

    assert [entry.relative_path for entry in scan.entries] == ["Thumbs.db"]
    assert scan.complete is True


def test_metadata_filter_is_applied_after_one_stat(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "large.bin").write_bytes(b"12345")
    (root / "small.bin").write_bytes(b"1")
    policy = _policy(
        FileFilterRule(
            "exclude-files-at-least-five-bytes",
            FilterAction.EXCLUDE,
            FilterRuleKind.MIN_SIZE_BYTES,
            5,
        )
    )

    scan = _scan(root, policy)

    assert [entry.relative_path for entry in scan.entries] == ["small.bin"]


def test_empty_directory_exclusion_is_resolved_deepest_first(tmp_path: Path) -> None:
    root = tmp_path / "source"
    (root / "empty" / "nested").mkdir(parents=True)
    (root / "nonempty").mkdir()
    (root / "nonempty" / "keep.txt").write_text("keep", encoding="utf-8")
    policy = _policy(
        FileFilterRule(
            "exclude-empty-directories",
            FilterAction.EXCLUDE,
            FilterRuleKind.EMPTY_DIRECTORY,
            True,
        )
    )

    scan = _scan(root, policy)

    assert [entry.relative_path for entry in scan.entries] == [
        "nonempty",
        "nonempty/keep.txt",
    ]
    assert [item.relative_path for item in scan.coverage] == [".", "nonempty"]


def test_explicit_reparse_exclusion_avoids_the_blocking_reparse_issue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    policy = _policy(
        FileFilterRule(
            "exclude-reparse-points",
            FilterAction.EXCLUDE,
            FilterRuleKind.REPARSE_POINT,
            True,
        )
    )

    scan = _scan(root, policy)

    assert scan.entries == ()
    assert scan.issues == ()
    assert scan.complete is True


def test_excluded_entries_still_count_toward_the_scan_bound(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.tmp").write_text("excluded", encoding="utf-8")
    (root / "keep.txt").write_text("keep", encoding="utf-8")

    scan = _scan(root, max_entries=1)

    assert scan.entries == ()
    assert scan.complete is False
    assert [issue.error_code for issue in scan.issues] == [
        "SNAPSHOT_ENTRY_LIMIT_EXCEEDED"
    ]


def test_cheap_exclusion_skips_named_stream_inspection(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "excluded.tmp").write_text("excluded", encoding="utf-8")
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    probe = _RecordingNamedStreamProbe()

    scan = _scan(root, named_stream_probe=probe)

    assert [entry.relative_path for entry in scan.entries] == ["keep.txt"]
    assert root / "excluded.tmp" not in probe.paths
    assert root / "keep.txt" in probe.paths


def test_regex_budget_failure_marks_relevant_coverage_incomplete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / ("a" * 200 + "!")).write_text("value", encoding="utf-8")
    policy = FileFilterPolicy(
        advanced_regex_enabled=True,
        include_default_exclusions=False,
        rules=(
            FileFilterRule(
                "bounded-regex",
                FilterAction.EXCLUDE,
                FilterRuleKind.REGEX,
                "^(a+)+$",
            ),
        ),
    )

    scan = _scan(
        root,
        policy,
        filter_session_factory=lambda candidate: FileFilterSession(
            candidate,
            regex_match_timeout_seconds=1e-9,
            regex_total_budget_seconds=1e-6,
        ),
    )

    assert scan.complete is False
    assert scan.coverage[0].coverage_state == "FILTER_INCOMPLETE"
    assert scan.issues[0].issue_type == "FILTER_EVALUATION_INCOMPLETE"
    assert scan.issues[0].error_code == "FILTER_REGEX_BUDGET_EXCEEDED"
