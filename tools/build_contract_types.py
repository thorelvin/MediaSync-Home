from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
STATE_MACHINES_PATH = ROOT / "schema" / "state-machines.yaml"
REASON_CODES_PATH = ROOT / "schema" / "reason-codes.yaml"
PYTHON_OUTPUT_PATH = ROOT / "src" / "mediasync_home" / "generated" / "contract_types.py"
DOCUMENT_OUTPUT_PATH = ROOT / "docs" / "generated" / "CONTRACT_TYPES.md"

MACHINE_CLASS_NAMES = {
    "operation_commit": "RecoveryOperationPhase",
    "cross_store_handoff": "CrossStoreHandoffState",
    "endpoint_ownership": "EndpointOwnershipState",
    "directory_create": "DirectoryCreateState",
    "command_receipt": "CommandReceiptState",
}
STATE_FIELDS = ("states", "linear_success_path", "optional_states", "terminal")
IDENTIFIER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ContractTypeGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedOutputs:
    python: str
    documentation: str


def build_outputs() -> GeneratedOutputs:
    state_document = _load_yaml(STATE_MACHINES_PATH)
    reason_document = _load_yaml(REASON_CODES_PATH)
    machines = _machine_states(state_document)
    reasons = _reason_codes(reason_document)
    source_hash = _source_hash()
    return GeneratedOutputs(
        python=_render_python(
            machines=machines,
            state_document=state_document,
            reasons=reasons,
            source_hash=source_hash,
        ),
        documentation=_render_documentation(
            machines=machines,
            reasons=reasons,
            source_hash=source_hash,
        ),
    )


def write_outputs(outputs: GeneratedOutputs) -> None:
    PYTHON_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PYTHON_OUTPUT_PATH.write_text(outputs.python, encoding="utf-8", newline="\n")
    DOCUMENT_OUTPUT_PATH.write_text(outputs.documentation, encoding="utf-8", newline="\n")


def check_outputs(outputs: GeneratedOutputs) -> tuple[Path, ...]:
    expected = {
        PYTHON_OUTPUT_PATH: outputs.python,
        DOCUMENT_OUTPUT_PATH: outputs.documentation,
    }
    return tuple(
        path
        for path, content in expected.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ContractTypeGenerationError(f"{path.name} must contain a mapping")
    return document


def _machine_states(document: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    raw_machines = document.get("machines")
    if not isinstance(raw_machines, dict):
        raise ContractTypeGenerationError("state-machines.yaml must contain machines")
    if set(raw_machines) != set(MACHINE_CLASS_NAMES):
        raise ContractTypeGenerationError("state machine set drifted")

    result: dict[str, tuple[str, ...]] = {}
    for machine_name in MACHINE_CLASS_NAMES:
        machine = raw_machines.get(machine_name)
        if not isinstance(machine, dict):
            raise ContractTypeGenerationError(f"{machine_name} must be a mapping")
        values: list[str] = []
        _append_state(values, machine.get("initial"))
        for field in STATE_FIELDS:
            raw_values = machine.get(field, [])
            if not isinstance(raw_values, list):
                raise ContractTypeGenerationError(f"{machine_name}.{field} must be a list")
            for value in raw_values:
                _append_state(values, value)
        _append_state(values, machine.get("conflict"))
        if not values:
            raise ContractTypeGenerationError(f"{machine_name} has no states")
        result[machine_name] = tuple(values)
    return result


def _append_state(values: list[str], value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ContractTypeGenerationError(f"invalid generated enum value: {value!r}")
    if value not in values:
        values.append(value)


def _reason_codes(document: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    raw_codes = document.get("codes")
    if not isinstance(raw_codes, list):
        raise ContractTypeGenerationError("reason-codes.yaml must contain codes")
    reasons: list[tuple[str, str, str]] = []
    for item in raw_codes:
        if not isinstance(item, dict):
            raise ContractTypeGenerationError("reason code entries must be mappings")
        code = item.get("code")
        category = item.get("category")
        severity = item.get("severity")
        for label, value in (
            ("code", code),
            ("category", category),
            ("severity", severity),
        ):
            if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
                raise ContractTypeGenerationError(f"invalid reason {label}: {value!r}")
        reasons.append((code, category, severity))
    if not reasons or len({code for code, _, _ in reasons}) != len(reasons):
        raise ContractTypeGenerationError("reason codes must be non-empty and unique")
    return tuple(reasons)


def _source_hash() -> str:
    digest = hashlib.sha256()
    for path in (STATE_MACHINES_PATH, REASON_CODES_PATH):
        digest.update(path.name.encode("ascii"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _render_python(
    *,
    machines: dict[str, tuple[str, ...]],
    state_document: dict[str, Any],
    reasons: tuple[tuple[str, str, str], ...],
    source_hash: str,
) -> str:
    lines = [
        '"""Generated from schema/state-machines.yaml and schema/reason-codes.yaml.',
        "",
        "Do not edit by hand. Run: python tools/build_contract_types.py",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from enum import Enum",
        "from typing import Final",
        "",
        "",
        f'CONTRACT_SOURCE_SHA256: Final[str] = "{source_hash}"',
        "",
        "",
    ]
    for machine_name, class_name in MACHINE_CLASS_NAMES.items():
        lines.extend(_render_enum(class_name, machines[machine_name]))

    lines.extend(_render_enum("ReasonCode", tuple(code for code, _, _ in reasons)))
    lines.extend(
        [
            "REASON_CODE_METADATA: Final[dict[ReasonCode, tuple[str, str]]] = {",
            *(
                f'    ReasonCode.{code}: ("{category}", "{severity}"),'
                for code, category, severity in reasons
            ),
            "}",
            "",
        ]
    )

    command = _required_mapping(
        _required_mapping(state_document, "machines"),
        "command_receipt",
    )
    terminal = _required_string_list(command, "terminal")
    transitions = _required_mapping(command, "transitions")
    lines.extend(
        [
            "COMMAND_RECEIPT_TERMINAL_STATES: Final[frozenset[CommandReceiptState]] = frozenset(",
            "    {",
            *(f"        CommandReceiptState.{value}," for value in terminal),
            "    }",
            ")",
            "",
            (
                "COMMAND_RECEIPT_TRANSITIONS: "
                "Final[dict[CommandReceiptState, tuple[CommandReceiptState, ...]]] = {"
            ),
        ]
    )
    for source, raw_targets in transitions.items():
        if not isinstance(source, str) or IDENTIFIER_PATTERN.fullmatch(source) is None:
            raise ContractTypeGenerationError("invalid command transition source")
        if not isinstance(raw_targets, list):
            raise ContractTypeGenerationError("command transition targets must be a list")
        targets = tuple(_required_identifier(value) for value in raw_targets)
        lines.append(f"    CommandReceiptState.{source}: (")
        lines.extend(f"        CommandReceiptState.{target}," for target in targets)
        lines.append("    ),")
    lines.extend(["}", ""])

    operation = _required_mapping(
        _required_mapping(state_document, "machines"),
        "operation_commit",
    )
    operation_terminal = _required_string_list(operation, "terminal")
    operation_linear = _required_string_list(operation, "linear_success_path")
    lines.extend(
        [
            (
                "RECOVERY_OPERATION_TERMINAL_PHASES: "
                "Final[frozenset[RecoveryOperationPhase]] = frozenset("
            ),
            "    {",
            *(f"        RecoveryOperationPhase.{value}," for value in operation_terminal),
            "    }",
            ")",
            "",
            (
                "RECOVERY_OPERATION_LINEAR_SUCCESS_PATH: "
                "Final[tuple[RecoveryOperationPhase, ...]] = ("
            ),
            *(f"    RecoveryOperationPhase.{value}," for value in operation_linear),
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def _render_enum(class_name: str, values: tuple[str, ...]) -> list[str]:
    return [
        f"class {class_name}(str, Enum):",
        *(f'    {value} = "{value}"' for value in values),
        "",
        "",
    ]


def _render_documentation(
    *,
    machines: dict[str, tuple[str, ...]],
    reasons: tuple[tuple[str, str, str], ...],
    source_hash: str,
) -> str:
    lines = [
        "# Generated Contract Types",
        "",
        "Generated by `tools/build_contract_types.py`. Do not edit by hand.",
        "",
        f"Source SHA-256: `{source_hash}`",
        "",
        "## State Machines",
        "",
        "| Contract | Python type | Values |",
        "|---|---|---|",
    ]
    for machine_name, class_name in MACHINE_CLASS_NAMES.items():
        lines.append(
            f"| `{machine_name}` | `{class_name}` | "
            f"{', '.join(f'`{value}`' for value in machines[machine_name])} |"
        )
    lines.extend(
        [
            "",
            "## Reason Codes",
            "",
            "| Code | Category | Severity |",
            "|---|---|---|",
            *(
                f"| `{code}` | `{category}` | `{severity}` |"
                for code, category, severity in reasons
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _required_mapping(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ContractTypeGenerationError(f"{key} must be a mapping")
    return value


def _required_string_list(mapping: dict[str, Any], key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ContractTypeGenerationError(f"{key} must be a list")
    return tuple(_required_identifier(item) for item in value)


def _required_identifier(value: object) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ContractTypeGenerationError(f"invalid identifier: {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Python and Markdown contract types.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when generated outputs differ from the checked-in files.",
    )
    args = parser.parse_args()
    outputs = build_outputs()
    if args.check:
        drifted = check_outputs(outputs)
        if drifted:
            for path in drifted:
                print(f"DRIFT: {path.relative_to(ROOT)}")
            return 1
        print("PASS: generated contract types and documentation match YAML")
        return 0
    write_outputs(outputs)
    print(f"WROTE: {PYTHON_OUTPUT_PATH.relative_to(ROOT)}")
    print(f"WROTE: {DOCUMENT_OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
