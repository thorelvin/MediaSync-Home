from __future__ import annotations

from tools.validate_handoff import _is_ignored_scan_path


def test_handoff_scan_ignores_only_local_runtime_artifact_patterns() -> None:
    assert _is_ignored_scan_path(("build", "installer", "metadata", "LICENSE.txt"))
    assert _is_ignored_scan_path(("dist", "MediaSyncHome0B.exe"))
    assert _is_ignored_scan_path(
        ("artifacts", "local-unsigned-final3", "__main__.build", "clcache.txt")
    )
    assert _is_ignored_scan_path(
        ("artifacts", "0b", "packaged-runtime", "__main__.build", "scons-debug.py")
    )
    assert _is_ignored_scan_path(
        ("artifacts", "0b", "packaged-runtime", "__main__.dist", "LICENSE.txt")
    )
    assert _is_ignored_scan_path(
        ("artifacts", "package-manual-state", "state-migration.intent.json")
    )
    assert _is_ignored_scan_path(("artifacts", "package-manual-host.stdout.log"))

    assert not _is_ignored_scan_path(
        ("artifacts", "0b", "packaged-runtime-smoke-latest.json")
    )
    assert not _is_ignored_scan_path(("src", "local-unsigned-module.py"))
