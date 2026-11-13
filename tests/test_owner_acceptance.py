"""Owner scope acceptance keeps model rejection intact and cannot waive defects."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_owner_acceptance as owner
import flow_delivery_chain as chain
from aim_ci.owned_paths import Refused
import test_flow_run_executed as local_fixtures
import test_delivery_chain as delivery_fixtures


def decision(review, packet):
    return {'candidate': review['candidate'], 'requirementSha256': owner.api.digest(packet['requirement']['original'].encode()),
        'reviewSha256': owner.api.digest(owner.api.encoded(review)),
        'authority': {'actor': 'Owner Jonas', 'statement': 'Keyboard and accessible labels are sufficient for this pilot.',
            'source': 'Explicit owner instruction retained with this exact Flow grant.'},
        'criteria': [{'id': x['requirementId'], 'sha256': owner.api.digest(owner.api.encoded(x))}
            for x in review['coverage'] if x['status'] == 'uncertain'],
        'priorItems': [{'id': x['itemId'], 'sha256': owner.api.digest(owner.api.encoded(x))}
            for x in review['priorDispositions'] if x['status'] == 'unresolved'],
        'unknowns': [{'id': str(i), 'sha256': owner.api.digest(owner.api.encoded(x))} for i, x in enumerate(review['unknowns'])],
        'supportingEvidenceIds': [x['id'] for x in packet['evidence']],
        'rationale': 'Accept the demonstrated keyboard and accessible-name contract for this bounded pilot; preserve the unperformed method as a limitation.',
        'limitations': ['Actual assistive-technology use and spoken output have not been tested.']}


class OwnerScopeTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'candidate': {'commit': 'a'*40, 'tree': 'b'*40},
            'requirement': {'original': 'Verify keyboard and screen-reader accessibility.'},
            'execution': {'status': 'passed'}, 'evidence': [{'id': 'artifact:state.json', 'kind': 'application/json'}]}
        row = {'requirementId': 'criterion-1', 'status': 'uncertain', 'analysis': 'Actual reader use not observed.',
            'sources': [{'path': 'candidate/button.js'}], 'evidenceKinds': ['behavior'], 'evidenceIds': ['artifact:state.json']}
        self.review = {'candidate': self.packet['candidate'], 'localFeatureAccepted': False,
            'findings': [], 'coverage': [row], 'priorDispositions': [], 'unknowns': ['Reader session not observed.'],
            'blockers': ['Reader session not observed.', 'criterion-1: Actual reader use not observed.']}
        self.decision = decision(self.review, self.packet)

    def test_explicit_owner_scope_preserves_the_blocked_model_observation(self):
        before = copy.deepcopy(self.review)
        value = owner.check(self.decision, self.review, self.packet)
        self.assertTrue(value['ownerLocalFeatureAccepted']); self.assertFalse(value['modelLocalFeatureAccepted'])
        self.assertEqual(self.review, before); self.assertFalse(value['publicationAuthorized'])

    def test_unaddressed_or_changed_candidate_requirement_review_and_scope_refuse(self):
        for key in ('candidate', 'requirementSha256', 'reviewSha256', 'criteria', 'limitations'):
            d = copy.deepcopy(self.decision); d[key] = [] if isinstance(d[key], list) else {}
            with self.subTest(key=key), self.assertRaises(Refused): owner.check(d, self.review, self.packet)
        self.review['blockers'].append('Unaccounted integrity blocker')
        self.decision = decision(self.review, self.packet)
        with self.assertRaisesRegex(Refused, 'every exact blocker'): owner.check(self.decision, self.review, self.packet)

    def test_concrete_defect_failed_execution_or_missing_evidence_cannot_be_accepted(self):
        for kind in ('defect', 'finding', 'failed', 'missing'):
            r = copy.deepcopy(self.review); p = copy.deepcopy(self.packet)
            if kind == 'defect': r['coverage'][0]['status'] = 'defect'
            if kind == 'finding': r['findings'] = ['Actual blocking product defect']
            if kind == 'failed': p['execution']['status'] = 'failed'
            if kind == 'missing': r['coverage'][0]['evidenceIds'] = []
            with self.subTest(kind=kind), self.assertRaises(Refused): owner.check(decision(r, p), r, p)


class OwnerFlowIntegrationTests(local_fixtures.ExecutedRunTests):
    def post_all(self, endpoint, payload):
        answer = super().post_all(endpoint, payload)
        if endpoint == 'responses' and payload.get('text', {}).get('format', {}).get('name') == 'executed_acceptance_review_v1':
            body = json.loads(answer['output'][0]['content'][0]['text'])
            body['coverage'][0].update(status='uncertain', analysis='Keyboard/name assertions passed; actual reader session not observed.')
            body['unknowns'] = ['Actual reader session not observed.']
            answer['output'][0]['content'][0]['text'] = json.dumps(body)
        return answer

    def test_real_receipts_owner_grant_and_delivery_chain_preserve_model_rejection(self):
        from aim_ci import project_container as container
        for module in (owner, chain, chain.publication, chain.delivery):
            p = patch.object(module, 'ROOT', self.installed); p.start(); self.addCleanup(p.stop)
        self.delivery_pending = True
        with patch.object(container, 'execute', side_effect=self.execute_with_artifacts):
            self.local_result = self.full(self.full()['intentSha256'], transport=self.post_all)
        self.assertEqual(self.local_result['status'], 'executed-evidence-review-blocked')
        spec = {'config': self.pin(self.wpath), 'state': str(self.fs),
            'grant': self.pin(self.fs / 'grant.json'), 'result': self.pin(self.fs / 'result.json')}
        raw = {p: p.read_bytes() for p in [self.fs / 'result.json', self.fs / 'execution-review/review.json', self.fs / 'execution-review/response.json']}
        packet = json.loads((self.fs / 'execution-review/packet.json').read_bytes())
        config = {'format': owner.FORMAT, 'schemaVersion': 1, 'manifestSha256': self.release['manifestSha256'],
            'localRun': spec, 'decision': decision(self.local_result['executionReview'], packet)}
        cp = self.case / 'owner.json'; cp.write_bytes(owner.api.encoded(config)); pin = self.pin(cp); state = self.case / 'owner-acceptance'
        preview = owner.run(cp, pin['sha256'], state); self.assertFalse(state.exists())
        with self.assertRaises(Refused): owner.run(cp, pin['sha256'], state, '0'*64)
        accepted = owner.run(cp, pin['sha256'], state, preview['intentSha256'])
        self.assertEqual(owner.run(cp, pin['sha256'], state, verify_only=True), accepted)
        scoped = {**spec, 'ownerAcceptance': {'config': pin, 'state': str(state),
            'grant': self.pin(state / 'grant.json'), 'result': self.pin(state / 'result.json')}}
        with self.assertRaises(Refused): chain.verify_local(spec, self.release['manifestSha256'])
        verified = chain.verify_local(scoped, self.release['manifestSha256'])
        self.assertFalse(verified['result']['localFeatureAccepted']); self.assertFalse(verified['review']['localFeatureAccepted'])
        self.assertIn('model review remains blocked', chain.render_body(verified))
        self.assertNotIn('A fresh model review accepted', chain.render_body(verified))
        self.source = verified['source']; self.ds = self.case / 'delivery-chain'
        gh = self.case / 'no-network-gh'; gh.write_text('#!/bin/sh\nexit 91\n'); gh.chmod(0o700)
        self.dc = {'format': chain.FORMAT, 'schemaVersion': 1, 'manifestSha256': self.release['manifestSha256'],
            'localRun': scoped, 'repository': {'name': 'fixture/project', 'id': 123}, 'baseSha': self.source['baseSha'], 'baseRef': 'main',
            'branch': 'flow/owned-chain', 'title': 'Apply Flow candidate with explicit owner scope decision', 'bodyScope': chain.BODY_SCOPE,
            'requiredPrChecks': [{'name': 'Build', 'appId': 55}], 'requiredPrStatuses': [],
            'workflow': {'path': '.github/workflows/check.yml', 'sha256': chain.api.digest(delivery_fixtures.WORKFLOW),
                'requiredJobs': [{'key': 'frontend-build', 'name': 'Frontend build'}],
                'artifact': {'nameTemplate': 'frontend-{sha}-{attempt}', 'retentionDays': 7}},
            'transport': {'gh': self.pin(gh), 'home': str(self.case), 'configDirectory': str(self.case), 'timeoutSeconds': 30}}
        self.dp = self.case / 'delivery.json'; delivery_fixtures.DeliveryChainTests.save_delivery(self)
        self.github = delivery_fixtures.GitHubFixture(self)
        self.deliver = lambda authorization=None, **kw: delivery_fixtures.DeliveryChainTests.deliver(self, authorization, **kw)
        self.start_delivery = lambda: self.deliver(self.deliver()['intentSha256'])
        delivery_fixtures.DeliveryChainTests.test_exact_new_grant_runs_real_receipt_chain_through_pending_pr_and_main_ci(self)
        self.assertEqual(raw, {p: p.read_bytes() for p in raw})


for name in dir(local_fixtures.ExecutedRunTests):
    if name.startswith('test_') and name not in OwnerFlowIntegrationTests.__dict__:
        setattr(OwnerFlowIntegrationTests, name, None)
