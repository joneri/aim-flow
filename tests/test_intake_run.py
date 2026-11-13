"""Real committed intake with synthetic run boundaries; no paid or project effects."""
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import test_requirement_intake as fixtures
import flow_api_delivery as api
import flow_run as full
from aim_ci.owned_paths import Refused


class IntakeRunTests(fixtures.IntakeTests):
    def setUp(self):
        super().setUp()
        self.state = self.root / 'intake-run'
        self.base = {'source': self.source, 'requirement': self.requirement,
            'resourceCatalog': {'path': str(self.root/'resources.json'), 'sha256': 'r'},
            'campaignDirectory': str(self.root/'campaign'),
            'historicalBudget': {'directory': str(self.root/'budget'),
                                 'legacy': {'path': str(self.root/'legacy'/'ledger.json')}}}
        self.base_path = self.root/'base.json'
        self.base_path.write_bytes(api.encoded(self.base))
        self.base_sha = api.digest(self.base_path.read_bytes())
        for target, value in [('ROOT', self.control), ('prepare', self.prepare_child), ('run', self.run_child)]:
            p = patch.object(full, target, value); p.start(); self.addCleanup(p.stop)
        self.calls = []

    def prepare_child(self, path, sha, state, **options):
        config = json.loads(api.pinned({'path': str(path), 'sha256': sha}))
        self.assertEqual(config['editablePaths'], ['src/Carousel.ts', 'src/timer.js'])
        self.assertEqual(config['historicalBudget'], self.base['historicalBudget'])
        return {'config': {'path': str(path), 'sha256': sha}, 'state': str(state), **options}, {}

    def run_child(self, path, sha, state, authorization, **options):
        self.calls.append((authorization, options))
        if not options['verify_only']:
            state.mkdir(exist_ok=True)
        return {'status': 'synthetic-run-completed'}

    def invoke(self, authorization=None, **options):
        return full.run_requirement(self.base_path, self.base_sha, self.state, ['src/', 'scripts/'], authorization, **options)

    def test_preview_pins_derived_inputs_and_dispatches_existing_run(self):
        preview = self.invoke(review_executed=True)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.state/'run').exists())
        self.assertEqual(preview, self.invoke(review_executed=True))
        self.assertEqual(preview['intent']['runIntent']['review_executed'], True)
        child_hash = api.digest(api.encoded(preview['intent']['runIntent']))
        self.assertEqual(self.invoke(preview['intentSha256'], review_executed=True)['status'], 'synthetic-run-completed')
        self.assertEqual(self.calls[0][0], child_hash)
        self.invoke(review_executed=True, verify_only=True)
        self.assertIsNone(self.calls[-1][0])

    def test_changed_artifacts_or_missing_inputs_are_never_reconstructed(self):
        for name in ('intake.json', 'workflow-config.json', 'intake-grant.json', 'intake.lock'):
            with self.subTest(name=name):
                self.state = self.root / ('state-' + name)
                grant = self.invoke()
                target = self.state/name; target.unlink()
                with self.assertRaises((Refused, OSError)):
                    self.invoke(grant['intentSha256'])
                self.assertFalse(target.exists())
        self.assertFalse(self.calls)

    def test_mutated_report_config_base_and_options_refused_before_dispatch(self):
        preview = self.invoke()
        report_path = self.state/'intake.json'; original = report_path.read_bytes()
        report_path.write_bytes(original+b' ')
        with self.assertRaises(Refused): self.invoke(preview['intentSha256'])
        report_path.write_bytes(original)
        with self.assertRaises(Refused): self.invoke(preview['intentSha256'], review_executed=True)
        self.base['historicalBudget']['changed'] = True
        self.base_path.write_bytes(api.encoded(self.base)); self.base_sha = api.digest(self.base_path.read_bytes())
        with self.assertRaises(Refused): self.invoke(preview['intentSha256'])
        self.assertFalse(self.calls)

    def test_unknown_dispatch_never_replays_and_verify_never_repairs(self):
        preview = self.invoke()
        with patch.object(full, 'run', side_effect=OSError('unknown')) as dispatch:
            with self.assertRaises(OSError): self.invoke(preview['intentSha256'])
            self.assertEqual(self.invoke()['status'], 'blocked')
            with self.assertRaises(Refused): self.invoke(verify_only=True)
            self.assertEqual(dispatch.call_count, 1)

    def test_old_child_grants_do_not_authorize_intake(self):
        preview = self.invoke()
        old = api.digest(api.encoded(preview['intent']['runIntent']))
        with self.assertRaises(Refused): self.invoke(old)
        with self.assertRaises(Refused): self.invoke(verify_only=True)
        self.assertFalse(self.calls)

    def test_no_paid_preview_rejects_credentials_and_transport(self):
        for option in ({'key_file': self.root/'never-read'}, {'transport': object()}, {'verify_only': True}):
            with self.assertRaises(Refused): self.invoke(**option)
            self.assertFalse(self.state.exists())
        self.invoke()
        with self.assertRaises(Refused): self.invoke(transport=object())
        self.assertFalse(self.calls)

for name in dir(fixtures.IntakeTests):
    if name.startswith('test_') and name not in IntakeRunTests.__dict__:
        setattr(IntakeRunTests, name, None)


# Exercise the real existing run/receipt chain as well as the real retrieval tests
# above. Only retrieval is synthetic here to reuse the established child fixture.
import test_flow_run as run_fixtures
import flow_requirement_intake as intake
from aim_ci import project_container as container

class IntakeChildIntegrationTests(run_fixtures.FullRunTests):
    def test_bridge_drives_and_verifies_existing_child_without_manual_config_extraction(self):
        state = self.case/'intake-bridge'
        selected = {name: self.wc[name] for name in ('projectDocuments', 'onboardingSources', 'reviewSources', 'editablePaths')}
        derived = {'format': intake.FORMAT, 'status': 'selected', 'source': self.wc['source'],
                   'requirement': self.wc['requirement'], 'selection': selected}
        self.fs = state/'run'; self.ws = self.fs/'initial'; self.ps = self.fs/'validation'
        with patch.object(intake, 'derive', return_value=derived), patch.object(container, 'execute', side_effect=self.execute_fake):
            preview = full.run_requirement(self.wpath, self.wsha, state, ['web'])
            result = full.run_requirement(self.wpath, self.wsha, state, ['web'], preview['intentSha256'], transport=self.post_all)
            self.assertEqual(result['status'], 'local-validation-passed')
            self.assertEqual(result['intakeEvidence']['workflowState'], str(self.fs))
            self.assertEqual(result['intakeEvidence']['workflowConfig'], full.work.pin(state/'workflow-config.json'))
            self.assertEqual(full.run_requirement(self.wpath, self.wsha, state, ['web'], verify_only=True), result)
        self.assertEqual(len(self.container_calls), 1)
        self.assertEqual(self.wcalls, ['responses/input_tokens', 'responses']*3)

for name in dir(run_fixtures.FullRunTests):
    if name.startswith('test_') and name not in IntakeChildIntegrationTests.__dict__:
        setattr(IntakeChildIntegrationTests, name, None)

if __name__ == '__main__': unittest.main()
