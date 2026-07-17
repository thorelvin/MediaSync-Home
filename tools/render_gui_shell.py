from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_QPA_PLATFORM = "windows" if os.name == "nt" else "offscreen"
os.environ.setdefault("QT_QPA_PLATFORM", DEFAULT_QPA_PLATFORM)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - exercised in environments without PySide6.
    raise SystemExit("PySide6 is required; run this tool with the repo .venv.") from exc

from mediasync_home.application.runtime_status import startup_status  # noqa: E402
from mediasync_home.domain.process_roles import ProcessRole  # noqa: E402
from mediasync_home.ipc.protocol import IpcResponse  # noqa: E402
from mediasync_home.presentation.app import build_main_window, ensure_qapplication  # noqa: E402
from mediasync_home.presentation.theme.theme_manager import ThemeMode  # noqa: E402
from mediasync_home.presentation.view_models.engine_status import (  # noqa: E402
    engine_status_from_response,
)


@dataclass(frozen=True)
class RenderResult:
    theme: str
    scale_percent: int
    image_path: str
    manifest_path: str
    width: int
    height: int
    sampled_pixels: int
    unique_sampled_colors: int
    non_background_ratio: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def render_reference(
    output_dir: Path,
    *,
    theme: ThemeMode,
    scale_percent: int,
    width: int = 1120,
    height: int = 700,
) -> RenderResult:
    if QApplication.instance() is None:
        os.environ["QT_SCALE_FACTOR"] = _scale_value(scale_percent)

    output_dir.mkdir(parents=True, exist_ok=True)
    state = engine_status_from_response(
        IpcResponse.accepted({"host_status": startup_status(ProcessRole.ENGINE_HOST).to_dict()})
    )
    app = ensure_qapplication([])
    window = build_main_window(initial_state=state, theme_mode=theme)
    window.resize(width, height)
    window.show()
    app.processEvents()

    pixmap = window.grab()
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    metrics = _image_metrics(image)
    if metrics["unique_sampled_colors"] < 8 or metrics["non_background_ratio"] < 0.02:
        raise RuntimeError("rendered GUI shell appears blank or nearly blank")

    stem = f"gui-shell-{theme.value}-{scale_percent}"
    image_path = output_dir / f"{stem}.png"
    manifest_path = output_dir / f"{stem}.json"
    if not pixmap.save(str(image_path), "PNG"):
        raise RuntimeError(f"failed to save screenshot: {image_path}")

    result = RenderResult(
        theme=theme.value,
        scale_percent=scale_percent,
        image_path=image_path.name,
        manifest_path=manifest_path.name,
        width=image.width(),
        height=image.height(),
        sampled_pixels=int(metrics["sampled_pixels"]),
        unique_sampled_colors=int(metrics["unique_sampled_colors"]),
        non_background_ratio=float(metrics["non_background_ratio"]),
    )
    manifest_path.write_bytes(
        (json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    window.close()
    window.deleteLater()
    app.processEvents()
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render MediaSync Home GUI shell evidence")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/0b/gui-shell"))
    parser.add_argument("--theme", choices=tuple(mode.value for mode in ThemeMode), required=True)
    parser.add_argument("--scale-percent", type=int, choices=(100, 150, 200), required=True)
    parser.add_argument("--platform", choices=("windows", "offscreen", "minimal"))
    parser.add_argument("--width", type=int, default=1120)
    parser.add_argument("--height", type=int, default=700)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.platform:
        os.environ["QT_QPA_PLATFORM"] = args.platform

    result = render_reference(
        args.output_dir,
        theme=ThemeMode(args.theme),
        scale_percent=args.scale_percent,
        width=args.width,
        height=args.height,
    )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


def _image_metrics(image: QImage) -> dict[str, float | int]:
    width = image.width()
    height = image.height()
    step = max(1, min(width, height) // 80)
    background = image.pixelColor(0, 0).rgba()
    colors: set[int] = set()
    sampled = 0
    non_background = 0
    for y in range(0, height, step):
        for x in range(0, width, step):
            color = image.pixelColor(x, y).rgba()
            colors.add(color)
            sampled += 1
            if color != background:
                non_background += 1
    return {
        "sampled_pixels": sampled,
        "unique_sampled_colors": len(colors),
        "non_background_ratio": non_background / sampled if sampled else 0.0,
    }


def _scale_value(scale_percent: int) -> str:
    return f"{scale_percent / 100:.2f}".rstrip("0").rstrip(".")


if __name__ == "__main__":
    raise SystemExit(main())
