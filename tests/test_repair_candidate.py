"""Real installed verifiers/Git/accounting; synthetic provider, no project execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_candidate_review as source_review
import flow_repair_candidate as repair
import flow_requirement_intake as intake
import flow_resume_candidate as resume
import flow_run as original_run
import flow_workflow as work
import flow_workflow_validation as phase
from aim_ci.owned_paths import Refused
import test_workflow_validation as fixtures


class RepairTests(fixtures.ContinuationTests):
    def citation(self, packet, path='README.md', start=1, end=1):
        if path == 'web/package.json' and any(s['path'] == 'package.json' for s in packet['sources']):
            path = 'package.json'
        return super().citation(packet, path, start, end)

    def assessment(self, packet):
        value = super().assessment(packet)
        if any(s['path'] == 'package.json' for s in packet['sources']):
            for step in value['steps']:
                step['workingDirectory'] = '.'
                step['sources'] = [self.citation(packet, 'package.json')]
        return value

    def workflow_post(self, endpoint, payload):
        if endpoint == 'responses/input_tokens' or payload['text']['format']['name'] == 'project_onboarding_compact_v2':
            return super().workflow_post(endpoint, payload)
        self.wcalls.append(endpoint)
        answer = copy.deepcopy(self.answer)
        answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Update feature display name.',
            'edits': [{'path': 'src/feature.js', 'old': 'custom-stack', 'new': 'improved-stack'}]})
        return answer

    def setUp(self):
        super().setUp()
        for module in (repair, resume, original_run):
            p = patch.object(module, 'ROOT', self.installed); p.start(); self.addCleanup(p.stop)
        (self.checkout / 'src').mkdir()
        (self.checkout / 'src/feature.js').write_text('export const projectDisplayName = "custom-stack";\n')
        (self.checkout / 'scripts/legacy-test.mjs').write_text('assert.equal(selector, "old-selector");\n')
        (self.checkout / 'package.json').write_text(json.dumps({'engines': {'node': '>=24'},
            'scripts': {'test': 'node scripts/legacy-test.mjs'}}) + '\n')
        (self.checkout / 'package-lock.json').write_text('{"lockfileVersion":3}\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Repair fixture source')
        self.commit = self.git_run('rev-parse', 'HEAD'); self.wc['source']['commit'] = self.commit
        Path(self.wc['requirement']['path']).write_text('Change projectDisplayName in src/feature.js from custom-stack to improved-stack. Preserve all tests and business assertions.')
        self.wc['requirement'] = self.pin(Path(self.wc['requirement']['path']))
        self.catalog['resources'][1]['capabilities']['lockSha256'] = self.pin(self.checkout / 'package-lock.json')['sha256']
        Path(self.wc['resourceCatalog']['path']).write_bytes(api.encoded(self.catalog))
        self.wc['resourceCatalog'] = self.pin(Path(self.wc['resourceCatalog']['path']))
        self.selected = intake.derive(self.installed, self.wc['source'], self.wc['requirement'], ['src/', 'scripts/'])
        self.wc = intake.assemble_config(self.wc, self.selected)
        self.outer = self.case / 'intake-proof'; self.outer.mkdir()
        (self.outer / 'run').mkdir()
        self.ws = self.outer / 'run/initial'
        self.wpath = self.outer / 'workflow-config.json'
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
        self.produced = self.grant()
        self.assertEqual(self.produced['status'], 'candidate-prepared')
        self.assertNotIn('scripts/legacy-test.mjs', self.wc['editablePaths'])
        (self.outer / 'intake.json').write_bytes(api.encoded(self.selected))
        base = self.case / 'original-base.json'; base.write_bytes(api.encoded(self.wc))
        old_grant = json.loads((self.ws / 'grant.json').read_bytes())
        run_intent, _ = original_run.prepare(self.wpath, self.wsha, self.outer / 'run')
        for name, value in [('grant.json', {'intent': run_intent, 'intentSha256': api.digest(api.encoded(run_intent))}),
                            ('initial-dispatch.json', old_grant['intent']), ('initial-result.json', self.produced)]:
            (self.outer / 'run' / name).write_bytes(api.encoded(value))
        grant_intent = {'format': 'aim-flow-intake-local-run', 'schemaVersion': 1,
            'state': str(self.outer),
            'baseConfig': self.pin(base), 'intake': self.pin(self.outer / 'intake.json'),
            'workflowConfig': self.pin(self.wpath), 'runIntent': run_intent}
        original_grant = {'intent': grant_intent, 'intentSha256': api.digest(api.encoded(grant_intent))}
        for name in ('intake-grant.json', 'run-dispatch.json'):
            (self.outer / name).write_bytes(api.encoded(original_grant))
        self.pc.update(workflowConfig=self.pin(self.wpath), workflowState=str(self.ws), refineExecutionPlan=True)
        self.save_phase_config()
        self.review_body.update(findings=[{'id': 'F1', 'description': 'Preexisting stale test selector.',
            'severity': 'medium', 'requirementIds': ['criterion-001'],
            'sources': [{'path': 'candidate/scripts/legacy-test.mjs', 'startLine': 1, 'endLine': 1}]}],
            coverage=[{'requirementId': 'criterion-001', 'status': 'unverified', 'analysis': 'Not established.',
                'sources': [{'path': 'candidate/src/feature.js', 'startLine': 999, 'endLine': 999}]}],
            workspacePolicy={'generatedOutputs': [], 'retainedArchiveInputs': [{'path': 'web/bundle.zip', 'reason': 'Fixture input'}], 'unknowns': []},
            executionRefinement=[{'stepId': key, 'kind': 'finite-local', 'evidenceSteps': [],
                'executionDependsOn': deps, 'analysis': 'Synthetic finite stage.',
                'sources': [{'path': 'candidate/package.json', 'startLine': 1, 'endLine': 1}]}
                for key, deps in [('install', []), ('test', ['install'])]])
        preview = self.phase()
        result = self.phase(preview['intentSha256'], self.review_post)
        self.rejected = self.ps / 'review-attempt'
        self.assertEqual(api.status(self.rejected)['status'], 'review-rejected', result)
        # Diagnostics preserve the actual provider wire body (legacy endLine or
        # current lineCount), not the fixture's pre-serialization authoring form.
        response = json.loads((self.rejected / 'response.json').read_bytes())
        self.review_body = json.loads(response['output'][0]['content'][0]['text'])
        self.assertFalse(self.container_calls)
        self.rs = self.case / 'repair'; self.rpath = self.case / 'repair-config.json'
        self.rc = {'format': repair.FORMAT, 'schemaVersion': 1, 'manifestSha256': self.wc['manifestSha256'],
            'originalProducer': {'workflowConfig': self.pin(self.wpath), 'workflowState': str(self.ws),
                'producerController': {'path': str(self.installed), 'manifestSha256': self.wc['manifestSha256']}},
            'intakeAuthority': {'intake': self.pin(self.outer / 'intake.json'),
                'grant': self.pin(self.outer / 'intake-grant.json'), 'dispatch': self.pin(self.outer / 'run-dispatch.json')},
            'rejectedReview': {'state': str(self.rejected),
                'files': {name: self.pin(self.rejected / name)['sha256'] for name in repair.REVIEW_FILES}},
            'campaignBudget': self.pin(Path(self.wc['campaignDirectory']) / 'budget.json'),
            'python': self.pin(Path(sys.executable).resolve()), 'producerVerificationTimeoutSeconds': 900,
            'maxOutputTokens': 8192, 'maxOperationNanoUsd': 1_000_000_000, 'timeoutSeconds': 900}
        self.save_repair()
        self.before = {str(p): p.read_bytes() for root in (self.ws, self.outer, self.rejected)
                       for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}
        self.repair_calls = []

    def save_repair(self):
        self.rpath.write_bytes(api.encoded(self.rc)); self.rsha = self.pin(self.rpath)['sha256']

    def repaired(self, authorization=None, **options):
        return repair.run(self.rpath, self.rsha, self.rs, authorization, **options)

    def repair_post(self, endpoint, payload):
        self.repair_calls.append(endpoint)
        if endpoint == 'responses/input_tokens': return {'input_tokens': 100}
        data = json.loads(payload['input'])
        self.assertEqual(data['rejectedReviewDiagnostics']['candidateReview'], None)
        self.assertEqual(data['rejectedReviewDiagnostics']['body'], self.review_body)
        self.assertIn('scripts/legacy-test.mjs', data['editableSources'])
        self.assertNotIn('tools', payload)
        answer = copy.deepcopy(self.answer)
        answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Source-checked selector repair; acceptance remains unknown.',
            'edits': [{'path': 'scripts/legacy-test.mjs', 'old': 'old-selector', 'new': 'new-selector'}]})
        return answer

    def grant_repair(self):
        return self.repaired(self.repaired()['intentSha256'], transport=self.repair_post)

    def test_repair_descendant_keeps_baseline_rejection_and_original_grants(self):
        preview = self.repaired()
        self.assertFalse(self.rs.exists())
        self.assertEqual(preview['intent']['scope']['editablePaths'], ['scripts/legacy-test.mjs'])
        with self.assertRaises(Refused): self.repaired('0' * 64, transport=self.repair_post)
        result = self.repaired(preview['intentSha256'], transport=self.repair_post)
        self.assertEqual(result['status'], 'candidate-prepared', result)
        candidate = Path(result['candidate']['checkout'])
        self.assertIn('new-selector', (candidate / 'scripts/legacy-test.mjs').read_text())
        self.assertIn('improved-stack', (candidate / 'src/feature.js').read_text())
        parent = work.git(self.wc, candidate, 'rev-parse', 'HEAD^').decode().strip()
        self.assertEqual(parent, self.produced['candidate']['commit'])
        self.assertEqual(result['source']['baseSha'], self.commit)
        self.assertEqual(self.repair_calls, ['responses/input_tokens', 'responses'])
        self.assertFalse(result['projectCommandsExecuted']); self.assertFalse(result['publicationAuthorized'])
        self.assertEqual(result['diagnosticAuthority'], 'none')
        self.assertEqual(result['onboardingReuse'], {'status': 'source-evidence-unchanged', 'changedEvidencePaths': []})
        self.assertTrue(result['freshIndependentReviewRequired'])
        self.assertEqual(self.before, {p: Path(p).read_bytes() for p in self.before})
        feedback = json.loads((self.rs / 'feedback.json').read_bytes())
        self.assertEqual(feedback['body'], self.review_body)
        with self.assertRaises(Refused):
            source_review.verify_evidence(self.pin(self.rs / 'feedback.json'), self.pin(self.rejected / 'packet.json'))
        self.assertEqual([r['reference']['startLine'] for r in feedback['citations'] if r['status'] == 'invalid'], [999])
        self.assertEqual(self.repaired(verify_only=True), result)

    def test_installed_fixed_verifier_and_validation_bridge_are_read_only(self):
        result = self.grant_repair()
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.rs.rglob('*') if p.is_file()}
        argv = [str(Path(sys.executable).resolve()), '-IBS', str(self.installed / 'aim-flow.py'), 'repair-candidate',
                '--config', str(self.rpath), '--config-sha256', self.rsha, '--state', str(self.rs), '--verify']
        proc = subprocess.run(argv, env=self.env, cwd=self.case, capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stderr); self.assertFalse(proc.stderr)
        self.assertEqual(json.loads(proc.stdout), result)
        bridge = {**self.pc, 'workflowConfig': result['derivedWorkflowConfig'], 'workflowState': str(self.rs),
            'producerController': self.rc['originalProducer']['producerController'],
            'producerKind': 'repair-candidate', 'repairConfig': self.pin(self.rpath)}
        path = self.case / 'repair-validation.json'; path.write_bytes(api.encoded(bridge))
        prepared = phase.inputs(path, self.pin(path)['sha256'], self.case / 'repaired-validation')
        self.assertEqual(prepared[3], result)
        self.assertEqual(prepared[3]['onboardingReuse'], {'status': 'source-evidence-unchanged', 'changedEvidencePaths': []})
        self.assertEqual(prepared[-1]['instanceController'], self.rc['originalProducer']['producerController'])
        self.assertIn('scripts/legacy-test.mjs', prepared[1]['reviewSources'])
        self.assertEqual(prepared[1]['source']['commit'], self.commit)
        self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.rs.rglob('*') if p.is_file()})
        self.assertFalse((self.case / 'repaired-validation').exists())

    def test_outside_scope_and_weakened_arbitrary_paths_are_rejected(self):
        def attack(endpoint, payload):
            answer = self.repair_post(endpoint, payload)
            if endpoint == 'responses':
                answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Change scope',
                    'edits': [{'path': 'package.json', 'old': 'node scripts/legacy-test.mjs', 'new': 'true'}]})
            return answer
        result = self.repaired(self.repaired()['intentSha256'], transport=attack)
        self.assertEqual(result['status'], 'blocked'); self.assertFalse((self.rs / 'candidate').exists())
        self.assertEqual(result['attemptResult']['status'], 'proposal-rejected')
        self.assertEqual(self.repaired(transport=lambda *a: self.fail('replayed')), result)

    def test_unknown_provider_and_private_preparation_never_replay(self):
        def unknown(endpoint, payload):
            self.repair_calls.append(endpoint)
            if endpoint == 'responses': raise TimeoutError('Synthetic provider outcome unknown')
            return {'input_tokens': 100}
        preview = self.repaired(); result = self.repaired(preview['intentSha256'], transport=unknown)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.repaired(transport=lambda *a: self.fail('replayed')), result)
        self.assertFalse((self.rs / 'candidate').exists())
        self.assertEqual(len(self.repair_calls), 2)

    def test_interrupted_candidate_creation_is_not_recreated(self):
        with patch.object(work, 'create_candidate', side_effect=OSError('Synthetic interrupted Git')) as called:
            with self.assertRaises(OSError): self.grant_repair()
            result = self.repaired(transport=lambda *a: self.fail('replayed'))
            self.assertEqual(result['status'], 'blocked'); self.assertEqual(called.call_count, 1)
        with self.assertRaises(Refused): self.repaired(verify_only=True)

    def test_consumed_missing_inputs_and_source_drift_refused(self):
        result = self.grant_repair()
        for name in ('feedback.json', 'implementation-request.json', 'implementation-config.json', 'repair.lock'):
            with self.subTest(name=name):
                p = self.rs / name; raw = p.read_bytes(); p.unlink()
                with self.assertRaises((Refused, OSError)): self.repaired(verify_only=True)
                self.assertFalse(p.exists()); p.write_bytes(raw); p.chmod(0o600)
        p = Path(result['candidate']['checkout']) / 'scripts/legacy-test.mjs'; p.write_text('changed')
        with self.assertRaisesRegex(Refused, 'candidate content changed'): self.repaired(verify_only=True)
        self.assertEqual(len(self.repair_calls), 2)

    def test_rejected_response_or_accounting_drift_never_becomes_authority(self):
        response = self.rejected / 'response.json'; raw = response.read_bytes()
        response.write_bytes(raw + b' ')
        with self.assertRaises(Refused): self.repaired()
        response.write_bytes(raw)
        budget = Path(self.rc['campaignBudget']['path']); value = json.loads(budget.read_bytes())
        value['requests'].pop(); budget.write_bytes(api.encoded(value))
        self.rc['campaignBudget'] = self.pin(budget); self.save_repair()
        with self.assertRaisesRegex(Refused, 'completed accounting'): self.repaired()
        self.assertFalse(self.rs.exists()); self.assertFalse(self.repair_calls)

    def test_unknown_producer_kind_is_refused_not_an_arbitrary_command(self):
        self.grant_repair()
        config = {**self.pc, 'producerController': self.rc['originalProducer']['producerController'],
            'producerKind': 'sh', 'repairConfig': self.pin(self.rpath)}
        with self.assertRaisesRegex(Refused, 'Unknown repair producer'):
            phase.verified_producer(config, self.wc, self.rs)

    def test_original_directory_authority_cannot_be_silently_expanded(self):
        path = self.outer / 'intake.json'
        changed = json.loads(path.read_bytes()); changed['allowedDirectories'].append('outside/')
        path.write_bytes(api.encoded(changed))
        self.rc['intakeAuthority']['intake'] = self.pin(path); self.save_repair()
        with self.assertRaisesRegex(Refused, 'Original intake does not bind'):
            self.repaired()
        self.assertFalse(self.rs.exists()); self.assertFalse(self.repair_calls)

    def test_repair_output_cannot_overlap_any_original_outer_evidence(self):
        self.rs = self.outer / 'new-repair'
        with self.assertRaisesRegex(Refused, 'disjoint from original producer'): self.repaired()
        self.assertFalse(self.rs.exists())

    def test_inherited_original_assessment_changes_cannot_be_relabelled_unchanged(self):
        unchanged = repair.onboarding_reuse(self.wc, self.produced['projectInstance'], self.produced['candidate'])
        self.assertEqual(unchanged, {'status': 'source-evidence-unchanged', 'changedEvidencePaths': []})
        (self.checkout / 'README.md').write_text('Changed assessed source, unrelated to the repair.\n')
        self.git_run('add', 'README.md'); self.git_run('commit', '-qm', 'Synthetic assessed evidence drift')
        target = {'checkout': str(self.checkout), 'commit': self.git_run('rev-parse', 'HEAD'),
                  'tree': self.git_run('rev-parse', 'HEAD^{tree}')}
        reuse = repair.onboarding_reuse(self.wc, self.produced['projectInstance'], target)
        self.assertEqual(reuse, {'status': 'fresh-candidate-assessment-required', 'changedEvidencePaths': ['README.md']})
        with patch.object(phase, 'review_candidate', side_effect=AssertionError('No review or execution may run')):
            result = phase.advance({}, self.wc, self.ws, {'onboardingReuse': reuse}, {}, {}, {}, {}, self.rs, None, None)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['onboardingReuse'], reuse)

    def test_request_projects_resource_candidates_but_grant_retains_local_plan(self):
        parts = repair.prepare(self.rpath, self.rsha, self.rs)
        scope, payload, intent = parts[6], parts[9], parts[10]
        self.assertIn(str(self.spec_path), json.dumps(scope['unresolvedPlan']))
        self.assertNotIn(str(self.spec_path), payload['input'])
        self.assertNotIn('prepared synthetic runtime', payload['input'])
        self.assertEqual(intent['scope']['unresolvedPlan'], scope['unresolvedPlan'])
        disclosed = json.loads(payload['input'])['scope']['unresolvedPlan']
        self.assertEqual(disclosed['unsupportedSteps'], scope['unresolvedPlan']['unsupportedSteps'])
        self.assertEqual(disclosed['agentUnknowns'], scope['unresolvedPlan']['agentUnknowns'])
        self.assertTrue(any(row['candidateCapabilities'] for row in disclosed['resourceNeeds']))
        self.assertIn('not measured runtime qualification', disclosed['qualification'])
        self.assertFalse(self.rs.exists()); self.assertFalse(self.repair_calls)

    def test_unrecorded_partial_child_and_forged_rejection_are_refused(self):
        self.rs.mkdir(mode=0o700)
        with self.assertRaises((Refused, OSError)): self.repaired()
        self.assertFalse((self.rs / 'grant.json').exists())
        self.rs.rmdir()
        result_path = self.rejected / 'result.json'
        forged = json.loads(result_path.read_bytes()); forged['reservation']['state'] = str(self.case / 'another')
        result_path.write_bytes(api.encoded(forged))
        self.rc['rejectedReview']['files']['result.json'] = self.pin(result_path)['sha256']; self.save_repair()
        with self.assertRaisesRegex(Refused, 'completed accounting'): self.repaired()
        self.assertFalse(self.rs.exists()); self.assertFalse(self.repair_calls)

    def test_fully_repinned_shadow_authority_is_not_the_original_grant(self):
        shadow = self.case / 'shadow'; shadow.mkdir()
        selected = intake.derive(self.installed, self.wc['source'], self.wc['requirement'], ['src/', 'scripts/', 'unapproved/'])
        self.assertEqual(selected['selection'], self.selected['selection'])
        (shadow / 'intake.json').write_bytes(api.encoded(selected))
        proof = json.loads((self.outer / 'intake-grant.json').read_bytes())
        proof['intent']['intake'] = self.pin(shadow / 'intake.json')
        proof['intentSha256'] = api.digest(api.encoded(proof['intent']))
        for name in ('intake-grant.json', 'run-dispatch.json'):
            (shadow / name).write_bytes(api.encoded(proof))
        self.rc['intakeAuthority'] = {'intake': self.pin(shadow / 'intake.json'),
            'grant': self.pin(shadow / 'intake-grant.json'), 'dispatch': self.pin(shadow / 'run-dispatch.json')}
        self.save_repair()
        with self.assertRaisesRegex(Refused, 'genuine canonical outer state'): self.repaired()
        self.assertEqual(self.before, {p: Path(p).read_bytes() for p in self.before})

    def test_manually_substituted_proposal_cannot_be_a_flow_candidate(self):
        with patch.object(repair, 'candidate_inputs', side_effect=RuntimeError('Synthetic stop before candidate marker')):
            with self.assertRaises(RuntimeError): self.grant_repair()
        attempt = self.rs / 'implementation-attempt'
        response_before = (attempt / 'response.json').read_bytes()
        budget = Path(self.wc['campaignDirectory']) / 'budget.json'; ledger_before = budget.read_bytes()
        proposal_path = attempt / 'proposal.json'; proposal = json.loads(proposal_path.read_bytes())
        proposal['edits'][0]['new'] = 'manual-forgery'; proposal_path.write_bytes(api.encoded(proposal))
        result_path = attempt / 'result.json'; result = json.loads(result_path.read_bytes())
        result['evidenceSha256']['proposal.json'] = self.pin(proposal_path)['sha256']; result_path.write_bytes(api.encoded(result))
        with self.assertRaisesRegex(Refused, 'exact retained provider response'): self.repaired()
        with self.assertRaisesRegex(Refused, 'exact retained provider response'): self.repaired(verify_only=True)
        self.assertFalse((self.rs / 'candidate').exists())
        self.assertEqual(response_before, (attempt / 'response.json').read_bytes())
        self.assertEqual(ledger_before, budget.read_bytes())

    def test_repair_cost_cap_stops_before_reservation_or_generation(self):
        self.rc['maxOperationNanoUsd'] = 1; self.save_repair()
        ledger = (Path(self.wc['campaignDirectory']) / 'budget.json').read_bytes()
        result = self.grant_repair()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['attemptResult']['status'], 'preflight-refused')
        self.assertEqual(self.repair_calls, ['responses/input_tokens'])
        self.assertFalse((self.rs / 'implementation-attempt/reservation.json').exists())
        self.assertEqual(ledger, (Path(self.wc['campaignDirectory']) / 'budget.json').read_bytes())


# Reuse fixture setup, not its many complete-workflow tests.
for name in dir(fixtures.ContinuationTests):
    if name.startswith('test_') and name not in RepairTests.__dict__:
        setattr(RepairTests, name, None)


class DiagnosticUnitTests(unittest.TestCase):
    def test_public_scope_strips_opaque_resource_data_without_mutating_local_plan(self):
        need = {'step': 'browser-test', 'kind': 'browser-runtime', 'status': 'configured-candidate',
            'requirements': ['node >=24', 'Browser acceptance still needs execution'],
            'sources': [{'path': 'package.json', 'startLine': 1, 'endLine': 1}],
            'opaqueNeedMetadata': 'opaque-NEED-PRIVATE',
            'candidates': [{'id': 'opaque-ID-PRIVATE', 'kind': 'browser-runtime',
                'identity': '/private/host-IDENTITY', 'qualification': 'operator-PROSE-PRIVATE',
                'executionSpec': {'path': '/private/host-template.json', 'backend': 'container'},
                'opaque': 'opaque-RESOURCE-PRIVATE', 'capabilities': {'nodeVersion': '24.22.2',
                    'npmVersion': '11.19.1', 'platform': 'linux', 'architecture': 'arm64',
                    'browserPackages': {'playwright': '1.58.2', 'opaque': 'browser-PRIVATE'},
                    'utilities': {'git': '/private/host-git'}, 'opaque': 'capability-PRIVATE'}}]}
        scope = {'editablePaths': ['scripts/test.js'], 'allowedDirectories': ['scripts/'],
            'sourceBoundFindingPaths': ['scripts/test.js'], 'committedScriptEntrypoints': ['scripts/test.js'],
            'protectedPaths': ['package.json'], 'excluded': [], 'qualification': 'New explicit grant',
            'commandContext': {'opaque': '/private/context-not-disclosed'},
            'unresolvedPlan': {'resourceNeeds': [need], 'unsupportedSteps': [{'step': 'manual-review',
                'reason': 'Manual prerequisite remains unresolved'}], 'agentUnknowns': ['Acceptance not established']}}
        before = copy.deepcopy(scope); disclosed = repair.public_scope(scope); wire = json.dumps(disclosed)
        for private in ('/private/', 'PRIVATE', 'executionSpec', 'identity', 'opaqueNeedMetadata', 'utilities'):
            self.assertNotIn(private, wire)
        self.assertEqual(scope, before)
        public_need = disclosed['unresolvedPlan']['resourceNeeds'][0]
        for key in ('step', 'kind', 'status', 'requirements', 'sources'):
            self.assertEqual(public_need[key], need[key])
        self.assertEqual(public_need['candidateCapabilities'], [{'kind': 'browser-runtime', 'capabilities': {
            'nodeVersion': '24.22.2', 'npmVersion': '11.19.1', 'platform': 'linux', 'architecture': 'arm64',
            'browserPackages': {'playwright': '1.58.2'}}}])
        for key in ('unsupportedSteps', 'agentUnknowns'):
            self.assertEqual(disclosed['unresolvedPlan'][key], scope['unresolvedPlan'][key])

    def test_count_protocol_is_pinned_not_reinterpreted_or_promoted(self):
        body = {'findings': [{'sources': [{'path': 'candidate/a.js', 'startLine': 1, 'lineCount': 1},
            {'path': 'candidate/a.js', 'startLine': 1, 'lineCount': 2}]}], 'unknowns': ['Unverified']}
        response = {'model': api.MODEL, 'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': json.dumps(body)}]}]}
        packet = {'citationProtocol': 'start-line-count-v1',
            'sources': [{'path': 'candidate/a.js', 'text': 'x\n', 'sha256': api.digest(b'x\n'), 'lineCount': 1}]}
        value = repair.diagnostics(response, packet, 'a'*64, 'b'*64, {'class': 'Refused'})
        self.assertEqual(value['body'], body)
        self.assertEqual([r['status'] for r in value['citations']], ['source-location-verified', 'invalid'])
        self.assertIsNone(value['candidateReview'])
        del packet['citationProtocol']
        legacy = repair.diagnostics(response, packet, 'a'*64, 'b'*64, {'class': 'Refused'})
        self.assertTrue(all(r['status'] == 'invalid' for r in legacy['citations']))

    def test_malformed_and_invalid_references_preserved_without_clamping(self):
        body = {'summary': 'Unverified claim', 'findings': [{'sources': [
            {'path': 'candidate/src/a.js', 'startLine': 9, 'endLine': 12},
            {'path': 'candidate/src/a.js'}, 'not a reference']}],
            'unknowns': ['All uncertainty is preserved'], 'unexpectedClaim': {'preserve': True}}
        response = {'model': api.MODEL, 'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': json.dumps(body)}]}]}
        packet = {'sources': [{'path': 'candidate/src/a.js', 'text': 'one line\n', 'sha256': api.digest(b'one line\n')}]}
        value = repair.diagnostics(response, packet, 'a'*64, 'b'*64, {'class': 'Refused'})
        self.assertEqual(value['body'], body)
        self.assertEqual(len(value['citations']), 3)
        self.assertTrue(all(c['status'] == 'invalid' for c in value['citations']))
        self.assertEqual(value['candidateReview'], None)
        self.assertFalse(value['executionAuthorized'])

    def test_scope_outside_original_directory_and_protected_definition_refused(self):
        instance = {'packet': {'sources': [{'path': 'scripts/protected.js'}]}}
        initial = {'projectInstance': {'path': '/synthetic/instance.json', 'sha256': 'a'*64}}
        wc = {'resourceCatalog': {'path': '/synthetic/catalog.json', 'sha256': 'b'*64}}
        feedback = {'citations': [{'status': 'source-location-verified', 'location': '/findings/0/sources/0',
                                  'reference': {'path': 'candidate/outside/test.js'}}]}
        declaration = {'sourcePaths': [], 'jobs': [{'stages': []}]}
        context = {'traversal': [{'kind': 'node-entrypoint', 'target': 'scripts/protected.js'}]}
        with patch.object(api, 'pinned', side_effect=lambda pin: api.encoded(instance if 'instance' in pin['path'] else {})), \
             patch.object(phase.derivation, 'resource_catalog', return_value={}), \
             patch.object(phase.derivation, 'derive_from_instance', return_value={'declaration': declaration}), \
             patch.object(repair.commands, 'derive', return_value=context):
            with self.assertRaisesRegex(Refused, 'original allowed directories'):
                repair.derive_scope(wc, initial, {'allowedDirectories': ['scripts/']}, feedback,
                                    {'workspaceContext': {'stages': []}}, object())

    def test_unresolved_assessment_predecessor_is_diagnostic_context_not_execution(self):
        instance = {'packet': {'sources': []}}
        initial = {'projectInstance': {'path': '/synthetic/instance.json', 'sha256': 'a'*64}}
        wc = {'resourceCatalog': {'path': '/synthetic/catalog.json', 'sha256': 'b'*64}}
        feedback = {'citations': [{'status': 'source-location-verified', 'location': '/findings/0/sources/0',
                                  'reference': {'path': 'candidate/scripts/test.js'}}]}
        declaration = {'sourcePaths': [], 'jobs': [{'stages': []}]}
        derived = {'declaration': declaration,
            'resourceNeeds': [{'kind': 'assessment-predecessor', 'requirements': ['persistent-preview'], 'status': 'unresolved'}],
            'unsupportedSteps': [{'step': 'persistent-preview', 'reason': 'persistent/manual service'}],
            'agentUnknowns': ['Browser acceptance unresolved']}
        with patch.object(api, 'pinned', side_effect=lambda pin: api.encoded(instance if 'instance' in pin['path'] else {})), \
             patch.object(phase.derivation, 'resource_catalog', return_value={}), \
             patch.object(phase.derivation, 'derive_from_instance', return_value=derived), \
             patch.object(phase, 'compose', side_effect=AssertionError('Repair must not compose execution')), \
             patch.object(repair.commands, 'derive', return_value={'traversal': []}):
            scope = repair.derive_scope(wc, initial, {'allowedDirectories': ['scripts/']}, feedback,
                                        {'workspaceContext': {'stages': []}}, object())
            self.assertEqual(scope['editablePaths'], ['scripts/test.js'])
            self.assertEqual(scope['unresolvedPlan']['resourceNeeds'], derived['resourceNeeds'])


if __name__ == '__main__':
    unittest.main()
