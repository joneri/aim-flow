"""Exact committed membership for bounded source selection; no omitted blob reads."""
from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci.source_subset import git_id, source_subset, verify_source_subset
from openai_budget import Refused


class Reader:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.verified = 0
        script = self.add('blob', b'console.log("source check");\n')
        package = self.add('blob', b'{"scripts":{"test:source":"node scripts/check.mjs"}}\n')
        lock = self.add('blob', b'{"lockfileVersion":3,"packages":{}}\n')
        # Only the object ID is present. Reading omitted data would fail.
        absent = bytes.fromhex('f' * 40)
        nested = self.add('tree', b'100644 check.mjs\0' + script + b'100644 huge.bin\0' + absent)
        self.tree = self.add('tree', b'100644 package-lock.json\0' + lock + b'100644 package.json\0' + package
                            + b'40000 scripts\0' + nested + b'120000 symlink\0' + script).hex()
        self.commit = self.add('commit', f'tree {self.tree}\nauthor Fixture <fixture@example.invalid> 0 +0000\n'
                               'committer Fixture <fixture@example.invalid> 0 +0000\n\nfixture\n').hex()

    def add(self, kind, raw):
        if isinstance(raw, str):
            raw = raw.encode()
        oid = git_id(kind, raw)
        self.objects[oid.hex()] = kind, raw
        return oid

    def size(self, oid):
        self.reads.append(('size', oid))
        return len(self.objects[oid][1])

    def checked_object(self, kind, oid, maximum):
        self.reads.append((kind, oid))
        actual, raw = self.objects[oid]
        assert actual == kind and len(raw) <= maximum
        return raw

    def verify_head(self):
        self.verified += 1


class SourceSubsetTests(unittest.TestCase):
    def prepared(self):
        reader = Reader()
        rows, commit, proof = source_subset(reader, ['scripts/check.mjs', 'package.json', 'package-lock.json'])
        return reader, rows, commit, proof

    def test_selected_bytes_bind_original_commit_without_omitted_blobs(self):
        reader, rows, commit, proof = self.prepared()
        self.assertEqual(reader.verified, 1)
        self.assertEqual(proof['commit'], reader.commit)
        self.assertEqual(proof['tree'], reader.tree)
        self.assertEqual(len(proof['trees']), 2)
        self.assertEqual(list(rows), proof['selectedPaths'])
        self.assertNotIn('f' * 40, [oid for kind, oid in reader.reads])
        verify_source_subset(rows, commit, proof)
        # Readback already measures blobs in the archive and need not retain bytes.
        verify_source_subset({name: {**row, 'data': None} for name, row in rows.items()}, commit, proof)

    def test_exact_selection_and_measured_identity_reject_tamper(self):
        _, rows, commit, proof = self.prepared()
        bad_rows = []
        missing = deepcopy(rows); missing.pop('package.json'); bad_rows.append(missing)
        extra = deepcopy(rows); extra['extra'] = rows['package.json']; bad_rows.append(extra)
        for key, value in [('mode', 0o755), ('blob', bytes(20)), ('size', 999), ('sha256', '0' * 64), ('data', b'other')]:
            changed = deepcopy(rows); changed['package.json'][key] = value; bad_rows.append(changed)
        for changed in bad_rows:
            with self.subTest(changed=changed), self.assertRaises(Refused):
                verify_source_subset(changed, commit, proof)
        with self.assertRaises(Refused):
            verify_source_subset(rows, commit + b'changed', proof)

    def test_proof_rejects_missing_extra_changed_and_noncanonical_trees(self):
        _, rows, commit, proof = self.prepared()
        changes = []
        bad = deepcopy(proof); bad['trees'].pop(proof['tree']); changes.append(bad)
        bad = deepcopy(proof); bad['trees']['0' * 40] = b64encode(b'').decode(); changes.append(bad)
        bad = deepcopy(proof); bad['trees'][proof['tree']] += '\n'; changes.append(bad)
        bad = deepcopy(proof); bad['selectedPaths'].reverse(); changes.append(bad)
        bad = deepcopy(proof); bad['executionAuthorized'] = True; changes.append(bad)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(Refused):
                verify_source_subset(rows, commit, changed)

    def test_selection_rejects_links_missing_protected_and_nonliteral_paths(self):
        for paths in (['symlink'], ['missing'], ['scripts/missing'], ['../escape'], ['.env'], ['sub/.env.local'],
                      ['node_modules/pkg/a.js'], ['.npmrc'], [], ['package.json', 'package.json']):
            with self.subTest(paths=paths), self.assertRaises(Refused):
                source_subset(Reader(), paths)

    def test_declared_oversize_blob_is_refused_before_read(self):
        reader = Reader()
        reader.size = lambda oid: 32 * 1024 * 1024 + 1
        with self.assertRaisesRegex(Refused, 'source byte bound'):
            source_subset(reader, ['package.json'])
        self.assertFalse(any(kind == 'blob' for kind, _ in reader.reads))


if __name__ == '__main__':
    unittest.main()
