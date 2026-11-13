"""Offline initial-environment/attempt facts; never a live project or provider."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
import flow_review_execution_context as context
import flow_workflow_validation as phase
import flow_workspace_review as workspace
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused
import test_workflow_validation as fixtures


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.spec = {'format': container.SPEC, 'schemaVersion': 1, 'job': 'unused', 'sourcePaths': [],
            'docker': {}, 'image': {'id': 'sha256:'+'1'*64, 'repoDigest': 'node@sha256:'+'2'*64,
                'os': 'linux', 'architecture': 'arm64', 'nodeVersion': '24.0.0', 'npmVersion': '11.0.0'},
            'npmCache': {}, 'runtimeReview': ['Unqualified runtime candidate'], 'sourceReview': ['Review needed'],
            'applicationEnvironment': {}, 'timeoutSeconds': 60, 'outputBytes': 100000,
            'memoryBytes': 256*1024**2, 'processCount': 64, 'workspaceBytes': 64*1024**2}
        self.target = {'checkout': '/private/never-disclose', 'commit': 'a'*40, 'tree': 'b'*40}
        self.resource = {'id': 'private-resource-name', 'qualificationState': 'qualified',
            'executionSpec': {'backend': 'container', 'path': '/private/template.json', 'sha256': 'c'*64}}
        self.stages = [{'root': '.', 'operation': 'ci', 'script': None}]
        self.declaration = {'jobs': [{'id': 'install', 'stages': self.stages}]}

    def build(self):
        return context.build(self.target, {'path': '/private/catalog.json', 'sha256': 'd'*64},
            self.resource, self.spec, self.declaration, 'e'*64)

    def packet(self):
        return {'candidate': {'commit': 'a'*40, 'tree': 'b'*40}, 'workspaceContext': {'stages': self.stages}}

    def test_public_context_binds_source_template_stages_but_never_qualifies(self):
        value = self.build()
        self.assertEqual(context.validate(value, self.packet()), value)
        wire = json.dumps(value)
        self.assertNotIn('/private', wire)
        self.assertNotIn('private-resource-name', wire)
        self.assertEqual(value['bindings']['executionSpecSha256'], 'c'*64)
        self.assertEqual(value['localAdapterSteps'][0]['stages'], self.stages)
        self.assertIn('pending', value['contract']['qualification'])
        self.assertIn('not measured', value['contract']['qualification'])
        self.assertIn('no execution', value['contract']['authority'])
        self.assertIn('every stage boundary', value['contract']['sourceCopies'])
        self.assertIn('not source identity', value['contract']['sourceMetadata'])

    def test_sensitive_and_unclassified_values_are_present_but_withheld(self):
        self.spec['applicationEnvironment'] = {'API_TOKEN': 'sk-proj-neverdisclosethis',
            'EVIDENCE_DIR': '/Users/alice/private-output', 'MODE': 'opaque-sensitive-string',
            'PUBLIC_URL': 'https://user:password@example.invalid', 'PRIVATE_PATH': '/opt/keys',
            'BROWSER_PATH': '/opt/browsers', 'DISABLE_TELEMETRY': '1', 'ZIP_OPTIONS': '-b /work/tmp'}
        value = self.build(); env = value['initialEnvironment']
        self.assertEqual(env['values']['BROWSER_PATH'], '/opt/browsers')
        self.assertEqual(env['values']['ZIP_OPTIONS'], '-b /work/tmp')
        self.assertEqual(env['withheldNames'], ['API_TOKEN', 'EVIDENCE_DIR', 'MODE', 'PRIVATE_PATH', 'PUBLIC_URL'])
        for secret in ('sk-proj-neverdisclosethis', '/Users/alice', 'opaque-sensitive-string', 'user:password'):
            self.assertNotIn(secret, json.dumps(value))
        self.assertFalse(env['inheritsHostEnvironment'])
        self.assertNotIn('OUTPUT_DIR', env['values'])
        self.assertNotIn('OUTPUT_DIR', env['withheldNames'])
        self.assertEqual(context.validate(value, self.packet()), value)

    def test_runtime_security_overrides_are_rejected_before_disclosure(self):
        for name in ('PATH', 'NODE_OPTIONS', 'NPM_CONFIG_USERCONFIG', 'GIT_CONFIG_GLOBAL', 'HOME', 'FLOW_SCOPE'):
            self.spec['applicationEnvironment'] = {name: '1'}
            with self.subTest(name=name), self.assertRaises(Refused): self.build()

    def test_exact_initial_environment_matches_existing_runner_not_host(self):
        node = shutil.which('node')
        if not node: self.skipTest('Node needed for fixed-helper environment parity, no project execution')
        helper = Path(__file__).resolve().parents[1] / 'lib/aim_ci/project_container_runner.cjs'
        for version in (1, 2):
            application = {'OUTPUT_DIR': '/work/evidence', 'NO_TELEMETRY': '1'}
            result = subprocess.run([node, '-e', 'const h=require(process.argv[1]);process.stdout.write(JSON.stringify(h.environment("/work",JSON.parse(process.argv[2]),Number(process.argv[3]))));',
                str(helper), json.dumps(application), str(version)], capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(json.loads(result.stdout), context.environment(application, version)['values'])

    def test_changed_target_stages_pins_or_contract_refuse(self):
        base = self.build()
        for mutate in (
                lambda v: v['candidate'].update(commit='f'*40),
                lambda v: v['bindings'].update(executionSpecSha256='bad'),
                lambda v: v['contract'].update(qualification='passed'),
                lambda v: v['localAdapterSteps'][0]['stages'].append({'root': '.', 'operation': 'run', 'script': 'publish'}),
                lambda v: v['initialEnvironment'].update(inheritsHostEnvironment=True),
                lambda v: v['initialEnvironment']['values'].update(API_TOKEN='1'),
                lambda v: v['limits'].update(timeoutSeconds=99999)):
            value = copy.deepcopy(base); mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(Refused): context.validate(value, self.packet())

    def test_unsupported_adapter_and_limits_refuse(self):
        for key, value in [('schemaVersion', 3), ('timeoutSeconds', 0), ('processCount', 9999)]:
            old = self.spec[key]; self.spec[key] = value
            with self.subTest(key=key), self.assertRaises(Refused): self.build()
            self.spec[key] = old
        self.resource['executionSpec']['backend'] = 'local'
        with self.assertRaises(Refused): self.build()

    def test_context_instructions_match_enforcement_and_direct_dependencies(self):
        text = workspace.review_instructions(True, True, self.build())
        self.assertNotIn('other source mutation needs are unsupported', text)
        self.assertNotIn('establishes a terminating local command', text)
        self.assertNotIn('executionDependsOn as all original predecessors', text)
        self.assertIn('DIRECT', text)
        self.assertIn('not a mathematical termination proof', text)
        self.assertIn('Known source defects', text)
        self.assertIn('withheldNames are present', text)
        self.assertIn('acceptance', text)

    def test_legacy_instruction_assembly_remains_unchanged_without_context(self):
        self.assertEqual(workspace.review_instructions(False, False, None), workspace.INSTRUCTIONS)
        self.assertEqual(workspace.review_instructions(True, False, None),
            workspace.INSTRUCTIONS + workspace.refinement.INSTRUCTIONS)

    def test_bounded_protocol_instruction_is_explicit_not_a_legacy_rewrite(self):
        text = workspace.review_instructions(True, True, self.build(), citation_protocol='start-line-count-v1')
        self.assertIn('Do not return endLine, hashes or quotes.', text)
        self.assertIn('source.lineCount', text)
        with self.assertRaises(Refused): workspace.review_instructions(True, True, self.build(), citation_protocol='unknown')


class ContextContinuationTests(fixtures.ContinuationTests):
    def setUp(self):
        super().setUp()
        self.pc['refineExecutionPlan'] = True
        self.save_phase_config()
        self.review_body['executionRefinement'] = [
            {'stepId': name, 'kind': 'finite-local', 'evidenceSteps': [], 'executionDependsOn': deps,
             'analysis': 'Finite source-supported operation; runtime evidence remains pending',
             'sources': [{'path': 'candidate/web/package.json', 'startLine': 1, 'endLine': 1}]}
            for name, deps in [('install', []), ('test', ['install'])]]

    def test_preview_and_request_bind_context_without_inventing_qualification(self):
        self.grant()
        preview = self.phase()
        context_value = preview['intent']['reviewExecutionContext']
        self.assertEqual(context_value['bindings']['executionSpecSha256'], self.pin(self.spec_path)['sha256'])
        self.assertEqual(len(self.wcalls), 4)
        with patch.object(container, 'execute', self.execute_fake):
            result = self.grant_phase()
        self.assertEqual(result['status'], 'local-validation-passed')
        packet = json.loads((self.ps / 'review-attempt/packet.json').read_bytes())
        self.assertEqual(packet['workspaceContext']['executionContext'], context_value)
        self.assertEqual(len(self.container_calls), 1)

    def test_template_mutation_is_refused_before_review(self):
        self.grant(); self.phase()
        self.spec['applicationEnvironment']['NEW_OVERRIDE'] = '/work/new-output'
        self.spec_path.write_bytes(api.encoded(self.spec))
        with self.assertRaises(Refused): self.phase()
        self.assertEqual(len(self.wcalls), 4)
        self.assertEqual(self.container_calls, [])

    def test_unsupported_resource_stops_before_review_even_with_refinement(self):
        self.catalog['resources'][0]['capabilities']['nodeVersion'] = '18.0.0'
        Path(self.wc['resourceCatalog']['path']).write_bytes(api.encoded(self.catalog))
        self.wc['resourceCatalog'] = self.pin(Path(self.wc['resourceCatalog']['path']))
        self.wpath.write_bytes(api.encoded(self.wc)); self.wsha = self.pin(self.wpath)['sha256']
        self.pc['workflowConfig'] = self.pin(self.wpath); self.save_phase_config()
        self.grant()
        with self.assertRaisesRegex(Refused, 'compatible container'): self.phase()
        self.assertEqual(len(self.wcalls), 4)
        self.assertEqual(self.container_calls, [])


for name in dir(fixtures.ContinuationTests):
    if name.startswith('test_') and name not in ContextContinuationTests.__dict__:
        setattr(ContextContinuationTests, name, None)

if __name__ == '__main__': unittest.main()
