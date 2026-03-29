#!/usr/bin/env python3
"""
Demo: Bank Transfer Race Condition

This demo shows a classic concurrency bug: a race condition in bank transfers
that can cause money to be created or destroyed.

The Bug:
    Two threads simultaneously transfer money between accounts.
    Without proper locking, the read-modify-write operations can interleave,
    causing incorrect final balances.

Expected:
    With correct locking: total money is conserved
    With the bug: total money may change (money created or destroyed)

This demo proves:
    1. The bug occurs rarely under normal execution (nondeterministic)
    2. With DRT recording, we capture a buggy execution
    3. With DRT replay, the bug reproduces 100% of the time
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import (
    DRTRuntime, DRTThread, DRTMutex,
    runtime_yield, drt_random, drt_time
)


class BuggyBankAccount:
    """
    A bank account with a deliberately buggy transfer method.
    
    The bug: transfer() reads both balances, yields, then writes both.
    This allows another thread to interleave and corrupt the state.
    """
    
    def __init__(self, account_id: str, initial_balance: float):
        self.account_id = account_id
        self.balance = initial_balance
        # Note: No lock! This is the bug.
        
    def transfer_to(self, other: 'BuggyBankAccount', amount: float) -> bool:
        """
        Transfer money to another account (BUGGY).
        
        This method has a race condition: it reads both balances,
        then yields, then writes both. Another thread can interleave.
        """
        # Read phase
        my_balance = self.balance
        their_balance = other.balance
        
        if my_balance < amount:
            return False
            
        # DANGER: Yield point here allows interleaving!
        # In real code, this might be a cache miss, context switch,
        # or any other timing variation.
        runtime_yield()
        
        # Write phase (using stale values!)
        self.balance = my_balance - amount
        other.balance = their_balance + amount
        
        return True


class CorrectBankAccount:
    """
    A bank account with correct locking.
    
    Uses a mutex to ensure atomic read-modify-write.
    """
    
    # Global lock for all transfers (simple but correct)
    _transfer_lock = None
    
    @classmethod
    def set_lock(cls, lock: DRTMutex):
        cls._transfer_lock = lock
        
    def __init__(self, account_id: str, initial_balance: float):
        self.account_id = account_id
        self.balance = initial_balance
        
    def transfer_to(self, other: 'CorrectBankAccount', amount: float) -> bool:
        """
        Transfer money to another account (CORRECT).
        
        Uses locking to ensure atomicity.
        """
        with self._transfer_lock:
            if self.balance < amount:
                return False
                
            self.balance -= amount
            other.balance += amount
            return True


def run_buggy_demo():
    """
    Run the buggy bank transfer demo.
    
    Creates two accounts with $1000 each ($2000 total).
    Two threads perform transfers in opposite directions.
    With the bug, the total may not be $2000 at the end.
    """
    print("=" * 60)
    print("BUGGY BANK TRANSFER DEMO")
    print("=" * 60)
    
    # Create accounts
    alice = BuggyBankAccount("Alice", 1000.0)
    bob = BuggyBankAccount("Bob", 1000.0)
    
    initial_total = alice.balance + bob.balance
    print(f"Initial balances: Alice=${alice.balance}, Bob=${bob.balance}")
    print(f"Initial total: ${initial_total}")
    
    # Track transfer counts
    transfer_count = [0, 0]  # [alice_to_bob, bob_to_alice]
    
    def alice_to_bob_transfers():
        """Thread 1: Transfer from Alice to Bob"""
        for i in range(5):
            amount = 100.0
            if alice.transfer_to(bob, amount):
                transfer_count[0] += 1
            runtime_yield()
            
    def bob_to_alice_transfers():
        """Thread 2: Transfer from Bob to Alice"""
        for i in range(5):
            amount = 100.0
            if bob.transfer_to(alice, amount):
                transfer_count[1] += 1
            runtime_yield()
    
    # Create and start threads
    t1 = DRTThread(target=alice_to_bob_transfers, name="AliceToBob")
    t2 = DRTThread(target=bob_to_alice_transfers, name="BobToAlice")
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
    
    # Check results
    final_total = alice.balance + bob.balance
    print(f"\nFinal balances: Alice=${alice.balance}, Bob=${bob.balance}")
    print(f"Final total: ${final_total}")
    print(f"Transfers completed: Alice→Bob={transfer_count[0]}, Bob→Alice={transfer_count[1]}")
    
    if abs(final_total - initial_total) > 0.01:
        print(f"\n*** BUG DETECTED! ***")
        print(f"Money {'created' if final_total > initial_total else 'destroyed'}: "
              f"${abs(final_total - initial_total)}")
        return True  # Bug occurred
    else:
        print(f"\nNo bug detected in this run (money conserved)")
        return False  # No bug this time


def run_correct_demo():
    """
    Run the correct (fixed) bank transfer demo.
    
    Uses proper locking to ensure atomic transfers.
    """
    print("=" * 60)
    print("CORRECT BANK TRANSFER DEMO")
    print("=" * 60)
    
    # Set up the lock
    transfer_lock = DRTMutex("transfer_lock")
    CorrectBankAccount.set_lock(transfer_lock)
    
    # Create accounts
    alice = CorrectBankAccount("Alice", 1000.0)
    bob = CorrectBankAccount("Bob", 1000.0)
    
    initial_total = alice.balance + bob.balance
    print(f"Initial balances: Alice=${alice.balance}, Bob=${bob.balance}")
    print(f"Initial total: ${initial_total}")
    
    transfer_count = [0, 0]
    
    def alice_to_bob_transfers():
        for i in range(5):
            if alice.transfer_to(bob, 100.0):
                transfer_count[0] += 1
            runtime_yield()
            
    def bob_to_alice_transfers():
        for i in range(5):
            if bob.transfer_to(alice, 100.0):
                transfer_count[1] += 1
            runtime_yield()
    
    t1 = DRTThread(target=alice_to_bob_transfers, name="AliceToBob")
    t2 = DRTThread(target=bob_to_alice_transfers, name="BobToAlice")
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
    
    final_total = alice.balance + bob.balance
    print(f"\nFinal balances: Alice=${alice.balance}, Bob=${bob.balance}")
    print(f"Final total: ${final_total}")
    print(f"Transfers: Alice→Bob={transfer_count[0]}, Bob→Alice={transfer_count[1]}")
    
    if abs(final_total - initial_total) > 0.01:
        print(f"\n*** UNEXPECTED BUG! ***")
        return True
    else:
        print(f"\nCorrect: Money conserved (as expected)")
        return False


def main():
    """Main demo entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Bank Transfer Race Condition Demo')
    parser.add_argument('--mode', choices=['record', 'replay', 'normal'],
                       default='normal', help='Execution mode')
    parser.add_argument('--log', default='bank_demo.log',
                       help='Log file path')
    parser.add_argument('--variant', choices=['buggy', 'correct'],
                       default='buggy', help='Which demo to run')
    parser.add_argument('--iterations', type=int, default=1,
                       help='Number of iterations (normal mode only)')
    
    args = parser.parse_args()
    
    demo_func = run_buggy_demo if args.variant == 'buggy' else run_correct_demo
    
    if args.mode == 'normal':
        # Run without DRT to show nondeterministic behavior
        print("\nRunning WITHOUT deterministic runtime...")
        print("(Bug may or may not appear depending on thread scheduling)\n")
        
        bug_count = 0
        for i in range(args.iterations):
            if args.iterations > 1:
                print(f"\n--- Iteration {i+1}/{args.iterations} ---")
            
            # Create a minimal runtime just to initialize the primitives
            runtime = DRTRuntime(mode='record', log_path='/tmp/throwaway.log')
            try:
                bug_occurred = runtime.run(demo_func)
                if bug_occurred:
                    bug_count += 1
            except Exception as e:
                print(f"Error: {e}")
                
        if args.iterations > 1:
            print(f"\n{'='*60}")
            print(f"Bug occurred in {bug_count}/{args.iterations} runs "
                  f"({100*bug_count/args.iterations:.1f}%)")
                  
    elif args.mode == 'record':
        print(f"\nRecording execution to {args.log}...")
        print("(This will capture the exact thread interleaving)\n")
        
        runtime = DRTRuntime(mode='record', log_path=args.log)
        try:
            bug_occurred = runtime.run(demo_func)
            print(f"\nRecording complete!")
            print(f"Log file: {args.log}")
            print(f"Events recorded: {len(runtime.log)}")
            if bug_occurred:
                print("Bug was captured in this recording!")
        except Exception as e:
            print(f"Recording failed: {e}")
            raise
            
    elif args.mode == 'replay':
        print(f"\nReplaying execution from {args.log}...")
        print("(This will reproduce the exact same behavior)\n")
        
        runtime = DRTRuntime(mode='replay', log_path=args.log)
        try:
            bug_occurred = runtime.run(demo_func)
            print(f"\nReplay complete!")
            print("Execution matched recording exactly.")
            if bug_occurred:
                print("Bug reproduced successfully!")
        except Exception as e:
            print(f"Replay failed: {e}")
            raise


if __name__ == '__main__':
    main()
