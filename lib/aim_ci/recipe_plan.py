"""Read-only planning for a separately reviewed declarative npm/Vite recipe."""
from contextlib import nullcontext
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from openai_budget import Refused, canonical, fingerprint, read_owned, safe_path
from .owned_paths import read_owned_content
from .budget import Budget
from .control_package import MAX_FILE, VERSION_PATTERN, check_digest, sha256
from .effects import EffectJournal
from .installed_runtime import file_sha
from .project_discovery import SCRIPT_NAME, SourceBindingMismatch, unique_json, version_relation


# The shared Python/JavaScript JSON contract must preserve integer byte counts exactly.
MAX_SAFE_INTEGER = (1 << 53) - 1

OPERATIONS = {
    'dependencies': ['ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'],
    'test': ['--ignore-scripts', 'run', 'test'],
    'build': ['--ignore-scripts', 'run', 'build'],
}


def recipe_operations(test_script, selector=None):
    test = ['--ignore-scripts', 'run', test_script]
    if selector is not None:
        file = selector['file'].replace('.', r'\.')
        title = f"{selector['project']} {file.removeprefix('tests/')} {selector['suite']}"
        names = '|'.join(selector['names'])
        test = ['--ignore-scripts', '--silent', 'run', test_script, '--',
                '--config=playwright.config.ts', f'/^/work/project/{file}$/', f'--grep=/^{title} ({names})$/',
                f"--project={selector['project']}", '--no-deps', '--workers=1', '--retries=0',
                '--repeat-each=1', '--forbid-only', '--update-snapshots=none', '--reporter=json']
    return {**OPERATIONS, 'test': test}


def planned_operations(tools, script, selector, names):
    operations = recipe_operations(script, selector)
    result = []
    for name in names:
        argv = [tools['node']['path'], tools['npm']['path'], *operations[name]]
        row = {'name': name, 'argv': argv, 'cwd': 'isolated-candidate-root', 'status': 'not-run'}
        if name == 'test' and selector is not None:
            row['discoveryArgv'] = [*argv, '--list']
        result.append(row)
    return result


INPUT_FILES = ('package.json', 'package-lock.json')
REPOSITORY_URL = r'https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.git'
BRANCH_NAME = r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}'
PROTECTED = ('.git', '.github', '.npmrc', '.env', 'node_modules', 'dist', *INPUT_FILES)
RECIPE_FILES = ('recipe.json', 'oracle.json')


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise Refused(f'Invalid {label} fields')


def digest(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value)


def pinned_bytes(path, expected, label):
    if not digest(expected):
        raise Refused(f'Invalid expected {label} SHA-256')
    safe_path(path)
    path = path.resolve()
    data = read_owned_content(path.parent, path.name)
    check_digest(data, expected, label)
    return data


def version_one(value, label):
    if type(value['version']) is not int or value['version'] != 1:
        raise Refused(f'Unsupported {label} version')


def policy_path(value):
    return (isinstance(value, str)
            and re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', value)
            and all(part not in ('.', '..') for part in value.split('/')))


def exact_paths(paths, label, *, path_validator=policy_path):
    if not isinstance(paths, list) or not paths:
        raise Refused(f'Invalid {label} paths')
    if not all(path_validator(value) for value in paths):
        raise Refused(f'Invalid {label} path')
    if len(set(paths)) != len(paths):
        raise Refused(f'Duplicate {label} path')
    return paths


def read_recipe(path, pin):
    safe_path(path)
    manifest = json.loads(pinned_bytes(path / 'recipe-manifest.json', pin, 'Recipe manifest'))
    fields(manifest, 'format id version files', 'recipe manifest')
    if (manifest['format'] != 'aim-ci-recipe'
            or not isinstance(manifest['id'], str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', manifest['id'])
            or not isinstance(manifest['version'], str) or not re.fullmatch(VERSION_PATTERN, manifest['version'])):
        raise Refused('Invalid recipe package identity')
    files = manifest['files']
    if ({entry.name for entry in path.iterdir()} != {'recipe-manifest.json', *RECIPE_FILES}
            or not isinstance(files, dict) or set(files) != set(RECIPE_FILES)):
        raise Refused('Unexpected recipe package members')
    recipe, oracle = (json.loads(pinned_bytes(path / name, files[name], f'Recipe member {name}'))
                      for name in RECIPE_FILES)
    forbidden = validate_recipe(recipe, oracle)
    return manifest, recipe, oracle, forbidden


PLAYWRIGHT_CONFIG = 'playwright.config.ts'
PLAYWRIGHT_VERSION = '1.59.1'


def validate_test_selector(selector):
    fields(selector, 'kind file project suite names', 'Playwright selection')
    name = r'[A-Za-z0-9]+(?: [A-Za-z0-9]+)*'
    names = selector['names']
    if (selector['kind'] != 'playwright-names-v1'
            or not isinstance(selector['file'], str)
            or not re.fullmatch(r'tests/[A-Za-z0-9_-]{1,90}\.spec\.ts', selector['file'])
            or not isinstance(selector['project'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}', selector['project'])
            or not isinstance(names, list) or not 1 <= len(names) <= 16
            or not all(isinstance(value, str) and 1 <= len(value) <= 150 and re.fullmatch(name, value)
                       for value in [selector['suite'], *names])
            or len(set(names)) != len(names)):
        raise Refused('Invalid literal Playwright project, test file, suite or exact names')
    return selector


def planned_test_selector(plan):
    selection = plan.get('testSelection')
    if isinstance(selection, dict) and 'selector' in selection:
        return validate_test_selector(selection['selector'])
    return None


def require_test_runtime(selector):
    if selector is not None:
        raise Refused('Selected Playwright tests require a verified browser runtime and approved container loopback')


def validate_recipe(recipe, oracle):
    version = recipe.get('version') if isinstance(recipe, dict) else None
    if type(version) is not int or version not in (1, 2, 3):
        raise Refused('Unsupported recipe schema version')
    common = 'version stack allowedPaths forbiddenPaths artifact limits'
    selector = None
    if version == 1:
        fields(recipe, common + ' operations', 'recipe')
        if recipe['operations'] != OPERATIONS:
            raise Refused('Recipe operations must match the registered offline npm/Vite argv')
    else:
        fields(recipe, common + ' testScript' + (' sourcePaths dependencies' if version == 3 else
               ' testSelector' if 'testSelector' in recipe else ''), 'recipe')
        if 'testSelector' in recipe:
            selector = validate_test_selector(recipe['testSelector'])
        name = recipe['testScript']
        if not isinstance(name, str) or not re.fullmatch(SCRIPT_NAME, name):
            raise Refused('Recipe testScript must be one supported literal npm script name')
    if version == 3:
        from .npm_platform import project_path
        paths = exact_paths(recipe['sourcePaths'], 'selected source', path_validator=project_path)
        if (recipe['stack'] != 'npm-source-check' or recipe['dependencies'] != {'mode': 'none'}
                or paths != sorted(paths) or not set(INPUT_FILES) <= set(paths)
                or any(any(part in ('.git', 'node_modules', '.npmrc', 'dist')
                           or re.match(r'^\.env(?:\.|$)', part) for part in PurePosixPath(path).parts)
                       for path in paths)):
            raise Refused('Source check requires explicit source files, no dependencies and no build outputs')
    elif recipe['stack'] != 'npm-vite-static':
        raise Refused('Recipe stack must be npm-vite-static')
    allowed = exact_paths(recipe['allowedPaths'], 'allowed')
    if version == 3 and not set(allowed) <= set(recipe['sourcePaths']):
        raise Refused('Source check allowed edits must be included in the selected source files')
    if selector is not None and set(allowed).intersection((selector['file'], PLAYWRIGHT_CONFIG)):
        raise Refused('Recipe cannot allow edits to the selected Playwright spec or config')
    forbidden = [*exact_paths(recipe['forbiddenPaths'], 'forbidden'), *PROTECTED]
    for value in allowed:
        path_parts = PurePosixPath(value).parts
        if any(part.startswith('.') for part in path_parts) or any(
                PurePosixPath(value).is_relative_to(entry) for entry in forbidden):
            raise Refused('Recipe allows a protected or forbidden path')
    if version == 3:
        if recipe['artifact'] != {'kind': 'none'} or oracle != {
                'kind': 'source-check-exit-v1', 'script': recipe['testScript']}:
            raise Refused('Source check requires no artifact and its exact script exit oracle')
        check_limits(recipe['limits'])
        return sorted(set(forbidden))
    artifact = recipe['artifact']
    fields(artifact, 'directory requiredFiles maximumBytes', 'artifact policy')
    from .npm_platform import project_path
    if (artifact['directory'] != 'dist' or type(artifact['maximumBytes']) is not int
            or not 1 <= artifact['maximumBytes'] <= MAX_SAFE_INTEGER
            or 'index.html' not in exact_paths(artifact['requiredFiles'], 'artifact', path_validator=project_path)):
        raise Refused('Invalid static artifact policy')
    limits = recipe['limits']
    check_limits(limits)
    fields(oracle, 'kind file heading', 'oracle')
    if (oracle['kind'] != 'static-heading-v1' or oracle['file'] != 'index.html'
            or not isinstance(oracle['heading'], str) or not oracle['heading'].strip() or len(oracle['heading']) > 150):
        raise Refused('Invalid static heading oracle')
    return sorted(set(forbidden))


def check_limits(limits):
    fields(limits, 'network timeoutSeconds memoryMiB maxProcesses', 'recipe limits')
    ranges = {'timeoutSeconds': (1, 600), 'memoryMiB': (128, 8192), 'maxProcesses': (1, 64)}
    if limits['network'] != 'disabled' or any(type(limits[key]) is not int or not lo <= limits[key] <= hi
                                               for key, (lo, hi) in ranges.items()):
        raise Refused('Invalid planned resource or network limits')


def read_assignment(path, pin, allowed):
    value = json.loads(pinned_bytes(path, pin, 'Assignment'))
    return validate_assignment(value, allowed)


def validate_assignment(value, allowed):
    fields(value, 'version id title acceptance paths', 'assignment')
    version_one(value, 'assignment')
    if not all(isinstance(value[k], str) and value[k].strip() and len(value[k]) <= limit
               for k, limit in (('id', 100), ('title', 200), ('acceptance', 500))):
        raise Refused('Invalid assignment identity or acceptance text')
    if any(name not in allowed for name in exact_paths(value['paths'], 'assignment')):
        raise Refused('Assignment path is outside the approved recipe')
    return value


def verify_tools(runtime, names):
    fields(runtime, names, 'runtime')
    for name, tool in runtime.items():
        fields(tool, 'path sha256', f'{name} tool')
        if not isinstance(tool['path'], str) or not digest(tool['sha256']):
            raise Refused(f'Invalid pinned {name} runtime')
        path = Path(tool['path'])
        if not path.is_absolute() or path.resolve() != path or not path.is_file() or not os.access(path, os.X_OK):
            raise Refused(f'Invalid pinned {name} executable path')
        if file_sha(path) != tool['sha256']:
            raise Refused(f'Pinned {name} runtime SHA-256 mismatch')


def image_digest(value):
    return isinstance(value, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', value)


BROWSER_RUNTIME_KIND = 'playwright-chromium-inventory-v1'
BROWSER_REVISION = '1217'
BROWSER_ROOT = '/opt/aim-ci-browser'
BROWSER_NETWORK = {'mode': 'none', 'loopback': 'container-only', 'externalEgress': 'deny',
                   'publishedPorts': [], 'hostMounts': [], 'hostOrVmDns': 'not-requested'}
BROWSER_MAX_BYTES = 256 * 1024 * 1024
BROWSER_LIBRARY_MAX_BYTES = 32 * 1024 * 1024


def browser_runtime_files(runtime):
    return {runtime['executable']['path']: {key: value for key, value in runtime['executable'].items()
                                         if key != 'path'}, **runtime['libraries']}


def browser_runtime_paths(runtime):
    return {f'browser-{index:02d}': name for index, name in enumerate(sorted(browser_runtime_files(runtime)))}


def validate_browser_runtime(runtime):
    fields(runtime, 'kind platform revision executable libraries network', 'browser runtime')
    platform = runtime['platform']
    fields(platform, 'os cpu libc', 'browser platform')
    if platform['os'] != 'linux' or platform['cpu'] not in ('arm64', 'x64') or platform['libc'] != 'glibc':
        raise Refused('Browser inventory requires a supported Linux glibc platform')
    if runtime['kind'] != BROWSER_RUNTIME_KIND or runtime['revision'] != BROWSER_REVISION:
        raise Refused('Unsupported Playwright Chromium runtime or revision')
    if runtime['network'] != BROWSER_NETWORK:
        raise Refused('Browser network policy requires only internal loopback and no external connectivity')
    executable = runtime['executable']
    fields(executable, 'path sha256 size mode', 'browser executable')
    suffix = ('chrome-linux/headless_shell' if platform['cpu'] == 'arm64'
              else 'chrome-headless-shell-linux64/chrome-headless-shell')
    if executable['path'] != f'{BROWSER_ROOT}/chromium_headless_shell-{BROWSER_REVISION}/{suffix}':
        raise Refused('Chromium executable path differs from the reviewed revision and CPU layout')
    libraries = runtime['libraries']
    if not isinstance(libraries, dict) or not 1 <= len(libraries) <= 8:
        raise Refused('Browser inventory requires one to eight pinned system libraries')
    for path in libraries:
        if (not isinstance(path, str) or len(path) > 240 or not path.startswith('/usr/lib/')
                or not re.fullmatch(r'/usr/lib/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*\.so(?:\.[0-9]+)*', path)
                or any(part in ('.', '..') or len(part) > 128 for part in path.split('/'))):
            raise Refused('Browser inventory requires literal regular system library paths under /usr/lib')
    for path, row in browser_runtime_files(runtime).items():
        fields(row, 'sha256 size mode', 'browser runtime file')
        is_browser = path == executable['path']
        maximum = BROWSER_MAX_BYTES if is_browser else BROWSER_LIBRARY_MAX_BYTES
        if (not digest(row['sha256']) or type(row['size']) is not int or not 20 <= row['size'] <= maximum
                or type(row['mode']) is not int or row['mode'] not in ((0o755,) if is_browser else (0o644, 0o755))):
            raise Refused('Invalid browser runtime byte, size or mode pin')
    return runtime


def container_runtime_sha(tools, browser=None):
    return (fingerprint(tools) if browser is None else
            fingerprint({'tools': tools, 'browserRuntimeSha256': fingerprint(browser)}))


def planned_browser_runtime(plan):
    profile = plan.get('containerProfile')
    if isinstance(profile, dict) and 'browserRuntime' in profile:
        runtime = validate_browser_runtime(profile['browserRuntime'])
        if planned_test_selector(plan) is None:
            raise Refused('Browser inventory requires a reviewed Playwright test selector')
        return runtime
    return None


def read_container_profile(path, pin):
    value = unique_json(pinned_bytes(path, pin, 'Container profile'))
    names = 'version imageDigest tools dependencyStoreSha256'
    if isinstance(value, dict) and 'browserRuntime' in value:
        names += ' browserRuntime'
        validate_browser_runtime(value['browserRuntime'])
    fields(value, names, 'container profile')
    version_one(value, 'container profile')
    if not image_digest(value['imageDigest']) or not digest(value['dependencyStoreSha256']):
        raise Refused('Invalid container image or dependency store identity')
    validate_container_tools(value['tools'])
    return value


def validate_container_tools(tools):
    fields(tools, 'node npm', 'container tools')
    for name, tool in tools.items():
        fields(tool, 'path sha256', f'container {name} tool')
        path = tool['path']
        if (not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', path)
                or any(part in ('.', '..') for part in path.split('/')) or not digest(tool['sha256'])):
            raise Refused(f'Invalid declared container {name} runtime')
    if tools['node']['path'] == tools['npm']['path']:
        raise Refused('Container Node and npm CLI require distinct paths')
    return tools


def validate_target(target):
    fields(target, 'repository branch commit', 'target')
    if (not isinstance(target['repository'], str)
            or not re.fullmatch(REPOSITORY_URL, target['repository'])
            or not isinstance(target['commit'], str) or not re.fullmatch('[a-f0-9]{40}', target['commit'])
            or not isinstance(target['branch'], str) or not re.fullmatch(BRANCH_NAME, target['branch'])):
        raise Refused('Invalid approved repository, branch or commit')


def inspect_target(checkout, target, git, input_pins, allowed):
    validate_target(target)
    fields(input_pins, 'package.json package-lock.json', 'approved committed input')
    safe_path(checkout)
    checkout = checkout.resolve(strict=True)
    environment = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
                   'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_REPLACE_OBJECTS': '1'}

    def read(*args, maximum=MAX_FILE):
        command = [git, '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', *args]
        proc = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, timeout=20)
        if proc.returncode or len(proc.stdout) > maximum:
            raise Refused(f'Local Git could not read {args[0]} from the pinned target')
        return proc.stdout

    version = re.match(rb'git version (\d+)\.(\d+)(?:\.(\d+))?', read('--version'))
    if not version or tuple(int(part or 0) for part in version.groups()) < (2, 54, 0):
        raise Refused('Recipe planning requires Git 2.54.0 or newer')
    if read('rev-parse', '--show-toplevel').decode().rstrip('\n') != str(checkout):
        raise Refused('Recipe planning requires an explicit Git worktree root')
    remote = read('config', '--local', '--no-includes', '--get-all', 'remote.origin.url')
    if remote.decode().splitlines() != [target['repository']]:
        raise SourceBindingMismatch('Local origin differs from the approved repository identity')
    if (read('rev-parse', 'HEAD').decode().strip() != target['commit']
            or read('symbolic-ref', '--short', 'HEAD').decode().strip() != target['branch']):
        raise SourceBindingMismatch('Checkout branch or commit differs from the approved target')
    tree = read('rev-parse', f"{target['commit']}^{{tree}}").decode().strip()

    def tracked_file(name):
        entry = read('ls-tree', '-z', target['commit'], '--', name)
        if not re.fullmatch(rb'100(?:644|755) blob [a-f0-9]{40}\t' + re.escape(name.encode()) + rb'\x00', entry):
            raise Refused(f'Approved target lacks a regular committed file at {name}')

    for path in (*INPUT_FILES, *allowed):
        tracked_file(path)
    inputs = {}
    for name in INPUT_FILES:
        spec = f"{target['commit']}:{name}"
        size = int(read('cat-file', '-s', spec))
        if size < 0:
            raise Refused('Invalid measured committed input size')
        encoded = read('cat-file', 'blob', spec, maximum=size)
        if len(encoded) != size:
            raise Refused('Committed input changed while reading its measured bytes')
        expected = input_pins[name]
        if not digest(expected):
            raise Refused(f'Invalid approved digest for {name}')
        check_digest(encoded, expected, f'Committed {name}')
        inputs[name] = json.loads(encoded)
    return checkout, tree, inputs


def inspect_npm(inputs, recipe, *, allow_unselected_test=False):
    package, lock = (inputs[name] for name in INPUT_FILES)
    if not isinstance(package, dict) or not isinstance(lock, dict):
        raise Refused('Invalid committed npm package or lockfile')
    version = recipe['version']
    test_script = recipe['testScript'] if version in (2, 3) else 'test'
    scripts = package.get('scripts')
    if version == 3:
        if (not isinstance(scripts, dict) or not isinstance(test_script, str)
                or not re.fullmatch(SCRIPT_NAME, test_script) or not isinstance(scripts.get(test_script), str)
                or not scripts[test_script].strip()):
            raise Refused('Source check requires the selected committed npm script')
        if any(name in scripts for name in ('pre' + test_script, 'post' + test_script)):
            raise Refused('Source check cannot silently skip declared selected-script lifecycle hooks')
        locked = lock.get('packages')
        if (type(lock.get('lockfileVersion')) is not int or lock['lockfileVersion'] != 3
                or not isinstance(locked, dict) or not isinstance(locked.get(''), dict)
                or any(not isinstance(row, dict) for row in locked.values())):
            raise Refused('Source check requires committed npm v3 lock metadata')
        if any(locked[''].get(key, {}) != package.get(key, {}) for key in (
                'dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'peerDependenciesMeta')):
            raise Refused('Committed package dependencies and lock metadata disagree')
        if any('workspaces' in value for value in (package, lock, locked[''])):
            raise Refused('Source check supports one root npm package')
        manager = package.get('packageManager')
        if manager is not None and (not isinstance(manager, str) or not manager.startswith('npm@')):
            raise Refused('Source check requires npm package-manager metadata')
        return {'scriptsAreExecutableInputs': True, 'scripts': scripts, 'lockfileVersion': 3,
                'dependencyCount': len(locked) - 1, 'dependenciesInstalled': False,
                'lockInstallScripts': sorted(name for name, entry in locked.items() if entry.get('hasInstallScript'))}
    requires_test = test_script is not None or not allow_unselected_test
    if version == 2 and requires_test and (not isinstance(scripts, dict) or test_script not in scripts):
        raise Refused('Selected testScript is absent from committed package.json scripts')
    deps = package.get('devDependencies')
    locked = lock.get('packages')
    if (not isinstance(scripts, dict) or 'build' not in scripts or requires_test and test_script not in scripts
            or any(not isinstance(value, str) or not value.strip() for value in scripts.values())
            or not isinstance(deps, dict) or not isinstance(deps.get('vite'), str)
            or lock.get('lockfileVersion') != 3 or not isinstance(locked, dict)
            or any(not isinstance(value, dict) for value in locked.values())):
        raise Refused('Recipe requires committed build/test scripts and an npm v3 Vite lockfile')
    vite = locked.get('node_modules/vite', {})
    declaration, resolved = deps['vite'], vite.get('version')
    agrees = (vite.get('integrity') and locked.get('', {}).get('devDependencies') == deps
              and locked.get('', {}).get('dependencies', {}) == package.get('dependencies', {}))
    if version == 1 and (not agrees or declaration != resolved):
        raise Refused('Committed package dependencies do not match the exact locked Vite version')
    if not agrees:
        raise Refused('Committed package dependencies and lock metadata disagree')
    metadata = {'scriptsAreExecutableInputs': True, 'scripts': scripts,
                'lockInstallScripts': sorted(name for name, entry in locked.items() if entry.get('hasInstallScript')),
                'lockfileVersion': 3, 'viteVersion': resolved, 'dependencyCount': len(locked) - 1}
    if version == 1:
        return metadata
    if type(lock.get('lockfileVersion')) is not int:
        raise Refused('Schema v2 requires integer npm lockfileVersion 3')
    root = locked.get('', {})
    if any(root.get(key, {}) != package.get(key, {})
           for key in ('optionalDependencies', 'peerDependencies', 'peerDependenciesMeta')):
        raise Refused('Committed optional or peer dependencies and root lock metadata disagree')
    if not isinstance(resolved, str) or vite.get('link') or not isinstance(vite.get('integrity'), str):
        raise Refused('Committed Vite lock entry lacks an exact non-link version')
    relation = version_relation(declaration, resolved)
    if relation == 'unsupported-syntax':
        raise Refused('Committed Vite declaration or lock version is outside the supported exact and caret subset')
    if relation != 'matches':
        raise Refused('Committed Vite declaration does not match the locked version')
    if 'testSelector' in recipe and (scripts[test_script] != 'playwright test'
            or any(entry.get('version') != PLAYWRIGHT_VERSION or not entry.get('integrity') or entry.get('link')
                   for entry in (locked.get('node_modules/' + name, {})
                                 for name in ('@playwright/test', 'playwright', 'playwright-core')))):
        raise Refused('Playwright selection requires the exact committed playwright test script and supported lock')
    metadata['viteRelation'] = {'declaration': declaration, 'lockedVersion': resolved, 'relation': relation,
                                'parser': 'exact-and-caret-stable-v1',
                                'sources': [{'path': 'package.json', 'locator': 'devDependencies["vite"]'},
                                            {'path': 'package-lock.json', 'locator': 'packages["node_modules/vite"].version'}]}
    return metadata


def inspect_state(spec, identity, excluded_roots):
    if not isinstance(spec, dict) or spec.get('mode') not in ('uninitialized', 'retained'):
        raise Refused('Invalid approved planning state mode')
    retained = spec['mode'] == 'retained'
    fields(spec, 'mode path files' if retained else 'mode path', 'approved state')
    if not isinstance(spec['path'], str):
        raise Refused('Invalid approved state path')
    state = Path(spec['path'])
    safe_path(state)
    if not state.is_absolute() or state.resolve() != state or any(
            state.is_relative_to(p) or p.is_relative_to(state) for p in excluded_roots):
        raise Refused('Planning state must be a separate absolute path')
    if not retained:
        if state.exists():
            raise Refused('Uninitialized planning state already exists; preserve its history')
        return {**spec, 'budget': 'uninitialized'}
    files = ('binding.json', 'legacy.json', 'budget-established.json', 'budget.json')
    fields(spec['files'], ' '.join(files), 'approved state digest')
    retained_binding = pinned_bytes(state / 'binding.json', spec['files']['binding.json'],
                                    'Planning state binding.json')
    if unique_json(retained_binding) != {**identity, 'statePath': str(state)}:
        raise Refused('Retained planning state identity differs from this plan')
    for name in files[1:]:
        pinned_bytes(state / name, spec['files'][name], f'Planning state {name}')
    try:
        ledger = Budget.read_existing(state, state / 'legacy.json')
    except (KeyError, TypeError, ValueError) as error:
        raise Refused('Invalid retained planning budget schema') from error
    for name in files:
        pinned_bytes(state / name, spec['files'][name], f'Planning state {name}')
    return {**spec, 'budget': {'limitNanoUsd': ledger['limitNanoUsd'], 'maximumCalls': ledger['maxCalls'],
                              'retainedCalls': ledger['legacyCalls'] + len(ledger['requests'])}}


def plan_recipe(root, args, control):
    manifest, recipe, oracle, forbidden = read_recipe(args.recipe_package, args.recipe_sha256)
    approval = unique_json(pinned_bytes(args.approval, args.approval_sha256, 'Operator approval'))
    version = recipe['version']
    approval_version = approval.get('version') if isinstance(approval, dict) else None
    if type(approval_version) is not int or approval_version not in (1, 2):
        raise Refused('Unsupported operator approval version')
    container = approval_version == 2
    if container and version not in (2, 3):
        raise Refused('Container planning requires recipe schema v2')
    if version == 3 and not container:
        raise Refused('Source check requires the existing reviewed container runtime')
    approval_fields = 'version kind reviewReference controlManifestSha256 recipeManifestSha256 target runtime committedInputs state'
    if version in (2, 3):
        approval_fields += ' reviewSha256'
    if container:
        approval_fields += ' containerProfileSha256'
    fields(approval, approval_fields, 'operator approval')
    if (approval['kind'] not in ('operator-record', 'synthetic-fixture')
            or not isinstance(approval['reviewReference'], str) or not approval['reviewReference'].strip()
            or len(approval['reviewReference']) > 250):
        raise Refused('Invalid operator approval review reference')
    if (approval['controlManifestSha256'] != args.manifest_sha256
            or approval['recipeManifestSha256'] != args.recipe_sha256):
        raise Refused('Operator approval does not bind this control and recipe package')
    assignment = read_assignment(args.assignment, args.assignment_sha256, recipe['allowedPaths'])
    host_tools = approval['runtime']
    if container:
        if args.container_profile is None or args.container_profile_sha256 is None:
            raise Refused('Container planning requires a profile and its externally supplied SHA-256')
        if approval['containerProfileSha256'] != args.container_profile_sha256:
            raise Refused('Operator approval does not bind this container profile')
        profile = read_container_profile(args.container_profile, args.container_profile_sha256)
        verify_tools(host_tools, 'git')
        tools = profile['tools']
    else:
        if args.container_profile is not None or args.container_profile_sha256 is not None:
            raise Refused('A container profile requires operator approval version 2')
        verify_tools(host_tools, 'git node npm')
        tools = host_tools
    selector = recipe.get('testSelector')
    if selector is not None and not container:
        raise Refused('Selected Playwright planning requires a reviewed container profile')
    tracked = [*recipe['allowedPaths']]
    if version == 3:
        tracked.extend(recipe['sourcePaths'])
    if selector is not None:
        tracked.extend((selector['file'], PLAYWRIGHT_CONFIG))
    target = approval['target']
    checkout, tree, inputs = inspect_target(args.checkout, target, host_tools['git']['path'],
                                           approval['committedInputs'], tracked)
    recipe_root = args.recipe_package.resolve()
    approval_path = args.approval.resolve()
    if (recipe_root.is_relative_to(checkout) or checkout.is_relative_to(recipe_root)
            or approval_path.is_relative_to(checkout) or approval_path.is_relative_to(recipe_root)
            or approval_path.is_relative_to(root)):
        raise Refused('Recipe package and operator approval must be outside the checkout and separate')
    npm = inspect_npm(inputs, recipe)
    test_script = recipe['testScript'] if version in (2, 3) else 'test'
    selection = {}
    if version in (2, 3):
        selection = {'recipeSchemaVersion': version,
                     'testSelection': {'script': test_script, 'selectedBy': 'pinned-recipe',
                                       'source': {'path': 'package.json', 'locator': f'scripts[{json.dumps(test_script)}]',
                                                  'sha256': approval['committedInputs']['package.json']},
                                       'runtimeEffects': 'review-required', 'loopback': 'not-approved'},
                     'oracleSuitability': 'synthetic-fixture-only' if approval['kind'] == 'synthetic-fixture' else 'review-required'}
    if selector is not None:
        selection['testSelection']['selector'] = selector
    identity = {'controlManifestSha256': args.manifest_sha256, 'recipeManifestSha256': args.recipe_sha256,
                'target': target, 'assignmentSha256': args.assignment_sha256,
                'runtimeSha256': container_runtime_sha(tools, profile.get('browserRuntime') if container else None)}
    source_record = {}
    if version == 3:
        from .project_discovery import CommitReader
        from .source_subset import source_subset

        reader = CommitReader(root, checkout, Path(host_tools['git']['path']),
                              host_tools['git']['sha256'], target['commit'])
        source, _, proof = source_subset(reader, recipe['sourcePaths'])
        selected_manifest = {name: {key: row[key] for key in ('mode', 'size', 'sha256')} for name, row in source.items()}
        execution = {'kind': 'npm-source-check-v1', 'dependencies': 'none', 'outputs': 'none',
                     'sourcePaths': recipe['sourcePaths']}
        source_record = {'sourceFiles': selected_manifest, 'sourceProofSha256': fingerprint(proof),
                         'sourceProofBytes': len(canonical(proof).encode()),
                         'sourceSelectionSha256': fingerprint(selected_manifest), 'execution': execution}
        identity.update(sourceProofSha256=source_record['sourceProofSha256'],
                        sourceSelectionSha256=source_record['sourceSelectionSha256'],
                        executionSha256=fingerprint(execution))
        reader.verify_head()
    elif container:
        from .project_discovery import CommitReader
        from .build_preparation import source_inventory, source_manifest

        reader = CommitReader(root, checkout, Path(host_tools['git']['path']),
                              host_tools['git']['sha256'], target['commit'])
        source, _ = source_inventory(reader)
        inventory = source_manifest(source)
        source_record = {'sourceInventory': inventory, 'sourceInventorySha256': fingerprint(inventory)}
        reader.verify_head()
    runtime = {'tools': tools, 'sha256': identity['runtimeSha256'], 'plannerPython': sys.version.split()[0]}
    container_record = {}
    if container:
        profile_path = args.container_profile.resolve()
        if (profile_path in (approval_path, args.assignment.resolve())
                or any(profile_path.is_relative_to(path) for path in (checkout, recipe_root, root))):
            raise Refused('Container profile must be separate from the other records, checkout and packages')
        identity.update(containerProfileSha256=args.container_profile_sha256, targetTree=tree,
                        gitReaderSha256=fingerprint(host_tools))
        runtime.update(kind='reviewed-container-runtime', verification='declared-not-observed')
        container_record = {
            'gitReader': {**host_tools['git'], 'verification': 'matched-local-executable-pin'},
            'containerProfile': {'sha256': args.container_profile_sha256, 'imageDigest': profile['imageDigest'],
                                 'dependencyStoreSha256': profile['dependencyStoreSha256'],
                                 'verification': 'matched-separate-review-pin'},
        }
        if 'browserRuntime' in profile:
            container_record['containerProfile']['browserRuntime'] = profile['browserRuntime']
            planned_browser_runtime({**container_record, **selection})
    review = {}
    if version in (2, 3):
        if args.review is None or args.review_sha256 is None:
            raise Refused('Schema v2 requires a separately pinned recipe review')
        review_path = args.review.resolve()
        if (review_path == approval_path or (container and review_path == profile_path)
                or review_path.is_relative_to(checkout)
                or review_path.is_relative_to(recipe_root) or review_path.is_relative_to(root)):
            raise Refused('Recipe review must be separate from the approval, checkout and packages')
        if approval['reviewSha256'] != args.review_sha256:
            raise Refused('Operator approval does not bind this recipe review')
        review_data = unique_json(pinned_bytes(args.review, args.review_sha256, 'Recipe review'))
        expected_review = {'version': approval_version, 'decision': 'reviewed-for-planning', 'kind': approval['kind'],
                           **identity, 'testScript': test_script, 'reviewReference': approval['reviewReference'],
                           'committedInputs': approval['committedInputs']}
        if version == 3:
            expected_review['sourceFiles'] = source_record['sourceFiles']
        if 'sourceInventory' in review_data or 'sourceInventorySha256' in review_data:
            if 'sourceInventory' not in source_record:
                raise Refused('Full source inventory requires a container build review')
            expected_review.update(sourceInventory=source_record['sourceInventory'],
                                   sourceInventorySha256=source_record['sourceInventorySha256'])
        if selector is not None:
            expected_review['testSelector'] = selector
        fields(review_data, ' '.join(expected_review), 'recipe review')
        if type(review_data['version']) is not int or review_data['version'] != approval_version:
            raise Refused('Unsupported recipe review version for this approval')
        if review_data != expected_review:
            raise Refused('Recipe review does not bind this selection, target and pinned inputs')
        identity['reviewSha256'] = args.review_sha256
        review = {'review': {'sha256': args.review_sha256, 'decision': review_data['decision'],
                             'kind': review_data['kind'], 'verification': 'matched-separate-review-pin'}}
    elif args.review is not None or args.review_sha256 is not None:
        raise Refused('Recipe schema v1 uses its original approval contract')
    state = inspect_state(approval['state'], identity, (root, checkout, recipe_root))
    # Existing planning state already pins the complete Git tree. The new plan also pins its measured inventory.
    if 'sourceInventorySha256' in source_record:
        identity['sourceInventorySha256'] = source_record['sourceInventorySha256']
    return {
        'version': approval_version, 'status': 'planned-only', 'executionAuthorized': False,
        **selection, **review, **container_record, **source_record,
        'control': {'version': control['version'], 'manifestSha256': args.manifest_sha256},
        'recipe': {**manifest, 'manifestSha256': args.recipe_sha256},
        'approval': {'kind': approval['kind'], 'reviewReference': approval['reviewReference'],
                     'sha256': args.approval_sha256, 'verification': 'matched-operator-supplied-pin'},
        'target': {**target, 'checkout': str(checkout), 'tree': tree, 'originVerification': 'local-config-only'},
        'assignment': {**assignment, 'sha256': args.assignment_sha256},
        'runtime': runtime,
        'committedInputs': approval['committedInputs'], 'npm': npm,
        'scope': {'allowedPaths': recipe['allowedPaths'], 'forbiddenPaths': forbidden, 'requestedPaths': assignment['paths']},
        'operations': planned_operations(tools, test_script, selector, ('test',) if version == 3 else OPERATIONS),
        'limits': recipe['limits'], 'state': state,
        'bindingSha256': fingerprint({**identity, 'state': approval['state'], 'approvalSha256': args.approval_sha256}),
        'artifact': ({'kind': 'none'} if version == 3 else
                     {**recipe['artifact'], 'linksAllowed': False, 'status': 'not-built'}),
        'oracle': {**oracle, 'sha256': manifest['files']['oracle.json'], 'status': 'not-run'},
        'requiredBeforeRelease': {name: 'not-run' for name in
                                  ('dependencyStoreVerification', 'sandboxEnforcement', 'dependencyAudit', 'sbom', 'tests', 'independentOracle', 'signing',
                                   'provenanceVerification', 'releaseAuthorization', 'destinationVerification')},
    }


_NATIVE_CAPABILITIES = {
    'network': 'policy-available',
    'workspaceWrites': 'policy-available',
    'readIsolation': 'unqualified',
    'processTreeTimeout': 'unqualified',
    'aggregateMemory': 'unqualified',
    'processCount': 'unqualified',
    'dependencyStore': 'unqualified',
}
_TEST_REPOSITORY = 'https://fixture.invalid/aim-ci/recipe-test.git'


def planned_source_check(plan):
    """Return the bounded, explicitly selected contract, never infer it from scripts."""
    if plan.get('recipeSchemaVersion') != 3:
        if any(name in plan for name in ('execution', 'sourceFiles', 'sourceProofSha256', 'sourceSelectionSha256')):
            raise Refused('Source check fields require recipe schema v3')
        return None
    from .npm_platform import project_path

    execution = plan.get('execution')
    fields(execution, 'kind dependencies outputs sourcePaths', 'source check execution')
    paths = exact_paths(execution['sourcePaths'], 'selected source', path_validator=project_path)
    profile = plan.get('containerProfile', {})
    if (execution['kind'] != 'npm-source-check-v1' or execution['dependencies'] != 'none'
            or execution['outputs'] != 'none' or paths != sorted(paths) or not set(INPUT_FILES) <= set(paths)
            or plan.get('artifact') != {'kind': 'none'} or planned_test_selector(plan) is not None
            or not isinstance(profile, dict) or 'browserRuntime' in profile):
        raise Refused('Unsupported source check execution contract')
    manifest = plan.get('sourceFiles')
    if not isinstance(manifest, dict) or set(manifest) != set(paths):
        raise Refused('Source check manifest differs from the exact selected paths')
    for path, row in manifest.items():
        fields(row, 'mode size sha256', 'selected source file')
        if (type(row['mode']) is not int or row['mode'] not in (0o644, 0o755)
                or type(row['size']) is not int or row['size'] < 0 or not digest(row['sha256'])
                or any(part in ('.git', 'node_modules', '.npmrc', 'dist')
                       or re.match(r'^\.env(?:\.|$)', part) for part in PurePosixPath(path).parts)):
            raise Refused('Invalid selected source file or protected input')
    if (not digest(plan.get('sourceProofSha256'))
            or plan.get('sourceSelectionSha256') != fingerprint(manifest)
            or any(manifest[name]['sha256'] != plan.get('committedInputs', {}).get(name) for name in INPUT_FILES)):
        raise Refused('Source check lacks exact source and proof pins')
    if 'sourceProofBytes' in plan and (type(plan['sourceProofBytes']) is not int or plan['sourceProofBytes'] < 1):
        raise Refused('Invalid measured source proof size')
    return execution


def planned_source_inventory(plan):
    """Validate the complete build inventory, including both optional compatibility fields."""
    if not any(key in plan for key in ('sourceInventory', 'sourceInventorySha256')):
        return None
    from .npm_platform import project_path

    inventory = plan.get('sourceInventory')
    if (plan.get('recipeSchemaVersion') == 3 or plan.get('version') != 2
            or not isinstance(inventory, dict) or not set(INPUT_FILES) <= inventory.keys()):
        raise Refused('Invalid complete build source inventory')
    for name, row in inventory.items():
        fields(row, 'mode size sha256', 'build source inventory file')
        if (not project_path(name) or type(row['mode']) is not int or row['mode'] not in (0o644, 0o755)
                or type(row['size']) is not int or row['size'] < 0 or not digest(row['sha256'])
                or any(part in ('.git', 'node_modules', '.npmrc') or re.match(r'^\.env(?:\.|$)', part)
                       for part in name.split('/'))):
            raise Refused('Invalid complete build source inventory file')
    if (plan.get('sourceInventorySha256') != fingerprint(inventory)
            or any(inventory[name]['sha256'] != plan.get('committedInputs', {}).get(name) for name in INPUT_FILES)):
        raise Refused('Complete build source inventory differs from its pinned inputs')
    return inventory


def _execution_plan(path, pin, control_pin):
    plan = unique_json(pinned_bytes(path, pin, 'Execution plan'))
    if (not isinstance(plan, dict) or type(plan.get('recipeSchemaVersion')) is not int
            or plan['recipeSchemaVersion'] not in (2, 3)
            or plan.get('status') != 'planned-only' or plan.get('executionAuthorized') is not False):
        raise Refused('Execution admission requires a planning-only schema v2 plan')
    control, recipe, runtime = (plan.get(name) for name in ('control', 'recipe', 'runtime'))
    target, inputs = plan.get('target'), plan.get('committedInputs')
    fields(inputs, 'package.json package-lock.json', 'committed input')
    if not (all(isinstance(value, dict) for value in (control, recipe, runtime, target))
            and digest(control_pin) and control.get('manifestSha256') == control_pin
            and all(digest(value) for value in (plan.get('bindingSha256'), recipe.get('manifestSha256'),
                                                runtime.get('sha256'), *inputs.values()))
            and all(isinstance(target.get(name), str) and re.fullmatch('[a-f0-9]{40}', target[name])
                    for name in ('commit', 'tree'))):
        raise Refused('Execution plan lacks its pinned control, recipe, source or runtime identity')
    check_limits(plan.get('limits'))
    binding = {'planSha256': pin, 'planningBindingSha256': plan['bindingSha256'],
               'controlManifestSha256': control_pin, 'recipeManifestSha256': recipe['manifestSha256'],
               'sourceCommit': target['commit'], 'sourceTree': target['tree'], 'committedInputs': inputs,
               'runtimeSha256': runtime['sha256']}
    inventory = planned_source_inventory(plan)
    if inventory is not None:
        binding['sourceInventorySha256'] = plan['sourceInventorySha256']
    execution = planned_source_check(plan)
    if execution is not None:
        if plan.get('version') != 2:
            raise Refused('Source check execution requires the existing reviewed container runtime')
        binding.update(sourceProofSha256=plan['sourceProofSha256'],
                       sourceSelectionSha256=plan['sourceSelectionSha256'],
                       executionSha256=fingerprint(execution))
        if 'sourceProofBytes' in plan:
            binding['sourceProofBytes'] = plan['sourceProofBytes']
    plan_version = plan.get('version', 1)
    if type(plan_version) is not int or plan_version not in (1, 2):
        raise Refused('Unsupported execution plan version')
    if plan_version == 2:
        profile = plan.get('containerProfile')
        names = 'sha256 imageDigest dependencyStoreSha256 verification'
        browser = planned_browser_runtime(plan)
        if browser is not None:
            names += ' browserRuntime'
            binding.update(browserRuntimeSha256=fingerprint(browser), browserRuntimePlatform=browser['platform'])
        fields(profile, names, 'planned container profile')
        if (not digest(profile['sha256']) or not image_digest(profile['imageDigest'])
                or not digest(profile['dependencyStoreSha256'])
                or profile['verification'] != 'matched-separate-review-pin'
                or runtime.get('verification') != 'declared-not-observed'
                or not _container_runtime_matches(runtime, binding['runtimeSha256'], browser)):
            raise Refused('Invalid planned container profile or runtime identity')
        binding.update(containerProfileSha256=profile['sha256'], imageDigest=profile['imageDigest'],
                       dependencyStoreSha256=profile['dependencyStoreSha256'])
    elif 'containerProfile' in plan or runtime.get('verification') == 'declared-not-observed':
        raise Refused('A container profile requires plan version 2')
    return plan, binding


def check_recipe_execution(path, pin, control_pin, *, trusted_docker_policy=None,
                           trusted_docker_policy_pin=None, fixture_observation=None):
    """Report readiness. Only trusted controller code can supply a Docker policy."""
    plan, binding = _execution_plan(path, pin, control_pin)
    if trusted_docker_policy is not None:
        return _prepare_docker_recipe(plan, binding, trusted_docker_policy,
                                      trusted_docker_policy_pin, fixture_observation)
    if trusted_docker_policy_pin is not None or fixture_observation is not None:
        raise Refused('Docker preparation requires a separately pinned controller policy')
    container = 'containerProfileSha256' in binding
    capabilities = dict(_NATIVE_CAPABILITIES)
    if container:
        capabilities = dict.fromkeys((*capabilities, 'controllerPolicy', 'containerImage', 'containerRuntime',
                                      'loopback'), 'unqualified')
    if planned_browser_runtime(plan) is not None:
        capabilities['browserRuntime'] = 'unqualified'
    return {'version': 1, 'status': 'blocked-before-dispatch', 'backend': 'docker' if container else 'native',
            'binding': binding, 'limits': plan['limits'], 'capabilities': capabilities,
            'unqualified': [name for name, status in capabilities.items() if status == 'unqualified'],
            'executionAuthorized': False, 'processStarted': False, 'exitCode': None, 'observedEffects': []}


def _container_runtime_matches(runtime, pin, browser=None):
    tools = runtime.get('tools')
    if (runtime.get('kind') != 'reviewed-container-runtime' or runtime.get('sha256') != pin
            or not isinstance(tools, dict) or set(tools) != {'node', 'npm'}):
        return False
    return (all(isinstance(tool, dict) and set(tool) == {'path', 'sha256'}
                and isinstance(tool['path'], str) and PurePosixPath(tool['path']).is_absolute()
                and '..' not in PurePosixPath(tool['path']).parts and digest(tool['sha256'])
                for tool in tools.values())
            and container_runtime_sha(tools, browser) == pin)


def reviewed_docker_operations(plan, names):
    selection = plan.get('testSelection')
    script = selection.get('script') if isinstance(selection, dict) else None
    if (not isinstance(script, str) or not re.fullmatch(SCRIPT_NAME, script)
            or selection.get('selectedBy') != 'pinned-recipe'):
        raise Refused('Docker lifecycle requires one pinned literal test script')
    selector = planned_test_selector(plan)
    expected = planned_operations(plan['runtime']['tools'], script, selector, names)
    operations = plan.get('operations')
    if (not isinstance(operations, list)
            or [item for item in operations if isinstance(item, dict) and item.get('name') in names] != expected):
        operation_label = '/'.join(names) if 'test' in names else 'install/build'
        raise Refused(f'Docker {operation_label} argv does not match the pinned container runtime and recipe')
    return script, expected


def reviewed_platform_operations(plan, with_offline_test):
    source_check = planned_source_check(plan)
    names = ('test',) if source_check is not None else (
        ('dependencies', 'test', 'build') if with_offline_test else ('dependencies', 'build'))
    script, operations = reviewed_docker_operations(plan, names)
    if source_check is not None and plan['operations'] != operations:
        raise Refused('Source check must contain exactly one named test operation')
    if with_offline_test or source_check is not None:
        source = {'path': 'package.json', 'locator': f'scripts[{json.dumps(script)}]',
                  'sha256': plan['committedInputs']['package.json']}
        expected = {'script': script, 'selectedBy': 'pinned-recipe', 'source': source,
                    'runtimeEffects': 'review-required', 'loopback': 'not-approved'}
        selector = planned_test_selector(plan)
        if selector is not None:
            expected['selector'] = selector
        if plan['testSelection'] != expected:
            raise Refused('Offline test requires the committed script selection without additional runtime effects')
    return script, operations


def docker_job_intent(preparation, store_pin, script, platform_review):
    intent = {key: value for key, value in preparation['intent'].items()
              if key not in ('effectId', 'containerName')}
    intent.update(dependencyStoreSha256=store_pin, testScript=script)
    if platform_review is not None:
        intent['platformBuild'] = platform_review
        if 'testSelector' in platform_review and 'browserRuntime' in platform_review:
            network = platform_review['browserRuntime']['network']
            intent['requestedControls'] = {**intent['requestedControls'], 'loopback': network['loopback'],
                                           'browserNetwork': network}
    effect = fingerprint(intent)
    return {**intent, 'effectId': effect, 'containerName': 'aim-ci-' + effect[:24]}


def _advance_docker_recipe(path, pin, control_pin, *, trusted_docker_policy,
                           trusted_docker_policy_pin, dependency_store_sha256,
                           journal_path, request, clock, platform_evidence=None, response=None,
                           with_offline_test=False, readback_acquisition_id=None):
    """Internal lifecycle seam. The public readiness command supplies no transport."""
    from . import docker_engine

    plan, binding = _execution_plan(path, pin, control_pin)
    source_check = planned_source_check(plan)
    if source_check is not None:
        with_offline_test = True
    prepared = _prepare_docker_recipe(plan, binding, trusted_docker_policy,
                                      trusted_docker_policy_pin, None)
    adapter = Path(docker_engine.__file__)
    if ('dependencyStoreSha256' in binding
            and dependency_store_sha256 != binding['dependencyStoreSha256']):
        raise Refused('Docker dependency store does not match the reviewed container profile')
    if (prepared['intent'] is None or not digest(dependency_store_sha256)
            or file_sha(adapter) != trusted_docker_policy['adapterSha256']):
        raise Refused('Docker lifecycle requires the reviewed adapter, container runtime and dependency store')
    if platform_evidence is None:
        if with_offline_test or planned_test_selector(plan) is not None:
            raise Refused('Offline test requires verified platform build inputs')
        script, _ = reviewed_docker_operations(plan, ('test',))
    else:
        script, operations = reviewed_platform_operations(plan, with_offline_test)
        if with_offline_test:
            require_test_runtime(planned_test_selector(plan))
    platform_review = None
    job_lock = nullcontext()
    if platform_evidence is not None:
        from .platform_build import platform_job_lock, reviewed_build

        if response is None:
            raise Refused('Platform build requires the reviewed streaming log transport')
        if not isinstance(platform_evidence, tuple) or len(platform_evidence) != 2:
            raise Refused('Platform build requires an evidence path and external pin')
        platform_review = reviewed_build(plan, binding, trusted_docker_policy,
                                         trusted_docker_policy_pin, *platform_evidence,
                                         acquisition_id=readback_acquisition_id)
        if with_offline_test:
            platform_review['testOperation'] = operations[0 if source_check is not None else 1]
            selector = planned_test_selector(plan)
            if selector is not None:
                platform_review['testSelector'] = selector
        job_lock = platform_job_lock(journal_path, plan['target'].get('checkout'))
    intent = docker_job_intent(prepared, dependency_store_sha256, script, platform_review)
    with job_lock:
        journal = EffectJournal(journal_path, {'mode': 'docker-engine-lifecycle', 'effectId': intent['effectId']})
        engine = docker_engine.DockerEngine(trusted_docker_policy['endpoint'], request, response)
        job = docker_engine.DockerRecipeJob(journal, engine, intent,
                                           trusted_docker_policy['imageDigest'], script, clock)
        return job.advance()


def _prepare_docker_recipe(plan, binding, policy, policy_pin, observation):
    fields(policy, 'version endpoint imageDigest adapterSha256 runtimeSha256 controlManifestSha256',
           'Docker controller policy')
    version_one(policy, 'Docker controller policy')
    if (not digest(policy_pin) or fingerprint(policy) != policy_pin
            or not isinstance(policy['endpoint'], str)
            or not re.fullmatch(r'unix:///[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.sock', policy['endpoint'])
            or '..' in PurePosixPath(policy['endpoint'].removeprefix('unix://')).parts
            or not image_digest(policy['imageDigest'])
            or not all(digest(policy[key]) for key in ('adapterSha256', 'runtimeSha256'))
            or policy['controlManifestSha256'] != binding['controlManifestSha256']):
        raise Refused('Docker controller policy identity or pin mismatch')
    limits = plan['limits']
    memory = limits['memoryMiB'] * 1024 * 1024
    requested = {'networkMode': 'none', 'loopback': 'disabled',
                 'workspaceWrites': 'isolated', 'readIsolation': 'isolated',
                 'memoryBytes': memory, 'memoryAndSwapBytes': memory, 'processCount': limits['maxProcesses'],
                 'wholeContainerDeadlineSeconds': limits['timeoutSeconds'], 'dependencyStore': 'verified'}
    observed = {}
    if observation is not None:
        fields(observation, 'kind policyIdentity controls', 'Docker fixture observation')
        identity = {name: value for name, value in policy.items() if name != 'version'}
        if (observation['kind'] != 'synthetic-fixture'
                or observation['policyIdentity'] != identity
                or not isinstance(observation['controls'], dict)):
            raise Refused('Docker fixture observation does not match the controller policy')
        observed = observation['controls']
    controls = {}
    for name, value in requested.items():
        row = observed.get(name)
        row = row if isinstance(row, dict) else {}
        observation_status = row.get('status')
        if observation_status not in ('enforced', 'observed', 'requested', 'client-timeout-only'):
            observation_status = 'unknown'
        measured = row.get('value') if observation_status in ('enforced', 'observed') else None
        if type(measured) is not type(value):
            measured = None
        matched = measured == value and observation_status == 'enforced'
        controls[name] = {'requested': value, 'observed': measured,
                          'observationStatus': observation_status,
                          'status': 'fixture-enforced' if matched else 'unqualified'}
    unqualified = [name for name, row in controls.items() if row['status'] == 'unqualified']
    runtime_matches = _container_runtime_matches(plan['runtime'], policy['runtimeSha256'], planned_browser_runtime(plan))
    if not runtime_matches:
        unqualified.append('containerRuntimeIdentity')
    image_matches = binding.get('imageDigest', policy['imageDigest']) == policy['imageDigest']
    if not image_matches:
        unqualified.append('containerImageIdentity')
    intent = None
    if runtime_matches and image_matches:
        intent = {'binding': binding, 'dockerPolicySha256': policy_pin, 'requestedControls': requested}
        effect_id = fingerprint(intent)
        intent.update(effectId=effect_id, containerName='aim-ci-' + effect_id[:24])
    return {'version': 1, 'status': 'blocked-before-dispatch', 'backend': 'docker',
            'preparationStatus': 'fixture-controls-match' if not unqualified else 'unqualified',
            'evidenceKind': 'synthetic-fixture' if observation is not None else 'preparation-only',
            'binding': binding, 'policySha256': policy_pin, 'intent': intent, 'controls': controls,
            'unqualified': unqualified, 'executionAuthorized': False, 'processStarted': False,
            'exitCode': None, 'signal': None, 'oom': None, 'timedOut': None,
            'cleanup': 'unknown', 'resourceState': 'unknown', 'observedEffects': [], 'productAccepted': False}


def _docker_fixture_lifecycle(observation, intent):
    if not isinstance(observation, dict):
        return None
    keys = ('effectId', 'containerName', 'dockerPolicySha256')
    container_id = observation.get('containerId')
    if (any(observation.get(key) != intent[key] for key in keys)
            or observation.get('kind') != 'synthetic-fixture' or container_id != intent['fixtureContainerId']):
        return None
    lifecycle = observation.get('lifecycle')
    if not isinstance(lifecycle, dict) or lifecycle.get('processStarted') is not True:
        return None
    code, signal = lifecycle.get('exitCode'), lifecycle.get('signal')
    if not ((type(code) is int and 0 <= code <= 255 and signal is None)
            or (code is None and type(signal) is int and 1 <= signal <= 64)):
        return None
    return {'containerId': container_id, 'processStarted': True, 'exitCode': code, 'signal': signal,
            'oom': lifecycle.get('oom') if type(lifecycle.get('oom')) is bool else None,
            'timedOut': lifecycle.get('timedOut') if type(lifecycle.get('timedOut')) is bool else None,
            'cleanup': 'confirmed' if lifecycle.get('cleanup') == 'confirmed' else 'unknown',
            'resourceState': 'observed' if lifecycle.get('resourceState') == 'observed' else 'unknown'}


def _record_docker_fixture(journal_path, path, pin, control_pin, *, trusted_docker_policy,
                           trusted_docker_policy_pin, fixture_observation, fixture_container_id, fixture_backend):
    """Exercise the existing claim and reconciliation protocol with fixture responses."""
    preparation = check_recipe_execution(path, pin, control_pin, trusted_docker_policy=trusted_docker_policy,
                                         trusted_docker_policy_pin=trusted_docker_policy_pin,
                                         fixture_observation=fixture_observation)
    if (preparation['preparationStatus'] != 'fixture-controls-match'
            or preparation['evidenceKind'] != 'synthetic-fixture'):
        raise Refused('Docker fixture controls or runtime identity remain unqualified')
    if not digest(fixture_container_id):
        raise Refused('Docker fixture requires an independent container ID')
    intent = {**preparation['intent'], 'fixtureContainerId': fixture_container_id}
    claim = {'mode': 'docker-fixture-only', 'effectId': intent['effectId']}
    journal = EffectJournal(journal_path, claim, synthetic=True)
    identity = {key: intent[key] for key in ('effectId', 'containerName', 'dockerPolicySha256')}

    def observed_receipt(key, request, observed):
        lifecycle = _docker_fixture_lifecycle(observed, request)
        if lifecycle is None:
            return None
        result = {**preparation, 'status': 'simulated', 'fixtureLifecycle': lifecycle}
        return journal.receipt(key, fingerprint(request), result)

    def dispatch(key, request):
        receipt = observed_receipt(key, request, fixture_backend.observe(identity.copy()))
        if receipt is None:
            raise Refused('Docker fixture outcome is unknown')
        return receipt

    def lookup(key, request):
        receipt = observed_receipt(key, request, fixture_backend.lookup(identity.copy()))
        return {'state': 'completed', 'receipt': receipt} if receipt else {'state': 'unknown'}

    return journal.perform('docker:prepare', intent, dispatch, lookup=lookup)


def _simulate_recipe_test(path, pin, control_pin, *, current_plan, authorization_path,
                          authorization_pin, journal_path, test_backend):
    """Internal test seam. The backend never receives executable argv or a checkout."""
    plan, binding = _execution_plan(path, pin, control_pin)
    if fingerprint(current_plan) != fingerprint(plan):
        raise Refused('Current recipe plan or input identity differs from the frozen plan')
    if (plan['target'].get('repository') != _TEST_REPOSITORY
            or any(not isinstance(plan.get(name), dict) or plan[name].get('kind') != 'synthetic-fixture'
                   for name in ('review', 'approval'))):
        raise Refused('Test admission requires the reviewed synthetic fixture')
    safe_path(journal_path)
    if not journal_path.is_absolute() or journal_path.resolve() != journal_path:
        raise Refused('Test effect requires an absolute journal path')
    authorization = json.loads(pinned_bytes(authorization_path, authorization_pin, 'Test authorization'))
    expected = {'version': 1, 'kind': 'synthetic-test-authorization', 'effect': 'recipe:test',
                'binding': binding, 'journal': str(journal_path)}
    if fingerprint(authorization) != fingerprint(expected):
        raise Refused('Test authorization does not bind this effect, plan and journal')
    capabilities = test_backend.capabilities()
    if capabilities != dict.fromkeys(_NATIVE_CAPABILITIES, 'simulated'):
        raise Refused('Test backend does not cover every required capability in simulation')
    claim = {'binding': binding, 'authorizationSha256': authorization_pin, 'mode': 'simulation-only'}
    journal = EffectJournal(journal_path, claim, synthetic=True)
    intent = {'binding': binding, 'limits': plan['limits']}

    def dispatch(key, request):
        simulated_exit = test_backend.simulate_test()
        if type(simulated_exit) is not int or not 0 <= simulated_exit <= 255:
            raise Refused('Test backend returned an invalid simulated exit code')
        result = {'version': 1, 'status': 'simulated', 'backend': 'synthetic-test',
                  'binding': binding, 'limits': plan['limits'], 'capabilities': capabilities,
                  'executionAuthorized': False, 'processStarted': False, 'exitCode': None,
                  'simulatedExitCode': simulated_exit, 'observedEffects': []}
        return journal.receipt(key, fingerprint(request), result)

    return journal.perform('recipe:test', intent, dispatch)
