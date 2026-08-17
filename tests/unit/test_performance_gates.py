from tools.check_performance_gates import (
    evaluate_performance_evidence,
    passing_self_test_evidence,
)


def test_performance_gates_accept_complete_boundary_evidence() -> None:
    report = evaluate_performance_evidence(passing_self_test_evidence())

    assert report["passed"] is True
    assert report["failed_count"] == 0


def test_performance_gates_fail_missing_and_out_of_budget_metrics() -> None:
    report = evaluate_performance_evidence(
        {
            "metrics": {
                "large_file_throughput_ratio": 0.84,
                "common_gui_freeze_max_ms": 100,
            }
        }
    )

    failed = {
        result["metric"]
        for result in report["results"]
        if result["passed"] is False
    }
    assert report["passed"] is False
    assert "large_file_throughput_ratio" in failed
    assert "common_gui_freeze_max_ms" in failed
    assert "one_million_peak_rss_bytes" in failed
