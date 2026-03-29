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
    
    This indicates that the replayed execution cannot match the recorded
    execution - the system guarantee has been violated.
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
