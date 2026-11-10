"""Installed project-local handoff, real committed metadata, no project execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import signal
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import project_validation as validation
from aim_ci.owned_paths import Refused
import test_project_onboarding as fixtures


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProjectValidationTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectOnboardingTests.setUpClass.__func__)
    git_run = fixtures.ProjectOnboardingTests.git_run
    cli = fixtures.ProjectOnboardingTests.cli
    prepare = fixtures.ProjectOnboardingTests.prepare
    citation = fixtures.ProjectOnboardingTests.citation
    assessment = fixtures.ProjectOnboardingTests.assessment
    record = fixtures.ProjectOnboardingTests.record

    def setUp(self):
        fixtures.ProjectOnboardingTests.setUp(self)
        self.current, self.current_pin = self.installed, self.release['manifestSha256']
        self.prior = self.case / 'prior-controller'
        shutil.copytree(self.current, self.prior)
        removed = ['lib/aim_ci/project_validation.py', 'docs/PROJECT-VALIDATION.md']
        for name in removed: (self.prior / name).unlink()
        archive = self.prior / 'lib/aim_ci/control_archive.py'
        text = archive.read_text()
        for name in removed: text = text.replace('    ' + repr(name) + ',\n', '')
        archive.write_text(text)
        manifest_path = self.prior / 'control-manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        for name in removed: del manifest['files'][name]
        manifest['files']['lib/aim_ci/control_archive.py'] = sha(archive)
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        self.installed = self.prior
        self.release = {**self.release, 'manifestSha256': sha(manifest_path)}
        root = {'name': 'fixture-root', 'version': '1.0.0', 'engines': {'node': '>=22.12.0'},
            'scripts': {'preinstall': 'echo install-review-marker', 'prepare': 'echo prepare-review-marker', 'prebuild': 'node scripts/check.py', 'build': 'node scripts/check.py',
                'ci:gate': 'npm run build && npm --prefix web ci && npm --prefix web run test'}}
        nested = {'name': 'fixture-api', 'version': '1.0.0', 'engines': {'node': '>=24'},
                  'scripts': {'pretest': 'echo reviewed-prehook', 'test': 'node ../scripts/check.py'}}
        for prefix, package in [('', root), ('web/', nested)]:
            (self.checkout / (prefix + 'package.json')).write_text(json.dumps(package, indent=2) + '\n')
            (self.checkout / (prefix + 'package-lock.json')).write_text(json.dumps({
                'name': package['name'], 'version': '1.0.0', 'lockfileVersion': 3,
                'packages': {'': {'name': package['name'], 'version': '1.0.0'}}}, indent=2) + '\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Project gates')
        self.commit = self.git_run('rev-parse', 'HEAD')
        packet = self.prepare(); self.record(self.assessment(packet))
        self.instance = json.loads(self.instance_path.read_bytes())
        self.declaration = self.case / 'validation-declaration.json'
        self.output = self.case / 'validation-handoff.json'
        cite = {'path': 'README.md', 'startLine': 1, 'endLine': 2}
        self.d = {'format': validation.DECLARATION, 'schemaVersion': 1,
            'instanceSha256': sha(self.instance_path), 'target': self.instance['target'],
            'review': {'reference': 'Independent local planning review',
                'qualifications': ['Use pinned-commit tree evidence, not index listings.',
                                   'Review committed selected content for secrets.'],
                'openItems': ['Installation and runtime qualification remain open.']},
            'sourcePaths': ['README.md', 'package.json', 'package-lock.json', 'web/package.json',
                            'web/package-lock.json', 'scripts/check.py', '.github/workflows/ci.yml'],
            'packageRoots': [{'directory': '.', 'lockfile': 'package-lock.json'},
                             {'directory': 'web', 'lockfile': 'web/package-lock.json'}],
            'runtimes': [{'id': 'local-node24', 'requirements': ['Node 24, reviewed npm and fixture tools'], 'sources': [cite]}],
            'jobs': [
                {'id': 'local-aggregate', 'mode': 'local-sequence', 'runtime': 'local-node24', 'needs': [],
                 'stages': [{'root': '.', 'operation': 'ci', 'script': None},
                            {'root': '.', 'operation': 'run', 'script': 'ci:gate'}],
                 'coverage': 'Actual local aggregate, not hosted CI', 'effects': ['Normal installs, hooks and build outputs'],
                 'prerequisites': ['Review exact sources, runtimes and effects'], 'sources': [cite]},
                {'id': 'nested-component', 'mode': 'component-only', 'runtime': 'local-node24', 'needs': [],
                 'stages': [{'root': 'web', 'operation': 'run', 'script': 'test'}],
                 'coverage': 'One component, not full validation', 'effects': ['Runs the named component and prehook'],
                 'prerequisites': ['Separate component qualification'], 'sources': [cite]},
                {'id': 'hosted-job', 'mode': 'hosted-reference', 'runtime': 'local-node24', 'needs': [],
                 'stages': [], 'coverage': 'Workflow reference only', 'effects': ['Declared hosted effects, not invoked'],
                 'prerequisites': ['Independent workflow and run review'],
                 'sources': [{'path': '.github/workflows/ci.yml', 'startLine': 1, 'endLine': 9}]}],
            'excludedCoverage': ['Hosted/Windows execution, artifact publication and deployment']}
        self.save()

    def save(self):
        self.declaration.write_text(json.dumps(self.d, indent=2) + '\n')

    def args(self):
        return SimpleNamespace(action='plan-project-validation', manifest_sha256=self.current_pin,
            instance=self.instance_path, instance_sha256=sha(self.instance_path), instance_controller=self.prior,
            instance_controller_manifest_sha256=self.release['manifestSha256'],
            python=Path(sys.executable).resolve(), python_sha256=sha(Path(sys.executable).resolve()),
            declaration=self.declaration, declaration_sha256=sha(self.declaration), output=self.output)

    def validation_cli(self, verify=False, expected=0, extra=()):
        args = self.args()
        command = [str(args.python), '-IBS', str(self.current / 'aim-flow.py'),
            'verify-project-validation' if verify else 'plan-project-validation']
        for option in ('manifest_sha256', 'instance', 'instance_sha256', 'instance_controller',
                       'instance_controller_manifest_sha256', 'python', 'python_sha256', 'declaration', 'declaration_sha256'):
            command += ['--' + option.replace('_', '-'), str(getattr(args, option))]
        command += (['--handoff', str(self.output), '--handoff-sha256', sha(self.output)] if verify
                    else ['--output', str(self.output)])
        command += list(extra)
        result = subprocess.run(command, cwd=self.case, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected: return result.stderr
        self.assertFalse(result.stderr)
        return json.loads(result.stdout)

    def test_installed_handoff_preserves_old_instance_real_scripts_hooks_and_independent_jobs(self):
        before = self.instance_path.read_bytes()
        receipt = self.validation_cli()
        self.assertEqual(receipt['sha256'], sha(self.output))
        value = json.loads(self.output.read_bytes())
        self.assertFalse(value['ready']); self.assertFalse(value['executionAuthorized'])
        self.assertEqual(value['agentUnknowns'], self.instance['assessment']['unknowns'])
        self.assertEqual(value['review'], self.d['review'])
        self.assertEqual([j['needs'] for j in value['jobs']], [[], [], []])
        self.assertEqual([s['name'] for s in value['jobs'][0]['stages'][0]['declaredLifecycle']], ['preinstall', 'prepare'])
        self.assertIn('dependency-and-default-hooks-unexpanded', value['jobs'][0]['stages'][0]['lifecycleCoverage'])
        self.assertEqual([s['argv'] for s in value['jobs'][0]['stages']], [['npm', 'ci'], ['npm', 'run', 'ci:gate']])
        self.assertEqual([s['name'] for s in value['jobs'][1]['stages'][0]['declaredLifecycle']], ['pretest', 'test'])
        self.assertEqual(value['packageRoots']['.']['scripts']['prebuild'], 'node scripts/check.py')
        self.assertEqual(value['packageRoots']['web']['engines'], {'node': '>=24'})
        self.assertEqual(value['jobs'][2]['status'], 'reference-only')
        self.assertEqual(value['readiness']['deployment'], 'not-authorized')
        self.assertEqual(self.instance_path.read_bytes(), before)
        self.assertEqual(self.validation_cli(verify=True)['status'], 'source-bound-validation-handoff')

    def test_model_command_text_is_not_turned_into_argv(self):
        value = validation.derive(self.current, self.args())
        self.assertNotIn('uv sync --frozen', json.dumps([stage['argv'] for job in value['jobs'] for stage in job['stages']]))
        self.assertEqual(value['jobs'][0]['stages'][1]['declaredLifecycle'][0]['body'], self.dummy_body())

    @staticmethod
    def dummy_body(): return 'npm run build && npm --prefix web ci && npm --prefix web run test'

    def test_index_only_file_and_dirty_source_are_not_committed_evidence(self):
        (self.checkout / 'index-only.txt').write_text('not committed\n'); self.git_run('add', 'index-only.txt')
        self.d['sourcePaths'].append('index-only.txt'); self.save()
        self.assertIn('committed regular', self.validation_cli(expected=1))
        self.d['sourcePaths'].remove('index-only.txt'); self.save()
        (self.checkout / 'package.json').write_text('{"scripts":{"ci:gate":"deploy production"}}')
        value = validation.derive(self.current, self.args())
        self.assertEqual(value['packageRoots']['.']['scripts']['ci:gate'], self.dummy_body())

    def test_script_root_reference_and_dependency_tampering_refuse(self):
        original = copy.deepcopy(self.d)
        changes = [lambda d: d['jobs'][0]['stages'][1].update(script='absent'),
            lambda d: d['jobs'][0]['stages'][1].update(argv=['npm', '--ignore-scripts', 'run', 'ci:gate']),
            lambda d: d['jobs'][0].update(needs=['hosted-job']),
            lambda d: d['jobs'][0]['sources'][0].update(startLine=True),
            lambda d: d['packageRoots'][1].update(lockfile='package-lock.json'),
            lambda d: d.update(executionAuthorized=True),
            lambda d: d['jobs'][2]['stages'].append({'root': '.', 'operation': 'run', 'script': 'ci:gate'})]
        for change in changes:
            self.d = copy.deepcopy(original); change(self.d); self.save()
            with self.assertRaises(Refused): validation.derive(self.current, self.args())

    def test_old_controller_extra_import_bytecode_and_symlink_refuse(self):
        for name, contents in [('lib/shadow.py', 'pass\n'), ('lib/stale.pyc', 'bytecode')]:
            path = self.prior / name; path.write_text(contents)
            with self.assertRaises(Refused): validation.derive(self.current, self.args())
            path.unlink()
        path = self.prior / 'lib/linked'; path.symlink_to(self.current / 'lib')
        with self.assertRaises(Refused): validation.derive(self.current, self.args())
        path.unlink()
        path = self.prior / 'lib/__pycache__'; path.mkdir()
        with self.assertRaises(Refused): validation.derive(self.current, self.args())

    def test_original_controller_instance_and_python_pins_are_required(self):
        for key in ('instance_sha256', 'instance_controller_manifest_sha256', 'python_sha256'):
            args = self.args(); setattr(args, key, '0' * 64)
            with self.assertRaises(Refused): validation.derive(self.current, args)
        (self.prior / 'aim-flow.py').write_text('raise SystemExit(0)\n')
        with self.assertRaises(Refused): validation.derive(self.current, self.args())

    def test_old_verifier_unexpected_stdout_or_stderr_refuses(self):
        result = {'status': 'source-bound-project-draft', 'ready': False, 'executionAuthorized': False,
            'target': self.instance['target'], 'instanceSha256': sha(self.instance_path),
            'validation': self.instance['validation'], 'next': self.instance['next']}
        for output, error in [(json.dumps({**result, 'ready': True}).encode(), b''),
                              (json.dumps(result).encode(), b'unexpected diagnostic')]:
            with patch.object(validation, 'verification_process', return_value=SimpleNamespace(returncode=0, stdout=output, stderr=error)):
                with self.assertRaises(Refused): validation.verified_instance(self.current, self.args())

    def test_source_commit_declaration_handoff_drift_and_overwrite_refuse(self):
        self.validation_cli()
        self.assertIn('unsafe', self.validation_cli(expected=1))
        data = json.loads(self.output.read_bytes()); data['jobs'][0]['stages'].reverse()
        self.output.write_text(json.dumps(data))
        self.assertIn('differs', self.validation_cli(verify=True, expected=1))
        self.git_run('commit', '--allow-empty', '-qm', 'Changed target')
        self.assertIn('Predecessor refused', self.validation_cli(verify=True, expected=1))

    def test_malformed_nested_metadata_is_a_controlled_refusal(self):
        self.d['jobs'][0]['runtime'] = []
        self.save()
        with self.assertRaises(Refused): validation.derive(self.current, self.args())

    def test_predecessor_output_capture_is_bounded_and_exact_argv_is_isolated(self):
        with self.assertRaises(Refused):
            validation.verification_process([str(Path(sys.executable).resolve()), '-IBS', '-c',
                'import sys; sys.stdout.write("x" * 100000)'], self.case, {'PATH': '/usr/bin:/bin'})
        real = validation.verification_process
        calls = []
        def observe(command, cwd, env):
            calls.append((command, cwd, env))
            return real(command, cwd, env)
        with patch.object(validation, 'verification_process', side_effect=observe):
            validation.verified_instance(self.current, self.args())
        command, cwd, env = calls[0]
        self.assertEqual(command[:3], [str(Path(sys.executable).resolve()), '-IBS', str(self.prior / 'aim-flow.py')])
        self.assertEqual(command[3], 'verify-project-onboarding')
        self.assertEqual(cwd, self.current)
        self.assertEqual(set(env), {'PATH', 'LC_ALL', 'HOME'})

    def test_failed_capture_kills_descendant_even_after_leader_exit(self):
        marker = self.case / 'escaped-verifier-child'
        pid_file = self.case / 'verifier-child-pid'
        child = 'import time; from pathlib import Path; time.sleep(1); Path(' + repr(str(marker)) + ').touch(); time.sleep(60)'
        leader = 'import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,"-IBS","-c",' + repr(child) + ']); Path(' + repr(str(pid_file)) + ').write_text(str(p.pid))'
        try:
            with patch.object(validation, 'VERIFIER_TIMEOUT', 0.5):
                with self.assertRaises(Refused):
                    validation.verification_process([str(Path(sys.executable).resolve()), '-IBS', '-c', leader],
                                                    self.case, {'PATH': '/usr/bin:/bin'})
            time.sleep(1.1)
            self.assertTrue(pid_file.exists())
            self.assertFalse(marker.exists(), 'Exited verifier leader must not leave its child running')
        finally:
            if pid_file.exists():
                try: os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass

    def test_reference_only_and_component_scope_never_upgrade_full_readiness(self):
        self.d['runtimes'][0]['requirements'] = ['Node 22.12.0: incompatible with nested >=24; investigate']
        self.save(); value = validation.derive(self.current, self.args())
        self.assertFalse(value['ready'])
        self.assertEqual(value['runtimes']['local-node24']['qualification'], 'not-verified-by-planning')
        self.assertTrue(value['readiness']['blockers'])
        self.assertTrue(all(j['status'] in ('not-run', 'reference-only') for j in value['jobs']))


if __name__ == '__main__': unittest.main()
