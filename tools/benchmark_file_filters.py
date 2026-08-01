from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mediasync_home.application.file_filters import (  # noqa: E402
    FileFilterPolicy,
    FileFilterRule,
    FileFilterSession,
    FileFilterSubject,
    FilterAction,
    FilterRuleKind,
)


BENCHMARK_REGEX_TOTAL_BUDGET_SECONDS = 30.0


def run_benchmark(path_count: int) -> dict[str, object]:
    if path_count < 1:
        raise ValueError("path_count must be positive")
    policy = FileFilterPolicy(
        advanced_regex_enabled=True,
        rules=(
            FileFilterRule(
                "exclude-cache",
                FilterAction.EXCLUDE,
                FilterRuleKind.RELATIVE_PATH_GLOB,
                "cache/**",
            ),
            FileFilterRule(
                "include-media-name",
                FilterAction.INCLUDE,
                FilterRuleKind.REGEX,
                r"^(?:[^/]+/)*IMG_\d{6}\.(?:jpg|raw)$",
            ),
        ),
    )
    timeout_policy = FileFilterPolicy(
        include_default_exclusions=False,
        advanced_regex_enabled=True,
        rules=(
            FileFilterRule(
                "bounded-timeout-probe",
                FilterAction.EXCLUDE,
                FilterRuleKind.REGEX,
                r"^(a+)+$",
            ),
        ),
    )
    session = FileFilterSession(
        policy,
        regex_total_budget_seconds=BENCHMARK_REGEX_TOTAL_BUDGET_SECONDS,
    )
    timeout_session = FileFilterSession(timeout_policy)
    included = 0
    excluded = 0
    budget_errors = 0
    tracemalloc.start()
    started = time.perf_counter()
    timeout_stride = max(1, path_count // 10)
    for index in range(path_count):
        if index % timeout_stride == 0:
            relative_path = "a" * 4_000 + "!"
        elif index % 10 == 0:
            relative_path = f"cache/day-{index % 31}/item-{index}.bin"
        else:
            extension = "raw" if index % 7 == 0 else "jpg"
            relative_path = f"media/day-{index % 31}/IMG_{index % 1_000_000:06d}.{extension}"
        evaluator = timeout_session if index % timeout_stride == 0 else session
        decision = evaluator.evaluate(
            FileFilterSubject(
                relative_path=relative_path,
                object_type="file",
            )
        )
        if decision.error_code is not None:
            budget_errors += 1
        elif decision.included:
            included += 1
        else:
            excluded += 1
    elapsed_seconds = time.perf_counter() - started
    _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "benchmark": "bounded-file-filter-evaluation-v1",
        "path_count": path_count,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "paths_per_second": round(path_count / elapsed_seconds, 2),
        "included_count": included,
        "excluded_count": excluded,
        "budget_error_count": budget_errors,
        "bounded_regex_match_timeout_ms": 5,
        "benchmark_regex_total_budget_seconds": (
            BENCHMARK_REGEX_TOTAL_BUDGET_SECONDS
        ),
        "production_default_regex_total_budget_seconds": 2,
        "tracemalloc_peak_bytes": peak_bytes,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark bounded MediaSync file-filter evaluation."
    )
    parser.add_argument("--paths", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_benchmark(args.paths)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
