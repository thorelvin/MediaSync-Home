# Implementeringsstatus

| Arbeidspakke/milepæl | Status | Bevis/PR | Blockers | Neste eierhandling |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `docs/ARCHITECTURE_SPIKE_REPORT.md`; branch `spike/0a0-environment-preflight` | Ingen 0A.0-blocker | Gjennomgå rapporten og velg neste arbeidspakke |
| 0A.1 — Prosess og IPC | blocked | `spikes/0a1_process_ipc/`; `tests/spikes/0a1_process_ipc/`; `artifacts/0a1/unittest-output.txt`; `artifacts/0a1/scheduler-trigger-summary.json` | Lokal IPC/Job Object og Task Scheduler same-SID trigger består; ekte non-interactive/session-policy og feil-SID/remote klient mangler | Still lab/session til rådighet eller godkjenn scope-reduksjon |
| 0A.2 — Endpoint-eierskap | blocked | `spikes/0a2_endpoint_ownership/`; `tests/spikes/0a2_endpoint_ownership/`; `artifacts/0a2/` | Lokal klassifisering/lock/takeover og endelig BLAKE3-marker bestått; to-klient SMB-lab mangler | Klargjør dedikert SMB-lab eller godkjenn scope-reduksjon |
| 0A.3 — Recovery og stier | passed | `spikes/0a3_recovery_paths/`; `tests/spikes/0a3_recovery_paths/`; `artifacts/0a3/` | Lokal NTFS/path/recovery bestått; SMB SourceReadGuard ikke kjørt uten SMB-lab | Bruk fallbackpolicy for uprovede SMB eller still SMB-lab til rådighet |
| 0A.4 — SQLite og kapasitet | passed | `spikes/0a4_sqlite_capacity/`; `tests/spikes/0a4_sqlite_capacity/`; `artifacts/0a4/` | Lokal 1M SQLite-/kapasitetsmåling bestått; ADR-003 anbefales, men eiergodkjenning gjenstår | Eier vurderer ADR-003-anbefalingen i 0A.6 |
| 0A.5 — Windows argv/pakking | blocked | `spikes/0a5_windows_packaging/`; `tests/spikes/0a5_windows_packaging/`; `artifacts/0a5/` | `GetSystemDirectoryW`/argv og minimal PySide6/BLAKE3/Nuitka-runtime bestått; pakket exe, Windows SDK/signering og ren Windows-VM mangler | Klargjør SDK/signering/VM og kjør pakket exe-smoke før ADR-028 kan anbefales |
| 0A.6 — Beslutningsreview | blocked | `docs/adr/0A_DECISION_REVIEW.md` | Eierbeslutninger, SMB-/Task Scheduler-lab og pakkemiljø mangler | Eier velger ADR-beslutninger eller scope-reduksjon |
| 0A — Samlet arkitekturbevis | blocked | 0A.0–0A.6 evidence/status docs | 0A.1/0A.2/0A.5 har åpne lab-/toolchainblockers; ADR-owner gate ikke passert | Ikke start 0B før eierporten er passert |
| 0B — Repository og kontrakter | blocked | | Avventer eiergodkjente 0A-ADR-er | Ikke start |

Tillatte milepælstatuser: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.

ADR-status følger `docs/DECISION_REGISTER.md` og er separat fra milepælstatus.
