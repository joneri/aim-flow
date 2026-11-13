"""Flow-owned repair/review/validation orchestration.

Schema two is a budget-governed loop. Schema one preserves its exact historical
one-repair grant. Both derive owned child configurations and dispatch markers.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path

import flow_api_delivery as api
import flow_repair_candidate as repair
import flow_resume_candidate as resume
import flow_workflow as work
from aim_ci import control_package, project_onboarding as onboarding
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file
from aim_ci.project_discovery import unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-repair-local-run'


def report(status, **extra):
    return {'format': FORMAT, 'status': status, 'ready': False, 'productAccepted': False,
            'publicationAuthorized': False, 'deploymentAuthorized': False, **extra}


def prepare(config_path, config_sha, state):
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    pin = {'path': str(config_path), 'sha256': config_sha}
    c = unique_json(api.pinned(pin))
    onboarding._keys(c, ('format', 'schemaVersion', 'manifestSha256', 'repairConfig',
        'reviewMaxOutputTokens', 'reviewMaxOperationNanoUsd',
        'executionMaxOutputTokens', 'executionMaxOperationNanoUsd',
        *(('reviewEvidencePolicy',) if 'reviewEvidencePolicy' in c else ()),
        *(('reviewMaxSourceBytes',) if 'reviewMaxSourceBytes' in c else ()),
        *(('findingDispositionPolicy',) if 'findingDispositionPolicy' in c else ()),
        *(('sourceReadModePolicy',) if 'sourceReadModePolicy' in c else ())), 'bounded repair local run')
    resume.validation.review.base.evidence_policy(c.get('reviewEvidencePolicy'))
    resume.validation.review.base.finding_policy(c.get('findingDispositionPolicy'))
    if 'reviewMaxSourceBytes' in c:resume.validation.review.base.source_byte_limit(c['reviewMaxSourceBytes'])
    if 'sourceReadModePolicy' in c:resume.validation.container.source_read_mode_policy(c['sourceReadModePolicy'])
    if c['format'] != FORMAT or type(c['schemaVersion']) is not int or c['schemaVersion'] != 1:
        raise Refused('Unsupported bounded repair local run')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    for prefix in ('review', 'execution'):
        api.token_bounds(0, c[prefix + 'MaxOutputTokens'])
        amount = c[prefix + 'MaxOperationNanoUsd']
        if type(amount) is not int or not 1 <= amount <= api.LIMIT:
            raise Refused('Invalid bounded repair review ceiling')
    onboarding._keys(c['repairConfig'], ('path', 'sha256'), 'bounded repair config pin')
    api.pinned(c['repairConfig'])
    if config_path.is_relative_to(state) or Path(c['repairConfig']['path']).is_relative_to(state):
        raise Refused('Outer repair inputs must be outside its state')
    parts = repair.prepare(Path(c['repairConfig']['path']), c['repairConfig']['sha256'], state / 'repair')
    rc, wc, initial, _, _, _, _, _, derived_wc, _, repair_intent = parts
    if rc['manifestSha256'] != c['manifestSha256']:
        raise Refused('Bounded repair and child controller must be identical')
    # Guard the whole parent, not only its repair child, against overlapping inputs.
    protected = [ROOT, Path(rc['originalProducer']['workflowState']).parent.parent,
        Path(rc['originalProducer']['producerController']['path']), Path(rc['rejectedReview']['state']),
        Path(wc['source']['checkout']), Path(wc['campaignDirectory']),
        Path(wc['historicalBudget']['directory']), Path(wc['historicalBudget']['legacy']['path']).parent]
    if 'parentRepair' in rc:
        protected += [Path(rc['parentRepair']['workflowState']), Path(rc['parentRepair']['producerController']['path'])]
    if 'failedValidation' in rc:
        protected += [Path(rc['failedValidation']['state']), Path(rc['failedValidation']['controller']['path'])]
    protected += api.prior_campaign_roots(wc)
    if any(state == p or state in p.parents or p in state.parents for p in protected):
        raise Refused('Bounded repair state overlaps protected evidence, source or accounting')
    # No immutable input may be placed inside the outer state, including a
    # sibling of the repair child. Child validation alone cannot cover that.
    def protect_pins(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                if api.canonical_path(value['path']).is_relative_to(state):
                    raise Refused('Bounded repair inputs must be outside its state')
            for child in value.values(): protect_pins(child)
        elif isinstance(value, list):
            for child in value: protect_pins(child)
    for value in (rc, wc): protect_pins(value)
    derived_pin = {'path': str(state / 'repair/derived-workflow-config.json'),
                   'sha256': api.digest(api.encoded(derived_wc))}
    phase = {'format': resume.FORMAT, 'schemaVersion': 1, 'manifestSha256': c['manifestSha256'],
        'producerKind': 'repair-candidate', 'repairConfig': c['repairConfig'],
        'workflowConfig': derived_pin, 'workflowState': str(state / 'repair'),
        'producerController': {'path': str(ROOT), 'manifestSha256': c['manifestSha256']},
        'python': rc['python'],
        'producerVerificationTimeoutSeconds': resume.validation.validation.REPAIRED_VERIFIER_TIMEOUT,
        **{key: c[key] for key in ('reviewMaxOutputTokens', 'reviewMaxOperationNanoUsd',
                                  'executionMaxOutputTokens', 'executionMaxOperationNanoUsd')}}
    if 'reviewEvidencePolicy' in c:
        phase['reviewEvidencePolicy'] = c['reviewEvidencePolicy']
    if 'reviewMaxSourceBytes' in c:phase['reviewMaxSourceBytes'] = c['reviewMaxSourceBytes']
    if 'findingDispositionPolicy' in c:
        phase['findingDispositionPolicy'] = c['findingDispositionPolicy']
    if 'sourceReadModePolicy' in c:phase['sourceReadModePolicy'] = c['sourceReadModePolicy']
    if 'campaignContinuation' in rc:
        phase['campaignContinuation'] = rc['campaignContinuation']
    intent = {'format': FORMAT, 'schemaVersion': 1, 'stage': 'grant-one-repair-through-reviewed-local-evidence',
        'config': pin, 'state': str(state), 'controllerManifestSha256': c['manifestSha256'],
        'repairConfig': c['repairConfig'], 'repairIntent': repair_intent,
        'derivedResumeConfig': phase, 'derivedResumeConfigSha256': api.digest(api.encoded(phase)),
        'originalCandidateReceipt': initial['candidateReceipt'], 'originalBaseline': wc['source']['commit'],
        'requirement': wc['requirement'], 'resourceCatalog': wc['resourceCatalog'],
        'campaignDirectory': wc['campaignDirectory'], 'historicalBudget': wc['historicalBudget'],
        'combinedHistoricalAndCampaignLimitNanoUsd': api.campaign_limit(wc),
        'maximumRepairGenerations': 1, 'maximumReviewGenerations': 2,
        'maximumGenerationAttempts': 3, 'maximumContainerAttempts': 1,
        'phases': ['one-source-bound-repair', 'fresh-independent-source-workspace-execution-review',
                   'qualified-contained-local-validation', 'fresh-executed-json-and-png-evidence-review'],
        'disclosure': 'Original requirement and approved plan, exact committed source and rejected diagnostic observations to the same provider; after fresh valid review and contained tests, source-selected bounded retained JSON bytes and PNG pixels.',
        'credentialReuse': 'Same approved provider credential within the three bounded model phases; never retained.',
        'diagnosticAuthority': 'none', 'freshIndependentReviewRequired': True,
        'projectCommandsAuthorized': True, 'publicationAuthorized': False, 'deploymentAuthorized': False,
        'productAcceptanceAuthorized': False,
        'boundary': 'One repair only. No automatic retry or second repair, no old grant upgrades, no review laundering, no uncertain-effect replay, no publication or deployment.'}
    return c, wc, phase, intent


def accounting(wc, state):
    ledger = resume.existing_campaign(wc).read()
    allowed = {str(state / 'repair/implementation-attempt'),
               str(state / 'reviewed/validation/review-attempt'), str(state / 'reviewed/execution-review')}
    own = [row for row in ledger['requests'] if Path(row['state']) == state or state in Path(row['state']).parents]
    states = [row['state'] for row in own]
    if (not state.exists() and own) or len(own) > 3 or len(states) != len(set(states)) or not set(states) <= allowed:
        raise Refused('Outer continuation exceeds its exact one-repair/two-review accounting scope')
    for attempt in map(Path, allowed):
        for name in ('reservation.json', 'result.json'):
            path = attempt / name
            if not path.exists(): continue
            retained = unique_json(api.file_bytes(path))
            if name == 'result.json':
                retained = retained.get('reservation')
                if retained is None: continue
            match = next((row for row in ledger['requests'] if row['id'] == retained.get('id')), None)
            if (match is None or retained.get('state') != str(attempt)
                    or any(match.get(k) != v for k, v in retained.items() if k != 'status')
                    or (retained.get('status') != 'reserved' and retained != match)):
                raise Refused('Bounded repair paid reservation disappeared or changed')


def marker(state, name, expected, child, *, create):
    path = state / name
    if path.exists():
        if unique_json(api.file_bytes(path)) != expected:
            raise Refused('Bounded repair consumed child dispatch changed')
        if not child.exists():
            if not create:
                raise Refused('Bounded repair child outcome unknown')
            return False
    else:
        if child.exists():
            raise Refused('Unrecorded bounded repair child; never adopt it')
        if not create:
            raise Refused('Bounded repair child was never dispatched')
        api.durable(state, name, api.encoded(expected))
    return True


def completion(repaired, reviewed, state):
    return {**reviewed, 'format': FORMAT,
        'repair': {'result': work.pin(state / 'repair-result.json'),
                   'candidateReceipt': repaired['candidateReceipt'], 'grant': repaired['repairGrant'],
                   'originalCandidateReceipt': repaired['originalCandidateReceipt'],
                   'diagnosticAuthority': 'none'},
        'reviewedRun': {'config': work.pin(state / 'resume-config.json'), 'state': str(state / 'reviewed'),
                        'grant': work.pin(state / 'reviewed/grant.json'),
                        'result': work.pin(state / 'reviewed/result.json')},
        # The child model's acceptance field is retained in its own evidence,
        # but this outer grant authorizes local evidence, not product acceptance.
        'ready': False, 'productAccepted': False, 'productAcceptanceAuthorized': False,
        'deliveryReady': False, 'publicationAuthorized': False, 'deploymentAuthorized': False,
        'nextStage': 'independent-product-acceptance-and-separately-authorized-publication-required'}


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None, verify_only=False):
    config = unique_json(api.pinned({'path': str(api.canonical_path(config_path)), 'sha256': config_sha}))
    if config.get('schemaVersion') == 2:
        import flow_repair_loop
        return flow_repair_loop.run(config_path, config_sha, state, authorization,
            key_file=key_file, transport=transport, verify_only=verify_only)
    if verify_only and (authorization is not None or key_file is not None or transport is not None):
        raise Refused('Bounded repair verification takes no effect authority or credentials')
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    c, wc, phase, intent = prepare(config_path, config_sha, state)
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    if not state.exists():
        if verify_only: raise Refused('Bounded repair verification requires completed state')
        if authorization is None:
            if key_file is not None or transport is not None:
                raise Refused('Ungranted outer preview takes no credential or transport')
            return report('awaiting-authorization', **grant)
        work.require_authorization(authorization, intent)
        accounting(wc, state)
        with new_owned_directory(state):
            api.fsync_directory(state.parent)
        for name, raw in [('grant.json', api.encoded(grant)), ('resume-config.json', api.encoded(phase)), ('run.lock', b'')]:
            api.durable(state, name, raw)
    if authorization is not None:
        work.require_authorization(authorization, intent)
    if unique_json(api.file_bytes(state / 'grant.json')) != grant:
        raise Refused('Outer repair grant changed; never upgrade it')
    if api.file_bytes(state / 'resume-config.json') != api.encoded(phase):
        raise Refused('Derived reviewed-continuation configuration changed or missing')
    with os.fdopen(owned_file(state, 'run.lock', os.O_RDONLY), 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        accounting(wc, state)
        terminal = verify_only or (state / 'result.json').exists() or (state / 'terminal-intent.json').exists()
        if terminal:
            if unique_json(api.file_bytes(state / 'terminal-intent.json')) != grant:
                raise Refused('Bounded repair terminal grant changed')
            for name in ('result.json', 'repair-dispatch.json', 'repair-completion.json',
                         'repair-result.json', 'resume-dispatch.json'):
                api.file_bytes(state / name)
        rp = c['repairConfig']; rs = state / 'repair'
        if not terminal and not (state / 'repair-dispatch.json').exists() and key_file is None and transport is None:
            return report('awaiting-provider-credential', phase='repair')
        if not marker(state, 'repair-dispatch.json', intent['repairIntent'], rs, create=not terminal):
            return report('blocked', phase='repair', reason='Repair outcome unknown; never replay', replayPermitted=False)
        verify_repair = terminal or (rs / 'candidate-receipt.json').exists()
        repaired = repair.run(Path(rp['path']), rp['sha256'], rs,
            None if verify_repair else api.digest(api.encoded(intent['repairIntent'])),
            key_file=None if verify_repair else key_file, transport=None if verify_repair else transport,
            verify_only=verify_repair)
        # Completed repair verification never takes live credentials.
        if repaired['status'] != 'candidate-prepared':
            return report(repaired['status'], phase='repair', childResult=repaired)
        if repaired['derivedWorkflowConfig'] != phase['workflowConfig']:
            raise Refused('Repaired producer differs from pre-granted derived workflow')
        if terminal or (state / 'repair-completion.json').exists():
            if unique_json(api.file_bytes(state / 'repair-completion.json')) != repaired:
                raise Refused('Consumed repair completion changed')
            if unique_json(api.file_bytes(state / 'repair-result.json')) != repaired:
                raise Refused('Consumed repaired producer result changed or missing')
        else:
            if any((state / name).exists() for name in ('repair-result.json', 'resume-dispatch.json', 'reviewed')):
                raise Refused('Unrecorded repair completion; never reconstruct it')
            api.durable(state, 'repair-completion.json', api.encoded(repaired))
            api.durable(state, 'repair-result.json', api.encoded(repaired))
        pp = work.pin(state / 'resume-config.json'); ps = state / 'reviewed'
        resume_intent = resume.prepare(Path(pp['path']), pp['sha256'], ps)[-1]
        if not terminal and not (state / 'resume-dispatch.json').exists() and key_file is None and transport is None:
            return report('awaiting-provider-credential', phase='fresh-review', repair=repaired)
        if not marker(state, 'resume-dispatch.json', resume_intent, ps, create=not terminal):
            return report('blocked', phase='fresh-review', reason='Reviewed continuation outcome unknown; never replay', replayPermitted=False)
        verify_reviewed = terminal or (ps / 'result.json').exists() or (ps / 'terminal-intent.json').exists()
        reviewed = resume.run(Path(pp['path']), pp['sha256'], ps,
            None if verify_reviewed else api.digest(api.encoded(resume_intent)),
            key_file=None if verify_reviewed else key_file, transport=None if verify_reviewed else transport,
            verify_only=verify_reviewed)
        accounting(wc, state)
        if not (ps / 'result.json').exists():
            return report(reviewed['status'], phase='reviewed-validation', childResult=reviewed, repair=repaired)
        value = completion(repaired, reviewed, state)
        if terminal:
            if (unique_json(api.file_bytes(state / 'terminal-intent.json')) != grant
                    or unique_json(api.file_bytes(state / 'result.json')) != value):
                raise Refused('Bounded repair terminal result or grant changed')
        else:
            api.durable(state, 'terminal-intent.json', api.encoded(grant))
            api.durable(state, 'result.json', api.encoded(value))
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--authorize-intent')
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    value = run(args.config, args.config_sha256, args.state, args.authorize_intent,
                key_file=args.key_file, verify_only=args.verify)
    print(json.dumps(value, sort_keys=True))
    return 0 if value['status'] in ('awaiting-authorization', 'local-feature-evidence-reviewed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
