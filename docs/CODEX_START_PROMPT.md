# Codex-oppstart — Milepæl 1

Bruk teksten nedenfor etter at 0B-kvalitetsporten er evaluert og prosjekteieren
har bedt om å gå videre. Ikke legg ved hele `MASTER_SPEC.md`.

```text
Arbeid på MediaSync Home i henhold til AGENTS.md.

Bindende arbeidsordre for denne økten:
1. Utfør Milepæl 1 — Engine Host, IPC, immutable revisjoner og databaser.
2. Respekter eierens scope-reduksjoner fra 2026-07-17: lokal usignert preview, local-only writable targets og same-user startup først. Ikke påstå signert release, writable SMB-sikkerhet eller full non-interactive automatisering.
3. Les bare dokumentene som AGENTS.md oppgir for Milepæl 1. Åpne MASTER_SPEC.md bare når målrettede dokumenter mangler nødvendig kontekst.
4. Før første endring: registrer repositoryrot, branch, git status og eksisterende filer. Ikke overskriv brukerarbeid eller normaliser hele repositoryet.
5. Oppdater den operative dokumentpakken slik at AGENTS.md, statusdokumenter, ADR-index og kontraktsmanifest ikke peker på en gammel arbeidspakke.
6. Ferdigstill Engine Host-singleton/readiness, faste IPC-ressursgrenser, command-idempotens, immutable revisjoner, parent-scoped FKs og defensive SQLite-policyer etter Milepæl 1.
7. Hold `schema/contracts-manifest.yaml` som draft/blocked/candidate inntil styrende ADR-er, særlig ADR-026, har owner decision og valideringstestene finnes. Ikke sett kontrakter til `frozen` uten eiergrunnlag.
8. Implementer ingen syncmotor, produksjons-Robocopy eller muterende filsystemflyt. Eventuelle filsystemprober skal bare bruke markerte labområder.
9. Ingen database- eller fil-I/O-transaksjon skal holdes over IPC-, prosess- eller filesystemventing.
10. Codex kan anbefale ADR-retning og oppdatere `evidence_status`, men kan ikke sette `owner_decision = OWNER_ACCEPTED`.
11. Kjør relevante kontroller fra AGENTS.md. En manglende valideringsavhengighet eller ikke-konfigurert kontroll er ikke en bestått kontroll.
12. Presenter til slutt: endrede filer, valideringsresultat, gjenstående blockers og neste konkrete Milepæl 1-slice.
```
