"""Internal Docker Engine HTTP transport and journaled container lifecycle."""
from contextlib import contextmanager
from datetime import datetime
import http.client
import json
import re
import socket
from time import monotonic
from urllib.parse import urlencode

from openai_budget import Refused, fingerprint

_API = '/v1.43'
_MAX_RESPONSE = 1_000_000
_ID = re.compile(r'[a-f0-9]{64}')
_ENTRYPOINT = ['/opt/aim-ci/run-recipe']
PLATFORM_WORKSPACE_OPTIONS = 'rw,nosuid,nodev,exec,mode=1777'


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__('localhost', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


@contextmanager
def unix_response(endpoint, method, route, body, timeout):
    """Raw HTTP over the reviewed Unix socket; no environment or TCP fallback."""
    if (not re.fullmatch(r'unix:///[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.sock', endpoint)
            or any(part in ('.', '..') for part in endpoint.removeprefix('unix://').split('/'))):
        raise Refused('Docker transport requires a literal Unix socket path')
    connection = _UnixConnection(endpoint.removeprefix('unix://'), timeout)
    try:
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        connection.request(method, route, body, headers)
        yield connection.getresponse()
    finally:
        connection.close()


def unix_request(endpoint, method, route, body, timeout):
    with unix_response(endpoint, method, route, body, timeout) as response:
        return response.status, response.read(_MAX_RESPONSE + 1)


def guarded_request(before_effect):
    containers = _API + '/containers/'

    def request(endpoint, method, route, body, timeout):
        suffix = route.removeprefix(containers)
        cleanup = route.startswith(containers) and (
            method == 'DELETE' and re.fullmatch(r'[a-f0-9]{64}\?force=false&v=false', suffix)
            or method == 'POST' and re.fullmatch(r'[a-f0-9]{64}/kill\?signal=SIGKILL', suffix))
        if method != 'GET' and not cleanup:
            before_effect()
        return unix_request(endpoint, method, route, body, timeout)

    return request


def _timestamp(value):
    if not isinstance(value, str):
        raise Refused('Malformed Docker state timestamp')
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise Refused('Malformed Docker state timestamp') from None
    if result.tzinfo is None:
        raise Refused('Docker state timestamp must include its timezone')
    return result


def _container_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise Refused('Docker operation requires a full container ID')
    return value


def _unique_object(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('Duplicate Docker response key')
        value[key] = item
    return value


def recipe_image_labels(binding, dependency_store_sha256):
    return {
        'org.aim-ci.source-commit': binding['sourceCommit'],
        'org.aim-ci.source-tree': binding['sourceTree'],
        'org.aim-ci.runtime': binding['runtimeSha256'],
        'org.aim-ci.dependencies': dependency_store_sha256,
    }


class DockerEngine:
    def __init__(self, endpoint, request, response=None):
        self.endpoint = endpoint
        self.request = request
        self.response = response

    def _call(self, method, route, expected, payload=None):
        body = None if payload is None else json.dumps(payload, separators=(',', ':')).encode()
        try:
            code, data = self.request(self.endpoint, method, _API + route, body, 5)
        except (OSError, http.client.HTTPException) as error:
            raise Refused(f'Docker {method} outcome is unknown ({type(error).__name__})') from None
        if type(code) is not int or code not in expected:
            raise Refused(f'Docker {method} did not confirm the operation')
        if not isinstance(data, bytes) or len(data) > _MAX_RESPONSE:
            raise Refused('Malformed or oversized Docker response')
        if code == 404:
            return None
        if code == 204:
            if data:
                raise Refused('Unexpected body in Docker no-content response')
            return None
        try:
            value = json.loads(data, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise Refused('Malformed Docker JSON response') from None
        if not isinstance(value, dict):
            raise Refused('Invalid Docker response object')
        return value

    def check_image(self, image_id, expected_labels, *, platform=None):
        image = self._call('GET', f'/images/{image_id}/json', (200,))
        config = image.get('Config')
        labels = config.get('Labels') if isinstance(config, dict) else None
        if (image.get('Id') != image_id or not isinstance(labels, dict)
                or config.get('Volumes') or any(labels.get(k) != v for k, v in expected_labels.items())):
            raise Refused('Local Docker image does not match the reviewed job inputs')
        if platform is not None:
            if image.get('Os') != platform['os'] or image.get('Architecture') != {
                    'x64': 'amd64', 'arm64': 'arm64'}[platform['cpu']]:
                raise Refused('Docker image OS or CPU differs from the reviewed dependency platform')
            env = config.get('Env')
            allowed = {'PATH', 'NODE_VERSION', 'YARN_VERSION'}
            if (not isinstance(env, list) or len(env) > len(allowed)
                    or any(not isinstance(item, str) or '=' not in item
                           or item.split('=', 1)[0] not in allowed for item in env)
                    or len({item.split('=', 1)[0] for item in env}) != len(env)):
                raise Refused('Platform image environment is outside the reviewed runtime')
        result = {'imageId': image['Id'], 'labels': {key: labels[key] for key in expected_labels},
                  'declaredVolumes': []}
        if platform is not None:
            result['platform'] = {'os': image['Os'], 'architecture': image['Architecture']}
        return result

    def create(self, job):
        platform = job.intent['platformBuild']['store']['platform'] if job.platform_build else None
        self.check_image(job.payload['Image'], job.image_labels, platform=platform)
        response = self._call('POST', '/containers/create?' + urlencode({'name': job.name}), (201,), job.payload)
        return {'containerId': _container_id(response.get('Id'))}

    def inspect(self, job, identifier=None):
        selector = job.name if identifier is None else _container_id(identifier)
        value = self._call('GET', f'/containers/{selector}/json', (200, 404))
        if value is None:
            return None
        observed_id = value.get('Id')
        if (not isinstance(observed_id, str) or not _ID.fullmatch(observed_id)
                or (identifier is not None and observed_id != identifier)):
            raise Refused('Docker inspect returned a different or invalid container ID')
        config = value.get('Config')
        labels = config.get('Labels') if isinstance(config, dict) else None
        host = value.get('HostConfig')
        restart = host.get('RestartPolicy') if isinstance(host, dict) else None
        requested = job.payload['HostConfig']
        if (value.get('Name') != '/' + job.name or value.get('Image') != job.payload['Image']
                or not isinstance(labels, dict) or any(labels.get(k) != v for k, v in job.labels.items())
                or any(config.get(key) != job.payload[key]
                       for key in ('Entrypoint', 'Cmd', 'User', 'WorkingDir'))
                or config.get('Volumes') or not isinstance(host, dict)
                or host.get('Binds') or host.get('Mounts') or host.get('VolumesFrom')
                or any(type(host.get(key)) is not type(requested[key]) or host[key] != requested[key] for key in (
                    'NetworkMode', 'ReadonlyRootfs', 'Privileged', 'Memory', 'MemorySwap',
                    'PidsLimit', 'AutoRemove', 'CapDrop', 'SecurityOpt', 'Tmpfs'))
                or not isinstance(restart, dict) or restart.get('Name') != 'no'):
            raise Refused('Docker container does not match the journaled recipe binding')
        if job.platform_build:
            mounts = value.get('Mounts')
            if (config.get('Tty') is not False or config.get('NetworkDisabled') is not True
                    or config.get('Healthcheck') != job.payload['Healthcheck']
                    or host.get('LogConfig') != requested['LogConfig']
                    or any(host.get(key) for key in ('Devices', 'DeviceRequests', 'PortBindings', 'PublishAllPorts'))
                    or not isinstance(mounts, list) or len(mounts) > 1
                    or any(not isinstance(mount, dict) or mount.get('Type') != 'tmpfs'
                           or mount.get('Destination') != '/work' or mount.get('RW') is not True for mount in mounts)):
                raise Refused('Docker platform build workspace, port or evidence settings differ')
            if 'browserNetwork' in job.intent['requestedControls'] and any(host.get(key) for key in
                    ('Dns', 'DnsOptions', 'DnsSearch', 'ExtraHosts', 'Links')):
                raise Refused('Docker browser job has unexpected host connectivity settings')
        state = value.get('State')
        if not isinstance(state, dict) or state.get('Status') not in ('created', 'running', 'exited'):
            raise Refused('Docker container state is unknown or unsupported')
        status = state['Status']
        if state.get('Running') is not (status == 'running'):
            raise Refused('Docker status and running flag disagree')
        result = {'containerId': observed_id, 'state': status}
        if status != 'created':
            started = _timestamp(state.get('StartedAt'))
            if started.year == 1:
                raise Refused('Docker has not confirmed a start time')
            result['startedAt'] = state['StartedAt']
        if status == 'exited':
            finished = _timestamp(state.get('FinishedAt'))
            exit_code, oom = state.get('ExitCode'), state.get('OOMKilled')
            if finished < started or type(exit_code) is not int or not 0 <= exit_code <= 255 or type(oom) is not bool:
                raise Refused('Docker has not confirmed a terminal outcome')
            result.update(finishedAt=state['FinishedAt'], exitCode=exit_code, oom=oom)
        return result

    def build_log(self, identifier, limit):
        """Read only bounded, multiplexed stdout from the exact terminal container."""
        route = f'/containers/{_container_id(identifier)}/logs?stdout=true&stderr=true'
        if self.response is None:
            raise Refused('Platform build requires the reviewed streaming log transport')
        output = bytearray()
        frames = 0
        deadline = monotonic() + 30
        try:
            with self.response(self.endpoint, 'GET', _API + route, None, 5) as response:
                if (response.status != 200 or response.getheader('Content-Type') not in
                        ('application/vnd.docker.raw-stream', 'application/vnd.docker.multiplexed-stream')
                        or response.getheader('Content-Encoding') not in (None, 'identity')):
                    raise Refused('Docker did not return an uncompressed build log stream')
                read = getattr(response, 'read1', response.read)

                def exact(size, *, eof=False):
                    data = bytearray()
                    while len(data) < size:
                        if monotonic() >= deadline:
                            raise Refused('Docker build log exceeded the read deadline')
                        chunk = read(min(65536, size - len(data)))
                        if not chunk:
                            if eof and not data:
                                return None
                            raise Refused('Truncated Docker build log frame')
                        data.extend(chunk)
                    return data

                while (header := exact(8, eof=True)) is not None:
                    frames += 1
                    size = int.from_bytes(header[4:], 'big')
                    if header[:4] != b'\x01\0\0\0' or not 0 < size <= limit - len(output) or frames > 8192:
                        raise Refused('Invalid or oversized Docker build log frame')
                    output.extend(exact(size))
        except (OSError, http.client.HTTPException) as error:
            raise Refused(f'Docker build log outcome is unknown ({type(error).__name__})') from None
        if not output:
            raise Refused('Docker returned no platform build evidence')
        return bytes(output)

    def start(self, identifier):
        self._call('POST', f'/containers/{_container_id(identifier)}/start', (204,))

    def kill(self, identifier):
        self._call('POST', f'/containers/{_container_id(identifier)}/kill?signal=SIGKILL', (204,))

    def remove(self, identifier):
        self._call('DELETE', f'/containers/{_container_id(identifier)}?force=false&v=false', (204, 404))


class DockerRecipeJob:
    def __init__(self, journal, engine, intent, image, test_script, clock):
        self.journal = journal
        self.engine = engine
        self.intent = intent
        self.name = intent['containerName']
        self.clock = clock
        self.labels = {
            'org.aim-ci.effect': intent['effectId'],
            'org.aim-ci.policy': intent['dockerPolicySha256'],
        }
        binding = intent['binding']
        self.image_labels = recipe_image_labels(binding, intent['dependencyStoreSha256'])
        controls = intent['requestedControls']
        self.platform_build = 'platformBuild' in intent
        self.payload = {
            'Image': image, 'Entrypoint': _ENTRYPOINT, 'Cmd': ['test', test_script],
            'WorkingDir': '/work', 'User': '65534:65534', 'Labels': self.labels,
            'NetworkDisabled': True, 'Tty': False,
            'HostConfig': {
                'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                'Memory': controls['memoryBytes'], 'MemorySwap': controls['memoryAndSwapBytes'],
                'PidsLimit': controls['processCount'], 'AutoRemove': False,
                'RestartPolicy': {'Name': 'no'},
                'Tmpfs': {'/work': 'rw,nosuid,nodev,mode=1777'},
            },
        }
        if self.platform_build:
            from .platform_build import runner_arguments
            from .recipe_plan import BROWSER_NETWORK

            review = intent['platformBuild']
            browser = review.get('browserRuntime') if 'testSelector' in review else None
            if (browser is not None and (controls.get('browserNetwork') != BROWSER_NETWORK
                                        or controls.get('loopback') != BROWSER_NETWORK['loopback']
                                        or controls.get('networkMode') != BROWSER_NETWORK['mode'])
                    or browser is None and 'browserNetwork' in controls):
                raise Refused('Docker job differs from the reviewed browser connectivity policy')
            self.payload.update(Entrypoint=[intent['platformBuild']['tools']['node']['path']],
                                Cmd=runner_arguments(intent), Healthcheck={'Test': ['NONE']})
            self.payload['HostConfig']['Tmpfs']['/work'] = PLATFORM_WORKSPACE_OPTIONS
            self.payload['HostConfig']['LogConfig'] = {
                'Type': 'json-file', 'Config': {'max-size': '36m', 'max-file': '1'},
            }

    def _effect(self, name, intent, action, observe=None):
        def receipt(key, request, value):
            return self.journal.receipt(key, fingerprint(request), value)

        def lookup(key, request):
            value = observe()
            if value is None:
                return {'state': 'unknown'}
            return {'state': 'completed', 'receipt': receipt(key, request, value)}

        return self.journal.perform('docker:' + name, intent,
                                    lambda key, request: receipt(key, request, action()),
                                    lookup=lookup if observe is not None else None)

    def _require_state(self, identifier, expected):
        state = self.engine.inspect(self, identifier)
        if state is None or state['state'] not in expected:
            raise Refused('Docker did not confirm the required container state')
        return state

    def _created(self):
        observed = self.engine.inspect(self)
        return {'containerId': observed['containerId']} if observed and observed['state'] == 'created' else None

    def _start(self, identifier, deadline):
        self._require_state(identifier, ('created',))
        if self.clock() >= deadline:
            return {'containerId': identifier, 'processStarted': False}
        self.engine.start(identifier)
        return {'containerId': identifier, 'processStarted': True}

    def _observe_started(self, identifier):
        observed = self.engine.inspect(self, identifier)
        if observed is not None and observed['state'] in ('running', 'exited'):
            return {'containerId': identifier, 'processStarted': True}
        return None

    def _terminal(self, identifier):
        observed = self.engine.inspect(self, identifier)
        return observed if observed is not None and observed['state'] == 'exited' else None

    def _terminate(self, identifier):
        observed = self._require_state(identifier, ('running', 'exited'))
        if observed['state'] == 'exited':
            return observed
        self.engine.kill(identifier)
        return self._require_state(identifier, ('exited',))

    def _absent(self, identifier):
        if self.engine.inspect(self, identifier) is None:
            return {'containerCleanup': 'confirmed-absent'}
        return None

    def _remove(self, identifier):
        if self._absent(identifier) is None:
            self._require_state(identifier, ('created', 'exited'))
            self.engine.remove(identifier)
        absent = self._absent(identifier)
        if absent is None:
            raise Refused('Docker removal has no confirmed absence')
        return absent

    def _build_evidence(self, bound, final):
        from .platform_build import report_limit, capture_report, reconcile_report

        identifier = bound['containerId']
        directory = self.journal.directory
        checked = {**final, 'deadlineExceeded': _timestamp(final.get('finishedAt')).timestamp() >= bound['expiresAt']}
        # Keep the original effect identity for both report modes.
        evidence_terminal = checked if 'testOperation' in self.intent['platformBuild'] else final

        def capture():
            try:
                self._require_state(identifier, ('exited',))
                return capture_report(directory, lambda: self.engine.build_log(identifier, report_limit(self.intent)),
                                      self.intent, checked)
            except (Refused, OSError) as error:
                return {'status': 'unverified', 'reason': str(error)}

        def reconcile():
            try:
                retained = reconcile_report(directory, self.intent, checked)
            except (Refused, OSError):
                retained = None
            return retained if retained is not None else capture()

        result = self._effect('build-evidence', {**bound, 'terminal': evidence_terminal}, capture, reconcile)
        if 'evidenceSha256' in result and reconcile_report(directory, self.intent, checked) != result:
            raise Refused('Retained platform build evidence is missing or differs from the journal')
        return result

    def cancel(self):
        """Reconcile and remove only the already journaled job, without create or start."""
        record = self.journal.inspect('docker:create')
        if record is None:
            return {'containerCleanup': 'not-needed'}

        def forbidden():
            raise Refused('Recovery cannot create a container')

        created = self._effect('create', self.intent, forbidden, self._created)
        identifier = _container_id(created['containerId'])
        deadline = self.journal.inspect('docker:deadline')['receipt']['result']['expiresAt']
        bound = {'containerId': identifier, 'effectId': self.intent['effectId'], 'expiresAt': deadline}
        previous = self.journal.inspect('docker:remove')
        if previous is None:
            observed = self.engine.inspect(self, identifier)
            if observed is not None and observed['state'] == 'running':
                self._effect('kill', bound, lambda: self._terminate(identifier), lambda: self._terminal(identifier))
        return {'containerId': identifier, **self._effect(
            'remove', bound, lambda: self._remove(identifier), lambda: self._absent(identifier))}

    def advance(self):
        """Advance or reconcile one job; the caller must observe it through termination."""
        deadline = self._effect('deadline', self.intent, lambda: {
            'expiresAt': self.clock() + self.intent['requestedControls']['wholeContainerDeadlineSeconds'],
        })['expiresAt']
        if type(deadline) not in (int, float) or not 0 < deadline < float('inf'):
            raise Refused('Docker deadline is unknown or invalid')
        if self.clock() >= deadline and not self.journal.inspect('docker:create'):
            raise Refused('Docker deadline elapsed before create')
        created = self._effect('create', self.intent, lambda: self.engine.create(self), self._created)
        identifier = _container_id(created['containerId'])
        bound = {'containerId': identifier, 'effectId': self.intent['effectId'], 'expiresAt': deadline}
        result = {**bound, 'loopback': 'unqualified', 'resourceEnforcement': 'unverified'}
        start_record = self.journal.inspect('docker:start')
        if start_record and start_record['state'] != 'completed' and self.clock() >= deadline:
            observed = self.engine.inspect(self, identifier)
            if observed is None or observed['state'] == 'created':
                # A lost start acknowledgement can still arrive at the daemon.
                # Preserve the ID and the unknown effect for later reconciliation.
                return {**result, 'status': 'start-outcome-unknown', 'containerCleanup': 'unknown'}
        started = self._effect('start', bound, lambda: self._start(identifier, deadline),
                               lambda: self._observe_started(identifier))
        if started['processStarted'] is False:
            cleanup = self._effect('remove', bound, lambda: self._remove(identifier), lambda: self._absent(identifier))
            return {**result, **cleanup, 'status': 'deadline-before-start'}

        def terminal():
            observed = self._require_state(identifier, ('running', 'exited'))
            if self.journal.inspect('docker:kill') or (observed['state'] == 'running' and self.clock() >= deadline):
                return self._effect('kill', bound, lambda: self._terminate(identifier),
                                    lambda: self._terminal(identifier))
            return observed

        previous = self.journal.inspect('docker:terminal')
        if previous is None:
            observed = terminal()
            if observed['state'] == 'running':
                return {**result, **observed, 'status': 'running', 'containerCleanup': 'not-attempted'}
            final = self._effect('terminal', bound, lambda: observed, lambda: self._terminal(identifier))
        else:
            final = self._effect('terminal', bound, lambda: self._require_state(identifier, ('exited',)),
                                 lambda: self._terminal(identifier))
        evidence = {}
        try:
            if self.platform_build:
                evidence = {'platformBuild': self._build_evidence(bound, final), 'productAccepted': False}
        finally:
            cleanup = self._effect('remove', bound, lambda: self._remove(identifier), lambda: self._absent(identifier))
        return {**result, **final, **cleanup, **evidence, 'status': 'exited',
                'deadlineTerminationAttempted': self.journal.inspect('docker:kill') is not None}
