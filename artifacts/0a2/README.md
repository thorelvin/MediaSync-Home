# 0A.2 evidence artifacts

Generated on 2026-07-17 for branch `spike/0a2-blake3-marker-evidence`.

- `demo-summary.json` records the local `.mediasync` classification states,
  marker-validated lab root use, local exclusive `mutation.lock` behavior,
  controlled takeover epoch increment, stale permit rejection, and the
  cross-machine SMB blocker.
- `unittest-output.txt` records:
  `python -m unittest discover -s tests\spikes\0a2_endpoint_ownership -v`

This spike mutates only temporary marker-validated lab roots. It does not prove
two-client SMB behavior. The endpoint marker checksum/root identity evidence now
uses `BLAKE3-256` over `JCS-RFC8785` canonical JSON in a repo-local `.venv` with
`blake3==1.0.9`.
