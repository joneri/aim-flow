"""Optional four-generation outer scope. Real child verification, fake external effects."""
import base64
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_run as full
import flow_api_delivery as api
import flow_execution_review as executed
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_flow_run as fixtures

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=')


class ExecutedRunTests(fixtures.FullRunTests):
    def test_proportional_policy_is_bound_in_original_outer_and_validation_grants(self):
        policy = full.validation.review.base.FINDING_DISPOSITION_POLICY
        intent, phase = full.prepare(self.wpath, self.wsha, self.fs,
                                     review_executed=True, finding_policy=policy)
        self.assertEqual(intent['findingDispositionPolicy'], policy)
        self.assertEqual(phase['findingDispositionPolicy'], policy)
        with self.assertRaises(Refused):
            full.prepare(self.wpath, self.wsha, self.fs, finding_policy='unsupported')

    def setUp(self):
        super().setUp()
        p = patch.object(executed, 'ROOT', self.installed); p.start(); self.addCleanup(p.stop)
        # Keep fixture metadata paths out of disclosed evidence; no redaction.
        self.wc['reviewSources'].remove('scripts/check.py')
        (self.checkout / 'web/generate.js').write_text('writeFileSync("bundle.zip", makeZip());\n'
            'writeFileSync("results/behavior.json", JSON.stringify(observations));\n'
            'await page.screenshot({path: "results/view.png"});\n')
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Synthetic observable evidence generator')
        self.commit = self.git_run('rev-parse', 'HEAD'); self.wc['source']['commit'] = self.commit
        self.spec['outputBytes'] = 32000000
        self.spec_path.write_bytes(api.encoded(self.spec))
        for row in self.catalog['resources']:
            if 'executionSpec' in row: row['executionSpec'].update(self.pin(self.spec_path))
        cp = Path(self.wc['resourceCatalog']['path']); cp.write_bytes(api.encoded(self.catalog))
        self.wc['resourceCatalog'] = self.pin(cp)
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
        self.review_body['artifactSelection'] = [
            {'path':'web/results/behavior.json', 'mediaType':'application/json', 'reason':'Exact behavior observations written by selected test source',
             'sources':[{'path':'candidate/web/generate.js','startLine':2,'endLine':2}]},
            {'path':'web/results/view.png','mediaType':'image/png','reason':'Exact rendered view written by selected test source',
             'sources':[{'path':'candidate/web/generate.js','startLine':3,'endLine':3}]}]
        self.acceptance_packets = []
        self.acceptance_defect = False

    def assessment(self, packet):
        value = super().assessment(packet)
        if getattr(self, 'delivery_pending', False):
            delivery = copy.deepcopy(value['steps'][-1])
            delivery.update(id='delivery', area='deployment', purpose='Separately approved deployment',
                command='./release.sh', workingDirectory='.', dependsOn=['test'],
                sources=[self.citation(packet, 'README.md', 3, 3)])
            value['steps'].append(delivery)
        return value

    def full(self, authorization=None, **options):
        options.setdefault('review_executed', True)
        return full.run(self.wpath,self.wsha,self.fs,authorization,**options)

    def post_all(self, endpoint, payload):
        name = payload.get('text',{}).get('format',{}).get('name')
        if name in ('candidate_workspace_review_v1', 'candidate_workspace_review_v2'):
            return self.review_post(endpoint, payload)
        if name != 'executed_acceptance_review_v1':
            return self.workflow_post(endpoint, payload)
        self.wcalls.append(endpoint)
        if endpoint == 'responses/input_tokens': return {'input_tokens':200}
        packet = json.loads(payload['input'][0]['content'][0]['text'])
        self.acceptance_packets.append(packet)
        self.assertTrue(any(x['type']=='input_image' for x in payload['input'][0]['content']))
        citation = {'path':'candidate/feature.js','startLine':1,'endLine':1}
        artifacts = ['artifact:web/results/behavior.json','artifact:web/results/view.png']
        body = {'summary':'Synthetic executed evidence assessment, no real browser run.',
            'coverage':[{'requirementId':c['id'], 'status':'defect' if self.acceptance_defect else 'satisfied',
                'analysis':'Synthetic fixture observations', 'evidenceKinds':['source','behavior','visual'],
                'sources':[copy.deepcopy(citation)], 'evidenceIds':artifacts} for c in packet['requirement']['criteria']],
            'priorDispositions':[{'itemId':item['id'],'status':'resolved','analysis':'Synthetic exact source and executed observations resolve this fixture item',
                'sources':[copy.deepcopy(citation)],'evidenceIds':artifacts} for item in packet['priorOpenItems']],
            'findings':['Synthetic visual defect'] if self.acceptance_defect else [], 'unknowns':[]}
        for item, disposition in zip(packet['priorOpenItems'], body['priorDispositions']):
            if item['origin'] == 'unsupported-step' and getattr(self, 'delivery_pending', False):
                disposition.update(status='pending-delivery', analysis='Exact typed deployment obligation remains unexecuted',
                    evidenceIds=['delivery-step:delivery'],
                    sources=[{'path':'candidate/README.md','startLine':3,'endLine':3}])
        answer=copy.deepcopy(self.answer);answer['output'][0]['content'][0]['text']=json.dumps(body)
        return answer

    def execute_with_artifacts(self, root, state, intent, reader):
        result = super().execute_fake(root,state,intent,reader)
        rows=[]
        for selection in intent['artifactCapture']['files']:
            raw = PNG if selection['mediaType']=='image/png' else b'{"events":["paused","resumed"],"viewport":{"width":800,"height":600}}'
            row={**selection,'bytes':len(raw),'sha256':api.digest(raw),'base64':base64.b64encode(raw).decode()}
            rows.append(row)
            result['report']['outputs']['project/'+row['path']]={'type':'file','mode':0o600,'bytes':len(raw),'sha256':row['sha256']}
        result['report']['artifacts']=rows
        (state/'container.stdout').write_bytes(api.encoded(result['report']))
        result['controlFiles']['container.stdout']=self.pin(state/'container.stdout')['sha256']
        return result

    def test_one_new_grant_selects_captures_and_reviews_fourth_call(self):
        preview=self.full()
        self.assertEqual(preview['intent']['schemaVersion'],2)
        self.assertEqual(preview['intent']['maximumGenerationAttempts'],4)
        self.assertIn('PNG',preview['intent']['reviewExecuted']['disclosure'])
        # The declared 32 MB report reserves JSON/base64 headroom; the removed
        # fixed eight-MiB ceiling must not reappear in the outer grant.
        self.assertEqual(preview['intent']['reviewExecuted']['artifactBounds'],
            {'maxFiles':8,'maxFileBytes':4194304,'maxTotalBytes':11987712})
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts):
            result=self.full(preview['intentSha256'],transport=self.post_all)
            self.assertEqual(result['status'],'local-feature-evidence-reviewed',result)
            self.assertTrue(result['localFeatureAccepted']);self.assertFalse(result['deliveryReady'])
            self.assertFalse(result['publicationAuthorized']);self.assertFalse(result['deploymentAuthorized'])
            before={str(p):p.read_bytes() for p in self.fs.rglob('*') if p.is_file() and '.git' not in p.parts}
            self.assertEqual(self.full(verify_only=True),result)
            self.assertEqual(self.full(),result)
            self.assertEqual(before,{str(p):p.read_bytes() for p in self.fs.rglob('*') if p.is_file() and '.git' not in p.parts})
        self.assertEqual(len(self.container_calls),1);self.assertEqual(len(self.acceptance_packets),1)
        self.assertEqual(self.wcalls,['responses/input_tokens','responses']*4)
        self.assertEqual(len(json.loads((Path(self.wc['campaignDirectory'])/'budget.json').read_bytes())['requests']),4)
        config=json.loads((self.fs/'validation-config.json').read_bytes())
        self.assertIs(config['captureArtifacts'],True)
        self.assertEqual(self.container_calls[0]['artifactCapture']['files'],
            [{k:x[k] for k in ('path','mediaType')} for x in self.review_body['artifactSelection']])

    def test_legacy_grant_never_authorizes_capture_or_fourth_call(self):
        old=self.full(review_executed=False)
        self.assertEqual(old['intent']['schemaVersion'],1)
        self.assertNotIn('reviewExecuted',old['intent'])
        with self.assertRaises(Refused):self.full(old['intentSha256'],transport=self.post_all)
        self.assertFalse(self.fs.exists());self.assertFalse(self.wcalls)
        self.full(old['intentSha256'],review_executed=False)
        with self.assertRaises(Refused):self.full(transport=self.post_all)
        self.assertFalse(self.wcalls)

    def test_execution_caps_require_mode_and_stay_pinned(self):
        with self.assertRaises(Refused):self.full(review_executed=False,execution_tokens=8192)
        preview=self.full(execution_tokens=4096,execution_limit=500000000)
        self.full(preview['intentSha256'],execution_tokens=4096,execution_limit=500000000)
        with self.assertRaises(Refused):self.full(execution_tokens=8192,execution_limit=500000000)
        self.assertFalse(self.wcalls)

    def test_unknown_fourth_generation_is_never_retried(self):
        def fail(endpoint,payload):
            if payload.get('text',{}).get('format',{}).get('name')=='executed_acceptance_review_v1' and endpoint=='responses':
                self.wcalls.append(endpoint);raise OSError('Lost review response')
            return self.post_all(endpoint,payload)
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts):
            result=self.full(self.full()['intentSha256'],transport=fail)
            self.assertEqual(result['status'],'blocked')
            again=self.full(transport=self.post_all)
            self.assertEqual(again['status'],'blocked');self.assertFalse(again['replayPermitted'])
        self.assertEqual(self.wcalls,['responses/input_tokens','responses']*4)
        self.assertEqual(len(self.container_calls),1)

    def test_source_selected_evidence_defect_blocks_local_acceptance(self):
        self.acceptance_defect=True
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts):
            result=self.full(self.full()['intentSha256'],transport=self.post_all)
        self.assertEqual(result['status'],'executed-evidence-review-blocked')
        self.assertFalse(result['localFeatureAccepted']);self.assertFalse(result['deliveryReady'])
        self.assertEqual(self.full(verify_only=True),result)

    def test_typed_delivery_obligations_remain_pending_after_local_acceptance(self):
        self.delivery_pending=True
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts):
            result=self.full(self.full()['intentSha256'],transport=self.post_all)
        self.assertEqual(result['status'],'local-feature-evidence-reviewed',result)
        self.assertTrue(result['localFeatureAccepted']);self.assertFalse(result['productAccepted'])
        self.assertFalse(result['deliveryReady']);self.assertTrue(result['pendingDeliveryObligations'])
        self.assertEqual(self.full(verify_only=True),result)

    def test_failed_local_validation_never_dispatches_fourth_generation(self):
        def failed(root,state,intent,reader):
            result=self.execute_with_artifacts(root,state,intent,reader)
            result['status']='failed';result['report']['status']='failed'
            result['report']['stages']=result['report']['stages'][:1]
            result['report']['stages'][0]['exitCode']=1
            (state/'container.stdout').write_bytes(api.encoded(result['report']))
            result['controlFiles']['container.stdout']=self.pin(state/'container.stdout')['sha256']
            return result
        with patch.object(container,'execute',side_effect=failed):
            result=self.full(self.full()['intentSha256'],transport=self.post_all)
        self.assertEqual(result['status'],'local-validation-failed')
        self.assertFalse(result['localFeatureAccepted']);self.assertFalse(result['deliveryReady'])
        self.assertEqual(result['executionReviewState'],'not-run')
        self.assertEqual(self.wcalls,['responses/input_tokens','responses']*3)
        self.assertEqual(self.full(verify_only=True),result)

    def test_interrupted_fourth_dispatch_without_attempt_never_replays(self):
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts), \
             patch.object(executed,'run',side_effect=OSError('Lost before attempt receipt')) as call:
            with self.assertRaises(OSError):self.full(self.full()['intentSha256'],transport=self.post_all)
            result=self.full(transport=self.post_all)
            self.assertEqual(result['status'],'blocked');self.assertFalse(result['replayPermitted'])
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.wcalls,['responses/input_tokens','responses']*3)

    def test_missing_consumed_acceptance_config_is_not_repaired(self):
        with patch.object(container,'execute',side_effect=self.execute_with_artifacts):
            self.full(self.full()['intentSha256'],transport=self.post_all)
        config=self.fs/'execution-review-config.json';config.unlink()
        with self.assertRaises((Refused,OSError)):self.full(verify_only=True)
        self.assertFalse(config.exists());self.assertEqual(len(self.acceptance_packets),1)


for name in dir(fixtures.FullRunTests):
    if name.startswith('test_') and name not in ExecutedRunTests.__dict__:setattr(ExecutedRunTests,name,None)

if __name__=='__main__':unittest.main()
