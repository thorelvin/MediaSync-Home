from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class PerformanceGate:
    metric: str
    limit: float
    comparison: str
    passes: Callable[[float, float], bool]


PERFORMANCE_GATES = (
    PerformanceGate("large_file_throughput_ratio", 0.85, ">=", lambda value, limit: value >= limit),
    PerformanceGate("mixed_small_throughput_ratio", 0.70, ">=", lambda value, limit: value >= limit),
    PerformanceGate("one_million_peak_rss_bytes", 400 * 1024 * 1024, "<=", lambda value, limit: value <= limit),
    PerformanceGate("cold_gui_start_seconds", 4.0, "<=", lambda value, limit: value <= limit),
    PerformanceGate("local_ipc_query_p95_ms", 100.0, "<=", lambda value, limit: value <= limit),
    PerformanceGate("warm_navigation_p95_ms", 150.0, "<=", lambda value, limit: value <= limit),
    PerformanceGate("indexed_filter_million_p95_ms", 500.0, "<=", lambda value, limit: value <= limit),
    PerformanceGate("common_gui_freeze_max_ms", 100.0, "<", lambda value, limit: value < limit),
    PerformanceGate("unbounded_queue_count", 0.0, "==", lambda value, limit: value == limit),
    PerformanceGate("orphaned_robocopy_process_count", 0.0, "==", lambda value, limit: value == limit),
    PerformanceGate("unbudgeted_regex_count", 0.0, "==", lambda value, limit: value == limit),
    PerformanceGate("uncontrolled_state_growth_count", 0.0, "==", lambda value, limit: value == limit),
)


def evaluate_performance_evidence(payload: object) -> dict[str, object]:
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    metric_values = metrics if isinstance(metrics, dict) else {}
    results: list[dict[str, object]] = []
    for gate in PERFORMANCE_GATES:
        raw_value = metric_values.get(gate.metric)
        value = (
            float(raw_value)
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)
            else None
        )
        passed = value is not None and gate.passes(value, gate.limit)
        results.append(
            {
                "comparison": gate.comparison,
                "limit": gate.limit,
                "metric": gate.metric,
                "passed": passed,
                "value": value,
            }
        )
    failed = [result for result in results if result["passed"] is False]
    return {
        "gate_count": len(results),
        "passed": not failed,
        "failed_count": len(failed),
        "results": results,
        "schema_version": 1,
    }


def passing_self_test_evidence() -> dict[str, object]:
    return {
        "metrics": {
            "large_file_throughput_ratio": 0.85,
            "mixed_small_throughput_ratio": 0.70,
            "one_million_peak_rss_bytes": 400 * 1024 * 1024,
            "cold_gui_start_seconds": 4.0,
            "local_ipc_query_p95_ms": 100.0,
            "warm_navigation_p95_ms": 150.0,
            "indexed_filter_million_p95_ms": 500.0,
            "common_gui_freeze_max_ms": 99.999,
            "unbounded_queue_count": 0,
            "orphaned_robocopy_process_count": 0,
            "unbudgeted_regex_count": 0,
            "uncontrolled_state_growth_count": 0,
        }
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when MediaSync release performance evidence is missing or outside budget."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", type=Path)
    source.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    payload = (
        passing_self_test_evidence()
        if args.self_test
        else json.loads(args.evidence.read_text(encoding="utf-8"))
    )
    report = evaluate_performance_evidence(payload)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    sys.stdout.write(rendered + "\n")
    return 0 if report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
