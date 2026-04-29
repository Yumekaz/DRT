"""Small callable targets for exercising ``drt check`` manually."""

from drt import DRTThread, runtime_yield


def counter_workload() -> int:
    """A DRT-managed workload that should pass under schedule exploration."""
    counter = {"value": 0}

    def worker() -> None:
        for _ in range(3):
            snapshot = counter["value"]
            runtime_yield()
            counter["value"] = snapshot + 1

    threads = [DRTThread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return counter["value"]


def intentional_failure() -> None:
    """A deterministic failing target used to smoke-test failure bundles."""
    assert False, "intentional drt check failure"


def scheduled_failure() -> None:
    """A failing target that records scheduler choices before failing."""
    seen = []

    def worker(name: str) -> None:
        seen.append((name, "start"))
        runtime_yield()
        seen.append((name, "end"))

    threads = [DRTThread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == 999, "scheduled drt check failure"
