#!/usr/bin/env python3
"""
Demo: producer-consumer lost wakeup bug.

This demo shows another classic concurrency bug: the "lost wakeup" problem
in producer-consumer implementations.

The bug:
    A producer signals a condition variable before the consumer starts waiting.
    The signal is "lost" and the consumer waits forever (deadlock).

This is particularly nasty because:
    1. It depends on thread scheduling order
    2. Adding printf/logging changes timing and hides the bug
    3. It causes deadlock, which is hard to debug

With DRT:
    1. Record an execution where the bug occurs
    2. Replay can drive that same recorded deadlock trace again
    3. The relevant DRT-managed interleaving is captured in the log
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import (
    DRTRuntime, DRTThread, DRTMutex, DRTCondition,
    runtime_yield
)


def make_temp_log_path() -> str:
    """Create a cross-platform temporary log path."""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
    temp_file.close()
    return temp_file.name


class BuggyQueue:
    """
    A producer-consumer queue with a lost wakeup bug.
    
    The bug: The consumer checks `empty` outside the lock, then waits.
    If the producer adds an item and signals between the check and wait,
    the signal is lost.
    """
    
    def __init__(self):
        self.items = []
        self.mutex = DRTMutex("queue_mutex")
        self.not_empty = DRTCondition(self.mutex, "not_empty")
        
    def put(self, item):
        """Add item to queue (producer)."""
        with self.mutex:
            self.items.append(item)
            print(f"  [Producer] Added item: {item}")
            self.not_empty.notify()
            
    def get_buggy(self, timeout_steps: int = 10) -> object:
        """
        Get item from queue (BUGGY consumer).
        
        Bug: checks empty without lock, then acquires lock and waits.
        """
        # BUG: Check outside lock!
        if not self.items:
            print(f"  [Consumer] Queue empty, will wait...")
            
            # Yield here allows producer to add item and signal
            # BEFORE we acquire the lock and wait
            runtime_yield()
            
            with self.mutex:
                # BUG: We might wait here even though item was added!
                # The signal was already sent before we started waiting.
                steps = 0
                while not self.items:
                    print(f"  [Consumer] Waiting on condition...")
                    
                    # Simulate timeout to prevent actual deadlock in demo
                    steps += 1
                    if steps > timeout_steps:
                        print(f"  [Consumer] TIMEOUT - Lost wakeup bug!")
                        return None
                        
                    self.not_empty.wait()
                    
        with self.mutex:
            if self.items:
                item = self.items.pop(0)
                print(f"  [Consumer] Got item: {item}")
                return item
        return None


class CorrectQueue:
    """
    A producer-consumer queue with correct implementation.
    
    Fix: Always check condition inside the lock, use while loop.
    """
    
    def __init__(self):
        self.items = []
        self.mutex = DRTMutex("queue_mutex")
        self.not_empty = DRTCondition(self.mutex, "not_empty")
        
    def put(self, item):
        """Add item to queue."""
        with self.mutex:
            self.items.append(item)
            print(f"  [Producer] Added item: {item}")
            self.not_empty.notify()
            
    def get(self) -> object:
        """Get item from queue (CORRECT)."""
        with self.mutex:
            # Correct: check AND wait inside lock, use while loop
            while not self.items:
                print(f"  [Consumer] Waiting...")
                self.not_empty.wait()
                
            item = self.items.pop(0)
            print(f"  [Consumer] Got item: {item}")
            return item


def run_buggy_demo():
    """
    Run the buggy producer-consumer demo.
    
    Shows the lost wakeup bug where consumer misses the signal.
    """
    print("=" * 60)
    print("BUGGY PRODUCER-CONSUMER (Lost Wakeup)")
    print("=" * 60)
    
    queue = BuggyQueue()
    results = {'produced': 0, 'consumed': 0, 'lost': 0}
    
    def producer():
        """Produces one item."""
        runtime_yield()  # Give consumer a chance to start
        queue.put("data")
        results['produced'] += 1
        
    def consumer():
        """Consumes one item."""
        item = queue.get_buggy()
        if item is None:
            results['lost'] += 1
            print("  [Consumer] FAILED - Lost wakeup!")
        else:
            results['consumed'] += 1
            
    # Start consumer first, then producer
    # This ordering can trigger the bug
    t_consumer = DRTThread(target=consumer, name="Consumer")
    t_producer = DRTThread(target=producer, name="Producer")
    
    print("\nStarting threads...")
    t_consumer.start()
    t_producer.start()
    
    t_consumer.join()
    t_producer.join()
    
    print(f"\nResults: produced={results['produced']}, "
          f"consumed={results['consumed']}, lost={results['lost']}")
    
    if results['lost'] > 0:
        print("\n*** LOST WAKEUP BUG DETECTED! ***")
        return True
    else:
        print("\nNo bug in this run")
        return False


def run_correct_demo():
    """Run the correct producer-consumer demo."""
    print("=" * 60)
    print("CORRECT PRODUCER-CONSUMER")
    print("=" * 60)
    
    queue = CorrectQueue()
    results = {'produced': 0, 'consumed': 0}
    
    def producer():
        runtime_yield()
        queue.put("data")
        results['produced'] += 1
        
    def consumer():
        queue.get()
        results['consumed'] += 1
        
    t_consumer = DRTThread(target=consumer, name="Consumer")
    t_producer = DRTThread(target=producer, name="Producer")
    
    print("\nStarting threads...")
    t_consumer.start()
    t_producer.start()
    
    t_consumer.join()
    t_producer.join()
    
    print(f"\nResults: produced={results['produced']}, "
          f"consumed={results['consumed']}")
    print("\nCorrect: All items consumed")
    return False


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Producer-Consumer Demo')
    parser.add_argument('--mode', choices=['record', 'replay', 'normal'],
                       default='normal')
    parser.add_argument('--log', default='prodcons_demo.log')
    parser.add_argument('--variant', choices=['buggy', 'correct'],
                       default='buggy')
    
    args = parser.parse_args()
    
    demo_func = run_buggy_demo if args.variant == 'buggy' else run_correct_demo
    
    if args.mode == 'normal':
        print("\nRunning a fresh DRT-managed execution without replay...\n")
        runtime = DRTRuntime(mode='record', log_path=make_temp_log_path())
        runtime.run(demo_func)
        
    elif args.mode == 'record':
        print(f"\nRecording to {args.log}...\n")
        runtime = DRTRuntime(mode='record', log_path=args.log)
        bug_occurred = runtime.run(demo_func)
        print(f"\nRecorded {len(runtime.log)} events")
        if bug_occurred:
            print("Bug captured!")
            
    elif args.mode == 'replay':
        print(f"\nReplaying from {args.log}...\n")
        runtime = DRTRuntime(mode='replay', log_path=args.log)
        bug_occurred = runtime.run(demo_func)
        print(f"\nReplay complete")
        if bug_occurred:
            print("Bug reappeared from the recorded trace.")


if __name__ == '__main__':
    main()
