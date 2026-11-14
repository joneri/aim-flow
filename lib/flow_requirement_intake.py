"""Deterministic committed-context retrieval, not semantic analysis or execution authority."""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import unicodedata

import flow_api_delivery as api
from aim_ci.owned_paths import Refused
from aim_ci.project_discovery import CommitReader, ROOT_FILES, PROJECT_DOC_PATH
from aim_ci.project_onboarding import _source_path, _outside

FORMAT = 'aim-flow-requirement-intake'
DEFAULT_LIMITS = {'fileBytes': 262_144, 'scanBytes': 24_000_000, 'contextBytes': 1_000_000, 'contextFiles': 80, 'reviewBytes': 440_000}
STOP = set('the and that this with from for are not must should existing without into can all when its using use have has was were will than then while user story want able need so as be to of a an in on or is it by at'.split())
# Keep Scandinavian words whole; ASCII fragments of e.g. "ändring" must not
# acquire rare-term weight. Prose grammar is not a feature identifier.
STOP.update('och att det den detta de dem som med från för på av till en ett är var har ska skall kan inte men om när där här hur vad vi ni jag man sig sin sina vår våra alla varje även eller utan under över bara helt finns vara bli blir vill önskad ändring klart borde skulle istället'.split())
SECRET_PATH = re.compile(r'(^|/)(?:\.env(?:\..*)?|\.npmrc|\.pypirc|credentials?[^/]*|secrets?[^/]*|id_rsa|id_ed25519)(?:/|$)|\.(?:pem|key|p12|pfx|keystore)$', re.I)
SECRET_TEXT = re.compile(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)|\b(?:sk-proj-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|(?im:^[ \t]*["\']?(?:password|api[_-]?key|client[_-]?secret|access[_-]?token)["\']?\s*[:=]\s*["\']?[^\s"\'${}]{8,})')
PROCESS = re.compile(r'(^|[/_.-])(?:deploy|release|publish|generator|generate|build|package|cutover)(?=[/_.-]|$)', re.I)
DOC_PROCESS = re.compile(r'(^|[/_.-])(?:readme|agents|contributing|installation|testing|deployment|release|architecture|development|verification|ci)(?=[/_.-]|$)', re.I)
TEXT_SUFFIXES = {'.js','.mjs','.cjs','.ts','.tsx','.jsx','.astro','.vue','.svelte','.css','.scss','.html','.md','.mdx','.json','.toml','.yaml','.yml','.py','.sh','.txt'}

def terms(text):
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return {t for t in re.findall(r'[^\W\d_][^\W_]*', text.casefold()) if len(t) >= 3 and t not in STOP}


def is_feature_code(path):
    return (PurePosixPath(path).suffix.lower() not in ('.md', '.mdx', '.txt')
            and not re.search(r'(^|[/_.-])(?:test|tests|spec)(?=[/_.-]|$)', path))


def derive(root, source, requirement, allowed_directories, limits=None):
    """Read pinned blobs only. A broad directory boundary is authority, not a source map."""
    limits = dict(DEFAULT_LIMITS if limits is None else limits)
    if set(limits) != set(DEFAULT_LIMITS) or any(type(n) is not int or n <= 0 for n in limits.values()):
        raise Refused('Intake limits must be explicit positive integers')
    if not allowed_directories or not isinstance(allowed_directories, list):
        raise Refused('Intake requires explicit allowed edit directories')
    prefixes = sorted(set(_source_path(p.rstrip('/')) + '/' for p in allowed_directories))
    _outside(Path(requirement['path']), Path(root), Path(source['checkout']))
    raw = api.pinned(requirement)
    try:
        requirement_text = raw.decode('utf-8')
    except UnicodeError:
        raise Refused('Requirement must be exact UTF-8 text') from None
    if not requirement_text.strip() or '\0' in requirement_text:
        raise Refused('Requirement must be nonempty regular text')
    reader = CommitReader(Path(root), Path(source['checkout']), Path(source['git']), source['gitSha256'], source['commit'])
    entries = sorted(reader.entries(reader.commit, recursive=True))
    records, texts, processes = {}, {}, set()
    referenced_process = set()
    scanned = 0
    for path, mode, kind, oid in entries:
        row = records[path] = {'path': path, 'gitObject': oid, 'mode': mode, 'gitType': kind, 'status': 'excluded', 'reason': 'outside-context-domain'}
        try:
            _source_path(path)
            path.encode('utf-8')
        except (Refused, UnicodeError):
            row['reason'] = 'unsupported-path'; continue
        process = (path in referenced_process or path in ROOT_FILES or path.startswith('.github/workflows/') or bool(PROCESS.search(path))
                   or (path.endswith('.md') and (PurePosixPath(path).name in ('README.md', 'AGENTS.md', 'CONTRIBUTING.md') or bool(DOC_PROCESS.search(PurePosixPath(path).name))) and not path.startswith('docs/features/'))
                   or (('/' not in path) and '.config.' in path))
        allowed = any(path.startswith(p) for p in prefixes)
        row['allowedDirectory'] = allowed
        row['processDefinition'] = process
        if process:
            processes.add(path)
        if SECRET_PATH.search(path) or '/config/' in '/' + path or path.startswith('config/'):
            row['reason'] = 'secret-or-credential-configuration-path'; continue
        if kind != 'blob' or mode not in ('100644', '100755'):
            row['reason'] = 'non-regular'; continue
        if not (process or allowed):
            continue
        if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES and path not in ROOT_FILES:
            row['reason'] = 'unsupported-text-type'; continue
        if PurePosixPath(path).suffix.lower() == '.json' and not process:
            row['reason'] = 'data-file-outside-code-retrieval'; continue
        size = reader.size(oid); row['bytes'] = size
        if size > limits['fileBytes'] or scanned + size > limits['scanBytes']:
            row.update(status='incomplete', reason='whole-file-read-bound'); continue
        data = reader.blob(oid, maximum=limits['fileBytes']); scanned += len(data)
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            row['reason'] = 'non-utf8'; continue
        if '\0' in text:
            row['reason'] = 'binary'; continue
        if SECRET_TEXT.search(text):
            row['reason'] = 'suspected-secret-content'; continue
        row.update(sha256=api.digest(data), reason='below-retrieval-threshold')
        texts[path] = text
        if PurePosixPath(path).name == 'package.json':
            try:
                scripts = json.loads(text).get('scripts', {})
                for body in scripts.values():
                    if isinstance(body, str):
                        for ref in re.findall(r'(?<![\w/.-])([\w.-]+(?:/[\w.-]+)+\.(?:js|mjs|cjs|py|sh))', body):
                            candidate = str(PurePosixPath(path).parent / ref)
                            if not any(candidate.startswith(prefix) for prefix in prefixes):
                                referenced_process.add(candidate)
            except (ValueError, AttributeError):
                pass
    for path in referenced_process:
        if path not in texts:
            if path in records:
                records[path].update(status='incomplete', reason='unread-literal-process-reference')
            else:
                records[path] = {'path': path, 'status': 'incomplete', 'reason': 'missing-literal-process-reference'}
    for path in processes:
        if path in records and path not in texts:
            records[path]['status'] = 'incomplete'
    # Transparent document-frequency weighting, never a confidence estimate.
    query = terms(requirement_text)
    token_sets = {p: terms(p + '\n' + t) for p, t in texts.items()}
    frequency = {t: len(re.findall(r'\b' + re.escape(t) + r'\b', requirement_text.casefold())) for t in query}
    weights = {t: (1 + math.log(1 + frequency[t])) * math.log(1 + len(texts) / (1 + sum(t in s for s in token_sets.values()))) for t in query}
    scores = {}
    for p, tokens in token_sets.items():
        matched = sorted(query & tokens)
        score = sum(weights[t] * (8 if t in terms(p) else 1) for t in matched)
        score /= math.sqrt(1 + len(texts[p].encode('utf-8')) / 8000)
        records[p].update(matchedTerms=matched, score=round(score, 6))
        if p not in processes and records[p]['allowedDirectory']:
            scores[p] = score
    # Documentation and test outliers must not set the code inclusion threshold.
    code_scores = {p: n for p, n in scores.items() if is_feature_code(p)}
    threshold = max(8.0, max((code_scores or scores).values(), default=0) * 0.45)
    selected = {p for p, score in scores.items() if score >= threshold *
                (0.75 if re.search(r'(^|[/_.-])(?:test|tests|spec)(?=[/_.-]|$)', p) else 1)}
    reasons = {p: 'requirement-term-ranking' for p in selected}
    # Retrieval is adjacent context, not an execution closure. Expanding a shared
    # layout recursively, then reversing all its references, used to pull most of
    # a site into a small label change. Unexpanded imports stay explicit below.
    seeds = set(selected)
    for p in sorted(seeds):
        for ref in re.findall(r'(?:from\s*|import\s*\(?|require\s*\()\s*["\'](\.[^"\']+)["\']', texts[p]):
            base = posixpath.normpath(str(PurePosixPath(p).parent / ref))
            candidates = [base, *(base + s for s in ('.ts','.tsx','.js','.jsx','.mjs','.astro','.css')), base + '/index.ts', base + '/index.js']
            found = next((c for c in candidates if c in texts), None)
            if found and found not in processes and found not in selected:
                selected.add(found); reasons[found] = 'literal-relative-import:' + p
    for p, text in texts.items():
        if p not in processes and p not in selected and PurePosixPath(p).suffix != '.md' and any(PurePosixPath(q).name in text for q in seeds if PurePosixPath(q).suffix != '.md'):
            reasons[p] = 'literal-selected-filename-reference'
    selected.update(reasons)
    requested = processes & texts.keys() | selected
    used = 0; review_used = 0; kept = []
    for p in sorted(requested, key=lambda p: (p not in processes, -scores.get(p, 0), p)):
        row = records[p]
        if (used + row['bytes'] > limits['contextBytes'] or len(kept) >= limits['contextFiles']
                or (p not in processes and review_used + row['bytes'] > limits['reviewBytes'])):
            row.update(status='incomplete', reason='whole-file-context-bound'); continue
        used += row['bytes']; kept.append(p)
        if p not in processes:
            review_used += row['bytes']
        row.update(status='selected', reason='immutable-process-definition' if p in processes else reasons[p])
    process_kept = sorted(set(kept) & processes)
    feature_kept = sorted(set(kept) - processes)
    documents = [p for p in process_kept if re.fullmatch(PROJECT_DOC_PATH, p)]
    selection = {'projectDocuments': documents, 'onboardingSources': sorted(set(process_kept) - set(documents)),
                 'reviewSources': feature_kept, 'editablePaths': [p for p in feature_kept if records[p]['allowedDirectory']]}
    omitted_dependencies = []
    for p in feature_kept:
        for ref in re.findall(r'(?:from\s*|import\s*\(?|require\s*\()\s*["\'](\.[^"\']+)["\']', texts[p]):
            base = posixpath.normpath(str(PurePosixPath(p).parent / ref))
            choices = [base, *(base + s for s in ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.astro', '.css')), base + '/index.ts', base + '/index.js']
            target = next((c for c in choices if c in records), None)
            if target not in kept:
                omitted_dependencies.append({'from': p, 'reference': ref, 'resolvedPath': target,
                    'reason': records[target]['reason'] if target else 'unresolved-literal-import'})
    incomplete = [p for p, row in records.items() if row['status'] == 'incomplete']
    reader.verify_head()
    return {'format': FORMAT, 'schemaVersion': 1, 'status': 'incomplete' if incomplete or not selection['editablePaths'] else 'selected',
            'source': copy.deepcopy(source), 'tree': reader.tree, 'requirement': copy.deepcopy(requirement),
            'allowedDirectories': prefixes, 'limits': limits, 'selection': selection,
            'coverage': list(records.values()), 'incompletePaths': incomplete, 'scannedBytes': scanned, 'contextBytes': used, 'reviewBytes': review_used,
            'omittedDependencies': omitted_dependencies,
            'reviewBounds': {'baselineFeatureBytes': review_used, 'allProcessAndFeatureBothSidesBytes': used * 2,
                'maximumBothSidesBytes': 1048576, 'requiresActualDeclarationAndCandidatePreflight': True},
            'algorithm': {'name': 'unicode-term-df-code-threshold-v2', 'terms': sorted(query), 'termWeights': {t: round(w, 6) for t, w in sorted(weights.items())},
                          'thresholdPopulation': 'feature-code, excluding documentation and tests; all features if no code',
                          'dependencyExpansion': 'one literal import hop and one reverse-reference hop from ranked seeds only',
                          'threshold': round(threshold, 6), 'testThresholdFactor': 0.75, 'lengthNormalizationBytes': 8000},
            'limitations': ['Heuristic retrieval only; exclusions may contain relevant evidence.',
                'Only committed regular UTF-8 blobs are read; no working-tree content or commands.',
                'Secret filtering is conservative, not proof that arbitrary source is safe to disclose.',
                'No semantic completeness, provider dispatch, execution, publication or acceptance is claimed.']}


def assemble_config(base_config, report):
    """Produce existing workflow schema without changing caller budget/resources or source."""
    if report.get('format') != FORMAT or report.get('status') != 'selected':
        raise Refused('Intake coverage incomplete or no editable source; cannot assemble workflow')
    if base_config.get('source') != report['source'] or base_config.get('requirement') != report['requirement']:
        raise Refused('Intake and workflow source/requirement differ')
    result = copy.deepcopy(base_config)
    result.update(copy.deepcopy(report['selection']))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-config', type=Path, required=True)
    parser.add_argument('--base-config-sha256', required=True)
    parser.add_argument('--allow-directory', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    base = json.loads(api.pinned({'path': str(args.base_config), 'sha256': args.base_config_sha256}))
    root = Path(__file__).resolve().parents[1]
    report = derive(root, base['source'], base['requirement'], args.allow_directory)
    _outside(args.output, root, Path(base['source']['checkout']))
    payload = {'intake': report, 'workflowConfig': assemble_config(base, report) if report['status'] == 'selected' else None}
    with os.fdopen(api.owned_file(args.output.parent, args.output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
        stream.write(api.encoded(payload))
        stream.flush(); os.fsync(stream.fileno())
    api.fsync_directory(args.output.parent)
    print(json.dumps({'status': report['status'], 'output': str(args.output), 'sha256': api.digest(api.encoded(payload))}))

if __name__ == '__main__':
    main()
