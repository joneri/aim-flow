"""Durable effect identity and reconciliation, independent of product/vendor.

Completing a transport effect does not establish product or owner acceptance.
Unknown outcomes remain unknown unless a matching durable receipt is observed.
"""
import copy
import fcntl
import json
import os
from pathlib import Path
import re

from openai_budget import Refused, atomic_json, fingerprint, safe_path


class EffectJournal:
    def __init__(self, directory, binding, *, synthetic=False, read_only=False):
        self.directory = Path(directory)
        safe_path(self.directory)
        self.read_only = read_only
        if not read_only:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'effects.json'
        self.marker = self.directory / 'effects-established.json'
        self.identity = {'version': 2, 'bindingSha256': fingerprint(binding), 'synthetic': synthetic}

    def _read(self, *, create=True):
        for path in (self.path, self.marker):
            safe_path(path)
        if self.marker.exists():
            if not self.path.exists():
                raise Refused('Established effect journal missing; reconcile without replay')
            if json.loads(self.marker.read_text()) != self.identity:
                raise Refused('Effect scope/profile/mode changed')
            data = json.loads(self.path.read_text())
            if {k: data.get(k) for k in self.identity} != self.identity:
                raise Refused('Effect journal identity changed')
            return data
        if self.path.exists():
            raise Refused('Unbound effect journal; do not adopt or reset')
        if not create:
            return None
        atomic_json(self.marker, self.identity)
        data = {**self.identity, 'operations': {}}
        atomic_json(self.path, data)
        return data

    def inspect(self, name):
        data = self._read(create=False)
        return copy.deepcopy(data['operations'].get(name)) if data else None

    def completed_result(self, name, intent):
        entry = self.inspect(name)
        request_sha = fingerprint(intent)
        key = fingerprint({'binding': self.identity, 'name': name, 'requestSha256': request_sha})
        if (not entry or entry.get('state') != 'completed' or entry.get('intent') != intent
                or entry.get('requestSha256') != request_sha or entry.get('operationKey') != key):
            raise Refused('Missing or mismatched completed effect')
        return self._result(entry.get('receipt'), key, request_sha)

    @staticmethod
    def receipt(key, request_sha, result):
        return {'operationKey': key, 'requestSha256': request_sha,
                'status': 'completed', 'result': result}

    def perform(self, name, intent, dispatch, *, lookup=None, confirmed_absence=False, fault=None):
        """Dispatch or reconcile; a missing dispatch callback permits observation only."""
        if self.read_only:
            raise Refused('Read-only effect verification cannot dispatch or reconcile')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9:._-]{0,100}', name):
            raise Refused('Invalid logical effect name')
        request_sha = fingerprint(intent)
        key = fingerprint({'binding': self.identity, 'name': name, 'requestSha256': request_sha})
        lock = self.directory / 'effects.lock'
        safe_path(lock)
        with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('Effect already owned by another worker') from None
            data = self._read(create=dispatch is not None)
            entries = data['operations'] if data else {}
            previous = entries.get(name)
            if previous:
                if previous.get('operationKey') != key or previous.get('requestSha256') != request_sha:
                    raise Refused('Logical effect input changed; no new dispatch')
                if fingerprint(previous.get('intent')) != request_sha:
                    raise Refused('Retained effect intent changed')
                if previous['state'] == 'completed':
                    return self._result(previous['receipt'], key, request_sha)
                observed = lookup(key, copy.deepcopy(intent)) if lookup else {'state': 'unknown'}
                if not isinstance(observed, dict):
                    raise Refused('Invalid reconciliation observation')
                if observed.get('state') == 'completed':
                    result = self._result(observed.get('receipt'), key, request_sha)
                    previous.update(state='completed', receipt=observed['receipt'], reconciled=True)
                    atomic_json(self.path, data)
                    return result
                if observed.get('state') != 'absent' or not confirmed_absence or dispatch is None:
                    raise Refused('Effect outcome unknown; retain resources and do not resend')
            else:
                if dispatch is None:
                    raise Refused('No retained effect to reconcile; do not dispatch on resume')
                if len(entries) >= 64:
                    raise Refused('Effect history bound reached')
                previous = {'operationKey': key, 'requestSha256': request_sha,
                            'intent': copy.deepcopy(intent), 'state': 'pending'}
                entries[name] = previous
                atomic_json(self.path, data)  # Before the external effect.
                if fault:
                    fault('after-intent', name)
            try:
                receipt = dispatch(key, copy.deepcopy(intent))
                if fault:
                    fault('after-dispatch', name)
                result = self._result(receipt, key, request_sha)
                previous.update(state='completed', receipt=receipt)
                atomic_json(self.path, data)
                if fault:
                    fault('after-record', name)
                return result
            except Exception as error:
                previous.update(state='unknown', failureClass=type(error).__name__)
                atomic_json(self.path, data)
                raise

    @staticmethod
    def _result(receipt, key, request_sha):
        if (not isinstance(receipt, dict)
                or set(receipt) != {'operationKey', 'requestSha256', 'status', 'result'}
                or receipt.get('status') != 'completed'
                or receipt.get('operationKey') != key or receipt.get('requestSha256') != request_sha):
            raise Refused('Incomplete, ignored, skipped or mismatched effect receipt')
        return copy.deepcopy(receipt['result'])
