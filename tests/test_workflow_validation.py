"""Offline orchestration: real workflow/Git/derivation/review/receipts, fake container only."""
import copy
import json
from pathlib import Path
import sys
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_candidate_review as source_review
import flow_workflow_validation as phase
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_requirement_workflow as fixtures


class ContinuationTests(fixtures.RequirementWorkflowTests):
    def setUp(self):
        super().setUp()
        for module in (phase, source_review):
            p = patch.object(module, 'ROOT', self.installed); p.start(); self.addCleanup(p.stop)
        (self.checkout / 'feature.js').write_text('export const name = "custom-stack";\n')
        self.wc['reviewSources'] += ['feature.js', 'web/generate.js']
        (self.checkout/'web/generate.js').write_text('writeFileSync("bundle.zip", makeZip());\nwriteFileSync("evidence/state.json", JSON.stringify(state));\n')
        with zipfile.ZipFile(self.checkout/'web/bundle.zip', 'w') as archive:
            archive.writestr('fixture.txt', 'Synthetic unchanged archive')
        self.wc['editablePaths'] = ['feature.js']
        (self.checkout / 'web/package.json').write_text(json.dumps({'engines': {'node': '>=24'},
            'scripts': {'pretest': 'node generate.js', 'test': 'node test.js'}}))
        (self.checkout / 'web/package-lock.json').write_text('{"lockfileVersion":3}')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Typed npm fixture')
        self.commit = self.git_run('rev-parse', 'HEAD'); self.wc['source']['commit'] = self.commit
        self.wc['onboardingSources'] += ['web/package-lock.json']
        cache = self.case / 'cache'; cache.mkdir()
        python = Path(sys.executable).resolve()
        self.spec = {'format': container.SPEC, 'schemaVersion': 1, 'job': 'stale-job', 'sourcePaths': ['stale-source'],
            'docker': {**self.pin(python), 'endpoint': 'unix:///tmp/test.sock'},
            'image': {'id': 'sha256:' + 'a'*64, 'repoDigest': 'node@sha256:' + 'b'*64,
                'os': 'linux', 'architecture': 'arm64', 'nodeVersion': '24.22.2', 'npmVersion': '11.19.1'},
            'npmCache': {k:v for k,v in container.local.tree_identity(cache).items() if k != 'files'},
            'runtimeReview': ['Synthetic prepared fact; adapter must qualify'], 'sourceReview': ['stale-source-review'],
            'applicationEnvironment': {}, 'timeoutSeconds': 60, 'outputBytes': 1000000,
            'memoryBytes': 256*1024**2, 'processCount': 64, 'workspaceBytes': 64*1024**2}
        self.spec_path = self.case / 'runtime.json'; self.spec_path.write_bytes(api.encoded(self.spec))
        self.catalog = {'format': phase.derivation.CATALOG, 'schemaVersion': 1, 'resources': [
            {'id': 'node', 'kind': 'node-runtime', 'identity': 'prepared synthetic runtime', 'requirements': [],
             'qualification': 'Not run', 'qualificationState': 'prepared',
             'executionSpec': {'backend': 'container', **self.pin(self.spec_path)},
             'capabilities': {'nodeVersion': '24.22.2', 'npmVersion': '11.19.1', 'platform': 'linux', 'architecture': 'arm64'}},
            {'id': 'cache', 'kind': 'npm-cache', 'identity': self.spec['npmCache']['sha256'], 'requirements': [],
             'executionSpec': {'backend': 'container', **self.pin(self.spec_path)},
             'qualification': 'Not run', 'capabilities': {'lockSha256': self.pin(self.checkout / 'web/package-lock.json')['sha256']}}]}
        Path(self.wc['resourceCatalog']['path']).write_bytes(api.encoded(self.catalog))
        self.wc['resourceCatalog'] = self.pin(Path(self.wc['resourceCatalog']['path']))
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
        self.pc = {'schemaVersion': 1, 'manifestSha256': self.wc['manifestSha256'],
            'workflowConfig': self.pin(self.wpath), 'workflowState': str(self.ws), 'python': self.pin(python),
            'reviewMaxOutputTokens': 8192, 'reviewMaxOperationNanoUsd': 1_000_000_000}
        self.ppath = self.case / 'phase.json'; self.ppath.write_bytes(api.encoded(self.pc))
        self.psha = self.pin(self.ppath)['sha256']; self.ps = self.case / 'phase'
        self.review_body = {'summary': 'Source reviewed; browser evidence remains missing.', 'findings': [],
            'coverage': [{'requirementId': 'criterion-001', 'status': 'unverified',
                          'analysis': 'Runtime evidence missing', 'sources': []}],
            'unknowns': ['Browser acceptance is not run'], 'workspacePolicy': {'generatedOutputs': [{'path':'web/bundle.zip', 'reason':'pretest runs generator before consumer',
                'generatorSources':[{'path':'candidate/web/generate.js','startLine':1,'endLine':1}],
                'lifecycleSources':[{'path':'candidate/web/package.json','startLine':1,'endLine':1}]}],
                'retainedArchiveInputs': [], 'unknowns': []}}
        self.container_calls = []

    def assessment(self, packet):
        value = super().assessment(packet)
        first = value['steps'][0]
        first.update(id='install', command='npm ci', workingDirectory='web')
        second = copy.deepcopy(first)
        second.update(id='test', area='tests', command='npm run test', dependsOn=['install'])
        value['steps'] = [first, second]
        return value

    def workflow_post(self, endpoint, payload):
        if endpoint == 'responses/input_tokens' or payload['text']['format']['name'] == 'project_onboarding_compact_v2':
            return super().workflow_post(endpoint, payload)
        self.wcalls.append(endpoint)
        answer = copy.deepcopy(self.answer)
        answer['output'][0]['content'][0]['text'] = json.dumps({'summary': 'Update display name.',
            'edits': [{'path': 'feature.js', 'old': 'custom-stack', 'new': 'improved-stack'}]})
        return answer

    def review_post(self, endpoint, payload):
        self.wcalls.append(endpoint)
        if endpoint == 'responses/input_tokens': return {'input_tokens': 100}
        disclosed = json.loads(payload['input'])
        self.assertNotIn(str(self.checkout), disclosed['approvedPlan'])
        self.assertNotIn(str(self.installed), disclosed['approvedPlan'])
        self.assertEqual(disclosed['requirement']['criteria'][0]['text'], Path(self.wc['requirement']['path']).read_text())
        answer = copy.deepcopy(self.answer)
        body = copy.deepcopy(self.review_body)
        if disclosed.get('citationProtocol') == source_review.CITATION_PROTOCOL:
            # This synthetic provider emits the requested new wire format. Keep
            # retained legacy fixture bodies unchanged, including their ranges.
            def count_references(value):
                if isinstance(value, list):
                    for item in value: count_references(item)
                elif isinstance(value, dict):
                    if set(value) == {'path', 'startLine', 'endLine'}:
                        value['lineCount'] = value.pop('endLine') - value['startLine'] + 1
                    else:
                        for item in value.values(): count_references(item)
            count_references(body)
        answer['output'][0]['content'][0]['text'] = json.dumps(body)
        return answer

    def phase(self, authorization=None, transport=None):
        return phase.run(self.ppath, self.psha, self.ps, authorization, transport=transport)

    def execute_fake(self, root, state, intent, reader):
        self.assertTrue((self.ps / 'phase-grant.json').exists())
        self.assertEqual(json.loads((self.ps / 'container-dispatch.json').read_bytes()), intent)
        self.container_calls.append(intent)
        sources = {n: {'sha256': r['sha256'], 'mode': int(r['mode'],8)&0o777, 'bytes':r['bytes']}
                   for n,r in intent['sources'].items()}
        body = {'format': 'aim-flow-project-container-report', 'schemaVersion': 1,
            'intentSha256': container.local.digest(intent), 'status': 'passed', 'qualificationPassed': True,
            'ready': False, 'acceptance': 'not-established', 'deployment': 'not-authorized',
            'sourcesBefore': sources, 'sourcesAfter': sources, 'sourcesRestored': True,
            'outputs': {'project/web/bundle.zip': {'type':'file','sha256':'a'*64,'bytes':42,'mode':0o644}},
            'runtime': {'actualNodeVersion': 'v24.22.2', 'actualNpmVersion': '11.19.1',
                'nodePath': container.NODE, 'npmPath': container.NPM, 'helperSha256': intent['helperSha256']},
            'stages': [{'index': i, 'exitCode': 0, 'termination': None, 'projectArgv': s['argv'],
                       'root': s['root'], 'sourcesAfter': sources} for i,s in enumerate(intent['job']['stages'])]}
        terminal = {'state': {'ExitCode': 0, 'OOMKilled': False}}
        api.durable(state, 'container.stdout', api.encoded(body))
        api.durable(state, 'container-terminal.json', api.encoded(terminal))
        return {'status': 'passed', 'intentSha256': container.local.digest(intent), 'report': body,
            'terminal': terminal, 'deadlineExceeded': False, 'logs': {'exitCode': 0, 'termination': None},
            'diagnostic': None, 'controlFiles': {n:self.pin(state/n)['sha256'] for n in ('container.stdout','container-terminal.json')},
            'job': intent['job']['id'], 'containerCleanup': 'synthetic-confirmed-absent'}

    def grant_phase(self):
        return self.phase(self.phase()['intentSha256'], self.review_post)

    def select_new_controller(self):
        archive = self.case / 'new-control.tar.gz'
        release = phase.control_package.build(Path(__file__).resolve().parents[1], archive,
                                               '0.1.0-continuation-test.2')
        current = self.case / 'new-controller'
        phase.control_package.install(archive, release['archiveSha256'], current)
        for module in (phase, source_review):
            patcher = patch.object(module, 'ROOT', current)
            patcher.start(); self.addCleanup(patcher.stop)
        self.pc['manifestSha256'] = release['manifestSha256']
        self.pc['producerController'] = {'path': str(self.installed),
                                        'manifestSha256': self.wc['manifestSha256']}
        self.save_phase_config()
        return current

    def save_phase_config(self):
        self.ppath.write_bytes(api.encoded(self.pc)); self.psha = self.pin(self.ppath)['sha256']

    def test_explicit_producer_continuation_preserves_paid_work_and_old_grant(self):
        produced = self.grant()
        originals = {name: (self.ws / name).read_bytes() for name in
                     ('workflow.json', 'grant.json', 'candidate-receipt.json')}
        old_preview = self.phase()
        self.select_new_controller()
        preview = self.phase()
        self.assertEqual(preview['intent']['producerController'], self.pc['producerController'])
        self.assertEqual(preview['intent']['candidateReceipt'], produced['candidateReceipt'])
        self.assertEqual(preview['intent']['requirement'], self.wc['requirement'])
        self.assertEqual(preview['intent']['historicalBudget'], self.wc['historicalBudget'])
        self.assertNotEqual(preview['intentSha256'], old_preview['intentSha256'])
        self.assertEqual(len(self.wcalls), 4)
        with self.assertRaises(Refused):
            self.phase(old_preview['intentSha256'], self.review_post)
        self.assertFalse(self.ps.exists())
        with patch.object(container, 'execute', side_effect=self.execute_fake), \
                patch.object(container, 'execution', wraps=container.execution) as dispatch:
            result = self.grant_phase()
            self.assertEqual(result['status'], 'local-validation-passed', result)
            self.assertEqual(phase.verify(self.ppath, self.psha, self.ps), result)
        for call in dispatch.call_args_list:
            self.assertEqual(str(call.args[1].instance_controller), self.pc['producerController']['path'])
            self.assertEqual(call.args[1].instance_controller_manifest_sha256, self.pc['producerController']['manifestSha256'])
        self.assertEqual(len(self.wcalls), 6)  # Only the newly granted independent review.
        self.assertEqual(originals, {name: (self.ws / name).read_bytes() for name in originals})

    def test_producer_deadline_is_configurable_explicit_and_grant_bound(self):
        self.grant(); self.select_new_controller()
        first = self.phase()
        self.assertIsNone(first['intent']['producerVerificationTimeoutSeconds'])
        self.pc['producerVerificationTimeoutSeconds'] = 900; self.save_phase_config()
        with patch.object(phase.validation, 'verification_process',
                          wraps=phase.validation.verification_process) as verifier:
            second = self.phase()
        self.assertEqual(verifier.call_args.kwargs['timeout_seconds'], 900)
        self.assertEqual(second['intent']['producerVerificationTimeoutSeconds'], 900)
        self.assertNotEqual(first['intentSha256'], second['intentSha256'])
        self.pc['producerVerificationTimeoutSeconds'] = 7200; self.save_phase_config()
        third = self.phase()
        self.assertEqual(third['intent']['producerVerificationTimeoutSeconds'], 7200)
        self.assertNotEqual(second['intentSha256'], third['intentSha256'])
        for timeout in (0, -1, True, 1.5):
            self.pc['producerVerificationTimeoutSeconds'] = timeout; self.save_phase_config()
            with self.assertRaisesRegex(Refused, 'deadline'):
                self.phase()

    def test_other_controller_requires_explicit_pin_and_rejects_tampering(self):
        self.grant(); self.select_new_controller()
        pin = self.pc.pop('producerController'); self.save_phase_config()
        with self.assertRaisesRegex(Refused, 'explicit producerController'):
            self.phase()
        self.pc['producerController'] = {**pin, 'manifestSha256': self.pc['manifestSha256']}
        self.save_phase_config()
        with self.assertRaises(Refused):
            self.phase()
        self.pc['producerController'] = pin; self.save_phase_config()
        receipt = self.ws / 'candidate-receipt.json'
        value = json.loads(receipt.read_bytes()); value['candidate']['commit'] = 'a' * 40
        receipt.write_bytes(api.encoded(value))
        with self.assertRaisesRegex(Refused, 'producer refused'):
            self.phase()
        self.assertFalse(self.ps.exists()); self.assertEqual(len(self.wcalls), 4)

    def test_refinement_grant_and_retained_review_are_distinct(self):
        self.grant()
        legacy = self.phase()
        self.pc['refineExecutionPlan'] = True
        self.ppath.write_bytes(api.encoded(self.pc)); self.psha = self.pin(self.ppath)['sha256']
        self.assertNotEqual(self.phase()['intentSha256'], legacy['intentSha256'])
        self.review_body['executionRefinement'] = [
            {'stepId': key, 'kind': 'finite-local', 'evidenceSteps': [],
             'executionDependsOn': deps, 'analysis': 'Candidate finite local stage inspected',
             'sources': [{'path':'candidate/web/package.json','startLine':1,'endLine':1}]}
            for key, deps in [('install',[]),('test',['install'])]]
        with patch.object(container, 'execute', side_effect=self.execute_fake):
            result = self.grant_phase()
            self.assertEqual(result['status'], 'local-validation-passed', result)
            self.assertEqual(self.phase(), result)
        self.assertEqual(result['acceptanceObligations'], [])
        self.assertEqual(len(result['executionRefinement']), 2)
        request = json.loads((self.ps/'review-attempt/request.json').read_bytes())
        self.assertEqual(request['text']['format']['name'], 'candidate_workspace_refinement_v2')

    def test_reviewed_duplicate_test_executes_once_and_verifies_without_replay(self):
        original_assessment = self.assessment
        def repeated_assessment(packet):
            value = original_assessment(packet)
            repeated = copy.deepcopy(value['steps'][-1])
            repeated.update(id='repeat', dependsOn=['test'])
            value['steps'].append(repeated)
            return value
        with patch.object(self, 'assessment', side_effect=repeated_assessment):
            self.grant()
        self.pc['refineExecutionPlan'] = True
        self.save_phase_config()
        self.review_body['executionRefinement'] = [
            {'stepId': key, 'kind': kind, 'evidenceSteps': evidence,
             'executionDependsOn': deps, 'analysis': 'Same source-bound adjacent test supports both obligations.',
             'sources': [{'path': 'candidate/web/package.json', 'startLine': 1, 'endLine': 1}]}
            for key, kind, evidence, deps in [
                ('install', 'finite-local', [], []),
                ('test', 'finite-local', [], ['install']),
                ('repeat', 'reuse-local-test', ['test'], [])]]
        with patch.object(container, 'execute', side_effect=self.execute_fake):
            result = self.grant_phase()
            self.assertEqual(result['status'], 'local-validation-passed', result)
            self.assertEqual(len(self.container_calls), 1)
            stages = self.container_calls[0]['job']['stages']
            self.assertEqual([stage['argv'] for stage in stages], [['npm', 'ci'], ['npm', 'run', 'test']])
            self.assertEqual(result['reusedLocalTests'][0]['producerStepId'], 'test')
            self.assertEqual([step['id'] for step in result['acceptanceObligations']], ['repeat'])
            calls = list(self.wcalls)
            self.assertEqual(self.phase(), result)
            self.assertEqual(self.wcalls, calls)
            self.assertEqual(len(self.container_calls), 1)

    def test_refinement_blocked_step_stops_before_container(self):
        self.grant()
        self.pc['refineExecutionPlan'] = True
        self.ppath.write_bytes(api.encoded(self.pc)); self.psha = self.pin(self.ppath)['sha256']
        self.review_body['executionRefinement'] = [
            {'stepId': key, 'kind': 'blocked', 'evidenceSteps': [], 'executionDependsOn': [],
             'analysis': 'Finite termination is not established',
             'sources': [{'path':'candidate/web/package.json','startLine':1,'endLine':1}]}
            for key in ['install','test']]
        with patch.object(container, 'execution', side_effect=AssertionError('must not execute')):
            result = self.grant_phase()
        self.assertEqual(result['status'], 'execution-plan-refinement-required')
        self.assertFalse((self.ps/'container-dispatch.json').exists())


    def test_capture_grant_is_distinct_and_empty_selection_blocks(self):
        self.grant()
        legacy = self.phase()
        self.assertNotIn('artifactCollection', legacy['intent'])
        self.pc['captureArtifacts'] = True
        self.ppath.write_bytes(api.encoded(self.pc)); self.psha = self.pin(self.ppath)['sha256']
        preview = self.phase()
        self.assertNotEqual(preview['intentSha256'], legacy['intentSha256'])
        self.assertEqual(preview['intent']['artifactCollection']['maxFiles'], 8)
        self.assertFalse(preview['intent']['artifactCollection']['artifactTransmissionAuthorized'])
        self.review_body['artifactSelection'] = []
        with patch.object(container, 'execute', side_effect=AssertionError('must not execute')):
            result = self.grant_phase()
            self.assertEqual(result['status'], 'artifact-selection-required')
            self.assertEqual(self.phase(), result)
        request = json.loads((self.ps/'review-attempt/request.json').read_bytes())
        self.assertEqual(request['text']['format']['name'], 'candidate_workspace_review_v2')
        self.assertTrue(json.loads(request['input'])['workspaceContext']['captureArtifacts'])

    def test_capture_spec_is_generated_from_review_not_template(self):
        self.grant()
        self.pc['captureArtifacts'] = True
        self.ppath.write_bytes(api.encoded(self.pc)); self.psha = self.pin(self.ppath)['sha256']
        self.review_body['artifactSelection'] = [{'path': 'web/evidence/state.json',
            'mediaType': 'application/json', 'reason': 'Generated test state',
            'sources': [{'path': 'candidate/web/generate.js', 'startLine': 2, 'endLine': 2}]}]
        calls = []
        def preview_only(root, args):
            calls.append(args)
            spec = json.loads(api.pinned({'path': str(args.execution_spec), 'sha256': args.execution_spec_sha256}))
            self.assertEqual(spec['artifactCapture'], {'schemaVersion': 1,
                'files': [{'path': 'web/evidence/state.json', 'mediaType': 'application/json'}],
                'maxFiles': 8, 'maxFileBytes': 362712, 'maxTotalBytes': 362712})
            self.assertEqual(spec['outputBytes'], 1_000_000)
            raise Refused('Synthetic stop before backend preview')
        with patch.object(container, 'execution', side_effect=preview_only):
            with self.assertRaisesRegex(Refused, 'Synthetic stop'):
                self.grant_phase()
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.ps/'container-dispatch.json').exists())
        selected = json.loads((self.ps/'review-attempt/review.json').read_bytes())['artifactSelection'][0]
        self.assertIn('JSON.stringify', selected['sources'][0]['quote'])

    def test_real_derivation_review_and_container_receipt_roundtrip(self):
        self.grant()
        with patch.object(container, 'execute', side_effect=self.execute_fake):
            result = self.grant_phase()
            self.assertEqual(result['status'], 'local-validation-passed', result)
            self.assertEqual(self.phase(), result)
        self.assertEqual(len(self.container_calls), 1)
        self.assertEqual([s['argv'] for s in self.container_calls[0]['job']['stages']], [['npm','ci'],['npm','run','test']])
        self.assertNotIn('stale-source', self.container_calls[0]['sources'])
        self.assertIn('pyproject.toml', self.container_calls[0]['sources'])
        self.assertNotIn('web/bundle.zip', self.container_calls[0]['sources'])
        self.assertTrue((self.ws/'candidate/web/bundle.zip').exists())
        self.assertFalse(result['productAccepted']); self.assertTrue(result['unresolvedReviewEvidence'])
        ledger = json.loads((Path(self.wc['campaignDirectory'])/'budget.json').read_bytes())
        self.assertEqual(len(ledger['requests']), 3)
        self.assertEqual(json.loads((self.ws/'grant.json').read_bytes())['intent']['maximumGenerationAttempts'], 2)

    def test_successful_exit_without_declared_archive_is_incomplete(self):
        self.grant()
        real = self.execute_fake
        def no_output(root, state, intent, reader):
            result = real(root, state, intent, reader)
            result['report']['outputs'] = {}
            (state/'container.stdout').write_bytes(api.encoded(result['report']))
            result['controlFiles']['container.stdout'] = self.pin(state/'container.stdout')['sha256']
            return result
        with patch.object(container, 'execute', side_effect=no_output):
            result = self.grant_phase()
        self.assertEqual(result['status'], 'local-validation-incomplete-generated-output')
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['workspaceOutputs']['missingOrInvalidOutputs'], ['web/bundle.zip'])

    def test_original_grant_never_authorizes_phase(self):
        self.grant(); preview = self.phase()
        self.assertEqual(preview['status'], 'awaiting-authorization'); self.assertFalse(self.ps.exists())
        old = json.loads((self.ws/'grant.json').read_bytes())['intentSha256']
        with self.assertRaises(Refused): self.phase(old, self.review_post)
        self.assertFalse(self.ps.exists()); self.assertEqual(len(self.wcalls), 4)

    def test_low_severity_source_defect_stops_tests(self):
        self.grant()
        self.review_body['findings'] = [{'id': 'bug', 'description': 'Wrong behavior', 'severity': 'low',
            'requirementIds': ['criterion-001'], 'sources': [{'path': 'candidate/feature.js', 'startLine': 1, 'endLine': 1}]}]
        with patch.object(container, 'execute', side_effect=AssertionError('must not run')):
            self.assertEqual(self.grant_phase()['status'], 'source-defects')

    def test_unknown_container_never_retried(self):
        self.grant()
        with patch.object(container, 'execute', side_effect=OSError('Lost container terminal outcome')) as call:
            with self.assertRaises(OSError): self.grant_phase()
            self.assertEqual(self.phase()['status'], 'blocked')
            self.assertEqual(call.call_count, 1)

    def test_missing_consumed_config_not_repaired(self):
        self.grant()
        with patch.object(container, 'execute', side_effect=self.execute_fake): self.grant_phase()
        target = self.ps/'container-spec.json'; target.unlink()
        with self.assertRaises((OSError, Refused)): self.phase()
        self.assertFalse(target.exists())

    def test_consumed_review_dispatch_without_attempt_never_retries(self):
        self.grant()
        with patch.object(phase.review, 'run', side_effect=OSError('Interrupted before receipt')) as call:
            with self.assertRaises(OSError): self.grant_phase()
            result = self.phase(transport=self.review_post)
            self.assertEqual(result['status'], 'blocked')
            self.assertFalse(result['replayPermitted'])
            self.assertEqual(call.call_count, 1)

    def test_unclassified_archive_requires_policy_not_silent_exclusion(self):
        self.grant(); self.review_body['workspacePolicy']['generatedOutputs'] = []
        with patch.object(container, 'execute', side_effect=AssertionError('must not execute')):
            result = self.grant_phase()
        self.assertEqual(result['status'], 'source-input-output-policy-required')
        self.assertTrue(result['review']['workspacePolicy']['unknowns'])

    def test_missing_retained_phase_lock_is_not_recreated(self):
        self.grant(); self.phase(self.phase()['intentSha256'])
        lock = self.ps/'phase.lock'; lock.unlink()
        with self.assertRaises((OSError, Refused)): self.phase(transport=self.review_post)
        self.assertFalse(lock.exists()); self.assertEqual(len(self.wcalls), 4)

    def test_resource_mismatch_stops_before_paid_review(self):
        self.catalog['resources'][0]['capabilities']['nodeVersion'] = '18.0.0'
        Path(self.wc['resourceCatalog']['path']).write_bytes(api.encoded(self.catalog))
        self.wc['resourceCatalog'] = self.pin(Path(self.wc['resourceCatalog']['path']))
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
        self.pc['workflowConfig'] = self.pin(self.wpath); self.ppath.write_bytes(api.encoded(self.pc)); self.psha=self.pin(self.ppath)['sha256']
        self.grant()
        result = self.grant_phase()
        self.assertEqual(result['status'], 'resource-or-adapter-required'); self.assertEqual(len(self.wcalls), 4)


for name in dir(fixtures.RequirementWorkflowTests):
    if name.startswith('test_') and name not in ContinuationTests.__dict__:
        setattr(ContinuationTests, name, None)

if __name__ == '__main__': unittest.main()
