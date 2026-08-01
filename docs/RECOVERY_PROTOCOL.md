# Recoveryprotokoll


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Commit-, durability-, intent-, managed-object- og recoveryregler for filer og kataloger.


## Commit- og recoveryinvarianter

### 4.5 Staging, durability, commit og varig recovery

Filsystemet, katalogdatabasen og recoverydatabasen er tre separate failure domains. Commit er derfor en journalført compare-and-swap-protokoll med idempotente overganger.

#### 4.5.1 Operasjonstilstander

```text
PLANNED
  -> SOURCE_VALIDATED
  -> SOURCE_STABILITY_BOUND
  -> TARGET_PRECONDITION_VALIDATED
  -> STAGING_ALLOCATED
  -> TRANSFERRED
  -> STAGING_DURABLE
  -> STAGING_VERIFIED
  -> COMMIT_INTENT_RECORDED
  -> COMMIT_PRECONDITIONS_REVALIDATED
  -> OLD_TARGET_PRESERVED      # bare fallback
  -> FILESYSTEM_APPLIED
  -> FINAL_DURABLE
  -> FINAL_VERIFIED
  -> CATALOG_RECORDED
  -> CLEANED
```

Terminale/avvikende tilstander:

```text
SKIPPED
CONFLICT
DEFERRED
FAILED_RETRYABLE
FAILED_BLOCKED
CANCELLED
ROLLBACK_REQUIRED
USER_DECISION_REQUIRED
```

Hver overgang skal:

- være tillatt fra en eksplisitt foregående tilstand;
- appendes og committes i `recovery.sqlite` før neste irreversible steg;
- inneholde lease/resource key, endepunkt-/planrevisjoner, bare endepunktrelative stier, expected source/target/parent/staging/final fingerprints og durabilitynivå;
- ha idempotent command handler og verifiserbar postcondition;
- referere et immutable, checksummet target-side intentsegment før første irreversible målmutasjon;
- kunne fault-injiseres før og etter hvert filsystemsteg.

Et intentsegment er sekundært recoverybevis for en avgrenset commitbatch, ikke én fil per operasjon. Det skrives under:

```text
.mediasync\installations\<installation-id-short>\recovery\<run-id-short>\segment-<sequence>.intent.jsonl
```

Segmentet skal:

- inneholde kanoniske intentrader for maksimalt 10 000 operasjoner eller 16 MiB, det som nås først;
- bare bruke relative stier, persistente IDs, fingerprints, endpointgenerasjon, `lease_id`, `fencing_token` og plan-/manifestchecksum;
- skrives til unik tempfil, flushes etter endepunktpolicy og publiseres med no-overwrite rename;
- få en separat manifest/header med schema, antall, byte, segmenthash og tidligere segmenthash;
- være uforanderlig etter publisering;
- være `DURABLE` før noen operasjon i segmentet får gå fra `STAGING_VERIFIED` til `COMMIT_INTENT_RECORDED`;
- beholdes til alle refererte operasjoner er terminale, catalog er avstemt og retentionregelen tillater cleanup.

Dette unngår millioner av små kontrollfiler uten å miste sekundært bevis. Segmentet viser hva MediaSync hadde til hensikt; faktisk filtilstand og primær recoveryjournal avgjør hva som virkelig skjedde.

#### 4.5.2 Normal flyt for ny eller endret fil

1. Kontroller `VALID_OWNED`, owner/epoch, global lock, aktiv lease, endpointrevisjon og planchecksum.
2. Revalider kildefilen mot planens source fingerprint, entry type, reparse tag, volume/file-ID-evidence, parentidentity, path-chain-hash og parent case-context; registrer `SOURCE_VALIDATED`.
3. Forsøk å ta `SourceReadGuard` for den aktive filen eller den lille aktive batchen. På støttede lokale/SMB-endepunkter holdes et read-handle som tillater lesere, men blokkerer skrive-/delete-deling gjennom transferen. Dersom dette ikke er pålitelig, bind eksplisitt alternativ policy: `POST_TRANSFER_CURRENT_HASH_REQUIRED` eller `DEFER_UNSTABLE_SOURCE`. Registrer `SOURCE_STABILITY_BOUND`.
4. Revalider måltilstand mot `ABSENT` eller `MATCH_FINGERPRINT`; registrer `TARGET_PRECONDITION_VALIDATED`.
5. Opprett en kort, unik stagingallokering under installasjonens object namespace; registrer `STAGING_ALLOCATED`. Manifestet inneholder original relativ sti, men fysisk objektsti speiler ikke brukertreet.
6. Kopier med supervisert Robocopy bare til stagingallokeringen; registrer `TRANSFERRED`.
7. Lukk Robocopy-håndtak, enumerer allokeringen, normaliser til `<allocation-id>.payload`, reopen og flush etter endepunktpolicy; registrer faktisk durability som `STAGING_DURABLE`.
8. Les kildeidentitet på nytt. Dersom `SourceReadGuard` ikke ga tilstrekkelig bevis, beregn en nåværende source-hash etter transfer. Sikker modus krever `current source hash == staging hash`; mismatch gir `SOURCE_CHANGED_DURING_TRANSFER` og ingen commit.
9. Verifiser staging med eksplisitt assurance/evidens; registrer `STAGING_VERIFIED`.
10. Kontroller at immutable intentsegment er publisert, checksummet, `DURABLE` og bundet til samme owner/epoch/lease/token som permiten; registrer `COMMIT_INTENT_RECORDED`.
11. Revalider owner/epoch/permit/global lock, final path/reparse/parentidentity/case-context, source og targetprecondition; registrer `COMMIT_PRECONDITIONS_REVALIDATED`.
12. Bruk sikreste støttede operasjon:
    - eksisterende vanlig fil, samme volum: `ReplaceFileW` med objektbasert versjonsbackup når profil/API støtter dette;
    - ny fil: same-volume no-overwrite rename/innsetting;
    - fallback: bevar gammel fil som kort versionobjekt med manifest, registrer `OLD_TARGET_PRESERVED`, og rename staging til final.
13. Ved enhver OS-feil: inspiser faktisk final/staging/version, file IDs, manifests og postconditions; returkode alene avgjør ikke state.
14. Registrer `FILESYSTEM_APPLIED`.
15. Bruk write-through rename/flush etter policy og registrer `FINAL_DURABLE` med ærlig durabilitynivå.
16. Verifiser finalfil og registrer assurance separat fra durability; registrer `FINAL_VERIFIED`.
17. Oppdater catalog outcome/audit/read models i separat kritisk transaksjon; registrer deretter `CATALOG_RECORDED` i recoveryjournalen.
18. Frigi `SourceReadGuard`. Fjern bare sikkert overflødig staging og tempobjekter; registrer `CLEANED`. Intentsegment og bevarte objekter ryddes separat etter referanse-/retentionregler.

Robocopy får aldri final path som destinasjon. Bare commitadapteren kan endre final tree.
#### 4.5.3 Krasjvinduer og recovery

| Observasjon etter oppstart | Sikker handling |
|---|---|
| Staging finnes, final er gammel/absent | Verifiser lease, intentsegment og staging; fortsett eller behold gammel/absent final |
| Gammel fil er bevart, final mangler | Fullfør med verifisert staging eller gjenopprett gammel fil |
| Ny final finnes, catalog mangler | Verifiser final og registrer catalog idempotent |
| Final og version finnes | Verifiser begge; slett ingen før state er entydig |
| Intentsegment finnes, lokal journal mangler | Blokker automatisk mutasjon, importer segmentet som sekundært bevis og krev sikker avstemming |
| Lokal journal finnes, intentsegment mangler før commit | Blokker operasjonen; ikke rekonstruer intent optimistisk etter målmutasjon |
| Målprecondition avviker fra både gammel og ny forventning | `USER_DECISION_REQUIRED`; aldri «nyeste vinner» |
| Ingen forventet fil finnes | Blokker og krev brukeravgjørelse |

Recovery må alltid ta endpointlease og få en ny aktuell `MutationPermit` før den endrer noe. En stale databaserad, gammel fencing token eller et gammelt intentsegment gir ikke i seg selv mutasjonstillatelse. Recovery kan gjenbruke bevis og staging, men må binde eventuell ny sideeffekt til den nye permiten i en eksplisitt journalovergang.

#### 4.5.4 Cross-store handoff mellom catalog, recovery og filsystem

Katalogdatabasen og recoverydatabasen skal aldri låses i samme write-transaksjon. Overgangen inn og ut av en muterende run bruker en eksplisitt saga/handoff med én `handoff_id`, retning, payloadschema, canonical payloadhash og idempotente faser.

Den generiske handofftilstanden er monoton:

```text
PREPARED
  -> PEER_COMMITTED
  -> SOURCE_CONFIRMED
  -> COMPLETED
  -> ABORTED | AMBIGUOUS       # bare etter type-spesifikk regel
```

`SOURCE` betyr databasen som opprettet handoffen; `PEER` betyr den andre databasen. Begge sider lagrer samme ID, retning, payloadschema og payloadhash. Domeneobjektet kan ha egne faser, men de skal aldri brukes som erstatning for handoffens generiske avstemmingsstate.

Runstart (`catalog_to_recovery`):

```text
run: CREATED_NOT_READY
catalog handoff: PREPARED
  -> recovery run + peer handoff: PEER_COMMITTED
  -> catalog run: QUEUED/READY + source handoff: SOURCE_CONFIRMED
  -> begge handoffs: COMPLETED ved avstemming
```

1. En kritisk catalogtransaksjon oppretter `run`, `run_targets`, command receipt og `store_handoff=PREPARED`; receipt står i `EFFECT_PREPARED`, og run er ikke kjørbar.
2. Etter commit validerer recoverywriteren payloaden og oppretter matching `recovery_run` + `recovery_handoff=PEER_COMMITTED` i én separat transaksjon.
3. En ny catalogtransaksjon verifiserer matching peercommit, setter run kjørbar, `store_handoff=SOURCE_CONFIRMED` og command receipt `ACCEPTED` i samme commit.
4. Først etter denne transaksjonen kan Engine Host svare varig `ACCEPTED`, ta mutationleases og starte filarbeid.
5. Reconciler markerer begge sider `COMPLETED` når source-confirmation er observert. Dette er housekeeping; run-tillatelsen kommer fra trinn 3, ikke fra en usikker IPC-hendelse.
6. Krasj mellom fasene avstemmes ved oppstart. En ensidig `PREPARED` ferdigstilles eller aborteres etter type-regelen; receipt løftes aldri til `ACCEPTED` uten matching recoverybinding.

Terminal katalogføring (`recovery_to_catalog`):

1. Recoveryoperasjonen når `FINAL_VERIFIED` og oppretter `recovery_handoff=PREPARED` med operation-/run-/plan-/fencingkontekst.
2. En kritisk catalogtransaksjon validerer handoffpayload og skriver idempotent outcome/audit/read model; matching `store_handoff=PEER_COMMITTED` opprettes i samme commit.
3. Recoverywriteren observerer catalogcommit, registrerer `CATALOG_RECORDED` og `recovery_handoff=SOURCE_CONFIRMED` i samme recoverytransaksjon.
4. Reconciler markerer begge sider `COMPLETED`. Dersom krasj skjer mellom 2 og 3, gjenkjenner startup det allerede committede outcome via samme IDs/payloadhash og fullfører uten duplikat.

Type-regler:

- `ABORTED` er bare lovlig før peer har publisert en irreversibel autoritativ effekt, eller etter bevist kompensasjon.
- `AMBIGUOUS` blokkerer nye mutasjoner i berørt scope og krever faktisk fil-/databaseinspeksjon.
- State kan ikke rulles bakover eller overskrives av «siste writer».
- Handoffpayload inneholder bare stabile IDs, schema, hashes, expected phases, lease/fencing og high-water; bulkdata refereres gjennom immutable entities.
- En handler holder aldri catalog-write lock mens den venter på recovery, filsystem eller IPC.
- Reconciliation har bounded retry, audit og én eier. Den kan ikke ligge som en uavgrenset best-effort-bakgrunnsoppgave.

Uenighet som ikke kan avgjøres fra IDs, hashes, fencing tokens, high-water og faktiske filer blir `RECOVERY_STATE_AMBIGUOUS`, ikke «siste database vinner».


#### 4.5.5 Katalogoperasjoner som egne recoveryprotokoller

Katalogoperasjoner skal ikke behandles som «ufarlige sideeffekter». De har egne monotone state machines og typepreconditions.

Opprett katalog:

```text
DIRECTORY_PLANNED
  -> DIRECTORY_PARENT_VALIDATED
  -> DIRECTORY_CREATE_INTENT_RECORDED
  -> DIRECTORY_CREATED
  -> DIRECTORY_IDENTITY_VERIFIED
  -> DIRECTORY_CATALOG_RECORDED
```

- `ABSENT` må fortsatt gjelde rett før oppretting;
- dersom en vanlig fil eller reparse point finnes på stien, er dette `TARGET_TYPE_CONFLICT`, ikke idempotent suksess;
- eksisterende katalog kan bare godtas som retry når identity/postcondition matcher operasjonen.

Katalogmetadata:

```text
DIRECTORY_METADATA_PLANNED
  -> CHILDREN_TERMINAL
  -> METADATA_PRECONDITION_VALIDATED
  -> METADATA_INTENT_RECORDED
  -> METADATA_APPLIED
  -> METADATA_VERIFIED
  -> DIRECTORY_CATALOG_RECORDED
```

Metadata settes etter alle underordnede filoperasjoner, dypeste katalog først, fordi arbeid under katalogen ellers kan endre tidspunktet igjen.

Karantene/restore av katalog:

```text
DIRECTORY_QUARANTINE_PLANNED
  -> DIRECTORY_EMPTY_REVALIDATED
  -> QUARANTINE_INTENT_RECORDED
  -> DIRECTORY_OBJECT_PRESERVED
  -> SOURCE_PATH_REMOVED
  -> QUARANTINE_CATALOG_RECORDED
```

```text
DIRECTORY_RESTORE_PLANNED
  -> RESTORE_TARGET_ABSENT_REVALIDATED
  -> RESTORE_INTENT_RECORDED
  -> DIRECTORY_RESTORED
  -> DIRECTORY_RESTORE_VERIFIED
  -> RESTORE_CATALOG_RECORDED
```

Recovery skal inspisere type, identity, parent, objectmanifest og faktisk final path. Den må aldri tolke «finnes» som tilstrekkelig postcondition når objektet har feil type eller tilhører en annen operasjon.

## 17. Verifisering, durability, versjonering og karantene

### 17.1 Verifikasjonspolicy er ikke resultat

Brukeren velger en policy som bestemmer hvor mye bevis systemet skal hente. Resultatet rapporterer separat hva som faktisk skjedde.

#### Rask

- bare tillatt for eksplisitt ikke-destruktiv nykopi etter policy;
- manifest, type, størrelse og source-/targetpreconditions;
- guard eller metadata før/etter transfer;
- påstår ikke innholdshashverifisering når hash ikke er lest.

#### Balansert — standard

- manifest, størrelse, metadata, source-/targetpreconditions og guard når tilgjengelig;
- full staginghash når en kompatibel nåværende kildehash allerede finnes;
- full kilde + staginghash ved tvil, risikoflagg, replace uten sterk guard eller policykrav;
- replace kan ikke bruke `LOW_ASSURANCE_NEW_COPY_ONLY`;
- resultatteksen gjenspeiler faktisk assurance.

#### Sikker

- full BLAKE3 av nåværende kilde og staging for alle kopierte filer;
- før-/etterfingerprint og source stability binding;
- commit bare ved full match og gyldige owner/lease/preconditions;
- named streams inngår når policyen lover full objekt-ekvivalens.

Ingen policy omgår stagingmanifest, target compare-and-swap, writer-eierskap eller recoveryjournal.

### 17.2 Tre separate resultataxer

```python
class TransferState(StrEnum):
    NOT_STARTED = "not_started"
    TRANSFERRED = "transferred"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssuranceLevel(StrEnum):
    NONE = "none"
    MANIFEST_VERIFIED = "manifest_verified"
    METADATA_VERIFIED = "metadata_verified"
    PRIMARY_STREAM_HASH_VERIFIED = "primary_stream_hash_verified"
    NAMED_STREAMS_VERIFIED = "named_streams_verified"
    FULL_OBJECT_VERIFIED = "full_object_verified"


class DurabilityState(StrEnum):
    NOT_REQUESTED = "not_requested"
    LOCAL_FILE_FLUSH_CONFIRMED = "local_file_flush_confirmed"
    WRITE_THROUGH_REQUEST_CONFIRMED = "write_through_request_confirmed"
    REMOTE_ACK_ONLY = "remote_ack_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerificationResult:
    transfer_state: TransferState
    assurance_level: AssuranceLevel
    source_stability_bound: bool
    source_guard_kind: str | None
    source_hash: str | None
    source_hash_evidence: str | None
    staging_or_target_hash: str | None
    manifest_matches: bool
    metadata_matches: bool | None
    named_streams_verified: bool | None
    full_object_equivalent: bool | None
    accepted_for_operation: bool
    reason_code: str | None


@dataclass(frozen=True)
class DurabilityResult:
    requested_level: str
    achieved_state: DurabilityState
    file_flush_succeeded: bool | None
    write_through_move_used: bool | None
    limitation_code: str | None
```

Transfer, assurance og durability er ortogonale:

- `TRANSFERRED` betyr bare at byte finnes i staging;
- `MANIFEST_VERIFIED` betyr at stagingformen matcher manifestet;
- `PRIMARY_STREAM_HASH_VERIFIED` betyr at den primære datastrømmen som ble lest matcher;
- `FULL_OBJECT_VERIFIED` krever alle egenskaper policyen lover, inkludert named streams når relevant;
- durability beskriver hvilken flush/write-through-forespørsel som faktisk ble bekreftet.

GUI og audit skal aldri oppgradere en lavere claim til «innhold verifisert» eller «fullt verifisert». Lik hash beviser innholdet som ble lest, ikke fysisk varighet på maskinvare/NAS.

### 17.3 Stagingdurability, source binding og commit

Før `STAGING_DURABLE`:

1. Robocopy er avsluttet og prosesshandle lukket/journalført.
2. Kort staginginbox enumereres mot manifest.
3. Payload flyttes til sin opaque `managed_object`-allokering.
4. Hver fil åpnes gjennom kontrollert adapter; buffere flushes når støttet.
5. Endpointlock, owner/ownership epoch, lease og lokal fencing-token valideres.
6. `SourceReadGuard` holdes fortsatt, eller den forseglede fallbackpolicyen produserer nåværende source-hash/postcondition.
7. `VerificationPort` produserer en immutable `VerifiedStagingArtifact` bundet til operation, manifest, object allocation, source-evidens og permitkontekst.
8. Faktisk assurance og durability lagres separat.

Før `FILESYSTEM_APPLIED`:

- `COMMIT_INTENT_RECORDED` er varig i recoveryjournalen, og operasjonens checksummede intentsegment er `DURABLE`;
- source-, target-, parent-, case- og reparse-preconditions revalideres;
- endpointlock er fortsatt eid, targetmarkøren viser samme writer og `ownership_epoch`, og aktiv `MutationPermit` har samme lease/token som recoveryoperasjon og intentsegment;
- staging er representert av en `VerifiedStagingArtifact` bundet til samme plan/manifest/object allocation/epoch/token;
- commitadapteren revaliderer permit, final-path-chain og preconditions i samme adapterkall som utfører no-overwrite insert, `ReplaceFileW` eller journalført fallback;
- ingen caller kan sende en absolutt final path; adapteren mottar endpointrevisjon + relativ sti og løser den gjennom `SafePath`.

Etter filsystemoperasjonen vurderes faktisk state via handles/fingerprints; returkode alene bestemmer ikke om operasjonen skjedde. `FINAL_DURABLE` er journalfasen der vurderingen registreres, ikke i seg selv en garanti om fysisk varighet. Den lokale adapteren flusher en target-side tempfil, verifiserer den, publiserer finalnavnet med `MoveFileExW(MOVEFILE_WRITE_THROUGH)`, reåpner finalfilen og flusher den før `LOCAL_FILE_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED`. Kataloger bruker samme write-through-move og flusher markørfilen før `LOCAL_DIRECTORY_MARKER_FLUSH_AND_WRITE_THROUGH_MOVE_CONFIRMED`. Idempotent katalogreplay som bare finner og re-verifiserer en allerede eksisterende katalog registrerer fortsatt `LOCAL_DIRECTORY_MARKER_FLUSH_CONFIRMED_ENTRY_UNCONFIRMED`. Planen må være bundet til en kontrollert targetprofil som beviser write-through-støtte. Recoveryeventen lagrer `durability_state`, `file_flush_succeeded` og `write_through_move_used`; adapterretur alene er aldri durabilitybevis. `FINAL_VERIFIED` kreves før katalogoutcome. `SourceReadGuard` frigis først etter at nødvendig sourcepostcondition og staging/finalbevis er registrert.

Implementasjonsnote 2026-08-01: Catalog schema 51 lagrer de kanoniske
`TransferState`-, `AssuranceLevel`- og `DurabilityState`-verdiene i separate
kolonner og beholder de eksakte underliggende claimene og final-eventens
flush-/write-through-flagg i `verification_json`. Historiske, tvetydige
`DURABLE`-verdier normaliseres til `UNKNOWN`; de oppgraderes aldri til en
write-through-claim. Ny terminal `SUCCEEDED`-audit krever `TRANSFERRED` og et
assurance-nivå over `NONE`. GUI viser alle tre akser separat og beskriver
`WRITE_THROUGH_REQUEST_CONFIRMED` som en bekreftet forespørsel, ikke som bevis
på fysisk varighet.

### 17.4 Versjoner som opaque managed objects

En bevart tidligere målfil lagres som et kort objekt, ikke under originalt mappetre:

```text
.mediasync\installations\<installation-id>\objects\<shard>\<allocation-id>.payload
.mediasync\installations\<installation-id>\manifests\<allocation-id>.json
```

Objektmanifestet har `logical_role=VERSION` og binder:

```text
allocation_id
job_id + run_id + operation_id
original_relative_path
original fingerprint og file ID-evidens
content hash/assurance når tilgjengelig
owner_installation_id + ownership_epoch
created_utc + retention policy
```

Regler:

- versjonering er en egen journalført mutasjon med endpointlock, aktuell permit/epoch/token og `MATCH_FINGERPRINT`;
- foretrekk dokumentert `ReplaceFileW` med backupobjekt på samme volum når API-/sti-/policyforutsetningene holder;
- fallback bevarer gammel fil i objektstore før ny settes inn og registrerer `OLD_TARGET_PRESERVED`;
- manifestet flushes og checksummes før objektet kan være recoverybevis;
- restore løser original relative path fra manifestet, kjører full SafePath-/owner-/preconditionkontroll og bruker egen recoveryoperasjon;
- retention kan ikke fjerne objekt som refereres av aktiv recovery, unresolved outcome eller hold;
- rydding kjører som separat command/run-target med egen lease, plan, audit og diskplasskontroll.

0B implementation note: preserved versions now carry a canonical SHA-256
self-hashed manifest bound to job/revision, run/target/operation, endpoint
revision/generation, owner/epoch, original fingerprint, creation time and exact
30-day expiry. Catalog migration 44 registers that immutable root atomically
with final-file handoff. Migration 45 creates immutable expiry plans/items,
holds and append-only events. The maintenance worker first resumes any existing
delete journal, otherwise plans due roots, proves the matching recovery
operation is exactly `CLEANED`, rechecks active-job/hold state, acquires a fresh
endpoint permit, verifies the manifest/payload pair and records delete intent
before removing bytes. `FILESYSTEM_DELETED` completes idempotently after a
crash; a partial deletion keeps its intent journal for reconciliation. Archived
jobs, active holds, active/mismatched recovery references, manifest drift and
payload drift all fail closed.

Historical file restore uses a separate catalog journal rather than pretending
to be a backup-run recovery operation. Migration 46 binds one protected source
version to a deterministic rollback object and append-only phases. Under a
fresh endpoint permit the worker verifies source and current final, records
intent, durably preserves current bytes, atomically applies the historical
payload, and verifies the final fingerprint before completion releases the
hold. Existing matching rollback/final postconditions make crashes after
preserve or replace resumable without repeating an unsafe effect. A changed
final, invalid manifest/payload, stale permit, unsafe path or reparse boundary
blocks the operation and keeps protected evidence.

Migration 47 journals the completed restore's rollback lifecycle separately so
undo never rewrites the original restore evidence. Confirmed undo is allowed
only before the bound due time and only while the live final still full-hash
matches the completed restore output. It records `UNDO_INTENT_RECORDED` before
replacement and resumes through `UNDO_APPLIED` and `UNDO_VERIFIED` under a fresh
lease. The rollback pair remains until expiry even after `UNDONE`. Expiry first
verifies the exact pair, records `EXPIRY_INTENT_RECORDED`, then removes payload
before manifest. Restart treats both-missing as completed and can validate and
remove a lone manifest after payload deletion; a payload without its ownership
manifest or any binding drift remains blocked.

Migration 48 makes a displaced empty directory a retained recovery source with
immutable role and canonical `{entry_count: 0, kind: DIRECTORY_EMPTY}`
fingerprint. Successful file publication no longer cleans that quarantine after
catalog handoff. The shared hold and expiry pipeline verifies an empty payload
directory before deletion. Confirmed restore full-hash verifies and preserves
the current file, removes it and recreates the empty directory under a fresh
lease; confirmed undo first proves the restored directory is still empty, then
restores the rollback file with no-overwrite semantics. Filesystem-before-
journal retries accept only those exact postconditions.

### 17.5 Karantene som opaque managed objects og compare-and-swap

All speil-«sletting» bevares i objektstore med `logical_role=QUARANTINE`:

```text
.mediasync\installations\<installation-id>\objects\<shard>\<allocation-id>.payload
.mediasync\installations\<installation-id>\manifests\<allocation-id>.json
```

- destructive coverage, source absence og casekontekst revalideres rett før hver berørt batch/operasjon;
- målfilen må fortsatt matche planens fingerprint, type og parent identity;
- endpointlock, owner/epoch og samme local fencing token holdes gjennom hele operasjonen;
- same-volume rename til opaque object path er foretrukket;
- fallback er kopi → flush/verifiser → manifest/intensjon → revalider → fjern original;
- feil eller mistet lease etter kopi men før fjerning lar original stå;
- tom katalog representeres av et metadata-/restoremanifest etter at underordnede filer er behandlet; full brukersti speiles ikke fysisk i kontrollområdet;
- permanent tømming er en separat retentionoperasjon med recovery/audit — aldri Robocopy `/PURGE`.

### 17.6 Diskplassberegning

Preflight beregnes per målvolum som peak:

```text
peak_required_free =
    maksimal samtidig ikke-committet inbox-/objektnyttelast
  + ekstra byte for fallback-kopi når same-volume rename/replace ikke kan brukes
  + object-manifest-, intentsegment-, database- og loggoverhead
  + sikkerhetsreserve
  - validert, gjenbrukbar objektpayload
```

- Gammel målfil som renames til versionobjekt på samme volum dobbeltelles ikke.
- Rename til quarantineobjekt på samme volum gir normalt ingen ny payload.
- Copy-verify-delete-fallback regner med midlertidig dobbel lagring.
- Beregn per samtidige batch, ikke hele fremtidige plan.
- Standard reserve: minst 5 % eller 10 GiB, størst verdi.
- Re-check før stor batch/fallback/retention.
- Ukjent eller inkonsistent fri-plassrapport blokkerer mutasjon som krever bevis.
- Lokal AppData-kapasitet beregnes separat etter §11.8.

### 17.7 Effektiv verifisering

- Balansert modus fullhasher ikke automatisk alle terabyte når en sterk source guard og lavrisiko nykopi gir tilstrekkelig policybevis.
- Kjent, kompatibel `CURRENT_READ_HASH` eller `USN_CONTINUITY_VALIDATED_HASH` gjenbrukes; bare staging/final leses.
- Metadatarevalidert cache oppgraderes ikke til nåværende innholdsbevis.
- Replace uten sterk source guard krever nåværende kilde- og staginghash før commit.
- Sikker modus viser forventet ekstra I/O.
- Hashing strømmer avgrensede blokker.
- Verifisering kan overlappe på uavhengige ressurser, men ikke aggressivt på samme HDD/NAS.
- Same-volume commit kopierer ikke nyttelast på nytt.
- Verifiseringscache blir gyldig først etter `FINAL_VERIFIED` og er bundet til endpointgeneration, owner/epoch, fingerprint og evidenskind.

### 17.8 Named streams og metadata-portabilitet

Standardpolicy er `PRESERVE_WHEN_PORTABLE_WARN_ON_LOSS`:

- Streams inventeres bare for filer som trenger kopi/erstatning eller streng verifisering.
- Når begge endepunkter støtter dem, bevares og verifiseres navn, størrelse og innhold.
- Når målet ikke støtter dem, kopieres primær datastrøm; GUI/audit viser samlet og per-fil portabilitetstap.
- `BLOCK_IF_NOT_PORTABLE` blokkerer filen.
- `PRIMARY_STREAM_ONLY_WITH_WARNING` er eksplisitt kompatibilitetsvalg.
- `FULL_OBJECT_VERIFIED` brukes aldri når en stream eller lovet egenskap er ukjent/utelatt.
- Sparse-, krypterings-, ACL- og andre filsystemegenskaper følger samme probe → plan → forklar → verifiser/blokker-modell.

### 17.9 Recovery- og retentioninvarianter

- Ingen version-/quarantine-/object cleanup kjører mens mål har aktiv mutationlease eller uavklart recovery.
- Retentionplanen er immutable og bruker target-/object-preconditions; den kan ikke fjerne objekter som dukket opp etter analyse uten ny plan.
- Manglende/ekstra inbox-, object- eller manifestfiler avstemmes, ikke slettes automatisk.
- Intentsegmentkjeden, recoveryjournalen, objectmanifestet og relevante cross-store handoffs må samsvare før cleanup.
- Cleanup er siste idempotente fase og fjerner bare objekter med bevist eierskap til fullført operation/attempt.
- En lagringsflush som ikke kan bekreftes gir ærlig `FINAL_DURABILITY_UNCONFIRMED`; policy avgjør advarsel eller blokkering, aldri falsk «garantert lagret».
- Katalogoppretting, katalogmetadata, tom-katalog-karantene og restore bruker de egne tilstandsmaskinene fra §4.5.5 og samme owner/epoch/permitkontroll.

## Operasjonell recovery og feilinjeksjon

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
