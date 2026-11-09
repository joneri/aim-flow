"""Portable model/request selection fixtures; no credentials, project or network."""
import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from openai_budget import (DEFAULT_OUTPUT, MAX_OUTPUT, MODEL, PROVIDER_MAX_OUTPUT,
                           Refused, provider_token_bounds)
from aim_ci import controller as c
from aim_ci.control_package import add_root_preview_input, root_preview_input


class OutputAllowanceTests(unittest.TestCase):
    def test_default_and_legacy_accounting_remain_8192(self):
        self.assertEqual(DEFAULT_OUTPUT, 8192)
        self.assertEqual(MAX_OUTPUT, 8192)
        request = c.model_request('intake', 'Inspect', {}, c.INTAKE_SCHEMA)
        self.assertEqual(request['max_output_tokens'], 8192)
        self.assertEqual(request['model'], MODEL)
        self.assertEqual(request, c.model_request('intake', 'Inspect', {}, c.INTAKE_SCHEMA, output_tokens=8192))

    def test_selected_allowance_has_only_genuine_provider_capacity_limit(self):
        for allowance in (1, 16000, 20000, PROVIDER_MAX_OUTPUT):
            with self.subTest(allowance=allowance):
                request = c.model_request('intake', 'Inspect', {}, c.INTAKE_SCHEMA, output_tokens=allowance)
                self.assertEqual(request['max_output_tokens'], allowance)
                self.assertEqual(request['model'], MODEL)
        provider_token_bounds(272000, 128000)
        for ins, outs in ((272001, 1), (1, 128001), (-1, 20000)):
            with self.subTest(ins=ins, outs=outs), self.assertRaises(Refused):
                provider_token_bounds(ins, outs)

    def test_selection_is_strict_positive_integer_before_admission_reads(self):
        for allowance in (True, False, 0, -1, 1.5, '20000', None, 128001):
            with self.subTest(allowance=allowance), self.assertRaises(Refused):
                c.model_request('intake', 'Inspect', {}, c.INTAKE_SCHEMA, output_tokens=allowance)
            with self.subTest(admission=allowance), patch.object(c, 'root_review_profile') as read:
                with self.assertRaises(Refused):
                    c.preview_root_admission(None, output_tokens=allowance)
                read.assert_not_called()

    def test_source_and_installed_preview_parser_preserve_selection(self):
        parser = argparse.ArgumentParser()
        add_root_preview_input(parser)
        base = ['--task-file', 'task.json', '--task-sha256', 'a' * 64]
        self.assertEqual(root_preview_input(parser, parser.parse_args(base)),
                         {'root_task': (Path('task.json'), 'a' * 64), 'intake_only': True})
        self.assertEqual(root_preview_input(parser, parser.parse_args([*base, '--output-tokens', '20000'])),
                         {'root_task': (Path('task.json'), 'a' * 64), 'intake_only': True, 'output_tokens': 20000})

    def test_selection_does_not_extend_unbound_worker_or_candidate_calls(self):
        with self.assertRaisesRegex(Refused, 'explicit root intake-only'):
            c.run_issue(1, output_tokens=20000)
        with self.assertRaisesRegex(Refused, 'explicit root intake-only'):
            c.run_issue(root_profile=('unused', 'a' * 64), retained_intake=('unused', 'b' * 64),
                        candidate_action='prepare', output_tokens=20000)


if __name__ == '__main__':
    unittest.main()
