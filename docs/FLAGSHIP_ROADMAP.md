# DRT Flagship Roadmap

DRT's strongest product direction is deterministic concurrency testing:
find a bad interleaving, preserve it, inspect it, and shrink it until the bug
is understandable.

This is not a claim that DRT transparently controls arbitrary Python programs.
The current model remains explicit and opt-in: code must use DRT-managed
threads, synchronization primitives, and nondeterminism APIs.

## Implemented MVP

### Schedule exploration

`DRTRuntime` supports record-mode schedule policies:

- `round_robin`: the original deterministic scheduler behavior
- `random`: seeded random choices among runnable threads
- `scripted`: choice indexes supplied by a tool such as the minimizer
- `priority`: lower configured thread priority values run first

Example:

```python
from drt import DRTRuntime

runtime = DRTRuntime(
    mode="record",
    log_path="run.log",
    schedule_strategy="random",
    schedule_seed=42,
)
runtime.run(my_drt_program)
```

### `drt check`

`drt check` loads a callable from `module:function` syntax and runs it under
repeated record-mode schedules.

```bash
drt check mymodule:target --runs 100 --strategy random --seed 1
drt check mymodule:target --strategy exhaustive --depth 4 --branching 2
drt check mymodule:target --strategy stress --runs 1000 --stress-max-runs 200
```

On failure it writes a failure bundle under `.drt/failures` by default.

### Failure bundles

A failure bundle is a plain directory:

```text
failure-.../
|-- trace.log
|-- metadata.json
|-- failure.txt
|-- source_hashes.json
`-- schedule_choices.json
```

The bundle records the failure, environment, DRT trace, source hashes, and
schedule choice indexes so later tooling can inspect or minimize it.

### Bundle replay and source drift

Bundles can be replayed against the current source tree:

```bash
drt replay .drt/failures/failure-...
```

DRT compares stored source hashes with files on disk and reports whether the
source changed before attempting to reproduce the failure.

### Trace inspection

DRT can inspect either a raw log or a failure bundle:

```bash
drt timeline .drt/failures/failure-...
drt explain .drt/failures/failure-...
drt report .drt/failures/failure-... --output trace.html
```

### Schedule minimization

`drt minimize` tries to remove schedule choices while preserving the original
failure class and message.

```bash
drt minimize .drt/failures/failure-... mymodule:target
```

This is an MVP delta-debugging pass, not a complete model checker.

### Pytest plugin

The pytest plugin can run normal test functions under DRT record-mode schedule
exploration:

```bash
pytest --drt --drt-runs 100 --drt-strategy random
pytest --drt --drt-strategy exhaustive --drt-depth 4 --drt-branching 2
```

When the package is installed, DRT also exposes a `pytest11` entry point.
Tests can opt in with `@drt_test(schedules=1000)` or
`@pytest.mark.drt(schedules=1000)`.
In a source tree without installation, load the plugin explicitly with
`pytest -p drt.pytest_plugin`.

The plugin uses the same schedule planner as `drt check`, so pytest opt-ins can
use `round_robin`, `random`, `exhaustive`, `priority`, and `stress` strategies.

### DRT-managed async tasks

`DRTAsyncRuntime` provides opt-in deterministic scheduling for coroutines that
yield through `drt_async_yield()` or `drt_async_sleep()`. This is useful for
testing DRT-managed async task interleavings, but it is not a transparent
replacement for the standard `asyncio` event loop.

## Next Serious Milestones

1. Make schedule exploration smarter: bounded exhaustive exploration,
   preemption-point budgeting, and schedule coverage metrics.
2. Make minimization stronger: shrink by semantic events, not only runnable
   choice indexes.
3. Improve replay semantics beyond exception type/message matching.
4. Add a local trace viewer that compares record, replay, and minimized traces.
5. Expand async support from DRT-managed coroutine tasks toward broader
   `asyncio` compatibility where the boundary can stay honest.

## Honest Boundaries

- DRT does not control normal `threading.Thread` code unless it is migrated to
  DRT primitives.
- DRT does not intercept sockets, subprocesses, signals, native extensions, or
  arbitrary filesystem behavior.
- Random schedule exploration can expose bugs, but it is not proof that no bad
  schedule exists.
- The minimizer and bundle replay preserve the same exception type/message.
  More advanced semantic failure matching is future work.
- Async support is opt-in for DRT-managed coroutines, not arbitrary `asyncio`
  networking, subprocess, or third-party event-loop behavior.
