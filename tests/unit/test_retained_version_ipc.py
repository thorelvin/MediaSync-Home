from __future__ import annotations

from dataclasses import replace

from mediasync_home.application.command_receipts import (
    CommandReceipt,
    CommandReceiptStore,
    ensure_idempotency_compatible,
)
from mediasync_home.application.retained_version_history import (
    ProtectRetainedVersionForRestoreCommand,
    RetainedVersionCursor,
    RetainedVersionSummary,
    RestoreRetainedVersionCommand,
    UndoRetainedVersionRestoreCommand,
    VersionRestoreProtectionOutcome,
    VersionRestoreRequestOutcome,
    VersionRestoreUndoRequestOutcome,
)
from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.client_identity import (
    ClientAuthorizationPolicy,
    VerifiedClientIdentity,
)
from mediasync_home.ipc.protocol import IpcReason, IpcStatus
from mediasync_home.ipc.server import EngineHostIpcService
from mediasync_home.presentation.engine_client import EngineClient


class _VersionStore:
    def __init__(self) -> None:
        self.query_calls: list[tuple[str, int, RetainedVersionCursor | None]] = []
        self.protection_calls: list[ProtectRetainedVersionForRestoreCommand] = []
        self.restore_calls: list[RestoreRetainedVersionCommand] = []
        self.undo_calls: list[UndoRetainedVersionRestoreCommand] = []

    def list_retained_versions_for_run(
        self,
        *,
        run_id: str,
        limit: int,
        after: RetainedVersionCursor | None,
    ) -> tuple[RetainedVersionSummary, ...]:
        self.query_calls.append((run_id, limit, after))
        return (_summary(),)

    def protect_retained_version_for_restore(
        self,
        *,
        command: ProtectRetainedVersionForRestoreCommand,
        created_utc: str,
    ) -> VersionRestoreProtectionOutcome:
        self.protection_calls.append(command)
        return VersionRestoreProtectionOutcome(
            protected=True,
            validation_code="VERSION_RESTORE_PROTECTED",
            next_action="Protected.",
            version=replace(
                _summary(),
                hold_id=f"restore:{command.idempotency_key}",
                hold_reason="RESTORE_REQUESTED",
                hold_created_utc=created_utc,
            ),
            idempotent_replay=len(self.protection_calls) > 1,
        )

    def request_retained_version_restore(
        self,
        *,
        command: RestoreRetainedVersionCommand,
        created_utc: str,
    ) -> VersionRestoreRequestOutcome:
        del created_utc
        self.restore_calls.append(command)
        return VersionRestoreRequestOutcome(
            scheduled=True,
            validation_code="VERSION_RESTORE_SCHEDULED",
            next_action="Scheduled.",
            restore_id="restore-a",
            state="REQUESTED",
            version=replace(
                _summary(),
                hold_id="restore:protect-key",
                hold_reason="RESTORE_REQUESTED",
            ),
            idempotent_replay=len(self.restore_calls) > 1,
        )

    def request_retained_version_restore_undo(
        self,
        *,
        command: UndoRetainedVersionRestoreCommand,
        created_utc: str,
    ) -> VersionRestoreUndoRequestOutcome:
        del created_utc
        self.undo_calls.append(command)
        return VersionRestoreUndoRequestOutcome(
            scheduled=True,
            validation_code="VERSION_RESTORE_UNDO_SCHEDULED",
            next_action="Scheduled.",
            restore_id=command.restore_id,
            state="UNDO_REQUESTED",
            version=replace(
                _summary(),
                restore_id=command.restore_id,
                restore_state="COMPLETED",
                rollback_state="UNDO_REQUESTED",
            ),
            idempotent_replay=len(self.undo_calls) > 1,
        )


class _ReceiptStore(CommandReceiptStore):
    def __init__(self) -> None:
        self.receipts: dict[str, CommandReceipt] = {}

    def record_received(self, receipt: CommandReceipt) -> CommandReceipt:
        existing = self.receipts.get(receipt.idempotency_key)
        if existing is not None:
            return ensure_idempotency_compatible(existing, receipt)
        self.receipts[receipt.idempotency_key] = receipt
        return receipt

    def load_command_receipt(self, idempotency_key: str) -> CommandReceipt | None:
        return self.receipts.get(idempotency_key)

    def update_command_receipt(self, receipt: CommandReceipt) -> None:
        self.receipts[receipt.idempotency_key] = receipt


class _RejectingVersionStore(_VersionStore):
    def protect_retained_version_for_restore(
        self,
        *,
        command: ProtectRetainedVersionForRestoreCommand,
        created_utc: str,
    ) -> VersionRestoreProtectionOutcome:
        del created_utc
        self.protection_calls.append(command)
        return VersionRestoreProtectionOutcome(
            protected=False,
            validation_code="VERSION_RESTORE_VERSION_CHANGED",
            next_action="Refresh.",
            version=_summary(),
        )


def test_retained_version_query_requires_handshake_and_forwards_cursor() -> None:
    store = _VersionStore()
    client = _client(_service(store))
    cursor = {
        "cursor_version": 1,
        "created_utc": "2026-08-01T00:00:00.000Z",
        "version_object_id": "version-z",
    }

    before = client.query_retained_versions(run_id="run-a")
    client.connect()
    response = client.query_retained_versions(run_id="run-a", limit=1, after=cursor)

    assert before.reason is IpcReason.HANDSHAKE_REQUIRED
    assert response.status is IpcStatus.ACCEPTED
    assert store.query_calls == [
        (
            "run-a",
            2,
            RetainedVersionCursor(
                created_utc="2026-08-01T00:00:00.000Z",
                version_object_id="version-z",
            ),
        )
    ]
    assert response.payload["retained_versions"]["versions"][0][
        "version_object_id"
    ] == "version-a"


def test_engine_client_protects_version_through_durable_command_receipt() -> None:
    store = _VersionStore()
    engine = EngineClient(_client(_service(store, mutations_enabled=True)))

    response = engine.protect_retained_version_for_restore(
        version_object_id="version-a",
        expected_row_version=1,
        request_id="11111111-1111-4111-8111-111111111111",
        idempotency_key="22222222-2222-4222-8222-222222222222",
    )

    assert response.status is IpcStatus.ACCEPTED
    protection = response.payload["version_restore_protection"]
    assert protection["protected"] is True
    assert protection["version"]["protected_for_restore"] is True
    assert response.payload["receipt"]["state"] == "SUCCEEDED"
    assert len(store.protection_calls) == 1


def test_restore_protection_rejects_missing_confirmation() -> None:
    client = _client(_service(_VersionStore(), mutations_enabled=True))
    client.connect()

    response = client.submit_command(
        "PROTECT_RETAINED_VERSION_FOR_RESTORE",
        request_id="11111111-1111-4111-8111-111111111111",
        idempotency_key="22222222-2222-4222-8222-222222222222",
        payload={
            "version_object_id": "version-a",
            "expected_row_version": 1,
            "explicit_confirmation": False,
        },
        payload_hash="0" * 64,
    )

    assert response.reason is IpcReason.INVALID_FRAME


def test_engine_client_schedules_protected_version_restore() -> None:
    store = _VersionStore()
    engine = EngineClient(_client(_service(store, mutations_enabled=True)))

    response = engine.restore_retained_version(
        version_object_id="version-a",
        expected_row_version=1,
        request_id="33333333-3333-4333-8333-333333333333",
        idempotency_key="44444444-4444-4444-8444-444444444444",
    )

    assert response.status is IpcStatus.ACCEPTED
    request = response.payload["version_restore_request"]
    assert request["scheduled"] is True
    assert request["restore_id"] == "restore-a"
    assert response.payload["receipt"]["state"] == "SUCCEEDED"
    assert len(store.restore_calls) == 1


def test_engine_client_schedules_retained_version_restore_undo() -> None:
    store = _VersionStore()
    engine = EngineClient(_client(_service(store, mutations_enabled=True)))

    response = engine.undo_retained_version_restore(
        restore_id="restore-a",
        version_object_id="version-a",
        expected_row_version=1,
        request_id="55555555-5555-4555-8555-555555555555",
        idempotency_key="66666666-6666-4666-8666-666666666666",
    )

    assert response.status is IpcStatus.ACCEPTED
    request = response.payload["version_restore_undo_request"]
    assert request["scheduled"] is True
    assert request["restore_id"] == "restore-a"
    assert response.payload["receipt"]["state"] == "SUCCEEDED"
    assert store.undo_calls == [
        UndoRetainedVersionRestoreCommand(
            request_id="55555555-5555-4555-8555-555555555555",
            idempotency_key="66666666-6666-4666-8666-666666666666",
            restore_id="restore-a",
            version_object_id="version-a",
            expected_row_version=1,
            explicit_confirmation=True,
        )
    ]


def test_rejected_restore_protection_replay_never_retries_the_effect() -> None:
    store = _RejectingVersionStore()
    engine = EngineClient(_client(_service(store, mutations_enabled=True)))
    arguments = {
        "version_object_id": "version-a",
        "expected_row_version": 1,
        "request_id": "11111111-1111-4111-8111-111111111111",
        "idempotency_key": "22222222-2222-4222-8222-222222222222",
    }

    first = engine.protect_retained_version_for_restore(**arguments)
    replay = engine.protect_retained_version_for_restore(**arguments)

    assert first.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert replay.reason is IpcReason.COMMAND_PRECONDITION_FAILED
    assert len(store.protection_calls) == 1


def _service(
    store: _VersionStore,
    *,
    mutations_enabled: bool = False,
) -> EngineHostIpcService:
    service = EngineHostIpcService(
        ClientAuthorizationPolicy(
            expected_user_sid_hash="same-user",
            expected_session_id=42,
        ),
        retained_version_read_store=store,
        version_restore_protection_store=store,
        version_restore_request_store=store,
        version_restore_undo_request_store=store,
        retained_version_utc_now=lambda: "2026-08-10T00:00:00.000Z",
        command_receipt_store=_ReceiptStore(),
    )
    if mutations_enabled:
        service.status = replace(
            service.status,
            mutations_enabled=True,
            scope="LOCAL_MUTATION_PREVIEW",
        )
    return service


def _client(service: EngineHostIpcService) -> InProcessIpcClient:
    return InProcessIpcClient(
        service=service,
        identity=VerifiedClientIdentity(
            user_sid_hash="same-user",
            session_id=42,
            is_remote=False,
            transport="retained-version-ipc-test",
        ),
        role=ProcessRole.GUI,
        client_instance_id="77777777-7777-4777-8777-777777777777",
    )


def _summary() -> RetainedVersionSummary:
    return RetainedVersionSummary(
        version_object_id="version-a",
        run_id="run-a",
        operation_id="operation-a",
        job_id="job-a",
        target_endpoint_id="target-a",
        final_relative_path="Photos/image.jpg",
        created_utc="2026-08-01T00:00:00.000Z",
        retention_until_utc="2026-08-31T00:00:00.000Z",
        state="RETAINED",
        row_version=1,
    )
