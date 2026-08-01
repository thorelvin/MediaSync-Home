from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class WritableEndpointRegistrationState(str, Enum):
    PREPARED = "PREPARED"
    FILESYSTEM_APPLIED = "FILESYSTEM_APPLIED"
    COMMITTED = "COMMITTED"
    BLOCKED = "BLOCKED"


class WritableEndpointRegistrationCommandName(str, Enum):
    REGISTER_WRITABLE_TARGETS = "REGISTER_WRITABLE_TARGETS"


class WritableEndpointRegistrationPayloadError(ValueError):
    pass


class WritableEndpointRegistrationError(RuntimeError):
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
class RegisterWritableTargetsCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    job_revision_id: str


@dataclass(frozen=True, slots=True)
class WritableEndpointRegistrationCandidate:
    job_id: str
    job_revision_id: str
    target_ordinal: int
    endpoint_id: str
    endpoint_revision_id: str
    endpoint_generation: int
    display_name: str
    root_uri: str


@dataclass(frozen=True, slots=True)
class WritableEndpointTargetIds:
    target_ordinal: int
    endpoint_revision_id: str
    control_area_id: str


@dataclass(frozen=True, slots=True)
class WritableEndpointRegistrationIds:
    intent_id: str
    resulting_job_revision_id: str
    targets: tuple[WritableEndpointTargetIds, ...]


@dataclass(frozen=True, slots=True)
class PreparedWritableEndpoint:
    target_ordinal: int
    endpoint_id: str
    source_endpoint_revision_id: str
    resulting_endpoint_revision_id: str
    resulting_endpoint_generation: int
    display_name: str
    root_uri: str
    control_area_id: str
    owner_installation_id: str
    ownership_epoch: int
    root_identity_hash_algorithm: str
    root_identity_hash: str
    marker_checksum_algorithm: str
    marker_checksum: str
    marker_payload_json: str
    ownership_payload_json: str
    probe_token: str


@dataclass(frozen=True, slots=True)
class WritableEndpointRegistrationIntent:
    intent_id: str
    job_id: str
    source_job_revision_id: str
    resulting_job_revision_id: str
    command_request_id: str
    command_idempotency_key: str
    state: WritableEndpointRegistrationState
    prepared_targets: tuple[PreparedWritableEndpoint, ...]
    created_utc: str
    updated_utc: str
    last_error_code: str | None = None
    last_next_action: str | None = None


@dataclass(frozen=True, slots=True)
class WritableEndpointRegistrationReport:
    job_id: str
    source_job_revision_id: str
    active_job_revision_id: str
    intent_id: str | None
    state: WritableEndpointRegistrationState | None
    target_count: int
    registered_target_count: int
    idempotent_replay: bool
    validation_codes: tuple[str, ...] = ()
    next_action: str | None = None

    @property
    def completed(self) -> bool:
        return (
            self.state is WritableEndpointRegistrationState.COMMITTED
            or "WRITABLE_ENDPOINT_REGISTRATION_NOT_REQUIRED"
            in self.validation_codes
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "source_job_revision_id": self.source_job_revision_id,
            "active_job_revision_id": self.active_job_revision_id,
            "intent_id": self.intent_id,
            "state": None if self.state is None else self.state.value,
            "target_count": self.target_count,
            "registered_target_count": self.registered_target_count,
            "idempotent_replay": self.idempotent_replay,
            "completed": self.completed,
            "validation_codes": list(self.validation_codes),
            "next_action": self.next_action,
        }


class WritableEndpointRegistrationIdFactory(Protocol):
    def new_registration_ids(
        self,
        candidates: tuple[WritableEndpointRegistrationCandidate, ...],
    ) -> WritableEndpointRegistrationIds: ...


class WritableEndpointControlAreaProvisioner(Protocol):
    def prepare_new_control_area(
        self,
        candidate: WritableEndpointRegistrationCandidate,
        *,
        intent_id: str,
        target_ids: WritableEndpointTargetIds,
        owner_installation_id: str,
        created_utc: str,
    ) -> PreparedWritableEndpoint: ...

    def apply_prepared_control_area(
        self,
        prepared: PreparedWritableEndpoint,
        *,
        intent_id: str,
    ) -> None: ...


class WritableEndpointRegistrationStore(Protocol):
    def load_registration_intent(
        self,
        *,
        job_id: str,
        source_job_revision_id: str,
    ) -> WritableEndpointRegistrationIntent | None: ...

    def load_registration_candidates(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> tuple[WritableEndpointRegistrationCandidate, ...]: ...

    def save_prepared_registration_intent(
        self,
        intent: WritableEndpointRegistrationIntent,
    ) -> WritableEndpointRegistrationIntent: ...

    def mark_registration_filesystem_applied(
        self,
        *,
        intent_id: str,
        updated_utc: str,
    ) -> WritableEndpointRegistrationIntent: ...

    def note_registration_failure(
        self,
        *,
        intent_id: str,
        validation_code: str,
        next_action: str,
        blocked: bool,
        updated_utc: str,
    ) -> WritableEndpointRegistrationIntent: ...

    def commit_registration_intent(
        self,
        *,
        intent_id: str,
        committed_utc: str,
    ) -> WritableEndpointRegistrationIntent: ...

    def list_recoverable_registration_intents(
        self,
        *,
        limit: int,
    ) -> tuple[WritableEndpointRegistrationIntent, ...]: ...


class WritableEndpointRegistrationCoordinator:
    def __init__(
        self,
        *,
        store: WritableEndpointRegistrationStore,
        provisioner: WritableEndpointControlAreaProvisioner,
        id_factory: WritableEndpointRegistrationIdFactory,
        owner_installation_id: str,
    ) -> None:
        self._store = store
        self._provisioner = provisioner
        self._id_factory = id_factory
        self._owner_installation_id = owner_installation_id

    def register_job_targets(
        self,
        *,
        job_id: str,
        job_revision_id: str,
        command_request_id: str,
        command_idempotency_key: str,
        observed_utc: str,
    ) -> WritableEndpointRegistrationReport:
        existing = self._store.load_registration_intent(
            job_id=job_id,
            source_job_revision_id=job_revision_id,
        )
        if existing is not None:
            return self._resume(existing, observed_utc=observed_utc, replay=True)

        candidates = self._store.load_registration_candidates(
            job_id=job_id,
            job_revision_id=job_revision_id,
        )
        if not candidates:
            return WritableEndpointRegistrationReport(
                job_id=job_id,
                source_job_revision_id=job_revision_id,
                active_job_revision_id=job_revision_id,
                intent_id=None,
                state=None,
                target_count=0,
                registered_target_count=0,
                idempotent_replay=False,
                validation_codes=("WRITABLE_ENDPOINT_REGISTRATION_NOT_REQUIRED",),
                next_action="No target registration is required.",
            )

        ids = self._id_factory.new_registration_ids(candidates)
        target_ids = {target.target_ordinal: target for target in ids.targets}
        if set(target_ids) != {candidate.target_ordinal for candidate in candidates}:
            raise WritableEndpointRegistrationError(
                "WRITABLE_ENDPOINT_REGISTRATION_IDS_INVALID",
                "Retry target registration with a complete identifier allocation.",
                retryable=True,
            )
        prepared = tuple(
            self._provisioner.prepare_new_control_area(
                candidate,
                intent_id=ids.intent_id,
                target_ids=target_ids[candidate.target_ordinal],
                owner_installation_id=self._owner_installation_id,
                created_utc=observed_utc,
            )
            for candidate in candidates
        )
        intent = self._store.save_prepared_registration_intent(
            WritableEndpointRegistrationIntent(
                intent_id=ids.intent_id,
                job_id=job_id,
                source_job_revision_id=job_revision_id,
                resulting_job_revision_id=ids.resulting_job_revision_id,
                command_request_id=command_request_id,
                command_idempotency_key=command_idempotency_key,
                state=WritableEndpointRegistrationState.PREPARED,
                prepared_targets=prepared,
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
    ) -> tuple[WritableEndpointRegistrationReport, ...]:
        if limit < 1 or limit > 128:
            raise ValueError("writable endpoint registration recovery limit is invalid")
        return tuple(
            self._resume(intent, observed_utc=observed_utc, replay=True)
            for intent in self._store.list_recoverable_registration_intents(limit=limit)
        )

    def _resume(
        self,
        intent: WritableEndpointRegistrationIntent,
        *,
        observed_utc: str,
        replay: bool,
    ) -> WritableEndpointRegistrationReport:
        if intent.state is WritableEndpointRegistrationState.COMMITTED:
            return _report(intent, replay=replay)
        if intent.state is WritableEndpointRegistrationState.BLOCKED:
            return _report(intent, replay=replay)
        try:
            if intent.state is WritableEndpointRegistrationState.PREPARED:
                for prepared in intent.prepared_targets:
                    self._provisioner.apply_prepared_control_area(
                        prepared,
                        intent_id=intent.intent_id,
                    )
                intent = self._store.mark_registration_filesystem_applied(
                    intent_id=intent.intent_id,
                    updated_utc=observed_utc,
                )
            intent = self._store.commit_registration_intent(
                intent_id=intent.intent_id,
                committed_utc=observed_utc,
            )
        except WritableEndpointRegistrationError as exc:
            intent = self._store.note_registration_failure(
                intent_id=intent.intent_id,
                validation_code=exc.validation_code,
                next_action=exc.next_action,
                blocked=not exc.retryable,
                updated_utc=observed_utc,
            )
        return _report(intent, replay=replay)


def parse_register_writable_targets_command(
    *,
    request_id: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> RegisterWritableTargetsCommand:
    if set(payload) != {"job_id", "job_revision_id"}:
        raise WritableEndpointRegistrationPayloadError(
            "REGISTER_WRITABLE_TARGETS_PAYLOAD_INVALID"
        )
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
        raise WritableEndpointRegistrationPayloadError(
            "REGISTER_WRITABLE_TARGETS_JOB_ID_INVALID"
        )
    job_revision_id = payload.get("job_revision_id")
    if (
        not isinstance(job_revision_id, str)
        or not job_revision_id.strip()
        or len(job_revision_id) > 128
    ):
        raise WritableEndpointRegistrationPayloadError(
            "REGISTER_WRITABLE_TARGETS_JOB_REVISION_ID_INVALID"
        )
    return RegisterWritableTargetsCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        job_revision_id=job_revision_id,
    )


def _report(
    intent: WritableEndpointRegistrationIntent,
    *,
    replay: bool,
) -> WritableEndpointRegistrationReport:
    target_count = len(intent.prepared_targets)
    completed = intent.state is WritableEndpointRegistrationState.COMMITTED
    validation_codes: tuple[str, ...] = ()
    if intent.last_error_code is not None:
        validation_codes = (intent.last_error_code,)
    return WritableEndpointRegistrationReport(
        job_id=intent.job_id,
        source_job_revision_id=intent.source_job_revision_id,
        active_job_revision_id=(
            intent.resulting_job_revision_id if completed else intent.source_job_revision_id
        ),
        intent_id=intent.intent_id,
        state=intent.state,
        target_count=target_count,
        registered_target_count=target_count if completed else 0,
        idempotent_replay=replay,
        validation_codes=validation_codes,
        next_action=intent.last_next_action,
    )
