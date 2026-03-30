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
- `tests/test_runtime.py`: regression coverage

## Before Opening A Change

1. Add or update regression coverage for behavior changes.
2. Run the core checks above.
3. Update the README or docs if the public behavior changed.
