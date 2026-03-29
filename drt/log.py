"""
DRT Log - Append-only event log for recording and replay.

The log is the source of truth for replay. It stores all scheduling
decisions and nondeterministic inputs in a binary format.

Properties:
- Append-only during recording
- Read-only during replay
- Binary format with strict parsing
- LOG_COMPLETE marker indicates clean shutdown
"""

import os
import struct
from pathlib import Path
from typing import Iterator, Optional, List
from threading import Lock

from .events import (
    LogEntry, EventType, HEADER_SIZE, HEADER_FORMAT
)
from .exceptions import LogCorruptionError, IncompleteLogError


# Magic bytes to identify DRT log files
LOG_MAGIC = b'DRTLOG01'
LOG_MAGIC_SIZE = 8


class EventLog:
    """
    Append-only event log for deterministic record and replay.
    
    In RECORD mode:
        - Events are appended sequentially
        - Each write is flushed to disk
        - LOG_COMPLETE is written on clean shutdown
        
    In REPLAY mode:
        - Log is loaded into memory
        - Events are accessed by logical time index
        - LOG_COMPLETE must be present
    """
    
    def __init__(self, filepath: Path):
        """
        Initialize the event log.
        
        Args:
            filepath: Path to the log file
        """
        self.filepath = Path(filepath)
        self._file = None
        self._lock = Lock()
        self._entries: List[LogEntry] = []
        self._is_recording = False
        self._is_complete = False
        
    def open_for_record(self):
        """
        Open the log for recording.
        
        Creates a new log file, overwriting any existing file.
        """
        self._file = open(self.filepath, 'wb')
        self._file.write(LOG_MAGIC)
        self._file.flush()
        self._is_recording = True
        self._entries = []
        
    def open_for_replay(self):
        """
        Open the log for replay.
        
        Loads all entries into memory and verifies LOG_COMPLETE marker.
        
        Raises:
            LogCorruptionError: If the log is corrupt
            IncompleteLogError: If LOG_COMPLETE marker is missing
        """
        if not self.filepath.exists():
            raise LogCorruptionError(f"Log file not found: {self.filepath}")
            
        with open(self.filepath, 'rb') as f:
            data = f.read()
            
        # Verify magic
        if len(data) < LOG_MAGIC_SIZE:
            raise LogCorruptionError("Log file too small")
        if data[:LOG_MAGIC_SIZE] != LOG_MAGIC:
            raise LogCorruptionError("Invalid log file magic")
            
        # Parse all entries
        offset = LOG_MAGIC_SIZE
        self._entries = []
        
        while offset < len(data):
            try:
                entry, offset = LogEntry.deserialize(data, offset)
                self._entries.append(entry)
            except (ValueError, struct.error) as e:
                raise LogCorruptionError(f"Failed to parse log entry: {e}")
                
        # Verify LOG_COMPLETE marker
        if not self._entries:
            raise IncompleteLogError("Log is empty")
            
        if self._entries[-1].event_type != EventType.LOG_COMPLETE:
            raise IncompleteLogError(
                "Log does not end with LOG_COMPLETE marker. "
                "The recorded execution may have crashed."
            )
            
        self._is_complete = True
        self._is_recording = False
        
    def append(self, entry: LogEntry):
        """
        Append an entry to the log.
        
        Thread-safe. Flushes to disk immediately.
        
        Args:
            entry: The log entry to append
        """
        if not self._is_recording:
            raise RuntimeError("Log is not open for recording")
            
        with self._lock:
            data = entry.serialize()
            self._file.write(data)
            self._file.flush()
            os.fsync(self._file.fileno())
            self._entries.append(entry)
            
    def get_entry(self, index: int) -> Optional[LogEntry]:
        """
        Get a log entry by index.
        
        Args:
            index: Zero-based index into the log
            
        Returns:
            The log entry, or None if index is out of bounds
        """
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None
    
    def get_entry_at_time(self, logical_time: int) -> Optional[LogEntry]:
        """
        Get the log entry at a specific logical time.
        
        Note: During replay, entries are accessed sequentially,
        so we search linearly. This is acceptable because the
        scheduler always advances logical time monotonically.
        
        Args:
            logical_time: The logical time to search for
            
        Returns:
            The log entry, or None if not found
        """
        for entry in self._entries:
            if entry.logical_time == logical_time:
                return entry
        return None
    
    def find_entries_by_type(self, event_type: EventType, 
                             start_time: int = 0) -> Iterator[LogEntry]:
        """
        Find all entries of a given type after a starting time.
        
        Args:
            event_type: Type of events to find
            start_time: Minimum logical time (inclusive)
            
        Yields:
            Matching log entries
        """
        for entry in self._entries:
            if entry.logical_time >= start_time and entry.event_type == event_type:
                yield entry
                
    def finalize(self):
        """
        Finalize the log with LOG_COMPLETE marker.
        
        Must be called for clean shutdown.
        """
        if not self._is_recording:
            return
            
        complete_entry = LogEntry(
            logical_time=len(self._entries),
            thread_id=0,
            event_type=EventType.LOG_COMPLETE,
            payload=b''
        )
        self.append(complete_entry)
        self._is_complete = True
        
    def close(self):
        """Close the log file."""
        if self._file:
            self._file.close()
            self._file = None
            
    @property
    def entry_count(self) -> int:
        """Number of entries in the log (excluding LOG_COMPLETE)."""
        count = len(self._entries)
        if self._is_complete and count > 0:
            return count - 1  # Exclude LOG_COMPLETE
        return count
    
    @property
    def is_complete(self) -> bool:
        """Whether the log has a LOG_COMPLETE marker."""
        return self._is_complete

    @property
    def is_recording(self) -> bool:
        """Whether the log is currently open for recording."""
        return self._is_recording and self._file is not None
    
    def __iter__(self) -> Iterator[LogEntry]:
        """Iterate over all entries (excluding LOG_COMPLETE)."""
        for entry in self._entries:
            if entry.event_type != EventType.LOG_COMPLETE:
                yield entry
                
    def __len__(self) -> int:
        """Number of entries (excluding LOG_COMPLETE)."""
        return self.entry_count
    
    def dump_readable(self) -> str:
        """
        Dump the log in human-readable format.
        
        Useful for debugging.
        """
        lines = [f"DRT Log: {self.filepath}", f"Entries: {len(self._entries)}", ""]
        
        for i, entry in enumerate(self._entries):
            payload_str = ""
            if entry.payload:
                if entry.event_type in (EventType.TIME_READ, EventType.RANDOM_READ):
                    from .events import deserialize_float_payload
                    try:
                        val = deserialize_float_payload(entry.payload)
                        payload_str = f" value={val}"
                    except:
                        payload_str = f" payload={entry.payload.hex()}"
                elif entry.event_type == EventType.LOCK_ACQUIRE:
                    from .events import deserialize_lock_acquire_payload
                    try:
                        mutex_id, blocking, immediate = deserialize_lock_acquire_payload(
                            entry.payload
                        )
                        payload_str = (
                            f" mutex={mutex_id} blocking={blocking}"
                            f" immediate={immediate}"
                        )
                    except:
                        payload_str = f" payload={entry.payload.hex()}"
                elif entry.event_type == EventType.LOCK_RELEASE:
                    from .events import deserialize_mutex_payload
                    try:
                        mutex_id = deserialize_mutex_payload(entry.payload)
                        payload_str = f" mutex={mutex_id}"
                    except:
                        payload_str = f" payload={entry.payload.hex()}"
                elif entry.event_type == EventType.THREAD_CREATE:
                    from .events import deserialize_thread_create_payload
                    try:
                        tid = deserialize_thread_create_payload(entry.payload)
                        payload_str = f" new_thread={tid}"
                    except:
                        payload_str = f" payload={entry.payload.hex()}"
                elif entry.event_type == EventType.THREAD_JOIN:
                    from .events import deserialize_thread_join_payload
                    try:
                        tid, immediate = deserialize_thread_join_payload(entry.payload)
                        payload_str = (
                            f" target_thread={tid} immediate={immediate}"
                        )
                    except:
                        payload_str = f" payload={entry.payload.hex()}"
                elif entry.event_type == EventType.IO_READ:
                    from .events import deserialize_io_read_payload
                    try:
                        path, size, data = deserialize_io_read_payload(entry.payload)
                        if path:
                            payload_str = (
                                f" path={path!r} size={size} bytes={len(data)}"
                            )
                        else:
                            payload_str = f" bytes={len(data)}"
                    except:
                        payload_str = f" payload={entry.payload.hex()}"
                else:
                    payload_str = f" payload={entry.payload.hex()}" if entry.payload else ""
                    
            lines.append(
                f"[{i:4d}] t={entry.logical_time:4d} thread={entry.thread_id:2d} "
                f"{entry.event_type.name}{payload_str}"
            )
            
        return "\n".join(lines)
