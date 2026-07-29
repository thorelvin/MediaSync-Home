# Automatisering og drift


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Task Scheduler, triggers, observabilitet, retry, recoverymodus og operasjonell feilbehandling.


## 18. Automatisering uten Windows-tjeneste

### 18.1 Arkitektur: trigger client, ikke separat syncmotor

Windows Oppgaveplanlegging skal aldri starte en ny uavhengig syncmotor som skriver databaser og filer parallelt med GUI-et. Task Scheduler starter den interne **trigger client**-rollen:

```text
MediaSyncHome.exe --internal-trigger <schedule-id> <schedule-revision-hash>
```

Argumentene er interne, strengt validerte ID/hashverdier; de kan ikke inneholde vilkårlig kommando eller filsti. En statisk taskdefinisjon skal ikke late som den kan inneholde en unik forekomst-ID for hver fremtidig start. Trigger client:

1. validerer at `schedule-revision-hash` matcher registrert desired task eller rapporterer drift;
2. samler en strukturert `TriggerDeliveryContext`: ny `delivery_id`, observert starttid, triggerklasse, boot-/logon-/event-/task-instance-identitet der Windows eksponerer dette, og taskdefinisjonshash;
3. bruker samme `HostLocator`/launcher-protokoll som GUI-et for å finne eller starte kompatibel Engine Host; den dupliserer ikke singleton-, upgrade- eller spawnlogikk;
4. utfører IPC-handshake;
5. sender `EnqueueTriggerOccurrence` med stabil command-idempotency key for leveringen og hele den validerte triggerkonteksten;
6. venter på varig `ACCEPTED`, `DUPLICATE` eller eksplisitt feil;
7. avslutter.

Engine Host eier `OccurrenceKeyFactory` og avleder den logiske forekomsten fra schedule-ID/revisjon og triggersemantikk:

- fast kalendertrigger: canonical planlagt tidsluke, ikke tilfeldig prosessstarttid;
- logon/startup: dokumentert logon-/bootidentitet når tilgjengelig, ellers en konservativ coalescingnøkkel for samme schedule/revisjon og vindu;
- eventtrigger: channel/provider/event record identity når tilgjengelig;
- agentgenerert disk-/watchertrigger: agentens persistente event-ID;
- manuell start: egen command-idempotency, ikke automatisk sammenslåing med planlagt forekomst.

Mange `delivery_id`-er kan dermed peke til samme `deduplication_key` og samme run. Ulik schedule-revisjon eller payload med samme logiske nøkkel er konflikt, ikke en ny skjult kjøring.

Host-bootstrap i planlagt kontekst er bindende:

- triggerrollen bruker samme cross-session singleton/HostLocator som launcher og GUI;
- dersom kompatibel host finnes, leverer triggerprosessen kommandoen og avslutter etter varig `ACCEPTED`/`DUPLICATE`;
- dersom ingen host finnes, skal bootstrap enten bli Engine Host i samme Task Scheduler-eide prosess eller fullføre en eksplisitt ownership/lifetime-handshake med en ny host før bootstrap kan avslutte; fire-and-forget child-spawn er forbudt;
- en bootstrap som blir host, persisterer startforekomsten gjennom vanlig command inbox/handoff før den rapporterer readiness;
- Task Scheduler-definisjonen skal tillate parallelle korte triggerinstanser slik at senere forekomster kan koble til en allerede kjørende task-eid host; deduplisering og køgrenser ligger fortsatt i Engine Host;
- taskens execution-time-/stop-policy skal ikke blindt drepe en aktiv host. Ekstern avslutning behandles som hosttap med Job Object-stopp og recovery, men ønsket konfigurasjon skal prioritere kontrollert shutdown;
- Task Scheduler-status er transportstatus, ikke autoritativt backupresultat. Run/outcome leses alltid fra Engine Host.
- Kalendertriggere lagrer eksplisitt Windows-tidssone-ID, DST-policy, misfire-policy og coalescingvindu. Forekomstnøkkelen inkluderer den normaliserte planlagte slotten med offset/fold-semantikk, slik at klokkejustering eller sommertid ikke stille skaper dobbel eller manglende logisk kjøring.

### 18.2 Windows Oppgaveplanlegging og sikkerhetskontekst

Bruk Task Scheduler 2.0 via COM/pywin32 under en installasjonsspesifikk mappe:

```text
\MediaSync Home\<installation-id>\
```

Støtt tid, dag/uke/måned, pålogging, lokal systemoppstart, inaktivitet og dokumenterte event-triggere.

| Jobb/endepunkt | Standard logontype | Merknad |
|---|---|---|
| Lokal disk → lokal disk | `INTERACTIVE_TOKEN` eller eksplisitt lokal bakgrunnskontekst | Ingen nettverkskrav |
| Lokal disk → USB | `INTERACTIVE_TOKEN` som standard | Volumhendelse krever kjørende agent/host eller task-trigger |
| Lokal disk → NAS/UNC | `INTERACTIVE_TOKEN` | Brukeren må normalt være logget inn |
| NAS-jobb når bruker er logget av | `PASSWORD`, bare etter eksplisitt avansert oppsett | Windows forvalter credential; MediaSync lagrer den ikke |
| `S4U` | Forbudt når jobb krever nettverk eller krypterte filer | Mangler nødvendig nettverkstilgang |
| Mapped drive | Konverter til UNC før lagring | Mapping kan mangle i bakgrunnskontekst |

Regler:

- Programmet lagrer ikke Windows-kontopassord, token eller reversible secrets i database/logg.
- Taskargumentet inneholder bare installasjons-/schedule-ID, forventet schedule-/taskdefinisjonshash og protokollmarkør; forekomst-ID beregnes aldri som en statisk taskparameter.
- `MultipleInstancesPolicy` settes eksplisitt slik at en task-eid host ikke gjør at senere forekomster ignoreres; Engine Host håndterer dedup/coalescing.
- Execution time limit og stop-policy er eksplisitte i desired-state-hash og skal ikke bruke en kort vilkårlig killgrense.
- «Kjør selv om brukeren ikke er logget inn» er et eksplisitt avansert valg med Windows-credential-flyt; lokal preview avviser dette inntil credential-flyten finnes.
- GUI viser logontype, nettverkstilgang og begrensning før ønsket task lagres.
- Same-user Task Scheduler-policyen aksepterer foreløpig bare `INTERACTIVE_TOKEN` med `run_only_when_logged_on=true`; `PASSWORD`, `S4U` og logged-off run markeres som unsupported desired state.
- Taskdefinisjonen er **desired state** i `schedules`; en reconciler sammenligner faktisk Task Scheduler-tilstand med `desired_definition_hash`.
- Oppretting, oppdatering og sletting er idempotent og kan gjentas etter krasj.
- Foreldreløse tasks identifiseres etter installasjons-ID og bruker-SID; de slettes ikke blindt hvis eierskap er tvetydig.
- Orphan-sweepen lister bare installasjonens Task Scheduler-mappe og sletter først når task path, protokoll-argv, installasjons-ID og binær matcher, mens manglende schedule gjør tasken eid og foreldreløs.
- En task som peker til annen binær eller ukjent argumentstruktur markeres som drift og krever eksplisitt reparasjon.

### 18.3 Triggerdeduplisering, claims og kø

`trigger_occurrences` er autoritativ deduplisering:

- en installasjonsglobal `deduplication_key` beregnes kanonisk av `OccurrenceKeyFactory` fra schedule-ID, schedule-revisjon og normalisert triggerkontekst; `delivery_id` brukes bare til audit/transport;
- identisk retry med samme payloadhash returnerer eksisterende status/run;
- samme nøkkel med annen payload, schedule-revisjon eller jobb avvises som idempotencykonflikt;
- flere ulike triggere for samme jobb kan coalesces etter policy, men hver forekomst beholder audit og peker til samme run når de bevisst flettes;
- terminale detaljer kan kompakteres, men deduplication key, payloadhash og terminal effect hash beholdes i `effect_dedup_tombstones`;
- manuell start kan prioriteres, men kan ikke omgå aktiv run-/endpointlease;
- en trigger som kommer under recovery blir `WAITING_FOR_RECOVERY`, ikke en ny konkurrerende run.

Claims i en levende Engine Host bruker:

```text
owner_instance_id
claim_generation
claim_token
monotonic_started
monotonic_deadline
```

Bare ownerinstansen kan sammenligne den monotone deadlinen. Persistente `claim_started_utc`/`claim_ttl_ms` er diagnostikk og grunnlag for konservativ startup-reconciliation, ikke direkte bevis på at en claim er utløpt. Etter prosess- eller Windows-restart behandles alle claims fra en annen `owner_instance_id` gjennom idempotent avstemming og ny CAS-claim; veggklokkeendring skal aldri skape tett loop eller stjele levende arbeid.

### 18.4 Disktilkobling

Engine Host eller en lett tray-agent som bare sender events overvåker volumhendelser:

1. oppdag volum;
2. les volumidentitet og markør uten mutasjon;
3. finn ventende schedule/job;
4. debounce samlet, standard 10 sekunder;
5. send idempotent triggerforekomst;
6. Engine Host tester lease, tilgang, kapabilitet og fri plass før analyse/run.

En tray-agent kan aldri starte Robocopy eller skrive database direkte. Disktilkobling fungerer bare mens Engine Host/agent eller en relevant Task Scheduler-trigger kjører.

### 18.5 Filendring

Bruk `ReadDirectoryChangesW` gjennom en adapter eller `watchdog` for lokale kilder:

- debounce/quiet window, standard 60 sekunder;
- mange hendelser flettes til én triggerforekomst;
- overflow, watcherrestart eller usikker sekvens utløser full skann;
- SMB bruker polling/fullskann som fallback;
- watcher-event er et hint, aldri et snapshot eller fraværsbevis;
- watcherstate påvirker ikke destruktive tillatelser;
- watcher leverer trigger til Engine Host gjennom samme idempotente kommandoflyt.

### 18.6 Automatisk utførelsespolicy

Per jobb velges én policy:

| Policy | Standardoppførsel |
|---|---|
| **Bare nye filer – anbefalt** | Kopier nye filer og opprett mapper. Erstatning, konflikt, karantene eller blokkering blir `Trenger kontroll`. |
| **Nye og endrede filer med versjoner** | Tillat verifiserte compare-and-swap-erstatninger med versions og beståtte terskler. Krever eksplisitt opt-in og pålitelig endpointlock. |
| **Kontroller bare** | Utfør analyse, lagre resultat og varsle; ingen run for filkopi. |

Policyen inngår i jobbrevisjon og planchecksum. `DEFER_AUTOMATION_POLICY` er en eksplisitt planrad. Automatikk kan aldri:

- omgå planreview som policyen krever;
- utføre mirror/quarantine eller toveiskonflikt ubemerket i første komplette versjon;
- gjenbruke plan etter incompatible endpoint/job revision, eierskapsepokebytte eller casekontekstendring;
- utføre operation der source-/targetprecondition avviker;
- overta et mål med aktiv lease eller fremmed writer-eier;
- bruke degradert no-lock-modus uten eksplisitt jobbpolicy.

For et endepunkt uten bevist lock gjelder:

- standard er read-only analyse;
- `COPY_NEW_ONLY_NO_REPLACE` kan bare aktiveres manuelt etter tydelig forklaring og dokumentert no-overwrite-kapabilitet;
- automatisk kjøring er av som standard og krever separat opt-in;
- hver fil revalideres som `ABSENT` i samme no-overwrite commitkall;
- replace, metadataendring, mirror, retention, restore og two-way forblir forbudt.

Automatisk run med trygge kopier og utsatte handlinger vises som `Fullført – handling nødvendig`, ikke som full suksess.

### 18.7 Varsler gjennom transactional outbox

Windows-varsler skrives først som `outbox_messages` i samme katalogtransaksjon som tilstanden som skal varsles om. En dispatcher leverer minst én gang med idempotency key.

Varsler brukes for:

- fullført kjøring dersom valgt;
- delvis fullført;
- handling nødvendig;
- feil eller recovery;
- langvarig manglende mål;
- blokkert speiling, target drift eller leasekonflikt.

Regler:

- varseladapteren tåler duplikatlevering;
- ett varsel grupperer like problemer per jobb/run;
- klikk åpner korrekt persisted entity, ikke en rå filsti fra payload;
- varslingsfeil endrer ikke filrunresultatet, men registreres som `OUTBOX_DELIVERY_FAILED` og prøves igjen;
- e-post er utenfor første versjon.

### 18.8 Systemstatusfelt

Systemstatusfeltet er en GUI-klient av Engine Host:

- viser autoritative progresssnapshots og reconnectstatus;
- kan sende pause/resume/stop/åpne GUI;
- kan lukkes uten å avslutte aktiv Engine Host-run;
- har ingen databasewriter, watcherautoritet eller Robocopy-håndtak;
- ved protocol mismatch viser den oppgraderingsbehov og sender ingen muterende kommando.

---

## 19. Feilhåndtering og observabilitet

### 19.1 Feiltaksonomi

Feil klassifiseres etter domene, retrybarhet og sikkerhetskonsekvens. Minst følgende koder støttes:

```text
ENGINE_NOT_AVAILABLE
ENGINE_PROTOCOL_MISMATCH
ENGINE_VERSION_INCOMPATIBLE
IPC_FRAME_INVALID
IPC_AUTHORIZATION_FAILED
IPC_RATE_LIMITED
COMMAND_IDEMPOTENCY_CONFLICT
COMMAND_DEDUP_TOMBSTONE_CONFLICT
TRIGGER_DEDUP_CONFLICT
OUTBOX_CLAIM_LOST
CROSS_STORE_HANDOFF_INCOMPLETE
ENDPOINT_UNAVAILABLE
ENDPOINT_IDENTITY_MISMATCH
ENDPOINT_CAPABILITIES_CHANGED
ENDPOINT_LEASE_BUSY
ENDPOINT_LEASE_LOST
STALE_FENCING_TOKEN
MUTATION_PERMIT_INVALID
ENDPOINT_LOCK_UNRELIABLE
ACCESS_DENIED
DISK_FULL
TARGET_FILE_TOO_LARGE
TARGET_NAME_TOO_LONG
TARGET_PATH_TOO_LONG
TARGET_CHANGED_SINCE_ANALYSIS
PARENT_IDENTITY_CHANGED
REPARSE_POINT_INTRODUCED
NETWORK_INTERRUPTED
SOURCE_CHANGED_DURING_COPY
BASELINE_CONTEXT_MISMATCH
ROBOCOPY_START_FAILED
CHILD_PROCESS_CONTAINMENT_FAILED
ROBOCOPY_FAILED
ROBOCOPY_TERMINATED_BY_SUPERVISOR
STAGING_MANIFEST_MISMATCH
INTENT_SEGMENT_MISMATCH
STAGING_FLUSH_UNSUPPORTED
VERIFY_FAILED
NAMED_STREAM_NOT_PORTABLE
COMMIT_FAILED
FINAL_DURABILITY_UNCONFIRMED
PATH_OUTSIDE_ROOT
CASE_COLLISION
UNSUPPORTED_REPARSE_POINT
DATABASE_LOCATION_UNSUPPORTED
DATABASE_BUSY
DATABASE_FULL
DATABASE_CORRUPT
SNAPSHOT_BATCH_CONFLICT
SNAPSHOT_IMMUTABILITY_VIOLATION
RETENTION_REFERENCE_CONFLICT
BACKUP_SET_INCOMPLETE
BACKUP_SET_EPOCH_MISMATCH
RESTORE_BLOCKED_BY_NEWER_INTENT
COMPACTION_EPOCH_INCOMPLETE
MIGRATION_EPOCH_INCOMPLETE
MIGRATION_FAILED
OUTBOX_DELIVERY_FAILED
EXTERNAL_RESOURCE_DRIFT
RECOVERY_REQUIRED
RECOVERY_STATE_AMBIGUOUS
```

Hver feiltype angir:

- `retry_class`: never, immediate_once, exponential, after_user_action;
- `scope`: operation, run_target, run, engine, installation;
- `safety_effect`: none, block_operation, block_target, block_all_mutations;
- brukerrettet tittel og anbefalt handling;
- teknisk event code og sanert detaljer.

### 19.2 Retrybudsjett

Retry skal eies av ett lag. Robocopy og outer coordinator kan ikke hver ha ukjent multipliserende retry.

Standardbudsjett:

- Robocopy intern: `/R:3 /W:2`, inkludert i samlet attemptbudsjett;
- batch/coordinator: maksimalt to nye batchforsøk etter klassifisering;
- nettverk: eksponentiell backoff med jitter og øvre grense;
- IPC command retry: samme idempotency key og payload;
- outbox: vedvarende retry med capped backoff;
- lease busy: vent/kø etter policy, men ingen spin;
- endpointidentity, path escape, reparse drift, target drift, tilgangsnekt, kapabilitetsbrudd og databasekorrupsjon prøves ikke blindt;
- disk full prøves bare etter ny peakberegning og faktisk plassendring;
- source changed kan få ny analyse, ikke direkte commitretry;
- hvert operation-/batch-/commandforsøk lagres uten å overskrive tidligere historikk.

Total retrytid og antall vises i diagnostikk. «Pause» eller offline venting teller ikke som aktiv retryloop.

### 19.3 Kjøringsresultat og reduksjon

```text
SUCCESS
SUCCESS_WITH_WARNINGS
ACTION_REQUIRED
PARTIAL_FAILURE
FAILED
CANCELLED
BLOCKED_BY_SAFETY
RECOVERY_REQUIRED
```

Runresultat beregnes deterministisk fra `run_targets` og ikke fra siste event. Eksempler:

- alle targets `SUCCEEDED` → `SUCCESS`;
- minst ett target lykkes og minst ett er failed/offline → `PARTIAL_FAILURE` eller `ACTION_REQUIRED` etter policy;
- enhver uavklart commit → `RECOVERY_REQUIRED`;
- target drift/lease lost uten commitambiguity → `BLOCKED_BY_SAFETY` for target og redusert runresultat;
- varslings-/outboxfeil alene endrer ikke korrekt filresultat, men gir operasjonell advarsel.

### 19.4 Strukturert logging

Engine Host skriver strukturert JSONL og en begrenset lesbar UI-logg. Hver hendelse har:

- UTC og monotonic offset;
- `process_instance_id`, `command_id`, `analysis_id`, `plan_id`, `run_id`, `run_target_id`, `operation_id`, `batch_id` der relevant;
- severity, event code, retry class og state transition;
- endepunkt/resource key, owner installation, ownership epoch, lease ID og local fencing token der relevant;
- relativ sti eller opaque object-ID bare ved behov;
- byte, duration, Robocopy-kode, transferstate, assurance, durability og sanert exception;
- app-, protocol-, planner-, contract- og schema-versjon.

Regler:

- aldri logg passord, tokens, launch nonce, Credential Manager-secret eller full command payload med sensitiv data;
- absolutte private stier kan saneres i supporteksport;
- ikke én standardhendelse per identisk/filtrert fil;
- aggreger progress og cachetreff;
- commit, ownership/takeover, lease, object allocation, version, quarantine, conflict, migration og recovery logges detaljert;
- hver Robocopy-batch har unik logg;
- loggkø er bounded; debug-events kan droppes/coalesces med teller, men audit/recoveryhendelser går aldri via en droppable loggkø;
- logg er ikke sannhetskilde for filcommit.

### 19.5 Revisjonslogg

For hver muterende operasjon registreres:

- job-/endpoint-/planrevisjoner og planchecksum;
- source/target/parent-/case-/path-preconditions;
- source guard-kind, source-evidens og før-/etterfingerprints;
- writer owner, ownership epoch, lease-ID, resource key, local fencing token og owner instance;
- endpointrevisjoner og relative original-/finalstier samt opaque staging/version/quarantine object IDs, objectmanifest og intentsegment-ID/ordinal;
- før-/etterfingerprints, file IDs og hardlinkklassifisering der tilgjengelig;
- recoveryeventsekvenser og hashkjede;
- transferstate, assurance og durability for staging/final;
- trigger, command receipt og brukerbeslutning;
- verification-/metadata-/named-stream-policy;
- alle attempts og final outcome.

Audit er append-orientert. En retentionjobb kan aggregere gamle, ikke-kritiske detaljrader først etter at ingen recovery-, restore-, baseline-, ownership- eller eksportreferanse finnes.

### 19.6 Recovery ved krasj, strømbrudd eller Engine Host-tap

Recovery er en egen Engine Host-modus. Før ny run mot et berørt mål:

1. Start kompatibel, unelevated Engine Host og ta singleton-mutex.
2. Oppdag/avslutt eventuell migration epoch før ordinære writes.
3. Åpne catalog/recovery separat, verifiser schema/migration checksums og recoveryeventenes per-run hashkjeder.
4. Avstem ikke-terminale `store_handoffs`/`recovery_handoffs` før nye muterende commands tas imot. En catalog-side `PREPARED` run uten matching peercommit har aldri mutasjonstillatelse.
5. Finn ikke-terminale recoveryruns/operations, short inboxes, managed objects/manifester og checksummede target-side intentsegmenter.
6. Klassifiser kontrollområdet. Dersom writer-eier/epoch avviker, stopp automatisk recovery og krev eksplisitt ownership recovery/takeover; gammel lokal state kan ikke mutere ny eiers mål.
7. Ta global endpointlock, les markør på nytt under lock, og registrer ny lease + local fencing token i den aktuelle ownership epoch før ny `MutationPermit` utstedes.
8. Verifiser endpointidentity/generation, intentsegmenthash, relative stier/object IDs, SafePath/final path, casekontekst og reparse-status.
9. Les journalfase, planchecksum, source-/targetpreconditions, source guard-evidens, handoff-ID og relevante fingerprints.
10. Inspiser faktisk source, inbox/objectmanifest, final, version/quarantine objects og segmentets autoriserte intentrad.
11. Klassifiser:
    - `CONTINUE_FROM_VERIFIED_OBJECT`;
    - `FILESYSTEM_APPLIED_NEEDS_CATALOG`;
    - `CATALOG_APPLIED_NEEDS_RECOVERY_ACK`;
    - `RESTORE_PREVIOUS_TARGET`;
    - `REVERIFY_FINAL`;
    - `DISCARD_UNVERIFIED_INBOX`;
    - `TARGET_DRIFT_REQUIRES_DECISION`;
    - `OWNERSHIP_EPOCH_MISMATCH_REQUIRES_DECISION`;
    - `LOCAL_JOURNAL_MISSING_USE_INTENT_SEGMENT_FOR_REVIEW`;
    - `CROSS_STORE_HANDOFF_INCOMPLETE`;
    - `USER_DECISION_REQUIRED`.
12. Utfør bare en idempotent overgang som er tillatt av journal, objectmanifest, handoff og observerte postconditions.
13. For katalogoperasjoner følg deres egen type-/identitytilstandsmaskin; en fil på forventet katalogsti er konflikt, ikke idempotent suksess.
14. Avstem catalog i separat kritisk transaksjon, deretter recovery acknowledgement i ny separat transaksjon.
15. Marker segment/object `reconciled`/`cleanup_eligible` først når alle refererte operasjoner er terminale og catalog/recovery/targetmanifest er enige.
16. Fjern inbox/segment og slipp lease først etter terminal, verifisert avstemming og retention-/holdkontroll.

Recovery skal aldri:

- bruke prosess-ID, heartbeat, databasefase, UTC-deadline, handoff eller intentsegment alene som bevis;
- dersom separate stores brukes: holde write-transaksjoner i begge samtidig;
- bruke absolutte stier fra recoverypayload;
- gjenbruke gammel permit eller akseptere resultat med feil writer/epoch/token;
- rydde en annen installasjons namespace;
- slette både ny og gammel kopi;
- fjerne eneste verifiserbare managed object/final/version;
- overskrive target som har driftet til ukjent innhold;
- starte ny run mot samme mål før recovery er terminal;
- rekonstruere en plan fra loggtekst eller Robocopy-output;
- late som en manglende side i en cross-store handoff er commitbevis.

Dersom `recovery.sqlite` er korrupt, går installasjonen i read-only sikkerhetsmodus. Intentsegmenter, objectmanifester og faktiske filer kan brukes som sekundært bevis i en veiledet recovery, men automatisk mutasjon er blokkert til autoritativ state er gjenopprettet eller brukeren eksplisitt velger et sikkert utfall. Dersom catalog er korrupt, kan recoveryjournal/targetbevis beskytte eksisterende filer, men jobber/planer rekonstrueres ikke automatisk fra stier eller logger.

### 19.7 Database- og migrasjonsfeil

- `DATABASE_BUSY` håndteres med kort busy timeout og kø; ingen endeløs retry.
- `DATABASE_FULL` stopper nye writes og nye filmutasjoner; allerede påbegynt irreversible fase må bringes til sikrest mulig journalført punkt eller `RECOVERY_REQUIRED`.
- `DATABASE_CORRUPT` blokkerer alle muterende commands og starter aldri ny Robocopy.
- Migrasjonsfeil ruller ikke videre til halvkompatibel host; backup beholdes og readiness publiseres ikke.
- Restore eller compaction åpner aldri ett medlem fra ny epoch og ett fra gammel; uklar swap holder Engine Host i maintenance.
- Et backupsett med recovery-high-water eldre enn uavklarte target-intents kan brukes til inspeksjon, men ikke til automatisk muterende restore.
- Catalog kan i noen tilfeller rekonstruere caches/snapshots, men jobbrevisjoner, planseal, outcomes, baselines og audit må ikke kastes stille.
- Recoverydatabase kan aldri «nullstilles» automatisk mens intentsegmenter, uferdige handoffs eller uavklarte managed objects, batch inboxes eller stagingallokeringer finnes.

### 19.8 Feilinjeksjon og kaostester

Testharnessen skal kunne injisere:

- Engine Host-kill før/etter hver recoverycommit;
- kill mellom hver `PREPARED`/`PEER_COMMITTED`/`SOURCE_CONFIRMED`/`COMPLETED`-handofffase og mellom catalog outcome/recovery acknowledgement;
- GUI-krasj og reconnect under aktiv run;
- duplisert/malformed IPC-command, samme key med annen principal/payload og pipe-flood over connection/framegrenser;
- samtidig GUI- og Task Scheduler-start;
- to samtidige aktiveringer av overlappende root claims; bare én catalogtransaksjon kan vinne;
- identisk snapshotbatchretry og samme sekvens med annen payloadhash;
- forsøk på å oppdatere sealed snapshot/plan;
- baseline context/generation endret etter planseal;
- andre lokal-/SMB-prosess som holder endpointlock;
- lease loss under SMB-disconnect, deretter reacquire med høyere fencing token mens gamle workerresultater fortsatt ankommer;
- forfalsket/serialisert `MutationPermit` og direkte kall til commitadapter uten gyldig permit;
- reparse point opprettet etter analyse;
- target endret etter analyse og rett før commit;
- child-process failure før/under Job Object-assignment; Robocopy skal aldri resume ukontrollert;
- Robocopy som fortsatt kjører når Engine Host dør; Job Object skal stoppe den;
- før/etter publisering av hvert intentsegment og mismatch i segmenthash/ordinal;
- før/etter `ReplaceFileW`, fallback rename, flush og final verify;
- catalog failure etter `FILESYSTEM_APPLIED`;
- recoverydatabase-/catalog-WAL full;
- migration kill etter backup, etter første database og før committed epoch;
- retention med tilfeldig referansegraf og hold som dukker opp før deletebatch;
- outbox claim loss, sen completion fra gammel claim og duplicate delivery;
- command/trigger receipt kompaktert til tombstone før en forsinket identisk eller konflikterende retry;
- backup-/restore-/compaction-kill etter hvert filbytte, inkludert forsøk på blandet databaseepoch;
- manglende/endrede inbox-, managed-object-, manifest-, final- eller intentsegmentfiler;
- fremmed writer-eier, eierskapsepokebytte og stale arbeid fra tidligere installasjon;
- `.mediasync` som ukjent brukerdata, case-alias, nyere schema, korrupt eller delvis kontrollområde;
- feil parent-ID i hver sammensatt database-FK;
- per-katalog caseflagg endret etter snapshot/planseal;
- metadatarevalidert cached hash forsøkt brukt som `SKIP_IDENTICAL`;
- source guard loss eller kildebytte mellom preflight og Robocopy-open;
- path-limit der final path er gyldig, men en speilet intern path ville vært for lang;
- systemklokkehopp og restart med gamle claims;
- regex som overskrider mønster-/CPU-/tidsbudsjett;
- `SQLITE_FULL` i catalog, recovery, logg og intern backupreserve.

For hver relevant injeksjon skal tester bevise `SAF-004`, `REC-001`–`REC-003`, `SYNC-004`, `DB-004`–`DB-005`, `ARC-001`, `ARC-003`–`ARC-013`, `DUR-001` og `SEC-001`.

---
