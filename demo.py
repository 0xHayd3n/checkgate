"""Reproducible, scripted baseline/controller experiment; no model API calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from checkgate import Controller

BROKEN = '''import json
from pathlib import Path


def parse_port(value):
    """Accept integer ports 1..maximum and whitespace-padded ASCII digits."""
    maximum = json.loads(Path(__file__).with_name("settings.json").read_text())["maximum"]
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Expected an integer or ASCII decimal string")
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal():
            raise ValueError("Expected ASCII decimal digits")
    number = int(value)
    if not 1 <= number <= maximum:
        raise ValueError("Port outside permitted range")
    return number
'''
FIXED = BROKEN.replace("    if isinstance(value, str):\n", "    if isinstance(value, str):\n        value = value.strip()\n")
FIXTURE_TESTS = '''import unittest
from port_parser import parse_port


class PortTests(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(parse_port(" 443 "), 443)

    def test_boundaries(self):
        for value in (1, 65535, "80"):
            with self.subTest(value=value):
                self.assertEqual(parse_port(value), int(value))

    def test_invalid(self):
        for value in (0, 65536, -1, True, 1.5, "", "  ", "1.5", "-80", "１２"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    parse_port(value)


if __name__ == "__main__":
    unittest.main()
'''
SYNTAX = '''import ast
from pathlib import Path
for path in Path(".").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
'''
PROPOSALS = ["unit", "syntax"] * 5


def make_fixture(root, broken=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "port_parser.py").write_text(BROKEN if broken else FIXED, encoding="utf-8")
    (root / "test_port_parser.py").write_text(FIXTURE_TESTS, encoding="utf-8")
    (root / "syntax_check.py").write_text(SYNTAX, encoding="utf-8")
    (root / "settings.json").write_text('{"maximum":65535}\n', encoding="utf-8")
    (root / "requirements.lock").write_text("# Standard-library-only fixture; no external dependencies.\n", encoding="utf-8")
    inputs = ["*.py", "settings.json", "requirements.lock"]
    policy = {
        "task": "Fix whitespace handling without changing port validation",
        "required": ["unit", "syntax"],
        "editable_files": ["port_parser.py"],
        "max_age_seconds": 3600,
        "max_executions_per_check_snapshot": 3,
        "environment_keys": ["CHECKGATE_TEST_CONTEXT"],
        "checks": {
            "unit": {"argv": ["{python}", "-B", "-m", "unittest", "-q", "test_port_parser"], "inputs": inputs},
            "syntax": {"argv": ["{python}", "-B", "syntax_check.py"], "inputs": inputs},
        },
    }
    (root / "checkgate.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    return root


def exercise(output_dir):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="checkgate-demo-", dir=output_dir) as temporary:
        base = Path(temporary)
        if not base.resolve().is_relative_to(output_dir):
            raise RuntimeError("Disposable fixture escaped its output directory")
        root = make_fixture(base / "task", broken=True)
        controller = Controller(root)
        initial = controller.run("unit")
        if initial.get("result") != "failed":
            raise AssertionError("The initial bug must fail its regression check")
        (root / "port_parser.py").write_text(FIXED, encoding="utf-8")

        baseline_policy = Controller(root)
        baseline_trace = []
        baseline_started = time.perf_counter()
        for name in PROPOSALS:
            process = subprocess.run(baseline_policy.command(name), cwd=root, env=baseline_policy.env,
                                     shell=False, capture_output=True, timeout=10)
            baseline_trace.append({"check": name, "exit_code": process.returncode})
        baseline_seconds = time.perf_counter() - baseline_started
        if any(item["exit_code"] != 0 for item in baseline_trace):
            raise AssertionError("The fixed task must pass the baseline checks")

        controlled_trace = []
        controlled_started = time.perf_counter()
        # This driver emulates a host that stops requesting proposals at completion.
        for name in PROPOSALS:
            controller = Controller(root)  # Reload state like a separate tool call.
            if controller.status()["decision"] == "complete":
                break
            controlled_trace.append(controller.run(name))
        controlled_seconds = time.perf_counter() - controlled_started
        complete = Controller(root).status()
        if complete["decision"] != "complete":
            raise AssertionError("The controlled task must satisfy the same required checks")

        process = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("checkgate.py")),
                                  "--root", str(root), "run", "unit"], capture_output=True, text=True, timeout=10)
        cross_session = json.loads(process.stdout)
        if process.returncode or cross_session["executed"] or not cross_session.get("reused"):
            raise AssertionError("An unchanged task must reuse evidence in another process")

        (root / "port_parser.py").write_text(FIXED + "\n# A subsequent agent edited this file.\n", encoding="utf-8")
        after_change = Controller(root).status()
        rerun = Controller(root).run("unit")
        if after_change["decision"] != "continue" or not rerun["executed"]:
            raise AssertionError("A source edit must invalidate the previous check")
        executions = sum(item["executed"] for item in controlled_trace)
        results = {
            "experiment": "scripted replay; no live LLM or token measurements",
            "task": "Port parser whitespace bug; 3 regression test methods plus syntax check",
            "initial_bug_detected": initial["result"] == "failed",
            "proposed_checks": PROPOSALS,
            "baseline": {"executions": len(baseline_trace), "seconds": round(baseline_seconds, 6), "trace": baseline_trace},
            "controller": {"executions": executions, "seconds": round(controlled_seconds, 6),
                           "proposals_not_requested_after_completion": len(PROPOSALS) - len(controlled_trace),
                           "trace": controlled_trace, "decision": complete["decision"]},
            "check_executions_avoided_percent": round(100 * (1 - executions / len(baseline_trace)), 1),
            "separate_process_reuse": cross_session,
            "source_edit": {"status": after_change, "rerun_executed": rerun["executed"]},
            "model_tokens_measured": False,
            "blockchain_transactions": 0,
        }
    (output_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    report = f'''# Checkgate prototype experiment

This is a controlled scripted replay, not a live Sol benchmark.

- The original whitespace bug failed its regression test.
- Both paths used the same fixed code and the same required checks.
- Baseline: {len(baseline_trace)} real check subprocesses.
- Controller: {executions} real check subprocesses.
- Check executions avoided: {results["check_executions_avoided_percent"]}%.
- The host driver stopped requesting the remaining {len(PROPOSALS) - len(controlled_trace)} scripted proposals after completion.
- A separate CLI process reused the unchanged passing result.
- A subsequent source edit invalidated evidence and caused a real check execution.
- Baseline wall time: {baseline_seconds:.3f}s; controller wall time: {controlled_seconds:.3f}s. This single local run is not a performance benchmark.
- Measured model-token savings: unavailable; no inference API was called.
- Blockchain transactions: zero.

This demonstrates execution reuse and stopping under declared requirements. It does not prove arbitrary code is safe, establish the adequacy of those requirements, or show that Sol will independently choose fewer checks.

For a live experiment, integrate the same controller through a host hook and compare repeated trials using the same task, model, reasoning setting and completion requirements. Record billed input/cached/output tokens, required-check failures and task correctness, including controller overhead. The host must end completed runs rather than simply returning repeated denials to the model.
'''
    (output_dir / "results.md").write_text(report, encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results")
    parser.add_argument("--init-fixture", help="Create a runnable fixed sample task instead of running the experiment")
    arguments = parser.parse_args()
    if arguments.init_fixture:
        print(make_fixture(arguments.init_fixture).resolve())
    else:
        result = exercise(arguments.output)
        print(json.dumps({key: result[key] for key in ("experiment", "initial_bug_detected", "check_executions_avoided_percent", "model_tokens_measured", "blockchain_transactions")}))
