# AGENTS.md — MediaSync Home

## Gjeldende arbeidsordre

Utfør **Milepæl 0B — Repository, kontrakter, arkitekturporter og appramme**.
Prosjekteieren åpnet 0B 2026-07-17 med eksplisitt redusert scope:

- lokal usignert preview er tillatt; ikke påstå signert release eller clean-VM-smoke;
- writable targets er lokale i første omgang; ikke påstå writable SMB-sikkerhet;
- oppstart er same-user/same-session først; ikke påstå full non-interactive Task Scheduler-automatisering.

0B skal etablere repository-/kontraktgrunnlaget og minimal ikke-muterende app-/IPC-ramme.
Ikke implementer produksjons-sync, produksjons-Robocopy, endelig migrasjon eller muterende
filsystemflyt utenfor markerte labområder.

## Les i denne rekkefølgen

1. `docs/CODEX_START_PROMPT.md`.
2. `docs/MILESTONES.md`, særlig §20.2.
3. `docs/REPOSITORY_AND_CODE_QUALITY.md`, særlig §10 og §23.
4. `docs/GOVERNANCE.md`, særlig §0.5.
5. `docs/ARCHITECTURE.md`, særlig lagdeling, IPC og porter.
6. `docs/adr/0A_DECISION_REVIEW.md`, `docs/DECISION_REGISTER.md`, `docs/adr/README.md` og `docs/adr/catalog.yaml`.
7. `schema/contracts-manifest.yaml`, `schema/README.md` og relevante filer under `schema/`.
8. `MASTER_SPEC.md` bare når de målrettede dokumentene mangler nødvendig kontekst.

Ikke last hele masterspesifikasjonen inn i arbeidskonteksten som standard.

## Presedens ved konflikt

1. Kanoniske produkt- og sikkerhetskrav i `docs/REQUIREMENTS_INDEX.md`.
2. ADR-er med `owner_decision = OWNER_ACCEPTED`.
3. Eksplisitte eiergodkjente scope-reduksjoner med `DEFERRED_WITH_SCOPE_REDUCTION`.
4. Versjonerte kontrakter med status `frozen` i `schema/contracts-manifest.yaml`.
5. Databaseconstraints og genererte typer produsert fra fryste kontrakter.
6. Kjørbare konformitets-, sikkerhets- og arkitekturtester.
7. Gjeldende arbeidspakkes eksplisitte leveranser og kvalitetsport.
8. Forklarende prosa, eksempler og wireframes.

Kontrakter med status `draft`, `candidate` eller `blocked` er ikke autoritative.
Codex kan oppdatere `evidence_status`, men bare prosjekteieren kan oppdatere
`owner_decision`. Ikke sett en kontrakt til `frozen` før styrende ADR-er er
eiergodkjent og valideringstestene finnes.

## Tillatte støtteområder

Utviklingsarbeid kan skrive til:

- repositoryets arbeidsområde;
- repositorylokal eller eksplisitt temp-basert virtuell miljømappe;
- `build/`, `dist/`, `artifacts/`, `logs/`, `tests/`, `tools/` og `spikes/`;
- `%TEMP%\MediaSyncHome-Spike\<run-id>`;
- `%LOCALAPPDATA%\MediaSyncHome-Spike\<run-id>`;
- Task Scheduler-mappen `\MediaSyncHome-Spike\<run-id>` bare når arbeidspakken uttrykkelig krever det;
- en dedikert lokal labrot med validert `.mediasync_test_root`-markør.

SMB-lab, produksjons-NAS eller reelle brukerdata er ikke del av gjeldende 0B-scope.
Sync-, ownership-, recovery-, replace-, cleanup- og filsystemprober kan bare mutere
labområder med korrekt markør, matching `run_id` og validert rotidentitet. Bruk aldri
ekte Bilder-, Dokumenter-, Skrivebord-, diskrot- eller produksjons-NAS-data.

## Absolutte sikkerhetsinvarianter

- Ingen `/MIR`, `/PURGE`, `/MOVE`, `/MOV`, `shell=True`, `pickle`, `eval`, `exec` eller dynamisk payloadkode.
- Ingen endelig produktdatabase, syncmotor, produksjons-Robocopy eller muterende produksjonsflyt i 0B.
- Ett skrivbart målrotområde har én autorisert writer-installasjon per `ownership_epoch`; writable SMB er utsatt.
- Ukjent `.mediasync`-innhold ekskluderes, adopteres, repareres eller ryddes aldri stille.
- To lokale prosesser er ikke bevis for cross-machine SMB-eierskap.
- Ekstern child-prosess får ikke kjøre før Job Object-containment er aktiv når arbeidspakken tester dette.
- Manglende miljøbevis markeres `BLOCKED_BY_ENVIRONMENT`; resultater skal aldri fabrikeres, overdrives eller erstattes av mocks.
- Ingen hemmeligheter, reelle NAS-legitimasjoner eller personlige filnavn skal legges i repository, logger eller rapporter.

## Delvise blockers og stoppregler

Et blokkert eksperiment stopper ikke uavhengige, ikke-muterende eksperimenter i samme
arbeidspakke. Marker eksperimentet `BLOCKED_BY_ENVIRONMENT` og fortsett bare når
videre arbeid ikke avhenger av det.

Stopp hele arbeidspakken når:

- videre arbeid vil bryte en sikkerhetsgrense;
- videre arbeid avhenger av et manglende bevis som ikke er scope-redusert av eier;
- bindende dokumenter eller fryste kontrakter motsier hverandre;
- testen ville kreve reelle brukerdata, writable SMB eller produksjonsinfrastruktur;
- resultatet ellers måtte fabrikeres eller overdrives;
- arbeidspakkens kvalitetsport er evaluert.

## Påkrevd leveranse fra 0B

Oppdater minst de filene som faktisk endres av 0B-slicen. Første 0B-slice skal prioritere:

- oppdatert `AGENTS.md` og operativ prompt for 0B;
- kontraktsvalidering for `schema/contracts-manifest.yaml`, JSON Schema-eksempler,
  reason codes og state machines;
- tester som beviser at ukjent/ulovlig kontraktsdrift feiler;
- status-/traceabilityoppdatering når konkret bevis foreligger.

Ingen kontrakt skal settes til `frozen` før ADR-026 og alle styrende ADR-er er
eiergodkjent og valideringstestene dekker drift.

## Kontroller

Kjør alle kontroller som faktisk finnes. Minimum for denne 0B-slicen:

```powershell
python tools\validate_contracts.py
python tools\validate_handoff.py
python tools\build_adr_docs.py --check
python tools\build_master.py --check
python -m pytest
python -m ruff check .
```

Når `src/`, mypy og import-linter er konfigurert, utvides minimum med:

```powershell
python -m mypy src
python -m importlinter
```

En manglende eller ikke-kjørbar kontroll registreres som manglende; den omtales aldri som bestått.
