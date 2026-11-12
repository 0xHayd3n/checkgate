"""Small host-bound interface shared by MCP, hooks and the benchmark host."""
import json
from pathlib import Path

from checkgate import Controller, PolicyError


class Service:
    def __init__(self, root, evidence_dir=None):
        self.root = Path(root).resolve()
        self.evidence_dir = evidence_dir

    def controller(self):
        # A server can outlive edits. Reload the accepted manifest for each call.
        return Controller(self.root, self.evidence_dir)

    def call(self, operation, arguments=None, *, reuse=True):
        arguments = {} if arguments is None else arguments
        try:
            if not isinstance(arguments, dict):
                raise PolicyError("Arguments must be an object")
            if operation == "checkgate_status" and not arguments:
                return self.controller().status()
            if operation == "checkgate_run" and set(arguments) == {"check"} and isinstance(arguments["check"], str):
                return self.controller().run(arguments["check"], reuse=reuse)
            raise PolicyError("Unknown operation or invalid arguments")
        except (PolicyError, OSError, ValueError) as error:
            return {"decision": "blocked", "executed": False, "reason": str(error)}

    def tools(self):
        checks = list(self.controller().policy["required"])
        return [
            {"name": "checkgate_status", "description": "Check whether this task's required checks have valid passing evidence.",
             "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "checkgate_run", "description": "Run one required check, reusing fresh matching passing evidence.",
             "inputSchema": {"type": "object", "properties": {"check": {"type": "string", "enum": checks}},
                             "required": ["check"], "additionalProperties": False}},
        ]


def compact(result):
    """Successful checks do not send test-runner output back to the model."""
    result = dict(result)
    if result.get("result") == "passed":
        result.pop("output_tail", None)
    return json.dumps(result, separators=(",", ":"))
