"""Synthetic full-content regressions without demo products or live transports."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import controller, model_support as support
from aim_ci.contracts import admit_issue, bounded_patch, check_change_size
from openai_budget import Campaign, Refused, provider_token_bounds

PROFILE = json.loads((ROOT / 'profiles/catalog.json').read_text())


class ContentSizeContractTests(unittest.TestCase):
    def test_explicit_requirement_content_has_no_arbitrary_text_byte_ceiling(self):
        body = '## Scope\nSelected catalog change\n## Acceptance criteria\n- [ ] Output is correct\n'
        body += 'Detailed requirement context ' * 10000
        issue = {'number': 1, 'node_id': 'I_1', 'title': 'Review ' + 'described behavior ' * 100,
                 'body': body, 'user': {'login': PROFILE['owner']},
                 'labels': [{'name': PROFILE['label']}], 'state': 'open', 'locked': False,
                 'updated_at': '2026-11-09T00:00:00Z', 'html_url': 'https://example.invalid/issues/1'}
        self.assertEqual(admit_issue(issue, PROFILE)['body'], body)

    def test_ambiguous_or_missing_patch_context_refused(self):
        path = PROFILE['allowedPaths'][0]
        for original, old in [('xx', 'x'), ('x', 'missing')]:
            with self.subTest(original=original), self.assertRaises(Refused):
                bounded_patch({path: original}, {'summary': 'Exact change',
                    'edits': [{'path': path, 'old': old, 'new': 'replacement'}]}, PROFILE)

    def test_large_replacements_many_edits_and_lines_have_no_default_product_ceiling(self):
        path = PROFILE['allowedPaths'][0]
        original = {path: ''.join('old-%d\n' % i for i in range(400))}
        edits = [{'path': path, 'old': 'old-%d\n' % i, 'new': 'new-%d\n' % i} for i in range(400)]
        proposed = bounded_patch(original, {'summary': 'Exact selected source', 'edits': edits}, PROFILE)
        self.assertEqual(proposed[path], ''.join('new-%d\n' % i for i in range(400)))
        large = {path: 'x' * 256340 + '\n'}
        result = bounded_patch(large, {'summary': 'Complete replacement',
            'edits': [{'path': path, 'old': large[path], 'new': 'y' * 256340 + '\n'}]}, PROFILE)
        self.assertEqual(len(result[path]), 256341)

    def test_explicit_changed_line_policy_still_applies(self):
        path = PROFILE['allowedPaths'][0]
        check_change_size({path: 'before\n'}, {path: 'after\n' * 1000}, PROFILE)
        with self.assertRaises(Refused):
            check_change_size({path: 'before\n'}, {path: 'after\n' * 161},
                              {**PROFILE, 'maximumChangedLines': 160})

    def test_formatter_preserves_complete_output_and_rejects_invalid_output_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder).resolve()
            (candidate / 'catalog').mkdir()
            target = candidate / 'catalog/catalog.py'
            target.write_text('before')
            def capture(code, output):
                def run(*args, **kwargs):
                    (candidate / 'stdout').write_bytes(output)
                    (candidate / 'stderr').write_text('diagnostic only')
                    return [{'exitCode': code, 'stdout': 'stdout', 'log': 'stderr'}]
                return run
            for code, output in [(1, b'partial'), (0, b''), (0, b'\xff')]:
                with self.subTest(code=code, length=len(output)), patch.object(
                        support, 'validate', side_effect=capture(code, output)):
                    with self.assertRaises(Refused):
                        controller.format_candidate(candidate, ['catalog.py'], 1, config=PROFILE)
                    self.assertEqual(target.read_text(), 'before')
            complete = b'formatted\n' * 30000
            with patch.object(support, 'validate', side_effect=capture(0, complete)):
                controller.format_candidate(candidate, ['catalog.py'], 1, config=PROFILE)
            self.assertEqual(target.read_bytes(), complete)

    def test_full_frozen_input_hash_and_selected_output_allowance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'src').mkdir()
            content = b'x' * 1_000_001
            (root / 'src/large.css').write_bytes(content)
            self.assertEqual(support.frozen_inputs(root)['src/large.css'], hashlib.sha256(content).hexdigest())
        request = controller.model_request('review', 'Review', {}, support.REVIEW_SCHEMA, output_tokens=16000)
        self.assertEqual(request['max_output_tokens'], 16000)
        self.assertTrue(request['stream'])

    def test_formatter_capture_has_no_fixed_file_size_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder).resolve()
            (candidate / 'catalog').mkdir()
            complete = b'x' * 256340
            def launch(*args, **kwargs):
                self.assertNotIn('preexec_fn', kwargs)
                self.assertTrue(kwargs['start_new_session'])
                kwargs['stdout'].write(complete)
                kwargs['stderr'].write('diagnostic only')
                child = unittest.mock.Mock()
                child.wait.return_value = 0
                return child
            with patch.object(support.subprocess, 'Popen', side_effect=launch) as process:
                outcome = support.validate(candidate, [['synthetic-formatter']], 'large',
                                           product='catalog', capture_stdout=True)[0]
            self.assertEqual((candidate / outcome['stdout']).read_bytes(), complete)
            self.assertEqual((candidate / outcome['log']).read_text(), 'diagnostic only')
            self.assertIn('/usr/bin/sandbox-exec', process.call_args.args[0])

    def test_real_provider_token_limits_are_distinct_from_local_financial_allowances(self):
        provider_token_bounds(272000, 128000)
        for ins, outs in ((272001, 8192), (1, 128001), (True, 8192), (-1, 8192)):
            with self.subTest(input=ins, output=outs), self.assertRaises(Refused):
                provider_token_bounds(ins, outs)
        with tempfile.TemporaryDirectory() as folder:
            legacy = Campaign(Path(folder).resolve() / 'legacy-envelope', 'test',
                              maximum_operation_nano_usd=41640000)
            with self.assertRaisesRegex(Refused, 'financial reservation'):
                legacy.reserve('too-expensive', 'hash', 120000, 8192)
            self.assertEqual(legacy.read()['requests'], [])


if __name__ == '__main__':
    unittest.main()
