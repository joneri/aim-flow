"""Synthetic offline parser/transport/provenance tests; no product commands or API."""
import base64
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_execution_review as review
from aim_ci.owned_paths import Refused


class ExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.source = {'schemaVersion': 1,
            'requirement': {'original': 'Show a blue button.', 'criteria': [{'id': 'one', 'text': 'Show a blue button.'}]},
            'approvedPlan': 'Implement a button', 'baseline': {'commit': 'a'*40, 'tree': 'a'*40},
            'candidate': {'commit': 'b'*40, 'tree': 'b'*40}, 'changes': [],
            'sources': [{'path': 'candidate/main.js', 'text': 'button.style.color = "blue";\n', 'sha256': 'c'*64}]}
        self.sr = {'findings': [], 'workspacePolicy': {'unknowns': []}, 'blockers': ['Needs actual visual review']}
        self.verified = {'validation': {'status': 'passed', 'intentSha256': 'd'*64,
            'report': {'runtime': {'actualNodeVersion': 'v24'}, 'stages': [{'exitCode': 0}], 'outputs': {}}}}
        self.body = {'summary': 'Reviewed evidence', 'findings': [], 'unknowns': [], 'priorDispositions': [
            {'itemId': 'source-blocker:0', 'status': 'unresolved', 'analysis': 'Runtime evidence absent',
             'sources': [{'path': 'candidate/main.js', 'startLine': 1, 'endLine': 1}],
             'evidenceIds': []}], 'coverage': [{
            'requirementId': 'one', 'status': 'satisfied', 'analysis': 'Visible blue button',
            'evidenceKinds': ['visual'], 'sources': [{'path': 'candidate/main.js', 'startLine': 1, 'endLine': 1}],
            'evidenceIds': ['runtime', 'stage-0']}]}

    def packet(self):
        return review.evidence_packet(self.source, self.sr, self.verified, {'sha256': 'e'*64})[0]

    def parsed(self, body=None, packet=None):
        response = {'output': [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': json.dumps(body or self.body)}]}]}
        return review.parse_response(response, packet or self.packet())

    def artifact(self, media='image/png', raw=b'\x89PNG\r\n\x1a\nsynthetic'):
        row = {'path': 'results/view.png' if media == 'image/png' else 'results/test.json', 'mediaType': media,
            'bytes': len(raw), 'sha256': review.api.digest(raw), 'base64': base64.b64encode(raw).decode()}
        report = self.verified['validation']['report']
        report['artifacts'] = [row]
        report['outputs']['project/'+row['path']] = {'type': 'file', 'bytes': len(raw), 'sha256': row['sha256']}
        self.body['coverage'][0]['evidenceIds'].append('artifact:' + row['path'])
        self.body['priorDispositions'][0].update(status='resolved', analysis='Actual generated image/test reviewed',
            evidenceIds=['artifact:' + row['path']])
        return row

    def test_assertion_policy_exact_evidence_ids_and_legacy_schema(self):
        packet=self.packet()
        legacy=review.response_schema(packet)
        self.assertNotIn('enum', legacy['properties']['coverage']['items']['properties']['evidenceIds']['items'])
        packet['evidenceEvaluationPolicy']=review.EVIDENCE_POLICY
        schema=review.response_schema(packet)
        for field in ('coverage','priorDispositions'):
            self.assertEqual(schema['properties'][field]['items']['properties']['evidenceIds']['items']['enum'],
                [row['id'] for row in packet['evidence']])
            self.assertEqual(schema['properties'][field]['items']['properties']['sources']['items']['properties']['path']['enum'],
                [row['path'] for row in packet['sources']])
        self.artifact(); packet=self.packet();packet['evidenceEvaluationPolicy']=review.EVIDENCE_POLICY
        self.assertTrue(self.parsed(packet=packet)['localFeatureAccepted'])
        self.body['priorDispositions'][0]['evidenceIds']=['artifact:results/view.png (debug)']
        with self.assertRaises(Refused): self.parsed(packet=packet)

    def test_assertion_policy_refuses_assessment_only_source_paths(self):
        self.artifact(); packet=self.packet();packet['evidenceEvaluationPolicy']=review.EVIDENCE_POLICY
        self.assertTrue(self.parsed(packet=packet)['localFeatureAccepted'])
        self.body['priorDispositions'][0]['sources'][0]['path']='docs/assessment-only-runbook.md'
        with self.assertRaises(Refused): self.parsed(packet=packet)

    def test_material_local_unknown_still_blocks_under_assertion_policy(self):
        self.artifact(); packet=self.packet();packet['evidenceEvaluationPolicy']=review.EVIDENCE_POLICY
        self.body['unknowns']=['Required keyboard activation remains unverified.']
        result=self.parsed(packet=packet)
        self.assertFalse(result['localFeatureAccepted'])
        self.assertIn(self.body['unknowns'][0],result['blockers'])

    def test_assertion_policy_does_not_accept_exit_code_without_artifacts(self):
        packet=self.packet();packet['evidenceEvaluationPolicy']=review.EVIDENCE_POLICY
        self.body['coverage'][0]['evidenceKinds']=['behavior']
        self.assertFalse(self.parsed(packet=packet)['localFeatureAccepted'])
        self.artifact();packet=self.packet();packet['evidenceEvaluationPolicy']=review.EVIDENCE_POLICY
        self.body['coverage'][0]['status']='defect'
        self.assertFalse(self.parsed(packet=packet)['localFeatureAccepted'])

    def nonblocking_case(self):
        self.artifact()
        self.sr['unknowns'] = ['Complete unchanged dependency closure was not displayed.']
        self.sr['blockers'] = list(self.sr['unknowns'])
        self.body['priorDispositions'] = [dict(self.body['priorDispositions'][0],
            itemId=origin+':0', status='nonblocking', evidenceIds=[],
            nonblockingRationale={'scope':'Unchanged dependency implementation is outside the button change.',
                'materiality':'No dependency changes or demonstrated material failures; original button criteria retain their evidence.'})
            for origin in ('source-unknown','source-blocker')]
        packet = self.packet(); packet['unknownDispositionPolicy'] = review.UNKNOWN_POLICY
        return packet

    def test_nonblocking_model_uncertainty_retained_not_resolved(self):
        result = self.parsed(packet=self.nonblocking_case())
        self.assertTrue(result['localFeatureAccepted'])
        self.assertEqual(len(result['nonblockingPriorItems']), 2)
        self.assertEqual(result['nonblockingPriorItems'][0]['status'], 'nonblocking')

    def test_nonblocking_cannot_waive_original_criterion(self):
        packet = self.nonblocking_case()
        self.body['coverage'][0].update(status='uncertain', analysis='Required visual evidence is incomplete')
        self.assertFalse(self.parsed(packet=packet)['localFeatureAccepted'])

    def test_nonblocking_refuses_protected_origins(self):
        for origin in ('source-finding','workspace-unknown','acceptance-obligation','unsupported-step'):
            with self.subTest(origin=origin):
                self.setUp(); packet=self.nonblocking_case()
                packet['priorOpenItems'][0]['origin']=origin
                if origin=='unsupported-step': packet['priorOpenItems'][0]['value']={'step':'required-local-test'}
                with self.assertRaises(Refused): self.parsed(packet=packet)

    def test_nonblocking_requires_optin_citations_and_rationales(self):
        for missing in ('policy','sources','rationale','duplicate'):
            with self.subTest(missing=missing):
                self.setUp(); packet=self.nonblocking_case()
                if missing=='policy': packet.pop('unknownDispositionPolicy')
                if missing=='sources': self.body['priorDispositions'][0]['sources']=[]
                if missing=='rationale': self.body['priorDispositions'][0]['nonblockingRationale']['materiality']=''
                if missing=='duplicate': packet['priorOpenItems'][1]['value']='Actual mandatory local gate failure'
                with self.assertRaises(Refused): self.parsed(packet=packet)

    def test_legacy_parse_has_no_new_policy_or_followup_fields(self):
        self.artifact(); result=self.parsed()
        self.assertTrue(result['localFeatureAccepted'])
        self.assertNotIn('unknownDispositionPolicy', result)
        self.assertNotIn('nonblockingPriorItems', result)

    def test_compact_stage_inventory_is_exactly_bound_not_resent(self):
        self.source.update(schemaVersion=2, unchangedSources=[])
        report = self.verified['validation']['report']
        inventory = {'main.js': {'sha256': 'f'*64, 'bytes': 50}}
        report['sourcesBefore'] = inventory
        report['stages'][0].update(sourcesAfter=copy.deepcopy(inventory), stdout='measured output',
                                  stderr='', projectArgv=['npm', 'run', 'test'])
        packet = self.packet()
        row = packet['evidence'][1]['value']
        self.assertEqual(row['stdout'], 'measured output')
        self.assertEqual(row['projectArgv'], ['npm', 'run', 'test'])
        self.assertEqual(row['sourcesAfter']['sha256'], review.api.digest(review.api.encoded(inventory)))
        self.assertEqual(row['sourcesAfter']['entries'], 1)
        self.assertEqual(packet['unchangedSources'], [])
        self.assertNotIn('main.js', row['sourcesAfter'])
        self.source['schemaVersion'] = 1
        self.assertEqual(self.packet()['evidence'][1]['value']['sourcesAfter'], inventory)
        self.source['schemaVersion'] = 2
        report['stages'][0]['sourcesAfter']['main.js']['bytes'] = 51
        with self.assertRaises(Refused): self.packet()

    def test_compact_inventory_equality_is_canonical_not_python_coercion(self):
        self.source.update(schemaVersion=2, unchangedSources=[])
        report = self.verified['validation']['report']
        report['sourcesBefore'] = {'main.js': {'bytes': 1}}
        for value in (True, 1.0):
            report['stages'][0]['sourcesAfter'] = {'main.js': {'bytes': value}}
            with self.subTest(value=value), self.assertRaises(Refused): self.packet()

    def test_compact_baseline_plan_quote_uses_only_verified_unchanged_alias(self):
        text = 'Example path: /home/example/app\n'
        sha = review.api.digest(text.encode())
        self.source.update(schemaVersion=2, unchangedSources=[{'path':'README.md',
            'displayedSource':'candidate/README.md', 'sha256':sha, 'mode':'100644', 'gitObject':'a'*40}])
        self.source['sources'].append({'path':'candidate/README.md', 'sha256':sha, 'text':text})
        citation = {'path':'README.md', 'startLine':1, 'endLine':1, 'sha256':sha, 'quote':text}
        self.source['approvedPlan'] = json.dumps({'sources':[citation]})
        self.packet()
        for change in ('hash', 'alias', 'quote', 'sibling'):
            saved = copy.deepcopy(self.source)
            if change == 'hash': self.source['unchangedSources'][0]['sha256'] = 'b'*64
            if change == 'alias': self.source['unchangedSources'][0]['path'] = 'OTHER.md'
            if change == 'quote':
                self.source['approvedPlan'] = json.dumps({'sources':[{**citation,'quote':'/home/private/secret'}]})
            if change == 'sibling':
                self.source['approvedPlan'] = json.dumps({'sources':[{**citation,'private':'/home/private/secret'}]})
            with self.subTest(change=change), self.assertRaises(Refused): self.packet()
            self.source = saved

    def test_exit_zero_and_source_cannot_accept_visual(self):
        result = self.parsed()
        self.assertFalse(result['productAccepted'])
        self.assertEqual(result['coverage'][0]['status'], 'uncertain')

    def test_retained_image_and_exact_source_can_support_bounded_review(self):
        self.artifact()
        packet, images = review.evidence_packet(self.source, self.sr, self.verified, {'sha256': 'e'*64})
        self.assertEqual(len(images), 1)
        result = self.parsed(packet=packet)
        self.assertTrue(result['productAccepted'])
        self.assertEqual(result['coverage'][0]['sources'][0]['quote'], self.source['sources'][0]['text'])
        self.assertFalse(result['deploymentAuthorized'])

    def test_json_does_not_replace_visual(self):
        self.artifact('application/json', b'{"observed":"blue"}')
        self.assertFalse(self.parsed()['productAccepted'])

    def test_png_does_not_replace_behavior(self):
        self.artifact(); self.body['coverage'][0]['evidenceKinds'] = ['behavior']
        self.assertFalse(self.parsed()['productAccepted'])

    def test_hash_inventory_and_duplicate_artifacts_refused(self):
        self.artifact(); self.verified['validation']['report']['artifacts'][0]['sha256'] = '0'*64
        with self.assertRaises(Refused): self.packet()
        self.setUp(); row = self.artifact(); self.verified['validation']['report']['artifacts'].append(row)
        with self.assertRaises(Refused): self.packet()
        self.setUp(); row = self.artifact(); self.verified['validation']['report']['outputs'] = {}
        with self.assertRaises(Refused): self.packet()

    def test_criteria_and_evidence_must_be_exact(self):
        self.body['coverage'][0]['requirementId'] = 'invented'
        with self.assertRaises(Refused): self.parsed()
        self.setUp(); self.body['coverage'][0]['evidenceIds'].append('invented')
        with self.assertRaises(Refused): self.parsed()
        self.setUp(); self.body['coverage'][0]['sources'][0]['endLine'] = 2
        with self.assertRaises(Refused): self.parsed()

    def test_findings_unknowns_and_unsupported_remain_blockers(self):
        self.artifact(); self.body['findings'] = ['Missing keyboard support']
        self.assertFalse(self.parsed()['productAccepted'])
        self.body['findings'] = []; self.verified['agentUnknowns'] = ['Unverified external dependency']
        self.body['priorDispositions'].append({'itemId':'assessment-unknown:0', 'status':'unresolved',
            'analysis':'External dependency remains unverified', 'sources':[], 'evidenceIds':[]})
        self.assertFalse(self.parsed()['productAccepted'])

    def delivery(self):
        step = {'id':'release', 'area':'deployment', 'command':'release later', 'sources':[]}
        self.source['approvedPlan'] = json.dumps({'assessment':{'steps':[step]}})
        self.verified['unsupportedSteps'] = [{'step':'release','reason':'Later adapter required'}]
        self.body['priorDispositions'].append({'itemId':'unsupported-step:0', 'status':'pending-delivery',
            'analysis':'Hosted release remains pending its later gate',
            'sources':[{'path':'candidate/main.js','startLine':1,'endLine':1}],
            'evidenceIds':['delivery-step:release']})

    def test_delivery_pending_does_not_deadlock_local_acceptance(self):
        self.artifact(); self.delivery()
        result = self.parsed()
        self.assertTrue(result['localFeatureAccepted'])
        self.assertFalse(result['productAccepted']); self.assertFalse(result['deliveryReady'])
        self.assertIn('unsupported-step:0', [x.get('itemId') for x in result['pendingDeliveryObligations']])

    def test_local_png_cannot_resolve_typed_delivery(self):
        self.artifact(); self.delivery()
        self.body['priorDispositions'][-1].update(status='resolved', evidenceIds=['artifact:results/view.png'])
        with self.assertRaisesRegex(Refused, 'cannot resolve typed'):
            self.parsed()

    def test_local_json_cannot_resolve_typed_ci(self):
        self.artifact('application/json', b'{"passed":true}')
        self.body['coverage'][0]['evidenceKinds'] = ['behavior']
        self.delivery()
        plan = json.loads(self.source['approvedPlan']); plan['assessment']['steps'][0]['area'] = 'ci'
        self.source['approvedPlan'] = json.dumps(plan)
        self.body['priorDispositions'][-1].update(status='resolved', evidenceIds=['artifact:results/test.json'])
        with self.assertRaisesRegex(Refused, 'cannot resolve typed'):
            self.parsed()

    def test_substituted_acceptance_requires_artifacts_and_cannot_defer(self):
        self.artifact(); self.delivery()
        self.verified['acceptanceObligations'] = [{'id':'manual', 'independentChecks':['Screen reader names and real device behavior']}]
        self.body['priorDispositions'].append({'itemId':'acceptance-obligation:0', 'status':'resolved',
            'analysis':'Source only cannot close manual observation',
            'sources':[{'path':'candidate/main.js','startLine':1,'endLine':1}], 'evidenceIds':['stage-0']})
        self.assertFalse(self.parsed()['localFeatureAccepted'])
        self.body['priorDispositions'][-1].update(status='pending-delivery', evidenceIds=['delivery-step:release'])
        with self.assertRaises(Refused): self.parsed()

    def test_literal_ci_cannot_be_deferred_to_hosted_delivery(self):
        self.artifact(); self.delivery()
        plan = json.loads(self.source['approvedPlan'])
        plan['assessment']['steps'][0].update(area='ci', command='npm run arbitrary-gate', workingDirectory='.')
        self.source['approvedPlan'] = json.dumps(plan)
        self.assertFalse(any(e['kind'] == 'delivery-obligation' for e in self.packet()['evidence']))
        with self.assertRaises(Refused):
            self.parsed()

    def test_literal_ci_is_not_automatically_pending_hosted_delivery(self):
        self.artifact()
        self.source['approvedPlan'] = json.dumps({'assessment': {'steps': [
            {'id': 'local-gate', 'area': 'ci', 'command': 'npm run arbitrary-gate', 'workingDirectory': '.'}]}})
        self.assertEqual(self.parsed()['pendingDeliveryObligations'], [])

    def test_delivery_gate_survives_source_and_coverage_aliases(self):
        self.artifact(); self.delivery()
        # Even if a prior-obligation alias is considered resolved, canonical typed
        # delivery evidence survives independently and prevents full acceptance.
        self.verified['unsupportedSteps'] = []
        self.body['priorDispositions'].pop()
        self.body['coverage'][0]['evidenceIds'].append('delivery-step:release')
        result = self.parsed()
        self.assertTrue(result['localFeatureAccepted'])
        self.assertFalse(result['productAccepted'])
        self.assertEqual(result['pendingDeliveryObligations'][0]['deliveryStepId'], 'delivery-step:release')
        self.assertEqual(result['pendingDeliveryObligations'][0]['status'], 'pending-delivery')

    def test_prior_runtime_unknown_resolves_only_with_new_artifact(self):
        self.body['priorDispositions'][0].update(status='resolved', evidenceIds=['stage-0'])
        result = self.parsed()
        self.assertEqual(result['priorDispositions'][0]['status'], 'unresolved')
        self.artifact()
        self.assertTrue(self.parsed()['localFeatureAccepted'])

    def test_prior_obligations_cannot_disappear_or_duplicate(self):
        self.body['priorDispositions'] = []
        with self.assertRaises(Refused): self.parsed()
        self.setUp(); self.body['priorDispositions'] *= 2
        with self.assertRaises(Refused): self.parsed()

    def test_local_test_cannot_borrow_delivery_step(self):
        self.artifact(); self.delivery()
        self.verified['unsupportedSteps'][0]['step'] = 'browser-test'
        with self.assertRaises(Refused): self.parsed()

    def test_defect_cannot_be_deferred_to_delivery(self):
        self.artifact(); self.delivery()
        self.sr['findings'] = [{'description':'Broken button'}]
        self.body['priorDispositions'].append({'itemId':'source-finding:0','status':'pending-delivery',
            'analysis':'Defer defect', 'sources':[{'path':'candidate/main.js','startLine':1,'endLine':1}],
            'evidenceIds':['delivery-step:release']})
        with self.assertRaises(Refused): self.parsed()

    def test_local_path_and_secret_refused_before_disclosure(self):
        self.source['approvedPlan'] = '/Users/someone/private'
        with self.assertRaises(Refused): self.packet()
        with self.assertRaises(Refused): review.safe_text('sk-proj-' + 'x'*30)

    def test_bound_source_home_literals_preserved_with_exact_citations(self):
        text = 'const target = "/home/example/app";\n'
        self.source['sources'][0].update(text=text, sha256=review.api.digest(text.encode()))
        self.source['requirement']['original'] = 'Document installation at /home/example/app.'
        self.source['changes'] = [{'path':'main.js', 'diff':'+const target = "/home/example/app";'}]
        self.artifact()
        packet = self.packet()
        self.assertEqual(packet['sources'][0], self.source['sources'][0])
        self.assertEqual(packet['changes'], self.source['changes'])
        result = self.parsed(packet=packet)
        self.assertEqual(result['coverage'][0]['sources'][0]['quote'], text)
        self.assertTrue(result['localFeatureAccepted'])

    def test_generated_runtime_and_json_home_paths_still_refused(self):
        self.verified['validation']['report']['runtime']['private'] = '/Users/operator/private'
        with self.assertRaises(Refused): self.packet()
        self.setUp(); self.artifact('application/json', b'{"path":"/home/operator/private"}')
        with self.assertRaises(Refused): self.packet()

    def test_exact_source_citation_exemption_cannot_hide_unbound_metadata(self):
        text = 'const target = "/home/example/app";\n'
        source = self.source['sources'][0]
        source.update(text=text, sha256=review.api.digest(text.encode()))
        ref = {'path':source['path'],'startLine':1,'endLine':1,'sha256':source['sha256'],'quote':text}
        self.sr['coverage'] = [{'sources':[ref]}]
        self.packet()
        ref['quote'] = '/Users/operator/private'
        with self.assertRaises(Refused): self.packet()

    def test_original_source_secret_pattern_remains_refused(self):
        self.source['sources'][0]['text'] = 'const token = "sk-proj-' + 'x'*30 + '";'
        with self.assertRaises(Refused): self.packet()

    def test_photograph_filename_is_not_a_credential_substring(self):
        text = "background: url('/assets/photographs/mixing-desk-panagiotis-falcos.jpg');\n"
        self.source['sources'][0].update(text=text, sha256=review.api.digest(text.encode()))
        self.artifact('application/json', b'{"image":"mixing-desk-panagiotis-falcos.jpg","ok":true}')
        packet = self.packet()
        self.assertEqual(packet['sources'][0]['text'], text)
        self.assertEqual(packet['evidence'][-1]['value']['image'], 'mixing-desk-panagiotis-falcos.jpg')

    def test_standalone_secret_tokens_still_refused_in_every_position(self):
        for token in ('sk-' + 'x'*30, 'sk-proj-' + 'x'*30):
            for text in (token, 'const key="'+token+'"', '{"key":"'+token+'"}', 'key='+token):
                with self.subTest(text=text):
                    with self.assertRaisesRegex(Refused, 'possible secret token'):
                        review.safe_text(text, original_paths=True)
        with self.assertRaisesRegex(Refused, 'local home path'):
            review.safe_text('/Users/operator/private')

    def test_preview_credential_refused_without_dispatch(self):
        with patch.object(review, 'dispatch') as dispatch:
            with self.assertRaises(Refused): review.run('c', 's', 'state', key_file=Path('secret'))
            dispatch.assert_not_called()

    def test_response_refusal_and_tool_output(self):
        self.assertIsNone(review.parse_response({'output': [{'type':'message','role':'assistant',
            'content':[{'type':'refusal'}]}]}, self.packet()))
        with self.assertRaises(Refused): review.parse_response({'output':[{'type':'tool_call'}]}, self.packet())


# Real installed package + shared campaign dispatcher. Only upstream execution
# verification is synthetic here; its own integration tests cover actual receipts.
import test_onboarding_api_delivery as fixtures

class ExecutionDispatchTests(unittest.TestCase):
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
        fixture = ExecutionReviewTests(); fixture.setUp(); fixture.artifact()
        self.evidence = fixture
        old = copy.deepcopy(self.c)
        self.vs = self.case / 'validation'; self.vs.mkdir()
        (self.vs/'review-attempt').mkdir(); (self.vs/'container-attempt').mkdir()
        (self.vs/'review-attempt'/'packet.json').write_bytes(review.api.encoded(fixture.source))
        (self.vs/'review-attempt'/'review.json').write_bytes(review.api.encoded(fixture.sr))
        (self.vs/'phase-grant.json').write_text('{"synthetic":true}')
        receipt = self.vs/'container-attempt'/'result.json'; receipt.write_text('{"limitations":["Synthetic unit fixture"]}')
        fixture.verified.update(status='local-validation-passed', review=fixture.sr, workspaceOutputs={'complete': True})
        fixture.verified['validation']['receiptSha256'] = self.pin(receipt)['sha256']
        self.workflow_config = self.case/'workflow.json'
        self.workflow_config.write_bytes(review.api.encoded({k: old[k] for k in
            ('manifestSha256','campaignDirectory','historicalBudget','timeoutSeconds','source')}))
        vc = self.case/'validation.json'
        vc.write_bytes(review.api.encoded({'manifestSha256': old['manifestSha256'],
            'workflowConfig': self.pin(self.workflow_config), 'workflowState': str(self.case/'work')}))
        self.c = {'schemaVersion': 1, 'manifestSha256': old['manifestSha256'],
            'validationConfig': self.pin(vc), 'validationState': str(self.vs),
            'maxOutputTokens': 8192, 'maxOperationNanoUsd': 1000000000}
        self.save()
        self.answer['output'][0]['content'][0]['text'] = json.dumps(fixture.body)
        for patcher in (patch.object(review, 'ROOT', self.installed),
                        patch.object(review.upstream, 'verify', create=True, return_value=fixture.verified)):
            patcher.start(); self.addCleanup(patcher.stop)

    def preview(self): return review.run(self.config, self.config_sha, self.state)
    def post(self, endpoint, payload):
        self.calls.append(endpoint)
        return {'input_tokens': 80} if endpoint == 'responses/input_tokens' else copy.deepcopy(self.answer)
    def execute(self, transport=None):
        return review.run(self.config, self.config_sha, self.state, self.preview()['intentSha256'], transport=transport or self.post)

    def test_preview_exact_multimodal_read_only(self):
        preview = self.preview(); payload = preview['request']
        self.assertFalse(self.state.exists()); self.assertEqual(self.calls, [])
        self.assertNotIn('previous_response_id', payload); self.assertNotIn('tools', payload)
        self.assertEqual(payload['input'][0]['content'][2]['type'], 'input_image')
        self.assertTrue(payload['input'][0]['content'][2]['image_url'].startswith('data:image/png;base64,'))
        self.assertNotIn(str(self.case), json.dumps(payload))
        self.assertEqual(preview['intent']['requestSha256'], review.api.digest(review.api.encoded(payload)))

    def test_dispatch_accounted_and_exact_response_verified(self):
        result = self.execute()
        self.assertEqual(result['status'], 'source-review-ready', result)
        self.assertTrue(result['review']['productAccepted'])
        self.assertEqual(self.calls, ['responses/input_tokens', 'responses'])
        self.assertEqual(review.verify(self.config, self.config_sha, self.state), result['review'])
        with self.assertRaises(Refused): self.execute()

    def test_unknown_generation_never_replayed(self):
        def lost(endpoint, payload):
            if endpoint == 'responses': raise OSError('Synthetic lost reply')
            return {'input_tokens': 80}
        result = self.execute(lost)
        self.assertEqual(result['status'], 'unknown')
        with self.assertRaises(Refused): review.verify(self.config, self.config_sha, self.state)
        with self.assertRaises(Refused): self.execute()

    def test_upstream_failure_prevents_dispatch(self):
        with patch.object(review.upstream, 'verify', create=True, side_effect=Refused('Candidate drift')):
            with self.assertRaises(Refused): self.preview()
        self.assertEqual(self.calls, []); self.assertFalse(self.state.exists())

    def test_tampered_packet_and_config_refused(self):
        self.execute()
        (self.state/'packet.json').write_text('{}')
        with self.assertRaises(Refused): review.verify(self.config, self.config_sha, self.state)
        self.config.write_text('{}')
        with self.assertRaises(Refused): review.verify(self.config, self.config_sha, self.state)

    def test_exact_controller_path_in_metadata_refused(self):
        self.evidence.verified['validation']['report']['runtime']['private'] = str(self.installed / 'private')
        with self.assertRaises(Refused): self.preview()
        self.assertEqual(self.calls, [])

    def test_manual_approval_response_cannot_replace_retained_response(self):
        self.execute()
        (self.state/'review.json').write_text('{"productAccepted":true}')
        with self.assertRaises(Refused): review.verify(self.config, self.config_sha, self.state)

if __name__ == '__main__': unittest.main()
