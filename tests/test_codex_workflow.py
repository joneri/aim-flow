"""Real Git, host test and installed native workflow; synthetic Codex/GitHub only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
import flow_api_delivery as api
import flow_codex_workflow as native
import flow_codex_delivery as cli
import flow_delivery_chain as chain
from aim_ci import control_package, control_archive, verification_runtime, project_validation
from aim_ci.owned_paths import Refused
from test_delivery_chain import GitHubFixture, WORKFLOW

def pin(path):return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
FAKE='''import json,pathlib,sys,uuid
if sys.argv[1:]==['login','status']:
 print('Logged in using ChatGPT');sys.exit(0)
if sys.argv[1:]==['exec','--help']:
 print('--ignore-user-config --output-schema --json --ephemeral');sys.exit(0)
r=json.load(sys.stdin);p=r['packet'];phase=r['phase'];model=sys.argv[sys.argv.index('--model')+1]
if model=='fixture-unknown':sys.exit(9)
if phase=='model-implementation':
 texts=p['editableSources'];path=next(x for x in texts if x.endswith('label.py'))
 old="'Tickets'" if "'Tickets'" in texts[path] else "'MORE'"
 new="'MORE'" if model=='fixture-repair' and old=="'Tickets'" else "'READ MORE'"
 answer={'summary':'Minimal label replacement','edits':[{'path':path,'old':old,'new':new}]}
elif phase=='model-source-review':
 ref={'path':'candidate/src/label.py','startLine':1,'endLine':2}
 answer={'summary':'Source review','findings':[], 'coverage':[{'requirementId':c['id'],'status':'covered','analysis':'Selected source inspected; runtime remains separate','sources':[ref]} for c in p['requirement']['criteria']],
  'unknowns':[], 'selectedTests':['focused-label'],'testRationale':'Project focused label check, one run; no broad suite',
  'executionGate':'ready-for-granted-tests','executionGateRationale':'Only reviewed local fixture recipe; observed runtime evidence follows'}
 if model=='fixture-source-blocked':
  answer['findings']=[{'id':'source-1','description':'Fixture requires an explicit source correction','severity':'medium','requirementIds':[p['requirement']['criteria'][0]['id']],'sources':[ref],
   'disposition':{'impact':'requirement-failure','rationale':'Injected blocking source review for refusal coverage','title':'Source correction required'}}]
 if model=='fixture-pending-source':
  answer['coverage'][0]['status']='unverified'
  answer['coverage'][0]['analysis']='Focused check and retained output have not yet been observed'
  answer['unknowns']=['Whether the declared focused check passed and its JSON was retained']
else:
 answer={'summary':'Observed focused project check and JSON evidence','limitations':['Synthetic model fixture; no live model qualification'], 'findings':[],
  'priorDispositions':[{'id':x['id'],'status':'resolved','analysis':'Observed exact recipe and JSON evidence','evidenceIds':['stage:focused-label','artifact:.evidence/check.json']} for x in p.get('pendingSourceVerification',[])],
  'coverage':[{'requirementId':c['id'],'status':'satisfied','analysis':'Actual selected project test passed and JSON artifact confirms the label',
  'evidenceIds':['stage:focused-label','artifact:.evidence/check.json']} for c in p['requirement']['criteria']]}
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))
print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid5(uuid.NAMESPACE_URL,str(pathlib.Path.cwd())))}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'cached_input_tokens':0,'output_tokens':7}}))
'''

class CodexWorkflowTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
  cls.scratch=Path(cls.tmp.name).resolve();cls.installed=cls.scratch/'installed'
  archive=cls.scratch/'controller.tar.gz';cls.release=control_package.build(ROOT,archive,'0.1.0-local.codex-workflow-test')
  control_archive.install(archive,cls.release['archiveSha256'],cls.installed)
 def setUp(self):
  tmp=tempfile.TemporaryDirectory(dir=self.scratch);self.addCleanup(tmp.cleanup);self.case=Path(tmp.name).resolve()
  self.git=Path(shutil.which('git')).resolve();self.checkout=self.case/'project';self.checkout.mkdir()
  self.git_run('init','-q','--initial-branch=main','--template=')
  self.git_run('config','user.name','Fixture');self.git_run('config','user.email','fixture@example.invalid')
  (self.checkout/'src').mkdir();(self.checkout/'src/label.py').write_text("# label change focused check verify original request\nLABEL = 'Tickets'\n")
  (self.checkout/'.gitignore').write_text('.evidence/\n__pycache__/\n')
  (self.checkout/'check.py').write_text('''import json,pathlib
p=pathlib.Path('src/label.py');text=p.read_text();assert "LABEL = 'READ MORE'" in text
out=pathlib.Path('.evidence');out.mkdir(exist_ok=True)
(out/'check.json').write_text(json.dumps({'label':'READ MORE','passed':True}))
print('focused label check passed')
''')
  self.git_run('add','.');self.git_run('commit','-qm','Fixture label task')
  self.req=self.case/'requirement.md';self.req.write_text('Change the Tickets label to READ MORE and verify the original request with the project focused check.')
  self.cli=self.case/'fake-codex';self.cli.write_text('#!'+sys.executable+'\n'+FAKE);self.cli.chmod(0o700)
  self.catalog=self.case/'test-catalog.json';self.catalog.write_bytes(api.encoded({'schemaVersion':1,
   'stages':[{'id':'focused-label','kind':'test','root':'.','tool':'python','arguments':['-IBS','check.py'],
    'needs':[],'definitionSources':['check.py'],'artifacts':[{'path':'.evidence/check.json','mediaType':'application/json'}]}],
   'requiredTests':['focused-label'],'minimumTests':1,'maximumTests':1}))
  self.c={'format':native.FORMAT,'schemaVersion':1,'manifestSha256':self.release['manifestSha256'],
   'delivery':{'backend':'codex-cli','executionHost':'local'},'codex':pin(self.cli),'model':'fixture-good','reasoning':'high',
   'source':{'checkout':str(self.checkout),'commit':self.git_run('rev-parse','HEAD'),'git':str(self.git),'gitSha256':pin(self.git)['sha256']},
   'requirement':pin(self.req),'allowedDirectories':['src/'],'dispatchDirectory':str(self.case/'codex-dispatch'),
   'timeoutSeconds':None,'testTimeoutSeconds':None,'maximumRounds':2,'maximumCodexTurns':6,
   'testCatalog':pin(self.catalog),'tools':{'python':pin(Path(sys.executable).resolve())},'environment':{'LC_ALL':'C'},
   'outputBytes':1000000,'artifactBytes':1000000,'runtimeReview':'Only local fixture check writes ignored JSON evidence; no network/deployment.',
   'python':pin(Path(sys.executable).resolve())}
  self.config=self.case/'run.json';self.state=self.case/'native-run';self.save()
  for module in (native,chain,chain.publication,chain.delivery):
   p=patch.object(module,'ROOT',self.installed);p.start();self.addCleanup(p.stop)
  cache=patch.object(verification_runtime,'shared_store',lambda runtime_sha:self.case/'verification-cache')
  cache.start();self.addCleanup(cache.stop)
 def git_run(self,*args,cwd=None):
  return subprocess.check_output([str(self.git),'-c','core.hooksPath=/dev/null','-c','commit.gpgSign=false',*args],
   cwd=cwd or self.checkout,env={**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'},text=True,stderr=subprocess.DEVNULL).strip()
 def save(self):self.config.write_bytes(api.encoded(self.c));self.sha=pin(self.config)['sha256']
 def run_native(self,**options):return native.run(self.config,self.sha,self.state,**options)
 def start(self):return self.run_native(authorization=self.run_native()['intentSha256'])
 def local_spec(self):return {'config':pin(self.config),'state':str(self.state),'grant':pin(self.state/'grant.json'),'result':pin(self.state/'result.json')}
 def test_full_source_free_native_run_and_read_only_verification(self):
  self.c['tools']['git']=pin(self.git);self.save()
  def invoke(*extra):
   p=subprocess.run([sys.executable,'-IBS',str(self.installed/'aim-flow.py'),'run-codex','--config',str(self.config),
    '--config-sha256',self.sha,'--state',str(self.state),*extra],cwd=self.case,capture_output=True,text=True)
   self.assertEqual(p.returncode,0,p.stdout+p.stderr);return json.loads(p.stdout)
  preview=invoke();self.assertFalse(self.state.exists())
  result=invoke('--authorize-intent',preview['intentSha256'])
  self.assertEqual(result['status'],'local-feature-evidence-reviewed');self.assertEqual(len(result['modelStages']),3)
  self.assertIsNone(result['amountNanoUsd']);self.assertFalse(result['responsesApiDispatchExecuted'])
  self.assertEqual(invoke('--verify'),result)
  request_path=self.state/'round-001/model-implementation/request.json'
  request=json.loads(request_path.read_bytes());packet=request['packet']
  self.assertFalse(set(packet['contextSources']) & set(packet['editableSources']))
  self.assertEqual(cli.input_summary(request_path.read_bytes()),preview['intent']['firstModelInput'])
  for path,text in {**packet['contextSources'],**packet['editableSources']}.items():
   self.assertEqual(text,(self.checkout/path).read_text())
  self.assertIn("'Tickets'",(self.checkout/'src/label.py').read_text())
  self.assertEqual(len(list((self.case/'codex-dispatch').glob('*.json'))),3)
 def test_large_source_preview_deduplicates_and_refuses_oversize_before_attempt(self):
  # More than half a CLI input in exact editable code: the old duplicated maps
  # exceeded transport capacity even though their union fits comfortably.
  for i in range(3):
   (self.checkout/f'src/label-context-{i}.py').write_text('# label.py change focused check original request\n'+'# context\n'*25000)
  self.git_run('add','.');self.git_run('commit','-qm','Large selected source fixture')
  self.c['source']['commit']=self.git_run('rev-parse','HEAD')
  self.c['intakeLimits']={**native.intake.DEFAULT_LIMITS,'reviewBytes':1000000}
  self.save();preview=self.run_native()
  c,report,files,tests,requirement,intent=native.prepare(self.config,self.sha,self.state)
  context={p:files[p][1].decode() for p in intent['contextPaths']}
  editable={p:files[p][1].decode() for p in report['selection']['editablePaths']}
  packet=native.implementation_packet(requirement,intent['target'],context,editable)
  self.assertEqual({**packet['contextSources'],**packet['editableSources']},context)
  old_packet={**packet,'contextSources':context}
  self.assertGreater(len(api.encoded(native.model.request_for('model-implementation',old_packet)).decode()),cli.MAX_INPUT_CHARACTERS)
  self.assertLess(preview['intent']['firstModelInput']['characters'],cli.MAX_INPUT_CHARACTERS)
  # Escaping, not just original file byte count, can exceed the wire limit.
  (self.checkout/'src/label-context-0.py').write_text('# label.py change focused check original request\n'+'\\'*250000)
  self.git_run('add','.');self.git_run('commit','-qm','Escaped source exceeds transport capacity')
  self.c['source']['commit']=self.git_run('rev-parse','HEAD');self.save()
  actual=subprocess.run
  def only_git(argv,*args,**kwargs):
   self.assertEqual(argv[0],str(self.git),'Oversize preview must not run the account CLI')
   return actual(argv,*args,**kwargs)
  with patch.object(subprocess,'run',side_effect=only_git):
   with self.assertRaisesRegex(Refused,'input_too_large before dispatch'):self.run_native()
  self.assertFalse(self.state.exists());self.assertFalse((self.case/'codex-dispatch').exists())
 def test_real_failed_test_repairs_once_and_delivery_handoff_uses_native_producer(self):
  self.c['model']='fixture-repair';self.save();result=self.start()
  self.assertEqual(result['status'],'local-feature-evidence-reviewed');self.assertEqual(len(result['rounds']),2)
  first=json.loads(api.pinned(result['rounds'][0]['execution']))
  self.assertEqual(first['status'],'failed');self.assertNotEqual(first['stages'][0]['exitCode'],0)
  self.assertEqual(len(result['modelStages']),5);self.assertEqual(self.run_native(verify_only=True),result)
  verified=chain.verify_local(self.local_spec(),self.release['manifestSha256']);self.assertTrue(verified['nativeCodex'])
  self.source=result['source'];self.ds=self.case/'delivery'
  gh=self.case/'no-network-gh';gh.write_text('#!/bin/sh\nexit 91\n');gh.chmod(0o700)
  self.dc={'format':chain.FORMAT,'schemaVersion':1,'manifestSha256':self.release['manifestSha256'],
   'localRun':self.local_spec(),'repository':{'name':'fixture/project','id':123},'baseSha':self.source['baseSha'],
   'baseRef':'main','branch':'flow/native-codex','title':'Apply native Codex candidate','bodyScope':chain.BODY_SCOPE,
   'requiredPrChecks':[{'name':'Build','appId':55}],'requiredPrStatuses':[],
   'workflow':{'path':'.github/workflows/check.yml','sha256':api.digest(WORKFLOW),
    'requiredJobs':[{'key':'frontend-build','name':'Frontend build'}],
    'artifact':{'nameTemplate':'frontend-{sha}-{attempt}','retentionDays':7}},
   'transport':{'gh':pin(gh),'home':str(self.case),'configDirectory':str(self.case),'timeoutSeconds':30}}
  dp=self.case/'delivery.json';dp.write_bytes(api.encoded(self.dc));dsha=pin(dp)['sha256']
  remote=GitHubFixture(self);remote.pr_pending=False;remote.ci_pending=False
  preview=chain.run(dp,dsha,self.ds);self.assertIn('native Codex',preview['intent']['draftBody'])
  outcome=chain.run(dp,dsha,self.ds,preview['intentSha256'],publication_transport=remote,push_transport=remote.push,github_transport=remote)
  self.assertEqual(outcome['status'],'main-push-handoff-ready',outcome);self.assertEqual(remote.effects,['push','draft','ready','merge'])
  pub=json.loads((self.ds/'publication-config.json').read_text());self.assertEqual(pub['candidateVerification']['producerKind'],'codex-workflow')
  self.assertEqual(len(list((self.case/'codex-dispatch').glob('*.json'))),5)
  from flow_release_continuation import verify_handoff
  receipts=sorted(self.ds.glob('result-*.json'));handoff=verify_handoff(pin(receipts[-1]));self.assertEqual(handoff['source'],outcome['source'])
 def test_source_findings_prevent_any_project_execution(self):
  self.c['model']='fixture-source-blocked';self.c['maximumRounds']=1;self.save()
  result=self.start();self.assertEqual(result['status'],'codex-review-blocked');self.assertFalse((self.state/'round-001/validation').exists())
  with self.assertRaises(Refused):chain.verify_local(self.local_spec(),self.release['manifestSha256'])
 def test_pending_execution_allows_tests_but_requires_every_final_disposition(self):
  self.c['model']='fixture-pending-source';self.save();result=self.start()
  final=result['rounds'][-1];source=json.loads(api.pinned(final['sourceReview']))
  self.assertTrue(source['blockers'],'Original pre-execution uncertainties stay recorded')
  self.assertFalse(source['sourceGate']['blockers'])
  executed=json.loads(api.pinned(final['executedReview']))
  self.assertTrue(executed['localFeatureAccepted']);self.assertEqual(len(executed['priorDispositions']),2)
  body=json.loads((self.state/'round-001/model-executed-review/answer.json').read_text())
  body['priorDispositions'][0]['status']='unresolved'
  checked=native.evaluate(body,json.loads(api.pinned(final['sourcePacket'])),json.loads(api.pinned(final['execution'])),source['sourceGate']['pendingVerification'])
  self.assertFalse(checked['localFeatureAccepted'])
  body['priorDispositions'].pop()
  with self.assertRaises(Refused):native.evaluate(body,json.loads(api.pinned(final['sourcePacket'])),json.loads(api.pinned(final['execution'])),source['sourceGate']['pendingVerification'])
 def test_unknown_model_attempt_is_not_replayed(self):
  self.c['model']='fixture-unknown';self.save()
  with self.assertRaises(Refused):self.start()
  before={p:p.read_bytes() for p in (self.case/'codex-dispatch').glob('*.json')};actual=subprocess.run
  with patch.object(subprocess,'run',wraps=actual) as calls:
   with self.assertRaises(Refused):self.run_native(continue_run=True)
  generations=[x for x in calls.call_args_list if x.args[0][0]==str(self.cli) and 'exec' in x.args[0] and '--help' not in x.args[0]]
  self.assertEqual(generations,[]);self.assertEqual(before,{p:p.read_bytes() for p in before})
 def test_turn_limit_stops_before_next_model_stage(self):
  self.c['maximumCodexTurns']=1;self.save()
  with self.assertRaisesRegex(Refused,'allowance exhausted'):self.start()
  self.assertEqual(len(list((self.case/'codex-dispatch').glob('*.json'))),1);self.assertFalse((self.state/'round-001/model-source-review').exists())
 def test_verified_pre_execution_stop_reuses_both_model_stages_and_remaining_allowance(self):
  # A real pinned fixture controller with the prior phase-ordering defect. All
  # its Git/model/source verifiers remain real; only the known gate is restored.
  payload=control_package._payload(ROOT)
  code=payload['lib/flow_codex_workflow.py'].decode()
  current="            if source_result['sourceGate']['blockers']:"
  self.assertEqual(code.count(current),1)
  payload['lib/flow_codex_workflow.py']=code.replace(current,"            if source_result['blockers']:").encode()
  archive=self.case/'legacy.tar.gz';raw,built=control_package._build_archive(payload,'0.1.0-local.legacy-source-gate')
  archive.write_bytes(raw);legacy=self.case/'legacy-installed';control_archive.install(archive,built['archiveSha256'],legacy)
  self.c.update(model='fixture-pending-source',manifestSha256=built['manifestSha256'],maximumRounds=1,maximumCodexTurns=3);self.save()
  def legacy_run(*extra):
   p=subprocess.run([sys.executable,'-IBS',str(legacy/'aim-flow.py'),'run-codex','--config',str(self.config),
    '--config-sha256',self.sha,'--state',str(self.state),*extra],capture_output=True,text=True)
   return json.loads(p.stdout)
  preview=legacy_run();old=legacy_run('--authorize-intent',preview['intentSha256'])
  self.assertEqual(old['status'],'codex-review-blocked');self.assertEqual(len(old['modelStages']),2)
  self.assertFalse((self.state/'round-001/validation').exists())
  prior=self.local_spec();prior['controller']={'path':str(legacy),'manifestSha256':built['manifestSha256']}
  old_review=json.loads(api.pinned(old['rounds'][-1]['sourceReview']))
  original_files={p:p.read_bytes() for p in self.state.glob('round-*/model-*/result.json')}
  self.c.update(manifestSha256=self.release['manifestSha256'],maximumCodexTurns=1,
   resumeCandidate={'localRun':prior,'deferredSourceItems':[x['id'] for x in native.pending_source_items(old_review)],
    'rationale':'Collect only the already granted unexecuted focused test and resolve each pending item in a fresh evidence review.'})
  self.config=self.case/'continuation.json';self.state=self.case/'continuation-run';self.save()
  result=self.start();self.assertEqual(result['status'],'local-feature-evidence-reviewed')
  self.assertEqual(result['retainedModelTurns'],2);self.assertEqual(len(result['modelStages']),3)
  self.assertEqual(result['candidateReceipt'],old['candidateReceipt'])
  self.assertEqual(len(list((self.case/'codex-dispatch').glob('*.json'))),3)
  self.assertEqual(original_files,{p:p.read_bytes() for p in original_files})
  self.assertEqual(self.run_native(verify_only=True),result)
 def test_artifact_tampering_refuses_read_only_delivery_verification(self):
  result=self.start();final=Path(result['producerState'])
  actual=project_validation._verification_process
  with patch.object(project_validation,'_verification_process',wraps=actual) as launches:
   chain.verify_local(self.local_spec(),self.release['manifestSha256'])
   first=launches.call_count
   chain.verify_local(self.local_spec(),self.release['manifestSha256'])
   self.assertGreater(first,0)
   self.assertEqual(launches.call_count,first,'Unchanged native proof must reuse its verified input closure')
  artifact=final/'validation/artifact-0.json';original=artifact.read_bytes();artifact.write_text('{"passed":false}')
  with self.assertRaises(Refused):chain.verify_local(self.local_spec(),self.release['manifestSha256'])
  artifact.write_bytes(original);review_file=final/'source-review.json';review_file.unlink()
  with self.assertRaises(Refused):self.run_native(verify_only=True)
  self.assertFalse(review_file.exists(),'Read-only verification must not recreate missing evidence')
if __name__=='__main__':unittest.main()
