"""Check-runner internals for deterministic concurrency testing.

The CLI uses these same Python building blocks so bundle creation, target
loading, and runtime feature detection stay testable outside argument parsing.
"""

from __future__ import annotations

import importlib
import inspect
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from . import __version__
from .bundle import create_failure_bundle
from .explorer import SchedulePlan, ScheduleRunSpec
from .runtime import DRTRuntime


Target = Callable[..., Any]


@dataclass(frozen=True)
class CheckRun:
    """Result for one record-mode check run."""

    run_index: int
    success: bool
    duration_seconds: float
    schedule_seed: Optional[int] = None
    schedule_strategy: Optional[str] = None
    log_path: Optional[Path] = None
    bundle_path: Optional[Path] = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    applied_runtime_kwargs: Mapping[str, Any] = field(default_factory=dict)
    unsupported_runtime_kwargs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    """Structured result returned by ``run_check``."""

    target_path: str
    requested_runs: int
    completed_runs: int
    passed: bool
    runs: Sequence[CheckRun]

    @property
    def failing_run(self) -> Optional[CheckRun]:
        """Return the first failing run, if any."""

        for run in self.runs:
            if not run.success:
                return run
        return None

    @property
    def failure_bundle(self) -> Optional[Path]:
        """Return the first failure bundle path, if any."""

        run = self.failing_run
        return run.bundle_path if run else None


@dataclass(frozen=True)
class _FailureInfo:
    origin: str
    exception_type: str
    exception_message: str
    traceback_text: str


def load_target(import_path: str) -> Target:
    """Load a target callable from ``module:function`` syntax."""

    if not isinstance(import_path, str) or ":" not in import_path:
        raise ValueError("Target must use 'module:function' import syntax")

    module_name, attr_path = import_path.split(":", 1)
    module_name = module_name.strip()
    attr_path = attr_path.strip()

    if not module_name or not attr_path:
        raise ValueError("Target must include both module and callable name")

    module = importlib.import_module(module_name)
    target: Any = module
    for part in attr_path.split("."):
        if not part:
            raise ValueError(f"Invalid target attribute path: {attr_path!r}")
        target = getattr(target, part)

    if not callable(target):
        raise TypeError(f"Target is not callable: {import_path}")

    return target


def run_check(
    target: Union[str, Target],
    *,
    runs: int = 1,
    target_args: Sequence[Any] = (),
    target_kwargs: Optional[Mapping[str, Any]] = None,
    bundle_root: Optional[Union[str, Path]] = None,
    schedule_strategy: Optional[str] = None,
    schedule_seed: Optional[int] = None,
    exploration_plan: Optional[Union[SchedulePlan, Sequence[ScheduleRunSpec]]] = None,
    stop_on_failure: bool = True,
    runtime_cls: type = DRTRuntime,
) -> CheckResult:
    """Run a target repeatedly under ``DRTRuntime`` record mode.

    ``schedule_seed`` is treated as a base seed: run 1 receives that seed,
    run 2 receives ``seed + 1``, and so on.  If the runtime does not support a
    requested schedule kwarg, the checker omits it and records the omission in
    each ``CheckRun`` instead of failing before the target runs.
    """

    plan_specs = _normalize_exploration_plan(exploration_plan)
    if plan_specs is not None:
        runs = len(plan_specs)

    if runs < 1:
        raise ValueError("runs must be >= 1")

    target_path, callable_target = _resolve_target(target)
    kwargs = dict(target_kwargs or {})
    root = Path(bundle_root) if bundle_root is not None else Path(".drt") / "failures"

    check_runs: List[CheckRun] = []

    with tempfile.TemporaryDirectory(prefix="drt-check-") as tmpdir:
        tmp_root = Path(tmpdir)

        for ordinal in range(1, runs + 1):
            plan_spec = plan_specs[ordinal - 1] if plan_specs is not None else None
            run_index = plan_spec.run_index if plan_spec is not None else ordinal
            log_path = tmp_root / f"run-{run_index}.log"
            if plan_spec is not None:
                requested_schedule = plan_spec.runtime_kwargs()
            else:
                requested_schedule = _schedule_kwargs_for_run(
                    run_index=run_index,
                    schedule_strategy=schedule_strategy,
                    schedule_seed=schedule_seed,
                )
            applied_schedule, unsupported_schedule = _partition_supported_kwargs(
                runtime_cls,
                requested_schedule,
            )
            runtime_kwargs = {
                "mode": "record",
                "log_path": str(log_path),
                **applied_schedule,
            }

            captured: Dict[str, _FailureInfo] = {}
            runtime = None

            def monitored_target() -> Any:
                try:
                    return callable_target(*target_args, **kwargs)
                except Exception as exc:
                    captured["failure"] = _failure_from_current_exception(
                        "target",
                        exc,
                    )
                    return None

            start = time.perf_counter()
            runtime_failure: Optional[_FailureInfo] = None

            try:
                runtime = runtime_cls(**runtime_kwargs)
                runtime.run(monitored_target)
            except Exception as exc:
                runtime_failure = _failure_from_current_exception("runtime", exc)

            duration = time.perf_counter() - start
            failure = runtime_failure or captured.get("failure")

            if failure is None:
                check_runs.append(
                    CheckRun(
                        run_index=run_index,
                        success=True,
                        duration_seconds=duration,
                        schedule_seed=applied_schedule.get("schedule_seed"),
                        schedule_strategy=applied_schedule.get("schedule_strategy"),
                        applied_runtime_kwargs=dict(applied_schedule),
                        unsupported_runtime_kwargs=dict(unsupported_schedule),
                    )
                )
                continue

            bundle = create_failure_bundle(
                log_path,
                root,
                target_path=target_path,
                run_index=run_index,
                total_runs=runs,
                failure_type=failure.exception_type,
                failure_message=failure.exception_message,
                traceback_text=failure.traceback_text,
                target=callable_target,
                schedule={
                    "requested": requested_schedule,
                    "applied": applied_schedule,
                    "unsupported": unsupported_schedule,
                    "choices": _recorded_schedule_choices(runtime),
                },
                runtime={
                    "class": _class_name(runtime_cls),
                    "drt_version": __version__,
                    "mode": "record",
                    "duration_seconds": duration,
                    "failure_origin": failure.origin,
                },
                extra_metadata={
                    "failure_origin": failure.origin,
                },
            )
            check_runs.append(
                CheckRun(
                    run_index=run_index,
                    success=False,
                    duration_seconds=duration,
                    schedule_seed=applied_schedule.get("schedule_seed"),
                    schedule_strategy=applied_schedule.get("schedule_strategy"),
                    log_path=bundle.trace_path,
                    bundle_path=bundle.path,
                    exception_type=failure.exception_type,
                    exception_message=failure.exception_message,
                    applied_runtime_kwargs=dict(applied_schedule),
                    unsupported_runtime_kwargs=dict(unsupported_schedule),
                )
            )

            if stop_on_failure:
                break

    return CheckResult(
        target_path=target_path,
        requested_runs=runs,
        completed_runs=len(check_runs),
        passed=all(run.success for run in check_runs),
        runs=tuple(check_runs),
    )


def runtime_supports_kwarg(runtime_cls: type, kwarg: str) -> bool:
    """Return whether ``runtime_cls`` accepts a constructor keyword."""

    try:
        signature = inspect.signature(runtime_cls.__init__)
    except (TypeError, ValueError, AttributeError):
        return False

    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == kwarg:
            return True
    return False


def _resolve_target(target: Union[str, Target]) -> Tuple[str, Target]:
    if isinstance(target, str):
        return target, load_target(target)
    if not callable(target):
        raise TypeError("target must be an import path or callable")
    return _callable_import_path(target), target


def _callable_import_path(target: Target) -> str:
    module = getattr(target, "__module__", "")
    qualname = getattr(target, "__qualname__", getattr(target, "__name__", "target"))
    if module:
        return f"{module}:{qualname}"
    return qualname


def _schedule_kwargs_for_run(
    *,
    run_index: int,
    schedule_strategy: Optional[str],
    schedule_seed: Optional[int],
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if schedule_strategy is not None:
        kwargs["schedule_strategy"] = schedule_strategy
    if schedule_seed is not None:
        kwargs["schedule_seed"] = schedule_seed + run_index - 1
    return kwargs


def _normalize_exploration_plan(
    exploration_plan: Optional[Union[SchedulePlan, Sequence[ScheduleRunSpec]]],
) -> Optional[Tuple[ScheduleRunSpec, ...]]:
    if exploration_plan is None:
        return None

    if isinstance(exploration_plan, SchedulePlan):
        specs = tuple(exploration_plan.runs)
    else:
        specs = tuple(exploration_plan)

    if not specs:
        raise ValueError("exploration_plan must contain at least one run spec")

    for spec in specs:
        if not isinstance(spec, ScheduleRunSpec):
            raise TypeError("exploration_plan entries must be ScheduleRunSpec objects")
    return specs


def _partition_supported_kwargs(
    runtime_cls: type,
    kwargs: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    supported: Dict[str, Any] = {}
    unsupported: Dict[str, Any] = {}

    for key, value in kwargs.items():
        if runtime_supports_kwarg(runtime_cls, key):
            supported[key] = value
        else:
            unsupported[key] = value

    return supported, unsupported


def _failure_from_current_exception(origin: str, exc: Exception) -> _FailureInfo:
    return _FailureInfo(
        origin=origin,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        traceback_text=traceback.format_exc(),
    )


def _class_name(runtime_cls: type) -> str:
    module = getattr(runtime_cls, "__module__", "")
    qualname = getattr(runtime_cls, "__qualname__", getattr(runtime_cls, "__name__", ""))
    return f"{module}.{qualname}" if module else qualname


def _recorded_schedule_choices(runtime: Any) -> List[int]:
    if runtime is None:
        return []

    scheduler = getattr(runtime, "scheduler", None)
    if scheduler is None:
        return []

    choices = getattr(scheduler, "recorded_schedule_choices", [])
    try:
        return [int(choice) for choice in choices]
    except TypeError:
        return []
