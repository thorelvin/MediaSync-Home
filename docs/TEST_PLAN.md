# Test- og akseptanseplan


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Sporbarhet, arkitekturtester, feilinjeksjon, funksjonell matrise, ytelsesporter og akseptansekriterier.


## 21. Teststrategi

### 21.1 Kravsporbarhet

Alle bindende krav-ID-er skal ha minst én automatisk test, dokumentert manuell verifikasjon, benchmarkgate eller eksplisitt godkjent avvik. En klasse, tabell eller GUI-side er ikke bevis i seg selv; testen skal demonstrere den relevante invarianten.

`docs/REQUIREMENTS_TRACEABILITY.md` skal minst ha:

| Krav | Design | Implementasjon | Test/gate | Status |
|---|---|---|---|---|
| `ARC-001` | §9.1–9.5 | Engine Host composition root | `test_ui_and_trigger_have_no_mutating_adapters` | pass/fail |
| `ARC-004` | §9.4–9.5 | IPC command inbox/dispatcher | identity, duplicate, conflict and restart suite | pass/fail |
| `ARC-009` | §4.5, §9.8, §11.2–11.4 | `CrossStoreHandoffService` | crash-at-every-handoff suite | pass/fail |
| `ARC-010` | §9.2, §15.2 | `ProcessSupervisor` | suspended-child containment test | pass/fail |
| `ARC-011` | §4.4, §9.7, §17.3 | capability-typed ports/commit adapter | no-generic-write-port + permit-forgery suite | pass/fail |
| `ARC-012` | §4.4, §9.9, §11.2 | lease manager/recovery journal | stale owner/epoch/token model and fault suite | pass/fail |
| `ARC-013` | §9.4, §9.13, §11.1 | inbox/trigger/outbox repositories | compaction + delayed retry/claim-loss suite | pass/fail |
| `OWN-001` | §4.1, §4.4, §12.7 | endpoint ownership service | two-Windows-client foreign-owner/takeover/stale-epoch suite | pass/fail |
| `CTRL-001` | §4.1, §7.2, §12 | `ControlAreaClassifier` | all control-area states + conditional exclusion suite | pass/fail |
| `DB-006` | §11.1 | endpoint/job head repositories | first-revision/head-CAS/rollback suite | pass/fail |
| `DB-007` | §11.0–11.1 | schema constraints | wrong-parent composite-FK negative suite | pass/fail |
| `CASE-001` | §11.1, §12.6, §13.3 | case-context probe/store | per-directory mode change invalidates plan | pass/fail |
| `HASH-001` | §6, §11.1, §13.8, §14.7 | hash evidence reducer/cache | metadata-only cache cannot produce `SKIP_IDENTICAL` | pass/fail |
| `SRC-001` | §4.5, §13.6, §15 | `SourceReadGuard`/fallback | source replacement/write/delete race suite | pass/fail |
| `PATH-001` | §4.1, §15.3, §17.4–17.5 | managed object allocator | final-path-near-limit + short-object restore suite | pass/fail |
| `SYNC-005` | §5.5, §14 | conflict allocator/saga | deterministic sealed name + crash-safe keep-both suite | pass/fail |
| `DUP-001` | §6, §11.1 | file-object alias classifier | hardlink/same-object does not count as reclaimable duplicate | pass/fail |
| `VER-001` | §17.1–17.3 | result reducer | transfer/assurance/durability truth table | pass/fail |
| `TIME-001` | §9.8, §9.13, §11.1 | claim manager | wall-clock jump + process/system restart suite | pass/fail |
| `LOCK-001` | §4.4, §12.7, §18.6 | endpoint policy reducer | only proven no-overwrite new-file mode without reliable lock | pass/fail |
| `OPS-001` | §11.8, §19 | local state capacity manager | `SQLITE_FULL`, quota and safe cache-reclaim suite | pass/fail |
| `FILTER-001` | §7 | filter evaluator | regex budget/cancel/disable suite | pass/fail |
| `PROC-001` | §15.2, §15.9 | system executable resolver/argv builder | `GetSystemDirectoryW` + Windows argv round-trip corpus | pass/fail |
| `REC-001` | §4.5 | `RecoveryJournal` | phase fault-injection suite | pass/fail |
| `REC-003` | §4.5, §9.14 | `IntentSegmentStore` | bounded segment/hash-chain suite | pass/fail |
| `REC-004` | §4.5, §17, §19.6 | directory mutation adapters | create/metadata/quarantine/restore crash suite | pass/fail |
| `SAF-003` | §4.4, §13.3 | `DestructiveGate` | `test_incomplete_coverage_blocks_quarantine` | pass/fail |
| `SYNC-004` | §5.5, §14.10 | `BaselineContext` | context mismatch/rebase suite | pass/fail |
| `DB-001` | §11.1 | snapshot repositories | case/coverage integration suite | pass/fail |
| `DB-004` | §11.5–11.6, §13.5 | seal/retention services | immutable seal and mark/sweep suite | pass/fail |
| `DB-005` | §11.4, §11.7 | backup-set/restore epoch service | mixed-pair/intent-high-water crash suite | pass/fail |
| `SEC-001` | §9.2–9.4, §15.2 | runtime/process/IPC policy | unelevated/token/DLL/handle suite | pass/fail |
| `DUR-001` | §4.5, §17.2–17.3 | durability adapter | flush/limitation suite | pass/fail |
| `DOC-001` | §0.5, §10, §20 | contract/docs pipeline | schema/code/docs drift and precedence suite | pass/fail |

Alle utgivelseskritiske krav skal være sporbare begge veier:

```text
krav → design/ADR → maskinlesbar kontrakt → kode → test/gate
kode/test → krav-ID og ADR
```

### 21.2 Arkitektur- og prosesskonformitet

Kjør arkitektur- og kontraktstester i hver CI-jobb:

- UI kan ikke importere konkrete database-, Robocopy-, Win32-mutation-, lease- eller recoveryadaptere.
- Domain kan ikke importere Qt, SQLite, subprocess, Win32 eller adapters.
- Application kan bare avhenge av definerte porter og domain.
- Application/domain har ingen generell write-capable filsystemport; final commit krever `MutationPermit` og `VerifiedStagingArtifact`.
- Bare Engine Host-composition root kan konstruere writable repositories og muterende adapters.
- Trigger client, launcher og tray inneholder ingen sync-, plan-, recovery- eller filsystemmotor.
- Ingen produksjonsmodul bruker global service locator, skjult writable singleton eller alternativ muterende composition root.
- Ingen `pickle`, dynamisk kodekjøring eller uversjonert IPC-serialisering finnes.
- IPC-authorisering bruker verifisert OS-klienttoken/SID/session; payloadens rollefelt kan ikke gi rettigheter.
- Kodebasen kan ikke holde writable transaksjoner i katalog- og recoverydatabasen samtidig, bruke writable `ATTACH DATABASE` eller skjule kryssingen bak en Unit of Work; all kryssing går via typed handoff dersom to databaser beholdes.
- Eierskapsepoke og lokal fencing token følger permit → coordinator → workerresultat → recovery → intentsegment → commit; alle muterende adapterkall avviser fremmed owner, stale epoch eller stale token.
- Persistente plan-, IPC-, recovery- og intentsegmentposter inneholder bare relative brukerfilstier og persistente endepunkt-/revisjons-ID-er.
- Parent-scope-ID-er håndheves i databasen med sammensatte fremmednøkler eller dokumentert trigger; Python-validering alene godtas ikke.
- `endpoint_heads` og `job_heads` er separate tabeller; stabile entiteter har ingen sirkulær `NOT NULL`-peker til første revisjon.
- Forbudte Robocopy-flagg og relative executable paths finnes ikke i command builder.
- Robocopybanen kommer fra `GetSystemDirectoryW`, og den endelige argumentstrengen round-trip-valideres før prosessoppstart.
- Transferchild opprettes suspended og kan ikke gjenopptas før vellykket Job Object-assignment.
- `.mediasync` behandles ikke som kontrollområde før en validert klassifisering foreligger.
- Fysiske staging-, version- og quarantinebaner bruker korte objekt-ID-er, ikke brukerens fulle relative tre.
- En metadatarevalidert hashcachepost kan ikke reduseres til nåværende innholdsbevis.
- Live claim-/timeoutlogikk leser monoton klokke; UTC alene kan ikke stjele eller forlenge et claim.
- Alle produksjonskøer, caches, eventbuffere og regexevalueringer har eksplisitt kapasitet/budsjett og overflow-/cancelpolicy.
- JSON Schema, YAML, SQL/migrasjoner, genererte typer og dokumentasjon valideres for drift i CI.

Prosesskonformitet testes på Windows:

1. Start minst ti launchere samtidig, inkludert interaktiv og Task Scheduler-lignende ikke-interaktiv sesjon; nøyaktig én kompatibel Engine Host blir eier.
2. Koble GUI og trigger client samtidig og bevis at begge bruker samme host og samme command inbox.
3. Krasj GUI; Engine Host og aktiv run fortsetter.
4. Krasj Engine Host; Job Object stopper transferchild og restart går til recovery.
5. Start inkompatibel GUI/host-protokoll; ingen muterende command aksepteres.
6. Forsøk direkte writable databaseåpning fra en annen testprosess; produksjonskorrekthet skal ikke avhenge av at dette tilfeldigvis mislykkes.
7. Koble fra remote/feil-SID/session-klient og overskrid frame-, rate- og subscriptiongrenser; forbindelsen avvises uten mutasjon.
8. Lever samme globale idempotency key fra GUI og trigger client med identisk payload; nøyaktig én logical command/effect oppstår.
9. Lever samme nøkkel med annen principal, schema eller payload; kommandoen avvises som konflikt uten ny effekt.
10. Mist og ta samme lokale lease på nytt; gamle permits, workerresultater og commitmeldinger avvises etter tokenøkning.
11. Gjennomfør kontrollert endepunktovertakelse; alle permits, planer og recoveryforsøk fra gammel `ownership_epoch` avvises.
12. Bruk to ekte Windows-klienter/VM-er mot samme SMB-share; bare én kan være writer, og fremmed owner forblir read-only.
13. Klassifiser alle `.mediasync`-tilstander, inkludert ukjent ikke-tom mappe, case-alias, nyere schema, delvis og korrupt markør; ingen ukjent brukerdata ekskluderes stille.
14. Forsøk å sette inn gyldige child-ID-er fra feil parent i alle sikkerhetsrelevante relasjoner; SQLite skal avvise raden.
15. Injiser systemklokkehopp bakover/fremover mens claims er aktive; live ownership påvirkes ikke av wall clock.
16. Restart host/Windows med gamle claims; startup-reconciliation bruker owner instance/generation, ikke persistert monotonverdi på tvers av boot.
17. Kompakter command/trigger/outboxdetaljer til tombstone og lever forsinket identisk og konflikterende retry; ingen ny effekt oppstår.
18. Krasj under intern backup/restore/compaction og verifiser at bare ett komplett databasepar fra én epoch kan åpnes dersom to databaser beholdes.
19. Injiser krasj mellom catalog- og recovery-handoff; restart avstemmer uten simultaneous cross-database write transaction.
20. Start instrumentert transferchild og verifiser at ingen brukerkode kjører før Job Object-policyen er aktiv.
21. Kjør Windows argv-corpus med mellomrom, Unicode, UNC, trailing backslash, tomt argument og grensenær kommandolinje; child mottar eksakt tiltenkt argumentvektor.
22. Bygg en brukerfil med gyldig finalbane nær grensen og bevis at short managed-object path fungerer mens speilet intern bane ville feilet.
23. Bytt eller endre kilde etter plan og rundt Robocopy-open; guard/fallback skal blokkere commit eller bevise current source hash.
24. Fyll lokal AppData/SQLite-fixture kontrollert; nye transfers stopper, mens eksisterende recoverybevis bevares og kan avstemmes.

### 21.3 Testnivåer

#### Unit

- `SafePath`, `ReparseGuard`, final-path-normalisering og Windows-navnesammenligning;
- `ControlAreaClassifier`, markerchecksum, owner/epoch-reduksjon og adoption/takeover-policy;
- endpoint capability/result reduction, lock-, source-guard- og durabilitynivå;
- per-katalog casekontekst, comparison-key-versjon og plan-stale-reduksjon;
- filterregler, coverage, årsakskoder og regexbudsjett/cancellation;
- jobbtype-/modusmatrise, inkludert `COPY_NEW_ONLY_NO_REPLACE`;
- sammenlignings-, duplikat-, hardlink-/same-object- og toveismatriser;
- hash-evidensreduksjon, cacheidentitet og forbud mot svakt `SKIP_IDENTICAL`;
- canonical serializer, plan-/manifestchecksum og dependency DAG;
- deterministic conflict-name allocator og lengde-/case-/reserved-name-regler;
- source-/target-/parent-/case-/owner-/epoch-preconditions;
- managed-object-ID, sharding, manifest og path-budgetberegning;
- `GetSystemDirectoryW`-resolver og Windows argumentserialisering/deserialisering;
- Robocopy-returkoder, profil og forbudte flagg etter endelig serialisering;
- transfer-, assurance- og durability-resultatreduksjon;
- diskplassformel for target og lokal programtilstand;
- Task Scheduler-logontype/desired-state diff;
- recovery-, directory-recovery- og runstateoverganger;
- monoton claim manager, falsk klokke og startup reconciliation;
- global idempotency key/principal/schema/payloadhash og command inbox-livssyklus;
- cross-store handoff/saga og startup reconciliation;
- baseline context hash og baseline-sett;
- aktiv/historisk root-claim-reduksjon;
- immutable snapshot/plan seal og retention-reference graph;
- intentsegmentgrense, segmentkjede og canonical checksum;
- outbox/retry/result reduction;
- kontraktgeneratorer og ukjent/ulovlig state/reason-code-avvisning.

#### Integration

- ekte valgt SQLite-arkitektur med Engine Host som eneste writer;
- første endpoint-/jobbrevisjon → head, ny revisjon → CAS-head og rollback ved feil;
- negative composite-FK-tester for endpoint, revision, analysis, snapshot, plan, operation, run, filter og baseline;
- ekte named-pipe-IPC med ACL, local-only-policy, faktisk klienttoken/SID/session, framing, størrelses-/rategrenser og reconnect;
- command inbox/replay over Engine Host-restart og klientbytte;
- ekte tempfilsystem, Windows handles, reparse points, hardlinks og case-sensitive katalog der støttet;
- alle kontrollområdeklassifikasjoner og fremmed owner/read-only/takeover;
- to ekte Windows-klienter/VM-er mot kontrollert SMB-share for lock, owner, epoch, disconnect og stale reconnect;
- ekte Robocopy/instrumentert child opprettet suspended og innlemmet i Job Object før resume;
- `GetSystemDirectoryW`, final executable-path-check og argv round-trip mot instrumentert child;
- `SourceReadGuard` på lokal NTFS og SMB samt post-transfer current-hash-fallback;
- short inbox, opaque managed object, manifest, final commit, version, quarantine og restore ved grensenære stier;
- endpointlease mellom uavhengige prosesser;
- bounded intentsegmenter, replace/fallback, versions, quarantine, directory operations og retention;
- idempotente catalog ↔ recovery handoffs med krasj i hvert mellomsteg dersom to databaser beholdes;
- immutable snapshot batch receipt/seal og referansedrevet catalog retention/compaction;
- baseline context mismatch og nytt baseline-sett;
- Task Scheduler desired-state mot testoppgaver;
- transactional outbox med duplikatlevering;
- `SQLITE_FULL`, database busy/corruption og lokal state-quota;
- FAT32/exFAT/NTFS-fixtures der tilgjengelig;
- migrasjoner, migration-epoch-krasj, newer-schema rejection og deterministic resume/rollback/backup;
- JSON Schema/YAML/SQL/codegen-drift som skal feile CI.

#### GUI

- shell, reconnect, navigasjon og breadcrumb;
- firestegs oppretting, kontrollmappemelding, fremmed eier, overtakelse og jobbtypevalidering;
- analyse, godkjenning, kjøring, historikk og recovery;
- sannferdig visning av transfer, assurance og durability uten å slå dem sammen til «verifisert»;
- synlig blokkering ved ukjent `.mediasync`, case-context drift, source drift, no-lock-degradering og full lokal programdisk;
- millionradmodell, keyset-filter og cancellable queries;
- tastatur, fokus, tilgjengelige navn og reduced motion;
- lys/mørk/system, High Contrast og 100–200 % DPI;
- norsk/engelsk, kontrast og referansebilder;
- Engine Host utilgjengelig, oppgraderingsbehov og stale progresssnapshot.

#### End-to-end

- én kilde → ett mål, nye filer;
- endret fil med versjonsbevaring;
- én kilde → tre mål, ett offline/busy;
- speiling til karantene;
- fremmed endpointowner → read-only → kontrollert overtakelse → full ny analyse;
- lang finalsti med kort managed-object staging/version/quarantine/restore;
- planlagt lokal jobb, korrekt NAS-kontekst og task-bootstrap med/uten eksisterende host;
- samtidig manuell og planlagt trigger;
- GUI-krasj, Engine Host-krasj og restart/recovery;
- upgrade med aktiv/paused run;
- reverse pair sync;
- toveis konflikt med deterministiske sealed navn og baseline;
- no-lock-endepunkt som bare tillater bevist new-file/no-overwrite, aldri replace eller automatikk.

### 21.4 Sikker testrot og testidentitet

Alle muterende tester krever nærmeste tillatte rotmarkør:

```text
.mediasync_test_root
```

I tillegg:

- testinstallasjon får egen installasjons-ID, brukerdata-/pipe-/mutexnamespace og Task Scheduler-mappe;
- fixturelager må være temp-/testvolum; aldri hjem, Bilder, Skrivebord, diskrot eller ekte produksjons-NAS;
- muterende adapter nekter å starte i testmodus uten testmarkør og expected root identity;
- SMB-tester krever eksplisitt opt-in, to navngitte Windows-klienter/VM-er og unik share-underrot;
- kontrollert full-disk-test bruker kvotert VHD/fixture eller tilsvarende og skal aldri fylle brukerens reelle systemdisk;
- cleanup bruker samme `SafePath`, ownership-, lease- og recoveryregler som produksjon, ikke rå rekursiv sletting.

### 21.5 IPC-, concurrency- og idempotencytester

Test minst:

- fragmenterte/sammenslåtte frames, ugyldig lengde, for stor payload, invalid UTF-8/JSON og ukjent message type;
- handshake med gammel, ny og ukjent protokoll;
- remote client, manglende/feil bruker-SID, installasjons-ID eller session/ACL-kontekst, samt legitim same-SID trigger fra godkjent ikke-interaktiv sesjon;
- klient-, request-, frame-, subscription- og event-rategrenser samt langsom-leser-policy;
- samme command levert før, under og etter restart;
- samme globale idempotency key fra samme og ulike klienter med identisk payload, og konflikt ved ulik principal/schema/payload;
- alle command inbox-overganger og krasj mellom `EFFECT_PREPARED`, handoff, `ACCEPTED` og terminal state;
- timeout etter servercommit før klientresponse; retry skal returnere samme receipt/resultat;
- progress gap/reconnect og full state refresh;
- to commands som konkurrerer om samme jobb, run, endepunkt eller ownership takeover;
- GUI + Task Scheduler + volume event samtidig;
- cancellation under kø-, lease-, Robocopy-, regex- og DB-wait;
- backpressure når langsom klient ikke leser events;
- live claim med wall-clock-hopp og restart med stale owner instance;
- takeover som øker epoch mens gammel klient/worker fortsatt leverer resultater.

Fuzz IPC-parseren som ren parser og i isolert Engine Host-test. Ugyldig input skal gi sanert feil/connection close, aldri kodekjøring, ubegrenset minne eller mutasjon.

### 21.6 Feilinjeksjon og varighet

Harnessen skal kunne drepe prosess eller injisere feil:

- før/etter hver recoverycommit og catalogcommit;
- mellom hvert catalog/recovery run-start- og outcome-handoff;
- før/etter oppretting, flush, no-overwrite rename og publisering av hvert intentsegment;
- mellom intentsegmenter og ved manglende, dupliserte eller endrede segmenter;
- før/etter global endpointlock, owner-/epoch-kontroll, lokal lease og lease loss;
- midt i kontrollert takeover før/etter ny markør/epoch og lokal catalogpublisering;
- før `CREATE_SUSPENDED`, mellom process creation og Job Object assignment, ved assignmentfeil, før resume og ved process exit;
- før/etter `SourceReadGuard`, source reopen, post-transfer hash og source-final-revalidation;
- før/etter stagingmanifestenumerering, flush og verifisering;
- før/etter managed-object-allokering, manifestpublisering, `ReplaceFileW`, no-overwrite rename og fallbackbevaring;
- etter `FILESYSTEM_APPLIED`, før `FINAL_DURABLE`, `FINAL_VERIFIED` og catalog outcome;
- før/etter katalogoppretting, katalogmetadata, karantene og restore;
- under recoveryavstemming;
- ved WAL, lokal state-quota, `SQLITE_FULL`, database busy og corruption;
- ved migration failure/newer schema, og kill etter backup, etter bare første database og før committed migration epoch;
- ved duplicate snapshotbatch med lik/ulik payloadhash og forsøk på mutasjon etter snapshot seal;
- ved case-context-endring mellom scan, plan, transfer og commit;
- ved baseline context mismatch;
- mellom recovery-root-export, catalog-import, mark og sweep;
- under reference-driven retention når hold/reference eller recovery high-water tilkommer før deletebatch;
- under compaction etter output, etter handle-close og før/etter database swap;
- ved outbox commit før/etter ekstern levering;
- ved wall-clock-hopp, process-instance-endring og systemrestart;
- ved regex timeout/cancellation og repeated-budget-exhaustion;
- ved strømbrudd-simulert reopen.

TOCTOU-injeksjoner:

- opprett reparse point etter analyse og rett før commit;
- endre target, parent eller per-katalog case-flag etter første precondition;
- bytt USB-/shareidentitet eller endpointowner under pause;
- endre, erstatte, flytte eller slette source rundt Robocopys åpning og under lesing;
- la annen prosess eller maskin holde/ta global endpointlock;
- koble SMB fra slik at lockstatus blir ukjent, og la gammel writer koble seg til etter overtakelse;
- opprett final target etter `ABSENT`-analyse på no-lock-endepunkt;
- la en vanlig fil dukke opp der en katalogoperasjon forventer katalog.

Invarianter:

```text
Aldri mist både gammel og ny kopi.
Aldri commit staging som ikke matcher manifest, source-policy og assurancekrav.
Aldri muter uten aktiv Engine Host-, run-, owner/epoch-, global lock- og lokal leasekontekst når policyen krever det.
Aldri la en writer fra gammel ownership_epoch utføre ny sideeffekt.
Aldri behandle ukjent .mediasync-innhold som kontrollmetadata eller skjule det med standardfilter.
Aldri utfør destruktiv handling med ufullstendig/volatil coverage eller ukjent casekontekst.
Aldri overskriv target som har endret seg siden plan.
Aldri fortsett gjennom reparse-, final-path-, parent- eller case-context-avvik.
Aldri drive SKIP_IDENTICAL fra metadatarevalidert eller stale hashhint.
Aldri bruke en full brukersti som fysisk staging-, version- eller quarantinebane.
Aldri la transferchild kjøre før containment eller fortsette foreldreløst etter Engine Host-tap.
Aldri autoriser IPC fra selvrapportert rolle når OS-klientidentitet ikke er verifisert.
Aldri holde skrivetransaksjon i catalog og recovery samtidig; handoff skal kunne avstemmes etter hvert krasjpunkt.
Aldri endre et forseglet snapshot/plan eller slette et objekt som fortsatt nås fra en aktiv retentionrot/hold.
Aldri gjenbruke toveisbaseline når baselinekonteksten ikke matcher.
Aldri la wall clock alene avgjøre live claim ownership.
Aldri rapportere CONTENT_HASH_VERIFIED eller sterk durability når bare svakere bevis foreligger.
Recovery, directory recovery og commandretry er idempotente.
```

### 21.7 Funksjonell testmatrise

| Scenario | Forventning |
|---|---|
| Ny kildefil | Manifeststaging → flush/verifisering → no-overwrite commit |
| Identisk fil med current-read hash | `SKIP_IDENTICAL` kan planlegges |
| Identisk fil med bare metadatarevalidert cache | Vises som tidligere/indikert lik; kan ikke sikkert hoppes over uten ny evidens |
| Samme størrelse, ulikt innhold | Hash skiller |
| Forventet replika | 0 byte mulig besparelse |
| Internt duplikat | Rapporteres separat |
| To hardlinks/samme fil-ID på samme volum | `SAME_FILE_MULTIPLE_PATHS`; ingen falsk besparelse |
| Endret målfil i update | Versjoneres og erstattes med compare-and-swap |
| Target endret etter analyse | Operasjon blokkeres; brukerendring bevares |
| Parent/reparse endret før commit | Blokkeres; ingen alternativ sti forsøkes |
| Per-katalog case-flag endret etter analyse | `PLAN_STALE_CASE_CONTEXT_CHANGED`; berørt mutasjon blokkeres |
| Ekstra målfil i update | Beholdes |
| Ekstra målfil i mirror | Karantene etter destructive revalidation |
| Kildecoverage ufullstendig/volatil | Ingen berørt karantene |
| Case-kollisjon | Alle poster lagres; berørt operasjon blokkeres |
| FAT32 og for stor video | Blokkeres før Robocopy |
| Named stream til inkompatibelt mål | Blokkering eller eksplisitt tapspolicy/audit |
| Feil disk på samme bokstav | Identitet blokkerer |
| Fremmed endpointowner | Read-only; ingen permit eller mutasjon |
| Kontrollert takeover | Ny `ownership_epoch`, full ny analyse og gammel plan/permit avvises |
| Gammel writer kobler seg til etter takeover | Stale epoch blokkeres før sideeffekt |
| `.mediasync` mangler | Kan registreres eksplisitt |
| `.mediasync` med gyldig egen markør | Normal kontrollområdeflyt |
| `.mediasync` med fremmed owner | Read-only/overtakelsesflyt |
| `.mediasync` med nyere schema | Read-only; ingen downgrade eller automatisk reparasjon |
| `.mediasync` ukjent ikke-tom eller case-alias | Hard blokkering; innhold ekskluderes ikke stille |
| NAS faller ut | Ingen utrygg commit; lock/owner/precondition avstemmes |
| Endpointlease opptatt | Run-target venter/blokkeres; ingen konkurrerende write |
| Endepunkt uten pålitelig lock, ny fil | Bare bevist `COPY_NEW_ONLY_NO_REPLACE` kan tilbys |
| Endepunkt uten pålitelig lock, eksisterende target | Replace/metadata/karantene blokkeres |
| Disk full på mål | Gammel fil beholdes |
| Lokal AppData/SQLite full | Nye analyser/transfers stoppes; recoverybevis beholdes og konkret tiltak vises |
| Source endres under kopi | Ikke committet; post-transfer hash/guard gir reanalyse eller defer |
| Source byttes mellom revalidation og Robocopy-open | Guard/final evidence avviser commit |
| Engine Host krasjer etter staging | Job Object stopper worker; recovery fortsetter/forkaster trygt |
| Krasj etter old preserved | Fullfører eller gjenoppretter gammel fil |
| Krasj etter `FILESYSTEM_APPLIED` | Avstemmer uten ny blind kopi |
| Katalog opprettet før krasj | Retry verifiserer type/identity og fortsetter idempotent |
| Vanlig fil finnes der katalog forventes | Typekonflikt; ingen «vellykket retry» |
| Durability ikke bekreftet | Ærlig durabilityresultat/advarsel eller policyblokkering |
| Bare metadata verifisert | GUI viser ikke innholdsverifisert |
| Unicode/lang finalsti | Fungerer eller presis blokkering |
| Finalsti gyldig, speilet intern sti for lang | Short managed object lykkes og manifest peker tilbake til logisk sti |
| Restore fra managed object | Original logisk sti valideres; objektsti eksponeres ikke som brukeradresse |
| Reparse-loop | Følges ikke |
| Rot-overlap/alias | Blokkeres før registrering/analyse |
| To jobber samme skrivbare rot i én installasjon | Atomisk aktivering gjør at bare én aktiv claim lykkes |
| To installasjoner samme målrot | Bare markert owner er writer; annen installasjon er read-only |
| Arkivert historisk root claim | Bevarer audit, men blokkerer ikke ny aktiv claim; reaktivering revalideres |
| 0-byte-fil | Kopieres/hashes korrekt |
| Én stor video | `/MT=1`, resume/progress fungerer |
| 1M poster | Paginert GUI og avgrenset minne |
| Tre mål, ett offline | To mål fullfører; offline venter |
| Ytelsestoken mangler | Arbeid venter; correctnesslease påvirkes ikke |
| Reverse i fler-mål-GUI | Ikke mulig |
| Toveis begge endret | Begge bevares |
| Toveis konfliktnavn etter restart | Samme forseglede navn og samme operasjonsresultat |
| Toveis baseline context endret | Gammel baseline avvises; ny baseline set og ikke-destruktiv første kjøring |
| Toveis slettet mot endret | Endret fil bevares |
| Filter/revisjon endret etter analyse | Plan forkastes |
| Regex overskrider budsjett | Evaluering kanselleres, filterfeil vises, mønster deaktiveres etter policy |
| Plan/manifest checksum endret | Kjøring blokkeres |
| Snapshotbatch samme sekvens/samme hash | Idempotent replay |
| Snapshotbatch samme sekvens/annen hash | `SNAPSHOT_BATCH_CONFLICT`; snapshot forsegles ikke |
| Forsøk på å endre forseglet snapshot | `SNAPSHOT_IMMUTABILITY_VIOLATION` |
| Feil parent-ID med ellers gyldig child-ID | SQLite composite FK avviser raden |
| Første jobbrevisjon | Stabil entity → revision → head uten sirkulær FK |
| Head peker til annen jobbs/endepunkts revisjon | SQLite avviser raden |
| Robocopy 1/3/5/7 | Ikke fatal; flagg tolkes og staging verifiseres |
| Robocopy 8+ | Feil; ingen commit |
| Ekstra fil i staging | `STAGING_MANIFEST_MISMATCH` |
| `GetSystemDirectoryW` returnerer systemkatalog | Bare validert Robocopy under denne katalogen kan startes |
| Arg med Unicode/mellomrom/trailing backslash | Instrumentert child mottar eksakt tiltenkt argument |
| Transferchild før Job Object | Kan ikke kjøre; assignmentfeil terminerer suspended child |
| Engine Host dør | Job Object terminerer Robocopy |
| Catalog/recovery handoff avbrutt | Startup reconciliation fullfører eller reverserer idempotent |
| Brutt/manglende intentsegment | Recovery blokkerer mutasjon og rapporterer mismatch |
| 100 ustabile filer | Én batchstabilitetsventing |
| NAS-task med S4U | Registrering blokkeres |
| Duplisert trigger | Ett receipt/run |
| Systemklokken hopper bakover/fremover | Aktiv claim endres ikke av wall clock |
| Restart med gammel claimrad | Ny owner instance avstemmer; stale rad stjeler ikke arbeid |
| Task trigger uten eksisterende host | Bootstrap blir host eller fullfører ownership/lifetime-handshake; ingen orphan child |
| Ny forekomst mens task-eid host kjører | Ny kort triggerinstans leverer til samme host; forekomsten ignoreres ikke av Task Scheduler-policy |
| Outbox levert to ganger | Idempotent brukerresultat |
| Task Scheduler drift | Reconciler reparerer eller ber om handling |
| Upgrade med aktiv run | Quiesce/sikker grense/recovery; ingen orphan worker |
| Migration kill etter første database | Migration epoch gir deterministisk resume eller restore før readiness |
| Catalog retention med aktiv hold/reference | Objektet slettes ikke; delete manifest avstemmes |
| Recoveryreferanse oppstår etter retention mark | Recovery high-water avviker; sweep pauses og rootsett bygges på nytt |
| Compaction-krasj rundt database swap | Intent/checksums velger verifisert gammel eller ny database før readiness |

### 21.8 Property-based og modellbaserte tester

Bruk Hypothesis/stateful testing for:

- relative paths, namespaces, ADS, reserverte navn og root escape;
- lokale/UNC-roter som er like, nestet, aliaserte eller separate;
- kontrollområdenavn med casevarianter, korrupt/ukjent innhold og schema-generasjoner;
- ownership state machine, takeover og monotont økende epoch;
- case-kolliderende Unicode-navn og per-katalog case-context-trær;
- coverage-/sync-/baselinekombinasjoner;
- hash-evidenslattice: svakere evidens kan aldri reduseres til sterkere beslutning;
- file-object-aliasgrupper kontra innholdsduplikatgrupper;
- deterministic canonical plan/manifest og konfliktnavn ved varierende batchrekkefølge;
- managed-object-allokering under alle path-/component-budsjetter;
- Windows argv round-trip for tilfeldig Unicode/backslash/quote-korpus innen dokumentert grense;
- dependency DAG er acyklisk og respekterer dybdeorden;
- ingen muterende operasjon uten komplette owner/epoch/source/target/case/lease-preconditions;
- ingen konfliktløsning fjerner begge kopier;
- ufullstendig coverage gir ingen destructive operation;
- recovery-, directory-recovery- og runstateoverganger er idempotente;
- duplicate commands på tvers av klienter gir én logical effect og kommandolivssyklusen er monoton;
- live claims påvirkes ikke av wall-clock-transformasjoner;
- startup reconciliation konvergerer etter vilkårlig stale owner instance;
- cross-store handoffstate konvergerer uten simultane write transactions;
- snapshotbatch-replay er idempotent, og seal gjør snapshot immutable;
- active root-claim activation er serialiserbar ved konkurrerende raser;
- databaseconstraints avviser alle genererte wrong-parent-kombinasjoner;
- baseline context mismatch kan aldri redusere til `unchanged` mot gammel baseline;
- retention mark/sweep sletter aldri en node som er nådd fra catalog-/recovery-root, hold, handoff eller baseline, heller ikke ved high-water-endring mellom mark og sweep;
- compaction-epoch konvergerer til én verifisert databasefil ved krasj i hvert swapsteg;
- intentsegmentpakking holder seg innen antall-/bytegrensen og bevarer canonical ordning/hashkjede;
- lease acquisition order er deadlockfri;
- diskplassformelen under-/dobbelteller ikke same-volume rename;
- transfer/assurance/durability-resultatreduksjon er assosiativ/deterministisk der spesifisert.

### 21.9 Ytelses- og stressmåling

Logg maskinvare, Windows, strømprofil, filsystem, endepunkt, filfordeling, sikkerhetsprofil og antiviruspåvirkning. Kjør minst tre ganger etter warm-up; rapporter median, P95 og peak RSS.

Baselines:

- minimal `os.scandir`;
- SQLite bulk insert;
- direkte Robocopy med sammenlignbar profil;
- tom PySide6-shell;
- named-pipe round-trip/throughput;
- kontrollklassifisering og owner/epoch-check over lokal disk og SMB.

Scenarier:

- 100k/1M metadataoppføringer;
- 100k småfiler;
- blandet JPG/RAW/video;
- 50–200 GiB storfiler der mulig;
- tre uavhengige mål og delt flaskehals;
- hashing HDD/SSD med current-read og cache-evidens;
- aktiv copy mens GUI navigeres/reconnecter;
- samtidig triggerstorm, global command dedup og IPC rate limiting;
- to ekte Windows-klienter mot samme SMB-share under lock/takeover/reconnect;
- short managed-object-allokering/manifest ved lange logiske stier;
- bounded regexevaluering over 1M paths, inkludert timeoutmønster;
- lokal `catalog.sqlite`/recovery-state ved 1M, 5M og estimert produksjonsretention;
- kontrollert local-state quota/full-disk og sikker cache-reclaim;
- 1M recoveryoperasjoner pakket i bounded intentsegmenter uten én fil per operasjon;
- catalog retention mark/sweep over stor referansegraf;
- pause/resume og gjenbruk av seal/outcomes;
- 8+ timers soak med fault injection, wall-clock-jump og Engine Host restart.

Utgivelsesbudsjett:

| Metrikk | Gate |
|---|---:|
| Storfil-throughput mot Robocopy | ≥ 85 % |
| Blandet/småfiler mot Robocopy | ≥ 70 % |
| Peak RSS ved 1M skann/analyse | ≤ 400 MiB |
| Kald GUI-start til interaktiv | ≤ 4 s |
| Lokal IPC query P95, varm host | dokumentert mål ≤ 100 ms |
| Varm sidenavigasjon P95 | ≤ 150 ms |
| Indeksert filter på 1M P95 | ≤ 500 ms |
| Vanlig GUI-frys | < 100 ms |
| Ubegrensede køer | 0 |
| Foreldreløse Robocopy-prosesser etter host-kill | 0 |
| Regexevaluering uten eksplisitt budsjett | 0 |
| Ukontrollert lokal statevekst uten estimat/kvote | 0 |

### 21.10 Arbeidsflyt- og brukervennlighetstester

Automatiser det målbare og gjennomfør den manuelle protokollen fra §8.30. Mål tid, handlinger og feil for oppretting, trygg start, risikostopp, offline/delresultat, retry, recovery, version restore og NAS-automatikk.

Test i tillegg at brukeren forstår:

- forskjellen mellom egen, fremmed, ukjent og korrupt `.mediasync`;
- at overtakelse er en sikkerhetskritisk eierskapsendring og krever ny analyse;
- at `Kopiert`, `metadata kontrollert`, `innhold hashverifisert` og durability ikke er samme påstand;
- at et endepunkt uten pålitelig lock bare kan få begrenset new-file-modus;
- at full lokal programdisk stopper nye operasjoner uten å true eksisterende backupfiler.

Kvantitative mål:

- standard backupoppretting: høyst fire steg;
- etablert trygg backup: én bevisst starthandling;
- ingen redundant modal for bare nye filer;
- identifisering av frakoblet mål/neste handling normalt under ti sekunder;
- gjenoppretting av ett valgt element: høyst fire handlinger;
- null tilfeller der testbruker forveksler kilde/mål, owner/fremmed mål eller fullført/delvis;
- Engine Host utilgjengelig/protokollmismatch forklares uten teknisk dump og uten at GUI later som en command lyktes.

Registrer kvalitative funn. Et raskt feilvalg er fortsatt en alvorlig UX-/sikkerhetsfeil.

## 22. Akseptansekriterier for komplett hjemmeversjon

### 22.1 Arkitektur og prosessisolasjon

| ID | Akseptansekriterium |
|---|---|
| `AC-ARC-01` | En headless Engine Host er eneste muterende tilstandseier og eneste prosess med writable forbindelser til alle autoritative SQLite-state stores valgt av ADR-003. |
| `AC-ARC-02` | GUI, launcher, trigger client og systemstatusfelt kan ikke konstruere Robocopy-, lease-, recovery- eller filsystemmutasjonsadaptere. |
| `AC-ARC-03` | Samtidige launcherforsøk, også fra interaktiv og ikke-interaktiv Windows-sesjon, resulterer i nøyaktig én kompatibel Engine Host per bruker/installasjon. |
| `AC-ARC-04` | Lokal IPC er versjonert, størrelsesbegrenset, ACL-beskyttet og avviser malformed eller inkompatible meldinger uten mutasjon. |
| `AC-ARC-05` | En muterende kommando er idempotent: retry med samme nøkkel/payload gir samme receipt/resultat og skaper ikke ekstra run eller sideeffekt. |
| `AC-ARC-06` | Samme idempotency key med annen payload avvises som konflikt. |
| `AC-ARC-07` | Jobb-, filter-, endepunkt- og planrevisjoner er uforanderlige; endring oppretter ny revisjon/avledet plan. |
| `AC-ARC-08` | Muterende operasjoner krever aktiv Engine Host-, run- og OS-støttet endpointlease; en databaserad eller lockfilens eksistens er ikke tilstrekkelig. |
| `AC-ARC-09` | Korrekthetsleases og schedulerens ytelsestokens er separate mekanismer; et token kan aldri gi mutasjonstillatelse. |
| `AC-ARC-10` | Ingen SQLite-transaksjon holdes mens kode venter på fil-I/O, prosess, IPC, lease eller schedulerressurs. |
| `AC-ARC-11` | Robocopy kjører under Engine Hosts prosesssupervisor/Job Object; Engine Host-tap etterlater ingen foreldreløs Robocopy. |
| `AC-ARC-12` | Task Scheduler, varsler og andre eksterne sideeffekter styres gjennom desired state/outbox og tåler gjentatt avstemming/levering. |
| `AC-ARC-13` | Nyere ukjent database-/IPC-schema blokkerer writes; migrasjonsfeil publiserer ikke readiness. |
| `AC-ARC-14` | Arkitekturtester håndhever lagretning og forbyr UI/domain-importer av konkrete muterende adapters. |
| `AC-ARC-15` | Launcher, GUI, Engine Host, trigger client og transferchild kjører som standard uten elevasjon, backup-/restore-privilegier eller annen privilegieøkning. |
| `AC-ARC-16` | IPC er local-only, verifiserer faktisk klienttoken/SID/integritetsnivå og rollebestemt sesjonsklasse, og håndhever faste grenser for frame, samtidige klienter, requests, subscriptions og eventrate; selvrapportert rolle gir ingen rettighet. |
| `AC-ARC-17` | Command inbox bruker globalt unik idempotency key og monoton receiptlivssyklus; samme logiske kommando fra ulike klienter eller etter restart kan ikke gi mer enn én effect. |
| `AC-ARC-18` | Dersom ADR-003 velger separate catalog/recovery stores, deltar de aldri i samme skrivetransaksjon; alle kryss-store overganger bruker korrelerte, idempotente handoffs som avstemmes ved oppstart. |
| `AC-ARC-19` | Transferchild opprettes suspended, tildeles et no-breakaway/kill-on-close Job Object og får ikke kjøre før containment er bekreftet; assignmentfeil terminerer child. |
| `AC-ARC-20` | Writable databaser ligger på lokalt, ACL-beskyttet lagringsområde; extension loading er deaktivert, `trusted_schema=OFF` brukes, og read pool er query-only der plattformen støtter det. |
| `AC-ARC-21` | Aktive root claims materialiseres atomisk; historiske/arkiverte claims bevarer audit uten å blokkere nye aktive røtter, og reaktivering revaliderer identitet/overlap. |
| `AC-ARC-22` | Migration epoch beskriver backup og high-water per valgt state store; krasj etter delvis migrasjon gir deterministisk resume/restore før readiness. |
| `AC-ARC-23` | Application/domain har ingen generell muterende filsystemport. Final tree kan bare endres gjennom smale commit/quarantine/version-adapters som krever levende `MutationPermit`, verifisert artefakt og relative stier. |
| `AC-ARC-24` | Hver endpointlease får en monoton fencing token. Lease loss/reacquire gjør alle eldre permits, workerresultater, intentsegmentforsøk og commitmeldinger ubrukelige før sideeffekt. |
| `AC-ARC-25` | Command-, trigger- og outboxdeduplisering overlever detaljretention som kompakte tombstones; claim loss, restart eller forsinket retry kan ikke skape ny akseptert effekt. |
| `AC-ARC-26` | Interne databasebackuper, restore og compaction åpner bare ett verifisert state-sett fra samme epoch; dersom flere databasefiler brukes, publiserer et blandet sett aldri readiness. |
| `AC-ARC-27` | Ett skrivbart målrotområde har nøyaktig én autorisert writer-installasjon per `ownership_epoch`; fremmed owner er read-only, og kontrollert overtakelse øker epoken og ugyldiggjør alt stale arbeid. |
| `AC-ARC-28` | Aktive jobb-/endepunktrevisjoner bruker separate head-tabeller, og alle sikkerhetsrelevante parent-child-relasjoner avvises av databasen når parent-scope ikke matcher. |
| `AC-ARC-29` | `AGENTS.md`, ADR-er, SQL/JSON Schema/YAML-kontrakter, genererte typer og dokumentasjon følger definert presedens; CI feiler ved drift eller ukjent overgang/reason code. |

### 22.2 Sikkerhet, commit og recovery

| ID | Akseptansekriterium |
|---|---|
| `AC-SAF-01` | Ingen produksjonskommando bruker `/MIR`, `/PURGE`, `/MOVE` eller `/MOV`. |
| `AC-SAF-02` | Endepunktidentitet, revisjon, generasjon, root/final path, kapabiliteter og lease kontrolleres før skriving. |
| `AC-SAF-03` | Ufullstendig eller volatil coverage, identitetsavvik eller ukjent lock blokkerer berørte destruktive operasjoner. |
| `AC-SAF-04` | En gyldig målfil overskrives aldri direkte uten manifeststaging, verifisering, commitintensjon og journalført replace. |
| `AC-SAF-05` | En kildefil som endres under transfer eller før commit, blir ikke committet. |
| `AC-SAF-06` | Relevant kildedrift før karantene blokkerer alle gjenværende destruktive operasjoner i berørt scope. |
| `AC-SAF-07` | Like, nestede, aliaserte eller overlappende rotområder blokkeres; separate røtter på samme enhet får advarsel, ikke falsk uavhengighet. |
| `AC-SAF-08` | Target, parent identity eller reparse/final path som endres etter analyse, overskrives/flyttes ikke; operasjonen blir stale/blokkert. |
| `AC-SAF-09` | Robocopy får bare unik staging som mål; bare commitadapteren kan endre final tree. |
| `AC-SAF-10` | Plan-, IPC-, recovery- og target-side poster autoriserer mutasjon med persistente endepunkt-/revisjons-ID-er og relative stier; en lagret absolutt brukerfilsti kan ikke brukes som alternativ mutasjonsadresse. |
| `AC-SAF-11` | En caller kan ikke forfalske eller deserialisere en `MutationPermit`; commit uten live OS-lock, matching token, scope og preconditions avvises uten filendring. |
| `AC-REC-01` | Hver irreversible commitfase finnes i recoveryjournal, og et checksummet immutable intentsegment er varig før første målmutasjon i segmentet. |
| `AC-REC-02` | Krasj før/etter hver fase kan gjenopprettes uten å miste både gammel og ny kopi. |
| `AC-REC-03` | Filsystem, intentsegment, recoverydatabase, catalog og cross-store handoff avstemmes idempotent etter krasj/strømbrudd. |
| `AC-REC-04` | Staging som ikke matcher plan, manifest og verifikasjon committes aldri. |
| `AC-REC-05` | Gammel versjon og karanteneelement kan gjenopprettes fra GUI med egen lease/recoveryoperasjon. |
| `AC-REC-06` | Intentsegmenter er immutable, hashkjedede og begrenset til definert operasjons-/bytevolum; systemet oppretter ikke én target-side kontrollfil per brukerfil. |
| `AC-REC-07` | Manglende, endret, duplisert eller feilordnet intentsegment gir blokkert/ambiguous recovery og kan aldri føre til blind videreføring av målmutasjoner. |
| `AC-REC-08` | Recovery tar ny endpointlease og ny fencing token før sideeffekt; den kan gjenbruke bevis/staging, men aldri gammel mutasjonstillatelse. |
| `AC-REC-09` | Cross-store handoffs følger monoton `PREPARED → PEER_COMMITTED → SOURCE_CONFIRMED → COMPLETED`; uløselig avvik blir `AMBIGUOUS`, ikke last-writer-wins. |
| `AC-DUR-01` | `STAGING_DURABLE` og `FINAL_DURABLE` lagrer faktisk oppnådd flush/write-through-nivå og kjent begrensning. |
| `AC-DUR-02` | GUI/rapport bruker aldri «garantert fysisk lagret» når endepunktet bare har bekreftet OS-/serverforespørsel eller nivået er ukjent. |
| `AC-OBS-01` | Commit, lease, versjonering, karantene, konflikt, attempt, recovery og feil har korrelert revisjonsspor. |
| `AC-SAF-12` | `.mediasync` klassifiseres før ekskludering, oppretting, reparasjon eller adoption; ukjent ikke-tom mappe, case-alias, nyere schema eller korrupt markør gir ingen stille mutasjon eller datautelatelse. |
| `AC-SAF-13` | Hver målmutasjon revaliderer owner, `ownership_epoch`, global lock, lokal lease/token og marker; gammel epoke eller fremmed owner avvises før sideeffekt. |
| `AC-SAF-14` | Kildeprecondition inkluderer type, reparse-tag, parent/path/case/identity; en `SourceReadGuard` eller eksplisitt post-transfer current hash lukker kilde-TOCTOU etter dokumentert endepunktpolicy. |
| `AC-REC-10` | Katalogoppretting, katalogmetadata, karantene og restore har egne journalførte, idempotente tilstandsmaskiner og behandler fil/katalog-typekonflikt som blokkering. |
| `AC-REC-11` | Staging-, version- og quarantinepayloads ligger som korte, checksummede managed objects med manifest; recovery/restore rekonstruerer logisk sti uten å stole på fysisk objektsti. |
| `AC-VER-01` | Transferstatus, assurance og durability lagres og reduseres som tre separate akser. |
| `AC-VER-02` | GUI og audit kan ikke vise `CONTENT_HASH_VERIFIED` når bare manifest eller metadata er kontrollert. |
| `AC-VER-03` | Durabilityresultatet oppgir faktisk bekreftet nivå og lover ikke lokal medie- eller fjernlagringsvarighet utover endepunktets dokumenterte bevis. |

### 22.3 Synkronisering og data

| ID | Akseptansekriterium |
|---|---|
| `AC-SYNC-01` | Brukeren kan opprette `multi_target_backup` med én kilde og opptil tre mål. |
| `AC-SYNC-02` | Fler-målsjobb tillater bare Oppdater/Speil kilde → mål. |
| `AC-SYNC-03` | Reverse update, reverse mirror og toveis finnes bare i `pair_sync`. |
| `AC-SYNC-04` | Kilden skannes én gang og snapshot/hash deles mellom tre mål. |
| `AC-SYNC-05` | Planen som vises/godkjennes, er samme forseglet, uforanderlig plan som Engine Host utfører. |
| `AC-SYNC-06` | Canonical plan/checksum er deterministisk ved samme revisjoner, policy og planner-/serializer-versjon. |
| `AC-SYNC-07` | Alle muterende operasjoner har eksplisitt source-, target-, parent-, path- og lease-precondition. |
| `AC-SYNC-08` | Update beholder ekstra målinnhold. |
| `AC-SYNC-09` | Mirror flytter ekstra innhold til karantene etter destruktiv revalidering. |
| `AC-SYNC-10` | Toveiskonflikter bevarer begge filer, og baseline oppdateres bare for avklarte resultater. |
| `AC-SYNC-11` | Første toveiskjøring uten baseline er ikke-destruktiv. |
| `AC-SYNC-12` | Brukeroverstyring oppretter ny avledet plan; original plan endres ikke. |
| `AC-SYNC-13` | Toveisplan bindes til eksakt baseline set/context/generasjon; endret root, identitet, filter, case-/tidssemantikk, metadata-/konfliktpolicy eller planner/schema kan ikke bruke gammel baseline uten bevist ekvivalens. |
| `AC-DB-01` | `ScanBatch` receipt og data committes atomisk; replay av samme sekvens/hash er idempotent, mens samme sekvens med annen hash blokkeres. |
| `AC-DB-02` | Etter snapshot-/planseal er innhold, coverage, metadata og checksum immutable; senere derivater lagres separat og kan ikke endre godkjent input. |
| `AC-DB-03` | Database-retention importerer et checksummet recovery-root-export/high-water gjennom handoff og bruker eksplisitt referansegraf, holds, `retention_pending` og immutable delete manifest; ny reference/high-water før sweep pauser sletting. |
| `AC-DB-04` | Compaction skjer som separat checksummet epoch under quiesce: ny fil verifiseres før same-volume swap, gammel fil bevares for rollback, og krasj kan ikke gi uklar databaseautoritet. |
| `AC-DB-05` | Intern state-backup bruker ett checksummet sett for alle autoritative state stores med high-water/intentbarrier; restore forbi nyere uavklart target-intent blokkeres. |
| `AC-DUP-01` | Bekreftet identitet krever full hash og størrelse. |
| `AC-DUP-02` | Forventede backupreplikaer teller ikke som mulig besparelse. |
| `AC-DUP-03` | Duplikatfunksjonen sletter, flytter eller hardlinker ingenting automatisk. |
| `AC-SYNC-14` | Konfliktnavn materialiseres deterministisk før planseal, er gyldige innen begge endepunkters grenser og gjenbrukes uendret ved restart/retry. |
| `AC-DB-06` | Første jobb-/endepunktrevisjon kan opprettes uten sirkulær FK: stabil entitet → revisjon → head; head kan ikke peke til en annen entitets revisjon. |
| `AC-DB-07` | Negative wrong-parent-tester beviser composite FK/constraint for endpoint, analysis, snapshot, plan, operation, run, filter og baseline. |
| `AC-DUP-04` | Flere stier til samme filobjekt klassifiseres som `SAME_FILE_MULTIPLE_PATHS`, teller ikke som mulig spart plass og blir ikke automatisk gjenskapt som hardlinks på mål. |

### 22.4 Endepunkter, filsystem og metadata

| ID | Akseptansekriterium |
|---|---|
| `AC-END-01` | Lokal disk, USB, mapped drive og SMB/UNC støttes. |
| `AC-END-02` | Mapped drive lagres som UNC når mulig. |
| `AC-END-03` | Maksimal filstørrelse, komponent-/stilengde og metadataportabilitet kontrolleres før Robocopy. |
| `AC-END-04` | Lange stier og Unicode fungerer eller blokkeres presist og ikke-destruktivt. |
| `AC-END-05` | Case-kolliderende poster beholdes alle i snapshot og vises som blokkering. |
| `AC-END-06` | En stor fil som målfilsystemet ikke støtter, avvises i analysen. |
| `AC-END-07` | Feil disk/share/root oppdages før brukerfil endres. |
| `AC-END-08` | Writable endpoint probe skjer bare i kontrollområdet og produserer immutable kapabilitetsrevisjon. |
| `AC-END-09` | SMB-endepunkt uten pålitelig eksklusiv kontrollock kan analyseres read-only, men automatiske writes blokkeres som standard. |
| `AC-END-10` | Live snapshot oppgir coverage/volatilitet ærlig og markedsføres ikke som punkt-i-tid-snapshot uten VSS. |
| `AC-META-01` | `birthtime_ns` brukes for Windows-opprettelsestid; `ctime_ns` brukes ikke som slik. |
| `AC-META-02` | Named streams og andre egenskaper bevares/verifiseres eller blokkeres/advares eksplisitt etter policy. |
| `AC-END-11` | Case-sensitivitet, evidens og case-context hash persisteres per katalog; endring etter analyse gjør planen stale og blokkerer berørt mutasjon. |
| `AC-END-12` | En gyldig finalsti nær endepunktets grense kan stages, versjoneres, settes i karantene og gjenopprettes via kort objektsti selv når en speilet intern sti ville vært for lang. |
| `AC-END-13` | Uten pålitelig global endpointlock er eneste mulige mutasjon bevist `COPY_NEW_ONLY_NO_REPLACE`; replace, metadataendring, karantene, speiling, toveis og automatikk blokkeres. |
| `AC-END-14` | Kontrollområdet er globalt for marker/lock og namespacet per installasjon for objekter/recovery; én installasjon rydder aldri en annens namespace automatisk. |

### 22.5 Robocopy, ytelse og ressursbruk

| ID | Akseptansekriterium |
|---|---|
| `AC-ROBO-01` | Robocopy startes via absolutt validert systemsti, `shell=False`, minimalt miljø, begrenset handle inheritance og skjult vindu. |
| `AC-ROBO-02` | Hver batch har immutable manifest/hash og unik staging-/loggrot. |
| `AC-ROBO-03` | Etter transfer enumereres staging, og ekstra/manglende/reparse-innhold blokkerer commit. |
| `AC-ROBO-07` | Transferadapteren kan bare skrive til den tildelte stagingroten og mottar aldri final root eller `MutationPermit`; samme regel verifiseres med architecture- og integrationtest. |
| `AC-ROBO-04` | Returkoder 0–7 og 8+ tolkes korrekt, men ingen kode omgår stagingverifisering. |
| `AC-ROBO-05` | `/MT`, prosessantall og aktive mål reguleres som separate parametere. |
| `AC-ROBO-06` | Robocopy startes med sikkert working directory/DLL-søk, minimalt Unicode-miljø, eksplisitt handleliste og uten arvede handles som kan utvide tilgang eller holde ressurser levende. |
| `AC-PERF-01` | Scanner, planner, IPC, GUI-modeller og transferpipeline har avgrenset minne/køer. |
| `AC-PERF-02` | Én million poster materialiseres ikke som én Python-/Qt-objektgraf. |
| `AC-PERF-03` | Storfil- og småfilporter mot direkte Robocopy består på referanseoppsettet. |
| `AC-PERF-04` | GUI-, IPC- og appstartporter består. |
| `AC-PERF-05` | Staging på målvolum gir normalt rename/replace uten ny payloadkopi. |
| `AC-PERF-06` | Diskplassberegningen bruker peak staging og dobbelteller ikke same-volume version/quarantine. |
| `AC-PERF-07` | Scheduler holder ingen databasetransaksjon mens den venter på tokens/leases, og fairness hindrer permanent sulting. |
| `AC-ROBO-08` | Robocopybanen løses via `GetSystemDirectoryW` og valideres; én kanonisk Windows-argumentbygger består round-trip for Unicode, mellomrom, UNC, trailing backslash, tomt argument og grensenær kommandolinje. |
| `AC-PERF-08` | Lokal AppData-state har vekstestimat, soft/hard quota og preflight; `SQLITE_FULL` stopper nye analyser/transfers uten å forkaste autoritativ recoverytilstand. |
| `AC-FILTER-01` | Glob er standard; avansert regex har mønster-/tids-/ressursbudsjett, kan kanselleres og kan ikke låse Engine Host eller bruke ubegrenset CPU. |

### 22.6 GUI og effektiv arbeidsflyt

| ID | Akseptansekriterium |
|---|---|
| `AC-UX-01` | Alle hovedfunksjoner kan brukes uten offentlig CLI. |
| `AC-UX-02` | Toppnivåene er Oversikt, Jobber, Historikk og Innstillinger. |
| `AC-UX-03` | Standard backup kan opprettes i høyst fire steg uten tekniske valg. |
| `AC-UX-04` | Kilde, retning, mål, modus og konsekvens kan forstås uten farge alene. |
| `AC-UX-05` | Det finnes én anbefalt primærhandling per handlingsområde. |
| `AC-UX-06` | Ny jobb → kontroll → kjøring → resultat → historikk kan gjennomføres med tastatur. |
| `AC-UX-07` | Hvert mål viser tilgjengelighet og eksakt siste vellykkede tidspunkt. |
| `AC-UX-08` | `Oppdatert` brukes bare når komplett, fortsatt gyldig analyse beviser null ventende endringer; watcher kan bare ugyldiggjøre. |
| `AC-UX-09` | En delvis kjøring vises som `N av M mål` og kan ikke forveksles med full suksess. |
| `AC-UX-10` | En etablert trygg backup kan startes fra dashboard med én bevisst handling. |
| `AC-UX-11` | Plan med erstatning, konflikt, karantene, blokkering eller terskelavvik stopper hurtigflyten. |
| `AC-UX-12` | Kontrollvisningen skjuler uendrede filer som standard og fremhever det som krever oppmerksomhet. |
| `AC-UX-13` | GUI viser fase, byte, hastighet, ETA, målstatus, aktiv fil og problemer uten falsk fremdrift. |
| `AC-UX-14` | Fullføringsoppsummeringen skiller fullført, delvis og `handling nødvendig`, med anbefalt neste handling. |
| `AC-UX-15` | Nytt forsøk kan begrenses til mislykket mål eller elementer. |
| `AC-UX-16` | Tom-, laste-, offline-, blokkert-, delvis-, feil-, reconnect- og recoverytilstander finnes. |
| `AC-UX-17` | Lys/mørk/system, norsk/engelsk og 100–200 % DPI fungerer uten kritisk klipping. |
| `AC-UX-18` | Tillatte semantiske fargepar består kontrasttest. |
| `AC-UX-19` | Duplikater og recovery åpnes kontekstuelt uten kompleks toppnavigasjon. |
| `AC-UX-20` | Normale sider eksponerer ikke intern arkitekturterminologi som forutsetning for standardbruk. |
| `AC-UX-21` | GUI-tråden utfører ingen fil-I/O, mediedekoding, stor SQL eller direkte databasewrite. |
| `AC-UX-22` | Oppgavetestene i §8.30 er dokumentert uten sikkerhetskritisk misforståelse. |
| `AC-UX-23` | Kontroll uten utførbare endringer viser `Ingen endringer` og oppretter ingen tom run. |
| `AC-UX-24` | GUI skiller konfigurerte mål fra bekreftet uavhengige lagringsenheter. |
| `AC-UX-25` | Et eksplisitt valg om tilgjengelige mål gir ingen redundant dialog, men uventet målutfall stopper hurtigflyten. |
| `AC-UX-26` | Historikken skiller kontroller fra backupkjøringer og viser control-only-resultater uten fiktiv run. |
| `AC-UX-27` | Jobbredigering bruker eksplisitt lagring/ny revisjon og kan ikke endre aktiv run. |
| `AC-UX-28` | Aktivitet, oppmerksomhet og per-mål-ferskhet er separate dimensjoner. |
| `AC-UX-29` | Jobb kan arkiveres/reaktiveres uten brukerfilendring eller historikktap; reaktivering krever ny kontroll. |
| `AC-UX-30` | GUI som mister Engine Host viser reconnect/incompatibility ærlig og påstår aldri at en ubekreftet command lyktes. |

### 22.7 Automatisering, oppgradering og utgivelse

| ID | Akseptansekriterium |
|---|---|
| `AC-AUTO-01` | Manuell, tid, pålogging, lokal oppstart, disktilkobling og filendring støttes som idempotente triggere. |
| `AC-AUTO-02` | NAS/UNC-jobb bruker ikke S4U og forklarer påloggings-/nettverkskrav. |
| `AC-AUTO-03` | MediaSync lagrer ikke Windows-kontopassord eller reversible credentials. |
| `AC-AUTO-04` | To samtidige/dupliserte triggere starter ikke samme logiske jobb dobbelt. |
| `AC-AUTO-05` | Automatikkpolicy og utsatte handlinger inngår i planseal, checksum og audit. |
| `AC-AUTO-06` | Task Scheduler-definisjoner avstemmes mot desired state; ukjent eierskap eller binærdrift slettes ikke blindt. |
| `AC-AUTO-07` | Systemstatusfeltet er ren IPC-klient og kan avsluttes uten å stoppe aktiv Engine Host/run. |
| `AC-AUTO-08` | Planlagt bootstrap uten eksisterende host blir selv Engine Host eller fullfører eksplisitt ownership/lifetime-handshake; fire-and-forget host-child er forbudt. |
| `AC-AUTO-09` | Task Scheduler desired state angir multiple-instances- og stop/execution-time-policy slik at en task-eid host ikke gjør at senere forekomster ignoreres eller blindt drepes. |
| `AC-REL-01` | Programmet fungerer uten internett. |
| `AC-REL-02` | CSV-/JSON-eksport og jobbeksport uten credentials fungerer. |
| `AC-REL-03` | Installer/oppgradering bevarer revisjoner, planer, baseline og recoverytilstand. |
| `AC-REL-04` | Oppgradering med aktiv run quiescer sikkert og etterlater ingen foreldreløs worker. |
| `AC-REL-05` | Ren støttet Windows-maskin kan installere og kjøre utgivelsesbygget. |
| `AC-REL-06` | Nyere schema/protokoll kan ikke nedgraderes eller åpnes writable av eldre build. |
| `AC-REL-07` | Kravsporingsmatrisen viser bestått test/gate eller godkjent avvik for alle utgivelseskritiske krav. |
| `AC-REL-08` | Oppgraderings-/migrasjonstest dreper prosessen etter backup, etter delvis migrering av valgt state-sett og før committed epoch; hver restart ender i kompatibel fullt migrert eller fullt gjenopprettet tilstand. |
| `AC-REL-09` | Release-testen beviser at databaser ikke plasseres på NAS/SMB/flyttbart medium, heller ikke i installasjonsfri mappepakke, og at en unsupported plassering blokkeres med trygg flytteveiledning. |
| `AC-AUTO-10` | Live claims og timeouts bruker monoton klokke og owner instance; wall-clock-hopp eller restart kan ikke alene stjele levende arbeid eller holde stale arbeid evig. |
| `AC-AUTO-11` | Automatisk kjøring er av som standard for endpoint uten pålitelig lock; begrenset no-overwrite-modus krever eksplisitt brukeraksept og synlig sikkerhetsnivå. |
| `AC-REL-10` | Utgivelsesrepositoryet inneholder kort `AGENTS.md`, milepælsrettede fagfiler og validerte kontrakter; den konsoliderte masterfilen er referanse, ikke eneste operative prompt. |
