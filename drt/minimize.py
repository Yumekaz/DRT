"""Schedule-choice minimization for DRT failure bundles."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from .runtime import DRTRuntime


@dataclass(frozen=True)
class ReproductionResult:
    """Result of attempting to reproduce a bundled failure."""

    reproduced: bool
    exception_type: str = ""
    exception_message: str = ""
    log_path: Optional[Path] = None
    choices: Sequence[int] = ()


@dataclass(frozen=True)
class MinimizeResult:
    """Summary of a minimization run."""

    original_choices: int
    minimized_choices: int
    attempts: int
    bundle_path: Path
    reproduced: bool


def load_bundle_metadata(bundle_path: Path | str) -> dict:
    """Load metadata.json from a failure bundle."""
    path = Path(bundle_path)
    metadata_path = path / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing bundle metadata: {metadata_path}")
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def get_bundle_schedule_choices(bundle_path: Path | str) -> List[int]:
    """Return recorded schedule choices from a failure bundle."""
    path = Path(bundle_path)
    choices_path = path / "schedule_choices.json"
    if choices_path.exists():
        payload = json.loads(choices_path.read_text(encoding="utf-8"))
        return [int(value) for value in payload.get("choices", [])]

    metadata = load_bundle_metadata(path)
    return [int(value) for value in metadata.get("schedule_choices", [])]


def exception_matches(metadata: dict, exc: BaseException) -> bool:
    """Check whether an exception is the same failure class as a bundle."""
    expected = metadata.get("exception") or metadata.get("failure") or {}
    expected_type = expected.get("type")
    expected_message = expected.get("message")
    actual_type = type(exc).__name__
    actual_message = str(exc)

    if expected_type and actual_type != expected_type:
        return False
    if expected_message and actual_message != expected_message:
        return False
    return True


def reproduce_with_choices(
    target: Callable[[], object],
    choices: Sequence[int],
    metadata: dict,
) -> ReproductionResult:
    """Run a target with scripted schedule choices and report if it fails alike."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    log_path = Path(temp_file.name)
    temp_file.close()

    captured: dict[str, BaseException] = {}

    def monitored_target() -> object:
        try:
            return target()
        except BaseException as exc:
            captured["exception"] = exc
            return None

    try:
        runtime = DRTRuntime(
            mode="record",
            log_path=str(log_path),
            schedule_strategy="scripted",
            schedule_choices=list(choices),
        )
        runtime.run(monitored_target)
    except BaseException as exc:
        return ReproductionResult(
            reproduced=exception_matches(metadata, exc),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            log_path=log_path,
            choices=list(choices),
        )

    if "exception" in captured:
        exc = captured["exception"]
        return ReproductionResult(
            reproduced=exception_matches(metadata, exc),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            log_path=log_path,
            choices=list(choices),
        )

    try:
        log_path.unlink()
    except OSError:
        pass

    return ReproductionResult(
        reproduced=False,
        log_path=None,
        choices=list(choices),
    )


def ddmin(
    choices: Sequence[int],
    reproduces: Callable[[Sequence[int]], bool],
) -> tuple[List[int], int]:
    """
    Delta-debug a schedule-choice list.

    This is intentionally conservative: it deletes contiguous chunks and keeps
    a candidate only if the original failure still reproduces.
    """
    candidate = list(choices)
    attempts = 0
    granularity = 2

    while len(candidate) >= 2:
        chunk_size = max(1, len(candidate) // granularity)
        reduced = False

        for start in range(0, len(candidate), chunk_size):
            trial = candidate[:start] + candidate[start + chunk_size :]
            if len(trial) == len(candidate):
                continue

            attempts += 1
            if reproduces(trial):
                candidate = trial
                granularity = max(2, granularity - 1)
                reduced = True
                break

        if reduced:
            continue

        if granularity >= len(candidate):
            break
        granularity = min(len(candidate), granularity * 2)

    return candidate, attempts


def write_minimized_bundle(
    original_bundle: Path | str,
    output_bundle: Path | str,
    choices: Sequence[int],
    reproduction: ReproductionResult,
    attempts: int,
) -> Path:
    """Copy a bundle and replace its trace/choice metadata with minimized data."""
    source = Path(original_bundle)
    destination = Path(output_bundle)

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    if reproduction.log_path is not None and reproduction.log_path.exists():
        shutil.copy2(reproduction.log_path, destination / "trace.log")

    (destination / "schedule_choices.json").write_text(
        json.dumps(
            {
                "choices": list(choices),
                "minimized": True,
                "attempts": attempts,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    metadata = load_bundle_metadata(destination)
    metadata["minimized"] = {
        "original_choices": len(get_bundle_schedule_choices(source)),
        "minimized_choices": len(choices),
        "attempts": attempts,
    }
    metadata["schedule_choices"] = list(choices)
    (destination / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return destination


def minimize_bundle(
    bundle_path: Path | str,
    target: Callable[[], object],
    output_path: Path | str | None = None,
) -> MinimizeResult:
    """Minimize a failure bundle's scripted schedule choices."""
    bundle = Path(bundle_path)
    metadata = load_bundle_metadata(bundle)
    original_choices = get_bundle_schedule_choices(bundle)

    if not original_choices:
        raise ValueError("Failure bundle does not contain schedule choices")

    best_reproduction: Optional[ReproductionResult] = None

    def reproduces(candidate: Sequence[int]) -> bool:
        nonlocal best_reproduction
        result = reproduce_with_choices(target, candidate, metadata)
        if result.reproduced:
            if best_reproduction and best_reproduction.log_path:
                try:
                    best_reproduction.log_path.unlink()
                except OSError:
                    pass
            best_reproduction = result
            return True

        if result.log_path:
            try:
                result.log_path.unlink()
            except OSError:
                pass
        return False

    minimized, attempts = ddmin(original_choices, reproduces)

    if best_reproduction is None:
        best_reproduction = reproduce_with_choices(target, minimized, metadata)

    destination = (
        Path(output_path)
        if output_path is not None
        else bundle.with_name(f"{bundle.name}-minimized")
    )

    if best_reproduction.reproduced:
        write_minimized_bundle(
            bundle,
            destination,
            minimized,
            best_reproduction,
            attempts,
        )

    return MinimizeResult(
        original_choices=len(original_choices),
        minimized_choices=len(minimized),
        attempts=attempts,
        bundle_path=destination,
        reproduced=best_reproduction.reproduced,
    )
