"""Actual local acceptance/adapter chain; all provider/container/GitHub effects injected."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
import zlib
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import flow_delivery_chain as chain
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused

AVAILABLE = importlib.util.find_spec('test_flow_run_executed') is not None
if AVAILABLE:
    import test_flow_run_executed as fixtures
    Base = fixtures.ExecutedRunTests
else:
    Base = unittest.TestCase

WORKFLOW = b'name: Synthetic main-push fixture\non: push\njobs: {}\n'
MERGED = 'e' * 40
REPOSITORY = {'id': 123, 'full_name': 'fixture/project'}


class GitHubFixture:
    def __init__(self, case):
        self.case = case; self.source = case.source
        self.calls = []; self.effects = []; self.branch = False; self.created = False
        self.draft = True; self.merged = False; self.pr_pending = True; self.ci_pending = True
        self.unknown_push = self.unknown_draft = self.unknown_merge = False
        self.mutate = lambda route, value: value

    def push(self, config, state, view):
        self.case.assertTrue((state / 'push-dispatch.json').exists())
        self.effects.append('push'); self.branch = True
        if self.unknown_push:
            raise chain.delivery.GitHubError('Synthetic unknown push', {'exitCode': 128, 'stderr': 'offline fixture'})
        return {'exitCode': 0, 'stdout': '*\t' + self.source['headSha'] + ':refs/heads/' + self.case.dc['branch'] + '\t[new branch]\n'}

    def pull(self):
        body = (self.case.ds / 'draft-body.md').read_text()
        return {'number': 17, 'node_id': 'PR_chain_fixture', 'state': 'closed' if self.merged else 'open',
            'draft': self.draft, 'merged': self.merged, 'mergeable': True,
            'mergeable_state': 'draft' if self.draft else 'clean', 'maintainer_can_modify': False,
            'merge_commit_sha': MERGED if self.merged else None,
            'title': self.case.dc['title'], 'body': body, 'html_url': 'https://github.com/fixture/project/pull/17',
            'head': {'sha': self.source['headSha'], 'ref': self.case.dc['branch'], 'repo': REPOSITORY},
            'base': {'sha': self.source['baseSha'], 'ref': 'main', 'repo': REPOSITORY}}

    def workflow_run(self):
        return {'id': 777, 'run_attempt': 1, 'workflow_id': 555, 'path': '.github/workflows/check.yml',
            'event': 'push', 'head_sha': MERGED, 'head_branch': 'main', 'repository': REPOSITORY,
            'head_repository': REPOSITORY, 'status': 'in_progress' if self.ci_pending else 'completed',
            'conclusion': None if self.ci_pending else 'success'}

    def __call__(self, method, route, body=None):
        self.calls.append((method, route, body))
        path = urlsplit(route).path.removeprefix('/repos/fixture/project')
        if method == 'POST' and path == '/pulls':
            self.case.assertTrue((self.case.ds / 'publication' / 'draft-dispatch.json').exists())
            self.effects.append('draft'); self.created = True
            self.case.assertTrue(body['draft']); self.case.assertFalse(body['maintainer_can_modify'])
            if self.unknown_draft:
                raise chain.delivery.GitHubError('Synthetic unknown draft', {'exitCode': 1, 'stderr': 'offline fixture'})
            value = self.pull()
        elif method == 'POST' and route == 'graphql':
            self.case.assertTrue((self.case.ds / 'merge' / 'ready-dispatch.json').exists())
            self.effects.append('ready'); self.draft = False
            value = {'data': {'markPullRequestReadyForReview': {'pullRequest': {'id': 'PR_chain_fixture', 'isDraft': False}}}}
        elif method == 'PUT' and path == '/pulls/17/merge':
            self.case.assertTrue((self.case.ds / 'merge' / 'merge-dispatch.json').exists())
            self.case.assertEqual(body, {'sha': self.source['headSha'], 'merge_method': 'merge'})
            self.effects.append('merge'); self.merged = True
            if self.unknown_merge:
                raise chain.delivery.GitHubError('Synthetic unknown merge', {'exitCode': 1, 'stderr': 'offline fixture'})
            value = {'merged': True, 'sha': MERGED}
        elif method != 'GET':
            raise AssertionError('Unexpected mutation ' + method + ' ' + route)
        elif not path:
            value = REPOSITORY
        elif path == '/git/ref/heads/main':
            value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': MERGED if self.merged else self.source['baseSha']}}
        elif path.startswith('/git/matching-refs/heads/'):
            value = [{'ref': 'refs/heads/' + self.case.dc['branch'], 'object': {'type': 'commit', 'sha': self.source['headSha']}}] if self.branch else []
        elif path == '/pulls':
            value = [self.pull()] if self.created else []
        elif path == '/pulls/17':
            value = self.pull()
        elif path == '/commits/' + self.source['headSha'] + '/check-runs':
            value = {'total_count': 1, 'check_runs': [{'id': 11, 'name': 'Build', 'app': {'id': 55},
                'head_sha': self.source['headSha'], 'status': 'in_progress' if self.pr_pending else 'completed',
                'conclusion': None if self.pr_pending else 'success'}]}
        elif path == '/commits/' + self.source['headSha'] + '/status':
            value = {'sha': self.source['headSha'], 'repository': REPOSITORY, 'total_count': 0, 'statuses': []}
        elif path == '/git/commits/' + MERGED:
            value = {'sha': MERGED, 'tree': {'sha': self.source['treeSha']},
                'parents': [{'sha': self.source['baseSha']}, {'sha': self.source['headSha']}]}
        elif path == '/contents/.github/workflows/check.yml':
            value = {'path': '.github/workflows/check.yml', 'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(WORKFLOW).decode()}
        elif path == '/actions/workflows/check.yml':
            value = {'id': 555, 'path': '.github/workflows/check.yml', 'state': 'active'}
        elif path == '/actions/workflows/555/runs':
            value = {'total_count': 1, 'workflow_runs': [self.workflow_run()]}
        elif path == '/actions/runs/777':
            value = self.workflow_run()
        elif path == '/actions/runs/777/attempts/1/jobs':
            value = {'total_count': 1, 'jobs': [{'id': 888, 'name': 'Frontend build', 'run_id': 777, 'run_attempt': 1,
                'head_sha': MERGED, 'status': 'completed', 'conclusion': 'success'}]}
        elif path == '/actions/runs/777/artifacts':
            created = datetime.now(timezone.utc) - timedelta(hours=1)
            value = {'total_count': 1, 'artifacts': [{'id': 999, 'name': 'frontend-' + MERGED + '-1', 'size_in_bytes': 1000,
                'expired': False, 'digest': 'sha256:' + 'd' * 64, 'created_at': created.isoformat(),
                'expires_at': (created + timedelta(days=7)).isoformat(),
                'workflow_run': {'id': 777, 'head_sha': MERGED, 'head_branch': 'main', 'repository_id': 123, 'head_repository_id': 123}}]}
        else:
            raise AssertionError('Unexpected read ' + route)
        return self.mutate(route, copy.deepcopy(value))


@unittest.skipUnless(AVAILABLE, 'Requires integrated four-call outer-run component')
class DeliveryChainTests(Base):
    def setUp(self):
        super().setUp()
        for module in (chain, chain.publication, chain.delivery):
            root_patch = patch.object(module, 'ROOT', self.installed); root_patch.start(); self.addCleanup(root_patch.stop)
        self.delivery_pending = True
        with patch.object(container, 'execute', side_effect=self.execute_with_artifacts):
            result = self.full(self.full()['intentSha256'], transport=self.post_all)
        self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
        self.local_result = result
        initial = json.loads((self.fs / 'initial-result.json').read_bytes()); self.source = initial['source']
        self.ds = self.case / 'delivery-chain'
        gh = self.case / 'no-network-gh'; gh.write_text('#!/bin/sh\nexit 91\n'); gh.chmod(0o700)
        self.dc = {'format': chain.FORMAT, 'schemaVersion': 1, 'manifestSha256': self.release['manifestSha256'],
            'localRun': {'config': self.pin(self.wpath), 'state': str(self.fs), 'grant': self.pin(self.fs / 'grant.json'), 'result': self.pin(self.fs / 'result.json')},
            'repository': {'name': 'fixture/project', 'id': 123}, 'baseSha': self.source['baseSha'], 'baseRef': 'main',
            'branch': 'flow/owned-chain', 'title': 'Apply the Flow requirement candidate', 'bodyScope': chain.BODY_SCOPE,
            'requiredPrChecks': [{'name': 'Build', 'appId': 55}], 'requiredPrStatuses': [],
            'workflow': {'path': '.github/workflows/check.yml', 'sha256': chain.api.digest(WORKFLOW),
                'requiredJobs': [{'key': 'frontend-build', 'name': 'Frontend build'}],
                'artifact': {'nameTemplate': 'frontend-{sha}-{attempt}', 'retentionDays': 7}},
            'transport': {'gh': self.pin(gh), 'home': str(self.case), 'configDirectory': str(self.case), 'timeoutSeconds': 30}}
        self.dp = self.case / 'delivery-chain.json'; self.save_delivery()
        self.github = GitHubFixture(self)

    def save_delivery(self):
        self.dp.write_bytes(chain.api.encoded(self.dc)); self.dsha = self.pin(self.dp)['sha256']

    def deliver(self, authorization=None, **options):
        return chain.run(self.dp, self.dsha, self.ds, authorization, publication_transport=self.github,
                         push_transport=self.github.push, github_transport=self.github, **options)

    def start_delivery(self):
        return self.deliver(self.deliver()['intentSha256'])

    def test_exact_new_grant_runs_real_receipt_chain_through_pending_pr_and_main_ci(self):
        count = list(self.wcalls); containers = len(self.container_calls)
        preview = self.deliver()
        self.assertFalse(self.ds.exists()); self.assertFalse(self.github.calls)
        self.assertIn('not human review', preview['intent']['draftBody'])
        self.assertIn('remain pending', preview['intent']['draftBody'])
        self.assertNotIn(str(self.case), preview['intent']['draftBody'])
        self.assertEqual(preview['intent']['pendingDeliveryObligations'], self.local_result['pendingDeliveryObligations'])
        with self.assertRaises(Refused):
            self.deliver(json.loads((self.fs / 'grant.json').read_bytes())['intentSha256'])
        self.assertFalse(self.ds.exists())
        pending = self.deliver(preview['intentSha256'])
        self.assertEqual(pending['status'], 'awaiting-pr-readiness', pending)
        self.assertEqual(self.github.effects, ['push', 'draft'])
        self.assertFalse((self.ds / 'merge-dispatch.json').exists())
        self.github.pr_pending = False
        observed = self.deliver(observe=True)
        self.assertEqual(observed['status'], 'pr-ready-awaiting-granted-continuation', observed)
        self.assertEqual(self.github.effects, ['push', 'draft'])
        pending = self.deliver(continue_run=True)
        self.assertEqual(pending['status'], 'awaiting-main-push', pending)
        self.assertEqual(self.github.effects, ['push', 'draft', 'ready', 'merge'])
        self.github.ci_pending = False
        final = self.deliver(observe=True)
        self.assertEqual(final['status'], 'main-push-handoff-ready', final)
        self.assertEqual((final['mergedSha'], final['runId'], final['runAttempt'], final['artifact']['id']), (MERGED, 777, 1, 999))
        self.assertEqual(final['pendingDeliveryObligations'], self.local_result['pendingDeliveryObligations'])
        self.assertFalse(final['productionDeployed']); self.assertFalse(final['productAccepted']); self.assertFalse(final['releaseExecuted'])
        self.assertEqual(final['mergedTree'], self.source['treeSha'])
        self.assertEqual(final['phaseGrant'], self.pin(self.ds / 'grant.json'))
        self.assertEqual(final['localRunReceipt'], self.dc['localRun']['result'])
        publication_result = chain.verify_receipt(final['publicationReceipt'])
        self.assertEqual(final['pushResponseConfirmed'], publication_result['pushResponseConfirmed'])
        self.assertEqual(final['draftResponseConfirmed'], publication_result['draftResponseConfirmed'])
        self.assertEqual(self.github.effects, ['push', 'draft', 'ready', 'merge'])
        self.assertEqual(self.wcalls, count); self.assertEqual(len(self.container_calls), containers)
        chain.verify_receipt(final['mainPushReceipt'])
        # Even a successful main workflow cannot establish acceptance for other tree bytes.
        self.github.mutate = lambda route, value: ({**value, 'tree': {'sha': 'f' * 40}}
            if '/git/commits/' in route else value)
        stopped = self.deliver(observe=True)
        self.assertEqual(stopped['status'], 'blocked-no-retry', stopped)
        self.assertIn('Merged main tree differs', stopped['error']['message'])
        self.assertEqual(self.github.effects, ['push', 'draft', 'ready', 'merge'])

    def test_unresolved_review_or_tampered_source_cannot_be_substituted_by_result_boolean(self):
        review = self.fs / 'execution-review' / 'review.json'; original = review.read_bytes()
        value = json.loads(original); value['findings'] = ['unresolved defect']; value['blockers'] = ['unresolved defect']
        review.write_bytes(chain.api.encoded(value))
        with self.assertRaises(Refused): self.deliver()
        review.write_bytes(original)
        source = Path(self.source['checkout']) / 'feature.js'; original = source.read_bytes(); source.write_text('unreviewed replacement')
        with self.assertRaises(Refused): self.deliver()
        source.write_bytes(original)
        self.assertFalse(self.github.calls); self.assertFalse(self.ds.exists())

    def test_invalid_ci_metadata_and_existing_pr_are_not_authorized_by_new_chain(self):
        self.dc['workflow']['path'] = '.github/workflows/../outside.yml'; self.save_delivery()
        with self.assertRaises(Refused): self.deliver()
        self.assertFalse(self.github.effects)
        self.dc['workflow']['path'] = '.github/workflows/check.yml'; self.save_delivery()
        self.github.branch = self.github.created = True
        # Existing target cannot be adopted even though the fixture has matching source/text.
        self.ds.mkdir(); (self.ds / 'draft-body.md').write_text('Existing unrelated draft')
        self.ds = self.case / 'fresh-delivery-chain'
        # Preflight refuses the existing branch before it asks for an existing body.
        result = self.start_delivery()
        self.assertEqual(result['status'], 'publication-pending-or-blocked', result)
        self.assertEqual(result['childStatus'], 'stopped-not-ready'); self.assertFalse(self.github.effects)

    def test_unknown_outer_dispatch_is_never_replayed_and_missing_inputs_not_repaired(self):
        preview = self.deliver()
        real = chain.publication.run
        def lost(*args, **kwargs):
            if len(args) >= 4 and args[3] is not None:
                raise OSError('Synthetic loss before child state exists')
            return real(*args, **kwargs)
        with patch.object(chain.publication, 'run', side_effect=lost):
            result = self.deliver(preview['intentSha256'])
        self.assertEqual(result['status'], 'blocked-no-retry')
        again = self.deliver(continue_run=True)
        self.assertEqual(again['status'], 'publication-outcome-unknown'); self.assertFalse(self.github.effects)
        config = self.ds / 'publication-config.json'; config.unlink()
        result = self.deliver(continue_run=True)
        self.assertEqual(result['status'], 'blocked-no-retry'); self.assertFalse(config.exists())

    def test_unknown_push_can_only_continue_unattempted_draft_under_exact_phase_grant(self):
        self.github.unknown_push = True
        first = self.start_delivery()
        self.assertEqual(first['status'], 'publication-pending-or-blocked', first)
        self.assertEqual(self.github.effects, ['push'])
        self.deliver(observe=True)
        self.assertEqual(self.github.effects, ['push'])
        next_result = self.deliver(continue_run=True)
        self.assertEqual(next_result['status'], 'awaiting-pr-readiness', next_result)
        self.assertEqual(self.github.effects, ['push', 'draft'])

    def test_unrecorded_merge_child_is_not_adopted_or_given_an_outer_reservation(self):
        pending = self.start_delivery()
        self.assertEqual(pending['status'], 'awaiting-pr-readiness', pending)
        (self.ds / 'merge').mkdir()
        self.github.pr_pending = False
        calls = list(self.github.calls)
        result = self.deliver(continue_run=True)
        self.assertEqual(result['status'], 'blocked-no-retry', result)
        self.assertIn('Unrecorded merge child', result['error']['message'])
        self.assertFalse((self.ds / 'merge-dispatch.json').exists())
        self.assertEqual(self.github.calls, calls)
        self.assertEqual(self.github.effects, ['push', 'draft'])

    def test_unknown_merge_can_only_be_reconciled_without_another_effect(self):
        self.github.pr_pending = self.github.ci_pending = False; self.github.unknown_merge = True
        result = self.start_delivery()
        self.assertEqual(result['status'], 'merge-pending-or-blocked', result)
        self.assertEqual(self.github.calls[-1][0], 'PUT')
        effects = list(self.github.effects)
        final = self.deliver(continue_run=True)
        self.assertEqual(final['status'], 'main-push-handoff-ready', final)
        self.assertFalse(final['mergeResponseConfirmed'])
        self.assertEqual(self.github.effects, effects)

    def test_installed_preview_reconstructs_acceptance_without_extra_external_work(self):
        command = [str(Path(sys.executable).resolve()), '-IBS', str(self.installed / 'aim-flow.py'), 'deliver',
            '--config', str(self.dp), '--config-sha256', self.dsha, '--state', str(self.ds), '--preview']
        result = subprocess.run(command, cwd=self.case, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout); self.assertEqual(value['status'], 'awaiting-authorization')
        self.assertFalse(self.ds.exists()); self.assertFalse(self.github.calls)


if AVAILABLE:
    for name in dir(Base):
        if name.startswith('test_') and name not in DeliveryChainTests.__dict__:
            setattr(DeliveryChainTests, name, None)
del Base  # Do not rediscover the imported fixture class as an additional suite.


class LargeArtifactDeliveryTests(DeliveryChainTests):
    def full(self, *args, **options):
        if not getattr(self, '_large_initialized', False):
            source = self.checkout / 'web/generate.js'
            source.write_text(source.read_text() + 'await page.screenshot({path: "results/other.png"});\n')
            self.git_run('add', '.'); self.git_run('commit', '-qm', 'Second synthetic captured image')
            self.commit = self.git_run('rev-parse', 'HEAD'); self.wc['source']['commit'] = self.commit
            self.review_body['artifactSelection'].append({'path': 'web/results/other.png', 'mediaType': 'image/png',
                'reason': 'Second exact screenshot output written by selected test source',
                'sources': [{'path': 'candidate/web/generate.js', 'startLine': 4, 'endLine': 4}]})
            self.wpath.write_bytes(chain.api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
            self._large_initialized = True
        return super().full(*args, **options)

    def execute_with_artifacts(self, root, state, intent, reader):
        result = super().execute_with_artifacts(root, state, intent, reader)
        # Valid synthetic PNG with a large ancillary chunk; no browser is claimed.
        data = b'note\0' + b'x' * 3500000
        kind = b'tEXt'
        chunk = len(data).to_bytes(4, 'big') + kind + data + zlib.crc32(kind + data).to_bytes(4, 'big')
        png = fixtures.PNG[:-12] + chunk + fixtures.PNG[-12:]
        for row in result['report']['artifacts']:
            raw = png if row['mediaType'] == 'image/png' else base64.b64decode(row['base64'])
            row.update(bytes=len(raw), sha256=chain.api.digest(raw), base64=base64.b64encode(raw).decode())
            result['report']['outputs']['project/' + row['path']].update(bytes=row['bytes'], sha256=row['sha256'])
        (state / 'container.stdout').write_bytes(chain.api.encoded(result['report']))
        result['controlFiles']['container.stdout'] = self.pin(state / 'container.stdout')['sha256']
        return result

    def test_full_valid_local_capture_above_github_metadata_bound_uses_verified_reference(self):
        self.assertGreater((self.fs / 'result.json').stat().st_size, chain.delivery.MAX_METADATA)
        aggregate = self.fs / 'validation' / 'container-attempt' / 'result.json'
        self.assertGreater(aggregate.stat().st_size, chain.delivery.MAX_METADATA)
        preview = self.deliver()
        result = self.deliver(preview['intentSha256'])
        self.assertEqual(result['status'], 'awaiting-pr-readiness', result)
        proof = self.ds / 'local-proof.json'
        self.assertEqual(preview['intent']['localProofSha256'], self.pin(proof)['sha256'])
        references = json.loads(proof.read_bytes())
        self.assertEqual(references['evidence']['aggregate'], self.pin(aggregate))
        declaration = json.loads((self.ds / 'publication-config.json').read_bytes())
        self.assertEqual(declaration['evidence']['aggregate'], self.pin(proof))
        self.assertLess(proof.stat().st_size, chain.delivery.MAX_METADATA)
        self.assertEqual(self.github.effects, ['push', 'draft'])
        calls = list(self.github.calls)
        proof.write_bytes(proof.read_bytes() + b' ')
        stopped = self.deliver(continue_run=True)
        self.assertEqual(stopped['status'], 'blocked-no-retry', stopped)
        self.assertEqual(self.github.calls, calls)


for name in dir(DeliveryChainTests):
    if name.startswith('test_') and name not in LargeArtifactDeliveryTests.__dict__:
        setattr(LargeArtifactDeliveryTests, name, None)

if __name__ == '__main__': unittest.main()
