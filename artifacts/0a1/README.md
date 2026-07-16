# 0A.1 evidence artifacts

Generated on 2026-07-16 for branch `spike/0a1-process-and-ipc`.

- `unittest-output.txt` records the Windows-only spike test run:
  `python -m unittest discover -s tests\spikes\0a1_process_ipc -v`

The artifact is sanitized: it does not include raw user SID, account name, credentials,
personal file paths, production NAS details, or IPC payload secrets.
