"""
DRT runtime context.

Tracks the active runtime bindings on each native thread so DRT objects
do not depend on process-global scheduler/interceptor state.
"""

import threading
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .scheduler import Scheduler


@dataclass(frozen=True)
class RuntimeContext:
    """Bindings shared by all managed threads in a single runtime instance."""

    scheduler: "Scheduler"
    interceptor: Any


_context_local = threading.local()


def set_current_runtime(context: RuntimeContext):
    """Bind a runtime context to the current native thread."""
    _context_local.runtime = context


def get_current_runtime(optional: bool = False) -> Optional[RuntimeContext]:
    """Return the runtime context bound to the current native thread."""
    context = getattr(_context_local, "runtime", None)
    if context is None and not optional:
        raise RuntimeError(
            "No active DRT runtime on this thread. "
            "Use drt.runtime.DRTRuntime to run your program."
        )
    return context


def clear_current_runtime():
    """Remove any runtime context bound to the current native thread."""
    if hasattr(_context_local, "runtime"):
        delattr(_context_local, "runtime")


def get_current_scheduler(optional: bool = False):
    """Return the scheduler for the active runtime context."""
    context = get_current_runtime(optional=optional)
    return None if context is None else context.scheduler


def get_current_interceptor(optional: bool = False):
    """Return the interceptor for the active runtime context."""
    context = get_current_runtime(optional=optional)
    return None if context is None else context.interceptor


def bind_runtime_context(scheduler: Any, interceptor: Any):
    """Compatibility wrapper used by the runtime and managed threads."""
    set_current_runtime(RuntimeContext(scheduler=scheduler, interceptor=interceptor))


def clear_runtime_context():
    """Compatibility wrapper that clears the active runtime context."""
    clear_current_runtime()


def capture_runtime_context():
    """Capture the scheduler/interceptor pair bound to the current thread."""
    context = get_current_runtime(optional=True)
    if context is None:
        return None, None
    return context.scheduler, context.interceptor
