from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class NamedStreamPolicy(str, Enum):
    BLOCK_IF_PRESENT_OR_UNCONFIRMED = "BLOCK_IF_PRESENT_OR_UNCONFIRMED"


DEFAULT_NAMED_STREAM_POLICY = NamedStreamPolicy.BLOCK_IF_PRESENT_OR_UNCONFIRMED


class NamedStreamState(str, Enum):
    NONE = "NONE"
    PRESENT = "PRESENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class NamedStreamInspection:
    state: NamedStreamState
    observed_named_stream_count: int = 0
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.observed_named_stream_count < 0:
            raise ValueError("NAMED_STREAM_COUNT_INVALID")
        if self.state is NamedStreamState.NONE and (
            self.observed_named_stream_count != 0 or self.error_code is not None
        ):
            raise ValueError("NAMED_STREAM_NONE_EVIDENCE_INVALID")
        if self.state is NamedStreamState.PRESENT and (
            self.observed_named_stream_count < 1 or self.error_code is not None
        ):
            raise ValueError("NAMED_STREAM_PRESENT_EVIDENCE_INVALID")
        if self.state is NamedStreamState.UNKNOWN and (
            self.observed_named_stream_count != 0
            or self.error_code is None
            or not self.error_code.strip()
        ):
            raise ValueError("NAMED_STREAM_UNKNOWN_EVIDENCE_INVALID")


class NamedStreamProbe(Protocol):
    def inspect_named_streams(self, path: Path) -> NamedStreamInspection: ...
