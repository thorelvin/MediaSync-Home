from __future__ import annotations

import re
from dataclasses import dataclass

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCAN_STATES = {"QUEUED", "RUNNING", "PAUSED", "COMPLETED", "FAILED"}
_SCAN_STAGES = {"QUICK_SIGNATURE", "FULL_HASH", "MATERIALIZE", "DONE"}
_RELATIONSHIP_CLASSES = {
    "EXPECTED_REPLICA",
    "INTRA_ENDPOINT_DUPLICATE",
    "UNRELATED_CROSS_ENDPOINT_DUPLICATE",
    "SAME_FILE_MULTIPLE_PATHS",
    "POTENTIAL_DUPLICATE",
}
_MAX_GROUP_ROWS = 200
_MAX_MEMBER_ROWS = 200


@dataclass(frozen=True)
class DuplicateScanViewState:
    analysis_id: str | None
    available: bool
    scan_id: str | None = None
    state: str | None = None
    stage: str | None = None
    candidate_file_count: int = 0
    quick_completed_count: int = 0
    full_hash_candidate_count: int = 0
    full_hash_completed_count: int = 0
    issue_count: int = 0
    reason_code: str | None = None

    @property
    def found(self) -> bool:
        return self.scan_id is not None

    @property
    def active(self) -> bool:
        return self.state in {"QUEUED", "RUNNING"}

    @property
    def progress_numerator(self) -> int:
        return self.quick_completed_count + self.full_hash_completed_count

    @property
    def progress_denominator(self) -> int:
        return self.candidate_file_count + self.full_hash_candidate_count


@dataclass(frozen=True)
class DuplicateGroupViewState:
    group_id: str
    relationship_class: str
    full_hash: str
    size_bytes: int
    member_count: int
    physical_object_count: int
    expected_replica_count: int
    potential_savings_bytes: int
    review_state: str


@dataclass(frozen=True)
class DuplicateGroupPageViewState:
    analysis_id: str | None
    read_model_available: bool
    groups: tuple[DuplicateGroupViewState, ...] = ()
    has_more: bool = False
    next_cursor: dict[str, object] | None = None


@dataclass(frozen=True)
class DuplicateMemberViewState:
    group_id: str
    snapshot_id: str
    endpoint_id: str
    file_entry_id: str
    relative_path: str
    member_role: str


@dataclass(frozen=True)
class DuplicateMemberPageViewState:
    group_id: str | None
    read_model_available: bool
    members: tuple[DuplicateMemberViewState, ...] = ()
    has_more: bool = False
    next_cursor: dict[str, object] | None = None


def empty_duplicate_scan_state(
    analysis_id: str | None = None,
) -> DuplicateScanViewState:
    return DuplicateScanViewState(analysis_id=analysis_id, available=False)


def empty_duplicate_group_page_state(
    analysis_id: str | None = None,
) -> DuplicateGroupPageViewState:
    return DuplicateGroupPageViewState(
        analysis_id=analysis_id,
        read_model_available=False,
    )


def empty_duplicate_member_page_state(
    group_id: str | None = None,
) -> DuplicateMemberPageViewState:
    return DuplicateMemberPageViewState(
        group_id=group_id,
        read_model_available=False,
    )


def duplicate_scan_from_response(
    response: IpcResponse | None,
) -> DuplicateScanViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_duplicate_scan_state()
    payload = response.payload.get("duplicate_scan")
    if not isinstance(payload, dict):
        return empty_duplicate_scan_state()
    analysis_id = _required_text(payload.get("analysis_id"))
    available = payload.get("available") is True
    scan = payload.get("scan")
    if not available or not isinstance(scan, dict):
        return DuplicateScanViewState(
            analysis_id=analysis_id,
            available=available,
        )
    return _scan_from_payload(scan, analysis_id=analysis_id, available=True)


def duplicate_group_page_from_response(
    response: IpcResponse | None,
) -> DuplicateGroupPageViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_duplicate_group_page_state()
    payload = response.payload.get("duplicate_groups")
    if not isinstance(payload, dict):
        return empty_duplicate_group_page_state()
    analysis_id = _required_text(payload.get("analysis_id"))
    values = payload.get("groups")
    if not isinstance(values, list) or len(values) > _MAX_GROUP_ROWS:
        return empty_duplicate_group_page_state(analysis_id)
    groups = tuple(
        group
        for item in values
        if isinstance(item, dict) and (group := _group_from_payload(item)) is not None
    )
    cursor = _group_cursor(payload.get("next_cursor"))
    has_more = payload.get("has_more") is True and cursor is not None
    return DuplicateGroupPageViewState(
        analysis_id=analysis_id,
        read_model_available=True,
        groups=groups,
        has_more=has_more,
        next_cursor=cursor,
    )


def duplicate_member_page_from_response(
    response: IpcResponse | None,
) -> DuplicateMemberPageViewState:
    if response is None or response.status is IpcStatus.REJECTED:
        return empty_duplicate_member_page_state()
    payload = response.payload.get("duplicate_members")
    if not isinstance(payload, dict):
        return empty_duplicate_member_page_state()
    group_id = _required_text(payload.get("group_id"))
    values = payload.get("members")
    if (
        group_id is None
        or not isinstance(values, list)
        or len(values) > _MAX_MEMBER_ROWS
    ):
        return empty_duplicate_member_page_state(group_id)
    members = tuple(
        member
        for item in values
        if isinstance(item, dict)
        and (member := _member_from_payload(item, group_id=group_id)) is not None
    )
    cursor = _member_cursor(payload.get("next_cursor"))
    has_more = payload.get("has_more") is True and cursor is not None
    return DuplicateMemberPageViewState(
        group_id=group_id,
        read_model_available=True,
        members=members,
        has_more=has_more,
        next_cursor=cursor,
    )


def _scan_from_payload(
    payload: dict[object, object],
    *,
    analysis_id: str | None,
    available: bool,
) -> DuplicateScanViewState:
    scan_id = _required_text(payload.get("scan_id"))
    payload_analysis_id = _required_text(payload.get("analysis_id"))
    state = _required_text(payload.get("state"))
    stage = _required_text(payload.get("stage"))
    counts = tuple(
        _non_negative_int(payload.get(key))
        for key in (
            "candidate_file_count",
            "quick_completed_count",
            "full_hash_candidate_count",
            "full_hash_completed_count",
            "issue_count",
        )
    )
    if (
        scan_id is None
        or payload_analysis_id is None
        or (analysis_id is not None and payload_analysis_id != analysis_id)
        or state not in _SCAN_STATES
        or stage not in _SCAN_STAGES
        or any(value is None for value in counts)
    ):
        return DuplicateScanViewState(analysis_id=analysis_id, available=available)
    candidate_count, quick_count, full_count, full_complete, issue_count = counts
    assert candidate_count is not None
    assert quick_count is not None
    assert full_count is not None
    assert full_complete is not None
    assert issue_count is not None
    if (
        quick_count > candidate_count
        or full_count > candidate_count
        or full_complete > full_count
    ):
        return DuplicateScanViewState(analysis_id=analysis_id, available=available)
    return DuplicateScanViewState(
        analysis_id=payload_analysis_id,
        available=available,
        scan_id=scan_id,
        state=state,
        stage=stage,
        candidate_file_count=candidate_count,
        quick_completed_count=quick_count,
        full_hash_candidate_count=full_count,
        full_hash_completed_count=full_complete,
        issue_count=issue_count,
        reason_code=_optional_text(payload.get("reason_code")),
    )


def _group_from_payload(
    payload: dict[object, object],
) -> DuplicateGroupViewState | None:
    group_id = _required_text(payload.get("group_id"))
    relationship_class = _required_text(payload.get("relationship_class"))
    full_hash = _required_text(payload.get("full_hash"))
    review_state = _required_text(payload.get("review_state"))
    counts = tuple(
        _non_negative_int(payload.get(key))
        for key in (
            "size_bytes",
            "member_count",
            "physical_object_count",
            "expected_replica_count",
            "potential_savings_bytes",
        )
    )
    if (
        group_id is None
        or relationship_class not in _RELATIONSHIP_CLASSES
        or full_hash is None
        or _HASH_PATTERN.fullmatch(full_hash) is None
        or review_state is None
        or any(value is None for value in counts)
    ):
        return None
    size, members, physical, replicas, savings = counts
    assert size is not None
    assert members is not None
    assert physical is not None
    assert replicas is not None
    assert savings is not None
    return DuplicateGroupViewState(
        group_id=group_id,
        relationship_class=relationship_class,
        full_hash=full_hash,
        size_bytes=size,
        member_count=members,
        physical_object_count=physical,
        expected_replica_count=replicas,
        potential_savings_bytes=savings,
        review_state=review_state,
    )


def _member_from_payload(
    payload: dict[object, object],
    *,
    group_id: str,
) -> DuplicateMemberViewState | None:
    payload_group_id = _required_text(payload.get("group_id"))
    snapshot_id = _required_text(payload.get("snapshot_id"))
    endpoint_id = _required_text(payload.get("endpoint_id"))
    file_entry_id = _required_text(payload.get("file_entry_id"))
    relative_path = _required_text(payload.get("relative_path"))
    member_role = _required_text(payload.get("member_role"))
    if (
        payload_group_id != group_id
        or snapshot_id is None
        or endpoint_id is None
        or file_entry_id is None
        or relative_path is None
        or member_role is None
    ):
        return None
    return DuplicateMemberViewState(
        group_id=group_id,
        snapshot_id=snapshot_id,
        endpoint_id=endpoint_id,
        file_entry_id=file_entry_id,
        relative_path=relative_path,
        member_role=member_role,
    )


def _group_cursor(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != {
        "relationship_class",
        "full_hash",
        "group_id",
    }:
        return None
    relationship_class = _required_text(value.get("relationship_class"))
    full_hash = _required_text(value.get("full_hash"))
    group_id = _required_text(value.get("group_id"))
    if (
        relationship_class not in _RELATIONSHIP_CLASSES
        or full_hash is None
        or _HASH_PATTERN.fullmatch(full_hash) is None
        or group_id is None
    ):
        return None
    return {
        "relationship_class": relationship_class,
        "full_hash": full_hash,
        "group_id": group_id,
    }


def _member_cursor(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != {
        "relative_path",
        "snapshot_id",
        "file_entry_id",
    }:
        return None
    relative_path = _required_text(value.get("relative_path"))
    snapshot_id = _required_text(value.get("snapshot_id"))
    file_entry_id = _required_text(value.get("file_entry_id"))
    if relative_path is None or snapshot_id is None or file_entry_id is None:
        return None
    return {
        "relative_path": relative_path,
        "snapshot_id": snapshot_id,
        "file_entry_id": file_entry_id,
    }


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_text(value: object) -> str | None:
    return _required_text(value)
