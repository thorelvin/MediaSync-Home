from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointRootDescriptor,
    EndpointRootResolver,
)


class SqliteEndpointRootResolver(EndpointRootResolver):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        descriptor = self.resolve_endpoint_root_descriptor(
            resource_key=resource_key,
            endpoint_id=endpoint_id,
            endpoint_revision_id=endpoint_revision_id,
        )
        if descriptor is None:
            return None
        return descriptor.root

    def resolve_endpoint_root_descriptor(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> EndpointRootDescriptor | None:
        if resource_key != f"endpoint:{endpoint_id}":
            raise EndpointLeaseUnavailable(
                "ENDPOINT_LEASE_RESOURCE_MISMATCH",
                "Refresh the run target because its lease resource no longer matches the endpoint.",
            )
        row = self._connection.execute(
            """
            SELECT
                root_uri,
                generation,
                control_area_id,
                root_identity_hash_algorithm,
                root_identity_hash,
                owner_installation_id,
                ownership_epoch,
                control_marker_checksum_algorithm,
                control_marker_checksum
            FROM endpoint_revisions
            WHERE endpoint_id = ?
                AND id = ?
            """,
            (endpoint_id, endpoint_revision_id),
        ).fetchone()
        if row is None:
            return None
        return EndpointRootDescriptor(
            root=local_path_from_file_uri(str(row[0])),
            endpoint_generation=_required_positive_int(row[1]),
            control_area_id=_optional_str(row[2]),
            root_identity_hash_algorithm=_optional_str(row[3]),
            root_identity_hash=_optional_str(row[4]),
            owner_installation_id=_optional_str(row[5]),
            ownership_epoch=_optional_int(row[6]),
            marker_checksum_algorithm=_optional_str(row[7]),
            marker_checksum=_optional_str(row[8]),
        )


def local_path_from_file_uri(root_uri: str) -> Path:
    parsed = urlparse(root_uri)
    if parsed.scheme.lower() != "file":
        raise EndpointLeaseUnavailable(
            "ENDPOINT_ROOT_URI_UNSUPPORTED",
            "Use a local file endpoint root before acquiring a local mutation lease.",
        )
    if parsed.netloc not in {"", "localhost"}:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_ROOT_URI_NOT_LOCAL",
            "Use local endpoint roots for the 0B local executor preview.",
        )
    path_text = unquote(parsed.path)
    if os.name == "nt" and len(path_text) >= 3 and path_text[0] == "/" and path_text[2] == ":":
        path_text = path_text[1:]
    path = Path(path_text)
    if not path.is_absolute():
        raise EndpointLeaseUnavailable(
            "ENDPOINT_ROOT_URI_NOT_ABSOLUTE",
            "Refresh endpoint adoption so the endpoint root is stored as an absolute file URI.",
        )
    return path


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise EndpointLeaseUnavailable(
                "ENDPOINT_REVISION_IDENTITY_INVALID",
                "Refresh endpoint adoption because the stored endpoint revision identity is invalid.",
            ) from exc
    raise EndpointLeaseUnavailable(
        "ENDPOINT_REVISION_IDENTITY_INVALID",
        "Refresh endpoint adoption because the stored endpoint revision identity is invalid.",
    )


def _required_positive_int(value: object) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed < 1:
        raise EndpointLeaseUnavailable(
            "ENDPOINT_GENERATION_INVALID",
            "Refresh endpoint adoption because the stored endpoint generation is invalid.",
        )
    return parsed
