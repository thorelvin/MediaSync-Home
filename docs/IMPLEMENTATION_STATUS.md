# Implementeringsstatus

| Arbeidspakke/milepæl | Status | Bevis/PR | Blockers | Neste eierhandling |
|---|---|---|---|---|
| 0A.0 — Miljøpreflight | passed | `docs/ARCHITECTURE_SPIKE_REPORT.md`; branch `spike/0a0-environment-preflight` | Ingen 0A.0-blocker | Gjennomgå rapporten og velg neste arbeidspakke |
| 0A.1 — Prosess og IPC | blocked | `spikes/0a1_process_ipc/`; `tests/spikes/0a1_process_ipc/`; `artifacts/0a1/unittest-output.txt` | Lokal IPC/Job Object-fixture består; ekte non-interactive Task Scheduler-session og feil-SID/remote klient mangler | Still lab/session til rådighet eller godkjenn scope-reduksjon |
| 0A.2 — Endpoint-eierskap | blocked | | Avventer eierstart og egnet to-klient SMB-lab | Klargjør dedikert SMB-lab eller godkjenn lokal delscope |
| 0A.3 — Recovery og stier | passed | `spikes/0a3_recovery_paths/`; `tests/spikes/0a3_recovery_paths/`; `artifacts/0a3/` | Lokal NTFS/path/recovery bestått; SMB SourceReadGuard ikke kjørt uten SMB-lab | Bruk fallbackpolicy for uprovede SMB eller still SMB-lab til rådighet |
| 0A.4 — SQLite og kapasitet | blocked | | Avventer eierstart og lokal benchmarkfixture | Åpne arbeidspakken manuelt etter ønsket rekkefølge |
| 0A.5 — Windows argv/pakking | blocked | | Pakkebevis avventer PySide6/BLAKE3/Nuitka/Windows SDK og ren Windows-VM; argv-delen er lokalt kjørbar | Klargjør toolchain/VM før pakkebevis |
| 0A.6 — Beslutningsreview | blocked | | Avventer tilgjengelige 0A-bevis | Eier gjennomgår ADR-er etter 0A.1–0A.5 |
| 0A — Samlet arkitekturbevis | in_progress | 0A.0 passed | Gjenstår 0A.1–0A.6 | Fortsett én arbeidspakke om gangen |
| 0B — Repository og kontrakter | blocked | | Avventer eiergodkjente 0A-ADR-er | Ikke start |

Tillatte milepælstatuser: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.

ADR-status følger `docs/DECISION_REGISTER.md` og er separat fra milepælstatus.
