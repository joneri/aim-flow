"""Real installed offline job execution; no provider and no production transport."""
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
from aim_ci import project_execution as execution
from aim_ci.owned_paths import Refused
import test_project_validation as planning_tests


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(sys.platform=='darwin','Qualified process adapter requires macOS')
class ProjectExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        planning_tests.ProjectValidationTests.setUpClass()
        cls.fixture=planning_tests.ProjectValidationTests()
        cls.fixture.setUp();cls.addClassCleanup(cls.fixture.doCleanups)
        f=cls.fixture
        (f.checkout/'scripts/check.py').write_text("require('node:fs').writeFileSync(process.env.TMPDIR+'/actual-project-ran', 'yes'); console.log('actual project script'); if(process.env.EXAMPLE_OUTPUTS){ const fs=require('node:fs');fs.writeFileSync('../bundle.zip','archive-bytes');fs.writeFileSync('../dist/output.js','built');const before=fs.statSync(__filename);fs.utimesSync(__filename,before.atime,new Date());fs.utimesSync(__filename,before.atime,before.mtime); } if(process.env.EXAMPLE_FAILURE)process.exit(7);\n")
        f.git_run('add','.');f.git_run('commit','-qm','Harmless actual Node test')
        f.commit=f.git_run('rev-parse','HEAD')
        f.packet_path=f.case/'new-packet.json';f.assessment_path=f.case/'new-assessment.json';f.instance_path=f.case/'new-instance.json'
        packet=f.prepare();f.record(f.assessment(packet));f.instance=json.loads(f.instance_path.read_bytes())
        f.d['instanceSha256']=sha(f.instance_path);f.d['target']=f.instance['target'];
        serial=copy.deepcopy(f.d['jobs'][1]);serial['id']='serial-component';serial['stages']*=2;f.d['jobs'].append(serial);f.save()
        cls.handoff=execution.planning.derive(f.current,f.args())
        paths = {name: shutil.which(name) for name in ('node', 'git', 'npm', 'sandbox-exec')}
        if not all(paths.values()):
            raise unittest.SkipTest('Real macOS execution requires node, git, npm and sandbox-exec on PATH')
        node, git, npm, sandbox = (Path(paths[name]).resolve() for name in ('node', 'git', 'npm', 'sandbox-exec'))
        npm_tree=execution.tree_identity(npm.parent.parent)
        cls.base={'format':execution.SPEC,'schemaVersion':1,'job':'nested-component',
            'sourcePaths':['web/package.json','scripts/check.py'],
            'tools':{k:{'path':str(p),'sha256':sha(p)} for k,p in {'node':node,'git':git,'npm':npm,'sandbox':sandbox}.items()},
            'npmTree':{k:npm_tree[k] for k in ('path','sha256')},'npmCache':None,
            'runtimeReview':['Actual local toolchain only; not hosted CI equivalence.'],
            'sourceReview':['Harmless committed component writes scratch marker; prehook preserved.'],
            'writablePaths':[],'writableFiles':[],'sourceWriteExceptions':[],'applicationEnvironment':{},'utilities':{},'timeoutSeconds':30,'outputBytes':100000}

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=self.fixture.case);self.addCleanup(self.folder.cleanup)
        self.case=Path(self.folder.name).resolve();self.spec=copy.deepcopy(self.base)
        self.spec_path=self.case/'spec.json';self.state=self.case/'attempt';self.save()

    def save(self): self.spec_path.write_text(json.dumps(self.spec,indent=2)+'\n')

    def args(self,action='run-project-validation',**extra):
        values=vars(self.fixture.args()).copy();values.update(action=action,execution_spec=self.spec_path,
            execution_spec_sha256=sha(self.spec_path),state=self.state,preview=False,authorize_intent=None)
        values.update(extra);return SimpleNamespace(**values)

    def prepare(self): return execution.prepare(self.fixture.current,self.args())

    def run_cli(self,flags,code=0):
        args=self.args();cmd=[str(Path(sys.executable).resolve()),'-IBS',str(self.fixture.current/'aim-flow.py'),flags[0]]
        for name in ('manifest_sha256','instance','instance_sha256','instance_controller','instance_controller_manifest_sha256',
                     'python','python_sha256','declaration','declaration_sha256','execution_spec','execution_spec_sha256','state'):
            cmd+=['--'+name.replace('_','-'),str(getattr(args,name))]
        cmd+=flags[1:];r=subprocess.run(cmd,cwd=self.case,env=self.fixture.env,capture_output=True,text=True,timeout=90)
        diagnostic=''.join(p.read_text() for p in self.state.glob('*.stderr')) if self.state.exists() else ''
        self.assertEqual(r.returncode,code,r.stderr+'\n'+r.stdout+'\n'+diagnostic)
        return json.loads(r.stdout) if r.stdout else r.stderr

    def test_installed_real_component_hooks_receipt_verification_and_no_replay(self):
        preview=self.run_cli(['run-project-validation','--preview']);self.assertFalse(self.state.exists())
        result=self.run_cli(['run-project-validation','--authorize-intent',preview['intentSha256']])
        self.assertEqual(result['status'],'passed');self.assertTrue(result['qualificationPassed']);self.assertFalse(result['ready'])
        self.assertEqual((self.state/'work/tmp/actual-project-ran').read_text(),'yes')
        self.assertIn('reviewed-prehook',(self.state/'stage-0.stdout').read_text())
        self.assertEqual(result['stages'][0]['projectArgv'],['npm','run','test'])
        verified=self.run_cli(['verify-project-validation-run','--receipt-sha256',result['receiptSha256']])
        self.assertEqual(verified,result)
        self.assertIn('already exists',self.run_cli(['run-project-validation','--authorize-intent',preview['intentSha256']],code=1))
        (self.state/'stage-0.stdout').write_text('tampered')
        self.assertIn('SHA-256 mismatch',self.run_cli(['verify-project-validation-run','--receipt-sha256',result['receiptSha256']],code=1))

    def test_digest_change_does_not_execute(self):
        before=self.prepare()[0];self.spec['runtimeReview'].append('New exception');self.save()
        with self.assertRaises(Refused): execution.execution(self.fixture.current,self.args(authorize_intent=execution.digest(before)))
        self.assertFalse(self.state.exists())

    def test_hosted_unresolved_dependencies_and_missing_manifest_refuse(self):
        for field,value in [('job','hosted-job'),('sourcePaths',['scripts/check.py'])]:
            self.spec=copy.deepcopy(self.base);self.spec[field]=value;self.save()
            with self.assertRaises(Refused): self.prepare()
        self.spec=copy.deepcopy(self.base);self.save();handoff=copy.deepcopy(self.handoff)
        handoff['jobs'][1]['needs']=['local-aggregate']
        with patch.object(execution.planning,'derive',return_value=handoff):
            with self.assertRaises(Refused): self.prepare()

    def test_write_overlap_traversal_tool_drift_and_bad_limits_refuse(self):
        mutations=[lambda d:d.update(writablePaths=['web']),lambda d:d.update(writablePaths=['../escape']),
            lambda d:d['tools']['node'].update(sha256='0'*64),lambda d:d.update(timeoutSeconds=True),
            lambda d:d.update(outputBytes=0),lambda d:d['npmTree'].update(sha256='0'*64),
            lambda d:d.update(sourcePaths=['index-only.js']),lambda d:d.update(argv=['deploy'])]
        for change in mutations:
            self.spec=copy.deepcopy(self.base);change(self.spec);self.save()
            with self.assertRaises(Refused): self.prepare()

    def test_external_tool_hardlinks_are_pinned_without_weakening_owned_state(self):
        tool=self.case/'tool';tool.write_text('#!/bin/sh\nexit 0\n');tool.chmod(0o755);os.link(tool,self.case/'alias')
        pin={'path':str(tool),'sha256':sha(tool)};self.assertEqual(execution.tool_identity(pin),pin)
        (self.case/'alias').write_text('changed')
        with self.assertRaises(Refused):execution.tool_identity(pin)

    def test_complete_npm_tree_detects_import_mutation_and_escape_link(self):
        tree=self.case/'runtime';tree.mkdir();(tree/'cli.js').write_text('entry');(tree/'import.js').write_text('original')
        before=execution.tree_identity(tree);(tree/'import.js').write_text('changed')
        self.assertNotEqual(before['sha256'],execution.tree_identity(tree)['sha256'])
        (tree/'escape').symlink_to('/usr/bin/git')
        with self.assertRaises(Refused): execution.tree_identity(tree)

    def test_qualification_failure_never_launches_project_and_retains_failed_result(self):
        intent,reader,npm,cache=self.prepare();self.state.mkdir();(self.state/'intent.json').write_bytes(execution.onboarding.encode(intent))
        fake={'argv':[],'exitCode':1,'termination':None,'elapsedSeconds':0,'logs':{}}
        with patch.object(execution,'capture',return_value=fake) as capture:
            result=execution.execute(self.state,intent,reader,npm,cache)
        self.assertEqual(capture.call_count,1);self.assertEqual(result['status'],'failed');self.assertEqual(result['unrunStages'],[0])
        self.assertFalse((self.state/'work/tmp/actual-project-ran').exists())

    def test_capture_timeout_output_bound_and_exited_parent_descendant_cleanup(self):
        env={'PATH':'/usr/bin:/bin'};python=str(Path(sys.executable).resolve())
        noisy=execution.capture([python,'-c','print("x"*100000)'],self.case,env,self.case,'noisy',3,100)
        self.assertEqual(noisy['termination'],'output-limit');self.assertLessEqual((self.case/'noisy.stdout').stat().st_size,100)
        marker=self.case/'late-marker'
        child='import time,pathlib;time.sleep(1);pathlib.Path('+repr(str(marker))+').touch()'
        parent='import subprocess;subprocess.Popen(['+repr(python)+',"-c",'+repr(child)+'])'
        result=execution.capture([python,'-c',parent],self.case,env,self.case,'pipe',.2,1000)
        self.assertEqual(result['termination'],'timeout');time.sleep(1.1);self.assertFalse(marker.exists())

    def test_exact_outputs_reviewed_source_exception_and_tampering(self):
        self.spec.update(writablePaths=['dist'],writableFiles=['bundle.zip'],sourceWriteExceptions=['scripts/check.py'],
                         applicationEnvironment={'EXAMPLE_OUTPUTS':'1'});self.save()
        preview=self.run_cli(['run-project-validation','--preview'])
        result=self.run_cli(['run-project-validation','--authorize-intent',preview['intentSha256']])
        self.assertEqual(result['status'],'passed')
        full=json.loads((self.state/'result.json').read_text())
        self.assertEqual(full['outputs']['project/bundle.zip']['sha256'],hashlib.sha256(b'archive-bytes').hexdigest())
        self.assertEqual(full['outputs']['project/dist/output.js']['type'],'file')
        self.assertIn('"sourceExceptions":1',(self.state/'qualification.stdout').read_text())
        source=self.state/'project/scripts/check.py';original=source.read_bytes();source.write_text('tampered source')
        self.assertIn('Retained committed execution source',self.run_cli(['verify-project-validation-run','--receipt-sha256',result['receiptSha256']],code=1))
        source.write_bytes(original)
        (self.state/'project/bundle.zip').write_text('changed')
        self.assertIn('outputs',self.run_cli(['verify-project-validation-run','--receipt-sha256',result['receiptSha256']],code=1))

    def test_output_inventory_never_follows_symlinks(self):
        self.state.mkdir();(self.state/'work').mkdir();(self.state/'work/escape').symlink_to('/Users')
        value=execution.output_inventory(self.state,{'writablePaths':[],'writableFiles':[]})
        self.assertEqual(set(value),{'work','work/escape'});self.assertEqual(value['work/escape']['type'],'link')

    def test_first_stage_failure_skips_following_stages(self):
        self.spec.update(job='serial-component',applicationEnvironment={'EXAMPLE_FAILURE':'1'});self.save()
        preview=self.run_cli(['run-project-validation','--preview'])
        result=self.run_cli(['run-project-validation','--authorize-intent',preview['intentSha256']],code=3)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['stages'][0]['exitCode'],7)
        self.assertFalse((self.state/'stage-1-started.json').exists())

    def test_project_config_and_runtime_environment_override_refuse(self):
        for environment in ({'NODE_OPTIONS':'--require /tmp/evil'},{'NPM_CONFIG_IGNORE_SCRIPTS':'true'},{'PATH':'/tmp'}):
            self.spec['applicationEnvironment']=environment;self.save()
            with self.assertRaises(Refused):self.prepare()
        self.spec=copy.deepcopy(self.base);self.save()
        real=execution.source_selection
        def configured(*args):
            sources,reader=real(*args)
            reader.entries=lambda *args,**kwargs: iter([('.npmrc','100644','blob','0'*40)])
            return sources,reader
        with patch.object(execution,'source_selection',side_effect=configured):
            with self.assertRaisesRegex(Refused,'cannot be silently omitted'):self.prepare()

    def test_unknown_effect_never_replays(self):
        intent=self.prepare()[0]
        with patch.object(execution,'execute',side_effect=RuntimeError('after durable dispatch')):
            with self.assertRaises(RuntimeError): execution.execution(self.fixture.current,self.args(authorize_intent=execution.digest(intent)))
        with patch.object(execution,'execute') as dispatch:
            with self.assertRaises(Refused): execution.execution(self.fixture.current,self.args(authorize_intent=execution.digest(intent)))
            self.assertFalse(dispatch.called)
        self.assertEqual(json.loads((self.state/'effects.json').read_text())['operations']['project-validation']['state'],'unknown')


if __name__=='__main__':unittest.main()
