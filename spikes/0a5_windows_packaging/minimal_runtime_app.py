from __future__ import annotations

import ctypes
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any

import blake3 as blake3_module
from PySide6 import __version__ as pyside6_module_version
from PySide6.QtCore import QCoreApplication, QLibraryInfo, qVersion


def distribution_version(distribution: str, fallback: str | None = None) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return fallback


def get_system_directory() -> str:
    if os.name != "nt":
        raise RuntimeError("GetSystemDirectoryW requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if size == 0:
        raise RuntimeError(f"GetSystemDirectoryW failed: {ctypes.get_last_error()}")
    if size >= len(buffer):
        raise RuntimeError("GetSystemDirectoryW output exceeded probe buffer")
    return str(Path(buffer.value))


def runtime_payload() -> dict[str, Any]:
    app = QCoreApplication.instance()
    created_app = app is None
    if app is None:
        app = QCoreApplication(["mediasync-0a5-packaged-probe"])
    QCoreApplication.setApplicationName("MediaSyncHome0A5PackagedProbe")

    system_directory = get_system_directory()
    digest_input = {
        "application_name": QCoreApplication.applicationName(),
        "qt_version": qVersion(),
        "system_directory_basename": Path(system_directory).name,
    }
    digest = blake3_module.blake3(
        json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "PASS",
        "application_name": QCoreApplication.applicationName(),
        "python_runtime": ".".join(str(part) for part in sys.version_info[:3]),
        "pyside6_version": distribution_version("PySide6", pyside6_module_version),
        "qt_version": qVersion(),
        "qt_prefix_path_known": bool(QLibraryInfo.path(QLibraryInfo.LibraryPath.PrefixPath)),
        "blake3_version": distribution_version("blake3", getattr(blake3_module, "__version__", None)),
        "win32_get_system_directory_basename": Path(system_directory).name,
        "probe_digest_algorithm": "BLAKE3-256",
        "probe_digest": digest,
        "qcoreapplication_created": created_app,
    }


def main() -> int:
    print(json.dumps(runtime_payload(), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
