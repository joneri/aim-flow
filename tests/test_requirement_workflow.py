"""Offline synthetic orchestration with real Git and existing provider/budget adapters."""
import copy
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_workflow as work
import flow_api_delivery as api
from aim_ci.owned_paths import Refused
import test_onboarding_api_delivery as fixtures


class RequirementWorkflowTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.OnboardingApiTests.setUpClass.__func__)
    git_run = fixtures.OnboardingApiTests.git_run
    cli = fixtures.OnboardingApiTests.cli
    prepare_packet = fixtures.OnboardingApiTests.prepare_packet
    citation = fixtures.OnboardingApiTests.citation
    assessment = fixtures.OnboardingApiTests.assessment
    compact = staticmethod(fixtures.OnboardingApiTests.compact)
    pin = fixtures.OnboardingApiTests.pin
    save = fixtures.OnboardingApiTests.save

    def setUp(self):
        fixtures.OnboardingApiTests.setUp(self)
        self.patch_work_root = patch.object(work, 'ROOT', self.installed)
        self.patch_work_root.start(); self.addCleanup(self.patch_work_root.stop)
        requirement = self.case / 'requirement.txt'
        requirement.write_text('Change the project display name to improved-stack. Preserve everything else.')
        catalog = self.case / 'resources.json'
        catalog.write_text('{"resources": []}')
        self.wc = {'schemaVersion': 1, 'manifestSha256': self.c['manifestSha256'],
            'source': self.c['source'], 'requirement': self.pin(requirement), 'resourceCatalog': self.pin(catalog),
            'projectDocuments': ['README.md'], 'onboardingSources': ['web/package.json'], 'reviewSources': ['pyproject.toml', 'scripts/check.py', 'web/package.json'],
            'editablePaths': ['pyproject.toml'],
            'campaignDirectory': self.c['campaignDirectory'], 'historicalBudget': self.c['historicalBudget'],
            'timeoutSeconds': 900, 'onboardingMaxOutputTokens': 8192,
            'implementationMaxOutputTokens': 8192, 'onboardingMaxOperationNanoUsd': 1_000_000_000}
        self.wpath = self.case / 'workflow-config.json'; self.wpath.write_bytes(api.encoded(self.wc))
        self.wsha = api.digest(self.wpath.read_bytes()); self.ws = self.case / 'workflow-run'
        self.wcalls = []

    def workflow(self, authorization=None, transport=None):
        return work.run(self.wpath, self.wsha, self.ws, authorization, transport=transport)

    def workflow_post(self, endpoint, payload):
        self.wcalls.append(endpoint)
        if endpoint == 'responses/input_tokens':
            return {'input_tokens': 100}
        answer = copy.deepcopy(self.answer)
        if payload['text']['format']['name'] == 'project_onboarding_compact_v2':
            self.assertEqual(json.loads(payload['input'])['requirement'], api.pinned(self.wc['requirement']).decode())
            self.assertEqual(json.loads(payload['input'])['availableCapabilities'], work.public_capabilities(json.loads(api.pinned(self.wc['resourceCatalog']))))
            # Build a source-bound synthetic assessment for this workflow packet.
            packet = json.loads((self.ws / 'packet.json').read_bytes())
            answer['output'][0]['content'][0]['text'] = json.dumps(self.compact(self.assessment(packet)))
        else:
            disclosed = json.loads(payload['input'])
            self.assertIn('display name', disclosed['requirement'])
            self.assertIn('onboarding', disclosed)
            answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Rename the project metadata only.',
                'edits': [{'path': 'pyproject.toml', 'old': 'custom-stack', 'new': 'improved-stack'}]})
        return answer

    def grant(self, transport=None):
        preview = self.workflow()
        return self.workflow(preview['intentSha256'], transport or self.workflow_post)

    def setup_workflow_funding(self, maximum=2):
        prior = {key: self.wc[key] for key in ('campaignDirectory', 'historicalBudget')}
        old = api.Campaign(prior)
        old.reserve('ancestor-operation', 'ancestor-request', 100, 8192, self.case / 'ancestor-attempt')
        old.finish('ancestor-operation', self.answer)
        self.prior_campaign = Path(prior['campaignDirectory'])
        self.prior_budget_bytes = (self.prior_campaign / 'budget.json').read_bytes()
        snapshot = self.case / 'workflow-prior-budget.json'; snapshot.write_bytes(self.prior_budget_bytes)
        declaration = {'format': 'aim-flow-campaign-continuation', 'schemaVersion': 2,
            'campaignDirectory': str(self.case / 'continued-workflow-campaign'),
            'limitNanoUsd': 30_000_000_000, 'maximumPaidGenerations': maximum,
            'priorCampaign': prior, 'priorBudgetSnapshot': self.pin(snapshot)}
        self.funding_path = self.case / 'workflow-funding.json'; self.funding_path.write_bytes(api.encoded(declaration))
        self.wc = api.funded_config(self.wc, self.pin(self.funding_path))
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = api.digest(self.wpath.read_bytes())

    def test_initial_workflow_continuation_funds_both_phases_and_retains_prior_history(self):
        self.setup_workflow_funding()
        preview = self.workflow()
        self.assertEqual(preview['intent']['combinedHistoricalAndCampaignLimitNanoUsd'], 30_000_000_000)
        self.assertEqual(preview['intent']['campaignContinuation'], self.pin(self.funding_path))
        result = self.grant()
        self.assertEqual(result['status'], 'candidate-prepared')
        for phase in ('onboarding', 'implementation'):
            config = json.loads((self.ws / (phase + '-config.json')).read_bytes())
            self.assertEqual(config['campaignContinuation'], self.pin(self.funding_path))
            self.assertEqual(config['campaignDirectory'], self.wc['campaignDirectory'])
        onboarding_intent = json.loads((self.ws / 'onboarding-attempt/attempt.json').read_bytes())['intent']
        self.assertEqual(onboarding_intent['campaignLimitNanoUsd'], 30_000_000_000)
        ledger = api.read_existing_campaign(self.wc)
        self.assertEqual(ledger['identity']['allocatedPriorCampaignNanoUsd'], 20_000_000_000)
        self.assertEqual(len(ledger['requests']), 2)
        self.assertEqual((self.prior_campaign / 'budget.json').read_bytes(), self.prior_budget_bytes)
        self.assertEqual(work.verify(self.wpath, self.wsha, self.ws), result)
        self.assertEqual(len(self.wcalls), 4, 'Verification must not call the provider again')
        campaign = api.Campaign(self.wc)
        with self.assertRaisesRegex(Refused, 'never replay'):
            campaign.reserve('new-operation', 'ancestor-request', 100, 8192, self.case / 'duplicate')
        with self.assertRaisesRegex(Refused, 'generation bound exhausted'):
            campaign.reserve('third-operation', 'third-request', 100, 8192, self.case / 'third')

    def test_initial_workflow_funding_rejects_prior_overlap_and_tampered_pin_before_dispatch(self):
        self.setup_workflow_funding()
        original = self.ws; self.ws = self.prior_campaign / 'nested-output'
        with self.assertRaisesRegex(Refused, 'disjoint'):
            self.workflow()
        self.assertFalse(self.ws.exists()); self.ws = original
        self.funding_path.write_bytes(self.funding_path.read_bytes() + b' ')
        with self.assertRaisesRegex(Refused, 'Pinned input changed'):
            self.workflow()
        self.assertFalse(self.ws.exists()); self.assertFalse(self.wcalls)

    def test_workflow_single_grant_reaches_real_clean_descendant_without_touching_dirty_source(self):
        original = (self.checkout / 'pyproject.toml').read_bytes()
        (self.checkout / 'untracked.txt').write_text('Do not copy me')
        (self.checkout / 'pyproject.toml').write_text('Unrelated dirty work')
        result = self.grant()
        self.assertEqual(result['status'], 'candidate-prepared', result)
        candidate = Path(result['candidate']['checkout'])
        self.assertFalse((candidate / 'untracked.txt').exists())
        self.assertEqual((candidate / 'pyproject.toml').read_bytes(), original.replace(b'custom-stack', b'improved-stack'))
        self.assertEqual((self.checkout / 'pyproject.toml').read_text(), 'Unrelated dirty work')
        self.assertFalse(result['ready']); self.assertEqual(result['validation'], 'not-run')
        self.assertEqual(self.wcalls, ['responses/input_tokens', 'responses'] * 2)
        ledger = json.loads((Path(self.c['campaignDirectory']) / 'budget.json').read_bytes())
        self.assertEqual(len(ledger['requests']), 2)
        self.assertEqual(work.verify(self.wpath, self.wsha, self.ws), result)
        self.assertEqual(self.workflow(), result)
        self.assertEqual(len(self.wcalls), 4)

    def test_git_executable_pin_does_not_apply_private_state_hardlink_rule(self):
        tool = self.case / 'fixture-git-input'
        tool.write_bytes(b'Pinned test executable, never actually executed.\n')
        tool.chmod(0o700)
        os.link(tool, self.case / 'fixture-git-other-link')
        config = {'source': {'git': str(tool), 'gitSha256': api.digest(tool.read_bytes())}}
        with self.assertRaises(Refused):
            api.file_bytes(tool)  # Private file ownership checks stay strict.
        with patch.object(work.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'fixture-version'
            self.assertEqual(work.git(config, self.case, '--version'), b'fixture-version')
            self.assertEqual(run.call_count, 1)
        tool.write_bytes(b'Changed')
        with patch.object(work.subprocess, 'run') as run:
            with self.assertRaises(Refused): work.git(config, self.case, '--version')
            run.assert_not_called()

    def test_workflow_preview_no_files_credential_or_provider(self):
        with patch.object(api, 'transport_from_key_file', side_effect=AssertionError('credential')):
            result = self.workflow()
        self.assertEqual(result['status'], 'awaiting-authorization')
        self.assertNotIn('campaignContinuation', result['intent'])
        self.assertEqual(result['intent']['combinedHistoricalAndCampaignLimitNanoUsd'], 20_000_000_000)
        self.assertFalse(self.ws.exists()); self.assertFalse(self.wcalls)
        with self.assertRaises(Refused):
            self.workflow('0' * 64, self.workflow_post)
        self.assertFalse(self.ws.exists())

    def test_workflow_grant_can_resume_with_missing_credential_without_new_approval(self):
        result = self.workflow(self.workflow()['intentSha256'])
        self.assertEqual(result['status'], 'awaiting-provider-credential')
        result = self.workflow(transport=self.workflow_post)
        self.assertEqual(result['status'], 'candidate-prepared')

    def test_workflow_unknown_generation_never_replays(self):
        def unknown(endpoint, payload):
            if endpoint == 'responses':
                raise TimeoutError('synthetic loss')
            return {'input_tokens': 100}
        result = self.grant(unknown)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['replayPermitted'])
        self.assertEqual(self.workflow(transport=lambda *args: self.fail('replayed')), result)
        self.assertFalse((self.ws / 'candidate').exists())

    def test_workflow_candidate_drift_and_grant_drift_refused(self):
        result = self.grant()
        path = Path(result['candidate']['checkout']) / 'pyproject.toml'
        path.write_text('drift')
        with self.assertRaisesRegex(Refused, 'candidate content changed'):
            self.workflow()
        (self.ws / 'grant.json').write_text('{}')
        with self.assertRaisesRegex(Refused, 'grant changed'):
            self.workflow()

    def test_workflow_source_config_drift_refused(self):
        self.workflow(self.workflow()['intentSha256'])
        self.wc['onboardingSources'] = []
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = api.digest(self.wpath.read_bytes())
        with self.assertRaisesRegex(Refused, 'identity/source/controller changed'):
            self.workflow()

    def test_workflow_interrupted_candidate_is_not_recreated(self):
        with patch.object(work, 'create_candidate', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.grant()
        result = self.workflow(transport=lambda *args: self.fail('replayed'))
        self.assertEqual(result['nextStage'], 'reconcile-private-candidate')
        self.assertFalse(result['replayPermitted'])

    def test_workflow_provider_input_excludes_environment_metadata(self):
        catalog = Path(self.wc['resourceCatalog']['path'])
        catalog.write_text(json.dumps({'resources': [{'kind': 'node-runtime',
            'identity': '/private/resource-SECRET', 'qualification': 'private-QUALIFICATION',
            'executionSpec': {'path': '/private/config-SECRET'},
            'capabilities': {'nodeVersion': '24.22.2', 'npmVersion': '11.19.1',
                'platform': 'linux', 'architecture': 'arm64', 'utilities': {'git': '/private/git-SECRET'}}}]}))
        self.wc['resourceCatalog'] = self.pin(catalog)
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = api.digest(self.wpath.read_bytes())
        def inspect(endpoint, payload):
            if payload['text']['format']['name'] == 'requirement_implementation':
                raw = json.dumps(payload)
                for private in (str(self.checkout), str(self.git), str(self.installed),
                                'resource-SECRET', 'config-SECRET', 'git-SECRET', 'private-QUALIFICATION'):
                    self.assertNotIn(private, raw)
                self.assertIn('24.22.2', raw)
                disclosed = json.loads(payload['input'])
                self.assertEqual(set(disclosed['editableSources']), {'pyproject.toml'})
                self.assertIn('web/package.json', [x['path'] for x in disclosed['contextSources']])
            return self.workflow_post(endpoint, payload)
        self.assertEqual(self.grant(inspect)['status'], 'candidate-prepared')

    def test_workflow_context_is_not_edit_authority(self):
        def attack(endpoint, payload):
            answer = self.workflow_post(endpoint, payload)
            if endpoint == 'responses' and payload['text']['format']['name'] == 'requirement_implementation':
                answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Change context',
                    'edits': [{'path': 'web/package.json', 'old': 'node nested-test.js', 'new': 'node injected.js'}]})
            return answer
        result = self.grant(attack)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['attemptResult']['status'], 'proposal-rejected')
        self.assertFalse((self.ws / 'candidate').exists())

    def test_workflow_verification_never_advances_incomplete_run(self):
        self.workflow(self.workflow()['intentSha256'])
        before = {str(p): p.read_bytes() for p in self.ws.rglob('*') if p.is_file()}
        with self.assertRaises(Refused):
            work.verify(self.wpath, self.wsha, self.ws)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.ws.rglob('*') if p.is_file()})

    def test_workflow_copied_valid_attempt_cannot_satisfy_another_generated_stage(self):
        self.workflow(self.workflow()['intentSha256'])
        with patch.object(work, 'create_candidate', side_effect=OSError('synthetic stop')):
            with self.assertRaises(OSError):
                self.workflow(transport=self.workflow_post)
        attempt = self.ws / 'implementation-attempt' / 'attempt.json'
        value = json.loads(attempt.read_bytes())
        value['intent']['configPath'] = '/another/workflow/config.json'
        attempt.write_bytes(api.encoded(value))
        with self.assertRaisesRegex(Refused, 'current generated workflow stage'):
            self.workflow()

    def test_workflow_changed_onboarding_definition_requires_fresh_assessment(self):
        result = self.grant()
        self.assertEqual(result['nextStage'], 'fresh-candidate-assessment-required')
        self.assertEqual(result['onboardingReuse']['changedEvidencePaths'], ['pyproject.toml'])

    def test_workflow_installed_verifier_reconciles_real_candidate(self):
        expected = self.grant()
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.ws.rglob('*') if p.is_file()}
        command = [str(Path(sys.executable).resolve()), '-IBS', str(self.installed / 'aim-flow.py'),
            'work', '--config', str(self.wpath), '--config-sha256', self.wsha,
            '--state', str(self.ws), '--verify']
        result = subprocess.run(command, cwd=self.case, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertFalse(result.stderr)
        self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in self.ws.rglob('*') if p.is_file()})

    def test_workflow_verifier_does_not_recreate_missing_lock(self):
        self.grant()
        lock = self.ws / 'workflow.lock'
        lock.unlink()
        with self.assertRaises(Refused):
            work.verify(self.wpath, self.wsha, self.ws)
        self.assertFalse(lock.exists())

    def test_workflow_resume_does_not_recreate_consumed_stage_inputs(self):
        self.grant()
        for name in ('onboarding-config.json', 'implementation-config.json',
                     'implementation-request.json', 'implementation-sources/pyproject.toml'):
            with self.subTest(name=name):
                path = self.ws / name
                raw = path.read_bytes()
                path.unlink()
                with self.assertRaises(Refused):
                    self.workflow()
                self.assertFalse(path.exists())
                path.write_bytes(raw)
