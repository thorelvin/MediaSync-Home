from __future__ import annotations

import re
from dataclasses import dataclass


WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
WINDOWS_RESERVED_BASENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
CONTROL_AREA_BASENAME = ".mediasync"


class SafePathViolation(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


@dataclass(frozen=True)
class EndpointRelativePath:
    value: str
    parts: tuple[str, ...]


def parse_endpoint_relative_path(value: str) -> EndpointRelativePath:
    normalized = value.replace("\\", "/")
    if not normalized.strip():
        raise SafePathViolation("SAFE_PATH_EMPTY")
    if normalized.startswith("/") or normalized.startswith("//"):
        raise SafePathViolation("SAFE_PATH_ABSOLUTE_OR_DEVICE")
    if WINDOWS_DRIVE_PATTERN.match(normalized):
        raise SafePathViolation("SAFE_PATH_DRIVE_RELATIVE")
    if any(ord(character) < 32 for character in normalized):
        raise SafePathViolation("SAFE_PATH_CONTROL_CHARACTER")

    parts = tuple(normalized.split("/"))
    if not parts:
        raise SafePathViolation("SAFE_PATH_EMPTY")
    for part in parts:
        _validate_part(part)
    return EndpointRelativePath(value="/".join(parts), parts=parts)


def _validate_part(part: str) -> None:
    if part in {"", ".", ".."}:
        raise SafePathViolation("SAFE_PATH_UNSAFE_SEGMENT")
    if ":" in part:
        raise SafePathViolation("SAFE_PATH_ALTERNATE_DATA_STREAM")
    if part != part.strip():
        raise SafePathViolation("SAFE_PATH_AMBIGUOUS_WHITESPACE")
    if part.endswith("."):
        raise SafePathViolation("SAFE_PATH_AMBIGUOUS_TRAILING_DOT")

    basename = part.split(".", 1)[0].upper()
    if basename in WINDOWS_RESERVED_BASENAMES:
        raise SafePathViolation("SAFE_PATH_RESERVED_DEVICE_NAME")
    if part.casefold() == CONTROL_AREA_BASENAME:
        raise SafePathViolation("SAFE_PATH_CONTROL_AREA_RESERVED")
