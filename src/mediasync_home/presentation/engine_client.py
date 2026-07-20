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

    def query_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        pass

    def query_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        pass

    def query_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        pass

    def query_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        pass

    def query_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        pass

    def query_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
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

    def get_backup_job_detail(self, *, job_id: str) -> IpcResponse:
        return self._ipc_client.query_backup_job_detail(job_id=job_id)

    def get_activity_overview(
        self,
        *,
        job_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._ipc_client.query_activity_overview(
            job_id=job_id,
            limit=limit,
            offset=offset,
        )

    def get_plan_operations(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._ipc_client.query_plan_operations(
            plan_id=plan_id,
            limit=limit,
            after=after,
        )

    def get_plan_endpoints(
        self,
        *,
        plan_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._ipc_client.query_plan_endpoints(
            plan_id=plan_id,
            limit=limit,
            after=after,
        )

    def get_snapshot_entries(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
    ) -> IpcResponse:
        return self._ipc_client.query_snapshot_entries(
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
        )

    def get_snapshot_coverage(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        coverage_states: tuple[str, ...] = (),
    ) -> IpcResponse:
        return self._ipc_client.query_snapshot_coverage(
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
            coverage_states=coverage_states,
        )

    def get_snapshot_issues(
        self,
        *,
        snapshot_id: str,
        limit: int | None = None,
        after: dict[str, object] | None = None,
        blocking_only: bool = False,
    ) -> IpcResponse:
        return self._ipc_client.query_snapshot_issues(
            snapshot_id=snapshot_id,
            limit=limit,
            after=after,
            blocking_only=blocking_only,
        )

    def get_cataloged_files(
        self,
        *,
        run_id: str | None = None,
        target_endpoint_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> IpcResponse:
        return self._ipc_client.query_cataloged_files(
            run_id=run_id,
            target_endpoint_id=target_endpoint_id,
            limit=limit,
            offset=offset,
        )
