"""Phase-focused display with exact, read-only, source-bound expansion files.

This is retrieval, never an execution closure or semantic-completeness claim.
No selected source is lost: the inventory and full bytes remain available.
"""
import copy
import math
from pathlib import Path, PurePosixPath
import re

import flow_api_delivery as api
import flow_requirement_intake as intake
from aim_ci.owned_paths import Refused, owned_mkdir

POLICY = 'phase-focused-source-with-expansion-v1'
LOCK = re.compile(r'(?:^|/)(?:package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|poetry\.lock|uv\.lock|composer\.lock|Gemfile\.lock)$')
DEPENDENCY = re.compile(r'dependenc|lockfile|package[- ]lock|supply.chain|vulnerab|sårbar|beroend|paketversion', re.I)


def logical(path):
    return re.sub(r'^(?:baseline|candidate)/', '', path)


def windows(text, hits):
    """Line-addressed surrounding context, with every omission explicit."""
    lines = text.splitlines(keepends=True)
    intervals = []
    for hit in hits:
        start, end = max(0, hit-8), min(len(lines), hit+9)
        if intervals and start <= intervals[-1][1]: intervals[-1][1] = max(end, intervals[-1][1])
        else: intervals.append([start, end])
    return [{'startLine': a+1, 'endLine': b, 'text': ''.join(lines[a:b])} for a,b in intervals]


def project(packet, phase, state):
    """Pure construction; paths, exact bytes and display choices enter the grant."""
    packet = copy.deepcopy(packet)
    original = packet.get('requirement', '')
    original = original.get('original', '') if isinstance(original, dict) else original
    query = intake.terms(original)
    sources = {}
    for role in ('contextSources', 'editableSources'):
        for path,text in packet.get(role, {}).items():
            if path in sources and sources[path]['text'] != text: raise Refused('Conflicting model source')
            sources[path] = {'text':text, 'editable':role=='editableSources','immutable':role=='contextSources'}
    for row in packet.get('sources', []):
        if isinstance(row,dict) and isinstance(row.get('text'),str):
            sources[row['path']] = {'text':row['text'], 'editable':False,'immutable':False}
    paths = list(sources)
    changed = {r['path'] for r in packet.get('changes', [])}
    catalog = packet.get('testCatalog', {})
    definitions = {p for s in catalog.get('stages', []) for p in s.get('definitionSources', [])}
    scores = {}
    token_sets={p:intake.terms(p+'\n'+r['text']) for p,r in sources.items()}
    ranking=packet.pop('sourceRanking',{})
    for path,row in sources.items():
        p = logical(path); text = row['text']
        tokens = token_sets[path]; matches = query & tokens
        score = sum((8 if t in intake.terms(p) else 1) *
            math.log(1+len(paths)/(1+sum(t in tokens for tokens in token_sets.values()))) for t in matches)
        if intake.is_feature_code(p) and PurePosixPath(p).suffix in ('.py','.js','.mjs','.cjs','.ts','.tsx','.jsx','.astro','.vue','.svelte'):
            scores[path] = ranking.get(p,score / math.sqrt(1+len(text.encode())/8000))
    maximum = max(scores.values(), default=0)
    primary = {p for p,s in scores.items() if s>0 and s>=maximum*.8}
    primary_text = '\n'.join(sources[p]['text'] for p in primary)
    names = {PurePosixPath(logical(p)).stem for p in primary} - {'index','main','app','__init__','page','layout'}
    classes = set()
    for value in re.findall(r'(?:class|className)\s*=\s*["\']([^"\']*)["\']', primary_text):
        classes.update(re.findall(r'[A-Za-z_][\w-]*', value))
    references = re.findall(r'(?:from\s*|import\s*\(?|require\s*\()\s*["\']([^"\']+)["\']',primary_text)
    imported_names = {PurePosixPath(x).name for x in references}
    dependency_task = bool(DEPENDENCY.search(original))
    index, assets, displays = [], {}, {}
    for path,row in sources.items():
        text=row['text']; p=logical(path); lines=text.splitlines(keepends=True)
        pin={'path':str(Path(state)/'context'/ (api.digest(path.encode())+'.txt')), 'sha256':api.digest(text.encode())}
        assets[Path(pin['path']).name]=text.encode()
        full = (path in primary or p in changed or PurePosixPath(p).name in ('AGENTS.md','CONTRIBUTING.md')
            or p in definitions and not LOCK.search(p) or LOCK.search(p) and dependency_task)
        reason='primary-requirement-source' if path in primary else 'changed-source' if p in changed else 'reviewed-recipe-or-project-instructions'
        hits=[]
        process=(row['immutable'] or bool(intake.PROCESS.search(p)) or p.startswith('.github/'))
        if not full and not (LOCK.search(p) and not dependency_task) and (not process or PurePosixPath(p).name=='README.md'):
            if PurePosixPath(p).suffix in ('.css','.scss'):
                hits=[i for i,line in enumerate(lines) if any(re.search(r'\.'+re.escape(c)+r'(?![\w-])',line) for c in classes)]
                reason='selectors-used-by-primary-source'
            else:
                related=PurePosixPath(p).name in imported_names
                hits=[i for i,line in enumerate(lines) if any(re.search(r'(?<![\w-])'+re.escape(n)+r'(?![\w-])',line) for n in names)]
                if related: hits += [i for i,line in enumerate(lines) if query & intake.terms(line)]
                hits=sorted(set(hits));reason='literal-primary-reference-or-import'
        excerpts=[] if full else windows(text,hits)
        display='whole' if full else 'excerpts' if excerpts else 'on-demand'
        if display=='on-demand':reason='lock-inventory-only' if LOCK.search(p) else 'outside-current-phase-focus'
        record={'path':path,'editable':row['editable'],'sha256':pin['sha256'],'lineCount':len(lines),
            'fullSource':pin,'display':display,'reason':reason,
            'ranges':[{'startLine':x['startLine'],'endLine':x['endLine']} for x in excerpts]}
        index.append(record)
        displays[path]=(text if full else '\n'.join(x['text'] for x in excerpts),excerpts)
    for role in ('contextSources','editableSources'):
        if role in packet:packet[role]={p:displays[p][0] for p in packet[role] if next(r for r in index if r['path']==p)['display']!='on-demand'}
    if 'sources' in packet:
        for row in packet['sources']:
            if row['path'] not in displays:continue
            record=next(r for r in index if r['path']==row['path'])
            if record['display']!='whole':
                row.pop('text');row['excerpts']=displays[row['path']][1]
            row['fullSource']=record['fullSource'];row['lineCount']=record['lineCount']
    packet['contextIndex']=index
    packet['contextPolicy']={'name':POLICY,'phase':phase,'selectedSourceFiles':len(index),
        'wholeFiles':sum(r['display']=='whole' for r in index),'excerptFiles':sum(r['display']=='excerpts' for r in index),
        'onDemandFiles':sum(r['display']=='on-demand' for r in index),
        'fullSourceCharacters':sum(len(x['text']) for x in sources.values()),
        'displayedSourceCharacters':sum(len(x[0]) for x in displays.values()),
        'limitation':'Heuristic focus, not semantic completeness. Read exact fullSource files whenever displayed context is insufficient. Omitted lines are not adjacent. Cite original full-file line numbers.'}
    return packet, assets


def retain(state, assets, verify_only=False):
    if not assets:return
    directory=Path(state)/'context'
    if verify_only or directory.exists():
        if not directory.is_dir() or directory.is_symlink():raise Refused('Missing/linked full model context')
        if {p.name for p in directory.iterdir()}!=set(assets):raise Refused('Model context inventory changed')
        for name,raw in assets.items():
            if api.file_bytes(directory/name)!=raw:raise Refused('Full model context changed')
    else:
        owned_mkdir(state,'context')
        for name,raw in assets.items():api.durable(directory,name,raw)
