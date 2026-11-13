"""Dependency aliases are ordinary files; private control files stay single-link."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from aim_ci.project_execution import tree_identity
from aim_ci.owned_paths import Refused, read_owned_content

class RuntimeHardlinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'runtime';self.root.mkdir()
        self.file=self.root/'binary';self.file.write_bytes(b'fixture binary');self.file.chmod(0o755)
    def test_internal_npm_style_aliases_are_hashed_and_topology_recorded(self):
        alias=self.root/'alias';os.link(self.file,alias)
        result=tree_identity(self.root)
        self.assertEqual(result['files']['binary']['sha256'],result['files']['alias']['sha256'])
        self.assertEqual(result['files']['binary']['hardlinkPaths'],['alias','binary'])
        before=result['sha256'];alias.unlink();alias.write_bytes(b'different')
        self.assertNotEqual(tree_identity(self.root)['sha256'],before)
    def test_alias_outside_runtime_remains_refused(self):
        os.link(self.file,self.base/'outside')
        with self.assertRaisesRegex(Refused,'entirely within'):tree_identity(self.root)
    def test_escaping_symbolic_links_remain_refused(self):
        p=self.base/'outside';p.write_bytes(b'external');(self.root/'escape').symlink_to(p)
        with self.assertRaisesRegex(Refused,'within'):tree_identity(self.root)
    def test_private_control_files_are_still_required_to_be_single_link(self):
        os.link(self.file,self.root/'alias')
        with self.assertRaisesRegex(Refused,'single-link'):read_owned_content(self.root,'binary')

if __name__=='__main__':unittest.main()
