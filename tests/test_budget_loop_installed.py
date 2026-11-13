"""Fresh installed source-free budget loop; only provider/container are synthetic."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_repair_loop as loop
import flow_resume_candidate as resume
from aim_ci import project_container as container
import test_repair_run as fixtures


class InstalledBudgetLoopTests(fixtures.BoundedRepairRunTests):
    def test_schema_two_consumes_existing_candidate_then_two_reviews_without_an_extra_repair(self):
        with patch.object(loop, 'ROOT', self.installed):
            repaired = self.repaired(self.repaired()['intentSha256'], transport=self.post)
            self.assertEqual(repaired['status'], 'candidate-prepared')
            rc = {'format': resume.FORMAT, 'schemaVersion': 1,
                'manifestSha256': self.wc['manifestSha256'], 'workflowConfig': repaired['derivedWorkflowConfig'],
                'workflowState': str(self.rs), 'producerKind': 'repair-candidate',
                'producerController': {'path': str(self.installed), 'manifestSha256': self.wc['manifestSha256']},
                'repairConfig': self.pin(self.rpath), 'python': self.rc['python'],
                'producerVerificationTimeoutSeconds': None,
                'reviewMaxOutputTokens': 8192, 'reviewMaxOperationNanoUsd': 1_000_000_000,
                'executionMaxOutputTokens': 8192, 'executionMaxOperationNanoUsd': 1_000_000_000}
            rp = self.case / 'loop-resume.json'; rp.write_bytes(api.encoded(rc))
            config = {'format': loop.FORMAT, 'schemaVersion': 2, 'manifestSha256': self.wc['manifestSha256'],
                'resumeConfig': self.pin(rp), 'repairTemplate': self.pin(self.rpath),
                'maximumNewGenerations': 2, 'repairContextPaths': []}
            cp = self.case / 'loop.json'; cp.write_bytes(api.encoded(config)); pin = self.pin(cp)
            state = self.case / 'budget-loop'
            preview = loop.run(cp, pin['sha256'], state)
            self.assertFalse(state.exists()); self.assertEqual(preview['status'], 'awaiting-authorization')
            before = len(self.repair_calls)
            with patch.object(self, 'ps', state / 'review-0/validation'), patch.object(
                    container, 'execute', side_effect=self.execute_with_artifacts):
                result = loop.run(cp, pin['sha256'], state, preview['intentSha256'], transport=self.post)
            self.assertEqual(result['status'], 'local-feature-evidence-reviewed', result)
            self.assertTrue(result['localFeatureAccepted']); self.assertEqual(result['paidGenerations'], 2)
            self.assertEqual(len(self.repair_calls), before)
            self.assertEqual(loop.run(cp, pin['sha256'], state, verify_only=True), result)
            self.assertFalse(result['publicationAuthorized'])

    def test_reused_source_review_then_real_failed_receipt_enters_loop_repair_diagnostics(self):
        with patch.object(loop, 'ROOT', self.installed):
            initial = self.finish()
            self.assertTrue(initial['localFeatureAccepted'])
            cfg = json.loads((self.fs / 'resume-config.json').read_bytes())
            old_source = self.ps / 'review-attempt'
            cfg['sourceReviewReuse'] = {'controller': {'path': str(self.installed),
                'manifestSha256': cfg['manifestSha256']},
                'config': self.pin(self.ps / 'review-config.json'), 'state': str(old_source)}
            rp = self.case / 'reuse-loop-resume.json'; rp.write_bytes(api.encoded(cfg))
            config = {'format': loop.FORMAT, 'schemaVersion': 2, 'manifestSha256': cfg['manifestSha256'],
                'resumeConfig': self.pin(rp), 'repairTemplate': self.pin(self.rpath),
                'maximumNewGenerations': 4, 'repairContextPaths': ['scripts/legacy-test.mjs']}
            cp = self.case / 'reuse-loop.json'; cp.write_bytes(api.encoded(config)); pin = self.pin(cp)
            state = self.case / 'reuse-budget-loop'
            def fail(root, attempt, intent, reader):
                result = self.execute_with_artifacts(root, attempt, intent, reader)
                result['status'] = 'failed'; result['report']['status'] = 'failed'
                result['containerCleanup'] = 'confirmed-absent'
                result['terminal'] = {'inspected': True, 'state': {'Running': False, 'Pid': 0, 'ExitCode': 1}}
                result['report']['stages'][-1].update(exitCode=1,
                    stderr='AssertionError\n at file:///work/project/scripts/legacy-test.mjs:1:1\n')
                for name, body in [('container.stdout', result['report']), ('container-terminal.json', result['terminal'])]:
                    (attempt / name).write_bytes(api.encoded(body))
                    result['controlFiles'][name] = self.pin(attempt / name)['sha256']
                return result
            observed = []
            def stop_at_repair_preflight(endpoint, payload):
                self.assertEqual(endpoint, 'responses/input_tokens')
                body = json.loads(payload['input']); observed.append(body)
                raise RuntimeError('Synthetic provider stop after real diagnostic preparation')
            preview = loop.run(cp, pin['sha256'], state)
            with patch.object(self, 'ps', state / 'review-0/validation'), patch.object(container, 'execute', side_effect=fail):
                value = loop.run(cp, pin['sha256'], state, preview['intentSha256'], transport=stop_at_repair_preflight)
            self.assertEqual(value['status'], 'blocked', value)
            self.assertEqual(len(observed), 1)
            self.assertFalse((state / 'review-0/validation/review-attempt').exists())
            derived = json.loads((state / 'repair-0-config.json').read_bytes())
            self.assertEqual(derived['rejectedReview']['state'], str(old_source))
            self.assertIn('failedValidation', derived)
            self.assertEqual(observed[0]['rejectedReviewDiagnostics']['failedExecution']['sourceLocations'][0]['path'],
                             'scripts/legacy-test.mjs')


for name in dir(fixtures.BoundedRepairRunTests):
    if name.startswith("test_") and name not in InstalledBudgetLoopTests.__dict__:
        setattr(InstalledBudgetLoopTests, name, None)
