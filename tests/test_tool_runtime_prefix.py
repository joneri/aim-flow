"""Canonical helper discovery survives executable aliases in a private PATH."""
from pathlib import Path
import hashlib,shlex,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from aim_ci.project_command import qualified_tool_environment

class ToolPrefixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.core=self.root/'installation/libexec/git-core';self.core.mkdir(parents=True)
        self.git=self.root/'installation/bin/git';self.git.parent.mkdir()
        self.git.write_text("#!/bin/sh\nprintf '%s\\n' "+shlex.quote(str(self.core))+"\n");self.git.chmod(0o755)
        self.pin={'path':str(self.git),'sha256':hashlib.sha256(self.git.read_bytes()).hexdigest()}
        self.helper=self.core/'git-remote-http';self.helper.write_text('#!/bin/sh\nexit 0\n');self.helper.chmod(0o755)
        (self.core/'git-remote-https').symlink_to('git-remote-http')
    def test_prefix_is_derived_from_canonical_tool_and_protocol_helper_is_pinned(self):
        env,support=qualified_tool_environment({'git':self.pin},self.pin,{'LC_ALL':'C'})
        self.assertEqual(env['GIT_EXEC_PATH'],str(self.core));self.assertNotIn('PATH',env)
        self.assertEqual(support['git']['helpers']['git-remote-https']['path'],str(self.helper))
        before=support['git']['helpers']['git-remote-http']['sha256'];self.helper.write_text('#!/bin/sh\nexit 7\n')
        self.assertNotEqual(qualified_tool_environment({'git':self.pin},self.pin,{})[1]['git']['helpers']['git-remote-http']['sha256'],before)
    def test_explicit_environment_is_preserved(self):
        expected={'GIT_EXEC_PATH':str(self.core)}
        self.assertEqual(qualified_tool_environment({'git':self.pin},self.pin,expected),(expected,{}))
    def test_existing_legacy_intent_does_not_gain_new_runtime_authority(self):
        state=self.root/'old-state';state.mkdir();(state/'intent.json').write_text('{}')
        self.git.unlink()
        self.assertEqual(qualified_tool_environment({'git':self.pin},self.pin,{'LC_ALL':'C'},state=state),({'LC_ALL':'C'},{}))
    def test_an_unrelated_named_tool_does_not_gain_a_metadata_probe(self):
        self.git.unlink()
        self.assertEqual(qualified_tool_environment({'python':self.pin},self.pin,{'LC_ALL':'C'}),({'LC_ALL':'C'},{}))

if __name__=='__main__':unittest.main()
