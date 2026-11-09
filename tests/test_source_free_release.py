"""Run with python3 -m unittest discover -s tests -p test_source_free_release.py."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.10.0-local.1'
MANIFEST = 'control-manifest.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(directory):
    return {path.relative_to(directory).as_posix(): digest(path.read_bytes())
            for path in directory.rglob('*') if path.is_file()}


def write_archive(path, members):
    with tarfile.open(path, 'x:gz') as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            if isinstance(content, bytes):
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            else:
                member.type, member.linkname = content
                archive.addfile(member)


class SourceFreeReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = tempfile.TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.scratch = Path(workspace.name).resolve()
        cls.recipient = cls.scratch / 'recipient'
        empty_path = cls.scratch / 'no-tools'
        empty_path.mkdir()
        cls.env = {'PATH': str(empty_path), 'HOME': str(cls.scratch), 'LC_ALL': 'C'}
        built = cls.run_python(ROOT / 'aim-ci-control.py', 'build-release',
                               '--version', VERSION, '--output', cls.recipient)
        cls.release = json.loads((cls.recipient / 'release.json').read_bytes())
        if json.loads(built) != {**cls.release, 'bundle': str(cls.recipient)}:
            raise AssertionError('Build receipt and release metadata differ')
        cls.archive = cls.recipient / cls.release['archive']
        cls.archive_pin = cls.release['archiveSha256']
        cls.bootstrap = cls.recipient / cls.release['bootstrap']

    @classmethod
    def run_python(cls, script, *args, expected=0, refusal=None):
        argv = [sys.executable, '-IB', str(script), *map(str, args)]
        completed = subprocess.run(argv, cwd=cls.scratch, env=cls.env, capture_output=True,
                                   text=True, timeout=30)
        if completed.returncode != expected:
            raise AssertionError(f'{argv!r}: expected exit {expected}, got {completed.returncode}\n'
                                 f'stdout: {completed.stdout}\nstderr: {completed.stderr}')
        if refusal is not None:
            if completed.stdout or refusal not in completed.stderr:
                raise AssertionError(f'{argv!r}: unexpected refusal\n'
                                     f'stdout: {completed.stdout}\nstderr: {completed.stderr}')
        elif completed.stderr:
            raise AssertionError(f'{argv!r}: unexpected stderr\n{completed.stderr}')
        return completed.stdout

    def setUp(self):
        case = tempfile.TemporaryDirectory(dir=self.scratch)
        self.addCleanup(case.cleanup)
        self.case = Path(case.name)

    def install(self, archive, pin, destination, *, refusal=None):
        return self.run_python(self.bootstrap, 'install', '--archive', archive,
                               '--sha256', pin, '--destination', destination,
                               expected=1 if refusal else 0, refusal=refusal)

    def assert_archive_refused(self, archive, message, *, pin=None):
        destination = self.case / (archive.name + '-install')
        self.install(archive, pin or digest(archive.read_bytes()), destination, refusal=message)
        self.assertFalse(destination.exists())

    def test_generated_bundle_installs_and_inspects_without_source_checkout(self):
        self.assertFalse(self.scratch.is_relative_to(ROOT))
        self.assertEqual(self.release['version'], VERSION)
        bundle_hashes = snapshot(self.recipient)
        bundle_hashes.pop('release.json')
        self.assertEqual(bundle_hashes, self.release['filesSha256'])
        with zipfile.ZipFile(self.bootstrap) as bootstrap:
            self.assertEqual(sorted(bootstrap.namelist()),
                             ['__main__.py', 'aim_ci/__init__.py', 'aim_ci/owned_paths.py'])
        verified = json.loads(self.run_python(self.bootstrap, 'verify', '--archive', self.archive,
                                              '--sha256', self.archive_pin))
        self.assertTrue(verified['verified'])
        self.assertEqual(verified['archiveSha256'], self.archive_pin)
        self.assertEqual(verified['manifestSha256'], self.release['manifestSha256'])

        destination = self.case / 'installed'
        installed = json.loads(self.install(self.archive, self.archive_pin, destination))
        self.assertEqual(installed['installed'], str(destination))
        self.assertEqual(installed['manifestSha256'], self.release['manifestSha256'])
        manifest_data = (destination / MANIFEST).read_bytes()
        manifest = json.loads(manifest_data)
        before = snapshot(destination)
        self.assertEqual(len(manifest['files']), self.release['files'])
        self.assertEqual(digest(manifest_data), installed['manifestSha256'])
        self.assertEqual(before, {**manifest['files'], MANIFEST: installed['manifestSha256']})

        manager = destination / 'aim-ci-control.py'
        help_text = self.run_python(manager, '--help')
        for command in ('discover-project', 'propose-adaptation', 'draft-build-preparation', 'acquire-npm-tarballs',
                        'check-recipe-execution'):
            self.assertIn(command, help_text)
        readiness = json.loads(self.run_python(manager, 'readiness', '--manifest-sha256',
                                               installed['manifestSha256'], expected=3))
        self.assertTrue(readiness['packageVerified'])
        self.assertFalse(readiness['ready'])
        self.assertIsNone(readiness['checkout'])
        self.assertTrue(readiness['toolsOnPath'])
        self.assertFalse(any(readiness['toolsOnPath'].values()))
        explicit = json.loads(self.run_python(manager, 'readiness', '--product', 'catalog', '--manifest-sha256',
                                              installed['manifestSha256'], expected=3))
        self.assertEqual(explicit, readiness)
        catalog = json.loads(self.run_python(manager, 'readiness', '--product', 'catalog', '--manifest-sha256',
                                             installed['manifestSha256'], expected=3))
        self.assertEqual(set(catalog), set(readiness))
        self.assertEqual(catalog['product'], 'catalog')
        self.assertTrue(catalog['offlineOnlyProfile'])
        self.assertEqual(catalog['blockers'], readiness['blockers'])
        self.assertEqual(snapshot(destination), before)

    def test_tampered_pin_and_malformed_archives_are_refused(self):
        tampered = self.case / 'tampered.tar.gz'
        tampered.write_bytes(self.archive.read_bytes() + b'changed')
        self.assert_archive_refused(tampered, 'Controller archive SHA-256 mismatch', pin=self.archive_pin)
        malformed = self.case / 'malformed.tar.gz'
        malformed.write_bytes(b'not a gzip stream')
        self.assert_archive_refused(malformed, 'Controller archive SHA-256 mismatch', pin=self.archive_pin)
        self.assert_archive_refused(malformed, 'Malformed compressed controller archive')
        manifest = self.case / 'bad-manifest.tar.gz'
        write_archive(manifest, {MANIFEST: b'{}'})
        self.assert_archive_refused(manifest, 'Invalid controller package manifest')

    def test_archive_traversal_and_link_members_are_refused(self):
        outside = self.case / 'sentinel'
        outside.write_bytes(b'outside marker')
        cases = (
            ('traversal', '../escaped', b'escape'),
            ('symlink', 'aim-ci.py', (tarfile.SYMTYPE, str(outside))),
            ('hardlink', 'aim-ci.py', (tarfile.LNKTYPE, str(outside))),
        )
        for label, name, content in cases:
            with self.subTest(label=label):
                archive = self.case / f'{label}.tar.gz'
                write_archive(archive, {name: content})
                self.assert_archive_refused(archive, 'Unexpected, duplicate or unsafe archive member')
        self.assertFalse((self.case / 'escaped').exists())
        self.assertEqual(outside.read_bytes(), b'outside marker')

    def test_filesystem_links_and_overwrite_are_refused(self):
        linked_archive = self.case / 'linked.tar.gz'
        linked_archive.symlink_to(self.archive)
        self.assert_archive_refused(linked_archive, 'Owned input/output is linked, missing or unsafe',
                                    pin=self.archive_pin)
        existing = self.case / 'existing'
        existing.mkdir()
        (existing / 'sentinel').write_bytes(b'keep')
        before = snapshot(existing)
        linked_directory = self.case / 'linked-directory'
        linked_directory.symlink_to(existing, target_is_directory=True)
        self.install(self.archive, self.archive_pin, linked_directory / 'escape', refusal='Symlinked campaign path')
        self.install(self.archive, self.archive_pin, linked_directory, refusal='Destination already exists')
        self.install(self.archive, self.archive_pin, existing, refusal='Destination already exists')
        self.assertTrue(linked_directory.is_symlink())
        self.assertFalse((existing / 'escape').exists())
        self.assertEqual(snapshot(existing), before)

    def test_bootstrap_never_imports_payload_during_verification_or_install(self):
        marker = self.case / 'payload-imported'
        probe = f'from pathlib import Path\nPath({str(marker)!r}).write_text("imported")\n'.encode()
        with tarfile.open(self.archive, 'r:gz') as archive:
            members = {member.name: archive.extractfile(member).read() for member in archive}
        replacements = {'aim-ci-control.py': probe, 'lib/openai_budget.py': probe}
        members.update(replacements)
        invalid = self.case / 'wrong-member-digest.tar.gz'
        write_archive(invalid, members)
        self.assert_archive_refused(invalid, 'aim-ci-control.py SHA-256 mismatch')
        self.assertFalse(marker.exists())

        manifest = json.loads(members[MANIFEST])
        manifest['files'].update({name: digest(data) for name, data in replacements.items()})
        members[MANIFEST] = json.dumps(manifest).encode()
        valid = self.case / 'pinned-marker-payload.tar.gz'
        write_archive(valid, members)
        pin = digest(valid.read_bytes())
        self.run_python(self.bootstrap, 'verify', '--archive', valid, '--sha256', pin)
        self.assertFalse(marker.exists())
        destination = self.case / 'marker-install'
        self.install(valid, pin, destination)
        self.assertFalse(marker.exists())
        self.assertEqual((destination / 'aim-ci-control.py').read_bytes(), probe)


class DevelopmentCandidateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.checkout = self.workspace/'source'
        self.checkout.mkdir()
        sys.path.insert(0, str(ROOT/'lib'))
        self.addCleanup(sys.path.remove, str(ROOT/'lib'))
        from aim_ci.control_archive import FILES
        self.files = FILES
        for name in FILES:
            path = self.checkout/name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, path)
        git = shutil.which('git')
        self.assertIsNotNone(git, 'Exact-commit candidate fixtures require an existing Git executable')
        self.git = str(Path(git).resolve())
        self.env = {'PATH': str(Path(self.git).parent) + os.pathsep + os.defpath,
                    'HOME': str(self.workspace), 'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_ALLOW_PROTOCOL': '',
                    'GIT_AUTHOR_NAME': 'Candidate fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                    'GIT_COMMITTER_NAME': 'Candidate fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        self.local_git('init', '--quiet', '--initial-branch=main', '--template=')
        self.local_git('config', '--local', 'remote.origin.url', 'https://github.com/example/control')
        self.local_git('add', '--', '.')
        self.local_git('commit', '--quiet', '-m', 'Synthetic exact candidate source')
        self.head = self.local_git('rev-parse', 'HEAD').decode().strip()
        self.tree = self.local_git('rev-parse', 'HEAD^{tree}').decode().strip()
        self.output = self.workspace/'candidate'

    def local_git(self, *args):
        return subprocess.check_output([self.git, '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false', *args],
                                       cwd=self.checkout, env=self.env, timeout=15)

    def candidate(self, *, head=None, output=None, ref=None, refusal=None):
        args = [sys.executable, '-IB', str(self.checkout/'aim-ci-control.py'), 'build-development-candidate',
                '--commit', head or self.head, '--output', str(output or self.output), '--repository', 'example/control',
                '--run-id', '12345', '--run-attempt', '2', '--workflow-sha', 'a'*40,
                '--workflow-ref', ref or 'example/control/.github/workflows/aim-ci.yml@refs/pull/3/merge']
        result = subprocess.run(args, cwd=self.checkout, env=self.env, capture_output=True, timeout=30)
        if refusal:
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(refusal.encode(), result.stderr)
            self.assertFalse(result.stdout)
            self.assertFalse((output or self.output).exists())
            return None
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)
        return json.loads(result.stdout)

    def test_candidate_binds_all_bundle_bytes_and_actual_source_free_readiness(self):
        # A file outside the packaging allowlist never contributes to the bundle.
        (self.checkout/'not-part-of-the-bundle').write_text('untracked fixture')
        before = snapshot(self.checkout)
        receipt = self.candidate()
        self.assertEqual(snapshot(self.checkout), before)
        self.assertEqual(json.loads((self.output/'candidate.json').read_bytes()), receipt)
        self.assertEqual(receipt['kind'], 'aim-ci-development-candidate')
        self.assertEqual(receipt['acceptance'], 'unaccepted')
        self.assertEqual(receipt['version'], '0.0.0-dev.g'+self.head)
        self.assertEqual(receipt['source'], {'repository':'example/control', 'commit':self.head, 'tree':self.tree})
        self.assertEqual(receipt['workflow'], {'runId':'12345', 'runAttempt':'2', 'sha':'a'*40,
            'ref':'example/control/.github/workflows/aim-ci.yml@refs/pull/3/merge'})
        actual = snapshot(self.output)
        actual.pop('candidate.json')
        self.assertEqual(actual, receipt['filesSha256'])
        with tarfile.open(self.output/receipt['archive']) as bundle:
            self.assertEqual(set(bundle.getnames()), {*self.files, MANIFEST})
            self.assertEqual(digest(bundle.extractfile(MANIFEST).read()), receipt['manifestSha256'])
            for name in self.files:
                self.assertEqual(bundle.extractfile(name).read(), self.local_git('show', f'{self.head}:{name}'), name)
        with zipfile.ZipFile(self.output/receipt['bootstrap']) as bootstrap:
            self.assertEqual(bootstrap.read('__main__.py'), self.local_git('show', f'{self.head}:lib/aim_ci/control_archive.py'))
        self.assertEqual((self.output/'README.md').read_bytes(), self.local_git('show', f'{self.head}:docs/aim-ci-onboarding.md'))
        proof = receipt['installedProof']
        self.assertTrue(proof['outsideCheckout'] and proof['emptyPath'] and proof['systemSiteDisabled']
                        and proof['installedFilesUnchanged'])
        self.assertEqual(proof['bootstrapVerify']['exitCode'], 0)
        self.assertEqual(proof['bootstrapInstall']['exitCode'], 0)
        ready = proof['installedReadiness']
        self.assertEqual(ready['exitCode'], 3)
        self.assertFalse(ready['result']['ready'])
        self.assertTrue(ready['result']['packageVerified'])
        self.assertIsNone(ready['result']['checkout'])
        self.assertFalse(any(ready['result']['toolsOnPath'].values()))
        project = proof['projectReadiness']
        self.assertEqual(project['exitCode'], 3)
        self.assertEqual(project['result']['status'], 'configuration-incomplete')
        self.assertTrue(project['result']['package']['verified'])
        self.assertFalse(project['result']['executionAuthorized'])
        self.assertEqual(set(project['result']['runtime'].values()), {'not-checked'})

    def test_candidate_refuses_dirty_packaged_bytes_before_creating_output(self):
        path = self.checkout/'docs/aim-ci-onboarding.md'
        data = path.read_bytes()
        path.write_bytes(b'!' + data[1:])
        self.candidate(refusal='Candidate input differs from the validated commit: docs/aim-ci-onboarding.md')

    def test_candidate_refuses_moved_head_and_invalid_provenance(self):
        self.candidate(head='0'*40, refusal='HEAD differs from the validated commit')
        self.candidate(ref='other/repo/.github/workflows/aim-ci.yml@refs/pull/3/merge',
                       refusal='Invalid candidate source or workflow identity')
        self.candidate(output=self.checkout/'inside', refusal='outside the checkout')
        self.local_git('config', '--local', 'remote.origin.url', 'https://github.com/other/control')
        self.candidate(refusal='checkout origin differs from the workflow repository')
