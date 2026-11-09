"""Pinned macOS runtime and single-attempt synthetic catalog execution."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from types import SimpleNamespace

from openai_budget import Refused, atomic_json, fingerprint, owned_file, read_owned, safe_path
from .budget import Budget
from .control_package import OFFLINE_PATH_INPUTS, inspect_checkout, sha256
from .offline_assignment import heading_edits, validate_assignment


LEDGER_FILES = ('synthetic-legacy.json', 'worker-state/budget-established.json',
                'worker-state/budget.json')
RETAINED_FILES = (*LEDGER_FILES, 'vendors/catalog/external-records.json')


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_identity(runtime, node, git, chromium):
    if sys.platform != 'darwin':
        raise Refused('Installed offline execution requires macOS sandbox-exec')
    safe_path(runtime)
    if not runtime.is_dir():
        raise Refused('Selected runtime directory is missing or moved')
    runtime = runtime.resolve(strict=True)
    tools = {'node': node, 'git': git, 'python': Path(sys.executable),
             'sandbox': Path('/usr/bin/sandbox-exec'), 'tar': Path('/usr/bin/tar')}
    tools = {key: path.resolve(strict=True) for key, path in tools.items()}
    if tools['git'].name != 'git' or not all(os.access(p, os.X_OK) and p.is_file() for p in tools.values()):
        raise Refused('Runtime tool selection requires executable Python, Node and a Git binary named git')
    modules = runtime / 'node_modules'
    for name in ('playwright/index.mjs', 'playwright-core/package.json', '@playwright/test/index.mjs'):
        if not (modules / name).is_file():
            raise Refused('Runtime is missing Playwright module ' + name)
    browsers = runtime / 'browsers'
    chromium = Path(chromium)
    if (chromium.is_absolute() or '..' in chromium.parts or not chromium.parts
            or chromium.name != 'chrome-headless-shell'):
        raise Refused('Choose the relative Chromium headless-shell executable under runtime browsers')
    executable = browsers / chromium
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise Refused('Runtime Chromium is missing or not executable')
    files = {}
    def fail(error):
        raise error

    for directory, folders, names in os.walk(runtime, onerror=fail):
        directory = Path(directory)
        for name in folders:
            safe_path(directory / name)
        for name in names:
            path = (directory / name).relative_to(runtime)
            with os.fdopen(owned_file(runtime, path, os.O_RDONLY), 'rb') as stream:
                files[str(path)] = [stat.S_IMODE(os.fstat(stream.fileno()).st_mode),
                                    hashlib.file_digest(stream, 'sha256').hexdigest()]
    descriptor = {'version': 1, 'root': str(runtime),
                  'host': {'macOS': platform.mac_ver()[0], 'architecture': platform.machine()},
                  'tools': {key: {'path': str(path), 'sha256': file_sha(path)} for key, path in tools.items()},
                  'chromium': str(executable), 'files': files}
    return {'sha256': fingerprint(descriptor), 'identity': descriptor}


def disjoint(paths):
    for first, second in itertools.combinations(paths, 2):
        if first.is_relative_to(second) or second.is_relative_to(first):
            raise Refused('Installation, runtime, checkout, origin and synthetic state must be separate')


def retained_status(state, binding):
    if not state.exists():
        raise Refused('No installed offline run exists for this synthetic state')
    established = json.loads(read_owned(state, 'binding.json'))
    if established != binding:
        raise Refused('Installed job identity differs from the retained synthetic state')
    if not (state / 'result.json').exists():
        raise Refused('Installed attempt has no final receipt. Retain state and reconcile without replay.')
    result = json.loads(read_owned(state, 'result.json'))
    if result.get('bindingSha256') != fingerprint(binding):
        raise Refused('Retained result is not bound to this installed job')
    if 'assignment' in binding:
        assignment = binding['assignment']
        if (result.get('target') != binding['target']
                or result.get('assignmentSha256') != binding['assignmentSha256']
                or result.get('recipe') != assignment['recipe']
                or result.get('issueNumber') != assignment['issueNumber']):
            raise Refused('Retained result target or assignment identity changed')
    actual = {name: sha256(read_owned(state, name)) for name in RETAINED_FILES}
    if result.get('retainedSha256') != actual:
        raise Refused('Retained synthetic budget or transport history changed')
    Budget.read_existing(state / 'worker-state', state / LEDGER_FILES[0])
    return result


def prepare_installed(root, args, manifest):
    if args.product != 'catalog':
        raise Refused('Installed execution only accepts the offline catalog profile')
    assignment = validate_assignment(args.assignment)
    profile_bytes = read_owned(root, 'profiles/catalog.json')
    profile = json.loads(profile_bytes)
    if profile.get('offlineOnly') is not True:
        raise Refused('Installed execution requires an offline-only profile')
    runtime = runtime_identity(args.runtime, args.node, args.git, args.chromium)
    if args.runtime_sha256 is not None and runtime['sha256'] != args.runtime_sha256:
        raise Refused('Installed runtime SHA-256 mismatch')
    if not re.fullmatch('[a-f0-9]{40}', args.head):
        raise Refused('An exact target source SHA is required')
    for path in (args.checkout, args.origin, args.state):
        safe_path(path)
    checkout, origin, state = (path.resolve() for path in (args.checkout, args.origin, args.state))
    runtime_root = Path(runtime['identity']['root'])
    disjoint((root, runtime_root, checkout, origin, state))
    if not checkout.is_dir() or not origin.is_dir():
        raise Refused('Pinned checkout or local origin is missing. Preserve existing synthetic state.')
    if not state.parent.is_dir():
        raise Refused('The parent of the synthetic state must already exist')
    tools = runtime['identity']['tools']
    if any(Path(tool['path']).is_relative_to(path) for tool in tools.values()
           for path in (root, checkout, origin, state)):
        raise Refused('Runtime tools must be independent of the installation, target and state')
    environment = {'PATH': str(Path(tools['git']['path']).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   'HOME': str(state / 'home'), 'TMPDIR': str(state / 'tmp'),
                   'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    target = inspect_checkout(checkout, profile, git_path=tools['git']['path'])
    if target['head'] != args.head or target['origin'] != str(origin):
        raise Refused('Checkout head or origin differs from the pinned catalog target')
    origin_head = subprocess.check_output(
        [tools['git']['path'], 'rev-parse', '--verify', 'refs/heads/main^{commit}'],
        cwd=origin, env=environment, text=True, timeout=20).strip()
    if origin_head != args.head:
        raise Refused('Catalog origin main differs from the pinned target commit')
    for edit in heading_edits(assignment):
        path = edit['path']
        if path not in profile['allowedPaths']:
            raise Refused(f'Registered catalog heading edit is outside the verified profile at {path}')
        try:
            source = subprocess.check_output(
                [tools['git']['path'], 'show', f'{args.head}:catalog/{path}'],
                cwd=checkout, env=environment, text=True, stderr=subprocess.DEVNULL, timeout=20)
        except subprocess.CalledProcessError:
            raise Refused(f'Pinned commit lacks the registered catalog heading source at {path}') from None
        if edit['old'] not in source.splitlines(keepends=True) or source.count(edit['old']) != 1:
            raise Refused(f'Pinned commit lacks the unique declared catalog heading line at {path}')
    binding = {'version': 2, 'mode': 'installed-synthetic-offline', 'packageVersion': manifest['version'],
               'installation': str(root), 'manifestSha256': args.manifest_sha256,
               'runtimeSha256': runtime['sha256'], 'profileSha256': sha256(profile_bytes),
               'product': args.product, 'target': target, 'state': str(state),
               'assignment': assignment, 'assignmentSha256': fingerprint(assignment)}
    return binding, runtime, environment


def setup_offline(root, args, manifest):
    safe_path(args.output)
    output = args.output.resolve()
    if not output.parent.is_dir():
        raise Refused('The configuration output parent must already exist')
    if output.exists():
        raise Refused('Offline configuration already exists; choose a new output path')
    binding, runtime, _ = prepare_installed(root, args, manifest)
    target = binding['target']
    state = Path(binding['state'])
    descriptor = runtime['identity']
    disjoint((output, root, Path(descriptor['root']), Path(target['root']), Path(target['origin']), state))
    if state.exists():
        retained_status(state, binding)
    inputs = {'manifest_sha256': args.manifest_sha256, 'runtime_sha256': runtime['sha256'],
              'product': args.product, 'head': target['head'], 'checkout': target['root'],
              'origin': target['origin'], 'state': str(state), 'runtime': descriptor['root'],
              'node': descriptor['tools']['node']['path'], 'git': descriptor['tools']['git']['path'],
              'chromium': str(Path(descriptor['chromium']).relative_to(Path(descriptor['root']) / 'browsers'))}
    config = {'version': 2, 'path': str(output), 'inputs': inputs, 'binding': binding}
    encoded = (json.dumps(config, indent=2, sort_keys=True) + '\n').encode()
    with os.fdopen(owned_file(output.parent, output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return {'config': str(output), 'configSha256': sha256(encoded), 'binding': binding}


def configured_offline(root, config, manifest, action):
    if config['binding'].get('installation') != str(root):
        raise Refused('Offline configuration belongs to a different installation. Preserve existing synthetic state.')
    inputs = config['inputs']
    args = SimpleNamespace(**{key: Path(value) if key in OFFLINE_PATH_INPUTS else value
                              for key, value in inputs.items()})
    args.assignment = config['binding'].get('assignment')
    return run_installed(root, args, manifest, status=action == 'status-configured-offline',
                         preflight=action == 'preflight-offline', expected_binding=config['binding'])


def run_installed(root, args, manifest, *, status=False, preflight=False, expected_binding=None):
    binding, runtime, environment = prepare_installed(root, args, manifest)
    if expected_binding is not None and binding != expected_binding:
        raise Refused('Offline configuration binding changed. Preserve existing synthetic state.')
    state = Path(binding['state'])
    existing = retained_status(state, binding) if state.exists() or status else None
    if preflight:
        return {'ready': True, 'stateStatus': 'retained' if existing else 'new', 'binding': binding}
    if existing is not None:
        return existing
    checkout = Path(binding['target']['root'])
    tools = runtime['identity']['tools']
    runtime_root = Path(runtime['identity']['root'])
    state.mkdir(mode=0o700)
    atomic_json(state / 'binding.json', binding)
    (state / 'home').mkdir()
    (state / 'tmp').mkdir()
    atomic_json(state / LEDGER_FILES[0], {'synthetic': True, 'requests': []})
    atomic_json(state / 'runtime-identity.json', runtime)

    # All Git transports from this point are local paths controlled by the fixture.
    os.environ.clear()
    os.environ.update(environment, GIT_ALLOW_PROTOCOL='file')
    from . import controller as controller
    from .offline import SyntheticTransports
    browser = {'node': tools['node']['path'], 'modules': str(runtime_root / 'node_modules'),
               'chromium': runtime['identity']['chromium'], 'browsers': str(runtime_root / 'browsers')}
    transports = SyntheticTransports(state, 'catalog', checkout=checkout, head=args.head, browser=browser)
    assignment = binding['assignment']
    issue = assignment['issueNumber']
    heading = assignment['toHeading']
    with controller.offline_transports(transports):
        transports.set_job(issue, assignment['title'], assignment['fromHeading'], heading,
                           f'Catalog heading says {heading}', edits=heading_edits(assignment))
        delivery = controller.run_issue(issue, transports.config)
    if delivery['stage'] != 'offline_verified' or not delivery['productionProof']['passed']:
        raise Refused('Installed job stopped before browser verification. Retain the synthetic state.')
    budget = Budget.read_existing(transports.state, transports.legacy)
    result = {'bindingSha256': fingerprint(binding), 'stage': delivery['stage'],
              'productAccepted': delivery['productAccepted'], 'target': binding['target'], 'state': str(state),
              'assignmentSha256': binding['assignmentSha256'], 'issueNumber': issue,
              'recipe': assignment['recipe'], 'synthetic': True, 'syntheticModelCalls': len(budget['requests']),
              'browser': browser, 'browserProof': delivery['productionProof'],
              'retainedSha256': {name: sha256(read_owned(state, name)) for name in RETAINED_FILES}}
    atomic_json(state / 'result.json', result)
    return result
