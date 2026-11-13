"""Budget-governed candidate review, execution and repair, with immutable phases.

Schema two starts from a completed candidate. Each failure is independently
verified before deriving the next repair. Unknown effects stop without replay.
There is no repair-count or ancestry-depth ceiling; money and the user's paid
step authority govern the loop. Schema one remains the original exact grant.
"""
from __future__ import annotations
import copy
import fcntl
import os
from pathlib import Path

import flow_api_delivery as api
import flow_generation_budget as generations
import flow_repair_candidate as repair
import flow_resume_candidate as resume
import flow_workflow as work
from aim_ci import control_package, project_onboarding as onboarding
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file
from aim_ci.project_discovery import unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-repair-local-run'


def report(status, **extra):
    return {'format': FORMAT, 'schemaVersion': 2, 'status': status,
            'publicationAuthorized': False, 'deploymentAuthorized': False,
            'ready': False, 'deliveryReady': False, 'productAccepted': False, **extra}


def verified_failed_run(spec, rc, initial, loop_state):
    """Reverify an immutable terminal failure with its own installed controller."""
    onboarding._keys(spec, ('controller', 'config', 'state', 'grant', 'result'), 'retained failed run')
    onboarding._keys(spec['controller'], ('path', 'manifestSha256'), 'failed run controller')
    old_state = api.canonical_path(spec['state'])
    controller = api.canonical_path(spec['controller']['path'])
    for path in (old_state, controller):
        if path == loop_state or path in loop_state.parents or loop_state in path.parents:
            raise Refused('Loop must be disjoint from retained failure/controller')
    for key in ('config', 'grant', 'result'):
        if api.canonical_path(spec[key]['path']).is_relative_to(loop_state):
            raise Refused('Retained failed run inputs must be outside new loop')
    if (spec['grant']['path'] != str(old_state / 'grant.json')
            or spec['result']['path'] != str(old_state / 'result.json')):
        raise Refused('Failed run must pin its exact retained grant/result')
    before = resume.validation.validation.prior_controller(controller, spec['controller']['manifestSha256'])
    raw = {key: api.pinned(spec[key]) for key in ('config', 'grant', 'result')}
    old, grant, result = (unique_json(raw[key]) for key in ('config', 'grant', 'result'))
    intent = grant.get('intent', {})
    if (old.get('format') != resume.FORMAT or old.get('manifestSha256') != spec['controller']['manifestSha256']
            or intent.get('config') != spec['config'] or intent.get('state') != str(old_state)
            or intent.get('controllerManifestSha256') != spec['controller']['manifestSha256']
            or grant.get('intentSha256') != api.digest(api.encoded(intent))
            or result.get('format') != resume.FORMAT or result.get('status') != 'local-validation-failed'
            or result.get('localFeatureAccepted') is not False
            or result.get('candidate') != initial['candidate']
            or result.get('candidateReceipt') != initial['candidateReceipt']
            or any(old.get(key) != rc.get(key) for key in
                   ('workflowConfig', 'workflowState', 'producerController', 'producerKind', 'repairConfig'))):
        raise Refused('Retained run is not a terminal failed validation of this exact producer/candidate')
    python = old['python']; api.pinned(python)
    if not os.access(python['path'], os.X_OK):
        raise Refused('Retained failure verifier Python is not executable')
    command = [python['path'], '-IBS', str(controller / 'aim-flow.py'), 'resume-candidate',
        '--config', spec['config']['path'], '--config-sha256', spec['config']['sha256'],
        '--state', str(old_state), '--verify']
    process = resume.validation.validation.verification_process(command, ROOT,
        {'PATH': os.defpath, 'HOME': str(ROOT), 'LC_ALL': 'C'},
        metadata_limit=max(64000, len(raw['result']) * 2 + 4096),
        timeout_seconds=resume.validation.validation.producer_timeout(rc),
        expected_terminal_failure=result)
    # The resume CLI deliberately exits 1 for an intact failed local outcome.
    if process.returncode != 1 or process.stderr or unique_json(process.stdout) != result:
        raise Refused('Pinned failed-run verifier did not reproduce the exact terminal failure')
    if (before != resume.validation.validation.prior_controller(controller, spec['controller']['manifestSha256'])
            or any(api.pinned(spec[key]) != value for key, value in raw.items())):
        raise Refused('Retained failure changed during verification')
    api.pinned(python)
    return old, result


def prepare(config_path, config_sha, state):
    pin = {'path': str(api.canonical_path(config_path)), 'sha256': config_sha}
    state = api.canonical_path(state)
    c = unique_json(api.pinned(pin))
    onboarding._keys(c, ('format', 'schemaVersion', 'manifestSha256', 'resumeConfig',
        'repairTemplate', 'maximumNewGenerations', 'repairContextPaths',
        *(('retainedFailedRun',) if 'retainedFailedRun' in c else ())), 'budget-governed repair loop')
    if c['format'] != FORMAT or type(c['schemaVersion']) is not int or c['schemaVersion'] != 2:
        raise Refused('Unsupported repair loop schema')
    maximum = c['maximumNewGenerations']
    if type(maximum) is not int or maximum < 1:
        raise Refused('Repair loop requires an explicit positive paid-step authority')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    rc = unique_json(api.pinned(c['resumeConfig']))
    template = unique_json(api.pinned(c['repairTemplate']))
    onboarding._texts(c['repairContextPaths'], 'repair context paths')
    for path in c['repairContextPaths']: onboarding._source_path(path)
    if sorted(set(c['repairContextPaths'])) != c['repairContextPaths']:
        raise Refused('Repair context paths must be sorted and unique')
    if rc['manifestSha256'] != c['manifestSha256']:
        raise Refused('Loop and current reviewed child must use the same controller')
    # Genuine original authority is rechecked by every generated repair; the
    # template contributes pins and limits, never a hand-authored patch/result.
    api.pinned(template['originalProducer']['workflowConfig'])
    api.pinned(template['intakeAuthority']['grant'])
    _, wc, initial, _, child_intent = resume.prepare(Path(c['resumeConfig']['path']), c['resumeConfig']['sha256'], state / 'review-0')
    protected = [ROOT, Path(rc['workflowState']), Path(rc['producerController']['path']),
                 Path(wc['source']['checkout']), Path(wc['campaignDirectory']),
                 Path(wc['historicalBudget']['directory']), Path(wc['historicalBudget']['legacy']['path']).parent]
    protected += api.prior_campaign_roots(wc)
    for p in protected:
        if state == p or p in state.parents or state in p.parents:
            raise Refused('Loop state overlaps protected evidence/source/accounting')
    for p in (pin, c['resumeConfig'], c['repairTemplate']):
        if Path(p['path']).is_relative_to(state):
            raise Refused('Loop inputs must be outside owned state')
    if state.exists():
        baseline = unique_json(api.file_bytes(state / 'budget-baseline.json'))
    else:
        baseline = api.read_existing_campaign(wc)
    reconcile(wc, baseline, state, maximum)
    intent = {'format': FORMAT, 'schemaVersion': 2, 'config': pin, 'state': str(state),
        'controllerManifestSha256': c['manifestSha256'], 'resumeConfig': c['resumeConfig'],
        'repairTemplate': c['repairTemplate'], 'maximumNewGenerations': maximum,
        'budgetBaselineSha256': api.digest(api.encoded(baseline)),
        'campaignDirectory': wc['campaignDirectory'], 'campaignLimitNanoUsd': api.campaign_limit(wc),
        'initialCandidateReceipt': initial['candidateReceipt'], 'initialReviewIntent': child_intent,
        'requirement': wc['requirement'], 'repairContextPaths': c['repairContextPaths'],
        'phases': ['fresh-source-review', 'isolated-project-validation', 'fresh-evidence-review',
                   'source-bound-repair-on-verified-blocking-failure', 'review-again-within-budget'],
        'unknownEffects': 'stop-without-replay', 'nonblockingFindings': 'retain-and-report',
        'projectCommandsAuthorized': True, 'publicationAuthorized': False, 'deploymentAuthorized': False}
    if 'retainedFailedRun' in c:
        verified_failed_run(c['retainedFailedRun'], rc, initial, state)
        intent['retainedFailedRun'] = c['retainedFailedRun']
        intent['phases'] = ['verify-retained-failed-validation-without-replay',
            'repair-from-retained-diagnostics', *intent['phases']]
    return c, rc, template, wc, baseline, intent


def reconcile(wc, baseline, state, maximum):
    current = api.read_existing_campaign(wc)
    if (current['identity'] != baseline['identity']
            or current['requests'][:len(baseline['requests'])] != baseline['requests']):
        raise Refused('Repair loop accounting prefix changed')
    added = current['requests'][len(baseline['requests']):]
    if len(added) > maximum or any(not Path(r['state']).is_relative_to(state) for r in added):
        raise Refused('Repair loop paid-step authority exceeded or concurrent campaign activity detected')
    if len({r['state'] for r in added}) != len(added):
        raise Refused('Repair loop contains duplicated paid phase state')
    return current, added


def capacity(wc, baseline, state, maximum, required_steps, required_money):
    current, added = reconcile(wc, baseline, state, maximum)
    if any(r['status'] != 'completed' for r in added):
        return False, 'Unsettled paid outcome; reconcile without replay'
    identity = current['identity']
    available = api.campaign_limit(wc) - identity.get('allocatedPriorCampaignNanoUsd', wc['historicalBudget']['liabilityNanoUsd']) - sum(r['reservedNanoUsd'] for r in current['requests'])
    if maximum - len(added) < required_steps:
        return False, 'Insufficient authorized paid steps for the next complete repair/review/evidence cycle'
    if available < required_money:
        return False, 'Insufficient unreserved campaign budget for the next complete cycle'
    return True, None


def child(kind, config_pin, state, owner, key_file, transport, verify_only=False):
    module = resume if kind == 'review' else repair
    dispatch = owner / (state.name + '-dispatch.json')
    retained = owner / (state.name + '-result.json')
    expected = module.prepare(Path(config_pin['path']), config_pin['sha256'], state)[-1]
    if dispatch.exists():
        if unique_json(api.file_bytes(dispatch)) != expected:
            raise Refused('Loop child dispatch changed')
        if not state.exists():
            return report('blocked', reason='Dispatched child outcome unknown; never replay', replayPermitted=False)
    else:
        if verify_only: raise Refused('Loop child dispatch missing')
        if state.exists(): raise Refused('Unrecorded loop child; never adopt it')
        api.durable(owner, dispatch.name, api.encoded(expected))
    if retained.exists():
        # Do not pass effects authority to completed child verification.
        old = unique_json(api.file_bytes(retained))
        terminal = (state / ('candidate-receipt.json' if kind == 'repair' else 'result.json')).exists()
        if terminal:
            value = module.run(Path(config_pin['path']), config_pin['sha256'], state, verify_only=True)
        elif kind == 'review':
            value = resume.verify_partial(Path(config_pin['path']), config_pin['sha256'], state)
        else:
            raise Refused('Repair has no completed candidate to verify')
        if value != old: raise Refused('Loop retained child result changed')
        return value
    if verify_only: raise Refused('Loop child result missing or uncertain')
    value = module.run(Path(config_pin['path']), config_pin['sha256'], state,
        api.digest(api.encoded(expected)), key_file=key_file, transport=transport)
    api.durable(owner, retained.name, api.encoded(value))
    return value


def repair_config(c, template, rc, review_state, result, wc, budget_pin=None, diagnostic_controller=None):
    """Derive diagnostics from consumed paid results and completed runtime receipts."""
    source = resume.validation.source_review_state(rc, review_state / 'validation')
    status = api.status(source)
    if (status.get('reservation') or {}).get('status') != 'completed':
        raise Refused('No completed paid source diagnostics; unknown/refused steps cannot be repaired')
    if status['status'] not in ('review-blocked', 'source-review-ready'):
        raise Refused('No verified source review; malformed, rejected or unknown model output is not code-repair authority')
    evidence = resume.validation.review.verify_evidence(
        work.pin(source / 'review.json'), work.pin(source / 'packet.json'))
    source_defects = bool(resume.validation.review.base.blocking_findings(evidence))
    config = copy.deepcopy(template)
    config.update(manifestSha256=c['manifestSha256'], producerVerificationTimeoutSeconds=None,
                  campaignBudget=budget_pin or work.pin(Path(wc['campaignDirectory']) / 'budget.json'))
    for key in ('failedValidation', 'failedExecutionReview', 'parentRepair', 'repairOrdinal'):
        config.pop(key, None)
    if 'campaignContinuation' in rc: config['campaignContinuation'] = rc['campaignContinuation']
    config['rejectedReview'] = {'state': str(source), 'files': {
        name: work.pin(source / name)['sha256'] for name in repair.REVIEW_FILES}}
    if (source / 'review.json').exists():
        config['rejectedReview']['files']['review.json'] = work.pin(source / 'review.json')['sha256']
    if rc.get('producerKind') == 'repair-candidate':
        config['parentRepair'] = {k: rc[k] for k in ('producerKind', 'producerController',
                                                    'workflowConfig', 'workflowState', 'repairConfig')}
        depth = 2; ancestor = unique_json(api.pinned(rc['repairConfig'])); seen = set()
        while 'parentRepair' in ancestor:
            pin = ancestor['parentRepair']['repairConfig']; identity = (pin['path'], pin['sha256'])
            if identity in seen: raise Refused('Repair loop ancestry cycle')
            seen.add(identity); depth += 1; ancestor = unique_json(api.pinned(pin))
        if depth >= 3: config['repairOrdinal'] = depth
    validation_result = result.get('validation', {})
    backend = validation_result.get('executionBackend', 'container')
    if backend not in ('container', 'host'): raise Refused('Unknown failed execution backend')
    failed = review_state / 'validation' / (backend + '-attempt')
    if (failed / 'result.json').exists():
        receipt = unique_json(api.file_bytes(failed / 'result.json'))
        if receipt.get('status') == 'failed':
            config['failedValidation'] = {'controller': diagnostic_controller or {'path': str(ROOT), 'manifestSha256': c['manifestSha256']},
                'state': str(failed), 'intent': work.pin(failed / 'intent.json'),
                'result': work.pin(failed / 'result.json'), 'contextPaths': c['repairContextPaths'],
                **({'backend': 'host'} if backend == 'host' else {})}
    if result.get('status') == 'executed-evidence-review-blocked':
        config['failedExecutionReview'] = {'config': work.pin(review_state / 'execution-review-config.json'),
            'state': str(review_state / 'execution-review'), 'contextPaths': c['repairContextPaths']}
    if (not source_defects and 'failedValidation' not in config
            and 'failedExecutionReview' not in config):
        raise Refused('No verified blocking source defect or retained failed validation/evidence review; '
                      + str(result.get('status', 'unknown outcome'))
                      + ' is pending evidence or infrastructure, not code-repair authority')
    return config


def retain(state, name, value, verify_only):
    if verify_only:
        if api.file_bytes(state / name) != api.encoded(value):
            raise Refused('Retained loop input changed: ' + name)
        return work.pin(state / name)
    return work.retain(state, name, value)


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None, verify_only=False):
    if verify_only and any(x is not None for x in (authorization, key_file, transport)):
        raise Refused('Loop verification takes no effects authority')
    state = api.canonical_path(state)
    c, rc, template, wc, baseline, intent = prepare(config_path, config_sha, state)
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    if not state.exists():
        if verify_only: raise Refused('Loop verification requires completed state')
        if authorization is None:
            if key_file is not None or transport is not None: raise Refused('Preview takes no provider credential')
            return report('awaiting-authorization', **grant)
        work.require_authorization(authorization, intent)
        with new_owned_directory(state): api.fsync_directory(state.parent)
        api.durable(state, 'grant.json', api.encoded(grant))
        api.durable(state, 'budget-baseline.json', api.encoded(baseline))
        api.durable(state, 'run.lock', b'')
    if unique_json(api.file_bytes(state / 'grant.json')) != grant:
        raise Refused('Loop grant changed; never upgrade it')
    if authorization is not None: work.require_authorization(authorization, intent)
    def guard(campaign, data, attempt, amount):
        if str(campaign.directory) != wc['campaignDirectory'] or not attempt.is_relative_to(state):
            raise Refused('Generation outside exact loop campaign/state')
        current, added = reconcile(wc, baseline, state, c['maximumNewGenerations'])
        if current != data or len(added) >= c['maximumNewGenerations']:
            raise Refused('Loop paid-step authority exhausted')
        if any(row['status'] != 'completed' for row in added):
            raise Refused('Loop contains uncertain paid outcome; never dispatch again')
    with os.fdopen(owned_file(state, 'run.lock', os.O_RDONLY), 'rb') as lock, generations.authority(guard):
        fcntl.flock(lock, fcntl.LOCK_EX)
        iteration = 0; review_pin = c['resumeConfig']; phases = []
        while True:
            retained_failure = c.get('retainedFailedRun') if iteration == 0 else None
            diagnostic_rc = rc
            if retained_failure:
                # prepare already verified this immutable outcome; no old run is dispatched.
                review_state = Path(retained_failure['state'])
                value = unique_json(api.pinned(retained_failure['result']))
                diagnostic_rc = unique_json(api.pinned(retained_failure['config']))
                if 'campaignContinuation' in rc:
                    diagnostic_rc = {**diagnostic_rc, 'campaignContinuation': rc['campaignContinuation']}
                phases.append({'kind': 'retained-failed-review', 'config': retained_failure['config'],
                    'state': str(review_state), 'status': value['status']})
            else:
                review_state = state / ('review-' + str(iteration))
                if not (state / (review_state.name + '-dispatch.json')).exists():
                    if verify_only: raise Refused('Loop verification encountered missing iteration')
                    if key_file is None and transport is None: return report('awaiting-provider-credential')
                    ok, reason = capacity(wc, baseline, state, c['maximumNewGenerations'],
                        1 if 'sourceReviewReuse' in rc else 2,
                        (0 if 'sourceReviewReuse' in rc else rc['reviewMaxOperationNanoUsd']) + rc['executionMaxOperationNanoUsd'])
                    if not ok: return report('budget-blocked', reason=reason, phases=phases)
                value = child('review', review_pin, review_state, state, key_file, transport, verify_only)
                phases.append({'kind': 'review', 'config': review_pin, 'state': str(review_state), 'status': value['status']})
            if value.get('localFeatureAccepted') is True:
                final = {**value, **report('local-feature-evidence-reviewed'), 'localFeatureAccepted': True,
                    'reviewedRun': {'config': review_pin, 'state': str(review_state),
                        'grant': work.pin(review_state / 'grant.json'), 'result': work.pin(review_state / 'result.json')},
                    'phases': phases, 'paidGenerations': len(reconcile(wc, baseline, state, c['maximumNewGenerations'])[1])}
                retain(state, 'result.json', final, verify_only)
                return final
            repair_state = state / ('repair-' + str(iteration))
            if not (state / (repair_state.name + '-dispatch.json')).exists():
                if not verify_only and key_file is None and transport is None:
                    return report('awaiting-provider-credential', phase='repair', phases=phases)
                ok, reason = capacity(wc, baseline, state, c['maximumNewGenerations'], 3,
                    template['maxOperationNanoUsd'] + rc['reviewMaxOperationNanoUsd'] + rc['executionMaxOperationNanoUsd'])
                if not ok: return report('budget-blocked', reason=reason, lastResult=value, phases=phases)
            try:
                previous_config = state / (repair_state.name + '-config.json')
                retained_budget = unique_json(api.file_bytes(previous_config))['campaignBudget'] if previous_config.exists() else None
                derived = repair_config(c, template, diagnostic_rc, review_state, value, wc, retained_budget,
                    **({'diagnostic_controller': retained_failure['controller']} if retained_failure else {}))
            except Refused as error:
                return report('blocked', reason=str(error), lastResult=value, phases=phases, replayPermitted=False)
            rp = retain(state, repair_state.name + '-config.json', derived, verify_only)
            repaired = child('repair', rp, repair_state, state, key_file, transport, verify_only)
            phases.append({'kind': 'repair', 'config': rp, 'state': str(repair_state), 'status': repaired['status']})
            if repaired['status'] != 'candidate-prepared':
                return report('blocked', lastResult=repaired, phases=phases, replayPermitted=False)
            iteration += 1
            rc = {**rc, 'producerKind': 'repair-candidate', 'repairConfig': rp,
                  'producerController': {'path': str(ROOT), 'manifestSha256': c['manifestSha256']},
                  'workflowConfig': repaired['derivedWorkflowConfig'], 'workflowState': str(repair_state),
                  'producerVerificationTimeoutSeconds': None}
            for key in ('sourceReviewReuse', 'interruptedPredecessor'): rc.pop(key, None)
            review_pin = retain(state, 'review-' + str(iteration) + '-config.json', rc, verify_only)
