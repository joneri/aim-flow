"""Project-native host npm validation, with exact receipts and no replay.

This is reviewed code with the user's host privileges and normal network access,
not a sandbox. It copies committed candidate inputs, never the dirty checkout.
"""
import base64
import os
from pathlib import Path
import platform
import re
import stat
import time

from . import project_command as command, project_container as artifacts
from . import project_execution as local, project_onboarding as shape, project_validation as planning
from . import control_package as package, project_plan_derivation as derivation
from .effects import EffectJournal
from .owned_paths import Refused, new_owned_directory, read_owned_content
from .project_discovery import unique_json

SPEC = 'aim-flow-project-host-spec'
BOUNDARY = 'Reviewed host process with operator privileges and normal network; not a filesystem/network sandbox. Original checkout is not the execution directory.'


def prepare(root, args):
    handoff = planning.derive(root, args)
    _, spec = planning.pinned(args.execution_spec, args.execution_spec_sha256)
    shape._keys(spec, ('format', 'schemaVersion', 'job', 'sourcePaths', 'tools', 'environment',
        'runtimeReview', 'sourceReview', 'timeoutSeconds', 'outputBytes',
        *(('artifactCapture',) if 'artifactCapture' in spec else ())), 'host execution spec')
    if spec['format'] != SPEC or type(spec['schemaVersion']) is not int or spec['schemaVersion'] != 1:
        raise Refused('Unsupported project host spec')
    jobs = [j for j in handoff['jobs'] if j['id'] == spec['job']]
    if len(jobs) != 1 or jobs[0]['mode'] != 'local-sequence' or jobs[0]['needs']:
        raise Refused('Select one local host npm job without unresolved dependencies')
    for key in ('runtimeReview', 'sourceReview'):
        shape._texts(spec[key], key)
        if not spec[key]: raise Refused('Host runtime and source/effects must be reviewed')
    for key in ('timeoutSeconds', 'outputBytes'):
        if type(spec[key]) is not int or spec[key] <= 0: raise Refused('Positive host execution limits required')
    if not isinstance(spec['tools'], dict) or not {'node', 'npm', 'git'} <= spec['tools'].keys():
        raise Refused('Host tools must include node, npm and git')
    tools = {}
    for name, pin in spec['tools'].items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name): raise Refused('Invalid host tool name')
        tools[name] = command.tool_identity(pin)
    environment = spec['environment']
    if not isinstance(environment, dict): raise Refused('Explicit host environment required')
    for name, value in environment.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
                or name == 'PATH' or not isinstance(value, str) or '\0' in value
                or re.search(r'(?:^|_)(?:TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY)$', name, re.I)):
            raise Refused('Invalid host environment; PATH is tool-derived, no credential values')
    _, instance = planning.pinned(args.instance, args.instance_sha256)
    sources, reader = local.source_selection(root, handoff, instance, spec['sourcePaths'])
    for stage in jobs[0]['stages']:
        declared = handoff['packageRoots'][stage['root']]
        required = [declared['manifest']] + ([declared['lockfile']] if stage['operation'] == 'ci' else [])
        if not set(required) <= sources.keys(): raise Refused('Host stage manifest/lock missing')
        if not stage['argv'] or stage['argv'][0] != 'npm': raise Refused('Only assessed literal npm stages are supported')
    requirements = handoff['runtimes'][jobs[0]['runtime']]['requirements']
    for requirement in requirements:
        if requirement == 'npm named scripts with normal lifecycle hooks': continue
        name, separator, constraint = requirement.partition(' ')
        if name not in ('node', 'npm') or not separator or not constraint:
            raise Refused('Unsupported host runtime requirement: ' + requirement)
    state = shape._outside(command.canonical(str(args.state)), root, Path(handoff['target']['checkout']))
    shape._outside(state, args.instance_controller, Path(handoff['target']['checkout']))
    if 'candidate' in handoff: shape._outside(state, root, Path(handoff['candidate']['baseTarget']['checkout']))
    for path in [args.execution_spec, args.declaration, args.instance, *[Path(t['path']) for t in tools.values()]]:
        if state.is_relative_to(path) or path.is_relative_to(state): raise Refused('Host attempt overlaps pinned input')
    omitted = []
    for name, mode, kind, oid in reader.entries(reader.commit, recursive=True):
        if name not in sources: omitted.append(name)
    intent = {'format': 'aim-flow-project-host-intent', 'schemaVersion': 1,
        'controllerManifestSha256': args.manifest_sha256, 'executionSpecSha256': args.execution_spec_sha256,
        'handoffSha256': local.digest(handoff), 'target': handoff['target'], 'state': str(state),
        'job': jobs[0], 'runtimeRequirements': requirements, 'sources': sources, 'generatedSourcePaths': sorted(omitted), 'tools': tools,
        'environment': {**environment, 'PATH': str(state / 'bin')},
        'host': {'system': platform.system(), 'machine': platform.machine()},
        **{k: spec[k] for k in ('runtimeReview', 'sourceReview', 'timeoutSeconds', 'outputBytes')},
        'network': 'Normal project host network; no offline override', 'executionBoundary': BOUNDARY,
        'excludedCoverage': handoff['excludedCoverage'], 'review': handoff['review'],
        'agentUnknowns': handoff['agentUnknowns']}
    if 'candidate' in handoff: intent['candidate'] = handoff['candidate']
    if 'artifactCapture' in spec:
        intent['artifactCapture'] = artifacts.artifact_capture(spec['artifactCapture'], sources, spec['outputBytes'])
    return intent, reader


def source_snapshot(project, sources):
    result = {}
    for name, expected in sources.items():
        raw = read_owned_content(project, name)
        mode = stat.S_IMODE((project / name).lstat().st_mode)
        if (package.sha256(raw) != expected['sha256'] or len(raw) != expected['bytes']
                or mode != int(expected['mode'], 8) & 0o777):
            raise Refused('Host project stage changed selected source: ' + name)
        result[name] = {k: expected[k] for k in ('sha256', 'mode', 'bytes')}
    return result


def outputs(project, intent, report):
    contract = intent.get('artifactCapture')
    names = set(intent['generatedSourcePaths'])
    if contract: names.update(row['path'] for row in contract['files'])
    # Enforce selected artifact limits before reading even the inventory bytes.
    if contract:
        sizes = []
        for row in contract['files']:
            path = project / row['path']
            if not path.exists(): continue
            size = path.stat().st_size
            if size > contract['maxFileBytes']: raise Refused('Host artifact exceeds declared file limit')
            sizes.append(size)
        if sum(sizes) > contract['maxTotalBytes']: raise Refused('Host artifacts exceed declared total limit')
    measured = {}
    retained = []
    total = 0
    for name in sorted(names):
        shape._source_path(name)
        path = project / name
        if not path.exists(): continue
        raw = read_owned_content(project, name)
        measured['project/' + name] = {'type': 'file', 'bytes': len(raw), 'sha256': package.sha256(raw),
            'mode': stat.S_IMODE(path.lstat().st_mode)}
    report['outputs'] = measured
    if contract:
        for row in contract['files']:
            name = row['path']; path = project / name
            if path.stat().st_size > contract['maxFileBytes']: raise Refused('Host artifact exceeds declared file limit')
            raw = read_owned_content(project, name); total += len(raw)
            if total > contract['maxTotalBytes']: raise Refused('Host artifacts exceed declared total limit')
            retained.append({**row, 'bytes': len(raw), 'sha256': package.sha256(raw),
                'base64': base64.b64encode(raw).decode()})
        artifacts.validate_artifacts(intent, {**report, 'artifacts': retained}, required=True)
        report['artifacts'] = retained


def check_tools(intent, state):
    for name, pin in intent['tools'].items():
        command.tool_identity(pin)
        link = state / 'bin' / name
        if not link.is_symlink() or os.readlink(link) != pin['path']:
            raise Refused('Host tool binding changed')
    if set(p.name for p in (state / 'bin').iterdir()) != set(intent['tools']):
        raise Refused('Host tool directory changed')


def check_runtime(intent, runtime):
    observed = {'node': runtime['actualNodeVersion'].removeprefix('v'), 'npm': runtime['actualNpmVersion']}
    for name, version in observed.items():
        if not derivation.version_satisfies(version, '>=0'):
            raise Refused('Host runtime version could not be qualified: ' + name)
    for requirement in intent['runtimeRequirements']:
        if requirement == 'npm named scripts with normal lifecycle hooks': continue
        name, _, constraint = requirement.partition(' ')
        if not derivation.version_satisfies(observed.get(name), constraint):
            raise Refused('Actual host runtime does not satisfy source requirement: ' + requirement)


def execute(root, args, intent, reader):
    state = args.state; project = state / 'project'
    command.durable_write(state, 'started.json', shape.encode({'intentSha256': local.digest(intent), 'replayAuthorized': False}))
    project.mkdir(mode=0o700); (state / 'bin').mkdir(mode=0o700)
    for name, pin in intent['tools'].items(): (state / 'bin' / name).symlink_to(pin['path'])
    for name, source in intent['sources'].items():
        path = project / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = reader.blob(source['gitObject'])
        if package.sha256(raw) != source['sha256']: raise Refused('Source changed before host materialization')
        with path.open('xb') as stream: stream.write(raw)
        path.chmod(int(source['mode'], 8) & 0o777)
    report = {'format': 'aim-flow-project-host-report', 'status': 'failed',
        'sourcesBefore': source_snapshot(project, intent['sources']), 'stages': [], 'runtime': {},
        'outputs': {}, 'inventoryScope': 'Declared artifact files and omitted committed generated outputs only; not all dependency/cache bytes.',
        'ready': False, 'deployment': 'not-authorized', 'acceptance': 'not-established'}
    started = time.monotonic(); consumed = 0
    def run(argv, cwd, prefix):
        nonlocal consumed
        remaining = intent['timeoutSeconds'] - (time.monotonic() - started)
        if remaining <= 0 or consumed >= intent['outputBytes']: raise Refused('Declared host execution budget exhausted')
        observation = command.capture(argv, cwd, intent['environment'], state, prefix,
            remaining, intent['outputBytes'] - consumed)
        text = {}
        for key, pin in observation['logs'].items():
            raw = read_owned_content(state, pin['path']); consumed += len(raw)
            text[key] = raw.decode('utf-8', errors='replace')
        return {**observation, **text}
    try:
        probes = {}
        for name, argv in [('node', [intent['tools']['node']['path'], '--version']),
                           ('npm', [intent['tools']['node']['path'], intent['tools']['npm']['path'], '--version'])]:
            probe = run(argv, project, 'runtime-' + name); probes[name] = probe
            if probe['exitCode'] != 0 or probe['termination'] is not None: raise Refused('Host runtime probe failed')
        report['runtime'] = {'nodePath': intent['tools']['node']['path'], 'npmPath': intent['tools']['npm']['path'],
            'actualNodeVersion': probes['node']['stdout'].strip(), 'actualNpmVersion': probes['npm']['stdout'].strip(),
            'host': intent['host'], 'probes': probes, 'network': intent['network']}
        check_runtime(intent, report['runtime'])
        for i, stage in enumerate(intent['job']['stages']):
            check_tools(intent, state)
            cwd = project if stage['root'] == '.' else project / stage['root']
            argv = [intent['tools']['node']['path'], intent['tools']['npm']['path'], *stage['argv'][1:]]
            observed = run(argv, cwd, 'stage-' + str(i))
            row = {**observed, 'index': i, 'root': stage['root'], 'projectArgv': stage['argv']}
            report['stages'].append(row)
            row['sourcesAfter'] = source_snapshot(project, intent['sources'])
            if observed['exitCode'] != 0 or observed['termination'] is not None: raise Refused('Host project stage failed: ' + str(i))
        check_tools(intent, state)
        current, _ = prepare(root, args)
        if current != intent: raise Refused('Original source, tools or declared inputs changed during host execution')
        report['sourcesAfter'] = source_snapshot(project, intent['sources'])
        outputs(project, intent, report)
        report['status'] = 'passed'
    except (Refused, ValueError, OSError) as error:
        report['failure'] = str(error)
        # Failed observations stay useful, but incomplete artifacts never imply acceptance.
        try: outputs(project, intent, report)
        except (Refused, ValueError, OSError) as output_error: report['artifactFailure'] = str(output_error)
    command.durable_write(state, 'report.json', shape.encode(report))
    evidence = {'report.json': package.sha256(read_owned_content(state, 'report.json'))}
    for path in sorted(state.glob('*.stdout')) + sorted(state.glob('*.stderr')):
        evidence[path.name] = package.sha256(read_owned_content(state, path.name))
    return {'format': 'aim-flow-project-host-result', 'schemaVersion': 1, 'status': report['status'],
        'intentSha256': local.digest(intent), 'job': intent['job']['id'], 'report': report,
        'controlFiles': evidence, 'executionBackend': 'host', 'ready': False,
        'acceptance': 'not-established', 'deployment': 'not-authorized', 'replayAuthorized': False,
        'limitations': [BOUNDARY, 'Process exit and recorded artifacts require independent feature review.']}


def verify(state, intent, pin):
    raw = read_owned_content(state, 'result.json'); package.check_digest(raw, pin, 'Host result')
    result = unique_json(raw)
    if (unique_json(read_owned_content(state, 'intent.json')) != intent
            or result['intentSha256'] != local.digest(intent)
            or unique_json(read_owned_content(state, 'started.json')) != {'intentSha256': local.digest(intent), 'replayAuthorized': False}):
        raise Refused('Host execution intent or dispatch binding differs')
    if EffectJournal(state / 'receipt-journal', intent, read_only=True).completed_result('project-host', intent) != result:
        raise Refused('Host execution journal differs')
    for name, sha in result['controlFiles'].items():
        shape._source_path(name); package.check_digest(read_owned_content(state, name), sha, 'Host evidence')
    report = unique_json(read_owned_content(state, 'report.json'))
    if report != result['report']: raise Refused('Host report changed')
    expected = {n: {k: r[k] for k in ('sha256', 'mode', 'bytes')} for n, r in intent['sources'].items()}
    passed = (not report.get('failure') and report.get('sourcesBefore') == expected
        and report.get('sourcesAfter') == expected and len(report['stages']) == len(intent['job']['stages'])
        and all(row.get('index') == i and row.get('root') == stage['root'] and row.get('projectArgv') == stage['argv']
            and row.get('argv') == [intent['tools']['node']['path'], intent['tools']['npm']['path'], *stage['argv'][1:]]
            and row.get('exitCode') == 0 and row.get('termination') is None and row.get('sourcesAfter') == expected
            for i, (row, stage) in enumerate(zip(report['stages'], intent['job']['stages']))))
    if passed: check_runtime(intent, report['runtime'])
    if result['status'] != ('passed' if passed else 'failed') or report['status'] != result['status']:
        raise Refused('Host completion claim differs from observations')
    artifacts.validate_artifacts(intent, report, required=passed)
    return {**result, 'receipt': str(state / 'result.json'), 'receiptSha256': pin}


def execution(root, args):
    intent, reader = prepare(root, args)
    if args.action == 'verify-project-host-validation': return verify(args.state, intent, args.receipt_sha256)
    if args.preview:
        return {'status': 'host-execution-preview', 'intentSha256': local.digest(intent), 'intent': intent, 'executionAuthorized': False}
    if args.authorize_intent != local.digest(intent): raise Refused('Exact host execution authorization required')
    with new_owned_directory(args.state):
        command.durable_write(args.state, 'intent.json', shape.encode(intent))
        journal = EffectJournal(args.state / 'receipt-journal', intent)
        from openai_budget import fingerprint
        def dispatch(key, request):
            try: result = execute(root, args, request, reader)
            except BaseException as error:
                command.durable_write(args.state, 'failure.json', shape.encode({'status': 'outcome-unknown',
                    'failureClass': type(error).__name__, 'replayAuthorized': False}))
                raise
            return EffectJournal.receipt(key, fingerprint(request), result)
        result = journal.perform('project-host', intent, dispatch)
        command.durable_write(args.state, 'result.json', shape.encode(result))
    return verify(args.state, intent, package.sha256(read_owned_content(args.state, 'result.json')))
