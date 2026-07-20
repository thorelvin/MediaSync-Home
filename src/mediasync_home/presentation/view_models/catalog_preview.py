from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


@dataclass(frozen=True)
class CatalogedFilePreviewRow:
    handoff_id: str
    display_line: str


@dataclass(frozen=True)
class CatalogedFilesPreviewState:
    title: str
    summary_label: str
    read_model_available: bool
    has_more_files: bool
    rows: tuple[CatalogedFilePreviewRow, ...]


def empty_cataloged_files_preview_state() -> CatalogedFilesPreviewState:
    return CatalogedFilesPreviewState(
        title="Cataloged files",
        summary_label="No cataloged files to show.",
        read_model_available=False,
        has_more_files=False,
        rows=(),
    )


def cataloged_files_preview_from_response(response: IpcResponse | None) -> CatalogedFilesPreviewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_cataloged_files_preview_state()
    page = response.payload.get("cataloged_files")
    if not isinstance(page, dict):
        return empty_cataloged_files_preview_state()

    read_model_available = bool(page.get("read_model_available", False))
    has_more = bool(page.get("has_more", False))
    if not read_model_available:
        return CatalogedFilesPreviewState(
            title="Cataloged files",
            summary_label="Catalog read model is not available.",
            read_model_available=False,
            has_more_files=False,
            rows=(),
        )

    files_payload = page.get("files")
    files = tuple(item for item in files_payload if isinstance(item, dict)) if isinstance(files_payload, list) else ()
    rows = tuple(_preview_row(file) for file in files)
    return CatalogedFilesPreviewState(
        title="Cataloged files",
        summary_label=_summary_label(row_count=len(rows), has_more=has_more),
        read_model_available=True,
        has_more_files=has_more,
        rows=rows,
    )


def _preview_row(payload: dict[object, object]) -> CatalogedFilePreviewRow:
    handoff_id = _required_text(payload.get("handoff_id")) or "catalog-handoff"
    path = _required_text(payload.get("final_relative_path")) or "file"
    target = _required_text(payload.get("target_endpoint_id")) or "target"
    content_hash = _required_text(payload.get("content_hash"))
    hash_label = f"sha {content_hash[:8]}" if content_hash else "sha unknown"
    return CatalogedFilePreviewRow(
        handoff_id=handoff_id,
        display_line=f"{path} · {target} · {hash_label}",
    )


def _summary_label(*, row_count: int, has_more: bool) -> str:
    if row_count < 1:
        return "No cataloged files to show."
    more = " More cataloged files exist." if has_more else ""
    return f"{row_count} cataloged file{'s' if row_count != 1 else ''}.{more}"


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
