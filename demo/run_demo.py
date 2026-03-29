#!/usr/bin/env python3
"""
DRT end-to-end demonstration script.

This script walks through the core workflow:
    1. Run plain Python threads -> bug appears intermittently
    2. Run with --record -> capture one execution
    3. Run with --replay -> follow that recorded trace
    4. Repeat replay -> confirm replay stays consistent

It is a demo of the supported DRT workflow, not a proof that every Python
threading behavior is covered.
"""

import sys
import os
import random
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import (
    DRTRuntime, DRTThread, runtime_yield
)


def print_header(text):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f" {text}")
    print("=" * 70 + "\n")


def print_step(n, text):
    """Print a step header."""
    print(f"\n>>> STEP {n}: {text}\n")


class Counter:
    """
    A shared counter with a race condition bug.
    
    The bug: increment reads, yields, then writes - allowing
    another thread to interleave and cause lost updates.
    """
    
    def __init__(self):
        self.value = 0
        
    def increment_buggy(self):
        """Buggy increment - has race condition."""
        current = self.value
        runtime_yield()  # Race window!
        self.value = current + 1
        
    def increment_correct(self, mutex):
        """Correct increment - uses mutex."""
        with mutex:
            self.value += 1


def make_temp_log_path() -> str:
    """Create a cross-platform temporary log path."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
    temp_file.close()
    return temp_file.name


def run_buggy_counter_without_drt() -> tuple[bool, int]:
    """
    Show the same race in plain Python threads without DRT replay.

    The tiny random sleep widens the race window enough to make the bug visible
    without pretending that this execution is being recorded.
    """
    value = {'count': 0}
    num_threads = 4
    increments_per_thread = 40
    expected_total = num_threads * increments_per_thread

    def worker():
        for _ in range(increments_per_thread):
            snapshot = value['count']
            time.sleep(random.uniform(0.0, 0.0005))
            value['count'] = snapshot + 1

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    actual = value['count']
    lost = expected_total - actual

    print(f"  Expected count: {expected_total}")
    print(f"  Actual count:   {actual}")

    if lost > 0:
        print(f"  Lost updates:   {lost}")
        print("  Race condition observed in plain threaded execution")
        return True, actual

    print("  No race observed in this run")
    return False, actual


def run_buggy_counter_demo():
    """
    Demo: Multiple threads increment a counter without proper synchronization.
    Expected: With N threads doing M increments each, final count should be N*M.
    Actual: With bug, final count is often less (lost updates).
    """
    counter = Counter()
    num_threads = 3
    increments_per_thread = 5
    expected_total = num_threads * increments_per_thread
    
    def worker():
        for _ in range(increments_per_thread):
            counter.increment_buggy()
            
    threads = [DRTThread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    actual = counter.value
    lost = expected_total - actual
    
    print(f"  Expected count: {expected_total}")
    print(f"  Actual count:   {actual}")
    
    if lost > 0:
        print(f"  Lost updates:   {lost}")
        print(f"  *** BUG: Race condition detected! ***")
        return True
    else:
        print(f"  No bug detected in this run")
        return False


def main():
    print_header("DETERMINISTIC RECORD-AND-REPLAY RUNTIME DEMONSTRATION")
    
    print("""
This demonstration shows three things:
  1. The race exists under normal threaded execution before replay
  2. DRT can record one failing execution
  3. DRT can replay that recorded execution consistently

The demo uses a simple race condition: multiple threads incrementing
a shared counter without proper synchronization. This causes "lost updates"
where some increments are silently lost.
""")
    
    log_path = make_temp_log_path()
    
    # =========================================================================
    # STEP 1: Show that the bug exists before replay
    # =========================================================================
    print_step(1, "Run with plain Python threads to show the bug before replay")
    
    print("Running the buggy counter 5 times to show inconsistent results...\n")
    
    bug_count = 0
    observed_counts = []
    for i in range(5):
        print(f"  --- Run {i+1} ---")
        try:
            bug_occurred, actual_count = run_buggy_counter_without_drt()
            observed_counts.append(actual_count)
            if bug_occurred:
                bug_count += 1
        except Exception as e:
            print(f"  Error: {e}")
        print()
        
    print(f"Bug appeared in {bug_count}/5 runs ({100*bug_count/5:.0f}%)")
    unique_counts = sorted(set(observed_counts))
    if len(unique_counts) > 1:
        print(f"Observed final counts: {', '.join(str(count) for count in unique_counts)}")
        print("The plain threaded runs did not all land on the same result before replay pinned one down.")
    elif unique_counts:
        print(f"Observed final count in every run: {unique_counts[0]}")
        print("The bug already exists before replay; replay's job is to pin one execution down and make it repeatable.")
    
    # =========================================================================
    # STEP 2: Record an execution that exhibits the bug
    # =========================================================================
    print_step(2, "RECORD an execution that exhibits the bug")
    
    print(f"Recording execution to: {log_path}\n")
    
    # Keep trying until we capture a buggy execution
    attempts = 0
    captured_bug = False
    while not captured_bug and attempts < 20:
        attempts += 1
        runtime = DRTRuntime(mode='record', log_path=log_path)
        try:
            captured_bug = runtime.run(run_buggy_counter_demo)
        except Exception as e:
            print(f"  Error: {e}")
            
    if captured_bug:
        print(f"\n  Bug captured after {attempts} attempt(s)!")
        print(f"  Log file: {log_path}")
        print(f"  Events recorded: {len(runtime.log)}")
    else:
        print("\n  Could not capture bug in 20 attempts.")
        print("  (This can happen - the bug is rare. Try running again.)")
        return
        
    # =========================================================================
    # STEP 3: Replay the recorded execution
    # =========================================================================
    print_step(3, "REPLAY the recorded execution")
    
    print("Replaying the recorded execution...\n")
    
    runtime = DRTRuntime(mode='replay', log_path=log_path)
    try:
        bug_reproduced = runtime.run(run_buggy_counter_demo)
        print(f"\n  Replay {'succeeded' if bug_reproduced else 'completed'}!")
        print("  The runtime followed the recorded DRT execution.")
    except Exception as e:
        print(f"\n  Replay error: {e}")
        return
        
    # =========================================================================
    # STEP 4: Replay again to confirm replay stability
    # =========================================================================
    print_step(4, "REPLAY again to confirm replay stability")
    
    print("Replaying the same execution 3 more times...\n")
    
    for i in range(3):
        print(f"  --- Replay {i+2} ---")
        runtime = DRTRuntime(mode='replay', log_path=log_path)
        try:
            runtime.run(run_buggy_counter_demo)
            print("  Replay stayed consistent with the recording.\n")
        except Exception as e:
            print(f"  Error: {e}\n")
            
    # =========================================================================
    # SUMMARY
    # =========================================================================
    print_header("DEMONSTRATION COMPLETE")
    
    print(f"""
SUMMARY:
========

1. BEFORE REPLAY: Plain threaded execution can already expose the bug.
   Replay matters because it lets us hold onto one failing trace.

2. WITH DRT RECORD: We captured an execution where the bug occurred.
   The relevant DRT-managed scheduling and nondeterministic events were logged.

3. WITH DRT REPLAY: The recorded execution was replayed consistently.
   If replay had drifted, DRT would have raised DivergenceError.

4. REPEATED REPLAY: Replaying the same log kept reproducing the same
   recorded execution inside DRT's supported API surface.

This is the core value proposition of DRT:
   - Record a failing execution once
   - Re-run that recorded trace without guessing at the interleaving
   - Debug without the bug vanishing the moment you inspect it

Log file for further analysis: {log_path}
""")
    
    # Dump log excerpt
    print("\nLog excerpt (first 25 lines):")
    print("-" * 50)
    dump = runtime.log.dump_readable()
    lines = dump.split('\n')
    for line in lines[:25]:
        print(line)
    if len(lines) > 25:
        print(f"  ... ({len(lines) - 25} more entries)")


if __name__ == '__main__':
    main()
