from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from uuid import UUID

from blake3 import blake3

from mediasync_home.adapters.endpoint_leases import (
    EndpointLeaseUnavailable,
    EndpointLockHandle,
    EndpointLockOpener,
    Win32EndpointLockOpener,
)
from mediasync_home.adapters.local_endpoint_classifier import (
    BLAKE3_ALGORITHM,
    CONTROL_DIRECTORY_NAME,
    ENDPOINT_MARKER_NAME,
    LocalEndpointClassificationError,
    LocalEndpointControlAreaClassifier,
    endpoint_marker_checksum,
    load_validated_endpoint_marker_payload,
    local_root_identity_hash,
)
from mediasync_home.adapters.reparse_guard import (
    LocalFilesystemReparsePathProbe,
    ReparseGuardError,
    ReparsePathProbe,
)
from mediasync_home.adapters.sqlite.endpoint_roots import local_path_from_file_uri
from mediasync_home.application.endpoint_classification import (
    EndpointControlAreaClassification,
    EndpointControlAreaState,
)
from mediasync_home.application.endpoint_takeover import (
    EndpointTakeoverCandidate,
    EndpointTakeoverError,
    PreparedEndpointTakeover,
)


_MAX_RECOVERY_ENTRIES = 10_000


class LocalEndpointTakeoverFilesystem:
    def __init__(
        self,
        *,
        classifier: LocalEndpointControlAreaClassifier | None = None,
        probe: ReparsePathProbe | None = None,
        lock_opener: EndpointLockOpener | None = None,
    ) -> None:
        self._probe = probe or LocalFilesystemReparsePathProbe()
        self._classifier = classifier or LocalEndpointControlAreaClassifier(
            probe=self._probe
        )
        self._lock_opener = lock_opener or Win32EndpointLockOpener()

    def prepare_controlled_takeover(
        self,
        candidate: EndpointTakeoverCandidate,
        *,
        intent_id: str,
        resulting_endpoint_revision_id: str,
        owner_installation_id: str,
        created_utc: str,
    ) -> PreparedEndpointTakeover:
        _require_uuid(intent_id, "ENDPOINT_TAKEOVER_INTENT_ID_INVALID")
        _require_uuid(candidate.endpoint_id, "ENDPOINT_TAKEOVER_ENDPOINT_ID_INVALID")
        _require_uuid(
            resulting_endpoint_revision_id,
            "ENDPOINT_TAKEOVER_ENDPOINT_REVISION_ID_INVALID",
        )
        _require_uuid(owner_installation_id, "ENDPOINT_TAKEOVER_OWNER_ID_INVALID")
        if owner_installation_id == candidate.foreign_owner_installation_id:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_OWNER_NOT_FOREIGN",
                "Refresh endpoint ownership before starting a controlled takeover.",
                retryable=False,
            )
        root = _local_root(candidate.root_uri)
        lock = self._acquire_lock(root)
        try:
            classification = self._classify(root, owner_installation_id)
            self._require_matching_foreign_classification(classification, candidate)
            self._require_no_foreign_recovery(
                root, candidate.foreign_owner_installation_id
            )
            raw_marker = load_validated_endpoint_marker_payload(root, probe=self._probe)
            if raw_marker.get("marker_checksum") != candidate.marker_checksum:
                raise _takeover_error(
                    "ENDPOINT_TAKEOVER_MARKER_CHANGED",
                    "Refresh endpoint details before confirming takeover again.",
                    retryable=False,
                )
            self._require_live_lock(lock)
        except LocalEndpointClassificationError as exc:
            raise _takeover_error(
                exc.validation_code, exc.next_action, retryable=True
            ) from exc
        finally:
            lock.close()

        new_epoch = candidate.foreign_ownership_epoch + 1
        ownership_record_path = f"ownership/epoch-{new_epoch:08d}.json"
        takeover_record_path = f"ownership/takeover-{intent_id}.json"
        takeover_payload = {
            "intent_id": intent_id,
            "endpoint_id": candidate.endpoint_id,
            "control_area_id": candidate.control_area_id,
            "event": "CONTROLLED_TAKEOVER_INTENT",
            "previous_owner_installation_id": candidate.foreign_owner_installation_id,
            "previous_ownership_epoch": candidate.foreign_ownership_epoch,
            "owner_installation_id": owner_installation_id,
            "ownership_epoch": new_epoch,
            "created_utc": created_utc,
        }
        ownership_payload = {
            "endpoint_id": candidate.endpoint_id,
            "owner_installation_id": owner_installation_id,
            "ownership_epoch": new_epoch,
            "created_utc": created_utc,
            "event": "CONTROLLED_TAKEOVER",
            "previous_owner_installation_id": candidate.foreign_owner_installation_id,
            "previous_ownership_epoch": candidate.foreign_ownership_epoch,
            "takeover_intent_record": takeover_record_path,
        }
        marker_payload = dict(raw_marker)
        marker_payload.update(
            {
                "owner_installation_id": owner_installation_id,
                "ownership_epoch": new_epoch,
                "updated_utc": created_utc,
                "latest_ownership_record": ownership_record_path,
            }
        )
        marker_payload["marker_checksum"] = endpoint_marker_checksum(marker_payload)
        probe_token = blake3(
            f"{intent_id}:{candidate.endpoint_id}".encode()
        ).hexdigest()
        return PreparedEndpointTakeover(
            target_ordinal=candidate.target_ordinal,
            endpoint_id=candidate.endpoint_id,
            source_endpoint_revision_id=candidate.endpoint_revision_id,
            resulting_endpoint_revision_id=resulting_endpoint_revision_id,
            resulting_endpoint_generation=candidate.endpoint_generation + 1,
            display_name=candidate.display_name,
            root_uri=candidate.root_uri,
            control_area_id=candidate.control_area_id,
            foreign_owner_installation_id=candidate.foreign_owner_installation_id,
            foreign_ownership_epoch=candidate.foreign_ownership_epoch,
            owner_installation_id=owner_installation_id,
            ownership_epoch=new_epoch,
            root_identity_hash_algorithm=candidate.root_identity_hash_algorithm,
            root_identity_hash=candidate.root_identity_hash,
            old_marker_checksum_algorithm=candidate.marker_checksum_algorithm,
            old_marker_checksum=candidate.marker_checksum,
            marker_checksum_algorithm=BLAKE3_ALGORITHM,
            marker_checksum=str(marker_payload["marker_checksum"]),
            marker_payload_json=_canonical_json(marker_payload),
            ownership_record_path=ownership_record_path,
            ownership_payload_json=_canonical_json(ownership_payload),
            takeover_record_path=takeover_record_path,
            takeover_payload_json=_canonical_json(takeover_payload),
            probe_token=probe_token,
        )

    def apply_prepared_takeover(
        self,
        prepared: PreparedEndpointTakeover,
        *,
        intent_id: str,
    ) -> None:
        _require_uuid(intent_id, "ENDPOINT_TAKEOVER_INTENT_ID_INVALID")
        root = _local_root(prepared.root_uri)
        self._require_matching_root_identity(root, prepared)
        lock = self._acquire_lock(root)
        try:
            self._require_live_lock(lock)
            classification = self._classify(root, prepared.owner_installation_id)
            self._require_no_foreign_recovery(
                root,
                prepared.foreign_owner_installation_id,
            )
            if classification.state is EndpointControlAreaState.VALID_OWNED:
                self._require_matching_owned_classification(classification, prepared)
                self._ensure_takeover_files(root, prepared)
                self._cleanup_exact_temp_marker(root, prepared, intent_id=intent_id)
            elif classification.state is EndpointControlAreaState.VALID_FOREIGN:
                self._require_matching_prepared_foreign_classification(
                    classification,
                    prepared,
                )
                self._ensure_takeover_files(root, prepared)
                self._require_live_lock(lock)
                self._publish_marker(root, prepared, intent_id=intent_id)
                self._require_live_lock(lock)
                classification = self._classify(root, prepared.owner_installation_id)
                self._require_matching_owned_classification(classification, prepared)
            else:
                raise _takeover_error(
                    f"ENDPOINT_TAKEOVER_CONTROL_AREA_{classification.state.value}",
                    "Inspect the changed endpoint control area before retrying takeover.",
                    retryable=False,
                )
            self._writable_probe(root, prepared, intent_id=intent_id)
            self._require_live_lock(lock)
            self._require_matching_root_identity(root, prepared)
        finally:
            lock.close()

    def _acquire_lock(self, root: Path) -> EndpointLockHandle:
        control = root / CONTROL_DIRECTORY_NAME
        locks = control / "locks"
        self._require_ordinary_directory(control)
        self._require_ordinary_directory(locks)
        try:
            return self._lock_opener.acquire_exclusive_lock(locks / "mutation.lock")
        except EndpointLeaseUnavailable as exc:
            raise _takeover_error(
                exc.validation_code, exc.next_action, retryable=True
            ) from exc

    def _ensure_takeover_files(
        self,
        root: Path,
        prepared: PreparedEndpointTakeover,
    ) -> None:
        control = root / CONTROL_DIRECTORY_NAME
        for relative in _control_directories(
            _installation_namespace(prepared.owner_installation_id)
        ):
            self._ensure_directory(control / relative)
        self._ensure_exact_file(
            control / Path(prepared.takeover_record_path),
            _json_bytes(prepared.takeover_payload_json),
        )
        self._ensure_exact_file(
            control / Path(prepared.ownership_record_path),
            _json_bytes(prepared.ownership_payload_json),
        )

    def _publish_marker(
        self,
        root: Path,
        prepared: PreparedEndpointTakeover,
        *,
        intent_id: str,
    ) -> None:
        control = root / CONTROL_DIRECTORY_NAME
        marker_path = control / ENDPOINT_MARKER_NAME
        temp_path = control / _temp_marker_name(intent_id)
        expected = _json_bytes(prepared.marker_payload_json)
        self._ensure_exact_file(temp_path, expected)
        try:
            os.replace(temp_path, marker_path)
        except OSError as exc:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_MARKER_PUBLISH_FAILED",
                "Restore write access to the endpoint marker and retry takeover.",
                retryable=True,
            ) from exc

    def _cleanup_exact_temp_marker(
        self,
        root: Path,
        prepared: PreparedEndpointTakeover,
        *,
        intent_id: str,
    ) -> None:
        temp_path = root / CONTROL_DIRECTORY_NAME / _temp_marker_name(intent_id)
        if not temp_path.exists():
            return
        self._require_exact_file(temp_path, _json_bytes(prepared.marker_payload_json))
        try:
            temp_path.unlink()
        except OSError as exc:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_TEMP_CLEANUP_FAILED",
                "Retry takeover after its private marker file can be removed.",
                retryable=True,
            ) from exc

    def _require_no_foreign_recovery(self, root: Path, foreign_owner: str) -> None:
        recovery = (
            root
            / CONTROL_DIRECTORY_NAME
            / "installations"
            / _installation_namespace(foreign_owner)
            / "recovery"
        )
        if not recovery.exists():
            return
        self._require_ordinary_directory(recovery)
        count = 0
        try:
            for current, directory_names, file_names in os.walk(
                recovery,
                followlinks=False,
            ):
                self._require_ordinary_directory(Path(current))
                for name in (*directory_names, *file_names):
                    count += 1
                    if count > _MAX_RECOVERY_ENTRIES:
                        raise _takeover_error(
                            "OWNERSHIP_RECOVERY_REQUIRED",
                            "Resolve the foreign installation recovery data before takeover.",
                            retryable=False,
                        )
                    child = Path(current) / name
                    inspection = self._probe.inspect_path(child)
                    if inspection.is_reparse_point:
                        raise _unsafe_control_path()
        except EndpointTakeoverError:
            raise
        except (OSError, ReparseGuardError) as exc:
            raise _unsafe_control_path() from exc
        if count:
            raise _takeover_error(
                "OWNERSHIP_RECOVERY_REQUIRED",
                "Resolve the foreign installation recovery data before takeover.",
                retryable=False,
            )

    def _require_matching_foreign_classification(
        self,
        classification: EndpointControlAreaClassification,
        candidate: EndpointTakeoverCandidate,
    ) -> None:
        marker = classification.marker
        if (
            classification.state is not EndpointControlAreaState.VALID_FOREIGN
            or marker is None
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_FOREIGN_MARKER_REQUIRED",
                "Refresh endpoint details before confirming takeover.",
                retryable=False,
            )
        if (
            marker.endpoint_id != candidate.endpoint_id
            or marker.control_area_id != candidate.control_area_id
            or marker.owner_installation_id != candidate.foreign_owner_installation_id
            or marker.ownership_epoch != candidate.foreign_ownership_epoch
            or marker.root_identity_hash != candidate.root_identity_hash
            or marker.marker_checksum != candidate.marker_checksum
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_FOREIGN_IDENTITY_MISMATCH",
                "Refresh endpoint details before confirming takeover again.",
                retryable=False,
            )

    def _require_matching_prepared_foreign_classification(
        self,
        classification: EndpointControlAreaClassification,
        prepared: PreparedEndpointTakeover,
    ) -> None:
        marker = classification.marker
        if (
            classification.state is not EndpointControlAreaState.VALID_FOREIGN
            or marker is None
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_FOREIGN_MARKER_CHANGED",
                "Inspect the endpoint owner before retrying takeover.",
                retryable=False,
            )
        if (
            marker.endpoint_id != prepared.endpoint_id
            or marker.control_area_id != prepared.control_area_id
            or marker.owner_installation_id != prepared.foreign_owner_installation_id
            or marker.ownership_epoch != prepared.foreign_ownership_epoch
            or marker.root_identity_hash != prepared.root_identity_hash
            or marker.marker_checksum != prepared.old_marker_checksum
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_FOREIGN_MARKER_CHANGED",
                "Inspect the endpoint owner before retrying takeover.",
                retryable=False,
            )

    def _require_matching_owned_classification(
        self,
        classification: EndpointControlAreaClassification,
        prepared: PreparedEndpointTakeover,
    ) -> None:
        marker = classification.marker
        if (
            classification.state is not EndpointControlAreaState.VALID_OWNED
            or marker is None
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_MARKER_VERIFICATION_FAILED",
                "Inspect the endpoint marker before retrying takeover.",
                retryable=False,
            )
        if (
            marker.endpoint_id != prepared.endpoint_id
            or marker.control_area_id != prepared.control_area_id
            or marker.owner_installation_id != prepared.owner_installation_id
            or marker.ownership_epoch != prepared.ownership_epoch
            or marker.root_identity_hash != prepared.root_identity_hash
            or marker.marker_checksum != prepared.marker_checksum
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_MARKER_IDENTITY_MISMATCH",
                "Do not replace or repair the changed endpoint marker automatically.",
                retryable=False,
            )

    def _writable_probe(
        self,
        root: Path,
        prepared: PreparedEndpointTakeover,
        *,
        intent_id: str,
    ) -> None:
        probes = (
            root
            / CONTROL_DIRECTORY_NAME
            / "installations"
            / _installation_namespace(prepared.owner_installation_id)
            / "probes"
        )
        self._require_ordinary_directory(probes)
        probe_path = probes / f"takeover-{intent_id}.probe"
        expected = f"{prepared.probe_token}\n".encode()
        try:
            self._ensure_exact_file(probe_path, expected)
            self._require_exact_file(probe_path, expected)
            probe_path.unlink()
        except EndpointTakeoverError:
            raise
        except OSError as exc:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_PROBE_FAILED",
                "Restore read, write, flush and delete access to the endpoint control area.",
                retryable=True,
            ) from exc

    def _require_matching_root_identity(
        self,
        root: Path,
        prepared: PreparedEndpointTakeover,
    ) -> None:
        try:
            actual = local_root_identity_hash(root, probe=self._probe)
        except LocalEndpointClassificationError as exc:
            raise _takeover_error(
                exc.validation_code, exc.next_action, retryable=True
            ) from exc
        if actual != prepared.root_identity_hash:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_ROOT_IDENTITY_CHANGED",
                "Refresh endpoint details before retrying takeover.",
                retryable=False,
            )

    def _classify(
        self,
        root: Path,
        owner_installation_id: str,
    ) -> EndpointControlAreaClassification:
        try:
            return self._classifier.classify_control_area(
                root,
                local_installation_id=owner_installation_id,
            )
        except LocalEndpointClassificationError as exc:
            raise _takeover_error(
                exc.validation_code, exc.next_action, retryable=True
            ) from exc

    def _ensure_directory(self, path: Path) -> None:
        try:
            path.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_DIRECTORY_CREATE_FAILED",
                "Restore write access to the endpoint control area and retry takeover.",
                retryable=True,
            ) from exc
        self._require_ordinary_directory(path)

    def _require_ordinary_directory(self, path: Path) -> None:
        try:
            inspection = self._probe.inspect_path(path)
            ordinary = stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode)
        except (OSError, ReparseGuardError) as exc:
            raise _unsafe_control_path() from exc
        if not inspection.exists or inspection.is_reparse_point or not ordinary:
            raise _unsafe_control_path()

    def _ensure_exact_file(self, path: Path, expected: bytes) -> None:
        if path.exists():
            self._require_exact_file(path, expected)
            return
        try:
            with path.open("xb") as handle:
                handle.write(expected)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            self._require_exact_file(path, expected)
        except OSError as exc:
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_CONTROL_FILE_WRITE_FAILED",
                "Restore write and flush access to the endpoint control area.",
                retryable=True,
            ) from exc

    def _require_exact_file(self, path: Path, expected: bytes) -> None:
        try:
            inspection = self._probe.inspect_path(path)
            file_stat = path.stat(follow_symlinks=False)
            actual = path.read_bytes()
        except (OSError, ReparseGuardError) as exc:
            raise _unsafe_control_path() from exc
        if (
            not inspection.exists
            or inspection.is_reparse_point
            or not stat.S_ISREG(file_stat.st_mode)
            or actual != expected
        ):
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_CONTROL_FILE_CONFLICT",
                "Inspect the changed takeover control record before retrying.",
                retryable=False,
            )

    @staticmethod
    def _require_live_lock(lock: EndpointLockHandle) -> None:
        if not lock.is_alive():
            raise _takeover_error(
                "ENDPOINT_TAKEOVER_LOCK_LOST",
                "Stop takeover and reacquire the endpoint mutation lock.",
                retryable=True,
            )


def _control_directories(installation: str) -> tuple[Path, ...]:
    namespace = Path("installations") / installation
    return (
        Path("ownership"),
        Path("locks"),
        Path("installations"),
        namespace,
        namespace / "objects",
        namespace / "manifests",
        namespace / "recovery",
        namespace / "probes",
        namespace / "temp",
        Path("objects"),
        Path("objects") / "staging",
        Path("objects") / "versions",
        Path("objects") / "quarantine",
        Path("manifests"),
    )


def _installation_namespace(owner_installation_id: str) -> str:
    return owner_installation_id.replace("-", "")[:12]


def _temp_marker_name(intent_id: str) -> str:
    return f".endpoint-takeover-{intent_id}.json"


def _json_bytes(payload_json: str) -> bytes:
    return f"{payload_json}\n".encode()


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _local_root(root_uri: str) -> Path:
    try:
        return local_path_from_file_uri(root_uri)
    except Exception as exc:
        raise _takeover_error(
            "ENDPOINT_TAKEOVER_REQUIRES_LOCAL_FILE_ROOT",
            "Controlled takeover currently requires an absolute local folder.",
            retryable=False,
        ) from exc


def _require_uuid(value: str, code: str) -> None:
    try:
        UUID(value)
    except (TypeError, ValueError) as exc:
        raise _takeover_error(
            code,
            "Refresh endpoint details before retrying takeover.",
            retryable=False,
        ) from exc


def _unsafe_control_path() -> EndpointTakeoverError:
    return _takeover_error(
        "ENDPOINT_TAKEOVER_CONTROL_PATH_UNSAFE",
        "Use an ordinary non-reparse local endpoint control area.",
        retryable=False,
    )


def _takeover_error(
    code: str,
    next_action: str,
    *,
    retryable: bool,
) -> EndpointTakeoverError:
    return EndpointTakeoverError(code, next_action, retryable=retryable)
