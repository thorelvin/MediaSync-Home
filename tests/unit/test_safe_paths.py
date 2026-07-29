from __future__ import annotations

import pytest

from mediasync_home.application.safe_paths import (
    SafePathViolation,
    parse_endpoint_relative_path,
)


def test_endpoint_relative_path_normalizes_separators_and_exposes_parts() -> None:
    path = parse_endpoint_relative_path("Photos\\2026\\Image.jpg")

    assert path.value == "Photos/2026/Image.jpg"
    assert path.parts == ("Photos", "2026", "Image.jpg")


@pytest.mark.parametrize(
    ("value", "validation_code"),
    [
        ("", "SAFE_PATH_EMPTY"),
        ("   ", "SAFE_PATH_EMPTY"),
        ("/absolute.txt", "SAFE_PATH_ABSOLUTE_OR_DEVICE"),
        ("//server/share/file.txt", "SAFE_PATH_ABSOLUTE_OR_DEVICE"),
        ("\\\\?\\C:\\root\\file.txt", "SAFE_PATH_ABSOLUTE_OR_DEVICE"),
        ("C:/absolute.txt", "SAFE_PATH_DRIVE_RELATIVE"),
        ("C:drive-relative.txt", "SAFE_PATH_DRIVE_RELATIVE"),
        ("Photos/../secret.txt", "SAFE_PATH_UNSAFE_SEGMENT"),
        ("Photos//image.jpg", "SAFE_PATH_UNSAFE_SEGMENT"),
        ("Photos/name:stream.jpg", "SAFE_PATH_ALTERNATE_DATA_STREAM"),
        ("Photos/\x1f.jpg", "SAFE_PATH_CONTROL_CHARACTER"),
        ("Photos/trailing-space ", "SAFE_PATH_AMBIGUOUS_WHITESPACE"),
        ("Photos/trailing-dot.", "SAFE_PATH_AMBIGUOUS_TRAILING_DOT"),
        ("Photos/CON.txt", "SAFE_PATH_RESERVED_DEVICE_NAME"),
        ("Photos/.mediasync/file.txt", "SAFE_PATH_CONTROL_AREA_RESERVED"),
        ("LPT1", "SAFE_PATH_RESERVED_DEVICE_NAME"),
    ],
)
def test_endpoint_relative_path_rejects_unsafe_fragments(
    value: str,
    validation_code: str,
) -> None:
    with pytest.raises(SafePathViolation) as exc_info:
        parse_endpoint_relative_path(value)

    assert exc_info.value.validation_code == validation_code
