from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from mediasync_home.presentation.theme.theme_manager import ThemeMode  # noqa: E402
from tools.render_gui_shell import render_reference  # noqa: E402


def test_render_reference_writes_nonblank_png_and_manifest(tmp_path) -> None:
    result = render_reference(tmp_path, theme=ThemeMode.LIGHT, scale_percent=100)

    assert (tmp_path / result.image_path).is_file()
    assert (tmp_path / result.manifest_path).is_file()
    assert result.width > 0
    assert result.height > 0
    assert result.unique_sampled_colors >= 8
    assert result.non_background_ratio >= 0.02
