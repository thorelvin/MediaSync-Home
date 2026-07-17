from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
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
SUPPORTED_CONTROL_SCHEMA = 4
CANONICALIZATION_ALGORITHM = "JCS-RFC8785"
CHECKSUM_ALGORITHM = "BLAKE3-256"
APPLICATION = "MediaSync Home"

CLASSIFICATION_STATES = (
    "ABSENT",
    "VALID_OWNED",
    "VALID_FOREIGN",
    "VALID_READ_ONLY_NEWER_SCHEMA",
    "PARTIAL_CONTROL_AREA",
    "UNKNOWN_EMPTY_DIRECTORY",
    "UNKNOWN_NONEMPTY_DIRECTORY",
    "CASE_ALIAS_COLLISION",
    "CORRUPT_MARKER",
)


class EndpointOwnershipError(RuntimeError):
    pass


class LabRootError(EndpointOwnershipError):
    pass


try:
    from blake3 import blake3 as _blake3
except ImportError:  # pragma: no cover - exercised by environment preflight, not unit tests
    _blake3 = None


@dataclass(frozen=True)
class LabRoot:
    root: Path
    run_id: str

    @property
    def marker_path(self) -> Path:
        return self.root / ".mediasync_test_root"

    @property
    def control_dir(self) -> Path:
        return self.root / ".mediasync"


@dataclass(frozen=True)
class LockHandle:
    path: Path
    handle: int

    def close(self) -> None:
        if os.name == "nt" and self.handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def blake3_256_hex(data: bytes) -> str:
    if _blake3 is None:
        raise EndpointOwnershipError("blake3 package is required for final endpoint marker evidence")
    return _blake3(data).hexdigest()


def blake3_dependency_version() -> str:
    try:
        return importlib.metadata.version("blake3")
    except importlib.metadata.PackageNotFoundError:
        return "MISSING"


def checksum_payload(payload: dict[str, Any]) -> str:
    material = dict(payload)
    material.pop("marker_checksum", None)
    return blake3_256_hex(canonical_json_bytes(material))


def root_identity(root: Path) -> str:
    resolved = root.resolve()
    material = f"{resolved.drive.lower()}|{resolved.as_posix().lower()}"
    return blake3_256_hex(material.encode("utf-8"))


def create_lab_root(parent: Path | None = None, run_id: str | None = None) -> LabRoot:
    run_id = run_id or str(uuid.uuid4())
    raw_root = Path(tempfile.mkdtemp(prefix="msh-0a2-", dir=str(parent) if parent else None))
    marker = {
        "purpose": PURPOSE,
        "run_id": run_id,
        "created_utc": utc_now(),
        "expected_root_identity": root_identity(raw_root),
        "cleanup_allowed": True,
    }
    raw_root.joinpath(".mediasync_test_root").write_text(
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
        raise LabRootError("cleanup is not allowed")
    return marker


def require_under(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise LabRootError(f"path escapes lab root: {path}") from exc
    return resolved_path


def control_area_entries(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [item for item in root.iterdir() if item.name.casefold() == ".mediasync"]


def endpoint_marker_path(root: Path) -> Path:
    return root / ".mediasync" / "endpoint.json"


def build_endpoint_marker(
    root: Path,
    owner_installation_id: str,
    *,
    endpoint_id: str | None = None,
    control_area_id: str | None = None,
    ownership_epoch: int = 1,
    schema_version: int = SUPPORTED_CONTROL_SCHEMA,
    root_hash: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    marker = {
        "control_schema_version": schema_version,
        "endpoint_id": endpoint_id or str(uuid.uuid4()),
        "control_area_id": control_area_id or str(uuid.uuid4()),
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
        "ownership_mode": "EXCLUSIVE_WRITER",
        "created_utc": now,
        "updated_utc": now,
        "expected_volume_id": None,
        "expected_share": None,
        "root_identity_hash_algorithm": CHECKSUM_ALGORITHM,
        "root_identity_hash": root_hash or root_identity(root),
        "latest_ownership_record": f"ownership/epoch-{ownership_epoch:08d}.json",
        "canonicalization_algorithm": CANONICALIZATION_ALGORITHM,
        "marker_checksum_algorithm": CHECKSUM_ALGORITHM,
        "application": APPLICATION,
    }
    marker["marker_checksum"] = checksum_payload(marker)
    return marker


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_endpoint_control_area(
    lab: LabRoot,
    owner_installation_id: str,
    *,
    ownership_epoch: int = 1,
    schema_version: int = SUPPORTED_CONTROL_SCHEMA,
) -> dict[str, Any]:
    validate_lab_root(lab)
    marker = build_endpoint_marker(
        lab.root,
        owner_installation_id,
        ownership_epoch=ownership_epoch,
        schema_version=schema_version,
    )
    ownership_record = {
        "endpoint_id": marker["endpoint_id"],
        "owner_installation_id": owner_installation_id,
        "ownership_epoch": ownership_epoch,
        "created_utc": utc_now(),
        "event": "OWNER_REGISTERED",
    }
    write_json(lab.control_dir / marker["latest_ownership_record"], ownership_record)
    (lab.control_dir / "locks").mkdir(parents=True, exist_ok=True)
    (lab.control_dir / "installations" / owner_installation_id[:8]).mkdir(parents=True, exist_ok=True)
    write_json(lab.control_dir / "endpoint.json", marker)
    return marker


def read_marker(root: Path) -> dict[str, Any]:
    return json.loads(endpoint_marker_path(root).read_text(encoding="utf-8"))


def classify_control_area(root: Path, local_installation_id: str) -> dict[str, Any]:
    entries = control_area_entries(root)
    if not entries:
        return {"state": "ABSENT", "exclude_from_snapshot": False, "mutating_allowed": False}
    if len(entries) > 1 or entries[0].name != ".mediasync":
        return {"state": "CASE_ALIAS_COLLISION", "exclude_from_snapshot": False, "mutating_allowed": False}
    control_dir = entries[0]
    if not control_dir.is_dir():
        return {"state": "UNKNOWN_NONEMPTY_DIRECTORY", "exclude_from_snapshot": False, "mutating_allowed": False}
    marker_path = control_dir / "endpoint.json"
    if not marker_path.exists():
        children = list(control_dir.iterdir())
        if not children:
            return {"state": "UNKNOWN_EMPTY_DIRECTORY", "exclude_from_snapshot": False, "mutating_allowed": False}
        names = {child.name for child in children}
        if names & {"ownership", "locks", "installations"}:
            return {"state": "PARTIAL_CONTROL_AREA", "exclude_from_snapshot": False, "mutating_allowed": False}
        return {"state": "UNKNOWN_NONEMPTY_DIRECTORY", "exclude_from_snapshot": False, "mutating_allowed": False}

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"state": "CORRUPT_MARKER", "exclude_from_snapshot": False, "mutating_allowed": False}

    required = {
        "control_schema_version",
        "endpoint_id",
        "control_area_id",
        "owner_installation_id",
        "ownership_epoch",
        "ownership_mode",
        "root_identity_hash",
        "latest_ownership_record",
        "canonicalization_algorithm",
        "marker_checksum_algorithm",
        "marker_checksum",
        "application",
    }
    if not required.issubset(marker):
        return {"state": "CORRUPT_MARKER", "exclude_from_snapshot": False, "mutating_allowed": False}
    if marker.get("application") != APPLICATION:
        return {"state": "CORRUPT_MARKER", "exclude_from_snapshot": False, "mutating_allowed": False}
    if marker["control_schema_version"] > SUPPORTED_CONTROL_SCHEMA:
        return {"state": "VALID_READ_ONLY_NEWER_SCHEMA", "exclude_from_snapshot": True, "mutating_allowed": False}
    if marker.get("marker_checksum") != checksum_payload(marker):
        return {"state": "CORRUPT_MARKER", "exclude_from_snapshot": False, "mutating_allowed": False}
    if marker.get("root_identity_hash") != root_identity(root):
        return {"state": "PARTIAL_CONTROL_AREA", "exclude_from_snapshot": False, "mutating_allowed": False}
    if not (control_dir / marker["latest_ownership_record"]).is_file():
        return {"state": "PARTIAL_CONTROL_AREA", "exclude_from_snapshot": False, "mutating_allowed": False}
    if marker["owner_installation_id"] != local_installation_id:
        return {
            "state": "VALID_FOREIGN",
            "exclude_from_snapshot": True,
            "mutating_allowed": False,
            "ownership_epoch": marker["ownership_epoch"],
        }
    return {
        "state": "VALID_OWNED",
        "exclude_from_snapshot": True,
        "mutating_allowed": True,
        "ownership_epoch": marker["ownership_epoch"],
    }


def acquire_mutation_lock(root: Path) -> LockHandle:
    if os.name != "nt":
        raise EndpointOwnershipError("local lock probe requires Windows")
    lock_path = root / ".mediasync" / "locks" / "mutation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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

    GENERIC_READ_WRITE = 0xC0000000
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    handle = kernel32.CreateFileW(str(lock_path), GENERIC_READ_WRITE, 0, None, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, 0, invalid):
        raise EndpointOwnershipError(f"mutation.lock unavailable: {ctypes.get_last_error()}")
    return LockHandle(lock_path, int(handle))


def takeover_foreign_control_area(lab: LabRoot, new_owner_installation_id: str) -> dict[str, Any]:
    validate_lab_root(lab)
    lock = acquire_mutation_lock(lab.root)
    try:
        marker = read_marker(lab.root)
        previous_epoch = int(marker["ownership_epoch"])
        new_epoch = previous_epoch + 1
        new_marker = build_endpoint_marker(
            lab.root,
            new_owner_installation_id,
            endpoint_id=marker["endpoint_id"],
            control_area_id=marker["control_area_id"],
            ownership_epoch=new_epoch,
        )
        write_json(
            lab.control_dir / f"ownership/epoch-{new_epoch:08d}.json",
            {
                "endpoint_id": marker["endpoint_id"],
                "old_owner_installation_id": marker["owner_installation_id"],
                "owner_installation_id": new_owner_installation_id,
                "ownership_epoch": new_epoch,
                "created_utc": utc_now(),
                "event": "CONTROLLED_TAKEOVER",
            },
        )
        write_json(lab.control_dir / "endpoint.json", new_marker)
        return {"status": "TAKEN_OVER", "previous_epoch": previous_epoch, "new_epoch": new_epoch}
    finally:
        lock.close()


def permit_is_current(permit: dict[str, Any], marker: dict[str, Any]) -> bool:
    return (
        permit.get("owner_installation_id") == marker.get("owner_installation_id")
        and permit.get("ownership_epoch") == marker.get("ownership_epoch")
        and permit.get("endpoint_id") == marker.get("endpoint_id")
    )


def cleanup_installation_namespace(lab: LabRoot, acting_installation_id: str, target_installation_id: str) -> dict[str, Any]:
    validate_lab_root(lab)
    if acting_installation_id != target_installation_id:
        return {"status": "REFUSED_FOREIGN_NAMESPACE"}
    target = require_under(lab.root, lab.control_dir / "installations" / target_installation_id[:8])
    if target.exists():
        shutil.rmtree(target)
    return {"status": "CLEANED_OWN_NAMESPACE"}


def classification_demo_states() -> dict[str, str]:
    states: dict[str, str] = {}

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        states["absent"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        lab.control_dir.mkdir()
        states["unknown_empty"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        lab.control_dir.mkdir()
        (lab.control_dir / "user-file.txt").write_text("not control metadata", encoding="utf-8")
        states["unknown_nonempty"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        (lab.root / ".MEDIASYNC").mkdir()
        states["case_alias_collision"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        (lab.control_dir / "locks").mkdir(parents=True)
        states["partial_control_area"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        lab.control_dir.mkdir()
        (lab.control_dir / "endpoint.json").write_text("{bad-json", encoding="utf-8")
        states["corrupt_marker"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        write_endpoint_control_area(lab, owner)
        states["valid_owned"] = classify_control_area(lab.root, owner)["state"]
        states["valid_foreign"] = classify_control_area(lab.root, str(uuid.uuid4()))["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    lab = create_lab_root()
    try:
        owner = str(uuid.uuid4())
        write_endpoint_control_area(lab, owner, schema_version=SUPPORTED_CONTROL_SCHEMA + 1)
        states["valid_read_only_newer_schema"] = classify_control_area(lab.root, owner)["state"]
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)

    return states


def run_demo(output: Path) -> int:
    lab = create_lab_root()
    local_owner = str(uuid.uuid4())
    foreign_owner = str(uuid.uuid4())
    try:
        write_endpoint_control_area(lab, local_owner)
        first_lock = acquire_mutation_lock(lab.root)
        try:
            try:
                second_lock_blocked = False
                acquire_mutation_lock(lab.root)
            except EndpointOwnershipError:
                second_lock_blocked = True
        finally:
            first_lock.close()
        old_marker = read_marker(lab.root)
        old_permit = {
            "endpoint_id": old_marker["endpoint_id"],
            "owner_installation_id": old_marker["owner_installation_id"],
            "ownership_epoch": old_marker["ownership_epoch"],
        }
        takeover = takeover_foreign_control_area(lab, foreign_owner)
        new_marker = read_marker(lab.root)
        summary = {
            "lab_root": "<temporary-marker-validated-lab-root>",
            "classification_states": classification_demo_states(),
            "local_lock_second_open_blocked": second_lock_blocked,
            "takeover": takeover,
            "old_permit_current_after_takeover": permit_is_current(old_permit, new_marker),
            "checksum_algorithm": CHECKSUM_ALGORITHM,
            "canonicalization_algorithm": CANONICALIZATION_ALGORITHM,
            "blake3_dependency_version": blake3_dependency_version(),
            "smb_cross_machine": "BLOCKED_BY_ENVIRONMENT",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    finally:
        shutil.rmtree(lab.root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MediaSync Home 0A.2 endpoint ownership local spike")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        return run_demo(Path(args.output))
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
