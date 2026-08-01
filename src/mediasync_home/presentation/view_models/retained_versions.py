from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True, slots=True)
class RetainedVersionViewState:
    version_object_id: str
    run_id: str
    operation_id: str
    target_endpoint_id: str
    final_relative_path: str
    created_utc: str
    retention_until_utc: str
    state: str
    row_version: int
    restorable: bool
    protected_for_restore: bool


@dataclass(frozen=True, slots=True)
class RetainedVersionPageViewState:
    run_id: str | None
    read_model_available: bool
    has_more: bool
    versions: tuple[RetainedVersionViewState, ...]


def empty_retained_version_page_state() -> RetainedVersionPageViewState:
    return RetainedVersionPageViewState(
        run_id=None,
        read_model_available=False,
        has_more=False,
        versions=(),
    )


def retained_version_page_from_response(
    response: IpcResponse,
) -> RetainedVersionPageViewState:
    if response.status is not IpcStatus.ACCEPTED:
        return empty_retained_version_page_state()
    payload = response.payload.get("retained_versions")
    if not isinstance(payload, dict):
        return empty_retained_version_page_state()
    run_id = _optional_text(payload.get("run_id"))
    read_model_available = payload.get("read_model_available") is True
    raw_versions = payload.get("versions")
    if run_id is None or not isinstance(raw_versions, list):
        return empty_retained_version_page_state()
    versions = tuple(
        version
        for item in raw_versions
        if (version := _version_from_payload(item)) is not None
    )
    return RetainedVersionPageViewState(
        run_id=run_id,
        read_model_available=read_model_available,
        has_more=payload.get("has_more") is True,
        versions=versions,
    )


def _version_from_payload(value: object) -> RetainedVersionViewState | None:
    if not isinstance(value, dict):
        return None
    texts = {
        key: _optional_text(value.get(key))
        for key in (
            "version_object_id",
            "run_id",
            "operation_id",
            "target_endpoint_id",
            "final_relative_path",
            "created_utc",
            "retention_until_utc",
            "state",
        )
    }
    row_version = value.get("row_version")
    if (
        any(item is None for item in texts.values())
        or isinstance(row_version, bool)
        or not isinstance(row_version, int)
        or row_version < 1
    ):
        return None
    return RetainedVersionViewState(
        version_object_id=texts["version_object_id"] or "",
        run_id=texts["run_id"] or "",
        operation_id=texts["operation_id"] or "",
        target_endpoint_id=texts["target_endpoint_id"] or "",
        final_relative_path=texts["final_relative_path"] or "",
        created_utc=texts["created_utc"] or "",
        retention_until_utc=texts["retention_until_utc"] or "",
        state=texts["state"] or "",
        row_version=row_version,
        restorable=value.get("restorable") is True,
        protected_for_restore=value.get("protected_for_restore") is True,
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
