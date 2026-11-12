# Hermes and other agent integrations

Checkgate exposes named checks and current verification evidence through a local MCP server. Hermes can use these tools through its documented stdio configuration. The measured source preloading, compact task brief and immediate host stopping run inside `agent_host.Host`; adding an MCP server to another app does not enable those host optimizations.

## Configure Hermes on the machine that runs it

1. Place Checkgate on the Hermes host and choose a task project containing a reviewed `checkgate.json`. The manifest declares the accepted task, editable files, required checks, commands and their input files. Start with `examples/port-task/checkgate.json` and adapt it to the task. Keep manifest approval outside the model's write access.
2. After installing Checkgate into the Python environment used on that host, validate the project without executing its checks:

   ```text
   checkgate doctor --root "/absolute/path/to/your-task-project"
   ```

3. Generate the server block with the interpreter and paths you will use:

   ```text
   checkgate config --hermes --root "/absolute/path/to/your-task-project"
   ```

   This prints configuration with the installed interpreter; it does not edit your settings. Merge its `checkgate` entry into the active profile's `mcp_servers` mapping. The default profile uses `~/.hermes/config.yaml`. JSON object syntax from the generator is also valid YAML; preserve the surrounding configuration when merging. For an uninstalled source checkout, use `python -B entrypoint.py doctor ...` and `python -B entrypoint.py config --hermes ...` from the checkout directory.
4. Alternatively, fill in [the Windows template](../examples/hermes/mcp.windows.yaml) or [the POSIX/WSL template](../examples/hermes/mcp.posix.yaml). Both launch the installed `mcp_server` module using an explicit Python executable. Use paths visible to the Hermes process. A Windows path on your laptop is not a path inside another machine or a WSL environment.
5. Start a new Hermes session or use `/reload-mcp`. Ask it to call `checkgate_status` on the `checkgate` server. Hermes currently registers the tools as `mcp__checkgate__checkgate_status` and `mcp__checkgate__checkgate_run`.

Hermes documents the stdio `command`/`args` shape, tool filtering and reload command in its [MCP configuration reference](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/website/docs/reference/mcp-config-reference.md). The templates expose only Checkgate's two native tools.

When assigning a coding task, tell the agent to run each named required check after its final relevant edit and finish when `checkgate_status` reports complete. Checkgate can reuse valid evidence when its tools are called again. Ordinary terminal commands outside Checkgate do not enter its evidence cache. MCP provides tool access; the agent app still owns task execution and its inference loop.

## Optional Hermes shell-hook contract

`hermes_hook.py` is a reference adapter for Hermes's JSON shell-hook protocol. It requires an operator-supplied fixed project root and an explicit task ID that must exactly match `extra.task_id` in the event payload. A missing or different binding leaves the event alone. It accepts both current double-underscore MCP tool names and the legacy single-underscore alias used by older tests.

For that bound task, it can inject current status before a turn, block a fresh turn whose checks are already complete, or block a duplicate Checkgate check tool call. Its local tests exercise those behaviors and source-change invalidation. It is not installed or enabled automatically.

Hermes fires `pre_llm_call` once during turn setup, before the tool loop. Consequently this adapter cannot stop an already running Hermes loop immediately after the last check passes. Returning a directive from the request observer `pre_api_request` is not a supported stop mechanism. See the official [turn-hook contract](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/website/docs/developer-guide/observer-hooks.md) and [turn setup implementation](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/agent/turn_context.py#L907).

Current Hermes also offers a `pre_verify` continuation gate when a model tries to finish after edits, and request/execution middleware for behavior changes. Those APIs do not make this reference hook equivalent to Checkgate's measured host loop. A native optimization adapter needs a separately tested integration with the actual loop and provider response contracts. See [the stop gate implementation](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/agent/turn_stop_gates.py#L53) and [middleware contract](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/website/docs/developer-guide/middleware.md).

## DS Bot deployment checklist

A live DS Bot installation and its installed package metadata were unavailable for this release's integration checks. No DS Bot configuration schema, package origin or native MCP route has been verified. Do not assume that a similarly named public repository is the same application.

Before connecting a particular installation:

- Identify its installed version, package `repository` URL and active execution route. A native Codex app-server route, a separate agent loop and a Hermes-backed route need different integrations.
- If it delegates to Hermes, configure the Hermes profile that actually runs the task, using the machine-local paths above.
- If it supports external stdio MCP servers on the active route, map the same executable and arguments into its documented server configuration. Confirm discovery and a real `checkgate_status` call before relying on check reuse.
- If it owns a custom model loop, add Checkgate tool dispatch and completion control deliberately. To reproduce the measured optimizations, that loop must supply bounded editable-source snapshots, a compact accepted brief, and current-edit/current-check stopping. These are host behaviors, not MCP settings.
- Test a source edit after a passing check: status must return to incomplete and a new check must execute. Confirm that a new task cannot be skipped because an earlier task completed.

See [compatibility and validation scope](compatibility.md) before treating any integration as an established deployment.
