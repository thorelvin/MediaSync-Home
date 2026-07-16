# Ytelse og ressursstyring


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Backpressure, scheduler, lagringsprofiler, målemetode og utgivelsesbudsjetter.


## 16. Ressursstyring og selvbalanserende overføring

### 16.1 To separate mekanismer

Arkitekturen skal aldri blande:

1. **Korrekthetsleases:** eksklusivt eierskap til Engine Host, run og skrivbare endepunkter. Disse er harde preconditions og kan ikke overstyres for ytelse.
2. **Ytelsestokens:** reversible schedulerreservasjoner for disk, share, nettverk, hashing og prosessplasser. Disse styrer throughput, ikke mutasjonstillatelse.

Mistet korrekthetslease stopper nye mutasjoner. Manglende ytelsestoken betyr bare venting. En database-/heartbeat-rad er ikke OS-leasen.

### 16.2 Mål og brukerprofiler

Scheduler skal maksimere stabil samlet gjennomstrømning på 1 Gbit/s, HDD, SSD, USB og blandede mediefiler uten seek-storm, nettverkskø eller minnevekst.

| Profil | Oppførsel |
|---|---|
| **Skånsom** | Lav CPU, én prosess per flaskehals, bakgrunnshashing pauset under kopi |
| **Auto** | Standard; målinger, hysterese og avgrenset ressursbruk |
| **Maks gjennomstrømning** | Høyere grenser på SSD/uavhengige mål, samme sikkerhetsporter |

Brukeren velger profil, ikke rå prosess-/Robocopy-parametere.

### 16.3 Ressursgraf og canonical acquisition

Aktiviteter bruker stabile ressursnøkler for:

- fysisk kildevolum;
- fysisk målvolum;
- SMB-server/share;
- nettverksadapterklasse når kjent;
- hashlesing fra kilden;
- Robocopy-prosessplass;
- commit/rename på målrot.

Mapped drives til samme UNC deler nøkkel. Ukjent fysisk identitet grupperes konservativt.

Regler:

- flere tokens anskaffes i kanonisk sortert nøkkelrekkefølge;
- acquisition har cancellation/deadline og holder ingen SQLite-transaksjon;
- delvis anskaffede tokens frigis ved failure/cancel;
- tokeneieskap er in-memory under én Engine Host og kan rekonstrueres fra autoritativ runstate;
- scheduler forsøker aldri å «reservere» et endepunkt i stedet for endpointleasen;
- waiting work har fairness/aging slik at ett stort mål ikke sulter små, men sikkerhetskritisk recovery prioriteres.

### 16.4 Startheuristikk

| Arbeidslast | `/MT` i prosess | Prosesser per mål | Aktive mål fra samme kilde |
|---|---:|---:|---:|
| Mange små filer, SSD → SSD | 16 | 1 | 2 |
| Mange små filer, SSD → NAS 1 Gb | 8 | 1 | 1 |
| Blandet, SSD → NAS | 8 | 1 | 1 |
| HDD → NAS | 4 | 1 | 1 |
| Én stor videofil | 1 | 1 | 1 |
| Få store videofiler, SSD → uavhengige mål | 2 | 1 | 1–2 |
| USB-HDD → NAS | 4 | 1 | 1 |
| NAS → lokal HDD | 4 | 1 | 1 |

Dette er startpunkter, ikke garantier. `/MT`, prosessantall og aktive mål måles og vises separat.

### 16.5 Adaptiv regulator

Etter sammenlignbare batcher måles:

- throughput-EWMA og batchvarighet;
- disk-/nettverksbelastning når pålitelig;
- kødybde og waittid per ressurs;
- retry, latency og feil;
- samlet throughput og fairness på tvers av mål;
- sikkerhets-I/O som hash/flush/manifest separat.

Regulatoren:

1. endrer én parameter om gangen;
2. bruker små trinn;
3. krever minst tre sammenlignbare batcher;
4. bruker 10–15 % hysterese og cooldown;
5. ruller tilbake ved lavere total throughput, mer kø eller flere feil;
6. lagrer beste profil per endpointpar/arbeidslast, men revaliderer etter kapabilitets-/nettverksendring;
7. endrer aldri plan, verifikasjonsnivå, leases eller sikkerhetsporter.

### 16.6 Fler-mål-policy

- Kildesnapshot, hash og planmetadata deles.
- HDD/USB-HDD leses normalt mot ett mål om gangen.
- SSD kan mate to uavhengige mål hvis total throughput og latency forbedres.
- Shares på samme NAS behandles som samme flaskehals til målinger viser noe annet.
- Offline/tregt mål kan vente mens andre mål fullfører.
- Hvert mål har egen run-target-state, lease og retrybudsjett.
- Scheduler kopierer ikke fra backupmål til annet mål i første versjon.

### 16.7 Hash- og copy-samspill

- Full duplikatskann har lav prioritet og pauses ved diskbegrenset kopi.
- Hash nødvendig for planseal/verification får kontrollert prioritet.
- Kildehash beregnes én gang per gyldig cachegenerasjon.
- Samme NAS fullhashles og skrives ikke aggressivt samtidig ved throughputfall.
- Hasharbeid holder ingen endpointlease lenger enn nødvendig; muterende commit beholder lease etter §4.

### 16.8 Kø-, minne- og overloadgrenser

Startmål for én million poster:

- peak RSS ved normal skann/analyse ≤ 400 MiB;
- alle køer har eksplisitt maksimum og overflowpolicy;
- recovery-/commandkø kan ikke droppes og har egen liten reserve;
- progress/events kan coalesces eller droppes etter sekvensnummer;
- GUI-tabellcache holder få sider;
- forhåndsvisningscache er liten og rekonstruerbar;
- batchmanifest er begrenset og kan strømles fra database;
- overlast reduserer prefetch, preview, bakgrunnshash og parallelle mål før correctness påvirkes.

En kø uten maksimum er en arkitekturfeil og skal fanges av review/test.

### 16.9 1 Gbit/s og målerapport

Rapporten skiller kildelesing, målskriving, nettverk, Robocopy/MediaSync-overhead, waittid og ekstra sikkerhets-I/O. Kvalitetsporten sammenligner samme datasett/endepunkter med direkte Robocopy og dokumenterer profil, batcher, CPU, prosesser og verifikasjonsnivå.

## Ytelses- og stressmåling

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
