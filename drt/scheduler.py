"""
DRT Scheduler - Deterministic thread scheduler.

The scheduler controls which thread is allowed to execute at any time.
It enforces the yield point model: threads can only yield control at
specific points (lock acquire, lock release, condition wait, etc.).

In RECORD mode:
    - Scheduler chooses threads deterministically
    - All decisions are logged
    
In REPLAY mode:
    - Scheduler follows the log exactly
    - Any divergence is detected and reported
"""

import threading
from typing import Dict, Set, Optional, List, Callable
from enum import Enum, auto
from dataclasses import dataclass, field

from .events import (
    LogEntry, EventType,
    serialize_mutex_payload, serialize_cond_payload,
    serialize_cond_wake_payload, serialize_thread_create_payload,
    deserialize_cond_wake_payload
)
from .log import EventLog
from .exceptions import DivergenceError, SchedulerError, RuntimeStateError


class RuntimeMode(Enum):
    """Operating mode of the runtime."""
    RECORD = auto()
    REPLAY = auto()


class ThreadState(Enum):
    """State of a managed thread."""
    CREATED = auto()      # Thread object created, not yet started
    RUNNABLE = auto()     # Ready to run
    RUNNING = auto()      # Currently executing
    BLOCKED_MUTEX = auto()  # Waiting to acquire mutex
    BLOCKED_COND = auto()   # Waiting on condition variable
    BLOCKED_JOIN = auto()   # Waiting for another thread to exit
    EXITED = auto()       # Thread has terminated


@dataclass
class ManagedThread:
    """
    Internal representation of a managed thread.
    
    Each thread has a run_permission event that controls when it can execute.
    """
    thread_id: int
    state: ThreadState = ThreadState.CREATED
    run_permission: threading.Event = field(default_factory=threading.Event)
    native_thread: Optional[threading.Thread] = None
    blocked_on_mutex: Optional[int] = None
    blocked_on_cond: Optional[int] = None
    waiting_for_thread: Optional[int] = None
    
    def __hash__(self):
        return hash(self.thread_id)


class Scheduler:
    """
    Deterministic thread scheduler.
    
    Controls thread execution using run_permission events.
    Threads must request permission to run and yield control explicitly.
    """
    
    def __init__(self, mode: RuntimeMode, log: EventLog):
        """
        Initialize the scheduler.
        
        Args:
            mode: RECORD or REPLAY mode
            log: Event log for recording or replay
        """
        self.mode = mode
        self.log = log
        
        # Thread management
        self._threads: Dict[int, ManagedThread] = {}
        self._next_thread_id = 0
        self._current_thread_id: Optional[int] = None
        
        # Synchronization state
        self._mutex_owners: Dict[int, int] = {}  # mutex_id -> thread_id
        self._mutex_waiters: Dict[int, List[int]] = {}  # mutex_id -> [thread_ids]
        self._cond_waiters: Dict[int, List[int]] = {}  # cond_id -> [thread_ids]
        
        # Logical time
        self._logical_time = 0
        self._replay_index = 0
        
        # Runtime state
        self._lock = threading.Lock()
        self._running = False
        self._shutdown_requested = False
        
        # Main thread registration
        self._main_thread_registered = False
        
    def register_main_thread(self) -> int:
        """
        Register the main thread with the scheduler.
        
        Must be called before any other threads are created.
        
        Returns:
            Thread ID for the main thread (always 0)
        """
        with self._lock:
            if self._main_thread_registered:
                raise RuntimeStateError("Main thread already registered")
                
            main_thread = ManagedThread(
                thread_id=0,
                state=ThreadState.RUNNING,
                native_thread=threading.current_thread()
            )
            main_thread.run_permission.set()  # Main thread starts with permission
            
            self._threads[0] = main_thread
            self._current_thread_id = 0
            self._next_thread_id = 1
            self._main_thread_registered = True
            self._running = True
            
            return 0
            
    def create_thread(self, native_thread: threading.Thread) -> int:
        """
        Register a new thread with the scheduler.
        
        Args:
            native_thread: The native Python thread
            
        Returns:
            Assigned thread ID
        """
        with self._lock:
            if self.mode == RuntimeMode.REPLAY:
                # In replay, verify against log
                entry = self._get_next_replay_entry()
                if entry is None or entry.event_type != EventType.THREAD_CREATE:
                    raise DivergenceError(
                        "Thread creation not expected",
                        self._logical_time,
                        "no THREAD_CREATE event",
                        "THREAD_CREATE"
                    )
                from .events import deserialize_thread_create_payload
                expected_id = deserialize_thread_create_payload(entry.payload)
                thread_id = expected_id
            else:
                thread_id = self._next_thread_id
                self._next_thread_id += 1
                
            managed = ManagedThread(
                thread_id=thread_id,
                state=ThreadState.CREATED,
                native_thread=native_thread
            )
            self._threads[thread_id] = managed
            
            if self.mode == RuntimeMode.RECORD:
                self._log_event(
                    EventType.THREAD_CREATE,
                    serialize_thread_create_payload(thread_id)
                )
                
            return thread_id
            
    def thread_started(self, thread_id: int):
        """Called when a thread begins execution."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread:
                thread.state = ThreadState.RUNNABLE
                
    def thread_exited(self, thread_id: int):
        """Called when a thread exits."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread:
                thread.state = ThreadState.EXITED
                thread.run_permission.clear()
                
                # Log the exit
                if self.mode == RuntimeMode.RECORD:
                    self._log_event(EventType.THREAD_EXIT, b'', thread_id)
                    
                # Wake any threads waiting to join
                for t in self._threads.values():
                    if t.waiting_for_thread == thread_id:
                        t.waiting_for_thread = None
                        t.state = ThreadState.RUNNABLE
                        
                # Schedule next thread
                self._schedule_next()
                        
    def request_run(self, thread_id: int):
        """
        Request permission to run.
        
        Blocks until the scheduler grants permission.
        
        Args:
            thread_id: ID of the requesting thread
        """
        thread = self._threads.get(thread_id)
        if not thread:
            raise SchedulerError(f"Unknown thread: {thread_id}")
            
        # Wait for permission
        thread.run_permission.wait()
        
        with self._lock:
            self._current_thread_id = thread_id
            thread.state = ThreadState.RUNNING
            
    def yield_control(self, thread_id: int):
        """
        Yield control back to the scheduler.
        
        The thread will block until scheduled again.
        
        Args:
            thread_id: ID of the yielding thread
        """
        with self._lock:
            thread = self._threads.get(thread_id)
            if not thread:
                return
                
            # Clear permission
            thread.run_permission.clear()
            
            if thread.state == ThreadState.RUNNING:
                thread.state = ThreadState.RUNNABLE
                
            # Don't schedule next if thread has exited
            if thread.state != ThreadState.EXITED:
                # Schedule next thread
                self._schedule_next()
            
    def schedule_explicit_yield(self, thread_id: int):
        """Handle explicit yield point (runtime_yield)."""
        # No logging here - the SCHEDULE event is logged in _schedule_next
        # when the next thread is chosen
        pass
                
    def _schedule_next(self):
        """
        Choose and schedule the next thread.
        
        Must be called while holding self._lock.
        """
        if self._shutdown_requested:
            return
            
        runnable = self._get_runnable_threads()
        
        if not runnable:
            # Check if all threads have exited
            all_exited = all(
                t.state == ThreadState.EXITED 
                for t in self._threads.values()
            )
            if all_exited:
                self._shutdown_requested = True
            return
            
        if self.mode == RuntimeMode.REPLAY:
            # Follow the log - find next SCHEDULE entry
            entry = self._peek_next_schedule_entry()
            if entry is None:
                # Log exhausted - replay complete
                self._shutdown_requested = True
                # Grant permission to main thread to complete
                if 0 in runnable:
                    self._threads[0].run_permission.set()
                return
                
            expected_thread = entry.thread_id
            self._consume_schedule_entry()
            
            if expected_thread in runnable:
                chosen = expected_thread
            elif expected_thread in self._threads and self._threads[expected_thread].state == ThreadState.EXITED:
                # Thread already exited, pick any runnable thread
                chosen = min(runnable)
            else:
                # Divergence - expected thread should be runnable
                thread = self._threads.get(expected_thread)
                state_name = thread.state.name if thread else "UNKNOWN"
                raise DivergenceError(
                    f"Expected thread {expected_thread} is not runnable",
                    self._logical_time,
                    f"thread {expected_thread} runnable",
                    f"thread {expected_thread} in state {state_name}"
                )
        else:
            # RECORD mode: deterministic choice
            # Round-robin policy: pick next thread after current
            if self._current_thread_id is not None and len(runnable) > 1:
                # Find next thread in round-robin order
                sorted_runnable = sorted(runnable)
                try:
                    current_idx = sorted_runnable.index(self._current_thread_id)
                    next_idx = (current_idx + 1) % len(sorted_runnable)
                    chosen = sorted_runnable[next_idx]
                except ValueError:
                    # Current thread not in runnable, pick lowest
                    chosen = min(runnable)
            else:
                chosen = min(runnable)
            self._log_event(EventType.SCHEDULE, b'', chosen)
            
        self._logical_time += 1
        
        # Grant permission to chosen thread
        thread = self._threads[chosen]
        thread.run_permission.set()
        
    def _get_runnable_threads(self) -> Set[int]:
        """
        Get the set of runnable thread IDs.
        
        A thread is runnable iff:
        - It has not exited
        - It is not blocked on a mutex
        - It is not waiting on a condition variable
        - It is not waiting for another thread to join
        """
        runnable = set()
        for tid, thread in self._threads.items():
            if thread.state in (ThreadState.RUNNABLE, ThreadState.RUNNING):
                if (thread.blocked_on_mutex is None and 
                    thread.blocked_on_cond is None and
                    thread.waiting_for_thread is None):
                    runnable.add(tid)
        return runnable
        
    def _peek_next_schedule_entry(self) -> Optional[LogEntry]:
        """Peek at the next SCHEDULE entry without consuming it."""
        idx = self._replay_index
        while idx < len(self.log._entries):
            entry = self.log._entries[idx]
            if entry.event_type == EventType.LOG_COMPLETE:
                return None
            if entry.event_type == EventType.SCHEDULE:
                return entry
            idx += 1
        return None
        
    def _consume_schedule_entry(self):
        """Consume the next SCHEDULE entry."""
        while self._replay_index < len(self.log._entries):
            entry = self.log._entries[self._replay_index]
            self._replay_index += 1
            if entry.event_type == EventType.SCHEDULE:
                return
            if entry.event_type == EventType.LOG_COMPLETE:
                return
        
    def _get_next_replay_entry(self) -> Optional[LogEntry]:
        """Get and consume the next replay entry."""
        if self._replay_index < len(self.log._entries):
            entry = self.log._entries[self._replay_index]
            if entry.event_type != EventType.LOG_COMPLETE:
                self._replay_index += 1
                return entry
        return None
        
    def _log_event(self, event_type: EventType, payload: bytes, 
                   thread_id: Optional[int] = None):
        """Log an event during recording."""
        if self.mode != RuntimeMode.RECORD:
            return
            
        tid = thread_id if thread_id is not None else self._current_thread_id or 0
        entry = LogEntry(
            logical_time=self._logical_time,
            thread_id=tid,
            event_type=event_type,
            payload=payload
        )
        self.log.append(entry)
        
    # Mutex operations
    
    def mutex_lock(self, thread_id: int, mutex_id: int) -> bool:
        """
        Attempt to acquire a mutex.
        
        Args:
            thread_id: ID of the requesting thread
            mutex_id: ID of the mutex
            
        Returns:
            True if acquired, False if blocked
        """
        with self._lock:
            if self.mode == RuntimeMode.RECORD:
                self._log_event(
                    EventType.LOCK_ACQUIRE,
                    serialize_mutex_payload(mutex_id),
                    thread_id
                )
                
            owner = self._mutex_owners.get(mutex_id)
            
            if owner is None:
                # Mutex is free - acquire it
                self._mutex_owners[mutex_id] = thread_id
                return True
            elif owner == thread_id:
                # Already own it (reentrant - not supported, but don't block)
                return True
            else:
                # Blocked - add to waiters
                thread = self._threads[thread_id]
                thread.state = ThreadState.BLOCKED_MUTEX
                thread.blocked_on_mutex = mutex_id
                
                if mutex_id not in self._mutex_waiters:
                    self._mutex_waiters[mutex_id] = []
                self._mutex_waiters[mutex_id].append(thread_id)
                
                return False
                
    def mutex_unlock(self, thread_id: int, mutex_id: int):
        """
        Release a mutex.
        
        Args:
            thread_id: ID of the releasing thread
            mutex_id: ID of the mutex
        """
        with self._lock:
            if self.mode == RuntimeMode.RECORD:
                self._log_event(
                    EventType.LOCK_RELEASE,
                    serialize_mutex_payload(mutex_id),
                    thread_id
                )
                
            owner = self._mutex_owners.get(mutex_id)
            if owner != thread_id:
                raise SchedulerError(
                    f"Thread {thread_id} cannot unlock mutex {mutex_id} "
                    f"owned by {owner}"
                )
                
            # Release the mutex
            del self._mutex_owners[mutex_id]
            
            # Wake first waiter (FIFO)
            waiters = self._mutex_waiters.get(mutex_id, [])
            if waiters:
                next_owner = waiters.pop(0)
                self._mutex_owners[mutex_id] = next_owner
                
                thread = self._threads[next_owner]
                thread.state = ThreadState.RUNNABLE
                thread.blocked_on_mutex = None
                
    # Condition variable operations
    
    def cond_wait(self, thread_id: int, cond_id: int, mutex_id: int):
        """
        Wait on a condition variable.
        
        Releases the mutex and blocks.
        
        Args:
            thread_id: ID of the waiting thread
            cond_id: ID of the condition variable
            mutex_id: ID of the associated mutex
        """
        with self._lock:
            if self.mode == RuntimeMode.RECORD:
                self._log_event(
                    EventType.COND_WAIT,
                    serialize_cond_payload(cond_id),
                    thread_id
                )
                
            # Release mutex
            if self._mutex_owners.get(mutex_id) == thread_id:
                del self._mutex_owners[mutex_id]
                
                # Wake first mutex waiter
                waiters = self._mutex_waiters.get(mutex_id, [])
                if waiters:
                    next_owner = waiters.pop(0)
                    self._mutex_owners[mutex_id] = next_owner
                    next_thread = self._threads[next_owner]
                    next_thread.state = ThreadState.RUNNABLE
                    next_thread.blocked_on_mutex = None
                    
            # Block on condition
            thread = self._threads[thread_id]
            thread.state = ThreadState.BLOCKED_COND
            thread.blocked_on_cond = cond_id
            
            if cond_id not in self._cond_waiters:
                self._cond_waiters[cond_id] = []
            self._cond_waiters[cond_id].append(thread_id)
            
    def cond_signal(self, thread_id: int, cond_id: int, mutex_id: int) -> Optional[int]:
        """
        Signal a condition variable.
        
        Wakes one waiting thread.
        
        Args:
            thread_id: ID of the signaling thread
            cond_id: ID of the condition variable
            mutex_id: ID of the associated mutex
            
        Returns:
            ID of woken thread, or None
        """
        with self._lock:
            waiters = self._cond_waiters.get(cond_id, [])
            
            if not waiters:
                return None
                
            if self.mode == RuntimeMode.REPLAY:
                # In replay, we need to wake the thread specified in the log
                # For now, just wake first waiter (log determines order)
                target = waiters.pop(0)
            else:
                # RECORD mode: wake first waiter
                target = waiters.pop(0)
                
            if self.mode == RuntimeMode.RECORD:
                self._log_event(
                    EventType.COND_WAKE,
                    serialize_cond_wake_payload(target, cond_id),
                    thread_id
                )
                
            # Wake the target thread
            target_thread = self._threads[target]
            target_thread.state = ThreadState.BLOCKED_MUTEX  # Needs to reacquire mutex
            target_thread.blocked_on_cond = None
            target_thread.blocked_on_mutex = mutex_id
            
            # Add to mutex waiters
            if mutex_id not in self._mutex_waiters:
                self._mutex_waiters[mutex_id] = []
            self._mutex_waiters[mutex_id].append(target)
            
            return target
            
    def cond_broadcast(self, thread_id: int, cond_id: int, mutex_id: int) -> List[int]:
        """Signal all waiters on a condition variable."""
        woken = []
        while True:
            target = self.cond_signal(thread_id, cond_id, mutex_id)
            if target is None:
                break
            woken.append(target)
        return woken
        
    # Thread join
    
    def thread_join(self, thread_id: int, target_thread_id: int) -> bool:
        """
        Wait for another thread to exit.
        
        Args:
            thread_id: ID of the waiting thread
            target_thread_id: ID of thread to wait for
            
        Returns:
            True if target already exited, False if must wait
        """
        with self._lock:
            if self.mode == RuntimeMode.RECORD:
                self._log_event(EventType.THREAD_JOIN, b'', thread_id)
                
            target = self._threads.get(target_thread_id)
            if target is None or target.state == ThreadState.EXITED:
                return True
                
            # Block waiting for target
            thread = self._threads[thread_id]
            thread.state = ThreadState.BLOCKED_JOIN
            thread.waiting_for_thread = target_thread_id
            
            return False
            
    # Properties
    
    @property
    def logical_time(self) -> int:
        """Current logical time."""
        return self._logical_time
        
    @property
    def is_running(self) -> bool:
        """Whether the scheduler is running."""
        return self._running and not self._shutdown_requested
        
    def shutdown(self):
        """Request scheduler shutdown."""
        with self._lock:
            self._shutdown_requested = True
            # Wake all threads so they can exit
            for thread in self._threads.values():
                thread.run_permission.set()
