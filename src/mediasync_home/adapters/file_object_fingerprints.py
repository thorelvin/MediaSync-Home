from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mediasync_home.adapters.named_streams import (
    NoNamedStreamProbe,
    Win32NamedStreamProbe,
)
from mediasync_home.application.file_object_fingerprints import (
    canonical_file_object_fingerprint,
    named_stream_fingerprints,
)
from mediasync_home.application.named_streams import (
    NamedStreamInspection,
    NamedStreamProbe,
    NamedStreamState,
)


DEFAULT_CHUNK_BYTES = 1024 * 1024


class LocalFileObjectFingerprintError(RuntimeError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


class LocalFileObjectFingerprintAdapter:
    def __init__(
        self,
        *,
        named_stream_probe: NamedStreamProbe | None = None,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    ) -> None:
        if chunk_bytes <= 0:
            raise ValueError("FILE_OBJECT_FINGERPRINT_CHUNK_BYTES_INVALID")
        self._named_stream_probe = named_stream_probe or (
            Win32NamedStreamProbe() if os.name == "nt" else NoNamedStreamProbe()
        )
        self._chunk_bytes = chunk_bytes

    def fingerprint(self, path: Path) -> dict[str, object]:
        byte_count, content_hash = self._hash_path(path)
        return self.fingerprint_with_primary(
            path,
            primary_fingerprint={
                "byte_count": byte_count,
                "content_hash": content_hash,
            },
        )

    def fingerprint_with_primary(
        self,
        path: Path,
        *,
        primary_fingerprint: dict[str, object],
    ) -> dict[str, object]:
        inspection = self._inspection(path)
        streams: list[dict[str, object]] = []
        for record in inspection.named_streams:
            stream_byte_count, stream_hash = self._hash_path(
                _stream_path(path, record.stream_name)
            )
            if stream_byte_count != record.size_bytes:
                raise LocalFileObjectFingerprintError(
                    "FILE_OBJECT_NAMED_STREAM_CHANGED_DURING_HASH"
                )
            streams.append(
                {
                    "name": record.stream_name,
                    "byte_count": stream_byte_count,
                    "content_hash": stream_hash,
                }
            )
        return canonical_file_object_fingerprint(
            {
                "byte_count": primary_fingerprint.get("byte_count"),
                "content_hash": primary_fingerprint.get("content_hash"),
                "named_streams": streams,
            },
            require_named_stream_inventory=True,
        )

    def copy_named_streams(
        self,
        *,
        source: Path,
        destination: Path,
        expected_fingerprint: dict[str, object],
    ) -> None:
        expected_streams = named_stream_fingerprints(expected_fingerprint)
        source_inspection = self._inspection(source)
        observed_inventory = tuple(
            (record.stream_name, record.size_bytes)
            for record in source_inspection.named_streams
        )
        expected_inventory: list[tuple[str, int]] = []
        for stream in expected_streams:
            stream_byte_count = stream["byte_count"]
            if not isinstance(stream_byte_count, int):
                raise AssertionError("canonical stream byte count is not an integer")
            expected_inventory.append((str(stream["name"]), stream_byte_count))
        if observed_inventory != tuple(expected_inventory):
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_NAMED_STREAM_INVENTORY_CHANGED"
            )
        if self._inspection(destination).state is not NamedStreamState.NONE:
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_DESTINATION_STREAMS_NOT_EMPTY"
            )

        for expected in expected_streams:
            stream_name = str(expected["name"])
            source_stream = _stream_path(source, stream_name)
            destination_stream = _stream_path(destination, stream_name)
            digest = hashlib.sha256()
            byte_count = 0
            try:
                with source_stream.open("rb", buffering=0) as reader:
                    with destination_stream.open("xb", buffering=0) as writer:
                        while True:
                            chunk = reader.read(self._chunk_bytes)
                            if not chunk:
                                break
                            writer.write(chunk)
                            digest.update(chunk)
                            byte_count += len(chunk)
                        writer.flush()
                        os.fsync(writer.fileno())
            except OSError as exc:
                raise LocalFileObjectFingerprintError(
                    "FILE_OBJECT_NAMED_STREAM_COPY_FAILED"
                ) from exc
            if (
                byte_count != expected["byte_count"]
                or digest.hexdigest() != expected["content_hash"]
            ):
                raise LocalFileObjectFingerprintError(
                    "FILE_OBJECT_NAMED_STREAM_CHANGED_DURING_COPY"
                )

    def copy_file_object(
        self,
        *,
        source: Path,
        destination: Path,
        expected_fingerprint: dict[str, object],
    ) -> None:
        expected = canonical_file_object_fingerprint(
            expected_fingerprint,
            require_named_stream_inventory=True,
        )
        if self.fingerprint(source) != expected:
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_SOURCE_FINGERPRINT_MISMATCH"
            )
        try:
            with source.open("rb", buffering=0) as reader:
                with destination.open("xb", buffering=0) as writer:
                    while True:
                        chunk = reader.read(self._chunk_bytes)
                        if not chunk:
                            break
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
        except OSError as exc:
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_PRIMARY_STREAM_COPY_FAILED"
            ) from exc
        self.copy_named_streams(
            source=source,
            destination=destination,
            expected_fingerprint=expected,
        )
        if self.fingerprint(destination) != expected:
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_DESTINATION_FINGERPRINT_MISMATCH"
            )

    def flush_named_streams(
        self,
        *,
        path: Path,
        expected_fingerprint: dict[str, object],
    ) -> None:
        for stream in named_stream_fingerprints(expected_fingerprint):
            try:
                with _stream_path(path, str(stream["name"])).open(
                    "r+b", buffering=0
                ) as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise LocalFileObjectFingerprintError(
                    "FILE_OBJECT_NAMED_STREAM_FLUSH_FAILED"
                ) from exc

    def _inspection(self, path: Path) -> NamedStreamInspection:
        inspection = self._named_stream_probe.inspect_named_streams(path)
        if inspection.state is NamedStreamState.UNKNOWN:
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_NAMED_STREAM_ENUMERATION_UNCONFIRMED"
            )
        return inspection

    def _hash_path(self, path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        byte_count = 0
        try:
            with path.open("rb", buffering=0) as handle:
                while True:
                    chunk = handle.read(self._chunk_bytes)
                    if not chunk:
                        break
                    digest.update(chunk)
                    byte_count += len(chunk)
        except OSError as exc:
            raise LocalFileObjectFingerprintError(
                "FILE_OBJECT_STREAM_HASH_FAILED"
            ) from exc
        return byte_count, digest.hexdigest()


def _stream_path(path: Path, stream_name: str) -> Path:
    return Path(f"{path}{stream_name}")
