# 0A.4 evidence artifacts

Generated on 2026-07-16 for branch `spike/0a4-sqlite-capacity`.

- `benchmark-summary.json` records the 1,000,000-row SQLite comparison for
  one-db and two-db candidates, query plans/P95, WAL/checkpoint sizes, backup
  member counts, `SQLITE_FULL` behavior, and the Codex ADR-003 recommendation.
- `unittest-output.txt` records:
  `python -m unittest discover -s tests\spikes\0a4_sqlite_capacity -v`

The SQLite database files were generated only in temporary directories and are
not committed. No production schema file was changed.
