"""Synthetic external transports around the REAL worker, Git and product recipes.

This is an offline qualification destination. It neither contacts a model/GitHub
nor deploys to production. Its journals cannot replace the real API history.
"""
import copy
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from urllib.parse import parse_qs

from openai_budget import MODEL, Refused, atomic_json, fingerprint, safe_path, write_owned
from . import controller as c
from .artifacts import extract_verified
from .local_ci import run_product_ci
from .products import get_product


def git(root, *args):
    environment = {key:value for key,value in os.environ.items()
                   if not key.startswith(('OPENAI_', 'CODEX_', 'GH_TOKEN', 'GITHUB_TOKEN'))}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=root,
                            env=environment, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise Refused('Offline Git operation failed: ' + result.stderr[-1000:])
    return result.stdout.strip()


def tiny_edits(files, old, new):
    edits = []
    for path, text in files.items():
        while old in text:
            index = text.index(old)
            start = max(0, text.rfind('\n', 0, index))
            end = text.find('\n', index)
            end = len(text) if end < 0 else end + 1
            before = text[start:end]
            after = before.replace(old, new)
            if text.count(before) != 1:
                raise Refused('Synthetic model fixture needs unique source context')
            edits.append({'path': path, 'old': before, 'new': after})
            text = text.replace(before, after, 1)
    return {'summary': 'Synthetic deterministic proposal; no live model was called', 'edits': edits}


class SyntheticTransports:
    synthetic = True
    owner = 'fixture'
    destination = 'http://127.0.0.1:4173/'

    def __init__(self, workspace, name, *, reopen=False, checkout=None, head=None, browser=None):
        self.workspace = workspace
        self.product = get_product(name)
        self.repository = 'synthetic/' + name
        self.vendor = workspace / 'vendors' / name
        if not reopen: self.vendor.mkdir(parents=True)
        self.root = workspace / 'repositories' / name / 'source'
        if not reopen: self.root.mkdir(parents=True)
        self.origin = str(self.root.parent / 'remote.git')
        self.state = workspace / 'worker-state'
        self.legacy = workspace / 'synthetic-legacy.json'
        self.records = {'synthetic': True, 'issues': {}, 'prs': {}, 'ci': {}, 'deployments': {}, 'modelRequests': [], 'nextId': 1000}
        self.fault = None
        self.job = None
        self.config = None
        self.browser = browser
        if reopen:
            self.records = json.loads((self.vendor / 'external-records.json').read_text())
            if self.records.get('synthetic') is not True:
                raise Refused('Cannot reopen a non-synthetic transport journal')
            self.job = self.records['currentJob']
            self.fault = self.records.get('fault')
        else:
            self._seed(checkout, head)

    def save(self):
        if self.job:
            self.records['currentJob'] = self.job
        self.records['fault'] = self.fault
        atomic_json(self.vendor / 'external-records.json', self.records)

    def boundary(self, name):
        if self.fault != name:
            return
        marker = self.vendor / 'fault-fired.json'
        if marker.exists():
            return
        atomic_json(marker, {'synthetic': True, 'boundary': name})
        os._exit(77)  # Genuine process death; no exception/finally rescue.

    def _seed_fixture(self):
        source = c.ROOT / 'fixtures/products/catalog'
        if not source.is_dir():
            raise Refused('Synthetic catalog source is not bundled; provide an explicit reviewed checkout')
        shutil.copytree(source, self.root / self.product.directory,
                        ignore=shutil.ignore_patterns('node_modules', 'dist', '__pycache__', '*.pyc', 'test-results', 'playwright-report', '.env*', '.npmrc'))
        for name in ('lib', 'profiles', 'fixtures', 'tests/production'):
            shutil.copytree(c.ROOT / name, self.root / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('aim-ci.py', 'aim-ci-release.py'):
            shutil.copy2(c.ROOT / name, self.root / name)
        git(self.root, 'init', '-b', 'main')

    def _seed(self, checkout, head):
        if checkout is None:
            self._seed_fixture()
        else:
            git(self.root, 'clone', '--no-local', '--no-checkout', '--', str(checkout), '.')
            git(self.root, 'checkout', '-B', 'main', head)
            git(self.root, 'remote', 'remove', 'origin')
        git(self.root, 'config', 'user.name', 'AIM CI Offline Fixture')
        git(self.root, 'config', 'user.email', 'fixture@invalid')
        if checkout is None:
            git(self.root, 'add', '.')
            git(self.root, 'commit', '-m', 'Synthetic product seed')
        git(self.root.parent, 'init', '--bare', self.origin)
        git(self.root, 'remote', 'add', 'origin', self.origin)
        git(self.root, 'push', 'origin', 'main')
        self.save()

    def set_job(self, number, title, old, new, expected, *, edits=None):
        self.job = {'number': number, 'title': title, 'old': old, 'new': new, 'expected': expected}
        if edits is not None:
            self.job['edits'] = edits
        self.config = c.profile(self.product.name)
        self.records['issues'][str(number)] = self.issue()
        self.save()

    def issue(self):
        return {'number': self.job['number'], 'node_id': 'SYNTHETIC_' + str(self.job['number']),
                'title': self.job['title'],
                'body': '## Scope\n' + self.job['title'] + '\n## Acceptance criteria\n- [ ] ' + self.job['expected'],
                'user': {'login': self.owner}, 'labels': [{'name': 'aim-ci'}], 'state': 'open',
                'locked': False, 'updated_at': '2026-11-06T00:00:00Z',
                'html_url': 'synthetic://' + self.repository + '/issues/' + str(self.job['number'])}

    def transport(self, endpoint, payload):
        if endpoint == 'responses/input_tokens':
            return {'input_tokens': max(1, len(json.dumps(payload)) // 4)}
        if endpoint != 'responses':
            raise Refused('Unsupported synthetic model transport')
        phase = payload['text']['format']['name']
        data = json.loads(payload['input'])
        if phase == 'intake':
            if 'edits' in self.job:
                patch = {'summary': 'Synthetic proposal for the bound catalog heading assignment',
                         'edits': self.job['edits']}
            else:
                patch = tiny_edits(data['files'], self.job['old'], self.job['new'])
            result = {'triage': {'ready': True, 'reason': 'Synthetic small product change', 'risk': 'low',
                                 'paths': list(dict.fromkeys(edit['path'] for edit in patch['edits'])),
                                 'acceptance': [self.job['expected']]}, 'patch': patch}
        elif phase.startswith('review_'):
            result = {'summary': 'Synthetic separate review fixture, not a live model opinion', 'findings': []}
        else:
            raise Refused('Qualification fixture has no automatic extra model attempts')
        self.records['modelRequests'].append({'phase': phase, 'payloadSha256': fingerprint(payload),
                                              'issue': self.job['number']})
        self.save()
        self.boundary('after-model-dispatch:' + phase.replace('_', '-'))
        text = json.dumps(result)
        return {'id': 'synthetic_response_' + str(len(self.records['modelRequests'])), 'model': MODEL,
                'status': 'completed', 'usage': {'input_tokens': max(1, len(payload['input']) // 4),
                                                'output_tokens': max(1, len(text) // 4)},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}

    def next_id(self):
        self.records['nextId'] += 1
        return self.records['nextId']

    def ci(self, sha, event, branch='main'):
        key = sha + ':' + event
        if key in self.records['ci']:
            result = self.records['ci'][key]
            if not Path(result['artifact']).is_file():
                raise Refused('Original synthetic CI artifact missing; no automatic rebuild')
            return result
        identifier = self.next_id()
        directory = self.vendor / ('ci-' + str(identifier))
        directory.mkdir()
        git(self.root, 'fetch', 'origin')
        product_ci = run_product_ci(self.product, self.root, sha, directory)
        result = {'id': identifier, 'head_sha': sha, 'head_branch': branch,
                  'head_repository': {'full_name': self.repository}, 'event': event, 'name': 'AIM CI',
                  'path': '.github/workflows/aim-ci.yml', 'status': 'completed', 'conclusion': 'success',
                  'html_url': 'synthetic://' + self.repository + '/ci/' + str(identifier),
                  **product_ci,
                  'coverage': 'Real isolated product validation and build. Hosted SCM, audit and signing not run.'}
        self.records['ci'][key] = result; self.save()
        return result

    def deploy(self, sha):
        if sha in self.records['deployments']:
            return self.records['deployments'][sha]
        ci = self.ci(sha, 'push')
        archive = Path(ci['artifact'])
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ci['artifactSha256']:
            raise Refused('Retained synthetic artifact changed')
        self.boundary('before-deploy')
        identifier = self.next_id(); directory = self.vendor / ('promotion-' + str(identifier)); directory.mkdir()
        promoted = directory / 'site'
        release = extract_verified(archive, promoted, sha, ci['release']['product'], self.product.artifact_policy)
        handler = partial(SimpleHTTPRequestHandler, directory=str(promoted))
        server = ThreadingHTTPServer(('127.0.0.1', 4173), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        proof = directory / 'proof'
        try:
            environment = {'EXECUTION_MODE': 'synthetic-offline', 'PRODUCTION_URL': self.destination,
                           'SOURCE_SHA': sha, 'CONTENT_SHA256': ci['release']['contentSha256'], 'EVIDENCE_DIR': str(proof),
                           'EXPECTED_HEADING': self.job['new']}
            recipe = 'catalog-verify.mjs'
            if self.browser:
                node = self.browser['node']
                environment.update(PATH=os.defpath, HOME=os.environ['HOME'], TMPDIR=os.environ['TMPDIR'],
                                   AIM_CI_PLAYWRIGHT_ROOT=self.browser['modules'],
                                   AIM_CI_CHROMIUM=self.browser['chromium'],
                                   PLAYWRIGHT_BROWSERS_PATH=self.browser['browsers'])
            else:
                modules = os.environ.get('AIM_CI_PLAYWRIGHT_ROOT')
                chromium = os.environ.get('AIM_CI_CHROMIUM')
                node = shutil.which('node')
                if not modules or not chromium or not node:
                    raise Refused('Catalog browser qualification requires Node on PATH and explicit '
                                  'AIM_CI_PLAYWRIGHT_ROOT and AIM_CI_CHROMIUM; no runtime is bundled')
                environment.update(PATH=os.defpath, HOME=str(Path.home()),
                                   AIM_CI_PLAYWRIGHT_ROOT=modules, AIM_CI_CHROMIUM=chromium)
            completed = subprocess.run([node, str(c.ROOT / 'tests/production' / recipe)],
                                       env=environment, capture_output=True, text=True, timeout=180)
            (directory / 'browser.txt').write_text(completed.stdout + completed.stderr)
            if completed.returncode:
                raise Refused('Actual loopback browser proof failed; inspect ' + str(directory))
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        result = json.loads((proof / 'result.json').read_text())
        if (result.get('passed') is not True or result.get('executionMode') != 'synthetic-offline'
                or result.get('url') != self.destination or result.get('sourceSha') != sha
                or result.get('product') != release['product']
                or result.get('contentSha256') != ci['release']['contentSha256']
                or not set(self.product.required_checks).issubset(result.get('checks', []))):
            raise Refused('Offline destination did not pass the artifact-bound product probe')
        workflow = {'id': identifier, 'head_sha': sha, 'event': 'workflow_run', 'name': 'Synthetic local promotion',
                    'status': 'completed', 'conclusion': 'success',
                    'html_url': 'synthetic://' + self.repository + '/promotion/' + str(identifier),
                    'proof': str(proof), 'artifactSha256': ci['artifactSha256'], 'sourceCiRun': ci['id']}
        if self.browser:
            workflow['browserRuntime'] = self.browser
        self.records['deployments'][sha] = workflow; self.save()
        self.boundary('after-deploy')
        return workflow

    def download(self, argv):
        identifier = int(argv[3]); destination = Path(argv[-1])
        matches = [run for run in self.records['deployments'].values() if run['id'] == identifier]
        if len(matches) != 1: raise Refused('Synthetic proof run not found')
        shutil.copytree(matches[0]['proof'], destination)
        return ''

    def github(self, endpoint, method='GET', value=None):
        prefix = 'repos/' + self.repository + '/'
        if endpoint == 'user': return {'login': self.owner}
        if endpoint == 'graphql':
            number = str(value['variables']['number']); pr = self.records['prs'][number]
            return {'data': {'repository': {'pullRequest': {'headRefOid': pr['head']['sha'],
                     'reviewThreads': {'pageInfo': {'hasNextPage': False}, 'nodes': []}}}}}
        if not endpoint.startswith(prefix): raise Refused('Synthetic repository mismatch')
        path, _, query = endpoint[len(prefix):].partition('?'); parameters = parse_qs(query)
        if re.fullmatch(r'issues/\d+/comments', path) and method == 'GET': return []
        if path.startswith('issues/') and method == 'GET': return copy.deepcopy(self.records['issues'][path.split('/')[1]])
        if path == 'git/ref/heads/main': return {'object': {'sha': git(Path(self.origin), 'rev-parse', 'refs/heads/main')}}
        if path.startswith('git/commits/'):
            return {'parents': [{'sha': parent} for parent in git(self.root, 'show', '-s', '--format=%P', path.split('/')[-1]).split()]}
        if path == 'pulls' and method == 'POST':
            self.boundary('before-pr')
            number = len(self.records['prs']) + 1
            head = git(Path(self.origin), 'rev-parse', 'refs/heads/' + value['head'])
            baseline = git(Path(self.origin), 'rev-parse', 'refs/heads/main')
            changed = git(self.root, 'diff', '--name-only', baseline, head).splitlines()
            pr = {'number': number, 'html_url': 'synthetic://' + self.repository + '/pulls/' + str(number),
                  'user': {'login': self.owner}, 'title': value['title'], 'body': value['body'], 'state': 'open',
                  'head': {'sha': head, 'ref': value['head'], 'repo': {'full_name': self.repository}},
                  'base': {'sha': baseline, 'ref': 'main', 'repo': {'full_name': self.repository}},
                  'mergeable': True, 'mergeable_state': 'clean',
                  'merged': False, 'changed_files': len(changed)}
            self.records['prs'][str(number)] = pr; self.save()
            self.boundary('after-pr')
            return copy.deepcopy(pr)
        if path == 'pulls' and method == 'GET':
            branch = parameters.get('head', [''])[0].partition(':')[2]
            return [copy.deepcopy(pr) for pr in self.records['prs'].values() if pr['head']['ref'] == branch]
        match = re.fullmatch(r'pulls/(\d+)(?:/(.*))?', path)
        if match:
            pr = self.records['prs'][match[1]]; suffix = match[2]
            if suffix is None: return copy.deepcopy(pr)
            if suffix == 'reviews':
                return [{'id': 1, 'commit_id': pr['head']['sha'], 'state': 'COMMENTED', 'body': 'Review completed',
                         'user': {'login': 'chatgpt-codex-connector'}}]
            if suffix == 'comments': return []
            if suffix == 'files':
                return [{'filename': path, 'status': 'modified'} for path in
                        git(self.root, 'diff', '--name-only', pr['base']['sha'], pr['head']['sha']).splitlines()]
            if suffix == 'merge' and method == 'PUT':
                self.boundary('before-merge')
                if value['sha'] != pr['head']['sha'] or pr['merged']: raise Refused('Synthetic merge fence failed')
                work = self.vendor / ('merge-' + match[1])
                # Separate object store: hosted merges do not create objects in
                # the developer checkout. Shared worktrees would hide that seam.
                git(self.vendor, 'clone', '--no-checkout', self.origin, str(work))
                git(work, 'checkout', '--detach', pr['base']['sha'])
                git(work, 'config', 'user.name', 'Synthetic GitHub Merge Service')
                git(work, 'config', 'user.email', 'merge-service@invalid')
                git(work, 'merge', '--squash', pr['head']['sha'])
                git(work, 'commit', '-m', value['commit_title'])
                source = git(work, 'rev-parse', 'HEAD'); git(work, 'push', 'origin', 'HEAD:refs/heads/main')
                pr.update(merged=True, merged_at='synthetic-observed', merge_commit_sha=source, state='closed')
                self.save()
                self.boundary('after-merge')
                return {'merged': True, 'sha': source}
        match = re.fullmatch(r'commits/([a-f0-9]{40})/(pulls|check-runs)', path)
        if match:
            sha, kind = match.groups()
            if kind == 'pulls': return [copy.deepcopy(pr) for pr in self.records['prs'].values() if pr.get('merge_commit_sha') == sha]
            pr = next(pr for pr in self.records['prs'].values() if pr['head']['sha'] == sha)
            run = self.ci(sha, 'pull_request', pr['head']['ref'])
            return {'check_runs': [{'id': run['id'], 'head_sha': sha, 'name': self.config['requiredCheck'],
                                   'status': 'completed', 'conclusion': 'success', 'app': {'slug': 'github-actions'}}]}
        if path == 'actions/workflows/aim-ci.yml/runs':
            sha = parameters['head_sha'][0]; event = parameters['event'][0]
            branch = next((pr['head']['ref'] for pr in self.records['prs'].values() if pr['head']['sha'] == sha), 'main') if event == 'pull_request' else 'main'
            return {'workflow_runs': [self.ci(sha, event, branch)]}
        if path == 'actions/workflows/aim-ci-production.yml/runs':
            source = git(Path(self.origin), 'rev-parse', 'refs/heads/main')
            return {'workflow_runs': [self.deploy(source)]}
        if re.fullmatch(r'actions/runs/\d+/jobs', path):
            return {'jobs': [{'name': name, 'status': 'completed', 'conclusion': 'success'} for name in ('deploy', 'probe')]}
        raise Refused('Unsupported synthetic endpoint: ' + path)


def core_bindings():
    paths = ['aim-ci.py', 'aim-ci-release.py']
    paths += [str(path.relative_to(c.ROOT)) for path in sorted((c.ROOT / 'lib/aim_ci').glob('*.py'))]
    paths += ['lib/openai_budget.py', 'lib/review-policy.sh']
    paths += [str(path.relative_to(c.ROOT)) for path in sorted((c.ROOT / 'profiles').glob('*.json'))]
    return [{'path': path, 'sha256': hashlib.sha256((c.ROOT / path).read_bytes()).hexdigest()} for path in sorted(paths)]


FAULTS = ('before-model:intake', 'after-model-dispatch:intake', 'after-model-receipt:intake',
          'before-model:review-1', 'after-model-receipt:review-1',
          'before-push', 'after-push', 'before-pr', 'after-pr', 'before-merge', 'after-merge',
          'before-deploy', 'after-deploy')


def run_case(directory, *, fault=None, resume=False):
    """One bounded catalog worker witness; restart reopens the SAME journals."""
    directory = Path(directory).resolve(); safe_path(directory)
    if resume:
        context = json.loads((directory / 'synthetic-context.json').read_text())
        if (context.get('synthetic') is not True or context.get('product') != 'catalog'
                or context.get('contractSha256') != c.resume_contract_sha()):
            raise Refused('Offline resume requires its unchanged core and synthetic context')
        runtime = SyntheticTransports(directory, 'catalog', reopen=True)
    else:
        if directory.exists() or fault not in FAULTS:
            raise Refused('New synthetic case and a known fault boundary are required')
        directory.mkdir(parents=True)
        atomic_json(directory / 'synthetic-legacy.json', {'synthetic': True, 'requests': []})
        atomic_json(directory / 'synthetic-context.json',
                    {'synthetic': True, 'product': 'catalog', 'contractSha256': c.resume_contract_sha()})
        runtime = SyntheticTransports(directory, 'catalog')
        runtime.fault = fault
    with c.offline_transports(runtime):
        if resume:
            runtime.config = c.profile('catalog')
        else:
            runtime.set_job(102, 'Use a fictional product catalog heading', 'Synthetic catalog',
                            'Fictional product catalog', 'Catalog heading says Fictional product catalog')
        result = c.run_issue(102, runtime.config, resume=resume)
        print(json.dumps({'synthetic': True, 'stage': result['stage'],
                          'modelRequests': len(runtime.records['modelRequests']), 'output': str(directory)}))
        return result


def run_human_case(directory, *, resume=False, product=None, reviewer=None, decision_file=None):
    """Synthetic proposal, real local validation, explicit local human review only."""
    from openai_budget import read_owned
    from .human_review import policy
    directory = Path(directory).resolve()
    safe_path(directory)
    if resume:
        context = json.loads(read_owned(directory, 'human-context.json'))
        if context.get('synthetic') is not True or context.get('contractSha256') != c.resume_contract_sha():
            raise Refused('Human offline resume requires the unchanged original core')
        product = context['product']
        selected = context['reviewPolicy']
        policy({'reviewPolicy': selected})
        if selected['mode'] != 'human-local':
            raise Refused('Expected an existing human review context')
        runtime = SyntheticTransports(directory, product, reopen=True)
    else:
        selected = {'mode': 'human-local', 'reviewer': reviewer}
        policy({'reviewPolicy': selected})
        get_product(product)
        if directory.exists():
            raise Refused('New human offline workspace required; never replace an attempt')
        directory.mkdir(parents=True)
        atomic_json(directory / 'synthetic-legacy.json', {'synthetic': True, 'requests': []})
        atomic_json(directory / 'human-context.json', {'synthetic': True, 'product': product,
                    'contractSha256': c.resume_contract_sha(), 'reviewPolicy': selected})
        runtime = SyntheticTransports(directory, product)
    runtime.review_policy = selected
    runtime.human_decision_file = decision_file
    with c.offline_transports(runtime):
        if resume:
            runtime.config = c.profile(product)
        else:
            runtime.set_job(102, 'Use a fictional product catalog heading', 'Synthetic catalog',
                            'Fictional product catalog', 'Catalog heading says Fictional product catalog')
        state = c.run_issue(102, runtime.config, resume=resume)
        result = {'synthetic': True, 'stage': state['stage'], 'humanReview': state.get('humanReview'),
                  'publicationAuthorized': False, 'productAccepted': False,
                  'modelRequests': len(runtime.records['modelRequests'])}
        print(json.dumps(result))
        return result


def qualify(directory):
    directory = Path(directory).resolve(); safe_path(directory)
    if directory.exists(): raise Refused('New isolated qualification directory required; never reset a run')
    directory.mkdir(parents=True)
    atomic_json(directory / 'synthetic-legacy.json', {'synthetic': True, 'requests': []})
    real_paths = [c.STATE / 'budget.json', c.ROOT / '.sandbox/openai-api-probe/budget.json']
    real_before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                   for path in real_paths}
    frozen = core_bindings(); results = []
    adapters = {'catalog': SyntheticTransports(directory, 'catalog')}
    jobs = [('B1', 'catalog', 102, 'Use a fictional product catalog heading', 'Synthetic catalog',
             'Fictional product catalog', 'Catalog heading says Fictional product catalog')]
    try:
        for job, name, number, title, old, new, expected in jobs:
            if core_bindings() != frozen: raise Refused('Frozen core bytes changed between qualification jobs')
            runtime = adapters[name]
            with c.offline_transports(runtime):
                runtime.set_job(number, title, old, new, expected)
                state = c.run_issue(number, runtime.config)
                if state['stage'] != 'offline_verified': raise Refused('Synthetic worker did not verify its local destination')
                before = len(runtime.records['modelRequests'])
                replay = c.run_issue(number, runtime.config)
                if replay != state or len(runtime.records['modelRequests']) != before:
                    raise Refused('Duplicate event repeated synthetic model work')
                results.append({'job': job, 'product': name, 'state': state,
                                'coreSha256': fingerprint(frozen), 'synthetic': True})
                atomic_json(directory / 'result.json', {'synthetic': True, 'passed': False, 'jobs': results})
        if core_bindings() != frozen: raise Refused('Core changed during qualification')
        if any((hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None) != digest
               for path,digest in real_before.items()):
            raise Refused('Real resource history changed during offline qualification')
        result = {'synthetic': True, 'passed': True, 'coreBindings': frozen, 'jobs': results,
                  'realBudgetUnchanged': True, 'liveModelCalls': 0, 'liveGitHubWrites': 0, 'productionDeployments': 0,
                  'limitations': ['Hosted permissions/events/audit/signatures are simulated; no general live-platform qualification.',
                                 'Unknown started whole-worker execution still stops; stage-level receipts do not establish general worker resume.']}
        atomic_json(directory / 'result.json', result)
        print(json.dumps({'synthetic': True, 'passed': True, 'jobs': [item['job'] for item in results], 'output': str(directory)}))
        return result
    except Exception as error:
        atomic_json(directory / 'result.json', {'synthetic': True, 'passed': False, 'jobs': results,
                                               'failureClass': type(error).__name__, 'reason': str(error)})
        raise
