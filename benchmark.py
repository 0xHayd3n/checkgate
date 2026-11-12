"""Paired Codex subscription experiment. Dry preflight is the default.

Requires a saved ChatGPT login in Codex CLI. No API fallback or key handling.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from agent_host import Host
from demo import make_fixture
from providers import CodexProvider, ProviderError, preflight

EVALUATOR = '''import sys
sys.path.insert(0, sys.argv[1])
from port_parser import parse_port
for number in (1, 22, 80, 443, 8080, 65535):
    assert parse_port(number) == number
    assert parse_port(" \\t" + str(number) + "\\n ") == number
for invalid in (True, False, 0, -1, 65536, "", "  ", "1.2", "-1", "１２", None):
    try:
        parse_port(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(repr(invalid))
'''


def evaluate(root, environment):
    try:
        run = subprocess.run([sys.executable, "-B", "-c", EVALUATOR, str(root)], env=environment,
                             capture_output=True, timeout=10, shell=False)
    except subprocess.TimeoutExpired:
        return {"ran": True, "status": "timeout", "passed": False, "exit_code": None,
                "note": "Independent evaluation exceeded its 10-second timeout"}
    except OSError:
        return {"ran": False, "status": "not_run", "passed": None, "exit_code": None,
                "reason": "evaluator_start_error"}
    return {"ran": True, "status": "passed" if run.returncode == 0 else "failed",
            "passed": run.returncode == 0, "exit_code": run.returncode,
            "note": "Independent host assertions, outside measured agent check counts"}


def run_pairs(provider_factory, output, trials=3, max_calls=12, max_tokens=30000, *,
              report_name="live-results.json", report_metadata=None,
              fixture_factory=None, evaluator=None, retain_artifacts=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    pairs = []
    fixture_factory = fixture_factory or (lambda root: make_fixture(root, broken=True))
    evaluator = evaluator or evaluate
    with tempfile.TemporaryDirectory(prefix="live-fixtures-", dir=output) as temporary:
        base = Path(temporary).resolve()
        if not base.is_relative_to(output):
            raise RuntimeError("Fixture path escaped the output directory")
        for trial in range(trials):
            pair = {"trial": trial + 1}
            order = (False, True) if trial % 2 == 0 else (True, False)
            for controlled in order:
                name = "controller" if controlled else "baseline"
                root = fixture_factory(base / f"trial-{trial + 1}-{name}")
                provider = None
                try:
                    provider = provider_factory()
                    host = Host(root, provider, controlled=controlled, max_calls=max_calls, max_tokens=max_tokens)
                    result = host.run(host.service.controller().policy["task"])
                    result["independent_evaluation"] = evaluator(root, host.service.controller().env)
                except ProviderError as error:
                    result = {"decision": "provider_error", "error": str(error), "usage_available": False,
                              "model_calls": getattr(provider, "completed_turns", 0),
                              "cli_invocations_attempted": getattr(provider, "invocations", 0),
                              "independent_evaluation": {"ran": False, "status": "not_run", "passed": None,
                                                         "exit_code": None, "reason": "provider_error"}}
                if retain_artifacts:
                    artifact = output / "artifacts" / f"trial-{trial + 1}-{name}"
                    shutil.copytree(root, artifact, ignore=shutil.ignore_patterns(".checkgate", "__pycache__"))
                    result["artifact_directory"] = artifact.relative_to(output).as_posix()
                pair[name] = result
            pair["both_succeeded"] = all(pair[name]["decision"] == "complete" and pair[name]["independent_evaluation"]["passed"]
                                         for name in ("baseline", "controller"))
            if pair["both_succeeded"] and all(pair[name]["usage_available"] for name in ("baseline", "controller")):
                totals = {name: pair[name]["usage"]["input_tokens"] + pair[name]["usage"]["output_tokens"] for name in ("baseline", "controller")}
                pair["token_change_percent"] = round(100 * (totals["controller"] / totals["baseline"] - 1), 3) if totals["baseline"] else None
            pairs.append(pair)
            report = {**(report_metadata or {}), "pairs": pairs}
            (output / report_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {**(report_metadata or {}), "pairs": pairs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", help="Codex executable or the npm codex.js entry point")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-total-tokens", type=int, default=30000)
    parser.add_argument("--output", default="results/live")
    parser.add_argument("--live", action="store_true", help="Run models using the signed-in Codex subscription")
    args = parser.parse_args(argv)
    status = preflight(args.codex_bin)
    if not args.live or not status["ready"]:
        print(json.dumps(status))
        return 2 if args.live else 0
    if not 1 <= args.trials <= 10 or not 1 <= args.max_calls <= 30 or not 1 <= args.max_total_tokens <= 100000:
        parser.error("Benchmark limits are outside the allowed bounds")
    factory = lambda: CodexProvider(args.model, args.reasoning, binary=args.codex_bin, work_dir=Path(args.output) / "runtime")
    result = run_pairs(factory, args.output, args.trials, args.max_calls, args.max_total_tokens)
    result.update(live_model_run=any(pair[name].get("model_calls", 0) for pair in result["pairs"] for name in ("baseline", "controller")),
                  backend="codex_subscription", model=args.model, reasoning=args.reasoning, api_fallback=False,
                  measurement_note="CLI-reported tokens and wrapper rounds; no API billing or subscription-dollar conversion")
    (Path(args.output) / "live-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    successful_pairs = sum(pair["both_succeeded"] for pair in result["pairs"])
    print(json.dumps({"live_model_run": result["live_model_run"], "trials": len(result["pairs"]),
                      "successful_pairs": successful_pairs}))
    return 0 if successful_pairs == args.trials else 1


if __name__ == "__main__":
    raise SystemExit(main())
