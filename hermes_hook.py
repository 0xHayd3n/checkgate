"""Hermes shell-hook adapter. It never treats unrelated turns as completed.

The host must provide a fixed root and an explicitly bound task ID. This hook
can prevent a fresh turn for that completed task; it cannot end the current
Hermes tool loop. That requires the host controller used by agent_host.py.
"""
import argparse
import json
import sys

from service import Service, compact

CHECK_RUN_TOOL_NAMES = ("mcp__checkgate__checkgate_run", "mcp_checkgate_checkgate_run")


def handle(payload, service, task_id):
    if not isinstance(payload, dict):
        return {"action": "block", "message": "Checkgate received an invalid hook payload."}
    extra = payload.get("extra", {})
    if not task_id or not isinstance(extra, dict) or extra.get("task_id") != task_id:
        return {}  # An explicitly different/new task must still reach the model.
    event = payload.get("hook_event_name")
    if event == "pre_llm_call":
        status = service.call("checkgate_status")
        if status["decision"] == "complete":
            return {"action": "block", "message": "Checkgate: this bound task is complete; all required evidence is current."}
        return {"context": "Checkgate task status: " + compact(status)}
    if event == "pre_tool_call" and payload.get("tool_name") in CHECK_RUN_TOOL_NAMES:
        arguments = payload.get("tool_input", {})
        if not isinstance(arguments, dict) or set(arguments) != {"check"} or not isinstance(arguments["check"], str):
            return {"action": "block", "message": "Use one named Checkgate check."}
        status = service.call("checkgate_status")
        if status["decision"] == "blocked":
            return {"action": "block", "message": "Checkgate evidence could not be validated."}
        if status.get("checks", {}).get(arguments["check"]) == "passed":
            return {"action": "block", "message": "Checkgate: this check already has current passing evidence. Finalise if the task is complete."}
    return {}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--evidence-dir")
    args = parser.parse_args()
    try:
        result = handle(json.load(sys.stdin), Service(args.root, args.evidence_dir), args.task_id)
    except (OSError, ValueError):
        result = {"action": "block", "message": "Checkgate hook failed; review the task state."}
    print(json.dumps(result, separators=(",", ":")))
