# Milepæler


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Bindende implementeringsrekkefølge, leveranser og kvalitetsporter. Utfør bare én milepæl om gangen.


## 20. Milepæler og konkrete Codex-oppgaver

### 20.0 Leveranseregler

Hver milepæl skal leveres som en liten, gjennomgåbar endring eller PR. Før neste milepæl:

- kjør relevante unit-, integration- og GUI-tester;
- kjør Ruff og mypy;
- oppdater `docs/IMPLEMENTATION_STATUS.md`;
- oppdater `docs/REQUIREMENTS_TRACEABILITY.md` med krav → kode → test;
- mål nye varme kodebaner;
- dokumenter alle avvik;
- stopp dersom kvalitetsporten ikke består.

Første nyttige produktflyt skal komme tidlig. Destruktive funksjoner, fler-mål og toveis bygges først etter at én kilde → ett mål fungerer vertikalt og recovery er bevist.

### 20.1 Milepæl 0A — Arkitekturbevis og eiergodkjenning

**Krav-ID-er:** `DOC-001`, `OWN-001`, `CTRL-001`, `DB-002`, `DB-005`, `DB-006`, `DB-007`, `CASE-001`, `SRC-001`, `PATH-001`, `TIME-001`, `PROC-001`, `ARC-001`, `ARC-009`, `ARC-010`, `SEC-001`

0A er en sekvens av små, gjennomgåbare arbeidspakker. Codex utfører én arbeidspakke per økt/branch, evaluerer kvalitetsporten og stopper. Prosjekteieren åpner neste arbeidspakke manuelt.

| Arbeidspakke | Formål | Standardutfall |
|---|---|---|
| `0A.0` | Miljø- og sikkerhetspreflight | Kjørbarhetsmatrise og blockers |
| `0A.1` | Prosessmodell, named pipe og Job Object | Prosess-/IPC-bevis |
| `0A.2` | Kontrollområde, endpoint-eierskap og SMB-lås | Eksklusiv writer-bevis |
| `0A.3` | Korte objektstier, replace/recovery og source guard | Filsystem-/TOCTOU-bevis |
| `0A.4` | SQLite-arkitektur og kapasitet | Målt databaseanbefaling |
| `0A.5` | Windows argv, systemsti og pakking | Build-/runtimebevis |
| `0A.6` | Evidenssyntese og eierbeslutning | ADR-anbefalinger og manuell port |

Et blokkert eksperiment stopper ikke uavhengige, ikke-muterende eksperimenter. Det berørte beviset markeres `BLOCKED_BY_ENVIRONMENT`. Hele arbeidspakken stopper bare når videre arbeid bryter en sikkerhetsgrense eller avhenger av det manglende beviset.

#### 20.1.1 Milepæl 0A.0 — Miljø- og sikkerhetspreflight

- Valider dokumentpakken og registrer Git-baseline.
- Inventer Windows, Python, SDK/API-er, privilegier, Task Scheduler, VM-/SMB-lab, filsystemer, fri plass og pakkemiljø.
- Klassifiser alle senere eksperimenter som `RUNNABLE_NOW`, `RUNNABLE_WITH_LOCAL_FIXTURE`, `REQUIRES_USER_LAB_ACTION`, `BLOCKED_BY_ENVIRONMENT` eller `OUT_OF_SCOPE`.
- Opprett ingen produktkode eller muterende labfixture.
- Følg `docs/spikes/0A.0_ENVIRONMENT_PREFLIGHT.md` og stopp.

#### 20.1.2 Milepæl 0A.1 — Prosessmodell, named pipe og Job Object

- Bevis Engine Host discovery/readiness, reconnect og protokollmismatch uten muterende kommandoer.
- Bevis OS-token-/SID-/sessionverifisering.
- Bevis `CREATE_SUSPENDED → Job Object assignment → resume → kill-on-close` med instrumentert child.
- Tillat separate blockers for Task Scheduler-kontekst og Job Object-bevis.
- Følg `docs/spikes/0A.1_PROCESS_AND_IPC.md` og stopp.

#### 20.1.3 Milepæl 0A.2 — Kontrollområde, endpoint-eierskap og SMB-lås

- Prototyp alle `ControlAreaClassifier`-tilstander.
- Bruk to ekte Windows-klienter/VM-er mot dedikert SMB-lab for global lock, fremmed owner, takeover og stale epoch.
- To lokale prosesser er ikke cross-machine-bevis.
- Mangler lab, lever harness og manuell veiledning, og marker bare cross-machine-radene blokkert.
- Følg `docs/spikes/0A.2_ENDPOINT_OWNERSHIP.md` og stopp.

#### 20.1.4 Milepæl 0A.3 — Korte objektstier, replace/recovery og source guard

- Bevis opaque managed objects for lange logiske stier.
- Prototyp `ReplaceFileW` og journalført fallback med feilinjeksjon.
- Bevis idempotent recovery for fil- og katalogoperasjoner.
- Prototyp `SourceReadGuard` og fallbacken `POST_TRANSFER_CURRENT_HASH_REQUIRED`.
- Følg `docs/spikes/0A.3_RECOVERY_PATHS_AND_SOURCE_GUARD.md` og stopp.

#### 20.1.5 Milepæl 0A.4 — SQLite-arkitektur og kapasitet

- Sammenlign én- og to-databasekandidat med samme invariants og datasett.
- Mål krasj, handoff, backup/restore, migrasjon og kompleksitet.
- Mål minst én million filposter: størrelse, WAL, bulkinnlasting, peak RSS og representative P95-spørringer.
- Behold `catalog.sql`/`recovery.sql` som blokkerte plassholdere.
- Følg `docs/spikes/0A.4_SQLITE_AND_CAPACITY.md` og stopp.

#### 20.1.6 Milepæl 0A.5 — Windows argumenter og pakking

- Bevis `GetSystemDirectoryW`, kanonisk argv-serialisering og forbudte flaggkontroller.
- Bevis minimal Python/PySide6/BLAKE3/Win32-build på ren Windows-VM.
- Start ikke produksjons-Robocopy.
- Følg `docs/spikes/0A.5_WINDOWS_ARGUMENTS_AND_PACKAGING.md` og stopp.

#### 20.1.7 Milepæl 0A.6 — Evidenssyntese og eierbeslutning

- Samle bevis, blockers, risiko og reverseringskostnad i ADR-er og beslutningsregister.
- Codex kan sette `evidence_status` til `EVIDENCE_COMPLETE`, `RECOMMENDED` eller `BLOCKED`, men kan ikke endre `owner_decision`.
- Bare prosjekteieren kan godkjenne, avvise eller utsette med eksplisitt scope-reduksjon.
- Ingen kontrakt får `frozen`-status før tilhørende ADR har `owner_decision = OWNER_ACCEPTED` og valideringstest finnes.
- Følg `docs/spikes/0A.6_DECISION_REVIEW.md` og stopp.

#### Samlet kvalitetsport for 0A

- Ingen produktdatabase, endelig migrasjon, syncmotor eller muterende produksjonsflyt er implementert.
- Alle muterende prober har brukt validert labrot og etterprøvbar cleanup-policy.
- Bevisene inneholder eksakte miljøer, kommandoer, råartefakter og ærlige resultater.
- Sikkerhetskritiske blockers er enten løst eller knyttet til en eksplisitt eiergodkjent reduksjon av produktscope.
- Nødvendige ADR-er har `owner_decision = OWNER_ACCEPTED` før 0B åpnes.
- Codex starter aldri neste arbeidspakke automatisk.

### 20.2 Milepæl 0B — Repository, kontrakter, arkitekturporter og appramme

**Krav-ID-er:** `DOC-001`, `ARC-001`, `ARC-002`, `ARC-004`, `ARC-011`, `PERF-001`, `SEC-001`, `UX-001`, `UX-002`, `UX-003`

#### Oppgaver

- Opprett repository-strukturen fra §10 med separate entrypoints/composition roots for launcher, Engine Host, trigger client og GUI.
- Opprett den operative dokumentpakken fra §0.5: kort `AGENTS.md`, milepælsrettede fagfiler og ADR-index. Masterfilen skal ikke kopieres inn i hver oppgaveprompt.
- Opprett versjonerte kontraktfiler under `schema/` for IPC, endpoint marker, intentsegment, reason codes og state machines. `catalog.sql`/`recovery.sql` er autoritative først når Milepæl 1 fryser dem.
- Konfigurer CI som validerer Markdownlenker, ADR-status, JSON Schema/YAML, SQL-migrasjoner og drift mellom genererte enums/types og kontraktene.
- Konfigurer Python, PySide6, Ruff, mypy, pytest, coverage, Windows CI og dependency-/lisensskann etter ADR-ene fra 0A.
- Konfigurer `import-linter` eller tilsvarende arkitekturtester som håndhever lagretningen fra §9.6 og forbyr en generell write-capable filsystemport i application/domain.
- Definer første versjon av IPC-envelope, handshake, feilkoder, faste framegrenser, klientkvoter og kompatibilitetsmatrise uten muterende use cases.
- Implementer en minimal Engine Host som publiserer readiness/health over local-only named pipe, verifiserer faktisk klienttoken/SID/session og lar en GUI-klient koble til via en testbar IPC-port.
- Etabler runtimepolicy for alle interne roller: normalt brukertoken, ingen UAC/elevasjon, kontrollert current directory/DLL-søk og eksplisitt handle inheritance.
- Opprett minimal native PySide6-app med korrekt DPI-oppsett, `ThemeManager`, tokens, QSS-builder og ikonregister.
- Opprett appramme med navigasjon, handlingslinje, arbeidsflate og aktivitetslinje samt development-only komponentgalleri.
- Opprett test-, build- og benchmarkskript og status-/traceabilityfiler.

#### Kvalitetsport

```powershell
python -m pytest
python -m ruff check .
python -m mypy src
python -m importlinter
```

- Alle bindende 0A-ADR-er har `owner_decision = OWNER_ACCEPTED`, eller prosjekteieren har eksplisitt redusert scope og dokumentert blocker; ingen prototypeantakelse kopieres ukritisk til produksjon.
- `AGENTS.md` peker bare til relevante dokumenter for 0B og beskriver presedens/stoppregler.
- Kontraktsvalidering feiler ved ukjent state transition, duplikat reason code eller schema/code-drift.
- GUI og Engine Host starter som separate, ikke-elevated prosesser og består protocol handshake.
- Named pipe avviser remote clients og klienter med feil bruker-/installasjonskontekst; identitet tas fra OS-tokenet, ikke meldingens `role`-felt.
- GUI kan koble fra og til uten at Engine Host avsluttes.
- Presentasjonslaget kan ikke importere SQLite-, Robocopy-, Win32-mutation- eller recoveryadaptere.
- Bare capability-typede porter finnes for staging/final mutasjon; `MutationPermit` kan ikke konstrueres utenfor leaseadapteren.
- Domain/application importerer ikke Qt eller konkrete adapters.
- Lys/mørk/systemtema og 100/150/200 % DPI fungerer.
- Ingen uavgrensede produksjonskøer, databasewrites eller filmutasjoner finnes.

### 20.3 Milepæl 1 — Engine Host, IPC, immutable revisjoner og databaser

**Krav-ID-er:** `DOC-001`, `ARC-001`, `ARC-002`, `ARC-004`, `ARC-005`, `ARC-007`, `ARC-008`, `ARC-009`, `ARC-013`, `REC-001`, `REC-002`, `REC-003`, `DB-002`, `DB-004`, `DB-005`, `DB-006`, `DB-007`, `TIME-001`, `OPS-001`, `SEC-001`, `SYNC-002`

#### Oppgaver

- Implementer Engine Host-singleton per bruker/installasjon med ACL-beskyttet mutex/named pipe og definert readiness.
- Implementer versjonert, størrelsesbegrenset JSON-IPC med local-only pipe, verifisert klienttoken/SID/session, authorization, handshake, request correlation, klient-/abonnements-/rategrenser og sanert feilmodell.
- Implementer command inbox/dispatcher med global idempotency key, principal, schema-/payloadhash, permanent dedup-tombstone og monoton receiptlivssyklus.
- Implementer query/read-model-endepunkter og sekvensnummererte progresssnapshots.
- Implementer enums/value objects for jobbtype, syncmodus, analyse, plan, run, attempt, transfer, assurance, durability og commitfase fra maskinlesbare kontrakter.
- Opprett faktiske migrasjoner for valgt databasearkitektur etter ADR-0A. Bare Engine Host får writable connections/migratorrolle; databaseplassering på NAS/SMB/flyttbart medium avvises.
- Implementer `endpoints`/`endpoint_heads` og `jobs`/`job_heads` uten sirkulær førstegangs-FK. Headbytte bruker parent-scoped composite FK og optimistic CAS.
- Implementer alle parent-scope composite FKs, all-or-none CHECKs og negative migrasjonstester fra §11.0.1. Sealvalidering er tillegg, ikke erstatning.
- Konfigurer defensive SQLite-policyer: foreign keys, `STRICT` der støttet, `trusted_schema=OFF`, extension loading deaktivert, query-only read pool og avtalt durability.
- Implementer immutable revisjoner/filterversjoner og triggers/guards som avviser in-place-endring etter seal.
- Implementer run/attempt/outcome, append-only recovery events og lovlige faseoverganger uten fil-I/O.
- Implementer cross-store handoffs dersom to databasefiler ble valgt; samtidig write transaction i begge stores er forbudt.
- Implementer transactional outbox-skjelett og desired-state-tabeller. Claims bruker owner instance + generation/token og monoton runtime-deadline; startup avstemmer gamle claims.
- Implementer intern backup/migration epoch etter valgt databasearkitektur og AppData-kapasitetsmåling/preflight.
- Opprett falsk monoton/UTC-klokke, fake ports og feilinjeksjonsgrensesnitt.
- Generer schema-/state-/reason-code-dokumentasjon og typer i CI; håndskrevet drift skal feile builden.

0B-evidence 2026-07-31: Catalog migration 31 og
`initial_backup_plan_materializations` binder første standard-backupplan til den
aktive jobbrevisjonens eksakte forseglede source-/target-snapshots. Terminal
`SEALED`/`NO_CHANGES` er immutable og replayes uten ny plan-ID. Planleggeren
normaliserer under target case-semantikk, blokkerer casekollisjon og bruker
konservative target-preconditions. GUI kan lese planen uten at en run finnes.
Ingen run startes automatisk; `CREATE_DIRECTORY`-planer er preview-only til
directory executor/journal er ferdig.

#### Kvalitetsport

- Bare én kompatibel Engine Host blir tilstandseier ved samtidige launcherforsøk.
- Samme muterende command levert flere ganger, også fra ulike klientprosesser før/etter restart, gir ett autoritativt resultat.
- Samme idempotency key med annen principal, schema eller payload avvises.
- Første endpoint/job kan opprettes som entity → revision → head uten deferred hack eller ugyldig FK.
- Hver parent-scope-negativtest bruker en gyldig child-ID fra feil parent og avvises av SQLite/triggeren.
- GUI og trigger client kan ikke åpne databasefilene direkte gjennom produksjonskode.
- Ingen transaksjon holdes over IPC-/fil-/prosessventing; valgt cross-store-modell består crash/reconciliation-testene fra ADR-en.
- Claims tåler systemklokkehopp og restart uten å stjele levende arbeid eller låse permanent.
- `SQLITE_FULL`-injeksjon bevarer recoverybevis og starter ingen ny transfer.
- Ugyldige state transitions og in-place-endring av immutable revisjoner avvises.
- Nyere ukjent schema/protokoll gir tydelig incompatibility og ingen writes.
- Maskinlesbare kontrakter, migrasjoner, genererte enums og dokumentasjon er synkronisert.

### 20.4 Milepæl 2 — Endepunkteierskap, kontrollområde, kapabiliteter og `SafePath`

**Krav-ID-er:** `OWN-001`, `CTRL-001`, `LOCK-001`, `PATH-001`, `CASE-001`, `ARC-003`, `ARC-005`, `ARC-006`, `ARC-011`, `ARC-012`, `SAF-002`, `SAF-005`, `END-001`, `META-001`, `META-002`, `DB-001`

#### Oppgaver

- Implementer endepunktvelger for lokal, USB, mapped og UNC gjennom Engine Host-commands/queries.
- Implementer `ControlAreaClassifier` med alle tilstander i §4.1.4. `.mediasync` ekskluderes eller opprettes aldri før klassifisering.
- Implementer eksplisitt registrering av skrivbare endepunkter, checksummet `endpoint.json`, installasjonsspesifikke namespaces og target-side ownership records.
- Implementer eksklusiv writer-policy og journalført overtakelsessaga som øker `ownership_epoch` under global endpointlock.
- Implementer read-only og controlled writable probe, immutable endpointrevisjon og `capabilities_hash`.
- Implementer volum-/share-/rootidentitet, fysisk enhetsbevis og endepunktgenerasjon.
- Implementer faktisk OS-handlebasert global mutationlock, epoch-lokal fencing counter, privat `MutationPermit`-factory og stale owner/epoch/token-avvisning.
- Implementer `SafePath`, `ReparseGuard`, final-path-oppløsning fra handles og extended Windows paths.
- Implementer short managed-object path allocator og manifestpath-validator; ikke kopier payload ennå.
- Implementer parent/root identity og source-/target-precondition-primitiver.
- Implementer `WindowsNameComparer`, per-katalog case-queryadapter og case-context-hashing.
- Implementer fri plass, maksimal fil-/navn-/stilengde, safe no-overwrite, source guard, durability- og lockkapabiliteter.
- Implementer immutable historiske root claims og atomisk aktiv claim-kontroll.
- Implementer GUI-read models/dialoger for kontrollområde, fremmed eier, overtakelse og endepunktdiagnostikk.

0B-evidence 2026-07-31: Lokal førstegangsregistrering av ett valgt skrivbart mål
er implementert gjennom en eksplisitt GUI-handling, restartbar catalog-intent,
checksummet schema-4-markør, immutable ownership-record, installasjonsspesifikke
namespaces, kontrollert writable probe og append-only endpoint-/jobbrevisjoner.
Startup-resume og fail-closed avvisning av ukjent/fremmed/korrupt kontrollstate er
dekket. Dette fullfører ikke Milepæl 2: takeover, bred endpointvelger/kapabiliteter,
og SMB-bevis gjenstår. Pre-migration-30 pending-jobber kan nå repareres gjennom
en eksplisitt, revisjonsbundet **Registrer mål**-handling med restartbar intent,
command receipt og ny klassifisering/snapshot/plan etter commit.

#### Kvalitetsport

- Ingen test kan skrive utenfor eksplisitt testrot eller validert kontrollområde.
- `UNKNOWN_NONEMPTY_DIRECTORY`, case-alias, korrupt eller nyere-ukjent `.mediasync` blokkerer mutasjon og skjules ikke av filter.
- Fremmed owner er read-only. Overtakelse krever eksplisitt brukerhandling, global lock, ny epoch og full ny analyse.
- To ekte Windows-klienter/VM-er mot samme SMB-share kan ikke være writer samtidig; stale gammel epoch avvises etter reconnect.
- Feil markør, root identity, endpointgeneration eller capabilities hash blokkerer mutasjon.
- Lease loss/reacquire øker lokal token innen epoch; epokebytte invaliderer hele gammel permitkontekst.
- Reparse point eller final path som introduseres etter validering blokkerer operasjonen.
- Begge case-kolliderende poster kan representeres, og per-katalog casekontekst kan probes/persisteres.
- FAT32-/syntetiske fil-/navnegrenser blokkeres før transfer.
- Short object path forblir gyldig når en speilet intern path ville overskredet grensen.
- Uten pålitelig lock kan bare den eksplisitte, testede `COPY_NEW_ONLY_NO_REPLACE`-kapabiliteten tilbys; øvrige writes blokkeres.

### 20.5 Milepæl 3 — Strømmet skanner, coverage, filter og indeks

**Krav-ID-er:** `CTRL-001`, `CASE-001`, `FILTER-001`, `HASH-001`, `DB-001`, `DB-003`, `DB-004`, `META-001`, `PERF-001`, `SAF-003`

#### Oppgaver

- Implementer iterativ `os.scandir`-scanner bak `FileTreeReader`-porten.
- Implementer `directory_coverage`, `snapshot_issues`, per-katalog case-mode/evidence/context hash og live-snapshotresultatene fra §13.1.
- Åpne/valider katalogidentity før og etter enumerering og gjør begrenset rescan av volatile områder.
- Bygg versjonert sammenligningsnøkkel én gang per post.
- Strøm schema-versjonerte, checksummede `ScanBatch`-objekter til Engine Hosts katalogwriter; receipt og batchdata committes i samme transaksjon.
- Implementer filtermotor med betinget kontrollområdeekskludering, glob som standard og bounded/cancellable regexbudsjett.
- Registrer filer, mapper, tomme mapper, reparse points, volatility og skannefeil.
- Implementer batchbasert stabilitetskø uten venting per fil. Opprett hashcachetabell/evidensmodell, men ikke bruk svak cache til `SKIP_IDENTICAL`.
- Implementer hierarkisk cancellation, frekvensbegrenset progress og eksplisitt snapshotforsegling med checksum, batch-/coverage-/countkontroll og immutable seal.
- Opprett syntetisk testtre-generator og scannerbenchmarks.

#### Kvalitetsport

- Ufullstendig, kansellert eller volatil skann kan ikke feilmerkes som `COMPLETE_NO_KNOWN_GAPS`.
- Destruktiv absence proof kan bare bruke komplett coverage for berørt scope.
- 100 000 filer skannes med avgrenset minne.
- Scanner/domain importerer ikke SQLite og leser ikke hash, EXIF, MIME, preview eller streams.
- `birthtime_ns` brukes korrekt.
- Batchretry med samme `(snapshot_id, sequence_no)` og payloadhash dupliserer ikke poster eller coverage; samme sekvens med annen hash blokkeres som konflikt.
- Etter seal kan ingen filpost, coverage eller snapshotmetadata endres; senere hashresultater lagres i avledet cache/artifact.
- Mange ustabile filer gir samlet tidskø, ikke N × ventetid.

### 20.6 Milepæl 4 — Deterministisk enveis oppdateringsplan

**Krav-ID-er:** `ARC-005`, `ARC-006`, `SYNC-001`, `SYNC-002`, `SAF-003`, `DB-003`, `END-001`

#### Oppgaver

- Implementer `multi_target_backup`-validering med ett mål og bare `UPDATE_FORWARD`.
- Implementer sortert snapshot-/coverage-sammenligning uten live fil-I/O i planner.
- Implementer metadatafingerprints, årsakskoder, capabilitetsblokker og persistente hashbehov.
- Materialiser source-, target-, parent-, path-chain- og lease-preconditions per muterende operasjon.
- Implementer fase-/dybdeorden, dependency DAG og topologisk validering.
- Implementer versjonert canonical serializer, planseal og deterministic checksum.
- Implementer risk summary, config/filter/policy/capability hashes.
- Implementer dry-run og brukeroverstyring som ny avledet plan, aldri in-place.
- Opprett keyset-paginerte repositoryqueries.

#### Kvalitetsport

- Samme input gir byteidentisk canonical plan og checksum uavhengig av batch-/workerrekkefølge.
- Endret revisjon, coverage, capability, policy eller serializer/plannercompatibility ugyldiggjør planen.
- Alle muterende rader har eksplisitte preconditions og gyldig dependency graph.
- Planen inneholder bare nye kopier, mapper, skip, defer eller blokkering.
- Ingen ekstra målfil flyttes/slettes.
- Hard gate kan ikke overstyres i GUI.
- Én million syntetiske par planlegges med avgrenset minne.

### 20.7 Milepæl 5 — Supervisert Robocopy, manifeststaging og nye filer

**Krav-ID-er:** `ARC-001`, `ARC-003`, `ARC-006`, `ARC-009`, `ARC-010`, `ARC-011`, `ARC-012`, `SAF-001`, `SAF-004`, `REC-001`, `REC-002`, `REC-003`, `DUR-001`, `OBS-001`, `PERF-002`, `SEC-001`

#### Oppgaver

- Implementer `GetSystemDirectoryW`-basert, final-path-validert Robocopy-adapter med typed argumentliste, canonical Windows-serializer, round-trip-harness, `shell=False`, sikkert working directory/DLL-søk, minimalt Unicode-miljø, normalt brukertoken og eksplisitt handleliste.
- Implementer Windows Job Object-process supervisor som oppretter child suspended, tildeler et ikke-arvbart Job Object med kill-on-close/no-breakaway og først deretter gjenopptar prosessen; assignment-/policyfeil terminerer child før den kan kjøre.
- Implementer immutable `BatchManifest`, short batch inbox, opaque `managed_object`-allokering/manifester og canonical hashes. Full brukersti kan ikke bli fysisk kontrollsti.
- Implementer directory-manifest/storfilbatch og eksakt post-process stagingenumerering.
- Implementer unike batchlogger, outputgrenser og returkodeklassifisering.
- Implementer transfer, stagingflush/verifisering og no-overwrite commit for **nye filer bare**.
- Implementer endpoint owner/epoch/lease/token, `MutationPermit`, `SourceReadGuard` eller forseglet post-transfer-hashfallback, source-/target-/parent-/case-revalidation og bounded immutable intentsegmenter gjennom hele commitflyten; segmentene kjedes med checksum, bruker bare relative stier/persistente ID-er og publiseres varig før første mutasjon.
- Implementer catalog ↔ recovery handoff for run start/outcome og recoveryfasene, pause/stopp og idempotent attemptrestart.
- Implementer operation attempts, outcomes og audit. 0B-grunnlaget er levert i
  catalog schema 40 med recovery-avstemming og bounded IPC-read model.
- Mål mot direkte Robocopy.

#### Kvalitetsport

- Robocopy kan aldri motta final tree som destinasjon eller forbudte flagg.
- En instrumentert child kan ikke utføre første instruksjon før vellykket Job Object-assignment; assignmentfeil etterlater ingen kjørende child.
- Engine Host-kill stopper tilhørende Robocopy gjennom Job Object.
- Faktisk staginginnhold matcher manifestet eksakt; ekstra/manglende/reparse-objekt blokkerer commit.
- Uverifisert eller ikke-journalført staging committes aldri.
- Target/reparse/parent drift rett før commit gir blokkering, ikke overwrite.
- Fault injection etter hver fase og hvert cross-store handoff mister ingen tidligere gyldig fil.
- Intentsegmenter er bounded og skalerer uten én kontrollfil per brukerfil; recovery avviser brutt checksum-/segmentkjede.
- Ingen plan-, IPC-, recovery- eller target-side post lagrer en absolutt brukerfilsti som autoritativ mutasjonsadresse.
- Returkoder klassifiseres korrekt og parallelle batcher deler aldri logg. Argument round-trip består alle Windows-edge cases og forbudte flagg oppdages etter serialisering.
- Nye filer kan gjenopptas uten full planrestart når seal/manifest/preconditions fortsatt holder.

Dette er den første interne, ikke-destruktive dataplan-kjernen.

### 20.8 Milepæl 6A — Designsystem og produktskall

**Krav-ID-er:** `UX-001`, `UX-002`, `UX-003`, `UX-004`, `PERF-001`

#### Oppgaver

- Ferdigstill tokens, kontrastpar, typografi, spacing, radius og ikoner.
- Implementer apprammen med fire toppnivåer: Oversikt, Jobber, Historikk og Innstillinger.
- Implementer separate aktivitets-, oppmerksomhets- og per-mål-ferskhetsdimensjoner samt dynamisk primærhandling som gjenbrukbare visningsmodeller og komponenter.
- Implementer handlingslinje, aktivitetslinje, detaljpanel og responsiv navigasjon.
- Implementer kontrasttester for alle tillatte tokenpar.
- Implementer tom-, laste-, offline-, feil- og blokkert-komponenter.

#### Kvalitetsport

- Ingen side har mer enn én primærhandling per område.
- Aktivitet, oppmerksomhet og ferskhet er separate; `Oppdatert`, `Sist sikkerhetskopiert`, delresultat og blokkering kan ikke forveksles.
- Navigasjon og fokus fungerer med tastatur.
- Tillatte fargepar består kontrastkrav.
- Ingen produksjonsside har spredte QSS-/designverdier.
- Shell består latens- og DPI-smoketester.

### 20.9 Milepæl 6B — Dashboard og jobboppretting

**Krav-ID-er:** `SYNC-001`, `SAF-002`, `UX-001`, `UX-003`, `UX-004`, `UX-006`, `AUTO-001`

#### Oppgaver

- Implementer dashboard med oppmerksomhetsseksjon, per-mål-ferskhet og én anbefalt handling.
- Implementer firestegs standardflyt for én kilde → ett mål, med støtte for å legge til flere mål senere.
- Foreslå jobbnavn automatisk og lagre utkast automatisk.
- Implementer standardkortet for `Oppdater backup · Alle brukerfiler · Standard kontroll` og sammenleggbare avanserte seksjoner.
- Implementer separat inngang og skjermskall for avansert `pair_sync`; skjul reverse/toveis i standard backupflyt.
- Vis registrering av skrivbare mål og kontrollmappen eksplisitt.
- Implementer endepunktkort, målvelger, filtervalg, valgfri automatikk og sikkerhetsoppsummering.
- Implementer blokkering av like/nestede rotområder, kollisjon med andre lagrede skrivbare jobbrøtter, utkastbasert redigering og trygg arkivering/reaktivering av jobber.

#### Kvalitetsport

- Standard backup kan opprettes i høyst fire steg og helt med tastatur.
- Brukeren trenger ikke velge teknisk kontrollnivå, hash, scheduler-kontekst eller Robocopy-parametere.
- Kilde, retning og mål kan ikke misforstås.
- Dashboardet viser eksakt siste vellykkede tidspunkt per mål og skjuler ikke delresultat.
- Samme fysiske lagringsenhet, nettverksdeling eller kjente aliaser vises ikke som flere bekreftet uavhengige kopier.
- Jobboppretting endrer ingen brukerfiler.
- Ugyldig jobbtype-/modus-kombinasjon kan ikke lagres.
- Like eller overlappende kilde-/målrot kan ikke lagres; separate røtter på samme lagringsenhet vises som advarsel, ikke som uavhengige kopier.
- Redigering av etablert jobb endrer ikke gjeldende konfigurasjon før eksplisitt lagring og kan ikke påvirke en aktiv kjøring.
- Arkivering stopper automatikk og automatisk retensjonsopprydding, bevarer historikk/gjenoppretting og endrer ingen brukerfiler.
- Lange stier og frakoblede mål bryter ikke layout.
- Oppgavetestene for oppretting og målstatus fra §8.30 er dokumentert.

### 20.10 Milepæl 6C — Endringer, kjøring, resultat og historikk

**Krav-ID-er:** `SYNC-002`, `REC-002`, `OBS-001`, `UX-001`, `UX-002`, `UX-004`, `UX-005`

#### Oppgaver

- Implementer **Endringer**-visning med beslutningssummer, oppmerksomhetsbanner, per-mål-utvalg, årsakskoder, filtre og detaljpanel.
- Skjul uendrede filer som standard og fremhev `Krever oppmerksomhet`.
- Implementer **Kjør backup** som sikkerhetskontroll + analyse + hurtigflyt for nye filer, mappeoppretting og forventede filterhopp.
- Implementer kontrollstopp for første kjøring, endret konfigurasjon, erstatning, konflikt, karantene, blokkering og mål som uventet blir utilgjengelig; et på forhånd synlig, eksplisitt valg om **Kjør på tilgjengelige mål** skal ikke gi en redundant modal.
- Implementer aktiv kjøring med ærlig fase, byte, hastighet, ETA, aktiv fil og per-mål-resultat.
- Implementer pause/stopp, målspesifikt nytt forsøk og gruppert problemvisning.
- Implementer stabil fullføringsoppsummering med `N av M mål` og anbefalt neste handling.
- Implementer `Ingen endringer` som et lagret kontrollresultat uten tom kjøring.
- Implementer `Kontroll fullført – handling nødvendig` uten tom kjøring når alle funn er utsatt eller blokkert.
- Vis kontroller og backupkjøringer som separate aktivitetstyper i historikken.
- Implementer `Fullført – handling nødvendig` for eksplisitt utsatte automatikkoperasjoner.
- Implementer historikkdetalj, audit og kort gjenopprettingsflyt for ett element.
- Implementer virtuelle tabeller, cancellable queries og `UiUpdateCoalescer`.

#### Kvalitetsport

- Brukeren kan gjennomføre én kilde → ett mål: opprett → kontroller → kopier nye filer → verifiser → se resultat og historikk.
- En etablert trygg backup starter med én bevisst handling og ingen redundant modal.
- En risikoplan starter aldri via hurtigflyten.
- Delvis resultat kan ikke leses som fullført på alle mål.
- GUI-tråden utfører ingen fil-I/O eller stor SQL.
- Operasjonstabellen håndterer én million syntetiske rader uten full materialisering.
- Gjenoppretting kan åpnes før ny kjøring mot samme mål.
- Aktiv kopiering fortsetter ved sidenavigasjon.
- Oppgavetestene for start, delresultat, nytt forsøk og stopp fra §8.30 er dokumentert.

Dette er første brukbare, ikke-destruktive hjemme-MVP.

### 20.11 Milepæl 7 — Hashing og identiske filer

**Krav-ID-er:** `SYNC-003`, `HASH-001`, `DUP-001`, `META-002`, `DB-003`, `PERF-001`

#### Oppgaver

- Implementer streaming BLAKE3, quick signature og cache.
- Integrer behovsstyrt hash i tvetydig sammenligning.
- Implementer relasjonsklassene for forventet replika og interne duplikater.
- Implementer kontekstuell duplikatvisning, rapport og forhåndsvisning av valgt fil.
- Del kildehash mellom mål, og sett bakgrunnshashing på pause når kopieringen er flaskehalsen.

#### Kvalitetsport

- Samme størrelse/ulikt innhold bekreftes aldri identisk.
- Forventede replikaer gir 0 mulig besparelse.
- Vanlig update fullhasher ikke alle uendrede filer.
- Store filer hashes streaming.
- Funksjonen endrer ingen filer.

### 20.12 Milepæl 8 — Erstatning, durability, versjoner og full commitprotokoll

**Krav-ID-er:** `OWN-001`, `PATH-001`, `SRC-001`, `VER-001`, `REC-004`, `ARC-003`, `ARC-006`, `ARC-009`, `ARC-011`, `ARC-012`, `SAF-004`, `REC-001`, `REC-002`, `REC-003`, `DUR-001`, `META-002`, `OBS-001`

#### Oppgaver

- Implementer `REPLACE_CHANGED` med komplett source-/target-/parent compare-and-swap.
- Implementer `ReplaceFileW`-adapter med versjonsbackup når dokumenterte forutsetninger holder.
- Implementer journalført fallback med `OLD_TARGET_PRESERVED`.
- Implementer `STAGING_DURABLE`, `FINAL_DURABLE`, eksplisitt flush/write-through-resultat og ærlige guarantee levels.
- Implementer target-side intentsegmenter og avstemming mellom recoverydatabase, catalog, final, staging og versions.
- Implementer eksplisitt cross-store handoff for `FILESYSTEM_APPLIED` → recovery outcome → katalogoutcome; ingen database kan anta at den andre ble committet atomisk.
- Implementer full finalverifisering og idempotent katalogoutcome.
- Implementer versionsmanifest, gjenoppretting og retention holds.
- Implementer named-stream-/metadata-policy og verifisering.
- Utvid feilinjeksjon over alle replace-, flush- og catalogvinduer.

#### Kvalitetsport

- Feil/krasj før, under eller etter replace mister aldri både gammel og ny kopi.
- Target som endres etter analyse overskrives aldri.
- Katalog, recoveryjournal, handoff og intentsegment kan avstemmes etter `FILESYSTEM_APPLIED`.
- Oppnådd durabilitynivå lagres og GUI lover ikke mer enn adapteren kan bevise.
- Gammel versjon kan gjenopprettes med egen lease/recoveryoperasjon.
- Named streams blokkeres eller verifiseres etter policy.
- Diskplassberegningen dobbelteller ikke same-volume version rename.

### 20.13 Milepæl 9 — Tre mål og adaptiv scheduler

**Krav-ID-er:** `ARC-003`, `SYNC-001`, `DB-003`, `PERF-001`, `PERF-002`

#### Oppgaver

- Utvid `multi_target_backup` til opptil tre mål.
- Del kildesnapshot, kildehash og immutable planinput.
- Implementer per-mål-planseal, run-target-state, endpointlease, outcomes og historikk.
- Implementer separate correctness leases og ytelsestokens fra §16.
- Implementer ressursgraf, canonical token acquisition, cancellation/fairness og recoveryprioritet.
- Implementer Skånsom/Auto/Maks, separat `/MT`, prosess- og aktiv-mål-regulering.
- Implementer EWMA, hysterese, cooldown og offline-mål-policy.

#### Kvalitetsport

- Kilden skannes én gang.
- Ett offline/busy mål blokkerer ikke andre og utløser ingen destruktiv handling.
- Ytelsestoken kan aldri erstatte endpointleasen.
- Ingen SQLite-transaksjon holdes mens scheduler venter på token/lease/I/O.
- Samme HDD/NAS overbelastes ikke som standard.
- Profilendringer kan forklares og endrer aldri plan/sikkerhet.
- Kildehash beregnes høyst én gang per gyldig generasjon.

### 20.14 Milepæl 10 — Speiling, karantene, retention og katalogorden

**Krav-ID-er:** `PATH-001`, `REC-004`, `CASE-001`, `OWN-001`, `ARC-003`, `ARC-006`, `DB-004`, `SAF-001`, `SAF-003`, `REC-001`, `REC-002`, `REC-003`, `DUR-001`

#### Oppgaver

- Implementer `MIRROR_FORWARD` for fler-målsjobb.
- Implementer target-extra-planlegging, destructive coverage og sikkerhetsterskler.
- Implementer destruktiv revalidering av source absence, target fingerprint, parent identity og lease.
- Implementer journalført karantene som opaque managed objects, metadata-manifest for tomme kataloger og fallback copy-verify-remove.
- Implementer canonical fase-/dybdeorden, egne katalog-recoverytilstander og dependency checks.
- Implementer gjenoppretting og separat immutable retentionplan for brukerinnhold med holds/audit; den skal være atskilt fra katalogdatabasens referansedrevne retention i §11.6.
- Implementer terskeldialoger med konkret omfang.

#### Kvalitetsport

- Ingen `/MIR` eller `/PURGE` finnes i produksjonskommandoer.
- Ufullstendig/volatil coverage eller source drift blokkerer relevant karantene.
- Mål som endres etter analyse karanteneflyttes ikke.
- Filer behandles før foreldre i korrekt dybdeorden.
- Same-volume rename dobbelteller ikke nyttelastplass.
- Permanent sletting skjer bare i egen retentionoperasjon, aldri sync-run.
- Mistet lease stopper nye mutasjoner og gir entydig recovery.

### 20.15 Milepæl 11 — Automatisering, desired state og systemstatusfelt

**Krav-ID-er:** `TIME-001`, `LOCK-001`, `OWN-001`, `ARC-001`, `ARC-004`, `ARC-007`, `ARC-013`, `AUTO-001`, `AUTO-002`, `SAF-002`, `SYNC-002`

#### Oppgaver

- Implementer Task Scheduler desired-state-adapter, reconciler og logontypematrisen.
- Implementer trigger bootstrap som bruker samme cross-session `HostLocator`: koble til eksisterende host eller bli/etabler host gjennom eksplisitt ownership/lifetime-handshake; ingen fire-and-forget spawn.
- Implementer tid, pålogging, lokal oppstart, volume events og file watcher som hints/triggere.
- Implementer mapped-drive → UNC-konvertering.
- Implementer automatikkpolicy før planseal, `DEFER_AUTOMATION_POLICY`, monotone live claims/startup reconciliation og resultatet `Fullført – handling nødvendig`. No-lock-endepunkt er read-only som standard; eksplisitt copy-new-only har separat opt-in.
- Implementer transactional outbox-dispatch for Windows-varsler.
- Implementer systemstatusfelt som ren IPC-klient uten databasewriter, watcherautoritet eller Robocopy-håndtak.
- Vis nettverks-/credentialbegrensninger før registrering.

#### Kvalitetsport

- Task Scheduler og manuell start som leverer samme occurrence/command skaper ikke dobbelt run.
- Planlagt bootstrap uten eksisterende host etterlater ikke et usupervisert child; enten er taskprosessen selv host eller ownership/lifetime-handshake er bevist.
- En allerede task-eid host gjør ikke at senere planforekomster ignoreres; parallelle triggerinstanser dedupliseres i Engine Host.
- NAS-jobb med `S4U` kan ikke registreres.
- `INTERACTIVE_TOKEN` forklarer påloggingskravet; eksplisitt `PASSWORD` lagrer ikke passord i MediaSync.
- Task drift repareres idempotent fra desired state; ukjent eierskap slettes ikke blindt.
- Watcher overflow gir full skann.
- Outbox kan levere duplikat uten doble varslingseffekter eller endret runresultat.
- Tray kan lukkes/krasje uten å stoppe Engine Host/run.
- Samme jobb kjører ikke konkurrerende mot samme endpointlease.

### 20.16 Milepæl 12 — Reverse pair sync og toveis

**Krav-ID-er:** `SYNC-001`, `SYNC-004`, `SYNC-005`, `CASE-001`, `SAF-003`, `SAF-004`, `REC-002`, `DB-004`

#### Oppgaver

- Implementer `pair_sync` med nøyaktig to endepunkter.
- Implementer reverse update og reverse mirror bare for denne jobbtypen.
- Implementer immutable `baseline_sets`, et checksummet `baseline_context_hash`, tombstones og beslutningsmatrisen.
- Bind baselinekontekst til eksakte endepunktrevisjoner/rootidentiteter, case-/timestamp-/comparisonsemantikk, filter-/metadata-/conflictpolicy og planner-/schemaversjoner.
- Implementer deterministisk planmaterialisert konfliktnavn, recoverybeskyttet «behold begge»-saga og avklart baselineoppdatering.
- Implementer konservativ rename/move-detektering med fil-ID-hint/full hash.

#### Kvalitetsport

- Reversevalg vises aldri for fler-målsjobb.
- Alle toveismatriserader har tester.
- Første kjøring uten baseline er ikke-destruktiv.
- Endret baselinekontekst kan ikke gjenbruke gammel baseline; ny baseline set opprettes og første kjøring er ikke-destruktiv med mindre ekvivalens er eksplisitt bevist.
- Begge endrede filer bevares.
- Delvis feil gir ikke falsk full baseline.

### 20.17 Milepæl 13 — Brukervennlighets-, tilgjengelighets- og lokaliseringsherding

**Krav-ID-er:** `UX-001`, `UX-002`, `UX-003`, `UX-004`, `UX-005`, `PERF-001`

#### Oppgaver

- Ferdigstill alle tom-, offline-, blokkert-, delvis fullført-, feil- og gjenopprettingstilstander.
- Gjennomfør og dokumenter alle oppgavetester i §8.30; rett sikkerhetskritiske misforståelser før øvrig visuell finesse.
- Ferdigstill norsk/engelsk lokalisering og kjør terminologisjekk mot §8.18.
- Test tastatur, skjermlesernavn, fokusretur, reduced motion og High Contrast.
- Produser referansebilder i lys/mørk ved representative DPI-er.
- Profilér tabeller, detaljpanel, søk, tema og fremdriftsoppdatering.
- Fjern layout-hopping, per-rad widgets, redundant modalbruk og tunge effekter.

#### Kvalitetsport

- Alle oppgavetester er registrert; ingen sikkerhetskritisk misforståelse gjenstår.
- 100, 125, 150 og 200 % DPI består.
- Norsk og engelsk klipper ikke kritisk tekst.
- Status forstås uten farge og delresultat kan ikke forveksles med full suksess.
- Normale sider krever ikke intern teknisk terminologi.
- Navigasjon/filter/detaljpanel består latensbudsjettene.
- GUI fungerer uten mediedekoding.

### 20.18 Milepæl 14 — Ytelses-, stress- og soak-herding

**Krav-ID-er:** `OWN-001`, `CTRL-001`, `DB-003`, `DB-007`, `CASE-001`, `HASH-001`, `SRC-001`, `PATH-001`, `TIME-001`, `OPS-001`, `FILTER-001`, `PROC-001`, `PERF-001`, `PERF-002`, `REC-002`

#### Oppgaver

- Frys referansemaskin og fixtures: 100k småfiler, 1M metadata, blandet media og store videoer.
- Mål `os.scandir`, SQLite bulk, direkte Robocopy og MediaSync.
- Profiler skanner, databaseskriving, sammenligning, batchbygger, monitorering, recovery og GUI.
- Kjør 8+ timers soak med pause/resume, offline mål, nettverksbrudd og fault injection. Inkluder to Windows-klienter mot samme SMB-share, epokebytte, source drift, case-context drift, regexbudsjett og `SQLITE_FULL`.
- Verifiser avgrensede køer, caches, cancellation og backpressure.
- Dokumenter median, P95, peak RSS og begrensninger i `BENCHMARKS.md`.

#### Kvalitetsport

- Store filer ≥ 85 % av Robocopy-baseline.
- Blandet/små filer ≥ 70 % eller eksplisitt godkjent forklaring.
- 1M skann/analyse ≤ 400 MiB peak RSS på referansebuild.
- Ingen vanlig GUI-frys ≥ 100 ms.
- Ingen optimalisering endrer plan/checksum/sikkerhetsresultat.
- Gjenoppretting består alle faseinjeksjoner etter langvarig stresstest. Fremmed writer/stale epoch kan ikke mutere, og ingen intern path overskrider grensen når final path er gyldig.

### 20.19 Milepæl 15 — Pakking, oppgradering og hjemmeutgivelse

**Krav-ID-er:** alle utgivelseskritiske krav, særlig `DOC-001`, `OWN-001`, `CTRL-001`, `ARC-001`, `ARC-004`, `ARC-008`, `ARC-009`, `ARC-011`–`ARC-013`, `DB-004`–`DB-007`, `REC-002`, `DUR-001`, `SEC-001`

#### Oppgaver

- Bygg signeringsklar `.exe` med `pyside6-deploy`/Nuitka og separate interne prosessroller.
- Lag Inno Setup-installer og valgfri installasjonsfri mappepakke. Binærplassering er uavhengig av autoritativ state; database, recovery, pipe-/mutexidentitet og `installation_id` ligger fortsatt i lokal per-bruker AppData.
- Implementer upgrade handshake: launcher oppdager hostversion, quiescer ny work, lar sikre boundaries fullføres eller journalfører recovery, avslutter gammel host og starter kompatibel ny host.
- Implementer schema backup/migration/rollback-protokollen fra §11.4 og interne backup-/restore-sett fra §11.7 med én checksummet epoch for alle valgte state stores, high-water/intentbarrier og deterministisk resume/restore; avvis downgrade eller blandet state-sett.
- Implementer katalogretention/compaction fra §11.6 som cross-store recovery-root-export + mark/sweep over eksplisitte referanser/holds, immutable delete manifest, `retention_pending`, små idempotente batcher og separat checksummet compaction-epoch/`VACUUM INTO` under quiesce.
- Sikre at installer ikke erstatter binærer mens Engine Host/Robocopy bruker dem uten kontrollert shutdown.
- Inkluder plugins, translations, assets, lisensnotiser og dependency manifest.
- Implementer eksport/import av jobber uten credentials eller recovery secrets.
- Skriv norsk brukerhåndbok, arkitekturbegrensninger og gjenopprettingsveiledning.
- Test installasjon, side-by-side/upgrade, avinstallasjon, rollback og ren Windows-maskin.
- Kjør full sporingsmatrise, kontraktsdriftkontroll, ADR-konsistens og utgivelsesmåling. Pakken inkluderer milepælsrettet `AGENTS.md`/docs, ikke en gigantisk prompt som eneste styringsflate.

#### Kvalitetsport

- Ren støttet Windows-maskin kan installere og starte én kompatibel Engine Host og GUI.
- Oppgradering med aktiv run følger dokumentert quiesce/recovery og etterlater ingen foreldreløs Robocopy.
- Oppgradering bevarer immutable revisjoner, planer, baselines og recoverydata.
- Migrasjonsfeil eller kill etter bare én migrert database publiserer ikke readiness; restart fullfører eller gjenoppretter deterministisk fra migration epoch.
- Retention kan ikke slette aktive/paused runs, recovery/handoff, baseline context, forseglede planer/snapshots med catalog-/recoveryreferanser, holds eller target-side intentbevis; endret recovery high-water pauser sweep.
- Krasj i compaction eller state-restore før/etter outputverifisering/swap åpner deterministisk ett komplett verifisert databasepar, aldri en blandet/ukjent epoch.
- Downgrade mot nyere schema blokkeres med recovery-/eksportveiledning.
- Avinstallasjon spør før brukerdata fjernes og håndterer aktive tasks/host gjennom desired-state cleanup.
- Ingen credentials eksporteres eller pakkes.
- Alle krav-ID-er er koblet til kode og bestått test eller dokumentert, godkjent avvik.
