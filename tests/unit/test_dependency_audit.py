from __future__ import annotations

import pytest

from tools.audit_dependencies import read_pinned_requirements


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
