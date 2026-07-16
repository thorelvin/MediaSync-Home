# AGENTS.md — MediaSync Home

## Gjeldende arbeidsordre

Utfør bare **Milepæl 0A.0 — miljø- og sikkerhetspreflight**. Ikke start 0A.1, 0B eller produktimplementasjon. Prosjekteieren velger og åpner neste arbeidspakke etter gjennomgang.

## Les i denne rekkefølgen

1. `docs/CODEX_START_PROMPT.md`.
2. `docs/HANDOFF_CHECKLIST.md`.
3. `docs/MILESTONES.md`, bare §20.0 og §20.1.
4. `docs/spikes/0A.0_ENVIRONMENT_PREFLIGHT.md`.
5. `docs/ARCHITECTURE_SPIKE_REPORT.md`.
6. `docs/DECISION_REGISTER.md`, `docs/adr/README.md` og `docs/adr/catalog.yaml`.
7. `schema/contracts-manifest.yaml` og `schema/README.md`.
8. `MASTER_SPEC.md` bare når de målrettede dokumentene mangler nødvendig kontekst.

Ikke last hele masterspesifikasjonen inn i arbeidskonteksten som standard.

## Presedens ved konflikt

1. Kanoniske produkt- og sikkerhetskrav i `docs/REQUIREMENTS_INDEX.md`.
2. ADR-er med `owner_decision = OWNER_ACCEPTED`.
3. Versjonerte kontrakter med status `frozen` i `schema/contracts-manifest.yaml`.
4. Databaseconstraints og genererte typer produsert fra fryste kontrakter.
5. Kjørbare konformitets-, sikkerhets- og arkitekturtester.
6. Gjeldende arbeidspakkes eksplisitte leveranser og kvalitetsport.
7. Forklarende prosa, eksempler og wireframes.

Kontrakter med status `draft`, `candidate` eller `blocked` er ikke autoritative. Codex kan oppdatere `evidence_status`, men bare prosjekteieren kan oppdatere `owner_decision`.

## Tillatte støtteområder

Utviklingsarbeid kan skrive til:

- repositoryets arbeidsområde;
- repositorylokal eller eksplisitt temp-basert virtuell miljømappe;
- `build/`, `dist/`, `artifacts/`, `logs/` og `spikes/`;
- `%TEMP%\MediaSyncHome-Spike\<run-id>`;
- `%LOCALAPPDATA%\MediaSyncHome-Spike\<run-id>`;
- Task Scheduler-mappen `\MediaSyncHome-Spike\<run-id>` når arbeidspakken uttrykkelig krever det;
- en dedikert lokal eller SMB-basert labrot med validert `.mediasync_test_root`-markør.

Sync-, ownership-, recovery-, replace-, cleanup- og filsystemprober kan bare mutere labområder med korrekt markør, matching `run_id` og validert rotidentitet. Bruk aldri ekte Bilder-, Dokumenter-, Skrivebord-, diskrot- eller produksjons-NAS-data.

## Absolutte sikkerhetsinvarianter

- Ingen `/MIR`, `/PURGE`, `/MOVE`, `/MOV`, `shell=True`, `pickle`, `eval`, `exec` eller dynamisk payloadkode.
- Ingen endelig produktdatabase, migrasjon, syncmotor eller muterende produksjonsflyt i 0A.
- Ett skrivbart målrotområde har én autorisert writer-installasjon per `ownership_epoch`.
- Ukjent `.mediasync`-innhold ekskluderes, adopteres, repareres eller ryddes aldri stille.
- To lokale prosesser er ikke bevis for cross-machine SMB-eierskap.
- Ekstern child-prosess får ikke kjøre før Job Object-containment er aktiv når arbeidspakken tester dette.
- Manglende miljøbevis markeres `BLOCKED`; resultater skal aldri fabrikeres, overdrives eller erstattes av mocks.
- Ingen hemmeligheter, reelle NAS-legitimasjoner eller personlige filnavn skal legges i repository, logger eller rapporter.

## Delvise blockers og stoppregler

Et blokkert eksperiment stopper ikke uavhengige, ikke-muterende eksperimenter i samme arbeidspakke. Marker eksperimentet `BLOCKED_BY_ENVIRONMENT` og fortsett bare når videre arbeid ikke avhenger av det.

Stopp hele arbeidspakken når:

- videre arbeid vil bryte en sikkerhetsgrense;
- videre arbeid avhenger av et manglende bevis;
- bindende dokumenter eller fryste kontrakter motsier hverandre;
- testen ville kreve reelle brukerdata eller produksjonsinfrastruktur;
- resultatet ellers måtte fabrikeres eller overdrives;
- arbeidspakkens kvalitetsport er evaluert.

## Påkrevd leveranse fra 0A.0

Oppdater minst:

- `docs/ARCHITECTURE_SPIKE_REPORT.md`;
- `docs/IMPLEMENTATION_STATUS.md`;
- `docs/adr/catalog.yaml` dersom preflight avdekker en beslutningsblocker; kjør deretter `python tools/build_adr_docs.py`;
- `docs/REQUIREMENTS_TRACEABILITY.md` bare der faktisk bevis foreligger.

Rapporter eksakte kommandoer, OS-/verktøyversjoner, miljøklassifisering, blockers og hva som bevisst ikke ble implementert. Avslutt med en anbefalt, men ikke automatisk startet, neste arbeidspakke.

## Kontroller

Kjør alle kontroller som faktisk finnes. Før produktrepositoryet er etablert, er minimum:

```powershell
python tools/validate_handoff.py
```

Når Python-verktøyene er konfigurert, utvides minimum med:

```powershell
python -m pytest
python -m ruff check .
python -m mypy src
python -m importlinter
```

En manglende eller ikke-kjørbar kontroll registreres som manglende; den omtales aldri som bestått.
