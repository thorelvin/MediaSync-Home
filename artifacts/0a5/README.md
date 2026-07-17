# 0A.5 evidence artifacts

Generated on 2026-07-17 for branch `spike/0a5-local-nuitka-exe`.

- `demo-summary.json` records the `GetSystemDirectoryW`-resolved
  `Robocopy.exe` path, file hash/version, sanitized launch-plan shape,
  Windows argv round-trip results, forbidden switch rejection, and packaging
  preflight blockers.
- `minimal-runtime-summary.json` records a repo-local `.venv` proof that
  `PySide6==6.11.1`, `Qt 6.11.1`, `blake3==1.0.9`, `Nuitka==4.1.3`, and
  `GetSystemDirectoryW` can run in one minimal Python process.
- `nuitka-build-summary.json` records a local Nuitka standalone build and
  smoke run of `MediaSync0A5Probe.exe`; the packaged probe launched locally,
  created `QCoreApplication`, computed a BLAKE3 digest, and called
  `GetSystemDirectoryW`.
- `sdk-signing-inventory.json` records local Visual Studio/Windows SDK tool
  discovery. `cl.exe`, `rc.exe`, and `signtool.exe` were found off PATH and
  could start; the current-user certificate store had no code-signing
  certificate.
- `release-signing-plan.json` records the exact placeholder command templates,
  required owner inputs, clean-VM smoke checks, and evidence references needed
  to turn the local unsigned probe into signed release evidence later.
- `runtime-freeze.txt` records the exact local runtime/package versions used
  for this evidence.
- `unittest-output.txt` records:
  `cmd.exe /c ".venv\Scripts\python.exe -m unittest discover -s tests\spikes\0a5_windows_packaging -v > artifacts\0a5\unittest-output.txt 2>&1"`

No real Robocopy process, backup run, production adapter, signed release
installer, or clean-VM smoke test was created by this spike. The packaged
executable evidence is a local temp-built Nuitka probe only; the executable and
distribution directory are not committed. No certificate subject, thumbprint or
private key material is recorded.
