from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class NamedStreamPolicy(str, Enum):
    PRESERVE_WHEN_PORTABLE_BLOCK_IF_UNCONFIRMED = (
        "PRESERVE_WHEN_PORTABLE_BLOCK_IF_UNCONFIRMED"
    )
    BLOCK_IF_PRESENT_OR_UNCONFIRMED = "BLOCK_IF_PRESENT_OR_UNCONFIRMED"


DEFAULT_NAMED_STREAM_POLICY = (
    NamedStreamPolicy.PRESERVE_WHEN_PORTABLE_BLOCK_IF_UNCONFIRMED
)


class NamedStreamState(str, Enum):
    NONE = "NONE"
    PRESENT = "PRESENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NamedStreamRecord:
    stream_name: str
    size_bytes: int

    def __post_init__(self) -> None:
        upper_name = self.stream_name.upper()
        if (
            not self.stream_name.startswith(":")
            or not upper_name.endswith(":$DATA")
            or upper_name == "::$DATA"
            or len(self.stream_name) > 295
            or any(character in self.stream_name for character in ("\\", "/", "\0"))
        ):
            raise ValueError("NAMED_STREAM_NAME_INVALID")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("NAMED_STREAM_SIZE_INVALID")


@dataclass(frozen=True, slots=True)
class NamedStreamInspection:
    state: NamedStreamState
    observed_named_stream_count: int = 0
    named_streams: tuple[NamedStreamRecord, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.observed_named_stream_count < 0:
            raise ValueError("NAMED_STREAM_COUNT_INVALID")
        if self.state is NamedStreamState.NONE and (
            self.observed_named_stream_count != 0
            or self.named_streams
            or self.error_code is not None
        ):
            raise ValueError("NAMED_STREAM_NONE_EVIDENCE_INVALID")
        if self.state is NamedStreamState.PRESENT and (
            self.observed_named_stream_count < 1
            or len(self.named_streams) != self.observed_named_stream_count
            or self.error_code is not None
        ):
            raise ValueError("NAMED_STREAM_PRESENT_EVIDENCE_INVALID")
        if self.state is NamedStreamState.UNKNOWN and (
            self.observed_named_stream_count != 0
            or self.named_streams
            or self.error_code is None
            or not self.error_code.strip()
        ):
            raise ValueError("NAMED_STREAM_UNKNOWN_EVIDENCE_INVALID")
        names = [record.stream_name.casefold() for record in self.named_streams]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("NAMED_STREAM_RECORDS_NOT_CANONICAL")


class NamedStreamProbe(Protocol):
    def inspect_named_streams(self, path: Path) -> NamedStreamInspection: ...
