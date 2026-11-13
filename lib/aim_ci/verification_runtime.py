"""Content-bound memoization for fixed, read-only installed Flow verifiers.

A cache entry contains an authenticated *flat input closure*, including nested
verifiers, file bytes, queried directory entries and native Git query results.
A hit checks that closure instead of launching historical verifiers again.
Unknown processes are never cached; an exact caller-validated terminal failure may be reused. This module grants no effects.
"""
from __future__ import annotations
import base64
import builtins
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import runpy
import pwd
import stat
import subprocess
import sys
import tempfile
import time
import signal
from types import SimpleNamespace

_VERSION = 2
_DEADLINE = None
_ACTIVE = None
_NATIVE_RUN = subprocess.run
_OPEN, _IO_OPEN, _OS_OPEN = builtins.open, io.open, os.open
_STAT, _LSTAT, _LISTDIR, _SCANDIR, _READLINK = os.stat, os.lstat, os.listdir, os.scandir, os.readlink
_CLOSE = os.close
_MKDIR = os.mkdir


def remaining(deadline):
    if deadline is None:
        return None
    value = deadline - time.monotonic()
    if value <= 0:
        raise ValueError('Explicit metadata verification deadline exceeded')
    return value


def cleanup_process(process, owns_group):
    # Nested verifiers and Git children stay in the bootstrap's owned group.
    # Only its external owner kills that group; never signal the caller's group.
    try:
        if owns_group:
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def query(command, *, cwd, env, input=None, deadline=None):
    remaining(deadline)
    owns_group = _ACTIVE is None
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=owns_group)
    try:
        stdout, stderr = process.communicate(input, timeout=remaining(deadline))
        remaining(deadline)
        return SimpleNamespace(returncode=process.returncode, stdout=stdout, stderr=stderr)
    except subprocess.TimeoutExpired:
        raise ValueError('Explicit metadata verification deadline exceeded') from None
    finally:
        cleanup_process(process, owns_group)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None: stream.close()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


# Exact historical readonly wrapper, not an arbitrary Python execution facility.
COMPLETED_VALIDATION_WRAPPER = "import sys,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1]+'/lib')\nimport flow_workflow_validation as v\noriginal=v.verified_producer\nseen=[]\ndef record(*a,**k):\n    value=original(*a,**k)\n    seen.append(value)\n    return value\nv.verified_producer=record\nresult=v.verify(Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]))\nassert seen and all(x==seen[0] for x in seen)\nprint(json.dumps({'initial':seen[0],'validation':result}))\n"


def validation_wrapper(command):
    if not isinstance(command, (list, tuple)) or len(command) != 8:
        return False
    c = list(map(str, command))
    return (c[1:4] == ['-IBS', '-c', COMPLETED_VALIDATION_WRAPPER]
        and all(Path(c[i]).is_absolute() for i in (0, 4, 5, 7))
        and len(c[6]) == 64 and all(x in '0123456789abcdef' for x in c[6]))


def command_root(command):
    return Path(command[4]) if validation_wrapper(command) else Path(command[2]).parent


def allowed(command):
    if validation_wrapper(command):
        return True
    if not isinstance(command, (list, tuple)) or len(command) < 4:
        return False
    c = list(map(str, command))
    if c[1] != '-IBS' or Path(c[2]).name != 'aim-flow.py':
        return False
    if c[3] == 'verify-project-onboarding':
        return not any(x in c for x in ('--authorize-intent', '--key-file', '--continue'))
    return c[3] in ('work', 'repair-candidate', 'resume-candidate') and '--verify' in c and not any(
        x in c for x in ('--authorize-intent', '--key-file', '--continue'))


def _read(path):
    with _OPEN(path, 'rb') as stream:
        return stream.read()


def _shape(path, *, content=False, directory=False, campaign=False):
    try:
        s = _LSTAT(path)
    except FileNotFoundError:
        return {'kind': 'missing'}
    result = {'mode': s.st_mode, 'uid': s.st_uid, 'gid': s.st_gid, 'links': s.st_nlink}
    if stat.S_ISLNK(s.st_mode):
        result.update(kind='link', target=_READLINK(path))
        resolved = os.path.realpath(path)
        if resolved == path: raise ValueError('Unresolvable verification input link')
        result['resolved'] = resolved
        result['referent'] = _shape(resolved, content=content, directory=directory, campaign=campaign)
        return result
    if stat.S_ISDIR(s.st_mode):
        result.update(kind='directory')
        # Child creation must not invalidate a mere ancestor traversal.
        result.pop('links')
        if directory:
            result['entries'] = sorted([entry.name, _LSTAT(os.path.join(path, entry.name)).st_mode]
                                       for entry in _SCANDIR(path))
        return result
    if not stat.S_ISREG(s.st_mode):
        return {**result, 'kind': 'special'}
    if campaign and os.path.basename(path) == 'budget.json':
        try:
            ledger = json.loads(_read(path))
            identity = ledger.get('identity', {})
            if (set(ledger) == {'identity', 'requests'} and isinstance(ledger['requests'], list)
                    and identity.get('campaignDirectory') == str(Path(path).parent)
                    and 'historicalBudget' in identity):
                return {**result, 'kind': 'campaign', 'ledger': ledger}
        except (ValueError, AttributeError, TypeError):
            pass  # Historical budget files retain their exact immutable byte pin.
    result.update(kind='file', bytes=s.st_size, mtime=s.st_mtime_ns)
    if content:
        raw = _read(path)
        after = _LSTAT(path)
        if (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_size) != (
                after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
            raise ValueError('Verification input changed while being witnessed')
        result['sha256'] = digest(raw)
    return result


def _git_query(command):
    c = list(map(str, command))
    if not c or Path(c[0]).name != 'git':
        return False
    i = 1
    while i < len(c):
        if c[i] in ('--no-replace-objects', '--no-optional-locks'):
            i += 1
        elif c[i] == '-c' and i + 1 < len(c):
            i += 2
        else:
            break
    return i < len(c) and c[i] in ('--version', 'rev-parse', 'ls-tree', 'cat-file', 'diff',
                                    'merge-base', 'status', 'show', 'ls-files', 'rev-list', 'log')


def _query_result(result):
    return {'returncode': result.returncode, 'stdout': digest(result.stdout or b''),
            'stderr': digest(result.stderr or b'')}


class Trace:
    def __init__(self, store):
        self.store = Path(store)
        self.files = {}
        self.queries = {}
        self.fds = {}
        self.busy = False
        self.reasons = set()
        self._cacheable = True

    @property
    def cacheable(self):
        return self._cacheable

    @cacheable.setter
    def cacheable(self, value):
        if not value:
            frame = sys._getframe(1)
            self.reasons.add(Path(frame.f_code.co_filename).name + ':' + str(frame.f_lineno))
        self._cacheable = value

    def path(self, value, dir_fd=None):
        if isinstance(value, int):
            return self.fds.get(value)
        try:
            value = os.fsdecode(value)
        except TypeError:
            return None
        if not os.path.isabs(value):
            base = self.fds.get(dir_fd) if dir_fd is not None else os.getcwd()
            if base is None:
                self.cacheable = False
                return None
            value = os.path.join(base, value)
        return os.path.normpath(value)

    def observe(self, path, *, content=False, directory=False):
        if self.busy or path is None or path == os.devnull:
            return
        if path == str(self.store) or path.startswith(str(self.store) + os.sep):
            return
        self.busy = True
        try:
            campaign = bool(getattr(self, 'campaign_read_depth', 0))
            key = encoded([path, content, directory, campaign]).decode()
            value = _shape(path, content=content, directory=directory, campaign=campaign)
            if value.get('kind') == 'campaign' and any(r['status'] != 'completed' for r in value['ledger']['requests']):
                self.cacheable = False
            if key in self.files and self.files[key]['value'] != value:
                self.cacheable = False
            self.files[key] = {'path': path, 'content': content, 'directory': directory, 'campaign': campaign, 'value': value}
        except (OSError, ValueError):
            self.cacheable = False
        finally:
            self.busy = False

    def absorb(self, closure):
        for row in closure['files']:
            key = encoded([row['path'], row['content'], row['directory'], row.get('campaign', False)]).decode()
            if key in self.files and self.files[key] != row:
                self.cacheable = False
            self.files[key] = row
        for row in closure['queries']:
            self.queries[encoded(row['spec']).decode()] = row

    def closure(self):
        return {'files': sorted(self.files.values(), key=lambda x: encoded(x)),
                'queries': sorted(self.queries.values(), key=lambda x: encoded(x))}


def _install_trace(trace):
    global _ACTIVE
    _ACTIVE = trace
    def audit(event, args):
        if trace.busy: return
        if event == 'open':
            trace.observe(trace.path(args[0]), content=True)
        elif event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.fork', 'socket.connect'):
            if not getattr(trace, 'native_authorized', False):
                trace.cacheable = False
                trace.reasons.add('unsupported-audit:' + event)
        elif event in ('os.remove', 'os.rename', 'os.rmdir', 'os.chmod', 'os.truncate'):
            trace.cacheable = False
    sys.addaudithook(audit)

    def op(path, *args, **kw):
        mode = kw.get('mode', args[0] if args else 'r')
        if not trace.busy and (not isinstance(mode, str) or any(x in mode for x in 'wax') or '+' in mode and not str(trace.path(path)).endswith('.lock')):
            trace.cacheable = False
        trace.observe(trace.path(path), content=True)
        return _OPEN(path, *args, **kw)

    def iop(path, *args, **kw):
        mode = kw.get('mode', args[0] if args else 'r')
        if not trace.busy and (not isinstance(mode, str) or any(x in mode for x in 'wax') or '+' in mode and not str(trace.path(path)).endswith('.lock')):
            trace.cacheable = False
        trace.observe(trace.path(path), content=True)
        return _IO_OPEN(path, *args, **kw)

    def osp(path, flags, *args, **kw):
        name = trace.path(path, kw.get('dir_fd'))
        if not trace.busy and flags & (os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            trace.cacheable = False
        trace.observe(name, content=True)
        fd = _OS_OPEN(path, flags, *args, **kw)
        trace.fds[fd] = name
        return fd

    def close(fd):
        trace.fds.pop(fd, None)
        return _CLOSE(fd)

    def st(path, *args, **kw):
        trace.observe(trace.path(path, kw.get('dir_fd')))
        return _STAT(path, *args, **kw)

    def lst(path, *args, **kw):
        trace.observe(trace.path(path, kw.get('dir_fd')))
        return _LSTAT(path, *args, **kw)

    def ls(path='.'):
        trace.observe(trace.path(path), directory=True)
        return _LISTDIR(path)

    def scan(path='.'):
        trace.observe(trace.path(path), directory=True)
        return _SCANDIR(path)

    def link(path, *args, **kw):
        trace.observe(trace.path(path, kw.get('dir_fd')))
        return _READLINK(path, *args, **kw)

    def mkdir(path, *args, **kw):
        value = _MKDIR(path, *args, **kw)
        if not trace.busy: trace.cacheable = False
        return value

    os.mkdir = mkdir
    builtins.open, io.open, os.open, os.close = op, iop, osp, close
    os.stat, os.lstat, os.listdir, os.scandir, os.readlink = st, lst, ls, scan, link

    def native(command, *args, **kw):
        if trace.busy:
            return _NATIVE_RUN(command, *args, **kw)
        if _git_query(command) and not args and kw.get('input') is None and kw.get('capture_output'):
            trace.observe(os.path.abspath(str(command[0])), content=True)
            spec = {'command': list(map(str, command)), 'cwd': str(kw.get('cwd', os.getcwd())),
                    'env': dict(os.environ) if kw.get('env') is None else kw['env']}
            trace.native_authorized = True
            try:
                result = query(command, cwd=spec['cwd'], env=spec['env'], deadline=_DEADLINE)
            finally:
                trace.native_authorized = False
            trace.queries[encoded(spec).decode()] = {'spec': spec, 'result': _query_result(result)}
            return result
        trace.cacheable = False
        trace.reasons.add('unsupported-native:' + repr(list(map(str, command))[:18]))
        return _NATIVE_RUN(command, *args, **kw)
    subprocess.run = native


def _valid(closure, deadline=None):
    remaining(deadline)
    _valid.reason = {'kind': 'native-or-accounting-drift'}
    ledgers = {}
    protected = {str(Path(r['path']).parent) for r in closure['files']
                 if Path(r['path']).name in ('candidate-receipt.json', 'grant.json', 'phase-grant.json', 'attempt.json')}
    for row in closure['files']:
        remaining(deadline)
        expected = row['value']
        current = _shape(row['path'], content=row['content'], directory=row['directory'], campaign=row.get('campaign', False))
        if expected.get('kind') == 'campaign':
            if current.get('kind') != 'campaign': return False
            old, now = expected['ledger'], current['ledger']
            if ({k: v for k, v in expected.items() if k != 'ledger'} !=
                    {k: v for k, v in current.items() if k != 'ledger'}
                    or old['identity'] != now['identity']
                    or now['requests'][:len(old['requests'])] != old['requests']): return False
            if any(Path(r['state']).is_relative_to(p) for r in now['requests'][len(old['requests']):] for p in protected):
                return False
            if row['path'] not in ledgers:
                import flow_api_delivery as api
                identity = old['identity']
                config = {k: identity[k] for k in ('campaignDirectory', 'historicalBudget', 'campaignContinuation') if k in identity}
                ledgers[row['path']] = api.read_existing_campaign(config)
            if ledgers[row['path']] != now: return False
        elif current != expected:
            _valid.reason = {'kind': 'file-input-drift', 'path': row['path']}
            return False
    groups = {}
    for row in closure['queries']:
        spec = row['spec']; command = spec['command']
        if not _git_query(command):
            return False
        if len(command) >= 4 and command[-3] == 'cat-file' and command[-2] in ('-s', 'blob', 'tree', 'commit'):
            oid = command[-1]
            if len(oid) == 40 and all(c in '0123456789abcdef' for c in oid):
                group = {'command': command[:-3], 'cwd': spec['cwd'], 'env': spec['env']}
                key = encoded(group).decode()
                groups.setdefault(key, (group, []))[1].append(row)
                continue
        result = query(command, cwd=spec['cwd'], env=spec['env'], deadline=deadline)
        if _query_result(result) != row['result']:
            return False
    # Content-addressed object reads are revalidated with one native batch per
    # repository, not two subprocesses per historical file per repair.
    for group, rows in groups.values():
        oids = sorted({row['spec']['command'][-1] for row in rows})
        objects = {}
        for offset in range(0, len(oids), 256):
            chunk = oids[offset:offset+256]
            result = query([*group['command'], 'cat-file', '--batch'], cwd=group['cwd'],
                env=group['env'], input=('\n'.join(chunk) + '\n').encode(), deadline=deadline)
            if result.returncode:
                return False
            data = result.stdout; cursor = 0
            for oid in chunk:
                end = data.find(b'\n', cursor)
                if end < 0: return False
                header = data[cursor:end].split(); cursor = end + 1
                if len(header) != 3 or header[0].decode() != oid: return False
                size = int(header[2]); body = data[cursor:cursor+size]; cursor += size
                if len(body) != size or data[cursor:cursor+1] != b'\n': return False
                cursor += 1
                objects[oid] = (header[1].decode(), body, result.stderr)
            if cursor != len(data): return False
        for row in rows:
            command = row['spec']['command']; kind, body, stderr = objects[command[-1]]
            value = str(len(body)).encode() + b'\n' if command[-2] == '-s' else body
            if command[-2] != '-s' and command[-2] != kind: return False
            if row['result'] != {'returncode': 0, 'stdout': digest(value), 'stderr': digest(stderr)}:
                return False
    remaining(deadline)
    return True


def _key(store):
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    if store.is_symlink() or store.stat().st_uid != os.getuid() or store.stat().st_mode & 0o077:
        raise ValueError('Verification store must be private and owned')
    path = store / 'authority.key'
    try:
        fd = _OS_OPEN(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(os.urandom(32)); stream.flush(); os.fsync(stream.fileno())
    info = _LSTAT(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('Invalid verification authority file')
    raw = _read(path)
    if len(raw) != 32:
        raise ValueError('Invalid verification authority length')
    return raw


def _save(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.proof-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def shared_store(runtime_sha):
    # OS account home, not caller-supplied HOME or a controller installation.
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ('.aim-flow-verified-evidence-' + runtime_sha)


def terminal_failure(command, expected):
    if expected is None:
        return None
    if (not allowed(command) or command[3] != 'resume-candidate'
            or not isinstance(expected, dict)
            or expected.get('format') != 'aim-flow-resumed-candidate-local-run'
            or expected.get('status') != 'local-validation-failed'
            or expected.get('localFeatureAccepted') is not False):
        raise ValueError('Only exact retained resumed-candidate terminal failures may be cached')
    return json.loads(encoded(expected))


def result_matches(result, expected):
    if result.stderr:
        return False
    if expected is None:
        return result.returncode == 0
    if result.returncode != 1:
        return False
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('Duplicate JSON key')
            value[key] = item
        return value
    try:
        return json.loads(result.stdout, object_pairs_hook=unique) == expected
    except (ValueError, UnicodeError):
        return False


def evaluate(command, root, environment, runner, *, metadata_limit=64000, timeout_seconds=None,
             store=None, _deadline=None, expected_terminal_failure=None):
    """Verify first, reuse only authenticated unchanged input closures thereafter."""
    expected_failure = terminal_failure(command, expected_terminal_failure)
    if not allowed(command):
        return runner(command, root, environment, metadata_limit=metadata_limit, timeout_seconds=timeout_seconds)
    deadline = _deadline if _deadline is not None else (
        None if timeout_seconds is None else time.monotonic() + timeout_seconds)
    remaining(deadline)
    runtime_sha = digest(_read(__file__))
    store = Path(store) if store else shared_store(runtime_sha)
    # All allowed commands have absolute pinned inputs. Use their target's
    # immutable root, never the invoking controller's root, for relative process
    # context. Keep every other environment setting explicit and identity-bound.
    target_root = command_root(command)
    if not target_root.is_absolute():
        raise ValueError('Metadata verifier must have an absolute target controller')
    root = target_root
    environment = {**environment, 'HOME': str(target_root)}
    trace = _ACTIVE
    previous_busy = trace.busy if trace else False
    if trace: trace.busy = True
    try:
        key = _key(store)
        identity = {'version': _VERSION, 'runtime': runtime_sha,
                    'command': list(map(str, command)), 'cwd': str(root), 'env': environment,
                    'metadataLimit': metadata_limit, 'timeout': timeout_seconds,
                    'python': digest(_read(command[0])), 'expectedTerminalFailure': expected_failure}
        name = digest(encoded(identity))
        path = store / (name + '.json')
        proof = None
        if path.exists():
            envelope = json.loads(_read(path))
            payload = envelope['proof']
            expected = hmac.new(key, encoded(payload), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(envelope.get('signature', ''), expected) or payload['identity'] != identity:
                raise ValueError('Verified evidence authentication failed')
            # Memoization is only for the native query validation in this process;
            # files are always read again. No mtime-only source acceptance.
            if _valid(payload['closure'], deadline):
                proof = payload
        if proof is None:
            fd, trace_name = tempfile.mkstemp(dir=store, prefix='.trace-')
            os.close(fd)
            try:
                bootstrap = [str(command[0]), '-IBS', str(Path(__file__).resolve()), '--bootstrap',
                             str(store), trace_name, json.dumps(list(map(str, command))),
                             json.dumps(deadline)]
                result = runner(bootstrap, root, environment, metadata_limit=metadata_limit,
                                timeout_seconds=remaining(deadline))
                observed = json.loads(_read(trace_name)) if os.path.getsize(trace_name) else None
                if observed and not observed['cacheable']:
                    _save(store / (name + '.diagnostic'), {'reasons': observed.get('reasons', []),
                        'fileInputs': len(observed['closure']['files']), 'gitQueries': len(observed['closure']['queries'])})
                valid = (result_matches(result, expected_failure) and observed and observed['cacheable']
                         and _valid(observed['closure'], deadline))
                if observed and observed['cacheable'] and not valid:
                    _save(store / (name + '.diagnostic'), {'postVerification': getattr(_valid, 'reason', None),
                        'returncode': result.returncode, 'stderrBytes': len(result.stderr)})
                if valid:
                    proof = {'identity': identity, 'closure': observed['closure'],
                             'stdout': base64.b64encode(result.stdout).decode(), 'returncode': result.returncode}
                    _save(path, {'proof': proof, 'signature': hmac.new(key, encoded(proof), hashlib.sha256).hexdigest()})
            finally:
                os.unlink(trace_name)
            if proof is None:
                if trace: trace.cacheable = False
                return result
        if trace:
            trace.absorb(proof['closure'])
        return SimpleNamespace(returncode=proof['returncode'], stdout=base64.b64decode(proof['stdout']), stderr=b'')
    finally:
        if trace: trace.busy = previous_busy


def bootstrap(store, trace_path, command, deadline):
    global _DEADLINE
    _DEADLINE = deadline
    if not allowed(command):
        raise ValueError('Only fixed read-only Flow metadata commands may use verification transport')
    root = command_root(command)
    sys.path.insert(0, str(root / 'lib'))
    # Load this runtime exactly once; nested calls share its trace.
    sys.modules['aim_ci.verification_runtime'] = sys.modules[__name__]
    # Early onboarding installations predate project validation entirely. Their
    # fixed read-only entry point still gets traced; it has no nested validation
    # transport to replace. Do not hide missing dependencies in newer commands.
    if (not validation_wrapper(command) and command[3] == 'verify-project-onboarding'
            and not (root / 'lib/aim_ci/project_validation.py').is_file()):
        project_validation = None
    else:
        from aim_ci import project_validation
    # Only the subprocess transport is replaced, never a semantic check.
    def nested(cmd, cwd, env, *, metadata_limit=64000, timeout_seconds=None, expected_terminal_failure=None):
        return evaluate(cmd, cwd, env, _transport, metadata_limit=metadata_limit,
                        timeout_seconds=None, store=store, _deadline=deadline,
                        expected_terminal_failure=expected_terminal_failure)
    original_transport = getattr(project_validation, '_verification_process', None)
    def direct(cmd, cwd, env, **kw):
        if validation_wrapper(cmd):
            return nested(cmd, cwd, env, **kw)
        if original_transport is None:
            raise ValueError('No historical transport for unrecognized verification command')
        return original_transport(cmd, cwd, env, **kw)
    if project_validation is not None:
        project_validation.verification_process = nested
        project_validation._verification_process = direct
    trace = Trace(store)
    try:
        import flow_api_delivery as api
    except ImportError:
        pass  # Minimal transport qualification fixtures have no campaign API.
    else:
        campaign_read = api.Campaign.read
        def read_campaign(account):
            trace.campaign_read_depth = getattr(trace, 'campaign_read_depth', 0) + 1
            try:
                return campaign_read(account)
            finally:
                trace.campaign_read_depth -= 1
        api.Campaign.read = read_campaign
    _install_trace(trace)
    if not validation_wrapper(command):
        trace.observe(str(Path(command[2])), content=True)
    manifest = root / 'control-manifest.json'
    trace.observe(str(manifest), content=True)
    if manifest.exists():
        for name in json.loads(_read(manifest)).get('files', {}):
            trace.observe(str(root / name), content=True)
    code = 0
    sys.argv = ['-c', *command[4:]] if validation_wrapper(command) else command[2:]
    try:
        if validation_wrapper(command):
            exec(COMPLETED_VALIDATION_WRAPPER, {'__name__': '__main__'})
        else:
            runpy.run_path(command[2], run_name='__main__')
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
        if isinstance(error.code, str): print(error.code, file=sys.stderr)
    finally:
        trace.busy = True
        _save(Path(trace_path), {'cacheable': trace.cacheable and code in (0, 1), 'closure': trace.closure(), 'reasons': sorted(trace.reasons)})
    return code


def _transport(command, root, environment, *, metadata_limit=64000, timeout_seconds=None):
    # Identical output bound and process-group cleanup, independent of the
    # historical controller's old default wall clock.
    import selectors
    import signal
    import time
    process = subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=_ACTIVE is None)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    done = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise ValueError('Explicit metadata verification deadline exceeded')
                for event, _ in selector.select(remaining):
                    chunk = os.read(event.fileobj.fileno(), 8192)
                    if not chunk: selector.unregister(event.fileobj)
                    else: output[event.data].extend(chunk)
                    if sum(map(len, output.values())) > metadata_limit:
                        raise ValueError('Verification metadata output bound exceeded')
        process.wait(timeout=None if deadline is None else max(.001, deadline - time.monotonic()))
        done = True
        return SimpleNamespace(returncode=process.returncode, **{k: bytes(v) for k, v in output.items()})
    finally:
        cleanup_process(process, _ACTIVE is None)
        process.stdout.close(); process.stderr.close()


if __name__ == '__main__':
    if len(sys.argv) != 6 or sys.argv[1] != '--bootstrap':
        raise SystemExit('Read-only verification bootstrap only')
    raise SystemExit(bootstrap(sys.argv[2], sys.argv[3], json.loads(sys.argv[4]), json.loads(sys.argv[5])))
