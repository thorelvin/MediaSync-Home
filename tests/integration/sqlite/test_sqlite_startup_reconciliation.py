from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from mediasync_home.adapters.final_verification import LocalFinalArtifactVerificationAdapter
from mediasync_home.adapters.sqlite.catalog_handoffs import SqliteFinalFileCatalogHandoffStore
from mediasync_home.adapters.sqlite.command_receipts import SqliteCommandReceiptStore
from mediasync_home.adapters.sqlite.connection_policy import (
    apply_sqlite_connection_policy,
    catalog_critical_writer_policy,
    recovery_writer_policy,
)
from mediasync_home.adapters.sqlite.lease_tokens import SqliteResourceLeaseStore
from mediasync_home.adapters.sqlite.migrations import apply_sqlite_migrations, catalog_migration_plan
from mediasync_home.adapters.sqlite.migrations import recovery_migration_plan
from mediasync_home.adapters.sqlite.endpoint_roots import SqliteEndpointRootResolver
from mediasync_home.adapters.sqlite.outbox import SqliteOutboxStore
from mediasync_home.adapters.sqlite.plans import SqlitePlanStore
from mediasync_home.adapters.sqlite.recovery_intents import SqliteRecoveryIntentSegmentStore
from mediasync_home.adapters.sqlite.recovery_operations import SqliteRecoveryOperationStore
from mediasync_home.adapters.sqlite.runs import SqliteRunStore
from mediasync_home.application.command_receipts import (
    COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION,
    CommandReceipt,
    CommandReceiptState,
    transition_command_receipt,
)
from mediasync_home.application.outbox import command_effect_outbox_message
from mediasync_home.application.outbox import OutboxMessageState
from mediasync_home.application.plans import (
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    SealedPlan,
    TargetPreconditionKind,
    seal_plan,
)
from mediasync_home.application.recovery_intents import durable_recovery_intent_segment
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryTargetPreconditionKind,
    planned_recovery_operation,
)
from mediasync_home.application.recovery_reconciliation import (
    RecoveryOperationStartupClassification,
)
from mediasync_home.application.recovery_resume import RecoveryResumeAction
from mediasync_home.application.runs import (
    RunIdFactory,
    RunIds,
    RunState,
    RunTargetState,
    parse_start_run_command,
    start_run_from_sealed_plan,
)
from mediasync_home.application.startup_reconciliation import (
    EngineHostStartupReconciliationRequest,
    reconcile_engine_host_after_startup,
)


def test_sqlite_engine_host_startup_reconciliation_coordinates_stores(
    tmp_path: Path,
) -> None:
    catalog_database = tmp_path / "catalog.sqlite"
    recovery_database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(catalog_database) as catalog_connection:
        recovery_connection = sqlite3.connect(recovery_database)
        try:
            _prepare_catalog(catalog_connection, catalog_database)
            _prepare_recovery(recovery_connection, recovery_database)
            receipts = SqliteCommandReceiptStore(catalog_connection)
            outbox = SqliteOutboxStore(catalog_connection)
            recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
            _register_resource_lease(recovery_connection)
            recovery_operations.record_planned_operation(
                _planned_operation(),
                process_instance_id="host-old",
            )
            early = receipts.record_received(_receipt("idempotency-early"))
            prepared = _store_prepared_receipt(receipts, "idempotency-prepared")
            message = command_effect_outbox_message(_succeeded_receipt("idempotency-effect"))
            outbox.enqueue_outbox_message(message)
            outbox.claim_next_pending(owner_instance_id="host-old", claim_token="claim-old")

            report = reconcile_engine_host_after_startup(
                EngineHostStartupReconciliationRequest(
                    reconciler_instance_id="host-new",
                    command_receipt_limit=10,
                    outbox_limit=10,
                    recovery_operation_limit=10,
                    inactive_outbox_owner_instance_ids=("host-old",),
                ),
                command_receipts=receipts,
                outbox=outbox,
                recovery_operations=recovery_operations,
            )

            loaded_early = receipts.load_command_receipt(early.idempotency_key)
            loaded_prepared = receipts.load_command_receipt(prepared.idempotency_key)
            loaded_message = outbox.load_outbox_message(message.message_id)
            assert report.command_receipts is not None
            assert report.command_receipts.scanned == 2
            assert report.command_receipts.rejected_idempotency_keys == (early.idempotency_key,)
            assert report.command_receipts.pending_effect_reconciliation_keys == (
                prepared.idempotency_key,
            )
            assert report.outbox is not None
            assert report.outbox.scanned == 1
            assert report.outbox.requeued_message_ids == (message.message_id,)
            assert report.recovery_operations is not None
            assert report.recovery_operations.scanned == 1
            assert report.recovery_operations.findings[0].operation_id == "op-a"
            assert report.recovery_operations.findings[0].classification is (
                RecoveryOperationStartupClassification.DISCARD_UNVERIFIED_INBOX
            )
            assert loaded_early is not None
            assert loaded_early.state is CommandReceiptState.REJECTED
            assert (
                loaded_early.rejection_reason
                == COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION
            )
            assert loaded_prepared is not None
            assert loaded_prepared.state is CommandReceiptState.EFFECT_PREPARED
            assert loaded_message is not None
            assert loaded_message.state is OutboxMessageState.PENDING
            assert loaded_message.claim_owner_instance_id is None
            assert loaded_message.claim_token is None
            assert loaded_message.claim_generation == 2
        finally:
            recovery_connection.close()


def test_sqlite_engine_host_startup_reconciliation_resumes_catalog_recorded_target(
    tmp_path: Path,
) -> None:
    catalog_database = tmp_path / "catalog.sqlite"
    recovery_database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(catalog_database) as catalog_connection:
        recovery_connection = sqlite3.connect(recovery_database)
        try:
            _prepare_catalog(catalog_connection, catalog_database)
            _prepare_recovery(recovery_connection, recovery_database)
            _insert_plan_parent_rows(catalog_connection)
            receipts = SqliteCommandReceiptStore(catalog_connection)
            plans = SqlitePlanStore(catalog_connection)
            runs = SqliteRunStore(catalog_connection)
            recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
            _register_resource_lease(recovery_connection)
            SqliteRecoveryIntentSegmentStore(recovery_connection).publish_intent_segment(
                _segment()
            )
            receipts.record_received(_receipt("idempotency-a"))
            plan = _sealed_plan()
            plans.save_sealed_plan(plan)
            start_run_from_sealed_plan(
                command=parse_start_run_command(
                    request_id="request-a",
                    idempotency_key="idempotency-a",
                    payload={
                        "plan_id": plan.plan_id,
                        "plan_checksum": plan.plan_checksum,
                    },
                ),
                plans=plans,
                runs=runs,
                id_factory=_FixedRunIdFactory(),
            )
            _mark_run_target_executing(runs)
            _record_catalog_recorded_operation(recovery_operations)

            report = reconcile_engine_host_after_startup(
                EngineHostStartupReconciliationRequest(
                    reconciler_instance_id="host-new",
                    recovery_operation_limit=10,
                    recovery_resume_limit=10,
                ),
                recovery_operations=recovery_operations,
                recovery_resume_operations=recovery_operations,
                runs=runs,
            )

            loaded = runs.load_started_run("run-a")
            assert report.recovery_operations is not None
            assert report.recovery_operations.scanned == 1
            assert report.recovery_operations.findings[0].classification is (
                RecoveryOperationStartupClassification.CATALOG_RECORDED_NEEDS_RUN_COMPLETION
            )
            assert report.recovery_resume is not None
            assert report.recovery_resume.scanned == 1
            assert report.recovery_resume.completed_run_target_ids == (
                "run-a-target-0000",
            )
            assert report.recovery_resume.findings[0].action is RecoveryResumeAction.TARGET_COMPLETED
            assert loaded is not None
            assert loaded.state is RunState.COMPLETED
            assert loaded.targets[0].state is RunTargetState.SUCCEEDED
            assert loaded.targets[0].completed_operations == 1
            assert loaded.targets[0].completed_bytes == 128
        finally:
            recovery_connection.close()


def test_sqlite_engine_host_startup_reconciliation_records_final_verified_handoff(
    tmp_path: Path,
) -> None:
    catalog_database = tmp_path / "catalog.sqlite"
    recovery_database = tmp_path / "recovery.sqlite"
    with sqlite3.connect(catalog_database) as catalog_connection:
        recovery_connection = sqlite3.connect(recovery_database)
        try:
            _prepare_catalog(catalog_connection, catalog_database)
            _prepare_recovery(recovery_connection, recovery_database)
            _insert_plan_parent_rows(catalog_connection)
            receipts = SqliteCommandReceiptStore(catalog_connection)
            plans = SqlitePlanStore(catalog_connection)
            runs = SqliteRunStore(catalog_connection)
            catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
            recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
            _register_resource_lease(recovery_connection)
            SqliteRecoveryIntentSegmentStore(recovery_connection).publish_intent_segment(
                _segment()
            )
            receipts.record_received(_receipt("idempotency-a"))
            plan = _sealed_plan()
            plans.save_sealed_plan(plan)
            start_run_from_sealed_plan(
                command=parse_start_run_command(
                    request_id="request-a",
                    idempotency_key="idempotency-a",
                    payload={
                        "plan_id": plan.plan_id,
                        "plan_checksum": plan.plan_checksum,
                    },
                ),
                plans=plans,
                runs=runs,
                id_factory=_FixedRunIdFactory(),
            )
            _mark_run_target_executing(runs)
            _record_final_verified_operation(recovery_operations)

            report = reconcile_engine_host_after_startup(
                EngineHostStartupReconciliationRequest(
                    reconciler_instance_id="host-new",
                    recovery_operation_limit=10,
                    recovery_resume_limit=10,
                ),
                recovery_operations=recovery_operations,
                recovery_resume_operations=recovery_operations,
                recovery_resume_catalog_handoffs=catalog_handoffs,
                runs=runs,
            )

            loaded = runs.load_started_run("run-a")
            operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
            handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
            assert report.recovery_operations is not None
            assert report.recovery_operations.scanned == 1
            assert report.recovery_operations.findings[0].classification is (
                RecoveryOperationStartupClassification.FILESYSTEM_APPLIED_NEEDS_CATALOG
            )
            assert report.recovery_resume is not None
            assert report.recovery_resume.scanned == 2
            assert tuple(finding.action for finding in report.recovery_resume.findings) == (
                RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
                RecoveryResumeAction.TARGET_COMPLETED,
            )
            assert report.recovery_resume.completed_run_target_ids == (
                "run-a-target-0000",
            )
            assert handoff is not None
            assert handoff.content_hash == "a" * 64
            assert operation is not None
            assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
            assert operation.catalog_handoff_id == "final-file:run-a:op-a"
            assert loaded is not None
            assert loaded.state is RunState.COMPLETED
            assert loaded.targets[0].state is RunTargetState.SUCCEEDED
            assert loaded.targets[0].completed_operations == 1
            assert loaded.targets[0].completed_bytes == 128
        finally:
            recovery_connection.close()


def test_sqlite_engine_host_startup_reconciliation_reverifies_filesystem_applied(
    tmp_path: Path,
) -> None:
    catalog_database = tmp_path / "catalog.sqlite"
    recovery_database = tmp_path / "recovery.sqlite"
    target_root = tmp_path / "target"
    payload = b"x" * 128
    content_hash = hashlib.sha256(payload).hexdigest()
    (target_root / "Pictures").mkdir(parents=True)
    (target_root / "Pictures" / "A.jpg").write_bytes(payload)
    with sqlite3.connect(catalog_database) as catalog_connection:
        recovery_connection = sqlite3.connect(recovery_database)
        try:
            _prepare_catalog(catalog_connection, catalog_database)
            _prepare_recovery(recovery_connection, recovery_database)
            _insert_plan_parent_rows(catalog_connection, target_root=target_root)
            receipts = SqliteCommandReceiptStore(catalog_connection)
            plans = SqlitePlanStore(catalog_connection)
            runs = SqliteRunStore(catalog_connection)
            catalog_handoffs = SqliteFinalFileCatalogHandoffStore(catalog_connection)
            recovery_operations = SqliteRecoveryOperationStore(recovery_connection)
            final_verifier = LocalFinalArtifactVerificationAdapter(
                root_resolver=SqliteEndpointRootResolver(catalog_connection),
            )
            _register_resource_lease(recovery_connection)
            SqliteRecoveryIntentSegmentStore(recovery_connection).publish_intent_segment(
                _segment()
            )
            receipts.record_received(_receipt("idempotency-a"))
            plan = _sealed_plan()
            plans.save_sealed_plan(plan)
            start_run_from_sealed_plan(
                command=parse_start_run_command(
                    request_id="request-a",
                    idempotency_key="idempotency-a",
                    payload={
                        "plan_id": plan.plan_id,
                        "plan_checksum": plan.plan_checksum,
                    },
                ),
                plans=plans,
                runs=runs,
                id_factory=_FixedRunIdFactory(),
            )
            _mark_run_target_executing(runs)
            _record_filesystem_applied_operation(
                recovery_operations,
                content_hash=content_hash,
            )

            report = reconcile_engine_host_after_startup(
                EngineHostStartupReconciliationRequest(
                    reconciler_instance_id="host-new",
                    recovery_operation_limit=10,
                    recovery_resume_limit=10,
                ),
                recovery_operations=recovery_operations,
                recovery_resume_operations=recovery_operations,
                recovery_resume_catalog_handoffs=catalog_handoffs,
                recovery_resume_final_verifier=final_verifier,
                runs=runs,
            )

            loaded = runs.load_started_run("run-a")
            operation = recovery_operations.load_operation(run_id="run-a", operation_id="op-a")
            handoff = catalog_handoffs.load_final_file_handoff("final-file:run-a:op-a")
            assert report.recovery_operations is not None
            assert report.recovery_operations.scanned == 1
            assert report.recovery_operations.findings[0].classification is (
                RecoveryOperationStartupClassification.REVERIFY_FINAL
            )
            assert report.recovery_resume is not None
            assert report.recovery_resume.scanned == 3
            assert tuple(finding.action for finding in report.recovery_resume.findings) == (
                RecoveryResumeAction.FINAL_REVERIFIED,
                RecoveryResumeAction.CATALOG_HANDOFF_RECORDED,
                RecoveryResumeAction.TARGET_COMPLETED,
            )
            assert handoff is not None
            assert handoff.content_hash == content_hash
            assert operation is not None
            assert operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
            assert operation.catalog_handoff_id == "final-file:run-a:op-a"
            assert loaded is not None
            assert loaded.state is RunState.COMPLETED
            assert loaded.targets[0].state is RunTargetState.SUCCEEDED
            assert loaded.targets[0].completed_operations == 1
            assert loaded.targets[0].completed_bytes == 128
        finally:
            recovery_connection.close()


def _prepare_catalog(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, catalog_critical_writer_policy(database))
    apply_sqlite_migrations(connection, catalog_migration_plan())


def _prepare_recovery(connection: sqlite3.Connection, database: Path) -> None:
    apply_sqlite_connection_policy(connection, recovery_writer_policy(database))
    apply_sqlite_migrations(connection, recovery_migration_plan())


def _register_resource_lease(connection: sqlite3.Connection) -> None:
    assert SqliteResourceLeaseStore(connection).register_acquired_resource_lease(
        lease_id="lease-a",
        resource_key="endpoint:target-a",
        owner_instance_id="owner-a",
        ownership_epoch=1,
        run_id="run-a",
        run_target_id="run-a-target-0000",
        endpoint_id="target-a",
        endpoint_generation=None,
        lease_mode="EXCLUSIVE",
        os_lock_kind="LOCAL_OS_HANDLE",
    ) == 1


def _planned_operation():
    return planned_recovery_operation(
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
    )


class _FixedRunIdFactory(RunIdFactory):
    def new_run_ids(self) -> RunIds:
        return RunIds(run_id="run-a", logical_run_group_id="run-group-a")


def _insert_plan_parent_rows(connection: sqlite3.Connection, *, target_root: Path | None = None) -> None:
    root_uri = "file:///E:/Backup" if target_root is None else target_root.as_uri()
    connection.execute("INSERT INTO jobs (id, kind) VALUES ('job-a', 'multi_target_backup')")
    connection.execute("INSERT INTO filter_sets (job_id, id) VALUES ('job-a', 'filter-a')")
    connection.execute(
        """
        INSERT INTO job_revisions (job_id, id, filter_set_id)
            VALUES ('job-a', 'job-rev-a', 'filter-a')
        """
    )
    connection.execute("INSERT INTO job_heads (job_id, active_revision_id) VALUES ('job-a', 'job-rev-a')")
    connection.execute(
        """
        INSERT INTO analyses (id, job_id, job_revision_id)
            VALUES ('analysis-a', 'job-a', 'job-rev-a')
        """
    )
    connection.execute("INSERT INTO endpoints (id) VALUES ('target-a')")
    connection.execute(
        """
        INSERT INTO endpoint_revisions (endpoint_id, id, display_name, root_uri)
            VALUES ('target-a', 'target-rev-a', 'USB', ?)
        """,
        (root_uri,),
    )
    connection.execute(
        """
        INSERT INTO analysis_targets (analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.execute(
        """
        INSERT INTO snapshots (id, analysis_id, endpoint_id, endpoint_revision_id)
            VALUES ('target-snapshot-a', 'analysis-a', 'target-a', 'target-rev-a')
        """
    )
    connection.commit()


def _sealed_plan() -> SealedPlan:
    return seal_plan(
        plan_id="plan-a",
        analysis_id="analysis-a",
        job_id="job-a",
        job_revision_id="job-rev-a",
        endpoints=(_target_endpoint(),),
        operations=(
            PlanOperation(
                operation_id="op-a",
                operation_type=PlanOperationType.COPY_NEW,
                sequence_no=10,
                execution_phase=20,
                stable_order_key="020:Pictures/A.jpg",
                target_precondition_kind=TargetPreconditionKind.ABSENT,
                target_relative_path="Pictures/A.jpg",
                planned_bytes=128,
                reason_code="COPY_NEW",
                risk_level=PlanRiskLevel.LOW,
            ),
        ),
    )


def _target_endpoint() -> PlanEndpoint:
    return PlanEndpoint(
        endpoint_id="target-a",
        endpoint_revision_id="target-rev-a",
        snapshot_id="target-snapshot-a",
        role=PlanEndpointRole.TARGET_WRITABLE,
        target_ordinal=0,
        capabilities_hash="capabilities-a",
        root_case_context_hash="case-a",
        required_owner_installation_id="owner-a",
        required_ownership_epoch=1,
        control_schema_version=1,
        planned_operations=1,
        planned_bytes=128,
    )


def _mark_run_target_executing(runs: SqliteRunStore) -> None:
    assert runs.begin_run_target_preflight(
        run_id="run-a",
        run_target_id="run-a-target-0000",
    ) is not None
    assert runs.record_run_target_lease_acquired(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease_id="lease-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
    ) is not None
    assert runs.record_run_target_execution_started(
        run_id="run-a",
        run_target_id="run-a-target-0000",
        lease_id="lease-a",
        owner_installation_id="owner-a",
        ownership_epoch=1,
        fencing_token=1,
    ) is not None


def _record_catalog_recorded_operation(store: SqliteRecoveryOperationStore) -> RecoveryOperation:
    operation = _record_final_verified_operation(store)
    updated = store.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=RecoveryOperationPhase.CATALOG_RECORDED,
        process_instance_id="host-a",
        catalog_handoff_id="final-file:run-a:op-a",
    )
    assert updated is not None
    return updated


def _record_final_verified_operation(store: SqliteRecoveryOperationStore) -> RecoveryOperation:
    operation = _record_filesystem_applied_operation(store)
    for next_phase in (
        RecoveryOperationPhase.FINAL_DURABLE,
        RecoveryOperationPhase.FINAL_VERIFIED,
    ):
        updated = store.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=next_phase,
            process_instance_id="host-a",
        )
        assert updated is not None
        operation = updated
    return operation


def _record_filesystem_applied_operation(
    store: SqliteRecoveryOperationStore,
    *,
    content_hash: str = "a" * 64,
) -> RecoveryOperation:
    operation = store.record_planned_operation(
        _catalog_resume_operation(content_hash=content_hash),
        process_instance_id="host-a",
    )
    for next_phase in (
        RecoveryOperationPhase.SOURCE_VALIDATED,
        RecoveryOperationPhase.SOURCE_STABILITY_BOUND,
        RecoveryOperationPhase.TARGET_PRECONDITION_VALIDATED,
        RecoveryOperationPhase.STAGING_ALLOCATED,
        RecoveryOperationPhase.TRANSFERRED,
        RecoveryOperationPhase.STAGING_DURABLE,
        RecoveryOperationPhase.STAGING_VERIFIED,
    ):
        updated = store.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=next_phase,
            process_instance_id="host-a",
        )
        assert updated is not None
        operation = updated
    updated = store.record_operation_phase_transition(
        run_id=operation.run_id,
        operation_id=operation.operation_id,
        expected_phase=operation.phase,
        next_phase=RecoveryOperationPhase.COMMIT_INTENT_RECORDED,
        process_instance_id="host-a",
        intent_segment_id="segment-a",
        intent_ordinal=0,
    )
    assert updated is not None
    operation = updated
    for next_phase in (
        RecoveryOperationPhase.COMMIT_PRECONDITIONS_REVALIDATED,
        RecoveryOperationPhase.FILESYSTEM_APPLIED,
    ):
        updated = store.record_operation_phase_transition(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            expected_phase=operation.phase,
            next_phase=next_phase,
            process_instance_id="host-a",
        )
        assert updated is not None
        operation = updated
    return operation


def _catalog_resume_operation(*, content_hash: str = "a" * 64) -> RecoveryOperation:
    return replace(
        _planned_operation(),
        staging_object_id="op-a",
        expected_final_fingerprint_json=json.dumps(
            {"byte_count": 128, "content_hash": content_hash},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _segment():
    return durable_recovery_intent_segment(
        segment_id="segment-a",
        run_id="run-a",
        run_target_id="run-a-target-0000",
        target_endpoint_id="target-a",
        target_endpoint_revision_id="target-rev-a",
        endpoint_generation=1,
        owner_installation_id="owner-a",
        ownership_epoch=1,
        lease_id="lease-a",
        fencing_token=1,
        segment_sequence=0,
        relative_path="installations/owner-a/recovery/run-a/segment-000000.intent.jsonl",
        schema_version=1,
        operation_count=1,
        byte_count=128,
        segment_hash="b" * 64,
    )


def _receipt(idempotency_key: str) -> CommandReceipt:
    return CommandReceipt(
        request_id=f"request-{idempotency_key}",
        client_instance_id="client-a",
        principal_fingerprint="principal-a",
        idempotency_key=idempotency_key,
        command_name="create_standard_backup_job",
        payload_hash="a" * 64,
        protocol_version=1,
        schema_version=1,
        expected_entity_revision=7,
    )


def _store_prepared_receipt(
    store: SqliteCommandReceiptStore,
    idempotency_key: str,
) -> CommandReceipt:
    receipt = store.record_received(_receipt(idempotency_key))
    validated = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
    prepared = transition_command_receipt(
        validated,
        CommandReceiptState.EFFECT_PREPARED,
        result_entity_type="standard_backup_job",
        result_entity_id="job-a",
    )
    store.update_command_receipt(prepared)
    return prepared


def _succeeded_receipt(idempotency_key: str) -> CommandReceipt:
    return replace(
        _receipt(idempotency_key),
        state=CommandReceiptState.SUCCEEDED,
        result_entity_type="standard_backup_job",
        result_entity_id="job-a",
    )
