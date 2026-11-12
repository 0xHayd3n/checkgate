// Official MCP SDK interoperability probe. No model calls or external services.
// From this directory: npm ci --ignore-scripts; node verify.mjs [absolute-checkgate-source-directory]
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync, appendFileSync, rmSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { LATEST_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS } from '@modelcontextprotocol/sdk/types.js';

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(process.argv[2] ?? join(here, '../..'));
const python = execFileSync('python', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim();
const sdk = JSON.parse(readFileSync(join(here, 'node_modules/@modelcontextprotocol/sdk/package.json')));
const report = {
  sdk: { name: sdk.name, version: sdk.version },
  platform: process.platform,
  node: process.version,
  python: execFileSync(python, ['--version'], { encoding: 'utf8' }).trim(),
  clientOfferedProtocol: LATEST_PROTOCOL_VERSION,
  sdkSupportedProtocols: SUPPORTED_PROTOCOL_VERSIONS,
  sources: ['https://github.com/modelcontextprotocol/typescript-sdk', 'https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/refs/heads/v1.x/docs/client.md'],
  liveModelCalls: 0,
  authInspection: false,
  sourceHashes: Object.fromEntries(['checkgate.py', 'service.py', 'mcp_server.py', 'hermes_hook.py', 'entrypoint.py', 'build.py', 'demo.py'].map(name => [name, createHash('sha256').update(readFileSync(join(source, name))).digest('hex')])),
  cases: [],
};
const scratch = mkdtempSync(join(here, 'fixture-'));
const safeRelative = relative(here, scratch);
assert(safeRelative && !safeRelative.startsWith('..') && !isAbsolute(safeRelative));
const pythonCode = (code, ...args) => execFileSync(python, ['-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); ' + code, source, ...args], { encoding: 'utf8' });

async function connect(root, mode) {
  const transport = new StdioClientTransport({
    command: python,
    args: ['-B', mode === 'source' ? join(source, 'mcp_server.py') : join(scratch, 'checkgate.pyz'), ...(mode === 'source' ? [] : ['mcp']), '--root', root],
    cwd: root,
    env: { PYTHONDONTWRITEBYTECODE: '1' },
    stderr: 'pipe',
  });
  const client = new Client({ name: 'checkgate-official-sdk-probe', version: '1.0.0' }, { capabilities: {} });
  const handshake = {};
  const request = client.request.bind(client);
  client.request = async (...args) => {
    const result = await request(...args);
    if (args[0].method === 'initialize') {
      handshake.request = args[0];
      handshake.response = result;
    }
    return result;
  };
  let stderr = '';
  transport.stderr?.on('data', data => { stderr += data.toString(); });
  await client.connect(transport);
  return { client, handshake, stderr: () => stderr };
}

async function verify(mode) {
  const root = join(scratch, mode);
  pythonCode('from demo import make_fixture; make_fixture(sys.argv[2], broken=True)', root);
  const entry = { mode, checks: [] };
  report.cases.push(entry);
  let session = await connect(root, mode);
  const check = (name, action) => { action(); entry.checks.push({ name, passed: true }); };
  const call = (name, args = {}) => session.client.callTool({ name, arguments: args });
  try {
    entry.handshake = session.handshake;
    check('initialization negotiates supported pinned protocol', () => {
      assert.equal(session.handshake.response.protocolVersion, '2025-06-18');
      assert.deepEqual(session.client.getServerVersion(), { name: 'checkgate', version: '0.2.0' });
      assert.deepEqual(session.client.getServerCapabilities(), { tools: {} });
    });
    const pong = await session.client.ping();
    check('ping accepted', () => assert.deepEqual(pong, {}));
    const tools = await session.client.listTools();
    entry.tools = tools.tools;
    check('two tool declarations pass SDK schema parsing', () => {
      assert.deepEqual(tools.tools.map(t => t.name).sort(), ['checkgate_run', 'checkgate_status']);
      assert.deepEqual(tools.tools.find(t => t.name === 'checkgate_run').inputSchema.properties.check.enum, ['unit', 'syntax']);
    });
    const initial = await call('checkgate_status');
    check('initial status remains incomplete', () => {
      assert.equal(initial.structuredContent.decision, 'continue');
      assert.equal(initial.isError, false);
      assert.equal(initial.content[0].type, 'text');
      assert.deepEqual(JSON.parse(initial.content[0].text), initial.structuredContent);
    });
    const failed = await call('checkgate_run', { check: 'unit' });
    check('real failing regression reported as tool error', () => {
      assert.equal(failed.isError, true);
      assert.equal(failed.structuredContent.result, 'failed');
      assert.equal(failed.structuredContent.executed, true);
    });
    pythonCode('from pathlib import Path; from demo import FIXED; Path(sys.argv[2], "port_parser.py").write_text(FIXED, encoding="utf-8")', root);
    const unit = await call('checkgate_run', { check: 'unit' });
    check('fixed unit check executes and passes', () => {
      assert.equal(unit.isError, false);
      assert.equal(unit.structuredContent.result, 'passed');
      assert.equal(unit.structuredContent.executed, true);
    });
    const syntax = await call('checkgate_run', { check: 'syntax' });
    check('syntax check executes and passes', () => {
      assert.equal(syntax.isError, false);
      assert.equal(syntax.structuredContent.result, 'passed');
      assert.equal(syntax.structuredContent.executed, true);
    });
    const complete = await call('checkgate_status');
    check('both required checks yield completion', () => assert.equal(complete.structuredContent.decision, 'complete'));
    const reused = await call('checkgate_run', { check: 'unit' });
    check('passing evidence reused in one session', () => {
      assert.equal(reused.isError, false);
      assert.equal(reused.structuredContent.executed, false);
      assert.equal(reused.structuredContent.reused, true);
    });
    const unknown = await call('shell', { command: 'must-not-execute' });
    check('unknown operation returns blocked tool result', () => {
      assert.equal(unknown.isError, true);
      assert.equal(unknown.structuredContent.decision, 'blocked');
      assert.equal(unknown.structuredContent.executed, false);
    });
    const invalid = await call('checkgate_run', { check: 'not-declared' });
    check('undeclared check returns blocked tool result', () => {
      assert.equal(invalid.isError, true);
      assert.equal(invalid.structuredContent.executed, false);
    });
    await session.client.close();
    check('first stdio session leaves stderr clean', () => assert.equal(session.stderr(), ''));
    session = await connect(root, mode);
    const restartedReuse = await call('checkgate_run', { check: 'unit' });
    check('new process reuses evidence through SDK', () => {
      assert.equal(restartedReuse.structuredContent.executed, false);
      assert.equal(restartedReuse.structuredContent.reused, true);
    });
    appendFileSync(join(root, 'port_parser.py'), '\n# SDK probe invalidation\n');
    const stale = await call('checkgate_status');
    check('tracked source edit invalidates prior completion', () => assert.equal(stale.structuredContent.decision, 'continue'));
    const rerun = await call('checkgate_run', { check: 'unit' });
    check('source edit causes a real check execution', () => {
      assert.equal(rerun.structuredContent.executed, true);
      assert.equal(rerun.structuredContent.result, 'passed');
    });
    entry.outcome = 'passed';
  } finally {
    await session.client.close();
    entry.stderr = session.stderr();
  }
}

try {
  pythonCode('from build import build; build(sys.argv[2])', join(scratch, 'checkgate.pyz'));
  await verify('source');
  await verify('zipapp');
  report.outcome = 'passed';
  report.passedChecks = report.cases.reduce((n, c) => n + c.checks.length, 0);
} catch (error) {
  report.outcome = 'failed';
  report.error = { name: error.name, message: error.message, stack: error.stack };
  process.exitCode = 1;
} finally {
  rmSync(scratch, { recursive: true, force: true });
  report.disposableFixtureRemoved = true;
  writeFileSync(join(here, 'results.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ outcome: report.outcome, sdk: report.sdk, passedChecks: report.passedChecks, cases: report.cases.map(c => ({ mode: c.mode, outcome: c.outcome, checks: c.checks.length })), error: report.error?.message }));
}
