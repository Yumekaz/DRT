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
    deserialize_cond_wake_payload,
    deserialize_thread_create_payload,
    deserialize_thread_join_payload,
    serialize_cond_payload,
    serialize_cond_wake_payload,
    serialize_lock_acquire_payload,
    serialize_mutex_payload,
    serialize_thread_create_payload,
    serialize_thread_join_payload,
)
from .log import EventLog
from .exceptions import (
    DeadlockError,
    DivergenceError,
    SchedulerError,
    RuntimeStateError,
)


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
    completed_join_target: Optional[int] = None
    exception: Optional[BaseException] = None
    
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
        self._next_sync_id = 0
        
        # Synchronization state
        self._mutex_owners: Dict[int, int] = {}  # mutex_id -> thread_id
        self._mutex_pending_owners: Dict[int, int] = {}  # mutex_id -> thread_id
        self._mutex_waiters: Dict[int, List[int]] = {}  # mutex_id -> [thread_ids]
        self._cond_waiters: Dict[int, List[int]] = {}  # cond_id -> [thread_ids]
        
        # Logical time
        self._logical_time = 0
        self._replay_index = 0
        
        # Runtime state
        self._lock = threading.Lock()
        self._running = False
        self._shutdown_requested = False
        self._deadlock_error: Optional[DeadlockError] = None
        self._fatal_exception: Optional[BaseException] = None
        self._failing_thread_id: Optional[int] = None
        
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
                creator_id = self._current_thread_id if self._current_thread_id is not None else 0
                entry = self._consume_replay_event_unlocked(
                    EventType.THREAD_CREATE,
                    creator_id,
                )
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

    def report_thread_failure(self, thread_id: int, exc: BaseException):
        """Record an unhandled worker-thread exception and wake the runtime."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread:
                thread.exception = exc

            if self._fatal_exception is None:
                self._fatal_exception = exc
                self._failing_thread_id = thread_id
                self._shutdown_requested = True

                for managed in self._threads.values():
                    managed.run_permission.set()

    def get_thread_exception(self, thread_id: int) -> Optional[BaseException]:
        """Get the stored exception for a managed thread, if any."""
        with self._lock:
            thread = self._threads.get(thread_id)
            return thread.exception if thread else None

    def has_live_threads(self, exclude_thread_ids: Optional[Set[int]] = None) -> bool:
        """Check whether any managed threads are still alive."""
        exclude = exclude_thread_ids or set()
        with self._lock:
            return any(
                tid not in exclude and thread.state != ThreadState.EXITED
                for tid, thread in self._threads.items()
            )

    def has_runnable_threads(
        self, exclude_thread_ids: Optional[Set[int]] = None
    ) -> bool:
        """Check whether any managed threads are runnable."""
        exclude = exclude_thread_ids or set()
        with self._lock:
            return any(tid not in exclude for tid in self._get_runnable_threads())

    def get_native_threads(
        self, exclude_thread_ids: Optional[Set[int]] = None
    ) -> List[threading.Thread]:
        """Return native threads for managed threads that have been started."""
        exclude = exclude_thread_ids or set()
        with self._lock:
            return [
                thread.native_thread
                for tid, thread in self._threads.items()
                if tid not in exclude and thread.native_thread is not None
            ]

    def allocate_sync_id(self) -> int:
        """Allocate a per-runtime synchronization primitive ID."""
        with self._lock:
            sync_id = self._next_sync_id
            self._next_sync_id += 1
            return sync_id

    def thread_has_exited(self, thread_id: int) -> bool:
        """Check whether a managed thread has exited."""
        with self._lock:
            thread = self._threads.get(thread_id)
            return thread is not None and thread.state == ThreadState.EXITED

    def ensure_deadlock_error(
        self, message: str = "No runnable managed threads remain"
    ) -> DeadlockError:
        """Create and cache a deadlock error from current thread state."""
        with self._lock:
            if self._deadlock_error is None:
                self._deadlock_error = DeadlockError(
                    message,
                    self._logical_time,
                    self._format_thread_states_unlocked(),
                )

            return self._deadlock_error

    def raise_pending_error(self):
        """Raise any fatal scheduler error that should abort execution."""
        error = None

        with self._lock:
            if self._fatal_exception is not None:
                error = self._fatal_exception
            elif self._deadlock_error is not None:
                error = self._deadlock_error

        if error is not None:
            raise error

    def _format_thread_states_unlocked(self) -> str:
        """Format managed thread state for diagnostics. Requires self._lock."""
        parts = []

        for tid in sorted(self._threads):
            thread = self._threads[tid]
            status = [thread.state.name]

            if thread.blocked_on_mutex is not None:
                status.append(f"mutex={thread.blocked_on_mutex}")
            if thread.blocked_on_cond is not None:
                status.append(f"cond={thread.blocked_on_cond}")
            if thread.waiting_for_thread is not None:
                status.append(f"join={thread.waiting_for_thread}")
            if thread.exception is not None:
                status.append(f"exc={type(thread.exception).__name__}")

            parts.append(f"{tid}:{'/'.join(status)}")

        return ", ".join(parts)
                
    def thread_exited(self, thread_id: int):
        """Called when a thread exits."""
        with self._lock:
            if (
                self.mode == RuntimeMode.REPLAY
                and self._fatal_exception is None
                and not self._shutdown_requested
            ):
                self._consume_replay_event_unlocked(EventType.THREAD_EXIT, thread_id)

            thread = self._threads.get(thread_id)
            if thread:
                thread.state = ThreadState.EXITED
                thread.run_permission.clear()
                
                # Log the exit
                if self.mode == RuntimeMode.RECORD and self.log.is_recording:
                    self._log_event(EventType.THREAD_EXIT, b'', thread_id)
                    
                # Wake any threads waiting to join
                for t in self._threads.values():
                    if t.waiting_for_thread == thread_id:
                        t.completed_join_target = thread_id
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

        self.raise_pending_error()

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
            else:
                self._deadlock_error = DeadlockError(
                    "No runnable managed threads remain",
                    self._logical_time,
                    self._format_thread_states_unlocked(),
                )
                self._shutdown_requested = True

                for thread in self._threads.values():
                    thread.run_permission.set()
            return
            
        if self.mode == RuntimeMode.REPLAY:
            entry = self._consume_replay_event_unlocked(EventType.SCHEDULE)
            expected_thread = entry.thread_id

            if expected_thread not in runnable:
                thread = self._threads.get(expected_thread)
                state_name = thread.state.name if thread else "UNKNOWN"
                raise DivergenceError(
                    f"Expected scheduled thread {expected_thread} is not runnable",
                    self._logical_time,
                    f"thread {expected_thread} runnable",
                    f"thread {expected_thread} in state {state_name}",
                )

            chosen = expected_thread
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

    def peek_replay_event(self) -> Optional[LogEntry]:
        """Peek at the next replay event without consuming it."""
        with self._lock:
            return self._peek_replay_event_unlocked()

    def consume_replay_event(
        self,
        expected_type: EventType,
        expected_thread_id: Optional[int] = None,
    ) -> LogEntry:
        """Consume the exact next replay event, validating type and thread."""
        with self._lock:
            return self._consume_replay_event_unlocked(expected_type, expected_thread_id)

    def _peek_replay_event_unlocked(self) -> Optional[LogEntry]:
        """Peek at the next replay event. Requires self._lock."""
        if self.mode != RuntimeMode.REPLAY:
            return None

        if self._replay_index >= len(self.log._entries):
            return None

        entry = self.log._entries[self._replay_index]
        if entry.event_type == EventType.LOG_COMPLETE:
            return None

        return entry

    def _consume_replay_event_unlocked(
        self,
        expected_type: EventType,
        expected_thread_id: Optional[int] = None,
    ) -> LogEntry:
        """Consume the next replay event exactly. Requires self._lock."""
        entry = self._peek_replay_event_unlocked()
        expected = self._format_expected_event(expected_type, expected_thread_id)

        if entry is None:
            raise DivergenceError(
                f"Expected {expected_type.name} event but replay log ended",
                self._logical_time,
                expected,
                "end of log",
            )

        actual = self._format_log_entry(entry)

        if entry.logical_time != self._logical_time:
            raise DivergenceError(
                "Replay event appeared at an unexpected logical time",
                self._logical_time,
                f"{expected} at logical time {self._logical_time}",
                f"{actual} at logical time {entry.logical_time}",
            )

        if entry.event_type != expected_type:
            raise DivergenceError(
                f"Expected next replay event to be {expected_type.name}",
                self._logical_time,
                expected,
                actual,
            )

        if expected_thread_id is not None and entry.thread_id != expected_thread_id:
            raise DivergenceError(
                "Replay event came from an unexpected thread",
                self._logical_time,
                expected,
                actual,
            )

        self._replay_index += 1
        return entry

    def _format_expected_event(
        self,
        event_type: EventType,
        thread_id: Optional[int] = None,
    ) -> str:
        """Format an expected replay event for diagnostics."""
        if thread_id is None:
            return event_type.name
        return f"{event_type.name} by thread {thread_id}"

    def _format_log_entry(self, entry: LogEntry) -> str:
        """Format a log entry for divergence diagnostics."""
        return f"{entry.event_type.name} by thread {entry.thread_id}"

    def verify_replay_complete(self):
        """Ensure replay consumed the final LOG_COMPLETE marker and nothing else."""
        if self.mode != RuntimeMode.REPLAY:
            return

        with self._lock:
            if self._replay_index >= len(self.log._entries):
                raise DivergenceError(
                    "Replay reached the end of the log without LOG_COMPLETE",
                    self._logical_time,
                    "LOG_COMPLETE",
                    "end of log",
                )

            entry = self.log._entries[self._replay_index]
            if entry.event_type != EventType.LOG_COMPLETE:
                raise DivergenceError(
                    "Replay finished before the recorded execution was exhausted",
                    self._logical_time,
                    "LOG_COMPLETE",
                    self._format_log_entry(entry),
                )

            self._replay_index += 1

            if self._replay_index != len(self.log._entries):
                extra = self.log._entries[self._replay_index]
                raise DivergenceError(
                    "Replay log has trailing events after LOG_COMPLETE",
                    self._logical_time,
                    "end of log",
                    self._format_log_entry(extra),
                )
        
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

    def _grant_mutex_to_waiter_unlocked(self, mutex_id: int):
        """Grant the mutex to the next waiter without letting another thread steal it."""
        waiters = self._mutex_waiters.get(mutex_id, [])
        if not waiters:
            return

        next_owner = waiters.pop(0)
        self._mutex_pending_owners[mutex_id] = next_owner

        thread = self._threads[next_owner]
        thread.state = ThreadState.RUNNABLE
        thread.blocked_on_mutex = None
        
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
            owner = self._mutex_owners.get(mutex_id)
            pending_owner = self._mutex_pending_owners.get(mutex_id)
            if owner == thread_id:
                raise SchedulerError(
                    f"Reentrant mutex acquisition is not supported for mutex {mutex_id}"
                )

            acquired_immediately = (
                owner is None and (
                    pending_owner is None or pending_owner == thread_id
                )
            )

            payload = serialize_lock_acquire_payload(
                mutex_id,
                blocking=True,
                acquired_immediately=acquired_immediately,
            )

            if self.mode == RuntimeMode.REPLAY:
                entry = self._consume_replay_event_unlocked(
                    EventType.LOCK_ACQUIRE,
                    thread_id,
                )
                if entry.payload != payload:
                    raise DivergenceError(
                        "Replay mutex acquisition did not match the recorded call",
                        self._logical_time,
                        (
                            f"LOCK_ACQUIRE mutex={mutex_id} blocking=True "
                            f"immediate={acquired_immediately}"
                        ),
                        entry.payload.hex(),
                    )
            else:
                self._log_event(EventType.LOCK_ACQUIRE, payload, thread_id)

            if acquired_immediately and pending_owner is None:
                # Mutex is free - acquire it
                self._mutex_owners[mutex_id] = thread_id
                return True
            elif acquired_immediately and pending_owner == thread_id:
                del self._mutex_pending_owners[mutex_id]
                self._mutex_owners[mutex_id] = thread_id
                return True
            else:
                # Blocked - add to waiters
                thread = self._threads[thread_id]
                thread.state = ThreadState.BLOCKED_MUTEX
                thread.blocked_on_mutex = mutex_id
                
                if mutex_id not in self._mutex_waiters:
                    self._mutex_waiters[mutex_id] = []
                if thread_id not in self._mutex_waiters[mutex_id]:
                    self._mutex_waiters[mutex_id].append(thread_id)
                
                return False

    def mutex_try_lock(self, thread_id: int, mutex_id: int) -> bool:
        """
        Attempt to acquire a mutex without mutating blocked/waiter state.

        Returns:
            True if the mutex was acquired, False if another thread owns it.
        """
        with self._lock:
            owner = self._mutex_owners.get(mutex_id)
            pending_owner = self._mutex_pending_owners.get(mutex_id)
            if owner == thread_id:
                raise SchedulerError(
                    f"Reentrant mutex acquisition is not supported for mutex {mutex_id}"
                )

            acquired_immediately = (
                (owner is None and pending_owner is None)
                or (owner is None and pending_owner == thread_id)
            )

            payload = serialize_lock_acquire_payload(
                mutex_id,
                blocking=False,
                acquired_immediately=acquired_immediately,
            )

            if self.mode == RuntimeMode.REPLAY:
                entry = self._consume_replay_event_unlocked(
                    EventType.LOCK_ACQUIRE,
                    thread_id,
                )
                if entry.payload != payload:
                    raise DivergenceError(
                        "Replay mutex try_lock did not match the recorded call",
                        self._logical_time,
                        (
                            f"LOCK_ACQUIRE mutex={mutex_id} blocking=False "
                            f"immediate={acquired_immediately}"
                        ),
                        entry.payload.hex(),
                    )
            else:
                self._log_event(EventType.LOCK_ACQUIRE, payload, thread_id)

            if acquired_immediately:
                if pending_owner == thread_id:
                    del self._mutex_pending_owners[mutex_id]

                self._mutex_owners[mutex_id] = thread_id
                return True

            return False

    def owns_mutex(self, thread_id: int, mutex_id: int) -> bool:
        """Check whether a mutex is currently assigned to a specific thread."""
        with self._lock:
            return self._mutex_owners.get(mutex_id) == thread_id
                
    def mutex_unlock(self, thread_id: int, mutex_id: int):
        """
        Release a mutex.
        
        Args:
            thread_id: ID of the releasing thread
            mutex_id: ID of the mutex
        """
        with self._lock:
            if self.mode == RuntimeMode.REPLAY:
                entry = self._consume_replay_event_unlocked(
                    EventType.LOCK_RELEASE,
                    thread_id,
                )
                expected_payload = serialize_mutex_payload(mutex_id)
                if entry.payload != expected_payload:
                    raise DivergenceError(
                        "Replay mutex release targeted the wrong mutex",
                        self._logical_time,
                        f"LOCK_RELEASE mutex={mutex_id}",
                        entry.payload.hex(),
                    )
            else:
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
            self._grant_mutex_to_waiter_unlocked(mutex_id)
                
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
            if self.mode == RuntimeMode.REPLAY:
                entry = self._consume_replay_event_unlocked(
                    EventType.COND_WAIT,
                    thread_id,
                )
                expected_payload = serialize_cond_payload(cond_id)
                if entry.payload != expected_payload:
                    raise DivergenceError(
                        "Replay condition wait targeted the wrong condition",
                        self._logical_time,
                        f"COND_WAIT cond={cond_id}",
                        entry.payload.hex(),
                    )
            else:
                self._log_event(
                    EventType.COND_WAIT,
                    serialize_cond_payload(cond_id),
                    thread_id
                )
                
            # Release mutex
            if self._mutex_owners.get(mutex_id) == thread_id:
                del self._mutex_owners[mutex_id]
                
                # Wake first mutex waiter
                self._grant_mutex_to_waiter_unlocked(mutex_id)
                    
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
                if self.mode == RuntimeMode.REPLAY:
                    upcoming = self._peek_replay_event_unlocked()
                    if upcoming is not None and upcoming.event_type == EventType.COND_WAKE:
                        target, logged_cond_id = deserialize_cond_wake_payload(upcoming.payload)
                        if upcoming.thread_id == thread_id and logged_cond_id == cond_id:
                            raise DivergenceError(
                                "Replay expected a condition wake, but no waiter is present",
                                self._logical_time,
                                f"COND_WAKE cond={cond_id}",
                                "no waiter available",
                            )
                return None

            if self.mode == RuntimeMode.REPLAY:
                upcoming = self._peek_replay_event_unlocked()
                if upcoming is None or upcoming.event_type != EventType.COND_WAKE:
                    raise DivergenceError(
                        "Replay notify() found waiters but no recorded wake followed",
                        self._logical_time,
                        f"COND_WAKE by thread {thread_id}",
                        "no matching COND_WAKE",
                    )

                entry = self._consume_replay_event_unlocked(
                    EventType.COND_WAKE,
                    thread_id,
                )
                target, logged_cond_id = deserialize_cond_wake_payload(entry.payload)
                if logged_cond_id != cond_id:
                    raise DivergenceError(
                        "Replay condition wake targeted the wrong condition",
                        self._logical_time,
                        f"COND_WAKE cond={cond_id}",
                        f"COND_WAKE cond={logged_cond_id}",
                    )
                if target not in waiters:
                    raise DivergenceError(
                        "Replay condition wake targeted a thread that is not waiting",
                        self._logical_time,
                        f"thread waiting on cond {cond_id}",
                        f"thread {target} not waiting",
                    )
                waiters.remove(target)
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
            if thread_id == target_thread_id:
                raise SchedulerError("Thread cannot join itself")

            thread = self._threads[thread_id]
            target = self._threads.get(target_thread_id)
            completed_from_wait = thread.completed_join_target == target_thread_id

            if target is None or target.state == ThreadState.EXITED:
                expected_immediate = not completed_from_wait

                if self.mode == RuntimeMode.REPLAY:
                    entry = self._consume_replay_event_unlocked(
                        EventType.THREAD_JOIN,
                        thread_id,
                    )
                    logged_target_thread_id, logged_immediate = (
                        deserialize_thread_join_payload(entry.payload)
                    )
                    if logged_target_thread_id != target_thread_id:
                        raise DivergenceError(
                            "Replay join targeted the wrong thread",
                            self._logical_time,
                            f"THREAD_JOIN target={target_thread_id}",
                            f"THREAD_JOIN target={logged_target_thread_id}",
                        )
                    if logged_immediate != expected_immediate:
                        raise DivergenceError(
                            "Replay join completion mode does not match runtime state",
                            self._logical_time,
                            f"THREAD_JOIN immediate={expected_immediate}",
                            f"THREAD_JOIN immediate={logged_immediate}",
                        )
                else:
                    self._log_event(
                        EventType.THREAD_JOIN,
                        serialize_thread_join_payload(
                            target_thread_id,
                            expected_immediate,
                        ),
                        thread_id,
                    )

                thread.completed_join_target = None
                return True

            if self.mode == RuntimeMode.REPLAY:
                upcoming = self._peek_replay_event_unlocked()
                if (
                    upcoming is not None
                    and upcoming.event_type == EventType.THREAD_JOIN
                    and upcoming.thread_id == thread_id
                ):
                    logged_target_thread_id, _ = deserialize_thread_join_payload(
                        upcoming.payload
                    )
                    if logged_target_thread_id == target_thread_id:
                        raise DivergenceError(
                            "Replay join completed before the target thread exited",
                            self._logical_time,
                            f"thread {target_thread_id} still running",
                            f"THREAD_JOIN target={target_thread_id}",
                        )

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
