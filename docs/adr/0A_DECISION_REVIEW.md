# 0A Decision Review

This review summarizes the available 0A evidence for owner decisions. It is not
an owner decision record. Every ADR still requires an explicit owner decision in
`docs/adr/catalog.yaml` before it becomes binding.

## Evidence Summary

| Package | Result | Evidence | Remaining blocker |
|---|---|---|---|
| 0A.0 Environment preflight | Passed | `docs/ARCHITECTURE_SPIKE_REPORT.md` | None for preflight |
| 0A.1 Process and IPC | Blocked | `spikes/0a1_process_ipc/`, `artifacts/0a1/` | Task Scheduler-like session and wrong-SID/remote-client proof |
| 0A.2 Endpoint ownership | Blocked | `spikes/0a2_endpoint_ownership/`, `artifacts/0a2/` | Two-client SMB lab and final BLAKE3 marker checksum |
| 0A.3 Recovery and paths | Passed locally | `spikes/0a3_recovery_paths/`, `artifacts/0a3/` | SMB SourceReadGuard proof |
| 0A.4 SQLite and capacity | Passed locally | `spikes/0a4_sqlite_capacity/`, `artifacts/0a4/` | Owner decision on ADR-003 |
| 0A.5 Windows argv and packaging | Blocked | `spikes/0a5_windows_packaging/`, `artifacts/0a5/` | PySide6/BLAKE3/Nuitka/SDK/signing tools and clean Windows VM |

## Owner Decision List

Owner decision values must be one of `OWNER_ACCEPTED`, `REJECTED`, or
`DEFERRED_WITH_SCOPE_REDUCTION`. `PENDING` remains correct until the owner
chooses explicitly.

| ADR | Evidence status | 0A evidence | Alternatives | Risk and reversal cost | Codex recommendation | Owner action |
|---|---|---|---|---|---|---|
| ADR-001 Headless Engine Host | `PROPOSED` | Local host/client and receipt behavior passed; non-interactive Task Scheduler context is missing | GUI-owned engine, per-trigger engine, or headless singleton | Reversal is high after IPC, scheduler, and DB ownership land | Defer until Task Scheduler/session proof or scope reduction | Decide after 0A.1 blockers |
| ADR-002 Local named pipes | `PROPOSED` | Same-SID pipe, protocol mismatch, local-only flag, and idempotency passed; wrong-SID/remote proof missing | TCP loopback, COM, files, or direct GUI DB access | Reversal is medium-high after clients depend on IPC protocol | Defer until wrong-SID/remote test, or accept local-only scope reduction | Decide after 0A.1 blockers |
| ADR-003 One or two SQLite DBs | `RECOMMENDED` | 1M rows passed; two DB keeps recovery tiny and survives catalog `SQLITE_FULL` probe | One DB with differentiated durability | Reversal is very high after schema/migrations | Accept two DBs with explicit handoffs, unless owner prioritizes simplicity over isolation | Owner can accept/reject now |
| ADR-004 Robocopy only to staging | `PROPOSED` | Recovery/staging paths and argv hardening passed; no real Robocopy transfer run | Let Robocopy write final tree | Reversal is high after transfer adapter design | Keep design, but require later Robocopy integration proof | Defer to integration milestone |
| ADR-005 Immutable job/endpoint/plan revisions | `PROPOSED` | Not in 0A scope | Mutable config rows | Reversal is high after schema/UI | Keep proposed for 0B/Milestone 1 | Defer |
| ADR-006 OS handle is lease authority | `PROPOSED` | Local lock-handle proof passed; two-client SMB lock proof missing | DB lease row or lockfile existence | Reversal is high for endpoint safety | Defer until SMB lab or scope reduction to local-only/no-auto-SMB | Decide after 0A.2 blockers |
| ADR-007 CAS target mutation | `PROPOSED` | 0A.3 fallback replace and recovery preconditions passed locally | Last-writer-wins or overwrite-on-retry | Reversal is high; unsafe alternative risks data loss | Accept after owner reviews local-only nature | Owner can accept with SMB caveat |
| ADR-008 Outbox/reconciliation | `PROPOSED` | Not in 0A scope | Inline external side effects | Reversal is medium-high after scheduler/notifications | Keep proposed for 0B/Milestone 1 | Defer |
| ADR-009 No runtime plugins | `PROPOSED` | Not in 0A scope | Plugin runtime or dynamic adapter loading | Reversal is medium before packaging, high after distribution | Keep proposed; no 0A blocker | Defer |
| ADR-010 Best-effort live snapshot | `PROPOSED` | Local SourceReadGuard/fallback policy passed; SMB SourceReadGuard missing | Promise VSS-like point-in-time snapshot | Reversal is low in docs, high in UX promises | Accept best-effort semantics with honest fallback | Owner can accept with SMB caveat |
| ADR-011 Durable handoff saga | `EVIDENCE_COMPLETE` | 0A.3 filesystem recovery and 0A.4 DB handoff crash windows passed | Hidden cross-store transaction or best-effort repair | Reversal is high after schema/recovery | Accept saga/handoff model | Owner can accept now |
| ADR-012 Bounded intent segments | `PROPOSED` | 0A.3 local intent/recovery flow passed; large segment packing not fully proven | One file per operation or DB-only recovery | Reversal is medium-high after recovery design | Keep proposed, require 0B/Milestone 1 segment packing tests | Defer |
| ADR-013 Unelevated contained child | `PROPOSED` | Suspended child, Job Object assignment, and kill-on-close passed locally | Ordinary subprocess launch | Reversal is medium after process supervisor | Accept containment direction after packaging/integration confirms Robocopy child | Defer or accept with integration caveat |
| ADR-014 Baseline context | `PROPOSED` | Not in 0A scope | Reuse baseline across config changes | Reversal is high after pair-sync design | Keep proposed for Milestone 1/14 | Defer |
| ADR-015 Capability-typed final mutation ports | `PROPOSED` | 0A.3 supports narrow mutation semantics; 0B port architecture not built | General filesystem adapter | Reversal is medium-high after app layer | Keep proposed for 0B architecture tests | Defer |
| ADR-016 Monotone fencing tokens | `PROPOSED` | Local takeover stales old permit; SMB disconnect/reconnect missing | Epochless lease rows or wall-clock lease | Reversal is high after recovery schema | Defer until SMB/reconnect proof or local-only scope reduction | Decide after 0A.2 blockers |
| ADR-017 Permanent tombstones | `PROPOSED` | Not in 0A scope | Retain full receipts forever or delete details unsafely | Reversal is medium after retention | Keep proposed for 0B/Milestone 1 | Defer |
| ADR-018 Manifested backup epoch | `EVIDENCE_COMPLETE` | 0A.4 backup set rejects mixed catalog/recovery epochs | Per-file ad hoc restore | Reversal is high after migration/backup tools | Accept backup-set epoch model | Owner can accept now |
| ADR-019 One writer per ownership epoch | `PROPOSED` | Local classification/takeover passed; two-client SMB writer proof missing | Multi-writer coordination or DB-only ownership | Reversal is very high after endpoint design | Defer until SMB lab or reduce scope to read-only SMB writes | Decide after 0A.2 blockers |
| ADR-020 `.mediasync` classified first | `PROPOSED` | All nine local classification states passed; final BLAKE3 marker checksum missing | Blind exclude or automatic adoption | Reversal is low now, high after scanner | Accept classification rule after BLAKE3 marker validation | Decide after BLAKE3 blocker |
| ADR-021 Head tables and DB constraints | `PROPOSED` | Not in 0A scope | Python-only validation or mutable active pointers | Reversal is high after schema | Keep proposed for Milestone 1 | Defer |
| ADR-022 Hash evidence levels | `PROPOSED` | 0A.3 touched source evidence/fallback; full hash lattice not built | Treat metadata cache as current content proof | Reversal is high after scanner/cache | Keep proposed for Milestone 3 | Defer |
| ADR-023 SourceReadGuard or current hash | `PROPOSED` | Local NTFS guard passed; SMB guard missing | Trust source metadata from analysis time | Reversal is medium-high after transfer adapter | Accept fallback principle, require SMB capability profile | Owner can accept with SMB caveat |
| ADR-024 Short object paths | `PROPOSED` | 0A.3 long logical paths and opaque objects passed | Mirror full user tree under control area | Reversal is medium after recovery tools | Accept short-object path model | Owner can accept now |
| ADR-025 Monotonic live claims | `PROPOSED` | Not in 0A scope | UTC-only live claim expiry | Reversal is medium after scheduler/outbox | Keep proposed for 0B/Milestone 1 | Defer |
| ADR-026 Exact contracts | `PROPOSED` | Handoff validators exist; contracts remain draft/blocked | Markdown-only contracts | Reversal is medium before codegen, high after codegen | Keep proposed; do not freeze contracts yet | Defer |
| ADR-027 Windows argv/resolver | `EVIDENCE_COMPLETE` | `GetSystemDirectoryW`, final-path validation, argv round-trip, forbidden flag rejection passed | PATH lookup, shell command string, ad hoc quoting | Reversal is medium after process adapter | Accept canonical resolver/argv builder | Owner can accept now |
| ADR-028 Windows packaging | `BLOCKED` | Packaging preflight shows missing PySide6/BLAKE3/Nuitka/SDK/signing tools and no clean VM | Zip-only Python runtime or alternate packager | Reversal is medium before dependencies lock, high after release tooling | Keep blocked until toolchain and clean VM smoke pass | Provide toolchain/VM or defer scope |

## Scope Reduction Options

| Option | What changes | Safety consequence | ADRs affected |
|---|---|---|---|
| Local-only first release | SMB writable targets remain read-only or manual-only | Avoids unproven cross-machine locking while preserving local backup path | ADR-006, ADR-016, ADR-019, ADR-023 |
| No scheduled non-interactive trigger initially | Only interactive GUI/agent starts Engine Host | Avoids unproven Task Scheduler session class but weakens automation | ADR-001, ADR-002 |
| Zip/dev-run preview only | No packaged exe claim | Avoids false packaging promise; not a user-ready release | ADR-028 |
| Defer final BLAKE3 marker freeze | Keep endpoint marker schema draft until dependency is locked | Avoids claiming final checksum compatibility too early | ADR-020, ADR-026 |

## Contract Gate

No contract should move to `frozen` in this review. `schema/contracts-manifest.yaml`
correctly remains draft/blocked because no governing ADR has
`owner_decision = OWNER_ACCEPTED`.

## 0B Gate

0B must remain blocked. The next owner action is to either provide the missing
lab/toolchain evidence or explicitly choose scope reductions in the ADR catalog.
