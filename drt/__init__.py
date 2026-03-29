"""
DRT - Deterministic Record-and-Replay Runtime

A user-space runtime for reproducing concurrency bugs in Python.

The Problem:
    Concurrency bugs are nondeterministic. They appear once in 1000 runs,
    and disappear when you add logging or attach a debugger.

    The Solution:
        DRT records scheduler decisions and supported nondeterministic inputs
        for DRT-managed code, then replays that recorded execution.

Quick Start:
    from drt import DRTRuntime, DRTThread, DRTMutex
    from drt import drt_time, drt_random, runtime_yield
    
    def my_program():
        mutex = DRTMutex()
        results = []
        
        def worker(n):
            with mutex:
                results.append(f"Worker {n} at {drt_time()}")
                
        threads = [DRTThread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results
            
    # Record execution
    runtime = DRTRuntime(mode='record', log_path='execution.log')
    recorded_results = runtime.run(my_program)
    
    # Replay execution
    runtime = DRTRuntime(mode='replay', log_path='execution.log')
    replayed_results = runtime.run(my_program)
 
    assert recorded_results == replayed_results

    Core Guarantee:
        Given the same initial state and a complete execution log, replayed
        execution follows the same recorded behavior within the supported
        DRT-managed API surface, or raises DivergenceError.

See DESIGN.md for architectural decisions and tradeoffs.
"""

__version__ = '0.3.0'

# Core runtime
from .runtime import (
    DRTRuntime,
    run_recorded,
    run_replay,
    dump_log,
)

# Thread abstraction
from .thread import (
    DRTThread,
    runtime_yield,
    get_current_thread_id,
)

# Synchronization primitives
from .sync import (
    DRTMutex,
    DRTCondition,
    DRTSemaphore,
    DRTBarrier,
)

# Nondeterminism interceptors
from .intercept import (
    drt_time,
    drt_monotonic,
    drt_sleep,
    drt_random,
    drt_randint,
    drt_randrange,
    drt_choice,
    drt_shuffle,
    drt_sample,
    drt_seed,
    drt_read_file,
    drt_read_text,
)

# Exceptions
from .exceptions import (
    DRTError,
    DeadlockError,
    DivergenceError,
    LogCorruptionError,
    IncompleteLogError,
    RuntimeStateError,
    ThreadStateError,
    UnloggedNondeterminismError,
    SchedulerError,
)

# Advanced usage
from .events import EventType, LogEntry
from .log import EventLog
from .scheduler import RuntimeMode

__all__ = [
    # Version
    '__version__',
    
    # Core
    'DRTRuntime',
    'run_recorded',
    'run_replay',
    'dump_log',
    
    # Threading
    'DRTThread',
    'runtime_yield',
    'get_current_thread_id',
    
    # Synchronization
    'DRTMutex',
    'DRTCondition',
    'DRTSemaphore',
    'DRTBarrier',
    
    # Interceptors
    'drt_time',
    'drt_monotonic',
    'drt_sleep',
    'drt_random',
    'drt_randint',
    'drt_randrange',
    'drt_choice',
    'drt_shuffle',
    'drt_sample',
    'drt_seed',
    'drt_read_file',
    'drt_read_text',
    
    # Exceptions
    'DRTError',
    'DeadlockError',
    'DivergenceError',
    'LogCorruptionError',
    'IncompleteLogError',
    'RuntimeStateError',
    'ThreadStateError',
    'UnloggedNondeterminismError',
    'SchedulerError',
    
    # Advanced
    'EventType',
    'LogEntry',
    'EventLog',
    'RuntimeMode',
]
