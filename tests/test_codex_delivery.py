"""Actual pinned-Git discovery and a fake CLI process; never a model/API call."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
import flow_api_delivery as api
import flow_codex_delivery as codex
from flow_delivery_backend import route
from aim_ci import control_archive, control_package
from aim_ci.owned_paths import Refused


def pin(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class CodexDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root/'project'; self.repo.mkdir()
        self.git = Path(os.environ.get('AIM_TEST_GIT', shutil.which('git'))).resolve()
        self.git_run('init', '-q'); self.git_run('config', 'user.email', 'fixture@example.invalid')
        self.git_run('config', 'user.name', 'Fixture')
        (self.repo/'src').mkdir()
        (self.repo/'src/ShowsFeed.astro').write_text('<th>Location</th><a>Tickets</a> Upcoming Past shows')
        (self.repo/'README.md').write_text('Project documentation')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'fixture')
        self.req = self.root/'requirement.md'
        self.req.write_text('Remove Location and change Tickets in Upcoming and Past shows to READ MORE.')
        self.bin = self.root/'fake-codex'
        # A real local process exercises stdin, command-line output paths, logs,
        # environment isolation and durable single-dispatch across state paths.
        self.bin.write_text('#!'+sys.executable+'\n'+'''import json,os,pathlib,sys
if sys.argv[1:]==['login','status']:
 print('Logged in using ChatGPT');sys.exit(0)
if sys.argv[1:]==['exec','--help']:
 print('--ignore-user-config --output-schema --json --ephemeral');sys.exit(0)
assert not any(k in os.environ for k in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','GH_TOKEN'))
assert '--sandbox' in sys.argv and sys.argv[sys.argv.index('--sandbox')+1]=='read-only'
assert 'forced_login_method="chatgpt"' in sys.argv
r=json.load(sys.stdin);p=next(iter(r['editableSources']))
answer={'summary':'Replace misleading ticket label','edits':[{'path':p,'old':'Tickets','new':'READ MORE'}]}
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))
print(json.dumps({'type':'thread.started','thread_id':'synthetic-thread'}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':17,'cached_input_tokens':0,'output_tokens':5}}))
''')
        self.bin.chmod(0o700)
        self.config = self.root/'codex.json'; self.state = self.root/'attempt-1'
        self.c = {'schemaVersion': 1, 'manifestSha256': 'a'*64,
            'delivery': {'backend': 'codex-cli', 'executionHost': 'local'},
            'codex': pin(self.bin), 'model': 'explicit-fixture-model', 'reasoning': 'high',
            'requirement': pin(self.req), 'source': {'checkout': str(self.repo),
                'commit': self.git_run('rev-parse', 'HEAD').strip(), 'git': str(self.git),
                'gitSha256': pin(self.git)['sha256']}, 'allowedDirectories': ['src/'],
            'dispatchDirectory': str(self.root/'dispatch'), 'timeoutSeconds': None}
        self.save()
        # Product packaging itself is tested separately with a fresh installation.
        self.verifier = patch.object(control_package, 'verify_installation', return_value={})
        self.verifier.start(); self.addCleanup(self.verifier.stop)

    def git_run(self, *args):
        return subprocess.check_output([str(self.git), *args], cwd=self.repo,
                                       stderr=subprocess.DEVNULL, text=True)

    def save(self):
        self.config.write_bytes(api.encoded(self.c))
        self.config_sha = pin(self.config)['sha256']

    def preview(self, state=None):
        return codex.deliver(self.config, self.config_sha, state or self.state, preview=True)

    def run_once(self):
        intent = self.preview()['intentSha256']
        return codex.deliver(self.config, self.config_sha, self.state, authorize_intent=intent)

    def test_separate_route_and_cloud_refusal_without_fallback(self):
        for backend in ('responses-api', 'codex-cli'):
            local = route({'backend': backend, 'executionHost': 'local'})
            self.assertEqual(local['status'], 'available')
            cloud = route({'backend': backend, 'executionHost': 'cloud'})
            self.assertEqual(cloud['status'], 'blocked')
            self.assertFalse(cloud['automaticFallback']); self.assertIsNone(cloud['command'])
        self.c['delivery']['executionHost'] = 'cloud'; self.save()
        with patch.object(codex.subprocess, 'run', side_effect=AssertionError('no cloud dispatch')):
            with self.assertRaisesRegex(Refused, 'unavailable'): self.preview()
        self.assertFalse(self.state.exists())

    def test_preview_no_process_no_state_or_budget_write_and_drift_refused(self):
        real_run = subprocess.run
        def only_git(argv, *args, **kwargs):
            self.assertEqual(argv[0], str(self.git))
            return real_run(argv, *args, **kwargs)
        with patch.object(codex.subprocess, 'run', side_effect=only_git):
            result = self.preview()
        self.assertEqual(result['intent']['cost']['amountNanoUsd'], None)
        self.assertFalse(self.state.exists()); self.assertFalse((self.root/'dispatch').exists())
        self.req.write_text('Changed requirement')
        with self.assertRaisesRegex(Refused, 'Pinned input changed'): self.preview()

    def test_real_process_receipt_verify_and_global_request_replay_fence(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'never-forward', 'CODEX_API_KEY':'never-forward',
                                    'OPENAI_BASE_URL':'never-forward', 'GH_TOKEN':'never-forward'}):
            result = self.run_once()
        self.assertEqual(result['status'], 'proposal-ready-for-independent-review')
        self.assertIsNone(result['cost']['amountNanoUsd'])
        self.assertEqual(result['usage']['input_tokens'], 17)
        self.assertEqual((self.repo/'src/ShowsFeed.astro').read_text(), '<th>Location</th><a>Tickets</a> Upcoming Past shows')
        verified = codex.deliver(self.config,self.config_sha,self.state,verify=True)
        self.assertTrue(verified['verified'])
        other = self.root/'attempt-2'; approval = self.preview(other)['intentSha256']
        with self.assertRaises((Refused, FileExistsError)):
            codex.deliver(self.config,self.config_sha,other,authorize_intent=approval)
        self.assertFalse((other/'events.jsonl').exists())
        # A fresh copied requirement path cannot turn the same committed request
        # into a new chargeable identity.
        copied=self.root/'copied-requirement.md';copied.write_bytes(self.req.read_bytes())
        self.c['requirement']=pin(copied);self.save()
        duplicate=self.root/'attempt-3';approval=self.preview(duplicate)['intentSha256']
        with self.assertRaises((Refused,FileExistsError)):
            codex.deliver(self.config,self.config_sha,duplicate,authorize_intent=approval)
        self.assertFalse((duplicate/'events.jsonl').exists())
        self.c['requirement']=pin(self.req);self.save()
        (self.state/'events.jsonl').write_text('tampered')
        with self.assertRaises((Refused, ValueError)):
            codex.deliver(self.config,self.config_sha,self.state,verify=True)

    def test_unknown_turn_never_replayed_and_empty_edit_reports_missing_context(self):
        events = json.dumps({'type':'turn.failed','error':{'message':'quota'}}).encode()
        with self.assertRaises(Refused): codex.parse_output(events, b'{}', {})
        events = b'\n'.join(json.dumps(v).encode() for v in [
            {'type':'thread.started','thread_id':'fixture'},
            {'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}}])
        answer, receipt = codex.parse_output(events, b'{"summary":"Missing component source","edits":[]}', {})
        self.assertEqual(answer['edits'], [])
        with self.assertRaises(Refused):
            codex.parse_output(events,b'{"summary":"Out of scope","edits":[{"path":"private.key","old":"a","new":"b"}]}',{})

    def test_failed_process_leaves_attempt_and_dedupe_marker_without_success(self):
        self.bin.write_text(self.bin.read_text().replace("r=json.load(sys.stdin);p=next(iter(r['editableSources']))", "sys.exit(9)"))
        self.c['codex'] = pin(self.bin); self.save()
        with self.assertRaisesRegex(Refused, 'did not complete'): self.run_once()
        self.assertTrue((self.state/'started.json').exists())
        self.assertFalse((self.state/'result.json').exists())
        self.assertEqual(len(list((self.root/'dispatch').glob('*.json'))),1)
        with self.assertRaises((Refused, FileExistsError)): self.run_once()

    def test_api_auth_refused_before_consuming_account_usage(self):
        self.bin.write_text(self.bin.read_text().replace('Logged in using ChatGPT','Logged in using API key'))
        self.c['codex'] = pin(self.bin); self.save()
        with self.assertRaisesRegex(Refused, 'Saved ChatGPT login required'): self.run_once()
        self.assertFalse(self.state.exists()); self.assertFalse((self.root/'dispatch').exists())

    def test_fresh_source_free_installation_preview_dispatch_and_verify(self):
        self.verifier.stop()  # This case verifies the real packaged controller.
        archive=self.root/'controller.tar.gz'; installed=self.root/'installed'
        built=control_package.build(ROOT,archive,'0.1.0-local.codex-test')
        receipt=control_archive.install(archive,built['archiveSha256'],installed)
        self.c['manifestSha256']=receipt['manifestSha256'];self.save()
        def invoke(*extra):
            process=subprocess.run([sys.executable,'-IBS',str(installed/'aim-flow.py'),'deliver-codex',
                '--config',str(self.config),'--config-sha256',self.config_sha,
                '--state',str(self.state),*extra],cwd=self.root,capture_output=True,text=True,
                env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'})
            self.assertEqual(process.returncode,0,process.stderr)
            return json.loads(process.stdout)
        preview=invoke('--preview');self.assertFalse(self.state.exists())
        result=invoke('--authorize-intent',preview['intentSha256'])
        self.assertEqual(result['status'],'proposal-ready-for-independent-review')
        self.assertTrue(invoke('--verify')['verified'])
        (installed/'lib/flow_codex_delivery.py').write_text('# drift')
        # Verify installation integrity directly; no model is invoked on drift.
        with self.assertRaises(Refused):control_package.verify_installation(installed,receipt['manifestSha256'])


class CodexInputTests(unittest.TestCase):
    def test_exact_source_union_and_conflicting_editable_source(self):
        sources={'src/label.py':'exact \\"\n å😀', 'README.md':'process context'}
        editable={'src/label.py':sources['src/label.py']}
        context=codex.distinct_context(sources,editable)
        self.assertEqual({**context,**editable},sources)
        self.assertFalse(set(context)&set(editable))
        with self.assertRaises(Refused):codex.distinct_context(sources,{'src/label.py':'changed'})
        with self.assertRaises(Refused):codex.distinct_context(sources,{'missing.py':'missing'})

    def test_exact_serialized_character_boundary_includes_json_escaping(self):
        raw=codex.checked_input({'text':'å😀\\"\n'},'fixture')
        self.assertEqual(raw,api.encoded({'text':'å😀\\"\n'}))
        self.assertEqual(codex.input_summary(raw)['characters'],len(raw.decode()))
        self.assertGreater(len(raw),len(raw.decode()))
        overhead=len(api.encoded({'text':''}).decode())
        request={'text':'x'*(codex.MAX_INPUT_CHARACTERS-overhead)}
        self.assertEqual(codex.input_summary(codex.checked_input(request,'fixture'))['characters'],codex.MAX_INPUT_CHARACTERS)
        request['text']+='x'
        with self.assertRaisesRegex(Refused,'input_too_large before dispatch'):codex.checked_input(request,'fixture')
        with self.assertRaises(Refused):codex.checked_input({'text':'\\'*(codex.MAX_INPUT_CHARACTERS//2)},'escaped')

    def test_every_native_phase_rejects_oversize_without_cli_or_dispatch_marker(self):
        import flow_codex_stage as stage
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            for phase in ('model-implementation','model-source-review','model-executed-review'):
                with self.subTest(phase=phase), patch.object(stage.subprocess,'run',side_effect=AssertionError('no CLI invocation')):
                    with self.assertRaisesRegex(Refused,phase+' input_too_large before dispatch'):
                        stage.stage({},root/phase,phase,{'text':'x'*codex.MAX_INPUT_CHARACTERS},{})
            self.assertEqual(list(root.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
