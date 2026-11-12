# Contributing

Use Python 3.10 or newer. The runtime uses the standard library; installing from
source needs the build backend declared in `pyproject.toml`.

```sh
python -m pip install -e .
python -B -m unittest discover -v
python -B build.py --output dist/checkgate.pyz
```

The tests use disposable local fixtures and synthetic model providers. They do
not need a model login, API key, paid inference, or a running Hermes bot. Keep
ordinary tests independent of personal accounts and network services.

For a change, explain the concrete behavior it fixes and include a focused
regression when a failure would matter. Preserve the separation between
host-accepted policy, editable source and verification evidence. A source edit
after a passing check must invalidate that check's evidence. A failed or timed-out
check must never establish completion. Missing provider usage must stay unknown.

CI exercises Windows, Linux and Intel macOS with Python 3.10, 3.12 and 3.14, plus
Apple Silicon macOS with Python 3.14. Each job runs the tests, builds and installs a
wheel, and checks installed CLI/MCP commands and the zipapp from a temporary
directory outside the checkout. The workflow is also available through manual
dispatch and retains build artifacts; it does not publish to PyPI or call a live
model. An unrun CI matrix is a configured check, not evidence of a pass.

Use `sys.executable`, argument lists and temporary directories in subprocess
tests. Avoid shell-specific commands. Symlink and junction tests should skip
when the operating system or account cannot create the link, and code running on
Python 3.10 must not assume newer `pathlib` methods exist. Cache fingerprints
intentionally change across platforms, interpreter versions and root paths.

Do not commit `.checkgate` evidence, credentials, raw model transcripts, personal
paths or private project source. Publish only reviewed aggregate measurements,
label synthetic and live measurements accurately, and retain failures and
missing measurements in reported totals. Live experiments are explicit opt-ins
and should use a new output directory so prior evidence remains intact.

Before submitting, run the relevant tests and inspect the staged diff. Report
security concerns using the process in [SECURITY.md](SECURITY.md).

Workflow dependencies are pinned to full commit hashes. When updating them,
check the upstream [checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python) and
[upload-artifact](https://github.com/actions/upload-artifact) release notes and
the supported [runner images](https://github.com/actions/runner-images).
