"""Local proposal and reviewed private-candidate fixtures, never project code or live transports."""
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from openai_budget import MODEL, Refused, fingerprint
from aim_ci import controller as c, control_package
from aim_ci.budget import Budget


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


class IntakeProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git_tool = Path(shutil.which('git')).resolve()
        version = subprocess.check_output([str(cls.git_tool), '--version']).decode()
        import re
        match = re.match(r'git version (\d+)\.(\d+)', version)
        if not match or tuple(map(int, match.groups())) < (2, 54):
            raise unittest.SkipTest('Real Git 2.54 or newer is required for intake profile tests')
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.package_root = Path(folder.name).resolve()
        archive = cls.package_root / 'controller.tar.gz'
        cls.package = control_package.build(ROOT, archive, '0.1.0-intake-profile.1')
        cls.installed = cls.package_root / 'installed'
        control_package.install(archive, cls.package['archiveSha256'], cls.installed)

    def git(self, *args):
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file'}
        return subprocess.check_output([str(self.git_tool), '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.fsmonitor=false', *args], cwd=self.checkout, env=env).decode().strip()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.checkout = self.root / 'astro-project'
        self.checkout.mkdir()
        self.source_path = 'src/pages/index.astro'
        source = self.checkout / self.source_path
        source.parent.mkdir(parents=True)
        source.write_text('<h1>Hello reader</h1>\n')
        (self.checkout / 'package.json').write_bytes(encoded({'name': 'intake-only-fixture',
            'scripts': {'build': 'DO_NOT_RUN_PROJECT_SCRIPTS'}, 'dependencies': {'astro': '^5.0.0'}}))
        self.git('init', '--template=', '-b', 'main')
        self.git('config', 'user.name', 'Local intake fixture')
        self.git('config', 'user.email', 'fixture@invalid')
        self.git('add', '--', 'src/pages/index.astro', 'package.json')
        self.git('commit', '--no-gpg-sign', '-m', 'Astro-shaped local intake fixture')
        self.repository = 'synthetic/astro-customer'
        self.origin = 'https://github.com/' + self.repository + '.git'
        self.git('remote', 'add', 'origin', self.origin)
        self.worker = self.root / 'worker'
        self.legacy = self.root / 'legacy.json'
        self.legacy.write_text('{"requests":[]}')
        self.budget = Budget(self.worker, self.legacy)
        self.profile_file = self.root / 'intake-profile.json'
        self.profile = {'kind': c.INTAKE_PROFILE_KIND, 'version': 1, 'scope': 'proposal-only',
            'checkout': str(self.checkout),
            'target': {'repository': self.origin, 'branch': 'main', 'commit': self.git('rev-parse', 'HEAD')},
            'tree': self.git('rev-parse', 'HEAD^{tree}'),
            'git': {'path': str(self.git_tool), 'sha256': sha(self.git_tool.read_bytes())},
            'allowedPaths': [self.source_path]}
        self.selection = self.save_profile(self.profile)
        self.task_file = self.root / 'local-card-fixture.json'
        self.task = {'kind': 'local-task', 'version': 1, 'id': 'aiwebsite-local-card-fixture',
            'title': 'Local card fixture: review the greeting',
            'body': '## Scope\nLocal card fixture only; the Kanban source is not built.\n'
                    '## Acceptance criteria\n- [ ] Show the reviewed greeting'}
        self.task_file.write_bytes(encoded(self.task))
        self.task_selection = self.task_file, sha(self.task_file.read_bytes())

    def save_profile(self, value):
        self.profile_file.write_bytes(encoded(value))
        return self.profile_file, sha(self.profile_file.read_bytes())

    @contextmanager
    def guarded(self):
        with patch.object(c, 'STATE', self.worker), patch.object(c, 'legacy_budget_path', return_value=self.legacy), \
                patch.object(c, 'github', side_effect=AssertionError('GitHub forbidden')), \
                patch.object(c, 'transport_from_key_file', side_effect=AssertionError('Credentials forbidden')), \
                patch.object(c, 'human_review_pause', side_effect=AssertionError('Technical review forbidden')), \
                patch.object(c, 'publish_candidate', side_effect=AssertionError('Publication forbidden')), \
                patch.object(c.probe, 'validate', side_effect=AssertionError('Project execution forbidden')), \
                patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')), \
                redirect_stdout(io.StringIO()):
            yield

    def preview(self, selection=None):
        return c.preview_root_admission(selection or self.selection, root_task=self.task_selection, intake_only=True)

    @contextmanager
    def candidate_guarded(self):
        command = c.command
        def git_only(argv, *args, **kwargs):
            self.assertEqual(argv[0], str(self.git_tool))
            return command(argv, *args, **kwargs)
        with self.guarded(), patch.object(c, 'model_call', side_effect=AssertionError('Model forbidden')), \
                patch.object(c, 'command', side_effect=git_only):
            yield

    def test_real_installed_no_key_preview_is_read_only_and_scope_bound(self):
        before = snapshot(self.root)
        installed_before = snapshot(self.installed)
        # Run the verified installed entry with guarded credentials, sockets and every subprocess.
        driver = '''import runpy, socket, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'lib'))
from aim_ci import controller as c
def forbidden(*args, **kwargs):
    raise AssertionError('Credential or network access forbidden')
c.transport_from_key_file = forbidden
c.github = forbidden
socket.socket = forbidden
git_pin = sys.argv[2]
run = subprocess.run
def readonly(argv, *args, **kwargs):
    assert argv[0] == git_pin, argv
    assert argv[6] in ('--version','rev-parse','cat-file','symbolic-ref','config','remote','ls-tree'), argv
    if argv[6] == 'remote':
        assert argv[7] == 'get-url', argv
    if argv[6] == 'config':
        assert argv[7:] == ['--local','--no-includes','--get-all','remote.origin.url'], argv
    return run(argv, *args, **kwargs)
subprocess.run = readonly
entry = str(Path(sys.argv[1]) / 'aim-ci-control.py')
sys.argv = [entry, *sys.argv[3:]]
runpy.run_path(entry, run_name='__main__')
'''
        args = ['preview-root-admission', '--manifest-sha256', self.package['manifestSha256'],
            '--state-dir', str(self.worker), '--legacy-budget', str(self.legacy),
            '--profile', str(self.profile_file), '--profile-sha256', self.selection[1],
            '--task-file', str(self.task_file), '--task-sha256', self.task_selection[1], '--output-tokens', '20000']
        env = {'PATH': str(self.git_tool.parent) + ':/usr/bin:/bin', 'HOME': str(self.root)}
        result = subprocess.run([sys.executable, '-IBS', '-c', driver, str(self.installed), str(self.git_tool), *args],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(snapshot(self.installed), installed_before)
        self.assertFalse(preview['executionAuthorized'] or preview['liveModelAvailable'])
        self.assertEqual(preview['modelRequest']['max_output_tokens'], 20000)
        self.assertEqual(preview['intent']['maxOutputTokens'], 20000)
        self.assertEqual(preview['intent']['controllerInstallation']['manifestSha256'], self.package['manifestSha256'])
        self.assertEqual(preview['intent']['task']['dataSha256'], fingerprint(self.task))
        self.assertEqual(preview['intent']['profile']['sha256'], self.selection[1])
        self.assertEqual(preview['intent']['target']['commit'], self.profile['target']['commit'])
        scope = preview['intent']['intakeScope']
        self.assertEqual(scope['scope'], 'proposal-only')
        self.assertEqual(scope['buildQualification'], 'not-selected')
        payload = json.loads(preview['modelRequest']['input'])
        self.assertEqual(payload['files'], {self.source_path: '<h1>Hello reader</h1>\n'})
        self.assertEqual(payload['controllerAuthorization']['intakeScope'], scope)
        self.assertFalse(payload['controllerAuthorization']['authorAuthenticated'])
        self.assertEqual(preview['projectGrantRequirements']['status'], 'approval-required')
        self.assertEqual(preview['projectGrantRequirements']['usedCallSlots'], 0)
        self.assertEqual(preview['handoff']['blocked'],
                         ['technical-validation', 'project-execution'])
        self.assertIn('preview-reviewed-private-candidate', preview['handoff']['available'])

    def select_large_css(self, raw):
        self.source_path = 'src/styles/site.css'
        selected = self.checkout / self.source_path
        selected.parent.mkdir(parents=True)
        selected.write_bytes(raw)
        self.git('add', '--', self.source_path)
        self.git('commit', '--no-gpg-sign', '-m', 'Complete selected CSS regression input')
        self.profile.update(allowedPaths=[self.source_path], tree=self.git('rev-parse', 'HEAD^{tree}'),
                            target={**self.profile['target'], 'commit': self.git('rev-parse', 'HEAD')})
        self.selection = self.save_profile(self.profile)
        return selected

    def assert_complete_css_preview(self, raw):
        selected = self.select_large_css(raw)
        before = snapshot(self.root)
        with self.guarded():
            preview = self.preview()
            actual = json.loads(preview['modelRequest']['input'])['files'][self.source_path].encode()
            self.assertEqual(actual, raw)
            self.assertEqual(sha(actual), sha(raw))
            self.assertGreater(len(preview['modelRequest']['input'].encode()), 100_000)
            self.assertFalse(preview['executionAuthorized'] or preview['liveModelAvailable'])
            self.assertIsNone(preview['budget']['maximumIntakeNanoUsd'])
            self.assertEqual(preview['budget']['tokenCountStatus'], 'unverified-preflight-required')
            self.assertEqual(preview['projectGrantRequirements']['tokenCountStatus'],
                             'unverified-preflight-required')
            self.assertEqual(preview['intent']['target']['commit'], self.git('rev-parse', 'HEAD'))
            self.assertEqual(snapshot(self.root), before)
            # Same-length dirty bytes must still fail, rather than using committed/truncated text.
            selected.write_bytes(b'!' + raw[1:])
            with self.assertRaisesRegex(Refused, 'differs from its committed source'):
                self.preview()
            selected.write_bytes(raw)
        self.assertEqual(snapshot(self.root), before)
        return preview

    def test_complete_large_css_no_key_preview_and_dirty_source_refusal(self):
        raw = (''.join('.rule%d { color: #123456; }\n' % i for i in range(11000))).encode()
        self.assertGreater(len(raw), 256_340)
        self.assert_complete_css_preview(raw)

    def test_hash_pinned_task_larger_than_old_metadata_reader_is_complete(self):
        body = self.task['body'] + '\nDetailed context: ' + 'read complete local task context ' * 70000
        self.task = {**self.task, 'body': body}
        self.task_file.write_bytes(encoded(self.task))
        self.task_selection = self.task_file, sha(self.task_file.read_bytes())
        self.assertGreater(self.task_file.stat().st_size, 2_000_000)
        before = self.budget.path.read_bytes()
        with self.guarded():
            preview = self.preview()
        self.assertEqual(json.loads(preview['modelRequest']['input'])['task']['body'], body)
        self.assertEqual(self.budget.path.read_bytes(), before)
        self.assertFalse(preview['executionAuthorized'])

    def test_exact_profile_scope_cannot_be_changed_or_extended(self):
        with self.guarded():
            for change in ({'scope': 'execution'}, {'version': True}, {'version': 2},
                           {'kind': 'root-profile'}, {'recipeManifestSha256': None}, {'runtimeSha256': 'a' * 64},
                           {'testScript': 'build'}, {'reviewer': 'invented'}, {'owner': 'invented'},
                           {'authorAuthenticated': True}, {'grant': {}}):
                with self.subTest(change=change), self.assertRaises(Refused):
                    self.preview(self.save_profile({**self.profile, **change}))
            for field in ('kind', 'scope'):
                changed = dict(self.profile)
                del changed[field]
                with self.subTest(missing=field), self.assertRaises(Refused):
                    self.preview(self.save_profile(changed))
            self.save_profile(self.profile)
            with self.assertRaisesRegex(Refused, 'proposal-only local task selection'):
                c.preview_root_admission(self.selection, (self.task_file, self.task_selection[1]))
            with self.assertRaisesRegex(Refused, 'separate intake-only'):
                c.preview_root_admission(self.selection, root_task=self.task_selection)
            with self.assertRaisesRegex(Refused, 'separate intake-only'):
                c.run_issue(root_profile=self.selection, root_task=self.task_selection)
        self.assertEqual(self.budget.read()['requests'], [])

    def test_project_and_input_drift_remain_bound_without_budget_mutation(self):
        before = self.budget.path.read_bytes()
        with self.guarded():
            base = self.preview()
            for field, value in (('tree', '0' * 40), ('allowedPaths', ['.env.local']),
                                 ('target', {**self.profile['target'], 'commit': '0' * 40}),
                                 ('git', {**self.profile['git'], 'sha256': '0' * 64})):
                with self.subTest(field=field), self.assertRaises(Refused):
                    self.preview(self.save_profile({**self.profile, field: value}))
            self.save_profile(self.profile)
            source = self.checkout / self.source_path
            original = source.read_bytes()
            source.write_bytes(original + b'changed')
            with self.assertRaisesRegex(Refused, 'differs from its committed source'):
                self.preview()
            source.write_bytes(original)
            self.git('remote', 'set-url', 'origin', 'https://github.com/synthetic/other.git')
            with self.assertRaisesRegex(Refused, 'origin differs'):
                self.preview()
            self.git('remote', 'set-url', 'origin', self.origin)
            self.task_file.write_bytes(encoded({**self.task, 'title': 'Changed local fixture'}))
            with self.assertRaises(Refused):
                self.preview()
            self.task_file.write_bytes(encoded(self.task))
            self.assertEqual(self.preview(), base)
        self.assertEqual(self.budget.path.read_bytes(), before)
        self.assertFalse((self.worker / 'local-tasks').exists())

    def test_missing_budget_marker_never_initializes_or_resets_history(self):
        marker = self.worker / 'budget-established.json'
        saved = self.worker / 'saved-marker.json'
        marker.rename(saved)
        before = snapshot(self.root)
        with self.guarded(), self.assertRaisesRegex(Refused, 'established shared budget'):
            self.preview()
        self.assertEqual(snapshot(self.root), before)
        saved.rename(marker)

    def test_fixture_project_grant_binds_new_scope_without_dispatch_or_budget_change(self):
        # This grant is only fixture data, never a grant against the operator's real ledger.
        before = self.budget.path.read_bytes()
        with self.guarded():
            base = self.preview()
            requirements = base['projectGrantRequirements']
            names = ('operation', 'model', 'requestSha256', 'scopeSha256', 'target', 'taskSha256',
                     'sourceSha256', 'originalLedgerSha256', 'markerSha256', 'legacyBytesSha256')
            grant = {key: requirements[key] for key in names}
            grant.update(version=3, extraNanoUsd=0, extraGenerations=0, maxOperationNanoUsd=50_000_000,
                         maxLedgerNanoUsd=requirements['existingLimitNanoUsd'],
                         maxLedgerCalls=requirements['existingCallCeiling'])
            grant_file = self.root / 'fixture-project-grant.json'
            grant_file.write_bytes(encoded(grant))
            selection = grant_file, sha(grant_file.read_bytes())
            live = c.preview_root_admission(self.selection, root_task=self.task_selection, intake_only=True,
                                            live_grant=selection)
            self.assertEqual(live['intent']['intakeScope'], base['intent']['intakeScope'])
            self.assertEqual(live['modelRequest'], base['modelRequest'])
            self.assertNotEqual(live['intentSha256'], base['intentSha256'])
            self.assertEqual(live['budget']['binding']['projectGrantSha256'], fingerprint(grant))
            with self.assertRaisesRegex(Refused, 'Consent must match'):
                c.run_issue(root_profile=self.selection, root_task=self.task_selection, intake_only=True,
                            live_grant=selection, authorize_root_intent=base['intentSha256'])
            grant_file.write_bytes(encoded({**grant, 'scopeSha256': '0' * 64}))
            with self.assertRaisesRegex(Refused, 'does not bind'):
                c.preview_root_admission(self.selection, root_task=self.task_selection, intake_only=True,
                                         live_grant=(grant_file, sha(grant_file.read_bytes())))
        self.assertEqual(self.budget.path.read_bytes(), before)
        self.assertFalse((self.worker / 'local-tasks').exists())

    def reviewed_retained_fixture(self, *, output_tokens=None, input_tokens=100, response_status='completed'):
        """A live-shaped receipt, using a provider double and a temporary fixture ledger only."""
        selected = {} if output_tokens is None else {'output_tokens': output_tokens}
        with self.guarded():
            base = c.preview_root_admission(self.selection, root_task=self.task_selection, intake_only=True, **selected)
            requirements = base['projectGrantRequirements']
            names = ('operation', 'model', 'requestSha256', 'scopeSha256', 'target', 'taskSha256',
                     'sourceSha256', 'originalLedgerSha256', 'markerSha256', 'legacyBytesSha256')
            grant = {key: requirements[key] for key in names}
            grant.update(version=3, extraNanoUsd=0, extraGenerations=0, maxOperationNanoUsd=50_000_000,
                         maxLedgerNanoUsd=requirements['existingLimitNanoUsd'],
                         maxLedgerCalls=requirements['existingCallCeiling'])
            self.grant_file = self.root / 'fixture-grant.json'
            self.grant_file.write_bytes(encoded(grant))
            grant_selection = self.grant_file, sha(self.grant_file.read_bytes())
            preview = c.preview_root_admission(self.selection, root_task=self.task_selection, intake_only=True,
                                               live_grant=grant_selection, **selected)

            def provider(endpoint, payload):
                if endpoint == 'responses/input_tokens':
                    return {'input_tokens': input_tokens}
                self.assertEqual(endpoint, 'responses')
                self.assertEqual(payload['max_output_tokens'], output_tokens or 8192)
                proposal = {'triage': {'ready': True, 'reason': 'Local fixture', 'risk': 'low',
                    'paths': [self.source_path], 'acceptance': ['Show the reviewed greeting']},
                    'patch': {'summary': 'Local fixture', 'edits': [{'path': self.source_path,
                        'old': 'Hello reader', 'new': 'Hello reviewer'}]}}
                return {'id': 'fixture_retained_intake_response', 'model': MODEL, 'status': response_status,
                    'usage': {'input_tokens': input_tokens, 'output_tokens': 100},
                    'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(proposal)}]}]}

            with patch.object(c, 'transport_from_key_file', return_value=provider):
                state = c.run_issue(root_profile=self.selection, root_task=self.task_selection, intake_only=True,
                                    live_grant=grant_selection, authorize_root_intent=preview['intentSha256'], **selected)
        retained = self.worker / 'local-tasks' / state['key']
        paths = {'stateSha256': retained / 'state.json', 'proposalSha256': retained / 'intake.json',
                 'receiptSha256': retained / 'effects/intake.receipt.json',
                 'journalSha256': retained / 'effects/effects.json'}
        self.review = {'version': 1, 'grantVersion': 3, 'originControllerSha256': state['resumeContractSha256'],
            'candidateControllerSha256': c.resume_contract_sha(),
            **{key: sha(path.read_bytes()) for key, path in paths.items()}}
        self.review_file = self.root / 'independent-review.json'
        return self.candidate_inputs(), state, paths

    def candidate_inputs(self, review=None):
        self.review_file.write_bytes(encoded(self.review if review is None else review))
        return {'root_profile': self.selection, 'root_task': self.task_selection,
                'retained_intake': (self.review_file, sha(self.review_file.read_bytes()))}

    def test_installed_live_intake_cli_selects_exact_allowance_and_resumes_without_provider(self):
        key = self.root / 'fixture-private-key'
        key.write_text('Fixture sentinel, never read as a credential')
        key.chmod(0o600)
        driver = '''import contextlib, io, json, runpy, socket, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'lib'))
from aim_ci import controller as c
from openai_budget import MODEL
def forbidden(*args, **kwargs):
    raise AssertionError('Real credentials, network, execution or publication forbidden')
c.github = c.human_review_pause = c.publish_candidate = c.probe.validate = forbidden
socket.socket = forbidden
git_pin, run = sys.argv[2], subprocess.run
def git_only(argv, *args, **kwargs):
    assert argv[0] == git_pin, argv
    return run(argv, *args, **kwargs)
subprocess.run = git_only
def provider(endpoint, payload):
    assert '--resume' not in sys.argv, 'Provider replay forbidden'
    if endpoint == 'responses/input_tokens':
        return {'input_tokens': 11179}
    assert endpoint == 'responses' and payload['max_output_tokens'] == 20000
    proposal = {'triage': {'ready': True, 'reason': 'Fixture', 'risk': 'low',
        'paths': ['src/pages/index.astro'], 'acceptance': ['Show reviewed greeting']},
        'patch': {'summary': 'Fixture', 'edits': [{'path': 'src/pages/index.astro',
            'old': 'Hello reader', 'new': 'Hello reviewer'}]}}
    return {'id': 'fixture-installed-response', 'model': MODEL, 'status': 'completed',
        'usage': {'input_tokens': 11179, 'output_tokens': 100},
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(proposal)}]}]}
def credentials(*args, **kwargs):
    assert '--resume' not in sys.argv, 'Credential replay forbidden'
    return provider
c.transport_from_key_file = credentials
original = c.run_issue
def quiet_run(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return original(*args, **kwargs)
c.run_issue = quiet_run
entry = str(Path(sys.argv[1]) / 'aim-ci-control.py')
sys.argv = [entry, *sys.argv[3:]]
runpy.run_path(entry, run_name='__main__')
'''
        common = ['run-local-intake', '--manifest-sha256', self.package['manifestSha256'],
            '--state-dir', str(self.worker), '--legacy-budget', str(self.legacy),
            '--profile', str(self.profile_file), '--profile-sha256', self.selection[1],
            '--task-file', str(self.task_file), '--task-sha256', self.task_selection[1],
            '--key-file', str(key), '--output-tokens', '20000']

        def cli(*options, success=True):
            result = subprocess.run([sys.executable, '-IBS', '-c', driver, str(self.installed),
                str(self.git_tool), *common, *options], cwd=self.root,
                env={'PATH': str(self.git_tool.parent) + ':/usr/bin:/bin', 'HOME': str(self.root)},
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
            return json.loads(result.stdout) if success else result.stderr

        source_before = snapshot(self.checkout)
        before = snapshot(self.root)
        base = cli('--preview')
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(base['intent']['maxOutputTokens'], 20000)
        requirements = base['projectGrantRequirements']
        names = ('operation', 'model', 'requestSha256', 'scopeSha256', 'target', 'taskSha256',
                 'sourceSha256', 'originalLedgerSha256', 'markerSha256', 'legacyBytesSha256')
        grant = {name: requirements[name] for name in names}
        grant.update(version=3, extraNanoUsd=0, extraGenerations=0, maxOperationNanoUsd=50_000_000,
                     maxLedgerNanoUsd=requirements['existingLimitNanoUsd'],
                     maxLedgerCalls=requirements['existingCallCeiling'])
        grant_file = self.root / 'fixture-installed-grant.json'
        grant_file.write_bytes(encoded(grant))
        grant_args = ['--live-grant', str(grant_file), '--live-grant-sha256', sha(grant_file.read_bytes())]
        approved = cli('--preview', *grant_args)
        self.assertEqual(approved['modelRequest']['max_output_tokens'], 20000)
        self.assertIn('Consent must match', cli('--authorize-intent', base['intentSha256'], *grant_args, success=False))
        result = cli('--authorize-intent', approved['intentSha256'], *grant_args)
        self.assertEqual(result['stage'], 'intake_proposed')
        self.assertEqual(result['rootAdmission']['maxOutputTokens'], 20000)
        retained = snapshot(self.worker)
        self.assertEqual(cli('--authorize-intent', approved['intentSha256'], '--resume', *grant_args), result)
        self.assertEqual(snapshot(self.worker), retained)
        self.assertEqual(snapshot(self.checkout), source_before)
        entry = self.budget.read()['requests'][-1]
        self.assertEqual(entry['maxOutputTokens'], 20000)
        self.assertEqual(entry['actualNanoUsd'], 11179 * 250 + 100 * 2000)

    def test_output_allowance_binds_grant_consent_resume_and_candidate_without_replay(self):
        with self.guarded():
            default = self.preview()
            explicit_default = c.preview_root_admission(self.selection, root_task=self.task_selection,
                                                        intake_only=True, output_tokens=8192)
            selected = c.preview_root_admission(self.selection, root_task=self.task_selection,
                                                intake_only=True, output_tokens=20000)
            self.assertEqual(default, explicit_default)
            self.assertNotIn('maxOutputTokens', default['intent'])
            self.assertEqual(selected['intent']['maxOutputTokens'], 20000)
            self.assertNotEqual(selected['intentSha256'], default['intentSha256'])
            self.assertNotEqual(selected['projectGrantRequirements']['requestSha256'],
                                default['projectGrantRequirements']['requestSha256'])
            self.assertNotEqual(selected['projectGrantRequirements']['scopeSha256'],
                                default['projectGrantRequirements']['scopeSha256'])
        args, intake, paths = self.reviewed_retained_fixture(output_tokens=20000, input_tokens=11179)
        admission = intake['rootAdmission']
        inputs = dict(root_profile=self.selection, root_task=self.task_selection, intake_only=True,
                      live_grant=(self.grant_file, sha(self.grant_file.read_bytes())),
                      authorize_root_intent=admission['intentSha256'], output_tokens=20000)
        preserved = {path: path.read_bytes() for path in (*paths.values(), self.budget.path, self.legacy)}
        with self.guarded():
            self.assertEqual(c.run_issue(**inputs, resume=True), intake)
            for allowance in (8192, 16000, 25000):
                with self.subTest(allowance=allowance), self.assertRaises(Refused):
                    c.run_issue(**{**inputs, 'output_tokens': allowance}, resume=True)
            with self.assertRaisesRegex(Refused, 'Consent must match'):
                c.run_issue(**{**inputs, 'authorize_root_intent': default['intentSha256']})
            with self.assertRaisesRegex(Refused, 'already dispatched'):
                c.run_issue(**inputs)
        with self.candidate_guarded():
            candidate = c.preview_intake_candidate(**args)
            self.assertEqual(candidate['intent']['maxOutputTokens'], 20000)
            self.assertEqual(candidate['intent']['intakeIntentSha256'], admission['intentSha256'])
            # A genuinely different consumer contract still reconstructs the producer's exact request.
            with patch.object(c, 'resume_contract_sha', return_value='e' * 64):
                newer_args = self.candidate_inputs({**self.review, 'candidateControllerSha256': 'e' * 64})
                newer = c.preview_intake_candidate(**newer_args)
                self.assertEqual(newer['intent']['controllerSha256'], 'e' * 64)
                self.assertEqual(newer['intent']['originControllerSha256'], intake['resumeContractSha256'])
                self.assertEqual(newer['intent']['intakeIntentSha256'], admission['intentSha256'])
                self.assertEqual(newer['intent']['maxOutputTokens'], 20000)
            self.candidate_inputs()
            state_path = paths['stateSha256']
            state_bytes = state_path.read_bytes()
            changed = json.loads(state_bytes)
            changed['rootAdmission']['maxOutputTokens'] = 16000
            state_path.write_bytes(encoded(changed))
            with self.assertRaisesRegex(Refused, 'independently reviewed bytes'):
                c.preview_intake_candidate(**args)
            changed_args = self.candidate_inputs({**self.review, 'stateSha256': sha(state_path.read_bytes())})
            with self.assertRaisesRegex(Refused, 'Project grant differs from the exact request'):
                c.preview_intake_candidate(**changed_args)
            state_path.write_bytes(state_bytes)
            self.candidate_inputs()
        entry = self.budget.read()['requests'][-1]
        self.assertEqual(entry['maxOutputTokens'], 20000)
        self.assertEqual(entry['inputTokens'], 11179)
        self.assertEqual(entry['reservedNanoUsd'], 50_000_000)
        self.assertEqual(entry['actualNanoUsd'], 11179 * 250 + 100 * 2000)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)

    def test_selected_allowance_incomplete_response_never_replays(self):
        self.assert_stopped_output_selection(11179, 'incomplete', 'Model response incomplete')

    def test_selected_allowance_over_cap_preflight_never_replays(self):
        self.assert_stopped_output_selection(40000, 'completed', 'Actual token count exceeds')

    def assert_stopped_output_selection(self, input_tokens, response_status, reason):
        with self.assertRaisesRegex(Refused, reason):
            self.reviewed_retained_fixture(output_tokens=20000, input_tokens=input_tokens,
                                           response_status=response_status)
        entry = self.budget.read()['requests'][-1]
        self.assertEqual(entry['maxOutputTokens'], 20000)
        self.assertEqual(entry['reservedNanoUsd'], 50_000_000)
        retained = self.worker / 'local-tasks' / c.local_task_key(self.repository, self.task['id'])
        state = json.loads((retained / 'state.json').read_bytes())
        self.assertEqual(state['stage'], 'stopped')
        self.assertFalse((retained / 'intake.json').exists())
        self.assertFalse((retained / 'effects/intake.receipt.json').exists())
        if response_status == 'incomplete':
            self.assertEqual(entry['status'], 'completed')
            self.assertEqual(entry['responseStatus'], 'incomplete')
        else:
            self.assertEqual(entry['status'], 'unknown')
            self.assertEqual(entry['failureClass'], 'TokenPreflightRefused')
        # Resume may append a stopped audit event, but cannot change accounting or the model journal.
        observed = (self.budget.path, retained / 'effects/effects.json', retained / 'triage/budget.json')
        preserved = {path: path.read_bytes() for path in observed if path.exists()}
        inputs = dict(root_profile=self.selection, root_task=self.task_selection, intake_only=True,
            live_grant=(self.grant_file, sha(self.grant_file.read_bytes())), output_tokens=20000,
            authorize_root_intent=state['rootAdmission']['intentSha256'])
        with self.guarded():
            for resume in (False, True):
                with self.subTest(resume=resume), self.assertRaises(Refused):
                    c.run_issue(**inputs, resume=resume)
            with self.assertRaises(Refused):
                c.run_issue(**{**inputs, 'output_tokens': 16000}, resume=True)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
        self.assertEqual(json.loads((retained / 'state.json').read_bytes())['stage'], 'stopped')
        self.assertFalse((retained / 'intake.json').exists())
        self.assertFalse((retained / 'effects/intake.receipt.json').exists())

    def test_reviewed_proposal_prepares_only_private_unvalidated_candidate(self):
        args, intake, paths = self.reviewed_retained_fixture()
        (self.checkout / 'unselected-dirty.txt').write_text('Keep this local work\n')
        preserved = {path: path.read_bytes() for path in (*paths.values(), self.budget.path, self.legacy)}
        source_before = snapshot(self.checkout)
        with self.candidate_guarded():
            before = snapshot(self.root)
            preview = c.preview_intake_candidate(**args)
            self.assertEqual(snapshot(self.root), before)
            self.assertFalse(preview['executionAuthorized'])
            self.assertEqual(preview['intent']['candidateScope']['technicalValidation'], 'not-performed')
            for consent in (None, intake['rootAdmission']['intentSha256'], '0' * 64):
                with self.assertRaisesRegex(Refused, 'separate explicit consent|Consent must match'):
                    c.run_issue(**args, candidate_action='prepare', authorize_candidate_intent=consent)
            launch = {**args, 'candidate_action': 'prepare', 'authorize_candidate_intent': preview['intentSha256']}
            result = c.run_issue(**launch)
            self.assertEqual(result['stage'], 'candidate_prepared_unvalidated')
            self.assertFalse(result['productAccepted'] or result['publicationAuthorized'])
            self.assertEqual(result['validation'], [])
            self.assertEqual(result['candidateScope'], preview['intent']['candidateScope'])
            candidate = Path(result['recipeCandidate']['checkout'])
            self.assertEqual((candidate / self.source_path).read_text(), '<h1>Hello reviewer</h1>\n')
            self.assertFalse((candidate / 'unselected-dirty.txt').exists())
            source_objects = self.checkout / '.git/objects'
            for obj in source_objects.glob('*/*'):
                copied = candidate / '.git/objects' / obj.relative_to(source_objects)
                if copied.exists():
                    self.assertNotEqual((obj.stat().st_dev, obj.stat().st_ino),
                                        (copied.stat().st_dev, copied.stat().st_ino))
            self.assertEqual(c.run_issue(**launch, resume=True), result)
            for evidence in ({'recipe_evidence': ('unused',) * 4}, {'technical_decision': ('unused', '0' * 64)}):
                with self.assertRaisesRegex(Refused, 'Preparation-only candidate cannot'):
                    c.run_issue(**launch, resume=True, **evidence)
            self.assertEqual(c.run_issue(**{**launch, 'candidate_action': 'withdraw'})['status'], 'withdrawn')
            with self.assertRaisesRegex(Refused, 'was withdrawn'):
                c.run_issue(**launch, resume=True)
        self.assertEqual(snapshot(self.checkout), source_before)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)

    def test_reviewed_candidate_rejects_source_profile_task_grant_and_receipt_drift(self):
        args, _, paths = self.reviewed_retained_fixture()
        with self.candidate_guarded():
            before = self.budget.path.read_bytes()
            preview = c.preview_intake_candidate(**args)
            for name, path in (*paths.items(), ('grant', self.grant_file), ('task', self.task_file)):
                raw = path.read_bytes()
                path.write_bytes(raw + b' ')
                with self.subTest(name=name), self.assertRaises(Refused):
                    c.preview_intake_candidate(**args)
                path.write_bytes(raw)
            changed = {**self.profile, 'allowedPaths': ['package.json']}
            selection = self.save_profile(changed)
            with self.assertRaises(Refused):
                c.preview_intake_candidate(**{**args, 'root_profile': selection})
            self.save_profile(self.profile)
            selected = self.checkout / self.source_path
            original = selected.read_bytes()
            selected.write_bytes(original.replace(b'reader', b'writer'))
            with self.assertRaisesRegex(Refused, 'differs from its committed source'):
                c.preview_intake_candidate(**args)
            selected.write_bytes(original)
            # Even a fresh independent pin cannot turn altered patch bytes into the completed effect.
            proposal = paths['proposalSha256']
            original = proposal.read_bytes()
            proposal.write_bytes(original.replace(b'Hello reviewer', b'Hello attacker'))
            with self.assertRaisesRegex(Refused, 'patch or receipt differs'):
                c.preview_intake_candidate(**self.candidate_inputs({**self.review,
                    'proposalSha256': sha(proposal.read_bytes())}))
            proposal.write_bytes(original)
            self.candidate_inputs()
            self.assertEqual(c.preview_intake_candidate(**args), preview)
            self.assertEqual(self.budget.path.read_bytes(), before)
        self.assertFalse((self.worker / 'intake-candidates').exists())

    def test_reviewed_candidate_recovery_never_replays_unknown_preparation(self):
        class Interrupted(BaseException):
            pass
        for point in ('before-retained-candidate-receipt', 'after-retained-candidate-receipt'):
            with self.subTest(point=point):
                self.setUp()
                args, _, _ = self.reviewed_retained_fixture()
                with self.candidate_guarded():
                    preview = c.preview_intake_candidate(**args)
                    launch = {**args, 'candidate_action': 'prepare',
                              'authorize_candidate_intent': preview['intentSha256']}
                    def interrupt(name):
                        if name == point:
                            raise Interrupted()
                    with patch.object(c, 'boundary', side_effect=interrupt), self.assertRaises(Interrupted):
                        c.run_issue(**launch)
                    candidate = Path(preview['intent']['output'])
                    head = (candidate / '.git/refs/heads' / ('aim-ci-review-' + c.local_task_key(
                        self.repository, self.task['id']))).read_bytes()
                    with patch.object(c, 'root_candidate', wraps=c.root_candidate) as operation:
                        if point.startswith('before'):
                            with self.assertRaisesRegex(Refused, 'Effect outcome unknown'):
                                c.run_issue(**launch, resume=True)
                            operation.assert_not_called()
                        else:
                            result = c.run_issue(**launch, resume=True)
                            self.assertEqual(result['stage'], 'candidate_prepared_unvalidated')
                            self.assertTrue(all(not call.kwargs.get('prepare') for call in operation.call_args_list))
                    self.assertEqual((candidate / '.git/refs/heads' / ('aim-ci-review-' + c.local_task_key(
                        self.repository, self.task['id']))).read_bytes(), head)

    def test_installed_candidate_cli_preview_prepare_resume_and_withdraw_without_key(self):
        args, intake, paths = self.reviewed_retained_fixture(output_tokens=20000, input_tokens=11179)
        preserved = {path: path.read_bytes() for path in (*paths.values(), self.budget.path, self.legacy)}
        (self.checkout / 'unselected-dirty.txt').write_text('Keep local edits\n')
        source_before, installed_before = snapshot(self.checkout), snapshot(self.installed)
        driver = '''import runpy, socket, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'lib'))
from aim_ci import controller as c
def forbidden(*args, **kwargs):
    raise AssertionError('Credential, provider, project execution or publication forbidden')
c.transport_from_key_file = c.model_call = c.github = c.human_review_pause = c.publish_candidate = forbidden
c.probe.validate = forbidden
socket.socket = forbidden
git_pin, run = sys.argv[2], subprocess.run
def git_only(argv, *args, **kwargs):
    assert argv[0] == git_pin, argv
    return run(argv, *args, **kwargs)
subprocess.run = git_only
entry = str(Path(sys.argv[1]) / 'aim-ci-control.py')
sys.argv = [entry, *sys.argv[3:]]
runpy.run_path(entry, run_name='__main__')
'''
        common = ['prepare-local-candidate', '--manifest-sha256', self.package['manifestSha256'],
            '--state-dir', str(self.worker), '--legacy-budget', str(self.legacy),
            '--profile', str(self.profile_file), '--profile-sha256', self.selection[1],
            '--task-file', str(self.task_file), '--task-sha256', self.task_selection[1],
            '--retained-intake', str(args['retained_intake'][0]),
            '--retained-intake-sha256', args['retained_intake'][1]]

        def cli(*options, success=True):
            result = subprocess.run([sys.executable, '-IBS', '-c', driver, str(self.installed),
                str(self.git_tool), *common, *options], cwd=self.root,
                env={'PATH': str(self.git_tool.parent) + ':/usr/bin:/bin', 'HOME': str(self.root)},
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
            return json.loads(result.stdout) if success else result.stderr

        before = snapshot(self.root)
        preview = cli('--preview')
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(preview['executionAuthorized'])
        self.assertEqual(preview['intent']['controllerInstallation']['manifestSha256'], self.package['manifestSha256'])
        self.assertEqual(preview['intent']['candidateScope']['scope'], 'preparation-only')
        self.assertEqual(preview['intent']['maxOutputTokens'], 20000)
        self.assertEqual(intake['rootAdmission']['maxOutputTokens'], 20000)
        self.assertIn('Consent must match', cli('--authorize-intent', intake['rootAdmission']['intentSha256'], success=False))
        self.assertFalse(Path(preview['intent']['output']).exists())
        consent = preview['intentSha256']
        prepared = cli('--authorize-intent', consent)
        self.assertEqual(prepared['stage'], 'candidate_prepared_unvalidated')
        self.assertFalse(prepared['productAccepted'] or prepared['publicationAuthorized'])
        self.assertEqual(cli('--authorize-intent', consent, '--resume'), prepared)
        self.assertEqual(cli('--withdraw-intent', consent)['status'], 'withdrawn')
        self.assertIn('was withdrawn', cli('--authorize-intent', consent, '--resume', success=False))
        self.assertEqual(snapshot(self.checkout), source_before)
        self.assertEqual(snapshot(self.installed), installed_before)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)

    def test_synthetic_proposal_retains_narrow_scope_and_cannot_become_candidate(self):
        # Retained source-containing state/journals must not inherit metadata byte caps.
        self.task = {**self.task, 'body': self.task['body'] + '\n' + 'complete retained context ' * 85000}
        self.task_file.write_bytes(encoded(self.task))
        self.task_selection = self.task_file, sha(self.task_file.read_bytes())
        calls = []

        def transport(endpoint, payload):
            calls.append(endpoint)
            if endpoint == 'responses/input_tokens':
                return {'input_tokens': 100}
            self.assertEqual(endpoint, 'responses')
            proposal = {'triage': {'ready': True, 'reason': 'Synthetic bounded fixture', 'risk': 'low',
                'paths': [self.source_path], 'acceptance': ['Show the reviewed greeting']},
                'patch': {'summary': 'Synthetic fixture only', 'edits': [{'path': self.source_path,
                    'old': 'Hello reader', 'new': 'Hello reviewer'}]}}
            return {'id': 'synthetic_intake_profile_response', 'model': MODEL, 'status': 'completed',
                'usage': {'input_tokens': 100, 'output_tokens': 100},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(proposal)}]}]}

        local_origin = self.root / 'unused-local-origin.git'
        self.git('remote', 'set-url', 'origin', str(local_origin))
        runtime = SimpleNamespace(synthetic=True, workspace=self.root, root=self.checkout, state=self.worker,
            legacy=self.legacy, repository=self.repository, owner='fixture', origin=str(local_origin),
            destination='http://127.0.0.1:4173/', transport=transport, boundary=lambda *_: None)
        source_before = snapshot(self.checkout)
        with self.guarded(), c.offline_transports(runtime):
            preview = self.preview()
            args = dict(root_profile=self.selection, root_task=self.task_selection, intake_only=True,
                        authorize_root_intent=preview['intentSha256'])
            state = c.run_issue(**args)
            self.assertEqual(state['stage'], 'intake_proposed')
            self.assertGreater((self.worker / 'local-tasks' / state['key'] / 'state.json').stat().st_size, 2_000_000)
            self.assertFalse(state['intakeResult']['changeApproved'] or state['intakeResult']['changeApplied'])
            self.assertFalse(state['productAccepted'])
            self.assertEqual(state['rootAdmission']['intakeScope'], preview['intent']['intakeScope'])
            config = c.root_review_profile(self.selection, runtime, self.worker, local_task=True, intake_only=True)[0]
            self.assertEqual(state['profileSha256'], fingerprint(config))
            self.assertEqual(c.run_issue(**args, resume=True), state)
            self.assertEqual(calls, ['responses/input_tokens', 'responses'])
            for action in (lambda: c.preview_intake_candidate(self.selection, root_task=self.task_selection),
                           lambda: c.run_issue(root_profile=self.selection, root_task=self.task_selection,
                               candidate_action='prepare', authorize_candidate_intent=preview['intentSha256']),
                           lambda: c.root_candidate(self.worker / 'unused', state, config, self.profile, prepare=True)):
                with self.assertRaisesRegex(Refused, 'Proposal-only intake profile cannot'):
                    action()
            # Replacing the intake profile with a v2 profile cannot reuse the proposal/state or consent.
            upgraded = {key: value for key, value in self.profile.items() if key not in ('kind', 'scope')}
            upgraded.update(version=2, owner='fixture', reviewer='fixture-reviewer',
                            recipeManifestSha256='a' * 64, runtimeSha256='b' * 64, testScript='build')
            qualified_selection = self.save_profile(upgraded)
            for consent in (preview['intentSha256'], self.preview(qualified_selection)['intentSha256']):
                with self.assertRaisesRegex(Refused, 'Consent must match|already binds different inputs'):
                    c.run_issue(**{**args, 'root_profile': qualified_selection, 'authorize_root_intent': consent}, resume=True)
            self.save_profile(self.profile)
            self.assertEqual(c.run_issue(**args, resume=True), state)
        self.assertEqual(snapshot(self.checkout), source_before)
        self.assertEqual(len(self.budget.read()['requests']), 1)
        self.assertFalse((self.worker / 'intake-candidates').exists())

    def test_validation_and_ungranted_live_paths_refuse_before_external_action(self):
        # The existing validation entry may take its ordinary advisory worker lock before refusing.
        (self.worker / 'worker.lock').touch()
        before = snapshot(self.root)
        with self.guarded():
            preview = self.preview()
            with self.assertRaisesRegex(Refused, 'Live root project execution is blocked'):
                c.run_issue(root_profile=self.selection, root_task=self.task_selection, intake_only=True,
                            authorize_root_intent=preview['intentSha256'])
            with c.installed_root_context(self.package['manifestSha256'], self.worker, self.legacy), \
                    patch.object(c, 'installed_root_binding', return_value={
                        'path': str(self.installed), 'manifestSha256': self.package['manifestSha256'],
                        'version': self.package['version']}):
                for action in (lambda: c.preview_candidate_validation(self.selection, root_task=self.task_selection,
                                   retained_intake=('unused', '0' * 64), candidate_validation=('unused', '0' * 64),
                                   recipe_evidence=None),
                               lambda: c.run_issue(root_profile=self.selection, root_task=self.task_selection,
                                   retained_intake=('unused', '0' * 64), candidate_validation=('unused', '0' * 64),
                                   authorize_validation_intent=preview['intentSha256'])):
                    with self.assertRaisesRegex(Refused, 'Proposal-only intake profile cannot'):
                        action()
        self.assertEqual(snapshot(self.root), before)


if __name__ == '__main__':
    unittest.main()
