"""Exact local candidate to a create-only GitHub branch and new draft PR."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import stat
import tempfile
import time
from urllib.parse import quote, urlencode

import flow_github_delivery as gh
from aim_ci import control_package, project_validation
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file, owned_mkdir, read_owned
from aim_ci.project_discovery import CommitReader

local = gh.local
ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = ('Creates only a new branch and new draft PR. Empty expected-ref lease cannot overwrite '
            'an existing branch. No existing PR edit, force overwrite, branch deletion, merge, CI '
            'rerun or release. Evidence pins bind local bytes, not authenticity or causal attribution. '
            'Unknown effects stop; resume only observes. Routine PR text is exact declared text, '
            'not an automatically invented verification claim.')


def branch(value):
    gh.require(gh.pattern(value, '[A-Za-z0-9_][A-Za-z0-9_./-]*') and '..' not in value
               and '//' not in value and not value.endswith(('/', '.'))
               and not any(part.startswith('.') or part.endswith('.lock') for part in value.split('/')),
               'Invalid publication branch')


def executable_pin(spec):
    """Read-only executable pins permit OS hard links; never permit linked paths or outputs."""
    gh.keys(spec, ('path', 'sha256'), 'executable pin')
    path = local.canonical_path(spec['path'])
    gh.require(gh.pattern(spec['sha256'], '[a-f0-9]{64}') and os.access(path, os.X_OK), 'Invalid executable pin')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        gh.require(stat.S_ISREG(before.st_mode), 'Executable must be a regular file')
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    gh.require(identity(before) == identity(after) and digest.hexdigest() == spec['sha256'], 'Pinned executable changed')


def api_pin_identity(value):
    return local.digest(local.encoded(value))


def source_binding(c):
    source = c['source']
    gh.keys(source, ('checkout', 'baseSha', 'headSha', 'treeSha', 'diffSha256', 'git'), 'candidate source')
    for name in ('baseSha', 'headSha', 'treeSha'):
        gh.require(gh.pattern(source[name], '[a-f0-9]{40}'), 'Invalid candidate commit/tree')
    gh.require(gh.pattern(source['diffSha256'], '[a-f0-9]{64}') and source['baseSha'] != source['headSha'],
               'Invalid candidate difference')
    checkout = local.canonical_path(source['checkout'])
    executable_pin(source['git'])
    reader = CommitReader(ROOT, checkout, Path(source['git']['path']), source['git']['sha256'], source['headSha'])
    commit = reader.checked_object('commit', source['headSha'])
    header = commit.split(b'\n\n', 1)[0].splitlines()
    parents = [line[7:].decode('ascii') for line in header if line.startswith(b'parent ')]
    expected_parent = source['baseSha']
    spec = c['candidateVerification']
    if spec.get('producerKind') == 'codex-workflow':
        receipt = gh.unique_json(gh.pinned_metadata(c['evidence']['candidate']))
        intent = receipt.get('intent', {})
        expected_parent = intent.get('parent', {}).get('commit')
        gh.require(intent.get('format') == 'aim-flow-codex-candidate'
            and intent.get('originalBaseline') == source['baseSha']
            and gh.pattern(expected_parent, '[a-f0-9]{40}'), 'Native Codex candidate parent/baseline differs')
    elif 'producerKind' in spec or 'repairConfig' in spec:
        gh.require(spec.get('producerKind') == 'repair-candidate' and 'repairConfig' in spec,
                   'Unknown repair publication producer')
        receipt = gh.unique_json(gh.pinned_metadata(c['evidence']['candidate']))
        intent = receipt.get('intent', {})
        parent = intent.get('parent', {})
        expected_parent = parent.get('commit')
        gh.require(intent.get('originalBaseline') == source['baseSha']
                   and gh.pattern(expected_parent, '[a-f0-9]{40}')
                   and gh.pattern(parent.get('tree'), '[a-f0-9]{40}'), 'Repair parent/baseline binding differs')
        repair_config = gh.unique_json(gh.pinned_metadata(spec['repairConfig']))
        # Check every retained single-parent link, then independently invoke
        # the fixed installed producer verifier. Additional links are explicit.
        cursor = parent
        seen = set()
        while True:
            identity = (cursor['commit'], api_pin_identity(repair_config))
            if identity in seen:
                raise Refused('Publication repair ancestry contains a cycle')
            seen.add(identity)
            ancestor_header = reader.checked_object('commit', cursor['commit']).split(b'\n\n', 1)[0].splitlines()
            if 'parentRepair' not in repair_config:
                gh.require([line[7:].decode('ascii') for line in ancestor_header if line.startswith(b'parent ')]
                           == [source['baseSha']] and ancestor_header[0] == ('tree ' + cursor['tree']).encode(),
                           'Original repair parent must be the exact single child of the original baseline')
                break
            previous = repair_config['parentRepair']
            repair_config = gh.unique_json(gh.pinned_metadata(previous['repairConfig']))
            prior = gh.unique_json(local.file_bytes(Path(previous['workflowState']) / 'candidate-receipt.json'))
            gh.require(prior.get('candidate') == cursor
                       and prior.get('intent', {}).get('originalBaseline') == source['baseSha'],
                       'Repair must retain the exact previous repaired candidate')
            ancestor = prior['intent'].get('parent', {})
            gh.require(gh.pattern(ancestor.get('commit'), '[a-f0-9]{40}')
                       and gh.pattern(ancestor.get('tree'), '[a-f0-9]{40}'), 'Invalid previous repair parent')
            gh.require([line[7:].decode('ascii') for line in ancestor_header if line.startswith(b'parent ')]
                       == [ancestor['commit']] and ancestor_header[0] == ('tree ' + cursor['tree']).encode(),
                       'Previous repair must be the exact single child of its retained parent')
            cursor = ancestor
    gh.require(parents == [expected_parent] and reader.tree == source['treeSha']
               and header[0] == ('tree ' + source['treeSha']).encode(),
               'Candidate must be the exact single child commit/tree of its verified parent')
    reader.checked_object('commit', source['baseSha'])
    reader.checked_object('tree', source['treeSha'])
    diff = reader.read('diff', '--no-ext-diff', '--no-textconv', '--binary', '--full-index', '--no-renames',
                       source['baseSha'], source['headSha'], '--')
    gh.require(diff and local.digest(diff) == source['diffSha256'], 'Complete committed candidate diff changed')
    reader.verify_head()
    return reader


def verify_producer(c):
    spec = c['candidateVerification']
    native = spec.get('producerKind') == 'codex-workflow'
    gh.keys(spec, ('controller', 'python', 'config', 'state',
        *(('producerKind', 'runState', 'runGrant', 'runResult') if native else
          ('producerKind', 'repairConfig') if 'producerKind' in spec or 'repairConfig' in spec else ()),
        *(('producerVerificationTimeoutSeconds',) if 'producerVerificationTimeoutSeconds' in spec else ())), 'candidate verifier')
    timeout = project_validation.producer_timeout(spec)
    gh.keys(spec['controller'], ('path', 'manifestSha256'), 'candidate controller')
    producer = local.canonical_path(spec['controller']['path'])
    state = local.canonical_path(spec['state'])
    before = project_validation.prior_controller(producer, spec['controller']['manifestSha256'])
    manifest = gh.unique_json(before)
    gh.require('lib/flow_workflow.py' in manifest['files'], 'Producer controller lacks the fixed workflow verifier')
    executable_pin(spec['python'])
    gh.require(os.access(spec['python']['path'], os.X_OK), 'Pinned verifier Python is not executable')
    config_raw = gh.pinned_metadata(spec['config'])
    config = gh.unique_json(config_raw)
    gh.require(config.get('manifestSha256') == spec['controller']['manifestSha256'], 'Producer config/controller differ')
    candidate_pin = c['evidence']['candidate']
    gh.require(candidate_pin['path'] == str(state / 'candidate-receipt.json'), 'Candidate receipt is outside producer state')
    raw = gh.pinned_metadata(candidate_pin)
    candidate = gh.unique_json(raw)
    source = {key: value for key, value in c['source'].items() if key != 'git'}
    target = {'checkout': source['checkout'], 'commit': source['headSha'], 'tree': source['treeSha']}
    gh.require(candidate.get('status') == 'candidate-prepared' and candidate.get('source') == source
               and candidate.get('candidate') == target, 'Candidate producer receipt/source differs')
    if native:
        from flow_codex_workflow import verified_local
        verified = verified_local({'controller': spec['controller'], 'config': spec['config'],
            'state': spec['runState'], 'grant': spec['runGrant'], 'result': spec['runResult']},
            spec['controller']['manifestSha256'])
        gh.require(verified['source'] == source and verified['candidate'] == target
            and verified['evidence']['candidate'] == candidate_pin and verified['producerState'] == str(state)
            and verified['runIntent']['python'] == spec['python'],
            'Native Codex verifier did not confirm the exact accepted producer')
        gh.require(before == project_validation.prior_controller(producer, spec['controller']['manifestSha256'])
            and raw == gh.pinned_metadata(candidate_pin) and config_raw == gh.pinned_metadata(spec['config']),
            'Native producer evidence changed during verification')
        return {'candidateReceipt': candidate_pin, 'source': source, 'producer': spec['controller'],
            'producerKind': 'codex-workflow', 'status': 'source-bound-flow-candidate',
            'verification': 'Fixed installed run-codex --verify; native receipts, no API receipt substitution'}
    if 'producerKind' in spec:
        from flow_repair_candidate import verify_repair_producer
        bridge = {'producerKind': spec['producerKind'], 'repairConfig': spec['repairConfig'],
            'producerController': spec['controller'], 'workflowConfig': spec['config'],
            'python': spec['python'], 'producerVerificationTimeoutSeconds': timeout}
        result = verify_repair_producer(bridge, config, state)
        gh.require(result.get('source') == source and result.get('candidate') == target
                   and result.get('candidateReceipt') == candidate_pin,
                   'Repair verifier did not confirm the publication source')
        return {'candidateReceipt': candidate_pin, 'source': source, 'producer': spec['controller'],
                'producerKind': spec['producerKind'], 'repairConfig': spec['repairConfig'],
                'status': 'source-bound-flow-candidate',
                'verification': 'fixed installed repair-candidate --verify; no authority granted'}
    command = [spec['python']['path'], '-IBS', str(producer / 'aim-flow.py'), 'work',
               '--config', spec['config']['path'], '--config-sha256', spec['config']['sha256'],
               '--state', str(state), '--verify']
    process = project_validation.verification_process(command, ROOT,
        {'PATH': os.defpath, 'HOME': str(ROOT), 'LC_ALL': 'C'}, metadata_limit=gh.MAX_METADATA, timeout_seconds=timeout)
    gh.require(process.returncode == 0 and not process.stderr, 'Pinned Flow producer refused candidate verification')
    result = gh.unique_json(process.stdout)
    gh.require(isinstance(result, dict) and result.get('format') == 'aim-flow-requirement-workflow'
               and result.get('status') == 'candidate-prepared' and result.get('candidateReceipt') == candidate_pin
               and result.get('source') == source and result.get('candidate') == target
               and result.get('publicationAuthorized') is False,
               'Producer verifier did not confirm the exact private candidate')
    gh.require(before == project_validation.prior_controller(producer, spec['controller']['manifestSha256'])
               and raw == gh.pinned_metadata(candidate_pin) and config_raw == gh.pinned_metadata(spec['config']),
               'Producer evidence changed during verification')
    executable_pin(spec['python'])
    return {'candidateReceipt': candidate_pin, 'source': source, 'producer': spec['controller'],
            'status': 'source-bound-flow-candidate', 'verification': 'fixed installed work --verify; no authority granted'}


def payload(c):
    return {'title': c['pull']['title'], 'body': gh.pinned_metadata(c['pull']['body']).decode('utf-8'),
            'head': c['branch'], 'base': c['baseRef'], 'draft': True, 'maintainer_can_modify': False}


def prepare(config_path, config_sha, state):
    config_path, state = local.canonical_path(config_path), local.canonical_path(state)
    raw = gh.pinned_metadata({'path': str(config_path), 'sha256': config_sha})
    c = gh.unique_json(raw)
    gh.keys(c, ('format', 'schemaVersion', 'manifestSha256', 'repository', 'source', 'branch', 'baseRef',
                'pull', 'evidence', 'transport', 'candidateVerification'), 'publication declaration')
    gh.require(c['format'] == 'aim-flow-github-publication' and type(c['schemaVersion']) is int
               and c['schemaVersion'] == 1, 'Unsupported publication declaration')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    gh.keys(c['repository'], ('name', 'id'), 'repository identity')
    gh.require(gh.pattern(c['repository']['name'], '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
               and all(part not in ('.', '..') for part in c['repository']['name'].split('/'))
               and gh.positive(c['repository']['id']), 'Invalid repository identity')
    branch(c['branch']); branch(c['baseRef'])
    gh.require(c['branch'] != c['baseRef'], 'Publication branch cannot be the base branch')
    gh.keys(c['pull'], ('title', 'body'), 'exact draft payload')
    title = c['pull']['title']
    gh.require(isinstance(title, str) and 1 <= len(title) <= 256 and all(ord(v) >= 32 for v in title),
               'Invalid draft PR title')
    body = gh.pinned_metadata(c['pull']['body']).decode('utf-8')
    gh.require(0 < len(body.encode()) <= 65536 and all(ord(v) >= 32 or v in '\n\r\t' for v in body),
               'Draft PR body exceeds the platform bound or contains control bytes')
    gh.keys(c['evidence'], ('candidate', 'aggregate', 'browser', 'independentReview'), 'publication evidence')
    for pin in c['evidence'].values():
        gh.unique_json(gh.pinned_metadata(pin))
    gh.keys(c['transport'], ('gh', 'home', 'configDirectory', 'timeoutSeconds'), 'publication transport')
    tr = c['transport']
    local.pinned(tr['gh'])
    gh.require(os.access(tr['gh']['path'], os.X_OK), 'Pinned gh is not executable')
    for key in ('home', 'configDirectory'):
        gh.require(local.canonical_path(tr[key]).is_dir(), 'Declared authentication directory missing')
    gh.require(type(tr['timeoutSeconds']) is int and 1 <= tr['timeoutSeconds'] <= 300,
               'Publication deadline must be 1..300 seconds')
    reader = source_binding(c)
    producer = verify_producer(c)
    protected = [config_path, ROOT, Path(c['source']['checkout']), Path(c['source']['git']['path']),
                 Path(tr['gh']['path']), Path(tr['home']), Path(tr['configDirectory']),
                 Path(c['pull']['body']['path']), *(Path(pin['path']) for pin in c['evidence'].values()),
                 Path(c['candidateVerification']['controller']['path']), Path(c['candidateVerification']['state']),
                 Path(c['candidateVerification']['python']['path']), Path(c['candidateVerification']['config']['path'])]
    if c['candidateVerification'].get('producerKind') == 'codex-workflow':
        native_root = local.canonical_path(c['candidateVerification']['runState'])
        gh.require(not state.is_relative_to(native_root), 'Publication state overlaps native Codex run')
        native_config = gh.unique_json(gh.pinned_metadata(c['candidateVerification']['config']))
        gh.require(not state.is_relative_to(local.canonical_path(native_config['dispatchDirectory'])),
                   'Publication state overlaps native Codex dispatch ledger')
    gh.require(not any(p.is_relative_to(state) for p in protected)
               and not state.is_relative_to(ROOT) and not state.is_relative_to(reader.checkout),
               'Publication state overlaps protected inputs')
    intent = {'format': 'aim-flow-github-publication-intent', 'schemaVersion': 1,
              'declarationSha256': config_sha, 'declaration': c, 'state': str(state), 'controller': str(ROOT),
              'body': body, 'boundary': BOUNDARY, 'producerVerification': producer,
              'effects': ['create new branch with exact empty expected-ref lease', 'create new draft PR with exact payload'],
              'branchRef': 'refs/heads/' + c['branch'], 'expectedPreviousRef': '', 'draftPayload': payload(c)}
    return raw, c, intent, reader


class PublicationTransport(gh.GhTransport):
    def validate_request(self, method, route, body):
        c = self.config
        repo = '/repos/' + c['repository']['name']
        if method == 'POST':
            gh.require(route == repo + '/pulls' and body == payload(c), 'Unsupported draft creation request')
            return
        gh.require(method == 'GET' and body is None and isinstance(route, str) and route.startswith(repo),
                   'Publication supports only exact reads and new draft creation')
        suffix = route[len(repo):]
        fixed = ('', '/git/ref/heads/' + quote(c['baseRef'], safe=''),
                 '/git/matching-refs/heads/' + quote(c['branch'], safe=''))
        query = urlencode({'state': 'all', 'head': c['repository']['name'].split('/')[0] + ':' + c['branch'],
                           'base': c['baseRef'], 'per_page': 100})
        gh.require(suffix in fixed or gh.pattern(suffix, re.escape('/pulls?' + query) + '&page=[1-9][0-9]*')
                   or gh.pattern(suffix, '/pulls/[1-9][0-9]*'), 'Unsupported publication route')


class PublicationSession(gh.Session):
    def base(self):
        value = self.request('GET', '/git/ref/heads/' + quote(self.c['baseRef'], safe=''))
        gh.require(isinstance(value, dict) and value.get('ref') == 'refs/heads/' + self.c['baseRef']
                   and isinstance(value.get('object'), dict) and value['object'].get('type') == 'commit'
                   and value['object'].get('sha') == self.c['source']['baseSha'], 'Remote base changed')

    def branch_sha(self):
        values = self.request('GET', '/git/matching-refs/heads/' + quote(self.c['branch'], safe=''))
        gh.require(isinstance(values, list), 'Invalid matching-refs response')
        exact = []
        for value in values:
            gh.require(isinstance(value, dict) and isinstance(value.get('ref'), str)
                       and value['ref'].startswith('refs/heads/' + self.c['branch'])
                       and isinstance(value.get('object'), dict) and value['object'].get('type') == 'commit'
                       and gh.pattern(value['object'].get('sha'), '[a-f0-9]{40}'), 'Invalid branch reference')
            if value['ref'] == 'refs/heads/' + self.c['branch']:
                exact.append(value['object']['sha'])
        gh.require(len(exact) <= 1, 'Duplicate publication branch identity')
        return exact[0] if exact else None

    def pulls(self):
        found, page = [], 1
        while True:
            suffix = '/pulls?' + urlencode({'state': 'all', 'head': self.c['repository']['name'].split('/')[0] + ':' + self.c['branch'],
                                           'base': self.c['baseRef'], 'per_page': 100, 'page': page})
            batch = self.request('GET', suffix)
            gh.require(isinstance(batch, list) and len(batch) <= 100 and all(isinstance(v, dict) for v in batch),
                       'Invalid paginated PR response')
            found.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        gh.require(len({v.get('number') for v in found}) == len(found)
                   and all(gh.positive(v.get('number')) for v in found), 'Duplicate or invalid PR identity')
        return found

    def verify_pull(self, value, number):
        c, expected = self.c, payload(self.c)
        gh.require(isinstance(value, dict) and value.get('number') == number and gh.positive(number) and value.get('state') == 'open'
                   and value.get('draft') is True and value.get('merged') is False
                   and value.get('maintainer_can_modify') is False
                   and value.get('title') == expected['title'] and value.get('body') == expected['body'],
                   'Remote draft PR differs from exact declared payload/state')
        for side, ref, sha in (('head', c['branch'], c['source']['headSha']), ('base', c['baseRef'], c['source']['baseSha'])):
            data = value.get(side)
            gh.require(isinstance(data, dict) and data.get('ref') == ref and data.get('sha') == sha,
                       'Remote PR head/base identity changed')
            self.repo_identity(data.get('repo'))
        gh.require(value.get('html_url') == 'https://github.com/' + c['repository']['name'] + '/pull/' + str(value['number']),
                   'Remote PR link differs from identity')
        return value

    def preflight(self, *, must_be_absent):
        self.repository(); self.base()
        existing = self.branch_sha()
        gh.require(existing is None if must_be_absent else existing == self.c['source']['headSha'],
                   'Publication branch already exists' if must_be_absent else 'Publication branch differs or is absent')
        gh.require(not self.pulls(), 'A PR already exists; no update or replacement is allowed')
        return existing


def private_git_view(c, state, reader):
    common = local.canonical_path(reader.read('rev-parse', '--path-format=absolute', '--git-common-dir').decode().rstrip('\n'))
    objects = common / 'objects'
    gh.require(objects.is_dir() and '\n' not in str(objects) and '\r' not in str(objects), 'Unsafe source object directory')
    view = state / 'push.git'
    with new_owned_directory(view):
        for name in ('objects/info', 'objects/pack', 'refs/heads', 'refs/tags'):
            owned_mkdir(view, name)
        local.durable(view, 'HEAD', b'ref: refs/heads/unborn\n')
        local.durable(view, 'config', b'[core]\nrepositoryformatversion = 0\nbare = true\n')
        local.durable(view / 'objects/info', 'alternates', (str(objects) + '\n').encode())
    local.fsync_directory(state)
    return view


def push_command(c, view):
    source, tr = c['source'], c['transport']
    ref = 'refs/heads/' + c['branch']
    # The fixed helper is Git's normal gh credential protocol, never a declaration-supplied command.
    helper = '!' + shlex.quote(tr['gh']['path']) + ' auth git-credential'
    argv = [source['git']['path'], '--no-replace-objects', '--git-dir=' + str(view),
            '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'gc.auto=0',
            '-c', 'maintenance.auto=false', '-c', 'credential.helper=', '-c', 'credential.helper=' + helper,
            '-c', 'credential.useHttpPath=true', '-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false',
            '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
            'push', '--porcelain', '--atomic', '--no-verify', '--no-follow-tags', '--recurse-submodules=no',
            '--no-signed', '--force-with-lease=' + ref + ':', '--',
            'https://github.com/' + c['repository']['name'] + '.git', source['headSha'] + ':' + ref]
    env = {'PATH': os.defpath, 'HOME': tr['home'], 'GH_CONFIG_DIR': tr['configDirectory'], 'GH_HOST': 'github.com',
           'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'GH_NO_EXTENSION_UPDATE_NOTIFIER': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
           'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1',
           'GIT_GRAFT_FILE': os.devnull, 'GIT_ALLOW_PROTOCOL': 'https', 'LC_ALL': 'C'}
    return argv, env


def push(c, state, view):
    executable_pin(c['source']['git']); local.pinned(c['transport']['gh'])
    argv, env = push_command(c, view)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=state, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        failure = None
        try:
            stop = time.monotonic() + c['transport']['timeoutSeconds']
            while process.poll() is None:
                if time.monotonic() >= stop or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > gh.MAX_METADATA:
                    failure = 'Push deadline or metadata bound reached'
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
                    break
                time.sleep(0.02)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
        out.seek(0); err.seek(0)
        record = {'argv': argv, 'exitCode': process.returncode, 'stdout': out.read(gh.MAX_METADATA).decode('utf-8', 'replace'),
                  'stderr': err.read(gh.MAX_METADATA).decode('utf-8', 'replace'), 'failure': failure,
                  'outputTruncated': max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > gh.MAX_METADATA}
        if failure or record['outputTruncated'] or process.returncode:
            raise gh.GitHubError('Create-only Git push failed; no retry', record)
        expected = '*\t' + c['source']['headSha'] + ':refs/heads/' + c['branch'] + '\t[new branch]'
        if [line for line in record['stdout'].splitlines() if '\t' in line] != [expected]:
            raise gh.GitHubError('Push did not report exactly one new branch; no PR will be created', record)
        return record


def validate_effect_records(c, state):
    if (state / 'push-dispatch.json').exists():
        argv, _ = push_command(c, state / 'push.git')
        expected = {'argv': argv, 'headSha': c['source']['headSha'], 'ref': 'refs/heads/' + c['branch'],
                    'expectedPreviousRef': '', 'replayPermitted': False}
        gh.require(gh.unique_json(read_owned(state, 'push-dispatch.json')) == expected, 'Stored push intent changed')
    if (state / 'draft-dispatch.json').exists():
        stored = gh.unique_json(read_owned(state, 'draft-dispatch.json'))
        gh.require(stored.get('method') == 'POST' and stored.get('route') == '/repos/' + c['repository']['name'] + '/pulls'
                   and stored.get('body') == payload(c) and stored.get('replayPermitted') is False,
                   'Stored draft intent changed')


def continuation(intent):
    return {'format': 'aim-flow-create-draft-continuation', 'intentSha256': local.digest(local.encoded(intent)),
            'state': intent['state'], 'branchRef': intent['branchRef'], 'headSha': intent['declaration']['source']['headSha'],
            'draftPayload': intent['draftPayload'], 'effects': ['create new draft only; never push again']}


def receipt(state, c, intent, session, value):
    evidence = {p.name: local.digest(local.file_bytes(p)) for p in state.iterdir()
                if p.is_file() and p.name != 'lock' and not p.name.startswith('result-')}
    result = {'format': 'aim-flow-github-publication-result', 'schemaVersion': 1, 'observedAt': gh.stamp(),
              'intentSha256': local.digest(local.encoded(intent)), 'declarationSha256': intent['declarationSha256'],
              'repository': c['repository'], 'source': c['source'], 'branch': c['branch'], 'baseRef': c['baseRef'],
              'evidence': c['evidence'], 'evidenceFiles': evidence, 'boundary': BOUNDARY,
              'pushResponseConfirmed': (state / 'push-confirmed.json').exists(),
              'draftResponseConfirmed': (state / 'draft-confirmed.json').exists(),
              'causalAttribution': 'Unknown responses reconciled to remote state do not prove who created the branch or PR',
              'mergeExecuted': False, 'releaseExecuted': False, 'replayPermitted': False,
              'validationAuthenticated': False, 'acceptanceEstablished': False, **value}
    local.durable(state, 'result-%06d.json' % (len(list(state.glob('result-*.json'))) + 1), local.encoded(result))
    return result


def run(config_path, config_sha, state, authorization=None, *, resume=False, draft_authorization=None,
        transport=None, push_transport=None):
    raw, c, intent, reader = prepare(config_path, config_sha, state)
    state = local.canonical_path(state)
    intent_sha = local.digest(local.encoded(intent))
    gh.require(sum((authorization is not None, resume, draft_authorization is not None)) <= 1, 'Choose one publication mode')
    if authorization is None and not resume and draft_authorization is None:
        return {'status': 'preview', 'intentSha256': intent_sha, 'intent': intent, 'externalReads': False, 'externalEffects': False}
    if authorization is not None:
        gh.require(authorization == intent_sha, 'Exact publication preview authorization required')
        with new_owned_directory(state):
            local.durable(state, 'intent.json', local.encoded(intent)); local.durable(state, 'declaration.json', raw)
            local.durable(state, 'lock', b'')
    with os.fdopen(owned_file(state, 'lock', os.O_RDWR), 'rb+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gh.require(read_owned(state, 'intent.json', gh.MAX_METADATA) == local.encoded(intent)
                   and read_owned(state, 'declaration.json', gh.MAX_METADATA) == raw, 'Stored publication inputs changed')
        session = PublicationSession(c, transport if transport is not None else PublicationTransport(c), state)
        phase = 'preflight'
        try:
            validate_effect_records(c, state)
            if resume:
                session.repository(); session.base()
                remote = session.branch_sha()
                gh.require(remote in (None, c['source']['headSha']), 'Remote branch differs from candidate')
                matches = session.pulls()
                gh.require(len(matches) <= 1, 'Multiple PRs require independent review')
                if remote is None:
                    gh.require(not matches, 'PR exists but source branch is absent')
                    return receipt(state, c, intent, session, {'status': 'branch-not-observed-no-retry'})
                gh.require((state / 'push-dispatch.json').exists(), 'No retained publication push intent')
                if not matches:
                    result = {'status': 'branch-observed-no-pr-no-retry'}
                    if not (state / 'draft-dispatch.json').exists():
                        result.update(draftContinuation=continuation(intent),
                                      draftContinuationSha256=local.digest(local.encoded(continuation(intent))))
                    return receipt(state, c, intent, session, result)
                gh.require((state / 'draft-dispatch.json').exists(), 'PR not reserved by this publication attempt')
                pr = session.verify_pull(session.request('GET', '/pulls/' + str(matches[0]['number'])), matches[0]['number'])
            else:
                if draft_authorization is not None:
                    gh.require(draft_authorization == local.digest(local.encoded(continuation(intent))),
                               'Exact create-draft continuation authorization required')
                    gh.require((state / 'push-dispatch.json').exists() and not (state / 'draft-dispatch.json').exists(),
                               'Draft continuation cannot repeat any prior draft request')
                    session.preflight(must_be_absent=False)
                else:
                    session.preflight(must_be_absent=True)
                    view = private_git_view(c, state, reader)
                    gh.require(prepare(config_path, config_sha, state)[2] == intent, 'Inputs changed before push')
                    session.base()
                    gh.require(session.branch_sha() is None, 'Branch appeared before create-only push')
                    phase = 'push'
                    argv, _ = push_command(c, view)
                    local.durable(state, 'push-dispatch.json', local.encoded({'argv': argv, 'headSha': c['source']['headSha'],
                                  'ref': 'refs/heads/' + c['branch'], 'expectedPreviousRef': '', 'replayPermitted': False}))
                    try:
                        pushed = (push_transport or push)(c, state, view)
                        session.retain({'method': 'GIT-PUSH', 'at': gh.stamp(), 'response': pushed})
                    except Exception as error:
                        session.retain({'method': 'GIT-PUSH', 'at': gh.stamp(), 'error': {'class': type(error).__name__,
                                        'message': str(error), 'externalObservation': getattr(error, 'observation', None)}})
                        raise
                    local.durable(state, 'push-confirmed.json', local.encoded(pushed))
                    phase = 'preflight'
                    session.preflight(must_be_absent=False)
                gh.require(prepare(config_path, config_sha, state)[2] == intent, 'Inputs changed before draft creation')
                session.base()
                gh.require(session.branch_sha() == c['source']['headSha'], 'Remote head changed before draft creation')
                phase = 'draft'
                created = session.request('POST', '/pulls', payload(c), effect='draft')
                gh.require(isinstance(created, dict) and gh.positive(created.get('number')), 'Draft creation response lacks PR identity')
                local.durable(state, 'draft-confirmed.json', local.encoded(created))
                phase = 'reconcile'
                pr = session.verify_pull(session.request('GET', '/pulls/' + str(created['number'])), created['number'])
            if (state / 'draft-confirmed.json').exists():
                confirmed = gh.unique_json(read_owned(state, 'draft-confirmed.json', gh.MAX_METADATA))
                gh.require(confirmed.get('number') == pr['number'], 'Confirmed draft differs from reconciled PR')
            session.base()
            gh.require(session.branch_sha() == c['source']['headSha'], 'Remote head changed after draft creation')
            gh.require(prepare(config_path, config_sha, state)[2] == intent, 'Inputs changed during publication')
            return receipt(state, c, intent, session, {'status': 'draft-pr-verified', 'pullNumber': pr['number'],
                'pullUrl': pr['html_url'], 'pull': pr,
                'deliveryPull': {'number': pr['number'], 'headSha': c['source']['headSha'], 'headRef': c['branch'],
                                 'baseSha': c['source']['baseSha'], 'baseRef': c['baseRef'], 'markDraftReady': True}})
        except Exception as error:
            return receipt(state, c, intent, session, {'status': 'effect-outcome-unknown' if phase in ('push', 'draft') else 'stopped-not-ready',
                'phase': phase, 'error': {'class': type(error).__name__, 'message': str(error)},
                'nextStep': 'Read-only --resume; never repeat push or an already reserved draft request'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True); parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true'); mode.add_argument('--authorize-intent')
    mode.add_argument('--resume', action='store_true'); mode.add_argument('--authorize-draft-intent')
    args = parser.parse_args(argv)
    result = run(args.config, args.config_sha256, args.state, args.authorize_intent,
                 resume=args.resume, draft_authorization=args.authorize_draft_intent)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('preview', 'draft-pr-verified') else 1
