"""
DRT Thread - Managed thread abstraction.

Provides DRTThread, a drop-in replacement for threading.Thread that
integrates with the deterministic scheduler.

Users must use DRTThread instead of threading.Thread to ensure
deterministic execution.
"""

import threading
import time
from typing import Callable, Any, Optional, Tuple

from .context import (
    bind_runtime_context,
    capture_runtime_context,
    clear_runtime_context,
    get_current_scheduler,
)
from .scheduler import Scheduler, ThreadState
from .exceptions import RuntimeStateError


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


def clear_current_thread_id():
    """Clear the DRT thread ID for the current thread."""
    if hasattr(_thread_local, 'thread_id'):
        delattr(_thread_local, 'thread_id')


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
    
    # Legacy fallback for callers that still use the old global setter.
    _default_scheduler: Optional[Scheduler] = None
    
    @classmethod
    def set_scheduler(cls, scheduler: Scheduler):
        """Set a legacy fallback scheduler for DRTThread instances."""
        cls._default_scheduler = scheduler
        
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
        scheduler = get_current_scheduler() or self._default_scheduler
        if scheduler is None:
            raise RuntimeError(
                "DRTThread.set_scheduler() must be called before creating threads"
            )

        self._scheduler = scheduler
        self._runtime_context = capture_runtime_context()
             
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
        bind_runtime_context(*self._runtime_context)

        # Set thread-local ID
        set_current_thread_id(self._thread_id)
        
        # Notify scheduler that thread has started
        self._scheduler.thread_started(self._thread_id)
        
        try:
            # Request initial permission to run
            self._scheduler.request_run(self._thread_id)
            
            if self._scheduler.is_running and self._target:
                self._result = self._target(*self._args, **self._kwargs)
        except BaseException as e:
            self._exception = e
            self._scheduler.report_thread_failure(self._thread_id, e)
                    
        finally:
            self._exited = True
            try:
                self._scheduler.thread_exited(self._thread_id)
            except BaseException as e:
                if self._exception is None:
                    self._exception = e
                    self._scheduler.report_thread_failure(self._thread_id, e)
            finally:
                self._exit_event.set()
                clear_current_thread_id()
                clear_runtime_context()
             
    def join(self, timeout: float = None):
        """
        Wait for the thread to complete.
        
        Args:
            timeout: Maximum time to wait (currently uses logical time)
        """
        if not self._started:
            raise RuntimeError("Thread not started")
             
        current_id = get_current_thread_id()
        if current_id >= 0:
            if timeout is not None:
                raise RuntimeError(
                    "join(timeout=...) is not supported for managed DRT threads"
                )
            if current_id == self._thread_id:
                raise RuntimeError("Thread cannot join itself")

            joined = self._scheduler.thread_join(current_id, self._thread_id)
            while not joined and not self._exited:
                self._scheduler.yield_control(current_id)
                self._scheduler.request_run(current_id)

                if not self._exited:
                    self._scheduler.raise_pending_error()
        else:
            self._native_thread.join(timeout=timeout)

            if timeout is not None and self._native_thread.is_alive():
                return

        if not self._exited:
            self._scheduler.raise_pending_error()
            raise RuntimeStateError(
                f"Thread {self._thread_id} did not reach EXITED state during join"
            )

        if self._exception is not None:
            raise self._exception

        thread_exception = self._scheduler.get_thread_exception(self._thread_id)
        if thread_exception is not None:
            raise thread_exception
                
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
        
    scheduler = get_current_scheduler() or DRTThread._default_scheduler
    if scheduler is None or not scheduler.is_running:
        return
        
    # Log the yield point (in record mode)
    scheduler.schedule_explicit_yield(thread_id)
    
    # Yield control to scheduler
    scheduler.yield_control(thread_id)
    
    # Wait to be scheduled again
    scheduler.request_run(thread_id)
