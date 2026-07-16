from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.9.2"
MASTER_PATH = ROOT / "MASTER_SPEC.md"

SECTION_SOURCES: dict[int, str] = {
    0: "docs/GOVERNANCE.md",
    1: "docs/PRODUCT_REQUIREMENTS.md",
    2: "docs/PRODUCT_REQUIREMENTS.md",
    3: "docs/REQUIREMENTS_INDEX.md",
    4: "docs/ARCHITECTURE.md",
    5: "docs/SYNC_SEMANTICS.md",
    6: "docs/SYNC_SEMANTICS.md",
    7: "docs/SYNC_SEMANTICS.md",
    8: "docs/GUI_AND_UX.md",
    9: "docs/ARCHITECTURE.md",
    10: "docs/REPOSITORY_AND_CODE_QUALITY.md",
    11: "docs/STORAGE_AND_SCHEMA.md",
    12: "docs/ENDPOINT_OWNERSHIP.md",
    13: "docs/SYNC_SEMANTICS.md",
    14: "docs/SYNC_SEMANTICS.md",
    15: "docs/ROBOCOPY_ADAPTER.md",
    16: "docs/PERFORMANCE.md",
    17: "docs/RECOVERY_PROTOCOL.md",
    18: "docs/OPERATIONS_AND_AUTOMATION.md",
    19: "docs/OPERATIONS_AND_AUTOMATION.md",
    20: "docs/MILESTONES.md",
    21: "docs/TEST_PLAN.md",
    22: "docs/TEST_PLAN.md",
    23: "docs/REPOSITORY_AND_CODE_QUALITY.md",
    24: "docs/REPOSITORY_AND_CODE_QUALITY.md",
    25: "docs/REFERENCES.md",
    27: "docs/LATER_IMPROVEMENTS.md",
    28: "CHANGELOG.md",
}

TOC = """- [0. Instruks til Codex](#0-instruks-til-codex)
- [0.5 Dokumentpakke, presedens og maskinlesbare kontrakter](#05-dokumentpakke-presedens-og-maskinlesbare-kontrakter)
- [1. Produktmål](#1-produktmål)
- [2. Låste produktvalg og standardverdier](#2-låste-produktvalg-og-standardverdier)
- [3. Terminologi og kravsporbarhet](#3-terminologi-og-kravsporbarhet)
- [4. Sikkerhetsmodell og invarianter](#4-sikkerhetsmodell-og-invarianter)
- [5. Synkroniseringsmoduser](#5-synkroniseringsmoduser)
- [6. Identiske filer, hash-evidens og duplikatdeteksjon](#6-identiske-filer-hash-evidens-og-duplikatdeteksjon)
- [7. Filfiltre](#7-filfiltre)
- [8. Produktdesign, GUI og brukeropplevelse](#8-produktdesign-gui-og-brukeropplevelse)
- [9. Teknisk arkitektur](#9-teknisk-arkitektur)
- [10. Repository-struktur](#10-repository-struktur)
- [11. Datamodell](#11-datamodell)
- [12. Endepunktoppdagelse, identitet og kapabiliteter](#12-endepunktoppdagelse-identitet-og-kapabiliteter)
- [13. Skanner, coverage og indeks](#13-skanner-coverage-og-indeks)
- [14. Sammenlignings- og planleggingsmotor](#14-sammenlignings--og-planleggingsmotor)
- [15. Robocopy-adapter og prosessisolasjon](#15-robocopy-adapter-og-prosessisolasjon)
- [16. Ressursstyring og selvbalanserende overføring](#16-ressursstyring-og-selvbalanserende-overføring)
- [17. Verifisering, durability, versjonering og karantene](#17-verifisering-durability-versjonering-og-karantene)
- [18. Automatisering uten Windows-tjeneste](#18-automatisering-uten-windows-tjeneste)
- [19. Feilhåndtering og observabilitet](#19-feilhåndtering-og-observabilitet)
- [20. Milepæler og konkrete Codex-oppgaver](#20-milepæler-og-konkrete-codex-oppgaver)
- [21. Teststrategi](#21-teststrategi)
- [22. Akseptansekriterier](#22-akseptansekriterier-for-komplett-hjemmeversjon)
- [23. Kodekvalitet og utviklingsregler](#23-kodekvalitet-og-utviklingsregler)
- [24. Foreslåtte versjoner ved prosjektstart](#24-foreslåtte-versjoner-ved-prosjektstart)
- [25. Offisielle tekniske referanser](#25-offisielle-tekniske-referanser)
- [26. Oppstartsprompt til Codex](#26-oppstartsprompt-til-codex)
- [27. Senere forbedringer](#27-senere-forbedringer)
- [28. Revisjonslogg](#28-revisjonslogg)"""


def _numbered_heading(line: str) -> int | None:
    match = re.match(r"^##\s+(\d+)\.\s+", line)
    return int(match.group(1)) if match else None


def extract_section(relative_path: str, number: int) -> str:
    path = ROOT / relative_path
    lines = path.read_text(encoding="utf-8").splitlines()
    in_fence = False
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        found = _numbered_heading(line)
        if found == number and start is None:
            start = index
            continue
        if start is not None and found is not None:
            end = index
            break
    if start is None:
        raise ValueError(f"Section {number} not found in {relative_path}")
    section = "\n".join(lines[start:end]).rstrip() + "\n"
    return rewrite_relative_links(section, Path(relative_path))


def rewrite_relative_links(text: str, source_path: Path) -> str:
    pattern = re.compile(r"(!?\[[^\]]*\]\()([^)]+)(\))")
    in_fence = False
    output: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue

        def replace(match: re.Match[str]) -> str:
            target = match.group(2).strip()
            if target.startswith(("#", "http://", "https://", "mailto:", "data:")):
                return match.group(0)
            if target.startswith("<") and target.endswith(">"):
                raw = target[1:-1]
                wrapped = True
            else:
                raw = target.split(maxsplit=1)[0]
                wrapped = False
            path_part, separator, anchor = raw.partition("#")
            resolved = (ROOT / source_path.parent / path_part).resolve()
            try:
                relative = resolved.relative_to(ROOT.resolve()).as_posix()
            except ValueError:
                return match.group(0)
            rewritten = relative + (separator + anchor if separator else "")
            if wrapped:
                rewritten = f"<{rewritten}>"
            suffix = target[len(raw):] if not wrapped else ""
            return match.group(1) + rewritten + suffix + match.group(3)

        output.append(pattern.sub(replace, line))
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def build_master() -> str:
    header = f"""# MediaSync Home — generert masterspesifikasjon og Codex-plan

> **GENERERT FIL — IKKE REDIGER DIREKTE.** Kjør `python tools/build_master.py` etter endring i de kanoniske fagfilene under `docs/`. Validatoren feiler dersom masteren driver fra kildene.

| Felt | Verdi |
|---|---|
| **Dokumentversjon** | {VERSION} — Codex-overleveringspakke med kanoniske fagfiler, komplett ADR-styring, streng baselinevalidator, GitHub-vennlig prosjektinngang og vertikal leveransestige |
| **Revidert** | 2026-07-16 |
| **Plattform** | Windows 10 og Windows 11, x64 |
| **Primærbruk** | Privat sikkerhetskopiering og synkronisering av flere terabyte bilder, videoer og andre filer mellom lokale disker, USB-disker og SMB/NAS |
| **Teknologi** | Python 3.14, PySide6/Qt 6, SQLite og Robocopy |
| **Brukerflate** | Grafisk Windows-program; ingen offentlig kommandolinje |
| **Hovedscenario** | Én kildemappe sikkerhetskopieres til opptil tre uavhengige mål |
| **Dokumentstatus** | Generert konsolidert referanse. `AGENTS.md` og de kanoniske fagfilene styrer aktivt Codex-arbeid. |

---

## Innholdsfortegnelse

{TOC}

---
"""
    chunks = [header.rstrip()]
    for number in range(0, 26):
        chunks.append(extract_section(SECTION_SOURCES[number], number).rstrip())
    prompt = (ROOT / "docs/CODEX_START_PROMPT.md").read_text(encoding="utf-8").splitlines()
    if not prompt or not prompt[0].startswith("# "):
        raise ValueError("docs/CODEX_START_PROMPT.md must start with an H1")
    chunks.append("## 26. Oppstartsprompt til Codex\n\n" + "\n".join(prompt[1:]).strip())
    chunks.append(extract_section(SECTION_SOURCES[27], 27).rstrip())
    chunks.append(extract_section(SECTION_SOURCES[28], 28).rstrip())
    return "\n\n---\n\n".join(chunks).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the generated MediaSync Home master specification")
    parser.add_argument("--check", action="store_true", help="fail when MASTER_SPEC.md differs from generated output")
    args = parser.parse_args()
    generated = build_master()
    if args.check:
        actual = MASTER_PATH.read_text(encoding="utf-8") if MASTER_PATH.exists() else ""
        if actual != generated:
            print("ERROR: MASTER_SPEC.md is not synchronized with canonical documents")
            return 1
        print("PASS: MASTER_SPEC.md matches canonical documents")
        return 0
    MASTER_PATH.write_text(generated, encoding="utf-8", newline="\n")
    print(f"WROTE: {MASTER_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
