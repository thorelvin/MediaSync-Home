from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import UUID


class EndpointTakeoverState(str, Enum):
    PREPARED = "PREPARED"
    FILESYSTEM_APPLIED = "FILESYSTEM_APPLIED"
    COMMITTED = "COMMITTED"
    BLOCKED = "BLOCKED"


class EndpointTakeoverCommandName(str, Enum):
    START_CONTROLLED_ENDPOINT_TAKEOVER = "START_CONTROLLED_ENDPOINT_TAKEOVER"


class EndpointTakeoverPayloadError(ValueError):
    pass


class EndpointTakeoverError(RuntimeError):
    def __init__(
        self,
        validation_code: str,
        next_action: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code
        self.next_action = next_action
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class StartControlledEndpointTakeoverCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    job_revision_id: str
    target_ordinal: int
    endpoint_id: str
    expected_foreign_owner_installation_id: str
    expected_ownership_epoch: int


@dataclass(frozen=True, slots=True)
class EndpointTakeoverCandidate:
    job_id: str
    job_revision_id: str
    target_ordinal: int
    endpoint_id: str
    endpoint_revision_id: str
    endpoint_generation: int
    display_name: str
    root_uri: str
    control_area_id: str
    foreign_owner_installation_id: str
    foreign_ownership_epoch: int
    root_identity_hash_algorithm: str
    root_identity_hash: str
    marker_checksum_algorithm: str
    marker_checksum: str


@dataclass(frozen=True, slots=True)
class EndpointTakeoverIds:
    intent_id: str
    resulting_endpoint_revision_id: str
    resulting_job_revision_id: str
    analysis_request_id: str


@dataclass(frozen=True, slots=True)
class PreparedEndpointTakeover:
    target_ordinal: int
    endpoint_id: str
    source_endpoint_revision_id: str
    resulting_endpoint_revision_id: str
    resulting_endpoint_generation: int
    display_name: str
    root_uri: str
    control_area_id: str
    foreign_owner_installation_id: str
    foreign_ownership_epoch: int
    owner_installation_id: str
    ownership_epoch: int
    root_identity_hash_algorithm: str
    root_identity_hash: str
    old_marker_checksum_algorithm: str
    old_marker_checksum: str
    marker_checksum_algorithm: str
    marker_checksum: str
    marker_payload_json: str
    ownership_record_path: str
    ownership_payload_json: str
    takeover_record_path: str
    takeover_payload_json: str
    probe_token: str


@dataclass(frozen=True, slots=True)
class EndpointTakeoverIntent:
    intent_id: str
    job_id: str
    source_job_revision_id: str
    resulting_job_revision_id: str
    analysis_request_id: str
    command_request_id: str
    command_idempotency_key: str
    state: EndpointTakeoverState
    prepared: PreparedEndpointTakeover
    created_utc: str
    updated_utc: str
    last_error_code: str | None = None
    last_next_action: str | None = None


@dataclass(frozen=True, slots=True)
class EndpointTakeoverReport:
    job_id: str
    source_job_revision_id: str
    active_job_revision_id: str
    endpoint_id: str
    target_ordinal: int
    intent_id: str
    analysis_request_id: str
    state: EndpointTakeoverState
    old_owner_installation_id: str
    old_ownership_epoch: int
    new_owner_installation_id: str
    new_ownership_epoch: int
    idempotent_replay: bool
    validation_codes: tuple[str, ...] = ()
    next_action: str | None = None

    @property
    def completed(self) -> bool:
        return self.state is EndpointTakeoverState.COMMITTED

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "source_job_revision_id": self.source_job_revision_id,
            "active_job_revision_id": self.active_job_revision_id,
            "endpoint_id": self.endpoint_id,
            "target_ordinal": self.target_ordinal,
            "intent_id": self.intent_id,
            "analysis_request_id": self.analysis_request_id,
            "state": self.state.value,
            "old_owner_installation_id": self.old_owner_installation_id,
            "old_ownership_epoch": self.old_ownership_epoch,
            "new_owner_installation_id": self.new_owner_installation_id,
            "new_ownership_epoch": self.new_ownership_epoch,
            "idempotent_replay": self.idempotent_replay,
            "completed": self.completed,
            "full_analysis_queued": self.completed,
            "start_when_safe": False,
            "validation_codes": list(self.validation_codes),
            "next_action": self.next_action,
        }


class EndpointTakeoverIdFactory(Protocol):
    def new_takeover_ids(self) -> EndpointTakeoverIds: ...


class EndpointTakeoverFilesystem(Protocol):
    def prepare_controlled_takeover(
        self,
        candidate: EndpointTakeoverCandidate,
        *,
        intent_id: str,
        resulting_endpoint_revision_id: str,
        owner_installation_id: str,
        created_utc: str,
    ) -> PreparedEndpointTakeover: ...

    def apply_prepared_takeover(
        self,
        prepared: PreparedEndpointTakeover,
        *,
        intent_id: str,
    ) -> None: ...


class EndpointTakeoverStore(Protocol):
    def load_takeover_intent(
        self,
        *,
        job_id: str,
        source_job_revision_id: str,
        target_ordinal: int,
    ) -> EndpointTakeoverIntent | None: ...

    def load_takeover_candidate(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        target_ordinal: int,
        endpoint_id: str,
        expected_foreign_owner_installation_id: str,
        expected_ownership_epoch: int,
    ) -> EndpointTakeoverCandidate: ...

    def save_prepared_takeover_intent(
        self,
        intent: EndpointTakeoverIntent,
    ) -> EndpointTakeoverIntent: ...

    def mark_takeover_filesystem_applied(
        self,
        *,
        intent_id: str,
        updated_utc: str,
    ) -> EndpointTakeoverIntent: ...

    def note_takeover_failure(
        self,
        *,
        intent_id: str,
        validation_code: str,
        next_action: str,
        blocked: bool,
        updated_utc: str,
    ) -> EndpointTakeoverIntent: ...

    def commit_takeover_intent(
        self,
        *,
        intent_id: str,
        committed_utc: str,
    ) -> EndpointTakeoverIntent: ...

    def list_recoverable_takeover_intents(
        self,
        *,
        limit: int,
    ) -> tuple[EndpointTakeoverIntent, ...]: ...


class EndpointTakeoverCoordinator:
    def __init__(
        self,
        *,
        store: EndpointTakeoverStore,
        filesystem: EndpointTakeoverFilesystem,
        id_factory: EndpointTakeoverIdFactory,
        owner_installation_id: str,
    ) -> None:
        self._store = store
        self._filesystem = filesystem
        self._id_factory = id_factory
        self._owner_installation_id = owner_installation_id

    def start_controlled_takeover(
        self,
        *,
        command: StartControlledEndpointTakeoverCommand,
        observed_utc: str,
    ) -> EndpointTakeoverReport:
        existing = self._store.load_takeover_intent(
            job_id=command.job_id,
            source_job_revision_id=command.job_revision_id,
            target_ordinal=command.target_ordinal,
        )
        if existing is not None:
            _require_command_matches_intent(command, existing)
            return self._resume(existing, observed_utc=observed_utc, replay=True)

        candidate = self._store.load_takeover_candidate(
            job_id=command.job_id,
            job_revision_id=command.job_revision_id,
            target_ordinal=command.target_ordinal,
            endpoint_id=command.endpoint_id,
            expected_foreign_owner_installation_id=(
                command.expected_foreign_owner_installation_id
            ),
            expected_ownership_epoch=command.expected_ownership_epoch,
        )
        ids = self._id_factory.new_takeover_ids()
        prepared = self._filesystem.prepare_controlled_takeover(
            candidate,
            intent_id=ids.intent_id,
            resulting_endpoint_revision_id=ids.resulting_endpoint_revision_id,
            owner_installation_id=self._owner_installation_id,
            created_utc=observed_utc,
        )
        intent = self._store.save_prepared_takeover_intent(
            EndpointTakeoverIntent(
                intent_id=ids.intent_id,
                job_id=command.job_id,
                source_job_revision_id=command.job_revision_id,
                resulting_job_revision_id=ids.resulting_job_revision_id,
                analysis_request_id=ids.analysis_request_id,
                command_request_id=command.request_id,
                command_idempotency_key=command.idempotency_key,
                state=EndpointTakeoverState.PREPARED,
                prepared=prepared,
                created_utc=observed_utc,
                updated_utc=observed_utc,
            )
        )
        return self._resume(intent, observed_utc=observed_utc, replay=False)

    def reconcile_pending(
        self,
        *,
        observed_utc: str,
        limit: int = 16,
    ) -> tuple[EndpointTakeoverReport, ...]:
        if limit < 1 or limit > 128:
            raise ValueError("endpoint takeover recovery limit is invalid")
        return tuple(
            self._resume(intent, observed_utc=observed_utc, replay=True)
            for intent in self._store.list_recoverable_takeover_intents(limit=limit)
        )

    def _resume(
        self,
        intent: EndpointTakeoverIntent,
        *,
        observed_utc: str,
        replay: bool,
    ) -> EndpointTakeoverReport:
        if intent.state in {
            EndpointTakeoverState.COMMITTED,
            EndpointTakeoverState.BLOCKED,
        }:
            return _report(intent, replay=replay)
        try:
            if intent.state is EndpointTakeoverState.PREPARED:
                self._filesystem.apply_prepared_takeover(
                    intent.prepared,
                    intent_id=intent.intent_id,
                )
                intent = self._store.mark_takeover_filesystem_applied(
                    intent_id=intent.intent_id,
                    updated_utc=observed_utc,
                )
            intent = self._store.commit_takeover_intent(
                intent_id=intent.intent_id,
                committed_utc=observed_utc,
            )
        except EndpointTakeoverError as exc:
            intent = self._store.note_takeover_failure(
                intent_id=intent.intent_id,
                validation_code=exc.validation_code,
                next_action=exc.next_action,
                blocked=not exc.retryable,
                updated_utc=observed_utc,
            )
        return _report(intent, replay=replay)


def parse_start_controlled_endpoint_takeover_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> StartControlledEndpointTakeoverCommand:
    expected_fields = {
        "job_id",
        "job_revision_id",
        "target_ordinal",
        "endpoint_id",
        "expected_foreign_owner_installation_id",
        "expected_ownership_epoch",
        "explicit_confirmation",
    }
    if (
        set(payload) != expected_fields
        or payload.get("explicit_confirmation") is not True
    ):
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_PAYLOAD_INVALID")
    job_id = _bounded_text(payload.get("job_id"))
    job_revision_id = _bounded_text(payload.get("job_revision_id"))
    target_ordinal = _positive_int(payload.get("target_ordinal"))
    endpoint_id = _uuid_text(payload.get("endpoint_id"))
    foreign_owner = _uuid_text(payload.get("expected_foreign_owner_installation_id"))
    ownership_epoch = _positive_int(payload.get("expected_ownership_epoch"))
    if job_id is None:
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_JOB_ID_INVALID")
    if job_revision_id is None:
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_JOB_REVISION_ID_INVALID")
    if target_ordinal is None or target_ordinal > 3:
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_TARGET_ORDINAL_INVALID")
    if endpoint_id is None:
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_ENDPOINT_ID_INVALID")
    if foreign_owner is None:
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_OWNER_ID_INVALID")
    if ownership_epoch is None:
        raise EndpointTakeoverPayloadError("ENDPOINT_TAKEOVER_OWNERSHIP_EPOCH_INVALID")
    return StartControlledEndpointTakeoverCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        job_revision_id=job_revision_id,
        target_ordinal=target_ordinal,
        endpoint_id=endpoint_id,
        expected_foreign_owner_installation_id=foreign_owner,
        expected_ownership_epoch=ownership_epoch,
    )


def _report(intent: EndpointTakeoverIntent, *, replay: bool) -> EndpointTakeoverReport:
    prepared = intent.prepared
    completed = intent.state is EndpointTakeoverState.COMMITTED
    return EndpointTakeoverReport(
        job_id=intent.job_id,
        source_job_revision_id=intent.source_job_revision_id,
        active_job_revision_id=(
            intent.resulting_job_revision_id
            if completed
            else intent.source_job_revision_id
        ),
        endpoint_id=prepared.endpoint_id,
        target_ordinal=prepared.target_ordinal,
        intent_id=intent.intent_id,
        analysis_request_id=intent.analysis_request_id,
        state=intent.state,
        old_owner_installation_id=prepared.foreign_owner_installation_id,
        old_ownership_epoch=prepared.foreign_ownership_epoch,
        new_owner_installation_id=prepared.owner_installation_id,
        new_ownership_epoch=prepared.ownership_epoch,
        idempotent_replay=replay,
        validation_codes=(
            () if intent.last_error_code is None else (intent.last_error_code,)
        ),
        next_action=intent.last_next_action,
    )


def _require_command_matches_intent(
    command: StartControlledEndpointTakeoverCommand,
    intent: EndpointTakeoverIntent,
) -> None:
    prepared = intent.prepared
    if (
        command.endpoint_id != prepared.endpoint_id
        or command.expected_foreign_owner_installation_id
        != prepared.foreign_owner_installation_id
        or command.expected_ownership_epoch != prepared.foreign_ownership_epoch
    ):
        raise EndpointTakeoverError(
            "ENDPOINT_TAKEOVER_INTENT_CONFLICT",
            "Refresh endpoint details before retrying takeover.",
            retryable=False,
        )


def _bounded_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        return None
    return value


def _uuid_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        UUID(value)
    except ValueError:
        return None
    return value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value
