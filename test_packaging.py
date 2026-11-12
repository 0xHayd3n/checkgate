"""Portable CLI and zipapp checks; no installed credentials or model calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from build import build
from checkgate import Controller
from configure import configuration
from entrypoint import doctor, initialize


SOURCE = Path(__file__).resolve().parent


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="checkgate-packaging-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "project with spaces"

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *args, artifact=None, input=None):
        launcher = artifact or SOURCE / "entrypoint.py"
        return subprocess.run([sys.executable, "-B", str(launcher), *map(str, args)],
                              cwd=self.base, input=input, capture_output=True,
                              text=True, encoding="utf-8", timeout=20, shell=False)

    def custom(self, **changes):
        options = {"task": "Verify the local source", "checks": [
            'unit=["{python}","-B","-c","print(42)"]'],
            "inputs": ["**/*.py"], "editable_files": ["app.py"]}
        options.update(changes)
        return initialize(self.root, **options)

    def test_custom_init_preserves_existing_project_and_command_arguments(self):
        self.root.mkdir()
        source = self.root / "app.py"
        source.write_text("value = 1\n", encoding="utf-8")
        self.custom()
        policy = json.loads((self.root / "checkgate.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["checks"]["unit"]["argv"], ["{python}", "-B", "-c", "print(42)"])
        self.assertEqual(policy["editable_files"], ["app.py"])
        self.assertEqual(source.read_text(encoding="utf-8"), "value = 1\n")

    def test_existing_manifest_is_never_overwritten(self):
        self.custom()
        manifest = self.root / "checkgate.json"
        accepted = manifest.read_bytes()
        with self.assertRaises(FileExistsError):
            self.custom(task="A replacement task")
        self.assertEqual(manifest.read_bytes(), accepted)

    def test_cli_accepts_json_argv_without_shell_interpretation(self):
        created = self.command("init", "--root", self.root, "--task", "Check the source",
                               "--check", 'unit=["{python}","-c","print(42)"]',
                               "--input", "**/*.py", "--editable", "app.py")
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        ran = self.command("run", "unit", "--root", self.root)
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertEqual(json.loads(ran.stdout)["result"], "passed")

    def test_custom_init_rejects_unsafe_or_incomplete_policy_before_writing(self):
        invalid = [
            {"task": ""}, {"checks": []}, {"inputs": []},
            {"inputs": ["../outside.py"]}, {"inputs": ["C:\\outside.py"]},
            {"editable_files": ["/outside.py"]}, {"editable_files": ["*.py"]},
            {"checks": ["unit=echo unsafe"]}, {"checks": ['unit=["python",2]']},
            {"checks": ['unit=[]']}, {"checks": ['unit=["python"]', 'unit=["python"]']},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.custom(**changes)
            self.assertFalse((self.root / "checkgate.json").exists())

    def test_demo_refuses_nonempty_directory_and_preserves_files(self):
        self.root.mkdir()
        existing = self.root / "port_parser.py"
        existing.write_text("my source\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            initialize(self.root, demo=True)
        self.assertEqual(existing.read_text(encoding="utf-8"), "my source\n")
        self.assertFalse((self.root / "checkgate.json").exists())

    def test_doctor_validates_without_executing_or_writing_evidence(self):
        self.custom()
        with patch.object(Controller, "run", side_effect=AssertionError("doctor must not run checks")):
            result = doctor(self.root)
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"]["decision"], "continue")
        self.assertFalse((self.root / ".checkgate").exists())

    def test_doctor_reports_unavailable_check_executable(self):
        self.custom(checks=['unit=["checkgate-nonexistent-program-742901"]'])
        result = self.command("doctor", "--root", self.root)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ready"])

    def test_legacy_and_command_first_root_orders_and_current_completion(self):
        created = self.command("init", "--root", self.root, "--demo")
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        status = self.command("--root", self.root, "status")
        self.assertEqual(json.loads(status.stdout)["decision"], "continue")
        for check in ("unit", "syntax"):
            result = self.command("run", check, "--root", self.root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)["result"], "passed")
        completed = self.command("complete", "--root", self.root)
        self.assertEqual(json.loads(completed.stdout)["decision"], "complete")

    def test_failed_check_cli_exit_is_nonzero(self):
        self.custom(checks=['unit=["{python}","-c","raise SystemExit(1)"]'])
        result = self.command("run", "unit", "--root", self.root)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "failed")

    def test_configuration_uses_installed_module_and_fixed_paths(self):
        config = configuration(self.root, hermes=True, evidence_dir=self.base / "evidence with spaces")
        server = config["mcp_servers"]["checkgate"]
        self.assertEqual(server["command"], sys.executable)
        self.assertEqual(server["args"][:3], ["-B", "-m", "mcp_server"])
        self.assertIn(str(self.root.resolve()), server["args"])
        self.assertIn(str((self.base / "evidence with spaces").resolve()), server["args"])

    def test_zipapp_is_self_contained_and_its_configuration_starts_mcp(self):
        artifact = build(self.base / "release with spaces" / "checkgate.pyz")
        with zipfile.ZipFile(artifact) as archive:
            self.assertEqual(set(archive.namelist()), {
                "__main__.py", "checkgate.py", "service.py", "mcp_server.py",
                "hermes_hook.py", "configure.py", "demo.py"})
        created = self.command("init", "--root", self.root, "--demo", artifact=artifact)
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        configured = self.command("config", "--root", self.root, artifact=artifact)
        self.assertEqual(configured.returncode, 0, configured.stderr + configured.stdout)
        server = json.loads(configured.stdout)["mcpServers"]["checkgate"]
        self.assertEqual(server["args"][:3], ["-B", str(artifact), "mcp"])
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "clientInfo": {"name": "packaging-test", "version": "1"},
                "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        process = subprocess.run([server["command"], *server["args"]],
                                 input="".join(json.dumps(request) + "\n" for request in requests),
                                 cwd=self.base, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([item["name"] for item in replies[-1]["result"]["tools"]],
                         ["checkgate_status", "checkgate_run"])


if __name__ == "__main__":
    unittest.main()
