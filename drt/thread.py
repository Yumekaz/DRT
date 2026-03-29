"""
DRT Thread - Managed thread abstraction.

Provides DRTThread, a drop-in replacement for threading.Thread that
integrates with the deterministic scheduler.

Users must use DRTThread instead of threading.Thread to ensure
deterministic execution.
"""

import threading
from typing import Callable, Any, Optional, Tuple
from functools import wraps

from .scheduler import Scheduler, ThreadState


# Thread-local storage for current thread ID
_thread_local = threading.local()


def get_current_thread_id() -> int:
    """
    Get the DRT thread ID of the current thread.
    
    Returns:
        Thread ID, or -1 if not a managed thread
    """
    return getattr(_thread_local, 'thread_id', -1)


def set_current_thread_id(thread_id: int):
    """Set the DRT thread ID for the current thread."""
    _thread_local.thread_id = thread_id


class DRTThread:
    """
    A managed thread that integrates with the deterministic scheduler.
    
    Provides a similar API to threading.Thread but ensures all execution
    is controlled by the scheduler.
    
    Example:
        def worker():
            print("Working")
            
        t = DRTThread(target=worker)
        t.start()
        t.join()
    """
    
    # Class-level scheduler reference (set by runtime)
    _scheduler: Optional[Scheduler] = None
    
    @classmethod
    def set_scheduler(cls, scheduler: Scheduler):
        """Set the scheduler for all DRTThread instances."""
        cls._scheduler = scheduler
        
    def __init__(self, target: Callable[..., Any] = None, 
                 args: Tuple = (), kwargs: dict = None,
                 name: str = None, daemon: bool = False):
        """
        Initialize a managed thread.
        
        Args:
            target: Callable to run in the thread
            args: Arguments for target
            kwargs: Keyword arguments for target
            name: Thread name (optional)
            daemon: Whether thread is daemon (currently ignored)
        """
        if self._scheduler is None:
            raise RuntimeError(
                "DRTThread.set_scheduler() must be called before creating threads"
            )
            
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._name = name
        self._daemon = daemon
        
        self._thread_id: Optional[int] = None
        self._native_thread: Optional[threading.Thread] = None
        self._started = False
        self._exited = False
        self._result: Any = None
        self._exception: Optional[Exception] = None
        
        # Event for join synchronization
        self._exit_event = threading.Event()
        
    def start(self):
        """
        Start the thread.
        
        The thread will not execute until the scheduler grants permission.
        """
        if self._started:
            raise RuntimeError("Thread already started")
            
        # Create native thread
        self._native_thread = threading.Thread(
            target=self._thread_entry,
            name=self._name,
            daemon=self._daemon
        )
        
        # Register with scheduler
        self._thread_id = self._scheduler.create_thread(self._native_thread)
        
        # Start native thread
        self._native_thread.start()
        self._started = True
        
        # Yield to let the new thread get scheduled
        current_id = get_current_thread_id()
        if current_id >= 0:
            self._scheduler.yield_control(current_id)
            self._scheduler.request_run(current_id)
        
    def _thread_entry(self):
        """
        Entry point for the native thread.
        
        Implements the thread execution loop with scheduler coordination.
        """
        # Set thread-local ID
        set_current_thread_id(self._thread_id)
        
        # Notify scheduler that thread has started
        self._scheduler.thread_started(self._thread_id)
        
        try:
            # Request initial permission to run
            self._scheduler.request_run(self._thread_id)
            
            if self._scheduler.is_running and self._target:
                try:
                    self._result = self._target(*self._args, **self._kwargs)
                except Exception as e:
                    self._exception = e
                    
        finally:
            self._exited = True
            self._scheduler.thread_exited(self._thread_id)
            self._exit_event.set()
            
    def join(self, timeout: float = None):
        """
        Wait for the thread to complete.
        
        Args:
            timeout: Maximum time to wait (currently uses logical time)
        """
        if not self._started:
            raise RuntimeError("Thread not started")
            
        current_id = get_current_thread_id()
        
        # Keep yielding until the target thread exits
        while not self._exited and self._scheduler.is_running:
            if current_id >= 0:
                self._scheduler.yield_control(current_id)
                self._scheduler.request_run(current_id)
            else:
                # Not a managed thread, just wait
                self._native_thread.join(timeout=0.01)
                
    @property
    def thread_id(self) -> int:
        """Get the DRT thread ID."""
        return self._thread_id
        
    @property
    def is_alive(self) -> bool:
        """Check if the thread is still running."""
        return self._started and not self._exited
        
    @property
    def name(self) -> str:
        """Get the thread name."""
        return self._name or f"DRTThread-{self._thread_id}"
        
    @name.setter
    def name(self, value: str):
        """Set the thread name."""
        self._name = value
        if self._native_thread:
            self._native_thread.name = value
            
    def __repr__(self):
        status = "not started"
        if self._started:
            status = "alive" if self.is_alive else "exited"
        return f"<DRTThread({self.name}, {status})>"


def runtime_yield():
    """
    Explicit yield point.
    
    Call this to give other threads a chance to run.
    Must be called from within a DRTThread.
    """
    thread_id = get_current_thread_id()
    if thread_id < 0:
        return  # Not a managed thread
        
    scheduler = DRTThread._scheduler
    if scheduler is None or not scheduler.is_running:
        return
        
    # Log the yield point (in record mode)
    scheduler.schedule_explicit_yield(thread_id)
    
    # Yield control to scheduler
    scheduler.yield_control(thread_id)
    
    # Wait to be scheduled again
    scheduler.request_run(thread_id)
