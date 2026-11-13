"""Real completed runtime process/journal; recovery never executes it again."""
import copy
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import flow_runtime_recovery as recovery
import test_project_command as fixtures
from aim_ci import project_command as command
from aim_ci.owned_paths import Refused

class RuntimeRecoveryTests(fixtures.ProjectCommandTests):
    def pin(self,path):
        return {"path":str(path),"sha256":fixtures.sha(path)}
    def test_completed_inventory_failure_is_reconciled_without_rerun_and_old_result_stays_exact(self):
        self.prepare_runtime_fixture()
        real=command.tree_identity
        def old_inventory(path):
            if Path(path)==self.checkout/'.receipts/runtime':raise Refused('Owned input/output must be a single-link regular file')
            return real(path)
        with patch.object(command,'tree_identity',side_effect=old_inventory):result=self.run_driver()
        self.assertEqual(result['status'],'outcome-unknown');self.assertEqual(result['process']['exitCode'],0)
        original=(self.state/'result.json').read_bytes()
        config={'format':recovery.FORMAT,'schemaVersion':1,'manifestSha256':self.release['manifestSha256'],
            'python':self.pin(self.python),'prior':{'controller':{'path':str(self.installed),'manifestSha256':self.release['manifestSha256']},
            'declaration':self.pin(self.declaration),'state':str(self.state),'receipt':self.pin(self.state/'result.json')}}
        cp=self.case/'recover.json';cp.write_bytes(recovery.api.encoded(config));pin=self.pin(cp);state=self.case/'recovered'
        with patch.object(recovery,'ROOT',self.installed):
            preview=recovery.run(cp,pin['sha256'],state);self.assertFalse(state.exists())
            with patch.object(command,'execute',side_effect=AssertionError('must never rerun')):
                value=recovery.run(cp,pin['sha256'],state,preview['intentSha256'])
                self.assertEqual(value['status'],'runtime-preparation-reconciled');self.assertFalse(value['commandRerun'])
                self.assertEqual(recovery.run(cp,pin['sha256'],state,verify_only=True),value)
            self.assertEqual((self.state/'result.json').read_bytes(),original)
            (self.checkout/'.receipts/runtime/dependency.py').write_text('changed output')
            with self.assertRaises(Refused):recovery.run(cp,pin['sha256'],state,verify_only=True)

for name in dir(fixtures.ProjectCommandTests):
    if name.startswith('test_') and name not in RuntimeRecoveryTests.__dict__:setattr(RuntimeRecoveryTests,name,None)
