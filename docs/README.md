# Dokumentasjon

Denne mappen inneholder de kanoniske fagfilene for MediaSync Home. Bruk tabellene nedenfor for å åpne minst mulig, men tilstrekkelig, kontekst for oppgaven du skal utføre.

> **Gjeldende fase:** `0A.0 — miljø- og sikkerhetspreflight`. Produktkode og endelig databaseskjema er ikke startet.

## Start etter rolle

| Rolle eller behov | Anbefalt inngang |
|---|---|
| Prosjektbesøkende | [`../README.md`](../README.md) |
| Prosjekteier som skal starte Codex | [`../AGENTS.md`](../AGENTS.md), [`HANDOFF_CHECKLIST.md`](HANDOFF_CHECKLIST.md) og [`CODEX_START_PROMPT.md`](CODEX_START_PROMPT.md) |
| Produkt- eller UX-gjennomgang | [`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md), [`RELEASE_SCOPE.md`](RELEASE_SCOPE.md) og [`GUI_AND_UX.md`](GUI_AND_UX.md) |
| Arkitektur- og sikkerhetsreview | [`ARCHITECTURE.md`](ARCHITECTURE.md), [`ENDPOINT_OWNERSHIP.md`](ENDPOINT_OWNERSHIP.md) og [`RECOVERY_PROTOCOL.md`](RECOVERY_PROTOCOL.md) |
| Synkroniseringslogikk | [`SYNC_SEMANTICS.md`](SYNC_SEMANTICS.md) og [`ROBOCOPY_ADAPTER.md`](ROBOCOPY_ADAPTER.md) |
| Datamodell og kontrakter | [`STORAGE_AND_SCHEMA.md`](STORAGE_AND_SCHEMA.md) og [`../schema/README.md`](../schema/README.md) |
| Test og ytelse | [`TEST_PLAN.md`](TEST_PLAN.md), [`PERFORMANCE.md`](PERFORMANCE.md) og [`BENCHMARKS.md`](BENCHMARKS.md) |
| Status og beslutninger | [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md), [`DECISION_REGISTER.md`](DECISION_REGISTER.md) og [`adr/README.md`](adr/README.md) |

## Produkt og brukeropplevelse

| Dokument | Innhold |
|---|---|
| [`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md) | Produktmål, låste standardvalg og releaseomfang. |
| [`RELEASE_SCOPE.md`](RELEASE_SCOPE.md) | Kort leveransestige fra Alpha 0.1 til senere funksjoner. |
| [`GUI_AND_UX.md`](GUI_AND_UX.md) | Informasjonsarkitektur, skjermflyter, designsystem, tilgjengelighet og GUI-ytelse. |
| [`USABILITY_CHECKLIST.md`](USABILITY_CHECKLIST.md) | Register for faktiske brukervennlighetstester. |
| [`LATER_IMPROVEMENTS.md`](LATER_IMPROVEMENTS.md) | Funksjoner som bevisst ligger utenfor første leveranser. |

## Arkitektur og datasikkerhet

| Dokument | Innhold |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Prosessmodell, Engine Host, IPC, autoritetsgrenser og sikkerhetsinvarianter. |
| [`ENDPOINT_OWNERSHIP.md`](ENDPOINT_OWNERSHIP.md) | Endepunktidentitet, `.mediasync`, eksklusiv writer og kontrollert eierskap. |
| [`RECOVERY_PROTOCOL.md`](RECOVERY_PROTOCOL.md) | Journalførte, idempotente fil- og katalogoverganger etter feil eller krasj. |
| [`SYNC_SEMANTICS.md`](SYNC_SEMANTICS.md) | Oppdater, speil, toveis, identiske filer, filtre og plansemantikk. |
| [`ROBOCOPY_ADAPTER.md`](ROBOCOPY_ADAPTER.md) | Robocopy-profiler, staging, argumentbygging og prosessisolasjon. |
| [`STORAGE_AND_SCHEMA.md`](STORAGE_AND_SCHEMA.md) | Kandidatdatamodell, constraints, revisjoner, leases og recovery-state. |

## Implementasjon, drift og kvalitet

| Dokument | Innhold |
|---|---|
| [`MILESTONES.md`](MILESTONES.md) | Sekvensielle arbeidspakker og kvalitetsporter. |
| [`REPOSITORY_AND_CODE_QUALITY.md`](REPOSITORY_AND_CODE_QUALITY.md) | Repositorylayout, avhengighetsregler, CI og kodestandarder. |
| [`OPERATIONS_AND_AUTOMATION.md`](OPERATIONS_AND_AUTOMATION.md) | Task Scheduler, varsler, logging, retention og driftsflyter. |
| [`PERFORMANCE.md`](PERFORMANCE.md) | Strømming, køgrenser, scheduler og målbare ytelsesbudsjetter. |
| [`TEST_PLAN.md`](TEST_PLAN.md) | Unit-, integrasjons-, Windows-, SMB-, recovery-, sikkerhets- og stresstester. |
| [`BENCHMARKS.md`](BENCHMARKS.md) | Register for målte ytelsesresultater og råartefakter. |

## Governance, krav og status

| Dokument | Innhold |
|---|---|
| [`GOVERNANCE.md`](GOVERNANCE.md) | Dokumentpresedens, sikkerhetsgrenser og arbeidsregler. |
| [`REQUIREMENTS_INDEX.md`](REQUIREMENTS_INDEX.md) | Kanoniske krav-ID-er. |
| [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) | Krav mot design, milepæl, implementasjon og test. |
| [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) | Faktisk status, blockers og neste eierhandling. |
| [`ARCHITECTURE_SPIKE_REPORT.md`](ARCHITECTURE_SPIKE_REPORT.md) | Reproduserbare bevis fra Milepæl 0A. |
| [`DECISION_REGISTER.md`](DECISION_REGISTER.md) | Generert arbeidsvisning av ADR-katalogen. |
| [`adr/README.md`](adr/README.md) | Generert, lesbar ADR-oversikt. |
| [`adr/catalog.yaml`](adr/catalog.yaml) | Kanonisk maskinlesbar ADR-status og eierbeslutning. |
| [`REFERENCES.md`](REFERENCES.md) | Primære tekniske referanser. |

## Codex-arbeidspakker

- [`CODEX_START_PROMPT.md`](CODEX_START_PROMPT.md) åpner bare 0A.0.
- [`spikes/README.md`](spikes/README.md) forklarer sekvensen 0A.0–0A.6.
- Hver fil omtalt i [`spikes/README.md`](spikes/README.md) angir leseliste, tillatt skriveområde, eksperimenter og kvalitetsport for én arbeidspakke.
- Codex starter aldri neste arbeidspakke uten eksplisitt eierbeslutning.

## Kanoniske og genererte filer

Fagfilene i denne mappen er kanoniske med mindre de er tydelig merket som genererte. Følgende filer skal ikke redigeres direkte:

- [`../MASTER_SPEC.md`](../MASTER_SPEC.md) — bygges fra fagfilene;
- [`DECISION_REGISTER.md`](DECISION_REGISTER.md) — bygges fra `adr/catalog.yaml`;
- [`adr/README.md`](adr/README.md) — bygges fra `adr/catalog.yaml`.

Etter dokumentendringer:

```powershell
python tools/build_adr_docs.py
python tools/build_master.py
python tools/validate_handoff.py
```

Før første endring i en urørt overleveringspakke brukes i tillegg:

```powershell
python tools/validate_handoff.py --verify-bundle
```

## Presedens ved konflikt

1. Kanoniske produkt- og sikkerhetskrav.
2. ADR-er med `owner_decision = OWNER_ACCEPTED`.
3. Versjonerte kontrakter med status `frozen`.
4. Constraints og genererte typer fra fryste kontrakter.
5. Kjørbare konformitets-, sikkerhets- og arkitekturtester.
6. Gjeldende arbeidspakkes eksplisitte kvalitetsport.
7. Forklarende tekst, eksempler og wireframes.

Se [`../AGENTS.md`](../AGENTS.md) for den operative og fullstendige regelen.
