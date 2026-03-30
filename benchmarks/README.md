# Benchmarks

This directory holds small, runnable checks that help you feel the runtime
cost of DRT in a real workflow.

## Benchmark

Run the current benchmark with:

```bash
python benchmarks/benchmark_drt.py
```

It measures a tiny counter workload in three modes:

1. Plain execution
2. DRT record
3. DRT replay

The point is not a magic headline number. The point is to keep a repeatable
sanity check around the cost of the supported record/replay path.

## Reading The Result

- `plain` is the baseline without runtime orchestration.
- `record` shows the overhead of logging and scheduling.
- `replay` shows the cost of consuming a recorded trace.

If the ratio starts drifting, that is a useful signal that something about the
runtime path changed.
