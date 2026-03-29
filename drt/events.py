"""
DRT Events - Event type definitions for the execution log.

This module defines the complete set of events that can be recorded
and replayed. Each event captures a specific source of nondeterminism
or scheduling decision.
"""

from enum import IntEnum
from dataclasses import dataclass
from typing import Optional, Any
import struct


class EventType(IntEnum):
    """
    Exhaustive enumeration of all event types.
    
    Event types are stored as uint16 in the log.
    """
    # Scheduling events
    SCHEDULE = 1          # Thread scheduled to run
    
    # Synchronization events
    LOCK_ACQUIRE = 10     # Mutex acquired
    LOCK_RELEASE = 11     # Mutex released
    COND_WAIT = 20        # Condition wait entered
    COND_WAKE = 21        # Condition signal delivered
    
    # Nondeterminism events
    TIME_READ = 30        # time.time() called
    RANDOM_READ = 31      # random.random() called
    RANDOM_SEED = 32      # random seed set
    IO_READ = 40          # File read
    
    # Thread lifecycle events
    THREAD_CREATE = 50    # New thread created
    THREAD_EXIT = 51      # Thread exited
    THREAD_JOIN = 52      # Thread join completed
    
    # Runtime events
    LOG_COMPLETE = 100    # Clean shutdown marker


# Log entry header format: logical_time (u64), thread_id (u32), 
#                          event_type (u16), payload_len (u16)
HEADER_FORMAT = '<QIhH'  # Little-endian: uint64, uint32, int16, uint16
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 16 bytes

# Magic bytes to identify DRT log files
LOG_MAGIC = b'DRTLOG01'
LOG_MAGIC_SIZE = 8


@dataclass
class LogEntry:
    """
    A single entry in the execution log.
    
    Attributes:
        logical_time: Monotonic counter value when event occurred
        thread_id: ID of the thread that generated this event
        event_type: Type of event (from EventType enum)
        payload: Event-specific data
    """
    logical_time: int
    thread_id: int
    event_type: EventType
    payload: bytes = b''
    
    def serialize(self) -> bytes:
        """Serialize this entry to binary format."""
        header = struct.pack(
            HEADER_FORMAT,
            self.logical_time,
            self.thread_id,
            self.event_type.value,
            len(self.payload)
        )
        return header + self.payload
    
    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> tuple['LogEntry', int]:
        """
        Deserialize a log entry from binary data.
        
        Args:
            data: Binary data containing the entry
            offset: Starting offset in the data
            
        Returns:
            Tuple of (LogEntry, new_offset)
        """
        if len(data) - offset < HEADER_SIZE:
            raise ValueError("Insufficient data for header")
            
        logical_time, thread_id, event_type_val, payload_len = struct.unpack(
            HEADER_FORMAT,
            data[offset:offset + HEADER_SIZE]
        )
        
        payload_start = offset + HEADER_SIZE
        payload_end = payload_start + payload_len
        
        if len(data) < payload_end:
            raise ValueError("Insufficient data for payload")
            
        payload = data[payload_start:payload_end]
        
        return cls(
            logical_time=logical_time,
            thread_id=thread_id,
            event_type=EventType(event_type_val),
            payload=payload
        ), payload_end
    
    def __repr__(self):
        return (f"LogEntry(time={self.logical_time}, thread={self.thread_id}, "
                f"type={self.event_type.name}, payload_len={len(self.payload)})")


# Payload serialization helpers

def serialize_mutex_payload(mutex_id: int) -> bytes:
    """Serialize mutex ID for LOCK_ACQUIRE/LOCK_RELEASE events."""
    return struct.pack('<I', mutex_id)


def deserialize_mutex_payload(payload: bytes) -> int:
    """Deserialize mutex ID from payload."""
    return struct.unpack('<I', payload)[0]


def serialize_cond_payload(cond_id: int) -> bytes:
    """Serialize condition variable ID for COND_WAIT events."""
    return struct.pack('<I', cond_id)


def deserialize_cond_payload(payload: bytes) -> int:
    """Deserialize condition variable ID from payload."""
    return struct.unpack('<I', payload)[0]


def serialize_cond_wake_payload(target_thread: int, cond_id: int) -> bytes:
    """Serialize target thread and condition ID for COND_WAKE events."""
    return struct.pack('<II', target_thread, cond_id)


def deserialize_cond_wake_payload(payload: bytes) -> tuple[int, int]:
    """Deserialize target thread and condition ID from payload."""
    return struct.unpack('<II', payload)


def serialize_float_payload(value: float) -> bytes:
    """Serialize float for TIME_READ/RANDOM_READ events."""
    return struct.pack('<d', value)


def deserialize_float_payload(payload: bytes) -> float:
    """Deserialize float from payload."""
    return struct.unpack('<d', payload)[0]


def serialize_thread_create_payload(new_thread_id: int) -> bytes:
    """Serialize new thread ID for THREAD_CREATE events."""
    return struct.pack('<I', new_thread_id)


def deserialize_thread_create_payload(payload: bytes) -> int:
    """Deserialize new thread ID from payload."""
    return struct.unpack('<I', payload)[0]


def serialize_io_payload(data: bytes) -> bytes:
    """Serialize I/O data (already bytes)."""
    return data


def deserialize_io_payload(payload: bytes) -> bytes:
    """Deserialize I/O data."""
    return payload
