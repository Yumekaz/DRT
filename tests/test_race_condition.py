#!/usr/bin/env python3
"""
Test: Real Race Condition Bug - Caught and Reproduced

This test demonstrates DRT's core value proposition with a REAL race condition.

The Bug: Lost Update
    Multiple threads increment a shared counter without synchronization.
    Each thread reads the counter, adds 1, writes back.
    If two threads read the same value, one update is lost.
    
    Expected: counter = N * increments_per_thread
    Actual: counter < expected (some increments lost)

Why This Bug Is Hard:
    - Depends on exact interleaving of read-modify-write
    - Adding print() changes timing, bug may disappear
    - Debugger stepping changes timing, bug may disappear  
    - May only occur 1 in 100 runs normally

What DRT Does:
    - Records the exact interleaving that caused lost updates
    - Replays it perfectly, same lost updates every time
    - Now you can debug without the bug disappearing
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import DRTRuntime, DRTThread, runtime_yield


class BuggyCounter:
    """
    Counter with a classic lost update race condition.
    
    The bug: increment() does read-modify-write non-atomically.
    Two threads can read the same value, both add 1, both write back.
    One increment is lost.
    """
    
    def __init__(self):
        self.value = 0
        
    def increment(self):
        """
        Increment the counter. NOT THREAD-SAFE.
        
        Bug: read, modify, write are separate operations.
        Another thread can read between our read and write.
        """
        # Read current value
        current = self.value
        
        # Yield to let other threads interleave here
        # This is where the race condition happens:
        # Another thread reads the SAME value
        runtime_yield()
        
        # Write back incremented value
        # If another thread also read 'current', their write will
        # overwrite ours (or vice versa) - one increment lost!
        self.value = current + 1


def run_buggy_counter(num_threads: int, increments_per_thread: int):
    """
    Run the buggy counter with multiple threads.
    Returns (final_value, expected_value).
    """
    counter = BuggyCounter()
    
    def worker():
        for _ in range(increments_per_thread):
            counter.increment()
    
    threads = [DRTThread(target=worker) for _ in range(num_threads)]
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    expected = num_threads * increments_per_thread
    return counter.value, expected


def main():
    print("=" * 70)
    print("RACE CONDITION: LOST UPDATE BUG")
    print("=" * 70)
    print()
    print("Bug: Multiple threads increment a counter without synchronization.")
    print("     Read-modify-write is not atomic, so updates get lost.")
    print()
    
    log_path = tempfile.mktemp(suffix='.log')
    
    NUM_THREADS = 3
    INCREMENTS = 5  # Per thread
    EXPECTED = NUM_THREADS * INCREMENTS  # = 15
    
    # ─────────────────────────────────────────────────────────────────────
    # STEP 1: Record execution with the bug
    # ─────────────────────────────────────────────────────────────────────
    print("-" * 70)
    print("STEP 1: Recording execution...")
    print("-" * 70)
    print(f"  {NUM_THREADS} threads, {INCREMENTS} increments each")
    print(f"  Expected final count: {EXPECTED}")
    print()
    
    recorded_value = [0]
    
    def program():
        value, expected = run_buggy_counter(NUM_THREADS, INCREMENTS)
        recorded_value[0] = value
    
    runtime = DRTRuntime(mode='record', log_path=log_path)
    runtime.run(program)
    
    actual = recorded_value[0]
    lost = EXPECTED - actual
    
    print(f"  Actual final count: {actual}")
    print(f"  Lost updates: {lost}")
    print()
    
    if lost > 0:
        print("  *** BUG TRIGGERED: Updates were lost! ***")
    else:
        print("  (No race occurred this run - threads happened to not interleave badly)")
        print("  (In real code without yield points, you'd need to run many times)")
    
    # ─────────────────────────────────────────────────────────────────────
    # STEP 2: Replay and verify identical result
    # ─────────────────────────────────────────────────────────────────────
    print()
    print("-" * 70)
    print("STEP 2: Replaying execution...")
    print("-" * 70)
    
    replayed_value = [0]
    
    def replay_program():
        value, expected = run_buggy_counter(NUM_THREADS, INCREMENTS)
        replayed_value[0] = value
    
    runtime = DRTRuntime(mode='replay', log_path=log_path)
    runtime.run(replay_program)
    
    print(f"  Replay final count: {replayed_value[0]}")
    
    if replayed_value[0] == actual:
        print("  ✓ Replay matched recording exactly!")
    else:
        print("  ✗ REPLAY DIVERGED - this should never happen!")
        sys.exit(1)
    
    # ─────────────────────────────────────────────────────────────────────
    # STEP 3: Replay 10 times to prove determinism
    # ─────────────────────────────────────────────────────────────────────
    print()
    print("-" * 70)
    print("STEP 3: Replaying 10 times to prove determinism...")
    print("-" * 70)
    
    all_match = True
    for i in range(10):
        replay_val = [0]
        
        def prog():
            value, _ = run_buggy_counter(NUM_THREADS, INCREMENTS)
            replay_val[0] = value
        
        runtime = DRTRuntime(mode='replay', log_path=log_path)
        runtime.run(prog)
        
        match = replay_val[0] == actual
        status = "✓" if match else "✗"
        print(f"  Replay {i+1:2d}: count={replay_val[0]:2d} {status}")
        
        if not match:
            all_match = False
    
    # ─────────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    
    if all_match:
        print("SUCCESS: All 10 replays produced identical results!")
        print("=" * 70)
        print()
        if lost > 0:
            print(f"The race condition (lost {lost} updates) was captured once")
            print("and reproduced perfectly 10 times.")
        else:
            print("No race occurred, but the execution was still deterministic.")
        print()
        print("This is DRT's value: record once, reproduce forever.")
    else:
        print("FAILURE: Replays were not deterministic!")
        print("=" * 70)
        sys.exit(1)
    
    os.unlink(log_path)


if __name__ == '__main__':
    main()
