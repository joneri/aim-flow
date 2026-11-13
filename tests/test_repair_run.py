"""One bounded repair chain: actual installed verifiers/Git, synthetic external effects."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_candidate_review as source_review
import flow_execution_review as executed
import flow_delivery_chain as delivery
import flow_github_publication as publication
import flow_repair_run as outer
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_repair_candidate as fixtures

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=')


class BoundedRepairRunTests(fixtures.RepairTests):
    def grant(self, transport=None):
        # Choose artifact-report headroom before the original fixture producer
        # consumes its resource catalog; no retained configuration is upgraded.
        self.spec['outputBytes'] = 32_000_000
        self.spec_path.write_bytes(api.encoded(self.spec))
        for row in self.catalog['resources']:
            if 'executionSpec' in row: row['executionSpec'].update(self.pin(self.spec_path))
        catalog = Path(self.wc['resourceCatalog']['path'])
        catalog.write_bytes(api.encoded(self.catalog)); self.wc['resourceCatalog'] = self.pin(catalog)
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
        return super().grant(transport)

    def setUp(self):
        super().setUp()
        for module in (outer, executed):
            p = patch.object(module, 'ROOT', self.installed); p.start(); self.addCleanup(p.stop)
        self.fs = self.case / 'bounded-repair'
        self.fpath = self.case / 'bounded-repair.json'
        self.fc = {'format': outer.FORMAT, 'schemaVersion': 1,
            'manifestSha256': self.rc['manifestSha256'], 'repairConfig': self.pin(self.rpath),
            'reviewMaxOutputTokens': 8192, 'reviewMaxOperationNanoUsd': 1_000_000_000,
            'executionMaxOutputTokens': 8192, 'executionMaxOperationNanoUsd': 1_000_000_000}
        self.save_outer()
        self.ps = self.fs / 'reviewed/validation'
        self.fresh_review = copy.deepcopy(self.review_body)
        self.fresh_review.update(findings=[], unknowns=[], coverage=[{'requirementId': 'criterion-001',
            'status': 'unverified', 'analysis': 'Fresh source inspected; execution still required.',
            'sources': [{'path': 'candidate/src/feature.js', 'startLine': 1, 'endLine': 1}]}],
            artifactSelection=[{'path': 'results/behavior.json', 'mediaType': 'application/json',
                'reason': 'Behavior written by inspected test source',
                'sources': [{'path': 'candidate/scripts/legacy-test.mjs', 'startLine': 2, 'endLine': 2}]},
                {'path': 'results/view.png', 'mediaType': 'image/png', 'reason': 'Screenshot written by inspected test source',
                 'sources': [{'path': 'candidate/scripts/legacy-test.mjs', 'startLine': 3, 'endLine': 3}]}])
        self.acceptance_packets = []
        self.review_generations = []
        self.execution_defect = False

    def save_outer(self):
        self.fpath.write_bytes(api.encoded(self.fc)); self.fsha = self.pin(self.fpath)['sha256']

    def bounded(self, authorization=None, **options):
        return outer.run(self.fpath, self.fsha, self.fs, authorization, **options)

    def post(self, endpoint, payload):
        name = payload.get('text', {}).get('format', {}).get('name')
        if name.startswith('candidate_workspace_'):
            self.review_generations.append(endpoint)
            with patch.object(self, 'review_body', self.fresh_review):
                return self.review_post(endpoint, payload)
        if name != 'executed_acceptance_review_v1':
            answer = self.repair_post(endpoint, payload)
            if endpoint == 'responses':
                body = json.loads(answer['output'][0]['content'][0]['text'])
                body['edits'] = [{'path': 'scripts/legacy-test.mjs',
                    'old': 'assert.equal(selector, "old-selector");\n',
                    'new': 'assert.equal(selector, "new-selector");\n'
                        'writeFileSync("results/behavior.json", JSON.stringify(observations));\n'
                        'await page.screenshot({path: "results/view.png"});\n'}]
                answer['output'][0]['content'][0]['text'] = json.dumps(body)
            return answer
        self.review_generations.append(endpoint)
        if endpoint == 'responses/input_tokens': return {'input_tokens': 200}
        packet = json.loads(payload['input'][0]['content'][0]['text'])
        self.acceptance_packets.append(packet)
        self.assertTrue(any(row['type'] == 'input_image' for row in payload['input'][0]['content']))
        citation = {'path': 'candidate/src/feature.js', 'startLine': 1, 'endLine': 1}
        artifacts = ['artifact:results/behavior.json', 'artifact:results/view.png']
        body = {'summary': 'Synthetic evidence review only; no browser executed.',
            'coverage': [{'requirementId': c['id'], 'status': 'defect' if self.execution_defect else 'satisfied',
                'analysis': 'Synthetic observations', 'evidenceKinds': ['source', 'behavior', 'visual'],
                'sources': [citation], 'evidenceIds': artifacts} for c in packet['requirement']['criteria']],
            'priorDispositions': [{'itemId': item['id'], 'status': 'resolved', 'analysis': 'Synthetic evidence resolves fixture item',
                'sources': [citation], 'evidenceIds': artifacts} for item in packet['priorOpenItems']],
            'findings': ['Synthetic defect'] if self.execution_defect else [], 'unknowns': []}
        answer = copy.deepcopy(self.answer)
        answer['output'][0]['content'][0]['text'] = json.dumps(body)
        return answer

    def execute_with_artifacts(self, root, state, intent, reader):
        result = self.execute_fake(root, state, intent, reader)
        rows = []
        for selection in intent['artifactCapture']['files']:
            raw = PNG if selection['mediaType'] == 'image/png' else b'{"observed":"synthetic"}'
            row = {**selection, 'bytes': len(raw), 'sha256': api.digest(raw), 'base64': base64.b64encode(raw).decode()}
            rows.append(row)
            result['report']['outputs']['project/' + row['path']] = {
                'type': 'file', 'mode': 0o600, 'bytes': len(raw), 'sha256': row['sha256']}
        result['report']['artifacts'] = rows
        (state / 'container.stdout').write_bytes(api.encoded(result['report']))
        result['controlFiles']['container.stdout'] = self.pin(state / 'container.stdout')['sha256']
        return result

    def finish(self):
        with patch.object(container, 'execute', side_effect=self.execute_with_artifacts):
            return self.bounded(self.bounded()['intentSha256'], transport=self.post)

    def snapshot(self):
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fs.rglob('*')
                if p.is_file() and '.git' not in p.parts}

    def assert_preserved(self):
        self.assertEqual(self.before, {p: Path(p).read_bytes() for p in self.before})
        self.assertEqual(self.history_before, {p: Path(p).read_bytes() for p in self.history_before})

    def test_exact_preview_then_complete_installed_source_free_chain_and_read_only_replay(self):
        preview = self.bounded()
        self.assertFalse(self.fs.exists()); self.assertFalse(self.repair_calls)
        self.assertEqual(preview['intent']['maximumRepairGenerations'], 1)
        self.assertEqual(preview['intent']['maximumReviewGenerations'], 2)
        self.assertEqual(preview['intent']['maximumGenerationAttempts'], 3)
        self.assertEqual(preview['intent']['maximumContainerAttempts'], 1)
        self.assertEqual(preview['intent']['requirement'], self.wc['requirement'])
        self.assertEqual(preview['intent']['repairIntent'], outer.repair.prepare(
            self.rpath, self.rsha, self.fs / 'repair')[-1])
        with self.assertRaises(Refused): self.bounded('0' * 64, transport=self.post)
        with self.assertRaises(Refused): self.bounded(transport=self.post)
        result = self.finish()
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
        self.assertEqual(result['format'], outer.FORMAT)
        for key in ('ready', 'productAccepted', 'publicationAuthorized', 'deploymentAuthorized', 'deliveryReady'):
            self.assertFalse(result[key], key)
        self.assertTrue(result['localFeatureAccepted'])
        self.assertEqual(self.repair_calls, ['responses/input_tokens', 'responses'])
        self.assertEqual(self.review_generations, ['responses/input_tokens', 'responses'] * 2)
        self.assertEqual(len(self.container_calls), 1); self.assertEqual(len(self.acceptance_packets), 1)
        state = self.fs / 'repair'
        self.assertEqual(result['repair']['candidateReceipt'], self.pin(state / 'candidate-receipt.json'))
        self.assertEqual(result['repair']['originalCandidateReceipt'], self.produced['candidateReceipt'])
        before = self.snapshot()
        self.assertEqual(self.bounded(verify_only=True), result)
        self.assertEqual(self.bounded(), result)
        argv = [str(Path(sys.executable).resolve()), '-IBS', str(self.installed / 'aim-flow.py'),
            'repair-and-validate', '--config', str(self.fpath), '--config-sha256', self.fsha,
            '--state', str(self.fs), '--verify']
        proc = subprocess.run(argv, env=self.env, cwd=self.case, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr); self.assertFalse(proc.stderr)
        self.assertEqual(json.loads(proc.stdout), result)
        self.assertEqual(self.snapshot(), before); self.assert_preserved()
        self.assertEqual(len(self.container_calls), 1); self.assertEqual(len(self.review_generations), 4)

    def test_credentials_and_all_limits_are_explicit_before_any_child_dispatch(self):
        preview = self.bounded()
        value = self.bounded(preview['intentSha256'])
        self.assertEqual(value['status'], 'awaiting-provider-credential')
        self.assertFalse((self.fs / 'repair-dispatch.json').exists())
        for options in ({'transport': self.post}, {'key_file': self.rpath}, {'authorization': preview['intentSha256']}):
            with self.assertRaises(Refused): self.bounded(verify_only=True, **options)
        original = self.fc['reviewMaxOperationNanoUsd']
        self.fc['reviewMaxOperationNanoUsd'] = original + 1; self.save_outer()
        with self.assertRaisesRegex(Refused, 'grant changed'): self.bounded()
        self.assertFalse(self.repair_calls)

    def test_review_policy_is_bound_and_forwarded_to_derived_resumed_child(self):
        self.fc['reviewEvidencePolicy'] = 'substantive-sections-and-opaque-literals-v1'
        self.save_outer()
        preview = self.bounded()
        self.assertEqual(preview['intent']['derivedResumeConfig']['reviewEvidencePolicy'], self.fc['reviewEvidencePolicy'])
        self.assertFalse(self.fs.exists()); self.assertFalse(self.repair_calls)
        self.fc['reviewEvidencePolicy'] = 'unknown'; self.save_outer()
        with self.assertRaisesRegex(Refused, 'Unsupported review evidence policy'): self.bounded()

    def proportional_finding(self, impact='follow-up', severity='low'):
        return {'id': 'F1', 'description': 'Optional naming clarity observation in the synthetic fixture.',
            'severity': severity, 'requirementIds': ['criterion-001'],
            'sources': [{'path': 'candidate/src/feature.js', 'startLine': 1, 'endLine': 1}],
            'disposition': {'impact': impact, 'title': 'Optional naming clarity',
                'rationale': 'Synthetic follow-up does not demonstrate a feature failure or invalidate the executed evidence.'}}

    def test_proportional_follow_ups_reach_delivery_without_waiving_required_evidence(self):
        self.fc['findingDispositionPolicy'] = source_review.FINDING_DISPOSITION_POLICY; self.save_outer()
        self.fresh_review['findings'] = [self.proportional_finding()]
        def post(endpoint, payload):
            answer = self.post(endpoint, payload)
            if endpoint == 'responses' and payload['text']['format']['name'] == 'executed_acceptance_review_v1':
                body = json.loads(answer['output'][0]['content'][0]['text'])
                body['findings'] = [{**self.proportional_finding(),
                    'id': 'E1', 'evidenceIds': ['artifact:results/behavior.json']}]
                answer['output'][0]['content'][0]['text'] = json.dumps(body)
            return answer
        preview = self.bounded()
        self.assertEqual(preview['intent']['derivedResumeConfig']['findingDispositionPolicy'],
                         source_review.FINDING_DISPOSITION_POLICY)
        with patch.object(container, 'execute', side_effect=self.execute_with_artifacts):
            result = self.bounded(preview['intentSha256'], transport=post)
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
        self.assertTrue(result['localFeatureAccepted'])
        self.assertEqual(len(self.container_calls), 1)
        self.assertEqual(len(result['executionReview']['followUps']), 2)
        self.assertEqual(len(result['executionReview']['findings']), 1)
        counts = (list(self.repair_calls), list(self.review_generations), len(self.container_calls))
        with patch.object(delivery, 'ROOT', self.installed):
            verified = delivery.verify_local(result['reviewedRun'], self.fc['manifestSha256'])
            body = delivery.render_body(verified)
            self.assertIn('## Non-blocking follow-ups', body)
            self.assertIn('Optional naming clarity', body)
            self.assertEqual(len(delivery.local_proof(verified)['followUps']), 2)
        self.assertEqual(counts, (self.repair_calls, self.review_generations, len(self.container_calls)))
        self.assert_preserved()

    def test_proportional_real_source_failure_still_prevents_project_execution(self):
        self.fc['findingDispositionPolicy'] = source_review.FINDING_DISPOSITION_POLICY; self.save_outer()
        self.fresh_review['findings'] = [self.proportional_finding('requirement-failure', 'high')]
        result = self.finish()
        self.assertEqual(result['status'], 'source-defects', result)
        self.assertFalse(self.container_calls)
        self.assertFalse(self.acceptance_packets)

    def test_read_mode_policy_flows_through_review_execution_acceptance_and_delivery(self):
        self.fc.update(sourceReadModePolicy=container.SOURCE_READ_MODE_POLICY, reviewMaxSourceBytes=2097152)
        self.save_outer()
        preview = self.bounded()
        self.assertEqual(preview['intent']['derivedResumeConfig']['sourceReadModePolicy'], container.SOURCE_READ_MODE_POLICY)
        self.assertEqual(preview['intent']['derivedResumeConfig']['reviewMaxSourceBytes'], 2097152)
        def fake(root, state, intent, reader):
            self.assertEqual(intent['sourceReadModePolicy'], container.SOURCE_READ_MODE_POLICY)
            source_packet = json.loads((self.ps / 'review-attempt/packet.json').read_bytes())
            self.assertEqual(source_packet['sourceByteLimit'], 2097152)
            self.assertEqual(source_packet['workspaceContext']['sourceByteLimit'], 2097152)
            result = self.execute_with_artifacts(root, state, intent, reader)
            name, source = next((n, r) for n, r in intent['sources'].items() if r['mode'] == '100644')
            result['report']['sourceReadModePolicy'] = intent['sourceReadModePolicy']
            result['report']['sourceReadModeRestorations'] = [{'path': name, 'stageIndex': 0,
                'sha256': source['sha256'], 'bytes': source['bytes'], 'fromMode': 0o600, 'toMode': 0o644}]
            (state / 'container.stdout').write_bytes(api.encoded(result['report']))
            result['controlFiles']['container.stdout'] = self.pin(state / 'container.stdout')['sha256']
            return result
        with patch.object(container, 'execute', side_effect=fake):
            result = self.bounded(preview['intentSha256'], transport=self.post)
        self.assertTrue(result['localFeatureAccepted'], result)
        self.assertEqual(len(self.container_calls), 1)
        packet = self.acceptance_packets[0]
        self.assertEqual(packet['execution']['sourceReadModePolicy'], container.SOURCE_READ_MODE_POLICY)
        self.assertIn('Flow adapter', packet['execution']['sourceReadModeRestorationActor'])
        stage = next(r['value'] for r in packet['evidence'] if r['id'] == 'stage-0')
        self.assertEqual(stage['sourceReadModeRestorations'][0]['fromMode'], 0o600)
        with patch.object(delivery, 'ROOT', self.installed):
            self.assertTrue(delivery.verify_local(result['reviewedRun'], self.fc['manifestSha256'])['review']['localFeatureAccepted'])
        self.assert_preserved()

    def test_funded_repair_source_container_and_evidence_chain_keep_original_producer_budget(self):
        old = Path(self.wc['campaignDirectory'])
        before = (old / 'budget.json').read_bytes()
        baseline = self.case / 'prior-budget.json'; baseline.write_bytes(before)
        funding = self.case / 'funding.json'
        funding.write_bytes(api.encoded({'format': 'aim-flow-campaign-continuation', 'schemaVersion': 1,
            'campaignDirectory': str(self.case / 'new-campaign'), 'limitNanoUsd': 30_000_000_000,
            'priorCampaign': {key: self.wc[key] for key in ('campaignDirectory', 'historicalBudget')},
            'priorBudgetSnapshot': self.pin(baseline)}))
        self.pc['campaignContinuation'] = self.pin(funding)
        self.pc['reviewEvidencePolicy'] = 'substantive-sections-and-opaque-literals-v1'
        self.pc['captureArtifacts'] = True
        self.review_body['artifactSelection'] = []
        self.ps = self.case / 'funded-feedback'; self.save_phase_config()
        preview = self.phase(); self.phase(preview['intentSha256'], self.review_post)
        rejected = self.ps / 'review-attempt'
        self.assertEqual(api.status(rejected)['status'], 'review-rejected', api.status(rejected))
        self.rc.update(campaignContinuation=self.pin(funding),
            rejectedReview={'state': str(rejected), 'files': {
                name: self.pin(rejected / name)['sha256'] for name in outer.repair.REVIEW_FILES}},
            campaignBudget=self.pin(self.case / 'new-campaign/budget.json'))
        self.save_repair(); self.fc['repairConfig'] = self.pin(self.rpath); self.save_outer()
        self.ps = self.fs / 'reviewed/validation'
        preview = self.bounded()
        self.assertEqual(preview['intent']['combinedHistoricalAndCampaignLimitNanoUsd'], 30_000_000_000)
        self.assertEqual(preview['intent']['derivedResumeConfig']['campaignContinuation'], self.pin(funding))
        self.assertIsNone(preview['intent']['derivedResumeConfig']['producerVerificationTimeoutSeconds'])
        result = self.finish()
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
        self.assertTrue(result['localFeatureAccepted'])
        self.assertEqual(len(self.container_calls), 1)
        self.assertEqual((old / 'budget.json').read_bytes(), before)
        ledger = api.read_existing_campaign(api.funded_config(self.wc, self.pin(funding)))
        self.assertEqual(len(ledger['requests']), 4)
        self.assertTrue(all(row['status'] == 'completed' for row in ledger['requests']))
        self.assertEqual(self.bounded(verify_only=True), result)
        repaired = json.loads((self.fs / 'repair-result.json').read_bytes())
        with patch.object(publication, 'ROOT', self.installed):
            declaration = {'source': {**repaired['source'], 'git': {
                    'path': self.wc['source']['git'], 'sha256': self.wc['source']['gitSha256']}},
                'evidence': {'candidate': repaired['candidateReceipt']},
                'candidateVerification': {'controller': {'path': str(self.installed),
                    'manifestSha256': self.fc['manifestSha256']}, 'python': self.rc['python'],
                    'config': repaired['derivedWorkflowConfig'], 'state': str(self.fs / 'repair'),
                    'producerKind': 'repair-candidate', 'repairConfig': self.pin(self.rpath),
                    'producerVerificationTimeoutSeconds': 1800}}
            self.assertEqual(publication.verify_producer(declaration)['status'], 'source-bound-flow-candidate')

    def test_repaired_resumed_acceptance_reaches_installed_publication_preview_without_new_effects(self):
        self.assertEqual(self.finish()['status'], 'local-feature-evidence-reviewed')
        state = self.fs / 'reviewed'
        spec = {'config': self.pin(self.fs / 'resume-config.json'), 'state': str(state),
                'grant': self.pin(state / 'grant.json'), 'result': self.pin(state / 'result.json')}
        counts = (list(self.repair_calls), list(self.review_generations), len(self.container_calls))
        with patch.object(delivery, 'ROOT', self.installed), patch.object(publication, 'ROOT', self.installed):
            verified = delivery.verify_local(spec, self.fc['manifestSha256'])
            destination = self.case / 'publication-inputs'; destination.mkdir()
            gh = destination / 'offline-gh'; gh.write_text('#!/bin/sh\nexit 91\n'); gh.chmod(0o700)
            declaration = {'manifestSha256': self.fc['manifestSha256'], 'localRun': spec,
                'repository': {'name': 'fixture/project', 'id': 123}, 'branch': 'flow/repaired', 'baseRef': 'main',
                'title': 'Publish verified repair', 'transport': {'gh': self.pin(gh),
                    'home': str(destination), 'configDirectory': str(destination), 'timeoutSeconds': 10}}
            body = delivery.render_body(verified)
            c = delivery.publication_config(declaration, verified, destination, body)
            (destination / 'draft-body.md').write_text(body)
            (destination / 'local-proof.json').write_bytes(api.encoded(delivery.local_proof(verified)))
            self.assertEqual(c['candidateVerification']['producerKind'], 'repair-candidate')
            self.assertEqual(c['candidateVerification']['repairConfig'], self.pin(self.rpath))
            config = destination / 'publication.json'; config.write_bytes(api.encoded(c))
            target = self.case / 'publication'
            preview = publication.run(config, self.pin(config)['sha256'], target)
            self.assertEqual(preview['status'], 'preview')
            self.assertIn('repair-candidate --verify', preview['intent']['producerVerification']['verification'])
            command = [str(Path(sys.executable).resolve()), '-IBS', str(self.installed / 'aim-flow.py'),
                'publish-github', '--config', str(config), '--config-sha256', self.pin(config)['sha256'],
                '--state', str(target), '--preview']
            proc = subprocess.run(command, cwd=self.case, env=self.env, capture_output=True, text=True, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), preview)
            self.assertFalse(target.exists())
            for key in ('producerKind', 'repairConfig'):
                changed = copy.deepcopy(c); changed['candidateVerification'].pop(key)
                with self.subTest(missing=key), self.assertRaises(Refused):
                    publication.verify_producer(changed)
            changed = copy.deepcopy(c); changed['candidateVerification']['producerKind'] = 'arbitrary'
            with self.assertRaises(Refused): publication.verify_producer(changed)
            changed = copy.deepcopy(c); changed['candidateVerification'].pop('producerKind')
            changed['candidateVerification'].pop('repairConfig')
            with self.assertRaises(Refused): publication.source_binding(changed)
            changed = copy.deepcopy(c); changed['candidateVerification']['repairConfig']['sha256'] = '0' * 64
            with self.assertRaises(Refused): publication.verify_producer(changed)
            receipt_path = Path(c['evidence']['candidate']['path']); original = receipt_path.read_bytes()
            receipt = json.loads(original); receipt['intent']['parent']['commit'] = c['source']['baseSha']
            receipt_path.write_bytes(api.encoded(receipt)); changed = copy.deepcopy(c)
            changed['evidence']['candidate'] = self.pin(receipt_path)
            with self.assertRaises(Refused): publication.source_binding(changed)
            with self.assertRaises(Refused): publication.verify_producer(changed)
            receipt_path.write_bytes(original)
        self.assertEqual(counts, (self.repair_calls, self.review_generations, len(self.container_calls)))
        self.assert_preserved()

    def test_unknown_outer_child_dispatch_is_not_replayed(self):
        preview = self.bounded()
        with patch.object(outer.repair, 'run', side_effect=OSError('Unknown child dispatch')) as called:
            with self.assertRaises(OSError): self.bounded(preview['intentSha256'], transport=self.post)
            result = self.bounded(transport=self.post)
            self.assertEqual(result['status'], 'blocked'); self.assertFalse(result['replayPermitted'])
            self.assertEqual(called.call_count, 1)
        self.assertFalse(self.repair_calls); self.assert_preserved()

    def test_unknown_repair_provider_never_replays(self):
        def unknown(endpoint, payload):
            if endpoint == 'responses': raise OSError('Synthetic unknown provider outcome')
            return self.post(endpoint, payload)
        result = self.bounded(self.bounded()['intentSha256'], transport=unknown)
        self.assertEqual(result['status'], 'blocked')
        before = list(self.repair_calls)
        self.assertEqual(self.bounded(transport=self.post)['status'], 'blocked')
        self.assertEqual(self.repair_calls, before); self.assertFalse(self.review_generations)
        self.assertFalse(self.container_calls); self.assert_preserved()

    def test_rejected_fresh_review_stops_before_container_and_does_not_repair_again(self):
        self.fresh_review['coverage'][0]['sources'][0]['startLine'] = 999
        self.fresh_review['coverage'][0]['sources'][0]['endLine'] = 999
        result = self.finish()
        self.assertEqual(result['status'], 'blocked', result)
        before = self.snapshot()
        self.assertEqual(self.bounded(transport=self.post), result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.repair_calls), 2); self.assertEqual(len(self.review_generations), 2)
        self.assertFalse(self.container_calls); self.assertFalse(self.acceptance_packets)
        self.assert_preserved()

    def test_unknown_reviewed_dispatch_stops_after_single_repair(self):
        with patch.object(outer.resume, 'run', side_effect=OSError('Unknown resume dispatch')) as called:
            with self.assertRaises(OSError): self.finish()
            result = self.bounded(transport=self.post)
            self.assertEqual(result['status'], 'blocked'); self.assertFalse(result['replayPermitted'])
            self.assertEqual(called.call_count, 1)
        self.assertEqual(len(self.repair_calls), 2); self.assertFalse(self.review_generations)
        self.assert_preserved()

    def test_unknown_container_dispatch_is_not_replayed(self):
        with patch.object(container, 'execute', side_effect=OSError('Unknown container dispatch')) as called:
            with self.assertRaises(OSError): self.bounded(self.bounded()['intentSha256'], transport=self.post)
            result = self.bounded(transport=self.post)
            self.assertEqual(result['status'], 'blocked'); self.assertEqual(called.call_count, 1)
        self.assertEqual(len(self.repair_calls), 2); self.assertEqual(len(self.review_generations), 2)
        self.assertFalse(self.acceptance_packets); self.assert_preserved()

    def test_new_grant_reuses_identical_completed_review_after_infrastructure_failure(self):
        with patch.object(container, 'execute', side_effect=OSError('Synthetic pre-job infrastructure failure')):
            with self.assertRaises(OSError):
                self.bounded(self.bounded()['intentSha256'], transport=self.post)
        previous = self.snapshot()
        old_generations = list(self.review_generations)
        cfg = json.loads((self.fs / 'resume-config.json').read_bytes())
        cfg['sourceReviewReuse'] = {'controller': {'path': str(self.installed),
                'manifestSha256': cfg['manifestSha256']},
            'config': self.pin(self.ps / 'review-config.json'), 'state': str(self.ps / 'review-attempt')}
        path = self.case / 'reused-review-run.json'; path.write_bytes(api.encoded(cfg))
        sha = self.pin(path)['sha256']; state = self.case / 'reused-review-run'
        preview = outer.resume.run(path, sha, state)
        self.assertEqual(preview['intent']['maximumGenerationAttempts'], 1)
        with patch.object(self, 'ps', state / 'validation'), patch.object(
                container, 'execute', side_effect=self.execute_with_artifacts):
            result = outer.resume.run(path, sha, state, preview['intentSha256'], transport=self.post)
        self.assertTrue(result['localFeatureAccepted'], result)
        self.assertEqual(self.review_generations, old_generations + ['responses/input_tokens', 'responses'])
        self.assertEqual(len(self.container_calls), 1)
        self.assertFalse((state / 'validation/review-attempt').exists())
        self.assertEqual(json.loads((state / 'validation/review-reuse.json').read_bytes())['newProviderCalls'], 0)
        self.assertEqual(outer.resume.run(path, sha, state, verify_only=True), result)
        spec = {'config': self.pin(path), 'state': str(state), 'grant': self.pin(state / 'grant.json'),
                'result': self.pin(state / 'result.json')}
        with patch.object(delivery, 'ROOT', self.installed):
            verified = delivery.verify_local(spec, cfg['manifestSha256'])
        self.assertEqual(verified['evidence']['sourceReview'], self.pin(self.ps / 'review-attempt/review.json'))
        self.assertEqual(previous, self.snapshot())
        self.assert_preserved()

    def test_unknown_executed_review_stops_without_second_container(self):
        with patch.object(executed, 'run', side_effect=OSError('Unknown review dispatch')) as called:
            with self.assertRaises(OSError): self.finish()
            result = self.bounded(transport=self.post)
            self.assertEqual(result['status'], 'blocked'); self.assertEqual(called.call_count, 1)
        self.assertEqual(len(self.container_calls), 1); self.assertFalse(self.acceptance_packets)
        self.assertEqual(len(self.repair_calls), 2); self.assert_preserved()

    def test_failed_container_is_terminal_without_executed_review(self):
        def failed(root, state, intent, reader):
            result = self.execute_with_artifacts(root, state, intent, reader)
            result['status'] = 'failed'; result['report']['status'] = 'failed'
            result['report']['stages'] = result['report']['stages'][:1]
            result['report']['stages'][0]['exitCode'] = 1
            (state / 'container.stdout').write_bytes(api.encoded(result['report']))
            result['controlFiles']['container.stdout'] = self.pin(state / 'container.stdout')['sha256']
            return result
        with patch.object(container, 'execute', side_effect=failed):
            result = self.bounded(self.bounded()['intentSha256'], transport=self.post)
        self.assertEqual(result['status'], 'local-validation-failed', result)
        self.assertFalse(result['localFeatureAccepted'])
        self.assertEqual(result['executionReviewState'], 'not-run')
        self.assertEqual(self.bounded(verify_only=True), result)
        self.assertEqual(len(self.repair_calls), 2); self.assertEqual(len(self.review_generations), 2)
        self.assertEqual(len(self.container_calls), 1); self.assertFalse(self.acceptance_packets)
        self.assert_preserved()

    def test_executed_defect_remains_blocked_without_another_repair(self):
        self.execution_defect = True
        result = self.finish()
        self.assertEqual(result['status'], 'executed-evidence-review-blocked', result)
        self.assertFalse(result['localFeatureAccepted']); self.assertFalse(result['productAccepted'])
        self.assertEqual(self.bounded(verify_only=True), result)
        self.assertEqual(len(self.repair_calls), 2); self.assertEqual(len(self.review_generations), 4)
        self.assertEqual(len(self.container_calls), 1); self.assert_preserved()

    def test_terminal_missing_or_changed_consumed_evidence_never_reconstructed(self):
        result = self.finish()
        targets = ('grant.json', 'resume-config.json', 'repair-dispatch.json', 'repair-completion.json',
            'repair-result.json', 'resume-dispatch.json', 'terminal-intent.json', 'result.json',
            'repair/derived-workflow-config.json', 'reviewed/execution-review-config.json')
        for name in targets:
            target = self.fs / name; raw = target.read_bytes()
            target.unlink()
            with self.assertRaises((Refused, OSError), msg=name): self.bounded(verify_only=True)
            self.assertFalse(target.exists(), name); target.write_bytes(raw)
        forged = copy.deepcopy(result); forged['publicationAuthorized'] = True
        (self.fs / 'result.json').write_bytes(api.encoded(forged))
        with self.assertRaises(Refused): self.bounded(verify_only=True)
        self.assertEqual(len(self.repair_calls), 2); self.assertEqual(len(self.review_generations), 4)
        self.assertEqual(len(self.container_calls), 1); self.assert_preserved()

    def test_terminal_paid_reservations_cannot_disappear_or_expand_scope(self):
        self.finish()
        budget = Path(self.wc['campaignDirectory']) / 'budget.json'
        raw = budget.read_bytes(); ledger = json.loads(raw)
        own = [i for i, row in enumerate(ledger['requests']) if str(self.fs) in row['state']]
        self.assertEqual(len(own), 3)
        for index in own:
            changed = copy.deepcopy(ledger); changed['requests'].pop(index)
            budget.write_bytes(api.encoded(changed))
            with self.assertRaises((Refused, OSError)): self.bounded(verify_only=True)
        budget.write_bytes(raw)
        api.Campaign(self.wc).reserve('extra', 'extra-request', 100, 100, self.fs / 'extra')
        with self.assertRaisesRegex(Refused, 'accounting scope'): self.bounded(verify_only=True)
        self.assertEqual(len(self.container_calls), 1)


# Reuse fixture behavior without rerunning all its independent repair tests.
for name in dir(fixtures.RepairTests):
    if name.startswith('test_') and name not in BoundedRepairRunTests.__dict__:
        setattr(BoundedRepairRunTests, name, None)


class DispatchMarkerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='aim-repair-markers-')
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name).resolve()
        self.child = self.state / 'child'
        self.expected = {'exact': 'bounded synthetic intent'}

    def marker(self, create):
        return outer.marker(self.state, 'dispatch.json', self.expected, self.child, create=create)

    def test_missing_dispatched_child_is_unknown_not_replayed(self):
        self.assertTrue(self.marker(True))
        before = (self.state / 'dispatch.json').read_bytes()
        self.assertFalse(self.marker(True))
        with self.assertRaisesRegex(Refused, 'outcome unknown'): self.marker(False)
        self.assertEqual((self.state / 'dispatch.json').read_bytes(), before)
        self.assertFalse(self.child.exists())

    def test_unrecorded_child_or_changed_marker_is_never_adopted(self):
        self.child.mkdir()
        with self.assertRaisesRegex(Refused, 'Unrecorded'): self.marker(True)
        self.child.rmdir()
        self.marker(True)
        self.child.mkdir()
        self.assertTrue(self.marker(False))
        (self.state / 'dispatch.json').write_bytes(api.encoded({'exact': 'forged'}))
        with self.assertRaisesRegex(Refused, 'dispatch changed'): self.marker(False)

    def test_child_acceptance_never_upgrades_outer_authority(self):
        repaired = {'candidateReceipt': {}, 'repairGrant': {}, 'originalCandidateReceipt': {}}
        reviewed = {'status': 'local-feature-evidence-reviewed', 'productAccepted': True,
                    'ready': True, 'deliveryReady': True, 'localFeatureAccepted': True}
        with patch.object(outer.work, 'pin', side_effect=lambda path: {'path': str(path), 'sha256': 'a' * 64}):
            value = outer.completion(repaired, reviewed, self.state)
        for field in ('ready', 'productAccepted', 'productAcceptanceAuthorized', 'deliveryReady',
                      'publicationAuthorized', 'deploymentAuthorized'):
            self.assertFalse(value[field], field)
        self.assertTrue(value['localFeatureAccepted'])

    def test_verification_never_creates_a_marker(self):
        with self.assertRaisesRegex(Refused, 'never dispatched'): self.marker(False)
        self.assertFalse((self.state / 'dispatch.json').exists())


if __name__ == '__main__': unittest.main()
