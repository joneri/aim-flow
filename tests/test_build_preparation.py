"""Offline preparation contracts and installed CLI proof with mocked Docker staging."""
from base64 import b64encode
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256, sha512
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
BASE = 'sha256:' + 'a' * 64
IMAGE = 'sha256:' + 'b' * 64
STAGE = 'c' * 64
LAYER = 'sha256:' + 'd' * 64
ENDPOINT = 'unix:///fixture/prepare.sock'
PLATFORM = {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'}
LAYOUT = 'opt/aim-ci/qualification'
ZERO = '0001-01-01T00:00:00Z'


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def pin(data):
    return sha256(data).hexdigest()


def sri(data):
    return 'sha512-' + b64encode(sha512(data).digest()).decode()


def fixture_tgz(value):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        entry = tarfile.TarInfo('package/index.js')
        entry.size = len(value)
        archive.addfile(entry, io.BytesIO(value))
    return out.getvalue()


def lock_entry(data, **extra):
    return {'version': '7.3.3', 'resolved': 'https://registry.invalid/fixture.tgz', 'integrity': sri(data), **extra}


class PreparationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / 'lib'))

    def test_configuration_draft_refuses_before_preparation_paths(self):
        from aim_ci.build_preparation import _CONFIG_FIELDS, check_configuration
        from openai_budget import Refused

        fields = dict.fromkeys(_CONFIG_FIELDS.split(), 'fixture')
        check_configuration({**fields, 'version': 1})
        draft = {**fields, 'version': 2, 'source': {'tree': None}}
        with self.assertRaisesRegex(Refused, 'Incomplete preparation configuration draft'):
            check_configuration(draft)
        with self.assertRaisesRegex(Refused, 'Invalid build preparation configuration fields'):
            check_configuration({'version': 1, 'configurationDraft': draft})

    def test_source_check_configuration_allows_only_the_intentionally_absent_tarball_input(self):
        from aim_ci.build_preparation import _CONFIG_FIELDS, check_configuration
        from openai_budget import Refused

        configuration = dict.fromkeys(_CONFIG_FIELDS.split(), 'fixture')
        configuration.update(version=2, tarballsDir=None, recipe={'stack': 'npm-source-check'}, source={
            'tree': 'a'*40, 'sourceSelectionSha256': 'b'*64, 'sourceProofSha256': 'c'*64,
            'committedInputs': {'package.json': 'd'*64, 'package-lock.json': 'e'*64}})
        check_configuration(configuration)
        check_configuration({key: value for key, value in configuration.items() if key != 'tarballsDir'})
        with self.assertRaisesRegex(Refused, 'Incomplete preparation'):
            check_configuration({**configuration, 'base': None})
        with self.assertRaisesRegex(Refused, 'source binding fields'):
            check_configuration({**configuration, 'source': {
                'tree': 'a'*40, 'sourceManifestSha256': 'b'*64, 'committedInputs': configuration['source']['committedInputs']}})

    def test_selected_playwright_requires_literal_scope_and_exact_npm_inputs(self):
        from aim_ci.recipe_plan import inspect_npm, validate_recipe
        from openai_budget import Refused

        selector = {'kind': 'playwright-names-v1', 'file': 'tests/receipt.spec.ts', 'project': 'chromium',
                    'suite': 'receipt component', 'names': ['opens receipt']}
        recipe = {'version': 2, 'stack': 'npm-vite-static', 'testScript': 'test:browser',
                  'testSelector': selector, 'allowedPaths': ['index.html'], 'forbiddenPaths': ['dist'],
                  'artifact': {'directory': 'dist', 'requiredFiles': ['index.html'], 'maximumBytes': 100},
                  'limits': {'network': 'disabled', 'timeoutSeconds': 5, 'memoryMiB': 128, 'maxProcesses': 4}}
        oracle = {'kind': 'static-heading-v1', 'file': 'index.html', 'heading': 'Fixture'}
        dependencies = {'vite': '7.3.3', '@playwright/test': '1.59.1'}
        scripts = {'test:browser': 'playwright test', 'build': 'vite build'}
        package = {'scripts': scripts, 'devDependencies': dependencies}
        packages = {'': {'devDependencies': dependencies}, 'node_modules/vite': lock_entry(b'vite')}
        for name in ('@playwright/test', 'playwright', 'playwright-core'):
            packages[f'node_modules/{name}'] = lock_entry(b'playwright', version='1.59.1')
        inputs = {'package.json': package, 'package-lock.json': {'lockfileVersion': 3, 'packages': packages}}

        with patch.object(subprocess, 'Popen', side_effect=AssertionError('Process forbidden')), \
                patch.object(socket, 'socket', side_effect=AssertionError('Socket forbidden')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            validate_recipe(recipe, oracle)
            self.assertEqual(inspect_npm(inputs, recipe)['scripts'], scripts)
            for invalid, reason in (
                    ({**selector, 'argv': ['--list']}, 'Invalid Playwright selection fields'),
                    ({**selector, 'names': ['opens.*']}, 'Invalid literal Playwright')):
                with self.subTest(selector=invalid), self.assertRaisesRegex(Refused, reason):
                    validate_recipe({**recipe, 'testSelector': invalid}, oracle)
            for path in (selector['file'], 'playwright.config.ts'):
                with self.subTest(path=path), self.assertRaisesRegex(Refused, 'cannot allow edits'):
                    validate_recipe({**recipe, 'allowedPaths': ['index.html', path]}, oracle)
            with patch.dict(scripts, {'test:browser': 'playwright test --list'}):
                with self.assertRaisesRegex(Refused, 'exact committed playwright test script'):
                    inspect_npm(inputs, recipe)
            with patch.dict(packages['node_modules/playwright-core'], {'version': '1.59.2'}):
                with self.assertRaisesRegex(Refused, 'supported lock'):
                    inspect_npm(inputs, recipe)

    def test_readiness_without_independent_git_pin_dispatches_no_process_or_socket(self):
        from aim_ci.build_preparation import _CONFIG_FIELDS
        from aim_ci.operator_readiness import operator_readiness

        with tempfile.TemporaryDirectory() as location:
            directory = Path(location).resolve()
            config = dict.fromkeys(_CONFIG_FIELDS.split(), 'declared')
            config.update(version=1, manifestSha256='a'*64, platform=PLATFORM,
                          target={'repository':'https://fixture.invalid/team/project.git','branch':'main','commit':'c'*40},
                          git={'path':'/untrusted/declaration','sha256':'d'*64})
            path = directory/'config.json'
            path.write_bytes(encode(config))
            args = SimpleNamespace(workflow='acquisition', config=path, config_sha256=pin(path.read_bytes()),
                                   git_sha256=None, manifest_sha256='a'*64)
            with patch.object(subprocess, 'run', side_effect=AssertionError('Process forbidden')), \
                    patch.object(socket, 'socket', side_effect=AssertionError('Socket forbidden')), \
                    patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
                report, code = operator_readiness(directory, args, {'version':'fixture'})
                self.assertEqual((report['status'], code), ('source-not-checked', 3))
                self.assertEqual(report['next']['command'], 'readiness')
                self.assertEqual(report['source']['status'], 'declared-not-checked')
                self.assertFalse(report['ready'] or report['executionAuthorized'])
                self.assertEqual(set(report['runtime'].values()), {'not-checked'})
                args.git_sha256 = 'e'*64
                stale, code = operator_readiness(directory, args, {'version':'fixture'})
                self.assertEqual((stale['status'], code), ('stale-input', 1))
            self.assertEqual(list(directory.iterdir()), [path])

    def test_preparation_readiness_checks_optional_selector_without_transport(self):
        from aim_ci.build_preparation import _CONFIG_FIELDS, PREPARATION_RECIPE_FIELDS
        from aim_ci.operator_readiness import operator_readiness

        recipe = dict.fromkeys(PREPARATION_RECIPE_FIELDS.split(), 'declared')
        selector = {'kind': 'playwright-names-v1', 'file': 'tests/receipt.spec.ts', 'project': 'chromium',
                    'suite': 'receipt component', 'names': ['opens receipt']}
        config = dict.fromkeys(_CONFIG_FIELDS.split(), 'declared')
        config.update(version=1, manifestSha256='a'*64, platform=PLATFORM,
                      target={'repository': 'https://fixture.invalid/team/project.git', 'branch': 'main',
                              'commit': 'c'*40}, git={'path': '/untrusted/git', 'sha256': 'd'*64})
        with tempfile.TemporaryDirectory() as location, \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('Process forbidden')), \
                patch.object(socket, 'socket', side_effect=AssertionError('Socket forbidden')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            directory = Path(location).resolve()
            path = directory/'config.json'
            args = SimpleNamespace(workflow='preparation', config=path, git_sha256=None, manifest_sha256='a'*64)
            cases = ((recipe, 'source-not-checked', 3),
                     ({**recipe, 'testSelector': selector}, 'source-not-checked', 3),
                     ({**recipe, 'testSelector': {**selector, 'names': ['.*']}}, 'invalid-input', 1),
                     ({**recipe, 'testSelector': None}, 'invalid-input', 1),
                     ({**recipe, 'unexpected': selector}, 'invalid-input', 1))
            for selected_recipe, status, expected in cases:
                with self.subTest(recipe=selected_recipe):
                    raw = encode({**config, 'recipe': selected_recipe})
                    path.write_bytes(raw)
                    args.config_sha256 = pin(raw)
                    report, code = operator_readiness(directory, args, {'version': 'fixture'})
                    self.assertEqual((report['status'], code), (status, expected))
                    self.assertFalse(report['ready'] or report['executionAuthorized'])
                    self.assertEqual(set(report['runtime'].values()), {'not-checked'})
                    if code == 3:
                        self.assertEqual(report['unset'], [])
                    self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(directory.iterdir()), [path])

    def test_tarball_matching_ignores_extras_and_explains_missing_applicable_bytes(self):
        from aim_ci.build_preparation import dependency_inventory

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            vite, native, other = map(fixture_tgz, (b'vite', b'native', b'other'))
            lock = {'packages': {'': {'optionalDependencies': {'off-platform': '7.3.3'}},
                'node_modules/vite': lock_entry(vite),
                'node_modules/native': lock_entry(native, optional=True, cpu=['arm64']),
                'node_modules/off-platform': lock_entry(other, optional=True, os=['darwin'])}}
            (directory/'anything.tgz').write_bytes(vite)
            (directory/'other.tgz').write_bytes(other)
            index, _, ignored, missing = dependency_inventory(directory, lock, 'f'*64, PLATFORM)
            self.assertIsNone(index)
            self.assertEqual(ignored, [{'name': 'other.tgz', 'reason': 'outside-applicable-lock-integrities'}])
            self.assertEqual(missing, [{'locator': 'node_modules/native', 'integrity': sri(native),
                                        'resolved': 'https://registry.invalid/fixture.tgz'}])
            (directory/'native.tgz').write_bytes(native)
            (directory/'duplicate.tgz').write_bytes(vite)
            index, blobs, ignored, missing = dependency_inventory(directory, lock, 'f'*64, PLATFORM)
            self.assertFalse(missing)
            self.assertEqual(set(blobs), {sri(vite), sri(native)})
            self.assertEqual(index['excluded'], {'node_modules/off-platform': ['os']})
            self.assertEqual(index['included']['node_modules/vite']['sha256'], pin(vite))
            self.assertIn({'name': 'duplicate.tgz', 'reason': 'duplicate-applicable-integrity'}, ignored)

    def test_linked_tarball_refuses_and_sensitive_committed_dist_path_is_never_read(self):
        from aim_ci.build_preparation import dependency_inventory, source_inventory
        from openai_budget import Refused

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            data = fixture_tgz(b'vite')
            (directory/'safe').write_bytes(data)
            (directory/'link.tgz').symlink_to('safe')
            with self.assertRaisesRegex(Refused, 'linked'):
                dependency_inventory(directory, {'packages': {'node_modules/vite': lock_entry(data)}}, 'f'*64, PLATFORM)

        class Reader:
            commit = 'a'*40

            def entries(self, *args, **kwargs):
                yield 'dist/.env.local', '100644', 'blob', 'a'*40

            def size(self, oid):
                raise AssertionError('Protected blob must not be read')

        with self.assertRaisesRegex(Refused, 'environment file'):
            source_inventory(Reader())


    def test_unknown_upload_removes_inert_container_and_is_not_replayed(self):
        from aim_ci import docker_engine
        from aim_ci.image_preparation import ImagePreparation
        from openai_budget import Refused

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve()
            data = b'bounded staging tar fixture'
            (output/'input.tar').write_bytes(data)
            intent = {'base': {'imageDigest': BASE, 'tools': {}}, 'docker': {'endpoint': ENDPOINT,
                      'path': '/fixture/docker'}, 'target': {'commit': 'a'*40, 'tree': 'b'*40},
                      'storeIndexSha256': 'c'*64, 'platform': PLATFORM}
            daemon = Protocol({'docker': '/fixture/docker', 'output': str(output), 'mode': 'lost-upload'})
            with patch.object(socket,'socket',side_effect=AssertionError('Real socket forbidden')), \
                    patch.object(docker_engine,'unix_request',daemon.request), \
                    patch.object(subprocess,'run',daemon.docker_cli):
                preparation = ImagePreparation(output, intent, '1'*64, lambda: None)
                with self.assertRaisesRegex(Refused, 'cp outcome is unknown'):
                    preparation.run('input.tar', {'sha256': pin(data), 'size': len(data)})
                self.assertIsNone(daemon.stage)
                before = len(daemon.calls)
                with self.assertRaisesRegex(Refused, 'do not resend'):
                    preparation.run('input.tar', {'sha256': pin(data), 'size': len(data)})
                self.assertTrue(all(row[0]=='GET' for row in daemon.calls[before:]))
                operations = json.loads((output/'image-journal/effects.json').read_bytes())['operations']
                self.assertEqual(operations['prepare:upload']['state'],'unknown')
                self.assertEqual(operations['prepare:remove']['state'],'completed')

    def test_final_image_cleanup_contract_uses_original_identity_and_never_replays(self):
        from aim_ci import docker_engine
        from aim_ci.image_preparation import ImagePreparation
        from openai_budget import Refused

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve()
            data = b'committed fixture'
            (output/'input.tar').write_bytes(data)
            archive = {'sha256': pin(data), 'size': len(data)}
            intent = {'base': {'imageDigest': BASE, 'tools': {}}, 'docker': {'endpoint': ENDPOINT,
                      'path': '/fixture/docker'}, 'target': {'commit': 'a'*40, 'tree': 'b'*40},
                      'storeIndexSha256': 'c'*64, 'platform': PLATFORM}
            daemon = Protocol({'docker': '/fixture/docker', 'output': str(output), 'mode': 'image-cleanup-lost-absent'})
            with patch.object(socket, 'socket', side_effect=AssertionError('Real socket forbidden')), \
                    patch.object(docker_engine, 'unix_request', daemon.request), \
                    patch.object(subprocess, 'run', daemon.docker_cli):
                prep = ImagePreparation(output, intent, '1'*64, lambda: None)
                image = prep.run('input.tar', archive)
                before = len(daemon.calls)
                preview = prep.final_image(IMAGE, image, archive, preview=True)
                self.assertEqual(preview['journalState'], 'not-dispatched')
                self.assertEqual(daemon.calls[before:], [])
                with self.assertRaisesRegex(Refused, 'full derived image ID'):
                    prep.final_image(BASE, image, archive)
                self.assertEqual(daemon.calls[before:], [])
                daemon.references = STAGE.encode()
                with self.assertRaisesRegex(Refused, 'referenced by a container'):
                    prep.final_image(IMAGE, image, archive)
                daemon.references = b''
                self.assertIsNone(prep.journal.inspect('prepare:remove-image'))
                with self.assertRaisesRegex(Refused, 'rm outcome is unknown'):
                    prep.final_image(IMAGE, image, archive)
                self.assertEqual(prep.journal.inspect('prepare:remove-image')['state'], 'unknown')
                before = len(daemon.calls)
                self.assertEqual(prep.final_image(IMAGE, image, archive)['status'], 'confirmed-absent')
                self.assertTrue(all(call[0] == 'GET' for call in daemon.calls[before:]))
                self.assertEqual(daemon.calls.count(['cli', 'rm']), 1)
                self.assertTrue(prep.journal.inspect('prepare:remove-image')['reconciled'])

    def test_empty_derived_layers_are_refused(self):
        from aim_ci.image_preparation import ImagePreparation
        from openai_budget import Refused, fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve()
            intent = {'base': {'imageDigest': BASE, 'tools': {}}, 'docker': {'endpoint': ENDPOINT,
                      'path': '/fixture/docker'}, 'target': {'commit': 'a'*40, 'tree': 'b'*40},
                      'storeIndexSha256': 'c'*64, 'platform': PLATFORM}
            preparation = ImagePreparation(output, intent, '1'*64, lambda: None)
            env = ['PATH=/usr/bin']
            image = {'Id': IMAGE, 'RepoTags': [preparation.tag], 'Os': 'linux', 'Architecture': 'arm64',
                     'Config': {'Env': env, 'Labels': preparation.labels}, 'RootFS': {'Layers': []}}
            with patch.object(preparation, 'inspect_image', return_value=image):
                with self.assertRaisesRegex(Refused, 'Derived image does not match'):
                    preparation.derived(IMAGE, {'layers': [], 'env': env,
                                                'derivedLabelsSha256': fingerprint(preparation.labels)})


class Protocol:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.stage = None
        self.image = None
        self.upload = None
        self.after_create = None
        self.references = b''
        self.base_env = ['PATH=/usr/bin']
        self.base_labels = None

    def docker_cli(self, argv, **kwargs):
        assert argv[:3] == [self.fixture['docker'], '--host', ENDPOINT]
        args = argv[3:]
        self.calls.append(['cli', args[1]])
        env = kwargs['env']
        assert env['HOME'] == env['DOCKER_CONFIG'] and not list(Path(env['HOME']).iterdir())
        assert Path(env['HOME']).parent == Path(self.fixture['output'])
        assert set(env) == {'PATH', 'LC_ALL', 'HOME', 'DOCKER_CONFIG'}
        if args[:2] == ['container', 'create']:
            assert args[args.index('--pull')+1]=='never' and args[args.index('--network')+1]=='none'
            assert args[args.index('--entrypoint')+1]=='/bin/false' and args[-2]==BASE
            name = args[args.index('--name')+1]
            label = args[args.index('--label')+1].split('=',1)[1]
            self.stage = {'Id': STAGE, 'Name': '/'+name, 'Image': BASE, 'Mounts': [],
                'Config': {'Entrypoint':['/bin/false'],'Cmd':['--aim-ci-preparation-only'],
                           'Labels':{**(self.base_labels or {}), 'org.aim-ci.preparation':label}},
                'HostConfig': {'NetworkMode':'none','Privileged':False,'ReadonlyRootfs':False,'AutoRemove':False,
                    'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'RestartPolicy':{'Name':'no'}},
                'State': {'Status':'created','Pid':0,'Running':False,'Paused':False,'Restarting':False,'Dead':False,
                          'OOMKilled':False,'StartedAt':ZERO,'FinishedAt':ZERO}}
            if self.after_create is not None:
                self.after_create()
            data=STAGE.encode()
        elif args == ['container','cp','-',STAGE+':/']:
            assert self.stage is not None
            self.upload=kwargs['stdin'].read()
            if self.fixture['mode']=='lost-upload':
                raise subprocess.TimeoutExpired(argv,120)
            data=b''
        elif args == ['container','diff',STAGE]:
            data=b'A /opt/aim-ci/npm_platform_build.cjs\nA /opt/aim-ci/qualification\n'
        elif args[:3]==['container','commit','--no-pause']:
            assert self.stage is not None and self.upload and args[-2]==STAGE
            labels=dict(item.removeprefix('LABEL ').split('=',1) for item in args[4:-2:2])
            self.image={'Id':IMAGE,'RepoTags':[args[-1]],'RepoDigests':['aim-ci-prepared@'+IMAGE],
                        'Os':'linux','Architecture':'arm64',
                        'Config':{'Env':['PATH=/usr/bin'],'Labels':{**self.stage['Config']['Labels'], **labels}},
                        'RootFS':{'Layers':[LAYER,'sha256:'+'e'*64]}}
            if self.fixture['mode']=='lost-commit':
                raise subprocess.TimeoutExpired(argv,120)
            data=IMAGE.encode()
        elif args == ['container','ls','--all','--quiet','--no-trunc','--filter','ancestor='+IMAGE]:
            data=self.references
        elif args == ['image','rm','--no-prune',IMAGE]:
            assert self.image is not None and not self.references
            if self.fixture['mode'] == 'image-cleanup-lost-present':
                raise subprocess.TimeoutExpired(argv,20)
            self.image=None
            if self.fixture['mode'] == 'image-cleanup-lost-absent':
                raise subprocess.TimeoutExpired(argv,20)
            data=b''
        else:
            raise AssertionError(f'Unreviewed Docker argv {args}')
        return subprocess.CompletedProcess(argv,0,data,b'')

    def request(self, endpoint, method, route, body, timeout):
        assert endpoint==ENDPOINT and timeout==5 and body is None
        self.calls.append([method,route])
        if method=='GET' and route==f'/v1.43/images/{BASE}/json':
            return 200,encode({'Id':BASE,'Os':'linux','Architecture':'arm64',
                              'Config':{'Env':self.base_env,'Labels':self.base_labels},'RootFS':{'Layers':[LAYER]}})
        if method=='GET' and route.startswith('/v1.43/images/'):
            selector=route.split('/')[-2]
            if self.image and selector in (IMAGE,self.image['RepoTags'][0]):
                return 200,encode(self.image)
            return 404,b''
        if method=='GET' and route.startswith('/v1.43/containers/'):
            selector=route.split('/')[-2]
            if self.stage and selector in (STAGE,self.stage['Name'][1:]):
                return 200,encode(self.stage)
            return 404,b''
        if method=='DELETE' and route==f'/v1.43/containers/{STAGE}?force=false&v=false':
            assert self.stage is not None
            self.stage=None
            return 204,b''
        raise AssertionError(f'Unreviewed Docker request {method} {route}')


def drive(path):
    fixture=json.loads(Path(path).read_bytes())
    installed=Path(fixture['installed'])
    entry=installed/'aim-ci-control.py'
    sys.path.insert(0,str(installed/'lib'))
    from aim_ci import docker_engine
    assert Path(docker_engine.__file__).is_relative_to(installed)
    protocol=Protocol(fixture)
    if fixture['mode'] == 'image-cleanup':
        protocol.base_labels = {'org.example.base': 'inherited', 'org.aim-ci.source-commit': 'overridden'}
    subprocess_run=subprocess.run
    events={}

    def dispatch(argv,**kwargs):
        if argv[0]==fixture['docker']:
            return protocol.docker_cli(argv,**kwargs)
        assert str(argv[0])==fixture['git'], argv
        assert kwargs['env']['GIT_ALLOW_PROTOCOL']==''
        return subprocess_run(argv,**kwargs)

    def invoke(label,*args):
        out,err=io.StringIO(),io.StringIO()
        before=len(protocol.calls)
        with patch.object(sys,'argv',[str(entry),*args,'--manifest-sha256',fixture['manifestPin']]), \
                redirect_stdout(out),redirect_stderr(err):
            try:
                runpy.run_path(str(entry),run_name='__main__')
            except SystemExit as exit:
                code=exit.code
        events[label]={'code':code,'stdout':json.loads(out.getvalue()) if out.getvalue() else None,
                       'stderr':err.getvalue(),'calls':protocol.calls[before:]}
        return events[label]

    config=['--config',fixture['config'],'--config-sha256',fixture['configPin']]
    with patch.object(socket,'socket',side_effect=AssertionError('Real Docker socket forbidden')), \
            patch.object(docker_engine,'unix_request',protocol.request), patch.object(subprocess,'run',dispatch):
        if fixture['mode'] == 'selected':
            invoke('selectedReadiness', 'readiness', '--workflow', 'preparation', *config,
                   '--git-sha256', json.loads(Path(fixture['config']).read_bytes())['git']['sha256'])
        preview=invoke('preview','prepare-reviewed-build','--preview',*config)
        if fixture['mode'].startswith('selected-invalid-'):
            print(json.dumps(events))
            return
        if fixture['mode']=='unsupported':
            invoke('unsupportedApproval','prepare-reviewed-build','--authorize-intent','0'*64,*config)
            print(json.dumps(events))
            return
        if fixture['mode'] in ('optional-graph', 'optional-root-mismatch'):
            invoke('graphApproval', 'prepare-reviewed-build', '--authorize-intent', '0'*64, *config)
            print(json.dumps(events))
            return
        intent=preview['stdout']['intentSha256']
        invoke('wrongApproval','prepare-reviewed-build','--authorize-intent','0'*64,*config)
        if fixture['mode']=='stale-source':
            env={'PATH':'/usr/bin:/bin','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1',
                 'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@example.invalid',
                 'GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@example.invalid',
                 'GIT_ALLOW_PROTOCOL':''}
            subprocess_run([fixture['git'],'-c','core.hooksPath=/dev/null','commit','--allow-empty',
                            '-qm','Move fixture HEAD'],cwd=fixture['checkout'],env=env,check=True,timeout=10)
            invoke('staleSourceApproval','prepare-reviewed-build','--authorize-intent',intent,*config)
        elif fixture['mode']=='stale':
            Path(fixture['tarball']).write_bytes(b'changed after preview')
            invoke('missingPreview','prepare-reviewed-build','--preview',*config)
            invoke('staleApproval','prepare-reviewed-build','--authorize-intent',intent,*config)
        elif fixture['mode']=='withdraw-active':
            cleanup = ['cleanup-build-preparation', '--output', fixture['output'], '--intent-sha256', intent]
            protocol.after_create = lambda: invoke('activeCleanup', *cleanup)
            invoke('approved', 'prepare-reviewed-build', '--authorize-intent', intent, *config)
            invoke('finalCleanup', *cleanup)
            invoke('revokedResume', 'prepare-reviewed-build', '--resume', *config)
        else:
            result=invoke('approved','prepare-reviewed-build','--authorize-intent',intent,*config)
            if fixture['mode']=='lost-commit':
                result=invoke('resumed','prepare-reviewed-build','--resume',*config)
            run=result['stdout']
            args=['--config',run['runConfigPath'],'--config-sha256',run['runConfigSha256']]
            if fixture['mode']=='success':
                invoke('buildReadiness','readiness','--workflow','build',*args)
            invoke('runPreview','run-reviewed-build','--preview',*args)
            if fixture['mode'] in ('selected', 'selected-browser'):
                invoke('selectedPreview','run-reviewed-build','--preview','--with-offline-test',*args)
            invoke('prepCannotApproveBuild','run-reviewed-build','--authorize-intent',intent,*args)
            if fixture['mode'].startswith('image-cleanup'):
                cleanup = ['cleanup-build-preparation', '--output', fixture['output'], '--intent-sha256', intent]
                final = [*cleanup, '--remove-image', IMAGE]
                output = Path(fixture['output'])
                journal = output/'image-journal/effects.json'
                if fixture['mode'] == 'image-cleanup':
                    before = {p:pin(p.read_bytes()) for p in output.rglob('*') if p.is_file()}
                    invoke('previewCleanup', *final, '--preview')
                    assert {p:pin(p.read_bytes()) for p in output.rglob('*') if p.is_file()} == before
                    invoke('wrongImage', *cleanup, '--remove-image', BASE)
                    invoke('unownedImage', *cleanup, '--remove-image', 'sha256:'+'f'*64)
                    invoke('previewWithoutImage', *cleanup, '--preview')
                    config_path = Path(fixture['config'])
                    config_bytes = config_path.read_bytes()
                    config_path.write_bytes(config_bytes+b' ')
                    invoke('changedConfig', *final)
                    config_path.write_bytes(config_bytes)
                    archive = output/'image-input.tar'
                    archive_bytes = archive.read_bytes()
                    archive.write_bytes(archive_bytes+b' ')
                    invoke('changedArchive', *final)
                    archive.write_bytes(archive_bytes)
                    state = journal.read_bytes()
                    changed = json.loads(state)
                    changed['operations']['prepare:commit']['intent']['base'] = BASE
                    journal.write_bytes(encode(changed))
                    invoke('malformedBase', *final, '--preview')
                    journal.write_bytes(state)
                    invoke('defaultCleanup', *cleanup)
                    assert protocol.image is not None
                    labels = protocol.image['Config']['Labels']
                    assert labels['org.example.base'] == 'inherited'
                    assert labels['org.aim-ci.source-commit'] == run['image']['labels']['org.aim-ci.source-commit']
                    assert labels['org.aim-ci.source-commit'] != protocol.base_labels['org.aim-ci.source-commit']
                    protocol.base_env = ['PATH=/unexpected']
                    invoke('changedBase', *final)
                    protocol.base_env = ['PATH=/usr/bin']
                    protocol.base_labels['org.example.base'] = 'changed'
                    invoke('changedBaseLabel', *final)
                    protocol.base_labels['org.example.base'] = 'inherited'
                    protocol.image['RepoTags'].append('foreign:tag')
                    invoke('extraTag', *final)
                    protocol.image['RepoTags'].pop()
                    protocol.image['RepoDigests'].append('foreign@'+IMAGE)
                    invoke('extraDigest', *final)
                    protocol.image['RepoDigests'].pop()
                    labels['org.example.extra'] = 'unexpected'
                    invoke('extraLabel', *final)
                    del labels['org.example.extra']
                    labels['org.example.base'] = 'changed'
                    invoke('changedLabel', *final)
                    labels['org.example.base'] = 'inherited'
                    protocol.references = (STAGE+'\n').encode()
                    invoke('inUse', *final)
                    protocol.references = b''
                    assert 'prepare:remove-image' not in json.loads(journal.read_bytes())['operations']
                invoke('finalCleanup', *final)
                invoke('recheckPreview', *final, '--preview')
                invoke('recheckCleanup', *final)
    print(json.dumps(events))


class InstalledPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates=[Path('/usr/bin/git'),Path(shutil.which('git') or '/usr/bin/git').resolve()]
        cls.git=None
        for candidate in candidates:
            try:
                version=subprocess.check_output([candidate,'--version'],timeout=10)
                matched=re.search(rb'(\d+)\.(\d+)\.(\d+)',version)
                if matched and tuple(map(int,matched.groups())) >= (2,54,0):
                    cls.git=candidate.resolve()
                    break
            except (OSError, subprocess.SubprocessError):
                continue
        if cls.git is None:
            raise unittest.SkipTest('Installed preparation fixture requires real Git 2.54 or newer')
        tmp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.root=Path(tmp.name).resolve()
        cls.installed=cls.root/'controller'
        archive=cls.root/'controller.tar.gz'
        exe=[sys.executable,'-IB',str(ROOT/'aim-ci-control.py')]
        receipt=json.loads(subprocess.check_output([*exe,'build','--version','1.0.0-local.1','--output',str(archive)]))
        subprocess.check_output([*exe,'install','--archive',str(archive),'--sha256',receipt['archiveSha256'],
                                 '--destination',str(cls.installed)])
        cls.manifest=receipt['manifestSha256']

    def create_fixture(self, mode):
        root=self.root/mode
        root.mkdir()
        checkout=root/'checkout'
        checkout.mkdir()
        tarballs=root/'tarballs'
        tarballs.mkdir()
        docker=root/'mock-docker'
        marker = root / 'unexpected-dispatch'
        docker.write_text(f'#!/bin/sh\n: > "{marker}"\nexit 99\n')
        docker.chmod(0o700)
        def git(*args):
            env={'PATH':'/usr/bin:/bin','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1',
                 'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@example.invalid',
                 'GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@example.invalid'}
            return subprocess.check_output([self.git,'-c','core.hooksPath=/dev/null',*args],cwd=checkout,env=env)
        branch = 'pilot-branch' if mode.startswith('draft') else 'main'
        git('init','-q','-b',branch)
        origin='https://fixture.invalid/owner/project.git'
        git('remote','add','origin',origin)
        blob=fixture_tgz(b'vite')
        file=tarballs/'vite-original-name.tgz'
        file.write_bytes(blob)
        (tarballs/'optional-other.tgz').write_bytes(fixture_tgz(b'unrelated'))
        deps={'vite':'^7.0.0' if mode.startswith('draft') else '7.3.3'}
        package={'scripts':{'test:unit':'never-run-test','build':'never-run-build'},'devDependencies':deps}
        lock={'lockfileVersion':3,'packages':{'':{'devDependencies':deps},'node_modules/vite':lock_entry(blob)}}
        if mode.startswith('source-check'):
            package = {'scripts': {'test:unit': 'node check.cjs'}}
            lock = {'lockfileVersion': 3, 'packages': {'': {}, 'node_modules/not-installed': {}}}
            (checkout/'check.cjs').write_text('process.exit(0);\n')
            (checkout/'.npmrc').write_text('omitted-private-configuration=true\n')
        if mode.startswith('selected'):
            selector = {'kind': 'playwright-names-v1', 'file': 'tests/receipt.spec.ts',
                        'project': 'chromium', 'suite': 'receipt component', 'names': ['opens receipt']}
            if mode != 'selected-invalid-script':
                package['scripts']['test:unit'] = 'playwright test'
            deps['@playwright/test'] = '1.59.1'
            for name in ('@playwright/test', 'playwright', 'playwright-core'):
                version = '1.59.2' if name == 'playwright' and mode == 'selected-invalid-lock' else '1.59.1'
                lock['packages']['node_modules/' + name] = lock_entry(blob, version=version)
            (checkout/'playwright.config.ts').write_text('// inert synthetic fixture')
            if mode != 'selected-invalid-source':
                (checkout/'tests').mkdir()
                (checkout/selector['file']).write_text('// inert synthetic fixture')
        if mode in ('draft-acquire', 'draft-readiness'):
            lock['packages']['node_modules/vite']['resolved'] = 'https://registry.npmjs.org/vite/-/vite-7.3.3.tgz'
        if mode in ('optional-graph', 'optional-root-mismatch', 'draft-acquire-optional-graph'):
            optional = {'off-platform': '7.3.3'}
            lock['packages'][''].update(optionalDependencies=optional)
            lock['packages']['node_modules/off-platform'] = lock_entry(fixture_tgz(b'off-platform'),
                optional=True, os=['darwin'], dependencies={'leaf': '7.3.3'})
            lock['packages']['node_modules/leaf'] = lock_entry(fixture_tgz(b'leaf'), optional=True)
            if mode != 'optional-root-mismatch':
                package['optionalDependencies'] = optional
        if mode in ('draft-unknown', 'draft-readiness-unknown'):
            (checkout/'pyproject.toml').write_text('[project]\nname = "fixture-python"\nversion = "1.0.0"\n')
        else:
            if mode.startswith('draft'):
                package['scripts']['build'] = str(docker)
                (checkout/'README.md').write_text(f'Execute `{docker}`.\n\nRun `npm run test:unit`.\n')
            (checkout/'package.json').write_bytes(encode(package))
            (checkout/'package-lock.json').write_bytes(encode(lock))
        (checkout/'Font File.woff2').write_bytes(b'committed')
        (checkout/'index.html').write_text('<h1>Fixture</h1>')
        git('add','--','.')
        git('commit','-qm','Fixture source')
        commit,tree=git('rev-parse','HEAD','HEAD^{tree}').decode().splitlines()
        (checkout/'Font File.woff2').write_bytes(b'uncommitted must not be staged')
        output=root/'prepared'
        configuration={'version':1,'manifestSha256':self.manifest,'checkout':str(checkout),'outputDir':str(output),
            'target':{'repository':origin,'branch':branch,'commit':commit},
            'git':{'path':str(self.git),'sha256':pin(self.git.read_bytes())},
            'base':{'imageDigest':BASE,'tools':{'node':{'path':'/usr/bin/node','sha256':'1'*64},
                    'npm':{'path':'/usr/lib/npm-cli.js','sha256':'2'*64}}},
            'docker':{'path':str(docker),'sha256':pin(docker.read_bytes()),'endpoint':ENDPOINT},
            'platform':PLATFORM,'tarballsDir':str(tarballs),
            'recipe':{'id':'fixture','stack':'npm-vite-static','testScript':'test:unit','allowedPaths':['index.html'],
                      'forbiddenPaths':['dist'],'artifact':{'directory':'dist','requiredFiles':['index.html'],'maximumBytes':100},
                      'limits':{'network':'disabled','timeoutSeconds':5,'memoryMiB':128,'maxProcesses':4}},
            'oracle':{'kind':'static-heading-v1','file':'index.html','heading':'Fixture'},
            'assignment':{'version':1,'id':'fixture','title':'Fixture','acceptance':'Fixture heading','paths':['index.html']}}
        if mode.startswith('selected'):
            configuration['recipe']['testSelector'] = selector
            if mode == 'selected-browser':
                from test_docker_image_evidence import browser_fixture
                configuration['base']['browserRuntime'], _ = browser_fixture()
            if mode == 'selected-invalid-scope':
                configuration['recipe']['allowedPaths'].append(selector['file'])
        if mode=='unsupported':
            configuration['recipe']['stack']='python-static'
        if mode.startswith('source-check'):
            configuration.update(tarballsDir=None, oracle={'kind': 'source-check-exit-v1', 'script': 'test:unit'})
            configuration['recipe'].update(stack='npm-source-check', allowedPaths=['check.cjs'],
                sourcePaths=['check.cjs', 'package-lock.json', 'package.json'], dependencies={'mode': 'none'},
                artifact={'kind': 'none'})
            configuration['assignment']['paths'] = ['check.cjs']
        return root, checkout, configuration, git, tree

    def fixture(self,mode):
        root, checkout, configuration, _, tree = self.create_fixture(mode)
        config=root/'config.json'
        config.write_bytes(encode(configuration))
        fixture=root/'fixture.json'
        output = Path(configuration['outputDir'])
        fixture.write_bytes(encode({'mode':mode,'installed':str(self.installed),'manifestPin':self.manifest,
            'config':str(config),'configPin':pin(config.read_bytes()),'output':str(output),
            'git':str(self.git),'docker':configuration['docker']['path'],
            'tarball':str(root/'tarballs/vite-original-name.tgz'),'checkout':str(checkout)}))
        result=subprocess.run([sys.executable,'-IB',str(Path(__file__).resolve()),'--fixture-driver',str(fixture)],
                              capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout),output,checkout,configuration['target']['commit'],tree

    def installed_cli(self, root, *args, refusal=None, expected=0):
        result = subprocess.run([sys.executable, '-IB', str(self.installed/'aim-ci-control.py'), *map(str, args),
                                 '--manifest-sha256', self.manifest], cwd=root,
                                env={'PATH': str(root/'no-tools'), 'HOME': str(root), 'LC_ALL': 'C'},
                                capture_output=True, text=True, timeout=30)
        if refusal:
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(result.stdout)
            self.assertIn(refusal, result.stderr)
            return None
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertFalse(result.stderr)
        return json.loads(result.stdout)

    def draft(self, root, config, *args, refusal=None):
        return self.installed_cli(root, 'draft-build-preparation', '--checkout', config['checkout'],
            '--git', self.git, '--git-sha256', config['git']['sha256'], '--commit', config['target']['commit'],
            *args, refusal=refusal)

    def test_source_free_draft_keeps_observations_and_choices_separate(self):
        root, checkout, config, _, tree = self.create_fixture('draft-success')
        def snapshot():
            return {p.relative_to(root): pin(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
        before = snapshot()
        report = self.draft(root, config, '--project-doc', 'README.md')
        self.assertEqual(snapshot(), before)
        self.assertFalse(report['executionAuthorized'])
        self.assertFalse(report['checkoutFacts']['identityVerified'])
        draft = report['configurationDraft']
        self.assertEqual(draft['target'], config['target'])
        self.assertEqual(draft['source']['tree'], tree)
        self.assertEqual(draft['source']['committedInputs']['package.json'], pin((checkout/'package.json').read_bytes()))
        self.assertEqual(draft['source']['committedInputs']['package-lock.json'],
                         pin((checkout/'package-lock.json').read_bytes()))
        self.assertIsNone(draft['recipe']['stack'])
        self.assertIsNone(draft['recipe']['testScript'])
        for key in ('docker', 'base', 'platform', 'oracle', 'assignment', 'tarballsDir', 'outputDir'):
            self.assertIsNone(draft[key])
        self.assertEqual(report['recipeSuggestion']['stack'], 'npm-vite-static')
        self.assertEqual(report['declaredScripts']['values']['build'], config['docker']['path'])
        self.assertFalse(report['declaredScripts']['behaviorVerified'])
        selected = self.draft(root, config, '--test-script', 'test:unit')['configurationDraft']
        self.assertEqual(selected['recipe']['testScript'], 'test:unit')
        self.draft(root, config, '--test-script', 'test:unit; anything', refusal='literal test script')

    def test_source_free_unknown_stack_reports_no_configuration_or_credential(self):
        root, _, config, git, _ = self.create_fixture('draft-unknown')
        git('config', '--local', 'remote.origin.url', 'https://user:synthetic-sentinel@fixture.invalid/team/site.git')
        git('checkout', '--quiet', '--detach', config['target']['commit'])
        report = self.draft(root, config)
        self.assertEqual(report['status'], 'preparation-draft-needs-investigation')
        self.assertIsNone(report['configurationDraft'])
        self.assertEqual(report['checkoutFacts']['values'], {'repository': None, 'branch': None})
        self.assertNotIn('synthetic-sentinel', json.dumps(report))
        self.assertTrue(any(row['path']=='pyproject.toml' for row in report['adaptation']['observations']['files']))
        self.assertTrue(report['adaptation']['proposal']['reasons'])
        self.assertFalse((root/'unexpected-dispatch').exists())

    def test_source_free_draft_requires_completion_fresh_source_and_separate_consent(self):
        root, checkout, config, git, _ = self.create_fixture('draft-binding')
        report = self.draft(root, config, '--test-script', 'test:unit')
        draft = report['configurationDraft']
        path = root/'reviewed.json'

        def prepare(document, *mode, refusal=None):
            path.write_bytes(encode(document))
            return self.installed_cli(root, 'prepare-reviewed-build', *mode, '--config', path,
                                      '--config-sha256', pin(path.read_bytes()), refusal=refusal)

        prepare(report, '--preview', refusal='Invalid build preparation configuration fields')
        prepare(draft, '--authorize-intent', '0'*64, refusal='Incomplete preparation configuration draft')
        completed = {**config, 'version': 2, 'source': draft['source']}
        preview = prepare(completed, '--preview')
        self.assertEqual(preview['intent']['sourceManifestSha256'], draft['source']['sourceManifestSha256'])
        self.assertFalse(preview['executionAuthorized'])
        prepare(completed, '--authorize-intent', report['adaptation']['observationSha256'],
                refusal='Explicit preparation approval must match')
        altered = {**completed, 'source': {**draft['source'], 'tree': '0'*40}}
        prepare(altered, '--preview', refusal='Preparation source binding differs')
        (checkout/'index.html').write_text('<h1>New committed tree</h1>')
        git('add', '--', 'index.html')
        git('commit', '-qm', 'New fixture tree')
        self.draft(root, config, refusal='HEAD differs')
        completed['target'] = {**config['target'], 'commit': git('rev-parse','HEAD').decode().strip()}
        prepare(completed, '--preview', refusal='Preparation source binding differs')
        self.assertFalse(Path(config['outputDir']).exists())
        self.assertFalse((root/'unexpected-dispatch').exists())

    def test_source_free_draft_refuses_tampered_committed_definition(self):
        root, checkout, config, git, _ = self.create_fixture('draft-tamper')
        oid = git('rev-parse', 'HEAD:package.json').decode().strip()
        object_path = checkout/'.git/objects'/oid[:2]/oid[2:]
        data = b'{"altered":true}'
        object_path.chmod(0o600)
        object_path.write_bytes(zlib.compress(f'blob {len(data)}\0'.encode() + data))
        self.draft(root, config, refusal='Committed Git object bytes differ from their identity')
        self.assertFalse((root/'unexpected-dispatch').exists())

    def test_source_free_acquisition_preview_needs_no_runtime_values_or_state(self):
        root, _, config, _, tree = self.create_fixture('draft-acquire')
        draft = self.draft(root, config)['configurationDraft']
        tarballs = Path(config['tarballsDir'])
        tarballs.chmod(0o700)
        (tarballs/'vite-original-name.tgz').unlink()
        draft.update(platform=PLATFORM, tarballsDir=str(tarballs))
        self.assertIsNone(draft['docker'])
        self.assertIsNone(draft['base'])
        self.assertIsNone(draft['oracle'])
        self.assertIsNone(draft['recipe']['limits'])
        state, path = root/'acquisition', root/'acquisition-inputs.json'
        path.write_bytes(encode(draft))
        args = ('acquire-npm-tarballs', '--state', state, '--config', path, '--config-sha256', pin(path.read_bytes()),
                '--locator', 'node_modules/vite')
        preview = self.installed_cli(root, *args, '--preview')
        self.assertEqual(preview['status'], 'acquisition-preview')
        self.assertEqual(preview['intent']['source']['tree'], tree)
        self.assertFalse(preview['intent']['fullLockVerified'])
        self.assertFalse(preview['executionAuthorized'])
        request, = preview['intent']['requests']
        self.assertEqual(request['package']['resolved'], 'https://registry.npmjs.org/vite/-/vite-7.3.3.tgz')
        self.assertEqual(request['package']['version'], '7.3.3')
        self.installed_cli(root, *args, '--authorize-intent', '0'*64, refusal='exact nonempty preview')
        self.installed_cli(root, 'prepare-reviewed-build', '--preview', '--config', path,
                           '--config-sha256', pin(path.read_bytes()), refusal='Incomplete preparation')
        self.assertFalse(state.exists())
        self.assertEqual([item.name for item in tarballs.iterdir()], ['optional-other.tgz'])
        self.assertFalse((root/'unexpected-dispatch').exists())
        draft['source']['tree'] = '0'*40
        path.write_bytes(encode(draft))
        self.installed_cli(root, 'acquire-npm-tarballs', '--preview', '--state', state, '--config', path,
                           '--config-sha256', pin(path.read_bytes()), refusal='Preparation source binding differs')

    def test_source_free_acquisition_refuses_other_registry_or_graph_before_state(self):
        for mode, refusal in (('draft-acquire-unsupported', 'Unsupported locked tarball URL'),
                              ('draft-acquire-optional-graph', 'no proven nonoptional path')):
            with self.subTest(mode=mode):
                root, _, config, _, _ = self.create_fixture(mode)
                draft = self.draft(root, config)['configurationDraft']
                tarballs = Path(config['tarballsDir'])
                tarballs.chmod(0o700)
                (tarballs/'vite-original-name.tgz').unlink()
                draft.update(platform=PLATFORM, tarballsDir=str(tarballs))
                path, state = root/'acquisition-inputs.json', root/'acquisition'
                path.write_bytes(encode(draft))
                self.installed_cli(root, 'acquire-npm-tarballs', '--preview', '--state', state, '--config', path,
                                   '--config-sha256', pin(path.read_bytes()), refusal=refusal)
                self.assertFalse(state.exists())

    def test_source_free_readiness_distinguishes_unset_missing_and_preview_inputs(self):
        root, _, config, git, tree = self.create_fixture('draft-readiness')
        draft = self.draft(root, config)['configurationDraft']
        path = root/'readiness-config.json'
        git_pin = config['git']['sha256']

        def readiness(document, workflow='preparation', *, local_source=True, expected=3):
            raw = encode(document) if not isinstance(document, bytes) else document
            path.write_bytes(raw)
            flags = ['--git-sha256', git_pin] if local_source else []
            return self.installed_cli(root, 'readiness', '--workflow', workflow, '--config', path,
                                      '--config-sha256', pin(raw), *flags, expected=expected)

        initial = readiness(draft, local_source=False)
        self.assertEqual(initial['status'], 'configuration-incomplete')
        self.assertEqual(initial['source']['status'], 'declared-not-checked')
        self.assertIn('platform', initial['unset'])
        self.assertIn('recipe.testScript', initial['unset'])
        observed = readiness(draft)
        self.assertEqual(observed['source']['binding']['tree'], tree)
        self.assertTrue(observed['source']['checkoutCompared'])
        self.assertEqual(observed['dependencies']['status'], 'not-checked')
        tarballs = Path(config['tarballsDir'])
        tarballs.chmod(0o700)
        tarball = tarballs/'vite-original-name.tgz'
        retained = tarball.read_bytes()
        tarball.unlink()
        draft.update(platform=PLATFORM, tarballsDir=str(tarballs))
        missing = readiness(draft, 'acquisition')
        self.assertEqual(missing['status'], 'missing-platform-tarballs')
        self.assertEqual(missing['next']['command'], 'acquire-npm-tarballs')
        self.assertIn('--preview', missing['next']['arguments'])
        self.assertEqual(missing['dependencies']['missingTarballs'][0]['locator'], 'node_modules/vite')
        tarball.write_bytes(retained)
        complete = readiness(config)
        self.assertEqual(complete['status'], 'preview-available')
        self.assertEqual(complete['next']['command'], 'prepare-reviewed-build')
        self.assertTrue(complete['package']['verified'])
        self.assertFalse(complete['ready'] or complete['executionAuthorized'])
        self.assertEqual(set(complete['runtime'].values()), {'not-checked'})
        self.assertEqual(readiness(b'{', expected=1)['status'], 'invalid-input')
        git('commit', '--allow-empty', '-qm', 'Move source after drafting')
        self.assertEqual(readiness(config, expected=1)['status'], 'stale-input')
        self.assertFalse(Path(config['outputDir']).exists())
        self.assertFalse((root/'unexpected-dispatch').exists())

    def test_source_free_readiness_links_unknown_stack_to_committed_definition(self):
        root, checkout, config, _, _ = self.create_fixture('draft-readiness-unknown')
        path = root/'config.json'
        path.write_bytes(encode(config))
        report = self.installed_cli(root, 'readiness', '--workflow', 'preparation', '--config', path,
            '--config-sha256', pin(path.read_bytes()), '--git-sha256', config['git']['sha256'], expected=1)
        self.assertEqual(report['status'], 'unsupported-input')
        self.assertEqual(report['sourceEvidence'], [{'path':'pyproject.toml',
                                                     'sha256':pin((checkout/'pyproject.toml').read_bytes())}])
        self.assertIn('discover-project', report['next']['decision'])
        self.assertFalse(Path(config['outputDir']).exists())

    def test_installed_preparation_pins_exact_source_and_hands_off_to_unapproved_build_preview(self):
        events,output,checkout,commit,tree=self.fixture('success')
        self.assertEqual(events['approved']['code'],0)
        self.assertEqual(events['preview']['calls'],[])
        self.assertEqual(events['wrongApproval']['calls'],[])
        self.assertEqual(events['runPreview']['calls'],[])
        readiness = events['buildReadiness']
        self.assertEqual((readiness['code'], readiness['calls']), (3, []))
        self.assertEqual(readiness['stdout']['source']['status'], 'pinned-planning-records')
        self.assertFalse(readiness['stdout']['source']['checkoutCompared'])
        self.assertEqual(readiness['stdout']['next']['command'], 'run-reviewed-build')
        self.assertEqual(events['prepCannotApproveBuild']['code'],1)
        self.assertFalse((output/'run-state').exists())
        self.assertEqual((checkout/'Font File.woff2').read_bytes(),b'uncommitted must not be staged')
        plan=json.loads((output/'plan.json').read_bytes())
        self.assertEqual((plan['target']['commit'],plan['target']['tree']),(commit,tree))
        self.assertFalse(plan['executionAuthorized'])
        inventory = json.loads((output/'source-manifest.json').read_bytes())
        review = json.loads((output/'planning-review.json').read_bytes())
        self.assertEqual(plan['sourceInventory'], inventory)
        self.assertEqual(review['sourceInventory'], inventory)
        self.assertEqual(plan['sourceInventorySha256'], pin(encode(inventory)))
        self.assertEqual(events['runPreview']['stdout']['intent']['binding']['sourceInventorySha256'], pin(encode(inventory)))
        preview=events['runPreview']['stdout']['intent']
        self.assertEqual(preview['binding']['imageDigest'],IMAGE)
        self.assertEqual(events['preview']['stdout']['ignoredFiles'][0]['name'],'optional-other.tgz')
        with tarfile.open(output/'image-input.tar','r:') as archive:
            self.assertEqual(archive.extractfile(LAYOUT+'/source/Font File.woff2').read(),b'committed')
            index=json.load(archive.extractfile(LAYOUT+'/dependency-store/index.json'))
            files=[p for p in archive.getnames() if p.startswith(LAYOUT+'/dependency-store/blobs/')]
            self.assertEqual(len(files),1)
            self.assertEqual(index['lockSha256'],plan['committedInputs']['package-lock.json'])
        self.assertEqual([row[1] for row in events['approved']['calls'] if row[0]=='cli'],
                         ['create','cp','diff','commit'])

    def test_installed_source_check_preparation_has_separate_approval_and_exact_source_proof(self):
        events, output, checkout, commit, tree = self.fixture('source-check')
        self.assertEqual(events['approved']['code'], 0, events['approved']['stderr'])
        for label in ('preview', 'wrongApproval', 'runPreview', 'prepCannotApproveBuild'):
            self.assertEqual(events[label]['calls'], [], label)
        self.assertEqual(events['prepCannotApproveBuild']['code'], 1)
        self.assertFalse((output/'run-state').exists())
        plan = json.loads((output/'plan.json').read_bytes())
        review = json.loads((output/'planning-review.json').read_bytes())
        proof = json.loads((output/'source.proof.json').read_bytes())
        expected_paths = ['check.cjs', 'package-lock.json', 'package.json']
        self.assertEqual(plan['execution'], {'kind': 'npm-source-check-v1', 'dependencies': 'none',
                                           'outputs': 'none', 'sourcePaths': expected_paths})
        self.assertEqual((proof['commit'], proof['tree']), (commit, tree))
        self.assertEqual(plan['sourceFiles'], review['sourceFiles'])
        self.assertEqual(plan['sourceProofSha256'], pin(encode(proof)))
        self.assertEqual(review['sourceSelectionSha256'], pin(encode(plan['sourceFiles'])))
        preview = events['runPreview']['stdout']['intent']
        self.assertEqual(preview['dependencyStore']['kind'], 'npm-no-install-v1')
        self.assertEqual(preview['artifact'], {'kind': 'none'})
        self.assertIsNone(preview['workflow']['artifactRoot'])
        self.assertFalse(preview['workflow']['dependencyInstallation'] or preview['workflow']['buildCommand'])
        self.assertEqual([op['name'] for op in preview['operations']], ['test'])
        with tarfile.open(output/'image-input.tar', 'r:') as archive:
            source = [name.removeprefix(LAYOUT+'/source/') for name in archive.getnames()
                      if name.startswith(LAYOUT+'/source/')]
            self.assertEqual(sorted(source), expected_paths)
            self.assertEqual(json.load(archive.extractfile(LAYOUT+'/source.proof.json')), proof)
            self.assertEqual(json.load(archive.extractfile(LAYOUT+'/dependency-store/index.json')), {
                'kind': 'npm-no-install-v1', 'platform': PLATFORM,
                'lockSha256': plan['committedInputs']['package-lock.json']})
            self.assertFalse(any(name.startswith(LAYOUT+'/dependency-store/blobs') for name in archive.getnames()))
        self.assertTrue((checkout/'.npmrc').exists())

    def test_installed_source_check_draft_and_readiness_need_no_tarball_store(self):
        root, _, config, _, tree = self.create_fixture('source-check-readiness')
        paths = config['recipe']['sourcePaths']
        selection = ['--execution-kind', 'npm-source-check', '--test-script', 'test:unit']
        for path in paths:
            selection.extend(['--source-path', path])
        report = self.draft(root, config, *selection)
        self.assertEqual(report['status'], 'preparation-configuration-draft')
        draft = report['configurationDraft']
        self.assertEqual(draft['recipe']['stack'], 'npm-source-check')
        self.assertEqual(draft['source']['tree'], tree)
        self.assertNotIn('sourceManifestSha256', draft['source'])
        self.assertIsNone(draft['tarballsDir'])
        path = root/'readiness.json'

        def readiness(document):
            path.write_bytes(encode(document))
            return self.installed_cli(root, 'readiness', '--workflow', 'preparation', '--config', path,
                '--config-sha256', pin(path.read_bytes()), '--git-sha256', config['git']['sha256'], expected=3)

        incomplete = readiness(draft)
        self.assertEqual(incomplete['status'], 'configuration-incomplete')
        self.assertEqual(incomplete['source']['binding'], draft['source'])
        self.assertEqual(incomplete['dependencies']['status'], 'not-required-no-install')
        self.assertNotIn('tarballsDir', incomplete['unset'])
        self.assertNotIn('source.sourceManifestSha256', incomplete['unset'])
        complete = readiness({**config, 'version': 2, 'source': draft['source']})
        self.assertEqual(complete['status'], 'preview-available')
        self.assertEqual(complete['next']['command'], 'prepare-reviewed-build')
        self.assertEqual(complete['dependencies']['status'], 'not-required-no-install')
        self.assertFalse(complete['executionAuthorized'] or complete['ready'])
        self.assertFalse(Path(config['outputDir']).exists())
        self.assertFalse((root/'unexpected-dispatch').exists())

    def test_installed_preparation_pins_selected_playwright_in_recipe_review_and_plan(self):
        events, output, _, commit, tree = self.fixture('selected')
        readiness = events['selectedReadiness']
        self.assertEqual((readiness['code'], readiness['calls']), (3, []))
        self.assertEqual(readiness['stdout']['status'], 'preview-available')
        self.assertEqual(readiness['stdout']['source']['binding']['tree'], tree)
        self.assertFalse(readiness['stdout']['executionAuthorized'])
        self.assertEqual(events['approved']['code'], 0)
        self.assertEqual(events['selectedPreview']['calls'], [])
        self.assertEqual(events['selectedPreview']['code'], 0)
        self.assertEqual(events['prepCannotApproveBuild']['code'], 1)
        plan = json.loads((output/'plan.json').read_bytes())
        recipe = json.loads((output/'recipe/recipe.json').read_bytes())
        review = json.loads((output/'planning-review.json').read_bytes())
        self.assertEqual((plan['target']['commit'], plan['target']['tree']), (commit, tree))
        self.assertEqual(plan['scope']['requestedPaths'], ['index.html'])
        self.assertEqual(recipe['testSelector'], review['testSelector'])
        self.assertEqual(recipe['testSelector'], plan['testSelection']['selector'])
        self.assertEqual(recipe['testSelector'], events['selectedPreview']['stdout']['intent']['testSelector'])
        test = plan['operations'][1]
        self.assertEqual(test['discoveryArgv'], [*test['argv'], '--list'])
        self.assertFalse((output/'run-state').exists())

    def test_installed_preparation_binds_browser_profile_through_derived_image_plan(self):
        from aim_ci.recipe_plan import container_runtime_sha

        events, output, _, _, _ = self.fixture('selected-browser')
        self.assertEqual(events['approved']['code'], 0)
        expected = events['preview']['stdout']['intent']['base']['browserRuntime']
        profile = json.loads((output/'container-profile.json').read_bytes())
        plan = json.loads((output/'plan.json').read_bytes())
        policy = json.loads((output/'docker-policy.json').read_bytes())
        runtime_pin = container_runtime_sha(profile['tools'], expected)
        self.assertEqual(profile['browserRuntime'], expected)
        self.assertEqual(plan['containerProfile']['browserRuntime'], expected)
        self.assertEqual(plan['runtime']['sha256'], policy['runtimeSha256'])
        self.assertEqual(policy['runtimeSha256'], runtime_pin)
        preview = events['selectedPreview']
        self.assertEqual((preview['code'], preview['calls']), (0, []))
        self.assertEqual(preview['stdout']['intent']['browserInventory']['profile'], expected)
        self.assertFalse((output/'run-state').exists())

    def test_installed_preparation_refuses_stale_playwright_inputs_without_effects(self):
        for variant, expected in (
                ('source', 'lacks a regular committed file'), ('script', 'exact committed playwright test'),
                ('lock', 'supported lock'), ('scope', 'cannot allow edits')):
            with self.subTest(variant=variant):
                events, output, *_ = self.fixture('selected-invalid-' + variant)
                self.assertEqual((events['preview']['code'], events['preview']['calls']), (1, []))
                self.assertIn(expected, events['preview']['stderr'])
                self.assertFalse(output.exists())

    def test_installed_final_image_cleanup_refuses_changed_bindings_and_only_removes_exact_image(self):
        events, output, *_ = self.fixture('image-cleanup')
        preview = events['previewCleanup']
        self.assertEqual((preview['code'], preview['calls']), (0, []))
        self.assertEqual(preview['stdout']['journalState'], 'not-dispatched')
        self.assertFalse(preview['stdout']['dockerContacted'])
        self.assertEqual(preview['stdout']['imageRemoval']['argv'][-4:], ['image','rm','--no-prune',IMAGE])
        for label in ('wrongImage','unownedImage','previewWithoutImage','changedConfig','changedArchive','malformedBase'):
            with self.subTest(label=label):
                self.assertEqual((events[label]['code'], events[label]['calls']), (1, []))
        self.assertFalse(events['defaultCleanup']['stdout']['derivedImageRemoved'])
        for label in ('changedBase','changedBaseLabel','extraTag','extraDigest','extraLabel','changedLabel','inUse'):
            with self.subTest(label=label):
                self.assertEqual(events[label]['code'], 1)
                self.assertNotIn(['cli','rm'],events[label]['calls'])
        final = events['finalCleanup']
        self.assertEqual(final['code'], 0, final['stderr'])
        for label in ('extraLabel', 'changedLabel'):
            self.assertIn('Derived image does not match', events[label]['stderr'])
        self.assertTrue(final['stdout']['derivedImageRemoved'])
        self.assertEqual(final['stdout']['imageRemoval']['baseImage'],BASE)
        self.assertEqual(final['calls'].count(['cli','rm']),1)
        self.assertEqual(events['recheckPreview']['calls'],[])
        self.assertEqual(events['recheckCleanup']['code'],0)
        self.assertTrue(all(row[0]=='GET' for row in events['recheckCleanup']['calls']))
        journal=json.loads((output/'image-journal/effects.json').read_bytes())['operations']
        base = journal['prepare:commit']['intent']['base']
        expected_labels = {'org.example.base': 'inherited', **events['approved']['stdout']['image']['labels']}
        self.assertEqual(base['derivedLabelsSha256'], pin(encode(expected_labels)))
        self.assertEqual(journal['prepare:create']['intent']['baseImage'],BASE)
        self.assertEqual(journal['prepare:remove-image']['state'],'completed')

    def test_installed_final_image_cleanup_reconciles_only_confirmed_absence(self):
        for suffix, expected in (('absent', 0), ('present', 1)):
            with self.subTest(suffix=suffix):
                events, output, *_ = self.fixture('image-cleanup-lost-'+suffix)
                first = events['finalCleanup']
                self.assertEqual(first['code'],1)
                self.assertEqual(first['calls'].count(['cli','rm']),1)
                self.assertEqual(events['recheckPreview']['calls'],[])
                self.assertEqual(events['recheckPreview']['stdout']['journalState'],'unknown')
                retry = events['recheckCleanup']
                self.assertEqual(retry['code'],expected)
                self.assertTrue(all(row[0]=='GET' for row in retry['calls']))
                journal=json.loads((output/'image-journal/effects.json').read_bytes())['operations']
                entry=journal['prepare:remove-image']
                self.assertEqual(entry['state'],'completed' if expected==0 else 'unknown')
                self.assertEqual(entry.get('reconciled',False),expected==0)

    def test_stale_tarball_refuses_and_lists_missing_input_without_transport(self):
        events,output,*_=self.fixture('stale')
        self.assertFalse(output.exists())
        self.assertEqual(events['staleApproval']['code'],1)
        self.assertEqual(events['staleApproval']['calls'],[])
        self.assertEqual(events['missingPreview']['stdout']['missingTarballs'][0]['locator'],'node_modules/vite')

    def test_unsupported_stack_refuses_with_discovery_guidance_without_effects(self):
        events,output,*_=self.fixture('unsupported')
        self.assertFalse(output.exists())
        self.assertTrue(all(not row['calls'] and row['code']==1 for row in events.values()))
        self.assertIn('discover-project and propose-adaptation',events['preview']['stderr'])

    def test_optional_graph_refuses_before_preparation_or_docker(self):
        for mode, reason in (('optional-graph', 'no proven nonoptional path'),
                             ('optional-root-mismatch', 'root lock metadata disagree')):
            with self.subTest(mode=mode):
                events, output, *_ = self.fixture(mode)
                self.assertFalse(output.exists())
                self.assertTrue(all(not row['calls'] and row['code']==1 for row in events.values()))
                self.assertIn(reason, events['preview']['stderr'])

    def test_moved_head_after_preview_refuses_before_any_staging_effect(self):
        events,output,*_=self.fixture('stale-source')
        self.assertFalse(output.exists())
        self.assertEqual(events['preview']['code'],0)
        self.assertEqual(events['staleSourceApproval']['code'],1)
        self.assertEqual(events['staleSourceApproval']['calls'],[])
        self.assertIn('HEAD differs',events['staleSourceApproval']['stderr'])

    def test_lost_commit_reconciles_original_image_without_replay(self):
        events,output,*_=self.fixture('lost-commit')
        self.assertEqual(events['approved']['code'],1)
        self.assertEqual(events['resumed']['code'],0)
        self.assertTrue(all(row[0]=='GET' for row in events['resumed']['calls']))
        journal=json.loads((output/'image-journal/effects.json').read_bytes())['operations']
        self.assertTrue(journal['prepare:commit']['reconciled'])
        self.assertEqual(journal['prepare:remove']['receipt']['result']['containerId'],STAGE)

    def test_cleanup_withdraws_consent_while_active_before_next_effect(self):
        events, output, *_ = self.fixture('withdraw-active')
        intent = events['preview']['stdout']['intentSha256']
        pending = events['activeCleanup']
        self.assertEqual(pending['code'], 1)
        self.assertEqual(pending['stdout']['status'], 'cleanup-pending-active-controller')
        self.assertTrue(pending['stdout']['withdrawalRecorded'])
        self.assertEqual(pending['calls'], [])
        self.assertEqual(json.loads((output/'preparation-withdrawn.json').read_bytes()), {'intentSha256': intent})
        approved = events['approved']
        self.assertEqual(approved['code'], 1)
        self.assertIn('consent was withdrawn', approved['stderr'])
        self.assertEqual([row[1] for row in approved['calls'] if row[0]=='cli'], ['create'])
        self.assertEqual([row[1] for row in approved['calls'] if row[0]=='DELETE'],
                         [f'/v1.43/containers/{STAGE}?force=false&v=false'])
        self.assertEqual(events['finalCleanup']['code'], 0)
        self.assertEqual(events['finalCleanup']['stdout']['staging']['containerId'], STAGE)
        self.assertEqual(events['finalCleanup']['calls'], [])
        self.assertEqual(events['revokedResume']['code'], 1)
        self.assertEqual(events['revokedResume']['calls'], [])
        self.assertFalse((output/'run-config.json').exists())


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--fixture-driver':
        drive(sys.argv[2])
    else:
        unittest.main()
