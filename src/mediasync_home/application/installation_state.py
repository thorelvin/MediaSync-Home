from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID


class InstallationStateViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InstallationState:
    installation_id: str
    product_channel: str
    created_utc: str
    last_started_app_version: str
    catalog_schema_version: int
    recovery_schema_version: int
    ipc_protocol_major: int
    row_version: int

    def __post_init__(self) -> None:
        validate_installation_id(self.installation_id)
        if not self.product_channel.strip():
            raise InstallationStateViolation("INSTALLATION_PRODUCT_CHANNEL_REQUIRED")
        if not self.last_started_app_version.strip():
            raise InstallationStateViolation("INSTALLATION_APP_VERSION_REQUIRED")
        _validate_utc_timestamp(self.created_utc)
        if self.catalog_schema_version < 1:
            raise InstallationStateViolation("INSTALLATION_CATALOG_SCHEMA_VERSION_INVALID")
        if self.recovery_schema_version < 1:
            raise InstallationStateViolation("INSTALLATION_RECOVERY_SCHEMA_VERSION_INVALID")
        if self.ipc_protocol_major < 1:
            raise InstallationStateViolation("INSTALLATION_IPC_PROTOCOL_MAJOR_INVALID")
        if self.row_version < 1:
            raise InstallationStateViolation("INSTALLATION_ROW_VERSION_INVALID")


class InstallationIdFactory(Protocol):
    def new_installation_id(self) -> str: ...


def validate_installation_id(value: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise InstallationStateViolation("INSTALLATION_ID_INVALID") from exc
    if str(parsed) != value:
        raise InstallationStateViolation("INSTALLATION_ID_NOT_CANONICAL")


def _validate_utc_timestamp(value: str) -> None:
    if not value.endswith("Z"):
        raise InstallationStateViolation("INSTALLATION_CREATED_UTC_INVALID")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise InstallationStateViolation("INSTALLATION_CREATED_UTC_INVALID") from exc
    if parsed.utcoffset() != timedelta(0):
        raise InstallationStateViolation("INSTALLATION_CREATED_UTC_INVALID")
