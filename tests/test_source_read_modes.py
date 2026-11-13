"""Narrow explicit read-bit restoration and independently checked evidence."""
import copy
import unittest
import test_review_execution_context as fixtures
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused

POLICY = container.SOURCE_READ_MODE_POLICY


class ReadModeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.intent = {'sourceReadModePolicy': POLICY, 'sources': {'archive.zip': {
            'sha256': 'a' * 64, 'bytes': 15, 'mode': '100644'}}, 'job': {'stages': [{}, {}]}}
        self.row = {'path': 'archive.zip', 'stageIndex': 1, 'sha256': 'a' * 64,
            'bytes': 15, 'fromMode': 0o600, 'toMode': 0o644}
        self.report = {'sourceReadModePolicy': POLICY, 'sourceReadModeRestorations': [self.row]}

    def test_only_exact_granted_restoration_evidence_is_admissible(self):
        self.assertTrue(container.valid_source_read_mode_restorations(self.intent, self.report))
        for mode in (0o600, 0o604, 0o640):
            self.row['fromMode'] = mode
            self.assertTrue(container.valid_source_read_mode_restorations(self.intent, self.report))
        for field, value in (('path', 'unbound.zip'), ('path', []), ('sha256', 'b' * 64),
                ('bytes', 16), ('bytes', True), ('fromMode', 0o755), ('fromMode', 0o4644),
                ('toMode', 0o600), ('stageIndex', 2), ('stageIndex', True)):
            changed = copy.deepcopy(self.report); changed['sourceReadModeRestorations'][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(container.valid_source_read_mode_restorations(self.intent, changed))

    def test_executable_sources_duplicates_or_ungranted_policy_are_refused(self):
        altered = copy.deepcopy(self.intent); altered['sources']['archive.zip']['mode'] = '100755'
        self.assertFalse(container.valid_source_read_mode_restorations(altered, self.report))
        self.report['sourceReadModeRestorations'].append(copy.deepcopy(self.row))
        self.assertFalse(container.valid_source_read_mode_restorations(self.intent, self.report))
        del self.intent['sourceReadModePolicy']
        self.assertFalse(container.valid_source_read_mode_restorations(self.intent, self.report))
        self.assertTrue(container.valid_source_read_mode_restorations(self.intent, {}))

    def test_policy_is_finite_and_requires_explicit_report_identity(self):
        self.assertEqual(container.source_read_mode_policy(POLICY), POLICY)
        for value in (None, True, 'ignore-source-modes', 'restore-content'):
            with self.subTest(value=value), self.assertRaises(Refused): container.source_read_mode_policy(value)
        self.assertFalse(container.valid_source_read_mode_restorations(self.intent, {}))
        self.report['sourceReadModePolicy'] = 'unapproved'
        self.assertFalse(container.valid_source_read_mode_restorations(self.intent, self.report))


class ReadModeContextTests(unittest.TestCase):
    setUp = fixtures.ContextTests.setUp
    build = fixtures.ContextTests.build
    packet = fixtures.ContextTests.packet

    def test_review_visible_policy_preserves_all_other_contract_fields(self):
        old = self.build(); self.spec['sourceReadModePolicy'] = POLICY; new = self.build()
        self.assertEqual(new['sourceReadModePolicy'], POLICY)
        self.assertEqual(fixtures.context.validate(new, self.packet()), new)
        self.assertIn('only for byte-identical', new['contract']['sourceCopies'])
        self.assertIn('every restoration is recorded', new['contract']['sourceCopies'])
        for key in old['contract']:
            if key != 'sourceCopies': self.assertEqual(old['contract'][key], new['contract'][key])
        self.assertEqual(old['limits'], new['limits']); self.assertEqual(old['initialEnvironment'], new['initialEnvironment'])
        hidden = copy.deepcopy(new); del hidden['sourceReadModePolicy']
        with self.assertRaises(Refused): fixtures.context.validate(hidden, self.packet())

    def test_unknown_null_or_boolean_policy_cannot_enter_source_review(self):
        for value in (None, True, 'broader'):
            self.spec['sourceReadModePolicy'] = value
            with self.subTest(value=value), self.assertRaises(Refused): self.build()
        self.spec['sourceReadModePolicy'] = POLICY; reviewed = self.build()
        reviewed['sourceReadModePolicy'] = None
        with self.assertRaises(Refused): fixtures.context.validate(reviewed, self.packet())


if __name__ == '__main__': unittest.main()
