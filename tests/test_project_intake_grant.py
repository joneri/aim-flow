"""Project-bound intake grants with local fixtures and guarded provider doubles only."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from openai_budget import LIMIT, MODEL, Refused, fingerprint
from aim_ci.budget import (AMENDMENT_EXTRA, Budget, project_grant_backup, verified_history,
                           verify_amendment)
import test_operator_build as fixture_tests
from test_operator_build import digest, encode


class ProjectIntakeGrantTests(unittest.TestCase):
    setUp = fixture_tests.RootCandidateRecipeReviewTests.setUp
    installed = ROOT
    manifest_pin = '9' * 64
    actual_git = False
    fixture_git_reader = fixture_tests.RootCandidateRecipeReviewTests.fixture_git_reader
    local_task_fixture = fixture_tests.RootCandidateRecipeReviewTests.local_task_fixture
    write_task = fixture_tests.RootCandidateRecipeReviewTests.write_task
    exhaust_intake_budget = fixture_tests.RootCandidateRecipeReviewTests.exhaust_intake_budget
    live_intake_fixture = fixture_tests.RootCandidateRecipeReviewTests.live_intake_fixture
    liam_continuation_fixture = fixture_tests.RootCandidateRecipeReviewTests.liam_continuation_fixture
    replace_intake_response = fixture_tests.RootCandidateRecipeReviewTests.replace_intake_response

    def grant(self, base, *, extra=0):
        c = self.controller
        intent = {key: value for key, value in base['intent'].items() if key != 'budget'}
        data = self.budget.read()
        limit, calls = verified_history(self.runtime.state, data,
            (self.runtime.state / 'budget-established.json').read_bytes(), self.runtime.legacy.read_bytes())
        return {'version': 3, 'operation': c.local_task_key(self.runtime.repository, self.task['id']) + '/intake',
            'model': MODEL, 'extraNanoUsd': 0, 'extraGenerations': extra, 'maxOperationNanoUsd': 50_000_000,
            'maxLedgerNanoUsd': limit, 'maxLedgerCalls': calls + extra,
            'target': {key: intent['target'][key] for key in ('repository', 'branch', 'commit', 'tree')},
            'taskSha256': intent['task']['dataSha256'],
            'sourceSha256': fingerprint(json.loads(base['modelRequest']['input'])['files']),
            'scopeSha256': fingerprint({'intent': intent, 'state': str(self.runtime.state),
                                        'legacy': str(self.runtime.legacy)}),
            'requestSha256': fingerprint(base['modelRequest']),
            'originalLedgerSha256': digest(self.budget.path.read_bytes()),
            'markerSha256': digest((self.runtime.state / 'budget-established.json').read_bytes()),
            'legacyBytesSha256': digest(self.runtime.legacy.read_bytes())}

    def save_grant(self, grant):
        path = self.root / 'project-grant.json'
        path.write_bytes(encode(grant))
        return path, digest(path.read_bytes())

    @contextmanager
    def project(self, *, exhaust=False, extra=0):
        from aim_ci.offline import git
        task = self.local_task_fixture()
        profile = json.loads(self.profile.read_bytes())
        git(self.runtime.root, 'remote', 'set-url', 'origin', profile['target']['repository'])
        c = self.controller
        with self.fixture_git_reader(), patch.object(c, 'STATE', self.runtime.state), \
                patch.object(c, 'legacy_budget_path', return_value=self.runtime.legacy), \
                patch.object(c, 'github', side_effect=AssertionError('GitHub forbidden')), \
                patch.object(c, 'root_candidate', side_effect=AssertionError('Candidate forbidden')), \
                patch.object(c, 'human_review_pause', side_effect=AssertionError('Review forbidden')), \
                patch.object(c, 'publish_candidate', side_effect=AssertionError('Publication forbidden')), \
                patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')), \
                redirect_stdout(io.StringIO()):
            base = c.preview_root_admission(self.selection, root_task=task, intake_only=True)
            if exhaust:
                self.exhaust_intake_budget(base)
                base = c.preview_root_admission(self.selection, root_task=task, intake_only=True)
            grant = self.grant(base, extra=extra)
            selection = self.save_grant(grant)
            preview = c.preview_root_admission(self.selection, root_task=task, intake_only=True, live_grant=selection)
            yield dict(root_profile=self.selection, root_task=task, intake_only=True, live_grant=selection,
                       authorize_root_intent=preview['intentSha256']), base, preview, grant

    def test_generic_project_claims_before_credentials_and_reconciles_without_replay(self):
        c = self.controller
        with self.project() as (inputs, base, preview, grant):
            self.assertNotEqual(grant['target'], c.LIAM_INTAKE_TARGET)
            before = self.budget.path.read_bytes()
            selected = self.runtime.root / 'banner.txt'
            source = selected.read_bytes()
            calls = []

            def provider(endpoint, payload):
                calls.append(endpoint)
                self.assertEqual(self.budget.read()['requests'][-1]['projectGrant'], grant)
                return self.runtime.transport(endpoint, payload)

            def credentials(_):
                self.assertEqual(self.budget.read()['requests'][-1]['status'], 'reserved')
                return provider

            with patch.object(c, 'transport_from_key_file', side_effect=credentials):
                state = c.run_issue(**inputs)
            self.assertEqual(state['stage'], 'intake_proposed')
            self.assertFalse(state['intakeResult']['changeApplied'])
            self.assertFalse(state['productAccepted'])
            self.assertEqual(selected.read_bytes(), source)
            self.assertEqual(calls, ['responses/input_tokens', 'responses'])
            self.assertEqual((self.runtime.state / project_grant_backup(grant)).read_bytes(), before)
            self.assertEqual(self.budget.read()['maxCalls'], 16)
            self.assertEqual(self.budget.read()['limitNanoUsd'], LIMIT)
            self.assertFalse(preview['executionAuthorized'])
            self.assertEqual(preview['budget']['binding']['projectGrantSha256'], fingerprint(grant))
            with patch.object(c, 'transport_from_key_file', side_effect=AssertionError('Credential replay forbidden')):
                self.assertEqual(c.run_issue(**inputs, resume=True), state)
                with self.assertRaisesRegex(Refused, 'already dispatched'):
                    c.run_issue(**inputs)
            self.assertFalse((self.runtime.state/'intake-candidates').exists())

    def test_prior_consent_missing_grant_and_changed_project_fields_refuse(self):
        c = self.controller
        with self.project() as (inputs, base, _, grant), patch.object(c, 'transport_from_key_file') as key:
            before = self.budget.path.read_bytes()
            for consent in (None, base['intentSha256'], '0' * 64):
                with self.subTest(consent=consent), self.assertRaisesRegex(Refused, 'explicit consent|Consent must match'):
                    c.run_issue(**{**inputs, 'authorize_root_intent': consent})
            for field in ('repository', 'branch', 'commit', 'tree'):
                changed = deepcopy(grant)
                changed['target'][field] = ('https://github.com/other/repo.git' if field == 'repository' else
                                            'elsewhere' if field == 'branch' else 'a' * 40)
                with self.subTest(field=field), self.assertRaisesRegex(Refused, 'exact target'):
                    c.run_issue(**{**inputs, 'live_grant': self.save_grant(changed)})
            for field in ('taskSha256', 'sourceSha256', 'scopeSha256', 'requestSha256'):
                with self.subTest(field=field), self.assertRaises(Refused):
                    c.run_issue(**{**inputs, 'live_grant': self.save_grant({**grant, field: '0' * 64})})
            self.save_grant(grant)
            with self.assertRaisesRegex(Refused, 'Live root project execution is blocked'):
                c.run_issue(**{key: value for key, value in inputs.items() if key != 'live_grant'})
            key.assert_not_called()
            self.assertEqual(self.budget.path.read_bytes(), before)
            self.assertFalse((self.runtime.state/'local-tasks').exists())

    def test_ceiling_and_prefix_are_derived_from_existing_history(self):
        c = self.controller
        with self.project() as (inputs, _, _, grant), patch.object(c, 'transport_from_key_file') as key:
            for change in ({'extraNanoUsd': 1}, {'maxOperationNanoUsd': 1}, {'maxOperationNanoUsd': 50_000_001}, {'extraGenerations': 2}, {'extraGenerations': 1, 'maxLedgerCalls': 17},
                           {'maxLedgerNanoUsd': LIMIT + AMENDMENT_EXTRA}, {'maxLedgerCalls': 17},
                           {'originalLedgerSha256': '0' * 64}, {'legacyBytesSha256': '0' * 64},
                           {'markerSha256': '0' * 64}, {'operation': 'task-' + 'a' * 64 + '/intake'}):
                with self.subTest(change=change), self.assertRaises(Refused):
                    c.run_issue(**{**inputs, 'live_grant': self.save_grant({**grant, **change})})
            key.assert_not_called()
            self.assertEqual(self.budget.read()['requests'], [])

    def test_exhausted_history_needs_an_explicit_one_call_grant(self):
        c = self.controller
        with self.project(exhaust=True) as (inputs, _, preview, grant), patch.object(c, 'transport_from_key_file') as key:
            self.assertFalse(preview['budget']['canAdmitIntake'])
            with self.assertRaisesRegex(Refused, 'Shared budget cannot admit'):
                c.run_issue(**inputs)
            key.assert_not_called()
            extended = {**grant, 'extraGenerations': 1, 'maxLedgerCalls': 17}
            selection = self.save_grant(extended)
            approved = c.preview_root_admission(self.selection, root_task=inputs['root_task'],
                                                intake_only=True, live_grant=selection)
            self.assertTrue(approved['budget']['canAdmitIntake'])
            with patch.object(c, 'transport_from_key_file', return_value=self.runtime.transport):
                c.run_issue(**{**inputs, 'live_grant': selection, 'authorize_root_intent': approved['intentSha256']})
            self.assertEqual(len(self.budget.read()['requests']), 17)
            self.assertEqual(self.budget.read()['limitNanoUsd'], LIMIT)
            self.assertEqual(self.budget.read()['requests'][-1]['projectGrant']['extraNanoUsd'], 0)
            with self.assertRaisesRegex(Refused, 'Aggregate USD10'):
                self.budget.reserve('unrelated', approved['modelRequest'], 0)

    def test_actual_count_above_old_100k_ceiling_uses_only_approved_operation_hold(self):
        c = self.controller
        with self.project() as (inputs, _, _, grant):
            calls = []
            def provider(endpoint, payload):
                calls.append(endpoint)
                if endpoint == 'responses/input_tokens':
                    return {'input_tokens': 120000}
                response = self.runtime.transport(endpoint, payload)
                response['usage']['input_tokens'] = 120000
                return response
            with patch.object(c, 'transport_from_key_file', return_value=provider):
                result = c.run_issue(**inputs)
            self.assertEqual(result['stage'], 'intake_proposed')
            self.assertEqual(calls, ['responses/input_tokens', 'responses'])
            entry = self.budget.read()['requests'][-1]
            self.assertEqual(entry['inputTokens'], 120000)
            self.assertEqual(entry['reservedNanoUsd'], grant['maxOperationNanoUsd'])
            self.assertEqual(entry['reservationPolicy'], 'approved-operation-cap-v1')
            self.assertLessEqual(entry['actualNanoUsd'], entry['reservedNanoUsd'])

    def test_count_exceeding_granted_money_is_retained_without_generation_or_retry(self):
        c = self.controller
        with self.project() as (inputs, _, _, grant):
            calls = []
            def provider(endpoint, payload):
                calls.append(endpoint)
                self.assertEqual(endpoint, 'responses/input_tokens', 'Generation must not be dispatched')
                return {'input_tokens': 200000}
            with patch.object(c, 'transport_from_key_file', return_value=provider):
                with self.assertRaisesRegex(Refused, 'Actual token count exceeds'):
                    c.run_issue(**inputs)
            self.assertEqual(calls, ['responses/input_tokens'])
            entry = self.budget.read()['requests'][-1]
            self.assertEqual(entry['status'], 'unknown')
            self.assertEqual(entry['reservedNanoUsd'], grant['maxOperationNanoUsd'])
            self.assertEqual(entry['failureClass'], 'TokenPreflightRefused')
            with patch.object(c, 'transport_from_key_file', side_effect=AssertionError('No retry')):
                with self.assertRaisesRegex(Refused, 'already dispatched|cannot be resumed'):
                    c.run_issue(**inputs)

    def test_request_output_allowance_is_reviewed_financial_input_not_universal_8192_cap(self):
        from openai_budget import Campaign
        with self.project() as (_, _, preview, grant):
            request = {**preview['modelRequest'], 'input': 'large input\n' * 25000, 'max_output_tokens': 16000}
            grant = {**grant, 'requestSha256': fingerprint(request)}
            calls = []
            def provider(endpoint, payload):
                calls.append(endpoint)
                if endpoint == 'responses/input_tokens':
                    self.assertEqual(payload['input'], request['input'])
                    self.assertEqual(payload['text'], request['text'])
                    return {'input_tokens': 1000}
                self.assertEqual(payload, request)
                return {'model': MODEL, 'status': 'completed',
                        'usage': {'input_tokens': 1000, 'output_tokens': 12000}}
            campaign = Campaign(self.root / 'large-output-campaign', 'large-output')
            campaign.call('intake', request, self.budget.transport(grant['operation'], provider,
                amendment=grant, request=request))
            self.assertEqual(calls, ['responses/input_tokens', 'responses'])
            entry = self.budget.read()['requests'][-1]
            self.assertEqual(entry['maxOutputTokens'], 16000)
            self.assertEqual(entry['status'], 'completed')
            self.assertEqual(entry['reservedNanoUsd'], grant['maxOperationNanoUsd'])

    def test_claim_fences_parallel_token_counts_and_other_endpoints(self):
        from concurrent.futures import ThreadPoolExecutor
        with self.project() as (_, _, preview, grant):
            request = preview['modelRequest']
            payload = {key: request[key] for key in ('model', 'input', 'instructions', 'text')}
            calls = []

            def count():
                transport = self.budget.transport(grant['operation'], lambda endpoint, _: calls.append(endpoint) or
                    {'input_tokens': 1}, amendment=grant, request=request)
                try:
                    transport('responses/input_tokens', payload)
                except Refused:
                    return False
                return True

            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(sorted(executor.map(lambda _: count(), range(2))), [False, True])
            self.assertEqual(calls, ['responses/input_tokens'])
            transport = self.budget.transport(grant['operation'], lambda *_: self.fail('Unexpected endpoint'),
                                               amendment=grant, request=request)
            for endpoint, data in (('models', request), ('responses', {**request, 'input': 'changed'}),
                                    ('responses/input_tokens', {**payload, 'input': 'changed'})):
                with self.subTest(endpoint=endpoint), self.assertRaises(Refused):
                    transport(endpoint, data)

    def test_missing_credential_consumes_claim_without_replay(self):
        c = self.controller
        with self.project() as (inputs, _, _, grant):
            with patch.object(c, 'transport_from_key_file', side_effect=Refused('Fixture missing credential')):
                with self.assertRaisesRegex(Refused, 'Fixture missing credential'):
                    c.run_issue(**inputs)
            self.assertEqual(self.budget.read()['requests'][-1]['status'], 'unknown')
            with patch.object(c, 'transport_from_key_file') as key:
                with self.assertRaisesRegex(Refused, 'outcome unknown'):
                    c.run_issue(**inputs, resume=True)
                key.assert_not_called()

    def test_task_source_profile_and_principal_drift_refuse_before_credentials(self):
        c = self.controller
        with self.project() as (inputs, _, _, grant), patch.object(c, 'transport_from_key_file') as key:
            task_raw = self.task_file.read_bytes()
            changed_task = self.write_task({**self.task, 'body': self.task['body'] + '\nDifferent scope'})
            with self.assertRaises(Refused):
                c.run_issue(**{**inputs, 'root_task': changed_task})
            self.task_file.write_bytes(task_raw)
            source = self.runtime.root / 'banner.txt'
            source_raw = source.read_bytes()
            source.write_text('Changed source\n')
            with self.assertRaisesRegex(Refused, 'differs from its committed source'):
                c.run_issue(**inputs)
            source.write_bytes(source_raw)
            raw_profile = self.profile.read_bytes()
            self.profile.write_bytes(encode({**json.loads(raw_profile), 'reviewer': 'changed-reviewer'}))
            changed_profile = self.profile, digest(self.profile.read_bytes())
            with self.assertRaisesRegex(Refused, 'does not bind'):
                c.run_issue(**{**inputs, 'root_profile': changed_profile})
            self.profile.write_bytes(raw_profile)
            with patch.object(c.os, 'getuid', return_value=-1), self.assertRaisesRegex(Refused, 'OS principal'):
                c.run_issue(**inputs)
            key.assert_not_called()

    def test_v3_after_both_liam_grants_preserves_the_proven_ceiling_chain(self):
        c = self.controller
        with self.liam_continuation_fixture() as (first, selection, _, _, _, _):
            args = dict(root_profile=first['root_profile'], root_task=first['root_task'], intake_only=True,
                        live_grant=selection)
            prior_preview = c.preview_root_admission(**args)
            with patch.object(c, 'transport_from_key_file', return_value=self.runtime.transport):
                c.run_issue(**args, authorize_root_intent=prior_preview['intentSha256'])
            old = self.budget.path.read_bytes()
            task = self.write_task({**self.task, 'id': 'new-generic-intake'})
            self.task = json.loads(task[0].read_bytes())
            base = c.preview_root_admission(self.selection, root_task=task, intake_only=True)
            grant = self.grant(base, extra=1)
            self.assertEqual(grant['maxLedgerCalls'], 19)
            self.assertEqual(grant['maxLedgerNanoUsd'], LIMIT + AMENDMENT_EXTRA)
            grant_selection = self.save_grant(grant)
            preview = c.preview_root_admission(self.selection, root_task=task, intake_only=True,
                                                live_grant=grant_selection)
            self.assertTrue(preview['budget']['canAdmitIntake'])
            with patch.object(c, 'transport_from_key_file', return_value=self.runtime.transport):
                result = c.run_issue(root_profile=self.selection, root_task=task, intake_only=True,
                    live_grant=grant_selection, authorize_root_intent=preview['intentSha256'])
            ledger = self.budget.read()
            original = json.loads(old)
            self.assertEqual(ledger['requests'][:-1], original['requests'])
            self.assertEqual(ledger['amendment'], original['amendment'])
            self.assertEqual(ledger['continuation'], original['continuation'])
            self.assertEqual((self.runtime.state/project_grant_backup(grant)).read_bytes(), old)
            self.assertEqual(ledger['maxCalls'], 16)
            self.assertEqual(ledger['limitNanoUsd'], LIMIT)
            self.assertEqual(result['stage'], 'intake_proposed')
            review = {'grantVersion': 3, 'originControllerSha256': base['intent']['controllerSha256']}
            original_admission, observed_ledger = c.retained_live_admission(result, base, review, result['key'],
                self.runtime.state/'local-tasks'/result['key'], self.runtime.repository, self.task['id'])
            self.assertEqual(original_admission, preview['intent'])
            self.assertEqual(observed_ledger, ledger)

    def test_rolled_back_ledger_blocks_different_operation_and_ordinary_spending(self):
        with self.project() as (_, _, preview, grant):
            original = self.budget.path.read_bytes()
            self.budget.claim_project_grant(grant['operation'], preview['modelRequest'], grant)
            self.budget.path.write_bytes(original)
            with self.assertRaisesRegex(Refused, 'without its claim'):
                self.budget.read()
            with self.assertRaisesRegex(Refused, 'without its claim'):
                self.budget.reserve('different-operation', preview['modelRequest'], 0)
            other = {**grant, 'operation': 'task-' + 'a' * 64 + '/intake'}
            with self.assertRaisesRegex(Refused, 'without its claim|established shared budget'):
                self.budget.claim_project_grant(other['operation'], preview['modelRequest'], other)

    def test_second_ledger_read_is_rechecked_before_claim(self):
        with self.project() as (_, _, preview, grant):
            snapshot = self.budget.admission_snapshot
            def changed(*args, **kwargs):
                result = snapshot(*args, **kwargs)
                self.budget.path.write_bytes(self.budget.path.read_bytes() + b'\n')
                return result
            with patch.object(self.budget, 'admission_snapshot', side_effect=changed):
                with self.assertRaisesRegex(Refused, 'Budget changed before project grant claim'):
                    self.budget.claim_project_grant(grant['operation'], preview['modelRequest'], grant)
            self.assertFalse((self.runtime.state/project_grant_backup(grant)).exists())
            self.assertEqual(self.budget.read()['requests'], [])

    def test_installed_preview_binds_key_path_without_read_and_dispatch_requires_that_grant(self):
        c = self.controller
        with self.project() as (source_inputs, _, _, _):
            key_file = self.root/'fake-private-key.env'
            fake_secret = 'sk-fixture-never-transmitted'
            key_file.write_text('OPENAI_API_KEY=' + fake_secret + '\n')
            key_file.chmod(0o600)
            installation = {'path': str(ROOT), 'manifestSha256': self.manifest_pin, 'version': 'fixture'}
            with c.installed_root_context(self.manifest_pin, self.runtime.state, self.runtime.legacy), \
                    patch.object(c, 'installed_root_binding', return_value=installation):
                preview_args = dict(root_task=source_inputs['root_task'], intake_only=True, live_key_file=key_file)
                with patch.object(c, 'transport_from_key_file', side_effect=AssertionError('Preview credentials forbidden')):
                    base = c.preview_root_admission(self.selection, **preview_args)
                self.assertFalse(base['liveModelAvailable'])
                self.assertFalse(base['executionAuthorized'])
                requirements = base['projectGrantRequirements']
                self.assertEqual(requirements['status'], 'approval-required')
                self.assertEqual(requirements['existingCallCeiling'], 16)
                self.assertEqual(base['intent']['liveTransport'], {'provider': 'openai',
                    'baseUrl': 'https://api.openai.com/v1', 'keyFile': str(key_file)})
                self.assertNotIn(fake_secret, json.dumps(base))
                self.assertNotIn(digest(fake_secret.encode()), json.dumps(base))
                self.assertNotIn(digest(key_file.read_bytes()), json.dumps(base))
                grant = self.grant(base)
                self.assertEqual(requirements['scopeSha256'], grant['scopeSha256'])
                selection = self.save_grant(grant)
                live = c.preview_root_admission(self.selection, live_grant=selection, **preview_args)
                inputs = dict(root_profile=self.selection, **preview_args, live_grant=selection,
                              authorize_root_intent=live['intentSha256'])
                with patch.object(c, 'transport_from_key_file') as reader:
                    with self.assertRaisesRegex(Refused, 'explicit private key file'):
                        c.run_issue(**{key: value for key, value in inputs.items() if key != 'live_key_file'})
                    with self.assertRaisesRegex(Refused, 'exact live project grant'):
                        c.run_issue(**{key: value for key, value in inputs.items() if key != 'live_grant'})
                    changed_key = self.root/'other-fake-key.env'
                    changed_key.write_text('OPENAI_API_KEY=sk-different-fixture')
                    changed_key.chmod(0o600)
                    with self.assertRaisesRegex(Refused, 'does not bind'):
                        c.run_issue(**{**inputs, 'live_key_file': changed_key})
                    reader.assert_not_called()
                def credentials(path, *, require_private):
                    self.assertEqual(path, key_file)
                    self.assertTrue(require_private)
                    self.assertEqual(self.budget.read()['requests'][-1]['projectGrant'], grant)
                    return self.runtime.transport
                with patch.object(c, 'transport_from_key_file', side_effect=credentials) as reader:
                    result = c.run_issue(**inputs)
                    reader.assert_called_once_with(key_file, require_private=True)
                self.assertEqual(result['stage'], 'intake_proposed')
                key_file.unlink()
                with patch.object(c, 'transport_from_key_file', side_effect=AssertionError('Resume credential access')):
                    self.assertEqual(c.run_issue(**inputs, resume=True), result)

    def test_key_metadata_refuses_public_linked_and_source_paths_without_read(self):
        import os
        c = self.controller
        with self.project() as (inputs, _, _, _):
            path = self.root/'fake-key.env'
            path.write_text('OPENAI_API_KEY=sk-fixture')
            path.chmod(0o644)
            args = dict(root_task=inputs['root_task'], intake_only=True)
            with patch.object(c, 'transport_from_key_file', side_effect=AssertionError('Credential read forbidden')):
                with self.assertRaisesRegex(Refused, 'private owned'):
                    c.preview_root_admission(self.selection, live_key_file=path, **args)
                path.chmod(0o600)
                alias = self.root/'key-alias.env'
                os.link(path, alias)
                with self.assertRaisesRegex(Refused, 'private owned'):
                    c.preview_root_admission(self.selection, live_key_file=path, **args)
                alias.unlink()
                alias.symlink_to(path)
                with self.assertRaisesRegex(Refused, 'canonical absolute'):
                    c.preview_root_admission(self.selection, live_key_file=alias, **args)
                with self.assertRaisesRegex(Refused, 'separate from controller'):
                    c.preview_root_admission(self.selection, live_key_file=self.runtime.root/'banner.txt', **args)

    def test_modified_claim_and_orphan_backup_fail_closed(self):
        c = self.controller
        with self.project() as (inputs, _, preview, grant):
            backup = self.runtime.state / project_grant_backup(grant)
            backup.write_bytes(self.budget.path.read_bytes())
            with patch.object(c, 'transport_from_key_file') as key, self.assertRaisesRegex(Refused, 'without its claim|established shared budget'):
                c.run_issue(**inputs)
            key.assert_not_called()
            backup.unlink()
            self.budget.claim_project_grant(grant['operation'], preview['modelRequest'], grant)
            raw = backup.read_bytes()
            backup.write_bytes(raw + b'\n')
            with self.assertRaisesRegex(Refused, 'original history'):
                self.budget.read()
            backup.write_bytes(raw)
            ledger = self.budget.read()
            ledger['requests'][-1]['projectGrant']['maxLedgerCalls'] += 1
            self.budget.path.write_bytes(encode(ledger))
            with self.assertRaisesRegex(Refused, 'project-bound budget reservation'):
                self.budget.read()


class PrivateKeyTransportTests(unittest.TestCase):
    def test_private_read_accepts_only_private_owned_bounded_regular_fake_credentials(self):
        from openai_budget import transport_from_key_file
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')):
            path = Path(temporary).resolve()/'fake-key.env'
            path.write_text('OPENAI_API_KEY=sk-fixture-only\n')
            path.chmod(0o600)
            self.assertTrue(callable(transport_from_key_file(path, require_private=True)))
            path.chmod(0o640)
            with self.assertRaisesRegex(Refused, 'unavailable or unsafe'):
                transport_from_key_file(path, require_private=True)
            self.assertTrue(callable(transport_from_key_file(path)))  # Legacy default remains unchanged.
            path.chmod(0o600)
            for raw in (b'x' * 65537, b'\xffOPENAI_API_KEY=sk-fixture'):
                path.write_bytes(raw)
                with self.assertRaisesRegex(Refused, 'unavailable or unsafe') as error:
                    transport_from_key_file(path, require_private=True)
                self.assertNotIn('sk-fixture', str(error.exception))


class SourceCheckReviewWordingTests(unittest.TestCase):
    def test_source_check_handoff_does_not_claim_a_build_or_artifact(self):
        from aim_ci.human_review import pause
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            state = {'stage': 'review', 'executionMode': 'synthetic-offline', 'task': {'title': 'Fixture check', 'body': 'Check source'},
                'key': 'fixture', 'executionBinding': {}, 'baseline': 'a' * 40, 'profileSha256': 'b' * 64,
                'resumeContractSha256': 'c' * 64, 'candidateSha256': 'd' * 64,
                'originalSources': {'source.js': 'before\n'}, 'frozenInputs': {}, 'round': 1, 'triage': {},
                'validation': [], 'validationSeal': {}, 'formatting': [], 'baselineTest': [],
                'recipeEvidence': {'status': 'source-check-passed', 'artifacts': {}}}
            def record(stage, **fields):
                state.update(stage=stage, **fields)
            pause(folder, state, {'reviewPolicy': {'mode': 'human-local', 'reviewer': 'fixture'}},
                  {'source.js': 'after\n'}, record=record)
            handoff = (folder/'human-review/review.md').read_text()
            self.assertIn('source-only check evidence', handoff)
            self.assertIn('No build or artifact was produced', handoff)
            self.assertNotIn('named test and build evidence', handoff)
            manifest = json.loads((folder/'human-review/manifest.json').read_bytes())
            self.assertFalse(manifest['binding']['productAccepted'])
            self.assertFalse(manifest['binding']['publicationAuthorized'])


if __name__ == '__main__':
    unittest.main()
