"""One aggregate ceiling, including maximum remaining legacy liability."""
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from openai_budget import LIMIT, MODEL, MAX_CALLS, MAX_OUTPUT, INPUT_RATE, OUTPUT_RATE, Refused, atomic_json, fingerprint, read_owned, safe_path, provider_token_bounds
from .owned_paths import owned_directory, owned_file


AMENDMENT_EXTRA = 10_000_000_000
AMENDMENT_MAX = 50_000_000
ORIGINAL_LEDGER = 'budget-before-amendment.json'
CONTINUATION_LEDGER = 'budget-before-continuation.json'
PROJECT_GRANT_VERSION = 3


def amendment_slot(grant):
    if grant['version'] == PROJECT_GRANT_VERSION:
        raise Refused('Project grants have per-operation claims, not Liam amendment slots')
    if grant['version'] == 2:
        return 'continuation', CONTINUATION_LEDGER, 17
    return 'amendment', ORIGINAL_LEDGER, 16


def verify_amendment(grant):
    hashes = ('originalLedgerSha256', 'markerSha256', 'legacyBytesSha256', 'scopeSha256', 'requestSha256')
    version = grant.get('version') if isinstance(grant, dict) else None
    if type(version) is not int or version not in (1, 2, PROJECT_GRANT_VERSION):
        raise Refused('Invalid single-operation budget amendment')
    if version == 2:
        hashes += ('previousStateSha256', 'previousReceiptSha256')
    extra = set()
    if version == PROJECT_GRANT_VERSION:
        hashes += ('taskSha256', 'sourceSha256')
        extra = {'target', 'maxLedgerCalls', 'maxLedgerNanoUsd'}
        target = grant.get('target')
        if (not isinstance(target, dict) or set(target) != {'repository', 'branch', 'commit', 'tree'}
                or not isinstance(target['repository'], str)
                or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git', target['repository'])
                or not isinstance(target['branch'], str) or not target['branch']
                or any(not isinstance(target[key], str) or not re.fullmatch('[a-f0-9]{40}', target[key])
                       for key in ('commit', 'tree'))
                or type(grant.get('maxLedgerCalls')) is not int or grant['maxLedgerCalls'] < 1
                or type(grant.get('maxLedgerNanoUsd')) is not int
                or grant['maxLedgerNanoUsd'] not in (LIMIT, LIMIT + AMENDMENT_EXTRA)):
            raise Refused('Invalid project-bound budget grant')
    if (set(grant) != {*hashes, *extra, 'version', 'operation', 'model',
            'extraNanoUsd', 'maxOperationNanoUsd', 'extraGenerations'}
            or any(not isinstance(grant[key], str) or not re.fullmatch('[a-f0-9]{64}', grant[key]) for key in hashes)
            or type(grant['extraGenerations']) is not int
            or grant['extraGenerations'] not in ((0, 1) if version == PROJECT_GRANT_VERSION else (1,))
            or type(grant['extraNanoUsd']) is not int or grant['extraNanoUsd'] != (AMENDMENT_EXTRA if version == 1 else 0)
            or type(grant['maxOperationNanoUsd']) is not int
            or not (1 <= grant['maxOperationNanoUsd'] <= AMENDMENT_MAX if version == PROJECT_GRANT_VERSION
                    else grant['maxOperationNanoUsd'] == AMENDMENT_MAX)
            or grant['model'] != MODEL or not isinstance(grant['operation'], str)
            or not re.fullmatch(r'task-[a-f0-9]{64}/intake', grant['operation'])):
        raise Refused('Invalid single-operation budget amendment')


def verify_budget_prefix(grant, original, marker, legacy):
    verify_amendment(grant)
    if any(sha256(raw).hexdigest() != grant[key] for raw, key in (
            (original, 'originalLedgerSha256'), (marker, 'markerSha256'), (legacy, 'legacyBytesSha256'))):
        raise Refused('Budget amendment original history or legacy bytes differ')


def reservation(tokens, output):
    if (type(tokens) is not int or tokens < 0
            or type(output) is not int or output < 1):
        raise Refused('Invalid exact token count')
    return (tokens + 1024) * INPUT_RATE + output * OUTPUT_RATE


def completed_amendment(data):
    return ('amendment' in data and bool(data['requests'])
            and data['requests'][-1].get('status') == 'completed'
            and data['requests'][-1].get('responseStatus') == 'completed')


def checked_prefix(directory, data, slot, backup, previous_slots, marker, legacy):
    if slot not in data:
        if os.path.lexists(directory / backup):
            raise Refused('Budget amendment backup exists without its claim; reconcile without spending')
        return data
    grant = data[slot]
    original = read_owned(directory, backup)
    verify_budget_prefix(grant, original, marker, legacy)
    prefix = json.loads(original)
    requests = data['requests']
    if (amendment_slot(grant) != (slot, backup, previous_slots) or not requests
            or {**{k: v for k, v in data.items() if k != slot}, 'requests': requests[:-1]} != prefix
            or len(requests[:-1]) + data['legacyCalls'] != previous_slots
            or any(row['id'] == grant['operation'] for row in requests[:-1])
            or (slot == 'continuation' and not completed_amendment(prefix))):
        raise Refused('Amended budget no longer matches its original request prefix')
    entry = requests[-1]
    if (entry.get('id') != grant['operation'] or entry.get('payloadSha256') != grant['requestSha256']
            or type(entry.get('reservedNanoUsd')) is not int
            or entry['reservedNanoUsd'] != reservation(100000, MAX_OUTPUT)
            or entry.get('status') not in ('reserved', 'unknown', 'completed')
            or (entry['status'] == 'completed' and 'inputTokens' not in entry)
            or data['legacyReservedNanoUsd'] + sum(row['reservedNanoUsd'] for row in requests) > LIMIT + AMENDMENT_EXTRA):
        raise Refused('Invalid claimed budget amendment reservation')
    return prefix


def project_grant_backup(grant):
    verify_amendment(grant)
    if grant['version'] != PROJECT_GRANT_VERSION:
        raise Refused('Invalid project budget claim version')
    return 'budget-before-' + grant['operation'].removesuffix('/intake') + '.json'


def verified_history(directory, data, marker, legacy):
    """Validate every grant against its immutable prefix, including old Liam receipts."""
    requests = data['requests']
    project = [index for index, row in enumerate(requests) if 'projectGrant' in row]
    named = [requests.index(row) for slot in ('amendment', 'continuation') if slot in data
             for row in requests if row.get('id') == data[slot]['operation']]
    if project and (not named or project[-1] > max(named)):
        index = project[-1]
        entry = requests[index]
        grant = entry['projectGrant']
        verify_amendment(grant)
        if grant['version'] != PROJECT_GRANT_VERSION:
            raise Refused('Invalid project budget claim version')
        raw = read_owned(directory, project_grant_backup(grant))
        verify_budget_prefix(grant, raw, marker, legacy)
        prefix = json.loads(raw)
        if {**data, 'requests': requests[:index]} != prefix:
            raise Refused('Project budget claim no longer matches its original prefix')
        limit, calls = verified_history(directory, prefix, marker, legacy)
        if (grant['maxLedgerNanoUsd'] != limit or grant['maxLedgerCalls'] != calls + grant['extraGenerations']
                or grant['extraGenerations'] == 1 and len(prefix['requests']) + prefix['legacyCalls'] != calls
                or any(row['id'] == grant['operation'] for row in requests[:index])
                or entry.get('id') != grant['operation'] or entry.get('payloadSha256') != grant['requestSha256']
                or type(entry.get('reservedNanoUsd')) is not int
                or (entry['reservedNanoUsd'] != (grant['maxOperationNanoUsd']
                    if entry.get('reservationPolicy') == 'approved-operation-cap-v1'
                    else reservation(100000, MAX_OUTPUT)))
                or entry.get('reservationPolicy') not in (None, 'approved-operation-cap-v1')
                or entry.get('reservationPolicy') == 'approved-operation-cap-v1' and (
                    type(entry.get('maxOutputTokens')) is not int or entry['maxOutputTokens'] < 1
                    or reservation(0, entry['maxOutputTokens']) > entry['reservedNanoUsd'])
                or entry['reservedNanoUsd'] > grant['maxOperationNanoUsd']
                or entry.get('status') not in ('reserved', 'unknown', 'completed')
                or entry['status'] == 'completed' and 'inputTokens' not in entry):
            raise Refused('Invalid project-bound budget reservation')
        calls = grant['maxLedgerCalls']
    elif 'continuation' in data:
        prefix = checked_prefix(directory, data, 'continuation', CONTINUATION_LEDGER, 17, marker, legacy)
        verified_history(directory, prefix, marker, legacy)
        limit, calls = LIMIT + AMENDMENT_EXTRA, 18
    elif 'amendment' in data:
        prefix = checked_prefix(directory, data, 'amendment', ORIGINAL_LEDGER, 16, marker, legacy)
        verified_history(directory, prefix, marker, legacy)
        limit, calls = LIMIT + AMENDMENT_EXTRA, 17
    else:
        limit, calls = LIMIT, 16
    if (len(requests) + data['legacyCalls'] > calls
            or data['legacyReservedNanoUsd'] + sum(row['reservedNanoUsd'] for row in requests) > limit):
        raise Refused('Aggregate budget identity/history drift; reconcile without new spending')
    return limit, calls


class Budget:
    def __init__(self, directory, legacy, *, require_established=False):
        self.directory = Path(directory)
        safe_path(self.directory)
        self.path = self.directory / 'budget.json'
        self.legacy = Path(legacy)
        if require_established:
            try:
                self.read()
            except (OSError, Refused) as error:
                raise Refused(f'The established shared budget cannot be opened or verified: {error}') from None
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock():
            if not self.path.exists():
                if (self.directory / 'budget-established.json').exists():
                    raise Refused('Established aggregate budget missing; never reset')
                old = json.loads(self.legacy.read_text())
                requests = old['requests']
                if (not isinstance(requests, list) or len(requests) > MAX_CALLS
                        or any(type(x.get('reservedNanoUsd')) is not int or x['reservedNanoUsd'] <= 0 for x in requests)):
                    raise Refused('Invalid legacy reservation history')
                held = sum(x['reservedNanoUsd'] for x in requests)
                remaining = max(0, MAX_CALLS - len(requests))
                # Original runner may still execute unused slots: reserve their maximum too.
                held += remaining * ((100000 + 1024) * INPUT_RATE + MAX_OUTPUT * OUTPUT_RATE)
                identity = {'version': 1, 'limitNanoUsd': LIMIT, 'maxCalls': 16,
                            'legacySha256': fingerprint(old), 'legacyCalls': len(requests),
                            'legacyReservedNanoUsd': held}
                atomic_json(self.directory / 'budget-established.json', identity)
                atomic_json(self.path, {**identity, 'requests': []})
            self.read()

    def lock(self):
        import contextlib
        @contextlib.contextmanager
        def locked():
            path = self.directory / 'budget.lock'
            safe_path(path)
            with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                yield
        return locked()

    def read(self):
        return self.read_existing(self.directory, self.legacy)

    @staticmethod
    def read_existing(directory, legacy):
        """Validate an existing ledger without creating directories, locks or history."""
        directory = Path(directory)
        legacy = Path(legacy)
        data_bytes = read_owned(directory, 'budget.json')
        marker_bytes = read_owned(directory, 'budget-established.json')
        legacy_bytes = read_owned(legacy.parent, legacy.name)
        data = json.loads(data_bytes)
        marker = json.loads(marker_bytes)
        old = json.loads(legacy_bytes)
        for slot, backup in (('amendment', ORIGINAL_LEDGER), ('continuation', CONTINUATION_LEDGER)):
            if slot not in data and os.path.lexists(directory / backup):
                raise Refused('Budget amendment backup exists without its claim; reconcile without spending')
        expected_project_backups = {project_grant_backup(row['projectGrant']) for row in data['requests']
                                    if isinstance(row, dict) and 'projectGrant' in row}
        actual_project_backups = {path.name for path in directory.glob('budget-before-task-*.json')}
        if actual_project_backups != expected_project_backups:
            raise Refused('Project budget backup exists without its claim or is missing; reconcile without spending')
        verified_history(directory, data, marker_bytes, legacy_bytes)
        if (any(data.get(k) != v for k, v in marker.items())
                or marker.get('limitNanoUsd') != LIMIT or marker.get('maxCalls') != 16
                or fingerprint(old) != marker['legacySha256']):
            raise Refused('Aggregate budget identity/history drift; reconcile without new spending')
        return data

    @staticmethod
    def admission_snapshot(directory, legacy, payload, *, amendment=None):
        """Bound the existing shared ledger without reserving or requesting token counts."""
        try:
            data = Budget.read_existing(directory, legacy)
            identity = {key: data[key] for key in ('version', 'limitNanoUsd', 'maxCalls', 'legacySha256',
                                                   'legacyCalls', 'legacyReservedNanoUsd')}
            requests = data['requests']
            reservations = [row['reservedNanoUsd'] for row in requests]
            if (not isinstance(requests, list) or any(type(value) is not int or value <= 0 for value in reservations)
                    or any(type(identity[key]) is not int or identity[key] < 0
                           for key in ('legacyCalls', 'legacyReservedNanoUsd'))
                    or type(payload['max_output_tokens']) is not int
                    or payload['max_output_tokens'] < 1):
                raise Refused('Invalid aggregate budget admission inputs')
        except (OSError, KeyError, TypeError, ValueError):
            raise Refused('Local admission requires the established shared budget and its original legacy ledger') from None
        claimed_slots = int('amendment' in data) + int('continuation' in data)
        base_reservations = reservations[:len(reservations) - claimed_slots]
        remaining_calls = max(0, identity['maxCalls'] - identity['legacyCalls'] - len(base_reservations))
        available = identity['limitNanoUsd'] - identity['legacyReservedNanoUsd'] - sum(base_reservations)
        # A no-key preview cannot establish token usage. Bytes are diagnostic only;
        # generation is gated on the actual provider preflight and money reservation.
        minimum = reservation(0, payload['max_output_tokens'])
        estimate = reservation(len(json.dumps(payload, ensure_ascii=False).encode()), payload['max_output_tokens'])
        maximum = None
        binding = {'directory': str(Path(directory).resolve()), 'legacy': str(Path(legacy).resolve()),
                   'establishmentSha256': fingerprint(identity)}
        if amendment is not None:
            directory, legacy = Path(directory), Path(legacy)
            verify_amendment(amendment)
            if amendment['version'] == PROJECT_GRANT_VERSION:
                return Budget.project_admission_snapshot(directory, legacy, payload, amendment, data, binding, maximum)
            # Historical Liam grants retain their exact established reservation.
            maximum = reservation(100000, MAX_OUTPUT)
            slot, _, previous_slots = amendment_slot(amendment)
            claimed = slot in data
            if (amendment['requestSha256'] != fingerprint(payload) or payload.get('model') != MODEL
                    or payload['max_output_tokens'] != MAX_OUTPUT or maximum > amendment['maxOperationNanoUsd']
                    or (claimed and data[slot] != amendment)
                    or (not claimed and (identity['legacyCalls'] + len(requests) != previous_slots
                        or any(row['id'] == amendment['operation'] for row in requests)
                        or (slot == 'continuation' and not completed_amendment(data))))):
                raise Refused('Budget amendment differs from the exact request or exhausted history')
            if not claimed:
                verify_budget_prefix(amendment, read_owned(directory, 'budget.json'),
                                     read_owned(directory, 'budget-established.json'),
                                     read_owned(legacy.parent, legacy.name))
            binding['amendmentSha256'] = fingerprint(amendment)
            extended = LIMIT + AMENDMENT_EXTRA - identity['legacyReservedNanoUsd'] - sum(reservations)
            available = 0 if claimed else min(amendment['maxOperationNanoUsd'], extended)
            remaining_calls = int(not claimed)
        return {'binding': binding, 'remainingCalls': remaining_calls, 'availableNanoUsd': available,
                'maximumIntakeNanoUsd': maximum, 'tokenCountStatus': 'unverified-preflight-required',
                'estimatedIntakeNanoUsd': estimate, 'minimumIntakeNanoUsd': minimum,
                'canAdmitIntake': remaining_calls > 0 and available >= (maximum if maximum is not None else minimum)}

    @staticmethod
    def project_admission_snapshot(directory, legacy, payload, grant, data, binding, maximum):
        """A grant can consume one exact operation; only an explicit field adds one slot."""
        rows = [row for row in data['requests'] if row['id'] == grant['operation']]
        claimed = bool(rows)
        marker = read_owned(directory, 'budget-established.json')
        old = read_owned(legacy.parent, legacy.name)
        if (grant['requestSha256'] != fingerprint(payload) or payload.get('model') != MODEL
                or type(payload.get('max_output_tokens')) is not int or payload['max_output_tokens'] < 1
                or reservation(0, payload['max_output_tokens']) > grant['maxOperationNanoUsd']
                or claimed and (len(rows) != 1 or rows[0].get('projectGrant') != grant)):
            raise Refused('Project grant differs from the exact request or claimed operation')
        if not claimed:
            verify_budget_prefix(grant, read_owned(directory, 'budget.json'), marker, old)
            if os.path.lexists(directory / project_grant_backup(grant)):
                raise Refused('Project budget backup exists without its claim; reconcile without spending')
            limit, calls = verified_history(directory, data, marker, old)
            if (grant['maxLedgerNanoUsd'] != limit or grant['maxLedgerCalls'] != calls + grant['extraGenerations']
                    or grant['extraGenerations'] == 1 and len(data['requests']) + data['legacyCalls'] != calls):
                raise Refused('Project grant cannot expand money or add an unapproved call slot')
        available = grant['maxLedgerNanoUsd'] - data['legacyReservedNanoUsd'] - sum(
            row['reservedNanoUsd'] for row in data['requests'])
        remaining = grant['maxLedgerCalls'] - data['legacyCalls'] - len(data['requests'])
        available = 0 if claimed else min(available, grant['maxOperationNanoUsd'])
        # Reserve the exact approved operation cap before credentials/token counting.
        # This is a financial hold, not a source or model-context size ceiling.
        maximum = grant['maxOperationNanoUsd']
        return {'binding': {**binding, 'projectGrantSha256': fingerprint(grant)},
                'remainingCalls': int(not claimed and remaining > 0), 'availableNanoUsd': available,
                'maximumIntakeNanoUsd': maximum, 'tokenCountStatus': 'unverified-preflight-required',
                'canAdmitIntake': not claimed and remaining > 0 and available >= maximum}

    def claim_project_grant(self, operation, payload, grant):
        with self.lock():
            snapshot = self.admission_snapshot(self.directory, self.legacy, payload, amendment=grant)
            if operation != grant['operation'] or not snapshot['canAdmitIntake']:
                raise Refused('Project grant already claimed or outside the existing shared ceiling')
            raw = read_owned(self.directory, self.path.name)
            if sha256(raw).hexdigest() != grant['originalLedgerSha256']:
                raise Refused('Budget changed before project grant claim')
            with os.fdopen(owned_file(self.directory, project_grant_backup(grant),
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            data = json.loads(raw)
            data['requests'].append({'id': operation, 'payloadSha256': grant['requestSha256'],
                'reservedNanoUsd': snapshot['maximumIntakeNanoUsd'], 'status': 'reserved', 'projectGrant': grant,
                'reservationPolicy': 'approved-operation-cap-v1', 'maxOutputTokens': payload['max_output_tokens']})
            self._write_amendment(data)

    def reserve(self, operation, payload, tokens):
        amount = reservation(tokens, payload.get('max_output_tokens'))
        with self.lock():
            data = self.read()
            if (len(data['requests']) + data['legacyCalls'] >= 16
                    or any(x['id'] == operation for x in data['requests'])
                    or data['legacyReservedNanoUsd'] + sum(x['reservedNanoUsd'] for x in data['requests']) + amount > LIMIT):
                raise Refused('Aggregate USD10/call ceiling or duplicate dispatch')
            data['requests'].append({'id': operation, 'payloadSha256': fingerprint(payload),
                                     'reservedNanoUsd': amount, 'status': 'reserved',
                                     'maxOutputTokens': payload['max_output_tokens']})
            atomic_json(self.path, data)

    def _write_amendment(self, data):
        atomic_json(self.path, data)
        descriptor = owned_directory(self.directory)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def claim_amendment(self, operation, payload, grant):
        with self.lock():
            snapshot = self.admission_snapshot(self.directory, self.legacy, payload, amendment=grant)
            if operation != grant['operation'] or not snapshot['canAdmitIntake']:
                raise Refused('Budget amendment already claimed or outside its authorized operation')
            raw = read_owned(self.directory, self.path.name)
            if sha256(raw).hexdigest() != grant['originalLedgerSha256']:
                raise Refused('Budget changed before amendment claim')
            slot, backup, _ = amendment_slot(grant)
            with os.fdopen(owned_file(self.directory, backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            data = json.loads(raw)
            data[slot] = grant
            data['requests'].append({'id': operation, 'payloadSha256': grant['requestSha256'],
                                     'reservedNanoUsd': snapshot['maximumIntakeNanoUsd'], 'status': 'reserved'})
            self._write_amendment(data)

    def _start_amended_response(self, operation, payload, tokens, grant):
        amount = reservation(tokens, payload.get('max_output_tokens'))
        with self.lock():
            data = self.read()
            entry = next(row for row in data['requests'] if row['id'] == operation)
            observed = (entry.get('projectGrant') if grant['version'] == PROJECT_GRANT_VERSION
                        else data.get(amendment_slot(grant)[0]))
            if (observed != grant or entry['id'] != operation or entry['status'] != 'reserved'
                    or 'inputTokens' in entry or amount > entry['reservedNanoUsd']):
                raise Refused('Budget amendment response already started or outside its reservation')
            entry['inputTokens'] = tokens
            self._write_amendment(data)

    def settle(self, operation, response=None, failure=None):
        with self.lock():
            data = self.read()
            entry = next(x for x in data['requests'] if x['id'] == operation)
            amended = 'projectGrant' in entry or any(
                data.get(slot, {}).get('operation') == operation for slot in ('amendment', 'continuation'))
            if entry['status'] != 'reserved':
                raise Refused('Aggregate operation already settled')
            entry['status'] = 'unknown'
            if failure:
                entry['failureClass'] = failure
            usage = response.get('usage', {}) if isinstance(response, dict) else {}
            ins, outs = usage.get('input_tokens'), usage.get('output_tokens')
            if (isinstance(response, dict) and response.get('model') == MODEL
                    and type(ins) is int and type(outs) is int and min(ins, outs) >= 0):
                cost = ins * INPUT_RATE + outs * OUTPUT_RATE
                maximum_output = entry.get('maxOutputTokens', MAX_OUTPUT if amended else None)
                if cost <= entry['reservedNanoUsd'] and (maximum_output is None or outs <= maximum_output):
                    entry.update(status='completed', actualNanoUsd=cost, usage=usage,
                                 responseId=response.get('id'), responseStatus=response.get('status'))
            if amended:
                self._write_amendment(data)
            else:
                atomic_json(self.path, data)

    def transport(self, operation, underlying, *, amendment=None, request=None):
        count = None
        def call(endpoint, payload):
            nonlocal count
            if amendment is not None:
                if endpoint == 'responses/input_tokens':
                    expected = {key: request[key] for key in ('model', 'input', 'instructions', 'text')}
                    if payload != expected:
                        raise Refused('Token count input differs from the bound request')
                    if amendment['version'] == PROJECT_GRANT_VERSION:
                        self.claim_project_grant(operation, request, amendment)
                    else:
                        self.claim_amendment(operation, request, amendment)
                elif endpoint != 'responses' or payload != request:
                    raise Refused('Response input or endpoint differs from the bound request')
            if endpoint == 'responses/input_tokens':
                if amendment is None:
                    data = self.read()
                    if len(data['requests']) + data['legacyCalls'] >= 16:
                        raise Refused('Aggregate USD10/call ceiling before token counting')
                try:
                    result = underlying(endpoint, payload)
                except Exception as error:
                    if amendment is not None:
                        self.settle(operation, failure=type(error).__name__)
                    raise
                count = result.get('input_tokens')
                if amendment is not None:
                    try:
                        provider_token_bounds(count, request['max_output_tokens'])
                        data = self.read()
                        entry = next(row for row in data['requests'] if row['id'] == operation)
                        if reservation(count, request['max_output_tokens']) > entry['reservedNanoUsd']:
                            raise Refused('Actual token count exceeds the approved operation reservation')
                    except Refused:
                        self.settle(operation, failure='TokenPreflightRefused')
                        raise
                return result
            if amendment is None:
                self.reserve(operation, payload, count)
            else:
                self._start_amended_response(operation, payload, count, amendment)
            try:
                response = underlying(endpoint, payload)
            except Exception as error:
                self.settle(operation, failure=type(error).__name__)
                raise
            self.settle(operation, response)
            return response
        return call
