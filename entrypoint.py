"""Portable command dispatcher for installed consoles and the single-file zipapp."""
import argparse
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
import tempfile

from checkgate import Controller, PolicyError, VERSION


def _relative(value):
    """Manifest paths use forward slashes and stay relative on every platform."""
    value = value.replace("\\", "/")
    if not value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or ".." in Path(value).parts:
        raise ValueError("Use project-relative paths and input globs without '..'")
    return value


def _checks(entries, inputs):
    checks = {}
    for entry in entries:
        name, separator, encoded = entry.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("Each --check must be NAME=JSON_ARRAY")
        if name in checks:
            raise ValueError("Check names must be unique")
        try:
            argv = json.loads(encoded)
        except ValueError as error:
            raise ValueError("Check argv must be a JSON array of command arguments") from error
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("Check argv must be a nonempty JSON array of nonempty strings")
        checks[name] = {"argv": argv, "inputs": inputs}
    return checks


def initialize(root, *, task=None, checks=(), inputs=(), editable_files=(), demo=False):
    """Create a new manifest; never replace an existing project file."""
    root = Path(root).resolve()
    manifest = root / "checkgate.json"
    if manifest.exists():
        raise FileExistsError("checkgate.json already exists; review it before making a new task")
    if demo:
        if task or checks or inputs or editable_files:
            raise ValueError("Use --demo alone, or provide the custom task options")
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise FileExistsError("The demo requires a new or empty directory")
        from demo import make_fixture
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="checkgate-init-", dir=root.parent) as temporary:
            staged = make_fixture(Path(temporary) / "demo")
            root.mkdir(parents=True, exist_ok=True)
            # Publish the accepted manifest only after its sample files exist.
            for source in sorted(staged.iterdir(), key=lambda path: path.name == "checkgate.json"):
                with source.open("rb") as content, (root / source.name).open("xb") as target:
                    shutil.copyfileobj(content, target)
    else:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A custom manifest requires --task")
        if not checks or not inputs:
            raise ValueError("A custom manifest requires at least one --check and --input")
        patterns = [_relative(value) for value in inputs]
        editable = [_relative(value) for value in editable_files]
        if any(any(character in name for character in "*?[") for name in editable):
            raise ValueError("--editable requires individual file paths, not globs")
        definitions = _checks(checks, patterns)
        policy = {
            "task": task, "required": list(definitions), "editable_files": editable,
            "max_age_seconds": 3600, "max_executions_per_check_snapshot": 3,
            "environment_keys": [], "checks": definitions,
        }
        root.mkdir(parents=True, exist_ok=True)
        with manifest.open("x", encoding="utf-8") as output:
            output.write(json.dumps(policy, indent=2) + "\n")
    return {"initialized": True, "root": str(root), "manifest": str(manifest),
            "required": json.loads(manifest.read_text(encoding="utf-8"))["required"]}


def doctor(root, evidence_dir=None):
    """Validate commands, fingerprints and stored evidence without running checks."""
    controller = Controller(root, evidence_dir)
    status = controller.status()
    return {"ready": True, "version": VERSION, "python": sys.version.split()[0],
            "root": str(controller.root), "required": controller.policy["required"],
            "status": status}


def _project_options(parser, *, global_options=False):
    parser.add_argument("--root", default="." if global_options else argparse.SUPPRESS,
                        help="Project directory containing checkgate.json (default: current directory)")
    parser.add_argument("--evidence-dir", default=None if global_options else argparse.SUPPRESS,
                        help="Optional host-controlled directory for verification evidence")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="checkgate " + VERSION)
    _project_options(parser, global_options=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("status", "complete", "doctor", "mcp"):
        _project_options(commands.add_parser(name))
    run = commands.add_parser("run", help="Run one named required check")
    _project_options(run)
    run.add_argument("check")
    init = commands.add_parser("init", help="Create a manifest or runnable demo without overwriting files")
    _project_options(init)
    init.add_argument("--demo", action="store_true", help="Create a fixed, runnable sample in a new or empty directory")
    init.add_argument("--task", help="Accepted task and its completion requirements")
    init.add_argument("--check", action="append", default=[], metavar="NAME=JSON_ARRAY",
                      help='Named command arguments, e.g. unit=["{python}","-m","unittest","discover"]')
    init.add_argument("--input", action="append", default=[], help="Relative file or glob included in every check fingerprint")
    init.add_argument("--editable", action="append", default=[], help="One relative file the coding host may edit")
    config = commands.add_parser("config", help="Print MCP client configuration JSON")
    _project_options(config)
    config.add_argument("--hermes", action="store_true", help="Use Hermes's mcp_servers key")
    hook = commands.add_parser("hook", help="Run the explicitly task-bound Hermes shell-hook adapter")
    _project_options(hook)
    hook.add_argument("--task-id", required=True)
    demo = commands.add_parser("demo", help="Run the scripted experiment without model calls")
    demo.add_argument("--output", default="results/demo")
    args = parser.parse_args(argv)
    try:
        if args.operation == "init":
            result = initialize(args.root, task=args.task, checks=args.check, inputs=args.input,
                                editable_files=args.editable, demo=args.demo)
        elif args.operation == "doctor":
            result = doctor(args.root, args.evidence_dir)
        elif args.operation == "config":
            from configure import configuration
            result = configuration(args.root, args.hermes, args.evidence_dir)
        elif args.operation == "mcp":
            from mcp_server import StdioServer
            from service import Service
            StdioServer(Service(args.root, args.evidence_dir)).serve(sys.stdin.buffer, sys.stdout.buffer)
            return 0
        elif args.operation == "hook":
            from hermes_hook import handle
            from service import Service
            result = handle(json.load(sys.stdin), Service(args.root, args.evidence_dir), args.task_id)
        elif args.operation == "demo":
            from demo import exercise
            result = exercise(args.output)
        else:
            controller = Controller(args.root, args.evidence_dir)
            result = controller.run(args.check) if args.operation == "run" else controller.status()
    except (OSError, PolicyError, ValueError) as error:
        result = {"decision": "blocked", "reason": str(error), "executed": False}
        if args.operation == "doctor":
            result["ready"] = False
        print(json.dumps(result, separators=(",", ":")))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 2 if result.get("decision") == "blocked" else 1 if result.get("result") in ("failed", "inputs_changed") else 0


def mcp_main(argv=None):
    """Console alias whose stdout contains only MCP protocol messages."""
    return main(["mcp", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
