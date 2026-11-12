"""Exercise the host end-to-end using explicitly synthetic model replies."""
import argparse
import json
from pathlib import Path

from benchmark import run_pairs
from demo import FIXED


class SyntheticProvider:
    def __init__(self):
        self.replies = iter([
            {"actions": [{"tool": "read_file", "arguments": {"path": "port_parser.py"}}]},
            {"actions": [
                {"tool": "write_file", "arguments": {"path": "port_parser.py", "content": FIXED}},
                {"tool": "checkgate_run", "arguments": {"check": "unit"}},
                {"tool": "checkgate_run", "arguments": {"check": "syntax"}}]},
            {"actions": [{"tool": "checkgate_run", "arguments": {"check": "unit"}},
                         {"tool": "checkgate_run", "arguments": {"check": "syntax"}}]},
            {"final": "Whitespace handling fixed and required checks passed."},
        ])

    def complete(self, _messages):
        return next(self.replies), None  # No invented inference token counts.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/integration")
    args = parser.parse_args()
    report = run_pairs(SyntheticProvider, args.output, trials=1,
                       report_name="synthetic-results.json",
                       report_metadata={"experiment": "synthetic provider replay", "live_model_run": False,
                                        "model_tokens_measured": False})
    report.update(experiment="synthetic provider replay", live_model_run=False, model_tokens_measured=False)
    path = Path(args.output) / "synthetic-results.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    pair = report["pairs"][0]
    print(json.dumps({"experiment": report["experiment"], "both_succeeded": pair["both_succeeded"],
                      "baseline_calls": pair["baseline"]["model_calls"], "controller_calls": pair["controller"]["model_calls"],
                      "baseline_checks": pair["baseline"]["check_executions"], "controller_checks": pair["controller"]["check_executions"],
                      "model_tokens_measured": False}))
