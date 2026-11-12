"""Subscription CLI contract tests using a fake process; no model inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from benchmark import run_pairs
from providers import CodexProvider, ProviderError, cli_command, preflight

FAKE_CLI = '''import json,os,sys
from pathlib import Path
config_path=Path(sys.argv[1])
config=json.loads(config_path.read_text())
if 'login' in sys.argv:
    print(config.get('login','Logged in using ChatGPT'))
    raise SystemExit(config.get('login_exit',0))
Path(config['audit']).write_text(json.dumps({'argv':sys.argv,'stdin':sys.stdin.read(),
    'api_environment':[k for k in os.environ if k in ('OPENAI_API_KEY','CODEX_API_KEY','OPENROUTER_API_KEY','CODEX_ACCESS_TOKEN')]}))
print(config.get('raw') or '\\n'.join(json.dumps(event) for event in config['events']))
print('private diagnostic',file=sys.stderr)
raise SystemExit(config.get('exit',0))
'''

def events(usage=True):
    result=[{'type':'thread.started','thread_id':'fake'}, {'type':'turn.started'},
            {'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'final':'done'})}},
            {'type':'turn.completed'}]
    if usage:
        result[-1]['usage']={'input_tokens':120,'cached_input_tokens':40,'output_tokens':30}
    return result

class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='checkgate-subscription-',dir=Path(__file__).resolve().parent)
        self.base=Path(self.temporary.name).resolve()
        self.assertTrue(self.base.is_relative_to(Path(__file__).resolve().parent))
        self.script=self.base/'fake_cli.py'
        self.script.write_text(FAKE_CLI,encoding='utf-8')
        self.config=self.base/'config.json'
        self.audit=self.base/'audit.json'
        self.command=[sys.executable,'-B',str(self.script),str(self.config)]
        self.settings={'audit':str(self.audit),'events':events()}
        self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self):
        self.config.write_text(json.dumps(self.settings),encoding='utf-8')

    def provider(self):
        return CodexProvider(_command=self.command,work_dir=self.base/'runtime')

    def test_subscription_process_contract_and_usage(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key','CODEX_API_KEY':'test-key','CODEX_ACCESS_TOKEN':'test-token'}):
            provider=self.provider()
            result,usage=provider.complete([{'role':'user','content':'synthetic'}])
        self.assertEqual(result,{'final':'done'})
        self.assertEqual(usage,{'input_tokens':120,'cached_input_tokens':40,'output_tokens':30,'cache_write_tokens':0})
        audit=json.loads(self.audit.read_text())
        self.assertEqual(audit['api_environment'],[])
        for flag in ('forced_login_method="chatgpt"','--ignore-user-config','--ephemeral','read-only','shell_tool'):
            self.assertIn(flag,audit['argv'])
        self.assertIn('synthetic',audit['stdin'])
        self.assertEqual(provider.invocations,1)
        self.assertEqual(provider.completed_turns,1)

    def test_api_login_is_rejected_before_exec(self):
        self.settings['login']='Logged in using an API key'
        self.save()
        with self.assertRaises(ProviderError): self.provider()
        self.assertFalse(self.audit.exists())

    def test_signed_out_login_is_rejected(self):
        self.settings.update(login='Not logged in',login_exit=1)
        self.save()
        with self.assertRaises(ProviderError): self.provider()

    def test_missing_usage_is_not_invented(self):
        self.settings['events']=events(False)
        self.save()
        self.assertIsNone(self.provider().complete([])[1])

    def test_malformed_usage_is_rejected(self):
        for malformed in (True,-1,'100',None):
            with self.subTest(value=malformed):
                sample=events()
                sample[-1]['usage']['input_tokens']=malformed
                with self.assertRaises(ProviderError): CodexProvider.parse_events('\n'.join(map(json.dumps,sample)))

    def test_cli_cache_write_usage_is_preserved(self):
        sample=events()
        sample[-1]['usage']['cache_write_input_tokens']=17
        _, usage=CodexProvider.parse_events('\n'.join(map(json.dumps,sample)))
        self.assertEqual(usage['cache_write_tokens'],17)

    def test_native_tools_are_rejected_as_contaminated(self):
        sample=events()
        sample.insert(2,{'type':'item.completed','item':{'type':'command_execution','command':'not-executed'}})
        with self.assertRaises(ProviderError): CodexProvider.parse_events('\n'.join(map(json.dumps,sample)))

    def test_failed_or_multiple_turns_are_rejected(self):
        for sample in ([{'type':'turn.failed','error':{'message':'private'}}],events()+[events()[-1]]):
            with self.assertRaises(ProviderError): CodexProvider.parse_events('\n'.join(map(json.dumps,sample)))

    def test_cli_failure_does_not_echo_diagnostics(self):
        self.settings['exit']=1
        self.save()
        with self.assertRaises(ProviderError) as raised: self.provider().complete([])
        self.assertNotIn('private diagnostic',str(raised.exception))
        self.assertIn('exit 1',str(raised.exception))

    def test_malformed_json_does_not_echo_output(self):
        self.settings['raw']='private invalid output'
        self.save()
        with self.assertRaises(ProviderError) as raised: self.provider().complete([])
        self.assertNotIn('private invalid output',str(raised.exception))

    def test_preflight_missing_binary_is_blocked(self):
        report=preflight(self.base/'not-installed.exe')
        self.assertFalse(report['ready'])
        self.assertFalse(report['api_fallback'])
        self.assertFalse(report['live_model_run'])

    def test_provider_start_failure_is_recorded_in_both_arms(self):
        def factory(): raise ProviderError('Subscription unavailable')
        report=run_pairs(factory,self.base/'report',trials=1)
        pair=report['pairs'][0]
        self.assertFalse(pair['both_succeeded'])
        self.assertEqual(pair['baseline']['decision'],'provider_error')
        self.assertEqual(pair['controller']['decision'],'provider_error')
        self.assertNotIn('token_change_percent',pair)

    def test_live_command_requires_subscription_and_has_no_api_option(self):
        run=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('benchmark.py')),'--live','--codex-bin',str(self.base/'missing.exe')],
                           capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,2,run.stderr)
        self.assertFalse(json.loads(run.stdout)['api_fallback'])

if __name__=='__main__': unittest.main()
