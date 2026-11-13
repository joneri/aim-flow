"""Portable private-terminal contracts with product-only fixtures and no live effects."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if sys.argv[1:2] == ['--installed-controller']:
    ROOT = Path(sys.argv[2]).resolve()
    del sys.argv[1:3]
sys.path.insert(0, str(ROOT / 'lib'))
import openai_budget as api
from aim_ci import controller as c
from aim_ci.budget import Budget


PARTIAL = '{"triage":{"ready":true},"patch":{"edits":[{"old":"unchanged'


def response(**changes):
    return {'id': 'resp_synthetic_terminal', 'model': api.MODEL, 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'},
            'usage': {'input_tokens': 12000, 'output_tokens': 20000, 'total_tokens': 32000,
                      'output_tokens_details': {'reasoning_tokens': 512}},
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': PARTIAL}]}], **changes}


class TerminalDiagnosticTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.campaign = api.Campaign(self.root / 'campaign', 'synthetic-scope')
        self.request = c.model_request('intake', 'Synthetic instructions', {'files': {}}, c.INTAKE_SCHEMA,
                                       output_tokens=20000)
        self.name = hashlib.sha256(b'intake').hexdigest() + '.json'
        self.path = self.campaign.directory / 'terminal-diagnostics' / self.name
        self.calls = []

    def provider(self, terminal):
        def call(endpoint, payload):
            self.calls.append(endpoint)
            return {'input_tokens': 12000} if endpoint.endswith('input_tokens') else terminal
        return call

    def reserve(self):
        self.campaign.reserve('intake', api.fingerprint(self.request), 12000, 20000)

    def test_incomplete_stream_retains_exact_visible_json_billing_reason_and_no_replay(self):
        terminal = response()
        wire = ('data: ' + json.dumps({'type': 'response.incomplete', 'response': terminal}) + '\n\n').encode()
        with self.assertRaisesRegex(api.Refused, 'Model response incomplete'):
            self.campaign.call('intake', self.request, self.provider(api.collect_response_stream(io.BytesIO(wire))))
        value = json.loads(self.path.read_bytes())
        self.assertEqual(value, {'version': 1, 'operation': 'intake',
            'requestSha256': api.fingerprint(self.request), 'campaignScopeSha256': api.fingerprint('synthetic-scope'),
            'responseId': terminal['id'], 'responseStatus': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'}, 'usage': terminal['usage'],
            'visibleOutputText': [PARTIAL]})
        item = self.campaign.read()['requests'][0]
        self.assertEqual(item['status'], 'completed')  # Financially settled, never proposal-completed.
        self.assertEqual(item['responseStatus'], 'incomplete')
        self.assertEqual(item['actualNanoUsd'], 43_000_000)
        self.assertEqual(item['maxOutputTokens'], 20000)
        self.assertGreater(item['reservedNanoUsd'], item['actualNanoUsd'])
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.campaign.retain_terminal('intake', terminal)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        with self.assertRaises(api.Refused):
            self.campaign.call('intake', self.request, self.provider(terminal))
        self.assertEqual(self.calls.count('responses'), 1)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(self.path.stat().st_uid, os.geteuid())

    def test_only_documented_fields_are_kept_without_claiming_secret_scrubbing(self):
        terminal = response(input='REQUEST-SECRET', instructions='INSTRUCTION-SECRET',
            error={'message': 'RAW-ERROR-SECRET'}, arbitrary='ARBITRARY-SECRET')
        terminal['incomplete_details']['extra'] = 'DETAIL-SECRET'
        terminal['usage'].update(arbitrary='USAGE-SECRET', input_tokens_details={'cached_tokens': 3, 'extra': 'CACHE-SECRET'})
        terminal['output'].extend([
            {'type': 'reasoning', 'encrypted_content': 'ENCRYPTED-SECRET',
             'summary': [{'type': 'summary_text', 'text': 'REASONING-SECRET'}]},
            {'type': 'message', 'role': 'user', 'content': [{'type': 'output_text', 'text': 'USER-SECRET'}]},
            {'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'REFUSAL-SECRET'}]},
        ])
        terminal['output'][0]['content'].append({'type': 'output_text', 'text': 'VISIBLE-source-secret'})
        value = api.terminal_diagnostic(terminal)
        self.assertEqual(value['visibleOutputText'], [PARTIAL, 'VISIBLE-source-secret'])
        self.assertEqual(value['usage']['input_tokens_details'], {'cached_tokens': 3})
        serialized = json.dumps(value)
        for omitted in ('REQUEST-', 'INSTRUCTION-', 'RAW-ERROR-', 'ARBITRARY-', 'DETAIL-', 'USAGE-',
                        'CACHE-', 'ENCRYPTED-', 'REASONING-', 'USER-', 'REFUSAL-'):
            self.assertNotIn(omitted, serialized)
        for reason in ('max_output_tokens', 'content_filter'):
            self.assertEqual(api.terminal_diagnostic(response(incomplete_details={'reason': reason}))['incomplete_details'],
                             {'reason': reason})
        for changes in ({'id': {'text': 'SECRET'}}, {'id': '../SECRET'}, {'status': ['SECRET']},
                        {'status': 'SECRET'}, {'incomplete_details': {'reason': 'SECRET'}}):
            self.assertNotIn('SECRET', json.dumps(api.terminal_diagnostic(response(**changes))))

    def test_malformed_usage_and_response_still_settle_pessimistically(self):
        for index, terminal in enumerate([None, [], response(usage=None), response(usage=[]),
                                          response(usage='bad'), response(usage={'input_tokens': True, 'output_tokens': 1}),
                                          response(status='completed', usage=None)]):
            with self.subTest(response=terminal):
                campaign = api.Campaign(self.root / str(index), 'synthetic-scope')
                with self.assertRaisesRegex(api.Refused, 'full reservation retained'):
                    campaign.call('intake', self.request, self.provider(terminal))
                item = campaign.read()['requests'][0]
                self.assertEqual(item['status'], 'unknown')
                self.assertNotIn('actualNanoUsd', item)
                self.assertGreater(item['reservedNanoUsd'], 0)

    def test_complete_success_creates_no_diagnostic_and_is_unchanged(self):
        terminal = response(status='completed')
        self.assertIs(self.campaign.call('intake', self.request, self.provider(terminal)), terminal)
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(self.campaign.read()['requests'][0]['responseStatus'], 'completed')

    def test_private_write_failure_never_skips_observed_billing(self):
        with patch.object(api, 'private_terminal_record', side_effect=OSError('Synthetic disk full')):
            with self.assertRaisesRegex(api.Refused, 'private terminal diagnostic unavailable'):
                self.campaign.call('intake', self.request, self.provider(response()))
        item = self.campaign.read()['requests'][0]
        self.assertEqual((item['status'], item['responseStatus'], item['actualNanoUsd']),
                         ('completed', 'incomplete', 43_000_000))
        self.assertFalse(self.path.exists())

    def test_interruption_during_persistence_still_settles_usage(self):
        with patch.object(api, 'private_terminal_record', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.campaign.call('intake', self.request, self.provider(response()))
        self.assertEqual(self.campaign.read()['requests'][0]['actualNanoUsd'], 43_000_000)

    def test_mismatched_existing_record_is_never_overwritten_and_accounting_survives(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_bytes(b'{"different":"evidence"}\n')
        self.path.chmod(0o600)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(api.Refused, 'diagnostic unavailable'):
            self.campaign.call('intake', self.request, self.provider(response()))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.campaign.read()['requests'][0]['actualNanoUsd'], 43_000_000)

    def test_unsafe_target_directory_or_file_refused_without_following_or_chmod(self):
        self.reserve()
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'unchanged')
        sentinel.chmod(0o600)
        self.path.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((api.Refused, OSError)):
            self.campaign.retain_terminal('intake', response())
        self.path.parent.unlink()
        self.path.parent.mkdir(mode=0o755)
        with self.assertRaises(api.Refused):
            self.campaign.retain_terminal('intake', response())
        self.path.parent.chmod(0o700)
        for kind in ('symlink', 'dangling', 'hardlink', 'fifo', 'directory', 'mode'):
            with self.subTest(kind=kind):
                if kind == 'symlink': self.path.symlink_to(sentinel)
                elif kind == 'dangling': self.path.symlink_to(outside / 'missing')
                elif kind == 'hardlink': os.link(sentinel, self.path)
                elif kind == 'fifo': os.mkfifo(self.path, 0o600)
                elif kind == 'directory': self.path.mkdir()
                else:
                    self.path.write_bytes(b'unsafe mode')
                    self.path.chmod(0o644)
                with self.assertRaises((api.Refused, OSError)):
                    self.campaign.retain_terminal('intake', response())
                if self.path.is_dir() and not self.path.is_symlink(): self.path.rmdir()
                else: self.path.unlink()
        self.assertEqual(sentinel.read_bytes(), b'unchanged')
        self.assertFalse((outside / self.name).exists())

    def test_unowned_parent_directory_and_file_are_refused(self):
        # Simulated UID observation avoids requiring root/chown in portable CI.
        self.reserve()
        self.campaign.retain_terminal('intake', response())
        original = api.os.fstat
        for target in (self.campaign.directory, self.path.parent, self.path):
            inode = target.stat().st_ino
            def unowned(descriptor):
                info = original(descriptor)
                if info.st_ino == inode:
                    values = list(info)
                    values[4] = os.geteuid() + 1
                    return os.stat_result(values)
                return info
            with self.subTest(target=target.name), patch.object(api.os, 'fstat', side_effect=unowned):
                with self.assertRaises(api.Refused):
                    self.campaign.retain_terminal('intake', response())

    def test_atomic_publication_interruption_and_fixed_staging_are_inert(self):
        self.reserve()
        with patch.object(api.os, 'link', side_effect=OSError('Synthetic publish interruption')):
            with self.assertRaises(OSError):
                self.campaign.retain_terminal('intake', response())
        self.assertFalse(self.path.exists())
        pending = self.path.with_name(self.path.name + '.pending')
        self.assertTrue(pending.is_file())
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)
        self.campaign.retain_terminal('intake', response())  # Reuse exact complete staging, no overwrite.
        self.assertTrue(self.path.is_file())
        self.assertFalse(pending.exists())
        self.path.unlink()
        pending.write_bytes(b'partial')
        pending.chmod(0o600)
        for _ in range(2):
            with self.assertRaises(api.Refused):
                self.campaign.retain_terminal('intake', response())
        self.assertEqual(pending.read_bytes(), b'partial')
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)
        for name in ('../escape.json', '/absolute.json', 'unbound.json'):
            with self.assertRaises(api.Refused):
                api.private_terminal_record(self.campaign.directory, name, {})

    def test_interruption_after_publication_retains_complete_two_link_evidence_without_repair(self):
        unlink = api.os.unlink
        def interrupted(name, *args, **kwargs):
            if str(name).endswith('.json.pending'):
                raise OSError('Synthetic interruption after atomic publication')
            return unlink(name, *args, **kwargs)
        with patch.object(api.os, 'unlink', side_effect=interrupted):
            with self.assertRaisesRegex(api.Refused, 'diagnostic unavailable'):
                self.campaign.call('intake', self.request, self.provider(response()))
        pending = self.path.with_name(self.path.name + '.pending')
        self.assertEqual(self.path.read_bytes(), pending.read_bytes())
        self.assertEqual(json.loads(self.path.read_bytes())['visibleOutputText'], [PARTIAL])
        self.assertEqual(self.path.stat().st_nlink, 2)
        original = self.path.read_bytes()
        with self.assertRaises(api.Refused):
            self.campaign.retain_terminal('intake', response())
        with self.assertRaises(api.Refused):
            self.campaign.call('intake', self.request, self.provider(response()))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.calls.count('responses'), 1)
        self.assertEqual(self.campaign.read()['requests'][0]['actualNanoUsd'], 43_000_000)

    def test_unsafe_pending_targets_are_never_overwritten_or_published(self):
        self.reserve()
        self.path.parent.mkdir(mode=0o700)
        pending = self.path.with_name(self.path.name + '.pending')
        sentinel = self.root / 'sentinel'
        sentinel.write_bytes(b'unchanged')
        sentinel.chmod(0o600)
        for kind in ('symlink', 'dangling', 'hardlink', 'fifo', 'directory', 'mode', 'unowned'):
            with self.subTest(kind=kind):
                if kind == 'symlink': pending.symlink_to(sentinel)
                elif kind == 'dangling': pending.symlink_to(self.root / 'absent')
                elif kind == 'hardlink': os.link(sentinel, pending)
                elif kind == 'fifo': os.mkfifo(pending, 0o600)
                elif kind == 'directory': pending.mkdir()
                else:
                    pending.write_bytes(b'pending')
                    pending.chmod(0o644 if kind == 'mode' else 0o600)
                original = api.os.fstat
                inode = pending.lstat().st_ino
                def unowned(descriptor):
                    info = original(descriptor)
                    if kind == 'unowned' and info.st_ino == inode:
                        values = list(info)
                        values[4] = os.geteuid() + 1
                        return os.stat_result(values)
                    return info
                with patch.object(api.os, 'fstat', side_effect=unowned):
                    with self.assertRaises((api.Refused, OSError)):
                        self.campaign.retain_terminal('intake', response())
                self.assertFalse(self.path.exists())
                if pending.is_dir() and not pending.is_symlink(): pending.rmdir()
                else: pending.unlink()
        self.assertEqual(sentinel.read_bytes(), b'unchanged')

    def test_no_additional_content_cap_or_visible_text_truncation(self):
        visible = 'complete visible synthetic output\n' * 70000
        self.reserve()
        terminal = response(output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': visible}]}])
        self.campaign.retain_terminal('intake', terminal)
        self.assertEqual(json.loads(self.path.read_bytes())['visibleOutputText'], [visible])


class ProductOnlyIntakeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.directory = self.root / 'task-synthetic'
        self.directory.mkdir()
        self.legacy = self.root / 'legacy.json'
        self.legacy.write_text('{"requests": []}')
        self.budget = Budget(self.root / 'shared', self.legacy)
        issue = {'title': 'Synthetic local product change'}
        self.binding = {'issueSha256': api.fingerprint(issue), 'baseline': 'a' * 40,
                        'profileSha256': 'b' * 64, 'target': None}
        api.atomic_json(self.directory / 'state.json', {'issue': issue, 'baseline': 'a' * 40,
            'profileSha256': 'b' * 64, 'executionBinding': self.binding})
        self.calls = []

    def invoke(self, terminal, *, reconcile_only=False):
        def provider(endpoint, payload):
            self.calls.append(endpoint)
            return {'input_tokens': 12000} if endpoint.endswith('input_tokens') else terminal
        return c.model_call(self.directory, 'intake', 'Synthetic', {}, c.INTAKE_SCHEMA,
                            self.budget, provider, binding=self.binding, synthetic=True,
                            reconcile_only=reconcile_only)

    def test_partial_json_is_not_proposal_receipt_candidate_or_resume_authority(self):
        terminal = response(usage={'input_tokens': 12000, 'output_tokens': 8192})
        with self.assertRaisesRegex(api.Refused, 'Model response incomplete'):
            self.invoke(terminal)
        self.assertFalse((self.directory / 'intake.json').exists())
        self.assertFalse((self.directory / 'effects/intake.receipt.json').exists())
        self.assertFalse((self.root / 'shared/intake-candidates').exists())
        shared = self.budget.read()['requests'][0]
        campaign = json.loads((self.directory / 'triage/budget.json').read_bytes())['requests'][0]
        self.assertEqual(shared['responseStatus'], campaign['responseStatus'])
        self.assertEqual(shared['actualNanoUsd'], campaign['actualNanoUsd'])
        diagnostic = next((self.directory / 'triage/terminal-diagnostics').glob('*.json'))
        self.assertEqual(json.loads(diagnostic.read_bytes())['visibleOutputText'], [PARTIAL])
        observed = [self.budget.path, self.directory / 'triage/budget.json', self.directory / 'effects/effects.json']
        before = {path: path.read_bytes() for path in observed}
        for historical_missing in (False, True):
            if historical_missing:
                diagnostic.unlink()  # Historical absence remains valid, never fabricated or recovered.
            with self.assertRaisesRegex(api.Refused, 'outcome unknown'):
                self.invoke(terminal, reconcile_only=True)
            self.assertEqual({path: path.read_bytes() for path in observed}, before)
        self.assertEqual(self.calls, ['responses/input_tokens', 'responses'])
        self.assertFalse(diagnostic.exists())

    def test_shared_malformed_usage_settles_unknown_and_never_creates_proposal(self):
        for usage in (None, [], 'bad'):
            with self.subTest(usage=usage):
                operation = 'malformed-' + str(usage)
                provider = self.budget.transport(operation, lambda endpoint, payload:
                    {'input_tokens': 10} if endpoint.endswith('input_tokens') else response(usage=usage))
                provider('responses/input_tokens', {})
                provider('responses', c.model_request('intake', 'Synthetic', {}, c.INTAKE_SCHEMA))
                item = self.budget.read()['requests'][-1]
                self.assertEqual(item['status'], 'unknown')
                self.assertNotIn('actualNanoUsd', item)

    def test_persistence_failure_keeps_both_accounting_records_and_no_receipt(self):
        with patch.object(api, 'private_terminal_record', side_effect=OSError('Synthetic disk failure')):
            with self.assertRaisesRegex(api.Refused, 'diagnostic unavailable'):
                self.invoke(response(usage={'input_tokens': 12000, 'output_tokens': 8192}))
        for path in (self.budget.path, self.directory / 'triage/budget.json'):
            item = json.loads(path.read_bytes())['requests'][0]
            self.assertEqual((item['status'], item['responseStatus']), ('completed', 'incomplete'))
            self.assertEqual(item['actualNanoUsd'], 12000 * 250 + 8192 * 2000)
        self.assertFalse((self.directory / 'intake.json').exists())
        self.assertFalse((self.directory / 'effects/intake.receipt.json').exists())

    def test_intake_payload_preserves_complete_source_scope_and_request_controls(self):
        config = {'owner': 'synthetic', 'label': 'synthetic', 'productionUrl': None,
                  'allowedPaths': ['src/page.txt'], 'maximumChangedLines': None}
        source = {'src/page.txt': 'Complete source\n' * 10000}
        for local_task in (False, True):
            with self.subTest(local_task=local_task):
                instructions, data = c.intake_arguments(config, {'title': 'Synthetic'}, source,
                    root_review=True, intake_only=True, local_task=local_task)
                self.assertTrue(instructions.strip())
                self.assertNotIn(source['src/page.txt'], instructions)
                self.assertEqual(data['files'], source)
                self.assertEqual(data['allowedPaths'], config['allowedPaths'])
                request = c.model_request('intake', instructions, data, c.INTAKE_SCHEMA, output_tokens=20000)
                self.assertEqual(request['reasoning'], {'effort': 'low'})
                self.assertEqual(request['max_output_tokens'], 20000)
                self.assertIs(request['store'], False)
                self.assertNotIn('tools', request)
                self.assertEqual(request['text']['format']['schema'], c.INTAKE_SCHEMA)
                self.assertNotEqual(api.fingerprint(request),
                    api.fingerprint({**request, 'instructions': instructions + '\nChanged instruction'}))

    def test_completed_success_writes_existing_proposal_and_receipt(self):
        value = {'triage': {'ready': False}, 'patch': {'summary': 'Synthetic', 'edits': []}}
        terminal = response(status='completed', usage={'input_tokens': 12000, 'output_tokens': 100},
            output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}])
        self.assertEqual(self.invoke(terminal), value)
        self.assertEqual(json.loads((self.directory / 'intake.json').read_bytes()), value)
        self.assertEqual(json.loads((self.directory / 'effects/intake.receipt.json').read_bytes())['result'], value)
        self.assertFalse((self.directory / 'triage/terminal-diagnostics').exists())
        self.assertEqual(self.invoke(terminal, reconcile_only=True), value)
        self.assertEqual(self.calls, ['responses/input_tokens', 'responses'])


if __name__ == '__main__':
    unittest.main()
