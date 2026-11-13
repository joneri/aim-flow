"""Public planning facts for one pinned container attempt, never qualification.

No environment is inherited or executed here. Secret/unclassified application
values are withheld, not replaced; their names remain present and unresolved.
"""
import copy
import re

from aim_ci import project_onboarding as onboarding
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused

FORMAT = 'aim-flow-review-execution-context'
BOUNDARY = {
    'authority': 'planning-only; no execution, acceptance, publication or deployment authority',
    'qualification': 'pending; catalog and preparation records are claims, not measured execution',
    'network': 'none; private loopback only; no host mounts or published ports',
    'isolation': 'nonroot; read-only image baseline; dropped capabilities; no-new-privileges',
    'sourceCopies': 'private tmpfs copies may change during a stage; exact content, size and mode must match every stage boundary and at completion',
    'sourceMetadata': 'timestamps are not source identity; changing private-copy timestamps is not a persistent source edit',
    'originalCheckout': 'not mounted or modified',
    'processes': 'stage process-group cleanup and private-namespace drain; deadline or failed cleanup never counts as passing',
    'npm': 'offline cache; normal lifecycle hooks; empty user/global npm configuration; committed project npm configuration must pass adapter admission',
    'limitations': 'containment and exit status do not prove dependency behavior, browser operation, source correctness or acceptance',
}
ENVIRONMENT_SCOPE = ('Exact initial stage-parent environment only. Npm and project code may derive or mutate their own environment; '
                     'inspect displayed code before resolving an output path. A withheld value is present, not absent.')
FIXED_ENVIRONMENT = {
    'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/work/home', 'XDG_CONFIG_HOME': '/work/home/.config',
    'TMPDIR': '/work/tmp', 'LANG': 'C', 'LC_ALL': 'C', 'NODE_DISABLE_COMPILE_CACHE': '1',
    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/work/git.config', 'GIT_TEMPLATE_DIR': '/work/empty-template',
    'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'core.hooksPath', 'GIT_CONFIG_VALUE_0': '/work/empty-hooks',
    'GIT_CONFIG_KEY_1': 'commit.gpgsign', 'GIT_CONFIG_VALUE_1': 'false', 'GIT_TERMINAL_PROMPT': '0',
    'npm_config_userconfig': '/work/user.npmrc', 'npm_config_globalconfig': '/work/global.npmrc',
    'npm_config_cache': '/work/cache', 'npm_config_offline': 'true', 'npm_config_audit': 'false',
    'npm_config_fund': 'false', 'npm_config_update_notifier': 'false',
}
SENSITIVE = re.compile(r'(?i:secret|password|passwd|token|credential|authorization|cookie|private|(?:^|_)key(?:_|$))')
SAFE_PATH = re.compile(r'(?:/(?:work|opt|usr)(?:/[A-Za-z0-9_.-]+)*|/dev/null)')


def safe_value(name, value):
    """Deliberately narrow disclosure; arbitrary strings/URLs/host paths stay local."""
    if SENSITIVE.search(name) or SENSITIVE.search(value) or len(value) > 256:
        return False
    if value in ('', '0', '1', 'true', 'false'):
        return True
    # Container-local paths and a single tool option with such a path. Never a
    # home path, URL, opaque identifier, free-form operator text or secret value.
    path = value[3:] if re.match(r'^-[A-Za-z] ', value) else value
    return bool(SAFE_PATH.fullmatch(path)) and all(p not in ('.', '..') for p in path.split('/'))


def environment(application, version):
    if not isinstance(application, dict):
        raise Refused('Invalid review application environment')
    values = dict(FIXED_ENVIRONMENT)
    withheld = []
    for name, value in sorted(application.items()):
        if (not isinstance(name, str) or not re.fullmatch('[A-Z][A-Z0-9_]*', name)
                or not isinstance(value, str) or '\0' in value
                or name in ('PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'SHELL', 'ENV', 'BASH_ENV', 'ZDOTDIR', 'LANG', 'LC_ALL', 'XDG_CONFIG_HOME')
                or name.startswith(('NODE_', 'NPM_', 'GIT_', 'DYLD_', 'LD_', 'PYTHON', 'FLOW_'))):
            raise Refused('Application settings cannot override runtime/security configuration')
        if safe_value(name, value):
            values[name] = value
        else:
            withheld.append(name)
    if version == 2:
        values['npm_config_loglevel'] = 'info'
    return {'inheritsHostEnvironment': False, 'values': values, 'withheldNames': withheld, 'scope': ENVIRONMENT_SCOPE}


def boundary(policy=None):
    result = copy.deepcopy(BOUNDARY)
    if policy is not None:
        container.source_read_mode_policy(policy)
        result['sourceCopies'] += ('; explicit adapter policy may restore lost group/other read bits '
            'only for byte-identical, size-identical non-executable 0644 inputs observed as 0600/0604/0640, '
            'after process cleanup and before the boundary check; every restoration is recorded. '
            'Content, owner permissions, write bits, execute bits and special bits are never repaired or ignored')
    return result


HOST_BOUNDARY = {
    'authority': 'planning-only; no execution, acceptance, publication or deployment authority',
    'qualification': 'pending; catalog claims are not measured execution',
    'network': 'normal host network access; existing online project dependencies may be contacted',
    'isolation': 'NOT a security sandbox; project code runs as the current host user with normal host access',
    'sourceCopies': 'private committed-source copy; selected source bytes and modes checked at stage boundaries',
    'sourceMetadata': 'timestamps are not source identity',
    'originalCheckout': 'not the working directory and not intentionally modified; no sandbox access barrier is claimed',
    'processes': 'owned stage process-group cleanup; no container namespace isolation',
    'npm': 'normal project npm lifecycle and networking with the explicit environment; no imposed offline mode',
    'limitations': 'source checks and exit status do not establish host containment, dependency correctness or acceptance',
}


def host_environment(application):
    if not isinstance(application, dict): raise Refused('Host environment must be explicit')
    values, withheld = {}, []
    for name, value in sorted(application.items()):
        if (not isinstance(name, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name)
                or not isinstance(value, str) or '\0' in value):
            raise Refused('Invalid host environment entry')
        # Host home/cache paths remain local. Safe values retain exact semantics;
        # withheld names are present, never represented as absent or sanitized.
        safe_path_list = name == 'PATH' and all(safe_value(name, x) for x in value.split(':'))
        if safe_value(name, value) or value in ('C', 'C.UTF-8', 'en_US.UTF-8') or safe_path_list:
            values[name] = value
        else:
            withheld.append(name)
    if 'PATH' not in application:
        withheld.append('PATH')  # Adapter derives its private exact-pinned-tool directory.
    return {'inheritsHostEnvironment': False, 'values': values, 'withheldNames': sorted(withheld), 'scope': ENVIRONMENT_SCOPE}


def build_host(candidate, catalog_pin, resource, spec, declaration, helper_sha):
    onboarding._keys(spec, ('format', 'schemaVersion', 'job', 'sourcePaths', 'tools', 'environment',
        'runtimeReview', 'sourceReview', 'timeoutSeconds', 'outputBytes',
        *(('artifactCapture',) if 'artifactCapture' in spec else ())), 'host execution template')
    if spec['format'] != 'aim-flow-project-host-spec' or spec['schemaVersion'] != 1:
        raise Refused('Unsupported host execution template')
    for key in ('runtimeReview', 'sourceReview'):
        onboarding._texts(spec[key], key)
        if not spec[key]: raise Refused('Host source/runtime review required')
    caps = resource.get('capabilities', {})
    result = {'format': FORMAT, 'schemaVersion': 2, 'adapter': 'project-host', 'hostSpecSchemaVersion': 1,
        'candidate': {k: candidate[k] for k in ('commit', 'tree')},
        'bindings': {'resourceCatalogSha256': catalog_pin['sha256'],
            'executionSpecSha256': resource['executionSpec']['sha256'], 'runnerSha256': helper_sha},
        'runtimeClaims': {'os': caps.get('platform'), 'architecture': caps.get('architecture'),
            'nodeVersion': caps.get('nodeVersion'), 'npmVersion': caps.get('npmVersion')},
        'workspaceExecution': 'host-private-copy',
        'limits': {key: spec[key] for key in ('timeoutSeconds', 'outputBytes')},
        'localAdapterSteps': [{'stepId': j['id'], 'stages': copy.deepcopy(j['stages'])} for j in declaration['jobs']],
        'initialEnvironment': host_environment(spec['environment']), 'contract': copy.deepcopy(HOST_BOUNDARY)}
    validate_host(result, {'candidate': result['candidate'], 'workspaceContext': {
        'stages': [s for j in declaration['jobs'] for s in j['stages']]}})
    return result


def validate_host(value, packet):
    onboarding._keys(value, ('format', 'schemaVersion', 'candidate', 'bindings', 'adapter',
        'hostSpecSchemaVersion', 'runtimeClaims', 'workspaceExecution', 'limits', 'localAdapterSteps',
        'initialEnvironment', 'contract'), 'host review context')
    if (value['format'] != FORMAT or value['schemaVersion'] != 2 or value['hostSpecSchemaVersion'] != 1
            or value['adapter'] != 'project-host' or value['contract'] != HOST_BOUNDARY
            or value['workspaceExecution'] != 'host-private-copy' or value['candidate'] != packet['candidate']):
        raise Refused('Host review contract/candidate changed')
    onboarding._keys(value['bindings'], ('resourceCatalogSha256', 'executionSpecSha256', 'runnerSha256'), 'host context pins')
    if any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in value['bindings'].values()):
        raise Refused('Host context requires exact pins')
    claims = value['runtimeClaims']
    onboarding._keys(claims, ('os', 'architecture', 'nodeVersion', 'npmVersion'), 'host runtime claims')
    if (claims['os'] not in ('darwin', 'linux', 'win32', 'windows')
            or claims['architecture'] not in ('arm64', 'amd64', 'x64')
            or any(not isinstance(claims[k], str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', claims[k]) for k in ('nodeVersion', 'npmVersion'))):
        raise Refused('Invalid host runtime claims')
    # Host receipts are local files, not Docker's bounded stdout transport.
    # The explicit grant still bounds logs and base64 artifact/report headroom.
    bounds = {'timeoutSeconds': (1, 3600), 'outputBytes': (4096, 64_000_000)}
    onboarding._keys(value['limits'], bounds, 'host resource limits')
    if any(type(value['limits'][k]) is not int or not lo <= value['limits'][k] <= hi for k, (lo, hi) in bounds.items()):
        raise Refused('Invalid host execution limits')
    steps = value['localAdapterSteps']
    if (not isinstance(steps, list) or not steps
            or any(not isinstance(s, dict) or set(s) != {'stepId', 'stages'} for s in steps)
            or len({s['stepId'] for s in steps}) != len(steps)
            or [stage for step in steps for stage in step['stages']] != packet['workspaceContext']['stages']):
        raise Refused('Host stages differ from source-bound declaration')
    env = value['initialEnvironment']
    onboarding._keys(env, ('inheritsHostEnvironment', 'values', 'withheldNames', 'scope'), 'host initial environment')
    if (env['inheritsHostEnvironment'] is not False or env['scope'] != ENVIRONMENT_SCOPE
            or not isinstance(env['withheldNames'], list) or env['withheldNames'] != sorted(set(env['withheldNames']))
            or set(env['withheldNames']) & env['values'].keys()
            or host_environment(env['values'])['values'] != env['values']):
        raise Refused('Invalid host environment projection')
    return value


def build(candidate, catalog_pin, resource, spec, declaration, helper_sha):
    if resource.get('executionSpec', {}).get('backend') == 'local':
        return build_host(candidate, catalog_pin, resource, spec, declaration, helper_sha)
    version = spec.get('schemaVersion')
    extra = ('workspaceExecution', 'inventoryBytes', 'preparedImage') if version == 2 else ()
    if 'artifactCapture' in spec:
        extra += ('artifactCapture',)
    if 'sourceReadModePolicy' in spec:
        extra += ('sourceReadModePolicy',)
    onboarding._keys(spec, ('format', 'schemaVersion', 'job', 'sourcePaths', 'docker', 'image', 'npmCache',
        'runtimeReview', 'sourceReview', 'applicationEnvironment', 'timeoutSeconds', 'outputBytes',
        'memoryBytes', 'processCount', 'workspaceBytes', *extra), 'review container template')
    if (spec['format'] != 'aim-flow-project-container-spec' or type(version) is not int or version not in (1, 2)
            or resource.get('executionSpec', {}).get('backend') != 'container'):
        raise Refused('Unsupported review execution context adapter')
    if 'sourceReadModePolicy' in spec:
        container.source_read_mode_policy(spec['sourceReadModePolicy'])
    bounds = {'timeoutSeconds': (1, 3600), 'outputBytes': (4096, 32_000_000),
              'memoryBytes': (64*1024**2, 3*1024**3), 'processCount': (8, 512),
              'workspaceBytes': (16*1024**2, 2*1024**3)}
    for key, (low, high) in bounds.items():
        if type(spec[key]) is not int or not low <= spec[key] <= high:
            raise Refused('Unsupported review execution resource limit: ' + key)
    mode = spec.get('workspaceExecution', 'noexec')
    if mode not in ('exec', 'noexec') or (version == 2 and (type(spec['inventoryBytes']) is not int
            or not 16*1024**2 <= spec['inventoryBytes'] <= spec['workspaceBytes'])):
        raise Refused('Unsupported review workspace execution settings')
    image = spec['image']
    onboarding._keys(image, ('id', 'repoDigest', 'os', 'architecture', 'nodeVersion', 'npmVersion'), 'review image')
    if (image.get('os') != 'linux' or image.get('architecture') not in ('arm64', 'amd64')
            or not isinstance(image['id'], str) or not re.fullmatch('sha256:[a-f0-9]{64}', image['id'])
            or not isinstance(image['repoDigest'], str) or not re.fullmatch('node@sha256:[a-f0-9]{64}', image['repoDigest'])
            or any(not isinstance(image.get(k), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', image[k])
                   for k in ('nodeVersion', 'npmVersion'))):
        raise Refused('Unsupported review runtime facts')
    if version == 2:
        container.prepared_runtime(spec['preparedImage'], image)  # Pinned preparation claims only.
    for key in ('runtimeReview', 'sourceReview'):
        onboarding._texts(spec[key], key)
        if not spec[key]:
            raise Refused('Source/effect and runtime review remain required')
    steps = [{'stepId': j['id'], 'stages': copy.deepcopy(j['stages'])} for j in declaration['jobs']]
    result = {'format': FORMAT, 'schemaVersion': 1,
        'candidate': {k: candidate[k] for k in ('commit', 'tree')},
        'bindings': {'resourceCatalogSha256': catalog_pin['sha256'],
            'executionSpecSha256': resource['executionSpec']['sha256'], 'runnerSha256': helper_sha},
        'adapter': 'project-container', 'containerSpecSchemaVersion': version,
        'runtimeClaims': {k: image[k] for k in ('os', 'architecture', 'nodeVersion', 'npmVersion')},
        'workspaceExecution': mode, 'limits': {k: spec[k] for k in bounds},
        'localAdapterSteps': steps, 'initialEnvironment': environment(spec['applicationEnvironment'], version),
        'contract': boundary(spec.get('sourceReadModePolicy'))}
    if 'sourceReadModePolicy' in spec:result['sourceReadModePolicy'] = spec['sourceReadModePolicy']
    return result


def validate(value, packet):
    """Check public packet shape/bindings; this never authenticates qualifications."""
    if value.get('adapter') == 'project-host':
        return validate_host(value, packet)
    onboarding._keys(value, ('format', 'schemaVersion', 'candidate', 'bindings', 'adapter',
        'containerSpecSchemaVersion', 'runtimeClaims', 'workspaceExecution', 'limits',
        'localAdapterSteps', 'initialEnvironment', 'contract',
        *(('sourceReadModePolicy',) if 'sourceReadModePolicy' in value else ())), 'review execution context')
    if 'sourceReadModePolicy' in value:container.source_read_mode_policy(value['sourceReadModePolicy'])
    if (value['format'] != FORMAT or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['adapter'] != 'project-container' or value['contract'] != boundary(value.get('sourceReadModePolicy'))
            or value['candidate'] != packet['candidate']):
        raise Refused('Review execution context target or contract changed')
    onboarding._keys(value['bindings'], ('resourceCatalogSha256', 'executionSpecSha256', 'runnerSha256'), 'execution context bindings')
    if any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in value['bindings'].values()):
        raise Refused('Review execution context requires exact pins')
    steps = value['localAdapterSteps']
    if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) or set(s) != {'stepId', 'stages'} for s in steps):
        raise Refused('Invalid execution context step mapping')
    for step in steps:
        if (not isinstance(step['stepId'], str) or not re.fullmatch('[a-z][a-z0-9-]*', step['stepId'])
                or not isinstance(step['stages'], list)):
            raise Refused('Invalid execution context step identity')
    if (len({s['stepId'] for s in steps}) != len(steps)
            or [stage for step in steps for stage in step['stages']] != packet['workspaceContext']['stages']):
        raise Refused('Execution context differs from proposed literal stages')
    claims = value['runtimeClaims']
    onboarding._keys(claims, ('os', 'architecture', 'nodeVersion', 'npmVersion'), 'review runtime claims')
    if (claims['os'] != 'linux' or claims['architecture'] not in ('arm64', 'amd64')
            or any(not isinstance(claims[k], str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', claims[k])
                   for k in ('nodeVersion', 'npmVersion')) or value['workspaceExecution'] not in ('exec', 'noexec')):
        raise Refused('Unsupported public runtime claims')
    limits = value['limits']
    bounds = {'timeoutSeconds': (1, 3600), 'outputBytes': (4096, 32_000_000),
              'memoryBytes': (64*1024**2, 3*1024**3), 'processCount': (8, 512),
              'workspaceBytes': (16*1024**2, 2*1024**3)}
    onboarding._keys(limits, bounds, 'review resource limits')
    if any(type(limits[k]) is not int or not low <= limits[k] <= high for k, (low, high) in bounds.items()):
        raise Refused('Unsupported public resource limits')
    env = value['initialEnvironment']
    onboarding._keys(env, ('inheritsHostEnvironment', 'values', 'withheldNames', 'scope'), 'review initial environment')
    version = value['containerSpecSchemaVersion']
    if type(version) is not int or version not in (1, 2):
        raise Refused('Unsupported execution context version')
    if version == 1 and value['workspaceExecution'] != 'noexec':
        raise Refused('Legacy container schema cannot claim executable workspace')
    fixed = environment({}, version)['values']
    if (env['inheritsHostEnvironment'] is not False or env['scope'] != ENVIRONMENT_SCOPE
            or not isinstance(env['values'], dict) or any(env['values'].get(k) != v for k, v in fixed.items())
            or not isinstance(env['withheldNames'], list) or any(not isinstance(n, str) for n in env['withheldNames'])
            or env['withheldNames'] != sorted(set(env['withheldNames']))
            or set(env['withheldNames']) & env['values'].keys()):
        raise Refused('Invalid or incomplete initial environment facts')
    application = {k: v for k, v in env['values'].items() if k not in fixed}
    if environment(application, version)['values'] != env['values']:
        raise Refused('Unsafe disclosed application environment')
    for name in env['withheldNames']:
        environment({name: ''}, version)
    # This public projection is only usable as request-bound evidence. The
    # orchestrator separately rederives it from pinned private inputs.
    return value


INSTRUCTIONS = '''
The executionContext is a controller-generated description of ONE pinned candidate
local attempt, not authorization or successful qualification. Its contract states
whether this is a container or normal host execution; never infer sandboxing or
offline requirements for a host attempt. Follow the existing project process,
not a process invented to fit an adapter. Use its exact
initialEnvironment: unlisted names are absent from that initial parent environment;
withheldNames are present with unknown values, never absent. Npm and project code
can derive environment values; source review must still inspect such assignments.
Resolve output paths from displayed implementation plus these initial facts only.
Private source copies are mutable during a stage, including temporary timestamp
changes. Exact source bytes, size and mode must match at EVERY stage boundary and
completion. Follow the adapter’s explicit original-checkout and isolation contract;
normal host execution is not a security sandbox. The archive
policy controls input exclusion, not permission to weaken restoration or alter source.
Retained archives and permitted temporary metadata effects are not policy unknowns
merely because they are retained or mutable. Report actual unresolved policy issues.
Runtime/tool/cache/native lifecycle qualification remains pending until the adapter
checks and measures the attempt. A catalog, preparation record or image identity
never establishes successful qualification, safe dependency behavior or acceptance.
For finite-local classify a source-supported finite install/build/test command shape
that has an exact localAdapterSteps entry, not a mathematical termination proof.
Normal dependency hooks, runtime availability and unexecuted observations remain
explicit unknowns/prerequisites, not automatic reasons to misclassify a bounded
local command as persistent. Known source defects, unknown consequential external
effects, unsupported commands and genuine authorization/safety prerequisites still block.
Containment/timeouts cannot prove project behavior correct. Persistent previews,
manual checks and acceptance obligations cannot become finite-local by timeout.
For executionDependsOn retain each DIRECT original finite predecessor. Recursively
replace only evidence-only-acceptance predecessors by their original predecessors
and evidenceSteps, preserving first-seen order and removing duplicates. Do not add
the transitive ancestors of an already retained finite predecessor.
An operator-only deployment preparation/dry-run step without a local adapter stays
hosted-delivery: this means an external delivery obligation, not that remote effects
occurred. Never classify unsupported operator deployment commands as finite-local.
Preserve all original acceptance, hosted-CI, publication and release obligations.
'''
