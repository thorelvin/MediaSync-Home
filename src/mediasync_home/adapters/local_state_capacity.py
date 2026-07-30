from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediasync_home.application.state_capacity import StateCapacityObservation


DEFAULT_MAX_STATE_SCAN_ENTRIES = 100_000


class DiskUsage(Protocol):
    @property
    def free(self) -> int: ...


@dataclass(frozen=True, slots=True)
class LocalStateCapacityProbe:
    root: Path
    max_entries: int = DEFAULT_MAX_STATE_SCAN_ENTRIES
    disk_usage_reader: Callable[[Path], DiskUsage] = shutil.disk_usage

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ValueError("STATE_CAPACITY_ROOT_MUST_BE_ABSOLUTE")
        if self.max_entries < 1:
            raise ValueError("STATE_CAPACITY_SCAN_LIMIT_MUST_BE_POSITIVE")

    def measure(self) -> StateCapacityObservation:
        state_size = 0
        scanned_entries = 0
        pending = [self.root]
        error_code: str | None = None

        while pending and error_code is None:
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        scanned_entries += 1
                        if scanned_entries > self.max_entries:
                            error_code = "STATE_CAPACITY_SCAN_LIMIT_EXCEEDED"
                            break
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        if entry.is_file(follow_symlinks=False):
                            state_size += entry.stat(follow_symlinks=False).st_size
                            continue
                        error_code = "STATE_CAPACITY_UNSUPPORTED_ENTRY"
                        break
            except OSError:
                error_code = "STATE_CAPACITY_STATE_SCAN_FAILED"

        free_space = 0
        try:
            free_space = int(self.disk_usage_reader(self.root).free)
        except (OSError, ValueError):
            error_code = error_code or "STATE_CAPACITY_DISK_USAGE_FAILED"

        return StateCapacityObservation(
            state_size_bytes=state_size,
            local_free_space_bytes=max(0, free_space),
            measurement_complete=error_code is None,
            scanned_entry_count=min(scanned_entries, self.max_entries),
            measurement_error_code=error_code,
        )
