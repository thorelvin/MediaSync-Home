from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


STATE_MAINTENANCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class StateMaintenanceCommandName(str, Enum):
    RESTORE_STATE_FROM_BACKUP_SET = "RESTORE_STATE_FROM_BACKUP_SET"


class StateMaintenancePayloadError(ValueError):
    pass


@dataclass(frozen=True)
class RestoreStateFromBackupSetCommand:
    request_id: str
    idempotency_key: str
    backup_dir: Path
    restore_epoch_id: str
    started_utc: str


class StateRestoreCommandExecutor(Protocol):
    def __call__(self, command: RestoreStateFromBackupSetCommand) -> dict[str, object]: ...


def parse_restore_state_from_backup_set_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> RestoreStateFromBackupSetCommand:
    backup_dir = _local_absolute_path(payload.get("backup_dir"), "backup_dir")
    restore_epoch_id = _required_epoch_id(
        payload.get("restore_epoch_id"),
        "restore_epoch_id",
    )
    started_utc = _required_text(payload.get("started_utc"), "started_utc")
    return RestoreStateFromBackupSetCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        backup_dir=backup_dir,
        restore_epoch_id=restore_epoch_id,
        started_utc=started_utc,
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateMaintenancePayloadError(
            f"RESTORE_STATE_REQUIRES_{field_name.upper()}"
        )
    return value


def _required_epoch_id(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if STATE_MAINTENANCE_ID_PATTERN.fullmatch(text) is None:
        raise StateMaintenancePayloadError(
            f"RESTORE_STATE_INVALID_{field_name.upper()}"
        )
    return text


def _local_absolute_path(value: object, field_name: str) -> Path:
    text = _required_text(value, field_name)
    path = Path(text)
    if not path.is_absolute():
        raise StateMaintenancePayloadError(
            f"RESTORE_STATE_{field_name.upper()}_MUST_BE_ABSOLUTE"
        )
    if str(path).startswith("\\\\"):
        raise StateMaintenancePayloadError(
            f"RESTORE_STATE_{field_name.upper()}_MUST_BE_LOCAL"
        )
    return path
