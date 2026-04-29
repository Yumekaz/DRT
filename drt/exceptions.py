"""
DRT Exceptions - Custom exception types for the Deterministic Runtime.

All exceptions that indicate system-level failures are defined here.
"""

from typing import Iterable, Optional


class DRTError(Exception):
    """Base exception for all DRT errors."""
    pass


class DivergenceError(DRTError):
    """
    Raised when replay execution diverges from recorded execution.
    
    This indicates that the runtime could not continue replay while staying
    consistent with the recorded execution.
    """
    def __init__(
        self,
        message: str,
        logical_time: int = -1,
        expected: str = "",
        actual: str = "",
        event_index: Optional[int] = None,
    ):
        self.message = message
        self.logical_time = logical_time
        self.expected = expected
        self.actual = actual
        self.event_index = event_index
        super().__init__(
            _format_divergence_message(
                message,
                logical_time,
                expected,
                actual,
                event_index,
            )
        )


def format_replay_failure(
    error: DivergenceError,
    *,
    source_changed: Optional[bool] = None,
    source_drifts: Iterable[object] = (),
) -> str:
    """Return a user-facing replay divergence report."""

    location = (
        f"event {error.event_index}"
        if error.event_index is not None
        else f"logical time {_display_value(error.logical_time)}"
    )
    lines = [
        f"Diverged at {location}",
        f"reason: {_display_value(error.message)}",
        f"logical time: {_display_value(error.logical_time)}",
        f"expected: {_display_value(error.expected)}",
        f"actual:   {_display_value(error.actual)}",
    ]

    if source_changed is not None:
        lines.append(f"source changed: {'yes' if source_changed else 'no'}")
        for drift in source_drifts:
            status = _display_value(getattr(drift, "status", "unknown"))
            path = _display_value(getattr(drift, "path", "unknown"))
            lines.append(f"  {status}: {path}")

    return "\n".join(lines)


def _format_divergence_message(
    message: str,
    logical_time: int,
    expected: str,
    actual: str,
    event_index: Optional[int],
) -> str:
    location = (
        f"event {event_index}"
        if event_index is not None
        else f"logical time {logical_time}"
    )
    lines = [
        f"Diverged at {location}",
        f"message: {message}",
    ]
    if event_index is not None:
        lines.append(f"logical time: {logical_time}")
    lines.extend(
        [
            f"expected: {expected}",
            f"actual:   {actual}",
        ]
    )
    return "\n".join(lines)


def _display_value(value: object) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


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
