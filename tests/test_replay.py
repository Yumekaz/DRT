#!/usr/bin/env python3
"""Tests for failure bundle replay helpers."""

import importlib
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drt.bundle import create_failure_bundle
from drt.replay import (
    load_bundle_metadata,
    load_source_hashes,
    replay_bundle,
    validate_source_hashes,
)
from drt.exceptions import DivergenceError, format_replay_failure
from drt.runtime import DRTRuntime


class ReplayTestCase(unittest.TestCase):
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
        module_name = f"replay_target_{len(self.module_names)}"
        module_path = self.root / f"{module_name}.py"
        module_path.write_text(textwrap.dedent(source), encoding="utf-8")
        self.module_names.append(module_name)
        importlib.invalidate_caches()
        module = importlib.import_module(module_name)
        return module_name, module_path, module

    def make_bundle(self, target_path, target, choices=(0, 1, 0)):
        trace_path = self.root / "source.log"
        DRTRuntime(mode="record", log_path=str(trace_path)).run(lambda: None)
        return create_failure_bundle(
            trace_path,
            self.root / "bundles",
            target_path=target_path,
            run_index=1,
            total_runs=1,
            failure_type="AssertionError",
            failure_message="boom",
            traceback_text="Traceback...\nAssertionError: boom",
            target=target,
            schedule={"choices": list(choices)},
        )


class TestBundleReplay(ReplayTestCase):
    def test_loads_metadata_sources_and_replays_bundle_target(self):
        module_name, module_path, module = self.write_module(
            """
            def target():
                assert False, "boom"
            """
        )
        bundle = self.make_bundle(f"{module_name}:target", module.target)

        metadata = load_bundle_metadata(bundle.path)
        source_hashes = load_source_hashes(bundle.path)
        drifts = validate_source_hashes(bundle.path)
        result = replay_bundle(bundle.path)

        self.assertEqual(metadata["target"]["path"], f"{module_name}:target")
        hashed_paths = {Path(entry["path"]) for entry in source_hashes["files"]}
        self.assertIn(module_path.resolve(), hashed_paths)
        self.assertEqual(drifts, [])
        self.assertTrue(result.reproduced)
        self.assertFalse(result.source_changed)
        self.assertEqual(result.source_drifts, ())
        self.assertEqual(result.target_path, f"{module_name}:target")
        self.assertEqual(result.expected_exception_type, "AssertionError")
        self.assertEqual(result.expected_exception_message, "boom")
        self.assertEqual(result.actual_exception_type, "AssertionError")
        self.assertEqual(result.actual_exception_message, "boom")
        self.assertEqual(result.schedule_choices, (0, 1, 0))
        self.assertIsNotNone(result.log_path)
        self.assertTrue(result.log_path.exists())

    def test_reports_source_drift_against_current_files(self):
        module_name, module_path, module = self.write_module(
            """
            def target():
                assert False, "boom"
            """
        )
        bundle = self.make_bundle(f"{module_name}:target", module.target)

        module_path.write_text(
            textwrap.dedent(
                """
                def target():
                    assert False, "boom"
                # changed after bundle capture
                """
            ),
            encoding="utf-8",
        )

        drifts = validate_source_hashes(bundle.path)
        result = replay_bundle(bundle.path)

        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].path, module_path.resolve())
        self.assertEqual(drifts[0].status, "changed")
        self.assertTrue(result.source_changed)
        self.assertEqual(len(result.source_drifts), 1)
        self.assertEqual(result.source_drifts[0].status, "changed")
        self.assertTrue(result.reproduced)

        report = format_replay_failure(
            DivergenceError(
                "Replay event mismatch",
                logical_time=4,
                expected="THREAD_JOIN thread=0",
                actual="THREAD_EXIT thread=1",
                event_index=7,
            ),
            source_changed=result.source_changed,
            source_drifts=result.source_drifts,
        )
        self.assertIn("Diverged at event 7", report)
        self.assertIn("logical time: 4", report)
        self.assertIn("expected: THREAD_JOIN thread=0", report)
        self.assertIn("actual:   THREAD_EXIT thread=1", report)
        self.assertIn("source changed: yes", report)
        self.assertIn(f"changed: {module_path.resolve()}", report)

    def test_replay_returns_false_when_failure_no_longer_matches(self):
        module_name, _, module = self.write_module(
            """
            def target():
                assert False, "different"
            """
        )
        bundle = self.make_bundle(f"{module_name}:target", module.target)

        result = replay_bundle(bundle.path)

        self.assertFalse(result.reproduced)
        self.assertEqual(result.expected_exception_message, "boom")
        self.assertEqual(result.actual_exception_message, "different")


if __name__ == "__main__":
    unittest.main()
