# Case Study: Capturing A Lost-Update Bug

This case study walks through the same kind of bug shown in the lost-update
demo, but from the perspective of a debugging session instead of a quick tour.
The point is to show what DRT buys you in practice: one bad run, one recorded
trace, and one replayable failure you can reason about.

## The Bug

A shared counter is incremented by multiple threads without synchronization.
Each worker does a read-modify-write sequence:

1. Read the current value.
2. Yield.
3. Write back `value + 1`.

That is enough to lose updates whenever two threads read the same value before
either write lands. The symptom is simple: the final count is lower than the
expected count.

## What Made It Hard

This class of bug is annoying because the broken execution is fragile. A print
statement, a breakpoint, or a slight timing change can make the failure vanish.
That means the usual "run it again and watch" workflow often loses the evidence
you needed.

## How DRT Helped

The repo's lost-update demo captures the failure with a DRT-managed workload
and then replays that same trace. That gives you a concrete sequence to study
instead of a hand-wavy "it usually fails somewhere around here."

The flow is:

1. Run the workload once under `record`.
2. Keep the log for inspection.
3. Re-run the same workload under `replay`.
4. Confirm the replay stays on the recorded path.

If replay diverges, DRT stops instead of pretending the run is still valid.

## The Fix

The real fix is boring, which is usually a good sign:

1. Protect the shared counter with a mutex.
2. Remove the accidental race window.
3. Keep the DRT demo around as a regression check.

That is the part people should notice. DRT does not just "show a bug." It
helps you keep the bug pinned long enough to fix it and prove the fix.

## Why This Matters

The value here is not only that DRT can reproduce a race once. The deeper value
is that it turns a disappearing concurrency failure into a stable artifact you
can document, test, and hand to someone else.

That makes it much easier to:

1. Explain the failure to a teammate.
2. Verify the fix.
3. Preserve the regression as a testable story.

For a quick runnable version of the same idea, see
[tests/test_race_condition.py](../tests/test_race_condition.py) and
[demo/run_demo.py](../demo/run_demo.py).
