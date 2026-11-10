"""One explicitly granted npm job in a pinned, network-none Docker container.

The image baseline and original checkout are protected. The private working
copy is mutable during a stage; restoration is checked, not assumed atomic.
"""
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time
from urllib.parse import urlencode

from . import control_package as package, docker_engine as docker
from . import project_execution as local, project_onboarding as onboarding, project_validation as planning
from .effects import EffectJournal
from .owned_paths import Refused, new_owned_directory, read_owned_content, write_owned
from .project_discovery import unique_json

SPEC = 'aim-flow-project-container-spec'
RESULT = 'aim-flow-project-container-result'
HELPER = 'lib/aim_ci/project_container_runner.cjs'
LAYOUT = '/opt/aim-project'
NODE = '/usr/local/bin/node'
NPM = '/usr/local/lib/node_modules/npm/bin/npm-cli.js'


def prepare(root, args):
    handoff = planning.derive(root, args)
    _, spec = planning.pinned(args.execution_spec, args.execution_spec_sha256)
    onboarding._keys(spec, ('format', 'schemaVersion', 'job', 'sourcePaths', 'docker', 'image', 'npmCache',
        'runtimeReview', 'sourceReview', 'applicationEnvironment', 'timeoutSeconds', 'outputBytes',
        'memoryBytes', 'processCount', 'workspaceBytes'), 'container execution spec')
    if spec['format'] != SPEC or type(spec['schemaVersion']) is not int or spec['schemaVersion'] != 1:
        raise Refused('Unsupported project container spec')
    jobs = [j for j in handoff['jobs'] if j['id'] == spec['job']]
    if len(jobs) != 1 or jobs[0]['mode'] == 'hosted-reference' or jobs[0]['needs']:
        raise Refused('Select one local npm job without unresolved dependencies')
    for key in ('runtimeReview', 'sourceReview'):
        onboarding._texts(spec[key], key)
        if not spec[key]: raise Refused('Separate source/effect and runtime reviews are required')
    bounds = {'timeoutSeconds': (1, 3600), 'outputBytes': (4096, 32_000_000),
        'memoryBytes': (64*1024**2, 3*1024**3), 'processCount': (8, 512),
        'workspaceBytes': (16*1024**2, 2*1024**3)}
    for key, (low, high) in bounds.items():
        if type(spec[key]) is not int or not low <= spec[key] <= high:
            raise Refused('Container resource limit outside supported bounds: '+key)
    onboarding._keys(spec['docker'], ('path', 'sha256', 'endpoint'), 'Docker tool')
    tool = local.tool_identity({k: spec['docker'][k] for k in ('path', 'sha256')})
    endpoint = spec['docker']['endpoint']
    if (not isinstance(endpoint, str) or not re.fullmatch(r'unix:///[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.sock', endpoint)
        or any(p in ('.','..') for p in endpoint.removeprefix('unix://').split('/'))):
        raise Refused('Use an explicit local Docker Unix socket, never a context or remote endpoint')
    image = spec['image']
    onboarding._keys(image, ('id', 'repoDigest', 'os', 'architecture', 'nodeVersion', 'npmVersion'), 'image')
    if (not re.fullmatch(r'sha256:[a-f0-9]{64}', image['id']) or
        not re.fullmatch(r'node@sha256:[a-f0-9]{64}', image['repoDigest']) or image['os'] != 'linux' or
        image['architecture'] not in ('arm64', 'amd64') or
        any(not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', image[k]) for k in ('nodeVersion', 'npmVersion'))):
        raise Refused('Pin an exact local official Node image and observed runtime versions')
    application = spec['applicationEnvironment']
    if not isinstance(application, dict): raise Refused('Invalid application environment')
    for name, value in application.items():
        if (not re.fullmatch('[A-Z][A-Z0-9_]*', name) or not isinstance(value, str) or '\0' in value
            or name in ('PATH','HOME','TMPDIR','TMP','TEMP','SHELL','ENV','BASH_ENV','ZDOTDIR','LANG','LC_ALL','XDG_CONFIG_HOME')
            or name.startswith(('NODE_', 'NPM_', 'GIT_', 'DYLD_', 'LD_', 'PYTHON', 'FLOW_'))):
            raise Refused('Application settings cannot override runtime/security configuration')
    onboarding._keys(spec['npmCache'], ('path', 'sha256'), 'cache')
    cache = local.tree_identity(spec['npmCache']['path'])
    if cache['sha256'] != spec['npmCache']['sha256'] or any('link' in v for v in cache['files'].values()):
        raise Refused('Cache must contain the pinned regular-file tree only')
    if any(row['mode'] not in (0o644,0o755) for row in cache['files'].values()):
        raise Refused('Root-owned image cache must be readable by the nonroot runtime')
    _, instance = planning.pinned(args.instance, args.instance_sha256)
    sources, reader = local.source_selection(root, handoff, instance, spec['sourcePaths'])
    configs = {'.npmrc'}
    for name in handoff['packageRoots']:
        directory = Path(name)
        configs.update(str(p/'.npmrc') for p in (directory, *directory.parents) if str(p) != '.')
    if list(reader.entries(reader.commit, *sorted(configs))) or any(Path(n).name == '.npmrc' for n in sources):
        raise Refused('Project npm configuration needs a separate review adapter')
    for stage in jobs[0]['stages']:
        declared = handoff['packageRoots'][stage['root']]
        required = [declared['manifest']] + ([declared['lockfile']] if stage['operation'] == 'ci' else [])
        if not set(required) <= sources.keys(): raise Refused('Stage manifest/lock missing from selected sources')
    state = onboarding._outside(args.state, root, Path(handoff['target']['checkout']))
    onboarding._outside(state, args.instance_controller, Path(handoff['target']['checkout']))
    for path in [args.execution_spec, args.declaration, args.instance, Path(cache['path']), Path(tool['path'])]:
        if path.is_relative_to(state) or state.is_relative_to(path): raise Refused('Attempt state overlaps pinned inputs')
    intent = {'format': 'aim-flow-project-container-intent', 'schemaVersion': 1,
        'controllerManifestSha256': args.manifest_sha256, 'executionSpecSha256': args.execution_spec_sha256,
        'handoffSha256': local.digest(handoff), 'target': handoff['target'], 'state': str(state),
        'job': jobs[0], 'sources': sources, 'cache': cache, 'docker': {**tool, 'endpoint': endpoint}, 'image': image,
        'helperSha256': package.sha256(read_owned_content(root, HELPER)),
        **{key: spec[key] for key in (*bounds, 'applicationEnvironment', 'runtimeReview', 'sourceReview')},
        'network': 'Docker network:none, private loopback namespace; no ports, host mounts or external route',
        'sourceBoundary': 'Read-only image baseline and original checkout. Private tmpfs copied source is mutable during stages; exact final content/mode must match.',
        'authority': 'One separate exact operator grant. No model-derived authority, deployment or acceptance.',
        'excludedCoverage': handoff['excludedCoverage'], 'review': handoff['review'], 'agentUnknowns': handoff['agentUnknowns']}
    return intent, reader


def job_document(intent):
    return {'format': 'aim-flow-project-container-job', 'schemaVersion': 1,
        'intentSha256': local.digest(intent), 'stages': [{k: s[k] for k in ('argv','root')} for s in intent['job']['stages']],
        'sources': {n: {k: r[k] for k in ('sha256','mode','bytes')} for n,r in intent['sources'].items()},
        'cache': intent['cache']['files'], 'runtime': {'nodePath': NODE, 'npmPath': NPM,
            'nodeVersion': intent['image']['nodeVersion'], 'npmVersion': intent['image']['npmVersion']},
        **{k: intent[k] for k in ('applicationEnvironment','timeoutSeconds','outputBytes')}}


def input_archive(root, state, intent, reader):
    files = {'job.json': onboarding.encode(job_document(intent)), 'runner.cjs': read_owned_content(root, HELPER)}
    package.check_digest(files['runner.cjs'], intent['helperSha256'], 'Container helper')
    for name, row in intent['sources'].items():
        raw = reader.blob(row['gitObject']); package.check_digest(raw, row['sha256'], 'Committed source')
        files['source/'+name] = raw
    for name, row in intent['cache']['files'].items():
        raw = read_owned_content(Path(intent['cache']['path']), name)
        package.check_digest(raw, row['sha256'], 'Pinned npm cache'); files['npm-cache/'+name] = raw
    if sum(map(len, files.values())) > 256*1024**2: raise Refused('Container staging archive exceeds supported size')
    buffer = io.BytesIO()
    # npm content-addressed cache basenames exceed USTAR's 100-byte name field.
    # This archive is constructed here from validated regular-file inputs;
    # PAX long-path records do not import caller-controlled tar headers.
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        directories = {Path(LAYOUT.lstrip('/')), Path(LAYOUT.lstrip('/'))/'source', Path(LAYOUT.lstrip('/'))/'npm-cache'}
        for name in files:
            directories.update(p for p in (Path(LAYOUT.lstrip('/'))/name).parents if str(p) != '.')
        for path in sorted(directories, key=lambda p: (len(p.parts), str(p))):
            item=tarfile.TarInfo(str(path));item.type=tarfile.DIRTYPE;item.mode=0o755;archive.addfile(item)
        for name, raw in sorted(files.items()):
            item=tarfile.TarInfo(LAYOUT.lstrip('/')+'/'+name);item.size=len(raw)
            source=intent['sources'].get(name.removeprefix('source/')) if name.startswith('source/') else None
            item.mode=0o755 if source and source['mode']=='100755' else 0o644
            if name.startswith('npm-cache/'): item.mode=intent['cache']['files'][name.removeprefix('npm-cache/')]['mode']
            archive.addfile(item,io.BytesIO(raw))
    reader.verify_head(); raw=buffer.getvalue();write_owned(state,'inputs.tar',raw,exclusive=True)
    return {'sha256':package.sha256(raw),'bytes':len(raw)}


def checked_image(engine, intent):
    expected=intent['image']; row=engine._call('GET','/images/'+expected['id']+'/json',(200,))
    config=row.get('Config',{});env=config.get('Env'); pairs={}
    if isinstance(env,list):
        for item in env:
            if not isinstance(item,str) or '=' not in item: raise Refused('Invalid image environment')
            key,value=item.split('=',1)
            if key in pairs or key not in ('PATH','NODE_VERSION','YARN_VERSION'): raise Refused('Unexpected image environment')
            pairs[key]=value
    if (row.get('Id')!=expected['id'] or expected['repoDigest'] not in row.get('RepoDigests',[])
        or row.get('Os')!=expected['os'] or row.get('Architecture')!=expected['architecture']
        or config.get('Volumes') or config.get('OnBuild') or pairs.get('NODE_VERSION')!=expected['nodeVersion']
        or not isinstance(env,list) or len(env)!=len(pairs)):
        raise Refused('Local Node image differs from the approved immutable runtime')
    return {'imageId':row['Id'],'repoDigests':row['RepoDigests'],'os':row['Os'],'architecture':row['Architecture'],
        'env':env,'layers':row.get('RootFS',{}).get('Layers')}


def payload(intent, image, label, *, staging=False):
    host={'NetworkMode':'none','ReadonlyRootfs':not staging,'Privileged':False,'CapDrop':['ALL'],
        'SecurityOpt':['no-new-privileges:true'],'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'AutoRemove':False,
        'Memory':intent['memoryBytes'],'MemorySwap':intent['memoryBytes'],'PidsLimit':intent['processCount'],
        'Tmpfs':{} if staging else {'/work':f"rw,nosuid,nodev,noexec,mode=1777,size={intent['workspaceBytes']}"},
        'LogConfig':{'Type':'json-file','Config':{'max-size':'36m','max-file':'1'}}}
    return {'Image':image,'Entrypoint':['/bin/false'] if staging else [NODE,'--disable-sigusr1'],
        'Cmd':['--inert-staging-only'] if staging else [LAYOUT+'/runner.cjs'],
        'User':'65534:65534','WorkingDir':'/' if staging else '/work','Tty':False,'NetworkDisabled':True,
        'Healthcheck':{'Test':['NONE']},'Labels':{'org.aim-flow.project-intent':label},'HostConfig':host}


def inspect_container(engine, identifier, name, expected):
    row=engine._call('GET','/containers/'+identifier+'/json',(200,404))
    if row is None:return None
    config=row.get('Config',{});host=row.get('HostConfig',{});want=expected['HostConfig']
    if (row.get('Id')!=identifier or row.get('Name')!='/'+name or row.get('Image')!=expected['Image']
        or any(config.get(k)!=expected[k] for k in ('Entrypoint','Cmd','User','WorkingDir','Tty','NetworkDisabled','Healthcheck'))
        or config.get('Labels',{}).get('org.aim-flow.project-intent')!=expected['Labels']['org.aim-flow.project-intent']
        or config.get('Volumes') or any(host.get(k)!=want[k] for k in want
            if not (k=='Tmpfs' and want[k]=={} and host.get(k) in (None,{})))
        or any(host.get(k) for k in ('Binds','Mounts','VolumesFrom','Devices','DeviceRequests','PortBindings','PublishAllPorts','Dns','DnsOptions','DnsSearch','ExtraHosts','Links','CapAdd','GroupAdd','Sysctls','CgroupParent'))
        or any(host.get(k) in ('host', 'container') or str(host.get(k,'')).startswith('container:') for k in ('PidMode','IpcMode','UTSMode','UsernsMode','CgroupnsMode'))):
        raise Refused('Container identity, isolation or resource settings differ from the exact request')
    mounts=row.get('Mounts')
    if not isinstance(mounts,list) or any(m.get('Type')!='tmpfs' or m.get('Destination')!='/work' or m.get('RW') is not True for m in mounts) or len(mounts)>1:
        raise Refused('Unexpected runtime mount')
    state=row.get('State',{})
    if state.get('Status') not in ('created','running','exited') or state.get('Running')!=(state.get('Status')=='running'):
        raise Refused('Container state is unknown')
    return {'id':identifier,'state':state,'networkMode':host['NetworkMode'],'mounts':mounts,
        'payloadSha256':local.digest(expected),'inspected':True}


def execute(root,state,intent,reader):
    engine=docker.DockerEngine(intent['docker']['endpoint'],docker.unix_request)
    journal=EffectJournal(state/'effects',intent)
    def effect(name,request,action):
        from openai_budget import fingerprint
        return journal.perform(name,request,lambda key,value:journal.receipt(key,fingerprint(value),action()))
    base=checked_image(engine,intent);write_owned(state,'base-image.json',onboarding.encode(base),exclusive=True)
    archive=input_archive(root,state,intent,reader);pin=local.digest(intent)
    stage_name='aim-flow-stage-'+pin[:24];run_name='aim-flow-job-'+pin[:24]
    staged=payload(intent,intent['image']['id'],pin,staging=True)
    def create(name,body):
        result=engine._call('POST','/containers/create?'+urlencode({'name':name}),(201,),body)
        return {'id':docker._container_id(result.get('Id'))}
    staged_id=effect('stage:create',{'name':stage_name,'payload':staged},lambda:create(stage_name,staged))['id']
    observed=inspect_container(engine,staged_id,stage_name,staged)
    if not observed or observed['state'].get('Status')!='created' or observed['state'].get('Pid')!=0:
        raise Refused('Staging is not a never-started inert container')
    argv=[intent['docker']['path'],'--host',intent['docker']['endpoint'],'container','cp','-',staged_id+':/']
    def upload():
        local.tool_identity({k:intent['docker'][k] for k in ('path','sha256')})
        raw=read_owned_content(state,'inputs.tar');package.check_digest(raw,archive['sha256'],'Staging archive')
        try:
            p=subprocess.run(argv,input=raw,cwd=state,env={'PATH':'/usr/bin:/bin','HOME':str(state),'DOCKER_CONFIG':str(state)},capture_output=True,timeout=120)
        except (OSError,subprocess.TimeoutExpired): raise Refused('Staging upload outcome unknown; do not resend') from None
        if p.returncode or len(p.stdout)+len(p.stderr)>10000:raise Refused('Staging upload did not confirm success')
        return {'exitCode':p.returncode,'archive':archive}
    effect('stage:upload',{'argv':argv,'archive':archive},upload)
    observed=inspect_container(engine,staged_id,stage_name,staged)
    if observed['state']['Status']!='created':raise Refused('Staging unexpectedly started')
    route='/commit?'+urlencode({'container':staged_id,'pause':'false'})
    derived=effect('stage:commit',{'containerId':staged_id,'archive':archive},lambda:engine._call('POST',route,(201,)))
    image=derived.get('Id')
    if not isinstance(image,str) or not re.fullmatch('sha256:[a-f0-9]{64}',image):raise Refused('Invalid derived image identity')
    derived_meta=engine._call('GET','/images/'+image+'/json',(200,))
    if derived_meta.get('RootFS',{}).get('Layers',[])[:-1]!=base['layers']:raise Refused('Derived image is not the staged base plus one layer')
    write_owned(state,'derived-image.json',onboarding.encode({'imageId':image,'base':base['imageId'],'archive':archive}),exclusive=True)
    effect('stage:remove',{'id':staged_id},lambda:engine.remove(staged_id))
    run=payload(intent,image,pin);identifier=effect('job:create',{'name':run_name,'payload':run},lambda:create(run_name,run))['id']
    initial=inspect_container(engine,identifier,run_name,run)
    if not initial or initial['state']['Status']!='created':raise Refused('Runtime not in created state')
    write_owned(state,'container-created.json',onboarding.encode(initial),exclusive=True)
    deadline=time.monotonic()+intent['timeoutSeconds']+30
    effect('job:start',{'id':identifier,'payloadSha256':local.digest(run)},lambda:engine.start(identifier))
    timed_out=False
    while True:
        final=inspect_container(engine,identifier,run_name,run)
        if not final:raise Refused('Runtime disappeared before evidence capture')
        if final['state']['Status']=='exited':break
        if time.monotonic()>=deadline:
            effect('job:kill',{'id':identifier},lambda:engine.kill(identifier));timed_out=True
            final=inspect_container(engine,identifier,run_name,run)
            if not final or final['state']['Status']!='exited':raise Refused('Deadline termination outcome unconfirmed')
            break
        time.sleep(.2)
    write_owned(state,'container-terminal.json',onboarding.encode(final),exclusive=True)
    logs=local.capture([intent['docker']['path'],'--host',intent['docker']['endpoint'],'logs',identifier],state,
        {'PATH':'/usr/bin:/bin','HOME':str(state),'DOCKER_CONFIG':str(state)},state,'container',30,intent['outputBytes']+1000)
    report=None;diagnostic=None
    try:report=unique_json(read_owned_content(state,'container.stdout'))
    except (ValueError,UnicodeError):diagnostic='No complete helper JSON report'
    result={'format':RESULT,'schemaVersion':1,'intentSha256':pin,'target':intent['target'],
        'status':'failed','ready':False,'acceptance':'not-established','deployment':'not-authorized',
        'job':intent['job']['id'],'coverage':intent['job']['coverage'],'baseImage':base['imageId'],'derivedImage':image,
        'containerId':identifier,'terminal':final,'deadlineExceeded':timed_out,'logs':logs,'report':report,
        'diagnostic':diagnostic,'review':intent['review'],'limitations':[intent['sourceBoundary'],
            'Container helper evidence is not independent product acceptance. No hosted CI, native Windows, browser or deployment qualification.',
            'Selected project code is reviewed, not treated as hostile. Same-UID project/helper execution is not an adversarial evidence trust boundary; inspector activation is disabled.',
            'Private tmpfs outputs cease at exit; only bounded report inventories/logs are retained. Derived image remains local for review.']}
    result['limitations'].append('Workspace is noexec: explicit image Node invocations work, but native binaries and shebang CLIs in node_modules/.bin cannot execute. This is not general frontend npm coverage.')
    if read_owned_content(state,'container.stderr') or len(read_owned_content(state,'container.stdout'))>intent['outputBytes']:
        result['diagnostic']='Unexpected helper stderr or oversized report'
    result['status']='passed' if passing_report(intent,result) else 'failed'
    effect('job:remove',{'id':identifier},lambda:engine.remove(identifier))
    if engine._call('GET','/containers/'+identifier+'/json',(200,404)) is not None:raise Refused('Job cleanup not confirmed')
    result['containerCleanup']='confirmed-absent'
    result['controlFiles']={n:package.sha256(read_owned_content(state,n)) for n in (
        'base-image.json','inputs.tar','derived-image.json','container-created.json','container-terminal.json','container.stdout','container.stderr',
        'effects/effects.json','effects/effects-established.json')}
    return result


def passing_report(intent,result):
    # The runner's detailed evidence is retained even for a failed qualification.
    report=result.get('report')
    expected_sources={n:{'sha256':r['sha256'],'mode':int(r['mode'],8)&0o777,'bytes':r['bytes']} for n,r in intent['sources'].items()}
    runtime=report.get('runtime',{}) if isinstance(report,dict) else {}
    return (isinstance(report,dict) and report.get('format')=='aim-flow-project-container-report' and report.get('schemaVersion')==1
        and report.get('intentSha256')==local.digest(intent)
        and report.get('status')=='passed' and report.get('qualificationPassed') is True
        and report.get('ready') is False and report.get('deployment')=='not-authorized' and report.get('acceptance')=='not-established'
        and report.get('sourcesBefore')==expected_sources and report.get('sourcesAfter')==expected_sources
        and runtime.get('actualNodeVersion')=='v'+intent['image']['nodeVersion'] and runtime.get('actualNpmVersion')==intent['image']['npmVersion']
        and runtime.get('nodePath')==NODE and runtime.get('npmPath')==NPM and runtime.get('helperSha256')==intent['helperSha256']
        and report.get('sourcesRestored') is True and len(report.get('stages',[]))==len(intent['job']['stages'])
        and all(r.get('exitCode')==0 and r.get('termination') is None and r.get('index')==i
            and r.get('projectArgv')==s['argv'] and r.get('root')==s['root'] and r.get('sourcesAfter')==expected_sources
            for i,(r,s) in enumerate(zip(report['stages'],intent['job']['stages'])))
        and result['terminal']['state'].get('ExitCode')==0 and result['terminal']['state'].get('OOMKilled') is False
        and not result['deadlineExceeded'] and result['logs']['exitCode']==0 and result['logs']['termination'] is None
        and result.get('diagnostic') is None)


def verify(state,intent,pin):
    raw=read_owned_content(state,'result.json');package.check_digest(raw,pin,'Container result')
    result=unique_json(raw)
    if unique_json(read_owned_content(state,'intent.json'))!=intent or result.get('intentSha256')!=local.digest(intent):raise Refused('Container intent changed')
    if result.get('status')!=('passed' if passing_report(intent,result) else 'failed'):raise Refused('Container status disagrees with evidence')
    for name,digest in result['controlFiles'].items():
        onboarding._source_path(name);package.check_digest(read_owned_content(state,name),digest,'Container evidence')
    try:retained_report=unique_json(read_owned_content(state,'container.stdout'))
    except (ValueError,UnicodeError):retained_report=None
    if retained_report!=result['report']:raise Refused('Retained helper report differs')
    if unique_json(read_owned_content(state,'container-terminal.json'))!=result['terminal']:raise Refused('Retained terminal observation differs')
    if EffectJournal(state/'receipt-journal',intent,read_only=True).completed_result('project-container',intent)!=result:raise Refused('Container receipt differs from journal')
    return {'status':result['status'],'receipt':str(state/'result.json'),'receiptSha256':pin,
        'intentSha256':local.digest(intent),'job':result['job'],'containerCleanup':result['containerCleanup'],
        'ready':False,'acceptance':'not-established','deployment':'not-authorized','report':result['report']}


def execution(root,args):
    intent,reader=prepare(root,args)
    if args.action=='verify-project-container-validation':return verify(args.state,intent,args.receipt_sha256)
    if args.preview:return {'status':'container-execution-preview','intentSha256':local.digest(intent),'intent':intent,'executionAuthorized':False}
    if args.authorize_intent!=local.digest(intent):raise Refused('Exact separate container execution grant required')
    with new_owned_directory(args.state):
        write_owned(args.state,'intent.json',onboarding.encode(intent),exclusive=True)
        journal=EffectJournal(args.state/'receipt-journal',intent)
        from openai_budget import fingerprint
        def dispatch(key,request):
            try:result=execute(root,args.state,intent,reader)
            except Exception as error:
                write_owned(args.state,'failure.json',onboarding.encode({'status':'unknown-incomplete-attempt','error':str(error),'replayAuthorized':False}),exclusive=True)
                raise
            return journal.receipt(key,fingerprint(request),result)
        result=journal.perform('project-container',intent,dispatch)
        raw=onboarding.encode(result);write_owned(args.state,'result.json',raw,exclusive=True)
    return verify(args.state,intent,package.sha256(raw))
