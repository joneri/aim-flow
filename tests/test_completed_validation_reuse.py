"""Small read-only predecessor boundary fixtures; no provider/project dispatch."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_execution_review as review
import flow_resume_candidate as resume
import flow_run as local
import flow_api_delivery as api
from aim_ci.owned_paths import Refused

class CompletedValidationReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.old = self.root/'old'; (self.old/'lib').mkdir(parents=True)
        # Minimal historical transport namespace for the real cache bootstrap.
        package=self.old/'lib/aim_ci';package.mkdir()
        (package/'__init__.py').write_text('')
        (package/'project_validation.py').write_text('')
        self.state = self.root/'retained'; self.state.mkdir()
        self.initial = {'status':'candidate-prepared', 'candidate':{'commit':'a','tree':'b'},'candidateReceipt':{'path':'receipt','sha256':'c'}}
        self.outputs = {'complete':True, 'candidate':self.initial['candidate']}
        self.result = {'status':'local-validation-passed','validation':{'status':'passed'},'workspaceOutputs':self.outputs}
        py = Path(sys.executable).resolve()
        self.config = {'manifestSha256':'a'*64,'python':self.pin(py),'producerVerificationTimeoutSeconds':None}
        self.cp = self.root/'config.json'; self.save(self.cp,self.config)
        intent = {'config':self.pin(self.cp),'state':str(self.state),'manifestSha256':'a'*64,
            'candidate':self.initial['candidate'],'candidateReceipt':self.initial['candidateReceipt']}
        self.save(self.state/'phase-grant.json',{'intent':intent,'intentSha256':api.digest(api.encoded(intent))})
        self.save(self.state/'workspace-output-evidence.json',self.outputs)
        self.spec={'controller':{'path':str(self.old),'manifestSha256':'a'*64},'validationConfig':self.pin(self.cp),
            'validationState':str(self.state),'phaseGrant':self.pin(self.state/'phase-grant.json'),
            'workspaceEvidence':self.pin(self.state/'workspace-output-evidence.json')}
        self.module=self.old/'lib/flow_workflow_validation.py'
        self.write_module()
        self.guard=patch.object(review.upstream.validation,'prior_controller',return_value=b'fixed inventory')
        self.guard.start();self.addCleanup(self.guard.stop)
    def save(self,p,v):p.write_bytes(api.encoded(v))
    def pin(self,p):return {'path':str(p),'sha256':api.digest(p.read_bytes())}
    def write_module(self):
        self.module.write_text('def verified_producer(*a,**k):return '+repr(self.initial)+'\n'
            +'def verify(*a):\n verified_producer()\n return '+repr(self.result)+'\n')
    def test_fixed_real_subprocess_returns_exact_pass_without_retained_writes(self):
        before={p:p.read_bytes() for p in self.state.iterdir()}
        vc,initial,result=review.verified_completed_validation(self.spec)
        self.assertEqual((vc,initial,result),(self.config,self.initial,self.result))
        self.assertEqual(before,{p:p.read_bytes() for p in self.state.iterdir()})
    def test_false_success_and_input_drift_refused(self):
        self.result['validation']['status']='failed';self.write_module()
        with self.assertRaisesRegex(Refused,'passing source-bound'):review.verified_completed_validation(self.spec)
        self.save(self.state/'workspace-output-evidence.json',{'complete':False})
        with self.assertRaises(Exception):review.verified_completed_validation(self.spec)
    def test_new_evidence_config_points_to_retained_phase(self):
        intent={'reviewExecuted':{'maxOutputTokens':1000,'maxOperationNanoUsd':1000},
            'controllerManifestSha256':'b'*64,'completedValidationReuse':self.spec}
        c=local.execution_config(intent,self.root/'new')
        self.assertEqual(c['validationConfig'],self.spec['validationConfig'])
        self.assertEqual(c['validationState'],str(self.state))
        self.assertEqual(c['manifestSha256'],'b'*64)
        self.assertEqual(c['completedValidationReuse'],self.spec)
    def test_excludes_source_only_reuse_before_any_effects(self):
        # The exact-key schema is accepted, then incompatible recovery modes stop.
        c={k:None for k in ('workflowConfig','workflowState','producerController','producerVerificationTimeoutSeconds','python',
             'reviewMaxOutputTokens','reviewMaxOperationNanoUsd','executionMaxOutputTokens','executionMaxOperationNanoUsd')}
        c.update(format=resume.FORMAT,schemaVersion=1,manifestSha256='b'*64,
            completedValidationReuse=self.spec,sourceReviewReuse={})
        p=self.root/'resume.json';self.save(p,c)
        with self.assertRaisesRegex(Refused,'excludes source-only'):resume.prepare(p,self.pin(p)['sha256'],self.root/'new')

    def test_reuse_grant_authorizes_one_model_step_and_no_project_attempts(self):
        intent={'maximumGenerationAttempts':2,'maximumContainerAttempts':1,'maximumHostAttempts':1,
            'projectCommandsAuthorized':True,'publicationAuthorized':False,'deploymentAuthorized':False}
        resume.completed_validation_grant(intent,self.spec)
        self.assertEqual(intent['maximumGenerationAttempts'],1)
        self.assertEqual((intent['maximumContainerAttempts'],intent['maximumHostAttempts']),(0,0))
        self.assertFalse(intent['projectCommandsAuthorized'])
        self.assertFalse(intent['publicationAuthorized']);self.assertFalse(intent['deploymentAuthorized'])

    def test_unknown_disposition_is_execution_only_and_preserves_one_step(self):
        intent={'reviewExecuted':{'maxOutputTokens':1000,'maxOperationNanoUsd':1000},
            'controllerManifestSha256':'b'*64,'unknownDispositionPolicy':'materiality-and-scope-v1',
            'evidenceEvaluationPolicy':'assertion-grounded-v1'}
        resume.completed_validation_grant(intent,self.spec)
        config=local.execution_config(intent,self.root/'new')
        self.assertEqual(config['unknownDispositionPolicy'],'materiality-and-scope-v1')
        self.assertEqual(config['evidenceEvaluationPolicy'],'assertion-grounded-v1')
        self.assertEqual(intent['maximumGenerationAttempts'],1)
        self.assertEqual(intent['maximumHostAttempts'],0)
        self.assertNotIn('unknownDispositionPolicy',json.loads(self.cp.read_bytes()))
        self.assertEqual(config['validationConfig'],self.spec['validationConfig'])
