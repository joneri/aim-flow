"""Offline real-dispatch path, shared Sol budget, no external requests or credentials."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import flow_api_delivery as api
import flow_onboarding_delivery as agent
from aim_ci.budget import Budget
from aim_ci.owned_paths import Refused
import test_project_onboarding as fixtures


class OnboardingApiTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectOnboardingTests.setUpClass.__func__)
    git_run = fixtures.ProjectOnboardingTests.git_run
    cli = fixtures.ProjectOnboardingTests.cli
    prepare_packet = fixtures.ProjectOnboardingTests.prepare
    citation = fixtures.ProjectOnboardingTests.citation
    assessment = fixtures.ProjectOnboardingTests.assessment

    def setUp(self):
        fixtures.ProjectOnboardingTests.setUp(self)
        self.packet = self.prepare_packet()
        self.patch_root = patch.object(agent, 'ROOT', self.installed)
        self.patch_root.start(); self.addCleanup(self.patch_root.stop)
        self.historical = self.case / 'historical'; self.historical.mkdir()
        legacy_root = self.case / 'legacy'; legacy_root.mkdir()
        self.legacy = legacy_root / 'budget.json'; self.legacy.write_text('{"requests":[]}')
        (legacy_root / 'budget.lock').touch()
        Budget(self.historical, self.legacy)
        data = Budget.read_existing(self.historical, self.legacy)
        self.config = self.case / 'dispatch.json'; self.state = self.case / 'attempt'
        self.c = {'schemaVersion': 1, 'purpose': agent.PURPOSE, 'model': api.MODEL, 'reasoning': 'xhigh',
            'manifestSha256': self.release['manifestSha256'],
            'source': {'checkout': str(self.checkout), 'commit': self.commit, 'git': str(self.git), 'gitSha256': self.git_pin},
            'packet': self.pin(self.packet_path), 'campaignDirectory': str(self.case / 'campaign'),
            'historicalBudget': {'directory': str(self.historical), 'legacy': self.pin(self.legacy),
                'files': {p.name: api.digest(p.read_bytes()) for p in self.historical.glob('*.json')},
                'liabilityNanoUsd': data['legacyReservedNanoUsd']},
            'timeoutSeconds': 900, 'maxOutputTokens': 8192, 'maxOperationNanoUsd': 1_000_000_000}
        self.save()
        full = self.assessment(self.packet)
        self.body = {key: full[key] for key in ('summary', 'areas', 'steps', 'decisions', 'unknowns')}
        self.answer = {'id': 'synthetic-assessment', 'model': api.MODEL, 'status': 'completed',
            'service_tier': 'default', 'usage': {'input_tokens': 80, 'output_tokens': 30},
            'output': [{'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': json.dumps(self.body)}]}]}
        self.calls = []; self.count = 80
        self.history_before = {str(p): p.read_bytes() for d in (self.historical, legacy_root) for p in d.iterdir()}

    def pin(self, path):
        return {'path': str(path), 'sha256': api.digest(path.read_bytes())}

    def save(self):
        self.config.write_bytes(api.encoded(self.c)); self.config_sha = api.digest(self.config.read_bytes())

    def preview(self):
        return agent.run(self.config, self.config_sha, self.state)

    def post(self, endpoint, payload):
        self.calls.append(endpoint)
        if endpoint == 'responses/input_tokens':
            return {'input_tokens': self.count}
        ledger = json.loads((Path(self.c['campaignDirectory']) / 'budget.json').read_bytes())
        self.assertEqual(ledger['requests'][-1]['status'], 'reserved')
        self.assertTrue((self.state / 'reservation.json').exists())
        return copy.deepcopy(self.answer)

    def execute(self, post=None):
        return agent.run(self.config, self.config_sha, self.state, self.preview()['intentSha256'], transport=post or self.post)

    def test_preview_is_read_only_and_discloses_exact_request_without_private_locations(self):
        with patch.object(agent, 'transport_from_key_file', side_effect=AssertionError('credential read')):
            result = self.preview()
        self.assertEqual(result['status'], 'preview')
        self.assertFalse(self.state.exists()); self.assertFalse(Path(self.c['campaignDirectory']).exists())
        self.assertFalse(api.authority(self.c)[0].exists())
        payload = result['request']
        disclosed = json.loads(payload['input'])
        self.assertEqual(set(disclosed['project']), {'commit', 'tree'})
        self.assertNotIn(str(self.installed), json.dumps(payload))
        self.assertNotIn(str(self.historical), json.dumps(payload))
        # Selected source is exact, not secretly redacted: the fixture itself
        # contains a local marker path, which remains visible for review.
        source = next(s for s in disclosed['sources'] if s['path'] == 'scripts/check.py')
        self.assertIn(str(self.marker), ''.join(source['lines']))
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['model'], api.MODEL)
        self.assertEqual(payload['reasoning'], {'effort': 'xhigh'})
        self.assertEqual(result['intent']['requestSha256'], api.digest(api.encoded(payload)))
        self.assertIn('Ignore prior instructions', payload['input'])
        self.assertIn('untrusted data', payload['instructions'])
        self.assertEqual(len(result['intent']['disclosure']), len(self.packet['sources']))
        self.assertEqual(payload['text']['format']['schema'], agent.ASSESSMENT_BODY_SCHEMA)

    def test_success_generates_and_records_source_bound_instance_with_shared_reservation(self):
        result = self.execute()
        self.assertEqual(result['status'], 'assessment-ready-for-independent-review', result)
        self.assertEqual(self.calls, ['responses/input_tokens', 'responses'])
        self.assertEqual(api.status(self.state), result)
        self.assertFalse(result['executionAuthorized']); self.assertFalse(result['deploymentAuthorized'])
        instance = json.loads((self.state / 'project-instance.json').read_bytes())
        self.assertEqual(instance['assessment']['summary'], self.body['summary'])
        self.assertFalse(instance['ready']); self.assertFalse(instance['executionAuthorized'])
        for name, content in self.history_before.items(): self.assertEqual(Path(name).read_bytes(), content)
        for path in self.state.iterdir(): self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertEqual(result['reservation']['reservedNanoUsd'], api.reservation(self.count, self.c['maxOutputTokens']))

    def test_exact_consent_and_explicit_scope_required_before_key_access(self):
        with patch.object(agent, 'transport_from_key_file', side_effect=AssertionError('credential read')):
            with self.assertRaises(Refused):
                agent.run(self.config, self.config_sha, self.state, '0' * 64, key_file=self.case / 'absent')
        for key, value in (('purpose', 'patch'), ('model', 'other'), ('reasoning', 'high'),
                           ('maxOperationNanoUsd', 0), ('maxOperationNanoUsd', True), ('maxOperationNanoUsd', api.LIMIT + 1)):
            original = copy.deepcopy(self.c)
            self.c[key] = value; self.save()
            with self.assertRaises(Refused): self.preview()
            self.c = original; self.save()
        self.assertEqual(self.calls, []); self.assertFalse(self.state.exists())

    def test_per_operation_limit_blocks_generation_before_reservation(self):
        self.c['maxOperationNanoUsd'] = 1; self.save()
        result = self.execute()
        self.assertEqual(result['status'], 'preflight-refused')
        self.assertEqual(self.calls, ['responses/input_tokens'])
        self.assertEqual(api.Campaign(self.c).read()['requests'], [])
        self.assertFalse((self.state / 'assessment.json').exists())

    def test_existing_patch_campaign_liability_is_shared_and_cannot_reset(self):
        campaign = api.Campaign(self.c)
        for i in range(3): campaign.reserve('previous-patch-' + str(i), 'previous-request-' + str(i), 922000, 128000, self.case / ('previous-' + str(i)))
        self.c['maxOutputTokens'] = 128000; self.c['maxOperationNanoUsd'] = 2_000_000_000; self.save()
        self.count = 90000
        result = self.execute()
        self.assertEqual(result['status'], 'preflight-refused')
        self.assertEqual(self.calls, ['responses/input_tokens'])
        self.assertEqual(len(campaign.read()['requests']), 3)
        self.state = self.case / 'different-attempt'
        self.c['campaignDirectory'] = str(self.case / 'different-campaign'); self.save()
        with self.assertRaises(Refused): self.preview()

    def test_no_replay_even_with_new_state_for_identical_request(self):
        self.execute()
        with self.assertRaises(Refused): self.execute()
        self.state = self.case / 'second-attempt'
        result = self.execute()
        self.assertEqual(result['status'], 'preflight-refused')
        self.assertEqual(self.calls.count('responses'), 1)

    def test_invented_citation_is_retained_but_never_becomes_instance(self):
        body = copy.deepcopy(self.body); body['steps'][0]['sources'][0]['quote'] = 'fabricated\n'
        self.answer['output'][0]['content'][0]['text'] = json.dumps(body)
        result = self.execute()
        self.assertEqual(result['status'], 'assessment-rejected')
        self.assertTrue((self.state / 'response.json').exists())
        self.assertFalse((self.state / 'assessment.json').exists())
        self.assertFalse((self.state / 'project-instance.json').exists())
        self.assertEqual(result['reservation']['status'], 'completed')

    def test_authority_claim_in_model_body_is_rejected(self):
        self.body['executionAuthorized'] = True
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)
        self.assertEqual(self.execute()['status'], 'assessment-rejected')
        self.assertFalse((self.state / 'project-instance.json').exists())

    def test_model_mismatch_unknown_usage_and_refusal_keep_terminal_evidence(self):
        self.answer['model'] = 'wrong'
        result = self.execute()
        self.assertEqual(result['status'], 'unknown-usage-or-model')
        self.assertEqual(result['reservation']['status'], 'unknown')
        self.assertTrue((self.state / 'response.json').exists())
        self.assertFalse((self.state / 'assessment.json').exists())

    def test_incomplete_response_is_not_parsed_or_replayed(self):
        self.answer['status'] = 'incomplete'
        result = self.execute()
        self.assertEqual(result['status'], 'model-incomplete')
        self.assertFalse((self.state / 'assessment.json').exists())
        with self.assertRaises(Refused): self.execute()

    def test_provider_refusal_is_not_an_assessment(self):
        self.answer['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'Synthetic refusal'}]
        self.assertEqual(self.execute()['status'], 'model-refused')
        self.assertFalse((self.state / 'project-instance.json').exists())

    def test_transport_loss_is_unknown_with_full_hold_and_no_secret_error_retained(self):
        secret = 'synthetic-private-value-never-log'
        def post(endpoint, payload):
            if endpoint == 'responses':
                raise RuntimeError(secret)
            return self.post(endpoint, payload)
        result = self.execute(post)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reservation']['status'], 'unknown')
        for file in self.state.iterdir(): self.assertNotIn(secret.encode(), file.read_bytes())
        with self.assertRaises(Refused): self.execute()

    def test_source_drift_during_generation_retains_response_without_instance(self):
        def post(endpoint, payload):
            response = self.post(endpoint, payload)
            if endpoint == 'responses': self.git_run('commit', '--allow-empty', '-qm', 'Source drift')
            return response
        self.assertEqual(self.execute(post)['status'], 'assessment-rejected')
        self.assertTrue((self.state / 'response.json').exists())
        self.assertFalse((self.state / 'project-instance.json').exists())

    def test_source_drift_after_reservation_holds_liability_without_generation(self):
        reserve = api.Campaign.reserve
        def altered(campaign, *args):
            entry = reserve(campaign, *args)
            self.git_run('commit', '--allow-empty', '-qm', 'Source drift')
            return entry
        with patch.object(api.Campaign, 'reserve', altered): result = self.execute()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.calls, ['responses/input_tokens'])
        self.assertGreater(result['reservation']['reservedNanoUsd'], 0)

    def test_status_is_integrity_checked_and_never_dispatches(self):
        self.execute(); calls = list(self.calls)
        (self.state / 'assessment.json').write_text('{}')
        self.assertEqual(api.status(self.state)['status'], 'integrity-failed')
        self.assertEqual(self.calls, calls)

    def test_fresh_installed_runner_works_without_source_imports_or_network(self):
        answer = self.case / 'fixture-answer.json'; answer.write_text(json.dumps(self.answer))
        harness = '''
import json,sys,socket
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])/'lib'))
import flow_onboarding_delivery as agent
socket.socket=lambda *a, **k: (_ for _ in ()).throw(AssertionError('Network forbidden'))
response=json.loads(Path(sys.argv[5]).read_text())
def post(endpoint,payload):
 return {'input_tokens':80} if endpoint=='responses/input_tokens' else response
preview=agent.run(Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]))
result=agent.run(Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]),preview['intentSha256'],transport=post)
print(json.dumps({'module':agent.__file__,'result':result}))
'''
        result = subprocess.run([str(Path(sys.executable).resolve()), '-IBS', '-c', harness, str(self.installed),
            str(self.config), self.config_sha, str(self.state), str(answer)], cwd=self.case, env=self.env,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['module'], str(self.installed / 'lib/flow_onboarding_delivery.py'))
        self.assertEqual(report['result']['status'], 'assessment-ready-for-independent-review', report)
        cli = subprocess.run([str(Path(sys.executable).resolve()), '-IBS', str(self.installed / 'aim-flow.py'),
            'onboard-api', '--status', '--state', str(self.state)], cwd=self.case, env=self.env,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), report['result'])


if __name__ == '__main__':
    unittest.main()
