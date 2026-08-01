from __future__ import annotations

import sqlite3

from mediasync_home.application.catalog_read_models import CatalogedFileReadModel
from mediasync_home.application.catalog_handoff import (
    FinalFileCatalogHandoff,
    FinalFileCatalogHandoffStore,
    RetainedVersionCatalogHandoff,
    validate_final_file_catalog_handoff,
)


class SqliteCatalogHandoffStoreError(ValueError):
    pass


class SqliteFinalFileCatalogHandoffStore(FinalFileCatalogHandoffStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record_final_file_handoff(
        self,
        handoff: FinalFileCatalogHandoff,
    ) -> FinalFileCatalogHandoff:
        validate_final_file_catalog_handoff(handoff)
        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")

            existing = self.load_final_file_handoff(handoff.handoff_id)
            if existing is not None:
                if existing != handoff:
                    raise SqliteCatalogHandoffStoreError("CATALOG_HANDOFF_IDEMPOTENCY_CONFLICT")
                if not outer_transaction:
                    self._connection.execute("COMMIT")
                return existing

            self._connection.execute(
                """
                INSERT INTO final_file_catalog_handoffs (
                    handoff_id,
                    run_id,
                    run_target_id,
                    operation_id,
                    target_endpoint_id,
                    target_endpoint_revision_id,
                    final_relative_path,
                    content_hash,
                    lease_id,
                    fencing_token,
                    effect_kind
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff.handoff_id,
                    handoff.run_id,
                    handoff.run_target_id,
                    handoff.operation_id,
                    handoff.target_endpoint_id,
                    handoff.target_endpoint_revision_id,
                    handoff.final_relative_path,
                    handoff.content_hash,
                    handoff.lease_id,
                    handoff.fencing_token,
                    handoff.effect_kind,
                ),
            )
            if handoff.retained_version is not None:
                self._record_retained_version(handoff)
            recorded = self.load_final_file_handoff(handoff.handoff_id)
            if recorded is None:
                raise SqliteCatalogHandoffStoreError("CATALOG_HANDOFF_LOAD_FAILED")
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return recorded
        except SqliteCatalogHandoffStoreError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteCatalogHandoffStoreError("CATALOG_HANDOFF_PERSISTENCE_CONFLICT") from exc
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteCatalogHandoffStoreError("CATALOG_HANDOFF_PERSISTENCE_FAILED") from exc

    def load_final_file_handoff(self, handoff_id: str) -> FinalFileCatalogHandoff | None:
        row = self._connection.execute(
            """
            SELECT
                handoff_id,
                run_id,
                run_target_id,
                operation_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                final_relative_path,
                content_hash,
                lease_id,
                fencing_token,
                effect_kind
            FROM final_file_catalog_handoffs
            WHERE handoff_id = ?
            """,
            (handoff_id,),
        ).fetchone()
        if row is None:
            return None
        retained_version = self._load_retained_version(handoff_id)
        return FinalFileCatalogHandoff(
            handoff_id=str(row[0]),
            run_id=str(row[1]),
            run_target_id=str(row[2]),
            operation_id=str(row[3]),
            target_endpoint_id=str(row[4]),
            target_endpoint_revision_id=str(row[5]),
            final_relative_path=str(row[6]),
            content_hash=str(row[7]),
            lease_id=str(row[8]),
            fencing_token=int(row[9]),
            effect_kind=str(row[10]),
            retained_version=retained_version,
        )

    def _record_retained_version(self, handoff: FinalFileCatalogHandoff) -> None:
        retained = handoff.retained_version
        if retained is None:
            return
        self._connection.execute(
            """
            INSERT INTO retained_version_objects (
                version_object_id,
                handoff_id,
                run_id,
                run_target_id,
                operation_id,
                job_id,
                job_revision_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                final_relative_path,
                original_fingerprint_json,
                created_utc,
                retention_policy,
                retention_until_utc,
                manifest_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                retained.version_object_id,
                handoff.handoff_id,
                handoff.run_id,
                handoff.run_target_id,
                handoff.operation_id,
                retained.job_id,
                retained.job_revision_id,
                handoff.target_endpoint_id,
                handoff.target_endpoint_revision_id,
                retained.endpoint_generation,
                retained.owner_installation_id,
                retained.ownership_epoch,
                handoff.final_relative_path,
                retained.original_fingerprint_json,
                retained.created_utc,
                retained.retention_policy,
                retained.retention_until_utc,
                retained.manifest_hash,
            ),
        )

    def _load_retained_version(
        self,
        handoff_id: str,
    ) -> RetainedVersionCatalogHandoff | None:
        row = self._connection.execute(
            """
            SELECT
                version_object_id,
                job_id,
                job_revision_id,
                endpoint_generation,
                owner_installation_id,
                ownership_epoch,
                original_fingerprint_json,
                created_utc,
                retention_policy,
                retention_until_utc,
                manifest_hash
            FROM retained_version_objects
            WHERE handoff_id = ?
            """,
            (handoff_id,),
        ).fetchone()
        if row is None:
            return None
        return RetainedVersionCatalogHandoff(
            version_object_id=str(row[0]),
            job_id=str(row[1]),
            job_revision_id=str(row[2]),
            endpoint_generation=int(row[3]),
            owner_installation_id=str(row[4]),
            ownership_epoch=int(row[5]),
            original_fingerprint_json=str(row[6]),
            created_utc=str(row[7]),
            retention_policy=str(row[8]),
            retention_until_utc=str(row[9]),
            manifest_hash=str(row[10]),
        )

    def list_recent_cataloged_files(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
    ) -> tuple[CatalogedFileReadModel, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")

        filters: list[str] = ["effect_kind = 'COPY_NEW_FINAL_FILE'"]
        parameters: list[object] = []
        if run_id is not None:
            filters.append("run_id = ?")
            parameters.append(run_id)
        if target_endpoint_id is not None:
            filters.append("target_endpoint_id = ?")
            parameters.append(target_endpoint_id)
        where_clause = f"WHERE {' AND '.join(filters)}"
        rows = self._connection.execute(
            f"""
            SELECT
                handoff_id,
                run_id,
                run_target_id,
                operation_id,
                target_endpoint_id,
                target_endpoint_revision_id,
                final_relative_path,
                content_hash,
                lease_id,
                fencing_token,
                effect_kind,
                recorded_utc
            FROM final_file_catalog_handoffs
            {where_clause}
            ORDER BY recorded_utc DESC, handoff_id DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        ).fetchall()
        return tuple(_cataloged_file_from_row(row) for row in rows)


def _cataloged_file_from_row(row: sqlite3.Row | tuple[object, ...]) -> CatalogedFileReadModel:
    return CatalogedFileReadModel(
        handoff_id=str(row[0]),
        run_id=str(row[1]),
        run_target_id=str(row[2]),
        operation_id=str(row[3]),
        target_endpoint_id=str(row[4]),
        target_endpoint_revision_id=str(row[5]),
        final_relative_path=str(row[6]),
        content_hash=str(row[7]),
        lease_id=str(row[8]),
        fencing_token=_fencing_token_from_column(row[9]),
        effect_kind=str(row[10]),
        recorded_utc=str(row[11]),
    )


def _fencing_token_from_column(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("fencing_token must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("fencing_token must be an integer")
