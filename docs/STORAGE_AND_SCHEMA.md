# Lagring og datamodell


> **Status:** Kanonisk fagfil for MediaSync Home v2.9.2. `MASTER_SPEC.md` genereres fra fagfilene og skal ikke redigeres direkte. Ved konflikt gjelder presedensen i `AGENTS.md`.


SQLite-eierskap, revisjoner, parent-scope-integritet, recoverydata, retention, backup/restore og lokal kapasitetsstyring.


## 11. Datamodell

Dette kapittelet beskriver **kandidatmodellen med to lokale SQLite-databaser** fordi den er den strengeste modellen å spesifisere. 0A.4 skal måle alternativene; 0A.6 skal formulere anbefalingen, og bare prosjekteieren kan godkjenne ADR-003. Dersom én database velges, beholdes de samme logiske grensene, tabellinvariantene, durabilityklassene og recoverykravene, men cross-store-handoffs/paired backup-sett erstattes av én lokal transaksjons-/backupmodell.

Kandidatmodellens ansvar:

- `catalog.sqlite` — konfigurasjonsrevisjoner, snapshots, planer, read models, historikk, deduplisering, kommandoidempotens og outbox;
- `recovery.sqlite` — aktive Engine Host-instanser, leases, commitintensjoner og append-only recoveryhendelser.

Alle valgte state stores ligger under lokal brukerdatafolder med begrenset ACL. De skal aldri plasseres på NAS, synkroniseres som brukerdata eller åpnes skrivbart av GUI-prosessen.

### 11.0 Generelle skjemaregler

Alle migrasjoner og tabeller følger disse reglene:

- `PRAGMA foreign_keys=ON` på hver forbindelse;
- bruk SQLite `STRICT`-tabeller der alle nødvendige datatyper kan uttrykkes uten å svekke portabiliteten;
- alle fremmednøkler, `NOT NULL`, `CHECK` og unike constraints uttrykkes eksplisitt;
- audit-, plan-, snapshot-, revisjons- og recoveryrader bruker `ON DELETE RESTRICT`; de slettes bare av en eksplisitt retentionjobb etter referansesjekk;
- composite-key-tabeller kan bruke `WITHOUT ROWID` når benchmark og query plan viser fordel;
- mutable aggregater har `row_version INTEGER NOT NULL` for optimistic concurrency;
- alle UTC-tidspunkter lagres i én validert RFC 3339-form med `Z`; varighet og timeout bruker monotonic time i prosessen og lagres som beregnede millisekunder, ikke som veggklokkeavhengig deadline;
- JSON brukes bare for kalde, versjonerte payloads. Felt som inngår i join, filter, sortering, invariant eller sikkerhetsbeslutning skal være egne kolonner;
- enums har `CHECK` eller valideres gjennom versjonert kode + migrasjon; ukjente sikkerhetskritiske enumverdier blokkerer oppstart;
- hver migrasjon har sekvensnummer, navn og SHA-256/BLAKE3-checksum. Endret historisk migrasjonsfil er en fatal integritetsfeil;
- plan-, snapshot- og revisjonstabeller har triggers eller repositoryguard som avviser `UPDATE`/`DELETE` etter forsegling;
- plan-, snapshot-, recovery- og IPCtabeller persisterer bare relative stier og IDs; absolutt root finnes bare i endpointrevisjonen og løses gjennom `SafePath`;
- historiske revisjonsclaims er auditdata. Bare eksplisitt materialiserte aktive claims for ikke-arkiverte jobber deltar i konfliktkontroll;
- ingen transaksjon dekker både SQLite-state og filsystem. Dersom separate catalog/recovery stores beholdes, er cross-store-avstemming eksplisitt og ingen handler holder write-lock i begge samtidig.


#### 11.0.1 Parent-scope-relasjonell integritet

Alle redundante IDs som brukes for sikkerhet skal enten fjernes eller bindes med sammensatte fremmednøkler. Tabellen under er bindende minimum. Når Milepæl 1 fryser databaseskjemaet valgt av ADR-003, er `schema/catalog.sql` eller den ADR-valgte samlede skjema-filen den eksakte autoriteten. Frem til da er tabellen, kanoniske krav og eiergodkjente ADR-er kandidatgrunnlaget; plassholder-SQL er uttrykkelig ikke autoritativ.

| Barn | Parent-scope som skal håndheves |
|---|---|
| `endpoint_heads` | `(endpoint_id, active_revision_id) -> endpoint_revisions(endpoint_id, id)` |
| `job_heads` | `(job_id, active_revision_id) -> job_revisions(job_id, id)` |
| `job_revisions` | `(job_id, filter_set_id) -> filter_sets(job_id, id)` og `(filter_set_id, filter_set_version) -> filter_set_versions` |
| `analyses` | `(job_id, job_revision_id) -> job_revisions(job_id, id)` |
| `analysis_targets` | `(endpoint_id, endpoint_revision_id) -> endpoint_revisions(endpoint_id, id)` |
| `snapshots` | `(analysis_id, endpoint_id) -> analysis_targets` og `(endpoint_id, endpoint_revision_id) -> endpoint_revisions` |
| `file_entries` | `(snapshot_id, endpoint_id) -> snapshots(id, endpoint_id)` |
| `case_collision_members` | `(snapshot_id, file_entry_id) -> file_entries(snapshot_id, id)` og `(snapshot_id, group_id) -> case_collision_groups` |
| `plan_endpoints` | planens `analysis_id` + endpoint + snapshot må peke til samme snapshot/endpointrevision |
| `planned_operations` | source/target snapshot, entry og endpointrevision må tilhøre samme planbinding |
| `operation_dependencies` | begge operasjoner må ha samme `plan_id` |
| `runs` | `(job_id, job_revision_id)` og planens jobb/revisjon må samsvare |
| `operation_outcomes` | run, run target og operation må tilhøre samme plan/run |
| `operation_attempts` | run attempt, run target og operation må tilhøre samme run/plan |

Migrasjonstester skal forsøke å bruke en gyldig child-ID fra feil parent og kreve `FOREIGN KEY constraint failed` eller tilsvarende eksplisitt triggeravvisning. Seal-validering er et tillegg, ikke en erstatning.

### 11.1 `catalog.sqlite`

#### `schema_migrations`

- `version INTEGER PRIMARY KEY`
- `name TEXT NOT NULL`
- `checksum TEXT NOT NULL`
- `applied_utc TEXT NOT NULL`
- `app_version TEXT NOT NULL`

#### `installation_state`

- `installation_id TEXT PRIMARY KEY`
- `product_channel TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- `last_started_app_version TEXT NOT NULL`
- `catalog_schema_version INTEGER NOT NULL`
- `recovery_schema_version INTEGER NOT NULL`
- `ipc_protocol_major INTEGER NOT NULL`
- `row_version INTEGER NOT NULL`

`installation_id` er tilfeldig og stabil for installasjonen. Den er ikke en credential. Den persisteres i den lokale, ACL-beskyttede dataroten og avledes aldri fra executable path; kopiering av en installasjonsfri binærmappe til en ny maskin oppretter ny lokal installasjonsidentitet.

#### `endpoints`

Stabil produktidentitet; aktive revisjonshoder ligger separat slik at første revisjon kan opprettes uten sirkulær FK.

- `id TEXT PRIMARY KEY`
- `display_name TEXT NOT NULL`
- `kind TEXT NOT NULL` — local, removable, mapped, smb
- `retired_utc TEXT`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- `row_version INTEGER NOT NULL`

#### `endpoint_heads`

- `endpoint_id TEXT PRIMARY KEY REFERENCES endpoints(id) ON DELETE RESTRICT`
- `active_revision_id TEXT NOT NULL`
- `activated_utc TEXT NOT NULL`
- `row_version INTEGER NOT NULL`
- sammensatt FK `(endpoint_id, active_revision_id) REFERENCES endpoint_revisions(endpoint_id, id) ON DELETE RESTRICT`

Opprettelsesrekkefølgen er: stabil endpoint-rad → første immutable revisjon → head-rad. Headbytte er compare-and-swap på `row_version` og kan aldri peke til en annen endpointidentitet.

#### `endpoint_revisions`

Uforanderlig observasjon/konfigurasjon av ett endepunkt.

- `id TEXT PRIMARY KEY`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `generation INTEGER NOT NULL`
- `configured_path TEXT NOT NULL`
- `canonical_path TEXT`
- `canonical_root_key TEXT NOT NULL`
- `root_identity_hash_algorithm TEXT`
- `root_identity_hash TEXT NOT NULL`
- `volume_guid TEXT`
- `volume_serial TEXT`
- `physical_device_key TEXT`
- `physical_device_key_confidence TEXT NOT NULL`
- `unc_server TEXT`
- `unc_share TEXT`
- `marker_uuid TEXT`
- `control_area_id TEXT`
- `control_area_state TEXT NOT NULL`
- `control_schema_version INTEGER`
- `owner_installation_id TEXT`
- `ownership_epoch INTEGER`
- `ownership_mode TEXT`
- `control_marker_checksum_algorithm TEXT`
- `control_marker_checksum TEXT`
- `filesystem_name TEXT`
- `default_case_mode TEXT NOT NULL` — endpoint-default; autoritativ katalogspesifikk state lagres i `directory_coverage`
- `comparison_key_version INTEGER NOT NULL`
- `timestamp_precision_ns INTEGER`
- `capabilities_hash TEXT NOT NULL`
- `capabilities_json TEXT NOT NULL`
- `probe_status TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- unik `(endpoint_id, generation)`
- unik `(endpoint_id, id)`

En ny sikkerhetsrelevant probe oppretter ny revisjon/generasjon; historiske planer endres ikke.

#### `endpoint_ownership_events`

Lokal audit/read model for mål-side ownership-records. Den autoritative aktive eieren ligger i validert target marker/record.

- `id TEXT PRIMARY KEY`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_revision_id TEXT NOT NULL`
- `control_area_id TEXT NOT NULL`
- `previous_owner_installation_id TEXT`
- `new_owner_installation_id TEXT NOT NULL`
- `previous_ownership_epoch INTEGER`
- `new_ownership_epoch INTEGER NOT NULL`
- `event_type TEXT NOT NULL` — register, takeover, adopt_namespace, marker_recovery
- `target_record_relative_path TEXT NOT NULL`
- `target_record_checksum TEXT NOT NULL`
- `confirmed_by_user INTEGER NOT NULL CHECK (confirmed_by_user IN (0,1))`
- `created_utc TEXT NOT NULL`
- sammensatt FK `(endpoint_id, endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`
- unik `(endpoint_id, new_ownership_epoch)`

#### `jobs`

Stabil jobbidentitet og livssyklus.

- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `enabled INTEGER NOT NULL CHECK (enabled IN (0,1))`
- `archived_utc TEXT`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- `row_version INTEGER NOT NULL`

#### `job_heads`

- `job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE RESTRICT`
- `active_revision_id TEXT NOT NULL`
- `activated_utc TEXT NOT NULL`
- `row_version INTEGER NOT NULL`
- sammensatt FK `(job_id, active_revision_id) REFERENCES job_revisions(job_id, id) ON DELETE RESTRICT`

Opprettelsesrekkefølgen er: stabil jobb → første immutable revisjon → head. Endring av aktiv revisjon og `active_root_claims` skjer i samme kritiske transaksjon.

#### `job_revisions`

Uforanderlig, kanonisk jobbkonfigurasjon.

- `id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `revision_no INTEGER NOT NULL`
- `job_type TEXT NOT NULL` — multi_target_backup, pair_sync
- `source_endpoint_id TEXT`
- `left_endpoint_id TEXT`
- `right_endpoint_id TEXT`
- `sync_mode TEXT NOT NULL`
- `filter_set_id TEXT NOT NULL`
- `filter_set_version INTEGER NOT NULL`
- `verification_level TEXT NOT NULL`
- `metadata_policy TEXT NOT NULL`
- `named_stream_policy TEXT NOT NULL`
- `conflict_policy TEXT NOT NULL`
- `quarantine_days INTEGER NOT NULL`
- `version_days INTEGER NOT NULL`
- `file_stability_seconds INTEGER NOT NULL`
- `require_plan_review INTEGER NOT NULL`
- `all_targets_must_succeed INTEGER NOT NULL`
- `execution_policy TEXT NOT NULL`
- `configuration_json TEXT NOT NULL`
- `configuration_hash TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- unik `(job_id, revision_no)`
- unik `(job_id, id)`
- unik `(job_id, configuration_hash)` når identisk revisjon ikke skal dupliseres
- sammensatt FK `(job_id, filter_set_id) REFERENCES filter_sets(job_id, id)`
- sammensatt FK `(filter_set_id, filter_set_version) REFERENCES filter_set_versions(filter_set_id, version)`

Bindende `CHECK`/domenevalidering:

```text
multi_target_backup:
    source_endpoint_id IS NOT NULL
    left_endpoint_id IS NULL
    right_endpoint_id IS NULL
    sync_mode IN (UPDATE_FORWARD, MIRROR_FORWARD)

pair_sync:
    source_endpoint_id IS NULL
    left_endpoint_id IS NOT NULL
    right_endpoint_id IS NOT NULL
    left_endpoint_id <> right_endpoint_id
    sync_mode IN (
        UPDATE_LEFT_TO_RIGHT,
        MIRROR_LEFT_TO_RIGHT,
        UPDATE_RIGHT_TO_LEFT,
        MIRROR_RIGHT_TO_LEFT,
        TWO_WAY
    )
```

#### `job_revision_targets`

Bare for `multi_target_backup`; uforanderlig sammen med jobbrevisjonen.

- `job_revision_id TEXT NOT NULL REFERENCES job_revisions(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 3)`
- `sync_mode_override TEXT`
- `enabled INTEGER NOT NULL CHECK (enabled IN (0,1))`
- primærnøkkel `(job_revision_id, endpoint_id)`
- unik `(job_revision_id, ordinal)`

#### `job_root_claims`

Uforanderlige claims som beskriver hver jobbrevisjon. Historiske rader er audit og blokkerer ikke alene fremtidig konfigurasjon.

- `id TEXT PRIMARY KEY`
- `job_revision_id TEXT NOT NULL REFERENCES job_revisions(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `role TEXT NOT NULL` — source, target, left, right
- `access_mode TEXT NOT NULL` — read, write
- `canonical_root_key TEXT NOT NULL`
- `root_depth INTEGER NOT NULL`
- `created_utc TEXT NOT NULL`
- unik `(job_revision_id, endpoint_id, role)`
- indeks `(canonical_root_key, access_mode)`

#### `job_root_claim_ancestors`

Normalisert, indeksbar ancestorstruktur. Overlap skal ikke avgjøres fra JSON eller string-prefix alene.

- `claim_id TEXT NOT NULL REFERENCES job_root_claims(id) ON DELETE RESTRICT`
- `ancestor_key TEXT NOT NULL`
- `ancestor_depth INTEGER NOT NULL`
- primærnøkkel `(claim_id, ancestor_key)`
- indeks `(ancestor_key, claim_id)`

Listen inneholder alle kanoniske ancestors innen samme endpointnamespace, inkludert claimets egen rot. Alias-/volume-/shareidentitet inngår i nøkkelen slik at `C:\x` og en junction/UNC-alias ikke antas uavhengige når Windows kan bevise samme rot.

#### `active_root_claims`

Materialisert sikkerhetsindeks for aktive, ikke-arkiverte jobber. Dette er tabellen som deltar i transactional conflict check.

- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `job_revision_id TEXT NOT NULL REFERENCES job_revisions(id) ON DELETE RESTRICT`
- `claim_id TEXT NOT NULL REFERENCES job_root_claims(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `role TEXT NOT NULL`
- `access_mode TEXT NOT NULL`
- `canonical_root_key TEXT NOT NULL`
- `activated_utc TEXT NOT NULL`
- primærnøkkel `(job_id, claim_id)`
- unik `(claim_id)`
- indeks `(canonical_root_key, access_mode)`
- indeks `(endpoint_id, access_mode)`

Aktivering eller endring av en jobb skjer i én kritisk catalogtransaksjon:

1. valider expected `jobs.row_version` og at ny revisjon er forseglet;
2. bygg/valider immutable claims og ancestorrows;
3. sammenlign både `new_root IN existing_ancestors` og `existing_root IN new_ancestors`;
4. avvis når minst én side er `write`, med mindre begge er den samme jobben/revisjonsovergangen som erstattes;
5. fjern gamle `active_root_claims`, compare-and-swap `job_heads.active_revision_id` og sett inn nye aktive claims;
6. commit `FULL` før GUI får suksess.

Flere rene read-claims på samme kilde er tillatt. Arkivering fjerner bare aktive claims og stopper triggere; historiske claims beholdes. Reaktivering re-prober endepunkter og kjører full overlapkontroll før claims aktiveres igjen.

#### `job_target_state`

Muterbar read model per jobb/mål, adskilt fra konfigurasjonsrevisjon.

- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `last_successful_run_id TEXT`
- `last_complete_analysis_id TEXT`
- `freshness_state TEXT NOT NULL`
- `attention_state TEXT NOT NULL`
- `last_seen_utc TEXT`
- `row_version INTEGER NOT NULL`
- primærnøkkel `(job_id, endpoint_id)`

#### `filter_sets`

- `id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `name TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- `retired_utc TEXT`
- unik `(job_id, name)`
- unik `(job_id, id)`

#### `filter_set_versions`

- `filter_set_id TEXT NOT NULL REFERENCES filter_sets(id) ON DELETE RESTRICT`
- `version INTEGER NOT NULL`
- `rules_hash TEXT NOT NULL`
- `rules_json TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- primærnøkkel `(filter_set_id, version)`

#### `analyses`

- `id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `job_revision_id TEXT NOT NULL REFERENCES job_revisions(id) ON DELETE RESTRICT`
- `state TEXT NOT NULL`
- `result_kind TEXT`
- `planner_version TEXT NOT NULL`
- `started_utc TEXT NOT NULL`
- `completed_utc TEXT`
- `app_version TEXT NOT NULL`
- `warning_count INTEGER NOT NULL DEFAULT 0`
- `error_count INTEGER NOT NULL DEFAULT 0`
- `row_version INTEGER NOT NULL`
- unik `(job_id, id)`
- sammensatt FK `(job_id, job_revision_id) REFERENCES job_revisions(job_id, id)`

#### `analysis_targets`

- `analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_revision_id TEXT NOT NULL REFERENCES endpoint_revisions(id) ON DELETE RESTRICT`
- `role TEXT NOT NULL`
- `state TEXT NOT NULL`
- `result_kind TEXT`
- `started_utc TEXT`
- `completed_utc TEXT`
- `warning_count INTEGER NOT NULL DEFAULT 0`
- `error_count INTEGER NOT NULL DEFAULT 0`
- primærnøkkel `(analysis_id, endpoint_id)`
- sammensatt FK `(endpoint_id, endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`

#### `snapshots`

- `id TEXT PRIMARY KEY`
- `analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_revision_id TEXT NOT NULL REFERENCES endpoint_revisions(id) ON DELETE RESTRICT`
- `endpoint_generation INTEGER NOT NULL`
- `root_identity_hash TEXT NOT NULL`
- `consistency_model TEXT NOT NULL` — LIVE_BEST_EFFORT i første versjon
- `snapshot_schema_version INTEGER NOT NULL`
- `status TEXT NOT NULL`
- `started_utc TEXT NOT NULL`
- `completed_utc TEXT`
- `entry_count INTEGER NOT NULL DEFAULT 0`
- `total_bytes INTEGER NOT NULL DEFAULT 0`
- `scan_error_count INTEGER NOT NULL DEFAULT 0`
- `volatile_directory_count INTEGER NOT NULL DEFAULT 0`
- `scan_generation INTEGER NOT NULL`
- `scan_duration_ms INTEGER`
- `metadata_cache_hits INTEGER NOT NULL DEFAULT 0`
- `complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0,1))`
- `immutable INTEGER NOT NULL DEFAULT 0 CHECK (immutable IN (0,1))`
- `sealed_utc TEXT`
- `checksum_algorithm TEXT`
- `snapshot_checksum TEXT`
- unik `(analysis_id, endpoint_id)`
- unik `(id, endpoint_id)`
- unik `(analysis_id, endpoint_id, id)`
- sammensatt FK `(analysis_id, endpoint_id) REFERENCES analysis_targets(analysis_id, endpoint_id)`
- sammensatt FK `(endpoint_id, endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`

Et snapshot blir immutable i én kritisk sealtransaksjon etter at alle batchreceipts, entries, coverage, issues, tellinger og checksum er validert. Etter `immutable=1` kan ingen `file_entries`, `directory_coverage`, `snapshot_issues`, kollisjonsrader eller summer oppdateres. Sen hash/metadata lagres i cache eller en eksplisitt avledet artefakt.

#### `snapshot_batches`

Idempotent inbox for strømmet skanneinnlasting.

- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `sequence_no INTEGER NOT NULL`
- `payload_hash TEXT NOT NULL`
- `entry_count INTEGER NOT NULL`
- `coverage_update_count INTEGER NOT NULL`
- `issue_count INTEGER NOT NULL`
- `approximate_bytes INTEGER NOT NULL`
- `state TEXT NOT NULL` — received, committed
- `committed_utc TEXT`
- primærnøkkel `(snapshot_id, sequence_no)`

Writeren setter batchreceipt og batchens entries/coverage/issues i samme katalogtransaksjon. Identisk retry returnerer eksisterende commit; samme `sequence_no` med annen hash er `SNAPSHOT_BATCH_CONFLICT` og blokkerer snapshotseal.

#### `snapshot_issues`

Dekker feil som ikke kan representeres som en vanlig filpost.

- `id INTEGER PRIMARY KEY`
- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `relative_path TEXT NOT NULL`
- `issue_type TEXT NOT NULL`
- `error_code TEXT`
- `sanitized_message TEXT`
- `blocks_destructive_actions INTEGER NOT NULL`
- `observed_utc TEXT NOT NULL`
- indeks `(snapshot_id, relative_path)`

#### `directory_coverage`

- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `relative_path TEXT NOT NULL`
- `comparison_key TEXT NOT NULL`
- `coverage_state TEXT NOT NULL` — complete, unreadable, disappeared, volatile, cancelled
- `case_mode TEXT NOT NULL`
- `case_mode_evidence TEXT NOT NULL`
- `case_context_hash TEXT NOT NULL`
- `case_probe_error TEXT`
- `identity_before_json TEXT`
- `identity_after_json TEXT`
- `enumerated_start_utc TEXT`
- `enumerated_end_utc TEXT`
- primærnøkkel `(snapshot_id, relative_path)`
- indeks `(snapshot_id, comparison_key, coverage_state)`

En fraværspåstand kan bare bygges innen dokumentert coverage. Global `complete=1` krever at alle inkluderte kataloger har terminal, akseptabel coverage og at feilstatistikken er committet.

#### `file_entries`

- `id INTEGER PRIMARY KEY`
- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `relative_path TEXT NOT NULL`
- `comparison_key TEXT NOT NULL`
- `comparison_key_version INTEGER NOT NULL`
- `parent_key TEXT NOT NULL`
- `parent_case_context_hash TEXT NOT NULL`
- `name TEXT NOT NULL`
- `path_depth INTEGER NOT NULL`
- `entry_type TEXT NOT NULL`
- `size_bytes INTEGER`
- `mtime_ns INTEGER`
- `birthtime_ns INTEGER`
- `metadata_change_time_ns INTEGER`
- `attributes INTEGER`
- `volume_identity TEXT`
- `file_id TEXT`
- `file_id_reliability TEXT NOT NULL DEFAULT 'hint'`
- `link_count INTEGER`
- `reparse_tag INTEGER`
- `quick_hash TEXT`
- `full_hash TEXT`
- `hash_algorithm TEXT`
- `named_stream_count INTEGER`
- `named_stream_bytes INTEGER`
- `scan_error_code TEXT`
- `scan_error_message TEXT`

Indekser/constraints:

- unik `(snapshot_id, relative_path)` med eksakt/binary tekstsemantikk;
- unik `(snapshot_id, id)`;
- sammensatt FK `(snapshot_id, endpoint_id) REFERENCES snapshots(id, endpoint_id)`;
- ikke-unik `(snapshot_id, comparison_key)`;
- covering `(snapshot_id, comparison_key, entry_type, size_bytes, mtime_ns, file_id)`;
- `(snapshot_id, parent_key, entry_type)`;
- `(size_bytes, quick_hash)`;
- partial `(full_hash, size_bytes)` der `full_hash IS NOT NULL`.

Case-kollisjon skal aldri avbryte innsetting. `birthtime_ns` er Windows-opprettelsestid når semantikken er kjent; `ctime_ns` brukes ikke som erstatning. Hashfelt kan fylles mens snapshotet fortsatt er et kontrollert utkast, men fryses ved seal. Hash som blir tilgjengelig senere skrives til `hash_cache`/avledet planinput og kan ikke mutere den historiske filposten.

#### `case_collision_groups`

- `id TEXT PRIMARY KEY`
- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `comparison_key TEXT NOT NULL`
- `member_count INTEGER NOT NULL`
- `case_mode TEXT NOT NULL`
- `blocking INTEGER NOT NULL`
- unik `(snapshot_id, comparison_key)`
- unik `(snapshot_id, id)`

#### `case_collision_members`

- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `group_id TEXT NOT NULL`
- `file_entry_id INTEGER NOT NULL`
- primærnøkkel `(group_id, file_entry_id)`
- sammensatt FK `(snapshot_id, group_id) REFERENCES case_collision_groups(snapshot_id, id)`
- sammensatt FK `(snapshot_id, file_entry_id) REFERENCES file_entries(snapshot_id, id)`

#### `baseline_sets`

Et baseline-sett representerer én immutable semantisk kontekst for `pair_sync`. Det kan ikke gjenbrukes bare fordi `job_id` er likt.

- `id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `left_endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `right_endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `left_root_identity_hash TEXT NOT NULL`
- `right_root_identity_hash TEXT NOT NULL`
- `context_schema_version INTEGER NOT NULL`
- `comparison_key_version INTEGER NOT NULL`
- `filter_rules_hash TEXT NOT NULL`
- `sync_semantics_hash TEXT NOT NULL`
- `context_hash TEXT NOT NULL`
- `generation INTEGER NOT NULL`
- `state TEXT NOT NULL` — building, active, retired, invalidated
- `created_from_run_id TEXT`
- `created_utc TEXT NOT NULL`
- `retired_utc TEXT`
- unik `(job_id, context_hash, generation)`
- indeks `(job_id, state, generation)`

`context_hash` beregnes kanonisk fra røtter, endpointroller, sammenlignings-/filter-/metadata-/konfliktsemantikk og relevante schema-/planner-versjoner. En ny jobbrevisjon kan peke til et eksisterende aktivt sett bare når en eksplisitt ekvivalensfunksjon dokumenterer at alle baselinepåvirkende felt er identiske.

#### `baselines`

- `baseline_set_id TEXT NOT NULL REFERENCES baseline_sets(id) ON DELETE RESTRICT`
- `comparison_key TEXT NOT NULL`
- `comparison_key_version INTEGER NOT NULL`
- `left_relative_path TEXT`
- `right_relative_path TEXT`
- `left_fingerprint_json TEXT`
- `right_fingerprint_json TEXT`
- `left_tombstone INTEGER NOT NULL DEFAULT 0`
- `right_tombstone INTEGER NOT NULL DEFAULT 0`
- `resolved_content_hash TEXT`
- `state TEXT NOT NULL`
- `baseline_generation INTEGER NOT NULL`
- `last_resolved_run_id TEXT`
- `updated_utc TEXT NOT NULL`
- primærnøkkel `(baseline_set_id, comparison_key_version, comparison_key)`

Baseline oppdateres bare for operasjoner hvis begge sider og avhengigheter har terminalt, verifisert resultat. Delvis kjøring kan ikke skrive falsk global baseline. Oppdatering bruker expected `baseline_generation`; stale writer får konflikt i stedet for last-write-wins.

#### `plans`

- `id TEXT PRIMARY KEY`
- `analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `job_revision_id TEXT NOT NULL REFERENCES job_revisions(id) ON DELETE RESTRICT`
- `parent_plan_id TEXT REFERENCES plans(id) ON DELETE RESTRICT`
- `planner_version TEXT NOT NULL`
- `plan_schema_version INTEGER NOT NULL`
- `operation_schema_version INTEGER NOT NULL`
- `execution_policy TEXT NOT NULL`
- `execution_policy_hash TEXT NOT NULL`
- `baseline_set_id TEXT REFERENCES baseline_sets(id) ON DELETE RESTRICT`
- `baseline_context_hash TEXT`
- `baseline_generation INTEGER`
- `checksum_algorithm TEXT NOT NULL`
- `plan_checksum TEXT NOT NULL`
- `risk_summary_json TEXT NOT NULL`
- `created_utc TEXT NOT NULL`
- `approved_utc TEXT`
- `sealed_utc TEXT`
- `immutable INTEGER NOT NULL DEFAULT 0 CHECK (immutable IN (0,1))`
- `operation_count INTEGER NOT NULL DEFAULT 0`
- `planned_bytes INTEGER NOT NULL DEFAULT 0`
- `build_duration_ms INTEGER`
- unik `(id, analysis_id)`
- unik `(id, job_id, job_revision_id)`
- sammensatt FK `(job_id, job_revision_id) REFERENCES job_revisions(job_id, id)`

En forseglet plan og alle dens rader er uforanderlige. Brukeroverstyring lager en ny plan med `parent_plan_id` og ny checksum. En toveisplan binder eksakt `baseline_set_id`, `baseline_context_hash` og generasjon. Første ikke-destruktive etableringsplan uten tidligere baseline lagrer eksplisitt context hash og `baseline_generation=0`; den kan ikke tolkes som en tom, autoritativ baseline.

#### `plan_endpoints`

- `plan_id TEXT NOT NULL`
- `analysis_id TEXT NOT NULL`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_revision_id TEXT NOT NULL`
- `snapshot_id TEXT NOT NULL`
- `role TEXT NOT NULL`
- `target_ordinal INTEGER`
- `capabilities_hash TEXT NOT NULL`
- `root_case_context_hash TEXT NOT NULL`
- `required_owner_installation_id TEXT`
- `required_ownership_epoch INTEGER`
- `control_schema_version INTEGER`
- primærnøkkel `(plan_id, endpoint_id, role)`
- unik `(plan_id, endpoint_id)`
- unik `(plan_id, snapshot_id)`
- sammensatt FK `(plan_id, analysis_id) REFERENCES plans(id, analysis_id)`
- sammensatt FK `(endpoint_id, endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`
- sammensatt FK `(analysis_id, endpoint_id, snapshot_id) REFERENCES snapshots(analysis_id, endpoint_id, id)`
- `CHECK` som krever owner/epoch/control schema for roller som kan muteres, og krever dem `NULL` for rene read-only roller der dette ikke er relevant

Planbindingen fryser hva analysen trodde om endepunktet. En ny owner, ownership epoch, kontrollschema, capabilities hash eller root case context gjør planen inkompatibel; executor skal ikke oppdatere disse feltene i en gammel plan.

#### `plan_target_summaries`

- `plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT`
- `target_endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `operation_count INTEGER NOT NULL`
- `planned_bytes INTEGER NOT NULL`
- `risk_level TEXT NOT NULL`
- `summary_json TEXT NOT NULL`
- primærnøkkel `(plan_id, target_endpoint_id)`

#### `planned_operations`

- `id TEXT PRIMARY KEY`
- `plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT`
- `source_endpoint_id TEXT REFERENCES endpoints(id) ON DELETE RESTRICT`
- `source_endpoint_revision_id TEXT REFERENCES endpoint_revisions(id) ON DELETE RESTRICT`
- `source_snapshot_id TEXT REFERENCES snapshots(id) ON DELETE RESTRICT`
- `affected_endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `target_endpoint_revision_id TEXT NOT NULL REFERENCES endpoint_revisions(id) ON DELETE RESTRICT`
- `target_snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `source_file_entry_id INTEGER REFERENCES file_entries(id) ON DELETE RESTRICT`
- `target_file_entry_id INTEGER REFERENCES file_entries(id) ON DELETE RESTRICT`
- `sequence_no INTEGER NOT NULL`
- `execution_phase INTEGER NOT NULL`
- `operation_type TEXT NOT NULL`
- `deferred_operation_type TEXT`
- `source_relative_path TEXT`
- `target_relative_path TEXT`
- `source_parent_key TEXT`
- `target_parent_key TEXT`
- `path_depth INTEGER NOT NULL DEFAULT 0`
- `batch_key TEXT`
- `stable_order_key TEXT NOT NULL`
- `priority INTEGER NOT NULL DEFAULT 100`
- `expected_size_bytes INTEGER`
- `expected_source_fingerprint_json TEXT`
- `expected_source_parent_identity_json TEXT`
- `expected_source_path_chain_hash TEXT`
- `expected_source_case_context_hash TEXT`
- `source_guard_policy TEXT NOT NULL`
- `required_source_assurance TEXT NOT NULL`
- `target_precondition_kind TEXT NOT NULL` — ABSENT, MATCH_FINGERPRINT, DIRECTORY_EMPTY, NONE
- `expected_target_fingerprint_json TEXT`
- `expected_target_parent_identity_json TEXT`
- `expected_target_path_chain_hash TEXT`
- `expected_target_case_context_hash TEXT`
- `required_capabilities_hash TEXT NOT NULL`
- `required_owner_installation_id TEXT`
- `required_ownership_epoch INTEGER`
- `required_lease_resource_key TEXT NOT NULL`
- `reason_code TEXT NOT NULL`
- `risk_level TEXT NOT NULL`
- `decision_origin TEXT NOT NULL` — planner, user_derived, automation_policy
- `planning_state TEXT NOT NULL`
- `name_allocation_schema_version INTEGER`
- `name_allocation_hash TEXT`
- `allocated_target_relative_path TEXT`
- `managed_object_role TEXT`
- unik `(plan_id, sequence_no)`
- unik `(plan_id, id)`
- sammensatt FK `(plan_id, source_endpoint_id) REFERENCES plan_endpoints(plan_id, endpoint_id)` når source er satt
- sammensatt FK `(source_endpoint_id, source_endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)` når source er satt
- sammensatt FK `(plan_id, source_snapshot_id) REFERENCES plan_endpoints(plan_id, snapshot_id)` når source er satt
- sammensatt FK `(source_snapshot_id, source_file_entry_id) REFERENCES file_entries(snapshot_id, id)` når source entry er satt
- sammensatt FK `(plan_id, affected_endpoint_id) REFERENCES plan_endpoints(plan_id, endpoint_id)`
- sammensatt FK `(affected_endpoint_id, target_endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`
- sammensatt FK `(plan_id, target_snapshot_id) REFERENCES plan_endpoints(plan_id, snapshot_id)`
- sammensatt FK `(target_snapshot_id, target_file_entry_id) REFERENCES file_entries(snapshot_id, id)` når target entry er satt
- indeks `(plan_id, affected_endpoint_id, execution_phase, stable_order_key)`

SQLite håndhever ikke en nullable sammensatt FK dersom én kolonne er `NULL`. `schema/catalog.sql` skal derfor kombinere FKs med eksplisitte all-or-none-`CHECK`-constraints:

```text
source tuple:
    enten alle påkrevde source IDs er NULL for en operation uten source,
    eller endpoint/revision/snapshot/path og relevante entryfelt er konsistente og ikke-NULL

writable target tuple:
    owner_installation_id, ownership_epoch, capabilities_hash,
    lease_resource_key og konkret target_precondition er påkrevd
```

Muterende operasjoner skal alltid ha en eksplisitt target-precondition. `NONE` er bare lovlig for rene skip-/defer-/diagnostikkrader. Konfliktnavn og andre alternative målrelative navn ligger i `allocated_target_relative_path` før seal; execution genererer dem aldri på nytt.

#### `operation_dependencies`

- `plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT`
- `operation_id TEXT NOT NULL`
- `depends_on_operation_id TEXT NOT NULL`
- primærnøkkel `(plan_id, operation_id, depends_on_operation_id)`
- sammensatt FK `(plan_id, operation_id) REFERENCES planned_operations(plan_id, id)`
- sammensatt FK `(plan_id, depends_on_operation_id) REFERENCES planned_operations(plan_id, id)`
- `CHECK (operation_id <> depends_on_operation_id)`

Bruk eksplisitte avhengigheter bare når fase/dybde ikke er nok. Seal-valideringen må avvise syklus.

#### `runs`

- `id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `job_revision_id TEXT NOT NULL REFERENCES job_revisions(id) ON DELETE RESTRICT`
- `plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT`
- `command_receipt_id TEXT`
- `trigger_occurrence_id TEXT`
- `logical_run_group_id TEXT NOT NULL`
- `resumed_from_run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT`
- `trigger_type TEXT NOT NULL`
- `state TEXT NOT NULL`
- `started_utc TEXT NOT NULL`
- `finished_utc TEXT`
- `summary_json TEXT`
- `warning_count INTEGER NOT NULL DEFAULT 0`
- `error_count INTEGER NOT NULL DEFAULT 0`
- `app_version TEXT NOT NULL`
- `row_version INTEGER NOT NULL`
- unik `(id, plan_id)`
- unik `(id, job_id, job_revision_id)`
- sammensatt FK `(job_id, job_revision_id) REFERENCES job_revisions(job_id, id)`
- sammensatt FK `(plan_id, job_id, job_revision_id) REFERENCES plans(id, job_id, job_revision_id)`

#### `run_targets`

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_revision_id TEXT NOT NULL REFERENCES endpoint_revisions(id) ON DELETE RESTRICT`
- `required_owner_installation_id TEXT`
- `required_ownership_epoch INTEGER`
- `state TEXT NOT NULL`
- `lease_resource_key TEXT`
- `last_lease_id TEXT`
- `last_ownership_epoch INTEGER`
- `last_fencing_token INTEGER`
- `started_utc TEXT`
- `finished_utc TEXT`
- `planned_operations INTEGER NOT NULL DEFAULT 0`
- `completed_operations INTEGER NOT NULL DEFAULT 0`
- `planned_bytes INTEGER NOT NULL DEFAULT 0`
- `completed_bytes INTEGER NOT NULL DEFAULT 0`
- `warning_count INTEGER NOT NULL DEFAULT 0`
- `error_count INTEGER NOT NULL DEFAULT 0`
- `result_json TEXT`
- `row_version INTEGER NOT NULL`
- unik `(run_id, endpoint_id)`
- unik `(run_id, id)`
- sammensatt FK `(endpoint_id, endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`

`required_*` kommer fra planen. `last_*` er diagnostisk read model; autorisasjon kommer fra levende endpointlock, validert målmarkør og matching recoverylease. Et ownership-epokebytte gjør run-target stale og krever ny plan.

#### `run_attempts`

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT`
- `attempt_number INTEGER NOT NULL`
- `process_instance_id TEXT NOT NULL`
- `started_utc TEXT NOT NULL`
- `finished_utc TEXT`
- `termination_reason TEXT`
- unik `(run_id, attempt_number)`
- unik `(id, run_id)`

#### `operation_outcomes`

- `run_id TEXT NOT NULL`
- `plan_id TEXT NOT NULL`
- `run_target_id TEXT NOT NULL`
- `operation_id TEXT NOT NULL`
- `final_state TEXT NOT NULL`
- `bytes_transferred INTEGER NOT NULL DEFAULT 0`
- `transfer_state TEXT NOT NULL`
- `assurance_level TEXT NOT NULL`
- `hash_evidence_kind TEXT`
- `durability_level TEXT NOT NULL`
- `verification_json TEXT`
- `error_code TEXT`
- `error_message TEXT`
- `completed_utc TEXT`
- primærnøkkel `(run_id, operation_id)`
- sammensatt FK `(run_id, plan_id) REFERENCES runs(id, plan_id)`
- sammensatt FK `(run_id, run_target_id) REFERENCES run_targets(run_id, id)`
- sammensatt FK `(plan_id, operation_id) REFERENCES planned_operations(plan_id, id)`

#### `operation_attempts`

- `id TEXT PRIMARY KEY`
- `run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE RESTRICT`
- `run_id TEXT NOT NULL`
- `plan_id TEXT NOT NULL`
- `run_target_id TEXT NOT NULL`
- `operation_id TEXT NOT NULL`
- `attempt_number INTEGER NOT NULL`
- `state TEXT NOT NULL`
- `batch_id TEXT`
- `lease_id TEXT`
- `ownership_epoch INTEGER`
- `fencing_token INTEGER`
- `source_guard_kind TEXT`
- `source_guard_evidence_hash TEXT`
- `transfer_state TEXT`
- `assurance_level TEXT`
- `durability_level TEXT`
- `started_utc TEXT`
- `finished_utc TEXT`
- `bytes_transferred INTEGER NOT NULL DEFAULT 0`
- `duration_ms INTEGER`
- `robocopy_exit_code INTEGER`
- `verification_json TEXT`
- `error_code TEXT`
- `error_message TEXT`
- unik `(run_id, operation_id, attempt_number)`
- sammensatt FK `(run_attempt_id, run_id) REFERENCES run_attempts(id, run_id)`
- sammensatt FK `(run_id, plan_id) REFERENCES runs(id, plan_id)`
- sammensatt FK `(run_id, run_target_id) REFERENCES run_targets(run_id, id)`
- sammensatt FK `(plan_id, operation_id) REFERENCES planned_operations(plan_id, id)`

#### `hash_cache`

- `id INTEGER PRIMARY KEY`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_generation INTEGER NOT NULL`
- `volume_identity TEXT`
- `relative_path TEXT NOT NULL`
- `comparison_key TEXT NOT NULL`
- `comparison_key_version INTEGER NOT NULL`
- `parent_case_context_hash TEXT NOT NULL`
- `entry_type TEXT NOT NULL`
- `size_bytes INTEGER NOT NULL`
- `mtime_ns INTEGER NOT NULL`
- `birthtime_ns INTEGER`
- `attributes INTEGER`
- `reparse_tag INTEGER`
- `file_id TEXT`
- `file_id_reliability TEXT NOT NULL`
- `link_count INTEGER`
- `quick_hash TEXT`
- `full_hash TEXT`
- `algorithm TEXT NOT NULL`
- `evidence_kind TEXT NOT NULL`
- `hash_schema_version INTEGER NOT NULL`
- `signature_schema_version INTEGER`
- `read_started_fingerprint_hash TEXT`
- `read_completed_fingerprint_hash TEXT`
- `usn_journal_id TEXT`
- `usn_first_record TEXT`
- `usn_last_record TEXT`
- `evidence_generation INTEGER NOT NULL`
- `active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))`
- `computed_utc TEXT NOT NULL`
- normaliserte non-null cachekeykolonner for nullable birthtime/file-ID; ikke stol på ad hoc `COALESCE` i applikasjonskode
- unik aktiv cacheidentitet over endpoint/generasjon/sti/type/størrelse/tid/birthtime/file-ID/algorithm/schema/evidence generation
- indeks `(endpoint_id, endpoint_generation, comparison_key, comparison_key_version, size_bytes, mtime_ns)`
- indeks `(full_hash, size_bytes, evidence_kind)` der `full_hash IS NOT NULL`

Fil-ID er et hint. Cachegjenbruk krever identitetskombinasjonen og evidensreglene i §6/§13.8. `METADATA_REVALIDATED_CACHED_HASH` kan ikke oppgraderes til `CURRENT_READ_HASH` uten ny full lesing. Hurtigsignaturer med ulike `signature_schema_version` er ikke sammenlignbare. En transaksjon som aktiverer en ny post deaktiverer den gamle aktive posten for samme logiske cacheidentitet; konkurrerende beregninger løses deterministisk etter evidensstyrke og generation, ikke last-write-wins.

#### `duplicate_groups`

- `id TEXT PRIMARY KEY`
- `analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT`
- `full_hash TEXT NOT NULL`
- `size_bytes INTEGER NOT NULL`
- `member_count INTEGER NOT NULL`
- `relationship_class TEXT NOT NULL`
- `potential_savings_bytes INTEGER NOT NULL`
- `review_state TEXT NOT NULL`

#### `duplicate_members`

- `group_id TEXT NOT NULL REFERENCES duplicate_groups(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `relative_path TEXT NOT NULL`
- `member_role TEXT NOT NULL`
- primærnøkkel `(group_id, endpoint_id, relative_path)`

#### `file_object_alias_groups`

Klassifiserer flere snapshotstier som peker til samme underliggende filobjekt. Dette er ikke et innholdsduplikat og gir normalt ingen mulig lagringsbesparelse.

- `id TEXT PRIMARY KEY`
- `snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `volume_identity TEXT NOT NULL`
- `file_id TEXT NOT NULL`
- `file_id_reliability TEXT NOT NULL`
- `reported_link_count INTEGER`
- `member_count INTEGER NOT NULL`
- `classification_state TEXT NOT NULL`
- unik `(snapshot_id, volume_identity, file_id)`
- unik `(snapshot_id, id)`

#### `file_object_alias_members`

- `snapshot_id TEXT NOT NULL`
- `group_id TEXT NOT NULL`
- `file_entry_id INTEGER NOT NULL`
- primærnøkkel `(group_id, file_entry_id)`
- sammensatt FK `(snapshot_id, group_id) REFERENCES file_object_alias_groups(snapshot_id, id)`
- sammensatt FK `(snapshot_id, file_entry_id) REFERENCES file_entries(snapshot_id, id)`

Aliasgrupper er endpointlokale. De brukes aldri som identitetsbevis mellom to endepunkter, og MediaSync forsøker ikke å gjenskape hardlinktopologi på backupmålet som standard.

#### `managed_objects`

Read model/audit for objectbaserte versions-/quarantine-/recoveryartefakter etter catalog-handoff. Fysisk manifest på målet og recoveryjournal er autoritative under aktiv overgang.

- `id TEXT PRIMARY KEY`
- `endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT`
- `endpoint_revision_id TEXT NOT NULL`
- `installation_id TEXT NOT NULL`
- `ownership_epoch INTEGER NOT NULL`
- `object_role TEXT NOT NULL` — staging, version, quarantine, recovery_export
- `object_relative_path TEXT NOT NULL`
- `manifest_relative_path TEXT NOT NULL`
- `original_relative_path TEXT NOT NULL`
- `run_id TEXT`
- `operation_id TEXT`
- `size_bytes INTEGER`
- `content_hash TEXT`
- `state TEXT NOT NULL`
- `retention_until_utc TEXT`
- `created_utc TEXT NOT NULL`
- sammensatt FK `(endpoint_id, endpoint_revision_id) REFERENCES endpoint_revisions(endpoint_id, id)`
- unik `(endpoint_id, installation_id, object_relative_path)`
- unik `(endpoint_id, installation_id, manifest_relative_path)`

Fysisk path skal være kort og ID-basert. `original_relative_path` er metadata og brukes ikke til å konstruere kontrollstien.

#### `run_metrics`

- `run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE RESTRICT`
- `phase_timings_json TEXT NOT NULL`
- `scan_entries_per_second REAL`
- `plan_rows_per_second REAL`
- `hash_bytes_per_second REAL`
- `copy_bytes_per_second REAL`
- `robocopy_process_count INTEGER NOT NULL DEFAULT 0`
- `peak_rss_bytes INTEGER`
- `max_queue_depths_json TEXT`
- `cache_summary_json TEXT`
- `ipc_summary_json TEXT`
- `lease_summary_json TEXT`

#### `schedules`

Lagrer ønsket Task Scheduler-tilstand.

- `id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `plan_id TEXT NOT NULL REFERENCES plan_seal_details(plan_id) ON DELETE RESTRICT` — 0B active sealed-plan binding
- `plan_checksum TEXT NOT NULL` — 0B run-start checksum binding
- `trigger_type TEXT NOT NULL`
- `configuration_json TEXT NOT NULL`
- `definition_generation INTEGER NOT NULL`
- `desired_definition_hash TEXT NOT NULL`
- `time_zone_id TEXT`
- `dst_policy TEXT NOT NULL`
- `misfire_policy TEXT NOT NULL`
- `coalescing_window_seconds INTEGER NOT NULL`
- `task_logon_type TEXT NOT NULL`
- `requires_network INTEGER NOT NULL`
- `run_only_when_logged_on INTEGER NOT NULL`
- `enabled INTEGER NOT NULL`
- `row_version INTEGER NOT NULL`
- `last_triggered_utc TEXT`

#### `external_resource_state`

- `resource_type TEXT NOT NULL` — task_scheduler, notification_channel, control_marker
- `resource_id TEXT NOT NULL`
- `desired_generation INTEGER NOT NULL`
- `desired_hash TEXT NOT NULL`
- `observed_generation INTEGER`
- `observed_hash TEXT`
- `state TEXT NOT NULL`
- `claim_owner_instance_id TEXT`
- `claim_generation INTEGER NOT NULL DEFAULT 0`
- `claim_token TEXT`
- `claim_started_utc TEXT`
- `claim_ttl_ms INTEGER`
- `last_attempt_utc TEXT`
- `last_success_utc TEXT`
- `last_error_code TEXT`
- `attempt_count INTEGER NOT NULL DEFAULT 0`
- `row_version INTEGER NOT NULL`
- primærnøkkel `(resource_type, resource_id)`

Reconciliation fullfører med compare-and-swap på `desired_generation` + `claim_token`. Et sent adapterresultat fra eldre generasjon kan aldri markere nyere ønsket tilstand som observert.

En levende owner binder claimen til en in-memory monoton deadline fra samme runtimeklokke som startet claimen. Dersom deadlinen nås under ekstern avstemming, kan resultatet ikke fullføre den gamle tokenen; owner/generation/token invalideres i stedet med en kort compare-and-swap-requeue. `claim_started_utc` og `claim_ttl_ms` er bare audit- og startupdata. Etter restart kreves separat bevis for at forrige owner ikke lever.

#### `trigger_occurrences`

- `id TEXT PRIMARY KEY`
- `schedule_id TEXT REFERENCES schedules(id) ON DELETE RESTRICT`
- `schedule_revision_hash TEXT NOT NULL`
- `job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT`
- `occurrence_key TEXT NOT NULL`
- `deduplication_key TEXT NOT NULL UNIQUE`
- `first_delivery_id TEXT NOT NULL`
- `occurrence_slot_utc TEXT`
- `source_instance_key TEXT`
- `trigger_type TEXT NOT NULL`
- `payload_hash TEXT NOT NULL`
- `received_utc TEXT NOT NULL`
- `state TEXT NOT NULL`
- `run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT`
- `terminal_effect_hash TEXT`
- `completed_utc TEXT`
- indeks `(job_id, received_utc)`

`deduplication_key` beregnes kanonisk i Engine Host fra installasjon, schedule-ID, schedule-revisjon og normalisert triggersemantikk. `delivery_id` er unik per prosesslevering og er ikke den logiske forekomsten. Samme nøkkel med annen payload/schedule-revisjon er konflikt. Task Scheduler-retry av samme forekomst oppretter ikke flere runs, og kompaktering skal bevare nøkkelen som tombstone.

#### `command_receipts`

Durable command inbox og idempotencylogg. `idempotency_key` er global innen installasjonen; `client_id` er audit/rate-limit metadata.

- `id TEXT PRIMARY KEY`
- `request_id TEXT NOT NULL`
- `client_id TEXT NOT NULL`
- `principal_sid_hash TEXT NOT NULL`
- `idempotency_key TEXT NOT NULL UNIQUE`
- `command_name TEXT NOT NULL`
- `command_schema_version INTEGER NOT NULL`
- `payload_hash TEXT NOT NULL`
- `expected_entity_revision INTEGER`
- `state TEXT NOT NULL` — `RECEIVED`, `VALIDATED`, `EFFECT_PREPARED`, `ACCEPTED`, `RUNNING`, `SUCCEEDED`, `REJECTED`, `FAILED`, `CANCELLED`; constraint og enum genereres fra `schema/state-machines.yaml`
- `effect_entity_type TEXT`
- `effect_entity_id TEXT`
- `handoff_id TEXT`
- `result_json TEXT`
- `terminal_effect_hash TEXT`
- `retention_class TEXT NOT NULL` — permanent_key, long_lived, ordinary
- `error_code TEXT`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- `completed_utc TEXT`
- indeks `(state, updated_utc)`

Samme key med annen principal, `command_name`, schema, `expected_entity_revision` eller payloadhash avvises. For command og første catalogeffekt brukes samme transaksjon. Commands som krever recoverydatabase binder `handoff_id` og følger §4.5.4. Ikke-terminale receipts avstemmes ved hostoppstart før nye muterende commands tas imot. Destruktive, run-startende og restore-relaterte kommandoer bruker `permanent_key`: detaljpayload/resultat kan komprimeres, men idempotency key, payloadhash, command schema og terminal effect hash bevares i en tombstone.

`REJECTED` er terminal avvisning før en autoritativ effekt er akseptert. Etter `EFFECT_PREPARED` brukes `FAILED` dersom en delvis autoritativ effekt må avstemmes eller aborteres. `RUNNING` er valgfri for langvarige kommandoer; korte kommandoer kan gå direkte fra `ACCEPTED` til `SUCCEEDED`, `FAILED` eller `CANCELLED`.

0B-avstemmingen avviser bare tidlige `RECEIVED`/`VALIDATED`-receipts med `COMMAND_RECEIPT_REJECTED_AFTER_STARTUP_RECONCILIATION`; `EFFECT_PREPARED`, `ACCEPTED` og `RUNNING` beholdes og rapporteres som ventende effektavstemming.

#### `command_dedup_tombstones`

Kompakt, append-only dedupliseringsindeks etter at detaljert command receipt ikke lenger må beholdes.

- `idempotency_key TEXT PRIMARY KEY`
- `request_id TEXT NOT NULL` — kompakt auditfelt som gjør 0B-replaypayload stabil etter detaljkompaktering
- `client_instance_id TEXT NOT NULL` — audit/rate-limit metadata, ikke namespace
- `principal_fingerprint TEXT NOT NULL`
- `command_name TEXT NOT NULL`
- `protocol_version INTEGER NOT NULL`
- `schema_version INTEGER NOT NULL` — command schema version
- `expected_entity_revision INTEGER`
- `payload_hash TEXT NOT NULL`
- `payload_hash_scope TEXT NOT NULL`
- `payload_canonicalization_algorithm TEXT NOT NULL`
- `payload_hash_algorithm TEXT NOT NULL`
- `terminal_state TEXT NOT NULL`
- `result_entity_type TEXT`
- `result_entity_id TEXT`
- `rejection_reason TEXT`
- `terminal_effect_hash TEXT`
- `first_seen_utc TEXT NOT NULL`
- `compacted_utc TEXT NOT NULL`

Innsetting av tombstone og fjerning/komprimering av receipt skjer i samme kritiske catalogtransaksjon. Command dispatcher sjekker både aktive receipts og tombstones før ny effekt opprettes.

#### `store_handoffs`

Catalogsiden av eksplisitte cross-store-overganger. Recoverydatabasen har en matching tabell med samme `handoff_id` og payloadhash.

- `id TEXT PRIMARY KEY`
- `handoff_type TEXT NOT NULL` — run_start, operation_catalog_record, retention_root_export, migration_barrier
- `direction TEXT NOT NULL` — catalog_to_recovery, recovery_to_catalog
- `payload_schema_version INTEGER NOT NULL`
- `entity_type TEXT NOT NULL`
- `entity_id TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `payload_hash TEXT NOT NULL`
- `state TEXT NOT NULL` — prepared, peer_committed, source_confirmed, completed, aborted, ambiguous
- `expected_peer_state TEXT NOT NULL`
- `attempt_count INTEGER NOT NULL DEFAULT 0`
- `last_error_code TEXT`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- `completed_utc TEXT`
- unik `(handoff_type, entity_type, entity_id, payload_hash)`
- indeks `(state, updated_utc)`

#### `outbox_messages`

- `id TEXT PRIMARY KEY`
- `message_type TEXT NOT NULL`
- `aggregate_type TEXT NOT NULL`
- `aggregate_id TEXT NOT NULL`
- `idempotency_key TEXT NOT NULL UNIQUE`
- `payload_json TEXT NOT NULL`
- `payload_hash TEXT NOT NULL`
- `state TEXT NOT NULL` — pending, claimed, delivered, dead_letter
- `available_utc TEXT NOT NULL`
- `next_attempt_utc TEXT NOT NULL`
- `claim_owner_instance_id TEXT`
- `claim_generation INTEGER NOT NULL DEFAULT 0`
- `claim_token TEXT`
- `claim_started_utc TEXT`
- `claim_ttl_ms INTEGER`
- `attempt_count INTEGER NOT NULL DEFAULT 0`
- `last_attempt_utc TEXT`
- `delivered_utc TEXT`
- `terminal_effect_hash TEXT`
- `last_error_code TEXT`
- `row_version INTEGER NOT NULL`
- indeks `(state, next_attempt_utc)`

Claim skjer med compare-and-swap i en kort catalogtransaksjon. Dispatcher utfører sideeffekten uten å holde transaksjonen, og fullfører bare dersom `claim_token` fortsatt matcher. En levende ownerinstans bruker monoton deadline i minnet. Etter host-/OS-restart kan en claim fra en annen `claim_owner_instance_id` bare tas over gjennom startup-reconciliation og ny CAS-generation; `claim_started_utc + claim_ttl_ms` er diagnostikk, ikke alene utløpsbevis. Samme idempotency key/payloadhash skal gi samme eksterne effekt eller en trygg duplikat. Etter retention bevares en kompakt dedup-tombstone for sideeffekter som ikke må leveres på nytt.

#### `effect_dedup_tombstones`

Kompakt deduplisering for leverte outboxeffekter og terminale triggerforekomster.

- `deduplication_key TEXT PRIMARY KEY`
- `effect_kind TEXT NOT NULL` — outbox, trigger
- `payload_hash TEXT NOT NULL`
- `terminal_state TEXT NOT NULL`
- `effect_entity_type TEXT`
- `effect_entity_id TEXT`
- `terminal_effect_hash TEXT`
- `first_seen_utc TEXT NOT NULL`
- `compacted_utc TEXT NOT NULL`

Dispatcher og trigger handler sjekker aktive rader og tombstones i samme dedupoppslag. Tombstones slettes ikke av ordinær historikkretention; en eksplisitt inkompatibel reinstallasjon får ny `installation_id` og dermed nytt namespace.

### 11.2 `recovery.sqlite`

Recoverydatabasen skal være liten, lokal og fokusert på pågående korrekthet. Den inneholder ingen bulk-snapshots eller duplikatdata.

#### `recovery_schema_migrations`

- `version INTEGER PRIMARY KEY`
- `name TEXT NOT NULL`
- `checksum TEXT NOT NULL`
- `applied_utc TEXT NOT NULL`
- `app_version TEXT NOT NULL`

#### `engine_instances`

Diagnostikk og recoverykontekst; named mutex er singletonautoritet.

- `process_instance_id TEXT PRIMARY KEY`
- `installation_id TEXT NOT NULL`
- `user_sid_hash TEXT NOT NULL`
- `app_version TEXT NOT NULL`
- `protocol_major INTEGER NOT NULL`
- `process_id INTEGER NOT NULL`
- `started_utc TEXT NOT NULL`
- `heartbeat_utc TEXT NOT NULL`
- `shutdown_state TEXT NOT NULL`

#### `recovery_runs`

- `run_id TEXT PRIMARY KEY`
- `job_id TEXT NOT NULL`
- `job_revision_id TEXT NOT NULL`
- `plan_id TEXT NOT NULL`
- `plan_checksum TEXT NOT NULL`
- `start_handoff_id TEXT NOT NULL`
- `state TEXT NOT NULL`
- `process_instance_id TEXT`
- `started_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- `last_event_sequence INTEGER NOT NULL DEFAULT 0`

#### `recovery_handoffs`

Recoverydatabasens side av §4.5.4. Catalog og recovery bruker samme ID, retning, schema og payloadhash, men oppdateres i separate transaksjoner.

- `id TEXT PRIMARY KEY`
- `handoff_type TEXT NOT NULL`
- `direction TEXT NOT NULL`
- `payload_schema_version INTEGER NOT NULL`
- `entity_type TEXT NOT NULL`
- `entity_id TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `payload_hash TEXT NOT NULL`
- `state TEXT NOT NULL` — prepared, peer_committed, source_confirmed, completed, aborted, ambiguous
- `expected_peer_state TEXT NOT NULL`
- `attempt_count INTEGER NOT NULL DEFAULT 0`
- `last_error_code TEXT`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- `completed_utc TEXT`
- unik `(handoff_type, entity_type, entity_id, payload_hash)`
- indeks `(state, updated_utc)`

Handoffpayload er liten, kanonisk og identisk checksummet på begge sider. Den inneholder bare stabile IDs, schema, forventede faser og high-water; bulkdata refereres gjennom immutable entity IDs. Reconciliation er type-spesifikk, men må følge den generiske monotone state machine og kan aldri hoppe fra `prepared` til `completed` uten bevist peer-commit.

#### `lease_counters`

Monoton fencingsekvens per muterbar ressurs.

- `resource_key TEXT PRIMARY KEY`
- `ownership_epoch INTEGER NOT NULL CHECK (ownership_epoch >= 1)`
- `last_fencing_token INTEGER NOT NULL CHECK (last_fencing_token >= 0)`
- `updated_utc TEXT NOT NULL`

Token økes i recoverytransaksjonen som registrerer en ny lease. Token gjenbrukes aldri, heller ikke etter normal release.

#### `resource_leases`

Varig speil av OS-håndtak og fencing; ikke selvstendig lockautoritet.

- `lease_id TEXT PRIMARY KEY`
- `resource_key TEXT NOT NULL`
- `ownership_epoch INTEGER NOT NULL`
- `fencing_token INTEGER NOT NULL`
- `lease_mode TEXT NOT NULL`
- `owner_instance_id TEXT NOT NULL`
- `run_id TEXT`
- `run_target_id TEXT`
- `endpoint_id TEXT`
- `endpoint_generation INTEGER`
- `os_lock_kind TEXT NOT NULL`
- `state TEXT NOT NULL`
- `acquired_utc TEXT NOT NULL`
- `heartbeat_utc TEXT NOT NULL`
- `released_utc TEXT`
- unik `(resource_key, ownership_epoch, fencing_token)`
- partial unik `(resource_key)` for aktive eksklusive leases der SQLite-uttrykket støttes

En stale rad gir aldri automatisk overtakelse. Engine Host må bevise at OS-håndtaket ikke kan være levende, ta faktisk lock og øke fencing token før ny permit utstedes. Ingen melding med eldre `ownership_epoch`, eller lavere lokal token innen samme epoke, kan autorisere sideeffekt. Ved epokebytte opprettes ny counterkontekst; gamle tokens sammenlignes ikke globalt.

#### `recovery_intent_segments`

Materialisert lokal katalog over immutable target-side intentsegmenter.

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL`
- `run_target_id TEXT NOT NULL`
- `target_endpoint_id TEXT NOT NULL`
- `target_endpoint_revision_id TEXT NOT NULL`
- `endpoint_generation INTEGER NOT NULL`
- `owner_installation_id TEXT NOT NULL`
- `ownership_epoch INTEGER NOT NULL`
- `lease_id TEXT NOT NULL`
- `fencing_token INTEGER NOT NULL`
- `segment_sequence INTEGER NOT NULL`
- `relative_path TEXT NOT NULL`
- `schema_version INTEGER NOT NULL`
- `operation_count INTEGER NOT NULL`
- `byte_count INTEGER NOT NULL`
- `segment_hash TEXT NOT NULL`
- `previous_segment_hash TEXT`
- `durability_state TEXT NOT NULL` — `PENDING`, `DURABLE`
- `state TEXT NOT NULL` — `BUILDING`, `DURABLE`, `RECONCILED`, `CLEANUP_ELIGIBLE`, `CLEANED`
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- unik `(run_target_id, segment_sequence)`
- unik `(run_target_id, relative_path)`

`relative_path` ligger alltid under den validerte installasjonsspesifikke `.mediasync/installations/<id>/recovery`-roten. Segmentet er immutable etter `DURABLE`; endring i byte/hash er `INTENT_SEGMENT_MISMATCH`. 0B-skjemaet håndhever én rad per `(run_target_id, segment_sequence)` og `(run_target_id, relative_path)`, 10 000-operasjons-/16 MiB-grensene og immutability for durable bevisfelt.

#### `recovery_object_allocations`

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL`
- `run_target_id TEXT NOT NULL`
- `operation_id TEXT`
- `target_endpoint_id TEXT NOT NULL`
- `target_endpoint_revision_id TEXT NOT NULL`
- `owner_installation_id TEXT NOT NULL`
- `ownership_epoch INTEGER NOT NULL`
- `object_role TEXT NOT NULL`
- `object_relative_path TEXT NOT NULL`
- `manifest_relative_path TEXT NOT NULL`
- `manifest_hash TEXT NOT NULL`
- `expected_size_bytes INTEGER`
- `expected_fingerprint_hash TEXT`
- `state TEXT NOT NULL` — allocated, transferred, durable, verified, preserved, cataloged, cleanup_eligible, cleaned
- `created_utc TEXT NOT NULL`
- `updated_utc TEXT NOT NULL`
- unik `(target_endpoint_id, owner_installation_id, object_relative_path)`
- unik `(target_endpoint_id, owner_installation_id, manifest_relative_path)`

Kontrollstiene genereres fra allocation-ID, ikke fra brukerens relative sti. Manifestet binder allokeringen til original sti og operation.

#### `recovery_operations`

Alle stier er relative til eksplisitte endpoint-/kontrollrøtter. En korrupt recoveryrad skal derfor ikke kunne peke commitadapteren til en vilkårlig absolutt sti.

- `run_id TEXT NOT NULL`
- `run_target_id TEXT NOT NULL`
- `operation_id TEXT NOT NULL`
- `source_endpoint_id TEXT`
- `source_endpoint_revision_id TEXT`
- `target_endpoint_id TEXT NOT NULL`
- `target_endpoint_revision_id TEXT NOT NULL`
- `endpoint_generation INTEGER NOT NULL`
- `owner_installation_id TEXT NOT NULL`
- `ownership_epoch INTEGER NOT NULL`
- `lease_id TEXT NOT NULL`
- `lease_resource_key TEXT NOT NULL`
- `fencing_token INTEGER NOT NULL`
- `phase TEXT NOT NULL`
- `source_relative_path TEXT`
- `source_guard_kind TEXT`
- `source_guard_evidence_hash TEXT`
- `source_hash_evidence_kind TEXT`
- `source_path_chain_hash TEXT`
- `source_case_context_hash TEXT`
- `staging_object_id TEXT`
- `final_relative_path TEXT NOT NULL`
- `version_object_id TEXT`
- `quarantine_object_id TEXT`
- `intent_segment_id TEXT REFERENCES recovery_intent_segments(id) ON DELETE RESTRICT`
- `intent_ordinal INTEGER`
- `target_precondition_kind TEXT NOT NULL`
- `expected_source_fingerprint_json TEXT`
- `expected_target_fingerprint_json TEXT`
- `expected_source_parent_identity_json TEXT`
- `expected_target_parent_identity_json TEXT`
- `expected_target_path_chain_hash TEXT`
- `expected_staging_fingerprint_json TEXT`
- `expected_final_fingerprint_json TEXT`
- `observed_target_file_id TEXT`
- `transfer_state TEXT`
- `assurance_level TEXT`
- `staging_durability_state TEXT`
- `final_durability_state TEXT`
- `catalog_handoff_id TEXT`
- `last_error_code TEXT`
- `updated_utc TEXT NOT NULL`
- primærnøkkel `(run_id, operation_id)`
- unik `(intent_segment_id, intent_ordinal)` når begge er satt

`COMMIT_INTENT_RECORDED` krever et `DURABLE` intentsegment, gyldig ordinal og samme `lease_id`/`fencing_token` som aktiv `MutationPermit`. 0B-skjemaet persisterer primærnøkkel `(run_id, operation_id)`, unik `(intent_segment_id, intent_ordinal)` når begge er satt og en materialisert fase som bare kan flyttes via recoverywriterens CAS-store. Alle absolutte stier rekonstrueres fra endpointrevisjon + relative path gjennom `SafePath`; de tas aldri direkte fra recoverypayload.

#### `recovery_events`

- `event_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `run_id TEXT NOT NULL`
- `run_sequence INTEGER NOT NULL`
- `operation_id TEXT`
- `from_phase TEXT`
- `to_phase TEXT NOT NULL`
- `event_utc TEXT NOT NULL`
- `process_instance_id TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `previous_event_hash TEXT`
- `event_hash TEXT NOT NULL`
- unik `(run_id, run_sequence)`

Hver faseovergang appendes til events og oppdaterer materialisert operation/run-state i samme recoverytransaksjon. Hashkjeden er per run: `previous_event_hash` peker til foregående `run_sequence`, og hashinput bruker canonical schema/version/payload. 0B-store beregner kjeden deterministisk over schema, runsekvens, operation, fase, prosessinstans, payload og forrige hash. Kjeden er korrupsjonsdeteksjon og audit, ikke kryptografisk autentisering mot en ondsinnet lokal bruker.

### 11.3 Varighet, forbindelser og writer-eierskap

Engine Host eier alle skrivbare forbindelser. GUI og trigger client åpner ingen databasefil direkte.

Katalogens bulkforbindelse:

```sql
PRAGMA foreign_keys = ON;
PRAGMA trusted_schema = OFF;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA cache_size = -65536;
PRAGMA wal_autocheckpoint = 1000;
```

Katalogwriteren har i tillegg en serialisert kritisk `FULL`-forbindelse. Bulk- og kritisk forbindelse skriver aldri samtidig.

`recovery.sqlite`:

```sql
PRAGMA foreign_keys = ON;
PRAGMA trusted_schema = OFF;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;
PRAGMA wal_autocheckpoint = 100;
```

Krav:

- kun Engine Host utfører migrasjoner eller writes;
- databasefilene må ligge på lokal støttet filsystemsti med begrenset ACL; oppstart avviser NAS/SMB/removable plassering;
- extension loading er deaktivert, `trusted_schema=OFF` brukes, og `SQLITE_DBCONFIG_DEFENSIVE`/tilsvarende aktiveres når runtime støtter det;
- read pool bruker `query_only=ON`, korte snapshot-transaksjoner og keyset-paginering;
- rekonstruerbare `file_entries`, hashcache og metrics kan committes med `NORMAL`;
- jobb-/endepunktrevisjoner, aktiv revisjonspeker/claims, snapshot-/planseal, command receipt, handoff, outbox, baseline og terminal runstate bruker kritisk `FULL`-transaksjon;
- recoveryfase committes med `FULL` før neste irreversible filsystemsteg;
- ingen recoverytransaksjon venter på katalogwriter, GUI eller fil-I/O;
- ingen handler holder catalog- og recovery-write-transaksjon samtidig; `store_handoffs`/`recovery_handoffs` er den eneste autoriserte cross-store-protokollen;
- writable connections bruker ikke `ATTACH DATABASE`, SQLite shared-cache eller en wrapper som skjuler en cross-database transaksjon;
- det finnes ingen cross-database foreign keys;
- snapshot blir aldri komplett/immutable før alle `snapshot_batches`, coverage, kollisjoner, summer og feilstatistikk er committet og checksummet;
- plan blir aldri immutable før operasjonstelling, canonical checksum, dependencies, baselinekontekst og target-preconditions er validert;
- `SQLITE_BUSY`, `SQLITE_BUSY_SNAPSHOT`, `SQLITE_FULL`, `SQLITE_CORRUPT`, `SQLITE_NOTADB`, readonly- og I/O-feil klassifiseres separat;
- WAL-størrelse overvåkes; checkpoint startes kontrollert når lange readers ikke blokkerer;
- `VACUUM` kjøres aldri automatisk under analyse, run eller recovery;
- online backup tas før ikke-triviell migrasjon og periodisk etter terminal avstemming, med retention og high-water manifest;
- integritetsfeil blokkerer muterende kommandoer. Programmet skal ikke «reparere» en autoritativ database ved å ignorere rader.

### 11.4 Migrasjons- og kompatibilitetsprotokoll

Migrasjon av det autoritative state-settet er en restartbar epoch. Flyten under viser kandidatdesignet med to databaser; dersom ADR-003 velger én database, beholdes epoch, backup, checksums og recoveryporten, mens pair-/handoffsteg som ikke er relevante utgår:

1. Ta Engine Host singleton og eksklusiv migrasjonslease.
2. Gå i quiesce: stans nye muterende commands, vent til alle filsystemoverganger er ved sikkert punkt og tøm writerkøer.
3. Verifiser at ingen aktiv run/commitfase er uavklart; recovery kan ikke skjules av migrasjon.
4. Les schemaet i alle valgte state stores read-only, avvis nyere unsupported epoch og verifiser alle historiske migration checksums. Runtime beregner SHA-256 over kanonisk JSON med migrationens versjon, navn og eksakte ordnede SQL-statements. En eksisterende lokal preview-database med eldre name-only metadata kan få checksumkolonnen backfillet én gang, men bare etter at versjoner/navn utgjør et komplett kjent prefiks uten hull eller nyere rader. Deretter er historikken databasebeskyttet mot `UPDATE`/`DELETE`, og enhver checksumdrift er fatal før nye migrasjoner kjøres.
5. Opprett en lokal `migration-<epoch>.intent.json` via temp → flush → rename. Den inneholder før-/målversjoner, app-build, high-water per valgt store og forventede backupfiler.
6. Ta en logical backup barrier. Lag SQLite Online Backup av hver valgt database mens writes er quiesced; skriv checksum, størrelse, schema og high-water til manifest. Ved flere filer er backupene et koordinert sett, men det påstås ikke cross-database atomisitet.
7. Migrer én valgt database om gangen med separat transaksjon. Bruk expand → backfill → validate → contract; store backfills er restartbare og progressjournalførte.
8. Etter hvert steg oppdateres migration-intent med fullført store/phase via atomisk rewrite.
9. Kjør `foreign_key_check`, domeneinvarianter, handoff-/recoveryavstemming og quick/integrity-check etter policy.
10. Oppdater `installation_state` først når alle valgte state stores og kontrollschemas er kompatible. Marker epoken committed og publiser IPC readiness.
11. Ved krasj før commit oppdager startup intentfilen og velger deterministisk: fortsett samme epoch eller restore det verifiserte backupsettet. Ved flere stores kan den ikke åpne ordinær writable drift i en blandet epoke.
12. Ved feil: steng writable tilgang, behold intent/backups og gi recoveryveiledning. Ingen automatisk «reset database».

Kontrollmappen `.mediasync` har separat `control_schema_version`. En kontrollmappemigrasjon krever endpointlease og immutable migrationmanifest per mål. Ny app skal kunne lese minst én dokumentert eldre kontrollversjon; eldre app skal avvise nyere ukjent versjon. Ingen kontrollmappemigrasjon skjer mens mål har uavklart recovery.

### 11.5 Spørrings-, indeks- og immutable-regler

Codex skal ha dedikerte repositorymetoder og realistiske query-plan-tester for:

- case-kollisjoner på ikke-unik `comparison_key`;
- merge-lignende sammenligning av snapshots;
- directory coverage og absence proof;
- per-target analysesummer uten detaljmaterialisering;
- paginert operasjonstabell med stabil cursor;
- duplikater etter størrelse/hash/replika-relasjon;
- batchgruppering per forelder/fase/batch key;
- ikke-terminale recoveryfaser og leasekonflikter;
- command/trigger-deduplisering;
- outbox-claiming uten dobbel samtidig levering;
- avstemming mellom recovery og catalog;
- run-/target-/operation-attempts uten historikkoverskriving.

Hver varm query skal ha integrasjonstest med minst 100 000 rader og lagret `EXPLAIN QUERY PLAN`-forventning. En schemaendring som introduserer full table scan i en definert varm query skal feile performance/architecture-gaten.

Plan-immutability håndheves slik:

- før `immutable=1` kan builderen skrive i én kontrollert byggesession;
- seal-transaksjonen validerer counts, dependencies, preconditions, endpoint/job revisions og canonical checksum;
- etter seal avviser database-trigger/repository enhver `UPDATE` eller `DELETE` på planen, dens endpointbindinger, operations og dependencies;
- en ny beslutning oppretter en avledet plan, aldri en patch av den gamle;
- execution leser planrader read-only og skriver resultater til run/outcome-tabeller.

### 11.6 Referansedrevet retention og databasekomprimering

Snapshots, planer, hashes og audit kan vokse til mange millioner rader. Retention skal derfor være eksplisitt, trygg og uavhengig av filretention på backupmålet.

Bindende regler:

- mark-and-sweep starter fra roots: aktive jobb-/endepunktrevisjoner, ikke-terminale analyses/runs/handoffs, alle recoveryreferanser, aktive baseline-sett, gjenopprettbare versjons-/karanteneobjekter, brukerholds og valgt historikkvindu;
- recoverywriteren publiserer et immutable, checksummet `retention_root_export` med recovery high-water og alle catalog-entity-ID-er som fortsatt må beskyttes; catalog importerer dette gjennom vanlig cross-store handoff før markfasen;
- en snapshot-/plan-/revisjonsrad kan bare bli kandidat når ingen root eller transitiv FK/auditreferanse peker til den;
- retention bygger en immutable slettemanifest med counts, byteestimat, cutoff, catalog-/recovery-high-water, rootset-hash og schema-version før første delete; kandidatene markeres `retention_pending`, og nye use cases kan ikke opprette referanser til dem;
- før hver deletebatch revalideres catalog-referanser/holds og matching recovery-root-export/high-water. Ny recoveryreferanse, handoff eller hold pauser manifestet og krever ny markfase;
- delete skjer i små catalogtransaksjoner med expected manifest/state og kan resumes; ingen `ON DELETE CASCADE` brukes til å skjule stort eller sikkerhetskritisk arbeid;
- `recovery.sqlite`, ikke-terminale handoffs, intentsegmenter, aktive baseline-sett og audit som kreves for restore/recovery slettes aldri av vanlig katalogretention;
- command-, trigger- og outboxdetaljer kan kompakteres, men deres permanente dedupnøkler/payloadhashes/terminal effect hashes flyttes atomisk til tombstone før detaljraden kan fjernes;
- arkivert jobb beholder konfigurert minimumshistorikk og alle restore-/recoveryreferanser;
- hashcache og reconstructible metrics kan ha kortere policy enn snapshots/planer;
- databasebackup tas før stor retention når policy/ledig plass tillater det;
- `VACUUM INTO` eller annen komprimering er en separat vedlikeholdsepoch under full quiesce, etter logical backup og integrity check. Output skrives til ny lokal fil, verifiseres mot schema/high-water/checksum, alle databasehandles lukkes, og en checksummet compaction-intent styrer same-volume swap med bevart rollbackfil; vanlig autoretention kjører ikke full `VACUUM`;
- krasj før compaction-commit skal velge den ene verifiserte databasen etter intent/state og aldri åpne både gammel og ny fil som tilfeldige sannhetskilder;
- disk full under retention skal ikke føre til sletting av recoverybevis eller blind retryloop.

Foreslåtte tabeller:

```text
# recovery.sqlite
recovery_retention_root_exports(id, recovery_high_water, root_hash, payload_hash, state, created_utc)

# catalog.sqlite
retention_holds(id, entity_type, entity_id, reason, created_utc, expires_utc, released_utc)
catalog_retention_root_imports(id, recovery_export_id, recovery_high_water, root_hash, handoff_id, state)
catalog_retention_runs(id, policy_hash, catalog_high_water, recovery_high_water, rootset_hash, manifest_hash, state, created_utc, completed_utc)
catalog_retention_items(run_id, entity_type, entity_id, expected_reference_count, state)
```

Retentiontester må bevise at en tilfeldig katalog av historiske objekter aldri fjerner noe som fortsatt er nåbart fra catalog- eller recovery-rootsettet, og at en ny hold/recoveryreferanse mellom mark og sweep avbryter sletting.

### 11.7 Interne backup-sett og restore av applikasjonstilstand

Alle autoritative SQLite-state stores valgt av ADR-003 er ett logisk tilstandssett. Backup og restore bruker derfor en checksummet **backup-sett-epoch**. Kandidatdesignet under viser to filer; ved én database inneholder settet én databasefil, men samme manifest-, high-water-, intent- og target-reconciliationkrav gjelder.

Lokal struktur:

```text
<AppData>\MediaSync Home\state-backups\<backup-set-id>\
    backup-set.intent.json
    catalog.sqlite.backup
    recovery.sqlite.backup
    backup-set.manifest.json
```

0B-implementasjonsnote: Den konkrete grensen ligger i `adapters/sqlite/state_backup.py`. Den oppretter ett manifestert backup-sett for ADR-003-paret med SQLite Online Backup, per-store identity/schema/migration high-water, `quick_check`, `foreign_key_check`, size/SHA-256, unresolved target-intent count/high-water og combined state-set hash, og verifiserer at catalog/recovery-medlemmene ikke er manglende, manipulerte eller blandet fra ulike epoker. `plan_sqlite_state_restore()` bygger en typed restore-plan bare etter full settverifisering og blokkerer automatisk restore når nåværende recoverydatabase har nyere unresolved target-intents enn backupen, også ved samme timestamp men høyere count. `restore_sqlite_state_backup_set()`/`apply_sqlite_state_restore_plan()` kopierer hvert backupmedlem til same-directory tempfiler, re-verifiserer SQLite-evidence, skriver en restore-epoch intent, bytter catalog/recovery-livefiler med separate rollbackfiler, flytter stale SQLite sidecars ut av live-navnene, re-verifiserer de publiserte targetfilene og skriver committed-markør først etter at hele paret er aktivt; en simulert andre-store-feil ruller første store tilbake. `recover_incomplete_sqlite_state_restore_epochs()` validerer uferdige intentfiler mot canonical layout-/temp-/rollback-/sidecarstier, ruller uferdige epochs tilbake, skriver `state-restore.rolled-back.json`, og `build_engine_host_runtime()` kjører dette før SQLite åpnes writable. `compact_sqlite_state_stores()` kjører `VACUUM INTO` til same-directory tempfiler under samme maintenance-admission, verifiserer catalog/recovery-output mot SQLite-evidence og checksum, skriver `state-compaction.intent.json`, bytter paret med rollbackfiler, skriver `state-compaction.committed.json` først etter at begge livefiler er verifisert, og `recover_incomplete_sqlite_state_compaction_epochs()` ruller uferdige compaction-epochs tilbake før runtime åpner SQLite. `admit_sqlite_state_restore_maintenance()` leser catalog/recovery read-only og avviser restore-/compaction-maintenance når nåværende state viser aktive runs/run-targets, ikke-terminale command receipts, uleverte outbox-meldinger, aktive resource leases, unresolved target-intent segments eller uferdige restore-/compaction-epochs; `EngineHostRuntime.admit_state_restore_maintenance()` legger i tillegg til host-retained in-memory leases som blocker. `EngineHostRuntime.restore_state_from_backup_set()` og `EngineHostRuntime.compact_state_stores()` bruker admission-gaten, nekter blokkert vedlikehold uten å lukke handles, lukker host-eide SQLite connections ved clean admission og kjører den verifiserte swappen slik at neste runtime-start åpner ett konsistent state-sett. `EngineHostIpcService` gjenkjenner `RESTORE_STATE_FROM_BACKUP_SET` i read-only IPC mode og dispatches via runtime restore-executor mens ordinære muterende commands forblir deaktivert; ved vellykket restore er restore-epoch control files den varige effektreceipten, siden den gamle catalogdatabasen og dens command receipts med vilje erstattes. `plan_sqlite_state_maintenance_retention()`/`apply_sqlite_state_maintenance_retention()` bygger og utfører en count-basert retentionplan som bare sletter verifiserte backup-sett og terminale restore-/compaction-epochs, beskytter backup-sett referert av beholdte restore-epochs, sletter tilhørende rollbackfiler først etter terminal kontrollfilvalidering, og skipper uferdige eller malformede artifacts; `EngineHostRuntime.prune_state_maintenance_artifacts()` kjører dette bak samme vedlikeholdsadmission. `reconcile_committed_sqlite_state_restore_epochs()` validerer terminale restore-epoch markører etter restore-/compaction-recovery og før SQLite åpnes writable, rapporterer committed/rolled-back counts og siste committed restore-epoch i runtime- og startup-payloaden før ordinær startup-reconciliation. `plan_sqlite_state_restore()` leser også target-side intentmarkørheaders read-only fra kjente lokale endpointrøtter, deduper dem mot nåværende recoverydatabase etter segment-ID og blokkerer restore når kombinert marker-/databasebevis er nyere enn backupsettets high-water.

Bindende backup-protokoll:

1. Ta Engine singleton og en eksklusiv maintenancelease.
2. Gå i quiesce: avvis nye muterende commands, la aktive commits nå et sikkert journalpunkt og tøm alle writerkøer.
3. Dersom flere stores brukes, avstem alle ikke-terminale cross-store handoffs. Et backupsett kan ikke tas mens en handoff står mellom peercommit og source-confirmation.
4. Persistér `backup-set.intent.json` med nytt ID, installasjon, app-/schema-/kontrollversjoner og forventet output.
5. Etabler en logical barrier med high-water per valgt store, aktive recovery-run/intentsegment heads og eventuell handoff-root-hash.
6. Ta SQLite Online Backup av hver valgt database til unik tempfil mens writes er quiesced. Ikke bruk filkopi av åpne WAL-databaser.
7. Kjør `quick_check`/`foreign_key_check` etter policy på backupfilene og beregn størrelse + kryptografisk checksum.
8. Skriv canonical `backup-set.manifest.json` med alle storefiler, high-water, schema, checksums og root hashes; flush og publiser settet med atomisk directory-/manifestmarkør.
9. Først når manifestet er `COMMITTED` kan settet brukes eller inngå i retention. En halv mappe er bare kandidat for opprydding.

Restore-protokoll:

1. Start Engine Host i eksplisitt `RESTORE_MAINTENANCE`; ordinær IPC er read-only og ingen endepunktlease eller transferprosess kan være aktiv.
2. Velg ett komplett backupsett. Dersom flere stores brukes, er det forbudt å blande filer fra ulike sett/epoker.
3. Verifiser installation-ID/policy, manifestchecksum, alle filchecksums, schema-/appkompatibilitet og high-water-sammenheng før noen livefil erstattes.
4. Les nåværende target-side intentsegmenter og kontrollmarker read-only. Dersom de viser autoriserte mutasjoner nyere enn backupsettets recovery-high-water, er automatisk restore blokkert til de er avstemt; backupen må ikke brukes til å «glemme» mulig filarbeid.
5. Restore alle valgte databaser til nye lokale tempfiler, kjør integritets-/domene-/eventuelle handoffkontroller og skriv en checksummet restore-intent.
6. Lukk alle databasehandles. Bytt alle storefiler gjennom en restartbar same-volume swap med separate rollbackfiler og én restore-epoch som angir nøyaktig hvilket sett som er aktivt.
7. Ved krasj fullfører startup samme epoch eller ruller tilbake hele settet. Ved flere stores åpner den aldri en blanding av nye og gamle medlemmer.
8. Etter swap kjøres full startup-reconciliation mot target intentsegmenter før muterende readiness.

Retention av interne backupsett:

- behold minst ett siste verifisert sett, siste pre-migration/pre-compaction-sett og alle sett med aktiv hold;
- slett hele settet etter manifestvalidering, aldri enkeltmedlemmer;
- detaljert registry i catalog er en read model; filsystemmanifestet er nødvendig når catalog selv er korrupt;
- intern state-backup er ikke en erstatning for brukerens bilde-/videobackup og skal beskrives som applikasjonsrecovery.

Faulttester skal krasje etter hvert trinn i backup- og restore-epochen og bevise at Engine Host enten åpner ett komplett verifisert state-sett eller forblir blokkert i maintenance.


### 11.8 Lokal tilstandskapasitet og `SQLITE_FULL`

Før en stor analyse estimerer Engine Host lokal programtilstand under AppData:

```text
estimated_catalog_growth
estimated_recovery_growth
estimated_hash_cache_growth
estimated_log_growth
internal_backup_reserve
minimum_free_space
soft_quota
hard_stop_quota
```

Bindende policy:

- vis forventet lokal vekst separat fra ledig plass på backupmålet;
- soft quota utløser anbefalt cache-/loggopprydding, men sletter aldri recovery-/baseline-/planbevis;
- hard stop blokkerer nye analyser og transfers før katalogen risikerer `SQLITE_FULL`;
- ved faktisk `SQLITE_FULL` stopper writerne ved et trygt punkt, bevarer committet recoverybevis, avbryter uforseglede snapshots og går ikke inn i ukontrollert retryloop;
- rekonstruerbar hashcache, metrics og gamle logs kan ryddes gjennom en manifestert policy; jobbrevisjoner, planseal, outcomes, baselines, handoffs og ikke-terminal recovery kan ikke kastes;
- flytting av lokal state til en annen lokal fast disk er en maintenance-saga med quiesce, verifisert backup-sett, same-volume/cross-volume copy-verify-swap og rollback;
- NAS, SMB og flyttbare medier er fortsatt forbudt for autoritativ SQLite-state.

0B-implementasjonsnote: `StateCapacityGate` måler den lokale state-roten med en
avgrenset, symlink-fri skann og reserverer estimert catalog-, recovery-, hashcache-
og loggvekst, intern backupreserve og minimum ledig plass. Standardgrensene er 4
GiB soft quota, 8 GiB hard stop, 1 GiB minimum ledig plass og 512 MiB intern
backupreserve. Snapshotmaterialisering sjekker både et konservativt estimat før
skann og målte radantall før første databasetransaksjon; run-executoren sjekker
før første steg. Soft quota publiserer bare anbefalingen
`CLEAN_NON_AUTHORITATIVE_CACHE_AND_LOGS`; ingen automatisk sletting utføres.
Faktisk `SQLITE_FULL` rulles tilbake, klassifiseres uten ukontrollert retry,
latches til Engine Host restart og publiseres gjennom handshake/status. Catalog
og recovery er separate SQLite-filer, og integrasjonstesten fyller bare catalog
mens den verifiserer at tidligere committet recoverybevis fortsatt kan leses.
