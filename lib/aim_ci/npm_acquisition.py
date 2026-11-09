"""Explicit, journaled acquisition of reviewed npm tarball bytes."""
from base64 import b64decode
from datetime import datetime, timezone
from hashlib import sha256
import http.client
import io
import ipaddress
import os
from queue import Empty, Queue
import re
import socket
import ssl
import stat
from threading import Lock, Thread
import time

from openai_budget import Refused, canonical, fingerprint
from .build_preparation import MAX_FILE, PreparationInputs, dependency_inventory
from .effects import EffectJournal
from .image_preparation import measure_file
from .npm_platform import platform_partition
from .operator_build import canonical_path, operator_lock, overlaps, validate_directory, write_state
from .owned_paths import new_owned_directory, owned_directory, owned_file, read_owned
from .platform_build import report_json
from .recipe_plan import fields


_HOST = 'registry.npmjs.org'
_PREFIX = f'https://{_HOST}/'
_URL_PATH = re.compile(r'((?:@[a-z0-9_.-]+/)?)([a-z0-9_][a-z0-9_.-]*)/-/([^/]+)\.tgz')
_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?')
_AUTH = 'acquisition-authorization.json'
_LOCK = '.aim-ci-npm-acquire.lock'
_LIMITS = {'maxRequests': 16, 'maxTarballBytes': 16 * 1024 * 1024, 'maxTotalTarballBytes': 256 * 1024 * 1024,
           'maxRequestSeconds': 25, 'maxTotalSeconds': 300, 'oversizeProbeBytesPerRequest': 1}


def registry_path(row):
    url, version = row['resolved'], row['version']
    path = url.removeprefix(_PREFIX)
    match = _URL_PATH.fullmatch(path) if url.startswith(_PREFIX) and len(url) <= 1024 else None
    if (match is None or not _VERSION.fullmatch(version) or '..' in path
            or match[3] != f'{match[2]}-{version}'):
        raise Refused(f"Unsupported locked tarball URL for {row['locator']}. Supply verified local bytes or use a canonical registry.npmjs.org HTTPS tarball.")
    return '/' + path


def time_left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Refused('npm acquisition deadline reached')
    return remaining


def public_address(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.sixtofour or ip.teredo or any(ip in network for network in (
                ipaddress.ip_network('64:ff9b::/96'), ipaddress.ip_network('64:ff9b:1::/48'))):
            return False
        ip = ip.ipv4_mapped or ip
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not getattr(ip, 'is_site_local', False)


def dial_registry(deadline):
    time_left(deadline)
    rows = socket.getaddrinfo(_HOST, 443, type=socket.SOCK_STREAM)
    if not 1 <= len(rows) <= 32 or any(family not in (socket.AF_INET, socket.AF_INET6)
            or kind != socket.SOCK_STREAM or proto not in (0, socket.IPPROTO_TCP) or address[1] != 443
            or not public_address(address[0]) or family == socket.AF_INET6 and address[3]
            for family, kind, proto, _, address in rows):
        raise Refused('npm registry DNS returned a nonpublic or unsupported address')
    family, socktype, proto, _, address = min(rows, key=lambda row: row[0] != socket.AF_INET)
    sock = socket.socket(family, socktype, proto)
    try:
        sock.settimeout(time_left(deadline))
        sock.connect(address)
        sock.settimeout(time_left(deadline))
        return sock
    except BaseException:
        sock.close()
        raise


def registry_bytes(path, limit, deadline, progress):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    connection = http.client.HTTPSConnection(_HOST, 443, timeout=time_left(deadline), context=context)
    # Keep TLS hostname validation, but dial only the checked numeric DNS address.
    connection._create_connection = lambda *_: dial_registry(deadline)
    try:
        connection.request('GET', path, headers={'Accept-Encoding': 'identity', 'Connection': 'close'})
        with connection.getresponse() as response:
            progress.record(httpStatus=response.status)
            if response.status != 200:
                raise Refused(f'npm registry returned HTTP {response.status}; redirects and retries are disabled')
            lengths = response.headers.get_all('Content-Length', [])
            encodings = response.headers.get_all('Content-Encoding', [])
            transfer = response.headers.get_all('Transfer-Encoding', [])
            if (len(lengths) > 1 or lengths and not re.fullmatch(r'[0-9]{1,12}', lengths[0])
                    or encodings not in ([], ['identity']) or transfer not in ([], ['chunked'])
                    or lengths and transfer):
                raise Refused('Unsupported npm registry response framing or encoding')
            expected = int(lengths[0]) if lengths else None
            if expected is not None and not 0 < expected <= limit:
                raise Refused('npm registry response exceeds its byte bound or is empty')
            data = bytearray()
            while True:
                time_left(deadline)
                block = response.read1(min(64 * 1024, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                progress.record(bodyBytesObserved=len(data))
                if len(data) > limit:
                    raise Refused('npm registry response exceeds its byte bound')
            if not data or expected is not None and len(data) != expected:
                raise Refused('npm registry response was empty or interrupted')
            progress.record(bodyByteCountComplete=True)
            time_left(deadline)
            return bytes(data)
    finally:
        connection.close()


class ResponseProgress:
    def __init__(self):
        self.lock = Lock()
        self.values = {'httpStatus': None, 'bodyBytesObserved': None, 'bodyByteCountComplete': False}

    def record(self, **values):
        with self.lock:
            self.values.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.values)


def fetch_bytes(row, limit, deadline, progress):
    path = registry_path(row)
    result = Queue(maxsize=1)

    def read():
        try:
            result.put(registry_bytes(path, limit, deadline, progress))
        except Refused as error:
            result.put(error)
        except (OSError, http.client.HTTPException, ValueError):
            result.put(Refused('npm registry transport failed; the request is not replayed'))

    # DNS and slow header reads are covered by the caller deadline as well as socket timeouts.
    # The worker cannot publish bytes, and the CLI exits on timeout without a second request.
    Thread(target=read, daemon=True).start()
    try:
        data = result.get(timeout=time_left(deadline))
    except Empty:
        raise Refused('npm registry request timed out; the request is not replayed') from None
    if isinstance(data, Refused):
        raise data
    time_left(deadline)
    return data


def file_stat(directory, name):
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def local_receipt(directory, name, integrity, *, links=1):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != links or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise Refused('Unsafe or unexpectedly linked npm tarball output')
        measured = measure_file(stream, _LIMITS['maxTarballBytes'], with_sri=True)
        if not measured['size'] or measured['integrity'] != integrity:
            raise Refused('Local npm tarball differs from the locked integrity; preserve it without overwrite')
        return {'file': name, **measured}


def retain_tarball(directory, name, staging, integrity):
    """Reconcile a verified staging file and publish without replacing a destination."""
    stage, target = file_stat(directory, staging), file_stat(directory, name)
    if target is not None and target.st_nlink == 1:
        return local_receipt(directory, name, integrity)
    if stage is None:
        if target is not None:
            raise Refused('npm tarball output has an unowned link')
        return None
    if target is None:
        local_receipt(directory, staging, integrity)
        os.link(staging, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        target = file_stat(directory, name)
    if (stage.st_dev, stage.st_ino) != (target.st_dev, target.st_ino):
        raise Refused('npm tarball publication does not match the owned staging file')
    local_receipt(directory, name, integrity, links=2)
    os.unlink(staging, dir_fd=directory)
    os.fsync(directory)
    return local_receipt(directory, name, integrity)


class NpmAcquisition(PreparationInputs):
    def __init__(self, root, args):
        super().__init__(root, args, acquisition=True)
        self.state = canonical_path(str(args.state), 'npm acquisition state')
        if any(path is not None and overlaps(self.state, path)
               for path in (root, self.checkout, self.tarballs, self.output, self.config_path, self.git)):
            raise Refused('npm acquisition state must be separate from project and preparation inputs')
        validate_directory(self.state.parent, private=False)
        validate_directory(self.tarballs, private=True)
        entries = {path: entry for path, entry in self.lock['packages'].items() if path}
        included, excluded = platform_partition(entries, self.config['platform'], root=self.lock['packages'].get(''))
        self.packages = {path: {'locator': path, **{key: entries[path][key] for key in ('version', 'resolved', 'integrity')}}
                         for path in sorted(included)}
        if args.locator is not None and args.locator not in self.packages:
            raise Refused('Requested npm locator is absent from the applicable platform set')
        self.excluded = len(excluded)

    def scope(self, directory):
        info = os.fstat(directory)
        return {'version': 1, 'effect': 'acquire-locked-npm-tarballs', 'registry': _PREFIX,
                'invocation': {'uid': os.getuid(), 'gid': os.getgid(), 'controller': str(self.root),
                               'config': str(self.config_path), 'configSha256': self.args.config_sha256,
                               'state': str(self.state), 'locator': self.args.locator},
                'manifestSha256': self.args.manifest_sha256, 'target': self.target, 'source': self.source_binding,
                'platform': self.config['platform'], 'fullLockVerified': False,
                'included': len(self.packages), 'excluded': self.excluded,
                'output': {'directory': str(self.tarballs), 'device': info.st_dev, 'inode': info.st_ino},
                'limits': _LIMITS, 'effects': {'network': 'public HTTPS GET only', 'redirects': False,
                    'automaticRetries': False, 'overwrite': False, 'installation': False}}

    def request(self, row):
        registry_path(row)
        key = row['integrity']
        return {'package': row, 'outputFile': b64decode(key[7:]).hex() + '.tgz',
                'alsoSatisfies': [item for item in self.packages.values() if item != row and item['integrity'] == key]}

    def plan(self, directory):
        if os.path.lexists(self.state):
            raise Refused('npm acquisition state already exists. Use --resume with its original inputs.')
        _, blobs, _, missing = dependency_inventory(directory, self.lock,
                                                  self.committed['package-lock.json'], self.config['platform'])
        rows = [self.packages[item['locator']] for item in missing]
        candidates = [row for row in rows if self.args.locator in (None, row['locator'])]
        unique = {}
        for row in candidates:
            if row['integrity'] not in unique:
                unique[row['integrity']] = self.request(row)
        requests = list(unique.values())[:_LIMITS['maxRequests']]
        for request in requests:
            if file_stat(directory, request['outputFile']) is not None:
                raise Refused('A requested npm output filename is already occupied by unrelated bytes')
        self.before_request(directory)
        return {**self.scope(directory), 'requests': requests}, {
            'missingPlatformLocators': len(rows), 'deferredTarballs': len(unique) - len(requests),
            'verifiedLocalTarballs': [blobs[key] for key in sorted(blobs)]}

    def check_output(self, directory):
        current = owned_directory(self.tarballs)
        try:
            original, now = os.fstat(directory), os.fstat(current)
            if ((original.st_dev, original.st_ino) != (now.st_dev, now.st_ino)
                    or now.st_uid != os.geteuid() or stat.S_IMODE(now.st_mode) & 0o077):
                raise Refused('npm tarball output changed after review')
        finally:
            os.close(current)

    def before_request(self, directory):
        self.verify_inputs()
        self.reader.checked_object('commit', self.reader.commit, MAX_FILE)
        self.reader.checked_object('tree', self.reader.tree, MAX_FILE)
        for name in self.committed:
            self.reader.checked_object('blob', self.source[name]['blob'].hex(), MAX_FILE)
        self.check_output(directory)

    def record(self, key, phase, details):
        name = f'{key}-{phase}.json'
        entry = {**details, 'observedAt': datetime.now(timezone.utc).isoformat()}
        if os.path.lexists(self.state/name):
            retained = report_json(read_owned(self.state, name))
            if not isinstance(retained, dict) or {k: v for k, v in retained.items() if k != 'observedAt'} != details:
                raise Refused('Retained npm acquisition observation changed')
        else:
            write_state(self.state, name, canonical(entry).encode())

    def retained_authorization(self, directory):
        auth = report_json(read_owned(self.state, _AUTH))
        fields(auth, 'intent intentSha256 authorizedAt deadlineUnix', 'npm acquisition authorization')
        intent = auth['intent']
        if not isinstance(intent, dict) or fingerprint(intent) != auth['intentSha256']:
            raise Refused('Retained npm acquisition authorization changed')
        requests = intent.get('requests')
        if (not isinstance(requests, list) or not 1 <= len(requests) <= _LIMITS['maxRequests']
                or intent != {**self.scope(directory), 'requests': requests}
                or not isinstance(auth['authorizedAt'], str) or type(auth['deadlineUnix']) is not int):
            raise Refused('Retained npm acquisition does not bind these exact inputs')
        try:
            issued = datetime.fromisoformat(auth['authorizedAt'])
            if issued.tzinfo is None or auth['deadlineUnix'] != int(issued.timestamp()) + _LIMITS['maxTotalSeconds']:
                raise ValueError('invalid deadline')
        except (OverflowError, ValueError):
            raise Refused('Retained npm acquisition deadline is invalid') from None
        used = set()
        for request in requests:
            if not isinstance(request, dict) or not isinstance(request.get('package'), dict):
                raise Refused('Invalid retained npm acquisition request')
            locator = request['package'].get('locator')
            if (not isinstance(locator, str) or locator not in self.packages or self.args.locator not in (None, locator)
                    or request != self.request(self.packages[locator]) or request['outputFile'] in used):
                raise Refused('Retained npm acquisition request differs from the pinned lock')
            used.add(request['outputFile'])
        return auth

    def execute(self, directory):
        with operator_lock(self.state), operator_lock(directory, name=_LOCK):
            self.check_output(directory)
            auth = self.retained_authorization(directory)
            intent_pin = auth['intentSha256']
            journal = EffectJournal(self.state/'journal', {'acquisitionIntentSha256': intent_pin})
            deadline = time.monotonic() + min(_LIMITS['maxTotalSeconds'], auth['deadlineUnix'] - time.time())
            receipts, total = [], 0
            for request in auth['intent']['requests']:
                package, name = request['package'], request['outputFile']
                key = sha256(package['integrity'].encode()).hexdigest()
                staging = f'.aim-ci-{intent_pin[:24]}-{key}.part'

                def observe(operation, _):
                    receipt = retain_tarball(directory, name, staging, package['integrity'])
                    if receipt is not None:
                        self.record(key, 'reconciled', {'requestSha256': fingerprint(request), 'mode': 'local-reconciliation',
                            'outcome': 'verified-retained-file', 'networkThisObservation': False, 'file': receipt})
                    return ({'state': 'completed', 'receipt': journal.receipt(operation, fingerprint(request), receipt)}
                            if receipt is not None else {'state': 'unknown'})

                def dispatch(operation, _):
                    request_pin = fingerprint(request)
                    receipt = retain_tarball(directory, name, staging, package['integrity'])
                    if receipt is None:
                        maximum = min(_LIMITS['maxTarballBytes'], _LIMITS['maxTotalTarballBytes'] - total)
                        if maximum <= 0:
                            raise Refused('npm acquisition exhausted its total byte bound')
                        expires = min(deadline, time.monotonic() + _LIMITS['maxRequestSeconds'])
                        time_left(expires)
                        start = {'requestSha256': request_pin, 'mode': 'registry-attempt',
                                 'outcome': 'dispatch-pending-or-in-flight'}
                        self.record(key, 'attempt', start)
                        progress = ResponseProgress()
                        try:
                            data = fetch_bytes(package, maximum, expires, progress)
                            measured = measure_file(io.BytesIO(data), maximum, with_sri=True)
                            if not measured['size'] or measured['integrity'] != package['integrity']:
                                raise Refused('Downloaded npm tarball failed locked SHA-512 integrity')
                            self.before_request(directory)
                            with os.fdopen(owned_file(directory, staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
                                stream.write(data)
                                stream.flush()
                                os.fsync(stream.fileno())
                            receipt = retain_tarball(directory, name, staging, package['integrity'])
                        except (KeyboardInterrupt, OSError, Refused) as error:
                            self.record(key, 'result', {**start, **progress.snapshot(),
                                'outcome': 'failed-or-interrupted',
                                'error': str(error) if isinstance(error, Refused) else type(error).__name__})
                            raise
                        self.record(key, 'result', {**start, **progress.snapshot(), 'outcome': 'verified-and-retained',
                            'file': receipt})
                    else:
                        self.record(key, 'result', {'requestSha256': request_pin, 'mode': 'local-file-reuse',
                            'outcome': 'verified-retained-file', 'networkThisObservation': False, 'file': receipt})
                    return journal.receipt(operation, request_pin, receipt)

                self.before_request(directory)
                receipt = journal.perform('tarball:' + key, request, dispatch, lookup=observe)
                if receipt != local_receipt(directory, name, package['integrity']):
                    raise Refused('Retained npm tarball receipt differs from the current file')
                total += receipt['size']
                if total > _LIMITS['maxTotalTarballBytes']:
                    raise Refused('npm acquisition exceeded its total retained byte bound')
                receipts.append(receipt)
            self.check_output(directory)
            return {'status': 'acquisition-batch-retained', 'executionAuthorized': False,
                    'intentSha256': intent_pin, 'fullLockVerified': False, 'files': receipts,
                    'verifiedBytes': total, 'evidenceDirectory': str(self.state)}

    def run(self):
        directory = owned_directory(self.tarballs)
        try:
            self.check_output(directory)
            if not self.args.resume:
                intent, summary = self.plan(directory)
                intent_pin = fingerprint(intent)
                if self.args.preview:
                    return {'status': 'acquisition-preview' if intent['requests'] else 'acquisition-no-missing-selection',
                            'executionAuthorized': False, 'intent': intent,
                            'intentSha256': intent_pin if intent['requests'] else None,
                            **summary}
                if not intent['requests'] or self.args.authorize_intent != intent_pin:
                    raise Refused('Explicit npm acquisition approval must match the exact nonempty preview intent SHA-256')
                self.verify_inputs()
                issued = datetime.now(timezone.utc)
                with new_owned_directory(self.state) as state:
                    write_state(state, _AUTH, canonical({'intent': intent, 'intentSha256': intent_pin,
                        'authorizedAt': issued.isoformat(),
                        'deadlineUnix': int(issued.timestamp()) + _LIMITS['maxTotalSeconds']}).encode())
            validate_directory(self.state, private=True)
            return self.execute(directory)
        finally:
            os.close(directory)
