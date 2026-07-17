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


class ContractValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationSummary:
    contracts: int
    json_schemas: int
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

    contracts = validate_manifest(root, manifest, catalog)
    json_schemas, examples = validate_json_schemas(root, Draft202012Validator, FormatChecker)
    reason_count = validate_reason_codes(reason_codes)
    machine_count = validate_state_machines(state_machines)
    validate_blocked_sql_placeholders(root, manifest)
    return ValidationSummary(
        contracts=contracts,
        json_schemas=json_schemas,
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
    print(f"INFO: examples={summary.examples}")
    print(f"INFO: reason_codes={summary.reason_codes}")
    print(f"INFO: state_machines={summary.state_machines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
