# Implementeringsstatus

| Arbeidspakke/milepæl | Status | Bevis/PR | Blockers | Neste eierhandling |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `docs/ARCHITECTURE_SPIKE_REPORT.md`; branch `spike/0a0-environment-preflight` | Ingen 0A.0-blocker | Gjennomgå rapporten og velg neste arbeidspakke |
| 0A.1 — Prosess og IPC | blocked | `spikes/0a1_process_ipc/`; `tests/spikes/0a1_process_ipc/`; `artifacts/0a1/unittest-output.txt`; `artifacts/0a1/scheduler-trigger-summary.json` | Lokal IPC/Job Object og Task Scheduler same-SID trigger består; ekte non-interactive/session-policy og feil-SID/remote klient mangler | Scope-reduksjon valgt 2026-07-17: same-user startup først; lab trengs senere for full automasjon |
| 0A.2 — Endpoint-eierskap | blocked | `spikes/0a2_endpoint_ownership/`; `tests/spikes/0a2_endpoint_ownership/`; `artifacts/0a2/` | Lokal klassifisering/lock/takeover og endelig BLAKE3-marker bestått; to-klient SMB-lab mangler | Scope-reduksjon valgt 2026-07-17: lokal-only første release; SMB-lab trengs senere for writable SMB |
| 0A.3 — Recovery og stier | passed | `spikes/0a3_recovery_paths/`; `tests/spikes/0a3_recovery_paths/`; `artifacts/0a3/` | Lokal NTFS/path/recovery bestått; SMB SourceReadGuard ikke kjørt uten SMB-lab | Bruk fallbackpolicy for uprovede SMB eller still SMB-lab til rådighet |
| 0A.4 — SQLite og kapasitet | passed | `spikes/0a4_sqlite_capacity/`; `tests/spikes/0a4_sqlite_capacity/`; `artifacts/0a4/` | Lokal 1M SQLite-/kapasitetsmåling bestått; ADR-003 eiergodkjent 2026-07-17 | Bruk to lokale SQLite-databaser med eksplisitte handoffs i 0B |
| 0A.5 — Windows argv/pakking | blocked | `spikes/0a5_windows_packaging/`; `tests/spikes/0a5_windows_packaging/`; `artifacts/0a5/` | `GetSystemDirectoryW`/argv, minimal runtime, lokal Nuitka exe-smoke, SDK-tool-inventory og release-signing-plan bestått; signeringssertifikat/signert release og ren Windows-VM mangler | Scope-reduksjon valgt 2026-07-17: lokal usignert preview først; full release krever signering og ren VM senere |
| 0A.6 — Beslutningsreview | passed | `docs/adr/0A_DECISION_REVIEW.md`; `docs/adr/owner-decision-intake.current.json` | Eierbeslutninger registrert 2026-07-17; full SMB-/Task Scheduler-/signeringsevidens er eksplisitt utsatt | Åpne 0B med lokale scope-begrensninger |
| 0A — Samlet arkitekturbevis | passed | 0A.0–0A.6 evidence/status docs | 0A owner gate passert for scoped local-preview path; full SMB/signering/non-interactive automasjon er utsatt | Fortsett 0B uten å påstå utsatte garantier |
| 0B — Repository og kontrakter | in_progress | `AGENTS.md`; `docs/CODEX_START_PROMPT.md`; `tools/validate_contracts.py`; `tests/architecture/test_contract_validation.py`; `src/mediasync_home/`; `scripts/run_role.py`; `tests/unit/test_bootstrap_roles.py`; `tests/unit/test_dev_runner.py`; `tests/architecture/test_repository_structure.py` | 0B-work order, draft contract validation, process entrypoints, composition roots og capability-typed port skeleton er etablert; ingen kontrakt er frosset, og ekte IPC/GUI/database er ikke implementert ennå | Neste slice: minimal Engine Host/GUI IPC handshake uten muterende commands |

Tillatte milepælstatuser: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.

ADR-status følger `docs/DECISION_REGISTER.md` og er separat fra milepælstatus.
