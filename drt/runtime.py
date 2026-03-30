"""
DRT Runtime - Main runtime controller.

The DRTRuntime class is the entry point for deterministic record and replay.
It orchestrates all components: scheduler, log, threads, and interceptors.

Usage:
    # Record execution
    runtime = DRTRuntime(mode='record', log_path='execution.log')
    runtime.run(my_program)
    
    # Replay execution
    runtime = DRTRuntime(mode='replay', log_path='execution.log')
    runtime.run(my_program)
"""

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Any, Optional

from .context import bind_runtime_context, clear_runtime_context
from .scheduler import Scheduler, RuntimeMode
from .log import EventLog
from .thread import clear_current_thread_id, set_current_thread_id
from .intercept import NondeterminismInterceptor
from .exceptions import (
    DRTError, DivergenceError, LogCorruptionError, 
    IncompleteLogError, RuntimeStateError
)


class DRTRuntime:
    """
    Main runtime controller for deterministic record and replay.
    
    Manages the lifecycle of all DRT components and provides
    the interface for executing programs deterministically.
    """
    
    def __init__(self, mode: str = 'record', log_path: str = 'execution.log'):
        """
        Initialize the runtime.
        
        Args:
            mode: 'record' or 'replay'
            log_path: Path to the execution log file
        """
        if mode not in ('record', 'replay'):
            raise ValueError(f"Invalid mode: {mode}. Must be 'record' or 'replay'")
            
        self._mode = RuntimeMode.RECORD if mode == 'record' else RuntimeMode.REPLAY
        self._log_path = Path(log_path)
        
        # Initialize components
        self._log = EventLog(self._log_path)
        self._scheduler = Scheduler(self._mode, self._log)
        self._interceptor = NondeterminismInterceptor(self._scheduler)
        
        # Runtime state
        self._initialized = False
        self._completed = False
        self._exception: Optional[Exception] = None
        self._result: Any = None
        
    def run(self, target: Callable[[], Any], *args, **kwargs) -> Any:
        """
        Run a program with deterministic execution.
        
        Args:
            target: The main function to execute
            *args: Arguments for target
            **kwargs: Keyword arguments for target
            
        Returns:
            Return value of target
            
        Raises:
            DivergenceError: If replay diverges from recording
            LogCorruptionError: If the log is corrupt
            Any exception raised by target
        """
        try:
            self._initialize()
            self._result = target(*args, **kwargs)
            self._wait_for_managed_threads()
            self._scheduler.verify_replay_complete()
            self._finalize()
            return self._result
            
        except DivergenceError:
            # Re-raise divergence errors directly
            raise
            
        except Exception as e:
            self._exception = e
            self._handle_exception(e)
            raise
            
        finally:
            self._cleanup()
            
    def _initialize(self):
        """Initialize the runtime components."""
        if self._initialized:
            raise RuntimeStateError("Runtime already initialized")
            
        if self._mode == RuntimeMode.RECORD:
            self._log.open_for_record()
        else:
            self._log.open_for_replay()
            
        # Register main thread
        set_current_thread_id(0)
        self._scheduler.register_main_thread()
        bind_runtime_context(self._scheduler, self._interceptor)
        
        self._initialized = True
        
    def _finalize(self):
        """Finalize the runtime (clean shutdown)."""
        if not self._initialized:
            return
            
        self._scheduler.shutdown()
        
        if self._mode == RuntimeMode.RECORD:
            self._log.finalize()
            
        self._completed = True
        
    def _cleanup(self):
        """Clean up runtime resources."""
        try:
            if self._initialized:
                self._scheduler.shutdown()
                self._join_native_threads()
        finally:
            clear_current_thread_id()
            clear_runtime_context()
            self._log.close()

    def _wait_for_managed_threads(self):
        """Wait for all non-main managed threads to finish or fail loudly."""
        self._scheduler.raise_pending_error()

        while self._scheduler.has_live_threads(exclude_thread_ids={0}):
            if not self._scheduler.has_runnable_threads(exclude_thread_ids={0}):
                raise self._scheduler.ensure_deadlock_error(
                    "Main target returned while other managed threads are still blocked"
                )

            self._scheduler.yield_control(0)
            self._scheduler.request_run(0)
            self._scheduler.raise_pending_error()

        self._scheduler.raise_pending_error()

    def _join_native_threads(self, timeout: float = 1.0):
        """Best-effort join for worker native threads before closing the log."""
        deadline = time.monotonic() + timeout
        current_thread = threading.current_thread()

        while time.monotonic() < deadline:
            live_threads = []

            for native_thread in self._scheduler.get_native_threads(exclude_thread_ids={0}):
                if native_thread is current_thread:
                    continue
                if native_thread.is_alive():
                    live_threads.append(native_thread)
                    native_thread.join(timeout=0.01)

            if not live_threads:
                return
        
    def _handle_exception(self, exc: Exception):
        """Handle an exception during execution."""
        if self._mode == RuntimeMode.RECORD:
            # In record mode, we don't finalize the log on exception
            # This makes the log incomplete, which is correct behavior
            pass
        else:
            # In replay mode, exceptions should match the recorded execution
            pass
            
    @property
    def mode(self) -> str:
        """Get the runtime mode as string."""
        return 'record' if self._mode == RuntimeMode.RECORD else 'replay'
        
    @property
    def log(self) -> EventLog:
        """Get the event log."""
        return self._log
        
    @property
    def scheduler(self) -> Scheduler:
        """Get the scheduler."""
        return self._scheduler
        
    @property
    def is_recording(self) -> bool:
        """Check if recording mode."""
        return self._mode == RuntimeMode.RECORD
        
    @property
    def is_replaying(self) -> bool:
        """Check if replay mode."""
        return self._mode == RuntimeMode.REPLAY


def run_recorded(target: Callable, log_path: str = 'execution.log', 
                 verbose: bool = False) -> Any:
    """
    Convenience function to record an execution.
    
    Args:
        target: Function to execute
        log_path: Path for the execution log
        verbose: If True, print execution summary
        
    Returns:
        Return value of target
    """
    runtime = DRTRuntime(mode='record', log_path=log_path)
    
    try:
        result = runtime.run(target)
        
        if verbose:
            print(f"\n=== Recording Complete ===")
            print(f"Log file: {log_path}")
            print(f"Events recorded: {len(runtime.log)}")
            print(f"Logical time: {runtime.scheduler.logical_time}")
            
        return result
        
    except Exception as e:
        if verbose:
            print(f"\n=== Recording Failed ===")
            print(f"Exception: {e}")
        raise


def run_replay(target: Callable, log_path: str = 'execution.log',
               verbose: bool = False) -> Any:
    """
    Convenience function to replay an execution.
    
    Args:
        target: Function to execute (must match recorded execution)
        log_path: Path to the execution log
        verbose: If True, print execution summary
        
    Returns:
        Return value of target
        
    Raises:
        DivergenceError: If execution diverges from recording
        IncompleteLogError: If log is incomplete
    """
    runtime = DRTRuntime(mode='replay', log_path=log_path)
    
    try:
        result = runtime.run(target)
        
        if verbose:
            print(f"\n=== Replay Complete ===")
            print(f"Log file: {log_path}")
            print("Replay stayed consistent with the recorded execution")
            
        return result
        
    except DivergenceError as e:
        if verbose:
            print(f"\n=== Replay Diverged ===")
            print(f"Divergence detected at logical time {e.logical_time}")
            print(f"Expected: {e.expected}")
            print(f"Actual: {e.actual}")
        raise
        
    except IncompleteLogError as e:
        if verbose:
            print(f"\n=== Replay Failed ===")
            print(f"Log is incomplete: {e}")
        raise


def dump_log(log_path: str) -> str:
    """
    Dump an execution log in human-readable format.
    
    Args:
        log_path: Path to the log file
        
    Returns:
        Human-readable log contents
    """
    log = EventLog(Path(log_path))
    log.open_for_replay()
    return log.dump_readable()


# Command-line interface support

def main():
    """Command-line interface for DRT utilities."""
    import argparse
    from . import __version__
    
    parser = argparse.ArgumentParser(
        description='Deterministic Record-and-Replay Runtime'
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}',
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # dump command
    dump_parser = subparsers.add_parser('dump', help='Dump log contents')
    dump_parser.add_argument('log_file', help='Log file to dump')
    
    # info command
    info_parser = subparsers.add_parser('info', help='Show log information')
    info_parser.add_argument('log_file', help='Log file to inspect')

    # verify command
    verify_parser = subparsers.add_parser('verify', help='Verify log structure and integrity')
    verify_parser.add_argument('log_file', help='Log file to verify')
    
    args = parser.parse_args()
    
    if args.command == 'dump':
        try:
            print(dump_log(args.log_file))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == 'info':
        try:
            log = EventLog(Path(args.log_file))
            log.open_for_replay()
            print(f"Log file: {args.log_file}")
            print(f"Entries: {len(log)}")
            print(f"Format version: {log.format_version}")
            print(f"Complete: {log.is_complete}")
            if log.integrity_available:
                print(f"Integrity: verified")
                print(f"CRC32: 0x{log.body_checksum:08x}")
            else:
                print("Integrity: unavailable (legacy log format)")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'verify':
        try:
            log = EventLog(Path(args.log_file))
            log.open_for_replay()
            print(f"Verified: {args.log_file}")
            print(f"Format version: {log.format_version}")
            print(f"Entries: {len(log)}")
            if log.integrity_available:
                print(f"CRC32: 0x{log.body_checksum:08x}")
            else:
                print("CRC32: unavailable (legacy log format)")
            print("Status: ok")
        except Exception as e:
            print(f"Verification failed: {e}", file=sys.stderr)
            sys.exit(1)
            
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
