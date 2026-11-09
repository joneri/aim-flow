"""Source-only execution contracts, without Docker or project command execution."""
import copy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci.platform_build import (MAX_REPORT, capture_report, reconcile_report, report_limit,
                                   reviewed_artifact, runner_arguments, validate_report)
from aim_ci.recipe_plan import (_execution_plan, inspect_npm, planned_operations, planned_source_check,
                                reviewed_platform_operations, validate_recipe)
from openai_budget import Refused, canonical, fingerprint


class SourceCheckTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch('subprocess.Popen', side_effect=AssertionError('No project command or Docker permitted'))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.script = 'check:source'
        self.package = {'scripts': {self.script: 'node scripts/check.mjs'},
                        'dependencies': {'any-framework': '1.0.0'}}
        self.lock = {'lockfileVersion': 3, 'packages': {'': {'dependencies': self.package['dependencies']},
                                                      'node_modules/any-framework': {'version': '1.0.0'}}}
        data = {'package.json': canonical(self.package).encode(),
                'package-lock.json': canonical(self.lock).encode(), 'scripts/check.mjs': b'console.log("ok")\n'}
        self.files = {name: {'mode': 0o644, 'size': len(raw), 'sha256': sha256(raw).hexdigest()}
                      for name, raw in data.items()}
        self.recipe = {'version': 3, 'stack': 'npm-source-check', 'testScript': self.script,
                       'dependencies': {'mode': 'none'}, 'artifact': {'kind': 'none'},
                       'sourcePaths': sorted(data), 'allowedPaths': ['scripts/check.mjs'],
                       'forbiddenPaths': ['secrets'],
                       'limits': {'network': 'disabled', 'timeoutSeconds': 2, 'memoryMiB': 128, 'maxProcesses': 4}}
        self.oracle = {'kind': 'source-check-exit-v1', 'script': self.script}
        self.tools = {'node': {'path': '/usr/local/bin/node', 'sha256': 'a' * 64},
                      'npm': {'path': '/usr/local/lib/node_modules/npm/bin/npm-cli.js', 'sha256': 'b' * 64}}
        execution = {'kind': 'npm-source-check-v1', 'dependencies': 'none', 'outputs': 'none',
                     'sourcePaths': self.recipe['sourcePaths']}
        pins = {name: self.files[name]['sha256'] for name in ('package.json', 'package-lock.json')}
        self.plan = {'version': 2, 'recipeSchemaVersion': 3, 'status': 'planned-only', 'executionAuthorized': False,
                     'bindingSha256': 'c' * 64, 'control': {'manifestSha256': 'd' * 64},
                     'recipe': {'manifestSha256': 'e' * 64}, 'target': {'commit': '1' * 40, 'tree': '2' * 40},
                     'runtime': {'tools': self.tools, 'sha256': fingerprint(self.tools),
                                 'kind': 'reviewed-container-runtime', 'verification': 'declared-not-observed'},
                     'containerProfile': {'sha256': 'f' * 64, 'imageDigest': 'sha256:' + '3' * 64,
                                          'dependencyStoreSha256': '4' * 64,
                                          'verification': 'matched-separate-review-pin'},
                     'execution': execution, 'sourceFiles': self.files, 'sourceProofSha256': '5' * 64,
                     'sourceSelectionSha256': fingerprint(self.files), 'committedInputs': pins,
                     'artifact': {'kind': 'none'}, 'limits': self.recipe['limits'],
                     'testSelection': {'script': self.script, 'selectedBy': 'pinned-recipe',
                        'source': {'path': 'package.json', 'locator': 'scripts["check:source"]',
                                   'sha256': pins['package.json']},
                        'runtimeEffects': 'review-required', 'loopback': 'not-approved'},
                     'operations': planned_operations(self.tools, self.script, None, ('test',))}

    def binding(self, plan=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'plan.json'
            raw = canonical(self.plan if plan is None else plan).encode()
            path.write_bytes(raw)
            return _execution_plan(path, sha256(raw).hexdigest(), 'd' * 64)[1]

    def test_generic_recipe_needs_neither_vite_nor_build_and_never_selects_hooks(self):
        validate_recipe(self.recipe, self.oracle)
        result = inspect_npm({'package.json': self.package, 'package-lock.json': self.lock}, self.recipe)
        self.assertFalse(result['dependenciesInstalled'])
        self.assertNotIn('viteVersion', result)
        for hook in ('pre' + self.script, 'post' + self.script):
            package = copy.deepcopy(self.package)
            package['scripts'][hook] = 'node another.mjs'
            with self.assertRaisesRegex(Refused, 'lifecycle hooks'):
                inspect_npm({'package.json': package, 'package-lock.json': self.lock}, self.recipe)
        for changes in ({'stack': 'astro-special-case'}, {'dependencies': {'mode': 'install'}},
                        {'sourcePaths': list(reversed(self.recipe['sourcePaths']))},
                        {'sourcePaths': sorted([*self.recipe['sourcePaths'], '.env'])},
                        {'artifact': {'directory': 'dist'}}):
            with self.subTest(changes=changes), self.assertRaises(Refused):
                validate_recipe(self.recipe | changes, self.oracle)
        with self.assertRaises(Refused):
            validate_recipe(self.recipe, self.oracle | {'script': 'other'})
        with self.assertRaisesRegex(Refused, 'allowed edits must be included'):
            validate_recipe(self.recipe | {'allowedPaths': ['scripts/unselected.mjs']}, self.oracle)

    def test_execution_binds_proof_selection_and_one_ordered_named_command(self):
        binding = self.binding()
        self.assertEqual(binding['sourceProofSha256'], self.plan['sourceProofSha256'])
        self.assertEqual(binding['sourceSelectionSha256'], fingerprint(self.files))
        self.assertEqual(binding['executionSha256'], fingerprint(self.plan['execution']))
        self.assertEqual(reviewed_platform_operations(self.plan, False), (self.script, self.plan['operations']))
        changed = copy.deepcopy(self.plan)
        changed['operations'] += planned_operations(self.tools, self.script, None, ('build',))
        with self.assertRaisesRegex(Refused, 'exactly one'):
            reviewed_platform_operations(changed, True)
        changed = copy.deepcopy(self.plan)
        changed['sourceFiles']['scripts/check.mjs']['sha256'] = '9' * 64
        with self.assertRaisesRegex(Refused, 'source and proof pins'):
            self.binding(changed)
        changed = copy.deepcopy(self.plan)
        changed['version'] = 1
        with self.assertRaisesRegex(Refused, 'container runtime'):
            self.binding(changed)
        changed = copy.deepcopy(self.plan)
        changed['recipeSchemaVersion'] = 2
        with self.assertRaisesRegex(Refused, 'schema v3'):
            planned_source_check(changed)

    def test_large_source_selection_is_pinned_without_size_or_path_count_ceiling(self):
        for index in range(125):
            self.files[f'large-{index:03}.css'] = {'mode': 0o644, 'size': 32 * 1024 * 1024 + 1,
                                                 'sha256': '9' * 64}
        self.recipe['sourcePaths'] = sorted(self.files)
        self.plan['execution']['sourcePaths'] = sorted(self.files)
        self.plan['sourceSelectionSha256'] = fingerprint(self.files)
        self.plan['sourceProofBytes'] = 3_000_001
        validate_recipe(self.recipe, self.oracle)
        self.assertEqual(planned_source_check(self.plan)['sourcePaths'], sorted(self.files))
        self.assertEqual(self.binding()['sourceProofBytes'], 3_000_001)
        changed = copy.deepcopy(self.plan)
        changed['sourceFiles'].pop('large-124.css')
        with self.assertRaisesRegex(Refused, 'exact selected paths'):
            self.binding(changed)
        for invalid in (0, True, -1):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Refused, 'proof size'):
                self.binding(self.plan | {'sourceProofBytes': invalid})

    def test_artifact_policy_has_only_its_explicit_reviewed_size_limit(self):
        artifact = {'directory': 'dist', 'requiredFiles': ['index.html', *[f'file-{i}.css' for i in range(125)]],
                    'maximumBytes': 40 * 1024 * 1024}
        recipe = {key: value for key, value in self.recipe.items() if key not in ('sourcePaths', 'dependencies')}
        recipe.update(version=2, stack='npm-vite-static', artifact=artifact)
        oracle = {'kind': 'static-heading-v1', 'file': 'index.html', 'heading': 'Hello'}
        validate_recipe(recipe, oracle)
        plan = {'recipeSchemaVersion': 2, 'artifact': artifact | {'linksAllowed': False, 'status': 'not-built'}}
        self.assertEqual(reviewed_artifact(plan), {key: artifact[key] for key in ('requiredFiles', 'maximumBytes')})
        for invalid in (0, -1, True, 1 << 53):
            with self.subTest(invalid=invalid), self.assertRaises(Refused):
                validate_recipe(recipe | {'artifact': artifact | {'maximumBytes': invalid}}, oracle)
        for invalid in (0, 1 << 53):
            with self.subTest(invalid=invalid), self.assertRaises(Refused):
                reviewed_artifact(plan | {'artifact': plan['artifact'] | {'maximumBytes': invalid}})

    def report_fixture(self):
        binding = self.binding()
        review = {'tools': self.tools, 'artifact': {'kind': 'none'}, 'execution': self.plan['execution'],
                  'sourceFiles': self.files, 'testOperation': self.plan['operations'][0],
                  'store': {'kind': 'npm-no-install-v1', 'platform': {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'},
                            'lockPackageCount': 1, 'packageCount': 0, 'tarballCount': 0,
                            'fullLockVerified': False, 'excludedPackageCount': 1}}
        intent = {'effectId': '6' * 64, 'binding': binding, 'testScript': self.script, 'platformBuild': review,
                  'requestedControls': {'wholeContainerDeadlineSeconds': 2}}
        report = {'version': 5, 'effectId': intent['effectId'], 'binding': binding,
                  'execution': self.plan['execution'], 'testScript': self.script,
                  'runtime': {'node': 'v22.23.3', 'npm': '10.9.9', 'cacache': '19.0.1',
                              'pacote': '19.0.2', 'contentVersionRaw': 2, 'contentVersion': 2, 'glibc': '2.36'},
                  'workspaceMount': {'id': 2, 'parentId': 1, 'mountPoint': '/work', 'type': 'tmpfs',
                                     'mountOptions': ['rw', 'nosuid', 'nodev'], 'superOptions': ['rw'], 'executable': True},
                  'materialized': None, 'install': None, 'installedPackages': None, 'buildTools': None,
                  'build': None, 'artifacts': None, 'error': None,
                  'test': {'exitCode': 0, 'signal': None, 'error': None, 'stdout': 'b2sK', 'stderr': ''}}
        return intent, report, {'exitCode': 0, 'oom': False, 'deadlineExceeded': False}

    def test_artifact_report_above_thirty_megabytes_roundtrips_with_reviewed_budget(self):
        import base64
        intent, report, terminal = self.report_fixture()
        review = intent['platformBuild']
        review.pop('execution')
        review.pop('sourceFiles')
        review['store'].update(kind='npm-v3-platform-tarballs-v1', tarballCount=1, packageCount=1)
        data = b'x' * (24 * 1024 * 1024)
        review['artifact'] = {'requiredFiles': ['index.html'], 'maximumBytes': len(data)}
        operation = {'exitCode': 0, 'signal': None, 'error': None, 'stdout': '', 'stderr': ''}
        report.pop('execution')
        report.update(version=2, materialized=1, installedPackages=1, install=operation, build=operation,
                      buildTools={'tsc': None, 'vite': None}, artifacts={'index.html': {
                          'size': len(data), 'sha256': sha256(data).hexdigest(),
                          'data': base64.b64encode(data).decode()}})
        raw = canonical(report).encode()
        self.assertGreater(len(raw), MAX_REPORT)
        self.assertLess(len(raw), report_limit(intent))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = capture_report(root, lambda: raw, intent, terminal)
            self.assertEqual(result['status'], 'build-and-test-passed')
            self.assertEqual(result['artifacts']['bytes'], len(data))
            self.assertEqual(reconcile_report(root, intent, terminal), result)
            smaller = copy.deepcopy(intent)
            smaller['platformBuild']['artifact']['maximumBytes'] -= 1
            refused = reconcile_report(root, smaller, terminal)
            self.assertEqual(refused['status'], 'unverified')
            self.assertRegex(refused['reason'], 'Oversized|oversized')
        small = copy.deepcopy(intent)
        small['platformBuild']['artifact']['maximumBytes'] = 1
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(Refused, 'oversized'):
            capture_report(Path(directory).resolve(), lambda: b'x' * (report_limit(small) + 1), small, terminal)

    def test_source_check_reports_no_install_build_or_artifact_success(self):
        intent, report, terminal = self.report_fixture()
        result = validate_report(canonical(report).encode(), intent, terminal)
        self.assertEqual(result['status'], 'source-check-passed')
        self.assertIsNone(result['install'])
        self.assertIsNone(result['build'])
        self.assertIsNone(result['artifacts'])
        import base64
        config = json.loads(base64.b64decode(runner_arguments(intent)[1]))
        self.assertEqual(config['sourceFiles'], self.files)
        self.assertEqual(config['testScript'], self.script)
        for name in ('materialized', 'installedPackages'):
            with self.subTest(name=name), self.assertRaises(Refused):
                validate_report(canonical(report | {name: 0}).encode(), intent, terminal)
        for terminal_change in ({'exitCode': 1}, {'oom': True}, {'deadlineExceeded': True}, {'deadlineExceeded': None}):
            with self.subTest(terminal=terminal_change), self.assertRaises(Refused):
                validate_report(canonical(report).encode(), intent, terminal | terminal_change)
        failed = report | {'test': report['test'] | {'exitCode': 7}}
        self.assertEqual(validate_report(canonical(failed).encode(), intent, terminal | {'exitCode': 1})['status'],
                         'source-check-failed')
        with self.assertRaises(Refused):
            validate_report(canonical(report | {'test': None}).encode(), intent, terminal)


if __name__ == '__main__':
    unittest.main()
