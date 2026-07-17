# Owner Decision Worksheet

This worksheet is a non-binding input page for the project owner. It does not
record a decision. The binding source remains `owner_decision` in
[`catalog.yaml`](catalog.yaml), and every value stays `PENDING` until the owner
explicitly chooses.

Use this page to answer in chat later with short labels such as:

```text
Packaging: P2
SMB: S2
Scheduler: T2
Ready ADRs: approve Codex recommended set
0B: wait
```

## Decision Order

The safest order is:

1. Packaging scope.
2. SMB/write-target scope.
3. Scheduler/automation scope.
4. Ready local ADRs.
5. Permission to open 0B.

## Packaging

| Choice | Meaning | What Codex does next | ADR effect |
|---|---|---|---|
| P1 | Prove signed release path now | Use [`../../artifacts/0a5/release-signing-plan.json`](../../artifacts/0a5/release-signing-plan.json) after owner provides signing policy/certificate, timestamp URL, clean VM, and transfer path | ADR-028 can move only after signed build and clean-VM smoke pass |
| P2 | Unsigned local preview for now | Keep the local Nuitka executable evidence, do not claim release-ready packaging | ADR-028 remains blocked or can be deferred with scope reduction |
| P3 | Reject Nuitka/PySide6 packaging | Stop current packaging direction and run a new packager spike | ADR-028 rejected/superseded later |

Codex recommendation: P1 for release quality, P2 if the goal is a quick local
preview before signing infrastructure exists.

## SMB Scope

| Choice | Meaning | What Codex does next | ADR effect |
|---|---|---|---|
| S1 | Provide two-client SMB lab | Run cross-machine writer ownership, stale reconnect, lock, and SMB SourceReadGuard probes | ADR-006, ADR-016, ADR-019, ADR-023 can be decided from stronger evidence |
| S2 | Local-only first release | Writable SMB targets stay out of first release; SMB can be read-only/manual until proven | Affected ADRs can be deferred with scope reduction |
| S3 | Reject one-writer endpoint model | Stop ownership design and spike another coordination model | ADR-006, ADR-016, ADR-019 likely rejected/superseded later |

Codex recommendation: S1 if a lab is available; otherwise S2 to avoid unsafe
cross-machine claims.

## Scheduler Scope

| Choice | Meaning | What Codex does next | ADR effect |
|---|---|---|---|
| T1 | Provide non-interactive/wrong-SID test setup | Run Task Scheduler non-interactive/session-policy and wrong-SID/remote IPC tests | ADR-001 and ADR-002 can be decided from stronger evidence |
| T2 | Interactive/same-user automation first | Keep same-SID local trigger evidence; do not claim full non-interactive automation | ADR-001 and ADR-002 can be deferred with scope reduction |
| T3 | Reject headless host/named-pipe direction | Stop current process architecture and spike GUI-owned engine or another IPC model | ADR-001 and ADR-002 likely rejected/superseded later |

Codex recommendation: T1 if test credentials/lab are available; otherwise T2.

## Ready Local ADRs

These have strong local evidence and can be approved without external lab
work, if the owner agrees with the tradeoffs:

| ADR | Recommended owner choice | Why |
|---|---|---|
| ADR-003 | Approve two local SQLite databases with explicit handoffs | 1M-row local evidence passed; recovery state stays small when catalog growth hits `SQLITE_FULL` |
| ADR-011 | Approve durable handoff saga | Filesystem and database crash-window evidence passed locally |
| ADR-018 | Approve manifested backup epoch | Mixed catalog/recovery restore epochs are rejected by evidence |
| ADR-020 | Approve `.mediasync` classification-first | All local classification states and BLAKE3/JCS marker validation passed |
| ADR-024 | Approve short object paths | Long-path and opaque object evidence passed locally |
| ADR-027 | Approve Windows argv/resolver | `GetSystemDirectoryW`, final-path validation, argv round-trip, and forbidden flag rejection passed |

Optional caveat approvals:

| ADR | Suggested choice | Caveat |
|---|---|---|
| ADR-007 | Approve local CAS target mutation | SMB-specific integration still needs later proof |
| ADR-010 | Approve best-effort snapshot semantics | SMB SourceReadGuard remains unproven |
| ADR-023 | Approve fallback principle | SMB capability profile still needs lab proof |

## 0B Gate

| Choice | Meaning |
|---|---|
| B1 | Open 0B after approving enough ADRs or accepting explicit scope reductions |
| B2 | Keep 0B blocked until external lab/signing evidence is complete |

Codex recommendation: B2 until the owner has chosen packaging, SMB scope, and
scheduler scope. If P2, S2, and T2 are intentionally chosen, B1 becomes a
reasonable local-preview path.

## Required Inputs

For P1:

- signing policy: certificate store thumbprint or PFX handoff method;
- RFC3161 timestamp URL;
- clean Windows VM image/snapshot;
- transfer path for the complete Nuitka dist directory.

For S1:

- dedicated SMB share path;
- two Windows clients or VMs;
- statement that the share is safe for destructive lab fixtures;
- credential/session instructions sent through a secure channel, not committed.

For T1:

- test account or session setup for non-interactive Task Scheduler;
- wrong-SID or remote-client test path;
- permission to create and delete temporary tasks under `\MediaSyncHome-Spike\`.

## Safety Notes

- Do not commit signing certificate bytes, private keys, passwords, real NAS
  credentials, personal file paths, or production share roots.
- A deferred scope reduction is not a hidden approval. It must name the missing
  guarantee and the first-release limitation.
- 0B should not start until the owner either provides missing evidence or
  explicitly chooses the scope reductions above.
