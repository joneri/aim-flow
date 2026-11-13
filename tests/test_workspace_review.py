"""Strict synthetic archive-policy parsing; no provider or project execution."""
import copy
import io
import tarfile
import gzip
import zipfile
from unittest.mock import patch
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_workspace_review as policy
from aim_ci.owned_paths import Refused


class WorkspacePolicyTests(unittest.TestCase):
    def setUp(self):
        sources = {'candidate/scripts/generate.js': 'writeFileSync("bundle.zip", makeZip());\n',
            'candidate/package.json': '{"scripts":{"pretest":"node scripts/generate.js","test":"node test.js"}}\n',
            'candidate/README.md': 'Generated bundle.zip\n'}
        self.packet = {'schemaVersion': 1,
            'requirement': {'original': 'Fix feature', 'criteria': [{'id': 'r', 'text': 'Fix feature'}]},
            'approvedPlan': 'Assess tests', 'baseline': {'commit':'a'*40,'tree':'b'*40},
            'candidate': {'commit':'c'*40,'tree':'d'*40}, 'changes': [{'path':'feature.js'}],
            'sources': [{'path':p, 'sha256':api.digest(t.encode()), 'text':t} for p,t in sources.items()],
            'workspaceContext': {'protectedPaths':['scripts/generate.js','package.json','README.md'],
                'stages':[{'root':'.','operation':'run','script':'test'}],
                'archiveCandidates':[{'path':'bundle.zip','sha256':'e'*64,'gitObject':'f'*40,'mode':'100644'}]}}
        self.body = {'summary':'Source only', 'findings':[],
            'coverage':[{'requirementId':'r','status':'unverified','analysis':'Runtime needed','sources':[]}],
            'unknowns':[], 'workspacePolicy': {'generatedOutputs':[{'path':'bundle.zip','reason':'pretest calls generator before test consumer',
                'generatorSources':[self.ref('scripts/generate.js')], 'lifecycleSources':[self.ref('package.json')]}],
                'retainedArchiveInputs':[], 'unknowns':[]}}

    def ref(self, name): return {'path':'candidate/'+name,'startLine':1,'endLine':1}
    def parse(self):
        return policy.parse_response({'output':[{'type':'message','role':'assistant','content':[
            {'type':'output_text','text':json.dumps(self.body)}]}]}, self.packet)

    def test_recognized_archives_validate_bytes_without_extraction(self):
        for name, raw in [('fake.tgz', b'\x1f\x8b\x08not an archive'),
                          ('fake.tar', b'X'*257+b'ustar'+bytes(762)), ('fake.zip', b'PKnotzip')]:
            self.assertFalse(policy.recognized_archive(name, raw))
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            info=tarfile.TarInfo('fixture.txt');info.size=3
            archive.addfile(info,io.BytesIO(b'abc'))
        self.assertTrue(policy.recognized_archive('valid.tar',stream.getvalue()))
        self.assertTrue(policy.recognized_archive('valid.tgz',gzip.compress(stream.getvalue())))
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:archive.writestr('fixture.txt','abc')
        raw=stream.getvalue()
        self.assertTrue(policy.recognized_archive('valid.zip',raw))
        self.assertFalse(policy.recognized_archive('bad.zip',raw.replace(b'abc',b'xyz')))

    def test_generated_unchanged_archive_exact_evidence_roundtrip(self):
        result = self.parse(); output = result['workspacePolicy']['generatedOutputs'][0]
        self.assertEqual(output['sourceIdentity']['sha256'], 'e'*64)
        self.assertIn('writeFileSync', output['generatorSources'][0]['quote'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); p=root/'packet.json'; r=root/'review.json'
            p.write_bytes(api.encoded(self.packet)); r.write_bytes(api.encoded(result))
            pin=lambda x:{'path':str(x),'sha256':api.digest(x.read_bytes())}
            self.assertEqual(policy.verify_evidence(pin(r),pin(p)), result)
            result['workspacePolicy']['generatedOutputs'][0]['generatorSources'][0]['quote']='forged'
            r.write_bytes(api.encoded(result))
            with self.assertRaises(Refused): policy.verify_evidence(pin(r),pin(p))

    def test_definition_changed_or_invented_exclusions_rejected(self):
        for name in ('package.json','feature.js','missing.zip','../bundle.zip'):
            self.body['workspacePolicy']['generatedOutputs'][0]['path']=name
            with self.subTest(name=name), self.assertRaises(Refused): self.parse()

    def test_labels_and_wrong_lifecycle_are_not_generator_evidence(self):
        output=self.body['workspacePolicy']['generatedOutputs'][0]
        output['generatorSources']=[self.ref('README.md')]
        with self.assertRaises(Refused): self.parse()
        output['generatorSources']=[self.ref('scripts/generate.js')]
        output['lifecycleSources']=[self.ref('scripts/generate.js')]
        with self.assertRaises(Refused): self.parse()

    def test_unclassified_archive_blocks_and_retained_input_is_allowed(self):
        self.body['workspacePolicy']['generatedOutputs']=[]
        self.assertIn('Unclassified committed archive',self.parse()['workspacePolicy']['unknowns'][0])
        self.body['workspacePolicy']['retainedArchiveInputs']=[{'path':'bundle.zip','reason':'Fixture input, preserve exact bytes'}]
        self.assertEqual(self.parse()['workspacePolicy']['unknowns'],[])

    def test_ambiguous_duplicate_and_wrong_lines_are_rejected(self):
        output=self.body['workspacePolicy']['generatedOutputs'][0]
        self.body['workspacePolicy']['retainedArchiveInputs']=[{'path':'bundle.zip','reason':'both'}]
        with self.assertRaises(Refused):self.parse()
        self.body['workspacePolicy']['retainedArchiveInputs']=[]
        output['generatorSources'][0]['endLine']=20
        with self.assertRaises(Refused):self.parse()


class ArtifactSelectionTests(WorkspacePolicyTests):
    def setUp(self):
        super().setUp()
        text = 'await page.screenshot({path: "evidence/desktop.png"});\n'
        self.packet['sources'].append({'path': 'candidate/test.js', 'text': text,
                                      'sha256': api.digest(text.encode())})
        self.packet['workspaceContext'].update(captureArtifacts=True,
            committedPaths=['scripts/generate.js', 'package.json', 'README.md', 'test.js', 'bundle.zip'],
            artifactCaptureLimits=policy.CAPTURE_LIMITS)
        self.packet['workspaceContext']['protectedPaths'].append('test.js')
        self.body['artifactSelection'] = [{'path': 'evidence/desktop.png', 'mediaType': 'image/png',
            'reason': 'Browser test writes the desktop state for later independent review',
            'sources': [self.ref('test.js')]}]

    def test_selection_caps_and_enriched_roundtrip(self):
        result = self.parse()
        self.assertEqual(result['artifactSelection'][0]['sources'][0]['sha256'],
                         self.packet['sources'][-1]['sha256'])
        capture = policy.artifact_capture(result['artifactSelection'])
        self.assertEqual(capture, {'schemaVersion': 1,
            'files': [{'path': 'evidence/desktop.png', 'mediaType': 'image/png'}], **policy.CAPTURE_LIMITS})
        with tempfile.TemporaryDirectory() as folder:
            pins = []
            for name, value in [('review', result), ('packet', self.packet)]:
                path = Path(folder).resolve() / (name + '.json'); path.write_bytes(api.encoded(value))
                pins.append({'path': str(path), 'sha256': api.digest(path.read_bytes())})
            self.assertEqual(policy.verify_evidence(*pins), result)

    def test_empty_selection_is_explicit_blocker(self):
        self.body['artifactSelection'] = []
        self.assertIn('no credible', self.parse()['blockers'][-1])
        with self.assertRaises(Refused): policy.artifact_capture([])

    def test_missing_selection_and_extra_legacy_field_rejected(self):
        del self.body['artifactSelection']
        with self.assertRaises(Refused): self.parse()
        self.body['artifactSelection'] = []
        del self.packet['workspaceContext']['captureArtifacts']
        with self.assertRaises(Refused): self.parse()

    def test_bad_paths_and_types_rejected(self):
        output = self.body['artifactSelection'][0]
        for path in ('package.json', '../x.png', '/tmp/x.png', 'node_modules/x.png', '.git/x.png',
                     'npm-cache/x.png', 'secrets/x.png', 'credentials.json', 'a/*.png',
                     'test.js/x.png', '.ssh/x.png', '.env/x.png', '.env.production/x.json',
                     '.npm/x.png', '.cache/x.png', '.aws/x.json', '.config/x.json',
                     '.hg/x.json', '.svn/x.json', '.gcloud/x.json', '.pki/x.json',
                     '.yarn/x.json', '.pnpm-store/x.json', '.venv/x.json', 'venv/x.json',
                     'secrets.production.json', 'credentials-prod.json'):
            output['path'] = path
            with self.subTest(path=path), self.assertRaises(Refused): self.parse()
        output['path'] = 'evidence/desktop.png'; output['mediaType'] = 'application/json'
        with self.assertRaises(Refused): self.parse()

    def test_template_and_joined_paths_with_hidden_artifact_directory(self):
        text = ("const evidenceDir = process.env.EVIDENCE_DIR || '.aim/analysis/latest';\n"
                "writeFileSync(`${evidenceDir}/browser-results.json`, JSON.stringify(results));\n"
                "const outputDir = 'output/playwright/layout';\n"
                "await page.screenshot({path: path.resolve(path.join(outputDir, 'mobile.png'))});\n")
        self.packet['sources'][-1].update(text=text, sha256=api.digest(text.encode()))
        self.body['artifactSelection'] = [
            {'path': '.aim/analysis/latest/browser-results.json', 'mediaType': 'application/json',
             'reason': 'Source default output directory and template write; runtime must verify the exact output',
             'sources': [{'path': 'candidate/test.js', 'startLine': 1, 'endLine': 2}]},
            {'path': 'output/playwright/layout/mobile.png', 'mediaType': 'image/png',
             'reason': 'Joined screenshot output path from displayed constant',
             'sources': [{'path': 'candidate/test.js', 'startLine': 3, 'endLine': 4}]}]
        result = self.parse()
        self.assertEqual([x['path'] for x in result['artifactSelection']],
                         [x['path'] for x in self.body['artifactSelection']])
        self.assertIn('`${evidenceDir}/browser-results.json`', result['artifactSelection'][0]['sources'][0]['quote'])
        self.assertIn('path.join', result['artifactSelection'][1]['sources'][0]['quote'])
        self.assertEqual(policy.artifact_capture(result['artifactSelection'])['maxFiles'], 8)

    def test_refs_duplicates_and_count_enforced(self):
        output = self.body['artifactSelection'][0]
        output['sources'] = [self.ref('README.md')]
        with self.assertRaises(Refused): self.parse()
        output['sources'] = [self.ref('test.js')]; output['sources'][0]['endLine'] = 100
        with self.assertRaises(Refused): self.parse()
        output['sources'] = [self.ref('test.js')]
        self.body['artifactSelection'] = [output, copy.deepcopy(output)]
        with self.assertRaises(Refused): self.parse()
        self.body['artifactSelection'] *= 5
        with self.assertRaises(Refused): self.parse()

    def test_capture_flag_is_exact_opt_in(self):
        for value in (False, 1, 'true', None):
            with self.subTest(value=value), self.assertRaises(Refused):
                policy.capture_requested({'captureArtifacts': value})
        self.assertFalse(policy.capture_requested({}))
        self.assertTrue(policy.capture_requested({'captureArtifacts': True}))


class CompilerTests(unittest.TestCase):
    def test_original_markdown_sections_remain_distinct_exact_criteria(self):
        import flow_workflow_validation as phase
        original = 'Story\n\n## Acceptance criteria\n\n### AC1: Pause\n- Stop timer.\n\n### AC2: Resume\n- Restart timer.\n\n## Definition of done\n- Test browser.\n'
        value = phase.requirement_document(original)
        self.assertEqual(value['original'], original)
        self.assertEqual(''.join(x['text'] for x in value['criteria']), original)
        self.assertEqual(len(value['criteria']), 5)
        self.assertIn('### AC1', value['criteria'][2]['text'])
        self.assertIn('### AC2', value['criteria'][3]['text'])
        with self.assertRaises(Refused): phase.requirement_document('x' * 2001)

    def test_composition_preserves_all_stages_edges_and_evidence(self):
        import flow_workflow_validation as phase
        jobs = [{'id':name,'mode':'local-sequence','runtime':'r','needs':deps,
            'stages':[{'root':'.','operation':op,'script':script}], 'coverage':name,
            'effects':[name+' effects'],'prerequisites':[name+' prerequisite'], 'sources':[{'path':'package.json','startLine':1,'endLine':1}]}
            for name,deps,op,script in [('install',[],'ci',None),('test',['install'],'run','test')]]
        derived={'declaration': {'jobs':jobs,'runtimes':[{'id':'r','requirements':['node >=24'],'sources':jobs[0]['sources']}]},'resourceNeeds':[]}
        declaration, graph=phase.compose(derived)
        self.assertEqual(graph[1]['needs'],['install'])
        self.assertEqual(len(declaration['jobs'][0]['stages']),2)
        self.assertEqual(declaration['jobs'][0]['prerequisites'],['install prerequisite','test prerequisite'])
        derived['resourceNeeds']=[{'kind':'assessment-predecessor'}]
        with self.assertRaises(Refused):phase.compose(derived)
        derived['resourceNeeds']=[];jobs[1]['needs']=['external']
        with self.assertRaises(Refused):phase.compose(derived)

    def test_browser_requires_exact_prepared_provenance_and_measured_binary_binding(self):
        import flow_workflow_validation as phase
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'provenance.json'
            image={'id':'sha256:'+'a'*64,'nodeVersion':'24.0.0','npmVersion':'11.0.0'}
            tools={'headless_shell':{'path':'/opt/browser/headless_shell','sha256':'b'*64}}
            runtime={'nodeVersion':'24.0.0','npmVersion':'11.0.0','nodeSha256':'c'*64,'npmCliSha256':'d'*64,'npmTreeSha256':'e'*64}
            record={'format':'aim-flow-prepared-project-runtime','schemaVersion':1,
                'imageId':'sha256:'+'f'*64,'baseImageId':image['id'],'runtimeTools':tools,
                'baseRuntime':runtime,'preparedRuntime':runtime,'baseLayers':['sha256:'+'1'*64],
                'imageLayers':['sha256:'+'1'*64,'sha256:'+'2'*64],'review':['Prepared only, never launched'],
                'preparation':{'runtimeTools':tools,'browserTreeSha256':'3'*64,'playwrightVersion':'1.58.2'}}
            path.write_bytes(api.encoded(record))
            prepared={'id':record['imageId'],'baseImageId':image['id'],'runtimeTools':tools,
                'provenance':{'path':str(path),'sha256':api.digest(path.read_bytes())}}
            spec={'backend':'container','path':'/template','sha256':'4'*64}
            row={'kind':'browser-runtime','identity':prepared['id'],'executionSpec':spec,
                'capabilities':{'browserPackages':{'playwright':'1.58.2'},'utilities':{'headless_shell':'b'*64}}}
            template={'image':image,'preparedImage':prepared}
            self.assertTrue(phase.browser_resource(row,spec,template,['playwright >=1.58']))
            row['capabilities']['utilities']['headless_shell']='5'*64
            self.assertFalse(phase.browser_resource(row,spec,template,['playwright >=1.58']))
            row['capabilities']['utilities']['headless_shell']='b'*64
            row['capabilities']['browserPackages']['playwright']='1.59.0'
            self.assertFalse(phase.browser_resource(row,spec,template,['playwright >=1.58']))

    def test_wrong_cache_and_separate_browser_spec_do_not_satisfy_workspace(self):
        import flow_workflow_validation as phase
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'spec.json'
            value={'image':{'nodeVersion':'24.0.0','npmVersion':'11.0.0','architecture':'arm64','os':'linux'},'npmCache':{'sha256':'a'*64}}
            path.write_bytes(api.encoded(value))
            spec={'backend':'container','path':str(path),'sha256':api.digest(path.read_bytes())}
            node={'id':'node','kind':'node-runtime','executionSpec':spec,'capabilities':{'nodeVersion':'24.0.0','npmVersion':'11.0.0','architecture':'arm64','platform':'linux'}}
            cache={'id':'cache','kind':'npm-cache','identity':'a'*64,'executionSpec':spec,'capabilities':{'lockSha256':'b'*64}}
            catalog={'resources':[node,cache]}
            derived={'declaration':{'jobs':[{'id':'test'}]}, 'resourceNeeds':[
                {'step':'test','kind':'node-runtime','requirements':['node >=24','npm named scripts with normal lifecycle hooks']},
                {'step':'test','kind':'npm-cache','requirements':['lock-sha256:'+'b'*64]}]}
            self.assertEqual(phase.select_resource(derived,catalog)[0]['id'],'node')
            cache['identity']='c'*64
            with self.assertRaises(Refused):phase.select_resource(derived,catalog)
            cache['identity']='a'*64
            derived['resourceNeeds'].append({'step':None,'kind':'browser-runtime','requirements':['playwright >=1.58']})
            browser={'id':'browser','kind':'browser-runtime','executionSpec':{**spec,'sha256':'d'*64},'capabilities':{'browserPackages':{'playwright':'1.58.2'}}}
            catalog['resources'].append(browser)
            with self.assertRaises(Refused):phase.select_resource(derived,catalog)
            browser['executionSpec']=spec
            with self.assertRaises(Refused):phase.select_resource(derived,catalog)
            # A matching catalog label alone never qualifies a plain Node image.

if __name__=='__main__': unittest.main()

class CaptureResourceBudgetTests(unittest.TestCase):
    def test_total_capture_scales_with_granted_report_headroom(self):
        limits = policy.capture_limits(32_000_000)
        self.assertGreater(limits['maxTotalBytes'], 9_151_324)
        self.assertLessEqual(4 * ((limits['maxTotalBytes'] + 2) // 3) + 16384, 16_000_000)
        self.assertLess(policy.capture_limits(8_000_000)['maxTotalBytes'], limits['maxTotalBytes'])
        with self.assertRaises(Refused): policy.capture_limits(4096)
