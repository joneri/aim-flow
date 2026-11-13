"""Proportional follow-ups never waive actual acceptance or evidence failures."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_candidate_review as source
import flow_execution_review as executed
from aim_ci.owned_paths import Refused


def response(body):
    return {'output': [{'type': 'message', 'role': 'assistant',
        'content': [{'type': 'output_text', 'text': json.dumps(body)}]}]}


def finding(impact='follow-up', severity='low'):
    return {'id': 'F1', 'description': 'Synthetic maintenance observation, not a feature failure.',
        'severity': severity, 'requirementIds': ['C1'],
        'sources': [{'path': 'candidate/feature.js', 'startLine': 1, 'endLine': 1}],
        'disposition': {'impact': impact, 'title': 'Maintenance follow-up',
            'rationale': 'Synthetic fixture has no demonstrated effect on the requirement or execution evidence.'}}


class FindingDispositionTests(unittest.TestCase):
    def setUp(self):
        text = 'export const feature = true;\n'
        self.packet = {'schemaVersion': 1, 'requirement': {'criteria': [{'id': 'C1', 'text': 'Feature works'}]},
            'baseline': {'commit': 'a' * 40, 'tree': 'b' * 40},
            'candidate': {'commit': 'c' * 40, 'tree': 'd' * 40}, 'changes': [{'path': 'feature.js'}],
            'sources': [{'path': 'candidate/feature.js', 'text': text, 'sha256': api.digest(text.encode())}],
            'findingDispositionPolicy': source.FINDING_DISPOSITION_POLICY}
        self.body = {'summary': 'Synthetic source review', 'findings': [finding()], 'unknowns': [],
            'coverage': [{'requirementId': 'C1', 'status': 'covered', 'analysis': 'Source-supported',
                'sources': [{'path': 'candidate/feature.js', 'startLine': 1, 'endLine': 1}]}]}

    def test_low_follow_up_is_retained_without_blocking(self):
        for severity in ('low', 'medium'):
            self.body['findings'] = [finding(severity=severity)]
            r = source.parse_response(response(self.body), self.packet)
            self.assertEqual(r['blockers'], [])
            self.assertEqual(len(r['findings']), 1)
            self.assertEqual(r['followUps'], r['findings'])
            self.assertFalse(source.blocking_findings(r))
            self.assertIn('quote', r['followUps'][0]['sources'][0])

    def test_real_impact_blocks_even_at_low_severity(self):
        for impact in ('requirement-failure', 'evidence-integrity', 'material-delivery-risk'):
            self.body['findings'] = [finding(impact)]
            r = source.parse_response(response(self.body), self.packet)
            self.assertEqual(len(source.blocking_findings(r)), 1)
            self.assertEqual(r['followUps'], [])
            self.assertTrue(r['blockers'])

    def test_high_or_critical_cannot_be_disguised_as_follow_up(self):
        for severity in ('high', 'critical'):
            self.body['findings'] = [finding(severity=severity)]
            with self.assertRaises(Refused): source.parse_response(response(self.body), self.packet)

    def test_missing_disposition_or_invalid_citation_is_refused(self):
        del self.body['findings'][0]['disposition']
        with self.assertRaises(Refused): source.parse_response(response(self.body), self.packet)
        self.body['findings'] = [finding()]
        self.body['findings'][0]['sources'][0]['startLine'] = 999
        with self.assertRaises(Refused): source.parse_response(response(self.body), self.packet)

    def test_legacy_reviews_keep_their_old_contract(self):
        del self.packet['findingDispositionPolicy']; del self.body['findings'][0]['disposition']
        r = source.parse_response(response(self.body), self.packet)
        self.assertTrue(r['blockers'])
        self.assertNotIn('followUps', r)
        self.assertEqual(source.blocking_findings(r), r['findings'])

    def execution(self):
        packet = copy.deepcopy(self.packet)
        source_result = source.parse_response(response(self.body), self.packet)
        packet.update(execution={'status': 'passed'}, priorOpenItems=[],
            priorFollowUps=source_result['followUps'], sourceReview=source_result,
            evidence=[{'id': 'png', 'kind': 'image/png'}, {'id': 'json', 'kind': 'application/json'}])
        f = finding(); f['evidenceIds'] = ['json']
        body = {'summary': 'Synthetic executed evidence', 'findings': [f], 'unknowns': [],
            'priorDispositions': [], 'coverage': [{'requirementId': 'C1', 'status': 'satisfied',
                'analysis': 'Synthetic supporting observations', 'sources': finding()['sources'],
                'evidenceKinds': ['source', 'behavior', 'visual'], 'evidenceIds': ['png', 'json']}]}
        return packet, body

    def test_source_and_execution_follow_ups_survive_acceptance(self):
        packet, body = self.execution()
        r = executed.parse_response(response(body), packet)
        self.assertTrue(r['localFeatureAccepted'])
        self.assertEqual(r['blockers'], [])
        self.assertEqual({f['origin'] for f in r['followUps']}, {'source-review', 'execution-review'})
        self.assertEqual(len(r['findings']), 1)

    def test_failed_checks_missing_evidence_and_unknowns_still_block(self):
        for change in ('failed-stage', 'missing-artifact', 'criterion-defect', 'unknown'):
            packet, body = self.execution()
            if change == 'failed-stage': packet['execution']['status'] = 'failed'
            if change == 'missing-artifact': body['coverage'][0]['evidenceIds'] = []
            if change == 'criterion-defect': body['coverage'][0]['status'] = 'defect'
            if change == 'unknown': body['unknowns'] = ['Required behavior remains unknown']
            r = executed.parse_response(response(body), packet)
            self.assertFalse(r['localFeatureAccepted'], change)

    def test_execution_real_defect_and_invalid_evidence_are_not_follow_ups(self):
        packet, body = self.execution()
        body['findings'][0]['disposition']['impact'] = 'requirement-failure'
        self.assertFalse(executed.parse_response(response(body), packet)['localFeatureAccepted'])
        body['findings'][0]['evidenceIds'] = ['unseen']
        with self.assertRaises(Refused): executed.parse_response(response(body), packet)


if __name__ == '__main__': unittest.main()
