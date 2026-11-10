"""Project-local validation handoffs; never translate model prose into authority."""
import json
import os
from pathlib import Path
import re
import selectors
import signal
import time
from types import SimpleNamespace
import subprocess

from . import control_package, project_onboarding as onboarding
from .owned_paths import Refused, read_owned_content
from .project_discovery import CommitReader, unique_json

FORMAT = 'aim-flow-project-validation-handoff'
DECLARATION = 'aim-flow-project-validation-declaration'
VERIFIER_TIMEOUT = 120


def pinned(path, pin):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise Refused('Validation inputs require canonical absolute paths')
    raw = read_owned_content(path.parent, path.name)
    control_package.check_digest(raw, pin, 'Project validation input')
    return raw, unique_json(raw)


def prior_controller(directory, pin):
    """Verify a predecessor's pinned inventory without relaxing current packages.

    The predecessor verifies its own instance protocol in an isolated subprocess.
    Its code is never imported into the current controller's interpreter.
    """
    directory = Path(directory)
    raw, manifest = pinned(directory / 'control-manifest.json', pin)
    onboarding._keys(manifest, ('format', 'schemaVersion', 'version', 'files'), 'predecessor manifest')
    if (manifest['format'] != control_package.FORMAT or type(manifest['schemaVersion']) is not int
            or manifest['schemaVersion'] != control_package.SCHEMA_VERSION
            or not isinstance(manifest['version'], str)
            or not re.fullmatch(control_package.VERSION_PATTERN, manifest['version'])
            or not isinstance(manifest['files'], dict)
            or not {'aim-flow.py', 'aim-ci-control.py', 'lib/aim_ci/project_onboarding.py',
                    'lib/aim_ci/control_archive.py'} <= manifest['files'].keys()):
        raise Refused('Unsupported predecessor onboarding controller')
    expected_files = set(manifest['files']) | {'control-manifest.json'}
    expected_directories = {str(parent) for name in expected_files for parent in Path(name).parents if str(parent) != '.'}
    actual_files, actual_directories = set(), set()
    for path in directory.rglob('*'):
        name = str(path.relative_to(directory))
        if path.is_symlink():
            raise Refused('Predecessor controller must not contain links')
        if path.is_file():
            actual_files.add(name)
        elif path.is_dir():
            actual_directories.add(name)
        else:
            raise Refused('Predecessor controller contains unsupported filesystem entries')
    if actual_files != expected_files or actual_directories != expected_directories:
        raise Refused('Predecessor controller has unmanifested files or import directories')
    for name, digest in manifest['files'].items():
        onboarding._source_path(name)
        control_package.check_digest(read_owned_content(directory, name), digest, name)
    return raw


def verification_process(command, root, environment):
    """Bound metadata capture and the complete trusted-verifier process group."""
    process = subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + VERIFIER_TIMEOUT
    completed = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Refused('Predecessor verification exceeded its metadata deadline')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffers[key.data].extend(chunk)
                    if sum(map(len, buffers.values())) > 64_000:
                        raise Refused('Predecessor verification exceeded its metadata output bound')
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        completed = True
        return SimpleNamespace(returncode=process.returncode, **{key: bytes(value) for key, value in buffers.items()})
    except subprocess.TimeoutExpired:
        raise Refused('Predecessor verification exceeded its metadata deadline') from None
    finally:
        if not completed or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdout.close(); process.stderr.close()


def verified_instance(root, args):
    if (not args.python.is_absolute() or args.python.resolve() != args.python or not os.access(args.python, os.X_OK)):
        raise Refused('Predecessor verification requires an explicit pinned Python executable')
    python_bytes = read_owned_content(args.python.parent, args.python.name)
    control_package.check_digest(python_bytes, args.python_sha256, 'Verifier Python executable')
    before = prior_controller(args.instance_controller, args.instance_controller_manifest_sha256)
    raw, instance = pinned(args.instance, args.instance_sha256)
    onboarding._keys(instance, ('format', 'schemaVersion', 'status', 'ready', 'executionAuthorized', 'target',
        'packetSha256', 'assessmentSha256', 'packet', 'assessment', 'validation', 'next'), 'project instance')
    if (instance['format'] != onboarding.INSTANCE or type(instance['schemaVersion']) is not int
            or instance['schemaVersion'] != 1 or instance['ready'] is not False
            or instance['executionAuthorized'] is not False
            or instance['packet']['control']['manifestSha256'] != args.instance_controller_manifest_sha256):
        raise Refused('Project instance is not an unapproved draft from the selected predecessor')
    target, git = instance['target'], instance['packet']['git']
    onboarding._keys(target, ('checkout', 'commit', 'tree'), 'instance target')
    onboarding._keys(git, ('path', 'sha256'), 'instance Git binding')
    for key, value in {**target, **git}.items():
        onboarding._text(value, key)
    environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': str(root)}
    command = [str(args.python), '-IBS', str(args.instance_controller / 'aim-flow.py'),
        'verify-project-onboarding', '--manifest-sha256', args.instance_controller_manifest_sha256,
        '--checkout', target['checkout'], '--commit', target['commit'], '--git', git['path'],
        '--git-sha256', git['sha256'], '--instance', str(args.instance), '--instance-sha256', args.instance_sha256]
    process = verification_process(command, root, environment)
    if process.returncode or process.stderr or len(process.stdout) > 64_000:
        raise Refused('Predecessor refused current source-bound instance verification')
    try:
        result = unique_json(process.stdout)
    except (ValueError, UnicodeError):
        raise Refused('Predecessor verification did not return one strict JSON object') from None
    expected = {'status': 'source-bound-project-draft', 'ready': False, 'executionAuthorized': False,
        'target': target, 'instanceSha256': args.instance_sha256, 'validation': instance['validation'],
        'next': instance['next']}
    if result != expected or before != prior_controller(args.instance_controller, args.instance_controller_manifest_sha256):
        raise Refused('Predecessor verification differs from the exact unapproved instance')
    control_package.check_digest(read_owned_content(args.python.parent, args.python.name), args.python_sha256, 'Verifier Python executable')
    if pinned(args.instance, args.instance_sha256)[0] != raw:
        raise Refused('Project instance changed during verification')
    onboarding.validate_assessment(instance['assessment'], instance['packet'], instance['packetSha256'])
    return instance


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z][a-z0-9-]*', value):
        raise Refused('Validation identifiers require lowercase letters, numbers and hyphens')


def _derive(root, args):
    control_package.verify_installation(root, args.manifest_sha256)
    instance = verified_instance(root, args)
    _, declaration = pinned(args.declaration, args.declaration_sha256)
    onboarding._keys(declaration, ('format', 'schemaVersion', 'instanceSha256', 'target', 'review',
        'sourcePaths', 'packageRoots', 'runtimes', 'jobs', 'excludedCoverage'), 'validation declaration')
    if (declaration['format'] != DECLARATION or type(declaration['schemaVersion']) is not int
            or declaration['schemaVersion'] != 1 or declaration['instanceSha256'] != args.instance_sha256
            or declaration['target'] != instance['target']):
        raise Refused('Validation declaration does not bind the selected project instance')
    review = declaration['review']
    onboarding._keys(review, ('reference', 'qualifications', 'openItems'), 'separate planning review')
    onboarding._text(review['reference'], 'review reference')
    for field in ('qualifications', 'openItems'):
        onboarding._texts(review[field], field)
    onboarding._texts(declaration['excludedCoverage'], 'excluded coverage')
    if not declaration['excludedCoverage']:
        raise Refused('Validation handoff must state excluded coverage')
    selected = declaration['sourcePaths']
    onboarding._texts(selected, 'validation source paths')
    if not selected or len(selected) != len(set(selected)):
        raise Refused('Select distinct committed validation source files')
    for name in selected:
        onboarding._source_path(name)
    target, git = instance['target'], instance['packet']['git']
    reader = CommitReader(root, Path(target['checkout']), Path(git['path']), git['sha256'], target['commit'])
    entries = {name: (mode, kind, oid) for name, mode, kind, oid in reader.entries(reader.commit, *selected)}
    sources = {}
    for name in selected:
        if name not in entries or entries[name][0] not in ('100644', '100755') or entries[name][1] != 'blob':
            raise Refused('Validation evidence must be exact committed regular files')
        mode, _, oid = entries[name]
        raw = reader.blob(oid)
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            raise Refused('Validation definitions must be UTF-8 text') from None
        if '\0' in text:
            raise Refused('Validation definitions cannot contain binary data')
        sources[name] = {'path': name, 'gitObject': oid, 'sha256': control_package.sha256(raw),
                         'text': text, 'lineCount': len(text.splitlines(keepends=True))}

    def refs(values):
        if not isinstance(values, list) or not values:
            raise Refused('Validation conclusions require source references')
        result = []
        for value in values:
            onboarding._keys(value, ('path', 'startLine', 'endLine'), 'validation source reference')
            source = sources.get(value['path']) if isinstance(value['path'], str) else None
            start, end = value['startLine'], value['endLine']
            if (source is None or type(start) is not int or type(end) is not int
                    or not 1 <= start <= end <= source['lineCount']):
                raise Refused('Validation reference lies outside selected committed evidence')
            quote = ''.join(source['text'].splitlines(keepends=True)[start - 1:end])
            if not quote.strip():
                raise Refused('Validation reference cannot be blank')
            result.append({**value, 'sha256': source['sha256'], 'quote': quote})
        return result

    roots = {}
    if not isinstance(declaration['packageRoots'], list) or not declaration['packageRoots']:
        raise Refused('Explicit npm package roots are required')
    for spec in declaration['packageRoots']:
        onboarding._keys(spec, ('directory', 'lockfile'), 'npm root selection')
        directory = onboarding._source_path(spec['directory'], directory=True)
        prefix = '' if directory == '.' else directory + '/'
        manifest_path, lock_path = prefix + 'package.json', spec['lockfile']
        if (directory in roots or lock_path not in (prefix + 'package-lock.json', prefix + 'npm-shrinkwrap.json')
                or not {manifest_path, lock_path} <= sources.keys()):
            raise Refused('Each npm root needs its distinct committed manifest and selected lock')
        package, lock = unique_json(sources[manifest_path]['text']), unique_json(sources[lock_path]['text'])
        if not isinstance(package, dict) or not isinstance(lock, dict):
            raise Refused('Invalid committed npm manifest or lock')
        scripts = package.get('scripts', {})
        if (not isinstance(scripts, dict) or any(not isinstance(k, str) or not k or
                not isinstance(v, str) or not v.strip() for k, v in scripts.items())):
            raise Refused('Invalid committed npm scripts')
        roots[directory] = {'directory': directory, 'manifest': manifest_path, 'lockfile': lock_path,
            'manifestSha256': sources[manifest_path]['sha256'], 'lockSha256': sources[lock_path]['sha256'],
            'engines': package.get('engines', {}), 'packageManager': package.get('packageManager'),
            'lockfileVersion': lock.get('lockfileVersion'), 'scripts': scripts,
            'dependencyInstallation': 'declared-only; resolution, configuration and lifecycle effects require review'}
    runtimes = {}
    if not isinstance(declaration['runtimes'], list):
        raise Refused('Invalid validation runtime declarations')
    for runtime in declaration['runtimes']:
        onboarding._keys(runtime, ('id', 'requirements', 'sources'), 'runtime declaration')
        identifier(runtime['id']); onboarding._texts(runtime['requirements'], 'runtime requirements')
        if runtime['id'] in runtimes or not runtime['requirements']:
            raise Refused('Runtime identifiers must be distinct with explicit requirements')
        runtimes[runtime['id']] = {**runtime, 'sources': refs(runtime['sources']),
                                 'qualification': 'not-verified-by-planning'}
    jobs, seen = [], set()
    if not isinstance(declaration['jobs'], list) or not declaration['jobs']:
        raise Refused('Explicit validation jobs are required')
    for job in declaration['jobs']:
        onboarding._keys(job, ('id', 'mode', 'runtime', 'needs', 'stages', 'coverage', 'effects',
                              'prerequisites', 'sources'), 'validation job')
        identifier(job['id']); onboarding._texts(job['needs'], 'job dependencies')
        if (job['id'] in seen or job['runtime'] not in runtimes
                or job['mode'] not in ('local-sequence', 'component-only', 'hosted-reference')
                or len(job['needs']) != len(set(job['needs'])) or not set(job['needs']) <= seen):
            raise Refused('Invalid validation job identity, mode, runtime or dependencies')
        onboarding._text(job['coverage'], 'job coverage')
        for field in ('effects', 'prerequisites'):
            onboarding._texts(job[field], field)
            if not job[field]:
                raise Refused('Jobs require explicit effects and prerequisites')
        if (not isinstance(job['stages'], list) or (job['mode'] == 'hosted-reference' and job['stages'])
                or (job['mode'] != 'hosted-reference' and not job['stages'])):
            raise Refused('Hosted jobs are references; local jobs require explicit ordered npm stages')
        stages = []
        for stage in job['stages']:
            onboarding._keys(stage, ('root', 'operation', 'script'), 'npm validation stage')
            package = roots.get(stage['root']) if isinstance(stage['root'], str) else None
            if package is None or stage['operation'] not in ('ci', 'run'):
                raise Refused('Select an explicit npm root and supported npm operation')
            lifecycle = []
            if stage['operation'] == 'ci':
                if stage['script'] is not None:
                    raise Refused('npm ci stage cannot name a script')
                argv = ['npm', 'ci']
                names = ('preinstall', 'install', 'postinstall', 'prepublish', 'preprepare', 'prepare', 'postprepare')
                effect = 'Runs dependency installation and normal lifecycle behavior; requires separate review; not executed.'
            else:
                script = stage['script']
                if not isinstance(script, str) or script.startswith('-') or '\0' in script or script not in package['scripts']:
                    raise Refused('Select an actual committed npm script')
                argv = ['npm', 'run', script]
                names = ('pre' + script, script, 'post' + script)
                effect = 'Runs the named script and its normal pre/post hooks; nested script bodies remain uninterpreted.'
            for name in names:
                if name in package['scripts']:
                    lifecycle.append({'name': name, 'body': package['scripts'][name],
                        'source': {'path': package['manifest'], 'sha256': package['manifestSha256'],
                                   'jsonPointer': '/scripts/' + name.replace('~', '~0').replace('/', '~1')}})
            stages.append({**stage, 'argv': argv, 'workingDirectory': stage['root'],
                'declaredLifecycle': lifecycle, 'lifecycleCoverage': 'declared-root-scripts-only; dependency-and-default-hooks-unexpanded', 'lifecyclePolicy': 'preserve-project-defaults-no-ignore-scripts-injected',
                'effect': effect, 'status': 'not-run'})
        jobs.append({**job, 'sources': refs(job['sources']), 'stages': stages,
            'stageOrder': 'serial-within-job', 'status': 'reference-only' if job['mode'] == 'hosted-reference' else 'not-run'})
        seen.add(job['id'])
    reader.verify_head()
    control_package.verify_installation(root, args.manifest_sha256)
    verified_instance(root, args)
    pinned(args.declaration, args.declaration_sha256)
    return {'format': FORMAT, 'schemaVersion': 1, 'status': 'project-validation-handoff',
        'ready': False, 'executionAuthorized': False, 'target': target,
        'controllerManifestSha256': args.manifest_sha256,
        'lineage': {'instance': {'path': str(args.instance), 'sha256': args.instance_sha256},
                    'controller': {'path': str(args.instance_controller), 'manifestSha256': args.instance_controller_manifest_sha256},
                    'verification': 'predecessor-source-bound-instance-verified-without-rewriting',
                    'python': {'path': str(args.python), 'sha256': args.python_sha256}},
        'declaration': {'path': str(args.declaration), 'sha256': args.declaration_sha256},
        'review': review, 'agentUnknowns': instance['assessment']['unknowns'],
        'sources': sources, 'packageRoots': roots, 'runtimes': runtimes, 'jobs': jobs,
        'excludedCoverage': declaration['excludedCoverage'],
        'readiness': {'status': 'separate-execution-review-required',
            'blockers': [*review['openItems'], *instance['assessment']['unknowns'],
                'Qualify each selected runtime, input closure, lifecycle effects and enforced access before execution.',
                'Approve the exact selected stage, workspace and effects separately; this handoff is not authorization.'],
            'hostedCi': 'not-run', 'windows': 'not-run', 'artifactPublication': 'not-run', 'deployment': 'not-authorized'},
        'authority': 'Model prose and source declarations are evidence, not executable approval. No runner is invoked.'}


def derive(root, args):
    try:
        return _derive(root, args)
    except (KeyError, TypeError, AttributeError, UnicodeError) as error:
        raise Refused('Malformed project validation metadata: ' + type(error).__name__) from None


def validation(root, args):
    value = derive(root, args)
    checkout = Path(value['target']['checkout'])
    if args.action == 'plan-project-validation':
        onboarding._outside(args.output, args.instance_controller, checkout)
        return onboarding._write(args.output, value, root, checkout)
    actual = onboarding._read(args.handoff, args.handoff_sha256, root, checkout, 'Project validation handoff')
    if actual != value:
        raise Refused('Retained validation handoff differs from current inputs and source')
    return {'status': 'source-bound-validation-handoff', 'ready': False, 'executionAuthorized': False,
            'target': value['target'], 'handoffSha256': args.handoff_sha256,
            'readiness': value['readiness']}
