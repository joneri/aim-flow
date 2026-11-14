"""One reviewed project-owned command, never a substitute release policy.

No ambient environment, shell wrapping, automated retry, or inferred success.
The command runs on the host with its operator's privileges: this is NOT the
network-denying validation adapter or an adversarial execution sandbox.
"""
import fnmatch
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import subprocess
import time

from . import control_package as package, project_onboarding as shape
from .effects import EffectJournal
from .owned_paths import Refused, new_owned_directory, owned_file, read_owned_content, safe_path
from .project_discovery import CommitReader, unique_json
from .project_execution import digest, tool_identity, tree_identity

DECLARATION = 'aim-flow-project-command-declaration'
INTENT = 'aim-flow-project-command-intent'
RESULT = 'aim-flow-project-command-result'


def canonical(value):
    shape._text(value, 'canonical path')
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise Refused('Project command paths must be canonical and absolute')
    safe_path(path)
    return path


def file_pin(value):
    shape._keys(value, ('path', 'sha256'), 'file pin')
    path = canonical(value['path'])
    raw = read_owned_content(path.parent, path.name)
    package.check_digest(raw, value['sha256'], 'Pinned command input')
    return raw


def assertion_contract(checks):
    if not isinstance(checks, list) or not checks:
        raise Refused('Project receipt/evidence needs explicit equality assertions')
    for check in checks:
        shape._keys(check, ('path', 'equals'), 'receipt assertion')
        path = check['path']
        if not isinstance(path, list) or not path:
            raise Refused('Assertion path must be a nonempty key/index list')
        if any(not isinstance(part, str) and (type(part) is not int or part < 0) for part in path):
            raise Refused('Assertion path components must be object keys or nonnegative indices')


def assertions(value, checks):
    assertion_contract(checks)
    for check in checks:
        path = check['path']
        current = value
        for part in path:
            if isinstance(current, dict) and isinstance(part, str) and part in current:
                current = current[part]
            elif isinstance(current, list) and type(part) is int and 0 <= part < len(current):
                current = current[part]
            else:
                raise Refused('Project receipt/evidence assertion path is absent')
        # JSON true and 1 must never compare equal, including inside collections.
        if shape.encode(current) != shape.encode(check['equals']):
            raise Refused('Project receipt/evidence assertion does not match')


def source_identity(reader):
    """Compare actual tracked bytes/modes; Git status alone can hide local drift."""
    result = {}
    for name, mode, kind, oid in reader.entries(reader.commit, recursive=True):
        shape._source_path(name)
        path = reader.checkout / name
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise Refused('Host command v1 requires regular tracked files; links/submodules need separate review')
        raw = reader.blob(oid)
        if read_owned_content(reader.checkout, name) != raw:
            raise Refused('Tracked command source differs from the approved commit')
        info = path.lstat()
        if bool(info.st_mode & 0o111) != (mode == '100755'):
            raise Refused('Tracked command source mode differs from the approved commit')
        result[name] = {'gitObject': oid, 'mode': mode, 'sha256': package.sha256(raw)}
    # status/diff refresh can execute repository clean/process filters, even in
    # preview. These plumbing reads do not invoke source filters or project code.
    index = {}
    for entry in reader.read('ls-files', '--stage', '-z').split(b'\0'):
        if not entry: continue
        metadata, path = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode('ascii').split()
        if stage != '0': raise Refused('Project command index has unresolved stages')
        index[path.decode('utf-8')] = (mode, oid)
    if index != {name: (item['mode'], item['gitObject']) for name, item in result.items()}:
        raise Refused('Project command index differs from the approved commit')
    if reader.read('ls-files', '--others', '--exclude-standard', '-z'):
        raise Refused('Project command requires clean untracked source')
    reader.verify_head()
    return result


def receipt_files(target, receipt):
    directory = target / receipt['directory']
    safe_path(directory)
    if not directory.exists():
        return {}
    result = {}
    for path in sorted(directory.iterdir()):
        if fnmatch.fnmatchcase(path.name, receipt['filenamePattern']):
            raw = read_owned_content(directory, path.name)
            result[path.name] = package.sha256(raw)
    return result


def qualified_tool_environment(tools, source_git, environment, *, state=None):
    """Preserve canonical Git's helper prefix behind private PATH aliases."""
    environment = dict(environment)
    if state is not None and (Path(state) / 'intent.json').exists():
        retained = unique_json(read_owned_content(Path(state), 'intent.json'))
        if 'toolRuntimeSupport' not in retained:
            return environment, {}  # Historical intents retain their original runtime semantics.
    if tools.get('git') != source_git or 'GIT_EXEC_PATH' in environment:
        return environment, {}
    # The same pinned Git already used by CommitReader, never a model command.
    process = subprocess.run([source_git['path'], '--exec-path'], capture_output=True,
        env={'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': os.devnull,
             'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'})
    if process.returncode or len(process.stdout) > 4096:
        raise Refused('Pinned Git helper directory could not be qualified')
    directory = canonical(process.stdout.decode().strip())
    if directory.resolve() != directory or not directory.is_dir():
        raise Refused('Pinned Git helper directory is not canonical')
    helpers = {}
    for name in ('git-remote-http', 'git-remote-https', 'git-upload-pack', 'git-receive-pack'):
        path = (directory / name).resolve()
        if path.exists():
            helpers[name] = tool_identity({'path': str(path), 'sha256': package.sha256(path.read_bytes())})
    environment['GIT_EXEC_PATH'] = str(directory)
    return environment, {'git': {'execPath': str(directory), 'helpers': helpers}}


def prepare(root, args):
    package.verify_installation(root, args.manifest_sha256)
    path = canonical(str(args.declaration))
    raw = file_pin({'path': str(path), 'sha256': args.declaration_sha256})
    value = unique_json(raw)
    shape._keys(value, ('format', 'schemaVersion', 'target', 'git', 'tools', 'trees', 'inputs',
        'evidence', 'command', 'environment', 'environmentFile', 'review', 'scope', 'receipt', 'timeoutSeconds', 'outputBytes'), 'project command declaration')
    if value['format'] != DECLARATION or type(value['schemaVersion']) is not int or value['schemaVersion'] not in (1, 2):
        raise Refused('Unsupported project command declaration')
    shape._keys(value['target'], ('checkout', 'commit', 'tree'), 'command target')
    target = value['target']
    checkout = canonical(target['checkout'])
    git = tool_identity(value['git'])
    reader = CommitReader(root, checkout, Path(git['path']), git['sha256'], target['commit'])
    if reader.tree != target['tree']:
        raise Refused('Project command tree differs from the approved target')
    sources = source_identity(reader)
    if not isinstance(value['tools'], dict) or not value['tools']:
        raise Refused('Project command requires named pinned executables')
    tools = {}
    for name, pin in value['tools'].items():
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9._-]*', name):
            raise Refused('Invalid command tool name')
        tools[name] = tool_identity(pin)
    trees = []
    if not isinstance(value['trees'], list):
        raise Refused('Invalid command runtime trees')
    for item in value['trees']:
        shape._keys(item, ('path', 'sha256'), 'runtime tree pin')
        tree = tree_identity(item['path'])
        if tree['sha256'] != item['sha256']:
            raise Refused('Project command runtime tree changed')
        trees.append(tree)
    for key in ('inputs', 'evidence'):
        if not isinstance(value[key], list):
            raise Refused('Invalid pinned command ' + key)
    for item in value['inputs']:
        file_pin(item)
    for item in value['evidence']:
        shape._keys(item, ('path', 'sha256', 'assertions'), 'prerequisite evidence')
        evidence = file_pin({key: item[key] for key in ('path', 'sha256')})
        assertions(unique_json(evidence), item['assertions'])
    if not value['evidence']:
        raise Refused('Project command requires separately reviewed prerequisite evidence')
    shape._keys(value['command'], ('tool', 'arguments'), 'project command')
    tool, arguments = value['command']['tool'], value['command']['arguments']
    if not isinstance(tool, str) or tool not in tools:
        raise Refused('Command executable must be a pinned named tool')
    shape._texts(arguments, 'literal command arguments')
    shape._keys(value['review'], ('reference', 'effects', 'qualifications', 'prerequisites'), 'command review')
    shape._text(value['review']['reference'], 'command review reference')
    for key in ('effects', 'qualifications', 'prerequisites'):
        shape._texts(value['review'][key], 'command review ' + key)
        if not value['review'][key]:
            raise Refused('Project command requires effects and qualifications review')
    shape._keys(value['scope'], ('destinations', 'selections'), 'project command scope')
    shape._texts(value['scope']['destinations'], 'effect destinations')
    if not value['scope']['destinations'] or not isinstance(value['scope']['selections'], dict):
        raise Refused('Command scope needs explicit destinations and named selections')
    for name, paths in value['scope']['selections'].items():
        shape._text(name, 'selection name')
        shape._texts(paths, 'selected scope')
    environment = value['environment']
    if not isinstance(environment, dict):
        raise Refused('Command environment must be explicit')
    for name, setting in environment.items():
        if (not isinstance(name, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name)
                or name == 'PATH' or re.search(r'(?:^|_)(?:TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY)$', name, re.I)
                or not isinstance(setting, str) or '\0' in setting):
            raise Refused('Invalid command environment; PATH is derived from pinned tools and secret values must use existing credential facilities')
    private = value['environmentFile']
    if private is not None:
        shape._keys(private, ('path', 'sha256', 'keys', *(('confidentialKeys',) if 'confidentialKeys' in private else ())), 'private environment reference')
        canonical(private['path'])  # Reference only: do not read credentials during preview.
        if not isinstance(private['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', private['sha256']):
            raise Refused('Private environment requires an independently supplied SHA-256')
        shape._texts(private['keys'], 'private environment field names')
        if not private['keys'] or len(set(private['keys'])) != len(private['keys']):
            raise Refused('Private environment requires distinct explicit field names')
        confidential = private.get('confidentialKeys', private['keys'])
        shape._texts(confidential, 'confidential environment field names')
        if len(set(confidential)) != len(confidential) or not set(confidential) <= set(private['keys']):
            raise Refused('Confidential fields must be a distinct subset of selected fields')
        for name in private['keys']:
            if (not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name) or name in environment or name == 'PATH'
                    or name.upper() in ('BASH_ENV', 'ENV', 'ZDOTDIR', 'SHELL', 'GIT_SSH_COMMAND')
                    or name.upper().startswith(('LD_', 'DYLD_', 'NODE_', 'PYTHON', 'GIT_CONFIG_', 'NPM_CONFIG_'))):
                raise Refused('Private fields cannot override declared environment or runtime loading')
    receipt = value['receipt']
    runtime_preparation = value['schemaVersion'] == 2
    output_paths = []
    if runtime_preparation:
        shape._keys(receipt, ('mode', 'outputTrees', 'meaning'), 'runtime preparation contract')
        if receipt['mode'] != 'runtime-preparation':
            raise Refused('Schema two is only local runtime preparation')
        shape._texts(receipt['outputTrees'], 'runtime output trees')
        if not receipt['outputTrees'] or len(set(receipt['outputTrees'])) != len(receipt['outputTrees']):
            raise Refused('Select distinct runtime output trees')
        for name in receipt['outputTrees']:
            shape._source_path(name)
            if any(part.lower() == '.git' for part in Path(name).parts):
                raise Refused('Runtime output cannot overlap Git metadata')
            output = canonical(str(checkout / name))
            if any(output.is_relative_to(other) or other.is_relative_to(output) for other in output_paths):
                raise Refused('Runtime output trees overlap')
            if any((checkout / source).is_relative_to(output) or output.is_relative_to(checkout / source)
                   for source in sources):
                raise Refused('Runtime output overlaps tracked source')
            output_paths.append(output)
        if not Path(args.state).exists() and any(p.exists() for p in output_paths):
            raise Refused('Runtime preparation requires fresh absent output trees')
    else:
        shape._keys(receipt, ('directory', 'filenamePattern', 'assertions', 'meaning'), 'project receipt contract')
        shape._source_path(receipt['directory'])
        pattern = receipt['filenamePattern']
        shape._text(pattern, 'receipt filename pattern')
        if '/' in pattern or '\\' in pattern or pattern in ('.', '..'):
            raise Refused('Receipt pattern must select filenames within one declared directory')
        assertion_contract(receipt['assertions'])
    shape._text(receipt['meaning'], 'project receipt meaning')
    for key in ('timeoutSeconds', 'outputBytes'):
        if type(value[key]) is not int or value[key] <= 0:
            raise Refused('Command time/output limits must be positive integers')
    state = shape._outside(canonical(str(args.state)), root, checkout)
    protected = [path, Path(git['path']), *[Path(t['path']) for t in tools.values()],
        *[Path(t['path']) for t in trees], *[Path(t['path']) for t in value['inputs'] + value['evidence']],
        *([Path(private['path'])] if private else [])]
    for other in protected:
        if state.is_relative_to(other) or other.is_relative_to(state):
            raise Refused('Command state overlaps pinned input or tool')
    for output in output_paths:
        for other in [root.resolve(), state, *protected]:
            if output.is_relative_to(other) or other.is_relative_to(output):
                raise Refused('Runtime output overlaps protected command input')
    environment, tool_support = qualified_tool_environment(tools, git, environment, state=state)
    intent = {'format': INTENT, 'schemaVersion': 1, 'controllerManifestSha256': args.manifest_sha256,
        'declarationSha256': args.declaration_sha256, 'declaration': value, 'sources': sources,
        'runtimeTrees': trees, 'state': str(state), 'argv': [tools[tool]['path'], *arguments],
        'environment': {**environment, 'PATH': str(state / 'bin')},
        'receiptFilesBefore': {} if runtime_preparation else receipt_files(checkout, receipt),
        'executionBoundary': 'Reviewed host command with operator privileges; no network/filesystem sandbox.',
        'acceptance': 'not-established',
        **({'toolRuntimeSupport': tool_support} if tool_support else {})}
    if runtime_preparation:
        intent['schemaVersion'] = 2
        intent['runtimeOutputsBefore'] = {str(p): tree_identity(p) if p.exists() else None for p in output_paths}
    return intent, reader


def durable_write(state, name, data):
    with os.fdopen(owned_file(state, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def check_inputs(root, args, intent):
    # Completion may create receipts and ignored outputs, so compare the immutable
    # portion separately from the pre-effect receipt inventory.
    current, _ = prepare(root, args)
    bindings = args.state / 'bin'
    expected = intent['declaration']['tools']
    if ({p.name for p in bindings.iterdir()} != set(expected) or
            any(not (bindings / name).is_symlink() or os.readlink(bindings / name) != tool['path']
                for name, tool in expected.items())):
        return False
    return {k: v for k, v in current.items() if k not in ('receiptFilesBefore', 'runtimeOutputsBefore')} == {
        k: v for k, v in intent.items() if k not in ('receiptFilesBefore', 'runtimeOutputsBefore')}


def private_environment(reference):
    """Read only at dispatch. Never include file bytes/values in exceptions."""
    if reference is None:
        return {}
    try:
        raw = file_pin({key: reference[key] for key in ('path', 'sha256')})
        values = {}
        for line in raw.decode('utf-8').splitlines():
            match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
            if not match or match[1] not in reference['keys']:
                continue
            name = match[1]
            if name in values:
                raise ValueError('duplicate')
            parts = shlex.split(match[2], comments=True, posix=True)
            if len(parts) != 1 or not parts[0] or '\0' in parts[0]:
                raise ValueError('invalid value')
            values[name] = parts[0]  # No variable expansion, substitution, shell or eval.
        if set(values) != set(reference['keys']):
            raise ValueError('missing fields')
        return values
    except (Refused, OSError, ValueError, UnicodeError):
        raise Refused('Private environment input is missing, changed or outside the reviewed literal dotenv subset') from None


def capture(command, cwd, environment, state, prefix, seconds, output_limit, redactions=()):
    """Bound process group/logs; redact private values before any disk write."""
    patterns = sorted({v.encode() for v in redactions if v}, key=len, reverse=True)
    keep = max((len(v) for v in patterns), default=1) - 1
    pending = {key: b'' for key in ('stdout', 'stderr')}
    paths = {key: prefix + '.' + key for key in pending}
    streams = {key: os.fdopen(owned_file(state, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb')
               for key, name in paths.items()}

    retained_total = 0

    def flush(key, chunk=b'', final=False):
        nonlocal retained_total, reason
        data = pending[key] + chunk
        end = len(data) if final else max(0, len(data) - keep)
        output = bytearray()
        index = 0
        while index < end:
            found = next((value for value in patterns if data.startswith(value, index)), None)
            if found:
                output.extend(b'[REDACTED]'); index += len(found)
            else:
                output.append(data[index]); index += 1
        available = max(0, output_limit - retained_total)
        streams[key].write(output[:available])
        retained_total += min(len(output), available)
        if len(output) > available: reason = 'output-limit'
        pending[key] = data[index:]

    started = time.monotonic(); deadline = None if seconds is None else started + seconds; total = 0; reason = None
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            for key in streams: selector.register(getattr(process, key), selectors.EVENT_READ, key)
            while selector.get_map():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0: reason = 'timeout'; break
                for event, _ in selector.select(remaining):
                    chunk = os.read(event.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(event.fileobj); flush(event.data, final=True); continue
                    retained = chunk[:max(0, output_limit - total)]
                    flush(event.data, retained); total += len(chunk)
                    if total > output_limit: reason = 'output-limit'; break
                if reason: break
            if not reason:
                try: process.wait(timeout=None if deadline is None else max(.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired: reason = 'timeout'
    except BaseException:
        reason = 'interrupted'
        raise
    finally:
        if process is not None:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
            for key in streams: getattr(process, key).close()
        for key in streams:
            # A truncated private value at a log limit is also sensitive. Drop
            # the held suffix unless the stream reached a normal end.
            if not reason: flush(key, final=True)
            streams[key].close()
    return {'argv': command, 'exitCode': process.returncode, 'termination': reason,
        'elapsedSeconds': time.monotonic() - started,
        'logs': {key: {'path': name, 'sha256': package.sha256(read_owned_content(state, name))}
                 for key, name in paths.items()}}


def execute(root, args, intent):
    state, value = args.state, intent['declaration']
    (state / 'bin').mkdir(mode=0o700)
    for name, tool in value['tools'].items():
        (state / 'bin' / name).symlink_to(tool['path'])
    current, _ = prepare(root, args)
    if current != intent:
        raise Refused('Command inputs or prior receipts changed before dispatch')
    # Exclusive durable marker is authoritative even if process launch or any
    # later receipt/journal write has an uncertain outcome. Never delete/reset it.
    durable_write(state, 'started.json', shape.encode({'intentSha256': digest(intent),
        'status': 'started-outcome-unknown', 'replayAuthorized': False}))
    private = private_environment(value['environmentFile'])
    confidential = {name: private[name] for name in value['environmentFile'].get('confidentialKeys', private)} if private else {}
    observed = capture(intent['argv'], Path(value['target']['checkout']), {**intent['environment'], **private},
        state, 'command', value['timeoutSeconds'], value['outputBytes'], confidential.values())
    result = {'format': RESULT, 'schemaVersion': value['schemaVersion'], 'status': 'outcome-unknown',
        'intentSha256': digest(intent), 'process': observed, 'projectReceipt': None,
        'projectReceiptMeaning': value['receipt']['meaning'], 'inputsUnchanged': False,
        'replayAuthorized': False, 'acceptance': 'not-established',
        'operationsPerformedByFlow': ['Invoked the exact reviewed project command and observed its process outcome.'],
        'prerequisitesPerformedSeparately': value['review']['prerequisites'],
        'scope': value['scope']}
    try:
        if value['environmentFile'] is not None:
            file_pin({key: value['environmentFile'][key] for key in ('path', 'sha256')})
        result['inputsUnchanged'] = check_inputs(root, args, intent)
        if not result['inputsUnchanged']:
            raise Refused('Project command source/tool/config/evidence changed')
        if value['schemaVersion'] == 2:
            if any(item is not None for item in intent['runtimeOutputsBefore'].values()):
                raise Refused('Runtime preparation did not start with absent output trees')
            if observed['exitCode'] != 0 or observed['termination'] is not None:
                raise Refused('Runtime preparation command did not complete successfully')
            outputs = [tree_identity(path) for path in intent['runtimeOutputsBefore']]
            if any(not tree['files'] for tree in outputs):
                raise Refused('Runtime preparation produced an empty output tree')
            evidence = {'format': 'aim-flow-runtime-preparation', 'schemaVersion': 1,
                'intentSha256': digest(intent), 'target': value['target'],
                'outputTrees': outputs, 'sourceIdentity': intent['sources'],
                'tools': value['tools'], 'inputRuntimeTrees': intent['runtimeTrees'],
                'inputs': value['inputs'], 'process': observed}
            raw = shape.encode(evidence)
            durable_write(state, 'runtime-preparation.json', raw)
            result.update(schemaVersion=2, status='runtime-preparation-completed',
                runtimePreparation={'retainedPath': 'runtime-preparation.json', 'sha256': package.sha256(raw)},
                preparedRuntimeTrees=[{key: tree[key] for key in ('path', 'sha256')} for tree in outputs])
            result['operationsPerformedByFlow'].append('Measured newly created local runtime trees after successful preparation; no project, release or deployment acceptance.')
            return result
        current = receipt_files(Path(value['target']['checkout']), value['receipt'])
        fresh = sorted(set(current) - set(intent['receiptFilesBefore']))
        if len(fresh) != 1:
            raise Refused('Expected exactly one new project completion record')
        path = Path(value['target']['checkout']) / value['receipt']['directory'] / fresh[0]
        raw = read_owned_content(path.parent, path.name)
        if package.sha256(raw) != current[fresh[0]]:
            raise Refused('Project completion record changed while inspected')
        if package.sha256(raw) in intent['receiptFilesBefore'].values():
            raise Refused('Project completion record repeats pre-existing bytes under a new filename')
        record = unique_json(raw)
        assertions(record, value['receipt']['assertions'])
        def includes_private(item):
            if isinstance(item, str): return any(setting in item for setting in confidential.values())
            if isinstance(item, dict): return any(includes_private(k) or includes_private(v) for k, v in item.items())
            if isinstance(item, list): return any(includes_private(v) for v in item)
            return False
        if includes_private(record) or any(setting.encode() in raw for setting in confidential.values()):
            raise Refused('Project receipt contains a private environment value; it cannot be retained')
        durable_write(state, 'project-receipt.json', raw)
        result['projectReceipt'] = {'sourcePath': str(path), 'retainedPath': 'project-receipt.json',
            'sha256': package.sha256(raw)}
        if observed['exitCode'] == 0 and observed['termination'] is None:
            result['status'] = 'project-receipt-matched'
            result['operationsPerformedByFlow'].append('Matched the project-owned completion record against the exact reviewed assertions.')
        else:
            result['diagnostic'] = 'Project receipt matched but process did not complete successfully; reconcile before any further action.'
    except (Refused, ValueError, OSError) as error:
        result['diagnostic'] = str(error)
    return result


def verify(root, args):
    package.verify_installation(root, args.manifest_sha256)
    state = canonical(str(args.state))
    raw = read_owned_content(state, 'result.json')
    package.check_digest(raw, args.receipt_sha256, 'Project command receipt')
    result = unique_json(raw)
    intent = unique_json(read_owned_content(state, 'intent.json'))
    if (intent['controllerManifestSha256'] != args.manifest_sha256
            or intent['declarationSha256'] != args.declaration_sha256
            or str(state) != intent['state'] or result['intentSha256'] != digest(intent)):
        raise Refused('Retained project command binding differs')
    declared = file_pin({'path': str(canonical(str(args.declaration))), 'sha256': args.declaration_sha256})
    if unique_json(declared) != intent['declaration']:
        raise Refused('Retained project command declaration differs')
    started = unique_json(read_owned_content(state, 'started.json'))
    if started != {'intentSha256': digest(intent), 'status': 'started-outcome-unknown', 'replayAuthorized': False}:
        raise Refused('Command started marker differs')
    journal = EffectJournal(state, intent, read_only=True)
    if journal.completed_result('project-command', intent) != result:
        raise Refused('Command journal and result differ')
    for item in result['process']['logs'].values():
        shape._source_path(item['path'])
        package.check_digest(read_owned_content(state, item['path']), item['sha256'], 'Project command log')
    receipt = result['projectReceipt']
    if receipt is not None:
        evidence = read_owned_content(state, 'project-receipt.json')
        package.check_digest(evidence, receipt['sha256'], 'Retained project completion record')
        assertions(unique_json(evidence), intent['declaration']['receipt']['assertions'])
    matched = (result['inputsUnchanged'] is True and receipt is not None and
        result['process']['argv'] == intent['argv'] and result['process']['exitCode'] == 0
        and result['process']['termination'] is None)
    if intent['declaration']['schemaVersion'] == 2:
        if receipt is not None or result['status'] == 'project-receipt-matched':
            raise Refused('Runtime preparation cannot claim project acceptance')
        proof = result.get('runtimePreparation')
        complete = False
        if proof is not None:
            if proof.get('retainedPath') != 'runtime-preparation.json':
                raise Refused('Runtime preparation receipt path differs')
            raw_proof = read_owned_content(state, 'runtime-preparation.json')
            package.check_digest(raw_proof, proof['sha256'], 'Runtime preparation evidence')
            evidence = unique_json(raw_proof)
            trees = evidence.get('outputTrees', [])
            expected = {'format': 'aim-flow-runtime-preparation', 'schemaVersion': 1,
                'intentSha256': digest(intent), 'target': intent['declaration']['target'],
                'outputTrees': trees, 'sourceIdentity': intent['sources'],
                'tools': intent['declaration']['tools'], 'inputRuntimeTrees': intent['runtimeTrees'],
                'inputs': intent['declaration']['inputs'], 'process': result['process']}
            if (evidence != expected or [t['path'] for t in trees] != list(intent['runtimeOutputsBefore'])
                    or any(v is not None for v in intent['runtimeOutputsBefore'].values())
                    or any(not t['files'] or t['sha256'] != digest(t['files']) for t in trees)
                    or result.get('preparedRuntimeTrees') != [{k: t[k] for k in ('path', 'sha256')} for t in trees]):
                raise Refused('Runtime preparation inventory or binding differs')
            complete = (result['inputsUnchanged'] is True and bool(trees)
                and result['process']['argv'] == intent['argv'] and result['process']['exitCode'] == 0
                and result['process']['termination'] is None)
        if (result['status'] == 'runtime-preparation-completed') != complete:
            raise Refused('Runtime preparation claim differs from retained observations')
        return {**result, 'receipt': str(state / 'result.json'), 'receiptSha256': package.sha256(raw)}
    if (result['status'] == 'project-receipt-matched') != bool(matched):
        raise Refused('Command completion claim differs from retained observations')
    return {**result, 'receipt': str(state / 'result.json'), 'receiptSha256': package.sha256(raw)}


def command(root, args):
    try:
        if args.action == 'verify-project-command':
            # Historical verification must not need unchanged current HEAD,
            # credentials, the original generated file, or any external call.
            return verify(root, args)
        intent, _ = prepare(root, args)
        if args.preview:
            return {'status': 'command-preview', 'intentSha256': digest(intent), 'intent': intent,
                'executionAuthorized': False}
        if args.authorize_intent != digest(intent):
            raise Refused('Project command requires the exact separately authorized intent')
        with new_owned_directory(args.state):
            parent = os.open(args.state.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            durable_write(args.state, 'intent.json', shape.encode(intent))
            journal = EffectJournal(args.state, intent)
            from openai_budget import fingerprint
            def dispatch(key, request):
                try:
                    result = execute(root, args, request)
                except BaseException as error:
                    durable_write(args.state, 'failure.json', shape.encode({'status': 'outcome-unknown',
                        'failureClass': type(error).__name__, 'replayAuthorized': False}))
                    raise
                return EffectJournal.receipt(key, fingerprint(request), result)
            result = journal.perform('project-command', intent, dispatch)
            durable_write(args.state, 'result.json', shape.encode(result))
        args.receipt_sha256 = package.sha256(read_owned_content(args.state, 'result.json'))
        return verify(root, args)
    except (KeyError, TypeError, AttributeError, UnicodeError) as error:
        raise Refused('Malformed project command metadata: ' + type(error).__name__) from None
