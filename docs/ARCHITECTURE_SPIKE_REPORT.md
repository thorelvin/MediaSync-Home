# Arkitekturspike — Milepæl 0A

## Samlet status

| Arbeidspakke | Status | Branch/commit | Rapport-/artefaktsti | Blocker |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `spike/0a0-environment-preflight` / baseline `d3282ef` | `docs/ARCHITECTURE_SPIKE_REPORT.md` | Ingen 0A.0-blocker |
| 0A.1 — Prosess og IPC | blocked | `spike/0a1-process-and-ipc` | `spikes/0a1_process_ipc/`, `tests/spikes/0a1_process_ipc/`, `artifacts/0a1/unittest-output.txt` | Lokal IPC/Job Object-fixture består; ekte wrong-SID/remote og non-interactive Task Scheduler-kontekst mangler |
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
| Engine Host discovery/IPC | 0A.1 | Lokal Windows-fixture | `python -m unittest discover -s tests\spikes\0a1_process_ipc -v` | `PASS` | `artifacts/0a1/unittest-output.txt` | ADR-001, ADR-002 |
| Suspended child → Job Object → resume | 0A.1 | Lokal Windows-fixture | `python -m unittest discover -s tests\spikes\0a1_process_ipc -v` | `PASS` | `artifacts/0a1/unittest-output.txt` | ADR-013 |
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

0A.1 ble kjørt som en spike-fixture under `spikes/0a1_process_ipc/` og `tests/spikes/0a1_process_ipc/`. Den oppretter bare lokale, midlertidige named pipes, childprosesser og testkvitteringsfiler. Den oppretter ingen produktdatabase, syncmotor, GUI, Robocopy-prosess, Task Scheduler-oppgave eller filsystemfixture med brukerdata.

### 0A.1 lokal evidens

| Eksperiment | Resultat | Bevis |
|---|---|---|
| Minimal Engine Host-readiness over lokal named pipe | `PASS` | Testhost publiserer readinessfil og aksepterer same-SID handshake |
| Local-only pipe med DACL for aktuell SID + `LOCAL_SYSTEM` og `PIPE_REJECT_REMOTE_CLIENTS` | `PASS` for konfigurasjon og same-SID klient | `win32_ipc_job.py` bruker SDDL `D:P(A;;GA;;;current_sid)(A;;GA;;;SY)` og Win32 pipeflagget |
| Faktisk klienttoken/SID, ikke payload-claim | `PASS` for same-SID klient | Server bruker `ImpersonateNamedPipeClient` + thread token; test sender falsk `claimed_sid` og får bare OS-SID-hash tilbake |
| Protokollmismatch | `PASS` | Nyere/ukjent protokoll returnerer `PROTOCOL_MISMATCH` uten command receipt |
| Reconnect/host-restart/idempotency | `PASS` | Samme idempotency key og payload returnerer samme receipt etter host-restart med samme spike receipt store |
| Idempotency-konflikt | `PASS` | Samme key med annen payload returnerer `IDEMPOTENCY_CONFLICT` |
| `CREATE_SUSPENDED` før brukerkode | `PASS` | Instrumentert child skriver ikke marker før assignment/resume |
| Job Object assignment + kill-on-close | `PASS` | Child legges i Job Object med `KILL_ON_JOB_CLOSE`; close terminerer child |
| Engine Host close/crash stopper child | `PASS` for Job Object-close-scenariet | `test_suspended_child_is_contained_before_resume_and_killed_on_job_close` |
| Task Scheduler-lignende non-interactive sesjon | `BLOCKED_BY_ENVIRONMENT` | Krever opprettet task/credential/session-policy som ikke ble etablert i denne økten |
| Remote eller feil-SID klient | `BLOCKED_BY_ENVIRONMENT` | Krever separat Windows-bruker, remote client eller lab som kan forsøke faktisk feil principal |

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

git switch -c spike/0a1-process-and-ipc
python -m py_compile spikes\0a1_process_ipc\win32_ipc_job.py tests\spikes\0a1_process_ipc\test_win32_ipc_job.py
cmd.exe /c "python -m unittest discover -s tests\spikes\0a1_process_ipc -v > artifacts\0a1\unittest-output.txt 2>&1"
python -m pytest tests\spikes\0a1_process_ipc -q
python -m ruff check .
python -m mypy --version
python -m importlinter --version
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\validate_handoff.py
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\build_adr_docs.py --check
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\build_master.py --check
```

Notater:

- `Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All` returnerte at operasjonen krever forhøying.
- Målrettet scan for personlige brukersti-/konto-/epoststrenger fant ingen treff. Den lokale kontomarkøren er redigert bort fra rapporten.
- En bredere sikkerhetsord-scan fant bare forventede fagtermer i spesifikasjonen, som `token`, `credential` og eksempel-UNC.
- `python -m pytest tests\spikes\0a1_process_ipc -q` besto med `5 passed`.
- `python -m ruff check .` besto.
- `python -m mypy --version` og `python -m importlinter --version` feilet fordi modulene ikke er installert i aktiv Python. De er ikke registrert som bestått.

## Målinger

Ingen ytelses- eller kapasitetsmålinger ble kjørt i 0A.0. Diskplass og verktøyversjoner er registrert som preflightdata. Median/P95, SQLite-størrelse, peak RSS, WAL-atferd og krasjpunkter skal først måles i de relevante 0A.3/0A.4-arbeidspakkene.

## Sikkerhetsavvik og blockers

| ID | Arbeidspakke | Beskrivelse | Konsekvens | Sikker midlertidig handling | Eier |
|---|---|---|---|---|---|
| 0A0-BLK-001 | 0A.2 | Ingen andre Windows-klient/VM og ingen dedikert SMB-lab med `.mediasync_test_root` er tilgjengelig | Cross-machine writer ownership, fremmed owner, takeover og stale epoch kan ikke bestås | Lever lokal harness og marker SMB-radene `BLOCKED` til eier stiller lab | Eier |
| 0A0-BLK-002 | 0A.5 | PySide6, BLAKE3, Nuitka, Windows SDK build/signing tools og ren Windows-VM mangler | Reproduserbar pakking kan ikke bevises i nåværende miljø | Kjør argv/systemsti-delen separat; utsett pakkebevis til toolchain/VM finnes | Eier |
| 0A0-BLK-003 | 0A.2/0A.5 | Hypervisor er present, men `Get-VM` mangler og valgfri Windows-feature-query krever elevation | Codex kan ikke selv inventere eller orkestrere lokal VM-lab | Eier må bekrefte VM-oppsett eller gi eksplisitt labinstruks | Eier |
| 0A1-BLK-001 | 0A.1 | Ekte non-interactive Task Scheduler-session under samme bruker er ikke etablert | 0A.1 kan ikke bevise registrert trigger-client/session-policy fullt ut | Eier må tillate/opprette dedikert `\MediaSyncHome-Spike\<run-id>` task eller gi testcredential/sessionoppsett | Eier |
| 0A1-BLK-002 | 0A.1 | Feil-SID eller remote pipe-klient er ikke tilgjengelig | DACL/local-only-policy er konfigurert, men faktisk avvisning av annen principal er ikke demonstrert | Kjør 0A.1-identitetstesten fra separat Windows-bruker/VM eller remote klient | Eier |

## Beslutninger

Ingen ADR-er ble endret i 0A.0 eller den lokale 0A.1-fixturen. ADR-001, ADR-002 og ADR-013 bør forbli `PROPOSED` til de blokkerte identitets-/Task Scheduler-radene er bevist eller eier eksplisitt godkjenner en scope-reduksjon. Alle `owner_decision`-felt forblir eierstyrt.

## Anbefalt rekkefølge

1. Fullfør de blokkerte 0A.1-identitetsradene med dedikert Task Scheduler-session og feil-SID/remote klient, eller få eksplisitt eiergodkjent scope-reduksjon.
2. Start lokale deler av `0A.3` og `0A.4` dersom eier ønsker mer lokalt bevis før SMB-lab er klar.
3. Forbered dedikert to-klient SMB-lab før `0A.2` skal bestå cross-machine writer ownership.
4. Forbered PySide6/BLAKE3/Nuitka/Windows SDK og en ren Windows-VM før `0A.5` skal bestå pakkebevis.
5. Kjør `0A.6` først etter at 0A.1–0A.5 enten har bestått eller fått eksplisitt eiergodkjent scope-reduksjon.

## Bevisst ikke implementert

0A.0 og 0A.1 opprettet ikke produktkode under `src/`, endelig produktdatabase, migrasjon, GUI, syncmotor, Robocopy-adapter, Task Scheduler-oppgave, SMB-lock eller muterende filfixture. 0A.1 opprettet bare spikehost, spikeklient, instrumentert childprosess og midlertidige receipt-/markerfiler under testens tempområde. Ingen reelle brukerdata, produksjons-NAS, Bilder-/Dokumenter-/Skrivebord-stier eller diskrot ble brukt som testgrunnlag.
