# Endepunkteierskap og kontrollområde


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Bindende regler for writer-eierskap, `.mediasync`, takeover, lock/fencing, endepunktidentitet og kapabiliteter.


## Eierskap og sikkerhetsporter

### 4.1 Endepunktidentitet, eksklusivt writer-eierskap og kontrollområde

Et endepunkt en jobb kan skrive til, registreres eksplisitt med **Registrer som skrivbart MediaSync-endepunkt**. Registreringen oppretter produktmetadata, men kopierer, erstatter eller flytter ingen brukerfiler. En strengt skrivebeskyttet kilde i `multi_target_backup` kan identifiseres uten kontrollområde. I `pair_sync` må begge endepunkter som kan motta data være registrert.

#### 4.1.1 Én writer-installasjon per skrivbart rotområde

Første komplette hjemmeversjon bruker ikke distribuert multi-writer-koordinering. Den bindende modellen er:

> Ett skrivbart MediaSync-rotområde har nøyaktig én autorisert `owner_installation_id` i én monoton `ownership_epoch`.

En annen installasjon eller maskin kan lese, analysere og vise historisk kontrollmetadata, men kan ikke stagingkopiere, versjonere, karanteneflytte, reparere kontrollområdet eller endre final tree. Lokal fencing er tuple:

```text
(ownership_epoch, local_fencing_token)
```

`local_fencing_token` er bare monoton innen samme eierskapsepoke og må aldri sammenlignes globalt mellom installasjoner. En overtatt eller reinstallert writer får ny `installation_id` og høyere `ownership_epoch`; alle permits, intents og workerresultater fra eldre epoke er stale.

#### 4.1.2 Fysisk kontrollstruktur

Kontrollområdet er globalt for endpointidentitet/lås og namespacet per installasjon for arbeidsobjekter:

```text
<writable-endpoint-root>\.mediasync
├── endpoint.json
├── ownership
│   ├── epoch-00000001.json
│   └── epoch-00000002.json
├── locks
│   └── mutation.lock
└── installations
    └── <installation-id-short>
        ├── objects
        │   └── 3f
        │       └── <allocation-id>.payload
        ├── manifests
        │   └── <allocation-id>.json
        ├── recovery
        │   └── <run-id-short>
        │       └── segment-000001.intent.jsonl
        ├── probes
        └── temp
```

Globale objekter:

- `endpoint.json` — aktiv, checksummet markør med endpoint-ID, owner og eierskapsepoke;
- `ownership/epoch-*.json` — immutable eierskapsbevis og takeover-audit;
- `locks/mutation.lock` — global eksklusiv writerlock for hele rotområdet.

Installasjonsspesifikke objekter:

- korte staging-, version- og quarantineobjekter under `objects/`;
- immutable manifester som binder objekt-ID til logisk rolle, original relativ sti, operation/run og innholdsfingerprint;
- bounded target-side intentsegmenter og probeobjekter.

En installasjon skal aldri rydde en annen installasjons namespace automatisk. Reinstallasjon bruker nytt namespace. Adoption av et gammelt namespace er en separat, journalført recovery-/overtakelsessaga.

#### 4.1.3 Markørformat

`endpoint.json` skal minst følge `schema/endpoint-marker.schema.json` og inneholde:

```json
{
  "control_schema_version": 4,
  "endpoint_id": "550e8400-e29b-41d4-a716-446655440000",
  "control_area_id": "550e8400-e29b-41d4-a716-446655440001",
  "owner_installation_id": "550e8400-e29b-41d4-a716-446655440002",
  "ownership_epoch": 7,
  "ownership_mode": "EXCLUSIVE_WRITER",
  "created_utc": "2026-07-15T12:00:00Z",
  "updated_utc": "2026-07-15T12:00:00Z",
  "expected_volume_id": null,
  "expected_share": "server/share/root",
  "root_identity_hash_algorithm": "BLAKE3-256",
  "root_identity_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "latest_ownership_record": "ownership/epoch-00000007.json",
  "canonicalization_algorithm": "JCS-RFC8785",
  "marker_checksum_algorithm": "BLAKE3-256",
  "marker_checksum": "1111111111111111111111111111111111111111111111111111111111111111",
  "application": "MediaSync Home"
}
```

`marker_checksum` er BLAKE3-256 over UTF-8-bytene fra `JCS-RFC8785`-kanonisering av hele objektet uten selve `marker_checksum`-feltet. Algoritme- og canonicalization-feltene inngår i det hashende objektet. JSON Schema-validering skal aktivere formatkontroll for `uuid` og `date-time`.

Markør og eierskapsrecord publiseres med unik tempfil, flush etter endpointpolicy og no-overwrite/same-directory rename etter den dokumenterte kontrollprotokollen. Ved takeover skrives først et immutable nytt epoch-record; deretter publiseres ny aktiv markør. En ukjent eller korruptert markør repareres ikke optimistisk.

#### 4.1.4 Klassifisering før ekskludering eller bruk

En katalog med navnet `.mediasync` klassifiseres før den kan brukes som kontrollområde eller ekskluderes fra snapshot:

```text
ABSENT
VALID_OWNED
VALID_FOREIGN
VALID_READ_ONLY_NEWER_SCHEMA
PARTIAL_CONTROL_AREA
UNKNOWN_EMPTY_DIRECTORY
UNKNOWN_NONEMPTY_DIRECTORY
CASE_ALIAS_COLLISION
CORRUPT_MARKER
```

| Tilstand | Tillatt oppførsel |
|---|---|
| `ABSENT` | Kan registreres etter eksplisitt brukerhandling og writable probe |
| `VALID_OWNED` | Normal kontrollbruk når owner/epoch/root matcher |
| `VALID_FOREIGN` | Read-only; tilby kontrollert overtakelsesveiviser |
| `VALID_READ_ONLY_NEWER_SCHEMA` | Read-only; ingen downgrade eller mutasjon |
| `PARTIAL_CONTROL_AREA` | Blokker mutasjon; åpne recoveryveiviser |
| `UNKNOWN_EMPTY_DIRECTORY` | Krev eksplisitt valg; ikke adopter automatisk |
| `UNKNOWN_NONEMPTY_DIRECTORY` | Hard blokkering; behandle som mulig brukerinnhold |
| `CASE_ALIAS_COLLISION` | Hard blokkering til navnekollisjon er løst manuelt |
| `CORRUPT_MARKER` | Read-only/recovery; ingen automatisk «reparasjon» |

Standardfilteret ekskluderer `.mediasync/**` bare når `VALID_OWNED`, `VALID_FOREIGN` eller `VALID_READ_ONLY_NEWER_SCHEMA` beviser et faktisk MediaSync-kontrollområde. På en ren kilde er en ukjent mappe med dette navnet vanlig brukerdata eller et synlig, blokkerende avvik — den forsvinner aldri stille.

#### 4.1.5 Kontrollert overtakelse

Overtakelse av `VALID_FOREIGN` er en eksplisitt saga:

1. ta global `mutation.lock` uten å stole på lokal database;
2. les markør, alle relevante ownership-records, intentsegmenter og namespaces read-only;
3. bevis at ingen aktiv målmutasjon eller uavklart fremmed recovery pågår;
4. vis gammel owner, siste kjente epoke, kontrollrester og konsekvens;
5. krev eksplisitt brukerbekreftelse og ny full analyse;
6. skriv immutable takeover-intent og nytt `ownership/epoch-N.json` med `N > previous`;
7. publiser ny checksummet `endpoint.json` med ny owner og epoke;
8. opprett ny lokal endpointrevisjon og invalider tidligere mutasjonsplaner;
9. tillat ingen mutasjon før full probe, full analyse og recoveryavstemming er bestått.

Hvis gammel epoke ikke kan bestemmes sikkert, er tilstanden `OWNERSHIP_RECOVERY_REQUIRED`; automatisk overtakelse er forbudt.

#### 4.1.6 Preflight før hver muterende kjøring

Engine Host skal verifisere:

- at rot og final namespace matcher lagret endpointrevisjon;
- at kontrollområdet klassifiseres `VALID_OWNED`;
- at `owner_installation_id` er lokal installasjon og `ownership_epoch` matcher planen/permiten;
- at `endpoint_id`, kontrollskjema, volum-/shareidentitet og root fingerprint stemmer;
- at global `mutation.lock` kan tas og holdes;
- at installasjonens object/manifest/recovery-roots faktisk ligger under riktig kontrollområde;
- at ingen fremmed namespace ryddes eller adopteres;
- at kapabilitetsprofil og fri plass tillater operasjonen;
- at actual final path fra handles fortsatt tilhører registrert rot.

Ved avvik stoppes kjøringen før brukerfiler endres. Kontrollområdet kan bare migreres eller repareres gjennom en eksplisitt, journalført flyt med ny endpointrevisjon.

### 4.4 Eierskap, leases, preconditions og destruktiv sperre

Ingen muterende target-operasjon utføres uten:

- aktiv Engine Host-eierskap;
- kontrollområde klassifisert `VALID_OWNED`;
- matching `owner_installation_id` og `ownership_epoch`;
- lokal run-/resourcelease;
- globalt eksklusivt endpoint lock-handle når endepunktet støtter sikker mutasjon;
- en lokal monoton `fencing_token` bundet til samme eierskapsepoke;
- matching endpointrevisjon/generasjon og planchecksum;
- eksplisitt source- og target-precondition;
- recoveryintensjon før irreversibelt steg;
- en levende, ikke-serialiserbar `MutationPermit` utstedt av ownership-/lease-/pathlaget.

`MutationPermit` binder minst:

```text
installation_id
ownership_epoch
lease_id
resource_key
local_fencing_token
endpoint_revision_id + endpoint_generation
validated root/control handles
tillatt mutasjonsscope
plan/operation binding
```

Den kan ikke opprettes fra IPC, JSON, en rå sti, en boolsk verdi eller en stale recoveryrad. Bare ownership-/leaseadapteren kan utstede den etter vellykket kontrollmarkørvalidering, global OS-lock og recoveryregistrering. Bare commit-, karantene-, versjons- og katalogadapterne kan konsumere den.

#### 4.4.1 Eierskaps- og fencingregel

1. Klassifiser kontrollområdet og verifiser `VALID_OWNED`.
2. Ta global `mutation.lock` på målroten.
3. Les aktiv marker/ownership-record på nytt gjennom validert handle.
4. Avvis dersom owner eller `ownership_epoch` er endret.
5. Recoverywriteren øker lokal token for ressursnøkkelen og registrerer `(ownership_epoch, local_fencing_token)` i én transaksjon.
6. Dersom registreringen feiler, lukkes lock-håndtaket og ingen permit utstedes.
7. Coordinator-, worker- og batchmeldinger bærer begge tokenkomponenter, men kan ikke selv mutere.
8. Rett før sideeffekt validerer adapteren markør, global lock, epoke, permit og lokal token.
9. Lease loss, ownerendring eller høyere eierskapsepoke ugyldiggjør alle eldre permits, meldinger, intents og staging som mutasjonsautorisasjon.

En lokal token fra epoke 12 kan aldri sammenlignes som «nyere» enn en token fra epoke 13. Epoken avgjør først; lokal token avgjør bare innen epoken.

#### 4.4.2 Degradert modus uten pålitelig endpointlock

Et endepunkt uten pålitelig eksklusiv kontrollås får ikke vanlig `UPDATE_FORWARD`. Den eneste mulige muterende degraderingen er:

```text
COPY_NEW_ONLY_NO_REPLACE
```

Dette er bare lov når alle punkter er bevist og dokumentert av endpointprofilen:

- målsti var `ABSENT` ved analyse;
- filen er fortsatt fraværende rett før innsetting;
- adapteren støtter sikker no-overwrite-innsetting;
- ingen eksisterende fil, katalog eller metadata erstattes;
- ingen versjonering, karantene, retention, speiling eller toveisoperasjon kjøres;
- automatisk kjøring er av som standard og GUI viser redusert sikkerhetsnivå.

Hvis sikker no-overwrite-innsetting ikke kan bevises, er endepunktet read-only. «Best effort overwrite» er forbudt.

#### 4.4.3 Target-preconditions

Target-preconditions bruker compare-and-swap-semantikk:

- `ABSENT` — final path må fortsatt mangle;
- `MATCH_FINGERPRINT` — eksisterende mål må fortsatt matche planens fingerprint, type, file-ID-evidence, parentidentity og case-kontekst;
- `DIRECTORY_EMPTY` — katalogen må fortsatt være forventet tom katalog;
- `NONE` — bare lovlig for ikke-muterende planrader.

Dersom en annen app, bruker eller maskin endrer målet etter analyse, skal MediaSync ikke overskrive endringen. Operasjonen blir `TARGET_CHANGED_SINCE_ANALYSIS`; resten kan fortsette bare når avhengigheter og sikkerhet tillater det.

#### 4.4.4 Destruktiv sperre

Karantene-/sletteoperasjoner blokkeres når minst ett av følgende gjelder:

- skann/coverage er ufullstendig eller relevant katalog er volatile/unreadable;
- rot, kontrollklassifisering, owner, eierskapsepoke eller endpointidentitet er endret;
- endpointlease eller global lock mangler/mistes;
- terskler for count, byte eller prosent overskrides;
- mer enn 10 % av tidligere observerte kildefiler plutselig mangler;
- I/O-, database- eller nettverksfeil gjør absence proof usikkert;
- baseline mangler for toveis sletting;
- tids-, case-, hash-evidens- eller kapabilitetssemantikk er ukjent;
- source/target/parent-precondition avviker;
- reparse point, path chain eller final path er endret;
- planen er bygget av inkompatibel planner/operation schema.

Før fase 40–50 skal motoren gjøre destruktiv revalidering:

- valider owner/epoch/global lock/leases og endpointidentity på nytt;
- verifiser coverage og at hver planlagt target-extra fortsatt mangler på kilden;
- revalider målobjektets fingerprint før quarantine;
- blokker alle gjenværende destruktive operasjoner i berørt scope dersom én relevant fraværspåstand ikke kan bevises;
- allerede verifiserte ikke-destruktive commits kan fullføres sikkert uten at drift tolkes som slettetillatelse.

Standard terskler:

```text
Maks karantenefiler uten ekstra bekreftelse: 100
Maks karantenebyte uten ekstra bekreftelse: 50 GiB
Maks andel av målinnhold: 5 %
Plutselig kildetap som blokkerer destruktive handlinger: 10 %
```

Terskler kan justeres per jobb, men hard safety gates kan ikke deaktiveres.

## 12. Endepunktoppdagelse, identitet og kapabiliteter

### 12.1 Arkitektur og probeprinsipp

Endepunktoppdagelse er en adaptertjeneste under Engine Host. GUI-et velger en plassering, men kan aldri selv avgjøre identitet, skriveevne eller sikkerhetsnivå. Resultatet lagres som en uforanderlig `endpoint_revision` med:

- normalisert visningssti og kanonisk Windows-root;
- rotidentitet, volum-/shareidentitet og fysisk enhetsbevis med tillitsnivå;
- kapabilitetsprofil og `capabilities_hash`;
- probeversjon, tidspunkt, resultat og årsakskoder;
- hvilke tester som var read-only og hvilke som brukte kontrollområdet;
- hvilken build/API-adapter som produserte profilen.

Probe utføres i to nivåer:

1. **Read-only probe:** identitet, type, filsystem, grenser, case-/reparseegenskaper, tilgjengelighet og fri plass. Denne kan brukes på kilder.
2. **Controlled writable probe:** bare for et registrert skrivbart endepunkt og bare inne i `.mediasync`. Den tester oppretting, flush, rename/replace, eksklusiv lock og opprydding uten å berøre brukerfiler.

En probe skal være idempotent. Midlertidige testobjekter får installasjons- og probe-ID, journalføres og ryddes gjennom recovery ved krasj. En delvis probe gir aldri optimistiske kapabiliteter.

### 12.2 Lokale og flyttbare volumer

Samle og lagre når Windows kan dokumentere det:

- volum-GUID, serienummer og filsystemnavn;
- fysisk enhetsnøkkel og tillitsnivå;
- stasjonsbokstav bare som visnings-/fallbackinformasjon;
- removable/fixed/nettverkstype;
- total og fri plass;
- maksimal filstørrelse, komponentlengde og stilengde;
- tidsstempelpresisjon og opprettelsestid;
- case-modus, inkludert case-sensitive kataloger;
- reparse-tag-inspeksjon og final-path-oppløsning fra åpne handles;
- same-volume rename, no-overwrite insert og replace;
- filflush, write-through rename/replace og rapportert durabilitynivå;
- fil-ID og vurdert stabilitet;
- attributter, named streams, sparse files, hardlinks og kryptering.

Stasjonsbokstav eller visningssti er aldri endepunktidentitet alene. Volumidentitet kan heller ikke alene bevise at den valgte roten er uendret; root-handle/final path og endepunktmarkør inngår i kontrollen.

### 12.3 SMB/NAS

- Aksepter UNC som `\\server\share\folder`.
- En mapped drive kan velges i GUI, men lagres som underliggende UNC når den kan løses sikkert.
- Identifiser normalisert server/share, konfigurert relativ rot, endepunktmarkør og observerbar server-/shareidentitet.
- Ikke anta NTFS-presisjon, stabile fil-ID-er, named streams, varig flush, case-regler eller atomisk replace.
- Bruk konservativ tidsstempeltoleranse og behandle fil-ID som hint med mindre endepunktet har dokumentert høyere tillit.
- Re-probe etter reconnect, I/O-feil, endret markør/serveridentitet eller endret kontrollskjema.
- Fri plass kan være ukjent eller forsinket; ukjent plass kan ikke passere en muterende peak-kontroll uten eksplisitt konservativ policy.
- SMB-lock testes med et separat prosessforsøk mot samme kontrollfil. Et vellykket share-mode-forsøk er bevis på observert sperreadferd, ikke en garanti mot administratorer, ikke-samarbeidende klienter eller feilaktig NAS-implementasjon.
- Dersom en pålitelig eksklusiv endpointlease ikke kan etableres, tillates read-only analyse, men automatisk mutasjon blokkeres som standard.

### 12.4 Lagringsuavhengighet

MediaSync skal skille **plassering**, **skrivekonfliktdomene** og **uavhengig lagringsenhet**:

- lik `physical_device_key` betyr at lokale volumer/partisjoner deler fysisk enhet;
- samme kanoniske SMB-server/share og markør betyr samme logiske endepunkt eller alias;
- ulike shares på samme NAS beviser ikke fysisk uavhengighet;
- ulike bokstaver, mapper eller UNC-aliaser teller aldri alene som uavhengige kopier;
- et endepunkt kan være en separat skrivbar rot, men fortsatt dele samme feil-/ytelsesdomene;
- GUI viser både `N mål`, `M bekreftet uavhengige lagringsenheter` og eventuelle `ukjente`;
- ukjent topologi skal aldri fremstilles som bekreftet uavhengighet.

Denne vurderingen kan gi advarsel uten å blokkere et bevisst hjemmeoppsett. Rot-overlap og konkurrerende skriveeierskap forblir harde blokkeringer.

### 12.5 Kapabilitetsprofil

```python
from dataclasses import dataclass
from enum import StrEnum


class CaseMode(StrEnum):
    INSENSITIVE = "insensitive"
    SENSITIVE = "sensitive"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class FileIdReliability(StrEnum):
    STABLE = "stable"
    HINT = "hint"
    UNAVAILABLE = "unavailable"


class LockScope(StrEnum):
    LOCAL_MACHINE = "local_machine"
    REMOTE_SHARE_OBSERVED = "remote_share_observed"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class SourceReadGuardLevel(StrEnum):
    DENY_WRITE_AND_DELETE = "deny_write_and_delete"
    STABILITY_HANDLE_ONLY = "stability_handle_only"
    POST_TRANSFER_HASH_ONLY = "post_transfer_hash_only"
    UNAVAILABLE = "unavailable"


class DurabilityLevel(StrEnum):
    FILE_FLUSH_CONFIRMED = "file_flush_confirmed"
    REMOTE_ACK_ONLY = "remote_ack_only"
    BEST_EFFORT = "best_effort"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EndpointCapabilities:
    filesystem_name: str | None
    maximum_file_size: int | None
    maximum_component_length: int | None
    maximum_path_length: int | None
    timestamp_precision_ns: int
    default_case_mode: CaseMode
    supports_per_directory_case_query: bool
    supports_reparse_inspection: bool
    supports_final_path_resolution: bool
    supports_directory_identity_handles: bool
    supports_atomic_rename: bool
    supports_no_overwrite_insert: bool
    supports_atomic_replace: bool
    supports_file_flush: bool
    supports_write_through_move: bool
    durability_level: DurabilityLevel
    lock_scope: LockScope
    supports_exclusive_control_lock: bool
    source_read_guard_level: SourceReadGuardLevel
    supports_file_ids: bool
    file_id_reliability: FileIdReliability
    supports_birthtime: bool
    supports_attributes: bool
    supports_named_streams: bool
    supports_sparse_files: bool
    supports_hardlinks: bool
    supports_encryption: bool
    supports_long_paths: bool
    is_network: bool
    is_removable: bool
    likely_rotational: bool | None
```

Kapabilitetsfelt beskriver **observert, versjonert bevis**, ikke et løfte avledet bare fra filsystemnavnet. `supports_atomic_replace` betyr at adapteren har bestått den dokumenterte proben under bestemte forutsetninger; recoveryjournal, target-precondition og etterkontroll er fortsatt obligatoriske. `supports_no_overwrite_insert` betyr at en eksisterende måloppføring ikke kan erstattes i den testede kodebanen. `source_read_guard_level` beskriver den sterkeste observerte måten kildeinnhold kan bindes til transferen på; den oppgraderes aldri fra antakelser.

`durability_level` skal aldri oversettes til «fysisk lagret» når endepunktet bare har bekreftet en OS-/serverforespørsel. Alle sikkerhetsrelevante felt inngår i `capabilities_hash`. Endret felt oppretter en ny endpointrevisjon og gjør berørte planer stale.

### 12.6 Case-modus og sammenligningsnøkkel

Windows kan ha case-sensitive kataloger i et ellers case-insensitivt namespace. Endpointnivået er derfor bare en default; autoritativ sammenligningssemantikk persisteres per katalog.

For hver katalog som inngår i coverage skal skanneren, når adapteren støtter det:

1. åpne katalogen uten å følge ukontrollerte reparse points;
2. lese case-sensitive-flagg og identitetsbevis før enumerering;
3. produsere `case_mode_evidence` og en kanonisk `case_context_hash` fra katalogidentitet, flagg, probe-/comparer-versjon og relevant parentkontekst;
4. enumerere barna;
5. lese flagg/identitet på nytt etter enumerering;
6. markere katalogen `VOLATILE` dersom casekonteksten endret seg;
7. lagre konteksten i `directory_coverage` og referere den fra alle direkte barn.

Bindende regler:

- faktisk Unicode-sti lagres uendret;
- `WindowsNameComparer` produserer en versjonert nøkkel fra den konkrete parentkatalogens `case_context_hash`;
- generisk `str.casefold()` er aldri autoritativ Windows-identitetsregel;
- arv av parentens case-modus er bare tillatt når adapteren kan dokumentere at plattformsemantikken gjør dette korrekt;
- `MIXED`, `UNKNOWN`, probe-feil eller endret casekontekst blokkerer berørte erstatninger, karantene- og toveisavgjørelser;
- alle kollisjonsmedlemmer beholdes i `file_entries`;
- source- og target-preconditions inneholder parentens casekontekst;
- endret casekontekst mellom analyse og utførelse gir `PLAN_STALE_CASE_CONTEXT_CHANGED`, ikke en ny implicit nøkkel under samme plan.

Sammenligningsnøkkelen er et planleggingsverktøy, ikke filsystemidentitet. Handle-/fil-ID-/sti- og parentbevis brukes fortsatt ved mutasjon.

### 12.7 Writer-eierskap, endpointlock og rotvalidering

Før en plan kan utføres mot et skrivbart endepunkt, må Engine Host gjennomføre hele denne sekvensen:

1. Åpne registrert rot read-only og klassifisere `.mediasync` etter §4.1. En ukjent, korrupt, nyere-ukjent eller case-kolliderende kontrollmappe gir ingen mutasjonstillatelse.
2. Validere `endpoint.json`, kontrollområde-ID, root identity, endpointgenerasjon og `owner_installation_id`/`ownership_epoch` mot den forseglede endpointrevisjonen.
3. Avvise normal mutasjon dersom målet eies av en annen installasjon. Read-only analyse kan fortsatt tilbys; overtakelse går gjennom den eksplisitte sagaen i §4.1.5.
4. Ta den globale OS-støttede `mutation.lock` i kontrollområdet og beholde handle gjennom mutasjon/recovery. Låsen tas før en lokal fencing-token allokeres.
5. Lese markør og eierskapsrecord på nytt **etter** lock acquisition for å lukke løpet mellom preflight og lås.
6. Registrere `lease_id`, aktuell `ownership_epoch` og neste monotone lokale `fencing_token` i `recovery.sqlite` før `MutationPermit` utstedes.
7. Binde permiten til tuple `(endpoint_id, endpoint_revision_id, owner_installation_id, ownership_epoch, lease_id, local_fencing_token, scope_hash)`.
8. Validere final path/reparse-status fra handles når adapteren støtter det, og re-probe/blokkere dersom sikkerhetsrelevante kapabiliteter er endret.
9. Ved hver muterende adapteroperasjon kontrollere at lockhandle fortsatt eies og at permitens epoch/token ikke er stale.

En `resource_leases`-rad, en lockfils eksistens eller et gammelt heartbeat er aldri selve låsen. Et UTC-tidspunkt autoriserer ikke overtakelse. Ved mistet/ukjent lock stoppes nye mutasjoner, alle gamle permits invalideres og målet går til recovery/avstemming.

**Kryssmaskinbevis:** SMB-støtte for mutasjon regnes ikke som ferdig før en integrasjonstest med to ekte Windows-klienter eller isolerte Windows-VM-er mot samme share viser at bare én klient kan holde locken, at klient B blokkeres av fremmed owner/epoch, og at klient A etter reconnect ikke kan bruke arbeid fra en eldre epoch. To prosesser på samme PC er ikke tilstrekkelig bevis.

**Degradert modus:** Dersom pålitelig endpointlock ikke kan bevises, er endepunktet read-only som standard. Bare eksplisitt `COPY_NEW_ONLY_NO_REPLACE` kan tillates, og bare når adapteren har bevist sikker no-overwrite-innsetting, måloppføringen var fraværende ved analyse og fortsatt er fraværende i samme commitadapterkall. Ingen replace, metadataharmonisering, versjonering, karantene, restore, retention eller toveisoperasjon er tillatt i denne modusen. Automatikk er deaktivert som standard.

### 12.8 Kapabilitets- og identitetsfeil

Planlegging/preflight skal minst kunne produsere:

```text
TARGET_FILE_TOO_LARGE
TARGET_NAME_TOO_LONG
TARGET_PATH_TOO_LONG
TARGET_CASE_COLLISION
TARGET_READ_ONLY
TARGET_NO_FREE_SPACE
TARGET_UNSUPPORTED_NAMED_STREAM
TARGET_UNSUPPORTED_ENCRYPTED_FILE
TARGET_UNSUPPORTED_METADATA
TARGET_REPLACE_NOT_SUPPORTED
ENDPOINT_CAPABILITIES_UNKNOWN
ENDPOINT_IDENTITY_MISMATCH
ENDPOINT_CAPABILITIES_CHANGED
ENDPOINT_LEASE_UNAVAILABLE
ENDPOINT_LOCK_UNRELIABLE
TARGET_CHANGED_SINCE_ANALYSIS
PARENT_IDENTITY_CHANGED
REPARSE_POINT_INTRODUCED
FINAL_PATH_OUTSIDE_ROOT
FINAL_DURABILITY_UNCONFIRMED
ROOT_OVERLAP
WRITABLE_ROOT_OWNED_BY_OTHER_JOB
```

Eksempler:

- En video over målfilsystemets maksimum blokkeres i analysen.
- Named streams mot et mål uten streamstøtte følger §17.8 og fremstilles aldri som fullt bevart uten bevis.
- Et endepunkt med ukjent replace-semantikk bruker journalført fallback eller blokkerer erstatning.
- Et mål som overlapper kilde, annet mål eller en skrivbar rot i en annen jobb blokkeres før skanning.
- Et SMB-mål uten pålitelig endpointlease kan analyseres read-only, men automatisk mutasjon er blokkert.

Kapabiliteter kan caches kortvarig. Ny probe som endrer en sikkerhetsrelevant egenskap oppretter ny endpointrevisjon og ugyldiggjør berørte analyser/planer; den gamle revisjonen omskrives aldri.
