"""Test the Docker preparation contract with in-process fixture observations."""
import copy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci.recipe_plan import check_recipe_execution, _record_docker_fixture
from openai_budget import Refused, fingerprint


CONTROL = 'a' * 64
CONTAINER = '5' * 64


class FixtureBackend:
    def __init__(self, journal):
        self.journal = journal
        self.calls = 0
        self.response = None
        self.lookups = 0

    def observe(self, identity):
        self.calls += 1
        entry = json.loads((self.journal / 'effects.json').read_bytes())['operations']['docker:prepare']
        if entry['state'] != 'pending' or entry['intent']['effectId'] != identity['effectId']:
            raise AssertionError('Fixture response requested before persisting the matching intent')
        return self.response

    def lookup(self, identity):
        self.lookups += 1
        return self.response


class DockerRecipeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        guard = patch('subprocess.Popen', side_effect=AssertionError('Subprocess forbidden in fixture tests'))
        guard.start()
        self.addCleanup(guard.stop)
        root = Path(scratch.name).resolve()
        self.plan_path = root / 'plan.json'
        self.journal = root / 'journal'
        tools = {'node': {'path': '/usr/local/bin/node', 'sha256': '1' * 64},
                 'npm': {'path': '/usr/local/lib/node_modules/npm/bin/npm-cli.js', 'sha256': '2' * 64}}
        runtime = {'kind': 'reviewed-container-runtime', 'tools': tools, 'sha256': fingerprint(tools)}
        self.plan = {'recipeSchemaVersion': 2, 'status': 'planned-only', 'executionAuthorized': False,
                     'bindingSha256': 'b' * 64, 'control': {'manifestSha256': CONTROL},
                     'recipe': {'manifestSha256': 'c' * 64}, 'runtime': runtime,
                     'target': {'commit': 'd' * 40, 'tree': 'e' * 40},
                     'committedInputs': {'package.json': 'f' * 64, 'package-lock.json': '1' * 64},
                     'limits': {'network': 'disabled', 'timeoutSeconds': 2, 'memoryMiB': 128, 'maxProcesses': 4}}
        self.identity = {'endpoint': 'unix:///test/docker.sock', 'imageDigest': 'sha256:' + '3' * 64,
                         'adapterSha256': '4' * 64, 'runtimeSha256': runtime['sha256'],
                         'controlManifestSha256': CONTROL}
        self.policy = {'version': 1, **self.identity}
        self.pin = fingerprint(self.policy)
        self.controls = {'networkMode': 'none', 'loopback': 'disabled',
                         'workspaceWrites': 'isolated', 'readIsolation': 'isolated',
                         'memoryBytes': 134217728, 'memoryAndSwapBytes': 134217728, 'processCount': 4,
                         'wholeContainerDeadlineSeconds': 2, 'dependencyStore': 'verified'}
        self.observation = {'kind': 'synthetic-fixture', 'policyIdentity': self.identity.copy(),
                            'controls': {name: {'value': value, 'status': 'enforced'}
                                         for name, value in self.controls.items()}}
        self.backend = FixtureBackend(self.journal)

    def args(self):
        encoded = json.dumps(self.plan).encode()
        self.plan_path.write_bytes(encoded)
        return self.plan_path, sha256(encoded).hexdigest(), CONTROL

    def options(self):
        return {'trusted_docker_policy': self.policy, 'trusted_docker_policy_pin': self.pin,
                'fixture_observation': self.observation}

    def prepare(self):
        return check_recipe_execution(*self.args(), **self.options())

    def record(self):
        return _record_docker_fixture(self.journal, *self.args(), **self.options(),
                                      fixture_container_id=CONTAINER, fixture_backend=self.backend)

    def terminal(self):
        intent = self.prepare()['intent']
        identity = {key: intent[key] for key in ('effectId', 'containerName', 'dockerPolicySha256')}
        return {**identity, 'kind': 'synthetic-fixture', 'containerId': CONTAINER,
                'lifecycle': {'processStarted': True, 'signal': 9, 'oom': True}}

    def test_plan_cannot_supply_policy_and_independent_pins_bind_observation(self):
        self.plan['dockerPolicy'] = self.policy
        self.plan['dockerObservation'] = self.observation
        self.assertEqual(check_recipe_execution(*self.args())['backend'], 'native')
        for field, new in (('endpoint', 'unix:///other/docker.sock'), ('imageDigest', 'sha256:' + '6' * 64),
                           ('adapterSha256', '6' * 64), ('controlManifestSha256', '6' * 64)):
            with self.subTest(field=field):
                changed = self.policy | {field: new}
                with self.assertRaisesRegex(Refused, 'policy identity or pin mismatch'):
                    check_recipe_execution(*self.args(), **(self.options() | {'trusted_docker_policy': changed}))
        self.observation['policyIdentity']['adapterSha256'] = '6' * 64
        with self.assertRaisesRegex(Refused, 'does not match the controller policy'):
            self.prepare()

    def test_host_runtime_and_runtime_pin_drift_require_fresh_review(self):
        self.plan['runtime'].pop('kind')
        preparation = self.prepare()
        self.assertIn('containerRuntimeIdentity', preparation['unqualified'])
        self.assertIsNone(preparation['intent'])
        with self.assertRaisesRegex(Refused, 'remain unqualified'):
            self.record()
        self.plan['runtime']['kind'] = 'reviewed-container-runtime'
        self.plan['runtime']['tools']['node']['sha256'] = '7' * 64
        self.assertIn('containerRuntimeIdentity', self.prepare()['unqualified'])
        self.assertFalse(self.journal.exists())

    def test_none_loopback_client_timeout_and_unenforced_resource_requests_do_not_pass(self):
        self.observation['controls'].update(
            loopback={'value': 'enabled', 'status': 'enforced'},
            wholeContainerDeadlineSeconds={'value': 2, 'status': 'client-timeout-only'},
            memoryBytes={'value': 134217728, 'status': 'requested'},
            memoryAndSwapBytes={'value': 268435456, 'status': 'enforced'},
        )
        self.observation['controls'].pop('processCount')
        prepared = self.prepare()
        self.assertEqual(prepared['controls']['networkMode']['status'], 'fixture-enforced')
        self.assertEqual(prepared['controls']['loopback']['observed'], 'enabled')
        self.assertIsNone(prepared['controls']['memoryBytes']['observed'])
        self.assertEqual(prepared['controls']['wholeContainerDeadlineSeconds']['observationStatus'],
                         'client-timeout-only')
        self.assertIsNone(prepared['controls']['wholeContainerDeadlineSeconds']['observed'])
        self.assertEqual(set(prepared['unqualified']), {'loopback', 'wholeContainerDeadlineSeconds', 'memoryBytes',
                                                       'memoryAndSwapBytes', 'processCount'})
        self.assertEqual(prepared['cleanup'], 'unknown')
        with self.assertRaisesRegex(Refused, 'remain unqualified'):
            self.record()
        self.assertFalse(self.journal.exists())

    def test_preparation_and_fixture_terminal_fields_do_not_claim_real_execution(self):
        prepared = self.prepare()
        self.assertEqual(prepared['intent'], self.prepare()['intent'])
        self.assertEqual(prepared['status'], 'blocked-before-dispatch')
        self.assertFalse(self.journal.exists())
        self.backend.response = self.terminal()
        receipt = self.record()
        self.assertEqual(receipt['status'], 'simulated')
        self.assertEqual(receipt['fixtureLifecycle']['signal'], 9)
        self.assertTrue(receipt['fixtureLifecycle']['oom'])
        self.assertIsNone(receipt['fixtureLifecycle']['timedOut'])
        self.assertEqual(receipt['fixtureLifecycle']['cleanup'], 'unknown')
        self.assertFalse(receipt['processStarted'])
        self.assertFalse(receipt['executionAuthorized'])
        self.assertFalse(receipt['productAccepted'])
        for name in ('exitCode', 'signal', 'oom', 'timedOut'):
            self.assertIsNone(receipt[name])
        self.assertEqual(receipt['observedEffects'], [])
        self.backend.response = None
        self.assertEqual(self.record(), receipt)
        self.assertEqual((self.backend.calls, self.backend.lookups), (1, 0))
        self.plan['limits']['timeoutSeconds'] = 3
        self.observation['controls']['wholeContainerDeadlineSeconds']['value'] = 3
        self.assertNotEqual(self.prepare()['intent']['effectId'], prepared['intent']['effectId'])
        with self.assertRaisesRegex(Refused, 'scope/profile/mode changed'):
            self.record()

    def test_unknown_reconciles_only_matching_container_and_effect_with_terminal_evidence(self):
        terminal = self.terminal()
        self.backend.response = copy.deepcopy(terminal)
        self.backend.response['lifecycle'] = {'timedOut': True}
        with self.assertRaisesRegex(Refused, 'fixture outcome is unknown'):
            self.record()
        for field in ('effectId', 'containerName', 'containerId', 'dockerPolicySha256'):
            self.backend.response = terminal | {field: '8' * 64}
            with self.assertRaisesRegex(Refused, 'outcome unknown'):
                self.record()
        self.backend.response = terminal
        reconciled = self.record()
        self.assertEqual(reconciled['fixtureLifecycle']['containerId'], terminal['containerId'])
        self.assertEqual((self.backend.calls, self.backend.lookups), (1, 5))
        entry = json.loads((self.journal / 'effects.json').read_bytes())['operations']['docker:prepare']
        self.assertTrue(entry['reconciled'])
