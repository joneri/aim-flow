"""Check installed publication wiring and critical push/PR no-replay contracts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstalledGitHubPublicationTests(unittest.TestCase):
    def test_fresh_installed_publication_contracts_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); release = base / 'release'; installed = base / 'installed'
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(base), 'LC_ALL': 'C'}
            def run(*args):
                result = subprocess.run([str(Path(sys.executable).resolve()), '-IBS', *map(str, args)], cwd=base,
                    env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result
            run(ROOT / 'aim-flow.py', 'build-release', '--version', '0.10.0-publication-installed.1', '--output', release)
            metadata = json.loads((release / 'release.json').read_bytes())
            run(release / 'aim-ci-bootstrap.pyz', 'install', '--archive', release / metadata['archive'],
                '--sha256', metadata['archiveSha256'], '--destination', installed)
            self.assertIn('--authorize-draft-intent', run(installed / 'aim-flow.py', 'publish-github', '--help').stdout)
            env['AIM_FLOW_TEST_ROOT'] = str(installed)
            result = run(ROOT / 'tests/test_github_publication.py',
                'GitHubPublicationTests.test_success_binds_draft_identity_for_merge_without_editing_any_existing_pr',
                'GitHubPublicationTests.test_unknown_push_stops_and_read_only_resume_never_retries',
                'GitHubPublicationTests.test_unknown_draft_never_replays_even_if_absent_and_can_reconcile', '-v')
            self.assertIn('OK', result.stderr)
            self.assertNotIn('skipped=', result.stderr, 'Installed contracts must actually execute')
            print('Installed publication selection: ' + result.stderr.split('Ran ')[1].splitlines()[0])


if __name__ == '__main__':
    unittest.main()
