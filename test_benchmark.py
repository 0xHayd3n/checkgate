"""Benchmark result semantics; all providers are synthetic and use no inference."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import benchmark
from checkgate import Controller
from demo import make_fixture
from providers import ProviderError


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-benchmark-", dir=Path(__file__).resolve().parent)
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_independent_evaluation_records_actual_pass_and_assertion_failure(self):
        for broken, expected in ((False, True), (True, False)):
            with self.subTest(broken=broken):
                root = make_fixture(self.base / str(broken), broken=broken)
                result = benchmark.evaluate(root, Controller(root).env)
                self.assertTrue(result["ran"])
                self.assertIs(result["passed"], expected)
                self.assertEqual(result["status"], "passed" if expected else "failed")
                self.assertEqual(result["exit_code"] == 0, expected)

    def test_evaluation_timeout_is_a_failure_without_aborting_reporting(self):
        with patch("benchmark.subprocess.run", side_effect=subprocess.TimeoutExpired("evaluator", 10)):
            result = benchmark.evaluate(self.base, {})
        self.assertTrue(result["ran"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["exit_code"])

    def test_evaluator_start_error_is_not_an_assertion_failure(self):
        with patch("benchmark.subprocess.run", side_effect=OSError("private diagnostic")):
            result = benchmark.evaluate(self.base, {})
        self.assertFalse(result["ran"])
        self.assertIsNone(result["passed"])
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["reason"], "evaluator_start_error")
        self.assertNotIn("private diagnostic", json.dumps(result))

    def test_provider_error_does_not_claim_independent_assertions_failed(self):
        def unavailable():
            raise ProviderError("Subscription unavailable")

        with patch("benchmark.evaluate") as evaluator:
            report = benchmark.run_pairs(unavailable, self.base / "provider-failure", trials=1)
        evaluator.assert_not_called()
        pair = report["pairs"][0]
        self.assertFalse(pair["both_succeeded"])
        self.assertNotIn("token_change_percent", pair)
        for arm in ("baseline", "controller"):
            outcome = pair[arm]["independent_evaluation"]
            self.assertFalse(outcome["ran"])
            self.assertIsNone(outcome["passed"])
            self.assertEqual(outcome["status"], "not_run")
            self.assertEqual(outcome["reason"], "provider_error")

    def test_synthetic_run_can_write_only_explicitly_labeled_synthetic_report(self):
        class SyntheticProvider:
            def complete(self, _messages):
                return {"final": "synthetic premature final"}, None

        output = self.base / "synthetic"
        metadata = {"experiment": "synthetic provider replay", "live_model_run": False,
                    "model_tokens_measured": False}
        report = benchmark.run_pairs(SyntheticProvider, output, trials=1,
                                     report_name="synthetic-results.json", report_metadata=metadata)
        saved = json.loads((output / "synthetic-results.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, report)
        self.assertEqual(saved["experiment"], "synthetic provider replay")
        self.assertFalse(saved["live_model_run"])
        self.assertFalse((output / "live-results.json").exists())

    def test_live_cli_exit_reflects_every_requested_pair(self):
        for successes, expected_exit in ((2, 0), (1, 1), (0, 1)):
            with self.subTest(successes=successes):
                output = self.base / f"cli-{successes}"
                pairs = [{"both_succeeded": index < successes,
                          "baseline": {"model_calls": 1}, "controller": {"model_calls": 1}}
                         for index in range(2)]
                captured = io.StringIO()
                with patch("benchmark.preflight", return_value={"ready": True}), \
                     patch("benchmark.run_pairs", return_value={"pairs": pairs}), \
                     redirect_stdout(captured):
                    output.mkdir()
                    status = benchmark.main(["--live", "--trials", "2", "--output", str(output)])
                self.assertEqual(status, expected_exit)
                self.assertEqual(json.loads(captured.getvalue())["successful_pairs"], successes)
                saved = json.loads((output / "live-results.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["pairs"], pairs)
                self.assertFalse(saved["api_fallback"])


if __name__ == "__main__":
    unittest.main()
