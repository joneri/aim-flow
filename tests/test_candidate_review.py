"""Offline review dispatch, exact diff, strict findings and shared liability."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import flow_candidate_review as review
import flow_api_delivery as api
from aim_ci.owned_paths import Refused
import test_onboarding_api_delivery as fixtures

class ReviewTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.OnboardingApiTests.setUpClass.__func__)
    git_run = fixtures.OnboardingApiTests.git_run
    cli = fixtures.OnboardingApiTests.cli
    prepare_packet = fixtures.OnboardingApiTests.prepare_packet
    citation = fixtures.OnboardingApiTests.citation
    assessment = fixtures.OnboardingApiTests.assessment
    compact = staticmethod(fixtures.OnboardingApiTests.compact)
    pin = fixtures.OnboardingApiTests.pin
    save = fixtures.OnboardingApiTests.save
    post = fixtures.OnboardingApiTests.post

    def setUp(self):
        fixtures.OnboardingApiTests.setUp(self)
        self.patch_review_root = patch.object(review, 'ROOT', self.installed)
        self.patch_review_root.start(); self.addCleanup(self.patch_review_root.stop)
        (self.checkout / 'feature.js').write_text('export const enabled = true;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Candidate')
        candidate = self.git_run('rev-parse', 'HEAD')
        requirement = self.case / 'requirement.json'
        requirement.write_text(json.dumps({'original': 'Enable feature', 'criteria': [{'id': 'enable', 'text': 'Enable feature'}]}))
        plan = self.case / 'plan.txt'; plan.write_text('Approved scope: add feature flag; no deployment.')
        self.c.update(purpose=review.PURPOSE, source={'checkout': str(self.checkout), 'baselineCommit': self.commit,
            'candidateCommit': candidate, 'git': str(self.git), 'gitSha256': self.git_pin},
            requirement=self.pin(requirement), approvedPlan=self.pin(plan), sourcePaths=['README.md'])
        del self.c['packet']; self.save()
        self.body = {'summary': 'Source flag added; runtime remains unverified.', 'findings': [],
            'coverage': [{'requirementId': 'enable', 'status': 'covered', 'analysis': 'Flag enabled in source.',
                'sources': [{'path': 'candidate/feature.js', 'startLine': 1, 'endLine': 1}]}], 'unknowns': []}
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)

    def preview(self): return review.run(self.config, self.config_sha, self.state)
    def execute(self, post=None):
        return review.run(self.config, self.config_sha, self.state, self.preview()['intentSha256'], transport=post or self.post)

    def test_preview_fresh_exact_and_read_only(self):
        value = self.preview(); packet = json.loads(value['request']['input'])
        self.assertFalse(self.state.exists()); self.assertFalse(Path(self.c['campaignDirectory']).exists())
        self.assertEqual(packet['changes'][0]['path'], 'feature.js')
        self.assertIsNone(packet['changes'][0]['before'])
        self.assertIn('+export const enabled = true;', packet['changes'][0]['diff'])
        self.assertNotIn('previous_response_id', value['request'])
        self.assertEqual(value['request']['reasoning'], {'effort': 'xhigh'})
        self.assertNotIn('tools', value['request'])

    def test_new_bounded_packet_reports_exact_counts_without_changing_legacy(self):
        (self.checkout / 'feature.js').write_bytes(b'first\r\nsecond\r\nlast')
        (self.checkout / 'empty.txt').write_bytes(b'')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Line ending fixture')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        args = (self.installed, self.c['source'], json.loads(api.pinned(self.c['requirement'])),
                api.pinned(self.c['approvedPlan']).decode(), ['README.md'])
        legacy = review.build_packet(*args, deduplicate_unchanged=True)
        packet = review.build_packet(*args, deduplicate_unchanged=True, citation_protocol=review.CITATION_PROTOCOL)
        self.assertEqual(packet['citationProtocol'], review.CITATION_PROTOCOL)
        self.assertEqual(next(s['lineCount'] for s in packet['sources'] if s['path'] == 'candidate/feature.js'), 3)
        self.assertEqual(next(s['lineCount'] for s in packet['sources'] if s['path'] == 'candidate/empty.txt'), 0)
        projected = copy.deepcopy(packet); del projected['citationProtocol']
        for source in projected['sources']:
            self.assertEqual(source.pop('lineCount'), len(source['text'].splitlines(keepends=True)))
        self.assertEqual(projected, legacy)

    def test_explicit_source_byte_limit_retains_whole_large_context_and_preserves_default(self):
        paths = ['context-' + str(i) + '.css' for i in range(6)]
        text = '/* complete source */\n' + 'a{color:red}\n' * 16000
        self.assertLess(len(text.encode()), 262144)
        for path in paths:(self.checkout / path).write_text(text)
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Whole stylesheet context')
        baseline = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'feature.js').write_text('export const enabled = false;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Different feature')
        source = {**self.c['source'], 'baselineCommit': baseline, 'candidateCommit': self.git_run('rev-parse', 'HEAD')}
        args = (self.installed, source, json.loads(api.pinned(self.c['requirement'])),
                api.pinned(self.c['approvedPlan']).decode(), paths)
        with self.assertRaisesRegex(Refused, '1 MiB; no silent truncation'):
            review.build_packet(*args, deduplicate_unchanged=True)
        packet = review.build_packet(*args, deduplicate_unchanged=True, max_source_bytes=2097152)
        self.assertEqual(packet['sourceByteLimit'], 2097152)
        for path in paths:
            self.assertEqual(next(s['text'] for s in packet['sources'] if s['path'] == 'candidate/' + path), text)
        self.assertGreater(sum(len(s['text'].encode()) for s in packet['sources']), 1048576)
        for value in (None, True, 0, 2097153, 1.5):
            with self.subTest(value=value), self.assertRaises(Refused):
                review.build_packet(*args, max_source_bytes=value)

    def test_workspace_prepare_protocol_is_opt_in_and_intent_bound(self):
        import flow_workspace_review as workspace
        self.c.update(purpose=workspace.PURPOSE,
            workspaceContext={'protectedPaths': ['feature.js'], 'stages': []})
        self.save()
        _, _, old_packet, old_payload, old_intent = workspace.prepare(self.config, self.config_sha, self.state)
        self.assertNotIn('citationProtocol', old_packet)
        self.assertNotIn('citationProtocol', old_intent)
        self.assertEqual(old_payload['text']['format']['schema'], workspace.SCHEMA)
        self.c['workspaceContext']['refineExecutionPlan'] = True
        self.save()
        _, _, packet, payload, intent = workspace.prepare(self.config, self.config_sha, self.state)
        self.assertEqual(packet['citationProtocol'], review.CITATION_PROTOCOL)
        self.assertEqual(intent['citationProtocol'], review.CITATION_PROTOCOL)
        self.assertEqual(payload['text']['format']['name'], 'candidate_workspace_refinement_v2')
        self.assertIn(review.COUNT_CITATION_INSTRUCTIONS, payload['instructions'])
        self.assertNotIn('endLine', json.dumps(payload['text']['format']['schema']))
        self.assertEqual(intent['packetSha256'], api.digest(api.encoded(packet)))
        self.assertEqual(intent['requestSha256'], api.digest(api.encoded(payload)))
        self.assertFalse(self.state.exists()); self.assertFalse(Path(self.c['campaignDirectory']).exists())

    def test_new_protocol_does_not_reinterpret_rejected_legacy_output(self):
        import flow_workspace_review as workspace
        self.c.update(purpose=workspace.PURPOSE,
            workspaceContext={'protectedPaths': ['feature.js'], 'stages': [], 'refineExecutionPlan': True})
        Path(self.c['approvedPlan']['path']).write_text(json.dumps({'assessment': {'steps': []}}))
        self.c['approvedPlan'] = self.pin(Path(self.c['approvedPlan']['path']))
        self.body.update(workspacePolicy={'generatedOutputs': [], 'retainedArchiveInputs': [], 'unknowns': []},
                         executionRefinement=[])
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)
        self.save()
        preview = workspace.run(self.config, self.config_sha, self.state)
        result = workspace.run(self.config, self.config_sha, self.state, preview['intentSha256'], transport=self.post)
        self.assertEqual(result['status'], 'review-rejected')
        self.assertIsNone(result['review']); self.assertFalse((self.state / 'review.json').exists())
        self.assertEqual(json.loads((self.state / 'response.json').read_bytes()), self.answer)
        self.assertEqual(api.status(self.state), result)
        before = {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        with self.assertRaises(Refused): workspace.run(self.config, self.config_sha, self.state)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()})

    def test_compact_unchanged_context_binds_both_revisions_without_alias(self):
        args = (self.installed, self.c['source'], json.loads(api.pinned(self.c['requirement'])),
                api.pinned(self.c['approvedPlan']).decode(), ['README.md'])
        old = review.build_packet(*args)
        new = review.build_packet(*args, deduplicate_unchanged=True)
        self.assertEqual(old['schemaVersion'], 1)
        self.assertNotIn('unchangedSources', old)
        self.assertEqual(new['schemaVersion'], 2)
        self.assertEqual(old['changes'], new['changes'])
        names = {row['path'] for row in new['sources']}
        self.assertIn('candidate/README.md', names)
        self.assertNotIn('baseline/README.md', names)
        entry = new['unchangedSources'][0]
        self.assertEqual(entry['path'], 'README.md')
        self.assertEqual(entry['displayedSource'], 'candidate/README.md')
        self.assertEqual(entry['gitObject'], self.git_run('rev-parse', self.commit + ':README.md'))
        self.assertEqual(entry['gitObject'], self.git_run('rev-parse', self.c['source']['candidateCommit'] + ':README.md'))
        body = copy.deepcopy(self.body)
        body['coverage'][0]['sources'][0]['path'] = 'baseline/README.md'
        response = copy.deepcopy(self.answer)
        response['output'][0]['content'][0]['text'] = json.dumps(body)
        with self.assertRaises(Refused): review.parse_response(response, new)

    def test_compact_scope_counts_unique_text_without_changing_limit(self):
        for i in range(3):
            (self.checkout / ('context' + str(i) + '.txt')).write_text('plain text ' * 20000)
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Large unchanged context')
        self.c['source']['baselineCommit'] = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'feature.js').write_text('export const enabled = false;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Change only feature')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        args = (self.installed, self.c['source'], json.loads(api.pinned(self.c['requirement'])),
                api.pinned(self.c['approvedPlan']).decode(), ['context' + str(i) + '.txt' for i in range(3)])
        with self.assertRaisesRegex(Refused, '1 MiB'): review.build_packet(*args)
        packet = review.build_packet(*args, deduplicate_unchanged=True)
        self.assertEqual(len(packet['unchangedSources']), 3)
        self.assertEqual(len(packet['sources']), 5)
        self.assertEqual(len(packet['changes']), 1)
        # Mode-only changes must retain both sides even if the blob is identical.
        (self.checkout / 'context0.txt').chmod(0o755)
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Mode change')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        packet = review.build_packet(*args, deduplicate_unchanged=True)
        self.assertEqual(len(packet['unchangedSources']), 2)
        self.assertIn('baseline/context0.txt', {s['path'] for s in packet['sources']})
        with self.assertRaises(Refused): review.build_packet(*args, deduplicate_unchanged=1)

    def test_opt_in_command_context_uses_committed_whole_files_and_normal_identity(self):
        (self.checkout / 'package.json').write_text(json.dumps({'scripts': {'test': 'node test.js'}}))
        (self.checkout / 'test.js').write_text("import './helper.js';\n")
        (self.checkout / 'helper.js').write_text('export const finite = true;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Command baseline')
        self.c['source']['baselineCommit'] = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'feature.js').write_text('export const enabled = false;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Candidate change')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'test.js').write_text('UNCOMMITTED MUST NOT BE READ\n')
        args = (self.installed, self.c['source'], json.loads(api.pinned(self.c['requirement'])),
                api.pinned(self.c['approvedPlan']).decode(), ['README.md'])
        old = review.build_packet(*args, deduplicate_unchanged=True)
        new = review.build_packet(*args, deduplicate_unchanged=True,
            command_source_stages=[{'root': '.', 'operation': 'run', 'script': 'test'}])
        self.assertNotIn('commandSourceContext', old)
        self.assertNotIn('candidate/test.js', {s['path'] for s in old['sources']})
        self.assertEqual(next(s['text'] for s in new['sources'] if s['path'] == 'candidate/test.js'),
                         "import './helper.js';\n")
        self.assertTrue({'package.json', 'test.js', 'helper.js'} <=
                        {s['path'] for s in new['unchangedSources']})
        self.assertEqual(new['changes'], old['changes'])
        self.assertEqual(new['commandSourceContext']['commit'], self.c['source']['candidateCommit'])

    def test_refinement_secret_omission_cannot_reenter_via_explicit_or_changed_paths(self):
        (self.checkout / 'package.json').write_text(json.dumps({'scripts': {'test': 'node test.js'}}))
        (self.checkout / 'test.js').write_text('const password = "synthetic-secret-value";\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Secret fixture baseline')
        self.c['source']['baselineCommit'] = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'feature.js').write_text('export const enabled = false;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Unrelated candidate')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        stages = [{'root': '.', 'operation': 'run', 'script': 'test'}]
        def build(paths, refine=True):
            return review.build_packet(self.installed, self.c['source'],
                json.loads(api.pinned(self.c['requirement'])), api.pinned(self.c['approvedPlan']).decode(),
                paths, deduplicate_unchanged=True, command_source_stages=stages if refine else None)
        # Derivation withholds the secret, but an explicit source overlap must
        # refuse the whole packet rather than reintroduce it during assembly.
        with self.assertRaisesRegex(Refused, 'suspected secret'): build(['test.js'])
        self.assertIn('candidate/test.js', {s['path'] for s in build(['test.js'], False)['sources']})
        self.assertNotIn('candidate/test.js', {s['path'] for s in build(['README.md'])['sources']})
        # Candidate-only secret in a changed path is equally blocked.
        self.c['source']['baselineCommit'] = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'test.js').write_text('const password = "changed-synthetic-secret";\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Changed secret fixture')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Refused, 'suspected secret'): build(['README.md'])
        # Removing the literal cannot expose its previous value in a baseline
        # source or diff. Both displayed revisions are checked independently.
        self.c['source']['baselineCommit'] = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'test.js').write_text('export const safe = true;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Remove fixture literal')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Refused, 'suspected secret'): build(['README.md'])

    def test_refinement_excluded_config_path_cannot_reenter_as_change(self):
        (self.checkout / 'config').mkdir()
        (self.checkout / 'config' / 'runtime.js').write_text('export const enabled = true;\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Config fixture')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Refused, 'credential configuration path'):
            review.build_packet(self.installed, self.c['source'],
                json.loads(api.pinned(self.c['requirement'])), api.pinned(self.c['approvedPlan']).decode(),
                ['README.md'], command_source_stages=[])

    def test_retained_review_and_citations(self):
        value = self.execute(); self.assertEqual(value['status'], 'source-review-ready')
        cite = value['review']['coverage'][0]['sources'][0]
        self.assertEqual(cite['quote'], 'export const enabled = true;\n')
        self.assertEqual(cite['sha256'], api.digest(cite['quote'].encode()))
        self.assertEqual(api.status(self.state), value)
        with self.assertRaises(Refused): self.execute()
        self.assertEqual(self.calls, ['responses/input_tokens', 'responses'])

    def test_unknowns_and_all_defects_block(self):
        self.body['unknowns'] = ['Browser acceptance not performed.']
        self.body['findings'] = [{'id': 'bug-1', 'description': 'Missing off switch.', 'severity': 'low',
            'requirementIds': ['enable'], 'sources': copy.deepcopy(self.body['coverage'][0]['sources'])}]
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)
        value = self.execute(); self.assertEqual(value['status'], 'review-blocked')
        self.assertEqual(len(value['review']['blockers']), 2)

    def test_strict_invalid_output_retained(self):
        self.body['coverage'][0]['sources'][0]['endLine'] = 50
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)
        value = self.execute(); self.assertEqual(value['status'], 'review-rejected')
        self.assertTrue((self.state / 'response.json').exists())
        self.assertIsNone(value['review'])

    def test_unknown_transport_not_replayed(self):
        def post(endpoint, payload):
            if endpoint == 'responses': raise OSError('Lost terminal response')
            return {'input_tokens': 80}
        value = self.execute(post); self.assertEqual(value['status'], 'unknown')
        self.assertEqual(value['reservation']['status'], 'unknown')
        with self.assertRaises(Refused): self.execute()

    def test_semantic_refusals(self):
        packet = json.loads(self.preview()['request']['input'])
        for mutation in ('extra', 'duplicate', 'no-citation', 'forged-hash'):
            body = copy.deepcopy(self.body)
            if mutation == 'extra': body['authority'] = True
            if mutation == 'duplicate': body['coverage'] *= 2
            if mutation == 'no-citation': body['coverage'][0]['sources'] = []
            if mutation == 'forged-hash': body['coverage'][0]['sources'][0]['sha256'] = 'a'*64
            response = copy.deepcopy(self.answer); response['output'][0]['content'][0]['text'] = json.dumps(body)
            with self.subTest(mutation=mutation), self.assertRaises(Refused): review.parse_response(response, packet)

    def test_history_continuity_and_refusal_of_second_campaign(self):
        value = self.execute()
        self.assertGreater(value['reservation']['reservedNanoUsd'], 0)
        for path, raw in self.history_before.items(): self.assertEqual(Path(path).read_bytes(), raw)
        self.state = self.case / 'second'; self.c['campaignDirectory'] = str(self.case / 'other-campaign'); self.save()
        with self.assertRaises(Refused): self.execute()

    def test_evidence_verification_rejects_tampered_quote_and_blockers(self):
        self.execute()
        pin = self.pin(self.state / 'review.json'); packet_pin = self.pin(self.state / 'packet.json')
        self.assertEqual(review.verify_evidence(pin, packet_pin)['blockers'], [])
        retained = json.loads((self.state / 'review.json').read_bytes())
        retained['coverage'][0]['sources'][0]['quote'] = 'forged'
        (self.state / 'review.json').write_bytes(api.encoded(retained))
        with self.assertRaises(Refused): review.verify_evidence(self.pin(self.state / 'review.json'), packet_pin)
        self.assertEqual(api.status(self.state)['status'], 'integrity-failed')

    def test_wrong_authorization_has_no_effects(self):
        with self.assertRaises(Refused):
            review.run(self.config, self.config_sha, self.state, '0'*64, transport=self.post)
        self.assertFalse(self.state.exists()); self.assertEqual(self.calls, [])

    def test_duplicate_request_new_state_still_refused(self):
        self.execute(); self.state = self.case / 'duplicate'
        value = self.execute()
        self.assertEqual(value['status'], 'preflight-refused')
        self.assertEqual(self.calls.count('responses'), 1)

    def test_terminal_incomplete_is_not_review(self):
        self.answer['status'] = 'incomplete'
        value = self.execute()
        self.assertEqual(value['status'], 'model-incomplete-or-failed'); self.assertIsNone(value['review'])
        self.assertTrue((self.state / 'response.json').exists())

    def test_pin_change_after_token_count_stops_generation(self):
        def post(endpoint, payload):
            self.assertEqual(endpoint, 'responses/input_tokens')
            Path(self.c['approvedPlan']['path']).write_text('changed')
            return {'input_tokens': 80}
        value = self.execute(post)
        self.assertEqual(value['status'], 'preflight-refused')
        self.assertIsNone(value['reservation'])

    def test_deletion_and_mode_only_change_are_bound(self):
        (self.checkout / 'README.md').unlink()
        (self.checkout / 'feature.js').chmod(0o755)
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Delete and change mode')
        self.c['source']['candidateCommit'] = self.git_run('rev-parse', 'HEAD'); self.save()
        packet = json.loads(self.preview()['request']['input'])
        rows = {row['path']: row for row in packet['changes']}
        self.assertIsNone(rows['README.md']['after'])
        self.assertEqual(rows['feature.js']['after']['mode'], '100755')

if __name__ == '__main__': unittest.main()
