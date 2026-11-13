"""Pinned external accepted-run transport, with no delivery effects."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import flow_delivery_chain as chain
from aim_ci import project_validation
from aim_ci.owned_paths import Refused

class ExternalLocalRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.old=self.root/'controller';self.old.mkdir()
        self.config={'producerVerificationTimeoutSeconds':None};self.expected={'status':'local-feature-evidence-reviewed','localFeatureAccepted':True}
        self.spec={'controller':{'path':str(self.old),'manifestSha256':'a'*64},'state':str(self.root/'run')}
        for key,value in [('config',self.config),('grant',{}),('result',self.expected)]:
            p=self.root/(key+'.json');p.write_bytes(chain.api.encoded(value));self.spec[key]={'path':str(p),'sha256':chain.api.digest(p.read_bytes())}
        self.intent={'python':{'path':str(Path(sys.executable).resolve()),'sha256':'b'*64}}
        for obj,name in [(chain.publication,'executable_pin')]:
            p=patch.object(obj,name);p.start();self.addCleanup(p.stop)
    def invoke(self,process=None,mutate=None,inventory=None):
        def run(*a,**k):
            if mutate:mutate()
            return process or SimpleNamespace(returncode=0,stderr=b'',stdout=json.dumps(self.expected).encode())
        with patch.object(project_validation,'prior_controller',side_effect=inventory or [b'fixed',b'fixed']),patch.object(project_validation,'verification_process',side_effect=run) as call:
            value=chain.verified_external_local_run(self.spec,self.config,self.intent,self.expected)
            self.assertEqual(call.call_args[0][0][3],'resume-candidate')
            self.assertEqual(call.call_args[0][0][-1],'--verify')
            return value
    def test_exact_retained_result_accepted_without_effect_arguments(self):
        self.assertEqual(self.invoke(),self.expected)
    def test_wrong_result_exit_or_stderr_refused(self):
        for code,err,body in [(1,b'',self.expected),(0,b'warning',self.expected),(0,b'',{'status':'accepted'})]:
            with self.subTest(code=code,body=body),self.assertRaises(Refused):
                self.invoke(SimpleNamespace(returncode=code,stderr=err,stdout=json.dumps(body).encode()))
    def test_controller_and_retained_config_receipt_drift_refused(self):
        with self.assertRaises(Refused):self.invoke(inventory=[b'before',b'after'])
        for key in ('config','result'):
            p=Path(self.spec[key]['path']);before=p.read_bytes()
            with self.subTest(key=key),self.assertRaises(Refused):self.invoke(mutate=lambda:p.write_bytes(before+b' '))
            p.write_bytes(before)
