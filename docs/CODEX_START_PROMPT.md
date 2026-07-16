# Codex-oppstart — Milepæl 0A.0

Bruk teksten nedenfor etter at pakken er lagt i repository-roten og baseline er kontrollert. Ikke legg ved hele `MASTER_SPEC.md`.

```text
Arbeid på MediaSync Home i henhold til AGENTS.md.

Bindende arbeidsordre for denne økten:
1. Utfør bare Milepæl 0A.0 — miljø- og sikkerhetspreflight. Ikke start 0A.1, 0B eller produktimplementasjon.
2. Les bare dokumentene som AGENTS.md oppgir for 0A.0. Åpne MASTER_SPEC.md bare når målrettede dokumenter mangler nødvendig kontekst.
3. Før første endring: registrer repositoryrot, branch, git status og eksisterende filer. Ikke overskriv brukerarbeid eller normaliser hele repositoryet.
4. Kjør python tools/validate_handoff.py --verify-bundle og registrer eksakt resultat før første endring. En manglende valideringsavhengighet er ikke en bestått kontroll.
5. Inventer miljøet: Windows-utgave/build, CPU-arkitektur, Python, PowerShell, Git, Windows SDK/API-tilgang, PySide6/BLAKE3/Nuitka-tilgjengelighet, administratorstatus, Task Scheduler-tilgang, Hyper-V/VM-er, SMB-lab, filsystemer, fri plass og sikkerhetsprogramvare som kan påvirke prober.
6. Klassifiser hver senere 0A-arbeidspakke og hvert hovedeksperiment som RUNNABLE_NOW, RUNNABLE_WITH_LOCAL_FIXTURE, REQUIRES_USER_LAB_ACTION, BLOCKED_BY_ENVIRONMENT eller OUT_OF_SCOPE.
7. Opprett ingen produktdatabase, migrasjon, Engine Host, GUI, syncmotor eller produksjonsadapter. Eventuell kode i 0A.0 skal bare validere pakken eller samle ikke-muterende miljøinformasjon.
8. Skriv bare i repositoryet og eksplisitte støtteområder fra AGENTS.md. Opprett ingen Task Scheduler-oppgave, SMB-lock eller muterende filfixture i 0A.0.
9. Oppdater docs/ARCHITECTURE_SPIKE_REPORT.md med miljømatrise, eksakte kommandoer, blockers og anbefalt rekkefølge. Oppdater docs/IMPLEMENTATION_STATUS.md for 0A.0.
10. Dersom ett fremtidig eksperiment mangler miljø, marker bare dette eksperimentet blokkert. Fortsett med uavhengig preflight; stopp hele oppgaven bare etter stoppreglene i AGENTS.md.
11. Codex kan anbefale ADR-retning og oppdatere `evidence_status`, men kan ikke sette `owner_decision = OWNER_ACCEPTED` eller fryse kontrakter.
12. Presenter til slutt: endrede filer, valideringsresultat, miljøklassifisering, blockers, nødvendige brukerhandlinger og anbefalt neste arbeidspakke.
13. Stopp etter 0A.0. Ikke endre AGENTS.md til neste arbeidspakke og ikke fortsett automatisk.
```
