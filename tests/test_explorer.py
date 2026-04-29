#!/usr/bin/env python3
"""Tests for schedule exploration plan builders."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt.explorer import ScheduleRunSpec, build_schedule_plan


class TestSchedulePlanBuilder(unittest.TestCase):
    def test_round_robin_plan_uses_default_strategy(self):
        plan = build_schedule_plan(mode="round_robin", runs=2)

        self.assertEqual(len(plan), 2)
        self.assertEqual(
            [spec.runtime_kwargs() for spec in plan.runs],
            [
                {"schedule_strategy": "round_robin"},
                {"schedule_strategy": "round_robin"},
            ],
        )

    def test_exhaustive_count_and_depth(self):
        plan = build_schedule_plan(
            mode="exhaustive",
            branching_factor=3,
            depth=2,
        )

        self.assertEqual(len(plan), 9)
        self.assertEqual(plan.mode, "exhaustive")
        self.assertTrue(
            all(len(spec.schedule_choices) == 2 for spec in plan.runs)
        )
        self.assertEqual(plan.runs[0].schedule_choices, (0, 0))
        self.assertEqual(plan.runs[-1].schedule_choices, (2, 2))
        self.assertTrue(
            all(spec.schedule_strategy == "scripted" for spec in plan.runs)
        )

    def test_random_seed_stability(self):
        first = build_schedule_plan(mode="random", runs=4, seed=10)
        second = build_schedule_plan(mode="random", runs=4, seed=10)

        self.assertEqual(first.runs, second.runs)
        self.assertEqual(
            [spec.schedule_seed for spec in first.runs],
            [10, 11, 12, 13],
        )
        self.assertTrue(
            all(spec.schedule_strategy == "random" for spec in first.runs)
        )

    def test_stress_plan_is_seeded_and_capped(self):
        first = build_schedule_plan(mode="stress", runs=100, max_runs=5, seed=99)
        second = build_schedule_plan(mode="stress", runs=100, max_runs=5, seed=99)

        self.assertEqual(len(first), 5)
        self.assertEqual(first.runs, second.runs)
        self.assertEqual(first.max_runs, 5)
        self.assertTrue(
            all(spec.schedule_strategy == "random" for spec in first.runs)
        )

    def test_priority_uses_deterministic_scripted_choice_order(self):
        plan = build_schedule_plan(
            mode="priority",
            branching_factor=3,
            depth=2,
            priority_choices=[2, 0, 1],
            runs=4,
        )

        self.assertEqual(
            [spec.schedule_choices for spec in plan.runs],
            [(2, 2), (2, 0), (2, 1), (0, 2)],
        )
        self.assertEqual(
            [spec.runtime_kwargs() for spec in plan.runs],
            [
                {"schedule_strategy": "scripted", "schedule_choices": [2, 2]},
                {"schedule_strategy": "scripted", "schedule_choices": [2, 0]},
                {"schedule_strategy": "scripted", "schedule_choices": [2, 1]},
                {"schedule_strategy": "scripted", "schedule_choices": [0, 2]},
            ],
        )

    def test_run_spec_omits_empty_optional_kwargs(self):
        spec = ScheduleRunSpec(run_index=1, schedule_strategy="round_robin")

        self.assertEqual(spec.runtime_kwargs(), {"schedule_strategy": "round_robin"})


if __name__ == "__main__":
    unittest.main()
