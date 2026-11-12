"""Focused behavioural tests for the verification controller's boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from checkgate import Controller, PolicyError
from demo import FIXED, make_fixture


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-test-", dir=Path(__file__).resolve().parent)
        self.assertTrue(Path(self.temporary.name).resolve().is_relative_to(Path(__file__).resolve().parent))
        self.root = make_fixture(Path(self.temporary.name) / "task")

    def tearDown(self):
        self.temporary.cleanup()

    def controller(self):
        return Controller(self.root)

    def update_policy(self, change):
        path = self.root / "checkgate.json"
        policy = json.loads(path.read_text())
        change(policy)
        path.write_text(json.dumps(policy), encoding="utf-8")

    def complete(self):
        self.assertTrue(self.controller().run("unit")["executed"])
        result = self.controller().run("syntax")
        self.assertEqual(result["decision"], "complete")

    def test_failure_is_not_cached_as_pass_and_fix_reruns(self):
        make_fixture(self.root, broken=True)
        result = self.controller().run("unit")
        self.assertEqual(result["result"], "failed")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertEqual(self.controller().status()["checks"]["unit"], "pending")
        (self.root / "port_parser.py").write_text(FIXED, encoding="utf-8")
        self.assertEqual(self.controller().run("unit")["result"], "passed")

    def test_completion_requires_both_checks_and_repeated_checks_do_not_execute(self):
        self.assertEqual(self.controller().run("unit")["decision"], "continue")
        self.assertEqual(self.controller().run("syntax")["decision"], "complete")
        result = self.controller().run("unit")
        self.assertFalse(result["executed"])
        self.assertTrue(result["reused"])
        self.assertEqual(len(self.controller().load()["events"]), 2)

    def test_separate_cli_process_reuses_persisted_evidence(self):
        self.complete()
        process = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("checkgate.py")),
                                  "--root", str(self.root), "run", "unit"], capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertFalse(result["executed"])
        self.assertTrue(result["reused"])

    def test_source_test_configuration_and_dependency_edits_invalidate(self):
        self.complete()
        for name in ("port_parser.py", "test_port_parser.py", "settings.json", "requirements.lock"):
            with self.subTest(file=name):
                path = self.root / name
                previous = path.read_bytes()
                path.write_bytes(previous + b"\n")
                self.assertEqual(self.controller().status()["decision"], "continue")
                self.assertTrue(self.controller().run("unit")["executed"])
                path.write_bytes(previous)
                self.assertEqual(self.controller().status()["decision"], "complete")

    def test_new_file_matching_declared_glob_invalidates(self):
        self.complete()
        (self.root / "new_module.py").write_text("VALUE = 1\n")
        self.assertEqual(self.controller().status()["decision"], "continue")

    def test_manifest_command_change_invalidates(self):
        self.complete()
        self.update_policy(lambda p: p["checks"]["unit"]["argv"].insert(1, "-I"))
        self.assertEqual(self.controller().status()["decision"], "continue")

    def test_selected_environment_change_invalidates(self):
        with patch.dict(os.environ, {"CHECKGATE_TEST_CONTEXT": "first"}):
            self.complete()
        with patch.dict(os.environ, {"CHECKGATE_TEST_CONTEXT": "second"}):
            self.assertEqual(self.controller().status()["decision"], "continue")

    def test_expired_evidence_is_not_reused(self):
        self.complete()
        controller = self.controller()
        state = controller.load()
        for record in state["records"].values():
            record["finished_at"] = time.time() - 7200
        controller.save(state)
        self.assertEqual(self.controller().status()["decision"], "continue")
        self.assertTrue(self.controller().run("unit")["executed"])

    def test_unknown_check_is_rejected_without_starting_a_process(self):
        with patch("checkgate.subprocess.run") as runner:
            with self.assertRaises(PolicyError):
                self.controller().run("invented-test-suite")
            runner.assert_not_called()

    def test_declared_but_out_of_scope_check_is_blocked(self):
        self.update_policy(lambda p: p["checks"].update(extra={"argv": ["{python}", "-c", "raise Exception('must not run')"], "inputs": ["*.py"]}))
        self.complete()
        result = self.controller().run("extra")
        self.assertEqual(result["reason"], "outside_task_scope")
        self.assertFalse(result["executed"])

    def test_inputs_changed_during_successful_command_cannot_be_cached(self):
        script = "from pathlib import Path; p=Path('port_parser.py'); p.write_text(p.read_text()+'\\n# changed while running\\n')"
        self.update_policy(lambda p: p["checks"]["unit"].update(argv=["{python}", "-c", script]))
        result = self.controller().run("unit")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["result"], "inputs_changed")
        self.assertEqual(self.controller().status()["checks"]["unit"], "pending")

    def test_repeated_failure_is_bounded(self):
        self.update_policy(lambda p: p["checks"]["unit"].update(argv=["{python}", "-c", "raise SystemExit(1)"]))
        for _ in range(3):
            self.assertTrue(self.controller().run("unit")["executed"])
        result = self.controller().run("unit")
        self.assertEqual(result["reason"], "execution_budget_exhausted")
        self.assertFalse(result["executed"])

    def test_timeout_does_not_satisfy_completion(self):
        self.update_policy(lambda p: p["checks"]["unit"].update(argv=["{python}", "-c", "import time; time.sleep(2)"], timeout_seconds=0.05))
        result = self.controller().run("unit")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(result["result"], "failed")
        self.assertEqual(self.controller().status()["decision"], "continue")

    def test_corrupt_evidence_fails_closed(self):
        controller = self.controller()
        controller.directory.mkdir()
        controller.state_path.write_text("not JSON")
        with self.assertRaises(PolicyError):
            controller.status()

    def test_outside_project_paths_are_rejected(self):
        self.update_policy(lambda p: p["checks"]["unit"].update(inputs=["../outside.py"]))
        with self.assertRaises(PolicyError):
            self.controller()

    def test_execution_lock_prevents_a_second_runner(self):
        first = self.controller()
        with first.lock():
            with self.assertRaises(PolicyError):
                self.controller().run("unit")
        self.assertFalse((first.directory / "runner.lock").exists())


if __name__ == "__main__":
    unittest.main()
