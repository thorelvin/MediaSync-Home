from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediasync_home.application.plans import (
    PlanDependency,
    PlanEndpoint,
    PlanEndpointRole,
    PlanOperationCursor,
    PlanOperationPage,
    PlanOperationPageQuery,
    PlanOperationReadModel,
    PlanOperationReadModelStore,
    PlanOperation,
    PlanOperationType,
    PlanRiskLevel,
    PlanStore,
    SealedPlan,
    TargetPreconditionKind,
    validate_plan_operation_page_query,
)


class SqlitePlanStoreError(ValueError):
    pass


class SqlitePlanStore(PlanStore, PlanOperationReadModelStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_sealed_plan(self, plan: SealedPlan) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "INSERT INTO plans (id, analysis_id) VALUES (?, ?)",
                (plan.plan_id, plan.analysis_id),
            )
            for operation in plan.operations:
                self._connection.execute(
                    """
                    INSERT INTO planned_operations (plan_id, id, operation_type)
                        VALUES (?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        operation.operation_id,
                        operation.operation_type.value,
                    ),
                )
            for dependency in plan.dependencies:
                self._connection.execute(
                    """
                    INSERT INTO operation_dependencies (
                        plan_id,
                        before_operation_id,
                        after_operation_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        dependency.before_operation_id,
                        dependency.after_operation_id,
                    ),
                )
            for endpoint in plan.endpoints:
                self._connection.execute(
                    """
                    INSERT INTO plan_endpoints (
                        plan_id,
                        analysis_id,
                        endpoint_id,
                        endpoint_revision_id,
                        snapshot_id,
                        role,
                        target_ordinal,
                        capabilities_hash,
                        root_case_context_hash,
                        required_owner_installation_id,
                        required_ownership_epoch,
                        control_schema_version,
                        planned_operations,
                        planned_bytes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        plan.analysis_id,
                        endpoint.endpoint_id,
                        endpoint.endpoint_revision_id,
                        endpoint.snapshot_id,
                        endpoint.role.value,
                        endpoint.target_ordinal,
                        endpoint.capabilities_hash,
                        endpoint.root_case_context_hash,
                        endpoint.required_owner_installation_id,
                        endpoint.required_ownership_epoch,
                        endpoint.control_schema_version,
                        endpoint.planned_operations,
                        endpoint.planned_bytes,
                    ),
                )
            for operation in plan.operations:
                self._connection.execute(
                    """
                    INSERT INTO plan_operation_seal_details (
                        plan_id,
                        operation_id,
                        sequence_no,
                        execution_phase,
                        stable_order_key,
                        target_precondition_kind,
                        reason_code,
                        risk_level,
                        target_relative_path,
                        planned_bytes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        operation.operation_id,
                        operation.sequence_no,
                        operation.execution_phase,
                        operation.stable_order_key,
                        operation.target_precondition_kind.value,
                        operation.reason_code,
                        operation.risk_level.value,
                        operation.target_relative_path,
                        operation.planned_bytes,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO plan_seal_details (
                    plan_id,
                    analysis_id,
                    job_id,
                    job_revision_id,
                    parent_plan_id,
                    planner_version,
                    plan_schema_version,
                    operation_schema_version,
                    execution_policy,
                    checksum_algorithm,
                    serializer_version,
                    plan_checksum,
                    risk_summary_json,
                    operation_count,
                    planned_bytes,
                    immutable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    plan.plan_id,
                    plan.analysis_id,
                    plan.job_id,
                    plan.job_revision_id,
                    plan.parent_plan_id,
                    plan.planner_version,
                    plan.plan_schema_version,
                    plan.operation_schema_version,
                    plan.execution_policy,
                    plan.checksum_algorithm,
                    plan.serializer_version,
                    plan.plan_checksum,
                    _json_dump(plan.risk_summary),
                    plan.operation_count,
                    plan.planned_bytes,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise SqlitePlanStoreError("SEALED_PLAN_PERSISTENCE_FAILED") from exc

    def load_sealed_plan(self, plan_id: str) -> SealedPlan | None:
        row = self._connection.execute(
            """
            SELECT
                plan_id,
                analysis_id,
                job_id,
                job_revision_id,
                parent_plan_id,
                planner_version,
                plan_schema_version,
                operation_schema_version,
                execution_policy,
                checksum_algorithm,
                serializer_version,
                plan_checksum,
                risk_summary_json,
                operation_count,
                planned_bytes,
                immutable
            FROM plan_seal_details
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return SealedPlan(
            plan_id=str(row[0]),
            analysis_id=str(row[1]),
            job_id=str(row[2]),
            job_revision_id=str(row[3]),
            parent_plan_id=None if row[4] is None else str(row[4]),
            planner_version=str(row[5]),
            plan_schema_version=int(row[6]),
            operation_schema_version=int(row[7]),
            execution_policy=str(row[8]),
            checksum_algorithm=str(row[9]),
            serializer_version=str(row[10]),
            plan_checksum=str(row[11]),
            risk_summary=_json_object(str(row[12])),
            operation_count=int(row[13]),
            planned_bytes=int(row[14]),
            immutable=bool(row[15]),
            endpoints=self._load_endpoints(plan_id),
            operations=self._load_operations(plan_id),
            dependencies=self._load_dependencies(plan_id),
        )

    def page_plan_operations(self, query: PlanOperationPageQuery) -> PlanOperationPage:
        validate_plan_operation_page_query(query)
        rows = self._connection.execute(
            _plan_operation_page_sql(query.after),
            (*_plan_operation_page_parameters(query), query.limit + 1),
        ).fetchall()
        page_rows = rows[: query.limit]
        operations = tuple(
            PlanOperationReadModel(
                operation_id=str(row[0]),
                operation_type=PlanOperationType(str(row[1])),
                sequence_no=int(row[2]),
                execution_phase=int(row[3]),
                stable_order_key=str(row[4]),
                target_precondition_kind=TargetPreconditionKind(str(row[5])),
                reason_code=str(row[6]),
                risk_level=PlanRiskLevel(str(row[7])),
                target_relative_path=None if row[8] is None else str(row[8]),
                planned_bytes=int(row[9]),
            )
            for row in page_rows
        )
        has_more = len(rows) > query.limit
        return PlanOperationPage(
            plan_id=query.plan_id,
            operations=operations,
            next_cursor=_plan_operation_cursor(operations[-1]) if has_more and operations else None,
            has_more=has_more,
        )

    def _load_endpoints(self, plan_id: str) -> tuple[PlanEndpoint, ...]:
        rows = self._connection.execute(
            """
            SELECT
                endpoint_id,
                endpoint_revision_id,
                snapshot_id,
                role,
                target_ordinal,
                capabilities_hash,
                root_case_context_hash,
                required_owner_installation_id,
                required_ownership_epoch,
                control_schema_version,
                planned_operations,
                planned_bytes
            FROM plan_endpoints
            WHERE plan_id = ?
            ORDER BY role, target_ordinal, endpoint_id
            """,
            (plan_id,),
        ).fetchall()
        return tuple(
            PlanEndpoint(
                endpoint_id=str(row[0]),
                endpoint_revision_id=str(row[1]),
                snapshot_id=str(row[2]),
                role=PlanEndpointRole(str(row[3])),
                target_ordinal=None if row[4] is None else int(row[4]),
                capabilities_hash=str(row[5]),
                root_case_context_hash=str(row[6]),
                required_owner_installation_id=None if row[7] is None else str(row[7]),
                required_ownership_epoch=None if row[8] is None else int(row[8]),
                control_schema_version=None if row[9] is None else int(row[9]),
                planned_operations=int(row[10]),
                planned_bytes=int(row[11]),
            )
            for row in rows
        )

    def _load_operations(self, plan_id: str) -> tuple[PlanOperation, ...]:
        rows = self._connection.execute(
            """
            SELECT
                details.operation_id,
                operations.operation_type,
                details.sequence_no,
                details.execution_phase,
                details.stable_order_key,
                details.target_precondition_kind,
                details.reason_code,
                details.risk_level,
                details.target_relative_path,
                details.planned_bytes
            FROM plan_operation_seal_details AS details
            INNER JOIN planned_operations AS operations
                ON operations.plan_id = details.plan_id
                AND operations.id = details.operation_id
            WHERE details.plan_id = ?
            ORDER BY details.sequence_no
            """,
            (plan_id,),
        ).fetchall()
        return tuple(
            PlanOperation(
                operation_id=str(row[0]),
                operation_type=PlanOperationType(str(row[1])),
                sequence_no=int(row[2]),
                execution_phase=int(row[3]),
                stable_order_key=str(row[4]),
                target_precondition_kind=TargetPreconditionKind(str(row[5])),
                reason_code=str(row[6]),
                risk_level=PlanRiskLevel(str(row[7])),
                target_relative_path=None if row[8] is None else str(row[8]),
                planned_bytes=int(row[9]),
            )
            for row in rows
        )

    def _load_dependencies(self, plan_id: str) -> tuple[PlanDependency, ...]:
        rows = self._connection.execute(
            """
            SELECT before_operation_id, after_operation_id
            FROM operation_dependencies
            WHERE plan_id = ?
            ORDER BY before_operation_id, after_operation_id
            """,
            (plan_id,),
        ).fetchall()
        return tuple(
            PlanDependency(
                before_operation_id=str(row[0]),
                after_operation_id=str(row[1]),
            )
            for row in rows
        )


def _json_dump(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_object(payload: str) -> dict[str, object]:
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SqlitePlanStoreError("SEALED_PLAN_JSON_INVALID") from exc
    if not isinstance(data, dict):
        raise SqlitePlanStoreError("SEALED_PLAN_JSON_INVALID")
    return data


def _plan_operation_page_sql(after: PlanOperationCursor | None) -> str:
    cursor_clause = ""
    if after is not None:
        cursor_clause = """
            AND (
                details.execution_phase > ?
                OR (
                    details.execution_phase = ?
                    AND details.stable_order_key > ?
                )
                OR (
                    details.execution_phase = ?
                    AND details.stable_order_key = ?
                    AND details.operation_id > ?
                )
            )
        """
    return f"""
        SELECT
            details.operation_id,
            operations.operation_type,
            details.sequence_no,
            details.execution_phase,
            details.stable_order_key,
            details.target_precondition_kind,
            details.reason_code,
            details.risk_level,
            details.target_relative_path,
            details.planned_bytes
        FROM plan_operation_seal_details AS details
        INNER JOIN planned_operations AS operations
            ON operations.plan_id = details.plan_id
            AND operations.id = details.operation_id
        WHERE details.plan_id = ?
        {cursor_clause}
        ORDER BY
            details.execution_phase,
            details.stable_order_key,
            details.operation_id
        LIMIT ?
        """


def _plan_operation_page_parameters(query: PlanOperationPageQuery) -> tuple[object, ...]:
    parameters: list[object] = [query.plan_id]
    if query.after is not None:
        parameters.extend(
            (
                query.after.execution_phase,
                query.after.execution_phase,
                query.after.stable_order_key,
                query.after.execution_phase,
                query.after.stable_order_key,
                query.after.operation_id,
            )
        )
    return tuple(parameters)


def _plan_operation_cursor(operation: PlanOperationReadModel) -> PlanOperationCursor:
    return PlanOperationCursor(
        execution_phase=operation.execution_phase,
        stable_order_key=operation.stable_order_key,
        operation_id=operation.operation_id,
    )
