from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_benchmark(
    *,
    file_count: int = 200,
    file_bytes: int = 4_096,
    runs: int = 3,
) -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Robocopy batching benchmark requires Windows")
    if file_count < 2 or file_bytes < 1 or runs < 1:
        raise ValueError("file_count, file_bytes, and runs are out of range")
    with tempfile.TemporaryDirectory(prefix="mediasync-robocopy-benchmark-") as raw_root:
        root = Path(raw_root)
        source = root / "source"
        source.mkdir()
        names = tuple(f"file-{index:04d}.bin" for index in range(file_count))
        payload = bytes((index % 251 for index in range(file_bytes)))
        for name in names:
            (source / name).write_bytes(payload)
        batch_times = tuple(
            _run_batch(source=source, destination=root / f"batch-{run}", names=names)
            for run in range(runs)
        )
        individual_times = tuple(
            _run_individual(
                source=source,
                destination=root / f"individual-{run}",
                names=names,
            )
            for run in range(runs)
        )
    batch_median = statistics.median(batch_times)
    individual_median = statistics.median(individual_times)
    return {
        "benchmark": "robocopy-directory-batching-v1",
        "batch_median_ms": round(batch_median * 1000, 3),
        "batch_runs_ms": [round(value * 1000, 3) for value in batch_times],
        "file_bytes": file_bytes,
        "file_count": file_count,
        "individual_median_ms": round(individual_median * 1000, 3),
        "individual_runs_ms": [
            round(value * 1000, 3) for value in individual_times
        ],
        "process_amortization_speedup": round(individual_median / batch_median, 3),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runs": runs,
    }


def _run_batch(*, source: Path, destination: Path, names: tuple[str, ...]) -> float:
    destination.mkdir()
    log_path = destination.parent / f"{destination.name}.log"
    started = time.perf_counter()
    completed = subprocess.run(
        _command(source, destination, names, log_path=log_path),
        check=False,
        capture_output=True,
    )
    elapsed = time.perf_counter() - started
    _validate_run(completed, destination=destination, expected_count=len(names))
    return elapsed


def _run_individual(
    *,
    source: Path,
    destination: Path,
    names: tuple[str, ...],
) -> float:
    destination.mkdir()
    started = time.perf_counter()
    for index, name in enumerate(names):
        completed = subprocess.run(
            _command(
                source,
                destination,
                (name,),
                log_path=destination.parent / f"{destination.name}-{index:04d}.log",
            ),
            check=False,
            capture_output=True,
        )
        if completed.returncode > 7:
            raise RuntimeError(f"Robocopy failed with exit code {completed.returncode}")
    elapsed = time.perf_counter() - started
    _validate_destination(destination, expected_count=len(names))
    return elapsed


def _command(
    source: Path,
    destination: Path,
    names: tuple[str, ...],
    *,
    log_path: Path,
) -> tuple[str, ...]:
    return (
        "Robocopy.exe",
        str(source),
        str(destination),
        *names,
        "/Z",
        "/R:1",
        "/W:1",
        "/COPY:DAT",
        "/DCOPY:DA",
        "/NP",
        "/NFL",
        "/NDL",
        "/MT:8",
        f"/UNILOG:{log_path}",
    )


def _validate_run(
    completed: subprocess.CompletedProcess[bytes],
    *,
    destination: Path,
    expected_count: int,
) -> None:
    if completed.returncode > 7:
        raise RuntimeError(f"Robocopy failed with exit code {completed.returncode}")
    _validate_destination(destination, expected_count=expected_count)


def _validate_destination(destination: Path, *, expected_count: int) -> None:
    actual_count = sum(path.is_file() for path in destination.iterdir())
    if actual_count != expected_count:
        raise RuntimeError(
            f"Robocopy produced {actual_count} files; expected {expected_count}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare one Robocopy directory batch with process-per-file copying."
    )
    parser.add_argument("--files", type=int, default=200)
    parser.add_argument("--file-bytes", type=int, default=4_096)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_benchmark(
        file_count=args.files,
        file_bytes=args.file_bytes,
        runs=args.runs,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
