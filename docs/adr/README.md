# ADR-katalog

> **GENERERT FIL — IKKE REDIGER DIREKTE.** Oppdater `docs/adr/catalog.yaml` og kjør `python tools/build_adr_docs.py`.

## Statusmodell

- `evidence_status`: `PROPOSED`, `EVIDENCE_COMPLETE`, `RECOMMENDED` eller `BLOCKED`.
- `owner_decision`: `PENDING`, `OWNER_ACCEPTED`, `REJECTED`, `DEFERRED_WITH_SCOPE_REDUCTION` eller `SUPERSEDED`.

En ADR er bindende bare når `owner_decision = OWNER_ACCEPTED`. Ved `DEFERRED_WITH_SCOPE_REDUCTION` skal berørt garanti, release og eksplisitt sikker fallback dokumenteres.

## Katalog

| ADR | Beslutning | Hovedbegrunnelse | Konsekvens | Bevispakke | Evidensstatus | Eierbeslutning | Eierdato | Erstatter |
|---|---|---|---|---|---|---|---|---|
| `ADR-001` | Headless Engine Host er eneste muterende tilstandseier | Samme motor for GUI og Task Scheduler; færre races | Krever lokal IPC og prosesslivssyklus | `0A.1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-002` | Lokale named pipes med ACL og versjonert JSON-protokoll | Windows-native, reconnect og tydelig sikkerhetsgrense | Wire-schema må testes og vedlikeholdes | `0A.1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-003` | Én eller to lokale SQLite-databaser fryses etter Milepæl 0A | Målt avveining mellom enkel atomisitet og isolasjon av bulk/read model fra varig recovery | Ved to databaser kreves eksplisitt handoff/backup-epoch; ved én database kreves differensiert durability og samme recoveryinvarianter | `0A.4` | `RECOMMENDED` | `PENDING` |  |  |
| `ADR-004` | Robocopy skriver kun til unik staging | Isolerer tredjeparts prosess fra final tree | Python må eie commit og verifisering | `0A.3 + 0A.5` | `PROPOSED` | `PENDING` |  |  |
| `ADR-005` | Jobber, endepunkter og planer bruker uforanderlige revisjoner | Reproduserbar analyse, audit og retry | Flere rader og tydelig retention kreves | `0B + Milepæl 1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-006` | OS-håndtak er leaseautoritet; DB-rad er diagnostikk | Tåler prosesskrasj og kan sperre over SMB | Endepunkt uten pålitelig låsing får begrenset funksjon | `0A.2` | `PROPOSED` | `PENDING` |  |  |
| `ADR-007` | Targetmutasjon bruker compare-and-swap-preconditions | Hindrer overskriving av eksterne endringer etter analyse | Flere revalideringer og mulige `Plan utdatert`-resultater | `0A.3` | `PROPOSED` | `PENDING` |  |  |
| `ADR-008` | Outbox og desired-state reconciliation for eksterne systemer | Unngår falsk atomisitet med Task Scheduler/varsler | Sideeffekter kan leveres minst én gang | `0B + Milepæl 1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-009` | Ingen runtime-plugins eller dynamisk kodeinnlasting i første versjon | Reduserer angrepsflate og pakkeusikkerhet | Nye adapters krever ny signert build | `0B` | `PROPOSED` | `PENDING` |  |  |
| `ADR-010` | Live snapshot er eksplisitt best-effort, ikke VSS | Ærlig semantikk og korrekt revalidering | Speiling krever strengere preconditions | `0A.3` | `PROPOSED` | `PENDING` |  |  |
| `ADR-011` | Catalog/recovery/filsystem koordineres som saga med durable handoffs | Ingen falsk cross-store-atomisitet; restartbar avstemming | Flere eksplisitte mellomtilstander | `0A.3 + 0A.4` | `EVIDENCE_COMPLETE` | `PENDING` |  |  |
| `ADR-012` | Target-side recoverybevis lagres som bounded immutable intentsegmenter | Sekundært bevis uten én kontrollfil per brukerfil | Recovery må inspisere faktisk filtilstand per operasjon | `0A.3` | `PROPOSED` | `PENDING` |  |  |
| `ADR-013` | Alle runtimeprosesser kjører unelevated; transferchild opprettes suspended og contained før resume | Minste privilegium og ingen orphan-race | Manglende tilgang/Job Object-støtte blokkerer arbeid | `0A.1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-014` | Toveisbaseline bindes til immutable baselinekontekst | Hindrer at endret filter/root/semantikk tolkes mot gammel historikk | Contextendring krever ny, ikke-destruktiv etablering | `Milepæl 1 + Milepæl 14` | `PROPOSED` | `PENDING` |  |  |
| `ADR-015` | Finalmutasjon eksponeres bare gjennom capability-typede porter | Gjør bypass av lease/path/precondition vanskelig i kode | Flere smale porter og opaque runtimeobjekter | `0A.3 + 0B` | `PROPOSED` | `PENDING` |  |  |
| `ADR-016` | Endpointleaser bruker monotone fencing tokens | Avviser stale workers/recovery etter lease loss/reacquire | Token må følge meldinger, journal og intentsegment | `0A.2` | `PROPOSED` | `PENDING` |  |  |
| `ADR-017` | Idempotency- og deliverynøkler komprimeres til permanente tombstones | Retention kan ikke gjøre en gammel retry til ny sideeffekt | Små dedupindekser beholdes langsiktig | `0B + Milepæl 1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-018` | Catalog/recovery backup og restore bruker ett manifestert epoch-sett | Hindrer blandet intern sannhet etter crash/restore | Quiesce, high-water barrier og parvis swap kreves | `0A.4` | `EVIDENCE_COMPLETE` | `PENDING` |  |  |
| `ADR-019` | Ett skrivbart endpoint har én writer-installasjon per eierskapsepoke | Unngår distribuert multi-writer-synkronisering i hjemmeproduktet | Overtakelse er eksplisitt saga; fremmed owner er read-only | `0A.2` | `PROPOSED` | `PENDING` |  |  |
| `ADR-020` | `.mediasync` klassifiseres før ekskludering/adoption | Hindrer tap av legitim brukerdata med samme navn | Flere kontrolltilstander og recovery-UI | `0A.2` | `PROPOSED` | `PENDING` |  |  |
| `ADR-021` | Aktive revisjoner lagres i separate head-tabeller og alle parent-scope-relasjoner har DB-constraints | Gjør umulige kombinasjoner fysisk ugyldige | Flere composite keys og migrasjonstester | `0B + Milepæl 1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-022` | Hashes bærer evidensnivå; metadatarevalidert cache er ikke nåværende innholdsbevis | Hindrer falsk `SKIP_IDENTICAL` | Mer hashing i sikker modus | `0A.3 + Milepæl 3` | `PROPOSED` | `PENDING` |  |  |
| `ADR-023` | Kilden bindes med `SourceReadGuard` eller post-transfer current hash | Lukker source-TOCTOU så langt endepunktet tillater | Aktive handles og eksplisitt fallbackpolicy | `0A.3` | `PROPOSED` | `PENDING` |  |  |
| `ADR-024` | Kontrollområdet bruker korte objektstier med manifester | Unngår path-length-feil i staging/versions/quarantine | Restore og browsing går gjennom objektkatalog | `0A.3` | `PROPOSED` | `PENDING` |  |  |
| `ADR-025` | Live claims bruker monoton klokke; persistent UTC er diagnostikk | Tåler klokkejustering og restart uten falsk takeover | Startup må avstemme owner instance | `0B + Milepæl 1` | `PROPOSED` | `PENDING` |  |  |
| `ADR-026` | Eksakte kontrakter ligger i SQL/JSON Schema/YAML og valideres i CI | Reduserer drift mellom plan, kode og tester | Krever codegen/contract checks | `0B` | `PROPOSED` | `PENDING` |  |  |
| `ADR-027` | Windows-argumentserialisering og sikker systemprogram-resolusjon | Unngår PATH-hijacking, quotingfeil og avvik mellom Python-argumenter og Windows-parsing | Én kanonisk argumentbygger og Windows round-trip-tester kreves | `0A.5` | `PROPOSED` | `PENDING` |  |  |
| `ADR-028` | Reproduserbar Python/PySide6-pakkestrategi for Windows | Gir en reproducerbar, offline-brukbar Windows-build med kontrollert Python/Qt-kompatibilitet | Toolchain låses etter 0A.5 og smoke-testes på ren Windows-VM | `0A.5` | `PROPOSED` | `PENDING` |  |  |

Hver ADR skal få en egen fil under `docs/adr/` når bevisarbeidet starter. Endring av en eiergodkjent beslutning krever ny ADR, berørte krav-ID-er og migrasjons-/testplan.
