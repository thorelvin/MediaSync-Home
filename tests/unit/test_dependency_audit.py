from __future__ import annotations

import pytest

from tools.audit_dependencies import (
    APPROVED_LICENSES,
    NOTICE_REQUIRED_LICENSES,
    _normalize_license_name,
    read_pinned_requirements,
)


def test_read_pinned_requirements_follows_includes(tmp_path) -> None:
    base = tmp_path / "base.txt"
    dev = tmp_path / "dev.txt"
    base.write_text("jsonschema==4.26.0\n", encoding="utf-8")
    dev.write_text("-r base.txt\npytest==9.1.1\n", encoding="utf-8")

    requirements = read_pinned_requirements(dev)

    assert [(item.name, item.version, item.source) for item in requirements] == [
        ("jsonschema", "4.26.0", "base.txt"),
        ("pytest", "9.1.1", "dev.txt"),
    ]


def test_read_pinned_requirements_rejects_unpinned_dependency(tmp_path) -> None:
    requirements_file = tmp_path / "requirements.txt"
    requirements_file.write_text("pytest>=9\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be pinned"):
        read_pinned_requirements(requirements_file)


def test_dependency_audit_normalizes_python_software_foundation_license_alias() -> None:
    assert _normalize_license_name("PSF") == "PSF-2.0"
    assert _normalize_license_name("Python Software Foundation License") == "PSF-2.0"
    assert _normalize_license_name("MIT") == "MIT"


def test_regex_dual_license_is_approved_and_requires_notice() -> None:
    license_expression = "Apache-2.0 AND CNRI-Python"

    assert license_expression in APPROVED_LICENSES
    assert license_expression in NOTICE_REQUIRED_LICENSES
