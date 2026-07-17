from __future__ import annotations

from mediasync_home.ipc.client import InProcessIpcClient
from mediasync_home.ipc.protocol import IpcResponse


class EngineClient:
    def __init__(self, ipc_client: InProcessIpcClient) -> None:
        self._ipc_client = ipc_client

    def connect(self) -> IpcResponse:
        return self._ipc_client.connect()

    def get_status(self) -> IpcResponse:
        return self._ipc_client.query_status()
