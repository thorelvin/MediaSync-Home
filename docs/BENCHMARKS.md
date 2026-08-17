# Benchmarkregister

| Dato | Build/commit | Miljø | Datasett | Baseline | MediaSync-resultat | Avvik | Råartefakt |
|---|---|---|---|---|---|---|---|
| 2026-08-17 | working tree | Windows 10.0.26200, Python 3.10.6 | 1M synthetic snapshot rows, 1k bounded batches | Previous full-materialization pipeline | 80.544 s, 12,415 rows/s, 31.3 MiB peak RSS, checksum sealed | PASS: RSS 31.3 MiB <= 400 MiB | `artifacts/0b/snapshot-pipeline-million-current.json` |
| 2026-08-17 | working tree | Windows 10.0.26200, Robocopy | 200 files x 4 KiB, 3 runs | One Robocopy process per file: 2775.242 ms median | One directory batch: 71.402 ms median | 38.868x process-amortization speedup | `artifacts/0b/robocopy-batching-current.json` |

Reproducible commands:

```powershell
.\.venv\Scripts\python.exe tools\benchmark_snapshot_pipeline.py --entries 1000000 --enforce --output artifacts\0b\snapshot-pipeline-million-current.json
.\.venv\Scripts\python.exe tools\benchmark_robocopy_batching.py --files 200 --runs 3 --output artifacts\0b\robocopy-batching-current.json
.\.venv\Scripts\python.exe tools\check_performance_gates.py --evidence artifacts\release-performance-evidence.json
```

The release gate command fails when any metric from `PERFORMANCE.md` is missing or outside budget. Hardware-dependent release metrics remain `not_run` until a complete `release-performance-evidence.json` is captured on the release machine.

Alle ytelsespåstander skal peke til reproduserbare kommandoer og råartefakter. Manglende måling skal stå som `not_run`.
