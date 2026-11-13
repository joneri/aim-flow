"""One outer grant with real children/receipts and synthetic external boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import flow_run as full
import flow_api_delivery as api
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_workflow_validation as fixtures


class FullRunTests(fixtures.ContinuationTests):
    def setUp(self):
        super().setUp()
        p=patch.object(full,'ROOT',self.installed);p.start();self.addCleanup(p.stop)
        self.fs=self.case/'full-run';self.ws=self.fs/'initial';self.ps=self.fs/'validation'

    def post_all(self,endpoint,payload):
        if payload.get('text',{}).get('format',{}).get('name')=='candidate_workspace_review_v1':
            return self.review_post(endpoint,payload)
        return self.workflow_post(endpoint,payload)

    def full(self,authorization=None,**options):
        return full.run(self.wpath,self.wsha,self.fs,authorization,**options)

    def test_one_grant_generates_all_phase_configs_and_verifies_without_replay(self):
        preview=self.full()
        self.assertEqual(preview['intent']['maximumGenerationAttempts'],3)
        self.assertEqual(preview['intent']['maximumContainerAttempts'],1)
        self.assertFalse(self.fs.exists())
        with patch.object(container,'execute',side_effect=self.execute_fake):
            result=self.full(preview['intentSha256'],transport=self.post_all)
            self.assertEqual(result['status'],'local-validation-passed',result)
            files={str(p):p.read_bytes() for p in self.fs.rglob('*') if p.is_file() and '.git' not in p.parts}
            self.assertEqual(self.full(verify_only=True),result)
            self.assertEqual(self.full(),result)
            self.assertEqual(files,{str(p):p.read_bytes() for p in self.fs.rglob('*') if p.is_file() and '.git' not in p.parts})
        self.assertEqual(len(self.container_calls),1)
        self.assertEqual(self.wcalls,['responses/input_tokens','responses']*3)
        self.assertFalse(result['productAccepted'])
        self.assertEqual(json.loads((self.fs/'validation-config.json').read_bytes())['workflowState'],str(self.ws))
        self.assertEqual(json.loads((self.ws/'grant.json').read_bytes())['intent']['maximumGenerationAttempts'],2)
        self.assertEqual(json.loads((self.ps/'phase-grant.json').read_bytes())['intent']['maximumGenerationAttempts'],1)

    def test_credential_pause_resumes_under_same_outer_grant(self):
        preview=self.full(); result=self.full(preview['intentSha256'])
        self.assertEqual(result['status'],'awaiting-provider-credential')
        self.assertFalse(self.wcalls)
        with patch.object(container,'execute',side_effect=self.execute_fake):
            self.assertEqual(self.full(transport=self.post_all)['status'],'local-validation-passed')

    def test_old_grant_and_changed_review_limits_never_expand_authority(self):
        preview=self.full()
        old=api.digest(api.encoded(preview['intent']['initialIntent']))
        with self.assertRaises(Refused):self.full(old,transport=self.post_all)
        self.assertFalse(self.fs.exists())
        self.full(preview['intentSha256'])
        with self.assertRaises(Refused):self.full(review_tokens=4096,transport=self.post_all)
        self.assertFalse(self.wcalls)

    def test_unknown_initial_dispatch_is_not_retried(self):
        preview=self.full()
        with patch.object(full.work,'run',side_effect=OSError('Lost initial child')) as call:
            with self.assertRaises(OSError):self.full(preview['intentSha256'],transport=self.post_all)
            self.assertEqual(self.full(transport=self.post_all)['status'],'blocked')
            self.assertEqual(call.call_count,1)

    def test_unknown_validation_dispatch_is_not_retried(self):
        preview=self.full()
        with patch.object(full.validation,'run',side_effect=OSError('Lost validation child')) as call:
            with self.assertRaises(OSError):self.full(preview['intentSha256'],transport=self.post_all)
            result=self.full(transport=self.post_all)
            self.assertEqual(result['status'],'blocked')
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.wcalls,['responses/input_tokens','responses']*2)

    def test_missing_retained_outer_lock_is_not_recreated(self):
        preview=self.full();self.full(preview['intentSha256'])
        lock=self.fs/'run.lock';lock.unlink()
        with self.assertRaises((OSError,Refused)):self.full(transport=self.post_all)
        self.assertFalse(lock.exists());self.assertFalse(self.wcalls)

    def test_verify_incomplete_run_never_dispatches_or_repairs(self):
        preview=self.full();self.full(preview['intentSha256'])
        files={str(p):p.read_bytes() for p in self.fs.rglob('*') if p.is_file()}
        with patch.object(full.work,'run',side_effect=AssertionError('verify dispatched child')):
            with self.assertRaises((Refused,OSError)):self.full(verify_only=True)
        self.assertEqual(files,{str(p):p.read_bytes() for p in self.fs.rglob('*') if p.is_file()})
        self.assertFalse(self.wcalls)

    def test_missing_terminal_child_input_is_not_repaired(self):
        with patch.object(container,'execute',side_effect=self.execute_fake):
            result=self.full(self.full()['intentSha256'],transport=self.post_all)
        target=self.ps/'workspace-output-evidence.json';target.unlink()
        with self.assertRaises((Refused,OSError)):self.full(verify_only=True)
        self.assertFalse(target.exists());self.assertEqual(len(self.container_calls),1)


for name in dir(fixtures.ContinuationTests):
    if name.startswith('test_') and name not in FullRunTests.__dict__:setattr(FullRunTests,name,None)

if __name__=='__main__':unittest.main()
