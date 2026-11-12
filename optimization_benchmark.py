"""Serial four-variant subscription experiment. Without --live, only preflight runs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import tempfile
import time

from agent_host import Host
from checkgate import Controller
from providers import CodexProvider, ProviderError, preflight
from task_catalog import TASKS, evaluate_task, make_task


ARMS = {
    "current": {"preload_sources": False, "compact_brief": False},
    "compact_only": {"preload_sources": False, "compact_brief": True},
    "preload_only": {"preload_sources": True, "compact_brief": False},
    "optimized": {"preload_sources": True, "compact_brief": True},
}
WILLIAMS_ROWS = (
    ("current", "compact_only", "optimized", "preload_only"),
    ("compact_only", "preload_only", "current", "optimized"),
    ("preload_only", "optimized", "compact_only", "current"),
    ("optimized", "current", "preload_only", "compact_only"),
)
COMPARISONS = (
    ("optimized", "current"), ("compact_only", "current"),
    ("preload_only", "current"), ("optimized", "preload_only"),
)
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def write_json(path, value):
    """Replace checkpoints atomically so interruption leaves the prior checkpoint valid."""
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pending.replace(path)


def source_snapshot(source_root):
    """Capture source bytes once; CLI calls this before any experiment runs."""
    source_root = Path(source_root).resolve()
    paths = list(source_root.glob("*.py")) + list((source_root / "task_sets").rglob("*.py"))
    return {path.relative_to(source_root).as_posix(): path.read_bytes()
            for path in sorted(paths) if "__pycache__" not in path.parts}


class ObservedProvider:
    """Keep only known completed-round measurements if a later provider call fails."""
    def __init__(self, provider):
        self.provider = provider
        self.attempted = 0
        self.completed = 0
        self.measured_rounds = 0
        self.known_usage = {key: 0 for key in TOKEN_KEYS}
        self.call_seconds = []

    def complete(self, messages):
        self.attempted += 1
        started = time.perf_counter()
        try:
            reply, usage = self.provider.complete(messages)
            self.completed += 1
            if usage is not None:
                self.measured_rounds += 1
                for key in TOKEN_KEYS:
                    self.known_usage[key] += usage.get(key, 0)
            return reply, usage
        finally:
            self.call_seconds.append(time.perf_counter() - started)


def distribution(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return {"count": len(values), "total": sum(values),
            "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None}


def token_counts(usage):
    return {**usage, "uncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
            "total_tokens": usage["input_tokens"] + usage["output_tokens"]}


def aggregate_arm(runs):
    measured = [run for run in runs if run.get("usage_available") is True and run.get("usage") is not None]
    partial = [run for run in runs if not run.get("usage_available") and run.get("observed_partial_usage")]
    counts = [token_counts(run["usage"]) for run in measured]
    return {
        "runs": len(runs), "successful_runs": sum(run.get("succeeded", False) for run in runs),
        "failed_runs": sum(not run.get("succeeded", False) for run in runs),
        "end_to_end_seconds": distribution(run.get("end_to_end_seconds") for run in runs),
        "host_seconds": distribution(run.get("seconds") for run in runs),
        "model_calls": distribution(run.get("model_calls") for run in runs),
        "check_executions": distribution(run.get("check_executions") for run in runs),
        "model_input_chars": distribution(run.get("model_input_chars") for run in runs),
        "timing_breakdown": {
            key: distribution(run.get(key) for run in runs)
            for key in ("fixture_create_seconds", "provider_init_seconds", "host_run_elapsed_seconds",
                        "provider_call_seconds", "evaluation_seconds", "artifact_save_seconds")
        },
        "measured_usage": {
            "runs": len(measured), "unknown_or_partial_runs": len(runs) - len(measured),
            "tokens": {key: distribution(item[key] for item in counts)
                       for key in (*TOKEN_KEYS, "uncached_input_tokens", "total_tokens")},
        },
        "known_partial_usage": {
            "runs": len(partial),
            "note": "Known completed-round tokens only; missing or failed-round usage remains unknown.",
            "tokens": {key: sum(run["observed_partial_usage"].get(key, 0) for run in partial)
                       for key in TOKEN_KEYS},
        },
    }


def compare_values(groups, optimized, reference, field):
    pairs = []
    for group in groups:
        opt, ref = group[optimized], group[reference]
        if field == "total_tokens":
            if not all(run.get("usage_available") is True and run.get("usage") is not None for run in group.values()):
                continue
            values = [token_counts(run["usage"])["total_tokens"] for run in (opt, ref)]
        else:
            values = [run.get(field) for run in (opt, ref)]
            if any(value is None for value in values):
                continue
        pairs.append(values)
    opt_values = [pair[0] for pair in pairs]
    ref_values = [pair[1] for pair in pairs]
    opt_total, ref_total = sum(opt_values), sum(ref_values)
    return {"matched_groups": len(pairs), "optimized": distribution(opt_values),
            "reference": distribution(ref_values),
            "reduction_percent": 100 * (1 - opt_total / ref_total) if ref_total else None}


def summarize_runs(runs, planned_groups):
    grouped = {}
    for run in runs:
        grouped.setdefault((run["task_id"], run["trial"]), {})[run["arm"]] = run
    completed = [group for group in grouped.values() if set(group) == set(ARMS)]
    successful = [group for group in completed if all(run.get("succeeded") for run in group.values())]
    matched_runs = [run for group in successful for run in group.values()]
    return {
        "planned_groups": planned_groups, "completed_arms": len(runs),
        "completed_groups": len(completed), "fully_successful_groups": len(successful),
        "failures": [{"task_id": run["task_id"], "trial": run["trial"], "arm": run["arm"],
                      "decision": run["decision"], "independent_evaluation": run["independent_evaluation"]}
                     for run in runs if not run.get("succeeded")],
        "all_outcomes": {arm: aggregate_arm([run for run in runs if run["arm"] == arm]) for arm in ARMS},
        "fully_successful_matched": {
            "groups": len(successful),
            "arms": {arm: aggregate_arm([run for run in matched_runs if run["arm"] == arm]) for arm in ARMS},
            "comparisons": {
                optimized + "_vs_" + reference: {
                    field: compare_values(successful, optimized, reference, field)
                    for field in ("end_to_end_seconds", "seconds", "total_tokens", "model_calls", "check_executions")
                } for optimized, reference in COMPARISONS
            },
        },
    }


def summarize(runs, tasks, trials):
    summary = summarize_runs(runs, len(tasks) * trials)
    summary["per_task"] = {
        task["id"]: summarize_runs([run for run in runs if run["task_id"] == task["id"]], trials)
        for task in tasks
    }
    return summary


def run_experiment(provider_factory, output, tasks, trials=2, max_calls=12, max_tokens=30000,
                   *, metadata=None, snapshot=None):
    """Run fresh fixtures serially; provider_factory is called without arguments per arm."""
    experiment_started = time.perf_counter()
    if not 1 <= trials <= 4 or not 1 <= max_calls <= 30 or not 1 <= max_tokens <= 100000:
        raise ValueError("Limits require trials 1..4, max_calls 1..30, max_tokens 1..100000")
    tasks = json.loads(canonical(list(tasks)))
    if not tasks or len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("Provide at least one task with unique identifiers")
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Experiment output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    definitions = canonical(tasks)
    frozen = output / "frozen"
    frozen.mkdir()
    (frozen / "task_definitions.json").write_bytes(definitions)
    hashes = {}
    for name, contents in (snapshot or {}).items():
        target = (frozen / "source" / name).resolve()
        if not target.is_relative_to(frozen.resolve()):
            raise ValueError("Snapshot filename escaped its destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        hashes[name] = digest(contents)
    schedule = []
    for task_index, task in enumerate(tasks):
        for trial_index in range(trials):
            row_index = (task_index + 2 * (trial_index % 2) + trial_index // 2) % 4
            schedule.append({"task_id": task["id"], "trial": trial_index + 1,
                             "williams_row": row_index, "order": list(WILLIAMS_ROWS[row_index])})
    plan = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment": "serial four-variant controlled host optimization comparison",
        "metadata": metadata or {}, "trials_per_task": trials, "task_ids": [task["id"] for task in tasks],
        "task_definitions_sha256": digest(definitions), "task_definitions_file": "frozen/task_definitions.json",
        "source_hashes": hashes, "source_snapshot_directory": "frozen/source" if snapshot else None,
        "variants": {arm: {"controlled": True, **flags} for arm, flags in ARMS.items()},
        "max_calls": max_calls, "max_tokens": max_tokens, "schedule": schedule,
        "order_description": "Four-row Williams counterbalance; row=(task_index+2*(trial_index%2)+(trial_index//2))%4 with zero-based indices. Two trials give each task a row and its reverse; six tasks/two trials give each row three uses and balance current/optimized order 6/6.",
        "execution": "Strictly serial; one arm completes evaluation and artifact retention before the next starts.",
        "timing": "end_to_end_seconds includes fixture creation, provider initialization, host run, independent evaluation and artifact saving; host seconds is recorded separately.",
        "success_gate": "All four arms complete with passing declared checks and held-out oracle. Token comparisons additionally require complete measured usage in all four arms.",
        "measurement_note": "CLI-reported token counts and wrapper rounds; cached input is a subset of input, and no subscription-dollar conversion is made.",
    }
    write_json(output / "plan.json", plan)
    report = {"plan": plan, "status": "running", "runs": [], "summary": summarize([], tasks, trials),
              "experiment_wall_seconds": time.perf_counter() - experiment_started}
    write_json(output / "results.json", report)
    write_json(output / "summary.json", report["summary"])
    by_id = {task["id"]: task for task in tasks}
    with tempfile.TemporaryDirectory(prefix="optimization-fixtures-", dir=output) as temporary:
        base = Path(temporary).resolve()
        for group in schedule:
            task = by_id[group["task_id"]]
            for position, arm in enumerate(group["order"]):
                started = time.perf_counter()
                root = base / task["id"] / f"trial-{group['trial']}-{arm}"
                provider = observed = host = None
                result = {"task_id": task["id"], "category": task.get("category"),
                          "trial": group["trial"], "arm": arm, "arm_position": position + 1,
                          "host_options": {"controlled": True, **ARMS[arm]}}
                try:
                    phase_started = time.perf_counter()
                    make_task(root, task)
                    result["fixture_create_seconds"] = time.perf_counter() - phase_started
                    phase_started = time.perf_counter()
                    try:
                        provider = provider_factory()
                    finally:
                        result["provider_init_seconds"] = time.perf_counter() - phase_started
                    observed = ObservedProvider(provider)
                    host = Host(root, observed, controlled=True, max_calls=max_calls,
                                max_tokens=max_tokens, **ARMS[arm])
                    phase_started = time.perf_counter()
                    try:
                        result.update(host.run(task["task"]))
                    finally:
                        result["host_run_elapsed_seconds"] = time.perf_counter() - phase_started
                    result["host_options"] = {"controlled": True, **ARMS[arm]}
                    result["usage_status"] = "complete" if result.get("usage_available") else "partial_unknown"
                except Exception as error:
                    is_provider = isinstance(error, ProviderError)
                    result.update(decision="provider_error" if is_provider else "runner_error",
                                  error=str(error), error_type=type(error).__name__,
                                  usage_available=False, usage=None, usage_status="partial_unknown",
                                  model_calls=observed.completed if observed else 0,
                                  provider_calls_attempted=observed.attempted if observed else 0,
                                  cli_invocations_attempted=getattr(provider, "invocations", None),
                                  seconds=None, check_executions=sum(bool(item["result"].get("executed"))
                                      for item in host.trace) if host else 0,
                                  trace=host.trace if host else [])
                if observed is not None:
                    result["provider_calls_attempted"] = observed.attempted
                    result["completed_provider_rounds"] = observed.completed
                    result["measured_provider_rounds"] = observed.measured_rounds
                    result["provider_call_seconds"] = sum(observed.call_seconds)
                    result["provider_call_seconds_by_request"] = observed.call_seconds
                    if not result.get("usage_available"):
                        result["observed_partial_usage"] = observed.known_usage
                        result["unmeasured_or_failed_provider_rounds"] = observed.attempted - observed.measured_rounds
                        result["usage_note"] = "Partial usage contains only observed completed rounds; remaining usage is unknown, never zero-filled."
                phase_started = time.perf_counter()
                try:
                    result["independent_evaluation"] = evaluate_task(root, Controller(root).env, task)
                except Exception as error:
                    result["independent_evaluation"] = {"ran": False, "passed": None, "status": "not_run",
                                                         "reason": "evaluation_error", "error_type": type(error).__name__}
                result["evaluation_seconds"] = time.perf_counter() - phase_started
                artifact = output / "artifacts" / task["id"] / f"trial-{group['trial']}-{arm}"
                phase_started = time.perf_counter()
                try:
                    shutil.copytree(root, artifact, ignore=shutil.ignore_patterns(".checkgate", "__pycache__", "*.pyc"))
                    result["artifact_directory"] = artifact.relative_to(output).as_posix()
                    result["artifact_saved"] = True
                except OSError as error:
                    result["artifact_saved"] = False
                    result["artifact_error_type"] = type(error).__name__
                result["artifact_save_seconds"] = time.perf_counter() - phase_started
                result["end_to_end_seconds"] = time.perf_counter() - started
                result["succeeded"] = (result["decision"] == "complete"
                                       and result["independent_evaluation"].get("passed") is True
                                       and result["artifact_saved"])
                report["runs"].append(result)
                report["summary"] = summarize(report["runs"], tasks, trials)
                report["experiment_wall_seconds"] = time.perf_counter() - experiment_started
                write_json(output / "results.json", report)
                write_json(output / "summary.json", report["summary"])
                print(json.dumps({"task_id": task["id"], "trial": group["trial"], "arm": arm,
                                  "decision": result["decision"], "succeeded": result["succeeded"],
                                  "end_to_end_seconds": result["end_to_end_seconds"]}), flush=True)
    report["status"] = "complete"
    write_json(output / "results.json", report)
    write_json(output / "summary.json", report["summary"])
    report["experiment_wall_seconds"] = time.perf_counter() - experiment_started
    report["experiment_wall_note"] = "Includes initial plan/snapshot persistence, serial arms, fixture cleanup and checkpoint writes; excludes CLI preflight/source capture and this final result persistence."
    write_json(output / "results.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--output", default="results/optimization")
    parser.add_argument("--maxcalls", "--max-calls", dest="max_calls", type=int, default=12)
    parser.add_argument("--maxtokens", "--max-tokens", "--max-total-tokens", dest="max_tokens", type=int, default=30000)
    parser.add_argument("--codex-bin")
    args = parser.parse_args(argv)
    if not 1 <= args.trials <= 4 or not 1 <= args.max_calls <= 30 or not 1 <= args.max_tokens <= 100000:
        parser.error("Limits require trials 1..4, maxcalls 1..30, maxtokens 1..100000")
    ready = preflight(args.codex_bin)
    if not args.live or not ready["ready"]:
        print(json.dumps(ready))
        return 2 if args.live else 0
    snapshot = source_snapshot(Path(__file__).resolve().parent)
    output = Path(args.output).resolve()
    factory = lambda: CodexProvider(args.model, args.reasoning, binary=args.codex_bin, work_dir=output / "runtime")
    metadata = {"backend": "codex_subscription", "model": args.model, "reasoning": args.reasoning,
                "api_fallback": False, "live_model_run": True, "preflight": ready}
    try:
        report = run_experiment(factory, output, TASKS, args.trials, args.max_calls, args.max_tokens,
                                metadata=metadata, snapshot=snapshot)
    except ValueError as error:
        parser.error(str(error))
    successful = report["summary"]["fully_successful_groups"]
    print(json.dumps({"output": str(output), "completed_arms": len(report["runs"]),
                      "successful_groups": successful, "planned_groups": len(TASKS) * args.trials}))
    return 0 if successful == len(TASKS) * args.trials else 1


if __name__ == "__main__":
    raise SystemExit(main())
