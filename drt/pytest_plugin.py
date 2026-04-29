"""
Pytest integration for running tests under DRT schedule exploration.

The module intentionally avoids importing pytest at module import time so it can
be imported by lightweight unit tests and tooling that do not have pytest
installed.
"""

from __future__ import annotations

import importlib
import inspect
import json
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from .explorer import build_schedule_plan
from .runtime import DRTRuntime


DEFAULT_RUNS = 10
DEFAULT_BUNDLE_DIR = ".drt-bundles"
DEFAULT_SCHEDULE_STRATEGY = "random"
DEFAULT_SCHEDULE_SEED = 1
DEFAULT_DEPTH = 4
DEFAULT_BRANCHING = 2
_DRT_DECORATOR_ATTR = "__drt_test_options__"


@dataclass(frozen=True)
class _DRTOptions:
    runs: int
    strategy: str
    seed: int
    depth: int
    branching: int
    stress_max_runs: Optional[int]
    bundle_root: Path


def drt_test(
    schedules: Any = 1000,
    strategy: str = DEFAULT_SCHEDULE_STRATEGY,
    seed: int = DEFAULT_SCHEDULE_SEED,
    bundle_dir: Optional[str] = None,
    depth: int = DEFAULT_DEPTH,
    branching: int = DEFAULT_BRANCHING,
    stress_max_runs: Optional[int] = None,
) -> Callable[..., Any]:
    """
    Decorate a pytest test function so the DRT plugin runs it under schedules.

    The decorator does not require pytest to be installed. When pytest is
    available, it also adds a normal ``drt`` marker for better test reporting.
    """
    if callable(schedules):
        function = schedules
        options = _decorator_options(
            schedules=1000,
            strategy=strategy,
            seed=seed,
            bundle_dir=bundle_dir,
            depth=depth,
            branching=branching,
            stress_max_runs=stress_max_runs,
        )
        return _attach_drt_options(function, options)

    options = _decorator_options(
        schedules=schedules,
        strategy=strategy,
        seed=seed,
        bundle_dir=bundle_dir,
        depth=depth,
        branching=branching,
        stress_max_runs=stress_max_runs,
    )

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        return _attach_drt_options(function, options)

    return decorate


def pytest_addoption(parser: Any) -> None:
    """Register DRT command-line options."""
    _add_drt_options(parser)


def pytest_configure(config: Any) -> None:
    """Register the DRT marker when pytest loads the plugin."""
    config.addinivalue_line(
        "markers",
        "drt: run this test under DRT deterministic record-mode schedule exploration; "
        "kwargs: schedules/runs, strategy, seed, depth, branching, "
        "stress_max_runs, bundle_dir",
    )


def pytest_pyfunc_call(pyfuncitem: Any) -> Optional[bool]:
    """
    Run normal test functions under DRTRuntime when DRT is enabled.

    Returning True tells pytest the test call was handled. Returning None lets
    pytest continue with its default call path.
    """
    if not _is_drt_enabled(pyfuncitem):
        return None

    call_test = _build_pytest_call(pyfuncitem)
    nodeid = getattr(pyfuncitem, "nodeid", getattr(pyfuncitem, "name", "test"))
    drt_options = _resolve_drt_options(pyfuncitem)

    _run_under_drt(
        call_test,
        nodeid=nodeid,
        runs=drt_options.runs,
        strategy=drt_options.strategy,
        seed=drt_options.seed,
        depth=drt_options.depth,
        branching=drt_options.branching,
        stress_max_runs=drt_options.stress_max_runs,
        bundle_root=drt_options.bundle_root,
        runtime_cls=DRTRuntime,
    )
    return True


def _add_drt_options(parser: Any) -> None:
    group = parser.getgroup("drt", "deterministic record/replay testing")
    group.addoption(
        "--drt",
        action="store_true",
        default=False,
        help="Run pytest test functions under DRT record-mode schedule exploration.",
    )
    group.addoption(
        "--drt-runs",
        action="store",
        type=int,
        default=DEFAULT_RUNS,
        metavar="N",
        help=f"Number of DRT record-mode runs per selected test. Default: {DEFAULT_RUNS}.",
    )
    group.addoption(
        "--drt-strategy",
        action="store",
        choices=("round_robin", "random", "exhaustive", "priority", "stress"),
        default=DEFAULT_SCHEDULE_STRATEGY,
        help=f"DRT schedule exploration strategy. Default: {DEFAULT_SCHEDULE_STRATEGY}.",
    )
    group.addoption(
        "--drt-seed",
        action="store",
        type=int,
        default=DEFAULT_SCHEDULE_SEED,
        help=f"Base seed for seeded DRT schedules. Default: {DEFAULT_SCHEDULE_SEED}.",
    )
    group.addoption(
        "--drt-depth",
        action="store",
        type=int,
        default=DEFAULT_DEPTH,
        help=f"Depth for exhaustive/priority schedule exploration. Default: {DEFAULT_DEPTH}.",
    )
    group.addoption(
        "--drt-branching",
        action="store",
        type=int,
        default=DEFAULT_BRANCHING,
        help=f"Branching factor for exhaustive/priority exploration. Default: {DEFAULT_BRANCHING}.",
    )
    group.addoption(
        "--drt-stress-max-runs",
        action="store",
        type=int,
        default=None,
        metavar="N",
        help="Maximum generated runs when --drt-strategy=stress.",
    )
    group.addoption(
        "--drt-bundle-dir",
        action="store",
        default=DEFAULT_BUNDLE_DIR,
        metavar="DIR",
        help=f"Directory for DRT failure bundles. Default: {DEFAULT_BUNDLE_DIR}.",
    )


def _is_drt_enabled(pyfuncitem: Any) -> bool:
    config = getattr(pyfuncitem, "config", None)
    option_enabled = bool(_get_option(config, "--drt", "drt", False))
    marker_enabled = _get_marker(pyfuncitem, "drt") is not None
    decorator_enabled = _decorator_kwargs(pyfuncitem) is not None
    return option_enabled or marker_enabled or decorator_enabled


def _has_marker(item: Any, marker_name: str) -> bool:
    return _get_marker(item, marker_name) is not None


def _get_marker(item: Any, marker_name: str) -> Optional[Any]:
    get_closest_marker = getattr(item, "get_closest_marker", None)
    if callable(get_closest_marker):
        marker = get_closest_marker(marker_name)
        if marker is not None:
            return marker

    iter_markers = getattr(item, "iter_markers", None)
    if callable(iter_markers):
        for marker in iter_markers():
            if getattr(marker, "name", None) == marker_name:
                return marker

    keywords = getattr(item, "keywords", {})
    if marker_name in keywords:
        try:
            return keywords[marker_name]
        except (TypeError, KeyError):
            return True
    return None


def _resolve_drt_options(pyfuncitem: Any) -> _DRTOptions:
    config = getattr(pyfuncitem, "config", None)
    raw_options: Dict[str, Any] = {
        "runs": _get_option(config, "--drt-runs", "drt_runs", DEFAULT_RUNS),
        "strategy": _get_option(
            config,
            "--drt-strategy",
            "drt_strategy",
            DEFAULT_SCHEDULE_STRATEGY,
        ),
        "seed": _get_option(config, "--drt-seed", "drt_seed", DEFAULT_SCHEDULE_SEED),
        "depth": _get_option(config, "--drt-depth", "drt_depth", DEFAULT_DEPTH),
        "branching": _get_option(
            config,
            "--drt-branching",
            "drt_branching",
            DEFAULT_BRANCHING,
        ),
        "stress_max_runs": _get_option(
            config,
            "--drt-stress-max-runs",
            "drt_stress_max_runs",
            None,
        ),
        "bundle_dir": _get_option(
            config,
            "--drt-bundle-dir",
            "drt_bundle_dir",
            DEFAULT_BUNDLE_DIR,
        ),
    }

    decorator_kwargs = _decorator_kwargs(pyfuncitem)
    if decorator_kwargs:
        raw_options.update(_without_none_values(decorator_kwargs))

    marker_kwargs = _marker_kwargs(_get_marker(pyfuncitem, "drt"))
    if marker_kwargs:
        raw_options.update(_without_none_values(marker_kwargs))

    runs_value = raw_options.get("schedules")
    if runs_value is None:
        runs_value = raw_options.get("runs", DEFAULT_RUNS)
    bundle_dir = raw_options.get("bundle_dir") or DEFAULT_BUNDLE_DIR
    return _DRTOptions(
        runs=_positive_int(runs_value, DEFAULT_RUNS),
        strategy=str(raw_options.get("strategy") or DEFAULT_SCHEDULE_STRATEGY),
        seed=_positive_int(raw_options.get("seed"), DEFAULT_SCHEDULE_SEED),
        depth=_int_at_least(raw_options.get("depth"), DEFAULT_DEPTH, 0),
        branching=_positive_int(raw_options.get("branching"), DEFAULT_BRANCHING),
        stress_max_runs=_optional_positive_int(raw_options.get("stress_max_runs")),
        bundle_root=Path(bundle_dir),
    )


def _decorator_options(
    *,
    schedules: Any,
    strategy: str,
    seed: int,
    bundle_dir: Optional[str],
    depth: int,
    branching: int,
    stress_max_runs: Optional[int],
) -> Dict[str, Any]:
    return {
        "schedules": schedules,
        "strategy": strategy,
        "seed": seed,
        "bundle_dir": bundle_dir,
        "depth": depth,
        "branching": branching,
        "stress_max_runs": stress_max_runs,
    }


def _attach_drt_options(function: Callable[..., Any], options: Dict[str, Any]) -> Callable[..., Any]:
    setattr(function, _DRT_DECORATOR_ATTR, dict(options))
    try:
        pytest = importlib.import_module("pytest")
    except ImportError:
        return function

    mark = getattr(getattr(pytest, "mark", None), "drt", None)
    if callable(mark):
        return mark(**{key: value for key, value in options.items() if value is not None})(function)
    return function


def _decorator_kwargs(item: Any) -> Optional[Dict[str, Any]]:
    function = getattr(item, "obj", item)
    options = getattr(function, _DRT_DECORATOR_ATTR, None)
    return dict(options) if isinstance(options, dict) else None


def _marker_kwargs(marker: Any) -> Dict[str, Any]:
    kwargs = getattr(marker, "kwargs", None)
    if isinstance(kwargs, dict):
        return dict(kwargs)
    return {}


def _without_none_values(options: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in options.items() if value is not None}


def _get_option(config: Any, cli_name: str, attr_name: str, default: Any) -> Any:
    if config is None:
        return default

    getoption = getattr(config, "getoption", None)
    if callable(getoption):
        for name in (cli_name, attr_name):
            try:
                value = getoption(name)
            except (AttributeError, ValueError):
                continue
            if value is not None:
                return value

    option = getattr(config, "option", None)
    if option is not None and hasattr(option, attr_name):
        value = getattr(option, attr_name)
        if value is not None:
            return value

    return default


def _positive_int(value: Any, default: int) -> int:
    return _int_at_least(value, default, 1)


def _int_at_least(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _optional_positive_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _build_pytest_call(pyfuncitem: Any) -> Callable[[], Any]:
    testfunction = pyfuncitem.obj
    funcargs = getattr(pyfuncitem, "funcargs", {})
    fixtureinfo = getattr(pyfuncitem, "_fixtureinfo", None)
    argnames = getattr(fixtureinfo, "argnames", None)

    if argnames is None:
        argnames = inspect.signature(testfunction).parameters

    testargs = {name: funcargs[name] for name in argnames if name in funcargs}
    return lambda: testfunction(**testargs)


def _run_under_drt(
    test_call: Callable[[], Any],
    *,
    nodeid: str,
    runs: int,
    strategy: str = DEFAULT_SCHEDULE_STRATEGY,
    seed: int = DEFAULT_SCHEDULE_SEED,
    depth: int = DEFAULT_DEPTH,
    branching: int = DEFAULT_BRANCHING,
    stress_max_runs: Optional[int] = None,
    bundle_root: Path,
    runtime_cls: type = DRTRuntime,
) -> None:
    plan = build_schedule_plan(
        mode=strategy,
        runs=runs,
        seed=seed,
        branching_factor=branching,
        depth=depth,
        max_runs=stress_max_runs,
    )

    for spec in plan:
        run_index = spec.run_index
        log_path = _log_path_for(bundle_root, nodeid, run_index)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        schedule_kwargs = spec.runtime_kwargs()
        runtime = None
        try:
            runtime = _make_runtime(runtime_cls, log_path, schedule_kwargs)
            runtime.run(test_call)
        except BaseException as exc:
            recorded_choices = _recorded_schedule_choices(runtime)
            if not recorded_choices:
                recorded_choices = list(spec.schedule_choices)
            _create_failure_bundle(
                bundle_root=bundle_root,
                nodeid=nodeid,
                run_index=run_index,
                log_path=log_path,
                schedule_kwargs=schedule_kwargs,
                schedule_choices=recorded_choices,
                exc=exc,
            )
            raise


def _make_runtime(
    runtime_cls: type,
    log_path: Path,
    schedule_kwargs: Mapping[str, Any],
) -> Any:
    kwargs: Dict[str, Any] = {
        "mode": "record",
        "log_path": str(log_path),
    }
    kwargs.update(
        _supported_schedule_kwargs(
            runtime_cls,
            schedule_kwargs=schedule_kwargs,
        )
    )
    return runtime_cls(**kwargs)


def _supported_schedule_kwargs(
    runtime_cls: type,
    run_index: int = 1,
    strategy: str = DEFAULT_SCHEDULE_STRATEGY,
    seed: Optional[int] = None,
    schedule_kwargs: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        signature = inspect.signature(runtime_cls)
    except (TypeError, ValueError):
        return {}

    parameters = signature.parameters
    has_var_kwargs = any(param.kind == param.VAR_KEYWORD for param in parameters.values())
    if schedule_kwargs is None:
        schedule_kwargs = {
            "schedule_strategy": strategy,
            "schedule_seed": run_index if seed is None else seed,
        }
    if has_var_kwargs:
        return dict(schedule_kwargs)
    return {key: value for key, value in schedule_kwargs.items() if key in parameters}


def _create_failure_bundle(
    *,
    bundle_root: Path,
    nodeid: str,
    run_index: int,
    log_path: Path,
    exc: BaseException,
    schedule_kwargs: Optional[Mapping[str, Any]] = None,
    schedule_choices: Sequence[int] = (),
) -> Path:
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_dir = bundle_root / f"{_safe_nodeid(nodeid)}-run-{run_index}"
    if schedule_kwargs is None:
        applied_schedule = {
            "schedule_strategy": DEFAULT_SCHEDULE_STRATEGY,
            "schedule_seed": run_index,
        }
    else:
        applied_schedule = dict(schedule_kwargs)

    richer_bundle = _try_richer_bundle(
        bundle_root=bundle_root,
        nodeid=nodeid,
        run_index=run_index,
        log_path=log_path,
        schedule_kwargs=applied_schedule,
        schedule_choices=schedule_choices,
        exc=exc,
    )
    if richer_bundle is not None:
        return richer_bundle

    bundle_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "nodeid": nodeid,
        "run_index": run_index,
        "mode": "record",
        "schedule_strategy": applied_schedule.get(
            "schedule_strategy",
            DEFAULT_SCHEDULE_STRATEGY,
        ),
        "schedule_seed": applied_schedule.get("schedule_seed"),
        "schedule_choices": list(schedule_choices),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
    }
    (bundle_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (bundle_dir / "exception.txt").write_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        encoding="utf-8",
    )
    if log_path.exists():
        shutil.copy2(log_path, bundle_dir / "execution.log")
    return bundle_dir


def _try_richer_bundle(
    *,
    bundle_root: Path,
    nodeid: str,
    run_index: int,
    log_path: Path,
    schedule_kwargs: Mapping[str, Any],
    schedule_choices: Sequence[int],
    exc: BaseException,
) -> Optional[Path]:
    try:
        bundle_module = importlib.import_module("drt.bundle")
    except ImportError:
        return None

    bundle_func = getattr(bundle_module, "create_failure_bundle", None)
    if callable(bundle_func):
        result = bundle_func(
            log_path,
            bundle_root,
            target_path=nodeid,
            run_index=run_index,
            total_runs=run_index,
            failure_type=type(exc).__name__,
            failure_message=str(exc),
            traceback_text="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
            schedule={
                "applied": {
                    **dict(schedule_kwargs),
                },
                "choices": list(schedule_choices),
            },
            runtime={
                "mode": "record",
                "source": "pytest_plugin",
            },
            extra_metadata={
                "nodeid": nodeid,
            },
        )
        return Path(getattr(result, "path", result))
    return None


def _recorded_schedule_choices(runtime: Any) -> Sequence[int]:
    scheduler = getattr(runtime, "scheduler", None)
    if scheduler is None:
        return []

    choices = getattr(scheduler, "recorded_schedule_choices", [])
    try:
        return [int(choice) for choice in choices]
    except TypeError:
        return []


def _log_path_for(bundle_root: Path, nodeid: str, run_index: int) -> Path:
    return bundle_root / "runs" / f"{_safe_nodeid(nodeid)}-run-{run_index}.log"


def _safe_nodeid(nodeid: str) -> str:
    safe_chars = []
    for char in nodeid:
        if char.isalnum() or char in ("-", "_", "."):
            safe_chars.append(char)
        else:
            safe_chars.append("_")
    safe = "".join(safe_chars).strip("._")
    return safe or "test"


__all__ = [
    "drt_test",
    "pytest_addoption",
    "pytest_configure",
    "pytest_pyfunc_call",
]
