from __future__ import annotations

from pathlib import Path

from mediasync_home.adapters.selected_directory_identity import (
    LocalSelectedDirectoryIdentityProbe,
)
from mediasync_home.application.selected_directory_identity import (
    StorageIdentityTrust,
)


def test_local_probe_confirms_two_temp_directories_share_storage_device(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    probe = LocalSelectedDirectoryIdentityProbe()

    source_evidence = probe.inspect_directory(str(source))
    target_evidence = probe.inspect_directory(str(target))

    assert source_evidence.object_identity_key != target_evidence.object_identity_key
    assert source_evidence.final_path != target_evidence.final_path
    assert source_evidence.storage_identity_trust is StorageIdentityTrust.CONFIRMED
    assert target_evidence.storage_identity_trust is StorageIdentityTrust.CONFIRMED
    assert source_evidence.storage_identity_key == target_evidence.storage_identity_key
