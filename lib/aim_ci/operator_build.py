"""Installed local operator admission and recovery for one reviewed build."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import os
from pathlib import Path
from stat import S_IMODE
import time

from openai_budget import Refused, canonical, fingerprint
from . import docker_engine
from .control_package import MAX_FILE, verify_installation
from .docker_image_evidence import (_reviewed_inputs, acquire_image_evidence, recover_image_readback,
                                    verify_readback_receipts)
from .effects import EffectJournal
from .installed_runtime import file_sha
from .npm_platform import PLATFORM_STORE
from .owned_paths import new_owned_directory, owned_directory, owned_file, read_owned
from .platform_build import HELPER, reconcile_report, report_json, reviewed_artifact, reviewed_build
from .recipe_plan import (_advance_docker_recipe, _prepare_docker_recipe, digest, docker_job_intent,
                          fields, planned_browser_runtime, planned_source_check, planned_test_selector, require_test_runtime, reviewed_platform_operations)

_LIMITATIONS = {
    'authority': 'local-os-principal-only',
    'loopback': 'available-inside-container-strict-denial-unqualified',
    'resourceEnforcement': 'requested-and-inspected-no-independent-qualification',
    'controllerDeath': 'no-independent-watchdog-resume-original-journal',
    'workspaceExecution': 'executable-disposable-tmpfs-for-reviewed-project-code',
    'hostileCodeIsolation': 'not-qualified',
    'browserTestsAndHostedEffects': 'outside-this-effect',
}
_AUTH = 'authorization.json'
_REVOKED = 'revoked.json'


def canonical_path(value, label):
    if not isinstance(value, str):
        raise Refused(f'Invalid {label} path')
    path = Path(value)
    if not path.is_absolute() or str(path) != value or path.resolve() != path:
        raise Refused(f'{label} requires a canonical absolute path')
    return path


class PinnedInputMismatch(Refused):
    pass


def owned_input(path, pin, label):
    if not digest(pin):
        raise Refused(f'Invalid {label} SHA-256')
    with os.fdopen(owned_file(path.parent, path.name, os.O_RDONLY), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.geteuid() or S_IMODE(info.st_mode) & 0o022:
            raise Refused(f'{label} must be owned by the invoking OS user and protected from other writers')
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise Refused(f'{label} bytes differ from the expected SHA-256')
    if sha256(raw).hexdigest() != pin:
        raise PinnedInputMismatch(f'{label} bytes differ from the expected SHA-256')
    return raw


def validate_directory(path, *, private):
    descriptor = owned_directory(path)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or S_IMODE(info.st_mode) & (0o077 if private else 0o022):
            raise Refused('Operator build state requires an owned and protected directory')
    finally:
        os.close(descriptor)


def overlaps(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


class ActiveController(Refused):
    pass


@contextmanager
def operator_lock(state, *, name='operator.lock'):
    with os.fdopen(owned_file(state, name, os.O_RDWR | os.O_CREAT), 'r+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ActiveController('This operator build already has an active controller') from None
        yield


def original_authorization(root, state, intent_pin):
    validate_directory(state, private=True)
    record = report_json(read_owned(state, _AUTH))
    fields(record, 'version intent intentSha256 consent authorizedAt', 'operator authorization')
    intent = record['intent']
    context = intent.get('invocation') if isinstance(intent, dict) else None
    if (type(record['version']) is not int or record['version'] != 1 or not digest(intent_pin)
            or record['intentSha256'] != intent_pin or fingerprint(intent) != intent_pin
            or record['consent'] != 'explicit-cli-invocation-by-local-os-principal'
            or not isinstance(record['authorizedAt'], str) or not record['authorizedAt']
            or not isinstance(context, dict) or intent.get('effect') != 'one-local-reviewed-platform-build'
            or context.get('state') != str(state) or context.get('controller') != str(root)
            or (context.get('uid'), context.get('gid')) != (os.getuid(), os.getgid())
            or (os.geteuid(), os.getegid()) != (os.getuid(), os.getgid())):
        raise Refused('Retained authorization differs from this invocation and exact effect')
    return intent


def write_state(directory, name, raw):
    with os.fdopen(owned_file(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def journal_operations(state, kind):
    child = state / kind
    path = child / 'effects.json'
    marker = child / 'effects-established.json'
    if not os.path.lexists(path) and not os.path.lexists(marker):
        return {}
    validate_directory(child, private=True)
    data = report_json(read_owned(child, path.name, 4_000_000))
    identity = report_json(read_owned(child, marker.name))
    fields(data, 'version bindingSha256 synthetic operations', 'retained build journal')
    if (type(data['version']) is not int or data['version'] != 2 or data['synthetic'] is not False
            or not digest(data['bindingSha256']) or not isinstance(data['operations'], dict)
            or identity != {name: data[name] for name in ('version', 'bindingSha256', 'synthetic')}
            or any(not isinstance(row, dict) or row.get('state') not in ('pending', 'unknown', 'completed')
                   for row in data['operations'].values())):
        raise Refused('Invalid retained build journal')
    return data['operations']


def operation_changes(before, after):
    return {name: {'state': row['state'], 'observation': (
        'new' if name not in before else 'reconciled' if row != before[name] else 'retained')}
        for name, row in after.items()}


BUILD_CONFIGURATION_FIELDS = 'version manifestSha256 plan containerProfile dockerPolicy platform stateDir'


class OperatorBuild:
    def __init__(self, root, config_path, config_pin, manifest_pin, *, with_offline_test=False):
        self.root = root
        self.config_path = canonical_path(str(config_path), 'Build configuration')
        self.config_pin = config_pin
        self.manifest_pin = manifest_pin
        self.with_offline_test = with_offline_test
        config = report_json(owned_input(self.config_path, config_pin, 'Build configuration'))
        fields(config, BUILD_CONFIGURATION_FIELDS, 'operator build configuration')
        if type(config['version']) is not int or config['version'] != 1:
            raise Refused('Unsupported operator build configuration version')
        uid, gid = os.geteuid(), os.getegid()
        if (uid, gid) != (os.getuid(), os.getgid()):
            raise Refused('Operator build requires the invoking local OS principal')
        self.state = canonical_path(config['stateDir'], 'Build state')
        validate_directory(self.state.parent, private=False)
        platform = config['platform']
        fields(platform, 'os cpu libc', 'approved dependency platform')
        if platform['os'] != 'linux' or platform['cpu'] not in ('x64', 'arm64') or platform['libc'] != 'glibc':
            raise Refused('Unsupported operator build platform')

        paths = {}
        for name in ('plan', 'containerProfile', 'dockerPolicy'):
            entry = config[name]
            fields(entry, 'path sha256', name)
            paths[name] = canonical_path(entry['path'], name)
            raw = owned_input(paths[name], entry['sha256'], name)
            if name == 'dockerPolicy':
                self.policy = report_json(raw)
        self.policy_pin = config['dockerPolicy']['sha256']
        if config['manifestSha256'] != manifest_pin:
            raise Refused('Build configuration does not match the supplied installed manifest pin')
        manifest = verify_installation(root, manifest_pin)
        helper_pin = manifest['files'].get(HELPER)
        if not digest(helper_pin) or 'lib/aim_ci/operator_build.py' not in manifest['files']:
            raise Refused('Operator build requires a verified installed controller with its build helper')
        self.args = (paths['plan'], config['plan']['sha256'], manifest_pin, paths['containerProfile'],
                     config['containerProfile']['sha256'], helper_pin)
        plan, binding, tools, _, read_paths = _reviewed_inputs(*self.args, platform_build=True)
        source_check = planned_source_check(plan)
        if source_check is not None:
            self.with_offline_test = True
        if (not all(isinstance(plan.get(name), dict) and plan[name].get('kind') == 'operator-record'
                    and digest(plan[name].get('sha256')) for name in ('review', 'approval'))
                or not isinstance(plan.get('recipe'), dict) or plan['recipe'].get('format') != 'aim-ci-recipe'):
            raise Refused('Operator build requires separately reviewed and approved planning records')
        target = plan['target']
        checkout = canonical_path(target.get('checkout'), 'Planned checkout')
        if overlaps(self.state, root) or overlaps(self.state, checkout):
            raise Refused('Build state must be separate from the controller and checkout')
        for path in (self.config_path, *paths.values()):
            if path.is_relative_to(root) or path.is_relative_to(checkout) or overlaps(path, self.state):
                raise Refused('Reviewed build inputs must be separate from the controller, checkout and state')
        if len({self.config_path, *paths.values()}) != 4:
            raise Refused('Reviewed build inputs must use separate files')
        if not all(isinstance(target.get(key), str) and target[key] for key in ('repository', 'branch')):
            raise Refused('Reviewed build lacks its repository and branch identity')
        preparation = _prepare_docker_recipe(plan, binding, self.policy, self.policy_pin, None)
        if (preparation['intent'] is None
                or file_sha(Path(docker_engine.__file__)) != self.policy['adapterSha256']):
            raise Refused('Operator build requires the reviewed daemon, adapter, runtime and image')
        script, operations = reviewed_platform_operations(plan, self.with_offline_test)
        browser = planned_browser_runtime(plan)
        if browser is not None and browser['platform'] != platform:
            raise Refused('Operator dependency platform differs from the reviewed browser platform')
        limits = preparation['intent']['requestedControls']
        self.intent = {
            'version': 1, 'effect': 'one-local-reviewed-platform-build',
            'invocation': {'uid': uid, 'gid': gid, 'controller': str(root), 'state': str(self.state),
                           'configPath': str(self.config_path), 'configSha256': config_pin},
            'target': {key: target[key] for key in ('repository', 'branch', 'checkout', 'commit', 'tree')},
            'binding': binding, 'planningReview': plan['review'], 'planningApproval': plan['approval'],
            'policy': self.policy, 'policySha256': self.policy_pin, 'helperSha256': helper_pin,
            'dependencyStore': {'kind': 'npm-no-install-v1' if source_check is not None else PLATFORM_STORE, 'platform': platform,
                                'indexSha256': binding['dependencyStoreSha256']},
            'tools': tools, 'readbackPaths': read_paths, 'operations': operations, 'plannedTestScript': script,
            'artifact': reviewed_artifact(plan),
            'workflow': {'readback': 'one-never-started-container', 'buildStarts': 1,
                         'cleanup': 'exact-owned-containers', 'targetCheckoutWrites': False,
                         'artifactRoot': 'dist', 'artifactLinksAllowed': False},
            'requestedControls': {'networkMode': 'none', 'hostPorts': [], 'hostMounts': [],
                                  'rootReadOnly': True, 'user': '65534:65534', 'capDrop': ['ALL'],
                                  'noNewPrivileges': True, 'workspace': {'/work': docker_engine.PLATFORM_WORKSPACE_OPTIONS},
                                  **{name: limits[name] for name in ('memoryBytes', 'memoryAndSwapBytes',
                                                                   'processCount', 'wholeContainerDeadlineSeconds')}},
            'acknowledgedLimitations': _LIMITATIONS,
        }
        if source_check is not None:
            self.intent.update(execution=source_check, sourceFiles=plan['sourceFiles'])
            self.intent['workflow'].update(artifactRoot=None, artifactLinksAllowed=False,
                                           dependencyInstallation=False, buildCommand=False)
        if self.with_offline_test:
            self.intent['testPrerequisites'] = {
                'requiresBrowserNetworkLoopbackOrServices': False,
                'usesOnlyPinnedRuntimeAndInstalledDependencies': source_check is None,
            }
            if source_check is not None:
                self.intent['testPrerequisites'].update(dependenciesInstalled=False, usesOnlyPinnedRuntimeAndSelectedSource=True)
        selector = planned_test_selector(plan) if self.with_offline_test else None
        if selector is not None:
            self.intent['testSelector'] = selector
            self.intent['testPrerequisites'] = {
                'verifiedBrowserRuntime': False,
                'approvedContainerLoopback': False,
                'discoveryLoadsProjectCode': True,
            }
        if browser is not None:
            self.intent['browserInventory'] = {'status': 'reviewed-not-measured', 'profile': browser}
            if selector is not None:
                self.intent['requestedControls']['browserNetwork'] = browser['network']
                self.intent['testPrerequisites']['browserRuntimeQualification'] = 'unqualified'
        self.intent_pin = fingerprint(self.intent)

    def preview(self):
        return {'status': 'preview', 'executionAuthorized': False, 'intentSha256': self.intent_pin,
                'intent': self.intent, 'stateExists': os.path.lexists(self.state)}

    def authorize(self, supplied):
        if supplied != self.intent_pin:
            raise Refused('Explicit approval must match the exact preview intent SHA-256')
        require_test_runtime(self.intent.get('testSelector'))
        record = {'version': 1, 'intent': self.intent, 'intentSha256': self.intent_pin,
                  'consent': 'explicit-cli-invocation-by-local-os-principal',
                  'authorizedAt': datetime.now(timezone.utc).isoformat()}
        with new_owned_directory(self.state) as directory:
            write_state(directory, _AUTH, canonical(record).encode())

    def check_authorization(self):
        if original_authorization(self.root, self.state, self.intent_pin) != self.intent:
            raise Refused('Retained operator intent changed')

    def before_effect(self):
        fresh = OperatorBuild(self.root, self.config_path, self.config_pin, self.manifest_pin,
                              with_offline_test=self.with_offline_test)
        if fresh.intent_pin != self.intent_pin:
            raise Refused('Reviewed build inputs or local invocation changed before dispatch')
        self.check_authorization()
        if os.path.lexists(self.state / _REVOKED):
            raise Refused('Build authority was withdrawn. Only exact-owned cleanup is allowed.')

    def status(self):
        result = {'status': 'not-started', 'intentSha256': self.intent_pin, 'dockerContacted': False}
        if not os.path.lexists(self.state):
            return result
        return recorded_status(self.root, self.state, self.intent_pin, self.manifest_pin)

    def run(self):
        self.check_authorization()
        require_test_runtime(self.intent.get('testSelector'))
        with operator_lock(self.state):
            self.before_effect()
            phases = ('image-readback', 'build')
            before = {kind: journal_operations(self.state, kind) for kind in phases}
            acquisition_id = fingerprint({'operatorIntent': self.intent_pin, 'phase': 'image-readback'})
            acquisition = acquire_image_evidence(
                *self.args, trusted_docker_policy=self.policy, trusted_docker_policy_pin=self.policy_pin,
                acquisition_id=acquisition_id,
                journal_path=self.state / 'image-readback', platform_build=True, before_effect=self.before_effect)
            store = acquisition['measurements']['dependencyTarballs']
            if store['platform'] != self.intent['dependencyStore']['platform']:
                raise Refused('Measured dependency platform differs from the approved effect')
            evidence = self.retain('readback.json', acquisition)
            request = docker_engine.guarded_request(self.before_effect)
            # An absolute deadline survives a CLI restart. The Docker job journals it once.
            while True:
                self.before_effect()
                result = _advance_docker_recipe(
                    *self.args[:3], trusted_docker_policy=self.policy, trusted_docker_policy_pin=self.policy_pin,
                    dependency_store_sha256=self.intent['dependencyStore']['indexSha256'],
                    journal_path=self.state / 'build', request=request, clock=time.time,
                    platform_evidence=(self.state / 'readback.json', evidence), response=docker_engine.unix_response,
                    with_offline_test=self.with_offline_test, readback_acquisition_id=acquisition_id)
                if result['status'] != 'running':
                    break
                time.sleep(0.25)
            if result['status'] != 'start-outcome-unknown':
                self.retain('result.json', {'intentSha256': self.intent_pin, 'result': result})
            return {**result, 'operator': {'intentSha256': self.intent_pin, 'journalOperations': {
                kind: operation_changes(before[kind], journal_operations(self.state, kind)) for kind in phases}}}

    def retain(self, name, value):
        raw = canonical(value).encode()
        if os.path.lexists(self.state / name):
            if read_owned(self.state, name, 4_000_000) != raw:
                raise Refused('Retained operator build evidence differs')
        else:
            write_state(self.state, name, raw)
        return sha256(raw).hexdigest()


def recorded_status(root, state, intent_pin, manifest_pin):
    state = canonical_path(str(state), 'Build state')
    intent = original_authorization(root, state, intent_pin)
    if intent['binding']['controlManifestSha256'] != manifest_pin:
        raise Refused('Original state does not match the supplied installed manifest pin')
    result = {'intentSha256': intent_pin, 'dockerContacted': False}
    states = {kind: {name: row['state'] for name, row in journal_operations(state, kind).items()}
              for kind in ('image-readback', 'build')}
    result.update(status='recorded', operations=states, revoked=os.path.lexists(state / _REVOKED))
    if os.path.lexists(state / 'cleanup.json'):
        result['recordedCleanup'] = report_json(read_owned(state, 'cleanup.json'))
    if os.path.lexists(state / 'result.json'):
        saved = report_json(read_owned(state, 'result.json', 4_000_000))
        if saved['intentSha256'] != intent_pin:
            raise Refused('Retained build result does not bind this exact intent')
        result['recordedResult'] = saved['result']
    return result


def verify_named_test_evidence(root, state, intent_pin, manifest_pin, *, target, profile, paths, exact_scope=False):
    """Recheck the producer's receipts and raw result without advancing any effect."""
    root = canonical_path(str(root), 'Controller installation')
    state = canonical_path(str(state), 'Build state')
    intent = original_authorization(root, state, intent_pin)
    if intent['target'] != target:
        raise Refused('Recipe evidence is for a different candidate target')
    binding = intent['binding']
    if (binding['controlManifestSha256'] != manifest_pin
            or binding['recipeManifestSha256'] != profile['recipeManifestSha256']
            or binding['runtimeSha256'] != profile['runtimeSha256']
            or intent['plannedTestScript'] != profile['testScript']
            or intent.get('testSelector') != profile.get('testSelector')):
        raise Refused('Recipe evidence does not match the reviewed recipe, runtime and test')
    invocation = intent['invocation']
    checked = OperatorBuild(root, invocation['configPath'], invocation['configSha256'], manifest_pin,
                            with_offline_test=True)
    if checked.intent != intent or os.path.lexists(state / _REVOKED):
        raise Refused('Named test authorization changed or was withdrawn')
    plan, _, _, script, _ = _reviewed_inputs(*checked.args, platform_build=True)
    scope = plan.get('scope', {})
    requested = scope.get('requestedPaths')
    if (scope.get('allowedPaths') != profile['allowedPaths'] or not isinstance(requested, list)
            or (requested != paths if exact_scope else any(path not in requested for path in paths))):
        raise Refused('Recipe scope differs from the reviewed project profile')
    readback = read_owned(state, 'readback.json', MAX_FILE)
    readback_pin = sha256(readback).hexdigest()
    acquisition_id = fingerprint({'operatorIntent': intent_pin, 'phase': 'image-readback'})
    review = reviewed_build(plan, binding, checked.policy, checked.policy_pin,
                            state / 'readback.json', readback_pin, controller_root=root,
                            acquisition_id=acquisition_id)
    request = {'acquisitionId': acquisition_id, 'binding': binding, 'policySha256': checked.policy_pin,
               'endpoint': checked.policy['endpoint'], 'readPaths': intent['readbackPaths'],
               'entrypointSha256': intent['helperSha256']}
    journal_operations(state, 'image-readback')
    verify_readback_receipts(state / 'image-readback', request, report_json(readback), script)
    review['testOperation'] = next(operation for operation in intent['operations'] if operation['name'] == 'test')
    if 'testSelector' in intent:
        review['testSelector'] = intent['testSelector']
    preparation = _prepare_docker_recipe(plan, binding, checked.policy, checked.policy_pin, None)
    job = docker_job_intent(preparation, intent['dependencyStore']['indexSha256'], script, review)
    journal_operations(state, 'build')
    journal = EffectJournal(state / 'build', {'mode': 'docker-engine-lifecycle', 'effectId': job['effectId']},
                            read_only=True)
    deadline = journal.completed_result('docker:deadline', job)['expiresAt']
    created = journal.completed_result('docker:create', job)
    bound = {'containerId': docker_engine._container_id(created.get('containerId')),
             'effectId': job['effectId'], 'expiresAt': deadline}
    if (journal.completed_result('docker:start', bound) != {'containerId': bound['containerId'], 'processStarted': True}
            or journal.inspect('docker:kill') is not None):
        raise Refused('Named test lacks an uninterrupted authorized start')
    terminal = journal.completed_result('docker:terminal', bound)
    if terminal.get('containerId') != bound['containerId'] or terminal.get('state') != 'exited':
        raise Refused('Named test lacks an exact terminal result')
    terminal = {**terminal, 'deadlineExceeded': docker_engine._timestamp(terminal['finishedAt']).timestamp() >= deadline}
    evidence = journal.completed_result('docker:build-evidence', {**bound, 'terminal': terminal})
    expected_status = 'source-check-passed' if planned_source_check(plan) is not None else 'build-and-test-passed'
    if (evidence.get('status') != expected_status
            or reconcile_report(state / 'build', job, terminal) != evidence
            or journal.completed_result('docker:remove', bound) != {'containerCleanup': 'confirmed-absent'}):
        raise Refused('Named test, raw report or exact cleanup did not verify')
    return {'status': expected_status, 'target': target, 'binding': binding,
            'operatorIntentSha256': intent_pin, 'effectId': job['effectId'],
            'testScript': script, 'test': evidence['test'], 'artifacts': evidence['artifacts'],
            'evidenceSha256': evidence['evidenceSha256'], 'cleanup': 'confirmed-absent',
            **{key: evidence[key] for key in ('testSelector', 'discovery', 'browserRuntime', 'browserFiles') if key in evidence}}


def recover_build(root, state, intent_pin, manifest_pin):
    """Withdraw new dispatch and recover exact-owned resources from original state."""
    state = canonical_path(str(state), 'Build state')
    intent = original_authorization(root, state, intent_pin)
    binding = intent['binding']
    if binding['controlManifestSha256'] != manifest_pin:
        raise Refused('Original state does not match the supplied installed manifest pin')
    policy = intent['policy']
    if fingerprint(policy) != intent['policySha256'] or file_sha(Path(docker_engine.__file__)) != policy['adapterSha256']:
        raise Refused('Original recovery adapter or policy differs')

    def readback():
        if not journal_operations(state, 'image-readback').get('image-readback:create'):
            return {'cleanup': 'not-needed'}
        original = {'acquisitionId': fingerprint({'operatorIntent': intent_pin, 'phase': 'image-readback'}),
                    'binding': binding, 'policySha256': intent['policySha256'], 'endpoint': policy['endpoint'],
                    'readPaths': intent['readbackPaths'], 'entrypointSha256': intent['helperSha256']}
        return recover_image_readback(state / 'image-readback', original)

    def container():
        entry = journal_operations(state, 'build').get('docker:create')
        if entry is None:
            return {'containerCleanup': 'not-needed'}
        original = entry['intent']
        effect = fingerprint({key: value for key, value in original.items() if key not in ('effectId', 'containerName')})
        review = original.get('platformBuild', {})
        controls = original.get('requestedControls', {})
        test_operation = next((op for op in intent['operations'] if op['name'] == 'test'), None)
        if (original.get('effectId') != effect or original.get('containerName') != 'aim-ci-' + effect[:24]
                or original.get('binding') != binding or original.get('dockerPolicySha256') != intent['policySha256']
                or original.get('dependencyStoreSha256') != intent['dependencyStore']['indexSha256']
                or original.get('testScript') != intent['plannedTestScript']
                or review.get('testOperation') != test_operation
                or review.get('execution') != intent.get('execution')
                or review.get('sourceFiles') != intent.get('sourceFiles')
                or review.get('testSelector') != intent.get('testSelector')
                or (review.get('browserRuntime') or {}).get('profileSha256') != binding.get('browserRuntimeSha256')
                or controls.get('browserNetwork') != intent['requestedControls'].get('browserNetwork')
                or review.get('artifact') != intent['artifact'] or review.get('tools') != intent['tools']
                or review.get('entrypointSha256') != intent['helperSha256']
                or review.get('store', {}).get('platform') != intent['dependencyStore']['platform']
                or any(controls.get(key) != intent['requestedControls'][key] for key in
                       ('memoryBytes', 'memoryAndSwapBytes', 'processCount', 'wholeContainerDeadlineSeconds'))):
            raise Refused('Build recovery journal differs from the original approved effect')
        journal = EffectJournal(state / 'build', {'mode': 'docker-engine-lifecycle', 'effectId': effect})
        engine = docker_engine.DockerEngine(policy['endpoint'], docker_engine.unix_request)
        job = docker_engine.DockerRecipeJob(journal, engine, original, binding['imageDigest'],
                                           intent['plannedTestScript'], time.time)
        return job.cancel()

    withdrawal = {'intentSha256': intent_pin}
    marker = state / _REVOKED
    if not os.path.lexists(marker):
        try:
            write_state(state, _REVOKED, canonical(withdrawal).encode())
        except Refused:
            if not os.path.lexists(marker):
                raise
    if report_json(read_owned(state, _REVOKED)) != withdrawal:
        raise Refused('Retained withdrawal does not bind the original intent')
    result = {'status': 'cleanup-confirmed', 'intentSha256': intent_pin,
              'withdrawalRecorded': True, 'newBuildAuthorized': False}
    try:
        with operator_lock(state):
            for name, action in (('build', container), ('imageReadback', readback)):
                try:
                    result[name] = action()
                except (OSError, Refused) as error:
                    result['status'] = 'cleanup-unresolved'
                    result[name] = {'status': 'unknown', 'reason': str(error)}
            if result['status'] == 'cleanup-confirmed':
                location = state / 'cleanup.json'
                raw = canonical(result).encode()
                if not os.path.lexists(location):
                    write_state(state, location.name, raw)
                elif read_owned(state, location.name) != raw:
                    raise Refused('Retained cleanup evidence differs')
    except ActiveController:
        result['status'] = 'cleanup-pending-active-controller'
    return result, 0 if result['status'] == 'cleanup-confirmed' else 1


def operator_build(root, args):
    if args.action == 'cleanup-reviewed-build':
        return recover_build(root, args.state, args.intent_sha256, args.manifest_sha256)
    if args.action == 'status-reviewed-build':
        if args.state is not None:
            if (args.config is not None or args.config_sha256 is not None or args.intent_sha256 is None
                    or args.with_offline_test):
                raise Refused('Use only the original state and intent SHA-256 for retained status')
            return recorded_status(root, args.state, args.intent_sha256, args.manifest_sha256), 0
        if args.config is None or args.config_sha256 is None or args.intent_sha256 is not None:
            raise Refused('Provide either the configuration and pin or the original state and intent pin')
    build = OperatorBuild(root, args.config, args.config_sha256, args.manifest_sha256,
                          with_offline_test=args.with_offline_test)
    if args.action == 'status-reviewed-build':
        return build.status(), 0
    if args.preview:
        return build.preview(), 0
    if not args.resume:
        build.authorize(args.authorize_intent)
    result = build.run()
    expected = ('source-check-passed' if 'execution' in build.intent else
                'build-and-test-passed' if args.with_offline_test else 'build-passed')
    passed = (result.get('platformBuild', {}).get('status') == expected
              and result.get('containerCleanup') == 'confirmed-absent'
              and result.get('deadlineTerminationAttempted') is False)
    return result, 0 if passed else 1
