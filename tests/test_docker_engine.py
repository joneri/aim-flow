"""Exercise Docker Engine HTTP and effect lifecycles without opening a socket."""
from base64 import b64decode, b64encode
from contextlib import contextmanager
import copy
import io
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci import control_archive, docker_engine
from aim_ci.npm_platform import PLATFORM_STORE
from aim_ci.platform_build import CONTAINER_HELPER, EVIDENCE_NAME, HELPER, platform_job_lock
from aim_ci.recipe_plan import _advance_docker_recipe, _execution_plan, check_recipe_execution
from openai_budget import Refused, fingerprint

CONTAINER = 'a' * 64
OTHER = 'b' * 64
CONTROL = 'c' * 64
DEPENDENCIES = 'd' * 64
IMAGE = 'sha256:' + 'e' * 64
ENDPOINT = 'unix:///reviewed/docker.sock'
API = '/v1.43'


class DockerEngineTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket', 'subprocess.Popen'):
            guard = patch(target, side_effect=AssertionError('Real transport or process forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.journal = self.root / 'journal'
        self.plan_path = self.root / 'plan.json'
        tools = {'node': {'path': '/usr/bin/node', 'sha256': '1' * 64},
                 'npm': {'path': '/usr/lib/npm-cli.js', 'sha256': '2' * 64}}
        self.runtime_pin = fingerprint(tools)
        self.plan = {
            'recipeSchemaVersion': 2, 'status': 'planned-only', 'executionAuthorized': False,
            'bindingSha256': '3' * 64, 'control': {'manifestSha256': CONTROL},
            'recipe': {'manifestSha256': '4' * 64}, 'target': {'commit': '5' * 40, 'tree': '6' * 40},
            'committedInputs': {'package.json': '7' * 64, 'package-lock.json': '8' * 64},
            'runtime': {'kind': 'reviewed-container-runtime', 'tools': tools, 'sha256': self.runtime_pin},
            'limits': {'network': 'disabled', 'timeoutSeconds': 2, 'memoryMiB': 128, 'maxProcesses': 4},
            'testSelection': {'script': 'test:unit', 'selectedBy': 'pinned-recipe'},
            'operations': [{'name': 'test', 'argv': ['/usr/bin/node', '/usr/lib/npm-cli.js',
                                                    '--ignore-scripts', 'run', 'test:unit'],
                            'cwd': 'isolated-candidate-root', 'status': 'not-run'}],
        }
        self.policy = {'version': 1, 'endpoint': ENDPOINT, 'imageDigest': IMAGE,
                       'adapterSha256': sha256(Path(docker_engine.__file__).read_bytes()).hexdigest(),
                       'runtimeSha256': self.runtime_pin, 'controlManifestSha256': CONTROL}
        self.pending = []
        self.now = 100.0
        self.payload = None
        self.platform = False
        self.name = None
        self.addCleanup(lambda: self.assertEqual(self.pending, []))

    def entry(self, name):
        return json.loads((self.journal / 'effects.json').read_bytes())['operations']['docker:' + name]

    def plan_args(self):
        data = json.dumps(self.plan).encode()
        self.plan_path.write_bytes(data)
        return self.plan_path, sha256(data).hexdigest(), CONTROL

    def advance(self, **changes):
        options = {'trusted_docker_policy': self.policy,
                   'trusted_docker_policy_pin': fingerprint(self.policy),
                   'dependency_store_sha256': DEPENDENCIES, 'journal_path': self.journal,
                   'request': self.request, 'clock': lambda: self.now}
        return _advance_docker_recipe(*self.plan_args(), **(options | changes))

    def expect(self, method, route, response):
        self.pending.append((method, route, response))

    def request(self, endpoint, method, route, body, timeout):
        self.assertEqual(endpoint, ENDPOINT)
        self.assertEqual(timeout, 5)
        self.assertTrue(self.pending, (method, route))
        expected_method, expected_route, response = self.pending.pop(0)
        self.assertEqual(method, expected_method)
        route = route.removeprefix(API)
        self.assertEqual(route, expected_route() if callable(expected_route) else expected_route)
        if callable(response):
            response = response(body)
        else:
            self.assertIsNone(body)
        if isinstance(response, Exception):
            raise response
        code, data = response
        return code, json.dumps(data).encode() if isinstance(data, dict) else data

    def queue_create(self, response=None, *, image=None):
        image_labels = {'org.aim-ci.source-commit': '5' * 40, 'org.aim-ci.source-tree': '6' * 40,
                        'org.aim-ci.runtime': self.runtime_pin, 'org.aim-ci.dependencies': DEPENDENCIES}
        image_data = {'Id': IMAGE, 'Config': {'Labels': image_labels}}
        if self.platform:
            image_data.update(Os='linux', Architecture='arm64')
            image_data['Config']['Env'] = ['PATH=/usr/local/bin:/usr/bin:/bin']
        self.expect('GET', '/images/' + IMAGE + '/json', image or (200, image_data))
        if image is not None:
            return

        def route():
            intent = self.entry('create')['intent']
            self.name = 'aim-ci-' + intent['effectId'][:24]
            return '/containers/create?name=' + self.name

        def create(body):
            self.assertEqual(self.entry('deadline')['receipt']['result']['expiresAt'], 102.0)
            entry = self.entry('create')
            self.assertEqual(entry['state'], 'pending')
            self.payload = json.loads(body)
            self.assertEqual(self.payload['Image'], IMAGE)
            if self.platform:
                self.assertEqual(self.payload['Entrypoint'], ['/usr/bin/node'])
                self.assertEqual(self.payload['Cmd'][0], CONTAINER_HELPER)
                self.assertEqual(len(self.payload['Cmd']), 2)
                parameters = json.loads(b64decode(self.payload['Cmd'][1]))
                self.assertEqual(parameters['effectId'], entry['intent']['effectId'])
                self.assertEqual(parameters['binding'], self.platform_binding)
                self.assertEqual(parameters['platform'], {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'})
                self.assertFalse(self.payload['Tty'])
                self.assertEqual(self.payload['Healthcheck'], {'Test': ['NONE']})
            else:
                self.assertEqual(self.payload['Cmd'], ['test', 'test:unit'])
                self.assertEqual(self.payload['Entrypoint'], ['/opt/aim-ci/run-recipe'])
            self.assertEqual(self.payload['Labels']['org.aim-ci.effect'], entry['intent']['effectId'])
            config = self.payload['HostConfig']
            self.assertEqual((config['NetworkMode'], config['Memory'], config['MemorySwap'], config['PidsLimit']),
                             ('none', 134217728, 134217728, 4))
            expected_tmpfs = 'rw,nosuid,nodev,exec,mode=1777' if self.platform else 'rw,nosuid,nodev,mode=1777'
            self.assertEqual(config['Tmpfs'], {'/work': expected_tmpfs})
            self.assertTrue(config['ReadonlyRootfs'])
            self.assertFalse(config['Privileged'])
            self.assertNotIn('Binds', config)
            self.assertNotIn('Mounts', config)
            return response or (201, {'Id': CONTAINER})

        self.expect('POST', route, create)

    def inspect(self, status, *, identifier=CONTAINER, by_name=False, changes=None):
        def respond(body):
            self.assertIsNone(body)
            if not by_name:
                self.assertEqual(self.entry('create')['receipt']['result']['containerId'], CONTAINER)
            if status == 'absent':
                return 404, b''
            config = {name: copy.deepcopy(self.payload[name]) for name in
                      ('Cmd', 'Entrypoint', 'Labels', 'User', 'WorkingDir')}
            value = {
                'Id': identifier, 'Name': '/' + self.name, 'Image': IMAGE, 'Config': config,
                'HostConfig': copy.deepcopy(self.payload['HostConfig']),
                'State': {'Status': status, 'Running': status == 'running',
                          'StartedAt': '2026-11-07T10:00:00.123456789Z',
                          'FinishedAt': '2026-11-07T10:00:01.123456789Z', 'ExitCode': 0, 'OOMKilled': False},
            }
            if self.platform:
                config.update({key: self.payload[key] for key in ('Tty', 'NetworkDisabled', 'Healthcheck')})
                value['Mounts'] = []
                value['State'].update(StartedAt='1970-01-01T00:01:40Z', FinishedAt='1970-01-01T00:01:41Z')
            if changes:
                changes(value)
            return 200, value

        self.expect('GET', lambda: f'/containers/{self.name if by_name else CONTAINER}/json', respond)

    def start(self, response=(204, b'')):
        self.inspect('created')

        def start(body):
            self.assertIsNone(body)
            self.assertEqual(self.entry('start')['state'], 'pending')
            return response

        self.expect('POST', '/containers/' + CONTAINER + '/start', start)

    def initial_running(self):
        self.queue_create()
        self.start()
        self.inspect('running')
        return self.advance()

    def remove(self, state='exited', response=(204, b''), *, confirmed=True):
        self.inspect(state)
        self.inspect(state)
        self.expect('DELETE', '/containers/' + CONTAINER + '?force=false&v=false', response)
        if confirmed:
            self.inspect('absent')

    def platform_fixture(self):
        self.platform = True
        self.plan['version'] = 2
        self.plan['runtime']['verification'] = 'declared-not-observed'
        self.plan['target']['checkout'] = str(self.root / 'checkout')
        self.plan['containerProfile'] = {'sha256': 'f' * 64, 'imageDigest': IMAGE,
                                         'dependencyStoreSha256': DEPENDENCIES,
                                         'verification': 'matched-separate-review-pin'}
        self.plan['artifact'] = {'directory': 'dist', 'maximumBytes': 100,
                                 'requiredFiles': ['index.html', 'assets/Fixture Font.woff2'],
                                 'linksAllowed': False, 'status': 'not-built'}
        for name, args in (('dependencies', ['ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund']),
                           ('build', ['--ignore-scripts', 'run', 'build'])):
            self.plan['operations'].append({'name': name, 'argv': ['/usr/bin/node', '/usr/lib/npm-cli.js', *args],
                                            'cwd': 'isolated-candidate-root', 'status': 'not-run'})
        _, self.platform_binding = _execution_plan(*self.plan_args())
        helper_sha = sha256((Path(__file__).resolve().parents[1] / HELPER).read_bytes()).hexdigest()
        guard = patch('aim_ci.platform_build.verify_installation', return_value={'files': {HELPER: helper_sha}})
        guard.start()
        self.addCleanup(guard.stop)
        self.evidence_path = self.root / 'image-evidence.json'
        before = {'containerId': OTHER, 'neverStarted': True}
        self.image_evidence = {
            'version': 1, 'status': 'bytes-match-reviewed-inputs', 'binding': self.platform_binding,
            'provenance': {'kind': 'trusted-local-daemon', 'imageOrigin': 'verified-by-trusted-readback',
                           'policySha256': fingerprint(self.policy), 'endpoint': ENDPOINT,
                           'imageInspect': {'imageId': IMAGE}, 'before': before, 'after': before,
                           'containerId': OTHER, 'cleanup': 'confirmed-absent',
                           'readPaths': {'entrypoint': CONTAINER_HELPER}},
            'measurements': {'entrypointSha256': helper_sha, 'tools': self.plan['runtime']['tools'],
                             'dependencyTarballs': {'kind': PLATFORM_STORE,
                                 'platform': {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'},
                                 'lockPackageCount': 3, 'packageCount': 2, 'tarballCount': 2,
                                 'excludedPackageCount': 1, 'fullLockVerified': False}},
        }
        self.log_reads = 0
        self.log_value = None

    def advance_platform(self):
        encoded = json.dumps(self.image_evidence).encode()
        self.evidence_path.write_bytes(encoded)
        return self.advance(platform_evidence=(self.evidence_path, sha256(encoded).hexdigest()),
                            response=self.platform_log)

    @contextmanager
    def platform_log(self, endpoint, method, route, body, timeout):
        self.assertEqual((endpoint, method, route, body, timeout),
                         (ENDPOINT, 'GET', API + '/containers/' + CONTAINER + '/logs?stdout=true&stderr=true',
                          None, 5))
        self.assertEqual(self.entry('terminal')['state'], 'completed')
        self.assertEqual(self.entry('build-evidence')['state'], 'pending')
        self.log_reads += 1
        raw = self.log_value if isinstance(self.log_value, bytes) else json.dumps(self.log_value).encode()
        parts = (raw[:18], raw[18:])
        framed = b''.join(b'\x01\0\0\0' + len(part).to_bytes(4, 'big') + part for part in parts if part)
        response = io.BytesIO(framed)
        response.status = 200
        response.getheader = lambda name: 'application/vnd.docker.raw-stream' if name == 'Content-Type' else None
        yield response

    def platform_terminal(self, exit_code=0, *, invalid=False, noexec=False,
                          finished_at='1970-01-01T00:01:41Z'):
        self.queue_create()
        self.start()
        self.inspect('running')
        self.assertEqual(self.advance_platform()['status'], 'running')
        for _ in range(2):
            self.inspect('exited', changes=lambda value: value['State'].update(ExitCode=exit_code,
                                                                            FinishedAt=finished_at))
        self.remove()
        package = {'exitCode': 0, 'signal': None, 'error': None, 'stdout': '', 'stderr': ''}
        artifact = lambda data: {'data': b64encode(data).decode(), 'sha256': sha256(data).hexdigest(), 'size': len(data)}
        files = {'index.html': artifact(b'hello'), 'assets/Fixture Font.woff2': artifact(b'font')}
        self.log_value = {
            'version': 1, 'effectId': self.entry('create')['intent']['effectId'], 'binding': self.platform_binding,
            'runtime': {'node': 'v22.23.3', 'npm': '10.9.9', 'cacache': '19.0.1', 'pacote': '19.0.2',
                        'contentVersionRaw': '2', 'contentVersion': 2, 'glibc': '2.36'},
            'workspaceMount': {'id': 9, 'parentId': 2, 'mountPoint': '/work', 'type': 'tmpfs',
                               'mountOptions': ['rw', 'nosuid', 'nodev'], 'superOptions': ['rw'],
                               'executable': True},
            'buildTools': {'tsc': {'entry': 'node_modules/.bin/tsc', 'linkTarget': '../typescript/bin/tsc',
                                   'resolvedPath': 'node_modules/typescript/bin/tsc', 'mode': 0o755,
                                   'uid': 65534, 'gid': 65534, 'executable': True}, 'vite': None},
            'materialized': 2, 'install': package, 'installedPackages': 2, 'error': None,
            'build': {**package, 'exitCode': 2 if exit_code else 0}, 'artifacts': None if exit_code else files,
        }
        if noexec:
            self.log_value['workspaceMount']['mountOptions'].append('noexec')
            self.log_value['workspaceMount']['executable'] = False
        if invalid:
            self.log_value = b'{"version":1,"version":2}'
        return self.advance_platform()

    def test_platform_build_captures_source_bound_artifact_bytes_before_exact_cleanup(self):
        self.platform_fixture()
        terminal = self.platform_terminal()
        evidence = terminal['platformBuild']
        self.assertEqual(evidence['status'], 'build-passed')
        self.assertTrue(evidence['workspaceMount']['executable'])
        self.assertEqual(evidence['buildTools']['tsc']['resolvedPath'], 'node_modules/typescript/bin/tsc')
        self.assertEqual(evidence['artifacts']['files']['assets/Fixture Font.woff2']['size'], 4)
        self.assertFalse(terminal['productAccepted'])
        self.assertEqual(terminal['containerCleanup'], 'confirmed-absent')
        self.assertEqual(self.entry('build-evidence')['state'], 'completed')
        self.assertEqual(sha256((self.journal / EVIDENCE_NAME).read_bytes()).hexdigest(), evidence['evidenceSha256'])
        self.assertEqual(self.advance_platform(), terminal)
        self.assertEqual(self.log_reads, 1)
        self.log_value['artifacts']['index.html']['data'] = b64encode(b'tamper').decode()
        (self.journal / EVIDENCE_NAME).write_text(json.dumps(self.log_value))
        with self.assertRaisesRegex(Refused, 'differs from the journal'):
            self.advance_platform()

    def test_platform_refuses_legacy_late_pass_without_rewriting_or_replaying(self):
        from aim_ci import platform_build

        self.platform_fixture()
        validate = platform_build.validate_report
        with patch.object(platform_build, 'validate_report',
                          side_effect=lambda raw, intent, final: validate(raw, intent, {**final, 'deadlineExceeded': False})):
            terminal = self.platform_terminal(finished_at='1970-01-01T00:01:43Z')
        self.assertEqual(terminal['platformBuild']['status'], 'build-passed')
        self.assertNotIn('deadlineExceeded', self.entry('build-evidence')['intent']['terminal'])
        journal = self.journal / 'effects.json'
        before = journal.read_bytes()
        raw = (self.journal / EVIDENCE_NAME).read_bytes()
        with self.assertRaisesRegex(Refused, 'differs from the journal'):
            self.advance_platform()
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual((self.journal / EVIDENCE_NAME).read_bytes(), raw)
        self.assertEqual(self.log_reads, 1)

    def test_platform_failed_build_preserves_report_without_artifact_pass(self):
        self.platform_fixture()
        terminal = self.platform_terminal(exit_code=1)
        self.assertEqual(terminal['platformBuild']['status'], 'build-failed')
        self.assertEqual(terminal['platformBuild']['build']['exitCode'], 2)
        self.assertIsNone(terminal['platformBuild']['artifacts'])
        self.assertTrue((self.journal / EVIDENCE_NAME).is_file())
        self.assertEqual(terminal['containerCleanup'], 'confirmed-absent')

    def test_platform_invalid_report_cannot_skip_cleanup_or_supply_success(self):
        self.platform_fixture()
        terminal = self.platform_terminal(invalid=True)
        self.assertEqual(terminal['platformBuild']['status'], 'unverified')
        self.assertIn('duplicate-key', terminal['platformBuild']['reason'])
        self.assertEqual((self.journal / EVIDENCE_NAME).read_bytes(), self.log_value)
        self.assertEqual(terminal['platformBuild']['evidenceSha256'], sha256(self.log_value).hexdigest())
        self.assertEqual(terminal['containerCleanup'], 'confirmed-absent')
        self.assertEqual(self.advance_platform(), terminal)
        self.assertEqual(self.log_reads, 1)

    def test_platform_report_cannot_claim_an_install_under_observed_noexec(self):
        self.platform_fixture()
        terminal = self.platform_terminal(noexec=True)
        self.assertEqual(terminal['platformBuild']['status'], 'unverified')
        self.assertEqual(terminal['containerCleanup'], 'confirmed-absent')

    def test_platform_store_declaration_must_match_observed_image_architecture(self):
        self.platform_fixture()
        image = {'Id': IMAGE, 'Os': 'linux', 'Architecture': 'amd64', 'Config': {
            'Labels': docker_engine.recipe_image_labels(self.platform_binding, DEPENDENCIES),
            'Env': ['PATH=/usr/bin'],
        }}
        self.queue_create(image=(200, image))
        with self.assertRaisesRegex(Refused, 'OS or CPU'):
            self.advance_platform()
        self.assertEqual(self.entry('create')['state'], 'unknown')

    def test_platform_rejects_unpinned_operations_and_synthetic_image_evidence(self):
        self.platform_fixture()
        self.plan['operations'][-1]['argv'].append('--unreviewed')
        with self.assertRaisesRegex(Refused, 'install/build argv'):
            self.advance_platform()
        self.plan['operations'][-1]['argv'].pop()
        self.image_evidence['provenance']['kind'] = 'synthetic-protocol'
        with self.assertRaisesRegex(Refused, 'trusted image byte evidence'):
            self.advance_platform()
        self.assertFalse(self.journal.exists())

    def test_platform_journal_lock_prevents_concurrent_capture_and_cleanup(self):
        self.platform_fixture()
        with platform_job_lock(self.journal, self.plan['target']['checkout']):
            with self.assertRaisesRegex(Refused, 'already has an active controller'):
                self.advance_platform()

    def test_create_start_terminal_and_cleanup_are_separate_durable_records(self):
        running = self.initial_running()
        self.assertEqual((running['status'], running['loopback'], running['resourceEnforcement']),
                         ('running', 'unqualified', 'unverified'))
        self.inspect('exited', changes=lambda value: value['State'].update(ExitCode=7))
        self.remove()
        terminal = self.advance()
        self.assertEqual((terminal['status'], terminal['exitCode'], terminal['oom'], terminal['containerCleanup']),
                         ('exited', 7, False, 'confirmed-absent'))
        self.assertEqual(self.entry('terminal')['receipt']['result']['containerId'], CONTAINER)
        self.assertEqual(self.advance(), terminal)

    def test_create_reply_loss_reconciles_matching_name_once_then_uses_full_id(self):
        self.queue_create(TimeoutError())
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            self.advance()
        self.assertEqual(self.entry('create')['state'], 'unknown')
        self.inspect('absent', by_name=True)
        with self.assertRaisesRegex(Refused, 'do not resend'):
            self.advance()
        self.inspect('created', by_name=True)
        self.start()
        self.inspect('running')
        self.assertEqual(self.advance()['containerId'], CONTAINER)
        self.assertTrue(self.entry('create')['reconciled'])

    def test_lost_start_reply_reconciles_running_and_never_replays_start(self):
        self.queue_create()
        self.start(TimeoutError())
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            self.advance()
        self.inspect('running')
        self.inspect('running')
        self.assertEqual(self.advance()['status'], 'running')
        self.assertTrue(self.entry('start')['reconciled'])

    def test_created_after_unknown_start_and_deadline_preserves_unknown_cleanup(self):
        self.queue_create()
        self.start(TimeoutError())
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            self.advance()
        self.now = 103.0
        self.inspect('created')
        result = self.advance()
        self.assertEqual((result['status'], result['containerCleanup']), ('start-outcome-unknown', 'unknown'))
        self.assertEqual(self.entry('start')['state'], 'unknown')

    def test_deadline_before_start_skips_start_and_confirms_only_container_absence(self):
        self.queue_create(TimeoutError())
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            self.advance()
        self.now = 103.0
        self.inspect('created', by_name=True)
        self.inspect('created')
        self.remove('created')
        result = self.advance()
        self.assertEqual((result['status'], result['containerCleanup']),
                         ('deadline-before-start', 'confirmed-absent'))
        self.assertEqual(self.advance(), result)

    def test_different_valid_id_is_rejected_even_with_matching_labels(self):
        self.queue_create()
        self.inspect('created', identifier=OTHER)
        with self.assertRaisesRegex(Refused, 'different or invalid container ID'):
            self.advance()
        self.assertEqual(self.entry('create')['receipt']['result']['containerId'], CONTAINER)
        self.inspect('running', identifier=OTHER)
        with self.assertRaisesRegex(Refused, 'different or invalid container ID'):
            self.advance()

    def test_deadline_kill_and_removal_reply_loss_require_independent_observation(self):
        self.initial_running()
        self.now = 103.0
        self.inspect('running')
        self.inspect('running')
        self.expect('POST', '/containers/' + CONTAINER + '/kill?signal=SIGKILL', TimeoutError())
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            self.advance()
        self.inspect('running')
        self.inspect('running')
        with self.assertRaisesRegex(Refused, 'do not resend'):
            self.advance()
        self.assertEqual(self.entry('kill')['state'], 'unknown')
        self.inspect('exited')
        self.inspect('exited', changes=lambda value: value['State'].update(ExitCode=137, OOMKilled=True))
        self.remove(response=TimeoutError(), confirmed=False)
        with self.assertRaisesRegex(Refused, 'outcome is unknown'):
            self.advance()
        self.assertTrue(self.entry('kill')['reconciled'])
        self.assertEqual(self.entry('remove')['state'], 'unknown')
        self.inspect('absent')
        terminal = self.advance()
        self.assertEqual((terminal['deadlineTerminationAttempted'], terminal['exitCode'], terminal['oom'],
                          terminal['containerCleanup']), (True, 137, True, 'confirmed-absent'))
        self.assertEqual(self.advance(), terminal)

    def test_deadline_kill_ack_without_terminal_state_does_not_remove(self):
        self.initial_running()
        self.now = 103.0
        self.inspect('running')
        self.inspect('running')
        self.expect('POST', '/containers/' + CONTAINER + '/kill?signal=SIGKILL', (204, b''))
        self.inspect('running')
        with self.assertRaisesRegex(Refused, 'required container state'):
            self.advance()
        self.assertEqual(self.entry('kill')['state'], 'unknown')

    def test_removal_ack_without_absence_does_not_claim_cleanup(self):
        self.initial_running()
        self.inspect('exited')
        self.remove(confirmed=False)
        self.inspect('exited')
        with self.assertRaisesRegex(Refused, 'no confirmed absence'):
            self.advance()
        self.assertEqual(self.entry('remove')['state'], 'unknown')
        self.inspect('exited')
        with self.assertRaisesRegex(Refused, 'do not resend'):
            self.advance()

    def test_malformed_create_and_cross_bound_reconciliation_fail_closed(self):
        self.queue_create((201, b'not-json'))
        with self.assertRaisesRegex(Refused, 'Malformed Docker JSON'):
            self.advance()
        self.inspect('created', by_name=True,
                     changes=lambda value: value['Config']['Labels'].update({'org.aim-ci.policy': OTHER}))
        with self.assertRaisesRegex(Refused, 'does not match the journaled recipe'):
            self.advance()
        self.assertEqual(self.entry('create')['state'], 'unknown')

    def test_malformed_terminal_cannot_be_removed(self):
        self.initial_running()
        self.inspect('exited', changes=lambda value: value['State'].update(ExitCode=True))
        with self.assertRaisesRegex(Refused, 'has not confirmed a terminal outcome'):
            self.advance()

    def test_private_memory_minimum_is_checked_before_journal_and_transport(self):
        request = Mock(side_effect=RuntimeError('Transport sentinel'))
        for memory in (96, 127):
            self.plan['limits']['memoryMiB'] = memory
            with self.subTest(memory=memory), self.assertRaisesRegex(Refused, 'Invalid planned resource'):
                self.advance(request=request)
            request.assert_not_called()
            self.assertFalse(self.journal.exists())

        self.plan['limits']['memoryMiB'] = 128
        with self.assertRaisesRegex(RuntimeError, '^Transport sentinel$'):
            self.advance(request=request)
        request.assert_called_once_with(ENDPOINT, 'GET', API + '/images/' + IMAGE + '/json', None, 5)
        controls = self.entry('create')['intent']['requestedControls']
        self.assertEqual((controls['memoryBytes'], controls['memoryAndSwapBytes']), (134217728, 134217728))

    def test_image_with_different_source_tree_is_refused_before_container_create(self):
        labels = {'org.aim-ci.source-commit': self.plan['target']['commit'],
                  'org.aim-ci.source-tree': '9' * 40, 'org.aim-ci.runtime': self.runtime_pin,
                  'org.aim-ci.dependencies': DEPENDENCIES}
        self.queue_create(image=(200, {'Id': IMAGE, 'Config': {'Labels': labels}}))
        with self.assertRaisesRegex(Refused, 'image does not match the reviewed job inputs'):
            self.advance()
        self.assertEqual(self.entry('create')['state'], 'unknown')
        self.assertIsNone(self.payload)

    def test_policy_runtime_plan_and_image_are_bound_before_create(self):
        policy = {**self.policy, 'adapterSha256': OTHER}
        with self.assertRaisesRegex(Refused, 'reviewed adapter'):
            self.advance(trusted_docker_policy=policy, trusted_docker_policy_pin=fingerprint(policy))
        with self.assertRaisesRegex(Refused, 'policy identity or pin'):
            self.advance(trusted_docker_policy_pin=OTHER)
        self.plan['runtime']['kind'] = 'host-runtime'
        with self.assertRaisesRegex(Refused, 'container runtime'):
            self.advance()
        self.plan['runtime']['kind'] = 'reviewed-container-runtime'
        self.plan['testSelection']['script'] = 'test; other'
        with self.assertRaisesRegex(Refused, 'literal test script'):
            self.advance()
        self.plan['testSelection']['script'] = 'test:unit'
        self.plan['operations'][0]['argv'][-1] = 'test:other'
        with self.assertRaisesRegex(Refused, 'test argv'):
            self.advance()
        self.plan['operations'][0]['argv'][-1] = 'test:unit'
        self.queue_create(image=(200, {'Id': IMAGE, 'Config': {'Labels': {}, 'Volumes': {'/host': {}}}}))
        with self.assertRaisesRegex(Refused, 'image does not match'):
            self.advance()
        with self.assertRaisesRegex(Refused, 'Effect scope/profile/mode changed'):
            self.advance(dependency_store_sha256=OTHER)

    def test_public_readiness_stays_blocked_and_archive_accepts_prior_allowlists(self):
        result = check_recipe_execution(*self.plan_args())
        self.assertEqual(result['status'], 'blocked-before-dispatch')
        self.assertFalse(result['processStarted'])
        self.assertFalse(self.journal.exists())
        self.assertIn('lib/aim_ci/docker_engine.py', control_archive.FILES)
        self.assertIn('lib/aim_ci/docker_image_evidence.py', control_archive.FILES)
        for files in (control_archive.FILES, control_archive.DOCKER_FILES,
                      control_archive.SOURCE_FREE_FILES, control_archive.LEGACY_FILES):
            with self.subTest(files=len(files)):
                value = {'format': 'aim-ci-control', 'schemaVersion': 1, 'version': '0.11.0-local.1',
                         'files': {name: CONTROL for name in files}}
                self.assertEqual(control_archive.parse_manifest(json.dumps(value)), value)

    def test_unix_http_uses_only_the_pinned_socket(self):
        simulated_socket = Mock()
        response = Mock(status=204)
        response.read.return_value = b''
        with patch.object(docker_engine.socket, 'socket', return_value=simulated_socket) as factory, \
             patch.object(docker_engine._UnixConnection, 'getresponse', return_value=response):
            self.assertEqual(docker_engine.unix_request(ENDPOINT, 'POST', API + '/test', b'{}', 5), (204, b''))
        factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        simulated_socket.connect.assert_called_once_with('/reviewed/docker.sock')
        simulated_socket.settimeout.assert_called_once_with(5)
        self.assertIn(b'POST /v1.43/test HTTP/1.1', simulated_socket.sendall.call_args_list[0].args[0])
        self.assertIn(b'Content-Type: application/json', simulated_socket.sendall.call_args_list[0].args[0])
        self.assertEqual(simulated_socket.sendall.call_args_list[1].args[0], b'{}')
        simulated_socket.close.assert_called_once()
        with self.assertRaisesRegex(Refused, 'literal Unix socket'):
            docker_engine.unix_request('tcp://elsewhere:2375', 'POST', API + '/test', None, 5)
