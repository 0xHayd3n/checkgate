"""Subprocess, host-control, hook and packaging tests; no live model calls."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_host import Host
from benchmark import run_pairs
from build import build
from configure import configuration
from demo import FIXED, make_fixture
from hermes_hook import handle
from mcp_server import MAX_MESSAGE, PROTOCOL, StdioServer
from service import Service


def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


class ScriptedProvider:
    """Synthetic usage counters test accounting, never reported as model usage."""
    def __init__(self, replies, usage=True):
        self.replies = iter(replies)
        self.calls = 0
        self.has_usage = usage

    def complete(self, messages):
        self.calls += 1
        usage = {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 40} if self.has_usage else None
        return next(self.replies), usage


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-integration-", dir=Path(__file__).resolve().parent)
        self.base = Path(self.temporary.name).resolve()
        self.assertTrue(self.base.is_relative_to(Path(__file__).resolve().parent))
        self.root = make_fixture(self.base / "task", broken=True)
        self.service = Service(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def task(self):
        return self.service.controller().policy["task"]

    def fixed_actions(self):
        return {"actions": [action("write_file", path="port_parser.py", content=FIXED),
                            action("checkgate_run", check="unit"), action("checkgate_run", check="syntax")]}

    def finish(self):
        (self.root / "port_parser.py").write_text(FIXED, encoding="utf-8")
        self.service.call("checkgate_run", {"check": "unit"})
        self.service.call("checkgate_run", {"check": "syntax"})

    def exchange(self, requests, script=None):
        script = script or Path(__file__).with_name("mcp_server.py")
        mode = ["mcp"] if script.suffix == ".pyz" else []
        payload = b"".join((json.dumps(request) + "\n").encode() for request in requests)
        process = subprocess.run([sys.executable, "-B", str(script), *mode, "--root", str(self.root)],
                                 input=payload, capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
        return [json.loads(line) for line in process.stdout.splitlines()]

    @staticmethod
    def initialize():
        return [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2026-07-28", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]

    def test_mcp_process_negotiates_lists_and_calls_tools(self):
        self.finish()
        requests = self.initialize() + [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "checkgate_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "checkgate_run", "arguments": {"check": "unit"}}}]
        responses = self.exchange(requests)
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["result"]["protocolVersion"], PROTOCOL)
        self.assertEqual(len(responses[1]["result"]["tools"]), 2)
        self.assertEqual(responses[2]["result"]["structuredContent"]["decision"], "complete")
        self.assertFalse(responses[3]["result"]["structuredContent"]["executed"])

    def test_mcp_executes_failing_check_and_reports_error(self):
        response = self.exchange(self.initialize() + [{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "checkgate_run", "arguments": {"check": "unit"}}}])[-1]
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["result"], "failed")

    def test_mcp_rejects_calls_before_initialization(self):
        response = self.exchange([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])[0]
        self.assertEqual(response["error"]["code"], -32002)

    def test_mcp_unknown_tool_does_not_execute_arbitrary_code(self):
        result = self.exchange(self.initialize() + [{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "shell", "arguments": {"command": "invented"}}}])[-1]["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(result["structuredContent"]["executed"])

    def test_mcp_malformed_json_and_large_message_keep_clean_protocol(self):
        source = io.BytesIO(b"not-json\n" + b"x" * (MAX_MESSAGE + 1) + b"\n")
        destination = io.BytesIO()
        StdioServer(self.service).serve(source, destination)
        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual([item["error"]["code"] for item in responses], [-32700, -32600])

    def test_long_lived_service_reloads_changed_manifest(self):
        self.finish()
        self.assertEqual(self.service.call("checkgate_status")["decision"], "complete")
        path = self.root / "checkgate.json"
        policy = json.loads(path.read_text())
        policy["task"] = "A newly accepted task"
        path.write_text(json.dumps(policy))
        self.assertEqual(self.service.call("checkgate_status")["decision"], "continue")

    def test_host_stops_before_another_model_call_after_valid_batch(self):
        provider = ScriptedProvider([self.fixed_actions()])
        result = Host(self.root, provider).run(self.task())
        self.assertEqual(result["decision"], "complete")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["check_executions"], 2)
        self.assertEqual(result["usage"]["input_tokens"], 100)

    def test_baseline_continues_to_model_final_without_reuse(self):
        provider = ScriptedProvider([self.fixed_actions(), {"actions": [action("checkgate_run", check="unit")]}, {"final": "done"}])
        result = Host(self.root, provider, controlled=False).run(self.task())
        self.assertEqual(result["decision"], "complete")
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["check_executions"], 3)

    def test_host_rejects_premature_final(self):
        result = Host(self.root, ScriptedProvider([{"final": "done"}])).run(self.task())
        self.assertEqual(result["decision"], "premature_final")

    def test_later_write_in_same_batch_invalidates_earlier_pass(self):
        batch = self.fixed_actions()
        batch["actions"].append(action("write_file", path="port_parser.py", content=FIXED + "\n# edited later\n"))
        provider = ScriptedProvider([batch, {"final": "done"}])
        result = Host(self.root, provider).run(self.task())
        self.assertEqual(result["decision"], "premature_final")
        self.assertEqual(provider.calls, 2)

    def test_host_does_not_finish_new_invocation_from_old_evidence(self):
        self.finish()
        provider = ScriptedProvider([{"final": "done"}])
        result = Host(self.root, provider).run(self.task())
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["decision"], "premature_final")

    def test_host_rejects_a_different_task_without_rebinding(self):
        with self.assertRaises(ValueError):
            Host(self.root, ScriptedProvider([])).run("Add a new feature")

    def test_reused_host_requires_current_invocation_work(self):
        provider = ScriptedProvider([self.fixed_actions(), {"final": "done"}])
        host = Host(self.root, provider)
        first = host.run(self.task())
        self.assertEqual(first["decision"], "complete")
        second = host.run(self.task())
        self.assertEqual(second["decision"], "premature_final")
        self.assertEqual(second["model_calls"], 1)
        self.assertEqual(second["check_executions"], 0)
        self.assertEqual(second["trace"], [])

    def test_host_protects_tests_manifest_and_outside_paths(self):
        host = Host(self.root, ScriptedProvider([]))
        for path in ("test_port_parser.py", "checkgate.json", "../escape.py", str(self.base / "absolute.py")):
            with self.subTest(path=path):
                result = host.action(action("write_file", path=path, content="bad"))
                self.assertEqual(result["decision"], "blocked")

    def test_missing_usage_remains_unavailable(self):
        result = Host(self.root, ScriptedProvider([self.fixed_actions()], usage=False)).run(self.task())
        self.assertIsNone(result["usage"])
        self.assertFalse(result["usage_available"])

    def test_model_call_budget_stops_retry_loop(self):
        provider = ScriptedProvider([{"actions": [action("checkgate_status")]}] * 2)
        result = Host(self.root, provider, max_calls=2).run(self.task())
        self.assertEqual(result["decision"], "call_budget_exhausted")
        self.assertEqual(provider.calls, 2)

    def test_bound_hermes_turn_is_blocked_when_completed(self):
        self.finish()
        result = handle({"hook_event_name": "pre_llm_call", "extra": {"task_id": "accepted-task"}}, self.service, "accepted-task")
        self.assertEqual(result["action"], "block")

    def test_unrelated_hermes_turn_is_never_blocked_by_old_completion(self):
        self.finish()
        result = handle({"hook_event_name": "pre_llm_call", "extra": {"task_id": "new-task"}}, self.service, "old-task")
        self.assertEqual(result, {})

    def test_hook_process_json_contract(self):
        process = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("hermes_hook.py")),
                                  "--root", str(self.root), "--task-id", "bound"],
                                 input=json.dumps({"hook_event_name": "pre_llm_call", "extra": {"task_id": "bound"}}),
                                 text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("context", json.loads(process.stdout))

    def test_single_file_distribution_runs_cli_and_mcp(self):
        artifact = build(self.base / "checkgate.pyz")
        process = subprocess.run([sys.executable, "-B", str(artifact), "--root", str(self.root), "status"],
                                 text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["decision"], "continue")
        responses = self.exchange(self.initialize() + [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], artifact)
        self.assertEqual(len(responses[-1]["result"]["tools"]), 2)

    def test_config_uses_existing_python_and_fixed_root(self):
        config = configuration(self.root, hermes=True)
        entry = config["mcp_servers"]["checkgate"]
        self.assertEqual(entry["command"], sys.executable)
        self.assertIn(str(self.root), entry["args"])

    def test_completion_on_last_permitted_model_call(self):
        result = Host(self.root, ScriptedProvider([self.fixed_actions()]), max_calls=1).run(self.task())
        self.assertEqual(result["decision"], "complete")

    def test_malformed_model_response_stops_cleanly(self):
        result = Host(self.root, ScriptedProvider([{"actions": [], "final": "done"}])).run(self.task())
        self.assertEqual(result["decision"], "invalid_model_response")

    def test_malformed_evidence_records_fail_closed(self):
        controller = self.service.controller()
        controller.directory.mkdir()
        controller.save({"schema": 1, "records": {}, "events": ["not-a-record"]})
        self.assertEqual(self.service.call("checkgate_status")["decision"], "blocked")

    def test_hook_rejects_malformed_check_argument(self):
        result = handle({"hook_event_name": "pre_tool_call", "tool_name": "mcp_checkgate_checkgate_run",
                         "tool_input": {"check": []}, "extra": {"task_id": "bound"}}, self.service, "bound")
        self.assertEqual(result["action"], "block")

    def test_mcp_internal_error_keeps_request_identity(self):
        (self.root / "checkgate.json").write_text("broken JSON")
        responses = self.exchange(self.initialize() + [{"jsonrpc": "2.0", "id": 17, "method": "tools/list"}])
        self.assertEqual(responses[-1]["id"], 17)
        self.assertEqual(responses[-1]["error"]["code"], -32603)

    def test_paired_harness_runs_independent_evaluator_and_records_failures(self):
        factory = lambda: ScriptedProvider([self.fixed_actions(), {"final": "done"}])
        report = run_pairs(factory, self.base / "paired-results", trials=2)
        self.assertEqual(len(report["pairs"]), 2)
        self.assertTrue(all(pair["both_succeeded"] for pair in report["pairs"]))
        self.assertTrue((self.base / "paired-results" / "live-results.json").is_file())
        failed = run_pairs(lambda: ScriptedProvider([{"final": "done"}]), self.base / "failed-pair", trials=1)
        self.assertFalse(failed["pairs"][0]["both_succeeded"])
        self.assertNotIn("token_change_percent", failed["pairs"][0])


if __name__ == "__main__":
    unittest.main()
