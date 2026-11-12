"""Validate task fixtures, hidden acceptance and unbiased aggregate reporting."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmark import run_pairs
from broad_benchmark import summarize
from checkgate import Controller
from task_catalog import TASKS, make_task, evaluate_task


class BroadBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-broad-", dir=Path(__file__).resolve().parent)
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_each_task_rejects_initial_bug_and_accepts_reference(self):
        self.assertEqual(len(TASKS), 6)
        for task in TASKS:
            with self.subTest(task=task["id"]):
                root = make_task(self.base / task["id"], task)
                controller = Controller(root)
                self.assertEqual(controller.run("unit")["result"], "failed")
                self.assertFalse(evaluate_task(root, controller.env, task)["passed"])
                for name, content in task["reference_files"].items():
                    (root / name).write_text(content, encoding="utf-8")
                self.assertEqual(controller.run("unit")["result"], "passed")
                self.assertEqual(controller.run("syntax")["result"], "passed")
                self.assertTrue(evaluate_task(root, controller.env, task)["passed"])
                self.assertEqual(controller.status()["decision"], "complete")

    def test_oracles_and_reference_sources_are_not_in_agent_fixture(self):
        for task in TASKS:
            with self.subTest(task=task["id"]):
                root = make_task(self.base / task["id"], task)
                self.assertEqual(set(task["reference_files"]), set(task["editable_files"]))
                self.assertTrue(all(name in task["files"] for name in task["editable_files"]))
                expected = set(task["files"]) | {"syntax_check.py", "requirements.lock", "checkgate.json"}
                actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
                self.assertEqual(actual, expected)
                self.assertEqual(json.loads((root / "checkgate.json").read_text())["task"], task["task"])

    def test_generalized_pair_runner_retains_code_and_uses_task_oracle(self):
        task = TASKS[0]

        class ReferenceProvider:
            def __init__(self):
                self.step = 0

            def complete(self, _messages):
                self.step += 1
                if self.step == 1:
                    actions = [{"tool": "write_file", "arguments": {"path": n, "content": c}}
                               for n, c in task["reference_files"].items()]
                    actions += [{"tool": "checkgate_run", "arguments": {"check": c}} for c in ("unit", "syntax")]
                    return {"actions": actions}, None
                return {"final": "done"}, None

        output = self.base / "paired"
        report = run_pairs(ReferenceProvider, output, trials=1,
                           fixture_factory=lambda root: make_task(root, task),
                           evaluator=lambda root, env: evaluate_task(root, env, task), retain_artifacts=True)
        pair = report["pairs"][0]
        self.assertTrue(pair["both_succeeded"])
        self.assertNotIn("token_change_percent", pair)
        for name in ("baseline", "controller"):
            artifact = output / pair[name]["artifact_directory"]
            self.assertTrue((artifact / "checkgate.json").is_file())
            self.assertFalse((artifact / ".checkgate").exists())
            for path, content in task["reference_files"].items():
                self.assertEqual((artifact / path).read_text(encoding="utf-8"), content)

    def test_failed_pairs_stay_visible_and_do_not_improve_savings(self):
        def arm(passed, tokens):
            return {"decision": "complete", "independent_evaluation": {"passed": passed},
                    "model_calls": 2, "check_executions": 2, "usage_available": True,
                    "usage": {"input_tokens": tokens, "cached_input_tokens": 0, "output_tokens": 0}}
        good = {"both_succeeded": True, "baseline": arm(True, 100), "controller": arm(True, 80),
                "token_change_percent": -20}
        bad = {"both_succeeded": False, "baseline": arm(True, 100), "controller": arm(False, 1)}
        report = {"task_id": "example", "category": "test", "pairs": [good, bad]}
        summary = summarize([report])
        self.assertEqual(summary["pairs"], 2)
        self.assertEqual(summary["successful_pairs"], 1)
        self.assertEqual(summary["controller"]["runs"], 2)
        self.assertEqual(summary["controller"]["independent_passes"], 1)
        self.assertEqual(summary["controller"]["input_tokens"], 81)
        self.assertEqual(summary["reported_token_reduction_percent_successful_pairs"], 20)


if __name__ == "__main__":
    unittest.main()
