#!/usr/bin/env python3
"""Tests for explicit RECORD-mode schedule policies."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt import DRTRuntime, DRTThread, runtime_yield


class TestScheduleExploration(unittest.TestCase):
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
        self.log_path = self.log_file.name
        self.log_file.close()

    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def _run_workload(self, **runtime_kwargs):
        results = []

        def program():
            def worker(name):
                for index in range(4):
                    results.append((name, index))
                    runtime_yield()

            threads = [
                DRTThread(target=worker, args=(name,))
                for name in ("a", "b", "c")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        runtime = DRTRuntime(
            mode="record",
            log_path=self.log_path,
            **runtime_kwargs,
        )
        runtime.run(program)
        return results, runtime.scheduler.recorded_schedule_choices

    def test_random_schedule_is_seeded(self):
        first_results, first_choices = self._run_workload(
            schedule_strategy="random",
            schedule_seed=1234,
        )
        second_results, second_choices = self._run_workload(
            schedule_strategy="random",
            schedule_seed=1234,
        )

        self.assertEqual(first_results, second_results)
        self.assertEqual(first_choices, second_choices)
        self.assertGreater(len(first_choices), 0)

    def test_different_random_seeds_can_explore_different_schedules(self):
        first_results, first_choices = self._run_workload(
            schedule_strategy="random",
            schedule_seed=1,
        )
        second_results, second_choices = self._run_workload(
            schedule_strategy="random",
            schedule_seed=2,
        )

        self.assertNotEqual(first_choices, second_choices)
        self.assertNotEqual(first_results, second_results)

    def test_scripted_schedule_reuses_runnable_choice_indexes(self):
        scripted_choices = [0, 0, 0, 0, 0, 0, 0, 0]
        _, recorded_choices = self._run_workload(
            schedule_strategy="scripted",
            schedule_choices=scripted_choices,
        )

        self.assertEqual(recorded_choices[: len(scripted_choices)], scripted_choices)

    def test_priority_schedule_prefers_lower_priority_value(self):
        results = []

        def program():
            def worker(name):
                runtime_yield()
                results.append(name)

            low_priority = DRTThread(target=worker, args=("low",))
            high_priority = DRTThread(target=worker, args=("high",))
            low_priority.start()
            high_priority.start()
            low_priority.join()
            high_priority.join()

        runtime = DRTRuntime(
            mode="record",
            log_path=self.log_path,
            schedule_strategy="priority",
            schedule_priorities={1: 10, 2: -10},
        )
        runtime.run(program)

        self.assertEqual(results[0], "high")


if __name__ == "__main__":
    unittest.main()
