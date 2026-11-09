"""Installed inspection and planning against disposable Git repositories."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import docker_engine
from aim_ci.project_discovery import CommitReader, repository_footprint, version_relation
from aim_ci.recipe_plan import (
    _advance_docker_recipe, check_recipe_execution, plan_recipe, read_container_profile,
)
from openai_budget import LIMIT, Refused, fingerprint


TEST_SCRIPT = 'test:browser.smoke'
ORIGIN = 'https://fixture.invalid/aim-ci/static-site.git'
IMAGE = 'sha256:' + 'a' * 64
STORE = 'b' * 64


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    return sha(path)


def write_recipe(workspace, script=TEST_SCRIPT, *, version=2):
    recipe = workspace / 'recipe'
    recipe.mkdir(parents=True)
    selection = {'testScript': script} if version == 2 else {'operations': {
        'dependencies': ['ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'],
        'test': ['--ignore-scripts', 'run', 'test'], 'build': ['--ignore-scripts', 'run', 'build'],
    }}
    recipe_pin = write_json(recipe / 'recipe.json', {
        'version': version, 'stack': 'npm-vite-static', **selection,
        'allowedPaths': ['src/main.js'], 'forbiddenPaths': ['tests'],
        'artifact': {'directory': 'dist', 'requiredFiles': ['index.html'], 'maximumBytes': 10000},
        'limits': {'network': 'disabled', 'timeoutSeconds': 20, 'memoryMiB': 256, 'maxProcesses': 4}})
    oracle_pin = write_json(recipe / 'oracle.json', {'kind': 'static-heading-v1', 'file': 'index.html',
                                                     'heading': 'Synthetic test fixture'})
    manifest_pin = write_json(recipe / 'recipe-manifest.json', {
        'format': 'aim-ci-recipe', 'id': 'fixture', 'version': '1.0.0',
        'files': {'recipe.json': recipe_pin, 'oracle.json': oracle_pin}})
    return recipe, manifest_pin


class ProjectMetadataTests(unittest.TestCase):
    def test_container_profile_requires_valid_bytes_and_an_external_pin(self):
        tools = {'node': {'path': '/opt/aim-ci/node', 'sha256': 'c' * 64},
                 'npm': {'path': '/opt/aim-ci/npm-cli.js', 'sha256': 'd' * 64}}
        profile = {'version': 1, 'imageDigest': IMAGE, 'tools': tools, 'dependencyStoreSha256': STORE}
        with (tempfile.TemporaryDirectory() as folder,
              patch('socket.socket', side_effect=AssertionError('No socket expected')),
              patch('subprocess.Popen', side_effect=AssertionError('No subprocess expected'))):
            path = Path(folder).resolve() / 'profile.json'
            pin = write_json(path, profile)
            self.assertEqual(read_container_profile(path, pin), profile)
            valid = path.read_bytes()
            path.write_bytes(valid + b'\n')
            with self.assertRaisesRegex(Refused, 'Container profile SHA-256 mismatch'):
                read_container_profile(path, pin)

            for changes, reason in (
                ({'version': 2}, 'Unsupported container profile version'),
                ({'qualification': 'verified'}, 'Invalid container profile fields'),
                ({'tools': tools | {'npm': tools['npm'] | {'path': '/opt/aim-ci/../npm-cli.js'}}},
                 'Invalid declared container npm runtime'),
            ):
                with self.subTest(reason=reason):
                    changed_pin = write_json(path, profile | changes)
                    with self.assertRaisesRegex(Refused, reason):
                        read_container_profile(path, changed_pin)

            path.write_bytes(b'{"version":1,' + valid[1:])
            with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
                read_container_profile(path, sha(path))

    def test_unsuffixed_supported_git_version(self):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder).resolve()
            git = Path(sys.executable).resolve()
            commit = 'a' * 40
            with patch.object(CommitReader, 'read', side_effect=[
                    b'git version 2.54.0\n', str(checkout).encode() + b'\n',
                    commit.encode() + b'\n', b'bbbb\n']):
                reader = CommitReader(ROOT, checkout, git, sha(git), commit)
            self.assertEqual(reader.tree, 'bbbb')

    def test_supported_exact_and_caret_subset(self):
        for declared, locked, relation in (
            ('7.3.3', '7.3.3', 'matches'),
            ('7.3.3', '7.3.4', 'mismatch'),
            ('^7.0.0', '7.3.3', 'matches'),
            ('^7.0.0', '8.0.0', 'mismatch'),
            ('^0.2.1', '0.2.9', 'matches'),
            ('^0.2.1', '0.3.0', 'mismatch'),
            ('^0.0.2', '0.0.3', 'mismatch'),
            ('~7.3.0', '7.3.3', 'unsupported-syntax'),
            ('^7.0.0', '7.3.3-rc.1', 'unsupported-syntax'),
        ):
            with self.subTest(declared=declared, locked=locked):
                self.assertEqual(version_relation(declared, locked), relation)

    def test_repository_footprint_reads_complete_metadata_without_project_size_ceiling(self):
        reader = Mock(commit='a' * 40, tree='b' * 40)
        reader.read.return_value = (
            b'100644 blob ' + b'c' * 40 + b' 123\tpublic/space name.png\0'
            + b'100755 blob ' + b'd' * 40 + b' 7\tbuild.sh\0'
            + b'120000 blob ' + b'e' * 40 + b' 4\tlinked-file\0')
        result = repository_footprint(reader)
        self.assertEqual(result['regularFileBytes'], 130)
        self.assertEqual(result['regularFileCount'], 2)
        self.assertEqual(result['otherEntryCount'], 1)
        self.assertFalse(result['buildInputsSelected'])
        self.assertEqual(result['topLevelBytes'], {'(root files)': 7, 'public': 123})
        self.assertEqual(result['largestFiles'][0]['path'], 'public/space name.png')
        reader.read.assert_called_once_with('ls-tree', '-r', '-l', '-z', '--full-tree', reader.commit)
        reader.blob.assert_not_called()
        for raw in (b'not complete', b'100644 blob ' + b'c' * 40 + b' -1\tfile\0',
                    b'100644 blob ' + b'c' * 40 + b' 5\tbad/../path\0'):
            reader.read.return_value = raw
            unknown = repository_footprint(reader)
            self.assertEqual(unknown['status'], 'unknown')
            self.assertNotIn('regularFileBytes', unknown)
        reader.read.return_value = b'100644 blob ' + b'c' * 40 + ' 5\tpublic/å.png\0'.encode()
        self.assertEqual(repository_footprint(reader)['largestFiles'][0]['path'], 'public/å.png')
        reader.read.return_value = b'100644 blob ' + b'c' * 40 + b' 5\tinvalid-\xff\0'
        self.assertEqual(repository_footprint(reader)['status'], 'unknown')
        reader.read.return_value = b''.join(b'100644 blob ' + b'c' * 40 + b' 5\t' + str(i).encode() + b'\0'
                                          for i in range(4097))
        self.assertEqual(repository_footprint(reader)['regularFileCount'], 4097)
        for error in (Refused('bounded Git read'), subprocess.TimeoutExpired('git ls-tree', 20)):
            reader.read.side_effect = error
            self.assertEqual(repository_footprint(reader)['status'], 'unknown')

    def test_actual_planner_refuses_changed_approval_before_git(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            recipe, recipe_pin = write_recipe(workspace)
            approval = workspace / 'approval.json'
            original_pin = write_json(approval, {'kind': 'synthetic-fixture'})
            approval.write_text('{"kind":"operator-record"}')
            args = SimpleNamespace(recipe_package=recipe, recipe_sha256=recipe_pin,
                                   approval=approval, approval_sha256=original_pin)
            with patch('subprocess.Popen', side_effect=AssertionError('No subprocess expected')):
                with self.assertRaisesRegex(Refused, 'Operator approval SHA-256 mismatch'):
                    plan_recipe(ROOT, args, {'version': 'fixture'})


class ProjectPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        git = shutil.which('git')
        if not git:
            raise RuntimeError('ProjectPlanningTests requires Git 2.54.0+ on PATH')
        cls.git_path = Path(git).resolve()
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        cls.scratch = Path(scratch.name).resolve()
        cls.env = {'PATH': os.defpath, 'HOME': str(cls.scratch), 'LC_ALL': 'C',
                   'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
                   'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_REPLACE_OBJECTS': '1',
                   'GIT_AUTHOR_NAME': 'Planning fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                   'GIT_COMMITTER_NAME': 'Planning fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        version = cls.run_command(cls.git_path, '--version')
        parts = re.search(r'^git version (\d+)\.(\d+)\.(\d+)', version)
        if not parts or tuple(map(int, parts.groups())) < (2, 54, 0):
            raise RuntimeError(f'ProjectPlanningTests requires Git 2.54.0+, got {version.strip()}')
        cls.git_pin = sha(cls.git_path)
        archive = cls.scratch / 'control.tar.gz'
        receipt = json.loads(cls.run_command(sys.executable, '-IB', ROOT / 'aim-ci-control.py',
                                             'build', '--version', '0.10.0-local.1', '--output', archive))
        installation = cls.scratch / 'control'
        installed = json.loads(cls.run_command(sys.executable, '-IB', ROOT / 'aim-ci-control.py',
                                               'install', '--archive', archive, '--sha256', receipt['archiveSha256'],
                                               '--destination', installation))
        cls.manifest_pin = installed['manifestSha256']
        cls.manager = installation / 'aim-ci-control.py'

    @classmethod
    def run_command(cls, *argv, cwd=None, refusal=None, exit_code=0):
        command = list(map(str, argv))
        result = subprocess.run(command, cwd=cwd or cls.scratch, env=cls.env,
                                capture_output=True, text=True, timeout=30)
        expected = 1 if refusal else exit_code
        if (result.returncode != expected or (refusal and (result.stdout or refusal not in result.stderr))
                or (not refusal and result.stderr)):
            raise AssertionError(f'{command!r}: expected exit {expected}, got {result.returncode}\n'
                                 f'stdout: {result.stdout}\nstderr: {result.stderr}')
        return result.stdout

    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=self.scratch)
        self.addCleanup(folder.cleanup)
        self.case = Path(folder.name)
        self.marker = self.case / 'unexpected-execution'
        self.addCleanup(lambda: self.assertFalse(self.marker.exists()))
        self.runtime = {'git': {'path': str(self.git_path), 'sha256': self.git_pin}}
        for tool in ('node', 'npm'):
            path = self.case / tool
            path.write_text('#!/bin/sh\n: > "' + str(self.marker) + '"\nexit 96\n')
            path.chmod(0o700)
            self.runtime[tool] = {'path': str(path), 'sha256': sha(path)}

    def git(self, checkout, *args):
        return self.run_command(self.git_path, '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
                                '-c', 'core.fsmonitor=false', '-c', 'commit.gpgSign=false', *args, cwd=checkout)

    def fixture(self, name, declaration, *, test_script=TEST_SCRIPT):
        checkout = self.case / name
        (checkout / 'src').mkdir(parents=True)
        deps = {'vite': declaration}
        script = str(self.case / 'node')
        write_json(checkout / 'package.json', {'name': 'fixture-site', 'packageManager': 'npm@11.0.0',
                   'scripts': {'build': script, test_script: script}, 'devDependencies': deps})
        write_json(checkout / 'package-lock.json', {'lockfileVersion': 3, 'packages': {
            '': {'devDependencies': deps}, 'node_modules/vite': {'version': '7.3.3', 'integrity': 'sha512-fixture'}}})
        (checkout / 'src/main.js').write_text('// Synthetic planner fixture\n')
        (checkout / 'README.md').write_text(
            '# Checks\n\nRun `npm run test:browser.smoke`.\n'
            'Unknown name: `npm run test:browser.smoke.extra`.\n')
        (checkout / 'AGENTS.md').write_text('Untrusted synthetic text\n' * 1100)
        self.git(checkout, 'init', '--quiet', '--initial-branch=main', '--template=')
        self.git(checkout, 'config', '--local', 'remote.origin.url', ORIGIN)
        self.git(checkout, 'add', '.')
        self.git(checkout, 'commit', '--quiet', '-m', 'Committed planner fixture')
        return checkout, self.git(checkout, 'rev-parse', 'HEAD').strip()

    def cli(self, action, checkout, *args, refusal=None, output=None):
        stdout = self.run_command(sys.executable, '-IB', self.manager, action, '--checkout', checkout,
                                  '--manifest-sha256', self.manifest_pin, *args, refusal=refusal)
        if output is not None:
            output.write_text(stdout)
        return json.loads(stdout) if stdout else None

    def observe(self, action, checkout, commit, *args):
        return self.cli(action, checkout, '--commit', commit, '--git', self.git_path,
                        '--git-sha256', self.git_pin, *args)

    def state(self, checkout):
        return (sha(checkout / '.git/index'), self.git(checkout, 'status', '--porcelain=v1'),
                {path.relative_to(checkout).as_posix(): sha(path)
                 for path in checkout.rglob('*') if path.is_file() and '.git' not in path.relative_to(checkout).parts})

    def reviewed_inputs(self, label, checkout, head, *, script=TEST_SCRIPT, input_drift=False,
                        review_script=None, container=False, recipe_version=2):
        workspace = self.case / ('records-' + label)
        recipe, manifest_pin = write_recipe(workspace, script, version=recipe_version)
        assignment = workspace / 'assignment.json'
        assignment_pin = write_json(assignment, {'version': 1, 'id': 'fixture-task', 'title': 'Fixture title',
                                                'acceptance': 'Synthetic criterion', 'paths': ['src/main.js']})
        inputs = {name: sha(checkout / name) for name in ('package.json', 'package-lock.json')}
        if input_drift:
            inputs['package.json'] = '0' * 64
        target = {'repository': ORIGIN, 'branch': 'main', 'commit': head}
        shared = {'version': 2 if container else 1, 'kind': 'synthetic-fixture',
                  'reviewReference': 'Local fixture data',
                  'controlManifestSha256': self.manifest_pin, 'recipeManifestSha256': manifest_pin,
                  'target': target, 'committedInputs': inputs}
        runtime = self.runtime
        options, profile_binding, review_binding = {}, {}, {}
        if container:
            runtime = {'git': self.runtime['git']}
            tools = {'node': {'path': '/opt/aim-ci/node', 'sha256': 'c' * 64},
                     'npm': {'path': '/opt/aim-ci/npm-cli.js', 'sha256': 'd' * 64}}
            profile = workspace / 'profile.json'
            profile_pin = write_json(profile, {'version': 1, 'imageDigest': IMAGE, 'tools': tools,
                                               'dependencyStoreSha256': STORE})
            profile_binding = {'containerProfileSha256': profile_pin}
            review_binding = {'targetTree': self.git(checkout, 'rev-parse', 'HEAD^{tree}').strip(),
                              'gitReaderSha256': fingerprint(runtime)}
            options.update({'container-profile': profile, 'container-profile-sha256': profile_pin})
        else:
            tools = runtime
        if recipe_version == 2:
            review = workspace / 'review.json'
            review_pin = write_json(review, {**shared, **profile_binding, **review_binding,
                        'decision': 'reviewed-for-planning', 'assignmentSha256': assignment_pin,
                        'testScript': review_script or script, 'runtimeSha256': fingerprint(tools)})
            options.update({'review': review, 'review-sha256': review_pin})
            review_binding = {'reviewSha256': review_pin}
        approval = workspace / 'approval.json'
        approval_pin = write_json(approval, {**shared, **profile_binding, **review_binding, 'runtime': runtime,
                                            'state': {'mode': 'uninitialized', 'path': str(workspace / 'state')}})
        return {'recipe-package': recipe, 'recipe-sha256': manifest_pin, 'approval': approval,
                'approval-sha256': approval_pin, 'assignment': assignment, 'assignment-sha256': assignment_pin,
                **options}

    def plan(self, checkout, inputs, *, refusal=None, output=None):
        args = [item for name, value in inputs.items() for item in ('--' + name, value)]
        state = inputs['approval'].parent / 'state'
        existed = state.exists()
        result = self.cli('plan-recipe', checkout, *args, refusal=refusal, output=output)
        self.assertEqual(state.exists(), existed)
        return result

    def test_discovery_and_adaptation_keep_facts_inferences_and_open_items_separate(self):
        checkout, head = self.fixture('discovery', '^7.0.0')
        (checkout / 'README.md').write_text('Dirty local instructions must stay outside the observation\n')
        before = self.state(checkout)
        docs = ('--project-doc', 'README.md', '--project-doc', 'AGENTS.md')
        discovery = self.observe('discover-project', checkout, head, *docs)
        self.assertEqual(discovery['status'], 'observed-only')
        self.assertFalse(discovery['executionAuthorized'])
        self.assertEqual(discovery['target']['commit'], head)
        references = [fact for fact in discovery['facts'] if fact['code'] == 'document-script-reference']
        self.assertEqual(references, [{'code': 'document-script-reference', 'kind': 'detected',
            'value': TEST_SCRIPT, 'sources': [{'path': 'README.md', 'locator': 'line 3'},
                                              {'path': 'package.json', 'locator': 'scripts.' + TEST_SCRIPT}]}])
        vite = next(fact for fact in discovery['facts'] if fact['code'] == 'vite-indicator')
        self.assertEqual((vite['kind'], vite['value']), ('inferred', '^7.0.0'))
        self.assertEqual(vite['sources'], [{'path': 'package.json', 'locator': 'devDependencies.vite'}])
        unknown = {item['code']: item['sources'] for item in discovery['openItems']}
        self.assertEqual(unknown['document-script-unresolved'], [{'path': 'README.md', 'locator': 'line 4'}])
        self.assertNotIn('definition-size-limit', unknown)
        self.assertIn('node-test-undefined', unknown)
        agents = next(file for file in discovery['files'] if file['path'] == 'AGENTS.md')
        self.assertEqual(agents['status'], 'reference-scan')
        self.assertEqual(agents['bytes'], len(('Untrusted synthetic text\n' * 1100).encode()))
        self.assertEqual(agents['sha256'], sha(checkout / 'AGENTS.md'))
        self.assertNotIn('maximumBytes', agents)

        unselected = self.observe('propose-adaptation', checkout, head, *docs)
        self.assertEqual(unselected['proposal']['status'], 'needs-investigation')
        self.assertEqual(unselected['proposal']['plannedCommands'], [])
        draft = self.observe('propose-adaptation', checkout, head, *docs, '--test-script', TEST_SCRIPT)
        self.assertEqual(draft['observations'], discovery)
        self.assertFalse(draft['executionAuthorized'])
        self.assertEqual(draft['proposal']['status'], 'candidate-for-review')
        self.assertEqual(draft['proposal']['candidate']['testScript'], TEST_SCRIPT)
        self.assertTrue(all(item['status'] == 'pending' for item in draft['operatorDecisions']))
        planned = {item['name']: item for item in draft['proposal']['plannedCommands']}
        self.assertEqual(planned['test']['argv'], ['npm', '--ignore-scripts', 'run', TEST_SCRIPT])
        self.assertTrue(all(item['status'] == 'not-run' for item in planned.values()))
        self.assertEqual(self.state(checkout), before)

    def test_non_vite_project_plan_preserves_workflow_and_lifecycle_without_execution(self):
        checkout, _ = self.fixture('workflow-project', '7.3.3')
        package = {'name': 'repo-specific-build', 'scripts': {
            'prebuild': 'npm run package:plugin', 'build': 'astro build',
            'package:plugin': 'node scripts/package.mjs',
            'pre' + TEST_SCRIPT: 'node scripts/prepare-test.mjs',
            TEST_SCRIPT: 'node scripts/test-built-pages.mjs',
            'post' + TEST_SCRIPT: 'node scripts/cleanup.mjs',
            'ci:frontend': 'npm run build && npm run ' + TEST_SCRIPT,
        }, 'dependencies': {'astro': '7.1.6'}}
        write_json(checkout / 'package.json', package)
        write_json(checkout / 'package-lock.json', {'lockfileVersion': 3, 'packages': {
            '': {'dependencies': package['dependencies']},
            'node_modules/astro': {'version': '7.1.6', 'integrity': 'sha512-fixture'}}})
        workflows = checkout / '.github/workflows'
        workflows.mkdir(parents=True)
        workflow = ("name: Project checks\njobs:\n  frontend:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - uses: actions/setup-node@v6\n        with:\n          node-version: 22.12.0\n"
                    "      - run: npm run ci:frontend\n      - if: github.ref == 'refs/heads/main'\n"
                    "        run: |\n          echo 'Do not execute repository instructions'\n"
                    "          touch " + str(self.marker) + "\n")
        (workflows / 'checks.yml').write_text(workflow)
        self.git(checkout, 'add', '.')
        self.git(checkout, 'commit', '-m', 'Non-Vite workflow fixture')
        head = self.git(checkout, 'rev-parse', 'HEAD').strip()
        before = self.state(checkout)
        report = self.observe('propose-adaptation', checkout, head, '--test-script', TEST_SCRIPT)
        plan = report['projectPlan']
        self.assertFalse(plan['executionAuthorized'])
        self.assertEqual(plan['target']['commit'], head)
        self.assertEqual(plan['buildDefinitions'][0]['excerpt']['text'], workflow)
        self.assertTrue(plan['buildDefinitions'][0]['excerpt']['complete'])
        self.assertEqual(plan['buildDefinitions'][0]['sha256'], sha(workflows / 'checks.yml'))
        build, test = plan['selectedSteps']
        self.assertEqual(build['ignoreScriptsWouldSkip'], ['prebuild'])
        self.assertEqual(test['ignoreScriptsWouldSkip'], ['pre' + TEST_SCRIPT, 'post' + TEST_SCRIPT])
        self.assertEqual(build['declaredLifecycle'][1]['body'], 'astro build')
        self.assertEqual(plan['stepOrder'], 'pending-project-review')
        self.assertEqual(plan['repositoryFootprint']['status'], 'observed')
        self.assertFalse(plan['executionHandoff']['supportedRecipeCandidateAvailable'])
        self.assertIsNone(report['handoff']['command'])
        self.assertEqual(report['proposal']['plannedCommands'], [])
        self.assertIsNone(report['proposal']['candidate'])
        self.assertEqual(self.state(checkout), before)
        missing = self.observe('propose-adaptation', checkout, head, '--test-script', 'test:missing')
        self.assertEqual(missing['projectPlan']['testSelection'], {
            'requested': 'test:missing', 'selected': None, 'status': 'missing-or-unselected'})

    def test_workflow_review_preserves_source_beyond_former_line_and_byte_ceilings(self):
        checkout, _ = self.fixture('long-workflow', '7.3.3')
        workflows = checkout / '.github/workflows'
        workflows.mkdir(parents=True)
        (workflows / 'checks.yml').write_text('name: checks\n' + '# review-only\n' * 301)
        self.git(checkout, 'add', '.')
        self.git(checkout, 'commit', '-m', 'Bounded workflow fixture')
        head = self.git(checkout, 'rev-parse', 'HEAD').strip()
        report = self.observe('propose-adaptation', checkout, head, '--test-script', TEST_SCRIPT)
        excerpt = report['projectPlan']['buildDefinitions'][0]['excerpt']
        self.assertTrue(excerpt['complete'])
        self.assertEqual(excerpt['lastLine'], 302)
        self.assertEqual(excerpt['text'], (workflows / 'checks.yml').read_text())
        self.assertFalse(any(row['code'] in ('definition-excerpt-limit', 'reference-line-limit')
                             for row in report['observations']['openItems']))
        (workflows / 'checks.yml').write_text('name: checks\n# ' + 'é' * 13_000 + '\n')
        self.git(checkout, 'add', '.')
        self.git(checkout, 'commit', '-m', 'Byte-bounded workflow fixture')
        head = self.git(checkout, 'rev-parse', 'HEAD').strip()
        report = self.observe('propose-adaptation', checkout, head, '--test-script', TEST_SCRIPT)
        excerpt = report['projectPlan']['buildDefinitions'][0]['excerpt']
        self.assertTrue(excerpt['complete'])
        self.assertEqual(excerpt['lastLine'], 2)
        self.assertEqual(excerpt['text'], (workflows / 'checks.yml').read_text())
        self.assertGreater(len(excerpt['text'].encode()), 24_000)

    def test_discovery_reads_all_large_committed_definitions_documents_and_inventory(self):
        checkout, _ = self.fixture('large-discovery', '7.3.3')
        package = json.loads((checkout / 'package.json').read_text())
        package['description'] = 'large committed manifest ' * 25_000
        write_json(checkout / 'package.json', package)
        workflows = checkout / '.github/workflows'
        workflows.mkdir(parents=True)
        workflow_names = [f'{index:03d}-' + 'w' * 200 + '.yml' for index in range(170)]
        for name in workflow_names:
            (workflows / name).write_text('run: npm run build\n')
        long_workflow = '# ' + 'é' * 260_000 + '\n' + '# retained line\n' * 301 + 'run: npm run build\n'
        (workflows / workflow_names[-1]).write_text(long_workflow)
        docs = checkout / 'docs' / ('selected-' + 'd' * 190)
        docs.mkdir(parents=True)
        selected = []
        for index in range(9):
            doc = docs / f'check-{index}.md'
            doc.write_text('# retained documentation\n' * 1100 + f'Run `npm run {TEST_SCRIPT}`.\n')
            selected.append(doc.relative_to(checkout).as_posix())
        inventory = checkout / ('inventory-' + 'i' * 150)
        inventory.mkdir()
        for index in range(4100):
            (inventory / f'{index}.txt').write_text('count every committed file\n')
        self.git(checkout, 'add', '.')
        self.git(checkout, 'commit', '-m', 'Complete large discovery fixture')
        head = self.git(checkout, 'rev-parse', 'HEAD').strip()
        tree_size = int(self.git(checkout, 'cat-file', '-s', f'{head}:.github/workflows'))
        self.assertGreater(tree_size, 32_768)
        self.assertGreater((checkout / 'package.json').stat().st_size, 512_000)
        self.assertGreater(len(self.git(checkout, 'ls-tree', '-r', '-l', head).encode()), 512_000)
        before = self.state(checkout)
        (checkout / 'package.json').write_text('dirty local content must not be read')
        report = self.observe('discover-project', checkout, head,
                              *[arg for name in selected for arg in ('--project-doc', name)])
        files = {row['path']: row for row in report['files']}
        self.assertEqual(files['package.json']['status'], 'parsed')
        self.assertEqual(len([name for name in files if name.startswith('.github/workflows/')]), 170)
        references = [fact for fact in report['facts'] if fact['code'] == 'document-script-reference']
        self.assertEqual({row['sources'][0]['path'] for row in references}, set(selected))
        self.assertTrue(all(row['sources'][0]['locator'] == 'line 1101' for row in references))
        workflow_path = '.github/workflows/' + workflow_names[-1]
        excerpt = next(row['value'] for row in report['facts']
                       if row['code'] == 'definition-review-excerpt' and row['sources'][0]['path'] == workflow_path)
        self.assertTrue(excerpt['complete'])
        self.assertEqual(excerpt['text'], long_workflow)
        self.assertTrue(any(row['code'] == 'definition-line-indicator' and row['value'] == 'run'
                            and row['sources'][0] == {'path': workflow_path, 'locator': 'line 303'}
                            for row in report['facts']))
        self.assertEqual(report['repositoryFootprint']['regularFileCount'], len(before[2]))
        self.assertGreater(report['repositoryFootprint']['regularFileCount'], 4096)
        self.assertFalse(any(row['code'].endswith('-limit') for row in report['openItems']))
        self.assertEqual((checkout / 'package.json').read_text(), 'dirty local content must not be read')
        self.assertEqual(sha(checkout / '.git/index'), before[0])

    def test_locked_version_subset_through_discovery_and_actual_planner(self):
        for name, declaration, relation, refusal in (
            ('exact', '7.3.3', 'matches', None),
            ('caret', '^7.0.0', 'matches', None),
            ('mismatch', '^8.0.0', 'mismatch', 'does not match the locked version'),
            ('unsupported', '~7.3.0', 'unsupported-syntax', 'outside the supported exact and caret subset'),
        ):
            with self.subTest(declaration=declaration):
                checkout, head = self.fixture(name, declaration)
                before = self.state(checkout)
                discovery = self.observe('discover-project', checkout, head)
                fact = next(item for item in discovery['facts'] if item['code'] == 'npm-direct-version-relation')
                self.assertEqual((fact['value']['declaration'], fact['value']['lockedVersion'],
                                  fact['value']['relation']), (declaration, '7.3.3', relation))
                self.assertEqual(fact['sources'], [{'path': 'package.json', 'locator': 'devDependencies["vite"]'},
                    {'path': 'package-lock.json', 'locator': 'packages["node_modules/vite"].version'}])
                plan = self.plan(checkout, self.reviewed_inputs(name, checkout, head), refusal=refusal)
                if refusal:
                    proposal = self.observe('propose-adaptation', checkout, head, '--test-script', TEST_SCRIPT)
                    self.assertEqual(proposal['proposal']['status'], 'needs-investigation')
                else:
                    self.assertEqual(plan['status'], 'planned-only')
                    self.assertFalse(plan['executionAuthorized'])
                    self.assertEqual(plan['target']['commit'], head)
                    self.assertEqual(plan['npm']['viteRelation']['relation'], 'matches')
                    self.assertEqual(plan['review']['kind'], 'synthetic-fixture')
                    self.assertEqual(plan['approval']['kind'], 'synthetic-fixture')
                    self.assertEqual(plan['oracleSuitability'], 'synthetic-fixture-only')
                    self.assertEqual(plan['testSelection']['source']['locator'], f'scripts["{TEST_SCRIPT}"]')
                    self.assertEqual(plan['limits']['network'], 'disabled')
                    self.assertTrue(all(item['status'] == 'not-run' for item in plan['operations']))
                self.assertEqual(self.state(checkout), before)

    def test_planner_refuses_stale_approval_source_script_and_runtime(self):
        checkout, head = self.fixture('bindings', '^7.0.0')
        before = self.state(checkout)
        stale = self.reviewed_inputs('stale', checkout, head)
        stale['approval'].write_bytes(stale['approval'].read_bytes() + b'\n')
        self.plan(checkout, stale, refusal='Operator approval SHA-256 mismatch')
        self.plan(checkout, self.reviewed_inputs('source', checkout, head, input_drift=True),
                  refusal='Committed package.json SHA-256 mismatch')
        self.plan(checkout, self.reviewed_inputs('script', checkout, head, script='test:absent'),
                  refusal='Selected testScript is absent')
        self.plan(checkout, self.reviewed_inputs('review', checkout, head, review_script='test:other'),
                  refusal='Recipe review does not bind this selection')
        runtime = self.reviewed_inputs('runtime', checkout, head)
        node = Path(self.runtime['node']['path'])
        original_node = node.read_bytes()
        node.write_text('changed executable bytes\n')
        self.plan(checkout, runtime, refusal='Pinned node runtime SHA-256 mismatch')
        self.assertEqual(self.state(checkout), before)
        node.write_bytes(original_node)
        self.git(checkout, 'commit', '--allow-empty', '--quiet', '-m', 'New fixture revision')
        advanced = self.state(checkout)
        self.plan(checkout, runtime, refusal='Checkout branch or commit differs from the approved target')
        self.assertEqual(self.state(checkout), advanced)

    def repin(self, records, name, changes):
        path = records[name]
        records[name + '-sha256'] = write_json(path, json.loads(path.read_bytes()) | changes)
        return records[name + '-sha256']

    def test_original_recipe_and_host_runtime_still_plan(self):
        checkout, head = self.fixture('legacy', '7.3.3', test_script='test')
        before = self.state(checkout)
        records = self.reviewed_inputs('legacy', checkout, head, script='test', recipe_version=1)
        plan = self.plan(checkout, records)
        self.assertEqual(plan['version'], 1)
        self.assertEqual(plan['runtime']['tools'], self.runtime)
        self.assertNotIn('containerProfile', plan)
        self.assertEqual(plan['operations'][1]['argv'][-2:], ['run', 'test'])
        self.assertEqual(self.state(checkout), before)

    def test_installed_container_plan_reaches_only_the_private_transport_boundary(self):
        checkout, head = self.fixture('container', '^7.0.0')
        before = self.state(checkout)
        records = self.reviewed_inputs('container', checkout, head, container=True)
        path = self.case / 'plan.json'
        plan = self.plan(checkout, records, output=path)
        profile = json.loads(records['container-profile'].read_bytes())
        runtime = plan['runtime']
        self.assertEqual(plan['version'], 2)
        self.assertEqual(plan['status'], 'planned-only')
        self.assertFalse(plan['executionAuthorized'])
        self.assertEqual((plan['target']['commit'], plan['target']['tree']),
                         (head, self.git(checkout, 'rev-parse', 'HEAD^{tree}').strip()))
        self.assertEqual(plan['committedInputs'], {name: sha(checkout / name)
                         for name in ('package.json', 'package-lock.json')})
        self.assertEqual(plan['testSelection']['source'], {'path': 'package.json',
                         'locator': f'scripts["{TEST_SCRIPT}"]', 'sha256': sha(checkout / 'package.json')})
        self.assertEqual(plan['testSelection']['loopback'], 'not-approved')
        self.assertEqual(plan['gitReader'], self.runtime['git'] | {'verification': 'matched-local-executable-pin'})
        self.assertEqual(runtime['tools'], profile['tools'])
        self.assertEqual(runtime['verification'], 'declared-not-observed')
        self.assertEqual(plan['containerProfile']['sha256'], records['container-profile-sha256'])
        self.assertEqual(plan['operations'][1]['argv'],
                         ['/opt/aim-ci/node', '/opt/aim-ci/npm-cli.js', '--ignore-scripts', 'run', TEST_SCRIPT])
        args = (path, sha(path), self.manifest_pin)
        readiness = json.loads(self.run_command(sys.executable, '-IB', self.manager, 'check-recipe-execution',
                               '--plan', path, '--plan-sha256', args[1], '--manifest-sha256', self.manifest_pin,
                               exit_code=3))
        self.assertEqual(readiness['backend'], 'docker')
        self.assertEqual(readiness['status'], 'blocked-before-dispatch')
        self.assertFalse(readiness['processStarted'])
        self.assertEqual(readiness['observedEffects'], [])
        for name in ('loopback', 'containerRuntime', 'containerImage', 'dependencyStore'):
            self.assertEqual(readiness['capabilities'][name], 'unqualified')

        policy = {'version': 1, 'endpoint': 'unix:///fixture/test.sock', 'imageDigest': IMAGE,
                  'adapterSha256': sha(Path(docker_engine.__file__)), 'runtimeSha256': runtime['sha256'],
                  'controlManifestSha256': self.manifest_pin}
        journal = self.case / 'transport-fixture-journal'
        calls = []

        class TransportBoundary(Exception):
            pass

        def no_io(endpoint, method, route, body, timeout):
            calls.append((endpoint, method, route, body, timeout))
            raise TransportBoundary

        def advance(policy, store):
            return _advance_docker_recipe(*args, trusted_docker_policy=policy,
                                          trusted_docker_policy_pin=fingerprint(policy),
                                          dependency_store_sha256=store, journal_path=journal,
                                          request=no_io, clock=lambda: 100.0)

        with (patch('socket.socket', side_effect=AssertionError('Socket forbidden in admission fixture')),
              patch('subprocess.Popen', side_effect=AssertionError('Process forbidden in admission fixture'))):
            for field, wrong in (('imageDigest', 'sha256:' + '0' * 64), ('runtimeSha256', '0' * 64)):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(Refused, 'requires the reviewed adapter'):
                        advance(policy | {field: wrong}, STORE)
            with self.assertRaisesRegex(Refused, 'dependency store does not match'):
                advance(policy, '0' * 64)
            self.assertEqual(calls, [])
            self.assertFalse(journal.exists())
            prepared = check_recipe_execution(*args, trusted_docker_policy=policy,
                                              trusted_docker_policy_pin=fingerprint(policy))
            self.assertEqual(prepared['controls']['dependencyStore']['status'], 'unqualified')
            self.assertEqual(prepared['controls']['loopback']['status'], 'unqualified')
            self.assertFalse(journal.exists())
            with self.assertRaises(TransportBoundary):
                advance(policy, STORE)
            self.assertEqual(calls, [(policy['endpoint'], 'GET', f'/v1.43/images/{IMAGE}/json', None, 5)])
            create = json.loads((journal / 'effects.json').read_bytes())['operations']['docker:create']
            self.assertEqual(create['state'], 'unknown')
            self.assertEqual(create['intent']['binding'], readiness['binding'])
            self.assertEqual(create['intent']['dependencyStoreSha256'], STORE)
            self.assertEqual(create['intent']['testScript'], TEST_SCRIPT)
        self.assertEqual(self.state(checkout), before)

    def test_container_profile_requires_external_pin_and_matching_review_scope(self):
        checkout, head = self.fixture('container-refusals', '^7.0.0')
        before = self.state(checkout)
        for name, change, refusal in (
            ('missing-pin', None, 'requires a profile and its externally supplied SHA-256'),
            ('stale-bytes', {'dependencyStoreSha256': 'e' * 64}, 'Container profile SHA-256 mismatch'),
            ('unknown-field', {'qualification': 'verified'}, 'Invalid container profile fields'),
            ('unsupported', {'version': 2}, 'Unsupported container profile version'),
            ('unbound', {'imageDigest': 'sha256:' + 'e' * 64}, 'Operator approval does not bind this container profile'),
            ('unreviewed', {'dependencyStoreSha256': 'e' * 64}, 'Recipe review does not bind this selection'),
        ):
            with self.subTest(name=name):
                records = self.reviewed_inputs(name, checkout, head, container=True)
                if change is None:
                    records.pop('container-profile-sha256')
                else:
                    original = records['container-profile-sha256']
                    new_pin = self.repin(records, 'container-profile', change)
                    if name == 'stale-bytes':
                        records['container-profile-sha256'] = original
                    elif name != 'unbound':
                        self.repin(records, 'approval', {'containerProfileSha256': new_pin})
                self.plan(checkout, records, refusal=refusal)
        for field, wrong in (('targetTree', '0' * 40), ('gitReaderSha256', '0' * 64),
                             ('runtimeSha256', '0' * 64), ('testScript', 'test:other')):
            with self.subTest(field=field):
                records = self.reviewed_inputs(field, checkout, head, container=True)
                pin = self.repin(records, 'review', {field: wrong})
                self.repin(records, 'approval', {'reviewSha256': pin})
                self.plan(checkout, records, refusal='Recipe review does not bind this selection')
        records = self.reviewed_inputs('stale-review', checkout, head, container=True)
        with records['review'].open('ab') as review:
            review.write(b'\n')
        self.plan(checkout, records, refusal='Recipe review SHA-256 mismatch')
        records = self.reviewed_inputs('lock', checkout, head, container=True)
        approval = json.loads(records['approval'].read_bytes())
        self.repin(records, 'approval', {'committedInputs': approval['committedInputs'] | {
                                         'package-lock.json': '0' * 64}})
        self.plan(checkout, records, refusal='Committed package-lock.json SHA-256 mismatch')
        self.assertEqual(self.state(checkout), before)

    def test_retained_state_cannot_adopt_a_new_container_or_dependency_identity(self):
        checkout, head = self.fixture('retained', '^7.0.0')
        records = self.reviewed_inputs('retained', checkout, head, container=True)
        review = json.loads(records['review'].read_bytes())
        state = records['approval'].parent / 'state'
        state.mkdir()
        binding = {key: review[key] for key in ('controlManifestSha256', 'recipeManifestSha256',
                   'target', 'assignmentSha256', 'runtimeSha256', 'containerProfileSha256',
                   'targetTree', 'gitReaderSha256')}
        legacy = {'requests': []}
        marker = {'version': 1, 'limitNanoUsd': LIMIT, 'maxCalls': 16,
                  'legacySha256': fingerprint(legacy), 'legacyCalls': 0, 'legacyReservedNanoUsd': 0}
        files = {name: write_json(state / name, value) for name, value in (
            ('binding.json', binding | {'reviewSha256': records['review-sha256'], 'statePath': str(state)}),
            ('legacy.json', legacy), ('budget-established.json', marker), ('budget.json', marker | legacy),
        )}
        self.repin(records, 'approval', {'state': {'mode': 'retained', 'path': str(state), 'files': files}})
        plan = self.plan(checkout, records)
        self.assertEqual(plan['state']['budget']['limitNanoUsd'], LIMIT)
        profile_pin = self.repin(records, 'container-profile', {'dependencyStoreSha256': 'e' * 64})
        review_pin = self.repin(records, 'review', {'containerProfileSha256': profile_pin})
        self.repin(records, 'approval', {'containerProfileSha256': profile_pin, 'reviewSha256': review_pin})
        self.plan(checkout, records, refusal='Retained planning state identity differs from this plan')
        self.assertEqual({path.name: sha(path) for path in state.iterdir()}, files)

