from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from mediasync_home.application.catalog_handoff import (
    CatalogHandoffError,
    CatalogHandoffOutcome,
    FinalFileCatalogHandoffStore,
    record_catalog_handoff_after_final_verification,
)
from mediasync_home.application.recovery_operations import (
    RecoveryOperation,
    RecoveryOperationPhase,
    RecoveryOperationStore,
)
from mediasync_home.domain.capabilities import MutationPermit


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RunTargetCatalogHandoffOperationStore(RecoveryOperationStore, Protocol):
    def list_operations_for_run_target_in_phase(
        self,
        *,
        run_id: str,
        run_target_id: str,
        phase: RecoveryOperationPhase,
        limit: int,
    ) -> tuple[RecoveryOperation, ...]: ...


@dataclass(frozen=True)
class RunTargetCatalogHandoffStepOutcome:
    idle: bool
    recorded: bool
    run_id: str
    run_target_id: str
    operation_id: str | None
    handoff_id: str | None
    handoff_outcome: CatalogHandoffOutcome | None
    validation_codes: tuple[str, ...]
    next_action: str


def record_next_run_target_catalog_handoff(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetCatalogHandoffOperationStore,
    catalog_handoffs: FinalFileCatalogHandoffStore,
    process_instance_id: str,
) -> RunTargetCatalogHandoffStepOutcome:
    if not process_instance_id.strip():
        return _failed(
            permit=permit,
            operation_id=None,
            validation_code="RUN_TARGET_CATALOG_HANDOFF_REQUIRES_PROCESS_INSTANCE",
            next_action="Bind catalog handoff execution to the Engine Host process instance.",
        )

    operation = _next_final_verified_operation(
        permit=permit,
        recovery_operations=recovery_operations,
    )
    if operation is None:
        return RunTargetCatalogHandoffStepOutcome(
            idle=True,
            recorded=False,
            run_id=permit.run_id,
            run_target_id=permit.run_target_id,
            operation_id=None,
            handoff_id=None,
            handoff_outcome=None,
            validation_codes=(),
            next_action="No run-target operation is waiting for catalog handoff.",
        )
    if not _operation_matches_permit(operation=operation, permit=permit):
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            validation_code="RUN_TARGET_CATALOG_HANDOFF_PERMIT_MISMATCH",
            next_action="Reacquire the endpoint lease before recording catalog handoff.",
        )

    content_hash = _content_hash(operation)
    if content_hash is None:
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            validation_code="RUN_TARGET_CATALOG_HANDOFF_REQUIRES_CONTENT_HASH",
            next_action="Verify the final artifact fingerprint before recording catalog handoff.",
        )

    try:
        handoff_outcome = record_catalog_handoff_after_final_verification(
            run_id=operation.run_id,
            operation_id=operation.operation_id,
            content_hash=content_hash,
            recovery_operations=recovery_operations,
            catalog_handoffs=catalog_handoffs,
            process_instance_id=process_instance_id,
        )
    except (CatalogHandoffError, ValueError) as exc:
        return _failed(
            permit=permit,
            operation_id=operation.operation_id,
            validation_code=_error_code(exc),
            next_action=_error_next_action(
                exc,
                "Reconcile catalog handoff state before retrying executor catalog publication.",
            ),
        )

    return RunTargetCatalogHandoffStepOutcome(
        idle=False,
        recorded=True,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation.operation_id,
        handoff_id=handoff_outcome.handoff.handoff_id,
        handoff_outcome=handoff_outcome,
        validation_codes=(),
        next_action="Catalog handoff is recorded for the verified run-target operation.",
    )


def _next_final_verified_operation(
    *,
    permit: MutationPermit,
    recovery_operations: RunTargetCatalogHandoffOperationStore,
) -> RecoveryOperation | None:
    operations = recovery_operations.list_operations_for_run_target_in_phase(
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        phase=RecoveryOperationPhase.FINAL_VERIFIED,
        limit=1,
    )
    if not operations:
        return None
    return operations[0]


def _operation_matches_permit(*, operation: RecoveryOperation, permit: MutationPermit) -> bool:
    return (
        operation.phase is RecoveryOperationPhase.FINAL_VERIFIED
        and operation.run_id == permit.run_id
        and operation.run_target_id == permit.run_target_id
        and operation.target_endpoint_id == permit.endpoint_id
        and operation.target_endpoint_revision_id == permit.endpoint_revision_id
        and operation.owner_installation_id == permit.owner_installation_id
        and operation.ownership_epoch == permit.ownership_epoch
        and operation.lease_id == permit.lease_id
        and operation.lease_resource_key == permit.resource_key
        and operation.fencing_token == permit.fencing_token
    )


def _content_hash(operation: RecoveryOperation) -> str | None:
    for raw_payload in (
        operation.expected_final_fingerprint_json,
        operation.expected_staging_fingerprint_json,
    ):
        if raw_payload is None:
            continue
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        content_hash = payload.get("content_hash")
        if isinstance(content_hash, str) and HASH_PATTERN.fullmatch(content_hash) is not None:
            return content_hash
    return None


def _failed(
    *,
    permit: MutationPermit,
    operation_id: str | None,
    validation_code: str,
    next_action: str,
) -> RunTargetCatalogHandoffStepOutcome:
    return RunTargetCatalogHandoffStepOutcome(
        idle=False,
        recorded=False,
        run_id=permit.run_id,
        run_target_id=permit.run_target_id,
        operation_id=operation_id,
        handoff_id=None,
        handoff_outcome=None,
        validation_codes=(validation_code,),
        next_action=next_action,
    )


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "validation_code", None)
    if isinstance(code, str) and code.strip():
        return code
    message = str(exc)
    if message.strip():
        return message
    return type(exc).__name__


def _error_next_action(exc: Exception, fallback: str) -> str:
    next_action = getattr(exc, "next_action", None)
    if isinstance(next_action, str) and next_action.strip():
        return next_action
    return fallback
