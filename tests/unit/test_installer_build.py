from __future__ import annotations

import json
from pathlib import Path

from tools.build_installer import (
    EXE_NAME,
    INSTALLER_SCRIPT,
    build_iscc_command,
    build_nuitka_command,
    installer_numeric_version,
    _sanitize_evidence as sanitize_build_evidence,
)
from tools.installer_smoke import (
    _last_json_object,
    _sanitize_evidence as sanitize_smoke_evidence,
)


def test_installer_definition_is_per_user_and_preserves_external_state() -> None:
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in script
    assert "DefaultDirName={localappdata}\\Programs\\MediaSync Home" in script
    assert "CloseApplications=no" in script
    assert "IsMediaSyncRunning" in script
    assert "--shutdown-local-preview-host --timeout-seconds 15" in script
    assert "--cleanup-owned-scheduled-tasks" in script
    assert "CleanupOwnedScheduledTasks" in script
    assert "[UninstallDelete]" not in script
    assert "{localappdata}\\MediaSyncHome" not in script
    assert "Norwegian.isl" in script
    assert 'Source: "{#MetadataDir}\\LICENSE.txt"' in script


def test_repository_has_mit_license() -> None:
    license_text = (INSTALLER_SCRIPT.parents[1] / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 MediaSync Home contributors" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text


def test_nuitka_installer_build_uses_product_entrypoint_and_gui_attach_mode(tmp_path: Path) -> None:
    command = build_nuitka_command(
        python_executable=tmp_path / "python.exe",
        output_dir=tmp_path / "package",
    )

    assert "--standalone" in command
    assert "--windows-console-mode=attach" in command
    assert "--include-package=mediasync_home" in command
    assert command[-1].endswith("src\\mediasync_home\\__main__.py")
    assert f"--output-filename={EXE_NAME}" in command


def test_iscc_command_passes_release_inputs(tmp_path: Path) -> None:
    command = build_iscc_command(
        compiler=tmp_path / "ISCC.exe",
        source_dir=tmp_path / "source",
        metadata_dir=tmp_path / "metadata",
        output_dir=tmp_path / "output",
        app_version="0.1.0",
        numeric_version="0.1.0.0",
    )

    assert "/DAppVersion=0.1.0" in command
    assert "/DAppVersionNumeric=0.1.0.0" in command
    assert command[-1] == str(INSTALLER_SCRIPT)


def test_installer_numeric_version_has_four_components() -> None:
    assert installer_numeric_version("0.1.0") == "0.1.0.0"
    assert installer_numeric_version("2.4.1-alpha3") == "2.4.1.0"


def test_installer_smoke_reads_last_json_object() -> None:
    assert _last_json_object('noise\n{"accepted":true}\n') == {"accepted": True}
    assert _last_json_object("noise only") is None


def test_installer_evidence_removes_home_from_nested_json_text() -> None:
    home = str(Path.home())
    payload = {"path": home, "nested_json": json.dumps({"path": home})}

    build_evidence = json.dumps(sanitize_build_evidence(payload))
    smoke_evidence = json.dumps(sanitize_smoke_evidence(payload))

    assert home not in build_evidence
    assert home not in smoke_evidence
    assert "<USER_HOME>" in build_evidence
    assert "<USER_HOME>" in smoke_evidence
