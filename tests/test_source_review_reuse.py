"""Actual offline paid-review reconstruction; no API or project execution."""
import copy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
import test_candidate_review as fixtures
import flow_workspace_review as workspace
from aim_ci.docker_engine import DockerEngine
from aim_ci.owned_paths import Refused
api = fixtures.api


class SourceReuseTests(fixtures.ReviewTests):
    def setUp(self):
        super().setUp()
        self.c.update(purpose=workspace.PURPOSE,
            workspaceContext={'protectedPaths': ['feature.js'], 'stages': []})
        self.body['workspacePolicy'] = {'generatedOutputs': [], 'retainedArchiveInputs': [], 'unknowns': []}
        self.answer['output'][0]['content'][0]['text'] = json.dumps(self.body)
        self.save()

    def completed(self):
        prepared = workspace.prepare(self.config, self.config_sha, self.state)
        value = workspace.run(self.config, self.config_sha, self.state,
            api.digest(api.encoded(prepared[-1])), transport=self.post)
        self.assertEqual(value['status'], 'source-review-ready', value)
        spec = {'controller': {'path': str(self.installed), 'manifestSha256': self.c['manifestSha256']},
            'config': self.pin(self.config), 'state': str(self.state)}
        return spec, workspace.prepare(self.config, self.config_sha, self.case / 'unused-review')

    def test_same_disclosure_reuses_exact_paid_review_without_effects_or_old_writes(self):
        spec, prepared = self.completed()
        before = {p: p.read_bytes() for p in self.state.iterdir()}
        calls = list(self.calls)
        evidence, reused = workspace.reuse_completed(spec, prepared)
        self.assertEqual(evidence, json.loads((self.state / 'review.json').read_bytes()))
        self.assertEqual(reused['newProviderCalls'], 0)
        self.assertEqual(reused['evidence']['response.json'], self.pin(self.state / 'response.json'))
        self.assertEqual(calls, self.calls)
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.iterdir()})
        self.assertFalse((self.case / 'unused-review').exists())

    def test_any_disclosure_or_wire_instruction_change_requires_fresh_review(self):
        spec, prepared = self.completed()
        for index in (2, 3):
            changed = list(copy.deepcopy(prepared))
            changed[index]['unreviewed'] = 'different'
            with self.subTest(index=index), self.assertRaisesRegex(Refused, 'fresh review required'):
                workspace.reuse_completed(spec, changed)

    def test_paid_accounting_and_actual_response_are_required(self):
        spec, prepared = self.completed()
        path = Path(self.c['campaignDirectory']) / 'budget.json'
        original = path.read_bytes(); ledger = json.loads(original); ledger['requests'] = []
        path.write_bytes(api.encoded(ledger))
        with self.assertRaisesRegex(Refused, 'completed paid accounting'):
            workspace.reuse_completed(spec, prepared)
        path.write_bytes(original)
        response = self.state / 'response.json'; response.write_bytes(response.read_bytes() + b' ')
        with self.assertRaises(Refused):
            workspace.reuse_completed(spec, prepared)

    def test_rejected_or_incomplete_review_is_not_reused(self):
        self.answer['status'] = 'incomplete'
        prepared = workspace.prepare(self.config, self.config_sha, self.state)
        workspace.run(self.config, self.config_sha, self.state,
            api.digest(api.encoded(prepared[-1])), transport=self.post)
        spec = {'controller': {'path': str(self.installed), 'manifestSha256': self.c['manifestSha256']},
            'config': self.pin(self.config), 'state': str(self.state)}
        with self.assertRaisesRegex(Refused, 'completed paid accounting'):
            workspace.reuse_completed(spec, prepared)


for name in dir(fixtures.ReviewTests):
    if name.startswith('test_') and name not in SourceReuseTests.__dict__:
        setattr(SourceReuseTests, name, None)


class DockerSnapshotDeadlineTests(unittest.TestCase):
    def test_snapshot_has_longer_bound_ordinary_metadata_keeps_five_seconds(self):
        observed = []
        def request(endpoint, method, route, body, timeout):
            observed.append(timeout)
            return 201, b'{"Id":"sha256:example"}'
        engine = DockerEngine('unix:///tmp/synthetic.sock', request)
        engine._call('POST', '/commit?container=example', (201,), timeout_seconds=180)
        engine._call('POST', '/containers/create', (201,))
        self.assertEqual(observed, [180, 5])
        for timeout in (0, 181, True):
            with self.assertRaises(Refused):
                engine._call('POST', '/commit', (201,), timeout_seconds=timeout)
        self.assertEqual(observed, [180, 5])

    def test_unknown_snapshot_is_consumed_without_implicit_transport_retry(self):
        observed = []
        def request(*args):
            observed.append(args)
            raise TimeoutError()
        engine = DockerEngine('unix:///tmp/synthetic.sock', request)
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            engine._call('POST', '/commit', (201,), timeout_seconds=180)
        self.assertEqual(len(observed), 1)


class DiagnosticRunnerCompatibilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.old, self.new = (Path(tmp.name).resolve() / name for name in ('old', 'new'))
        for root in (self.old, self.new): (root / 'lib/aim_ci').mkdir(parents=True)
        self.relative = Path('lib/aim_ci/project_container_runner.cjs')
        self.before = b'qualified prefix\n' + workspace._LEGACY_SOURCE_IDENTITY_CHECK.encode() + b'qualified suffix\n'
        self.after = b'qualified prefix\n' + workspace._DIAGNOSTIC_SOURCE_IDENTITY_CHECK.encode() + b'qualified suffix\n'
        (self.old / self.relative).write_bytes(self.before); (self.new / self.relative).write_bytes(self.after)
        self.old_packet = {'requirement': {'original': 'unchanged task'}, 'candidate': {'commit': 'a' * 40},
            'workspaceContext': {'executionContext': {'bindings': {'runnerSha256': api.digest(self.before)},
                'contract': {'sourceCopies': 'exact boundary identity'}, 'initialEnvironment': {'X': 'same'}}}}
        self.new_packet = copy.deepcopy(self.old_packet)
        self.new_packet['workspaceContext']['executionContext']['bindings']['runnerSha256'] = api.digest(self.after)
        self.old_payload = {'input': json.dumps(self.old_packet, sort_keys=True), 'instructions': 'same strict review',
            'text': {'format': 'same schema'}}
        self.new_payload = {**copy.deepcopy(self.old_payload), 'input': json.dumps(self.new_packet, sort_keys=True)}
        p = patch.object(workspace.base, 'ROOT', self.new); p.start(); self.addCleanup(p.stop)

    def verify(self):
        return workspace.diagnostic_runner_compatibility(self.old_packet, self.new_packet,
            self.old_payload, self.new_payload, self.old)

    def test_only_exact_diagnostic_patch_is_recognized_without_changing_old_review(self):
        old = copy.deepcopy(self.old_packet); new = copy.deepcopy(self.new_packet)
        result = self.verify()
        self.assertEqual(result['oldRunnerSha256'], api.digest(self.before))
        self.assertEqual(result['newRunnerSha256'], api.digest(self.after))
        self.assertEqual(self.old_packet, old); self.assertEqual(self.new_packet, new)

    def test_other_runner_code_or_wrong_hash_is_never_equivalent(self):
        changed = self.after + b'changed behavior\n'; (self.new / self.relative).write_bytes(changed)
        self.new_packet['workspaceContext']['executionContext']['bindings']['runnerSha256'] = api.digest(changed)
        self.new_payload['input'] = json.dumps(self.new_packet, sort_keys=True)
        with self.assertRaisesRegex(Refused, 'exact qualified'): self.verify()
        (self.new / self.relative).write_bytes(self.after)
        with self.assertRaisesRegex(Refused, 'exact qualified'): self.verify()

    def test_no_source_requirement_environment_contract_or_instruction_drift_is_permitted(self):
        original = copy.deepcopy(self.new_packet)
        for mutate in (
                lambda p: p['candidate'].update(commit='b' * 40),
                lambda p: p['requirement'].update(original='different task'),
                lambda p: p['workspaceContext']['executionContext']['contract'].update(sourceCopies='weaker'),
                lambda p: p['workspaceContext']['executionContext']['initialEnvironment'].update(X='different')):
            self.new_packet = copy.deepcopy(original); mutate(self.new_packet)
            self.new_payload['input'] = json.dumps(self.new_packet, sort_keys=True)
            with self.assertRaisesRegex(Refused, 'any other disclosure'): self.verify()
        self.new_packet = original; self.new_payload['input'] = json.dumps(original, sort_keys=True)
        self.new_payload['instructions'] = 'different review'
        with self.assertRaisesRegex(Refused, 'any other disclosure'): self.verify()


if __name__ == '__main__': unittest.main()
