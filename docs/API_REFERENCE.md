# DRT API Reference

## Complete API Documentation

**Version:** 1.0.0  
**Python:** 3.8+

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Core Runtime](#core-runtime)
3. [Threading](#threading)
4. [Synchronization Primitives](#synchronization-primitives)
5. [Nondeterminism Interceptors](#nondeterminism-interceptors)
6. [Exceptions](#exceptions)
7. [Advanced API](#advanced-api)
8. [Examples](#examples)

---

## Quick Start

### Installation

```bash
cd drt-project
pip install -e .
```

### Minimal Example

```python
from drt import DRTRuntime, DRTThread, DRTMutex

def my_program():
    mutex = DRTMutex()
    results = []
    
    def worker(n):
        with mutex:
            results.append(n)
    
    threads = [DRTThread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(results)

# Record
runtime = DRTRuntime(mode='record', log_path='exec.log')
runtime.run(my_program)

# Replay (identical output!)
runtime = DRTRuntime(mode='replay', log_path='exec.log')
runtime.run(my_program)
```

---

## Core Runtime

### class `DRTRuntime`

Main runtime controller for deterministic record and replay.

```python
from drt import DRTRuntime
```

#### Constructor

```python
DRTRuntime(mode: str, log_path: str)
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `mode` | `str` | Either `'record'` or `'replay'` |
| `log_path` | `str` | Path to the execution log file |

**Raises:**
- `ValueError` if mode is not 'record' or 'replay'

**Example:**
```python
# Create runtime for recording
runtime = DRTRuntime(mode='record', log_path='my_execution.log')

# Create runtime for replay
runtime = DRTRuntime(mode='replay', log_path='my_execution.log')
```

#### Methods

##### `run(target: Callable, *args, **kwargs) → Any`

Execute a program with deterministic execution control.

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `target` | `Callable` | The main function to execute |
| `*args` | `Any` | Positional arguments for target |
| `**kwargs` | `Any` | Keyword arguments for target |

**Returns:** Return value of target function

**Raises:**
- `DivergenceError` if replay diverges from recording
- `IncompleteLogError` if log lacks LOG_COMPLETE marker
- Any exception raised by target

**Example:**
```python
def my_program(name):
    print(f"Hello, {name}!")
    return 42

runtime = DRTRuntime(mode='record', log_path='test.log')
result = runtime.run(my_program, "World")
print(result)  # 42
```

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `mode` | `str` | Current mode ('record' or 'replay') |
| `log` | `EventLog` | The event log instance |
| `scheduler` | `Scheduler` | The scheduler instance |
| `is_recording` | `bool` | True if in record mode |
| `is_replaying` | `bool` | True if in replay mode |

---

### Convenience Functions

#### `run_recorded(target, log_path, verbose=False) → Any`

Convenience function to record an execution.

```python
from drt import run_recorded

def my_program():
    return "done"

result = run_recorded(my_program, log_path='exec.log', verbose=True)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | `Callable` | — | Function to execute |
| `log_path` | `str` | `'execution.log'` | Log file path |
| `verbose` | `bool` | `False` | Print execution summary |

#### `run_replay(target, log_path, verbose=False) → Any`

Convenience function to replay an execution.

```python
from drt import run_replay

result = run_replay(my_program, log_path='exec.log', verbose=True)
```

**Raises:**
- `DivergenceError` if execution diverges
- `IncompleteLogError` if log is incomplete

#### `dump_log(log_path: str) → str`

Dump an execution log in human-readable format.

```python
from drt import dump_log

print(dump_log('exec.log'))
```

**Output:**
```
DRT Log: exec.log
Entries: 25

[   0] t=   0 thread= 0 THREAD_CREATE new_thread=1
[   1] t=   0 thread= 1 SCHEDULE
[   2] t=   1 thread= 0 SCHEDULE
...
```

---

## Threading

### class `DRTThread`

A managed thread that integrates with the deterministic scheduler.

```python
from drt import DRTThread
```

#### Constructor

```python
DRTThread(
    target: Callable = None,
    args: tuple = (),
    kwargs: dict = None,
    name: str = None,
    daemon: bool = False
)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target` | `Callable` | `None` | Function to run in thread |
| `args` | `tuple` | `()` | Arguments for target |
| `kwargs` | `dict` | `None` | Keyword arguments for target |
| `name` | `str` | `None` | Thread name (auto-generated if None) |
| `daemon` | `bool` | `False` | Daemon thread flag |

**Example:**
```python
def worker(x, y):
    return x + y

t = DRTThread(target=worker, args=(1, 2), name="adder")
t.start()
t.join()
```

#### Methods

##### `start()`

Start the thread. The thread will not execute until the scheduler grants permission.

**Raises:**
- `RuntimeError` if thread already started

##### `join(timeout: float = None)`

Wait for the thread to complete.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | `float` | `None` | Maximum wait time (not fully implemented) |

**Raises:**
- `RuntimeError` if thread not started

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `thread_id` | `int` | DRT thread ID |
| `is_alive` | `bool` | True if thread is running |
| `name` | `str` | Thread name |

---

### `runtime_yield()`

Explicit yield point. Gives other threads a chance to run.

```python
from drt import runtime_yield

def worker():
    for i in range(10):
        do_work()
        runtime_yield()  # Allow other threads to run
```

**Usage:** Call this in long-running loops to enable thread interleaving.

---

### `get_current_thread_id() → int`

Get the DRT thread ID of the current thread.

```python
from drt import get_current_thread_id

def worker():
    tid = get_current_thread_id()
    print(f"I am thread {tid}")
```

**Returns:** Thread ID, or -1 if not a managed thread.

---

## Synchronization Primitives

### class `DRTMutex`

A deterministic mutex (lock) that integrates with the scheduler.

```python
from drt import DRTMutex
```

#### Constructor

```python
DRTMutex(name: str = None)
```

#### Methods

##### `acquire(blocking: bool = True) → bool`

Acquire the mutex. This is a yield point.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `blocking` | `bool` | `True` | Block until acquired |

**Returns:** `True` if acquired, `False` if non-blocking and not acquired.

##### `release()`

Release the mutex. This is a yield point.

**Raises:**
- `RuntimeError` if not owner

##### `locked() → bool`

Check if mutex is currently held.

#### Context Manager

```python
mutex = DRTMutex()

with mutex:
    # Critical section
    shared_resource += 1
```

---

### class `DRTCondition`

A deterministic condition variable.

```python
from drt import DRTCondition
```

#### Constructor

```python
DRTCondition(lock: DRTMutex = None, name: str = None)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lock` | `DRTMutex` | `None` | Associated mutex (created if None) |
| `name` | `str` | `None` | Name for debugging |

#### Methods

##### `wait(timeout: float = None) → bool`

Wait on the condition variable. Releases the mutex and blocks until signaled.

**Must hold the lock when calling.**

##### `wait_for(predicate: Callable[[], bool], timeout: float = None) → bool`

Wait until predicate returns True.

```python
cond.wait_for(lambda: queue_not_empty)
```

##### `notify(n: int = 1)`

Wake one or more waiting threads.

##### `notify_all()`

Wake all waiting threads.

#### Example

```python
mutex = DRTMutex()
cond = DRTCondition(mutex)
ready = False

# Consumer thread
def consumer():
    with mutex:
        while not ready:
            cond.wait()
        # Process data

# Producer thread
def producer():
    with mutex:
        ready = True
        cond.notify()
```

---

### class `DRTSemaphore`

A deterministic counting semaphore.

```python
from drt import DRTSemaphore
```

#### Constructor

```python
DRTSemaphore(value: int = 1, name: str = None)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `value` | `int` | `1` | Initial semaphore value |

#### Methods

##### `acquire(blocking: bool = True) → bool`

Decrement the semaphore, blocking if zero.

##### `release()`

Increment the semaphore.

#### Example

```python
# Limit concurrent access to 3
sem = DRTSemaphore(3)

def worker():
    with sem:
        # At most 3 threads here simultaneously
        access_resource()
```

---

### class `DRTBarrier`

A deterministic barrier for thread synchronization.

```python
from drt import DRTBarrier
```

#### Constructor

```python
DRTBarrier(parties: int, name: str = None)
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `parties` | `int` | Number of threads that must wait |

#### Methods

##### `wait() → int`

Wait at the barrier until all parties arrive.

**Returns:** Arrival index (0 to parties-1)

#### Example

```python
barrier = DRTBarrier(3)

def worker():
    # Phase 1
    do_phase1()
    
    barrier.wait()  # All threads synchronize here
    
    # Phase 2 (all threads start together)
    do_phase2()
```

---

## Nondeterminism Interceptors

These functions replace standard library functions with deterministic versions.

### Time Functions

#### `drt_time() → float`

Deterministic replacement for `time.time()`.

```python
from drt import drt_time

timestamp = drt_time()
```

**RECORD:** Returns real `time.time()` and logs it.  
**REPLAY:** Returns logged value.

#### `drt_monotonic() → float`

Deterministic replacement for `time.monotonic()`.

```python
from drt import drt_monotonic

elapsed = drt_monotonic()
```

**Returns:** Logical time (scheduler counter) as float.

#### `drt_sleep(seconds: float)`

Deterministic replacement for `time.sleep()`.

```python
from drt import drt_sleep

drt_sleep(1.0)  # Logical sleep, yields to other threads
```

**Note:** Does not actually delay—time is logical, not physical.

---

### Random Functions

#### `drt_random() → float`

Deterministic replacement for `random.random()`.

```python
from drt import drt_random

value = drt_random()  # Returns float in [0.0, 1.0)
```

#### `drt_randint(a: int, b: int) → int`

Deterministic replacement for `random.randint()`.

```python
from drt import drt_randint

roll = drt_randint(1, 6)  # Returns int in [a, b]
```

#### `drt_randrange(start: int, stop: int = None, step: int = 1) → int`

Deterministic replacement for `random.randrange()`.

#### `drt_choice(seq) → Any`

Deterministic replacement for `random.choice()`.

```python
from drt import drt_choice

color = drt_choice(['red', 'green', 'blue'])
```

#### `drt_shuffle(x: list)`

Deterministic replacement for `random.shuffle()`.

```python
from drt import drt_shuffle

deck = [1, 2, 3, 4, 5]
drt_shuffle(deck)  # Shuffles in place
```

#### `drt_sample(population, k: int) → list`

Deterministic replacement for `random.sample()`.

#### `drt_seed(value: int = None)`

Deterministic replacement for `random.seed()`.

---

### I/O Functions

#### `drt_read_file(path: str, size: int = -1) → bytes`

Deterministic file read.

```python
from drt import drt_read_file

data = drt_read_file('input.bin')
```

**RECORD:** Reads file and logs contents.  
**REPLAY:** Returns logged contents (file not accessed).

#### `drt_read_text(path: str, encoding: str = 'utf-8') → str`

Deterministic text file read.

```python
from drt import drt_read_text

text = drt_read_text('input.txt')
```

---

## Exceptions

### class `DRTError`

Base exception for all DRT errors.

```python
from drt import DRTError
```

---

### class `DivergenceError`

Raised when replay execution diverges from recording.

```python
from drt import DivergenceError

try:
    runtime.run(program)
except DivergenceError as e:
    print(f"Divergence at time {e.logical_time}")
    print(f"Expected: {e.expected}")
    print(f"Actual: {e.actual}")
```

**Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `logical_time` | `int` | When divergence occurred |
| `expected` | `str` | What the log expected |
| `actual` | `str` | What actually happened |

---

### class `LogCorruptionError`

Raised when the event log is corrupted.

---

### class `IncompleteLogError`

Raised when the log lacks LOG_COMPLETE marker (subclass of LogCorruptionError).

```python
from drt import IncompleteLogError

try:
    runtime = DRTRuntime(mode='replay', log_path='crashed.log')
except IncompleteLogError:
    print("Recording did not complete cleanly")
```

---

### class `RuntimeStateError`

Raised when the runtime is in an invalid state.

---

### class `ThreadStateError`

Raised when a thread is in an invalid state.

---

### class `SchedulerError`

Raised when the scheduler encounters an error.

---

### class `UnloggedNondeterminismError`

Raised when nondeterministic input bypasses the runtime.

---

## Advanced API

### class `EventLog`

Low-level access to the execution log.

```python
from drt import EventLog

log = EventLog('my.log')
log.open_for_replay()

for entry in log:
    print(entry)

print(log.dump_readable())
```

### class `EventType`

Enumeration of all event types.

```python
from drt import EventType

EventType.SCHEDULE      # = 1
EventType.LOCK_ACQUIRE  # = 10
EventType.TIME_READ     # = 30
# etc.
```

### class `LogEntry`

A single log entry.

```python
from drt import LogEntry

entry = LogEntry(
    logical_time=0,
    thread_id=1,
    event_type=EventType.SCHEDULE,
    payload=b''
)
```

### class `RuntimeMode`

Enumeration for runtime modes.

```python
from drt import RuntimeMode

RuntimeMode.RECORD
RuntimeMode.REPLAY
```

---

## Examples

### Example 1: Basic Record and Replay

```python
from drt import DRTRuntime, DRTThread

def program():
    results = []
    
    def worker(n):
        results.append(n)
    
    threads = [DRTThread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(f"Results: {results}")

# Record
runtime = DRTRuntime(mode='record', log_path='example1.log')
runtime.run(program)

# Replay (same output!)
runtime = DRTRuntime(mode='replay', log_path='example1.log')
runtime.run(program)
```

### Example 2: Mutex and Condition

```python
from drt import DRTRuntime, DRTThread, DRTMutex, DRTCondition

def producer_consumer():
    mutex = DRTMutex()
    cond = DRTCondition(mutex)
    queue = []
    done = [False]
    
    def producer():
        for i in range(5):
            with mutex:
                queue.append(i)
                cond.notify()
        with mutex:
            done[0] = True
            cond.notify()
    
    def consumer():
        while True:
            with mutex:
                while not queue and not done[0]:
                    cond.wait()
                if queue:
                    item = queue.pop(0)
                    print(f"Consumed: {item}")
                elif done[0]:
                    break
    
    p = DRTThread(target=producer)
    c = DRTThread(target=consumer)
    
    p.start()
    c.start()
    
    p.join()
    c.join()

runtime = DRTRuntime(mode='record', log_path='prodcons.log')
runtime.run(producer_consumer)
```

### Example 3: Deterministic Random

```python
from drt import DRTRuntime, drt_random, drt_randint

def random_program():
    values = [drt_random() for _ in range(5)]
    dice = [drt_randint(1, 6) for _ in range(3)]
    print(f"Random values: {values}")
    print(f"Dice rolls: {dice}")

# Record
runtime = DRTRuntime(mode='record', log_path='random.log')
runtime.run(random_program)

# Replay produces SAME random values
runtime = DRTRuntime(mode='replay', log_path='random.log')
runtime.run(random_program)
```

### Example 4: Debugging a Race Condition

```python
from drt import DRTRuntime, DRTThread, runtime_yield

def buggy_counter():
    counter = [0]  # Shared state
    
    def increment():
        for _ in range(100):
            # BUG: Read-modify-write is not atomic
            current = counter[0]
            runtime_yield()  # Allows interleaving
            counter[0] = current + 1
    
    threads = [DRTThread(target=increment) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    expected = 300
    actual = counter[0]
    print(f"Expected: {expected}, Actual: {actual}")
    if actual != expected:
        print("BUG: Lost updates detected!")

# Record until we capture the bug
runtime = DRTRuntime(mode='record', log_path='race.log')
runtime.run(buggy_counter)

# Replay reproduces the bug exactly
runtime = DRTRuntime(mode='replay', log_path='race.log')
runtime.run(buggy_counter)
```

---

## See Also

- [Architecture Document](ARCHITECTURE.md) — Detailed system design
- [User Guide](USER_GUIDE.md) — Step-by-step usage guide
- [Failure Analysis](../FAILURE_ANALYSIS.md) — Limitations and known issues
