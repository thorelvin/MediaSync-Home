from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docs/adr/catalog.yaml"
README_PATH = ROOT / "docs/adr/README.md"
REGISTER_PATH = ROOT / "docs/DECISION_REGISTER.md"


def load_catalog() -> dict[str, Any]:
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))


def render_readme(catalog: dict[str, Any]) -> str:
    rows: list[str] = []
    for adr in catalog["adrs"]:
        date = adr.get("owner_decision_date") or ""
        supersedes = ", ".join(adr.get("supersedes", []))
        rows.append(
            f"| `{adr['id']}` | {adr['title']} | {adr['rationale']} | {adr['consequence']} | "
            f"`{adr['evidence_package']}` | `{adr['evidence_status']}` | `{adr['owner_decision']}` | {date} | {supersedes} |"
        )
    return (
        "# ADR-katalog\n\n"
        "> **GENERERT FIL — IKKE REDIGER DIREKTE.** Oppdater `docs/adr/catalog.yaml` og kjør "
        "`python tools/build_adr_docs.py`.\n\n"
        "## Statusmodell\n\n"
        "- `evidence_status`: `PROPOSED`, `EVIDENCE_COMPLETE`, `RECOMMENDED` eller `BLOCKED`.\n"
        "- `owner_decision`: `PENDING`, `OWNER_ACCEPTED`, `REJECTED`, "
        "`DEFERRED_WITH_SCOPE_REDUCTION` eller `SUPERSEDED`.\n\n"
        "En ADR er bindende bare når `owner_decision = OWNER_ACCEPTED`. Ved "
        "`DEFERRED_WITH_SCOPE_REDUCTION` skal berørt garanti, release og eksplisitt sikker fallback dokumenteres.\n\n"
        "## Katalog\n\n"
        "| ADR | Beslutning | Hovedbegrunnelse | Konsekvens | Bevispakke | Evidensstatus | Eierbeslutning | Eierdato | Erstatter |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\nHver ADR skal få en egen fil under `docs/adr/` når bevisarbeidet starter. Endring av en "
        "eiergodkjent beslutning krever ny ADR, berørte krav-ID-er og migrasjons-/testplan.\n"
    )


def render_register(catalog: dict[str, Any]) -> str:
    rows: list[str] = []
    for adr in catalog["adrs"]:
        recommendation = adr.get("codex_recommendation") or ""
        date = adr.get("owner_decision_date") or ""
        supersedes = ", ".join(adr.get("supersedes", []))
        rows.append(
            f"| `{adr['id']}` | {adr['title']} | `{adr['evidence_package']}` | "
            f"`{adr['evidence_status']}` | {recommendation} | `{adr['owner_decision']}` | {date} | {supersedes} |"
        )
    return (
        "# Beslutningsregister\n\n"
        "> **GENERERT FIL — IKKE REDIGER DIREKTE.** Oppdater "
        "[`adr/catalog.yaml`](adr/catalog.yaml) og kjør `python tools/build_adr_docs.py`.\n\n"
        "| ADR | Tema | Bevispakke | Evidensstatus | Codex-anbefaling | Eierbeslutning | Eierdato | Erstatter |\n"
        "|---|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n## Statusmodell\n\n"
        "Codex kan endre `evidence_status` til `EVIDENCE_COMPLETE`, `RECOMMENDED` eller `BLOCKED`, "
        "og kan fylle `codex_recommendation`. Bare prosjekteieren kan endre `owner_decision` fra "
        "`PENDING` til `OWNER_ACCEPTED`, `REJECTED`, `DEFERRED_WITH_SCOPE_REDUCTION` eller `SUPERSEDED`.\n\n"
        "Ingen kontrakt kan få status `frozen` før alle styrende ADR-er har "
        "`owner_decision = OWNER_ACCEPTED`.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ADR index and decision register from the canonical catalog")
    parser.add_argument("--check", action="store_true", help="fail if generated files are out of date")
    args = parser.parse_args()
    catalog = load_catalog()
    outputs = {README_PATH: render_readme(catalog), REGISTER_PATH: render_register(catalog)}
    if args.check:
        stale = [path for path, expected in outputs.items() if not path.exists() or path.read_text(encoding="utf-8") != expected]
        if stale:
            print("ERROR: generated ADR documents are stale: " + ", ".join(str(path.relative_to(ROOT)) for path in stale))
            return 1
        print("PASS: generated ADR documents match catalog")
        return 0
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"WROTE: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
