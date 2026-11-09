"""Network-free npm acquisition protocol, retention and recovery regressions."""
from base64 import b64encode
from email.message import Message
from hashlib import sha512
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci import npm_acquisition as acquisition
from aim_ci.operator_build import operator_lock
from openai_budget import Refused


HOST = 'registry.npmjs.org'
BYTES = b'fixture compressed bytes; acquisition does not extract them'
PLATFORM = {'os': 'linux', 'cpu': 'arm64', 'libc': 'glibc'}


def package(name, data=BYTES):
    return {'locator': 'node_modules/' + name, 'version': '1.0.0',
            'resolved': f'https://{HOST}/{name}/-/{name.rsplit("/", 1)[-1]}-1.0.0.tgz',
            'integrity': 'sha512-' + b64encode(sha512(data).digest()).decode()}


def response(status, data, headers):
    class Response(io.BytesIO):
        pass

    result = Response(data)
    result.status = status
    result.headers = Message()
    for name, value in headers:
        result.headers[name] = value
    return result


class RegistryProtocolTests(unittest.TestCase):
    def test_only_canonical_credential_free_locked_tgz_urls_are_accepted(self):
        row = package('@scope/tool')
        self.assertEqual(acquisition.registry_path(row), '/@scope/tool/-/tool-1.0.0.tgz')
        for url in (row['resolved'].replace(HOST, 'other.example'), row['resolved'].replace(HOST, '127.0.0.1'),
                    row['resolved'].replace(HOST, 'user:secret@' + HOST), row['resolved'].replace(HOST, HOST+':443'),
                    row['resolved'].replace('https:', 'http:'), row['resolved'].replace('tool-1', 'tool-2'),
                    row['resolved'].replace('/-/', '/%2e%2e/-/'), row['resolved']+'?token=fixture',
                    row['resolved']+'#fragment', row['resolved'].replace('.tgz', '.json')):
            with self.subTest(url=url), self.assertRaisesRegex(Refused, 'Unsupported locked tarball URL'):
                acquisition.registry_path({**row, 'resolved': url})

    def test_rejects_private_or_mixed_dns_before_opening_socket(self):
        rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:127.0.0.1', 443, 0, 0))]
        with patch.object(socket, 'getaddrinfo', return_value=rows) as dns, \
                patch.object(socket, 'socket', side_effect=AssertionError('No socket allowed')):
            with self.assertRaisesRegex(Refused, 'nonpublic'):
                acquisition.dial_registry(time.monotonic()+1)
            dns.assert_called_once_with(HOST, 443, type=socket.SOCK_STREAM)
        for ip in ('0.0.0.0', '10.1.2.3', '224.0.0.1', '64:ff9b::7f00:1', '2002:7f00:1::1'):
            self.assertFalse(acquisition.public_address(ip), ip)

    def test_checked_address_is_dialed_directly(self):
        address = ('1.1.1.1', 443)
        rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', address)]
        with patch.object(socket, 'getaddrinfo', return_value=rows), patch.object(socket, 'socket') as sock:
            result = acquisition.dial_registry(time.monotonic()+1)
            self.assertIs(result, sock.return_value)
            sock.return_value.connect.assert_called_once_with(address)

    def test_fixed_https_get_and_response_bounds(self):
        path = '/example/-/example-1.0.0.tgz'
        for status, body, headers, limit, error in (
            (200, BYTES, [('Content-Length', str(len(BYTES)))], len(BYTES), None),
            (302, BYTES, [('Location', 'https://private.invalid/')], len(BYTES), 'redirects'),
            (200, BYTES, [('Content-Encoding', 'gzip')], len(BYTES), 'encoding'),
            (200, BYTES, [('Content-Length', '100000')], len(BYTES), 'byte bound'),
            (200, BYTES, [], len(BYTES)-1, 'byte bound'),
            (200, BYTES, [('Content-Length', str(len(BYTES)+1))], len(BYTES)+1, 'interrupted'),
        ):
            with self.subTest(error=error), patch.object(acquisition.http.client, 'HTTPSConnection') as connect:
                progress = acquisition.ResponseProgress()
                client = connect.return_value
                client.getresponse.return_value = response(status, body, headers)
                if error:
                    with self.assertRaisesRegex(Refused, error):
                        acquisition.registry_bytes(path, limit, time.monotonic()+1, progress)
                else:
                    self.assertEqual(acquisition.registry_bytes(path, limit, time.monotonic()+1, progress), BYTES)
                    self.assertTrue(progress.snapshot()['bodyByteCountComplete'])
                self.assertEqual(connect.call_args.args, (HOST, 443))
                context = connect.call_args.kwargs['context']
                self.assertTrue(context.check_hostname)
                self.assertIsNone(context.keylog_filename)
                client.request.assert_called_once_with('GET', path,
                    headers={'Accept-Encoding': 'identity', 'Connection': 'close'})
                client.close.assert_called_once()
                self.assertEqual(progress.snapshot()['httpStatus'], status)
                if status == 302:
                    self.assertIsNone(progress.snapshot()['bodyBytesObserved'])

    def test_caller_timeout_does_not_invent_body_bytes_or_accept_late_result(self):
        release, finished = Event(), Event()

        def late(*_):
            release.wait(1)
            finished.set()
            return BYTES

        progress = acquisition.ResponseProgress()
        with patch.object(acquisition, 'registry_bytes', side_effect=late) as fetch:
            try:
                with self.assertRaisesRegex(Refused, 'timed out'):
                    acquisition.fetch_bytes(package('example'), len(BYTES), time.monotonic()+0.05, progress)
                self.assertEqual(progress.snapshot(), {'httpStatus': None, 'bodyBytesObserved': None,
                                                       'bodyByteCountComplete': False})
                fetch.assert_called_once()
            finally:
                release.set()
                self.assertTrue(finished.wait(1))


class NpmAcquisitionContractTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.output = self.root/'tarballs'
        self.output.mkdir(mode=0o700)
        self.state = self.root/'state'
        self.job = object.__new__(acquisition.NpmAcquisition)
        self.job.root = self.root/'control'
        self.job.state = self.state
        self.job.tarballs = self.output
        self.job.args = SimpleNamespace(preview=True, resume=False, authorize_intent=None, locator=None,
            manifest_sha256='1'*64, config_sha256='2'*64)
        self.job.config_path = self.root/'config.json'
        self.job.target = {'commit': 'a'*40, 'tree': 'b'*40}
        self.job.config = {'platform': PLATFORM}
        self.job.committed = {'package-lock.json': '3'*64}
        self.job.source_binding = {'committedInputs': self.job.committed}
        self.job.excluded = 0
        self.job.packages = {row['locator']: row for row in (package('example'), package('present', b'existing'))}
        self.job.lock = {'packages': {'': {}, **{key: {k: v for k, v in row.items() if k != 'locator'}
                                               for key, row in self.job.packages.items()}}}
        self.job.verify_inputs = lambda: None
        self.job.before_request = self.job.check_output
        (self.output/'arbitrary-name.tgz').write_bytes(b'existing')

    def approve(self, fetch):
        before = set(self.output.iterdir())
        preview = self.job.run()
        self.assertEqual(set(self.output.iterdir()), before)
        self.assertFalse(self.state.exists())
        self.assertEqual(preview['status'], 'acquisition-preview')
        self.assertFalse(preview['executionAuthorized'])
        self.assertEqual(preview['intent']['requests'][0]['package'], package('example'))
        self.assertEqual(preview['missingPlatformLocators'], 1)
        self.assertEqual([row['name'] for row in preview['verifiedLocalTarballs']], ['arbitrary-name.tgz'])
        self.job.args.preview = False
        self.job.args.authorize_intent = '0'*64
        with self.assertRaisesRegex(Refused, 'exact nonempty preview'):
            self.job.run()
        self.assertFalse(self.state.exists())
        fetch.assert_not_called()
        self.job.args.authorize_intent = preview['intentSha256']
        return preview['intent']['requests'][0]['outputFile']

    @staticmethod
    def http_success(_, __, ___, progress, data=BYTES):
        progress.record(httpStatus=200, bodyBytesObserved=len(data), bodyByteCountComplete=True)
        return data

    def evidence(self, phase):
        path, = self.state.glob(f'*-{phase}.json')
        return json.loads(path.read_bytes())

    def test_approved_verified_bytes_are_reused_and_tampered_receipt_refuses(self):
        with patch.object(acquisition, 'fetch_bytes', side_effect=self.http_success) as fetch:
            name = self.approve(fetch)
            outcome = self.job.run()
            fetch.assert_called_once()
            self.assertFalse(outcome['fullLockVerified'])
            self.assertFalse(outcome['executionAuthorized'])
            self.assertEqual((self.output/name).read_bytes(), BYTES)
            self.assertEqual((self.output/'arbitrary-name.tgz').read_bytes(), b'existing')
            observed = self.evidence('result')
            self.assertEqual((observed['mode'], observed['httpStatus'], observed['bodyBytesObserved']),
                             ('registry-attempt', 200, len(BYTES)))
            self.assertTrue(observed['bodyByteCountComplete'])
            self.job.args.resume = True
            self.assertEqual(self.job.run(), outcome)
            fetch.assert_called_once()
            (self.output/name).write_bytes(b'tampered')
            with self.assertRaisesRegex(Refused, 'differs from the locked integrity'):
                self.job.run()
            fetch.assert_called_once()

    def test_integrity_failure_preserves_actual_http_and_never_replays(self):
        def bad(*args):
            return self.http_success(*args, data=b'bad body')

        with patch.object(acquisition, 'fetch_bytes', side_effect=bad) as fetch:
            name = self.approve(fetch)
            with self.assertRaisesRegex(Refused, 'failed locked SHA-512'):
                self.job.run()
            self.assertFalse((self.output/name).exists())
            observed = self.evidence('result')
            self.assertEqual((observed['httpStatus'], observed['bodyBytesObserved']), (200, len(b'bad body')))
            self.assertTrue(observed['bodyByteCountComplete'])
            self.assertIn('integrity', observed['error'])
            self.job.args.resume = True
            with self.assertRaisesRegex(Refused, 'do not resend'):
                self.job.run()
            fetch.assert_called_once()

    def test_transport_failure_records_status_without_claiming_a_body(self):
        def refuse(_, __, ___, progress):
            progress.record(httpStatus=302)
            raise Refused('npm registry returned HTTP 302; redirects and retries are disabled')

        with patch.object(acquisition, 'fetch_bytes', side_effect=refuse) as fetch:
            name = self.approve(fetch)
            with self.assertRaisesRegex(Refused, 'HTTP 302'):
                self.job.run()
            observed = self.evidence('result')
            self.assertEqual(observed['httpStatus'], 302)
            self.assertIsNone(observed['bodyBytesObserved'])
            self.assertFalse(observed['bodyByteCountComplete'])
            self.assertFalse((self.output/name).exists())

    def test_verified_file_arriving_before_dispatch_is_observed_as_local_reuse(self):
        with patch.object(acquisition, 'fetch_bytes', side_effect=AssertionError('No network')) as fetch:
            name = self.approve(fetch)

            def after_claim(directory):
                self.job.check_output(directory)
                if self.state.exists():
                    (self.output/name).write_bytes(BYTES)

            self.job.before_request = after_claim
            self.job.run()
            fetch.assert_not_called()
            observed = self.evidence('result')
            self.assertEqual(observed['mode'], 'local-file-reuse')
            self.assertFalse(observed['networkThisObservation'])

    def test_interrupted_link_is_reconciled_without_network_or_overwrite(self):
        unlink = os.unlink

        def interrupt(path, **kwargs):
            if str(path).endswith('.part'):
                raise KeyboardInterrupt
            return unlink(path, **kwargs)

        with patch.object(acquisition, 'fetch_bytes', side_effect=self.http_success) as fetch:
            name = self.approve(fetch)
            with patch.object(acquisition.os, 'unlink', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                self.job.run()
            self.assertEqual((self.output/name).stat().st_nlink, 2)
            self.assertEqual(self.evidence('result')['outcome'], 'failed-or-interrupted')
            self.job.args.resume = True
            self.job.run()
            fetch.assert_called_once()
            self.assertEqual((self.output/name).stat().st_nlink, 1)
            self.assertEqual((self.output/name).read_bytes(), BYTES)
            self.assertEqual(self.evidence('reconciled')['mode'], 'local-reconciliation')

    def test_collision_and_exclusive_output_claim_are_fail_closed(self):
        with patch.object(acquisition, 'fetch_bytes', side_effect=AssertionError('No network')) as fetch:
            name = self.job.run()['intent']['requests'][0]['outputFile']
            (self.output/name).write_bytes(b'owned but unrelated')
            with self.assertRaisesRegex(Refused, 'occupied'):
                self.job.run()
            self.assertEqual((self.output/name).read_bytes(), b'owned but unrelated')
            (self.output/name).unlink()
            self.approve(fetch)
            with operator_lock(self.output, name=acquisition._LOCK):
                with self.assertRaisesRegex(Refused, 'active controller'):
                    self.job.run()
            self.assertTrue(self.state.is_dir())
            fetch.assert_not_called()
