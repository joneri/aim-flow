"""Exercise the control archive and the installed CLI outside the source repo."""
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0-local.2'


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ControlPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.scratch = Path(folder.name).resolve()
        cls.evidence = Path(os.environ.get('AIM_CI_CONTROL_OUTPUT', cls.scratch / 'evidence')).resolve()
        cls.evidence.mkdir()
        cls.env = {'PATH': os.environ['PATH'], 'HOME': str(cls.scratch), 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
        cls.archive = cls.evidence / f'aim-ci-control-{VERSION}.tar.gz'
        result = cls.cli('build', ROOT, 'aim-ci-control.py', 'build', '--version', VERSION,
                         '--output', str(cls.archive))
        cls.built = json.loads(result.stdout)
        cls.archive_sha = cls.built['archiveSha256']
        cls.destination = cls.scratch / 'installation'
        cls.cli('install', ROOT, 'aim-ci-control.py', 'install', '--archive', str(cls.archive),
                '--sha256', cls.archive_sha, '--destination', str(cls.destination))

    @classmethod
    def cli(cls, label, base, script, *args, expected=0, env=None):
        argv = [sys.executable, '-I', str(base / script), *args]
        result = subprocess.run(argv, cwd=cls.scratch, env=env or cls.env, capture_output=True, text=True, timeout=30)
        with (cls.evidence / (label + '.json')).open('x') as stream:
            json.dump({'argv': argv, 'cwd': str(cls.scratch), 'exitCode': result.returncode,
                       'stdout': result.stdout, 'stderr': result.stderr}, stream, indent=2)
        print(f'{label}: exit {result.returncode}', flush=True)
        if result.returncode != expected:
            raise AssertionError(f'{label}: expected {expected}, got {result.returncode}\n{result.stderr}')
        return result

    @classmethod
    def snapshot(cls, directory):
        return {str(path.relative_to(directory)): digest(path.read_bytes())
                for path in directory.rglob('*') if path.is_file()}

    def members(self):
        with tarfile.open(self.archive, 'r:gz') as stream:
            return {member.name: stream.extractfile(member).read() for member in stream}

    def readiness(self, label, *args, base=None, expected=3, env=None):
        return self.cli(label, base or self.destination, 'aim-ci-control.py', 'readiness',
                        '--manifest-sha256', self.built['manifestSha256'], *args, expected=expected, env=env)

    def test_archive_and_new_installation_match_current_controller_bytes(self):
        result = self.cli('verify', ROOT, 'aim-ci-control.py', 'verify', '--archive', str(self.archive),
                          '--sha256', self.archive_sha)
        self.assertTrue(json.loads(result.stdout)['verified'])
        self.assertEqual(digest(self.archive.read_bytes()), self.archive_sha)
        members = self.members()
        manifest_bytes = members.pop('control-manifest.json')
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest['version'], VERSION)
        self.assertEqual(digest(manifest_bytes), self.built['manifestSha256'])
        self.assertEqual(manifest['files'], {name: digest(data) for name, data in members.items()})
        self.assertEqual(self.snapshot(self.destination), {**manifest['files'], 'control-manifest.json': digest(manifest_bytes)})
        self.assertEqual(members['aim-ci.py'], (ROOT / 'aim-ci.py').read_bytes())
        self.assertIn('fakeATG/package-lock.json', members)
        self.assertIn('tests/production/artifact-probe.mjs', members)
        for name, data in members.items():
            self.assertFalse({'node_modules', '.sandbox', '.git', '.env.local', 'pilot.sh'} & set(Path(name).parts))
            self.assertNotIn(str(ROOT).encode(), data)
            self.assertNotIn(str(Path.home()).encode(), data)
        help_text = self.cli('installed-controller-help', self.destination, 'aim-ci.py', '--help').stdout
        self.assertIn('resume-deployment', help_text)
        self.assertIn('local-ci', help_text)

    def test_platform_build_helper_is_installed_and_tamper_refuses_readiness(self):
        helper = 'lib/aim_ci/npm_platform_build.cjs'
        members = self.members()
        for name in (helper, 'lib/aim_ci/npm_platform.py', 'lib/aim_ci/platform_build.py'):
            self.assertEqual(members[name], (ROOT / name).read_bytes())
            self.assertEqual((self.destination / name).read_bytes(), members[name])
        broken = self.scratch / 'modified-platform-helper'
        shutil.copytree(self.destination, broken)
        (broken / helper).write_bytes(members[helper] + b'// changed after installation')
        failure = self.readiness('platform-helper-tamper', base=broken, expected=1)
        self.assertIn(helper + ' SHA-256 mismatch', failure.stderr)

    def test_default_readiness_uses_no_tools_and_writes_nothing(self):
        sentinel = self.scratch / 'unexpected-tool-call'
        tools = self.scratch / 'tools'
        tools.mkdir()
        for name in ('git', 'gh', 'node', 'npm', 'sandbox-exec'):
            tool = tools / name
            tool.write_text(f'#!/bin/sh\n: > "{sentinel}"\nexit 99\n')
            tool.chmod(0o700)
        before = self.snapshot(self.destination)
        env = {**self.env, 'PATH': str(tools)}
        result = json.loads(self.readiness('default-readiness', env=env).stdout)
        self.assertTrue(result['packageVerified'])
        self.assertFalse(result['ready'])
        self.assertIsNone(result['checkout'])
        self.assertEqual(result['toolsOnPath'], {name: True for name in ('git', 'gh', 'node', 'npm', 'sandbox-exec')})
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.snapshot(self.destination), before)
        self.assertFalse((self.destination / '.sandbox').exists())
        self.assertFalse((self.destination / '.env.local').exists())

    def git(self, checkout, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=checkout,
                                       env=self.env, text=True).strip()

    def checkout(self, product, directory):
        checkout = self.scratch / (product + '-target')
        checkout.mkdir()
        (checkout / directory).mkdir()
        (checkout / directory / 'test.txt').write_text('Local target fixture\n')
        self.git(checkout, 'init', '-q', '-b', 'main')
        self.git(checkout, 'add', '.')
        self.git(checkout, '-c', 'user.name=Local fixture', '-c', 'user.email=fixture@invalid',
                 'commit', '-qm', 'Local target')
        return checkout

    def test_readiness_checks_explicit_checkout_and_offline_profile(self):
        fake = self.checkout('fakeatg', 'fakeATG')
        self.git(fake, 'remote', 'add', 'origin', 'https://github.com/joneri/agenticpipeline.git')
        result = json.loads(self.readiness('fakeatg-target', '--product', 'fakeatg', '--checkout', str(fake)).stdout)
        self.assertEqual(result['checkout']['head'], self.git(fake, 'rev-parse', 'HEAD'))
        self.assertFalse(result['ready'])
        self.git(fake, 'remote', 'set-url', '--push', 'origin', 'https://github.com/fixture/wrong.git')
        failure = self.readiness('wrong-target', '--checkout', str(fake), expected=1)
        self.assertIn('origin does not match', failure.stderr)
        catalog = self.checkout('catalog', 'catalog')
        origin = self.scratch / 'catalog.git'
        self.git(self.scratch, 'clone', '-q', '--bare', '--local', str(catalog), str(origin))
        self.git(catalog, 'remote', 'add', 'origin', str(origin))
        result = json.loads(self.readiness('catalog-target', '--product', 'catalog', '--checkout', str(catalog)).stdout)
        self.assertTrue(result['offlineOnlyProfile'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['checkout']['origin'], str(origin))

    def test_partial_checkout_is_refused_without_fetch_or_object_writes(self):
        source = self.checkout('partial-fixture', 'catalog')
        origin = self.scratch / 'promisor.git'
        self.git(self.scratch, 'clone', '-q', '--bare', '--local', str(source), str(origin))
        self.git(origin, 'config', 'uploadpack.allowFilter', 'true')
        partial = self.scratch / 'partial-checkout'
        self.git(self.scratch, '-c', 'protocol.file.allow=always', 'clone', '-q',
                 '--filter=tree:0', '--no-checkout', origin.as_uri(), str(partial))
        self.assertEqual(self.git(partial, 'config', '--get', 'remote.origin.promisor'), 'true')
        self.git(partial, 'remote', 'set-url', 'origin', str(origin))
        objects = partial / '.git/objects'
        before = self.snapshot(objects)
        self.assertTrue(any(name.endswith('.promisor') for name in before))

        trace = self.evidence / 'partial-git-trace.jsonl'
        tools = self.scratch / 'partial-tools'
        tools.mkdir()
        git = tools / 'git'
        executable = shutil.which('git', path=self.env['PATH'])
        self.assertIsNotNone(executable)
        git.write_text(f'#!/bin/sh\nGIT_TRACE2_EVENT={shlex.quote(str(trace))} '
                       f'exec {shlex.quote(executable)} "$@"\n')
        git.chmod(0o700)
        env = {**self.env, 'PATH': str(tools) + os.pathsep + self.env['PATH']}
        result = self.readiness('partial-checkout', '--product', 'catalog', '--checkout', str(partial),
                                expected=1, env=env)
        self.assertIn('product tree is missing or unavailable locally', result.stderr)
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        commands = [row['argv'] for row in events if 'argv' in row]
        self.assertTrue(any('cat-file' in argv for argv in commands))
        self.assertFalse(any('fetch' in argv for argv in commands))
        after = self.snapshot(objects)
        self.assertEqual(after, before)
        with (self.evidence / 'partial-object-check.json').open('x') as stream:
            json.dump({'before': before, 'after': after, 'gitCommands': commands,
                       'fetchObserved': False, 'originTransport': 'local filesystem'}, stream, indent=2)

    def test_checkout_inspection_refuses_git_below_supported_version(self):
        tools = self.scratch / 'old-git-tools'
        tools.mkdir()
        calls = self.evidence / 'old-git-calls.txt'
        git = tools / 'git'
        git.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {shlex.quote(str(calls))}\n'
                       'printf "%s\\n" "git version 2.53.0"\n')
        git.chmod(0o700)
        result = self.readiness('old-git', '--checkout', str(self.scratch), expected=1,
                                env={**self.env, 'PATH': str(tools)})
        self.assertIn('requires Git 2.54.0 or later', result.stderr)
        self.assertEqual(calls.read_text().splitlines(), ['-c core.fsmonitor=false --version'])

    def test_installed_work_entrypoints_refuse_without_state(self):
        before = self.snapshot(self.destination)
        for label, script, args in (
            ('worker', 'aim-ci.py', ('run', '--issue', '1')),
            ('recovery', 'aim-ci.py', ('resume-deployment', '--issue', '1', '--observe')),
            ('local-qualification', 'aim-ci.py', ('qualify-offline', '--output', str(self.scratch / 'unstarted'))),
            ('release-gate', 'aim-ci-release.py', ('gate', '--head', 'a' * 40)),
            ('legacy-probe', 'api-story-probe.py', ('run',)),
        ):
            with self.subTest(entry=label):
                result = self.cli(label, self.destination, script, *args, expected=1)
                self.assertIn('inspection-only', result.stderr)
        self.assertFalse((self.scratch / 'unstarted').exists())
        self.assertFalse((self.destination / '.sandbox').exists())
        self.assertEqual(self.snapshot(self.destination), before)

    def test_refuses_tampered_archive_and_unsafe_members_before_install(self):
        target = self.scratch / 'refused-install'
        for label in ('wrong-hash', 'changed-content', 'unexpected-member', 'traversal'):
            with self.subTest(case=label):
                if label == 'wrong-hash':
                    archive, sha = self.archive, '0' * 64
                    reason = 'archive SHA-256 mismatch'
                else:
                    members = self.members()
                    if label == 'changed-content':
                        members['aim-ci.py'] += b'\n# changed\n'
                        reason = 'aim-ci.py SHA-256 mismatch'
                    else:
                        members['.env.local' if label == 'unexpected-member' else '../escaped'] = b'fixture only'
                        reason = 'unsafe archive member'
                    archive = self.scratch / f'{label}.tar.gz'
                    with tarfile.open(archive, 'w:gz') as stream:
                        for name, data in members.items():
                            member = tarfile.TarInfo(name)
                            member.size = len(data)
                            stream.addfile(member, io.BytesIO(data))
                    sha = digest(archive.read_bytes())
                result = self.cli(label, ROOT, 'aim-ci-control.py', 'install', '--archive', str(archive),
                                  '--sha256', sha, '--destination', str(target), expected=1)
                self.assertIn(reason, result.stderr)
                self.assertFalse(target.exists())
        self.assertFalse((self.scratch / 'escaped').exists())

    def test_existing_install_and_changed_installed_file_are_refused(self):
        before = self.snapshot(self.destination)
        self.cli('existing-install', ROOT, 'aim-ci-control.py', 'install', '--archive', str(self.archive),
                 '--sha256', self.archive_sha, '--destination', str(self.destination), expected=1)
        self.assertEqual(self.snapshot(self.destination), before)
        modified = self.scratch / 'modified-install'
        shutil.copytree(self.destination, modified)
        (modified / 'profiles/catalog.json').write_text('{}\n')
        result = self.readiness('modified-install', base=modified, expected=1)
        self.assertIn('profiles/catalog.json SHA-256 mismatch', result.stderr)


if __name__ == '__main__':
    unittest.main()
