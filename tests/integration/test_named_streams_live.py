from __future__ import annotations

import os
from pathlib import Path

import pytest

from mediasync_home.adapters.local_snapshot_scanner import (
    LocalFilesystemSnapshotScanner,
)
from mediasync_home.adapters.file_object_fingerprints import (
    LocalFileObjectFingerprintAdapter,
)
from mediasync_home.adapters.named_streams import Win32NamedStreamProbe
from mediasync_home.application.named_streams import NamedStreamState
from mediasync_home.application.snapshot_scanning import DirectoryCaseContext


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named streams only")


class _FixedCaseModeProbe:
    def inspect_directory_case_context(self, path: Path) -> DirectoryCaseContext:
        del path
        return DirectoryCaseContext(
            case_mode="CASE_INSENSITIVE",
            evidence="FIXED_TEST_CASE_MODE_V1",
        )


def test_win32_named_stream_probe_and_scanner_block_live_ads(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    file_path = root / "document.txt"
    file_path.write_text("primary", encoding="utf-8")
    stream_path = Path(f"{file_path}:mediasync-test")
    try:
        stream_path.write_text("named", encoding="utf-8")
    except OSError:
        pytest.skip("the temporary filesystem does not support named streams")

    probe = Win32NamedStreamProbe()
    inspection = probe.inspect_named_streams(file_path)
    assert inspection.state is NamedStreamState.PRESENT
    assert [(record.stream_name, record.size_bytes) for record in inspection.named_streams] == [
        (":mediasync-test:$DATA", 5)
    ]

    scan = LocalFilesystemSnapshotScanner(
        case_mode_probe=_FixedCaseModeProbe(),
        named_stream_probe=probe,
    ).scan(
        root,
        snapshot_id="snapshot-live-named-stream",
        exclude_control_area=False,
    )

    assert scan.complete is True
    assert len(scan.issues) == 1
    assert scan.issues[0].relative_path == "document.txt"
    assert scan.issues[0].error_code == "SNAPSHOT_NAMED_STREAM_PRESENT"
    assert scan.issues[0].blocks_destructive_actions is False

    stream_path.unlink()
    assert probe.inspect_named_streams(file_path).state is NamedStreamState.NONE


def test_file_object_fingerprint_copies_hashes_and_renames_live_ads(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "staging.payload"
    final = tmp_path / "final.txt"
    source.write_bytes(b"primary-content")
    stream_path = Path(f"{source}:metadata")
    try:
        stream_path.write_bytes(b"named-content")
    except OSError:
        pytest.skip("the temporary filesystem does not support named streams")

    adapter = LocalFileObjectFingerprintAdapter()
    expected = adapter.fingerprint(source)
    adapter.copy_file_object(
        source=source,
        destination=destination,
        expected_fingerprint=expected,
    )

    assert adapter.fingerprint(staging := destination) == expected
    assert Path(f"{staging}:metadata").read_bytes() == b"named-content"
    staging.replace(final)
    assert adapter.fingerprint(final) == expected
    assert Path(f"{final}:metadata").read_bytes() == b"named-content"
