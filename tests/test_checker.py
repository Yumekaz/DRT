#!/usr/bin/env python3
"""Tests for check-runner internals."""

import importlib
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt.checker import load_target, run_check, runtime_supports_kwarg
from drt.log import EventLog
from drt.runtime import DRTRuntime


class CheckerTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.module_names = []
        sys.path.insert(0, str(self.root))

    def tearDown(self):
        try:
            sys.path.remove(str(self.root))
        except ValueError:
            pass
        for module_name in self.module_names:
            sys.modules.pop(module_name, None)
        self.tempdir.cleanup()

    def write_module(self, source):
        module_name = f"checker_target_{len(self.module_names)}"
        module_path = self.root / f"{module_name}.py"
        module_path.write_text(textwrap.dedent(source), encoding="utf-8")
        self.module_names.append(module_name)
        importlib.invalidate_caches()
        return module_name, module_path


class TestLoadTarget(CheckerTestCase):
    def test_load_target_resolves_module_function_and_nested_callable(self):
        module_name, _ = self.write_module(
            """
            VALUE = 7

            def target():
                return VALUE

            class Holder:
                @staticmethod
                def nested():
                    return "nested-ok"
            """
        )

        target = load_target(f"{module_name}:target")
        nested = load_target(f"{module_name}:Holder.nested")

        self.assertEqual(target(), 7)
        self.assertEqual(nested(), "nested-ok")

    def test_load_target_rejects_bad_paths_and_noncallables(self):
        module_name, _ = self.write_module("VALUE = 7\n")

        with self.assertRaises(ValueError):
            load_target(module_name)

        with self.assertRaises(TypeError):
            load_target(f"{module_name}:VALUE")


class TestRunCheck(CheckerTestCase):
    def test_run_check_passes_multiple_runs_without_bundles(self):
        module_name, _ = self.write_module(
            """
            def ok():
                return "ok"
            """
        )

        result = run_check(
            f"{module_name}:ok",
            runs=2,
            bundle_root=self.root / "bundles",
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.completed_runs, 2)
        self.assertEqual(len(result.runs), 2)
        self.assertIsNone(result.failure_bundle)
        self.assertTrue(all(run.success for run in result.runs))

    def test_run_check_creates_replayable_failure_bundle(self):
        module_name, module_path = self.write_module(
            """
            def fails():
                assert False, "boom"
            """
        )

        result = run_check(
            f"{module_name}:fails",
            runs=3,
            bundle_root=self.root / "bundles",
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.completed_runs, 1)
        failing_run = result.failing_run
        self.assertIsNotNone(failing_run)
        self.assertEqual(failing_run.exception_type, "AssertionError")
        self.assertIn("boom", failing_run.exception_message)

        bundle_path = result.failure_bundle
        self.assertIsNotNone(bundle_path)
        self.assertTrue((bundle_path / "trace.log").exists())
        self.assertTrue((bundle_path / "metadata.json").exists())
        self.assertTrue((bundle_path / "failure.txt").exists())
        self.assertTrue((bundle_path / "source_hashes.json").exists())

        log = EventLog(bundle_path / "trace.log")
        log.open_for_replay()
        self.assertTrue(log.is_complete)

        metadata = json.loads((bundle_path / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["target"]["path"], f"{module_name}:fails")
        self.assertEqual(metadata["run"]["index"], 1)
        self.assertEqual(metadata["run"]["total"], 3)
        self.assertEqual(metadata["failure"]["type"], "AssertionError")
        self.assertEqual(metadata["failure_origin"], "target")

        failure_text = (bundle_path / "failure.txt").read_text(encoding="utf-8")
        self.assertIn("AssertionError", failure_text)
        self.assertIn("boom", failure_text)

        source_hashes = json.loads(
            (bundle_path / "source_hashes.json").read_text(encoding="utf-8")
        )
        hashed_paths = {Path(entry["path"]) for entry in source_hashes["files"]}
        self.assertIn(module_path.resolve(), hashed_paths)

    def test_run_check_falls_back_when_runtime_lacks_schedule_kwargs(self):
        calls = []

        class MinimalRuntime:
            def __init__(self, mode="record", log_path="execution.log"):
                calls.append({"mode": mode, "log_path": log_path})

            def run(self, target):
                return target()

        result = run_check(
            lambda: None,
            runs=1,
            bundle_root=self.root / "bundles",
            schedule_strategy="random",
            schedule_seed=7,
            runtime_cls=MinimalRuntime,
        )

        self.assertTrue(result.passed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["mode"], "record")
        self.assertEqual(result.runs[0].applied_runtime_kwargs, {})
        self.assertEqual(
            result.runs[0].unsupported_runtime_kwargs,
            {"schedule_strategy": "random", "schedule_seed": 7},
        )

    def test_run_check_passes_incremented_seed_when_runtime_supports_it(self):
        if not (
            runtime_supports_kwarg(DRTRuntime, "schedule_strategy")
            and runtime_supports_kwarg(DRTRuntime, "schedule_seed")
        ):
            self.skipTest("DRTRuntime does not expose schedule kwargs")

        module_name, _ = self.write_module(
            """
            def ok():
                return None
            """
        )

        result = run_check(
            f"{module_name}:ok",
            runs=2,
            bundle_root=self.root / "bundles",
            schedule_strategy="round_robin",
            schedule_seed=100,
        )

        self.assertTrue(result.passed)
        self.assertEqual([run.schedule_seed for run in result.runs], [100, 101])
        self.assertEqual(
            [run.schedule_strategy for run in result.runs],
            ["round_robin", "round_robin"],
        )


if __name__ == "__main__":
    unittest.main()
