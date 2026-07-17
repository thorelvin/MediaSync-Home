from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    try:
        from importlinter.cli import lint_imports
    except ImportError as exc:
        raise SystemExit(
            "import-linter is required; install tooling with "
            "python -m pip install -r requirements-dev.txt"
        ) from exc

    os.chdir(ROOT)
    return lint_imports(config_filename=str(ROOT / "pyproject.toml"), no_cache=True)


if __name__ == "__main__":
    raise SystemExit(main())
