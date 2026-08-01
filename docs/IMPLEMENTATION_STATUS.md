# Implementeringsstatus

Update 2026-08-01: `META-001` now has executable Windows creation-time
evidence. The local scanner reads `FILETIME` creation time through
`GetFileInformationByHandle`; it never substitutes `st_ctime` and leaves the
scan unsealed when creation time is unavailable. Snapshot schema 3 binds
`birthtime_ns` into canonical checksums while preserving schema 1/2
verification. Catalog migration 49 persists the non-negative value on
`file_entries` and rejects sealing schema-3 file/directory evidence with a
missing value. Bounded snapshot reads and initial-plan loading round-trip the
same field. Native scanner, legacy checksum, SQLite migration/seal, backup,
IPC, contract and negative-fallback tests cover the boundary.

Update 2026-08-01: empty-directory quarantine is now a retained, user-visible
recovery object instead of short-lived cleanup data. Catalog migration 48 adds
the immutable `EMPTY_DIRECTORY_QUARANTINE` role beside
`OLD_TARGET_VERSION`. A successful file-over-empty-directory commit keeps the
canonical self-hashed empty-folder object for 30 days, registers it in the same
hold/expiry catalog, and exposes it in History as an explicitly labeled
recovery item. The existing confirmed protect, restore and undo commands now
restore the empty directory under a fresh endpoint lease while preserving the
current file as rollback evidence; undo full-hash verifies and restores that
file. Expiry verifies the empty directory and manifest before journaled
deletion. SQLite integration covers commit-to-catalog, restore, undo, expiry
and immutable role binding. Norwegian/English Qt coverage proves the type label,
language switch, real actions and zero horizontal clipping at 900x560.

Update 2026-08-01: History now provides a complete protected historical-file
restore path. The bounded `QUERY_RETAINED_VERSIONS` read model shows protection
and restore state in Norwegian and English. The first confirmed action creates
the durable `RESTORE_REQUESTED` hold; the second confirmed
`RESTORE_RETAINED_VERSION` command atomically creates catalog migration 46's
restore operation and append-only event chain through the normal command
receipt transaction. The maintenance worker acquires the exact endpoint lease,
validates the retained manifest/payload and current final path, journals the
current fingerprint, writes and verifies a durable 30-day rollback object, then
atomically replaces and full-hash verifies the final file. Retries after either
filesystem-before-journal crash window are idempotent. Manifest drift, payload
drift, path/reparse mismatch, stale permit or changed final bytes fail closed;
the live file and hold remain untouched/protected. The hold is released only
after terminal final verification. Compact GUI tests cover both confirmed
actions, language switching and no horizontal clipping at 900x560. Automated
expiry and user-visible undo are now implemented by catalog migration 47.
`UNDO_RETAINED_VERSION_RESTORE` uses the normal durable command receipt,
requires explicit confirmation and a fresh endpoint lease, and replaces the
restored final only while its full fingerprint still matches the completed
restore. The rollback object remains available until its original 30-day due
time even after undo. Expiry verifies the exact payload/manifest pair, records
intent before deletion and resumes after either full or payload-only deletion.
The History action and lifecycle status are localized in Norwegian and English;
compact GUI coverage includes restore, undo, language switching and bounded
text/path rendering at 900x560.

Update 2026-08-01: PATH-001 catalog-recorded cleanup now covers every managed
staging object, not only created-directory markers and empty-directory
quarantine. The executor keeps the live endpoint permit until cleanup validates
the canonical manifest against the journal, verifies any remaining file hash or
directory marker, removes the short staging payload and manifest, and journals
`CLEANED`. Missing/tampered evidence fails closed; an already-removed pair is an
idempotent retry. Version payloads, manifests and empty-directory quarantine
remain intact for retention, while created-directory recovery markers keep
their existing post-catalog cleanup. Time-based local recovery-object
expiration is now implemented
through catalog migrations 44-45 and recovery migration 11: canonical version
manifests, immutable due plans, recovery/hold/archive checks, fresh endpoint
permits, append-only delete events and crash-safe resume. Non-local endpoint
evidence and the broader catalog-retention graph remain pending.

Update 2026-08-01: PATH-001 production final publication now requires the
canonical short-object staging manifest before any new file/directory publish
or destructive old-target preservation. The resolver parses the exact canonical
JSON, verifies its SHA-256 self-hash, and binds object/run/target, endpoint
revision and generation, logical final path, operation kind, and verified
content hash to the live mutation permit. Missing, altered, or differently
bound evidence fails closed before the final tree changes. Directory replay
after a completed rename remains idempotent because its manifest is retained
beside the consumed staging payload. Catalog-recorded staging cleanup is covered
by the newer update above.

Oppdatering 2026-08-01: Etablerte backupjobber kan nå arkiveres og reaktiveres
trygt fra **Jobber**. Arkivering krever eksplisitt bekreftelse, avvises under
aktiv kjøring eller uavklart recovery, deaktiverer alle triggere, skjuler jobben
fra standardoversikten og pauser automatisk retensjonsopprydding uten å endre
brukerfiler eller slette historikk. **Aktiv**/**Arkivert**-filteret og handlingen
er lokalisert på norsk og engelsk. Reaktivering holder triggere avslått og køer
alltid en ny full kontroll uten automatisk backupstart. Catalog migration 43
lagrer optimistisk livssyklustilstand og immutable hendelsesbevis; start,
kontroll, målregistrering, kontrollert takeover og analysearbeidere avviser
arkiverte jobber. SQLite-/IPC-testene beviser restart, idempotent replay,
historikkbevaring og filterisolasjon, mens Qt-testene dekker reell handling,
språkbytte og null horisontal tekstklipping ved 900×560.

Oppdatering 2026-08-01: Kontrollert overtakelse av et lokalt mål med gyldig
fremmed eier er nå implementert. **Oversikt** og **Jobber** viser en eksplisitt
lokalisert handling; dialogen viser gammel eier, eierskapsepoke, recovery-status,
at tidligere kontrollnamespace beholdes og at full analyse kreves. Bekreftelsen
sendes som en streng, revisjonsbundet og idempotent kommando. Engine Host tar den
virkelige `mutation.lock`, klassifiserer markør og recovery på nytt under låsen,
avviser aktiv mutasjon eller uavklart recovery, skriver immutable takeover-intent
og neste ownership-record, og publiserer deretter en checksummet markør for lokal
eier med eksakt `old_epoch + 1`. Catalog migration 42 lagrer restartbar intent og
immutable bevis, appender nye endpoint-/jobbrevisjoner, ugyldiggjør gamle planer
og køer obligatorisk full analyse uten automatisk start. Avbrudd før og etter
markørpublisering fortsetter deterministisk ved startup; gammel owner/epoch
avvises som stale. Qt-bevis ved 900×560 dekker norsk/engelsk språkbytte, lang
målsti, dialogens høyde-for-bredde og klikkbare kontroller uten tekstklipping.
To-maskin SMB-lab og writable SMB-scope er fortsatt eksplisitt utsatt.

Oppdatering 2026-08-01: Aktive jobber som ble opprettet før catalog migration
30 kan nå repareres uten automatisk eller skjult skriving til brukerens mål.
Når alle målbindingene i den eksakte aktive jobbrevisjonen er
`REGISTRATION_PENDING`, viser **Oversikt** og **Jobber** den eksplisitte
handlingen **Registrer mål**. GUI sender den revisjonsbundne og idempotente
`REGISTER_WRITABLE_TARGETS`-kommandoen på en separat worker, slik at navigasjon
og språkbytte forblir responsive. Engine Host lagrer command receipt, bruker
den eksisterende restartbare registreringsintenten, oppretter kontrollområde
og probe-evidence, appender endpoint-/jobbrevisjon og kjører deretter
klassifisering, snapshots og første plan på nytt. Stale revisjon, blandet
registreringsstate og fremmed/korrupt kontrollstate avvises uten takeover.
Kompakt GUI-bevis ved 900×560 dekker dobbelklikk, lang målsti, språkbytte og
ingen horisontal klipping. Kontrollert lokal fremmed-overtakelse er dekket av
den nyere oppdateringen over.

Oppdatering 2026-08-01: Multi-target backup er nå bevist gjennom hele den
lokale executorgrensen, ikke bare i planleggingen. En forseglet plan binder to
separate `COPY_NEW`-operasjoner til `target-a` og `target-b`; run-start
materialiserer ett mål per endpoint med egne operasjons-/bytetall. Den bounded
executorcycle-en tar separate endpointleaser og `MutationPermit`-er, planlegger
bare operasjonen som er bundet til gjeldende run-target, utfører staging,
journal, final commit, operasjonsaudit og cataloghandoff mot riktig rot og
frigir hver lease separat. Integrasjonen verifiserer identiske kildebytes på
begge mål, to separate terminale operation outcomes/handoffs, to vellykkede
targettilstander og at runnen først blir `COMPLETED` etter begge. Dette lukker
den tidligere markerte multi-target operation-binding-gapen. Kontrollert lokal
fremmed-overtakelse er dekket av den nyere oppdateringen over.

Oppdatering 2026-08-01: Rekonstruerbare GUI-spørringer har nå faktisk
transportkansellering i tillegg til den eksisterende logical cancellation og
stale-result-sperren. Hver query får et eget kanselleringssignal; same-key
erstatning, eksplisitt cancel, cancel-all og window-close signaliserer også en
allerede kjørende query. Workerens `EngineClient` binder signalet til
Win32-klienten, som avbryter den eksakte overlapped pipe-operasjonen med
`CancelIoEx` under request, response og acknowledgment. Overlapped venting
sjekker signalet minst hver 25 ms, mens pipe-open fortsatt har maksimalt 250 ms
busy/missing-pipe-intervall. En avbrutt transport forkaster klienten før den
nyere queryen starter med en fersk tilkobling; sent resultat er fortsatt bundet
til querytokenet. En server-side read kan fullføre etter at klienten har koblet
fra, men den holder ikke lenger GUI-workerens eneste plass. Muterende kommandoer
beholder sin separate durable policy og kanselleres ikke etter submission.
Adversarial Qt-bevis avbryter en aktiv same-key-query uten testutløsing, og en
ekte Windows named-pipe-integrasjon avbryter en blokkert statusrespons på under
ett sekund.

Oppdatering 2026-08-01: **Endringer**, **Historikk**-tidslinjen og historikkens
**Filresultater** prefetchet nå høyst én neste side per arbeidsflate. Hver cache
er bundet til eksakt plan, aktivitet, run, filter, side, keyset-cursor og
generasjon; navigasjon bakover, refresh, filter-/jobb-/aktivitetsskifte og
window-close ugyldiggjør resultatet. Spekulative reads bruker én separat,
serialisert worker-client med høyst én aktiv og én latest-wins pending request,
og kan derfor aldri ligge foran en brukerbestilt read i den ordinære querykøen.
Ved cachetreff bytter **Neste** side uten ny IPC-venting
og starter eventuelt ett nytt lookahead; ved miss går den ordinære bounded
bakgrunnsreaden som før. GUI-et materialiserer fortsatt bare gjeldende side i
Qt-modellen, mens hver arbeidsflate kan holde høyst én plain view-model-side i
prefetchcache. Testene dekker keyset og eldre offset-Host, cacheerstatning,
Changes, tidslinje og filresultater samt en blokkert prefetch mens en nyere
foreground-filterread fullfører og det gamle resultatet forkastes.

Oppdatering 2026-08-01: **Historikk**-tidslinjen bruker nå en versjonert,
typet keyset-cursor med total rekkefølge over
`(started_utc, activity_kind, activity_id)` i stedet for produksjons-`OFFSET`.
GUI-et holder en avgrenset cursorstack for tilbakeknappen, validerer stale
bakgrunnssvar mot eksakt cursor og oppdager automatisk en eldre Engine Host som
ikke sender `next_cursor`; bare den kompatibilitetsbanen bruker offset, med hard
grense 10 000. Catalog schema 41 legger globale og jobbfiltrerte
expression-indekser på begge kontrollkildene samt analyseoppslaget; runs bruker
de eksisterende recent-run-indeksene. SQLite-testene beviser identiske
tidsstempler, innsetting mellom sider, faktiske index seeks og varm side ved en
100 000-raders runkatalog. Kontrollert bakgrunnsprefetch er levert i
oppdateringen over.

Oppdatering 2026-08-01: De tre uavgrensede resultatsflatene **Endringer**,
**Historikk**-tidslinjen og historikkens **Filresultater** bruker nå en delt
`QAbstractTableModel`/`QTableView`-grense med Qt-delegater i stedet for
`QListWidgetItem`-objekter. Modellen holder bare gjeldende side, har en hard
cachegrense og avviser duplikate radidentiteter, feil kolonneantall og
overskridelse. Plan-/filresultatsider er økt til 200 rader over eksisterende
keyset-cursor, mens tidslinjens nåværende IPC-side fortsatt er avgrenset til 25.
Stier elideres i cellen og beholdes komplett i tooltip/detalj; radvalg bindes
fortsatt til immutable activity-/operation-ID. En simulert millionradkilde
beviser at første og siste side bare holder 200 plain row records og ingen
per-rad `QObject`/`QWidget`-graf. Reelle Qt-klikk bevarer filter, paging,
filaudit/retry, stale-query-sperre, norsk/engelsk og null horisontal overflow
ved 900×560. Tidslinjens keyset-migrasjon og kontrollert bakgrunnsprefetch er
levert i oppdateringene over.

Oppdatering 2026-08-01: Alle ikke-rekonstruerbare GUI-kommandoer kjører nå på
en egen serialisert command-worker med en separat, gjenbrukt Engine-client.
Oppretting/målregistrering, ny kontroll, run-start og pause/fortsett/stopp har
høyst én in-flight submission og ingen lossy ventekø; en ny handling avvises i
GUI-et til den aktive kommandoen har et resultat. Request- og idempotency-ID
opprettes før workerstart og beholdes ved et usikkert transportutfall, slik at
eksakt samme handling kan prøves på nytt uten duplikateffekter. Et definitivt
resultat brukes til autoritativ refresh selv om brukeren har navigert eller
valgt en annen jobb, men resultatet kan ikke male gammel jobb/run inn i den nye
konteksten. Lukking av vinduet kansellerer ikke en allerede sendt mutasjon, men
forkaster sen UI-callback. Filretryens tidligere synkrone jobbdetalj-read er
fjernet; Engine Host validerer jobben og retry-scope autoritativt. Adversarial
Qt-bevis blokkerer hver kommandofamilie, dobbeltklikker, bytter side, språk og
jobb og verifiserer off-UI-tråd, én submission, stale-result-sperre og eksakt
idempotency-gjenbruk etter simulert timeout.

Oppdatering 2026-08-01: Produktvinduet vises nå før første live Engine-read, og
alle rutinemessige rekonstruerbare GUI-reads kjører utenfor GUI-tråden når
Engine-client-factory er tilgjengelig. Dette omfatter status, Jobber-oversikt og
-detalj, Aktivitet, Endringer, Historikkens tidslinje/filresultater/operasjonsaudit,
plan-/endpoint-/snapshot-/katalogpreview samt run-progress og analysepolling. Den
sentrale bounded kontrolleren gjenbruker én dedikert worker-client, kjører høyst
én query om gangen, holder maksimalt fire ventende querynøkler og beholder
serialisering også når en resultatcallback starter neste avhengige read. En nyere
query erstatter ventende arbeid med samme nøkkel, og stale resultat forkastes mot
eksakt jobb, run, plan, aktivitet, operasjon, filter, side og cursor. Sidepilene
og resultatlisten låses bare når deres egne data er stale; filtre, andre jobber,
språkvelger og navigasjon forblir responsive. Timerpolling tillater bare én
in-flight read og kan derfor ikke bygge kø mot en treg host. `UiUpdateCoalescer`
holder maksimalt 16 kanaler og anvender bare siste snapshot per kanal ved høyst
4 Hz. Adversarial GUI-bevis blokkerer status, jobbdetalj, jobbside, progress,
analyse, Endringer, Historikk og filaudit med vilje, utfører reelle klikk mens de
venter og verifiserer latest-wins, én aktiv worker, én client-factory-resolusjon,
close-cancellation, bounded overflow, norsk/engelsk og 900×560 uten horisontal
clipping. Virtuelle resultattabeller, historikktidslinjens keyset-migrasjon og
kontrollert prefetch er levert i oppdateringene over.

Oppdatering 2026-07-31: **Historikk** kan nå prøve én valgt uferdig fil på
nytt fra den bounded, paginerte filresultatvisningen. Handlingen vises bare
for varige `SKIPPED`, `CANCELLED` eller `RECOVERY_REQUIRED`-outcomes, kjører
alltid en ny kontroll uten automatisk start og sender source-run,
source-operation og endpoint som eksplisitt retry-scope. Application-laget
verifiserer terminal kilde, retrybart mål, source-plan/checksum og auditbinding,
avviser vellykkede eller forsvunne operasjoner, mapper endpoint + relativ sti
til nøyaktig én operasjon i den ferske planen og inkluderer bare nødvendige
plandependenser. Runnen lagrer source- og fresh-operation-ID-er, scoped tall og
opprinnelig lineage; executorplanlegging validerer og håndhever det varige
scope-et før recovery-operasjoner opprettes. Norsk/engelsk, reell klikkflyt og
900×560 uten horisontal overflow er dekket. Sammen med målretry fullfører dette
begge scope-variantene i `AC-UX-15` for lokal Alpha.

Oppdatering 2026-07-31: Et terminalt resultat i **Jobber** kan nå prøve ett
mislykket, avbrutt eller blokkert mål på nytt uten å kjøre allerede vellykkede
mål. Brukeren velger målet i en lokalisert resultatkontroll; GUI-et bestiller
først en ny kontroll med automatisk start avslått og sender deretter
`START_RUN` med bare valgt endpoint og opprinnelig run som lineage. Backend
krever en ny forseglet plan, avviser vellykkede/ukjente mål og ikke-terminale
kilder, beregner run-tall bare for valgt mål og gjenbruker opprinnelig
`logical_run_group_id` med varig `resumed_from_run_id`. Et blokkert funn på et
annet eksplisitt bundet mål blokkerer ikke det valgte trygge målet. Norsk,
engelsk, reell klikkflyt og 900×560 uten horisontal overflow er dekket.
Elementspesifikt manuelt retry er levert i oppdateringen over.

Oppdatering 2026-07-31: **Jobber** har nå en egen, lokalisert
**Endringer**-arbeidsflate for den valgte jobbens gjeldende forseglede plan.
Den viser immutable beslutningstall og oppmerksomhetsbanner, filtrerer
autoritativt på mål og risikonivå i SQLite/IPC, blar med bounded keyset-cursor
og materialiserer høyst 25 rader i GUI-et. Valgt endring viser beslutning,
operasjonstype, mål, sti, rå årsakskode, målprecondition og planlagte byte.
Norsk/engelsk språkbytte bevarer filtre og valgt detalj; to sider, to mål,
safe/review/high/blocked og 900×560 uten horisontal overflow er dekket.
Virtuelle resultattabeller, historikktidslinjens keyset-migrasjon og kontrollert
sideprefetch er levert i oppdateringene over. Bredere faktisk kansellering av
allerede kjørende bakgrunnsspørringer er levert i oppdateringen over.

Oppdatering 2026-07-31: Terminale resultater i **Jobber** viser nå et stabilt,
lokalisert sammendrag av hvor mange mål som ble fullført, for eksempel
**2 av 3 mål fullført**. `SUCCEEDED` og `SUCCEEDED_WITH_WARNINGS` teller som
fullførte mål; feil, avbrudd, sikkerhetsblokkering og recoverybehov gjør ikke
det. Aktive kjøringer påvirkes ikke, og eldre terminale snapshots uten mål
beholder det eksisterende resultatet uten en misvisende nulltelling. GUI-
regresjonene dekker omstart, suksess, varsel, delvis feil, norsk, engelsk og
legacy-snapshot.

Oppdatering 2026-07-31: Den vedvarende desktop-Engine Hosten bruker nå
supervisert Robocopy som standard stagingbackend. Produktinngangen,
desktop-launchplanen, den publiserte same-user hosten, direkte Engine Host-
komposisjon og runtimebyggeren velger alle `robocopy`; `local-file` finnes bare
som en eksplisitt utviklings-/testoverride og forwardes uten å falle tilbake til
produksjonsdefaulten. En Windows-integrasjon driver en ekte planlagt fil gjennom
lease, recoveryjournal, Job Object-contained Robocopy, manifestbundet inbox,
staginghash, final commit, cataloghandoff, operasjonsaudit og fullført run. Den
eksisterende live-testen beviser fortsatt fatal missing-source-håndtering og tom
handle-arv. Pakkesmoken krever nå at den pakkede Engine Hosten annonserer
`run_executor_staging_backend=robocopy`, i tillegg til host/GUI- og Task
Scheduler-rundturen.

Oppdatering 2026-07-31: **Jobber** gjenoppretter nå den siste terminale
backupkjøringens autoritative `QUERY_RUN_PROGRESS`-snapshot for valgt jobb etter
GUI- eller hostrestart. Sekvenscursoren gjenbrukes bare for samme `run_id`, og
snapshot fra en annen kjøring eller jobb vises ikke. Resultatkortet skiller
fullført, fullført med varsler, delvis feil, feil, avbrutt, sikkerhetsblokkert og
recoverypåkrevd, viser operasjoner, fullførte byte og varsel-/feiltall, samt en
lokalisert neste handling. Terminale resultater viser ikke aktiv fil,
pause/stopp eller beregning av ETA. To ferske GUI-vinduer mot samme lagrede run,
jobbskifte og norsk/engelsk delresultat er dekket av GUI-testene.

Oppdatering 2026-07-31: Aktivitetens bounded run-read model beregner nå eksakt
`last_success_utc` per jobb og mål fra terminale `SUCCEEDED`- og
`SUCCEEDED_WITH_WARNINGS`-mål. En nyere aktiv, feilet eller avbrutt kjøring
overskriver derfor ikke det siste vellykkede tidspunktet. Dashboardets
**Ferskhet per mål** viser én wrapbar linje per mål med separat ferskhetstilstand
og lokalisert dato/tid, eller eksplisitt **Ingen vellykket backup**. SQLite-
integrasjonen beviser historisk suksess under en nyere revalidering, og GUI-
dekningen beviser tre mål ved 900×560, språkbytte og null horisontal overflow.
Den separate **Neste handling**-dimensjonen beholder jobbanbefalingen og viser
i tillegg én målidentifisert, lokalisert handling bare for mål som venter,
kontrolleres, kopierer, er pauset, frakoblet, avbrutt, har varsler eller er
blokkert. Tre mål med ulike feil-/ventetilstander reflowes i samme kompakte
norsk/engelsk GUI-bevis uten å skjule en handling.

Oppdatering 2026-07-31: Catalog schema 40 materialiserer nå durable
`run_attempts`, immutable `operation_attempts` og immutable
`operation_outcomes` fra den hashkjedede recoveryjournalen. Hvert klassifisert
filforsøk beholder prosess-, lease-, epoch-, fencing-, source-guard-, transfer-,
assurance- og durabilitybevis slik de var ved eventen. Executor avstemmer
journalen idempotent før intent/terminalisering, og startup-resume gjør samme
avstemming før et catalog-recorded mål kan fullføres. Et krasj mellom recovery-
event og catalog-write repareres derfor i neste bounded pass. Parent-scope-FK-er
binder radene til samme run, plan, mål og planoperasjon. Bounded
`QUERY_OPERATION_AUDIT` eksponerer målsti, forsøk og sluttresultat uten at GUI
leser SQLite direkte. **Historikk** viser nå en bounded, keyset-paginert liste
over den valgte backupkjøringens forseglede planoperasjoner. Valg av én fil
henter bare den eksakte auditposten og viser sluttresultat, fullført tid,
transferstatus, overførte byte, assurance, durability, siste feil og opptil 25
forsøk. Et
transient feilforsøk etterfulgt av suksess vises som to separate, tidsstemplete
rader. Kontrollaktiviteter skjuler filseksjonen, språkbytte bevarer valgt fil,
og 900×560-dekning verifiserer at lange filstier ikke gir horisontal clipping.

Oppdatering 2026-07-31: Utilgjengelige og opptatte mål går nå i en varig,
ikke-destruktiv ventetilstand i stedet for å blokkere hele kjøringen. Catalog
schema 39 lagrer immutable `run_target_endpoint_wait_events` med mål,
forsøksnummer, årsak, faktisk backoff og neste tillatte retrytid. Manglende
målrot og en opptatt `mutation.lock` flytter bare det aktuelle målet til
`WAITING_FOR_ENDPOINT` og nullstiller stale leasebevis; marker-, owner- og
identitetsfeil forblir harde sikkerhetsblokker. Retry bruker deterministisk
jitteret eksponentiell backoff fra fem sekunder til maksimalt fem minutter.
Levende deadlines bruker monotonic clock; lagret UTC brukes én gang etter
restart for bounded reconciliation og til visning. Klassifiserte Windows-
nettverksbrudd eller en endpointrot som forsvinner under lokal/Robocopy-transfer
flytter også hele målet til samme ventetilstand med `NETWORK_INTERRUPTED`.
Upublisert temp-/inboxinnhold ryddes, operasjonsfasen og failure count beholdes,
stale lease frigis, og resume krever ny preflight, lease og recovery-rebind. Jobs
viser **Venter på mål** med forsøksnummer og lokal retrytid; tooltip lokaliserer
nettverksårsaken og viser samlet planlagt ventetid. Volume-arrival hints gjenstår.

Oppdatering 2026-07-31: Feil på enkeltfiler har nå en varig, avgrenset
retryflyt. Recovery schema 10 lagrer `staging_failure_count`, faktisk jitteret
backoff og neste tillatte retrytid på operasjonen; samme timing bindes til den
hashkjedede failure-eventen. Levende venting bruker monotonic clock, mens lagret
UTC avstemmes én gang etter restart. Første og andre transientfeil venter omtrent
ett og to sekunder; tredje feil flytter operasjonen til `SKIPPED`. Bare
eksplisitt klassifiserte lokale I/O- og Robocopy-feil prøves på nytt;
precondition-, reparse-, containment- og andre sikkerhetsfeil forblir blokkert.
Mens ett mål venter kan et annet retained mål fortsette, uten at senere
operasjoner på samme mål går forbi den ventende operasjonen. Et ellers ferdig
mål avsluttes som `SUCCEEDED_WITH_WARNINGS`, og run blir
`COMPLETED_WITH_WARNINGS`. Progress schema 4 og Jobs viser neste filforsøk,
lokal retrytid, feilkode og backoff i en tekstflate som reflowes. Nettverksbrudd
bruker fortsatt den separate tidsstyrte retryflyten for hele målet. Forsøkene
materialiseres nå i catalog schema 40 før terminalisering.

Oppdatering 2026-07-31: Kildebytes er nå bundet til analysen som opprettet
planen. Snapshot schema 2 og catalog schema 37 lagrer et stabilt
identitetsfingerprint for hver kildefil. Operation schema 3 forsegler eksakt
snapshotpost, relativ sti, filtype, størrelse og identitet i planchecksumen og
fører samme precondition videre til recovery schema 8. Lokal transfer validerer
identiteten før åpning, holder filhåndtaket gjennom hashlesingen, sammenligner
`fstat` før og etter lesing og kontrollerer kilden igjen etter kopiering.
Robocopy-kjøring kontrollerer samme identitet rett før child-start og etter
child-exit før inboxen kan publiseres. Endring av kilden etter analyse eller
under transfer stopper operasjonen uten å publisere målfilen. Et kildehåndtak
holdt gjennom hele den eksterne Robocopy-prosessen og bredere SMB-fault injection
gjenstår som Windows-spesifikk hardening.

Oppdatering 2026-07-31: Gjentatte backuper skiller nå dokumentert identiske
filer fra reelle endringer. Catalog schema 36 lagrer append-only
`CURRENT_READ_HASH`-evidens for eksakte immutable snapshotposter. Den lokale
leseren reparse- og path-guarder hver fil, beregner full BLAKE3 i avgrensede
chunks og godtar bare evidensen når størrelse og før-/etterfingerprint er
uendret. Planleggeren aggregerer slike identiske kilde-/målpar ut av planen;
en analyse med bare identiske filer blir `NO_CHANGES` og oppretter ingen tom
run. **Kjør backup** lagrer samtidig brukerens startintensjon. Etter fersk
analyse køes run automatisk bare når hele planen består av low-risk
`COPY_NEW`/`CREATE_DIRECTORY` med fraværspreconditions. Erstatning,
typekonflikt, blokkering eller annen review-plan stopper fortsatt før mutasjon.
Runtime-testen beviser komplett sekvens: ny fil analyseres, run køes og
fullføres, neste kontroll hashes på begge sider, returnerer `NO_CHANGES` og
oppretter ingen ny run.

Oppdatering 2026-07-31: En etablert backup kan nå kontrolleres på nytt uten å
gjenbruke den forseglede førstegangsplanen. `CHECK_BACKUP` lagrer en durable,
idempotent køforespørsel i catalog schema 35 og returnerer før filskanning
starter. Etter-IPC-/vedlikeholdspumpen klassifiserer endepunktene på nytt,
oppretter nye immutable snapshots for bare valgt jobb og materialiserer et nytt
immutable planresultat. Avbrutt `RUNNING`-analyse køes på nytt ved primær
Engine Host-start, mens aktiv run blokkerer ny analyse. Jobbdeltaljen og
Historikk eksponerer kø-, kjøre- og terminaltilstand. GUI-ens ene
primærhandling skifter fra **Start backup** til **Kjør backup** etter at en plan
allerede er kjørt, viser **Kontrollerer endringer...** mens Engine Host arbeider
og poller uten å utføre skanning i GUI-tråden. Runtime-integrasjonen oppretter
en tom jobb, legger til en ny kildefil, køer kontrollen og beviser at et nytt
én-operasjonsplan blir gjeldende.

Oppdatering 2026-07-31: Produktinngangen uten argumenter åpner nå den faktiske
desktopapplikasjonen. Launcheren finner og validerer en kompatibel same-user
Engine Host, eller starter én frakoblet lokal host og venter på en akseptert
status før GUI-et åpnes. Kildekjøring og Nuitka-pakket kjøring bruker samme
validerte rolleargumenter. Engine Host fortsetter etter at GUI-vinduet lukkes,
slik at kø og kjøringer ikke eies av vinduets levetid. Samtidige oppstarter
håndterer singleton-kappløpet ved å adoptere vinneren, mens inkompatibel host,
prosessfeil og timeout gir en synlig oppstartsfeil. Enhetstester dekker adoption,
ny host, kappløp, timeout, prosessfeil og pakket argv. Ekte Windows-smoke har
verifisert adoption, oppstart fra tom isolert state, GUI-uavhengig hostlevetid
og en lokal usignert Nuitka-pakke. Den komplette pakkesmoken verifiserer også
pakket trigger/host/GUI, koordinert catalog/recovery-migrering og same-user
Task Scheduler apply/load/cleanup.

Oppdatering 2026-07-31: Navigasjonssiden **Innstillinger** er ikke lenger en
placeholder. Tema (system/lys/mørk), tetthet, redusert bevegelse og språk brukes
umiddelbart og lagres atomisk i en validert, versjonert JSON-fil under samme
brukers lokale MediaSync-state. Språkvalget i flaggmenyen og på innstillingssiden
er samme preferanse. Siden viser de eneste domenestøttede standardene
skrivebeskyttet i stedet for å tilby valg motoren ikke kan håndheve. Lagringsfelt
kommer fra Engine Hosts eksisterende kapasitetsrapport; datamappen kan åpnes, og
en kopierbar diagnostikkrapport utelater brukernavn og private stier. Kontrollene
reflowes ved kompakt bredde, og GUI-dekning verifiserer 900×560 uten horisontal
clipping samt at preferansene lastes ved neste oppstart.

Oppdatering 2026-07-31: Navigasjonssiden **Historikk** er ikke lenger en
placeholder. En ny bounded `QUERY_HISTORY_TIMELINE`-kontrakt samler immutable
førstegangskontroller og backupkjøringer fra Engine Host i én tidsordnet side,
med opptil 25 aktiviteter per query. Tidslinjen kan filtreres mellom alle
aktiviteter, kontroller og backupkjøringer, samt per aktiv jobb, uten at GUI
åpner SQLite. Hver rad viser faktisk jobb, mål, status og start; valgt detalj
viser i tillegg slutt, varighet, operasjoner, byte, gjennomsnittshastighet,
varsler/feil, trigger og autoritative
run-/analysis-/planidentifikatorer. Valget bindes til `(activity_kind,
activity_id)`, bevares ved refresh og nullstilles ved filter-/sidebytte.
Tom/utilgjengelig tilstand, norsk/engelsk, forrige/neste side og lange
målidentifikatorer er dekket ved 900×560 uten horisontal clipping. Lys/mørk
referanse ligger i `docs/assets/history-workspace-light.png` og
`docs/assets/history-workspace-dark.png`.

Oppdatering 2026-07-31: Operation schema 2 og catalog migration 33 binder hver
muterende forseglet planoperasjon til ett eksakt skrivbart mål og inkluderer
bindingen i planchecksumen. Førstegangsplanleggeren støtter nå opptil tre mål,
beregner mål-lokale operasjoner, katalogrekkefølge, avhengigheter, bytes og
operasjonstall, mens run-planlegging bare journalfører operasjonene for målet
som eier den aktive leasen. Eldre schema-1-planer med ett mål backfilles uten å
endre historisk checksumtolkning. IPC og planpreview eksponerer mål-ID-en.
Dashboardet nullstiller og beregner deretter breddesensitive labelhøyder og
sidens minimumshøyde på nytt; GUI-beviset dekker lange valgte kilde-/målstier ved
900×560, 1000×650 og 1120×700 uten utilgjengelig eller horisontalt klippet
innhold. Targetkontrollen og hvert synlig targetrow bruker nå også eksplisitt
vertikal minimumspolicy, og panelet reserverer den dynamiske layouthøyden før
visning. Valg eller fjerning av mapper og vindusendring bevarer viewporten i
stedet for å autoscrolle bort setupoverskriften; primærhandlingen forblir
tilgjengelig via sidens vertikale scrollbar når panelet er høyere enn vinduet.

Oppdatering 2026-07-31: Navigasjonssiden **Jobber** er ikke lenger en
placeholder. Den viser opptil 25 aktive jobber per bounded Engine Host-query,
bevarer valgt `job_id` ved refresh, tilbyr fungerende forrige/neste-sidekontroller
og laster den eksakte immutable jobbrevisjonen og forseglede planen ved valg.
**Start backup** på siden bruker valgt plans ID og checksum; bytte av jobb
nullstiller en eventuell uferdig start-idempotensidentitet. Tom, utilgjengelig,
norsk og engelsk tilstand samt lange stier er dekket uten GUI-databasetilgang
eller horisontal clipping.

| Arbeidspakke/milepæl | Status | Bevis/PR | Blockers | Neste eierhandling |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `docs/ARCHITECTURE_SPIKE_REPORT.md`; branch `spike/0a0-environment-preflight` | Ingen 0A.0-blocker | Gjennomgå rapporten og velg neste arbeidspakke |
| 0A.1 — Prosess og IPC | blocked | `spikes/0a1_process_ipc/`; `tests/spikes/0a1_process_ipc/`; `artifacts/0a1/unittest-output.txt`; `artifacts/0a1/scheduler-trigger-summary.json` | Lokal IPC/Job Object og Task Scheduler same-SID trigger består; ekte non-interactive/session-policy og feil-SID/remote klient mangler | Scope-reduksjon valgt 2026-07-17: same-user startup først; lab trengs senere for full automasjon |
| 0A.2 — Endpoint-eierskap | blocked | `spikes/0a2_endpoint_ownership/`; `tests/spikes/0a2_endpoint_ownership/`; `artifacts/0a2/` | Lokal klassifisering/lock/takeover og endelig BLAKE3-marker bestått; to-klient SMB-lab mangler | Scope-reduksjon valgt 2026-07-17: lokal-only første release; SMB-lab trengs senere for writable SMB |
| 0A.3 — Recovery og stier | passed | `spikes/0a3_recovery_paths/`; `tests/spikes/0a3_recovery_paths/`; `artifacts/0a3/` | Lokal NTFS/path/recovery bestått; SMB SourceReadGuard ikke kjørt uten SMB-lab | Bruk fallbackpolicy for uprovede SMB eller still SMB-lab til rådighet |
| 0A.4 — SQLite og kapasitet | passed | `spikes/0a4_sqlite_capacity/`; `tests/spikes/0a4_sqlite_capacity/`; `artifacts/0a4/` | Lokal 1M SQLite-/kapasitetsmåling bestått; ADR-003 eiergodkjent 2026-07-17 | Bruk to lokale SQLite-databaser med eksplisitte handoffs i 0B |
| 0A.5 — Windows argv/pakking | blocked | `spikes/0a5_windows_packaging/`; `tests/spikes/0a5_windows_packaging/`; `artifacts/0a5/` | `GetSystemDirectoryW`/argv, minimal runtime, lokal Nuitka exe-smoke, SDK-tool-inventory og release-signing-plan bestått; signeringssertifikat/signert release og ren Windows-VM mangler | Scope-reduksjon valgt 2026-07-17: lokal usignert preview først; full release krever signering og ren VM senere |
| 0A.6 — Beslutningsreview | passed | `docs/adr/0A_DECISION_REVIEW.md`; `docs/adr/owner-decision-intake.current.json` | Eierbeslutninger registrert 2026-07-17; full SMB-/Task Scheduler-/signeringsevidens er eksplisitt utsatt | Åpne 0B med lokale scope-begrensninger |
| 0A — Samlet arkitekturbevis | passed | 0A.0–0A.6 evidence/status docs | 0A owner gate passert for scoped local-preview path; full SMB/signering/non-interactive automasjon er utsatt | Fortsett 0B uten å påstå utsatte garantier |
| 0B — Repository og kontrakter | in_progress | `AGENTS.md`; `docs/CODEX_START_PROMPT.md`; `.github/workflows/ci.yml`; `requirements-dev.txt`; `pyproject.toml`; `tools/check_imports.py`; `tools/audit_dependencies.py`; `tools/audit_vulnerabilities.py`; `tools/validate_contracts.py`; `schema/database-contract.yaml`; `schema/examples/ipc-command.valid.json`; `security/vulnerability-advisories.json`; `artifacts/0b/dependency-audit.json`; `artifacts/0b/vulnerability-audit.json`; `tests/architecture/test_contract_validation.py`; `tests/architecture/test_ci_workflow.py`; `src/mediasync_home/`; `src/mediasync_home/application/process_supervision.py`; `src/mediasync_home/application/host_locator.py`; `src/mediasync_home/application/safe_paths.py`; `src/mediasync_home/application/job_drafts.py`; `src/mediasync_home/application/job_creation.py`; `src/mediasync_home/application/writable_endpoint_registration.py`; `src/mediasync_home/application/journaled_commit.py`; `src/mediasync_home/application/catalog_handoff.py`; `src/mediasync_home/application/catalog_read_models.py`; `src/mediasync_home/application/command_receipts.py`; `src/mediasync_home/application/plans.py`; `src/mediasync_home/application/plan_read_models.py`; `src/mediasync_home/application/runs.py`; `src/mediasync_home/application/recovery_intents.py`; `src/mediasync_home/application/recovery_operations.py`; `src/mediasync_home/adapters/writable_endpoint_registration.py`; `src/mediasync_home/adapters/endpoint_leases.py`; `src/mediasync_home/adapters/local_host_locator.py`; `src/mediasync_home/adapters/host_mutex.py`; `src/mediasync_home/adapters/final_commit.py`; `src/mediasync_home/adapters/process_supervisor.py`; `src/mediasync_home/adapters/reparse_guard.py`; `src/mediasync_home/adapters/robocopy.py`; `src/mediasync_home/adapters/sqlite/writable_endpoint_registrations.py`; `src/mediasync_home/adapters/sqlite/catalog_handoffs.py`; `src/mediasync_home/adapters/sqlite/connection_policy.py`; `src/mediasync_home/adapters/sqlite/endpoint_roots.py`; `src/mediasync_home/adapters/sqlite/migrations.py`; `src/mediasync_home/adapters/sqlite/lease_tokens.py`; `src/mediasync_home/adapters/sqlite/recovery_intents.py`; `src/mediasync_home/adapters/sqlite/recovery_operations.py`; `src/mediasync_home/adapters/sqlite/job_draft_store.py`; `src/mediasync_home/adapters/sqlite/job_catalog.py`; `src/mediasync_home/adapters/sqlite/command_receipts.py`; `src/mediasync_home/adapters/sqlite/plans.py`; `src/mediasync_home/adapters/sqlite/runs.py`; `src/mediasync_home/ipc/protocol.py`; `src/mediasync_home/ipc/server.py`; `src/mediasync_home/ipc/client.py`; `src/mediasync_home/ipc/win32_named_pipe.py`; `src/mediasync_home/composition/launcher.py`; `src/mediasync_home/composition/ui.py`; `src/mediasync_home/presentation/view_models/backup_setup.py`; `src/mediasync_home/presentation/view_models/plan_preview.py`; `src/mediasync_home/presentation/view_models/plan_endpoints.py`; `src/mediasync_home/presentation/view_models/catalog_preview.py`; `src/mediasync_home/presentation/view_models/localization.py`; `scripts/run_role.py`; `tools/render_gui_shell.py`; `artifacts/0b/gui-shell/`; `tests/unit/test_bootstrap_roles.py`; `tests/unit/test_dev_runner.py`; `tests/unit/test_dependency_audit.py`; `tests/unit/test_vulnerability_audit.py`; `tests/unit/test_runtime_policy.py`; `tests/unit/test_process_supervision.py`; `tests/unit/test_reparse_guard.py`; `tests/unit/test_robocopy_adapter.py`; `tests/unit/test_host_locator.py`; `tests/unit/test_host_mutex.py`; `tests/unit/test_safe_paths.py`; `tests/unit/test_launcher_role.py`; `tests/unit/test_job_drafts.py`; `tests/unit/test_job_creation.py`; `tests/unit/test_journaled_commit.py`; `tests/unit/test_catalog_handoff.py`; `tests/unit/test_catalog_read_models.py`; `tests/unit/test_catalog_preview_view_model.py`; `tests/unit/test_command_receipts.py`; `tests/unit/test_plan_sealing.py`; `tests/unit/test_run_start.py`; `tests/unit/test_run_target_preflight.py`; `tests/unit/test_run_target_lease.py`; `tests/unit/test_recovery_intents.py`; `tests/unit/test_recovery_operations.py`; `tests/unit/test_endpoint_leases.py`; `tests/unit/test_final_commit_adapter.py`; `tests/unit/test_sqlite_connection_policy.py`; `tests/unit/test_sqlite_migrations.py`; `tests/unit/test_backup_setup_view_model.py`; `tests/unit/test_plan_preview_view_model.py`; `tests/unit/test_plan_endpoint_preview_view_model.py`; `tests/unit/test_ipc_handshake.py`; `tests/unit/test_engine_host_pipe_loop.py`; `tests/unit/test_ui_client_role.py`; `tests/unit/test_engine_status_view_model.py`; `tests/integration/test_endpoint_lock_integration.py`; `tests/integration/test_writable_endpoint_registration.py`; `tests/integration/test_robocopy_live.py`; `tests/integration/sqlite/test_connection_policy.py`; `tests/integration/sqlite/test_migrations.py`; `tests/integration/sqlite/test_writable_endpoint_registrations.py`; `tests/integration/sqlite/test_sqlite_endpoint_roots.py`; `tests/integration/sqlite/test_sqlite_lease_tokens.py`; `tests/integration/sqlite/test_sqlite_recovery_intents.py`; `tests/integration/sqlite/test_sqlite_recovery_operations.py`; `tests/integration/sqlite/test_journaled_final_commit.py`; `tests/integration/sqlite/test_sqlite_catalog_handoff.py`; `tests/integration/sqlite/test_job_draft_store.py`; `tests/integration/sqlite/test_job_catalog.py`; `tests/integration/sqlite/test_sqlite_command_receipts.py`; `tests/integration/sqlite/test_sqlite_plans.py`; `tests/integration/sqlite/test_sqlite_runs.py`; `tests/gui/test_pyside_shell.py`; `tests/gui/test_render_gui_shell.py`; `tests/integration/ipc/test_win32_named_pipe.py`; `tests/integration/ipc/test_named_pipe_roles.py` ; `tests/integration/test_process_supervisor_live.py`; `tests/architecture/test_repository_structure.py` | 0B-work order, draft contract validation, database invariant contract skeleton og executable two-store migration skeleton for case-collision storage, separate head tables and composite parent-scope FKs, process entrypoints, composition roots, capability-typed port skeleton, protocol-first IPC handshake, same-user local-only Win32 named-pipe adapter, runtimepolicy-status for interne roller, ProcessSupervisor launch-plan policy skeleton og lokal internal-role subprocess-adapter, lokal HostLocator descriptor for same-user preview pipe/state-root og scoped Engine Host mutex guard, sentral endpoint-relative SafePath-validering med reservert `.mediasync` kontrollnavn, lokal ReparseGuard-adaptergrense for endpointrøtter og eksisterende path-kjeder i staging, recovery-verification og final commit, Win32 handle-open/final-path/file-ID-evidence med final-commit escapeavvisning, persisted endpoint identity binding fra catalog revision til `.mediasync/endpoint.json` før fencing-token/resource-lease issuance, simulated post-inspection reparse-swap rejection i final-path guard, canonical Robocopy batchmanifest/hash/no-overwrite manifestpublisering med directory-manifest argv for eksakte filnavn og full inbox-enumerering før payloadpublisering, SQLite connection policy skeleton for catalog/recovery PRAGMAs and local state layout, standard backup setup view-model/dashboard skeleton med fire steg, sikre standarder, applikasjonslagets job-draft value object/port, durable local job-draft store adapter, sealed standard-backup job revision persistence skeleton, versioned `CREATE_STANDARD_BACKUP_JOB` command skeleton som gjenkjennes men avvises mens 0B-mutasjoner er deaktivert, enabled local dispatcher/effect receipt path for `CREATE_STANDARD_BACKUP_JOB`, restartbar eksplisitt registrering av lokale skrivbare mål med checksummet kontrollmarkør, ownership-record, kontrollert probe og immutable endpoint-/jobbrevisjoner, IPC-wired durable command receipt/idempotency replay skeleton for terminal disabled-command rejection/success, GUI role command-submit smoke path to Engine Host over local named pipe, deterministic sealed-plan checksum/immutability skeleton, checksum-bound queued run/run-target materialization, run-target preflight claim into `ACQUIRING_LEASE`, live lease-authority handoff into `REVALIDATING`, local OS-handle endpoint lock adapter med Win32 file-handle liveness probe, adapter-issued non-serializable `MutationPermit` skeleton with released/lost-handle guard, lab-only no-overwrite final commit adapter skeleton with live permit revalidation, `.mediasync_test_root` marker, staging hash recheck, sentral safe relative final-path validation and safe relative final-path insert, journaled final commit wrapper from durable commit intent through `COMMIT_PRECONDITIONS_REVALIDATED`, `FILESYSTEM_APPLIED`, `FINAL_DURABLE` and `FINAL_VERIFIED` against the SQLite recovery store, idempotent final-file catalog handoff ledger, recovery `CATALOG_RECORDED` handoff id after final verification, and bounded startup reconciliation for already-committed catalog handoffs, recovery-backed per-resource/epoch fencing-token allocation, durable `resource_leases` registration/release skeleton and recovery operation journal/hash-chain skeleton, og separate activity/attention/freshness-dimensjoner, bounded Engine Host pipe loop med sanitert feil-event, eksplisitt long-running `--serve-forever` pipe mode med graceful interrupt-stop event, etter-IPC og timerdrevet bounded run-executor cycle med idle-backoff for state-root-backed host, launcherens `--local-preview-host` startup-modus for persistent HostLocator-publisert same-user Engine Host, separate Engine Host/GUI role status roundtrip, GUI reconnect proof and launcher local-preview readiness smoke, minimal PySide shell med tokens/QSS/icon registry/EngineClient-statusvisning, flaggbasert språkvelger som bytter synlige shell-/dashboardetiketter i presentasjonslaget, bounded plan-endepunkt/snapshot-read-model og cataloged-files read model over Engine Host IPC med aktivitetspanelpreview uten GUI-SQLite, strict `mypy` for `src` og audittools, import-linter layer contracts, offline dependency-/lisens-/sårbarhetsinventory for dev closure, Windows CI-skeleton for 0B-gates og 0B shell light/dark 100/150/200 %-evidence, live transfer-child host-exit/orphan-prosessbevis er etablert; explicit Start Backup command and journaled directory execution, foreign-owner takeover, registration of pre-migration-30 pending jobs, production-complete repositories, recovery-atomic multi-command dispatcher/effect acceptance, full plan preconditions/run workflow, broader scanner-to-GUI catalog read-model coverage, broader live reparse/fault-injection evidence, production replace/version/quarantine commit adapters, full lokaliseringsmatrise, remaining scheduler password/S4U enablement, production HostLocator adoption hardening, broader Robocopy crash/fault-injection matrix, SMB lease-loss lab-evidence, full visuell akseptansematrise, frisk advisory-feed/release-SBOM og full non-interactive/wrong-SID lab er ikke implementert ennå | Neste slice: add explicit Start Backup and journaled directory execution, then multi-target operation binding |

Statusrettelse 2026-08-01: Milepælradens eldre «Neste slice» er erstattet av
oppdateringene over og av start-/directory-beviset nedenfor. Explicit start,
journalført katalogoppretting og multi-target operation-binding er levert.
Reparasjon av pre-migration-30 pending jobs er nå også levert gjennom den
eksplisitte, revisjonsbundne **Registrer mål**-handlingen. Den senere
oppdateringen øverst dekker også kontrollert lokal fremmed-overtakelse.

Nyeste 0B-slice: `VER-001` har nå tre kanoniske og uavhengige resultatakser:
transfer, assurance og durability. Catalog migration 51 normaliserer eldre
auditposter konservativt, beholder tvetydig `DURABLE` som `UNKNOWN`, og
installerer insert-guards som avviser ukjente verdier og suksess uten
transfer-/assurancebevis. Materialiseringen beholder de eksakte rå claimene og
final-eventens `file_flush_succeeded`/`write_through_move_used` i
`verification_json`; manglende suksessevidens gir ingen terminal suksessrad.
Historikk viser transferstatus, byte, verifisering og durability i separate
norske/engelske rader ved 900×560. `WRITE_THROUGH_REQUEST_CONFIRMED` vises som
bekreftet forespørsel, aldri som fysisk mediegaranti. Named streams,
full-object-verifisering og writable SMB-evidens gjenstår, så `VER-001` er
fortsatt `in_progress`.

Forrige 0B-slice: `DUR-001` bruker nå `MoveFileExW` med
`MOVEFILE_WRITE_THROUGH` for no-overwrite finalfil, replacement og
katalogpublisering etter at target-side tempinnhold er flushet og verifisert.
Finalfilen reåpnes og flushes før receipt. Den kontrollerte capability-proben
måler samme API og lagrer `supports_write_through_move`; planforsegling blokkerer
flush-only-profiler. Førstegangspublisering og idempotent katalogreplay har
separate claims, slik at replay aldri arver en write-through-påstand den ikke
utførte. Fysisk mediegaranti og writable SMB-evidens gjenstår, så kravet er
fortsatt `in_progress` i det scope-reduserte produktet.

Forrige 0B-slice: `DUR-001` fikk eksplisitt og maskinvaliderbar
final-durability-evidence. Lokale filcommits reåpner og `fsync`-er den ferdige
filen før `LOCAL_FILE_FLUSH_CONFIRMED`; katalogcommits flusher markørfilen, men
oppgir ærlig at directory-entry durability er ubekreftet uten write-through.
Commit receipts avviser ukjente og inkonsistente claims, og recoveryjournalen
lagrer adapterens eksakte state samt flush-/write-through-flagg. Den tidligere
syntetiske adapter-completed-claimen er fjernet. Endpointprofilstyrt
write-through og parent-directory flush gjenstår, så kravet er fortsatt
`in_progress`.

Forrige 0B-slice: `END-001` har naa en kanonisk schema-1 endpoint capability-
profil. Read-only klassifisering lagrer Win32 volum-, filsystem-, navn-, path-,
case- og metadataevidence. Eksplisitt targetregistrering tester kontrollert
flush, rename, no-overwrite, replace og named streams i privat probeomraade,
lagrer immutable evidence i catalog migration 50 og binder eksakt SHA-256 til
endpointregistreringen. Planforsegling validerer canonical JSON, hash og
probe-scope og blokkerer manglende eller manipulert evidence. SMB-/shareprobe,
fysisk medietype og full capability-driven metadata-loss policy gjenstaar.

Forrige 0B-slice: **Jobber**-arbeidsflaten viser nå live, sekvensbevisst
`QUERY_RUN_PROGRESS` for den aktive kjøringen med durable runntilstand,
operasjon-/bytefremdrift og faktisk tilstand per mål. `PAUSE_RUN` publiserer
først `PAUSING`; den separate executortråden fullfører pause bare mellom
bounded recovery-steg, frigir retained endpointleaser, nullstiller stale
lease-/fencingbevis og publiserer deretter `PAUSED`. `RESUME_RUN` flytter
pausede mål tilbake til `PENDING` og runnen til `QUEUED`, slik at vanlig
lease acquisition og revalidation må lykkes før videre mutasjon. Begge
kommandoer bruker command-receipt/idempotens-transaksjonen. GUI viser Pause
bare i aktive tilstander og Fortsett bare i `PAUSED`. Safe Stop, aktiv fil,
hastighet og ETA gjenstår. Native Windows-referanser ligger i
`docs/assets/jobs-workspace-progress-light.png` og
`docs/assets/jobs-workspace-progress-dark.png`.

Forrige 0B-slice: **Jobber**-arbeidsflaten bruker den eksisterende paginerte
backupoversikten og eksakte jobbdeltalj-queryen over IPC. Hver rad bindes til
`job_id`; valgt rad oppdaterer både Jobber-detaljen og dashboardkonteksten.
Forseglede runnable planer kan startes fra denne siden uten checksum-/jobbdrift.
Qt-interaksjonstester dekker to jobber med ulike planchecksums, sidebytte,
selection preservation, køstatus, språkbytte og responsive labelhøyder ved
900×560. Lys/mørk referanse ligger i `docs/assets/jobs-workspace-light.png` og
`docs/assets/jobs-workspace-dark.png`. Den neste placeholder-baserte
produktflaten er **Innstillinger**.

Forrige 0B-slice: En runnable forseglet førstegangsplan kan nå startes eksplisitt fra jobbdeltaljen. GUI sender plan-ID og checksum som `START_RUN`, beholder request-/idempotens-ID ved feil og viser køstatus etter aksept; catalog avviser samtidig en ny levende run for samme jobb. Journalførte `CREATE_DIRECTORY`-operasjoner har eksplisitt operation-kind og planrekkefølge, en deterministisk recovery-markør, atomisk lokal rename, restartverifisering, eget catalog-effect og kontrollert markøropprydding. Executor fullfører parent-directory før nested filer og kjeder flere durable intent-segmenter. Catalog migration 32 og recovery migration 6 oppgraderer eksisterende state. Integrasjonstesten kjører en directory-plus-file-plan helt til `COMPLETED`, og fault-window-testen beviser idempotent retry etter rename. Source-, target-, status- og planetiketter beholder den tidligere breddesensitive minimumshøyden, slik at valgte lange mål ikke klippes. Multi-target operation-binding, pre-migration-30 pending-jobbreparasjon og kontrollert fremmed-overtakelse gjenstår.

Statusrettelse: Multi-target operation-binding, pre-migration-30
pending-jobbreparasjon og kontrollert lokal fremmed-overtakelse er levert og
bevist gjennom oppdateringene øverst.

Forrige 0B-slice: Den firestegs lokale backupflyten utfører eksplisitt registrering av det valgte skrivbare målet gjennom handlingen **Opprett og registrer**. Før filsystempublisering lagrer Engine Host en restartbar catalog-intent. Den lokale provisioneren godtar bare et fraværende kontrollområde eller sin egen eksakt matchende partial staging, oppretter checksummet schema-4 `endpoint.json`, immutable ownership-record, påkrevde og installasjonsspesifikke namespaces, og gjennomfører en avgrenset write/read/delete-probe. Catalog migration 30 lagrer immutable registreringsbevis; commit appender ny endpointrevisjon/generasjon og ny jobbrevisjon, flytter begge heads med compare-and-swap og setter bare den aktive bindingen til `WRITABLE_READY`. Startup fullfører eksakte pending intents før endpointklassifisering. Fremmed, korrupt, nyere, ukjent eller endret kontrollstate blokkeres uten takeover eller sletting. GUI viser registreringsstatus, beholder gjennomgått utkast ved delvis feil og tilbyr **Prøv registrering på nytt**.

Forrige 0B-slice: HostLocator fallback skiller nå en stale/unreachable publikasjon fra en fersk host som fortsatt er i startupvinduet mellom locator-publisering og pipe-readiness. En ny liveness-aware compare-and-delete-policy fjerner bare den eksakte samme publikasjonen når prosessen er død, heartbeaten er stale, eller den unreachable posten er et eldre heartbeat-løst kompatibilitetsformat. Fersk heartbeat med levende eller ukjent prosessliveness bevares. Launcher returnerer da den opprinnelige adoption-feilen uten å starte en konkurrerende host; GUI og trigger bruker samme policy og rapporterer om cleanup faktisk skjedde. Unit- og ekte launcher/GUI/trigger-subprosesstester dekker preserve/clear-grensene. Dette herder same-user local preview; cross-session singleton, Task Scheduler-bootstrap og full produksjonslivssyklus gjenstår.

Forrige 0B-slice: Transferchild-grensen klassifiserer nå feil fra prosessventing, terminering og handleopprydding i stedet for å lekke rå adapterfeil. En ventefeil eller timeout utløser kontrollert terminering; cleanup-feil utløser et ekstra terminate/close-forsøk og blokkerer fortsatt publisering. `ContainedTransferProcess.close()` forsøker både Job Object- og prosesshandle selv om det første close-kallet feiler, beholder bare feilede handles for retry, og `terminate()` utfører alltid close-fasen. Robocopy rydder fortsatt bare tom eller manifest-forventet uncommitted inbox ved disse feilene. Fault-injection dekker wait-, terminate-, Job-handle- og cleanup-feil i tillegg til de eksisterende live kill-on-close/host-exit-bevisene; live OS-feilinjeksjon og reparse-race lab gjenstår.

Forrige 0B-slice: `tools/build_contract_types.py` genererer nå deterministiske Python-typer og kontraktdokumentasjon fra `schema/state-machines.yaml` og `schema/reason-codes.yaml`. Runtime bruker de genererte typene for command-receipt- og recovery-operation-tilstander, mens alle reason codes eksponeres som en typed enum med generert metadata. CI, arkitekturtester og `--check` avviser drift mellom YAML, `src/mediasync_home/generated/contract_types.py` og `docs/generated/CONTRACT_TYPES.md`. `operation_commit` har fått en eksplisitt ordnet state-inventory som samsvarer med runtime. Kontraktene beholder sin eksisterende draft-status; genereringen fryser dem ikke og innebærer ingen eiergodkjenning.

Forrige 0B-slice: Catalog migration 29 etablerer en ekte monoton `endpoint_revisions.generation` og fjerner den tidligere runtime-feilen der `control_schema_version` ble brukt som endpointgeneration. Eksisterende revisjoner backfilles deterministisk per endpoint etter creation time og revision-ID; nye revisjoner må bruke eksakt neste positive generation, og revisjonens eksisterende immutable-guard låser verdien. `snapshots` og `plan_endpoints` lagrer nå den eksakte generationen, avviser mismatch mot endpointrevisionen, og planbindingen må også matche snapshotets generation. Endpoint-root-resolver, live lease, recovery-backed resource-lease og den ikke-serialiserbare `MutationPermit` bærer samme generation; operation planning avviser plan/permit-drift før recoveryoperasjonen opprettes. Den maskinlesbare kontrakten og en ekte migration-28-fixture låser backfill, skipped-generation rejection og wrong-generation snapshot/plan negatives. Full endpoint reprobe/adoption som oppretter en ny sikkerhetsrelevant revisjon er fortsatt Milepæl 2-arbeid.

Forrige 0B-slice: Catalog migration 28 innfører append-only `filter_set_versions` og en eksakt, immutable filterversjonsbinding for hver jobbrevisjon. `job_revisions.filter_set_version` er positiv og kan ikke endres; en pre-insert guard avviser manglende eller wrong-parent versjon før revisjonen opprettes. `job_revision_filter_bindings` binder `(job_id, job_revision_id)` til `(job_id, filter_set_id, filter_set_version)` med faktiske parent-scoped foreign keys og opprettes automatisk av databasen etter en gyldig revisjonsinsert. Både versjonsrader og bindingsrader avviser `UPDATE`/`DELETE`. Eksisterende migration-27-state backfilles deterministisk til versjon 1 med kanonisk `ALL_USER_FILES`-rules JSON og SHA-256, mens standard-jobbrepositoryet skriver samme payload/hash og eksponerer versjon 1 i read models. Den maskinlesbare databasekontrakten og negative driftstester låser tabellene og alle tre nye composite FKs.

Forrige 0B-slice: Engine Host startup migrerer nå det eiergodkjente catalog/recovery-paret gjennom en restartbar state-migration epoch før ordinære writable forbindelser eller HostLocator-readiness publiseres. Begge stores preflightes read-only med store identity, sammenhengende migrationhistorikk og checksums; eksisterende initialisert state får ett koordinert, verifisert SQLite Online Backup-sett før første schemawrite. En kanonisk intent binder layout, appversjon, migrationplanhash, før-/målversjon og backuphash, og rewrites atomisk etter hver separat storemigrasjon og integritetsverifisering. Committed-markøren godtas bare når begge stores, plan/intent, backup og finalversjoner stemmer. Startup fortsetter samme epoch etter krasj mellom catalog og recovery, rydder bare en entydig upublisert pre-intent-katalog, og feiler lukket ved partial initialization, drift, manipulering eller flere pending epochs. Integrasjonstester beviser fresh install, no-op restart, pre-migration backup, krasj etter bare catalog, deterministisk resume av recovery, tamperavvisning og orphan-intent-temp recovery. Kontrollmappemigrasjon og bredere prosessdrap-faultmatrise gjenstår.

Forrige 0B-slice: Catalog migration 27 håndhever nå immutable jobb- og endepunktrevisjoner i selve databasen. Direkte `UPDATE`/`DELETE` av `endpoint_revisions`, `job_revisions` og forseglede standard-backup-revisjonsdetaljer avvises; filtersett kan ikke endres eller slettes etter at en revisjon refererer dem. Jobbrevisjonens endpoint-binding kan fortsatt oppdatere bare dynamisk registration state/reason, mens identitet, rolle, ordinal, endpointrevision og opprettelsestid er låst. Separate `job_heads`/`endpoint_heads` kan fortsatt flyttes til en ny innskutt revisjon. Den maskinlesbare databasekontrakten låser samme grense og har en negativ driftstest. En ekte migration-22-fixture beviser at startup kan migrere og backfille endpoint-bindinger uten å slette eller omskrive historiske revisjoner. Filterversjoner og endpointgeneration er etablert i de nyere migration-28/29-slicene.

Forrige 0B-slice: SQLite-policyen er nå håndhevet på de faktiske forbindelsene som alle produksjonsrepositories deler. Etter apply verifiseres canonical main-databasepath, fravær av attached stores og aktive PRAGMA-verdier. En connection-authorizer blokkerer senere `ATTACH`/`DETACH` og policyendringer som kan svekke `synchronous=FULL`, WAL, foreign keys, trusted schema, query-only, checkpoint eller andre defensive valg. Extension loading forblir deaktivert, `SQLITE_DBCONFIG_DEFENSIVE` aktiveres når runtime støtter det, og en AST-gate reserverer safety-hookene for policyadapteren. Runtime-, migration-, backup- og negative downgrade/attach-tester passerer. En separat serialisert catalog bulk-writer med `NORMAL` er fortsatt senere arbeid; dagens produksjonsrepositories bruker den kritiske `FULL`-forbindelsen.

Forrige 0B-slice: Alle gyldige named-pipe handshake-, query- og commandrammer har nå en UUID `request_id`, og Engine Host ekkoer den i responsens toppnivå. Win32-klienten validerer eksakt samsvar før response acknowledgment og før payloaden godtas; manglende eller spoofet correlation avvises som protokollfeil. Det eneste snevre compatibility-unntaket er en eldre hosts ukorrelerte terminale protocol/schema-mismatch under handshake, slik at oppgradering gir strukturert avvisning i stedet for traceback. In-process-adapteren følger samme kontrakt, og hver handshake/query får ny ID. Handshake-/sessionschemaet er løftet til 2, mens durable command schema forblir 1. Muterende retry beholder derfor separat semantikk: toppnivået korrelerer dette forsøket, mens nested receipt kan vise den opprinnelige durable request-ID-en og `idempotency_key` fortsatt er effektens dedupnøkkel.

Forrige 0B-slice: SQLite migration history er nå checksum-pinnet og fail-closed. Hver anvendt migrasjon lagrer SHA-256 over kanonisk versjon, navn og eksakte ordnede SQL-statements. Startup godtar bare et sammenhengende kjent prefiks, og avviser navne-/checksumdrift, hull, foreign-store-rader og schema nyere enn runtime før nye migrasjoner. Gyldig eldre local-preview metadata uten checksum backfilles én gang etter samme validering; deretter blokkerer databasetriggere `UPDATE` og `DELETE` av historikken. Koordinert migration-epoch er etablert i den nyere startup-slicen; faktisk control-area migration gjenstår.

Forrige 0B-slice: Engine Host eksponerer nå bounded `QUERY_RUN_PROGRESS` gjennom in-process-klient, Win32 named pipe, GUI-CLI og `EngineClient`. Det autoritative snapshotet er begrenset til 32 mål og 64 KiB, og bruker en monoton per-run `sequence_no` avledet fra persisterte run-/target-`row_version`-felt. Polling etter samme sekvens returnerer `changed=false` uten duplisert snapshot; reconnect med eldre sekvens får full refresh, og state restore som flytter sekvensen bakover gir eksplisitt `sequence_reset=true`. SQLite-, reconnect-, maksimalstørrelse-, CLI-, Win32- og process-role-evidens dekker kontrakten. Flyktig push-subscription er fortsatt en senere optimalisering og er ikke sannhetskilde.

Forrige 0B-slice: `TIME-001` har nå én injectable `ClockPort` med produksjonsklokke og delt fake UTC/monotonic testklokke. Transactional outbox og Task Scheduler desired-state claims fanger UTC-start kun som auditdata og bruker en in-memory monoton deadline som live autoritet. Frem- og bakoverhopp i veggklokken utløper ikke claims. Når monotonic deadline nås før et eksternt resultat committes, avvises den gamle completionen og owner/generation/token invalideres med en kort SQLite CAS-requeue; outbox får ingen falsk tombstone, og scheduler-state forblir idempotent retrybar selv når ekstern apply allerede skjedde. Startup-requeue krever fortsatt eksplisitt inactive-owner-bevis og bruker aldri UTC+TTL alene. Gjenværende standalone live timeouts/backoff må fortsatt flyttes til den delte klokken før `TIME-001` kan lukkes.

Forrige 0B-slice: Lokal AppData-state har nå en bounded capacity-preflight med separate estimater for catalog, recovery, hashcache og logger, samt intern backupreserve, minimum ledig plass og soft/hard quota. Snapshotmaterialisering sjekker kapasitet før skann og igjen fra målte radantall før persistence; run-executoren sjekker før første steg. Soft quota anbefaler bare opprydding av ikke-autoritative cache-/loggdata og sletter ingenting automatisk. Hard quota, lav lokal diskplass eller ufullstendig måling starter ingen ny analyse/transfer. Direkte eller wrapped `SQLITE_FULL` rulles tilbake én gang uten retry, latches til restart, publiseres sanitert gjennom Engine Host handshake/status og frigir retained executor-leaser. En ekte catalog-full integrasjonstest beviser at den ucommittede catalog-writen forsvinner mens tidligere committet recoverybevis i den separate recoverydatabasen fortsatt kan leses. Manifestert cache-reclaim og bredere recovery-writer fault injection gjenstår.

Forrige 0B-slice: Catalog migration 26 connects each active standard-backup job revision to one durable source/target snapshot analysis. Engine Host runs a bounded, strictly read-only local scanner after endpoint classification at startup and after successful job creation. It records regular files, directories, byte counts, per-directory Windows case-mode evidence, volatility, unreadable/disappeared paths, unsupported objects, scan limits and reparse blocks; reparse points are never traversed. Exact `.mediasync` content is excluded only after a valid owned/foreign/newer-schema classification, so unknown control-like content remains visible and blocking. Filesystem reads finish before `BEGIN IMMEDIATE`; analysis parents, deterministic batches, case collisions, seals and the job materialization outcome are then persisted atomically. Only complete/stable coverage without blocking issues is checksum-sealed and reusable on restart. Live scans are best-effort rather than VSS point-in-time snapshots, no user root is written, and post-commit scan failure cannot roll back a durable job. State-backup evidence now reports catalog schema/migration 26. Explicit marker registration/adoption, controlled writable probing and automatic plan/run creation remain.

Forrige 0B-slice: Catalog migration 25 persists one current read-only classification observation per registered endpoint revision together with a precise registration reason on every job binding. Engine Host classifies all registered local roots at startup and immediately after a successful job transaction, while filesystem reads remain outside the catalog write transaction. Sources without a control area become `READ_ONLY_READY`; targets without one remain `REGISTRATION_PENDING`; foreign/newer-schema targets remain read-only; unsafe, mismatched or inaccessible roots become `BLOCKED`. This path never grants `WRITABLE_READY`, never creates `.mediasync`, and a post-commit classification failure cannot roll back an already durable job. The runtime exposes bounded classification and binding counts. Explicit marker registration/adoption, controlled writable probing and automatic plan/run creation remain.

Forrige 0B-slice: `catalog.sqlite` har nå en ekte singleton `installation_state` i migration 24. Engine Host oppretter én tilfeldig kanonisk UUID først etter at både catalog- og recovery-migrasjonene er kompatible, bevarer UUID og opprettelsestid på restart, oppdaterer bare app-/schema-/protokollmetadata med row-version CAS, og avviser channel-drift eller korrupt identitet. Database-triggere gjør identitet, channel og opprettelsestid immutable og blokkerer sletting. Runtime eksponerer denne separate owner-identiteten uten å endre HostLocator-aliaset `local-dev`. State-backup manifestene rapporterte catalog schema 24 i denne slicen. Klassifiseringsstatuspersistens er senere etablert; eksplisitt marker/adoption, writable probe, snapshots og plan/run gjenstår.

Forrige 0B-slice: En produksjonsformet, strengt read-only `LocalEndpointControlAreaClassifier` dekker nå alle ni kontrollområdestater: `ABSENT`, `VALID_OWNED`, `VALID_FOREIGN`, `VALID_READ_ONLY_NEWER_SCHEMA`, `PARTIAL_CONTROL_AREA`, `UNKNOWN_EMPTY_DIRECTORY`, `UNKNOWN_NONEMPTY_DIRECTORY`, `CASE_ALIAS_COLLISION` og `CORRUPT_MARKER`. Klassifiseringen bruker bounded katalog-/JSON-lesing, avviser reparse-/typeavvik, duplicate JSON keys og ugyldig UUID/tid/schema, verifiserer JCS-formet BLAKE3 markerchecksum med constant-time compare, binder root identity til OS-handleidentitet og validerer immutable ownership-record mot markøren. Bare de tre dokumenterte valid-state-ene kan ekskluderes fra snapshot; ukjent `.mediasync` forblir synlig og ikke-muterbar. Integrasjonstestene muterer bare temp-røtter. Klassifisereren er ikke koblet til automatisk adoption og skriver ingen kontrollfiler; registreringsstatuspersistens, eksplisitt marker/adoption, writable probe, snapshots og plan/run gjenstår.

Forrige 0B-slice: En lagret standard-backupjobb får nå varige lokale endpoint-entiteter, revisjoner, heads, kanoniske root-claims og kilde-/mål-bindinger i catalog migration 23. Samme lokale rot gjenbruker en stabil UUID-endpoint på tvers av jobber, oppretting skjer i samme `BEGIN IMMEDIATE`-transaksjon som jobb/receipt/outbox, idempotent replay returnerer de samme bindingene, og Engine Host backfiller eldre aktive jobber ved oppstart. Alle nye bindinger er eksplisitt `REGISTRATION_PENDING`; GUI viser derfor at endpoint-sikkerhetsoppsett venter. Denne slicen skriver ikke `.mediasync` eller andre kontrollfiler i valgte brukerrot-mapper og påstår ikke writable ownership. Engine Host verifiserer også den mottatte kommandoens kanoniske BLAKE3 payload-hash før dispatch, receipt eller effect. Read-only filesystem-klassifisering og stabil installasjonsidentitet er senere etablert; marker/adoption, snapshotmaterialisering og automatisk plan/run-oppretting gjenstår.

Forrige 0B-slice: Den firestegs Qt-flyten kan nå opprette en varig standard-backupjobb gjennom den tilkoblede Engine Host. GUI sender det gjennomgåtte kilde-/målutkastet som en versjonert og BLAKE3-hashet `CREATE_STANDARD_BACKUP_JOB`-kommando med stabile request-/idempotency-ID-er; Engine Host validerer og lagrer inline-utkastet før jobbforsegling, command receipt og outbox under samme `BEGIN IMMEDIATE`-transaksjon. Persistent HostLocator-start med lokal state-root aktiverer eksplisitt same-user local mutations, mens bounded/direct host uten flagget forblir read-only. Produksjonskomposisjonen har nå både UUID-jobb-ID-factory og SQLite command-effect transaction runner, og rollbackdekning beviser at utkast, jobb, receipt og outbox ikke blir delvis stående ved outbox-feil. `blake3==1.0.9` er pin-et og lisens-/sårbarhetsauditert. Endpoint registration er senere etablert; filesystem-klassifisering/adoption, snapshotmaterialisering og automatisk plan/run-oppretting gjenstår.

Forrige 0B-slice: Qt-shellen er ikke lenger en statisk forhåndsvisning for hovedkontrollene. Navigasjonslisten bytter workspace-side, refresh-knappen er klikkbar også uten Engine Host og viser sannferdig lokal preview-status, og den firestegs standard-backupflaten kan velge kilde- og målmapper lokalt, flytte gjennom defaults/review og markere at durable oppretting krever tilkoblet Engine Host. Dekningen beviser nav-klikk, mappevalg via primærknappen, review-klikk og eksisterende språkbytte; durable GUI-triggered job creation er nå etablert, mens endpoint adoption gjenstår.

Forrige 0B-slice: Språkvelgeren har nå bidireksjonell dashboard-/aktivitetsdekning. `localize_display_value()` oversetter også English-origin `Latest run: ...` tilbake til norsk, unit-dekningen beviser aktivitets-/oppmerksomhets-/ferskhet-/neste-handling-prefixer begge veier, og GUI-testen velger flaggmenyen `nb -> en -> nb` og verifiserer synlige dashboard-/activity labels etter hver reappisering. Full fremtidig strenginventar/lokaliseringsmatrise gjenstår fortsatt.

Forrige 0B-slice: Robocopy manifest-mismatch fault cleanup er nå mer presis. `RobocopyStagingTransferAdapter` forsøker guarded cleanup også når en ikke-fatal Robocopy-exit etterlater en tom eller bare manifest-forventet inbox som ikke kan publiseres, slik at neste retry ikke blokkeres av `ROBOCOPY_STAGING_INBOX_EXISTS`; samme cleanup nekter fortsatt å slette typeavvik, kataloger/symlinks/reparse-lignende innhold eller uventede ekstra filer. Dekningen beviser både retry etter manglende forventet payload og bevaring av en expected-name directory mismatch for inspeksjon.

Forrige 0B-slice: DB-005 restore-preflight leser nå endpoint-side intentmarkører read-only. `plan_sqlite_state_restore()` kombinerer nåværende recoverydatabase-segmenter med bounded scan av `.mediasync/installations/<owner>/recovery/*/*.intent.jsonl` under kjente lokale endpointrevisjoner, validerer markerheaderen, deduper etter segment-ID og blokkerer restore når kombinert target-marker/database high-water er nyere enn backupsettet. Dekningen beviser at en target-side marker uten nyere DB-rad stopper restore, og at en marker som matcher en allerede talt DB-rad ikke dobbelttelles.

Forrige 0B-slice: DB-005 post-swap startup-reconciliation er nå synlig i Engine Host startup. `reconcile_committed_sqlite_state_restore_epochs()` scanner terminale restore-epochs etter restore-/compaction-recovery men før writable SQLite åpnes, validerer committed/rolled-back markører, rapporterer siste committed restore epoch/backup-sett/state-set hash, og failer lukket på ikke-terminale eller konfliktende epochmapper. `build_engine_host_runtime()` eksponerer rapporten som `state_restore_startup_reconciliation` på runtime og `ENGINE_HOST_PIPE_STARTING` payloaden før den vanlige `startup_reconciliation`-rapporten. Dekningen beviser både committed restore restart og rolled-back marker-count.

Forrige 0B-slice: DB-005 har nå en konkret retention policy for interne SQLite state-artifacts. `plan_sqlite_state_maintenance_retention()` bygger en eksplisitt plan for verifiserte backup-sett og terminale restore-/compaction-epochs, holder nyeste artifacts etter policy, beskytter backup-sett som er referert av beholdte restore-epochs, og skipper uferdige eller malformede artifacts. `apply_sqlite_state_maintenance_retention()` sletter bare planlagte artifacts og tilhørende rollbackfiler etter terminal kontrollfilvalidering, og `EngineHostRuntime.prune_state_maintenance_artifacts()` kjører dette bak samme restore-maintenance admission uten å lukke live SQLite-handles. Endpoint-side intentmarkørlesing gjenstår fortsatt.

Forrige 0B-slice: DB-005 har nå en read-only IPC maintenance command for state restore. `RESTORE_STATE_FROM_BACKUP_SET` parses lokal backup-settsti, restore epoch og starttid, `EngineHostIpcService` gjenkjenner kommandoen mens `mutations_enabled` fortsatt er false, og dispatcher via runtime restore-executor til `EngineHostRuntime.restore_state_from_backup_set()`. Vellykket restore returnerer restore-epoch receipt og `host_restart_required`, fordi den gamle catalogdatabasen og dens command receipts med vilje erstattes av backupsettet. Dekningen beviser både fake-executor IPC-semantikk og en real runtime restore gjennom `InProcessIpcClient`. Endpoint-side intentmarkørlesing og post-swap startup-reconciliation gjenstår fortsatt.

Forrige 0B-slice: DB-005 har nå en guarded SQLite compaction epoch for catalog/recovery-paret. `compact_sqlite_state_stores()` kjører `VACUUM INTO` til same-directory tempfiler under restore-maintenance admission, verifiserer SQLite identity/schema/migration/high-water/quick-check/foreign-key evidence og checksum, skriver `state-compaction.intent.json`, bytter begge livefiler med rollbackfiler og skriver `state-compaction.committed.json` først etter at hele paret er aktivt. `recover_incomplete_sqlite_state_compaction_epochs()` ruller uferdige compaction-epochs tilbake før runtime åpner SQLite, startup-eventet eksponerer rapporten, og `EngineHostRuntime.compact_state_stores()` lukker live handles bare etter clean admission. Full muterende Engine Host restore-command med read-only IPC-modus og retention policy er senere etablert; endpoint-side intentmarkørlesing og post-swap startup-reconciliation gjenstår fortsatt.

Forrige 0B-slice: DB-005 har nå runtime-eid restore execution etter clean maintenance admission. `EngineHostRuntime.restore_state_from_backup_set()` kjører `admit_state_restore_maintenance()`, nekter blokkert restore uten å lukke åpne SQLite-handles, lukker host-eide catalog/recovery connections ved clean admission og delegerer deretter til den verifiserte SQLite restore-swappen. Unit-dekningen beviser at en runtime kan restore et backup-sett tilbake over mutert live-state etter handle-close, og at retained in-memory leases stopper restore mens handles forblir åpne. Full muterende Engine Host restore-command med read-only IPC-modus, endpoint-side intentmarkørlesing og post-swap startup-reconciliation gjenstår fortsatt.

Forrige 0B-slice: DB-005 restore-maintenance har nå en konkret admission/quiesce-preflight. `admit_sqlite_state_restore_maintenance()` leser catalog/recovery read-only og returnerer en typed admission report med blocker-koder og counts for aktive runs/run-targets, ikke-terminale command receipts, uleverte outbox-meldinger, aktive resource leases, unresolved target-intent segments og uferdige restore-epochs. `EngineHostRuntime.admit_state_restore_maintenance()` eksponerer samme gate gjennom runtime og legger til blocker for retained in-memory run-target leases. Dekningen beviser både clean admission og samlet blokkering fra aktiv catalog/recovery/epoch-evidence samt runtime-retained leases. Muterende Engine Host restore-command med read-only IPC-modus, endpoint-side intentmarkørlesing, compaction epoch og post-swap startup-reconciliation gjenstår fortsatt.

Forrige 0B-slice: DB-005 restore-epochs har nå startup rollback før SQLite åpnes writable. `recover_incomplete_sqlite_state_restore_epochs()` scanner `state-restore-epochs`, hopper over committed eller allerede rolled-back epochs, validerer `state-restore.intent.json` mot canonical layout-/temp-/rollback-/sidecarstier, ruller uferdige restoreforsøk tilbake til pre-restore catalog/recovery-filer, fjerner upubliserte tempfiler, gjenoppretter sidecar-rollbackfiler og skriver `state-restore.rolled-back.json`. `build_engine_host_runtime()` kjører dette rett etter state-root-oppretting og før `sqlite3.connect()`/migrering, og startup-eventet eksponerer rapporten. Testene simulerer hard avbrytelse midt i andre store-publisering og beviser at neste startup ruller begge DB-ene tilbake i stedet for å åpne et blandet par. Engine Host restore-maintenance command, endpoint-side intentmarkørlesing, compaction epoch og post-swap startup-reconciliation gjenstår fortsatt.

Forrige 0B-slice: DB-005 har nå en første guarded SQLite restore-swap for det verifiserte catalog/recovery-settet. `restore_sqlite_state_backup_set()` og `apply_sqlite_state_restore_plan()` bygger på restore-admission-planen, kopierer hvert backupmedlem til same-directory tempfiler, re-verifiserer SQLite-evidence, skriver `state-restore.intent.json`, bytter begge live storefiler med separate rollbackfiler under `state-restore-epochs/<restore-epoch-id>/`, flytter stale SQLite sidecars bort fra live-navnene, re-verifiserer de publiserte targetfilene og skriver `state-restore.committed.json` først etter at hele settet er på plass. Integrasjonstesten dekker både vellykket restore av begge DB-er og en simulert feil ved andre store-publisering som ruller catalog tilbake i stedet for å etterlate et blandet par. Full Engine Host quiesce/maintenance-wiring, startup resume/rollback av uferdige restore-epochs, endpoint-side intentmarkørlesing, compaction epoch og post-swap startup-reconciliation gjenstår fortsatt.

Forrige 0B-slice: DB-005 backup-settgrensen har nå restore-admission med target-intent high-water. Manifest schema v2 registrerer unresolved target-intent count og `target_intent_high_water_utc` fra recovery-backupen, og `plan_sqlite_state_restore()` verifiserer hele backupsettet, bygger en typed restore-plan for catalog/recovery-medlemmene og avviser automatisk restore når nåværende recoverydatabase har nyere unresolved target-intents, også ved samme timestamp men høyere count. Selve restore-swapen, compaction-epoch, quiesce/maintenance-wiring, endpoint-side intentmarkørlesing og full startup-reconciliation etter swap gjenstår fortsatt.

Forrige 0B-slice: DB-005 har nå en konkret SQLite backup-settgrense for det eiergodkjente to-databasevalget. `create_sqlite_state_backup_set()` skriver `backup-set.intent.json`, bruker SQLite Online Backup for `catalog.sqlite` og `recovery.sqlite`, inspiserer backupfilene read-only for store identity, schema/migration high-water, `quick_check`, `foreign_key_check`, page count, størrelse og SHA-256, og publiserer et canonical `backup-set.manifest.json` med combined state-set hash. `verify_sqlite_state_backup_set()` avviser manglende, manipulerte eller blandede catalog/recovery-filer fra en annen epoch før de kan brukes som restorable set. Restore-swap, compaction-epoch, quiesce/maintenance-wiring og restoreblokkering for nyere uavklarte target-intents gjenstår fortsatt.

Forrige 0B-slice: HostLocator-adopsjon bevarer nå strukturerte live-rejections. Launcherens `--local-preview-status` behandler bare timeout, malformed/no response og `ENGINE_HOST_UNAVAILABLE` som stale-publication-kandidater; en live GUI/Engine Host-respons som avviser adoption med identitet, remote-client, rolle, protocol/schema eller annen strukturert rejection returneres til caller uten å rydde HostLocator-publiseringen eller starte en replacement host. Unit-regresjonen dekker `CLIENT_IDENTITY_MISMATCH` og beviser at publikasjonen står igjen; IPC-handshake- og Win32 named-pipe-testene dekker de underliggende same-user/remote/protocol rejection-grensene. Full separat wrong-SID/non-interactive lab gjenstår fortsatt.

Forrige 0B-slice: Win32 transferchild-containment har nå host-exit/orphan-prosessbevis. `tests/integration/test_process_supervisor_live.py` starter en separat helper-host som oppretter en ekte sovende transfer-child gjennom `Win32JobObjectTransferSupervisor`, parent-testen holder en uavhengig monitorhandle til childen, helperen avsluttes abrupt med `os._exit()` uten høflig `ContainedTransferProcess.close()`, og testen verifiserer at Job Object kill-on-close terminerer childen innen timeout. Dette dekker Engine Host-krasj/forced-upgrade-formen der OS lukker eierprosessens jobhandle; bredere Robocopy crash-matrise og live reparse-race lab gjenstår fortsatt.

Forrige 0B-slice: HostLocator-publisering har nå bounded heartbeat/freshness. Engine Host skriver `heartbeat_utc` i `engine-host.locator.json` ved publish, holder publikasjonen fersk med en egen lifecycle-bundet heartbeat-loop, stopper loopen før cleanup og rydder fortsatt bare den nyeste publikasjonen den selv eier. `load_matching_live_local_engine_host_publication()` avviser og guarded-clearer descriptor-matching publikasjoner med for gammel eller urimelig fremtidig heartbeat, mens eldre v1-publikasjoner uten heartbeat fortsatt aksepteres som ukjent freshness og må bevises av eksisterende pipe-probe. Unit- og IPC-testene dekker optional heartbeat parsing, stale/future freshness rejection, guarded refresh/cleanup og launcher HostLocator payload. Wrong-SID og full non-interactive lab gjenstår fortsatt.

Forrige 0B-slice: HostLocator-adopsjon har nå en konservativ process-liveness fast path. `load_matching_live_local_engine_host_publication()` brukes av launcher, GUI og trigger-client, matcher fortsatt descriptor før adopsjon, og rydder en matching publication uten pipe-timeout når den registrerte Engine Host-PID-en er definitivt død; ukjent PID-status faller fortsatt tilbake til eksisterende live pipe-probe. Unit-testene dekker live/ukjent, definitivt død og mismatch-bevaring, mens IPC-integrasjonen viser at live PID + død pipe fortsatt går gjennom guarded stale-clear path. Heartbeat/freshness hardening er senere etablert; wrong-SID og full non-interactive lab gjenstår fortsatt.

Forrige 0B-slice: Robocopy staging-faults etterlater ikke lenger en retry-blokkerende expected-only attempt-inbox. `RobocopyStagingTransferAdapter` rydder en tom eller manifest-forventet, uncommitted inbox etter containment failure, timeout, fatal/invalid exit, konfigurasjonsfeil eller source-hash mismatch, slik at samme operation kan prøves på nytt uten manuell temp-opprydding; cleanup nekter fortsatt å slette uventede filer, kataloger, symlinks/reparse-lignende entries eller andre manifestavvik, som bevares for inspeksjon. Host-exit/orphan containment er senere etablert; bredere Robocopy crash/fault-injection og live reparse-race lab gjenstår fortsatt.

Forrige 0B-slice: Engine Host rydder nå opp sin egen HostLocator-publisering ved host-exit. `run_engine_host()` beholder den eksakte `LocalEngineHostPublication` etter publish og kaller guarded clear i `finally`, slik at normal stopp, tidlig startup-feil eller bounded host-exit ikke etterlater en adopterbar stale `engine-host.locator.json`; cleanup sletter fortsatt bare en identisk publikasjon og bevarer en nyere/mismatchet publikasjon fra en annen host. Unit- og IPC-integrasjonstestene beviser både cleanup etter host-exit og at adoption fortsatt fungerer mens publikasjonen er live. Process-liveness fast rejection og heartbeat/freshness er senere etablert.

Forrige 0B-slice: Win32 transferchild-supervisoren har nå live kill-on-close/orphan-prosessbevis. `tests/integration/test_process_supervisor_live.py` starter en ekte langlevende Python transfer-child via `Win32JobObjectTransferSupervisor`, holder en uavhengig monitorhandle, lukker supervisorens `ContainedTransferProcess` og verifiserer at Job Object kill-on-close terminerer childen innen timeout uten å etterlate en orphan. Abrupt host-exit containment er senere etablert; bredere Robocopy crash/fault-injection-matrise gjenstår fortsatt.

Forrige 0B-slice: Robocopy attempt-resultatet har nå typed exit-bitklassifisering og profile threshold guard. `RobocopyResult` binder exit code, nonfatal/fatal-kategori, copied/extra/mismatch/flagg, executable path/version, command hash, environment hash, manifest hash og loggsti; profiler kan ikke løfte `success_max_exit_code` over Robocopys faste 0-7 nonfatal-grense; negativ/invalid og 8+ fatal exit avvises; og en nonfatal extra-bit kan bare ende i staging når inboxen fortsatt matcher batchmanifestet nøyaktig. Expected-only retry cleanup er senere etablert; bredere live fault-injection gjenstår fortsatt.

Forrige 0B-slice: Lokal HostLocator-publisering er nå herdet mot reparse-/symlink-kontrollstier og tempkollisjoner. `load_local_engine_host_publication()` og `publish_local_engine_host_publication()` reparse-guarder `engine-host.locator.json` før adopsjon eller publisering, publisering bruker en randomisert no-overwrite tempfil, og en eksisterende tempkollisjon bevares i stedet for å bli overskrevet eller slettet. Engine Host cleanup-on-exit, process-liveness og heartbeat/freshness hardening er senere etablert.

Forrige 0B-slice: ReparseGuard avviser nå post-inspection reparse-swap i den handle-baserte final-path-sjekken. Etter at en path-chain først har sett ren ut, feiler `require_resolved_under_root()` fortsatt lukket hvis root eller checked path rapporteres som reparse point i final-path-inspeksjonen, og `tests/unit/test_reparse_guard.py::test_reparse_guard_rejects_reparse_swap_after_clean_chain_inspection` beviser denne raceformen. Bredere live reparse-/fault-injection-evidence gjenstår fortsatt.

Forrige 0B-slice: Persisted endpoint identity binding er nå koblet fra catalog til lokal lease acquisition. Catalog migration 22 legger nullable control/root/owner/checksum identity-felt på `endpoint_revisions`, `SqliteEndpointRootResolver` returnerer `EndpointRootDescriptor`, og `LocalResolvingEndpointLeaseAuthority` validerer lagret control-area, root identity hash/algorithm, owner/epoch og marker checksum mot `.mediasync/endpoint.json` etter OS-lock og før fencing-token/resource-lease issuance. Bredere live reparse-/fault-injection-evidence gjenstår fortsatt.

Forrige 0B-slice: ReparseGuard identity-evidence er nå flyttet inn i den delte adaptergrensen. På Windows åpner `LocalFilesystemReparsePathProbe` eksisterende paths med `FILE_FLAG_OPEN_REPARSE_POINT`/`FILE_FLAG_BACKUP_SEMANTICS`, henter `GetFileInformationByHandle` volume/file-ID og `GetFinalPathNameByHandleW` final-path, og `require_resolved_under_root()` bruker handle-resolved final paths før staging, recovery-verification og final commit stoler på en parent. Final commit ruter nå path escape-avvisning gjennom samme guard. Bredere live reparse-/fault-injection-evidence gjenstår fortsatt.

Forrige 0B-slice: Live Robocopy-evidence er nå lagt til som en trygg temp-only integrasjonstest. Den faktiske production-adapteren resolver `Robocopy.exe` via Windows systemkatalog/final-path, starter child via `Win32JobObjectTransferSupervisor`, skriver batchmanifest før start, kopierer bare fra temp-source til temp-staging, verifiserer manifestert payload og logg, og en separat missing-source run beviser fatal Robocopy-retur under samme contained supervisor uten payloadpublisering. Transferchild kill-on-close/orphan-prosessbevis er senere etablert; bredere fault-injection-matrise, ytelsesporter og bredere live reparse-/fault-injection-evidence gjenstår fortsatt.

Forrige 0B-slice: Robocopy batchmanifest er nå en faktisk adaptergrense. `RobocopyBatchManifest` bygger canonical JSON, `canonical_manifest_hash` og profilhash, persisteres no-overwrite før child start, og `build_robocopy_directory_manifest_command_plan()` bygger én directory-manifest kommando med eksakte filnavn, manifesthash-binding, forbudte switch-valideringer og konservativ 24 000-tegns kommandolinjegrense. `RobocopyStagingTransferAdapter` bruker samme manifest også for dagens enkeltfilsti, og publiserer ikke payload før hele staginginboxen matcher manifestet uten manglende, ekstra, reparse- eller typeavvik. Bredere fault-injection-lab og bred returkodesemantikk gjenstår fortsatt.

Forrige 0B-slice: Lokal ReparseGuard-grense er nå samlet i `adapters/reparse_guard.py`. Staging, Robocopy-arvet staging, final recovery-verification og final commit bruker samme guard til å avvise reparse endpointrøtter og eksisterende path-kjeder før filsystemlesing eller mutasjon, samtidig som lazy kontrollobjekt-suffixer fortsatt kan opprettes deterministisk. `SafePath` reserverer nå `.mediasync` som brukerrelativt kontrollnavn. Handle-/file-ID-basert final-path proof og persisted endpoint identity binding er senere etablert; bredere live reparse-/fault-injection-evidence gjenstår fortsatt.

Forrige 0B-slice: Robocopy transfer-wiring fikk en produksjonsformet, opprinnelig opt-in enkeltfil-stagingadapter. `RobocopyStagingTransferAdapter` bygger typed Robocopy-argv fra source-parent, staginginbox, eksakt filnavn og trygg profil, validerer forbudte switcher både før og etter Windows command-line parsing, resolver `Robocopy.exe` via Windows systemkatalog/final-path-sjekk, og starter transferen gjennom `Win32JobObjectTransferSupervisor` slik at child må være contained før resume. Engine Host og launcher kan velge `--run-executor-staging-backend`; Robocopy er nå produksjonsdefault og `local-file` krever eksplisitt override. Batchmanifestgrensen, live temp-only adapter/supervisor-smoke, handlebasert ReparseGuard identity-evidence og persisted endpoint identity binding er senere etablert; bredere Robocopy fault-injection og live reparse-/fault-injection-evidence er fortsatt pending.

Forrige 0B-slice: Win32 transferchild containment er nå løftet fra policy-skjelett til produksjonsformet adapterflate. `build_transfer_child_launch_plan()` tillater bare skjult, unelevated, no-shell, tom handleliste og minimal environment, mens `Win32JobObjectTransferSupervisor` oppretter child suspended, lager kill-on-close Job Object, assigner før resume, terminerer suspended child ved containment-feil og beholder process/job-handles til attempt-livssyklusen kan avsluttes. Robocopy command/profile-/manifestwiring, live temp-only containment-smoke og live kill-on-close/orphan-prosessbevis er senere etablert; bredere fault-injection-evidence er fortsatt pending.

Forrige 0B-slice: Task Scheduler local advanced-policy boundary er nå eksplisitt i applikasjonslaget. Same-user definition builder/hash/staging avviser `PASSWORD`, `S4U` og logged-off run requests før desired state blir claimbar eller COM-adapteren kan forsøke apply, slik at lokal preview ikke halvveis aksepterer unsupported credential-/nettverksmodus. Full password/S4U enablement, signed/clean-VM releasebevis, production ReparseGuard og Robocopy transferchild-integrasjon er fortsatt pending.

Forrige 0B-slice: Task Scheduler delete/orphan cleanup er nå en bounded install-folder sweep. Registry-porten kan liste og slette Task Scheduler-oppgaver, pywin32-COM-adapteren gjør idempotent `DeleteTask`, og Engine Host startup-/intervalpumpen kjører en egen orphan-pass med cursor/count-telemetri. Sweepen sletter bare oppgaver der path, protocol-argv, installasjons-ID og binær matcher samme installasjon mens schedule mangler; ukjent argv/eier/path/binærdrift blokkeres fortsatt uten blind delete. Advanced password/S4U-valg, signed/clean-VM releasebevis, production ReparseGuard/transferchild og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Local empty-directory quarantine cleanup er nå journalført. `RecoveryObjectCleanupPort` og `cleanup_next_run_target_recovery_object()` rydder bare `CATALOG_RECORDED` `DIRECTORY_EMPTY`-operasjoner med `quarantine_object_id`, den lokale adapteren validerer manifest og tom payload før den fjerner `.mediasync/objects/quarantine/<operation-id>.*`, executor-cycle har ny `RECOVERY_OBJECT_CLEANED` action før target completion, og run completion teller både `CATALOG_RECORDED` og `CLEANED`. Bredere retention-sweeps, production ReparseGuard/transferchild, user repair-workflow og fault-injection er fortsatt pending.

Forrige 0B-slice: Quarantined empty-directory restore er nå koblet inn i den eksplisitte preserved-old-target recoveryhandlingen. `restore_next_run_target_preserved_old_target()` godtar nå `DIRECTORY_EMPTY` bare når `quarantine_object_id` finnes, den lokale final-commit-adapteren validerer quarantine-manifest/payload før den no-overwrite gjenskaper final-katalogen, og SQLite final-commit-testen journalfører restore som terminal `CANCELLED` med `RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED`. Bredere retention-sweeps, production ReparseGuard/transferchild, user repair-workflow og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Lokal empty-directory quarantine er nå koblet inn i executorens commitløype for `DIRECTORY_EMPTY` target-preconditions. Staging binder tom-katalog-bevis, `JournaledFinalCommitPort` bruker preservation-porten til å flytte den tomme katalogen til `.mediasync/objects/quarantine/<operation-id>.payload` med manifest før verified staging commit, og SQLite executor-cycle beviser sealed-plan flyt fra sourcefil via quarantine, final insert, catalog handoff, run completion og lease release. Full egen directory state machine, cleanup/retention for quarantine, production ReparseGuard/transferchild, user repair-workflow og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Terminal recovery flyter nå tilbake til run-state. `complete_run_target_after_terminal_recovery()` gjør `USER_DECISION_REQUIRED` recovery-operasjoner til `RECOVERY_REQUIRED` run-target/run, gjør restored-old-target `CANCELLED` til `CANCELLED` target/run når planlagt arbeid er terminalt, og executor-cycle frigir retained lease med eksplisitt `TARGET_RECOVERY_REQUIRED`/`TARGET_CANCELLED` action. SQLite CAS-overgangene persisterer terminal reason i `result_json`, error counters og `finished_utc`. Full production ReparseGuard/transferchild, user repair-workflow og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Ambiguous preserved-replace final drift journalføres nå som eksplisitt user-decision recovery. En smal `recovery_phase_for_commit_failure()`-klassifisering flytter kjente post-`OLD_TARGET_PRESERVED` drift-/type-/race-koder til terminal `USER_DECISION_REQUIRED` med `last_error_code`, både i ordinær `JournaledFinalCommitPort` og i preserved-resume-broen, mens øvrige commitfeil fortsatt går til `FAILED_RETRYABLE`. Directory quarantine, full production ReparseGuard/transferchild, user repair-workflow og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Preserved old-target restore er nå en eksplisitt recovery-handling. `OldTargetRestorePort` og `restore_next_run_target_preserved_old_target()` kan under en live `MutationPermit` velge neste `OLD_TARGET_PRESERVED` operation, validere preserved versionmanifest/payload, gjeninnsette gammel target med no-overwrite når final path er tom, nekte å overskrive en endret finalfil, og journalføre at replacementen er avbrutt som terminal `CANCELLED` med `RUN_TARGET_PRESERVED_OLD_TARGET_RESTORED`. Directory quarantine, full production ReparseGuard/transferchild og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Preserved versioned replacement kan nå fullføres når finalfilen mangler etter `OLD_TARGET_PRESERVED`. Den lokale replace-adapteren validerer fortsatt versionmanifestet og preserved payloaden mot den gamle target-fingerprinten, revaliderer staginghashen, avviser symlink/typekonflikt og gjenoppdukket target, og gjør deretter en no-overwrite insert av verifisert stagingpayload til final path før eksisterende journalbro registrerer `FILESYSTEM_APPLIED`, `FINAL_DURABLE` og `FINAL_VERIFIED`. Directory quarantine, full production ReparseGuard/transferchild og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Engine Host runtime eier nå en resolver-backed lokal final-commit adapter for executor-cycle. `LocalResolvingFinalCommitAdapter` henter targetroot fra catalog endpoint-revision ved commit-tid, revaliderer `MutationPermit` mot den retained live leasen før filsystemberøring, gjør absent-target insert uten `.mediasync_test_root` via temp+fsync+hardlink no-overwrite, og delegerer preserved `MATCH_FINGERPRINT` replacements til versioned replace-adapteren. `EngineHostRuntime.run_executor_cycle()` faller nå tilbake til runtime-eide staging/final/preservation-porter når callers ikke injiserer porter, og SQLite executor-cycle-testen bruker runtime-defaultene for real local insert. Restore når final mangler eller er tvetydig, directory quarantine, full production ReparseGuard/transferchild og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Lokal staging støtter nå `MATCH_FINGERPRINT`-preconditions for versioned replacement. `LocalFileStagingTransferAdapter` validerer at den eksisterende målfilen er en vanlig fil under endpointroten, binder dens byte count/content hash som `expected_target_fingerprint_json`, avviser drift mot allerede bundet target-evidence og holder `DIRECTORY_EMPTY`/`NONE` eksplisitt unsupported. Operation planning bevarer `MATCH_FINGERPRINT` fra en checksum-validerte sealed plan, og SQLite executor-cycle-testen driver en queued run fra sealed plan gjennom real staging, old-target preservation, final replacement, catalog handoff, run-target completion og version payload/manifest-verifikasjon. Restore når final mangler eller er tvetydig, directory quarantine og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Run executor kan nå gjenoppta et `OLD_TARGET_PRESERVED` versioned-replace crashvindu uten å bruke stale lease. Før ny filesystem-mutasjon publiseres et ferskt hash-chained intentsegment for den retained endpointleasen, SQLite CAS-oppdaterer operationens lease/fencing/intentbinding i samme `OLD_TARGET_PRESERVED`-fase, og neste executor-cycle prioriterer preserved replacements, hopper over ny preservation, anvender verifisert stagingpayload under live `MutationPermit`, og journalfører `FILESYSTEM_APPLIED`, `FINAL_DURABLE` og `FINAL_VERIFIED`. Hvis final ikke lenger matcher den bevarte gamle targeten, blokkerer adapteren i stedet for blind replay. Restore når final mangler eller er tvetydig, directory quarantine og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: Startup recovery klassifiserer nå `OLD_TARGET_PRESERVED` som en final-reverify posture i stedet for generisk manuell recovery. Når final-verifieren kan bevise at den nye finalfilen allerede finnes og matcher forventet byte count/content hash, flyttes recoveryoperasjonen trygt fra `OLD_TARGET_PRESERVED` gjennom `FILESYSTEM_APPLIED`, `FINAL_DURABLE`, `FINAL_VERIFIED`, catalog handoff og run-target completion uten å replaye replacement. Hvis final ikke kan verifiseres, blir operasjonen blokkert i `OLD_TARGET_PRESERVED` med bevart `version_object_id`. Executor-side apply når final fortsatt er gammel og matcher preserved-manifestet er nå dekket; restore når final mangler eller er tvetydig er fortsatt pending.

Forrige 0B-slice: Final commit-laget har nå en production-shaped versioned replace path for `MATCH_FINGERPRINT`-operasjoner. `JournaledFinalCommitPort` kan kreve en `OldTargetPreservationPort`, bevare gammel målfil før replacement, registrere `OLD_TARGET_PRESERVED` med `version_object_id` i `recovery.sqlite`, og først deretter anvende verifisert stagingpayload. Den lokale adapteren validerer endpoint-marker, live mutation permit, forventet target-fingerprint, staginghash og drift etter preservation, skriver gammel fil til `.mediasync/objects/versions/<operation-id>.payload` med manifest, og nekter replacement hvis final endres mellom preservation og apply. Full planmaterialisering av version/quarantine-objektroller, restore når final mangler eller er tvetydig, directory quarantine og bredere fault-injection er fortsatt pending.

Forrige 0B-slice: `external_resource_state` har nå konservativ startup-requeue for claimed Task Scheduler resources fra eksplisitt inaktive owner-instance IDs. Requeue-en er bounded, CAS-er claim-generation, nuller stale claim owner/token/start/TTL, setter diagnostisk `EXTERNAL_RESOURCE_CLAIM_REQUEUED_AFTER_STARTUP`, og Engine Host startup kjører den sammen med øvrig startup reconciliation. Når persistent local-preview host holder mutex og scheduler-maintenance interval er aktivert, legger hosten automatisk til forrige deterministic scheduler-maintenance owner som inaktiv proof. Veggklokke/TTL alene brukes fortsatt ikke til å stjele claims; bredere production scheduler-policy tuning er fortsatt pending.

Forrige 0B-slice: Persistent Engine Host har nå en egen timerdrevet Task Scheduler maintenance loop ved siden av executor-loop-en. Loop-en bruker egen runtime/egne SQLite-forbindelser, kjører eksisterende bounded stage-and-claim-pump med konfigurert page-/claimbudsjett, bærer `stage_next_cursor` videre mellom intervaller når sidebudsjettet er brukt, backer off ved idle/feil, og rapporterer sanitert `ENGINE_HOST_TASK_SCHEDULER_RECONCILIATION`/`FAILED` med `next_interval_ms`. Launcherens `--local-preview-host --reconcile-task-scheduler-resources` kan nå sende interval/max-interval til Engine Host. Bredere production scheduler-policy tuning er fortsatt pending.

Forrige 0B-slice: Task Scheduler pywin32-gatewayen initialiserer og avinitialiserer nå en COM apartment rundt production `Schedule.Service` load/apply-kall, samtidig som injiserte fake-gatewayer fortsatt kan brukes uten pywin32. Load-pathen bevarer adapterfeil fra COM apartment-init i stedet for å pakke dem som generisk load-feil, og unit-testene dekker custom apartment, default pywin32 apartment og init-feil. Bredere production scheduler-policy tuning er fortsatt pending.

Forrige 0B-slice: Executor maintenance loop har nå enkel idle-backoff. Den bruker baseintervallet når arbeid går framover eller blokkerer, dobler intervallet opp til en validert maksgrense når cycle stopper idle eller feiler, og rapporterer `next_interval_ms` i cycle-/failure-telemetri. Launcherens `--local-preview-host` kan sette både baseintervallet og maksintervallet. Bredere production scheduler-policy tuning er fortsatt pending.

Forrige 0B-slice: Engine Host har nå en timerdrevet executor maintenance loop for persistent local-preview host. Loop-en kjører i egen tråd med egen runtime/egne SQLite-forbindelser, bruker validert interval/step-limit, stopper rent med hosten, og rapporterer sanitert `ENGINE_HOST_RUN_EXECUTOR_CYCLE` eller `ENGINE_HOST_RUN_EXECUTOR_CYCLE_FAILED` med `cycle_trigger="INTERVAL"`. `--local-preview-host` kan sette intervallet og Win32 launcher-host-smoken bekrefter nå både HostLocator-query, request-drevet cycle og interval-drevet idle cycle. Adaptive pacing/backoff og bredere production hardening er fortsatt pending.

Forrige 0B-slice: Engine Host kan nå opt-in kjøre en bounded run-executor cycle etter hver vellykkede IPC-request, med validert step-limit og sanitert `ENGINE_HOST_RUN_EXECUTOR_CYCLE` payload. `--local-preview-host` aktiverer dette automatisk når den har local state-root, slik at en trigger-/GUI-request i den persistente HostLocator-publiserte hosten kan etterfølges av runtime-eid executor-framdrift. Win32 launcher-host-smoken bekrefter nå både HostLocator-query og idle executor-cycle etter GUI `QUERY_STATUS`. Dette er request-drevet executor-framdrift; timer/idle background loop er fortsatt pending.

Forrige 0B-slice: Launcherrollen har nå en eksplisitt `--local-preview-host` startup-modus som gjør app-/launcher-entrypointen til den persistente same-user Engine Host-prosessen. Når pipe ikke er eksplisitt gitt, avleder den samme HostLocator descriptor som GUI/trigger bruker, publiserer host locator, tar scoped mutex, bruker local state-root, kjører Engine Host med `--serve-forever`, og kan samtidig be om Task Scheduler startup-reconciliation med en eksplisitt lokal executable path. En Win32 role-smoke starter launcher-hosten som persistent subprocess, venter på HostLocator-publication og får GUI `QUERY_STATUS` gjennom publikasjonen uten pipe-argument. Dette gir en lokal usignert startup action-path uten wrapper rundt Engine Host-rollen; scheduler/executor background loop og HostLocator adoption hardening er fortsatt pending.

Forrige 0B-slice: Engine Host pipe mode har nå et eksplisitt `--serve-forever` valg for long-running same-user lokal IPC, samtidig som bounded `--serve-requests` fortsatt er default for røyk-/pakke-smoker. Long-running loop-en server til prosessen avbrytes, rapporterer `ENGINE_HOST_PIPE_STOPPED` med `stop_reason="INTERRUPTED"` ved kontrollert interrupt, og beholder sanitert `ENGINE_HOST_PIPE_FAILED` ved interne serve-feil. Dette er IPC-livsløpsdelen av production Engine Host, ikke full scheduler/executor background loop ennå.

Forrige 0B-slice: Lokal usignert pakket runtime har nå en 0B-smoke som bygger `MediaSyncHome0B.exe` med Nuitka fra produkt-entrypointen, inkluderer `mediasync_home`, PySide6 og pywin32/`win32com`, verifiserer at protocol-only `--enqueue-trigger-occurrence` fortsatt rutes til trigger-client uten eksplisitt `--role`, kjører pakket Engine Host med Task Scheduler startup-reconciler mot en throwaway local state-root, får pakket GUI-status over samme pipe, laster den opprettede Task Scheduler-tasken tilbake som `IN_SYNC`, og rydder task, smoke-folder og root-folder. `artifacts/0b/packaged-runtime-smoke.json` viser `PASS` med `pywin32==312`, `Nuitka==4.1.3`, 77 filer i disten og cleanup verifisert. Scheduler/executor background loop, HostLocator startup/adoption hardening, signert/ren-VM releasebevis og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Task Scheduler COM-pathen har nå en live 0B-smoke som oppretter en unik disabled task under `\MediaSync Home\<smoke-id>`, laster den tilbake gjennom pywin32-gatewayen, klassifiserer den `IN_SYNC`, og sletter task, smoke-folder og root-folder når smoken opprettet roten. Smoken avdekket og fikset at pywin32 rapporterer manglende folder/task som `DISP_E_EXCEPTION` med egentlig `0x80070002` i `excepinfo`; adapteren gjenkjenner nå begge HRESULT-formene, og `artifacts/0b/task-scheduler-com-smoke.json` viser `PASS` med cleanup verifisert. Long-running production loop, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: `pywin32==312` er nå med i den pinnede lokale dependency-closure-en for Task Scheduler COM-pathen. Auditverktøyet normaliserer pywin32s PSF-lisensmetadata til den allerede godkjente `PSF-2.0` policyformen, dependency- og vulnerability-artifacts er regenerert med 32 scannede dependencies, og en lokal `win32com.client` import-smoke passerer. Long-running production loop, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: App-bootstrapen kan nå rute en protocol-only Task Scheduler-invokasjon med `--enqueue-trigger-occurrence` direkte til trigger-client når `--role` ikke er eksplisitt satt, slik at en lokal usignert pakket app-exe kan være Task Scheduler action path uten wrapper-argumenter. Launcherens local-preview statuspath har også opt-in flagg for å be Engine Host kjøre den bounded Task Scheduler startup-pumpen med en eksplisitt executable path, mens standard statuspath forblir uendret. Long-running production loop, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host har nå en opt-in bounded Task Scheduler startup-pump for local-preview pipe mode. Flagget validerer pipe/state-root/executable path før sideeffektarbeid, velger foreløpig COM-backend, kjører stage-and-claim gjennom runtime-eide SQLite stores før pipe-serving, og eksponerer en sanitert `task_scheduler_reconciliation` startup-payload eller sanitert failure-event med exit code 4. Focused unit/type/lint gates dekker parseren og injected-registry happy path; long-running production loop, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Task Scheduler-adapteren har nå en lazy pywin32 Task Scheduler 2.0 COM-gateway bak registry-porten. Gatewayen kobler til `Schedule.Service` når backend velges, oppretter manglende `\MediaSync Home\<installation-id>`-mapper, materialiserer local-MVP daily triggers fra `configuration_json`, setter `INTERACTIVE_TOKEN` + least-privilege runlevel, `Parallel` multiple-instances, network flagg og `PT0S` unlimited execution limit, registrerer med create-or-update, og laster registrerte tasks tilbake via `ITaskDefinition.Data` metadata. Fake-COM-testene dekker apply/load/missing task og safe drift for manglende action; long-running production loop, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Task Scheduler-siden har nå en gateway-backed Windows registry-adapter og en production `windows_argv` utility hentet fra 0A.5-beviset. Adapteren splitter installasjonsspesifikke task paths i folder/task name, serialiserer Task Scheduler action-argumenter med kanonisk Windows quoting, validerer full action command-line budget før apply, laster gateway-observert task state tilbake til `ObservedTaskSchedulerDefinition`, og gjør uparsebare argumentstrenger til trygg argumentdrift i stedet for blind repair. Dette er adapterkontrakten før pywin32 bindes inn; real Task Scheduler COM-gateway, long-running production loop, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Task Scheduler desired-resource reconciliation har nå en bounded pump. Pumpen pager `schedules` inn i runtime-eid `external_resource_state`, stopper med cursor når pagebudsjettet er brukt, claimer deretter et eksplisitt avgrenset antall pending `task_scheduler` resources med deterministic claim tokens og fullfører/blokkerer dem gjennom ledgeren. Engine Host runtime eksponerer pumpen over sine SQLite stores, mens single-page/single-claim hookene beholdes for målrettet drift. Dette er fortsatt bounded orchestration, ikke en long-running production loop. Real Task Scheduler COM-adapter, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host runtime har nå bounded Task Scheduler desired-resource hooks: en staging-metode leser `schedules` gjennom runtime-eid SQLite store og skriver claimable `task_scheduler` state til `external_resource_state`, og en claim-next metode tar neste pending resource med runtime instance-id, kjører den eksisterende safe reconciliation-porten og fullfører/blokkerer via ledgeren. Dette er fortsatt en eksplisitt bounded runtime hook, ikke en long-running scheduler loop. Real Task Scheduler COM-adapter, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host runtime eier nå SQLite `external_resource_state` store-en sammen med `schedules` og `trigger_occurrences` når local state-root brukes. Det gjør schedulerens desired-state ledger til en ekte runtime dependency i stedet for bare en isolert adapter/testkontrakt. Real Task Scheduler COM-adapter, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Same-user Task Scheduler reconciliation kan nå behandle en claimed `task_scheduler` external resource gjennom ledgeren. Claimed-resource-passet validerer at claimen er for riktig resource type/state, laster schedule, beviser at schedule generation/hash fortsatt matcher claimens desired state, klassifiserer observert task, apply-er bare safe create/update gjennom registry-porten, markerer in-sync via claim token eller blokkerer unsafe drift/stale desired state med error code i `external_resource_state`. Real Task Scheduler COM-adapter, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Same-user Task Scheduler schedule-pages kan nå stages inn i `external_resource_state` som claimable desired resources. Staging-passet bygger samme canonical taskdefinition som driftklassifiseringen, upserter `task_scheduler` resource med schedule-id, definition generation og definition hash, rapporterer invalid desired state uten å stage, og gir den kommende COM-adapteren en konkret claim-kø i stedet for å lese `schedules` direkte. Real Task Scheduler COM-adapter, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Catalog har nå en persistent `external_resource_state` desired-state/claim ledger for eksterne ressurser som Task Scheduler. Tabellen og SQLite-store-en kan upserte monoton desired generation/hash, claim-e neste pending resource med owner/token/ttl, fullføre bare når claim token og desired generation/hash fortsatt matcher, blokkere drift med error code og avvise sen completion etter nyere desired state. Dette gir COM-reconcilerens kommende sideeffekter en CAS-basert katalogautoritet uten falsk cross-system-transaksjon. Real Task Scheduler COM-adapter, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Same-user Task Scheduler reconciliation har nå en bounded applikasjonspass over schedule-pages. Passet validerer page-grenser, bygger ønsket taskdefinition for hver schedule, laster observert task gjennom en smal registry-port, bruker driftklassifiseringen fra forrige slice, apply-er bare safe create/update for owned definitions, og rapporterer blokkert invalid desired state, ukjent argv/owner og binærdrift uten å reparere blindt. Real Task Scheduler COM-adapter, external-resource claim ledger, production Engine Host lifecycle og production replace/version/quarantine var fortsatt pending.

Forrige 0B-slice: Schedule-store-en har nå en bounded keyset-read for Task Scheduler reconciliation. `list_schedules_for_reconciliation` validerer positiv eksplisitt grense, bruker `after_schedule_id` som cursor, returnerer deterministisk `id`-orden og inkluderer disabled schedules slik at en kommende reconciler kan disable owned tasks i stedet for å glemme dem. Real Task Scheduler COM-reconciler, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Same-user Task Scheduler desired-state har nå en deterministisk applikasjonskontrakt før COM-adapteren lander. Den nye task-definition builderen avleder canonical definition hash fra schedule/job/plan/logon/network/triggerfeltene, bygger trigger-client argv med bare protokollflagg (`--enqueue-trigger-occurrence`, installation, schedule id, schedule revision hash, trigger kind og task-definition hash), og klassifiserer reconciliation som create, in-sync, safe owned update eller blokkert drift. Ukjent argumentstruktur, feil owner-binding og binærdrift repareres ikke blindt. Real Task Scheduler COM-reconciler, production Engine Host lifecycle og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host startup recovery-resume kan nå håndtere et `COMMIT_PRECONDITIONS_REVALIDATED` recoveryop uten å replaye filsystemmutasjonen blindt. Startup-reconciliation klassifiserer fasen som `REVERIFY_FINAL`, final-verifieren må bevise at finalartefaktet finnes og matcher forventet byte count/content hash, og resume flytter deretter operationen gjennom `FILESYSTEM_APPLIED`, `FINAL_DURABLE` og `FINAL_VERIFIED` før eksisterende catalog-handoff og target-completion path fullfører runnen. Hvis finalartefaktet ikke kan verifiseres, blokkeres resume fortsatt for manuell/trygg recovery. Real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor kan nå gjenoppta et `COMMIT_INTENT_RECORDED` recoveryop etter restart ved å publisere et nytt chained intentsegment for den ferske endpointleasen før final commit forsøkes. Refresh-steget scanner bounded commit-intent-operasjoner for targetet, validerer stable run/target/endpoint/ownership-binding, laster eksisterende og siste intentsegment, publiserer segment N+1 med previous-hash, CAS-oppdaterer operationens lease/fencing/intentbinding i samme recoveryfase og lar neste cycle fortsette til vanlig final commit. Startup-reconciliation klassifiserer denne posturen som `REFRESH_COMMIT_INTENT`. `COMMIT_PRECONDITIONS_REVALIDATED` og senere faser rebindes ikke; der kan filesystemforsøket allerede ha startet, så reverify/resume for den fasen var fortsatt pending i den slicen.

Forrige 0B-slice: Engine Host startup-reconciliation klassifiserer nå pre-commit recoveryoperasjoner (`PLANNED` til og med `STAGING_VERIFIED`) som `REACQUIRE_AND_REBIND_PRE_COMMIT` i stedet for å blande dem med discard/verified-object pathene. Rapportens anbefalte handling peker eksplisitt på fresh endpointlease + pre-commit operation rebind, mens `COMMIT_INTENT_RECORDED` og senere faser fortsatt sto uten automatisk rebind/resume i den slicen. Gjenopptak fra commit-intent/precondition-faser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine var fortsatt pending.

Forrige 0B-slice: Run executor kan nå re-binde pre-commit recoveryoperasjoner etter Engine Host restart når et `EXECUTING` target har fått en fersk retained endpointlease. Den nye bounded rebind-steget scanner bare `PLANNED` til og med `STAGING_VERIFIED`, validerer at run/target/endpoint/ownership-bindingen fortsatt matcher permiten, CAS-oppdaterer operationens gamle lease/fencing-token til den ferske lease-en og journalfører en hash-chained same-phase recovery event før vanlig staging eller intentpublisering fortsetter. `COMMIT_INTENT_RECORDED` og senere faser rebindes ikke; gjenopptak fra commit-intent/precondition-faser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor kan nå gjenoppta et persisted `EXECUTING` run-target etter Engine Host restart når den in-memory retained lease-registryen er tom, men recoveryoperasjoner ennå ikke er planlagt. Cycle-en velger et persisted `EXECUTING` target, tar en ny endpointlease via lease authority, CAS-oppdaterer targetets gamle `last_lease_id`/epoch/fencing-token til den nye live lease-en, retain-er handle-en og fortsetter til ordinær operation planning med ny fencing-token. Dette dekker crashvinduet etter execution-start før operation planning; rebinding av allerede planlagte/old-lease-bound recoveryoperasjoner, gjenopptak fra commit-intent/precondition-faser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor kan nå gjenoppta et persisted `REVALIDATING` run-target etter Engine Host restart når den in-memory retained lease-en er borte. Execution-start-steget kan, når lease authority er tilgjengelig, hente targetens lagrede leasebinding, ta ny endpointlease, CAS-oppdatere run-targetets gamle `last_lease_id`/epoch/fencing-token til ny live lease, retain-e den nye handle-en og deretter starte normal `EXECUTING`-fase. Dette dekker crashvinduer før target execution start; gjenopptak fra commit-intent/precondition-faser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Local endpoint lease acquisition kan nå rydde en stale aktiv `resource_leases`-rad etter at lokal preview faktisk har tatt endpointets OS-lock og validert marker. SQLite-store-en flytter bare matching local-handle exclusive lease-rader fra `ACQUIRED` til `RELEASED` under transaksjon før ny fencing-token/lease registreres, og mismatch forblir blokkert. Dette fjerner en crash-restart-felle der et dødt diagnostikkrad kunne hindre ny lease selv om OS-locken var fri. Gjenopptak fra commit-intent/precondition-faser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host startup recovery-resume kan nå re-verifisere `FILESYSTEM_APPLIED` og `FINAL_DURABLE` operasjoner før catalog handoff. En ny lokal final-artifact-verifier resolver endpointroten fra catalog, validerer endpoint-relative final path, avviser reparse/symlink-foreldre i lokal preview, leser faktisk finalfil og matcher byte count/content hash mot recoveryfingerprint før recovery flyttes til `FINAL_VERIFIED`; deretter gjenbrukes eksisterende catalog-handoff og target-completion resume. Gjenopptak fra commit-intent/precondition-faser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host startup recovery-resume kan nå også håndtere `FINAL_VERIFIED` operasjoner ved restart. Resume-passet validerer run-target, endpoint-, lease-, ownership- og fencingbinding, henter content-hash fra verifisert final/staging fingerprint, skriver manglende final-file catalog handoff i catalog.sqlite, flytter recoveryoperasjonen til `CATALOG_RECORDED` i separat recoverytransaksjon og fullfører deretter run-target via den eksisterende catalog-recorded completion-broen. Gjenopptak fra tidligere commitfaser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host startup reconciliation kjører nå en bounded, idempotent resume-pass for `CATALOG_RECORDED` recoveryoperasjoner etter klassifisering. Den fullfører bare run-targets som allerede har katalogført alle planlagte operasjoner med matching endpoint-, lease-, ownership- og fencingbevis, summerer bytes fra verifiserte fingerprints og eksponerer `recovery_resume` i startup payloaden uten å replaye filsystemmutasjon. Gjenopptak fra tidligere commitfaser, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Engine Host startup reconciliation klassifiserer nå bounded ikke-terminale recoveryoperasjoner fra `recovery.sqlite` før ny muterende runtimearbeid får stole på tilstanden. Rapporten grupperer restartposture som `DISCARD_UNVERIFIED_INBOX`, `CONTINUE_FROM_VERIFIED_OBJECT`, `REVERIFY_FINAL`, `FILESYSTEM_APPLIED_NEEDS_CATALOG` og `CATALOG_RECORDED_NEEDS_RUN_COMPLETION`, eksponerer funn i startup payloaden og er integrert med eksisterende command receipt/outbox reconciliation. Automatisk idempotent resume etter restart, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor har nå en bounded local staging/transfer step som binder planlagte `COPY_NEW`-operasjoner til den ene source-endpointen i sealed plan, validerer sourcefil og absent target-precondition, allokerer opaque staging object, kopierer til staging payload, fsync-er, verifiserer staginghash mot post-transfer sourcehash og persisterer staging metadata i recovery.sqlite ved hver recoveryfase. Engine Host runtime konstruerer local staging-adapteren fra endpoint-root resolveren, og SQLite cycle-integrasjonstesten driver en queued run fra preflight via real local staging, intent, final commit, catalog handoff, target completion og lease release. Long-running produksjonsloop, restartrecovery for avbrutte operasjoner, real transferchild/Robocopy containment, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor har nå en bounded cycle som sekvenserer eksisterende preflight, execution-start, operation planning, intent publication, final commit, catalog handoff og completion steps for retained live target permits. Unit- og SQLite-integrasjonstestene viser at cycle-en kan drive en queued target fram til den eksplisitte staging/transfer-gapen (`RUN_EXECUTOR_STAGING_STEP_NOT_IMPLEMENTED`), og at en allerede staged operation kan gå hele veien til catalog handoff, target completion og lease release med en konfigurert final commit port. Long-running produksjonsloop, restartrecovery for avbrutte operasjoner, real staging/transferchild, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor har nå en bounded completion bridge som teller `CATALOG_RECORDED` recoveryoperasjoner for et live targetpermit, summerer bytes fra verifiserte fingerprints og bruker den eksisterende run-target CAS-en til å markere target/run `SUCCEEDED`/`COMPLETED`. SQLite-integrasjonstesten starter en ekte catalog-run gjennom preflight/execution-start, registrerer real recoveryoperation i `CATALOG_RECORDED` og fullfører runnen uten caller-supplied counts. Long-running produksjonsloop, restartrecovery for avbrutte operasjoner, real transferchild, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor har nå en bounded catalog-handoff step som velger neste `FINAL_VERIFIED` operasjon for et live targetpermit, skriver final-file catalog handoff i catalog.sqlite og flytter recoveryoperasjonen til `CATALOG_RECORDED` i en separat recoverytransaksjon. Engine Host runtime eier også denne dependencyen, og unit-/SQLite-integrasjonstester beviser happy path, idle, permit mismatch, manglende hash og persistence failure. Long-running produksjonsloop, restartrecovery for avbrutte operasjoner, real transferchild, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor har nå en testet lokal målpipe fra `REVALIDATING` til `EXECUTING`, recovery-operasjonsplanlegging, bounded intentsegmentpublisering, lab final-commit og `SUCCEEDED` target/run completion med persistente completed-tellere. Engine Host runtime eksponerer de nye stegene og full gate passerte etter hver slice. Dette er fortsatt bounded step-komposisjon, ikke en long-running produksjonsloop; catalog-handoff-kjøring per executoroperasjon, restartrecovery for avbrutte operasjoner, real transferchild, Task Scheduler COM-reconciler og production replace/version/quarantine er fortsatt pending.

Forrige 0B-slice: Run executor har nå en bounded preflight pump med eksplisitt maksgrense som kjører executor-steps til idle, blokkering eller step-limit, og holder alle acquired endpoint leases i en runtime lease registry. Engine Host runtime eier registryen og release-er live leases før SQLite-connectionene stenges, slik at `REVALIDATING`-målet ikke mister OS-håndtaket umiddelbart etter preflight. Operation revalidation/transfer/commit og Task Scheduler COM-reconciler er fortsatt pending.

Forrige 0B-slice: Engine Host runtime har nå en resolver-backed local lease authority for executor-bruk: endpoint roots hentes fra `endpoint_revisions.root_uri` i catalog, bare lokale `file:`-røtter aksepteres, ressursnøkkel må matche endpoint, og leaseadapteren resolve-er rooten just-in-time før `mutation.lock` tas og `resource_leases`/fencing-token registreres i recovery. Operation revalidation/transfer/commit og Task Scheduler COM-reconciler er fortsatt pending.

Forrige 0B-slice: Run executor har nå en bounded preflight-step som deterministisk finner neste queued/preflight run med pending target, claimer target til `ACQUIRING_LEASE`, henter live endpoint lease og persisterer `REVALIDATING` med lease-id/ownership-epoch/fencing-token. SQLite-store-en har en queue-selector for neste runnable run. Operation execution, revalidation/transfer/commit-loop og Task Scheduler COM-reconciler er fortsatt pending.

Forrige 0B-slice: Enabled `ENQUEUE_TRIGGER_OCCURRENCE` kan nå resolve ready schedules, registrere/deduplisere `trigger_occurrences`, starte en checksum-bound queued run med occurrence-scoped run-idempotency, markere occurrence som `RUN_ENQUEUED` og skrive run command-effect outbox i samme catalog-transaction. Delivery UUID er fortsatt command-idempotency key, mens scheduled retries deler run via occurrence key. Real Task Scheduler COM-reconciler, production host lifecycle og executor-loop er fortsatt pending.

Forrige 0B-slice: Win32 endpointlock-håndtaket prober nå den faktiske file handle-en med `GetFileInformationByHandle` før en lease regnes som live. Probe-feil låses som tapt handle for resten av lease-en, slik at nye `MutationPermit`-er stoppes etter første lease-loss-observasjon. SMB lease-loss lab-evidence og production lifecycle er fortsatt pending.

Forrige 0B-slice: `catalog.sqlite` har nå en bounded `schedules` desired-state mapping med jobbinding, 0B active sealed-plan/checksum binding, trigger type, desired definition hash, logontype-/nettverksflagg og enabled status. Applikasjonslaget kan resolve en trigger mot schedule og skille ready, missing, disabled og revision-drift; Engine Host runtime eier SQLite-store-en. Real Task Scheduler COM-reconciler og production host bootstrap/lifecycle er fortsatt pending.

Forrige 0B-slice: Triggerforekomster har nå en bounded, catalog-backed `trigger_occurrences` dedup-ledger i `catalog.sqlite` med kanonisk Engine Host-avledet occurrence key, installasjonsglobal deduplication key, logisk payloadhash som ikke inkluderer tilfeldig delivery UUID, replay av samme planlagte forekomst, konflikt ved payload/job/revision-drift og kompakt `effect_dedup_tombstones`-replay etter detaljkompaktering. Production host bootstrap/lifecycle er fortsatt pending.

Forrige trigger-command-slice: Trigger-client-rollen kan nå levere en validert `ENQUEUE_TRIGGER_OCCURRENCE` kommando over samme Engine Host IPC som GUI. Den bygger en strukturert delivery context med schedule-ID, revision hash, delivery UUID, trigger-kind og task-definition hash, bruker delivery UUID som stabil command idempotency key, og får en durable recognized command receipt i local state-root når 0B-mutasjoner er deaktivert. Task Scheduler desired-state og production host bootstrap/lifecycle er fortsatt pending.

Forrige catalog-slice: Final-file catalog handoff ledgeren har nå en bounded `QUERY_CATALOGED_FILES` read-model gjennom SQLite-adapter, Engine Host IPC, GUI pipe action, `EngineClient` og aktivitetspanelpreview uten direkte GUI-SQLite. Slice-en viser de siste katalogførte finalfilene med filter for run/target endpoint, men bred scanner-to-GUI catalog registry, production lifecycle og versjon/karantene/konfliktvisninger er fortsatt pending.

Forrige trigger-discovery-slice: Trigger-client-rollen har nå en bounded local-preview `--query-status` path som bruker HostLocator når `--pipe-name` mangler. Den avleder samme installation/user/state-root-publication som GUI, kobler til publisert pipe som `trigger-client`, returnerer typed `ENGINE_HOST_UNAVAILABLE` når publication mangler, og rydder guarded en matching død publication før avvisning. Dette er bare en non-mutating status/handshake-slice; Task Scheduler desired-state, production host bootstrap/lifecycle og dedup er fortsatt pending.

Forrige Qt-shell-slice: Qt-shellen bruker nå HostLocator når `--pipe-name` mangler: den avleder samme local-preview publication som pipe-action pathen, bygger `EngineClient` mot publisert pipe når recorden matcher, og starter fortsatt trygt frakoblet når ingen matching Engine Host er publisert. Eksplisitt `--pipe-name` har fortsatt presedens, og timeouten følger GUI-rollen. Trigger occurrence enqueue, Task Scheduler desired-state, long-running production host lifecycle, mutex-lifetime for long-running production host og production lifecycle er fortsatt pending.

Forrige GUI-discovery-slice: GUI-rollen bruker nå HostLocator for lokale preview pipe-actions når `--pipe-name` mangler. Den avleder descriptor fra installation/user/state-root, kobler til en matching live `engine-host.locator.json` publication for `QUERY_STATUS` og andre pipe-actions, og returnerer typed `ENGINE_HOST_UNAVAILABLE` når publication mangler eller ikke er levende. Død matching publication ryddes guarded før avvisningen. Trigger occurrence enqueue, Task Scheduler desired-state, long-running production host lifecycle, mutex-lifetime for long-running production host og production lifecycle er fortsatt pending.

Forrige cleanup-slice: Launcherens descriptor-backed local preview rydder nå en matching stale `engine-host.locator.json` når live statusprobe mot publisert pipe feiler, før den faller tilbake til vanlig bounded host-start. Cleanupen er guarded: den sletter bare den deterministiske locatorfilen dersom dagens parsebare innhold fortsatt er samme publication som ble forsøkt adoptert, og ferskere eller mismatchende records beholdes. Trigger occurrence enqueue, Task Scheduler desired-state, mutex-lifetime for long-running production host og production lifecycle er fortsatt pending.

Forrige adoption-slice: Launcherens descriptor-backed local preview leser nå en matchende `engine-host.locator.json`, gjør en live GUI `QUERY_STATUS` mot publisert pipe, og adopterer den allerede kjørende hosten uten å spawne ny Engine Host når statusproben aksepteres. Mismatched, korrupt eller ikke-levende publication faller tilbake til vanlig bounded host-start. Trigger occurrence enqueue, Task Scheduler desired-state, mutex-lifetime for long-running production host og production lifecycle er fortsatt pending.

Forrige publication-slice: Descriptor-backed local preview Engine Host skriver nå en durable `engine-host.locator.json` under HostLocator state-root etter at den scoped same-user mutexen er tatt og før pipe-serving annonseres. Recorden binder `installation_id`, `locator_key`, pipe, mutex, `process_id`, scope/status/schema, og duplicate same-user rejecten skriver ingen publication. Trigger occurrence enqueue, Task Scheduler desired-state, mutex-lifetime for long-running production host og production lifecycle er fortsatt pending.

Forrige mutex-slice: Engine Host kan nå ta en scoped local preview named mutex før den åpner pipe/runtime, og avviser en andre same-user host med en sanitert `ENGINE_HOST_SINGLETON_REJECTED`-event. Launcherens HostLocator-path sender descriptorens mutexnavn til Engine Host, og status-smoken viser at mutexen ble tatt sammen med deterministisk pipe/state-root. Trigger occurrence enqueue, Task Scheduler desired-state, mutex-lifetime for long-running production host og production lifecycle er fortsatt pending.

Forrige HostLocator-slice: Launcherens lokale preview bruker en sentral HostLocator-descriptor når `--pipe-name` ikke er eksplisitt satt. Descriptoren bindes til samme Windows-bruker via SID-hash, gir deterministisk local-only pipe-navn, mutex-navn for kommende singleton og lokal AppData-state-root, og launcher-smoken viser descriptoren i statuspayloaden mens Engine Host fortsatt startes som bounded intern role-prosess.

Forrige SafePath-slice: En sentral pure `SafePath`-validator for endpoint-relative paths avviser absolutte/device paths, drive-relative fragmenter, `..`, tomme segmenter, ADS-kolon, kontrolltegn, tvetydig trailing whitespace/dot og Windows device-navn. Lab final commit-adapteren bruker nå denne grensen før den resolver eller berører final path, samtidig som labmarkør, parent, symlink/reparse og no-overwrite-sjekkene ligger i adapteren. Production final-path handle resolution er senere etablert i den lokale `ReparseGuard`; full production endpoint identity-binding, persisted parent identity og live reparse-race lab står fortsatt utenfor 0B-local-preview scope.

Forrige launcher-slice: Launcherrollen har `--local-preview-status`, som bygger validerte Engine Host-/GUI-launchplaner, starter en bounded Engine Host som separat intern role-prosess gjennom den lokale subprocess-supervisoren og verifiserer readiness via GUI `QUERY_STATUS` over local-only named pipe. Launchplanmiljøet bevarer Windows `SystemRoot` case-insensitivt mens `PATH`, shell, elevasjon og arvede handles fortsatt avvises.

Forrige IPC-slice: Same-user lokal IPC har en eksplisitt reconnect-regresjonstest der to separate GUI-statusklienter kobler til samme bounded Engine Host-prosess etter tur. Testen bekrefter at hosten fortsatt lever etter første GUI-frakobling og først stopper etter begge handshake/query-sykluser er servert.

Forrige GUI-slice: PySide-aktivitetspanelet bruker siste `QUERY_PLAN_ENDPOINTS`-kildesnapshot og henter bounded `QUERY_SNAPSHOT_ISSUES`/`QUERY_SNAPSHOT_COVERAGE` via `EngineClient` for en kompakt snapshot-health-preview. GUI viser blokkerende snapshotproblemer og ikke-komplett directory coverage uten å åpne SQLite eller starte en muterende flyt, og språkvelgeren oversetter de nye helseetikettene. Windows-plattform-rendering av 0B-shellen er regenerert i lys/mørk 100/150/200 % med norsk standardrender. Produksjonsmutasjon, scanner, snapshot materialization workflow, full planner workflow, GUI plan approval, executor, full retentionjobb, full desired-state reconciler, real external delivery adapters, recovery-atomisk cross-store handoff/outbox, full lokaliseringsmatrise og real target mutation er fortsatt ikke implementert.

Snapshot-health-GUI-evidens fra forrige slice: `src/mediasync_home/presentation/view_models/snapshot_health.py`, `src/mediasync_home/presentation/view_models/plan_endpoints.py`, `src/mediasync_home/presentation/view_models/localization.py`, `src/mediasync_home/presentation/main_window.py`, `tests/unit/test_snapshot_health_preview_view_model.py`, `tests/unit/test_plan_endpoint_preview_view_model.py`, `tests/gui/test_pyside_shell.py` og `artifacts/0b/gui-shell/`. Tidligere GUI-plan-endepunkt, GUI-språkvelger, GUI-plan-preview, GUI-jobbdetalj, backup-job-detail IPC, backup-/activity-overview, plan-operation, snapshot-health IPC og command-/outbox-/repository-evidens står fortsatt i 0B-raden over.

Tillatte milepælstatuser: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.

ADR-status følger `docs/DECISION_REGISTER.md` og er separat fra milepælstatus.
