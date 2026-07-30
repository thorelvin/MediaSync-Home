from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mediasync_home.adapters.local_state_capacity import LocalStateCapacityProbe


@dataclass(frozen=True)
class _DiskUsage:
    free: int


def test_local_state_capacity_probe_sums_nested_regular_files(tmp_path: Path) -> None:
    nested = tmp_path / "backups" / "epoch-a"
    nested.mkdir(parents=True)
    (tmp_path / "catalog.sqlite").write_bytes(b"catalog")
    (nested / "recovery.sqlite").write_bytes(b"recovery")
    probe = LocalStateCapacityProbe(
        root=tmp_path,
        disk_usage_reader=lambda root: _DiskUsage(free=123_456),
    )

    observation = probe.measure()

    assert observation.measurement_complete is True
    assert observation.state_size_bytes == len(b"catalogrecovery")
    assert observation.local_free_space_bytes == 123_456
    assert observation.scanned_entry_count == 4


def test_local_state_capacity_probe_fails_closed_at_entry_bound(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"{index}.sqlite").write_bytes(b"x")
    probe = LocalStateCapacityProbe(
        root=tmp_path,
        max_entries=2,
        disk_usage_reader=lambda root: _DiskUsage(free=123_456),
    )

    observation = probe.measure()

    assert observation.measurement_complete is False
    assert observation.measurement_error_code == "STATE_CAPACITY_SCAN_LIMIT_EXCEEDED"
    assert observation.scanned_entry_count == 2


def test_local_state_capacity_probe_fails_closed_when_disk_usage_fails(
    tmp_path: Path,
) -> None:
    def fail_disk_usage(root: Path) -> _DiskUsage:
        raise OSError("unavailable")

    observation = LocalStateCapacityProbe(
        root=tmp_path,
        disk_usage_reader=fail_disk_usage,
    ).measure()

    assert observation.measurement_complete is False
    assert observation.measurement_error_code == "STATE_CAPACITY_DISK_USAGE_FAILED"
    assert observation.local_free_space_bytes == 0
