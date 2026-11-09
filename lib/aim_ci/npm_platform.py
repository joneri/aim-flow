"""The reviewed platform subset of an npm v3 lock, without dependency resolution."""
from base64 import b64decode, b64encode
from collections import Counter
import re

from openai_budget import Refused, canonical
from .project_discovery import unique_json, version_relation
from .recipe_plan import digest, fields

PLATFORM_STORE = 'npm-v3-platform-tarballs-v1'
FULL_STORE = 'npm-v3-tarballs-v1'
_ARTIFACT_COMPONENT = r'[A-Za-z0-9@_.+-]+(?: [A-Za-z0-9@_.+-]+)*'
_SRI = re.compile(r'sha512-[A-Za-z0-9+/]{86}==')
_PACKAGE = r'(?:@[a-z0-9_.-]+/)?[a-z0-9_][a-z0-9_.-]*'
_INSTALL_PATH = re.compile(r'node_modules/(' + _PACKAGE + r')(?:/node_modules/(' + _PACKAGE + r'))*')
_REGISTRY_TARBALL = re.compile(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9@%/_.~+-]+\.tgz')


def project_path(name):
    return (isinstance(name, str) and 0 < len(name) <= 240
            and all(len(part) <= 128 and part not in ('.', '..')
                    and re.fullmatch(_ARTIFACT_COMPONENT, part) for part in name.split('/')))


def _dependency_map(entry, field):
    value = entry.get(field, {})
    if not isinstance(value, dict) or any(not isinstance(name, str) or not re.fullmatch(_PACKAGE, name)
            or not isinstance(spec, str) or not spec.strip() for name, spec in value.items()):
        raise Refused('Unsupported npm optional dependency graph: invalid dependency declaration')
    return value


def _check_optional_graph(entries, root, excluded):
    # npm 10.9.9 Arborist optionalSet follows nonoptional incoming edges backwards,
    # then prunes dependencies without a nonoptional referrer outside that set.
    # v1 can retain its selector partition only when those steps cannot prune any included locator.
    reason = 'Unsupported npm optional dependency graph for the v1 platform store'
    if not isinstance(root, dict):
        raise Refused(reason + ': missing root lock metadata')
    locators = {path: _INSTALL_PATH.fullmatch(path).groups() for path in entries}
    names = Counter(nested or top for top, nested in locators.values())

    def top_level(name):
        path = 'node_modules/' + name
        return path if names[name] == 1 and path in entries else None

    def locked_child(path, name):
        while path:
            child = path + '/node_modules/' + name
            if child in entries:
                return child
            path = path.rpartition('/node_modules/')[0]
        return 'node_modules/' + name

    skipped = {locators[path][1] or locators[path][0] for path in excluded}
    for path, (_, nested) in locators.items():
        if nested:
            parent = path.rpartition('/node_modules/')[0]
            if parent not in entries or parent in excluded:
                raise Refused(reason + ': unsupported nested locator below an absent or off-platform package')

    required, optional_referrers, affected = {}, set(), set()
    for path, entry in [('', root), *entries.items()]:
        optional = _dependency_map(entry, 'optionalDependencies')
        peers = _dependency_map(entry, 'peerDependencies')
        dependencies = _dependency_map(entry, 'dependencies')
        hard = {name: spec for name, spec in dependencies.items() if name not in optional}
        if not path:
            dev = _dependency_map(entry, 'devDependencies')
            if dev.keys() & (dependencies.keys() | optional.keys() | peers.keys()):
                raise Refused(reason + ': overlapping root development dependency scopes')
            hard.update(dev)
        if skipped.intersection(hard.keys() | peers.keys()):
            raise Refused(reason + ': off-platform package has a possible nonoptional referrer')
        if path not in excluded:
            for name in skipped.intersection(optional):
                child = locked_child(path, name)
                # Arborist Edge.reload binds its target by locator before range validation.
                if child in excluded:
                    optional_referrers.add(child)
        required[path] = {name: spec for name, spec in hard.items() if name not in peers}
        if path not in excluded:
            continue
        if optional or peers:
            raise Refused(reason + ': off-platform package declares optional or peer children')
        for name, spec in hard.items():
            child = top_level(name)
            if child is None or version_relation(spec, entries[child]['version']) != 'matches':
                raise Refused(reason + ': cannot bind an off-platform dependency to an exact locator')
            affected.add(child)
    if set(excluded) != optional_referrers:
        raise Refused(reason + ': missing optional referrer for an off-platform package')
    if not affected:
        return

    # A proven nonoptional path from the root protects a shared child from pruning.
    seen, pending = {''}, ['']
    while pending and not affected <= seen:
        for name, spec in required[pending.pop()].items():
            path = top_level(name)
            if (path is not None and path not in seen and path not in excluded
                    and version_relation(spec, entries[path]['version']) == 'matches'):
                seen.add(path)
                pending.append(path)
    if not affected <= seen:
        raise Refused(reason + ': child of an off-platform package has no proven nonoptional path from the root')


class UnsupportedNpmPlatform(Refused):
    pass


def validate_platform(target):
    fields(target, 'os cpu libc', 'npm target platform')
    if target['os'] != 'linux' or target['cpu'] not in ('arm64', 'x64') or target['libc'] != 'glibc':
        raise UnsupportedNpmPlatform('Unsupported npm target platform')


def platform_partition(entries, target, *, root=None):
    """Classify the reviewed subset of npm-install-checks/lib/index.js checkList."""
    validate_platform(target)
    included, excluded = set(), {}
    for name, entry in entries.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise Refused('Invalid npm platform lock entry')
        integrity = entry.get('integrity')
        if (not _INSTALL_PATH.fullmatch(name) or entry.get('link') or entry.get('inBundle')
                or not isinstance(entry.get('resolved'), str) or not _REGISTRY_TARBALL.fullmatch(entry['resolved'])
                or not isinstance(integrity, str) or not _SRI.fullmatch(integrity)
                or 'sha512-' + b64encode(b64decode(integrity[7:])).decode() != integrity
                or not isinstance(entry.get('version'), str) or not entry['version']
                or any(key in entry and type(entry[key]) is not bool for key in ('optional', 'link', 'inBundle'))):
            raise Refused('Unsupported npm platform dependency source, integrity or layout')
        mismatches = []
        for axis in ('os', 'cpu', 'libc'):
            if axis not in entry:
                continue
            declared = entry[axis]
            if isinstance(declared, str):
                declared = [declared]
            if (not isinstance(declared, list) or not 1 <= len(declared) <= 64
                    or any(not isinstance(item, str) or not re.fullmatch(r'!?[a-z0-9_-]{1,32}', item)
                           or item == '!any' for item in declared)
                    or len(set(declared)) != len(declared)
                    or ('any' in declared and declared != ['any'])):
                raise Refused('Unsupported npm platform selector')
            value = target[axis]
            allowed = [item for item in declared if not item.startswith('!')]
            if '!' + value in declared or (allowed and allowed != ['any'] and value not in allowed):
                mismatches.append(axis)
        if mismatches:
            if entry.get('optional') is not True:
                raise Refused('Required npm dependency does not match the target platform')
            excluded[name] = mismatches
        else:
            included.add(name)
    if excluded:
        _check_optional_graph(entries, root, excluded)
    return included, excluded


def platform_store(files, lock, index, binding):
    fields(index, 'kind lockSha256 platform included excluded', 'platform dependency store index')
    entries = {name: row for name, row in lock['packages'].items() if name}
    included, excluded = platform_partition(entries, index['platform'], root=lock['packages'].get(''))
    packages = index['included']
    if (index['lockSha256'] != binding['committedInputs']['package-lock.json']
            or canonical(index).encode() != files['index.json']['data']
            or not isinstance(packages, dict) or set(packages) != included
            or index['excluded'] != excluded):
        raise Refused('Platform dependency store does not exactly partition the pinned npm lock')
    expected = {'index.json'}
    for name, row in packages.items():
        fields(row, 'integrity sha256', 'platform dependency tarball')
        integrity = entries[name]['integrity']
        if row['integrity'] != integrity or not digest(row['sha256']):
            raise Refused('Platform dependency store integrity differs from the pinned npm lock')
        path = 'blobs/' + row['sha256'] + '.tgz'
        expected.add(path)
        actual = files.get(path)
        if (actual is None or not actual['size'] or actual['mode'] != 0o644 or actual['sha256'] != row['sha256']
                or 'sha512-' + b64encode(actual['sha512']).decode() != integrity):
            raise Refused('Missing or mismatched applicable npm platform tarball')
    if set(files) != expected:
        raise Refused('Platform dependency store contains unexplained files')
    return {'kind': PLATFORM_STORE, 'platform': index['platform'], 'lockPackageCount': len(entries),
            'packageCount': len(included), 'tarballCount': len(expected) - 1, 'fullLockVerified': False,
            'excludedPackageCount': len(excluded)}


def read_platform_index(data):
    index = unique_json(data)
    if not isinstance(index, dict) or index.get('kind') != PLATFORM_STORE:
        raise Refused('Expected a versioned npm platform dependency store')
    return index
