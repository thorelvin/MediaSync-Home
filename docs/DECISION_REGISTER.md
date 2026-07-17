# Beslutningsregister

> **GENERERT FIL — IKKE REDIGER DIREKTE.** Oppdater [`adr/catalog.yaml`](adr/catalog.yaml) og kjør `python tools/build_adr_docs.py`.

| ADR | Tema | Bevispakke | Evidensstatus | Codex-anbefaling | Eierbeslutning | Eierdato | Erstatter |
|---|---|---|---|---|---|---|---|
| `ADR-001` | Headless Engine Host er eneste muterende tilstandseier | `0A.1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-002` | Lokale named pipes med ACL og versjonert JSON-protokoll | `0A.1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-003` | Én eller to lokale SQLite-databaser fryses etter Milepæl 0A | `0A.4` | `RECOMMENDED` | Behold to lokale SQLite-databaser med eksplisitte handoffs; kostnaden er flere recoverytilstander, men recovery.sqlite forblir liten og skrivbar når katalogvekst treffer SQLITE_FULL. | `PENDING` |  |  |
| `ADR-004` | Robocopy skriver kun til unik staging | `0A.3 + 0A.5` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-005` | Jobber, endepunkter og planer bruker uforanderlige revisjoner | `0B + Milepæl 1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-006` | OS-håndtak er leaseautoritet; DB-rad er diagnostikk | `0A.2` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-007` | Targetmutasjon bruker compare-and-swap-preconditions | `0A.3` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-008` | Outbox og desired-state reconciliation for eksterne systemer | `0B + Milepæl 1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-009` | Ingen runtime-plugins eller dynamisk kodeinnlasting i første versjon | `0B` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-010` | Live snapshot er eksplisitt best-effort, ikke VSS | `0A.3` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-011` | Catalog/recovery/filsystem koordineres som saga med durable handoffs | `0A.3 + 0A.4` | `EVIDENCE_COMPLETE` | Lokal 0A.3 og 0A.4 viser restartbare filsystem- og databasehandoffs uten skjult cross-store-atomisitet. | `PENDING` |  |  |
| `ADR-012` | Target-side recoverybevis lagres som bounded immutable intentsegmenter | `0A.3` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-013` | Alle runtimeprosesser kjører unelevated; transferchild opprettes suspended og contained før resume | `0A.1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-014` | Toveisbaseline bindes til immutable baselinekontekst | `Milepæl 1 + Milepæl 14` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-015` | Finalmutasjon eksponeres bare gjennom capability-typede porter | `0A.3 + 0B` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-016` | Endpointleaser bruker monotone fencing tokens | `0A.2` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-017` | Idempotency- og deliverynøkler komprimeres til permanente tombstones | `0B + Milepæl 1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-018` | Catalog/recovery backup og restore bruker ett manifestert epoch-sett | `0A.4` | `EVIDENCE_COMPLETE` | 0A.4 verifiserer manifestert backup-sett og avviser blandede catalog/recovery-epoker. | `PENDING` |  |  |
| `ADR-019` | Ett skrivbart endpoint har én writer-installasjon per eierskapsepoke | `0A.2` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-020` | `.mediasync` klassifiseres før ekskludering/adoption | `0A.2` | `EVIDENCE_COMPLETE` | 0A.2 validerer alle kontrollområdeklassifikasjoner og endelig BLAKE3/JCS-markerchecksum mot draftschemaet; aksepter klassifiser-først-regelen. | `PENDING` |  |  |
| `ADR-021` | Aktive revisjoner lagres i separate head-tabeller og alle parent-scope-relasjoner har DB-constraints | `0B + Milepæl 1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-022` | Hashes bærer evidensnivå; metadatarevalidert cache er ikke nåværende innholdsbevis | `0A.3 + Milepæl 3` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-023` | Kilden bindes med `SourceReadGuard` eller post-transfer current hash | `0A.3` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-024` | Kontrollområdet bruker korte objektstier med manifester | `0A.3` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-025` | Live claims bruker monoton klokke; persistent UTC er diagnostikk | `0B + Milepæl 1` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-026` | Eksakte kontrakter ligger i SQL/JSON Schema/YAML og valideres i CI | `0B` | `PROPOSED` |  | `PENDING` |  |  |
| `ADR-027` | Windows-argumentserialisering og sikker systemprogram-resolusjon | `0A.5` | `EVIDENCE_COMPLETE` | Behold én kanonisk Windows argv-builder og GetSystemDirectoryW-basert Robocopy-resolver; lokal round-trip og flaggvalidering er bevist i 0A.5. | `PENDING` |  |  |
| `ADR-028` | Reproduserbar Python/PySide6-pakkestrategi for Windows | `0A.5` | `BLOCKED` | Pakkestrategi kan ikke anbefales for release før signeringssertifikat/-policy og ren Windows-VM er tilgjengelig og smoke-testet; minimal PySide6/BLAKE3/Nuitka-runtime, lokal Nuitka standalone exe-smoke og SDK/signing-tool inventory er bevist. | `PENDING` |  |  |

## Statusmodell

Codex kan endre `evidence_status` til `EVIDENCE_COMPLETE`, `RECOMMENDED` eller `BLOCKED`, og kan fylle `codex_recommendation`. Bare prosjekteieren kan endre `owner_decision` fra `PENDING` til `OWNER_ACCEPTED`, `REJECTED`, `DEFERRED_WITH_SCOPE_REDUCTION` eller `SUPERSEDED`.

Ingen kontrakt kan få status `frozen` før alle styrende ADR-er har `owner_decision = OWNER_ACCEPTED`.
