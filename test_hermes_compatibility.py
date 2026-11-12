"""Hermes hook wire-contract checks with real local evidence; no inference."""
from pathlib import Path
import tempfile
import unittest

from demo import FIXED, make_fixture
from hermes_hook import handle
from service import Service


class HermesCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-hermes-compat-",
                                                      dir=Path(__file__).resolve().parent)
        self.root = make_fixture(Path(self.temporary.name) / "task", broken=True)
        self.service = Service(self.root)
        self.task_id = "explicitly-bound-task"

    def tearDown(self):
        self.temporary.cleanup()

    def payload(self, name, arguments=None, task_id=None):
        return {"hook_event_name": "pre_tool_call", "tool_name": name,
                "tool_input": {"check": "unit"} if arguments is None else arguments,
                "extra": {"task_id": self.task_id if task_id is None else task_id}}

    def passing_unit(self):
        (self.root / "port_parser.py").write_text(FIXED, encoding="utf-8")
        result = self.service.call("checkgate_run", {"check": "unit"})
        self.assertEqual(result["result"], "passed")

    def test_current_and_legacy_names_block_repeating_current_passing_check(self):
        self.passing_unit()
        for name in ("mcp__checkgate__checkgate_run", "mcp_checkgate_checkgate_run"):
            with self.subTest(name=name):
                result = handle(self.payload(name), self.service, self.task_id)
                self.assertEqual(result["action"], "block")
                self.assertIn("current passing evidence", result["message"])

    def test_both_names_allow_check_again_after_source_changes(self):
        self.passing_unit()
        (self.root / "port_parser.py").write_text(FIXED + "\n# source changed\n", encoding="utf-8")
        for name in ("mcp__checkgate__checkgate_run", "mcp_checkgate_checkgate_run"):
            with self.subTest(name=name):
                self.assertEqual(handle(self.payload(name), self.service, self.task_id), {})

    def test_both_names_reject_malformed_check_arguments(self):
        for name in ("mcp__checkgate__checkgate_run", "mcp_checkgate_checkgate_run"):
            for arguments in ({"check": []}, {"check": "unit", "command": "invented"}, {}):
                with self.subTest(name=name, arguments=arguments):
                    self.assertEqual(handle(self.payload(name, arguments), self.service, self.task_id)["action"], "block")

    def test_task_binding_and_other_servers_are_preserved(self):
        self.passing_unit()
        self.assertEqual(handle(self.payload("mcp__checkgate__checkgate_run", task_id="another-task"),
                                self.service, self.task_id), {})
        self.assertEqual(handle(self.payload("mcp__another_server__checkgate_run"), self.service, self.task_id), {})

    def test_malformed_tool_name_does_not_crash_the_hook(self):
        for name in (None, [], {}, 1):
            with self.subTest(name=name):
                self.assertEqual(handle(self.payload(name), self.service, self.task_id), {})

    def test_pre_turn_block_requires_complete_current_evidence_and_exact_binding(self):
        self.passing_unit()
        payload = {"hook_event_name": "pre_llm_call", "extra": {"task_id": self.task_id}}
        self.assertIn("context", handle(payload, self.service, self.task_id))
        self.service.call("checkgate_run", {"check": "syntax"})
        self.assertEqual(handle(payload, self.service, self.task_id)["action"], "block")
        payload["extra"]["task_id"] = "a-new-task"
        self.assertEqual(handle(payload, self.service, self.task_id), {})


if __name__ == "__main__":
    unittest.main()
