from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from uuid import UUID

from blake3 import blake3

from mediasync_home.adapters.local_endpoint_classifier import (
    APPLICATION_NAME,
    BLAKE3_ALGORITHM,
    CANONICALIZATION_ALGORITHM,
    CONTROL_DIRECTORY_NAME,
    ENDPOINT_MARKER_NAME,
    SUPPORTED_CONTROL_SCHEMA_VERSION,
    LocalEndpointClassificationError,
    LocalEndpointControlAreaClassifier,
    endpoint_marker_checksum,
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
from mediasync_home.application.writable_endpoint_registration import (
    PreparedWritableEndpoint,
    WritableEndpointRegistrationCandidate,
    WritableEndpointRegistrationError,
    WritableEndpointTargetIds,
)


_OWNERSHIP_EPOCH = 1
_MAX_CONTROL_ENTRIES = 1_024


class LocalWritableEndpointControlAreaProvisioner:
    def __init__(
        self,
        *,
        classifier: LocalEndpointControlAreaClassifier | None = None,
        probe: ReparsePathProbe | None = None,
    ) -> None:
        self._probe = probe or LocalFilesystemReparsePathProbe()
        self._classifier = classifier or LocalEndpointControlAreaClassifier(probe=self._probe)

    def prepare_new_control_area(
        self,
        candidate: WritableEndpointRegistrationCandidate,
        *,
        intent_id: str,
        target_ids: WritableEndpointTargetIds,
        owner_installation_id: str,
        created_utc: str,
    ) -> PreparedWritableEndpoint:
        _require_uuid(intent_id, "WRITABLE_ENDPOINT_REGISTRATION_INTENT_ID_INVALID")
        _require_uuid(candidate.endpoint_id, "WRITABLE_ENDPOINT_ID_INVALID")
        _require_uuid(
            target_ids.endpoint_revision_id,
            "WRITABLE_ENDPOINT_REVISION_ID_INVALID",
        )
        _require_uuid(target_ids.control_area_id, "WRITABLE_CONTROL_AREA_ID_INVALID")
        _require_uuid(owner_installation_id, "WRITABLE_ENDPOINT_OWNER_ID_INVALID")
        if target_ids.target_ordinal != candidate.target_ordinal:
            raise _registration_error(
                "WRITABLE_ENDPOINT_TARGET_ORDINAL_MISMATCH",
                "Refresh the active backup job before registering this target.",
                retryable=False,
            )

        root = _local_root(candidate.root_uri)
        classification = self._classify(root, owner_installation_id)
        if classification.state is not EndpointControlAreaState.ABSENT:
            raise _registration_error(
                _classification_registration_code(classification.state),
                "Inspect the existing target control area before registering it.",
                retryable=False,
            )
        try:
            root_identity_hash = local_root_identity_hash(root, probe=self._probe)
        except LocalEndpointClassificationError as exc:
            raise _registration_error(exc.validation_code, exc.next_action, retryable=True) from exc

        ownership_path = f"ownership/epoch-{_OWNERSHIP_EPOCH:08d}.json"
        ownership_payload = {
            "endpoint_id": candidate.endpoint_id,
            "owner_installation_id": owner_installation_id,
            "ownership_epoch": _OWNERSHIP_EPOCH,
            "created_utc": created_utc,
            "event": "OWNER_REGISTERED",
        }
        marker_payload: dict[str, object] = {
            "control_schema_version": SUPPORTED_CONTROL_SCHEMA_VERSION,
            "endpoint_id": candidate.endpoint_id,
            "control_area_id": target_ids.control_area_id,
            "owner_installation_id": owner_installation_id,
            "ownership_epoch": _OWNERSHIP_EPOCH,
            "ownership_mode": "EXCLUSIVE_WRITER",
            "created_utc": created_utc,
            "updated_utc": created_utc,
            "expected_volume_id": None,
            "expected_share": None,
            "root_identity_hash_algorithm": BLAKE3_ALGORITHM,
            "root_identity_hash": root_identity_hash,
            "latest_ownership_record": ownership_path,
            "canonicalization_algorithm": CANONICALIZATION_ALGORITHM,
            "marker_checksum_algorithm": BLAKE3_ALGORITHM,
            "application": APPLICATION_NAME,
        }
        marker_payload["marker_checksum"] = endpoint_marker_checksum(marker_payload)
        probe_token = blake3(
            f"{intent_id}:{candidate.target_ordinal}:{candidate.endpoint_id}".encode()
        ).hexdigest()
        return PreparedWritableEndpoint(
            target_ordinal=candidate.target_ordinal,
            endpoint_id=candidate.endpoint_id,
            source_endpoint_revision_id=candidate.endpoint_revision_id,
            resulting_endpoint_revision_id=target_ids.endpoint_revision_id,
            resulting_endpoint_generation=candidate.endpoint_generation + 1,
            display_name=candidate.display_name,
            root_uri=candidate.root_uri,
            control_area_id=target_ids.control_area_id,
            owner_installation_id=owner_installation_id,
            ownership_epoch=_OWNERSHIP_EPOCH,
            root_identity_hash_algorithm=BLAKE3_ALGORITHM,
            root_identity_hash=root_identity_hash,
            marker_checksum_algorithm=BLAKE3_ALGORITHM,
            marker_checksum=str(marker_payload["marker_checksum"]),
            marker_payload_json=_canonical_json(marker_payload),
            ownership_payload_json=_canonical_json(ownership_payload),
            probe_token=probe_token,
        )

    def apply_prepared_control_area(
        self,
        prepared: PreparedWritableEndpoint,
        *,
        intent_id: str,
    ) -> None:
        _require_uuid(intent_id, "WRITABLE_ENDPOINT_REGISTRATION_INTENT_ID_INVALID")
        root = _local_root(prepared.root_uri)
        self._require_matching_root_identity(root, prepared)
        final_control = root / CONTROL_DIRECTORY_NAME
        staging_control = root / _staging_name(intent_id, prepared.target_ordinal)

        classification = self._classify(root, prepared.owner_installation_id)
        if classification.state is EndpointControlAreaState.VALID_OWNED:
            self._require_matching_classification(classification, prepared)
            if staging_control.exists():
                self._remove_exact_staging(staging_control, prepared)
            self._writable_probe(final_control, prepared, intent_id=intent_id)
            return
        if classification.state is not EndpointControlAreaState.ABSENT:
            raise _registration_error(
                _classification_registration_code(classification.state),
                "Inspect the changed target control area before retrying registration.",
                retryable=False,
            )

        self._materialize_staging(staging_control, prepared)
        self._require_matching_root_identity(root, prepared)
        if final_control.exists():
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_AREA_APPEARED_DURING_REGISTRATION",
                "Inspect the target control area before retrying registration.",
                retryable=False,
            )
        try:
            os.rename(staging_control, final_control)
        except OSError as exc:
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_AREA_PUBLISH_FAILED",
                "Restore write access to the target root and retry registration.",
                retryable=True,
            ) from exc

        classification = self._classify(root, prepared.owner_installation_id)
        self._require_matching_classification(classification, prepared)
        self._writable_probe(final_control, prepared, intent_id=intent_id)
        self._require_matching_root_identity(root, prepared)

    def _materialize_staging(
        self,
        staging_control: Path,
        prepared: PreparedWritableEndpoint,
    ) -> None:
        if staging_control.exists():
            self._require_expected_staging_tree(staging_control, prepared, allow_partial=True)
        else:
            try:
                staging_control.mkdir()
            except OSError as exc:
                raise _registration_error(
                    "WRITABLE_ENDPOINT_CONTROL_AREA_CREATE_FAILED",
                    "Restore write access to the target root and retry registration.",
                    retryable=True,
                ) from exc
        self._require_ordinary_directory(staging_control)

        installation = _installation_namespace(prepared.owner_installation_id)
        directories = _control_directories(installation)
        for relative in directories:
            self._ensure_directory(staging_control / relative)

        ownership_relative = Path("ownership") / f"epoch-{prepared.ownership_epoch:08d}.json"
        self._ensure_exact_file(
            staging_control / ownership_relative,
            _json_bytes(prepared.ownership_payload_json),
        )
        self._ensure_exact_file(
            staging_control / ENDPOINT_MARKER_NAME,
            _json_bytes(prepared.marker_payload_json),
        )
        self._require_expected_staging_tree(staging_control, prepared, allow_partial=False)

    def _require_expected_staging_tree(
        self,
        staging_control: Path,
        prepared: PreparedWritableEndpoint,
        *,
        allow_partial: bool,
    ) -> None:
        installation = _installation_namespace(prepared.owner_installation_id)
        expected_dirs = {Path("."), *(_control_directories(installation))}
        ownership_relative = Path("ownership") / f"epoch-{prepared.ownership_epoch:08d}.json"
        expected_files = {
            ownership_relative: _json_bytes(prepared.ownership_payload_json),
            Path(ENDPOINT_MARKER_NAME): _json_bytes(prepared.marker_payload_json),
        }
        observed_count = 0
        for current, directory_names, file_names in os.walk(staging_control, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(staging_control)
            normalized_dir = Path(".") if str(relative_dir) == "." else relative_dir
            if normalized_dir not in expected_dirs:
                raise _unexpected_staging()
            self._require_ordinary_directory(current_path)
            for name in (*directory_names, *file_names):
                observed_count += 1
                if observed_count > _MAX_CONTROL_ENTRIES:
                    raise _unexpected_staging()
                child = current_path / name
                relative = child.relative_to(staging_control)
                if name in directory_names:
                    if relative not in expected_dirs:
                        raise _unexpected_staging()
                    self._require_ordinary_directory(child)
                else:
                    expected = expected_files.get(relative)
                    if expected is None:
                        raise _unexpected_staging()
                    self._require_exact_file(child, expected)
        if not allow_partial:
            for relative in expected_dirs:
                if relative != Path(".") and not (staging_control / relative).is_dir():
                    raise _unexpected_staging()
            for relative in expected_files:
                if not (staging_control / relative).is_file():
                    raise _unexpected_staging()

    def _remove_exact_staging(
        self,
        staging_control: Path,
        prepared: PreparedWritableEndpoint,
    ) -> None:
        self._require_expected_staging_tree(staging_control, prepared, allow_partial=True)
        files: list[Path] = []
        directories: list[Path] = []
        for current, directory_names, file_names in os.walk(staging_control, followlinks=False):
            current_path = Path(current)
            files.extend(current_path / name for name in file_names)
            directories.extend(current_path / name for name in directory_names)
        try:
            for path in files:
                path.unlink()
            for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
                path.rmdir()
            staging_control.rmdir()
        except OSError as exc:
            raise _registration_error(
                "WRITABLE_ENDPOINT_STAGING_CLEANUP_FAILED",
                "Retry after the private registration staging directory can be removed.",
                retryable=True,
            ) from exc

    def _writable_probe(
        self,
        control: Path,
        prepared: PreparedWritableEndpoint,
        *,
        intent_id: str,
    ) -> None:
        installation = _installation_namespace(prepared.owner_installation_id)
        probes = control / "installations" / installation / "probes"
        self._require_ordinary_directory(probes)
        probe_path = probes / f"register-{intent_id}-{prepared.target_ordinal}.probe"
        expected = f"{prepared.probe_token}\n".encode()
        try:
            if probe_path.exists():
                self._require_exact_file(probe_path, expected)
            else:
                self._write_new_file(probe_path, expected)
            self._require_exact_file(probe_path, expected)
            probe_path.unlink()
        except WritableEndpointRegistrationError:
            raise
        except OSError as exc:
            raise _registration_error(
                "WRITABLE_ENDPOINT_PROBE_FAILED",
                "Restore read, write, flush and delete access to the target control area.",
                retryable=True,
            ) from exc

    def _require_matching_root_identity(
        self,
        root: Path,
        prepared: PreparedWritableEndpoint,
    ) -> None:
        try:
            actual = local_root_identity_hash(root, probe=self._probe)
        except LocalEndpointClassificationError as exc:
            raise _registration_error(exc.validation_code, exc.next_action, retryable=True) from exc
        if actual != prepared.root_identity_hash:
            raise _registration_error(
                "WRITABLE_ENDPOINT_ROOT_IDENTITY_CHANGED",
                "Select and register the target root again.",
                retryable=False,
            )

    def _require_matching_classification(
        self,
        classification: EndpointControlAreaClassification,
        prepared: PreparedWritableEndpoint,
    ) -> None:
        state = classification.state
        marker = classification.marker
        if state is not EndpointControlAreaState.VALID_OWNED or marker is None:
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_AREA_VERIFICATION_FAILED",
                "Inspect the target control area before retrying registration.",
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
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_AREA_IDENTITY_MISMATCH",
                "Do not repair or replace the changed endpoint marker automatically.",
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
            raise _registration_error(exc.validation_code, exc.next_action, retryable=True) from exc

    def _ensure_directory(self, path: Path) -> None:
        try:
            path.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_DIRECTORY_CREATE_FAILED",
                "Restore write access to the target control area and retry registration.",
                retryable=True,
            ) from exc
        self._require_ordinary_directory(path)

    def _require_ordinary_directory(self, path: Path) -> None:
        try:
            inspection = self._probe.inspect_path(path)
            ordinary = stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode)
        except (OSError, ReparseGuardError) as exc:
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_PATH_UNSAFE",
                "Use an ordinary non-reparse local target directory.",
                retryable=False,
            ) from exc
        if not inspection.exists or inspection.is_reparse_point or not ordinary:
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_PATH_UNSAFE",
                "Use an ordinary non-reparse local target directory.",
                retryable=False,
            )

    def _ensure_exact_file(self, path: Path, expected: bytes) -> None:
        if path.exists():
            self._require_exact_file(path, expected)
            return
        self._write_new_file(path, expected)

    def _write_new_file(self, path: Path, payload: bytes) -> None:
        try:
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            self._require_exact_file(path, payload)
        except OSError as exc:
            raise _registration_error(
                "WRITABLE_ENDPOINT_CONTROL_FILE_WRITE_FAILED",
                "Restore write and flush access to the target control area.",
                retryable=True,
            ) from exc

    def _require_exact_file(self, path: Path, expected: bytes) -> None:
        try:
            inspection = self._probe.inspect_path(path)
            file_stat = path.stat(follow_symlinks=False)
            actual = path.read_bytes()
        except (OSError, ReparseGuardError) as exc:
            raise _unexpected_staging() from exc
        if (
            not inspection.exists
            or inspection.is_reparse_point
            or not stat.S_ISREG(file_stat.st_mode)
            or actual != expected
        ):
            raise _unexpected_staging()


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


def _staging_name(intent_id: str, target_ordinal: int) -> str:
    return f".mediasync-register-{intent_id}-{target_ordinal}"


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
        raise _registration_error(
            "WRITABLE_ENDPOINT_REQUIRES_LOCAL_FILE_ROOT",
            "Choose an absolute local folder for this preview target.",
            retryable=False,
        ) from exc


def _require_uuid(value: str, code: str) -> None:
    try:
        UUID(value)
    except (TypeError, ValueError) as exc:
        raise _registration_error(
            code,
            "Refresh the backup job and retry target registration.",
            retryable=False,
        ) from exc


def _classification_registration_code(state: EndpointControlAreaState) -> str:
    return f"WRITABLE_ENDPOINT_CONTROL_AREA_{state.value}"


def _unexpected_staging() -> WritableEndpointRegistrationError:
    return _registration_error(
        "WRITABLE_ENDPOINT_REGISTRATION_STAGING_UNSAFE",
        "Inspect the private registration staging directory before retrying.",
        retryable=False,
    )


def _registration_error(
    code: str,
    next_action: str,
    *,
    retryable: bool,
) -> WritableEndpointRegistrationError:
    return WritableEndpointRegistrationError(
        code,
        next_action,
        retryable=retryable,
    )
