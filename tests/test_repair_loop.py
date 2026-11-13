"""Budget-driven iteration and single-use authority, without external providers."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_repair_loop as loop
import flow_generation_budget as generations
from aim_ci.owned_paths import Refused


class RepairLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.state = self.root / 'loop'
        self.baseline = {'identity': {'allocatedPriorCampaignNanoUsd': 0}, 'requests': []}
        self.ledger = copy.deepcopy(self.baseline)
        self.wc = {'campaignDirectory': str(self.root / 'campaign'), 'historicalBudget': {'liabilityNanoUsd': 0}}
        self.rc = {'reviewMaxOperationNanoUsd': 100, 'executionMaxOperationNanoUsd': 100}
        self.template = {'maxOperationNanoUsd': 100}
        self.c = {'maximumNewGenerations': 30, 'resumeConfig': {'path': str(self.root / 'resume.json'), 'sha256': '0'*64},
                  'manifestSha256': '1'*64}
        self.intent = {'format': loop.FORMAT, 'schemaVersion': 2, 'state': str(self.state)}
        self.prepared = (self.c, self.rc, self.template, self.wc, self.baseline, self.intent)
        self.calls = []; self.repaired = 0; self.target_repairs = 6
        self.account = SimpleNamespace(directory=Path(self.wc['campaignDirectory']))
        for target, name, value in ((loop, 'prepare', self.prepared), (api, 'campaign_limit', 40_000)):
            p = patch.object(target, name, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch.object(api, 'read_existing_campaign', side_effect=lambda _: copy.deepcopy(self.ledger)); p.start(); self.addCleanup(p.stop)
        p = patch.object(loop, 'repair_config', side_effect=lambda *a, **kw: {'campaignBudget': {'path': '/budget', 'sha256': '0'*64}}); p.start(); self.addCleanup(p.stop)
        p = patch.object(loop, 'child', side_effect=self.child); p.start(); self.addCleanup(p.stop)

    def reserve(self, state):
        generations.check(self.account, copy.deepcopy(self.ledger), state, 100)
        self.ledger['requests'].append({'id': str(len(self.ledger['requests'])), 'state': str(state),
                                       'status': 'completed', 'reservedNanoUsd': 100})

    def child(self, kind, pin, state, owner, key, transport, verify_only):
        marker = owner / (state.name + '-dispatch.json'); saved = owner / (state.name + '-result.json')
        if saved.exists(): return json.loads(saved.read_text())
        self.assertFalse(verify_only)
        api.durable(owner, marker.name, b'{}')
        state.mkdir(); self.calls.append((kind, state.name))
        self.reserve(state / 'paid')
        if kind == 'repair':
            self.repaired += 1
            result = {'status': 'candidate-prepared', 'derivedWorkflowConfig': {'path': '/derived', 'sha256': '0'*64}}
        elif self.repaired < self.target_repairs:
            result = {'status': 'source-defects'}
        else:
            self.reserve(state / 'evidence')
            result = {'status': 'local-feature-evidence-reviewed', 'localFeatureAccepted': True, 'followUps': ['retain me']}
            (state / 'grant.json').write_text('{}'); (state / 'result.json').write_text(json.dumps(result))
        api.durable(owner, saved.name, api.encoded(result))
        return result

    def run_loop(self, **kw):
        return loop.run(self.root / 'config.json', '0'*64, self.state, **kw)

    def test_six_repairs_are_budget_driven_and_verification_never_repeats_paid_phases(self):
        preview = self.run_loop(); self.assertFalse(self.state.exists()); self.assertFalse(self.calls)
        result = self.run_loop(authorization=preview['intentSha256'], transport=object())
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed')
        self.assertEqual(self.repaired, 6)
        self.assertEqual(result['paidGenerations'], 14)
        self.assertEqual(len(result['phases']), 13)
        before = copy.deepcopy(self.ledger); count = len(self.calls)
        self.assertEqual(self.run_loop(verify_only=True), result)
        self.assertEqual(self.ledger, before); self.assertEqual(len(self.calls), count)
        self.assertFalse(result['publicationAuthorized']); self.assertFalse(result['productAccepted'])

    def test_verify_does_not_recreate_missing_final_or_derived_evidence(self):
        self.target_repairs = 1
        self.run_loop(authorization=self.run_loop()['intentSha256'], transport=object())
        for name in ('result.json', 'repair-0-config.json', 'review-1-config.json'):
            path = self.state / name; raw = path.read_bytes(); path.unlink()
            with self.assertRaises((Refused, FileNotFoundError)):
                self.run_loop(verify_only=True)
            self.assertFalse(path.exists(), name)
            path.write_bytes(raw)

    def test_retained_failure_starts_with_repair_and_carries_new_funding_without_old_dispatch(self):
        old = self.root / 'old'; old.mkdir()
        old_config = old / 'config.json'; old_config.write_bytes(api.encoded({'sourceReviewReuse': {'state': '/original/source'}}))
        old_result = old / 'result.json'; old_result.write_bytes(api.encoded({'status': 'local-validation-failed'}))
        self.c['retainedFailedRun'] = {'state': str(old), 'config': loop.work.pin(old_config),
            'result': loop.work.pin(old_result), 'controller': {'path': '/old/controller', 'manifestSha256': 'a'*64}}
        self.rc['campaignContinuation'] = {'path': '/funding60.json', 'sha256': 'b'*64}
        self.c['maximumNewGenerations'] = 3; self.target_repairs = 1
        observed = []
        def derive(*args, **kw):
            observed.append((args, kw))
            return {'campaignBudget': {'path': '/budget', 'sha256': '0'*64}}
        before = {p: p.read_bytes() for p in old.iterdir()}
        with patch.object(loop, 'repair_config', side_effect=derive):
            value = self.run_loop(authorization=self.run_loop()['intentSha256'], transport=object())
            self.assertTrue(value['localFeatureAccepted'])
            self.assertEqual(self.calls, [('repair', 'repair-0'), ('review', 'review-1')])
            self.assertEqual(observed[0][0][2]['campaignContinuation'], self.rc['campaignContinuation'])
            self.assertEqual(observed[0][1]['diagnostic_controller'], self.c['retainedFailedRun']['controller'])
            self.assertEqual(self.run_loop(verify_only=True), value)
        self.assertEqual({p: p.read_bytes() for p in old.iterdir()}, before)

    def test_one_remaining_paid_step_stops_before_a_review_that_cannot_complete(self):
        self.c['maximumNewGenerations'] = 1
        result = self.run_loop(authorization=self.run_loop()['intentSha256'], transport=object())
        self.assertEqual(result['status'], 'budget-blocked'); self.assertFalse(self.calls)

    def test_step_limit_stops_after_known_failure_without_an_extra_repair(self):
        self.c['maximumNewGenerations'] = 3
        result = self.run_loop(authorization=self.run_loop()['intentSha256'], transport=object())
        self.assertEqual(result['status'], 'budget-blocked')
        self.assertEqual(self.calls, [('review', 'review-0')])
        self.assertEqual(len(self.ledger['requests']), 1)

    def test_unknown_accounting_outcome_and_changed_prefix_refuse_more_work(self):
        self.c['maximumNewGenerations'] = 3
        result = self.run_loop(authorization=self.run_loop()['intentSha256'], transport=object())
        self.ledger['requests'][0]['status'] = 'unknown'
        self.assertFalse(loop.capacity(self.wc, self.baseline, self.state, 3, 1, 1)[0])
        baseline = copy.deepcopy(self.ledger)
        self.ledger['requests'][0]['reservedNanoUsd'] += 1
        with self.assertRaisesRegex(Refused, 'prefix'):
            loop.reconcile(self.wc, baseline, self.state, 3)

    def test_reservation_guard_is_enforced_at_the_actual_reservation_boundary(self):
        called = []
        with generations.authority(lambda *args: called.append(args)):
            generations.check(self.account, self.ledger, self.state, 100)
        generations.check(self.account, self.ledger, self.state, 100)
        self.assertEqual(len(called), 1)


class RepairRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.state = self.root / 'review'
        self.source = self.state / 'validation/review-attempt'; self.source.mkdir(parents=True)
        for name in {*loop.repair.REVIEW_FILES, 'review.json', 'packet.json'}:
            (self.source / name).write_text('{}')
        self.status = {'status': 'review-blocked', 'reservation': {'status': 'completed'}}
        self.evidence = {'findings': []}
        self.c = {'manifestSha256': '1'*64, 'repairContextPaths': []}
        for obj, name, value in ((api, 'status', self.status),
                (loop.resume.validation.review, 'verify_evidence', self.evidence)):
            mocked = patch.object(obj, name, return_value=value)
            mocked.start(); self.addCleanup(mocked.stop)

    def derive(self, status):
        return loop.repair_config(self.c, {}, {}, self.state, {'status': status}, {},
            {'path': '/existing/budget.json', 'sha256': '0'*64})

    def test_pending_review_evidence_does_not_authorize_infrastructure_code_repair(self):
        for status in ('resource-or-adapter-required', 'artifact-selection-required', 'blocked'):
            with self.assertRaisesRegex(Refused, 'not code-repair authority'):
                self.derive(status)

    def test_rejected_or_unknown_model_output_is_not_code_repair_authority(self):
        for status in ('review-rejected', 'unknown'):
            self.status['status'] = status
            with self.assertRaisesRegex(Refused, 'not code-repair authority'):
                self.derive('blocked')

    def test_actual_source_defects_or_failed_runtime_receipts_still_route_to_repair(self):
        self.evidence['findings'] = [{'id': 'actual-source-defect'}]
        self.assertIn('rejectedReview', self.derive('source-defects'))
        self.evidence['findings'] = []
        failed = self.state / 'validation/container-attempt'; failed.mkdir()
        (failed / 'result.json').write_bytes(api.encoded({'status': 'failed'}))
        (failed / 'intent.json').write_text('{}')
        self.assertIn('failedValidation', self.derive('local-validation-failed'))


class RetainedFailureBoundaryTests(unittest.TestCase):
    def test_exact_old_verifier_boundary_and_candidate_drift_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); old = root / 'failed'; old.mkdir()
            controller = root / 'controller'; controller.mkdir()
            pin = loop.work.pin
            workflow = root / 'workflow.json'; workflow.write_text('{}')
            rc = {'workflowConfig': pin(workflow), 'workflowState': '/original/producer',
                'producerController': {'path': '/original/controller', 'manifestSha256': '0'*64},
                'producerVerificationTimeoutSeconds': None}
            config = {**rc, 'format': resume.FORMAT, 'manifestSha256': '1'*64,
                'python': pin(Path(sys.executable).resolve())}
            config_path = root / 'old-config.json'; config_path.write_bytes(api.encoded(config))
            intent = {'config': pin(config_path), 'state': str(old), 'controllerManifestSha256': '1'*64}
            grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
            (old / 'grant.json').write_bytes(api.encoded(grant))
            initial = {'candidate': {'commit': 'a'*40, 'tree': 'b'*40, 'checkout': '/candidate'},
                'candidateReceipt': {'path': '/original/receipt', 'sha256': '2'*64}}
            result = {**initial, 'format': resume.FORMAT, 'status': 'local-validation-failed', 'localFeatureAccepted': False}
            (old / 'result.json').write_bytes(api.encoded(result))
            spec = {'controller': {'path': str(controller), 'manifestSha256': '1'*64},
                'config': pin(config_path), 'state': str(old), 'grant': pin(old / 'grant.json'), 'result': pin(old / 'result.json')}
            before = {p: p.read_bytes() for p in old.iterdir()}
            boundary = loop.resume.validation.validation
            with patch.object(boundary, 'prior_controller', return_value=b'{}'), patch.object(boundary,
                    'verification_process', return_value=SimpleNamespace(returncode=1, stderr=b'', stdout=api.encoded(result))) as verify:
                self.assertEqual(loop.verified_failed_run(spec, rc, initial, root / 'new'), (config, result))
                self.assertIn('--verify', verify.call_args.args[0])
                self.assertNotIn('--authorize-intent', verify.call_args.args[0])
                self.assertEqual(verify.call_count, 1)
                wrong = copy.deepcopy(initial); wrong['candidate']['commit'] = 'c'*40
                with self.assertRaisesRegex(Refused, 'exact producer/candidate'):
                    loop.verified_failed_run(spec, rc, wrong, root / 'new')
                self.assertEqual(verify.call_count, 1)
                verify.return_value.stdout = b'{}'
                with self.assertRaisesRegex(Refused, 'exact terminal failure'):
                    loop.verified_failed_run(spec, rc, initial, root / 'new')
            self.assertEqual({p: p.read_bytes() for p in old.iterdir()}, before)


# One real installed partial-source integration; inherited broad suites are not run.
import test_repair_run as fixtures
import flow_resume_candidate as resume
from aim_ci import project_container as container


class PartialReviewVerificationTests(fixtures.BoundedRepairRunTests):
    def test_completed_source_blocked_child_is_verified_without_effects_or_recreated_files(self):
        self.fresh_review['findings'] = copy.deepcopy(self.review_body['findings'])
        value = self.finish()
        self.assertEqual(value['status'], 'source-defects')
        config = self.fs / 'resume-config.json'; pin = self.pin(config)
        state = self.fs / 'reviewed'
        before = self.snapshot()
        with patch.object(container, 'execute', side_effect=AssertionError('verification executed project')), \
                patch.object(resume, 'run', side_effect=AssertionError('verification resumed child')):
            result = resume.verify_partial(config, pin['sha256'], state)
            self.assertEqual(result['status'], 'source-defects')
            owner = self.case / 'loop-partial'; owner.mkdir()
            expected = resume.prepare(config, pin['sha256'], state)[-1]
            (owner / (state.name + '-dispatch.json')).write_bytes(api.encoded(expected))
            (owner / (state.name + '-result.json')).write_bytes(api.encoded(result))
            self.assertEqual(loop.child('review', pin, state, owner, None, None, True), result)
            self.assertEqual(self.snapshot(), before)
            missing = state / 'validation/review-plan.json'; missing.unlink()
            with self.assertRaises((Refused, FileNotFoundError)):
                loop.child('review', pin, state, owner, None, None, True)
            self.assertFalse(missing.exists())


for name in dir(fixtures.BoundedRepairRunTests):
    if name.startswith('test_') and name not in PartialReviewVerificationTests.__dict__:
        setattr(PartialReviewVerificationTests, name, None)


if __name__ == '__main__': unittest.main()
