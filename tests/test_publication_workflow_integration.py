"""Actual Flow candidate verifier → publication handoff; external transports stay injected."""
import importlib.util
import json
import subprocess
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import flow_github_publication as pub
import test_github_publication as publication

AVAILABLE = importlib.util.find_spec('flow_workflow') is not None and importlib.util.find_spec('test_requirement_workflow') is not None


@unittest.skipUnless(AVAILABLE, 'Requires the separately implemented Flow requirement workflow integration')
class PublicationWorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_requirement_workflow as fixtures
        cls.fixture = fixtures.RequirementWorkflowTests
        cls.fixture.setUpClass()
        # Use the actual installed developer tool, not Apple's hard-linked xcrun launcher.
        if sys.platform == 'darwin' and cls.fixture.git.stat().st_nlink != 1:
            cls.fixture.git = Path(subprocess.check_output(['/usr/bin/xcrun', '--find', 'git'], text=True).strip()).resolve()
            cls.fixture.git_pin = publication.pin(cls.fixture.git)['sha256']
        cls.addClassCleanup(cls.fixture.doClassCleanups)

    def test_actual_installed_flow_candidate_verifier_feeds_exact_draft_handoff(self):
        work = self.fixture('test_workflow_installed_verifier_reconciles_real_candidate')
        self.addCleanup(work.doCleanups); work.setUp()
        result = work.grant()
        self.assertEqual(result['status'], 'candidate-prepared')
        self.assertEqual(len(work.wcalls), 4)  # Two synthetic-provider phases, no live provider or key.
        evidence = {'candidate': result['candidateReceipt']}
        for role in ('aggregate', 'browser', 'independentReview'):
            path = work.case / (role + '-publication-fixture.json')
            path.write_text(json.dumps({'synthetic': True, 'qualification': 'integration contract only', 'source': result['source']}))
            evidence[role] = publication.pin(path)
        body = work.case / 'draft-body.md'
        body.write_text('Requirement candidate from Flow.\nProvider, validation and GitHub observations are offline fixtures; no live qualification.\n')
        gh = work.case / 'fixture-gh'; gh.write_text('#!/bin/sh\nexit 90\n'); gh.chmod(0o700)
        self.c = {'format': 'aim-flow-github-publication', 'schemaVersion': 1,
            'manifestSha256': work.release['manifestSha256'], 'repository': {'name': 'fixture/project', 'id': 123},
            'source': {**result['source'], 'git': {'path': str(work.git), 'sha256': work.git_pin}},
            'branch': 'flow/integration-candidate', 'baseRef': 'main',
            'pull': {'title': 'Apply Flow requirement candidate', 'body': publication.pin(body)}, 'evidence': evidence,
            'transport': {'gh': publication.pin(gh), 'home': str(work.case), 'configDirectory': str(work.case), 'timeoutSeconds': 20},
            'candidateVerification': {'controller': {'path': str(work.installed), 'manifestSha256': work.release['manifestSha256']},
                'python': publication.pin(Path(sys.executable).resolve()), 'config': publication.pin(work.wpath), 'state': str(work.ws)}}
        config = work.case / 'publication-integration.json'; config.write_text(json.dumps(self.c))
        self.state = work.case / 'publication-integration-state'
        transport = publication.FakeGitHub(self)
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in work.ws.rglob('*') if p.is_file()}
        with patch.object(pub, 'ROOT', work.installed):
            preview = pub.run(config, publication.pin(config)['sha256'], self.state)
            published = pub.run(config, publication.pin(config)['sha256'], self.state, preview['intentSha256'],
                                transport=transport, push_transport=transport.push)
        self.assertEqual(published['status'], 'draft-pr-verified', published)
        self.assertEqual(published['deliveryPull']['headSha'], result['source']['headSha'])
        self.assertEqual(published['source']['diffSha256'], result['source']['diffSha256'])
        self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in work.ws.rglob('*') if p.is_file()})
        self.assertEqual(len(work.wcalls), 4)
        self.assertEqual(len(transport.pushes), 1)
        self.assertEqual(len([v for v in transport.calls if v[0] == 'POST']), 1)


if __name__ == '__main__':
    unittest.main()
