"""Schedule exploration plan builders.

The planner is intentionally independent from the runtime.  It turns a compact
mode description into per-run scheduling kwargs that ``DRTRuntime`` or the check
runner can execute.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


PlanMode = str


@dataclass(frozen=True)
class ScheduleRunSpec:
    """Scheduling inputs for one record-mode DRT run."""

    run_index: int
    schedule_strategy: str
    schedule_seed: Optional[int] = None
    schedule_choices: Tuple[int, ...] = ()
    schedule_priorities: Mapping[int, int] = field(default_factory=dict)
    label: str = ""

    def runtime_kwargs(self) -> Dict[str, Any]:
        """Return constructor kwargs supported by ``DRTRuntime``."""

        kwargs: Dict[str, Any] = {"schedule_strategy": self.schedule_strategy}
        if self.schedule_seed is not None:
            kwargs["schedule_seed"] = self.schedule_seed
        if self.schedule_choices:
            kwargs["schedule_choices"] = list(self.schedule_choices)
        if self.schedule_priorities:
            kwargs["schedule_priorities"] = dict(self.schedule_priorities)
        return kwargs


@dataclass(frozen=True)
class SchedulePlan:
    """A deterministic collection of schedule runs."""

    mode: PlanMode
    runs: Tuple[ScheduleRunSpec, ...]
    branching_factor: Optional[int] = None
    depth: Optional[int] = None
    seed: Optional[int] = None
    max_runs: Optional[int] = None
    time_budget_seconds: Optional[float] = None

    def __len__(self) -> int:
        return len(self.runs)

    def __iter__(self) -> Iterable[ScheduleRunSpec]:
        return iter(self.runs)


def build_schedule_plan(
    mode: PlanMode = "random",
    *,
    runs: Optional[int] = None,
    seed: Optional[int] = None,
    branching_factor: int = 2,
    depth: int = 1,
    priority_choices: Optional[Sequence[int]] = None,
    max_runs: Optional[int] = None,
    time_budget_seconds: Optional[float] = None,
) -> SchedulePlan:
    """Build a bounded schedule exploration plan.

    Modes:
        ``round_robin``: ``runs`` default deterministic scheduler runs.

        ``random``: ``runs`` seeded random scheduler runs, using ``seed`` as a
        stable base seed.

        ``exhaustive``: every scripted choice sequence of length ``depth`` over
        ``range(branching_factor)``.

        ``priority``: scripted choice sequences ordered by ``priority_choices``.
        If no priority list is supplied, lower choice indexes win first.

        ``stress``: CI-friendly random run specs capped by ``max_runs``.
    """

    normalized_mode = _normalize_mode(mode)
    _validate_bounds(branching_factor=branching_factor, depth=depth)

    if normalized_mode == "round_robin":
        count = _positive_or_default(runs, 1, "runs")
        specs = tuple(
            ScheduleRunSpec(
                run_index=index + 1,
                schedule_strategy="round_robin",
                label=f"round-robin-{index + 1}",
            )
            for index in range(count)
        )
    elif normalized_mode == "random":
        count = _positive_or_default(runs, 1, "runs")
        base_seed = 0 if seed is None else int(seed)
        specs = tuple(
            ScheduleRunSpec(
                run_index=index + 1,
                schedule_strategy="random",
                schedule_seed=base_seed + index,
                label=f"random-{index + 1}",
            )
            for index in range(count)
        )
    elif normalized_mode == "exhaustive":
        choices = range(branching_factor)
        specs = _scripted_product_specs(
            mode="exhaustive",
            choice_order=choices,
            depth=depth,
            limit=runs,
        )
    elif normalized_mode == "priority":
        choice_order = _priority_choice_order(
            priority_choices,
            branching_factor=branching_factor,
        )
        specs = _scripted_product_specs(
            mode="priority",
            choice_order=choice_order,
            depth=depth,
            limit=runs,
        )
    elif normalized_mode == "stress":
        requested = _positive_or_default(runs, 50, "runs")
        cap = _positive_or_default(max_runs, requested, "max_runs")
        count = min(requested, cap)
        rng = random.Random(0 if seed is None else int(seed))
        specs = tuple(
            ScheduleRunSpec(
                run_index=index + 1,
                schedule_strategy="random",
                schedule_seed=rng.randrange(0, 2**31),
                label=f"stress-{index + 1}",
            )
            for index in range(count)
        )
    else:
        raise ValueError(
            "mode must be one of 'round_robin', 'random', 'exhaustive', "
            "'priority', or 'stress'"
        )

    return SchedulePlan(
        mode=normalized_mode,
        runs=specs,
        branching_factor=branching_factor
        if normalized_mode in {"exhaustive", "priority"}
        else None,
        depth=depth if normalized_mode in {"exhaustive", "priority"} else None,
        seed=seed,
        max_runs=max_runs if normalized_mode == "stress" else None,
        time_budget_seconds=time_budget_seconds
        if normalized_mode == "stress"
        else None,
    )


def _normalize_mode(mode: PlanMode) -> str:
    if not isinstance(mode, str):
        raise TypeError("mode must be a string")
    return mode.strip().lower()


def _validate_bounds(*, branching_factor: int, depth: int) -> None:
    if branching_factor < 1:
        raise ValueError("branching_factor must be >= 1")
    if depth < 0:
        raise ValueError("depth must be >= 0")


def _positive_or_default(value: Optional[int], default: int, name: str) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1")
    return parsed


def _scripted_product_specs(
    *,
    mode: str,
    choice_order: Iterable[int],
    depth: int,
    limit: Optional[int],
) -> Tuple[ScheduleRunSpec, ...]:
    count_limit = None if limit is None else _positive_or_default(limit, 1, "runs")
    product_iter = itertools.product(tuple(choice_order), repeat=depth)
    if count_limit is not None:
        product_iter = itertools.islice(product_iter, count_limit)

    specs = []
    for index, choices in enumerate(product_iter, start=1):
        specs.append(
            ScheduleRunSpec(
                run_index=index,
                schedule_strategy="scripted",
                schedule_choices=tuple(int(choice) for choice in choices),
                label=f"{mode}-{index}",
            )
        )
    return tuple(specs)


def _priority_choice_order(
    priority_choices: Optional[Sequence[int]],
    *,
    branching_factor: int,
) -> Tuple[int, ...]:
    if priority_choices is None:
        return tuple(range(branching_factor))

    order = tuple(int(choice) for choice in priority_choices)
    expected = set(range(branching_factor))
    actual = set(order)
    if len(order) != branching_factor or actual != expected:
        raise ValueError(
            "priority_choices must contain each choice index from "
            "0 to branching_factor - 1 exactly once"
        )
    return order
