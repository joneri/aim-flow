"""Container intent, strict observations and retained evidence; no daemon needed."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_project_validation as planning_tests


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        planning_tests.ProjectValidationTests.setUpClass()
        cls.f=planning_tests.ProjectValidationTests();cls.f.setUp();cls.addClassCleanup(cls.f.doCleanups)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=self.f.case);self.addCleanup(self.tmp.cleanup)
        self.case=Path(self.tmp.name).resolve();cache=self.case/'cache';cache.mkdir()
        self.spec={'format':container.SPEC,'schemaVersion':1,'job':'nested-component',
            'sourcePaths':['web/package.json','scripts/check.py'],
            'docker':{'path':str(Path(sys.executable).resolve()),'sha256':sha(sys.executable),'endpoint':'unix:///tmp/test.sock'},
            'image':{'id':'sha256:'+'a'*64,'repoDigest':'node@sha256:'+'b'*64,'os':'linux','architecture':'arm64','nodeVersion':'24.22.2','npmVersion':'11.19.1'},
            'npmCache':{k:v for k,v in container.local.tree_identity(cache).items() if k!='files'},
            'runtimeReview':['Pinned local synthetic image, no hosted qualification.'],'sourceReview':['Harmless committed fixture.'],
            'applicationEnvironment':{},'timeoutSeconds':60,'outputBytes':1000000,'memoryBytes':256*1024**2,'processCount':64,'workspaceBytes':64*1024**2}
        self.path=self.case/'spec.json';self.state=self.case/'attempt';self.save()

    def save(self):self.path.write_text(json.dumps(self.spec)+'\n')

    def args(self,**kw):
        data=vars(self.f.args()).copy();data.update(execution_spec=self.path,execution_spec_sha256=sha(self.path),state=self.state,
            action='run-project-container-validation',preview=True,authorize_intent=None);data.update(kw);return SimpleNamespace(**data)

    def prepare(self):return container.prepare(self.f.current,self.args())

    def test_preview_binds_sources_limits_and_separate_authority_without_daemon(self):
        with patch.object(container.docker,'unix_request',side_effect=AssertionError('Unexpected transport')):
            result=container.execution(self.f.current,self.args())
        self.assertEqual(result['status'],'container-execution-preview');self.assertFalse(result['executionAuthorized'])
        self.assertEqual(result['intent']['job']['stages'][0]['argv'],['npm','run','test']);self.assertFalse(self.state.exists())
        with self.assertRaises(Refused):container.execution(self.f.current,self.args(preview=False,authorize_intent='0'*64))

    def test_prepare_rejects_unknown_fields_paths_runtime_overrides_and_limits(self):
        original=copy.deepcopy(self.spec)
        changes=[lambda s:s.update(argv=['sh']),lambda s:s.update(processCount=True),lambda s:s.update(workspaceBytes=0),
            lambda s:s['docker'].update(endpoint='tcp://localhost:2375'),lambda s:s['docker'].update(endpoint='unix:///tmp/../test.sock'),
            lambda s:s['image'].update(repoDigest='untrusted@sha256:'+'b'*64),lambda s:s.update(job='hosted-job'),
            lambda s:s.update(sourcePaths=['../escape']),lambda s:s['applicationEnvironment'].update(NODE_OPTIONS='--require x')]
        for change in changes:
            self.spec=copy.deepcopy(original);change(self.spec);self.save()
            with self.assertRaises((Refused,ValueError)):self.prepare()

    def test_staging_archive_is_regular_pinned_and_empty_cache_directory_exists(self):
        intent,reader=self.prepare();self.state.mkdir()
        result=container.input_archive(self.f.current,self.state,intent,reader)
        self.assertEqual(result['sha256'],sha(self.state/'inputs.tar'))
        with tarfile.open(self.state/'inputs.tar') as archive:
            names=archive.getnames();self.assertIn('opt/aim-project/npm-cache',names)
            self.assertTrue(archive.getmember('opt/aim-project/npm-cache').isdir())
            self.assertTrue(all(m.isfile() or m.isdir() for m in archive.getmembers()))
            job=json.load(archive.extractfile('opt/aim-project/job.json'))
            self.assertEqual(job,container.job_document(intent));self.assertEqual(job['cache'],{})

    def test_long_npm_cache_content_names_are_safely_retained(self):
        name='_cacache/content-v2/sha512/aa/bb/'+'c'*124
        path=self.case/'cache'/name;path.parent.mkdir(parents=True);path.write_bytes(b'archive-bytes');path.chmod(0o644)
        self.spec['npmCache']={k:v for k,v in container.local.tree_identity(self.case/'cache').items() if k!='files'};self.save()
        intent,reader=self.prepare();self.state.mkdir();container.input_archive(self.f.current,self.state,intent,reader)
        with tarfile.open(self.state/'inputs.tar') as archive:
            self.assertEqual(archive.extractfile('opt/aim-project/npm-cache/'+name).read(),b'archive-bytes')

    def observation(self,intent,staging=False):
        payload=container.payload(intent,intent['image']['id'],'d'*64,staging=staging)
        row={'Id':'e'*64,'Name':'/test','Image':payload['Image'],'Config':{k:copy.deepcopy(v) for k,v in payload.items() if k not in ('Image','HostConfig')},
            'HostConfig':copy.deepcopy(payload['HostConfig']),'Mounts':[],
            'State':{'Status':'created','Running':False,'Pid':0}}
        return payload,row

    def test_container_inspection_rejects_host_access_extra_caps_and_shape_drift(self):
        intent,_=self.prepare();payload,row=self.observation(intent)
        engine=SimpleNamespace(_call=lambda *a:row)
        self.assertTrue(container.inspect_container(engine,'e'*64,'test',payload)['inspected'])
        original=copy.deepcopy(row)
        mutations=[lambda r:r['HostConfig'].update(CapAdd=['SYS_ADMIN']),lambda r:r['HostConfig'].update(NetworkMode='host'),
            lambda r:r['HostConfig'].update(PidMode='host'),lambda r:r['HostConfig'].update(PortBindings={'80/tcp':[{'HostPort':'80'}]}),
            lambda r:r['HostConfig'].update(Binds=['/Users:/host']),lambda r:r['HostConfig'].update(Sysctls={'x':'y'}),
            lambda r:r['Config'].update(Entrypoint=['sh']),lambda r:r['Config'].update(Volumes={'/host':{}}),
            lambda r:r.update(Mounts=[{'Type':'bind','Destination':'/work','RW':True}])]
        for mutate in mutations:
            row=copy.deepcopy(original);mutate(row)
            with self.assertRaises(Refused):container.inspect_container(engine,'e'*64,'test',payload)
        self.assertIn('--disable-sigusr1',payload['Entrypoint']);self.assertIn('noexec',payload['HostConfig']['Tmpfs']['/work'])
        staged,empty=self.observation(intent,staging=True);empty['HostConfig'].pop('Tmpfs')
        self.assertTrue(container.inspect_container(SimpleNamespace(_call=lambda *a:empty),'e'*64,'test',staged)['inspected'])

    def test_exact_image_metadata_is_required(self):
        intent,_=self.prepare();i=intent['image'];row={'Id':i['id'],'RepoDigests':[i['repoDigest']],'Os':'linux','Architecture':'arm64',
            'Config':{'Env':['PATH=/usr/local/bin:/usr/bin:/bin','NODE_VERSION=24.22.2','YARN_VERSION=1.22.22']},'RootFS':{'Layers':['sha256:'+'f'*64]}}
        engine=SimpleNamespace(_call=lambda *a:row)
        self.assertEqual(container.checked_image(engine,intent)['imageId'],i['id'])
        row['Config']['Env'].append('NODE_OPTIONS=--require evil')
        with self.assertRaises(Refused):container.checked_image(engine,intent)

    def test_report_pass_requires_actual_stages_restored_sources_runtime_and_terminal(self):
        intent,_=self.prepare();expected={n:{'sha256':r['sha256'],'mode':int(r['mode'],8)&0o777,'bytes':r['bytes']} for n,r in intent['sources'].items()}
        report={'format':'aim-flow-project-container-report','schemaVersion':1,'intentSha256':container.local.digest(intent),'status':'passed',
            'qualificationPassed':True,'sourcesRestored':True,'ready':False,'acceptance':'not-established','deployment':'not-authorized',
            'sourcesBefore':expected,'sourcesAfter':expected,'runtime':{'actualNodeVersion':'v24.22.2','actualNpmVersion':'11.19.1','nodePath':container.NODE,'npmPath':container.NPM,'helperSha256':intent['helperSha256']},
            'stages':[{'exitCode':0,'termination':None,'index':0,'projectArgv':['npm','run','test'],'root':'web','sourcesAfter':expected}]}
        result={'report':report,'terminal':{'state':{'ExitCode':0,'OOMKilled':False}},'deadlineExceeded':False,'logs':{'exitCode':0,'termination':None},'diagnostic':None}
        self.assertTrue(container.passing_report(intent,result))
        for mutate in [lambda r:r['report'].update(sourcesRestored=False),lambda r:r['report']['runtime'].update(actualNodeVersion='v22.12.0'),
                       lambda r:r['report']['stages'][0].update(projectArgv=['npm','run','other']),lambda r:r.update(deadlineExceeded=True),
                       lambda r:r['terminal']['state'].update(ExitCode=1),lambda r:r['report'].update(sourcesAfter={})]:
            altered=copy.deepcopy(result);mutate(altered);self.assertFalse(container.passing_report(intent,altered))


if __name__=='__main__':unittest.main()
