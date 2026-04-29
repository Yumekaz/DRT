# Case Study: Finding And Preserving A Real Oversell Race

This is the smallest serious evidence path for the current DRT direction:
`drt check` finds a schedule-dependent concurrency bug, writes a failure bundle,
then the bundle can be replayed, minimized, and inspected.

The target is intentionally narrow. It uses DRT-managed threads and explicit
yield points. It is not a claim that DRT can magically control arbitrary
`threading.Thread` code.

## The Bug

The demo models a last-item inventory reservation:

1. Stock starts at one unit.
2. Two checkout workers try to reserve that unit.
3. The buggy code reads stock, yields, then writes the reservation from the
   stale read.
4. Under the bad interleaving, both workers reserve the same last item.

The invariant is simple: `len(reservations) + final_stock` must equal the
initial stock. The bug violates that invariant with:

```text
oversold GPU-42: initial=1 final_stock=0 reservations=('order-1001', 'order-1002')
```

Targets:

```text
demo.case_study_inventory_oversell:check_last_item_oversell
demo.case_study_inventory_oversell:check_last_item_fixed
```

## Direct Smoke Script

Run the direct script first if you only want to see the case study target and
the fixed control in one quick pass:

```powershell
python demo\case_study_inventory_oversell.py
```

Expected result: the buggy target is captured under the built-in scripted
schedule, and the fixed target passes the same schedule.

## Find The Bug With `drt check`

This command intentionally exits with status 1 when it succeeds at finding the
bug. The non-zero exit is the check runner reporting a discovered failure, not a
broken CLI invocation.

```powershell
python -m drt check demo.case_study_inventory_oversell:check_last_item_oversell --strategy exhaustive --depth 6 --branching 2 --runs 64 --bundle-dir demo\.case-study\failures
```

Expected evidence markers:

```text
Checked: demo.case_study_inventory_oversell:check_last_item_oversell
Failure: AssertionError: oversold GPU-42: initial=1 final_stock=0 reservations=('order-1001', 'order-1002')
Status: failed
```

Capture the newest bundle path:

```powershell
$bundle = Get-ChildItem demo\.case-study\failures -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$bundle.FullName
```

The bundle contains:

```text
trace.log
metadata.json
failure.txt
source_hashes.json
schedule_choices.json
```

## Replay The Bundle

Replay uses the stored schedule choices and checks source drift against the
hashes captured in the bundle.

```powershell
python -m drt replay $bundle.FullName
```

Expected evidence markers:

```text
Expected: AssertionError: oversold GPU-42: initial=1 final_stock=0 reservations=('order-1001', 'order-1002')
Actual: AssertionError: oversold GPU-42: initial=1 final_stock=0 reservations=('order-1001', 'order-1002')
Reproduced: True
Source changed: False
```

## Minimize The Schedule

The minimizer tries to remove unnecessary schedule choices while preserving the
same exception type and message. The important result is `Reproduced: True`.

```powershell
python -m drt minimize $bundle.FullName demo.case_study_inventory_oversell:check_last_item_oversell --output demo\.case-study\minimized
```

Expected evidence markers:

```text
Original schedule choices: ...
Minimized schedule choices: ...
Reproduced: True
```

Replay the minimized bundle:

```powershell
python -m drt replay demo\.case-study\minimized
```

## Inspect The Trace

Timeline and explanation:

```powershell
python -m drt timeline demo\.case-study\minimized
python -m drt explain demo\.case-study\minimized
```

Standalone HTML report:

```powershell
python -m drt report demo\.case-study\minimized --output demo\.case-study\inventory-oversell-report.html
```

Expected evidence markers:

```text
Integrity: verified
THREAD_CREATE
SCHEDULE
THREAD_EXIT
```

## Prove The Fix Survives The Same Search

The fixed target protects the stock ledger with `DRTMutex` and deliberately
keeps a yield inside the critical section. Passing here means the fix survives
the same bounded schedule search, not merely that the yield disappeared.

```powershell
python -m drt check demo.case_study_inventory_oversell:check_last_item_fixed --strategy exhaustive --depth 6 --branching 2 --runs 64 --bundle-dir demo\.case-study\fixed-check
```

Expected result:

```text
Runs: 64/64
Status: ok
```

## What This Proves

- `drt check` can find a real invariant violation in DRT-managed concurrent
  code.
- The failure bundle is portable enough to inspect as plain files.
- `drt replay` can reproduce the same failure and report source drift.
- `drt minimize` can shrink the stored schedule while preserving the failure.
- The fixed control gives a bounded regression check against the same schedule
  exploration surface.

## What It Does Not Prove

- It does not cover uninstrumented `threading.Thread` programs.
- It does not prove every possible schedule for every Python concurrency model.
- It does not turn DRT into a production debugger yet. It is focused evidence
  for the current deterministic concurrency testing direction.
