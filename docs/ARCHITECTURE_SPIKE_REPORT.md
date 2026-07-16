# Arkitekturspike — Milepæl 0A

## Samlet status

| Arbeidspakke | Status | Branch/commit | Rapport-/artefaktsti | Blocker |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | not_started | | | |
| 0A.1 — Prosess og IPC | not_started | | | |
| 0A.2 — Endpoint-eierskap | not_started | | | |
| 0A.3 — Recovery og stier | not_started | | | |
| 0A.4 — SQLite og kapasitet | not_started | | | |
| 0A.5 — Windows argv/pakking | not_started | | | |
| 0A.6 — Beslutningsreview | not_started | | | |

## Miljøpreflight

| Felt | Verdi |
|---|---|
| Dato | |
| Repository/branch/commit | |
| Windows-utgave/build | |
| CPU-arkitektur | |
| Python/PowerShell/Git | |
| Windows SDK/API-tilgang | |
| Administratorstatus | |
| Task Scheduler-tilgang | |
| Windows-klient/VM A | |
| Windows-klient/VM B | |
| SMB-server/share | |
| Lokale filsystemer | |
| Fri plass | |
| PySide6/BLAKE3/Nuitka | |
| Sikkerhetsprogramvare/policy | |
| Utførende | |

## Kjørbarhetsmatrise

| Bevis | Klassifisering | Mangler/forutsetning | Sikker neste handling |
|---|---|---|---|
| Engine Host discovery/IPC | | | |
| Suspended child → Job Object → resume | | | |
| To-klient SMB writer ownership | | | |
| `.mediasync`-klassifisering | | | |
| Short managed-object path | | | |
| Replace/fallback crashpunkter | | | |
| SourceReadGuard/fallback | | | |
| Én kontra to databaser | | | |
| 1M state/kapasitetsmåling | | | |
| GetSystemDirectoryW/argv | | | |
| Ren Windows-pakkebygg | | | |

Tillatte klassifiseringer: `RUNNABLE_NOW`, `RUNNABLE_WITH_LOCAL_FIXTURE`, `REQUIRES_USER_LAB_ACTION`, `BLOCKED_BY_ENVIRONMENT`, `OUT_OF_SCOPE`.

## Bevismatrise

| Bevis | Arbeidspakke | Miljø | Kommando/test | Resultat | Artefakt/logg | ADR |
|---|---|---|---|---|---|---|
| Engine Host discovery/IPC | 0A.1 | | | | | |
| Suspended child → Job Object → resume | 0A.1 | | | | | |
| To-klient SMB writer ownership | 0A.2 | | | | | |
| `.mediasync`-klassifisering | 0A.2 | | | | | |
| Short managed-object path | 0A.3 | | | | | |
| Replace/fallback crashpunkter | 0A.3 | | | | | |
| SourceReadGuard/fallback | 0A.3 | | | | | |
| Én kontra to databaser | 0A.4 | | | | | |
| 1M state/kapasitetsmåling | 0A.4 | | | | | |
| GetSystemDirectoryW/argv | 0A.5 | | | | | |
| Ren Windows-pakkebygg | 0A.5 | | | | | |

Resultatverdier: `PASS`, `FAIL`, `BLOCKED`, `INCONCLUSIVE`.

## Testmiljø og reproduksjon

Oppgi eksakte OS-versjoner, filsystem, nettverksoppsett, VM-/maskinidentiteter og kommandoer. To lokale prosesser skal ikke registreres som cross-machine-bevis.

## Målinger

Ta med rådata, median/P95 der relevant, database-/indeksstørrelse, peak RSS, WAL-atferd, krasjpunkter og observerte API-begrensninger.

## Sikkerhetsavvik og blockers

| ID | Arbeidspakke | Beskrivelse | Konsekvens | Sikker midlertidig handling | Eier |
|---|---|---|---|---|---|

## Beslutninger

Lenk til ADR-er. Codex kan oppdatere `evidence_status`; bare prosjekteieren kan oppdatere `owner_decision`.

## Bevisst ikke implementert

Bekreft eksplisitt at spikene ikke opprettet endelig produktdatabase, endelig migrasjon, syncmotor eller muterende produksjonsflyt.
