from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "2.9.2"
ACTIVE_WORK_ORDER = "Milepæl 1"
REQUIRED_PACKAGES = {"jsonschema": "4.26.0", "PyYAML": "6.0.3"}
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".sql", ".py", ".txt"}
IGNORED_SCAN_DIRS = {
    ".git",
    ".import_linter_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "venv",
}
ADR_PATTERN = re.compile(r"ADR-\d{3}")
REQ_PATTERN = re.compile(
    r"(?:SAF|REC|SYNC|DB|END|META|AUTO|PERF|ARC|DUR|SEC|OWN|CTRL|CASE|HASH|SRC|PATH|DUP|VER|TIME|LOCK|OPS|FILTER|PROC|DOC|UX|OBS)-\d{3}"
)
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


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def require_dependencies(*, strict_versions: bool = False) -> tuple[Any, Any, Any]:
    for distribution, expected in REQUIRED_PACKAGES.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            fail(
                f"required validation dependency {distribution}=={expected} is missing; "
                "run: python -m pip install -r requirements-handoff.txt"
            )
        if strict_versions and actual != expected:
            fail(
                f"validation dependency version mismatch for {distribution}: expected {expected}, got {actual}; "
                "run: python -m pip install -r requirements-handoff.txt"
            )
    import yaml  # type: ignore[import-not-found]
    from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-not-found]

    return yaml, Draft202012Validator, FormatChecker


def read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            fail(f"UTF-8 BOM is not allowed: {path.relative_to(ROOT)}")
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"invalid UTF-8 in {path.relative_to(ROOT)}: {exc}")
    raise AssertionError("unreachable")


def iter_repo_files() -> list[Path]:
    result: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(ROOT).parts
        if any(part in IGNORED_SCAN_DIRS for part in relative_parts):
            continue
        result.append(path)
    return result


def check_text_hygiene() -> None:
    for path in iter_repo_files():
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = read_text(path)
        if not text.endswith("\n"):
            fail(f"missing final newline: {path.relative_to(ROOT)}")
        for number, line in enumerate(text.splitlines(), start=1):
            if line.endswith((" ", "\t")) or "\t" in line:
                fail(f"whitespace issue in {path.relative_to(ROOT)}:{number}")
            for char in line:
                if unicodedata.category(char) == "Cf":
                    fail(
                        f"hidden Unicode format character U+{ord(char):04X} in "
                        f"{path.relative_to(ROOT)}:{number}"
                    )


def check_json_and_examples(Draft202012Validator: Any, FormatChecker: Any) -> None:
    schema_dir = ROOT / "schema"
    schemas: dict[str, tuple[Path, Any]] = {}
    for path in sorted(schema_dir.glob("*.schema.json")):
        try:
            document = json.loads(read_text(path))
            Draft202012Validator.check_schema(document)
        except Exception as exc:  # noqa: BLE001 - validation utility
            fail(f"invalid JSON Schema {path.relative_to(ROOT)}: {exc}")
        schemas[path.name] = (path, document)

    mapping = {
        "endpoint-marker.valid.json": "endpoint-marker.schema.json",
        "ipc-command.valid.json": "ipc-command.schema.json",
        "ipc-event.valid.json": "ipc-event.schema.json",
        "intent-segment-header.valid.json": "intent-segment.schema.json",
        "intent-segment-operation.valid.json": "intent-segment.schema.json",
    }
    examples_dir = schema_dir / "examples"
    actual_examples = {path.name for path in examples_dir.glob("*.json")}
    if actual_examples != set(mapping):
        fail(
            "schema example set mismatch: expected "
            f"{sorted(mapping)}, got {sorted(actual_examples)}"
        )
    for example_name, schema_name in mapping.items():
        example_path = examples_dir / example_name
        try:
            instance = json.loads(read_text(example_path))
            _, schema = schemas[schema_name]
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            validator.validate(instance)
        except Exception as exc:  # noqa: BLE001 - validation utility
            fail(f"schema example validation failed for {example_path.relative_to(ROOT)}: {exc}")

    architecture = read_text(ROOT / "docs/ARCHITECTURE.md")
    match = re.search(
        r"Kommandoenvelope:\s*```json\n(?P<json>\{.*?\})\n```",
        architecture,
        flags=re.DOTALL,
    )
    if not match:
        fail("docs/ARCHITECTURE.md lacks the canonical IPC command envelope example")
    try:
        markdown_example = json.loads(match.group("json"))
        _, command_schema = schemas["ipc-command.schema.json"]
        Draft202012Validator(
            command_schema, format_checker=FormatChecker()
        ).validate(markdown_example)
    except Exception as exc:  # noqa: BLE001 - validation utility
        fail(f"Markdown IPC command example does not validate: {exc}")
    file_example = json.loads(read_text(examples_dir / "ipc-command.valid.json"))
    if markdown_example != file_example:
        fail(
            "docs/ARCHITECTURE.md IPC command example differs from "
            "schema/examples/ipc-command.valid.json"
        )


def load_yaml_documents(yaml: Any) -> dict[Path, Any]:
    documents: dict[Path, Any] = {}
    for path in sorted(list((ROOT / "schema").glob("*.yaml")) + [ROOT / "docs/adr/catalog.yaml"]):
        try:
            documents[path] = yaml.safe_load(read_text(path))
        except Exception as exc:  # noqa: BLE001 - validation utility
            fail(f"invalid YAML {path.relative_to(ROOT)}: {exc}")
    return documents


def heading_slug(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").strip().lower()
    output: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if char in {" ", "-", "_"}:
            output.append("-" if char == " " else char)
        elif category[0] in {"L", "N", "M"}:
            output.append(char)
    return "".join(output).strip("-")


def markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in read_text(path).splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base = heading_slug(match.group(2))
        count = counts.get(base, 0)
        counts[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def parse_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    # Optional title follows whitespace. Paths containing spaces must be angle-bracketed or encoded.
    return target.split(maxsplit=1)[0]


def check_markdown() -> None:
    paths = [path for path in iter_repo_files() if path.suffix.lower() == ".md"]
    anchors = {path.resolve(): markdown_anchors(path) for path in paths}
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

    for path in paths:
        text = read_text(path)
        fences = [line for line in text.splitlines() if re.match(r"^\s*```", line)]
        if len(fences) % 2:
            fail(f"unbalanced code fences in {path.relative_to(ROOT)}")

        previous_level = 0
        in_fence = False
        for number, line in enumerate(text.splitlines(), start=1):
            if re.match(r"^\s*```", line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = re.match(r"^(#{1,6})\s+", line)
            if match:
                level = len(match.group(1))
                if previous_level and level > previous_level + 1:
                    fail(f"heading level jump in {path.relative_to(ROOT)}:{number}")
                previous_level = level

        for match in link_pattern.finditer(text):
            target = parse_link_target(match.group(1))
            if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            decoded = unquote(target)
            if decoded.startswith("#"):
                target_path = path.resolve()
                anchor = decoded[1:]
            else:
                path_part, separator, anchor = decoded.partition("#")
                target_path = (path.parent / path_part).resolve()
                if not target_path.is_file():
                    fail(f"broken relative link in {path.relative_to(ROOT)}: {target}")
                if not separator:
                    continue
            if target_path.suffix.lower() != ".md":
                if anchor:
                    fail(f"anchor used on non-Markdown target in {path.relative_to(ROOT)}: {target}")
                continue
            if anchor and anchor not in anchors.get(target_path, set()):
                fail(f"broken Markdown anchor in {path.relative_to(ROOT)}: {target}")


def parse_table_ids(path: Path, pattern: re.Pattern[str]) -> list[str]:
    result: list[str] = []
    for line in read_text(path).splitlines():
        if not line.startswith("|"):
            continue
        matches = pattern.findall(line)
        if matches:
            result.append(matches[0])
    return result


def check_adr_governance(yaml_documents: dict[Path, Any]) -> set[str]:
    catalog_path = ROOT / "docs/adr/catalog.yaml"
    catalog = yaml_documents[catalog_path]
    if catalog.get("bundle_version") != EXPECTED_VERSION:
        fail("ADR catalog bundle version mismatch")
    evidence_allowed = set(catalog.get("allowed_evidence_statuses", []))
    decision_allowed = set(catalog.get("allowed_owner_decisions", []))
    adrs = catalog.get("adrs", [])
    ids = [item.get("id") for item in adrs]
    expected_ids = [f"ADR-{number:03d}" for number in range(1, 29)]
    if ids != expected_ids:
        fail(f"ADR catalog must contain exact ordered IDs ADR-001..ADR-028, got {ids}")
    if len(set(ids)) != len(ids):
        fail("duplicate ADR ID in catalog")
    for item in adrs:
        if not item.get("title") or not item.get("evidence_package"):
            fail(f"ADR catalog entry missing title/evidence package: {item.get('id')}")
        if not item.get("rationale") or not item.get("consequence"):
            fail(f"ADR catalog entry missing rationale/consequence: {item.get('id')}")
        if "codex_recommendation" not in item:
            fail(f"ADR catalog entry missing codex_recommendation field: {item.get('id')}")
        if item.get("evidence_status") not in evidence_allowed:
            fail(f"invalid evidence_status for {item.get('id')}")
        if item.get("owner_decision") not in decision_allowed:
            fail(f"invalid owner_decision for {item.get('id')}")
        if item.get("owner_decision") == "OWNER_ACCEPTED" and not item.get("owner_decision_date"):
            fail(f"OWNER_ACCEPTED ADR lacks owner_decision_date: {item.get('id')}")

    readme_ids = parse_table_ids(ROOT / "docs/adr/README.md", ADR_PATTERN)
    register_ids = parse_table_ids(ROOT / "docs/DECISION_REGISTER.md", ADR_PATTERN)
    if readme_ids != expected_ids:
        fail("docs/adr/README.md is not synchronized with ADR catalog")
    if register_ids != expected_ids:
        fail("docs/DECISION_REGISTER.md is not synchronized with ADR catalog")

    known = set(expected_ids)
    for path in iter_repo_files():
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        unknown = set(ADR_PATTERN.findall(read_text(path))) - known
        if unknown:
            fail(f"unknown ADR reference(s) in {path.relative_to(ROOT)}: {sorted(unknown)}")

    contracts = yaml_documents[ROOT / "schema/contracts-manifest.yaml"]
    for contract in contracts.get("contracts", []):
        unknown = set(contract.get("governing_adrs", [])) - known
        if unknown:
            fail(f"contract {contract.get('path')} references unknown ADRs: {sorted(unknown)}")

    forbidden = {"DEFER_WITH_SCOPE_REDUCTION", "`ACCEPT`", "`REJECT`"}
    for path in [repo_path for repo_path in iter_repo_files() if repo_path.suffix.lower() == ".md"]:
        text = read_text(path)
        for term in forbidden:
            if term in text:
                fail(f"obsolete ADR decision vocabulary {term!r} in {path.relative_to(ROOT)}")
    return known


def check_requirements() -> set[str]:
    index_path = ROOT / "docs/REQUIREMENTS_INDEX.md"
    canonical: list[str] = []
    canonical_rows: dict[str, tuple[str, str]] = {}
    for line in read_text(index_path).splitlines():
        match = re.match(r"^\| `([A-Z]+-\d{3})` \| (.*?) \| (.*?) \|$", line)
        if match:
            requirement_id = match.group(1)
            canonical.append(requirement_id)
            canonical_rows[requirement_id] = (match.group(2), match.group(3))
    if not canonical or len(canonical) != len(set(canonical)):
        fail("canonical requirement index is empty or contains duplicates")
    known = set(canonical)

    traceability_rows: dict[str, tuple[str, str]] = {}
    for line in read_text(ROOT / "docs/REQUIREMENTS_TRACEABILITY.md").splitlines():
        match = re.match(r"^\| `([A-Z]+-\d{3})` \| (.*?) \| (.*?) \|", line)
        if match:
            traceability_rows[match.group(1)] = (match.group(2), match.group(3))
    if traceability_rows != canonical_rows:
        missing = sorted(set(canonical_rows) - set(traceability_rows))
        extra = sorted(set(traceability_rows) - set(canonical_rows))
        drift = sorted(
            requirement_id
            for requirement_id in set(canonical_rows) & set(traceability_rows)
            if canonical_rows[requirement_id] != traceability_rows[requirement_id]
        )
        fail(f"requirement traceability drift; missing={missing}, extra={extra}, changed={drift}")
    for path in iter_repo_files():
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        unknown = set(REQ_PATTERN.findall(read_text(path))) - known
        if unknown:
            fail(f"unknown requirement ID(s) in {path.relative_to(ROOT)}: {sorted(unknown)}")
    for required_path in [ROOT / "docs/REQUIREMENTS_TRACEABILITY.md", ROOT / "docs/MILESTONES.md"]:
        present = set(REQ_PATTERN.findall(read_text(required_path)))
        missing = known - present
        if missing:
            fail(f"requirements missing from {required_path.relative_to(ROOT)}: {sorted(missing)}")
    return known


def check_yaml_semantics(yaml_documents: dict[Path, Any], known_adrs: set[str]) -> None:
    contracts = yaml_documents[ROOT / "schema/contracts-manifest.yaml"]
    if contracts.get("bundle_version") != EXPECTED_VERSION:
        fail("contracts manifest version mismatch")
    allowed_statuses = set(contracts.get("allowed_contract_statuses", []))
    for contract in contracts.get("contracts", []):
        if contract.get("status") not in allowed_statuses:
            fail(f"invalid contract status for {contract.get('path')}")
        if not set(contract.get("governing_adrs", [])) <= known_adrs:
            fail(f"unknown governing ADR in contract {contract.get('path')}")

    reason_codes = yaml_documents[ROOT / "schema/reason-codes.yaml"].get("codes", [])
    codes = [item.get("code") for item in reason_codes]
    if len(codes) != len(set(codes)):
        fail("duplicate reason code in schema/reason-codes.yaml")
    if any(not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", code) for code in codes):
        fail("invalid reason-code identifier")

    machines = yaml_documents[ROOT / "schema/state-machines.yaml"].get("machines", {})
    for name, machine in machines.items():
        for field in ("states", "terminal", "linear_success_path", "optional_states", "takeover_requires"):
            values = machine.get(field)
            if isinstance(values, list) and len(values) != len(set(values)):
                fail(f"duplicate value in state machine {name}.{field}")

    command = machines.get("command_receipt")
    if not isinstance(command, dict):
        fail("schema/state-machines.yaml lacks command_receipt")
    if command.get("initial") != "RECEIVED":
        fail("command_receipt initial state must be RECEIVED")
    if command.get("states") != COMMAND_RECEIPT_STATES:
        fail("command_receipt state vocabulary/order differs from the canonical set")
    if command.get("terminal") != COMMAND_RECEIPT_TERMINAL:
        fail("command_receipt terminal states differ from the canonical set")
    if command.get("transitions") != COMMAND_RECEIPT_TRANSITIONS:
        fail("command_receipt transitions differ from the canonical transition map")
    if command.get("monotonic") is not True:
        fail("command_receipt must be monotonic")

    architecture = read_text(ROOT / "docs/ARCHITECTURE.md")
    storage = read_text(ROOT / "docs/STORAGE_AND_SCHEMA.md")
    for state in COMMAND_RECEIPT_STATES:
        if state not in architecture:
            fail(f"docs/ARCHITECTURE.md omits command receipt state {state}")
        if state not in storage:
            fail(f"docs/STORAGE_AND_SCHEMA.md omits command receipt state {state}")
    if "ACCEPTED -> COMPLETED" in architecture or " accepted, completed," in storage.lower():
        fail("obsolete COMPLETED command receipt vocabulary remains in canonical documents")


def check_bundle_rules() -> None:
    if (ROOT / "CODEX_IMPLEMENTATION_PLAN_MEDIASYNC_HOME.md").exists():
        fail("duplicate master file is present")
    master = read_text(ROOT / "MASTER_SPEC.md")
    readme = read_text(ROOT / "README.md")
    if f"**Dokumentversjon** | {EXPECTED_VERSION}" not in master:
        fail("MASTER_SPEC.md version mismatch")
    if "GENERERT FIL — IKKE REDIGER DIREKTE" not in master:
        fail("MASTER_SPEC.md is not marked as generated")
    if f"v{EXPECTED_VERSION}" not in readme:
        fail("README.md version mismatch")
    required_readme_fragments = {
        "spesifikasjon / pre-alpha",
        "docs/assets/gui-concept-v1.png",
        "docs/IMPLEMENTATION_STATUS.md",
        "docs/RELEASE_SCOPE.md",
        "docs/README.md",
        "docs/CODEX_START_PROMPT.md",
        "Det finnes ingen installasjonsklar",
        "MediaSync Home er et uavhengig prosjekt",
    }
    for fragment in required_readme_fragments:
        if fragment not in readme:
            fail(f"README.md missing required GitHub landing-page content: {fragment}")
    docs_index = read_text(ROOT / "docs/README.md")
    for fragment in (
        "# Dokumentasjon",
        "PRODUCT_REQUIREMENTS.md",
        "ARCHITECTURE.md",
        "RECOVERY_PROTOCOL.md",
        "MILESTONES.md",
        "IMPLEMENTATION_STATUS.md",
        "../AGENTS.md",
    ):
        if fragment not in docs_index:
            fail(f"docs/README.md missing documentation-index content: {fragment}")
    footer = f"**Slutt på implementeringsplan — dokumentversjon {EXPECTED_VERSION}.**"
    if not read_text(ROOT / "CHANGELOG.md").rstrip().endswith(footer):
        fail("CHANGELOG.md footer version mismatch")
    if not master.rstrip().endswith(footer):
        fail("MASTER_SPEC.md footer version mismatch")
    agents = read_text(ROOT / "AGENTS.md")
    if ACTIVE_WORK_ORDER not in agents:
        fail(f"AGENTS.md does not name the active {ACTIVE_WORK_ORDER} work order")
    start = read_text(ROOT / "docs/CODEX_START_PROMPT.md")
    if f"Utfør {ACTIVE_WORK_ORDER}" not in start:
        fail(f"start prompt does not name the active {ACTIVE_WORK_ORDER} work order")
    for phrase in (
        "owner_decision",
        "DEFERRED_WITH_SCOPE_REDUCTION",
        "BLOCKED_BY_ENVIRONMENT",
        "validate_contracts.py",
        "lokal usignert preview",
        "writable SMB er utsatt",
        "same-user",
    ):
        if phrase not in agents:
            fail(f"AGENTS.md missing required governance phrase: {phrase}")

    endpoint_schema = json.loads(read_text(ROOT / "schema/endpoint-marker.schema.json"))
    endpoint_required = set(endpoint_schema.get("required", []))
    for field in {
        "root_identity_hash_algorithm",
        "canonicalization_algorithm",
        "marker_checksum_algorithm",
        "marker_checksum",
    }:
        if field not in endpoint_required:
            fail(f"endpoint marker does not require {field}")

    command_schema = json.loads(read_text(ROOT / "schema/ipc-command.schema.json"))
    command_required = set(command_schema.get("required", []))
    for field in {
        "payload_hash_scope",
        "payload_canonicalization_algorithm",
        "payload_hash_algorithm",
        "payload_hash",
    }:
        if field not in command_required:
            fail(f"IPC command does not require {field}")

    for generator, description in [
        ("tools/build_adr_docs.py", "generated ADR document check failed"),
        ("tools/build_master.py", "generated master check failed"),
        ("tools/validate_contracts.py", "draft contract validation failed"),
    ]:
        args = ["--check"] if generator != "tools/validate_contracts.py" else []
        result = subprocess.run(
            [sys.executable, str(ROOT / generator), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            fail(result.stdout.strip() or result.stderr.strip() or description)


def manifest_files() -> set[str]:
    excluded = {"BUNDLE_MANIFEST.sha256"}
    files: set[str] = set()
    for path in iter_repo_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative not in excluded:
            files.add(relative)
    return files


def check_manifest_hashes() -> None:
    manifest_path = ROOT / "BUNDLE_MANIFEST.sha256"
    if not manifest_path.exists():
        fail("BUNDLE_MANIFEST.sha256 is missing")
    entries: dict[str, str] = {}
    for line in read_text(manifest_path).splitlines():
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            fail(f"invalid manifest line: {line}")
        if relative in entries:
            fail(f"duplicate manifest entry: {relative}")
        entries[relative] = expected
    actual_files = manifest_files()
    if set(entries) != actual_files:
        missing = sorted(actual_files - set(entries))
        extra = sorted(set(entries) - actual_files)
        fail(f"manifest coverage mismatch; missing={missing}, extra={extra}")
    for relative, expected in entries.items():
        path = ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"hash mismatch: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the MediaSync Home Codex handoff bundle")
    parser.add_argument(
        "--verify-bundle",
        action="store_true",
        help="also verify exact baseline file coverage and SHA-256 hashes",
    )
    args = parser.parse_args()

    yaml, Draft202012Validator, FormatChecker = require_dependencies(
        strict_versions=args.verify_bundle
    )
    check_text_hygiene()
    check_json_and_examples(Draft202012Validator, FormatChecker)
    yaml_documents = load_yaml_documents(yaml)
    check_markdown()
    known_adrs = check_adr_governance(yaml_documents)
    check_requirements()
    check_yaml_semantics(yaml_documents, known_adrs)
    check_bundle_rules()
    if args.verify_bundle:
        check_manifest_hashes()
    else:
        print("INFO: baseline hashes not checked; use --verify-bundle before first edit")
    print("PASS: MediaSync Home handoff bundle validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
