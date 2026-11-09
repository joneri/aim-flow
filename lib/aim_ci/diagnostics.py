"""Read-only projections of existing run state and authoritative budget history.

No replacement ledger, recovery decision, external lookup or credential access.
Observations are local snapshots, not a transaction across active workers.
"""
import json
from pathlib import Path

from openai_budget import Refused, fingerprint, read_owned
from .budget import Budget


def _error(error):
    # Never include arbitrary file contents or parsing exception excerpts.
    return str(error) if isinstance(error, Refused) else type(error).__name__


def _resources(directory, legacy):
    try:
        data = Budget.read_existing(directory, legacy)
        old = json.loads(read_owned(Path(legacy).parent, Path(legacy).name))
        # The same legacy bytes must still match the budget's bound history.
        if fingerprint(old) != data['legacySha256']:
            raise Refused('Legacy budget changed during observation')
        entries = [('legacy', item) for item in old['requests']]
        entries += [('aggregate', item) for item in data['requests']]
        calls = data['legacyCalls'] + len(data['requests'])
        held = data['legacyReservedNanoUsd'] + sum(item['reservedNanoUsd'] for item in data['requests'])
        known = sum(item.get('actualNanoUsd', 0) for _, item in entries if item['status'] == 'completed')
        return {
            'available': True,
            'callsUsed': calls, 'maximumCalls': data['maxCalls'],
            'remainingCallSlots': data['maxCalls'] - calls,
            'limitNanoUsd': data['limitNanoUsd'],
            'heldLiabilityNanoUsd': held,
            'unreservedNanoUsd': data['limitNanoUsd'] - held,
            'knownUsageNanoUsd': known,
            'unsettledRequests': [
                {'ledger': ledger, 'id': item['id'], 'status': item['status'],
                 'reservedNanoUsd': item['reservedNanoUsd']}
                for ledger, item in entries if item['status'] != 'completed'
            ],
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError, Refused) as error:
        return {'available': False, 'error': _error(error)}


def observe(directory, legacy):
    directory = Path(directory)
    runs = []
    for path in sorted(directory.glob('issues/*/state.json')):
        try:
            state = json.loads(read_owned(directory, path.relative_to(directory)))
            issue = state['issue']
            if not isinstance(issue, dict):
                raise ValueError('Invalid Issue snapshot')
            runs.append({
                'key': state['key'], 'issue': issue['number'],
                'repository': issue.get('repository'),
                'stage': state['stage'], 'stoppedFrom': state.get('stoppedFrom'),
                'reason': state.get('reason'),
                'executionMode': state.get('executionMode', 'unrecorded'),
                'retained': {key: state[key] for key in (
                    'baseline', 'branch', 'headSha', 'pullRequest', 'pullRequestUrl',
                    'mergeSha', 'ciRun', 'deploymentRun',
                ) if key in state},
                # Never convert transport success into owner acceptance.
                'productAccepted': state.get('productAccepted', False),
            })
        except (OSError, ValueError, KeyError, TypeError, Refused) as error:
            runs.append({'directory': path.parent.name, 'observationError': _error(error)})
    return {
        'observationOnly': True,
        'notes': [
            'Local snapshots; concurrent changes may require another observation.',
            'Remaining slots and unreserved funds are not permission to retry or spend.',
            'Unknown outcomes retain reservations; no external outcome is inferred.',
            'Held liability includes legacy unused-slot allowance; it is not actual billing.',
        ],
        'runs': runs,
        'resources': _resources(directory, Path(legacy)),
    }
