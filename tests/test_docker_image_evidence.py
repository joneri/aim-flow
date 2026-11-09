"""Verify image byte and staging protocol contracts without Docker or project processes."""
from base64 import b64encode
from contextlib import contextmanager
from hashlib import sha1, sha256, sha512
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci import docker_engine, docker_image_evidence as evidence
from aim_ci.npm_platform import PLATFORM_STORE
from aim_ci.recipe_plan import (browser_runtime_files, browser_runtime_paths, container_runtime_sha,
                                validate_browser_runtime)
from openai_budget import Refused, canonical, fingerprint


IMAGE = 'sha256:' + 'a' * 64
CONTROL = 'b' * 64
CONTAINER = 'c' * 64
ENDPOINT = 'unix:///fixture/test.sock'
SCRIPT = 'test:unit'
LAYOUT = '/opt/aim-ci/qualification'
ZERO = '0001-01-01T00:00:00Z'

BROWSER_SELECTOR = {'kind': 'playwright-names-v1', 'file': 'tests/receipt.spec.ts',
                    'project': 'chromium', 'suite': 'receipt component', 'names': ['opens receipt']}


def synthetic_elf(machine=183):
    return b'\x7fELF\x02\x01\x01' + bytes(9) + b'\x03\x00' + machine.to_bytes(2, 'little') + b'fixture'


def browser_fixture(executable_bytes=None):
    binary = '/opt/aim-ci-browser/chromium_headless_shell-1217/chrome-linux/headless_shell'
    library = '/usr/lib/aarch64-linux-gnu/libstdc++.so.6.0.34'
    members = {binary: (0o755, executable_bytes or synthetic_elf()), library: (0o644, synthetic_elf())}
    pins = {name: {'size': len(data), 'mode': mode, 'sha256': sha256(data).hexdigest()}
            for name, (mode, data) in members.items()}
    runtime = {'kind': 'playwright-chromium-inventory-v1',
               'platform': {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'}, 'revision': '1217',
               'executable': {'path': binary, **pins[binary]}, 'libraries': {library: pins[library]},
               'network': {'mode': 'none', 'loopback': 'container-only', 'externalEgress': 'deny',
                           'publishedPorts': [], 'hostMounts': [], 'hostOrVmDns': 'not-requested'}}
    return runtime, members


def git_object(kind, data):
    return sha1(kind + b' ' + str(len(data)).encode() + b'\0' + data).digest()


def tar_bytes(rows, *, gzip=False):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w:gz' if gzip else 'w:', format=tarfile.USTAR_FORMAT) as tar:
        for name, mode, kind, data in rows:
            item = tarfile.TarInfo(name)
            item.type, item.mode, item.size = kind, mode, len(data)
            tar.addfile(item, io.BytesIO(data))
    return target.getvalue()


def regular(name, data, mode=0o644):
    return name, mode, tarfile.REGTYPE, data


def directory(name):
    return name, 0o755, tarfile.DIRTYPE, b''


class ArchiveResponse(io.BytesIO):
    status = 200

    def read1(self, size=-1):
        if not 0 < size <= 64 * 1024:
            raise AssertionError('Expected a bounded streaming read')
        return super().read1(size)

    def getheader(self, name):
        return 'application/x-tar' if name == 'Content-Type' else None


class DockerImageEvidenceTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket', 'subprocess.Popen'):
            guard = patch(name, side_effect=AssertionError('Real transport or process forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.plan_path, self.profile_path = self.root / 'plan.json', self.root / 'profile.json'
        self.journal = self.root / 'journal'
        self.acquisition = 'd' * 64
        self.blob = tar_bytes([regular('package/index.js', b'export {};\n')], gzip=True)
        self.integrity = 'sha512-' + b64encode(sha512(self.blob).digest()).decode()
        self.archives, self.read_paths, self.calls = {}, {}, []
        self.payload = self.name = None
        self.exists = False
        self.image_id = IMAGE
        self.architecture = 'arm64'
        self.host_overrides = {}
        self.mounts = []
        self.started_at = ZERO
        self.change_after_copies = False
        self.lose_create = self.lose_copy = self.lose_remove = False
        self.remove_takes_effect = True
        self.build()

    def build(self, *, store_blob=None, uncovered=False, platform=False, source_leaf=None, selector=None):
        vite = {'version': '7.3.3', 'integrity': self.integrity,
                'resolved': 'https://registry.npmjs.org/vite/-/vite-7.3.3.tgz'}
        deps = {'vite': '7.3.3'}
        scripts = {SCRIPT: 'fixture', 'build': 'vite build'}
        extra = {}
        entries = {'node_modules/vite': vite}
        if selector is not None:
            scripts[SCRIPT] = 'playwright test'
            deps['@playwright/test'] = '1.59.1'
            for name in ('@playwright/test', 'playwright', 'playwright-core'):
                entries['node_modules/' + name] = {
                    'version': '1.59.1', 'integrity': self.integrity,
                    'resolved': f'https://registry.npmjs.org/{name}/-/{name.split("/")[-1]}-1.59.1.tgz'}
            extra = {'playwright.config.ts': b'// synthetic configuration\n', selector['file']: b'// synthetic spec\n'}
        pkg = canonical({'scripts': scripts, 'devDependencies': deps}).encode()
        lock = canonical({'lockfileVersion': 3, 'packages': {'': {'devDependencies': deps}, **entries}}).encode()
        leaf = b'export const a = 1;\n' if source_leaf is None else source_leaf
        prefix = b'/* prefix sibling */\n'
        subtree = git_object(b'tree', b'100755 Fixture Font.woff2\0' + git_object(b'blob', leaf))
        # Git compares a directory as "a/", so a.c precedes a despite lexical basename order.
        tree_data = (b'100644 a.c\0' + git_object(b'blob', prefix)
                     + b'40000 a\0' + subtree
                     + b'100644 package-lock.json\0' + git_object(b'blob', lock)
                     + b'100644 package.json\0' + git_object(b'blob', pkg))
        if extra:
            spec = selector['file'].split('/')[1].encode()
            spec_tree = git_object(b'tree', b'100644 ' + spec + b'\0' + git_object(b'blob', extra[selector['file']]))
            tree_data += (b'100644 playwright.config.ts\0' + git_object(b'blob', extra['playwright.config.ts'])
                          + b'40000 tests\0' + spec_tree)
        tree = git_object(b'tree', tree_data).hex()
        commit = (f'tree {tree}\nauthor Fixture <fixture@example.invalid> 0 +0000\n'
                  'committer Fixture <fixture@example.invalid> 0 +0000\n\nFixture\n').encode()
        inputs = {'package.json': sha256(pkg).hexdigest(), 'package-lock.json': sha256(lock).hexdigest()}
        blob = self.blob if store_blob is None else store_blob
        blob_pin = sha256(blob).hexdigest()
        packages = {} if uncovered else {name: {'sha256': blob_pin, 'integrity': self.integrity} for name in entries}
        index = {'kind': 'npm-v3-tarballs-v1', 'lockSha256': inputs['package-lock.json'], 'packages': packages}
        entrypoint = '/opt/aim-ci/run-recipe'
        node, npm, entry = b'n' * (1_000_000 + 513), b'// npm cli fixture\n', b'#!/bin/sh\nexit 1\n'
        if platform:
            index = {'kind': PLATFORM_STORE, 'lockSha256': inputs['package-lock.json'],
                     'platform': {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'},
                     'included': packages, 'excluded': {}}
            entrypoint = evidence.PLATFORM_ENTRYPOINT
            entry = Path(evidence.__file__).with_name('npm_platform_build.cjs').read_bytes()
        index = canonical(index).encode()
        tools = {'node': {'path': '/usr/bin/node', 'sha256': sha256(node).hexdigest()},
                 'npm': {'path': '/usr/lib/npm-cli.js', 'sha256': sha256(npm).hexdigest()}}
        self.entry_pin = sha256(entry).hexdigest()
        self.profile = {'version': 1, 'imageDigest': IMAGE, 'tools': tools,
                        'dependencyStoreSha256': sha256(index).hexdigest()}
        self.profile_path.write_text(canonical(self.profile))
        self.profile_pin = sha256(self.profile_path.read_bytes()).hexdigest()
        self.plan = {'version': 2, 'recipeSchemaVersion': 2, 'status': 'planned-only',
                     'executionAuthorized': False, 'bindingSha256': '0' * 64,
                     'control': {'manifestSha256': CONTROL}, 'recipe': {'manifestSha256': '1' * 64},
                     'runtime': {'kind': 'reviewed-container-runtime', 'verification': 'declared-not-observed',
                                 'tools': tools, 'sha256': fingerprint(tools)},
                     'containerProfile': {'sha256': self.profile_pin, 'imageDigest': IMAGE,
                                          'dependencyStoreSha256': self.profile['dependencyStoreSha256'],
                                          'verification': 'matched-separate-review-pin'},
                     'target': {'commit': git_object(b'commit', commit).hex(), 'tree': tree},
                     'committedInputs': inputs, 'testSelection': {'script': SCRIPT, 'selectedBy': 'pinned-recipe'},
                     'limits': {'network': 'disabled', 'memoryMiB': 128, 'maxProcesses': 4, 'timeoutSeconds': 5}}
        self.read_paths = {'node': '/usr/bin/node', 'npm': '/usr/lib/npm-cli.js',
                           'entrypoint': entrypoint, 'commit': LAYOUT + '/source.commit',
                           'source': LAYOUT + '/source', 'store': LAYOUT + '/dependency-store'}
        self.source_rows = [directory('source'), directory('source/a'), regular('source/a/Fixture Font.woff2', leaf, 0o755),
                            regular('source/a.c', prefix), regular('source/package-lock.json', lock),
                            regular('source/package.json', pkg)]
        if extra:
            self.source_rows.extend([directory('source/tests'),
                                     *(regular('source/' + name, data) for name, data in extra.items())])
        self.archives = {
            'node': tar_bytes([regular('node', node, 0o755)]),
            'npm': tar_bytes([regular('npm-cli.js', npm)]),
            'entrypoint': tar_bytes([regular(entrypoint.rsplit('/', 1)[-1], entry, 0o644 if platform else 0o755)]),
            'commit': tar_bytes([regular('source.commit', commit)]),
            'source': tar_bytes(self.source_rows),
            'store': tar_bytes([directory('dependency-store'), directory('dependency-store/blobs'),
                                regular('dependency-store/index.json', index),
                                regular('dependency-store/blobs/' + blob_pin + '.tgz', blob)]),
        }

    def add_browser(self, selector=BROWSER_SELECTOR, *, executable_bytes=None):
        runtime, members = browser_fixture(executable_bytes)
        self.profile['browserRuntime'] = runtime
        self.profile_path.write_text(canonical(self.profile))
        self.profile_pin = sha256(self.profile_path.read_bytes()).hexdigest()
        self.plan['containerProfile'].update(sha256=self.profile_pin, browserRuntime=runtime)
        self.plan['runtime']['sha256'] = container_runtime_sha(self.plan['runtime']['tools'], runtime)
        self.plan['testSelection']['selector'] = selector
        for role, name in browser_runtime_paths(runtime).items():
            mode, data = members[name]
            self.read_paths[role] = name
            self.archives[role] = tar_bytes([regular(Path(name).name, data, mode)])
        return runtime

    def source_check(self):
        self.build(platform=True)
        package = canonical({'scripts': {SCRIPT: 'node check.cjs'}}).encode()
        lock = canonical({'lockfileVersion': 3, 'packages': {'': {}, 'node_modules/unused': {}}}).encode()
        selected = {'package.json': package, 'package-lock.json': lock, 'check.cjs': b'process.exit(0)\n'}
        # The omitted sibling is represented only by Git membership, never selected bytes.
        complete = {**selected, '.npmrc': b'must-never-be-staged'}
        raw_tree = b''.join(b'100644 ' + name.encode() + b'\0' + git_object(b'blob', data)
                            for name, data in sorted(complete.items()))
        tree = git_object(b'tree', raw_tree).hex()
        commit = f'tree {tree}\n\nsource-check fixture\n'.encode()
        commit_pin = git_object(b'commit', commit).hex()
        proof = {'version': 1, 'kind': 'git-tree-source-subset-v1', 'commit': commit_pin, 'tree': tree,
                 'selectedPaths': sorted(selected), 'trees': {tree: b64encode(raw_tree).decode()}}
        manifest = {name: {'mode': 0o644, 'size': len(data), 'sha256': sha256(data).hexdigest()}
                    for name, data in selected.items()}
        index = canonical({'kind': 'npm-no-install-v1', 'platform': {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'},
                           'lockSha256': manifest['package-lock.json']['sha256']}).encode()
        self.profile['dependencyStoreSha256'] = sha256(index).hexdigest()
        self.profile_path.write_text(canonical(self.profile))
        self.profile_pin = sha256(self.profile_path.read_bytes()).hexdigest()
        self.plan.update(recipeSchemaVersion=3, target={'commit': commit_pin, 'tree': tree},
                         committedInputs={name: manifest[name]['sha256'] for name in ('package.json', 'package-lock.json')},
                         artifact={'kind': 'none'}, sourceFiles=manifest, sourceProofSha256=fingerprint(proof),
                         sourceSelectionSha256=fingerprint(manifest),
                         execution={'kind': 'npm-source-check-v1', 'dependencies': 'none', 'outputs': 'none',
                                    'sourcePaths': sorted(selected)})
        self.plan['containerProfile'].update(sha256=self.profile_pin, dependencyStoreSha256=sha256(index).hexdigest())
        self.read_paths['proof'] = LAYOUT + '/source.proof.json'
        self.source_rows = [directory('source'), *(regular('source/' + name, data) for name, data in sorted(selected.items()))]
        self.archives.update(commit=tar_bytes([regular('source.commit', commit)]), source=tar_bytes(self.source_rows),
                             proof=tar_bytes([regular('source.proof.json', canonical(proof).encode())]),
                             store=tar_bytes([directory('dependency-store'), regular('dependency-store/index.json', index)]))
        return proof, index

    def test_source_check_verifies_exact_subset_and_no_install_store(self):
        self.source_check()
        result = self.acquire(platform_build=True)
        measured = result['measurements']
        self.assertEqual(measured['sourceFiles'], self.plan['sourceFiles'])
        self.assertEqual(measured['sourceProofSha256'], self.plan['sourceProofSha256'])
        self.assertEqual(measured['dependencyTarballs'], {
            'kind': 'npm-no-install-v1', 'platform': {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'},
            'lockPackageCount': 1, 'packageCount': 0, 'tarballCount': 0,
            'fullLockVerified': False, 'excludedPackageCount': 1})
        self.assertFalse(result['executionAuthorized'])
        self.assertFalse(any(method == 'POST' and '/start' in path for method, path in self.calls))
        self.assertFalse(self.exists)

    def test_source_check_rejects_unselected_bytes_hooks_and_tarballs(self):
        for change, expected in (('source', 'selection'), ('hook', 'lifecycle hooks'), ('store', 'no-install store')):
            with self.subTest(change=change):
                _, index = self.source_check()
                if change == 'source':
                    self.archives['source'] = tar_bytes([*self.source_rows, regular('source/unselected.cjs', b'0')])
                elif change == 'hook':
                    self.archives['source'] = tar_bytes([
                        row if row[0] != 'source/package.json' else regular(row[0], canonical({
                            'scripts': {SCRIPT: 'node check.cjs', 'pre' + SCRIPT: 'node other.cjs'}}).encode())
                        for row in self.source_rows])
                    # Even a fully re-pinned hook declaration is rejected by npm admission.
                    inputs = {'package.json': {'scripts': {SCRIPT: 'node check.cjs', 'pre' + SCRIPT: 'node other.cjs'}},
                              'package-lock.json': {'lockfileVersion': 3, 'packages': {'': {}}}}
                    with self.assertRaisesRegex(Refused, expected):
                        evidence.inspect_npm(inputs, {'version': 3, 'testScript': SCRIPT})
                    continue
                else:
                    self.archives['store'] = tar_bytes([directory('dependency-store'), directory('dependency-store/blobs'),
                        regular('dependency-store/index.json', index), regular('dependency-store/blobs/unexpected.tgz', b'bytes')])
                with self.assertRaisesRegex(Refused, expected):
                    self.local(platform_build=True)

    def args(self):
        self.plan_path.write_text(canonical(self.plan))
        return (self.plan_path, sha256(self.plan_path.read_bytes()).hexdigest(), CONTROL,
                self.profile_path, self.profile_pin, self.entry_pin)

    def local(self, *, platform_build=False):
        archive_directory = self.root / 'archives'
        archive_directory.mkdir(exist_ok=True)
        for role, data in self.archives.items():
            (archive_directory / (role + '.tar')).write_bytes(data)
        return evidence.verify_local_image_archives(*self.args(), archive_directory, platform_build=platform_build)

    def acquire(self, *, platform_build=False):
        engine = docker_engine.DockerEngine(ENDPOINT, self.request)
        return evidence._acquire(evidence._reviewed_inputs(*self.args(), platform_build=platform_build),
                                 self.entry_pin, 'e' * 64,
                                 self.acquisition, self.journal, engine, self.response, synthetic=True)

    def journal_entry(self, name):
        data = json.loads((self.journal / 'effects.json').read_bytes())
        self.assertTrue(data['synthetic'])
        return data['operations']['image-readback:' + name]

    def request(self, endpoint, method, url, body, timeout):
        self.assertEqual((endpoint, timeout), (ENDPOINT, 5))
        self.calls.append((method, url))
        route = url.removeprefix('/v1.43')
        code, result = 200, None
        if route == f'/images/{IMAGE}/json':
            labels = {'org.aim-ci.source-commit': self.plan['target']['commit'],
                      'org.aim-ci.source-tree': self.plan['target']['tree'],
                      'org.aim-ci.runtime': self.plan['runtime']['sha256'],
                      'org.aim-ci.dependencies': self.profile['dependencyStoreSha256']}
            result = {'Id': self.image_id, 'Os': 'linux', 'Architecture': self.architecture,
                      'Config': {'Labels': labels, 'Env': ['PATH=/usr/bin']}}
        elif method == 'POST' and route.startswith('/containers/create?'):
            self.assertEqual(self.journal_entry('create')['state'], 'pending')
            self.assertEqual(self.journal_entry('create')['intent']['readPaths'], self.read_paths)
            self.name = parse_qs(urlsplit(url).query)['name'][0]
            self.payload = json.loads(body)
            self.assertEqual(self.payload['Entrypoint'], ['/bin/false'])
            self.assertEqual(self.payload['HostConfig']['NetworkMode'], 'none')
            self.assertTrue(self.payload['HostConfig']['ReadonlyRootfs'])
            self.assertNotIn('Binds', self.payload['HostConfig'])
            self.assertNotIn('Mounts', self.payload['HostConfig'])
            self.assertEqual(self.payload['Labels']['org.aim-ci.acquisition'], self.acquisition)
            self.exists = True
            if self.lose_create:
                raise OSError('lost create acknowledgement')
            code, result = 201, {'Id': CONTAINER}
        elif method == 'GET' and route in (f'/containers/{CONTAINER}/json', f'/containers/{self.name}/json'):
            if not self.exists:
                return 404, b''
            config = {key: value for key, value in self.payload.items() if key != 'HostConfig'}
            state = dict.fromkeys(('Running', 'Paused', 'Restarting', 'Dead', 'OOMKilled'), False)
            state.update(Status='created', Pid=0, StartedAt=self.started_at, FinishedAt=ZERO)
            result = {'Id': CONTAINER, 'Name': '/' + self.name, 'Image': IMAGE, 'Config': config,
                      'HostConfig': {**self.payload['HostConfig'], **self.host_overrides},
                      'State': state, 'Mounts': self.mounts,
                      'Created': '2026-11-07T00:00:01.000000001Z'}
        elif method == 'DELETE' and route == f'/containers/{CONTAINER}?force=false&v=false':
            self.assertEqual(self.journal_entry('remove')['state'], 'pending')
            self.exists = not self.remove_takes_effect
            if self.lose_remove:
                raise OSError('lost cleanup acknowledgement')
            return 204, b''
        else:
            self.fail(f'Unexpected Docker request {method} {url}')
        return code, json.dumps(result).encode()

    @contextmanager
    def response(self, endpoint, method, url, body, timeout):
        self.assertEqual((endpoint, method, body, timeout), (ENDPOINT, 'GET', None, 10))
        self.assertEqual(urlsplit(url).path, f'/v1.43/containers/{CONTAINER}/archive')
        path = parse_qs(urlsplit(url).query)['path'][0]
        role = next(key for key, value in self.read_paths.items() if path == value)
        self.calls.append((method, url))
        self.assertTrue(self.exists)
        effect = self.journal_entry('copy-and-verify')
        self.assertEqual(effect['state'], 'pending')
        self.assertEqual(effect['intent']['containerId'], CONTAINER)
        self.assertEqual(effect['intent']['readPaths'][role], path)
        if self.lose_copy:
            raise OSError('lost archive response')
        with ArchiveResponse(self.archives[role]) as stream:
            yield stream
        if self.change_after_copies and role == 'store':
            self.started_at = '2026-11-07T00:00:02Z'

    def test_local_bytes_match_independent_git_tree_and_lock_but_never_prove_image_origin(self):
        result = self.local()
        self.assertEqual(result['provenance'], {'kind': 'local-archives', 'imageOrigin': 'unverified'})
        self.assertEqual(result['binding']['sourceTree'], self.plan['target']['tree'])
        self.assertEqual(result['measurements']['sourceFileCount'], 4)
        self.assertEqual(result['measurements']['dependencyTarballs'], {'packageCount': 1, 'tarballCount': 1})
        self.assertFalse(result['executionAuthorized'])
        self.assertFalse(result['productAccepted'])

    def test_bytes_refuse_stale_tools_entrypoint_source_lock_and_unsafe_tar(self):
        cases = (
            ('node', [regular('node', b'wrong', 0o755)], 'node bytes'),
            ('npm', [regular('npm-cli.js', b'wrong')], 'npm bytes'),
            ('entrypoint', [regular('run-recipe', b'wrong', 0o755)], 'entrypoint bytes'),
            ('commit', [regular('source.commit', b'tree wrong')], 'pinned Git commit and tree'),
            ('source', [*self.source_rows, regular('source/extra.js', b'')], 'pinned Git commit and tree'),
            ('source', [*self.source_rows[:-2], regular('source/package-lock.json', b'{}'),
                        self.source_rows[-1]], 'pinned Git commit and tree'),
            ('node', [('node', 0o755, tarfile.SYMTYPE, b'')], 'unsafe image readback member'),
            ('node', [('node', 0o755, tarfile.XHDTYPE, b'')], 'unsafe image readback member'),
            ('node', [regular('node', b'a'), regular('node', b'b')], 'unsafe image readback member'),
            ('source', [*self.source_rows, regular('source/../elsewhere', b'a')], 'unsafe image readback member'),
        )
        original = self.archives.copy()
        for role, rows, reason in cases:
            with self.subTest(role=role, reason=reason):
                self.archives = original | {role: tar_bytes(rows)}
                with self.assertRaisesRegex(Refused, reason):
                    self.local()
        self.archives = original | {'node': original['node'] + b'not tar padding'}
        with self.assertRaisesRegex(Refused, 'Unexpected data after'):
            self.local()

    def test_platform_archives_bind_helper_and_store_without_granting_authority(self):
        self.build(platform=True)
        result = self.local(platform_build=True)
        self.assertEqual(result['measurements']['dependencyTarballs']['kind'], PLATFORM_STORE)
        self.assertFalse(result['measurements']['dependencyTarballs']['fullLockVerified'])
        self.assertEqual(result['measurements']['entrypointSha256'], self.entry_pin)
        self.assertEqual(result['provenance']['imageOrigin'], 'unverified')
        self.assertFalse(result['executionAuthorized'])

    def test_browser_inventory_requires_supported_platform_revision_and_private_network(self):
        runtime, _ = browser_fixture()
        validate_browser_runtime(runtime)
        variants = [
            {'platform': {**runtime['platform'], 'os': 'darwin'}},
            {'platform': {**runtime['platform'], 'cpu': 'x64'}},
            {'revision': '1218'}, {'libraries': {}},
            {'executable': {**runtime['executable'], 'sha256': None}},
            {'libraries': {next(iter(runtime['libraries'])): {'size': 24, 'mode': 0o644}}},
        ]
        pin = next(iter(runtime['libraries'].values()))
        for path in ('/usr/lib/../libstdc++.so.6', '/usr/lib/libstdc++;x.so.6'):
            variants.append({'libraries': {path: pin}})
        tools = self.plan['runtime']['tools']
        self.assertEqual(container_runtime_sha(tools), fingerprint(tools))
        self.assertNotEqual(container_runtime_sha(tools, runtime), container_runtime_sha(
            tools, {**runtime, 'libraries': {next(iter(runtime['libraries'])): {**pin, 'sha256': 'f'*64}}}))
        for axis, value in (('loopback', 'host'), ('externalEgress', 'allow'), ('publishedPorts', [4176]),
                            ('hostMounts', ['/tmp']), ('hostOrVmDns', 'host')):
            variants.append({'network': {**runtime['network'], axis: value}})
        for change in variants:
            with self.subTest(change=change), self.assertRaises(Refused):
                validate_browser_runtime({**runtime, **change})
        self.assertEqual(self.calls, [])

    def test_browser_inventory_measures_exact_elf_bytes_and_rejects_missing_or_linked_files(self):
        self.build(platform=True, selector=BROWSER_SELECTOR)
        runtime = self.add_browser()
        local = self.local(platform_build=True)
        self.assertEqual(local['measurements']['browserRuntime']['files'], browser_runtime_files(runtime))
        self.assertEqual(local['provenance']['imageOrigin'], 'unverified')
        self.assertFalse(local['executionAuthorized'])
        original = self.archives.copy()
        for role, path in browser_runtime_paths(runtime).items():
            name = Path(path).name
            mode = browser_runtime_files(runtime)[path]['mode']
            for rows, reason in (([], 'lacks the requested file'),
                                 ([(name, mode, tarfile.SYMTYPE, b'')], 'unsafe image readback member'),
                                 ([regular(name, synthetic_elf()[:-1]+b'x', mode)], 'bytes or mode differ')):
                with self.subTest(role=role, reason=reason), self.assertRaisesRegex(Refused, reason):
                    self.archives = original | {role: tar_bytes(rows)}
                    self.local(platform_build=True)
        for data in (synthetic_elf(62), b'Mach-O' + bytes(24)):
            self.build(platform=True, selector=BROWSER_SELECTOR)
            self.add_browser(executable_bytes=data)
            with self.assertRaisesRegex(Refused, 'not a matching ELF64'):
                self.local(platform_build=True)

    def test_browser_acquisition_binds_observed_platform_and_inert_image_paths(self):
        self.build(platform=True, selector=BROWSER_SELECTOR)
        runtime = self.add_browser()
        self.architecture = 'amd64'
        with self.assertRaisesRegex(Refused, 'OS or CPU differs'):
            self.acquire(platform_build=True)
        self.assertFalse(any(method == 'POST' for method, _ in self.calls))
        self.journal = self.root/'correct-architecture'
        self.architecture = 'arm64'
        readback = self.acquire(platform_build=True)
        self.assertEqual(readback['binding']['browserRuntimeSha256'], fingerprint(runtime))
        self.assertEqual(readback['provenance']['imageInspect']['platform'], {'os': 'linux', 'architecture': 'arm64'})
        self.assertEqual(readback['provenance']['imageOrigin'], 'unverified')
        self.assertEqual(readback['provenance']['cleanup'], 'confirmed-absent')
        self.assertEqual(readback['measurements']['browserRuntime']['files'], browser_runtime_files(runtime))
        self.assertEqual(self.journal_entry('copy-and-verify')['intent']['readPaths'], self.read_paths)

    def test_browser_readback_refuses_host_connectivity_overrides(self):
        self.build(platform=True, selector=BROWSER_SELECTOR)
        self.add_browser()
        for axis, value in (('PortBindings', {'4176/tcp': [{}]}), ('Dns', ['192.0.2.1']),
                            ('NetworkMode', 'host'), ('Binds', ['/tmp:/mnt'])):
            with self.subTest(axis=axis):
                self.journal = self.root/axis
                self.host_overrides = {axis: value}
                self.calls.clear()
                with self.assertRaisesRegex(Refused, 'connectivity|configuration differs'):
                    self.acquire(platform_build=True)
                self.assertFalse(any('/archive?' in route or method == 'DELETE' for method, route in self.calls))

    def test_large_streamed_source_contributes_to_the_exact_tree(self):
        content = b'x' * (evidence.MAX_FILE + 1)
        self.build(platform=True, source_leaf=content)
        result = self.local(platform_build=True)
        self.assertEqual(result['binding']['sourceTree'], self.plan['target']['tree'])
        self.source_rows[2] = regular('source/a/Fixture Font.woff2', b'z' + content[1:], 0o755)
        self.archives['source'] = tar_bytes(self.source_rows)
        with self.assertRaisesRegex(Refused, 'pinned Git commit and tree'):
            self.local(platform_build=True)

    def test_large_source_member_and_retained_json_limits_refuse_before_body(self):
        for name, size, reason in (
                ('asset.bin', evidence._MAX_SOURCE + 1, 'unsafe image readback member'),
                ('package-lock.json', evidence.MAX_FILE + 1, 'source input exceeds its size bound')):
            with self.subTest(name=name):
                root = tarfile.TarInfo('source')
                root.type, root.mode = tarfile.DIRTYPE, 0o755
                member = tarfile.TarInfo('source/' + name)
                member.size, member.mode = size, 0o644
                stream = io.BytesIO(root.tobuf() + member.tobuf())
                with self.assertRaisesRegex(Refused, reason):
                    evidence._archive(stream, 'source', evidence._MAX_SOURCE + 2 * 1024 * 1024,
                                      evidence._MAX_SOURCE, {'package-lock.json'}, directory=True)

    def test_source_payload_budget_is_separate_from_tar_framing(self):
        rows = [directory('source'), regular('source/asset-a', b'1234'), regular('source/asset-b', b'5678')]
        with patch.object(evidence, '_MAX_SOURCE', 7):
            with self.assertRaisesRegex(Refused, 'cumulative file size bound'):
                evidence._archive(io.BytesIO(tar_bytes(rows)), 'source', 20_000, 7, (), directory=True)

    def test_store_requires_exact_lock_coverage_and_actual_tarball_sri(self):
        self.build(store_blob=self.blob + b'changed')
        with self.assertRaisesRegex(Refused, 'tarball differs from the pinned npm lock'):
            self.local()
        self.build(uncovered=True)
        with self.assertRaisesRegex(Refused, 'does not cover the pinned npm lock'):
            self.local()
        self.build()
        self.plan['containerProfile']['dependencyStoreSha256'] = 'f' * 64
        with self.assertRaisesRegex(Refused, 'profile bytes disagree'):
            self.local()
        self.build()
        self.plan['committedInputs']['package-lock.json'] = 'f' * 64
        with self.assertRaisesRegex(Refused, 'pinned committed package-lock.json'):
            self.local()

    def test_complete_protocol_streams_exact_paths_and_deduplicates(self):
        result = self.acquire()
        provenance = result['provenance']
        self.assertEqual((provenance['kind'], provenance['imageOrigin']), ('synthetic-protocol', 'unverified'))
        self.assertEqual(provenance['before'], provenance['after'])
        self.assertEqual(provenance['imageInspect']['imageId'], IMAGE)
        self.assertEqual(provenance['cleanup'], 'confirmed-absent')
        self.assertEqual(provenance['before']['startedAt'], ZERO)
        self.assertEqual(result['measurements']['archiveSha256']['node'], sha256(self.archives['node']).hexdigest())
        self.assertEqual(sum('/archive?' in url for _, url in self.calls), 6)
        calls = self.calls[:]
        self.assertEqual(self.acquire(), result)
        self.assertEqual(self.calls, calls)

    def test_trusted_entrypoint_admission_reaches_only_the_first_transport_boundary(self):
        checkout = self.root / 'target'
        checkout.mkdir()
        self.plan['target']['checkout'] = str(checkout)
        policy = {'version': 1, 'endpoint': ENDPOINT, 'imageDigest': IMAGE,
                  'adapterSha256': sha256(Path(docker_engine.__file__).read_bytes()).hexdigest(),
                  'runtimeSha256': self.plan['runtime']['sha256'], 'controlManifestSha256': CONTROL}
        installed = {'files': {'lib/aim_ci/docker_image_evidence.py': '0' * 64}}

        def enter(candidate=policy, journal=self.journal):
            return evidence.acquire_image_evidence(
                *self.args(), trusted_docker_policy=candidate, trusted_docker_policy_pin=fingerprint(candidate),
                acquisition_id=self.acquisition, journal_path=journal)

        class TransportBoundary(Exception):
            pass

        # Substitute only installation lookup and the first transport. The plan, policy,
        # actual adapter hash and journal checks are the maintained admission path.
        with (patch.object(evidence, 'verify_installation', return_value=installed) as verify,
              patch.object(docker_engine, 'unix_request', side_effect=TransportBoundary) as transport):
            with self.assertRaisesRegex(Refused, 'policy identity or pin mismatch'):
                enter(policy | {'endpoint': 'tcp://fixture:2375'})
            with self.assertRaisesRegex(Refused, 'reviewed adapter, image and runtime'):
                enter(policy | {'adapterSha256': '0' * 64})
            with self.assertRaisesRegex(Refused, 'separate canonical journal path'):
                enter(journal=checkout / 'journal')
            self.plan['target']['checkout'] = str(checkout / '..' / checkout.name)
            with self.assertRaisesRegex(Refused, 'canonical planned checkout identity'):
                enter()
            self.plan['target']['checkout'] = str(checkout)
            self.assertFalse(self.journal.exists())
            self.assertFalse((checkout / 'journal').exists())
            transport.assert_not_called()
            with self.assertRaises(TransportBoundary):
                enter()
            transport.assert_called_once_with(ENDPOINT, 'GET', f'/v1.43/images/{IMAGE}/json', None, 5)
            self.assertEqual(verify.call_args.args, (Path(evidence.__file__).resolve().parents[2], CONTROL))

    def test_image_identity_mismatch_refuses_before_create(self):
        self.image_id = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(Refused, 'image does not match'):
            self.acquire()
        self.assertEqual(len(self.calls), 1)

    def test_readback_with_mounts_refuses_copy_and_does_not_delete_foreign_state(self):
        self.mounts = [{'Type': 'bind'}]
        with self.assertRaisesRegex(Refused, 'ownership or configuration differs'):
            self.acquire()
        self.assertFalse(any('/archive?' in url or method == 'DELETE' for method, url in self.calls))
        self.assertEqual(self.journal_entry('remove')['state'], 'unknown')

    def test_stopped_after_start_is_not_never_started_and_cleanup_remains_unknown(self):
        self.change_after_copies = True
        with self.assertRaisesRegex(Refused, 'not confirmed never started'):
            self.acquire()
        self.assertEqual(self.journal_entry('copy-and-verify')['state'], 'unknown')
        self.assertFalse(any(method == 'DELETE' for method, _ in self.calls))
        self.assertEqual(self.journal_entry('remove')['state'], 'unknown')

    def test_lost_create_is_reconciled_by_name_without_replaying_post(self):
        self.lose_create = True
        with self.assertRaisesRegex(Refused, 'Docker POST outcome is unknown'):
            self.acquire()
        self.assertEqual(self.journal_entry('create')['state'], 'unknown')
        self.assertEqual(self.acquire()['provenance']['cleanup'], 'confirmed-absent')
        self.assertEqual(sum(method == 'POST' for method, _ in self.calls), 1)
        self.assertTrue(self.journal_entry('create')['reconciled'])

    def test_lost_copy_is_never_replayed_and_owned_container_is_cleaned_up(self):
        self.lose_copy = True
        with self.assertRaisesRegex(OSError, 'lost archive response'):
            self.acquire()
        self.assertEqual(self.journal_entry('copy-and-verify')['state'], 'unknown')
        self.assertEqual(self.journal_entry('remove')['state'], 'completed')
        calls = self.calls[:]
        with self.assertRaisesRegex(Refused, 'outcome unknown'):
            self.acquire()
        self.assertEqual(self.calls, calls)

    def test_lost_cleanup_does_not_report_success_until_exact_id_absent(self):
        self.lose_remove = True
        self.remove_takes_effect = False
        with self.assertRaisesRegex(Refused, 'Docker DELETE outcome is unknown'):
            self.acquire()
        self.assertEqual(self.journal_entry('remove')['state'], 'unknown')
        with self.assertRaisesRegex(Refused, 'outcome unknown'):
            self.acquire()
        self.exists = False  # Trusted observation now confirms the exact ID is absent.
        self.assertEqual(self.acquire()['provenance']['cleanup'], 'confirmed-absent')
        self.assertEqual(sum(method == 'DELETE' for method, _ in self.calls), 1)
        self.assertEqual(sum('/archive?' in url for _, url in self.calls), 6)
