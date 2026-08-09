from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mediasync_home.adapters.recovery_intents import (
    LocalTargetRecoveryIntentError,
    LocalTargetRecoveryIntentSegmentPublisher,
    SqliteCatalogTargetRecoveryIntentSegmentReader,
)
from mediasync_home.application.recovery_intents import RecoveryIntentSegment
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.run_intent_segments import (
    build_run_target_recovery_intent_segment,
)
from mediasync_home.application.target_recovery_intents import (
    ScannedTargetRecoveryIntentSegment,
    TargetRecoveryIntentScanReport,
    build_target_recovery_intent_segment_document,
    parse_target_recovery_intent_segment_document,
    reconcile_target_recovery_intents_after_startup,
)
from mediasync_home.domain.capabilities import MutationPermit, _issue_mutation_permit


PLAN_CHECKSUM = "1" * 64


def test_target_recovery_intent_document_round_trips_canonical_evidence() -> None:
    operation = _operation()
    segment = _segment(operation)

    encoded = build_target_recovery_intent_segment_document(
        segment=segment,
        operations=(operation,),
        plan_checksum=PLAN_CHECKSUM,
        created_utc="2026-08-09T10:00:00.000Z",
    )
    parsed = parse_target_recovery_intent_segment_document(encoded)

    assert parsed.segment == segment
    assert parsed.plan_checksum == PLAN_CHECKSUM
    assert parsed.operation_payloads[0]["operation_id"] == "op-a"
    assert len(encoded.splitlines()) == 2


def test_target_recovery_intent_document_rejects_changed_operation() -> None:
    operation = _operation()
    encoded = build_target_recovery_intent_segment_document(
        segment=_segment(operation),
        operations=(operation,),
        plan_checksum=PLAN_CHECKSUM,
        created_utc="2026-08-09T10:00:00.000Z",
    )
    lines = encoded.splitlines()
    operation_payload = json.loads(lines[1])
    operation_payload["final_relative_path"] = "Pictures/B.jpg"
    lines[1] = json.dumps(
        operation_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(ValueError, match="TARGET_RECOVERY_INTENT_HASH_MISMATCH"):
        parse_target_recovery_intent_segment_document(b"\n".join(lines) + b"\n")


def test_local_target_recovery_intent_publisher_is_durable_and_idempotent(
    tmp_path: Path,
) -> None:
    target_root = _target_root(tmp_path)
    operation = _operation()
    segment = _segment(operation)
    publisher = LocalTargetRecoveryIntentSegmentPublisher(
        root_resolver=_RootResolver(target_root),
        clock=_AdvancingClock(),
    )

    publisher.publish_target_intent_segment(
        segment=segment,
        operations=(operation,),
        plan_checksum=PLAN_CHECKSUM,
    )
    publisher.publish_target_intent_segment(
        segment=segment,
        operations=(operation,),
        plan_checksum=PLAN_CHECKSUM,
    )

    path = target_root / ".mediasync" / Path(segment.relative_path)
    parsed = parse_target_recovery_intent_segment_document(path.read_bytes())
    assert parsed.segment == segment
    assert not tuple(path.parent.glob("*.tmp"))


def test_local_target_recovery_intent_publisher_rejects_existing_conflict(
    tmp_path: Path,
) -> None:
    target_root = _target_root(tmp_path)
    operation = _operation()
    segment = _segment(operation)
    path = target_root / ".mediasync" / Path(segment.relative_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(LocalTargetRecoveryIntentError, match="HEADER_INVALID"):
        LocalTargetRecoveryIntentSegmentPublisher(
            root_resolver=_RootResolver(target_root),
            clock=_Clock(),
        ).publish_target_intent_segment(
            segment=segment,
            operations=(operation,),
            plan_checksum=PLAN_CHECKSUM,
        )


def test_sqlite_catalog_target_recovery_intent_reader_scans_owned_endpoint(
    tmp_path: Path,
) -> None:
    target_root = _target_root(tmp_path)
    operation = _operation()
    segment = _segment(operation)
    LocalTargetRecoveryIntentSegmentPublisher(
        root_resolver=_RootResolver(target_root),
        clock=_Clock(),
    ).publish_target_intent_segment(
        segment=segment,
        operations=(operation,),
        plan_checksum=PLAN_CHECKSUM,
    )
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE endpoint_revisions (
                id TEXT NOT NULL,
                endpoint_id TEXT NOT NULL,
                root_uri TEXT NOT NULL,
                generation INTEGER NOT NULL,
                owner_installation_id TEXT,
                ownership_epoch INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO endpoint_revisions (
                id,
                endpoint_id,
                root_uri,
                generation,
                owner_installation_id,
                ownership_epoch
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "target-rev-a",
                "target-a",
                target_root.as_uri(),
                1,
                "owner-a",
                1,
            ),
        )
        reader = SqliteCatalogTargetRecoveryIntentSegmentReader(
            catalog_connection=connection,
            owner_installation_id="owner-a",
        )

        report = reader.scan_target_intent_segments(limit=100)

        assert report.scanned == 1
        assert report.issues == ()
        assert report.truncated is False
        assert report.segments[0].document.segment == segment
    finally:
        connection.close()


def test_startup_reconciliation_imports_target_first_crash_evidence() -> None:
    operation = _operation()
    segment = _segment(operation)
    document = parse_target_recovery_intent_segment_document(
        build_target_recovery_intent_segment_document(
            segment=segment,
            operations=(operation,),
            plan_checksum=PLAN_CHECKSUM,
            created_utc="2026-08-09T10:00:00.000Z",
        )
    )
    store = _IntentStore()

    report = reconcile_target_recovery_intents_after_startup(
        reader=_Reader(document),
        intent_segments=store,
        recovery_operations=_OperationStore(operation),
    )

    assert report.mutation_safe is True
    assert report.imported_segment_ids == (segment.segment_id,)
    assert store.imported == (segment,)


def test_startup_reconciliation_blocks_orphan_without_local_operation() -> None:
    operation = _operation()
    segment = _segment(operation)
    document = parse_target_recovery_intent_segment_document(
        build_target_recovery_intent_segment_document(
            segment=segment,
            operations=(operation,),
            plan_checksum=PLAN_CHECKSUM,
            created_utc="2026-08-09T10:00:00.000Z",
        )
    )

    report = reconcile_target_recovery_intents_after_startup(
        reader=_Reader(document),
        intent_segments=_IntentStore(),
        recovery_operations=_OperationStore(),
    )

    assert report.mutation_safe is False
    assert report.unreconciled_segment_ids == (segment.segment_id,)


def test_startup_reconciliation_blocks_database_segment_missing_on_target() -> None:
    operation = _operation()
    segment = _segment(operation)
    store = _IntentStore(initial=(segment,))

    report = reconcile_target_recovery_intents_after_startup(
        reader=_Reader(),
        intent_segments=store,
        recovery_operations=_OperationStore(operation),
    )

    assert report.mutation_safe is False
    assert report.missing_target_segment_ids == (segment.segment_id,)


class _RootResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve_endpoint_root(
        self,
        *,
        resource_key: str,
        endpoint_id: str,
        endpoint_revision_id: str,
    ) -> Path | None:
        assert resource_key == "endpoint:target-a"
        assert endpoint_id == "target-a"
        assert endpoint_revision_id == "target-rev-a"
        return self._root


class _Clock:
    def utc_now(self) -> str:
        return "2026-08-09T10:00:00.000Z"


class _AdvancingClock:
    def __init__(self) -> None:
        self._calls = 0

    def utc_now(self) -> str:
        self._calls += 1
        return f"2026-08-09T10:00:0{self._calls}.000Z"


class _Reader:
    def __init__(self, *documents: object) -> None:
        self._documents = documents

    def scan_target_intent_segments(
        self, *, limit: int
    ) -> TargetRecoveryIntentScanReport:
        assert limit == 10_000
        return TargetRecoveryIntentScanReport(
            scanned=len(self._documents),
            segments=tuple(
                ScannedTargetRecoveryIntentSegment(
                    relative_path=f"segment-{index}.intent.jsonl",
                    document=document,
                )
                for index, document in enumerate(self._documents)
            ),
            issues=(),
            truncated=False,
        )


class _IntentStore:
    def __init__(self, *, initial: tuple[RecoveryIntentSegment, ...] = ()) -> None:
        self._segments = {segment.segment_id: segment for segment in initial}
        self.imported: tuple[RecoveryIntentSegment, ...] = ()

    def load_intent_segment(self, segment_id: str) -> RecoveryIntentSegment | None:
        return self._segments.get(segment_id)

    def import_intent_segment(
        self,
        segment: RecoveryIntentSegment,
    ) -> RecoveryIntentSegment:
        self._segments[segment.segment_id] = segment
        self.imported = (*self.imported, segment)
        return segment

    def list_unresolved_intent_segments(
        self,
        *,
        limit: int,
    ) -> tuple[RecoveryIntentSegment, ...]:
        return tuple(self._segments.values())[:limit]


class _OperationStore:
    def __init__(self, *operations: RecoveryOperation) -> None:
        self._operations = {
            (operation.run_id, operation.operation_id): operation
            for operation in operations
        }

    def load_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> RecoveryOperation | None:
        return self._operations.get((run_id, operation_id))


def _target_root(tmp_path: Path) -> Path:
    root = tmp_path / "target"
    (root / ".mediasync" / "installations" / "owner-a" / "recovery").mkdir(parents=True)
    return root


def _segment(operation: RecoveryOperation) -> RecoveryIntentSegment:
    return build_run_target_recovery_intent_segment(
        permit=_permit(),
        operations=(operation,),
        segment_sequence=0,
        previous_segment_hash=None,
        plan_checksum=PLAN_CHECKSUM,
    )


def _operation() -> RecoveryOperation:
    return replace(
        planned_recovery_operation(
            run_id="run-a",
            run_target_id="run-a-target-0000",
            operation_id="op-a",
            target_endpoint_id="target-a",
            target_endpoint_revision_id="target-rev-a",
            endpoint_generation=1,
            owner_installation_id="owner-a",
            ownership_epoch=1,
            lease_id="lease-a",
            lease_resource_key="endpoint:target-a",
            fencing_token=1,
            final_relative_path="Pictures/A.jpg",
            target_precondition_kind=RecoveryTargetPreconditionKind.ABSENT,
            planned_bytes=128,
        ),
        phase=RecoveryOperationPhase.STAGING_VERIFIED,
        staging_object_id="op-a",
        expected_staging_fingerprint_json='{"byte_count":128}',
    )


def _permit() -> MutationPermit:
    return _issue_mutation_permit(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
    )
