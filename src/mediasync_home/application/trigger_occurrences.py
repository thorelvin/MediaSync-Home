from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import UUID


HEX_256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class TriggerCommandName(str, Enum):
    ENQUEUE_TRIGGER_OCCURRENCE = "ENQUEUE_TRIGGER_OCCURRENCE"


class TriggerOccurrencePayloadError(ValueError):
    pass


class TriggerOccurrenceConflict(ValueError):
    pass


class TriggerKind(str, Enum):
    SCHEDULED_TIME = "SCHEDULED_TIME"
    LOGON = "LOGON"
    STARTUP = "STARTUP"
    EVENT = "EVENT"
    VOLUME_CONNECTED = "VOLUME_CONNECTED"
    MANUAL_LOCAL_PREVIEW = "MANUAL_LOCAL_PREVIEW"


class TriggerOccurrenceState(str, Enum):
    RECEIVED = "RECEIVED"
    WAITING_FOR_RECOVERY = "WAITING_FOR_RECOVERY"
    RUN_ENQUEUED = "RUN_ENQUEUED"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_TRIGGER_OCCURRENCE_STATES = {
    TriggerOccurrenceState.SUCCEEDED,
    TriggerOccurrenceState.REJECTED,
    TriggerOccurrenceState.FAILED,
    TriggerOccurrenceState.CANCELLED,
}


@dataclass(frozen=True)
class TriggerDeliveryContext:
    delivery_id: str
    observed_start_utc: str
    trigger_kind: TriggerKind
    task_definition_hash: str
    task_instance_id: str | None = None
    scheduled_slot_utc: str | None = None
    event_identity: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "delivery_id": self.delivery_id,
            "observed_start_utc": self.observed_start_utc,
            "task_definition_hash": self.task_definition_hash,
            "trigger_kind": self.trigger_kind.value,
        }
        if self.task_instance_id is not None:
            payload["task_instance_id"] = self.task_instance_id
        if self.scheduled_slot_utc is not None:
            payload["scheduled_slot_utc"] = self.scheduled_slot_utc
        if self.event_identity is not None:
            payload["event_identity"] = self.event_identity
        return payload


@dataclass(frozen=True)
class EnqueueTriggerOccurrenceCommand:
    request_id: str
    idempotency_key: str
    schedule_id: str
    schedule_revision_hash: str
    delivery: TriggerDeliveryContext

    def response_payload(self, *, mutations_enabled: bool) -> dict[str, object]:
        return {
            "command_name": TriggerCommandName.ENQUEUE_TRIGGER_OCCURRENCE.value,
            "delivery_id": self.delivery.delivery_id,
            "mutations_enabled": mutations_enabled,
            "recognized": True,
            "schedule_id": self.schedule_id,
            "schedule_revision_hash": self.schedule_revision_hash,
        }


@dataclass(frozen=True)
class TriggerOccurrence:
    occurrence_id: str
    schedule_id: str
    schedule_revision_hash: str
    job_id: str
    occurrence_key: str
    deduplication_key: str
    first_delivery_id: str
    trigger_type: TriggerKind
    payload_hash: str
    occurrence_slot_utc: str | None = None
    source_instance_key: str | None = None
    state: TriggerOccurrenceState = TriggerOccurrenceState.RECEIVED
    run_id: str | None = None
    terminal_effect_hash: str | None = None


@dataclass(frozen=True)
class TriggerOccurrenceRegistration:
    occurrence: TriggerOccurrence
    deduplicated: bool
    compacted: bool = False


class TriggerOccurrenceStore(Protocol):
    def record_received(self, occurrence: TriggerOccurrence) -> TriggerOccurrenceRegistration: ...

    def load_trigger_occurrence(self, occurrence_id: str) -> TriggerOccurrence | None: ...

    def load_trigger_occurrence_by_deduplication_key(
        self,
        deduplication_key: str,
    ) -> TriggerOccurrence | None: ...

    def mark_run_enqueued(
        self,
        *,
        deduplication_key: str,
        run_id: str,
    ) -> TriggerOccurrence: ...


def build_enqueue_trigger_occurrence_payload(
    *,
    schedule_id: str,
    schedule_revision_hash: str,
    delivery: TriggerDeliveryContext,
) -> dict[str, object]:
    command = parse_enqueue_trigger_occurrence_command(
        request_id=delivery.delivery_id,
        idempotency_key=delivery.delivery_id,
        payload={
            "delivery": delivery.to_payload(),
            "schedule_id": schedule_id,
            "schedule_revision_hash": schedule_revision_hash,
        },
    )
    return {
        "delivery": command.delivery.to_payload(),
        "schedule_id": command.schedule_id,
        "schedule_revision_hash": command.schedule_revision_hash,
    }


def payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_trigger_occurrence(
    *,
    installation_id: str,
    job_id: str,
    command: EnqueueTriggerOccurrenceCommand,
) -> TriggerOccurrence:
    installation_id = _required_identifier(installation_id, "installation_id")
    job_id = _required_identifier(job_id, "job_id")
    key_context = _logical_key_context(command.delivery)
    occurrence_key = f"{command.delivery.trigger_kind.value}:{key_context.key_material}"
    deduplication_key = _sha256_hex(
        {
            "installation_id": installation_id,
            "occurrence_key": occurrence_key,
            "schedule_id": command.schedule_id,
            "schedule_revision_hash": command.schedule_revision_hash,
        }
    )
    logical_payload_hash = _sha256_hex(
        {
            "job_id": job_id,
            "occurrence_key": occurrence_key,
            "schedule_id": command.schedule_id,
            "schedule_revision_hash": command.schedule_revision_hash,
            "source_instance_key": key_context.source_instance_key,
            "task_definition_hash": command.delivery.task_definition_hash,
            "trigger_type": command.delivery.trigger_kind.value,
        }
    )
    return TriggerOccurrence(
        occurrence_id=f"trigger:{deduplication_key}",
        schedule_id=command.schedule_id,
        schedule_revision_hash=command.schedule_revision_hash,
        job_id=job_id,
        occurrence_key=occurrence_key,
        deduplication_key=deduplication_key,
        first_delivery_id=command.delivery.delivery_id,
        occurrence_slot_utc=key_context.occurrence_slot_utc,
        source_instance_key=key_context.source_instance_key,
        trigger_type=command.delivery.trigger_kind,
        payload_hash=logical_payload_hash,
    )


def ensure_trigger_occurrence_compatible(
    existing: TriggerOccurrence,
    incoming: TriggerOccurrence,
) -> TriggerOccurrence:
    compared_fields = (
        "schedule_id",
        "schedule_revision_hash",
        "job_id",
        "occurrence_key",
        "trigger_type",
        "payload_hash",
    )
    for field_name in compared_fields:
        if getattr(existing, field_name) != getattr(incoming, field_name):
            raise TriggerOccurrenceConflict(f"TRIGGER_OCCURRENCE_CONFLICT:{field_name}")
    return existing


def parse_enqueue_trigger_occurrence_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> EnqueueTriggerOccurrenceCommand:
    _uuid_string(request_id, "request_id")
    _uuid_string(idempotency_key, "idempotency_key")
    schedule_id = _required_identifier(payload.get("schedule_id"), "schedule_id")
    schedule_revision_hash = _required_hash(
        payload.get("schedule_revision_hash"),
        "schedule_revision_hash",
    )
    delivery_payload = payload.get("delivery")
    if not isinstance(delivery_payload, dict):
        raise TriggerOccurrencePayloadError("ENQUEUE_TRIGGER_REQUIRES_DELIVERY_CONTEXT")
    delivery_id = _uuid_string(delivery_payload.get("delivery_id"), "delivery_id")
    if delivery_id != idempotency_key:
        raise TriggerOccurrencePayloadError("ENQUEUE_TRIGGER_DELIVERY_ID_MUST_MATCH_IDEMPOTENCY_KEY")
    return EnqueueTriggerOccurrenceCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        schedule_id=schedule_id,
        schedule_revision_hash=schedule_revision_hash,
        delivery=TriggerDeliveryContext(
            delivery_id=delivery_id,
            observed_start_utc=_required_utc_text(
                delivery_payload.get("observed_start_utc"),
                "observed_start_utc",
            ),
            trigger_kind=_trigger_kind(delivery_payload.get("trigger_kind")),
            task_definition_hash=_required_hash(
                delivery_payload.get("task_definition_hash"),
                "task_definition_hash",
            ),
            task_instance_id=_optional_text(delivery_payload.get("task_instance_id")),
            scheduled_slot_utc=_optional_utc_text(delivery_payload.get("scheduled_slot_utc")),
            event_identity=_optional_text(delivery_payload.get("event_identity")),
        ),
    )


def _required_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TriggerOccurrencePayloadError(f"ENQUEUE_TRIGGER_REQUIRES_{field_name.upper()}")
    normalized = value.strip()
    if IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise TriggerOccurrencePayloadError(f"ENQUEUE_TRIGGER_INVALID_{field_name.upper()}")
    return normalized


def _required_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or HEX_256_PATTERN.fullmatch(value) is None:
        raise TriggerOccurrencePayloadError(f"ENQUEUE_TRIGGER_INVALID_{field_name.upper()}")
    return value


def _uuid_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TriggerOccurrencePayloadError(f"ENQUEUE_TRIGGER_INVALID_{field_name.upper()}")
    try:
        UUID(value)
    except ValueError as exc:
        raise TriggerOccurrencePayloadError(f"ENQUEUE_TRIGGER_INVALID_{field_name.upper()}") from exc
    return value


def _trigger_kind(value: object) -> TriggerKind:
    if not isinstance(value, str):
        raise TriggerOccurrencePayloadError("ENQUEUE_TRIGGER_INVALID_TRIGGER_KIND")
    try:
        return TriggerKind(value)
    except ValueError as exc:
        raise TriggerOccurrencePayloadError("ENQUEUE_TRIGGER_INVALID_TRIGGER_KIND") from exc


def _required_utc_text(value: object, field_name: str) -> str:
    text = _optional_utc_text(value)
    if text is None:
        raise TriggerOccurrencePayloadError(f"ENQUEUE_TRIGGER_REQUIRES_{field_name.upper()}")
    return text


def _optional_utc_text(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if "T" not in text or not text.endswith("Z"):
        raise TriggerOccurrencePayloadError("ENQUEUE_TRIGGER_INVALID_UTC")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TriggerOccurrencePayloadError("ENQUEUE_TRIGGER_INVALID_TEXT")
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True)
class _LogicalKeyContext:
    key_material: str
    occurrence_slot_utc: str | None
    source_instance_key: str | None


def _logical_key_context(delivery: TriggerDeliveryContext) -> _LogicalKeyContext:
    if delivery.trigger_kind is TriggerKind.SCHEDULED_TIME:
        occurrence_slot_utc = delivery.scheduled_slot_utc or delivery.observed_start_utc
        return _LogicalKeyContext(
            key_material=f"slot:{occurrence_slot_utc}",
            occurrence_slot_utc=occurrence_slot_utc,
            source_instance_key=delivery.task_instance_id,
        )
    if delivery.trigger_kind is TriggerKind.MANUAL_LOCAL_PREVIEW:
        return _LogicalKeyContext(
            key_material=f"delivery:{delivery.delivery_id}",
            occurrence_slot_utc=delivery.observed_start_utc,
            source_instance_key=delivery.delivery_id,
        )

    source_instance_key = (
        delivery.event_identity
        or delivery.task_instance_id
        or f"observed:{delivery.observed_start_utc}"
    )
    return _LogicalKeyContext(
        key_material=f"source:{source_instance_key}",
        occurrence_slot_utc=delivery.scheduled_slot_utc,
        source_instance_key=source_instance_key,
    )


def _sha256_hex(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
