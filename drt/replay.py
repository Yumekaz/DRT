"""Failure bundle replay and source drift validation helpers."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Union

from .checker import load_target
from .exceptions import DivergenceError, format_replay_failure
from .runtime import DRTRuntime


PathLike = Union[str, Path]
Target = Callable[[], object]


@dataclass(frozen=True)
class SourceDrift:
    """A source file hash mismatch for a replayed failure bundle."""

    path: Path
    expected_sha256: Optional[str]
    actual_sha256: Optional[str]
    status: str
    expected_size_bytes: Optional[int] = None
    actual_size_bytes: Optional[int] = None


@dataclass(frozen=True)
class BundleReplayResult:
    """Structured result from replaying a failure bundle."""

    bundle_path: Path
    target_path: str
    reproduced: bool
    source_changed: bool
    source_drifts: Sequence[SourceDrift] = field(default_factory=tuple)
    expected_exception_type: str = ""
    expected_exception_message: str = ""
    actual_exception_type: str = ""
    actual_exception_message: str = ""
    failure_report: str = ""
    log_path: Optional[Path] = None
    schedule_choices: Sequence[int] = field(default_factory=tuple)


def load_bundle_metadata(bundle_path: PathLike) -> Mapping[str, Any]:
    """Load ``metadata.json`` from a failure bundle."""

    metadata_path = Path(bundle_path) / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing bundle metadata: {metadata_path}")
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def load_source_hashes(bundle_path: PathLike) -> Mapping[str, Any]:
    """Load ``source_hashes.json`` from a failure bundle."""

    source_hashes_path = Path(bundle_path) / "source_hashes.json"
    if not source_hashes_path.exists():
        raise FileNotFoundError(f"Missing bundle source hashes: {source_hashes_path}")
    return json.loads(source_hashes_path.read_text(encoding="utf-8"))


def validate_source_hashes(bundle_path: PathLike) -> List[SourceDrift]:
    """Compare bundled source hashes with files currently on disk."""

    source_hashes = load_source_hashes(bundle_path)
    drifts: List[SourceDrift] = []

    for entry in source_hashes.get("files", []):
        path = Path(str(entry.get("path", "")))
        expected_sha256 = entry.get("sha256")
        expected_size = _optional_int(entry.get("size_bytes"))

        if not path.exists() or not path.is_file():
            drifts.append(
                SourceDrift(
                    path=path,
                    expected_sha256=expected_sha256,
                    actual_sha256=None,
                    status="missing",
                    expected_size_bytes=expected_size,
                    actual_size_bytes=None,
                )
            )
            continue

        actual = _hash_file(path)
        if actual["sha256"] != expected_sha256:
            drifts.append(
                SourceDrift(
                    path=path,
                    expected_sha256=expected_sha256,
                    actual_sha256=actual["sha256"],
                    status="changed",
                    expected_size_bytes=expected_size,
                    actual_size_bytes=actual["size_bytes"],
                )
            )

    return drifts


def get_bundle_schedule_choices(bundle_path: PathLike) -> List[int]:
    """Return schedule choices stored in a failure bundle."""

    bundle = Path(bundle_path)
    metadata = load_bundle_metadata(bundle)
    artifacts = metadata.get("artifacts") or {}
    choices_name = artifacts.get("schedule_choices", "schedule_choices.json")
    choices_path = bundle / choices_name

    if choices_path.exists():
        payload = json.loads(choices_path.read_text(encoding="utf-8"))
        return [int(value) for value in payload.get("choices", [])]

    schedule = metadata.get("schedule") or {}
    if "choices" in schedule:
        return [int(value) for value in schedule.get("choices", [])]

    return [int(value) for value in metadata.get("schedule_choices", [])]


def replay_bundle(
    bundle_path: PathLike,
    target: Optional[Union[str, Target]] = None,
) -> BundleReplayResult:
    """Replay a failure bundle with its stored scripted schedule choices."""

    bundle = Path(bundle_path)
    metadata = load_bundle_metadata(bundle)
    source_drifts = tuple(validate_source_hashes(bundle))
    choices = tuple(get_bundle_schedule_choices(bundle))
    target_path, callable_target = _resolve_replay_target(metadata, target)

    expected = metadata.get("failure") or metadata.get("exception") or {}
    expected_type = str(expected.get("type") or "")
    expected_message = str(expected.get("message") or "")

    log_path = _temporary_log_path()
    captured: dict[str, BaseException] = {}

    def monitored_target() -> object:
        try:
            return callable_target()
        except BaseException as exc:
            captured["exception"] = exc
            return None

    actual_exception: Optional[BaseException] = None
    try:
        runtime = DRTRuntime(
            mode="record",
            log_path=str(log_path),
            schedule_strategy="scripted",
            schedule_choices=list(choices),
        )
        runtime.run(monitored_target)
    except BaseException as exc:
        actual_exception = exc

    if actual_exception is None:
        actual_exception = captured.get("exception")

    actual_type = type(actual_exception).__name__ if actual_exception else ""
    actual_message = str(actual_exception) if actual_exception else ""
    failure_report = ""
    if isinstance(actual_exception, DivergenceError):
        failure_report = format_replay_failure(
            actual_exception,
            source_changed=bool(source_drifts),
            source_drifts=source_drifts,
        )
    reproduced = _exception_matches(
        expected_type,
        expected_message,
        actual_type,
        actual_message,
    )

    return BundleReplayResult(
        bundle_path=bundle,
        target_path=target_path,
        reproduced=reproduced,
        source_changed=bool(source_drifts),
        source_drifts=source_drifts,
        expected_exception_type=expected_type,
        expected_exception_message=expected_message,
        actual_exception_type=actual_type,
        actual_exception_message=actual_message,
        failure_report=failure_report,
        log_path=log_path,
        schedule_choices=choices,
    )


def _resolve_replay_target(
    metadata: Mapping[str, Any],
    target: Optional[Union[str, Target]],
) -> tuple[str, Target]:
    if target is None:
        target_path = ((metadata.get("target") or {}).get("path") or "").strip()
        if not target_path:
            raise ValueError("Bundle metadata does not include target.path")
        return target_path, load_target(target_path)

    if isinstance(target, str):
        return target, load_target(target)

    if not callable(target):
        raise TypeError("target must be an import path, callable, or None")

    module = getattr(target, "__module__", "")
    qualname = getattr(target, "__qualname__", getattr(target, "__name__", "target"))
    target_path = f"{module}:{qualname}" if module else qualname
    return target_path, target


def _exception_matches(
    expected_type: str,
    expected_message: str,
    actual_type: str,
    actual_message: str,
) -> bool:
    if not actual_type:
        return False
    if expected_type and actual_type != expected_type:
        return False
    if expected_message and actual_message != expected_message:
        return False
    return True


def _temporary_log_path() -> Path:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    try:
        return Path(temp_file.name)
    finally:
        temp_file.close()


def _hash_file(path: Path) -> Mapping[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
    }


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
