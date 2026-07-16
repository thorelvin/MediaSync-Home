from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PURPOSE = "MEDIASYNC_ARCHITECTURE_SPIKE"
INSTALLATION_ID = "spike-installation"
MAX_LEGACY_WINDOWS_PATH = 260


class LabRootError(RuntimeError):
    pass


class CrashInjected(RuntimeError):
    pass


@dataclass(frozen=True)
class LabRoot:
    root: Path
    run_id: str

    @property
    def marker_path(self) -> Path:
        return self.root / ".mediasync_test_root"

    @property
    def control_root(self) -> Path:
        return self.root / ".mediasync" / "installations" / INSTALLATION_ID


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def root_identity(root: Path) -> str:
    resolved = root.resolve()
    material = f"{resolved.drive.lower()}|{resolved.as_posix().lower()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def create_lab_root(parent: Path | None = None, run_id: str | None = None) -> LabRoot:
    run_id = run_id or str(uuid.uuid4())
    raw_root = Path(tempfile.mkdtemp(prefix="msh-0a3-", dir=str(parent) if parent else None))
    marker = {
        "purpose": PURPOSE,
        "run_id": run_id,
        "created_utc": _utc_now(),
        "expected_root_identity": root_identity(raw_root),
        "cleanup_allowed": True,
    }
    (raw_root / ".mediasync_test_root").write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lab = LabRoot(raw_root, run_id)
    validate_lab_root(lab)
    return lab


def validate_lab_root(lab: LabRoot) -> dict[str, Any]:
    root = lab.root.resolve()
    if root.anchor == str(root):
        raise LabRootError("lab root may not be a drive root")
    marker_path = root / ".mediasync_test_root"
    if not marker_path.is_file():
        raise LabRootError("missing .mediasync_test_root marker")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("purpose") != PURPOSE:
        raise LabRootError("marker purpose mismatch")
    if marker.get("run_id") != lab.run_id:
        raise LabRootError("marker run_id mismatch")
    if marker.get("expected_root_identity") != root_identity(root):
        raise LabRootError("marker root identity mismatch")
    if marker.get("cleanup_allowed") is not True:
        raise LabRootError("cleanup is not allowed by marker")
    forbidden_names = {"Desktop", "Documents", "Pictures", "Bilder", "Skrivebord", "Dokumenter"}
    if root.name in forbidden_names:
        raise LabRootError("lab root may not be a standard user data folder")
    return marker


def safe_relative_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        raise ValueError("absolute paths are not allowed")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("relative path contains unsafe segment")
    return path


def require_under(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise LabRootError(f"path escapes lab root: {path}") from exc
    return resolved_path


def object_paths(lab: LabRoot, allocation_id: str) -> dict[str, Path]:
    shard = allocation_id[:2]
    base = lab.control_root
    return {
        "payload": base / "objects" / shard / f"{allocation_id}.payload",
        "manifest": base / "manifests" / f"{allocation_id}.json",
        "intent": base / "recovery" / allocation_id[:8] / "segment-000001.intent.jsonl",
        "journal": base / "recovery" / allocation_id[:8] / "journal.jsonl",
    }


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flush_file(path: Path) -> None:
    with path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_journal(path: Path, event: str, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"event": event, "utc": _utc_now(), **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def allocate_managed_object(lab: LabRoot, logical_relative_path: str, source: Path, role: str) -> dict[str, Any]:
    validate_lab_root(lab)
    relative = safe_relative_path(logical_relative_path)
    source = require_under(lab.root, source)
    allocation_id = uuid.uuid4().hex
    paths = object_paths(lab, allocation_id)
    paths["payload"].parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, paths["payload"])
    manifest = {
        "allocation_id": allocation_id,
        "logical_role": role,
        "original_relative_path": relative.as_posix(),
        "payload_relative_path": paths["payload"].relative_to(lab.root).as_posix(),
        "payload_sha256": file_hash(paths["payload"]),
        "size": paths["payload"].stat().st_size,
        "created_utc": _utc_now(),
    }
    write_json(paths["manifest"], manifest)
    return {"allocation_id": allocation_id, "paths": {k: str(v) for k, v in paths.items()}, "manifest": manifest}


def mirrored_control_path_length(lab: LabRoot, logical_relative_path: str) -> int:
    relative = safe_relative_path(logical_relative_path)
    mirrored = lab.control_root / "mirrored-user-tree" / relative
    return len(str(mirrored))


def make_long_relative_path(component_count: int = 9, component_len: int = 24) -> str:
    components = [f"{index:02d}-" + ("x" * (component_len - 3)) for index in range(component_count)]
    return str(Path(*components) / "image.raw")


def publish_intent(lab: LabRoot, allocation_id: str, operation: str, final_relative_path: str) -> Path:
    paths = object_paths(lab, allocation_id)
    row = {
        "allocation_id": allocation_id,
        "operation": operation,
        "final_relative_path": safe_relative_path(final_relative_path).as_posix(),
        "schema_version": 1,
        "utc": _utc_now(),
    }
    paths["intent"].parent.mkdir(parents=True, exist_ok=True)
    tmp = paths["intent"].with_suffix(".tmp")
    tmp.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, paths["intent"])
    return paths["intent"]


def _maybe_crash(point: str | None, current: str) -> None:
    if point == current:
        raise CrashInjected(current)


def fallback_replace_with_recovery(
    lab: LabRoot,
    final_relative_path: str,
    new_content: bytes,
    crash_at: str | None = None,
) -> dict[str, Any]:
    validate_lab_root(lab)
    final_rel = safe_relative_path(final_relative_path)
    final = require_under(lab.root, lab.root / final_rel)
    staging_dir = lab.control_root / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / f"{uuid.uuid4().hex}.payload"
    staging.write_bytes(new_content)

    allocation_id = uuid.uuid4().hex
    paths = object_paths(lab, allocation_id)
    append_journal(paths["journal"], "STAGING_VERIFIED", staging=str(staging.relative_to(lab.root)))
    _maybe_crash(crash_at, "before_intent")
    flush_file(staging)
    append_journal(paths["journal"], "STAGING_DURABLE")
    _maybe_crash(crash_at, "after_flush")
    publish_intent(lab, allocation_id, "FALLBACK_REPLACE", final_rel.as_posix())
    append_journal(paths["journal"], "COMMIT_INTENT_RECORDED")
    _maybe_crash(crash_at, "after_intent")

    version_manifest: dict[str, Any] | None = None
    if final.exists():
        version = allocate_managed_object(lab, final_rel.as_posix(), final, "VERSION")
        version_manifest = version["manifest"]
        append_journal(paths["journal"], "OLD_TARGET_PRESERVED", version_allocation_id=version["allocation_id"])
    _maybe_crash(crash_at, "after_preserve")

    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)
    append_journal(paths["journal"], "FILESYSTEM_APPLIED", final=str(final_rel.as_posix()))
    _maybe_crash(crash_at, "after_apply")

    final_hash = file_hash(final)
    append_journal(paths["journal"], "FINAL_VERIFIED", final_sha256=final_hash)
    _maybe_crash(crash_at, "after_verify")

    append_journal(paths["journal"], "CATALOG_RECORDED")
    append_journal(paths["journal"], "CLEANED")
    return {"allocation_id": allocation_id, "final_sha256": final_hash, "version_manifest": version_manifest}


def read_journal(journal: Path) -> list[dict[str, Any]]:
    if not journal.exists():
        return []
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]


def recover_fallback_replace(lab: LabRoot, allocation_id: str, final_relative_path: str) -> dict[str, Any]:
    validate_lab_root(lab)
    final_rel = safe_relative_path(final_relative_path)
    final = require_under(lab.root, lab.root / final_rel)
    paths = object_paths(lab, allocation_id)
    events = [item["event"] for item in read_journal(paths["journal"])]
    if "COMMIT_INTENT_RECORDED" not in events:
        return {"status": "BLOCKED", "reason": "MISSING_INTENT"}
    if "FILESYSTEM_APPLIED" not in events:
        return {"status": "SAFE_TO_RETRY_OR_KEEP_OLD", "final_exists": final.exists()}
    if not final.exists():
        return {"status": "USER_DECISION_REQUIRED", "reason": "FINAL_MISSING_AFTER_APPLY"}
    final_hash = file_hash(final)
    if "FINAL_VERIFIED" not in events:
        append_journal(paths["journal"], "FINAL_VERIFIED", final_sha256=final_hash, recovered=True)
    if "CATALOG_RECORDED" not in events:
        append_journal(paths["journal"], "CATALOG_RECORDED", recovered=True)
    return {"status": "RECOVERED", "final_sha256": final_hash}


def restore_managed_object(lab: LabRoot, allocation_id: str) -> dict[str, Any]:
    validate_lab_root(lab)
    paths = object_paths(lab, allocation_id)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    original = safe_relative_path(manifest["original_relative_path"])
    target = require_under(lab.root, lab.root / original)
    payload = require_under(lab.root, paths["payload"])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(payload, target)
    return {"restored_relative_path": original.as_posix(), "restored_sha256": file_hash(target)}


def quarantine_managed_object(lab: LabRoot, logical_relative_path: str) -> dict[str, Any]:
    validate_lab_root(lab)
    relative = safe_relative_path(logical_relative_path)
    target = require_under(lab.root, lab.root / relative)
    if not target.is_file():
        return {"status": "TARGET_NOT_FILE"}
    allocation_id = uuid.uuid4().hex
    paths = object_paths(lab, allocation_id)
    paths["payload"].parent.mkdir(parents=True, exist_ok=True)
    os.replace(target, paths["payload"])
    manifest = {
        "allocation_id": allocation_id,
        "logical_role": "QUARANTINE",
        "original_relative_path": relative.as_posix(),
        "payload_relative_path": paths["payload"].relative_to(lab.root).as_posix(),
        "payload_sha256": file_hash(paths["payload"]),
        "size": paths["payload"].stat().st_size,
        "created_utc": _utc_now(),
    }
    write_json(paths["manifest"], manifest)
    return {"status": "QUARANTINED", "allocation_id": allocation_id, "manifest": manifest}


def replace_filew_probe(lab: LabRoot) -> dict[str, Any]:
    validate_lab_root(lab)
    if os.name != "nt":
        return {"status": "BLOCKED", "reason": "NON_WINDOWS"}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReplaceFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.ReplaceFileW.restype = ctypes.c_int

    probe_dir = lab.control_root / "replacefilew-probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    final = probe_dir / "final.txt"
    replacement = probe_dir / "replacement.txt"
    backup = probe_dir / "backup.txt"
    final.write_text("old", encoding="utf-8")
    replacement.write_text("new", encoding="utf-8")
    ok = kernel32.ReplaceFileW(str(final), str(replacement), str(backup), 0, None, None)
    if not ok:
        return {"status": "BLOCKED", "reason": f"REPLACEFILEW_FAILED_{ctypes.get_last_error()}"}
    return {
        "status": "PASS",
        "same_volume": final.drive.lower() == backup.drive.lower(),
        "final_content": final.read_text(encoding="utf-8"),
        "backup_content": backup.read_text(encoding="utf-8"),
    }


def create_directory_with_recovery(lab: LabRoot, relative_path: str) -> dict[str, Any]:
    validate_lab_root(lab)
    rel = safe_relative_path(relative_path)
    target = require_under(lab.root, lab.root / rel)
    if target.exists() and not target.is_dir():
        return {"status": "TARGET_TYPE_CONFLICT"}
    target.mkdir(parents=True, exist_ok=True)
    return {"status": "DIRECTORY_CREATED", "relative_path": rel.as_posix()}


def source_guard_probe(lab: LabRoot, relative_path: str) -> dict[str, Any]:
    validate_lab_root(lab)
    rel = safe_relative_path(relative_path)
    path = require_under(lab.root, lab.root / rel)
    if os.name != "nt":
        return {"guard_level": "POST_TRANSFER_HASH_ONLY", "reason": "NON_WINDOWS"}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    handle = kernel32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, 0, invalid):
        return {"guard_level": "POST_TRANSFER_HASH_ONLY", "reason": f"OPEN_FAILED_{ctypes.get_last_error()}"}
    try:
        try:
            with path.open("ab"):
                writer_blocked = False
        except OSError:
            writer_blocked = True
        return {
            "guard_level": "DENY_WRITE_AND_DELETE" if writer_blocked else "POST_TRANSFER_HASH_ONLY",
            "writer_blocked": writer_blocked,
        }
    finally:
        kernel32.CloseHandle(handle)


def run_demo(output: Path) -> int:
    lab = create_lab_root()
    try:
        logical = make_long_relative_path()
        source = lab.root / "source.bin"
        source.write_bytes(b"original")
        managed = allocate_managed_object(lab, logical, source, "STAGING")
        final_rel = "target/final.bin"
        (lab.root / final_rel).parent.mkdir(parents=True, exist_ok=True)
        (lab.root / final_rel).write_bytes(b"old")
        result = fallback_replace_with_recovery(lab, final_rel, b"new")
        restored = restore_managed_object(lab, result["version_manifest"]["allocation_id"])
        quarantine_source = lab.root / "target" / "quarantine.bin"
        quarantine_source.write_bytes(b"quarantine")
        quarantine = quarantine_managed_object(lab, "target/quarantine.bin")
        replace_probe = replace_filew_probe(lab)
        guard = source_guard_probe(lab, "source.bin")
        summary = {
            "lab_root": "<temporary-marker-validated-lab-root>",
            "logical_path_length": len(str(lab.root / logical)),
            "mirrored_control_path_length": mirrored_control_path_length(lab, logical),
            "managed_payload_path_length": len(managed["paths"]["payload"]),
            "replace_result": result,
            "replace_filew_probe": replace_probe,
            "quarantine_result": quarantine,
            "restore_result": restored,
            "source_guard": guard,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MediaSync Home 0A.3 recovery/path spike")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        return run_demo(Path(args.output))
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
