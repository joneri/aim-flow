"""Native Codex implementation/review/test/evidence loop and delivery provenance.

Only explicit project recipes run, in a committed private copy. No Responses API,
API accounting fiction, automatic publication, or replay of unknown effects.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import flow_api_delivery as api
import flow_candidate_review as review
import flow_codex_delivery as cli
import flow_codex_stage as model
import flow_delivery_backend as backend
import flow_onboarding_delivery as shape
import flow_requirement_intake as intake
import flow_workflow as work
from aim_ci import control_package, project_command as commands
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file, owned_mkdir
from aim_ci.project_discovery import CommitReader, unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-codex-local-run'
obj, arr, string = shape.object_schema, shape.array_schema, shape.string_schema
SOURCE_SCHEMA = obj({**review.finding_schema(review.SCHEMA, review.FINDING_DISPOSITION_POLICY)['properties'],
    'selectedTests': arr(string(100), 128), 'testRationale': string(3000),
    'executionGate': {'type':'string','enum':['ready-for-granted-tests','blocked-before-tests']},
    'executionGateRationale': string(2000)})
IMPLEMENTATION_SCHEMA = api.PATCH_SCHEMA
EVIDENCE_SCHEMA = obj({'summary': string(3000), 'limitations': arr(string(1600), 32),
    'findings': review.finding_schema(review.SCHEMA, review.FINDING_DISPOSITION_POLICY)['properties']['findings'],
    'coverage': arr(obj({'requirementId': string(80),
        'status': {'type':'string','enum':['satisfied','failed','uncertain','pending-delivery']},
        'analysis': string(2000), 'evidenceIds': arr(string(1100), 128)}), 32, 1),
    'priorDispositions': arr(obj({'id':string(100),
        'status':{'type':'string','enum':['resolved','pending-delivery','nonblocking','unresolved']},
        'analysis':string(2000),'evidenceIds':arr(string(1100),128)}),64)})


def pending_source_items(source_result):
    return ([{'id':'source-unknown-'+str(i+1),'description':text} for i,text in enumerate(source_result['unknowns'])]
        + [{'id':'source-coverage-'+r['requirementId'],'description':r['analysis']}
            for r in source_result['coverage'] if r['status']=='unverified'])


def source_gate(source_result, gate, rationale):
    blockers=[x['id']+': '+x['description'] for x in review.blocking_findings(source_result)]
    blockers += [r['requirementId']+': '+r['analysis'] for r in source_result['coverage'] if r['status']=='missing']
    if gate!='ready-for-granted-tests':blockers.append(rationale)
    return {'policy':'source-gate-before-execution-v1','status':'blocked' if blockers else 'ready-for-granted-tests',
        'blockers':blockers,'rationale':rationale,'pendingVerification':pending_source_items(source_result)}


def saved(path): return unique_json(api.file_bytes(path))
def pin(path): return work.pin(path)
def keep(state, name, value): return work.retain(state, name, value)
def require(ok, text):
    if not ok: raise Refused(text)


def reader(c, target=None):
    source = c['source'] if target is None else {**c['source'], 'checkout': target['checkout'], 'commit': target['commit']}
    return CommitReader(ROOT, Path(source['checkout']), Path(source['git']), source['gitSha256'], source['commit'])


def blobs(c, target=None):
    r = reader(c, target); files = {}
    for path, mode, kind, oid in r.entries(r.commit, recursive=True):
        shape.onboarding._source_path(path)
        require(kind == 'blob' and mode in ('100644','100755'), 'Codex candidate requires regular committed files')
        files[path] = (mode, r.blob(oid))
    r.verify_head()
    return files


def tracked(c, target):
    r = reader(c, target); result = {}
    for path, (mode, raw) in blobs(c, target).items():
        actual = api.file_bytes(Path(target['checkout'])/path)
        require(actual == raw, 'Tracked test/candidate source changed: '+path)
        require(bool((Path(target['checkout'])/path).stat().st_mode & 0o111) == (mode=='100755'),
                'Tracked test/candidate mode changed: '+path)
        result[path] = {'mode':mode,'sha256':api.digest(raw)}
    index = {}
    for entry in r.read('ls-files','--stage','-z').split(b'\0'):
        if not entry: continue
        head, path = entry.split(b'\t',1); mode, oid, stage = head.decode().split()
        require(stage == '0','Unmerged private test index')
        index[path.decode()] = (mode,oid)
    expected = {path:(mode,oid) for path,mode,kind,oid in r.entries(r.commit,recursive=True)}
    require(index == expected,'Private candidate/test index changed')
    r.verify_head()
    return result


def catalog(c, files):
    value = unique_json(api.pinned(c['testCatalog']))
    shape.onboarding._keys(value, ('schemaVersion','stages','requiredTests','maximumTests','minimumTests'), 'test catalog')
    require(value['schemaVersion']==1 and type(value['schemaVersion']) is int, 'Unknown test catalog')
    require(type(value['maximumTests']) is int and type(value['minimumTests']) is int
            and 0 <= value['minimumTests'] <= value['maximumTests'], 'Explicit test selection bounds required')
    require(isinstance(value['stages'],list),'Project test stages required')
    seen = {}
    for stage in value['stages']:
        shape.onboarding._keys(stage, ('id','kind','root','tool','arguments','needs','definitionSources','artifacts'), 'test stage')
        require(isinstance(stage['id'],str) and re.fullmatch(r'[a-z][a-z0-9._-]*',stage['id'])
                and stage['id'] not in seen, 'Distinct project test stage ids required')
        require(stage['kind'] in ('install','test','build'), 'Only explicitly reviewed local test/build/install recipes')
        shape.onboarding._source_path(stage['root']) if stage['root'] != '.' else None
        require(stage['tool'] in c['tools'],'Test stage tool is not pinned')
        for name in ('arguments','needs','definitionSources'):
            shape.onboarding._texts(stage[name],name)
        require(set(stage['needs']) <= seen.keys(), 'Test dependencies must be earlier exact catalog stages')
        require(stage['definitionSources'],'Project recipes need committed process definitions')
        for path in stage['definitionSources']:
            shape.onboarding._source_path(path)
            require(path in files and not intake.SECRET_PATH.search(path), 'Missing/unsafe project recipe definition')
        require(isinstance(stage['artifacts'],list),'Exact artifact paths required')
        for artifact in stage['artifacts']:
            shape.onboarding._keys(artifact,('path','mediaType'),'test artifact')
            shape.onboarding._source_path(artifact['path'])
            require(artifact['mediaType'] in ('image/png','application/json') and artifact['path'] not in files
                and '.git' not in Path(artifact['path']).parts and not intake.SECRET_PATH.search(artifact['path']),
                'Only explicit untracked PNG/JSON outputs may be captured')
        require(stage['root']=='.' or any(p.startswith(stage['root']+'/') for p in files),
                'Project recipe working directory is absent from committed source')
        seen[stage['id']] = stage
    require(isinstance(value['requiredTests'],list) and set(value['requiredTests']) <= seen.keys(), 'Unknown required test')
    require(all(seen[x]['kind']=='test' for x in value['requiredTests']), 'Required tests must be test stages')
    return value


def prepare(config_path, config_sha, state):
    c = unique_json(api.pinned({'path':str(config_path),'sha256':config_sha}))
    shape.onboarding._keys(c, ('format','schemaVersion','manifestSha256','delivery','codex','model','reasoning',
        'source','requirement','allowedDirectories','dispatchDirectory','timeoutSeconds','testTimeoutSeconds',
        'maximumRounds','maximumCodexTurns','testCatalog','tools','environment','outputBytes','artifactBytes',
        'runtimeReview','python',*(('intakeLimits',) if 'intakeLimits' in c else ()),
        *(('reviewMaxSourceBytes',) if 'reviewMaxSourceBytes' in c else ()),
        *(('localGitTimeoutSeconds',) if 'localGitTimeoutSeconds' in c else ()),
        *(('resumeCandidate',) if 'resumeCandidate' in c else ())), 'Codex workflow')
    require(c['format']==FORMAT and type(c['schemaVersion']) is int and c['schemaVersion']==1,'Unknown Codex workflow')
    shape.onboarding._keys(c['source'], ('checkout','commit','git','gitSha256'), 'native committed source')
    require(c['delivery']=={'backend':'codex-cli','executionHost':'local'},'Native Codex run requires explicit local backend')
    control_package.verify_installation(ROOT,c['manifestSha256']); cli.tool(c['codex']); cli.tool(c['python'])
    require(isinstance(c['model'],str) and re.fullmatch(r'[a-zA-Z0-9._-]+',c['model'])
        and c['reasoning'] in ('low','medium','high','xhigh','max','ultra'),'Explicit Codex model/reasoning required')
    for key in ('maximumRounds','maximumCodexTurns','outputBytes','artifactBytes'):
        require(type(c[key]) is int and c[key]>0,'Positive explicit workflow limit required: '+key)
    for key in ('timeoutSeconds','testTimeoutSeconds','localGitTimeoutSeconds'):
        require(c.get(key) is None or type(c[key]) is int and c[key]>0,'Timeout must be null or explicitly positive')
    require(isinstance(c['tools'],dict) and c['tools'],'Explicit project tools required')
    for name,spec in c['tools'].items():
        require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*',name),'Invalid tool alias')
        commands.tool_identity(spec)
    require(isinstance(c['environment'],dict),'Explicit test environment required')
    for name,value in c['environment'].items():
        require(isinstance(name,str) and re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*',name)
            and name not in ('PATH','HOME','BASH_ENV','ENV','ZDOTDIR','GIT_EXEC_PATH')
            and not name.upper().startswith(('DYLD_','LD_','NODE_','PYTHON','GIT_CONFIG_','NPM_CONFIG_'))
            and not re.search(r'(?:KEY|TOKEN|SECRET|PASSWORD)',name,re.I)
            and isinstance(value,str) and '\0' not in value, 'Unsafe test environment')
    require(isinstance(c['runtimeReview'],str) and c['runtimeReview'].strip(),'Reviewed host effects required')
    state = api.canonical_path(state); ledger = api.canonical_path(c['dispatchDirectory'])
    roots = [state,ledger,ROOT,api.canonical_path(c['source']['checkout'])]
    for i,a in enumerate(roots):
        require(not any(a.is_relative_to(b) or b.is_relative_to(a) for b in roots[i+1:]),'Workflow roots overlap')
    for p in [config_path,c['requirement']['path'],c['testCatalog']['path'],c['codex']['path'],c['python']['path'],
              c['source']['git'],*[v['path'] for v in c['tools'].values()]]:
        p=api.canonical_path(p)
        require(not p.is_relative_to(state) and not p.is_relative_to(ledger),'Workflow input overlaps mutable state')
    report = intake.derive(ROOT,c['source'],c['requirement'],c['allowedDirectories'],limits=c.get('intakeLimits'))
    require(report['status']=='selected','Source discovery incomplete before account usage: '+', '.join(report['incompletePaths']))
    files=blobs(c); tests=catalog(c,files)
    if 'resumeCandidate' in c:
        resumed=c['resumeCandidate']
        shape.onboarding._keys(resumed,('localRun','deferredSourceItems','rationale'),'pending-execution continuation')
        require('controller' in resumed['localRun'],'Continuation requires the exact prior installed verifier')
        prior,prior_config,_,_=verified_run(resumed['localRun'],c['manifestSha256'],allow_blocked=True)
        for key in ('delivery','codex','model','reasoning','source','requirement','allowedDirectories',
                    'dispatchDirectory','testCatalog','tools','environment','runtimeReview','python'):
            require(c[key]==prior_config[key],'Continuation changed prior authority/input: '+key)
        require(prior['status']=='codex-review-blocked' and not prior['localFeatureAccepted']
            and 'execution' not in prior['rounds'][-1], 'Only a verified pre-execution stop can continue without replay')
        old_source=unique_json(api.pinned(prior['rounds'][-1]['sourceReview']))
        require(not review.blocking_findings(old_source) and all(x['status']!='missing' for x in old_source['coverage']),
                'Concrete source defects cannot be waived by pending-execution continuation')
        require('sourceGate' not in old_source or old_source['sourceGate']['status']=='ready-for-granted-tests',
                'An explicitly blocked execution safety gate cannot be waived by this continuation')
        pending={x['id'] for x in pending_source_items(old_source)}
        require(isinstance(resumed['deferredSourceItems'],list) and set(resumed['deferredSourceItems'])==pending
            and len(resumed['deferredSourceItems'])==len(pending) and isinstance(resumed['rationale'],str) and resumed['rationale'].strip(),
            'Continuation must explicitly bind every deferred verification item and rationale')
        require(c['maximumRounds']==1 and c['maximumCodexTurns'] <= prior_config['maximumCodexTurns']-len(prior['modelStages']),
                'Continuation cannot enlarge the prior Codex turn allowance or start new repair rounds')
        old_state=api.canonical_path(resumed['localRun']['state'])
        require(not state.is_relative_to(old_state) and not old_state.is_relative_to(state),'Continuation state overlaps prior evidence')
    # Reuse exact deterministic criterion extraction; models cannot add methods.
    from flow_workflow_validation import requirement_document
    requirement=requirement_document(api.pinned(c['requirement']).decode(),merge_heading_sections=True)
    context=sorted(set().union(*report['selection'].values()) |
                   {p for s in tests['stages'] for p in s['definitionSources']})
    for p in context:
        require(p in files and not intake.SECRET_PATH.search(p),'Unsafe context source')
        text=files[p][1].decode('utf-8')
        require(not intake.SECRET_TEXT.search(text),'Suspected secret in selected source')
    intent={'format':FORMAT,'schemaVersion':1,'config':{'path':str(config_path),'sha256':config_sha},
        'state':str(state),'controllerManifestSha256':c['manifestSha256'],'python':c['python'],
        'delivery':c['delivery'],'target':{'commit':c['source']['commit'],'tree':reader(c).tree},
        'sourceSelection':report['selection'],'contextPaths':context,'testCatalog':c['testCatalog'],
        'maximumRounds':c['maximumRounds'],'maximumCodexTurns':c['maximumCodexTurns'],
        'effects':['Fresh read-only Codex implementation, independent source review and executed-evidence turns',
            'Private single-parent committed candidates; independent selected local project recipes in a private copy'],
        'hostBoundary':'Reviewed host processes with operator privileges and normal network; NOT a security sandbox',
        'runtimeReview':c['runtimeReview'],'costSource':'codex-account-usage','amountNanoUsd':None,
        'sourceGatePolicy':'source-gate-before-execution-v1',
        **({'resumeCandidate':c['resumeCandidate']} if 'resumeCandidate' in c else {}),
        'apiDispatchAuthorized':False,'publicationAuthorized':False,'deploymentAuthorized':False}
    return c,report,files,tests,requirement,intent


def apply_candidate(c, state, parent, proposal, implementation_pin, original_baseline, round_number, *, verify_only=False):
    pc={**c,'source':{**c['source'],'checkout':parent['checkout'],'commit':parent['commit']}}
    original=blobs(pc); selected={p:original[p][1].decode() for p in proposal['editablePaths']}
    # Strict old/new validation is transport-independent and writes nothing.
    edits=proposal['answer']; checked=dict(selected)
    for edit in edits['edits']:
        require(isinstance(edit,dict) and set(edit)=={'path','old','new'} and edit['path'] in checked
            and all(isinstance(v,str) for v in edit.values()) and edit['old'] and edit['old']!=edit['new']
            and checked[edit['path']].count(edit['old'])==1,'Out-of-scope/ambiguous implementation edit')
        checked[edit['path']]=checked[edit['path']].replace(edit['old'],edit['new'],1)
    changes={};files=dict(original)
    for p,text in checked.items():
        raw=text.encode()
        if raw!=original[p][1]:
            changes[p]={'beforeSha256':api.digest(original[p][1]),'afterSha256':api.digest(raw)}
            files[p]=(original[p][0],raw)
    require(changes,'Codex proposed no effective change; context/requirement needs attention')
    timestamp=(saved(state/'candidate-attempt.json')['commitTimestamp'] if (state/'candidate-attempt.json').exists()
               else datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    intent={'format':'aim-flow-codex-candidate','round':round_number,'parent':parent,
        'originalBaseline':original_baseline,'proposal':implementation_pin,'commitTimestamp':timestamp,
        'files':{p:{'mode':mode,'sha256':api.digest(raw)} for p,(mode,raw) in sorted(files.items())},'changes':changes}
    receipt=state/'candidate-receipt.json'
    if receipt.exists():
        result=saved(receipt)
        require(result.get('intent')==intent and result.get('intentSha256')==api.digest(api.encoded(intent)),
                'Retained native candidate intent changed')
        target=result['candidate'];work.verify_candidate(pc,state,intent,target)
    else:
        require(not verify_only, 'Missing native candidate; verification never creates it')
        require(not (state/'candidate-attempt.json').exists() and not (state/'candidate').exists(),
            'Partial candidate outcome unknown; do not recreate')
        keep(state,'candidate-attempt.json',intent)
        target=work.create_candidate(pc,state,files,timestamp);work.verify_candidate(pc,state,intent,target)
        result={'status':'candidate-prepared','intent':intent,'intentSha256':api.digest(api.encoded(intent)),
            'candidate':target,'source':work.candidate_source(c,target)}
        keep(state,'candidate-receipt.json',result)
    return result


def selected_stages(tests, ids):
    require(isinstance(ids,list) and len(ids)==len(set(ids)),'Distinct selected test ids required')
    rows={r['id']:r for r in tests['stages']}
    require(set(ids)<=rows.keys() and set(tests['requiredTests'])<=set(ids),'Unknown or omitted mandatory test')
    count=sum(rows[x]['kind']=='test' for x in ids)
    require(tests['minimumTests']<=count<=tests['maximumTests'],'Selected tests violate reviewed test policy')
    closure=set(ids)
    for row in reversed(tests['stages']):
        if row['id'] in closure:closure.update(row['needs'])
    return [row for row in tests['stages'] if row['id'] in closure]


def execution(c, state, target, stages, source_review_pin, *, verify_only=False):
    files=blobs(c,target)
    environment,runtime_support=commands.qualified_tool_environment(c['tools'],
        {'path':c['source']['git'],'sha256':c['source']['gitSha256']},c['environment'],state=state)
    identity={'format':'aim-flow-codex-project-execution','candidate':target,'stages':stages,
        'tools':c['tools'],'environment':environment,'sourceReview':source_review_pin,
        **({'toolRuntimeSupport':runtime_support} if runtime_support else {}),
        'timeoutSeconds':c['testTimeoutSeconds'],'outputBytes':c['outputBytes'],'artifactBytes':c['artifactBytes']}
    if state.exists():
        require(saved(state/'intent.json')==identity,'Retained project test identity changed')
        result=saved(state/'result.json')
        require(saved(state/'started.json')=={'intentSha256':api.digest(api.encoded(identity)),'replayAuthorized':False},
            'Project test dispatch marker changed')
        for path,sha in result['evidenceSha256'].items():
            require(api.digest(api.file_bytes(state/path))==sha,'Project evidence changed: '+path)
        require(result['intentSha256']==api.digest(api.encoded(identity)),'Project execution binding differs')
        project_target={**target,'checkout':str(state/'project')}
        require(tracked(c,project_target)==result['sourceIdentity'],'Project test source drift')
        require(result['stages']==saved(state/'stages.json'),'Project stages changed')
        require(result.get('format')==identity['format'] and result.get('candidate')==target,
                'Native execution result/candidate differs')
        require(len(result['stages'])<=len(stages),'Unexpected project stages')
        expected_logs=set()
        for row,observed in zip(stages,result['stages']):
            require(observed['id']==row['id'] and observed['kind']==row['kind']
                and observed['argv']==[c['tools'][row['tool']]['path'],*row['arguments']], 'Observed test recipe differs')
            require(set(observed['logs'])=={'stdout','stderr'},'Missing project stage logs')
            for kind,log in observed['logs'].items():
                require(log['path']==row['id']+'.'+kind,'Project log path differs')
                expected_logs.add(log['path'])
        passed=len(result['stages'])==len(stages) and all(s['exitCode']==0 and s['termination'] is None for s in result['stages'])
        require((result['status']=='passed')==passed,'Project execution success is not supported by observed stages')
        expected_artifacts=[x for row in stages[:len(result['stages'])] for x in row['artifacts']]
        if passed:
            require([{k:x[k] for k in ('path','mediaType')} for x in result['artifacts']]==expected_artifacts,
                    'Missing or unexpected declared evidence artifacts')
        expected_logs.update(a['retainedPath'] for a in result['artifacts'])
        require(set(result['evidenceSha256'])==expected_logs|{'stages.json','workspace.json'},'Execution evidence inventory differs')
        workspace=saved(state/'workspace.json')
        require(workspace['candidate']==target and workspace['sourceIdentity']==result['sourceIdentity']
                and workspace['artifacts']==result['artifacts'],'Workspace evidence differs')
        for name,spec in c['tools'].items():
            require((state/'bin'/name).is_symlink() and os.readlink(state/'bin'/name)==spec['path'], 'Project tool alias changed')
        return result
    require(not verify_only,'Missing execution; verification never executes')
    with new_owned_directory(state):
        keep(state,'intent.json',identity)
        keep(state,'started.json',{'intentSha256':api.digest(api.encoded(identity)),'replayAuthorized':False})
        # clone/checkout plumbing only; no hooks, templates, filters, or hardlinks
        pc={**c,'source':{**c['source'],'checkout':target['checkout'],'commit':target['commit']}}
        work.git(pc,state,'clone','--local','--no-hardlinks','--no-checkout','--template=','--',target['checkout'],str(state/'project'))
        work.git(pc,state/'project','remote','remove','origin')
        work.git(pc,state/'project','read-tree',target['commit'])
        for path,(mode,raw) in files.items():
            owned_mkdir(state/'project',str(Path(path).parent));p=state/'project'/path
            api.durable(p.parent,p.name,raw)
            if mode=='100755':p.chmod(0o700)
        work.git(pc,state/'project','update-ref','refs/heads/aim-flow-tests',target['commit'])
        work.git(pc,state/'project','symbolic-ref','HEAD','refs/heads/aim-flow-tests')
        before=tracked(c,{**target,'checkout':str(state/'project')})
        (state/'bin').mkdir(mode=0o700)
        for name,spec in c['tools'].items():(state/'bin'/name).symlink_to(spec['path'])
        (state/'home').mkdir(mode=0o700)
        env={**environment,'PATH':str(state/'bin'),'HOME':str(state/'home')}
        observations=[];artifacts=[];evidence={};status='passed'
        for row in stages:
            argv=[c['tools'][row['tool']]['path'],*row['arguments']]
            observed=commands.capture(argv,state/'project'/row['root'],env,state,row['id'],
                                      c['testTimeoutSeconds'],c['outputBytes'])
            observations.append({'id':row['id'],'kind':row['kind'],**observed})
            for log in observed['logs'].values():evidence[log['path']]=log['sha256']
            if observed['exitCode']!=0 or observed['termination'] is not None:
                status='failed';break
            for item in row['artifacts']:
                raw=api.file_bytes(state/'project'/item['path'])
                require(len(raw)<=c['artifactBytes'],'Declared artifact limit exceeded')
                if item['mediaType']=='image/png':require(raw.startswith(b'\x89PNG\r\n\x1a\n'),'Invalid PNG evidence')
                else:unique_json(raw)
                name='artifact-'+str(len(artifacts))+('.png' if item['mediaType']=='image/png' else '.json')
                api.durable(state,name,raw);evidence[name]=api.digest(raw)
                artifacts.append({**item,'retainedPath':name,'sha256':api.digest(raw),'bytes':len(raw)})
                if sum(a['path']==item['path'] for stage in stages for a in stage['artifacts'])>1:
                    artifacts[-1]['evidenceId']='artifact:'+row['id']+':'+item['path']
        require(tracked(c,{**target,'checkout':str(state/'project')})==before,'Project tests changed tracked source')
        for spec in c['tools'].values():commands.tool_identity(spec)
        keep(state,'stages.json',observations);evidence['stages.json']=api.digest(api.file_bytes(state/'stages.json'))
        keep(state,'workspace.json',{'candidate':target,'sourceIdentity':before,'artifacts':artifacts,
            'scope':'Selected declared outputs only; not a complete dependency or workspace inventory'})
        evidence['workspace.json']=api.digest(api.file_bytes(state/'workspace.json'))
        result={'format':'aim-flow-codex-project-execution','status':status,'intentSha256':api.digest(api.encoded(identity)),
            'candidate':target,'sourceIdentity':before,'stages':observations,'artifacts':artifacts,
            'evidenceSha256':evidence,'executionBoundary':'Reviewed host processes; normal host/network privileges; not a sandbox',
            'replayAuthorized':False}
        keep(state,'result.json',result);return result


def evaluate(body, packet, execution_result, pending_items=()):
    shape.validate_shape(body,EVIDENCE_SCHEMA); body=copy.deepcopy(body)
    expected={x['id'] for x in packet['requirement']['criteria']}
    require([r['requirementId'] for r in body['coverage']] and
        {r['requirementId'] for r in body['coverage']}==expected and len(body['coverage'])==len(expected),
        'Executed review must cover every original criterion exactly once')
    available={'stage:'+r['id'] for r in execution_result['stages'] if r['exitCode']==0 and r['termination'] is None}
    available|={r.get('evidenceId','artifact:'+r['path']) for r in execution_result['artifacts']}
    for row in body['coverage']:
        require(len(row['evidenceIds'])==len(set(row['evidenceIds'])) and set(row['evidenceIds'])<=available,
                'Executed review references unknown/unverified evidence')
        require(row['status']!='satisfied' or row['evidenceIds'], 'Satisfied criteria require observed execution evidence')
    dispositions=body['priorDispositions'];expected_prior={x['id'] for x in pending_items}
    require({x['id'] for x in dispositions}==expected_prior and len(dispositions)==len(expected_prior),
            'Every pending source-verification item needs one executed disposition')
    for row in dispositions:
        require(len(row['evidenceIds'])==len(set(row['evidenceIds'])) and set(row['evidenceIds'])<=available,
                'Prior disposition cites unknown runtime evidence')
        require(row['status']!='resolved' or row['evidenceIds'],'Resolved source uncertainty requires actual execution evidence')
    # Shared strict source citations/materiality, without pretending a Codex body
    # was an API transport response or a historical API review receipt.
    findings=review.parse_body({'summary':'Execution finding citations','findings':body['findings'],
        'coverage':[{'requirementId':x,'status':'unverified','analysis':'Runtime evidence assessed separately','sources':[]}
                    for x in sorted(expected)],'unknowns':[]},packet,reviewer='Codex')['findings']
    body['findings']=findings
    classified={**body,'findingDispositionPolicy':review.FINDING_DISPOSITION_POLICY}
    blockers=[x['id']+': '+x['description'] for x in review.blocking_findings(classified)]
    blockers += [x['requirementId']+': '+x['analysis'] for x in body['coverage'] if x['status'] in ('failed','uncertain')]
    blockers += [x['id']+': '+x['analysis'] for x in dispositions if x['status']=='unresolved']
    require(execution_result['status']=='passed','Executed evidence cannot accept failed project tests')
    pending=[{'requirementId':x['requirementId'],'analysis':x['analysis']} for x in body['coverage'] if x['status']=='pending-delivery']
    pending += [{'itemId':x['id'],'analysis':x['analysis']} for x in dispositions if x['status']=='pending-delivery']
    return {'format':'aim-flow-codex-executed-review','findingDispositionPolicy':review.FINDING_DISPOSITION_POLICY,
        'candidate':packet['candidate'],**body,'blockers':blockers,'localFeatureAccepted':not blockers,
        'productAccepted':not blockers and not pending,'pendingDeliveryObligations':pending,
        'followUps':review.follow_up_findings(classified)}


def run(config_path,config_sha,state,authorization=None,*,verify_only=False,continue_run=False):
    config_path,state=api.canonical_path(config_path),api.canonical_path(state)
    c,report,original,tests,requirement,intent=prepare(config_path,config_sha,state)
    grant={'intent':intent,'intentSha256':api.digest(api.encoded(intent))}
    require(sum((authorization is not None,verify_only,continue_run))<=1,'Choose one Codex workflow mode')
    if not state.exists():
        require(not verify_only and not continue_run,'No existing Codex workflow to verify/continue')
        if authorization is None:return {'status':'awaiting-authorization',**grant,'modelCallsExecuted':False}
        require(authorization==grant['intentSha256'],'Exact native Codex workflow grant required')
        with new_owned_directory(state):pass
        keep(state,'grant.json',grant);keep(state,'intake.json',report)
        api.durable(state,'run.lock',b'')
    require(saved(state/'grant.json')==grant,'Codex workflow grant/config/source changed')
    if authorization is not None:require(authorization==grant['intentSha256'],'Exact workflow grant required')
    if not verify_only and not continue_run and authorization is None:
        if (state/'result.json').exists():verify_only=True
        else:return {'status':'awaiting-granted-continuation','replayAuthorized':False,'intentSha256':grant['intentSha256']}
    ledger=Path(c['dispatchDirectory'])
    if not ledger.exists():
        require(not verify_only,'Codex dispatch ledger missing')
        with new_owned_directory(ledger):pass
    with os.fdopen(owned_file(state,'run.lock',os.O_RDWR),'r+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refused('Codex workflow already has an active controller') from None
        def recheck():
            require(unique_json(api.pinned({'path':str(config_path),'sha256':config_sha}))==c,'Workflow config changed')
            control_package.verify_installation(ROOT,c['manifestSha256']);cli.tool(c['codex']);cli.tool(c['python'])
            api.pinned(c['requirement']);api.pinned(c['testCatalog']);reader(c).verify_head()
            for spec in c['tools'].values():commands.tool_identity(spec)
            if 'resumeCandidate' in c:
                verified_run(c['resumeCandidate']['localRun'],c['manifestSha256'],allow_blocked=True)
        resumed=None
        if 'resumeCandidate' in c:
            resumed=verified_run(c['resumeCandidate']['localRun'],c['manifestSha256'],allow_blocked=True)[0]
        models=copy.deepcopy(resumed['modelStages']) if resumed else []
        threads={x['threadId'] for x in models};rounds=[];feedback=None
        def retain_round(directory,name,value):
            if verify_only:
                require(api.file_bytes(directory/name)==api.encoded(value),'Native review evidence missing or changed: '+name)
            else:keep(directory,name,value)
        parent={'checkout':c['source']['checkout'],'commit':c['source']['commit'],'tree':intent['target']['tree']}
        def call(round_state,name,packet,schema,images=()):
            attempt=round_state/name
            if not attempt.exists():
                used=len(list(state.glob('round-*/model-*/started.json')))
                require(used<c['maximumCodexTurns'],'Codex turn allowance exhausted; no automatic extension')
            if not verify_only:
                api.atomic_json(state/'progress.json',{'phase':name,'round':round_state.name,
                    'state':'working','costSource':'codex-account-usage','amountNanoUsd':None})
            value=model.stage(c,attempt,name,packet,schema,verify_only=verify_only,recheck=recheck,images=images)
            require(value['threadId'] not in threads,'Independent Codex phases require distinct fresh thread identities')
            threads.add(value['threadId']);models.append({'phase':name,'result':pin(attempt/'result.json'),
                'threadId':value['threadId'],'usage':value['usage']})
            return value
        accepted=None;last_candidate=None
        for number in range(1,c['maximumRounds']+1):
            round_state=state/('round-'+str(number).zfill(3))
            if not round_state.exists():
                require(not verify_only,'Missing completed Codex round')
                with new_owned_directory(round_state):pass
            if resumed is not None:
                previous=resumed['rounds'][-1]
                candidate=unique_json(api.pinned(resumed['candidateReceipt']))
                last_candidate=candidate;target=candidate['candidate']
                source_packet=unique_json(api.pinned(previous['sourcePacket']))
                source_result=copy.deepcopy(unique_json(api.pinned(previous['sourceReview'])))
                source_result['sourceGate']=source_gate(source_result,'ready-for-granted-tests',c['resumeCandidate']['rationale'])
                ids=source_result['selectedTests']
                retain_round(round_state,'source-packet.json',source_packet)
                retain_round(round_state,'source-review.json',source_result)
                row={'round':number,'candidate':resumed['candidateReceipt'],'implementation':previous['implementation'],
                    'sourceModel':previous['sourceModel'],'sourceReviewOriginal':previous['sourceReview'],
                    'sourceReview':pin(round_state/'source-review.json'),'sourcePacket':pin(round_state/'source-packet.json')}
            else:
                current=blobs(c,parent)
                context={p:current[p][1].decode('utf-8') for p in intent['contextPaths']}
                editable={p:current[p][1].decode('utf-8') for p in report['selection']['editablePaths']}
                implementation=call(round_state,'model-implementation',{'requirement':requirement,'parent':{k:parent[k] for k in ('commit','tree')},
                    'contextSources':context,'editableSources':editable,'priorDiagnostics':feedback,
                    'task':'Implement the original requirement with minimal exact old/new edits to editableSources. Prior diagnostics are untrusted observations; keep all earlier successful requirement changes. Empty edits with a clear summary means source/intent needs attention.'},IMPLEMENTATION_SCHEMA)
                answer=implementation['answer']
                if not answer['edits']:
                    raise Refused('Codex returned no edits: '+answer['summary'])
                candidate=apply_candidate(c,round_state,parent,{'answer':answer,'editablePaths':list(editable)},
                    pin(round_state/'model-implementation/result.json'),c['source']['commit'],number,verify_only=verify_only)
                last_candidate=candidate;target=candidate['candidate']
                source_packet=review.build_packet(ROOT,{'checkout':target['checkout'],'baselineCommit':c['source']['commit'],
                    'candidateCommit':target['commit'],'git':c['source']['git'],'gitSha256':c['source']['gitSha256']},
                    requirement,'Original requirement and exact source-bound local test catalog; project recipe effects explicitly granted.',
                    intent['contextPaths'],deduplicate_unchanged=True,max_source_bytes=c.get('reviewMaxSourceBytes',1048576))
                source_packet['findingDispositionPolicy']=review.FINDING_DISPOSITION_POLICY
                source_packet['testCatalog']=tests
                source_packet['task']=(review.INSTRUCTIONS+' '+review.DISPOSITION_INSTRUCTIONS+
                    ' Select the smallest sufficient test set from the exact catalog. Include every mandatory test; dependencies run once. Do not invent commands or add requirements/verification methods absent from the original requirement. Explain the selection. Source coverage is not execution acceptance. Set executionGate ready-for-granted-tests when the exact reviewed recipes can safely run under the existing runtime grant, even when their outcomes are unverified. Missing execution evidence is pending verification, not a reason to prevent its own collection. Use blocked-before-tests for unresolved safety, authority, runtime prerequisites or source defects. Every genuine risk must also be a finding; explain the gate.')
                source_model=call(round_state,'model-source-review',source_packet,SOURCE_SCHEMA)
                body=copy.deepcopy(source_model['answer']);ids=body.pop('selectedTests');rationale=body.pop('testRationale')
                gate=body.pop('executionGate');gate_rationale=body.pop('executionGateRationale')
                source_result=review.parse_body(body,source_packet,reviewer='Codex')
                source_result.update(selectedTests=ids,testRationale=rationale)
                source_result['sourceGate']=source_gate(source_result,gate,gate_rationale)
                retain_round(round_state,'source-packet.json',source_packet);retain_round(round_state,'source-review.json',source_result)
                row={'round':number,'candidate':pin(round_state/'candidate-receipt.json'),
                     'implementation':pin(round_state/'model-implementation/result.json'),
                     'sourceModel':pin(round_state/'model-source-review/result.json'),
                     'sourceReview':pin(round_state/'source-review.json'),'sourcePacket':pin(round_state/'source-packet.json')}
            rounds.append(row)
            if source_result['sourceGate']['blockers']:
                if not review.blocking_findings(source_result) and all(x['status']!='missing' for x in source_result['coverage']):
                    feedback={'sourceReview':source_result}
                    break  # Source edits cannot grant missing runtime/permission prerequisites.
                feedback={'sourceReview':source_result};parent=target;continue
            stages=selected_stages(tests,ids)
            observed=execution(c,round_state/'validation',target,stages,pin(round_state/'source-review.json'),verify_only=verify_only)
            row['execution']=pin(round_state/'validation/result.json')
            if observed['status']!='passed':
                require(all(s['termination'] is None for s in observed['stages']),
                    'Project execution interrupted/limited; reconcile unknown effects without replay')
                feedback={'sourceReview':source_result,'execution':observed,
                    'logs':{log['path']:api.file_bytes(round_state/'validation'/log['path']).decode('utf-8',errors='replace')
                            for step in observed['stages'] for log in step['logs'].values()}}
                parent=target;continue
            evidence=[{'id':'stage:'+s['id'],'observation':s,
                'logs':{kind:api.file_bytes(round_state/'validation'/log['path']).decode('utf-8',errors='replace')
                        for kind,log in s['logs'].items()}} for s in observed['stages']]
            images=[]
            for artifact in observed['artifacts']:
                raw=api.file_bytes(round_state/'validation'/artifact['retainedPath'])
                item={'id':artifact.get('evidenceId','artifact:'+artifact['path']),'mediaType':artifact['mediaType'],'sha256':artifact['sha256']}
                if artifact['mediaType']=='image/png':
                    item['imageIndex']=len(images)
                    images.append(pin(round_state/'validation'/artifact['retainedPath']))
                else:item['json']=unique_json(raw)
                evidence.append(item)
            evidence_packet={'requirement':requirement,'candidate':source_packet['candidate'],'sources':source_packet['sources'],
                'evidence':evidence,'priorFollowUps':source_result['followUps'],
                'pendingSourceVerification':source_result['sourceGate']['pendingVerification'],
                'task':'Independently assess the original requirement against exact source and actual project execution evidence/attached PNG pixels. Cover each original criterion once. Satisfied requires evidence ids. Pending remote CI/release/production remain pending-delivery. Do not add mandatory verification methods absent from the requirement. Report unperformed optional methods as limitations; if essential behavior remains unproved use uncertain. Preserve genuine defects and follow-up findings with factual severity/impact. Account for each pendingSourceVerification id exactly once in priorDispositions. Resolved requires actual evidence ids; retain nonessential unperformed methods as nonblocking limitations, and leave essential unresolved evidence blocked. Do not claim human review, deployment or unexecuted tests.'}
            evidence_model=call(round_state,'model-executed-review',evidence_packet,EVIDENCE_SCHEMA,images)
            executed=evaluate(evidence_model['answer'],source_packet,observed,source_result['sourceGate']['pendingVerification'])
            # Independent evidence review cannot erase pre-existing follow-ups.
            executed['sourceFollowUps']=source_result['followUps']
            retain_round(round_state,'executed-packet.json',evidence_packet);retain_round(round_state,'executed-review.json',executed)
            row.update(executedModel=pin(round_state/'model-executed-review/result.json'),
                executedPacket=pin(round_state/'executed-packet.json'),executedReview=pin(round_state/'executed-review.json'))
            if executed['localFeatureAccepted']:
                accepted=executed;break
            feedback={'sourceReview':source_result,'executedReview':executed,'execution':observed};parent=target
        require(last_candidate is not None,'No private Codex candidate')
        usage={key:sum(x['usage'][key] for x in models) for key in ('input_tokens','cached_input_tokens','output_tokens')}
        result={'format':FORMAT,'status':'local-feature-evidence-reviewed' if accepted else 'codex-review-blocked',
            'grant':pin(state/'grant.json'),'config':intent['config'],'candidate':last_candidate['candidate'],
            'source':last_candidate['source'],'candidateReceipt':rounds[-1]['candidate'],
            'producerState':str(Path(rounds[-1]['candidate']['path']).parent),'finalReviewState':str(round_state),
            'rounds':rounds,'modelStages':models,'retainedModelTurns':len(resumed['modelStages']) if resumed else 0,
            'executedReview':accepted or (feedback or {}).get('executedReview'),
            'localFeatureAccepted':accepted is not None,'productAccepted':False,
            'pendingDeliveryObligations':accepted['pendingDeliveryObligations'] if accepted else [],
            'costSource':'codex-account-usage','usage':usage,'amountNanoUsd':None,
            'responsesApiDispatchExecuted':False,'publicationAuthorized':False,'deploymentAuthorized':False,
            'replayAuthorized':False,'qualification':'Native Codex model assessment plus actual local project receipts; not human acceptance or production verification.'}
        if verify_only:
            require(saved(state/'result.json')==result,'Retained Codex workflow result differs')
        else:keep(state,'result.json',result)
        if not verify_only:
            api.atomic_json(state/'progress.json',{'phase':'completed','state':result['status'],
                'costSource':'codex-account-usage','amountNanoUsd':None})
        return result


def verified_run(spec,manifest_sha,*,allow_blocked=False):
    from aim_ci import project_validation
    shape.onboarding._keys(spec,('config','state','grant','result',*(('controller',) if 'controller' in spec else ())), 'native Codex local run')
    if 'controller' in spec:shape.onboarding._keys(spec['controller'],('path','manifestSha256'),'native prior controller')
    state=api.canonical_path(spec['state']);grant=unique_json(api.pinned(spec['grant']));retained=unique_json(api.pinned(spec['result']))
    config=unique_json(api.pinned(spec['config']));root=api.canonical_path(spec.get('controller',{'path':str(ROOT)})['path'])
    local_manifest=spec.get('controller',{}).get('manifestSha256',manifest_sha)
    require(spec['grant']['path']==str(state/'grant.json') and spec['result']['path']==str(state/'result.json'), 'Native local pins outside state')
    require(config['manifestSha256']==local_manifest and grant['intent']['format']==FORMAT
        and grant['intent']['config']==spec['config'] and grant['intent']['state']==str(state), 'Native local identity differs')
    before=project_validation.prior_controller(root,local_manifest)
    require('lib/flow_codex_workflow.py' in unique_json(before)['files'],'Controller lacks native Codex verifier')
    commands.tool_identity(grant['intent']['python'])
    blocked=retained.get('status')=='codex-review-blocked' and retained.get('localFeatureAccepted') is False
    require(not blocked or allow_blocked,'Native Codex local acceptance incomplete')
    process=project_validation.verification_process([grant['intent']['python']['path'],'-IBS',str(root/'aim-flow.py'),
        'run-codex','--config',spec['config']['path'],'--config-sha256',spec['config']['sha256'],
        '--state',str(state),'--verify'],ROOT,{'PATH':os.defpath,'LC_ALL':'C','HOME':str(ROOT)},
        timeout_seconds=None,metadata_limit=max(64000,len(api.pinned(spec['result']))*2),
        expected_terminal_failure=retained if blocked else None)
    require(process.returncode==(1 if blocked else 0) and not process.stderr,'Native installed Codex verifier refused')
    result=unique_json(process.stdout)
    require(result==retained and result['format']==FORMAT and (blocked or result['status']=='local-feature-evidence-reviewed'
        and result['localFeatureAccepted'] is True),'Native Codex local acceptance incomplete')
    require(before==project_validation.prior_controller(root,local_manifest),'Native controller changed during verification')
    return result,config,grant,root


def verified_local(spec,manifest_sha):
    result,config,grant,root=verified_run(spec,manifest_sha)
    local_manifest=config['manifestSha256'];state=api.canonical_path(spec['state'])
    final=result['rounds'][-1];round_state=Path(result.get('finalReviewState',result['producerState']))
    candidate=unique_json(api.pinned(result['candidateReceipt']))
    source_review=unique_json(api.pinned(final['sourceReview']));executed=unique_json(api.pinned(final['executedReview']))
    observation=unique_json(api.pinned(final['execution']));packet=unique_json(api.pinned(final['executedPacket']))
    require(not source_review.get('sourceGate',source_review)['blockers'] and not executed['blockers']
        and observation['status']=='passed', 'Native local evidence remains blocked')
    evidence={'localRun':spec['result'],'localGrant':spec['grant'],'candidate':result['candidateReceipt'],
        'aggregate':final['execution'],'workspace':pin(round_state/'validation/workspace.json'),
        'sourceReview':final['sourceReview'],'executedReview':final['executedReview'],'executedPacket':final['executedPacket']}
    return {'result':result,'initial':candidate,'source':result['source'],'candidate':result['candidate'],
        'review':executed,'validation':observation,'packet':packet,'config':config,'runIntent':grant['intent'],
        'evidence':evidence,'nativeCodex':True,'producerController':{'path':str(root),'manifestSha256':local_manifest},
        'producerState':result['producerState'],'runState':str(state),'localConfig':spec['config']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--config-sha256',required=True)
    parser.add_argument('--state',type=Path,required=True);parser.add_argument('--authorize-intent')
    parser.add_argument('--verify',action='store_true');parser.add_argument('--continue',dest='continue_run',action='store_true')
    args=parser.parse_args(argv)
    try:
        result=run(args.config,args.config_sha256,args.state,args.authorize_intent,
            verify_only=args.verify,continue_run=args.continue_run)
    except (ValueError,OSError) as error:
        result={'format':FORMAT,'status':'blocked','reason':str(error), 'state':str(args.state),
            'costSource':'codex-account-usage','amountNanoUsd':None,'replayAuthorized':False}
        if (args.state/'progress.json').exists():
            result['lastProgress']=saved(args.state/'progress.json')
    print(json.dumps(result,indent=2))
    return 0 if result['status'] in ('awaiting-authorization','awaiting-granted-continuation','local-feature-evidence-reviewed') else 1
