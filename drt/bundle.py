"""Failure bundle helpers for check-style DRT runs.

The checker creates a small, portable directory when a target fails.  The
directory is intentionally plain files so the CLI, CI jobs, or humans can
inspect it without importing DRT.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import platform
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union


PathLike = Union[str, os.PathLike]


@dataclass(frozen=True)
class FailureBundle:
    """Paths for a materialized DRT failure bundle."""

    path: Path
    trace_path: Path
    metadata_path: Path
    failure_path: Path
    source_hashes_path: Path


def create_failure_bundle(
    trace_path: PathLike,
    bundle_root: PathLike,
    *,
    target_path: str,
    run_index: int,
    total_runs: int,
    failure_type: str,
    failure_message: str,
    traceback_text: str,
    target: Optional[Callable[..., Any]] = None,
    schedule: Optional[Mapping[str, Any]] = None,
    runtime: Optional[Mapping[str, Any]] = None,
    extra_metadata: Optional[Mapping[str, Any]] = None,
    extra_source_paths: Sequence[PathLike] = (),
) -> FailureBundle:
    """Create a replay-oriented failure bundle from a recorded trace.

    Args:
        trace_path: Source DRT log to copy into the bundle as ``trace.log``.
        bundle_root: Directory that will contain per-failure bundle folders.
        target_path: Import path or display name for the checked callable.
        run_index: One-based run index that produced the failure.
        total_runs: Number of requested check runs.
        failure_type: Exception class name or failure category.
        failure_message: Short exception message.
        traceback_text: Full formatted traceback for ``failure.txt``.
        target: Callable under test, used to locate source for hashing.
        schedule: Schedule metadata for the failed run.
        runtime: Runtime metadata for the failed run.
        extra_metadata: Additional JSON-serializable metadata to merge.
        extra_source_paths: Extra files to hash alongside the target source.

    Returns:
        A ``FailureBundle`` containing paths to the generated files.
    """

    now = datetime.now(timezone.utc)
    root = Path(bundle_root)
    root.mkdir(parents=True, exist_ok=True)

    bundle_id = _bundle_id(target_path, run_index, now)
    bundle_dir = _make_unique_dir(root, bundle_id)

    bundle_trace = bundle_dir / "trace.log"
    trace_metadata = _copy_trace(Path(trace_path), bundle_trace)

    source_hashes = collect_source_hashes(target=target, extra_paths=extra_source_paths)
    source_hashes_path = bundle_dir / "source_hashes.json"
    _write_json(source_hashes_path, source_hashes)

    schedule_choices = [
        int(choice)
        for choice in (schedule or {}).get("choices", [])
    ]
    schedule_choices_path = bundle_dir / "schedule_choices.json"
    _write_json(
        schedule_choices_path,
        {
            "schema_version": 1,
            "choices": schedule_choices,
        },
    )

    failure_path = bundle_dir / "failure.txt"
    failure_path.write_text(
        _format_failure_text(
            target_path=target_path,
            run_index=run_index,
            total_runs=total_runs,
            failure_type=failure_type,
            failure_message=failure_message,
            traceback_text=traceback_text,
            schedule=schedule or {},
        ),
        encoding="utf-8",
    )

    metadata = {
        "schema_version": 1,
        "bundle_id": bundle_dir.name,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "target": {
            "path": target_path,
            "module": getattr(target, "__module__", None) if target else None,
            "qualname": getattr(target, "__qualname__", None) if target else None,
        },
        "run": {
            "index": run_index,
            "total": total_runs,
        },
        "failure": {
            "type": failure_type,
            "message": failure_message,
        },
        "schedule": dict(schedule or {}),
        "runtime": dict(runtime or {}),
        "artifacts": {
            "trace_log": "trace.log",
            "failure_report": "failure.txt",
            "source_hashes": "source_hashes.json",
            "schedule_choices": "schedule_choices.json",
        },
        "schedule_choices": schedule_choices,
        "trace": trace_metadata,
        "environment": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
        },
    }
    if extra_metadata:
        metadata.update(dict(extra_metadata))

    metadata_path = bundle_dir / "metadata.json"
    _write_json(metadata_path, metadata)

    return FailureBundle(
        path=bundle_dir,
        trace_path=bundle_trace,
        metadata_path=metadata_path,
        failure_path=failure_path,
        source_hashes_path=source_hashes_path,
    )


def collect_source_hashes(
    *,
    target: Optional[Callable[..., Any]] = None,
    extra_paths: Sequence[PathLike] = (),
) -> Dict[str, Any]:
    """Collect SHA-256 hashes for source files that can be located."""

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "files": [],
        "unavailable": [],
    }

    paths: List[Path] = []
    unavailable: List[Dict[str, str]] = []

    if target is not None:
        try:
            source_path = inspect.getsourcefile(target) or inspect.getfile(target)
            if source_path:
                paths.append(Path(source_path))
            else:
                unavailable.append(
                    {
                        "target": _target_name(target),
                        "reason": "no source file reported by inspect",
                    }
                )
        except (OSError, TypeError) as exc:
            unavailable.append(
                {
                    "target": _target_name(target),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    paths.extend(Path(path) for path in extra_paths)

    seen = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)

        if not resolved.exists() or not resolved.is_file():
            unavailable.append({"path": key, "reason": "file not found"})
            continue

        try:
            result["files"].append(_hash_file(resolved))
        except OSError as exc:
            unavailable.append(
                {
                    "path": key,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    result["unavailable"] = unavailable
    return result


def _bundle_id(target_path: str, run_index: int, now: datetime) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
    slug = _slug(target_path, fallback="target")
    suffix = uuid.uuid4().hex[:8]
    return f"failure-{slug}-run-{run_index}-{stamp}-{suffix}"


def _make_unique_dir(root: Path, bundle_id: str) -> Path:
    for attempt in range(1000):
        suffix = "" if attempt == 0 else f"-{attempt}"
        candidate = root / f"{bundle_id}{suffix}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not allocate a unique bundle directory under {root}")


def _copy_trace(source: Path, destination: Path) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "source_path": str(source),
        "copied": False,
        "available": False,
        "size_bytes": 0,
        "sha256": None,
    }

    if source.exists() and source.is_file():
        shutil.copy2(source, destination)
        stat = destination.stat()
        metadata.update(
            {
                "copied": True,
                "available": True,
                "size_bytes": stat.st_size,
                "sha256": _sha256_file(destination),
            }
        )
    else:
        destination.write_bytes(b"")
        metadata["reason"] = "source trace was not available"

    return metadata


def _format_failure_text(
    *,
    target_path: str,
    run_index: int,
    total_runs: int,
    failure_type: str,
    failure_message: str,
    traceback_text: str,
    schedule: Mapping[str, Any],
) -> str:
    lines = [
        "DRT check failure",
        "",
        f"Target: {target_path}",
        f"Run: {run_index} of {total_runs}",
        f"Failure: {failure_type}: {failure_message}",
    ]
    if schedule:
        lines.append(f"Schedule: {json.dumps(_json_safe(schedule), sort_keys=True)}")
    lines.extend(["", "Traceback:", traceback_text.rstrip(), ""])
    return "\n".join(lines)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_safe(data), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _hash_file(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _slug(value: str, *, fallback: str) -> str:
    cleaned = []
    for char in value:
        if char.isalnum() or char in ("-", "_", "."):
            cleaned.append(char)
        elif char in (":", os.sep):
            cleaned.append("-")
        else:
            cleaned.append("-")

    slug = "".join(cleaned).strip("-._")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:80] or fallback


def _target_name(target: Callable[..., Any]) -> str:
    module = getattr(target, "__module__", "")
    qualname = getattr(target, "__qualname__", repr(target))
    return f"{module}:{qualname}" if module else qualname
