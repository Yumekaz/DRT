"""
DRT Exceptions - Custom exception types for the Deterministic Runtime.

All exceptions that indicate system-level failures are defined here.
"""


class DRTError(Exception):
    """Base exception for all DRT errors."""
    pass


class DivergenceError(DRTError):
    """
    Raised when replay execution diverges from recorded execution.
    
    This indicates that the runtime could not continue replay while staying
    consistent with the recorded execution.
    """
    def __init__(self, message: str, logical_time: int = -1, 
                 expected: str = "", actual: str = ""):
        self.logical_time = logical_time
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Divergence at logical time {logical_time}: {message}\n"
            f"  Expected: {expected}\n"
            f"  Actual: {actual}"
        )


class LogCorruptionError(DRTError):
    """
    Raised when the event log is corrupted or incomplete.
    
    This can occur if:
    - The log file was truncated
    - The log was not properly finalized
    - The log format is invalid
    """
    pass


class LogIntegrityError(LogCorruptionError):
    """Raised when log integrity metadata does not match the recorded body."""
    pass


class IncompleteLogError(LogCorruptionError):
    """Raised when the log lacks LOG_COMPLETE marker."""
    pass


class RuntimeStateError(DRTError):
    """Raised when the runtime is in an invalid state for an operation."""
    pass


class ThreadStateError(DRTError):
    """Raised when a thread is in an invalid state."""
    pass


class UnloggedNondeterminismError(DRTError):
    """
    Raised when nondeterministic input bypasses the runtime.
    
    This is a fatal error - the isolation invariant has been violated.
    """
    pass


class SchedulerError(DRTError):
    """Raised when the scheduler encounters an unrecoverable error."""
    pass


class DeadlockError(DRTError):
    """
    Raised when the runtime detects a deadlock.

    Attributes:
        logical_time: Scheduler logical time at detection
        thread_states: Human-readable summary of managed thread states
    """

    def __init__(self, message: str, logical_time: int = -1,
                 thread_states: str = ""):
        self.logical_time = logical_time
        self.thread_states = thread_states

        details = (
            f"Deadlock at logical time {logical_time}: {message}"
        )
        if thread_states:
            details += f"\n  Thread states: {thread_states}"

        super().__init__(details)
