"""Offline publication: real Git objects/state, injected producer/API/push fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('AIM_FLOW_TEST_ROOT', str(SOURCE)))
sys.path.insert(0, str(ROOT / 'lib'))
import flow_github_publication as pub
from aim_ci import control_package
from aim_ci.owned_paths import Refused


def pin(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class FakeGitHub:
    def __init__(self, test):
        self.t = test
        self.remote = None
        self.pr = None
        self.calls = []
        self.pushes = []
        self.fail_push = self.fail_draft = False
        self.push_then_fail = self.draft_then_fail = False
        self.mutate = lambda route, value: value
        self.before = lambda method, route, body: None

    def pull(self):
        c = self.t.c
        repository = {'id': 123, 'full_name': 'fixture/project'}
        return {'number': 17, 'state': 'open', 'draft': True, 'merged': False, 'maintainer_can_modify': False,
                'title': c['pull']['title'], 'body': Path(c['pull']['body']['path']).read_text(),
                'html_url': 'https://github.com/fixture/project/pull/17',
                'head': {'sha': c['source']['headSha'], 'ref': c['branch'], 'repo': repository},
                'base': {'sha': c['source']['baseSha'], 'ref': c['baseRef'], 'repo': repository}}

    def push(self, c, state, view):
        self.t.assertTrue((state / 'push-dispatch.json').exists())
        self.pushes.append(pub.push_command(c, view))
        self.t.assertTrue((view / 'objects/info/alternates').exists())
        if not self.fail_push or self.push_then_fail:
            self.remote = c['source']['headSha']
        if self.fail_push:
            raise pub.gh.GitHubError('Synthetic push interruption', {'exitCode': 128, 'stderr': 'fixture failure'})
        return {'exitCode': 0, 'stdout': '*\t' + c['source']['headSha'] + ':refs/heads/' + c['branch'] + '\t[new branch]\n'}

    def __call__(self, method, route, body=None):
        self.calls.append((method, route, body)); self.before(method, route, body)
        path = urlsplit(route).path.removeprefix('/repos/fixture/project')
        if method == 'POST':
            self.t.assertEqual(path, '/pulls')
            self.t.assertTrue((self.t.state / 'draft-dispatch.json').exists())
            self.t.assertEqual(body, pub.payload(self.t.c))
            if not self.fail_draft or self.draft_then_fail:
                self.pr = self.pull()
            if self.fail_draft:
                raise pub.gh.GitHubError('Synthetic draft interruption', {'exitCode': 1, 'stderr': 'fixture failure (HTTP 403)'})
            value = self.pr
        elif not path:
            value = {'id': 123, 'full_name': 'fixture/project'}
        elif path == '/git/ref/heads/main':
            value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': self.t.c['source']['baseSha']}}
        elif path.startswith('/git/matching-refs/heads/'):
            value = [] if self.remote is None else [{'ref': 'refs/heads/' + self.t.c['branch'],
                                                   'object': {'type': 'commit', 'sha': self.remote}}]
        elif path == '/pulls':
            value = [self.pr] if self.pr else []
        elif path == '/pulls/17':
            value = self.pr
        else:
            raise AssertionError('Unexpected route ' + method + ' ' + route)
        return self.mutate(route, copy.deepcopy(value))


class GitHubPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix='flow-publication-contract-')
        cls.addClassCleanup(directory.cleanup)
        cls.scratch = Path(directory.name).resolve()
        cls.git = Path(shutil.which('git')).resolve()
        if (ROOT / 'control-manifest.json').exists():
            cls.installation = ROOT; cls.manifest = pin(ROOT / 'control-manifest.json')['sha256']
        else:
            cls.installation = cls.scratch / 'installed'
            archive = cls.scratch / 'controller.tar.gz'
            release = control_package.build(ROOT, archive, '0.10.0-publication-contract.1')
            control_package.install(archive, release['archiveSha256'], cls.installation)
            cls.manifest = release['manifestSha256']

    def git_run(self, *args, cwd=None):
        return subprocess.check_output([str(self.git), '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false', *args],
            cwd=cwd or self.checkout, env=self.git_env, text=True).strip()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=self.scratch)
        self.addCleanup(directory.cleanup)
        self.case = Path(directory.name).resolve(); self.checkout = self.case / 'candidate'; self.checkout.mkdir()
        self.git_env = {'PATH': os.defpath, 'HOME': str(self.case), 'LC_ALL': 'C',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@invalid',
            'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@invalid'}
        self.git_run('init', '-q', '--initial-branch=main', '--template=')
        (self.checkout / 'README.md').write_text('Before\n'); self.git_run('add', '.'); self.git_run('commit', '-qm', 'Base')
        base = self.git_run('rev-parse', 'HEAD')
        (self.checkout / 'README.md').write_text('After\n'); self.git_run('add', '.'); self.git_run('commit', '-qm', 'Candidate')
        head = self.git_run('rev-parse', 'HEAD'); tree = self.git_run('rev-parse', 'HEAD^{tree}')
        diff = subprocess.check_output([str(self.git), 'diff', '--no-ext-diff', '--no-textconv', '--binary', '--full-index',
                                        '--no-renames', base, head, '--'], cwd=self.checkout, env=self.git_env)
        source = {'checkout': str(self.checkout), 'baseSha': base, 'headSha': head, 'treeSha': tree,
                  'diffSha256': hashlib.sha256(diff).hexdigest()}
        evidence = {}
        for role in ('aggregate', 'browser', 'independentReview'):
            path = self.case / (role + '.json'); path.write_text(json.dumps({'syntheticFixture': True, 'source': source}))
            evidence[role] = pin(path)
        self.producer_state = self.case / 'producer-state'; self.producer_state.mkdir()
        candidate = self.producer_state / 'candidate-receipt.json'
        target = {'checkout': source['checkout'], 'commit': head, 'tree': tree}
        candidate.write_text(json.dumps({'status': 'candidate-prepared', 'source': source, 'candidate': target}))
        evidence['candidate'] = pin(candidate)
        self.producer = self.case / 'producer'; self.producer.mkdir()
        # This is intentionally a synthetic pinned verifier fixture, not a genuine Flow producer.
        files = {'aim-flow.py': ('import argparse,json\n'
            'p=argparse.ArgumentParser();p.add_argument("command",choices=["work"]);p.add_argument("--config");'
            'p.add_argument("--config-sha256");p.add_argument("--state");p.add_argument("--verify",action="store_true");'
            'a=p.parse_args();assert a.verify;print(json.dumps(json.load(open(a.config))["fixtureResult"]))\n'),
            'aim-ci-control.py': '', 'lib/aim_ci/project_onboarding.py': '',
            'lib/aim_ci/control_archive.py': '', 'lib/flow_workflow.py': ''}
        for name, content in files.items():
            path = self.producer / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        manifest = {'format': 'aim-ci-control', 'schemaVersion': 3, 'version': '0.10.0-producer-fixture.1',
                    'files': {name: pin(self.producer / name)['sha256'] for name in files}}
        (self.producer / 'control-manifest.json').write_text(json.dumps(manifest))
        producer_manifest = pin(self.producer / 'control-manifest.json')['sha256']
        self.producer_config = self.case / 'producer-config.json'
        self.producer_config.write_text(json.dumps({'manifestSha256': producer_manifest, 'fixtureResult': {
            'format': 'aim-flow-requirement-workflow', 'status': 'candidate-prepared', 'source': source,
            'candidate': target, 'candidateReceipt': evidence['candidate'], 'publicationAuthorized': False}}))
        body = self.case / 'body.md'; body.write_text('Requirement: update the fixture.\nValidation: synthetic offline tests only.\n')
        self.gh = self.case / 'fixture-gh'; self.gh.write_text('#!/bin/sh\nexit 90\n'); self.gh.chmod(0o700)
        self.c = {'format': 'aim-flow-github-publication', 'schemaVersion': 1, 'manifestSha256': self.manifest,
            'repository': {'name': 'fixture/project', 'id': 123}, 'source': {**source, 'git': pin(self.git)},
            'branch': 'flow/new-candidate', 'baseRef': 'main', 'pull': {'title': 'Update fixture', 'body': pin(body)},
            'evidence': evidence, 'transport': {'gh': pin(self.gh), 'home': str(self.case), 'configDirectory': str(self.case), 'timeoutSeconds': 20},
            'candidateVerification': {'controller': {'path': str(self.producer), 'manifestSha256': producer_manifest},
                                      'python': pin(Path(sys.executable).resolve()), 'config': pin(self.producer_config), 'state': str(self.producer_state)}}
        self.config = self.case / 'publication.json'; self.write_config(); self.state = self.case / 'state'
        root_patch = patch.object(pub, 'ROOT', self.installation); root_patch.start(); self.addCleanup(root_patch.stop)
        self.api = FakeGitHub(self)

    def test_explicit_producer_deadline_is_configurable_and_forwarded(self):
        for value in (900, 901, 7200, None):
            self.c['candidateVerification']['producerVerificationTimeoutSeconds'] = value
            with patch.object(pub.project_validation, 'verification_process',
                              wraps=pub.project_validation.verification_process) as called:
                pub.verify_producer(self.c)
            self.assertEqual(called.call_args.kwargs['timeout_seconds'], value)
        for value in (0, -1, True, '900'):
            self.c['candidateVerification']['producerVerificationTimeoutSeconds'] = value
            with self.assertRaises(Refused): pub.verify_producer(self.c)
        self.c['candidateVerification'].pop('producerVerificationTimeoutSeconds')
        with patch.object(pub.project_validation, 'verification_process',
                          wraps=pub.project_validation.verification_process) as called:
            pub.verify_producer(self.c)
        self.assertEqual(called.call_args.kwargs['timeout_seconds'], pub.project_validation.VERIFIER_TIMEOUT)

    def write_config(self):
        self.config.write_text(json.dumps(self.c)); self.sha = pin(self.config)['sha256']

    def preview(self):
        return pub.run(self.config, self.sha, self.state, transport=self.api, push_transport=self.api.push)

    def start(self):
        return pub.run(self.config, self.sha, self.state, self.preview()['intentSha256'], transport=self.api, push_transport=self.api.push)

    def resume(self):
        return pub.run(self.config, self.sha, self.state, resume=True, transport=self.api, push_transport=self.api.push)

    def posts(self):
        return [call for call in self.api.calls if call[0] != 'GET']

    def test_preview_verifies_real_git_and_fixed_producer_protocol_without_network_or_state(self):
        result = self.preview()
        self.assertEqual(result['status'], 'preview')
        self.assertEqual(result['intent']['draftPayload'], pub.payload(self.c))
        self.assertEqual(result['intent']['expectedPreviousRef'], '')
        self.assertFalse(self.api.calls); self.assertFalse(self.api.pushes); self.assertFalse(self.state.exists())
        with self.assertRaises(Refused):
            pub.run(self.config, self.sha, self.state, '0' * 64, transport=self.api, push_transport=self.api.push)

    def test_success_binds_draft_identity_for_merge_without_editing_any_existing_pr(self):
        result = self.start()
        self.assertEqual(result['status'], 'draft-pr-verified', result)
        self.assertEqual(result['deliveryPull']['headSha'], self.c['source']['headSha'])
        self.assertTrue(result['deliveryPull']['markDraftReady'])
        self.assertTrue(result['pushResponseConfirmed']); self.assertTrue(result['draftResponseConfirmed'])
        self.assertFalse(result['mergeExecuted']); self.assertFalse(result['releaseExecuted'])
        self.assertEqual(len(self.api.pushes), 1); self.assertEqual(len(self.posts()), 1)
        for name, digest in result['evidenceFiles'].items():
            self.assertEqual(pin(self.state / name)['sha256'], digest)
        self.assertEqual(self.resume()['status'], 'draft-pr-verified')
        self.assertEqual(len(self.api.pushes), 1); self.assertEqual(len(self.posts()), 1)
        with self.assertRaises(Refused):
            self.start()

    def test_existing_branch_or_pr_is_never_reused_or_changed(self):
        for index in range(2):
            self.state = self.case / ('existing%d' % index); self.api = FakeGitHub(self)
            if index:
                self.api.pr = self.api.pull()
            else:
                self.api.remote = self.c['source']['headSha']
            self.assertEqual(self.start()['status'], 'stopped-not-ready')
            self.assertFalse(self.api.pushes); self.assertFalse(self.posts())

    def test_source_tree_diff_and_producer_drift_refused_before_effects(self):
        original = copy.deepcopy(self.c)
        for field in ('headSha', 'treeSha', 'baseSha', 'diffSha256'):
            self.c = copy.deepcopy(original); self.c['source'][field] = 'e' * len(self.c['source'][field]); self.write_config()
            with self.subTest(field=field), self.assertRaises(Refused):
                self.preview()
        self.c = original; self.write_config()
        (self.producer / 'lib/flow_workflow.py').write_text('changed')
        with self.assertRaises(Refused):
            self.preview()
        self.assertFalse(self.api.calls)

    def test_producer_receipt_cannot_be_replaced_by_unbound_assertions(self):
        value = json.loads(self.producer_config.read_bytes())
        value['fixtureResult']['candidateReceipt']['sha256'] = 'e' * 64
        self.producer_config.write_text(json.dumps(value)); self.c['candidateVerification']['config'] = pin(self.producer_config); self.write_config()
        with self.assertRaisesRegex(Refused, 'exact private candidate'):
            self.preview()

    def test_head_or_base_race_after_push_stops_before_draft(self):
        for index in range(2):
            self.state = self.case / ('race%d' % index); self.api = FakeGitHub(self)
            def mutate(route, value):
                if self.api.remote:
                    if index == 0 and '/git/ref/heads/' in route:
                        value['object']['sha'] = 'e' * 40
                    if index == 1 and '/git/matching-refs/' in route:
                        value[0]['object']['sha'] = 'e' * 40
                return value
            self.api.mutate = mutate
            self.assertEqual(self.start()['status'], 'stopped-not-ready')
            self.assertEqual(len(self.api.pushes), 1); self.assertFalse(self.posts())

    def test_unknown_push_stops_and_read_only_resume_never_retries(self):
        self.api.fail_push = True
        self.assertEqual(self.start()['status'], 'effect-outcome-unknown')
        self.assertEqual(self.resume()['status'], 'branch-not-observed-no-retry')
        self.assertEqual(len(self.api.pushes), 1); self.assertFalse(self.posts())

    def test_reconciled_push_allows_only_exact_explicit_unattempted_draft_continuation(self):
        self.api.fail_push = self.api.push_then_fail = True
        self.assertEqual(self.start()['status'], 'effect-outcome-unknown')
        result = self.resume()
        self.assertEqual(result['status'], 'branch-observed-no-pr-no-retry')
        self.assertFalse(result['pushResponseConfirmed']); self.assertFalse(self.posts())
        refusal = pub.run(self.config, self.sha, self.state, draft_authorization='0' * 64, transport=self.api)
        self.assertEqual(refusal['status'], 'stopped-not-ready'); self.assertFalse(self.posts())
        result = pub.run(self.config, self.sha, self.state, draft_authorization=result['draftContinuationSha256'], transport=self.api)
        self.assertEqual(result['status'], 'draft-pr-verified', result)
        self.assertFalse(result['pushResponseConfirmed']); self.assertTrue(result['draftResponseConfirmed'])
        self.assertEqual(len(self.api.pushes), 1); self.assertEqual(len(self.posts()), 1)

    def test_unknown_draft_never_replays_even_if_absent_and_can_reconcile(self):
        self.api.fail_draft = True
        self.assertEqual(self.start()['status'], 'effect-outcome-unknown')
        self.assertEqual(self.api.calls[-1][0], 'POST')
        result = self.resume()
        self.assertNotIn('draftContinuationSha256', result)
        denied = pub.run(self.config, self.sha, self.state,
                        draft_authorization=pub.local.digest(pub.local.encoded(pub.continuation(self.preview()['intent']))), transport=self.api)
        self.assertEqual(denied['status'], 'stopped-not-ready')
        self.api.pr = self.api.pull()
        result = self.resume()
        self.assertEqual(result['status'], 'draft-pr-verified')
        self.assertFalse(result['draftResponseConfirmed']); self.assertEqual(len(self.posts()), 1)

    def test_unknown_draft_resume_binds_the_requested_pr_number(self):
        self.api.fail_draft = self.api.draft_then_fail = True
        self.assertEqual(self.start()['status'], 'effect-outcome-unknown')
        def wrong_number(route, value):
            if route.endswith('/pulls/17'):
                value.update(number=18, html_url='https://github.com/fixture/project/pull/18')
            return value
        self.api.mutate = wrong_number
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertEqual(len(self.posts()), 1)

    def test_wrong_draft_body_base_head_or_id_does_not_pass(self):
        for index, mutate in enumerate((lambda v: v.update(body='changed'), lambda v: v.update(draft=False),
                                      lambda v: v['head'].update(sha='e' * 40), lambda v: v['base'].update(sha='e' * 40),
                                      lambda v: v.update(number=18, html_url='https://github.com/fixture/project/pull/18'),
                                      lambda v: v.update(maintainer_can_modify=True))):
            self.state = self.case / ('draft%d' % index); self.api = FakeGitHub(self)
            def change(route, value):
                if route.endswith('/pulls/17'):
                    mutate(value)
                return value
            self.api.mutate = change
            self.assertEqual(self.start()['status'], 'stopped-not-ready')
            self.assertEqual(len(self.posts()), 1)

    def test_no_effect_occurs_before_durable_reservation(self):
        original = pub.local.durable
        def failing(root, name, raw):
            if name == 'push-dispatch.json':
                raise OSError('fixture fsync failure')
            return original(root, name, raw)
        with patch.object(pub.local, 'durable', failing):
            self.assertEqual(self.start()['status'], 'effect-outcome-unknown')
        self.assertFalse(self.api.pushes); self.assertFalse(self.posts())

    def test_branch_grammar_and_lease_cannot_select_an_existing_expected_ref(self):
        for name in ('../main', 'main.lock/x', 'x/.hidden', 'x..y', 'x//y', '-x', 'x:', 'x\nmain', 'main'):
            self.c['branch'] = name; self.write_config()
            with self.subTest(branch=name), self.assertRaises(Refused):
                self.preview()
        self.assertFalse(self.api.calls)

    def test_clean_private_view_ignores_source_url_rewrites_and_push_hooks(self):
        self.git_run('config', 'url.https://unexpected.invalid/.insteadOf', 'https://github.com/')
        self.git_run('config', 'core.hooksPath', str(self.case / 'untrusted-hooks'))
        result = self.start(); self.assertEqual(result['status'], 'draft-pr-verified')
        argv, env = self.api.pushes[0]
        self.assertEqual(argv[-2], 'https://github.com/fixture/project.git')
        self.assertEqual(argv[-1], self.c['source']['headSha'] + ':refs/heads/' + self.c['branch'])
        self.assertEqual([v for v in argv if v.startswith('--force')], ['--force-with-lease=refs/heads/' + self.c['branch'] + ':'])
        self.assertNotIn('--force', argv); self.assertFalse(any(v.startswith('+') for v in argv))
        self.assertNotIn('unexpected.invalid', (self.state / 'push.git/config').read_text())
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
        self.assertFalse({'GH_TOKEN', 'GITHUB_TOKEN', 'GIT_TRACE', 'GIT_CONFIG_COUNT'} & set(env))

    def test_local_git_fixture_proves_empty_lease_create_only_and_no_fallback(self):
        target = self.case / 'remote.git'; self.git_run('init', '-q', '--bare', '--initial-branch=main', '--template=', str(target), cwd=self.case)
        self.state.mkdir()
        reader = pub.source_binding(self.c); view = pub.private_git_view(self.c, self.state, reader)
        argv, env = pub.push_command(self.c, view)
        # Test-only file transport substitutes only the destination and protocol allowlist.
        argv = [v.replace('protocol.https.allow=always', 'protocol.file.allow=always') for v in argv]
        argv[-2] = str(target); env['GIT_ALLOW_PROTOCOL'] = 'file'
        first = subprocess.run(argv, cwd=self.state, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = '*\t' + self.c['source']['headSha'] + ':refs/heads/' + self.c['branch'] + '\t[new branch]'
        self.assertIn(expected, first.stdout)
        # Simulate a concurrent different existing ref. Empty lease refuses even a fast-forward.
        self.git_run('--git-dir=' + str(target), 'update-ref', 'refs/heads/' + self.c['branch'], self.c['source']['baseSha'], cwd=self.case)
        raced = subprocess.run(argv, cwd=self.state, env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(raced.returncode, 0)
        self.assertEqual(self.git_run('--git-dir=' + str(target), 'rev-parse', 'refs/heads/' + self.c['branch'], cwd=self.case), self.c['source']['baseSha'])

    def test_fixed_github_transport_allows_only_generated_reads_and_new_draft(self):
        self.assertEqual(self.start()['status'], 'draft-pr-verified')
        transport = pub.PublicationTransport(self.c)
        for method, route, body in self.api.calls:
            transport.validate_request(method, route, body)
        refused = [
            ('PATCH', '/repos/fixture/project/pulls/1', {'body': 'unrequested edit'}),
            ('POST', '/repos/other/project/pulls', pub.payload(self.c)),
            ('POST', '/repos/fixture/project/pulls', dict(pub.payload(self.c), draft=False)),
            ('POST', '/repos/fixture/project/pulls/17/comments', {'body': 'extra comment'}),
            ('PUT', '/repos/fixture/project/pulls/17/merge', {'sha': self.c['source']['headSha']}),
            ('DELETE', '/repos/fixture/project/git/refs/heads/' + self.c['branch'], None),
            ('GET', 'https://unrelated.invalid/', None),
        ]
        for method, route, body in refused:
            with self.subTest(method=method, route=route), self.assertRaises(Refused):
                transport.validate_request(method, route, body)

    def test_real_push_transport_captures_fixed_argv_and_refuses_up_to_date_noop(self):
        self.state.mkdir()
        reader = pub.source_binding(self.c); view = pub.private_git_view(self.c, self.state, reader)
        fake_git = self.case / 'fake-git'
        capture = self.case / 'git-capture.json'
        expected = '*\t' + self.c['source']['headSha'] + ':refs/heads/' + self.c['branch'] + '\t[new branch]'
        def script(line, exit_code=0):
            fake_git.write_text('#!' + str(Path(sys.executable).resolve()) + '\nimport json,os,sys\n'
                'open(' + repr(str(capture)) + ',"w").write(json.dumps({"argv":sys.argv,"env":dict(os.environ)}))\n'
                'print(' + repr(line) + ')\nprint("fixture stderr",file=sys.stderr)\nsys.exit(' + str(exit_code) + ')\n')
            fake_git.chmod(0o700); self.c['source']['git'] = pin(fake_git)
        script(expected)
        with patch.dict(os.environ, {'GIT_TRACE': '1', 'GH_TOKEN': 'fixture-secret-not-real'}):
            response = pub.push(self.c, self.state, view)
        self.assertEqual(response['exitCode'], 0)
        captured = json.loads(capture.read_bytes())
        self.assertEqual(captured['argv'][1:], pub.push_command(self.c, view)[0][1:])
        self.assertFalse({'GIT_TRACE', 'GH_TOKEN'} & set(captured['env']))
        for line, code in (('=\tHEAD:refs/heads/x\t[up to date]', 0), ('rejected', 1)):
            script(line, code)
            with self.assertRaises(pub.gh.GitHubError) as failure:
                pub.push(self.c, self.state, view)
            self.assertIn(line, failure.exception.observation['stdout'])
            self.assertIn('fixture stderr', failure.exception.observation['stderr'])

    def test_effect_record_drift_stops_before_reconciliation_or_new_effect(self):
        self.api.fail_push = True
        self.start()
        path = self.state / 'push-dispatch.json'
        value = json.loads(path.read_bytes()); value['expectedPreviousRef'] = self.c['source']['baseSha']
        path.write_text(json.dumps(value)); before = len(self.api.calls)
        result = self.resume()
        self.assertEqual(result['status'], 'stopped-not-ready')
        self.assertEqual(len(self.api.calls), before)
        self.assertEqual(len(self.api.pushes), 1)

    def test_installed_preview_uses_fixed_pinned_producer_without_source_imports(self):
        result = subprocess.run([str(Path(sys.executable).resolve()), '-IBS', str(self.installation / 'aim-flow.py'),
            'publish-github', '--config', str(self.config), '--config-sha256', self.sha, '--state', str(self.state), '--preview'],
            cwd=self.case, env={'PATH': '/usr/bin:/bin', 'HOME': str(self.case), 'LC_ALL': 'C'}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'preview'); self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
