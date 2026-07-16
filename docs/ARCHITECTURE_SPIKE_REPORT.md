# Arkitekturspike — Milepæl 0A

## Samlet status

| Arbeidspakke | Status | Branch/commit | Rapport-/artefaktsti | Blocker |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `spike/0a0-environment-preflight` / baseline `d3282ef` | `docs/ARCHITECTURE_SPIKE_REPORT.md` | Ingen 0A.0-blocker |
| 0A.1 — Prosess og IPC | not_started | | | Avventer eierstart; lokal fixture er mulig |
| 0A.2 — Endpoint-eierskap | not_started | | | To-klient SMB-lab mangler for global writer-bevis |
| 0A.3 — Recovery og stier | not_started | | | Avventer eierstart; lokal labrot må opprettes i arbeidspakken |
| 0A.4 — SQLite og kapasitet | not_started | | | Avventer eierstart; lokal benchmarkfixture må opprettes i arbeidspakken |
| 0A.5 — Windows argv/pakking | not_started | | | Pakkeverktøy og ren Windows-VM mangler |
| 0A.6 — Beslutningsreview | blocked | | | Avventer 0A.1–0A.5-bevis |

## Miljøpreflight

| Felt | Verdi |
|---|---|
| Dato | 2026-07-16 |
| Repository/branch/commit | `C:\claude\mediasynch`, `spike/0a0-environment-preflight`, baseline `d3282ef3cff814dd9e493f068f5bb288d42ffe7e` |
| Windows-utgave/build | Microsoft Windows 11 Home, version `10.0.26200`, build `26200`, x64 |
| CPU-arkitektur | x64 PC, Intel Core i7-13700F, 16 kjerner / 24 logiske prosessorer |
| Python/PowerShell/Git | PowerShell `5.1.26100.8875`; Git `2.38.1.windows.1`; default Python `3.10.6`; Python launcher har også `3.13.14`; valideringsvenv bruker `jsonschema 4.26.0` og `PyYAML 6.0.3` |
| Windows SDK/API-tilgang | Runtime-API-er funnet via `ctypes`: `CreateJobObjectW`, `AssignProcessToJobObject`, `SetInformationJobObject`, `CreateNamedPipeW`, `GetSystemDirectoryW`, `CreateFileW`, `GetFinalPathNameByHandleW`, `ReplaceFileW`, `MoveFileExW`; SDK-/buildverktøy `cl`, `rc` og `signtool` ikke funnet |
| Administratorstatus | Unelevated interaktiv sesjon; `IsAdministrator=False`; administratorgrupper er deny-only i tokenet |
| Task Scheduler-tilgang | Read-only query fungerer med `schtasks /Query` mot `\Microsoft\Windows\Defrag\ScheduledDefrag`; ingen oppgaver ble opprettet |
| Windows-klient/VM A | Gjeldende host `Panther`, Windows 11 Home; hypervisor er present |
| Windows-klient/VM B | Ikke tilgjengelig i denne sesjonen; `Get-VM` mangler |
| SMB-server/share | Ingen aktiv `Get-SmbConnection` eller `Get-SmbMapping`; ingen `MEDIASYNC`/SMB-labvariabler; ingen `.mediasync_test_root` ved `C:\claude` eller repositoryroten |
| Lokale filsystemer | Lokale NTFS-volumer `C:` og `D:`; flyttbare FAT32-volumer `E:` og `F:`; `LongPathsEnabled=1` |
| Fri plass | `C:` ca. 231.73 GB fri av 1861.39 GB; `D:` ca. 183.71 GB fri av 892.87 GB; `E:` og `F:` er flyttbare FAT32-volumer med ca. 14.37 GB og 7.48 GB fri |
| PySide6/BLAKE3/Nuitka | Ikke installert i default Python 3.10 eller Python 3.13 |
| Sikkerhetsprogramvare/policy | Windows Defender aktiv; sanntidsbeskyttelse, tamper protection, behavior monitor og IOAV er aktivert |
| Utførende | Codex i lokal, unelevated PowerShell-sesjon |

## Kjørbarhetsmatrise

| Bevis | Klassifisering | Mangler/forutsetning | Sikker neste handling |
|---|---|---|---|
| Engine Host discovery/IPC | `RUNNABLE_WITH_LOCAL_FIXTURE` | Produktkode finnes ikke ennå; 0A.1 må lage minimal ikke-muterende host/client-fixture | Start 0A.1 på egen branch etter eiergodkjenning |
| Suspended child → Job Object → resume | `RUNNABLE_WITH_LOCAL_FIXTURE` | 0A.1 må lage instrumentert child-fixture; ingen admin kreves observert nå | Start 0A.1 og bevis containment før child resume |
| To-klient SMB writer ownership | `REQUIRES_USER_LAB_ACTION` | Mangler ekte andre Windows-klient/VM og dedikert SMB-lab med markert testrot | Eier må stille to-klient SMB-lab til rådighet før cross-machine-radene i 0A.2 kan bestås |
| `.mediasync`-klassifisering | `RUNNABLE_WITH_LOCAL_FIXTURE` | 0A.2 må opprette dedikert lokal labrot med gyldig `.mediasync_test_root` | Start lokal klassifiseringsharness i 0A.2 uten å late som dette beviser SMB |
| Short managed-object path | `RUNNABLE_WITH_LOCAL_FIXTURE` | Krever dedikert labrot; ingen produktstier eller brukerdata kan brukes | Start 0A.3 med lokal NTFS-labfixture |
| Replace/fallback crashpunkter | `RUNNABLE_WITH_LOCAL_FIXTURE` | Krever dedikert labrot og fault-injection-fixture; ikke kjørt i 0A.0 | Start 0A.3 med marker-guarded labrot |
| SourceReadGuard/fallback | `RUNNABLE_WITH_LOCAL_FIXTURE` | Krever lokal filfixture og eksplisitt fallbackpolicy; ikke kjørt i 0A.0 | Start 0A.3 etter eierstart |
| Én kontra to databaser | `RUNNABLE_WITH_LOCAL_FIXTURE` | Krever benchmarkdatabase i repo-/temp-artefaktområde; ingen produktdatabase | Start 0A.4 med generert testdata |
| 1M state/kapasitetsmåling | `RUNNABLE_WITH_LOCAL_FIXTURE` | Krever tid, lokal diskplass og råmålinger; ingen ekstern lab nødvendig | Start 0A.4 og lagre råmålinger i artefakter |
| GetSystemDirectoryW/argv | `RUNNABLE_NOW` | Runtime-API og Robocopy finnes; sikker serializer/harness er ikke implementert | Start 0A.5 for argv- og systemsti-harness |
| Ren Windows-pakkebygg | `BLOCKED_BY_ENVIRONMENT` | PySide6, BLAKE3, Nuitka, Windows SDK build/signing tools og ren Windows-VM mangler | Installer/frys toolchain og/eller still ren VM til rådighet før 0A.5-pakkebevis |

Tillatte klassifiseringer: `RUNNABLE_NOW`, `RUNNABLE_WITH_LOCAL_FIXTURE`, `REQUIRES_USER_LAB_ACTION`, `BLOCKED_BY_ENVIRONMENT`, `OUT_OF_SCOPE`.

## Bevismatrise

| Bevis | Arbeidspakke | Miljø | Kommando/test | Resultat | Artefakt/logg | ADR |
|---|---|---|---|---|---|---|
| Engine Host discovery/IPC | 0A.1 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-001, ADR-002 |
| Suspended child → Job Object → resume | 0A.1 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-013 |
| To-klient SMB writer ownership | 0A.2 | `REQUIRES_USER_LAB_ACTION` | Ikke kjørt i 0A.0 | `BLOCKED` | Mangler to-klient SMB-lab | ADR-006, ADR-016, ADR-019 |
| `.mediasync`-klassifisering | 0A.2 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-020 |
| Short managed-object path | 0A.3 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-024 |
| Replace/fallback crashpunkter | 0A.3 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-004, ADR-007, ADR-011 |
| SourceReadGuard/fallback | 0A.3 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-010, ADR-022, ADR-023 |
| Én kontra to databaser | 0A.4 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-003, ADR-018 |
| 1M state/kapasitetsmåling | 0A.4 | `RUNNABLE_WITH_LOCAL_FIXTURE` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-003, ADR-018 |
| GetSystemDirectoryW/argv | 0A.5 | `RUNNABLE_NOW` | Ikke kjørt i 0A.0 | `INCONCLUSIVE` | Se kjørbarhetsmatrise | ADR-027 |
| Ren Windows-pakkebygg | 0A.5 | `BLOCKED_BY_ENVIRONMENT` | Ikke kjørt i 0A.0 | `BLOCKED` | Mangler pakkeverktøy/ren VM | ADR-028 |

Resultatverdier: `PASS`, `FAIL`, `BLOCKED`, `INCONCLUSIVE`.

## Testmiljø og reproduksjon

0A.0 ble kjørt som en ikke-muterende preflight. Ingen Task Scheduler-oppgave, SMB-lock, `.mediasync`-labrot, SQLite-produktdatabase, Engine Host, GUI, syncmotor eller produksjonsadapter ble opprettet.

Baseline ble kontrollert med streng hashverifisering før Git-initialisering og før første dokumentendring. Prosjektet ble deretter flyttet til `C:\claude\mediasynch` etter brukeravklaring om plassering.

### Kommandojournal

```powershell
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\validate_handoff.py --verify-bundle
# PASS: MediaSync Home handoff bundle validation completed

git init -b chore/spec-baseline-v2.9.2
git add .
git commit -m "chore: add MediaSync Home specification v2.9.2"
# d3282ef chore: add MediaSync Home specification v2.9.2

git switch -c spike/0a0-environment-preflight
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short --branch

& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\validate_handoff.py
# PASS: MediaSync Home handoff bundle validation completed

Get-CimInstance Win32_OperatingSystem
Get-CimInstance Win32_ComputerSystem
Get-CimInstance Win32_Processor
$PSVersionTable
git --version
python --version
py -0p

python -c "import importlib.util, platform, sqlite3, sys; ..."
py -3.13 -c "import sys, sqlite3; ..."
py -3.13 -c "import importlib.util; ..."

& "$env:windir\System32\whoami.exe" /groups
schtasks /Query /FO LIST /TN "\Microsoft\Windows\Defrag\ScheduledDefrag"
Get-ComputerInfo -Property CsHypervisorPresent,OsName,OsVersion,WindowsProductName,WindowsVersion
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
python -c "import ctypes; ..."
Get-Volume
Get-MpComputerStatus
Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct
Get-SmbConnection
Get-SmbMapping
rg -n -i "C:\\Users|gmail|<redacted-local-account-marker>" .
```

Notater:

- `Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All` returnerte at operasjonen krever forhøying.
- Målrettet scan for personlige brukersti-/konto-/epoststrenger fant ingen treff. Den lokale kontomarkøren er redigert bort fra rapporten.
- En bredere sikkerhetsord-scan fant bare forventede fagtermer i spesifikasjonen, som `token`, `credential` og eksempel-UNC.

## Målinger

Ingen ytelses- eller kapasitetsmålinger ble kjørt i 0A.0. Diskplass og verktøyversjoner er registrert som preflightdata. Median/P95, SQLite-størrelse, peak RSS, WAL-atferd og krasjpunkter skal først måles i de relevante 0A.3/0A.4-arbeidspakkene.

## Sikkerhetsavvik og blockers

| ID | Arbeidspakke | Beskrivelse | Konsekvens | Sikker midlertidig handling | Eier |
|---|---|---|---|---|---|
| 0A0-BLK-001 | 0A.2 | Ingen andre Windows-klient/VM og ingen dedikert SMB-lab med `.mediasync_test_root` er tilgjengelig | Cross-machine writer ownership, fremmed owner, takeover og stale epoch kan ikke bestås | Lever lokal harness og marker SMB-radene `BLOCKED` til eier stiller lab | Eier |
| 0A0-BLK-002 | 0A.5 | PySide6, BLAKE3, Nuitka, Windows SDK build/signing tools og ren Windows-VM mangler | Reproduserbar pakking kan ikke bevises i nåværende miljø | Kjør argv/systemsti-delen separat; utsett pakkebevis til toolchain/VM finnes | Eier |
| 0A0-BLK-003 | 0A.2/0A.5 | Hypervisor er present, men `Get-VM` mangler og valgfri Windows-feature-query krever elevation | Codex kan ikke selv inventere eller orkestrere lokal VM-lab | Eier må bekrefte VM-oppsett eller gi eksplisitt labinstruks | Eier |

## Beslutninger

Ingen ADR-er ble endret i 0A.0. Preflighten avdekket miljøblockers, men ingen ny beslutningsblocker som Codex bør registrere i `docs/adr/catalog.yaml` nå. Alle `owner_decision`-felt forblir eierstyrt.

## Anbefalt rekkefølge

1. Start `0A.1 — Prosess og IPC`, fordi runtime-API-er, Python og unelevated lokal prosesskontekst er tilgjengelig.
2. Start lokale deler av `0A.3` og `0A.4` etter 0A.1 dersom eier ønsker mer lokalt bevis før SMB-lab er klar.
3. Forbered dedikert to-klient SMB-lab før `0A.2` skal bestå cross-machine writer ownership.
4. Forbered PySide6/BLAKE3/Nuitka/Windows SDK og en ren Windows-VM før `0A.5` skal bestå pakkebevis.
5. Kjør `0A.6` først etter at 0A.1–0A.5 enten har bestått eller fått eksplisitt eiergodkjent scope-reduksjon.

## Bevisst ikke implementert

0A.0 opprettet ikke produktkode, endelig produktdatabase, migrasjon, Engine Host, GUI, syncmotor, Robocopy-adapter, Task Scheduler-oppgave, SMB-lock eller muterende filfixture. Ingen reelle brukerdata, produksjons-NAS, Bilder-/Dokumenter-/Skrivebord-stier eller diskrot ble brukt som testgrunnlag.
