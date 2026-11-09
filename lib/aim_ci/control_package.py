"""Versioned local distribution of the current controller's control files."""
import gzip
from hashlib import sha1
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from .control_archive import (FILES, FORMAT, MANIFEST, MAX_ARCHIVE, MAX_FILE, MIN_RUNTIME_PYTHON,
                              RUNTIME_PYTHON_REQUIREMENT, VERSION_PATTERN, check_digest, install,
                              parse_manifest, sha256, verification_receipt, verify)
from .owned_paths import Refused, new_owned_directory, read_owned, safe_path, write_owned


def installed(root):
    return os.path.lexists(root / MANIFEST)


def require_source_checkout(root):
    if installed(root):
        raise Refused('This entry point is inspection-only in an installation. Use aim-ci-control.py.')


def add_root_preview_input(parser):
    inputs = parser.add_mutually_exclusive_group(required=True)
    for kind in ('issue', 'task'):
        inputs.add_argument(f'--{kind}-file', type=Path)
        parser.add_argument(f'--{kind}-sha256')


def root_preview_input(parser, args):
    if args.task_file is not None:
        if args.task_sha256 is None or args.issue_sha256 is not None:
            parser.error('--task-file requires only --task-sha256')
        return {'root_task': (args.task_file, args.task_sha256), 'intake_only': True}
    if args.issue_sha256 is None or args.task_sha256 is not None:
        parser.error('--issue-file requires only --issue-sha256')
    return {'root_issue': (args.issue_file, args.issue_sha256)}


def _payload(root):
    require_source_checkout(root)
    return {name: read_owned(root, name, MAX_FILE) for name in FILES}


def _build_archive(payload, version):
    if not re.fullmatch(VERSION_PATTERN, version):
        raise Refused('Use a version such as 0.1.0-local.1')
    manifest = {'format': FORMAT, 'schemaVersion': 1, 'version': version,
                'files': {name: sha256(content) for name, content in payload.items()}}
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    members = {**payload, MANIFEST: encoded}
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, content in sorted(members.items()):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
    data = buffer.getvalue()
    if len(data) > MAX_ARCHIVE:
        raise Refused('Controller archive exceeds the size limit')
    return data, {'version': version, 'archiveSha256': sha256(data), 'manifestSha256': sha256(encoded), 'files': len(payload)}


def build(root, output, version):
    data, receipt = _build_archive(_payload(root), version)
    safe_path(output)
    write_owned(output.parent, output.name, data, exclusive=True)
    return receipt


def build_release(root, output, version):
    return _build_release(_payload(root), output, version)


def _build_release(payload, output, version):
    data, receipt = _build_archive(payload, version)
    bootstrap = io.BytesIO()
    with zipfile.ZipFile(bootstrap, 'w', compression=zipfile.ZIP_STORED) as bundle:
        for name, source in (
                ('__main__.py', 'lib/aim_ci/control_archive.py'),
                ('aim_ci/__init__.py', 'lib/aim_ci/__init__.py'),
                ('aim_ci/owned_paths.py', 'lib/aim_ci/owned_paths.py')):
            bundle.writestr(zipfile.ZipInfo(name), payload[source])
    archive = f'aim-ci-control-{version}.tar.gz'
    files = {archive: data, 'aim-ci-bootstrap.pyz': bootstrap.getvalue(),
             'README.md': payload['docs/aim-ci-onboarding.md']}
    metadata = {**receipt, 'archive': archive, 'bootstrap': 'aim-ci-bootstrap.pyz',
                'filesSha256': {name: sha256(content) for name, content in files.items()}}
    files['release.json'] = (json.dumps(metadata, indent=2, sort_keys=True) + '\n').encode()
    with new_owned_directory(output) as directory:
        for name, content in files.items():
            write_owned(directory, name, content, exclusive=True)
    return {**metadata, 'bundle': str(output.absolute())}


def _candidate_source(root, head, repository, payload):
    executable = shutil.which('git')
    if executable is None or Path(executable).resolve().is_relative_to(root):
        raise Refused('Candidate packaging requires Git outside the checkout')
    environment = {'PATH': os.defpath, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1',
                   'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_REPLACE_OBJECTS': '1'}

    def git(*args):
        argv = [str(Path(executable).resolve()), '--no-replace-objects', '-c', 'core.fsmonitor=false',
                '-c', 'core.hooksPath=/dev/null', *args]
        result = subprocess.run(argv, cwd=root, env=environment, capture_output=True, timeout=20)
        if result.returncode or len(result.stdout) > MAX_FILE:
            raise Refused(f'Candidate Git could not read {args[0]}')
        return result.stdout

    def current_head():
        if git('rev-parse', '--verify', 'HEAD') != (head + '\n').encode():
            raise Refused('Candidate checkout HEAD differs from the validated commit')

    if git('rev-parse', '--show-toplevel') != (str(root) + '\n').encode():
        raise Refused('Candidate packaging requires the Git worktree root')
    current_head()
    origin = f'https://github.com/{repository}'
    if git('config', '--local', '--no-includes', '--get-all', 'remote.origin.url') not in (
            (origin + '\n').encode(), (origin + '.git\n').encode()):
        raise Refused('Candidate checkout origin differs from the workflow repository')
    tree = git('rev-parse', '--verify', head + '^{tree}').decode().strip()
    if not re.fullmatch('[a-f0-9]{40}', tree):
        raise Refused('Candidate commit has an invalid Git tree identity')

    def object_bytes(kind, oid, expected_size=None):
        size = git('cat-file', '-s', oid)
        if (not re.fullmatch(rb'[0-9]{1,9}\n', size) or int(size) > MAX_FILE
                or expected_size is not None and int(size) != expected_size):
            raise Refused(f'Candidate Git {kind} size differs or exceeds its bound')
        data = git('cat-file', kind, oid)
        if len(data) != int(size) or sha1(f'{kind} {len(data)}\0'.encode() + data).hexdigest() != oid:
            raise Refused(f'Candidate Git {kind} bytes differ from their object ID')
        return data

    if not object_bytes('commit', head).startswith(f'tree {tree}\n'.encode()):
        raise Refused('Candidate tree differs from the validated commit')
    object_bytes('tree', tree)
    committed = {}
    for entry in git('ls-tree', '-r', '-z', '--full-tree', head, '--', *payload).split(b'\0'):
        if not entry:
            continue
        match = re.fullmatch(rb'100(?:644|755) blob ([a-f0-9]{40})\t([^\0]+)', entry)
        if match is None:
            raise Refused('Candidate inputs must be regular committed files')
        name, oid = match[2].decode(), match[1].decode()
        if name in committed or name not in payload:
            raise Refused('Unexpected or duplicate candidate source input')
        if object_bytes('blob', oid, len(payload[name])) != payload[name]:
            raise Refused(f'Candidate input differs from the validated commit: {name}')
        committed[name] = oid
    if set(committed) != set(payload):
        raise Refused('Candidate checkout is missing committed package inputs')
    current_head()
    return tree


def _candidate_smoke(output, release):
    with tempfile.TemporaryDirectory(prefix='aim-ci-candidate-proof-', dir=output.parent) as location:
        scratch = Path(location)
        empty, home, installed = (scratch/name for name in ('no-tools', 'home', 'install'))
        empty.mkdir()
        home.mkdir()
        environment = {'PATH': str(empty), 'HOME': str(home), 'LC_ALL': 'C'}
        executable = str(Path(sys.executable).resolve())

        def invoke(entry, *args, expected=0):
            result = subprocess.run([executable, '-IBS', str(entry), *map(str, args)], cwd=scratch,
                                    env=environment, capture_output=True, timeout=30)
            if result.returncode != expected or result.stderr or len(result.stdout) > MAX_FILE:
                raise Refused(f'Candidate installed check failed: {args[0]} (exit {result.returncode}, expected {expected})')
            return json.loads(result.stdout)

        bootstrap = output/release['bootstrap']
        archive = output/release['archive']
        flags = ('--archive', archive, '--sha256', release['archiveSha256'])
        verified = invoke(bootstrap, 'verify', *flags)
        installed_result = invoke(bootstrap, 'install', *flags, '--destination', installed)
        manifest_pin = release['manifestSha256']
        manifest = verify_installation(installed, manifest_pin)
        expected = {**manifest['files'], MANIFEST: manifest_pin}

        def installed_hashes():
            return {p.relative_to(installed).as_posix(): sha256(p.read_bytes())
                    for p in installed.rglob('*') if p.is_file()}

        if installed_hashes() != expected:
            raise Refused('Candidate installation differs from its manifest')
        ready = invoke(installed/'aim-ci-control.py', 'readiness', '--manifest-sha256', manifest_pin, expected=3)
        config = {'version': 1, 'manifestSha256': manifest_pin, 'plan': None, 'containerProfile': None,
                  'dockerPolicy': None, 'platform': None, 'stateDir': None}
        config_data = json.dumps(config).encode()
        write_owned(scratch, 'incomplete-build.json', config_data, exclusive=True)
        project = invoke(installed/'aim-ci-control.py', 'readiness', '--workflow', 'build',
                         '--manifest-sha256', manifest_pin, '--config', scratch/'incomplete-build.json',
                         '--config-sha256', sha256(config_data), expected=3)
        if (verified != {'verified': True, 'version': release['version'], 'archiveSha256': release['archiveSha256'],
                         'manifestSha256': manifest_pin}
                or installed_result != {'version': release['version'], 'manifestSha256': manifest_pin,
                                        'installed': str(installed), 'mode': 'installed-offline-capable'}
                or ready.get('version') != release['version'] or ready.get('packageVerified') is not True
                or ready.get('ready') is not False or ready.get('checkout') is not None
                or ready.get('runtimePythonSupported') is not True
                or ready.get('toolsOnPath') != dict.fromkeys(('git', 'gh', 'node', 'npm', 'sandbox-exec'), False)
                or project.get('mode') != 'local-operator' or project.get('status') != 'configuration-incomplete'
                or project.get('ready') is not False or project.get('executionAuthorized') is not False
                or project.get('package', {}).get('verified') is not True
                or set(project.get('unset', [])) != {'plan', 'containerProfile', 'dockerPolicy', 'platform', 'stateDir'}
                or set(project.get('runtime', {}).values()) != {'not-checked'}
                or read_owned(scratch, 'incomplete-build.json', MAX_FILE) != config_data
                or installed_hashes() != expected or any(empty.iterdir()) or any(home.iterdir())):
            raise Refused('Candidate installed proof differs from the source-free read-only contract')
        return {'outsideCheckout': True, 'emptyPath': True, 'emptyHome': True, 'systemSiteDisabled': True,
                'installedFilesUnchanged': True,
                'bootstrapVerify': {'exitCode': 0, 'result': verified},
                'bootstrapInstall': {'exitCode': 0, 'manifestSha256': manifest_pin, 'files': len(manifest['files'])},
                'installedReadiness': {'exitCode': 3, 'result': ready},
                'projectReadiness': {'exitCode': 3, 'result': project}}


def build_development_candidate(root, output, *, commit, repository, run_id, run_attempt, workflow_ref, workflow_sha):
    if (not re.fullmatch('[a-f0-9]{40}', commit) or not re.fullmatch('[a-f0-9]{40}', workflow_sha)
            or not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or not re.fullmatch('[1-9][0-9]{0,19}', run_id) or not re.fullmatch('[1-9][0-9]{0,8}', run_attempt)
            or not re.fullmatch(re.escape(repository) + r'/.github/workflows/aim-ci\.yml@refs/(?:heads/main|pull/[1-9][0-9]*/merge)', workflow_ref)):
        raise Refused('Invalid candidate source or workflow identity')
    output = output.absolute()
    if output != output.resolve() or output.is_relative_to(root) or root.is_relative_to(output):
        raise Refused('Candidate output and proof must be outside the checkout')
    payload = _payload(root)
    tree = _candidate_source(root, commit, repository, payload)
    version = f'0.0.0-dev.g{commit}'
    release = _build_release(payload, output, version)

    def verify_bundle_files():
        for name, pin in release['filesSha256'].items():
            check_digest(read_owned(output, name, MAX_ARCHIVE), pin, f'Candidate bundle file {name}')

    verify_bundle_files()
    proof = _candidate_smoke(output, release)
    verify_bundle_files()
    release_data = read_owned(output, 'release.json', MAX_FILE)
    if json.loads(release_data) != {key: value for key, value in release.items() if key != 'bundle'}:
        raise Refused('Candidate release metadata changed during verification')
    metadata = {'schemaVersion': 1, 'kind': 'aim-ci-development-candidate', 'acceptance': 'unaccepted',
                'version': version, 'source': {'repository': repository, 'commit': commit, 'tree': tree},
                'workflow': {'runId': run_id, 'runAttempt': run_attempt, 'ref': workflow_ref, 'sha': workflow_sha},
                'archive': release['archive'], 'bootstrap': release['bootstrap'], 'manifestSha256': release['manifestSha256'],
                'filesSha256': {**release['filesSha256'], 'release.json': sha256(release_data)},
                'installedProof': proof}
    write_owned(output, 'candidate.json', (json.dumps(metadata, indent=2, sort_keys=True) + '\n').encode(), exclusive=True)
    return metadata


def inspect_checkout(checkout, profile, *, git_path=None):
    checkout = checkout.resolve(strict=True)
    path = str(Path(git_path).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin' if git_path else os.environ.get('PATH', os.defpath)
    env = {'PATH': path, 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
           'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''}

    def git(*args, cwd=checkout):
        return subprocess.check_output([git_path or 'git', '-c', 'core.fsmonitor=false', *args],
                                       cwd=cwd, env=env, text=True, stderr=subprocess.DEVNULL).rstrip('\n')

    version = re.match(r'^git version (\d+)\.(\d+)(?:\.(\d+))?(?=[ .-]|$)', git('--version'))
    if not version or tuple(int(part or 0) for part in version.groups()) < (2, 54, 0):
        raise Refused('Checkout inspection requires Git 2.54.0 or later')
    if git('rev-parse', '--show-toplevel') != str(checkout):
        raise Refused('Readiness requires an explicit Git worktree root')
    try:
        product_type = git('cat-file', '-t', 'HEAD:' + profile['product'])
    except subprocess.CalledProcessError:
        raise Refused('Configured product tree is missing or unavailable locally') from None
    if product_type != 'tree':
        raise Refused('Checkout does not contain the configured product tree')
    origins = git('remote', 'get-url', '--all', 'origin').splitlines()
    pushes = git('remote', 'get-url', '--push', '--all', 'origin').splitlines()
    if len(origins) != 1 or len(pushes) != 1:
        raise Refused('Checkout must have exactly one fetch and one push origin')
    origin, push = origins[0], pushes[0]
    if profile.get('offlineOnly'):
        path = Path(origin)
        valid_origin = (origin == push and path.is_absolute() and path.is_dir()
                        and git('rev-parse', '--is-bare-repository', cwd=path) == 'true')
    else:
        repo = profile['repository']
        allowed = (f'https://github.com/{repo}.git', f'git@github.com:{repo}.git')
        valid_origin = origin in allowed and push in allowed
    if not valid_origin:
        raise Refused('Checkout origin does not match the registered product profile')
    return {'root': str(checkout), 'head': git('rev-parse', 'HEAD'), 'origin': origin}


def verify_installation(root, expected):
    encoded = read_owned(root, MANIFEST)
    check_digest(encoded, expected, 'Installed manifest')
    manifest = parse_manifest(encoded)
    for name, digest in manifest['files'].items():
        check_digest(read_owned(root, name, MAX_FILE), digest, name)
    return manifest


def readiness(root, expected, product, checkout=None):
    manifest = verify_installation(root, expected)
    profile = json.loads(read_owned(root, f'profiles/{product}.json'))
    target = inspect_checkout(checkout, profile) if checkout is not None else None
    python_supported = sys.version_info >= MIN_RUNTIME_PYTHON
    blockers = ['committed-controls-integration-not-configured', 'runtime-dependencies-not-verified',
                'credentials-and-journals-not-checked']
    if not python_supported:
        blockers.insert(0, 'unsupported-python-version')
    return {
        'version': manifest['version'], 'packageVerified': True, 'ready': False,
        'product': product, 'profileRepository': profile['repository'], 'checkout': target,
        'platform': sys.platform, 'pythonVersion': sys.version.split()[0],
        'minimumRuntimePython': RUNTIME_PYTHON_REQUIREMENT, 'runtimePythonSupported': python_supported,
        'toolsOnPath': {name: shutil.which(name) is not None for name in ('git', 'gh', 'node', 'npm', 'sandbox-exec')},
        'blockers': blockers,
        'offlineOnlyProfile': profile.get('offlineOnly', False),
    }


OFFLINE_PATH_INPUTS = ('runtime', 'node', 'git', 'chromium', 'checkout', 'origin', 'state')
OFFLINE_PIN_INPUTS = ('manifest_sha256', 'runtime_sha256', 'head', 'product')


def load_offline_configuration(path, expected):
    safe_path(path)
    path = path.resolve()
    encoded = read_owned(path.parent, path.name)
    check_digest(encoded, expected, 'Offline configuration')
    config = json.loads(encoded)
    if (not isinstance(config, dict) or set(config) != {'version', 'path', 'inputs', 'binding'}
            or type(config['version']) is not int or config['version'] != 2
            or not isinstance(config['binding'], dict) or not isinstance(config['inputs'], dict)
            or set(config['inputs']) != {*OFFLINE_PATH_INPUTS, *OFFLINE_PIN_INPUTS}
            or any(not isinstance(value, str) or not value for value in config['inputs'].values())):
        raise Refused('Invalid offline configuration')
    if config['path'] != str(path):
        raise Refused('Offline configuration moved. Preserve the original configuration and state.')
    return config
