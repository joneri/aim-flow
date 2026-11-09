"""Project-aware installation keeps project learning outside both immutable inputs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import control_package, project_installation
from aim_ci.owned_paths import Refused


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {str(path.relative_to(root)): sha(path) for path in root.rglob('*') if path.is_file()}


class ProjectInstallationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory(prefix='aim-project-install-test-')
        cls.addClassCleanup(folder.cleanup)
        cls.scratch = Path(folder.name).resolve()
        cls.git = Path(shutil.which('git')).resolve()
        version = subprocess.check_output([str(cls.git), '--version'], text=True)
        match = re.match(r'git version (\d+)\.(\d+)\.(\d+)', version)
        if not match or tuple(map(int, match.groups())) < (2, 54, 0):
            raise RuntimeError('ProjectInstallationTests requires Git 2.54.0 or newer')
        cls.git_pin = sha(cls.git)
        cls.archive = cls.scratch / 'control.tar.gz'
        cls.release = control_package.build(ROOT, cls.archive, '0.10.0-project-install.1')
        cls.seed = cls.scratch / 'seed-controller'
        control_package.install(cls.archive, cls.release['archiveSha256'], cls.seed)

    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=self.scratch)
        self.addCleanup(folder.cleanup)
        self.case = Path(folder.name).resolve()
        self.checkout = self.case / 'project'
        self.checkout.mkdir()
        self.marker = self.case / 'project-code-was-executed'
        self.addCleanup(lambda: self.assertFalse(self.marker.exists()))
        self.env = {'PATH': os.defpath, 'HOME': str(self.case), 'LC_ALL': 'C',
                    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': '',
                    'GIT_AUTHOR_NAME': 'Installation fixture', 'GIT_AUTHOR_EMAIL': 'fixture@invalid',
                    'GIT_COMMITTER_NAME': 'Installation fixture', 'GIT_COMMITTER_EMAIL': 'fixture@invalid'}
        self.git_run('init', '-q', '--initial-branch=main', '--template=')
        self.destination, self.output = self.case / 'installed', self.case / 'project-plan.json'

    def git_run(self, *args):
        return subprocess.check_output([str(self.git), '-c', 'core.hooksPath=/dev/null',
                                        '-c', 'core.fsmonitor=false', '-c', 'commit.gpgSign=false', *args],
                                       cwd=self.checkout, env=self.env, text=True).strip()

    def commit(self, *, node=False):
        workflow = self.checkout / '.github/workflows/build.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: Project build\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n'
                            '    steps:\n      - uses: actions/checkout@v4\n      - run: |\n'
                            '          echo custom build\n          touch ' + str(self.marker) + '\n')
        (self.checkout / 'README.md').write_text('Run `npm run test:pages` after reviewing the CI job.\n')
        if node:
            body = 'touch ' + str(self.marker)
            (self.checkout / 'package.json').write_text(json.dumps({'scripts': {
                'prebuild': body, 'build': body, 'postbuild': body,
                'pretest:pages': body, 'test:pages': body, 'posttest:pages': body}}))
        else:
            (self.checkout / 'pyproject.toml').write_text('[build-system]\nrequires = ["custom-builder"]\n'
                                                        'build-backend = "custom_build"\n')
        self.git_run('add', '.')
        self.git_run('commit', '-qm', 'Committed project fixture')
        return self.git_run('rev-parse', 'HEAD')

    def args(self, commit, **changes):
        return SimpleNamespace(archive=self.archive, sha256=self.release['archiveSha256'],
                               destination=self.destination, project_plan_output=self.output,
                               checkout=self.checkout, git=self.git, git_sha256=self.git_pin,
                               commit=commit, project_doc=['README.md'], test_script=None, **changes)

    def source_fixture(self, *, hook=False):
        self.commit()
        self.git_run('remote', 'add', 'origin', 'https://fixture.invalid/aim-ci/source-project.git')
        (self.checkout / 'scripts').mkdir()
        (self.checkout / 'scripts/check.mjs').write_text('throw new Error("must not execute while drafting");\n')
        scripts = {'check:source': 'node scripts/check.mjs'}
        if hook:
            scripts['precheck:source'] = 'touch ' + str(self.marker)
        package = {'name': 'generic-source-fixture', 'packageManager': 'npm@11.0.0',
                   'scripts': scripts, 'dependencies': {'arbitrary-framework': '1.0.0'}}
        (self.checkout / 'package.json').write_text(json.dumps(package))
        (self.checkout / 'package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': {
            '': {'dependencies': package['dependencies']},
            'node_modules/arbitrary-framework': {'version': '1.0.0'}}}))
        self.git_run('add', '.')
        self.git_run('commit', '-qm', 'Generic source-check fixture')
        paths = ['package.json', 'scripts/check.mjs', 'package-lock.json']
        flags = ['--execution-kind', 'npm-source-check', '--test-script', 'check:source']
        for name in paths:
            flags.extend(('--source-path', name))
        return self.git_run('rev-parse', 'HEAD'), paths, flags

    def inspect_cli(self, manager, action, commit, *flags, expected=0):
        argv = [str(Path(sys.executable).resolve()), '-IBS', str(manager), action,
                '--manifest-sha256', self.release['manifestSha256'],
                '--checkout', str(self.checkout), '--commit', commit,
                '--git', str(self.git), '--git-sha256', self.git_pin, '--project-doc', 'README.md', *flags]
        result = subprocess.run(argv, cwd=self.case, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected:
            self.assertFalse(result.stdout)
            return result.stderr
        self.assertFalse(result.stderr)
        return json.loads(result.stdout)

    def local_intake_cli(self, *mode, expected=0, manifest_pin=None, with_grant=True):
        # Import the real installed manager/controller in an isolated Python,
        # replacing only the dispatch endpoints. No profile/task/grant/key or
        # budget file exists, so a premature input/key read cannot pass.
        harness = '''
import importlib.util, json, sys
from pathlib import Path
installation = Path(sys.argv[1])
sys.path.insert(0, str(installation / 'lib'))
from aim_ci import controller
from aim_ci.owned_paths import Refused
def capture(kind, **kwargs):
    return json.loads(json.dumps({'called': kind, 'inputs': kwargs,
                                 'context': controller.ROOT_INSTALLATION.get()}, default=str))
controller.preview_root_admission = lambda **kwargs: capture('preview', **kwargs)
controller.run_issue = lambda **kwargs: capture('run', **kwargs)
controller.transport_from_key_file = lambda *a, **k: (_ for _ in ()).throw(AssertionError('No credential read'))
spec = importlib.util.spec_from_file_location('installed_manager', installation / 'aim-ci-control.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)
sys.argv = [str(installation / 'aim-ci-control.py'), *json.loads(sys.argv[2])]
try:
    code = manager.main()
except Refused as error:
    print(error, file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(code)
'''
        flags = ['run-local-intake', '--manifest-sha256', manifest_pin or self.release['manifestSha256'],
                 '--state-dir', str(self.case / 'existing-state'),
                 '--legacy-budget', str(self.case / 'original-ledger.json')]
        for name in ('profile', 'task', *(['live-grant'] if with_grant else [])):
            file_flag = 'task-file' if name == 'task' else name
            flags.extend(('--' + file_flag, str(self.case / (name + '.json')),
                          '--' + name + '-sha256', 'a' * 64))
        flags.extend(('--key-file', str(self.case / 'private-api-key'), *mode))
        result = subprocess.run([str(Path(sys.executable).resolve()), '-IBS', '-c', harness,
                                 str(self.seed), json.dumps(flags)], cwd=self.case, env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected:
            self.assertFalse(result.stdout)
            return result.stderr
        self.assertFalse(result.stderr)
        return json.loads(result.stdout)

    def cli(self, commit, *extra, expected=0, env=None):
        argv = [str(Path(sys.executable).resolve()), '-IBS', str(self.seed / 'aim-ci-control.py'),
                'install-project', '--archive', str(self.archive), '--sha256', self.release['archiveSha256'],
                '--destination', str(self.destination), '--project-plan-output', str(self.output),
                '--checkout', str(self.checkout), '--commit', commit,
                '--git', str(self.git), '--git-sha256', self.git_pin,
                '--project-doc', 'README.md', *extra]
        result = subprocess.run(argv, cwd=self.case, env=env or self.env, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stderr)
        if not expected:
            self.assertFalse(result.stderr)
            return json.loads(result.stdout)
        self.assertFalse(result.stdout)
        return result.stderr

    def test_installed_cli_retains_generic_ci_learning_without_running_or_changing_project(self):
        commit = self.commit()
        (self.checkout / 'README.md').write_text('Uncommitted text must stay untouched.\n')
        before, seed_before = snapshot(self.checkout), snapshot(self.seed)
        result = self.cli(commit)
        self.assertFalse(result['ready'])
        self.assertFalse(result['executionAuthorized'])
        self.assertEqual(result['mode'], 'installed-project-review-required')
        self.assertEqual(result['projectPlan']['sha256'], sha(self.output))
        receipt = json.loads(self.output.read_bytes())
        self.assertEqual(receipt['format'], 'aim-ci-project-installation')
        self.assertEqual(receipt['control'], {
            'installation': str(self.destination), 'version': self.release['version'],
            'archiveSha256': self.release['archiveSha256'], 'manifestSha256': self.release['manifestSha256']})
        self.assertEqual(receipt['target']['commit'], commit)
        self.assertEqual(receipt['target']['tree'], self.git_run('rev-parse', 'HEAD^{tree}'))
        plan = receipt['adaptation']['projectPlan']
        self.assertFalse(plan['executionAuthorized'])
        self.assertEqual(plan['selectedSteps'], [])
        self.assertIn('touch ' + str(self.marker), plan['buildDefinitions'][0]['excerpt']['text'])
        self.assertIsNone(receipt['adaptation']['proposal']['candidate'])
        self.assertEqual(snapshot(self.checkout), before)
        self.assertEqual(snapshot(self.seed), seed_before)
        manifest = control_package.verify_installation(self.destination, self.release['manifestSha256'])
        self.assertEqual(snapshot(self.destination), {**manifest['files'],
                         control_package.MANIFEST: self.release['manifestSha256']})
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        original = self.output.read_bytes()
        self.assertIn('must both be new', self.cli(commit, expected=1))
        self.assertEqual(self.output.read_bytes(), original)

    def test_named_scripts_and_hooks_remain_pending_in_external_receipt(self):
        commit = self.commit(node=True)
        result = self.cli(commit, '--test-script', 'test:pages')
        receipt = json.loads(self.output.read_bytes())
        plan = receipt['adaptation']['projectPlan']
        self.assertEqual(receipt['selection']['testScript'], 'test:pages')
        self.assertEqual(plan['testSelection']['selected'], 'test:pages')
        self.assertEqual([row['purpose'] for row in plan['selectedSteps']], ['build', 'test'])
        self.assertEqual(plan['selectedSteps'][1]['ignoreScriptsWouldSkip'],
                         ['pretest:pages', 'posttest:pages'])
        self.assertTrue(all(row['status'] == 'declared-not-run' for row in plan['selectedSteps']))
        self.assertEqual(result['projectPlan']['target'], receipt['target'])

    def test_explicit_source_check_installs_without_vite_build_or_full_project_claim(self):
        commit, paths, flags = self.source_fixture()
        before = snapshot(self.checkout)
        result = self.cli(commit, *flags)
        receipt = json.loads(self.output.read_bytes())
        self.assertEqual(receipt['selection'], {'projectDocuments': ['README.md'],
                         'testScript': 'check:source', 'executionKind': 'npm-source-check',
                         'sourcePaths': sorted(paths)})
        proposal = receipt['adaptation']['proposal']
        self.assertEqual(proposal['candidate']['stack'], 'npm-source-check')
        self.assertEqual(proposal['candidate']['sourcePaths'], sorted(paths))
        self.assertEqual(proposal['candidate']['recipeSchemaVersion'], 3)
        self.assertFalse(proposal['candidate']['behaviorVerified'])
        self.assertEqual(proposal['candidate']['dependencies'], {'mode': 'none'})
        self.assertEqual(proposal['candidate']['artifact'], {'kind': 'none'})
        self.assertEqual([row['name'] for row in proposal['plannedCommands']], ['test'])
        self.assertEqual(proposal['scope']['fullProjectQualification'], 'not-established')
        self.assertTrue(proposal['backgroundObservations'])
        self.assertEqual(set(proposal['sourceSelection']['sourceFiles']), set(paths))
        self.assertEqual(snapshot(self.checkout), before)
        self.assertEqual(result['projectPlan']['sha256'], sha(self.output))
        default = self.inspect_cli(self.seed / 'aim-ci-control.py', 'propose-adaptation', commit,
                                   '--test-script', 'check:source')
        self.assertIsNone(default['proposal']['candidate'])

    def test_source_check_hooks_or_absent_source_cannot_produce_candidate(self):
        commit, paths, flags = self.source_fixture(hook=True)
        proposal = self.inspect_cli(self.seed / 'aim-ci-control.py', 'propose-adaptation', commit, *flags)['proposal']
        self.assertIsNone(proposal['candidate'])
        self.assertIn('lifecycle hooks', ' '.join(proposal['reasons']))
        self.assertEqual([row['name'] for row in proposal['declaredLifecycle']],
                         ['precheck:source', 'check:source'])
        proposal = self.inspect_cli(self.seed / 'aim-ci-control.py', 'propose-adaptation', commit,
                                    *flags, '--source-path', 'missing.mjs')['proposal']
        self.assertIsNone(proposal['candidate'])
        self.assertIn('absent', ' '.join(proposal['reasons']))

    def test_source_check_cli_needs_explicit_test_and_exact_selection(self):
        commit, _, _ = self.source_fixture()
        for flags in (('--execution-kind', 'npm-source-check', '--source-path', 'package.json'),
                      ('--execution-kind', 'npm-source-check', '--test-script', 'check:source'),
                      ('--source-path', 'package.json')):
            with self.subTest(flags=flags):
                error = self.inspect_cli(self.seed / 'aim-ci-control.py', 'propose-adaptation', commit,
                                         *flags, expected=2)
                self.assertIn('requires', error)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.output.exists())

    def test_frozen_install_receipt_is_consumed_and_changed_selection_or_bytes_refuse(self):
        commit, paths, flags = self.source_fixture()
        installed = self.cli(commit, *flags)
        manager = self.destination / 'aim-ci-control.py'
        receipt_flags = ['--project-plan', str(self.output), '--project-plan-sha256', installed['projectPlan']['sha256']]
        report = self.inspect_cli(manager, 'draft-build-preparation', commit, *flags, *receipt_flags)
        provenance = report['projectInstallation']
        self.assertEqual(provenance['sha256'], installed['projectPlan']['sha256'])
        self.assertEqual(provenance['selection']['sourcePaths'], sorted(paths))
        self.assertEqual(provenance['status'], 'verified-review-draft-only')
        self.assertFalse(provenance['executionAuthorized'])
        self.assertIsNotNone(report['configurationDraft'])
        error = self.inspect_cli(manager, 'draft-build-preparation', commit, *flags,
                                 '--source-path', 'README.md', *receipt_flags, expected=1)
        self.assertIn('explicit selection differs', error)
        original = self.output.read_bytes()
        self.output.write_bytes(original + b'\n')
        error = self.inspect_cli(manager, 'draft-build-preparation', commit, *flags, *receipt_flags, expected=1)
        self.assertIn('Project plan receipt SHA-256 mismatch', error)
        self.output.write_bytes(original)
        error = self.inspect_cli(manager, 'draft-build-preparation', commit, *flags,
                                 '--project-plan', str(self.output), expected=2)
        self.assertIn('must be supplied together', error)
        changed = json.loads(original)
        changed['adaptation']['projectPlan']['stepOrder'] = 'changed frozen plan'
        self.output.write_text(json.dumps(changed))
        error = self.inspect_cli(manager, 'draft-build-preparation', commit, *flags,
                                 '--project-plan', str(self.output), '--project-plan-sha256', sha(self.output), expected=1)
        self.assertIn('Frozen project adaptation differs', error)
        self.output.write_bytes(original)
        error = self.inspect_cli(self.seed / 'aim-ci-control.py', 'draft-build-preparation', commit,
                                 *flags, *receipt_flags, expected=1)
        self.assertIn('current controller installation', error)

    def test_installed_local_intake_preview_dispatches_pins_without_key_or_budget_reads(self):
        before = snapshot(self.seed)
        report = self.local_intake_cli('--preview')
        self.assertEqual(report['called'], 'preview')
        self.assertEqual(report['context'], [self.release['manifestSha256'], str(self.case / 'existing-state'),
                                             str(self.case / 'original-ledger.json')])
        inputs = report['inputs']
        self.assertEqual(inputs['root_profile'], [str(self.case / 'profile.json'), 'a' * 64])
        self.assertEqual(inputs['root_task'], [str(self.case / 'task.json'), 'a' * 64])
        self.assertEqual(inputs['live_grant'], [str(self.case / 'live-grant.json'), 'a' * 64])
        self.assertEqual(inputs['live_key_file'], str(self.case / 'private-api-key'))
        self.assertIs(inputs['intake_only'], True)
        self.assertNotIn('authorize_root_intent', inputs)
        self.assertEqual(snapshot(self.seed), before)
        self.assertFalse((self.case / 'private-api-key').exists())
        self.assertFalse((self.case / 'existing-state').exists())
        pending = self.local_intake_cli('--preview', with_grant=False)
        self.assertEqual(pending['called'], 'preview')
        self.assertIsNone(pending['inputs']['live_grant'])
        self.assertEqual(pending['inputs']['live_key_file'], inputs['live_key_file'])

    def test_installed_local_intake_authorize_and_resume_pass_exact_consent_without_provider(self):
        for mode in ((), ('--resume',)):
            with self.subTest(mode=mode):
                report = self.local_intake_cli('--authorize-intent', 'b' * 64, *mode)
                self.assertEqual(report['called'], 'run')
                self.assertEqual(report['inputs']['authorize_root_intent'], 'b' * 64)
                self.assertEqual(report['inputs']['resume'], bool(mode))
                self.assertIs(report['inputs']['intake_only'], True)
        self.assertFalse((self.case / 'private-api-key').exists())

    def test_installed_local_intake_modes_and_manifest_guard_dispatch(self):
        for mode in ((), ('--preview', '--resume'), ('--preview', '--authorize-intent', 'b' * 64)):
            with self.subTest(mode=mode):
                self.local_intake_cli(*mode, expected=2)
        error = self.local_intake_cli('--preview', manifest_pin='0' * 64, expected=1)
        self.assertIn('Installed manifest SHA-256 mismatch', error)
        error = self.local_intake_cli('--authorize-intent', 'b' * 64, with_grant=False, expected=2)
        self.assertIn('requires --live-grant', error)
        error = self.local_intake_cli('--preview', '--live-grant', str(self.case / 'grant.json'),
                                      with_grant=False, expected=2)
        self.assertIn('must be supplied together', error)

    def test_bad_git_or_head_pins_leave_no_outputs(self):
        commit = self.commit()
        args = self.args(commit)
        for field, value, error in (('git_sha256', '0' * 64, 'Git executable SHA-256 mismatch'),
                                    ('commit', '0' * 40, 'Checkout HEAD differs')):
            original = getattr(args, field)
            setattr(args, field, value)
            with self.subTest(field=field), self.assertRaisesRegex(Refused, error):
                project_installation.install_project(ROOT, args)
            setattr(args, field, original)
            self.assertFalse(self.destination.exists())
            self.assertFalse(self.output.exists())

    def test_overlapping_existing_and_linked_outputs_refuse_before_installation(self):
        args = self.args(self.commit())
        scenarios = ((self.checkout / 'controller', self.output),
                     (ROOT / 'new-controller', self.output),
                     (self.destination, self.destination / 'plan.json'),
                     (self.destination, self.checkout / 'plan.json'),
                     (self.destination, ROOT / 'plan.json'))
        with patch.object(control_package, 'install', side_effect=AssertionError('No install expected')):
            for destination, output in scenarios:
                with self.subTest(destination=destination, output=output):
                    args.destination, args.project_plan_output = destination, output
                    with self.assertRaises(Refused):
                        project_installation.install_project(ROOT, args)
            args.destination, args.project_plan_output = self.destination, self.output
            self.output.write_text('Do not overwrite')
            with self.assertRaisesRegex(Refused, 'must both be new'):
                project_installation.install_project(ROOT, args)
            self.assertEqual(self.output.read_text(), 'Do not overwrite')
            self.output.unlink()
            alias = self.case / 'linked'
            alias.symlink_to(self.checkout, target_is_directory=True)
            args.project_plan_output = alias / 'plan.json'
            with self.assertRaisesRegex(Refused, 'Symlinked'):
                project_installation.install_project(ROOT, args)
        self.assertFalse(self.destination.exists())

    def test_failed_installed_inspection_retains_controller_without_completed_receipt(self):
        args = self.args(self.commit())
        with patch.object(project_installation, '_installed_adaptation', side_effect=Refused('synthetic refusal')):
            with self.assertRaisesRegex(Refused, 'Controller retained.*project installation is incomplete: synthetic refusal'):
                project_installation.install_project(ROOT, args)
        control_package.verify_installation(self.destination, self.release['manifestSha256'])
        self.assertFalse(self.output.exists())

    def test_mismatched_report_cannot_receive_receipt(self):
        args = self.args(self.commit())
        actual = project_installation._installed_adaptation

        def altered(destination, selected, pin):
            report = actual(destination, selected, pin)
            report['projectPlan']['target']['commit'] = '0' * 40
            return report

        with patch.object(project_installation, '_installed_adaptation', side_effect=altered):
            with self.assertRaisesRegex(Refused, 'final source and controller binding'):
                project_installation.install_project(ROOT, args)
        self.assertFalse(self.output.exists())

    def test_head_changed_after_discovery_cannot_receive_receipt(self):
        args = self.args(self.commit())
        actual = project_installation._installed_adaptation

        def changed_head(destination, selected, pin):
            report = actual(destination, selected, pin)
            self.git_run('commit', '--allow-empty', '-qm', 'Changed during inspection')
            return report

        with patch.object(project_installation, '_installed_adaptation', side_effect=changed_head):
            with self.assertRaisesRegex(Refused, 'Checkout HEAD differs'):
                project_installation.install_project(ROOT, args)
        self.assertFalse(self.output.exists())

    def test_controller_changed_after_discovery_cannot_receive_receipt(self):
        args = self.args(self.commit())
        actual = project_installation._installed_adaptation

        def changed_controller(destination, selected, pin):
            report = actual(destination, selected, pin)
            helper = destination / 'lib/aim_ci/__init__.py'
            helper.write_bytes(helper.read_bytes() + b'\n# changed after inspection\n')
            return report

        with patch.object(project_installation, '_installed_adaptation', side_effect=changed_controller):
            with self.assertRaisesRegex(Refused, '__init__.py SHA-256 mismatch'):
                project_installation.install_project(ROOT, args)
        self.assertFalse(self.output.exists())

    def test_receipt_created_during_inspection_is_not_overwritten(self):
        args = self.args(self.commit())
        actual = project_installation._installed_adaptation

        def competing_output(destination, selected, pin):
            report = actual(destination, selected, pin)
            self.output.write_text('Existing evidence')
            return report

        with patch.object(project_installation, '_installed_adaptation', side_effect=competing_output):
            with self.assertRaisesRegex(Refused, 'project installation is incomplete'):
                project_installation.install_project(ROOT, args)
        self.assertEqual(self.output.read_text(), 'Existing evidence')
        control_package.verify_installation(self.destination, self.release['manifestSha256'])

    def test_older_archive_still_installs_but_project_install_refuses_before_outputs(self):
        from aim_ci.control_archive import READINESS_FILES
        payload = {name: (ROOT / name).read_bytes() for name in READINESS_FILES}
        data, built = control_package._build_archive(payload, '0.10.0-old-install.1')
        archive = self.case / 'older-control.tar.gz'
        archive.write_bytes(data)
        ordinary = self.case / 'ordinary-install'
        result = control_package.install(archive, built['archiveSha256'], ordinary)
        self.assertEqual(result['mode'], 'installed-offline-capable')
        args = self.args(self.commit())
        args.archive, args.sha256 = archive, built['archiveSha256']
        with self.assertRaisesRegex(Refused, 'predates project-aware installation'):
            project_installation.install_project(ROOT, args)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
