from __future__ import annotations

import sqlite3

from mediasync_home.application.directory_metadata import (
    DirectoryMetadataCatalogRecord,
    DirectoryMetadataCatalogStore,
)


class SqliteDirectoryMetadataCatalogStoreError(ValueError):
    pass


class SqliteDirectoryMetadataCatalogStore(DirectoryMetadataCatalogStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_directory_metadata(
        self,
        record: DirectoryMetadataCatalogRecord,
    ) -> DirectoryMetadataCatalogRecord:
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            existing = self.load_directory_metadata(record.recovery_id)
            if existing is not None:
                if existing != record:
                    raise SqliteDirectoryMetadataCatalogStoreError(
                        "DIRECTORY_METADATA_CATALOG_IDEMPOTENCY_CONFLICT"
                    )
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing
            self._connection.execute(
                """
                INSERT INTO directory_metadata_records (
                    recovery_id,
                    operation_id,
                    run_id,
                    run_target_id,
                    target_endpoint_id,
                    target_endpoint_revision_id,
                    final_relative_path,
                    desired_metadata_json,
                    applied_metadata_json,
                    metadata_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.recovery_id,
                    record.operation_id,
                    record.run_id,
                    record.run_target_id,
                    record.target_endpoint_id,
                    record.target_endpoint_revision_id,
                    record.final_relative_path,
                    record.desired_metadata_json,
                    record.applied_metadata_json,
                    record.metadata_hash,
                ),
            )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return record
        except SqliteDirectoryMetadataCatalogStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteDirectoryMetadataCatalogStoreError(
                "DIRECTORY_METADATA_CATALOG_CONFLICT"
            ) from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteDirectoryMetadataCatalogStoreError(
                "DIRECTORY_METADATA_CATALOG_PERSISTENCE_FAILED"
            ) from exc

    def load_directory_metadata(
        self,
        recovery_id: str,
    ) -> DirectoryMetadataCatalogRecord | None:
        row = self._connection.execute(
            """
            SELECT
                recovery_id,
                operation_id,
                run_id,
                run_target_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                final_relative_path,
                desired_metadata_json,
                applied_metadata_json,
                metadata_hash
            FROM directory_metadata_records
            WHERE recovery_id = ?
            """,
            (recovery_id,),
        ).fetchone()
        if row is None:
            return None
        return DirectoryMetadataCatalogRecord(
            recovery_id=str(row[0]),
            operation_id=str(row[1]),
            run_id=str(row[2]),
            run_target_id=str(row[3]),
            target_endpoint_id=str(row[4]),
            target_endpoint_revision_id=str(row[5]),
            final_relative_path=str(row[6]),
            desired_metadata_json=str(row[7]),
            applied_metadata_json=str(row[8]),
            metadata_hash=str(row[9]),
        )
