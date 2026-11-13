"""Completed original work, new controller, two new paid boundaries, real verifiers."""
import copy
import fcntl
import os
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_resume_candidate as resume
import flow_delivery_chain as delivery
import flow_execution_review as executed
import flow_api_delivery as api
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_flow_run_executed as fixtures


class ResumedCandidateTests(fixtures.ExecutedRunTests):
    def setup_resume(self):
        self.pc['workflowConfig'] = self.pin(self.wpath)
        self.pc['workflowState'] = str(self.ws)
        self.fs.mkdir()
        produced = self.grant()
        self.originals = {str(p): p.read_bytes() for p in self.ws.rglob('*')
                          if p.is_file() and '.git' not in p.parts}
        current = self.select_new_controller()
        for module in (resume, executed, delivery):
            patcher = patch.object(module, 'ROOT', current); patcher.start(); self.addCleanup(patcher.stop)
        self.rs = self.case / 'resumed'; self.rpath = self.case / 'resume.json'
        self.ps = self.rs / 'validation'
        self.rc = {k: self.pc[k] for k in ('schemaVersion','manifestSha256','workflowConfig',
            'workflowState','producerController','python','reviewMaxOutputTokens','reviewMaxOperationNanoUsd')}
        self.rc.update(format=resume.FORMAT, producerVerificationTimeoutSeconds=900,
            executionMaxOutputTokens=self.pc['reviewMaxOutputTokens'],
            executionMaxOperationNanoUsd=self.pc['reviewMaxOperationNanoUsd'])
        self.rpath.write_bytes(api.encoded(self.rc)); self.rsha = self.pin(self.rpath)['sha256']
        self.review_body['executionRefinement'] = [
            {'stepId': key, 'kind':'finite-local', 'evidenceSteps':[], 'executionDependsOn':deps,
             'analysis':'Candidate finite local stage inspected',
             'sources':[{'path':'candidate/web/package.json','startLine':1,'endLine':1}]}
            for key,deps in [('install',[]),('test',['install'])]]
        return produced

    def resumed(self, authorization=None, **options):
        return resume.run(self.rpath, self.rsha, self.rs, authorization, **options)

    def resume_post(self, endpoint, payload):
        if payload.get('text',{}).get('format',{}).get('name') in (
                'candidate_workspace_refinement_v1', 'candidate_workspace_refinement_v2'):
            return self.review_post(endpoint, payload)
        return self.post_all(endpoint, payload)

    def test_new_budget_is_forwarded_through_source_tests_and_executed_review_without_old_writes(self):
        self.setup_resume()
        old = Path(self.wc['campaignDirectory'])
        before = (old / 'budget.json').read_bytes()
        snapshot = self.case / 'prior-campaign-budget.json'; snapshot.write_bytes(before)
        extension = self.case / 'funding.json'
        extension.write_bytes(api.encoded({'format': 'aim-flow-campaign-continuation', 'schemaVersion': 1,
            'campaignDirectory': str(self.case / 'continued-campaign'), 'limitNanoUsd': 30_000_000_000,
            'priorCampaign': {k: self.wc[k] for k in ('campaignDirectory', 'historicalBudget')},
            'priorBudgetSnapshot': self.pin(snapshot)}))
        self.rc['campaignContinuation'] = self.pin(extension)
        self.rpath.write_bytes(api.encoded(self.rc)); self.rsha = self.pin(self.rpath)['sha256']
        preview = self.resumed()
        self.assertEqual(preview['intent']['combinedHistoricalAndCampaignLimitNanoUsd'], 30_000_000_000)
        self.assertEqual(preview['intent']['campaignContinuation'], self.pin(extension))
        self.assertFalse((self.case / 'continued-campaign').exists())
        with patch.object(container, 'execute', side_effect=self.execute_with_artifacts):
            result = self.resumed(preview['intentSha256'], transport=self.resume_post)
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
        self.assertEqual((old / 'budget.json').read_bytes(), before)
        for name in ('validation/review-config.json', 'validation/review-attempt/config.json'):
            config = json.loads((self.rs / name).read_bytes())
            self.assertEqual(config['campaignContinuation'], self.pin(extension))
            self.assertEqual(config['campaignDirectory'], str(self.case / 'continued-campaign'))
        executed_config = json.loads((self.rs / 'execution-review/config.json').read_bytes())
        self.assertEqual(executed_config['campaignContinuation'], self.pin(extension))
        executed_intent = json.loads((self.rs / 'execution-review/attempt.json').read_bytes())['intent']
        self.assertEqual(executed_intent['campaignDirectory'], str(self.case / 'continued-campaign'))
        self.assertEqual(executed_intent['campaignLimitNanoUsd'], 30_000_000_000)
        ledger = api.read_existing_campaign(api.funded_config(self.wc, self.pin(extension)))
        self.assertEqual(len(ledger['requests']), 2)
        self.assertEqual(self.resumed(verify_only=True), result)
        self.assertEqual(len(self.container_calls), 1)

    def test_real_original_producer_and_new_complete_chain_verification(self):
        produced = self.setup_resume()
        preview = self.resumed()
        self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls),4)
        self.assertEqual(preview['intent']['maximumGenerationAttempts'],2)
        self.assertEqual(preview['intent']['candidateReceipt'],produced['candidateReceipt'])
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts):
            result = self.resumed(preview['intentSha256'],transport=self.resume_post)
        self.assertEqual(result['status'],'local-feature-evidence-reviewed',result)
        self.assertEqual(result['producer']['producerController'],self.rc['producerController'])
        self.assertEqual(len(self.wcalls),8); self.assertEqual(len(self.container_calls),1)
        self.assertEqual(len(self.acceptance_packets),1)
        self.assertEqual(self.originals,{str(p):p.read_bytes() for p in self.ws.rglob('*')
                          if p.is_file() and '.git' not in p.parts})
        local = {'config':self.pin(self.rpath),'state':str(self.rs),
                 'grant':self.pin(self.rs/'grant.json'),'result':self.pin(self.rs/'result.json')}
        snapshot = {str(p):p.read_bytes() for p in self.rs.rglob('*') if p.is_file()}
        verified = delivery.verify_local(local,self.rc['manifestSha256'])
        self.assertEqual(verified['result'],result)
        self.assertEqual(snapshot,{str(p):p.read_bytes() for p in self.rs.rglob('*') if p.is_file()})
        publication = delivery.publication_config({'manifestSha256':self.rc['manifestSha256'],
            'repository':{},'branch':'fixture','baseRef':'main','localRun':local,'transport':{},'title':'Fixture'},
            verified,self.case/'delivery','Fixture body')
        self.assertEqual(publication['candidateVerification'],{
            'controller':self.rc['producerController'],'config':self.rc['workflowConfig'],
            'state':self.rc['workflowState'],'python':self.rc['python'],'producerVerificationTimeoutSeconds':900})
        self.assertEqual(len(self.wcalls),8); self.assertEqual(len(self.container_calls),1)
        forged = copy.deepcopy(result); forged['producer']['workflowState'] = str(self.case/'forged')
        (self.rs/'result.json').write_bytes(api.encoded(forged))
        with self.assertRaises(Refused):self.resumed(verify_only=True)
        (self.rs/'result.json').write_bytes(api.encoded(result))
        missing = self.rs/'execution-review-config.json'; missing.unlink()
        with self.assertRaises((Refused,OSError)):self.resumed(verify_only=True)
        self.assertFalse(missing.exists());self.assertEqual(len(self.wcalls),8)

    def test_unknown_dispatch_and_missing_inputs_never_replayed_or_repaired(self):
        self.setup_resume(); preview=self.resumed()
        with self.assertRaises(Refused):self.resumed(transport=self.resume_post)
        with patch.object(resume.validation,'run',side_effect=OSError('Unknown dispatch')) as called:
            with self.assertRaises(OSError):self.resumed(preview['intentSha256'],transport=self.resume_post)
            self.assertEqual(self.resumed(transport=self.resume_post)['status'],'blocked')
            self.assertEqual(called.call_count,1)
        target=self.rs/'validation-config.json';target.unlink()
        with self.assertRaises((Refused,OSError)):self.resumed(transport=self.resume_post)
        self.assertFalse(target.exists());self.assertEqual(len(self.wcalls),4)


class InterruptedRecoveryTests(ResumedCandidateTests):
    """Real local producer/ledger/verifiers; all provider/container effects are fixtures."""
    def setup_interrupted(self):
        self.setup_resume()
        self.current_root = resume.ROOT
        # An explicitly supplied, previously built schema-1 installation exercises
        # the actual allowlisted predecessor. Otherwise admit only this synthetic
        # test package's inventory, never an arbitrary production predecessor.
        predecessor_root = Path(os.environ.get('AIM_FLOW_TEST_PREDECESSOR', self.current_root))
        manifest = json.loads((predecessor_root / 'control-manifest.json').read_bytes())
        old_manifest = self.pin(predecessor_root / 'control-manifest.json')['sha256']
        current_manifest = self.rc['manifestSha256']
        self.rc['manifestSha256'] = old_manifest
        self.save_resume()
        with patch.object(resume, 'ROOT', predecessor_root), patch.object(resume.validation, 'ROOT', predecessor_root):
            preview = self.resumed()
            with patch.object(resume.validation, 'run', side_effect=OSError('Synthetic pre-child interruption')):
                with self.assertRaises(OSError):
                    self.resumed(preview['intentSha256'], transport=self.resume_post)
        self.previous = self.rs
        self.previous_config = self.rpath
        self.previous_raw = {p.name: p.read_bytes() for p in self.previous.iterdir()}
        self.budget = Path(self.wc['campaignDirectory']) / 'budget.json'
        self.budget_raw = self.budget.read_bytes()
        self.rc['manifestSha256'] = current_manifest
        self.rc['interruptedPredecessor'] = {
            'controller': {'path': str(predecessor_root), 'manifestSha256': old_manifest},
            'config': self.pin(self.previous_config), 'state': str(self.previous),
            'files': {name: api.digest(raw) for name, raw in self.previous_raw.items()},
            'campaignBudget': self.pin(self.budget)}
        self.rs = self.case / 'recovered'; self.ps = self.rs / 'validation'
        self.rpath = self.case / 'recovery.json'; self.save_resume()
        if 'AIM_FLOW_TEST_PREDECESSOR' not in os.environ:
            code = {n: h for n, h in manifest['files'].items() if not n.startswith('docs/')}
            fingerprint = api.digest(json.dumps(code, sort_keys=True, separators=(',', ':')).encode())
            p = patch.object(resume, 'PREDECESSOR_CODE_SHA256', fingerprint)
            p.start(); self.addCleanup(p.stop)

    def save_resume(self):
        self.rpath.write_bytes(api.encoded(self.rc)); self.rsha = self.pin(self.rpath)['sha256']

    def assert_preserved(self):
        self.assertEqual(self.previous_raw, {p.name: p.read_bytes() for p in self.previous.iterdir()})
        self.assertEqual(self.originals, {str(p): p.read_bytes() for p in self.ws.rglob('*')
                                        if p.is_file() and '.git' not in p.parts})

    def test_recovery_completes_new_chain_without_replaying_old_grant(self):
        self.setup_interrupted()
        preview = self.resumed()
        self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls), 4)
        self.assertEqual(self.budget_raw, self.budget.read_bytes())
        evidence = preview['intent']['interruptedPredecessor']
        self.assertIn('unknown', evidence['tokenPreflight'])
        old_grant = json.loads(self.previous_raw['grant.json'])
        with self.assertRaisesRegex(Refused, 'authorization'):
            self.resumed(old_grant['intentSha256'], transport=self.resume_post)
        def locked_post(endpoint, payload):
            with open(self.previous / 'run.lock', 'rb') as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue((self.rs / 'predecessor-budget.json').is_file())
            return self.resume_post(endpoint, payload)
        with patch.object(container, 'execute', side_effect=self.execute_with_artifacts):
            result = self.resumed(preview['intentSha256'], transport=locked_post)
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
        self.assertEqual(len(self.wcalls), 8); self.assertEqual(len(self.container_calls), 1)
        self.assertEqual(self.budget_raw, (self.rs / 'predecessor-budget.json').read_bytes())
        self.assertEqual(self.resumed(verify_only=True), result)
        completed_budget = self.budget.read_bytes()
        self.budget.write_bytes(self.budget_raw)
        with self.assertRaisesRegex(Refused, 'paid reservation disappeared'):
            self.resumed(verify_only=True)
        self.budget.write_bytes(completed_budget)
        # Later unrelated campaign reservations do not invalidate terminal evidence.
        campaign = api.Campaign(self.wc)
        campaign.reserve('unrelated', 'unrelated-request', 100, 100, self.case / 'unrelated')
        self.assertEqual(self.resumed(verify_only=True), result)
        self.assert_preserved()
        self.assertEqual(len(list(Path(self.wc['campaignDirectory']).glob('resume-recovery-*.json'))), 1)

    def test_active_old_lock_and_campaign_lock_refuse_without_side_effects(self):
        self.setup_interrupted()
        for path, expected in [(self.previous / 'run.lock', 'predecessor is active'),
                               (self.budget.parent / 'budget.lock', 'accounting is active')]:
            with open(path, 'rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(Refused, expected): self.resumed()
        self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls), 4)
        self.assertEqual(self.budget_raw, self.budget.read_bytes()); self.assert_preserved()

    def test_drift_missing_inputs_links_or_any_child_evidence_refuse(self):
        self.setup_interrupted()
        for name, raw in self.previous_raw.items():
            path = self.previous / name
            path.write_bytes(raw + b' ')
            with self.assertRaises(Refused): self.resumed()
            path.write_bytes(raw)
            path.unlink()
            with self.assertRaises(Refused): self.resumed()
            self.assertFalse(path.exists()); path.write_bytes(raw)
            path.unlink(); path.symlink_to(self.previous_config)
            with self.assertRaises(Refused): self.resumed()
            path.unlink(); path.write_bytes(raw)
        for name in ('validation', 'execution-review', 'review-dispatch.json', 'reservation.json'):
            path = self.previous / name; path.mkdir()
            with self.assertRaisesRegex(Refused, 'possible validation/effect'): self.resumed()
            path.rmdir()
        self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls), 4)
        self.assert_preserved()

    def test_mutated_and_repinned_old_grant_or_dispatch_still_refuse(self):
        self.setup_interrupted()
        for name in ('grant.json', 'validation-dispatch.json'):
            path = self.previous / name; value = json.loads(path.read_bytes())
            if name == 'grant.json':
                value['intent']['maximumGenerationAttempts'] = 999
                value['intentSha256'] = api.digest(api.encoded(value['intent']))
            else:
                value['executionSourcePaths'] = []
            path.write_bytes(api.encoded(value))
            self.rc['interruptedPredecessor']['files'][name] = self.pin(path)['sha256']; self.save_resume()
            with self.assertRaisesRegex(Refused, 'reconstruct'): self.resumed()
            path.write_bytes(self.previous_raw[name])
            self.rc['interruptedPredecessor']['files'][name] = self.pin(path)['sha256']; self.save_resume()
        self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls), 4)

    def test_reservation_even_without_child_excludes_recovery(self):
        self.setup_interrupted()
        campaign = api.Campaign(self.wc)
        campaign.reserve('lost-child', 'lost-request', 100, 100, self.previous / 'validation' / 'review-attempt')
        self.rc['interruptedPredecessor']['campaignBudget'] = self.pin(self.budget); self.save_resume()
        for status in ('reserved', 'unknown', 'completed'):
            ledger = json.loads(self.budget.read_bytes()); ledger['requests'][-1]['status'] = status
            self.budget.write_bytes(api.encoded(ledger))
            self.rc['interruptedPredecessor']['campaignBudget'] = self.pin(self.budget); self.save_resume()
            with self.assertRaisesRegex(Refused, 'generation reservation'): self.resumed()
        self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls), 4)

    def test_unknown_code_scope_budget_and_authority_are_not_adopted(self):
        self.setup_interrupted()
        with patch.object(resume, 'PREDECESSOR_CODE_SHA256', '0' * 64):
            with self.assertRaisesRegex(Refused, 'not recognized'): self.resumed()
        old = self.rc['reviewMaxOutputTokens']; self.rc['reviewMaxOutputTokens'] += 1; self.save_resume()
        with self.assertRaisesRegex(Refused, 'scope, limits or budget'): self.resumed()
        self.rc['reviewMaxOutputTokens'] = old; self.save_resume()
        self.budget.write_bytes(self.budget_raw + b' ')
        with self.assertRaisesRegex(Refused, 'Pinned input changed'): self.resumed()
        self.budget.write_bytes(self.budget_raw)
        authority, _ = api.authority(self.wc); original = authority.read_bytes(); authority.unlink()
        with self.assertRaises(Refused): self.resumed()
        self.assertFalse(authority.exists()); authority.write_bytes(original)
        lock = self.budget.parent / 'budget.lock'; lock.unlink()
        with self.assertRaises(Refused): self.resumed()
        self.assertFalse(lock.exists()); self.assertFalse(self.rs.exists()); self.assertEqual(len(self.wcalls), 4)

    def test_orphan_claim_and_partial_creation_are_never_repaired(self):
        self.setup_interrupted(); preview = self.resumed()
        with patch.object(resume, 'new_owned_directory', side_effect=OSError('Crash after claim')):
            with self.assertRaises(OSError): self.resumed(preview['intentSha256'])
        self.assertFalse(self.rs.exists())
        with self.assertRaisesRegex(Refused, 'consumed successor'): self.resumed()
        with self.assertRaisesRegex(Refused, 'consumed successor'): self.resumed(preview['intentSha256'])
        first = self.rs; self.rs = self.case / 'second-recovery'
        with self.assertRaisesRegex(Refused, 'consumed successor'): self.resumed()
        self.assertFalse(self.rs.exists()); self.rs = first
        # Simulate crashes at each creation boundary; the controller must never
        # supply the next file itself, even under the same exact authorization.
        self.rs.mkdir()
        grant = {key: preview[key] for key in ('intent', 'intentSha256')}
        stages = [('grant.json', api.encoded(grant)),
                  ('predecessor-budget.json', self.budget_raw), ('run.lock', b''),
                  ('initial-result.json', self.previous_raw['initial-result.json'])]
        for name, raw in stages:
            snapshot = {p.name: p.read_bytes() for p in self.rs.iterdir()}
            with self.assertRaises(Refused): self.resumed(preview['intentSha256'])
            self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.rs.iterdir()})
            (self.rs / name).write_bytes(raw)
        with self.assertRaises(Refused): self.resumed(preview['intentSha256'])
        self.assertFalse((self.rs / 'validation-config.json').exists())
        self.assertEqual(len(self.wcalls), 4); self.assert_preserved()

    def test_paused_recovery_blocks_sibling_and_missing_snapshot_repair(self):
        self.setup_interrupted(); preview = self.resumed()
        self.assertEqual(self.resumed(preview['intentSha256'])['status'], 'awaiting-provider-credential')
        first = self.rs; self.rs = self.case / 'second-recovery'
        with self.assertRaisesRegex(Refused, 'consumed successor'): self.resumed()
        self.rs = first
        snapshot = self.rs / 'predecessor-budget.json'; snapshot.unlink()
        with self.assertRaises(Refused): self.resumed(transport=self.resume_post)
        self.assertFalse(snapshot.exists()); self.assertEqual(len(self.wcalls), 4); self.assert_preserved()


for name in dir(ResumedCandidateTests):
    if name.startswith('test_') and name not in InterruptedRecoveryTests.__dict__:
        setattr(InterruptedRecoveryTests, name, None)


for name in dir(fixtures.ExecutedRunTests):
    if name.startswith('test_') and name not in ResumedCandidateTests.__dict__:
        setattr(ResumedCandidateTests,name,None)

if __name__=='__main__':unittest.main()
