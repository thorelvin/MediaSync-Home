# Robocopy-adapter og prosessisolasjon


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Sikker executable-resolusjon, argumentserialisering, Job Object, batching, staging og returkodehåndtering.


## 15. Robocopy-adapter og prosessisolasjon

### 15.1 Ansvarsdeling og sikkerhetsgrense

Python/Engine Host eier skanning, plan, leases, stagingmanifest, verifisering, commit, karantene, versjonering, audit og recovery. Robocopy er en **uprivilegert byteoverføringsarbeider** under Engine Hosts prosesssupervisor.

Bindende regler:

- Robocopy får aldri final tree som destinasjon.
- Robocopy får aldri `/MIR`, `/PURGE`, `/MOVE` eller `/MOV`.
- Robocopy avgjør aldri hva som skal kopieres, overskrives eller fjernes.
- `TransferPort` mottar bare en `StagingAllocation`; den får verken final root, `MutationPermit` eller en generell write-capability.
- Bare commitadapteren kan flytte en `VerifiedStagingArtifact` til final path, og bare med levende `MutationPermit` + matching fencing token.
- En Robocopy-returkode eller logglinje er aldri alene bevis på korrekt staging.

### 15.2 Låst executable, Windows-argumenter og prosessoppstart

Prosessadapteren skal:

1. hente den faktiske Windows-systemkatalogen med `GetSystemDirectoryW`; ikke bygge executable path fra en uverifisert `%SystemRoot%`-miljøverdi;
2. appendere `Robocopy.exe`, åpne filen og verifisere resolved final path mot den hentede systemkatalogen før start;
3. lagre executable path, filversjon og hash/Authenticode-evidens i attemptdiagnostikk når praktisk;
4. bruke Win32 `CreateProcessW` eller en testet wrapper med `shell=False`, skjult vindu og uten PATH-/`COMSPEC`-søk;
5. bygge én kanonisk Windows-kommandolinje fra en typed argumentliste. Den serialiserte strengen skal round-trip-testes med en egen test-child som returnerer eksakte `argv`-verdier;
6. validere forbudte flagg både i typed profil **og etter endelig serialisering/parsing**;
7. sette en kontrollert arbeidsmappe under lokal appdata, sikre DLL-søkepolicy og bruke et minimalt, eksplisitt Unicode-miljø;
8. kjøre under samme unelevated bruker-token som Engine Host, uten backup-/restoreprivilegier, credentialdelegasjon eller UAC;
9. bruke `bInheritHandles=FALSE` eller eksplisitt `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`; database-, lease-, pipe-, token-, Job- og recoveryhandles kan aldri arves;
10. opprette child med `CREATE_SUSPENDED`;
11. opprette/konfigurere et ikke-arvbart Job Object med kill-on-close, uten breakaway, og tilordne child før resume;
12. dersom Engine Host selv ligger i et inkompatibelt overordnet Job Object, avvise batchen eller bruke en dokumentert/testet nested-job-policy — aldri kjøre child uten containment;
13. verifisere assignment, prosess-ID, executable og manifestbinding, og først deretter resume primærtråden;
14. ved feil før resume: terminere suspended child, lukke handles og journalføre `CHILD_PROCESS_CONTAINMENT_FAILED`;
15. beholde prosess- og Job Object-handles til attempt er avsluttet, exit status/postcondition er lest og resultatet er journalført;
16. unngå uleste stdout/stderr-pipes; output går til kontrollert fil/`NUL` etter profil, mens egen Unicode batchlogg har størrelses- og retentiongrense.

Round-trip-suiten skal minst dekke mellomrom, tomme argumenter, Unicode, UNC, trailing backslash, anførselstegn, navn som begynner med `/`, lange stier og kommandoer nær den fastsatte grensen. Ingen brukerfilnavn får tolkes som et Robocopy-flagg.

Job Object skal minst ha kill-on-close. Engine Host-krasj eller tvungen upgrade skal derfor ikke etterlate Robocopy som fortsetter uten lease/recoveryeier. Containment er en precondition, ikke best-effort. Dersom child kan kjøre før assignment eller assignment ikke kan bevises, startes ikke batchen.

0B-implementasjonsnote: Prosesssupervisoren har nå en egen Win32 transferchild-adapterflate for `CREATE_SUSPENDED`, kill-on-close Job Object, assignment før resume og suspended-child cleanup på pre-resume feil. Robocopy-wiringen har også en opt-in enkeltfiladapter som bygger typed argv, validerer switcher etter final Windows-parsing, resolver `Robocopy.exe` via Windows systemkatalog/final-path-sjekk, bruker den delte lokale ReparseGuard-grensen fra staging og starter via transferchild-supervisoren. Full batchmanifestbinding, live Robocopy/fault-injection-lab, bred returkodesemantikk og handlebasert ReparseGuard-identitet er fortsatt pending.

Ingen brukerverdi tolkes som flagg. Kilde, staginginbox og eksakte filer kommer fra forseglet plan/manifest og går gjennom `SafePath`/handlebasert rotvalidering.

### 15.3 Kort staginginbox, objektallokering og batchmanifest

Kontrollområdet speiler aldri brukerens fulle relative mappetre. Hver batch får en kort, unik inbox:

```text
.mediasync\installations\<installation-id>\temp\<batch-id>\inbox\
```

Verifiserte payloads flyttes deretter internt til en kort objektsti:

```text
.mediasync\installations\<installation-id>\objects\<shard>\<allocation-id>.payload
.mediasync\installations\<installation-id>\manifests\<allocation-id>.json
```

`Directory manifest batch` grupperer eksakte filer med samme source-parent slik at Robocopy kan kopiere basenames til den korte inboxen uten å gjenskape hele source-treet. Etter prosessavslutning:

1. MediaSync enumererer inboxen mot batchmanifestet;
2. hver fil verifiseres etter policy;
3. hver verifisert fil renames til sin opaque objektallokering på samme målvolum;
4. et checksummet objektmanifest binder allocation-ID til operation, logical role og original/final relativ sti;
5. inboxen blir cleanup-eligible først når alle objekter er journalført.

Før start lagres et immutable `BatchManifest` med:

```text
batch_id
plan_id + plan_checksum
operation_ids i stabil rekkefølge
source_parent_relative_path + source_parent_identity
source_root_identity
target_endpoint_revision_id
owner_installation_id + ownership_epoch
lease_id + local_fencing_token
staging_allocation_ids
short_inbox_relative_path
forventede basenames, typer og størrelser
forventet source fingerprint/guardpolicy per fil
robocopy_profile_hash
canonical_manifest_hash
```

Batchtyper:

1. **Bevist sikkert subtree:** bare når short-path-/filter-/manifestsemantikk er ekvivalent for hele treet og property-tester beviser dette. Ikke standard.
2. **Directory manifest batch:** eksakte navn med samme source-parent og én kort inbox.
3. **Storfilbatch:** én eller få store filer.

Regler:

- ingen brede wildcards uten subtree-bevis;
- konservativ kommandolinjegrense: 24 000 UTF-16 code units etter serialisering;
- normal batchvarighet 2–20 sekunder;
- småfilstart 128–1 024 filer;
- blandet/stor start 256 MiB–2 GiB;
- batchen kan prøves på nytt idempotent i samme inbox bare etter manifest-, state-, owner/epoch- og fencingkontroll;
- lease reacquire eller eierskapsepokebytte ugyldiggjør gammel batch som mutasjonsgrunnlag; objektpayload kan eventuelt re-verifiseres og bindes til en ny allocation/attempt, aldri arve tillatelse automatisk;
- ferdig/verifisert batch kjøres ikke igjen uten eksplisitt invalidasjon;
- inbox, object store og final tree ligger på samme målvolum/share når endpointet tillater safe rename;
- preflight validerer sluttsti, kort inboxsti, object path, manifest path, konfliktnavn og restoresti separat.

Etter prosessavslutning enumererer MediaSync inboxen selv. Faktisk innhold må matche manifestet: ingen manglende, ekstra, reparse- eller typeavvik. Uventet innhold gir `STAGING_MANIFEST_MISMATCH`; ingenting committes.

### 15.4 Robocopy-profil og schedulerparametere

```python
@dataclass(frozen=True, slots=True)
class RobocopyBatchProfile:
    mt_threads: int
    use_restartable_mode: bool
    use_unbuffered_io: bool
    copy_flags: str
    directory_copy_flags: str
    retry_count: int
    retry_wait_seconds: int
    unicode_log_path: str


@dataclass(frozen=True, slots=True)
class SchedulerLimits:
    maximum_concurrent_processes: int
    maximum_active_targets: int
    maximum_files_per_batch: int
    maximum_bytes_per_batch: int
```

Konservativ basisprofil:

```text
/COPY:DAT
/DCOPY:DAT
/R:3
/W:2
/XJ
/BYTES
/FP
/TS
/NP
/NJH
/NJS
/MT:<adaptive>
/UNILOG:<unique-batch-log-path>
```

Hver batch har unik logg:

```text
logs\<run-id>\<target-id>\batch-000001.robocopy.log
```

Parallelle prosesser skriver aldri samme logg. Loggen er diagnostikk; manifest, stagingenumerering, hashes og outcomes er autoritative.

Adaptive regler:

- `/Z` for SMB/ustabil forbindelse eller eksplisitt resumepolicy;
- `/J` bare for store filer når benchmark viser gevinst;
- `/MT` er samtidige filkopier i én prosess, ikke segmentering av én fil;
- én stor fil får normalt `mt_threads=1`;
- prosessantall/aktive mål styres av scheduler;
- `/NFL` og `/NDL` kan brukes når egen audit er tilstrekkelig;
- `/TEE`, `/COPYALL`, `/SEC`, `/SECFIX`, `/B`, `/ZB` og `/FFT` er ikke standard;
- forbudte flagg valideres både ved profilbygging og rett før spawn.

### 15.5 Named streams og metadata

`/COPY:DAT` beviser ikke full filobjektevivalens. Stream-/metadataadapteren følger policyen i §17.8 og må være konsistent med batchprofilen. Når full streambevaring loves, skal adapteren eksplisitt inventere og verifisere streams; ellers vises portabilitetsadvarsel og audit. Robocopylogg alene kan aldri oppgradere resultatet til «fullt verifisert».

### 15.6 Returkoder og attemptresultat

- `0–7`: ingen fatal Robocopy-feil, men resultatflagg klassifiseres.
- `8+`: minst én kopieringsfeil.

```python
@dataclass(frozen=True)
class RobocopyResult:
    exit_code: int
    copied: bool
    extras_reported: bool
    mismatches_reported: bool
    failed: bool
    terminated_by_supervisor: bool
    executable_path: Path
    executable_version: str | None
    arguments_hash: str
    environment_hash: str
    manifest_hash: str
    log_path: Path
```

Selv en ikke-fatal exit code etterfølges av stagingmanifestkontroll og valgt verifisering. En fatal code kan etterlate resumérbar staging, men aldri en final commit.

### 15.7 Fremdrift med lav overhead

- plan gir total nyttelast;
- committede outcomes gir autoritative ferdige byte;
- monitor poller bare aktive prosesser og begrenset staging;
- 1–2 samples/s som standard;
- EWMA og `UiUpdateCoalescer` begrenser IPC/UI-oppdateringer;
- ETA bygger på stabil throughput, gjenstående batcher og endepunktprofil;
- aktiv fil er best-effort;
- loggparser er aldri sikkerhetsgrunnlag.

### 15.8 Pause, stopp og prosesssupervisjon

- **Pause etter aktiv batch:** ikke start nye batcher.
- **Pause nå:** signaliser kontrollert stopp/terminering gjennom supervisor; behold staging.
- **Stopp etter aktiv batch:** fullfør sikker grense og avslutt.
- **Stopp nå:** terminer hele batchens Job Object, journalfør attempt og sett nødvendige operasjoner til recovery/retry.

Resume med `/Z` er bare tillatt når planseal, manifest, source-precondition, endpointlease og stagingpostcondition fortsatt matcher. Ellers fjernes ikke staging blindt; det isoleres og ny batch får nytt attempt.

### 15.9 Kommando-, argument- og loggherding

- rå argumentstreng logges ikke dersom den kan inneholde sensitive share-/brukernavn; lagre sanert visning og hash;
- UNC-credentials eller tokens forekommer aldri i argumenter, miljø eller logg;
- typed argumentliste er kanonisk input; én Windows-serializer produserer endelig command line;
- et round-trip testprogram skal bevise at serialisering gir identisk `argv` for mellomrom, Unicode, UNC, trailing backslashes, quotes, tomme argumenter og navn som begynner med `/`;
- etter serialisering kjøres en uavhengig validator som avviser forbudte switcher, ukjent executable, relative roots og kommando over grensen;
- relative filnavn sendes bare i posisjoner/manifestformer der de ikke kan tolkes som switch; ellers brukes eksplisitt source-parent + sikkert filutvalg;
- loggfil opprettes i kontrollert lokal loggrot, ikke i bruker-/målsti;
- all output behandles som ubetrodd tekst med størrelsesgrense og robust dekoding;
- ingen loggtekst rendres som HTML eller kjøres som kommando;
- attempt får maksimal loggstørrelse og truncationmarkør;
- cleanup av gamle logger følger retensjon og pågår aldri i aktiv runtransaksjon.

### 15.10 Root-attributter og kontrollmapper

Staging kan arve uønskede attributter. Etter transfer normaliserer en eksplisitt Windows-adapter bare tillatte attributter på staging/kontrollobjekter. Målrotens metadata endres ikke som bivirkning av Robocopy.

### 15.11 Gjennomstrømningskrav

På samme maskin/datasett/endepunkter:

- store filer: minst 85 % av direkte Robocopy-baseline i balansert modus;
- blandet/små filer: minst 70 %;
- hash, manifestenumerering og durability-I/O rapporteres separat;
- process spawn/log/monitor skal ikke dominere batchvarighet;
- resultatet rapporterer absolutte tider, prosessantall og sikkerhetskostnad når prosent er misvisende.
