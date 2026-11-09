"""Draft an operator review from observations of the selected project commit."""
import json
from pathlib import PurePosixPath
import re

from openai_budget import Refused, fingerprint, read_owned, safe_path
from .control_package import VERSION_PATTERN, sha256
from .project_discovery import CommitReader, LOCKS, discover_project, unique_json
from .recipe_plan import INPUT_FILES, inspect_npm, recipe_operations


NPM_REFERENCES = {
    'commands/npm-ci.md': (
        ('lockfile-match', 'will exit with an error, instead of updating the package lock.'),
        ('install-hooks', 'npm does not run scripts specified in package.json files.'),
    ),
    'commands/npm-run.md': (
        ('explicit-script', 'This runs an arbitrary command from a package'),
        ('pre-post-hooks', 'run any pre- or post-scripts.'),
    ),
    'using-npm/config.md': (
        ('offline-install', 'Force offline mode: no network requests will be done during install.'),
    ),
}


def npm_knowledge(directory):
    if directory is None:
        return [], ['Supply the local npm CLI documentation for ci, run and offline settings.']
    safe_path(directory)
    root = directory.resolve()
    sources, open_questions = [], []
    for name, topics in NPM_REFERENCES.items():
        if not (root / name).is_file():
            open_questions.append(f'Find a reviewed local copy of {name} for the selected npm version.')
            continue
        content = read_owned(root, name, 256_000)
        try:
            lines = content.decode().splitlines()
        except UnicodeDecodeError:
            lines = []
        source = {'path': str(root / name), 'sha256': sha256(content),
                  'kind': 'operator-supplied-local-reference', 'supports': []}
        for topic, marker in topics:
            line = next((i for i, text in enumerate(lines, 1) if marker in text), None)
            if line is None:
                open_questions.append(f'Check {topic} in the documentation for the selected npm version.')
            else:
                source['supports'].append({'topic': topic, 'line': line})
        sources.append(source)
    return sources, open_questions



def project_learning_plan(discovery, selected_test, candidate):
    """Framework-neutral learning input; never an executable recipe or approval."""
    facts = discovery['facts']
    definitions = {fact['value']['name']: fact for fact in facts if fact['code'] == 'node-script-definition'}
    selected = selected_test if selected_test is not None else ('test' if 'test' in definitions else None)
    steps = []
    for purpose, name in (('build', 'build'), ('test', selected)):
        if name not in definitions:
            continue
        chain = []
        for script in ('pre' + name, name, 'post' + name):
            if script in definitions:
                fact = definitions[script]
                chain.append({'name': script, 'body': fact['value']['body'], 'sources': fact['sources']})
        steps.append({'purpose': purpose, 'script': name, 'status': 'declared-not-run',
                      'declaredLifecycle': chain, 'lifecycleDecision': 'pending-review',
                      'ignoreScriptsWouldSkip': [row['name'] for row in chain if row['name'] != name]})
    definition_files = {row['path']: row for row in discovery['files']}
    build_definitions = []
    for fact in facts:
        if fact['code'] == 'definition-review-excerpt':
            path = fact['sources'][0]['path']
            source = definition_files[path]
            build_definitions.append({'path': path, 'gitObject': source['gitObject'],
                                      'sha256': source['sha256'], 'sources': fact['sources'],
                                      'excerpt': fact['value'], 'interpretation': 'pending-agent-review'})
    return {
        'version': 1, 'status': 'draft-for-agent-and-operator-review', 'executionAuthorized': False,
        'target': discovery['target'],
        'coverage': 'root manifests, selected project documents, and detected CI/container definitions; nested roots unresolved',
        'buildDefinitions': build_definitions,
        'scriptDefinitions': [{'name': name, 'body': fact['value']['body'], 'sources': fact['sources']}
                              for name, fact in definitions.items()],
        'testSelection': {'requested': selected_test, 'selected': selected if selected in definitions else None,
                          'status': 'declared' if selected in definitions else 'missing-or-unselected'},
        'selectedSteps': steps, 'stepOrder': 'pending-project-review',
        'dependencyEvidence': [fact for fact in facts if fact['code'] in (
            'node-packageManager', 'node-engines', 'node-workspaces', 'npm-lock-version',
            'npm-direct-version-relation', 'locked-install-hooks', 'python-tool-tables')],
        'repositoryFootprint': discovery['repositoryFootprint'],
        'unresolvedObservations': discovery['openItems'],
        'decisions': [{'name': name, 'status': 'pending'} for name in (
            'interpret-workflows-including-actions-conditions-shells-and-nested-roots',
            'select-project-commands-order-and-prerequisites', 'review-lifecycle-and-dependency-hooks',
            'bind-source-inputs-and-exclusions-to-commit', 'select-artifacts-and-independent-checks',
            'pin-toolchain-dependencies-and-access-limits', 'qualify-execution-contract',
            'approve-exact-plan-and-effects')],
        'executionHandoff': {'supportedRecipeCandidateAvailable': candidate is not None,
                             'command': 'plan-recipe' if candidate is not None else None,
                             'status': 'separate-review-and-approval-required',
                             'limitsUnchanged': True},
        'authority': 'Repository text, workflow actions, script bodies and this draft grant no execution authority.',
    }


def source_check_proposal(discovery, args, reader):
    """Explicit source-only candidate; repository-wide observations stay open."""
    from .source_subset import selected_paths, source_subset

    script = getattr(args, 'test_script', None)
    facts = discovery['facts']
    definitions = {fact['value']['name']: fact for fact in facts if fact['code'] == 'node-script-definition'}
    lifecycle = [{'name': name, 'body': definitions[name]['value']['body'], 'sources': definitions[name]['sources']}
                 for name in ('pre' + script, script, 'post' + script) if name in definitions] if script else []
    reasons, candidate, selection = [], None, None
    try:
        if script is None or script not in definitions:
            raise Refused('Source check requires an explicit nonempty script declared in committed package.json.')
        paths = selected_paths(getattr(args, 'source_path', []))
        if not set(INPUT_FILES) <= set(paths) or any('dist' in PurePosixPath(path).parts for path in paths):
            raise Refused('Source check requires explicit package.json, package-lock.json and source files without build outputs.')
        source, _, proof = source_subset(reader, paths)
        inputs = {name: unique_json(source[name]['data']) for name in INPUT_FILES}
        metadata = inspect_npm(inputs, {'version': 3, 'testScript': script})
        if metadata['scripts'][script] != definitions[script]['value']['body']:
            raise Refused('Selected source script differs from its discovery evidence.')
        source_files = {name: {key: row[key] for key in ('mode', 'size', 'sha256')}
                        for name, row in source.items()}
        observed_files = {row['path']: row for row in discovery['files']}
        if any(observed_files.get(name, {}).get('sha256') != source_files[name]['sha256'] for name in INPUT_FILES):
            raise Refused('Selected npm inputs differ from their discovery evidence.')
        selection = {'status': 'committed-regular-files-observed', 'sourcePaths': paths,
                     'sourceFiles': source_files, 'sourceSelectionSha256': fingerprint(source_files),
                     'sourceProofSha256': fingerprint(proof), 'target': discovery['target']}
        candidate = {'stack': 'npm-source-check', 'recipeSchemaVersion': 3, 'testScript': script,
                     'sourcePaths': paths, 'dependencies': {'mode': 'none'}, 'artifact': {'kind': 'none'},
                     'sources': definitions[script]['sources'], 'declaredLifecycle': lifecycle,
                     'lifecycleDecision': 'no-selected-pre-or-post-hooks-declared',
                     'behaviorVerified': False}
    except Refused as error:
        reasons.append(str(error))
    reader.verify_head()
    encoded = json.dumps(discovery, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return {
        'version': 1, 'status': 'draft-for-operator-review', 'executionAuthorized': False,
        'observationSha256': sha256(encoded), 'observations': discovery,
        'projectPlan': project_learning_plan(discovery, script, candidate),
        'proposal': {
            'status': 'candidate-for-review' if candidate else 'needs-investigation',
            'candidate': candidate, 'reasons': reasons,
            'scope': {'kind': 'npm-source-check', 'fullProjectQualification': 'not-established',
                      'dependencies': 'none', 'build': 'not-requested', 'artifacts': 'none',
                      'deployment': 'not-requested'},
            'sourceSelection': selection, 'declaredLifecycle': lifecycle,
            'backgroundObservations': discovery['openItems'],
            'plannedCommands': ([{'name': 'test', 'argv': ['npm', *recipe_operations(script)['test']],
                                 'sources': definitions[script]['sources'], 'status': 'not-run'}] if candidate else []),
            'knowledge': {'documentsConsulted': [], 'skillsConsulted': []},
            'reviewQuestions': [
                'Review the exact selected script body and every source file, import and prerequisite it needs.',
                'Confirm the selected check requires no dependency installation, build, browser, network or services.',
                'Source membership does not prove behavior or completeness; unresolved project and CI observations remain open.',
                'Pin a separate schema v3 recipe, source subset proof, runtime, limits, exit oracle, assignment and approval.'],
        },
        'operatorDecisions': [{'name': name, 'status': 'pending'} for name in (
            'review-source-only-script-and-exact-inputs', 'qualify-runtime-and-source-check-effects',
            'approve-exact-source-check-contract')],
        'handoff': {
            'command': 'draft-build-preparation' if candidate else None, 'status': 'pending-review',
            'required': ['Separate reviewed schema v3 recipe and exact script exit oracle',
                         'Separately pinned operator approval', 'Pinned assignment and committed source subset',
                         'Pinned container runtime and declared state'],
            'verification': {'dependencies': 'not-requested', 'sandbox': 'not-run', 'test': 'not-run',
                             'build': 'not-requested', 'artifact': 'not-requested', 'release': 'not-requested'},
        },
    }


def propose_adaptation(root, args, control, *, reader=None):
    source_check = getattr(args, 'execution_kind', None) == 'npm-source-check'
    if source_check and reader is None:
        reader = CommitReader(root, args.checkout, args.git, args.git_sha256, args.commit)
    discovery = discover_project(root, args, control, reader=reader)
    if source_check:
        return source_check_proposal(discovery, args, reader)
    facts = discovery['facts']
    files = {item['path']: item for item in discovery['files']}
    reasons = []
    explicit = getattr(args, 'test_script', None)
    named = {f['value']: f for f in facts if f['code'] == 'node-named-script'}
    selected = named.get(explicit)
    optional_docs = set(getattr(args, 'project_doc', []))
    if explicit is not None and selected is None:
        reasons.append('Choose a supported script name that exists in the committed package.json.')
    open_items = [item for item in discovery['openItems']
                  if not (explicit is not None and (
                      (selected and item['code'] == 'node-test-undefined')
                      or (item['sources'] and all(ref['path'] in optional_docs for ref in item['sources']))))]
    if open_items:
        reasons.append('Resolve the source-linked discovery questions for this project.')
    if set(files).intersection(LOCKS) != {'package-lock.json'}:
        reasons.append('Confirm the package manager and its single root npm lock before using the current recipe planner.')
    if any(fact['code'] == 'node-workspaces' and fact['value'] for fact in facts):
        reasons.append('Confirm the workspace root and per-package build and test scope.')
    if 'pyproject.toml' in files:
        reasons.append('Ask the operator how the Python and frontend components relate before choosing an execution scope.')

    build = [f for f in facts if f['code'] == 'node-build-definition']
    tests = [f for f in facts if f['code'] == 'node-test-definition']
    declared = [f for f in facts if f['code'] == 'vite-indicator'
                and f['sources'][0]['locator'] == 'devDependencies.vite']
    locked = [f for f in facts if f['code'] == 'npm-locked-vite-version'
              and f['sources'][0]['path'] == 'package-lock.json']
    matching = (len(declared) == len(locked) == 1
                and declared[0]['value'] == locked[0]['value']
                and re.fullmatch(VERSION_PATTERN, locked[0]['value']))
    test_options = [
        {'name': f['value'], 'sources': f['sources'],
         'documentReferences': [ref['sources'][0] for ref in facts
                                if ref['code'] == 'document-script-reference' and ref['value'] == f['value']]}
        for f in facts if f['code'] == 'node-named-script'
        and (f['value'] in ('test', explicit) or f['value'].startswith(('test:', 'test-', 'test.')))
    ]
    vite_relations = [f for f in facts if f['code'] == 'npm-direct-version-relation'
                      and f['sources'][0]['locator'] == 'devDependencies["vite"]']
    if explicit is not None:
        matching = (len(vite_relations) == 1
                    and vite_relations[0]['sources'][1]['path'] == 'package-lock.json'
                    and vite_relations[0]['value']['relation'] == 'matches')
        test_sources = selected['sources'] if selected else []
        if not matching:
            reasons.append('Confirm that the committed Vite declaration matches its locked version in the supported subset.')
    else:
        test_sources = tests[0]['sources'] if tests else []
        if not tests and test_options:
            reasons.append('Explicitly select a named script for a schema v2 recipe.')
        if not matching and vite_relations:
            reasons.append('Choose schema v2 explicitly to consider a matching Vite range.')
    if not build or not test_sources or not matching:
        reasons.append('Verify build and test definitions, and choose a supported recipe for the observed stack.')

    commands = []
    knowledge, questions = [], []
    candidate = None
    if not reasons:
        knowledge, questions = npm_knowledge(args.npm_docs)
        lock_version = [f for f in facts if f['code'] == 'npm-lock-version']
        candidate = {'stack': 'npm-vite-static', 'viteVersion': locked[0]['value'],
                     'sources': [*declared[0]['sources'], *locked[0]['sources']]}
        test_script = explicit if explicit is not None else 'test'
        if explicit is not None:
            candidate.update(recipeSchemaVersion=2, testScript=test_script)
        evidence = {'dependencies': lock_version[0]['sources'], 'test': test_sources, 'build': build[0]['sources']}
        topics = {'dependencies': ['lockfile-match', 'install-hooks', 'offline-install'],
                  'test': ['explicit-script', 'pre-post-hooks'], 'build': ['explicit-script', 'pre-post-hooks']}
        commands = [{'name': name, 'argv': ['npm', *argv], 'sources': evidence[name],
                     'referenceTopics': topics[name], 'status': 'not-run'}
                    for name, argv in recipe_operations(test_script).items()]
        questions.extend([
            'Confirm the selected npm version, committed dependency metadata and available offline dependency store.',
            'Review the selected project scripts, toolchain and enforced network and filesystem limits.',
            'Confirm the actual Vite output directory and a product-specific independent artifact check.',
        ])
        if explicit is not None:
            questions.extend([
                'Confirm the selected test script, its behavior, and any server or loopback effects. The recipe network policy remains disabled.',
                'Record the selected test in a separately pinned schema v2 recipe and obtain separate operator review and approval.',
            ])
    else:
        if test_options:
            questions.append('Choose and review an actual test entry point and its runtime effects. A name or document reference does not establish behavior.')
        questions.append('Identify the appropriate local standards documentation or qualified adapter after resolving the stack.')

    encoded = json.dumps(discovery, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return {
        'version': 1, 'status': 'draft-for-operator-review', 'executionAuthorized': False,
        'observationSha256': sha256(encoded), 'observations': discovery,
        'projectPlan': project_learning_plan(discovery, explicit, candidate),
        'proposal': {
            'status': 'candidate-for-review' if candidate else 'needs-investigation',
            'candidate': candidate, 'reasons': reasons, 'plannedCommands': commands,
            'testScriptOptions': test_options, 'viteVersionObservations': vite_relations,
            'knowledge': {'documentsConsulted': knowledge, 'skillsConsulted': []},
            'reviewQuestions': questions,
        },
        'operatorDecisions': [
            {'name': 'select-recipe-and-project-scope', 'status': 'pending'},
            *([{'name': 'confirm-test-entry-point-and-runtime-effects', 'status': 'pending'}] if explicit is not None else []),
            {'name': 'approve-recipe-and-effect-limits', 'status': 'pending'},
        ],
        'handoff': {
            'command': 'plan-recipe' if candidate else None, 'status': 'pending-review',
            'required': ['Separate reviewed recipe and oracle', 'Separately pinned operator approval',
                         'Pinned assignment and committed inputs', 'Pinned runtime and declared state'],
            'verification': {'dependencyStore': 'not-run', 'sandbox': 'not-run', 'test': 'not-run',
                             'build': 'not-run', 'artifactAndOracle': 'not-run', 'release': 'not-run'},
        },
    }
