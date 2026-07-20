from __future__ import annotations

from typing import Protocol

from mediasync_home.ipc.protocol import IpcResponse


class StatusIpcClient(Protocol):
    def connect(self) -> IpcResponse:
        pass

    def query_status(self) -> IpcResponse:
        pass

    def query_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass


class EngineClient:
    def __init__(self, ipc_client: StatusIpcClient) -> None:
        self._ipc_client = ipc_client

    def connect(self) -> IpcResponse:
        return self._ipc_client.connect()

    def get_status(self) -> IpcResponse:
        return self._ipc_client.query_status()

    def get_backup_overview(
        self,
        *,
        draft_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._ipc_client.query_backup_overview(
            draft_id=draft_id,
            limit=limit,
            offset=offset,
        )
