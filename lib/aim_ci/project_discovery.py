"""Complete, network-free observations of an explicit local Git commit."""
from hashlib import sha1
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from openai_budget import Refused, safe_path
from .control_package import sha256
from .installed_runtime import file_sha


LOCKS = {'package-lock.json': 'npm', 'npm-shrinkwrap.json': 'npm',
         'pnpm-lock.yaml': 'pnpm', 'yarn.lock': 'yarn'}
PYTHON_FILES = ('requirements.txt', 'poetry.lock', 'uv.lock', 'Pipfile.lock')
CONTAINERS = ('Dockerfile', 'compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml')
CI = ('.gitlab-ci.yml', 'azure-pipelines.yml', 'Jenkinsfile')
WORKFLOWS = '.github/workflows'
ROOT_FILES = ('package.json', *LOCKS, 'pyproject.toml', *PYTHON_FILES, *CONTAINERS, *CI, WORKFLOWS)
DEPENDENCY_KEYS = ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies')
STABLE_VERSION = r'(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})'
DISPLAY_VERSION = r'[A-Za-z0-9.^~<>=|* +_-]{1,120}'
SCRIPT_NAME = r'[A-Za-z0-9][A-Za-z0-9:._-]{0,99}'
SCRIPT_REFERENCE = re.compile(
    r'(?:^|[ \t])(?P<inline>`)?npm[ \t]+run(?:-script)?[ \t]+'
    r'(?(inline)(?P<code>[^ \t`]+)(?=[ \t`])(?=[^`]*`)|(?P<plain>[^ \t]+))'
)
PROJECT_DOC_PATH = r'(?:AGENTS|README)\.md|docs/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9][A-Za-z0-9_.-]*\.md'
NPM_HOOKS = ('preinstall', 'install', 'postinstall', 'prepublish', 'preprepare', 'prepare',
             'postprepare', 'pretest', 'posttest', 'prebuild', 'postbuild')


class SourceBindingMismatch(Refused):
    pass


class CommitReader:
    def __init__(self, root, checkout, git, pin, commit):
        if not re.fullmatch('[a-f0-9]{40}', commit):
            raise Refused('Project discovery requires a full Git commit SHA')
        safe_path(checkout)
        self.checkout = checkout.resolve(strict=True)
        if root.is_relative_to(self.checkout) or self.checkout.is_relative_to(root):
            raise Refused('Project checkout and control installation must be separate')
        if not git.is_absolute() or git.resolve() != git or not git.is_file() or not os.access(git, os.X_OK):
            raise Refused('Project discovery requires an explicit absolute Git executable')
        if not re.fullmatch('[a-f0-9]{64}', pin) or file_sha(git) != pin:
            raise Refused('Git executable SHA-256 mismatch')
        self.git = str(git)
        self.commit = commit
        version = re.match(rb'^git version (\d+)\.(\d+)(?:\.(\d+))?(?=[ .-]|$)', self.read('--version'))
        if not version or tuple(int(part or 0) for part in version.groups()) < (2, 54, 0):
            raise Refused('Project discovery requires Git 2.54.0 or newer')
        if self.read('rev-parse', '--show-toplevel').decode().rstrip('\n') != str(self.checkout):
            raise Refused('Project discovery requires a Git worktree root')
        self.verify_head()
        self.tree = self.read('rev-parse', f'{commit}^{{tree}}').decode().strip()

    def read(self, *args, maximum=None, missing_ok=False):
        environment = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                       'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1',
                       'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_REPLACE_OBJECTS': '1'}
        command = [self.git, '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', *args]
        result = subprocess.run(command, cwd=self.checkout, env=environment, capture_output=True, timeout=None)
        if missing_ok and result.returncode == 1 and not result.stdout and not result.stderr:
            return None
        if result.returncode or (maximum is not None and len(result.stdout) > maximum):
            raise Refused(f'Local Git could not read {args[0]} from the selected commit')
        return result.stdout

    def verify_head(self):
        if self.read('rev-parse', 'HEAD').decode().strip() != self.commit:
            raise SourceBindingMismatch('Checkout HEAD differs from the selected discovery commit')

    def entries(self, tree, *paths, recursive=False, maximum=None):
        suffix = ('--', *paths) if paths else ()
        options = ('-r',) if recursive else ()
        raw = self.read('ls-tree', '-z', '--full-tree', *options, tree, *suffix, maximum=maximum)
        if raw and not raw.endswith(b'\0'):
            raise Refused('Committed Git tree listing is incomplete')
        for entry in raw.split(b'\0'):
            if entry:
                metadata, path = entry.split(b'\t', 1)
                mode, kind, oid = metadata.decode('ascii').split()
                yield path.decode('utf-8', errors='surrogateescape'), mode, kind, oid

    def size(self, oid):
        raw = self.read('cat-file', '-s', oid)
        if not re.fullmatch(rb'[0-9]+\n', raw):
            raise Refused('Invalid committed Git object size')
        return int(raw)

    def blob(self, oid, *, maximum=None):
        return self.checked_object('blob', oid, maximum)

    def checked_object(self, kind, oid, maximum=None):
        if kind not in ('blob', 'tree', 'commit') or not re.fullmatch('[a-f0-9]{40}', oid):
            raise Refused('Invalid committed Git object identity')
        size = self.size(oid)
        if type(size) is not int or size < 0 or (maximum is not None and size > maximum):
            raise Refused('Committed Git object exceeds its expected read size')
        # Use the pinned object's measured size, never a project-size ceiling.
        raw = self.read('cat-file', kind, oid, maximum=size)
        if len(raw) != size or sha1(f'{kind} {len(raw)}\0'.encode() + raw).hexdigest() != oid:
            raise Refused('Committed Git object bytes differ from their identity')
        return raw


class Observations:
    def __init__(self, reader):
        self.reader = reader
        self.files = {}
        self.facts = []
        self.open = []

    def fact(self, code, value, path, locator, kind='parsed', related=()):
        self.facts.append({'code': code, 'kind': kind, 'value': value,
                           'sources': [{'path': path, 'locator': locator}, *related]})

    def unknown(self, code, detail, sources, next_step):
        self.open.append({'code': code, 'status': 'open', 'detail': detail,
                          'sources': sources, 'next': next_step})

    def content(self, path, mode, kind, oid):
        record = {'path': path, 'commit': self.reader.commit, 'gitObject': oid,
                  'mode': mode, 'gitType': kind, 'status': 'detected'}
        self.files[path] = record
        if kind != 'blob' or mode not in ('100644', '100755'):
            record['status'] = 'not-read'
            self.unknown('non-regular-definition', 'Definition is not a regular committed file.',
                         [{'path': path}], 'Ask the operator for a regular file in the selected commit.')
            return None
        size = self.reader.size(oid)
        record['bytes'] = size
        data = self.reader.checked_object('blob', oid, size)
        if len(data) != size:
            raise Refused('Committed definition differs from its measured size')
        record['sha256'] = sha256(data)
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            record['status'] = 'unsupported-encoding'
            self.unknown('definition-encoding', 'Definition is not UTF-8.', [{'path': path}],
                         'Confirm the file format and encoding with the operator.')
            return None

    def parse(self, path, content, decoder, parser):
        try:
            data = decoder(content)
            if not isinstance(data, dict):
                raise ValueError('expected an object')
        except (ValueError, RecursionError):
            self.files[path]['status'] = 'invalid-format'
            self.unknown('invalid-definition', f'{parser} could not parse a definition object.',
                         [{'path': path}], 'Review the committed syntax and format version.')
            return None
        self.files[path].update(status='parsed', parser=parser)
        return data


def unique_json(content):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f'Invalid JSON number {value}')

    return json.loads(content, object_pairs_hook=object_pairs, parse_constant=reject_constant)


def inspect_node(report, texts):
    path = 'package.json'
    package = report.parse(path, texts[path], unique_json, 'json') if path in texts else None
    manager = None
    if package is not None:
        for key in ('name', 'packageManager'):
            if isinstance(package.get(key), str):
                report.fact('node-' + key, package[key], path, key)
        engines = package.get('engines')
        if isinstance(engines, dict):
            report.fact('node-engines', {key: value for key, value in engines.items() if isinstance(value, str)},
                        path, 'engines')
        workspaces = package.get('workspaces')
        if isinstance(workspaces, dict):
            workspaces = workspaces.get('packages')
        if isinstance(workspaces, list) and all(isinstance(item, str) for item in workspaces):
            report.fact('node-workspaces', workspaces, path, 'workspaces')
        declaration = package.get('packageManager')
        match = re.fullmatch(r'(npm|pnpm|yarn)@[^\s]+', declaration) if isinstance(declaration, str) else None
        if match:
            manager = match.group(1)
        elif declaration is not None:
            report.unknown('package-manager-declaration', 'Package manager declaration is outside the recognized form.',
                           [{'path': path, 'locator': 'packageManager'}], 'Check the package manager documentation for this declaration.')
        scripts = package.get('scripts')
        if isinstance(scripts, dict):
            for name, definition in scripts.items():
                if not re.fullmatch(SCRIPT_NAME, name):
                    report.unknown('node-script-name-unsupported', 'A declared script name is outside the literal name syntax.',
                                   [{'path': path, 'locator': f'scripts[{json.dumps(name)}]'}],
                                   'Review the exact script name and its invocation with the operator.')
                elif isinstance(definition, str) and definition.strip():
                    report.fact('node-named-script', name, path, 'scripts.' + name)
                    report.fact('node-script-definition', {'name': name, 'body': definition}, path,
                                'scripts.' + name)
        for name in ('build', 'test'):
            locator = f'scripts.{name}'
            if isinstance(scripts, dict) and isinstance(scripts.get(name), str) and scripts[name].strip():
                report.fact('node-' + name + '-definition', scripts[name], path, locator)
            else:
                report.unknown('node-' + name + '-undefined', f'No nonempty npm-style {name} script is declared.',
                               [{'path': path, 'locator': locator}],
                               f'Review named scripts and ask the operator which command to use for {name}.')
        if isinstance(scripts, dict):
            for name in NPM_HOOKS:
                if name in scripts:
                    report.fact('script-lifecycle', name, path, 'scripts.' + name, 'risk')
        for key in DEPENDENCY_KEYS:
            dependencies = package.get(key)
            if isinstance(dependencies, dict):
                report.fact('node-dependencies', sorted(dependencies), path, key)
                if isinstance(dependencies.get('vite'), str):
                    report.fact('vite-indicator', dependencies['vite'], path, f'{key}.vite', 'inferred')
        if isinstance(scripts, dict) and scripts:
            report.fact('script-code-review', 'Package scripts can execute project or dependency code.', path, 'scripts', 'risk')

    locks = [name for name in LOCKS if name in report.files]
    if package is not None and not locks:
        report.unknown('missing-node-lock', 'No recognized root lockfile accompanies package.json.',
                       [{'path': path}], 'Confirm the package manager and committed dependency lock location.')
    if len(locks) > 1 or (manager and any(LOCKS[name] != manager for name in locks)):
        refs = [{'path': name} for name in locks]
        if manager:
            refs.append({'path': path, 'locator': 'packageManager'})
        report.unknown('package-manager-conflict', 'Several lock definitions or the declared manager and lockfile disagree.',
                       refs, 'Ask the operator which manager and lockfile apply to this root.')
    for name in locks:
        if name not in texts:
            continue
        if LOCKS[name] == 'npm':
            inspect_npm_lock(report, name, texts[name], package)
        else:
            report.files[name].update(status='detected', parser='lock-header')
            for number, line in enumerate(texts[name].splitlines(), 1):
                if re.match(r'^(?:lockfileVersion:|# yarn lockfile v\d+|__metadata:)', line):
                    report.fact('lock-header-indicator', line, name, f'line {number}', 'detected')
                    break
            report.unknown('unsupported-lock-format', f'{LOCKS[name]} lockfile contents are outside the qualified parser.',
                           [{'path': name}], 'Review the matching package-manager lockfile documentation or adapter.')


def inspect_npm_lock(report, path, text, package):
    lock = report.parse(path, text, unique_json, 'npm-lock-json')
    if lock is None:
        return
    version = lock.get('lockfileVersion')
    shown_version = version if type(version) is int or isinstance(version, str) else None
    report.fact('npm-lock-version', shown_version, path, 'lockfileVersion')
    if type(version) is not int or version != 3 or not isinstance(lock.get('packages'), dict):
        report.files[path]['status'] = 'unsupported-version-or-schema'
        report.unknown('unsupported-npm-lock', 'Only npm lockfile v3 package metadata is interpreted.',
                       [{'path': path, 'locator': 'lockfileVersion'}], 'Review version-specific npm documentation.')
        return
    packages = lock['packages']
    report.fact('npm-locked-package-count', len(packages) - int('' in packages), path, 'packages')
    vite = packages.get('node_modules/vite')
    if isinstance(vite, dict) and isinstance(vite.get('version'), str):
        report.fact('npm-locked-vite-version', vite['version'], path, 'packages["node_modules/vite"].version')
    if package is not None:
        inspect_direct_versions(report, path, packages, package)
    hooks = sorted(name for name, entry in packages.items() if isinstance(entry, dict) and entry.get('hasInstallScript') is True)
    if hooks:
        report.fact('locked-install-hooks', hooks, path, 'packages.*.hasInstallScript', 'risk')


def version_relation(declared, locked):
    """Compare only complete stable numeric versions and a single caret range."""
    caret = declared.startswith('^')
    lower = re.fullmatch(STABLE_VERSION, declared[1:] if caret else declared)
    upper = re.fullmatch(STABLE_VERSION, locked)
    if lower is None or upper is None:
        return 'unsupported-syntax'
    minimum = tuple(map(int, lower.groups()))
    resolved = tuple(map(int, upper.groups()))
    if not caret:
        return 'matches' if minimum == resolved else 'mismatch'
    major, minor, patch = minimum
    ceiling = (major + 1, 0, 0) if major else ((0, minor + 1, 0) if minor else (0, 0, patch + 1))
    return 'matches' if minimum <= resolved < ceiling else 'mismatch'


def inspect_direct_versions(report, lock_path, packages, package):
    for key in DEPENDENCY_KEYS:
        declared = package.get(key)
        if not isinstance(declared, dict):
            continue
        for name, spec in declared.items():
            if not isinstance(spec, str):
                continue
            lock_entry = f'node_modules/{name}'
            entry = packages.get(lock_entry)
            locked = entry.get('version') if isinstance(entry, dict) and not entry.get('link') else None
            relation = version_relation(spec, locked) if isinstance(locked, str) else 'unresolved-lock-entry'
            locator = f'{key}[{json.dumps(name)}]'
            related = [{'path': lock_path, 'locator': f'packages[{json.dumps(lock_entry)}].version'}]
            visible_spec = spec if re.fullmatch(DISPLAY_VERSION, spec) else None
            visible_locked = locked if isinstance(locked, str) and re.fullmatch(DISPLAY_VERSION, locked) else None
            report.fact('npm-direct-version-relation',
                        {'dependency': name, 'declaration': visible_spec, 'lockedVersion': visible_locked,
                         'relation': relation, 'parser': 'exact-and-caret-stable-v1'},
                        'package.json', locator, related=related)
            if relation != 'matches':
                report.unknown('npm-version-relation-open', f'Direct version comparison for {name}: {relation}.',
                               [{'path': 'package.json', 'locator': locator}, *related],
                               'Review the declaration and lock resolution with the intended npm version.')


def inspect_project_documents(report, selected):
    if not selected:
        return
    if (len(set(selected)) != len(selected)
            or any(not re.fullmatch(PROJECT_DOC_PATH, path) for path in selected)):
        raise Refused('Select distinct root README.md or AGENTS.md files, or Markdown files under docs/')
    reader = report.reader
    entries = {path: (mode, kind, oid) for path, mode, kind, oid in reader.entries(reader.commit, *selected)}
    scripts = {fact['value']: fact['sources'][0] for fact in report.facts if fact['code'] == 'node-named-script'}
    for path in selected:
        if path not in entries:
            report.files[path] = {'path': path, 'commit': reader.commit, 'status': 'missing'}
            report.unknown('project-document-missing', 'Selected document is absent from the commit.',
                           [{'path': path}], 'Select a committed project document or supply evidence separately.')
            continue
        try:
            content = report.content(path, *entries[path])
        except (Refused, subprocess.TimeoutExpired):
            report.files[path].update(status='not-readable')
            report.unknown('project-document-unreadable',
                           'Selected document could not be read completely from its pinned Git object.',
                           [{'path': path}], 'Review the pinned Git object and selected documentation separately.')
            continue
        if content is None:
            continue
        report.files[path].update(status='reference-scan', parser='literal-npm-run-reference')
        for number, line in enumerate(content.splitlines(), 1):
            locator = f'line {number}'
            names = dict.fromkeys(match['code'] or match['plain'] for match in SCRIPT_REFERENCE.finditer(line))
            for name in names:
                if not re.fullmatch(SCRIPT_NAME, name):
                    report.unknown('document-script-syntax-unsupported', 'Script token is outside the literal name syntax.',
                                   [{'path': path, 'locator': locator}], 'Confirm the exact invocation with the operator.')
                elif name not in scripts:
                    report.unknown('document-script-unresolved', 'The complete referenced script name is absent from the manifest.',
                                   [{'path': path, 'locator': locator}], 'Confirm which script the documentation refers to.')
                else:
                    report.fact('document-script-reference', name, path, locator,
                                'detected', related=[scripts[name]])


def inspect_python(report, texts):
    path = 'pyproject.toml'
    for name in PYTHON_FILES:
        if name in report.files:
            report.fact('python-dependency-definition', name, name, 'file', 'detected')
    if sum(name in report.files for name in PYTHON_FILES[1:]) > 1:
        report.unknown('python-lock-conflict', 'Several Python lockfiles are present.',
                       [{'path': name} for name in PYTHON_FILES[1:] if name in report.files],
                       'Confirm which Python manager and lockfile apply.')
    if path not in texts:
        return
    data = report.parse(path, texts[path], tomllib.loads, 'tomllib')
    if data is None:
        return
    for section, keys in (('build-system', ('build-backend', 'requires', 'backend-path')),
                          ('project', ('name', 'requires-python', 'dependencies', 'dynamic'))):
        table = data.get(section)
        if isinstance(table, dict):
            for key in keys:
                if key not in table:
                    continue
                value = table[key]
                expected_list = key in ('requires', 'backend-path', 'dependencies', 'dynamic')
                valid = (isinstance(value, list) and all(isinstance(item, str) for item in value)
                         if expected_list else isinstance(value, str))
                if valid:
                    report.fact('python-' + key, value, path, f'{section}.{key}')
                else:
                    report.unknown('python-field-schema', 'Python field has an unexpected value type.',
                                   [{'path': path, 'locator': f'{section}.{key}'}],
                                   'Review the applicable pyproject metadata specification.')
            if table.get('backend-path') or table.get('dynamic'):
                report.unknown('python-dynamic-metadata', 'Python metadata or its backend can require project code.',
                               [{'path': path, 'locator': section}], 'Review backend code and obtain the resolved metadata separately.')
    if isinstance(data.get('tool'), dict):
        report.fact('python-tool-tables', sorted(data['tool']), path, 'tool')
    report.unknown('python-test-command', 'Tool tables alone leave the intended Python test command open.',
                   [{'path': path}], 'Consult the project test documentation and operator.')


def inspect_references(report, path, text):
    report.files[path].update(status='detected', parser='literal-line-indicators')
    # Preserve the actual definition for agent review, not an interpreted command.
    # Multiline run bodies, conditions, action inputs and shells matter together.
    lines = text.splitlines(keepends=True)
    report.fact('definition-review-excerpt', {
        'text': text, 'firstLine': 1, 'lastLine': len(lines),
        'complete': True, 'semantics': 'uninterpreted-source',
    }, path, 'lines 1-' + str(len(lines)), 'detected')
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if path == 'Dockerfile':
            match = re.match(r'^(FROM|RUN|ONBUILD|ADD|COPY|ARG)\b', stripped, re.IGNORECASE)
        else:
            match = re.match(r'^(?:-\s+)?(services|image|build|include|extends|uses|run|script|permissions|secrets|volumes|privileged|runs-on)\s*:', stripped)
        if match:
            report.fact('definition-line-indicator', match.group(1), path, f'line {number}', 'detected')
        if '${' in stripped or '$(' in stripped:
            report.fact('unresolved-expression', 'Expression-like text', path, f'line {number}', 'risk')
    report.unknown('definition-semantics-open', 'Literal indicators leave includes, commands and permissions unresolved.',
                   [{'path': path}], 'Review the matching container or CI documentation and referenced definitions.')



def repository_footprint(reader):
    """Read complete tree metadata only; no source blob or project code is read."""
    unknown = {'status': 'unknown', 'scope': 'committed-tree-metadata', 'buildInputsSelected': False}
    try:
        raw = reader.read('ls-tree', '-r', '-l', '-z', '--full-tree', reader.commit)
        rows = raw.split(b'\0')
        if rows[-1] != b'':
            return {**unknown, 'reason': 'Tree inventory is incomplete.'}
        total, regular, other, groups, largest, names = 0, 0, 0, {}, [], set()
        for row in rows[:-1]:
            metadata, raw_path = row.split(b'\t', 1)
            mode, kind, oid, size = metadata.split()
            path = raw_path.decode('utf-8')
            if (not path or path in names or path.startswith('/')
                    or any(part in ('', '.', '..') for part in path.split('/'))
                    or not re.fullmatch(rb'[a-f0-9]{40}', oid)):
                raise ValueError('Invalid tree metadata')
            names.add(path)
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                other += 1
                continue
            if not re.fullmatch(rb'[0-9]+', size):
                raise ValueError('Invalid blob size')
            count = int(size)
            total += count
            regular += 1
            group = path.split('/', 1)[0] if '/' in path else '(root files)'
            groups[group] = groups.get(group, 0) + count
            largest.append({'path': path, 'bytes': count, 'gitObject': oid.decode()})
        return {'status': 'observed', 'scope': 'committed-tree-metadata', 'buildInputsSelected': False,
                'commit': reader.commit, 'tree': reader.tree, 'regularFileCount': regular,
                'regularFileBytes': total, 'otherEntryCount': other,
                'topLevelBytes': dict(sorted(groups.items())),
                'largestFiles': sorted(largest, key=lambda row: (-row['bytes'], row['path']))[:10]}
    except (Refused, ValueError, UnicodeError, subprocess.TimeoutExpired):
        return {**unknown, 'reason': 'Committed tree metadata could not be read completely.'}


def discover_project(root, args, control, *, reader=None):
    if reader is None:
        reader = CommitReader(root, args.checkout, args.git, args.git_sha256, args.commit)
    report = Observations(reader)
    entries = list(reader.entries(reader.commit, *ROOT_FILES))
    texts = {}
    for path, mode, kind, oid in entries:
        if path == WORKFLOWS and kind == 'tree':
            report.files[path] = {'path': path, 'commit': reader.commit, 'gitObject': oid, 'mode': mode,
                                  'gitType': kind, 'status': 'detected'}
            workflows = [entry for entry in reader.entries(oid) if entry[0].endswith(('.yml', '.yaml'))]
            for name, wf_mode, wf_kind, wf_oid in workflows:
                full = f'{path}/{name}'
                content = report.content(full, wf_mode, wf_kind, wf_oid)
                if content is not None:
                    inspect_references(report, full, content)
            continue
        content = report.content(path, mode, kind, oid)
        if content is not None:
            texts[path] = content
    inspect_node(report, texts)
    inspect_project_documents(report, getattr(args, 'project_doc', []))
    inspect_python(report, texts)
    for path in (*CONTAINERS, *CI):
        if path in texts:
            inspect_references(report, path, texts[path])
    compose = [name for name in CONTAINERS[1:] if name in report.files]
    if len(compose) > 1:
        report.unknown('compose-definition-conflict', 'Several root Compose definitions are present.',
                       [{'path': name} for name in compose], 'Confirm the intended base file and any override order.')
    if not report.files:
        report.unknown('no-recognized-definitions', 'No supported definition names were found in the selected scope.',
                       [], 'Ask the operator for the project root and its build or test documentation.')
    footprint = repository_footprint(reader)
    reader.verify_head()
    return {'version': 1, 'status': 'observed-only', 'executionAuthorized': False,
            'control': {'version': control['version'], 'manifestSha256': args.manifest_sha256},
            'target': {'checkout': str(reader.checkout), 'commit': reader.commit, 'tree': reader.tree},
            'git': {'path': reader.git, 'sha256': args.git_sha256},
            'files': list(report.files.values()), 'facts': report.facts, 'openItems': report.open,
            'repositoryFootprint': footprint,
            'adaptation': {'status': 'proposed-for-review', 'steps': [
                {'name': 'resolve-open-items-and-cite-documentation', 'status': 'not-run'},
                {'name': 'propose-build-test-and-artifact-recipe', 'status': 'not-run'},
                {'name': 'operator-review-of-recipe-and-access', 'status': 'not-run'},
                {'name': 'qualify-execution', 'status': 'not-run'}],
                'documentationOrSkillsConsulted': []}}
