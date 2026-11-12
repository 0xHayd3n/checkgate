"""Serial experiment correctness with real fixture checks and synthetic providers."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import optimization_benchmark as benchmark
from providers import ProviderError


TASK = {
    "id": "increment", "category": "synthetic_harness_fixture",
    "task": "Fix increment(value) to return value + 1 for every integer. Run unit and syntax.",
    "files": {
        "calc.py": "def increment(value):\n    return value\n",
        "test_calc.py": "import unittest\nfrom calc import increment\nclass Tests(unittest.TestCase):\n    def test_one(self):\n        self.assertEqual(increment(1), 2)\n",
    },
    "editable_files": ["calc.py"], "unit_modules": ["test_calc"],
    "reference_files": {"calc.py": "def increment(value):\n    return value + 1\n"},
    "oracle": "from calc import increment\nassert increment(-7) == -6\nassert increment(4) == 5\n",
}
USAGE = {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10, "cache_write_tokens": 0}


class SyntheticProvider:
    def __init__(self, *, usage=True, fail_second=False, premature=False, wrong=False, clock=None):
        self.usage = usage
        self.fail_second = fail_second
        self.premature = premature
        self.wrong = wrong
        self.calls = 0
        self.clock = clock

    def complete(self, messages):
        self.calls += 1
        if self.clock is not None:
            self.clock[0] += 1
        if self.fail_second and self.calls == 2:
            raise ProviderError("Synthetic provider failed after one measured round")
        counts = dict(USAGE) if self.usage else None
        if self.premature:
            return {"final": "Premature"}, counts
        preloaded = "Initial editable source data (JSON):" in messages[1]["content"]
        if not preloaded and self.calls == 1:
            return {"actions": [{"tool": "read_file", "arguments": {"path": "calc.py"}}]}, counts
        fixed = "def increment(value):\n    return 2\n" if self.wrong else TASK["reference_files"]["calc.py"]
        return {"actions": [
            {"tool": "write_file", "arguments": {"path": "calc.py", "content": fixed}},
            {"tool": "checkgate_run", "arguments": {"check": "unit"}},
            {"tool": "checkgate_run", "arguments": {"check": "syntax"}},
        ]}, counts


class OptimizationBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="optimization-tests-")
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_quietly(self, factory, output, **kwargs):
        with redirect_stdout(io.StringIO()):
            return benchmark.run_experiment(factory, output, [TASK], **kwargs)

    def test_four_variants_are_serial_checkpointed_frozen_and_fully_timed(self):
        output = self.base / "serial"
        clock = [0.0]
        calls = []
        original_fixture = benchmark.make_task
        original_evaluator = benchmark.evaluate_task
        original_copy = benchmark.shutil.copytree

        def fixture(*args, **kwargs):
            result = original_fixture(*args, **kwargs)
            clock[0] += 2
            return result

        def evaluate(*args, **kwargs):
            result = original_evaluator(*args, **kwargs)
            clock[0] += 5
            return result

        def copy(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            clock[0] += 7
            return result

        def factory():
            # A saved plan and exact definitions must precede even provider initialization.
            plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
            report = json.loads((output / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report["runs"]), len(calls))
            if report["runs"]:
                previous = report["runs"][-1]
                self.assertTrue((output / previous["artifact_directory"] / "calc.py").is_file())
                self.assertEqual(json.loads((output / "summary.json").read_text())["completed_arms"], len(calls))
            self.assertEqual(plan["task_definitions_sha256"], hashlib.sha256((output / "frozen/task_definitions.json").read_bytes()).hexdigest())
            calls.append(len(calls))
            clock[0] += 3
            return SyntheticProvider(clock=clock)

        snapshot = {"agent_host.py": b"# frozen host\n", "task_sets/example.py": b"TASKS = []\n"}
        with patch("optimization_benchmark.time.perf_counter", side_effect=lambda: clock[0]), \
             patch("optimization_benchmark.make_task", side_effect=fixture), \
             patch("optimization_benchmark.evaluate_task", side_effect=evaluate), \
             patch("optimization_benchmark.shutil.copytree", side_effect=copy):
            report = self.run_quietly(factory, output, trials=2, snapshot=snapshot,
                                     metadata={"live_model_run": False, "experiment": "synthetic"})
        expected = list(benchmark.WILLIAMS_ROWS[0]) + list(benchmark.WILLIAMS_ROWS[2])
        self.assertEqual([run["arm"] for run in report["runs"]], expected)
        self.assertEqual(len(calls), 8)
        self.assertEqual(report["status"], "complete")
        for run in report["runs"]:
            self.assertTrue(run["succeeded"])
            self.assertTrue(run["independent_evaluation"]["passed"])
            self.assertEqual(run["end_to_end_seconds"], run["seconds"] + 17)
            self.assertEqual(run["check_executions"], 2)
            self.assertTrue(run["host_options"]["controlled"])
            self.assertEqual(run["model_calls"], 1 if benchmark.ARMS[run["arm"]]["preload_sources"] else 2)
            artifact = output / run["artifact_directory"]
            self.assertEqual((artifact / "calc.py").read_text(), TASK["reference_files"]["calc.py"])
            self.assertFalse((artifact / ".checkgate").exists())
            self.assertFalse((artifact / "__pycache__").exists())
            self.assertFalse((artifact / "oracle.py").exists())
        for name, content in snapshot.items():
            self.assertEqual((output / "frozen/source" / name).read_bytes(), content)
        summary = report["summary"]
        self.assertEqual(summary["fully_successful_groups"], 2)
        comparisons = summary["fully_successful_matched"]["comparisons"]
        self.assertEqual(comparisons["optimized_vs_current"]["total_tokens"]["reduction_percent"], 50)
        self.assertEqual(comparisons["compact_only_vs_current"]["total_tokens"]["reduction_percent"], 0)
        self.assertEqual(summary["all_outcomes"]["current"]["measured_usage"]["tokens"]["uncached_input_tokens"]["total"], 320)
        self.assertEqual(summary["per_task"]["increment"]["fully_successful_groups"], 2)
        self.assertEqual(json.loads((output / "results.json").read_text()), report)

    def test_failures_and_missing_usage_stay_unknown_and_out_of_matched_savings(self):
        providers = iter((SyntheticProvider(fail_second=True), SyntheticProvider(usage=False, premature=True),
                          SyntheticProvider(), SyntheticProvider(usage=False)))
        report = self.run_quietly(lambda: next(providers), self.base / "unknown", trials=1)
        by_arm = {run["arm"]: run for run in report["runs"]}
        current, compact, preload, optimized = [by_arm[arm] for arm in benchmark.ARMS]
        self.assertEqual(current["decision"], "provider_error")
        self.assertIsNone(current["usage"])
        self.assertFalse(current["usage_available"])
        self.assertEqual(current["observed_partial_usage"], USAGE)
        self.assertEqual(current["unmeasured_or_failed_provider_rounds"], 1)
        self.assertEqual(current["model_calls"], 1)
        self.assertEqual(current["provider_calls_attempted"], 2)
        self.assertEqual(compact["decision"], "premature_final")
        self.assertTrue(preload["succeeded"])
        self.assertIsNone(preload["usage"])
        self.assertTrue(optimized["succeeded"])
        summary = report["summary"]
        self.assertEqual(len(summary["failures"]), 2)
        self.assertEqual(summary["fully_successful_groups"], 0)
        metric = summary["fully_successful_matched"]["comparisons"]["optimized_vs_current"]["total_tokens"]
        self.assertEqual(metric["matched_groups"], 0)
        self.assertIsNone(metric["reduction_percent"])
        self.assertEqual(summary["all_outcomes"]["current"]["measured_usage"]["runs"], 0)
        self.assertEqual(summary["all_outcomes"]["current"]["known_partial_usage"]["tokens"]["input_tokens"], 100)
        self.assertEqual(summary["all_outcomes"]["optimized"]["measured_usage"]["tokens"]["total_tokens"]["total"], 110)
        self.assertTrue(all(run["artifact_saved"] for run in report["runs"]))

    def test_oracle_failure_blocks_success_even_after_declared_checks_pass(self):
        report = self.run_quietly(lambda: SyntheticProvider(wrong=True), self.base / "oracle", trials=1)
        self.assertTrue(all(run["decision"] == "complete" for run in report["runs"]))
        self.assertTrue(all(run["check_executions"] == 2 for run in report["runs"]))
        self.assertTrue(all(run["independent_evaluation"]["passed"] is False for run in report["runs"]))
        self.assertEqual(report["summary"]["fully_successful_groups"], 0)
        self.assertEqual(len(report["summary"]["failures"]), 4)

    def test_successful_missing_usage_group_keeps_wall_comparison_but_no_token_claim(self):
        report = self.run_quietly(lambda: SyntheticProvider(usage=False), self.base / "no-usage", trials=1)
        self.assertEqual(report["summary"]["fully_successful_groups"], 1)
        comparison = report["summary"]["fully_successful_matched"]["comparisons"]["optimized_vs_current"]
        self.assertEqual(comparison["end_to_end_seconds"]["matched_groups"], 1)
        self.assertEqual(comparison["total_tokens"]["matched_groups"], 0)
        self.assertIsNone(comparison["total_tokens"]["reduction_percent"])

    def test_provider_initialization_failure_is_preserved_with_unknown_usage(self):
        def unavailable():
            raise ProviderError("Synthetic initialization failure")
        report = self.run_quietly(unavailable, self.base / "init-failure", trials=1)
        self.assertEqual(len(report["runs"]), 4)
        for run in report["runs"]:
            self.assertEqual(run["decision"], "provider_error")
            self.assertIsNone(run["usage"])
            self.assertFalse(run["usage_available"])
            self.assertTrue(run["artifact_saved"])

    def test_fresh_output_guard_prevents_overwriting_existing_evidence(self):
        output = self.base / "existing"
        output.mkdir()
        evidence = output / "keep.txt"
        evidence.write_text("prior evidence", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            self.run_quietly(SyntheticProvider, output, trials=1)
        self.assertEqual(evidence.read_text(), "prior evidence")

    def test_source_snapshot_contains_controller_provider_and_task_sources(self):
        snapshot = benchmark.source_snapshot(Path(benchmark.__file__).parent)
        for required in ("checkgate.py", "service.py", "providers.py", "agent_host.py", "task_catalog.py", "task_sets/algorithm_tasks.py"):
            self.assertIn(required, snapshot)
        self.assertTrue(all(name.endswith(".py") for name in snapshot))
        self.assertFalse(any("results/" in name for name in snapshot))

    def test_cli_defaults_to_dry_preflight_and_rejects_invalid_limits(self):
        with patch("optimization_benchmark.preflight", return_value={"ready": True}) as preflight, \
             patch("optimization_benchmark.run_experiment") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(benchmark.main([]), 0)
        preflight.assert_called_once_with(None)
        run.assert_not_called()
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            for arguments in (["--trials", "0"], ["--trials", "5"], ["--maxcalls", "0"], ["--maxtokens", "100001"]):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as raised:
                    benchmark.main(arguments)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
