"""Explicit Sol API patch proposals. No source apply, tools, retries or acceptance."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from aim_ci.budget import Budget
from aim_ci.owned_paths import (Refused, new_owned_directory, owned_directory, owned_file,
                                read_owned_content, safe_path, write_owned)
from openai_budget import ApiFailure, transport_from_key_file

MODEL = 'gpt-6.1-sol'
LIMIT = 20_000_000_000
MAX_INPUT, MAX_CONTEXT, MAX_OUTPUT = 922_000, 1_050_000, 128_000
INPUT_HEADROOM = 1024
PRICING = {'source': 'https://developers.openai.com/api/docs/pricing',
           'verifiedDate': '2026-11-09', 'thresholdInputTokens': 272_000,
           'inputNanoUsdPerToken': 2500, 'outputNanoUsdPerToken': 10000,
           'longInputNanoUsdPerToken': 5000, 'longOutputNanoUsdPerToken': 15000,
           'policy': 'cache-write maximum input rate; full reservation never refunded'}
PATCH_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'edits'],
                'properties': {'summary': {'type': 'string'}, 'edits': {'type': 'array',
                    'items': {'type': 'object', 'additionalProperties': False,
                              'required': ['path', 'old', 'new'],
                              'properties': {key: {'type': 'string'} for key in ('path', 'old', 'new')}}}}}


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_path(value):
    if not isinstance(value, (str, Path)):
        raise Refused('Expected canonical absolute path')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or str(path) != str(value):
        raise Refused('Expected canonical absolute path')
    safe_path(path)
    return path


def file_bytes(path):
    path = canonical_path(path)
    return read_owned_content(path.parent, path.name)


def pinned(spec):
    if (not isinstance(spec, dict) or set(spec) != {'path', 'sha256'}
            or not isinstance(spec['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', spec['sha256'])):
        raise Refused('Expected file path and SHA256 pin')
    raw = file_bytes(spec['path'])
    if digest(raw) != spec['sha256']:
        raise Refused('Pinned input changed')
    return raw


def fsync_directory(path):
    descriptor = owned_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable(root, name, raw):
    write_owned(root, name, raw, exclusive=True)
    with os.fdopen(owned_file(root, name, os.O_RDONLY), 'rb') as stream:
        os.fsync(stream.fileno())
    fsync_directory(root)


def atomic_json(path, value):
    safe_path(path)
    # Private sibling, flushed and replaced before the containing directory is synced.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.budget-')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def token_bounds(tokens, output):
    if (type(tokens) is not int or tokens < 0 or type(output) is not int or output < 1
            or tokens > MAX_INPUT or output > MAX_OUTPUT or tokens + output > MAX_CONTEXT):
        raise Refused('Request exceeds pinned Sol provider token limits')


def charge(tokens, output):
    if type(tokens) is not int or type(output) is not int or min(tokens, output) < 0:
        raise Refused('Invalid token usage')
    long = tokens > PRICING['thresholdInputTokens']
    return tokens * (5000 if long else 2500) + output * (15000 if long else 10000)


def reservation(tokens, output):
    token_bounds(tokens, output)
    return charge(tokens + INPUT_HEADROOM, output)


def request_payload(spec):
    raw = pinned(spec)
    payload = json.loads(raw)
    required = {'model', 'input', 'instructions', 'max_output_tokens', 'reasoning',
                'service_tier', 'store', 'stream', 'text'}
    if (not isinstance(payload, dict) or not required <= set(payload)
            or set(payload) - required or payload['model'] != MODEL
            or payload['reasoning'] != {'effort': 'xhigh'}
            or payload['service_tier'] != 'default' or payload['store'] is not False
            or payload['stream'] is not True or not isinstance(payload['input'], str)
            or not isinstance(payload['instructions'], str) or not payload['instructions']):
        raise Refused('Only explicit Sol/xhigh standard streaming text requests without tools are admitted')
    token_bounds(0, payload['max_output_tokens'])
    if 'text' in payload:
        text = payload['text']
        form = text.get('format') if isinstance(text, dict) else None
        if (not isinstance(text, dict) or set(text) != {'format'} or not isinstance(form, dict)
                or set(form) != {'type', 'name', 'strict', 'schema'}
                or form['type'] != 'json_schema' or form['strict'] is not True
                or not isinstance(form['name'], str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', form['name'])
                or form['schema'] != PATCH_SCHEMA):
            raise Refused('Only the strict summary/edits old/new JSON schema is supported')
    return raw, payload


def historical_snapshot(spec):
    if not isinstance(spec, dict) or set(spec) != {'directory', 'legacy', 'files', 'liabilityNanoUsd'}:
        raise Refused('Historical budget pins and liability required')
    directory = canonical_path(spec['directory'])
    pins = spec['files']
    names = {'budget.json', 'budget-established.json'} | {p.name for p in directory.glob('budget-before-*.json')}
    if not isinstance(pins, dict) or set(pins) != names:
        raise Refused('Historical budget file inventory changed')
    def verify():
        for name in names:
            pinned({'path': str(directory / name), 'sha256': pins[name]})
        pinned(spec['legacy'])
    verify()
    data = Budget.read_existing(directory, canonical_path(spec['legacy']['path']))
    requests = data['requests']
    if (type(data['legacyReservedNanoUsd']) is not int or data['legacyReservedNanoUsd'] < 0
            or not isinstance(requests, list) or any(type(r.get('reservedNanoUsd')) is not int
            or r['reservedNanoUsd'] <= 0 for r in requests)):
        raise Refused('Invalid historical budget liabilities')
    held = data['legacyReservedNanoUsd'] + sum(r['reservedNanoUsd'] for r in requests)
    if type(spec['liabilityNanoUsd']) is not int or spec['liabilityNanoUsd'] != held or held > LIMIT:
        raise Refused('Historical budget liability differs or exceeds approved ceiling')
    verify()
    return held


@contextlib.contextmanager
def historical_lock(spec):
    # Both established writers use these locks. Open read-only; never create or modify history.
    roots = sorted({spec['directory'], str(Path(spec['legacy']['path']).parent)})
    with contextlib.ExitStack() as stack:
        for root in roots:
            stream = stack.enter_context(os.fdopen(owned_file(root, 'budget.lock', os.O_RDONLY), 'rb'))
            fcntl.flock(stream, fcntl.LOCK_SH)
        yield


def implementation_pins():
    root = Path(__file__).resolve().parents[1]
    names = ['aim-flow.py', 'lib/flow_api_delivery.py', 'lib/openai_budget.py',
             'lib/aim_ci/budget.py', 'lib/aim_ci/owned_paths.py']
    return {name: digest(file_bytes(root / name)) for name in names}



def authority(config):
    history = config['historicalBudget']
    key = digest(encoded({'directory': history['directory'], 'legacy': history['legacy']['path']}))
    path = Path(history['directory']).parent / ('sol-api-authority-' + key + '.json')
    identity = {'schemaVersion': 1, 'campaignDirectory': config['campaignDirectory'],
                'historicalBudget': history, 'limitNanoUsd': LIMIT, 'model': MODEL, 'pricing': PRICING}
    if path.exists() and json.loads(file_bytes(path)) != identity:
        raise Refused('This historical budget already belongs to a different Sol campaign; never reset')
    return path, identity


def prepare(config_path, config_sha, state):
    raw = pinned({'path': str(config_path), 'sha256': config_sha})
    config = json.loads(raw)
    required = {'schemaVersion', 'method', 'model', 'reasoning', 'candidateDirectory',
                'sourceFiles', 'inputFiles', 'request', 'campaignDirectory', 'historicalBudget', 'timeoutSeconds'}
    if (not isinstance(config, dict) or set(config) != required or type(config['schemaVersion']) is not int
            or config['schemaVersion'] != 1 or config['method'] != 'responses-api'
            or config['model'] != MODEL or config['reasoning'] != 'xhigh'
            or type(config['timeoutSeconds']) is not int or not 1 <= config['timeoutSeconds'] <= 3600):
        raise Refused('Unsupported explicit Sol delivery configuration')
    candidate = canonical_path(config['candidateDirectory'])
    state = canonical_path(state)
    campaign = canonical_path(config['campaignDirectory'])
    historical = canonical_path(config['historicalBudget']['directory'])
    legacy_root = canonical_path(config['historicalBudget']['legacy']['path']).parent
    # Neither retained state nor campaign writes may overlap source, one another or historical roots.
    protected = [candidate, historical, legacy_root, Path(__file__).resolve().parents[1]]
    for root in (state, campaign):
        for other in [*protected, campaign if root == state else state]:
            if root == other or root in other.parents or other in root.parents:
                raise Refused('State and campaign must be external, disjoint canonical directories')
    if not isinstance(config['sourceFiles'], dict) or not config['sourceFiles']:
        raise Refused('Explicit source file pins required')
    sources = {}
    for name, spec in config['sourceFiles'].items():
        path = Path(name)
        if (not name or path.is_absolute() or '..' in path.parts or str(path) != name
                or path.parts[0] == '.git' or spec.get('path') != str(candidate / path)):
            raise Refused('Source path outside exact candidate scope')
        sources[name] = pinned(spec).decode('utf-8')
    if not isinstance(config['inputFiles'], list) or not config['inputFiles']:
        raise Refused('Pinned task/instruction inputs required')
    for spec in config['inputFiles']:
        pinned(spec)
    request_raw, payload = request_payload(config['request'])
    historical_snapshot(config['historicalBudget'])
    authority_path, _ = authority(config)
    intent = {'schemaVersion': 1, 'configPath': str(config_path), 'configSha256': config_sha,
              'implementationSha256': implementation_pins(), 'request': config['request'],
              'candidateDirectory': str(candidate), 'sourceFiles': config['sourceFiles'],
              'inputFiles': config['inputFiles'], 'campaignDirectory': str(campaign),
              'historicalBudget': config['historicalBudget'], 'state': str(state),
              'authorityPath': str(authority_path),
              'modelRequested': MODEL, 'reasoningRequested': 'xhigh', 'serviceTier': 'default',
              'timeoutSeconds': config['timeoutSeconds'], 'limitNanoUsd': LIMIT, 'pricing': PRICING,
              'maxOutputTokens': payload['max_output_tokens'], 'inputTokenHeadroom': INPUT_HEADROOM,
              'maxAttempts': 1, 'sourceApplied': False, 'productAccepted': False}
    return config, raw, request_raw, payload, sources, intent


class Campaign:
    def __init__(self, config):
        self.directory = canonical_path(config['campaignDirectory'])
        self.history = config['historicalBudget']
        authority_path, identity = authority(config)
        created_authority = False
        if not authority_path.exists():
            if self.directory.exists():
                raise Refused('Established campaign authority is missing; never recreate')
            try:
                durable(authority_path.parent, authority_path.name, encoded(identity))
                created_authority = True
            except Refused:
                # Concurrent first initialization may win; never replace the authority.
                if not authority_path.exists() or json.loads(file_bytes(authority_path)) != identity:
                    raise
        authority(config)
        self.identity = {'schemaVersion': 1, 'campaignDirectory': str(self.directory),
                         'model': MODEL, 'limitNanoUsd': LIMIT, 'pricing': PRICING,
                         'historicalBudget': self.history}
        if not self.directory.exists():
            if not created_authority:
                raise Refused('Established campaign directory is missing; never recreate')
            with new_owned_directory(self.directory):
                fsync_directory(self.directory.parent)
                durable(self.directory, 'campaign.json', encoded(self.identity))
                durable(self.directory, 'budget.json', encoded({'identity': self.identity, 'requests': []}))
        # Never recreate an established directory's missing marker or ledger.
        with self.lock():
            self.read()

    @contextlib.contextmanager
    def lock(self):
        with os.fdopen(owned_file(self.directory, 'budget.lock', os.O_CREAT | os.O_RDWR), 'r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self):
        marker = json.loads(file_bytes(self.directory / 'campaign.json'))
        data = json.loads(file_bytes(self.directory / 'budget.json'))
        if marker != self.identity or data.get('identity') != self.identity or set(data) != {'identity', 'requests'}:
            raise Refused('Shared Sol campaign identity changed; never reset')
        entries = data['requests']
        if not isinstance(entries, list):
            raise Refused('Invalid Sol campaign history')
        ids, hashes = set(), set()
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or entry['id'] in ids
                    or not isinstance(entry.get('requestSha256'), str) or entry['requestSha256'] in hashes
                    or entry.get('status') not in ('reserved', 'unknown', 'completed')
                    or type(entry.get('reservedNanoUsd')) is not int
                    or entry['reservedNanoUsd'] != reservation(entry.get('inputTokens'), entry.get('maxOutputTokens'))):
                raise Refused('Invalid Sol reservation history')
            ids.add(entry['id']); hashes.add(entry['requestSha256'])
        if self.history['liabilityNanoUsd'] + sum(e['reservedNanoUsd'] for e in entries) > LIMIT:
            raise Refused('Combined historical and Sol budget exceeds USD20')
        return data

    def reserve(self, intent_sha, request_sha, tokens, output, state):
        amount = reservation(tokens, output)
        with self.lock():
            historical_snapshot(self.history)
            data = self.read()
            if any(e['id'] == intent_sha or e['requestSha256'] == request_sha for e in data['requests']):
                raise Refused('Attempt or identical request already reserved; never replay')
            total = self.history['liabilityNanoUsd'] + sum(e['reservedNanoUsd'] for e in data['requests']) + amount
            if total > LIMIT:
                raise Refused('Reservation would exceed combined historical and Sol USD20 ceiling')
            entry = {'id': intent_sha, 'requestSha256': request_sha, 'state': str(state),
                     'inputTokens': tokens, 'maxOutputTokens': output, 'reservedNanoUsd': amount,
                     'status': 'reserved', 'modelRequested': MODEL}
            data['requests'].append(entry)
            atomic_json(self.directory / 'budget.json', data)
        return entry

    def finish(self, intent_sha, response, failure=None):
        with self.lock():
            data = self.read()
            entry = next(e for e in data['requests'] if e['id'] == intent_sha)
            if entry['status'] != 'reserved':
                raise Refused('Attempt already settled; never replay')
            entry.update(status='unknown')
            if failure:
                entry['failure'] = failure
            if isinstance(response, dict):
                usage = response.get('usage')
                entry.update(modelReturned=response.get('model'), responseStatus=response.get('status'),
                             responseId=response.get('id'), usage=usage)
                if isinstance(usage, dict):
                    ins, outs = usage.get('input_tokens'), usage.get('output_tokens')
                    if (response.get('model') == MODEL and response.get('service_tier', 'default') == 'default'
                            and type(ins) is int and type(outs) is int and min(ins, outs) >= 0
                            and ins <= entry['inputTokens'] + INPUT_HEADROOM and outs <= entry['maxOutputTokens']
                            and charge(ins, outs) <= entry['reservedNanoUsd']):
                        entry.update(status='completed', maximumUsageNanoUsd=charge(ins, outs))
            atomic_json(self.directory / 'budget.json', data)
        return entry


def proposal(response, sources):
    texts = []
    for item in response.get('output', []):
        if not isinstance(item, dict):
            raise Refused('Invalid response output')
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('role') != 'assistant':
            raise Refused('Unexpected tool or output item')
        for part in item.get('content', []):
            if part.get('type') == 'refusal':
                return None
            if part.get('type') != 'output_text' or not isinstance(part.get('text'), str):
                raise Refused('Unexpected proposal content')
            texts.append(part['text'])
    if len(texts) != 1:
        raise Refused('Exactly one JSON proposal required')
    value = json.loads(texts[0])
    if (not isinstance(value, dict) or set(value) != {'summary', 'edits'}
            or not isinstance(value['summary'], str) or not isinstance(value['edits'], list) or not value['edits']):
        raise Refused('Nonempty summary/edits proposal required')
    updated = dict(sources)
    seen = set()
    for edit in value['edits']:
        if (not isinstance(edit, dict) or set(edit) != {'path', 'old', 'new'}
                or any(not isinstance(v, str) for v in edit.values()) or edit['path'] not in sources
                or not edit['old'] or edit['old'] == edit['new']
                or (edit['path'], edit['old']) in seen or updated[edit['path']].count(edit['old']) != 1):
            raise Refused('Every edit must be an in-scope unique nonempty old/new replacement')
        seen.add((edit['path'], edit['old']))
        updated[edit['path']] = updated[edit['path']].replace(edit['old'], edit['new'], 1)
    if updated == sources:
        raise Refused('Proposal makes no change')
    return value


def status(state):
    state = canonical_path(state)
    failure = {'status': 'integrity-failed', 'replayPermitted': False, 'attemptsRemaining': 0}
    try:
        attempt = json.loads(file_bytes(state / 'attempt.json'))
        failure['intentSha256'] = attempt['intentSha256']
        if not (state / 'result.json').exists():
            return {**failure, 'status': 'unknown', 'reason': 'Attempt consumed; reconcile retained state and campaign, never replay'}
        result = json.loads(file_bytes(state / 'result.json'))
        if result['intentSha256'] != attempt['intentSha256'] or not {'attempt.json', 'config.json', 'request.json'} <= set(result['evidenceSha256']):
            raise Refused('Terminal receipt identity differs')
        for name, sha in result['evidenceSha256'].items():
            if not isinstance(name, str) or Path(name).name != name or digest(file_bytes(state / name)) != sha:
                raise Refused('Retained evidence changed or missing')
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return failure


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None):
    state = canonical_path(state)
    if state.exists():
        raise Refused('Attempt state already exists; use --status, never replay or reset')
    c, raw, request_raw, payload, sources, intent = prepare(config_path, config_sha, state)
    intent_sha = digest(encoded(intent))
    if authorization is None:
        return {'status': 'preview', 'intentSha256': intent_sha, 'intent': intent,
                'tokenPreflight': 'not-run', 'generation': 'not-run'}
    if authorization != intent_sha:
        raise Refused('Exact current preview intent authorization required')
    if transport is None and key_file is None:
        raise Refused('--key-file is required only for an authorized invocation')
    with historical_lock(c['historicalBudget']):
        historical_snapshot(c['historicalBudget'])
        campaign = Campaign(c)
        with new_owned_directory(state):
            fsync_directory(state.parent)
        durable(state, 'attempt.json', encoded({'intentSha256': intent_sha, 'intent': intent,
                'status': 'attempt-reserved', 'replayPermitted': False, 'attemptsRemaining': 0}))
        durable(state, 'config.json', raw)
        durable(state, 'request.json', request_raw)
        for i, spec in enumerate(c['inputFiles']):
            durable(state, 'input-' + str(i) + '.bin', pinned(spec))
        for i, (name, text) in enumerate(sorted(sources.items())):
            durable(state, 'source-' + str(i) + '.txt', text.encode())
        entry = None
        response = None
        outcome = 'unknown'
        failure = None
        try:
            # Credential path/content are never copied into intent, state, logs, or campaign.
            post = transport if transport is not None else transport_from_key_file(
                key_file, require_private=True, deadline_seconds=c['timeoutSeconds'])
            current = prepare(config_path, config_sha, state)
            if current[-1] != intent:
                raise Refused('Pinned inputs changed before preflight')
            count_payload = {k: payload[k] for k in ('model', 'input', 'instructions', 'text') if k in payload}
            count = post('responses/input_tokens', count_payload)
            durable(state, 'token-count.json', encoded(count))
            if not isinstance(count, dict) or count.get('model', MODEL) != MODEL:
                raise Refused('Token count model differs')
            tokens = count.get('input_tokens')
            token_bounds(tokens, payload['max_output_tokens'])
            if prepare(config_path, config_sha, state)[-1] != intent:
                raise Refused('Pinned inputs changed during preflight')
            entry = campaign.reserve(intent_sha, digest(encoded(payload)), tokens, payload['max_output_tokens'], state)
            durable(state, 'reservation.json', encoded(entry))
            # Recheck after durable reservation; drift consumes the full hold without dispatch.
            if prepare(config_path, config_sha, state)[-1] != intent:
                raise Refused('Pinned inputs changed after reservation')
            # All liability and the attempt are on disk before any generation is dispatched.
            response = post('responses', payload)
            durable(state, 'response.json', encoded(response))
            entry = campaign.finish(intent_sha, response)
            historical_snapshot(c['historicalBudget'])
            if entry['status'] != 'completed':
                outcome = 'unknown-usage-or-model'
            elif response.get('status') != 'completed':
                outcome = 'model-' + (response.get('status') if response.get('status') in ('incomplete', 'failed') else 'unknown-status')
            else:
                parsed = proposal(response, sources)
                if parsed is None:
                    outcome = 'model-refused'
                else:
                    # Response can be valid but source may have changed during the remote call.
                    if prepare(config_path, config_sha, state)[-1] != intent:
                        raise Refused('Pinned inputs changed during generation')
                    durable(state, 'proposal.json', encoded(parsed))
                    outcome = 'proposal-ready-for-independent-review'
        except Exception as error:
            # No exception text: transport/parser exceptions can contain private request data.
            failure = {'class': type(error).__name__}
            if isinstance(error, ApiFailure):
                failure.update(httpStatus=error.status, code=error.code)
            if entry is not None and entry['status'] == 'reserved':
                entry = campaign.finish(intent_sha, response, failure)
            outcome = 'unknown' if entry is not None and entry['status'] == 'unknown' else 'proposal-rejected' if response is not None else 'preflight-refused'
        result = {'status': outcome, 'intentSha256': intent_sha, 'attemptsUsed': 1,
                  'attemptsRemaining': 0, 'replayPermitted': False, 'modelRequested': MODEL,
                  'modelReturned': response.get('model') if isinstance(response, dict) else None,
                  'responseStatus': response.get('status') if isinstance(response, dict) else None,
                  'usage': response.get('usage') if isinstance(response, dict) else None,
                  'reservation': entry, 'failure': failure, 'sourceApplied': False,
                  'productAccepted': False, 'independentQA': 'not-run',
                  'evidenceSha256': {p.name: digest(file_bytes(p)) for p in state.iterdir() if p.is_file()}}
        durable(state, 'result.json', encoded(result))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--config-sha256')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--key-file', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--authorize-intent')
    mode.add_argument('--status', action='store_true')
    args = parser.parse_args(argv)
    if args.status:
        if args.key_file:
            parser.error('--key-file is only allowed for an authorized invocation')
        result = status(args.state)
    else:
        if not args.config or not args.config_sha256:
            parser.error('--config and --config-sha256 required')
        if args.preview and args.key_file:
            parser.error('--preview never reads a credential')
        result = run(args.config, args.config_sha256, args.state, args.authorize_intent, key_file=args.key_file)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('preview', 'proposal-ready-for-independent-review') else 1
