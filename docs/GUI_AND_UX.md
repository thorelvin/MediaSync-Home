# GUI og brukeropplevelse


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


Designsystem, arbeidsflyter, tilstander, tilgjengelighet, ytelse og brukervennlighet.


## 8. Produktdesign, GUI og brukeropplevelse

GUI-en er en del av produktets sikkerhetsmodell. Den skal ikke bare se moderne ut; den skal gjøre det vanskelig å misforstå retning, mål, konsekvenser og kjørestatus. Alle visuelle valg i dette kapittelet er implementeringskrav med mindre de uttrykkelig er merket som valgfrie.

### 8.1 Designmål

MediaSync Home skal oppleves som et rolig, presist og pålitelig kontrollsenter for store filsamlinger. Uttrykket skal være moderne Windows, men ha en selvstendig identitet og ikke være en visuell kopi av Allway Sync eller andre kommersielle produkter.

Følgende prinsipper gjelder i prioritert rekkefølge:

1. **Neste handling skal være åpenbar.** Hver side og hvert jobbkort skal ha én anbefalt handling som følger av faktisk tilstand.
2. **Retning skal forstås umiddelbart.** Kilde, mål og backup-/synkroniseringsmodus skal kunne leses uten å åpne innstillinger.
3. **Status skal være sannferdig per mål.** Et vellykket gammelt resultat må ikke presenteres som om dagens kilde er verifisert oppdatert.
4. **Risiko skal vises før handling.** Erstatning, konflikt, karantene, utelatte mål og blokkering skal ha tydelig språk, ikon og konsekvensoppsummering.
5. **Vanlig hjemmebackup skal være enkel.** Hovedflyten er: velg jobb og trykk **Kjør backup**. Programmet analyserer og stopper bare når noe krever kontroll.
6. **Avansert funksjonalitet skal være tilgjengelig, men ikke dominere.** Bruk progressiv avdekking og en separat avansert opprettingsflyt for `pair_sync` og toveis.
7. **Programmet skal være rolig under langvarig arbeid.** Unngå blinkende elementer, raske animasjoner og støyende logger i hovedflaten.
8. **Status skal kunne forstås uten farge.** Tekst, ikon og form skal alltid støtte fargekodingen.
9. **Store datamengder skal føles håndterbare.** Vis summer, avvik og anbefalt handling før enkeltrader.
10. **Feil og delvise resultater skal være handlingsbare.** Vis hva som ble påvirket, hva som fortsatt er trygt, og hva brukeren bør gjøre videre.

#### 8.1.1 Opplevelsesmål

En ny bruker skal kunne:

- opprette en standard backup med én kilde og opptil tre mål i høyst fire skjermer uten dokumentasjon;
- fullføre opprettingen uten å velge hashmetode, Robocopy-parametere, Task Scheduler-logontype eller andre tekniske innstillinger;
- forstå forskjellen mellom **Oppdater backup**, **Speil backup** og **Toveis synkronisering** før et avansert valg lagres;
- forstå fra dashboardet hvilket mål som er ferskt, gammelt, frakoblet eller blokkert;
- starte en etablert trygg backup med én bevisst handling;
- forstå hvorfor hurtigflyten stoppet når planen inneholder erstatning, konflikt, karantene eller andre avvik;
- stoppe en aktiv kjøring uten å lure på om en fil blir stående halvkopiert;
- prøve bare et mislykket mål eller mislykkede elementer på nytt;
- finne igjen en tidligere versjon eller karantenefil fra historikken.

#### 8.1.2 Primær mental modell

Normalbrukeren skal møte **backup**, ikke en generell synkroniseringsmotor.

```text
Dette vil jeg beskytte  →  Her vil jeg ha kopier  →  Kjør backup
```

Den tekniske jobbtypen `multi_target_backup` presenteres som **Backupjobb**. Den separate jobbtypen `pair_sync` presenteres som **Avansert synkronisering** og opprettes fra en sekundær tekstlenke eller overflow-meny. Toveis og reverse skal aldri vises som like fremtredende valg som standard backup på tomt dashboard.

Brukerrettet terminologi:

| Internt begrep | Normal GUI-tekst |
|---|---|
| `multi_target_backup` | `Backupjobb` |
| `UPDATE_FORWARD` | `Oppdater backup` |
| `MIRROR_FORWARD` | `Speil backup` |
| `pair_sync` | `Avansert synkronisering` |
| analyse/plan | `Kontroll av endringer` / `Endringer som skal utføres` |
| run | `Backupkjøring` eller `Synkroniseringskjøring`, avhengig av jobbtype |
| endpoint | `Kilde`, `backupmål`, `disk`, `mappe` eller `NAS` |
| recovery | `Gjenoppretting` |

Interne ord som `snapshot`, `baseline`, `fingerprint`, `batch`, `commit`, `plan-checksum` og `Robocopy` finnes bare under **Tekniske detaljer** og diagnostikk.

#### 8.1.3 Progressiv avdekking

GUI-et har tre informasjonsnivåer, men ingen global «enkel/ekspert»-bryter som kan skjule sikkerhetskritisk informasjon:

1. **Primærnivå:** status, kilde, mål, neste handling, fremdrift og resultat.
2. **Flere valg:** filtre, tidsplan, versjonsbevaring, verifisering og ytelsesprofil per jobb.
3. **Tekniske detaljer:** filsystemegenskaper, årsakskoder, logger, Robocopy, database-/run-ID-er og scheduler-kontekst.

En bruker skal kunne fullføre standardflyten uten nivå 2 eller 3. Når et avansert valg påvirker sikkerhet eller datatapspotensial, skal konsekvensen likevel vises på primærnivå før start.

### 8.2 Visuell identitet

Den ikke-pikselbindende designreferansen [`assets/gui-concept-v1.png`](assets/gui-concept-v1.png) viser ønsket stemning, hierarki og tetthet.

#### 8.2.1 Produktkarakter

Uttrykket skal være:

- mørkt eller lyst etter brukerens systemvalg;
- teknisk presist, men ikke «serververktøy»-preget;
- romslig, med tydelig hierarki og moderate kontraster;
- visuelt distinkt gjennom en grafisk **dataflyt-linje** som går igjen i logo, endepunktkart og fremdrift;
- fri for kameraklisjeer, roterende piler og kopierte produktikoner.

#### 8.2.2 Merkeelement

Lag et originalt merkeelement bestående av tre avrundede datalinjer som går fra én kilde til tre mål og danner en diskret skjold-/arkivform. Merket skal fungere i 16, 24, 32, 48 og 256 piksler.

Krav:

- egen SVG-kilde;
- monokrom variant for systemstatusfelt og små størrelser;
- lys og mørk variant;
- ingen tekst inne i selve ikonet;
- ingen fotografiske ressurser;
- ingen direkte likhet med Allway Sync-logo eller Windows Backup-ikonet.

Foreslått kort undertittel i første oppstart og om-dialog:

> **Dine filer. Flere trygge kopier.**

#### 8.2.3 Illustrasjoner

Illustrasjoner brukes bare i onboarding og tomtilstander. De skal bygges av de samme endepunktkortene og dataflyt-linjene som resten av GUI-en. Ikke bruk generiske stockbilder.

### 8.3 Bindende designsystem

Alle visuelle verdier skal ligge i én sentral tokenmodell. Ingen side eller widget skal ha tilfeldige hardkodede farger, marger, radiusverdier eller fontstørrelser.

Foreslått struktur:

```text
presentation/theme/
├── tokens.py
├── palettes.py
├── typography.py
├── metrics.py
├── icon_registry.py
├── qss_builder.py
└── theme_manager.py
```

#### 8.3.1 Avstand og rutenett

Bruk et grunnrutenett på 4 logiske piksler og følgende navngitte tokens:

| Token | Verdi | Typisk bruk |
|---|---:|---|
| `space_1` | 4 px | Ikonjustering, tette mellomrom |
| `space_2` | 8 px | Mellom ikon og tekst, kompakte kontroller |
| `space_3` | 12 px | Feltinteriør, små kort |
| `space_4` | 16 px | Standard gap og kortpadding |
| `space_5` | 20 px | Kontrollgrupper |
| `space_6` | 24 px | Seksjonspadding |
| `space_8` | 32 px | Mellom hovedseksjoner |
| `space_10` | 40 px | Store topp-/bunnavstander |
| `space_12` | 48 px | Onboarding og hero-flater |

Regler:

- hovedinnhold har 24 px padding ved normal bredde og 16 px i kompakt modus;
- kort har normalt 16 eller 20 px innvendig padding;
- tabellrader er 40 px i kompakt modus og 48 px i komfortabel modus;
- ingen vilkårlige avstander som 13, 17 eller 23 px uten dokumentert grunn;
- alle mål er logiske piksler og skal fungere med Qt DPI-skalering.

#### 8.3.2 Radius, kant og dybde

| Token | Verdi | Bruk |
|---|---:|---|
| `radius_sm` | 6 px | Chips, små felt, statusmerker |
| `radius_md` | 10 px | Felt, knapper og mindre kort |
| `radius_lg` | 14 px | Endepunktkort og paneler |
| `radius_xl` | 20 px | Onboarding/hero og store modaler |
| `border_hairline` | 1 px | Standard skillelinje |
| `border_focus` | 2 px | Tastaturfokus |

Dybde skal være subtil:

- standardkort bruker kantlinje, ikke skygge;
- hevede paneler og menyer kan bruke én myk skygge;
- dialoger og flytende paneler kan bruke sterkere skygge;
- ingen flere lag med skygger eller «glow» rundt vanlige kontroller;
- hover skal primært uttrykkes med overflateendring, ikke bevegelse.

#### 8.3.3 Typografi

Bruk Windows-systemfonten **Segoe UI Variable** når den finnes, deretter `Segoe UI`, og til slutt Qt sin generiske sans-serif. Ikke pakk eller distribuer fontfiler.

| Stil | Størrelse | Vekt | Bruk |
|---|---:|---:|---|
| `display` | 32 px | 650 | Onboarding og store tomtilstander |
| `title_1` | 26 px | 650 | Sidetittel |
| `title_2` | 20 px | 600 | Seksjonstittel og jobbtittel |
| `title_3` | 16 px | 600 | Korttittel og dialogtittel |
| `body` | 14 px | 400 | Standardtekst |
| `body_strong` | 14 px | 600 | Viktig verdi og kontrolltekst |
| `small` | 12 px | 400 | Sekundær metadata |
| `small_strong` | 12 px | 600 | Status og tabellhode |
| `mono` | 12–13 px | 400 | Stier, hash og tekniske verdier |

Regler:

- filstier bruker monospaced systemfont der det forbedrer lesbarheten;
- store bokstaver brukes bare i korte seksjonsetiketter, aldri i lange knapper;
- brødtekst skal ha minst 1,35 linjehøyde;
- avkortede stier skal vise full verdi i tooltip og kunne kopieres;
- tall som endres raskt, som hastighet og byte, bruker tabulære sifre når tilgjengelig.

#### 8.3.4 Lys palett

| Semantisk token | Verdi | Bruk |
|---|---|---|
| `canvas` | `#F4F6F9` | Appbakgrunn |
| `surface` | `#FFFFFF` | Kort og panel |
| `surface_subtle` | `#F8FAFC` | Sekundær flate |
| `surface_hover` | `#EEF3F8` | Hover |
| `surface_selected` | `#E9EFFF` | Valgt rad/kort |
| `border` | `#D8E0E8` | Standard kant |
| `border_strong` | `#AEB9C5` | Tydelig skille |
| `text_primary` | `#17212B` | Primær tekst |
| `text_secondary` | `#506070` | Sekundær tekst |
| `text_muted` | `#5B6A79` | Liten metadata på lyse og valgte flater |
| `accent` | `#315EFB` | Primær knapp/fylt kontroll |
| `accent_text` | `#2449C7` | Lenke eller aksenttekst på lyse flater |
| `accent_hover` | `#274EDB` | Hover på primær handling |
| `accent_pressed` | `#203FAF` | Presset primær handling |
| `text_on_accent` | `#FFFFFF` | Tekst/ikon på primær handling |
| `focus_ring` | `#315EFB` | Tastaturfokus |
| `success` | `#147A48` | Fullført/tilgjengelig |
| `warning` | `#9A5A00` | Risiko/venter |
| `danger` | `#C7352D` | Feil/destruktiv handling |
| `info` | `#1769AA` | Informasjon |

#### 8.3.5 Mørk palett

| Semantisk token | Verdi | Bruk |
|---|---|---|
| `canvas` | `#0E141B` | Appbakgrunn |
| `surface` | `#151D26` | Kort og panel |
| `surface_subtle` | `#1A2430` | Sekundær flate |
| `surface_hover` | `#223040` | Hover |
| `surface_selected` | `#24345C` | Valgt rad/kort |
| `border` | `#2B3948` | Standard kant |
| `border_strong` | `#4A5A6C` | Tydelig skille |
| `text_primary` | `#F3F6F9` | Primær tekst |
| `text_secondary` | `#BBC6D1` | Sekundær tekst |
| `text_muted` | `#A7B2BE` | Liten metadata på mørke og valgte flater |
| `accent` | `#7DA2FF` | Primær knapp/fylt kontroll |
| `accent_text` | `#AFC4FF` | Lenke eller aksenttekst på mørke flater |
| `accent_hover` | `#91B1FF` | Hover på primær handling |
| `accent_pressed` | `#668DEB` | Presset primær handling |
| `text_on_accent` | `#0B1220` | Tekst/ikon på primær handling |
| `focus_ring` | `#AFC4FF` | Tastaturfokus |
| `success` | `#54C98A` | Fullført/tilgjengelig |
| `warning` | `#F0B24B` | Risiko/venter |
| `danger` | `#FF7B72` | Feil/destruktiv handling |
| `info` | `#69B7F4` | Informasjon |

Palettene er bindende startverdier. Codex skal ha automatiserte kontrasttester for alle tillatte foreground/background-par, ikke bare hver tekstfarge mot hovedbakgrunnen. Minstekravet er 4,5:1 for normal tekst og 3:1 for store komponentgrenser og fokus der relevant. `accent` brukes som fylt kontrollfarge; løpende aksenttekst bruker `accent_text`.

Tillatte tekstpar skal minst testes mot `surface`, `surface_subtle`, `surface_hover` og `surface_selected` i begge temaer. En semantisk kombinasjon som ikke består, skal ikke kunne velges av komponentbiblioteket.

#### 8.3.6 Merkegradient

En diskret gradient fra `#315EFB` til `#18A999` kan brukes i:

- produktmerket;
- onboarding-illustrasjonen;
- et smalt aktivt dataflytsegment under kjøring.

Gradienten skal ikke brukes som bakgrunn bak tabeller, skjemaer eller lange tekstområder. Funksjonelle knapper bruker én solid semantisk farge.

#### 8.3.7 Semantiske statuser

| Status | Ikonform | Fargetone | Standardtekst |
|---|---|---|---|
| Klar/tilgjengelig | Fylt sirkel med hake | Grønn | `Klar` |
| Analyserer/kjører | Sirkel med fremdrift | Aksent | `Analyserer` / `Kopierer` |
| Venter/frakoblet | Klokke eller frakoblet plugg | Gul | `Venter på mål` |
| Advarsel | Trekant med utropstegn | Gul | `Trenger kontroll` |
| Feil | Sirkel med kryss | Rød | `Mislyktes` |
| Pauset | To vertikale streker | Nøytral/blå | `Pauset` |
| Identisk | Likhetstegn | Nøytral/grønn | `Identisk` |
| Konflikt | Delt filsymbol | Rød/gul | `Konflikt` |
| Karantene | Arkivboks/skjold | Gul | `Flyttes til karantene` |

Ingen status skal uttrykkes med farge alene.

### 8.4 Appramme og vindusstruktur

Bruk operativsystemets native vindusramme og standard Windows-funksjoner for flytting, maksimering, snapping og tilgjengelighet. Ikke bygg et rammeløst «custom chrome»-vindu i første versjon.

På Windows 11 kan en støttet system-backdrop brukes diskret i navigasjons-/tittelområdet dersom den kan implementeres uten uoffisielle API-er og uten å påvirke Windows 10. Solid `canvas` er obligatorisk fallback. Datatabeller og arbeidsflater skal alltid ha ugjennomsiktig bakgrunn for lesbarhet.

#### 8.4.1 Størrelser

- standard vindusstørrelse: 1440 × 900 logiske piksler;
- minimum: 1024 × 700;
- optimal arbeidsbredde: 1280–1720;
- dialoger skal aldri være høyere enn tilgjengelig arbeidsområde;
- sist brukte størrelse, posisjon, side og splitterposisjon lagres;
- ved skjermendring skal vinduet flyttes tilbake til et synlig område.

#### 8.4.2 Hovedsoner

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Native title bar: MediaSync Home                                             │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ Navigasjon    │ Handlingslinje: sidetittel · kontekst · primærhandling      │
│ 240 / 72 px   ├──────────────────────────────────────────────────────────────┤
│               │                                                              │
│ Oversikt      │ Arbeidsflate                                                 │
│ Jobber        │                                                              │
│ Historikk     │                                      ┌────────────────────┐  │
│               │                                      │ Valgfritt detaljpanel │  │
│ ───────────   │                                      │ 320–400 px        │  │
│ Innstillinger │                                      └────────────────────┘  │
│               │                                                              │
│               ├──────────────────────────────────────────────────────────────┤
│               │ Aktivitetslinje: samlet status · hastighet · varsler         │
└───────────────┴──────────────────────────────────────────────────────────────┘
```

Duplikater og gjenoppretting er kontekstuelle arbeidsflater, ikke permanente toppnivåer. Duplikater åpnes fra en jobb eller analyse; gjenoppretting åpnes fra jobbens historikk eller en kjøringsdetalj.

#### 8.4.3 Navigasjon

Venstre navigasjon skal ha denne rekkefølgen:

1. **Oversikt**
2. **Jobber**
3. **Historikk**
4. **Innstillinger** nederst

**Hjelp og om** ligger i applikasjonsmenyen og under Innstillinger. Kontekstuelle ruter for duplikater, kjøringsdetalj og gjenoppretting skal fortsatt ha breadcrumb og korrekt tilbakeflyt, men skal ikke gjøre navigasjonslisten lengre.

Krav:

- valgt side har både markør, bakgrunn og sterkere tekst;
- aktiv jobb kan vises som en liten statusindikator ved **Jobber**;
- navigasjonen kan komprimeres fra 240 til 72 px;
- kompakt modus beholder tooltips og tilgjengelige navn;
- ingen hamburgermeny ved normal skrivebordsbredde;
- sidebytte skal ikke åpne nye toppnivåvinduer;
- kontekstuelle sider returnerer til opprinnelig jobb eller kjøring uten å miste filter-/scrolltilstand.

#### 8.4.4 Handlingslinje

Handlingslinjen øverst i arbeidsflaten inneholder:

- sidetittel og eventuell breadcrumb;
- kontekstavhengig sekundær handling;
- én tydelig primærhandling helt til høyre;
- overflow-meny for sjeldne handlinger;
- global aktivitet/varsling;
- aldri mer enn fire synlige handlingsknapper samtidig.

Eksempel på en etablert backupjobb:

```text
Fotoarkiv / Oversikt                       [Rediger backup] [Kjør backup]
```

Når noe må kontrolleres:

```text
Fotoarkiv / Endringer                   [Forkast kontroll] [Start backup]
```

En avansert `pair_sync` bruker tilsvarende teksten `Start synkronisering`. Bruk presise, jobbtypespesifikke verb. Unngå generiske etiketter som `Kjør`, `OK` og `Utfør` når en mer konkret tekst finnes.

#### 8.4.5 Aktivitetslinje

En 36–40 px høy aktivitetslinje nederst viser global status uten å dominere:

- aktiv jobb;
- kort tilstandstekst;
- fase og prosent bare når totalen er kjent og prosent er meningsfull; ellers behandlede filer, forløpt tid eller ubestemt fremdrift;
- glidende gjennomsnittshastighet;
- antall advarsler/feil;
- knapp for å åpne aktiv kjøring.

Når ingen jobb kjører, vises siste bakgrunnshendelse eller teksten `Alle jobber er inaktive`.

### 8.5 Informasjonsarkitektur

#### 8.5.1 Primære objekter

GUI-en organiseres rundt følgende objekter:

- **Jobb** — lagret oppsett;
- **Endepunkt** — kilde eller mål;
- **Analyse** — øyeblikksbilde og uforanderlig plan;
- **Kjøring** — utførelse av en plan;
- **Filoperasjon** — én planlagt eller utført handling;
- **Duplikatgruppe** — innholdsidentiske filer;
- **Gjenopprettingselement** — karantene eller tidligere versjon.

Brukeren skal aldri måtte forstå databaseobjekter, batch-ID-er eller Robocopy-returkoder for å bruke programmet.

#### 8.5.2 Jobbnavigasjon

En standard backupjobb har fire faner:

1. **Oversikt** — målstatus, siste resultater og anbefalt neste handling.
2. **Endringer** — aktiv analyse, kontroll av planlagte endringer og aktiv kjøring.
3. **Automatikk** — tidsplan, disktilkobling og filendringstriggere.
4. **Historikk** — tidligere kjøringer, versjoner, karantene og gjenoppretting.

Jobboppsettet redigeres fra handlingen **Rediger backup** som åpner den samme kompakte opprettingsflaten med eksisterende verdier. Filvalg og filtre vises der under **Hva skal tas med**; de skal ikke kreve en permanent teknisk fane kalt `Regler`.

Duplikatanalyse åpnes fra **Endringer** eller overflow-menyen som en kontekstuell side. Karantene, versjoner og gjenoppretting åpnes fra **Historikk**. En aktiv kjøring vises i **Endringer** og kan åpnes direkte fra aktivitetslinjen.

#### 8.5.3 Bevaring av kontekst

- valgt jobb beholdes ved sidebytte;
- tabellfiltre, sortering, kolonnebredder og scrollposisjon beholdes per jobb;
- tilbakeknapp eller breadcrumb returnerer til tidligere side uten å miste kontekst;
- en endret jobb med ulagrede innstillinger skal ikke forlates uten tydelig valg om å lagre eller forkaste;
- utkast for en ny jobb lagres automatisk lokalt etter hvert gyldig steg og kan gjenopptas; redigering av en etablert jobb følger den eksplisitte lagringsmodellen i §8.8.6;
- når en handling åpner en detaljside, skal tilbakeflyten returnere til samme rad, filter og mål.

#### 8.5.4 Oppmerksomhets- og aktivitetsmodell

GUI-et skal ikke presse aktivitet, risiko og ferskhet inn i én statusverdi. De er separate dimensjoner:

| Oppmerksomhetsnivå | Betydning | Eksempel på neste handling |
|---|---|---|
| **Blokkert** | Sikker kjøring er ikke mulig. Gjenoppretting, feil målidentitet eller kritisk kapabilitetsfeil finnes. | `Løs problemet` |
| **Trenger oppmerksomhet** | Brukeren må kontrollere endringer, utsatte automatiske handlinger, feil, kapasitet eller et gammelt mål. | `Se endringer` / `Frigjør plass` |
| **Venter** | Et mål eller en trigger er midlertidig utilgjengelig, men ingen skade har skjedd. | `Kontroller mål` |
| **Normal** | Ingen kjent blokkering eller advarsel krever handling akkurat nå. | `Kjør backup` eller ingen handling |

Aktivitetstilstanden er separat: `Inaktiv`, `Kontrollerer`, `Kopierer`, `Verifiserer`, `Pauset` eller `Gjenoppretter`. En jobb kan derfor være `Kopierer` og samtidig ha advarselen `USB 2 er frakoblet`, uten at den aktive kjøringen skjules.

Dashboardet fester aktive jobber øverst, deretter blokkerte og oppmerksomhetskrevende jobber. Hver advarsel skal ledsages av konkret grunn, berørt mål og anbefalt handling. Ferskhet vises fortsatt per mål og skal ikke utledes av aktivitets- eller oppmerksomhetsnivået.

#### 8.5.5 Sannferdig ferskhet og delresultat

- `Oppdatert` brukes bare når en komplett og fortsatt gyldig analyse viser null ventende endringer for det aktuelle målet.
- Filovervåking kan endre statusen til `Endringer oppdaget` eller `Må kontrolleres`, men kan ikke etablere `Oppdatert`.
- Et vellykket tidligere resultat uten ny kildekontroll vises som `Sist sikkerhetskopiert <tid>`, ikke som `Oppdatert` eller `Beskyttet`.
- Hvert mål viser eget siste vellykkede tidspunkt, siste kontroll og eventuell forsinkelse.
- En jobb som lykkes på to av tre mål vises som `Fullført på 2 av 3 mål`, aldri bare `Fullført`.
- Relativ tid, som `i går`, kombineres med absolutt dato/tid i tooltip eller detaljvisning.
- En manuell jobb uten tidsplan får ikke vilkårlig rød «utdatert»-status. Påminnelse om alder aktiveres bare når brukeren har valgt ønsket maksimumsalder eller en tidsplan finnes.
- For en planlagt jobb kan standard terskel være neste forventede kjøring pluss en toleranse, for eksempel 50 % av intervallet, men den konkrete regelen skal vises og kunne endres.

### 8.6 Komponentbibliotek

Bygg et lite internt komponentbibliotek. Sidene skal komponeres av disse komponentene, ikke lage egne varianter av samme mønster.

#### 8.6.1 Knapper

Varianter:

- **Primary** — én per handlingsområde; for eksempel `Kjør backup`, `Se endringer`, `Start backup` eller `Start synkronisering`, avhengig av jobbtype og tilstand;
- **Secondary** — vanlige handlinger;
- **Subtle** — lavprioriterte handlinger i verktøylinjer;
- **Danger** — permanent tømming eller eksplisitt destruktiv handling;
- **Icon button** — må ha tooltip og tilgjengelig navn;
- **Split button** — bare der en standardhandling har to–tre nært beslektede varianter.

Tilstander:

- normal;
- hover;
- pressed;
- keyboard focus;
- disabled med forklarende tooltip når årsaken ikke er åpenbar;
- loading med spinner og stabil bredde.

Minimum treffområde er 32 × 32 px for kompakte kontroller og 40 × 40 px for primære kontroller.

#### 8.6.2 Endepunktkort

Et endepunktkort skal alltid vise:

- rolle: `Kilde`, `Mål 1`, `Mål 2` eller `Mål 3`;
- egendefinert navn;
- forkortet sti;
- enhetstype: SSD, HDD, USB, SMB/NAS eller ukjent;
- tilgjengelighet;
- ledig plass;
- sist kontrollert tilgjengelighet;
- statusikon;
- meny for test, redigering og åpning i Utforsker.

Eksempel:

```text
┌──────────────────────────────────────────────┐
│ KILDE                              ● Klar    │
│ Bildemaster                                  │
│ D:\Bilder og videoer                         │
│ SSD · 3,82 TiB brukt · Tilgjengelig nå         │
└──────────────────────────────────────────────┘
```

Krav:

- et frakoblet mål blir ikke bare grått; det viser `Frakoblet`, sist sett og en konkret handling;
- feil endepunktidentitet bruker rødt sperresymbol og teksten `Feil disk eller delt mappe`;
- fri plass visualiseres med en liten, nøytral kapasitetslinje, aldri som dominerende dashboardgrafikk;
- kortet skal være tastaturfokuserbart.

#### 8.6.3 Dataflyt og topologivisning

Jobboversikten bruker en tydelig topologivisning:

```text
[Kilde] ── Oppdater ──► [Mål 1]
        ├─ Oppdater ──► [Mål 2]
        └─ Oppdater ──► [Mål 3]
```

Visningen skal:

- bruke pilretning og tekstetikett;
- vise én separat linje per mål;
- bruke heltrukket linje for aktivt mål og stiplet linje for frakoblet mål;
- vise en liten puls som beveger seg langs aktiv linje under overføring;
- deaktivere puls ved redusert bevegelse;
- aldri kommunisere retning bare med plassering eller farge;
- bytte til stablet vertikal layout ved smal bredde.

#### 8.6.4 Statusmerker

Statusmerker er kompakte komponenter med ikon og tekst, for eksempel:

- `Klar`;
- `Ikke analysert`;
- `1 204 skal kopieres`;
- `18 skal erstattes`;
- `2 konflikter`;
- `Mål frakoblet`;
- `Gjenoppretting kreves`.

Maksimalt tre merker vises direkte på et kort. Resten samles i `+N` med tooltip eller detaljvisning.

#### 8.6.5 Oppsummeringskort

Bruk oppsummeringskort for beslutningsrelevant informasjon, ikke dekorative KPI-er. Et analysepanel kan ha:

- `Nye filer`;
- `Filer som erstattes`;
- `Konflikter`;
- `Til karantene`;
- `Blokkert`;
- `Data som kopieres`.

`Identiske` og andre uendrede elementer vises som en dempet sammendragslinje eller i detaljvisning, ikke som et like fremtredende beslutningskort. Hvert kort viser verdi, etikett, ikon og ved behov en kort konsekvens. Klikk filtrerer operasjonstabellen.

#### 8.6.6 Fremdrift

Fremdrift skal være ærlig, stabil og målspesifikk:

- én bred indikator viser den aktive fasen for hele kjøringen;
- en tynn indikator per valgt mål viser målspesifikk fremdrift;
- under skanning uten kjent total vises fase, antall behandlede filer, forløpt tid og aktiv mappe, men ingen oppdiktet prosent;
- når operasjonsplanen finnes, beregnes kopifremdrift primært fra byte og suppleres med filantall;
- verifisering, commit og opprydding vises som egne faser; 100 % skal ikke vises før alle valgte mål har nådd et ferdig eller eksplisitt delresultat;
- dersom flere mål kjører parallelt, vises både samlet valgt datamengde og per-mål-resultat;
- en frakoblet eller utelatt kopi må ikke få den samlede indikatoren til å se fullført ut uten teksten `2 av 3 mål fullført`;
- ubestemt indikator brukes bare når totalen reelt er ukjent;
- tekst viser både prosent når den er meningsfull og faktiske tall;
- aldri bruk bare en stor sirkulær spinner for en timevis prosess.

Fasene er:

1. kontrollerer kilde og mål;
2. leser filer;
3. sammenligner endringer;
4. kopierer;
5. verifiserer;
6. setter filer på plass;
7. rydder opp og lagrer resultat.

Brukerrettet tekst kan være enklere enn interne fasenavn. `Commit` skal for eksempel vises som `Setter filer trygt på plass`.

#### 8.6.7 Bannere, varsler og kortvarige meldinger

- **Inline-banner** brukes for tilstander som må forbli synlige på siden.
- **Kortvarig melding** brukes for en kort bekreftelse, som `Jobben ble lagret`.
- **Systemvarsel** brukes når vinduet ikke er aktivt eller en lang kjøring fullføres/feiler.
- **Modal dialog** brukes bare når en beslutning må tas før brukeren kan fortsette.

Et varsel skal inneholde:

- kort overskrift;
- én konkret forklaring;
- berørt jobb og mål når relevant;
- anbefalt handling;
- valgfri detaljlenke;
- teknisk feilkode kun i detaljvisning.

Like varsler skal grupperes og dempes. En lang kjøring skal normalt gi ett systemvarsel ved fullføring eller når handling kreves, ikke ett varsel per mål, fil, batch eller nytt forsøk. Klikk på varselet åpner den konkrete kjøringen og riktig problemseksjon.

#### 8.6.8 Operasjonstabell

Bruk `QTableView` med egen `QAbstractTableModel`, delegater og server-/databasebasert filtrering. Ikke bruk `QTableWidget`.

Standardkolonner:

1. Handling
2. Relativ sti
3. Kilde
4. Mål
5. Størrelse
6. Sist endret
7. Årsak
8. Status

Valgfrie kolonner:

- hashstatus;
- risikoscore;
- tidligere målversjon;
- verifiseringsnivå;
- feil/nytt forsøk;
- varighet.

Krav:

- standardvisningen viser endringer og avvik; identiske/uendrede poster er skjult til brukeren velger `Vis uendrede`;
- en hurtigfilterknapp `Krever oppmerksomhet` viser konflikt, blokkering, feil, erstatning og karantene;
- første kolonne har ikon og tekst;
- relativ sti får mest fleksibel bredde;
- tabellen støtter multivalg og kontekstmeny;
- `Ctrl+C` kopierer valgte rader som tabulatorseparert tekst;
- kolonner kan skjules, flyttes og tilbakestilles;
- sortering på millioner av rader skal utføres via indeks/database, ikke ved å materialisere alt i GUI-minnet;
- sticky header og tydelig valgt rad;
- detaljpanelet åpnes ved Enter eller dobbeltklikk;
- inline-redigering brukes ikke for filhandlinger; overstyring skjer i detaljpanelet eller en dialog med forklaring.

#### 8.6.9 Filterchips og søk

Analyse- og historikksider har:

- søkefelt med debounce;
- filterchips med antall;
- en fremhevet `Krever oppmerksomhet`-visning når avvik finnes;
- én `Alle endringer`-tilstand og en separat `Vis uendrede`-bryter;
- `Nullstill filtre` når noe er aktivt;
- lagret visning per jobb;
- tydelig resultattekst, for eksempel `Viser 342 av 12 481 operasjoner`.

#### 8.6.10 Detaljpanel

Et høyre detaljpanel på 320–400 px brukes for detaljert filinformasjon uten å forlate tabellen. Den viser:

- full kilde- og målsti;
- planlagt handling og årsak;
- størrelse og tidspunkt på begge sider;
- hashstatus;
- målspesifikk konsekvens;
- eventuell konflikt;
- brukerens overstyring;
- lenker til Utforsker og teknisk logg.

Detaljpanelet skal kunne lukkes og huske bredde.

### 8.7 Dashboard / Oversikt

Dashboardet svarer på fem spørsmål i denne rekkefølgen:

1. Pågår noe nå?
2. Krever noe handling?
3. Når lyktes backup sist på hvert mål?
4. Hvilke mål er tilgjengelige?
5. Hva er den anbefalte neste handlingen?

#### 8.7.1 Layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Oversikt                                                  [+ Opprett backup] │
│ Tilgjengelighet oppdatert for 2 min siden                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│ TRENGER OPPMERKSOMHET (1)                                                    │
│ Fotoarkiv · USB 2 er frakoblet                                               │
│ USB 1: sist sikkerhetskopiert i går                                          │
│ USB 2: sist sikkerhetskopiert for 19 dager siden                             │
│ NAS:   sist sikkerhetskopiert i går                 [Kontroller mål]         │
├──────────────────────────────────────────────────────────────────────────────┤
│ ANDRE JOBBER                                                                 │
│ Videoarkiv · sist sikkerhetskopiert i går                  [Kjør backup]      │
└──────────────────────────────────────────────────────────────────────────────┘
```

Når en jobb er aktiv, vises en egen **Aktive jobber**-seksjon over **Trenger oppmerksomhet**. En kompakt oppsummeringsrad kan vise `1 krever handling`, `1 aktiv` og `2 jobber`, men skal ikke bruke dekorative «helsepoeng» eller ordet `beskyttet`. En dashboardtekst som `Tilgjengelighet oppdatert` gjelder bare disk-/NAS-tilgjengelighet og må aldri tolkes eller presenteres som en ny kontroll av filinnhold.

#### 8.7.2 Jobbkort

Hvert jobbkort viser:

- jobbtittel, aktivitet og oppmerksomhetsoppsummering;
- kompakt dataflyt mellom kilde og mål;
- ett statusfelt per mål med vennlig navn, tilgjengelighet og siste vellykkede tidspunkt;
- en advarsel dersom flere mål er aliaser eller ligger på samme bekreftede lagringsenhet; antall mål og antall bekreftet uavhengige lagringsenheter skal ikke blandes;
- neste planlagte kjøring eller `Manuell`;
- ventende endringer når en gyldig analyse finnes; filovervåking kan bare vise `Endringer oppdaget` og utløse en ny kontroll;
- én kontekstuell primærhandling;
- en kort årsakslinje når oppmerksomhet kreves.

Jobbkortet bygger visningen av tre uavhengige dimensjoner:

- **Aktivitet:** inaktiv, kontrollerer, kopierer, verifiserer, pauset eller gjenoppretter;
- **Oppmerksomhet:** blokkert, trenger oppmerksomhet, venter eller normal;
- **Ferskhet per mål:** oppdatert, sist sikkerhetskopiert, aldri kontrollert eller ukjent.

Presentasjonsprioriteten er deterministisk: aktiv fase vises først når arbeid pågår, blokkering/advarsel vises som en samtidig sekundær status, og ferskhet vises på hvert mål. `Oppdatert` brukes bare når en gyldig kontroll bekrefter null ventende endringer for det aktuelle målet. `Sist sikkerhetskopiert` brukes når siste kjøring lyktes, men dagens kilde ikke er kontrollert. `Ikke konfigurert` brukes når jobben mangler gyldig kilde/mål eller aldri er ferdigstilt.

Jobbkortet skal aldri skjule at bare noen mål lyktes. Ved delresultat brukes for eksempel:

```text
Fullført på 2 av 3 mål · USB 2 venter
```

#### 8.7.3 Dashboardtomtilstand

Når ingen jobber finnes:

```text
[Original dataflyt-illustrasjon]

Lag din første backup
Velg én mappe og opptil tre disker eller NAS-mål. Sikker standard er allerede valgt.

[Opprett backup]
Opprett avansert synkronisering

Ingen brukerfiler kopieres, erstattes eller flyttes før endringene er kontrollert. Registrering av et skrivbart mål oppretter bare en skjult kontrollmappe.
```

Den sekundære lenken **Opprett avansert synkronisering** skal være synlig, men ikke konkurrere visuelt med standardhandlingen.

### 8.8 Opprett og rediger jobb

Bruk en kompakt fullsideflyt, ikke en liten modal og ikke en åtte-trinns konfigurasjonsveiviser. Standard backup skal kunne opprettes i høyst fire steg. Ved oppretting lagres utkast automatisk etter hvert gyldig steg og kan lukkes og gjenopptas uten datatap. Redigering av en etablert jobb følger §8.8.6 og endrer ikke aktiv konfigurasjon før eksplisitt lagring.

#### 8.8.1 Standardflyt i fire steg

1. **Hva vil du beskytte?** — velg kilde. Jobbnavn foreslås fra mappenavnet og kan redigeres på samme side.
2. **Hvor vil du ha kopier?** — legg til ett til tre mål og gi dem vennlige navn.
3. **Hvordan skal backupen fungere?** — vis et kort sammendrag av sikre standarder og valgfrie, sammenleggbare seksjoner.
4. **Kontroller og opprett** — vis kilde, mål, forventet oppførsel og eventuelt tidsplan. Opprett jobben og start første kontroll av endringer.

En vertikal eller horisontal stepper viser de fire stegene. `Tilbake`, `Fortsett senere` og `Fortsett` har fast plassering. **Fortsett senere** lukker flyten uten å aktivere et uferdig oppsett; utkastet er allerede lagret automatisk. Det skal ikke finnes et eget obligatorisk steg for jobbnavn, filterbygger, verifiseringsnivå eller automatisering.

#### 8.8.2 Kilde og mål

Kildevelgeren tilbyr:

- mappevelger og nylig brukte steder;
- automatisk navneforslag;
- tydelig vennlig navn og full sti;
- varsling ved systemrot, brukerprofil eller annen risikabel plassering.

Målvelgeren tilbyr:

- oppdagede flyttbare disker;
- lokale mapper og tilordnede nettverksstasjoner;
- direkte UNC-inntasting;
- automatisk tilgangstest etter valg, med **Test på nytt** bare når testen feiler eller stedet senere blir utilgjengelig;
- visning av vennlig navn, disk-/NAS-type, fri plass og forventet kapasitet;
- sammenligning av lagringsidentitet mot kilden og allerede valgte mål; samme fysiske enhet eller kjent alias får teksten `Samme lagringsenhet som <navn> – dette gir ikke en ekstra uavhengig kopi`; et mål på samme fysiske enhet som kilden får i tillegg forklaringen `Beskytter ikke mot feil på denne lagringsenheten`;
- blokkering av like eller overlappende røtter før brukeren kan fortsette: `Dette målet ligger i eller inneholder kilden. Velg en separat mappe for å unngå at backupen kopierer seg selv.` For mål–mål-overlap brukes `Dette målet overlapper <navn>. Velg en separat målmappe.` Et skrivbart rotområde som allerede brukes av en annen lagret jobb, får teksten `Dette stedet brukes allerede av <jobb>. Åpne den eksisterende jobben eller velg en egen målmappe.`;
- handlingen `Legg til et mål` frem til tre mål er valgt;
- tydelig forklaring før et mål registreres som skrivbart og får `.mediasync`-kontrollmappe.
- en kompakt oppsummering, for eksempel `3 mål · 2 bekreftet uavhengige lagringsenheter · 1 ukjent`; ukjent uavhengighet er informasjon, ikke en falsk garanti.

Et valgt sted vises som et fullstendig endepunktkort før brukeren går videre. Rå volum-GUID, share-ID og kapabilitetsdata ligger under **Tekniske detaljer**.

#### 8.8.3 Backupinnstillinger med sikre standarder

Standardkortet viser, uten at brukeren må velge noe:

```text
Oppdater backup · Alle brukerfiler · Standard kontroll
Tidligere versjoner beholdes i 30 dager
Ekstra filer på målet beholdes
```

I utgangspunktet vises bare dette standardkortet og den sekundære handlingen **Tilpass**. Alle avanserte seksjoner er lukket til brukeren åpner dem, og overskriften viser en kort oppsummering av eventuelle avvik fra standarden.

Sammenleggbare seksjoner:

- **Hva skal tas med** — `Alle brukerfiler – anbefalt`, `Bilder og RAW`, `Video`, `Bilder, RAW og video`, eller egendefinerte filtre; mediepresets inkluderer relevante sidecars som standard;
- standardvalget viser tydelig at MediaSync-kontrollfiler, papirkurv, kjente systemfiler og midlertidige filer utelates; handlingen **Se automatiske unntak** viser den faktiske listen fra §7.2;
- **Backupoppførsel** — `Oppdater backup` som standard; `Speil backup` ligger under **Avansert** og forklarer karantene;
- **Sikkerhet og kontroll** — `Standard – anbefalt` som brukerrettet navn for balansert verifisering, samt valget `Grundig kontroll`, versjonsbevaring, karantene og ustabile filer;
- **Automatikk** — valgfri tidsplan eller trigger. Den kan hoppes over og tilbys igjen etter første vellykkede backup;
- **Ytelse** — viser `Auto – anbefalt`; andre profiler ligger under **Flere valg**.

Avanserte filterregler skal kunne leses som setninger, for eksempel:

```text
Inkluder filer der filtypen er JPG, HEIC eller CR3
Unntatt mapper som matcher **/.cache/**
```

#### 8.8.4 Avansert synkronisering

`pair_sync`, reverse og toveis opprettes fra **Opprett avansert synkronisering**. Denne flyten:

- har nøyaktig to endepunkter;
- viser retning på hvert steg;
- forklarer baseline og konfliktatferd med brukerspråk, ikke databaseterminologi;
- presenterer **Oppdater venstre → høyre**, **Oppdater høyre → venstre**, **Speil** og **Toveis** som avanserte valg;
- krever eksplisitt kontroll før første kjøring;
- kan ikke byttes om til eller fra en fler-måls backup uten å opprette en ny jobb.

#### 8.8.5 Sikkerhetsoppsummering

Siste steg viser:

- kilde og alle mål;
- retning og backupoppførsel;
- hva som tas med;
- kontrollnivå;
- eventuell automatikk;
- karantene og versjonsbevaring;
- hvilke mål som får en skjult kontrollmappe;
- teksten: `Ingen brukerfiler kopieres, erstattes eller flyttes når jobben opprettes. Første kontroll viser hva som vil skje.`

Primærknapp for standardflyten: **Opprett og kontroller endringer**.

Når brukeren aktiverer primærknappen:

- lagres jobben varig før den potensielt langvarige kontrollen starter;
- lukkes opprettingsflyten og jobben åpnes i **Endringer**, slik at kontrollen kan fortsette mens brukeren navigerer videre eller skjuler vinduet;
- avbrutt kontroll lar jobben stå som `Første kontroll ikke fullført` og oppdaterer ingen ferskhetsstatus;
- **Opprett uten å kontrollere** lagrer jobben uten å starte kontroll og er sekundær, ikke konkurrerende, handling;
- feil under første kontroll fører ikke brukeren tilbake til steg 1; valgene beholdes og riktig problem vises på jobben.

0B-implementasjonsnote: Den nåværende lokale previewen bruker
**Opprett og registrer** som review-handling. Den lagrer jobben varig og registrerer
det valgte lokale målet som skrivbart ved å opprette bare `.mediasync`-kontrollmetadata
og utføre en avgrenset write/read/delete-test; den kopierer ingen brukerfiler.
Vellykket registrering vises som **Skrivbar og registrert** i jobbdeltaljen. Dersom
registreringen ikke kan fullføres, beholdes hele det gjennomgåtte utkastet og knappen
endres til **Prøv registrering på nytt**. Feilteksten brytes vertikalt i arbeidsflaten
uten horisontal scrolling eller clipping. Alle dynamiske source-, target-, status- og
planetiketter reserverer høyden som den aktuelle bredden faktisk krever, også etter
at et langt mål er valgt og jobbdeltaljen utvides.

Etter registrering materialiserer Engine Host en immutable første plan fra de
eksakte forseglede source-/target-snapshottene. Den aktive jobbdeltaljen viser
planstatus, operasjonsantall, bytes og en bounded operasjonspreview selv når ingen
run finnes. Dette starter aldri kopiering automatisk. En runnable forseglet plan
viser en eksplisitt **Start backup**-knapp. Knappen sender den eksakte plan-ID-en og
checksummen med stabil idempotensnøkkel, og deaktiveres når kjøringen er lagt i kø.
`CREATE_DIRECTORY` utføres journalført før avhengige filer og bruker en verifiserbar
recovery-markør frem til catalog-handoff er registrert.

Etter første vellykkede manuelle backup kan programmet vise én diskret anbefaling: `Vil du kjøre denne backupen automatisk?` med handlingen **Sett opp automatikk**. Den skal ikke avbryte fullføringsoppsummeringen.

#### 8.8.6 Redigering av en etablert jobb

Redigering av en etablert jobb skal ikke være en skjult løpende endring:

- feltene redigeres i et lokalt utkast og påvirker ikke gjeldende jobb før **Lagre endringer** aktiveres;
- endringer i kilde, mål, modus, filtre, verifisering eller automatikk viser en kompakt konsekvensoppsummering før lagring;
- primærhandlingen er **Lagre og kontroller endringer** når endringen ugyldiggjør tidligere analyse; **Lagre uten å kontrollere** er sekundær og setter jobben til `Må kontrolleres`;
- ren endring av jobbnavn eller varslingsvalg kan bruke **Lagre endringer** uten ny kontroll;
- mens en backupkjøring er aktiv, åpnes sikkerhetskritiske jobbinnstillinger skrivebeskyttet med teksten `Denne backupen kjører nå. Innstillinger kan endres når kjøringen er ferdig.`;
- en lagret konfigurasjonsendring forkaster ubrukte planer, men endrer aldri en pågående eller tidligere kjøring;
- ved navigasjon bort fra et endret, ikke lagret utkast får brukeren valgene **Fortsett å redigere**, **Forkast endringer** og **Lagre endringer**, med fokus på det tryggeste ikke-destruktive valget.


#### 8.8.7 Endepunkteierskap og kontrollområde i GUI

Normalvisningen skal forklare konsekvens uten distribuert-systemterminologi:

- `Dette målet er registrert på denne PC-en` for `VALID_OWNED`;
- `Dette målet er registrert av en annen MediaSync-installasjon` for `VALID_FOREIGN`;
- `Kontrollmappen kan ikke tolkes sikkert` for partial/corrupt/unknown state;
- `Nyere MediaSync-versjon kreves` for nyere kontrollskjema.

`VALID_FOREIGN` er read-only. Handlingslinjen kan tilby **Se detaljer** og **Start kontrollert overtakelse**, men aldri **Kjør backup**. Overtakelsesveiviseren viser gammel installasjons-ID i forkortet form, siste eierskapsepoke, uavklart recovery, hva som ikke blir slettet, og at full kontroll må kjøres etterpå.

0B-implementasjonsnote: Den lokale GUI-flyten viser **Start kontrollert overtakelse**
bare for en eksakt fremmed målbinding. En kompakt, språkbyttbar bekreftelsesdialog
viser eier, epoke, recovery-status, bevart namespace og konsekvensen før en eksplisitt
checkbox låser opp bekreftelsen. Lange mål- og detaljtekster reserverer høyde etter
`heightForWidth`, og Qt-testen verifiserer hele dialogen og hovedvinduet ved 900×560.
Et vellykket svar køer full analyse og tilbyr ikke automatisk kjøring.

En eksisterende ukjent `.mediasync`-mappe skal ikke beskrives som «MediaSync-data» før markøren er validert. Ved `UNKNOWN_NONEMPTY_DIRECTORY` brukes en hard blokkering med mulighet til å åpne mappen; appen skal ikke foreslå å tømme eller gi nytt navn automatisk.

Assurance-tekst skal være presis:

- **Innhold kontrollert nå** bare ved tilstrekkelig aktuell full hash-evidens;
- **Metadata kontrollert** når størrelse/tid/type er kontrollert uten full innholdslesing;
- **Tidligere innholdshash gjenbrukt** for metadatarevalidert cache;
- **Skriving bekreftet av lagringslaget** eller **durability ukjent** separat fra innholdskontroll.

### 8.9 Jobboversikt

Jobboversikten er hovedarbeidsflaten etter opprettelse. Den skal vise dagens situasjon og neste handling før tekniske detaljer.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Fotoarkiv                                  [Rediger backup] [Kjør backup]    │
│ Kopierer D:\Bilder til tre backupmål                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ KILDE  Bildemaster · Klar                                                    │
│ USB 1  Sist sikkerhetskopiert i går                                          │
│ USB 2  Frakoblet · sist sikkerhetskopiert for 19 dager siden                 │
│ NAS    Sist sikkerhetskopiert i går                                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Neste: Koble til USB 2, eller kjør på de to tilgjengelige målene             │
│ [Kontroller mål]                         [Kjør på 2 tilgjengelige mål]        │
├──────────────────────────────────────────────────────────────────────────────┤
│ Automatikk: Daglig 23:00 · Kontroll: Standard · Versjoner: 30 dager          │
└──────────────────────────────────────────────────────────────────────────────┘
```

Den grafiske topologien kan vises under statuslisten, men skal ikke fortrenge per-mål-ferskhet eller anbefalt neste handling.

#### 8.9.1 Tilstandsbaserte hurtighandlinger

| Tilstand | Primærhandling |
|---|---|
| Ny jobb som aldri er kontrollert | **Kontroller første backup** |
| Lagret konfigurasjon er endret | **Kontroller endringer** |
| Etablert standardbackup | **Kjør backup** |
| Gyldig kontroll med erstatning/risiko | **Se endringer** |
| Aktiv kjøring | **Åpne fremdrift** |
| Ett mål frakoblet, andre tilgjengelige | **Kjør på tilgjengelige mål** |
| Alle mål frakoblet | **Kontroller mål** |
| Gjenoppretting kreves | **Gjenopprett kjøring** |
| Kritisk blokkering | **Løs problemet** |

Det skal aldri finnes to konkurrerende primærknapper. **Kontroller endringer** kan være en sekundær handling for brukeren som ønsker analyse uten automatisk start. **Kjør backup** utfører alltid sikkerhetskontroll og analyse først; det er ikke en snarvei rundt sikkerhetsmotoren.

#### 8.9.2 Arkivering av jobber

Jobbmenyen tilbyr **Arkiver jobb** fremfor en fremtredende slettehandling:

- arkivering deaktiverer alle triggere og fjerner jobben fra standarddashboardet;
- historikk, kontrollresultater, versjoner, karantene og gjenopprettingsinformasjon beholdes;
- arkiverte jobber finnes under filteret **Arkivert** på siden **Jobber**;
- **Aktiver jobb igjen** reaktiverer oppsettet, kontrollerer alle lagringssteder på nytt og setter status til `Må kontrolleres`;
- arkivering kan ikke gjennomføres mens jobben kjører eller har uløst gjenoppretting;
- automatisk retensjonsopprydding for jobbens versjoner og karantene pauses mens jobben er arkivert; eventuell opprydding krever en egen, eksplisitt vedlikeholdshandling;
- handlingen endrer aldri kilde-, backup- eller `.mediasync`-filer;
- permanent sletting av jobbhistorikk og gjenopprettingsmetadata er ikke en vanlig jobbhandling i første versjon.

### 8.10 Analysevisning

Analysevisningen er programmets viktigste sikkerhetsskjerm. I standard backup kalles fanen og sidetittelen **Endringer**. Den skal gjøre konsekvensene forståelige uten at brukeren må åpne operasjonstabellen.

#### 8.10.1 Layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Fotoarkiv / Endringer          [Forkast kontroll] [Start backup på 2 mål]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 2 mål klare · USB 2 er frakoblet · kontroll utført 14:32                    │
│ USB 2 er ikke med · [Kjør USB 2 automatisk når den kobles til]             │
├──────────────────────────────────────────────────────────────────────────────┤
│ [1 204 Nye] [18 Erstatter] [0 Konflikter] [0 Karantene] [642 GiB]            │
│ 8 420 uendrede filer · vis uendrede                                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Krever oppmerksomhet: 18 filer erstattes; gamle versjoner beholdes 30 dager │
├──────────────────────────────────────────────────────────────────────────────┤
│ Søk ... [Krever oppmerksomhet] [Nye] [Erstatter] [Blokkert] [Alle endringer]│
├──────────────────────────────────────────────┬───────────────────────────────┤
│ Operasjonstabell                             │ Detaljer                      │
│ ...                                          │ Valgt fil og begrunnelse      │
└──────────────────────────────────────────────┴───────────────────────────────┘
```

Identiske/uendrede filer skal ikke fylle standardtabellen. De vises som et dempet antall med handlingen **Vis uendrede**. Handlingen for det frakoblede målet er sekundær og åpner eller oppdaterer ventepolicyen; den skal ikke konkurrere visuelt med **Start backup på 2 mål**. Dersom agenten i systemstatusfeltet ikke er aktiv, forklares dette før en tilkoblingstrigger opprettes.

#### 8.10.2 Analysefaser

Under kontroll av endringer vises:

```text
1. Kontrollerer kilde og mål
2. Leser kilde
3. Leser mål
4. Sammenligner endringer
5. Kontrollerer tvetydige filer
6. Bygger sikker kjøreplan
```

Før totalen er kjent vises ikke prosent. Vis behandlede filer, forløpt tid og aktiv mappe, men ikke oppdater filnavn raskere enn GUI-throttlingen tillater.

#### 8.10.3 Oppmerksomhetsbanner

Banneret ligger over detaljlisten og bruker konkret språk:

- **Nøytralt:** `Bare nye filer blir kopiert. Ingen eksisterende filer endres.`
- **Gult:** `18 filer blir erstattet. Tidligere versjoner beholdes i 30 dager.`
- **Gult:** `92 filer flyttes til karantene på NAS.`
- **Gult:** `USB 2 er ikke med i denne kjøringen.`
- **Rødt:** `Backup er blokkert fordi målidentiteten ikke stemmer.`
- **Rødt:** `Endringsmengden overskrider sikkerhetsgrensen og må kontrolleres.`

Banneret skal beskrive årsak, berørt mål og neste handling. En trygg plan trenger ikke et stort grønt suksessbanner; en rolig nøytral oppsummering er tilstrekkelig.

#### 8.10.4 Handlingsspråk

| Intern operasjon | Brukertekst |
|---|---|
| `COPY_NEW` | `Kopier ny fil` |
| `REPLACE_CHANGED` | `Erstatt fil i backup` |
| `SKIP_IDENTICAL` | `Ingen endring – identisk` |
| `QUARANTINE_TARGET_EXTRA` | `Flytt ekstra fil til karantene` |
| `CONFLICT_BOTH_CHANGED` | `Behold begge – konflikt` |
| `SKIP_FILTERED` | `Ikke tatt med av filter` |
| `DEFER_UNSTABLE` | `Venter – filen endres fortsatt` |
| `DEFER_AUTOMATION_POLICY` | `Venter på kontroll` |
| `BLOCK_*` | `Blokkert – se årsak` |

For `pair_sync` kan målrettingen være `Erstatt fil til høyre/venstre`, men pilretning og endepunktnavn skal alltid være synlige.

#### 8.10.5 Kontroll og start

Kontrollflaten viser:

- tidspunkt og gyldighet;
- hvilke mål som kjøres nå;
- totale filer og byte per mål;
- erstatninger, karantene, konflikter og blokkeringer;
- fri plass;
- valgt verifisering;
- forklaring på utelatte mål.

Plan-ID og kontrollsum ligger under **Tekniske detaljer** og skal ikke være del av brukerens beslutning.

Bekreftelsesregler:

- Når brukeren trykker **Kjør backup** på en etablert jobb og kontrollen bare finner nye filer, mappeoppretting, identiske hopp og forventede filterhopp, starter backup uten en ekstra modal.
- Når jobbkortet allerede viser et frakoblet mål og brukeren eksplisitt velger **Kjør på tilgjengelige mål**, regnes målutelatelsen som forstått; en ellers trygg plan kan starte uten enda en bekreftelse. Hvis et mål blir utilgjengelig etter klikket eller statusen var foreldet, åpnes kontrollflaten.
- Første kjøring, endret konfigurasjon, erstatning, konflikt, karantene, blokkering, stor terskelendring eller et mål som uventet faller ut etter brukerens starthandling, åpner denne kontrollflaten.
- En vanlig versjonert erstatning krever én tydelig kontrollhandling, ikke skriving av jobbnavn.
- Speiling/toveis krever eksplisitt kontroll hver gang planen inneholder karantene eller konflikt.
- Skriving av jobbnavn brukes bare ved terskeloverskridelse fra §4.4 eller permanent tømming, ikke ved vanlig backup.

#### 8.10.6 Målutvalg og delvis kjøring

Når ett av flere mål er frakoblet eller blokkert:

- alle konfigurerte, tilgjengelige mål er valgt som standard;
- utilgjengelige mål vises eksplisitt med årsak og kan ikke velges;
- et tilgjengelig mål kan utelates bare ved en eksplisitt handling; valget vises i oppsummeringen, journalføres og oppdaterer ikke målets ferskhetsstatus;
- et eksplisitt valg om å kjøre på tilgjengelige mål er gyldig bare for den aktuelle kontrollen/kjøringen og endrer ikke jobbens permanente målsett;
- primærknappen bruker konkret tekst, som `Start backup på 2 mål`;
- resultatet og historikken beholder en utestående status for det utelatte målet;
- programmet kan tilby `Kjør USB 2 når den kobles til` dersom automatikk/agent er aktiv;
- det skal aldri se ut som alle tre kopier er oppdatert etter en kjøring som bare omfattet to.

#### 8.10.7 Ingen endringer

Når en fullført og gyldig analyse bare inneholder `SKIP_IDENTICAL` og forventede `SKIP_FILTERED`-poster, uten utsatte handlinger, konflikter, feil eller blokkeringer:

- opprettes ingen tom `run`; analysen lagres som kontrollresultat;
- vises en stabil melding: `Ingen endringer. Kilde og valgte mål ble kontrollert <dato og tid>.`;
- oppdateres ferskhetsstatus bare for mål som faktisk inngikk i den komplette kontrollen;
- tilbys handlingene **Se kontrollresultat** og **Til oversikt**, ikke **Start backup**;
- identiske filer og forventede filterhopp kan vises under detaljer, men skal ikke fremstilles som arbeid som må utføres.

#### 8.10.8 Kontroll fullført uten kjøring

Når kontrollen finner utsatte, konfliktfylte eller blokkerte operasjoner, men ingen operasjoner som policyen tillater å utføre nå:

- opprettes ingen tom `run`; kontrollresultatet lagres med status `Handling nødvendig`;
- oppdateres ingen mål til `Oppdatert` med mindre det aktuelle målet samtidig er bevist uten ventende endringer;
- viser dashboard, varsel og historikk samme konkrete antall, mål og årsak;
- er **Se endringer** primærhandling, med et målrettet alternativ som `Kontroller 18 erstatninger` når én årsak dominerer;
- behandles `Kontroller bare`-policyen på samme måte: `Ingen endringer` ved null funn, ellers `Kontroll fullført – handling nødvendig`.

### 8.11 Aktiv kjøring

Aktiv kjøring skal være informativ uten å ligne en rå loggmonitor. Brukeren skal kunne se hva som skjer nå, hva som allerede er trygt, og om noen mål er utelatt eller venter.

#### 8.11.1 Layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Fotoarkiv / Backup                                      [Pause] [Stopp ▾]    │
├──────────────────────────────────────────────────────────────────────────────┤
│ Kopierer til NAS · 62 %                                                       │
│ ████████████████████████████░░░░░░░░░░░░░░                                  │
│ 740 GiB av 1,20 TiB · 94 MB/s · omtrent 1–1,5 time igjen                      │
├──────────────────────────────────────────────────────────────────────────────┤
│ USB 1  ✓ Fullført   USB 2  ○ Ikke med   NAS  62 % Kopierer                  │
├──────────────────────────────────────────────────────────────────────────────┤
│ Nå: 2024\Familie\Sommer\VID_0421.MOV · 8,4 GiB                               │
│ [Diskret throughput-graf for siste 5 minutter]                               │
├──────────────────────────────────────────────────────────────────────────────┤
│ 8 941 ferdig · 417 igjen · 2 nye forsøk · 0 mislyktes                       │
│ [Vis hendelser] [Vis problemer]                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### 8.11.2 Kontrollhandlinger

- `Pause` venter på et sikkert stoppunkt og endrer tekst til `Pauser …`.
- `Fortsett` gjenopptar en pauset kjøring.
- `Stopp etter aktiv fil` er standard stoppvalg.
- `Stopp nå` er sekundært valg med forklaring om at midlertidige filer beholdes for trygg gjenoppretting eller senere fortsettelse.
- Hovedvinduets lukkeknapp skal ikke automatisk stoppe kjøringen. Første gang får brukeren valget `Skjul i systemstatusfeltet` eller `Stopp og avslutt`; en ikke-destruktiv preferanse kan huskes.
- Ingen global hurtigtast skal starte eller stoppe en kjøring uten at riktig side og handling er synlig.

#### 8.11.3 Hastighet, fremdrift og ETA

- vis glidende gjennomsnitt som hovedhastighet og øyeblikkelig hastighet i mindre tekst;
- ETA vises som intervall eller `Beregner …` når variasjonen er høy;
- ikke vis falsk presisjon i sekunder for timevis arbeid;
- ved mange små filer forklarer teksten at filbehandling, ikke nettverk, begrenser hastigheten;
- throughput-grafen viser maksimum fem minutter og oppdateres høyst én gang per sekund;
- samlet fremdrift skal ikke skjule at ett mål er langsommere, venter eller er utelatt;
- under verifisering og sikker innsetting kan bytekopiering være ferdig uten at kjøringen er fullført; vis fasen i stedet for å stå på `100 %` lenge.

#### 8.11.4 Per-mål-resultat

Hvert mål har én av tilstandene:

- `Venter`;
- `Kontrollerer`;
- `Kopierer`;
- `Verifiserer`;
- `Fullført`;
- `Fullført med advarsel`;
- `Fullført – handling nødvendig`;
- `Ikke med i denne kjøringen`;
- `Mislyktes – kan prøves på nytt`;
- `Blokkert`.

Klikk på et mål filtrerer hendelser og problemer til dette målet. `Prøv igjen` skal som standard gjelde bare mislykkede operasjoner eller det mislykkede målet, ikke starte hele jobben på nytt.

#### 8.11.5 Hendelser og problemer

Hendelsespanelet viser korte, brukerorienterte hendelser:

```text
14:42  NAS koblet til igjen
14:43  Prøver VID_0421.MOV på nytt
14:44  Kontroll fullført for 246 filer
```

Problemvisningen grupperer like årsaker og viser:

- hva som skjedde;
- hvor mange filer og hvilket mål som ble påvirket;
- om resten av kjøringen fortsatte trygt;
- anbefalt handling;
- `Prøv disse på nytt` når det er sikkert.

Rå Robocopy-output ligger i separat teknisk logg.

#### 8.11.6 Fullføringsoppsummering

Etter kjøring erstattes fremdriftsflaten av en stabil oppsummering:

```text
Backup fullført på 2 av 3 mål
USB 1 og NAS er ferdige. USB 2 var ikke tilkoblet.
1 204 filer kopiert · 18 filer erstattet · 642 GiB · 0 mislyktes

[Kjør USB 2 når den kobles til] [Se detaljer] [Til oversikt]
```

Resultatnivåer:

- **Fullført** — alle valgte mål og operasjoner lyktes;
- **Fullført med advarsel** — alle tillatte operasjoner ble forsøkt, men enkelte elementer ble hoppet over eller fikk en ikke-blokkerende advarsel; ingen umiddelbar brukeravgjørelse kreves;
- **Fullført – handling nødvendig** — automatikkpolicyen fullførte trygge operasjoner, men utsatte eksplisitte endringer som krever en konkret brukeravgjørelse;
- **Delvis fullført** — minst ett mål lyktes og minst ett mål mislyktes eller var utelatt;
- **Stoppet** — brukeren stoppet kontrollert;
- **Mislyktes** — ingen valgt mål fullførte eller gjenoppretting kreves.

Jobbnivået bruker **Delvis fullført** når ikke alle konfigurerte mål ble fullført. Dersom samme kjøring også har policyutsatte operasjoner, vises `Handling nødvendig` som en sekundær status og neste handling; dette skal ikke erstattes av et misvisende generelt suksessnivå.

Oppsummeringen skal ikke forsvinne som en kortvarig melding. Den skal være mulig å åpne igjen fra historikken.

### 8.12 Duplikatvisning

Duplikatvisningen skal prioritere innsikt og sikkerhet, ikke sletting. Den åpnes fra den aktuelle jobben eller analysen og skal tydelig skille forventede backupreplikaer fra reelle interne duplikater.

#### 8.12.1 Oversikt

Vis:

- antall bekreftede interne duplikatgrupper;
- antall forventede replikaer;
- antall filer i gruppene;
- samlet fysisk størrelse;
- mulig spart plass, ekskludert forventede replikaer;
- hashstatus;
- sist skannet tidspunkt.

Bruk teksten `Mulig spart plass på valgte lagringssteder` og forklar beregningen. Ingen filer fjernes automatisk.

#### 8.12.2 Visningsmoduser

- **Liste** — standard og best for store datasett;
- **Grupper** — én kompakt gruppe per bekreftet hash;
- **Valgt fil** — detaljpanel med metadata og en behovsstyrt forhåndsvisning.

Hver gruppe viser:

- relasjonsklasse: forventet replika, internt duplikat eller urelatert kopi;
- representativt navn;
- hashbekreftelse;
- størrelse per fil;
- antall kopier;
- lagringssteder og alle stier;
- eksplisitt spareberegning;
- eksport og `Åpne plassering`.

Ingen `Slett alle duplikater`-knapp skal finnes i første versjon.

#### 8.12.3 Forhåndsvisning av valgt fil

- bare valgt fil dekodes;
- arbeidet skjer utenfor GUI-tråden;
- bilde dekodes direkte til nødvendig visningsstørrelse;
- video bruker generisk videoikon i første versjon;
- cache er liten, minneavgrenset og kan slettes uten funksjonstap;
- feil eller skadet mediefil skal ikke påvirke resten av siden;
- forhåndsvisning er dekorativ og kan ikke være eneste identifikasjon.

### 8.13 Historikk og gjenoppretting

#### 8.13.1 Historikk

Historikksiden viser både **kontroller** og **backupkjøringer** i én virtualisert tidslinje. Hver rad viser:

- type: `Kontroll` eller `Backup`;
- jobb og mål som faktisk inngikk;
- status, for eksempel `Ingen endringer`, `Handling nødvendig`, `Fullført` eller `Delvis fullført`;
- start, slutt og varighet;
- kopierte, erstattede, karanteneflyttede, utsatte og blokkerte filer;
- byte og gjennomsnittshastighet bare når en overføring faktisk fant sted;
- trigger;
- varsel- eller feilteller.

Standardfilteret viser begge typer. Brukeren kan velge **Kontroller**, **Backupkjøringer** eller **Alle aktiviteter** uten å miste jobbfilter og tidsperiode.

Klikk på en kontroll eller kjøring åpner en detaljside med:

- oppsummering og målomfang;
- tidslinje;
- planlagte og eventuelt utførte operasjoner;
- identiske, filtrerte, utsatte, hoppede og blokkerte filer etter relevans;
- eksport til CSV/JSON;
- teknisk logg når en prosess faktisk ble kjørt;
- relaterte gjenopprettingselementer.

En `Ingen endringer`-kontroll skal være mulig å åpne igjen og vise hvilken komplette kontroll av kilden, hvilke mål og hvilket tidspunkt som begrunner ferskhetsstatusen.

#### 8.13.2 Gjenopprett

Gjenopprettingssiden samler:

- filer i karantene;
- tidligere målversjoner;
- avbrutte midlertidige filer som krever gjenoppretting;
- utløpstid og størrelse;
- opprinnelig og nåværende plassering.

For ett valgt element uten navnekollisjon skal standardflyten være kort:

1. velg element;
2. trykk `Gjenopprett til opprinnelig plassering`;
3. vis målsti og eventuell fil som blir erstattet;
4. gjenopprett og logg.

For flere elementer, ny plassering eller navnekollisjon brukes en eksplisitt gjenopprettingsplan. Brukeren kan forhåndsvise konflikter og velge `Behold begge`, `Erstatt med versjonering` eller `Hopp over`.

Permanent tømming er en separat farehandling og viser antall, byte, lagringsområde og at handlingen ikke kan angres.

### 8.14 Innstillinger og diagnostikk

Globale innstillinger skal være korte og forståelige. Jobbspesifikke valg redigeres på den aktuelle jobben; tekniske detaljer ligger i en separat diagnostikkflate.

#### 8.14.1 Globale innstillinger

Primærnivå:

- **Utseende:** system/lys/mørk, tetthet og redusert bevegelse;
- **Varsler:** fullført, advarsel, feil og stille perioder;
- **Standard for nye jobber:** versjonsbevaring, karanteneperiode og ytelsesprofil;
- **Lagring og vedlikehold:** logger, databaseplass, cache og oppryddingsoversikt;
- **Språk:** norsk bokmål og engelsk;
- **Om og hjelp:** versjon, lisenser, brukerhåndbok, diagnostikk og åpne datamappe.

Per-jobb-valg som filtre, mål, kontrollnivå, automatikk og ytelsesoverstyring skal ligge under **Rediger backup**, ikke dupliseres i globale innstillinger.

#### 8.14.2 Ytelsesprofil

Standardvalget er `Auto – anbefalt`.

- **Skånsom:** lav belastning, én aktiv overføring per flaskehals og pause i bakgrunnshashing under kopi.
- **Auto:** profilerer kilde, mål og arbeidslast og regulerer batcher med hysterese.
- **Maks gjennomstrømning:** høyere grenser på SSD og uavhengige mål, men identiske sikkerhets- og minneporter.

Normalvisningen viser bare profilnavn og en énlinjes forklaring. Valgt `/MT`, Robocopy-prosesser, aktive mål og hasharbeidere ligger under **Tekniske detaljer**. Rå tuningsparametere skal ikke være nødvendige i normal bruk, og ingen avansert innstilling kan deaktivere sikkerhetsporter.

#### 8.14.3 Diagnostikk

Diagnostikk åpnes fra **Hjelp og om**, en feildetalj eller endepunktmenyen. Den skal ikke være en dominerende global innstillingsseksjon.

Endepunktdiagnostikk viser:

- vennlig navn og konfigurert/faktisk sti;
- endepunkt-ID og generasjon;
- volum-GUID eller shareidentitet;
- lese-/skrivetest;
- long-path-status;
- fri plass og beregnet peak-behov;
- filsystem og maksimal støttet filstørrelse;
- case-modus og tidsstempelpresisjon;
- rename/replace-, fil-ID- og named-stream-støtte;
- valgt Task Scheduler-sikkerhetskontekst der automatikk er aktiv;
- siste feil med kopierbar teknisk detalj.

En knapp **Kopier diagnostikk** lager en personvernbevisst rapport som standard maskerer brukernavn og kan forkorte private stier før kopiering.

0B-implementasjonsnote: Innstillingssiden bruker tema, tetthet, redusert
bevegelse og norsk/engelsk umiddelbart og lagrer dem i en versjonert lokal
brukerpreferansefil med atomisk replace. Flaggmenyen og språkfeltet deler samme
preferanse. Engine Hosts eksisterende `state_capacity`-payload gir faktisk
tilstandsbruk, ledig plass og kapasitetstilstand uten GUI-SQLite-tilgang.
Datamappen kan åpnes, og **Kopier diagnostikk** lager en rapport uten brukernavn
eller private stier. Versjonsbevaring og ytelsesprofil vises skrivebeskyttet så
lenge domenekontrakten bare støtter henholdsvis 30 dager og Auto; karantene og
varsler merkes eksplisitt som utilgjengelige i den lokale previewen. Kontrollene
reflowes ved kompakt bredde og har automatisert 900×560-dekning uten horisontal
clipping.

### 8.15 Første oppstart og onboarding

Ikke bruk en tvungen karusell med flere sider. Første oppstart viser ett rolig velkomstpanel eller går direkte til dashboardets tomtilstand:

```text
Dine filer. Flere trygge kopier.
Velg én mappe og opptil tre backupmål. Du får se endringene før eksisterende filer påvirkes.

[Opprett backup]
Se en kort omvisning
```

Den valgfrie omvisningen har maksimalt tre fokuspunkter i den faktiske GUI-en:

1. kilden og backupmålene;
2. handlingen **Kjør backup**;
3. historikk og gjenoppretting.

Regler:

- brukeren kan hoppe over omvisningen;
- ingen forespørsel om oppstart, varsler, automatikk eller bakgrunnskjøring vises før brukeren aktiverer den relevante funksjonen;
- kontekstuelle tips vises én gang og skal kunne lukkes permanent;
- første jobboppretting fungerer som den egentlige onboardingen;
- sikkerhetsbudskapet skal være kort: `Du får se risikofylte endringer før de utføres. Speiling bruker karantene i stedet for direkte sletting.`

### 8.16 Tilstander som må designes eksplisitt

Hver primærside skal ha ferdig utformede tilstander for:

- første gangs tomtilstand;
- tomt søkeresultat;
- lasting med kjent total;
- lasting uten kjent total;
- delvis lastet innhold;
- offline/frakoblet endepunkt;
- tilgang nektet;
- utilstrekkelig diskplass;
- utdatert analyse;
- blokkert sikkerhetskontroll;
- avbrutt kjøring;
- gjenoppretting kreves;
- fullført med advarsler;
- fullført – handling nødvendig;
- ingen endringer etter kontroll;
- delvis fullført på noen mål;
- mål utelatt fra kjøringen;
- fatal feil;
- ingen internettilkobling skal ikke vises som feil, fordi produktet fungerer offline.

Skeleton placeholders kan brukes for korte databaseinnlastinger, men ikke for langvarig filskanning. Langvarig arbeid skal vise ekte fase og fremdrift.

### 8.17 Dialoger og sikkerhetsmønstre

Modaler skal brukes etter risiko, ikke vane. En bruker som allerede har trykket en tydelig handling skal ikke møte en ekstra `Er du sikker?` uten ny informasjon.

#### 8.17.1 Bekreftelsesmatrise

| Handling | Standard mønster |
|---|---|
| Opprette ny jobb | Utkast lagres automatisk; aktivering skjer først ved **Opprett og kontroller endringer** eller **Opprett uten å kontrollere** |
| Redigere etablert jobb | Endringer klargjøres i siden og aktiveres først ved eksplisitt **Lagre endringer**; vis konsekvensoppsummering uten en redundant «Er du sikker?»-modal |
| Kontrollere endringer | Ingen bekreftelse |
| Etablert backup med bare nye filer | Start etter innebygd sikkerhetskontroll og analyse, uten ekstra modal |
| Første backup eller endret konfigurasjon | Vis kontrollside før start |
| Erstatte filer med versjonsbevaring | Vis konkret kontrollside; én bekreftelse |
| Speil/karantene eller konflikt | Alltid synlig kontroll med antall, byte og mål |
| Stoppe etter aktiv fil | Ingen ekstra modal etter at stoppvalget er valgt |
| Stopp nå | Kort konsekvensforklaring |
| Arkivere jobb | Kort bekreftelse som forklarer at automatikk og retensjonsopprydding pauses, mens filer og historikk beholdes |
| Permanent tømming | Sterk, separat bekreftelse |

En reversibel handling skal ikke få unødvendig modal bekreftelse. Bruk en kortvarig melding med **Angre** bare når angre er reelt, raskt og sikkert.

#### 8.17.2 Risikofylt handling

Speiling, stor erstatning og karantene viser:

- hva som skjer;
- hvor mange filer og byte;
- nøyaktig mål med vennlig navn og sti;
- hvor lenge elementene beholdes;
- hvorfor handlingen er vurdert som risikofylt;
- knappetekst som beskriver handlingen.

#### 8.17.3 Permanent handling

Permanent tømming krever:

- tydelig farevariant;
- tydelig tekst om irreversibilitet;
- antall og byte;
- mål-/lagringsområde;
- en kort forsinkelse før primærknappen aktiveres, eller skriving av jobbnavn ved stor mengde;
- ingen forhåndsvalgt `Ikke spør igjen`;
- fokus skal ikke starte på fareknappen, og Enter skal ikke kunne utløse den utilsiktet.

#### 8.17.4 Feildialog

Vis først brukerrettet forklaring og anbefalt handling. En sammenleggbar `Tekniske detaljer`-seksjon kan inneholde:

- årsakskode;
- Windows-feilkode;
- berørt sti;
- korrelasjons-/run-ID;
- kopier til utklippstavle;
- åpne loggmappe.

Feilen skal følge mønsteret:

```text
Hva skjedde → Hva ble påvirket → Hva kan du gjøre nå → Tekniske detaljer
```

### 8.18 Mikrocopy og språk

Språket skal være konkret, rolig, jobbtypespesifikt og handlingsorientert.

Bruk i standard backup:

- `Kjør backup` som hovedhandling for en etablert jobb;
- `Kontroller endringer` når brukeren bare vil analysere;
- `Start backup` etter en kontrollside;
- `Oppdater backup` for ikke-destruktiv enveisoppdatering;
- `Speil backup` bare i avansert seksjon;
- `Stopp etter aktiv fil` i stedet for `Graceful stop`;
- `USB 2 er frakoblet` i stedet for `Endpoint unavailable`;
- `Sist sikkerhetskopiert i går` når dagens kilde ikke er kontrollert;
- `Oppdatert` bare når gjeldende kontroll faktisk beviser det;
- `Ingen filer slettes permanent` der karantene brukes;
- `Prøv disse filene på nytt` når handlingen bare gjentar mislykkede elementer.

Bruk i `pair_sync`:

- `Kontroller synkronisering`;
- `Start synkronisering`;
- tydelig venstre/høyre navn og pilretning.

Unngå i normal visning:

- rå enumverdier;
- `endepunkt`, `snapshot`, `baseline`, `fingerprint`, `batch`, `commit`, `plan-checksum` og `Robocopy`;
- tekniske forkortelser uten forklaring;
- utropstegn i vanlige suksessmeldinger;
- humor i feilmeldinger;
- ordet `slett` når programmet faktisk flytter til karantene;
- ordet `beskyttet` som generell garanti;
- generiske knapper som `OK`, `Kjør` og `Utfør` når et presist verb finnes.

Tid og størrelse følger brukerens locale. GUI viser binære størrelser konsekvent som KiB, MiB, GiB og TiB, mens nettverkshastighet vises som MB/s med forklaring i tooltip. Relativ tid suppleres med absolutt dato/tid i tooltip eller detaljvisning.

0B-implementasjonsnote: PySide-shellen har en flaggbasert språkvelger i handlingslinjen som viser valgt språk som flaggikon. Når den klikkes, åpnes en meny med støttede språk; valgt språk reappiserer synlige dashboard-, Engine Host-, jobbdeltalj-, planpreview-, snapshothelse-, katalog- og aktivitetslabels uten at presentasjonslaget åpner SQLite eller muterer Engine Host-state. Bidireksjonell 0B-dekning finnes for norsk/engelsk dashboard- og aktivitetsprefixer; full fremtidig strenginventar/lokaliseringsmatrise gjenstår.

Stier vises med vennlig mål- eller disknavn først. Full rå sti skal være tilgjengelig, kunne kopieres og aldri være eneste identifikasjon av et mål.

### 8.19 Tastatur, fokus og snarveier

Minimumssnarveier:

| Snarvei | Handling |
|---|---|
| `Ctrl+N` | Opprett ny backup |
| `Ctrl+F` | Fokuser søk/filter på gjeldende side |
| `Ctrl+,` | Åpne innstillinger |
| `Alt+Left` | Gå tilbake til forrige kontekst |
| `F5` | Oppdater synlig status eller kontroller tilgjengelighet uten å starte backup |
| `F6` | Flytt fokus mellom navigasjon, handlingslinje, arbeidsflate og aktivitetslinje |
| `Ctrl+Shift+C` | Kopier tekniske detaljer i feildialog |
| `Space` | Velg/avmerk rad når tabell har fokus |
| `Enter` | Åpne valgt element eller aktivere en vanlig, fokusert handling |
| `Esc` | Lukk detaljpanel, meny eller ikke-kritisk dialog |
| `F1` | Konteksthjelp |

Krav:

- logisk tab-rekkefølge;
- fokus returneres til utløsende kontroll når en dialog lukkes;
- synlig fokusramme på alle interaktive elementer;
- ingen global hurtigtast starter backup, speiling, gjenoppretting eller permanent handling;
- snarveier skal ikke omgå vanlig kontrollflyt;
- `Delete` skal ikke permanent slette filer fra analyse- eller duplikatvisning;
- `Enter` skal ikke aktivere en farehandling dersom fokus nettopp ble flyttet inn i dialogen.

### 8.20 Bevegelse og visuell respons

Bevegelse skal hjelpe orientering, ikke dekorere.

Tillatt:

- 120–180 ms hover/pressed-overganger;
- 180–240 ms side-/detaljpanelovergang;
- rolig dataflytpuls under aktiv overføring;
- determinerbar fremdriftsanimasjon;
- kort highlight når en status endres.

Ikke tillatt:

- kontinuerlig roterende logo;
- parallax;
- sprettende kontroller;
- store fade-ins på tabellrader;
- animasjon som forsinker handling;
- mer enn ett samtidig dekorativt bevegelseselement.

Respekter Windows-innstillingen for redusert bevegelse og tilby egen innstilling.

### 8.21 Tilgjengelighet

- full tastaturnavigasjon;
- tilgjengelige navn, beskrivelser og roller;
- skjermleservennlig statusoppdatering uten å annonsere hvert filnavn;
- høy kontrast og kompatibilitet med Windows High Contrast der Qt tillater det;
- ikke bruke farge som eneste signal;
- minimum 4,5:1 tekstkontrast og 3:1 for store komponentgrenser/fokus der relevant;
- automatisk test av alle tillatte semantiske foreground/background-par;
- tabeller skal kunne leses radvis og kopieres som tekst;
- alle ikoner som bærer mening har tekst eller tilgjengelig navn;
- forhåndsvisning har filnavn og filtype som tilgjengelig tekst;
- live-region-lignende annonsering begrenses til faseendring, pause, fullføring og feil;
- zoom/DPI fra 100 til 200 % uten klipping eller overlapp.

### 8.22 Responsivitet og DPI

#### 8.22.1 Breddeprofiler

| Profil | Logisk bredde | Oppførsel |
|---|---:|---|
| Kompakt | 1024–1199 px | Navigasjon 72 px, detaljpanel som overlegg, stablede endepunktkort |
| Standard | 1200–1599 px | Navigasjon 240 px, valgfritt detaljpanel, horisontal dataflyt |
| Bred | 1600 px eller mer | Mer tabellplass og fast detaljpanel; ikke strekk innhold uten maksgrenser |

#### 8.22.2 DPI-kvalitetskrav

Test minst:

- 100 % ved 1920 × 1080;
- 125 % ved 1920 × 1080;
- 150 % ved 2560 × 1440;
- 200 % ved 3840 × 2160;
- flytting mellom skjermer med ulik DPI;
- tekstforstørrelse i Windows;
- norsk og engelsk tekst, inkludert lengre etiketter.

Ingen kontroll skal få avkuttet tekst uten tooltip eller alternativ layout.

### 8.23 GUI-ytelse og trådsikkerhet

GUI-en skal føles umiddelbar selv når skanning, hashing eller Robocopy belaster maskinen. Følgende regler er bindende:

- GUI-tråden utfører aldri filskanning, hashing, Robocopy, mediedekoding, databasevedlikehold, store SQL-spørringer eller parsing av store logger;
- ingen synkron GUI-handler skal normalt bruke mer enn 16 ms; arbeid som kan overstige dette, flyttes til en arbeidstråd eller deles opp;
- resultater fra arbeidstråder leveres som små, uforanderlige view-model-snapshots via køede signaler;
- én sentral `UiUpdateCoalescer` samler hyppige arbeiderhendelser og oppdaterer widgets i kontrollerte intervaller;
- vanlig fremdrift oppdateres maksimalt 4 ganger per sekund, ETA 2 ganger per sekund og throughput-graf 1 gang per sekund;
- tabeller bruker `QAbstractTableModel`/`QTableView`, keyset-paginering og delegates; aldri én QWidget per rad;
- første side bør være 200–500 rader, med én side prefetchet i bakgrunnen;
- sortering og filtrering skjer i indeksstøttet SQL for store datasett; søk debounce-es 150–250 ms og gamle spørringer kanselleres;
- skjulte faner og innhold i detaljpanelet lastes først når de åpnes;
- bare valgt fil får forhåndsvisning; dekoding er behovsstyrt, størrelsesbegrenset og lagres i en liten LRU-cache;
- navigasjon, temaendring og oppdatering av ett statuskort skal ikke tvinge full restyle eller relayout av hele vinduet;
- ikoner rasteriseres/caches per DPI og tema; SVG skal ikke parses på nytt for hver repaint;
- en aktiv kjøring fortsetter uavhengig av hvilken side som er synlig eller om en side blir destruert;
- arbeidere holder aldri direkte referanser til widgets; livssyklusen styres gjennom kontrollere, kansellerbare oppgaver og trygge signalforbindelser;
- ved høy CPU- eller minnebelastning reduseres ikke-essensiell animasjon og forhåndsvisningsarbeid før skanning eller kopiering strupes.

#### 8.23.1 Opplevde latensbudsjetter

Målene må verifiseres på en dokumentert referansemaskin og rapporteres som median og P95:

| Handling | Mål |
|---|---:|
| Visuell respons etter klikk/tast | ≤ 50 ms |
| Navigasjon til allerede initialisert side | ≤ 150 ms P95 |
| Første side i en stor tabell | ≤ 300 ms etter ferdig SQL-resultat |
| Indeksert filter/søk på 1M poster | ≤ 500 ms P95 |
| Åpne detaljpanel for valgt rad | ≤ 150 ms uten medieforhåndsvisning |
| Maks enkeltfrys i vanlig bruk | < 100 ms |
| Kald appstart på referansemaskin | ≤ 4 s til interaktivt shell |

Målene er kvalitetsporter på referanseoppsettet, ikke garantier for vilkårlig maskinvare. Regressjoner over 20 % skal forklares før merge.

### 8.24 Presentasjonsarkitektur

Foreslåtte presentasjonskomponenter:

```text
presentation/
├── app.py
├── main_window.py
├── navigation/
│   ├── navigation_rail.py
│   └── route_controller.py
├── theme/
│   ├── tokens.py
│   ├── palettes.py
│   ├── typography.py
│   ├── metrics.py
│   ├── qss_builder.py
│   ├── icon_registry.py
│   └── theme_manager.py
├── components/
│   ├── buttons.py
│   ├── cards.py
│   ├── endpoint_card.py
│   ├── topology_view.py
│   ├── attention_panel.py
│   ├── target_freshness.py
│   ├── status_badge.py
│   ├── progress.py
│   ├── run_summary.py
│   ├── banners.py
│   ├── toast.py
│   ├── empty_state.py
│   ├── inspector.py
│   └── data_table.py
├── pages/
│   ├── dashboard_page.py
│   ├── jobs_page.py
│   ├── job_overview_page.py
│   ├── backup_setup_page.py
│   ├── advanced_sync_setup_page.py
│   ├── analysis_page.py
│   ├── run_page.py
│   ├── duplicates_page.py      # kontekstuell rute fra jobb/analyse
│   ├── history_page.py
│   ├── recovery_page.py        # kontekstuell rute fra historikk
│   └── settings_page.py
├── dialogs/
│   ├── confirmation_dialog.py
│   ├── destructive_action_dialog.py
│   ├── endpoint_picker_dialog.py
│   ├── error_details_dialog.py
│   └── close_while_running_dialog.py
├── models/
├── delegates/
├── controllers/
├── view_models/
└── resources.py
```

Krav:

- sidene skal være tynne visningslag;
- visningsmodeller eksponerer presentasjonsklar tilstand og kommandoer;
- controllers/application services eier arbeidsflyt;
- domenemodellen kjenner ikke Qt;
- `ThemeManager` kan bytte tema uten omstart;
- QSS genereres fra tokens og ligger ikke spredt i Python-filer;
- ikoner hentes gjennom `IconRegistry` med semantisk navn;
- komponenter har visuelle testsider eller et internt komponentgalleri i utviklingsbygg.

### 8.25 Systemstatusfelt

Agenten i systemstatusfeltet skal:

- bruke et tydelig monokromt ikon;
- vise aktiv jobb, fase og per-mål-resultat i tooltip;
- tilby `Åpne MediaSync Home`, en jobbmeny med `Kjør backup` for hver ikke-arkiverte jobb, `Pause`, `Fortsett`, `Stopp etter aktiv fil` og `Avslutt` når relevant; en direkte `Kjør backup`-handling vises bare når nøyaktig én jobb er entydig valgt som standard;
- åpne hovedvinduets kontrollside i stedet for å starte direkte dersom sikkerhetskontrollen finner erstatning, karantene, konflikt eller blokkering;
- vise siste feil uten rå teknisk tekst;
- overvåke disktilkobling og filendringer mens brukeren er logget inn;
- kunne starte automatisk ved pålogging;
- ikke vise en egen popup for hver fil eller batch;
- bruke systemvarsel ved fullført, fullført med advarsel og feil.

### 8.26 Visuell kvalitetsport

Før GUI-milepælen godkjennes, skal Codex opprette og lagre referansebilder for minst disse tilstandene i både lyst og mørkt tema:

1. tomt dashboard;
2. første steg i standard backupoppretting;
3. dashboard med tre mål, hvor ett er frakoblet;
4. jobboversikt med per-mål-ferskhet;
5. aktiv kontroll av endringer;
6. ferdig kontroll uten risiko;
7. kontroll med erstatning og karantene;
8. aktiv kjøring;
9. pauset kjøring;
10. delvis fullført på to av tre mål;
11. fullført med advarsler;
12. duplikatgrupper;
13. historikkdetalj;
14. gjenoppretting kreves;
15. innstillinger;
16. feildialog med tekniske detaljer;
17. 200 % DPI og norsk tekst.

Referansebildene skal gjennomgås mot følgende sjekkliste:

- tydelig visuell prioritet;
- én primærhandling;
- ingen klippet tekst;
- konsekvent avstand og radius;
- korrekt lys/mørk palett;
- statuser forstås uten farge;
- kilde, mål og retning kan ikke misforstås;
- destruktive handlinger har korrekt risikonivå;
- fokusrammer er synlige;
- tabell og detaljpanel fungerer ved minimumsbredde;
- ingen layout hopper når tall oppdateres;
- ingen proprietære grafiske elementer er kopiert.

### 8.27 GUI-akseptansekriterier

GUI-en er ferdig når:

1. alle hovedfunksjoner kan brukes uten CLI;
2. standard backup kan opprettes i høyst fire steg uten å åpne avanserte seksjoner;
3. jobbnavn foreslås automatisk og filter, kontrollnivå og automatikk krever ikke egne obligatoriske steg;
4. hovedflyten fra ny jobb til fullført kjøring kan gjennomføres med tastatur;
5. én kilde og tre mål vises uten uklar retning;
6. hvert mål viser tilgjengelighet og eksakt siste vellykkede tidspunkt;
7. ordet `Oppdatert` brukes bare når gjeldende kontroll beviser det;
8. en delvis kjøring vises som `N av M mål`, ikke som generell suksess;
9. det finnes én anbefalt primærhandling per handlingsområde;
10. en etablert trygg backup starter med én bevisst handling og uten redundant modal;
11. risikofunn stopper hurtigflyten og åpner kontrollvisningen før filer endres;
12. alle sider har definerte tom-, laste-, offline-, delvis fullførte, feil- og gjenopprettingstilstander;
13. kontrollvisningen viser konkrete konsekvenser før start;
14. en bruker kan finne årsaken til hver planlagt filhandling;
15. standardtabellen skjuler uendrede filer og fremhever elementer som krever oppmerksomhet;
16. en aktiv kjøring viser fase, byte, hastighet, ETA, aktiv fil, målstatus og problemer;
17. fullføringsoppsummeringen viser hva som lyktes, hva som mangler og neste handling;
18. nytt forsøk kan begrenses til mislykket mål eller mislykkede elementer;
19. lange stier kan inspiseres og kopieres uten å ødelegge layout;
20. operasjonstabellen fungerer med minst én million syntetiske rader uten å materialisere alle rader i GUI-minnet;
21. lyst, mørkt og systemtema fungerer uten omstart;
22. 100, 125, 150 og 200 % DPI består visuell test;
23. norsk og engelsk tekst fungerer uten klipping;
24. status kan forstås i gråskala og med skjermleser;
25. redusert bevegelse deaktiverer ikke-essensiell animasjon;
26. ingen langvarig aktivitet blokkerer GUI-tråden;
27. ingen side bruker tilfeldige hardkodede designverdier utenfor tokenmodellen;
28. referansebildene fra §8.26 er godkjent;
29. produktets visuelle identitet er selvstendig og ikke en kopi av Allway Sync;
30. navigasjon og tabellinteraksjon består latensbudsjettene i §8.23.1;
31. fremdriftsoppdateringer fører ikke til synlig layout-hopping eller høy CPU-bruk;
32. dashboard, kontrollside og kjøringsside fungerer uten mediedekoding eller annen dekorativ bakgrunnsjobb;
33. visuelle effekter kan nedskaleres automatisk uten å miste informasjon eller identitet;
34. normale sider eksponerer ikke interne ord som `endpoint`, `snapshot`, `commit`, `batch` eller `Robocopy`;
35. oppgavetestene i §8.30 er dokumentert og består uten sikkerhetskritisk misforståelse;
36. en aktiv jobb kan samtidig vise et frakoblet mål eller annen advarsel uten at aktiv fase eller neste handling skjules;
37. arkivering og reaktivering av en jobb bevarer historikk og endrer ingen brukerfiler.

### 8.28 Effektiv hovedflyt

GUI-en skal minimere valg og bekreftelser for det vanligste hjemmescenariet uten å redusere sikkerheten.

#### 8.28.1 Tilstandsbasert primærhandling

Hvert jobbkort og jobbheader viser nøyaktig én primærhandling:

| Tilstand | Primærhandling |
|---|---|
| Ny jobb som aldri er kontrollert | **Kontroller første backup** |
| Lagret konfigurasjon er endret | **Kontroller endringer** |
| Etablert backup uten kjent blokkering | **Kjør backup** |
| Kontroll finner bare nye filer, mappeoppretting og forventede hopp | Start automatisk som del av **Kjør backup** |
| Kontroll finner erstatning, konflikt, karantene eller terskelavvik | **Se endringer** |
| Aktiv kjøring | **Åpne fremdrift** |
| Pauset | **Fortsett** |
| Noen mål frakoblet | **Kjør på tilgjengelige mål** eller **Kontroller mål**, avhengig av policy |
| Gjenoppretting kreves | **Gjenopprett kjøring** |
| Kritisk blokkering | **Løs problemet** |

**Kjør backup** betyr alltid:

1. kontroller endepunktidentitet og kapasitet;
2. utfør inkrementell analyse;
3. vis `Ingen endringer` og opprett ingen kjøring når planen bare inneholder identiske filer og forventede filterhopp;
4. fortsett automatisk bare dersom planen består av nye kopier, mappeoppretting, identiske hopp og forventede filterhopp;
5. åpne **Endringer** med tydelig årsak dersom noe krever kontroll.

Dette krever ikke en egen «automatisk start etter analyse»-innstilling for manuell kjøring; brukerens klikk på **Kjør backup** er den bevisste startintensjonen. Planlagte/automatiske kjøringer bruker den separate policyen i §18.

#### 8.28.2 Effektive standarder

- Tomt dashboard tilbyr **Opprett backup** som primærhandling og avansert synkronisering som sekundær lenke.
- Standard opprettingsflyt har fire steg og foreslår jobbnavn automatisk.
- Preset **Én kilde → tre backupmål** kan fylle målstrukturen uten å endre sikkerhetsstandardene.
- `Oppdater backup`, `Alle brukerfiler`, `Standard kontroll`, `Auto – anbefalt` og 30 dagers versjonsbevaring er forhåndsvalgt.
- Avanserte filtre, speiling, scheduler-kontekst og rå ytelsesparametere ligger bak tydelige, sammenleggbare seksjoner.
- Dialoger brukes bare når en avgjørelse blokkerer videre arbeid; status og forklaring vises ellers inline.
- Jobbkort viser siste vellykkede tidspunkt per mål, neste kjøring, frakoblede mål og anbefalt handling uten at jobben må åpnes.
- Feilretting tilbyr en konkret neste handling, for eksempel **Koble til USB 2**, **Frigjør 84 GiB** eller **Prøv 3 filer på nytt**.
- Nytt forsøk gjenbruker gyldig plan og ferdige batcher når fingerprint og plan-checksum fortsatt stemmer; full analyse kjøres bare når sikkerheten krever det.
- Etter første vellykkede backup tilbys automatikk som et valgfritt neste steg, ikke som et hinder før første resultat.

### 8.29 Visuell effektbudsjett

Det mørke, premium uttrykket skal være lett å tegne og stabilt under belastning:

- native Windows-backdrop/Mica brukes bare når støttet og skal kunne deaktiveres uten layoutendring;
- ingen egen sanntidsblur over store flater;
- kort bruker primært kant, toneforskjell og svært subtil statisk skygge; store, myke skygger er reservert for modaler og flytende menyer;
- merkegradient brukes i logo, onboarding og små ikke-funksjonelle aksenter, ikke i funksjonelle knapper eller som kontinuerlig animert bakgrunn; primærknappen bruker en solid semantisk aksentfarge;
- animasjoner varer normalt 120–180 ms og begrenses til opacity, enkel geometri eller fremdriftsindikasjon;
- ingen pulserende dekorasjon, parallax eller kontinuerlig 60-fps-animasjon under filoverføring;
- throughput-grafen bruker et begrenset antall samplepunkter og repaintes bare når et nytt 1-sekundssample foreligger;
- tabellhover, selection og statusendring må ikke utløse expensive style recalculation på hele tabellen;
- bildeforhåndsvisning dekodes til visningsstørrelse, aldri full oppløsning i GUI-minnet;
- reduced-motion og automatisk ressursmodus skal bevare all informasjon og den visuelle identiteten.

### 8.30 Brukervennlighetsport og oppgavetester

Codex kan ikke erstatte reell brukertesting, men skal levere en reproducerbar manuell oppgaveprotokoll i `docs/USABILITY_CHECKLIST.md`. Minst prosjekteieren og helst to–fire andre Windows-brukere bør gjennomføre den før hjemmeutgivelsen.

Obligatoriske oppgaver:

| Oppgave | Bestått når |
|---|---|
| Opprett én kilde → tre backupmål | Fullført i høyst fire steg uten å åpne tekniske detaljer eller spørre hva et fagbegrep betyr |
| Velg to mål på samme fysiske disk | Brukeren ser at dette er to plasseringer, men bare én bekreftet uavhengig lagringsenhet |
| Kjør en etablert trygg backup | Én bevisst starthandling; ingen redundant modal når kontrollen bare finner nye filer |
| Kontroller en jobb uten endringer | Brukeren forstår at kontrollen er fullført, at ingen kopiering ble startet, og hvilke mål som nå er bekreftet oppdatert |
| Åpne kontrollen igjen fra historikken | Brukeren finner tidspunkt, målomfang og bevisgrunnlag uten at aktiviteten fremstilles som en backupkjøring |
| Finn hvorfor en jobb krever oppmerksomhet | Berørt mål, årsak og neste handling finnes fra dashboardet uten å åpne logg |
| Forstå delvis resultat | Brukeren kan si hvilke mål som lyktes og hvilket som mangler |
| Finn en konkret erstattet fil | Filen finnes fra kontroll-/historikkvisning med årsak og tidligere versjon |
| Prøv feil på nytt | Bare mislykket mål eller berørte filer startes på nytt |
| Gjenopprett én tidligere versjon | Fullført uten å åpne teknisk logg og uten uklar målsti |
| Stopp en kjøring trygt | Brukeren forstår forskjellen mellom `Stopp etter aktiv fil` og `Stopp nå` |
| Finn NAS-begrensning for automatikk | Forklaringen finnes ved oppsettet, ikke bare i dokumentasjon |
| Forstå utsatt automatikkhandling | Brukeren kan se hva som ble kopiert, hva som venter på kontroll, hvilket mål det gjelder, og neste handling |
| Rediger en etablert jobb | Brukeren forstår at endringer ikke gjelder før lagring, at tidligere kontroll blir ugyldig, og at en aktiv kjøring ikke endres |
| Velg en målmappe inne i kilden | Flyten blokkerer valget med en konkret forklaring og leder til valg av separat mappe |
| Følg en aktiv jobb med ett frakoblet mål | Brukeren ser både at kopiering pågår og at ett mål venter; ingen av tilstandene skjuler den andre |
| Arkiver og aktiver en jobb igjen | Automatikk stoppes, historikk bevares, ingen brukerfiler endres, og reaktivert jobb må kontrolleres på nytt |

Registrer for hver oppgave:

- fullført/ikke fullført;
- antall feilklikk eller tilbakehopp;
- steder der brukeren stopper opp i mer enn ti sekunder;
- ord eller statuser som misforstås;
- om brukeren kunne forutsi konsekvensen før start;
- om neste handling var synlig uten hjelp.

Utgivelsesblokkerende funn:

- brukeren forveksler kilde og mål;
- en delvis kjøring oppfattes som fullført på alle mål;
- `Oppdatert` tolkes som ferskere enn bevisgrunnlaget;
- speiling/karantene oppfattes som vanlig ikke-destruktiv backup;
- en bruker starter eller stopper en risikofylt handling ved et uhell;
- teknisk terminologi er nødvendig for å fullføre standardflyten.

---
