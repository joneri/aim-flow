"""Narrow host selection/context/receipt integration; external process adapter tested separately."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_workflow_validation as phase
import flow_review_execution_context as context
import flow_execution_review as executed
from aim_ci.owned_paths import Refused

class HostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        pin = {'path': str(Path(sys.executable).resolve()), 'sha256': api.digest(Path(sys.executable).resolve().read_bytes())}
        self.spec = {'format':'aim-flow-project-host-spec','schemaVersion':1,'job':'unused','sourcePaths':[],
            'tools':{'node':pin,'npm':pin,'git':pin}, 'environment':{'HOME':'/Users/private-person','LANG':'C'},
            'runtimeReview':['Existing project host runtime, not sandboxed'], 'sourceReview':['Source review required'],
            'timeoutSeconds':120,'outputBytes':32_000_000}
        path=self.root/'host.json';path.write_bytes(api.encoded(self.spec))
        self.resource={'id':'host','kind':'node-runtime','identity':'host-node','requirements':[],
            'qualification':'Not yet executed','executionSpec':{'backend':'local',**phase.workflow.pin(path)},
            'capabilities':{'nodeVersion':'24.19.0','npmVersion':'11.6.2','platform':'darwin','architecture':'arm64'}}
        self.stages=[{'root':'.','operation':'ci','script':None}]
        self.declaration={'jobs':[{'id':'install','stages':self.stages}]}
        self.derived={'declaration':self.declaration,'resourceNeeds':[
            {'step':'install','kind':'node-runtime','requirements':['node >=24','npm named scripts with normal lifecycle hooks']},
            {'step':'install','kind':'npm-cache','requirements':['lock-sha256:'+'a'*64]}]}
        self.catalog={'resources':[self.resource]}
        self.candidate={'commit':'a'*40,'tree':'b'*40,'checkout':'/private/source'}

    def test_online_host_does_not_invent_offline_cache_requirement(self):
        row,spec=phase.select_resource(self.derived,self.catalog)
        self.assertEqual(row,self.resource);self.assertEqual(spec,self.spec)
        self.spec['environment']['npm_config_offline']='true'
        Path(self.resource['executionSpec']['path']).write_bytes(api.encoded(self.spec))
        self.resource['executionSpec'].update(phase.workflow.pin(Path(self.resource['executionSpec']['path'])))
        with self.assertRaises(Refused):phase.select_resource(self.derived,self.catalog)

    def test_host_context_states_normal_network_no_sandbox_and_withheld_paths(self):
        value=context.build(self.candidate,{'sha256':'c'*64},self.resource,self.spec,self.declaration,'d'*64)
        packet={'candidate':{'commit':'a'*40,'tree':'b'*40},'workspaceContext':{'stages':self.stages}}
        self.assertEqual(context.validate(value,packet),value)
        self.assertIn('normal host network',value['contract']['network'])
        self.assertIn('NOT a security sandbox',value['contract']['isolation'])
        self.assertNotIn('/Users/private-person',json.dumps(value))
        self.assertEqual(value['initialEnvironment']['withheldNames'],['HOME','PATH'])
        self.assertNotIn('npm_config_offline',value['initialEnvironment']['values'])
        bad=copy.deepcopy(value);bad['contract']['network']='none'
        with self.assertRaises(Refused):context.validate(bad,packet)

    def test_host_receipt_binding_and_public_path_projection_preserve_artifacts(self):
        result={'executionBackend':'host','intentSha256':'a'*64,'receiptSha256':'b'*64,'report':{'outputs':{}}}
        output=phase.generated_output_evidence(result,{'workspacePolicy':{'generatedOutputs':[]}},self.candidate)
        self.assertEqual(output['executionReceiptSha256'],'b'*64)
        self.assertNotIn('containerReceiptSha256',output)
        original={'validation':{'report':{'runtime':{'location':'/private/state/host-attempt/project'},
            'stages':[{'stdout':'/private/state/host-attempt/project/file.js'}],
            'artifacts':[{'base64':'exact untouched bytes'}]}}}
        public=executed.public_host_observations(original,[(Path('/private/state/host-attempt/project'),'/project-workspace')])
        self.assertEqual(public['validation']['report']['stages'][0]['stdout'],'/project-workspace/file.js')
        self.assertEqual(public['validation']['report']['artifacts'],original['validation']['report']['artifacts'])
        self.assertIn('/private/state',original['validation']['report']['stages'][0]['stdout'])

if __name__=='__main__':unittest.main()

# One actual workflow/installed producer path; only the host process boundary is injected.
import test_review_execution_context as context_fixture
from aim_ci import project_host as host

class HostWorkflowDispatchTests(context_fixture.ContextContinuationTests):
    def test_source_review_dispatch_and_readonly_host_receipt_verification(self):
        pin=self.pin(Path(sys.executable).resolve())
        self.spec={'format':host.SPEC,'schemaVersion':1,'job':'unused','sourcePaths':[],
            'tools':{'node':pin,'npm':pin,'git':pin},'environment':{'LANG':'C'},
            'runtimeReview':['Fixture native runtime'],'sourceReview':['Pending source review'],
            'timeoutSeconds':60,'outputBytes':1_000_000}
        self.spec_path.write_bytes(api.encoded(self.spec))
        self.catalog['resources']=self.catalog['resources'][:1]
        self.catalog['resources'][0]['executionSpec']={'backend':'local',**self.pin(self.spec_path)}
        self.catalog['resources'][0]['capabilities']['platform']='darwin'
        catalog_path=Path(self.wc['resourceCatalog']['path']);catalog_path.write_bytes(api.encoded(self.catalog))
        self.wc['resourceCatalog']=self.pin(catalog_path)
        self.wpath.write_bytes(api.encoded(self.wc));self.wsha=self.pin(self.wpath)['sha256']
        self.pc['workflowConfig']=self.pin(self.wpath);self.save_phase_config()
        self.grant();preview=self.phase()
        self.assertEqual(preview['intent']['executionBackend'],'host')
        def execute(root,args,intent,reader):
            state=args.state
            self.assertTrue((self.ps/'host-dispatch.json').exists())
            api.durable(state,'started.json',api.encoded({'intentSha256':host.local.digest(intent),'replayAuthorized':False}))
            sources={n:{k:r[k] for k in ('sha256','mode','bytes')} for n,r in intent['sources'].items()}
            report={'status':'passed','sourcesBefore':sources,'sourcesAfter':sources,
                'runtime':{'actualNodeVersion':'v24.22.2','actualNpmVersion':'11.19.1'},
                'stages':[{'index':i,'root':stage['root'],'projectArgv':stage['argv'],
                    'argv':[intent['tools']['node']['path'],intent['tools']['npm']['path'],*stage['argv'][1:]],
                    'exitCode':0,'termination':None,'sourcesAfter':sources} for i,stage in enumerate(intent['job']['stages'])],
                'outputs':{'project/web/bundle.zip':{'type':'file','sha256':'a'*64,'bytes':42,'mode':0o644}}}
            api.durable(state,'report.json',api.encoded(report))
            return {'format':'aim-flow-project-host-result','schemaVersion':1,'status':'passed',
                'intentSha256':host.local.digest(intent),'report':report,'job':intent['job']['id'],
                'executionBackend':'host','controlFiles':{'report.json':self.pin(state/'report.json')['sha256']}}
        with patch.object(host,'execute',side_effect=execute) as dispatch, patch.object(phase.container,'execution',side_effect=AssertionError('wrong adapter')):
            result=self.grant_phase()
            self.assertEqual(result['status'],'local-validation-passed')
            self.assertEqual(result['executionBackend'],'host')
            before={p:p.read_bytes() for p in self.ps.rglob('*') if p.is_file()}
            self.assertEqual(phase.verify(self.ppath,self.psha,self.ps),result)
            self.assertEqual(dispatch.call_count,1)
            self.assertEqual({p:p.read_bytes() for p in self.ps.rglob('*') if p.is_file()},before)

for name in dir(context_fixture.ContextContinuationTests):
    if name.startswith('test_') and name not in HostWorkflowDispatchTests.__dict__:
        setattr(HostWorkflowDispatchTests,name,None)
