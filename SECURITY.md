# Security

Security fixes target the current release and the default branch. Reports about
older releases are welcome when the issue also affects current code.

Report a vulnerability through the repository's **Security → Advisories → Report
a vulnerability** option when it is available. If private reporting is not
enabled, open an issue asking for a private contact without posting the exploit,
credentials, private source, or local execution logs.

Include the Checkgate version, Python version, operating system, a minimal
reproduction using disposable files, and the expected and observed behavior.
Redact usernames, absolute private paths, tokens and command output containing
secrets. There is no promised response time or bug bounty.

## Trust boundary

Checkgate records whether declared commands passed for a declared input
snapshot. Passing checks establish the `declared-checks-only` assurance shown in
the result. They do not establish that the requirements are sufficient or that
arbitrary code is safe.

The accepting host must control the manifest, controller code, command
definitions and evidence store. Give an agent only the intended source edit
surface. Place evidence outside an agent-writable workspace when the host
supports it, and enforce filesystem access separately. A model with write access
to trusted policy or evidence can undermine this boundary.

Declared checks run with the local account's permissions. `shell=False` avoids
shell command interpolation; it does not sandbox a command or the code it imports.
Use a separate OS/container boundary when executing untrusted projects. Check
timeouts apply to the launched process; they are not a promise to terminate every
descendant process on every operating system.

Declare all files and relevant environment variables that can change a result.
The executable hash and selected environment snapshot do not cover every shared
library, remote service or external state a command might use. Evidence is local
to a project, platform and interpreter snapshot; do not copy evidence between
devices as a substitute for fresh verification.

The stdio MCP adapter exposes only the declared Checkgate tools. It does not
provide network authentication or a remote service. Keep its process and stdin
connection under the intended host's control.

## Evidence and logs

Evidence and failing-check output can contain project content, environment
values and private paths. Keep them out of public repositories. Raw experiment
results, provider state and local evidence are ignored by default; review any
aggregate report before publishing it. A crash can leave a runner lock behind;
confirm that no check process is running before manually removing a stale lock.
