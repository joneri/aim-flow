"""Offline checkout and release-binding tests; no network/project deployment."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_release_continuation as release
from aim_ci.owned_paths import Refused


def pin(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.git = Path(shutil.which('git')).resolve()
        self.git_pin = pin(self.git)
        self.source = self.base / 'source'; self.source.mkdir()
        self.run_git(self.source, 'init', '-q', '-b', 'main')
        (self.source / 'file.txt').write_text('reviewed\n')
        (self.source / '.gitattributes').write_text('file.txt filter=evil\n')
        self.run_git(self.source, 'add', '.')
        self.run_git(self.source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        self.sha = self.run_git(self.source, 'rev-parse', 'HEAD').decode().strip()
        self.tree = self.run_git(self.source, 'rev-parse', 'HEAD^{tree}').decode().strip()
        (self.base / 'chain').mkdir()
        self.handoff_path = self.base / 'chain' / 'handoff.json'; self.handoff_path.write_text('{}')
        self.handoff_pin = pin(self.handoff_path)
        self.handoff = {'mergedSha': self.sha, 'source': {'treeSha': self.tree},
            'repository': {'name': 'test/fixture', 'id': 1}, 'runId': 41, 'runAttempt': 2}
        self.target = self.base / 'target'; self.state = self.base / 'state'
        self.boundary = patch.object(release, 'verify_handoff', return_value=self.handoff)
        self.boundary.start(); self.addCleanup(self.boundary.stop)
        # Only version observation is faked. Actual local Git plumbing and file
        # verification exercise this unit's behavior, not Git runtime qualification.
        original = release.CommitReader.read
        def read(reader, *args, **kwargs):
            if args == ('--version',): return b'git version 2.54.0\n'
            return original(reader, *args, **kwargs)
        self.version = patch.object(release.CommitReader, 'read', read)
        self.version.start(); self.addCleanup(self.version.stop)
        original_run = release.subprocess.run
        def version_run(argv, *args, **kwargs):
            if argv[-1] == '--version':
                return SimpleNamespace(returncode=0, stdout=b'git version 2.54.0\n', stderr=b'')
            return original_run(argv, *args, **kwargs)
        self.local_version = patch.object(release.subprocess, 'run', side_effect=version_run)
        self.local_version.start(); self.addCleanup(self.local_version.stop)


    def run_git(self, cwd, *args):
        return subprocess.check_output([str(self.git), '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd,
            env={'PATH': os.defpath, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}, stderr=subprocess.PIPE)

    def transport(self, cwd, *args):
        if args[0] == 'fetch':
            # Fake external boundary: exact SHA is read from a synthetic local repo.
            args = ('fetch', '--no-tags', '--no-recurse-submodules', str(self.source), args[-1])
        return self.run_git(cwd, *args)

    def preview(self):
        return release.prepare_checkout(self.handoff_pin, self.git_pin, self.target, self.state)

    def execute(self):
        return release.prepare_checkout(self.handoff_pin, self.git_pin, self.target, self.state,
            self.preview()['intentSha256'], transport=self.transport)

    def test_preview_has_no_checkout_or_transport(self):
        with patch.object(release, 'Git', side_effect=AssertionError('network')):
            self.assertEqual(self.preview()['status'], 'awaiting-checkout-authorization')
        self.assertFalse(self.target.exists()); self.assertFalse(self.state.exists())

    def test_fresh_exact_main_materialized_without_filters(self):
        result = self.execute()
        self.assertEqual(result['status'], 'fresh-main-prepared')
        self.assertEqual((self.target / 'file.txt').read_text(), 'reviewed\n')
        self.assertEqual(self.run_git(self.target, 'branch', '--show-current').strip(), b'main')
        self.assertEqual(self.run_git(self.target, 'rev-parse', 'HEAD').decode().strip(), self.sha)
        self.assertFalse(result['releaseExecuted'])

    def test_replay_refused(self):
        self.execute()
        with self.assertRaises(Refused): self.execute()

    def test_wrong_grant_no_effect(self):
        with self.assertRaises(Refused):
            release.prepare_checkout(self.handoff_pin, self.git_pin, self.target, self.state, 'wrong', transport=self.transport)
        self.assertFalse(self.target.exists())

    def test_wrong_tree_never_materializes_source(self):
        self.handoff['source']['treeSha'] = 'a' * 40
        with self.assertRaises(Refused): self.execute()
        self.assertFalse((self.target / 'file.txt').exists())
        self.assertTrue((self.state / 'started.json').exists())

    def test_fetch_unknown_is_reserved_and_never_replayed(self):
        preview = self.preview()
        def fail(cwd, *args):
            if args[0] == 'fetch': raise TimeoutError('fake timeout')
            return self.transport(cwd, *args)
        with self.assertRaises(TimeoutError):
            release.prepare_checkout(self.handoff_pin, self.git_pin, self.target, self.state,
                preview['intentSha256'], transport=fail)
        self.assertTrue((self.state / 'started.json').exists())
        with self.assertRaises(Refused): self.execute()

    def test_symlink_source_refused(self):
        (self.source / 'link').symlink_to('file.txt')
        self.run_git(self.source, 'add', 'link')
        self.run_git(self.source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'link')
        self.handoff['mergedSha'] = self.run_git(self.source, 'rev-parse', 'HEAD').decode().strip()
        self.handoff['source']['treeSha'] = self.run_git(self.source, 'rev-parse', 'HEAD^{tree}').decode().strip()
        with self.assertRaises(Refused): self.execute()
        self.assertFalse((self.target / 'link').exists())

    def test_overlapping_paths_refused(self):
        with self.assertRaises(Refused):
            release.prepare_checkout(self.handoff_pin, self.git_pin, self.state / 'target', self.state)

    def release_fixture(self):
        prepared = self.execute()
        receipt = pin(self.state / 'result.json')
        contract_path = self.base / 'contract.json'
        self.contract = {'format': 'aim-flow-project-release-contract', 'schemaVersion': 1,
            'command': {'tool': 'fixture', 'arguments': ['release']},
            'scope': {'destinations': ['fixture'], 'selections': {}}, 'handoff': self.handoff_pin,
            'selectionPolicy': 'project-refresh-same-commit',
            'preEffectChecks': ['commit', 'runId', 'runAttempt', 'artifactIdentity', 'artifactBytes'],
            'sourceCitations': [{'path': 'file.txt', 'sha256': pin(self.target / 'file.txt')['sha256']}],
            'protectionObligations': [], 'unresolvedProtectionObligations': [], 'protectionEvidence': [],
            'receiptPaths': {'commit': ['commit'], 'runId': ['run'], 'runAttempt': ['attempt']}}
        contract_path.write_text(json.dumps(self.contract))
        contract_pin = pin(contract_path)
        self.declaration = {'target': prepared['target'], 'command': self.contract['command'],
            'scope': self.contract['scope'], 'inputs': [self.handoff_pin, receipt, contract_pin],
            'receipt': {'assertions': [{'path': ['commit'], 'equals': self.sha}]}}
        reader = release.CommitReader(release.ROOT, self.target, self.git, self.git_pin['sha256'], self.sha)
        self.options = SimpleNamespace(state=self.base / 'command-state', declaration_sha256='fixture')
        self.options.state.mkdir()
        self.contract_path = contract_path
        self.checkout_receipt = receipt
        self.reader = reader

    def invoke_release(self):
        self.contract_path.write_text(json.dumps(self.contract))
        self.declaration['inputs'][-1] = pin(self.contract_path)
        with patch.object(release.command, 'prepare', return_value=({'declaration': self.declaration}, self.reader)), \
             patch.object(release.command, 'command', return_value={'status': 'command-preview'}) as dispatch:
            result = release.release(release.ROOT, self.options, self.handoff_pin,
                self.checkout_receipt, pin(self.contract_path))
            self.assertEqual(dispatch.call_count, 1)
            return result

    def test_refresh_contract_does_not_require_old_attempt(self):
        self.release_fixture()
        result = self.invoke_release()
        self.assertEqual(result['selectionPolicy'], 'project-refresh-same-commit')
        self.assertFalse(result['productionFeatureAccepted'])

    def test_exact_contract_requires_handoff_run_assertions(self):
        self.release_fixture(); self.contract['selectionPolicy'] = 'handoff-exact'
        with self.assertRaises(Refused): self.invoke_release()
        self.declaration['receipt']['assertions'] += [
            {'path': ['run'], 'equals': 41}, {'path': ['attempt'], 'equals': 2}]
        self.invoke_release()

    def test_missing_backup_obligation_blocks_dispatch(self):
        self.release_fixture()
        self.contract['protectionObligations'] = ['Retain known-good source and backup']
        self.contract['unresolvedProtectionObligations'] = ['Retain known-good source and backup']
        with self.assertRaises(Refused): self.invoke_release()
        self.contract['unresolvedProtectionObligations'] = []
        with self.assertRaises(Refused): self.invoke_release()

    def test_changed_source_citation_blocks_dispatch(self):
        self.release_fixture(); self.contract['sourceCitations'][0]['sha256'] = 'f' * 64
        with self.assertRaises(Refused): self.invoke_release()

    def test_missing_pre_effect_guard_blocks_dispatch(self):
        self.release_fixture(); self.contract['preEffectChecks'].remove('artifactBytes')
        with self.assertRaises(Refused): self.invoke_release()

    def test_actual_project_command_child_dispatch_offline(self):
        (self.source / '.gitignore').write_text('.receipts/\n')
        (self.source / 'ship.py').write_text(
            "import json,pathlib,sys\n"
            "p=pathlib.Path('.receipts'); p.mkdir()\n"
            "(p/'new.json').write_text(json.dumps({'commit':sys.argv[1],'run':42,'attempt':3,'status':'verified'}))\n")
        self.run_git(self.source, 'add', '.')
        self.run_git(self.source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'local receipt only')
        self.sha = self.run_git(self.source, 'rev-parse', 'HEAD').decode().strip()
        self.handoff['mergedSha'] = self.sha
        self.handoff['source']['treeSha'] = self.run_git(self.source, 'rev-parse', 'HEAD^{tree}').decode().strip()
        self.release_fixture()
        archive = self.base / 'control.tar.gz'
        metadata = release.control_package.build(release.ROOT, archive, '0.1.0-release-test.1')
        installed = self.base / 'installed'
        release.control_package.install(archive, metadata['archiveSha256'], installed)
        python = Path(sys.executable).resolve()
        self.contract['command'] = {'tool': 'python', 'arguments': ['-IBS', 'ship.py', self.sha]}
        self.contract['sourceCitations'].append({'path': 'ship.py', 'sha256': pin(self.target / 'ship.py')['sha256']})
        self.contract_path.write_text(json.dumps(self.contract))
        self.declaration.update(format='aim-flow-project-command-declaration', schemaVersion=1,
            git=self.git_pin, tools={'python': pin(python)}, trees=[],
            command=self.contract['command'], environment={'LC_ALL': 'C'}, environmentFile=None,
            evidence=[{**self.handoff_pin, 'assertions': [{'path': ['fixture'], 'equals': True}]}],
            review={'reference': 'Synthetic offline command review', 'effects': ['Creates one local receipt'],
                'qualifications': ['No deployment or network'], 'prerequisites': ['Fake upstream handoff boundary']},
            timeoutSeconds=10, outputBytes=10000)
        evidence_path = self.base / 'fixture-evidence.json'; evidence_path.write_text('{"fixture":true}')
        self.declaration['evidence'] = [{**pin(evidence_path), 'assertions': [{'path': ['fixture'], 'equals': True}]}]
        self.declaration['inputs'][-1] = pin(self.contract_path)
        self.declaration['receipt'].update(directory='.receipts', filenamePattern='*.json', meaning='Synthetic operational completion')
        self.declaration['receipt']['assertions'].append({'path': ['status'], 'equals': 'verified'})
        declaration_path = self.base / 'command.json'; declaration_path.write_text(json.dumps(self.declaration))
        options = SimpleNamespace(action='run-project-command', manifest_sha256=metadata['manifestSha256'],
            declaration=declaration_path, declaration_sha256=pin(declaration_path)['sha256'],
            state=self.base / 'real-command-state', preview=True, authorize_intent=None)
        preview = release.release(installed, options, self.handoff_pin, self.checkout_receipt, pin(self.contract_path))
        options.preview = False; options.authorize_intent = preview['projectCommand']['intentSha256']
        result = release.release(installed, options, self.handoff_pin, self.checkout_receipt, pin(self.contract_path))
        self.assertEqual(result['status'], 'project-receipt-matched')
        self.assertEqual(result['actualReleaseIdentity'], {'commit': self.sha, 'runId': 42, 'runAttempt': 3})
        self.assertEqual(self.handoff['runId'], 41)  # Snapshot is not the uploaded-run claim.
        self.assertTrue((options.state / 'started.json').exists())
        with self.assertRaises(Refused):
            release.release(installed, options, self.handoff_pin, self.checkout_receipt, pin(self.contract_path))

    def test_scoped_gh_auth_is_explicit_and_fixed(self):
        auth = {'gh': pin(Path(sys.executable).resolve()), 'home': str(self.base),
            'configDirectory': str(self.base / 'auth'), 'timeoutSeconds': 20}
        (self.base / 'auth').mkdir()
        preview = release.prepare_checkout(self.handoff_pin, self.git_pin, self.target, self.state,
            authentication=auth)
        self.assertEqual(preview['intent']['authentication'], auth)
        with patch.object(release.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'')) as transport:
            release.Git(self.git_pin, auth)(self.base, 'fetch', 'origin', self.sha)
        argv = transport.call_args.args[0]
        env = transport.call_args.kwargs['env']
        self.assertIn('credential.helper=', argv)
        self.assertIn('credential.helper=!' + release.shlex.quote(auth['gh']['path']) + ' auth git-credential', argv)
        self.assertIn('http.followRedirects=false', argv)
        self.assertEqual(env['GH_CONFIG_DIR'], auth['configDirectory'])
        self.assertNotIn('GH_TOKEN', env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)

    def test_old_git_refused_before_state_authentication_or_transport(self):
        with patch.object(release.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=b'git version 2.53.0\n', stderr=b'')) as version, \
             patch.object(release, 'Git', side_effect=AssertionError('No transport permitted')):
            with self.assertRaisesRegex(Refused, 'Git 2.54.0'):
                release.prepare_checkout(self.handoff_pin, self.git_pin, self.target, self.state,
                    'irrelevant-authorization', transport=lambda *a: self.fail('No fetch permitted'))
        self.assertEqual(version.call_count, 1)
        self.assertEqual(version.call_args.args[0][-1], '--version')
        self.assertEqual(version.call_args.kwargs['env']['GIT_ALLOW_PROTOCOL'], '')
        self.assertNotIn('HOME', version.call_args.kwargs['env'])
        self.assertNotIn('GH_CONFIG_DIR', version.call_args.kwargs['env'])
        self.assertFalse(self.state.exists()); self.assertFalse(self.target.exists())


if __name__ == '__main__': unittest.main()
