"""Codex subscription adapter. Codex owns auth/transport; no API fallback."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

class ProviderError(RuntimeError):
    pass

AUTH_CONFIG = ['-c', 'forced_login_method="chatgpt"', '-c', 'model_provider="openai"']
EXCLUDED_ENV = {'OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY',
                'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'CODEX_ACCESS_TOKEN'}

def subscription_environment():
    return {k: v for k, v in os.environ.items() if k.upper() not in EXCLUDED_ENV}

def cli_command(binary=None):
    candidate = str(binary) if binary else (shutil.which('codex.exe') or shutil.which('codex'))
    if not candidate:
        raise ProviderError('Codex CLI was not found. Install it or pass --codex-bin PATH.')
    path = Path(shutil.which(candidate) or candidate).resolve()
    if path.suffix.lower() in ('.cmd', '.bat', '.ps1'):
        parents = (path.parent, path.parent.parent)
        path = next((p / 'node_modules/@openai/codex/bin/codex.js' for p in parents
                     if (p / 'node_modules/@openai/codex/bin/codex.js').is_file()), path)
    if not path.is_file() or path.suffix.lower() in ('.cmd', '.bat', '.ps1'):
        raise ProviderError('Use a Codex executable or the official codex.js entry point.')
    if path.suffix.lower() == '.js':
        node = shutil.which('node')
        if not node:
            raise ProviderError('Node.js is required for the npm Codex entry point.')
        return [node, str(path)]
    return [str(path)]

def login_status(command):
    try:
        process = subprocess.run([*command, *AUTH_CONFIG, 'login', 'status'],
                                 env=subscription_environment(), capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=15, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = (process.stdout + process.stderr).lower()
    return process.returncode == 0 and 'logged in using chatgpt' in text and 'api key' not in text

def preflight(binary=None):
    try:
        command = cli_command(binary)
    except ProviderError as error:
        return {'live_model_run': False, 'backend': 'codex_subscription', 'codex_available': False,
                'chatgpt_login_confirmed': False, 'ready': False, 'reason': str(error), 'api_fallback': False}
    authenticated = login_status(command)
    return {'live_model_run': False, 'backend': 'codex_subscription', 'codex_available': True,
            'chatgpt_login_confirmed': authenticated, 'ready': authenticated, 'api_fallback': False,
            'reason': None if authenticated else 'Sign in to Codex with ChatGPT using codex login.'}

class CodexProvider:
    def __init__(self, model='gpt-6.1-sol', reasoning='low', *, binary=None, work_dir=None,
                 timeout=120, _command=None):
        self.command = _command or cli_command(binary)
        if not login_status(self.command):
            raise ProviderError('A ChatGPT subscription login is required. No API fallback is permitted.')
        self.model, self.reasoning, self.timeout = model, reasoning, timeout
        self.work_dir = Path(work_dir or Path(__file__).with_name('results') / 'codex-runtime').resolve()
        self.invocations = self.completed_turns = 0

    def complete(self, messages):
        self.work_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='codex-turn-', dir=self.work_dir) as directory:
            # Host owns all edits/checks. These invocations are wrapper rounds,
            # not a count of Codex's internal inference requests.
            argv = [*self.command, 'exec', '--ignore-user-config', '--json', '--ephemeral',
                    '--sandbox', 'read-only', '--skip-git-repo-check', '--model', self.model,
                    *AUTH_CONFIG, '-c', 'model_reasoning_effort=' + json.dumps(self.reasoning),
                    '--disable', 'shell_tool', '--disable', 'multi_agent',
                    '--disable', 'apps', '-c', 'web_search="disabled"', '--cd', directory, '-']
            argv[-1:-1] = ['-c', 'sqlite_home=' + json.dumps(str(Path(directory) / 'state')),
                           '-c', 'log_dir=' + json.dumps(str(Path(directory) / 'logs'))]
            prompt = ('You are the decision component of a coding host. Use no native tools. '
                      'Read the conversation below and return only the next assistant JSON object. '
                      'The host will execute its declared actions. No Markdown fences.\n' + json.dumps(messages))
            try:
                self.invocations += 1
                process = subprocess.run(argv, input=prompt, env=subscription_environment(),
                                         capture_output=True, text=True, encoding='utf-8', errors='replace',
                                         timeout=self.timeout, shell=False)
            except (OSError, subprocess.TimeoutExpired):
                raise ProviderError('Codex CLI failed or timed out; diagnostic output is not logged.') from None
        if process.returncode or len(process.stdout) > 2 * 1024 * 1024:
            raise ProviderError(f'Codex CLI did not complete (exit {process.returncode}); diagnostic output is not logged.')
        result = self.parse_events(process.stdout)
        self.completed_turns += 1
        return result

    @staticmethod
    def parse_events(output):
        final, usage, completed = None, None, 0
        try:
            for line in output.splitlines():
                event = json.loads(line)
                if event.get('type') in ('turn.failed', 'error'):
                    raise ValueError('Failed turn')
                if event.get('type', '').startswith('item.'):
                    item = event.get('item', {})
                    if item.get('type') not in ('agent_message', 'reasoning'):
                        raise ValueError('Native tool use would contaminate the host experiment')
                    if event['type'] == 'item.completed' and item.get('type') == 'agent_message':
                        final = json.loads(item['text'])
                if event.get('type') == 'turn.completed':
                    completed += 1
                    counts = event.get('usage')
                    if counts is not None:
                        usage = {'input_tokens': counts['input_tokens'], 'cached_input_tokens': counts.get('cached_input_tokens', 0),
                                 'output_tokens': counts['output_tokens'],
                                 'cache_write_tokens': counts.get('cache_write_input_tokens', counts.get('cache_write_tokens', 0))}
                        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in usage.values()) or usage['cached_input_tokens'] > usage['input_tokens']:
                            raise ValueError('Invalid usage')
            if completed != 1 or not isinstance(final, dict):
                raise ValueError('Missing final result or ambiguous turn usage')
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError('Codex returned an invalid/failed turn or used native tools; output is not logged.') from None
        return final, usage
