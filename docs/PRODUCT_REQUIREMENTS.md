# Produktkrav


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Produktmål, avgrensninger, prioriterte arbeidsflyter og låste standardvalg.


## 1. Produktmål

MediaSync Home skal være et oversiktlig Windows-program for en privat bruker som ønsker å kopiere og synkronisere store bilde- og videosamlinger til flere sikkerhetskopier. Programmet skal kombinere en kjent mappepar-orientert arbeidsflyt med en moderne, visuelt ryddig GUI.

Produktet skal oppleves raskt på to måter: Det skal bruke maskinvaren effektivt under langvarige terabyte-kjøringer, og det skal redusere tiden brukeren bruker på å forstå status og starte riktig handling. En kjent jobb skal kunne åpnes og startes uten en ny veiviser, mens analyse, sikkerhetskontroller og målstatus fortsatt er synlige og etterprøvbare.

Brukeren skal kunne:

- opprette en jobb med én kilde og ett til tre mål;
- velge mellom ikke-destruktiv oppdatering, speiling og toveissynkronisering;
- analysere forskjeller før kjøring;
- se nøyaktig hva som skal kopieres, erstattes, beholdes, settes i karantene eller behandles som konflikt;
- oppdage innholdsidentiske filer, også når navn eller plassering er forskjellige;
- starte, pause, fortsette og stoppe en kjøring;
- se total fremdrift, aktiv fil, hastighet, datamengde, estimert gjenstående arbeid og feil;
- automatisere jobber etter tid, pålogging, oppstart, tilkoblet disk eller filendringer;
- bruke programmet mot lokale disker, eksterne disker, tilordnede stasjoner og SMB/UNC-stier;
- hente frem historikk og revisjonslogg for hver kjøring;
- gjenbruke tidligere metadata og hashresultater uten å stole på utdatert informasjon;
- oppnå overføringshastighet nær direkte Robocopy når sikker staging og verifisering er tatt med;
- fortsette øvrige backupmål dersom ett mål er frakoblet eller tregt;
- se meningsfull respons umiddelbart, også mens millionstore datasett analyseres i bakgrunnen.

### 1.1 Prioritert hjemmescenario

Den viktigste arbeidsflyten er:

```text
D:\Bilder og videoer
    ├──> E:\FotoBackup
    ├──> F:\FotoBackup
    └──> \\NAS\Backup\FotoBackup
```

Kilden skal skannes én gang. Det skal deretter bygges en separat, sikker operasjonsplan for hvert mål. Målene skal kunne være frakoblet uten at det oppstår slettinger eller feilslutninger.

### 1.2 Ikke-mål for første komplette versjon

Følgende skal ikke implementeres i første komplette versjon:

- skylagrings-API-er;
- FTP, SFTP eller WebDAV;
- blokk- eller deltasynkronisering inne i store filer;
- medietranscoding;
- redigering av EXIF eller videometadata;
- automatisk fjerning av duplikater;
- hardlink-, reflink- eller dedupliseringsmotor;
- Volume Shadow Copy Service for åpne filer;
- Windows-tjeneste;
- mobilapp eller nettgrensesnitt;
- offentlig kommandolinjegrensesnitt;
- automatisk programoppdatering.


### 1.3 Leveransestige og scope-port

Arkitektur- og sikkerhetskrav kan bevises tidlig, men produktet skal leveres vertikalt. En senere funksjon skal ikke forsinke en trygg, brukbar hjemmebackup når den kan avgrenses uten å svekke sikkerheten.

| Leveranse | Bindende brukerflyt | Bevisst utsatt |
|---|---|---|
| **Alpha 0.1** | Én Windows-installasjon, én kilde, ett mål, manuell analyse, `COPY_NEW_ONLY_NO_REPLACE`, Robocopy til staging, kontrollert innsetting, verifisering og resultatside. | Erstatning, sletting, automatikk, tre mål, takeover og toveis. |
| **Alpha 0.2** | Endrede filer, versjonslager, full commit-/recoveryprotokoll og historikk. | Speiling, toveis og avansert retention. |
| **Beta** | Opptil tre mål, USB/NAS, ressursstyring, automatisering og informativ duplikatvisning. | Sikker takeover mellom installasjoner og toveis. |
| **1.0** | Speiling med karantene, restore og dokumentert retention. | Reverse/toveis dersom egne porter ikke er bestått. |
| **Senere** | Reverse, toveis, kontrollert takeover og valgfrie avanserte optimaliseringer. | Ikke del av første brukbare backup. |

Alle absolutte sikkerhetsinvarianter gjelder også Alpha 0.1. Scope-reduksjon kan fjerne en funksjon, men kan aldri svekke staging, no-overwrite, endepunktidentitet, recoverybevis eller sannferdig GUI-status.

---

## 2. Låste produktvalg og standardverdier

Dette kapittelet besvarer krav som ikke ble eksplisitt angitt av brukeren.

| Område | Beslutning |
|---|---|
| Lisens-/brukskontekst | Privat hjemmebruk. Ingen kopiering av proprietær kode, navn eller grafiske ressurser. |
| Operativsystem | Windows 10 og Windows 11, x64. ARM64 kan vurderes senere. |
| GUI | PySide6 med Qt Widgets, native vindusramme, sentralt tokenisert designsystem, original dataflyt-identitet og system-/lys-/mørk modus. |
| Brukermodell | **Backup** er standardproduktet: én kilde til ett–tre mål. `pair_sync`, reverse og toveis opprettes fra en separat avansert flyt og presenteres ikke som likeverdige førstegangsvalg. |
| Opprettingsflyt | Standard backup opprettes i høyst fire steg. Jobbnavn foreslås automatisk; modus, filtre, verifisering og automatisering har sikre standarder og krever ikke egne steg. |
| Statusspråk | GUI-et viser eksakt siste vellykkede kjøring per mål. Ordet `Oppdatert` brukes bare når en fullført og fortsatt gyldig analyse bekrefter null ventende endringer for det aktuelle målet. Filovervåking kan ugyldiggjøre statusen eller melde at endringer er oppdaget, men kan aldri alene bevise at målet er oppdatert. Programmet lover aldri generelt at data er `beskyttet`. |
| Språk | Norsk bokmål som standard, engelsk oversettelse klargjøres. |
| Offentlig CLI | Ingen. En intern, skjult kjørevariant kan brukes av Windows Oppgaveplanlegging. |
| Jobbmodell | `multi_target_backup` har én autoritativ kilde og ett til tre mål, alltid kilde → mål. `pair_sync` har nøyaktig to endepunkter og kan bruke begge retninger eller toveis. |
| Endepunkter | Lokal disk, USB-disk, mapped drive og SMB/UNC. |
| Uavhengige backupmål | Mål som kan bevises å ligge på samme fysiske lagringsenhet eller samme identifiserte nettverksdeling, vises ikke som uavhengige kopier. Ukjent fysisk uavhengighet merkes som ukjent, ikke som bekreftet. |
| Standardmodus | `Update A → B`: kopier nye og endrede filer, behold ekstra filer på målet. |
| Speiling | Ekstra målinnhold flyttes til karantene, ikke permanent sletting. |
| Toveis konflikt | Behold begge filer med konfliktmerking. Ingen stille overskriving. |
| Forhåndsanalyse | Første kjøring og endret konfigurasjon krever synlig kontroll. For en etablert backup utfører `Kjør backup` sikkerhetskontroll og analyse og fortsetter automatisk bare når planen består av nye kopier, mappeoppretting, identiske hopp og forventede filterhopp. Inneholder analysen bare identiske filer og forventede filterhopp, vises `Ingen endringer`, og ingen tom kjøring opprettes. Erstatning, karantene, konflikt, blokkering eller terskelavvik åpner kontrollvisningen før noe utføres. |
| Sletting | Karantene i 30 dager som standard. Permanent tømming er en separat handling. |
| Overskrevne filer | Gammel målversjon beholdes i 30 dager som standard. |
| Filtyper | Alle filendelser støttes som opake bytefiler innenfor kilde- og målfilsystemets dokumenterte grenser. Mediepresets er valgfrie. |
| Sidecar-filer | Vanlige sidecars som XMP, THM, AAE og SRT behandles som vanlige filer og kan grupperes visuelt med hovedfilen. |
| Filstabilitet | En fil må være uendret i minst 30 sekunder før automatisk kjøring kopierer den. Manuell kjøring kan vise en advarsel og hoppe over ustabile filer. |
| Sammenligning | Størrelse og tidspunkt først; hash ved tvil, verifisering eller duplikatdeteksjon. |
| Hash | BLAKE3 som standard, med full filstørrelse som del av identitetsbeviset. |
| Verifisering | Balansert modus som standard: størrelse/tidspunkt og hash der kildehash finnes eller risikoen er høy. Full hash kan aktiveres per jobb. |
| Metadata | Bevar primær datastrøm, attributter og tidsstempler. Ikke kopier ACL/eier/auditing som standard. Standardpolicyen bevarer og verifiserer named streams når begge endepunkter støtter dem; ellers kopieres primær datastrøm med samlet advarsel og revisjonsspor. Full ekvivalens for hele filobjektet loves bare når alle relevante streams er verifisert. |
| Tomme mapper | Ja. |
| Reparse points | Ekskluderes som standard og vises som advarsel. |
| Case-sensitivitet | Opprinnelig navn bevares. En dedikert Windows-navnesammenligner og endepunktets case-modus styrer nøkkelen; generisk Python `casefold()` er ikke autoritativ. Kolliderende poster lagres begge og blokkeres som konflikt. |
| Flytting/omdøping | Oppdages med stabil fil-ID lokalt eller full hash når mulig. Ellers behandles det som ny fil pluss slettet fil. |
| Utilgjengelig endepunkt | Jobben settes i ventemodus. Ingen destruktive handlinger. |
| Flyttbar disk | Identifiseres med volum-GUID, serienummer og en egen endepunktmarkør, ikke bare stasjonsbokstav. |
| NAS-legitimasjon | Bruk eksisterende Windows-økt og Windows Credential Manager. Programmet lagrer ikke passord i klartekst. |
| Logger | Detaljert operasjonslogg og sammendrag; beholdes i 90 dager som standard. |
| Eksport | CSV og JSON for kjøringsresultater. |
| Feil på enkeltfil | Tre forsøk; hopp deretter over filen, fortsett resten, og marker kjøringen med advarsel eller feil. |
| Nettverksbrudd | Sett kjøringen på vent eller planlegg et nytt forsøk; ikke fullfør eller slett. |
| Samtidighet | Ressursbevisst og selvbalanserende; begrens parallellitet på samme fysiske disk eller NAS. |
| Indeks | SQLite med vedvarende filindeks, hashcache, baseline og kjøringshistorikk. |
| Automatisering | Manuell, tidsplan, pålogging, lokal systemoppstart, disktilkobling og filendring. NAS/UNC-jobber følger en eksplisitt Task Scheduler-sikkerhetskontekst og kjører normalt bare når brukeren er logget inn. |
| Tjeneste | Ingen Windows-tjeneste. Oppgaveplanlegging og en agent i systemstatusfeltet brukes. |
| Pakking | Signerbar Windows-installasjon samt valgfri installasjonsfri mappepakke. Mutable tilstand og SQLite-databaser ligger alltid i lokal, ACL-beskyttet per-bruker AppData; «mappepakke» betyr ikke at autoritativ state flyttes til USB/NAS. |
| Ytelsesprofil | `Auto` som standard, med `Skånsom` og `Maks gjennomstrømning` som eksplisitte alternativer. |
| Skannestrategi | Én strømmet skann per endepunkt; kildekatalogen deles mellom alle mål. Ingen hashing i skannerens ordinære varme kodebane. |
| Hashstrategi | Cache og behovsstyrt hashing. Vanlig backup skal ikke fullhashe alle uendrede filer. |
| Database | Lokal, ACL-beskyttet SQLite-state eid av Engine Host. Kandidatdesignet bruker `catalog.sqlite` og `recovery.sqlite`; 0A.4 må måle én- og to-databasealternativene, og bare prosjekteieren kan godkjenne ADR-003 i 0A.6. Én serialisert skrivetjeneste per valgt database, keyset-paginering, preparerte spørringer og adaptive bulkbatcher. |
| Robocopy | Få, presise og adaptive batcher. `/Z`, `/J`, `/MT` og loggnivå velges per endepunkt og arbeidslast. |
| GUI-data | Lazy loading, virtuelle tabeller, delegater fremfor rad-widgets og coalesced fremdriftssignaler. |
| Effektiv hovedflyt | En eksisterende trygg backupjobb skal normalt kunne startes fra dashboardet med én bevisst handling. Analyse skjer alltid, men vises som kontrollstopp bare når noe krever oppmerksomhet. |
| Internett | Ikke nødvendig for bruk. |
| Utviklingsmetode | Små milepæler, automatiserte tester og sikkerhetsport etter hver fase. |

---
