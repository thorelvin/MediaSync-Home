from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mediasync_home.adapters.endpoint_leases import EndpointRootResolver
from mediasync_home.adapters.reparse_guard import LocalReparseGuard, ReparseGuardError
from mediasync_home.adapters.system_clock import SystemClock
from mediasync_home.adapters.windows_durability import move_path_write_through
from mediasync_home.adapters.sqlite.endpoint_roots import local_path_from_file_uri
from mediasync_home.application.clocks import ClockPort
from mediasync_home.application.recovery_intents import (
    RecoveryIntentSegment,
    RecoveryIntentSegmentState,
    recovery_intent_segment_evidence_matches,
)
from mediasync_home.application.recovery_operations import RecoveryOperation
from mediasync_home.application.run_intent_cleanup import (
    TargetRecoveryIntentSegmentCleanupPort,
)
from mediasync_home.application.run_intent_segments import (
    TargetRecoveryIntentSegmentPublisher,
    recovery_intent_segment_relative_path,
)
from mediasync_home.application.target_recovery_intents import (
    TARGET_RECOVERY_INTENT_DOCUMENT_BYTES,
    ScannedTargetRecoveryIntentSegment,
    TargetRecoveryIntentScanIssue,
    TargetRecoveryIntentScanReport,
    TargetRecoveryIntentSegmentReader,
    build_target_recovery_intent_segment_document,
    parse_target_recovery_intent_segment_document,
)
from mediasync_home.domain.capabilities import MutationPermit


class LocalTargetRecoveryIntentError(ValueError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class TargetRecoveryIntentMutationPermitValidator(Protocol):
    def assert_mutation_permit_current(self, permit: MutationPermit) -> None: ...


class LocalTargetRecoveryIntentSegmentPublisher(TargetRecoveryIntentSegmentPublisher):
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        clock: ClockPort | None = None,
        reparse_guard: LocalReparseGuard | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._clock = clock or SystemClock()
        self._reparse_guard = reparse_guard or LocalReparseGuard()

    def publish_target_intent_segment(
        self,
        *,
        segment: RecoveryIntentSegment,
        operations: tuple[RecoveryOperation, ...],
        plan_checksum: str,
    ) -> None:
        expected_relative_path = recovery_intent_segment_relative_path(
            owner_installation_id=segment.owner_installation_id,
            run_id=segment.run_id,
            segment_sequence=segment.segment_sequence,
        )
        if segment.relative_path.replace("\\", "/") != expected_relative_path:
            raise _error(
                "TARGET_RECOVERY_INTENT_PATH_MISMATCH",
                "Rebuild the intent path from stable target identifiers.",
            )
        root = self._resolve_root(segment)
        target_path = root / ".mediasync" / Path(expected_relative_path)
        self._prepare_parent(root=root, target_path=target_path)
        document = build_target_recovery_intent_segment_document(
            segment=segment,
            operations=operations,
            plan_checksum=plan_checksum,
            created_utc=self._clock.utc_now(),
        )

        if target_path.exists():
            self._require_existing_document(target_path, document)
            return

        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"
        try:
            with temp_path.open("xb") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                _publish_no_overwrite(temp_path, target_path)
            except FileExistsError:
                self._require_existing_document(target_path, document)
            else:
                self._require_existing_document(target_path, document)
        except LocalTargetRecoveryIntentError:
            raise
        except OSError as exc:
            raise _error(
                "TARGET_RECOVERY_INTENT_PUBLICATION_FAILED",
                "Restore target control-area write and flush access before retrying.",
            ) from exc
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _resolve_root(self, segment: RecoveryIntentSegment) -> Path:
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=f"endpoint:{segment.target_endpoint_id}",
                endpoint_id=segment.target_endpoint_id,
                endpoint_revision_id=segment.target_endpoint_revision_id,
            )
        except ValueError as exc:
            raise _error(
                "TARGET_RECOVERY_INTENT_ENDPOINT_MISMATCH",
                "Refresh the target endpoint binding before publishing recovery evidence.",
            ) from exc
        if root is None:
            raise _error(
                "TARGET_RECOVERY_INTENT_ENDPOINT_NOT_FOUND",
                "Refresh the target endpoint binding before publishing recovery evidence.",
            )
        try:
            return self._reparse_guard.resolve_existing_root(
                Path(root),
                missing_code="TARGET_RECOVERY_INTENT_ROOT_MISSING",
                missing_next_action="Reconnect the target before publishing recovery evidence.",
                reparse_code="TARGET_RECOVERY_INTENT_ROOT_REPARSE_BLOCKED",
                reparse_next_action="Use an ordinary non-reparse target root.",
            )
        except ReparseGuardError as exc:
            raise _error(exc.validation_code, exc.next_action) from exc

    def _prepare_parent(self, *, root: Path, target_path: Path) -> None:
        relative_parent = target_path.parent.relative_to(root)
        fixed_parent_parts = relative_parent.parts[:-1]
        try:
            self._reparse_guard.reject_reparse_chain(
                root=root,
                relative_parts=fixed_parent_parts,
                missing_code="TARGET_RECOVERY_INTENT_CONTROL_AREA_MISSING",
                missing_next_action="Re-register the writable target control area.",
                reparse_code="TARGET_RECOVERY_INTENT_CONTROL_REPARSE_BLOCKED",
                reparse_next_action="Repair the target control area before retrying.",
            )
            target_path.parent.mkdir(exist_ok=True)
            self._reparse_guard.reject_reparse_chain(
                root=root,
                relative_parts=relative_parent.parts,
                missing_code="TARGET_RECOVERY_INTENT_DIRECTORY_MISSING",
                missing_next_action="Retry target recovery intent publication.",
                reparse_code="TARGET_RECOVERY_INTENT_CONTROL_REPARSE_BLOCKED",
                reparse_next_action="Repair the target control area before retrying.",
            )
        except (OSError, ReparseGuardError, ValueError) as exc:
            if isinstance(exc, ReparseGuardError):
                raise _error(exc.validation_code, exc.next_action) from exc
            raise _error(
                "TARGET_RECOVERY_INTENT_DIRECTORY_CREATE_FAILED",
                "Restore target control-area access before retrying intent publication.",
            ) from exc

    def _require_existing_document(self, path: Path, expected: bytes) -> None:
        try:
            inspection = self._reparse_guard.reject_reparse_chain(
                root=path.parent,
                relative_parts=(path.name,),
                missing_code="TARGET_RECOVERY_INTENT_DOCUMENT_MISSING",
                missing_next_action="Retry target recovery intent publication.",
                reparse_code="TARGET_RECOVERY_INTENT_DOCUMENT_REPARSE_BLOCKED",
                reparse_next_action="Inspect the changed target recovery intent path.",
            )
            del inspection
            file_stat = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode):
                raise _error(
                    "TARGET_RECOVERY_INTENT_DOCUMENT_TYPE_CONFLICT",
                    "Inspect the changed target recovery intent path.",
                )
            if file_stat.st_size > TARGET_RECOVERY_INTENT_DOCUMENT_BYTES:
                raise _error(
                    "TARGET_RECOVERY_INTENT_DOCUMENT_TOO_LARGE",
                    "Inspect the changed target recovery intent document.",
                )
            actual = path.read_bytes()
            actual_document = parse_target_recovery_intent_segment_document(actual)
            expected_document = parse_target_recovery_intent_segment_document(expected)
        except LocalTargetRecoveryIntentError:
            raise
        except (OSError, ReparseGuardError, ValueError) as exc:
            validation_code = getattr(
                exc,
                "validation_code",
                "TARGET_RECOVERY_INTENT_DOCUMENT_INVALID",
            )
            next_action = getattr(
                exc,
                "next_action",
                "Inspect the changed target recovery intent document.",
            )
            raise _error(str(validation_code), str(next_action)) from exc
        if (
            actual_document.segment != expected_document.segment
            or actual_document.plan_checksum != expected_document.plan_checksum
            or actual_document.operation_payloads != expected_document.operation_payloads
        ):
            raise _error(
                "TARGET_RECOVERY_INTENT_IDEMPOTENCY_CONFLICT",
                "Inspect the conflicting immutable target recovery intent document.",
            )


class LocalTargetRecoveryIntentSegmentCleanup(
    TargetRecoveryIntentSegmentCleanupPort
):
    def __init__(
        self,
        *,
        root_resolver: EndpointRootResolver,
        permit_validator: TargetRecoveryIntentMutationPermitValidator,
        reparse_guard: LocalReparseGuard | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._permit_validator = permit_validator
        self._reparse_guard = reparse_guard or LocalReparseGuard()

    def ensure_target_intent_segment_absent(
        self,
        *,
        permit: MutationPermit,
        segment: RecoveryIntentSegment,
    ) -> bool:
        self._permit_validator.assert_mutation_permit_current(permit)
        self._require_cleanup_binding(permit=permit, segment=segment)
        expected_relative_path = recovery_intent_segment_relative_path(
            owner_installation_id=segment.owner_installation_id,
            run_id=segment.run_id,
            segment_sequence=segment.segment_sequence,
        )
        if segment.relative_path.replace("\\", "/") != expected_relative_path:
            raise _error(
                "TARGET_RECOVERY_INTENT_PATH_MISMATCH",
                "Inspect the changed target recovery intent path.",
            )
        root = self._resolve_root(segment)
        target_path = root / ".mediasync" / Path(expected_relative_path)
        self._require_safe_parent(root=root, target_path=target_path)
        if not target_path.exists() and not target_path.is_symlink():
            return True
        self._require_matching_document(path=target_path, segment=segment)
        try:
            target_path.unlink()
            if os.name != "nt":
                directory_fd = os.open(target_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise _error(
                "TARGET_RECOVERY_INTENT_CLEANUP_FAILED",
                "Restore target control-area write access before retrying cleanup.",
            ) from exc
        return False

    def _require_cleanup_binding(
        self,
        *,
        permit: MutationPermit,
        segment: RecoveryIntentSegment,
    ) -> None:
        if (
            segment.state is not RecoveryIntentSegmentState.CLEANUP_ELIGIBLE
            or segment.run_id != permit.run_id
            or segment.run_target_id != permit.run_target_id
            or segment.target_endpoint_id != permit.endpoint_id
            or segment.target_endpoint_revision_id != permit.endpoint_revision_id
            or segment.owner_installation_id != permit.owner_installation_id
            or segment.ownership_epoch != permit.ownership_epoch
        ):
            raise _error(
                "TARGET_RECOVERY_INTENT_CLEANUP_BINDING_MISMATCH",
                "Reconcile the endpoint lease and intent lifecycle before cleanup.",
            )

    def _resolve_root(self, segment: RecoveryIntentSegment) -> Path:
        try:
            root = self._root_resolver.resolve_endpoint_root(
                resource_key=f"endpoint:{segment.target_endpoint_id}",
                endpoint_id=segment.target_endpoint_id,
                endpoint_revision_id=segment.target_endpoint_revision_id,
            )
        except ValueError as exc:
            raise _error(
                "TARGET_RECOVERY_INTENT_ENDPOINT_MISMATCH",
                "Refresh the target endpoint binding before cleaning recovery evidence.",
            ) from exc
        if root is None:
            raise _error(
                "TARGET_RECOVERY_INTENT_ENDPOINT_NOT_FOUND",
                "Reconnect the target before cleaning recovery evidence.",
            )
        try:
            return self._reparse_guard.resolve_existing_root(
                Path(root),
                missing_code="TARGET_RECOVERY_INTENT_ROOT_MISSING",
                missing_next_action="Reconnect the target before cleaning recovery evidence.",
                reparse_code="TARGET_RECOVERY_INTENT_ROOT_REPARSE_BLOCKED",
                reparse_next_action="Use an ordinary non-reparse target root.",
            )
        except ReparseGuardError as exc:
            raise _error(exc.validation_code, exc.next_action) from exc

    def _require_safe_parent(self, *, root: Path, target_path: Path) -> None:
        try:
            relative_parent = target_path.parent.relative_to(root)
            self._reparse_guard.reject_reparse_chain(
                root=root,
                relative_parts=relative_parent.parts,
                missing_code="TARGET_RECOVERY_INTENT_CONTROL_AREA_MISSING",
                missing_next_action="Inspect the target recovery control area before cleanup.",
                reparse_code="TARGET_RECOVERY_INTENT_CONTROL_REPARSE_BLOCKED",
                reparse_next_action="Repair the target recovery control area before cleanup.",
            )
        except (OSError, ReparseGuardError, ValueError) as exc:
            if isinstance(exc, ReparseGuardError):
                raise _error(exc.validation_code, exc.next_action) from exc
            raise _error(
                "TARGET_RECOVERY_INTENT_CLEANUP_PATH_INVALID",
                "Inspect the target recovery control area before cleanup.",
            ) from exc

    def _require_matching_document(
        self,
        *,
        path: Path,
        segment: RecoveryIntentSegment,
    ) -> None:
        try:
            self._reparse_guard.reject_reparse_chain(
                root=path.parent,
                relative_parts=(path.name,),
                missing_code="TARGET_RECOVERY_INTENT_DOCUMENT_MISSING",
                missing_next_action="Retry target recovery intent cleanup.",
                reparse_code="TARGET_RECOVERY_INTENT_DOCUMENT_REPARSE_BLOCKED",
                reparse_next_action="Inspect the changed target recovery intent path.",
            )
            file_stat = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size > TARGET_RECOVERY_INTENT_DOCUMENT_BYTES
            ):
                raise _error(
                    "TARGET_RECOVERY_INTENT_DOCUMENT_TYPE_OR_SIZE_INVALID",
                    "Inspect the changed target recovery intent document.",
                )
            document = parse_target_recovery_intent_segment_document(path.read_bytes())
        except LocalTargetRecoveryIntentError:
            raise
        except (OSError, ReparseGuardError, ValueError) as exc:
            raise _error(
                str(
                    getattr(
                        exc,
                        "validation_code",
                        "TARGET_RECOVERY_INTENT_DOCUMENT_INVALID",
                    )
                ),
                str(
                    getattr(
                        exc,
                        "next_action",
                        "Inspect the changed target recovery intent document.",
                    )
                ),
            ) from exc
        if not recovery_intent_segment_evidence_matches(document.segment, segment):
            raise _error(
                "TARGET_RECOVERY_INTENT_CLEANUP_EVIDENCE_MISMATCH",
                "Inspect the changed target recovery intent document.",
            )


class SqliteCatalogTargetRecoveryIntentSegmentReader(TargetRecoveryIntentSegmentReader):
    def __init__(
        self,
        *,
        catalog_connection: sqlite3.Connection,
        owner_installation_id: str,
        reparse_guard: LocalReparseGuard | None = None,
    ) -> None:
        self._catalog_connection = catalog_connection
        self._owner_installation_id = owner_installation_id
        self._reparse_guard = reparse_guard or LocalReparseGuard()

    def scan_target_intent_segments(
        self,
        *,
        limit: int,
    ) -> TargetRecoveryIntentScanReport:
        if not 1 <= limit <= 10_000:
            raise _error(
                "TARGET_RECOVERY_INTENT_SCAN_LIMIT_INVALID",
                "Retry target recovery intent scanning with a bounded positive limit.",
            )
        roots = self._catalog_roots()
        scanned = 0
        truncated = False
        segments: list[ScannedTargetRecoveryIntentSegment] = []
        issues: list[TargetRecoveryIntentScanIssue] = []
        for root, bindings in roots:
            recovery_root = self._owner_recovery_root(root)
            if not recovery_root.exists():
                continue
            try:
                relative_recovery = recovery_root.relative_to(root)
                self._reparse_guard.reject_reparse_chain(
                    root=root,
                    relative_parts=relative_recovery.parts,
                    missing_code="TARGET_RECOVERY_INTENT_SCAN_ROOT_MISSING",
                    missing_next_action="Reconnect the target before startup reconciliation.",
                    reparse_code="TARGET_RECOVERY_INTENT_SCAN_REPARSE_BLOCKED",
                    reparse_next_action="Inspect the target recovery control area.",
                )
                run_directories = sorted(
                    recovery_root.iterdir(), key=lambda path: path.name
                )
            except (OSError, ReparseGuardError, ValueError) as exc:
                issues.append(
                    _scan_issue(
                        relative_path=_root_label(root),
                        exc=exc,
                        fallback="TARGET_RECOVERY_INTENT_SCAN_FAILED",
                    )
                )
                continue
            for run_directory in run_directories:
                try:
                    run_relative = run_directory.relative_to(root)
                    self._reparse_guard.reject_reparse_chain(
                        root=root,
                        relative_parts=run_relative.parts,
                        missing_code="TARGET_RECOVERY_INTENT_RUN_DIRECTORY_MISSING",
                        missing_next_action="Inspect the target recovery control area.",
                        reparse_code="TARGET_RECOVERY_INTENT_SCAN_REPARSE_BLOCKED",
                        reparse_next_action="Inspect the target recovery control area.",
                    )
                    if not run_directory.is_dir():
                        continue
                    marker_paths = sorted(
                        run_directory.glob("segment-*.intent.jsonl"),
                        key=lambda path: path.name,
                    )
                except (OSError, ReparseGuardError, ValueError) as exc:
                    issues.append(
                        _scan_issue(
                            relative_path=_relative_label(root, run_directory),
                            exc=exc,
                            fallback="TARGET_RECOVERY_INTENT_RUN_SCAN_FAILED",
                        )
                    )
                    continue
                for marker_path in marker_paths:
                    if scanned >= limit:
                        truncated = True
                        break
                    scanned += 1
                    try:
                        scanned_segment = self._read_marker(
                            root=root,
                            marker_path=marker_path,
                            bindings=bindings,
                        )
                    except (OSError, ReparseGuardError, ValueError) as exc:
                        issues.append(
                            _scan_issue(
                                relative_path=_relative_label(root, marker_path),
                                exc=exc,
                                fallback="TARGET_RECOVERY_INTENT_DOCUMENT_INVALID",
                            )
                        )
                    else:
                        segments.append(scanned_segment)
                if truncated:
                    break
            if truncated:
                break
        return TargetRecoveryIntentScanReport(
            scanned=scanned,
            segments=tuple(segments),
            issues=tuple(issues),
            truncated=truncated,
        )

    def _catalog_roots(
        self,
    ) -> tuple[tuple[Path, frozenset[tuple[str, str, int, int]]], ...]:
        rows = self._catalog_connection.execute(
            """
            SELECT
                root_uri,
                endpoint_id,
                id,
                generation,
                ownership_epoch
            FROM endpoint_revisions
            WHERE owner_installation_id = ?
            ORDER BY root_uri, endpoint_id, id
            """,
            (self._owner_installation_id,),
        ).fetchall()
        grouped: dict[Path, set[tuple[str, str, int, int]]] = {}
        for row in rows:
            root = local_path_from_file_uri(str(row[0])).resolve(strict=False)
            ownership_epoch = row[4]
            if not isinstance(ownership_epoch, int) or ownership_epoch < 1:
                raise _error(
                    "TARGET_RECOVERY_INTENT_ENDPOINT_OWNERSHIP_INVALID",
                    "Refresh target endpoint ownership before startup reconciliation.",
                )
            grouped.setdefault(root, set()).add(
                (str(row[1]), str(row[2]), int(row[3]), ownership_epoch)
            )
        return tuple(
            (root, frozenset(bindings))
            for root, bindings in sorted(grouped.items(), key=lambda item: str(item[0]))
        )

    def _owner_recovery_root(self, root: Path) -> Path:
        relative = recovery_intent_segment_relative_path(
            owner_installation_id=self._owner_installation_id,
            run_id="scan",
            segment_sequence=0,
        )
        owner_component = relative.split("/", 2)[1]
        return root / ".mediasync" / "installations" / owner_component / "recovery"

    def _read_marker(
        self,
        *,
        root: Path,
        marker_path: Path,
        bindings: frozenset[tuple[str, str, int, int]],
    ) -> ScannedTargetRecoveryIntentSegment:
        relative = marker_path.relative_to(root)
        self._reparse_guard.reject_reparse_chain(
            root=root,
            relative_parts=relative.parts,
            missing_code="TARGET_RECOVERY_INTENT_DOCUMENT_MISSING",
            missing_next_action="Inspect the target recovery control area.",
            reparse_code="TARGET_RECOVERY_INTENT_SCAN_REPARSE_BLOCKED",
            reparse_next_action="Inspect the target recovery control area.",
        )
        file_stat = marker_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_size > TARGET_RECOVERY_INTENT_DOCUMENT_BYTES
        ):
            raise _error(
                "TARGET_RECOVERY_INTENT_DOCUMENT_TYPE_OR_SIZE_INVALID",
                "Inspect the target recovery intent document.",
            )
        document = parse_target_recovery_intent_segment_document(
            marker_path.read_bytes()
        )
        segment = document.segment
        expected_binding = (
            segment.target_endpoint_id,
            segment.target_endpoint_revision_id,
            segment.endpoint_generation,
            segment.ownership_epoch,
        )
        if (
            segment.owner_installation_id != self._owner_installation_id
            or expected_binding not in bindings
        ):
            raise _error(
                "TARGET_RECOVERY_INTENT_ENDPOINT_BINDING_MISMATCH",
                "Inspect the target recovery evidence and endpoint ownership.",
            )
        expected_path = (Path(".mediasync") / Path(segment.relative_path)).as_posix()
        actual_path = marker_path.relative_to(root).as_posix()
        if actual_path != expected_path:
            raise _error(
                "TARGET_RECOVERY_INTENT_PATH_MISMATCH",
                "Inspect the relocated target recovery intent document.",
            )
        return ScannedTargetRecoveryIntentSegment(
            relative_path=actual_path,
            document=document,
        )


def _publish_no_overwrite(source: Path, destination: Path) -> None:
    if os.name == "nt":
        move_path_write_through(source, destination, replace_existing=False)
        return
    os.link(source, destination)
    source.unlink()
    directory_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _scan_issue(
    *,
    relative_path: str,
    exc: BaseException,
    fallback: str,
) -> TargetRecoveryIntentScanIssue:
    validation_code = getattr(exc, "validation_code", fallback)
    return TargetRecoveryIntentScanIssue(
        relative_path=relative_path,
        validation_code=str(validation_code),
    )


def _relative_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return _root_label(root)


def _root_label(root: Path) -> str:
    return f"endpoint-root:{root.name or 'root'}"


def _error(code: str, next_action: str) -> LocalTargetRecoveryIntentError:
    return LocalTargetRecoveryIntentError(code, next_action)
