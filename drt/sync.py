"""
DRT Sync - Deterministic synchronization primitives.

Provides DRTMutex and DRTCondition as replacements for threading.Lock
and threading.Condition that integrate with the deterministic scheduler.

All synchronization points are yield points - the scheduler decides
which thread runs after each acquire/release/wait/signal.
"""

import threading
from typing import Optional, Callable

from .scheduler import Scheduler
from .thread import get_current_thread_id


# Global mutex/condition ID counter
_next_sync_id = 0
_sync_id_lock = threading.Lock()


def _get_next_sync_id() -> int:
    """Get a unique ID for a synchronization primitive."""
    global _next_sync_id
    with _sync_id_lock:
        sync_id = _next_sync_id
        _next_sync_id += 1
        return sync_id


class DRTMutex:
    """
    A deterministic mutex (lock).
    
    Integrates with the scheduler to ensure deterministic acquisition order.
    Lock acquire and release are yield points.
    
    Example:
        mutex = DRTMutex()
        
        with mutex:
            # Critical section
            pass
    """
    
    # Class-level scheduler reference
    _scheduler: Optional[Scheduler] = None
    
    @classmethod
    def set_scheduler(cls, scheduler: Scheduler):
        """Set the scheduler for all DRTMutex instances."""
        cls._scheduler = scheduler
        
    def __init__(self, name: str = None):
        """
        Initialize a mutex.
        
        Args:
            name: Optional name for debugging
        """
        self._id = _get_next_sync_id()
        self._name = name or f"Mutex-{self._id}"
        self._owner: Optional[int] = None
        
    def acquire(self, blocking: bool = True) -> bool:
        """
        Acquire the mutex.
        
        This is a yield point - the scheduler will decide when this
        thread can proceed.
        
        Args:
            blocking: If True, block until acquired. If False, return
                     immediately if mutex is held by another thread.
                     
        Returns:
            True if acquired, False if non-blocking and not acquired.
        """
        if self._scheduler is None:
            raise RuntimeError("DRTMutex.set_scheduler() must be called first")
            
        thread_id = get_current_thread_id()
        if thread_id < 0:
            raise RuntimeError("acquire() must be called from a DRTThread")
            
        # Try to acquire
        acquired = self._scheduler.mutex_lock(thread_id, self._id)
        
        if acquired:
            self._owner = thread_id
            return True
            
        if not blocking:
            return False
            
        # Block until acquired
        self._scheduler.yield_control(thread_id)
        
        # Wait to be scheduled again (after lock is granted)
        while True:
            self._scheduler.request_run(thread_id)
            
            # Check if we now own the lock
            acquired = self._scheduler.mutex_lock(thread_id, self._id)
            if acquired:
                self._owner = thread_id
                return True
                
            self._scheduler.yield_control(thread_id)
            
    def release(self):
        """
        Release the mutex.
        
        This is a yield point - the scheduler may switch to a waiting thread.
        """
        if self._scheduler is None:
            raise RuntimeError("DRTMutex.set_scheduler() must be called first")
            
        thread_id = get_current_thread_id()
        if thread_id < 0:
            raise RuntimeError("release() must be called from a DRTThread")
            
        if self._owner != thread_id:
            raise RuntimeError(
                f"Cannot release mutex not owned by current thread "
                f"(owner={self._owner}, current={thread_id})"
            )
            
        self._scheduler.mutex_unlock(thread_id, self._id)
        self._owner = None
        
    def locked(self) -> bool:
        """Check if the mutex is currently held."""
        return self._owner is not None
        
    def __enter__(self):
        """Context manager entry."""
        self.acquire()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.release()
        return False
        
    @property
    def mutex_id(self) -> int:
        """Get the mutex ID."""
        return self._id
        
    def __repr__(self):
        status = "locked" if self.locked() else "unlocked"
        return f"<DRTMutex({self._name}, {status})>"


class DRTCondition:
    """
    A deterministic condition variable.
    
    Integrates with the scheduler to ensure deterministic wait/signal ordering.
    Wait and signal are yield points.
    
    Example:
        mutex = DRTMutex()
        cond = DRTCondition(mutex)
        
        # Thread 1: Wait for condition
        with mutex:
            while not ready:
                cond.wait()
            # Proceed
            
        # Thread 2: Signal condition
        with mutex:
            ready = True
            cond.signal()
    """
    
    # Class-level scheduler reference
    _scheduler: Optional[Scheduler] = None
    
    @classmethod
    def set_scheduler(cls, scheduler: Scheduler):
        """Set the scheduler for all DRTCondition instances."""
        cls._scheduler = scheduler
        
    def __init__(self, lock: DRTMutex = None, name: str = None):
        """
        Initialize a condition variable.
        
        Args:
            lock: Associated mutex (created if not provided)
            name: Optional name for debugging
        """
        self._id = _get_next_sync_id()
        self._name = name or f"Condition-{self._id}"
        self._lock = lock if lock is not None else DRTMutex()
        
    def wait(self, timeout: float = None) -> bool:
        """
        Wait on the condition variable.
        
        Releases the associated mutex and blocks until signaled.
        Reacquires the mutex before returning.
        
        This is a yield point.
        
        Args:
            timeout: Maximum time to wait (currently ignored - deterministic)
            
        Returns:
            True (timeout not yet implemented)
        """
        if self._scheduler is None:
            raise RuntimeError("DRTCondition.set_scheduler() must be called first")
            
        thread_id = get_current_thread_id()
        if thread_id < 0:
            raise RuntimeError("wait() must be called from a DRTThread")
            
        # Must hold the lock
        if self._lock._owner != thread_id:
            raise RuntimeError("wait() requires holding the associated lock")
            
        # Register wait with scheduler (releases mutex)
        self._scheduler.cond_wait(thread_id, self._id, self._lock._id)
        self._lock._owner = None
        
        # Yield control
        self._scheduler.yield_control(thread_id)
        
        # Wait to be woken and reacquire mutex
        while True:
            self._scheduler.request_run(thread_id)
            
            # Try to reacquire the mutex
            acquired = self._scheduler.mutex_lock(thread_id, self._lock._id)
            if acquired:
                self._lock._owner = thread_id
                return True
                
            self._scheduler.yield_control(thread_id)
            
    def wait_for(self, predicate: Callable[[], bool], 
                 timeout: float = None) -> bool:
        """
        Wait until a predicate becomes true.
        
        Args:
            predicate: Callable that returns True when ready
            timeout: Maximum time to wait (currently ignored)
            
        Returns:
            Result of predicate
        """
        while not predicate():
            self.wait(timeout)
        return True
        
    def notify(self, n: int = 1):
        """
        Wake one or more waiting threads.
        
        This is a yield point.
        
        Args:
            n: Number of threads to wake (default 1)
        """
        if self._scheduler is None:
            raise RuntimeError("DRTCondition.set_scheduler() must be called first")
            
        thread_id = get_current_thread_id()
        if thread_id < 0:
            raise RuntimeError("notify() must be called from a DRTThread")
            
        # Must hold the lock
        if self._lock._owner != thread_id:
            raise RuntimeError("notify() requires holding the associated lock")
            
        # Signal waiting threads
        for _ in range(n):
            self._scheduler.cond_signal(thread_id, self._id, self._lock._id)
            
    def notify_all(self):
        """
        Wake all waiting threads.
        
        This is a yield point.
        """
        if self._scheduler is None:
            raise RuntimeError("DRTCondition.set_scheduler() must be called first")
            
        thread_id = get_current_thread_id()
        if thread_id < 0:
            raise RuntimeError("notify_all() must be called from a DRTThread")
            
        # Must hold the lock
        if self._lock._owner != thread_id:
            raise RuntimeError("notify_all() requires holding the associated lock")
            
        self._scheduler.cond_broadcast(thread_id, self._id, self._lock._id)
        
    # Aliases for compatibility
    signal = notify
    broadcast = notify_all
    
    def acquire(self, *args, **kwargs):
        """Acquire the underlying lock."""
        return self._lock.acquire(*args, **kwargs)
        
    def release(self):
        """Release the underlying lock."""
        return self._lock.release()
        
    def __enter__(self):
        """Context manager entry."""
        self._lock.acquire()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self._lock.release()
        return False
        
    @property
    def condition_id(self) -> int:
        """Get the condition variable ID."""
        return self._id
        
    def __repr__(self):
        return f"<DRTCondition({self._name}, lock={self._lock._name})>"


class DRTSemaphore:
    """
    A deterministic semaphore.
    
    Built on top of DRTMutex and DRTCondition for deterministic behavior.
    """
    
    def __init__(self, value: int = 1, name: str = None):
        """
        Initialize a semaphore.
        
        Args:
            value: Initial semaphore value
            name: Optional name for debugging
        """
        self._value = value
        self._name = name or f"Semaphore-{id(self)}"
        self._mutex = DRTMutex(f"{self._name}-mutex")
        self._condition = DRTCondition(self._mutex, f"{self._name}-cond")
        
    def acquire(self, blocking: bool = True) -> bool:
        """
        Acquire the semaphore.
        
        Args:
            blocking: If True, block until acquired
            
        Returns:
            True if acquired, False otherwise
        """
        with self._mutex:
            if not blocking and self._value <= 0:
                return False
                
            while self._value <= 0:
                self._condition.wait()
                
            self._value -= 1
            return True
            
    def release(self):
        """Release the semaphore."""
        with self._mutex:
            self._value += 1
            self._condition.notify()
            
    def __enter__(self):
        self.acquire()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class DRTBarrier:
    """
    A deterministic barrier.
    
    All threads must reach the barrier before any can proceed.
    """
    
    def __init__(self, parties: int, name: str = None):
        """
        Initialize a barrier.
        
        Args:
            parties: Number of threads that must wait
            name: Optional name for debugging
        """
        self._parties = parties
        self._name = name or f"Barrier-{id(self)}"
        self._count = 0
        self._generation = 0
        self._mutex = DRTMutex(f"{self._name}-mutex")
        self._condition = DRTCondition(self._mutex, f"{self._name}-cond")
        
    def wait(self) -> int:
        """
        Wait at the barrier.
        
        Returns:
            Arrival index (0 to parties-1)
        """
        with self._mutex:
            index = self._count
            self._count += 1
            generation = self._generation
            
            if self._count == self._parties:
                # Last to arrive - release everyone
                self._count = 0
                self._generation += 1
                self._condition.notify_all()
            else:
                # Wait for others
                while self._generation == generation:
                    self._condition.wait()
                    
            return index
            
    @property
    def parties(self) -> int:
        """Number of parties required."""
        return self._parties
