# DRT Design Document

This document explains the key design decisions, tradeoffs, and the reasoning behind them.

---

## Problem Statement

Concurrency bugs are hard to reproduce. A race condition might occur once in 1000 runs, and adding print statements or a debugger changes timing enough to make the bug disappear. This is called a "Heisenbug."

**Goal**: Build a system where if a bug happens once, you can reproduce it forever.

---

## Core Design Decision: Explicit API vs. Transparent Interception

### The Choice

I chose **explicit API** (users must use `DRTThread`, `DRTMutex`, etc.) over **transparent interception** (monkey-patching `threading.Thread`, `time.time()`, etc.).

### Why Not Transparent?

Transparent interception seems better - users don't change their code. But:

1. **It's fragile.** Python's import system means monkey-patching must happen before any imports. Miss one path and you have untracked nondeterminism. The system silently becomes incorrect.

2. **It's incomplete.** You'd need to intercept:
   - `threading.*`
   - `time.*`
   - `random.*`
   - `os.urandom()`
   - `socket.*` (network timing)
   - `subprocess.*`
   - File modification times
   - Signal handlers
   - And more...

   Each one is a potential hole. One missed source of nondeterminism and your replay diverges.

3. **It lies to users.** If it looks like normal Python but has hidden constraints, users will hit confusing failures.

### Why Explicit Works

With explicit API:
- **Boundaries are clear.** You know exactly what's tracked.
- **Failures are obvious.** Use `threading.Thread`? It won't be scheduled. You'll notice.
- **The contract is honest.** "Use these primitives and we guarantee determinism."

**Tradeoff accepted**: More upfront work for users, but no silent failures.

---

## Scheduler Design: Cooperative vs. Preemptive

### The Choice

I chose **cooperative scheduling with yield points** over preemptive scheduling.

### How It Works

Threads can only be switched at specific points:
- Lock acquire/release
- Condition wait/signal
- Explicit `runtime_yield()`
- Thread start/exit

Between yield points, a thread runs atomically.

### Why Not Preemptive?

Preemptive scheduling (interrupt threads anywhere) would need:
- Bytecode instrumentation, or
- OS-level signal handling (SIGALRM), or
- Modifying the Python interpreter

All of these are:
- Platform-specific
- Fragile across Python versions
- Complex to implement correctly

### Why Cooperative Works

Most concurrency bugs occur at synchronization points anyway. If you have a race condition, it's because two threads are accessing shared state - which means locks are involved (or should be).

**Tradeoff accepted**: Can't catch bugs in pure compute loops without explicit yields. But those are rare in practice, and users can add `runtime_yield()` if needed.

---

## Log Format: Binary vs. Text

### The Choice

Binary format with:
```
[8 bytes] Magic: "DRTLOG01"
Per entry:
  [8 bytes] Logical time (uint64)
  [4 bytes] Thread ID (uint32)
  [2 bytes] Event type (int16)
  [2 bytes] Payload length (uint16)
  [N bytes] Payload
```

### Why Not Text/JSON?

1. **Size.** A busy program generates thousands of events. JSON bloats quickly.

2. **Parsing speed.** Binary is O(1) to parse each entry. JSON requires scanning.

3. **Atomicity.** Fixed-size headers mean you can detect truncation precisely. With JSON, a truncated `}` could look like corruption.

4. **No ambiguity.** Binary has no encoding issues, no escaping, no quote handling.

### Why This Specific Format?

- **Magic bytes**: Detect "this isn't a DRT log" immediately.
- **Fixed header, variable payload**: Fast seeking + flexibility.
- **Little-endian**: Matches x86/ARM. No conversion on common platforms.
- **LOG_COMPLETE marker**: Know if recording finished cleanly.

**Tradeoff accepted**: Not human-readable. Mitigated by `python -m drt dump` for debugging.

---

## Thread ID Assignment: Sequential vs. Deterministic Hash

### The Choice

Sequential assignment: first spawned thread is 1, second is 2, etc.

### Why?

Determinism requires thread IDs to be the same on replay. Options:

1. **Sequential**: Simple, deterministic if spawn order is deterministic.
2. **Hash of call stack**: Complex, fragile if code changes.
3. **User-provided IDs**: Burden on users.

Sequential works because spawn order is already controlled by the scheduler.

**Tradeoff accepted**: Thread IDs are only meaningful within one record/replay pair. Fine for this use case.

---

## Handling Nondeterminism: Record-All vs. Record-On-Demand

### The Choice

**Record-all**: Every `drt_time()`, `drt_random()` call logs the value, even if it might be derivable.

### Why Not Smarter?

You could try:
- Seed the RNG once and derive all random values
- Record only "external" time and compute relative times

But:

1. **Complexity.** Tracking what's derivable requires analysis. Bugs in that analysis = silent replay failures.

2. **Floating point.** `time.time()` has platform-specific precision. Recording exact values avoids drift.

3. **Log size is fine.** A float is 8 bytes. Even 10,000 time calls = 80KB. Not worth optimizing.

**Tradeoff accepted**: Slightly larger logs for bulletproof correctness.

---

## Error Handling: Fail-Fast vs. Best-Effort

### The Choice

**Fail-fast.** If replay diverges, throw `DivergenceError` immediately. Don't try to recover.

### Why?

A divergence means the replay is no longer matching the recording. Continuing would:
- Execute code paths that didn't happen in recording
- Potentially corrupt state
- Give users false confidence

If divergence happens, something is fundamentally wrong:
- Log corruption
- Code changed between record and replay
- Untracked nondeterminism leaked in

All of these require human investigation, not automatic recovery.

**Tradeoff accepted**: Replay is all-or-nothing. No partial results. This is the right call for a debugging tool.

---

## What I Didn't Build (And Why)

### No GUI Debugger

A visual debugger showing thread states would be nice. Didn't build it because:
- Significant engineering effort
- Core value is the determinism guarantee, not visualization
- Users can dump logs and use existing tools

### No Distributed Support

Multi-process or multi-machine replay would require:
- Synchronized clocks or logical timestamps across nodes
- Network message recording
- Consensus on global schedule

This is a research problem (see: Friday, FoundationDB). Out of scope.

### No Automatic Instrumentation

Tools like `rr` work by trapping syscalls at the kernel level. That requires:
- OS-specific code
- Elevated privileges sometimes
- Deep platform knowledge

I chose userspace-only for portability and simplicity.

---

## Lessons Learned

1. **Nondeterminism hides everywhere.** Dictionary iteration order, hash randomization, thread spawn timing, file modification times. You think you've got it all, then another one bites you.

2. **The scheduler is the heart.** Get scheduling wrong and nothing works. I rewrote it three times.

3. **Test the invariant, not the implementation.** The best tests record something, replay it, and check the output matches. They don't care how the scheduler works internally.

4. **Binary formats need versioning.** I added magic bytes late. Should have done it from the start.

---

## Summary of Tradeoffs

| Decision | Chose | Over | Why |
|----------|-------|------|-----|
| API style | Explicit | Transparent | No silent failures |
| Scheduling | Cooperative | Preemptive | Simplicity, portability |
| Log format | Binary | Text | Size, speed, atomicity |
| Thread IDs | Sequential | Hashed | Simplicity |
| Nondeterminism | Record-all | Record-smart | Correctness over size |
| Errors | Fail-fast | Best-effort | Debugging tool, not production |

Every tradeoff points the same direction: **simple, correct, honest about limitations.**

That's the design.
