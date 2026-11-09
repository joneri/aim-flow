"""Exercise recipe admission with a trusted, in-process synthetic stub."""
import copy
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci.recipe_plan import check_recipe_execution, _simulate_recipe_test
from openai_budget import Refused


CONTROL = 'a' * 64
CAPABILITIES = ('network', 'workspaceWrites', 'readIsolation', 'processTreeTimeout',
                'aggregateMemory', 'processCount', 'dependencyStore')


class SyntheticBackend:
    def __init__(self):
        self.coverage = dict.fromkeys(CAPABILITIES, 'simulated')
        self.calls = 0
        self.outcome = 7

    def capabilities(self):
        return self.coverage

    def simulate_test(self):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class RecipeExecutionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        guard = patch('subprocess.Popen', side_effect=AssertionError('No subprocess in this test suite'))
        guard.start()
        self.addCleanup(guard.stop)
        self.root = Path(scratch.name).resolve()
        self.plan_path = self.root / 'plan.json'
        self.authorization_path = self.root / 'test-authorization.json'
        self.journal = self.root / 'journal'
        self.backend = SyntheticBackend()
        self.plan = {
            'version': 1, 'recipeSchemaVersion': 2, 'status': 'planned-only', 'executionAuthorized': False,
            'bindingSha256': 'b' * 64, 'control': {'manifestSha256': CONTROL},
            'recipe': {'manifestSha256': 'c' * 64},
            'target': {'repository': 'https://fixture.invalid/aim-ci/recipe-test.git',
                       'commit': 'd' * 40, 'tree': 'e' * 40},
            'committedInputs': {'package.json': 'f' * 64, 'package-lock.json': '1' * 64},
            'runtime': {'sha256': '2' * 64},
            'limits': {'network': 'disabled', 'timeoutSeconds': 2, 'memoryMiB': 128, 'maxProcesses': 4},
            'review': {'kind': 'synthetic-fixture'}, 'approval': {'kind': 'synthetic-fixture'},
        }
        self.plan_pin = self.write(self.plan_path, self.plan)
        self.current = copy.deepcopy(self.plan)
        self.authorization = {'version': 1, 'kind': 'synthetic-test-authorization', 'effect': 'recipe:test',
                              'journal': str(self.journal), 'binding': {
                                  'planSha256': self.plan_pin, 'planningBindingSha256': 'b' * 64,
                                  'controlManifestSha256': CONTROL, 'recipeManifestSha256': 'c' * 64,
                                  'sourceCommit': 'd' * 40, 'sourceTree': 'e' * 40,
                                  'committedInputs': self.plan['committedInputs'], 'runtimeSha256': '2' * 64}}
        self.authorization_pin = self.write(self.authorization_path, self.authorization)

    @staticmethod
    def write(path, data):
        encoded = json.dumps(data, sort_keys=True).encode()
        path.write_bytes(encoded)
        return hashlib.sha256(encoded).hexdigest()

    def simulate(self, **overrides):
        args = {'current_plan': self.current, 'authorization_path': self.authorization_path,
                'authorization_pin': self.authorization_pin, 'journal_path': self.journal,
                'test_backend': self.backend}
        return _simulate_recipe_test(self.plan_path, self.plan_pin, CONTROL, **(args | overrides))

    def assert_no_dispatch(self):
        self.assertEqual(self.backend.calls, 0)
        self.assertFalse(self.journal.exists())

    def test_native_cli_blocks_before_dispatch_even_if_plan_claims_capabilities(self):
        self.plan['capabilities'] = dict.fromkeys(CAPABILITIES, 'qualified')
        pin = self.write(self.plan_path, self.plan)
        entry = runpy.run_path(str(ROOT / 'aim-ci-control.py'))
        argv = ['aim-ci-control.py', 'check-recipe-execution', '--manifest-sha256', CONTROL,
                '--plan', str(self.plan_path), '--plan-sha256', pin]
        output = io.StringIO()
        with patch.object(sys, 'argv', argv), redirect_stdout(output), patch(
                'aim_ci.control_package.verify_installation') as verify:
            self.assertEqual(entry['main'](), 3)
        verify.assert_called_once_with(ROOT, CONTROL)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['status'], 'blocked-before-dispatch')
        self.assertEqual(receipt['binding']['planSha256'], pin)
        self.assertIn('aggregateMemory', receipt['unqualified'])
        self.assertIn('processCount', receipt['unqualified'])
        self.assertIn('readIsolation', receipt['unqualified'])
        self.assertIsNone(receipt['exitCode'])
        self.assertFalse(receipt['processStarted'])
        self.assertEqual(receipt['observedEffects'], [])
        self.assert_no_dispatch()

    def test_changed_plan_bytes_or_current_committed_inputs_refused(self):
        self.current['committedInputs']['package.json'] = '3' * 64
        with self.assertRaisesRegex(Refused, 'input identity differs'):
            self.simulate()
        self.plan['limits']['memoryMiB'] = 256
        self.write(self.plan_path, self.plan)
        with self.assertRaisesRegex(Refused, 'Execution plan SHA-256 mismatch'):
            self.simulate()
        self.assert_no_dispatch()

    def test_changed_control_pin_and_network_policy_refused(self):
        with self.assertRaisesRegex(Refused, 'pinned control'):
            check_recipe_execution(self.plan_path, self.plan_pin, '9' * 64)
        self.plan['limits']['network'] = 'enabled'
        pin = self.write(self.plan_path, self.plan)
        with self.assertRaisesRegex(Refused, 'network limits'):
            check_recipe_execution(self.plan_path, pin, CONTROL)
        self.assert_no_dispatch()

    def test_test_authorization_must_be_explicit_and_pin_the_journal(self):
        with self.assertRaisesRegex(Refused, 'Invalid expected Test authorization SHA-256'):
            self.simulate(authorization_pin=None)
        invalid = self.authorization | {'kind': 'operator-record'}
        pin = self.write(self.authorization_path, invalid)
        with self.assertRaisesRegex(Refused, 'Test authorization does not bind'):
            self.simulate(authorization_pin=pin)
        invalid = self.authorization | {'binding': self.authorization['binding'] | {'runtimeSha256': '3' * 64}}
        pin = self.write(self.authorization_path, invalid)
        with self.assertRaisesRegex(Refused, 'Test authorization does not bind'):
            self.simulate(authorization_pin=pin)
        self.write(self.authorization_path, self.authorization)
        with self.assertRaisesRegex(Refused, 'Test authorization does not bind'):
            self.simulate(journal_path=self.root / 'alternate-journal')
        self.assert_no_dispatch()
        self.assertFalse((self.root / 'alternate-journal').exists())

    def test_test_seam_refuses_other_targets_and_unqualified_backends(self):
        self.backend.coverage['processCount'] = 'unqualified'
        with self.assertRaisesRegex(Refused, 'does not cover every required capability'):
            self.simulate()
        self.plan['target']['repository'] = 'https://example.invalid/other/project.git'
        self.plan_pin = self.write(self.plan_path, self.plan)
        with self.assertRaisesRegex(Refused, 'reviewed synthetic fixture'):
            self.simulate(current_plan=self.plan)
        self.assert_no_dispatch()

    def test_duplicate_returns_original_simulation_without_dispatch(self):
        initial = self.simulate()
        self.backend.outcome = 0
        self.assertEqual(self.simulate(), initial)
        self.assertEqual(self.backend.calls, 1)
        self.assertEqual(initial['status'], 'simulated')
        self.assertFalse(initial['executionAuthorized'])
        self.assertFalse(initial['processStarted'])
        self.assertIsNone(initial['exitCode'])
        self.assertEqual(initial['simulatedExitCode'], 7)
        self.assertEqual(initial['observedEffects'], [])
        self.assertEqual(initial['binding'], self.authorization['binding'])
        entry = json.loads((self.journal / 'effects.json').read_bytes())['operations']['recipe:test']
        self.assertEqual(entry['receipt']['result'], initial)

    def test_unknown_outcome_does_not_replay(self):
        self.backend.outcome = ConnectionError('synthetic response lost')
        with self.assertRaises(ConnectionError):
            self.simulate()
        with self.assertRaisesRegex(Refused, 'outcome unknown'):
            self.simulate()
        self.assertEqual(self.backend.calls, 1)
        entry = json.loads((self.journal / 'effects.json').read_bytes())['operations']['recipe:test']
        self.assertEqual(entry['state'], 'unknown')
        self.assertNotIn('receipt', entry)
