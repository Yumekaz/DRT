# DRT User Guide

## A Practical Guide to Deterministic Record-and-Replay

**Version:** 0.4.0  

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Installation](#2-installation)
3. [Getting Started](#3-getting-started)
4. [Core Concepts](#4-core-concepts)
5. [Converting Your Code](#5-converting-your-code)
6. [Recording Executions](#6-recording-executions)
7. [Replaying Executions](#7-replaying-executions)
8. [Debugging with DRT](#8-debugging-with-drt)
9. [Best Practices](#9-best-practices)
10. [Troubleshooting](#10-troubleshooting)
11. [FAQ](#11-faq)

---

## 1. Introduction

### What is DRT?

DRT (Deterministic Record-and-Replay Runtime) is a Python library that lets you record and replay executions of code that uses the DRT API. In supported code paths, DRT records scheduler decisions and selected nondeterministic inputs, then replays that execution and raises `DivergenceError` if behavior drifts.

### Why Use DRT?

**Problem:** Concurrency bugs are hard to debug because they're nondeterministic. A race condition might occur once in a thousand runs, and disappear when you add logging.

**Solution:** DRT captures a failing DRT-managed execution so you can replay that same trace until you understand and fix it.

### What Can DRT Do?

✅ Record multithreaded Python programs  
✅ Replay executions deterministically  
✅ Detect when replay diverges from recording  
✅ Capture time, random, and file I/O  

### What Can't DRT Do?

❌ Record distributed systems (multiple processes)  
❌ Record network I/O  
❌ Improve performance  
❌ Work with non-DRT threading primitives  

---

## 2. Installation

### From Source

```bash
git clone <repository>
cd drt-project
pip install -e .
```

### Verify Installation

```python
from drt import DRTRuntime, DRTThread
print("DRT installed successfully!")
```

### Requirements

- Python 3.9 or later
- No external dependencies

---

## 3. Getting Started

### Your First DRT Program

```python
from drt import DRTRuntime, DRTThread

def hello_threads():
    def say_hello(name):
        print(f"Hello from {name}!")
    
    t1 = DRTThread(target=say_hello, args=("Thread 1",))
    t2 = DRTThread(target=say_hello, args=("Thread 2",))
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()

# Record the execution
runtime = DRTRuntime(mode='record', log_path='hello.log')
runtime.run(hello_threads)
print("Recorded!")

# Replay the execution
runtime = DRTRuntime(mode='replay', log_path='hello.log')
runtime.run(hello_threads)
print("Replayed!")
```

**Output:**
```
Hello from Thread 1!
Hello from Thread 2!
Recorded!
Hello from Thread 1!
Hello from Thread 2!
Replayed!
```

Notice the order is **identical** in both runs. That's determinism!

---

## 4. Core Concepts

### 4.1 Modes

DRT operates in two modes:

| Mode | Description |
|------|-------------|
| **RECORD** | Execute program, capture decisions to log file |
| **REPLAY** | Execute program, follow decisions from log file |

### 4.2 Nondeterminism

Nondeterminism is anything that can vary between runs:

- **Thread scheduling** — which thread runs when
- **Time** — `time.time()` returns different values
- **Random numbers** — `random.random()` varies
- **File contents** — files may change between runs

DRT intercepts and records all of these.

### 4.3 Yield Points

Threads can only switch at specific "yield points":

- Acquiring a lock
- Releasing a lock
- Waiting on a condition
- Calling `runtime_yield()`
- Thread exit

Between yield points, a thread runs uninterrupted.

### 4.4 The Log File

The log file stores:

- Every scheduling decision
- Every nondeterministic value
- A completion marker

If the program crashes, the log is incomplete and can't be replayed.

---

## 5. Converting Your Code

### 5.1 Replace Imports

**Before (standard library):**
```python
import threading
import time
import random
```

**After (DRT):**
```python
from drt import (
    DRTThread, DRTMutex, DRTCondition,
    drt_time, drt_random, drt_sleep, runtime_yield
)
```

### 5.2 Replace Thread Creation

**Before:**
```python
t = threading.Thread(target=worker, args=(1,))
t.start()
t.join()
```

**After:**
```python
t = DRTThread(target=worker, args=(1,))
t.start()
t.join()
```

### 5.3 Replace Locks

**Before:**
```python
lock = threading.Lock()
with lock:
    # critical section
```

**After:**
```python
lock = DRTMutex()
with lock:
    # critical section
```

### 5.4 Replace Condition Variables

**Before:**
```python
cond = threading.Condition()
with cond:
    cond.wait()
    cond.notify()
```

**After:**
```python
mutex = DRTMutex()
cond = DRTCondition(mutex)
with mutex:
    cond.wait()
    cond.notify()
```

### 5.5 Replace Time/Random

**Before:**
```python
now = time.time()
value = random.random()
time.sleep(1.0)
```

**After:**
```python
now = drt_time()
value = drt_random()
drt_sleep(1.0)
```

### 5.6 Add Yield Points (Optional)

Add `runtime_yield()` in long-running loops to allow thread switching:

```python
def worker():
    for i in range(1000000):
        do_work(i)
        if i % 1000 == 0:
            runtime_yield()  # Let other threads run
```

---

## 6. Recording Executions

### Basic Recording

```python
from drt import DRTRuntime

def my_program():
    # Your code here
    pass

runtime = DRTRuntime(mode='record', log_path='execution.log')
runtime.run(my_program)
```

### Recording with Arguments

```python
def my_program(x, y, debug=False):
    pass

runtime = DRTRuntime(mode='record', log_path='execution.log')
runtime.run(my_program, 10, 20, debug=True)
```

### Recording with Return Value

```python
def my_program():
    return compute_result()

runtime = DRTRuntime(mode='record', log_path='execution.log')
result = runtime.run(my_program)
print(f"Result: {result}")
```

### Checking Recording Status

```python
runtime = DRTRuntime(mode='record', log_path='execution.log')
runtime.run(my_program)

print(f"Events logged: {len(runtime.log)}")
print(f"Log complete: {runtime.log.is_complete}")
```

---

## 7. Replaying Executions

### Basic Replay

```python
from drt import DRTRuntime

runtime = DRTRuntime(mode='replay', log_path='execution.log')
runtime.run(my_program)
```

### Handling Errors

```python
from drt import DRTRuntime, DivergenceError, IncompleteLogError

try:
    runtime = DRTRuntime(mode='replay', log_path='execution.log')
    runtime.run(my_program)
except IncompleteLogError:
    print("The recording did not complete. Was there a crash?")
except DivergenceError as e:
    print(f"Replay diverged at time {e.logical_time}")
    print(f"Expected: {e.expected}")
    print(f"Actual: {e.actual}")
```

### Viewing the Log

```python
from drt import dump_log

print(dump_log('execution.log'))
```

**Output:**
```
DRT Log: execution.log
Entries: 42

[   0] t=   0 thread= 0 THREAD_CREATE new_thread=1
[   1] t=   0 thread= 1 SCHEDULE
[   2] t=   1 thread= 1 LOCK_ACQUIRE mutex=0
...
```

---

## 8. Debugging with DRT

### 8.1 The Debugging Workflow

1. **Reproduce the bug** — Run until it happens
2. **Record** — Capture the failing execution
3. **Replay** — Reproduce the bug on demand
4. **Debug** — Add prints, use debugger, understand the bug
5. **Fix** — Modify the code
6. **Verify** — Confirm fix with new recordings

### 8.2 Finding Race Conditions

```python
from drt import DRTRuntime, DRTThread, runtime_yield

def race_condition_test():
    shared = {'count': 0}
    
    def increment():
        for _ in range(100):
            temp = shared['count']
            runtime_yield()  # Potential race window
            shared['count'] = temp + 1
    
    threads = [DRTThread(target=increment) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    expected = 300
    actual = shared['count']
    if actual != expected:
        print(f"RACE DETECTED: expected {expected}, got {actual}")
        return True
    return False

# Record until we capture a race
for i in range(100):
    runtime = DRTRuntime(mode='record', log_path=f'race_{i}.log')
    if runtime.run(race_condition_test):
        print(f"Captured race in race_{i}.log")
        break
```

### 8.3 Replaying with Debug Output

```python
# Add debugging, the race will still occur
def race_condition_test_debug():
    shared = {'count': 0}
    
    def increment():
        for i in range(100):
            temp = shared['count']
            print(f"Thread read: {temp}")  # Debug output
            runtime_yield()
            shared['count'] = temp + 1
            print(f"Thread wrote: {temp + 1}")  # Debug output
    
    # ... rest same as before

# Replay with debug output
runtime = DRTRuntime(mode='replay', log_path='race_0.log')
runtime.run(race_condition_test_debug)
```

### 8.4 Using with pdb

```python
import pdb

def debug_program():
    pdb.set_trace()  # Breakpoint here
    # Your code

runtime = DRTRuntime(mode='replay', log_path='execution.log')
runtime.run(debug_program)
```

---

## 9. Best Practices

### 9.1 Design for Determinism

✅ **Do:**
- Use DRT primitives for all concurrency
- Use `drt_time()`, `drt_random()` for nondeterministic values
- Add `runtime_yield()` in loops for better interleaving

❌ **Don't:**
- Mix DRT and standard library threading
- Call `time.time()` or `random.random()` directly
- Rely on dictionary ordering in threaded code

### 9.2 Keep Logs Organized

```python
import datetime

timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_path = f"logs/execution_{timestamp}.log"
```

### 9.3 Test Both Record and Replay

```python
def test_my_program():
    # Record
    runtime = DRTRuntime(mode='record', log_path='test.log')
    result1 = runtime.run(my_program)
    
    # Replay
    runtime = DRTRuntime(mode='replay', log_path='test.log')
    result2 = runtime.run(my_program)
    
    assert result1 == result2, "Replay produced different result!"
```

### 9.4 Handle Crashes Gracefully

```python
import os

log_path = 'execution.log'

# Check if previous run crashed
if os.path.exists(log_path):
    try:
        runtime = DRTRuntime(mode='replay', log_path=log_path)
    except IncompleteLogError:
        print("Previous run crashed, starting fresh recording")
        os.remove(log_path)
```

---

## 10. Troubleshooting

### "DivergenceError: Thread X is not runnable"

**Cause:** The replay expected thread X to run, but it's blocked.

**Solutions:**
1. Ensure you're using the same code for record and replay
2. Check if external state changed (files, environment)
3. Verify all synchronization uses DRT primitives

### "IncompleteLogError"

**Cause:** The recorded execution didn't finish cleanly.

**Solutions:**
1. The original program crashed—this is expected
2. Check for unhandled exceptions in your code
3. Ensure `runtime.run()` completes normally

### "RuntimeError: not a DRTThread"

**Cause:** Calling DRT functions from a non-managed thread.

**Solutions:**
1. Ensure all threads are `DRTThread`, not `threading.Thread`
2. The main function passed to `runtime.run()` is automatically managed

### Deadlock During Recording

**Cause:** Threads are waiting for each other.

**Solutions:**
1. Check for lock ordering issues
2. Add `runtime_yield()` in loops
3. Verify condition variable logic

### Different Results on Replay

**Cause:** Something is not being intercepted.

**Solutions:**
1. Replace all `time.time()` → `drt_time()`
2. Replace all `random.*` → `drt_random()` etc.
3. Check for file I/O that should use `drt_read_file()`

---

## 11. FAQ

### Q: Can I use DRT in production?

**A:** DRT is designed for debugging, not production. It adds overhead and changes timing. Use it to find and fix bugs, then deploy without DRT.

### Q: Can I record a GUI application?

**A:** GUI frameworks (Tkinter, PyQt, etc.) use their own threading and event loops which DRT doesn't control. You can use DRT for the backend logic if it's separated.

### Q: How big do log files get?

**A:** Roughly 20-50 bytes per event. A program with 1000 thread switches, 100 random calls, and 100 time checks would produce a ~25KB log.

### Q: Can I edit the log file?

**A:** Treat the log as an internal artifact, not a file you should hand-edit. Current logs carry format-versioned completion metadata with an entry count and CRC32 over the serialized event body, so accidental corruption or casual edits should be detected during replay or `drt verify`. That is an integrity check, not tamper-proof cryptographic signing.

### Q: Can I replay on a different machine?

**A:** Yes, as long as:
- Same Python version
- Same DRT version
- Same program code
- Files read with `drt_read_file()` (contents are logged)

### Q: Why not just use monkey-patching?

**A:** Monkey-patching the standard library is fragile:
- CPython uses threading internally
- C extensions bypass Python
- Order of imports matters
- Hard to debug issues

DRT's explicit API makes nondeterminism visible and reliable.

### Q: How do I know all nondeterminism is captured?

**A:** You don't, automatically. Follow the conversion guide carefully. If replay diverges, you missed something—the error will tell you where.

### Q: Can I use DRT with pytest?

**A:** Yes!

```python
# test_mymodule.py
from drt import DRTRuntime

def test_determinism():
    runtime = DRTRuntime(mode='record', log_path='test.log')
    result1 = runtime.run(my_function)
    
    runtime = DRTRuntime(mode='replay', log_path='test.log')
    result2 = runtime.run(my_function)
    
    assert result1 == result2
```

---

## Next Steps

- Read the [API Reference](API_REFERENCE.md) for complete function documentation
- Study the [Architecture Document](ARCHITECTURE.md) for system internals
- Run the demos in `demo/` to see DRT in action
- Try converting one of your own programs!
