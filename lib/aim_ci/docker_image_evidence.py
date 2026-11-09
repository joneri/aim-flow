"""Byte verification and internal, journaled readback from a never-started container."""
from base64 import b64encode
from contextlib import contextmanager
import fcntl
from hashlib import sha1, sha256, sha512
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
from time import monotonic
from urllib.parse import urlencode

from openai_budget import Refused, canonical, fingerprint
from . import docker_engine
from .control_package import MAX_FILE, verify_installation
from .effects import EffectJournal
from .installed_runtime import file_sha
from .npm_platform import FULL_STORE, PLATFORM_STORE, platform_store, project_path
from .owned_paths import owned_file
from .platform_build import CONTAINER_HELPER as PLATFORM_ENTRYPOINT, HELPER
from .project_discovery import SCRIPT_NAME, unique_json
from .recipe_plan import (PLAYWRIGHT_CONFIG, _execution_plan, _prepare_docker_recipe, browser_runtime_files,
                          browser_runtime_paths, digest, fields, inspect_npm, planned_browser_runtime,
                          planned_source_check, planned_test_selector, read_container_profile)


_LAYOUT = '/opt/aim-ci/qualification'
_ENTRYPOINT = '/opt/aim-ci/run-recipe'
_ZERO_TIME = '0001-01-01T00:00:00Z'
_BLOCK = 512
_CHUNK = 64 * 1024
_MAX_MEMBERS = 4096
# Legacy plans have no reviewed file inventory from which to derive archive bounds.
_MAX_UNREVIEWED_SOURCE = 32 * 1024 * 1024
_MAX_STORE = 512 * 1024 * 1024
_MAX_BLOB = 64 * 1024 * 1024
_MAX_TOOL = 150 * 1024 * 1024
_COMPONENT = r'[A-Za-z0-9@_.-]{1,128}'
NO_INSTALL_STORE = 'npm-no-install-v1'


def _literal_path(name, *, browser=False):
    component = r'[A-Za-z0-9@_.+-]{1,128}' if browser else _COMPONENT
    return (isinstance(name, str) and len(name) <= 240
            and all(re.fullmatch(component, part) and part not in ('.', '..')
                    for part in name.split('/')))


def _reviewed_inputs(plan_path, plan_pin, control_pin, profile_path, profile_pin, entrypoint_pin, *, platform_build=False):
    plan, binding = _execution_plan(plan_path, plan_pin, control_pin)
    if binding.get('containerProfileSha256') != profile_pin or not digest(entrypoint_pin):
        raise Refused('Image verification requires the bound container profile and pinned entrypoint')
    profile = read_container_profile(profile_path, profile_pin)
    tools = profile['tools']
    browser = planned_browser_runtime(plan)
    if (profile.get('browserRuntime') != browser or profile['imageDigest'] != binding['imageDigest']
            or profile['dependencyStoreSha256'] != binding['dependencyStoreSha256']
            or tools != plan['runtime']['tools']):
        raise Refused('Container profile bytes disagree with the frozen plan')
    selection = plan.get('testSelection')
    script = selection.get('script') if isinstance(selection, dict) else None
    if (not isinstance(script, str) or not re.fullmatch(SCRIPT_NAME, script)
            or selection.get('selectedBy') != 'pinned-recipe'):
        raise Refused('Image verification requires one pinned literal test script')
    paths = {'node': tools['node']['path'], 'npm': tools['npm']['path'], 'entrypoint': PLATFORM_ENTRYPOINT if platform_build else _ENTRYPOINT,
             'commit': _LAYOUT + '/source.commit', 'source': _LAYOUT + '/source',
             'store': _LAYOUT + '/dependency-store'}
    if planned_source_check(plan) is not None:
        if not platform_build:
            raise Refused('Source check requires the maintained platform helper and readback')
        paths['proof'] = _LAYOUT + '/source.proof.json'
    if browser is not None:
        if not platform_build:
            raise Refused('Browser inventory requires the maintained platform readback')
        paths.update(browser_runtime_paths(browser))
    if any(not path.startswith('/') or not _literal_path(path[1:], browser=role.startswith('browser-'))
           for role, path in paths.items()):
        raise Refused('Unsupported fixed image readback path')
    if len(set(paths.values())) != len(paths) or any(
            paths[role].startswith(paths[group] + '/') for group in ('source', 'store')
            for role in paths if role != group):
        raise Refused('Overlapping image readback paths')
    return plan, binding, tools, script, paths


def _archive(stream, root, maximum, member_limit, keep, *, directory=False, browser=False,
             reviewed_files=None, retain_limit=MAX_FILE):
    """Read a restricted uncompressed tar without extracting or honoring extended headers."""
    total = source_bytes = 0
    reviewed_directories = None
    member_count = _MAX_MEMBERS
    if reviewed_files is not None:
        if root != 'source' or not directory:
            raise Refused('Reviewed source inventory requires the source directory archive')
        reviewed_directories = {root}
        for name, row in reviewed_files.items():
            if (not isinstance(row, dict) or not project_path(name) or type(row.get('size')) is not int or row['size'] < 0
                    or row.get('mode') not in (0o644, 0o755) or not digest(row.get('sha256'))):
                raise Refused('Invalid reviewed source archive inventory')
            reviewed_directories.update(str(parent) for parent in PurePosixPath(root + '/' + name).parents
                                        if str(parent) != '.')
        member_count = len(reviewed_files) + len(reviewed_directories)
    archive_hash = sha256()
    deadline = monotonic() + 120
    read = getattr(stream, 'read1', stream.read)

    def chunk(size):
        nonlocal total
        if monotonic() >= deadline:
            raise Refused('Image readback archive exceeded the read deadline')
        block = read(size)
        total += len(block)
        if total > maximum:
            raise Refused('Image readback archive exceeds its size bound')
        archive_hash.update(block)
        return block

    def exact(size):
        value = bytearray()
        while len(value) < size:
            data = chunk(size - len(value))
            if not data:
                raise Refused('Truncated image readback archive')
            value.extend(data)
        return bytes(value)

    files, directories = {}, set()
    zero = bytes(_BLOCK)
    while (header := exact(_BLOCK)) != zero:
        if len(files) + len(directories) >= member_count:
            raise Refused('Source archive exceeds its exact reviewed selection' if reviewed_files is not None
                          else 'Too many image readback members')
        try:
            item = tarfile.TarInfo.frombuf(header, 'ascii', 'strict')
        except (tarfile.HeaderError, UnicodeError, ValueError) as error:
            raise Refused('Invalid image readback tar header') from error
        name = item.name
        allowed = name == root or (directory and name.startswith(root + '/'))
        if (not allowed or not (project_path(name) if root == 'source' else _literal_path(name, browser=browser))
                or name in files or name in directories
                or item.linkname or item.type not in (tarfile.REGTYPE, tarfile.DIRTYPE)
                or not 0 <= item.size <= member_limit):
            raise Refused('Unexpected, duplicate or unsafe image readback member')
        if item.type == tarfile.DIRTYPE:
            if not directory or item.size != 0 or item.mode != 0o755:
                raise Refused('Unsupported image readback directory')
            if reviewed_directories is not None and name not in reviewed_directories:
                raise Refused('Unselected source archive directory')
            directories.add(name)
            continue
        if (directory and name == root) or item.mode not in (0o644, 0o755):
            raise Refused('Unsupported image readback file mode or root')
        relative = name[len(root) + 1:] if directory else name
        if reviewed_files is not None:
            row = reviewed_files.get(relative)
            if row is None or item.size != row['size'] or item.mode != row['mode']:
                raise Refused('Source archive member differs from its reviewed size, mode or selection')
        retain = relative in keep
        if retain and reviewed_files is None and item.size > retain_limit:
            raise Refused('Image manifest or source input exceeds its size bound')
        if root == 'source' and reviewed_files is None:
            source_bytes += item.size
            if source_bytes > _MAX_UNREVIEWED_SOURCE:
                raise Refused('Image source exceeds its cumulative file size bound')
        data = bytearray() if retain else None
        h256, h512 = sha256(), sha512()
        blob = sha1(f'blob {item.size}\0'.encode()) if directory and root == 'source' else None
        remaining = item.size
        head = bytearray() if browser else None
        while remaining:
            part = exact(min(remaining, _CHUNK))
            h256.update(part)
            if head is not None and len(head) < 20:
                head.extend(part[:20 - len(head)])
            if blob is not None:
                blob.update(part)
            if root == 'dependency-store':
                h512.update(part)
            if retain:
                data.extend(part)
            remaining -= len(part)
        if reviewed_files is not None and h256.hexdigest() != reviewed_files[relative]['sha256']:
            raise Refused('Source archive bytes differ from their reviewed hash')
        if any(exact(-item.size % _BLOCK)):
            raise Refused('Nonzero image readback file padding')
        files[name] = {'mode': item.mode, 'size': item.size, 'sha256': h256.hexdigest(),
                       'sha512': h512.digest(), 'blob': blob.digest() if blob is not None else None,
                       'data': bytes(data) if retain else None}
        if browser:
            files[name]['header'] = bytes(head)
    if exact(_BLOCK) != zero:
        raise Refused('Image readback archive lacks a complete terminator')
    while tail := chunk(_CHUNK):
        if any(tail):
            raise Refused('Unexpected data after image readback archive')
    if not directory and set(files) != {root}:
        raise Refused('Image readback archive lacks the requested file')
    if directory:
        parents = {root}
        for name in files:
            parents.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
        if directories != parents:
            raise Refused('Image readback directory inventory differs from its regular files')
    if reviewed_files is not None and {name[len(root) + 1:] for name in files} != set(reviewed_files):
        raise Refused('Source archive lacks its exact reviewed file inventory')
    return {name[len(root) + 1:] if directory else name: value for name, value in files.items()}, archive_hash.hexdigest()


def _git_id(kind, data):
    return sha1(f'{kind} {len(data)}\0'.encode() + data).digest()


def _git_tree(source):
    directories = {'': {}}
    for path, row in source.items():
        parts = path.split('/')
        parent = ''
        for part in parts[:-1]:
            child = parent + '/' + part if parent else part
            directories[parent][part] = ('40000', child)
            directories.setdefault(child, {})
            parent = child
        directories[parent][parts[-1]] = (f"100{row['mode']:o}", row['blob'])
    hashes = {}
    for path in sorted(directories, key=lambda item: (item.count('/') + bool(item)), reverse=True):
        entries = directories[path]
        data = bytearray()
        for name, (mode, value) in sorted(entries.items(), key=lambda row: row[0] + (
                '/' if row[1][0] == '40000' else '')):
            data.extend(f'{mode} {name}\0'.encode())
            data.extend(hashes[value] if mode == '40000' else value)
        hashes[path] = _git_id('tree', data)
    return hashes[''].hex()


def _check_source(source, commit, binding, script, selector, *, proof=None, source_files=None, execution=None):
    tree = binding['sourceTree']
    if execution is not None:
        from .source_subset import verify_source_subset

        if (not isinstance(proof, dict) or fingerprint(proof) != binding.get('sourceProofSha256')
                or proof.get('commit') != binding['sourceCommit'] or proof.get('tree') != tree
                or proof.get('selectedPaths') != execution['sourcePaths']
                or fingerprint(execution) != binding.get('executionSha256')):
            raise Refused('Image source subset proof differs from the exact reviewed execution')
        verify_source_subset(source, commit, proof)
        measured = {name: {key: row[key] for key in ('mode', 'size', 'sha256')} for name, row in source.items()}
        if measured != source_files or fingerprint(measured) != binding.get('sourceSelectionSha256'):
            raise Refused('Image selected source differs from its exact reviewed inventory')
    elif (_git_tree(source) != tree or _git_id('commit', commit).hex() != binding['sourceCommit']
            or not commit.startswith(f'tree {tree}\n'.encode())):
        raise Refused('Image source bytes differ from the pinned Git commit and tree')
    if execution is None and source_files is not None:
        measured = {name: {key: row[key] for key in ('mode', 'size', 'sha256')} for name, row in source.items()}
        if measured != source_files or fingerprint(measured) != binding.get('sourceInventorySha256'):
            raise Refused('Image source differs from its exact complete build inventory')
    inputs = {}
    for name, expected in binding['committedInputs'].items():
        row = source.get(name)
        if row is None or row['sha256'] != expected:
            raise Refused(f'Image source differs from the pinned committed {name}')
        inputs[name] = unique_json(row['data'])
    package, lock = inputs['package.json'], inputs['package-lock.json']
    recipe = {'version': 3 if execution is not None else 2, 'testScript': script}
    if selector is not None:
        if not {selector['file'], PLAYWRIGHT_CONFIG} <= source.keys():
            raise Refused('Image source lacks the selected committed Playwright spec or config')
        recipe['testSelector'] = selector
    inspect_npm(inputs, recipe)
    if 'workspaces' in package or 'workspaces' in lock or 'workspaces' in lock['packages'].get('', {}):
        raise Refused('Workspace dependencies are outside the supported image layout')
    return lock


def _check_store(files, lock, binding):
    index_file = files.get('index.json')
    if index_file is None or index_file['sha256'] != binding['dependencyStoreSha256']:
        raise Refused('Image dependency store index differs from its reviewed pin')
    index = unique_json(index_file['data'])
    source_check = 'executionSha256' in binding
    if isinstance(index, dict) and index.get('kind') == NO_INSTALL_STORE:
        fields(index, 'kind platform lockSha256', 'no-install store index')
        platform = index['platform']
        fields(platform, 'os cpu libc', 'no-install platform')
        if (not source_check or canonical(index).encode() != index_file['data']
                or index['lockSha256'] != binding['committedInputs']['package-lock.json']
                or platform['os'] != 'linux' or platform['cpu'] not in ('arm64', 'x64') or platform['libc'] != 'glibc'
                or set(files) != {'index.json'} or index_file['mode'] != 0o644):
            raise Refused('Image no-install store differs from the reviewed source check')
        count = len(lock['packages']) - 1
        if not 0 <= count <= _MAX_MEMBERS:
            raise Refused('No-install lock metadata exceeds the existing package count bound')
        return {'kind': NO_INSTALL_STORE, 'platform': platform, 'lockPackageCount': count,
                'packageCount': 0, 'tarballCount': 0, 'fullLockVerified': False, 'excludedPackageCount': count}
    if source_check:
        raise Refused('Source check requires an exact no-install dependency store')
    if isinstance(index, dict) and index.get('kind') == PLATFORM_STORE:
        return platform_store(files, lock, index, binding)
    fields(index, 'kind lockSha256 packages', 'dependency store index')
    entries = {name: value for name, value in lock['packages'].items() if name}
    packages = index['packages']
    if (index['kind'] != FULL_STORE or index['lockSha256'] != binding['committedInputs']['package-lock.json']
            or canonical(index).encode() != index_file['data']
            or not isinstance(packages, dict) or set(packages) != set(entries)):
        raise Refused('Image dependency store index does not cover the pinned npm lock')
    expected = {'index.json'}
    for name, entry in entries.items():
        if (not _literal_path(name) or not name.startswith('node_modules/') or entry.get('link')
                or entry.get('inBundle') or not isinstance(entry.get('resolved'), str)
                or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9@%/_.~+-]+\.tgz',
                                    entry['resolved'])):
            raise Refused('Unsupported npm dependency source or layout')
        row = packages[name]
        fields(row, 'integrity sha256', 'dependency tarball entry')
        integrity = entry.get('integrity')
        if (not isinstance(integrity, str) or not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==', integrity)
                or row['integrity'] != integrity or not digest(row['sha256'])):
            raise Refused('Unsupported or mismatched npm tarball integrity')
        path = 'blobs/' + row['sha256'] + '.tgz'
        expected.add(path)
        actual = files.get(path)
        if (actual is None or not actual['size'] or actual['mode'] != 0o644 or actual['sha256'] != row['sha256']
                or 'sha512-' + b64encode(actual['sha512']).decode() != integrity):
            raise Refused('Image dependency tarball differs from the pinned npm lock')
    if set(files) != expected:
        raise Refused('Image dependency store contains unexplained files')
    return {'packageCount': len(entries), 'tarballCount': len(expected) - 1}


def _verify_archives(open_archive, binding, tools, script, paths, entrypoint_pin, selector, browser=None,
                     *, execution=None, source_files=None):
    archive_hashes = {}

    def read(role, maximum, member_limit, keep=(), *, directory=False, browser=False,
             reviewed_files=None, retain_limit=MAX_FILE):
        path = paths[role]
        with open_archive(role, path) as stream:
            files, archive_hashes[role] = _archive(stream, path.rsplit('/', 1)[-1],
                                                  maximum + 2 * 1024 * 1024, member_limit, keep,
                                                  directory=directory, browser=browser, reviewed_files=reviewed_files,
                                                  retain_limit=retain_limit)
        return files

    for role, expected, limit in (('node', tools['node']['sha256'], _MAX_TOOL),
                                  ('npm', tools['npm']['sha256'], MAX_FILE),
                                  ('entrypoint', entrypoint_pin, MAX_FILE)):
        row = next(iter(read(role, limit, limit).values()))
        if (row['sha256'] != expected or not row['size']
                or (role == 'node' and row['mode'] != 0o755)
                or (role == 'entrypoint' and row['mode'] != 0o755
                    and paths['entrypoint'] != PLATFORM_ENTRYPOINT)):
            raise Refused(f'Image {role} bytes or executable mode differ from the reviewed input')
    commit = read('commit', MAX_FILE, MAX_FILE, {'source.commit'})['source.commit']['data']
    if source_files is not None:
        # The frozen plan supplies exact sizes and membership; do not substitute a project-size ceiling.
        directories = {'.'}
        for name in source_files:
            directories.update(str(parent) for parent in PurePosixPath(name).parents)
        source_maximum = sum(row['size'] + -row['size'] % _BLOCK for row in source_files.values())
        source_maximum += (len(source_files) + len(directories)) * _BLOCK
        source = read('source', source_maximum, max(row['size'] for row in source_files.values()),
                      binding['committedInputs'], directory=True, reviewed_files=source_files)
    else:
        source = read('source', _MAX_UNREVIEWED_SOURCE, _MAX_UNREVIEWED_SOURCE,
                      binding['committedInputs'], directory=True)
    proof = None
    if execution is not None:
        proof_bytes = binding.get('sourceProofBytes', MAX_FILE)
        raw = read('proof', proof_bytes, proof_bytes, {'source.proof.json'},
                   retain_limit=proof_bytes)['source.proof.json']['data']
        if 'sourceProofBytes' in binding and len(raw) != proof_bytes:
            raise Refused('Image source proof differs from its reviewed size')
        proof = unique_json(raw)
        if canonical(proof).encode() != raw:
            raise Refused('Image source subset proof is not canonical')
    lock = _check_source(source, commit, binding, script, selector, proof=proof,
                         source_files=source_files, execution=execution)
    store = _check_store(read('store', _MAX_STORE, _MAX_BLOB, {'index.json'}, directory=True), lock, binding)
    if (paths['entrypoint'] == PLATFORM_ENTRYPOINT) != (store.get('kind') in (PLATFORM_STORE, NO_INSTALL_STORE)):
        raise Refused('Image entrypoint and dependency store contract differ')
    measured = {'archiveSha256': archive_hashes, 'sourceFileCount': len(source),
                'tools': tools, 'entrypointSha256': entrypoint_pin, 'dependencyTarballs': store}
    if execution is not None:
        measured.update(sourceFiles=source_files, sourceProofSha256=fingerprint(proof))
    elif source_files is not None:
        measured['sourceInventory'] = source_files
    if selector is not None:
        measured['selectedTestInputs'] = {
            name: source[name]['sha256'] for name in (selector['file'], PLAYWRIGHT_CONFIG)}
    if browser is not None:
        if store.get('platform') != browser['platform']:
            raise Refused('Browser inventory platform differs from the measured npm store')
        expected = browser_runtime_files(browser)
        observed = {}
        for role, name in browser_runtime_paths(browser).items():
            pin = expected[name]
            row = next(iter(read(role, pin['size'], pin['size'], browser=True).values()))
            header = row['header']
            elf_type = int.from_bytes(header[16:18], 'little')
            machine = int.from_bytes(header[18:20], 'little')
            if (len(header) != 20 or not header.startswith(b'\x7fELF\x02\x01\x01')
                    or machine != {'arm64': 183, 'x64': 62}[browser['platform']['cpu']]
                    or elf_type not in ((2, 3) if name == browser['executable']['path'] else (3,))):
                raise Refused(f'Browser inventory file is not a matching ELF64 executable or library: {name}')
            observed[name] = {key: row[key] for key in ('sha256', 'mode', 'size')}
            if observed[name] != pin:
                raise Refused(f'Browser runtime bytes or mode differ from the reviewed input: {name}')
        measured['browserRuntime'] = {'profileSha256': binding['browserRuntimeSha256'], 'files': observed,
                                      'coverage': 'listed-files-only', 'binaryFormat': 'elf64-little-endian-matching-cpu'}
    return measured


def _result(binding, script, measured, provenance):
    return {'version': 1, 'status': 'bytes-match-reviewed-inputs', 'binding': binding, 'testScript': script,
            'measurements': measured, 'provenance': provenance, 'npmRuntimeClosure': 'unqualified',
            'offlineInstallation': 'unqualified', 'runtimeIsolation': 'unqualified',
            'executionAuthorized': False, 'productAccepted': False}


def verify_local_image_archives(plan_path, plan_pin, control_pin, profile_path, profile_pin,
                                entrypoint_pin, archive_directory, *, platform_build=False):
    """Check local tar files. Supplied bytes cannot prove their image of origin."""
    plan, binding, tools, script, paths = _reviewed_inputs(
        plan_path, plan_pin, control_pin, profile_path, profile_pin, entrypoint_pin,
        platform_build=platform_build)

    def open_archive(role, path):
        return os.fdopen(owned_file(archive_directory, role + '.tar', os.O_RDONLY), 'rb')

    measured = _verify_archives(open_archive, binding, tools, script, paths, entrypoint_pin,
                                planned_test_selector(plan), planned_browser_runtime(plan),
                                execution=planned_source_check(plan), source_files=plan.get('sourceFiles', plan.get('sourceInventory')))
    return _result(binding, script, measured, {'imageOrigin': 'unverified', 'kind': 'local-archives'})


class _Staging:
    """Only image inspect, inert create, exact readback and unforced removal."""
    def __init__(self, engine, response, intent):
        self.engine = engine
        self.response = response
        self.image = intent['binding']['imageDigest']
        self.platform = intent['binding'].get('browserRuntimePlatform')
        self.name = 'aim-ci-readback-' + fingerprint(intent)[:24]
        self.labels = {'org.aim-ci.acquisition': intent['acquisitionId'],
                       'org.aim-ci.readback-intent': fingerprint(intent)}
        self.image_labels = docker_engine.recipe_image_labels(
            intent['binding'], intent['binding']['dependencyStoreSha256'])
        self.config = {'Image': self.image, 'Entrypoint': ['/bin/false'], 'Cmd': ['--aim-ci-readback-only'],
                       'User': '65534:65534', 'WorkingDir': '/', 'NetworkDisabled': True}
        self.host = {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False, 'AutoRemove': False,
                     'RestartPolicy': {'Name': 'no'}, 'CapDrop': ['ALL'],
                     'SecurityOpt': ['no-new-privileges']}

    def create(self):
        image = self.engine.check_image(self.image, self.image_labels, platform=self.platform)
        value = self.engine._call('POST', '/containers/create?' + urlencode({'name': self.name}), (201,),
                                  {**self.config, 'Labels': self.labels, 'HostConfig': self.host})
        return {'containerId': docker_engine._container_id(value.get('Id')), 'imageInspect': image}

    def inspect(self, identifier=None):
        selector = self.name if identifier is None else docker_engine._container_id(identifier)
        value = self.engine._call('GET', f'/containers/{selector}/json', (200, 404))
        if value is None:
            return None
        observed = docker_engine._container_id(value.get('Id'))
        config, host, state = (value.get(key) for key in ('Config', 'HostConfig', 'State'))
        if (identifier is not None and observed != identifier or value.get('Name') != '/' + self.name
                or value.get('Image') != self.image or value.get('Mounts') != []
                or not all(isinstance(row, dict) for row in (config, host, state))):
            raise Refused('Docker readback container identity, ownership or configuration differs')
        labels = config.get('Labels')
        restart = host.get('RestartPolicy')
        if (not isinstance(labels, dict) or any(labels.get(k) != v for k, v in self.labels.items())
                or any(type(config.get(key)) is not type(item) or config[key] != item
                       for key, item in self.config.items())
                or config.get('Volumes') or any(host.get(key) for key in
                   ('Binds', 'Mounts', 'VolumesFrom', 'Tmpfs', 'Devices', 'DeviceRequests'))
                or not isinstance(restart, dict) or restart.get('Name') != 'no'
                or any(type(host.get(key)) is not type(item) or host[key] != item
                       for key, item in self.host.items() if key != 'RestartPolicy')):
            raise Refused('Docker readback container identity, ownership or configuration differs')
        if self.platform is not None and any(host.get(key) for key in
                ('PortBindings', 'PublishAllPorts', 'Dns', 'DnsOptions', 'DnsSearch', 'ExtraHosts', 'Links')):
            raise Refused('Docker browser readback has unexpected host connectivity settings')
        if (state.get('Status') != 'created'
                or any(state.get(key) is not False for key in ('Running', 'Paused', 'Restarting', 'Dead', 'OOMKilled'))
                or type(state.get('Pid')) is not int or state['Pid'] != 0
                or any(state.get(key) != _ZERO_TIME for key in ('StartedAt', 'FinishedAt'))):
            raise Refused('Docker readback container is not confirmed never started')
        created = value.get('Created')
        if not isinstance(created, str) or docker_engine._timestamp(created).year == 1:
            raise Refused('Docker readback container creation time is missing')
        return {'containerId': observed, 'imageId': self.image, 'name': self.name, 'createdAt': created,
                'startedAt': state['StartedAt'], 'finishedAt': state['FinishedAt'],
                'neverStarted': True, 'mounts': []}

    def reconcile_create(self):
        observed = self.inspect()
        if observed is None:
            return None
        image = self.engine.check_image(self.image, self.image_labels, platform=self.platform)
        return {'containerId': observed['containerId'], 'imageInspect': image}

    @contextmanager
    def archive(self, identifier, path):
        route = f'/containers/{identifier}/archive?' + urlencode({'path': path})
        with self.response(self.engine.endpoint, 'GET', docker_engine._API + route, None, 10) as response:
            if (response.status != 200 or response.getheader('Content-Type') != 'application/x-tar'
                    or response.getheader('Content-Encoding') not in (None, 'identity')):
                raise Refused('Docker did not return an uncompressed readback tar')
            yield response

    def absent(self, identifier):
        if self.inspect(identifier) is None:
            return {'containerId': identifier, 'cleanup': 'confirmed-absent'}
        return None

    def remove(self, identifier):
        if not self.absent(identifier):
            self.engine.remove(identifier)
        result = self.absent(identifier)
        if result is None:
            raise Refused('Docker readback removal has no confirmed exact-ID absence')
        return result


def _effect(journal, name, intent, action, lookup=None):
    def receipt(key, observed):
        return journal.receipt(key, fingerprint(intent), observed)

    def reconcile(key, _intent):
        observed = lookup()
        if observed is None:
            return {'state': 'unknown'}
        return {'state': 'completed', 'receipt': receipt(key, observed)}

    return journal.perform('image-readback:' + name, intent, lambda key, _: receipt(key, action()),
                           lookup=reconcile if lookup else None)


def _acquire(plan_inputs, entrypoint_pin, policy_pin, acquisition_id, journal_path, engine, response, *, synthetic):
    plan, binding, tools, script, paths = plan_inputs
    intent = {'acquisitionId': acquisition_id, 'binding': binding, 'policySha256': policy_pin,
              'endpoint': engine.endpoint, 'readPaths': paths, 'entrypointSha256': entrypoint_pin}
    staging = _Staging(engine, response, intent)
    journal = EffectJournal(journal_path, {'scope': 'never-started-image-readback', **intent}, synthetic=synthetic)
    with os.fdopen(owned_file(journal_path, 'acquisition.lock', os.O_RDWR | os.O_CREAT), 'r+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Docker readback already has an active controller') from None
        created = _effect(journal, 'create', intent, staging.create, staging.reconcile_create)
        identifier = docker_engine._container_id(created.get('containerId'))
        exact = {**intent, 'containerId': identifier}

        def measure():
            before = staging.inspect(identifier)
            if before is None:
                raise Refused('Docker readback container disappeared before acquisition')
            measurements = _verify_archives(lambda _, path: staging.archive(identifier, path), binding,
                                            tools, script, paths, entrypoint_pin,
                                            planned_test_selector(plan), planned_browser_runtime(plan),
                                            execution=planned_source_check(plan), source_files=plan.get('sourceFiles', plan.get('sourceInventory')))
            after = staging.inspect(identifier)
            if before != after:
                raise Refused('Docker readback container changed during acquisition')
            return {'before': before, 'after': after, 'measurements': measurements}

        try:
            measured = _effect(journal, 'copy-and-verify', exact, measure)
        finally:
            cleanup = _effect(journal, 'remove', exact, lambda: staging.remove(identifier),
                              lambda: staging.absent(identifier))
    provenance = {'kind': 'synthetic-protocol' if synthetic else 'trusted-local-daemon',
                  'imageOrigin': 'unverified' if synthetic else 'verified-by-trusted-readback',
                  'acquisitionId': acquisition_id, 'policySha256': policy_pin, 'endpoint': engine.endpoint,
                  'readPaths': paths, 'imageInspect': created['imageInspect'],
                  'before': measured['before'], 'after': measured['after'], **cleanup}
    return _result(binding, script, measured['measurements'], provenance)


def acquire_image_evidence(plan_path, plan_pin, control_pin, profile_path, profile_pin, entrypoint_pin,
                           *, trusted_docker_policy, trusted_docker_policy_pin, acquisition_id, journal_path,
                           platform_build=False, before_effect=None):
    """Internal controller action. The caller must own the daemon and the authorized journal location."""
    from stat import S_IMODE
    from .owned_paths import owned_directory, owned_mkdir

    root = Path(__file__).resolve().parents[2]
    manifest = verify_installation(root, control_pin)
    if 'lib/aim_ci/docker_image_evidence.py' not in manifest['files'] or not digest(acquisition_id):
        raise Refused('Docker readback requires a pinned installed verifier and controller acquisition ID')
    if platform_build:
        if manifest['files'].get(HELPER) != entrypoint_pin:
            raise Refused('Platform build requires the installed, pinned npm helper bytes')
    inputs = _reviewed_inputs(plan_path, plan_pin, control_pin, profile_path, profile_pin, entrypoint_pin,
                              platform_build=platform_build)
    plan, binding, _, _, _ = inputs
    prepared = _prepare_docker_recipe(plan, binding, trusted_docker_policy, trusted_docker_policy_pin, None)
    if (prepared['intent'] is None or file_sha(Path(docker_engine.__file__)) != trusted_docker_policy['adapterSha256']):
        raise Refused('Docker readback requires the reviewed adapter, image and runtime')
    journal_path = Path(journal_path)
    checkout = plan['target'].get('checkout')
    if not isinstance(checkout, str) or not Path(checkout).is_absolute():
        raise Refused('Docker readback requires the canonical planned checkout identity')
    checkout_path = Path(checkout)
    if checkout_path.resolve() != checkout_path or str(checkout_path) != checkout:
        raise Refused('Docker readback requires the canonical planned checkout identity')
    if (not journal_path.is_absolute() or journal_path.resolve() != journal_path
            or any(journal_path.is_relative_to(path) or path.is_relative_to(journal_path)
                   for path in (root, checkout_path))):
        raise Refused('Docker readback requires a separate canonical journal path')
    owned_mkdir(journal_path.parent, journal_path.name)
    descriptor = owned_directory(journal_path)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or S_IMODE(info.st_mode) & 0o077:
            raise Refused('Docker readback journal must be private to the trusted controller')
    finally:
        os.close(descriptor)
    request = docker_engine.unix_request if before_effect is None else docker_engine.guarded_request(before_effect)
    engine = docker_engine.DockerEngine(trusted_docker_policy['endpoint'], request)
    return _acquire(inputs, entrypoint_pin, trusted_docker_policy_pin, acquisition_id, journal_path,
                    engine, docker_engine.unix_response, synthetic=False)



def verify_readback_receipts(journal_path, request, image, script):
    """Verify retained controller receipts without advancing any image effect."""
    journal = EffectJournal(journal_path, {'scope': 'never-started-image-readback', **request}, read_only=True)
    created = journal.completed_result('image-readback:create', request)
    exact = {**request, 'containerId': docker_engine._container_id(created.get('containerId'))}
    measured = journal.completed_result('image-readback:copy-and-verify', exact)
    removed = journal.completed_result('image-readback:remove', exact)
    provenance = image['provenance']
    if (image['testScript'] != script or provenance.get('acquisitionId') != request['acquisitionId']
            or provenance.get('imageInspect') != created['imageInspect'] or provenance.get('readPaths') != request['readPaths']
            or image['measurements'] != measured['measurements']
            or any(provenance.get(key) != measured[key] for key in ('before', 'after'))
            or removed != {'containerId': exact['containerId'], 'cleanup': 'confirmed-absent'}
            or any(provenance.get(key) != value for key, value in removed.items())):
        raise Refused('Image readback differs from its completed effect receipts')


def recover_image_readback(journal_path, intent):
    """Reconcile the exact never-started readback container from its original intent."""
    journal = EffectJournal(journal_path, {'scope': 'never-started-image-readback', **intent}, synthetic=False)
    if journal.inspect('image-readback:create') is None:
        return {'cleanup': 'not-needed'}
    engine = docker_engine.DockerEngine(intent['endpoint'], docker_engine.unix_request)
    staging = _Staging(engine, docker_engine.unix_response, intent)

    def forbidden():
        raise Refused('Recovery cannot create an image-readback container')

    created = _effect(journal, 'create', intent, forbidden, staging.reconcile_create)
    identifier = docker_engine._container_id(created['containerId'])
    return _effect(journal, 'remove', {**intent, 'containerId': identifier}, lambda: staging.remove(identifier),
                   lambda: staging.absent(identifier))
