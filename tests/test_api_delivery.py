"""Offline Sol proposal contracts, never a live model or product qualification."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('AIM_FLOW_TEST_ROOT', Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / 'lib'))
import flow_api_delivery as a
from aim_ci.budget import Budget


class ApiDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.candidate = self.base / 'candidate'; self.candidate.mkdir()
        self.source = self.candidate / 'app.js'; self.source.write_text('const value = 1;\n')
        self.hist = self.base / 'historical'; self.hist.mkdir()
        legacy_root = self.base / 'old'; legacy_root.mkdir()
        self.legacy = legacy_root / 'budget.json'; self.legacy.write_text('{"requests":[]}')
        (legacy_root / 'budget.lock').touch()
        Budget(self.hist, self.legacy)
        data = Budget.read_existing(self.hist, self.legacy)
        self.payload = {'model': a.MODEL, 'input': 'Change value to 2.', 'instructions': 'Return summary and unique old/new edits.',
                        'max_output_tokens': 1000, 'reasoning': {'effort': 'xhigh'}, 'service_tier': 'default',
                        'store': False, 'stream': True, 'text': {'format': {'type': 'json_schema', 'name': 'patch',
                        'strict': True, 'schema': a.PATCH_SCHEMA}}}
        self.req = self.base / 'request.json'; self.req.write_bytes(a.encoded(self.payload))
        self.task = self.base / 'task.txt'; self.task.write_text('Pinned task')
        self.config = self.base / 'config.json'
        self.state = self.base / 'operation-1'
        self.c = {'schemaVersion': 1, 'method': 'responses-api', 'model': a.MODEL, 'reasoning': 'xhigh',
                  'candidateDirectory': str(self.candidate), 'sourceFiles': {'app.js': self.pin(self.source)},
                  'inputFiles': [self.pin(self.task)], 'request': self.pin(self.req), 'timeoutSeconds': 900,
                  'campaignDirectory': str(self.base / 'campaign'), 'historicalBudget': {
                  'directory': str(self.hist), 'legacy': self.pin(self.legacy),
                  'files': {p.name: a.digest(p.read_bytes()) for p in self.hist.glob('*.json')},
                  'liabilityNanoUsd': data['legacyReservedNanoUsd']}}
        self.save()
        self.calls = []
        self.answer = {'id': 'resp_synthetic', 'model': a.MODEL, 'status': 'completed', 'service_tier': 'default',
                       'usage': {'input_tokens': 80, 'output_tokens': 30}, 'output': [{'type': 'message',
                       'role': 'assistant', 'content': [{'type': 'output_text', 'text': json.dumps({
                       'summary': 'Adjust value', 'edits': [{'path': 'app.js', 'old': 'value = 1', 'new': 'value = 2'}]})}]}]}
        self.original_history = {str(p): p.read_bytes() for d in (self.hist, legacy_root) for p in d.iterdir()}

    def pin(self, path):
        return {'path': str(path), 'sha256': a.digest(path.read_bytes())}

    def save(self):
        self.config.write_bytes(a.encoded(self.c)); self.sha = a.digest(self.config.read_bytes())

    def preview(self):
        return a.run(self.config, self.sha, self.state)

    def post(self, endpoint, payload):
        self.calls.append(endpoint)
        if endpoint == 'responses/input_tokens':
            return {'input_tokens': 80}
        ledger = json.loads((Path(self.c['campaignDirectory']) / 'budget.json').read_text())
        self.assertEqual(ledger['requests'][-1]['status'], 'reserved')
        self.assertTrue((self.state / 'reservation.json').is_file())
        return copy.deepcopy(self.answer)

    def execute(self, post=None):
        return a.run(self.config, self.sha, self.state, self.preview()['intentSha256'], transport=post or self.post)

    def test_preview_has_no_effect_or_key_read(self):
        with patch.object(a, 'transport_from_key_file', side_effect=AssertionError('read key')):
            result = self.preview()
        self.assertEqual(result['status'], 'preview')
        self.assertFalse(self.state.exists())
        self.assertFalse(Path(self.c['campaignDirectory']).exists())
        self.assertFalse(a.authority(self.c)[0].exists())

    def test_success_durable_reservation_and_unchanged_sources_history(self):
        result = self.execute()
        self.assertEqual(result['status'], 'proposal-ready-for-independent-review', result)
        self.assertEqual(a.status(self.state), result)
        self.assertEqual(self.calls, ['responses/input_tokens', 'responses'])
        self.assertEqual(self.source.read_text(), 'const value = 1;\n')
        self.assertFalse(result['sourceApplied']); self.assertFalse(result['productAccepted'])
        for path, raw in self.original_history.items(): self.assertEqual(Path(path).read_bytes(), raw)
        for path in self.state.iterdir(): self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_wrong_model_tier_tool_and_effort_refused_before_dispatch(self):
        for key, value in [('model', 'gpt-5-mini'), ('reasoning', {'effort': 'high'}),
                           ('service_tier', 'priority'), ('tools', []), ('stream', False), ('store', True)]:
            candidate = copy.deepcopy(self.payload); candidate[key] = value
            self.req.write_bytes(a.encoded(candidate)); self.c['request'] = self.pin(self.req); self.save()
            with self.assertRaises(a.Refused): self.preview()
        self.assertEqual(self.calls, [])

    def test_token_price_boundaries(self):
        self.assertEqual(a.charge(272000, 128000), 272000 * 2500 + 128000 * 10000)
        self.assertEqual(a.charge(272001, 128000), 272001 * 5000 + 128000 * 15000)
        self.assertEqual(a.reservation(272000, 128000), 273024 * 5000 + 128000 * 15000)
        self.assertEqual(a.reservation(922000, 128000), 923024 * 5000 + 128000 * 15000)
        for inp, out in [(922001, 1), (0, 128001), (-1, 1), (True, 1), (0, 0)]:
            with self.assertRaises(a.Refused): a.reservation(inp, out)

    def test_combined_campaign_ceiling_and_full_reservations(self):
        campaign = a.Campaign(self.c)
        for i in range(3): campaign.reserve(str(i), 'req' + str(i), 922000, 128000, self.state)
        with self.assertRaises(a.Refused): campaign.reserve('4', 'req4', 922000, 128000, self.state)
        ledger = campaign.read()
        self.assertEqual(len(ledger['requests']), 3)
        self.assertLessEqual(sum(r['reservedNanoUsd'] for r in ledger['requests']) + self.c['historicalBudget']['liabilityNanoUsd'], a.LIMIT)

    def test_no_replay_same_state_or_same_payload_new_state(self):
        self.execute()
        with self.assertRaises(a.Refused): self.execute()
        self.state = self.base / 'operation-2'
        result = self.execute()
        self.assertEqual(result['status'], 'preflight-refused')
        self.assertEqual(self.calls.count('responses'), 1)

    def test_new_campaign_does_not_reset_authority(self):
        self.execute()
        self.state = self.base / 'operation-2'
        self.c['campaignDirectory'] = str(self.base / 'campaign-2'); self.save()
        with self.assertRaises(a.Refused): self.preview()
        self.assertFalse(Path(self.c['campaignDirectory']).exists())

    def test_missing_whole_campaign_never_reinitialized(self):
        self.execute()
        Path(self.c['campaignDirectory']).rename(self.base / 'moved-campaign')
        self.state = self.base / 'operation-2'
        with self.assertRaises(a.Refused): self.execute()
        self.assertEqual(self.calls.count('responses'), 1)
        self.assertFalse(Path(self.c['campaignDirectory']).exists())

    def test_missing_campaign_ledger_never_reinitialized(self):
        self.execute()
        (Path(self.c['campaignDirectory']) / 'budget.json').unlink()
        with self.assertRaises(a.Refused): a.Campaign(self.c)

    def retained_mismatch(self, field, value):
        self.answer[field] = value
        result = self.execute()
        self.assertEqual(result['status'], 'unknown-usage-or-model', result)
        self.assertEqual(json.loads((self.state / 'response.json').read_text()), self.answer)
        self.assertEqual(result['reservation']['status'], 'unknown')

    def test_wrong_returned_model_retained(self):
        self.retained_mismatch('model', 'other')

    def test_wrong_returned_tier_retained(self):
        self.retained_mismatch('service_tier', 'priority')

    def test_unknown_usage_retained(self):
        self.retained_mismatch('usage', None)

    def test_missing_strict_schema_refused(self):
        del self.payload['text']
        self.req.write_bytes(a.encoded(self.payload)); self.c['request'] = self.pin(self.req); self.save()
        with self.assertRaises(a.Refused): self.preview()

    def test_missing_authority_never_recreated(self):
        self.execute()
        a.authority(self.c)[0].unlink()
        with self.assertRaises(a.Refused): a.Campaign(self.c)

    def test_source_drift_after_reservation_no_generation(self):
        reserve = a.Campaign.reserve
        def changed(campaign, *args):
            result = reserve(campaign, *args); self.source.write_text('drift'); return result
        with patch.object(a.Campaign, 'reserve', changed):
            result = self.execute()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reservation']['status'], 'unknown')
        self.assertEqual(self.calls, ['responses/input_tokens'])

    def test_incomplete_terminal_retained(self):
        self.answer['status'] = 'incomplete'; self.answer['incomplete_details'] = {'reason': 'max_output_tokens'}
        result = self.execute()
        self.assertEqual(result['status'], 'model-incomplete')
        self.assertEqual(json.loads((self.state / 'response.json').read_text()), self.answer)
        self.assertEqual(result['reservation']['status'], 'completed')

    def test_failed_terminal_retained(self):
        self.answer['status'] = 'failed'
        result = self.execute(); self.assertEqual(result['status'], 'model-failed')
        self.assertTrue((self.state / 'response.json').is_file())

    def test_refusal_terminal_retained(self):
        self.answer['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'synthetic refusal'}]
        result = self.execute(); self.assertEqual(result['status'], 'model-refused')
        self.assertTrue((self.state / 'response.json').is_file()); self.assertFalse((self.state / 'proposal.json').exists())

    def test_unknown_transport_failure_no_secrets_no_retry(self):
        def post(endpoint, payload):
            if endpoint == 'responses':
                self.calls.append(endpoint)
                raise TimeoutError('NEVER-RETAIN-THIS-SECRET /private/key.env')
            return self.post(endpoint, payload)
        result = self.execute(post)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reservation']['status'], 'unknown')
        self.assertEqual(self.calls.count('responses'), 1)
        for directory in (self.state, Path(self.c['campaignDirectory'])):
            for p in directory.iterdir():
                self.assertNotIn(b'NEVER-RETAIN-THIS-SECRET', p.read_bytes())
                self.assertNotIn(b'/private/key.env', p.read_bytes())
        with self.assertRaises(a.Refused): self.execute(post)

    def test_key_factory_scoped_timeout_and_path_not_retained(self):
        key = '/do/not/open/synthetic-secret.env'
        with patch.object(a, 'transport_from_key_file', return_value=self.post) as factory:
            result = a.run(self.config, self.sha, self.state, self.preview()['intentSha256'], key_file=key)
        factory.assert_called_once_with(key, require_private=True, deadline_seconds=900)
        self.assertEqual(result['status'], 'proposal-ready-for-independent-review')
        for p in self.state.iterdir(): self.assertNotIn(key.encode(), p.read_bytes())

    def test_input_pin_drift_after_preview(self):
        token = self.preview()['intentSha256']; self.task.write_text('changed')
        with self.assertRaises(a.Refused): a.run(self.config, self.sha, self.state, token, transport=self.post)
        self.assertEqual(self.calls, [])

    def test_source_drift_during_preflight_no_generation(self):
        def post(endpoint, payload):
            result = self.post(endpoint, payload); self.source.write_text('changed'); return result
        result = self.execute(post)
        self.assertEqual(result['status'], 'preflight-refused')
        self.assertEqual(self.calls, ['responses/input_tokens'])

    def test_history_drift_during_preflight_no_generation(self):
        def post(endpoint, payload):
            result = self.post(endpoint, payload)
            (self.hist / 'budget.json').write_bytes((self.hist / 'budget.json').read_bytes() + b'\n')
            return result
        result = self.execute(post)
        self.assertEqual(result['status'], 'preflight-refused')
        self.assertEqual(self.calls, ['responses/input_tokens'])

    def test_historical_liability_must_include_all_reservations(self):
        self.c['historicalBudget']['liabilityNanoUsd'] = 0; self.save()
        with self.assertRaises(a.Refused): self.preview()

    def test_bad_proposal_retains_response_and_reservation(self):
        self.answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'bad', 'edits': [
            {'path': '../escape', 'old': 'value', 'new': 'secret'}]})
        result = self.execute()
        self.assertEqual(result['status'], 'proposal-rejected')
        self.assertTrue((self.state / 'response.json').exists())
        self.assertEqual(result['reservation']['status'], 'completed')

    def test_exact_authorization_required(self):
        with self.assertRaises(a.Refused): a.run(self.config, self.sha, self.state, 'wrong', transport=self.post)
        self.assertFalse(self.state.exists())

    def test_status_missing_terminal_and_changed_evidence(self):
        self.execute()
        (self.state / 'response.json').write_text('{}')
        self.assertEqual(a.status(self.state)['status'], 'integrity-failed')
        (self.state / 'result.json').unlink()
        self.assertEqual(a.status(self.state)['status'], 'unknown')

    def test_cli_preview_and_no_key_allowed(self):
        command = [sys.executable, '-B', 'aim-flow.py', 'deliver-api', '--config', str(self.config),
                   '--config-sha256', self.sha, '--state', str(self.state), '--preview']
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'preview')
        result = subprocess.run(command + ['--key-file', '/not/read'], cwd=ROOT, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__': unittest.main()
