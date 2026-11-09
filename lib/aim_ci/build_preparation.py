"""Prepare the exact source, platform store, image and plan for a reviewed build."""
from datetime import datetime, timezone
from hashlib import sha256, sha512
from base64 import b64encode
import io
import os
from pathlib import Path, PurePosixPath
import re
from stat import S_IMODE
import tarfile
from types import SimpleNamespace

from openai_budget import Refused, canonical, fingerprint
from . import docker_engine
from .control_package import MAX_FILE, verify_installation
from .docker_image_evidence import PLATFORM_ENTRYPOINT, _LAYOUT, _MAX_BLOB, _MAX_MEMBERS, _MAX_SOURCE, _MAX_STORE, _git_id, _git_tree
from .image_preparation import ImagePreparation, measure_file, _MAX_ARCHIVE
from .installed_runtime import file_sha
from .npm_platform import PLATFORM_STORE, platform_partition, project_path
from .operator_build import ActiveController, canonical_path, operator_lock, overlaps, owned_input, validate_directory, write_state
from .owned_paths import new_owned_directory, owned_directory, owned_file, read_owned
from .platform_build import HELPER, report_json
from .project_adaptation import propose_adaptation
from .project_discovery import CommitReader, LOCKS, SourceBindingMismatch, unique_json
from .recipe_plan import (BRANCH_NAME, INPUT_FILES, PLAYWRIGHT_CONFIG, REPOSITORY_URL, container_runtime_sha, digest, fields, image_digest,
                          inspect_npm, inspect_target, plan_recipe,
                          validate_assignment, validate_browser_runtime, validate_container_tools, validate_recipe, verify_tools)

_AUTH = 'preparation-authorization.json'
_ARCHIVE = 'image-input.tar'
_ARCHIVE_PIN = 'image-input.json'
_WITHDRAWN = 'preparation-withdrawn.json'
_BLOB_NAME = re.compile(r'[A-Za-z0-9@_.+-]{1,128}\.tgz')
_CONFIG_FIELDS = 'version manifestSha256 checkout target git base docker platform tarballsDir recipe oracle assignment outputDir'
PREPARATION_RECIPE_FIELDS = 'id stack testScript allowedPaths forbiddenPaths artifact limits'
NO_INSTALL_STORE = 'npm-no-install-v1'
_LIMITATIONS = {
    'authority': 'local-os-principal-only',
    'scope': 'prepare-only-no-project-command-or-build-authorization',
    'imageVerification': 'daemon-byte-readback-required-by-separately-approved-build',
    'storeCoverage': 'applicable-platform-only',
    'network': 'no-downloads-or-builder',
}


def source_inventory(reader):
    source, total = {}, 0
    directories = set()
    for name, mode, kind, oid in reader.entries(reader.commit, recursive=True, maximum=MAX_FILE):
        if (kind != 'blob' or mode not in ('100644', '100755') or not project_path('source/' + name)
                or not re.fullmatch('[a-f0-9]{40}', oid) or name in source):
            raise Refused('Unsupported committed source member in build preparation')
        path = PurePosixPath(name)
        try:
            for relative in (path, *path.parents):
                tarfile.TarInfo(_LAYOUT[1:] + '/source/' + str(relative)).tobuf(format=tarfile.USTAR_FORMAT)
        except ValueError:
            raise Refused('Committed source path exceeds the supported tar layout') from None
        directories.update(str(parent) for parent in path.parents)
        if len(source) + len(directories) >= _MAX_MEMBERS:
            raise Refused('Committed source member count exceeds the readback bound')
        if any(part in ('.git', 'node_modules', '.npmrc') or re.match(r'^\.env(?:\.|$)', part)
               for part in path.parts):
            raise Refused('Committed npm configuration, dependency tree or environment file is unsupported')
        size = reader.size(oid)
        if not 0 <= size <= _MAX_SOURCE - total or name in ('package.json', 'package-lock.json') and size > MAX_FILE:
            raise Refused('Committed source exceeds its reviewed byte bound')
        data = reader.blob(oid, maximum=size)
        blob = _git_id('blob', data)
        if len(data) != size or blob.hex() != oid:
            raise Refused('Committed Git blob changed during preparation')
        source[name] = {'mode': int(mode, 8) & 0o777, 'size': size, 'sha256': sha256(data).hexdigest(),
                        'blob': blob, 'data': data}
        total += size
    commit = reader.read('cat-file', 'commit', reader.commit, maximum=MAX_FILE)
    if (_git_tree(source) != reader.tree or _git_id('commit', commit).hex() != reader.commit
            or not commit.startswith(f'tree {reader.tree}\n'.encode())):
        raise Refused('Committed source does not reconstruct the exact Git tree and commit')
    return source, commit


def source_manifest(source):
    return {name: {key: row[key] for key in ('mode', 'size', 'sha256')} for name, row in source.items()}


def source_binding(reader, manifest):
    return {'tree': reader.tree, 'sourceManifestSha256': fingerprint(manifest),
            'committedInputs': {name: manifest[name]['sha256'] for name in INPUT_FILES}}


class UnsupportedPreparationScope(Refused):
    def __init__(self, message, sources):
        super().__init__(message)
        self.sources = sources


def check_npm_scope(files):
    if (not set(INPUT_FILES) <= files.keys() or set(files).intersection(LOCKS) != {'package-lock.json'}
            or 'pyproject.toml' in files):
        observed = sorted(set(files).intersection({*LOCKS, 'package.json', 'pyproject.toml'}))
        raise UnsupportedPreparationScope(
            'Preparation requires one root npm v3 lock and an unambiguous npm/Vite scope. Review discovery first.',
            [{'path': name, 'sha256': files[name]['sha256']} for name in observed if 'sha256' in files[name]])


def preparation_npm(inputs, recipe, *, allow_unselected_test=False):
    metadata = inspect_npm(inputs, recipe, allow_unselected_test=allow_unselected_test)
    return metadata, npm_manager(inputs)


def npm_manager(inputs):
    package, lock = (inputs[name] for name in INPUT_FILES)
    manager = package.get('packageManager')
    if manager is not None and (not isinstance(manager, str) or not manager.startswith('npm@')):
        raise Refused('Committed packageManager is not npm. Review the stack through project discovery.')
    if any('workspaces' in data for data in (package, lock, lock['packages'].get('', {}))):
        raise Refused('Workspace dependencies require a separately supported recipe')
    return manager


def checkout_labels(reader):
    origin = reader.read('config', '--local', '--no-includes', '--null', '--get-all', 'remote.origin.url',
                         missing_ok=True)
    branch = reader.read('symbolic-ref', '--quiet', '--short', 'HEAD', missing_ok=True)
    origin = (origin or b'').decode('utf-8', errors='replace').removesuffix('\0')
    branch = (branch or b'').decode('utf-8', errors='replace').removesuffix('\n')
    return {'repository': origin if re.fullmatch(REPOSITORY_URL, origin) else None,
            'branch': branch if re.fullmatch(BRANCH_NAME, branch) else None}


def draft_build_preparation(root, args, control):
    checkout = canonical_path(str(args.checkout), 'Checkout')
    git = canonical_path(str(args.git), 'Git executable')
    if any(git.is_relative_to(path) for path in (checkout, root)):
        raise Refused('Git executable must be outside the checkout and installation')
    reader = CommitReader(root, checkout, git, args.git_sha256, args.commit)
    commit = reader.checked_object('commit', reader.commit, MAX_FILE)
    reader.checked_object('tree', reader.tree, MAX_FILE)
    if not commit.startswith(f'tree {reader.tree}\n'.encode()):
        raise Refused('Committed Git tree differs from the selected commit')
    labels = checkout_labels(reader)
    adaptation = propose_adaptation(root, args, control, reader=reader)
    from .project_installation import verify_project_plan

    installation = verify_project_plan(root, args, reader, adaptation=adaptation)
    observations = adaptation['observations']
    files = {row['path']: row for row in observations['files']}
    script = args.test_script
    source_check = getattr(args, 'execution_kind', None) == 'npm-source-check'
    if script is not None and not any(f['code'] == 'node-named-script' and f['value'] == script
                                      for f in observations['facts']):
        raise Refused('Select a literal test script present in the committed package.json')

    inputs = {name: unique_json(reader.checked_object('blob', files[name]['gitObject']))
              for name in INPUT_FILES if name in files and files[name]['status'] == 'parsed'}
    result = {'version': 1, 'status': 'preparation-configuration-draft', 'executionAuthorized': False,
              'checkoutFacts': {'values': labels, 'sources': {'repository': 'local Git remote.origin.url',
                                'branch': 'local Git symbolic HEAD'}, 'identityVerified': False},
              'adaptation': adaptation, 'configurationDraft': None, 'operatorChoices': []}
    if installation is not None:
        result['projectInstallation'] = installation
        result['reviewAuthority'] = 'installation-receipt-is-review-draft-only-configuration-requires-independent-operator-review'
    choices = result['operatorChoices']
    if labels['repository'] is None:
        choices.append('Use a checkout with one credential-free HTTPS origin supported by preparation.')
    if labels['branch'] is None:
        choices.append('Use an attached checkout on a branch name supported by preparation.')
    try:
        if not source_check:
            check_npm_scope(files)
        if set(inputs) != set(INPUT_FILES):
            raise Refused('Review the source-linked npm package and lock parsing questions.')
        if source_check:
            from .source_subset import selected_paths, source_subset

            paths = selected_paths(getattr(args, 'source_path', []))
            if not set(INPUT_FILES) <= set(paths) or any('dist' in PurePosixPath(path).parts for path in paths):
                raise Refused('Source check draft requires explicit package.json, package-lock.json and reviewed source paths without build outputs')
            metadata, _ = preparation_npm(inputs, {'version': 3, 'testScript': script})
            source, _, proof = source_subset(reader, paths)
        else:
            metadata, _ = preparation_npm(inputs, {'version': 2, 'testScript': script}, allow_unselected_test=True)
    except Refused as error:
        result['status'] = 'preparation-draft-needs-investigation'
        choices.append(str(error))
    else:
        if not source_check:
            source, _ = source_inventory(reader)
        if any(row.get('sha256') and (path not in source or source[path]['sha256'] != row['sha256'])
               and (not source_check or path in paths)
               for path, row in files.items()):
            raise Refused('Discovered definitions differ from the pinned preparation source')
        bound = source_binding(reader, source_manifest(source))
        if source_check:
            bound.pop('sourceManifestSha256')
            bound.update(sourceSelectionSha256=fingerprint(source_manifest(source)), sourceProofSha256=fingerprint(proof))
            result['recipeSuggestion'] = {'stack': 'npm-source-check', 'status': 'review-required',
                'dependencies': 'none', 'outputs': 'none', 'sourcePaths': paths, 'sources': [
                    {'path': 'package.json', 'locator': 'scripts[' + repr(script) + ']'},
                    {'path': 'package-lock.json', 'locator': 'lockfileVersion'}]}
        else:
            result['recipeSuggestion'] = {'stack': 'npm-vite-static', 'status': 'review-required',
                'viteRelation': metadata['viteRelation'], 'sources': [
                    {'path': 'package.json', 'locator': 'devDependencies.vite'},
                    {'path': 'package-lock.json', 'locator': 'packages["node_modules/vite"]'}]}
        result['declaredScripts'] = {'values': metadata['scripts'], 'behaviorVerified': False, 'source': {
            'path': 'package.json', 'locator': 'scripts', 'sha256': bound['committedInputs']['package.json']}}
        result['configurationDraft'] = {'version': 2, 'manifestSha256': args.manifest_sha256,
            'checkout': str(reader.checkout), 'target': {**labels, 'commit': reader.commit},
            'git': observations['git'], 'source': bound, 'base': None, 'docker': None, 'platform': None,
            'tarballsDir': None, 'outputDir': None,
            'recipe': {'id': None, 'stack': None, 'testScript': script, 'allowedPaths': None,
                       'forbiddenPaths': None, 'artifact': None, 'limits': None},
            'oracle': None, 'assignment': None}
        if source_check:
            result['configurationDraft']['recipe'].update(stack='npm-source-check', sourcePaths=paths,
                dependencies={'mode': 'none'}, artifact={'kind': 'none'})
            result['configurationDraft']['oracle'] = {'kind': 'source-check-exit-v1', 'script': script}
        choices.extend(['Review the stack, choose a recipe ID and select the allowed and forbidden paths.',
                        'Select a literal test script and review the committed script bodies.',
                        'Supply the artifact policy, resource limits, oracle and assignment.',
                        'Supply the reviewed base image, in-image tool pins, Docker CLI and endpoint, and target platform.',
                        'Supply a new preparation output path and keep tarballsDir unset for source checks.' if source_check
                        else 'Supply the local tarball directory and a new preparation output path.'])
    reader.verify_head()
    if checkout_labels(reader) != labels:
        raise Refused('Checkout branch or origin changed during preparation drafting')
    return result


def preparation_configuration_fields(config):
    version = config.get('version') if isinstance(config, dict) else None
    if type(version) is not int or version not in (1, 2):
        raise Refused('Expected a reviewed preparation configuration, not a draft report')
    names = _CONFIG_FIELDS
    if (isinstance(config.get('recipe'), dict) and config['recipe'].get('stack') == 'npm-source-check'
            and 'tarballsDir' not in config):
        names = ' '.join(name for name in names.split() if name != 'tarballsDir')
    return names + (' source' if version == 2 else '')


def check_configuration(config):
    fields(config, preparation_configuration_fields(config), 'build preparation configuration')
    version = config['version']
    source_check = isinstance(config.get('recipe'), dict) and config['recipe'].get('stack') == 'npm-source-check'
    if version == 2:
        pending = [config]
        while pending:
            value = pending.pop()
            if value is None:
                raise Refused('Incomplete preparation configuration draft. Review and fill every unset value first.')
            if isinstance(value, dict):
                pending.extend(item for key, item in value.items()
                               if not (value is config and key == 'tarballsDir'
                                       and source_check
                                       and item is None))
            elif isinstance(value, list):
                pending.extend(value)
        names = ('tree sourceSelectionSha256 sourceProofSha256 committedInputs'
                 if source_check
                 else 'tree sourceManifestSha256 committedInputs')
        fields(config['source'], names, 'preparation source binding')


def dependency_inventory(directory, lock, lock_pin, platform):
    entries = {name: row for name, row in lock['packages'].items() if name}
    included, excluded = platform_partition(entries, platform, root=lock['packages'].get(''))
    needed = {entries[name]['integrity'] for name in included}
    descriptor = owned_directory(directory)
    try:
        names = os.listdir(descriptor)
        if len(names) > _MAX_MEMBERS:
            raise Refused('Local tarball collection exceeds the preparation member bound')
        blobs, ignored, total = {}, [], 0
        for name in sorted(names):
            if not _BLOB_NAME.fullmatch(name):
                ignored.append({'name': name, 'reason': 'not-a-supported-tarball-name'})
                continue
            with os.fdopen(owned_file(descriptor, name, os.O_RDONLY), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != os.geteuid() or S_IMODE(info.st_mode) & 0o022:
                    raise Refused('Local tarball must be owned and protected from other writers')
                measured = measure_file(stream, min(_MAX_BLOB, _MAX_STORE - total), with_sri=True)
            integrity = measured['integrity']
            total += measured['size']
            if not measured['size'] or integrity not in needed:
                ignored.append({'name': name, 'reason': 'outside-applicable-lock-integrities'})
                continue
            if integrity in blobs:
                ignored.append({'name': name, 'reason': 'duplicate-applicable-integrity'})
                continue
            blobs[integrity] = {'name': name, **measured}
    finally:
        os.close(descriptor)
    missing = [{'locator': name, 'integrity': entries[name]['integrity'], 'resolved': entries[name]['resolved']}
               for name in sorted(included) if entries[name]['integrity'] not in blobs]
    if missing:
        return None, blobs, ignored, missing
    index = {'kind': PLATFORM_STORE, 'lockSha256': lock_pin, 'platform': platform,
             'included': {name: {'integrity': entries[name]['integrity'],
                                'sha256': blobs[entries[name]['integrity']]['sha256']} for name in included},
             'excluded': excluded}
    if len(canonical(index).encode()) > MAX_FILE or not blobs or len(blobs) + 3 > _MAX_MEMBERS:
        raise Refused('Unsupported or oversized platform dependency index')
    return index, blobs, ignored, []


def member(archive, name, data=None, *, mode=0o644):
    entry = tarfile.TarInfo(name)
    if data is None:
        entry.type, entry.mode = tarfile.DIRTYPE, 0o755
        archive.addfile(entry)
    else:
        entry.mode, entry.size = mode, len(data)
        archive.addfile(entry, io.BytesIO(data))


class PreparationInputs:
    def __init__(self, root, args, *, acquisition=False, allow_unset_tarballs=False):
        self.root, self.args = root, args
        self.config_path = canonical_path(str(args.config), 'Preparation configuration')
        config = report_json(owned_input(self.config_path, args.config_sha256, 'Preparation configuration'))
        if acquisition:
            names = preparation_configuration_fields(config)
            if not allow_unset_tarballs:
                fields(config, names, 'npm acquisition configuration')
            elif set(config).difference(names.split()):
                raise Refused('Invalid preparation source configuration fields')
        else:
            check_configuration(config)
        if config['manifestSha256'] != args.manifest_sha256:
            raise Refused('Preparation configuration does not match the installed manifest')
        self.config = config
        self.source_check = not acquisition and isinstance(config.get('recipe'), dict) and config['recipe'].get('stack') == 'npm-source-check'
        if acquisition and isinstance(config.get('recipe'), dict) and config['recipe'].get('stack') == 'npm-source-check':
            raise Refused('Source checks do not acquire or install dependencies')
        self.control = verify_installation(root, args.manifest_sha256)
        self.output = None
        if not acquisition or config.get('outputDir') is not None:
            self.output = canonical_path(config['outputDir'], 'Preparation output')
        if not acquisition:
            validate_directory(self.output.parent, private=False)
        self.tarballs = None
        if self.source_check:
            if config.get('tarballsDir') is not None:
                raise Refused('Source check preparation requires no local tarball directory')
        elif not acquisition or not allow_unset_tarballs or config.get('tarballsDir') is not None:
            self.tarballs = canonical_path(config['tarballsDir'], 'Local tarball directory')
        self.checkout = canonical_path(config['checkout'], 'Checkout')
        boundaries = tuple(path for path in (root, self.checkout, self.tarballs, self.output) if path is not None)
        if any(overlaps(left, right) for i, left in enumerate(boundaries) for right in boundaries[i + 1:]) or any(
                self.config_path.is_relative_to(path) for path in boundaries):
            raise Refused('Preparation configuration, output, tarballs, controller and checkout must be separate')
        fields(config['target'], 'repository branch commit', 'preparation target')
        if not isinstance(config['target']['commit'], str) or not re.fullmatch('[a-f0-9]{40}', config['target']['commit']):
            raise Refused('Preparation requires an exact committed Git source SHA')
        fields(config['git'], 'path sha256', 'reviewed Git tool')
        self.git = canonical_path(config['git']['path'], 'Git executable')
        if any(self.git.is_relative_to(path) for path in boundaries):
            raise Refused('Reviewed Git must be outside preparation and project inputs')
        if (os.getuid(), os.getgid()) != (os.geteuid(), os.getegid()):
            raise Refused('Preparation requires the invoking local OS principal')
        allowed = []
        if not acquisition:
            recipe = config['recipe']
            names = PREPARATION_RECIPE_FIELDS
            if self.source_check:
                names += ' sourcePaths dependencies'
            if isinstance(recipe, dict) and 'testSelector' in recipe:
                names += ' testSelector'
            fields(recipe, names, 'recipe selection')
            if recipe['stack'] not in ('npm-vite-static', 'npm-source-check'):
                raise UnsupportedPreparationScope(
                    'Preparation supports npm/Vite static builds. Use discover-project and propose-adaptation for this stack.', [])
            if not isinstance(recipe['id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', recipe['id']):
                raise Refused('Invalid reviewed recipe identifier')
            self.recipe = {'version': 3 if self.source_check else 2,
                           **{key: value for key, value in recipe.items() if key != 'id'}}
            allowed = self.recipe['allowedPaths']
            validate_recipe(self.recipe, config['oracle'])
            validate_assignment(config['assignment'], allowed)
            if self.source_check:
                allowed = [*allowed, *self.recipe['sourcePaths']]
            if 'testSelector' in self.recipe:
                allowed = [*allowed, self.recipe['testSelector']['file'], PLAYWRIGHT_CONFIG]
        self.reader = CommitReader(root, self.checkout, self.git, config['git']['sha256'], config['target']['commit'])
        self.proof = None
        if self.source_check:
            from .source_subset import source_subset
            self.source, self.commit, self.proof = source_subset(self.reader, self.recipe['sourcePaths'])
            for name in self.source:
                try:
                    for relative in (PurePosixPath(name), *PurePosixPath(name).parents):
                        tarfile.TarInfo(_LAYOUT[1:] + '/source/' + str(relative)).tobuf(format=tarfile.USTAR_FORMAT)
                except ValueError:
                    raise Refused('Selected source path exceeds the supported tar layout') from None
        else:
            self.source, self.commit = source_inventory(self.reader)
            check_npm_scope(self.source)
        self.source_manifest = source_manifest(self.source)
        bound = self.source_binding = source_binding(self.reader, self.source_manifest)
        if self.source_check:
            bound.pop('sourceManifestSha256')
            bound.update(sourceSelectionSha256=fingerprint(self.source_manifest), sourceProofSha256=fingerprint(self.proof))
        if config['version'] == 2 and config['source'] != bound:
            raise SourceBindingMismatch('Preparation source binding differs from the committed tree and inputs. Draft again from the current commit.')
        self.committed = bound['committedInputs']
        checkout, tree, npm = inspect_target(self.checkout, config['target'], self.git, self.committed, allowed)
        if tree != bound['tree']:
            raise SourceBindingMismatch('Preparation target tree changed while reading committed inputs')
        if acquisition:
            npm = {name: report_json(self.source[name]['data']) for name in INPUT_FILES}
            package, lock = (npm[name] for name in INPUT_FILES)
            if (not isinstance(package, dict) or not isinstance(lock, dict) or type(lock.get('lockfileVersion')) is not int
                    or lock['lockfileVersion'] != 3 or not isinstance(lock.get('packages'), dict)
                    or not isinstance(lock['packages'].get(''), dict)):
                raise UnsupportedPreparationScope('npm acquisition requires committed package.json and an npm v3 lock with root metadata',
                    [{'path': name, 'sha256': self.committed[name]} for name in INPUT_FILES])
            root_entry = lock['packages']['']
            for key in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'peerDependenciesMeta'):
                if root_entry.get(key, {}) != package.get(key, {}):
                    raise Refused('Committed npm root dependency declarations and lock metadata disagree')
            npm_manager(npm)
        else:
            self.npm_metadata, self.manager = preparation_npm(npm, self.recipe)
        self.npm = npm
        self.lock = npm['package-lock.json']
        self.target = {**config['target'], 'checkout': str(checkout), 'tree': tree}

    def verify_inputs(self):
        owned_input(self.config_path, self.args.config_sha256, 'Preparation configuration')
        verify_installation(self.root, self.args.manifest_sha256)
        verify_tools({'git': self.config['git']}, 'git')
        self.reader.verify_head()
        if checkout_labels(self.reader) != {key: self.target[key] for key in ('repository', 'branch')}:
            raise Refused('Checkout origin or branch changed after review')


class BuildPreparation(PreparationInputs):
    def __init__(self, root, args):
        super().__init__(root, args)
        config = self.config
        docker = config['docker']
        fields(docker, 'path sha256 endpoint', 'reviewed Docker preparation tool')
        self.docker = canonical_path(docker['path'], 'Docker executable')
        if any(self.docker.is_relative_to(path) for path in (root, self.checkout, self.tarballs, self.output) if path is not None):
            raise Refused('Reviewed Docker tool must be outside preparation and project inputs')
        verify_tools({'docker': {key: docker[key] for key in ('path', 'sha256')}}, 'docker')
        endpoint = docker['endpoint']
        if not isinstance(endpoint, str) or not re.fullmatch(r'unix:///[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.sock',
                                                             endpoint) or any(
                part in ('.', '..') for part in endpoint.removeprefix('unix://').split('/')):
            raise Refused('Preparation requires a literal reviewed local Docker socket')
        base = config['base']
        names = 'imageDigest tools'
        if isinstance(base, dict) and 'browserRuntime' in base:
            names += ' browserRuntime'
            browser = validate_browser_runtime(base['browserRuntime'])
            if 'testSelector' not in self.recipe or browser['platform'] != config['platform']:
                raise Refused('Prepared browser runtime requires the selected Playwright test and matching platform')
        fields(base, names, 'reviewed base image')
        if not image_digest(base['imageDigest']):
            raise Refused('Preparation requires a full preloaded base image ID')
        validate_container_tools(base['tools'])
        if self.source_check:
            fields(config['platform'], 'os cpu libc', 'source-check platform')
            if (config['platform']['os'] != 'linux' or config['platform']['cpu'] not in ('arm64', 'x64')
                    or config['platform']['libc'] != 'glibc'):
                raise Refused('Unsupported source-check platform')
            if len(self.lock['packages']) - 1 > _MAX_MEMBERS:
                raise Refused('No-install lock metadata exceeds the existing package count bound')
            self.index = {'kind': NO_INSTALL_STORE, 'platform': config['platform'],
                          'lockSha256': self.committed['package-lock.json']}
            self.blobs, self.ignored, self.missing = {}, [], []
        else:
            self.index, self.blobs, self.ignored, self.missing = dependency_inventory(
                self.tarballs, self.lock, self.committed['package-lock.json'], config['platform'])
        if self.missing:
            self.intent = None
            return
        self.index_pin = fingerprint(self.index)
        self.helper = read_owned(root, HELPER, MAX_FILE)
        self.intent = {
            'version': 1, 'effect': 'prepare-one-reviewed-local-build-image',
            'invocation': {'uid': os.getuid(), 'gid': os.getgid(), 'controller': str(root),
                           'config': str(self.config_path), 'configSha256': args.config_sha256, 'output': str(self.output)},
            'manifestSha256': args.manifest_sha256, 'target': self.target,
            'sourceFiles': len(self.source),
            'committedInputs': self.committed, 'base': base, 'docker': docker,
            'platform': config['platform'], 'storeIndexSha256': self.index_pin, 'store': {
                'included': len(self.index.get('included', {})), 'excluded': len(self.index.get('excluded', {})),
                'fullLockVerified': False, 'tarballs': [self.blobs[sri] for sri in sorted(self.blobs)]},
            'helperSha256': sha256(self.helper).hexdigest(),
            'recipeId': config['recipe']['id'], 'recipe': self.recipe, 'oracle': config['oracle'],
            'assignment': config['assignment'], 'committedScriptFacts': {
                'path': 'package.json', 'sha256': self.committed['package.json'],
                'packageManager': self.manager, 'build': self.npm_metadata['scripts'].get('build') if not self.source_check else None,
                'selectedTest': self.npm_metadata['scripts'][self.recipe['testScript']]},
            'effects': {'containerCreates': 1, 'containerStarts': 0, 'imageCommits': 1, 'networkMode': 'none',
                        'hostMounts': [], 'publishedPorts': [], 'exactContainerCleanup': True},
            'acknowledgedLimitations': _LIMITATIONS,
        }
        if self.source_check:
            self.execution = {'kind': 'npm-source-check-v1', 'dependencies': 'none', 'outputs': 'none',
                              'sourcePaths': self.recipe['sourcePaths']}
            self.intent.update(sourceFiles=self.source_manifest, execution=self.execution,
                               executionSha256=fingerprint(self.execution),
                               sourceSelectionSha256=self.source_binding['sourceSelectionSha256'],
                               sourceProofSha256=self.source_binding['sourceProofSha256'])
            self.intent['store'] = {'kind': NO_INSTALL_STORE, 'included': 0,
                'excluded': len(self.lock['packages']) - 1, 'fullLockVerified': False, 'tarballs': []}
            self.intent['acknowledgedLimitations'] = {**_LIMITATIONS, 'storeCoverage': 'none-no-dependencies-installed'}
        else:
            self.intent['sourceManifestSha256'] = self.source_binding['sourceManifestSha256']
        self.intent_pin = fingerprint(self.intent)

    def preview(self):
        if self.missing:
            return {'status': 'preparation-inputs-missing', 'executionAuthorized': False,
                    'target': {**self.config['target'], 'tree': self.reader.tree},
                    'platform': self.config['platform'], 'missingTarballs': self.missing,
                    'ignoredFiles': self.ignored, 'outputExists': os.path.lexists(self.output)}
        return {'status': 'preparation-preview', 'executionAuthorized': False, 'intentSha256': self.intent_pin,
                'intent': self.intent, 'ignoredFiles': self.ignored, 'outputExists': os.path.lexists(self.output)}

    def before_effect(self):
        self.verify_inputs()
        verify_tools({'docker': {k: self.config['docker'][k] for k in ('path', 'sha256')}}, 'docker')
        if os.path.lexists(self.output / _WITHDRAWN):
            raise Refused('Preparation consent was withdrawn')

    def write_archive(self, output):
        with os.fdopen(owned_file(self.output, output, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                layout = _LAYOUT[1:]
                store = layout + '/dependency-store'
                source = layout + '/source'
                dirs = {'opt', 'opt/aim-ci', layout, source, store}
                if not self.source_check:
                    dirs.add(store + '/blobs')
                for name in self.source:
                    dirs.update(str(parent) for parent in PurePosixPath(source, name).parents if str(parent) != '.')
                for path in sorted(dirs):
                    member(archive, path)
                member(archive, PLATFORM_ENTRYPOINT[1:], self.helper)
                member(archive, layout + '/source.commit', self.commit)
                if self.proof is not None:
                    member(archive, layout + '/source.proof.json', canonical(self.proof).encode())
                for name, row in sorted(self.source.items()):
                    member(archive, source + '/' + name, row['data'], mode=row['mode'])
                member(archive, store + '/index.json', canonical(self.index).encode())
                for row in sorted(self.blobs.values(), key=lambda item: item['sha256']):
                    with os.fdopen(owned_file(self.tarballs, row['name'], os.O_RDONLY), 'rb') as blob:
                        data = blob.read(row['size'] + 1)
                        if (len(data) != row['size'] or sha256(data).hexdigest() != row['sha256']
                                or 'sha512-' + b64encode(sha512(data).digest()).decode() != row['integrity']):
                            raise Refused('Reviewed local tarball changed before staging')
                        member(archive, store + '/blobs/' + row['sha256'] + '.tgz', data)
            stream.flush()
            os.fsync(stream.fileno())

    def retain(self, name, value):
        raw = canonical(value).encode()
        if os.path.lexists(self.output / name):
            if read_owned(self.output, name, MAX_FILE) != raw:
                raise Refused('Retained preparation bytes differ')
        else:
            write_state(self.output, name, raw)
        return sha256(raw).hexdigest()

    def plan(self, image):
        output, config = self.output, self.config
        manifest_pin = self.args.manifest_sha256
        recipe_dir = output / 'recipe'
        if not os.path.lexists(recipe_dir):
            with new_owned_directory(recipe_dir):
                pass
        files = {name: self.retain('recipe/' + name, content)
                 for name, content in (('recipe.json', self.recipe), ('oracle.json', config['oracle']))}
        recipe_pin = self.retain('recipe/recipe-manifest.json', {'format': 'aim-ci-recipe',
            'id': config['recipe']['id'], 'version': '1.0.0', 'files': files})
        assignment_pin = self.retain('assignment.json', config['assignment'])
        profile = {'version': 1, 'imageDigest': image['imageId'], 'tools': config['base']['tools'],
                   'dependencyStoreSha256': self.index_pin}
        if 'browserRuntime' in config['base']:
            profile['browserRuntime'] = config['base']['browserRuntime']
        profile_pin = self.retain('container-profile.json', profile)
        identity = {'controlManifestSha256': manifest_pin, 'recipeManifestSha256': recipe_pin,
                    'target': config['target'], 'assignmentSha256': assignment_pin,
                    'runtimeSha256': container_runtime_sha(profile['tools'], profile.get('browserRuntime')),
                    'containerProfileSha256': profile_pin,
                    'targetTree': self.reader.tree, 'gitReaderSha256': fingerprint({'git': config['git']})}
        if self.source_check:
            identity.update(sourceProofSha256=self.source_binding['sourceProofSha256'],
                            sourceSelectionSha256=self.source_binding['sourceSelectionSha256'],
                            executionSha256=fingerprint(self.execution))
        reference = f'Local preparation consent {self.intent_pin} by OS principal {os.getuid()}:{os.getgid()}'
        review = {'version': 2, 'kind': 'operator-record', 'decision': 'reviewed-for-planning',
                  **identity, 'testScript': self.recipe['testScript'], 'reviewReference': reference,
                  'committedInputs': self.committed}
        if self.source_check:
            review['sourceFiles'] = self.source_manifest
        if 'testSelector' in self.recipe:
            review['testSelector'] = self.recipe['testSelector']
        review_pin = self.retain('planning-review.json', review)
        approval_pin = self.retain('planning-approval.json', {'version': 2, 'kind': 'operator-record',
            'reviewReference': reference, 'controlManifestSha256': manifest_pin, 'recipeManifestSha256': recipe_pin,
            'target': config['target'], 'runtime': {'git': config['git']}, 'committedInputs': self.committed,
            'containerProfileSha256': profile_pin, 'reviewSha256': review_pin,
            'state': {'mode': 'uninitialized', 'path': str(output / 'planning-state')}})
        args = SimpleNamespace(manifest_sha256=manifest_pin, recipe_package=recipe_dir, recipe_sha256=recipe_pin,
            approval=output/'planning-approval.json', approval_sha256=approval_pin, assignment=output/'assignment.json',
            assignment_sha256=assignment_pin, checkout=self.checkout, review=output/'planning-review.json',
            review_sha256=review_pin, container_profile=output/'container-profile.json', container_profile_sha256=profile_pin)
        plan = plan_recipe(self.root, args, self.control)
        plan_pin = self.retain('plan.json', plan)
        policy_pin = self.retain('docker-policy.json', {'version': 1, 'endpoint': config['docker']['endpoint'],
            'imageDigest': image['imageId'], 'adapterSha256': file_sha(Path(docker_engine.__file__)),
            'runtimeSha256': identity['runtimeSha256'], 'controlManifestSha256': manifest_pin})
        run_config = {'version': 1, 'manifestSha256': manifest_pin, 'stateDir': str(output / 'run-state'),
                      'platform': config['platform']}
        for key, filename, pin in (('plan', 'plan.json', plan_pin),
                                   ('containerProfile', 'container-profile.json', profile_pin),
                                   ('dockerPolicy', 'docker-policy.json', policy_pin)):
            run_config[key] = {'path': str(output / filename), 'sha256': pin}
        config_pin = self.retain('run-config.json', run_config)
        return {'planSha256': plan_pin, 'runConfigPath': str(output / 'run-config.json'), 'runConfigSha256': config_pin}

    def prepare(self):
        if self.missing:
            raise Refused('Applicable dependency bytes are missing. Use --preview to see the required package, SRI and URL.')
        if not self.args.resume:
            if self.args.authorize_intent != self.intent_pin:
                raise Refused('Explicit preparation approval must match the exact preview intent SHA-256')
            with new_owned_directory(self.output) as directory:
                write_state(directory, _AUTH, canonical({'intent': self.intent, 'intentSha256': self.intent_pin,
                    'authorizedAt': datetime.now(timezone.utc).isoformat(), 'consent': 'explicit-local-preparation-only'}).encode())
        validate_directory(self.output, private=True)
        with operator_lock(self.output):
            auth = report_json(read_owned(self.output, _AUTH))
            fields(auth, 'intent intentSha256 authorizedAt consent', 'preparation authorization')
            if (auth.get('intent') != self.intent or auth.get('intentSha256') != self.intent_pin
                    or auth.get('consent') != 'explicit-local-preparation-only'):
                raise Refused('Retained preparation authorization differs from this exact effect')
            self.before_effect()
            self.retain('source-manifest.json', self.source_manifest)
            if self.proof is not None:
                self.retain('source.proof.json', self.proof)
            self.retain('dependency-index.json', self.index)
            fresh = not os.path.lexists(self.output / _ARCHIVE)
            if fresh:
                if os.path.lexists(self.output / _ARCHIVE_PIN):
                    raise Refused('Previously pinned preparation archive is missing')
                self.write_archive(_ARCHIVE)
            with os.fdopen(owned_file(self.output, _ARCHIVE, os.O_RDONLY), 'rb') as stream:
                archive_pin = measure_file(stream, _MAX_ARCHIVE)
            record = {'intentSha256': self.intent_pin, 'archive': archive_pin}
            if not fresh and not os.path.lexists(self.output / _ARCHIVE_PIN):
                raise Refused('Preparation archive lacks its original pinned receipt')
            self.retain(_ARCHIVE_PIN, record)
            image = ImagePreparation(self.output, self.intent, self.intent_pin, self.before_effect).run(_ARCHIVE, archive_pin)
            result = {'status': 'prepared-reviewed-build-inputs', 'executionAuthorized': False,
                      'intentSha256': self.intent_pin, 'image': image, **self.plan(image)}
            self.retain('preparation-result.json', result)
            return result


def cleanup_preparation(root, args):
    output = canonical_path(str(args.output), 'Preparation output')
    validate_directory(output, private=True)
    auth = report_json(read_owned(output, _AUTH))
    fields(auth, 'intent intentSha256 authorizedAt consent', 'preparation authorization')
    intent = auth['intent']
    invocation = intent.get('invocation') if isinstance(intent, dict) else None
    if (not isinstance(invocation, dict) or intent.get('effect') != 'prepare-one-reviewed-local-build-image'
            or not digest(args.intent_sha256) or args.intent_sha256 != auth['intentSha256']
            or fingerprint(intent) != args.intent_sha256 or auth['consent'] != 'explicit-local-preparation-only'
            or intent.get('manifestSha256') != args.manifest_sha256
            or invocation.get('controller') != str(root) or invocation.get('output') != str(output)
            or (invocation.get('uid'), invocation.get('gid')) != (os.getuid(), os.getgid())
            or (os.getuid(), os.getgid()) != (os.geteuid(), os.getegid())):
        raise Refused('Preparation cleanup requires the original local intent')
    if args.preview and args.remove_image is None:
        raise Refused('Cleanup preview requires an explicit full --remove-image ID')
    if args.remove_image is not None:
        config_args = SimpleNamespace(config=invocation.get('config'), config_sha256=invocation.get('configSha256'),
                                      manifest_sha256=args.manifest_sha256)
        preparation = BuildPreparation(root, config_args)
        if preparation.intent != intent or preparation.intent_pin != args.intent_sha256:
            raise Refused('Current preparation inputs differ from the original intent')
        prepared = report_json(read_owned(output, 'preparation-result.json', MAX_FILE))
        if (not isinstance(prepared, dict) or prepared.get('status') != 'prepared-reviewed-build-inputs'
                or prepared.get('executionAuthorized') is not False or prepared.get('intentSha256') != args.intent_sha256):
            raise Refused('Final image cleanup requires the original completed preparation result')
        validate_directory(output / 'image-journal', private=True)
        with os.fdopen(owned_file(output, _ARCHIVE, os.O_RDONLY), 'rb') as stream:
            archive = measure_file(stream, _MAX_ARCHIVE)
        if report_json(read_owned(output, _ARCHIVE_PIN, MAX_FILE)) != {
                'intentSha256': args.intent_sha256, 'archive': archive}:
            raise Refused('Original preparation archive differs from the retained receipt')

        def verify_inputs():
            preparation.verify_inputs()
            verify_tools({'docker': {key: intent['docker'][key] for key in ('path', 'sha256')}}, 'docker')
            if (report_json(read_owned(output, _AUTH, MAX_FILE)) != auth
                    or report_json(read_owned(output, 'preparation-result.json', MAX_FILE)) != prepared):
                raise Refused('Original preparation records changed before image cleanup')

        image = ImagePreparation(output, intent, args.intent_sha256, verify_inputs)
        preview = image.final_image(args.remove_image, prepared.get('image'), archive, preview=True)
        if args.preview:
            return preview, 0
    withdrawal = {'intentSha256': args.intent_sha256}
    marker = output / _WITHDRAWN
    if not os.path.lexists(marker):
        try:
            write_state(output, _WITHDRAWN, canonical(withdrawal).encode())
        except Refused:
            if not os.path.lexists(marker):
                raise
    if report_json(read_owned(output, _WITHDRAWN)) != withdrawal:
        raise Refused('Retained preparation withdrawal does not bind the original intent')

    result = {**withdrawal, 'withdrawalRecorded': True, 'executionAuthorized': False, 'derivedImageRemoved': False}
    try:
        with operator_lock(output):
            if args.remove_image is not None:
                verify_inputs()
                removal = image.final_image(args.remove_image, prepared.get('image'), archive)
                return {**result, 'status': 'image-cleanup-complete',
                        'imageRemoval': removal, 'derivedImageRemoved': True}, 0
            staging = ImagePreparation(output, intent, args.intent_sha256, None).cleanup()
    except ActiveController:
        return {**result, 'status': 'cleanup-pending-active-controller'}, 1
    return {**result, 'status': staging['status'], 'staging': staging}, 0


def prepare_reviewed_build(root, args):
    if args.action == 'cleanup-build-preparation':
        return cleanup_preparation(root, args)
    preparation = BuildPreparation(root, args)
    return (preparation.preview() if args.preview else preparation.prepare()), 0
