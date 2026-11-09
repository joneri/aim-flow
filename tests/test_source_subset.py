"""Exact committed membership for bounded source selection; no omitted blob reads."""
from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci.source_subset import git_id, selected_paths, source_subset, verify_source_subset
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

    def test_stale_head_refuses_after_complete_pinned_reads(self):
        reader = Reader()
        def stale():
            raise Refused('Checkout HEAD differs from the selected commit')
        reader.verify_head = stale
        with self.assertRaisesRegex(Refused, 'HEAD differs'):
            source_subset(reader, ['package.json'])

    def test_selection_rejects_links_missing_protected_and_nonliteral_paths(self):
        for paths in (['symlink'], ['missing'], ['scripts/missing'], ['../escape'], ['.env'], ['sub/.env.local'],
                      ['node_modules/pkg/a.js'], ['.npmrc'], [], ['package.json', 'package.json']):
            with self.subTest(paths=paths), self.assertRaises(Refused):
                source_subset(Reader(), paths)

    def test_large_blob_and_more_than_one_hundred_paths_have_complete_proof(self):
        reader = Reader()
        data = b'x' * (32 * 1024 * 1024 + 1)
        blob = reader.add('blob', data)
        paths = [f'file-{index:04}.css' for index in range(125)]
        small = reader.add('blob', b'body{}')
        raw = b''.join(b'100644 ' + name.encode() + b'\0' + (blob if name == paths[0] else small) for name in paths)
        reader.tree = reader.add('tree', raw).hex()
        reader.commit = reader.add('commit', f'tree {reader.tree}\n\nlarge source\n').hex()
        rows, commit, proof = source_subset(reader, paths)
        self.assertEqual(set(rows), set(paths))
        self.assertEqual(rows[paths[0]]['data'], data)
        self.assertEqual(proof['selectedPaths'], sorted(paths))
        verify_source_subset(rows, commit, proof)

    def test_proof_keeps_large_ancestor_and_many_distinct_trees_without_omitted_reads(self):
        reader = Reader()
        root, paths = [], []
        for index in range(260):
            name = f'directory-{index:03}'
            blob = reader.add('blob', str(index).encode())
            tree = reader.add('tree', b'100644 check.js\0' + blob)
            root.append(b'40000 ' + name.encode() + b'\0' + tree)
            paths.append(name + '/check.js')
        # Large membership proofs must remain complete, including omitted sibling IDs.
        for index in range(40000):
            root.append(f'100644 omitted-{index:06}.bin'.encode() + b'\0' + bytes.fromhex('f' * 40))
        reader.tree = reader.add('tree', b''.join(root)).hex()
        reader.commit = reader.add('commit', f'tree {reader.tree}\n\nlarge proof\n').hex()
        rows, commit, proof = source_subset(reader, paths)
        self.assertEqual(len(proof['trees']), 261)
        self.assertGreater(sum(len(value) for value in proof['trees'].values()), 2_000_000)
        self.assertNotIn('f' * 40, [oid for kind, oid in reader.reads])
        verify_source_subset(rows, commit, proof)
        changed = deepcopy(proof)
        changed['trees'][reader.tree] = changed['trees'][reader.tree][:-4]
        with self.assertRaises(Refused):
            verify_source_subset(rows, commit, changed)

    def test_build_preparation_inventory_accepts_large_source_and_many_files(self):
        from aim_ci.build_preparation import source_inventory
        reader = Reader()
        data = b'x' * (32 * 1024 * 1024 + 1)
        large = reader.add('blob', data)
        small = reader.add('blob', b'body{}')
        entries = [(f'file-{index:04}.css', '100644', 'blob', (large if index == 0 else small).hex())
                   for index in range(4100)]
        raw = b''.join(b'100644 ' + name.encode() + b'\0' + bytes.fromhex(oid)
                       for name, _, _, oid in entries)
        reader.tree = reader.add('tree', raw).hex()
        reader.commit = reader.add('commit', f'tree {reader.tree}\n\nlarge build\n').hex()
        reader.entries = lambda commit, *, recursive: iter(entries)
        reader.blob = lambda oid, maximum: reader.checked_object('blob', oid, maximum)
        source, commit = source_inventory(reader)
        self.assertEqual(len(source), 4100)
        self.assertEqual(source['file-0000.css']['data'], data)
        self.assertEqual(git_id('commit', commit).hex(), reader.commit)

    def test_invalid_measured_size_and_changed_measured_length_fail(self):
        for size in (-1, True, 'large'):
            reader = Reader()
            reader.size = lambda oid: size
            with self.subTest(size=size), self.assertRaisesRegex(Refused, 'measured source object size'):
                source_subset(reader, ['package.json'])
            self.assertFalse(any(kind == 'blob' for kind, _ in reader.reads))
        reader = Reader()
        original = reader.size
        reader.size = lambda oid: original(oid) + 1
        with self.assertRaisesRegex(Refused, 'changed while reading'):
            source_subset(reader, ['package.json'])


if __name__ == '__main__':
    unittest.main()
