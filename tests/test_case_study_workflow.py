#!/usr/bin/env python3
"""End-to-end proof for the inventory oversell case study."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo.case_study_inventory_oversell import (
    BUG_TARGET,
    FIXED_TARGET,
    check_last_item_oversell,
)
from drt.checker import run_check
from drt.explorer import build_schedule_plan
from drt.minimize import minimize_bundle
from drt.replay import replay_bundle
from drt.trace import format_explain, format_timeline


class TestInventoryOversellWorkflow(unittest.TestCase):
    def test_check_bundle_replay_minimize_and_fixed_control(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan = build_schedule_plan(
                mode="exhaustive",
                runs=64,
                branching_factor=2,
                depth=6,
            )

            result = run_check(
                BUG_TARGET,
                exploration_plan=plan,
                bundle_root=root / "failures",
            )

            self.assertFalse(result.passed)
            self.assertIsNotNone(result.failure_bundle)
            bundle = result.failure_bundle
            self.assertTrue((bundle / "trace.log").exists())
            self.assertTrue((bundle / "failure.txt").exists())
            self.assertTrue((bundle / "source_hashes.json").exists())
            self.assertTrue((bundle / "schedule_choices.json").exists())

            replay = replay_bundle(bundle)
            self.assertTrue(replay.reproduced)
            self.assertFalse(replay.source_changed)

            minimized = minimize_bundle(
                bundle,
                check_last_item_oversell,
                output_path=root / "minimized",
            )
            self.assertTrue(minimized.reproduced)
            self.assertLessEqual(
                minimized.minimized_choices,
                minimized.original_choices,
            )

            timeline = format_timeline(minimized.bundle_path)
            explain = format_explain(minimized.bundle_path)
            self.assertIn("THREAD_CREATE", timeline)
            self.assertIn("SCHEDULE", explain)
            self.assertIn("Integrity: verified", explain)

            fixed = run_check(
                FIXED_TARGET,
                exploration_plan=plan,
                bundle_root=root / "fixed",
                stop_on_failure=False,
            )
            self.assertTrue(fixed.passed)
            self.assertEqual(fixed.completed_runs, 64)


if __name__ == "__main__":
    unittest.main()
