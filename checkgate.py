"""Small local verification controller. Python standard library only.

The manifest and evidence directory are trusted host state, not agent claims.
This is an execution-evidence cache, not a proof that code is safe.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

VERSION = "0.2.0"
BASE_ENV = ("PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP")


class PolicyError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def controller_digest():
    # Both normal source installs and a stdlib zipapp expose this loader method.
    loader = globals().get("__loader__")
    contents = loader.get_data(__file__) if hasattr(loader, "get_data") else Path(__file__).read_bytes()
    return hashlib.sha256(contents).hexdigest()


class Controller:
    def __init__(self, root, evidence_dir=None):
        self.root = Path(root).resolve()
        self.directory = Path(evidence_dir).resolve() if evidence_dir else self.root / ".checkgate"
        self.state_path = self.directory / "evidence.json"
        try:
            self.policy = json.loads((self.root / "checkgate.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PolicyError("Cannot read checkgate.json") from error
        if not isinstance(self.policy, dict):
            raise PolicyError("The manifest must be a JSON object")
        checks = self.policy.get("checks", {})
        required = self.policy.get("required", [])
        if not isinstance(checks, dict) or not checks or not isinstance(required, list) or not required:
            raise PolicyError("A non-empty checks map and required list are needed")
        if any(not isinstance(name, str) or name not in checks for name in required):
            raise PolicyError("Required checks must be declared")
        for name, check in checks.items():
            if not isinstance(check, dict):
                raise PolicyError("Invalid check definition")
            for field in ("argv", "inputs"):
                values = check.get(field)
                if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
                    raise PolicyError(f"{name}: {field} must be a non-empty list of strings")
            for pattern in check["inputs"]:
                if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                    raise PolicyError("Input patterns must remain inside the project")
            timeout = check.get("timeout_seconds", 10)
            if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
                raise PolicyError("Timeout must be greater than zero and at most 60 seconds")
        self.max_age = self.policy.get("max_age_seconds", 3600)
        self.budget = self.policy.get("max_executions_per_check_snapshot", 6)
        if not isinstance(self.max_age, (int, float)) or not 0 < self.max_age <= 86400:
            raise PolicyError("Evidence lifetime must be between zero and one day")
        if not isinstance(self.budget, int) or not 1 <= self.budget <= 100:
            raise PolicyError("Execution budget must be between 1 and 100")
        extra_env = self.policy.get("environment_keys", [])
        if not isinstance(extra_env, list) or any(not isinstance(k, str) for k in extra_env):
            raise PolicyError("environment_keys must be a list of strings")
        self.env = {k: os.environ[k] for k in (*BASE_ENV, *extra_env) if k in os.environ}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")

    def command(self, name):
        if name not in self.policy["checks"]:
            raise PolicyError("Unknown check; arbitrary commands are not accepted")
        argv = [sys.executable if arg == "{python}" else arg for arg in self.policy["checks"][name]["argv"]]
        executable = shutil.which(argv[0], path=self.env.get("PATH"))
        if not executable:
            raise PolicyError("Check executable was not found")
        argv[0] = str(Path(executable).resolve())
        return argv

    def snapshot(self, name):
        check = self.policy["checks"][name]
        files = {}
        for pattern in check["inputs"]:
            matches = sorted(self.root.glob(pattern))
            if not matches:
                # Literal removals and empty globs change the snapshot too.
                files[f"missing:{pattern}"] = None
            for path in matches:
                resolved = path.resolve()
                if not resolved.is_relative_to(self.root):
                    raise PolicyError("An input resolves outside the project")
                if resolved.is_relative_to(self.directory):
                    raise PolicyError("Evidence files cannot be check inputs")
                if path.is_file():
                    files[path.relative_to(self.root).as_posix()] = file_digest(path)
        argv = self.command(name)
        payload = {
            "controller": VERSION,
            "controller_sha256": controller_digest(),
            "root": str(self.root),
            "policy": self.policy,
            "name": name,
            "files": files,
            "argv": argv,
            "executable_sha256": file_digest(Path(argv[0])),
            "python": sys.version,
            "platform": [platform.system(), platform.release(), platform.machine()],
            "environment": self.env,
        }
        return digest(payload)

    def load(self):
        if not self.state_path.exists():
            return {"schema": 1, "records": {}, "events": []}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("schema") != 1 or not isinstance(state.get("records"), dict) or not isinstance(state.get("events"), list):
                raise ValueError("Bad state schema")
            if any(not isinstance(item, dict) for item in state["records"].values()) or any(not isinstance(item, dict) for item in state["events"]):
                raise ValueError("Bad evidence record")
            return state
        except (OSError, ValueError, AttributeError) as error:
            raise PolicyError("Evidence is unreadable; refusing to accept it") from error

    def valid(self, record):
        try:
            age = time.time() - record["finished_at"]
            return record["passed"] is True and record["stable"] is True and record["exit_code"] == 0 and 0 <= age < self.max_age
        except (TypeError, KeyError):
            return False

    def status(self, state=None):
        state = self.load() if state is None else state
        checks = {}
        for name in self.policy["required"]:
            key = self.snapshot(name)
            checks[name] = "passed" if self.valid(state["records"].get(key, {})) else "pending"
        complete = all(value == "passed" for value in checks.values())
        return {"decision": "complete" if complete else "continue", "checks": checks,
                "executed": False, "assurance": "declared-checks-only"}

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / "runner.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise PolicyError("Runner busy; no concurrent check was started") from error
        try:
            os.close(descriptor)
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    def save(self, state):
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, sort_keys=True)
            os.replace(temporary, self.state_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def run(self, name, *, reuse=True):
        self.command(name)  # Validate before even creating local evidence state.
        if name not in self.policy["required"]:
            return {"decision": "blocked", "reason": "outside_task_scope", "executed": False, "check": name}
        with self.lock():
            state = self.load()
            before = self.snapshot(name)
            if reuse and self.valid(state["records"].get(before, {})):
                return {**self.status(state), "check": name, "reused": True}
            attempts = sum(event.get("fingerprint") == before for event in state["events"])
            if attempts >= self.budget:
                return {"decision": "blocked", "reason": "execution_budget_exhausted", "executed": False, "check": name}
            started = time.perf_counter()
            try:
                process = subprocess.run(self.command(name), cwd=self.root, env=self.env, shell=False,
                                         capture_output=True, timeout=self.policy["checks"][name].get("timeout_seconds", 10))
                code = process.returncode
                output = (process.stdout + process.stderr).decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                code, output = None, "Check timed out"
            except OSError as error:
                code, output = None, str(error)
            try:
                stable = before == Controller(self.root, self.directory).snapshot(name)
            except (PolicyError, OSError, ValueError):
                stable = False
            record = {"check": name, "fingerprint": before, "exit_code": code,
                      "stable": stable, "passed": code == 0 and stable,
                      "finished_at": time.time(), "seconds": time.perf_counter() - started,
                      "output_tail": output[-2000:]}
            state["records"][before] = record
            state["events"].append(record)
            self.save(state)
            status = self.status(state) if stable else {"decision": "continue", "checks": {}, "executed": False}
            return {**status, "check": name, "executed": True, "reused": False,
                    "result": "passed" if record["passed"] else "failed" if stable else "inputs_changed",
                    "exit_code": code, "output_tail": output[-2000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--evidence-dir", help="Host-controlled state directory (recommended)")
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("status")
    commands.add_parser("complete")
    run = commands.add_parser("run")
    run.add_argument("check")
    args = parser.parse_args()
    try:
        controller = Controller(args.root, args.evidence_dir)
        result = controller.run(args.check) if args.operation == "run" else controller.status()
    except (PolicyError, OSError, ValueError) as error:
        result = {"decision": "blocked", "executed": False, "reason": str(error)}
    print(json.dumps(result, separators=(",", ":")))
    return 2 if result["decision"] == "blocked" else 1 if result.get("result") in ("failed", "inputs_changed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
