"""Multi-task paired comparison using the signed-in Codex subscription."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

from benchmark import run_pairs
from providers import CodexProvider, preflight
from task_catalog import TASKS, BY_ID, make_task, evaluate_task


def summarize(reports):
    summary = {"task_types": len(reports), "pairs": 0, "successful_pairs": 0,
               "independent_evaluations_passed": 0, "per_task": []}
    for name in ("baseline", "controller"):
        summary[name] = {"runs": 0, "host_completed": 0, "independent_passes": 0,
                         "wrapper_rounds": 0, "real_check_executions": 0,
                         "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                         "runs_with_measured_usage": 0}
    paired_totals = {"baseline": 0, "controller": 0}
    measured_pairs = 0
    for report in reports:
        pairs = report["pairs"]
        summary["pairs"] += len(pairs)
        success = sum(pair["both_succeeded"] for pair in pairs)
        summary["successful_pairs"] += success
        row = {"id": report["task_id"], "category": report["category"],
               "pairs": len(pairs), "successful_pairs": success,
               "token_change_percent_by_pair": [p.get("token_change_percent") for p in pairs]}
        for name in ("baseline", "controller"):
            row[name] = []
            for pair in pairs:
                result = pair[name]
                totals = summary[name]
                totals["runs"] += 1
                totals["host_completed"] += result["decision"] == "complete"
                passed = result["independent_evaluation"].get("passed") is True
                totals["independent_passes"] += passed
                summary["independent_evaluations_passed"] += passed
                totals["wrapper_rounds"] += result.get("model_calls", 0)
                totals["real_check_executions"] += result.get("check_executions", 0)
                if result.get("usage_available"):
                    totals["runs_with_measured_usage"] += 1
                    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                        totals[key] += result["usage"][key]
                row[name].append({"decision": result["decision"], "independent_passed": passed,
                                  "rounds": result.get("model_calls", 0),
                                  "checks": result.get("check_executions", 0)})
        summary["per_task"].append(row)
        for pair in pairs:
            if pair["both_succeeded"] and all(pair[n].get("usage_available") for n in paired_totals):
                measured_pairs += 1
                for name in paired_totals:
                    paired_totals[name] += pair[name]["usage"]["input_tokens"] + pair[name]["usage"]["output_tokens"]
    summary["successful_measured_pairs"] = measured_pairs
    summary["successful_paired_input_plus_output_tokens"] = paired_totals
    summary["reported_token_reduction_percent_successful_pairs"] = (
        round(100 * (1 - paired_totals["controller"] / paired_totals["baseline"]), 3)
        if paired_totals["baseline"] else None)
    summary["limitations"] = [
        "Six small synthetic tasks with explicit acceptance criteria; not production repositories.",
        "Each wrapper round starts a fresh ephemeral Codex CLI; counts include CLI overhead and cached input.",
        "Reported tokens do not measure subscription allowance or dollar savings.",
        "Controller instruction, check reuse and host stopping are evaluated together.",
        "Concurrent task execution makes elapsed times unsuitable for a latency comparison.",
        "Failed and missing-usage runs remain in per-task reports; reduction uses successful measured pairs only.",
    ]
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-total-tokens", type=int, default=100000)
    parser.add_argument("--task", action="append", choices=sorted(BY_ID))
    parser.add_argument("--output", default="results/broad")
    parser.add_argument("--codex-bin")
    args = parser.parse_args(argv)
    if not 1 <= args.trials <= 4 or not 1 <= args.max_calls <= 20 or not 1 <= args.max_total_tokens <= 150000:
        parser.error("Benchmark bounds exceeded")
    status = preflight(args.codex_bin)
    selected = [t for t in TASKS if not args.task or t["id"] in args.task]
    if not args.live or not status["ready"]:
        print(json.dumps({**status, "tasks": [t["id"] for t in selected]}))
        return 2 if args.live else 0
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "plan.json").exists():
        parser.error("Use a fresh output directory to preserve prior trials")
    source = Path(__file__).resolve().parent
    hashes = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [source / "agent_host.py", source / "providers.py", source / "benchmark.py",
                        source / "task_catalog.py", source / "broad_benchmark.py",
                        *sorted((source / "task_sets").glob("*.py"))]}
    plan = {"backend": "codex_subscription", "model": args.model, "reasoning": args.reasoning,
            "api_fallback": False, "trials_per_task": args.trials, "workers": args.workers,
            "max_calls_per_arm": args.max_calls, "token_stopping_threshold_per_arm": args.max_total_tokens,
            "task_ids": [t["id"] for t in selected], "source_hashes": hashes,
            "order": "baseline/controller then controller/baseline within each task; no deliberate repeated-check instruction"}
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    def run_task(task):
        destination = output / task["id"]
        factory = lambda: CodexProvider(args.model, args.reasoning, binary=args.codex_bin,
                                       work_dir=destination / "runtime")
        return run_pairs(factory, destination, args.trials, args.max_calls, args.max_total_tokens,
                         fixture_factory=lambda root: make_task(root, task),
                         evaluator=lambda root, env: evaluate_task(root, env, task),
                         retain_artifacts=True,
                         report_metadata={"task_id": task["id"], "category": task["category"],
                                          "backend": "codex_subscription", "model": args.model,
                                          "reasoning": args.reasoning, "api_fallback": False})

    reports = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(run_task, task): task["id"] for task in selected}
        for future in as_completed(pending):
            reports.append(future.result())
            reports.sort(key=lambda r: r["task_id"])
            summary = summarize(reports)
            (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps({"task_completed": pending[future], "task_types_finished": len(reports),
                              "successful_pairs": summary["successful_pairs"], "pairs": summary["pairs"]}), flush=True)
    print(json.dumps(summary))
    return 0 if summary["successful_pairs"] == len(selected) * args.trials else 1


if __name__ == "__main__":
    raise SystemExit(main())
