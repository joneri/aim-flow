"""Offline GitHub delivery contracts: mocked API, real pinned installation/state."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('AIM_FLOW_TEST_ROOT', str(SOURCE)))
sys.path.insert(0, str(ROOT / 'lib'))
import flow_github_delivery as delivery
from aim_ci import control_package
from aim_ci.owned_paths import Refused

BASE, HEAD, MERGE, TREE = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
WORKFLOW = b'name: Generic checks\non: push\njobs: {}\n'
REPO = {'id': 123, 'full_name': 'fixture/project'}


def pin(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class FakeGitHub:
    def __init__(self, case):
        self.case = case
        self.calls = []
        self.draft = True
        self.merged = False
        self.attempt = 1
        self.pending = False
        self.no_runs = False
        self.mutate = lambda route, value: value
        self.before = lambda method, route, body: None
        self.fail_effect = None
        self.merge_then_fail = False
        self.run_count = 1

    def pull(self):
        return {'number': 1, 'node_id': 'PR_fixture', 'draft': self.draft, 'merged': self.merged,
                'state': 'closed' if self.merged else 'open', 'mergeable': True,
                'mergeable_state': 'draft' if self.draft else 'clean',
                'merge_commit_sha': MERGE if self.merged else None,
                'head': {'sha': HEAD, 'ref': 'feature/example', 'repo': REPO},
                'base': {'sha': BASE, 'ref': 'main', 'repo': REPO}}

    def run(self):
        return {'id': 777, 'run_attempt': self.attempt, 'workflow_id': 555, 'path': '.github/workflows/check.yml',
                'event': 'push', 'head_sha': MERGE, 'head_branch': 'main', 'repository': REPO,
                'head_repository': REPO, 'status': 'in_progress' if self.pending else 'completed',
                'conclusion': None if self.pending else 'success'}

    def __call__(self, method, route, body=None):
        self.calls.append((method, route, body))
        self.before(method, route, body)
        path = urlsplit(route).path.removeprefix('/repos/fixture/project')
        query = parse_qs(urlsplit(route).query)
        if method != 'GET':
            effect = 'ready' if route == 'graphql' else 'merge'
            record = self.case.state / (effect + '-dispatch.json')
            self.case.assertTrue(record.exists(), 'Effect must follow durable reservation')
            self.case.assertEqual(json.loads(record.read_bytes())['body'], body)
            if self.fail_effect == effect:
                if effect == 'merge' and self.merge_then_fail:
                    self.merged = True
                raise delivery.GitHubError('Fixture official error', {'exitCode': 1, 'stdout': '{"message":"fixture failure"}',
                                                                     'stderr': 'gh: fixture failure (HTTP 403)'})
            if route == 'graphql':
                self.case.assertEqual(body, {'query': delivery.READY_MUTATION, 'variables': {'id': 'PR_fixture'}})
                self.draft = False
                value = {'data': {'markPullRequestReadyForReview': {'pullRequest': {'id': 'PR_fixture', 'isDraft': False}}}}
            else:
                self.case.assertEqual((method, path, body), ('PUT', '/pulls/1/merge', {'sha': HEAD, 'merge_method': 'merge'}))
                self.merged = True
                value = {'merged': True, 'sha': MERGE, 'message': 'Pull Request successfully merged'}
        elif not path:
            value = REPO
        elif path == '/pulls/1':
            value = self.pull()
        elif path == '/git/ref/heads/main':
            value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': MERGE if self.merged else BASE}}
        elif path == '/commits/' + HEAD + '/check-runs':
            value = {'total_count': 1, 'check_runs': [{'id': 11, 'name': 'Build', 'app': {'id': 55},
                     'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'}]}
        elif path == '/commits/' + HEAD + '/status':
            value = {'sha': HEAD, 'repository': REPO, 'total_count': 1,
                     'statuses': [{'id': 22, 'context': 'Legacy', 'state': 'success'}]}
        elif path == '/git/commits/' + MERGE:
            value = {'sha': MERGE, 'tree': {'sha': TREE}, 'parents': [{'sha': BASE}, {'sha': HEAD}]}
        elif path == '/contents/.github/workflows/check.yml':
            self.case.assertEqual(query, {'ref': [MERGE]})
            value = {'path': '.github/workflows/check.yml', 'type': 'file', 'encoding': 'base64',
                     'content': base64.b64encode(WORKFLOW).decode()}
        elif path == '/actions/workflows/check.yml':
            value = {'id': 555, 'path': '.github/workflows/check.yml', 'state': 'active'}
        elif path == '/actions/workflows/555/runs':
            self.case.assertEqual(query, {'event': ['push'], 'head_sha': [MERGE], 'branch': ['main'],
                                          'per_page': ['100'], 'page': ['1']})
            runs = [] if self.no_runs else [dict(self.run(), id=777 + i) for i in range(self.run_count)]
            value = {'total_count': len(runs), 'workflow_runs': runs}
        elif path == '/actions/runs/777':
            value = self.run()
        elif path == '/actions/runs/777/attempts/%d/jobs' % self.attempt:
            value = {'total_count': 1, 'jobs': [{'id': 888, 'name': 'Frontend build', 'run_id': 777,
                     'run_attempt': self.attempt, 'head_sha': MERGE, 'status': 'completed', 'conclusion': 'success'}]}
        elif path == '/actions/runs/777/artifacts':
            created = datetime.now(timezone.utc) - timedelta(hours=1)
            value = {'total_count': 1, 'artifacts': [{'id': 999, 'name': 'frontend-%s-%d' % (MERGE, self.attempt),
                     'size_in_bytes': 1000, 'expired': False, 'digest': 'sha256:' + 'd' * 64,
                     'created_at': created.isoformat(), 'expires_at': (created + timedelta(days=7)).isoformat(),
                     'workflow_run': {'id': 777, 'head_sha': MERGE, 'head_branch': 'main',
                                      'repository_id': 123, 'head_repository_id': 123}}]}
        else:
            raise AssertionError('Unexpected route: ' + method + ' ' + route)
        return self.mutate(route, copy.deepcopy(value))


class GitHubDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix='flow-github-contract-')
        cls.addClassCleanup(directory.cleanup)
        cls.scratch = Path(directory.name).resolve()
        if (ROOT / 'control-manifest.json').exists():
            cls.installation = ROOT
            cls.manifest_sha = pin(ROOT / 'control-manifest.json')['sha256']
        else:
            cls.installation = cls.scratch / 'installed'
            archive = cls.scratch / 'controller.tar.gz'
            release = control_package.build(ROOT, archive, '0.10.0-github-contract.1')
            control_package.install(archive, release['archiveSha256'], cls.installation)
            cls.manifest_sha = release['manifestSha256']

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=self.scratch)
        self.addCleanup(directory.cleanup)
        self.case = Path(directory.name).resolve()
        self.state = self.case / 'state'
        self.config = self.case / 'declaration.json'
        self.gh = self.case / 'fixture-gh'
        self.gh.write_text('#!/bin/sh\nexit 91\n')
        self.gh.chmod(0o700)
        evidence = {}
        for role in ('aggregate', 'browser', 'independentReview'):
            path = self.case / (role + '.json')
            path.write_text(json.dumps({'fixtureOnly': True, 'head': HEAD}))
            evidence[role] = pin(path)
        self.c = {'format': 'aim-flow-github-delivery', 'schemaVersion': 1, 'manifestSha256': self.manifest_sha,
                  'repository': {'name': 'fixture/project', 'id': 123},
                  'pull': {'number': 1, 'headSha': HEAD, 'headRef': 'feature/example', 'baseSha': BASE,
                           'baseRef': 'main', 'markDraftReady': True},
                  'requiredPrChecks': [{'name': 'Build', 'appId': 55}], 'requiredPrStatuses': ['Legacy'],
                  'workflow': {'path': '.github/workflows/check.yml', 'sha256': hashlib.sha256(WORKFLOW).hexdigest(),
                               'requiredJobs': [{'key': 'frontend-build', 'name': 'Frontend build'}],
                               'artifact': {'nameTemplate': 'frontend-{sha}-{attempt}', 'retentionDays': 7}},
                  'evidence': evidence, 'transport': {'gh': pin(self.gh), 'home': str(self.case),
                                                     'configDirectory': str(self.case), 'timeoutSeconds': 20}}
        self.write_config()
        self.root_patch = patch.object(delivery, 'ROOT', self.installation)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.api = FakeGitHub(self)

    def write_config(self):
        self.config.write_text(json.dumps(self.c))
        self.config_sha = pin(self.config)['sha256']

    def preview(self):
        return delivery.run(self.config, self.config_sha, self.state, transport=self.api)

    def start(self):
        return delivery.run(self.config, self.config_sha, self.state, self.preview()['intentSha256'], transport=self.api)

    def resume(self):
        return delivery.run(self.config, self.config_sha, self.state, resume=True, transport=self.api)

    def effects(self):
        return [c for c in self.api.calls if c[0] != 'GET']

    def test_preview_is_local_and_exact_authorization_binds_all_inputs_and_state(self):
        preview = self.preview()
        self.assertEqual(preview['status'], 'preview')
        self.assertFalse(self.api.calls)
        self.assertFalse(self.state.exists())
        self.assertIn('mark specified draft ready', str(preview))
        self.assertIn('authenticity', str(preview))
        with self.assertRaisesRegex(Refused, 'Exact preview'):
            delivery.run(self.config, self.config_sha, self.state, '0' * 64, transport=self.api)
        self.assertFalse(self.state.exists())
        self.state = self.case / 'other-state'
        self.assertNotEqual(preview['intentSha256'], self.preview()['intentSha256'])

    def test_success_retains_effects_and_exact_ci_artifact_without_claiming_release(self):
        result = self.start()
        self.assertEqual(result['status'], 'main-push-verified', result)
        self.assertEqual(len(self.effects()), 2)
        self.assertEqual(result['mergedSha'], MERGE)
        self.assertEqual(result['mergedTree'], TREE)
        self.assertEqual((result['runId'], result['runAttempt'], result['artifact']['id']), (777, 1, 999))
        self.assertEqual(result['artifact']['digest'], 'sha256:' + 'd' * 64)
        self.assertEqual(result['requiredJobs'][0]['declaredKey'], 'frontend-build')
        self.assertFalse(result['artifactBytesVerified'])
        self.assertFalse(result['independentReviewAuthenticated'])
        self.assertFalse(result['releaseExecuted'])
        self.assertTrue(result['mergeResponseConfirmed'])
        self.assertEqual(result['declarationSha256'], self.config_sha)
        self.assertIn('merge-dispatch.json', result['evidenceFiles'])
        for name, digest in result['evidenceFiles'].items():
            self.assertEqual(pin(self.state / name)['sha256'], digest)
        self.assertEqual(json.loads((self.state / 'result-000001.json').read_bytes()), result)
        with self.assertRaises(Refused):
            self.start()
        self.assertEqual(len(self.effects()), 2)

    def test_already_ready_requires_false_declaration_and_never_marks_ready(self):
        self.c['pull']['markDraftReady'] = False
        self.api.draft = False
        self.write_config()
        self.assertEqual(self.start()['status'], 'main-push-verified')
        self.assertEqual([c[0] for c in self.effects()], ['PUT'])

    def test_input_drift_duplicate_keys_and_schema_routes_refused_without_network(self):
        old = self.config_sha
        self.config.write_text(self.config.read_text() + ' ')
        with self.assertRaises(Refused):
            delivery.run(self.config, old, self.state, transport=self.api)
        self.config.write_text('{"format":1,"format":2}')
        with self.assertRaisesRegex(Refused, 'Duplicate'):
            delivery.run(self.config, pin(self.config)['sha256'], self.state, transport=self.api)
        changes = [(['repository', 'name'], 'owner/repo?bad'), (['pull', 'baseRef'], '../main'),
                   (['workflow', 'path'], '.github/workflows/../secret.yml'),
                   (['workflow', 'artifact', 'nameTemplate'], '{sha}-{attempt}-{arbitrary}'),
                   (['transport', 'timeoutSeconds'], True), (['pull', 'number'], True)]
        original = copy.deepcopy(self.c)
        for path, value in changes:
            self.c = copy.deepcopy(original)
            target = self.c
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            self.write_config()
            with self.subTest(path=path), self.assertRaises(Refused):
                self.preview()
        self.assertFalse(self.api.calls)

    def test_input_pin_drift_and_symlink_refused(self):
        evidence = Path(self.c['evidence']['browser']['path'])
        evidence.write_text('{}')
        with self.assertRaises(Refused):
            self.preview()
        self.c['evidence']['browser'] = pin(evidence)
        self.write_config()
        evidence.unlink()
        evidence.symlink_to(self.c['evidence']['aggregate']['path'])
        with self.assertRaises(Refused):
            self.preview()
        self.assertFalse(self.api.calls)

    def test_all_remote_preflight_refusals_have_no_effect(self):
        mutations = [
            ('/pulls/1', lambda d: d['head'].update(sha='d' * 40)),
            ('/pulls/1', lambda d: d['base'].update(sha='d' * 40)),
            ('/pulls/1', lambda d: d['head']['repo'].update(id=124)),
            ('/pulls/1', lambda d: d.update(number=2)),
            ('/pulls/1', lambda d: d.update(state='closed')),
            ('/pulls/1', lambda d: d.update(draft=False)),
            ('/pulls/1', lambda d: d.update(mergeable=None)),
            ('/git/ref/heads/main', lambda d: d['object'].update(sha='d' * 40)),
            ('/check-runs?', lambda d: d['check_runs'][0].update(status='in_progress')),
            ('/check-runs?', lambda d: d['check_runs'][0].update(conclusion='failure')),
            ('/check-runs?', lambda d: d['check_runs'][0].update(conclusion='skipped')),
            ('/check-runs?', lambda d: d['check_runs'][0].update(head_sha=BASE)),
            ('/check-runs?', lambda d: d['check_runs'][0]['app'].update(id=66)),
            ('/check-runs?', lambda d: d.update(total_count=0, check_runs=[])),
            ('/check-runs?', lambda d: d.update(total_count=2, check_runs=d['check_runs'] * 2)),
            ('/status?', lambda d: d.update(sha=BASE)),
            ('/status?', lambda d: d['repository'].update(id=124)),
            ('/status?', lambda d: d['statuses'][0].update(state='pending')),
            ('/status?', lambda d: d.update(total_count=0, statuses=[])),
        ]
        for index, (match, change) in enumerate(mutations):
            with self.subTest(match=match, index=index):
                self.state = self.case / ('state%d' % index)
                self.api = FakeGitHub(self)
                def mutate(route, value):
                    if match in route:
                        change(value)
                    return value
                self.api.mutate = mutate
                result = self.start()
                self.assertEqual(result['status'], 'stopped-not-ready', result)
                self.assertFalse(self.effects())

    def test_check_change_after_mark_ready_stops_before_merge(self):
        def mutate(route, value):
            if '/check-runs?' in route and not self.api.draft:
                value['check_runs'][0]['conclusion'] = 'failure'
            return value
        self.api.mutate = mutate
        result = self.start()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertEqual([c[0] for c in self.effects()], ['POST'])
        self.assertEqual(self.resume()['status'], 'merge-not-observed-no-retry')
        self.assertEqual(len(self.effects()), 1)

    def test_ready_error_stops_immediately_and_resume_never_merges(self):
        self.api.fail_effect = 'ready'
        result = self.start()
        self.assertEqual(result['status'], 'effect-outcome-unknown')
        self.assertEqual(self.api.calls[-1][0], 'POST')
        observed = [json.loads(p.read_bytes()) for p in self.state.glob('observation-*.json')]
        self.assertTrue(any('externalObservation' in d.get('error', {}) for d in observed))
        self.assertEqual(self.resume()['status'], 'merge-not-observed-no-retry')
        self.assertEqual(len(self.effects()), 1)

    def test_unknown_merge_reconciles_read_only_after_actual_remote_success(self):
        self.api.fail_effect, self.api.merge_then_fail = 'merge', True
        result = self.start()
        self.assertEqual(result['status'], 'effect-outcome-unknown')
        self.assertEqual(self.api.calls[-1][0], 'PUT')
        resumed = self.resume()
        self.assertEqual(resumed['status'], 'main-push-verified')
        self.assertFalse(resumed['mergeResponseConfirmed'])
        self.assertIn('not proof of who merged', resumed['mergeAttribution'])
        self.assertEqual(len(self.effects()), 2)
        self.assertTrue((self.state / 'result-000002.json').exists())

    def test_unknown_merge_not_observed_is_never_retried(self):
        self.api.fail_effect = 'merge'
        self.assertEqual(self.start()['status'], 'effect-outcome-unknown')
        self.assertEqual(self.resume()['status'], 'merge-not-observed-no-retry')
        self.assertEqual(self.resume()['status'], 'merge-not-observed-no-retry')
        self.assertEqual(len(self.effects()), 2)

    def test_outside_merge_cannot_be_claimed_as_flow_merge(self):
        self.api.fail_effect = 'ready'
        self.start()
        self.api.merged = True
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertIn('not dispatched', result['error']['message'])

    def test_merge_parent_and_remote_main_races_stop_handoff_without_rollback(self):
        for index, match in enumerate(('/git/commits/', '/git/ref/heads/')):
            self.state = self.case / ('race%d' % index)
            self.api = FakeGitHub(self)
            def mutate(route, value):
                if self.api.merged and match in route:
                    if 'parents' in value:
                        value['parents'][0]['sha'] = 'd' * 40
                    else:
                        value['object']['sha'] = 'd' * 40
                return value
            self.api.mutate = mutate
            result = self.start()
            self.assertEqual(result['status'], 'stopped-not-ready')
            self.assertEqual(result['phase'], 'reconcile')
            self.assertEqual(len(self.effects()), 2)
            self.assertFalse(any('/actions/' in c[1] for c in self.api.calls))

    def test_missing_or_malformed_merged_tree_stops_handoff_without_retry(self):
        for index, tree in enumerate((None, {}, {'sha': 'bad'}, {'sha': True})):
            self.state = self.case / ('tree%d' % index)
            self.api = FakeGitHub(self)
            self.api.mutate = lambda route, value: ({**value, 'tree': tree}
                if '/git/commits/' in route else value)
            result = self.start()
            self.assertEqual(result['status'], 'stopped-not-ready', result)
            self.assertEqual(result['phase'], 'reconcile')
            self.assertIn('tree identity', result['error']['message'])
            self.assertEqual(len(self.effects()), 2)
            self.assertFalse(any('/actions/' in c[1] for c in self.api.calls))

    def test_pending_workflow_can_resume_without_another_merge(self):
        self.api.no_runs = True
        self.assertEqual(self.start()['status'], 'awaiting-main-push')
        self.api.no_runs, self.api.pending = False, True
        self.assertEqual(self.resume()['status'], 'awaiting-main-push')
        self.api.pending = False
        self.assertEqual(self.resume()['status'], 'main-push-verified')
        self.assertEqual(len(self.effects()), 2)

    def test_new_attempt_and_multiple_runs_are_not_silently_accepted(self):
        self.api.pending = True
        self.assertEqual(self.start()['status'], 'awaiting-main-push')
        self.api.pending, self.api.attempt = False, 2
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertIn('attempt changed', result['error']['message'])
        self.api.attempt, self.api.run_count = 1, 2
        self.assertEqual(self.resume()['status'], 'stopped-not-ready')
        self.assertEqual(len(self.effects()), 2)

    def test_ci_job_and_artifact_refusals(self):
        mutations = [
            ('/contents/', lambda d: d.update(content=base64.b64encode(b'changed').decode())),
            ('/actions/workflows/check.yml', lambda d: d.update(path='.github/workflows/other.yml')),
            ('/actions/workflows/555/runs?', lambda d: d['workflow_runs'][0].update(event='pull_request')),
            ('/actions/workflows/555/runs?', lambda d: d['workflow_runs'][0].update(head_sha=HEAD)),
            ('/actions/workflows/555/runs?', lambda d: d['workflow_runs'][0]['repository'].update(id=124)),
            ('/actions/runs/777', lambda d: d.update(conclusion='failure') if 'run_attempt' in d else None),
            ('/jobs?', lambda d: d['jobs'][0].update(conclusion='failure')),
            ('/jobs?', lambda d: d['jobs'][0].update(status='in_progress')),
            ('/jobs?', lambda d: d['jobs'][0].update(run_attempt=2)),
            ('/jobs?', lambda d: d['jobs'][0].update(head_sha=HEAD)),
            ('/jobs?', lambda d: d.update(total_count=0, jobs=[])),
            ('/jobs?', lambda d: d.update(total_count=2, jobs=d['jobs'] + [dict(d['jobs'][0], id=889)])),
            ('/artifacts?', lambda d: d['artifacts'][0].update(expired=True)),
            ('/artifacts?', lambda d: d['artifacts'][0].update(expires_at='2000-01-01T00:00:00Z')),
            ('/artifacts?', lambda d: d['artifacts'][0].update(name='frontend-' + MERGE + '-2')),
            ('/artifacts?', lambda d: d['artifacts'][0].update(size_in_bytes=0)),
            ('/artifacts?', lambda d: d['artifacts'][0]['workflow_run'].update(id=778)),
            ('/artifacts?', lambda d: d['artifacts'][0]['workflow_run'].update(head_sha=HEAD)),
            ('/artifacts?', lambda d: d['artifacts'][0].update(digest='sha256:bad')),
        ]
        for index, (match, change) in enumerate(mutations):
            with self.subTest(index=index):
                self.state = self.case / ('ci%d' % index)
                self.api = FakeGitHub(self)
                def mutate(route, value):
                    if match in route:
                        change(value)
                    return value
                self.api.mutate = mutate
                result = self.start()
                self.assertEqual(result['status'], 'stopped-not-ready', result)
                self.assertEqual(result['phase'], 'ci', result)
                self.assertEqual(len(self.effects()), 2)

    def test_final_attempt_change_and_disappearing_selection_stop_handoff(self):
        self.api.pending = True
        self.assertEqual(self.start()['status'], 'awaiting-main-push')
        self.api.no_runs = True
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertIn('disappeared', result['error']['message'])
        self.api.no_runs, self.api.pending = False, False
        seen_artifact = [False]
        def mutate(route, value):
            if '/artifacts?' in route:
                seen_artifact[0] = True
            if route.endswith('/actions/runs/777') and seen_artifact[0]:
                value['run_attempt'] = 2
            return value
        self.api.mutate = mutate
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertIn('Run changed', result['error']['message'])
        seen_artifact[0] = False
        def wrong_run_id(route, value):
            if '/artifacts?' in route:
                seen_artifact[0] = True
            if route.endswith('/actions/runs/777') and seen_artifact[0]:
                value['id'] = 778
            return value
        self.api.mutate = wrong_run_id
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertIn('Run changed', result['error']['message'])
        self.assertEqual(len(self.effects()), 2)

    def test_paginated_metadata_is_complete_and_retained(self):
        session = delivery.Session(self.c, None)
        calls = []
        def paginated(method, route, body):
            calls.append(route)
            page = int(parse_qs(urlsplit(route).query)['page'][0])
            return {'total_count': 101, 'jobs': [{'id': i} for i in (range(1, 101) if page == 1 else [101])]}
        session.transport = paginated
        items = session.pages('/actions/runs/777/attempts/1/jobs', 'jobs')
        self.assertEqual(len(items), 101)
        self.assertEqual(len(session.observations), 2)
        session.transport = lambda *a: {'total_count': 2, 'jobs': []}
        with self.assertRaisesRegex(Refused, 'Incomplete'):
            session.pages('/jobs', 'jobs')
        page = [0]
        def unstable(*a):
            page[0] += 1
            return {'total_count': 2 if page[0] == 1 else 3, 'jobs': [{'id': page[0]}]}
        session.transport = unstable
        with self.assertRaisesRegex(Refused, 'changed during pagination'):
            session.pages('/jobs', 'jobs')

    def test_inputs_rechecked_before_effect_after_observation(self):
        count = [0]
        def before(method, route, body):
            if route.endswith('/pulls/1'):
                count[0] += 1
                if count[0] == 2:
                    Path(self.c['evidence']['browser']['path']).write_text('{}')
        self.api.before = before
        self.assertEqual(self.start()['status'], 'stopped-not-ready')
        self.assertFalse(self.effects())

    def test_durable_reservation_failure_prevents_external_effect(self):
        original = delivery.local.durable
        def durable(root, name, raw):
            if name == 'merge-dispatch.json':
                raise OSError('fixture fsync failure')
            return original(root, name, raw)
        with patch.object(delivery.local, 'durable', durable):
            result = self.start()
        self.assertEqual(result['status'], 'effect-outcome-unknown')
        self.assertEqual([c[0] for c in self.effects()], ['POST'])

    def test_transport_uses_pinned_gh_fixed_argv_clean_environment_and_json_stdin(self):
        captured = self.case / 'captured.json'
        self.gh.write_text('#!' + str(Path(sys.executable).resolve()) + '\n'
            'import json,os,sys\n'
            'open(' + repr(str(captured)) + ',"w").write(json.dumps({"argv":sys.argv,"env":dict(os.environ),"body":sys.stdin.read()}))\n'
            'print("{\\"ok\\":true}")\n')
        self.c['transport']['gh'] = pin(self.gh)
        tr = delivery.GhTransport(self.c)
        with patch.dict(os.environ, {'GH_TOKEN': 'fixture-not-real', 'GH_DEBUG': 'api', 'GITHUB_TOKEN': 'fixture-not-real'}):
            value = tr('PUT', '/repos/fixture/project/pulls/1/merge', {'sha': HEAD, 'merge_method': 'merge'})
        self.assertEqual(value, {'ok': True})
        record = json.loads(captured.read_bytes())
        self.assertEqual(record['argv'][1:7], ['api', '--hostname', 'github.com', '--method', 'PUT', '/repos/fixture/project/pulls/1/merge'])
        self.assertFalse({'GH_TOKEN', 'GITHUB_TOKEN', 'GH_DEBUG'} & set(record['env']))
        self.assertEqual(json.loads(record['body']), {'sha': HEAD, 'merge_method': 'merge'})
        for route in ('https://evil.invalid/x', '/repos/fixture/project/../../x', '/repos/fixture/project/x;echo', '-x'):
            with self.subTest(route=route):
                # Traversal cannot be created by adapter inputs. Transport also rejects it directly.
                with self.assertRaises(Refused):
                    tr('GET', route)
        self.gh.write_text('drift')
        with self.assertRaises(Refused):
            tr('GET', '/repos/fixture/project')

    def test_transport_accepts_only_constructed_routes_methods_and_effect_bodies(self):
        self.assertEqual(self.start()['status'], 'main-push-verified')
        self.gh.write_text('#!' + str(Path(sys.executable).resolve()) + '\nprint("{}")\n')
        self.c['transport']['gh'] = pin(self.gh)
        transport = delivery.GhTransport(self.c)
        for method, route, body in self.api.calls:
            self.assertEqual(transport(method, route, body), {})
        refused = [
            ('DELETE', '/repos/fixture/project/pulls/1', None),
            ('PUT', '/repos/fixture/project/pulls/2/merge', {'sha': HEAD, 'merge_method': 'merge'}),
            ('PUT', '/repos/fixture/project/pulls/1/merge', {'sha': BASE, 'merge_method': 'merge'}),
            ('PUT', '/repos/fixture/project/pulls/1/merge', {'sha': HEAD, 'merge_method': 'merge', 'admin': True}),
            ('PATCH', '/repos/fixture/project/pulls/1', {'body': 'unrequested edit'}),
            ('GET', '/repos/other/project', None),
            ('GET', '/repos/fixture/project/actions/runs/777/artifacts/999/zip', None),
            ('GET', '/repos/fixture/project/actions/runs/777/rerun', None),
            ('POST', 'graphql', {'query': 'mutation { arbitrary }', 'variables': {}}),
        ]
        for method, route, body in refused:
            with self.subTest(method=method, route=route), self.assertRaises(Refused):
                transport(method, route, body)

    def test_transport_errors_timeout_and_metadata_overflow_preserve_observations(self):
        snippets = [
            ('import sys,json; print(json.dumps({"message":"fixture rejection"})); print("fixture stderr", file=sys.stderr); sys.exit(1)', 'fixture rejection'),
            ('print("not json")', 'not json'),
            ('import sys,time; print("partial output", flush=True); time.sleep(2)', 'partial output'),
            ('print("x" * 1000)', 'xxx'),
        ]
        for index, (snippet, expected) in enumerate(snippets):
            with self.subTest(index=index):
                self.gh.write_text('#!' + str(Path(sys.executable).resolve()) + '\n' + snippet + '\n')
                self.c['transport']['gh'] = pin(self.gh)
                transport = delivery.GhTransport(self.c)
                if index == 2:
                    import time
                    transport.deadline = time.monotonic() + 0.2
                with patch.object(delivery, 'MAX_METADATA', 128 if index == 3 else 8 * 1024 * 1024):
                    with self.assertRaises(delivery.GitHubError) as failure:
                        transport('GET', '/repos/fixture/project')
                self.assertIn(expected, failure.exception.observation['stdout'])
                if index == 3:
                    self.assertTrue(failure.exception.observation['outputTruncated'])

    def test_source_free_installed_cli_preview_has_no_transport(self):
        argv = [str(Path(sys.executable).resolve()), '-IBS', str(self.installation / 'aim-flow.py'), 'deliver-github',
                '--config', str(self.config), '--config-sha256', self.config_sha, '--state', str(self.state), '--preview']
        result = subprocess.run(argv, cwd=self.case, env={'PATH': '', 'HOME': str(self.case), 'LC_ALL': 'C'},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'preview')
        self.assertFalse(data['externalReads'])
        self.assertFalse(self.state.exists())
        self.assertEqual((self.installation / 'lib/flow_github_delivery.py').read_bytes(),
                         (ROOT / 'lib/flow_github_delivery.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
