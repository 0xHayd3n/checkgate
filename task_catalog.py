"""Build isolated coding tasks and evaluate with a separate held-out oracle."""
import json
from pathlib import Path
import subprocess
import sys

from task_sets.data_tasks import TASKS as DATA_TASKS
from task_sets.state_tasks import TASKS as STATE_TASKS
from task_sets.algorithm_tasks import TASKS as ALGORITHM_TASKS

TASKS = DATA_TASKS + STATE_TASKS + ALGORITHM_TASKS
BY_ID = {task["id"]: task for task in TASKS}
if len(BY_ID) != len(TASKS):
    raise ValueError("Task identifiers must be unique")

SYNTAX = '''import ast
from pathlib import Path
for path in Path('.').rglob('*.py'):
    if '.checkgate' not in path.parts:
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
'''


def make_task(root, task, *, reference=False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = dict(task["files"])
    if reference:
        if set(task["reference_files"]) != set(task["editable_files"]):
            raise ValueError("Reference implementations must cover exactly the edit targets")
        files.update(task["reference_files"])
    for name, content in files.items():
        relative = Path(name)
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or ".." in relative.parts:
            raise ValueError("Fixture path escaped its task")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / "syntax_check.py").write_text(SYNTAX, encoding="utf-8")
    (root / "requirements.lock").write_text("# Python standard library only\n", encoding="utf-8")
    inputs = ["**/*.py", "requirements.lock"]
    policy = {
        "task": task["task"], "task_id": task["id"],
        "required": ["unit", "syntax"], "editable_files": task["editable_files"],
        "max_age_seconds": 3600, "max_executions_per_check_snapshot": 3,
        "environment_keys": [],
        "checks": {
            "unit": {"argv": ["{python}", "-B", "-m", "unittest", "-q", *task["unit_modules"]], "inputs": inputs},
            "syntax": {"argv": ["{python}", "-B", "syntax_check.py"], "inputs": inputs},
        },
    }
    (root / "checkgate.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    return root


def evaluate_task(root, environment, task):
    # Neither this code nor the reference implementation exists inside the
    # fixture visible to the model. Requirements are still stated in its task.
    program = "import sys\nsys.path.insert(0, sys.argv[1])\n" + task["oracle"]
    try:
        run = subprocess.run([sys.executable, "-B", "-", str(root)], input=program,
                             cwd=root, env=environment, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=15, shell=False)
    except subprocess.TimeoutExpired:
        return {"ran": True, "status": "timeout", "passed": False, "exit_code": None}
    except OSError:
        return {"ran": False, "status": "not_run", "passed": None, "exit_code": None}
    return {"ran": True, "status": "passed" if run.returncode == 0 else "failed",
            "passed": run.returncode == 0, "exit_code": run.returncode,
            "diagnostic_tail": run.stderr[-4000:] if run.returncode else "",
            "note": "Held-out host assertions; not included in agent check counts or token usage"}
