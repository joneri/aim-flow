"""Explicit, one-attempt project npm validation with retained process evidence.

This is a locally qualified macOS/offline execution adapter, not an approval
engine or portable adversarial sandbox. Model assessment text is never argv.
"""
import json
import os
from pathlib import Path
import selectors
import platform
import re
import signal
import stat
import subprocess
import sys
import time

from . import control_package as package, project_onboarding as onboarding, project_validation as planning
from .effects import EffectJournal
from .owned_paths import Refused, new_owned_directory, read_owned_content, safe_path, write_owned
from .project_discovery import CommitReader

SPEC = 'aim-flow-project-execution-spec'
INTENT = 'aim-flow-project-execution-intent'
RESULT = 'aim-flow-project-execution-result'


def digest(value):
    return package.sha256(onboarding.encode(value))


def tree_identity(root):
    """Pin complete npm/cache bytes and modes; only internal relative links."""
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise Refused('Runtime tree must be an existing canonical directory')
    safe_path(root)
    files = {}
    for path in sorted(root.rglob('*')):
        name = str(path.relative_to(root))
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            if os.path.isabs(target) or not path.resolve().is_relative_to(root) or not path.resolve().is_file():
                raise Refused('Runtime links must resolve to files within the pinned tree')
            files[name] = {'link': target}
        elif stat.S_ISREG(info.st_mode):
            files[name] = {'sha256': package.sha256(read_owned_content(root, name)), 'mode': stat.S_IMODE(info.st_mode)}
        elif not stat.S_ISDIR(info.st_mode):
            raise Refused('Unsupported runtime tree entry')
    return {'path': str(root), 'sha256': digest(files), 'files': files}


def tool_identity(spec):
    onboarding._keys(spec, ('path', 'sha256'), 'execution tool')
    path = Path(spec['path'])
    if not path.is_absolute() or path.resolve() != path or not os.access(path, os.X_OK):
        raise Refused('Execution tools require canonical executable paths')
    # External read-only system tools may be legitimate hard links (macOS
    # unzip/zipinfo). Keep owned state single-link, but pin these exact bytes.
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode): raise Refused('Execution tool is not a regular file')
        raw=stream.read(before.st_size+1);after=os.fstat(stream.fileno())
    if len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise Refused('Execution tool changed while read')
    package.check_digest(raw,spec['sha256'],'Execution tool')
    return dict(spec)


def source_selection(root, handoff, instance, names):
    onboarding._texts(names, 'execution source paths')
    if not names or len(set(names)) != len(names):
        raise Refused('Select distinct committed execution source files')
    for name in names: onboarding._source_path(name)
    target, git = handoff['target'], instance['packet']['git']
    reader = CommitReader(root, Path(target['checkout']), Path(git['path']), git['sha256'], target['commit'])
    entries = {name: (mode, kind, oid) for name, mode, kind, oid in reader.entries(reader.commit, *names)}
    result = {}
    for name in names:
        if name not in entries or entries[name][0] not in ('100644', '100755') or entries[name][1] != 'blob':
            raise Refused('Execution inputs must be exact committed regular files')
        mode, _, oid = entries[name]
        raw = reader.blob(oid)
        result[name] = {'gitObject': oid, 'sha256': package.sha256(raw), 'mode': mode, 'bytes': len(raw)}
    reader.verify_head()
    return result, reader


def prepare(root, args):
    handoff = planning.derive(root, args)
    _, spec = planning.pinned(args.execution_spec, args.execution_spec_sha256)
    onboarding._keys(spec, ('format', 'schemaVersion', 'job', 'sourcePaths', 'tools', 'npmTree', 'npmCache',
        'runtimeReview', 'sourceReview', 'writablePaths', 'writableFiles', 'sourceWriteExceptions', 'applicationEnvironment', 'utilities', 'timeoutSeconds', 'outputBytes'), 'execution spec')
    if spec['format'] != SPEC or type(spec['schemaVersion']) is not int or spec['schemaVersion'] != 1:
        raise Refused('Unsupported project execution spec')
    jobs = [job for job in handoff['jobs'] if job['id'] == spec['job']]
    if len(jobs) != 1 or jobs[0]['mode'] == 'hosted-reference' or jobs[0]['needs']:
        raise Refused('Select a local job with no unresolved job dependencies')
    job = jobs[0]
    for key in ('runtimeReview', 'sourceReview'):
        onboarding._texts(spec[key], key)
        if not spec[key]: raise Refused('Execution needs separate runtime and source/effect reviews')
    for key in ('timeoutSeconds', 'outputBytes'):
        if type(spec[key]) is not int or spec[key] <= 0:
            raise Refused('Execution limits must be explicit positive integers')
    onboarding._keys(spec['tools'], ('node', 'git', 'npm', 'sandbox'), 'execution tools')
    tools = {key: tool_identity(value) for key, value in spec['tools'].items()}
    if tools['sandbox']['path'] != '/usr/bin/sandbox-exec' or sys.platform != 'darwin':
        raise Refused('Project execution currently requires the qualified macOS sandbox-exec adapter')
    onboarding._keys(spec['npmTree'], ('path', 'sha256'), 'npm tree')
    npm = tree_identity(spec['npmTree']['path'])
    if npm['sha256'] != spec['npmTree']['sha256'] or not Path(tools['npm']['path']).is_relative_to(Path(npm['path'])):
        raise Refused('npm entry point must belong to the complete pinned npm runtime tree')
    if not isinstance(spec['utilities'],dict): raise Refused('Utilities must be explicitly named tool pins')
    utilities={}
    for name,value in spec['utilities'].items():
        if not re.fullmatch('[a-z][a-z0-9_-]*',name) or name in tools: raise Refused('Invalid utility tool name')
        utilities[name]=tool_identity(value)
    application=spec['applicationEnvironment']
    if not isinstance(application,dict): raise Refused('Invalid application environment')
    for name,value in application.items():
        if (not re.fullmatch('[A-Z][A-Z0-9_]*',name) or not isinstance(value,str) or '\0' in value
            or name in ('PATH','HOME','TMPDIR','TMP','TEMP','SHELL','ENV','BASH_ENV','ZDOTDIR','XDG_CONFIG_HOME','LANG','LC_ALL')
            or name.startswith(('NODE_','NPM_','GIT_','DYLD_','LD_','PYTHON','FLOW_'))):
            raise Refused('Application settings must not override runtime or credential configuration')
    cache = None
    if spec['npmCache'] is not None:
        onboarding._keys(spec['npmCache'], ('path', 'sha256'), 'npm cache')
        cache = tree_identity(spec['npmCache']['path'])
        if cache['sha256'] != spec['npmCache']['sha256'] or any('link' in v for v in cache['files'].values()):
            raise Refused('npm cache requires pinned regular-file content only')
    _, instance = planning.pinned(args.instance, args.instance_sha256)
    sources, reader = source_selection(root, handoff, instance, spec['sourcePaths'])
    config_paths={'.npmrc'}
    for declared_root in handoff['packageRoots']:
        directory=Path(declared_root)
        config_paths.update(str(p/'.npmrc') for p in (directory,*directory.parents) if str(p)!='.')
    if list(reader.entries(reader.commit,*sorted(config_paths))):
        raise Refused('Committed project .npmrc requires a separate configuration adapter review; it cannot be silently omitted')
    if any(Path(p).name=='.npmrc' for p in sources):
        raise Refused('Project npm config cannot silently replace reviewed lifecycle settings')
    for stage in job['stages']:
        package_root = handoff['packageRoots'][stage['root']]
        required = [package_root['manifest']]
        if stage['operation'] == 'ci': required.append(package_root['lockfile'])
        if not set(required) <= sources.keys():
            raise Refused('Execution selection must include each stage manifest and install lock')
    onboarding._texts(spec['writablePaths'], 'writable output paths')
    onboarding._texts(spec['writableFiles'],'writable output files')
    writable = spec['writablePaths']+spec['writableFiles']
    if len(set(writable)) != len(writable): raise Refused('Duplicate writable output paths')
    for name in writable:
        onboarding._source_path(name)
        if any(Path(p).is_relative_to(name) or Path(name).is_relative_to(p) for p in sources):
            raise Refused('Writable outputs must not overlap committed execution inputs')
    for i,name in enumerate(writable):
        if any(Path(other).is_relative_to(name) or Path(name).is_relative_to(other) for other in writable[i+1:]):
            raise Refused('Output scopes must not overlap')
    source_exceptions=spec['sourceWriteExceptions']
    onboarding._texts(source_exceptions,'temporarily writable exact source paths')
    if len(set(source_exceptions))!=len(source_exceptions) or not set(source_exceptions)<=sources.keys():
        raise Refused('Source write exceptions require exact selected source files')
    state = onboarding._outside(args.state, root, Path(handoff['target']['checkout']))
    onboarding._outside(state, args.instance_controller, Path(handoff['target']['checkout']))
    for path in [args.execution_spec, args.declaration, args.instance, *[Path(v['path']) for v in (*tools.values(),*utilities.values())],
                 Path(npm['path']), *([Path(cache['path'])] if cache else [])]:
        if path.is_relative_to(state) or state.is_relative_to(path):
            raise Refused('Execution state must be separate from pinned inputs and tools')
    intent = {'format': INTENT, 'schemaVersion': 1, 'controllerManifestSha256': args.manifest_sha256,
        'handoffSha256': digest(handoff), 'executionSpecSha256': args.execution_spec_sha256,
        'target': handoff['target'], 'state': str(state), 'job': job, 'sources': sources,
        'tools': tools, 'utilities':utilities,'applicationEnvironment':application,'sourceWriteExceptions':source_exceptions, 'npmTree': {'path': npm['path'], 'sha256': npm['sha256']},
        'npmCache': None if cache is None else {'path': cache['path'], 'sha256': cache['sha256']},
        'runtimeReview': spec['runtimeReview'], 'sourceReview': spec['sourceReview'],
        'writablePaths': spec['writablePaths'], 'writableFiles':spec['writableFiles'], 'timeoutSeconds': spec['timeoutSeconds'], 'outputBytes': spec['outputBytes'],
        'host': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(), 'macOS': platform.mac_ver()[0]},
        'network': 'deny-all; npm offline mode', 'deviceWriteException': '/dev/null data only',
        'sourceWriteExceptionEffect': 'Temporary data, metadata, create/delete/replace authority on exact copied files; final regular single-link bytes and mode must be restored. No atomic immutability claim.',
        'readBoundary': 'Reads are not comprehensively confined; selected code must be reviewed.',
        'environment': 'Private HOME/TMP/cache/config, pinned tools on PATH, no inherited credentials; normal npm hooks.',
        'qualification': 'Parent and descendant network/write denial plus Git fixture operations before project execution.',
        'excludedCoverage': handoff['excludedCoverage'], 'agentUnknowns': handoff['agentUnknowns'], 'review': handoff['review'],
        'authority': 'Exact separate operator grant required; never model authority or deployment approval.'}
    return intent, reader, npm, cache


def profile(state, intent):
    work, project = state/'work', state/'project'
    return '\n'.join(['(version 1)', '(allow default)', '(deny network*)', '(deny file-write*)',
        '(allow file-write-data (literal "/dev/null"))',
        '(allow file-write* (subpath ' + json.dumps(str(work)) + '))',
        *['(allow file-write* (subpath ' + json.dumps(str(project/p)) + '))' for p in intent['writablePaths']],
        *['(allow file-write* (literal ' + json.dumps(str(project/p)) + '))' for p in intent['writableFiles']],
        *['(allow file-write* (literal ' + json.dumps(str(project/p)) + '))' for p in intent['sourceWriteExceptions']]]) + '\n'


PROBE = r'''const fs=require('node:fs'),net=require('node:net'),cp=require('node:child_process'),path=require('node:path'),assert=require('node:assert/strict');
async function one(label){const result={label};for(const [name,file] of Object.entries(JSON.parse(process.env.FLOW_PROTECTED))){try{let fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_APPEND);fs.closeSync(fd);throw Error('writable '+name);}catch(e){assert.equal(e.code,'EPERM');result[name]=e.code;}}
result.sourceExceptions=0;for(const file of JSON.parse(process.env.FLOW_SOURCE_EXCEPTIONS)){const before=fs.statSync(file);const bytes=fs.readFileSync(file);fs.appendFileSync(file,'probe');fs.truncateSync(file,bytes.length);fs.utimesSync(file,before.atime,new Date(2000000000000));fs.utimesSync(file,before.atime,before.mtime);assert.deepEqual(fs.readFileSync(file),bytes);result.sourceExceptions++;}const fd=fs.openSync('/dev/null','r+');assert.equal(fs.writeSync(fd,Buffer.from('probe')),5);assert.equal(fs.readSync(fd,Buffer.alloc(1),0,1,null),0);fs.closeSync(fd);result.null='read/write';result.network=await new Promise((yes,no)=>{const s=net.connect({host:'127.0.0.1',port:9});s.on('connect',()=>{s.destroy();no(Error('network allowed'));});s.on('error',e=>{try{assert.equal(e.code,'EPERM');yes(e.code);}catch(x){no(x);}});s.setTimeout(2000,()=>{s.destroy();no(Error('probe timeout'));});});return result;}
(async()=>{const rows=[await one(process.argv[2]||'parent')];if(!process.argv[2]){const c=cp.spawnSync(process.execPath,[__filename,'child'],{encoding:'utf8'});assert.equal(c.status,0,c.stderr);rows.push(...JSON.parse(c.stdout));const g=cp.spawnSync('/usr/bin/which',['git'],{encoding:'utf8'});assert.equal(g.status,0);assert.equal(fs.realpathSync(g.stdout.trim()),process.env.FLOW_GIT);const dir=path.join(process.env.TMPDIR,'git-probe');fs.mkdirSync(dir);for(const a of [['init','-b','main'],['config','user.name','Fixture'],['config','user.email','fixture@example.invalid'],['commit','--allow-empty','-m','probe']]){const p=cp.spawnSync('git',a,{cwd:dir,encoding:'utf8'});assert.equal(p.status,0,p.stderr);}fs.rmSync(dir,{recursive:true});rows.push({git:'passed',node:process.version});}console.log(JSON.stringify(rows));})().catch(e=>{console.error(e);process.exit(1);});
'''


def capture(command, cwd, environment, state, prefix, seconds, output_limit):
    """Bound logs and elapsed time, kill the group even if its leader exited."""
    started = time.monotonic(); deadline = started + seconds; total = 0; reason = None
    paths = {key: prefix+'.'+key for key in ('stdout', 'stderr')}
    streams = {key: (state/name).open('xb') for key, name in paths.items()}
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            for key in streams: selector.register(getattr(process, key), selectors.EVENT_READ, key)
            while selector.get_map():
                remaining = deadline-time.monotonic()
                if remaining <= 0: reason='timeout'; break
                for event, _ in selector.select(remaining):
                    chunk = os.read(event.fileobj.fileno(), 8192)
                    if not chunk: selector.unregister(event.fileobj); continue
                    retained = chunk[:max(0, output_limit-total)]
                    streams[event.data].write(retained); total += len(chunk)
                    if total > output_limit: reason='output-limit'; break
                if reason: break
            if not reason:
                try: process.wait(timeout=max(.001,deadline-time.monotonic()))
                except subprocess.TimeoutExpired: reason='timeout'
    finally:
        if process is not None:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
            for key in streams: getattr(process,key).close()
        for stream in streams.values(): stream.close()
    return {'argv':command, 'exitCode':process.returncode, 'termination':reason,
        'elapsedSeconds':time.monotonic()-started,
        'logs':{key:{'path':name,'sha256':package.sha256(read_owned_content(state,name))} for key,name in paths.items()}}


def output_inventory(state,intent):
    """Record regular output bytes without traversing links or special files."""
    roots=[state/'project'/p for p in (*intent['writablePaths'],*intent['writableFiles'])]+[state/'work']
    result={}
    def visit(path):
        name=str(path.relative_to(state))
        try: info=path.lstat()
        except FileNotFoundError: result[name]={'type':'absent'};return
        mode=stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode): result[name]={'type':'link','target':os.readlink(path),'mode':mode};return
        if stat.S_ISDIR(info.st_mode):
            result[name]={'type':'directory','mode':mode}
            for child in sorted(path.iterdir()):visit(child)
        elif stat.S_ISREG(info.st_mode):
            # npm may hard-link its own private installed binaries. Do not follow
            # links at open or assume that output hard links establish provenance.
            import hashlib
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                before=os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode): raise Refused('Output type changed during inventory')
                pin=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
                raise Refused('Output changed during inventory')
            result[name]={'type':'file','mode':mode,'bytes':after.st_size,'sha256':pin,'links':after.st_nlink}
        else: result[name]={'type':'special','mode':mode}
    for path in roots:visit(path)
    return result


def execute(state, intent, reader, npm, cache):
    work, project = state/'work', state/'project'
    for name in ('project','bin','empty-hooks','empty-template','work/home','work/tmp','work/npm-cache'):
        (state/name).mkdir(parents=True,exist_ok=True)
    for name, source in intent['sources'].items():
        raw=reader.blob(source['gitObject']);package.check_digest(raw,source['sha256'],'Committed execution source')
        path=project/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        path.chmod(0o755 if source['mode']=='100755' else 0o644)
    for name in intent['writablePaths']: (project/name).mkdir(parents=True,exist_ok=True)
    for name in intent['writableFiles']: (project/name).parent.mkdir(parents=True,exist_ok=True)
    if cache:
        for name in cache['files']:
            target=work/'npm-cache'/name;target.parent.mkdir(parents=True,exist_ok=True)
            raw=read_owned_content(Path(cache['path']),name)
            package.check_digest(raw,cache['files'][name]['sha256'],'Pinned cache source');target.write_bytes(raw)
    path_tools={**{k:intent['tools'][k] for k in ('node','git','npm')},**intent['utilities']}
    for name,tool in path_tools.items(): (state/'bin'/name).symlink_to(tool['path'])
    for name in ('npm-user.npmrc','npm-global.npmrc','git-global.config'): write_owned(state,name,b'',exclusive=True)
    write_owned(state,'validation.sb',profile(state,intent),exclusive=True)
    write_owned(state,'probe.cjs',PROBE,exclusive=True)
    immutable=next((p for p in intent['sources'] if p not in intent['sourceWriteExceptions']),None)
    if immutable is None: raise Refused('Retain an immutable source for containment qualification')
    protected={'source':str(project/immutable),'profile':str(state/'validation.sb'),
               'evidence':str(state/'forbidden-evidence-write'),'outside':str(state)+'.outside-probe',
               'checkout':str(Path(intent['target']['checkout'])/next(iter(intent['sources']))),
               'controller':str(Path(__file__).resolve())}
    for index,name in enumerate(intent['sourceWriteExceptions']):
        protected['originalException'+str(index)]=str(Path(intent['target']['checkout'])/name)
        protected['exceptionSibling'+str(index)]=str(project/name)+'.unapproved-write-probe'
    if any(Path(p).exists() for k,p in protected.items() if k in ('evidence','outside')):
        raise Refused('Qualification probe targets unexpectedly exist')
    env={'PATH':str(state/'bin')+':/usr/bin:/bin','HOME':str(work/'home'),'XDG_CONFIG_HOME':str(work/'home'/'.config'),
        'TMPDIR':str(work/'tmp'),'LANG':'C','LC_ALL':'C','NODE_DISABLE_COMPILE_CACHE':'1',
        'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':str(state/'git-global.config'),'GIT_TEMPLATE_DIR':str(state/'empty-template'),
        'GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':str(state/'empty-hooks'),
        'GIT_CONFIG_KEY_1':'commit.gpgsign','GIT_CONFIG_VALUE_1':'false','GIT_TERMINAL_PROMPT':'0',
        'npm_config_userconfig':str(state/'npm-user.npmrc'),'npm_config_globalconfig':str(state/'npm-global.npmrc'),
        'npm_config_cache':str(work/'npm-cache'),'npm_config_offline':'true','npm_config_audit':'false','npm_config_fund':'false',
        'npm_config_update_notifier':'false','FLOW_PROTECTED':json.dumps(protected),'FLOW_GIT':intent['tools']['git']['path'],
        'FLOW_SOURCE_EXCEPTIONS':json.dumps([str(project/p) for p in intent['sourceWriteExceptions']])}
    env.update(intent['applicationEnvironment'])
    write_owned(state,'environment.json',onboarding.encode(env),exclusive=True)
    frozen={name:package.sha256(read_owned_content(state,name)) for name in
        ('intent.json','validation.sb','probe.cjs','environment.json','npm-user.npmrc','npm-global.npmrc','git-global.config')}
    def integrity():
        return (all(package.sha256(read_owned_content(project,n))==v['sha256'] and stat.S_IMODE((project/n).lstat().st_mode)==(0o755 if v['mode']=='100755' else 0o644) for n,v in intent['sources'].items())
            and all(os.readlink(state/'bin'/name)==tool['path'] for name,tool in path_tools.items())
            and all(package.sha256(read_owned_content(state,n))==h for n,h in frozen.items())
            and all(tool_identity(t)==t for t in (*intent['tools'].values(),*intent['utilities'].values()))
            and tree_identity(npm['path'])==npm
            and not Path(protected['outside']).exists() and not Path(protected['evidence']).exists())
    null=os.stat('/dev/null')
    if not stat.S_ISCHR(null.st_mode): raise Refused('The null device is not a character device')
    prefix=[intent['tools']['sandbox']['path'],'-f',str(state/'validation.sb'),intent['tools']['node']['path']]
    qualification=capture([*prefix,str(state/'probe.cjs')],project,env,state,'qualification',min(30,intent['timeoutSeconds']),intent['outputBytes'])
    rows=[]
    good=qualification['exitCode']==0 and qualification['termination'] is None and integrity()
    if good:
        data=json.loads(read_owned_content(state,'qualification.stdout'))
        good=(len(data)==3 and [r.get('label') for r in data[:2]]==['parent','child']
            and all(all(r.get(k)=='EPERM' for k in (*protected,'network')) and r.get('null')=='read/write' and r.get('sourceExceptions')==len(intent['sourceWriteExceptions']) for r in data[:2])
            and data[2].get('git')=='passed')
    if good:
        for index, stage in enumerate(intent['job']['stages']):
            if not integrity(): raise Refused('Inputs changed before a project stage; no further execution')
            command=[*prefix,intent['tools']['npm']['path'],*stage['argv'][1:]]
            before_outputs=output_inventory(state,intent)
            write_owned(state,'stage-'+str(index)+'-started.json',onboarding.encode({'index':index,'outputsBefore':before_outputs,'argv':command,'workingDirectory':stage['root'],'intentSha256':digest(intent),'status':'started-outcome-unknown-until-receipt'}),exclusive=True)
            result=capture(command,project/stage['root'],env,state,'stage-'+str(index),intent['timeoutSeconds'],intent['outputBytes'])
            result.update(index=index,projectArgv=stage['argv'],workingDirectory=stage['root'],integrity=integrity(),outputsAfter=output_inventory(state,intent))
            rows.append(result)
            write_owned(state,'stage-'+str(index)+'.json',onboarding.encode(result),exclusive=True)
            if result['exitCode'] or result['termination'] or not result['integrity']: break
    passed=(good and len(rows)==len(intent['job']['stages']) and all(r['exitCode']==0 and r['termination'] is None and r['integrity'] for r in rows))
    return {'format':RESULT,'schemaVersion':1,'status':'passed' if passed else 'failed','intentSha256':digest(intent),
        'target':intent['target'],'job':intent['job']['id'],'coverage':intent['job']['coverage'],
        'qualification':qualification,'qualificationPassed':good,'stages':rows,
        'unrunStages':list(range(len(rows),len(intent['job']['stages']))),'ready':False,
        'acceptance':'not-established','hostedCi':'not-run','deployment':'not-authorized',
        'outputs':output_inventory(state,intent),
        'controlFiles':{name:package.sha256(read_owned_content(state,name)) for name in (*frozen,*['stage-'+str(i)+suffix for i in range(len(rows)) for suffix in ('.json','-started.json')])},
        'remainingUnknowns':intent['agentUnknowns'],'review':intent['review'],
        'limitations':['Local process execution only; passing exit status is not independent test or product acceptance.',
            'No comprehensive read sandbox, portable sandbox qualification, hosted CI, Windows or deployment.',
            'Output directories and work state are retained for review; project commands may leave temporary files.']}


def verify_result(state, intent, expected=None):
    raw=read_owned_content(state,'result.json')
    if expected is not None: package.check_digest(raw,expected,'Project execution receipt')
    result=json.loads(raw)
    retained=json.loads(read_owned_content(state,'intent.json'))
    if retained!=intent or result.get('intentSha256')!=digest(intent): raise Refused('Retained execution intent differs')
    for name,source in intent['sources'].items():
        package.check_digest(read_owned_content(state/'project',name),source['sha256'],'Retained committed execution source')
        if stat.S_IMODE((state/'project'/name).lstat().st_mode)!=(0o755 if source['mode']=='100755' else 0o644):
            raise Refused('Retained source mode changed')
    if result.get('outputs')!=output_inventory(state,intent): raise Refused('Retained project outputs or work residue changed')
    journal=EffectJournal(state,intent,read_only=True)
    if journal.completed_result('project-validation',intent)!=result: raise Refused('Execution result differs from journal')
    for name,pin in result['controlFiles'].items():
        onboarding._source_path(name)
        package.check_digest(read_owned_content(state,name),pin,'Execution control/evidence')
    for row in [result['qualification'],*result['stages']]:
        for item in row['logs'].values():
            onboarding._source_path(item['path'])
            package.check_digest(read_owned_content(state,item['path']),item['sha256'],'Execution log')
    return {'status':result['status'],'receipt':str(state/'result.json'),'receiptSha256':package.sha256(raw),
        'intentSha256':digest(intent),'target':result['target'],'job':result['job'],'stages':result['stages'],
        'qualificationPassed':result['qualificationPassed'],'ready':False,'acceptance':'not-established','deployment':'not-authorized'}


def execution(root,args):
    try:
        intent,reader,npm,cache=prepare(root,args)
        if args.action=='verify-project-validation-run': return verify_result(args.state,intent,args.receipt_sha256)
        if args.preview: return {'status':'execution-preview','intentSha256':digest(intent),'intent':intent,'executionAuthorized':False}
        if args.authorize_intent!=digest(intent): raise Refused('Execution requires the exact separately approved preview intent')
        with new_owned_directory(args.state):
            write_owned(args.state,'intent.json',onboarding.encode(intent),exclusive=True)
            journal=EffectJournal(args.state,intent)
            # EffectJournal uses its own canonical fingerprint for the receipt protocol.
            from openai_budget import fingerprint
            def dispatch_bound(key,request):
                try:
                    result=execute(args.state,request,reader,npm,cache)
                except Exception as error:
                    write_owned(args.state,'failure.json',onboarding.encode({'status':'unknown-incomplete-attempt','failureClass':type(error).__name__,'diagnostic':str(error),'replayAuthorized':False}),exclusive=True)
                    raise
                return EffectJournal.receipt(key,fingerprint(request),result)
            result=journal.perform('project-validation',intent,dispatch_bound)
            write_owned(args.state,'result.json',onboarding.encode(result),exclusive=True)
        return verify_result(args.state,intent)
    except (KeyError,TypeError,AttributeError,UnicodeError) as error:
        raise Refused('Malformed project execution metadata: '+type(error).__name__) from None
