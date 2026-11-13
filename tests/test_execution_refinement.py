"""Synthetic finite scheduling contracts; no project command/provider/container."""
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_execution_refinement as refinement
import flow_workspace_review as review
from aim_ci.owned_paths import Refused
import test_workspace_review as fixtures


class RefinementTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.WorkspacePolicyTests(); fixture.setUp()
        self.fixture = fixture
        self.steps = [dict(id='test', area='tests', command='npm run test', dependsOn=[]),
            dict(id='inspect', area='tests', command='npm run preview', dependsOn=['test']),
            dict(id='gate', area='ci', command='npm run gate', dependsOn=['inspect']),
            dict(id='ship', area='deployment', command='publish', dependsOn=['gate'])]
        self.rows = [dict(stepId=s['id'], kind=k, evidenceSteps=e, executionDependsOn=d,
            analysis='Candidate source establishes finite producer and reviewed evidence scheduling; acceptance stays open',
            sources=[fixture.ref('scripts/generate.js')]) for s,k,e,d in zip(self.steps,
                ['finite-local','evidence-only-acceptance','finite-local','hosted-delivery'],
                [[],['test'],[],[]], [[],[],['test'],[]])]
        fixture.packet['workspaceContext']['refineExecutionPlan'] = True
        fixture.packet['approvedPlan'] = json.dumps({'assessment':{'steps':self.steps}})
        fixture.body['executionRefinement'] = self.rows
        self.derived = {'declaration': {'jobs': [dict(id=s['id'], needs=s['dependsOn'],
            stages=[{'root':'.','operation':'run','script':s['command'][8:]}]) for s in self.steps[:3]]},
            'resourceNeeds':[{'kind':'assessment-predecessor', 'step':'gate'}, {'kind':'node-runtime', 'step':'gate'}],
            'unsupportedSteps':[{'step':'ship'}]}

    def apply(self):
        parsed = self.fixture.parse()
        return refinement.apply(self.derived, self.steps, parsed['executionRefinement'])

    def test_substitutes_only_reviewed_evidence_schedule_keeps_acceptance_open(self):
        original = copy.deepcopy(self.derived)
        result = self.apply()
        self.assertEqual([j['id'] for j in result['declaration']['jobs']], ['test','gate'])
        self.assertEqual(result['declaration']['jobs'][1]['needs'], ['test'])
        self.assertEqual(result['acceptanceObligations'], [self.steps[1]])
        self.assertEqual(result['resourceNeeds'], [{'kind':'node-runtime', 'step':'gate'}])
        self.assertEqual(self.derived, original)

    def test_every_original_step_required(self):
        self.rows.pop()
        with self.assertRaisesRegex(Refused, 'every exact'): self.apply()

    def test_persistent_service_without_substitution_blocks(self):
        self.rows[1].update(kind='blocked', evidenceSteps=[])
        with self.assertRaisesRegex(Refused, 'blocked'): self.apply()

    def test_cannot_omit_runtime_prerequisite(self):
        self.rows[2]['executionDependsOn'] = []
        with self.assertRaisesRegex(Refused, 'predecessor'): self.apply()

    def test_cannot_use_later_or_nonfinite_evidence(self):
        self.rows[1]['evidenceSteps'] = ['gate']
        with self.assertRaisesRegex(Refused, 'earlier'): self.apply()

    def test_cannot_defer_local_ci_to_hosted_delivery(self):
        self.rows[2].update(kind='hosted-delivery', executionDependsOn=[])
        with self.assertRaises(Refused): self.apply()

    def test_local_ci_gate_cannot_be_substituted_with_other_test_evidence(self):
        self.rows[2].update(kind='evidence-only-acceptance', executionDependsOn=[], evidenceSteps=['test'])
        with self.assertRaisesRegex(Refused, 'CI gates'): self.apply()

    def test_no_new_command_adapter_or_hosted_prerequisite_bypass(self):
        self.steps[3].update(area='ci', command='gh run watch')
        self.rows[3].update(kind='finite-local', executionDependsOn=['gate'])
        self.fixture.packet['approvedPlan'] = json.dumps({'assessment':{'steps':self.steps}})
        with self.assertRaisesRegex(Refused, 'unsupported command'): self.apply()

    def duplicate_test_plan(self):
        self.steps = [
            dict(id='test', area='tests', command='npm run test', workingDirectory='.', dependsOn=[]),
            dict(id='repeat', area='tests', command='npm run test', workingDirectory='.', dependsOn=['test'])]
        self.rows = [dict(stepId=s['id'], kind=kind, evidenceSteps=evidence, executionDependsOn=[],
            analysis='The same committed test in the same attempt supports the repeated outcome; no intervening work.',
            sources=[self.fixture.ref('scripts/generate.js')])
            for s, kind, evidence in zip(self.steps, ['finite-local', 'reuse-local-test'], [[], ['test']])]
        self.fixture.body['executionRefinement'] = self.rows
        self.sync_duplicate_plan()

    def sync_duplicate_plan(self):
        self.fixture.packet['approvedPlan'] = json.dumps({'assessment': {'steps': self.steps}})
        self.derived = {'declaration': {'runtimes': [dict(id='runtime', requirements=['fixture'], sources=[])],
            'jobs': [dict(id=s['id'], mode='local-sequence', runtime='runtime', needs=s['dependsOn'],
            coverage='Fixture test outcome', effects=['Fixture-only'], prerequisites=['Reviewed source'], sources=[],
            stages=[{'root': s['workingDirectory'], 'operation': 'run', 'script': s['command'][8:]}])
            for s in self.steps]}, 'resourceNeeds': [], 'unsupportedSteps': []}

    def test_reuses_reviewed_adjacent_identical_tests_once_and_retains_obligations(self):
        self.duplicate_test_plan()
        original = copy.deepcopy(self.derived)
        value = self.apply()
        self.assertEqual([j['id'] for j in value['declaration']['jobs']], ['test'])
        self.assertEqual(value['acceptanceObligations'], [self.steps[1]])
        self.assertEqual(value['reusedLocalTests'][0]['stepId'], 'repeat')
        self.assertEqual(value['reusedLocalTests'][0]['producerStepId'], 'test')
        self.assertEqual(self.derived, original)
        # The actual workflow composer consumes this selected declaration.
        from flow_workflow_validation import compose
        declaration, graph = compose(value)
        self.assertEqual(len(declaration['jobs'][0]['stages']), 1)
        self.assertEqual([row['id'] for row in graph], ['test'])

    def test_reuse_cannot_remove_installation_ci_or_deployment(self):
        for index in (0, 1):
            for area in ('installation', 'ci', 'deployment'):
                self.duplicate_test_plan()
                self.steps[index]['area'] = area
                self.sync_duplicate_plan()
                with self.subTest(index=index, area=area), self.assertRaises(Refused):
                    self.apply()

    def test_reuse_requires_same_exact_command_and_working_directory(self):
        for field, value in [('command', 'npm run other'), ('workingDirectory', 'web')]:
            self.duplicate_test_plan()
            self.steps[1][field] = value
            self.sync_duplicate_plan()
            with self.subTest(field=field), self.assertRaises(Refused):
                self.apply()

    def test_reuse_refuses_intervening_work_and_nonfinite_or_multiple_producers(self):
        self.duplicate_test_plan()
        self.steps.insert(1, dict(id='build', area='tests', command='npm run build', workingDirectory='.', dependsOn=['test']))
        self.rows.insert(1, dict(stepId='build', kind='finite-local', evidenceSteps=[], executionDependsOn=['test'],
            analysis='Build changes generated outputs', sources=[self.fixture.ref('scripts/generate.js')]))
        self.steps[2]['dependsOn'] = ['build']
        self.sync_duplicate_plan()
        with self.assertRaises(Refused): self.apply()
        for producers in ([], ['test', 'test'], ['repeat']):
            self.duplicate_test_plan()
            self.rows[1]['evidenceSteps'] = producers
            with self.subTest(producers=producers), self.assertRaises(Refused): self.apply()

    def test_reuse_is_explicit_and_does_not_assume_other_tests_are_equivalent(self):
        self.duplicate_test_plan()
        self.rows[1].update(kind='finite-local', evidenceSteps=[], executionDependsOn=['test'])
        self.assertEqual([j['id'] for j in self.apply()['declaration']['jobs']], ['test', 'repeat'])
        self.assertNotIn('reusedLocalTests', self.apply())

    def test_opt_in_is_explicit(self):
        self.assertFalse(refinement.requested({}))
        for value in (False, 1, 'true'):
            with self.assertRaises(Refused): refinement.requested({'refineExecutionPlan':value})

    def test_refinement_receipt_roundtrip_and_forgery(self):
        import tempfile
        import flow_api_delivery as api
        result = self.fixture.parse()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve()/'packet.json'; r=Path(tmp).resolve()/'review.json'
            p.write_bytes(api.encoded(self.fixture.packet));r.write_bytes(api.encoded(result))
            pin=lambda path: {'path':str(path),'sha256':api.digest(path.read_bytes())}
            self.assertEqual(review.verify_evidence(pin(r),pin(p)),result)
            result['executionRefinement'][0]['sources'][0]['quote']='forged'
            r.write_bytes(api.encoded(result))
            with self.assertRaises(Refused): review.verify_evidence(pin(r),pin(p))

if __name__ == '__main__': unittest.main()
