"""Fresh installed host-command driver, local synthetic effects only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import project_command as driver
from aim_ci.owned_paths import Refused
import test_project_onboarding as fixtures

sha = fixtures.sha


class ProjectCommandTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectOnboardingTests.setUpClass.__func__)
    git_run = fixtures.ProjectOnboardingTests.git_run

    def setUp(self):
        fixtures.ProjectOnboardingTests.setUp(self)
        (self.checkout / '.gitignore').write_text('.receipts/\n')
        (self.checkout / 'ship.py').write_text('''import json, os, pathlib, sys, time
p = pathlib.Path('.receipts'); p.mkdir(exist_ok=True)
mode = sys.argv[1]
if mode == 'overflow': print('x' * 10000)
if mode == 'timeout': time.sleep(5)
if mode == 'drift': pathlib.Path('README.md').write_text('changed')
if mode == 'bin-drift': (pathlib.Path(os.environ['PATH']) / 'unexpected').write_text('changed')
if mode != 'missing':
    record = {} if mode == 'empty' else {'status':'verified', 'commit':sys.argv[2], 'run':42, 'attempt':2, 'selected':{'web':['public/page.html'],'api':[]}, 'verified':True}
    if mode == 'wrong-attempt': record['attempt'] = 1
    if mode == 'wrong-type': record['verified'] = 1
    if mode == 'private-record': record['private'] = os.environ['PRIVATE_FIXTURE_VALUE']
    (p / 'record-new.json').write_text(json.dumps(record))
print('project command complete')
if mode == 'private-drift': pathlib.Path(os.environ['PRIVATE_FIXTURE_PATH']).write_text('changed')
if 'PRIVATE_FIXTURE_VALUE' in os.environ:
    print(os.environ['PRIVATE_FIXTURE_VALUE'])
    assert 'UNSELECTED_FIXTURE_VALUE' not in os.environ
assert 'AMBIENT_MUST_NOT_LEAK' not in os.environ
if mode == 'nonzero': sys.exit(7)
''')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Synthetic release command')
        self.commit = self.git_run('rev-parse', 'HEAD')
        self.evidence = self.case / 'evidence.json'
        self.evidence.write_text('{"status":"passed","candidate":"reviewed"}\n')
        self.declaration = self.case / 'command.json'
        self.state = self.case / 'attempt'
        self.python = Path(sys.executable).resolve()
        self.d = {'format':driver.DECLARATION, 'schemaVersion':1,
            'target':{'checkout':str(self.checkout), 'commit':self.commit, 'tree':self.git_run('rev-parse', 'HEAD^{tree}')},
            'git':{'path':str(self.git), 'sha256':self.git_pin},
            'tools':{'python':{'path':str(self.python), 'sha256':sha(self.python)}},
            'trees':[], 'inputs':[], 'evidence':[{'path':str(self.evidence), 'sha256':sha(self.evidence),
                'assertions':[{'path':['status'],'equals':'passed'}]}],
            'command':{'tool':'python', 'arguments':['-IBS', 'ship.py', 'success', self.commit]},
            'environment':{'LC_ALL':'C'}, 'environmentFile':None,
            'scope':{'destinations':['local fixture only'], 'selections':{'web':['public/page.html'], 'api':[]}},
            'review':{'reference':'Synthetic offline test', 'effects':['Creates ignored local JSON file only'],
                'qualifications':['No network, production or credential access'],
                'prerequisites':['Fixture evidence was created separately; no actual CI or merge.']},
            'receipt':{'directory':'.receipts','filenamePattern':'record-*.json','meaning':'Synthetic fixture finished',
                'assertions':[{'path':['status'],'equals':'verified'}, {'path':['commit'],'equals':self.commit},
                    {'path':['run'],'equals':42}, {'path':['attempt'],'equals':2},
                    {'path':['selected'],'equals':{'web':['public/page.html'],'api':[]}}, {'path':['verified'],'equals':True}]},
            'timeoutSeconds':10,'outputBytes':100000}
        self.save()

    def save(self):
        self.declaration.write_text(json.dumps(self.d, indent=2) + '\n')

    def args(self, **overrides):
        values = dict(action='run-project-command', manifest_sha256=self.release['manifestSha256'],
            declaration=self.declaration, declaration_sha256=sha(self.declaration), state=self.state,
            preview=True, authorize_intent=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def run_driver(self):
        args = self.args()
        preview = driver.command(self.installed, args)
        args.preview = False; args.authorize_intent = preview['intentSha256']
        return driver.command(self.installed, args)

    def cli(self, *extra, expected=0):
        argv = [str(self.python), '-IBS', str(self.installed / 'aim-flow.py'), *extra,
            '--manifest-sha256', self.release['manifestSha256'], '--declaration', str(self.declaration),
            '--declaration-sha256', sha(self.declaration), '--state', str(self.state)]
        env = {**self.env, 'AMBIENT_MUST_NOT_LEAK':'true'}
        result = subprocess.run(argv, cwd=self.case, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout) if not result.stderr else result.stderr

    def test_installed_preview_exact_dispatch_receipt_and_historical_verify(self):
        preview = self.cli('run-project-command', '--preview')
        self.assertFalse(self.state.exists())
        self.assertFalse(preview['executionAuthorized'])
        self.assertEqual(preview['intent']['declaration']['scope'], self.d['scope'])
        result = self.cli('run-project-command', '--authorize-intent', preview['intentSha256'])
        self.assertEqual(result['status'], 'project-receipt-matched')
        self.assertEqual(result['acceptance'], 'not-established')
        self.assertFalse(result['replayAuthorized'])
        self.assertEqual(result['prerequisitesPerformedSeparately'], self.d['review']['prerequisites'])
        (self.checkout / '.receipts/record-new.json').unlink()
        self.git_run('commit', '--allow-empty', '-qm', 'Later HEAD is not a replay')
        verified = self.cli('verify-project-command', '--receipt-sha256', result['receiptSha256'])
        self.assertEqual(verified['status'], 'project-receipt-matched')

    def test_no_receipt_empty_wrong_identity_or_nonzero_are_unknown(self):
        for mode in ('missing','empty','wrong-attempt','wrong-type','nonzero'):
            with self.subTest(mode=mode):
                self.state = self.case / ('attempt-' + mode)
                self.d['command']['arguments'][2] = mode; self.save()
                path = self.checkout / '.receipts/record-new.json'
                if path.exists(): path.unlink()
                result = self.run_driver()
                self.assertEqual(result['status'], 'outcome-unknown')
                self.assertFalse(result['replayAuthorized'])

    def test_existing_record_is_not_completion_and_attempt_is_not_replayed(self):
        (self.checkout / '.receipts').mkdir()
        (self.checkout / '.receipts/record-new.json').write_text('{}')
        result = self.run_driver()
        self.assertEqual(result['status'], 'outcome-unknown')
        with self.assertRaises(Refused): self.run_driver()

    def test_wrong_authorization_source_and_evidence_drift_refuse_before_effects(self):
        args = self.args(preview=False, authorize_intent='0' * 64)
        with self.assertRaises(Refused): driver.command(self.installed, args)
        self.assertFalse(self.state.exists())
        preview = driver.command(self.installed, self.args())
        self.evidence.write_text('{"status":"failed"}')
        with self.assertRaises(Refused): driver.command(self.installed, self.args(preview=False, authorize_intent=preview['intentSha256']))
        self.evidence.write_text('{"status":"passed","candidate":"reviewed"}\n')
        (self.checkout / 'ship.py').write_text('raise SystemExit(0)\n')
        with self.assertRaises(Refused): driver.command(self.installed, self.args())
        self.assertFalse(self.state.exists())

    def test_started_marker_precedes_launch_and_interruption_cannot_replay(self):
        def interrupted(*args):
            self.assertTrue((self.state / 'started.json').is_file())
            self.assertTrue((self.state / 'intent.json').is_file())
            raise KeyboardInterrupt()
        with patch.object(driver, 'capture', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.run_driver()
        self.assertEqual(json.loads((self.state/'failure.json').read_text())['status'], 'outcome-unknown')
        with self.assertRaises(Refused): self.run_driver()

    def test_timeout_output_limit_and_post_effect_source_drift_stay_unknown(self):
        for mode in ('timeout','overflow','bin-drift','drift'):
            with self.subTest(mode=mode):
                self.state = self.case / ('attempt-' + mode)
                self.d['command']['arguments'][2] = mode
                self.d['timeoutSeconds'] = 1; self.d['outputBytes'] = 1000; self.save()
                path = self.checkout / '.receipts/record-new.json'
                if path.exists(): path.unlink()
                result = self.run_driver()
                self.assertEqual(result['status'], 'outcome-unknown')

    def test_private_reference_preview_does_not_read_and_dispatch_retains_no_values(self):
        private = self.case / 'private.env'
        private.write_text('PRIVATE_FIXTURE_VALUE="synthetic-private-needle"\nUNSELECTED_FIXTURE_VALUE=excluded\n')
        self.d['environmentFile'] = {'path':str(private),'sha256':sha(private),'keys':['PRIVATE_FIXTURE_VALUE']}
        self.save()
        with patch.object(driver, 'private_environment', side_effect=AssertionError('preview read credentials')):
            preview = driver.command(self.installed, self.args())
        self.assertNotIn('synthetic-private-needle', json.dumps(preview))
        result = self.run_driver()
        self.assertEqual(result['status'], 'project-receipt-matched')
        for path in self.state.rglob('*'):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(b'synthetic-private-needle', path.read_bytes())
                self.assertNotIn(b'UNSELECTED_FIXTURE_VALUE', path.read_bytes())
        self.assertIn('[REDACTED]', (self.state/'command.stdout').read_text())

    def test_explicit_nonconfidential_config_metadata_can_appear_in_project_record(self):
        private=self.case/'metadata.env'; private.write_text('PRIVATE_FIXTURE_VALUE=verified\n')
        self.d['environmentFile']={'path':str(private),'sha256':sha(private),
            'keys':['PRIVATE_FIXTURE_VALUE'],'confidentialKeys':[]}
        self.save(); result=self.run_driver()
        self.assertEqual(result['status'],'project-receipt-matched')
        self.assertNotIn('PRIVATE_FIXTURE_VALUE=verified',(self.state/'intent.json').read_text())

    def test_short_confidential_value_does_not_expand_retained_log_budget(self):
        private=self.case/'short.env'; private.write_text('PRIVATE_FIXTURE_VALUE=x\n')
        self.d['environmentFile']={'path':str(private),'sha256':sha(private),'keys':['PRIVATE_FIXTURE_VALUE']}
        self.d['command']['arguments'][2]='overflow'; self.d['outputBytes']=1000
        self.save(); result=self.run_driver()
        self.assertEqual(result['status'],'outcome-unknown')
        self.assertEqual(result['process']['termination'],'output-limit')
        size=sum((self.state/('command.'+name)).stat().st_size for name in ('stdout','stderr'))
        self.assertLessEqual(size,1000)
        self.assertNotIn('x',(self.state/'command.stdout').read_text())

    def test_changed_private_config_refuses_after_started_without_command_or_replay(self):
        private = self.case/'private.env'; private.write_text('PRIVATE_FIXTURE_VALUE=old\n')
        self.d['environmentFile'] = {'path':str(private),'sha256':sha(private),'keys':['PRIVATE_FIXTURE_VALUE']}
        self.save(); private.write_text('PRIVATE_FIXTURE_VALUE=changed\n')
        with self.assertRaises(Refused): self.run_driver()
        self.assertTrue((self.state/'started.json').is_file())
        self.assertFalse((self.state/'command.stdout').exists())
        with self.assertRaises(Refused): self.run_driver()

    def test_copied_old_receipt_and_private_drift_or_escaped_receipt_are_unknown(self):
        for mode in ('old-copy', 'private-drift', 'private-record'):
            with self.subTest(mode=mode):
                self.state=self.case/('attempt-'+mode)
                receipts=self.checkout/'.receipts'; receipts.mkdir(exist_ok=True)
                fresh=receipts/'record-new.json'
                if fresh.exists(): fresh.unlink()
                self.d['command']['arguments'][2]=mode
                if mode == 'old-copy':
                    old={'status':'verified', 'commit':self.commit, 'run':42, 'attempt':2,
                        'selected':{'web':['public/page.html'],'api':[]}, 'verified':True}
                    (receipts/'record-old.json').write_text(json.dumps(old))
                else:
                    old_path=receipts/'record-old.json'
                    if old_path.exists(): old_path.unlink()
                    private=self.case/'private.env'; private.write_text("PRIVATE_FIXTURE_VALUE='synthetic\"private\\needle'\n")
                    self.d['environmentFile']={'path':str(private),'sha256':sha(private),'keys':['PRIVATE_FIXTURE_VALUE']}
                    self.d['environment']['PRIVATE_FIXTURE_PATH']=str(private)
                self.save(); result=self.run_driver()
                self.assertEqual(result['status'],'outcome-unknown')
                self.assertFalse((self.state/'project-receipt.json').exists())

    def test_malformed_assertion_contract_is_refused_in_preview(self):
        for checks in ([{}], [{'path':[], 'equals':1}], [{'path':[True], 'equals':1}],
                       [{'path':[-1], 'equals':1}], [{'path':[[1]], 'equals':1}]):
            self.d['receipt']['assertions']=checks; self.save()
            with patch.object(driver, 'capture', side_effect=AssertionError('dispatched invalid contract')):
                with self.assertRaises(Refused): driver.command(self.installed, self.args())
            self.assertFalse(self.state.exists())

    def test_preview_never_invokes_git_source_filters_and_rejects_staged_changes(self):
        (self.checkout/'.gitattributes').write_text('README.md filter=tripwire\n')
        self.git_run('add','.gitattributes'); self.git_run('commit','-qm','Filter declaration fixture')
        self.d['target'].update(commit=self.git_run('rev-parse','HEAD'),tree=self.git_run('rev-parse','HEAD^{tree}'))
        # Git status could execute this clean filter after the tracked stat changes.
        self.git_run('config','filter.tripwire.clean',str(self.python)+' -c '+
            repr("from pathlib import Path; Path("+repr(str(self.marker))+").touch()"))
        os.utime(self.checkout/'README.md', (2000000000, 2000000000)); self.save()
        driver.command(self.installed,self.args())
        self.assertFalse(self.marker.exists())
        (self.checkout/'staged.txt').write_text('staged only')
        self.git_run('add','staged.txt')
        with self.assertRaises(Refused): driver.command(self.installed,self.args())

    def test_log_and_receipt_tampering_fail_historical_verification(self):
        result = self.run_driver()
        args = self.args(action='verify-project-command', receipt_sha256=result['receiptSha256'])
        path = self.state / 'command.stdout'; saved = path.read_bytes(); path.write_bytes(b'fake')
        with self.assertRaises(Refused): driver.command(self.installed, args)
        path.write_bytes(saved)
        (self.state/'project-receipt.json').write_text('{}')
        with self.assertRaises(Refused): driver.command(self.installed, args)

    def test_environment_state_receipt_path_and_tool_pins_refuse(self):
        original = copy.deepcopy(self.d)
        for mutate in (lambda d:d['environment'].update(PATH='/usr/bin'),
                       lambda d:d['environment'].update(GH_TOKEN='not-a-real-token'),
                       lambda d:d['tools']['python'].update(sha256='0'*64),
                       lambda d:d['receipt'].update(directory='../escape'),
                       lambda d:d['receipt'].update(filenamePattern='../record.json')):
            self.d = copy.deepcopy(original); mutate(self.d); self.save()
            with self.assertRaises(Refused): driver.command(self.installed, self.args())
        self.d=original; self.save(); self.state=self.checkout/'unsafe-state'
        with self.assertRaises(Refused): driver.command(self.installed, self.args())

    def prepare_runtime_fixture(self, mode='success'):
        (self.checkout / 'prepare.py').write_text("""import pathlib, sys
root = pathlib.Path('.receipts/runtime')
mode = sys.argv[1]
if mode != 'missing':
    (root / '.bin').mkdir(parents=True)
    (root / 'dependency.py').write_text('print(42)')
    (root / '.bin/tool').symlink_to('../dependency.py')
if mode == 'drift': pathlib.Path('package-lock.json').write_text('changed')
if mode == 'nonzero': sys.exit(7)
""")
        (self.checkout / 'package-lock.json').write_text('{"lockfileVersion":3}\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Runtime preparation fixture')
        self.d['schemaVersion'] = 2
        self.d['target'].update(commit=self.git_run('rev-parse', 'HEAD'), tree=self.git_run('rev-parse', 'HEAD^{tree}'))
        self.d['command']['arguments'] = ['-IBS', 'prepare.py', mode]
        self.d['receipt'] = {'mode':'runtime-preparation', 'outputTrees':['.receipts/runtime'],
                             'meaning':'Local dependency preparation only, not deployment'}
        self.save()

    def test_runtime_preparation_real_command_and_reusable_tree_pin(self):
        self.prepare_runtime_fixture()
        result = self.run_driver()
        self.assertEqual(result['status'], 'runtime-preparation-completed', result)
        self.assertEqual(result['schemaVersion'], 2)
        self.assertIsNone(result['projectReceipt'])
        self.assertEqual(result['acceptance'], 'not-established')
        output = result['preparedRuntimeTrees'][0]
        self.assertEqual(driver.tree_identity(output['path'])['sha256'], output['sha256'])
        self.assertFalse((self.state / 'project-receipt.json').exists())
        # Ordinary subsequent command consumes this measured runtime, no new mode.
        self.d['schemaVersion'] = 1
        self.d['trees'] = [output]
        self.d['receipt'] = {'directory':'.receipts', 'filenamePattern':'record-*.json',
            'meaning':'Fixture follow-up', 'assertions':[{'path':['status'], 'equals':'verified'}]}
        self.d['command']['arguments'] = ['-IBS', 'ship.py', 'success', self.d['target']['commit']]
        self.state = self.case / 'consumer'; self.save()
        self.assertEqual(self.run_driver()['status'], 'project-receipt-matched')

    def test_runtime_preparation_failure_missing_and_source_drift(self):
        self.prepare_runtime_fixture()
        import shutil
        for mode in ('nonzero', 'missing', 'drift'):
            with self.subTest(mode=mode):
                self.git_run('restore', 'package-lock.json')
                shutil.rmtree(self.checkout / '.receipts/runtime', ignore_errors=True)
                self.state = self.case / ('runtime-' + mode)
                self.d['command']['arguments'][-1] = mode; self.save()
                result = self.run_driver()
                self.assertEqual(result['status'], 'outcome-unknown', result)
                self.assertNotIn('preparedRuntimeTrees', result)
                self.assertFalse(result['replayAuthorized'])
                if mode == 'drift': self.assertFalse(result['inputsUnchanged'])

    def test_runtime_preparation_existing_or_overlapping_outputs_refused(self):
        self.prepare_runtime_fixture()
        original = copy.deepcopy(self.d)
        for paths in (['.git/objects'], ['scripts'], ['.receipts/runtime', '.receipts/runtime/child'], ['../outside']):
            with self.subTest(paths=paths):
                self.d = copy.deepcopy(original); self.d['receipt']['outputTrees'] = paths; self.save()
                with self.assertRaises(Refused): driver.command(self.installed, self.args())
        self.d = original; self.save()
        (self.checkout / '.receipts/runtime').mkdir(parents=True)
        with self.assertRaisesRegex(Refused, 'fresh absent'): driver.command(self.installed, self.args())

    def test_runtime_preparation_historical_verification_and_no_replay(self):
        self.prepare_runtime_fixture()
        result = self.run_driver()
        verify_args = self.args(action='verify-project-command', receipt_sha256=result['receiptSha256'])
        (self.checkout / '.receipts/runtime/dependency.py').write_text('changed later')
        self.assertEqual(driver.command(self.installed, verify_args)['status'], 'runtime-preparation-completed')
        # Consumers must verify the output tree at use, not infer current validity.
        self.assertNotEqual(driver.tree_identity(result['preparedRuntimeTrees'][0]['path'])['sha256'],
                            result['preparedRuntimeTrees'][0]['sha256'])
        with self.assertRaises(Refused): self.run_driver()
        (self.state / 'runtime-preparation.json').write_text('{}')
        with self.assertRaises(Refused): driver.command(self.installed, verify_args)

    def test_runtime_preparation_installed_cli(self):
        self.prepare_runtime_fixture()
        preview = self.cli('run-project-command', '--preview')
        self.assertEqual(preview['intent']['schemaVersion'], 2)
        result = self.cli('run-project-command', '--authorize-intent', preview['intentSha256'])
        self.assertEqual(result['status'], 'runtime-preparation-completed', result)
        verified = self.cli('verify-project-command', '--receipt-sha256', result['receiptSha256'])
        self.assertEqual(verified['preparedRuntimeTrees'], result['preparedRuntimeTrees'])


if __name__ == '__main__': unittest.main()
