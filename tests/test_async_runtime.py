#!/usr/bin/env python3
"""Tests for the opt-in DRT async task runtime."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt.async_runtime import DRTAsyncRuntime, drt_async_yield
from drt.exceptions import DivergenceError


class TestDRTAsyncRuntime(unittest.TestCase):
    def setUp(self):
        self.log_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
        self.log_path = self.log_file.name
        self.log_file.close()

    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    async def _worker(self, name, results):
        results.append((name, "start"))
        await drt_async_yield()
        results.append((name, "end"))

    def _program(self, results):
        def program(runtime):
            runtime.create_task(self._worker("a", results))
            runtime.create_task(self._worker("b", results))

        return program

    def test_async_record_and_replay_are_deterministic(self):
        record_results = []
        replay_results = []

        runtime = DRTAsyncRuntime(mode="record", log_path=self.log_path)
        runtime.run(self._program(record_results))

        runtime = DRTAsyncRuntime(mode="replay", log_path=self.log_path)
        runtime.run(self._program(replay_results))

        self.assertEqual(record_results, replay_results)

    def test_seeded_random_async_schedule_is_reproducible(self):
        first_results = []
        second_results = []

        runtime = DRTAsyncRuntime(
            mode="record",
            log_path=self.log_path,
            schedule_strategy="random",
            schedule_seed=99,
        )
        runtime.run(self._program(first_results))
        first_choices = runtime.recorded_schedule_choices

        runtime = DRTAsyncRuntime(
            mode="record",
            log_path=self.log_path,
            schedule_strategy="random",
            schedule_seed=99,
        )
        runtime.run(self._program(second_results))

        self.assertEqual(first_results, second_results)
        self.assertEqual(first_choices, runtime.recorded_schedule_choices)

    def test_async_replay_rejects_missing_task_creation(self):
        runtime = DRTAsyncRuntime(mode="record", log_path=self.log_path)
        runtime.run(self._program([]))

        def missing_task_program(runtime):
            runtime.create_task(self._worker("a", []))

        runtime = DRTAsyncRuntime(mode="replay", log_path=self.log_path)
        with self.assertRaises(DivergenceError):
            runtime.run(missing_task_program)


if __name__ == "__main__":
    unittest.main()
