from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mediasync_home.ipc.protocol import IpcResponse, IpcStatus


class DirectoryRelationshipKind(str, Enum):
    SAME_ROOT_ALIAS = "SAME_ROOT_ALIAS"
    ROOT_OVERLAP = "ROOT_OVERLAP"
    SAME_PHYSICAL_DEVICE = "SAME_PHYSICAL_DEVICE"
    SAME_LOGICAL_STORAGE = "SAME_LOGICAL_STORAGE"


@dataclass(frozen=True)
class SelectedDirectoryIdentityItemViewState:
    ordinal: int
    independent_device_id: str | None
    ready: bool


@dataclass(frozen=True)
class SelectedDirectoryRelationshipViewState:
    left_ordinal: int
    right_ordinal: int
    kind: DirectoryRelationshipKind
    blocking: bool


@dataclass(frozen=True)
class SelectedDirectoryIdentityViewState:
    items: tuple[SelectedDirectoryIdentityItemViewState, ...]
    relationships: tuple[SelectedDirectoryRelationshipViewState, ...]
    read_model_available: bool

    @classmethod
    def unavailable(cls) -> "SelectedDirectoryIdentityViewState":
        return cls(items=(), relationships=(), read_model_available=False)

    @property
    def blocking(self) -> bool:
        return any(relationship.blocking for relationship in self.relationships)

    @property
    def same_physical_device(self) -> bool:
        return any(
            relationship.kind is DirectoryRelationshipKind.SAME_PHYSICAL_DEVICE
            for relationship in self.relationships
        )

    @property
    def same_logical_storage(self) -> bool:
        return any(
            relationship.kind is DirectoryRelationshipKind.SAME_LOGICAL_STORAGE
            for relationship in self.relationships
        )

    @property
    def confirmed_target_device_count(self) -> int:
        return len(
            {
                item.independent_device_id
                for item in self.items
                if item.ordinal > 0 and item.independent_device_id is not None
            }
        )

    @property
    def unknown_target_count(self) -> int:
        return sum(
            item.ordinal > 0 and item.independent_device_id is None
            for item in self.items
        )


def selected_directory_identity_from_response(
    response: IpcResponse | None,
    *,
    expected_count: int,
) -> SelectedDirectoryIdentityViewState:
    if (
        response is None
        or response.status is IpcStatus.REJECTED
        or not 1 <= expected_count <= 4
    ):
        return SelectedDirectoryIdentityViewState.unavailable()
    payload = response.payload.get("selected_directory_identities")
    if not isinstance(payload, dict):
        return SelectedDirectoryIdentityViewState.unavailable()
    raw_items = payload.get("items")
    raw_relationships = payload.get("relationships")
    if not isinstance(raw_items, list) or not isinstance(raw_relationships, list):
        return SelectedDirectoryIdentityViewState.unavailable()

    items: list[SelectedDirectoryIdentityItemViewState] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            return SelectedDirectoryIdentityViewState.unavailable()
        ordinal = raw_item.get("ordinal")
        status = raw_item.get("status")
        independent_device_id = raw_item.get("independent_device_id")
        if (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not 0 <= ordinal < expected_count
            or status not in {"READY", "UNAVAILABLE"}
            or (
                independent_device_id is not None
                and (
                    not isinstance(independent_device_id, str)
                    or len(independent_device_id) != 64
                )
            )
        ):
            return SelectedDirectoryIdentityViewState.unavailable()
        items.append(
            SelectedDirectoryIdentityItemViewState(
                ordinal=ordinal,
                independent_device_id=independent_device_id,
                ready=status == "READY",
            )
        )
    if sorted(item.ordinal for item in items) != list(range(expected_count)):
        return SelectedDirectoryIdentityViewState.unavailable()

    relationships: list[SelectedDirectoryRelationshipViewState] = []
    for raw_relationship in raw_relationships:
        if not isinstance(raw_relationship, dict):
            return SelectedDirectoryIdentityViewState.unavailable()
        left = raw_relationship.get("left_ordinal")
        right = raw_relationship.get("right_ordinal")
        blocking = raw_relationship.get("blocking")
        try:
            kind = DirectoryRelationshipKind(str(raw_relationship.get("kind")))
        except ValueError:
            return SelectedDirectoryIdentityViewState.unavailable()
        if (
            isinstance(left, bool)
            or not isinstance(left, int)
            or isinstance(right, bool)
            or not isinstance(right, int)
            or not 0 <= left < right < expected_count
            or not isinstance(blocking, bool)
            or blocking
            != (
                kind
                in {
                    DirectoryRelationshipKind.SAME_ROOT_ALIAS,
                    DirectoryRelationshipKind.ROOT_OVERLAP,
                }
            )
        ):
            return SelectedDirectoryIdentityViewState.unavailable()
        relationships.append(
            SelectedDirectoryRelationshipViewState(
                left_ordinal=left,
                right_ordinal=right,
                kind=kind,
                blocking=blocking,
            )
        )
    return SelectedDirectoryIdentityViewState(
        items=tuple(sorted(items, key=lambda item: item.ordinal)),
        relationships=tuple(relationships),
        read_model_available=True,
    )
