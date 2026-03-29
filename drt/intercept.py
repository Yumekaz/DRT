"""
DRT Intercept - Nondeterminism interceptors.

Provides deterministic replacements for common sources of nondeterminism:
- time.time()
- random.random()
- time.sleep()
- File I/O reads

In RECORD mode: Call real functions and log returned values
In REPLAY mode: Return logged values only

Users must use these functions instead of the standard library versions
to ensure deterministic replay.
"""

import time as _time
import random as _random
import os
from typing import Optional, Any, BinaryIO
from pathlib import Path

from .scheduler import Scheduler, RuntimeMode
from .events import (
    EventType, LogEntry,
    serialize_float_payload, deserialize_float_payload,
    serialize_io_payload, deserialize_io_payload
)
from .thread import get_current_thread_id
from .exceptions import DivergenceError, UnloggedNondeterminismError


class NondeterminismInterceptor:
    """
    Manages interception of nondeterministic operations.
    
    Provides methods that replace standard library functions with
    deterministic versions that record or replay values.
    """
    
    def __init__(self, scheduler: Scheduler):
        """
        Initialize the interceptor.
        
        Args:
            scheduler: The runtime scheduler
        """
        self._scheduler = scheduler
        self._log = scheduler.log
        self._replay_index = 0
        
        # Seeded random generator for deterministic random numbers
        self._random_state = _random.Random()
        self._random_initialized = False
        
    @property
    def mode(self) -> RuntimeMode:
        """Get the current runtime mode."""
        return self._scheduler.mode
        
    def time(self) -> float:
        """
        Get current time (deterministically).
        
        RECORD: Returns real time.time() and logs it
        REPLAY: Returns logged time value
        
        Returns:
            Unix timestamp as float
        """
        thread_id = get_current_thread_id()
        
        if self.mode == RuntimeMode.RECORD:
            # Call real function and log
            value = _time.time()
            self._log_value(EventType.TIME_READ, value, thread_id)
            return value
        else:
            # Replay: return logged value
            return self._replay_float(EventType.TIME_READ, thread_id)
            
    def monotonic(self) -> float:
        """
        Get monotonic time (deterministically).
        
        Uses logical time for determinism.
        
        Returns:
            Logical time as float
        """
        return float(self._scheduler.logical_time)
        
    def sleep(self, seconds: float):
        """
        Sleep for a duration (logically).
        
        In deterministic mode, this is a yield point but does not
        actually delay - time is logical, not physical.
        
        Args:
            seconds: Duration (used only for proportional logical delay)
        """
        from .thread import runtime_yield
        
        # Yield to give other threads a chance
        # In a more sophisticated implementation, we could track
        # logical sleep time and order wakeups
        runtime_yield()
        
    def random(self) -> float:
        """
        Get random number (deterministically).
        
        RECORD: Generates and logs random value
        REPLAY: Returns logged value
        
        Returns:
            Random float in [0.0, 1.0)
        """
        thread_id = get_current_thread_id()
        
        if self.mode == RuntimeMode.RECORD:
            value = _random.random()
            self._log_value(EventType.RANDOM_READ, value, thread_id)
            return value
        else:
            return self._replay_float(EventType.RANDOM_READ, thread_id)
            
    def randint(self, a: int, b: int) -> int:
        """
        Get random integer (deterministically).
        
        Args:
            a: Lower bound (inclusive)
            b: Upper bound (inclusive)
            
        Returns:
            Random integer in [a, b]
        """
        # Use our deterministic random() to generate the value
        r = self.random()
        return a + int(r * (b - a + 1))
        
    def randrange(self, start: int, stop: int = None, step: int = 1) -> int:
        """
        Get random integer from range (deterministically).
        
        Args:
            start: Start of range
            stop: End of range (exclusive)
            step: Step size
            
        Returns:
            Random value from range
        """
        if stop is None:
            stop = start
            start = 0
            
        width = (stop - start + step - 1) // step
        return start + step * int(self.random() * width)
        
    def choice(self, seq):
        """
        Choose random element (deterministically).
        
        Args:
            seq: Sequence to choose from
            
        Returns:
            Random element
        """
        if not seq:
            raise IndexError("Cannot choose from empty sequence")
        return seq[int(self.random() * len(seq))]
        
    def shuffle(self, x: list):
        """
        Shuffle list in place (deterministically).
        
        Args:
            x: List to shuffle
        """
        for i in range(len(x) - 1, 0, -1):
            j = int(self.random() * (i + 1))
            x[i], x[j] = x[j], x[i]
            
    def sample(self, population, k: int) -> list:
        """
        Sample without replacement (deterministically).
        
        Args:
            population: Population to sample from
            k: Number of samples
            
        Returns:
            List of k samples
        """
        pool = list(population)
        n = len(pool)
        if k > n:
            raise ValueError("Sample larger than population")
            
        result = []
        for _ in range(k):
            idx = int(self.random() * len(pool))
            result.append(pool.pop(idx))
        return result
        
    def seed(self, value: int = None):
        """
        Seed random number generator.
        
        In RECORD mode, logs the seed.
        In REPLAY mode, verifies the seed matches.
        
        Args:
            value: Seed value (uses time if None in RECORD mode)
        """
        thread_id = get_current_thread_id()
        
        if self.mode == RuntimeMode.RECORD:
            if value is None:
                value = int(_time.time() * 1000000)
            self._log_value(EventType.RANDOM_SEED, float(value), thread_id)
            self._random_state.seed(value)
        else:
            # Replay: use logged seed
            logged_seed = self._replay_float(EventType.RANDOM_SEED, thread_id)
            self._random_state.seed(int(logged_seed))
            
        self._random_initialized = True
        
    def read_file(self, path: str, size: int = -1) -> bytes:
        """
        Read file contents (deterministically).
        
        RECORD: Reads real file and logs contents
        REPLAY: Returns logged contents
        
        Args:
            path: File path
            size: Number of bytes to read (-1 for all)
            
        Returns:
            File contents as bytes
        """
        thread_id = get_current_thread_id()
        
        if self.mode == RuntimeMode.RECORD:
            with open(path, 'rb') as f:
                data = f.read(size) if size >= 0 else f.read()
            self._log_bytes(EventType.IO_READ, data, thread_id)
            return data
        else:
            return self._replay_bytes(EventType.IO_READ, thread_id)
            
    def read_text_file(self, path: str, encoding: str = 'utf-8') -> str:
        """
        Read text file (deterministically).
        
        Args:
            path: File path
            encoding: Text encoding
            
        Returns:
            File contents as string
        """
        data = self.read_file(path)
        return data.decode(encoding)
        
    # Internal logging methods
    
    def _log_value(self, event_type: EventType, value: float, thread_id: int):
        """Log a float value."""
        entry = LogEntry(
            logical_time=self._scheduler.logical_time,
            thread_id=thread_id if thread_id >= 0 else 0,
            event_type=event_type,
            payload=serialize_float_payload(value)
        )
        self._log.append(entry)
        
    def _log_bytes(self, event_type: EventType, data: bytes, thread_id: int):
        """Log bytes data."""
        entry = LogEntry(
            logical_time=self._scheduler.logical_time,
            thread_id=thread_id if thread_id >= 0 else 0,
            event_type=event_type,
            payload=serialize_io_payload(data)
        )
        self._log.append(entry)
        
    def _replay_float(self, expected_type: EventType, thread_id: int) -> float:
        """Get float value from replay log."""
        entry = self._get_replay_entry(expected_type, thread_id)
        return deserialize_float_payload(entry.payload)
        
    def _replay_bytes(self, expected_type: EventType, thread_id: int) -> bytes:
        """Get bytes data from replay log."""
        entry = self._get_replay_entry(expected_type, thread_id)
        return deserialize_io_payload(entry.payload)
        
    def _get_replay_entry(self, expected_type: EventType, 
                          thread_id: int) -> LogEntry:
        """
        Get the next replay entry of the expected type.
        
        Searches forward in the log for a matching entry.
        
        Raises:
            DivergenceError: If expected entry not found
        """
        log_entries = list(self._log)
        
        # Search for next entry of this type
        for i in range(self._replay_index, len(log_entries)):
            entry = log_entries[i]
            if entry.event_type == expected_type:
                self._replay_index = i + 1
                return entry
                
        raise DivergenceError(
            f"Expected {expected_type.name} event not found in log",
            self._scheduler.logical_time,
            expected_type.name,
            "end of log"
        )


# Module-level interceptor instance (set by runtime)
_interceptor: Optional[NondeterminismInterceptor] = None


def set_interceptor(interceptor: NondeterminismInterceptor):
    """Set the global interceptor instance."""
    global _interceptor
    _interceptor = interceptor


def _get_interceptor() -> NondeterminismInterceptor:
    """Get the global interceptor, raising if not initialized."""
    if _interceptor is None:
        raise RuntimeError(
            "Interceptor not initialized. "
            "Use drt.runtime.DRTRuntime to run your program."
        )
    return _interceptor


# Public API - drop-in replacements for standard library functions

def drt_time() -> float:
    """
    Deterministic replacement for time.time().
    
    Returns:
        Unix timestamp
    """
    return _get_interceptor().time()


def drt_monotonic() -> float:
    """
    Deterministic replacement for time.monotonic().
    
    Returns:
        Monotonic time (logical)
    """
    return _get_interceptor().monotonic()


def drt_sleep(seconds: float):
    """
    Deterministic replacement for time.sleep().
    
    Args:
        seconds: Sleep duration (logical)
    """
    _get_interceptor().sleep(seconds)


def drt_random() -> float:
    """
    Deterministic replacement for random.random().
    
    Returns:
        Random float in [0.0, 1.0)
    """
    return _get_interceptor().random()


def drt_randint(a: int, b: int) -> int:
    """
    Deterministic replacement for random.randint().
    
    Args:
        a: Lower bound (inclusive)
        b: Upper bound (inclusive)
        
    Returns:
        Random integer in [a, b]
    """
    return _get_interceptor().randint(a, b)


def drt_randrange(start: int, stop: int = None, step: int = 1) -> int:
    """
    Deterministic replacement for random.randrange().
    
    Args:
        start: Start of range
        stop: End of range (exclusive)
        step: Step size
        
    Returns:
        Random integer from range
    """
    return _get_interceptor().randrange(start, stop, step)


def drt_choice(seq):
    """
    Deterministic replacement for random.choice().
    
    Args:
        seq: Sequence to choose from
        
    Returns:
        Random element
    """
    return _get_interceptor().choice(seq)


def drt_shuffle(x: list):
    """
    Deterministic replacement for random.shuffle().
    
    Args:
        x: List to shuffle in place
    """
    _get_interceptor().shuffle(x)


def drt_sample(population, k: int) -> list:
    """
    Deterministic replacement for random.sample().
    
    Args:
        population: Population to sample from
        k: Number of samples
        
    Returns:
        List of k samples
    """
    return _get_interceptor().sample(population, k)


def drt_seed(value: int = None):
    """
    Deterministic replacement for random.seed().
    
    Args:
        value: Seed value
    """
    _get_interceptor().seed(value)


def drt_read_file(path: str, size: int = -1) -> bytes:
    """
    Deterministic file read.
    
    Args:
        path: File path
        size: Bytes to read (-1 for all)
        
    Returns:
        File contents
    """
    return _get_interceptor().read_file(path, size)


def drt_read_text(path: str, encoding: str = 'utf-8') -> str:
    """
    Deterministic text file read.
    
    Args:
        path: File path
        encoding: Text encoding
        
    Returns:
        File contents as string
    """
    return _get_interceptor().read_text_file(path, encoding)
