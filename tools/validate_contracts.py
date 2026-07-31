from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "2.9.2"
REQUIRED_PACKAGES = ("jsonschema", "PyYAML")

JSON_SCHEMA_EXAMPLES = {
    "endpoint-marker.valid.json": "endpoint-marker.schema.json",
    "ipc-command.valid.json": "ipc-command.schema.json",
    "ipc-event.valid.json": "ipc-event.schema.json",
    "intent-segment-header.valid.json": "intent-segment.schema.json",
    "intent-segment-operation.valid.json": "intent-segment.schema.json",
}
EXPECTED_CONTRACT_PATHS = {
    "catalog.sql",
    "database-contract.yaml",
    "recovery.sql",
    "endpoint-marker.schema.json",
    "ipc-command.schema.json",
    "ipc-event.schema.json",
    "intent-segment.schema.json",
    "reason-codes.yaml",
    "state-machines.yaml",
}
COMMAND_RECEIPT_STATES = [
    "RECEIVED",
    "VALIDATED",
    "EFFECT_PREPARED",
    "ACCEPTED",
    "RUNNING",
    "SUCCEEDED",
    "REJECTED",
    "FAILED",
    "CANCELLED",
]
COMMAND_RECEIPT_TERMINAL = ["SUCCEEDED", "REJECTED", "FAILED", "CANCELLED"]
COMMAND_RECEIPT_TRANSITIONS = {
    "RECEIVED": ["VALIDATED", "REJECTED"],
    "VALIDATED": ["EFFECT_PREPARED", "REJECTED"],
    "EFFECT_PREPARED": ["ACCEPTED", "FAILED"],
    "ACCEPTED": ["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"],
    "RUNNING": ["SUCCEEDED", "FAILED", "CANCELLED"],
}
REQUIRED_DATABASE_PARENT_SCOPE_FKS = {
    (
        "endpoint_heads",
        ("endpoint_id", "active_revision_id"),
        "endpoint_revisions",
        ("endpoint_id", "id"),
    ),
    ("job_heads", ("job_id", "active_revision_id"), "job_revisions", ("job_id", "id")),
    ("job_revisions", ("job_id", "filter_set_id"), "filter_sets", ("job_id", "id")),
    (
        "filter_set_versions",
        ("job_id", "filter_set_id"),
        "filter_sets",
        ("job_id", "id"),
    ),
    (
        "job_revision_filter_bindings",
        ("job_id", "job_revision_id"),
        "job_revisions",
        ("job_id", "id"),
    ),
    (
        "job_revision_filter_bindings",
        ("job_id", "filter_set_id", "filter_set_version"),
        "filter_set_versions",
        ("job_id", "filter_set_id", "version"),
    ),
    (
        "writable_endpoint_registration_intents",
        ("job_id", "source_job_revision_id"),
        "job_revisions",
        ("job_id", "id"),
    ),
    (
        "writable_endpoint_registrations",
        ("endpoint_id", "endpoint_revision_id"),
        "endpoint_revisions",
        ("endpoint_id", "id"),
    ),
    ("analyses", ("job_id", "job_revision_id"), "job_revisions", ("job_id", "id")),
    (
        "standard_backup_job_snapshot_materializations",
        ("job_id", "job_revision_id"),
        "job_revisions",
        ("job_id", "id"),
    ),
    (
        "initial_backup_plan_materializations",
        ("job_id", "job_revision_id"),
        "job_revisions",
        ("job_id", "id"),
    ),
    (
        "analysis_targets",
        ("endpoint_id", "endpoint_revision_id"),
        "endpoint_revisions",
        ("endpoint_id", "id"),
    ),
    ("snapshots", ("analysis_id", "endpoint_id"), "analysis_targets", ("analysis_id", "endpoint_id")),
    (
        "snapshots",
        ("endpoint_id", "endpoint_revision_id"),
        "endpoint_revisions",
        ("endpoint_id", "id"),
    ),
    ("file_entries", ("snapshot_id", "endpoint_id"), "snapshots", ("id", "endpoint_id")),
    (
        "current_read_hash_evidence",
        ("snapshot_id", "entry_id"),
        "file_entries",
        ("snapshot_id", "id"),
    ),
    (
        "current_read_hash_evidence",
        ("snapshot_id", "endpoint_id"),
        "snapshots",
        ("id", "endpoint_id"),
    ),
    (
        "case_collision_members",
        ("snapshot_id", "file_entry_id"),
        "file_entries",
        ("snapshot_id", "id"),
    ),
    (
        "case_collision_members",
        ("snapshot_id", "group_id"),
        "case_collision_groups",
        ("snapshot_id", "id"),
    ),
    (
        "operation_dependencies",
        ("plan_id", "before_operation_id"),
        "planned_operations",
        ("plan_id", "id"),
    ),
    (
        "operation_dependencies",
        ("plan_id", "after_operation_id"),
        "planned_operations",
        ("plan_id", "id"),
    ),
    ("operation_outcomes", ("run_id", "plan_id"), "runs", ("id", "plan_id")),
    (
        "operation_outcomes",
        ("run_id", "run_target_id"),
        "run_targets",
        ("run_id", "id"),
    ),
    (
        "operation_outcomes",
        ("plan_id", "operation_id"),
        "planned_operations",
        ("plan_id", "id"),
    ),
    (
        "operation_attempts",
        ("run_attempt_id", "run_id"),
        "run_attempts",
        ("id", "run_id"),
    ),
    ("operation_attempts", ("run_id", "plan_id"), "runs", ("id", "plan_id")),
    (
        "operation_attempts",
        ("run_id", "run_target_id"),
        "run_targets",
        ("run_id", "id"),
    ),
    (
        "operation_attempts",
        ("plan_id", "operation_id"),
        "planned_operations",
        ("plan_id", "id"),
    ),
}


class ContractValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationSummary:
    contracts: int
    json_schemas: int
    database_invariants: int
    examples: int
    reason_codes: int
    state_machines: int


def fail(message: str) -> None:
    raise ContractValidationError(message)


def require_dependencies() -> tuple[Any, Any, Any]:
    for distribution in REQUIRED_PACKAGES:
        try:
            importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            fail(
                f"required validation dependency {distribution} is missing; "
                "run: python -m pip install -r requirements-handoff.txt"
            )

    import yaml  # type: ignore[import-not-found]
    from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-not-found]

    return yaml, Draft202012Validator, FormatChecker


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        fail(f"UTF-8 BOM is not allowed: {path}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"invalid UTF-8 in {path}: {exc}")
    raise AssertionError("unreachable")


def load_json(path: Path) -> Any:
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON {path}: {exc}")
    raise AssertionError("unreachable")


def load_yaml(path: Path, yaml: Any) -> Any:
    try:
        return yaml.safe_load(read_text(path))
    except Exception as exc:  # noqa: BLE001 - validation utility
        fail(f"invalid YAML {path}: {exc}")
    raise AssertionError("unreachable")


def require_mapping(document: Any, label: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        fail(f"{label} must be a mapping")
    return document


def validate_manifest(root: Path, manifest: dict[str, Any], catalog: dict[str, Any]) -> int:
    if manifest.get("bundle_version") != EXPECTED_VERSION:
        fail("contracts manifest bundle_version mismatch")
    if manifest.get("status") not in {"draft", "candidate", "blocked", "frozen"}:
        fail("contracts manifest status is invalid")
    if manifest.get("owner_acceptance_required") is not True:
        fail("contracts manifest must require owner acceptance")

    allowed_statuses = set(manifest.get("allowed_contract_statuses", []))
    if allowed_statuses != {"draft", "candidate", "blocked", "frozen"}:
        fail("contracts manifest allowed_contract_statuses drifted")

    adrs = require_mapping(catalog, "ADR catalog").get("adrs", [])
    adr_decisions = {item.get("id"): item.get("owner_decision") for item in adrs}
    if len(adr_decisions) != 28:
        fail("ADR catalog must contain the complete ADR-001..ADR-028 set")

    contracts = manifest.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        fail("contracts manifest must list contracts")
    paths = [contract.get("path") for contract in contracts]
    if set(paths) != EXPECTED_CONTRACT_PATHS:
        fail(f"contracts manifest path set drifted: {sorted(paths)}")
    if len(paths) != len(set(paths)):
        fail("contracts manifest has duplicate contract paths")

    for contract in contracts:
        path = contract.get("path")
        status = contract.get("status")
        governing_adrs = contract.get("governing_adrs")
        if status not in allowed_statuses:
            fail(f"invalid status for contract {path}")
        if not isinstance(governing_adrs, list) or not governing_adrs:
            fail(f"contract {path} must list governing ADRs")
        unknown = sorted(set(governing_adrs) - set(adr_decisions))
        if unknown:
            fail(f"contract {path} references unknown ADRs: {unknown}")
        if not (root / "schema" / str(path)).is_file():
            fail(f"contract path does not exist: schema/{path}")
        if status == "frozen":
            not_accepted = [
                adr for adr in governing_adrs if adr_decisions.get(adr) != "OWNER_ACCEPTED"
            ]
            if not_accepted:
                fail(f"frozen contract {path} has unaccepted governing ADRs: {not_accepted}")
        if status in {"candidate", "frozen"} and not contract.get("freeze_after"):
            fail(f"candidate/frozen contract {path} must document freeze_after")

    return len(contracts)


def validate_json_schemas(
    root: Path,
    Draft202012Validator: Any,
    FormatChecker: Any,
) -> tuple[int, int]:
    schema_dir = root / "schema"
    schemas: dict[str, Any] = {}
    for path in sorted(schema_dir.glob("*.schema.json")):
        schema = load_json(path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001 - validation utility
            fail(f"invalid JSON Schema {path.relative_to(root)}: {exc}")
        if not str(schema.get("$id", "")).startswith("urn:mediasync-home:draft:"):
            fail(f"schema {path.name} must use a draft MediaSync urn")
        schemas[path.name] = schema

    if set(schemas) != {
        "endpoint-marker.schema.json",
        "ipc-command.schema.json",
        "ipc-event.schema.json",
        "intent-segment.schema.json",
    }:
        fail(f"JSON Schema set drifted: {sorted(schemas)}")

    examples_dir = schema_dir / "examples"
    actual_examples = {path.name for path in examples_dir.glob("*.json")}
    if actual_examples != set(JSON_SCHEMA_EXAMPLES):
        fail(f"schema example set drifted: {sorted(actual_examples)}")

    for example_name, schema_name in JSON_SCHEMA_EXAMPLES.items():
        instance = load_json(examples_dir / example_name)
        validator = Draft202012Validator(schemas[schema_name], format_checker=FormatChecker())
        try:
            validator.validate(instance)
        except Exception as exc:  # noqa: BLE001 - validation utility
            fail(f"schema example {example_name} does not validate against {schema_name}: {exc}")

    return len(schemas), len(JSON_SCHEMA_EXAMPLES)


def validate_reason_codes(document: dict[str, Any]) -> int:
    if document.get("schema_version") != 1:
        fail("reason-codes.yaml schema_version must be 1")
    if document.get("status") != "draft_non_exhaustive":
        fail("reason-codes.yaml must remain draft_non_exhaustive in 0B preflight")
    codes = document.get("codes")
    if not isinstance(codes, list) or not codes:
        fail("reason-codes.yaml must contain codes")

    seen: set[str] = set()
    allowed_severities = {
        "BLOCKED",
        "DEFERRED",
        "FATAL",
        "PLAN_STALE",
        "RECOVERY_REQUIRED",
        "RESTRICTED",
    }
    for item in codes:
        if not isinstance(item, dict):
            fail("reason code entries must be mappings")
        code = item.get("code")
        category = item.get("category")
        severity = item.get("severity")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", code):
            fail(f"invalid reason code identifier: {code!r}")
        if code in seen:
            fail(f"duplicate reason code: {code}")
        seen.add(code)
        if not isinstance(category, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", category):
            fail(f"invalid category for reason code {code}: {category!r}")
        if severity not in allowed_severities:
            fail(f"invalid severity for reason code {code}: {severity!r}")

    return len(codes)


def _unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        fail(f"duplicate value in {label}")


def _state_set(machine: dict[str, Any]) -> set[str]:
    states: set[str] = set()
    for field in ("states", "terminal", "linear_success_path", "optional_states"):
        values = machine.get(field)
        if isinstance(values, list):
            states.update(value for value in values if isinstance(value, str))
    transitions = machine.get("transitions")
    if isinstance(transitions, dict):
        states.update(key for key in transitions if isinstance(key, str))
    return states


def _indexed_mappings(values: list[Any], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            fail(f"{label} entries must be mappings")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            fail(f"{label} entries must have non-empty id")
        if item_id in result:
            fail(f"duplicate id in {label}: {item_id}")
        result[item_id] = item
    return result


def _column_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        fail(f"{label} must be a non-empty column list")
    if not all(isinstance(item, str) and item for item in value):
        fail(f"{label} must contain only non-empty column names")
    return tuple(value)


def _column_tuple_set(value: Any, label: str) -> set[tuple[str, ...]]:
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    return {_column_tuple(item, f"{label}[]") for item in value}


def _foreign_key_tuple(value: Any, label: str) -> tuple[str, tuple[str, ...], str, tuple[str, ...]]:
    mapping = require_mapping(value, label)
    child_table = mapping.get("child_table")
    parent_table = mapping.get("parent_table")
    if not isinstance(child_table, str) or not child_table:
        fail(f"{label}.child_table must be a non-empty string")
    if not isinstance(parent_table, str) or not parent_table:
        fail(f"{label}.parent_table must be a non-empty string")
    child_columns = _column_tuple(mapping.get("child_columns"), f"{label}.child_columns")
    parent_columns = _column_tuple(mapping.get("parent_columns"), f"{label}.parent_columns")
    if len(child_columns) < 2 or len(parent_columns) < 2:
        fail(f"{label} must be a composite foreign key")
    if len(child_columns) != len(parent_columns):
        fail(f"{label} child and parent column counts must match")
    return child_table, child_columns, parent_table, parent_columns


def _validate_case_collision_invariant(invariant: dict[str, Any]) -> None:
    if invariant.get("requirement_id") != "DB-001":
        fail("DB-001 invariant must reference DB-001")
    if invariant.get("table") != "file_entries":
        fail("DB-001 invariant must apply to file_entries")
    unique_keys = _column_tuple_set(invariant.get("must_have_unique_keys"), "DB-001 must_have_unique_keys")
    non_unique_indexes = _column_tuple_set(
        invariant.get("must_have_non_unique_indexes"),
        "DB-001 must_have_non_unique_indexes",
    )
    forbidden_unique_keys = _column_tuple_set(
        invariant.get("must_not_have_unique_keys"),
        "DB-001 must_not_have_unique_keys",
    )
    comparison_key = ("snapshot_id", "comparison_key")
    if comparison_key in unique_keys:
        fail("DB-001 forbids unique file_entries(snapshot_id, comparison_key)")
    if comparison_key not in non_unique_indexes:
        fail("DB-001 requires non-unique file_entries(snapshot_id, comparison_key) index")
    if comparison_key not in forbidden_unique_keys:
        fail("DB-001 must explicitly forbid unique file_entries(snapshot_id, comparison_key)")
    required_unique = {("snapshot_id", "relative_path"), ("snapshot_id", "id")}
    missing_unique = sorted(required_unique - unique_keys)
    if missing_unique:
        fail(f"DB-001 missing required file_entries unique key(s): {missing_unique}")

    collision_tables = require_mapping(invariant.get("collision_tables"), "DB-001 collision_tables")
    if collision_tables.get("groups") != "case_collision_groups":
        fail("DB-001 must keep case_collision_groups")
    if collision_tables.get("members") != "case_collision_members":
        fail("DB-001 must keep case_collision_members")


def _validate_head_table_invariant(
    invariant: dict[str, Any],
    *,
    stable_table: str,
    revision_table: str,
    head_table: str,
    child_columns: tuple[str, ...],
    parent_columns: tuple[str, ...],
) -> None:
    if invariant.get("requirement_id") != "DB-006":
        fail(f"{head_table} invariant must reference DB-006")
    if invariant.get("stable_table") != stable_table:
        fail(f"{head_table} invariant must keep stable table {stable_table}")
    if invariant.get("revision_table") != revision_table:
        fail(f"{head_table} invariant must keep revision table {revision_table}")
    if invariant.get("head_table") != head_table:
        fail(f"{head_table} invariant must keep separate head table {head_table}")

    head_fk = require_mapping(invariant.get("head_fk"), f"{head_table}.head_fk")
    if _column_tuple(head_fk.get("child_columns"), f"{head_table}.head_fk.child_columns") != child_columns:
        fail(f"{head_table} active head FK child columns drifted")
    if head_fk.get("parent_table") != revision_table:
        fail(f"{head_table} active head FK parent table drifted")
    if _column_tuple(head_fk.get("parent_columns"), f"{head_table}.head_fk.parent_columns") != parent_columns:
        fail(f"{head_table} active head FK parent columns drifted")


def _validate_parent_scope_invariant(invariant: dict[str, Any]) -> None:
    if invariant.get("requirement_id") != "DB-007":
        fail("DB-007 invariant must reference DB-007")
    raw_foreign_keys = invariant.get("required_composite_foreign_keys")
    if not isinstance(raw_foreign_keys, list):
        fail("DB-007 required_composite_foreign_keys must be a list")
    actual = {
        _foreign_key_tuple(item, "DB-007 required_composite_foreign_keys[]")
        for item in raw_foreign_keys
    }
    missing = sorted(REQUIRED_DATABASE_PARENT_SCOPE_FKS - actual)
    if missing:
        fail(f"DB-007 missing required composite foreign key(s): {missing}")


def _validate_immutable_revision_invariant(invariant: dict[str, Any]) -> None:
    if invariant.get("requirement_id") != "ARC-005":
        fail("immutable revision invariant must reference ARC-005")

    immutable_tables = set(
        _column_tuple(
            invariant.get("always_immutable_tables"),
            "ARC-005 always_immutable_tables",
        )
    )
    expected_immutable_tables = {
        "endpoint_revisions",
        "filter_set_versions",
        "job_revision_filter_bindings",
        "job_revisions",
        "standard_backup_job_revision_details",
        "writable_endpoint_registrations",
        "current_read_hash_evidence",
        "operation_attempts",
        "operation_outcomes",
    }
    if immutable_tables != expected_immutable_tables:
        fail("ARC-005 always immutable tables drifted")

    after_reference = require_mapping(
        invariant.get("immutable_after_reference"),
        "ARC-005 immutable_after_reference",
    )
    if after_reference.get("table") != "filter_sets":
        fail("ARC-005 must protect filter_sets after reference")
    if after_reference.get("referenced_by_table") != "job_revisions":
        fail("ARC-005 filter_sets reference owner drifted")
    if _column_tuple(
        after_reference.get("reference_columns"),
        "ARC-005 immutable_after_reference.reference_columns",
    ) != ("job_id", "filter_set_id"):
        fail("ARC-005 filter_sets reference columns drifted")
    if _column_tuple(
        after_reference.get("identity_columns"),
        "ARC-005 immutable_after_reference.identity_columns",
    ) != ("job_id", "id"):
        fail("ARC-005 filter_sets identity columns drifted")

    identity_guard = require_mapping(
        invariant.get("identity_guard"),
        "ARC-005 identity_guard",
    )
    if identity_guard.get("table") != "standard_backup_job_endpoint_bindings":
        fail("ARC-005 endpoint binding guard table drifted")
    immutable_columns = set(
        _column_tuple(
            identity_guard.get("immutable_columns"),
            "ARC-005 identity_guard.immutable_columns",
        )
    )
    if immutable_columns != {
        "job_id",
        "job_revision_id",
        "role",
        "ordinal",
        "endpoint_id",
        "endpoint_revision_id",
        "created_utc",
    }:
        fail("ARC-005 endpoint binding immutable columns drifted")
    mutable_columns = set(
        _column_tuple(
            identity_guard.get("mutable_columns"),
            "ARC-005 identity_guard.mutable_columns",
        )
    )
    if mutable_columns != {"registration_state", "registration_reason_code"}:
        fail("ARC-005 endpoint binding mutable columns drifted")

    mutable_heads = set(
        _column_tuple(
            invariant.get("mutable_head_tables"),
            "ARC-005 mutable_head_tables",
        )
    )
    if mutable_heads != {"endpoint_heads", "job_heads"}:
        fail("ARC-005 mutable head tables drifted")

    endpoint_generation = require_mapping(
        invariant.get("endpoint_generation"),
        "ARC-005 endpoint_generation",
    )
    if endpoint_generation.get("revision_table") != "endpoint_revisions":
        fail("ARC-005 endpoint generation revision table drifted")
    if endpoint_generation.get("generation_column") != "generation":
        fail("ARC-005 endpoint generation column drifted")
    if endpoint_generation.get("positive") is not True:
        fail("ARC-005 endpoint generation must remain positive")
    if endpoint_generation.get("monotonic_insert") is not True:
        fail("ARC-005 endpoint generation must advance monotonically")
    raw_bindings = endpoint_generation.get("exact_bindings")
    if not isinstance(raw_bindings, list):
        fail("ARC-005 endpoint generation exact_bindings must be a list")
    bindings = {
        (
            item.get("table"),
            _column_tuple(
                item.get("columns"),
                "ARC-005 endpoint_generation.exact_bindings[].columns",
            ),
        )
        for item in raw_bindings
        if isinstance(item, dict)
    }
    if bindings != {
        (
            "snapshots",
            ("endpoint_id", "endpoint_revision_id", "endpoint_generation"),
        ),
        (
            "plan_endpoints",
            ("endpoint_id", "endpoint_revision_id", "endpoint_generation"),
        ),
    }:
        fail("ARC-005 endpoint generation exact bindings drifted")
    if endpoint_generation.get("runtime_capability") != "MutationPermit":
        fail("ARC-005 endpoint generation runtime capability drifted")


def _validate_writable_endpoint_registration_invariant(
    invariant: dict[str, Any],
) -> None:
    if invariant.get("requirement_id") != "CTRL-001":
        fail("writable endpoint registration invariant must reference CTRL-001")
    if invariant.get("intent_table") != "writable_endpoint_registration_intents":
        fail("CTRL-001 writable endpoint registration intent table drifted")
    if invariant.get("evidence_table") != "writable_endpoint_registrations":
        fail("CTRL-001 writable endpoint registration evidence table drifted")
    if invariant.get("intent_identity_immutable") is not True:
        fail("CTRL-001 registration intent identity must be immutable")
    if invariant.get("evidence_immutable") is not True:
        fail("CTRL-001 registration evidence must be immutable")
    if set(
        _column_tuple(
            invariant.get("terminal_states"),
            "CTRL-001 terminal_states",
        )
    ) != {"COMMITTED", "BLOCKED"}:
        fail("CTRL-001 registration terminal states drifted")
    endpoint_binding = require_mapping(
        invariant.get("endpoint_revision_binding"),
        "CTRL-001 endpoint_revision_binding",
    )
    if _column_tuple(
        endpoint_binding.get("columns"),
        "CTRL-001 endpoint_revision_binding.columns",
    ) != ("endpoint_id", "endpoint_revision_id", "endpoint_generation"):
        fail("CTRL-001 registration endpoint revision binding drifted")
    if set(
        _column_tuple(
            invariant.get("active_heads_advanced"),
            "CTRL-001 active_heads_advanced",
        )
    ) != {"endpoint_heads", "job_heads"}:
        fail("CTRL-001 registration active heads drifted")


def _validate_initial_backup_plan_materialization_invariant(
    invariant: dict[str, Any],
) -> None:
    if invariant.get("requirement_id") != "SYNC-002":
        fail("initial backup plan materialization invariant must reference SYNC-002")
    if invariant.get("materialization_table") != "initial_backup_plan_materializations":
        fail("initial backup plan materialization must use its catalog evidence table")
    if set(
        _column_tuple(
            invariant.get("terminal_states"),
            "SYNC-002 terminal_states",
        )
    ) != {"SEALED", "NO_CHANGES"}:
        fail("initial backup plan terminal states must be SEALED and NO_CHANGES")
    if invariant.get("terminal_immutable") is not True:
        fail("initial backup plan terminal evidence must be immutable")
    bindings = require_mapping(
        invariant.get("exact_bindings"),
        "SYNC-002 exact_bindings",
    )
    if _column_tuple(
        bindings.get("active_revision_columns"),
        "SYNC-002 active_revision_columns",
    ) != ("job_id", "job_revision_id"):
        fail("initial backup plan must bind the exact active job revision")
    if bindings.get("analysis_column") != "analysis_id":
        fail("initial backup plan must bind its sealed analysis")
    if bindings.get("plan_column") != "plan_id":
        fail("initial backup plan must bind its sealed plan")
    if invariant.get("execution_requires_explicit_start") is not True:
        fail("initial backup plan materialization must not start a run automatically")


def _validate_current_read_hash_evidence_invariant(
    invariant: dict[str, Any],
) -> None:
    if invariant.get("requirement_id") != "HASH-001":
        fail("current-read hash invariant must reference HASH-001")
    if invariant.get("table") != "current_read_hash_evidence":
        fail("HASH-001 current-read hash evidence table drifted")
    if invariant.get("evidence_kind") != "CURRENT_READ_HASH":
        fail("HASH-001 current-read evidence kind drifted")
    if invariant.get("algorithm") != "BLAKE3-256":
        fail("HASH-001 current-read hash algorithm drifted")
    if invariant.get("hash_schema_version") != 1:
        fail("HASH-001 current-read hash schema version drifted")
    if invariant.get("fingerprint_must_be_stable") is not True:
        fail("HASH-001 current-read fingerprints must remain stable")
    if invariant.get("immutable") is not True:
        fail("HASH-001 current-read hash evidence must be immutable")


def _validate_source_file_precondition_invariant(
    invariant: dict[str, Any],
) -> None:
    if invariant.get("requirement_id") != "SRC-001":
        fail("source-file precondition invariant must reference SRC-001")
    expected = {
        "snapshot_table": "file_entries",
        "snapshot_identity_column": "identity_fingerprint_hash",
        "snapshot_schema_version": 2,
        "plan_table": "plan_operation_seal_details",
        "plan_source_path_column": "source_relative_path",
        "plan_precondition_column": "source_precondition_json",
        "operation_schema_version": 3,
        "recovery_table": "recovery_operations",
        "recovery_precondition_column": "source_precondition_json",
        "precondition_schema_version": 1,
        "checksum_bound": True,
        "before_and_after_transfer_validation": True,
    }
    drifted = sorted(
        key for key, expected_value in expected.items()
        if invariant.get(key) != expected_value
    )
    if drifted:
        fail(f"SRC-001 source-file precondition contract drifted: {drifted}")


def validate_state_machines(document: dict[str, Any]) -> int:
    if document.get("schema_version") != 1:
        fail("state-machines.yaml schema_version must be 1")
    if document.get("status") != "draft":
        fail("state-machines.yaml must remain draft in 0B preflight")
    machines = document.get("machines")
    if not isinstance(machines, dict) or not machines:
        fail("state-machines.yaml must contain machines")

    for machine_name, machine in machines.items():
        if not isinstance(machine, dict):
            fail(f"state machine {machine_name} must be a mapping")
        known_states = _state_set(machine)
        if not known_states:
            fail(f"state machine {machine_name} has no states")
        for field in ("states", "terminal", "linear_success_path", "optional_states"):
            values = machine.get(field)
            if isinstance(values, list):
                _unique(values, f"{machine_name}.{field}")
                unknown = sorted(value for value in values if value not in known_states)
                if unknown:
                    fail(f"unknown state(s) in {machine_name}.{field}: {unknown}")
        initial = machine.get("initial")
        if initial is not None and initial not in known_states:
            fail(f"state machine {machine_name} has unknown initial state {initial!r}")
        transitions = machine.get("transitions")
        if isinstance(transitions, dict):
            for source, targets in transitions.items():
                if source not in known_states:
                    fail(f"state machine {machine_name} transition source is unknown: {source}")
                if not isinstance(targets, list):
                    fail(f"state machine {machine_name}.{source} transitions must be a list")
                _unique(targets, f"{machine_name}.transitions.{source}")
                unknown_targets = sorted(target for target in targets if target not in known_states)
                if unknown_targets:
                    fail(
                        f"state machine {machine_name}.{source} has unknown transition "
                        f"target(s): {unknown_targets}"
                    )

    command = machines.get("command_receipt")
    if command.get("initial") != "RECEIVED":
        fail("command_receipt initial state must be RECEIVED")
    if command.get("states") != COMMAND_RECEIPT_STATES:
        fail("command_receipt states drifted from the canonical order")
    if command.get("terminal") != COMMAND_RECEIPT_TERMINAL:
        fail("command_receipt terminal states drifted")
    if command.get("transitions") != COMMAND_RECEIPT_TRANSITIONS:
        fail("command_receipt transitions drifted")
    if command.get("monotonic") is not True:
        fail("command_receipt must be monotonic")

    return len(machines)


def validate_database_contract(document: dict[str, Any]) -> int:
    if document.get("schema_version") != 1:
        fail("database-contract.yaml schema_version must be 1")
    if document.get("status") != "draft_non_authoritative_0b":
        fail("database-contract.yaml must remain draft_non_authoritative_0b in 0B")

    stores = require_mapping(document.get("stores"), "database-contract.yaml stores")
    if set(stores) != {"catalog", "recovery"}:
        fail("database-contract.yaml must describe catalog and recovery stores")
    for store_name, store in stores.items():
        store_mapping = require_mapping(store, f"database-contract.yaml stores.{store_name}")
        if store_mapping.get("writable_owner") != "engine_host":
            fail(f"database store {store_name} must be owned by engine_host")

    invariants = document.get("invariants")
    if not isinstance(invariants, list) or not invariants:
        fail("database-contract.yaml must contain invariants")
    invariant_by_id = _indexed_mappings(invariants, "database-contract.yaml invariants")
    required_ids = {
        "ARC-005_IMMUTABLE_REVISION_GUARDS",
        "CTRL-001_WRITABLE_ENDPOINT_REGISTRATION",
        "DB-001_FILE_ENTRIES_COMPARISON_KEY_IS_NON_UNIQUE",
        "DB-006_ENDPOINT_HEADS_ARE_SEPARATE",
        "DB-006_JOB_HEADS_ARE_SEPARATE",
        "DB-007_PARENT_SCOPE_COMPOSITE_KEYS",
        "HASH-001_CURRENT_READ_HASH_EVIDENCE",
        "SRC-001_SOURCE_FILE_PRECONDITION",
        "SYNC-002_INITIAL_BACKUP_PLAN_MATERIALIZATION",
    }
    missing = sorted(required_ids - set(invariant_by_id))
    if missing:
        fail(f"database-contract.yaml missing required invariant(s): {missing}")

    _validate_case_collision_invariant(
        invariant_by_id["DB-001_FILE_ENTRIES_COMPARISON_KEY_IS_NON_UNIQUE"]
    )
    _validate_head_table_invariant(
        invariant_by_id["DB-006_ENDPOINT_HEADS_ARE_SEPARATE"],
        stable_table="endpoints",
        revision_table="endpoint_revisions",
        head_table="endpoint_heads",
        child_columns=("endpoint_id", "active_revision_id"),
        parent_columns=("endpoint_id", "id"),
    )
    _validate_head_table_invariant(
        invariant_by_id["DB-006_JOB_HEADS_ARE_SEPARATE"],
        stable_table="jobs",
        revision_table="job_revisions",
        head_table="job_heads",
        child_columns=("job_id", "active_revision_id"),
        parent_columns=("job_id", "id"),
    )
    _validate_immutable_revision_invariant(
        invariant_by_id["ARC-005_IMMUTABLE_REVISION_GUARDS"]
    )
    _validate_writable_endpoint_registration_invariant(
        invariant_by_id["CTRL-001_WRITABLE_ENDPOINT_REGISTRATION"]
    )
    _validate_initial_backup_plan_materialization_invariant(
        invariant_by_id["SYNC-002_INITIAL_BACKUP_PLAN_MATERIALIZATION"]
    )
    _validate_current_read_hash_evidence_invariant(
        invariant_by_id["HASH-001_CURRENT_READ_HASH_EVIDENCE"]
    )
    _validate_source_file_precondition_invariant(
        invariant_by_id["SRC-001_SOURCE_FILE_PRECONDITION"]
    )
    _validate_parent_scope_invariant(invariant_by_id["DB-007_PARENT_SCOPE_COMPOSITE_KEYS"])
    return len(invariants)


def validate_blocked_sql_placeholders(root: Path, manifest: dict[str, Any]) -> None:
    blocked_sql = [
        contract.get("path")
        for contract in manifest.get("contracts", [])
        if str(contract.get("path", "")).endswith(".sql")
    ]
    for relative in blocked_sql:
        path = root / "schema" / str(relative)
        text = read_text(path)
        if "STATUS: BLOCKED PLACEHOLDER" not in text:
            fail(f"SQL contract {relative} must remain an explicit blocked placeholder")
        if re.search(r"^\s*CREATE\s+(TABLE|INDEX|TRIGGER)\b", text, flags=re.IGNORECASE | re.MULTILINE):
            fail(f"SQL contract {relative} must not contain executable DDL in 0B preflight")


def validate_repository(root: Path = ROOT) -> ValidationSummary:
    yaml, Draft202012Validator, FormatChecker = require_dependencies()
    manifest = require_mapping(
        load_yaml(root / "schema/contracts-manifest.yaml", yaml),
        "contracts manifest",
    )
    catalog = require_mapping(load_yaml(root / "docs/adr/catalog.yaml", yaml), "ADR catalog")
    reason_codes = require_mapping(
        load_yaml(root / "schema/reason-codes.yaml", yaml),
        "reason-codes.yaml",
    )
    state_machines = require_mapping(
        load_yaml(root / "schema/state-machines.yaml", yaml),
        "state-machines.yaml",
    )
    database_contract = require_mapping(
        load_yaml(root / "schema/database-contract.yaml", yaml),
        "database-contract.yaml",
    )

    contracts = validate_manifest(root, manifest, catalog)
    json_schemas, examples = validate_json_schemas(root, Draft202012Validator, FormatChecker)
    database_invariants = validate_database_contract(database_contract)
    reason_count = validate_reason_codes(reason_codes)
    machine_count = validate_state_machines(state_machines)
    validate_blocked_sql_placeholders(root, manifest)
    return ValidationSummary(
        contracts=contracts,
        json_schemas=json_schemas,
        database_invariants=database_invariants,
        examples=examples,
        reason_codes=reason_count,
        state_machines=machine_count,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MediaSync Home 0B draft contracts")
    parser.add_argument("--root", default=str(ROOT), help="repository root to validate")
    args = parser.parse_args()

    try:
        summary = validate_repository(Path(args.root))
    except ContractValidationError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("PASS: MediaSync Home draft contract validation completed")
    print(f"INFO: contracts={summary.contracts}")
    print(f"INFO: json_schemas={summary.json_schemas}")
    print(f"INFO: database_invariants={summary.database_invariants}")
    print(f"INFO: examples={summary.examples}")
    print(f"INFO: reason_codes={summary.reason_codes}")
    print(f"INFO: state_machines={summary.state_machines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
