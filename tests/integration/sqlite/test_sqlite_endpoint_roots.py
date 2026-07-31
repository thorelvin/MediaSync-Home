from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mediasync_home.adapters.endpoint_leases import EndpointLeaseUnavailable, EndpointRootDescriptor
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
)
from mediasync_home.adapters.sqlite.endpoint_roots import SqliteEndpointRootResolver
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan


def test_sqlite_endpoint_root_resolver_returns_local_file_root(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    endpoint_root = tmp_path / "target root"
    endpoint_root.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_endpoint_revision(connection, root_uri=endpoint_root.as_uri())

        resolved = SqliteEndpointRootResolver(connection).resolve_endpoint_root(
            resource_key="endpoint:target-a",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
        )

        assert resolved == endpoint_root


def test_sqlite_endpoint_root_resolver_returns_endpoint_identity_descriptor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    endpoint_root = tmp_path / "target root"
    endpoint_root.mkdir()
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_endpoint_revision(
            connection,
            root_uri=endpoint_root.as_uri(),
            control_area_id="control-a",
            root_identity_hash_algorithm="BLAKE3-256",
            root_identity_hash="a" * 64,
            owner_installation_id="owner-a",
            ownership_epoch=3,
            control_marker_checksum_algorithm="BLAKE3-256",
            control_marker_checksum="b" * 64,
        )

        resolved = SqliteEndpointRootResolver(connection).resolve_endpoint_root_descriptor(
            resource_key="endpoint:target-a",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
        )

        assert resolved == EndpointRootDescriptor(
            root=endpoint_root,
            endpoint_generation=1,
            control_area_id="control-a",
            root_identity_hash_algorithm="BLAKE3-256",
            root_identity_hash="a" * 64,
            owner_installation_id="owner-a",
            ownership_epoch=3,
            marker_checksum_algorithm="BLAKE3-256",
            marker_checksum="b" * 64,
        )


def test_sqlite_endpoint_root_resolver_returns_none_for_missing_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)

        resolved = SqliteEndpointRootResolver(connection).resolve_endpoint_root(
            resource_key="endpoint:target-a",
            endpoint_id="target-a",
            endpoint_revision_id="target-rev-a",
        )

        assert resolved is None


def test_sqlite_endpoint_root_resolver_rejects_resource_mismatch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)

        with pytest.raises(EndpointLeaseUnavailable) as exc_info:
            SqliteEndpointRootResolver(connection).resolve_endpoint_root(
                resource_key="endpoint:other",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
            )

        assert exc_info.value.validation_code == "ENDPOINT_LEASE_RESOURCE_MISMATCH"


@pytest.mark.parametrize(
    ("root_uri", "validation_code"),
    [
        ("s3://bucket/backup", "ENDPOINT_ROOT_URI_UNSUPPORTED"),
        ("file://server/share/backup", "ENDPOINT_ROOT_URI_NOT_LOCAL"),
        ("file:relative/path", "ENDPOINT_ROOT_URI_NOT_ABSOLUTE"),
    ],
)
def test_sqlite_endpoint_root_resolver_rejects_unsupported_roots(
    tmp_path: Path,
    root_uri: str,
    validation_code: str,
) -> None:
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        _prepare_catalog(connection, database)
        _insert_endpoint_revision(connection, root_uri=root_uri)

        with pytest.raises(EndpointLeaseUnavailable) as exc_info:
            SqliteEndpointRootResolver(connection).resolve_endpoint_root(
                resource_key="endpoint:target-a",
                endpoint_id="target-a",
                endpoint_revision_id="target-rev-a",
            )

        assert exc_info.value.validation_code == validation_code


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _insert_endpoint_revision(
    connection: sqlite3.Connection,
    *,
    root_uri: str,
    control_area_id: str | None = None,
    root_identity_hash_algorithm: str | None = None,
    root_identity_hash: str | None = None,
    owner_installation_id: str | None = None,
    ownership_epoch: int | None = None,
    control_marker_checksum_algorithm: str | None = None,
    control_marker_checksum: str | None = None,
) -> None:
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (
            endpoint_id,
            id,
            display_name,
            root_uri,
            control_area_id,
            root_identity_hash_algorithm,
            root_identity_hash,
            owner_installation_id,
            ownership_epoch,
            control_marker_checksum_algorithm,
            control_marker_checksum
        )
            VALUES ('target-a', 'target-rev-a', 'USB', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            root_uri,
            control_area_id,
            root_identity_hash_algorithm,
            root_identity_hash,
            owner_installation_id,
            ownership_epoch,
            control_marker_checksum_algorithm,
            control_marker_checksum,
        ),
    )
    connection.commit()
