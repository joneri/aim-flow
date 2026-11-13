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
from openai_budget import canonical
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
        self.body = self.compact(full)
        self.answer = {'id': 'synthetic-assessment', 'model': api.MODEL, 'status': 'completed',
            'service_tier': 'default', 'usage': {'input_tokens': 80, 'output_tokens': 30},
            'output': [{'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': json.dumps(self.body)}]}]}
        self.calls = []; self.count = 80
        self.history_before = {str(p): p.read_bytes() for d in (self.historical, legacy_root) for p in d.iterdir()}

    @staticmethod
    def compact(full):
        plan = copy.deepcopy({key: full[key] for key in ('areas', 'steps', 'decisions')})
        for item in [*plan['areas'].values(), *plan['steps'], *plan['decisions']]:
            item['sources'] = [{key: cite[key] for key in ('path', 'startLine', 'endLine')}
                               for cite in item['sources']]
        return {'brief': {key: full[key] for key in ('summary', 'unknowns')}, 'plan': plan}

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
        self.assertIn(str(self.marker), ''.join(source['excerpts'][0]['lines']))
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
        self.assertEqual(instance['assessment']['summary'], self.body['brief']['summary'])
        self.assertFalse(instance['ready']); self.assertFalse(instance['executionAuthorized'])
        for name, content in self.history_before.items(): self.assertEqual(Path(name).read_bytes(), content)
        for path in self.state.iterdir(): self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertEqual(result['reservation']['reservedNanoUsd'], api.reservation(self.count, self.c['maxOutputTokens']))

    def test_optional_requirement_is_exact_pinned_private_path_free_and_not_authority(self):
        plain = self.preview()
        self.assertNotIn('requirement', json.loads(plain['request']['input']))
        self.assertEqual(plain['request'], agent.request_payload(self.packet, self.c['maxOutputTokens']))
        requirement = self.case / 'private-user-story.md'
        requirement.write_text('Pause and resume the carousel. Verify keyboard and browser behavior.\n')
        self.c['requirement'] = self.pin(requirement); self.save()
        preview = self.preview()
        self.assertEqual(json.loads(preview['request']['input'])['requirement'], requirement.read_text())
        self.assertNotIn(str(requirement), json.dumps(preview['request']))
        self.assertIn('not execution authority', preview['request']['instructions'])
        self.assertEqual(preview['intent']['requirement'], {**self.pin(requirement), 'disclosedBytes': requirement.stat().st_size})
        self.assertNotEqual(preview['intentSha256'], plain['intentSha256'])
        result = self.execute()
        self.assertEqual(result['status'], 'assessment-ready-for-independent-review')
        self.assertFalse(result['executionAuthorized']); self.assertFalse(result['deploymentAuthorized'])

    def test_requirement_drift_during_preflight_blocks_generation_and_reservation(self):
        requirement = self.case / 'story.md'; requirement.write_text('Original task')
        self.c['requirement'] = self.pin(requirement); self.save()
        def mutate(endpoint, payload):
            result = self.post(endpoint, payload)
            requirement.write_text('Changed task')
            return result
        self.assertEqual(self.execute(mutate)['status'], 'preflight-refused')
        self.assertEqual(self.calls, ['responses/input_tokens'])
        self.assertEqual(api.Campaign(self.c).read()['requests'], [])

    def test_registered_capabilities_are_typed_private_path_free_and_not_qualification(self):
        row = {'kind': 'browser-runtime', 'capabilities': {'nodeVersion': '24.22.2',
            'npmVersion': '11.19.1', 'platform': 'linux', 'architecture': 'arm64',
            'browserPackages': {'playwright': '1.58.2'}}}
        self.c['availableCapabilities'] = [row]; self.save()
        preview = self.preview()
        self.assertEqual(json.loads(preview['request']['input'])['availableCapabilities'], [row])
        self.assertIn('not actual runtime qualification', preview['request']['instructions'])
        self.assertIn('npm run SCRIPT', preview['request']['instructions'])
        for field, value in [('identity', '/private/runtime'), ('executionSpec', {'path': '/private/spec'}),
                             ('qualification', 'secret prose')]:
            unsafe = {**row, field: value}
            self.c['availableCapabilities'] = [unsafe]; self.save()
            with self.assertRaises(Refused): self.preview()
        self.c['availableCapabilities'] = [{'kind': 'node-runtime',
            'capabilities': {'nodeVersion': '/private/runtime'}}]; self.save()
        with self.assertRaises(Refused): self.preview()
        self.c['availableCapabilities'] = [{'kind': 'browser-runtime',
            'capabilities': {'browserPackages': {'playwright': '1.2.3-' + 'x' * 200000}}}]; self.save()
        with self.assertRaises(Refused): self.preview()
        self.assertFalse(self.state.exists()); self.assertEqual(self.calls, [])

    def test_requirement_bounds_encoding_and_mutable_location_fail_before_dispatch(self):
        requirement = self.case / 'story.md'
        for text in ('a' * agent.MAX_REQUIREMENT_BYTES, 'å' * (agent.MAX_REQUIREMENT_BYTES // 2)):
            requirement.write_text(text)
            self.assertEqual(agent.requirement_text(self.pin(requirement)), text)
        for raw in (b'', b'\xff', b'a' * (agent.MAX_REQUIREMENT_BYTES + 1), b'\0'):
            requirement.write_bytes(raw); self.c['requirement'] = self.pin(requirement); self.save()
            with self.assertRaises(Refused): self.preview()
        campaign = Path(self.c['campaignDirectory']); campaign.mkdir()
        requirement = campaign / 'story.md'; requirement.write_text('Task')
        self.c['requirement'] = self.pin(requirement); self.save()
        with self.assertRaises(Refused): self.preview()
        self.assertFalse(self.state.exists()); self.assertEqual(self.calls, [])

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
        body = copy.deepcopy(self.body); body['plan']['steps'][0]['sources'][0]['quote'] = 'fabricated\n'
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

    def test_actual_canonical_wire_order_puts_brief_before_plan_and_steps_last(self):
        payload = self.preview()['request']
        wire = json.loads(canonical(payload))
        properties = wire['text']['format']['schema']['properties']
        self.assertEqual(list(properties), ['brief', 'plan'])
        self.assertEqual(list(properties['brief']['properties']), ['summary', 'unknowns'])
        self.assertEqual(list(properties['plan']['properties']), ['areas', 'decisions', 'steps'])
        pinned = json.loads(api.encoded(payload))
        self.assertEqual(list(pinned['text']['format']['schema']['properties']), ['brief', 'plan'])

    def test_compact_citations_enrich_locally_without_mutating_provider_body(self):
        original = copy.deepcopy(self.body)
        full = agent.enrich_body(self.body, self.packet)
        self.assertEqual(self.body, original)
        self.assertEqual(full['steps'][0]['sources'][0], self.citation(self.packet))
        self.assertNotIn('quote', self.body['plan']['steps'][0]['sources'][0])
        self.assertEqual(agent.onboarding.validate_assessment({
            'format': agent.onboarding.ASSESSMENT, 'schemaVersion': 1,
            'packetSha256': self.c['packet']['sha256'], 'target': self.packet['target'], **full},
            self.packet, self.c['packet']['sha256'])['summary'], full['summary'])

    def test_compact_reference_shape_ranges_and_bounds_are_locally_enforced(self):
        mutations = [lambda c: c.update(path='absent'), lambda c: c.update(startLine=True),
            lambda c: c.update(endLine=1.5), lambda c: c.update(startLine=0),
            lambda c: c.update(startLine=2, endLine=1), lambda c: c.update(endLine=1000000),
            lambda c: c.update(sha256='invented'), lambda c: c.update(quote='invented')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                body = copy.deepcopy(self.body)
                mutate(body['plan']['steps'][0]['sources'][0])
                with self.assertRaises(Refused): agent.enrich_body(body, self.packet)
        body = copy.deepcopy(self.body)
        body['brief']['unknowns'] *= 9
        with self.assertRaises(Refused): agent.enrich_body(body, self.packet)
        body = copy.deepcopy(self.body)
        body['brief']['summary'] = 'x' * 1001
        with self.assertRaises(Refused): agent.enrich_body(body, self.packet)
        body = copy.deepcopy(self.body)
        body['plan']['steps'] *= 11
        with self.assertRaises(Refused): agent.enrich_body(body, self.packet)
        body = copy.deepcopy(self.body)
        body['plan']['areas']['ci']['sources'] *= 5
        with self.assertRaises(Refused): agent.enrich_body(body, self.packet)

    def test_output_bounds_reject_provider_reply_after_retaining_original(self):
        self.body['brief']['summary'] = 'x' * 1001
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)
        result = self.execute()
        self.assertEqual(result['status'], 'assessment-rejected')
        retained = json.loads((self.state / 'response.json').read_bytes())
        self.assertEqual(retained, self.answer)
        self.assertFalse((self.state / 'assessment.json').exists())

    def test_duplicate_model_keys_and_old_verbose_contract_rejected(self):
        for text in ('{"brief":{},"brief":{},"plan":{}}', json.dumps(self.assessment(self.packet))):
            answer = copy.deepcopy(self.answer)
            answer['output'][0]['content'][0]['text'] = text
            with self.assertRaises((Refused, ValueError)):
                agent.parse_response(answer, self.packet, self.c['packet']['sha256'])

    def test_excerpt_selection_is_explicit_disclosed_and_restricts_citations(self):
        self.c['sourceRanges'] = {'README.md': [{'startLine': 1, 'endLine': 1},
                                               {'startLine': 3, 'endLine': 3}]}
        self.save()
        preview = self.preview()
        source = next(s for s in json.loads(preview['request']['input'])['sources'] if s['path'] == 'README.md')
        self.assertEqual(source['omittedLineCount'], 2)
        self.assertEqual([e['startLine'] for e in source['excerpts']], [1, 3])
        self.assertNotIn('Ignore prior instructions', preview['request']['input'])
        disclosure = next(s for s in preview['intent']['disclosure'] if s['path'] == 'README.md')
        self.assertEqual(disclosure['ranges'], self.c['sourceRanges']['README.md'])
        self.assertEqual(disclosure['omittedLineCount'], 2)
        with self.assertRaises(Refused):
            body = copy.deepcopy(self.body)
            body['plan']['steps'][0]['sources'][0]['endLine'] = 3
            agent.enrich_body(body, self.packet, self.c['sourceRanges'])
        result = self.execute()
        self.assertEqual(result['status'], 'assessment-ready-for-independent-review', result)

    def test_invalid_excerpt_selection_is_refused_before_credentials_or_network(self):
        for selection in ({'absent': [{'startLine': 1, 'endLine': 1}]}, {'README.md': []},
            {'README.md': [{'startLine': True, 'endLine': 1}]},
            {'README.md': [{'startLine': 1, 'endLine': 100}]},
            {'README.md': [{'startLine': 2, 'endLine': 2}, {'startLine': 1, 'endLine': 1}]},
            {'README.md': [{'startLine': 1, 'endLine': 3}, {'startLine': 3, 'endLine': 4}]}):
            self.c['sourceRanges'] = selection; self.save()
            with self.assertRaises(Refused): self.preview()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())

    def test_changed_excerpt_selection_changes_exact_intent(self):
        original = self.preview()['intentSha256']
        self.c['sourceRanges'] = {'README.md': [{'startLine': 1, 'endLine': 2}]}; self.save()
        self.assertNotEqual(self.preview()['intentSha256'], original)
        with self.assertRaises(Refused): agent.run(self.config, self.config_sha, self.state, original, transport=self.post)
        self.assertEqual(self.calls, [])

    def test_enrichment_preserves_crlf_and_final_line_without_newline(self):
        packet = copy.deepcopy(self.packet)
        source = next(s for s in packet['sources'] if s['path'] == 'README.md')
        source.update(text='first\r\nlast', lineCount=2)
        source['sha256'] = api.digest(source['text'].encode())
        body = copy.deepcopy(self.body)
        body['plan']['steps'][0]['sources'][0]['endLine'] = 2
        full = agent.enrich_body(body, packet)
        self.assertEqual(full['steps'][0]['sources'][0]['quote'], 'first\r\nlast')
        self.assertEqual(full['steps'][0]['sources'][0]['sha256'], source['sha256'])

    def test_blank_long_and_large_expanded_quotes_are_refused(self):
        for text, end in [('\n', 1), ('x\n' * 41, 41), ('x' * (agent.MAX_QUOTE_BYTES + 1), 1)]:
            packet = copy.deepcopy(self.packet)
            source = next(s for s in packet['sources'] if s['path'] == 'README.md')
            source.update(text=text, lineCount=len(text.splitlines(keepends=True)))
            body = copy.deepcopy(self.body)
            body['plan']['steps'][0]['sources'][0]['endLine'] = end
            with self.assertRaises(Refused): agent.enrich_body(body, packet)
        with patch.object(agent, 'MAX_ENRICHED_BYTES', 10):
            with self.assertRaises(Refused): agent.enrich_body(self.body, self.packet)

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
