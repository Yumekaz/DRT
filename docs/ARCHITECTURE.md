# DRT Architecture Document

## Deterministic Record-and-Replay Runtime for Python

**Version:** 0.4.0  
**Status:** Experimental Prototype  
**Last Updated:** March 29, 2026

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Solution Overview](#3-solution-overview)
4. [System Architecture](#4-system-architecture)
5. [Component Design](#5-component-design)
6. [Data Flow](#6-data-flow)
7. [Threading Model](#7-threading-model)
8. [Scheduling Algorithm](#8-scheduling-algorithm)
9. [Event Log Specification](#9-event-log-specification)
10. [Replay Mechanics](#10-replay-mechanics)
11. [Divergence Detection](#11-divergence-detection)
12. [Security Considerations](#12-security-considerations)
13. [Performance Characteristics](#13-performance-characteristics)
14. [Design Decisions](#14-design-decisions)

---

## 1. Executive Summary

DRT (Deterministic Record-and-Replay Runtime) is a user-space runtime system for reproducing concurrent executions in Python code that stays inside the DRT-managed API surface. It addresses the debugging problem of nondeterministic concurrency by recording scheduler decisions and supported nondeterministic inputs, then replaying that recorded execution while checking for divergence.

### Core Capability

```
Record → Capture a DRT-managed execution trace (thread schedule, random values, timestamps)
Replay → Re-run the same supported trace or fail with DivergenceError
```

### Key Guarantee

> Given the same initial state, the same program code, and a complete execution log, replay is intended to reproduce the same behavior within DRT's supported API surface. If the replayed execution drifts, DRT should raise `DivergenceError` instead of silently continuing.

---

## 2. Problem Statement

### 2.1 The Challenge of Concurrency Bugs

Concurrency bugs exhibit **nondeterministic** behavior:

- They depend on thread interleaving, which varies between runs
- They often disappear when debugging (Heisenbugs)
- Adding logging changes timing and masks the bug
- They may occur once in thousands of executions

### 2.2 Why Traditional Debugging Fails

| Approach | Problem |
|----------|---------|
| Print debugging | Changes timing, hides bug |
| Breakpoints | Pauses threads, changes interleaving |
| Core dumps | Capture state, not history |
| Stress testing | Finds bugs, can't reproduce them |

### 2.3 The Solution: Deterministic Replay

If we can **record** every nondeterministic decision during execution, we can **replay** the exact same execution—including the bug—every time.

---

## 3. Solution Overview

### 3.1 Approach

DRT intercepts and records all sources of nondeterminism:

1. **Thread scheduling decisions** — which thread runs when
2. **Synchronization operations** — mutex acquire order, condition signals
3. **External inputs** — time, random numbers, file contents

During replay, DRT enforces the recorded decisions instead of making new ones.

### 3.2 Design Philosophy

| Principle | Implementation |
|-----------|----------------|
| **Explicit over implicit** | Custom API instead of monkey-patching |
| **Correctness over performance** | Synchronous logging, no optimizations |
| **Fail-fast** | Immediate divergence detection |
| **Minimal scope** | Single process, user-space only |

### 3.3 Scope Boundaries

**In Scope:**
- Single-process Python programs
- Multiple threads (threading module)
- User-space implementation
- Deterministic scheduling
- Record and replay modes

**Out of Scope:**
- Distributed systems
- Multiple processes
- Kernel-level recording
- Signal handling
- Performance optimization

---

## 4. System Architecture

### 4.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Program                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │  DRTThread   │  │  DRTMutex    │  │  drt_time()  │           │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │
└─────────┼─────────────────┼─────────────────┼───────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DRT Runtime Layer                           │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   Runtime Controller                     │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │    │
│  │  │  Scheduler  │  │ Interceptor │  │   Event Log     │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘  │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Python Runtime / OS                           │
│         threading.Event    │    time.time()    │    File I/O    │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Component Overview

| Component | File | Responsibility |
|-----------|------|----------------|
| Runtime Controller | `runtime.py` | Lifecycle management, coordination |
| Scheduler | `scheduler.py` | Thread scheduling, state tracking |
| Managed Thread | `thread.py` | Thread abstraction with scheduler integration |
| Sync Primitives | `sync.py` | Mutex, Condition, Semaphore, Barrier |
| Interceptors | `intercept.py` | Capture nondeterministic values |
| Event Log | `log.py` | Persistent execution record |
| Events | `events.py` | Event types and serialization |
| Exceptions | `exceptions.py` | Error types for divergence, corruption |

---

## 5. Component Design

### 5.1 Runtime Controller (`runtime.py`)

The Runtime Controller is the entry point and orchestrator.

**Responsibilities:**
- Initialize all components in correct order
- Wire components together (dependency injection)
- Manage execution lifecycle (start → run → finalize)
- Handle exceptions and cleanup

**State Machine:**
```
CREATED → INITIALIZED → RUNNING → FINALIZED
                ↓
              FAILED
```

**Key Methods:**
```python
class DRTRuntime:
    def __init__(mode: str, log_path: str)  # 'record' or 'replay'
    def run(target: Callable) → Any         # Execute with DRT control
```

### 5.2 Scheduler (`scheduler.py`)

The Scheduler controls thread execution order.

**Responsibilities:**
- Track thread states (CREATED, RUNNABLE, BLOCKED, EXITED)
- Grant execution permission to one thread at a time
- Record scheduling decisions (RECORD mode)
- Follow recorded schedule (REPLAY mode)
- Detect divergence

**Thread States:**
```
CREATED ──start()──→ RUNNABLE ←──────────────┐
                         │                    │
                    scheduled                 │
                         ↓                    │
                     RUNNING                  │
                         │                    │
            ┌────────────┼────────────┐       │
            ↓            ↓            ↓       │
     BLOCKED_MUTEX  BLOCKED_COND  BLOCKED_JOIN
            │            │            │       │
            └────────────┴────────────┴───────┘
                         │
                    thread_exit
                         ↓
                      EXITED
```

**Scheduling Algorithm:**
```
RECORD mode:
    runnable = get_runnable_threads()
    chosen = select_deterministically(runnable)  # round-robin
    log(SCHEDULE, chosen)
    grant_permission(chosen)

REPLAY mode:
    expected = log.next_schedule_entry()
    if expected not in runnable:
        raise DivergenceError
    grant_permission(expected)
```

### 5.3 Managed Thread (`thread.py`)

DRTThread wraps native threads with scheduler integration.

**Responsibilities:**
- Register with scheduler on creation
- Block until scheduler grants permission
- Yield control at yield points
- Notify scheduler on exit

**Execution Loop:**
```python
def _thread_entry(self):
    scheduler.thread_started(thread_id)
    try:
        while not exited:
            scheduler.request_run(thread_id)  # Block until permitted
            if not scheduler.is_running:
                break
            result = target(*args, **kwargs)  # Execute user code
            break
    finally:
        scheduler.thread_exited(thread_id)
        scheduler.yield_control(thread_id)
```

### 5.4 Synchronization Primitives (`sync.py`)

Custom implementations that integrate with the scheduler.

**DRTMutex:**
- `acquire()`: Yield point, may block if held by another thread
- `release()`: Yield point, may wake blocked thread
- Logs: LOCK_ACQUIRE, LOCK_RELEASE

**DRTCondition:**
- `wait()`: Release mutex, block on condition, reacquire mutex
- `notify()`: Wake one waiter
- `notify_all()`: Wake all waiters
- Logs: COND_WAIT, COND_WAKE

**Additional Primitives:**
- `DRTSemaphore`: Counting semaphore built on Mutex + Condition
- `DRTBarrier`: Synchronization barrier for N threads

### 5.5 Nondeterminism Interceptors (`intercept.py`)

Capture and replay nondeterministic values.

**Intercepted Sources:**

| Source | RECORD Behavior | REPLAY Behavior |
|--------|-----------------|-----------------|
| `drt_time()` | Call `time.time()`, log result | Return logged value |
| `drt_random()` | Call `random.random()`, log result | Return logged value |
| `drt_sleep(n)` | Yield (logical sleep) | Yield |
| `drt_read_file(path)` | Read file, log contents | Return logged contents |

**Design Pattern:**
```python
def drt_time() -> float:
    if mode == RECORD:
        value = time.time()      # Call real function
        log(TIME_READ, value)    # Record it
        return value
    else:  # REPLAY
        return log.get_next(TIME_READ)  # Return recorded value
```

### 5.6 Event Log (`log.py`)

Persistent, append-only execution record.

**Properties:**
- Binary format for efficiency
- Append-only during recording
- Flushed and fsynced after each write
- LOG_COMPLETE marker plus entry-count / CRC32 payload for current-format integrity checks

**Operations:**
```python
class EventLog:
    def open_for_record()   # Create new log
    def open_for_replay()   # Load and validate existing log
    def append(entry)       # Add entry (record mode)
    def get_entry(index)    # Read entry (replay mode)
    def finalize()          # Write LOG_COMPLETE marker with integrity metadata
```

---

## 6. Data Flow

### 6.1 Record Mode Data Flow

```
User Code                    DRT Runtime                     Log File
    │                            │                               │
    │  DRTThread.start()         │                               │
    ├───────────────────────────→│                               │
    │                            │  log(THREAD_CREATE)           │
    │                            ├──────────────────────────────→│
    │                            │                               │
    │  mutex.acquire()           │                               │
    ├───────────────────────────→│                               │
    │                            │  log(LOCK_ACQUIRE)            │
    │                            ├──────────────────────────────→│
    │                            │                               │
    │  drt_time()                │                               │
    ├───────────────────────────→│  value = time.time()          │
    │                            │  log(TIME_READ, value)        │
    │                            ├──────────────────────────────→│
    │  ←─── value ───────────────│                               │
    │                            │                               │
```

### 6.2 Replay Mode Data Flow

```
User Code                    DRT Runtime                     Log File
    │                            │                               │
    │                            │  ←── load all entries ────────│
    │                            │                               │
    │  DRTThread.start()         │                               │
    ├───────────────────────────→│                               │
    │                            │  verify(THREAD_CREATE)        │
    │                            │                               │
    │  mutex.acquire()           │                               │
    ├───────────────────────────→│                               │
    │                            │  verify(LOCK_ACQUIRE)         │
    │                            │                               │
    │  drt_time()                │                               │
    ├───────────────────────────→│  value = log.get(TIME_READ)   │
    │  ←─── value ───────────────│  (no real time.time() call)   │
    │                            │                               │
```

---

## 7. Threading Model

### 7.1 Permission-Based Execution

Each thread has a `run_permission` event (threading.Event):

```python
@dataclass
class ManagedThread:
    thread_id: int
    state: ThreadState
    run_permission: threading.Event  # Controls execution
    native_thread: threading.Thread
```

**Invariant:** At most one thread has `run_permission.is_set() == True` at any time.

### 7.2 Yield Points

Threads can only yield control at specific points:

| Yield Point | Trigger |
|-------------|---------|
| Mutex lock | `DRTMutex.acquire()` |
| Mutex unlock | `DRTMutex.release()` |
| Condition wait | `DRTCondition.wait()` |
| Condition signal | `DRTCondition.notify()` |
| Explicit yield | `runtime_yield()` |
| Thread exit | End of thread function |

**Between yield points, execution is atomic** — no other thread can run.

### 7.3 Blocking Mechanism

```python
def request_run(thread_id):
    """Block until scheduler grants permission."""
    thread = threads[thread_id]
    thread.run_permission.wait()  # Block here
    # Permission granted, proceed

def yield_control(thread_id):
    """Release control back to scheduler."""
    thread = threads[thread_id]
    thread.run_permission.clear()  # Revoke own permission
    schedule_next()  # Let scheduler pick next thread
```

---

## 8. Scheduling Algorithm

### 8.1 Runnable Definition

A thread is **runnable** if and only if:
1. State is RUNNABLE or RUNNING
2. Not blocked on a mutex (`blocked_on_mutex is None`)
3. Not waiting on a condition (`blocked_on_cond is None`)
4. Not waiting for another thread (`waiting_for_thread is None`)

### 8.2 Record Mode: Deterministic Selection

```python
def schedule_next_record():
    runnable = get_runnable_threads()
    if not runnable:
        return  # All threads blocked or exited
    
    # Round-robin selection for varied interleavings
    sorted_runnable = sorted(runnable)
    if current_thread in sorted_runnable:
        idx = sorted_runnable.index(current_thread)
        chosen = sorted_runnable[(idx + 1) % len(sorted_runnable)]
    else:
        chosen = sorted_runnable[0]
    
    log(SCHEDULE, chosen)
    logical_time += 1
    threads[chosen].run_permission.set()
```

### 8.3 Replay Mode: Log-Driven Selection

```python
def schedule_next_replay():
    entry = log.next_schedule_entry()
    if entry is None:
        shutdown()  # Log exhausted
        return
    
    expected = entry.thread_id
    runnable = get_runnable_threads()
    
    if expected not in runnable:
        raise DivergenceError(
            f"Thread {expected} should be runnable",
            logical_time=logical_time
        )
    
    logical_time += 1
    threads[expected].run_permission.set()
```

---

## 9. Event Log Specification

### 9.1 File Format

```
┌─────────────────────────────────────────────┐
│  Magic: "DRTLOG02" (8 bytes)                │
├─────────────────────────────────────────────┤
│  Entry 0: [header][payload]                 │
├─────────────────────────────────────────────┤
│  Entry 1: [header][payload]                 │
├─────────────────────────────────────────────┤
│  ...                                        │
├─────────────────────────────────────────────┤
│  Entry N: LOG_COMPLETE [entry_count][crc32] │
└─────────────────────────────────────────────┘
```

### 9.2 Entry Header Format

| Field | Type | Size | Description |
|-------|------|------|-------------|
| logical_time | uint64 | 8 bytes | Monotonic counter |
| thread_id | uint32 | 4 bytes | Thread identifier |
| event_type | int16 | 2 bytes | Event type code |
| payload_len | uint16 | 2 bytes | Payload size |

**Total header size:** 16 bytes  
**Byte order:** Little-endian

### 9.3 Event Types

| Code | Event | Payload |
|------|-------|---------|
| 1 | SCHEDULE | (none) |
| 10 | LOCK_ACQUIRE | mutex_id: uint32 |
| 11 | LOCK_RELEASE | mutex_id: uint32 |
| 20 | COND_WAIT | cond_id: uint32 |
| 21 | COND_WAKE | target_thread: uint32, cond_id: uint32 |
| 30 | TIME_READ | value: float64 |
| 31 | RANDOM_READ | value: float64 |
| 32 | RANDOM_SEED | seed: float64 |
| 40 | IO_READ | data: bytes |
| 50 | THREAD_CREATE | new_thread_id: uint32 |
| 51 | THREAD_EXIT | (none) |
| 52 | THREAD_JOIN | target_thread_id: uint32, completed_immediately: uint8 |
| 100 | LOG_COMPLETE | entry_count: uint64, body_crc32: uint32 |

### 9.4 Integrity Guarantees

1. **Atomicity:** Each entry is written completely or not at all
2. **Durability:** `fsync()` after each write
3. **Completeness:** LOG_COMPLETE marks valid end
4. **Parseability:** No optional fields, fixed format

---

## 10. Replay Mechanics

### 10.1 Replay Initialization

```python
def open_for_replay():
    # 1. Load entire log into memory
    data = file.read()
    
    # 2. Verify magic
    assert data[:8] in (b'DRTLOG01', b'DRTLOG02')
    
    # 3. Parse all entries
    entries = parse_entries(data[8:])
    
    # 4. Verify completeness
    if entries[-1].event_type != LOG_COMPLETE:
        raise IncompleteLogError()
```

### 10.2 Value Replay

Interceptors return logged values instead of calling real functions:

```python
class NondeterminismInterceptor:
    def __init__(self, log):
        self.log = log
        self.replay_index = 0
    
    def time(self) -> float:
        if mode == REPLAY:
            entry = self.find_next_entry(TIME_READ)
            return deserialize_float(entry.payload)
        else:
            # Record mode: call real function
            ...
```

### 10.3 Schedule Replay

The scheduler follows the next recorded schedule entry:

```python
def _schedule_next_replay():
    entry = peek_next_schedule_entry()
    expected_thread = entry.thread_id
    
    # Verify thread is runnable
    if expected_thread not in get_runnable_threads():
        raise DivergenceError(...)
    
    # Grant permission to expected thread
    consume_schedule_entry()
    threads[expected_thread].run_permission.set()
```

---

## 11. Divergence Detection

### 11.1 Divergence Definition

Divergence occurs when replay execution differs from recording:

| Type | Detection Point |
|------|-----------------|
| Schedule divergence | Expected thread not runnable |
| Lock divergence | Lock blocks when it didn't during record |
| Value divergence | Nondeterministic value differs |
| Output divergence | Program produces different output |

### 11.2 DivergenceError

```python
class DivergenceError(DRTError):
    def __init__(self, message, logical_time, expected, actual):
        self.logical_time = logical_time  # When it happened
        self.expected = expected          # What log said
        self.actual = actual              # What happened
```

### 11.3 Detection Strategy

**Fail-fast:** Divergence is detected at the exact event boundary where it occurs.

**No recovery:** Replay is binary—identical or aborted. No "best-effort" continuation.

---

## 12. Security Considerations

### 12.1 Log File Security

- Log files contain execution data (timestamps, random values, file contents)
- Should be protected with appropriate file permissions
- Consider encryption for sensitive applications

### 12.2 Replay Isolation

- Replay does not execute real I/O operations
- File reads return logged data, not current file contents
- Time values are from recording, not current time

### 12.3 Trust Model

- Log file is trusted during replay
- Malicious log could cause arbitrary "replay" behavior
- Only replay logs from trusted sources

---

## 13. Performance Characteristics

### 13.1 Recording Overhead

| Operation | Overhead |
|-----------|----------|
| Thread switch | ~1ms (log write + fsync) |
| Mutex acquire/release | ~0.5ms each |
| drt_time() | ~0.1ms |
| drt_random() | ~0.1ms |

### 13.2 Replay Overhead

| Operation | Overhead |
|-----------|----------|
| Log loading | O(log size) |
| Thread switch | ~0.1ms |
| Value lookup | O(1) amortized |

### 13.3 Memory Usage

- Recording: O(events) for in-memory log copy
- Replay: O(log size) for full log in memory

### 13.4 Scalability Limits

| Factor | Limit | Reason |
|--------|-------|--------|
| Threads | ~100 | Scheduler overhead |
| Events | ~10M | Memory for log |
| Execution time | Hours | Log size |

---

## 14. Design Decisions

### 14.1 Why Explicit API (No Monkey-Patching)?

**Decision:** Require users to import DRT primitives explicitly.

**Rationale:**
1. **Clarity:** Nondeterminism boundaries are visible in code
2. **Reliability:** No interaction with CPython internals
3. **Debuggability:** Clear stack traces
4. **Completeness:** User knows exactly what's intercepted

**Trade-off:** More work for user, but stronger guarantees inside the
supported API surface.

### 14.2 Why Synchronous Logging?

**Decision:** Flush and fsync after every log write.

**Rationale:**
1. **Crash safety:** No lost events on crash
2. **Correctness:** Ordered, durable log
3. **Simplicity:** No buffering logic

**Trade-off:** Performance cost (~1ms per event).

### 14.3 Why No Preemption?

**Decision:** Threads only yield at explicit yield points.

**Rationale:**
1. **Determinism:** No timer-based interrupts
2. **Simplicity:** Fewer events to log
3. **Python GIL:** Already serializes execution

**Trade-off:** Long-running code between yield points blocks other threads.

### 14.4 Why Binary Log Format?

**Decision:** Use binary format instead of text/JSON.

**Rationale:**
1. **Size:** Smaller logs
2. **Speed:** Faster parsing
3. **Precision:** Exact float representation

**Trade-off:** Not human-readable (provide `dump` command).

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **Deterministic** | Same inputs → same outputs, always |
| **Divergence** | Replay differs from recording |
| **Logical time** | Monotonic counter of scheduling decisions |
| **Nondeterminism** | Behavior that varies between runs |
| **Runnable** | Thread ready to execute (not blocked) |
| **Yield point** | Location where thread may release control |

## Appendix B: File Structure

```
drt/
├── __init__.py      # Public API
├── events.py        # Event types, serialization
├── exceptions.py    # DivergenceError, etc.
├── intercept.py     # drt_time, drt_random, etc.
├── log.py           # EventLog class
├── runtime.py       # DRTRuntime controller
├── scheduler.py     # Scheduler, thread states
├── sync.py          # DRTMutex, DRTCondition
└── thread.py        # DRTThread
```

## Appendix C: References

1. R. O'Callahan et al., "Engineering Record and Replay for Deployability", USENIX ATC 2017
2. D. Devecsery et al., "Eidetic Systems", OSDI 2014
3. G. Altekar and I. Stoica, "ODR: Output-Deterministic Replay for Multicore Debugging", SOSP 2009
