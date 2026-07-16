# Endringslogg

> Kanonisk endringslogg. Seksjon 28 i `MASTER_SPEC.md` genereres herfra.

## 28. Revisjonslogg

### 2.9.2 — 2026-07-16

- erstattet den interne rot-README-en med en ryddig GitHub-forside for fremtidige brukere, bidragsytere og prosjekteier;
- lagt inn tydelig pre-alpha-advarsel, produktoversikt, sikkerhetsløfter, roadmap, målgruppetilpassede innganger og valideringskommandoer;
- gjort GUI-konseptbildet synlig på forsiden med en eksplisitt ikke-pikselbindende forklaring;
- lagt til `docs/README.md` som rollebasert dokumentasjonsindeks for produkt, UX, arkitektur, recovery, implementasjon, testing og governance;
- presisert rollene til `README.md`, `AGENTS.md`, `docs/README.md` og den genererte `MASTER_SPEC.md` i repositoryspesifikasjonen;
- utvidet overleveringsvalidatoren med regresjonskontroller for GitHub-forsiden og dokumentasjonsindeksen;
- oppdatert versjonsmarkører, generert master og integritetsmanifest for v2.9.2.

### 2.9.1 — 2026-07-16

- rettet dokumentfooter og alle operative versjonsmarkører til v2.9.1;
- synkronisert IPC-kommandoeksempelet med `schema/ipc-command.schema.json`, inkludert wire-felter, forventet revisjon og korrekt BLAKE3-hash over JCS-kanonisk payload;
- standardisert wire- og lagringsbegrepet til `command_name`;
- samlet command receipt-livssyklusen i én kanonisk tilstandsmaskin med `REJECTED`, valgfri `RUNNING` og terminal `SUCCEEDED`/`FAILED`/`CANCELLED`;
- utvidet validatoren slik at Markdown-eksempelet, JSON Schema-eksempelet og command-state-vokabularet ikke kan drive fra hverandre.

### 2.9 — 2026-07-15

- gjort fagfilene under `docs/` til kanoniske kilder og `MASTER_SPEC.md` til deterministisk generert artefakt;
- rettet dokumentpresedensen slik at godkjente krav og ADR-er styrer kontrakter, constraints og tester;
- innført komplett, maskinlesbar ADR-katalog for `ADR-001`–`ADR-028` og synkronisert beslutningsregisteret;
- skilt ADR-ens `evidence_status` fra prosjekteierens `owner_decision`, inkludert scope-reduksjon;
- gjort baselinevalidatoren fail-closed når obligatoriske valideringsbiblioteker mangler;
- lagt til formatvaliderte JSON-eksempler, ADR-/kravkryssjekk, ankerkontroll, manifestdekning og Unicode-kontroll;
- fjernet skjulte soft-hyphen-tegn og gjort slike formateringstegn til valideringsfeil;
- lagt inn en eksplisitt vertikal leveransestige fra Alpha 0.1 til senere avansert synkronisering;
- begrenset tidlig port-/interfacebruk til reelle prosess-, autoritets-, lagrings- og OS-grenser;
- inkludert GUI-konseptbildet som ikke-pikselbindende designreferanse;
- lagt til sjekklister for brukervennlighet og benchmarks samt et låst valideringsmiljø;
- oppdatert integritetsmanifestet og bygget en ny verifisert sluttpakke.

### 2.8 — 2026-07-15

- delt Milepæl 0A i syv sekvensielle, eierstyrte arbeidspakker med egen scope, filliste og kvalitetsport;
- gjort miljøpreflight til eneste første Codex-oppgave og forbudt automatisk overgang til neste arbeidspakke;
- innført delvise blockers slik at manglende fler-maskin-lab ikke stopper uavhengige lokale bevis;
- presisert tillatte støtteområder, testrotmarkør og fail-closed cleanup for alle muterende spiker;
- skilt Codex-anbefaling fra eiergodkjenning med ADR-statusene `EVIDENCE_COMPLETE`, `RECOMMENDED` og `OWNER_ACCEPTED`;
- synkronisert endpointmarkør og IPC-hashkontrakter med eksplisitt `BLAKE3-256`, `JCS-RFC8785`, scope og obligatoriske algoritmefelt;
- rettet kontrollområdets Markdown-tre og fjernet den byteidentiske duplikatmasteren fra Codex-pakken;
- lagt til overleveringssjekkliste, målrettede spikeinstrukser, bundlevalidator og integritetsmanifest;
- gjort masterfilen til eksplisitt referanse i stedet for obligatorisk full prompt.


### 2.7 — 2026-07-15

- lukket flerinstallasjonshullet med én autorisert writer-installasjon per målrot og eierskapsepoke, fremmed owner som read-only og eksplisitt takeover-saga;
- namespacet kontrollområdet per installasjon og gjort global marker/lock separat fra staging-, recovery-, version- og quarantineobjekter;
- definert full klassifisering av `.mediasync`, inkludert ukjent brukerinnhold, case-alias, nyere schema, delvis og korrupt markør; standardfilteret ekskluderer bare validert kontrollområde;
- erstattet sirkulære aktive revisjonspekere med separate head-tabeller og krevd sammensatte parent-scope-fremmednøkler for alle sikkerhetsrelevante relasjoner;
- gjort per-katalog case-sensitivitet og case-context hash til del av snapshot-, plan- og preconditionbevis;
- innført eksplisitte hash-evidensnivåer og forbud mot at metadatarevalidert cache alene driver `SKIP_IDENTICAL`;
- lagt til `SourceReadGuard` eller post-transfer current-hash-fallback for å lukke source-TOCTOU så langt endepunktet tillater;
- erstattet speilede interne kontrollstier med korte managed objects og checksummede manifester for staging, versjonering, karantene og restore;
- gjort konfliktnavn deterministiske og materialisert før planseal; «behold begge» er en recoverybeskyttet saga;
- skilt flere stier til samme filobjekt fra reelle innholdsduplikater og fjernet falsk spareberegning for hardlinks/same-object;
- skilt transfer, assurance og durability som separate resultataxer i datamodell, GUI, audit og akseptansekriterier;
- gjort claims monotone i levende Engine Host og flyttet wall-clock/UTC til diagnostikk og startup-reconciliation;
- begrenset endepunkter uten pålitelig lock til dokumentert `COPY_NEW_ONLY_NO_REPLACE`, med automatikk av som standard;
- lagt til lokal AppData-kapasitetsmodell, kvoter og sikker `SQLITE_FULL`-håndtering;
- avgrenset avanserte regulære uttrykk med tids-/ressursbudsjett og cancellation;
- herdet Robocopy-resolusjon med `GetSystemDirectoryW`, kanonisk Windows-argumentbygger og round-trip-tester;
- gitt katalogoppretting, katalogmetadata, karantene og restore egne idempotente recoverytilstandsmaskiner;
- lagt inn Milepæl 0A som obligatorisk arkitekturspike med to ekte Windows-klienter/VM-er for SMB-eierskap, én-vs-to-database-ADR, source-guard-, langsti-, Job Object-, argv- og pakkebevis;
- utvidet teststrategien med wrong-parent-FK-er, ownership epoch, kontrollområde, hash-evidens, source-races, short objects, wall-clock-jumps, lokal full disk og directory recovery;
- utvidet akseptansekriteriene og kravsporingen for de nye arkitekturkravene, og koblet `DUP-001`/`HASH-001` eksplisitt til hashing-/duplikatmilepælen;
- erstattet oppstartsprompten slik at Codex utfører kun Milepæl 0A og stopper ved manglende reelt testmiljø i stedet for å fabrikkere bevis;
- gjort dokumentpakken og maskinlesbare kontrakter til operativ Codex-inngang, mens masterfilen er konsolidert referanse;
- fjernet selvmotsigelsen mellom et påstått låst to-databasedesign og Milepæl 0A: to-databasemodellen er nå eksplisitt kandidat, og ADR-003 må fryses med målte crash-/ytelsesbevis før produksjonsskjema.

### 2.6 — 2026-07-15

- innført en separat headless Engine Host som eneste muterende tilstandseier, databasewriter, migrator, scheduler- og recoveryeier;
- definert en eksplisitt catalog ↔ recovery handoff/saga med separate skrivetransaksjoner, korrelasjons-ID, startup reconciliation og forbud mot falsk cross-store atomisitet;
- erstattet per-operasjon target-vitner med bounded, immutable og hashkjedede intentsegmenter som bruker relative stier og skalerer uten én kontrollfil per brukerfil;
- herdet command inbox med global idempotency key, verifisert principal/schema/payloadhash og monoton receiptlivssyklus på tvers av klienter og restart;
- gjort named-pipe-IPC local-only med verifisering av faktisk klienttoken/SID/session samt grenser for klienter, frames, requests, subscriptions og eventrate;
- gjort alle runtime-roller unelevated og definert sikker prosessoppretting med controlled DLL-søk, minimalt miljø og eksplisitt handleliste;
- krevd at transferchild opprettes suspended, innlemmes i no-breakaway/kill-on-close Job Object og først deretter får kjøre;
- innført aktive root claims som atomisk materialisert sett, mens historiske/arkiverte claims bare bevarer audit og reaktivering revalideres;
- innført checksummede snapshotbatch-receipts, immutable snapshot-/planseal og blokkering av sen mutasjon;
- bundet toveisbaseline til immutable baseline sets og eksakt baseline context hash for endepunkt-, filter-, case-, tid-, metadata-, konflikt- og plannerversjon;
- lagt til migration epoch med per-database backup/high-water og deterministisk resume/restore etter delvis migrasjon;
- lagt til referansedrevet database-retention med cross-store recovery-root-export/high-water, holds, `retention_pending`, immutable delete manifest og idempotente batcher;
- gjort databasekomprimering til en egen checksummet epoch med verifisert `VACUUM INTO`-output, rollbackfil og restartbar swap;
- herdet SQLite med lokal ACL-beskyttet plassering, query-only read pool, deaktivert extension loading og `trusted_schema=OFF` der støttet;
- gjort GUI, launcher, trigger client og systemstatusfelt til rene kontrollplansklienter uten direkte SQLite-, Robocopy- eller filsystemmutasjon;
- definert ACL-beskyttet, versjonert og størrelsesbegrenset lokal IPC med handshake, idempotente commands, `command_receipts`, payloadhash og reconnect;
- innført immutable jobb-, filter-, endepunkt- og planrevisjoner samt versjonert canonical serializer og planseal;
- skilt korrekthetsleases fra ytelsestokens og gjort OS-handlebasert endpointlease til bindende precondition;
- lagt til target compare-and-swap, parent identity, reparse/final-path-revalidering og bounded target-side intentsegmenter før commit/karantene;
- utvidet recoveryprotokollen med `TARGET_PRECONDITION_VALIDATED`, `STAGING_DURABLE`, `COMMIT_PRECONDITIONS_REVALIDATED` og `FINAL_DURABLE`;
- gjort live-skannens begrensninger eksplisitte og lagt til katalogcoverage, volatility, snapshot issues og destructive absence proof;
- isolert Robocopy til manifeststyrt staging med absolutt systemsti, minimalt miljø, kontrollert handle inheritance og Windows Job Object;
- lagt til eksakt post-transfer stagingenumerering, manifestchecksum og blokkering av ekstra, manglende eller reparse-baserte resultater;
- definert transactional outbox og desired-state-reconciliation for Task Scheduler og varsler;
- utvidet datamodellen med revisjoner, aktive/immutable root claims, command receipts, trigger occurrences, run targets, leases, outbox, handoffs og recovery-intentsegmenter;
- omarbeidet milepælene slik at arkitekturporter, Engine Host/IPC, leases/path-herding og deterministic planning kommer før transfer/GUI-funksjoner;
- utvidet arkitektur-, IPC-, concurrency-, TOCTOU-, migration-, upgrade-, orphan-process- og fault-injection-testene;
- lagt til akseptansekriterier som beviser state ownership, idempotency, process supervision, immutable plans, leases og durability honesty;
- herdet kodekvalitetsreglene med import-linter, composition-root-eierskap, korte transaksjoner, strukturert cancellation, reproduserbare dependency-hasher og forbud mot alternativ muterende kodebane;
- oppdatert Codex-oppstartsprompten og de offisielle tekniske referansene for den nye arkitekturen.

### 2.5 — 2026-07-15

- harmonisert utkast, oppretting og redigering: nye utkast lagres automatisk, mens etablerte jobber krever eksplisitt lagring og konsekvensoppsummering;
- delt primærteksten for første kontroll og kontroll etter konfigurasjonsendring;
- gjort dashboardets tilgjengelighetsoppdatering eksplisitt forskjellig fra kontroll av filinnhold;
- blokkert like, nestede og overlappende kilde-/målrot i krav, GUI, datavalidering, tester og akseptansekriterier;
- lagt til brukerrettede forklaringer når et mål ligger i kilden eller overlapper et annet mål;
- skilt analysens livssyklustilstand fra resultattypen i datamodellen;
- harmonisert duplikat-, historikk- og gjenopprettingsterminologi i normal brukerflate;
- fjernet motsetningen mellom solid primærknapp og merkegradient;
- skilt aktivitet, oppmerksomhet og per-mål-ferskhet slik at aktive kjøringer og samtidige advarsler kan forstås samtidig;
- forenklet analysevisningen til én startknapp, tydeligere navigasjonshandlinger og konsistente binære størrelsesenheter;
- lagt til trygg arkivering/reaktivering av jobber uten å endre brukerfiler eller miste historikk;
- utvidet brukervennlighetsporten med trygg redigering av etablert jobb og blokkering av selvrefererende backupoppsett.

### 2.4 — 2026-07-15

- gjort `Oppdatert` avhengig av komplett, gyldig analyse; filovervåking kan bare ugyldiggjøre status eller melde endringer;
- lagt til en eksplisitt `Ingen endringer`-tilstand som ikke oppretter tom kjøring;
- inkludert forventede filterhopp i den trygge hurtigflyten;
- gjort automatikkpolicy og utsatte handlinger til en del av den uforanderlige planen og revisjonssporet;
- samordnet interne operasjonsnavn mellom planmotor og GUI;
- lagt til `Fullført – handling nødvendig` som eget resultat per kjøring og mål;
- strammet målutvalg, systemstatusfelt, fremdrift, bekreftelser og sannferdig per-mål-ferskhet;
- språkvasket brukerflater og milepæler for gjenoppretting, sikkerhetskontroll, kortvarige meldinger og norske fagbegreper;
- utvidet milepæler og akseptansekriterier med nullendringsflyt og eksplisitt automatikkpolicy.
- erstattet misvisende `Alle filer`/`Balansert kontroll` i normal GUI med `Alle brukerfiler`/`Standard kontroll` og synlige automatiske unntak;
- lagt til påvisning og tydelig presentasjon av mål som deler samme fysiske lagringsenhet;
- gjort første langvarige kontroll bakgrunnsvennlig og fjernet redundant bekreftelse etter et eksplisitt valg om å kjøre på tilgjengelige mål.
- gjort kontroller til egne historikkaktiviteter og fjernet tomme kjøringer både ved null endringer og når alle funn venter på kontroll;
- erstattet brukerrettet `inspector` med `detaljpanel` uten å endre interne komponentnavn.

### 2.3 — 2026-07-15

- gjort **Backupjobb** til primær mental modell og flyttet `pair_sync` til separat avansert opprettingsflyt;
- redusert standard jobboppretting fra åtte obligatoriske steg til fire skjermer med sikre standarder;
- innført oppmerksomhetsstyrt dashboard, sannferdig per-mål-ferskhet og forbud mot ubegrunnede `Beskyttet`-/`Oppdatert`-påstander;
- definert **Kjør backup** som én bevisst handling som alltid analyserer og bare fortsetter automatisk ved en ren, ikke-destruktiv plan;
- skjult uendrede filer som standard og prioritert endringer, blokkeringer og anbefalt neste handling;
- lagt til eksplisitt målutvalg, delvis kjøring og `N av M mål` i fremdrift, resultat og historikk;
- lagt til stabil fullføringsoppsummering, målspesifikt nytt forsøk og kort gjenopprettingsflyt;
- redusert globale innstillinger og flyttet jobbvalg/diagnostikk til riktig kontekst;
- erstattet tvungen onboarding-karusell med tomtilstand, valgfri omvisning og kontekstuelle tips;
- strammet modalbruk, fokus ved farehandlinger, mikrocopy, teknisk terminologi og tastatursnarveier;
- lagt til konservativ automatikkpolicy for bare nye filer og tydelig `handling nødvendig` for utsatte endringer;
- lagt til kanoniske UX-krav, nye akseptansekriterier og en manuell brukervennlighetsport med oppgavetester.

### 2.2 — 2026-07-15

- rettet case-kollisjonsmodellen slik at alle poster beholdes;
- skilt bulk-/katalogdata fra varig gjenopprettingsjournal;
- erstattet falsk «atomisk commit» med journalført, idempotent flerfaseprotokoll;
- korrigert diskplassberegning til peak staging;
- erstattet `ctime_ns` med `birthtime_ns` for Windows-opprettelsestid;
- utvidet endepunktprofil med filstørrelse, navnelengde, case, replace og metadataegenskaper;
- presisert Task Scheduler-logontyper for NAS/UNC;
- begrenset reverse/toveis til `pair_sync`;
- innført run/attempt/outcome-modell, baseline-nøkler, aktiv filterversjon og operasjonsorden;
- gjort ustabilitetskontroll batchbasert;
- gitt hver Robocopy-batch unik logg og skilt `/MT` fra prosessantall;
- definert named-stream-policy og fil-ID-tillit;
- redusert toppnavigasjonen og utsatt full miniatyrgrid;
- korrigert kontrasttokens og lagt til automatiske tokenpartester;
- skilt forventede backupreplikaer fra reelle duplikater;
- delt GUI- og produktleveransen i mindre vertikale milepæler;
- lagt til kanoniske krav-ID-er og sporingsmatrise;
- gjort skrivbare endepunkter eksplisitte også for `pair_sync`;
- normalisert filterversjoner, planretning og katalogens `FULL`-transaksjoner;
- lagt inn destruktiv revalidering ved kildedrift;
- valgt en portabel, varslet named-stream-standard uten å love falsk ekvivalens;
- språkvasket tekniske beskrivelser, normalisert GUI-begreper og fjernet resterende tvetydige formuleringer.

### 2.1 — 2026-07-15

- ytelses-, effektivitets- og designpolering;
- strømmet pipeline, adaptive Robocopy-profiler og GUI-latensbudsjetter.

---

**Slutt på implementeringsplan — dokumentversjon 2.9.2.**
