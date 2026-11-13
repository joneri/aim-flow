"""Offline Flow controller contracts: no project command or provider is run."""
import copy
import json
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci import project_plan_derivation as plan
from aim_ci.owned_paths import Refused
import test_project_validation as fixtures


class SyntaxTests(unittest.TestCase):
    def test_literals(self):
        self.assertEqual(plan.parse_commands('npm ci && npm run ci:gate', '.'), [
            {'root': '.', 'operation': 'ci', 'script': None},
            {'root': '.', 'operation': 'run', 'script': 'ci:gate'}])

    def test_no_shell_salvage(self):
        for value in ('npm run test; touch pwn', 'npm run $(id)', 'npm run test -- --flag',
                      'node --version && npm ci', 'npm ci\nnpm run test', 'npm run -x',
                      'npm ci && curl example.com', 'npm run test || true', 'npm ci&&npm run test'):
            self.assertIsNone(plan.parse_commands(value, '.'))

    def test_probe_decomposition_preserves_needs(self):
        stages, probes = plan.parse_step('node --version && npm --version && git --version && command -v which zip && zip -v && npm ci', '.')
        self.assertEqual(stages, [{'root': '.', 'operation': 'ci', 'script': None}])
        self.assertEqual([row['tool'] for row in probes], ['node', 'npm', 'git', 'which', 'zip', 'zip'])
        for text in ('command -v $(evil) && npm ci', 'zip -v > /tmp/out && npm ci', 'echo safe && npm ci'):
            self.assertIsNone(plan.parse_step(text, '.')[0])

    def test_ci_locality_is_not_the_area_label(self):
        for command in ('npm ci', 'npm run arbitrary-gate', 'node --version && npm run gate'):
            step = {'area': 'ci', 'command': command, 'workingDirectory': '.'}
            self.assertTrue(plan.local_command_candidate(step))
            self.assertFalse(plan.delivery_obligation(step))
        for step in ({'area': 'ci', 'command': 'gh run watch'},
                     {'area': 'deployment', 'command': 'npm run release'}):
            self.assertFalse(plan.local_command_candidate(step))
            self.assertTrue(plan.delivery_obligation(step))

    def test_typed_engine_matching(self):
        self.assertTrue(plan.version_satisfies('24.22.2', '>=24 <25'))
        self.assertTrue(plan.version_satisfies('24.22.2', '>=22.12.0'))
        self.assertFalse(plan.version_satisfies('22.12.0', '>=24'))
        for value in ('>=24 || <20', '>=24; evil', 'latest', '*', ''):
            self.assertFalse(plan.version_satisfies('24.22.2', value))
        self.assertFalse(plan.version_satisfies('24.22.2-beta', '>=24'))

    def test_npm_jobs_require_measured_npm_availability(self):
        row = {'kind': 'node-runtime', 'capabilities': {'nodeVersion': '24.22.2',
            'platform': 'linux', 'architecture': 'arm64'}}
        requirements = ['npm named scripts with normal lifecycle hooks', 'node >=24']
        self.assertFalse(plan.matches_need(row, 'node-runtime', requirements))
        row['capabilities']['npmVersion'] = '11.19.1'
        self.assertTrue(plan.matches_need(row, 'node-runtime', requirements))
        row['capabilities']['npmVersion'] = 'unknown'
        self.assertFalse(plan.matches_need(row, 'node-runtime', requirements))

    def test_catalog_does_not_accept_authority(self):
        value = {'format': plan.CATALOG, 'schemaVersion': 1, 'resources': []}
        self.assertEqual(plan.resource_catalog(value), value)
        with self.assertRaises(Refused):
            plan.resource_catalog({**value, 'executionAuthorized': True})


class DerivationTests(fixtures.ProjectValidationTests):
    # Reuse the real isolated Git/installed controller fixture, not its tests.
    def derive(self, command='npm run ci:gate', catalog=None):
        instance = copy.deepcopy(self.instance)
        steps = instance['assessment']['steps']
        step = copy.deepcopy(steps[0]); step.update(id='gate', area='tests', command=command,
                                                   workingDirectory='.', dependsOn=[])
        instance['assessment']['steps'] = [step]
        return plan.derive_from_instance(self.current, instance, fixtures.sha(self.instance_path),
                 catalog or {'format': plan.CATALOG, 'schemaVersion': 1, 'resources': []})

    def test_installed_cli_round_trip(self):
        catalog = self.case / 'catalog.json'
        catalog.write_text(json.dumps({'format': plan.CATALOG, 'schemaVersion': 1, 'resources': []}))
        output = self.case / 'derived.json'
        python = Path(sys.executable).resolve()
        args = [str(python), '-IBS', str(self.current / 'aim-flow.py'), 'derive-project-validation',
                '--manifest-sha256', self.current_pin, '--instance', str(self.instance_path),
                '--instance-sha256', fixtures.sha(self.instance_path), '--instance-controller', str(self.prior),
                '--instance-controller-manifest-sha256', self.release['manifestSha256'],
                '--python', str(python), '--python-sha256', fixtures.sha(python),
                '--resource-catalog', str(catalog), '--resource-catalog-sha256', fixtures.sha(catalog),
                '--output', str(output)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(output.read_text())['executionAuthorized'])
        again = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)

    def test_derived_actual_script(self):
        result = self.derive()
        self.assertFalse(result['ready']); self.assertFalse(result['executionAuthorized'])
        declaration = result['declaration']
        self.assertEqual(declaration['jobs'][0]['stages'][0]['script'], 'ci:gate')
        self.assertEqual(declaration['packageRoots'], [{'directory': '.', 'lockfile': 'package-lock.json'}])
        self.assertTrue(all(row['status'] == 'missing' for row in result['resourceNeeds']))
        self.assertIn('package.json', declaration['sourcePaths'])

    def test_ci_area_derives_local_gate_without_losing_dependencies(self):
        instance = copy.deepcopy(self.instance)
        first = copy.deepcopy(instance['assessment']['steps'][0])
        first.update(id='install', area='installation', command='npm ci',
                     workingDirectory='.', dependsOn=[])
        second = copy.deepcopy(first)
        second.update(id='gate', area='ci', command='npm run ci:gate', dependsOn=['install'])
        instance['assessment']['steps'] = [first, second]
        result = plan.derive_from_instance(self.current, instance, fixtures.sha(self.instance_path),
                    {'format': plan.CATALOG, 'schemaVersion': 1, 'resources': []})
        self.assertEqual(result['unsupportedSteps'], [])
        self.assertEqual(result['declaration']['jobs'][1]['needs'], ['install'])
        self.assertEqual(result['declaration']['jobs'][1]['stages'][0]['script'], 'ci:gate')
        self.assertFalse(result['executionAuthorized'])

    def test_unknown_script_stops(self):
        result = self.derive('npm run nonexistent')
        self.assertIsNone(result['declaration'])
        self.assertIn('absent', result['unsupportedSteps'][0]['reason'])

    def test_whole_unsupported_command_retained(self):
        result = self.derive('echo unknown && npm ci')
        self.assertIsNone(result['declaration'])
        self.assertEqual(result['unsupportedSteps'][0]['command'], 'echo unknown && npm ci')

    def test_unsupported_predecessor_remains_blocking_need(self):
        instance = copy.deepcopy(self.instance)
        first = copy.deepcopy(instance['assessment']['steps'][0])
        first.update(id='inspect', area='tests', command='unknown-tool --check', workingDirectory='.', dependsOn=[])
        second = copy.deepcopy(first)
        second.update(id='test', command='npm run ci:gate', dependsOn=['inspect'])
        instance['assessment']['steps'] = [first, second]
        result = plan.derive_from_instance(self.current, instance, fixtures.sha(self.instance_path),
                    {'format': plan.CATALOG, 'schemaVersion': 1, 'resources': []})
        job = result['declaration']['jobs'][0]
        self.assertIn('Unresolved assessment predecessor: inspect', job['prerequisites'])
        self.assertTrue(any(row['kind'] == 'assessment-predecessor' and row['status'] == 'unresolved'
                            for row in result['resourceNeeds']))

    def test_dirty_manifest_not_used(self):
        (self.checkout / 'package.json').write_text('{"scripts":{"evil":"evil"}}')
        self.assertEqual(self.derive()['declaration']['jobs'][0]['stages'][0]['script'], 'ci:gate')

    def test_forged_cited_source_refused(self):
        instance = copy.deepcopy(self.instance)
        instance['packet']['sources'][0]['text'] += 'forged'
        with self.assertRaises(Refused):
            plan.derive_from_instance(self.current, instance, fixtures.sha(self.instance_path),
                        {'format': plan.CATALOG, 'schemaVersion': 1, 'resources': []})

    def test_catalog_claim_not_execution_or_provisioning(self):
        result = self.derive()
        wanted = result['resourceNeeds'][0]['requirements']
        catalog = {'format': plan.CATALOG, 'schemaVersion': 1, 'resources': [
            {'id': 'node', 'kind': 'node-runtime', 'identity': 'image sha256:' + 'a' * 64,
             'requirements': wanted, 'qualification': 'separate qualification record',
             'capabilities': {'nodeVersion': '24.22.2', 'npmVersion': '11.19.1', 'platform': 'linux', 'architecture': 'arm64'}}]}
        result = self.derive(catalog=catalog)
        self.assertTrue(any(row['status'] == 'catalog-candidate-review-required' for row in result['resourceNeeds']))
        self.assertFalse(result['executionAuthorized'])


# Avoid running inherited validation tests twice.
for name in dir(fixtures.ProjectValidationTests):
    if name.startswith('test_') and name not in DerivationTests.__dict__:
        setattr(DerivationTests, name, None)

if __name__ == '__main__':
    unittest.main()
