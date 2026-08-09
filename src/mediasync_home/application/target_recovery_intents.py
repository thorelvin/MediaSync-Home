from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol

from mediasync_home.application.recovery_intents import (
    MAX_INTENT_SEGMENT_BYTES,
    RecoveryIntentSegment,
    RecoveryIntentSegmentState,
    durable_recovery_intent_segment,
    recovery_intent_segment_evidence_matches,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
)
from mediasync_home.application.run_intent_segments import (
    HASH_PATTERN,
    canonical_recovery_intent_operation_payload,
    recovery_intent_segment_hash_for_binding,
    recovery_intent_segment_relative_path,
)
from mediasync_home.application.safe_paths import parse_endpoint_relative_path


TARGET_RECOVERY_INTENT_HEADER_BYTES = 64 * 1024
TARGET_RECOVERY_INTENT_DOCUMENT_BYTES = (
    MAX_INTENT_SEGMENT_BYTES + TARGET_RECOVERY_INTENT_HEADER_BYTES
)


class TargetRecoveryIntentViolation(ValueError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


@dataclass(frozen=True)
class TargetRecoveryIntentSegmentDocument:
    segment: RecoveryIntentSegment
    plan_checksum: str
    operation_payloads: tuple[Mapping[str, object], ...]
    created_utc: str
    updated_utc: str


@dataclass(frozen=True)
class TargetRecoveryIntentScanIssue:
    relative_path: str
    validation_code: str


@dataclass(frozen=True)
class ScannedTargetRecoveryIntentSegment:
    relative_path: str
    document: TargetRecoveryIntentSegmentDocument


@dataclass(frozen=True)
class TargetRecoveryIntentScanReport:
    scanned: int
    segments: tuple[ScannedTargetRecoveryIntentSegment, ...]
    issues: tuple[TargetRecoveryIntentScanIssue, ...]
    truncated: bool


@dataclass(frozen=True)
class TargetRecoveryIntentStartupReconciliationReport:
    scanned: int
    matched_segment_ids: tuple[str, ...]
    imported_segment_ids: tuple[str, ...]
    finalized_missing_segment_ids: tuple[str, ...]
    missing_target_segment_ids: tuple[str, ...]
    conflicting_segment_ids: tuple[str, ...]
    unreconciled_segment_ids: tuple[str, ...]
    scan_issues: tuple[TargetRecoveryIntentScanIssue, ...]
    truncated: bool

    @property
    def mutation_safe(self) -> bool:
        return not (
            self.missing_target_segment_ids
            or self.conflicting_segment_ids
            or self.unreconciled_segment_ids
            or self.scan_issues
            or self.truncated
        )


class TargetRecoveryIntentSegmentReader(Protocol):
    def scan_target_intent_segments(
        self,
        *,
        limit: int,
    ) -> TargetRecoveryIntentScanReport: ...


class RecoveryIntentSegmentReconciliationStore(Protocol):
    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None: ...

    def import_intent_segment(
        self,
        segment: RecoveryIntentSegment,
    ) -> RecoveryIntentSegment: ...

    def list_unresolved_intent_segments(
        self,
        *,
        limit: int,
    ) -> tuple[RecoveryIntentSegment, ...]: ...


class RecoveryIntentOperationLookup(Protocol):
    def load_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> RecoveryOperation | None: ...


class MissingTerminalIntentSegmentFinalizer(Protocol):
    def finalize_missing_terminal_intent_segment(self, segment_id: str) -> bool: ...


def reconcile_target_recovery_intents_after_startup(
    *,
    reader: TargetRecoveryIntentSegmentReader,
    intent_segments: RecoveryIntentSegmentReconciliationStore,
    recovery_operations: RecoveryIntentOperationLookup,
    missing_segment_finalizer: MissingTerminalIntentSegmentFinalizer | None = None,
    limit: int = 10_000,
) -> TargetRecoveryIntentStartupReconciliationReport:
    if not 1 <= limit <= 10_000:
        raise _violation(
            "TARGET_RECOVERY_INTENT_RECONCILIATION_LIMIT_INVALID",
            "Retry startup reconciliation with a bounded positive limit.",
        )
    scan = reader.scan_target_intent_segments(limit=limit)
    target_by_id: dict[str, ScannedTargetRecoveryIntentSegment] = {}
    conflicts: set[str] = set()
    for scanned in scan.segments:
        segment_id = scanned.document.segment.segment_id
        existing_target = target_by_id.get(segment_id)
        if existing_target is not None and existing_target.document != scanned.document:
            conflicts.add(segment_id)
            continue
        target_by_id[segment_id] = scanned

    matched: list[str] = []
    imported: list[str] = []
    unreconciled: set[str] = set()
    for segment_id, scanned in sorted(target_by_id.items()):
        if segment_id in conflicts:
            continue
        segment = scanned.document.segment
        existing_database = intent_segments.load_intent_segment(segment_id)
        if existing_database is not None:
            if existing_database.state is RecoveryIntentSegmentState.CLEANED:
                conflicts.add(segment_id)
            elif recovery_intent_segment_evidence_matches(existing_database, segment):
                matched.append(segment_id)
            else:
                conflicts.add(segment_id)
            continue
        operations = _load_document_operations(
            document=scanned.document,
            recovery_operations=recovery_operations,
        )
        if operations is None or not target_recovery_intent_document_matches_operations(
            scanned.document,
            operations,
        ):
            unreconciled.add(segment_id)
            continue
        try:
            intent_segments.import_intent_segment(segment)
        except ValueError:
            unreconciled.add(segment_id)
            continue
        imported.append(segment_id)

    unresolved_database_segments = intent_segments.list_unresolved_intent_segments(
        limit=limit
    )
    missing_candidates = tuple(
        segment.segment_id
        for segment in unresolved_database_segments
        if segment.segment_id not in target_by_id
    )
    finalized_missing: list[str] = []
    missing_target: list[str] = []
    can_finalize_missing = not (
        scan.truncated or scan.issues or conflicts or unreconciled
    )
    for segment_id in sorted(missing_candidates):
        finalized = False
        if missing_segment_finalizer is not None and can_finalize_missing:
            try:
                finalized = missing_segment_finalizer.finalize_missing_terminal_intent_segment(
                    segment_id
                )
            except ValueError:
                finalized = False
        if finalized:
            finalized_missing.append(segment_id)
        else:
            missing_target.append(segment_id)
    return TargetRecoveryIntentStartupReconciliationReport(
        scanned=scan.scanned,
        matched_segment_ids=tuple(matched),
        imported_segment_ids=tuple(imported),
        finalized_missing_segment_ids=tuple(finalized_missing),
        missing_target_segment_ids=tuple(missing_target),
        conflicting_segment_ids=tuple(sorted(conflicts)),
        unreconciled_segment_ids=tuple(sorted(unreconciled)),
        scan_issues=scan.issues,
        truncated=scan.truncated,
    )


def build_target_recovery_intent_segment_document(
    *,
    segment: RecoveryIntentSegment,
    operations: tuple[RecoveryOperation, ...],
    plan_checksum: str,
    created_utc: str,
) -> bytes:
    if HASH_PATTERN.fullmatch(plan_checksum) is None:
        raise _violation(
            "TARGET_RECOVERY_INTENT_PLAN_CHECKSUM_INVALID",
            "Reload the sealed plan before publishing target recovery evidence.",
        )
    if not created_utc.strip():
        raise _violation(
            "TARGET_RECOVERY_INTENT_TIMESTAMP_INVALID",
            "Retry intent publication with a valid UTC timestamp.",
        )
    operation_payloads = tuple(
        canonical_recovery_intent_operation_payload(
            operation=operation, ordinal=ordinal
        )
        for ordinal, operation in enumerate(operations)
    )
    _require_segment_document_binding(
        segment=segment,
        operation_payloads=operation_payloads,
        plan_checksum=plan_checksum,
    )
    header = _header_payload(
        segment=segment,
        plan_checksum=plan_checksum,
        created_utc=created_utc,
    )
    encoded_header = _canonical_json_line(header)
    if len(encoded_header) > TARGET_RECOVERY_INTENT_HEADER_BYTES:
        raise _violation(
            "TARGET_RECOVERY_INTENT_HEADER_TOO_LARGE",
            "Reduce recovery intent header metadata before retrying publication.",
        )
    encoded_operations = tuple(
        _canonical_json_line(payload) for payload in operation_payloads
    )
    document = b"".join((encoded_header, *encoded_operations))
    if len(document) > TARGET_RECOVERY_INTENT_DOCUMENT_BYTES:
        raise _violation(
            "TARGET_RECOVERY_INTENT_DOCUMENT_TOO_LARGE",
            "Publish a smaller bounded recovery intent segment.",
        )
    return document


def parse_target_recovery_intent_segment_document(
    document: bytes,
) -> TargetRecoveryIntentSegmentDocument:
    if not document or len(document) > TARGET_RECOVERY_INTENT_DOCUMENT_BYTES:
        raise _violation(
            "TARGET_RECOVERY_INTENT_DOCUMENT_SIZE_INVALID",
            "Inspect the bounded target recovery intent document.",
        )
    lines = document.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise _violation(
            "TARGET_RECOVERY_INTENT_DOCUMENT_NOT_CANONICAL",
            "Inspect the target recovery intent document before reconciliation.",
        )
    if len(lines[0]) > TARGET_RECOVERY_INTENT_HEADER_BYTES:
        raise _violation(
            "TARGET_RECOVERY_INTENT_HEADER_TOO_LARGE",
            "Inspect the target recovery intent header before reconciliation.",
        )
    header = _parse_canonical_object(
        lines[0], code="TARGET_RECOVERY_INTENT_HEADER_INVALID"
    )
    _require_exact_keys(
        header, _HEADER_KEYS, code="TARGET_RECOVERY_INTENT_HEADER_INVALID"
    )
    segment = _segment_from_header(header)
    plan_checksum = _required_hash(header, "plan_checksum")
    created_utc = _required_text(header, "created_utc")
    updated_utc = _required_text(header, "updated_utc")
    if created_utc != updated_utc:
        raise _violation(
            "TARGET_RECOVERY_INTENT_TIMESTAMP_MISMATCH",
            "Inspect the changed immutable target recovery intent header.",
        )

    operation_payloads: list[Mapping[str, object]] = []
    operation_bytes = 0
    for ordinal, line in enumerate(lines[1:]):
        payload = _parse_canonical_object(
            line,
            code="TARGET_RECOVERY_INTENT_OPERATION_INVALID",
        )
        _require_exact_keys(
            payload,
            _OPERATION_KEYS,
            code="TARGET_RECOVERY_INTENT_OPERATION_INVALID",
        )
        if (
            payload.get("record_type") != "OPERATION"
            or payload.get("ordinal") != ordinal
        ):
            raise _violation(
                "TARGET_RECOVERY_INTENT_OPERATION_ORDER_INVALID",
                "Inspect the target recovery intent operation order.",
            )
        _required_text(payload, "operation_id")
        parse_endpoint_relative_path(_required_text(payload, "final_relative_path"))
        source_relative_path = payload.get("source_relative_path")
        if source_relative_path is not None:
            if not isinstance(source_relative_path, str):
                raise _violation(
                    "TARGET_RECOVERY_INTENT_OPERATION_INVALID",
                    "Inspect the target recovery intent operation payload.",
                )
            parse_endpoint_relative_path(source_relative_path)
        operation_payloads.append(payload)
        operation_bytes += len(line)

    if len(operation_payloads) != segment.operation_count:
        raise _violation(
            "TARGET_RECOVERY_INTENT_OPERATION_COUNT_MISMATCH",
            "Inspect the incomplete target recovery intent document.",
        )
    if operation_bytes != segment.byte_count:
        raise _violation(
            "TARGET_RECOVERY_INTENT_BYTE_COUNT_MISMATCH",
            "Inspect the changed target recovery intent document.",
        )
    operation_tuple = tuple(operation_payloads)
    _require_segment_document_binding(
        segment=segment,
        operation_payloads=operation_tuple,
        plan_checksum=plan_checksum,
    )
    return TargetRecoveryIntentSegmentDocument(
        segment=segment,
        plan_checksum=plan_checksum,
        operation_payloads=operation_tuple,
        created_utc=created_utc,
        updated_utc=updated_utc,
    )


def target_recovery_intent_document_matches_operations(
    document: TargetRecoveryIntentSegmentDocument,
    operations: tuple[RecoveryOperation, ...],
) -> bool:
    if len(operations) != document.segment.operation_count:
        return False
    ordered = tuple(sorted(operations, key=lambda operation: operation.operation_id))
    expected = tuple(
        canonical_recovery_intent_operation_payload(
            operation=operation, ordinal=ordinal
        )
        for ordinal, operation in enumerate(ordered)
    )
    return expected == document.operation_payloads


def _load_document_operations(
    *,
    document: TargetRecoveryIntentSegmentDocument,
    recovery_operations: RecoveryIntentOperationLookup,
) -> tuple[RecoveryOperation, ...] | None:
    loaded: list[RecoveryOperation] = []
    for payload in document.operation_payloads:
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            return None
        operation = recovery_operations.load_operation(
            run_id=document.segment.run_id,
            operation_id=operation_id,
        )
        if (
            operation is None
            or operation.phase is not RecoveryOperationPhase.STAGING_VERIFIED
            or operation.run_target_id != document.segment.run_target_id
        ):
            return None
        loaded.append(operation)
    return tuple(loaded)


def _require_segment_document_binding(
    *,
    segment: RecoveryIntentSegment,
    operation_payloads: tuple[Mapping[str, object], ...],
    plan_checksum: str,
) -> None:
    expected_path = recovery_intent_segment_relative_path(
        owner_installation_id=segment.owner_installation_id,
        run_id=segment.run_id,
        segment_sequence=segment.segment_sequence,
    )
    if segment.relative_path.replace("\\", "/") != expected_path:
        raise _violation(
            "TARGET_RECOVERY_INTENT_PATH_MISMATCH",
            "Rebuild the target recovery intent path from stable identifiers.",
        )
    if len(operation_payloads) != segment.operation_count:
        raise _violation(
            "TARGET_RECOVERY_INTENT_OPERATION_COUNT_MISMATCH",
            "Rebuild the bounded target recovery intent segment.",
        )
    byte_count = sum(
        len(_canonical_json_line(payload)) for payload in operation_payloads
    )
    if byte_count != segment.byte_count:
        raise _violation(
            "TARGET_RECOVERY_INTENT_BYTE_COUNT_MISMATCH",
            "Rebuild the bounded target recovery intent segment.",
        )
    expected_hash = recovery_intent_segment_hash_for_binding(
        run_id=segment.run_id,
        run_target_id=segment.run_target_id,
        endpoint_id=segment.target_endpoint_id,
        endpoint_revision_id=segment.target_endpoint_revision_id,
        owner_installation_id=segment.owner_installation_id,
        ownership_epoch=segment.ownership_epoch,
        lease_id=segment.lease_id,
        fencing_token=segment.fencing_token,
        operation_payloads=operation_payloads,
        plan_checksum=plan_checksum,
    )
    if expected_hash != segment.segment_hash:
        raise _violation(
            "TARGET_RECOVERY_INTENT_HASH_MISMATCH",
            "Inspect the changed target recovery intent document.",
        )


def _header_payload(
    *,
    segment: RecoveryIntentSegment,
    plan_checksum: str,
    created_utc: str,
) -> dict[str, object]:
    return {
        "byte_count": segment.byte_count,
        "created_utc": created_utc,
        "durability_state": segment.durability_state.value,
        "endpoint_generation": segment.endpoint_generation,
        "fencing_token": segment.fencing_token,
        "lease_id": segment.lease_id,
        "operation_count": segment.operation_count,
        "owner_installation_id": segment.owner_installation_id,
        "ownership_epoch": segment.ownership_epoch,
        "plan_checksum": plan_checksum,
        "previous_segment_hash": segment.previous_segment_hash,
        "record_type": "HEADER",
        "relative_path": segment.relative_path,
        "run_id": segment.run_id,
        "run_target_id": segment.run_target_id,
        "schema_version": segment.schema_version,
        "segment_hash": segment.segment_hash,
        "segment_id": segment.segment_id,
        "segment_sequence": segment.segment_sequence,
        "state": segment.state.value,
        "target_endpoint_id": segment.target_endpoint_id,
        "target_endpoint_revision_id": segment.target_endpoint_revision_id,
        "updated_utc": created_utc,
    }


def _segment_from_header(header: Mapping[str, object]) -> RecoveryIntentSegment:
    if (
        header.get("record_type") != "HEADER"
        or header.get("durability_state") != "DURABLE"
        or header.get("state") != "DURABLE"
        or header.get("schema_version") != 1
    ):
        raise _violation(
            "TARGET_RECOVERY_INTENT_HEADER_INVALID",
            "Inspect the target recovery intent header.",
        )
    try:
        return durable_recovery_intent_segment(
            segment_id=_required_text(header, "segment_id"),
            run_id=_required_text(header, "run_id"),
            run_target_id=_required_text(header, "run_target_id"),
            target_endpoint_id=_required_text(header, "target_endpoint_id"),
            target_endpoint_revision_id=_required_text(
                header,
                "target_endpoint_revision_id",
            ),
            endpoint_generation=_required_int(header, "endpoint_generation"),
            owner_installation_id=_required_text(header, "owner_installation_id"),
            ownership_epoch=_required_int(header, "ownership_epoch"),
            lease_id=_required_text(header, "lease_id"),
            fencing_token=_required_int(header, "fencing_token"),
            segment_sequence=_required_int(header, "segment_sequence", minimum=0),
            relative_path=_required_text(header, "relative_path"),
            schema_version=_required_int(header, "schema_version"),
            operation_count=_required_int(header, "operation_count"),
            byte_count=_required_int(header, "byte_count", minimum=0),
            segment_hash=_required_hash(header, "segment_hash"),
            previous_segment_hash=_optional_hash(header, "previous_segment_hash"),
        )
    except ValueError as exc:
        if isinstance(exc, TargetRecoveryIntentViolation):
            raise
        raise _violation(
            "TARGET_RECOVERY_INTENT_HEADER_INVALID",
            "Inspect the target recovery intent header.",
        ) from exc


def _parse_canonical_object(line: bytes, *, code: str) -> dict[str, object]:
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _violation(code, "Inspect the target recovery intent document.") from exc
    if not isinstance(payload, dict) or _canonical_json_line(payload) != line:
        raise _violation(
            code, "Inspect the non-canonical target recovery intent document."
        )
    return payload


def _canonical_json_line(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _required_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _violation(
            "TARGET_RECOVERY_INTENT_FIELD_INVALID",
            "Inspect the target recovery intent document fields.",
        )
    return value


def _required_int(
    payload: Mapping[str, object],
    field: str,
    *,
    minimum: int = 1,
) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise _violation(
            "TARGET_RECOVERY_INTENT_FIELD_INVALID",
            "Inspect the target recovery intent document fields.",
        )
    return value


def _required_hash(payload: Mapping[str, object], field: str) -> str:
    value = _required_text(payload, field)
    if HASH_PATTERN.fullmatch(value) is None:
        raise _violation(
            "TARGET_RECOVERY_INTENT_FIELD_INVALID",
            "Inspect the target recovery intent document hashes.",
        )
    return value


def _optional_hash(payload: Mapping[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    return _required_hash(payload, field)


def _require_exact_keys(
    payload: Mapping[str, object],
    expected: frozenset[str],
    *,
    code: str,
) -> None:
    if frozenset(payload) != expected:
        raise _violation(code, "Inspect the target recovery intent document schema.")


def _violation(code: str, next_action: str) -> TargetRecoveryIntentViolation:
    return TargetRecoveryIntentViolation(code, next_action)


_HEADER_KEYS = frozenset(
    {
        "byte_count",
        "created_utc",
        "durability_state",
        "endpoint_generation",
        "fencing_token",
        "lease_id",
        "operation_count",
        "owner_installation_id",
        "ownership_epoch",
        "plan_checksum",
        "previous_segment_hash",
        "record_type",
        "relative_path",
        "run_id",
        "run_target_id",
        "schema_version",
        "segment_hash",
        "segment_id",
        "segment_sequence",
        "state",
        "target_endpoint_id",
        "target_endpoint_revision_id",
        "updated_utc",
    }
)

_OPERATION_KEYS = frozenset(
    {
        "assurance_level",
        "expected_final_fingerprint_json",
        "expected_source_fingerprint_json",
        "expected_source_parent_identity_json",
        "expected_staging_fingerprint_json",
        "expected_target_fingerprint_json",
        "expected_target_parent_identity_json",
        "expected_target_path_chain_hash",
        "final_relative_path",
        "operation_id",
        "operation_kind",
        "ordinal",
        "plan_sequence_no",
        "planned_bytes",
        "record_type",
        "source_case_context_hash",
        "source_endpoint_id",
        "source_endpoint_revision_id",
        "source_guard_evidence_hash",
        "source_guard_kind",
        "source_hash_evidence_kind",
        "source_path_chain_hash",
        "source_precondition_json",
        "source_relative_path",
        "staging_durability_state",
        "staging_object_id",
        "target_precondition_kind",
    }
)
