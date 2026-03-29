# DRT - Deterministic Record-and-Replay Runtime

A user-space runtime for reproducing concurrency bugs in Python.

---

## The Problem

Concurrency bugs are nondeterministic. They depend on thread scheduling, which varies between runs:

```
$ python buggy_program.py
Counter: 287  # Bug! Expected 300

$ python buggy_program.py
Counter: 300  # Works fine

$ python buggy_program.py  # Add print statements to debug
Counter: 300  # Bug disappeared!
```

The bug is real, but you can't reproduce it. Adding logging changes timing. Attaching a debugger changes timing. The bug vanishes when you try to observe it.

## The Solution

DRT records the exact thread schedule and all nondeterministic inputs:

```
$ python buggy_program.py --record bug.log
Counter: 287  # Bug captured!

$ python buggy_program.py --replay bug.log
Counter: 287  # Bug reproduced!

$ python buggy_program.py --replay bug.log
Counter: 287  # Bug reproduced again!

# Now debug with confidence - the bug won't disappear
```

---

## Quick Start

```python
from drt import DRTRuntime, DRTThread, DRTMutex, runtime_yield

def buggy_program():
    counter = [0]
    
    def worker():
        for _ in range(100):
            # Bug: read-modify-write without lock
            temp = counter[0]
            runtime_yield()  # Other thread can run here
            counter[0] = temp + 1
    
    threads = [DRTThread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(f"Counter: {counter[0]}")  # Should be 300, but isn't

# Record the buggy execution
runtime = DRTRuntime(mode='record', log_path='bug.log')
runtime.run(buggy_program)

# Replay - reproduces the exact same bug
runtime = DRTRuntime(mode='replay', log_path='bug.log')
runtime.run(buggy_program)
```

---

## How It Works

1. **Controlled Scheduling**: Threads only run when the scheduler permits
2. **Yield Points**: Threads can only switch at specific points (lock acquire, lock release, explicit yield)
3. **Logged Decisions**: Every scheduling decision is recorded
4. **Exact Replay**: During replay, the log is followed exactly

See [DESIGN.md](DESIGN.md) for architectural decisions and tradeoffs.

---

## API

### Runtime

```python
from drt import DRTRuntime

# Record execution
runtime = DRTRuntime(mode='record', log_path='execution.log')
runtime.run(my_program)

# Replay execution
runtime = DRTRuntime(mode='replay', log_path='execution.log')
runtime.run(my_program)
```

### Threading

```python
from drt import DRTThread, runtime_yield

def worker():
    print("Working")
    runtime_yield()  # Explicit yield point
    print("Done")

t = DRTThread(target=worker)
t.start()
t.join()
```

### Synchronization

```python
from drt import DRTMutex, DRTCondition, DRTSemaphore, DRTBarrier

# Mutex
mutex = DRTMutex()
with mutex:
    # Critical section
    pass

# Condition Variable
cond = DRTCondition(mutex)
with mutex:
    while not ready:
        cond.wait()

# Semaphore
sem = DRTSemaphore(value=3)
with sem:
    # Up to 3 concurrent
    pass

# Barrier
barrier = DRTBarrier(parties=4)
barrier.wait()  # Blocks until 4 threads arrive
```

### Nondeterminism Interceptors

```python
from drt import drt_time, drt_random, drt_randint, drt_read_file

# Time (recorded/replayed)
timestamp = drt_time()

# Random (recorded/replayed)
value = drt_random()
n = drt_randint(1, 100)

# File I/O (recorded/replayed)
data = drt_read_file('input.txt')
```

---

## Installation

```bash
cd drt-project
pip install -e .
```

---

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Race condition demo
python tests/test_race_condition.py
```

---

## Limitations

**In Scope:**
- Single process, multiple Python threads
- User-space runtime (no kernel modifications)
- Explicit API (must use DRT primitives)

**Out of Scope:**
- Distributed systems
- Automatic interception (no monkey-patching)
- External processes, signals
- GUI debugger

**Important:** You must use `DRTThread`, `DRTMutex`, `drt_time()`, etc. instead of standard library equivalents. DRT does not automatically intercept `threading.Thread` or `time.time()`. This is intentional - see [DESIGN.md](DESIGN.md) for why.

---

## Project Structure

```
drt-project/
├── drt/                    # Core runtime
│   ├── __init__.py         # Public API
│   ├── runtime.py          # Main controller
│   ├── scheduler.py        # Deterministic scheduler  
│   ├── thread.py           # Managed threads
│   ├── sync.py             # Mutex, Condition, etc.
│   ├── intercept.py        # Time, random, I/O capture
│   ├── log.py              # Binary event log
│   ├── events.py           # Event types
│   └── exceptions.py       # Error types
├── tests/
│   ├── test_runtime.py     # Core tests
│   └── test_race_condition.py  # Race condition demo
├── demo/
│   ├── run_demo.py         # Full demonstration
│   ├── bank_transfer.py    # Bank transfer bug
│   └── producer_consumer.py # Producer-consumer bug
├── docs/                   # Documentation
├── DESIGN.md               # Design decisions & tradeoffs
├── README.md               # This file
└── setup.py
```

---

## License

MIT
