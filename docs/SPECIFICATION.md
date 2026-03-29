# DRT Technical Specification

## Formal Specification of the Deterministic Record-and-Replay Runtime

**Version:** 0.3.0  
**Status:** Draft  
**Last Updated:** March 29, 2026

---

## 1. Definitions

### 1.1 Terminology

| Term | Definition |
|------|------------|
| **Execution** | A single run of a program from start to termination |
| **Log** | Persistent record of an execution's nondeterministic decisions |
| **Record Mode** | Mode where execution is captured to a log |
| **Replay Mode** | Mode where execution follows a log |
| **Logical Time** | Monotonically increasing counter of scheduling decisions |
| **Thread ID** | Unique integer identifier for a managed thread |
| **Yield Point** | Location where a thread may relinquish control |
| **Runnable** | State where a thread is ready to execute |
| **Divergence** | Condition where replay differs from recording |

### 1.2 Notation

- `T` — Set of all managed threads
- `t_i` — Thread with ID `i`
- `τ` — Logical time counter
- `L` — Event log (sequence of entries)
- `L[k]` — Entry at index `k`
- `σ(t)` — State of thread `t`

---

## 2. System Model

### 2.1 Thread States

A thread `t` is in exactly one state at any time:

```
σ(t) ∈ { CREATED, RUNNABLE, RUNNING, BLOCKED_MUTEX, 
         BLOCKED_COND, BLOCKED_JOIN, EXITED }
```

**State Transitions:**

```
CREATED ─────start()────→ RUNNABLE
RUNNABLE ───scheduled───→ RUNNING
RUNNING ────yield───────→ RUNNABLE
RUNNING ────mutex_wait──→ BLOCKED_MUTEX
RUNNING ────cond_wait───→ BLOCKED_COND
RUNNING ────join_wait───→ BLOCKED_JOIN
BLOCKED_* ──unblock─────→ RUNNABLE
RUNNING ────exit────────→ EXITED
```

### 2.2 Runnable Predicate

A thread `t` is runnable iff:

```
runnable(t) ≡ σ(t) ∈ {RUNNABLE, RUNNING} ∧
              ¬blocked_on_mutex(t) ∧
              ¬blocked_on_cond(t) ∧
              ¬blocked_on_join(t)
```

### 2.3 Scheduler Invariants

At any logical time `τ`:

1. **Single Runner:** At most one thread has permission to run
   ```
   |{t ∈ T : permission(t) = true}| ≤ 1
   ```

2. **No Orphan Permission:** Permission implies runnable
   ```
   ∀t ∈ T: permission(t) ⟹ runnable(t)
   ```

3. **Progress:** If any thread is runnable, one has permission
   ```
   ∃t ∈ T: runnable(t) ⟹ ∃t' ∈ T: permission(t')
   ```

---

## 3. Yield Points

### 3.1 Exhaustive List

Threads yield control only at these points:

| Yield Point | Function | Condition |
|-------------|----------|-----------|
| MUTEX_LOCK | `DRTMutex.acquire()` | Always |
| MUTEX_UNLOCK | `DRTMutex.release()` | Always |
| COND_WAIT | `DRTCondition.wait()` | Always |
| COND_SIGNAL | `DRTCondition.notify()` | Always |
| EXPLICIT | `runtime_yield()` | Always |
| THREAD_EXIT | End of thread function | Always |

### 3.2 Atomicity Guarantee

Between consecutive yield points, execution is atomic:

```
∀t ∈ T, ∀τ₁ < τ₂:
  yield_point(t, τ₁) ∧ yield_point(t, τ₂) ∧ 
  (∀τ: τ₁ < τ < τ₂ ⟹ ¬yield_point(t, τ))
  ⟹ no_other_thread_runs(t, τ₁, τ₂)
```

---

## 4. Event Log Specification

### 4.1 Log Structure

```
Log := Magic || Entry* || CompleteMarker
Magic := "DRTLOG01" (8 bytes, ASCII)
Entry := Header || Payload
CompleteMarker := Entry with type = LOG_COMPLETE
```

### 4.2 Entry Header Format

```
Header := logical_time || thread_id || event_type || payload_len
          (uint64_le)    (uint32_le)  (int16_le)   (uint16_le)
```

Total: 16 bytes, little-endian.

### 4.3 Event Types

| Code | Name | Payload |
|------|------|---------|
| 1 | SCHEDULE | ∅ |
| 10 | LOCK_ACQUIRE | mutex_id: uint32 |
| 11 | LOCK_RELEASE | mutex_id: uint32 |
| 20 | COND_WAIT | cond_id: uint32 |
| 21 | COND_WAKE | target_thread: uint32, cond_id: uint32 |
| 30 | TIME_READ | value: float64 |
| 31 | RANDOM_READ | value: float64 |
| 32 | RANDOM_SEED | seed: float64 |
| 40 | IO_READ | data: bytes |
| 50 | THREAD_CREATE | new_thread_id: uint32 |
| 51 | THREAD_EXIT | ∅ |
| 52 | THREAD_JOIN | ∅ |
| 100 | LOG_COMPLETE | ∅ |

### 4.4 Log Validity

A log `L` is valid iff:

1. **Magic Check:** `L[0:8] = "DRTLOG01"`
2. **Parseable:** All entries parse correctly
3. **Complete:** Last entry has type `LOG_COMPLETE`
4. **Monotonic:** Logical times are non-decreasing
   ```
   ∀i < j: L[i].logical_time ≤ L[j].logical_time
   ```

---

## 5. Record Mode Specification

### 5.1 Scheduling Algorithm

```
procedure RECORD_SCHEDULE():
    R ← {t ∈ T : runnable(t)}
    if R = ∅ then
        return  // All threads blocked or exited
    
    // Deterministic selection (round-robin)
    t_next ← SELECT_DETERMINISTIC(R)
    
    // Log the decision
    APPEND(L, Entry(τ, t_next, SCHEDULE, ∅))
    FLUSH(L)
    
    // Update state
    τ ← τ + 1
    GRANT_PERMISSION(t_next)
```

### 5.2 Nondeterminism Interception

For each intercepted function `f`:

```
procedure INTERCEPT_f():
    v ← CALL_REAL_f()
    APPEND(L, Entry(τ, current_thread, f_EVENT_TYPE, SERIALIZE(v)))
    return v
```

### 5.3 Synchronization Logging

**Mutex Acquire:**
```
procedure MUTEX_ACQUIRE(m):
    APPEND(L, Entry(τ, current_thread, LOCK_ACQUIRE, m.id))
    if m.owner ≠ ∅ then
        SET_BLOCKED_MUTEX(current_thread, m)
        YIELD()
    m.owner ← current_thread
```

**Mutex Release:**
```
procedure MUTEX_RELEASE(m):
    APPEND(L, Entry(τ, current_thread, LOCK_RELEASE, m.id))
    m.owner ← ∅
    if m.waiters ≠ ∅ then
        t ← POP(m.waiters)
        UNBLOCK(t)
```

---

## 6. Replay Mode Specification

### 6.1 Log Loading

```
procedure LOAD_LOG(path):
    data ← READ_FILE(path)
    VERIFY_MAGIC(data[0:8])
    L ← PARSE_ENTRIES(data[8:])
    VERIFY_COMPLETE(L)
    return L
```

### 6.2 Scheduling Algorithm

```
procedure REPLAY_SCHEDULE():
    if replay_index ≥ |L| then
        SHUTDOWN()
        return
    
    e ← L[replay_index]
    if e.type ≠ SCHEDULE then
        ADVANCE_TO_NEXT_SCHEDULE()
        return
    
    t_expected ← e.thread_id
    R ← {t ∈ T : runnable(t)}
    
    if t_expected ∉ R then
        raise DivergenceError(τ, t_expected, R)
    
    replay_index ← replay_index + 1
    τ ← τ + 1
    GRANT_PERMISSION(t_expected)
```

### 6.3 Value Replay

```
procedure REPLAY_f():
    e ← FIND_NEXT_ENTRY(f_EVENT_TYPE)
    if e = ∅ then
        raise DivergenceError("Expected " + f_EVENT_TYPE)
    return DESERIALIZE(e.payload)
```

---

## 7. Divergence Specification

### 7.1 Divergence Conditions

Divergence occurs iff any of:

1. **Schedule Divergence:**
   ```
   L[replay_index].thread_id ∉ {t ∈ T : runnable(t)}
   ```

2. **Thread Divergence:**
   ```
   THREAD_CREATE expected but thread not created, or vice versa
   ```

3. **Value Divergence:**
   ```
   Intercepted value differs from logged value
   ```

4. **Termination Divergence:**
   ```
   Program terminates at different logical time
   ```

### 7.2 Divergence Handling

On divergence:

1. Record logical time `τ`
2. Record expected value/state
3. Record actual value/state
4. Raise `DivergenceError`
5. Abort replay immediately

No recovery. No best-effort continuation.

---

## 8. Correctness Properties

### 8.1 Determinism Theorem

**Theorem:** Given valid log `L` and initial state `S₀`, replay produces identical observable behavior.

**Formally:**
```
∀L valid, ∀S₀:
  record(S₀) = L ⟹ replay(L, S₀) ≡ record(S₀)
```

Where `≡` denotes equivalence of:
- Thread schedule (order of execution)
- Synchronization order
- Nondeterministic values
- Program outputs
- Termination state

### 8.2 Ordering Invariant

**Invariant:** Synchronization operations occur in log order.

```
∀sync_op₁, sync_op₂:
  log_index(sync_op₁) < log_index(sync_op₂) ⟹
  happens_before(sync_op₁, sync_op₂)
```

### 8.3 Isolation Invariant

**Invariant:** No nondeterministic input bypasses the runtime.

```
∀nondeterministic_call:
  nondeterministic_call ∈ intercepted_functions ∧
  value_logged(nondeterministic_call)
```

---

## 9. Implementation Requirements

### 9.1 Mandatory Components

| Component | Requirement |
|-----------|-------------|
| Runtime Controller | Must manage component lifecycle |
| Scheduler | Must implement specified algorithm |
| Thread Abstraction | Must use permission-based blocking |
| Sync Primitives | Must log all operations |
| Interceptors | Must capture all specified sources |
| Event Log | Must use specified binary format |
| Replay Engine | Must detect all divergence types |

### 9.2 Thread Safety

- Log append: Thread-safe with lock
- Scheduler state: Protected by scheduler lock
- Thread state: Protected by scheduler lock

### 9.3 Durability

- Each log entry: Flushed with `fsync()`
- On crash: Incomplete log (no `LOG_COMPLETE`)

---

## 10. Conformance

An implementation conforms to this specification iff:

1. All mandatory components are implemented
2. Log format version and structure match this specification
3. Scheduling algorithms match
4. Divergence detection is immediate
5. No nondeterminism escapes interception
6. Replay of a valid log either stays consistent with the recorded DRT-managed
   execution or raises divergence

### 10.1 Conformance Testing

Minimum test cases:

1. Single thread, no synchronization
2. Two threads, mutex contention
3. Producer-consumer with condition variable
4. Nondeterministic time/random values
5. Divergence detection
6. Incomplete log rejection
7. Multiple replay of same log

---

## Appendix A: Binary Format Reference

### A.1 Type Encodings

| Type | Encoding | Size |
|------|----------|------|
| uint64_le | Unsigned 64-bit little-endian | 8 |
| uint32_le | Unsigned 32-bit little-endian | 4 |
| uint16_le | Unsigned 16-bit little-endian | 2 |
| int16_le | Signed 16-bit little-endian | 2 |
| float64_le | IEEE 754 double little-endian | 8 |
| bytes | Raw bytes, length in header | variable |

### A.2 Example Log Dump

```
Offset  Content
------  -------
0x0000  44 52 54 4C 4F 47 30 31  "DRTLOG01"
0x0008  00 00 00 00 00 00 00 00  logical_time = 0
0x0010  00 00 00 00              thread_id = 0
0x0014  32 00                    event_type = 50 (THREAD_CREATE)
0x0016  04 00                    payload_len = 4
0x0018  01 00 00 00              new_thread_id = 1
0x001C  00 00 00 00 00 00 00 00  logical_time = 0
0x0024  01 00 00 00              thread_id = 1
0x0028  01 00                    event_type = 1 (SCHEDULE)
0x002A  00 00                    payload_len = 0
...
```

---

## Appendix B: Reference Implementation

The reference implementation is provided in the `drt/` package:

```
drt/
├── __init__.py      # Public API exports
├── events.py        # Event types and serialization
├── exceptions.py    # Exception definitions
├── intercept.py     # Nondeterminism interceptors
├── log.py           # Event log implementation
├── runtime.py       # Runtime controller
├── scheduler.py     # Scheduler implementation
├── sync.py          # Synchronization primitives
└── thread.py        # Thread abstraction
```

All components implement this specification.
