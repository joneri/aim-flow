"""Check installed API wiring and critical replay/accounting contracts, never the API."""
import hashlib
import io
import json
import tarfile
import zipfile
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()


class InstalledApiDeliveryTests(unittest.TestCase):
    def test_fresh_install_runs_selected_sol_contracts_without_source_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            release = base / 'release'
            installed = base / 'installed'
            empty = base / 'no-tools'
            empty.mkdir()
            environment = {'PATH': str(empty), 'HOME': str(base), 'LC_ALL': 'C'}

            def invoke(*args):
                result = subprocess.run([sys.executable, '-IBS', *map(str, args)], cwd=base,
                                        env=environment, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            invoke(ROOT / 'aim-flow.py', 'build-release', '--version', VERSION, '--output', release)
            metadata = json.loads((release / 'release.json').read_text())
            invoke(release / 'aim-ci-bootstrap.pyz', 'install', '--archive', release / metadata['archive'],
                   '--sha256', metadata['archiveSha256'], '--destination', installed)
            manifest = json.loads((installed / 'control-manifest.json').read_text())
            self.assertEqual(manifest['schemaVersion'], 3)
            for name in ('aim-flow.py', 'lib/flow_api_delivery.py', 'docs/API-DELIVERY.md'):
                self.assertEqual((installed / name).read_bytes(), (ROOT / name).read_bytes())
            self.assertFalse((installed / 'lib/flow_agent_delivery.py').exists())
            version = invoke(installed / 'aim-flow.py', '--version')
            self.assertEqual(version.stdout.strip(), 'AIM Flow ' + VERSION)
            self.assertIn('--authorize-intent', invoke(installed / 'aim-flow.py', 'deliver-api', '--help').stdout)
            # Every imported adapter and helper comes from the installed path. The test
            # file only supplies generated fixtures; no source checkout is on sys.path.
            environment['AIM_FLOW_TEST_ROOT'] = str(installed)
            result = invoke(ROOT / 'tests/test_api_delivery.py',
                'ApiDeliveryTests.test_success_durable_reservation_and_unchanged_sources_history',
                'ApiDeliveryTests.test_exact_authorization_required',
                'ApiDeliveryTests.test_no_replay_same_state_or_same_payload_new_state',
                'ApiDeliveryTests.test_combined_campaign_ceiling_and_full_reservations', '-v')
            self.assertIn('OK', result.stderr)
            self.assertNotIn('skipped=', result.stderr, 'Installed contracts must actually execute')
            print('Installed API selection: ' + result.stderr.split('Ran ')[1].splitlines()[0])

    def test_version_uses_source_or_installed_label_and_refuses_invalid_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            entry = base / 'aim-flow.py'
            entry.write_bytes((ROOT / 'aim-flow.py').read_bytes())
            (base / 'VERSION').write_text(VERSION + '\n')
            def invoke():
                return subprocess.run([sys.executable, '-IBS', str(entry), '--version'],
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(invoke().stdout.strip(), 'AIM Flow ' + VERSION)
            manifest = base / 'control-manifest.json'
            manifest.write_text(json.dumps({'version': '0.1.0-local.42'}))
            self.assertEqual(invoke().stdout.strip(), 'AIM Flow 0.1.0-local.42')
            for value in ({'version': 'invalid label'}, [], {'version': 42}):
                manifest.write_text(json.dumps(value))
                self.assertNotEqual(invoke().returncode, 0)
            manifest.write_text(' ' * 65537)
            self.assertNotEqual(invoke().returncode, 0)

    def test_old_schema_three_inventory_needs_its_matching_bootstrap(self):
        sys.path.insert(0, str(ROOT / 'lib'))
        from aim_ci.control_archive import Refused, parse_manifest
        # Use the actual historical inventory. Subtracting a hand-maintained
        # list from today's FILES makes unrelated additions break this contract.
        with zipfile.ZipFile(ROOT / 'site/downloads/aim-flow-0.1.0-beta.8.zip') as bundle:
            prefix = 'aim-flow-0.1.0-beta.8/controller/'
            release = json.loads(bundle.read(prefix + 'release.json'))
            archive = bundle.read(prefix + release['archive'])
        self.assertEqual(hashlib.sha256(archive).hexdigest(), release['archiveSha256'])
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as archive:
            member = next(row for row in archive if row.name.endswith('control-manifest.json'))
            manifest = archive.extractfile(member).read()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), release['manifestSha256'])
        self.assertEqual(json.loads(manifest)['schemaVersion'], 3)
        with self.assertRaises(Refused):
            parse_manifest(manifest)

    def test_schema_two_manifest_is_not_silently_upgraded(self):
        sys.path.insert(0, str(ROOT / 'lib'))
        from aim_ci.control_archive import FILES, FORMAT, Refused, parse_manifest
        manifest = {'format': FORMAT, 'schemaVersion': 2, 'version': '0.1.0-beta.7',
                    'files': {name: '0' * 64 for name in FILES}}
        with self.assertRaises(Refused):
            parse_manifest(json.dumps(manifest).encode())


if __name__ == '__main__':
    unittest.main()
