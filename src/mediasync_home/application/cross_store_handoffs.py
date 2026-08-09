from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Mapping, Protocol

from mediasync_home.application.command_receipts import (
    CommandEffectTransaction,
    CommandReceiptState,
    CommandReceiptStore,
    transition_command_receipt,
)
from mediasync_home.application.catalog_handoff import (
    CatalogHandoffError,
    CatalogHandoffOutcome,
    FinalFileCatalogHandoff,
    FinalFileCatalogHandoffStore,
    RetainedVersionCatalogHandoff,
    catalog_handoff_transition_payload,
    validate_final_file_catalog_handoff,
)
from mediasync_home.application.outbox import OutboxStore, command_effect_outbox_message
from mediasync_home.application.runs import RunState, StartedRun
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.generated.contract_types import CrossStoreHandoffState


HANDOFF_PAYLOAD_SCHEMA_VERSION = 1
HANDOFF_HASH_ALGORITHM = "SHA-256"
RUN_START_HANDOFF_TYPE = "RUN_START"
CATALOG_TO_RECOVERY = "CATALOG_TO_RECOVERY"
RECOVERY_TO_CATALOG = "RECOVERY_TO_CATALOG"
OPERATION_CATALOG_HANDOFF_TYPE = "OPERATION_CATALOG_RECORD"


class CrossStoreHandoffError(RuntimeError):
    def __init__(self, validation_code: str, next_action: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action


class CrossStoreHandoffRole(str, Enum):
    SOURCE = "SOURCE"
    PEER = "PEER"


@dataclass(frozen=True, slots=True)
class CrossStoreHandoff:
    handoff_id: str
    handoff_type: str
    direction: str
    payload_schema_version: int
    entity_type: str
    entity_id: str
    payload_json: str
    payload_hash: str
    state: CrossStoreHandoffState
    expected_peer_state: CrossStoreHandoffState
    attempt_count: int = 0
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryRunBinding:
    run_id: str
    job_id: str
    job_revision_id: str
    plan_id: str
    plan_checksum: str
    start_handoff_id: str
    state: str = "BOUND"


@dataclass(frozen=True, slots=True)
class CrossStoreHandoffReconciliationReport:
    scanned: int
    completed_handoff_ids: tuple[str, ...]
    ambiguous_handoff_ids: tuple[str, ...]

    @property
    def should_block_mutating_readiness(self) -> bool:
        return bool(self.ambiguous_handoff_ids)


class CrossStoreHandoffStore(Protocol):
    def record_handoff(self, handoff: CrossStoreHandoff) -> CrossStoreHandoff: ...

    def load_handoff(self, handoff_id: str) -> CrossStoreHandoff | None: ...

    def transition_handoff(
        self,
        *,
        handoff_id: str,
        expected_state: CrossStoreHandoffState,
        next_state: CrossStoreHandoffState,
        expected_payload_hash: str,
        last_error_code: str | None = None,
    ) -> CrossStoreHandoff | None: ...

    def list_handoffs_for_reconciliation(
        self,
        *,
        handoff_type: str,
        direction: str,
        limit: int,
    ) -> tuple[CrossStoreHandoff, ...]: ...


class RecoveryRunStartPeerStore(Protocol):
    def commit_run_start_peer(
        self,
        *,
        binding: RecoveryRunBinding,
        handoff: CrossStoreHandoff,
    ) -> CrossStoreHandoff: ...

    def load_run_binding(self, run_id: str) -> RecoveryRunBinding | None: ...


class RunStartReleaseStore(Protocol):
    def load_started_run(self, run_id: str) -> StartedRun | None: ...

    def release_created_run(self, run_id: str) -> StartedRun | None: ...


class RunStartCrossStoreCoordinator(Protocol):
    def prepare_run_start(
        self,
        run: StartedRun,
        *,
        transition_command_receipt: bool = True,
    ) -> CrossStoreHandoff: ...

    def advance_run_start(self, run_id: str) -> CrossStoreHandoff: ...


class OperationCatalogCrossStoreCoordinator(Protocol):
    def record_operation_catalog_handoff(
        self,
        *,
        operation: RecoveryOperation,
        handoff: FinalFileCatalogHandoff,
        process_instance_id: str,
    ) -> CatalogHandoffOutcome: ...


class CrossStoreRunStartCoordinator(RunStartCrossStoreCoordinator):
    def __init__(
        self,
        *,
        catalog_handoffs: CrossStoreHandoffStore,
        recovery_handoffs: CrossStoreHandoffStore,
        recovery_runs: RecoveryRunStartPeerStore,
        catalog_runs: RunStartReleaseStore,
        command_receipts: CommandReceiptStore,
        catalog_transaction: CommandEffectTransaction,
        outbox: OutboxStore | None = None,
    ) -> None:
        self._catalog_handoffs = catalog_handoffs
        self._recovery_handoffs = recovery_handoffs
        self._recovery_runs = recovery_runs
        self._catalog_runs = catalog_runs
        self._command_receipts = command_receipts
        self._catalog_transaction = catalog_transaction
        self._outbox = outbox

    def prepare_run_start(
        self,
        run: StartedRun,
        *,
        transition_command_receipt: bool = True,
    ) -> CrossStoreHandoff:
        if run.state is not RunState.CREATED:
            raise CrossStoreHandoffError(
                "RUN_START_HANDOFF_REQUIRES_CREATED_RUN",
                "Persist the run as non-runnable before preparing recovery handoff.",
            )
        return self._catalog_handoffs.record_handoff(
            run_start_source_handoff(
                run,
                transition_command_receipt=transition_command_receipt,
            )
        )

    def commit_recovery_peer(self, run_id: str) -> CrossStoreHandoff:
        source = self._load_source(run_id)
        _assert_source_run_start_handoff(source)
        peer = replace(
            source,
            state=CrossStoreHandoffState.PEER_COMMITTED,
            expected_peer_state=CrossStoreHandoffState.SOURCE_CONFIRMED,
        )
        return self._recovery_runs.commit_run_start_peer(
            binding=recovery_run_binding_from_handoff(source),
            handoff=peer,
        )

    def confirm_catalog_source(self, run_id: str) -> CrossStoreHandoff:
        source = self._load_source(run_id)
        peer = self._recovery_handoffs.load_handoff(source.handoff_id)
        if peer is None:
            raise CrossStoreHandoffError(
                "RUN_START_PEER_HANDOFF_NOT_COMMITTED",
                "Commit the matching recovery run binding before releasing the run.",
            )
        mismatch = handoff_evidence_mismatch(source, peer)
        if mismatch is not None:
            self._mark_ambiguous(source, peer, mismatch)
            raise CrossStoreHandoffError(
                mismatch,
                "Inspect both state stores before allowing this run to mutate files.",
            )
        if peer.state not in {
            CrossStoreHandoffState.PEER_COMMITTED,
            CrossStoreHandoffState.COMPLETED,
        }:
            raise CrossStoreHandoffError(
                "RUN_START_PEER_HANDOFF_STATE_INVALID",
                "Reconcile the recovery peer handoff before releasing the run.",
            )

        def confirm() -> CrossStoreHandoff:
            current = self._load_source(run_id)
            if current.state is CrossStoreHandoffState.PREPARED:
                released = self._catalog_runs.release_created_run(run_id)
                if released is None or released.state is RunState.CREATED:
                    raise CrossStoreHandoffError(
                        "RUN_START_RELEASE_FAILED",
                        "Keep the run non-runnable and retry catalog source confirmation.",
                    )
                transitioned = self._catalog_handoffs.transition_handoff(
                    handoff_id=current.handoff_id,
                    expected_state=CrossStoreHandoffState.PREPARED,
                    next_state=CrossStoreHandoffState.SOURCE_CONFIRMED,
                    expected_payload_hash=current.payload_hash,
                )
                if transitioned is None:
                    raise CrossStoreHandoffError(
                        "RUN_START_SOURCE_CONFIRMATION_CONFLICT",
                        "Re-read the catalog handoff before releasing the run.",
                    )
                current = transitioned
            self._accept_run_start_receipt(current)
            return current

        return self._catalog_transaction.run(confirm)

    def complete_run_start(self, run_id: str) -> CrossStoreHandoff:
        source = self._load_source(run_id)
        peer = self._recovery_handoffs.load_handoff(source.handoff_id)
        if peer is None:
            raise CrossStoreHandoffError(
                "RUN_START_PEER_HANDOFF_NOT_COMMITTED",
                "Commit the matching recovery peer before handoff cleanup.",
            )
        mismatch = handoff_evidence_mismatch(source, peer)
        if mismatch is not None:
            self._mark_ambiguous(source, peer, mismatch)
            raise CrossStoreHandoffError(
                mismatch,
                "Inspect both state stores before completing this handoff.",
            )
        if source.state is CrossStoreHandoffState.COMPLETED:
            if peer.state is not CrossStoreHandoffState.COMPLETED:
                raise CrossStoreHandoffError(
                    "RUN_START_COMPLETION_STATE_DIVERGED",
                    "Reconcile the two handoff records before new mutations.",
                )
            return source
        if source.state is not CrossStoreHandoffState.SOURCE_CONFIRMED:
            raise CrossStoreHandoffError(
                "RUN_START_SOURCE_NOT_CONFIRMED",
                "Confirm catalog readiness before completing the handoff.",
            )
        if peer.state is CrossStoreHandoffState.PEER_COMMITTED:
            peer = self._recovery_handoffs.transition_handoff(
                handoff_id=peer.handoff_id,
                expected_state=CrossStoreHandoffState.PEER_COMMITTED,
                next_state=CrossStoreHandoffState.COMPLETED,
                expected_payload_hash=peer.payload_hash,
            )
            if peer is None:
                raise CrossStoreHandoffError(
                    "RUN_START_PEER_COMPLETION_CONFLICT",
                    "Retry handoff completion after re-reading recovery state.",
                )
        if peer.state is not CrossStoreHandoffState.COMPLETED:
            raise CrossStoreHandoffError(
                "RUN_START_PEER_NOT_COMPLETED",
                "Complete recovery acknowledgement before catalog cleanup.",
            )
        completed = self._catalog_handoffs.transition_handoff(
            handoff_id=source.handoff_id,
            expected_state=CrossStoreHandoffState.SOURCE_CONFIRMED,
            next_state=CrossStoreHandoffState.COMPLETED,
            expected_payload_hash=source.payload_hash,
        )
        if completed is None:
            raise CrossStoreHandoffError(
                "RUN_START_SOURCE_COMPLETION_CONFLICT",
                "Retry handoff completion after re-reading catalog state.",
            )
        return completed

    def advance_run_start(self, run_id: str) -> CrossStoreHandoff:
        self.commit_recovery_peer(run_id)
        self.confirm_catalog_source(run_id)
        return self.complete_run_start(run_id)

    def reconcile_pending(self, *, limit: int = 100) -> CrossStoreHandoffReconciliationReport:
        if limit < 1 or limit > 1000:
            raise CrossStoreHandoffError(
                "RUN_START_HANDOFF_RECONCILIATION_LIMIT_INVALID",
                "Use a reconciliation limit between 1 and 1000.",
            )
        catalog_candidates = self._catalog_handoffs.list_handoffs_for_reconciliation(
            handoff_type=RUN_START_HANDOFF_TYPE,
            direction=CATALOG_TO_RECOVERY,
            limit=limit,
        )
        recovery_candidates = self._recovery_handoffs.list_handoffs_for_reconciliation(
            handoff_type=RUN_START_HANDOFF_TYPE,
            direction=CATALOG_TO_RECOVERY,
            limit=limit,
        )
        completed: list[str] = []
        ambiguous: list[str] = []
        candidate_ids = tuple(
            dict.fromkeys(
                handoff.handoff_id
                for handoff in (*catalog_candidates, *recovery_candidates)
            )
        )
        for handoff_id in candidate_ids:
            source = self._catalog_handoffs.load_handoff(handoff_id)
            peer = self._recovery_handoffs.load_handoff(handoff_id)
            if source is None:
                if peer is not None and peer.state not in {
                    CrossStoreHandoffState.AMBIGUOUS,
                    CrossStoreHandoffState.COMPLETED,
                }:
                    self._recovery_handoffs.transition_handoff(
                        handoff_id=peer.handoff_id,
                        expected_state=peer.state,
                        next_state=CrossStoreHandoffState.AMBIGUOUS,
                        expected_payload_hash=peer.payload_hash,
                        last_error_code="RUN_START_SOURCE_HANDOFF_NOT_FOUND",
                    )
                ambiguous.append(handoff_id)
                continue
            if source.state is CrossStoreHandoffState.AMBIGUOUS or (
                peer is not None and peer.state is CrossStoreHandoffState.AMBIGUOUS
            ):
                ambiguous.append(handoff_id)
                continue
            if source.state is CrossStoreHandoffState.COMPLETED:
                if peer is None or peer.state is not CrossStoreHandoffState.COMPLETED:
                    if peer is not None and peer.state not in {
                        CrossStoreHandoffState.AMBIGUOUS,
                        CrossStoreHandoffState.COMPLETED,
                    }:
                        self._recovery_handoffs.transition_handoff(
                            handoff_id=peer.handoff_id,
                            expected_state=peer.state,
                            next_state=CrossStoreHandoffState.AMBIGUOUS,
                            expected_payload_hash=peer.payload_hash,
                            last_error_code="RUN_START_COMPLETION_STATE_DIVERGED",
                        )
                    ambiguous.append(handoff_id)
                continue
            try:
                self.advance_run_start(source.entity_id)
            except CrossStoreHandoffError:
                current = self._catalog_handoffs.load_handoff(handoff_id)
                if current is not None and current.state is CrossStoreHandoffState.AMBIGUOUS:
                    ambiguous.append(handoff_id)
                    continue
                raise
            completed.append(handoff_id)
        return CrossStoreHandoffReconciliationReport(
            scanned=len(candidate_ids),
            completed_handoff_ids=tuple(completed),
            ambiguous_handoff_ids=tuple(ambiguous),
        )

    def _load_source(self, run_id: str) -> CrossStoreHandoff:
        handoff = self._catalog_handoffs.load_handoff(run_start_handoff_id(run_id))
        if handoff is None:
            raise CrossStoreHandoffError(
                "RUN_START_SOURCE_HANDOFF_NOT_FOUND",
                "Keep the run non-runnable until its catalog handoff is recovered.",
            )
        return handoff

    def _accept_run_start_receipt(self, handoff: CrossStoreHandoff) -> None:
        payload = decode_handoff_payload(handoff)
        transition_required = payload.get("transition_command_receipt")
        if transition_required is False:
            return
        if transition_required is not True:
            raise CrossStoreHandoffError(
                "RUN_START_COMMAND_RECEIPT_POLICY_MISSING",
                "Record whether the run-start handoff owns a command receipt transition.",
            )
        receipt_id = _required_payload_string(payload, "command_receipt_id")
        receipt = self._command_receipts.load_command_receipt(receipt_id)
        if receipt is None:
            raise CrossStoreHandoffError(
                "RUN_START_COMMAND_RECEIPT_NOT_FOUND",
                "Recover the command receipt before releasing the run.",
            )
        if receipt.state is CommandReceiptState.EFFECT_PREPARED:
            receipt = transition_command_receipt(receipt, CommandReceiptState.ACCEPTED)
            self._command_receipts.update_command_receipt(receipt)
        if receipt.state is CommandReceiptState.ACCEPTED:
            receipt = transition_command_receipt(receipt, CommandReceiptState.SUCCEEDED)
            self._command_receipts.update_command_receipt(receipt)
        if receipt.state is not CommandReceiptState.SUCCEEDED:
            raise CrossStoreHandoffError(
                "RUN_START_COMMAND_RECEIPT_STATE_CONFLICT",
                "Keep the run blocked until its command receipt is reconciled.",
            )
        if receipt.result_entity_id != handoff.entity_id:
            raise CrossStoreHandoffError(
                "RUN_START_COMMAND_RECEIPT_ENTITY_MISMATCH",
                "Inspect the command receipt and run binding before continuing.",
            )
        if self._outbox is not None:
            self._outbox.enqueue_outbox_message(command_effect_outbox_message(receipt))

    def _mark_ambiguous(
        self,
        source: CrossStoreHandoff,
        peer: CrossStoreHandoff,
        error_code: str,
    ) -> None:
        if source.state not in {
            CrossStoreHandoffState.COMPLETED,
            CrossStoreHandoffState.AMBIGUOUS,
            CrossStoreHandoffState.ABORTED,
        }:
            self._catalog_handoffs.transition_handoff(
                handoff_id=source.handoff_id,
                expected_state=source.state,
                next_state=CrossStoreHandoffState.AMBIGUOUS,
                expected_payload_hash=source.payload_hash,
                last_error_code=error_code,
            )
        if peer.state not in {
            CrossStoreHandoffState.COMPLETED,
            CrossStoreHandoffState.AMBIGUOUS,
            CrossStoreHandoffState.ABORTED,
        }:
            self._recovery_handoffs.transition_handoff(
                handoff_id=peer.handoff_id,
                expected_state=peer.state,
                next_state=CrossStoreHandoffState.AMBIGUOUS,
                expected_payload_hash=peer.payload_hash,
                last_error_code=error_code,
            )


def run_start_handoff_id(run_id: str) -> str:
    return f"run-start:{run_id}"


def run_start_source_handoff(
    run: StartedRun,
    *,
    transition_command_receipt: bool = True,
) -> CrossStoreHandoff:
    payload = {
        "command_receipt_id": run.command_receipt_id,
        "job_id": run.job_id,
        "job_revision_id": run.job_revision_id,
        "plan_checksum": run.plan_checksum,
        "plan_id": run.plan_id,
        "run_id": run.run_id,
        "transition_command_receipt": transition_command_receipt,
        "targets": [
            {
                "endpoint_id": target.endpoint_id,
                "endpoint_revision_id": target.endpoint_revision_id,
                "required_owner_installation_id": target.required_owner_installation_id,
                "required_ownership_epoch": target.required_ownership_epoch,
                "run_target_id": target.run_target_id,
            }
            for target in sorted(run.targets, key=lambda item: item.run_target_id)
        ],
    }
    payload_json = canonical_handoff_payload(payload)
    return CrossStoreHandoff(
        handoff_id=run_start_handoff_id(run.run_id),
        handoff_type=RUN_START_HANDOFF_TYPE,
        direction=CATALOG_TO_RECOVERY,
        payload_schema_version=HANDOFF_PAYLOAD_SCHEMA_VERSION,
        entity_type="RUN",
        entity_id=run.run_id,
        payload_json=payload_json,
        payload_hash=hash_handoff_payload(payload_json),
        state=CrossStoreHandoffState.PREPARED,
        expected_peer_state=CrossStoreHandoffState.PEER_COMMITTED,
    )


def recovery_run_binding_from_handoff(handoff: CrossStoreHandoff) -> RecoveryRunBinding:
    payload = decode_handoff_payload(handoff)
    run_id = _required_payload_string(payload, "run_id")
    if run_id != handoff.entity_id:
        raise CrossStoreHandoffError(
            "RUN_START_HANDOFF_ENTITY_MISMATCH",
            "Inspect the immutable run-start handoff payload.",
        )
    return RecoveryRunBinding(
        run_id=run_id,
        job_id=_required_payload_string(payload, "job_id"),
        job_revision_id=_required_payload_string(payload, "job_revision_id"),
        plan_id=_required_payload_string(payload, "plan_id"),
        plan_checksum=_required_payload_string(payload, "plan_checksum"),
        start_handoff_id=handoff.handoff_id,
    )


def canonical_handoff_payload(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_handoff_payload(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def decode_handoff_payload(handoff: CrossStoreHandoff) -> dict[str, object]:
    if hash_handoff_payload(handoff.payload_json) != handoff.payload_hash:
        raise CrossStoreHandoffError(
            "CROSS_STORE_HANDOFF_PAYLOAD_HASH_MISMATCH",
            "Inspect the immutable handoff row before allowing mutations.",
        )
    try:
        payload = json.loads(handoff.payload_json)
    except json.JSONDecodeError as exc:
        raise CrossStoreHandoffError(
            "CROSS_STORE_HANDOFF_PAYLOAD_INVALID",
            "Inspect the immutable handoff row before allowing mutations.",
        ) from exc
    if not isinstance(payload, dict):
        raise CrossStoreHandoffError(
            "CROSS_STORE_HANDOFF_PAYLOAD_INVALID",
            "Use a canonical JSON object for handoff evidence.",
        )
    return payload


def handoff_evidence_mismatch(
    source: CrossStoreHandoff,
    peer: CrossStoreHandoff,
) -> str | None:
    fields = (
        "handoff_id",
        "handoff_type",
        "direction",
        "payload_schema_version",
        "entity_type",
        "entity_id",
        "payload_json",
        "payload_hash",
    )
    for field_name in fields:
        if getattr(source, field_name) != getattr(peer, field_name):
            return f"CROSS_STORE_HANDOFF_{field_name.upper()}_MISMATCH"
    try:
        decode_handoff_payload(source)
        decode_handoff_payload(peer)
    except CrossStoreHandoffError as exc:
        return exc.validation_code
    return None


def _assert_source_run_start_handoff(handoff: CrossStoreHandoff) -> None:
    if (
        handoff.handoff_type != RUN_START_HANDOFF_TYPE
        or handoff.direction != CATALOG_TO_RECOVERY
        or handoff.entity_type != "RUN"
        or handoff.state
        not in {
            CrossStoreHandoffState.PREPARED,
            CrossStoreHandoffState.SOURCE_CONFIRMED,
            CrossStoreHandoffState.COMPLETED,
        }
    ):
        raise CrossStoreHandoffError(
            "RUN_START_SOURCE_HANDOFF_INVALID",
            "Inspect the catalog handoff before binding recovery state.",
        )
    decode_handoff_payload(handoff)


def _required_payload_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CrossStoreHandoffError(
            "CROSS_STORE_HANDOFF_PAYLOAD_FIELD_MISSING",
            f"Record a non-empty {key} in the handoff payload.",
        )
    return value


class CrossStoreOperationCatalogCoordinator(OperationCatalogCrossStoreCoordinator):
    def __init__(
        self,
        *,
        recovery_handoffs: CrossStoreHandoffStore,
        catalog_handoffs: CrossStoreHandoffStore,
        recovery_operations: RecoveryOperationStore,
        catalog_effects: FinalFileCatalogHandoffStore,
        recovery_transaction: CommandEffectTransaction,
        catalog_transaction: CommandEffectTransaction,
    ) -> None:
        self._recovery_handoffs = recovery_handoffs
        self._catalog_handoffs = catalog_handoffs
        self._recovery_operations = recovery_operations
        self._catalog_effects = catalog_effects
        self._recovery_transaction = recovery_transaction
        self._catalog_transaction = catalog_transaction

    def prepare_operation_catalog(
        self,
        *,
        operation: RecoveryOperation,
        handoff: FinalFileCatalogHandoff,
    ) -> CrossStoreHandoff:
        validate_final_file_catalog_handoff(handoff)
        source = operation_catalog_source_handoff(operation=operation, handoff=handoff)

        def prepare() -> CrossStoreHandoff:
            current = self._recovery_operations.load_operation(
                run_id=operation.run_id,
                operation_id=operation.operation_id,
            )
            if current is None or current.phase not in {
                RecoveryOperationPhase.FINAL_VERIFIED,
                RecoveryOperationPhase.CATALOG_RECORDED,
            }:
                raise CrossStoreHandoffError(
                    "OPERATION_CATALOG_HANDOFF_REQUIRES_FINAL_VERIFIED",
                    "Verify the final artifact before preparing catalog handoff.",
                )
            return self._recovery_handoffs.record_handoff(source)

        return self._recovery_transaction.run(prepare)

    def commit_catalog_peer(self, handoff_id: str) -> CrossStoreHandoff:
        source = self._load_recovery_source(handoff_id)
        final_handoff = final_file_handoff_from_cross_store(source)

        def commit() -> CrossStoreHandoff:
            self._catalog_effects.record_final_file_handoff(final_handoff)
            peer = replace(
                source,
                state=CrossStoreHandoffState.PEER_COMMITTED,
                expected_peer_state=CrossStoreHandoffState.SOURCE_CONFIRMED,
            )
            return self._catalog_handoffs.record_handoff(peer)

        return self._catalog_transaction.run(commit)

    def confirm_recovery_source(
        self,
        *,
        handoff_id: str,
        process_instance_id: str,
    ) -> RecoveryOperation:
        source = self._load_recovery_source(handoff_id)
        peer = self._catalog_handoffs.load_handoff(handoff_id)
        if peer is None:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_PEER_NOT_COMMITTED",
                "Commit catalog outcome and peer evidence before recovery acknowledgement.",
            )
        mismatch = handoff_evidence_mismatch(source, peer)
        if mismatch is not None:
            self._mark_operation_ambiguous(source, peer, mismatch)
            raise CrossStoreHandoffError(
                mismatch,
                "Inspect catalog and recovery evidence before continuing this operation.",
            )
        final_handoff = final_file_handoff_from_cross_store(source)

        def confirm() -> RecoveryOperation:
            current = self._recovery_operations.load_operation(
                run_id=final_handoff.run_id,
                operation_id=final_handoff.operation_id,
            )
            if current is None:
                raise CrossStoreHandoffError(
                    "OPERATION_CATALOG_RECOVERY_OPERATION_NOT_FOUND",
                    "Recover the operation journal before acknowledging catalog commit.",
                )
            if current.phase is RecoveryOperationPhase.FINAL_VERIFIED:
                updated = self._recovery_operations.record_operation_phase_transition(
                    run_id=current.run_id,
                    operation_id=current.operation_id,
                    expected_phase=RecoveryOperationPhase.FINAL_VERIFIED,
                    next_phase=RecoveryOperationPhase.CATALOG_RECORDED,
                    process_instance_id=process_instance_id,
                    payload=catalog_handoff_transition_payload(final_handoff),
                    catalog_handoff_id=final_handoff.handoff_id,
                )
                if updated is None:
                    raise CrossStoreHandoffError(
                        "OPERATION_CATALOG_RECOVERY_PHASE_CONFLICT",
                        "Re-read recovery state before acknowledging catalog commit.",
                    )
                current = updated
            if (
                current.phase is not RecoveryOperationPhase.CATALOG_RECORDED
                or current.catalog_handoff_id != final_handoff.handoff_id
            ):
                raise CrossStoreHandoffError(
                    "OPERATION_CATALOG_RECOVERY_ACK_MISMATCH",
                    "Inspect operation and catalog IDs before continuing.",
                )
            latest_source = self._load_recovery_source(handoff_id)
            if latest_source.state is CrossStoreHandoffState.PREPARED:
                transitioned = self._recovery_handoffs.transition_handoff(
                    handoff_id=handoff_id,
                    expected_state=CrossStoreHandoffState.PREPARED,
                    next_state=CrossStoreHandoffState.SOURCE_CONFIRMED,
                    expected_payload_hash=latest_source.payload_hash,
                )
                if transitioned is None:
                    raise CrossStoreHandoffError(
                        "OPERATION_CATALOG_SOURCE_CONFIRMATION_CONFLICT",
                        "Retry recovery acknowledgement after re-reading handoff state.",
                    )
            return current

        return self._recovery_transaction.run(confirm)

    def complete_operation_catalog(self, handoff_id: str) -> CrossStoreHandoff:
        source = self._load_recovery_source(handoff_id)
        peer = self._catalog_handoffs.load_handoff(handoff_id)
        if peer is None:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_PEER_NOT_COMMITTED",
                "Commit catalog peer evidence before handoff completion.",
            )
        mismatch = handoff_evidence_mismatch(source, peer)
        if mismatch is not None:
            self._mark_operation_ambiguous(source, peer, mismatch)
            raise CrossStoreHandoffError(
                mismatch,
                "Inspect catalog and recovery evidence before completing the handoff.",
            )
        if source.state is CrossStoreHandoffState.COMPLETED:
            if peer.state is not CrossStoreHandoffState.COMPLETED:
                raise CrossStoreHandoffError(
                    "OPERATION_CATALOG_COMPLETION_STATE_DIVERGED",
                    "Inspect both handoff records before new mutations.",
                )
            return source
        if source.state is not CrossStoreHandoffState.SOURCE_CONFIRMED:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_SOURCE_NOT_CONFIRMED",
                "Acknowledge the catalog outcome in recovery before cleanup.",
            )
        if peer.state is CrossStoreHandoffState.PEER_COMMITTED:
            peer = self._catalog_handoffs.transition_handoff(
                handoff_id=handoff_id,
                expected_state=CrossStoreHandoffState.PEER_COMMITTED,
                next_state=CrossStoreHandoffState.COMPLETED,
                expected_payload_hash=peer.payload_hash,
            )
            if peer is None:
                raise CrossStoreHandoffError(
                    "OPERATION_CATALOG_PEER_COMPLETION_CONFLICT",
                    "Retry catalog peer completion after re-reading state.",
                )
        if peer.state is not CrossStoreHandoffState.COMPLETED:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_PEER_NOT_COMPLETED",
                "Complete catalog peer evidence before recovery cleanup.",
            )
        completed = self._recovery_handoffs.transition_handoff(
            handoff_id=handoff_id,
            expected_state=CrossStoreHandoffState.SOURCE_CONFIRMED,
            next_state=CrossStoreHandoffState.COMPLETED,
            expected_payload_hash=source.payload_hash,
        )
        if completed is None:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_SOURCE_COMPLETION_CONFLICT",
                "Retry recovery handoff completion after re-reading state.",
            )
        return completed

    def record_operation_catalog_handoff(
        self,
        *,
        operation: RecoveryOperation,
        handoff: FinalFileCatalogHandoff,
        process_instance_id: str,
    ) -> CatalogHandoffOutcome:
        was_recorded = operation.phase is RecoveryOperationPhase.CATALOG_RECORDED
        source = self.prepare_operation_catalog(operation=operation, handoff=handoff)
        self.commit_catalog_peer(source.handoff_id)
        updated = self.confirm_recovery_source(
            handoff_id=source.handoff_id,
            process_instance_id=process_instance_id,
        )
        self.complete_operation_catalog(source.handoff_id)
        recorded = self._catalog_effects.load_final_file_handoff(handoff.handoff_id)
        if recorded is None:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_EFFECT_NOT_FOUND",
                "Reconcile catalog effect evidence before continuing.",
            )
        return CatalogHandoffOutcome(
            handoff=recorded,
            recovery_operation=updated,
            idempotent_replay=was_recorded,
        )

    def reconcile_pending(
        self,
        *,
        process_instance_id: str,
        limit: int = 100,
    ) -> CrossStoreHandoffReconciliationReport:
        recovery_candidates = self._recovery_handoffs.list_handoffs_for_reconciliation(
            handoff_type=OPERATION_CATALOG_HANDOFF_TYPE,
            direction=RECOVERY_TO_CATALOG,
            limit=limit,
        )
        catalog_candidates = self._catalog_handoffs.list_handoffs_for_reconciliation(
            handoff_type=OPERATION_CATALOG_HANDOFF_TYPE,
            direction=RECOVERY_TO_CATALOG,
            limit=limit,
        )
        completed: list[str] = []
        ambiguous: list[str] = []
        candidate_ids = tuple(
            dict.fromkeys(
                handoff.handoff_id
                for handoff in (*recovery_candidates, *catalog_candidates)
            )
        )
        for handoff_id in candidate_ids:
            source = self._recovery_handoffs.load_handoff(handoff_id)
            peer = self._catalog_handoffs.load_handoff(handoff_id)
            if source is None:
                if peer is not None and peer.state not in {
                    CrossStoreHandoffState.AMBIGUOUS,
                    CrossStoreHandoffState.COMPLETED,
                }:
                    self._catalog_handoffs.transition_handoff(
                        handoff_id=peer.handoff_id,
                        expected_state=peer.state,
                        next_state=CrossStoreHandoffState.AMBIGUOUS,
                        expected_payload_hash=peer.payload_hash,
                        last_error_code="OPERATION_CATALOG_SOURCE_HANDOFF_NOT_FOUND",
                    )
                ambiguous.append(handoff_id)
                continue
            if source.state is CrossStoreHandoffState.AMBIGUOUS or (
                peer is not None and peer.state is CrossStoreHandoffState.AMBIGUOUS
            ):
                ambiguous.append(handoff_id)
                continue
            if source.state is CrossStoreHandoffState.COMPLETED:
                if peer is None or peer.state is not CrossStoreHandoffState.COMPLETED:
                    if peer is not None and peer.state not in {
                        CrossStoreHandoffState.AMBIGUOUS,
                        CrossStoreHandoffState.COMPLETED,
                    }:
                        self._catalog_handoffs.transition_handoff(
                            handoff_id=peer.handoff_id,
                            expected_state=peer.state,
                            next_state=CrossStoreHandoffState.AMBIGUOUS,
                            expected_payload_hash=peer.payload_hash,
                            last_error_code="OPERATION_CATALOG_COMPLETION_STATE_DIVERGED",
                        )
                    ambiguous.append(handoff_id)
                continue
            try:
                self.commit_catalog_peer(handoff_id)
                self.confirm_recovery_source(
                    handoff_id=handoff_id,
                    process_instance_id=process_instance_id,
                )
                self.complete_operation_catalog(handoff_id)
            except CrossStoreHandoffError:
                current = self._recovery_handoffs.load_handoff(handoff_id)
                if current is not None and current.state is CrossStoreHandoffState.AMBIGUOUS:
                    ambiguous.append(handoff_id)
                    continue
                raise
            completed.append(handoff_id)
        return CrossStoreHandoffReconciliationReport(
            scanned=len(candidate_ids),
            completed_handoff_ids=tuple(completed),
            ambiguous_handoff_ids=tuple(ambiguous),
        )

    def _load_recovery_source(self, handoff_id: str) -> CrossStoreHandoff:
        source = self._recovery_handoffs.load_handoff(handoff_id)
        if source is None:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_SOURCE_HANDOFF_NOT_FOUND",
                "Keep the operation blocked until recovery handoff evidence is restored.",
            )
        if (
            source.handoff_type != OPERATION_CATALOG_HANDOFF_TYPE
            or source.direction != RECOVERY_TO_CATALOG
            or source.entity_type != "RECOVERY_OPERATION"
        ):
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_SOURCE_HANDOFF_INVALID",
                "Inspect recovery handoff evidence before continuing.",
            )
        decode_handoff_payload(source)
        return source

    def _mark_operation_ambiguous(
        self,
        source: CrossStoreHandoff,
        peer: CrossStoreHandoff,
        error_code: str,
    ) -> None:
        if source.state not in {
            CrossStoreHandoffState.COMPLETED,
            CrossStoreHandoffState.AMBIGUOUS,
            CrossStoreHandoffState.ABORTED,
        }:
            self._recovery_handoffs.transition_handoff(
                handoff_id=source.handoff_id,
                expected_state=source.state,
                next_state=CrossStoreHandoffState.AMBIGUOUS,
                expected_payload_hash=source.payload_hash,
                last_error_code=error_code,
            )
        if peer.state not in {
            CrossStoreHandoffState.COMPLETED,
            CrossStoreHandoffState.AMBIGUOUS,
            CrossStoreHandoffState.ABORTED,
        }:
            self._catalog_handoffs.transition_handoff(
                handoff_id=peer.handoff_id,
                expected_state=peer.state,
                next_state=CrossStoreHandoffState.AMBIGUOUS,
                expected_payload_hash=peer.payload_hash,
                last_error_code=error_code,
            )


def operation_catalog_handoff_id(operation: RecoveryOperation) -> str:
    return f"operation-catalog:{operation.run_id}:{operation.operation_id}"


def operation_catalog_source_handoff(
    *,
    operation: RecoveryOperation,
    handoff: FinalFileCatalogHandoff,
) -> CrossStoreHandoff:
    if operation.phase not in {
        RecoveryOperationPhase.FINAL_VERIFIED,
        RecoveryOperationPhase.CATALOG_RECORDED,
    }:
        raise CrossStoreHandoffError(
            "OPERATION_CATALOG_HANDOFF_REQUIRES_FINAL_VERIFIED",
            "Verify the final artifact before catalog handoff.",
        )
    payload_json = canonical_handoff_payload(
        {
            "final_file_handoff": asdict(handoff),
            "operation_id": operation.operation_id,
            "plan_sequence_no": operation.plan_sequence_no,
            "run_id": operation.run_id,
            "run_target_id": operation.run_target_id,
        }
    )
    return CrossStoreHandoff(
        handoff_id=operation_catalog_handoff_id(operation),
        handoff_type=OPERATION_CATALOG_HANDOFF_TYPE,
        direction=RECOVERY_TO_CATALOG,
        payload_schema_version=HANDOFF_PAYLOAD_SCHEMA_VERSION,
        entity_type="RECOVERY_OPERATION",
        entity_id=f"{operation.run_id}:{operation.operation_id}",
        payload_json=payload_json,
        payload_hash=hash_handoff_payload(payload_json),
        state=CrossStoreHandoffState.PREPARED,
        expected_peer_state=CrossStoreHandoffState.PEER_COMMITTED,
    )


def final_file_handoff_from_cross_store(
    cross_store_handoff: CrossStoreHandoff,
) -> FinalFileCatalogHandoff:
    payload = decode_handoff_payload(cross_store_handoff)
    raw = payload.get("final_file_handoff")
    if not isinstance(raw, dict):
        raise CrossStoreHandoffError(
            "OPERATION_CATALOG_FINAL_HANDOFF_MISSING",
            "Record final-file handoff evidence in the cross-store payload.",
        )
    retained_raw = raw.get("retained_version")
    retained = None
    if retained_raw is not None:
        if not isinstance(retained_raw, dict):
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_RETAINED_VERSION_INVALID",
                "Record structured retained-version evidence in the handoff.",
            )
        try:
            retained = RetainedVersionCatalogHandoff(**retained_raw)
        except TypeError as exc:
            raise CrossStoreHandoffError(
                "OPERATION_CATALOG_RETAINED_VERSION_INVALID",
                "Record complete retained-version evidence in the handoff.",
            ) from exc
    values = dict(raw)
    values["retained_version"] = retained
    try:
        handoff = FinalFileCatalogHandoff(**values)
    except TypeError as exc:
        raise CrossStoreHandoffError(
            "OPERATION_CATALOG_FINAL_HANDOFF_INVALID",
            "Record complete final-file handoff evidence in the payload.",
        ) from exc
    try:
        validate_final_file_catalog_handoff(handoff)
    except (CatalogHandoffError, TypeError, ValueError) as exc:
        raise CrossStoreHandoffError(
            "OPERATION_CATALOG_FINAL_HANDOFF_INVALID",
            "Inspect final-file evidence before allowing catalog acknowledgement.",
        ) from exc
    return handoff
