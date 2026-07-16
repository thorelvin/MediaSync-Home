# 0A.5 evidence artifacts

Generated on 2026-07-16 for branch `spike/0a5-windows-argv-and-packaging`.

- `demo-summary.json` records the `GetSystemDirectoryW`-resolved
  `Robocopy.exe` path, file hash/version, sanitized launch-plan shape,
  Windows argv round-trip results, forbidden switch rejection, and packaging
  preflight blockers.
- `unittest-output.txt` records:
  `python -m unittest discover -s tests\spikes\0a5_windows_packaging -v`

No real Robocopy process, backup run, production adapter, packaged executable,
or clean-VM smoke test was created by this spike.
