from __future__ import annotations

import ntpath
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import quote

from mediasync_home.application.job_creation import SealedStandardBackupJob
from mediasync_home.application.job_endpoints import (
    ENDPOINT_CLASSIFICATION_PENDING,
    EndpointIdFactory,
    EndpointRegistrationState,
    JobEndpointRole,
    StandardBackupJobEndpointBinding,
    StandardBackupJobEndpointRegistrar,
    StandardBackupJobEndpointSet,
)


_WINDOWS_ABSOLUTE_ROOT = re.compile(r"^[A-Za-z]:[\\/]")


class SqliteJobEndpointRegistrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ConfiguredRoot:
    display_name: str
    root_uri: str
    canonical_root_key: str
    role: JobEndpointRole
    ordinal: int


class SqliteStandardBackupJobEndpointRegistrar(StandardBackupJobEndpointRegistrar):
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        id_factory: EndpointIdFactory,
    ) -> None:
        self._connection = connection
        self._id_factory = id_factory

    def register_standard_backup_job_endpoints(
        self,
        job: SealedStandardBackupJob,
    ) -> StandardBackupJobEndpointSet:
        configured_roots = _configured_roots(job)
        existing = self.load_standard_backup_job_endpoint_set(
            job_id=job.job_id,
            job_revision_id=job.job_revision_id,
        )
        if existing is not None:
            _validate_existing_bindings(existing, configured_roots)
            return existing

        outer_transaction = self._connection.in_transaction
        try:
            if not outer_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            for configured in configured_roots:
                endpoint_id, endpoint_revision_id = self._find_or_create_endpoint(configured)
                self._connection.execute(
                    """
                    INSERT INTO standard_backup_job_endpoint_bindings (
                        job_id,
                        job_revision_id,
                        role,
                        ordinal,
                        endpoint_id,
                        endpoint_revision_id,
                        registration_state,
                        registration_reason_code
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.job_revision_id,
                        configured.role.value,
                        configured.ordinal,
                        endpoint_id,
                        endpoint_revision_id,
                        EndpointRegistrationState.REGISTRATION_PENDING.value,
                        ENDPOINT_CLASSIFICATION_PENDING,
                    ),
                )
            registered = self.load_standard_backup_job_endpoint_set(
                job_id=job.job_id,
                job_revision_id=job.job_revision_id,
            )
            if registered is None:
                raise SqliteJobEndpointRegistrationError(
                    "STANDARD_BACKUP_JOB_ENDPOINT_BINDINGS_INCOMPLETE"
                )
            if not outer_transaction:
                self._connection.execute("COMMIT")
            return registered
        except SqliteJobEndpointRegistrationError:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if not outer_transaction and self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqliteJobEndpointRegistrationError(
                "STANDARD_BACKUP_JOB_ENDPOINT_REGISTRATION_FAILED"
            ) from exc

    def load_standard_backup_job_endpoint_set(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> StandardBackupJobEndpointSet | None:
        rows = self._connection.execute(
            """
            SELECT
                bindings.role,
                bindings.ordinal,
                bindings.endpoint_id,
                bindings.endpoint_revision_id,
                revisions.generation,
                revisions.display_name,
                revisions.root_uri,
                bindings.registration_state,
                bindings.registration_reason_code
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            WHERE bindings.job_id = ?
                AND bindings.job_revision_id = ?
            ORDER BY bindings.ordinal
            """,
            (job_id, job_revision_id),
        ).fetchall()
        if not rows:
            return None
        bindings = tuple(
            StandardBackupJobEndpointBinding(
                job_id=job_id,
                job_revision_id=job_revision_id,
                role=JobEndpointRole(str(row[0])),
                ordinal=int(row[1]),
                endpoint_id=str(row[2]),
                endpoint_revision_id=str(row[3]),
                endpoint_generation=int(row[4]),
                display_name=str(row[5]),
                root_uri=str(row[6]),
                registration_state=EndpointRegistrationState(str(row[7])),
                registration_reason_code=str(row[8]),
            )
            for row in rows
        )
        sources = tuple(binding for binding in bindings if binding.role is JobEndpointRole.SOURCE)
        targets = tuple(binding for binding in bindings if binding.role is JobEndpointRole.TARGET)
        if len(sources) != 1 or len(targets) != len(bindings) - 1:
            raise SqliteJobEndpointRegistrationError(
                "STANDARD_BACKUP_JOB_ENDPOINT_BINDINGS_INCOMPLETE"
            )
        return StandardBackupJobEndpointSet(
            job_id=job_id,
            job_revision_id=job_revision_id,
            source=sources[0],
            targets=targets,
        )

    def _find_or_create_endpoint(self, configured: _ConfiguredRoot) -> tuple[str, str]:
        row = self._connection.execute(
            """
            SELECT claims.endpoint_id, heads.active_revision_id
            FROM endpoint_root_claims AS claims
            INNER JOIN endpoint_heads AS heads
                ON heads.endpoint_id = claims.endpoint_id
            WHERE claims.canonical_root_key = ?
            """,
            (configured.canonical_root_key,),
        ).fetchone()
        if row is not None:
            return str(row[0]), str(row[1])

        ids = self._id_factory.new_endpoint_ids()
        self._connection.execute(
            "INSERT INTO endpoints (id) VALUES (?)",
            (ids.endpoint_id,),
        )
        self._connection.execute(
            """
            INSERT INTO endpoint_revisions (
                endpoint_id,
                id,
                display_name,
                root_uri
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                ids.endpoint_id,
                ids.endpoint_revision_id,
                configured.display_name,
                configured.root_uri,
            ),
        )
        self._connection.execute(
            "INSERT INTO endpoint_heads (endpoint_id, active_revision_id) VALUES (?, ?)",
            (ids.endpoint_id, ids.endpoint_revision_id),
        )
        self._connection.execute(
            "INSERT INTO endpoint_root_claims (canonical_root_key, endpoint_id) VALUES (?, ?)",
            (configured.canonical_root_key, ids.endpoint_id),
        )
        return ids.endpoint_id, ids.endpoint_revision_id


def _configured_roots(job: SealedStandardBackupJob) -> tuple[_ConfiguredRoot, ...]:
    roots = [
        _configured_root(
            display_name=job.source_name,
            path_label=job.source_path_label,
            role=JobEndpointRole.SOURCE,
            ordinal=0,
        )
    ]
    roots.extend(
        _configured_root(
            display_name=target.name,
            path_label=target.path_label,
            role=JobEndpointRole.TARGET,
            ordinal=ordinal,
        )
        for ordinal, target in enumerate(job.targets, start=1)
    )
    canonical_keys = tuple(root.canonical_root_key for root in roots)
    if len(set(canonical_keys)) != len(canonical_keys):
        raise SqliteJobEndpointRegistrationError(
            "STANDARD_BACKUP_JOB_ENDPOINT_ROOTS_MUST_BE_UNIQUE"
        )
    return tuple(roots)


def _configured_root(
    *,
    display_name: str,
    path_label: str,
    role: JobEndpointRole,
    ordinal: int,
) -> _ConfiguredRoot:
    raw = path_label.strip()
    if (
        not raw
        or raw.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\"))
        or _WINDOWS_ABSOLUTE_ROOT.match(raw) is None
    ):
        raise SqliteJobEndpointRegistrationError(
            "STANDARD_BACKUP_JOB_ENDPOINT_REQUIRES_ABSOLUTE_LOCAL_PATH"
        )
    normalized = ntpath.normpath(raw)
    if _WINDOWS_ABSOLUTE_ROOT.match(normalized) is None:
        raise SqliteJobEndpointRegistrationError(
            "STANDARD_BACKUP_JOB_ENDPOINT_REQUIRES_ABSOLUTE_LOCAL_PATH"
        )
    uri_path = normalized.replace("\\", "/")
    return _ConfiguredRoot(
        display_name=display_name.strip(),
        root_uri=f"file:///{quote(uri_path, safe='/:')}",
        canonical_root_key=f"local:{uri_path.casefold()}",
        role=role,
        ordinal=ordinal,
    )


def _validate_existing_bindings(
    existing: StandardBackupJobEndpointSet,
    configured_roots: tuple[_ConfiguredRoot, ...],
) -> None:
    existing_bindings = existing.all_bindings
    if len(existing_bindings) != len(configured_roots):
        raise SqliteJobEndpointRegistrationError(
            "STANDARD_BACKUP_JOB_ENDPOINT_BINDINGS_CONFLICT"
        )
    for binding, configured in zip(existing_bindings, configured_roots, strict=True):
        if (
            binding.role is not configured.role
            or binding.ordinal != configured.ordinal
            or binding.root_uri != configured.root_uri
        ):
            raise SqliteJobEndpointRegistrationError(
                "STANDARD_BACKUP_JOB_ENDPOINT_BINDINGS_CONFLICT"
            )
