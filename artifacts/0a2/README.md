# 0A.2 evidence artifacts

Generated on 2026-07-16 for branch `spike/0a2-endpoint-ownership-local`.

- `demo-summary.json` records the local `.mediasync` classification states,
  marker-validated lab root use, local exclusive `mutation.lock` behavior,
  controlled takeover epoch increment, stale permit rejection, and the
  cross-machine SMB blocker.
- `unittest-output.txt` records:
  `python -m unittest discover -s tests\spikes\0a2_endpoint_ownership -v`

This spike mutates only temporary marker-validated lab roots. It does not prove
two-client SMB behavior, and it uses a spike-local SHA-256 checksum stand-in
because the final BLAKE3 dependency is not available in the current environment.
