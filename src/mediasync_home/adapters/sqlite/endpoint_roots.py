from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootResolver


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
        if resource_key != f"endpoint:{endpoint_id}":
            raise EndpointLeaseUnavailable(
                "ENDPOINT_LEASE_RESOURCE_MISMATCH",
                "Refresh the run target because its lease resource no longer matches the endpoint.",
            )
        row = self._connection.execute(
            """
            SELECT root_uri
            FROM endpoint_revisions
            WHERE endpoint_id = ?
                AND id = ?
            """,
            (endpoint_id, endpoint_revision_id),
        ).fetchone()
        if row is None:
            return None
        return _local_path_from_file_uri(str(row[0]))


def _local_path_from_file_uri(root_uri: str) -> Path:
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
