"""Single-use structured dispatch using the established Sol accounting authority.

The caller supplies deterministic preparation and parsing, never model authority.
Existing adapter receipts remain unchanged.
"""
from pathlib import Path
import flow_api_delivery as api
from aim_ci.owned_paths import Refused, new_owned_directory
from openai_budget import ApiFailure, transport_from_key_file


def dispatch(prepare, parse, config_path, config_sha, state, authorization=None, *, key_file=None, transport=None):
    state = api.canonical_path(state)
    if state.exists():
        raise Refused('Attempt state already exists; inspect status, never replay')
    c, raw, packet, payload, intent = prepare(config_path, config_sha, state)
    intent_sha = api.digest(api.encoded(intent))
    if authorization is None:
        return {'status': 'preview', 'intentSha256': intent_sha, 'intent': intent,
                'request': payload, 'generation': 'not-run', 'tokenPreflight': 'not-run'}
    if authorization != intent_sha:
        raise Refused('Exact current review intent authorization required')
    if transport is None and key_file is None:
        raise Refused('Approved credential required')
    with api.historical_lock(c['historicalBudget']):
        api.historical_snapshot(c['historicalBudget'])
        campaign = api.Campaign(c)
        with new_owned_directory(state):
            api.fsync_directory(state.parent)
        api.durable(state, 'attempt.json', api.encoded({'intent': intent, 'intentSha256': intent_sha,
            'status': 'attempt-consumed', 'replayPermitted': False, 'attemptsRemaining': 0}))
        for name, content in [('config.json', raw), ('packet.json', api.encoded(packet)),
                              ('request.json', api.encoded(payload))]:
            api.durable(state, name, content)
        entry = response = review = failure = None
        outcome = 'unknown'
        def recheck():
            if prepare(config_path, config_sha, state)[-1] != intent:
                raise Refused('Review inputs changed')
        try:
            post = transport or transport_from_key_file(key_file, require_private=True,
                                                       deadline_seconds=c['timeoutSeconds'])
            recheck()
            count = post('responses/input_tokens', {k: payload[k] for k in ('model', 'input', 'instructions', 'text')})
            api.durable(state, 'token-count.json', api.encoded(count))
            if not isinstance(count, dict) or count.get('model', api.MODEL) != api.MODEL:
                raise Refused('Token count model differs')
            tokens = count.get('input_tokens')
            if api.reservation(tokens, c['maxOutputTokens']) > c['maxOperationNanoUsd']:
                raise Refused('Review reservation exceeds operation approval')
            recheck()
            entry = campaign.reserve(intent_sha, api.digest(api.encoded(payload)), tokens, c['maxOutputTokens'], state)
            api.durable(state, 'reservation.json', api.encoded(entry))
            recheck()
            response = post('responses', payload)
            api.durable(state, 'response.json', api.encoded(response))
            entry = campaign.finish(intent_sha, response)
            api.historical_snapshot(c['historicalBudget'])
            if entry['status'] != 'completed':
                outcome = 'unknown-usage-or-model'
            elif response.get('status') != 'completed':
                outcome = 'model-incomplete-or-failed'
            else:
                review = parse(response, packet)
                recheck()
                if review is None:
                    outcome = 'model-refused'
                else:
                    api.durable(state, 'review.json', api.encoded(review))
                    outcome = 'review-blocked' if review['blockers'] else 'source-review-ready'
        except Exception as error:
            review = None
            failure = {'class': type(error).__name__}
            if isinstance(error, ApiFailure):
                failure.update(httpStatus=error.status, code=error.code)
            if entry is not None and entry['status'] == 'reserved':
                entry = campaign.finish(intent_sha, response, failure)
            outcome = ('unknown' if entry is not None and entry['status'] == 'unknown'
                       else 'review-rejected' if response is not None else 'preflight-refused')
        result = {'status': outcome, 'intentSha256': intent_sha, 'reservation': entry,
            'failure': failure, 'review': review, 'replayPermitted': False, 'attemptsRemaining': 0,
            'attemptsUsed': 1, 'executionAuthorized': False, 'productAcceptance': 'not-run',
            'evidenceSha256': {p.name: api.digest(api.file_bytes(p)) for p in state.iterdir() if p.is_file()}}
        api.durable(state, 'result.json', api.encoded(result))
        return result
