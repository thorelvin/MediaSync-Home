from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTAKE = ROOT / "docs/adr/owner-decision-intake.example.json"

SCOPE_CHOICES = {
    "packaging_scope": {"P1", "P2", "P3", "UNDECIDED"},
    "smb_scope": {"S1", "S2", "S3", "UNDECIDED"},
    "scheduler_scope": {"T1", "T2", "T3", "UNDECIDED"},
    "open_0b": {"B1", "B2", "UNDECIDED"},
}
READY_ADRS = {"ADR-003", "ADR-007", "ADR-010", "ADR-011", "ADR-018", "ADR-020", "ADR-023", "ADR-024", "ADR-027"}
CORE_READY_ADRS = {"ADR-003", "ADR-011", "ADR-018", "ADR-020", "ADR-024", "ADR-027"}
OWNER_DECISIONS = {"PENDING", "OWNER_ACCEPTED", "REJECTED", "DEFERRED_WITH_SCOPE_REDUCTION"}
SCOPE_REDUCTION_CHOICES = {
    "packaging_scope": "P2",
    "smb_scope": "S2",
    "scheduler_scope": "T2",
}
OPENING_SCOPE_CHOICES = {
    "packaging_scope": {"P1", "P2"},
    "smb_scope": {"S1", "S2"},
    "scheduler_scope": {"T1", "T2"},
}


class IntakeError(ValueError):
    pass


def fail(message: str) -> None:
    raise IntakeError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")
    if not isinstance(document, dict):
        fail("intake must be a JSON object")
    return document


def require_keys(document: dict[str, Any], keys: set[str], path: str) -> None:
    missing = keys - set(document)
    if missing:
        fail(f"{path} missing required key(s): {sorted(missing)}")


def validate_intake(document: dict[str, Any]) -> list[str]:
    require_keys(document, {"version", "effective_only_after_owner_confirms", "decisions"}, "$")
    if document["version"] != 1:
        fail("version must be 1")
    if document["effective_only_after_owner_confirms"] is not True:
        fail("effective_only_after_owner_confirms must be true")
    if not isinstance(document["decisions"], dict):
        fail("decisions must be an object")

    decisions = document["decisions"]
    require_keys(decisions, set(SCOPE_CHOICES) | {"ready_adrs"}, "$.decisions")
    unexpected = set(decisions) - (set(SCOPE_CHOICES) | {"ready_adrs"})
    if unexpected:
        fail(f"$.decisions has unexpected key(s): {sorted(unexpected)}")

    summary: list[str] = []
    for key, allowed in SCOPE_CHOICES.items():
        value = decisions[key]
        if value not in allowed:
            fail(f"$.decisions.{key} must be one of {sorted(allowed)}, got {value!r}")
        summary.append(f"{key}={value}")

    ready_adrs = decisions["ready_adrs"]
    if not isinstance(ready_adrs, dict):
        fail("$.decisions.ready_adrs must be an object")
    missing_core = CORE_READY_ADRS - set(ready_adrs)
    if missing_core:
        fail(f"$.decisions.ready_adrs missing core ADR(s): {sorted(missing_core)}")
    unexpected_adrs = set(ready_adrs) - READY_ADRS
    if unexpected_adrs:
        fail(f"$.decisions.ready_adrs has unexpected ADR(s): {sorted(unexpected_adrs)}")
    for adr, decision in sorted(ready_adrs.items()):
        if decision not in OWNER_DECISIONS:
            fail(f"$.decisions.ready_adrs.{adr} has invalid owner decision {decision!r}")
        summary.append(f"{adr}={decision}")

    if decisions["open_0b"] == "B1":
        unresolved_scopes = [
            key
            for key, allowed in OPENING_SCOPE_CHOICES.items()
            if decisions[key] not in allowed
        ]
        unresolved_core_adrs = [adr for adr in sorted(CORE_READY_ADRS) if ready_adrs.get(adr) == "PENDING"]
        if unresolved_scopes or unresolved_core_adrs:
            fail(
                "open_0b=B1 requires explicit scope choices and non-PENDING core ADRs; "
                f"unresolved_scopes={unresolved_scopes}, unresolved_core_adrs={unresolved_core_adrs}"
            )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a non-binding MediaSync Home owner decision intake JSON file")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_INTAKE))
    args = parser.parse_args()

    path = Path(args.path)
    document = load_json(path)
    try:
        summary = validate_intake(document)
    except IntakeError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"PASS: {path} is a valid non-binding owner decision intake")
    print("INFO: no ADR catalog changes were made")
    for line in summary:
        print(f"INFO: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
