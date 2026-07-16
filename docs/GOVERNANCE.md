# Dokumentstyring og Codex-instruks

> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte.

## 0. Instruks til Codex

Når repositoryet inneholder `AGENTS.md`, les bare dokumentene den peker til for gjeldende arbeidspakke. Bruk denne masterfilen som konsolidert referanse når målrettede dokumenter mangler nødvendig kontekst; ikke last den som full standardprompt.

### 0.1 Arbeidsmåte

1. Implementer én milepæl om gangen, i rekkefølgen angitt i kapittel 20.
2. Del arbeidet i små, gjennomgåbare endringer. Ikke bygg hele programmet i én fil eller ett stort steg.
3. Etter hver milepæl:
   - kjør alle relevante tester;
   - kjør linting og typekontroll;
   - oppdater `docs/IMPLEMENTATION_STATUS.md`;
   - dokumenter kjente avvik;
   - stopp ved milepælens kvalitetsport før neste milepæl.
4. Bruk krav-ID-ene i kapittel 3 som sporingsnøkler i kode, tester, dokumentasjon og PR-beskrivelser. Velg alltid den sikreste oppførselen når et krav er tvetydig.
5. Domenelogikk, skanning, sammenligning og kjøreplan skal kunne testes uten GUI og uten ekte NAS.
6. GUI-tråden skal aldri utføre filskanning, hashing, databasevedlikehold eller Robocopy-kjøring.
7. GUI-en skal følge designsystemet i kapittel 8. Farger, avstander, radius, typografi og ikoner skal komme fra sentrale tokens og registre.
8. En GUI-side regnes ikke som ferdig før relevante tom-, laste-, offline-, feil- og tilgjengelighetstilstander er implementert.
9. All produksjonskode og alle kodeidentifikatorer skrives på engelsk. GUI-tekst leveres først på norsk bokmål, med støtte for engelsk oversettelse.
10. Programmet skal være fullt brukbart uten internettilgang.
11. Mål og instrumenter varme kodebaner fra første milepæl; ikke vent til utgivelsesfasen med å oppdage arkitekturflaskehalser.
12. Ingen fase skal materialisere hele filkatalogen, operasjonsplanen eller GUI-tabellen i minnet.
13. Bruk avgrensede køer og backpressure mellom skanning, database, planlegging, overføring og verifisering.
14. Sammenlign ytelsen mot rå `os.scandir`, SQLite-bulkinnlasting og direkte Robocopy på samme maskin og datasett.
15. Optimaliser målt total gjennomstrømning og brukeropplevd ventetid, ikke bare enkeltfunksjoner eller syntetiske mikrobenchmarks.
16. Implementer standardflyten for hjemmebackup før avanserte synkroniseringsvalg. En bruker skal ikke måtte åpne avanserte seksjoner for å opprette eller kjøre en vanlig backup.
17. Hver brukerrettet tilstand skal ha én tydelig anbefalt neste handling. Ikke eksponer interne begreper som endepunkt, snapshot, plan-checksum, batch, commit eller Robocopy i normal visning.
18. En GUI-leveranse er ikke ferdig før oppgavetestene i §8.30 er gjennomført manuelt og resultatene er registrert i `docs/USABILITY_CHECKLIST.md`.
19. GUI-prosessen skal aldri åpne en skrivbar databaseforbindelse eller konstruere muterende filsystem-/Robocopy-adapters. Alle muterende use cases går gjennom Engine Host.
20. Alle interne kommandoer skal være versjonerte, idempotente og persisteres før endelig suksessrespons. En reconnect eller Task Scheduler-retry skal ikke opprette duplikatkjøring.
21. En forseglet plan, jobbrevisjon eller endepunktrevisjon skal aldri patches in-place. Endring oppretter ny revisjon eller avledet plan.
22. Ingen filsystemmutasjon skjer uten aktiv lease, matching target-precondition og varig recoveryintensjon.
23. Dependency-retningen i §9.6 håndheves med architecture tests i CI; dokumenterte grenser uten test regnes ikke som ferdige.
24. Databasetransaksjoner skal avsluttes før kode venter på fil-I/O, Robocopy, IPC, sleep eller brukerinput.
25. Launcher, GUI, Engine Host, trigger client og transferarbeidere kjører som standardbruker uten UAC-elevasjon, backup-/restoreprivilegier eller administratoravhengig fast path.
26. Plan-, IPC- og recoverypayloads lagrer bare persistente IDs og endepunktrelative stier. Absolutte bruker-/målstier rekonstrueres bare av `SafePath` fra en validert immutable endpointrevisjon.
27. Et forseglet snapshot er skrivebeskyttet. Sen hash eller ny metadata lagres i versjonert cache/avledet artefakt og kan aldri endre en plan som allerede er forseglet.
28. En ekstern transferprosess får ikke kjøre én instruksjon før prosessupervisoren har plassert den i riktig Job Object og bekreftet containment.
29. Dersom ADR-003 beholder separate katalog- og recoverydatabaser, har de aldri samtidige write-transaksjoner i samme use case. Cross-store-flyt bruker varige handoffs og kan gjenopptas etter hvert mellomsteg.
30. Application-laget skal ikke ha en generell «skriv fil»-port. Final tree kan bare endres gjennom en smal `CommitPort` som krever en levende, ikke-serialiserbar `MutationPermit` og en verifisert stagingartefakt.
31. Hver mutasjonslease skal ha en monoton fencing token. Stale worker-resultater, meldinger eller recoveryforsøk med eldre token skal avvises før ny filsystemmutasjon.
32. Durable inbox-, trigger- og outboxnøkler skal overleve vanlig historikkretention som kompakte dedupliseringstombstones. En gammel retry kan ikke bli en ny sideeffekt bare fordi detaljhistorikk er komprimert.
33. Intern databasebackup, restore og komprimeringsswap dekker alle autoritative state stores valgt av ADR-003 som ett manifestert epoch-sett; dersom flere filer brukes, kan de aldri blandes på tvers av epoker.
34. Ett skrivbart MediaSync-rotområde har nøyaktig én autorisert writer-installasjon per `ownership_epoch`. En annen installasjon er read-only til en eksplisitt overtakelsessaga er fullført.
35. En mappe med navnet `.mediasync` ekskluderes eller adopteres aldri bare på grunn av navnet. Kontrollområdet må klassifiseres og valideres mot checksummet markør før det behandles som produktmetadata.
36. Alle parent-child-forhold som påvirker sikkerhet skal håndheves med sammensatte fremmednøkler, unike parent-scope-nøkler eller en dokumentert database-trigger. Python-validering alene er ikke tilstrekkelig.
37. En gammel hashcachepost kan bare drive `SKIP_IDENTICAL` når evidensnivået beviser nåværende innhold, ikke bare uendrede metadata.
38. Source-preconditions skal bindes så nær Robocopys lesing som endepunktet tillater. Ubeskyttet kilde krever post-transfer-bevis eller utsettelse; metadata alene er ikke sikkert TOCTOU-bevis.
39. Staging, versjoner og karantene bruker korte, objektbaserte kontrollstier med manifester. De skal ikke speile hele brukerens relative mappetre under `.mediasync`.
40. Leases og claims i en levende Engine Host bruker monoton klokke. Persistente UTC-tidspunkter er diagnostikk og kan ikke alene avgjøre at en claim er utløpt etter prosess- eller systemrestart.
41. Eksakte tabellfelter, wire-formater, årsakskoder og state transitions skal finnes som versjonerte maskinlesbare kontrakter. Markdown forklarer hensikt; kontraktene og testene håndhever formen.

### 0.2 Absolutte sikkerhetsregler

Følgende regler er ikke valgfrie:

- Ikke bruk Robocopy-flaggene `/MIR`, `/PURGE`, `/MOVE` eller `/MOV` i produksjonsflyten.
- Ikke bruk `shell=True` ved prosessoppstart.
- Ikke slett eller flytt noe før både kilde og mål er verifisert mot lagret endepunktidentitet.
- Ikke foreta destruktive handlinger når en skanning er ufullstendig, en mappe er utilgjengelig eller det finnes uavklarte skannefeil.
- Ikke skriv direkte over en eksisterende gyldig målfil. Kopier først til staging, verifiser, registrer en varig commit-intensjon og bruk den dokumenterte replace-/fallback-flyten.
- Ikke la en brukerdefinert relativ sti kunne unnslippe et konfigurert rotområde.
- Ikke kjør to instanser av samme jobb mot samme mål samtidig.
- Ikke automatisk slett identiske filer som er funnet av duplikatdeteksjonen.
- Ikke kopier ACL, eier, auditing eller privilegerte sikkerhetsdata som standard.
- Ikke følg NTFS-junctions, reparse points eller symbolske lenker som standard.
- Ikke etterlign Allway Sync-navn, logo, ikoner, skjermbilder eller pikselnøyaktig design. Bruk bare den generelle arbeidsflyten med jobber, endepunkter, analyse og synkronisering som funksjonell referanse.
- Ikke behandle filsystemet og SQLite som én atomisk transaksjon. Alle irreversible filsystemsteg skal ha en varig, idempotent gjenopprettingstilstand før og etter steget.
- Ikke la GUI, trigger client eller launcher endre brukerfiler, starte Robocopy direkte eller åpne skrivbar katalog-/recoverydatabase.
- Ikke la Robocopy skrive til final tree; den får bare et unikt stagingmål.
- Ikke utfør mutasjon uten eksklusiv mållease og umiddelbart revalidert target-precondition.
- Ikke bruk `pickle`, dynamisk kodeinnlasting eller vilkårlig Python-objektserialisering i IPC.
- Ikke stole på pipe-event, logg, stale lease-rad eller prosess-ID som eneste korrekthetsbevis.
- Ikke starte en intern rolle med vilkårlige stier eller kommandoargumenter; interne entry points mottar bare validerte IDs og triggerdata.
- Ikke kjør noen produktprosess elevért eller med `SeBackupPrivilege`/`SeRestorePrivilege` som standard; manglende tilgang skal være en synlig blokkering, ikke en skjult privilege escalation.
- Ikke eksponer en generell muterende filsystemadapter til GUI, domain eller application. Bare commit-/karantene-/versjonsadaptere med eksplisitt mutasjonskapabilitet kan endre final tree.
- Ikke godta et worker-resultat, en commitmelding eller et recoveryforsøk med stale fencing token, selv om `run_id` og sti ellers matcher.
- Ikke slett en idempotency key eller trigger-dedupliseringsnøkkel slik at en forsinket retry kan skape en ny kjøring eller sideeffekt.
- Ikke restore én intern database fra et backupsett og beholde den andre fra en annen epoch.
- Ikke persister absolutte source-, target-, staging-, version-, quarantine- eller intentsegmentstier i plan/recovery. Persistér endepunkt-ID/revisjon og relative stier, og løs dem gjennom `SafePath` ved bruk.
- Ikke oppdater et forseglet snapshot eller dets filposter med sent beregnede hash-/metadatafelt.
- Ikke resume en ny Robocopy-prosess før den er opprettet suspended, tilordnet kontrollert Job Object og kontrollert for arvede handles/breakaway.
- Ikke hold write-transaksjoner i begge SQLite-databaser samtidig; bruk eksplisitte handoffrader og idempotent avstemming.
- Ikke muter et skrivbart mål dersom `endpoint.json` viser en annen `owner_installation_id`, ukjent `ownership_epoch` eller uavklart eierskap.
- Ikke ekskluder, overskriv, reparer eller adopter en eksisterende `.mediasync`-mappe uten klassifisert kontrolltilstand og eksplisitt sikker flyt.
- Ikke bruk en lokal fencing-token som om den var global mellom installasjoner. Autorisasjonen er tuple `(ownership_epoch, local_fencing_token)` under global endpointlock.
- Ikke klassifiser cached metadatahash som nåværende full innholdsverifisering.
- Ikke bruk full relativ brukersti som fysisk staging-, versjons- eller karantenesti.
- Ikke la et utløpt UTC-felt alene autorisere overtakelse av en claim eller lease etter restart.
- Ikke tillat regulære uttrykk uten begrenset motor, cancellering, mønstergrense og evalueringsbudsjett.

### 0.3 Første Codex-oppgave

Codex skal først utføre **Milepæl 0A.0 — miljø- og sikkerhetspreflight** og deretter stoppe. 0A gjennomføres som separate arbeidspakker; Codex starter aldri neste arbeidspakke uten at prosjekteieren åpner den. Ikke implementer produktfunksjoner, endelig schema eller muterende backupflyt i Milepæl 0A.

### 0.4 Bindende ytelsesdoktrine

Disse prinsippene gjelder gjennom hele implementasjonen:

1. **Skann én gang, gjenbruk resultatet.** Kildesnapshot deles mellom alle mål i samme kjøring. Ingen mål får starte en ny kildeskann eller ny kildehash uten dokumentert behov.
2. **Billigste sikre beslutning først.** Sti, type, størrelse, stabil fil-ID og tidspunkt brukes før quick hash; full BLAKE3 brukes bare når sikkerheten eller brukerens eksplisitte valg krever det.
3. **Strøm data gjennom systemet.** Scanner, databasewriter, planlegger, batcher og GUI bruker avgrensede batcher. Millioner av poster skal aldri bli én Python-liste.
4. **Én serialisert skrivetjeneste per valgt SQLite-database, eid av Engine Host.** Kandidatdesignet skiller katalog/read model fra recoveryjournal, men 0A.4 skal måle alternativene og 0A.6 skal legge anbefalingen frem for eiergodkjenning av ADR-003. Uansett valg serialiseres writes per database; bulk-/rekonstruerbare transaksjoner og kritiske recoverytransaksjoner får eksplisitt durability. GUI, launcher og trigger client åpner ikke databasefilene; queries går gjennom korte Engine Host-read models.
5. **Minimer prosessoppstart.** Robocopy-batcher bygges store nok til å amortisere oppstart, men små nok til presis pause, nye forsøk og fremdrift.
6. **Staging på målvolumet.** Staging skal ligge på samme volum/share som sluttmålet slik at commit normalt er en billig rename, ikke en ny full filkopi.
7. **Ressursbevisst samtidighet.** Rotasjonsdisk, SSD, USB og SMB får ulike profiler. Maksimal parallellitet er ikke synonymt med maksimal gjennomstrømning.
8. **Ingen dobbel lesing uten grunn.** En fil som allerede er fullhashet skal ikke hashes på nytt for mål 2 og 3. Balansert verifisering skal ikke lese alle terabyte en ekstra gang.
9. **GUI er en observatør, ikke en arbeider.** Ingen fil-I/O, stor SQL-spørring, JSON-parsing eller bildegenerering utføres i GUI-tråden.
10. **Mål før optimalisering.** Hver ytelsesendring skal ha en reproducerbar benchmark og må ikke endre planens semantikk eller sikkerhetsinvarianter.

Prioritetsrekkefølgen er: **dataintegritet → korrekthet → vedvarende gjennomstrømning → responsivitet → visuell finesse**. Visuell finesse skal fortsatt være høy, men aldri på bekostning av en rask og stabil arbeidsflyt.

### 0.5 Dokumentpakke, presedens og maskinlesbare kontrakter

Denne masterfilen er et konsolidert lesedokument. Den operative overleveringspakken er allerede delt slik at Codex bare laster relevant kontekst for gjeldende arbeidspakke:

```text
.editorconfig
.gitattributes
.gitignore
AGENTS.md
BUNDLE_MANIFEST.sha256
CHANGELOG.md
MASTER_SPEC.md
README.md
requirements-handoff.txt
docs/
├── README.md
├── GOVERNANCE.md
├── CODEX_START_PROMPT.md
├── HANDOFF_CHECKLIST.md
├── PRODUCT_REQUIREMENTS.md
├── REQUIREMENTS_INDEX.md
├── ARCHITECTURE.md
├── ENDPOINT_OWNERSHIP.md
├── STORAGE_AND_SCHEMA.md
├── RECOVERY_PROTOCOL.md
├── SYNC_SEMANTICS.md
├── ROBOCOPY_ADAPTER.md
├── GUI_AND_UX.md
├── PERFORMANCE.md
├── OPERATIONS_AND_AUTOMATION.md
├── TEST_PLAN.md
├── MILESTONES.md
├── REPOSITORY_AND_CODE_QUALITY.md
├── REFERENCES.md
├── LATER_IMPROVEMENTS.md
├── RELEASE_SCOPE.md
├── ARCHITECTURE_SPIKE_REPORT.md
├── DECISION_REGISTER.md
├── IMPLEMENTATION_STATUS.md
├── REQUIREMENTS_TRACEABILITY.md
├── USABILITY_CHECKLIST.md
├── BENCHMARKS.md
├── assets/
├── spikes/
└── adr/
schema/
├── README.md
├── contracts-manifest.yaml
├── catalog.sql
├── recovery.sql
├── ipc-command.schema.json
├── ipc-event.schema.json
├── endpoint-marker.schema.json
├── intent-segment.schema.json
├── reason-codes.yaml
└── state-machines.yaml
tools/
├── build_adr_docs.py
├── build_master.py
└── validate_handoff.py
```

Bindende presedens ved konflikt:

1. kanoniske produkt- og sikkerhetskrav i `docs/REQUIREMENTS_INDEX.md`;
2. ADR-er med eierbeslutning `OWNER_ACCEPTED`;
3. versjonerte kontrakter med status `frozen` i `schema/contracts-manifest.yaml`;
4. databaseconstraints og genererte typer som er produsert fra fryste kontrakter;
5. kjørbare konformitets-, sikkerhets- og arkitekturtester;
6. gjeldende arbeidspakkes eksplisitte leveranse- og kvalitetsport;
7. forklarende tekst, eksempler og wireframes.

En kontrakt med status `draft`, `blocked` eller `candidate` i `schema/contracts-manifest.yaml` har ikke høyere autoritet enn eiergodkjente ADR-er og kanoniske krav. Codex kan sette ADR-feltet `evidence_status` til `EVIDENCE_COMPLETE`, `RECOMMENDED` eller `BLOCKED`, men bare prosjekteieren kan sette `owner_decision` til `OWNER_ACCEPTED`, `REJECTED`, `DEFERRED_WITH_SCOPE_REDUCTION` eller `SUPERSEDED`. Bare kontrakter med eksplisitt `frozen`-status, eiergodkjent ADR, skjema-/protokollversjon og tilhørende valideringstest kan brukes som maskinlesbar sannhetskilde. Plassholder-SQL skal aldri tolkes som implementeringsklart databaseskjema.

`AGENTS.md` skal være kort og inneholde dokumentpresedens, absolutte sikkerhetsinvarianter, aktuell milepæl, hvilke dokumenter som må leses, testkommandoer og stoppregler. Fagfilene under `docs/` er kanoniske. `MASTER_SPEC.md` genereres deterministisk med `python tools/build_master.py` og skal ikke redigeres direkte. CI skal feile ved drift mellom fagfiler, generert master, fryste SQL/JSON/YAML-kontrakter og kodegenererte typer. Masterfilen skal ikke limes inn som full prompt ved hver Codex-oppgave.

---
