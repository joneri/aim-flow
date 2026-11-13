"""One actual receipt-chain reconstruction; only external boundaries are fake."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_release_continuation as release
from aim_ci.owned_paths import Refused

AVAILABLE = importlib.util.find_spec('test_delivery_chain') is not None
if AVAILABLE:
    import test_delivery_chain as fixtures


@unittest.skipUnless(AVAILABLE, 'Requires integrated delivery-chain fixture')
class ReleaseHandoffChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DeliveryChainTests.setUpClass()
        cls.addClassCleanup(fixtures.DeliveryChainTests.doClassCleanups)

    def test_real_handoff_reconstruction_and_cross_child_tampering(self):
        case = fixtures.DeliveryChainTests(methodName='test_exact_new_grant_runs_real_receipt_chain_through_pending_pr_and_main_ci')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.github.pr_pending = False; case.github.ci_pending = False
        final = case.start_delivery()
        self.assertEqual(final['status'], 'main-push-handoff-ready', final)
        path = sorted(case.ds.glob('result-*.json'))[-1]
        original = path.read_bytes()
        calls = list(case.github.calls); effects = list(case.github.effects)
        model_calls = list(case.wcalls); container_calls = len(case.container_calls)
        with patch.object(release, 'ROOT', case.installed):
            self.assertEqual(release.verify_handoff(case.pin(path)), final)
            bad = copy.deepcopy(final); bad['phaseGrant']['sha256'] = '0' * 64
            path.write_bytes(fixtures.chain.api.encoded(bad))
            with self.assertRaisesRegex(Refused, 'Handoff evidence differs'):
                release.verify_handoff(case.pin(path))
            path.write_bytes(original)
            child_path = Path(final['mainPushReceipt']['path'])
            child_original = child_path.read_bytes()
            try:
                child = json.loads(child_original); child['pullNumber'] += 1
                child_path.write_bytes(fixtures.chain.api.encoded(child))
                bad = copy.deepcopy(final); bad['mainPushReceipt'] = case.pin(child_path)
                path.write_bytes(fixtures.chain.api.encoded(bad))
                with self.assertRaisesRegex(Refused, 'Main child binding differs'):
                    release.verify_handoff(case.pin(path))
            finally:
                child_path.write_bytes(child_original); path.write_bytes(original)
        self.assertEqual(case.github.calls, calls)
        self.assertEqual(case.github.effects, effects)
        self.assertEqual(case.wcalls, model_calls)
        self.assertEqual(len(case.container_calls), container_calls)


if __name__ == '__main__': unittest.main()
