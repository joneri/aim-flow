"""Admission and retained evidence for the private fixed npm platform build."""
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import os
from pathlib import Path
from stat import S_IMODE

from openai_budget import Refused, canonical, fingerprint
from .control_package import verify_installation
from .npm_platform import PLATFORM_STORE, project_path
from .owned_paths import owned_directory, owned_file, owned_mkdir, read_owned
from .project_discovery import unique_json
from .recipe_plan import (MAX_SAFE_INTEGER, PLAYWRIGHT_CONFIG, browser_runtime_files, browser_runtime_paths, digest, fields,
                          pinned_bytes, planned_browser_runtime, planned_source_check, planned_source_inventory, planned_test_selector,
                          validate_test_selector)

HELPER = 'lib/aim_ci/npm_platform_build.cjs'
CONTAINER_HELPER = '/opt/aim-ci/npm_platform_build.cjs'
EVIDENCE_NAME = 'platform-build.json'
MAX_REPORT = 30_000_000
MAX_OPERATION_LOG = 128 * 1024
NO_INSTALL_STORE = 'npm-no-install-v1'


def report_json(raw):
    try:
        return unique_json(raw)
    except (ValueError, RecursionError):
        raise Refused('Malformed or duplicate-key platform build evidence JSON') from None


def reviewed_build(plan, binding, policy, policy_pin, evidence_path, evidence_pin, *, controller_root=None,
                   acquisition_id=None):
    """Validate a controller-pinned readback receipt, separate from dispatch authority."""
    if 'containerProfileSha256' not in binding:
        raise Refused('Platform build requires the reviewed container profile')
    root = controller_root if controller_root is not None else Path(__file__).resolve().parents[2]
    manifest = verify_installation(root, binding['controlManifestSha256'])
    evidence = report_json(pinned_bytes(evidence_path, evidence_pin, 'Platform image evidence'))
    if not isinstance(evidence, dict):
        raise Refused('Invalid platform image evidence')
    provenance = evidence.get('provenance')
    measured = evidence.get('measurements')
    if (evidence.get('version') != 1 or evidence.get('status') != 'bytes-match-reviewed-inputs'
            or evidence.get('binding') != binding or not isinstance(provenance, dict)
            or provenance.get('kind') != 'trusted-local-daemon'
            or provenance.get('imageOrigin') != 'verified-by-trusted-readback'
            or provenance.get('cleanup') != 'confirmed-absent'
            or provenance.get('endpoint') != policy['endpoint'] or provenance.get('policySha256') != policy_pin
            or not isinstance(provenance.get('imageInspect'), dict)
            or provenance['imageInspect'].get('imageId') != binding['imageDigest']
            or not isinstance(provenance.get('before'), dict) or provenance.get('after') != provenance['before']
            or provenance['before'].get('neverStarted') is not True
            or provenance['before'].get('containerId') != provenance.get('containerId')
            or not digest(provenance.get('containerId'))
            or not isinstance(provenance.get('readPaths'), dict)
            or provenance['readPaths'].get('entrypoint') != CONTAINER_HELPER
            or not isinstance(measured, dict) or not digest(manifest['files'].get(HELPER))
            or measured.get('entrypointSha256') != manifest['files'][HELPER]
            or measured.get('tools') != plan['runtime']['tools']):
        raise Refused('Platform build requires exact installed helper and trusted image byte evidence')
    inventory = planned_source_inventory(plan)
    if inventory is not None and (measured.get('sourceInventory') != inventory
                                 or binding.get('sourceInventorySha256') != fingerprint(inventory)):
        raise Refused('Platform build requires its complete measured source inventory')
    execution = planned_source_check(plan)
    selector = planned_test_selector(plan)
    if selector is not None:
        test_inputs = measured.get('selectedTestInputs')
        fields(test_inputs, f"{selector['file']} {PLAYWRIGHT_CONFIG}", 'measured Playwright test inputs')
        if not all(digest(pin) for pin in test_inputs.values()):
            raise Refused('Platform build requires measured committed Playwright test inputs')
    store = measured.get('dependencyTarballs')
    fields(store, 'kind platform lockPackageCount packageCount tarballCount fullLockVerified excludedPackageCount',
           'measured npm platform store')
    platform = store['platform']
    fields(platform, 'os cpu libc', 'measured npm platform')
    if (store['kind'] != (NO_INSTALL_STORE if execution is not None else PLATFORM_STORE)
            or store['fullLockVerified'] is not False
            or platform['os'] != 'linux' or platform['cpu'] not in ('arm64', 'x64') or platform['libc'] != 'glibc'
            or any(type(store[key]) is not int or not 0 <= store[key] <= 4096 for key in
                   ('lockPackageCount', 'packageCount', 'tarballCount', 'excludedPackageCount'))
            or (not 0 < store['tarballCount'] <= store['packageCount'] if execution is None else
                store['tarballCount'] != 0 or store['packageCount'] != 0)
            or store['packageCount'] + store['excludedPackageCount'] != store['lockPackageCount']):
        raise Refused('Platform build requires the measured applicable npm store')
    result = {'imageEvidenceSha256': evidence_pin, 'entrypointSha256': measured['entrypointSha256'],
              'store': store, 'artifact': reviewed_artifact(plan), 'tools': plan['runtime']['tools']}
    if execution is not None:
        if (measured.get('sourceFiles') != plan['sourceFiles']
                or measured.get('sourceProofSha256') != binding['sourceProofSha256']
                or binding['sourceSelectionSha256'] != fingerprint(plan['sourceFiles'])
                or binding['executionSha256'] != fingerprint(execution)):
            raise Refused('Source check requires measured exact selected source and Git membership proof')
        result.update(execution=execution, sourceFiles=plan['sourceFiles'])
    browser = planned_browser_runtime(plan)
    if browser is not None:
        from .docker_image_evidence import verify_readback_receipts

        observed = measured.get('browserRuntime')
        expected = {'profileSha256': fingerprint(browser), 'files': browser_runtime_files(browser),
                    'coverage': 'listed-files-only', 'binaryFormat': 'elf64-little-endian-matching-cpu'}
        paths = browser_runtime_paths(browser)
        archives = measured.get('archiveSha256')
        if (observed != expected or binding.get('browserRuntimeSha256') != expected['profileSha256']
                or platform != browser['platform'] or provenance['imageInspect'].get('platform') != {
                    'os': 'linux', 'architecture': {'arm64': 'arm64', 'x64': 'amd64'}[platform['cpu']]}
                or not isinstance(archives, dict) or any(not digest(archives.get(key)) or
                    provenance['readPaths'].get(key) != path for key, path in paths.items())
                or not digest(acquisition_id)):
            raise Refused('Platform build requires image-bound browser inventory and its controller acquisition')
        request = {'acquisitionId': acquisition_id, 'binding': binding, 'policySha256': policy_pin,
                   'endpoint': policy['endpoint'], 'readPaths': provenance['readPaths'],
                   'entrypointSha256': measured['entrypointSha256']}
        verify_readback_receipts(evidence_path.parent/'image-readback', request, evidence, plan['testSelection']['script'])
        result['browserRuntime'] = {**observed, 'revision': browser['revision'], 'platform': browser['platform'],
                                    'executable': browser['executable']['path'], 'network': browser['network'],
                                    'runtimeQualification': 'unqualified', 'networkEnforcement': 'unqualified'}
    return result


def reviewed_artifact(plan):
    if planned_source_check(plan) is not None:
        return {'kind': 'none'}
    artifact = plan.get('artifact')
    fields(artifact, 'directory requiredFiles maximumBytes linksAllowed status', 'planned build artifact')
    files, limit = artifact['requiredFiles'], artifact['maximumBytes']
    if (artifact['directory'] != 'dist' or artifact['linksAllowed'] is not False
            or artifact['status'] != 'not-built' or type(limit) is not int or not 1 <= limit <= MAX_SAFE_INTEGER
            or not isinstance(files, list) or not files
            or not all(project_path(name) for name in files) or len(set(files)) != len(files)
            or 'index.html' not in files):
        raise Refused('Invalid reviewed static build artifact policy')
    return {'requiredFiles': files, 'maximumBytes': limit}


@contextmanager
def platform_job_lock(directory, checkout):
    directory = Path(directory)
    root = Path(__file__).resolve().parents[2]
    if not isinstance(checkout, str) or not Path(checkout).is_absolute():
        raise Refused('Platform build requires the canonical planned checkout identity')
    checkout_path = Path(checkout)
    if (checkout_path.resolve() != checkout_path or str(checkout_path) != checkout
            or not directory.is_absolute() or directory.resolve() != directory
            or any(directory.is_relative_to(path) or path.is_relative_to(directory)
                   for path in (root, checkout_path))):
        raise Refused('Platform build requires a separate canonical journal path')
    owned_mkdir(directory.parent, directory.name)
    descriptor = owned_directory(directory)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or S_IMODE(info.st_mode) & 0o077:
            raise Refused('Platform build journal must be private to the trusted controller')
        with os.fdopen(owned_file(descriptor, 'platform-build.lock', os.O_RDWR | os.O_CREAT), 'r+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('Platform build already has an active controller') from None
            yield
    finally:
        os.close(descriptor)


def runner_arguments(intent):
    review = intent['platformBuild']
    config = {'effectId': intent['effectId'], 'binding': intent['binding'], 'tools': review['tools'],
              'platform': review['store']['platform'], 'artifact': review['artifact'],
              'timeoutSeconds': intent['requestedControls']['wholeContainerDeadlineSeconds']}
    if 'execution' in review:
        config.update(execution=review['execution'], sourceFiles=review['sourceFiles'])
    if 'testOperation' in review:
        config['testScript'] = intent['testScript']
        if 'testSelector' in review:
            config['testSelector'] = review['testSelector']
            if 'browserRuntime' in review:
                config['browserRuntime'] = review['browserRuntime']
    return [CONTAINER_HELPER, b64encode(canonical(config).encode()).decode()]


def decoded(value, limit):
    if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
        raise Refused('Oversized or invalid build evidence bytes')
    try:
        data = b64decode(value, validate=True)
    except (Base64Error, ValueError):
        raise Refused('Invalid base64 build evidence bytes') from None
    if len(data) > limit or b64encode(data).decode() != value:
        raise Refused('Noncanonical or oversized build evidence bytes')
    return data


def operation(report):
    fields(report, 'exitCode signal error stdout stderr', 'npm operation evidence')
    code, signal, error = (report[key] for key in ('exitCode', 'signal', 'error'))
    if (not ((type(code) is int and 0 <= code <= 255 and signal is None)
             or (code is None and (isinstance(signal, str) and signal.startswith('SIG')
                                   or signal is None and error is not None)))
            or (error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 32))):
        raise Refused('Invalid npm operation outcome')
    return {'exitCode': code, 'signal': signal, 'error': error,
            'stdoutSha256': sha256(decoded(report['stdout'], MAX_OPERATION_LOG)).hexdigest(),
            'stderrSha256': sha256(decoded(report['stderr'], MAX_OPERATION_LOG)).hexdigest()}


def passed(result):
    return result is not None and result['exitCode'] == 0 and result['signal'] is None and result['error'] is None


def artifact_inventory(files, policy):
    if (not isinstance(files, dict) or not files
            or not set(policy['requiredFiles']) <= files.keys()):
        raise Refused('Required fresh build artifacts are absent')
    total, inventory = 0, {}
    for name, row in sorted(files.items()):
        if (not project_path(name)
                or any(name[:index] in files for index, char in enumerate(name) if char == '/')):
            raise Refused('Unsafe or overlapping build artifact path')
        fields(row, 'size sha256 data', 'build artifact')
        content = decoded(row['data'], policy['maximumBytes'] - total)
        size, pin = len(content), sha256(content).hexdigest()
        if type(row['size']) is not int or row['size'] != size or row['sha256'] != pin:
            raise Refused('Build artifact differs from its declared bytes')
        total += size
        inventory[name] = {'size': size, 'sha256': pin}
    if not inventory['index.html']['size']:
        raise Refused('Built index.html is empty')
    return {'files': inventory, 'bytes': total, 'sha256': fingerprint(inventory)}


def mount_observation(value):
    fields(value, 'id parentId mountPoint type mountOptions superOptions executable', 'workspace mount observation')
    options = value['mountOptions'], value['superOptions']
    if (any(type(value[key]) is not int or value[key] <= 0 for key in ('id', 'parentId'))
            or value['mountPoint'] != '/work' or not isinstance(value['type'], str) or not value['type']
            or any(not isinstance(items, list) or not 1 <= len(items) <= 64
                   or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in items)
                   for items in options)
            or value['executable'] is not all('noexec' not in items for items in options)):
        raise Refused('Invalid workspace mount observation')
    return value


def build_tool_observation(value):
    fields(value, 'tsc vite', 'installed build tool observation')
    for name, row in value.items():
        if row is None:
            continue
        fields(row, 'entry linkTarget resolvedPath mode uid gid executable', 'installed build tool')
        target = row['linkTarget']
        resolved = row['resolvedPath']
        if (row['entry'] != 'node_modules/.bin/' + name or not project_path(resolved)
                or not resolved.startswith('node_modules/') or type(row['mode']) is not int
                or not 0 <= row['mode'] <= 0o7777 or type(row['executable']) is not bool
                or any(type(row[key]) is not int or row[key] < 0 for key in ('uid', 'gid'))
                or target is not None and (not isinstance(target, str) or not 1 <= len(target) <= 240
                                           or any(ord(char) < 32 for char in target))):
            raise Refused('Invalid installed build tool observation')
    return value


def verify_playwright_report(raw, selector, *, discovery):
    report = report_json(raw)
    if not isinstance(report, dict) or report.get('errors') != []:
        raise Refused('Playwright report has errors or an invalid shape')
    config, suites = report.get('config'), report.get('suites')
    relative = selector['file'].removeprefix('tests/')
    if (not isinstance(config, dict) or config.get('rootDir') != '/work/project/tests'
            or not isinstance(suites, list) or len(suites) != 1 or not isinstance(suites[0], dict)):
        raise Refused('Playwright report does not bind the selected test root and file')
    file = suites[0]
    children = file.get('suites')
    if (file.get('title') != relative or file.get('file') != relative or file.get('specs') != []
            or not isinstance(children, list) or len(children) != 1 or not isinstance(children[0], dict)):
        raise Refused('Playwright report does not bind the selected test file and suite')
    suite = children[0]
    specs = suite.get('specs')
    if (suite.get('title') != selector['suite'] or suite.get('file') != relative
            or suite.get('suites', []) != [] or not isinstance(specs, list) or len(specs) != len(selector['names'])):
        raise Refused('Playwright report differs from the reviewed suite and selection')
    unseen = set(selector['names'])
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get('title'), str):
            raise Refused('Invalid selected Playwright test report')
        tests = spec.get('tests')
        if (spec['title'] not in unseen or spec.get('file') != relative or spec.get('tags') != []
                or not isinstance(tests, list) or len(tests) != 1 or not isinstance(tests[0], dict)):
            raise Refused('Playwright report contains a missing, extra or duplicate test')
        unseen.remove(spec['title'])
        test = tests[0]
        if test.get('projectName') != selector['project'] or test.get('expectedStatus') != 'passed':
            raise Refused('Playwright report has the wrong project or a non-passing expected status')
        runs = test.get('results')
        if discovery:
            if runs != []:
                raise Refused('Playwright discovery report contains test executions')
        elif (spec.get('ok') is not True or test.get('status') != 'expected'
              or not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict)
              or runs[0].get('status') != 'passed' or type(runs[0].get('retry')) is not int
              or runs[0]['retry'] != 0 or runs[0].get('errors') != []):
            raise Refused('Every selected Playwright test must pass once without retry')
    if not discovery:
        stats = report.get('stats')
        expected = {'expected': len(specs), 'skipped': 0, 'unexpected': 0, 'flaky': 0}
        if not isinstance(stats, dict) or any(type(stats.get(key)) is not int or stats[key] != count
                                              for key, count in expected.items()):
            raise Refused('Playwright totals do not verify the selected test result')


def validate_report(raw, intent, final):
    value = report_json(raw)
    review = intent['platformBuild']
    source_check = 'execution' in review
    with_test = 'testOperation' in review
    selector = validate_test_selector(review['testSelector']) if 'testSelector' in review else None
    browser = review.get('browserRuntime') if selector is not None else None
    required = 'version effectId binding runtime workspaceMount materialized install installedPackages buildTools build artifacts error'
    if with_test:
        required += ' testScript test'
    if selector is not None:
        required += ' testSelector discovery'
    if browser is not None:
        required += ' browserFiles'
    if source_check:
        required += ' execution'
    fields(value, required, 'platform build report')
    if (type(value['version']) is not int or value['version'] != (5 if source_check else
            4 if browser is not None else 3 if selector is not None else 2 if with_test else 1)
            or value['effectId'] != intent['effectId']
            or value['binding'] != intent['binding']):
        raise Refused('Platform build report does not bind this job and source')
    if source_check and (not with_test or selector is not None or browser is not None
            or value['execution'] != review['execution']
            or fingerprint(value['execution']) != intent['binding'].get('executionSha256')
            or fingerprint(review['sourceFiles']) != intent['binding'].get('sourceSelectionSha256')
            or review['artifact'] != {'kind': 'none'} or review['store']['kind'] != NO_INSTALL_STORE):
        raise Refused('Source check report does not bind the reviewed execution and selected inputs')
    if with_test and value['testScript'] != intent['testScript']:
        raise Refused('Named test report does not bind the reviewed script')
    if selector is not None and (not with_test or value['testSelector'] != selector):
        raise Refused('Playwright report does not bind the reviewed test selection')
    store = review['store']
    runtime = value['runtime']
    if runtime is not None:
        fields(runtime, 'node npm cacache pacote contentVersionRaw contentVersion glibc', 'observed npm runtime')
        raw_version = runtime['contentVersionRaw']
        if (runtime['node'] != 'v22.23.3' or runtime['npm'] != '10.9.9' or runtime['cacache'] != '19.0.1'
                or runtime['pacote'] != '19.0.2' or not (raw_version == '2' or type(raw_version) is int and raw_version == 2)
                or type(runtime['contentVersion']) is not int or runtime['contentVersion'] != 2
                or not isinstance(runtime['glibc'], str) or not runtime['glibc']):
            raise Refused('Platform build runtime observation is unsupported')
    workspace = None if value['workspaceMount'] is None else mount_observation(value['workspaceMount'])
    executable_workspace = (workspace and workspace['type'] == 'tmpfs' and workspace['executable']
                            and {'rw', 'nosuid', 'nodev'} <= set(workspace['mountOptions']))
    build_tools = None if value['buildTools'] is None else build_tool_observation(value['buildTools'])
    materialized, installed = value['materialized'], value['installedPackages']
    install = None if value['install'] is None else operation(value['install'])
    discovery = None if value.get('discovery') is None else operation(value['discovery'])
    test = None if value.get('test') is None else operation(value['test'])
    build = None if value['build'] is None else operation(value['build'])
    error = value['error']
    browser_files = value.get('browserFiles')
    if browser is not None and (browser_files != browser['files']
            and (browser_files is not None or install is not None or error is None)):
        raise Refused('Platform report lacks matching in-container browser file measurements')
    if source_check:
        if (any(value[key] is not None for key in (
                'materialized', 'install', 'installedPackages', 'buildTools', 'build', 'artifacts'))
                or test is not None and (runtime is None or not executable_workspace)
                or error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 1000)):
            raise Refused('Source check cannot install dependencies, build or report artifacts')
    elif (materialized is not None and (runtime is None or type(materialized) is not int
                                     or materialized != store['tarballCount'])
            or install is not None and (materialized is None or not executable_workspace)
            or installed is not None and (not passed(install) or type(installed) is not int
                                          or installed != store['packageCount'])
            or build_tools is not None and installed is None
            or discovery is not None and build_tools is None
            or test is not None and (build_tools is None or selector is not None and not passed(discovery))
            or build is not None and (installed is None or build_tools is None or with_test and not passed(test))
            or error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 1000)):
        raise Refused('Platform build phases are inconsistent')
    if passed(discovery) and (test is not None or error is None):
        verify_playwright_report(decoded(value['discovery']['stdout'], MAX_OPERATION_LOG), selector, discovery=True)
    if selector is not None and passed(test) and (build is not None or error is None):
        verify_playwright_report(decoded(value['test']['stdout'], MAX_OPERATION_LOG), selector, discovery=False)
    artifacts = value['artifacts']
    source_passed = source_check and passed(test) and error is None
    if source_check:
        if source_passed:
            if (final['exitCode'] != 0 or final['oom'] or final.get('deadlineExceeded') is not False):
                raise Refused('Successful source check conflicts with the terminal result')
        elif (final['exitCode'] != 1 or final['oom']
              or error is None and (test is None or passed(test))):
            raise Refused('Failed source check conflicts with the terminal result')
    elif artifacts is not None:
        if (not passed(build) or error is not None or final['exitCode'] != 0 or final['oom']
                or final.get('deadlineExceeded') is not False):
            raise Refused('Successful build evidence conflicts with the terminal result')
        artifacts = artifact_inventory(artifacts, review['artifact'])
    elif (final['exitCode'] != 1 or final['oom']
          or (error is None and all(result is None or passed(result) for result in (install, discovery, test, build)))):
        raise Refused('Failed build evidence conflicts with the terminal result')
    status = 'source-check' if source_check else 'build-and-test' if with_test else 'build'
    result = {'status': status + ('-passed' if artifacts or source_passed else '-failed'), 'runtime': runtime,
              'workspaceMount': workspace, 'buildTools': build_tools, 'materializedTarballs': materialized,
              'installedPackages': installed, 'install': install, 'build': build, 'artifacts': artifacts,
              'error': error, 'evidenceSha256': sha256(raw).hexdigest()}
    if source_check:
        result.update(execution=review['execution'], sourceSelectionSha256=intent['binding']['sourceSelectionSha256'],
                      sourceProofSha256=intent['binding']['sourceProofSha256'])
    if with_test:
        result.update(testScript=value['testScript'], test=test)
    if selector is not None:
        result.update(testSelector=selector, discovery=discovery)
    if browser is not None:
        result.update(browserRuntime=browser, browserFiles=browser_files)
    return result


def assess_report(raw, intent, final):
    try:
        return validate_report(raw, intent, final)
    except Refused as error:
        return {'status': 'unverified', 'reason': str(error), 'evidenceSha256': sha256(raw).hexdigest()}


def report_limit(intent):
    """Keep bounded protocol overhead while allowing the reviewed artifact payload.

    Each content byte may occupy four base64 bytes when files are one byte long.
    This covers per-file padding without imposing an artifact file count quota.
    Metadata still has a protocol-resource envelope, including zero-byte file rows.
    """
    review = intent['platformBuild']
    maximum = 0 if 'execution' in review else review['artifact']['maximumBytes']
    if type(maximum) is not int or not 0 <= maximum <= MAX_SAFE_INTEGER:
        raise Refused('Invalid reviewed artifact report allowance')
    known = {'binding': intent['binding']}
    if 'execution' in review:
        known['execution'] = review['execution']
    return MAX_REPORT + 4 * maximum + len(canonical(known).encode())


def capture_report(directory, read_logs, intent, final):
    """Persist verified bytes before cleanup, allowing receipt reconciliation after a crash."""
    location = directory / EVIDENCE_NAME
    retained = os.path.lexists(location)
    limit = report_limit(intent)
    raw = read_owned(directory, EVIDENCE_NAME, limit) if retained else read_logs()
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise Refused('Invalid or oversized retained Docker build log')
    if not retained:
        with os.fdopen(owned_file(directory, EVIDENCE_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return assess_report(raw, intent, final)


def reconcile_report(directory, intent, final):
    if not os.path.lexists(directory / EVIDENCE_NAME):
        return None
    return assess_report(read_owned(directory, EVIDENCE_NAME, report_limit(intent)), intent, final)
