"""New opt-in wire bounds with legacy evidence preserved; wholly offline."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_candidate_review as base
import flow_execution_refinement as refinement
import flow_onboarding_delivery as compact
import flow_workspace_review as workspace
from aim_ci.owned_paths import Refused
import test_workspace_review as fixtures


def references(body):
    for item in body['findings'] + body['coverage']:
        yield from item['sources']
    for item in body['workspacePolicy']['generatedOutputs']:
        yield from item['generatorSources']
        yield from item['lifecycleSources']
    for item in body['artifactSelection'] + body['executionRefinement']:
        yield from item['sources']


class BoundedCitationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ArtifactSelectionTests()
        fixture.setUp()
        self.packet, self.body = fixture.packet, fixture.body
        self.packet['workspaceContext']['refineExecutionPlan'] = True
        self.packet['approvedPlan'] = json.dumps({'assessment': {'steps': [
            {'id': 'test', 'area': 'tests', 'command': 'npm run test', 'dependsOn': []}]}})
        self.body['executionRefinement'] = [{'stepId': 'test', 'kind': 'finite-local',
            'evidenceSteps': [], 'executionDependsOn': [], 'analysis': 'Source only',
            'sources': [fixture.ref('test.js')]}]
        self.body['findings'] = [{'id': 'issue', 'description': 'Evidence remains source only',
            'severity': 'low', 'requirementIds': ['r'], 'sources': [fixture.ref('test.js')]}]
        self.body['coverage'][0].update(status='covered', sources=[fixture.ref('test.js')])
        for source in self.packet['sources']:
            # CRLF plus an unterminated final line exercise exact line identity.
            source['text'] += 'evidence line\r\n' * 127 + 'last line'
            source['sha256'] = api.digest(source['text'].encode())
        self.legacy_packet, self.legacy_body = copy.deepcopy((self.packet, self.body))
        self.packet['citationProtocol'] = base.CITATION_PROTOCOL
        for source in self.packet['sources']:
            source['lineCount'] = len(source['text'].splitlines(keepends=True))
        for ref in references(self.body):
            ref['lineCount'] = ref.pop('endLine') - ref['startLine'] + 1

    @staticmethod
    def response(body):
        return {'output': [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': json.dumps(body)}]}]}

    def parse(self, body=None, packet=None):
        return workspace.parse_response(self.response(self.body if body is None else body),
                                        self.packet if packet is None else packet)

    def roundtrip(self, result, packet):
        with tempfile.TemporaryDirectory() as folder:
            pins = []
            for name, value in [('review', result), ('packet', packet)]:
                path = Path(folder).resolve() / (name + '.json')
                path.write_bytes(api.encoded(value))
                pins.append({'path': str(path), 'sha256': api.digest(path.read_bytes())})
            return workspace.verify_evidence(*pins)

    def test_new_and_legacy_roundtrip_all_typed_citation_sites(self):
        legacy = self.parse(self.legacy_body, self.legacy_packet)
        self.assertNotIn('citationProtocol', legacy)
        self.assertEqual(self.roundtrip(legacy, self.legacy_packet), legacy)
        current = self.parse()
        self.assertEqual(current['citationProtocol'], base.CITATION_PROTOCOL)
        self.assertEqual(self.roundtrip(current, self.packet), current)
        for old, new in zip(references(legacy), references(current)):
            self.assertEqual(old, new)
            self.assertEqual(set(new), {'path', 'startLine', 'endLine', 'sha256', 'quote'})
        # Caller response and packet are never rewritten in place.
        self.assertTrue(all('lineCount' in ref and 'endLine' not in ref for ref in references(self.body)))

    def test_all_schema_sites_bound_count_without_mutating_legacy(self):
        old = base.obj({**workspace.CAPTURE_SCHEMA['properties'], 'executionRefinement': refinement.SCHEMA})
        snapshot = copy.deepcopy(old)
        new = base.citation_schema(old, base.CITATION_PROTOCOL)
        self.assertEqual(old, snapshot)
        def citation_sites(value, path=()):
            sites = {}
            if isinstance(value, dict):
                properties = value.get('properties', {})
                if 'path' in properties and 'startLine' in properties:
                    sites[path] = properties
                for key, child in value.items():
                    sites.update(citation_sites(child, (*path, key)))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    sites.update(citation_sites(child, (*path, index)))
            return sites
        legacy, current = citation_sites(old), citation_sites(new)
        self.assertTrue(legacy)
        self.assertEqual(current.keys(), legacy.keys())
        for path, properties in current.items():
            with self.subTest(schemaPath=path):
                self.assertNotIn('lineCount', legacy[path])
                self.assertIn('endLine', legacy[path])
                self.assertNotIn('endLine', properties)
                self.assertEqual(properties['lineCount'], {'type': 'integer', 'minimum': 1, 'maximum': 40})
        compact.validate_shape(self.body, new)
        for index in range(6):
            for value in (0, -1, 41, True, 1.0, '1', None):
                body = copy.deepcopy(self.body)
                list(references(body))[index]['lineCount'] = value
                with self.subTest(site=index, value=value), self.assertRaises(Refused):
                    compact.validate_shape(body, new)
                with self.subTest(site=index, value=value), self.assertRaises(Refused):
                    self.parse(body)

    def test_each_citation_site_checks_actual_eof_without_clamping(self):
        for index in range(6):
            for start, count in ((110, 22), (130, 1), (32, 45), (424, 41)):
                body = copy.deepcopy(self.body)
                list(references(body))[index].update(startLine=start, lineCount=count)
                before = copy.deepcopy(body)
                with self.subTest(site=index, start=start, count=count), self.assertRaises(Refused):
                    self.parse(body)
                self.assertEqual(body, before)

    def test_exact_40_line_and_eof_boundaries_preserve_quotes(self):
        for ref in references(self.body):
            ref.update(startLine=90, lineCount=40)
        result = self.parse()
        source = {row['path']: row for row in self.packet['sources']}
        for ref in references(result):
            self.assertEqual(ref['endLine'], 129)
            self.assertEqual(ref['quote'], ''.join(source[ref['path']]['text'].splitlines(keepends=True)[89:129]))
        self.assertEqual(self.roundtrip(result, self.packet), result)

    def test_mixed_unknown_and_downgraded_protocols_are_rejected(self):
        for index in range(6):
            for include_count in (False, True):
                body = copy.deepcopy(self.body)
                ref = list(references(body))[index]
                ref['endLine'] = 1
                if not include_count:
                    del ref['lineCount']
                with self.subTest(site=index, mixed=include_count), self.assertRaises(Refused):
                    self.parse(body)
        with self.assertRaises(Refused): self.parse(self.legacy_body)
        with self.assertRaises(Refused): self.parse(packet=self.legacy_packet)
        for protocol in ('unknown', '', None, 1, True):
            packet = copy.deepcopy(self.packet); packet['citationProtocol'] = protocol
            with self.subTest(protocol=protocol), self.assertRaises(Refused): self.parse(packet=packet)
        packet = copy.deepcopy(self.packet)
        del packet['workspaceContext']['refineExecutionPlan']
        with self.assertRaises(Refused): self.parse(packet=packet)

    def test_packet_line_counts_must_equal_text_not_authorize_overshoot(self):
        for value in (128, 130, True, '129', None):
            packet = copy.deepcopy(self.packet)
            packet['sources'][0]['lineCount'] = value
            with self.subTest(value=value), self.assertRaises(Refused): self.parse(packet=packet)
        packet = copy.deepcopy(self.packet)
        del packet['sources'][0]['lineCount']
        with self.assertRaises(Refused): self.parse(packet=packet)
        # Inventing a larger metadata bound cannot permit a real EOF overshoot.
        packet = copy.deepcopy(self.packet)
        for source in packet['sources']: source['lineCount'] = 200
        list(references(self.body))[0].update(startLine=110, lineCount=22)
        with self.assertRaises(Refused): self.parse(packet=packet)

    def test_hash_quote_and_range_forgery_cannot_roundtrip_any_site(self):
        result = self.parse()
        for index in range(6):
            for field, value in (('quote', 'forged'), ('sha256', '0' * 64), ('endLine', 2)):
                forged = copy.deepcopy(result)
                list(references(forged))[index][field] = value
                with self.subTest(site=index, field=field), self.assertRaises(Refused):
                    self.roundtrip(forged, self.packet)

    def test_blank_and_large_quotes_still_refused(self):
        for text in (' \n' * 129, 'x' * 16385 + '\n' * 129):
            packet = copy.deepcopy(self.packet)
            source = next(row for row in packet['sources'] if row['path'] == 'candidate/test.js')
            source.update(text=text, sha256=api.digest(text.encode()), lineCount=len(text.splitlines(keepends=True)))
            with self.subTest(size=len(text)), self.assertRaises(Refused): self.parse(packet=packet)


if __name__ == '__main__':
    unittest.main()
