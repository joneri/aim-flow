"""Explicit owner acceptance of source-bound verification scope, never model approval.

The original blocked model review is immutable. No concrete defect, failed stage,
missing source/artifact, or pending delivery gate can be accepted by this adapter.
"""
import argparse
import json
from pathlib import Path

import flow_api_delivery as api
import flow_candidate_review as findings
import flow_workflow as work
from aim_ci import control_package, project_onboarding as onboarding
from aim_ci.owned_paths import Refused, new_owned_directory
from aim_ci.project_discovery import unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-owner-local-acceptance'


def check(decision, review, packet):
    onboarding._keys(decision, ('candidate', 'requirementSha256', 'reviewSha256', 'authority',
        'criteria', 'priorItems', 'unknowns', 'supportingEvidenceIds', 'rationale', 'limitations'), 'owner decision')
    onboarding._keys(decision['authority'], ('actor', 'statement', 'source'), 'owner decision authority')
    for text in (*decision['authority'].values(), decision['rationale']):
        if not isinstance(text, str) or not 10 <= len(text.strip()) <= 8000:
            raise Refused('Owner decision needs a substantive explicit instruction and rationale')
    onboarding._texts(decision['limitations'], 'unverified limitations')
    if not decision['limitations']: raise Refused('Owner scope acceptance must retain unverified limitations')
    if (decision['candidate'] != review['candidate'] or packet['candidate'] != review['candidate']
            or decision['requirementSha256'] != api.digest(packet['requirement']['original'].encode())
            or decision['reviewSha256'] != api.digest(api.encoded(review))):
        raise Refused('Owner decision differs from the exact candidate, original requirement or review')
    if review.get('localFeatureAccepted') is not False or findings.blocking_findings(review):
        raise Refused('Owner scope decision requires a blocked review with no concrete blocking product finding')
    if packet['execution']['status'] != 'passed': raise Refused('Owner decision cannot accept failed execution')
    uncertain = [r for r in review['coverage'] if r['status'] not in ('satisfied', 'pending-delivery')]
    unresolved = [r for r in review['priorDispositions'] if r['status'] == 'unresolved']
    if any(r['status'] != 'uncertain' for r in uncertain):
        raise Refused('Owner scope decision cannot accept a demonstrated criterion defect')
    prior = {row['id']: row for row in packet.get('priorOpenItems', [])}
    if any(prior.get(row['itemId'], {}).get('origin') not in
            ('source-unknown', 'source-blocker', 'assessment-unknown', 'acceptance-obligation')
            for row in unresolved):
        raise Refused('Owner scope decision cannot waive concrete source/workspace defects or unsupported execution')
    targets = {'criteria': [(r['requirementId'], r) for r in uncertain],
        'priorItems': [(r['itemId'], r) for r in unresolved],
        'unknowns': [(str(i), text) for i, text in enumerate(review['unknowns'])]}
    evidence = {row['id']: row for row in packet['evidence']}
    support = decision['supportingEvidenceIds']
    if (not isinstance(support, list) or not support or len(set(support)) != len(support)
            or any(ref not in evidence for ref in support)):
        raise Refused('Owner decision requires exact retained supporting evidence IDs')
    for group, rows in targets.items():
        expected = [{'id': key, 'sha256': api.digest(api.encoded(row))} for key, row in rows]
        if decision[group] != expected:
            raise Refused('Owner decision must name every exact outstanding ' + group + ', without additions')
        if group != 'unknowns':
            for _, row in rows:
                refs = row['evidenceIds']; kinds = {evidence[ref]['kind'] for ref in [*refs, *support] if ref in evidence}
                if (not row['sources'] or not refs or any(ref not in evidence for ref in refs)
                        or not kinds.intersection({'image/png', 'application/json'})
                        or group == 'criteria' and ('visual' in row['evidenceKinds'] and 'image/png' not in kinds
                            or 'behavior' in row['evidenceKinds'] and 'application/json' not in kinds)):
                    raise Refused('Owner scope decision cannot replace missing source-bound executed evidence')
    # Each blocker must be a precisely addressed uncertainty, not an omitted
    # validation failure or unrecognized parser/integrity condition.
    expected_blockers = [*review['unknowns'],
        *(r['requirementId'] + ': ' + r['analysis'] for r in uncertain),
        *(r['itemId'] + ': ' + r['analysis'] for r in unresolved)]
    if review['blockers'] != expected_blockers or not expected_blockers:
        raise Refused('Owner decision does not account for every exact blocker')
    return {'basis': 'explicit-owner-verification-scope-decision', 'modelLocalFeatureAccepted': False,
        'ownerLocalFeatureAccepted': True, 'decision': decision,
        'qualification': 'Owner accepts the stated verification scope. Original model review remains blocked; limitations are unverified, not passed.',
        'publicationAuthorized': False, 'deploymentAuthorized': False}


def prepare(config_path, config_sha, state):
    import flow_delivery_chain as chain
    state = api.canonical_path(state)
    pin = {'path': str(api.canonical_path(config_path)), 'sha256': config_sha}
    c = unique_json(api.pinned(pin))
    onboarding._keys(c, ('format', 'schemaVersion', 'manifestSha256', 'localRun', 'decision'), 'owner acceptance configuration')
    if c['format'] != FORMAT or type(c['schemaVersion']) is not int or c['schemaVersion'] != 1:
        raise Refused('Unsupported owner acceptance configuration')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    if 'ownerAcceptance' in c['localRun']: raise Refused('Owner acceptance cannot recursively accept another decision')
    verified = chain.verify_local(c['localRun'], c['manifestSha256'], owner_decision=c['decision'])
    protected = [ROOT, Path(c['localRun']['state']), Path(verified['candidate']['checkout']),
                 Path(verified['config']['source']['checkout']), Path(verified['config']['campaignDirectory']),
                 Path(verified['config']['historicalBudget']['directory']), Path(pin['path'])]
    if 'controller' in c['localRun']: protected.append(Path(c['localRun']['controller']['path']))
    if any(state == p or p in state.parents or state in p.parents for p in protected):
        raise Refused('Owner decision state overlaps protected evidence or source')
    intent = {'format': FORMAT, 'schemaVersion': 1, 'config': pin, 'state': str(state),
        'controllerManifestSha256': c['manifestSha256'], 'localRun': c['localRun'],
        'sourceEvidence': verified['evidence'], 'acceptance': verified['ownerAcceptance'],
        'modelCallsAuthorized': False, 'projectExecutionAuthorized': False,
        'publicationAuthorized': False, 'deploymentAuthorized': False}
    return c, intent


def run(config_path, config_sha, state, authorization=None, *, verify_only=False):
    if verify_only and authorization is not None: raise Refused('Owner acceptance verification takes no authorization')
    state = api.canonical_path(state)
    c, intent = prepare(config_path, config_sha, state)
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    result = {'format': FORMAT, 'schemaVersion': 1, 'status': 'owner-local-scope-accepted',
        'config': intent['config'], 'localRun': c['localRun'], 'acceptance': intent['acceptance'],
        'sourceEvidence': intent['sourceEvidence'], 'modelReviewRewritten': False,
        'publicationAuthorized': False, 'deploymentAuthorized': False}
    if not state.exists():
        if verify_only: raise Refused('Owner decision requires a completed receipt')
        if authorization is None: return {'status': 'awaiting-authorization', **grant}
        work.require_authorization(authorization, intent)
        with new_owned_directory(state): api.fsync_directory(state.parent)
        api.durable(state, 'grant.json', api.encoded(grant))
        api.durable(state, 'result.json', api.encoded(result))
    if authorization is not None: work.require_authorization(authorization, intent)
    if (unique_json(api.file_bytes(state / 'grant.json')) != grant
            or unique_json(api.file_bytes(state / 'result.json')) != result):
        raise Refused('Owner acceptance grant or receipt changed; never reconstruct it')
    return result


def verify(spec, local_run, manifest):
    onboarding._keys(spec, ('config', 'state', 'grant', 'result'), 'owner acceptance receipt')
    state = api.canonical_path(spec['state'])
    if spec['grant']['path'] != str(state / 'grant.json') or spec['result']['path'] != str(state / 'result.json'):
        raise Refused('Owner receipt must pin its exact retained grant/result')
    config = unique_json(api.pinned(spec['config']))
    if config['manifestSha256'] != manifest or config['localRun'] != local_run:
        raise Refused('Owner acceptance applies to another controller or local run')
    api.pinned(spec['grant']); retained = unique_json(api.pinned(spec['result']))
    value = run(Path(spec['config']['path']), spec['config']['sha256'], state, verify_only=True)
    if value != retained: raise Refused('Owner decision receipt reconstruction differs')
    return {**value['acceptance'], 'receipt': spec['result'], 'grant': spec['grant']}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True); p.add_argument('--config-sha256', required=True)
    p.add_argument('--state', type=Path, required=True); p.add_argument('--authorize-intent'); p.add_argument('--verify', action='store_true')
    a = p.parse_args(argv)
    print(json.dumps(run(a.config, a.config_sha256, a.state, a.authorize_intent, verify_only=a.verify), sort_keys=True))
    return 0
