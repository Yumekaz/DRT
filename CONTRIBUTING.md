# Contributing

Thanks for taking the project seriously enough to improve it.

## Development Setup

```bash
python -m pip install -e .[dev]
```

## Core Checks

```bash
python tests/test_runtime.py
python -m unittest discover -v
python -m build
python -m drt verify path/to/logfile.log
python -m drt check module:function --runs 10
python -m drt replay path/to/failure-bundle
```

## Project Ground Rules

- Keep the scope honest. DRT is for code that stays inside the DRT-managed API surface.
- Prefer correctness and explicit divergence over silent fallback behavior.
- When docs make a claim about replay, tests should back it up.
- Cross-platform paths and output matter. Avoid Unix-only shortcuts in examples and demos.

## Where To Work First

- `drt/runtime.py`: runtime lifecycle and CLI
- `drt/scheduler.py`: replay validation and scheduling behavior
- `drt/log.py`: binary format, validation, and integrity checks
- `drt/intercept.py`: nondeterministic input capture
- `drt/checker.py`: repeated schedule checks and failure bundle creation
- `drt/explorer.py`: random, exhaustive, priority, and stress schedule plans
- `drt/replay.py`: failure bundle replay and source drift checks
- `drt/trace.py`: trace timelines, explanations, and HTML reports
- `drt/minimize.py`: schedule-choice minimization
- `drt/pytest_plugin.py`: pytest integration
- `drt/async_runtime.py`: opt-in deterministic async task runtime
- `tests/test_runtime.py`: regression coverage

## Before Opening A Change

1. Add or update regression coverage for behavior changes.
2. Run the core checks above.
3. Update the README or docs if the public behavior changed.
