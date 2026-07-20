from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from mediasync_home.application.activity_read_models import (
    ActivityOverviewQueryError,
    RunActivityReadModelStore,
    query_activity_overview,
)
from mediasync_home.application.catalog_read_models import (
    CatalogedFileReadModelStore,
    CatalogedFilesQueryError,
    query_cataloged_files,
)
from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptConflict,
    CommandEffectTransaction,
    CommandReceiptState,
    CommandReceiptStore,
    transition_command_receipt,
)
from mediasync_home.application.job_creation import (
    CreateStandardBackupJobCommand,
    JobCreationOutcome,
    JobCreationCommandName,
    JobCreationPayloadError,
    SealedStandardBackupJob,
    StandardBackupJobCatalog,
    StandardBackupJobIdFactory,
    create_standard_backup_job_from_draft,
    evaluate_standard_backup_job_creation,
    parse_create_standard_backup_job_command,
)
from mediasync_home.application.job_drafts import JobDraftStore
from mediasync_home.application.job_read_models import (
    BackupJobDetailQueryError,
    BackupOverviewQueryError,
    StandardBackupJobDetailReadModelStore,
    StandardBackupJobReadModelStore,
    query_backup_job_detail,
    query_backup_overview,
)
from mediasync_home.application.outbox import OutboxStore, command_effect_outbox_message
from mediasync_home.application.plan_read_models import (
    PlanEndpointsQueryError,
    PlanOperationsQueryError,
    query_plan_endpoints,
    query_plan_operations,
)
from mediasync_home.application.plans import (
    PlanEndpointReadModelStore,
    PlanOperationReadModelStore,
    PlanStore,
)
from mediasync_home.application.runtime_status import RuntimeStatus, startup_status
from mediasync_home.application.schedules import ScheduleStore
from mediasync_home.application.runs import (
    RunCommandName,
    RunIdFactory,
    RunStartOutcome,
    RunStartViolation,
    RunStore,
    StartRunCommand,
    StartedRun,
    evaluate_start_run,
    parse_start_run_command,
    start_run_from_sealed_plan,
)
from mediasync_home.application.snapshot_read_models import (
    SnapshotCoverageQueryError,
    SnapshotEntriesQueryError,
    SnapshotIssuesQueryError,
    query_snapshot_coverage,
    query_snapshot_entries,
    query_snapshot_issues,
)
from mediasync_home.application.snapshots import (
    SnapshotCoverageReadModelStore,
    SnapshotEntryReadModelStore,
    SnapshotIssueReadModelStore,
)
from mediasync_home.application.trigger_occurrences import (
    TriggerCommandName,
    TriggerOccurrenceStore,
    TriggerOccurrencePayloadError,
    parse_enqueue_trigger_occurrence_command,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client_identity import ClientAuthorizationPolicy, VerifiedClientIdentity
from mediasync_home.ipc.protocol import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    HandshakeRequest,
    IpcCommandEnvelope,
    IpcProtocolError,
    IpcReason,
    IpcResponse,
)


@dataclass
class EngineHostIpcService:
    authorization: ClientAuthorizationPolicy
    status: RuntimeStatus = field(default_factory=lambda: startup_status(ProcessRole.ENGINE_HOST))
    job_draft_store: JobDraftStore | None = None
    standard_backup_job_catalog: StandardBackupJobCatalog | None = None
    standard_backup_job_read_store: StandardBackupJobReadModelStore | None = None
    standard_backup_job_detail_store: StandardBackupJobDetailReadModelStore | None = None
    run_activity_read_store: RunActivityReadModelStore | None = None
    plan_operation_read_store: PlanOperationReadModelStore | None = None
    plan_endpoint_read_store: PlanEndpointReadModelStore | None = None
    snapshot_entry_read_store: SnapshotEntryReadModelStore | None = None
    snapshot_coverage_read_store: SnapshotCoverageReadModelStore | None = None
    snapshot_issue_read_store: SnapshotIssueReadModelStore | None = None
    cataloged_file_read_store: CatalogedFileReadModelStore | None = None
    standard_backup_job_id_factory: StandardBackupJobIdFactory | None = None
    plan_store: PlanStore | None = None
    run_store: RunStore | None = None
    run_id_factory: RunIdFactory | None = None
    schedule_store: ScheduleStore | None = None
    trigger_occurrence_store: TriggerOccurrenceStore | None = None
    command_receipt_store: CommandReceiptStore | None = None
    command_effect_transaction: CommandEffectTransaction | None = None
    outbox_store: OutboxStore | None = None
    _accepted_clients: dict[str, VerifiedClientIdentity] = field(default_factory=dict)

    def handshake(
        self,
        payload: dict[str, Any],
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            request = HandshakeRequest.from_dict(payload)
        except (IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if request.protocol_version != PROTOCOL_VERSION:
            return IpcResponse.rejected(IpcReason.PROTOCOL_MISMATCH)
        if request.schema_version != SCHEMA_VERSION:
            return IpcResponse.rejected(IpcReason.SCHEMA_MISMATCH)
        reject_reason = self.authorization.reject_reason(request.role, identity)
        if reject_reason is not None:
            return IpcResponse.rejected(reject_reason)

        self._accepted_clients[request.client_instance_id] = identity
        return IpcResponse.accepted(
            {
                "server_nonce": str(uuid4()),
                "verified_user_sid_hash": identity.user_sid_hash,
                "host_status": self.status.to_dict(),
            }
        )

    def query_status(self, client_instance_id: str) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        return IpcResponse.accepted({"host_status": self.status.to_dict()})

    def query_backup_overview(
        self,
        client_instance_id: str,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            overview = query_backup_overview(
                job_read_store=self.standard_backup_job_read_store,
                draft_store=self.job_draft_store,
                draft_id=draft_id,
                limit=limit,
                offset=offset,
            )
        except BackupOverviewQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"backup_overview": overview.to_dict()})

    def query_backup_job_detail(
        self,
        client_instance_id: str,
        *,
        job_id: str,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            detail = query_backup_job_detail(
                job_detail_store=self.standard_backup_job_detail_store,
                job_id=job_id,
            )
        except BackupJobDetailQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"backup_job_detail": detail.to_dict()})

    def query_activity_overview(
        self,
        client_instance_id: str,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            overview = query_activity_overview(
                run_read_store=self.run_activity_read_store,
                job_id=job_id,
                limit=limit,
                offset=offset,
            )
        except ActivityOverviewQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"activity_overview": overview.to_dict()})

    def query_plan_operations(
        self,
        client_instance_id: str,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            page = query_plan_operations(
                plan_read_store=self.plan_operation_read_store,
                plan_id=plan_id,
                limit=limit,
                after=after,
            )
        except PlanOperationsQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"plan_operations": page.to_dict()})

    def query_plan_endpoints(
        self,
        client_instance_id: str,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            page = query_plan_endpoints(
                plan_read_store=self.plan_endpoint_read_store,
                plan_id=plan_id,
                limit=limit,
                after=after,
            )
        except PlanEndpointsQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"plan_endpoints": page.to_dict()})

    def query_snapshot_entries(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            page = query_snapshot_entries(
                snapshot_read_store=self.snapshot_entry_read_store,
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
            )
        except SnapshotEntriesQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"snapshot_entries": page.to_dict()})

    def query_snapshot_coverage(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            page = query_snapshot_coverage(
                snapshot_coverage_store=self.snapshot_coverage_read_store,
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                coverage_states=coverage_states,
            )
        except SnapshotCoverageQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"snapshot_coverage": page.to_dict()})

    def query_snapshot_issues(
        self,
        client_instance_id: str,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            page = query_snapshot_issues(
                snapshot_issue_store=self.snapshot_issue_read_store,
                snapshot_id=snapshot_id,
                limit=limit,
                after=after,
                blocking_only=blocking_only,
            )
        except SnapshotIssuesQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"snapshot_issues": page.to_dict()})

    def query_cataloged_files(
        self,
        client_instance_id: str,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        try:
            page = query_cataloged_files(
                cataloged_file_read_store=self.cataloged_file_read_store,
                run_id=run_id,
                target_endpoint_id=target_endpoint_id,
                limit=limit,
                offset=offset,
            )
        except CatalogedFilesQueryError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        return IpcResponse.accepted({"cataloged_files": page.to_dict()})

    def submit_command(self, client_instance_id: str, command_name: str) -> IpcResponse:
        del command_name
        if client_instance_id not in self._accepted_clients:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED)

    def submit_command_envelope(self, payload: dict[str, Any]) -> IpcResponse:
        try:
            command = IpcCommandEnvelope.from_dict(payload)
        except (IpcProtocolError, TypeError, ValueError):
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)
        if command.protocol_version != PROTOCOL_VERSION:
            return IpcResponse.rejected(IpcReason.PROTOCOL_MISMATCH)
        if command.schema_version != SCHEMA_VERSION:
            return IpcResponse.rejected(IpcReason.SCHEMA_MISMATCH)
        identity = self._accepted_clients.get(command.client_instance_id)
        if identity is None:
            return IpcResponse.rejected(IpcReason.HANDSHAKE_REQUIRED)
        if command.command_name == JobCreationCommandName.CREATE_STANDARD_BACKUP_JOB.value:
            return self._handle_create_standard_backup_job(command, identity)
        if command.command_name == RunCommandName.START_RUN.value:
            return self._handle_start_run(command, identity)
        if command.command_name == TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value:
            return self._handle_enqueue_trigger_occurrence(command, identity)
        receipt_response = self._record_terminal_rejected_receipt(
            command,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {"command_name": command.command_name, "recognized": False}
        self._add_receipt_payload(response_payload, command.idempotency_key)
        return IpcResponse.rejected(
            IpcReason.MUTATING_COMMANDS_DISABLED,
            response_payload,
        )

    def _handle_enqueue_trigger_occurrence(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_enqueue_trigger_occurrence_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except TriggerOccurrencePayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            receipt_response = self._record_terminal_rejected_receipt(
                envelope,
                identity,
                rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
            )
            if receipt_response is not None:
                return receipt_response
            response_payload = command.response_payload(mutations_enabled=True)
            self._add_receipt_payload(response_payload, envelope.idempotency_key)
            return IpcResponse.rejected(
                IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED,
                response_payload,
            )

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload = command.response_payload(mutations_enabled=False)
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, response_payload)

    def _handle_create_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_create_standard_backup_job_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except JobCreationPayloadError:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._dispatch_create_standard_backup_job(envelope, identity, command)

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "draft_id": command.draft_id,
            "recognized": True,
            "mutations_enabled": self.status.mutations_enabled,
        }
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        if self.job_draft_store is not None:
            response_payload["readiness"] = evaluate_standard_backup_job_creation(
                command=command,
                drafts=self.job_draft_store,
            ).to_dict()
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, response_payload)

    def _handle_start_run(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> IpcResponse:
        try:
            command = parse_start_run_command(
                request_id=envelope.request_id,
                idempotency_key=envelope.idempotency_key,
                payload=envelope.payload,
            )
        except RunStartViolation:
            return IpcResponse.rejected(IpcReason.INVALID_FRAME)

        if self.status.mutations_enabled:
            return self._dispatch_start_run(envelope, identity, command)

        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.MUTATING_COMMANDS_DISABLED.value,
        )
        if receipt_response is not None:
            return receipt_response
        response_payload: dict[str, Any] = {
            "command_name": envelope.command_name,
            "plan_id": command.plan_id,
            "recognized": True,
            "mutations_enabled": self.status.mutations_enabled,
        }
        self._add_receipt_payload(response_payload, envelope.idempotency_key)
        if self.plan_store is not None:
            response_payload["readiness"] = evaluate_start_run(
                command=command,
                plans=self.plan_store,
            ).to_dict()
        return IpcResponse.rejected(IpcReason.MUTATING_COMMANDS_DISABLED, response_payload)

    def _dispatch_start_run(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartRunCommand,
    ) -> IpcResponse:
        return self._run_command_effect_transaction(
            lambda: self._dispatch_start_run_in_transaction(envelope, identity, command)
        )

    def _dispatch_start_run_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartRunCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.plan_store is None
            or self.run_store is None
            or self.run_id_factory is None
        ):
            return self._reject_config_missing_start_run(envelope, identity, command)

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)

        replay = self._start_run_replay_response(envelope, command, receipt)
        if replay is not None:
            return replay

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = start_run_from_sealed_plan(
            command=command,
            plans=self.plan_store,
            runs=self.run_store,
            id_factory=self.run_id_factory,
        )
        if outcome.run is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _start_run_response_payload(
                envelope=envelope,
                plan_id=command.plan_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="run",
            result_entity_id=outcome.run.run_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)

        payload = _start_run_response_payload(
            envelope=envelope,
            plan_id=command.plan_id,
            mutations_enabled=True,
            recognized=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_start_run(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: StartRunCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = {
            "command_name": envelope.command_name,
            "plan_id": command.plan_id,
            "recognized": True,
            "mutations_enabled": True,
        }
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload)

    def _start_run_replay_response(
        self,
        envelope: IpcCommandEnvelope,
        command: StartRunCommand,
        receipt: CommandReceipt,
    ) -> IpcResponse | None:
        if receipt.state is CommandReceiptState.RECEIVED:
            return None
        if self.run_store is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        run = self.run_store.load_started_run_by_idempotency_key(envelope.idempotency_key)
        payload = _start_run_response_payload(
            envelope=envelope,
            plan_id=command.plan_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            run=run,
        )
        payload["created"] = False
        payload["idempotent_replay"] = True
        self._add_receipt_payload(payload, envelope.idempotency_key)
        if receipt.state is CommandReceiptState.SUCCEEDED:
            return IpcResponse.accepted(payload)
        if receipt.state is CommandReceiptState.REJECTED:
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
        return IpcResponse.accepted(payload)

    def _dispatch_create_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CreateStandardBackupJobCommand,
    ) -> IpcResponse:
        return self._run_command_effect_transaction(
            lambda: self._dispatch_create_standard_backup_job_in_transaction(envelope, identity, command)
        )

    def _dispatch_create_standard_backup_job_in_transaction(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CreateStandardBackupJobCommand,
    ) -> IpcResponse:
        if (
            self.command_receipt_store is None
            or self.job_draft_store is None
            or self.standard_backup_job_catalog is None
            or self.standard_backup_job_id_factory is None
        ):
            return self._reject_config_missing_standard_backup_job(envelope, identity, command)

        receipt, conflict_response = self._record_received_receipt(envelope, identity)
        if conflict_response is not None:
            return conflict_response
        if receipt is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)

        replay = self._standard_backup_job_replay_response(envelope, command, receipt)
        if replay is not None:
            return replay

        receipt = transition_command_receipt(receipt, CommandReceiptState.VALIDATED)
        self.command_receipt_store.update_command_receipt(receipt)
        outcome = create_standard_backup_job_from_draft(
            command=command,
            drafts=self.job_draft_store,
            catalog=self.standard_backup_job_catalog,
            id_factory=self.standard_backup_job_id_factory,
        )
        if outcome.job is None:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=IpcReason.COMMAND_PRECONDITION_FAILED.value,
            )
            self.command_receipt_store.update_command_receipt(receipt)
            payload = _create_standard_backup_job_response_payload(
                envelope=envelope,
                draft_id=command.draft_id,
                mutations_enabled=True,
                recognized=True,
                outcome=outcome,
            )
            self._add_receipt_payload(payload, envelope.idempotency_key)
            return IpcResponse.rejected(IpcReason.COMMAND_PRECONDITION_FAILED, payload)

        receipt = transition_command_receipt(
            receipt,
            CommandReceiptState.EFFECT_PREPARED,
            result_entity_type="standard_backup_job",
            result_entity_id=outcome.job.job_id,
        )
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
        self.command_receipt_store.update_command_receipt(receipt)
        receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
        self.command_receipt_store.update_command_receipt(receipt)
        self._enqueue_command_effect_outbox(receipt)

        payload = _create_standard_backup_job_response_payload(
            envelope=envelope,
            draft_id=command.draft_id,
            mutations_enabled=True,
            recognized=True,
            outcome=outcome,
        )
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.accepted(payload)

    def _reject_config_missing_standard_backup_job(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        command: CreateStandardBackupJobCommand,
    ) -> IpcResponse:
        receipt_response = self._record_terminal_rejected_receipt(
            envelope,
            identity,
            rejection_reason=IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED.value,
        )
        if receipt_response is not None:
            return receipt_response
        payload = {
            "command_name": envelope.command_name,
            "draft_id": command.draft_id,
            "recognized": True,
            "mutations_enabled": True,
        }
        self._add_receipt_payload(payload, envelope.idempotency_key)
        return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED, payload)

    def _standard_backup_job_replay_response(
        self,
        envelope: IpcCommandEnvelope,
        command: CreateStandardBackupJobCommand,
        receipt: CommandReceipt,
    ) -> IpcResponse | None:
        if receipt.state is CommandReceiptState.RECEIVED:
            return None
        if self.standard_backup_job_catalog is None:
            return IpcResponse.rejected(IpcReason.COMMAND_DISPATCHER_NOT_CONFIGURED)
        job = self.standard_backup_job_catalog.load_standard_backup_job_by_idempotency_key(
            envelope.idempotency_key
        )
        payload = _create_standard_backup_job_response_payload(
            envelope=envelope,
            draft_id=command.draft_id,
            mutations_enabled=True,
            recognized=True,
            outcome=None,
            job=job,
        )
        payload["created"] = False
        payload["idempotent_replay"] = True
        self._add_receipt_payload(payload, envelope.idempotency_key)
        if receipt.state is CommandReceiptState.SUCCEEDED:
            return IpcResponse.accepted(payload)
        if receipt.state is CommandReceiptState.REJECTED:
            return IpcResponse.rejected(_receipt_rejection_reason(receipt), payload)
        return IpcResponse.accepted(payload)

    def _record_received_receipt(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
    ) -> tuple[CommandReceipt | None, IpcResponse | None]:
        if self.command_receipt_store is None:
            return None, None
        incoming = _receipt_from_envelope(envelope, identity)
        try:
            return self.command_receipt_store.record_received(incoming), None
        except CommandReceiptConflict as exc:
            return None, IpcResponse.rejected(
                IpcReason.COMMAND_IDEMPOTENCY_CONFLICT,
                {
                    "conflict": str(exc),
                    "idempotency_key": envelope.idempotency_key,
                },
            )

    def _record_terminal_rejected_receipt(
        self,
        envelope: IpcCommandEnvelope,
        identity: VerifiedClientIdentity,
        *,
        rejection_reason: str,
    ) -> IpcResponse | None:
        if self.command_receipt_store is None:
            return None
        incoming = _receipt_from_envelope(envelope, identity)
        try:
            receipt = self.command_receipt_store.record_received(incoming)
        except CommandReceiptConflict as exc:
            return IpcResponse.rejected(
                IpcReason.COMMAND_IDEMPOTENCY_CONFLICT,
                {
                    "conflict": str(exc),
                    "idempotency_key": envelope.idempotency_key,
                },
            )
        if receipt.state is CommandReceiptState.RECEIVED:
            receipt = transition_command_receipt(
                receipt,
                CommandReceiptState.REJECTED,
                rejection_reason=rejection_reason,
            )
            self.command_receipt_store.update_command_receipt(receipt)
        return None

    def _add_receipt_payload(self, payload: dict[str, Any], idempotency_key: str) -> None:
        if self.command_receipt_store is None:
            return
        receipt = self.command_receipt_store.load_command_receipt(idempotency_key)
        if receipt is not None:
            payload["receipt"] = _receipt_payload(receipt)

    def _run_command_effect_transaction(self, work: Callable[[], IpcResponse]) -> IpcResponse:
        if self.command_effect_transaction is None:
            return work()
        return self.command_effect_transaction.run(work)

    def _enqueue_command_effect_outbox(self, receipt: CommandReceipt) -> None:
        if self.outbox_store is None:
            return
        self.outbox_store.enqueue_outbox_message(command_effect_outbox_message(receipt))


def _receipt_from_envelope(
    envelope: IpcCommandEnvelope,
    identity: VerifiedClientIdentity,
) -> CommandReceipt:
    return CommandReceipt(
        request_id=envelope.request_id,
        client_instance_id=envelope.client_instance_id,
        principal_fingerprint=identity.user_sid_hash,
        idempotency_key=envelope.idempotency_key,
        command_name=envelope.command_name,
        payload_hash=envelope.payload_hash,
        protocol_version=envelope.protocol_version,
        schema_version=envelope.schema_version,
        expected_entity_revision=envelope.expected_entity_revision,
        payload_hash_scope=envelope.payload_hash_scope,
        payload_canonicalization_algorithm=envelope.payload_canonicalization_algorithm,
        payload_hash_algorithm=envelope.payload_hash_algorithm,
    )


def _receipt_payload(receipt: CommandReceipt) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": receipt.request_id,
        "idempotency_key": receipt.idempotency_key,
        "command_name": receipt.command_name,
        "state": receipt.state.value,
    }
    if receipt.result_entity_type is not None:
        payload["result_entity_type"] = receipt.result_entity_type
    if receipt.result_entity_id is not None:
        payload["result_entity_id"] = receipt.result_entity_id
    if receipt.rejection_reason is not None:
        payload["rejection_reason"] = receipt.rejection_reason
    return payload


def _create_standard_backup_job_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    draft_id: str,
    mutations_enabled: bool,
    recognized: bool,
    outcome: JobCreationOutcome | None,
    job: SealedStandardBackupJob | None = None,
) -> dict[str, Any]:
    result = {
        "command_name": envelope.command_name,
        "draft_id": draft_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        result["created"] = outcome.created
        result["idempotent_replay"] = outcome.idempotent_replay
        result["readiness"] = outcome.readiness.to_dict()
        job = outcome.job
    if job is not None:
        result["job"] = {
            "job_id": job.job_id,
            "job_revision_id": job.job_revision_id,
            "filter_set_id": job.filter_set_id,
        }
    return result


def _start_run_response_payload(
    *,
    envelope: IpcCommandEnvelope,
    plan_id: str,
    mutations_enabled: bool,
    recognized: bool,
    outcome: RunStartOutcome | None,
    run: StartedRun | None = None,
) -> dict[str, Any]:
    result = {
        "command_name": envelope.command_name,
        "plan_id": plan_id,
        "recognized": recognized,
        "mutations_enabled": mutations_enabled,
    }
    if outcome is not None:
        result["created"] = outcome.created
        result["idempotent_replay"] = outcome.idempotent_replay
        result["readiness"] = outcome.readiness.to_dict()
        run = outcome.run
    if run is not None:
        result["run"] = {
            "run_id": run.run_id,
            "job_id": run.job_id,
            "job_revision_id": run.job_revision_id,
            "plan_id": run.plan_id,
            "state": run.state.value,
            "plan_checksum": run.plan_checksum,
            "planned_operations": run.planned_operations,
            "planned_bytes": run.planned_bytes,
        }
    return result


def _receipt_rejection_reason(receipt: CommandReceipt) -> IpcReason:
    if receipt.rejection_reason is None:
        return IpcReason.COMMAND_PRECONDITION_FAILED
    try:
        return IpcReason(receipt.rejection_reason)
    except ValueError:
        return IpcReason.COMMAND_PRECONDITION_FAILED
