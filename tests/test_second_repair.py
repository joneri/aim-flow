"""One explicit second repair; real installed lineage/accounting, synthetic provider only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_repair_candidate as repair
import flow_github_publication as publication
from aim_ci.owned_paths import Refused
import test_repair_candidate as fixtures


class SecondRepairTests(fixtures.RepairTests):
    def test_second_repair_keeps_canonical_authority_prior_candidate_and_all_rejected_evidence(self):
        first = self.grant_repair()
        first_config = self.pin(self.rpath); first_state = self.rs
        first_snapshot = {str(p): p.read_bytes() for p in first_state.rglob('*') if p.is_file() and '.git' not in p.parts}
        self.pc.update(workflowConfig=first['derivedWorkflowConfig'], workflowState=str(first_state),
            producerController=self.rc['originalProducer']['producerController'],
            producerKind='repair-candidate', repairConfig=first_config)
        self.ps = self.case / 'second-review'; self.save_phase_config()
        self.review_body['findings'][0]['sources'] = [{'path': 'candidate/src/feature.js', 'startLine': 1, 'lineCount': 1}]
        preview = self.phase(); rejected = self.phase(preview['intentSha256'], self.review_post)
        self.assertEqual(rejected['reviewResult']['status'], 'review-rejected')
        rejected_state = self.ps / 'review-attempt'
        previous = copy.deepcopy(self.rc)
        self.rc.update(parentRepair={key: self.pc[key] for key in
            ('producerKind', 'producerController', 'workflowConfig', 'workflowState', 'repairConfig')},
            rejectedReview={'state': str(rejected_state), 'files': {
                name: self.pin(rejected_state / name)['sha256'] for name in repair.REVIEW_FILES}},
            campaignBudget=self.pin(Path(self.wc['campaignDirectory']) / 'budget.json'))
        self.rpath = self.case / 'second-repair.json'; self.rs = self.case / 'second-repair'; self.save_repair()
        preview = self.repaired()
        self.assertEqual(preview['intent']['parentRepair']['repairConfig'], first_config)
        self.assertIn('src/feature.js', preview['intent']['scope']['editablePaths'])
        ledger_before = json.loads((Path(self.wc['campaignDirectory']) / 'budget.json').read_bytes())
        def post(endpoint, payload):
            if endpoint == 'responses/input_tokens': return {'input_tokens': 100}
            answer = copy.deepcopy(self.answer)
            answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Synthetic second repair; no execution.',
                'edits': [{'path': 'src/feature.js', 'old': 'export const',
                           'new': '// synthetic bounded repair\nexport const'}]})
            return answer
        result = self.repaired(preview['intentSha256'], transport=post)
        self.assertEqual(result['status'], 'candidate-prepared', result)
        self.assertEqual(result['originalCandidateReceipt'], first['candidateReceipt'])
        self.assertEqual(result['source']['baseSha'], self.commit)
        self.assertEqual(result['instanceController'], previous['originalProducer']['producerController'])
        self.assertEqual(self.repaired(verify_only=True), result)
        with patch.object(publication, 'ROOT', self.installed):
            c = {'source': {**result['source'], 'git': {'path': self.wc['source']['git'],
                    'sha256': self.wc['source']['gitSha256']}}, 'evidence': {'candidate': result['candidateReceipt']},
                'candidateVerification': {'controller': {'path': str(self.installed),
                    'manifestSha256': self.rc['manifestSha256']}, 'python': self.rc['python'],
                    'config': result['derivedWorkflowConfig'], 'state': str(self.rs),
                    'producerKind': 'repair-candidate', 'repairConfig': self.pin(self.rpath),
                    'producerVerificationTimeoutSeconds': 900}}
            publication.source_binding(c)
            self.assertEqual(publication.verify_producer(c)['status'], 'source-bound-flow-candidate')
            changed = copy.deepcopy(c); changed['source']['baseSha'] = first['candidate']['commit']
            with self.assertRaises(Refused): publication.source_binding(changed)
        self.assertEqual(first_snapshot, {str(p): p.read_bytes() for p in first_state.rglob('*')
                                         if p.is_file() and '.git' not in p.parts})
        after = json.loads((Path(self.wc['campaignDirectory']) / 'budget.json').read_bytes())
        self.assertEqual(after['requests'][:-1], ledger_before['requests'])
        # A third repair cannot acquire authority by recursively embedding the second.
        third = copy.deepcopy(self.rc)
        third['parentRepair'].update(repairConfig=self.pin(self.rpath), workflowState=str(self.rs),
                                     workflowConfig=result['derivedWorkflowConfig'])
        path = self.case / 'third.json'; path.write_bytes(api.encoded(third))
        with self.assertRaisesRegex(Refused, 'explicit ordinal'):
            repair.prepare(path, self.pin(path)['sha256'], self.case / 'third')
        self.assertFalse(self.container_calls)

    def test_explicit_funded_third_repair_from_valid_source_findings_preserves_every_ancestor(self):
        self.test_second_repair_keeps_canonical_authority_prior_candidate_and_all_rejected_evidence()
        second = self.repaired(verify_only=True)
        old = Path(self.wc['campaignDirectory'])
        before = (old / 'budget.json').read_bytes()
        baseline = self.case / 'prior-budget.json'; baseline.write_bytes(before)
        funding = self.case / 'funding.json'
        funding.write_bytes(api.encoded({'format': 'aim-flow-campaign-continuation', 'schemaVersion': 1,
            'campaignDirectory': str(self.case / 'new-campaign'), 'limitNanoUsd': 30_000_000_000,
            'priorCampaign': {key: self.wc[key] for key in ('campaignDirectory', 'historicalBudget')},
            'priorBudgetSnapshot': self.pin(baseline)}))
        self.pc.update(workflowConfig=second['derivedWorkflowConfig'], workflowState=str(self.rs),
            repairConfig=self.pin(self.rpath), campaignContinuation=self.pin(funding))
        self.ps = self.case / 'third-source-review'; self.save_phase_config()
        self.review_body['coverage'][0]['sources'] = [
            {'path': 'candidate/src/feature.js', 'startLine': 1, 'lineCount': 1}]
        preview = self.phase(); value = self.phase(preview['intentSha256'], self.review_post)
        rejected = self.ps / 'review-attempt'
        self.assertEqual(api.status(rejected)['status'], 'review-blocked', value)
        self.rc.update(repairOrdinal=3, campaignContinuation=self.pin(funding),
            parentRepair={key: self.pc[key] for key in
                ('producerKind', 'producerController', 'workflowConfig', 'workflowState', 'repairConfig')},
            rejectedReview={'state': str(rejected), 'files': {
                name: self.pin(rejected / name)['sha256'] for name in (*repair.REVIEW_FILES, 'review.json')}},
            campaignBudget=self.pin(self.case / 'new-campaign/budget.json'))
        self.rs = self.case / 'third-repair'; self.rpath = self.case / 'third-repair.json'; self.save_repair()
        preview = self.repaired()
        self.assertEqual(preview['intent']['combinedHistoricalAndCampaignLimitNanoUsd'], 30_000_000_000)
        self.assertEqual(preview['intent']['repairOrdinal'], 3)
        def post(endpoint, payload):
            if endpoint == 'responses/input_tokens': return {'input_tokens': 100}
            data = json.loads(payload['input'])
            self.assertEqual(data['rejectedReviewDiagnostics']['originalReviewStatus'], 'review-blocked')
            answer = copy.deepcopy(self.answer)
            answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Synthetic explicit third repair.',
                'edits': [{'path': 'src/feature.js', 'old': '// synthetic bounded repair\n',
                           'new': '// synthetic third repair\n'}]})
            return answer
        result = self.repaired(preview['intentSha256'], transport=post)
        self.assertEqual(result['status'], 'candidate-prepared', result)
        self.assertEqual(result['originalCandidateReceipt'], second['candidateReceipt'])
        self.assertEqual(self.repaired(verify_only=True), result)
        self.assertEqual((old / 'budget.json').read_bytes(), before)
        derived = json.loads(api.pinned(result['derivedWorkflowConfig']))
        self.assertEqual(derived['campaignDirectory'], str(old))
        self.assertNotIn('campaignContinuation', derived)
        with patch.object(publication, 'ROOT', self.installed):
            c = {'source': {**result['source'], 'git': {'path': self.wc['source']['git'],
                    'sha256': self.wc['source']['gitSha256']}}, 'evidence': {'candidate': result['candidateReceipt']},
                'candidateVerification': {'controller': self.rc['originalProducer']['producerController'],
                    'python': self.rc['python'], 'config': result['derivedWorkflowConfig'], 'state': str(self.rs),
                    'producerKind': 'repair-candidate', 'repairConfig': self.pin(self.rpath),
                    'producerVerificationTimeoutSeconds': 900}}
            publication.source_binding(c)
            self.assertEqual(publication.verify_producer(c)['status'], 'source-bound-flow-candidate')
            with patch.dict(self.rc, repairOrdinal=4):
                self.save_repair()
                with self.assertRaises(Refused): self.repaired()
        self.assertFalse(self.container_calls)

    def test_explicit_fourth_repair_preserves_all_three_predecessors_and_publication_links(self):
        self.test_explicit_funded_third_repair_from_valid_source_findings_preserves_every_ancestor()
        # The preceding negative test intentionally persisted ordinal4 while
        # patch.dict restored the in-memory third configuration. Restore that
        # fixture input before reusing the completed third producer.
        self.save_repair()
        third = self.repaired(verify_only=True)
        self.pc.update(workflowConfig=third['derivedWorkflowConfig'], workflowState=str(self.rs),
            repairConfig=self.pin(self.rpath))
        self.ps = self.case / 'fourth-source-review'; self.save_phase_config()
        preview = self.phase(); self.phase(preview['intentSha256'], self.review_post)
        rejected = self.ps / 'review-attempt'
        self.assertEqual(api.status(rejected)['status'], 'review-blocked')
        self.rc.update(repairOrdinal=4,
            parentRepair={key: self.pc[key] for key in
                ('producerKind', 'producerController', 'workflowConfig', 'workflowState', 'repairConfig')},
            rejectedReview={'state': str(rejected), 'files': {
                name: self.pin(rejected / name)['sha256'] for name in (*repair.REVIEW_FILES, 'review.json')}},
            campaignBudget=self.pin(self.case / 'new-campaign/budget.json'))
        self.rs = self.case / 'fourth-repair'; self.rpath = self.case / 'fourth-repair.json'; self.save_repair()
        preview = self.repaired()
        self.assertEqual(preview['intent']['repairOrdinal'], 4)
        def post(endpoint, payload):
            if endpoint == 'responses/input_tokens': return {'input_tokens': 100}
            answer = copy.deepcopy(self.answer)
            answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Synthetic explicit fourth repair.',
                'edits': [{'path': 'src/feature.js', 'old': '// synthetic third repair\n',
                           'new': '// synthetic fourth repair\n'}]})
            return answer
        result = self.repaired(preview['intentSha256'], transport=post)
        self.assertEqual(result['originalCandidateReceipt'], third['candidateReceipt'])
        self.assertEqual(result, self.repaired(verify_only=True))
        with patch.object(publication, 'ROOT', self.installed):
            config = {'source': {**result['source'], 'git': {'path': self.wc['source']['git'],
                'sha256': self.wc['source']['gitSha256']}}, 'evidence': {'candidate': result['candidateReceipt']},
                'candidateVerification': {'producerKind': 'repair-candidate', 'repairConfig': self.pin(self.rpath)}}
            publication.source_binding(config)
        self.rc['repairOrdinal'] = 5; self.save_repair()
        with self.assertRaises(Refused): self.repaired()
        self.assertFalse(self.container_calls)

    def test_fifth_and_sixth_repair_keep_real_git_lineage_and_publication_binding(self):
        self.test_explicit_fourth_repair_preserves_all_three_predecessors_and_publication_links()
        self.rc['repairOrdinal'] = 4; self.save_repair()
        previous = self.repaired(verify_only=True)
        old_text = '// synthetic fourth repair\n'
        for ordinal in (5, 6):
            self.pc.update(workflowConfig=previous['derivedWorkflowConfig'], workflowState=str(self.rs),
                repairConfig=self.pin(self.rpath))
            self.ps = self.case / ('source-review-' + str(ordinal)); self.save_phase_config()
            self.phase(self.phase()['intentSha256'], self.review_post)
            rejected = self.ps / 'review-attempt'
            self.assertEqual(api.status(rejected)['status'], 'review-blocked')
            self.rc.update(repairOrdinal=ordinal, producerVerificationTimeoutSeconds=None,
                parentRepair={k: self.pc[k] for k in ('producerKind', 'producerController',
                    'workflowConfig', 'workflowState', 'repairConfig')},
                rejectedReview={'state': str(rejected), 'files': {name: self.pin(rejected / name)['sha256']
                    for name in (*repair.REVIEW_FILES, 'review.json')}},
                campaignBudget=self.pin(self.case / 'new-campaign/budget.json'))
            self.rs = self.case / ('repair-' + str(ordinal)); self.rpath = self.case / ('repair-' + str(ordinal) + '.json')
            self.save_repair()
            new_text = '// synthetic repair ' + str(ordinal) + '\n'
            def post(endpoint, payload):
                if endpoint == 'responses/input_tokens': return {'input_tokens': 100}
                answer = copy.deepcopy(self.answer)
                answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Synthetic deeper lineage fixture.',
                    'edits': [{'path': 'src/feature.js', 'old': old_text, 'new': new_text}]})
                return answer
            result = self.repaired(self.repaired()['intentSha256'], transport=post)
            self.assertEqual(result['status'], 'candidate-prepared')
            self.assertEqual(result['originalCandidateReceipt'], previous['candidateReceipt'])
            self.assertEqual(self.repaired(verify_only=True), result)
            with patch.object(publication, 'ROOT', self.installed):
                publication.source_binding({'source': {**result['source'],
                    'git': {'path': self.wc['source']['git'], 'sha256': self.wc['source']['gitSha256']}},
                    'evidence': {'candidate': result['candidateReceipt']},
                    'candidateVerification': {'producerKind': 'repair-candidate', 'repairConfig': self.pin(self.rpath)}})
            previous = result; old_text = new_text
        self.assertFalse(self.container_calls)


for name in dir(fixtures.RepairTests):
    if name.startswith('test_') and name not in SecondRepairTests.__dict__:
        setattr(SecondRepairTests, name, None)

if __name__ == '__main__': unittest.main()
