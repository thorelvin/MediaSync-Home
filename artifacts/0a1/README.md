# 0A.1 evidence artifacts

Generated on 2026-07-17 for branch `spike/0a1-task-scheduler-trigger`.

- `unittest-output.txt` records the Windows-only spike test run:
  `cmd.exe /c "set MEDIASYNC_RUN_TASKSCHEDULER_SPIKE=1&& python -m unittest discover -s tests\spikes\0a1_process_ipc -v > artifacts\0a1\unittest-output.txt 2>&1"`
- `scheduler-trigger-summary.json` records a real `schtasks /Create` +
  `schtasks /Run` probe under `\MediaSyncHome-Spike\<run-id>\TriggerProbe`.
  The task delivered one same-SID trigger command to the local spike host and
  was deleted together with its dedicated run folder.

The artifact is sanitized: it does not include raw user SID, account name, credentials,
personal file paths, production NAS details, or IPC payload secrets.
