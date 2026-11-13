"""Regression cases for substantive criteria and unchanged opaque source context."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import flow_candidate_review as review
import flow_command_source_context as context
import flow_onboarding_delivery as shapes
import flow_workflow_validation as validation
from aim_ci.owned_paths import Refused

GIT = Path(shutil.which('git') or '/nonexistent/git').resolve()


class RequirementSectionTests(unittest.TestCase):
    def test_headings_follow_substance_and_preserve_every_byte(self):
        original = 'Story\r\n\r\n## Acceptance criteria\r\n\r\n### AC1\r\n\r\n- Pause.\r\n\r\n## End\r\n'
        legacy = validation.requirement_document(original)
        current = validation.requirement_document(original, merge_heading_sections=True)
        self.assertEqual(''.join(row['text'] for row in current['criteria']), original)
        self.assertEqual(len(legacy['criteria']), 4)
        self.assertEqual(len(current['criteria']), 2)
        self.assertIn('## Acceptance criteria', current['criteria'][1]['text'])
        self.assertIn('- Pause.', current['criteria'][1]['text'])
        self.assertIn('## End', current['criteria'][1]['text'])
        self.assertEqual(current['original'], original)

    def test_heading_only_document_refuses_without_erasing_original_evidence(self):
        for original in ('## Acceptance criteria\n\n', '# Story\n\n## Acceptance\n'):
            self.assertTrue(validation.requirement_document(original)['criteria'])
            with self.assertRaisesRegex(Refused, 'headings only'):
                validation.requirement_document(original, merge_heading_sections=True)

    def test_oversized_heading_prefix_refuses_instead_of_creating_an_empty_obligation(self):
        original = '# ' + 'H' * 1997 + '\nA real obligation.\n'
        with self.assertRaisesRegex(Refused, 'heading prefix'):
            validation.requirement_document(original, merge_heading_sections=True)

    def test_substantive_long_section_remains_complete_and_bounded(self):
        original = '## Acceptance\n\n### AC1\n\n' + '- Keep this obligation.\n' * 160
        value = validation.requirement_document(original, merge_heading_sections=True)
        self.assertEqual(''.join(row['text'] for row in value['criteria']), original)
        self.assertTrue(all(len(row['text']) <= 2000 for row in value['criteria']))


class CoverageWireTests(unittest.TestCase):
    def test_new_wire_forbids_covered_without_evidence_but_preserves_legacy_schema(self):
        old = copy.deepcopy(review.SCHEMA)
        schema = review.coverage_schema(old, review.REVIEW_EVIDENCE_POLICY)
        value = {'summary': 'Unexecuted source review', 'findings': [], 'unknowns': [],
                 'coverage': [{'requirementId': 'c1', 'status': 'covered',
                               'analysis': 'No source supplied', 'sources': []}]}
        shapes.validate_shape(value, old)
        with self.assertRaises(Refused):
            shapes.validate_shape(value, schema)
        value['coverage'][0]['status'] = 'unverified'
        shapes.validate_shape(value, schema)
        value['coverage'][0].update(status='covered', sources=[{'path': 'candidate/test.js', 'startLine': 1, 'endLine': 1}])
        shapes.validate_shape(value, schema)
        self.assertEqual(old, review.SCHEMA)

    def test_unknown_policies_refuse(self):
        for value in (False, True, '', 'invented-policy'):
            with self.assertRaises(Refused):
                review.evidence_policy(value)


class OpaqueLiteralTests(unittest.TestCase):
    def test_values_are_withheld_with_equal_identity_and_exact_line_inventory(self):
        secret = 'synthetic-password-for-offline-test'
        text = 'const a = {password: "' + secret + '"};\r\nconst b = {password: "' + secret + '"};\r\n'
        projected = context.project_credential_literals(text)
        self.assertNotIn(secret, json.dumps(projected))
        self.assertEqual(projected['text'].splitlines(), ['const a = {password: "[opaque 1]"};', 'const b = {password: "[opaque 1]"};'])
        metadata = projected['sourceProjection']
        self.assertEqual(metadata['originalSha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual([x['startLine'] for x in metadata['redactions']], [1, 2])
        self.assertEqual([x['opaqueId'] for x in metadata['redactions']], [1, 1])
        self.assertIsNone(context.SECRET_TEXT.search(projected['text']))

    def test_equal_values_in_comparisons_are_also_withheld(self):
        secret = 'synthetic-equal-password-1234'
        text = 'const a = {password: "' + secret + '"};\nassert.equal(input, "' + secret + '");\n'
        projection = context.project_credential_literals(text)
        self.assertNotIn(secret, json.dumps(projection))
        self.assertEqual([r['opaqueId'] for r in projection['sourceProjection']['redactions']], [1, 1])
        self.assertEqual(projection['text'].count('[opaque 1]'), 2)
        self.assertIsNone(context.project_credential_literals(text + '// ' + secret))

    def test_only_supported_quoted_values_project(self):
        self.assertIsNotNone(context.project_credential_literals("const a = 'Bearer synthetic-token-123456';"))
        self.assertIsNotNone(context.project_credential_literals("const a = 'ghp_12345678901234567890';"))
        self.assertIsNone(context.project_credential_literals('Bearer synthetic-token-123456'))
        self.assertIsNone(context.project_credential_literals('ghp_12345678901234567890'))
        self.assertIsNone(context.project_credential_literals('-----BEGIN RSA PRIVATE KEY-----'))
        self.assertIsNone(context.project_credential_literals('const key = \"-----BEGIN RSA PRIVATE KEY-----\";'))
        self.assertIsNone(context.project_credential_literals('const password = process.env.PASSWORD;'))


class ExecutableContextPriorityTests(unittest.TestCase):
    def test_literal_data_does_not_starve_required_executable_imports(self):
        from test_command_source_context import Reader
        from unittest.mock import patch
        files = {'package.json': '{"scripts":{"test":"node front.js && node backend.js"}}',
            'front.js': 'const data = "content/big.json";',
            'backend.js': 'require("./routes/api.js");',
            'routes/api.js': '// required executable implementation\n' + 'x' * 35,
            'content/big.json': '"' + 'd' * 110 + '"'}
        ceiling = sum(len(text.encode()) for path, text in files.items() if path != 'routes/api.js')
        with patch.dict(context.LIMITS, {'sourceBytes': ceiling}):
            old = context.derive(Reader(files), [{'root': '.', 'operation': 'run', 'script': 'test'}])
            new = context.derive(Reader(files), [{'root': '.', 'operation': 'run', 'script': 'test'}], project_secrets=True)
        self.assertNotIn('routes/api.js', old['sourcePaths'])
        self.assertIn('routes/api.js', new['sourcePaths'])
        self.assertNotIn('content/big.json', new['sourcePaths'])
        self.assertTrue(any(row.get('target') == 'content/big.json' and row['reason'] == 'whole-file-byte-bound' for row in new['unresolved']))


class ProjectedPacketTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='flow-review-policy-')
        self.addCleanup(tmp.cleanup)
        self.checkout = Path(tmp.name).resolve()
        self.git('init', '-q', '--initial-branch=main', '--template=')
        self.git('config', 'user.name', 'Offline fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.checkout / 'package.json').write_text('{"scripts":{"test":"node test.js"}}\n')
        self.secret = 'synthetic-test-password-1234'
        (self.checkout / 'test.js').write_text('require("./helper.js");\nconst input = {password: "' + self.secret + '"};\n')
        (self.checkout / 'helper.js').write_text('module.exports = function fixture() { return true; };\n')
        (self.checkout / 'feature.js').write_text('export const enabled = false;\n')
        self.git('add', '.'); self.git('commit', '-qm', 'Baseline fixture')
        self.base = self.git('rev-parse', 'HEAD')
        (self.checkout / 'feature.js').write_text('export const enabled = true;\n')
        self.git('add', '.'); self.git('commit', '-qm', 'Candidate fixture')
        self.source = {'checkout': str(self.checkout), 'baselineCommit': self.base,
            'candidateCommit': self.git('rev-parse', 'HEAD'), 'git': str(GIT),
            'gitSha256': hashlib.sha256(GIT.read_bytes()).hexdigest()}

    def git(self, *args):
        return subprocess.check_output([str(GIT), '-C', str(self.checkout), *args], stderr=subprocess.PIPE).decode().strip()

    def packet(self, policy=review.REVIEW_EVIDENCE_POLICY):
        return review.build_packet(ROOT, self.source,
            {'original': 'Enable the feature.', 'criteria': [{'id': 'c1', 'text': 'Enable feature.'}]},
            'Approved original plan.', [], deduplicate_unchanged=True,
            command_source_stages=[{'root': '.', 'operation': 'run', 'script': 'test'}],
            citation_protocol=review.CITATION_PROTOCOL, review_evidence_policy=policy)

    def test_unchanged_test_implementation_and_its_imports_are_reviewable_without_values(self):
        packet = self.packet()
        self.assertNotIn(self.secret, json.dumps(packet))
        sources = {row['path']: row for row in packet['sources']}
        self.assertIn('candidate/test.js', sources)
        self.assertIn('candidate/helper.js', sources)
        selected = sources['candidate/test.js']
        self.assertEqual(selected['lineCount'], 2)
        self.assertEqual(selected['sha256'], hashlib.sha256(selected['text'].encode()).hexdigest())
        self.assertEqual(selected['sourceProjection']['originalSha256'], hashlib.sha256((self.checkout / 'test.js').read_bytes()).hexdigest())
        unchanged = next(row for row in packet['unchangedSources'] if row['path'] == 'test.js')
        self.assertEqual(unchanged['sha256'], selected['sha256'])
        self.assertEqual(packet['reviewEvidencePolicy'], review.REVIEW_EVIDENCE_POLICY)
        self.assertIn('credential-literal-values-withheld', {r['reason'] for r in packet['commandSourceContext']['unresolved']})
        # The actual execution input is untouched.
        self.assertIn(self.secret, (self.checkout / 'test.js').read_text())
        self.assertEqual(self.git('status', '--porcelain'), '')
        body = {'summary': 'Source only', 'findings': [], 'unknowns': ['Credential literal contents are withheld.'],
                'coverage': [{'requirementId': 'c1', 'status': 'covered', 'analysis': 'Visible source inspected.',
                              'sources': [{'path': 'candidate/test.js', 'startLine': 1, 'lineCount': 2}]}]}
        response = {'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': json.dumps(body)}]}]}
        parsed = review.parse_response(response, packet)
        self.assertEqual(parsed['coverage'][0]['sources'][0]['sha256'], selected['sha256'])
        self.assertNotIn(self.secret, json.dumps(parsed))
        import flow_execution_review as executed
        executed.check_packet_disclosure(packet)
        evidence, images = executed.evidence_packet(packet, parsed,
            {'validation': {'status': 'passed', 'intentSha256': 'a' * 64,
                'report': {'runtime': {'synthetic': True}, 'stages': [], 'outputs': {}}}},
            {'sha256': 'b' * 64})
        self.assertEqual(evidence['reviewEvidencePolicy'], review.REVIEW_EVIDENCE_POLICY)
        self.assertEqual(evidence['sources'], packet['sources'])
        self.assertTrue(any('withheld' in row['value'] for row in evidence['priorOpenItems'] if row['origin'] == 'source-unknown'))
        self.assertNotIn(self.secret, json.dumps(evidence))

    def test_same_value_in_another_unprojected_file_refuses_the_whole_packet(self):
        (self.checkout / 'helper.js').write_text('module.exports = "' + self.secret + '";\n')
        self.git('add', '.'); self.git('commit', '-qm', 'Repeated comparison fixture')
        self.source['candidateCommit'] = self.git('rev-parse', 'HEAD')
        # Also make the helper unchanged from the assessed baseline.
        self.source['baselineCommit'] = self.source['candidateCommit']
        (self.checkout / 'feature.js').write_text('export const enabled = 2;\n')
        self.git('add', '.'); self.git('commit', '-qm', 'New feature fixture')
        self.source['candidateCommit'] = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Refused, 'reappears outside'):
            self.packet()

    def test_legacy_packet_omits_the_same_suspected_file(self):
        packet = self.packet(None)
        self.assertNotIn('reviewEvidencePolicy', packet)
        self.assertNotIn('candidate/test.js', {s['path'] for s in packet['sources']})
        self.assertIn('suspected-secret-content', {r['reason'] for r in packet['commandSourceContext']['unresolved']})

    def test_changed_credential_file_is_not_hidden_by_projection(self):
        (self.checkout / 'test.js').write_text('const input = {password: "changed-synthetic-password-1234"};\n')
        self.git('add', '.'); self.git('commit', '-qm', 'Changed fixture credential')
        self.source['candidateCommit'] = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Refused, 'suspected secret'):
            self.packet()


class ArtifactAndRepairProjectionTests(unittest.TestCase):
    def test_provider_schema_refuses_manifest_citation_and_allows_only_displayed_protected_code(self):
        import flow_workspace_review as workspace
        packet = {'reviewEvidencePolicy': review.REVIEW_EVIDENCE_POLICY,
            'sources': [{'path': 'candidate/scripts/test.mjs'}, {'path': 'candidate/package.json'},
                        {'path': 'candidate/unprotected.js'}, {'path': 'baseline/scripts/old.mjs'}],
            'workspaceContext': {'protectedPaths': ['scripts/test.mjs', 'package.json', 'scripts/undisplayed.mjs']}}
        original = copy.deepcopy(workspace.CAPTURE_SCHEMA)
        schema = workspace.artifact_code_schema(original, packet)
        refs = schema['properties']['artifactSelection']['items']['properties']['sources']
        self.assertEqual(refs['items']['properties']['path']['enum'], ['candidate/scripts/test.mjs'])
        shapes.validate_shape([{'path': 'candidate/scripts/test.mjs', 'startLine': 1, 'endLine': 1}], refs)
        for path in ('candidate/package.json', 'candidate/unprotected.js', 'candidate/scripts/undisplayed.mjs',
                     'baseline/scripts/old.mjs'):
            with self.subTest(path=path), self.assertRaises(Refused):
                shapes.validate_shape([{'path': path, 'startLine': 1, 'endLine': 1}], refs)
        with self.assertRaises(Refused): shapes.validate_shape([], refs)
        self.assertEqual(original, workspace.CAPTURE_SCHEMA)
        self.assertEqual(workspace.artifact_code_schema(original, {}), original)
        packet['sources'] = []
        schema = workspace.artifact_code_schema(original, packet)
        self.assertEqual(schema['properties']['artifactSelection']['maxItems'], 0)

    def test_repair_rederives_exact_unchanged_projection_and_refuses_changed_or_forged_displays(self):
        import flow_repair_candidate as repair
        raw = b'const auth = {password: "synthetic-private-value-12345"};\n'
        projected = context.project_credential_literals(raw.decode())
        source = {**projected, 'sha256': hashlib.sha256(projected['text'].encode()).hexdigest()}
        self.assertEqual(repair.rejected_source_display(source, raw, True, review.REVIEW_EVIDENCE_POLICY),
                         projected['text'].encode())
        for unchanged, policy in ((False, review.REVIEW_EVIDENCE_POLICY), (True, None), (True, 'unknown')):
            with self.assertRaises(Refused): repair.rejected_source_display(source, raw, unchanged, policy)
        changed = copy.deepcopy(source); changed['sourceProjection']['originalSha256'] = '0' * 64
        with self.assertRaises(Refused): repair.rejected_source_display(changed, raw, True, review.REVIEW_EVIDENCE_POLICY)
        changed = copy.deepcopy(source); changed['text'] += 'forged\n'
        changed['sha256'] = hashlib.sha256(changed['text'].encode()).hexdigest()
        with self.assertRaises(Refused): repair.rejected_source_display(changed, raw, True, review.REVIEW_EVIDENCE_POLICY)
        ordinary = {'text': 'const ordinary = 1;\n', 'sha256': hashlib.sha256(b'const ordinary = 1;\n').hexdigest()}
        self.assertEqual(repair.rejected_source_display(ordinary, ordinary['text'].encode(), False, None),
                         ordinary['text'].encode())


if __name__ == '__main__':
    unittest.main()
