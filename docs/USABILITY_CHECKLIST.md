# Brukervennlighetssjekkliste

| Dato | Build/commit | Scenario | Deltakerprofil | Resultat | Funn | Oppfølging |
|---|---|---|---|---|---|---|
| 2026-07-31 | working tree after `cddb87c` | Select a long source and target at 900×560, 1000×650, and 1120×700; add three targets, remove the middle target, go back, and create the remaining two-target draft | Automated Qt keyboard/mouse interaction and rendered-layout inspection | passed | All controls responded; long paths wrapped; dashboard/activity horizontal overflow remained zero; the target step stayed open until explicit Continue | Covered by `tests/gui/test_pyside_shell.py`; rendered evidence: `docs/assets/target-selection-dark.png` and `docs/assets/target-selection-light.png` |

Minimumsscenarier følger §8.30 og §21.10. Registrer faktiske observasjoner; en ikke gjennomført test skal stå som `not_run`, aldri `passed`.
