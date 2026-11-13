"""Actual host Node/npm process and Git input fixtures; no paid/external calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import project_host as host
from aim_ci.owned_paths import Refused
import test_project_command as fixture


class HostTests(unittest.TestCase):
    setUpClass = classmethod(fixture.ProjectCommandTests.setUpClass.__func__)
    git_run = fixture.ProjectCommandTests.git_run

    def setUp(self):
        fixture.fixtures.ProjectOnboardingTests.setUp(self)
        self.node = Path(shutil.which('node')).resolve()
        self.npm = Path(shutil.which('npm')).resolve()
        (self.checkout / 'package.json').write_text(json.dumps({'scripts': {'test':'node check.cjs'}}))
        (self.checkout / 'check.cjs').write_text("""const fs=require('node:fs');
const mode=process.env.FIXTURE_MODE;
if(mode==='nonzero')process.exit(9);
if(mode==='drift')fs.writeFileSync('package.json','{}');
if(mode==='missing')process.exit(0);
const http=require('node:http');
const server=http.createServer((req,res)=>res.end('normal-host-network'));
server.listen(0,'127.0.0.1',async()=>{
 try {const text=await(await fetch('http://127.0.0.1:'+server.address().port)).text();
 fs.mkdirSync('output',{recursive:true});fs.writeFileSync('output/result.json',JSON.stringify({ok:true,text}));}
 finally{server.close();}
});
""")
        self.git_run('add', '.'); self.git_run('commit', '-qm', 'Host fixture')
        self.target = {'checkout':str(self.checkout), 'commit':self.git_run('rev-parse','HEAD'),
                       'tree':self.git_run('rev-parse','HEAD^{tree}')}
        self.pin = lambda p: {'path':str(p), 'sha256':fixture.sha(p)}
        self.instance_path = self.case / 'host-instance.json'
        self.instance_path.write_text(json.dumps({'packet':{'git':{'path':str(self.git),'sha256':self.git_pin}}}))
        self.spec_path = self.case / 'host-spec.json'; self.declaration = self.case / 'declaration.json'
        self.declaration.write_text('{}')
        home = self.case / 'home'; home.mkdir()
        self.spec = {'format':host.SPEC,'schemaVersion':1,'job':'test',
            'sourcePaths':['package.json','check.cjs'],
            'tools':{'node':self.pin(self.node),'npm':self.pin(self.npm),'git':self.pin(self.git),
                     'sh':self.pin(Path('/bin/sh').resolve())},
            'environment':{'HOME':str(home),'LC_ALL':'C','FIXTURE_MODE':'success'},
            'runtimeReview':['Normal local Node/npm; real loopback test, no external call'],
            'sourceReview':['Harmless committed fixture, no deployment'], 'timeoutSeconds':30,'outputBytes':1000000,
            'artifactCapture':{'schemaVersion':1,'files':[{'path':'output/result.json','mediaType':'application/json'}],
                'maxFiles':1,'maxFileBytes':10000,'maxTotalBytes':10000}}
        self.handoff = {'target':self.target, 'runtimes':{'host':{'requirements':['npm named scripts with normal lifecycle hooks','node >=20','npm >=9']}}, 'jobs':[{'id':'test','runtime':'host','mode':'local-sequence','needs':[],
            'stages':[{'argv':['npm','run','test'],'root':'.','operation':'run'}]}],
            'packageRoots':{'.':{'manifest':'package.json','lockfile':'package-lock.json'}},
            'excludedCoverage':[],'review':{},'agentUnknowns':[]}
        self.state = self.case / 'host-attempt'; self.save()
        mock = patch.object(host.planning, 'derive', return_value=self.handoff); mock.start(); self.addCleanup(mock.stop)

    def save(self): self.spec_path.write_text(json.dumps(self.spec))

    def args(self, **kw):
        values = dict(manifest_sha256=self.release['manifestSha256'], instance=self.instance_path,
            instance_sha256=fixture.sha(self.instance_path), instance_controller=self.installed,
            declaration=self.declaration, declaration_sha256=fixture.sha(self.declaration),
            execution_spec=self.spec_path, execution_spec_sha256=fixture.sha(self.spec_path),state=self.state,
            action='execute-project-host-validation',preview=True,authorize_intent=None)
        values.update(kw); return SimpleNamespace(**values)

    def run_host(self):
        preview = host.execution(self.installed, self.args())
        return host.execution(self.installed, self.args(preview=False, authorize_intent=preview['intentSha256']))

    def test_real_npm_host_network_artifact_and_readonly_verification(self):
        original = (self.checkout/'package.json').read_bytes()
        preview = host.execution(self.installed,self.args())
        self.assertFalse(self.state.exists()); self.assertIn('Normal project host network',preview['intent']['network'])
        result = self.run_host()
        self.assertEqual(result['status'],'passed',result.get('report'))
        self.assertEqual(result['report']['stages'][0]['projectArgv'],['npm','run','test'])
        self.assertEqual(result['report']['artifacts'][0]['mediaType'],'application/json')
        self.assertEqual((self.checkout/'package.json').read_bytes(), original)
        self.assertFalse((self.checkout/'output').exists())
        shutil.rmtree(self.state/'project')
        actual = host.execution(self.installed,self.args(action='verify-project-host-validation',receipt_sha256=result['receiptSha256']))
        self.assertEqual(actual, result)
        with self.assertRaises(Refused): self.run_host()

    def test_nonzero_missing_artifact_and_source_drift_are_failed(self):
        for mode in ('nonzero','missing','drift'):
            with self.subTest(mode=mode):
                self.state=self.case/('host-'+mode); self.spec['environment']['FIXTURE_MODE']=mode; self.save()
                result=self.run_host()
                self.assertEqual(result['status'],'failed',result)
                self.assertFalse(result['replayAuthorized'])
                if mode=='nonzero': self.assertEqual(result['report']['stages'][0]['exitCode'],9)
                if mode=='drift': self.assertIn('changed selected source',result['report']['failure'])

    def test_exact_authority_and_environment_and_log_tamper(self):
        with self.assertRaises(Refused): host.execution(self.installed,self.args(preview=False,authorize_intent='0'*64))
        self.spec['environment']['PATH']='/usr/bin';self.save()
        with self.assertRaises(Refused): host.execution(self.installed,self.args())
        del self.spec['environment']['PATH'];self.save()
        result=self.run_host();(self.state/'stage-0.stdout').write_text('tampered')
        with self.assertRaises(Refused): host.execution(self.installed,self.args(action='verify-project-host-validation',receipt_sha256=result['receiptSha256']))

    def test_failure_stops_later_stage_and_preserves_exact_original(self):
        self.spec['environment']['FIXTURE_MODE']='nonzero';self.save()
        self.handoff['jobs'][0]['stages']*=2
        result=self.run_host()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(result['report']['stages']),1)
        self.assertFalse((self.state/'stage-1.stdout').exists())
        self.assertEqual(self.git_run('status','--porcelain'),'')

    def test_actual_runtime_mismatch_stops_before_project_stage(self):
        self.handoff['runtimes']['host']['requirements'] = ['node >=999']
        result = self.run_host()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['report']['stages'], [])
        self.assertIn('does not satisfy source requirement', result['report']['failure'])
        self.assertFalse((self.state / 'stage-0.stdout').exists())

    def test_selected_artifact_limit_is_enforced_before_read(self):
        project = self.case / 'artifact-fixture'; (project / 'output').mkdir(parents=True)
        path = project / 'output/result.json'; path.write_bytes(b'x' * 10001)
        intent = {'generatedSourcePaths':[], 'artifactCapture': self.spec['artifactCapture']}
        with patch.object(host, 'read_owned_content', side_effect=AssertionError('Oversized artifact was read')):
            with self.assertRaisesRegex(Refused, 'declared file limit'):
                host.outputs(project, intent, {})


if __name__ == '__main__': unittest.main()
