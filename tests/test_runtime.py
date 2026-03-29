#!/usr/bin/env python3
"""
DRT Test Suite

Comprehensive tests for the Deterministic Record-and-Replay Runtime.
Tests verify:
    1. Basic functionality
    2. Determinism invariant
    3. Synchronization primitives
    4. Nondeterminism interception
    5. Replay correctness
"""

import sys
import os
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import (
    DRTRuntime, DRTThread, DRTMutex, DRTCondition, DRTSemaphore, DRTBarrier,
    runtime_yield, drt_time, drt_random, drt_randint, drt_seed,
    DeadlockError, DivergenceError, IncompleteLogError, EventType
)


class TestBasicRuntime(unittest.TestCase):
    """Tests for basic runtime functionality."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_simple_record(self):
        """Test recording a simple single-threaded program."""
        results = []
        
        def program():
            results.append('start')
            results.append('end')
            
        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)
        
        self.assertEqual(results, ['start', 'end'])
        self.assertTrue(runtime.log.is_complete)
        
    def test_simple_replay(self):
        """Test replaying a simple program."""
        results = []
        
        def program():
            results.append('hello')
            
        # Record
        runtime1 = DRTRuntime(mode='record', log_path=self.log_path)
        runtime1.run(program)
        
        # Replay
        results.clear()
        runtime2 = DRTRuntime(mode='replay', log_path=self.log_path)
        runtime2.run(program)
        
        self.assertEqual(results, ['hello'])


class TestThreading(unittest.TestCase):
    """Tests for thread management."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_thread_creation(self):
        """Test creating and joining threads."""
        results = []
        
        def program():
            def worker(n):
                results.append(f'worker-{n}')
                
            threads = []
            for i in range(3):
                t = DRTThread(target=worker, args=(i,))
                threads.append(t)
                t.start()
                
            for t in threads:
                t.join()
                
        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)
        
        # All workers should have run
        self.assertEqual(len(results), 3)
        self.assertIn('worker-0', results)
        self.assertIn('worker-1', results)
        self.assertIn('worker-2', results)
        
    def test_thread_determinism(self):
        """Test that thread scheduling is deterministic on replay."""
        results_record = []
        results_replay = []
        
        def program(results):
            def worker(n):
                for i in range(3):
                    results.append(f'{n}-{i}')
                    runtime_yield()
                    
            t1 = DRTThread(target=worker, args=('A',))
            t2 = DRTThread(target=worker, args=('B',))
            
            t1.start()
            t2.start()
            
            t1.join()
            t2.join()
            
        # Record
        runtime1 = DRTRuntime(mode='record', log_path=self.log_path)
        runtime1.run(lambda: program(results_record))
        
        # Replay
        runtime2 = DRTRuntime(mode='replay', log_path=self.log_path)
        runtime2.run(lambda: program(results_replay))
        
        # Order must be identical
        self.assertEqual(results_record, results_replay)


class TestMutex(unittest.TestCase):
    """Tests for mutex functionality."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_mutex_exclusion(self):
        """Test that mutex provides mutual exclusion."""
        results = []
        
        def program():
            mutex = DRTMutex()
            
            def worker(name):
                with mutex:
                    results.append(f'{name}-enter')
                    runtime_yield()  # Other thread could run here if no mutex
                    results.append(f'{name}-exit')
                    
            t1 = DRTThread(target=worker, args=('A',))
            t2 = DRTThread(target=worker, args=('B',))
            
            t1.start()
            t2.start()
            
            t1.join()
            t2.join()
            
        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)
        
        # Check that enter/exit are properly paired (no interleaving)
        # Either A-enter,A-exit,B-enter,B-exit or B-enter,B-exit,A-enter,A-exit
        a_enter = results.index('A-enter')
        a_exit = results.index('A-exit')
        b_enter = results.index('B-enter')
        b_exit = results.index('B-exit')
        
        # A's critical section should be contiguous
        self.assertEqual(a_exit, a_enter + 1)
        # B's critical section should be contiguous
        self.assertEqual(b_exit, b_enter + 1)


class TestCondition(unittest.TestCase):
    """Tests for condition variable functionality."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_condition_signal(self):
        """Test condition variable signaling."""
        results = []
        
        def program():
            mutex = DRTMutex()
            cond = DRTCondition(mutex)
            ready = [False]
            
            def waiter():
                with mutex:
                    while not ready[0]:
                        results.append('waiting')
                        cond.wait()
                    results.append('woken')
                    
            def signaler():
                runtime_yield()  # Let waiter start first
                with mutex:
                    ready[0] = True
                    results.append('signaling')
                    cond.notify()
                    
            t1 = DRTThread(target=waiter)
            t2 = DRTThread(target=signaler)
            
            t1.start()
            t2.start()
            
            t1.join()
            t2.join()
            
        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)
        
        # Waiter should wait, then signaler signals, then waiter wakes
        self.assertIn('waiting', results)
        self.assertIn('signaling', results)
        self.assertIn('woken', results)


class TestInterceptors(unittest.TestCase):
    """Tests for nondeterminism interceptors."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_time_determinism(self):
        """Test that time values are deterministic on replay."""
        times_record = []
        times_replay = []
        
        def program(times):
            for _ in range(5):
                times.append(drt_time())
                runtime_yield()
                
        # Record
        runtime1 = DRTRuntime(mode='record', log_path=self.log_path)
        runtime1.run(lambda: program(times_record))
        
        # Replay
        runtime2 = DRTRuntime(mode='replay', log_path=self.log_path)
        runtime2.run(lambda: program(times_replay))
        
        self.assertEqual(times_record, times_replay)
        
    def test_random_determinism(self):
        """Test that random values are deterministic on replay."""
        randoms_record = []
        randoms_replay = []
        
        def program(randoms):
            for _ in range(10):
                randoms.append(drt_random())
                
        # Record
        runtime1 = DRTRuntime(mode='record', log_path=self.log_path)
        runtime1.run(lambda: program(randoms_record))
        
        # Replay
        runtime2 = DRTRuntime(mode='replay', log_path=self.log_path)
        runtime2.run(lambda: program(randoms_replay))
        
        self.assertEqual(randoms_record, randoms_replay)

    def test_seed_controls_random_sequence(self):
        """Test that drt_seed controls the internal deterministic RNG."""
        values_record = []
        values_replay = []

        def program(values):
            drt_seed(123)
            values.append(drt_random())
            drt_seed(123)
            values.append(drt_random())

        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(lambda: program(values_record))

        runtime = DRTRuntime(mode='replay', log_path=self.log_path)
        runtime.run(lambda: program(values_replay))

        self.assertEqual(values_record, values_replay)
        self.assertEqual(values_record[0], values_record[1])


class TestDivergence(unittest.TestCase):
    """Tests for divergence detection."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_incomplete_log(self):
        """Test that incomplete logs are rejected."""
        # Create an empty log file
        with open(self.log_path, 'wb') as f:
            f.write(b'DRTLOG01')  # Magic only, no entries
            
        runtime = DRTRuntime(mode='replay', log_path=self.log_path)
        
        with self.assertRaises(IncompleteLogError):
            runtime.run(lambda: None)


class TestPhaseOneHardening(unittest.TestCase):
    """Regression tests for the Phase 1 runtime hardening work."""

    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()

    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass

    def test_join_propagates_worker_exception(self):
        """Unhandled worker exceptions should fail the run."""

        def program():
            def worker():
                raise ValueError('worker failed')

            thread = DRTThread(target=worker)
            thread.start()
            thread.join()

        runtime = DRTRuntime(mode='record', log_path=self.log_path)

        with self.assertRaisesRegex(ValueError, 'worker failed'):
            runtime.run(program)

    def test_runtime_waits_for_unjoined_threads(self):
        """Runtime should not finalize before unjoined workers finish."""
        results = []

        def program():
            def worker():
                results.append('worker-start')
                runtime_yield()
                results.append('worker-end')

            thread = DRTThread(target=worker)
            thread.start()
            results.append('main-end')

        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)

        self.assertEqual(results, ['worker-start', 'main-end', 'worker-end'])
        self.assertTrue(runtime.log.is_complete)

    def test_unjoined_worker_exception_still_fails_runtime(self):
        """Runtime should surface worker failures even if user code forgets to join."""

        def program():
            def worker():
                raise ValueError('boom')

            thread = DRTThread(target=worker)
            thread.start()

        runtime = DRTRuntime(mode='record', log_path=self.log_path)

        with self.assertRaisesRegex(ValueError, 'boom'):
            runtime.run(program)

    def test_deadlock_raises_instead_of_hanging(self):
        """Blocked managed threads should raise DeadlockError."""

        def program():
            mutex = DRTMutex()
            cond = DRTCondition(mutex)

            def waiter():
                with mutex:
                    cond.wait()

            thread = DRTThread(target=waiter)
            thread.start()

        runtime = DRTRuntime(mode='record', log_path=self.log_path)

        with self.assertRaises(DeadlockError):
            runtime.run(program)

    def test_reentrant_mutex_acquire_is_rejected(self):
        """Nested acquire on a non-reentrant mutex should fail loudly."""

        def program():
            mutex = DRTMutex()
            mutex.acquire()
            try:
                mutex.acquire()
            finally:
                mutex.release()

        runtime = DRTRuntime(mode='record', log_path=self.log_path)

        with self.assertRaisesRegex(RuntimeError, 'Reentrant mutex acquisition'):
            runtime.run(program)

    def test_nonblocking_acquire_does_not_poison_mutex(self):
        """Failed nonblocking acquire must not leave hidden waiter state behind."""
        results = []

        def program():
            mutex = DRTMutex()

            def holder():
                mutex.acquire()
                results.append('holder-acquired')
                runtime_yield()
                mutex.release()
                results.append('holder-released')

            thread = DRTThread(target=holder)
            thread.start()

            acquired = mutex.acquire(blocking=False)
            results.append(f'main-{acquired}')

            thread.join()

            mutex.acquire()
            results.append('main-acquired')
            mutex.release()

        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)

        self.assertEqual(
            results,
            ['holder-acquired', 'main-False', 'holder-released', 'main-acquired'],
        )


class TestLogFormat(unittest.TestCase):
    """Tests for log format and parsing."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_log_entries(self):
        """Test that log entries are correctly recorded."""
        def program():
            t = DRTThread(target=lambda: runtime_yield())
            t.start()
            t.join()
            
        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)
        
        # Check log has entries
        self.assertGreater(len(runtime.log), 0)
        
        # Check log is complete
        self.assertTrue(runtime.log.is_complete)
        
        # Verify we can dump the log
        dump = runtime.log.dump_readable()
        self.assertIn('LOG_COMPLETE', dump)


class TestSynchronizationPrimitives(unittest.TestCase):
    """Tests for additional synchronization primitives."""
    
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
        self.log_path = self.log_file.name
        self.log_file.close()
        
    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except:
            pass
            
    def test_semaphore(self):
        """Test semaphore functionality."""
        results = []
        
        def program():
            sem = DRTSemaphore(2)  # Allow 2 concurrent
            
            def worker(n):
                with sem:
                    results.append(f'{n}-in')
                    runtime_yield()
                    results.append(f'{n}-out')
                    
            threads = [DRTThread(target=worker, args=(i,)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
                
        runtime = DRTRuntime(mode='record', log_path=self.log_path)
        runtime.run(program)
        
        # All should complete
        self.assertEqual(len([r for r in results if r.endswith('-in')]), 3)
        self.assertEqual(len([r for r in results if r.endswith('-out')]), 3)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestBasicRuntime))
    suite.addTests(loader.loadTestsFromTestCase(TestThreading))
    suite.addTests(loader.loadTestsFromTestCase(TestMutex))
    suite.addTests(loader.loadTestsFromTestCase(TestCondition))
    suite.addTests(loader.loadTestsFromTestCase(TestInterceptors))
    suite.addTests(loader.loadTestsFromTestCase(TestDivergence))
    suite.addTests(loader.loadTestsFromTestCase(TestPhaseOneHardening))
    suite.addTests(loader.loadTestsFromTestCase(TestLogFormat))
    suite.addTests(loader.loadTestsFromTestCase(TestSynchronizationPrimitives))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
