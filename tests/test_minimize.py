#!/usr/bin/env python3
"""Tests for schedule-choice minimization helpers."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt.bundle import create_failure_bundle
from drt.minimize import ddmin, get_bundle_schedule_choices
from drt.runtime import DRTRuntime


class TestMinimize(unittest.TestCase):
    def test_ddmin_removes_irrelevant_choices(self):
        minimized, attempts = ddmin(
            [0, 9, 9, 1, 9],
            lambda choices: 0 in choices and 1 in choices,
        )

        self.assertEqual(minimized, [0, 1])
        self.assertGreater(attempts, 0)

    def test_bundle_schedule_choices_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            trace_path = root / "source.log"
            DRTRuntime(mode="record", log_path=str(trace_path)).run(lambda: None)

            bundle = create_failure_bundle(
                trace_path,
                root / "bundles",
                target_path="sample:target",
                run_index=1,
                total_runs=1,
                failure_type="AssertionError",
                failure_message="boom",
                traceback_text="Traceback...\nAssertionError: boom",
                schedule={"choices": [0, 1, 0]},
            )

            self.assertEqual(get_bundle_schedule_choices(bundle.path), [0, 1, 0])
            self.assertTrue((bundle.path / "schedule_choices.json").exists())


if __name__ == "__main__":
    unittest.main()
