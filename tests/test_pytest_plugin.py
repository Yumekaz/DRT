import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from drt import pytest_plugin


class FakeOptionGroup:
    def __init__(self):
        self.options = []

    def addoption(self, *names, **kwargs):
        self.options.append((names, kwargs))


class FakeParser:
    def __init__(self):
        self.group = FakeOptionGroup()

    def getgroup(self, name, description):
        self.group_name = name
        self.group_description = description
        return self.group


class FakeConfig:
    def __init__(self, **options):
        self.option = SimpleNamespace(**options)
        self.marker_lines = []

    def getoption(self, name):
        attr = name.lstrip("-").replace("-", "_")
        if hasattr(self.option, attr):
            return getattr(self.option, attr)
        raise ValueError(name)

    def addinivalue_line(self, name, value):
        self.marker_lines.append((name, value))


class FakeMarker:
    def __init__(self, name="drt", **kwargs):
        self.name = name
        self.kwargs = kwargs


class FakeRuntime:
    calls = []

    def __init__(
        self,
        mode,
        log_path,
        schedule_strategy=None,
        schedule_seed=None,
        schedule_choices=None,
        schedule_priorities=None,
    ):
        self.kwargs = {
            "mode": mode,
            "log_path": log_path,
            "schedule_strategy": schedule_strategy,
            "schedule_seed": schedule_seed,
            "schedule_choices": schedule_choices,
            "schedule_priorities": schedule_priorities,
        }
        self.scheduler = SimpleNamespace(recorded_schedule_choices=schedule_choices or [])
        FakeRuntime.calls.append(("init", self.kwargs))

    def run(self, target):
        FakeRuntime.calls.append(("run", self.kwargs["schedule_seed"]))
        return target()


class LegacyRuntime:
    calls = []

    def __init__(self, mode, log_path):
        self.kwargs = {"mode": mode, "log_path": log_path}
        LegacyRuntime.calls.append(("init", self.kwargs))

    def run(self, target):
        LegacyRuntime.calls.append(("run", None))
        return target()


class TestPytestPluginHelpers(unittest.TestCase):
    def test_adds_expected_options(self):
        parser = FakeParser()

        pytest_plugin._add_drt_options(parser)

        option_names = [names[0] for names, _ in parser.group.options]
        self.assertEqual(parser.group_name, "drt")
        self.assertIn("--drt", option_names)
        self.assertIn("--drt-runs", option_names)
        self.assertIn("--drt-strategy", option_names)
        self.assertIn("--drt-seed", option_names)
        self.assertIn("--drt-depth", option_names)
        self.assertIn("--drt-branching", option_names)
        self.assertIn("--drt-stress-max-runs", option_names)
        self.assertIn("--drt-bundle-dir", option_names)

    def test_configure_registers_marker(self):
        config = FakeConfig()

        pytest_plugin.pytest_configure(config)

        self.assertEqual(len(config.marker_lines), 1)
        self.assertEqual(config.marker_lines[0][0], "markers")
        self.assertIn("drt:", config.marker_lines[0][1])
        self.assertIn("schedules", config.marker_lines[0][1])

    def test_enablement_accepts_option_or_marker(self):
        option_item = SimpleNamespace(config=FakeConfig(drt=True), keywords={})
        marker_item = SimpleNamespace(
            config=FakeConfig(drt=False),
            get_closest_marker=lambda name: object() if name == "drt" else None,
        )
        disabled_item = SimpleNamespace(config=FakeConfig(drt=False), keywords={})

        self.assertTrue(pytest_plugin._is_drt_enabled(option_item))
        self.assertTrue(pytest_plugin._is_drt_enabled(marker_item))
        self.assertFalse(pytest_plugin._is_drt_enabled(disabled_item))

    def test_drt_test_decorator_marks_function_without_pytest_dependency(self):
        @pytest_plugin.drt_test(schedules=4, strategy="round_robin", seed=9, bundle_dir="custom")
        def sample_test():
            return None

        item = SimpleNamespace(obj=sample_test, config=FakeConfig(drt=False), keywords={})
        options = pytest_plugin._resolve_drt_options(item)

        self.assertTrue(pytest_plugin._is_drt_enabled(item))
        self.assertEqual(options.runs, 4)
        self.assertEqual(options.strategy, "round_robin")
        self.assertEqual(options.seed, 9)
        self.assertEqual(options.bundle_root, Path("custom"))

    def test_bare_drt_test_decorator_uses_public_defaults(self):
        def sample_test():
            return None

        decorated = pytest_plugin.drt_test(sample_test)
        item = SimpleNamespace(obj=decorated, config=FakeConfig(drt=False), keywords={})
        options = pytest_plugin._resolve_drt_options(item)

        self.assertTrue(pytest_plugin._is_drt_enabled(item))
        self.assertEqual(options.runs, 1000)
        self.assertEqual(options.strategy, "random")
        self.assertEqual(options.seed, 1)

    def test_decorator_does_not_clear_cli_bundle_dir_when_unset(self):
        @pytest_plugin.drt_test(schedules=3)
        def sample_test():
            return None

        item = SimpleNamespace(
            obj=sample_test,
            config=FakeConfig(drt=True, drt_bundle_dir="cli-bundles"),
            keywords={},
        )

        options = pytest_plugin._resolve_drt_options(item)

        self.assertEqual(options.runs, 3)
        self.assertEqual(options.bundle_root, Path("cli-bundles"))

    def test_marker_kwargs_override_cli_defaults_and_accept_runs_alias(self):
        item = SimpleNamespace(
            config=FakeConfig(drt=True, drt_runs=99, drt_bundle_dir="cli-bundles"),
            get_closest_marker=lambda name: FakeMarker(
                runs=5,
                strategy="priority",
                seed=20,
                depth=2,
                branching=3,
                bundle_dir="marker-bundles",
            )
            if name == "drt"
            else None,
        )

        options = pytest_plugin._resolve_drt_options(item)

        self.assertEqual(options.runs, 5)
        self.assertEqual(options.strategy, "priority")
        self.assertEqual(options.seed, 20)
        self.assertEqual(options.depth, 2)
        self.assertEqual(options.branching, 3)
        self.assertEqual(options.bundle_root, Path("marker-bundles"))

    def test_pyfunc_call_uses_marker_schedule_options(self):
        FakeRuntime.calls = []
        seen = []

        with tempfile.TemporaryDirectory() as tmpdir:
            def sample_test():
                seen.append("called")

            item = SimpleNamespace(
                obj=sample_test,
                funcargs={},
                _fixtureinfo=SimpleNamespace(argnames=[]),
                nodeid="tests/test_sample.py::test_case",
                config=FakeConfig(drt=False),
                get_closest_marker=lambda name: FakeMarker(
                    schedules=2,
                    strategy="priority",
                    depth=1,
                    branching=2,
                    seed=30,
                    bundle_dir=tmpdir,
                )
                if name == "drt"
                else None,
            )

            original_runtime = pytest_plugin.DRTRuntime
            try:
                pytest_plugin.DRTRuntime = FakeRuntime
                handled = pytest_plugin.pytest_pyfunc_call(item)
            finally:
                pytest_plugin.DRTRuntime = original_runtime

        init_calls = [payload for event, payload in FakeRuntime.calls if event == "init"]
        self.assertTrue(handled)
        self.assertEqual(seen, ["called", "called"])
        self.assertEqual(
            [call["schedule_strategy"] for call in init_calls],
            ["scripted", "scripted"],
        )
        self.assertEqual(
            [call["schedule_choices"] for call in init_calls],
            [[0], [1]],
        )

    def test_pyfunc_call_uses_exhaustive_schedule_plan(self):
        FakeRuntime.calls = []
        seen = []

        with tempfile.TemporaryDirectory() as tmpdir:
            def sample_test():
                seen.append("called")

            item = SimpleNamespace(
                obj=sample_test,
                funcargs={},
                _fixtureinfo=SimpleNamespace(argnames=[]),
                nodeid="tests/test_sample.py::test_case",
                config=FakeConfig(drt=False),
                get_closest_marker=lambda name: FakeMarker(
                    schedules=3,
                    strategy="exhaustive",
                    depth=2,
                    branching=2,
                    bundle_dir=tmpdir,
                )
                if name == "drt"
                else None,
            )

            original_runtime = pytest_plugin.DRTRuntime
            try:
                pytest_plugin.DRTRuntime = FakeRuntime
                handled = pytest_plugin.pytest_pyfunc_call(item)
            finally:
                pytest_plugin.DRTRuntime = original_runtime

        init_calls = [payload for event, payload in FakeRuntime.calls if event == "init"]
        self.assertTrue(handled)
        self.assertEqual(seen, ["called", "called", "called"])
        self.assertEqual(
            [call["schedule_strategy"] for call in init_calls],
            ["scripted", "scripted", "scripted"],
        )
        self.assertEqual(
            [call["schedule_choices"] for call in init_calls],
            [[0, 0], [0, 1], [1, 0]],
        )

    def test_schedule_kwargs_are_used_when_runtime_supports_them(self):
        kwargs = pytest_plugin._supported_schedule_kwargs(FakeRuntime, run_index=7)

        self.assertEqual(
            kwargs,
            {
                "schedule_strategy": pytest_plugin.DEFAULT_SCHEDULE_STRATEGY,
                "schedule_seed": 7,
            },
        )

    def test_schedule_kwargs_fall_back_for_legacy_runtime(self):
        self.assertEqual(
            pytest_plugin._supported_schedule_kwargs(LegacyRuntime, run_index=7),
            {},
        )

    def test_run_under_drt_repeats_test_with_incrementing_seeds(self):
        FakeRuntime.calls = []
        seen = []

        with tempfile.TemporaryDirectory() as tmpdir:
            pytest_plugin._run_under_drt(
                lambda: seen.append("called"),
                nodeid="tests/test_example.py::test_flaky",
                runs=3,
                bundle_root=Path(tmpdir),
                runtime_cls=FakeRuntime,
            )

        init_calls = [payload for event, payload in FakeRuntime.calls if event == "init"]
        self.assertEqual(seen, ["called", "called", "called"])
        self.assertEqual([call["schedule_seed"] for call in init_calls], [1, 2, 3])
        self.assertTrue(all(call["schedule_strategy"] == "random" for call in init_calls))

    def test_minimal_failure_bundle_is_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_root = Path(tmpdir)
            log_path = bundle_root / "runs" / "sample.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_bytes(b"partial-log")

            try:
                raise AssertionError("boom")
            except AssertionError as exc:
                bundle_dir = pytest_plugin._create_failure_bundle(
                    bundle_root=bundle_root,
                    nodeid="tests/test_sample.py::test_case[param]",
                    run_index=2,
                    log_path=log_path,
                    exc=exc,
                )

            metadata = json.loads((bundle_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["nodeid"], "tests/test_sample.py::test_case[param]")
            self.assertEqual(metadata["run"]["index"], 2)
            self.assertEqual(metadata["schedule"]["applied"]["schedule_seed"], 2)
            self.assertEqual((bundle_dir / "trace.log").read_bytes(), b"partial-log")
            self.assertIn("AssertionError: boom", (bundle_dir / "failure.txt").read_text())
            self.assertTrue((bundle_dir / "source_hashes.json").exists())
            self.assertTrue((bundle_dir / "schedule_choices.json").exists())

    def test_pyfunc_call_returns_none_when_disabled(self):
        item = SimpleNamespace(config=FakeConfig(drt=False), keywords={})

        self.assertIsNone(pytest_plugin.pytest_pyfunc_call(item))


if __name__ == "__main__":
    unittest.main()
