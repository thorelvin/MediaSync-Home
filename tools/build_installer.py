from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mediasync_home import __version__  # noqa: E402
from tools.audit_dependencies import audit_installed_dependencies  # noqa: E402


EXE_NAME = "MediaSyncHome0B.exe"
INSTALLER_SCRIPT = ROOT / "installer/MediaSyncHome.iss"
LICENSE_PREFIXES = ("authors", "copying", "copyright", "license", "notice")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the per-user unsigned MediaSync Home Windows installer"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "artifacts/0b/installer-build.json",
    )
    args = parser.parse_args(argv)

    result = build_installer(
        output_dir=args.output_dir,
        work_dir=args.work_dir,
        source_dir=args.source_dir,
        python_executable=args.python_executable,
        iscc=args.iscc,
        timeout_seconds=args.timeout_seconds,
    )
    _write_json(args.evidence, _sanitize_evidence(result))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 2


def build_installer(
    *,
    output_dir: Path,
    work_dir: Path | None,
    source_dir: Path | None,
    python_executable: Path,
    iscc: Path | None,
    timeout_seconds: int,
) -> dict[str, object]:
    if os.name != "nt":
        return {"status": "BLOCKED_BY_ENVIRONMENT", "reason": "WINDOWS_REQUIRED"}

    build_root = (work_dir or _default_work_dir()).resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    compiler = find_inno_compiler(iscc)
    if compiler is None:
        return {
            "status": "BLOCKED_BY_ENVIRONMENT",
            "reason": "INNO_SETUP_COMPILER_NOT_FOUND",
        }

    package_command: list[str] | None = None
    if source_dir is None:
        package_root = build_root / "package"
        package_command = build_nuitka_command(
            python_executable=python_executable.resolve(),
            output_dir=package_root,
        )
        package_result = _run(
            package_command,
            timeout_seconds=timeout_seconds,
            env=_source_environment(),
        )
        if package_result.returncode != 0:
            return {
                "status": "FAIL",
                "reason": "NUITKA_BUILD_FAILED",
                "package_command": package_command,
                "stdout_tail": package_result.stdout[-4000:],
                "stderr_tail": package_result.stderr[-4000:],
            }
        source_dir = find_standalone_directory(package_root)
        if source_dir is None:
            return {
                "status": "FAIL",
                "reason": "NUITKA_STANDALONE_DIRECTORY_NOT_FOUND",
                "package_command": package_command,
            }
    else:
        source_dir = source_dir.resolve()

    executable = source_dir / EXE_NAME
    if not executable.is_file():
        return {
            "status": "FAIL",
            "reason": "PACKAGED_EXECUTABLE_NOT_FOUND",
            "expected_executable": str(executable),
        }

    metadata_dir = build_root / "metadata"
    metadata_summary = prepare_installer_metadata(metadata_dir)
    numeric_version = installer_numeric_version(__version__)
    installer_command = build_iscc_command(
        compiler=compiler,
        source_dir=source_dir,
        metadata_dir=metadata_dir,
        output_dir=output_dir,
        app_version=__version__,
        numeric_version=numeric_version,
    )
    compile_result = _run(installer_command, timeout_seconds=timeout_seconds)
    installer = output_dir / f"MediaSyncHome-Setup-{__version__}-unsigned.exe"
    if compile_result.returncode != 0 or not installer.is_file():
        return {
            "status": "FAIL",
            "reason": "INNO_SETUP_BUILD_FAILED",
            "installer_command": installer_command,
            "stdout_tail": compile_result.stdout[-4000:],
            "stderr_tail": compile_result.stderr[-4000:],
        }

    return {
        "status": "PASS",
        "event": "INSTALLER_BUILD_COMPLETED",
        "app_version": __version__,
        "unsigned": True,
        "install_scope": "CURRENT_USER",
        "source_directory": str(source_dir),
        "source_file_count": sum(1 for path in source_dir.rglob("*") if path.is_file()),
        "packaged_executable_sha256": _sha256(executable),
        "installer": str(installer),
        "installer_bytes": installer.stat().st_size,
        "installer_sha256": _sha256(installer),
        "package_command": package_command,
        "installer_command": installer_command,
        "metadata": metadata_summary,
    }


def build_nuitka_command(*, python_executable: Path, output_dir: Path) -> list[str]:
    return [
        str(python_executable),
        "-m",
        "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        "--windows-console-mode=attach",
        f"--output-dir={output_dir}",
        f"--output-filename={EXE_NAME}",
        "--include-package=mediasync_home",
        "--include-package=win32com",
        "--include-module=pythoncom",
        "--include-module=pywintypes",
        str(ROOT / "src/mediasync_home/__main__.py"),
    ]


def build_iscc_command(
    *,
    compiler: Path,
    source_dir: Path,
    metadata_dir: Path,
    output_dir: Path,
    app_version: str,
    numeric_version: str,
) -> list[str]:
    return [
        str(compiler),
        f"/DSourceDir={source_dir}",
        f"/DMetadataDir={metadata_dir}",
        f"/DAppVersion={app_version}",
        f"/DAppVersionNumeric={numeric_version}",
        f"/DOutputDir={output_dir}",
        str(INSTALLER_SCRIPT),
    ]


def find_inno_compiler(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    discovered = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if discovered:
        candidates.append(Path(discovered))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs/Inno Setup 6/ISCC.exe")
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Inno Setup 6/ISCC.exe")
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def find_standalone_directory(build_root: Path) -> Path | None:
    matches = sorted(
        executable.parent
        for executable in build_root.rglob(EXE_NAME)
        if executable.is_file() and executable.parent.name.endswith(".dist")
    )
    return matches[-1].resolve() if matches else None


def installer_numeric_version(version: str) -> str:
    components: list[int] = []
    for component in version.split("."):
        digits = ""
        for character in component:
            if not character.isdigit():
                break
            digits += character
        components.append(int(digits or "0"))
        if len(components) == 4:
            break
    components.extend([0] * (4 - len(components)))
    return ".".join(str(component) for component in components)


def prepare_installer_metadata(destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    audit = audit_installed_dependencies(ROOT / "requirements-dev.txt")
    if not audit.passed:
        raise RuntimeError("dependency audit must pass before building an installer")
    _write_json(destination / "dependency-manifest.json", audit.to_dict())

    notice_lines = [
        "MediaSync Home - third-party dependency notices",
        "",
        "This local unsigned alpha includes or is built with the packages below.",
        "Package license files copied from the build environment are under licenses/packages.",
        "",
    ]
    copied_license_files = 0
    licenses_root = destination / "licenses"
    for record in audit.dependencies:
        notice_lines.append(f"{record.name} {record.version} - {record.license}")
        copied_license_files += _copy_distribution_license_files(
            distribution_name=record.canonical_name,
            destination=licenses_root / record.canonical_name,
        )
    (destination / "THIRD_PARTY_NOTICES.txt").write_text(
        "\n".join(notice_lines) + "\n",
        encoding="utf-8",
    )
    (destination / "LOCAL_ALPHA_README.txt").write_text(
        f"MediaSync Home {__version__} local unsigned alpha\n\n"
        "This build is unsigned and intended for local evaluation by the current Windows user.\n"
        "Application state is stored outside the installation directory and is preserved when "
        "the app is uninstalled.\n"
        "Close MediaSync Home and wait for backup activity to finish before upgrading or "
        "uninstalling.\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE.txt")
    return {
        "dependencies": len(audit.dependencies),
        "license_files": copied_license_files,
        "product_license": "MIT",
        "notice_required": audit.notice_required,
    }


def _copy_distribution_license_files(*, distribution_name: str, destination: Path) -> int:
    distribution = metadata.distribution(distribution_name)
    candidates = distribution.files or ()
    copied = 0
    used_names: set[str] = set()
    for relative_path in candidates:
        if not relative_path.name.lower().startswith(LICENSE_PREFIXES):
            continue
        source = Path(distribution.locate_file(relative_path))
        if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target_name = relative_path.name
        if target_name.lower() in used_names:
            target_name = f"{copied:02d}-{target_name}"
        used_names.add(target_name.lower())
        shutil.copy2(source, destination / target_name)
        copied += 1
    return copied


def _run(
    command: list[str],
    *,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout_seconds,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_work_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "build/installer" / f"{timestamp}-{uuid4().hex[:8]}"


def _source_environment() -> dict[str, str]:
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    source_paths = os.pathsep.join((str(SRC), str(ROOT)))
    environment["PYTHONPATH"] = (
        source_paths if not existing else os.pathsep.join((source_paths, existing))
    )
    return environment


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sanitize_evidence(payload: object) -> object:
    replacements = {
        str(Path.home()): "<USER_HOME>",
        str(ROOT): "<REPOSITORY_ROOT>",
    }
    return _replace_evidence_paths(payload, replacements)


def _replace_evidence_paths(payload: object, replacements: dict[str, str]) -> object:
    if isinstance(payload, dict):
        return {key: _replace_evidence_paths(value, replacements) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_replace_evidence_paths(value, replacements) for value in payload]
    if not isinstance(payload, str):
        return payload
    sanitized = payload
    for original, replacement in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        sanitized = sanitized.replace(original, replacement)
        sanitized = sanitized.replace(original.replace("\\", "\\\\"), replacement)
    return sanitized


if __name__ == "__main__":
    raise SystemExit(main())
