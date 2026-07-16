# Repository, kodekvalitet og versjoner


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Repositorylayout, dependency-regler, CI, sikker utviklingspraksis og foreslåtte verktøyversjoner.


## 10. Repository-struktur

Rotfilene har tydelige roller:

- `README.md` er den ryddige, menneskevennlige GitHub-forsiden og skal aldri være den operative Codex-arbeidsordren;
- `AGENTS.md` er den korte, operative instruksen for gjeldende arbeidspakke;
- `docs/README.md` er navigasjonsindeksen for fagfilene;
- `MASTER_SPEC.md` er en generert konsolidert eksport og skal ikke redigeres direkte.

```text
mediasync-home/
├── AGENTS.md
├── README.md
├── LICENSES.md
├── pyproject.toml
├── requirements.lock
├── ruff.toml
├── mypy.ini
├── pytest.ini
├── importlinter.ini
├── .gitignore
├── .github/
│   └── workflows/
│       ├── ci-windows.yml
│       ├── architecture-windows.yml
│       └── build-windows.yml
├── assets/
│   ├── brand/
│   ├── icons/
│   ├── illustrations/
│   ├── themes/
│   └── translations/
├── MASTER_SPEC.md
├── docs/
│   ├── README.md
│   ├── PRODUCT_REQUIREMENTS.md
│   ├── REQUIREMENTS_INDEX.md
│   ├── ARCHITECTURE.md
│   ├── ENDPOINT_OWNERSHIP.md
│   ├── STORAGE_AND_SCHEMA.md
│   ├── RECOVERY_PROTOCOL.md
│   ├── SYNC_SEMANTICS.md
│   ├── ROBOCOPY_ADAPTER.md
│   ├── GUI_AND_UX.md
│   ├── PERFORMANCE.md
│   ├── OPERATIONS_AND_AUTOMATION.md
│   ├── TEST_PLAN.md
│   ├── MILESTONES.md
│   ├── REPOSITORY_AND_CODE_QUALITY.md
│   ├── REFERENCES.md
│   ├── ARCHITECTURE_SPIKE_REPORT.md
│   ├── DECISION_REGISTER.md
│   ├── IMPLEMENTATION_STATUS.md
│   ├── REQUIREMENTS_TRACEABILITY.md
│   ├── BENCHMARKS.md
│   ├── USER_GUIDE.md
│   └── adr/
│       ├── README.md
│       ├── 0000-template.md
│       ├── 0001-engine-host.md
│       ├── 0002-local-ipc.md
│       ├── 0019-endpoint-exclusive-writer.md
│       ├── 0020-control-area-classification.md
│       └── ...
├── schema/
│   ├── README.md
│   ├── contracts-manifest.yaml
│   ├── catalog.sql
│   ├── recovery.sql
│   ├── ipc-command.schema.json
│   ├── ipc-event.schema.json
│   ├── endpoint-marker.schema.json
│   ├── intent-segment.schema.json
│   ├── reason-codes.yaml
│   └── state-machines.yaml
├── src/
│   └── mediasync_home/
│       ├── __init__.py
│       ├── __main__.py
│       ├── bootstrap.py                 # kun rollevelger; ingen forretningslogikk
│       ├── processes/
│       │   ├── launcher_main.py
│       │   ├── engine_host_main.py
│       │   ├── trigger_client_main.py
│       │   └── ui_main.py
│       ├── ipc/
│       │   ├── protocol.py
│       │   ├── schemas.py
│       │   ├── framing.py
│       │   ├── pipe_security.py
│       │   ├── client_identity.py
│       │   ├── limits.py
│       │   ├── server.py
│       │   ├── client.py
│       │   └── errors.py
│       ├── domain/
│       │   ├── models/
│       │   ├── value_objects/
│       │   ├── policies/
│       │   ├── state_machines/
│       │   ├── events.py
│       │   └── errors.py
│       ├── application/
│       │   ├── commands/
│       │   ├── queries/
│       │   ├── handlers/
│       │   ├── ports/
│       │   │   ├── catalog.py
│       │   │   ├── recovery.py
│       │   │   ├── handoffs.py
│       │   │   ├── snapshots.py
│       │   │   ├── plans.py
│       │   │   ├── root_claims.py
│       │   │   ├── retention.py
│       │   │   ├── endpoint_read.py
│       │   │   ├── source_read_guard.py
│       │   │   ├── path_resolution.py
│       │   │   ├── control_area_classifier.py
│       │   │   ├── endpoint_ownership.py
│       │   │   ├── leases.py
│       │   │   ├── control_area.py
│       │   │   ├── managed_objects.py
│       │   │   ├── staging.py
│       │   │   ├── transfer.py
│       │   │   ├── verification.py
│       │   │   ├── commit.py
│       │   │   ├── quarantine.py
│       │   │   ├── versions.py
│       │   │   ├── intent_segments.py
│       │   │   ├── processes.py
│       │   │   ├── scheduling.py
│       │   │   ├── notifications.py
│       │   │   ├── clock.py
│       │   │   └── ids.py
│       │   ├── services/
│       │   │   ├── job_service.py
│       │   │   ├── analysis_service.py
│       │   │   ├── recovery_service.py
│       │   │   ├── handoff_service.py
│       │   │   ├── automation_service.py
│       │   │   └── maintenance_service.py
│       │   └── dto.py
│       ├── engine/
│       │   ├── host.py
│       │   ├── command_dispatcher.py
│       │   ├── query_dispatcher.py
│       │   ├── run_coordinator.py
│       │   ├── run_target_executor.py
│       │   ├── scanner/
│       │   ├── planner/
│       │   ├── hashing/
│       │   ├── execution/
│       │   │   ├── batch_builder.py
│       │   │   ├── verification.py
│       │   │   ├── commit_protocol.py
│       │   │   └── quarantine.py
│       │   ├── scheduler/
│       │   ├── recovery/
│       │   ├── handoffs/
│       │   ├── leases/
│       │   ├── outbox/
│       │   └── progress/
│       ├── adapters/
│       │   ├── sqlite/
│       │   │   ├── catalog_connection.py
│       │   │   ├── recovery_connection.py
│       │   │   ├── catalog_writer.py
│       │   │   ├── recovery_writer.py
│       │   │   ├── repositories/
│       │   │   ├── query_plans.py
│       │   │   ├── backup_sets.py
│       │   │   ├── handoffs.py
│       │   │   ├── seals.py
│       │   │   ├── root_claims.py
│       │   │   ├── retention.py
│       │   │   ├── compaction_epoch.py
│       │   │   ├── migration_epoch.py
│       │   │   └── migrations/
│       │   │       ├── catalog/
│       │   │       └── recovery/
│       │   ├── windows/
│       │   │   ├── endpoint_probe.py
│       │   │   ├── endpoint_identity.py
│       │   │   ├── endpoint_ownership.py
│       │   │   ├── control_area_classifier.py
│       │   │   ├── safe_path.py
│       │   │   ├── source_read_guard.py
│       │   │   ├── reparse_guard.py
│       │   │   ├── final_path.py
│       │   │   ├── file_flush.py
│       │   │   ├── replace_file.py
│       │   │   ├── endpoint_lease.py
│       │   │   ├── mutation_permit.py
│       │   │   ├── named_mutex.py
│       │   │   ├── process_supervisor.py
│       │   │   ├── job_object.py
│       │   │   ├── dll_policy.py
│       │   │   ├── task_scheduler.py
│       │   │   ├── volume_events.py
│       │   │   ├── credential_manager.py
│       │   │   └── notifications.py
│       │   ├── robocopy/
│       │   │   ├── executable.py
│       │   │   ├── command_builder.py
│       │   │   ├── manifest.py
│       │   │   ├── process_adapter.py
│       │   │   ├── progress_monitor.py
│       │   │   ├── exit_codes.py
│       │   │   └── log_parser.py
│       │   ├── filesystem/
│       │   │   ├── scanner.py
│       │   │   ├── metadata.py
│       │   │   └── intent_segments.py
│       │   ├── hashing/
│       │   │   └── blake3_hasher.py
│       │   └── logging/
│       │       └── structured_logging.py
│       └── presentation/
│           ├── app.py
│           ├── main_window.py
│           ├── engine_client.py
│           ├── navigation/
│           ├── theme/
│           ├── components/
│           ├── pages/
│           ├── dialogs/
│           ├── models/
│           ├── delegates/
│           ├── controllers/
│           ├── view_models/
│           └── resources.py
├── tests/
│   ├── architecture/
│   │   ├── test_contract_precedence.py
│   │   ├── test_import_boundaries.py
│   │   ├── test_no_gui_mutation_adapters.py
│   │   ├── test_no_generic_write_port.py
│   │   ├── test_single_state_owner.py
│   │   ├── test_no_cross_store_transaction.py
│   │   ├── test_relative_persisted_paths.py
│   │   ├── test_sealed_state_immutability.py
│   │   ├── test_fencing_token_propagation.py
│   │   ├── test_child_process_containment.py
│   │   ├── test_endpoint_exclusive_writer.py
│   │   ├── test_composite_foreign_keys.py
│   │   ├── test_short_control_object_paths.py
│   │   └── test_no_domain_side_effects.py
│   ├── unit/
│   ├── integration/
│   │   ├── ipc/
│   │   ├── sqlite/
│   │   ├── windows/
│   │   ├── smb/
│   │   └── robocopy/
│   ├── e2e/
│   ├── safety/
│   ├── recovery/
│   ├── performance/
│   ├── fixtures/
│   └── helpers/
├── scripts/
│   ├── build.ps1
│   ├── test.ps1
│   ├── architecture.ps1
│   ├── package.ps1
│   ├── benchmark.ps1
│   ├── fault_matrix.ps1
│   └── create_test_tree.py
└── installer/
    ├── mediasync-home.iss
    └── assets/
```

Regler for repositoryet:

- `processes/*` er tynne composition roots og skal ikke inneholde domeneavgjørelser;
- `engine` kan bruke application-porter, men skal ikke importeres av `domain`;
- konkrete adapters registreres bare i Engine Host-composition root;
- `presentation/engine_client.py` er GUI-ets eneste inngang til muterende use cases;
- det finnes ingen generell `filesystem.py` med write-metoder i application-porter; read, staging og final commit er fysisk separate kontrakter;
- `mutation_permit.py` har private constructors og kan bare utstedes av den adapteren som eier aktivt lease-handle og fencing token;
- SQL ligger i repository/migration-lag, ikke spredt i GUI eller domain;
- alle Win32-kall har én adapter med eksplisitt feiloversettelse og fake i tester;
- arkitekturtestene er en obligatorisk CI-gate, ikke bare dokumentasjon;
- `AGENTS.md` er operativ inngang og peker bare til dokumenter relevant for aktuell milepæl;
- `schema/` er versjonert og valideres mot migrasjoner, Pydantic-/dataklasser, reason-code enums og dokumentasjon;
- konsolidert masterdokument er en generert/versjonert eksport og skal ikke være eneste sannhetskilde for eksakte kontrakter.

---

## 23. Kodekvalitet og utviklingsregler

### 23.1 Python og lokal korrekthet

- Python 3.14, standard CPython-build.
- Full typeannotering på offentlig kode og `mypy --strict` gradvis aktivert per pakke.
- `dataclass(frozen=True, slots=True)` for immutable value objects der hensiktsmessig.
- `Enum`/`StrEnum` for statuser, protokollverdier og årsakskoder.
- `Protocol`/porter for alle OS-, database-, prosess- og tidsavhengigheter.
- Ingen mutable default arguments eller skjult global mutable state.
- Ingen brede `except Exception` uten klassifisering, korrelasjon og eksplisitt re-raise/resultat.
- Ingen blocking I/O, stor SQL, filmetadata eller stor JSON-dekoding i GUI-callbacks.
- Bruk `pathlib` i display/konfigurasjon; kompakte records/strenger i hot paths og én sentral Win32 path-adapter.
- Ingen uavgrenset `Queue`, liste, cache eller eventbuffer i produksjonsflyt.
- Ingen `assert` for runtime-sikkerhetskontroll; bruk eksplisitte guards og domeneerrors.
- Ingen tid-/random-/UUID-kall direkte i domain; injiser `Clock`/`IdGenerator` når determinisme kreves.
- PR-er som endrer bindende oppførsel oppgir krav-ID-er, ADR og bevisende tester.

### 23.2 Arkitekturgrenser

Følgende er bindende og håndheves med `import-linter`/AST-tester:

```text
presentation -> application -> domain
process entrypoints -> composition roots -> application/adapters
adapters -> application ports/domain types
```

Forbud:

- domain importerer Qt, sqlite3, subprocess, pywin32, watchdog eller konkrete adapters;
- application importerer presentation eller konkrete adapterimplementasjoner;
- presentation importerer writable repositories, Robocopy, Win32 mutation, lease eller recovery;
- launcher/trigger client importerer sync-/planner-/commitmotor;
- konkrete adapters konstrueres utenfor Engine Host-composition root;
- global service locator, ambient database connection eller skjult singleton brukes som dependency injection;
- GUI leser databasefilene «for ytelse» uten Engine Host-queryport;
- en intern CLI blir en alternativ muterende kodebane;
- catalog- og recoveryrepository åpnes writable i samme komponent/transaksjon uten typed handoff;
- plan-, IPC- eller recoverykode tar en lagret absolutt brukerfilsti som autoritativ mutasjonsadresse;
- `pickle`, `eval`, `exec`, dynamiske imports fra IPC/payload eller uversjonert serialisering brukes.

Nye cross-layer-unntak krever ADR, tydelig begrunnelse og arkitekturtest som begrenser omfanget.

### 23.3 Concurrency, transaksjoner og cancellation

- Én Engine Host eier mutable state; intern concurrency koordineres gjennom eksplisitte actors/coordinators.
- Commands har én serialisert state transition per aggregate/resource scope og en global idempotency key med verifisert principal/schema/payloadhash.
- Command inbox-overganger er monotone; terminal receipt kan aldri gå tilbake til en ikke-terminal state, og permanent deduptombstone opprettes atomisk før detaljkomprimering.
- SQLite-transaksjoner er korte og omfatter aldri fil-, nettverks-, IPC-, lease-, process- eller schedulerventing.
- Samme use case holder aldri catalog- og recovery-write transactions samtidig; writable `ATTACH DATABASE` er forbudt, og kryss-store arbeid går via persisted handoff/saga og startup reconciliation.
- I/O utføres utenfor DB-transaksjon; resultatet committes med expected-state/CAS.
- Cancellation er hierarkisk og har sikre stoppunkter; ingen løs `threading.Event` uten eier/livssyklus.
- Alle tasks, threads, subprocesses, handles og timers har eksplisitt eier og cleanup.
- Ingen fire-and-forget task uten durable outbox/work item eller supervised task group.
- Resource acquisition bruker canonical ordering og `finally`/context manager for release.
- Progress/events kan coalesces; commands, recovery events og audit kan ikke droppes.
- Monotonic clock brukes for deadlines/backoff; UTC-veggklokke brukes for audit/display.
- Retry eies av ett nivå og har budsjett; nested ukjent retry er forbudt.
- Actor-/queue-meldinger har message ID, expected state, relevant fencing token og eksplisitt overflow-/shutdownpolicy; «send and hope» er forbudt.
- Lease loss/reacquire invalidiserer alle permits og ventende workerresultater fra eldre token før nye mutasjoner kan starte.

### 23.4 Database og migrasjoner

- Rå SQL samles i repository-/migrationlag; domain får typed records/resultater.
- Foreign keys, `STRICT`-tabeller, `CHECK`-constraints og unike idempotencynøkler brukes der SQLite støtter det.
- Alle queries har eksplisitt kolonneliste og deterministisk `ORDER BY` når rekkefølge betyr noe.
- Ingen `SELECT *` i schema-/protokollkritisk kode.
- Bulkoperasjoner er batchbaserte og idempotente; `(snapshot_id, sequence_no)` gjenbruk med annen payloadhash er hard konflikt.
- Immutable tabeller og forseglede snapshots/planer beskyttes med repositoryregler, constraints/triggere der hensiktsmessig og tester; historiske rader overskrives ikke.
- Aktive root claims materialiseres i én serialiserbar katalogtransaksjon; historiske claims brukes til audit, ikke som skjult lås.
- Baseline lagres i immutable baseline sets med eksplisitt context hash/generation.
- Retention er cross-store root-export + mark/sweep over eksplisitte roots/holds, journalført med immutable delete manifest, `retention_pending`, high-water-revalidering og små idempotente batcher.
- Compaction/`VACUUM INTO` kjøres som egen checksummet epoch under quiesce med verifisert output, lukkede handles, rollbackfil og restartbar swap; aldri som skjult del av normal write path.
- Intern backup/restore behandler alle autoritative state stores som ett checksummet backupsett; blandede filer/epoker og restore forbi nyere target-intents er forbudt.
- Command-, trigger- og outboxdetaljer kan bare kompakteres etter atomisk opprettelse av permanent dedup-tombstone.
- Migrations er monotone, checksummede og testet fra alle støttede tidligere versjoner.
- Engine Host tar singleton/migrationlease før schemaendring og bruker migration epoch-manifest med backup/high-water per database.
- Dersom ADR-003 velger flere databaser, bruker en migrasjon som berører flere stores separate transaksjoner og deterministisk resume/restore; cross-database atomisitet skal aldri simuleres.
- Writable databaser skal ligge lokalt i ACL-beskyttet appdataområde; NAS/SMB/flyttbart medium er unsupported og blokkeres.
- Extension loading er deaktivert, `trusted_schema=OFF` og defensive/query-only-innstillinger brukes der runtime støtter dem.
- Ingen automatisk «reset database» ved corruption/recoveryavvik.
- Query plans for milliontabeller testes; ny full scan i hot path krever måling og dokumentasjon.

### 23.5 Prosess-, IPC- og filsystemherding

- Launcher, GUI, Engine Host, trigger client og transferchild kjører unelevated med normalt brukertoken; backup-/restore-privilegier eller opportunistisk UAC er forbudt.
- Alle prosesser startes gjennom én `ProcessSupervisor`-adapter med absolutt executable path, sikkert working directory/DLL-søk, minimalt Unicode-miljø, eksplisitt handleliste og ingen utilsiktet inheritance.
- Transferchild opprettes suspended, tildeles et ikke-arvbart no-breakaway/kill-on-close Job Object og gjenopptas først etter verifisert containment; feil terminerer child.
- Robocopy-spawn uten Job Object/supervisor er forbudt.
- IPC-parser er separat, ren og fuzzbar; framing, samtidige klienter, requests, subscriptions og eventrate har faste grenser.
- Named pipe er local-only der støttet og autoriserer etter faktisk klienttoken/SID/session, aldri selvrapportert rolle.
- IPC-kommandoer refererer persistente ID-er, ikke vilkårlige filstier eller kommandolinjer.
- Persistente brukerfilstier er relative til en eksakt endpointrevision; absolutte stier kan bare eksistere som kortlivede, revaliderte adapterverdier.
- Path safety bruker handles/final identity for mutasjon; string-prefix alene er forbudt.
- Alle muterende Win32-kall finnes i små adapters med dokumenterte preconditions/postconditions.
- Application/domain har ingen generell write-capable filsystemport. Finalmutasjon krever opaque `MutationPermit`, matching fencing token og en verifisert stagingartefakt.
- `TransferEngine` kan bare adressere `StagingAllocation`; den mottar aldri final root eller permit.
- OS-returkode blir alltid tolket sammen med faktisk observert postcondition.
- Lockfilens eksistens, heartbeat eller PID alene behandles aldri som lease.
- Staging, versions, quarantine og intentsegmenter opprettes bare under validert kontrollområde.
- Intentsegmenter er immutable, canonical/hashkjedede og bounded etter både operasjonsantall og byte; én fil per brukeroperasjon er forbudt.

### 23.6 Avhengigheter og supply chain

Foreslåtte runtime-avhengigheter:

- PySide6;
- pywin32;
- psutil;
- watchdog;
- blake3;
- platformdirs;
- et kuratert SVG-ikonsett med permissiv lisens eller egne ikoner;
- Pydantic eller validerte dataclasses bare dersom verdien er tydelig og hot paths ikke påvirkes.

Utviklingsavhengigheter:

- pytest, pytest-qt, pytest-cov;
- hypothesis;
- ruff, mypy, import-linter;
- Nuitka/`pyside6-deploy`;
- pyperf eller tilsvarende stabilt benchmarkverktøy;
- dependency-/lisens-/sårbarhetsskann med dokumentert offlinevennlig workflow.

Regler:

- lås eksakte versjoner og integritetshasher i en reproduserbar lockfil;
- direkte og transitive lisenser/notices dokumenteres;
- dependencyoppgradering er egen PR med tester/benchmark for berørte områder;
- ingen runtime-download av kode, modeller, plugins eller binaries;
- plugins lastes ikke dynamisk fra bruker-/målmapper;
- pakket build inkluderer software bill of materials/dependency manifest;
- standardbibliotek foretrekkes når det reduserer angrepsflate uten å øke korrekthetsrisiko.

### 23.7 Testbarhet og porter

Minimumsporter:

```python
class CatalogStore(Protocol): ...
class RecoveryStore(Protocol): ...
class CrossStoreHandoffStore(Protocol): ...
class IntentSegmentStore(Protocol): ...
class SnapshotSealStore(Protocol): ...
class RootClaimStore(Protocol): ...
class RetentionGraphStore(Protocol): ...
class BackupSetStore(Protocol): ...
class FileTreeReader(Protocol): ...
class EndpointReadGateway(Protocol): ...
class EndpointResolver(Protocol): ...
class EndpointLeaseProvider(Protocol): ...
class ControlAreaGateway(Protocol): ...
class StagingAreaGateway(Protocol): ...
class VerificationGateway(Protocol): ...
class CommitGateway(Protocol): ...
class QuarantineGateway(Protocol): ...
class VersionStoreGateway(Protocol): ...
class ProcessSupervisor(Protocol): ...
class TransferEngine(Protocol): ...
class TaskSchedulerGateway(Protocol): ...
class NotificationGateway(Protocol): ...
class Clock(Protocol): ...
class IdGenerator(Protocol): ...
class Hasher(Protocol): ...
```

Fakes skal modellere feil, cancellation, partial success og CAS/lease semantics — ikke bare happy path. Integrasjonstester bruker ekte adapters for at fake ikke skjuler Windows-spesifikke feil.

### 23.8 Logging, sikkerhet og personvern

- Domain/application returnerer strukturerte resultater og feil; presentation oversetter dem.
- Teknisk logg har korrelasjons-ID, event code og sanert kontekst.
- Ingen raw stack trace er eneste brukerinformasjon.
- Credentials, tokens, full UNC-brukerinfo og sensitiv filmetadata maskeres etter policy.
- Rå IPC-payload, principal/tokenmateriale eller full kommandolinje logges ikke ukritisk.
- Absolutte brukerfilstier logges bare etter eksplisitt redactionpolicy; audit foretrekker endpoint-ID + relativ sti/hash.
- Loggtekst behandles som ubetrodd data og rendres ikke som HTML/kommando.
- Audit er append-orientert; debuglogg er roterbar og ikke autoritativ.
- Ingen telemetri sendes ut uten et senere eksplisitt, opt-in krav.

### 23.9 Commit-, PR- og ADR-praksis

Hold commits små og vertikale. Eksempler:

```text
chore: bootstrap process entrypoints and architecture tests
feat(ipc): add local-only authenticated command inbox and receipts
feat(db): add immutable revisions, handoffs and migration epochs
feat(paths): add endpoint identity, leases and reparse guard
feat(scan): stream coverage-aware snapshots into catalog
feat(plan): seal deterministic one-way plans with preconditions
feat(copy): contain suspended robocopy in job object before resume
feat(recovery): add bounded intent segments and cross-store handoffs
feat(replace): preserve old target through compare-and-swap replace
feat(auto): reconcile task scheduler desired state through trigger client
feat(ui): add engine-backed analysis and run progress pages
```

PR-beskrivelsen inneholder:

- berørte krav-ID-er og ADR-er;
- invarianten som endres;
- failure modes og rollback;
- test-/fault-injection-bevis;
- målinger for hot paths;
- schema-/protocolcompatibility;
- eksplisitt bekreftelse på at ingen alternativ muterende kodebane ble introdusert.

---

## 24. Foreslåtte versjoner ved prosjektstart

Ved revisjonsdatoen for denne planen:

- Python 3.14.6 er den valgte stabile vedlikeholdsutgaven.
- PySide6 6.11.1 er foreslått startversjon; Python 3.14-støtte kom i PySide6 6.10.1.
- Bruk ikke Python 3.15-beta i produksjonsbygget.
- `pyside6-deploy` kan brukes til å produsere en Windows-`.exe` via Nuitka.

Codex skal fortsatt kjøre en faktisk kompatibilitetstest og låse versjonene i prosjektet. Dersom en avhengighet ikke fungerer på Python 3.14, skal Python 3.13 brukes midlertidig og avviket dokumenteres, fremfor å bruke en førutgave.

---
