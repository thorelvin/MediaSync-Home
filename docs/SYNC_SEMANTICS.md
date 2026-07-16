# Synkroniseringssemantikk


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Moduser, konflikter, duplikater, filtre, scanning, hash-evidens og deterministisk planlegging.


## 5. Synkroniseringsmoduser

### 5.1 Bindende jobbtypematrise

| Jobbtype | Gyldige moduser | Ugyldige moduser |
|---|---|---|
| `multi_target_backup` | Oppdater kilde → mål, Speil kilde → mål | Reverse update, reverse mirror, toveis |
| `pair_sync` | Oppdater venstre → høyre, Speil venstre → høyre, Oppdater høyre → venstre, Speil høyre → venstre, Toveis | Flere enn to endepunkter |

Domenemodell, databasevalidering og GUI skal håndheve denne matrisen. Et fler-målsoppsett skal aldri kunne tolkes som at flere backupmål skriver tilbake til samme kilde.

### 5.2 Oppdater i valgt retning

GUI-navn: **Kopier og oppdater →** eller **← Kopier og oppdater**.

| Tilstand | Handling |
|---|---|
| Finnes bare på autoritativ side | Kopier til mottakende side |
| Finnes på begge, bekreftet lik | Ingen handling |
| Finnes på begge, ulik | Bevar gammel mottakerversjon og sett inn autoritativ fil |
| Finnes bare på mottakende side | Behold filen |
| Mottakende side har identisk innhold under annet navn | Vis duplikatinformasjon, men bevar forventet struktur |

Dette er standardmodus for privat backup. I `multi_target_backup` er retningen alltid kilde → mål.

### 5.3 Speil i valgt retning

GUI-navn: **Speil →** eller **← Speil**.

| Tilstand | Handling |
|---|---|
| Finnes bare på autoritativ side | Kopier til mottakende side |
| Finnes på begge, bekreftet lik | Ingen handling |
| Finnes på begge, ulik | Bevar gammel mottakerversjon og sett inn autoritativ fil |
| Finnes bare på mottakende side | Flytt til karantene |

Rå Robocopy-speiling er forbudt. Python-planleggeren materialiserer alle ekstra objekter eksplisitt, og `SAF-003` må være bestått.

### 5.4 Reverse-moduser i `pair_sync`

Reverse update og reverse mirror har samme semantikk som §5.2–5.3 med høyre side som autoritativ. Retningsendring forkaster eksisterende plan og krever ny analyse. Disse valgene skal ikke vises for `multi_target_backup`.

### 5.5 Toveissynkronisering

Toveisjobb tillates bare i `pair_sync` mellom nøyaktig to endepunkter. Den bruker en gyldig baseline fra sist vellykkede og avklarte kjøring. Første kjøring uten baseline er ikke-destruktiv.

Baseline er ikke bare knyttet til `job_id`. Den tilhører et immutable `baseline_set` med en kanonisk `context_hash` over minst:

```text
left/right endpoint identity og relative roots
comparison_key_version og case-/timestampsemantikk
filter rules hash
metadata-/named-stream-/conflictpolicy som påvirker endringsdeteksjon
planner/baseline schema version
```

Endring som påvirker konteksten oppretter et nytt baseline-sett. Gjenbruk er bare tillatt når en eksplisitt ekvivalensfunksjon beviser at endringen er irrelevant for baseline; «samme jobbnavn» eller samme stabile endpoint-ID er ikke nok. En plan binder eksakt `baseline_set_id` og generasjon. Context mismatch gjør første påfølgende kjøring ikke-destruktiv og krever ny baselineetablering.

#### Beslutningsmatrise

| Venstre siden baseline | Høyre siden baseline | Handling |
|---|---|---|
| Uendret | Uendret | Ingen handling |
| Endret | Uendret | Kopier venstre til høyre |
| Uendret | Endret | Kopier høyre til venstre |
| Ny | Mangler | Kopier venstre til høyre |
| Mangler | Ny | Kopier høyre til venstre |
| Endret | Endret, samme innhold | Oppdater metadata/baseline; ingen innholdskopi |
| Endret | Endret, ulikt innhold | Konflikt; behold begge |
| Slettet | Uendret | Flytt høyre kopi til karantene |
| Uendret | Slettet | Flytt venstre kopi til karantene |
| Slettet | Endret | Konflikt; behold endret fil og registrer slettingsintensjon |
| Endret | Slettet | Konflikt; behold endret fil og registrer slettingsintensjon |
| Slettet | Slettet | Oppdater baseline |
| Ny på begge, samme innhold | Ny på begge | Merk identisk; ingen kopi |
| Ny på begge, ulikt innhold | Ny på begge | Konflikt; behold begge med konfliktfilnavn |

#### Deterministisk konfliktnavn og «behold begge»-saga

Konfliktnavnet materialiseres i `planned_operations.target_relative_path` før planchecksum beregnes. Execution får ikke bruke gjeldende klokke eller generere et nytt navn ved retry.

Kanonisk mønster:

```text
<stem> (conflict <side-label> <stable-short-id>)<extension>
```

`stable-short-id` avledes fra plan-ID, begge source entry-identiteter og conflict-ordinal gjennom versjonert kanonisk hash. Navnet skal:

- være identisk for samme forseglede plan;
- være gyldig på begge endepunkter;
- trimmes deterministisk til laveste relevante komponentgrense;
- kontrolleres mot reserverte navn, case-kontekst og eksisterende planlagte/faktiske navn;
- reserveres med `ABSENT`/no-overwrite-precondition;
- logge hvilken side hver variant kom fra.

«Behold begge» er en fleroperasjonssaga. Ingen original flyttes, erstattes eller karanteneflyttes før begge planlagte variantene er stagingkopiert, verifisert og kan settes inn uten overwrite. Baseline blir ikke avklart før begge resultater og alle avhengigheter er terminalt journalført.

### 5.6 Fler-mål-backup

En `multi_target_backup` med én kilde og opptil tre mål skal:

- skanne kilden én gang;
- skanne hvert mål separat;
- bygge én delplan per mål fra samme kildesnapshot;
- bare tillate Oppdater kilde → mål eller Speil kilde → mål;
- vise totalsammendrag og per-mål-sammendrag;
- kunne kjøre mål sekvensielt eller ressursstyrt parallelt;
- beholde uavhengig historikk og sist vellykkede status per mål;
- ikke la ett frakoblet mål blokkere andre mål, med mindre brukeren har valgt «alle mål må lykkes»;
- aldri bruke mål 1, 2 eller 3 som implicit kilde for et annet mål i første versjon.

## 6. Identiske filer, hash-evidens og duplikatdeteksjon

### 6.1 Mål og avgrensning

Programmet skal oppdage filer med identisk innhold selv om filnavn, mappe eller tidsstempler er forskjellige. Funksjonen er informativ og skal aldri endre filer automatisk.

En tilsiktet kopi på et konfigurert backupmål er en **forventet replika**, ikke et lagringsproblem. Flere stier på samme volum kan dessuten peke til samme filobjekt og skal ikke behandles som to fysiske kopier.

Relasjonsklasser:

- `EXPECTED_REPLICA` — tilsiktet kilde-/backuprelasjon;
- `INTRA_ENDPOINT_DUPLICATE` — ulike filobjekter med identisk innhold på samme endepunkt;
- `UNRELATED_CROSS_ENDPOINT_DUPLICATE` — identisk innhold uten forventet replika-relasjon;
- `SAME_FILE_MULTIPLE_PATHS` — flere stier/hardlinks til samme pålitelige `(volume_identity, file_id)`;
- `POTENTIAL_DUPLICATE` — kandidat uten tilstrekkelig full hash-evidens.

Bare reelle, separate filobjekter kan inngå i «mulig spart plass». `EXPECTED_REPLICA` og `SAME_FILE_MULTIPLE_PATHS` har alltid 0 byte mulig besparelse.

### 6.2 Hash-evidensnivåer

En hashverdi og beviset bak den er separate data:

| Evidens | Betydning | Kan vises som bekreftet identisk | Kan drive `SKIP_IDENTICAL` |
|---|---|---:|---:|
| `CURRENT_READ_HASH` | Hele filen er lest i aktuell analyse/transferkontekst med start-/sluttfingerprint | Ja | Ja |
| `USN_CONTINUITY_VALIDATED_HASH` | Tidligere full hash er bundet til komplett, ubrutt og testet journalbevis | Ja | Ja |
| `METADATA_REVALIDATED_CACHED_HASH` | Tidligere full hash; metadata/ID matcher, men innhold er ikke lest nå | Nei — vis «tidligere bekreftet» | Bare under eksplisitt svak hurtigpolicy, aldri for destruktiv avgjørelse |
| `STALE_HASH_HINT` | Cachepost med svak eller ufullstendig identitet | Nei | Nei |
| `QUICK_SIGNATURE_ONLY` | Segmentbasert kandidat | Nei | Nei |

Første komplette versjon trenger ikke implementere USN-integrasjon. Inntil den finnes, er `CURRENT_READ_HASH` eneste sterke cache-/skip-evidens på tvers av potensielt muterbare filer.

### 6.3 Kostnadsstyrt algoritme

1. Gruppér filer etter størrelse og relevant duplikatomfang.
2. Skill pålitelige `SAME_FILE_MULTIPLE_PATHS` før fysisk spareberegning.
3. Størrelser som bare forekommer én gang er ikke kandidater.
4. Beregn versjonert hurtigsignatur for resterende kandidater.
5. Hurtigsignaturen hashes over en kanonisk byteform som inkluderer `signature_schema_version`, total filstørrelse og for hvert segment: offset, lengde og bytes.
6. Segmentvalg er deterministisk: første 1 MiB, siste 1 MiB og eventuelt versjonert midtsegment for svært store filer.
7. Beregn full BLAKE3 bare for kandidater som fortsatt matcher.
8. Ved full lesing registreres fingerprint før og etter lesingen. Endret type, size, mtime, file-ID-evidence eller path chain gjør resultatet `SOURCE_CHANGED_DURING_HASH`.
9. Bekreft identitet bare når størrelse og full hash er lik, og hver hash har tilstrekkelig evidens for den aktuelle beslutningen.
10. Klassifiser relasjonen mot jobb, endepunkt, filobjektidentitet og forventet relativ sti.
11. Cache lagrer identitetsfelt, evidens, hash-/segmentschema og eventuelt journalbevis.

### 6.4 GUI-kategorier

- **Forventet backupkopi:** tilsiktet replika; teller ikke som mulig besparelse.
- **Samme fil, flere stier:** hardlink-/file-ID-relasjon; bruker ikke nødvendigvis ekstra plass.
- **Identisk på samme endepunkt:** bekreftet separate filobjekter med likt innhold.
- **Identisk annet sted:** bekreftet identisk innhold uten forventet replika-relasjon.
- **Tidligere bekreftet:** cached full hash uten nåværende innholdsbevis.
- **Mulig identisk:** samme størrelse og hurtigsignatur; full hash mangler.
- **Samme navn, forskjellig innhold:** konfliktindikasjon, ikke duplikat.
- **Samme innhold, ulik metadata:** innholdet er likt, metadata avviker.

### 6.5 Handlinger i første versjon

- åpne plassering;
- kopiere sti;
- filtrere operasjonslisten til gruppen;
- eksportere rapport med relasjons- og evidensklassifisering;
- markere en gruppe som gjennomgått;
- åpne valgt fil i systemets standardprogram.

Ikke tilby automatisk sletting, hardlinking, flytting eller deduplisering. Backupen skal heller ikke automatisk gjenskape hardlinktopologi på målet.

### 6.6 Samspill med synkronisering

- Forventet målsti hoppes bare over som bekreftet identisk når hash-evidensen oppfyller planens assurancepolicy.
- Dersom identisk innhold finnes på en annen målsti, kopieres fortsatt forventet struktur.
- Metadatarevalidert cache kan redusere kandidatmengde, men oppgraderes ikke til nåværende verifikasjon uten dokumentert bevis.
- Duplikatskanning kan kjøres separat, pauses og strupes under aktiv backup.
- Rapporten skal aldri antyde at planlagte backupreplikaer eller flere hardlinkstier bør fjernes.

## 7. Filfiltre

### 7.1 Filtertyper

Støtt include- og exclude-regler for:

- filtype/extension;
- filnavnglob;
- relativ sti/glob;
- mappe;
- minimums- og maksimumsstørrelse;
- endret dato;
- opprettet dato der filsystemet støtter det;
- skjult attributt;
- systemattributt;
- midlertidige filer;
- reparse points;
- tomme mapper;
- regulære uttrykk i en eksplisitt avansert modus.

Glob er standard og skal dekke vanlig hjemmebruk. Regex er ikke nødvendig for den anbefalte flyten.

### 7.2 Standardfilter og kontrollområde

Standard er **alle brukerfiler**, med automatiske system-/temp-ekskluderinger:

```text
$RECYCLE.BIN/**
System Volume Information/**
Thumbs.db
Desktop.ini
*.tmp
~$*
```

`.mediasync/**` er ikke en navnebasert standardekskludering. Den ekskluderes bare når kontrollområdeklassifiseringen i §4.1 beviser `VALID_OWNED`, `VALID_FOREIGN` eller `VALID_READ_ONLY_NEWER_SCHEMA`. En ukjent eller ikke-tom `.mediasync` på en kilde er brukerdata eller et synlig avvik.

Systemfil- og midlertidigfilter kan overstyres i avansert modus. Et validert kontrollområde kan aldri inkluderes som vanlig brukerdata av en writable jobb.

### 7.3 Mediepresets

Tilby valgfrie presets:

- Bilder: JPG, JPEG, HEIC, HEIF, PNG, TIFF, BMP, GIF, WEBP, AVIF.
- RAW: CR2, CR3, NEF, NRW, ARW, SR2, RAF, ORF, RW2, PEF, DNG og andre vanlige RAW-varianter.
- Video: MP4, MOV, M4V, AVI, MKV, MTS, M2TS, MPG, MPEG, WMV, WEBM og flere.
- Sidecars: XMP, AAE, THM, SRT, JSON og prosjektspesifikke sidecars.

Filene behandles som opake bytefiler; ingen dekoding kreves.

### 7.4 Deterministisk og ressursbegrenset evaluering

- Evaluer regler i dokumentert rekkefølge og lagre filterversjon/rules hash i planen.
- Vis i GUI hvorfor en fil ble inkludert eller ekskludert.
- Test versaluavhengig extension-sammenligning uten å endre originalnavn.
- Regex skal bruke en dokumentert ikke-backtracking/begrenset motor når mulig. Python `re` med ubegrenset backtracking i scanner-hotpath er ikke akseptabel standard.
- Maks mønsterlengde, antall regexregler, evalueringsbudsjett per batch og samlet CPU-budsjett skal være eksplisitte konfigurasjonsgrenser.
- Regex kjøres cancellable uten GUI-tråd og uten å holde databasewrite-lock.
- Et mønster som gjentatte ganger overskrider budsjettet deaktiveres for analysen med `FILTER_REGEX_BUDGET_EXCEEDED`; det skal ikke fryse skanneren eller gi taus delanalyse.
- Filtermotoren får property-/fuzztester for patologiske mønstre, Unicode og millionstore stilister.

## 13. Skanner, coverage og indeks

### 13.1 Konsistensmodell

Første versjon bruker en **live, best-effort skann**, ikke VSS eller et sant punkt-i-tid-snapshot. Ordet `snapshot` betyr en uforanderlig katalogregistrering av hva skanneren observerte, sammen med eksplisitt coverage og kjente avvik. Det skal ikke antyde at hele filtreet eksisterte i nøyaktig samme tilstand på ett tidspunkt.

Gyldige snapshotresultater:

```text
COMPLETE_NO_KNOWN_GAPS   # all planlagt coverage ble lest; ingen kjente hull
COMPLETE_WITH_VOLATILITY # alt ble lest, men minst ett område endret seg under skann
INCOMPLETE               # minst ett relevant område kunne ikke leses/bevises
CANCELLED
FAILED
```

`COMPLETE_NO_KNOWN_GAPS` er fortsatt et live-observasjonsresultat. Derfor revalideres kildefilen før kopi og fravær før karantene. Destruktive handlinger krever i tillegg full coverage for berørt scope.

### 13.2 Traversering

Bruk iterativ `os.scandir`-basert traversering bak en `FileTreeReader`-port.

Krav:

- eksplisitt stack/deque, ikke Python-rekursjon;
- `DirEntry.is_dir(..., follow_symlinks=False)` og høyst én nødvendig `stat` per vanlig oppføring;
- klassifiser en rot-nær `.mediasync` før den eventuelt ekskluderes; bare `VALID_OWNED` eller eksplisitt validert `VALID_FOREIGN` behandles som kontrollmetadata; ukjent/case-kolliderende innhold blir vanlig brukerdata eller en synlig blokkering;
- ekskluder deretter validerte kontrollområder, reparse points og billige navn-/stifiltre før dyr metadatahenting;
- ikke bruk `Path.rglob()` i varm kodebane;
- behold kompakte strenger/verdier i varm bane; konverter til `Path` ved adaptergrensen;
- ikke les innhold, EXIF, MIME, thumbnails, streamlister eller hash under ordinær skann;
- yield `ScanBatch` fortløpende gjennom en avgrenset port til Engine Hosts katalogwriter;
- støtte hierarkisk kansellering mellom kataloger og før hver batch;
- registrer tomme mapper og alle relevante feil/hull;
- følg aldri junctions, symbolske lenker eller andre reparse points som standard;
- produser kildesnapshot én gang per fler-målsanalyse.

Scanner-/domainkode importerer ikke SQLite og åpner ingen database. Bare Engine Hosts katalogadapter materialiserer batchene.

### 13.3 Katalogcoverage, casekontekst og volatilitet

Hver besøkt katalog får en rad i `directory_coverage` og går gjennom:

```text
DISCOVERED -> ENUMERATING -> COMPLETE
                         \-> VOLATILE
                         \-> UNREADABLE
                         \-> DISAPPEARED
                         \-> REPARSE_BLOCKED
                         \-> CASE_CONTEXT_UNKNOWN
                         \-> CANCELLED
```

For hver katalog skal adapteren, når mulig:

1. åpne katalogen uten å følge ukontrollerte reparse points;
2. registrere final path, katalog-/parentidentitet, reparsebevis og casekontekst før enumerering;
3. enumerere barna;
4. lese identitet, casekontekst og observerbar metadata på nytt etter enumerering;
5. markere `VOLATILE` hvis identitet eller observerbar tilstand endret seg;
6. markere `CASE_CONTEXT_UNKNOWN` dersom case-semantikken ikke kan bestemmes sikkert;
7. forsøke et begrenset lokalt rescan, standard maksimalt to ganger;
8. beholde issuehistorikken selv om et senere forsøk lykkes.

Katalogmtime er bare et signal, ikke et komplett endringsbevis. Dersom stabil coverage eller casekontekst ikke kan bevises, kan berørt scope ikke drive karantene, erstatning basert på case-ekvivalens eller toveisfravær. Ikke-destruktive kopier kan fortsatt planlegges når filens egen source-precondition og målpolicy tillater det.

`snapshot_issues` lagrer minst:

- katalog-/oppføringssti og parent identity;
- feiltype, Windows error code og sanert detalj;
- retrycount og første/siste observasjon;
- om feilen påvirker read, compare, copy eller destructive absence proof;
- case-mode-bevis og eventuell `case_probe_error`;
- om scope er gjenopprettet, fortsatt ufullstendig eller krever brukerhandling.

### 13.4 Endepunktstyrt parallellitet

| Endepunkt | Enumeratorer | Standardbegrunnelse |
|---|---:|---|
| HDD / USB-HDD | 1 | Bevarer sekvensiell tilgang og unngår seek-storm |
| SMB/NAS | 1 | Reduserer latency-kø og sharebelastning |
| Lokal SSD/NVMe | 1, eventuelt 2 | Økes bare ved stabil benchmarkgevinst |
| Ukjent | 1 | Konservativt |

Parallell traversering er ikke global standard. Databaseorden blir deterministisk gjennom eksplisitt nøkkel/sortering, ikke workerfullføringsrekkefølge.

### 13.5 Poster, batching og writergrense

```python
@dataclass(slots=True)
class ScanEntry:
    relative_path: str
    comparison_key: str
    comparison_key_version: int
    parent_key: str
    parent_case_context_hash: str
    name: str
    path_depth: int
    entry_type: int
    size_bytes: int | None
    mtime_ns: int | None
    birthtime_ns: int | None
    metadata_change_time_ns: int | None
    attributes: int | None
    volume_identity: str | None
    file_id: str | None
    file_id_reliability: int
    link_count: int | None
    reparse_tag: int | None


@dataclass(slots=True)
class ScanBatch:
    snapshot_id: str
    schema_version: int
    sequence_no: int
    payload_hash: str
    entries: list[ScanEntry]
    coverage_updates: list[object]
    issues: list[object]
    approximate_bytes: int
```

Regler:

- bruk `st_birthtime_ns` på Windows når tilgjengelig; aldri `st_ctime_ns` som opprettelsestid;
- start med 4 096 poster og reguler 2 048–8 192 etter writerlatens/kødybde;
- flush også ved omtrent 8 MiB;
- produsent blokkerer ved full, avgrenset kø;
- én kort katalogtransaksjon per batch;
- ingen database-/fil-I/O holdes åpen mens produsenten venter på køplass;
- `(snapshot_id, sequence_no)` og canonical `payload_hash` gjør batchinnsetting idempotent; samme sekvens med annen hash er fatal snapshotkonflikt;
- batchreceipt, entries, coverage/issues og inkrementelle summer committes i samme writertransaksjon;
- snapshot-summer oppdateres inkrementelt;
- case-kollisjoner materialiseres uten å avvise medlemsposter;
- hver filpost bindes til parentens forseglede `case_context_hash`;
- seal er en egen kritisk transaksjon som validerer sekvenser, coverage, casekontekst, counts og checksum og setter `immutable=1`;
- etter seal er filposter og hashfelt read-only; senere hash skrives til cache/avledet artefakt;
- kansellering/failure forsegler aldri snapshot som komplett.

### 13.6 Fingeravtrykk, source-precondition og `SourceReadGuard`

```python
@dataclass(frozen=True, slots=True)
class FileFingerprint:
    entry_type: str
    size_bytes: int
    mtime_ns: int
    birthtime_ns: int | None
    attributes: int | None
    reparse_tag: int | None
    volume_identity: str | None
    file_id: str | None
    file_id_reliability: FileIdReliability
    parent_identity_hash: str
    path_chain_hash: str
    parent_case_context_hash: str
    content_hash: str | None
    hash_evidence_kind: str | None


@dataclass(frozen=True, slots=True)
class SourcePrecondition:
    endpoint_revision_id: str
    relative_path: str
    expected: FileFingerprint
    guard_policy: str
    required_assurance: str
```

Hashfelt er normalt tomme etter skann. Hver utførbar filoperasjon får en source-precondition som binder:

- endpointrevisjon og relativ sti;
- filtype, størrelse, tidspunkt/presisjon og attributter;
- reparse-tag og resolved path-chain;
- volum-/fil-ID med eksplisitt reliabilitet;
- parentidentitet og parentens casekontekst;
- eventuell nåværende innholdshash med evidensnivå.

Executor revaliderer source-precondition rett før transfer. Deretter brukes én av disse forseglede policyene:

| Policy | Oppførsel |
|---|---|
| `HANDLE_GUARD_REQUIRED` | Åpne `SourceReadGuard` som blokkerer skrive-/delete-deling gjennom transferen. Manglende guard gir defer/blokkering. |
| `HANDLE_GUARD_OR_CURRENT_HASH` | Bruk guard når tilgjengelig; ellers fullhash kilden etter transfer og krev match mot staging før commit. |
| `POST_TRANSFER_CURRENT_HASH_REQUIRED` | Brukes der guard ikke kan bevises; source og staging fullhashes etter transfer. |
| `LOW_ASSURANCE_NEW_COPY_ONLY` | Kun eksplisitt, ikke-destruktiv nykopi. Re-stat før/etter og rapporter lavere assurance; kan ikke brukes for replace, karantene, baselineavgjørelse eller «bekreftet identisk». |
| `DEFER_UNSTABLE_SOURCE` | Utsett filen dersom stabilitet ikke kan bindes sikkert. |

En guard er et levende runtimeobjekt eid av Engine Host. Den kan ikke serialiseres eller rekonstrueres fra en boolsk databaseverdi. Den beholdes til staging er verifisert og kildepostcondition er kontrollert. Dersom kildeidentitet, størrelse, path-chain, casekontekst eller nødvendig hash avviker, blir resultatet `SOURCE_CHANGED_DURING_TRANSFER`; staging isoleres og committes ikke.

Robocopy leser fortsatt ved sti. Derfor er et samtidig guard-handle eller en nåværende post-transfer-hash det faktiske beviset; metadatarevalidering alene påstår ikke at de kopierte byte representerer en stabil kilde.

### 13.7 Sammenligningsnøkkel og kollisjoner

- intern relativ separator er `/`;
- original Unicode-streng bevares;
- nøkkel bygges én gang av `WindowsNameComparer` med versjon;
- `comparison_key` er ikke unik;
- `MIXED`/`UNKNOWN` behandles konservativt;
- kollisjonsgrupper og medlemmer materialiseres separat;
- kollisjoner blokkerer bare berørte operasjoner, men gjør aldri snapshotet usynlig;
- ingen senere lag lager en alternativ casefolding uten egen skjemaversjon.

### 13.8 Cachegjenbruk, fil-ID-tillit og hash-evidens

Cacheoppslag bruker minst:

```text
endpoint_revision_id
endpoint_generation
volume_identity
comparison_key + comparison_key_version
parent_case_context_hash
entry_type
size_bytes
mtime_ns innen kjent presisjon
birthtime_ns når tilgjengelig
file_id + reliability når tilgjengelig
hash_algorithm + hash_schema_version
```

En treffende nøkkel betyr bare at en tidligere observasjon kan være relevant. Hver cachepost har ett evidensnivå:

| Evidens | Betydning | Tillatt bruk |
|---|---|---|
| `CURRENT_READ_HASH` | Full hash lest i den aktuelle beviskjeden, med før-/etterfingerprint | Kan drive `SKIP_IDENTICAL` og innholdsverifisering |
| `USN_CONTINUITY_VALIDATED_HASH` | Full hash med komplett, validert lokal journalcontinuity siden lesingen | Kan drive `SKIP_IDENTICAL` når journal-ID/range er bevist |
| `METADATA_REVALIDATED_CACHED_HASH` | Tidligere full hash; metadata/ID matcher nå | Hint eller svak hurtigpolicy, aldri standard «bekreftet identisk» |
| `STALE_HASH_HINT` | Relevans usikker | Bare prioritering av ny hash |
| `QUICK_SIGNATURE_ONLY` | Delhash/segmentbevis | Kandidatgruppering, aldri identitetsbevis |

Bindende regler:

- stabil lokal fil-ID styrker identitet, men står aldri alene og kan gjenbrukes over tid;
- SMB-/FAT-ID er som standard et hint;
- cacheposten lagrer `read_started_fingerprint`, `read_completed_fingerprint`, evidence generation og eventuell USN journal-ID/range;
- canonical quick signature inkluderer signaturversjon, total filstørrelse og for hvert segment offset, lengde og bytes/hash i fast rekkefølge;
- ved tvil nedgraderes eller forkastes evidensen;
- cachetreff fritar ikke fra å registrere filen i nytt snapshot;
- watchere er bare trigger/hint; de erstatter aldri coverage eller journalcontinuity;
- én logisk cacheidentitet har én aktiv høyeste evidenspost; konkurrerende beregninger dedupliseres og historieskrives, ikke last-write-wins.

Standardplanleggeren kan bare materialisere `SKIP_IDENTICAL` når begge sider har kompatible `CURRENT_READ_HASH`- eller `USN_CONTINUITY_VALIDATED_HASH`-bevis, eller når de blir lest i den aktuelle analyseflyten.

### 13.9 Filterrekkefølge og ressursbudsjett

Evaluer billigst først:

1. klassifisert kontrollområde og eksplisitt ekskludert rot;
2. glob-/sti-/navnemønster uten `stat`;
3. reparse-/systempolicy;
4. filtype;
5. størrelse og dato;
6. avanserte regler.

En `.mediasync`-mappe ekskluderes bare når `ControlAreaClassifier` har produsert en tilstand som policyen eksplisitt kjenner som kontrollmetadata. `UNKNOWN_NONEMPTY_DIRECTORY`, `CASE_ALIAS_COLLISION` og korrupt/nyere-ukjent markør skal aldri filtreres bort stille.

Glob er standard. Regulære uttrykk er en avansert funksjon og krever:

- dokumentert begrenset/non-backtracking motor eller isolert cancellable worker;
- maksimum mønsterlengde og antall regexregler;
- CPU-/tidsbudsjett per batch og samlet analyse;
- maksimum inputlengde per evaluering;
- deterministisk timeout-/feilkode;
- automatisk deaktivering av regelen etter gjentatte budsjettbrudd til brukeren retter den.

Filtermotoren returnerer stabile årsakskoder. GUI-tekst bygges i presentasjonslaget. Et filter som ikke kan evalueres innen sikker policy gjør relevant coverage ufullstendig; det blir ikke behandlet som «ingen treff».

### 13.10 Ustabile filer uten per-fil-venting

Ingen worker gjør `sleep` per fil.

1. Ved `T0` registreres størrelse og `mtime_ns` for alle kandidater.
2. Kandidater legges i en avgrenset tidskø med felles deadline.
3. Metadata leses på nytt i batch etter stabilitetsvinduet.
4. Uendrede kandidater kan planlegges; endrede får `DEFER_UNSTABLE`.
5. Utsatte filer kan kontrolleres i et senere samlet pass uten full reskann når coverageforutsetningene fortsatt holder.
6. Rett før kopi gjøres ny source-precondition-kontroll.

### 13.11 Fremdrift og ytelse

Vis entries, observerte byte, aktiv katalog, EWMA entries/s, kødybde, volatility og issues. Benchmark sammenligner ren enumerering og scanner + katalogwriter mot en minimal `os.scandir`-baseline. Minne skal være bundet av stack, batch og kø — ikke datasettets totale størrelse.

## 14. Sammenlignings- og planleggingsmotor

### 14.1 Ren og deterministisk planlegger

Planleggeren er en deterministisk application/domain-komponent. Den får uforanderlige revisjoner, snapshots, coverage, policy og kapabiliteter gjennom read-porter og produserer en ny plan. Den:

- leser ikke live filsystem;
- starter ikke hashing eller Robocopy direkte;
- endrer ikke snapshots, jobbrevisjoner eller tidligere planer;
- bruker indeksstøttede, sorterte SQL-strømmer fremfor store Python-dictionaries;
- returnerer eksplisitte behov for hash/reprobe/review til orkestratoren.

For hvert mål:

1. åpne snapshots sortert på `comparison_key`, eksakt sti og stabil tie-breaker;
2. strøm én sammenligningsgruppe om gangen;
3. håndter coverage, case- og typekollisjoner først;
4. klassifiser filer og mapper etter versjonert regelsett;
5. materialiser source-/target-/parent-preconditions;
6. skriv operasjoner og avhengigheter i batch;
7. oppdater counts, bytes, risiko og kanonisk checksumstrøm inkrementelt.

Samme inputrevisjoner og planner-/serializer-versjon skal gi byteidentisk kanonisk planrepresentasjon og samme checksum.

### 14.2 Klassifiseringsrekkefølge

1. Ufullstendig/volatil coverage eller ukjent/endret casekontekst som påvirker avgjørelsen → defer/block.
2. Uavklart case-kollisjon → konflikt/blokkering.
3. Fil/mappe-typekollisjon → konflikt.
4. Kapabilitets-, eierskaps- eller lockbrudd → blokkert årsakskode.
5. Bare på én side → kandidat etter jobbmodus, coverage og no-overwrite-policy.
6. Samme pålitelige volum-/fil-ID på samme endpoint → `SAME_FILE_MULTIPLE_PATHS`; ikke et lagringsduplikat og aldri cross-endpoint-identitet.
7. Ulik størrelse → endret innhold.
8. Lik størrelse og kompatibel full hash med `CURRENT_READ_HASH`/`USN_CONTINUITY_VALIDATED_HASH` → bekreftet identisk.
9. Lik størrelse og svakere identity/fingerprint/cacheevidens → identisk kandidat, ikke `SKIP_IDENTICAL` i standardpolicy.
10. Lik størrelse og tidspunkt innen toleranse → sannsynlig identisk bare i eksplisitt ikke-destruktiv hurtigbane med sannferdig lav assurance.
11. Tvetydig → persistente, dedupliserte hashbehov.
12. Full, nåværende hash ulik → endret innhold.

«Sannsynlig identisk» kan aldri alene drive karantene, toveis sletting, baselineoppdatering, automatisk konfliktvinner eller påstanden «bekreftet identisk».

### 14.3 Tidsstempel- og identitetssemantikk

Toleranse bestemmes per endepunktpar, kapabilitetsrevisjon og lagret presisjon. Ukjent/grov presisjon fører til hash ved tvil. Systemklokken alene avgjør aldri hvilken side som er riktig. Fil-ID behandles etter reliabilitetsnivå og kombineres med volum, størrelse, tid og endpointgenerasjon.

### 14.4 Planoperasjoner og årsakskoder

Støtt minst:

```text
COPY_NEW
REPLACE_CHANGED
COPY_REVERSE
COPY_CONFLICT_PRESERVED
QUARANTINE_TARGET_EXTRA
QUARANTINE_SOURCE_EXTRA
CREATE_DIRECTORY
QUARANTINE_EMPTY_DIRECTORY
SYNC_METADATA
SKIP_IDENTICAL
SKIP_FILTERED
CLASSIFY_SAME_FILE_MULTIPLE_PATHS
DEFER_UNSTABLE
DEFER_INCOMPLETE_COVERAGE
DEFER_AUTOMATION_POLICY
CONFLICT_BOTH_CHANGED
CONFLICT_TYPE_MISMATCH
CONFLICT_CASE_COLLISION
ERROR_UNREADABLE
BLOCK_TARGET_FILE_TOO_LARGE
BLOCK_TARGET_NAME_TOO_LONG
BLOCK_TARGET_PATH_TOO_LONG
BLOCK_UNSUPPORTED_NAMED_STREAM
BLOCK_ENDPOINT_CAPABILITIES_UNKNOWN
BLOCK_ENDPOINT_LEASE_UNAVAILABLE
BLOCK_ENDPOINT_OWNED_BY_OTHER_INSTALLATION
BLOCK_CONTROL_AREA_UNSAFE
BLOCK_CASE_CONTEXT_UNKNOWN
PLAN_STALE_CASE_CONTEXT_CHANGED
```

`SKIP_*` kan aggregeres når auditpolicy tillater det. `DEFER_*` er synlige, ikke-utførbare rader med `deferred_operation_type`. De fjernes aldri stille fra resultatet.

Konfliktnavn, restore-/quarantine-/versionobjektroller og alle alternative målrelative navn materialiseres i planutkastet før seal. Execution bruker aldri gjeldende klokke eller en ny tilfeldig suffix for å finne på et annet resultat.

### 14.5 Preconditions per operasjon

Hver muterende planoperasjon materialiserer:

```text
source_endpoint_revision_id
source_snapshot_id
source_relative_path
source_entry_type
source_expected_fingerprint
source_parent_identity
source_path_chain_hash
source_parent_case_context_hash
source_guard_policy
required_source_assurance

target_endpoint_revision_id
target_snapshot_id
target_relative_path
target_precondition_kind
expected_target_fingerprint_or_absence
expected_target_parent_identity
expected_target_path_chain_hash
expected_target_case_context_hash
required_capabilities_hash
required_owner_installation_id
required_ownership_epoch
required_lease_resource_key
```

Target-precondition er `ABSENT`, `MATCH_FINGERPRINT` eller `DIRECTORY_EMPTY`. `NONE` er bare tillatt for ikke-muterende rader. Preconditions er del av planseal og kan ikke fylles inn ad hoc under kjøring.

Source-precondition inkluderer type, reparse-tag, parentidentitet og casekontekst, ikke bare størrelse/tid. Operasjonen forsegler hvilken guard-/hashpolicy som kreves. Target-precondition binder også writer-eier og eierskapsepoke; en plan kan ikke utføres etter overtakelse uten ny analyse/plan.

Planner kan ikke bevise fremtidig tilstand. Executor gjør compare-and-swap-revalidering før transfer og igjen rett før commit/quarantine. Source guard eller post-transfer current hash binder byteinnholdet. Avvik gir ny analyse eller eksplisitt brukeravgjørelse, ikke stille replan i samme run.

### 14.6 Kanonisk utførelsesorden

Planen lagrer `execution_phase`, `path_depth`, `stable_order_key` og eksplisitte dependencies:

| Fase | Operasjoner | Orden |
|---:|---|---|
| 10 | Opprett nødvendige mapper/stagingstruktur | laveste dybde først |
| 20 | Kopier nye/endrede filer til staging | stabil planorden |
| 30 | Verifiser og commit filer | per mål, journalført |
| 35 | Destruktiv revalidering | coverage, fravær, lease og target fingerprint |
| 40 | Flytt ekstra filer til karantene | etter avhengige commits og fase 35 |
| 50 | Flytt ekstra mapper til karantene | høyeste dybde først |
| 60 | Sett katalogmetadata | høyeste dybde først |
| 70 | Oppdater baseline, outcomes og sammendrag | etter vellykkede dependencies |

Filoperasjonens interne recoverytilstander er ikke egne brukeroperasjoner. Planen må være en acyklisk graf; forsegling kjører topologisk validering og avviser manglende/ukjente dependencies.

### 14.7 Hurtigbane, hashforespørsler og evidens

- Ulik størrelse avgjøres uten hash.
- Bare kompatibel `CURRENT_READ_HASH` eller `USN_CONTINUITY_VALIDATED_HASH` kan drive standard `SKIP_IDENTICAL`.
- `METADATA_REVALIDATED_CACHED_HASH` og quick signature kan prioritere en hashjobb eller brukes i en eksplisitt svak, ikke-destruktiv hurtigpolicy; GUI/audit må da si «sannsynlig uendret», ikke «bekreftet identisk».
- Lik størrelse + trygt tidspunkt kan hoppes bare i eksplisitt godkjent `LOW_ASSURANCE_NEW_COPY_ONLY`; aldri for replace, mirror, two-way eller baselineoppdatering.
- Tvetydige rader gir persistente, dedupliserte hashforespørsler til orkestratoren.
- Planutkast kan bygges videre, men kan ikke forsegles mens nødvendige hashforespørsler er uløste.
- Hashresultat oppretter/reviderer bare utkastets berørte rader før seal.
- Hashlesing registrerer før-/etterfingerprint, evidenskind og algorithm/schema-versjon.
- Full duplikatskann er separat og klassifiserer hardlinks/samme filobjekt før innholdsduplikater.

### 14.8 Risikoscore og sikkerhetsporter

- **Low:** bare nye kopier og mapper.
- **Medium:** erstatning med versjonering eller metadataavvik.
- **High:** karantene, konflikt, stor endringsandel eller portabilitetstap.
- **Blocked:** identitetsavvik, ufullstendig coverage, lease-/kapabilitetsbrudd eller hard sikkerhetsregel.

Risikoscore er forklaring/prioritering, ikke autorisasjon. Hard gates evalueres separat og kan aldri senkes av en lav totalscore.

### 14.9 Brukeroverstyring som avledet plan

Brukeren kan velge hopp over, kopier likevel der policy tillater det, behold begge, ikke harmoniser metadata eller ikke sett i karantene. Overstyring:

1. oppretter et nytt immutable planutkast med `parent_plan_id`;
2. lagrer den eksplisitte beslutningen og bruker-ID/session;
3. beregner operations/dependencies/preconditions og checksum på nytt;
4. forsegles som en ny plan;
5. endrer aldri den opprinnelige godkjente planen.

Stibegrensning, feil endepunkt, ufullstendig destructive coverage, filstørrelsesgrense, manglende lease og andre harde gates kan ikke overstyres.

### 14.10 Planseal og utførelseskompatibilitet

Sealtransaksjonen lagrer:

- plan-/operation-/canonical-serializer-versjon;
- job-/endpoint-/filterrevisjoner og hashes;
- writer-eier, `ownership_epoch` og kontrollområdeschema per skrivbart mål;
- snapshots, coverage status, per-katalog casekontekst og kapabilitetshashes;
- eksakt baseline-sett/context/generasjon for toveis, eller eksplisitt etableringskontekst uten tidligere baseline;
- utførelsespolicy, source-guard-/assurancepolicy og hashevidenspolicy;
- deterministiske konflikt-/navneallokeringer og objektroller;
- operation count/bytes/risiko per mål;
- kanonisk operasjonsstrøm, dependency graph og preconditions;
- checksumalgoritme og `plan_checksum`.

Kanonisk serialisering har dokumentert feltorden, UTF-8, heltalls-/nullregler, path escaping og sorteringsregler. Den er en eksplisitt versjonert protokoll, ikke vanlig JSON-dump eller implisitt SQLite-rekkefølge.

Før run:

- Engine Host verifiserer checksum og at plan ikke er mutert;
- gjeldende build må støtte planner-, operation- og serializer-versjonen semantisk;
- jobbrevisjon, endpointgenerasjon, owner/ownership epoch, policy og relevante capabilities må fortsatt matche;
- per-katalog casekontekst som inngår i muterende operasjoner må fortsatt være kompatibel;
- en toveisplan må fortsatt peke til samme aktive baseline-sett/generasjon/context hash; mismatch kan ikke «oppgraderes» i executor;
- hver operasjon revaliderer live source-/targetpreconditions og guardpolicy;
- ferdige outcomes/staging kan gjenbrukes bare med samme seal, owner/epoch og bevist postcondition.

Ved incompatibility bygges ny plan. En gammel plan migreres ikke in-place.

### 14.11 Ytelseskrav

- O(n) over sorterte cursors etter indeksering;
- minne bundet av cursor-, hash- og writerbatcher;
- ingen millionstore Python-/Qt-objektgrafer;
- query plans og indekser testes med `EXPLAIN QUERY PLAN`;
- determinisme property-testes med varierende batch-/workerrekkefølge;
- planseal og checksum kan rekonstrueres/valideres uten å laste alle operasjoner i minnet.
