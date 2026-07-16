# Arkitekturspike — Milepæl 0A

## Samlet status

| Arbeidspakke | Status | Branch/commit | Rapport-/artefaktsti | Blocker |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `spike/0a0-environment-preflight` / baseline `d3282ef` | `docs/ARCHITECTURE_SPIKE_REPORT.md` | Ingen 0A.0-blocker |
| 0A.1 — Prosess og IPC | blocked | `spike/0a1-process-and-ipc` | `spikes/0a1_process_ipc/`, `tests/spikes/0a1_process_ipc/`, `artifacts/0a1/unittest-output.txt` | Lokal IPC/Job Object-fixture består; ekte wrong-SID/remote og non-interactive Task Scheduler-kontekst mangler |
| 0A.2 — Endpoint-eierskap | blocked | `spike/0a2-endpoint-ownership-local` | `spikes/0a2_endpoint_ownership/`, `tests/spikes/0a2_endpoint_ownership/`, `artifacts/0a2/` | Lokal klassifisering/lock/takeover bestått; to-klient SMB-lab og endelig BLAKE3-marker mangler |
| 0A.3 — Recovery og stier | passed | `spike/0a3-recovery-and-paths` | `spikes/0a3_recovery_paths/`, `tests/spikes/0a3_recovery_paths/`, `artifacts/0a3/` | Lokal NTFS/path/recovery bestått; SMB SourceReadGuard ikke kjørt uten SMB-lab |
| 0A.4 — SQLite og kapasitet | passed | `spike/0a4-sqlite-capacity` | `spikes/0a4_sqlite_capacity/`, `tests/spikes/0a4_sqlite_capacity/`, `artifacts/0a4/` | Lokal 1M SQLite-/kapasitetsmåling bestått; ADR-003 anbefales, men eiergodkjenning gjenstår |
| 0A.5 — Windows argv/pakking | blocked | `spike/0a5-windows-argv-and-packaging` | `spikes/0a5_windows_packaging/`, `tests/spikes/0a5_windows_packaging/`, `artifacts/0a5/` | `GetSystemDirectoryW`/argv bestått; PySide6/BLAKE3/Nuitka/SDK/signing tools og ren Windows-VM mangler |
| 0A.6 — Beslutningsreview | blocked | `spike/0a6-decision-review` | `docs/adr/0A_DECISION_REVIEW.md` | Eierbeslutninger, SMB-/Task Scheduler-lab og pakkemiljø mangler; 0B forblir blokkert |

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
| `.mediasync`-klassifisering | 0A.2 | Marker-validert lokal NTFS-labrot | `python -m unittest discover -s tests\spikes\0a2_endpoint_ownership -v` | `PASS` | `artifacts/0a2/unittest-output.txt`, `artifacts/0a2/demo-summary.json` | ADR-020 |
| Short managed-object path | 0A.3 | Marker-validert lokal NTFS-labrot | `python -m unittest discover -s tests\spikes\0a3_recovery_paths -v` | `PASS` | `artifacts/0a3/unittest-output.txt`, `artifacts/0a3/demo-summary.json` | ADR-024 |
| Replace/fallback crashpunkter | 0A.3 | Marker-validert lokal NTFS-labrot | `python -m unittest discover -s tests\spikes\0a3_recovery_paths -v` | `PASS` | `artifacts/0a3/unittest-output.txt`, `artifacts/0a3/demo-summary.json` | ADR-004, ADR-007, ADR-011 |
| SourceReadGuard/fallback | 0A.3 | Lokal NTFS pass; SMB-lab ikke tilgjengelig | `python -m unittest discover -s tests\spikes\0a3_recovery_paths -v` | `PASS` | Lokal guard returnerte `DENY_WRITE_AND_DELETE`; bruk fallback for uprovede SMB-endepunkter | ADR-010, ADR-022, ADR-023 |
| Én kontra to databaser | 0A.4 | Lokal SQLite-fixture i temp | `python -m unittest discover -s tests\spikes\0a4_sqlite_capacity -v` | `PASS` | `artifacts/0a4/unittest-output.txt`, `artifacts/0a4/benchmark-summary.json` | ADR-003, ADR-011, ADR-018 |
| 1M state/kapasitetsmåling | 0A.4 | Lokal SQLite-fixture i temp | `python spikes\0a4_sqlite_capacity\sqlite_capacity.py benchmark --rows 1000000 --query-repetitions 30 --output artifacts\0a4\benchmark-summary.json` | `PASS` | 1M rader per kandidat; peak RSS ca. 100 MiB; indeksert parent-page P95 < 1 ms | ADR-003, ADR-018 |
| GetSystemDirectoryW/argv | 0A.5 | Lokal Windows-runtime | `python -m unittest discover -s tests\spikes\0a5_windows_packaging -v` | `PASS` | `artifacts/0a5/unittest-output.txt`, `artifacts/0a5/demo-summary.json` | ADR-027 |
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

0A.2 ble kjørt som en marker-validert lokal endpoint-ownership-spike under `spikes/0a2_endpoint_ownership/` og `tests/spikes/0a2_endpoint_ownership/`. Harnesset muterer bare temp-labrøtter med `.mediasync_test_root`; to prosesser på samme maskin brukes bare som lokal lockharness, ikke som SMB-bevis.

### 0A.2 lokal evidens

| Eksperiment | Resultat | Bevis |
|---|---|---|
| Alle ni `.mediasync`-klassifiseringstilstander | `PASS` | Demo og test dekker `ABSENT`, `VALID_OWNED`, `VALID_FOREIGN`, `VALID_READ_ONLY_NEWER_SCHEMA`, `PARTIAL_CONTROL_AREA`, `UNKNOWN_EMPTY_DIRECTORY`, `UNKNOWN_NONEMPTY_DIRECTORY`, `CASE_ALIAS_COLLISION`, `CORRUPT_MARKER` |
| Ukjent ikke-tom `.mediasync` | `PASS` | Klassifiseres `UNKNOWN_NONEMPTY_DIRECTORY`, ekskluderes ikke fra snapshot og gir ingen mutasjonstillatelse |
| Markerchecksum og root identity | `PASS` for spike-algoritme | Tamper gir `CORRUPT_MARKER`; root identity mismatch gir `PARTIAL_CONTROL_AREA`; checksumalgoritmen er `SHA256-0A2-SPIKE` fordi `blake3` mangler |
| Lokal eksklusiv `mutation.lock` | `PASS` lokalt | Første Win32 handle med share-mode 0 blokkerer andre åpning til handle close; dette er ikke cross-machine-bevis |
| Fremmed owner | `PASS` lokalt | Klassifiseres `VALID_FOREIGN`, read-only og ingen mutasjonstillatelse |
| Kontrollert takeover | `PASS` lokalt | Ny owner publiseres med økt `ownership_epoch`; gammel permit blir stale |
| Namespace cleanup | `PASS` lokalt | Egen installasjonsnamespace kan ryddes i lab; fremmed namespace gir `REFUSED_FOREIGN_NAMESPACE`; feil labmarkør stopper cleanup |
| To-klient SMB writer ownership | `BLOCKED_BY_ENVIRONMENT` | Ingen dedikert SMB-share og to ekte Windows-klienter/VM-er tilgjengelig |

0A.3 ble kjørt som en marker-validert lokal filesystem-spike under `spikes/0a3_recovery_paths/` og `tests/spikes/0a3_recovery_paths/`. Hver testlab opprettes i temp med `.mediasync_test_root`, matching `run_id`, matching root identity og `cleanup_allowed=true`; harnesset nekter mutasjon dersom markøren ikke validerer.

### 0A.3 lokal evidens

| Eksperiment | Resultat | Bevis |
|---|---|---|
| Marker-guarded labrot | `PASS` | Test endrer markør og får lukket feil før mutasjon |
| Lang logisk sti → kort managed object + manifest | `PASS` | Demo: logisk sti 285 tegn, speilet kontrollsti 348 tegn, managed payload 146 tegn |
| Speilet intern sti ville overskride legacy-grense | `PASS` | `mirrored_control_path_length > 260` i demo og test |
| Final commit/fallback replace/version/restore | `PASS` | Gammel final bevares som `VERSION` object og restore bruker manifestets relative logical path |
| Quarantine/restore med opaque object | `PASS` | Target flyttes til `QUARANTINE` object; fysisk payload speiler ikke brukertreet; restore bruker manifest |
| `ReplaceFileW` på samme lokale volum | `PASS` | Probe viser `final_content=new`, `backup_content=old`, `same_volume=true` |
| Journalført fallback og crashvinduer | `PASS` | Tester dekker `before_intent`, `after_flush`, `after_intent`, `after_preserve`, `after_apply`, `after_verify` |
| Recovery basert på faktisk filtilstand | `PASS` | Før apply beholdes gammel final; etter apply/verify registreres final verification/catalog idempotent |
| Katalogoperasjon med typekonflikt | `PASS` | Eksisterende fil på katalogsti gir `TARGET_TYPE_CONFLICT`, ikke idempotent suksess |
| Lokal NTFS `SourceReadGuard` | `PASS` | Probe åpner read handle uten write/delete-share; samtidig write forsøkes blokkert |
| SMB `SourceReadGuard` | `BLOCKED_BY_ENVIRONMENT` | Ingen SMB-lab tilgjengelig i denne økten; fallbackpolicy for uprovet endpoint er `POST_TRANSFER_HASH_ONLY` |

0A.4 ble kjørt som en lokal SQLite-spike under `spikes/0a4_sqlite_capacity/` og `tests/spikes/0a4_sqlite_capacity/`. Harnesset oppretter bare kandidatdatabaser i temp, ikke endelig produktskjema, og endrer ikke `schema/catalog.sql` eller `schema/recovery.sql`.

### 0A.4 lokal evidens

| Eksperiment | Resultat | Bevis |
|---|---|---|
| Minimal én-databasekandidat | `PASS` | Runstart binder receipt, run og recoverybinding i én FULL-transaksjon; crash før commit etterlater ingen partial readiness |
| Minimal to-databasekandidat | `PASS` | Durable handoff går `PREPARED` → `PEER_COMMITTED` → `SOURCE_CONFIRMED` → `COMPLETED` uten samtidig write-transaksjon i begge databaser |
| Crashvinduer i databasehandoff | `PASS` | Tester krasjer etter catalog prepared, recovery peer committed og catalog source confirmed; startup-reconciler fullfører idempotent |
| Backup-/restore-epoch | `PASS` | Én-db backup har 1 medlem; to-db backup har `catalog` + `recovery`; blandede catalog/recovery-epoker avvises før restore |
| 1M bulkinnlasting | `PASS` | Én-db: 1,000,000 rader på 35.107 s; to-db catalog: 1,000,000 rader på 35.466 s |
| Database- og WAL-størrelse | `PASS` | Én-db total 333,422,592 bytes; to-db total 333,426,688 bytes, hvor `recovery.sqlite` er 286,720 bytes; WAL før checkpoint 48,257,592 bytes og 0 etter checkpoint |
| Representative query plans/P95 | `PASS` | Alle varme queries bruker indekser; parent-page P95 0.101 ms én-db og 0.171 ms to-db; hash lookup P95 ≤ 0.027 ms; coverage count P95 ≤ 0.087 ms |
| Peak RSS ved 1M | `PASS` | Målt working set peak 104,202,240 bytes én-db og 105,373,696 bytes to-db, under 400 MiB-gaten |
| Lokal AppData-/`SQLITE_FULL`-oppførsel | `PASS` | Kontrollert liten catalog treffer `SQLITE_FULL`; committet recoverybevis i separat recovery-store bevares |
| Kode-/testkompleksitet | `PASS` | Én-db trenger 1 runstart-write og 1 backupmedlem; to-db trenger 3 runstart-writes, 2 handofftabeller, 2 backupmedlemmer og flere recoverytilstander |
| Codex-anbefaling for ADR-003 | `RECOMMENDED` | Anbefal to lokale SQLite-databaser med eksplisitte handoffs: mer kompleksitet, men bedre isolasjon av liten FULL-synkron recovery-state fra stor rekonstruerbar catalogvekst |

0A.5 ble kjørt som en lokal Windows argv-/pakkeprobe under `spikes/0a5_windows_packaging/` og `tests/spikes/0a5_windows_packaging/`. Harnesset startet bare en instrumentert Python-child for argv-verifikasjon; det startet ikke Robocopy og utførte ingen backup.

### 0A.5 lokal evidens

| Eksperiment | Resultat | Bevis |
|---|---|---|
| `GetSystemDirectoryW`-resolver | `PASS` | `Robocopy.exe` ble resolvert under Windows systemkatalog og final path ble validert via handle; PATH-hijack-fixture ble ignorert |
| Robocopy executable-diagnostikk | `PASS` | Demo registrerte final path `\\?\C:\Windows\System32\Robocopy.exe`, SHA-256 og file version `10.0.26100.8737` |
| Kanonisk Windows argv-builder | `PASS` | Egen serializer round-trippet via `CommandLineToArgvW` for tomme args, spaces, quotes, UNC, Unicode, trailing backslash, switch-lignende navn og lang kommando |
| Instrumentert child round-trip | `PASS` | Python-child mottok eksakt payload for syv corpuscases; maksimal command-line-lengde 30,152 tegn |
| Forbudte Robocopy-flagg | `PASS` | `/MIR`, `/PURGE`, `/MOVE` og `/MOV` avvises både i typed switchliste og etter final serialisering/parsing |
| Launch-plan hygiene | `PASS` | Planen bruker absolutt resolversti, sanitert argv-shape, minimal Unicode-env (`SystemRoot`, `WINDIR`, `PATH`, `TEMP`, `TMP`) og `real_robocopy_started=false` |
| Minimal Python/PySide6/BLAKE3/Win32-app | `BLOCKED_BY_ENVIRONMENT` | `PySide6`, `blake3` og `nuitka` mangler i aktiv Python |
| Reproduserbar pakking og ren VM-smoke | `BLOCKED_BY_ENVIRONMENT` | `pyside6-deploy`, `nuitka`, `cl`, `rc`, `signtool` og ren Windows-VM mangler |

0A.6 ble kjørt som ren evidenssyntese uten nye tekniske prober. `docs/adr/0A_DECISION_REVIEW.md` oppsummerer alle ADR-ene med tilgjengelig 0A-bevis, alternativer, risiko/reverseringskostnad, Codex-anbefaling og konkret eierhandling.

### 0A.6 beslutningsport

| Kontroll | Resultat | Bevis |
|---|---|---|
| Alle 0A-bevis har miljø/kommando/artefakt/resultat | `PASS` | Bevismatrise, lokale evidenstabeller og kommandojournal i denne rapporten |
| PASS/BLOCKED/INCONCLUSIVE skilt tydelig | `PASS` | 0A.1, 0A.2 og 0A.5 er delvis/lab-blokkert; 0A.3/0A.4 er lokale pass med SMB-caveats |
| Hver blocker har produktkonsekvens | `PASS` | Blockertabellen og `docs/adr/0A_DECISION_REVIEW.md` |
| Hver ADR har alternativ/risiko/anbefaling/eierhandling | `PASS` | `docs/adr/0A_DECISION_REVIEW.md` |
| Ingen eierbeslutning forfalsket | `PASS` | Alle ADR-er har fortsatt `owner_decision: PENDING` |
| Ingen kontrakt frosset | `PASS` | `schema/contracts-manifest.yaml` forblir draft/blocked |
| 0B åpnet automatisk | `PASS` | Ikke åpnet; status forblir blokkert |

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

git switch -c spike/0a2-endpoint-ownership-local
python -m py_compile spikes\0a2_endpoint_ownership\endpoint_ownership.py tests\spikes\0a2_endpoint_ownership\test_endpoint_ownership.py
python -m unittest discover -s tests\spikes\0a2_endpoint_ownership -v
python -m ruff check spikes\0a2_endpoint_ownership tests\spikes\0a2_endpoint_ownership
python spikes\0a2_endpoint_ownership\endpoint_ownership.py demo --output artifacts\0a2\demo-summary.json
cmd.exe /c "python -m unittest discover -s tests\spikes\0a2_endpoint_ownership -v > artifacts\0a2\unittest-output.txt 2>&1"

git switch -c spike/0a3-recovery-and-paths
python -m py_compile spikes\0a3_recovery_paths\recovery_paths.py tests\spikes\0a3_recovery_paths\test_recovery_paths.py
python spikes\0a3_recovery_paths\recovery_paths.py demo --output artifacts\0a3\demo-summary.json
cmd.exe /c "python -m unittest discover -s tests\spikes\0a3_recovery_paths -v > artifacts\0a3\unittest-output.txt 2>&1"
python -m unittest discover -s tests\spikes\0a1_process_ipc -v
python -m unittest discover -s tests\spikes\0a3_recovery_paths -v
python -m pytest tests\spikes -q
python -m ruff check .

git switch -c spike/0a4-sqlite-capacity
python -m py_compile spikes\0a4_sqlite_capacity\sqlite_capacity.py tests\spikes\0a4_sqlite_capacity\test_sqlite_capacity.py
python -m unittest discover -s tests\spikes\0a4_sqlite_capacity -v
python -m ruff check spikes\0a4_sqlite_capacity tests\spikes\0a4_sqlite_capacity
python spikes\0a4_sqlite_capacity\sqlite_capacity.py benchmark --rows 1000000 --query-repetitions 30 --output artifacts\0a4\benchmark-summary.json
cmd.exe /c "python -m unittest discover -s tests\spikes\0a4_sqlite_capacity -v > artifacts\0a4\unittest-output.txt 2>&1"
python -m pytest tests\spikes -q
python -m ruff check .
git diff --check
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\validate_handoff.py
python tools\build_adr_docs.py --check
python tools\build_master.py --check

git switch -c spike/0a5-windows-argv-and-packaging
python -m py_compile spikes\0a5_windows_packaging\windows_packaging.py tests\spikes\0a5_windows_packaging\test_windows_packaging.py
python -m unittest discover -s tests\spikes\0a5_windows_packaging -v
python -m ruff check spikes\0a5_windows_packaging tests\spikes\0a5_windows_packaging
python spikes\0a5_windows_packaging\windows_packaging.py demo --output artifacts\0a5\demo-summary.json
cmd.exe /c "python -m unittest discover -s tests\spikes\0a5_windows_packaging -v > artifacts\0a5\unittest-output.txt 2>&1"
python -m pytest tests\spikes -q
python -m ruff check .
git diff --check
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\validate_handoff.py
python tools\build_adr_docs.py --check
python tools\build_master.py --check

git switch -c spike/0a6-decision-review
python tools\build_adr_docs.py --check
python tools\build_master.py --check
python -m pytest tests\spikes -q
python -m ruff check .
git diff --check
& "C:\claude\witchery\tmp\mediasync-handoff-venv\Scripts\python.exe" tools\validate_handoff.py
```

Notater:

- `Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All` returnerte at operasjonen krever forhøying.
- Målrettet scan for personlige brukersti-/konto-/epoststrenger fant ingen treff. Den lokale kontomarkøren er redigert bort fra rapporten.
- En bredere sikkerhetsord-scan fant bare forventede fagtermer i spesifikasjonen, som `token`, `credential` og eksempel-UNC.
- `python -m pytest tests\spikes\0a1_process_ipc -q` besto med `5 passed`.
- `python -m ruff check .` besto.
- `python -m mypy --version` og `python -m importlinter --version` feilet fordi modulene ikke er installert i aktiv Python. De er ikke registrert som bestått.
- `python -m pytest tests\spikes -q` besto med `23 passed` etter 0A.4 og `32 passed` etter 0A.5.
- `python -m pytest tests\spikes -q` besto med `39 passed` etter 0A.2/0A.6-review.
- Sikkerhetsord-scan etter 0A.5 fant bare de forventede negative-test-/avvisningsforekomstene av de forbudte Robocopy-flaggene.
- `python -m unittest discover -s tests\spikes` fant ingen tester på grunn av nested discover-layout; de reproduserbare unittest-kommandoene er per spikekatalog.

## Målinger

0A.0 kjørte ingen ytelses- eller kapasitetsmålinger. 0A.4 la til første lokale SQLite-måling med 1,000,000 syntetiske file entries per kandidat:

| Metrikk | Én database | To databaser |
|---|---:|---:|
| Bulk insert | 35.107 s / 28,484 rows/s | 35.466 s / 28,196 rows/s |
| Total DB-størrelse etter recovery seed | 333,422,592 bytes | 333,426,688 bytes |
| Recovery store-størrelse | Inkludert i `state.sqlite` | 286,720 bytes |
| WAL før checkpoint | 48,257,592 bytes | 48,257,592 bytes |
| WAL etter checkpoint | 0 bytes | 0 bytes |
| Parent-page query P95 | 0.101 ms | 0.171 ms |
| Hash lookup P95 | 0.009 ms | 0.027 ms |
| Coverage count P95 | 0.059 ms | 0.087 ms |
| Peak RSS | 104,202,240 bytes | 105,373,696 bytes |

Tallene er lokale spike-målinger på syntetiske metadata, ikke en produksjons-SLA. De viser at begge kandidater håndterer 1M rader godt lokalt, mens to-databasekandidaten kjøper recovery-isolasjon med flere eksplisitte handoff-/backup-tilstander.

## Sikkerhetsavvik og blockers

| ID | Arbeidspakke | Beskrivelse | Konsekvens | Sikker midlertidig handling | Eier |
|---|---|---|---|---|---|
| 0A0-BLK-001 | 0A.2 | Ingen andre Windows-klient/VM og ingen dedikert SMB-lab med `.mediasync_test_root` er tilgjengelig | Cross-machine writer ownership, fremmed owner, takeover og stale epoch kan ikke bestås | Lever lokal harness og marker SMB-radene `BLOCKED` til eier stiller lab | Eier |
| 0A0-BLK-002 | 0A.5 | PySide6, BLAKE3, Nuitka, Windows SDK build/signing tools og ren Windows-VM mangler | Reproduserbar pakking kan ikke bevises i nåværende miljø | Kjør argv/systemsti-delen separat; utsett pakkebevis til toolchain/VM finnes | Eier |
| 0A0-BLK-003 | 0A.2/0A.5 | Hypervisor er present, men `Get-VM` mangler og valgfri Windows-feature-query krever elevation | Codex kan ikke selv inventere eller orkestrere lokal VM-lab | Eier må bekrefte VM-oppsett eller gi eksplisitt labinstruks | Eier |
| 0A1-BLK-001 | 0A.1 | Ekte non-interactive Task Scheduler-session under samme bruker er ikke etablert | 0A.1 kan ikke bevise registrert trigger-client/session-policy fullt ut | Eier må tillate/opprette dedikert `\MediaSyncHome-Spike\<run-id>` task eller gi testcredential/sessionoppsett | Eier |
| 0A1-BLK-002 | 0A.1 | Feil-SID eller remote pipe-klient er ikke tilgjengelig | DACL/local-only-policy er konfigurert, men faktisk avvisning av annen principal er ikke demonstrert | Kjør 0A.1-identitetstesten fra separat Windows-bruker/VM eller remote klient | Eier |
| 0A2-BLK-001 | 0A.2 | To-klient SMB-lab er ikke tilgjengelig | Global writer ownership, fremmed owner fra annen maskin, stale reconnect og SMB-lock kan ikke bestås | Still dedikert SMB-share og to ekte Windows-klienter/VM-er til rådighet | Eier |
| 0A2-BLK-002 | 0A.2 | `blake3` er ikke installert | Endelig `endpoint.json`-checksum etter schemaets `BLAKE3-256` kan ikke bevises; lokal spike bruker `SHA256-0A2-SPIKE` | Installer/lås BLAKE3 før final marker-/schemaevidens | Eier |
| 0A3-BLK-001 | 0A.3 | SMB SourceReadGuard-lab er ikke tilgjengelig | SMB guard kan ikke påstå `DENY_WRITE_AND_DELETE`; uprovede SMB-endepunkter må bruke fallbackpolicy | Kjør samme source-guard-probe mot dedikert SMB-lab eller behold `POST_TRANSFER_HASH_ONLY`/`DEFER_UNSTABLE_SOURCE` for uprovede SMB | Eier |
| 0A5-BLK-001 | 0A.5 | PySide6/BLAKE3/Nuitka/Windows SDK/signing tools og ren Windows-VM er ikke tilgjengelig | Reproduserbar pakket `.exe` og ren-VM-oppstart kan ikke bevises | Installer/lås toolchain og kjør 0A.5-pakkedelen på ren VM før ADR-028 kan anbefales | Eier |

## Beslutninger

ADR-003 er satt til `RECOMMENDED` med Codex-anbefaling om to lokale SQLite-databaser og eksplisitte handoffs. ADR-011, ADR-018 og ADR-027 er satt til `EVIDENCE_COMPLETE`. ADR-028 er satt til `BLOCKED` fordi pakkebeviset mangler toolchain og ren Windows-VM. ADR-020 har lokal klassifiseringsevidens, men forblir `PROPOSED` til eier vurderer BLAKE3-markeravviket. ADR-006, ADR-016 og ADR-019 forblir `PROPOSED` til to-klient SMB-bevis finnes eller eier godkjenner scope-reduksjon. Alle `owner_decision`-felt forblir `PENDING`; bare eier kan akseptere, avvise eller godkjenne scope-reduksjon. ADR-001, ADR-002 og ADR-013 bør forbli `PROPOSED` til de blokkerte identitets-/Task Scheduler-radene er bevist eller eier eksplisitt godkjenner en scope-reduksjon.

`docs/adr/0A_DECISION_REVIEW.md` er eierens beslutningsliste. Den viser også mulige scope-reduksjoner for lokal-only første release, ingen non-interactive trigger i første omgang, zip/dev-run preview uten pakket `.exe`, og utsatt BLAKE3 marker-freeze.

## Anbefalt rekkefølge

1. Fullfør de blokkerte 0A.1-identitetsradene med dedikert Task Scheduler-session og feil-SID/remote klient, eller få eksplisitt eiergodkjent scope-reduksjon.
2. Forbered dedikert to-klient SMB-lab før `0A.2` skal bestå cross-machine writer ownership og før SMB SourceReadGuard kan oppgraderes fra fallback.
3. Forbered PySide6/BLAKE3/Nuitka/Windows SDK/signing tools og en ren Windows-VM før ADR-028 kan få full pakke-evidens.
4. Bruk `docs/adr/0A_DECISION_REVIEW.md` til å fatte eksplisitte ADR-beslutninger eller scope-reduksjoner. 0B skal ikke starte før de nødvendige ADR-ene har eierbeslutning.

## Bevisst ikke implementert

0A.0, 0A.1, 0A.2, 0A.3, 0A.4 og 0A.5 opprettet ikke produktkode under `src/`, endelig produktdatabase, migrasjon, GUI, syncmotor, Robocopy-adapter, Task Scheduler-oppgave eller SMB-lock. 0A.1 opprettet bare spikehost, spikeklient, instrumentert childprosess og midlertidige receipt-/markerfiler under testens tempområde. 0A.2 opprettet bare marker-validerte lokale labrøtter under temp og en spike-local `.mediasync`-fixture inne i dem. 0A.3 opprettet bare marker-validerte lokale labrøtter under temp og muterte filer inne i disse røttene. 0A.4 opprettet bare syntetiske SQLite-kandidatdatabaser i temp og lagret kompakte JSON-/testartefakter. 0A.5 startet bare en instrumentert Python-child for argv-verifikasjon og startet aldri Robocopy eller et pakket produkt. Ingen reelle brukerdata, produksjons-NAS, Bilder-/Dokumenter-/Skrivebord-stier eller diskrot ble brukt som testgrunnlag.
