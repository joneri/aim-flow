"""Retained failed-command diagnostics: real Git/receipt verification, fake execution."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_repair_candidate as repair
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_project_container as fixtures


class FailedExecutionDiagnosticTests(fixtures.ContainerTests):
    def setUp(self):
        super().setUp()
        initial, _ = self.prepare()
        self.initial = {'candidate': initial['target']}
        self.packet = {'candidate': {k: initial['target'][k] for k in ('commit', 'tree')}, 'sources': []}
        self.spec['sourceReview'] = ['candidate-source-review:' + api.digest(api.encoded(self.packet))]
        self.save()
        self.intent, self.reader = self.prepare()
        self.result = container.execution(self.f.current, self.args(preview=True))
        with patch.object(container, 'execute', side_effect=self.fake_failed_command):
            self.result = container.execution(self.f.current,
                self.args(preview=False, authorize_intent=self.result['intentSha256']))
        self.diagnostic = {'controller': {'path': str(self.f.current),
                'manifestSha256': self.intent['controllerManifestSha256']},
            'state': str(self.state), 'intent': self.pin(self.state / 'intent.json'),
            'result': self.pin(self.state / 'result.json'), 'contextPaths': ['scripts/check.py']}

    def pin(self, path):
        return {'path': str(path), 'sha256': api.digest(path.read_bytes())}

    def fake_failed_command(self, root, state, intent, reader):
        # Only the external execution observation is synthetic. All durable
        # effect journals and the installed container receipt verifier are real.
        body = {'format': 'aim-flow-project-container-report', 'schemaVersion': 1,
            'status': 'failed', 'failure': 'Synthetic assertion failure; no project ran',
            'stages': [{'index': 0, 'exitCode': 1, 'argv': ['node', 'scripts/check.py'],
                'projectArgv': ['npm', 'test'], 'signal': None, 'error': None,
                'stdout': '', 'stderr': 'AssertionError\n at file:///work/project/scripts/check.py:1:1\n'}]}
        terminal = {'inspected': True, 'state': {'Running': False, 'Pid': 0, 'ExitCode': 1}}
        api.durable(state, 'container.stdout', api.encoded(body))
        api.durable(state, 'container-terminal.json', api.encoded(terminal))
        return {'status': 'failed', 'intentSha256': container.local.digest(intent),
            'report': body, 'terminal': terminal, 'job': intent['job']['id'],
            'containerCleanup': 'confirmed-absent',
            'controlFiles': {name: self.pin(state / name)['sha256']
                for name in ('container.stdout', 'container-terminal.json')}}

    def feedback(self, spec=None, initial=None, packet=None):
        return repair.failed_execution_feedback(spec or self.diagnostic,
            initial or self.initial, self.reader, self.packet if packet is None else packet,
            {'authority': 'none', 'body': {'findings': []}, 'citations': []}, ['scripts/'])

    def test_actual_failed_receipt_yields_locations_and_separate_new_context_without_authority(self):
        original = copy.deepcopy(self.packet)
        feedback, augmented = self.feedback()
        diagnostic = feedback['failedExecution']
        self.assertEqual(diagnostic['authority'], 'none')
        self.assertEqual(diagnostic['projectAcceptance'], 'not-established')
        self.assertEqual(diagnostic['sourceLocations'][0]['path'], 'scripts/check.py')
        self.assertEqual(diagnostic['sourceLocations'][0]['startLine'], 1)
        self.assertEqual(augmented['sources'][0]['path'], 'candidate/scripts/check.py')
        self.assertEqual(self.packet, original)
        self.assertNotIn(str(self.case), json.dumps(diagnostic))

    def test_wrong_receipt_candidate_review_or_controller_is_refused(self):
        mutations = [lambda s: s['result'].update(sha256='0' * 64),
            lambda s: s['controller'].update(manifestSha256='0' * 64),
            lambda s: s['intent'].update(path=str(self.state / 'elsewhere.json'))]
        for change in mutations:
            spec = copy.deepcopy(self.diagnostic); change(spec)
            with self.subTest(spec=spec), self.assertRaises(Refused): self.feedback(spec)
        initial = copy.deepcopy(self.initial); initial['candidate']['commit'] = '0' * 40
        with self.assertRaises(Refused): self.feedback(initial=initial)
        packet = copy.deepcopy(self.packet); packet['sources'] = [{'path': 'invented'}]
        with self.assertRaises(Refused): self.feedback(packet=packet)

    def test_context_outside_original_scope_and_tampered_retained_report_are_refused(self):
        for paths in (['../secret'], ['README.md'], ['scripts/.env'], ['scripts/check.py'] * 2):
            spec = copy.deepcopy(self.diagnostic); spec['contextPaths'] = paths
            with self.subTest(paths=paths), self.assertRaises(Refused): self.feedback(spec)
        (self.state / 'container.stdout').write_bytes(b'{}\n')
        with self.assertRaises(Refused): self.feedback()


for name in dir(fixtures.ContainerTests):
    if name.startswith('test_') and name not in FailedExecutionDiagnosticTests.__dict__:
        setattr(FailedExecutionDiagnosticTests, name, None)


class RepairContextBudgetTests(unittest.TestCase):
    def test_scoped_repair_preserves_original_feature_and_selected_layout_bytes_without_unrelated_aggregate_context(self):
        sources = [{'path': path, 'text': 'Exact text for ' + path, 'sha256': path, 'lineCount': 1}
            for path in ('baseline/src/feature.js', 'candidate/src/feature.js',
                'candidate/src/styles/site.css', 'candidate/scripts/test.mjs', 'candidate/server/unrelated.js')]
        packet = {'sources': sources}; before = copy.deepcopy(packet)
        scope = {'editablePaths': ['src/styles/site.css', 'scripts/test.mjs']}
        legacy = repair.repair_context_sources(packet, scope, ['src/feature.js'])
        self.assertEqual(legacy, [{k: s[k] for k in ('path', 'sha256', 'text')} for s in sources])
        selected = repair.repair_context_sources(packet, scope, ['src/feature.js'], failed_execution=True)
        self.assertEqual([s['path'] for s in selected], [s['path'] for s in sources[:-1]])
        self.assertEqual(selected, legacy[:-1])
        self.assertEqual(packet, before)


class RepairMetadataDeadlineTests(unittest.TestCase):
    def test_local_metadata_policy_has_no_ordinal_specific_ceiling(self):
        self.assertIsNone(repair.verification_deadline({'producerVerificationTimeoutSeconds': None}))
        for value in (1, 1800, 7200):
            self.assertEqual(repair.verification_deadline({'producerVerificationTimeoutSeconds': value}), value)
        for value in (0, True, 1.5):
            with self.assertRaises(Refused): repair.verification_deadline({'producerVerificationTimeoutSeconds': value})

    def test_original_producer_uses_the_selected_metadata_policy(self):
        class StopAfterObservation(Exception): pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); config = root / 'workflow.json'; config.write_text('{}\n')
            c = {'originalProducer': {'workflowConfig': {'path': str(config), 'sha256': api.digest(config.read_bytes())},
                    'workflowState': str(root), 'producerController': {}},
                'python': {}, 'producerVerificationTimeoutSeconds': 1800}
            with patch('flow_workflow_validation.verified_producer', side_effect=StopAfterObservation) as verify:
                with self.assertRaises(StopAfterObservation): repair.original_inputs(c)
            self.assertEqual(verify.call_args.args[0]['producerVerificationTimeoutSeconds'], 1800)


if __name__ == '__main__': unittest.main()
