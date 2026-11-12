"""Prompt optimization and unchanged acceptance rules; no live inference."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent_host
from agent_host import Host
from checkgate import PolicyError
from demo import BROKEN, FIXED, make_fixture
from service import Service


SOURCE_MARKER = "\nInitial editable source data (JSON): "


def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


class RecordingProvider:
    """Captures exact requests before Host mutates its message list."""
    def __init__(self, replies=None, usage=None):
        self.replies = iter(replies or [{"final": "captured"}])
        self.usage = usage
        self.requests = []

    def complete(self, messages):
        self.requests.append(json.loads(json.dumps(messages)))
        return next(self.replies), self.usage


class SourceDrivenProvider(RecordingProvider):
    """Repairs the supplied source; requests it first when not preloaded."""
    def complete(self, messages):
        self.requests.append(json.loads(json.dumps(messages)))
        source = None
        if SOURCE_MARKER in messages[1]["content"]:
            source = json.loads(messages[1]["content"].split(SOURCE_MARKER, 1)[1])["files"].get("port_parser.py")
        if source is None and messages[-1]["content"].startswith("Tool results: "):
            source = json.loads(messages[-1]["content"].removeprefix("Tool results: "))[0].get("content")
        if source is None:
            return {"actions": [action("read_file", path="port_parser.py")]}, None
        repaired = source.replace("    if isinstance(value, str):\n",
                                  "    if isinstance(value, str):\n        value = value.strip()\n")
        return {"actions": [action("write_file", path="port_parser.py", content=repaired),
                            action("checkgate_run", check="unit"),
                            action("checkgate_run", check="syntax")]}, None


class HostOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-host-options-",
                                                      dir=Path(__file__).resolve().parent)
        self.base = Path(self.temporary.name).resolve()
        self.root = make_fixture(self.base / "task", broken=True)

    def tearDown(self):
        self.temporary.cleanup()

    def policy(self):
        return json.loads((self.root / "checkgate.json").read_text(encoding="utf-8"))

    def update_policy(self, **changes):
        policy = self.policy()
        policy.update(changes)
        (self.root / "checkgate.json").write_text(json.dumps(policy), encoding="utf-8")
        return policy

    def capture(self, **options):
        provider = RecordingProvider()
        host = Host(self.root, provider, **options)
        result = host.run(self.policy()["task"])
        return host, provider, result

    @staticmethod
    def sources(provider):
        return json.loads(provider.requests[0][1]["content"].split(SOURCE_MARKER, 1)[1])

    @staticmethod
    def fixed_batch():
        return {"actions": [action("write_file", path="port_parser.py", content=FIXED),
                            action("checkgate_run", check="unit"),
                            action("checkgate_run", check="syntax")]}

    def explicit_check_inputs(self):
        policy = self.policy()
        for check in policy["checks"].values():
            check["inputs"] = ["port_parser.py", "test_port_parser.py", "syntax_check.py",
                               "settings.json", "requirements.lock"]
        self.update_policy(checks=policy["checks"])

    def test_defaults_preserve_existing_full_manifest_prompt_and_no_source_preload(self):
        policy = self.policy()
        _, provider, result = self.capture()
        initial = provider.requests[0]
        self.assertEqual(initial[1]["content"], policy["task"] + "\nTask manifest: "
                         + json.dumps(policy, separators=(",", ":")))
        self.assertEqual(initial[0]["content"], agent_host.INSTRUCTIONS
                         + "\nThe host reuses current evidence and ends this task after an edit and all required checks pass.")
        self.assertNotIn(SOURCE_MARKER, initial[1]["content"])
        self.assertEqual(result["host_options"], {"preload_sources": False, "compact_brief": False})
        self.assertFalse(result["preload"]["enabled"])
        self.assertEqual(result["preload"]["files"], [])
        self.assertEqual(result["preload"]["source_bytes"], 0)

    def test_preload_contains_only_declared_editable_sources_and_labels_them_as_data(self):
        markers = {"test_port_parser.py": "PROTECTED_TEST_CONTENT_9137",
                   "oracle.py": "HELD_OUT_ORACLE_CONTENT_9137",
                   "private.txt": "SECRET_FILE_CONTENT_9137"}
        for name, marker in markers.items():
            path = self.root / name
            with path.open("a", encoding="utf-8") as output:
                output.write("\n# " + marker + "\n")
        _, provider, result = self.capture(preload_sources=True, compact_brief=True)
        payload = self.sources(provider)
        self.assertEqual(payload["files"], {"port_parser.py": BROKEN})
        self.assertEqual(payload["omitted"], [])
        self.assertEqual(result["preload"]["files"], ["port_parser.py"])
        self.assertEqual(result["preload"]["source_bytes"], len((self.root / "port_parser.py").read_bytes()))
        serialized = json.dumps(provider.requests[0])
        for marker in markers.values():
            self.assertNotIn(marker, serialized)
        self.assertIn("untrusted file data", provider.requests[0][0]["content"])
        self.assertIn("read_file", provider.requests[0][0]["content"])

    def test_utf8_preload_reports_bytes_and_matches_normal_read_newlines(self):
        raw = (BROKEN + '\n# caf\u00e9, "quoted" and \\backslash\n').replace("\n", "\r\n").encode("utf-8")
        (self.root / "port_parser.py").write_bytes(raw)
        host, provider, result = self.capture(preload_sources=True)
        preloaded = self.sources(provider)["files"]["port_parser.py"]
        read = host.action(action("read_file", path="port_parser.py"))
        self.assertEqual(preloaded, read["content"])
        self.assertEqual(result["preload"]["source_bytes"], len(raw))
        self.assertNotEqual(len(raw), len(preloaded))

    def test_missing_and_oversized_sources_are_explicitly_omitted_without_truncation(self):
        oversized = "# LARGE_BEGIN\n" + "x" * agent_host.MAX_FILE + "\n# LARGE_END\n"
        (self.root / "large.py").write_text(oversized, encoding="utf-8")
        self.update_policy(editable_files=["port_parser.py", "missing.py", "large.py"])
        host, provider, result = self.capture(preload_sources=True)
        payload = self.sources(provider)
        self.assertEqual(payload["files"], {"port_parser.py": BROKEN})
        omitted = {item["path"]: item["reason"] for item in result["preload"]["omitted"]}
        self.assertEqual(set(omitted), {"missing.py", "large.py"})
        self.assertTrue(all(omitted.values()))
        self.assertEqual(payload["omitted"], result["preload"]["omitted"])
        self.assertNotIn("LARGE_BEGIN", provider.requests[0][1]["content"])
        self.assertNotIn("LARGE_END", provider.requests[0][1]["content"])
        self.assertEqual(host.action(action("read_file", path="large.py"))["decision"], "blocked")

    def test_aggregate_limit_omits_whole_files_and_normal_read_can_fetch_an_omission(self):
        contents = {name: "# " + name + " BEGIN\n# " + "x" * (100 * 1024) + "\n# " + name + " END\n"
                    for name in ("first.py", "second.py", "third.py")}
        for name, content in contents.items():
            (self.root / name).write_text(content, encoding="utf-8")
        self.update_policy(editable_files=list(contents))
        host, provider, result = self.capture(preload_sources=True)
        payload = self.sources(provider)
        info = result["preload"]
        self.assertEqual(info["per_file_limit_bytes"], 128 * 1024)
        self.assertEqual(info["total_limit_bytes"], 256 * 1024)
        self.assertEqual(len(payload["files"]), 2)
        self.assertLessEqual(info["source_bytes"], info["total_limit_bytes"])
        self.assertEqual(info["source_bytes"], sum((self.root / name).stat().st_size for name in payload["files"]))
        for name, content in payload["files"].items():
            self.assertEqual(content, contents[name])
        omitted = info["omitted"]
        self.assertEqual(len(omitted), 1)
        missing_name = omitted[0]["path"]
        self.assertNotIn(missing_name, payload["files"])
        self.assertTrue(omitted[0]["reason"])
        self.assertEqual(host.action(action("read_file", path=missing_name))["content"], contents[missing_name])

    def test_actual_read_stays_bounded_when_file_size_grows_after_stat(self):
        target = self.root / "grew.py"
        target.write_bytes(b"x" * (agent_host.MAX_FILE + 500))
        self.update_policy(editable_files=["grew.py"])
        original_stat = Path.stat
        def stale_stat(path, *args, **kwargs):
            actual = original_stat(path, *args, **kwargs)
            if path == target:
                values = list(actual)
                values[6] = 1
                return os.stat_result(values)
            return actual
        with patch.object(Path, "stat", stale_stat):
            _, provider, result = self.capture(preload_sources=True)
        self.assertEqual(self.sources(provider)["files"], {})
        self.assertEqual(result["preload"]["source_bytes"], 0)
        self.assertEqual(result["preload"]["omitted"][0]["path"], "grew.py")
        self.assertTrue(result["preload"]["omitted"][0]["reason"])

    def test_preload_rejects_traversal_absolute_and_evidence_paths(self):
        outside = self.base / "outside.py"
        outside.write_text("OUTSIDE_SECRET_CONTENT_8412", encoding="utf-8")
        invalid = ["../outside.py", str(outside), ".checkgate/evidence.json"]
        self.update_policy(editable_files=["port_parser.py", *invalid])
        host, provider, result = self.capture(preload_sources=True, compact_brief=True)
        self.assertEqual(self.sources(provider)["files"], {"port_parser.py": BROKEN})
        self.assertEqual({item["path"] for item in result["preload"]["omitted"]}, set(invalid))
        self.assertNotIn("OUTSIDE_SECRET_CONTENT_8412", json.dumps(provider.requests[0]))
        for name in invalid:
            with self.subTest(path=name):
                self.assertEqual(host.action(action("read_file", path=name))["decision"], "blocked")

    def test_preload_rejects_symlink_resolving_outside_project(self):
        outside_directory = self.base / "outside"
        outside_directory.mkdir()
        outside = outside_directory / "source.py"
        outside.write_text("SYMLINK_OUTSIDE_SECRET_3412", encoding="utf-8")
        link = self.root / "linked"
        try:
            link.symlink_to(outside_directory, target_is_directory=True)
        except OSError as error:
            if os.name != "nt":
                self.skipTest("OS does not permit creating a test symlink: " + str(error))
            # Windows directory junctions exercise the same resolved-path escape
            # without requiring the optional symbolic-link privilege. Both
            # endpoints stay inside this test's temporary directory.
            process = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside_directory)],
                                     capture_output=True, timeout=5,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                self.skipTest("OS permits neither a symlink nor a directory junction for this test")
        self.explicit_check_inputs()
        self.update_policy(editable_files=["port_parser.py", "linked/source.py"])
        host, provider, result = self.capture(preload_sources=True)
        self.assertEqual(self.sources(provider)["files"], {"port_parser.py": BROKEN})
        self.assertEqual(result["preload"]["omitted"][0]["path"], "linked/source.py")
        self.assertNotIn("SYMLINK_OUTSIDE_SECRET_3412", json.dumps(provider.requests[0]))
        self.assertEqual(host.action(action("read_file", path="linked/source.py"))["decision"], "blocked")

    def test_compact_brief_contains_task_once_and_excludes_host_commands_hashes_and_cache_policy(self):
        policy = self.update_policy(host_private_hash="HOST_HASH_SECRET_4912")
        _, provider, _ = self.capture(compact_brief=True)
        initial = provider.requests[0]
        brief = json.loads(initial[1]["content"].removeprefix("Task brief: "))
        self.assertEqual(brief, {"task": policy["task"], "required": policy["required"],
                                 "editable_files": policy["editable_files"]})
        self.assertEqual(json.dumps(initial).count(policy["task"]), 1)
        serialized = json.dumps(initial)
        for key in ("argv", "inputs", "max_age_seconds", "max_executions_per_check_snapshot",
                    "environment_keys", "host_private_hash", "HOST_HASH_SECRET_4912", "syntax_check.py"):
            self.assertNotIn(key, serialized)

    def test_preloading_and_compact_brief_are_independently_optional(self):
        for options in ({"preload_sources": True}, {"compact_brief": True}):
            with self.subTest(options=options):
                _, provider, result = self.capture(**options)
                request = provider.requests[0][1]["content"]
                self.assertEqual(SOURCE_MARKER in request, options.get("preload_sources", False))
                self.assertEqual(request.startswith("Task brief: "), options.get("compact_brief", False))
                self.assertEqual(result["host_options"], {"preload_sources": options.get("preload_sources", False),
                                                         "compact_brief": options.get("compact_brief", False)})

    def test_character_diagnostics_are_exact_serialized_counts_and_not_provider_tokens(self):
        usage = {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3}
        provider = RecordingProvider([{"actions": [action("read_file", path="port_parser.py")]},
                                      {"final": "captured"}], usage=usage)
        result = Host(self.root, provider, preload_sources=True, compact_brief=True).run(self.policy()["task"])
        counts = [len(json.dumps(request)) for request in provider.requests]
        self.assertEqual(result["initial_input_chars"], counts[0])
        self.assertEqual(result["model_input_chars_by_request"], counts)
        self.assertEqual(result["model_input_chars"], sum(counts))
        self.assertEqual(result["usage"]["input_tokens"], 14)
        self.assertEqual(result["usage"]["output_tokens"], 6)
        self.assertNotEqual(result["model_input_chars"], result["usage"]["input_tokens"])
        _, _, unmeasured = self.capture(preload_sources=True, compact_brief=True)
        self.assertFalse(unmeasured["usage_available"])
        self.assertIsNone(unmeasured["usage"])

    def test_preloaded_source_enables_one_call_repair_while_current_host_reads_then_repairs(self):
        results = {}
        for optimized in (False, True):
            root = make_fixture(self.base / ("optimized" if optimized else "current"), broken=True)
            provider = SourceDrivenProvider()
            result = Host(root, provider, max_calls=2, preload_sources=optimized,
                          compact_brief=optimized).run(self.policy()["task"])
            self.assertEqual(result["decision"], "complete")
            self.assertEqual(result["check_executions"], 2)
            self.assertEqual((root / "port_parser.py").read_text(encoding="utf-8"), FIXED)
            self.assertFalse(result["usage_available"])
            results[optimized] = result
        self.assertEqual(results[False]["model_calls"], 2)
        self.assertEqual(results[True]["model_calls"], 1)
        self.assertEqual(results[False]["trace"][0]["tool"], "read_file")
        self.assertNotIn("read_file", [item["tool"] for item in results[True]["trace"]])

    def test_optimized_host_still_requires_an_edit_in_current_invocation(self):
        (self.root / "port_parser.py").write_text(FIXED, encoding="utf-8")
        service = Service(self.root)
        for check in ("unit", "syntax"):
            self.assertEqual(service.call("checkgate_run", {"check": check})["result"], "passed")
        _, _, result = self.capture(preload_sources=True, compact_brief=True)
        self.assertEqual(result["decision"], "premature_final")
        self.assertFalse(result["edited"])
        self.assertEqual(result["check_executions"], 0)

    def test_optimized_host_cannot_finish_without_all_required_checks(self):
        provider = RecordingProvider([{"actions": [action("write_file", path="port_parser.py", content=FIXED),
                                                   action("checkgate_run", check="unit")]}, {"final": "done"}])
        result = Host(self.root, provider, preload_sources=True, compact_brief=True).run(self.policy()["task"])
        self.assertEqual(result["decision"], "premature_final")
        self.assertTrue(result["edited"])
        self.assertEqual(result["check_executions"], 1)

    def test_optimized_host_cannot_finish_with_failed_required_check(self):
        batch = self.fixed_batch()
        batch["actions"][0]["arguments"]["content"] = BROKEN + "\n# an edit that leaves the bug\n"
        provider = RecordingProvider([batch, {"final": "done"}])
        result = Host(self.root, provider, preload_sources=True, compact_brief=True).run(self.policy()["task"])
        self.assertEqual(result["decision"], "premature_final")
        self.assertEqual(result["trace"][1]["result"]["result"], "failed")

    def test_late_write_still_invalidates_checks_in_same_optimized_batch(self):
        batch = self.fixed_batch()
        batch["actions"].append(action("write_file", path="port_parser.py", content=FIXED + "\n# later write\n"))
        provider = RecordingProvider([batch, {"final": "done"}])
        result = Host(self.root, provider, preload_sources=True, compact_brief=True).run(self.policy()["task"])
        self.assertEqual(result["decision"], "premature_final")
        self.assertEqual(result["model_calls"], 2)

    def test_optimized_host_preserves_write_protection_and_task_binding(self):
        host = Host(self.root, RecordingProvider(), preload_sources=True, compact_brief=True)
        for name in ("test_port_parser.py", "checkgate.json", "../escape.py", str(self.base / "absolute.py")):
            with self.subTest(path=name):
                self.assertEqual(host.action(action("write_file", path=name, content="bad"))["decision"], "blocked")
        with self.assertRaises(PolicyError):
            host.run("A different, unaccepted task")


if __name__ == "__main__":
    unittest.main()
