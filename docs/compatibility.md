# Compatibility and validation scope

The core controller and stdio MCP server use Python's standard library. Local validation used Windows and Python 3.14. The repository's tests can be run without model access; inference benchmarks additionally need the separately installed, signed-in Codex CLI.

| Surface | Implemented behavior | Validation boundary |
| --- | --- | --- |
| `checkgate.py` / `Service` | Named commands, evidence freshness, input/environment invalidation and cross-process reuse | Local automated tests |
| `mcp_server.py` | Stdio discovery and Checkgate tools | Local protocol tests plus official MCP SDK 1.32.2: 15 checks each for source and zipapp; a live Hermes connection was not exercised |
| `agent_host.Host` | Bounded tool loop; optional editable-source preload and compact brief; current-edit/current-check completion | Local tests and the saved controlled coding-task experiments |
| Hermes MCP | Configuration templates for a fixed project root | Matched to official docs/source, not an inspected installation |
| `hermes_hook.py` | Task-bound pre-turn status/blocking and duplicate-check interception; current and legacy tool names | Local shell-hook payload tests; not a native loop-stopping plugin |
| DS Bot | Capability checklist for a deployment-specific integration | No installed package, settings schema or live route verified |

## Hermes contract checked

Research was checked on 12 November 2026 against official `NousResearch/hermes-agent` commit [`aac0296baee3117c4f6a6b43bfc60f220ebd523f`](https://github.com/NousResearch/hermes-agent/commit/aac0296baee3117c4f6a6b43bfc60f220ebd523f). This pins the researched API; it does not identify the version installed on another computer.

- The stdio entry is `mcp_servers.<name>.command` plus `args`. Filtering uses original MCP tool names, while Hermes exposes prefixed registry names. [MCP reference](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/website/docs/reference/mcp-config-reference.md)
- Current registry names use `mcp__<server>__<tool>`. The reference adapter also accepts `mcp_checkgate_checkgate_run` for older integrations. [Name construction](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/tools/mcp_tool_schema.py#L172)
- Plugin callbacks register through `register(ctx)` / `ctx.register_hook(...)`; shell hooks receive JSON on stdin and return optional JSON on stdout. They are distinct installation paths. [Event hook reference](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/website/docs/user-guide/features/hooks.md)
- `pre_llm_call` runs before a user turn's loop, and can block that fresh turn. `pre_api_request` is an observer; its return value is not a loop-stop directive. [Observer hook reference](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/website/docs/developer-guide/observer-hooks.md)
- `pre_verify` can request another iteration when the model attempts a final answer after edits. It does not provide the host's immediate completion boundary after a successful check tool call. [Stop gate source](https://github.com/NousResearch/hermes-agent/blob/aac0296baee3117c4f6a6b43bfc60f220ebd523f/agent/turn_stop_gates.py#L53)

MCP tool availability and local hook-contract tests do not establish a reduction in Hermes or DS Bot inference calls. The reported optimization measurements apply to Checkgate's controlled host on the recorded synthetic tasks. Deployment-specific measurements are still required for other agent loops.

## Practical checks on a new host

Run the core local tests, configure a disposable task root, and confirm discovery plus named check execution. Verify reuse without edits, invalidation after an edit, and isolation between accepted tasks. For the reference hook, also verify that the real event's task ID matches the explicit binding and that its tool name matches the discovered registry name. Keep the existing agent configuration until those observations are concrete.

The Hermes templates and configuration generator contain no account credentials and do not alter application settings. A live deployment still requires applying the chosen configuration to the correct host/profile.
