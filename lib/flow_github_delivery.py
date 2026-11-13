"""Pinned GitHub PR merge and exact main-push observation, with no release execution."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import quote, urlencode

import flow_api_delivery as local
from aim_ci import control_package
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file, read_owned

ROOT = Path(__file__).resolve().parents[1]
MAX_METADATA = 8 * 1024 * 1024
READY_MUTATION = ('mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id})'
                  '{pullRequest{id isDraft}}}')
BOUNDARY = ('Local evidence hashes bind bytes, not authenticity or independent review. '
            'GitHub atomically guards the head only; base is checked immediately before '
            'merge and in the resulting merge parents. A concurrent base change may cause '
            'a merge but will stop this handoff. No release, download, rerun, force, admin '
            'override, automatic retry, rollback or model request is supported.')


def require(ok, message):
    if not ok:
        raise Refused(message)


def keys(value, expected, label):
    require(isinstance(value, dict) and set(value) == set(expected), 'Invalid ' + label)


def positive(value):
    return type(value) is int and value > 0


def pattern(value, expression):
    return isinstance(value, str) and re.fullmatch(expression, value) is not None


def unique_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refused('Nonfinite JSON')))


def pinned_metadata(pin):
    keys(pin, ('path', 'sha256'), 'metadata pin')
    path = local.canonical_path(pin['path'])
    raw = read_owned(path.parent, path.name, MAX_METADATA)
    require(pattern(pin['sha256'], '[a-f0-9]{64}') and local.digest(raw) == pin['sha256'],
            'Pinned metadata changed')
    return raw


def stamp():
    return datetime.now(timezone.utc).isoformat()


def validate_target(c):
    """Validate typed repository/PR/CI scope without state writes or external reads."""
    repo, pr, wf, tr = (c[key] for key in ('repository', 'pull', 'workflow', 'transport'))
    keys(repo, ('name', 'id'), 'repository identity')
    require(pattern(repo['name'], '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+') and
            all(part not in ('.', '..') for part in repo['name'].split('/')) and positive(repo['id']),
            'Invalid repository identity')
    keys(pr, ('number', 'headSha', 'headRef', 'baseSha', 'baseRef', 'markDraftReady'), 'pull identity')
    require(positive(pr['number']) and type(pr['markDraftReady']) is bool, 'Invalid pull declaration')
    for name in ('headSha', 'baseSha'):
        require(pattern(pr[name], '[a-f0-9]{40}'), 'Invalid exact commit')
    for name in ('headRef', 'baseRef'):
        value = pr[name]
        require(pattern(value, '[A-Za-z0-9_][A-Za-z0-9_./-]*') and '..' not in value
                and '//' not in value and not value.endswith(('/', '.', '.lock')), 'Invalid branch')
    require(pr['headSha'] != pr['baseSha'], 'Head and base must differ')
    keys(wf, ('path', 'sha256', 'requiredJobs', 'artifact'), 'workflow')
    require(pattern(wf['path'], r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml')
            and pattern(wf['sha256'], '[a-f0-9]{64}'), 'Invalid pinned workflow')
    require(isinstance(wf['requiredJobs'], list) and wf['requiredJobs'], 'Required main-push jobs missing')
    for job in wf['requiredJobs']:
        keys(job, ('key', 'name'), 'required job')
        require(pattern(job['key'], '[A-Za-z_][A-Za-z0-9_-]*') and
                isinstance(job['name'], str) and 0 < len(job['name']) <= 256 and
                all(ord(ch) >= 32 for ch in job['name']), 'Invalid required job')
    for field in ('name', 'key'):
        require(len({j[field] for j in wf['requiredJobs']}) == len(wf['requiredJobs']), 'Ambiguous job mapping')
    keys(wf['artifact'], ('nameTemplate', 'retentionDays'), 'artifact contract')
    template = wf['artifact']['nameTemplate']
    require(isinstance(template, str) and template.count('{sha}') == template.count('{attempt}') == 1
            and pattern(template.replace('{sha}', 'a').replace('{attempt}', '1'), '[A-Za-z0-9_.-]{1,240}')
            and positive(wf['artifact']['retentionDays']), 'Invalid artifact contract')
    checks, statuses = c['requiredPrChecks'], c['requiredPrStatuses']
    require(isinstance(checks, list) and isinstance(statuses, list) and (checks or statuses),
            'Explicit required PR checks or statuses missing')
    for check in checks:
        keys(check, ('name', 'appId'), 'required PR check')
        require(isinstance(check['name'], str) and 0 < len(check['name']) <= 256
                and positive(check['appId']), 'Invalid required PR check')
    require(len({(v['name'], v['appId']) for v in checks}) == len(checks), 'Duplicate PR check')
    require(all(isinstance(v, str) and 0 < len(v) <= 256 for v in statuses)
            and len(set(statuses)) == len(statuses), 'Invalid PR status contexts')
    return repo, pr, wf, tr


def prepare(config_path, config_sha, state):
    path, state = local.canonical_path(config_path), local.canonical_path(state)
    raw = pinned_metadata({'path': str(path), 'sha256': config_sha})
    c = unique_json(raw)
    keys(c, ('format', 'schemaVersion', 'manifestSha256', 'repository', 'pull', 'workflow',
             'requiredPrChecks', 'requiredPrStatuses', 'evidence', 'transport'), 'GitHub declaration')
    require(c['format'] == 'aim-flow-github-delivery' and type(c['schemaVersion']) is int
            and c['schemaVersion'] == 1, 'Unsupported GitHub declaration')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    repo, pr, wf, tr = validate_target(c)
    keys(c['evidence'], ('aggregate', 'browser', 'independentReview'), 'prerequisite evidence')
    protected = [path, ROOT]
    for pin in c['evidence'].values():
        # Deliberately no claim that parsing assertions proves a genuine run or review.
        unique_json(pinned_metadata(pin))
        protected.append(Path(pin['path']))
    keys(tr, ('gh', 'home', 'configDirectory', 'timeoutSeconds'), 'gh transport')
    local.pinned(tr['gh'])
    require(os.access(tr['gh']['path'], os.X_OK), 'Pinned gh is not executable')
    protected.append(Path(tr['gh']['path']))
    for field in ('home', 'configDirectory'):
        directory = local.canonical_path(tr[field])
        require(directory.is_dir(), 'Declared gh auth directory is missing')
        protected.append(directory)
    require(type(tr['timeoutSeconds']) is int and 1 <= tr['timeoutSeconds'] <= 300,
            'Metadata observation deadline must be 1..300 seconds')
    for item in protected:
        require(not item.is_relative_to(state), 'State would contain a protected input')
    require(not state.is_relative_to(ROOT), 'State must be outside installed controls')
    intent = {'format': 'aim-flow-github-intent', 'schemaVersion': 1,
              'declarationSha256': config_sha, 'declaration': c, 'state': str(state),
              'controller': str(ROOT), 'boundary': BOUNDARY,
              'effects': (['mark specified draft ready for review'] if pr['markDraftReady'] else [])
                         + ['merge specified pull using merge commit and expected head',
                            'read exact merged-main push CI and artifact metadata'],
              'mergeMethod': 'merge', 'authentication': 'existing gh auth in declared HOME/config; no token input'}
    return raw, c, intent


class GitHubError(RuntimeError):
    def __init__(self, message, observation):
        super().__init__(message)
        self.observation = observation


class GhTransport:
    """Only the adapter constructs routes; no command/endpoint supplied by a CLI caller."""
    def __init__(self, config):
        self.spec, self.config = config['transport'], config
        self.deadline = time.monotonic() + self.spec['timeoutSeconds']

    def validate_request(self, method, route, body):
        c = self.config
        repo, pr, wf = '/repos/' + c['repository']['name'], c['pull'], c['workflow']
        if method == 'PUT':
            require(route == repo + '/pulls/' + str(pr['number']) + '/merge'
                    and body == {'sha': pr['headSha'], 'merge_method': 'merge'}, 'Unsupported merge request')
        elif method == 'POST':
            require(route == 'graphql' and isinstance(body, dict) and set(body) == {'query', 'variables'}
                    and body['query'] == READY_MUTATION and isinstance(body['variables'], dict)
                    and set(body['variables']) == {'id'}
                    and pattern(body['variables']['id'], '[A-Za-z0-9_=-]{1,200}')
                    and pr['markDraftReady'] is True, 'Unsupported ready request')
        else:
            require(method == 'GET' and body is None and isinstance(route, str) and route.startswith(repo),
                    'Unsupported GitHub read')
            suffix = route[len(repo):]
            fixed = ('', '/pulls/' + str(pr['number']), '/git/ref/heads/' + quote(pr['baseRef'], safe=''),
                     '/actions/workflows/' + quote(wf['path'].split('/')[-1], safe=''))
            forms = (
                re.escape('/commits/' + pr['headSha'] + '/check-runs') + r'\?filter=latest&per_page=100&page=[1-9][0-9]*',
                re.escape('/commits/' + pr['headSha'] + '/status') + r'\?per_page=100&page=[1-9][0-9]*',
                r'/git/commits/[a-f0-9]{40}',
                re.escape('/contents/' + quote(wf['path'], safe='/')) + r'\?ref=[a-f0-9]{40}',
                r'/actions/workflows/[1-9][0-9]*/runs\?event=push&head_sha=[a-f0-9]{40}&branch='
                + re.escape(urlencode({'branch': pr['baseRef']}).split('=', 1)[1]) + r'&per_page=100&page=[1-9][0-9]*',
                r'/actions/runs/[1-9][0-9]*',
                r'/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*/jobs\?per_page=100&page=[1-9][0-9]*',
                r'/actions/runs/[1-9][0-9]*/artifacts\?per_page=100&page=[1-9][0-9]*')
            require(suffix in fixed or any(pattern(suffix, form) for form in forms), 'Unsupported GitHub route')
    def __call__(self, method, route, body=None):
        self.validate_request(method, route, body)
        local.pinned(self.spec['gh'])
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'GitHub metadata observation deadline reached')
        argv = [self.spec['gh']['path'], 'api', '--hostname', 'github.com', '--method', method,
                route, '-H', 'Accept: application/vnd.github+json',
                '-H', 'X-GitHub-Api-Version: 2022-11-28']
        if body is not None:
            argv += ['--input', '-']
        env = {'PATH': os.defpath, 'HOME': self.spec['home'], 'GH_CONFIG_DIR': self.spec['configDirectory'],
               'GH_HOST': 'github.com', 'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1',
               'GH_NO_EXTENSION_UPDATE_NOTIFIER': '1', 'GH_PAGER': 'cat', 'PAGER': 'cat', 'NO_COLOR': '1',
               'LC_ALL': 'C'}
        # Files, rather than communicate's unbounded buffers, bound each metadata response.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(argv, cwd=self.spec['home'], env=env, stdin=subprocess.PIPE,
                                       stdout=out, stderr=err, start_new_session=True)
            try:
                process.stdin.write(local.encoded(body) if body is not None else b'')
                process.stdin.close()
                stop = time.monotonic() + min(60, remaining)
                while process.poll() is None:
                    if time.monotonic() >= stop or max(os.fstat(out.fileno()).st_size,
                                                      os.fstat(err.fileno()).st_size) > MAX_METADATA:
                        raise TimeoutError('GitHub response deadline or metadata size bound reached')
                    time.sleep(0.02)
                require(max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) <= MAX_METADATA,
                        'GitHub metadata response exceeded size bound')
                out.seek(0); err.seek(0)
                stdout, stderr = out.read(MAX_METADATA), err.read(MAX_METADATA)
                observation = {'exitCode': process.returncode, 'stdout': stdout.decode('utf-8', 'replace'),
                               'stderr': stderr.decode('utf-8', 'replace')}
                if process.returncode:
                    raise GitHubError('Official gh API request failed; no automatic retry', observation)
                try:
                    return unique_json(stdout)
                except (ValueError, UnicodeError) as error:
                    raise GitHubError('Official gh API returned invalid JSON', observation) from error
            except (OSError, TimeoutError, Refused) as error:
                if process.poll() is None:
                    import signal
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                out.seek(0); err.seek(0)
                observation = {'exitCode': process.returncode, 'transportFailure': type(error).__name__,
                               'stdout': out.read(MAX_METADATA).decode('utf-8', 'replace'),
                               'stderr': err.read(MAX_METADATA).decode('utf-8', 'replace'),
                               'outputTruncated': max(os.fstat(out.fileno()).st_size,
                                                      os.fstat(err.fileno()).st_size) > MAX_METADATA}
                raise GitHubError(str(error), observation) from error
            finally:
                if process.poll() is None:
                    # gh can start its normal auth helper; stop the owned process group too.
                    import signal
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


class Session:
    def __init__(self, config, transport, state=None):
        self.c, self.transport, self.state = config, transport, state
        self.repo = '/repos/' + config['repository']['name']
        self.observations = []
        self.deadline = time.monotonic() + config['transport']['timeoutSeconds']

    def request(self, method, suffix, body=None, *, effect=None):
        require(time.monotonic() < self.deadline, 'GitHub metadata observation deadline reached')
        route = 'graphql' if suffix == 'graphql' else self.repo + suffix
        if effect:
            require(self.state is not None, 'Effects require durable state')
            local.durable(self.state, effect + '-dispatch.json', local.encoded({
                'status': 'dispatch-reserved-outcome-unknown', 'method': method, 'route': route,
                'body': body, 'at': stamp(), 'replayPermitted': False}))
        record = {'method': method, 'route': route, 'body': body, 'at': stamp()}
        try:
            data = self.transport(method, route, body)
            record['response'] = data
        except Exception as error:
            record['error'] = {'class': type(error).__name__, 'message': str(error)}
            if isinstance(error, GitHubError):
                record['error']['externalObservation'] = error.observation
            self.retain(record)
            raise
        self.retain(record)
        return data

    def retain(self, record):
        if self.state is not None:
            existing = [int(p.stem.split('-')[1]) for p in self.state.glob('observation-*.json')
                        if re.fullmatch(r'observation-[0-9]+\.json', p.name)]
            name = 'observation-%06d.json' % (max(existing, default=0) + 1)
            local.durable(self.state, name, local.encoded(record))
            self.observations.append({'file': name, 'sha256': local.digest(local.encoded(record))})
        else:
            self.observations.append(record)

    def pages(self, suffix, field, *, params=None, validate=None):
        items, total, page = [], None, 1
        while True:
            data = self.request('GET', suffix + '?' + urlencode({**(params or {}), 'per_page': 100, 'page': page}))
            require(isinstance(data, dict) and type(data.get('total_count')) is int
                    and data['total_count'] >= 0 and isinstance(data.get(field), list), 'Malformed paginated metadata')
            require(total is None or total == data['total_count'], 'Metadata changed during pagination')
            if validate is not None:
                validate(data)
            total = data['total_count']
            batch = data[field]
            require(len(batch) <= 100 and all(isinstance(v, dict) for v in batch), 'Invalid metadata page')
            items += batch
            require(len(items) <= total, 'Paginated metadata count differs')
            if len(items) == total:
                break
            require(batch, 'Incomplete paginated metadata')
            page += 1
        identities = [v.get('id') for v in items]
        require(all(positive(v) for v in identities) and len(set(identities)) == len(items),
                'Missing or duplicate paginated identities')
        return items

    def repository(self):
        data = self.request('GET', '')
        self.repo_identity(data)
        return data

    def repo_identity(self, data):
        require(isinstance(data, dict) and data.get('id') == self.c['repository']['id']
                and data.get('full_name') == self.c['repository']['name'], 'Repository identity changed')

    def pull(self):
        pr = self.c['pull']
        data = self.request('GET', '/pulls/' + str(pr['number']))
        require(isinstance(data, dict) and data.get('number') == pr['number'], 'Pull identity changed')
        for side in ('head', 'base'):
            value = data.get(side)
            require(isinstance(value, dict) and value.get('ref') == pr[side + 'Ref'], 'Pull branch identity changed')
            self.repo_identity(value.get('repo'))
        require(data['head'].get('sha') == pr['headSha'], 'Pull head changed')
        return data

    def main_sha(self):
        ref = 'refs/heads/' + self.c['pull']['baseRef']
        data = self.request('GET', '/git/ref/heads/' + quote(self.c['pull']['baseRef'], safe=''))
        require(isinstance(data, dict) and data.get('ref') == ref and
                isinstance(data.get('object'), dict) and data['object'].get('type') == 'commit'
                and pattern(data['object'].get('sha'), '[a-f0-9]{40}'), 'Invalid remote main reference')
        return data['object']['sha']

    def pr_checks(self):
        head = self.c['pull']['headSha']
        checks = self.pages('/commits/' + head + '/check-runs', 'check_runs', params={'filter': 'latest'})
        indexed = {}
        for check in checks:
            app = check.get('app')
            require(isinstance(app, dict) and positive(app.get('id')) and isinstance(check.get('name'), str)
                    and check.get('head_sha') == head, 'Invalid check identity')
            identity = (check['name'], app['id'])
            require(identity not in indexed, 'Ambiguous latest PR checks')
            indexed[identity] = check
            require(check.get('status') == 'completed' and check.get('conclusion') == 'success',
                    'A current PR check is pending or not successful')
        require(all((v['name'], v['appId']) in indexed for v in self.c['requiredPrChecks']),
                'Required PR check missing')
        def status_identity(data):
            require(data.get('sha') == head, 'Combined PR status is for another commit')
            self.repo_identity(data.get('repository'))
        statuses = self.pages('/commits/' + head + '/status', 'statuses', validate=status_identity)
        contexts = [s.get('context') for s in statuses]
        require(all(isinstance(v, str) for v in contexts) and len(set(contexts)) == len(contexts),
                'Ambiguous latest PR statuses')
        require(all(s.get('state') == 'success' for s in statuses), 'A current PR status is not successful')
        require(set(self.c['requiredPrStatuses']) <= set(contexts), 'Required PR status missing')
        return {'checks': checks, 'statuses': statuses}

    def preflight(self, draft):
        self.repository()
        data = self.pull()
        require(data.get('state') == 'open' and data.get('merged') is False
                and data.get('draft') is draft and data['base'].get('sha') == self.c['pull']['baseSha'],
                'Pull state, draft or base differs from authorization')
        require(data.get('mergeable') is True, 'Pull mergeability is pending or blocked')
        if not draft:
            require(data.get('mergeable_state') == 'clean', 'GitHub does not report a clean mergeable pull')
        checks = self.pr_checks()
        require(self.main_sha() == self.c['pull']['baseSha'], 'Remote main changed before effect')
        # Last pull read narrows the check/branch race. The server guards expected head atomically.
        last = self.pull()
        require(last == data, 'Pull metadata changed during preflight; inspect again independently')
        return data, checks

    def reconcile(self):
        self.repository()
        pr = self.pull()
        if pr.get('merged') is not True:
            return None
        require(pr.get('state') == 'closed' and pattern(pr.get('merge_commit_sha'), '[a-f0-9]{40}'),
                'Invalid merged pull observation')
        sha = pr['merge_commit_sha']
        commit = self.request('GET', '/git/commits/' + sha)
        require(isinstance(commit, dict) and commit.get('sha') == sha
                and isinstance(commit.get('parents'), list)
                and [p.get('sha') for p in commit['parents']] ==
                [self.c['pull']['baseSha'], self.c['pull']['headSha']],
                'Merged commit parents differ from authorized base/head; release blocked')
        require(isinstance(commit.get('tree'), dict) and pattern(commit['tree'].get('sha'), '[a-f0-9]{40}'),
                'Merged commit tree identity missing or malformed')
        main = self.main_sha()
        require(main == sha, 'Remote main is not the exact merged commit; release blocked')
        return {'mergedSha': sha, 'mergedTree': commit['tree']['sha'], 'observedMainSha': main}

    def ci(self, merge):
        wf, sha = self.c['workflow'], merge['mergedSha']
        content = self.request('GET', '/contents/' + quote(wf['path'], safe='/') + '?' + urlencode({'ref': sha}))
        require(isinstance(content, dict) and content.get('type') == 'file' and content.get('path') == wf['path']
                and content.get('encoding') == 'base64' and isinstance(content.get('content'), str),
                'Missing merged workflow source')
        try:
            source = base64.b64decode(content['content'].replace('\n', ''), validate=True)
        except ValueError as error:
            raise Refused('Invalid workflow content encoding') from error
        require(local.digest(source) == wf['sha256'], 'Merged workflow differs from pinned project policy')
        workflow = self.request('GET', '/actions/workflows/' + quote(wf['path'].split('/')[-1], safe=''))
        require(isinstance(workflow, dict) and positive(workflow.get('id'))
                and workflow.get('path') == wf['path'] and workflow.get('state') == 'active', 'Workflow identity differs')
        runs = self.pages('/actions/workflows/' + str(workflow['id']) + '/runs', 'workflow_runs',
                          params={'event': 'push', 'head_sha': sha, 'branch': self.c['pull']['baseRef']})
        for run in runs:
            self.check_run(run, workflow['id'], sha)
        require(len(runs) <= 1, 'Multiple exact main-push runs require independent selection')
        if not runs:
            require(not (self.state / 'ci-selection.json').exists(), 'Previously selected run disappeared')
            return {'status': 'awaiting-main-push', **merge}
        selected = {'runId': runs[0]['id'], 'runAttempt': runs[0]['run_attempt']}
        selection = self.state / 'ci-selection.json'
        if selection.exists():
            require(unique_json(read_owned(self.state, selection.name)) == selected,
                    'Main-push run or attempt changed; no silent substitution')
        else:
            local.durable(self.state, selection.name, local.encoded(selected))
        run = self.request('GET', '/actions/runs/' + str(selected['runId']))
        self.check_run(run, workflow['id'], sha)
        require(run['id'] == selected['runId'] and run['run_attempt'] == selected['runAttempt'],
                'Selected run attempt changed')
        common = {**merge, **selected, 'workflowId': workflow['id'], 'workflowPath': wf['path']}
        jobs = self.pages('/actions/runs/%d/attempts/%d/jobs' % (selected['runId'], selected['runAttempt']), 'jobs')
        for job in jobs:
            require(job.get('run_id') == selected['runId'] and job.get('run_attempt') == selected['runAttempt']
                    and job.get('head_sha') == sha, 'Job belongs to another SHA/run/attempt')
        if run.get('status') != 'completed':
            return {'status': 'awaiting-main-push', **common, 'observedJobs': jobs}
        require(run.get('conclusion') == 'success', 'Exact main-push workflow did not succeed')
        required = []
        for declared in wf['requiredJobs']:
            matches = [j for j in jobs if j.get('name') == declared['name']]
            require(len(matches) == 1, 'Required main-push job missing or ambiguous')
            job = matches[0]
            require(job.get('status') == 'completed' and job.get('conclusion') == 'success',
                    'Required main-push job is incomplete or unsuccessful')
            required.append({'declaredKey': declared['key'], 'observed': job})
        artifacts = self.pages('/actions/runs/' + str(selected['runId']) + '/artifacts', 'artifacts')
        name = wf['artifact']['nameTemplate'].replace('{sha}', sha).replace('{attempt}', str(selected['runAttempt']))
        matches = [a for a in artifacts if a.get('name') == name]
        require(len(matches) == 1, 'Exact attempt artifact missing or ambiguous')
        artifact = matches[0]
        origin = artifact.get('workflow_run')
        require(isinstance(origin, dict) and origin.get('id') == selected['runId']
                and origin.get('head_sha') == sha and origin.get('head_branch') == self.c['pull']['baseRef']
                and origin.get('repository_id') == self.c['repository']['id']
                and origin.get('head_repository_id') == self.c['repository']['id'], 'Artifact workflow identity differs')
        require(artifact.get('expired') is False and positive(artifact.get('size_in_bytes')), 'Artifact is expired or empty')
        try:
            expires = datetime.fromisoformat(artifact['expires_at'].replace('Z', '+00:00'))
            created = datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00'))
            require(expires.tzinfo is not None and created.tzinfo is not None, 'Artifact dates need timezones')
            require(created <= datetime.now(timezone.utc) < expires, 'Artifact expired or dates are invalid')
            # Actions expiry has second precision; one-minute tolerance is not a policy bypass.
            require(abs((expires - created).total_seconds() - wf['artifact']['retentionDays'] * 86400) <= 60,
                    'Artifact retention differs from pinned project contract')
        except (KeyError, ValueError, TypeError) as error:
            raise Refused('Invalid artifact expiry metadata') from error
        require(artifact.get('digest') is None or pattern(artifact['digest'], r'sha256:[a-f0-9]{64}'),
                'Invalid artifact digest')
        # Reconcile again after pages; do not hand off stale main or a newer run attempt.
        require(self.reconcile() == merge, 'Merge identity changed during CI observation')
        last_run = self.request('GET', '/actions/runs/' + str(selected['runId']))
        self.check_run(last_run, workflow['id'], sha)
        require(last_run.get('id') == selected['runId'] and last_run.get('run_attempt') == selected['runAttempt']
                and last_run.get('status') == 'completed'
                and last_run.get('conclusion') == 'success', 'Run changed during CI observation')
        return {'status': 'main-push-verified', **common, 'requiredJobs': required, 'artifact': artifact,
                'workflowSha256': wf['sha256'], 'artifactBytesVerified': False}

    def check_run(self, run, workflow_id, sha):
        require(isinstance(run, dict) and positive(run.get('id')) and positive(run.get('run_attempt'))
                and run.get('workflow_id') == workflow_id and run.get('path') == self.c['workflow']['path']
                and run.get('event') == 'push' and run.get('head_sha') == sha
                and run.get('head_branch') == self.c['pull']['baseRef'], 'Not the exact main-push workflow/run')
        self.repo_identity(run.get('repository'))
        self.repo_identity(run.get('head_repository'))


def result_record(state, c, intent_sha, session, result):
    value = {'format': 'aim-flow-github-main-push', 'schemaVersion': 1,
             'intentSha256': intent_sha, 'declarationSha256': local.digest(read_owned(state, 'declaration.json', MAX_METADATA)),
             'repository': c['repository'], 'pullNumber': c['pull']['number'],
             'headSha': c['pull']['headSha'], 'baseSha': c['pull']['baseSha'],
             'evidence': c['evidence'], 'independentReviewAuthenticated': False,
             'releaseExecuted': False, 'replayPermitted': False, 'boundary': BOUNDARY,
             'mergeResponseConfirmed': (state / 'merge-confirmed.json').exists(),
             'mergeAttribution': ('successful API response retained' if (state / 'merge-confirmed.json').exists()
                                  else 'not authenticated; any reconciled remote state is not proof of who merged'),
             'observedAt': stamp(), **result,
             'observationFiles': session.observations,
             'evidenceFiles': {p.name: local.digest(local.file_bytes(p))
                 for p in state.iterdir() if p.name != 'lock' and not p.name.startswith('result-')}}
    number = len(list(state.glob('result-*.json'))) + 1
    local.durable(state, 'result-%06d.json' % number, local.encoded(value))
    return value


def run(config_path, config_sha, state, authorization=None, *, resume=False, transport=None):
    raw, c, intent = prepare(config_path, config_sha, state)
    state = local.canonical_path(state)
    intent_sha = local.digest(local.encoded(intent))
    if authorization is None and not resume:
        return {'status': 'preview', 'intentSha256': intent_sha, 'intent': intent,
                'externalReads': False, 'externalEffects': False}
    require(not resume or authorization is None, 'Resume is read-only, not an authorization')
    if not resume:
        require(authorization == intent_sha, 'Exact preview authorization required')
        with new_owned_directory(state):
            local.durable(state, 'intent.json', local.encoded(intent))
            local.durable(state, 'declaration.json', raw)
            local.durable(state, 'lock', b'')
    with os.fdopen(owned_file(state, 'lock', os.O_RDWR), 'rb+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(read_owned(state, 'intent.json', MAX_METADATA) == local.encoded(intent)
                and read_owned(state, 'declaration.json', MAX_METADATA) == raw, 'Stored intent/declaration changed')
        session = Session(c, transport if transport is not None else GhTransport(c), state)
        phase = 'preflight'
        try:
            if not resume:
                pr, checks = session.preflight(c['pull']['markDraftReady'])
                if c['pull']['markDraftReady']:
                    require(pattern(pr.get('node_id'), '[A-Za-z0-9_=-]{1,200}'), 'Invalid pull node identity')
                    require(prepare(config_path, config_sha, state)[2] == intent, 'Inputs changed before mark-ready')
                    phase = 'ready'
                    reply = session.request('POST', 'graphql', {'query': READY_MUTATION, 'variables': {'id': pr['node_id']}},
                                            effect='ready')
                    expected = {'data': {'markPullRequestReadyForReview': {'pullRequest': {'id': pr['node_id'], 'isDraft': False}}}}
                    require(reply == expected, 'Mark-ready response is not an exact success')
                phase = 'preflight'
                # Always repeat full checks immediately before merging, including after mark-ready.
                session.preflight(False)
                require(prepare(config_path, config_sha, state)[2] == intent, 'Inputs changed before merge')
                require(session.main_sha() == c['pull']['baseSha'], 'Remote base changed immediately before merge')
                phase = 'merge'
                merged = session.request('PUT', '/pulls/' + str(c['pull']['number']) + '/merge',
                                         {'sha': c['pull']['headSha'], 'merge_method': 'merge'}, effect='merge')
                require(isinstance(merged, dict) and merged.get('merged') is True
                        and pattern(merged.get('sha'), '[a-f0-9]{40}'), 'Merge response not an exact success')
                local.durable(state, 'merge-confirmed.json', local.encoded(merged))
            phase = 'reconcile'
            merge = session.reconcile()
            if merge is None:
                return result_record(state, c, intent_sha, session, {'status': 'merge-not-observed-no-retry'})
            # A reservation proves local intent/no-replay state, not who caused a later remote merge.
            require((state / 'merge-dispatch.json').exists(), 'Merge not dispatched by this Flow attempt')
            reservation = unique_json(read_owned(state, 'merge-dispatch.json'))
            require(reservation.get('body') == {'sha': c['pull']['headSha'], 'merge_method': 'merge'}
                    and reservation.get('route') == session.repo + '/pulls/' + str(c['pull']['number']) + '/merge',
                    'Merge reservation differs')
            if (state / 'merge-confirmed.json').exists():
                confirmation = unique_json(read_owned(state, 'merge-confirmed.json'))
                require(confirmation.get('merged') is True and confirmation.get('sha') == merge['mergedSha'],
                        'Merge response differs from reconciled pull')
            phase = 'ci'
            result = session.ci(merge)
            require(prepare(config_path, config_sha, state)[2] == intent, 'Inputs changed during observation')
            return result_record(state, c, intent_sha, session, result)
        except Exception as error:
            # After ANY error stop. In particular there is no automatic read after a denied or uncertain effect.
            return result_record(state, c, intent_sha, session, {
                'status': 'effect-outcome-unknown' if phase in ('ready', 'merge') else 'stopped-not-ready',
                'phase': phase, 'error': {'class': type(error).__name__, 'message': str(error)},
                'nextStep': 'Independent review; --resume can only reconcile and observe, never repeat an effect'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true', help='Local, no network, no state writes')
    mode.add_argument('--authorize-intent', help='Exact preview SHA256, one durable attempt only')
    mode.add_argument('--resume', action='store_true', help='Read-only reconciliation/CI observation, never merge again')
    args = parser.parse_args(argv)
    result = run(args.config, args.config_sha256, args.state, args.authorize_intent, resume=args.resume)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('preview', 'main-push-verified') else 1
