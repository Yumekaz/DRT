#!/usr/bin/env python3
"""
Small DRT benchmark for a record/replay round trip.

This is intentionally simple and repeatable. It measures:
  1. Plain execution of a tiny workload
  2. Recording the same workload with DRT
  3. Replaying the recorded workload with DRT

The goal is not to produce a synthetic microbenchmark number that means
everything. The goal is to give a fast, runnable sanity check for the runtime
overhead of the supported workflow.
"""

from __future__ import annotations

import os
import statistics
import tempfile
import time
import threading
from pathlib import Path

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import DRTRuntime, DRTThread, runtime_yield


def make_temp_log_path() -> str:
    """Create a cross-platform temporary log path."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    temp_file.close()
    return temp_file.name


def tiny_workload(
    thread_factory,
    checkpoint,
    thread_count: int = 3,
    steps_per_thread: int = 40,
) -> int:
    """Run a tiny shared-counter workload with the provided threading model."""
    counter = {"value": 0}

    def worker() -> None:
        for _ in range(steps_per_thread):
            snapshot = counter["value"]
            checkpoint()
            counter["value"] = snapshot + 1

    threads = [thread_factory(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return counter["value"]


def plain_checkpoint() -> None:
    """Give the OS scheduler a chance to switch threads in the plain baseline."""
    time.sleep(0)


def plain_workload() -> int:
    """Run the workload with normal Python threads."""
    return tiny_workload(threading.Thread, plain_checkpoint)


def drt_workload() -> int:
    """Run the workload with DRT-managed threads."""
    return tiny_workload(DRTThread, runtime_yield)


def timed(label: str, func):
    """Run a callable once and return (label, seconds, result)."""
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    return label, elapsed, result


def format_row(name: str, elapsed: float, result: object) -> str:
    return f"{name:>12}  {elapsed:8.4f}s  result={result}"


def main() -> int:
    log_path = make_temp_log_path()
    try:
        samples = []

        samples.append(timed("plain", plain_workload))
        samples.append(
            timed(
                "record",
                lambda: DRTRuntime(mode="record", log_path=log_path).run(drt_workload),
            )
        )
        samples.append(
            timed(
                "replay",
                lambda: DRTRuntime(mode="replay", log_path=log_path).run(drt_workload),
            )
        )

        print("DRT benchmark")
        print("=============")
        for label, elapsed, result in samples:
            print(format_row(label, elapsed, result))

        timings = [elapsed for _, elapsed, _ in samples]
        print()
        print(f"Slowest/fastest ratio: {max(timings) / min(timings):.2f}x")
        print(f"Median time: {statistics.median(timings):.4f}s")
        return 0
    finally:
        try:
            Path(log_path).unlink(missing_ok=True)
        except TypeError:
            if Path(log_path).exists():
                Path(log_path).unlink()


if __name__ == "__main__":
    raise SystemExit(main())
