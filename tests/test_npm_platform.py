"""Pure platform-store and artifact contract regressions."""
from base64 import b64encode
from hashlib import sha256, sha512
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci.docker_image_evidence import _check_store
from aim_ci.npm_platform import FULL_STORE, PLATFORM_STORE, platform_partition, project_path
from aim_ci.platform_build import artifact_inventory
from openai_budget import Refused, canonical


def metadata(data):
    return {'size': len(data), 'mode': 0o644, 'sha256': sha256(data).hexdigest(), 'sha512': sha512(data).digest(),
            'data': data}


class NpmPlatformTests(unittest.TestCase):
    def setUp(self):
        self.target = {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'}
        self.blobs = {name: metadata(name.encode()) for name in
                      ('node_modules/vite', 'node_modules/@native/linux-arm64', 'node_modules/@native/darwin-arm64')}
        self.lock = {'packages': {'': {'optionalDependencies': {
            '@native/darwin-arm64': '1.0.0', '@native/linux-arm64': '1.0.0'}}}}
        self.entries = {}
        for name, row in self.blobs.items():
            self.entries[name] = {'version': '1.0.0', 'resolved': 'https://registry.invalid/fixture.tgz',
                                  'integrity': 'sha512-' + b64encode(row['sha512']).decode()}
        self.entries['node_modules/@native/linux-arm64'].update(optional=True, os=['linux'], cpu=['arm64'],
                                                                  libc=['!musl'])
        self.entries['node_modules/@native/darwin-arm64'].update(optional=True, os='darwin', cpu=['arm64'])
        self.lock['packages'].update(self.entries)
        self.lock_pin = '1' * 64

    def store(self, kind=PLATFORM_STORE):
        mapping = {name: {'sha256': row['sha256'], 'integrity': self.entries[name]['integrity']}
                   for name, row in self.blobs.items()}
        if kind == PLATFORM_STORE:
            included, excluded = platform_partition(self.entries, self.target, root=self.lock['packages'][''])
            index = {'kind': kind, 'lockSha256': self.lock_pin, 'platform': self.target,
                     'included': {name: mapping[name] for name in included}, 'excluded': excluded}
        else:
            included = set(mapping)
            index = {'kind': kind, 'lockSha256': self.lock_pin, 'packages': mapping}
        files = {'blobs/' + self.blobs[name]['sha256'] + '.tgz': self.blobs[name].copy() for name in included}
        return index, files

    def check(self, index, files):
        encoded = metadata(canonical(index).encode())
        binding = {'committedInputs': {'package-lock.json': self.lock_pin}, 'dependencyStoreSha256': encoded['sha256']}
        return _check_store({'index.json': encoded, **files}, self.lock, binding)

    def test_platform_store_is_an_exact_subset_and_full_store_remains_full(self):
        platform = self.check(*self.store())
        self.assertEqual((platform['lockPackageCount'], platform['packageCount'], platform['excludedPackageCount']),
                         (3, 2, 1))
        self.assertFalse(platform['fullLockVerified'])
        self.assertEqual(self.check(*self.store(FULL_STORE)), {'packageCount': 3, 'tarballCount': 3})
        index, files = self.store(FULL_STORE)
        del files[next(iter(files))]
        with self.assertRaisesRegex(Refused, 'tarball differs'):
            self.check(index, files)

    def test_wrong_target_or_tampered_exclusion_cannot_reuse_the_partition(self):
        index, files = self.store()
        with self.assertRaisesRegex(Refused, 'exactly partition'):
            self.check({**index, 'platform': {**self.target, 'cpu': 'x64'}}, files)
        with self.assertRaisesRegex(Refused, 'exactly partition'):
            self.check({**index, 'excluded': {}}, files)
        with self.assertRaisesRegex(Refused, 'Unsupported npm target'):
            self.check({**index, 'platform': {**self.target, 'libc': 'musl'}}, files)

    def test_prunable_leaf_refuses_even_with_a_pinned_legacy_platform_index(self):
        index, files = self.store()
        self.entries['node_modules/@native/darwin-arm64']['dependencies'] = {'vite': '1.0.0'}
        self.entries['node_modules/vite']['optional'] = True
        with self.assertRaisesRegex(Refused, 'no proven nonoptional path from the root'):
            self.check(index, files)
        self.assertEqual(self.check(*self.store(FULL_STORE)), {'packageCount': 3, 'tarballCount': 3})

    def test_shared_leaf_keeps_its_required_root_or_applicable_parent_path(self):
        root = self.lock['packages']['']
        self.entries['node_modules/@native/darwin-arm64']['dependencies'] = {'vite': '1.0.0'}
        with self.subTest(path='root'):
            root['devDependencies'] = {'vite': '^1.0.0'}
            self.assertEqual(self.check(*self.store())['packageCount'], 2)
        with self.subTest(path='applicable shared parent'):
            root['devDependencies'] = {'@native/linux-arm64': '1.0.0'}
            del root['optionalDependencies']['@native/linux-arm64']
            parent = self.entries['node_modules/@native/linux-arm64']
            parent.pop('optional')
            parent['dependencies'] = {'vite': '1.0.0'}
            self.assertEqual(self.check(*self.store())['packageCount'], 2)

    def test_overlapping_root_dev_scope_does_not_prove_shared_leaf(self):
        root = self.lock['packages']['']
        self.entries['node_modules/@native/darwin-arm64']['dependencies'] = {'vite': '1.0.0'}
        for scope in ('dependencies', 'optionalDependencies', 'peerDependencies'):
            overlapping = {**root, 'devDependencies': {'vite': '1.0.0'},
                           scope: {**root.get(scope, {}), 'vite': '2.0.0'}}
            with self.subTest(scope=scope), self.assertRaisesRegex(Refused, 'overlapping root development'):
                platform_partition(self.entries, self.target, root=overlapping)

    def test_unknown_optional_graph_refuses_instead_of_guessing_locators(self):
        root = self.lock['packages']['']
        parent = 'node_modules/@native/darwin-arm64'
        leaf = self.entries['node_modules/vite']
        for edge, child, reason in (
            ({'dependencies': {'missing': '1.0.0'}}, {}, 'exact locator'),
            ({'dependencies': {'vite': '1.0.0'}}, {parent + '/node_modules/vite': leaf}, 'nested'),
            ({'optionalDependencies': {'vite': '1.0.0'}}, {}, 'optional or peer children'),
            ({'dependencies': ['vite']}, {}, 'invalid dependency declaration'),
        ):
            with self.subTest(edge=edge, child=child), self.assertRaisesRegex(Refused, reason):
                platform_partition({**self.entries, parent: {**self.entries[parent], **edge}, **child},
                                   self.target, root=root)
        with self.assertRaisesRegex(Refused, 'possible nonoptional referrer'):
            platform_partition(self.entries, self.target,
                               root={'dependencies': {'@native/darwin-arm64': '1.0.0'}})
        with self.assertRaisesRegex(Refused, 'missing optional referrer'):
            platform_partition(self.entries, self.target, root={})

    def test_nested_off_platform_leaf_uses_the_nearest_locked_optional_locator(self):
        parent = 'node_modules/vite'
        top = 'node_modules/@native/darwin-arm64'
        nested = parent + '/' + top
        self.entries[parent]['optionalDependencies'] = {'@native/darwin-arm64': '1.0.1'}
        self.entries[nested] = {**self.entries[top], 'version': '1.0.1'}
        root = self.lock['packages']['']
        root['optionalDependencies']['@native/darwin-arm64'] = '~1.0.0'
        included, excluded = platform_partition(self.entries, self.target, root=root)
        self.assertEqual(included, {parent, 'node_modules/@native/linux-arm64'})
        self.assertEqual(excluded, {top: ['os'], nested: ['os']})
        del self.entries[parent]['optionalDependencies']
        with self.assertRaisesRegex(Refused, 'missing optional referrer'):
            platform_partition(self.entries, self.target, root=root)

    def test_missing_or_mismatched_required_and_applicable_native_bytes_refuse(self):
        for name in ('node_modules/vite', 'node_modules/@native/linux-arm64'):
            with self.subTest(name=name):
                index, files = self.store()
                path = 'blobs/' + self.blobs[name]['sha256'] + '.tgz'
                missing = {key: row for key, row in files.items() if key != path}
                with self.assertRaisesRegex(Refused, 'applicable npm platform tarball'):
                    self.check(index, missing)
                with self.assertRaisesRegex(Refused, 'applicable npm platform tarball'):
                    self.check(index, {**files, path: {**files[path], 'sha512': b'tampered'}})
                index['included'][name]['integrity'] = 'sha512-' + b64encode(b'x' * 64).decode()
                with self.assertRaisesRegex(Refused, 'integrity differs'):
                    self.check(index, files)

    def test_lock_refusals_cover_malformed_required_unsupported_and_non_registry_entries(self):
        name = 'node_modules/vite'
        good = self.entries[name]
        for entry, expected in ((None, 'Invalid npm platform lock entry'),
                                ({**good, 'cpu': ['!arm64']}, 'Required npm dependency'),
                                ({**good, 'cpu': ['any', 'arm64']}, 'Unsupported npm platform selector'),
                                ({**good, 'cpu': []}, 'Unsupported npm platform selector'),
                                ({**good, 'link': True}, 'Unsupported npm platform dependency'),
                                ({**good, 'resolved': 'file:somewhere'}, 'Unsupported npm platform dependency'),
                                ({**good, 'integrity': 'sha1-different'}, 'Unsupported npm platform dependency')):
            with self.subTest(entry=entry), self.assertRaisesRegex(Refused, expected):
                platform_partition({**self.entries, name: entry}, self.target)

    def test_artifacts_allow_font_spaces_and_reject_escapes_tamper_and_overflow(self):
        data = b'example'
        row = {'data': b64encode(data).decode(), 'sha256': sha256(data).hexdigest(), 'size': len(data)}
        font = 'assets/Receipt Text-Bold.woff2'
        files = {'index.html': row, font: row}
        policy = {'requiredFiles': ['index.html', font], 'maximumBytes': 14}
        self.assertEqual(artifact_inventory(files, policy)['bytes'], 14)
        for name in ('../outside', '/absolute', 'a//b', 'a/b\\c', 'a/ b', 'a/..', 'a/./b', 'a/ñ'):
            self.assertFalse(project_path(name), name)
        with self.assertRaisesRegex(Refused, 'oversized|Oversized'):
            artifact_inventory(files, {**policy, 'maximumBytes': 13})
        with self.assertRaisesRegex(Refused, 'declared bytes'):
            artifact_inventory({**files, font: {**row, 'sha256': '0' * 64}}, policy)
        with self.assertRaisesRegex(Refused, 'overlapping'):
            artifact_inventory({**files, font + '/child': row}, {**policy, 'maximumBytes': 100})
