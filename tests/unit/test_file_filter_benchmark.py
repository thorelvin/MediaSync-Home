from tools.benchmark_file_filters import run_benchmark


def test_file_filter_benchmark_counts_every_path_and_exercises_budget() -> None:
    result = run_benchmark(5_000)

    assert (
        result["included_count"]
        + result["excluded_count"]
        + result["budget_error_count"]
        == result["path_count"]
    )
    assert result["budget_error_count"] >= 2
    assert result["paths_per_second"] > 0
