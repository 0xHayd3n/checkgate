# Checkgate

**Local verification caching and completion control for coding agents.**

Checkgate is a small, provider-independent verification controller. It runs named checks from a reviewed task manifest, caches passing execution evidence, and invalidates that evidence when the declared inputs or execution context change. Its Python host can finish a coding task as soon as the current edit has current passing checks.

Use it through a CLI, a local stdio MCP server, or a Python host adapter. The core has **no third-party runtime dependencies** and needs Python 3.10 or newer.

This is an experimental developer tool. Windows has been tested locally; the CI matrix covers Windows, Linux, and macOS, including Apple Silicon. “Portable” means a Python-capable computer or server with the task's own check dependencies installed. It is not a native phone app.

## Install

Install directly from this repository; no PyPI release is required. With [pipx](https://pipx.pypa.io/stable/installation/) and Git installed:

```sh
pipx install git+https://github.com/0xHayd3n/checkgate.git
checkgate --version
```

Alternatively, clone the repository and install into a virtual environment:

```sh
git clone https://github.com/0xHayd3n/checkgate.git
cd checkgate
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\checkgate.exe --help
```

On Linux/macOS:

```sh
.venv/bin/python -m pip install .
.venv/bin/checkgate --help
```

The commands below assume `checkgate` is on your PATH. Otherwise use the virtual environment's full command path above. The distribution is named `checkgate-agent`; the commands are `checkgate` and `checkgate-mcp`.

## Try a disposable task

```sh
checkgate init --root demo-project --demo
checkgate doctor --root demo-project
checkgate run unit --root demo-project
checkgate run syntax --root demo-project
checkgate complete --root demo-project
checkgate run unit --root demo-project
```

The demo contains a working port parser and tests. The last command reuses passing evidence. Change `demo-project/port_parser.py`, then run `status` or `run unit` again: the changed input requires fresh verification. `init` refuses to overwrite a manifest, and the demo requires a new or empty directory. `doctor` checks the manifest and command fingerprints without executing checks.

`complete` is a status query: inspect its JSON `decision` for `"complete"`. An incomplete `"continue"` response can also exit successfully. Failed checks exit 1; blocked operations exit 2.

## Connect Hermes or another MCP client

Generate configuration **on the machine and in the Python environment that will run the server**:

```sh
checkgate config --hermes --root demo-project
```

Merge the printed `checkgate` entry into your Hermes profile's `mcp_servers` mapping, then start a new session or reload MCP. Ask Hermes to call `checkgate_status`, followed by the required named checks. For clients that use the `mcpServers` key, omit `--hermes`.

The server exposes only `checkgate_status` and `checkgate_run(check)`. It accepts check names declared in the manifest, rather than arbitrary model-supplied commands. Configuration uses absolute local paths; regenerate it after moving to another computer. For a protected evidence directory, add `--evidence-dir /absolute/host-controlled/path` when generating config and when invoking local commands.

See [Hermes instructions, templates, and the DS Bot checklist](docs/integrations.md) and [exact compatibility boundaries](docs/compatibility.md). The Hermes configuration matches its official MCP contract; a live Hermes connection and your DS Bot installation have not been tested here. DS Bot setup depends on which route runs its tasks, so no unverified DS Bot config is supplied.

**MCP enables check reuse. Source preloading and immediate completion control require integration with the app's agent loop.** Adding this MCP server alone does not reproduce the measured model-token savings in another agent.

## Define your own task

Create `checkgate.json` at the root of the project. Commands are argument arrays and use no shell. `{python}` selects the interpreter running Checkgate.

```json
{
  "task": "Fix the parser without changing its validation rules",
  "required": ["unit"],
  "editable_files": ["parser.py"],
  "max_age_seconds": 3600,
  "max_executions_per_check_snapshot": 3,
  "environment_keys": [],
  "checks": {
    "unit": {
      "argv": ["{python}", "-B", "-m", "unittest", "discover"],
      "inputs": ["**/*.py", "requirements.lock"],
      "timeout_seconds": 30
    }
  }
}
```

Include every relevant source file, test, configuration file and dependency lockfile in `inputs`; declare relevant environment keys. Checkgate cannot detect dependencies you omit, external service state, or every change to shared libraries. Checks run in the task root with a restricted environment. A manifest can execute programs, so review it before using it.

Successful evidence binds to the manifest, check, input contents, command/executable, controller code, interpreter, OS, selected environment, and project location. New machines rerun checks. Expired evidence, changed inputs, input changes during execution and corrupt evidence cannot satisfy completion. Failures have a per-snapshot execution budget. Raw local evidence is stored in `.checkgate/` unless you choose an external directory.

The accepted manifest and evidence must remain under trusted host control for enforcement. Checkgate is an execution-evidence cache, not a sandbox or a proof of software correctness. See [security and operational limits](SECURITY.md).

## Host optimization and measurements

For a custom Python agent loop, `agent_host.Host` supplies bounded file tools and named checks. The provider implements `complete(messages)` and returns `(reply, usage)`: `reply` is an `actions` or `final` object, and `usage` is a token-count mapping or `None`. Inspect `agent_host.py` and the scripted providers in its tests before adapting a backend. A model's claimed completion does not replace required evidence.

```python
from agent_host import Host

host = Host(
    project_root,
    provider,
    controlled=True,
    preload_sources=True,
    compact_brief=False,
)
result = host.run(accepted_task_text)
```

Preloading includes only declared editable UTF-8 sources, up to 128 KiB per file and 256 KiB total, with explicit omission records. Read tools remain available. The controlled host requires an edit in the current invocation plus current passing required checks before automatic completion.

In a **48-run pilot on six small Python tasks**, source preloading alone reduced total reported tokens by **48.8%** and mean task wall time by **34.3%** against the existing controlled host. All 48 final artifacts passed independent acceptance checks. Combining preloading with a compact brief saved less in this pilot, so preloading alone is the suggested experiment. These are workload-specific observations, not a guarantee for Hermes, DS Bot or large repositories. [Method, all variants, TPS definitions and sanitized totals](docs/benchmarks.md).

## Single-file use

From a source checkout:

```sh
python build.py --output dist/checkgate.pyz
python dist/checkgate.pyz --help
python dist/checkgate.pyz init --root demo-project --demo
python dist/checkgate.pyz config --hermes --root demo-project
```

Copy the `.pyz` to another Python-capable machine and regenerate its configuration there. It includes the verifier, CLI, MCP server, reference hook and demo. The custom benchmark host remains in the source/wheel distribution. No Python interpreter or task dependencies are bundled. GitHub Actions also builds wheel and zipapp artifacts.

## Development

```sh
python -B -m unittest discover -v
python -m pip wheel --no-deps --wheel-dir wheelhouse .
```

Tests use disposable local fixtures and need no model account. Optional official MCP SDK interoperability checks are in `verification/mcp/`. The scripted `demo.py` experiment makes no model calls. The optional live benchmark is separate and uses a signed-in Codex CLI; review [the benchmark notes](docs/benchmarks.md) before running it.

[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [MIT license](LICENSE)
