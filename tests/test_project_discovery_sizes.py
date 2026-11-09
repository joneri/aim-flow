"""Full selected Git-object reads retain exact size and identity checks."""
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci.project_discovery import CommitReader, Observations, inspect_node, inspect_project_documents
from openai_budget import Refused


def object_id(kind, data):
    return hashlib.sha1(f'{kind} {len(data)}\0'.encode() + data).hexdigest()


class CompleteDiscoveryTests(unittest.TestCase):
    def reader(self):
        reader = object.__new__(CommitReader)
        reader.git = '/usr/bin/git'
        reader.checkout = ROOT
        reader.commit = 'a' * 40
        return reader

    def test_complete_git_output_has_no_default_project_byte_ceiling(self):
        reader = self.reader()
        data = b'x' * 600_000
        with patch('aim_ci.project_discovery.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=data, stderr=b'')) as run:
            self.assertEqual(reader.read('ls-tree', reader.commit), data)
            with self.assertRaisesRegex(Refused, 'Local Git could not read'):
                reader.read('cat-file', 'blob', reader.commit, maximum=len(data) - 1)
        self.assertEqual(run.call_args.kwargs['timeout'], 20)
        self.assertEqual(run.call_args.kwargs['env']['GIT_ALLOW_PROTOCOL'], '')
        self.assertEqual(run.call_args.kwargs['env']['GIT_NO_REPLACE_OBJECTS'], '1')

    def test_checked_object_uses_measured_size_beyond_old_ceiling(self):
        reader = self.reader()
        data = b'complete selected source\n' * 30_000
        oid = object_id('blob', data)
        reader.size = Mock(return_value=len(data))
        reader.read = Mock(return_value=data)
        self.assertEqual(reader.checked_object('blob', oid), data)
        reader.read.assert_called_once_with('cat-file', 'blob', oid, maximum=len(data))
        reader.size.assert_called_once_with(oid)
        self.assertEqual(reader.blob(oid), data)

    def test_size_or_hash_mismatch_still_refuses_instead_of_truncating(self):
        data = b'committed bytes'
        oid = object_id('blob', data)
        for size, actual in ((len(data) + 1, data), (len(data), b'changed content'),
                             (len(data), data[:-1]), (-1, data)):
            with self.subTest(size=size, actual=actual):
                reader = self.reader()
                reader.size = Mock(return_value=size)
                reader.read = Mock(return_value=actual)
                with self.assertRaises(Refused):
                    reader.checked_object('blob', oid)
        reader = self.reader()
        reader.size = Mock(return_value=len(data))
        reader.read = Mock()
        with self.assertRaisesRegex(Refused, 'expected read size'):
            reader.checked_object('blob', oid, len(data) - 1)
        reader.read.assert_not_called()

    def test_truncated_tree_listing_is_not_a_complete_inventory(self):
        reader = self.reader()
        reader.read = Mock(return_value=b'100644 blob ' + b'a' * 40 + b'\tfile')
        with self.assertRaisesRegex(Refused, 'listing is incomplete'):
            list(reader.entries(reader.commit))

    def test_lock_header_evidence_is_not_shortened(self):
        report = Observations(Mock(commit='a' * 40))
        report.files['pnpm-lock.yaml'] = {'path': 'pnpm-lock.yaml'}
        header = 'lockfileVersion: ' + '1' * 200
        inspect_node(report, {'pnpm-lock.yaml': header + '\n'})
        fact = next(row for row in report.facts if row['code'] == 'lock-header-indicator')
        self.assertEqual(fact['value'], header)

    def test_document_errors_preserve_regular_encoding_and_identity_requirements(self):
        reader = Mock(commit='a' * 40)
        reader.entries.return_value = [('README.md', '100644', 'blob', 'b' * 40)]
        reader.size.return_value = 2
        reader.checked_object.return_value = b'\xff\xfe'
        report = Observations(reader)
        inspect_project_documents(report, ['README.md'])
        self.assertEqual(report.files['README.md']['status'], 'unsupported-encoding')
        self.assertEqual(report.open[0]['code'], 'definition-encoding')
        reader.checked_object.side_effect = Refused('mismatched pinned object')
        report = Observations(reader)
        inspect_project_documents(report, ['README.md'])
        self.assertEqual(report.files['README.md']['status'], 'not-readable')
        self.assertEqual(report.open[0]['code'], 'project-document-unreadable')
        reader.entries.return_value = [('README.md', '120000', 'blob', 'b' * 40)]
        reader.checked_object.reset_mock()
        report = Observations(reader)
        inspect_project_documents(report, ['README.md'])
        self.assertEqual(report.files['README.md']['status'], 'not-read')
        reader.checked_object.assert_not_called()
        for selected in (['README.md', 'README.md'], ['../README.md'], ['docs/../secret.md']):
            with self.subTest(selected=selected), self.assertRaises(Refused):
                inspect_project_documents(Observations(reader), selected)


if __name__ == '__main__':
    unittest.main()
