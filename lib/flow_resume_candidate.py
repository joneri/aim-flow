"""A new, bounded grant for a previously completed Flow-generated candidate.

The original producer and grant stay immutable. Only a fresh source/workspace/
execution-plan review, one contained validation and executed-evidence review run.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

import flow_api_delivery as api
import flow_run as local
import flow_workflow as work
import flow_workflow_validation as validation
from aim_ci import control_package, project_onboarding as onboarding
from aim_ci.owned_paths import Refused, new_owned_directory, owned_directory, owned_file
from aim_ci.project_discovery import unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-resumed-candidate-local-run'


# Complete executable/configuration inventory from the audited schema-1 contract.
# Package version and prose may differ; no historical Python is loaded or run.
PREDECESSOR_CODE_SHA256 = 'e2c8c39f4e9da901f9ebc2b4451596d65f72dd423ba3f22581629f8756b5f745'
PREDECESSOR_FILES = ('run.lock', 'grant.json', 'initial-result.json',
                     'validation-config.json', 'validation-dispatch.json')


def predecessor_spec(c):
    p = c['interruptedPredecessor']
    onboarding._keys(p, ('controller', 'config', 'state', 'files', 'campaignBudget'),
                     'interrupted resumed predecessor')
    onboarding._keys(p['controller'], ('path', 'manifestSha256'), 'interrupted controller')
    onboarding._keys(p['files'], PREDECESSOR_FILES, 'interrupted state inventory')
    return p


@contextmanager
def predecessor_lock(c):
    if 'interruptedPredecessor' not in c:
        yield lambda: None
        return
    p = predecessor_spec(c)
    state = api.canonical_path(p['state'])
    with os.fdopen(owned_file(state, 'run.lock', os.O_RDONLY), 'rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Interrupted predecessor is active; recovery cannot wait or replay') from None
        # No new lock or old-state write. Detect replacement of the locked inode.
        def recheck():
            with os.fdopen(owned_file(state, 'run.lock', os.O_RDONLY), 'rb') as current:
                left, right = os.fstat(stream.fileno()), os.fstat(current.fileno())
                if (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino):
                    raise Refused('Interrupted predecessor lock changed')
            predecessor_files(p)
        recheck()
        yield recheck
        recheck()


def predecessor_files(p):
    state = api.canonical_path(p['state'])
    descriptor = owned_directory(state)
    try:
        if set(os.listdir(descriptor)) != set(PREDECESSOR_FILES):
            raise Refused('Interrupted predecessor has missing inputs or possible validation/effect evidence')
    finally:
        os.close(descriptor)
    raw = {name: api.pinned({'path': str(state / name), 'sha256': p['files'][name]})
           for name in PREDECESSOR_FILES}
    if raw['run.lock'] != b'':
        raise Refused('Interrupted predecessor lock contents changed')
    return raw


def existing_campaign(wc):
    """Read the established accounting authority without initializing anything."""
    path, identity = api.authority(wc)
    if unique_json(api.file_bytes(path)) != identity:
        raise Refused('Interrupted recovery requires the existing campaign authority')
    campaign = object.__new__(api.Campaign)
    api.campaign_account(campaign, wc, identity)
    return campaign


@contextmanager
def campaign_lock(wc):
    with os.fdopen(owned_file(Path(wc['campaignDirectory']), 'budget.lock', os.O_RDONLY), 'rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Campaign accounting is active; retry recovery after it is idle') from None
        yield


def recovery_budget(c, wc, state, baseline):
    p = predecessor_spec(c)
    if p['campaignBudget']['path'] != str(Path(wc['campaignDirectory']) / 'budget.json'):
        raise Refused('Recovery budget must be the unchanged original campaign')
    if api.digest(baseline) != p['campaignBudget']['sha256']:
        raise Refused('Consumed predecessor budget snapshot changed; never repair it')
    api.historical_snapshot(wc['historicalBudget'])
    ledger = existing_campaign(wc).read()
    if unique_json(api.file_bytes(p['campaignBudget']['path'])) != ledger:
        raise Refused('Recovery campaign changed while locked')
    before = unique_json(baseline)
    if (not isinstance(before, dict) or set(before) != {'identity', 'requests'}
            or before['identity'] != ledger['identity'] or not isinstance(before['requests'], list)
            or len(before['requests']) > len(ledger['requests'])):
        raise Refused('Recovery campaign baseline changed')
    for old, now in zip(before['requests'], ledger['requests']):
        # Settlement can only add observations, never release a reservation.
        if old.get('status') == 'reserved' and now.get('status') in ('completed', 'unknown'):
            if any(now.get(k) != v for k, v in old.items() if k != 'status'):
                raise Refused('Recovery historical reservation changed')
        elif old != now:
            raise Refused('Recovery historical reservation changed')
    previous = Path(p['state'])
    own = []
    for entry in ledger['requests']:
        location = api.canonical_path(entry.get('state'))
        if location == previous or previous in location.parents:
            raise Refused('Interrupted predecessor has a generation reservation; outcome cannot be replayed')
        if location == state or state in location.parents:
            own.append(location)
    allowed = {state / 'validation' / 'review-attempt', state / 'execution-review'}
    if ((not state.exists() and own) or len(own) > 2
            or len(own) != len(set(own)) or not set(own) <= allowed):
        raise Refused('Recovery successor exceeds its exact two-generation scope')
    for attempt in allowed:
        # Paid liabilities must remain even after completion or unknown outcomes.
        # Terminal child verification alone only checks retained local receipts.
        for name in ('reservation.json', 'result.json'):
            path = attempt / name
            if not path.exists():
                continue
            retained = unique_json(api.file_bytes(path))
            if name == 'result.json':
                retained = retained.get('reservation')
                if retained is None:
                    continue
            match = next((entry for entry in ledger['requests'] if entry['id'] == retained.get('id')), None)
            if (match is None or retained.get('state') != str(attempt)
                    or any(match.get(key) != value for key, value in retained.items() if key != 'status')
                    or (retained.get('status') != 'reserved' and retained != match)):
                raise Refused('Recovery successor paid reservation disappeared or changed')
    return before


def reconcile_predecessor(c, wc, initial, phase, state):
    p = predecessor_spec(c)
    previous = api.canonical_path(p['state'])
    controller = api.canonical_path(p['controller']['path'])
    for path in (previous, controller):
        if state == path or state in path.parents or path in state.parents:
            raise Refused('Recovery state must be disjoint from interrupted predecessor/controller')
    for pin in (p['config'], p['campaignBudget']):
        if api.canonical_path(pin['path']).is_relative_to(state):
            raise Refused('Recovery predecessor inputs must be outside fresh state')
    manifest = unique_json(validation.validation.prior_controller(controller, p['controller']['manifestSha256']))
    code = {name: sha for name, sha in manifest['files'].items() if not name.startswith('docs/')}
    fingerprint = api.digest(json.dumps(code, sort_keys=True, separators=(',', ':')).encode())
    if fingerprint != PREDECESSOR_CODE_SHA256:
        raise Refused('Interrupted predecessor code contract is not recognized; no historical code execution')
    raw = predecessor_files(p)
    old = unique_json(api.pinned(p['config']))
    expected_config = {k: v for k, v in c.items() if k != 'interruptedPredecessor'}
    expected_config['manifestSha256'] = p['controller']['manifestSha256']
    if old != expected_config:
        raise Refused('Interrupted predecessor config changes producer, scope, limits or budget')
    old_phase = {**phase, 'manifestSha256': old['manifestSha256']}
    old_intent = grant_intent(old, wc, initial, old_phase, p['config'], previous)
    old_grant = {'intent': old_intent, 'intentSha256': api.digest(api.encoded(old_intent))}
    if unique_json(raw['grant.json']) != old_grant:
        raise Refused('Interrupted predecessor grant does not match exact current reconstruction')
    if unique_json(raw['initial-result.json']) != initial or unique_json(raw['validation-config.json']) != old_phase:
        raise Refused('Interrupted predecessor consumed inputs changed')
    cp = {'path': str(previous / 'validation-config.json'), 'sha256': p['files']['validation-config.json']}
    expected = validation.inputs(Path(cp['path']), cp['sha256'], previous / 'validation', controller_root=controller)[-1]
    if unique_json(raw['validation-dispatch.json']) != expected:
        raise Refused('Interrupted predecessor dispatch does not match reconstructed intent')
    baseline = (api.file_bytes(state / 'predecessor-budget.json') if state.exists()
                else api.pinned(p['campaignBudget']))
    with api.historical_lock(wc['historicalBudget']), campaign_lock(wc):
        before = recovery_budget(c, wc, state, baseline)
    predecessor_files(p)
    return baseline, {**p, 'codeContractSha256': fingerprint,
        'generationReservation': 'absent-under-audited-fsync-and-append-only-ledger-contract',
        'containerDispatch': 'excluded-by-required-successful-generation-ordering',
        'tokenPreflight': 'unknown-read-only-provider-transmission-may-have-occurred',
        'preservedHistoricalLiabilityNanoUsd': wc['historicalBudget']['liabilityNanoUsd'],
        'preservedCampaignReservationNanoUsd': sum(e['reservedNanoUsd'] for e in before['requests']),
        'oldStatePreserved': True, 'oldDispatchReplayed': False}


def recovery_claim(c, wc, state, grant, *, create):
    p = predecessor_spec(c)
    # A state, not its mutable formatting/hash, owns one possible successor.
    name = 'resume-recovery-' + api.digest(p['state'].encode()) + '.json'
    expected = {'format': 'aim-flow-interrupted-resume-successor', 'schemaVersion': 1,
        'predecessor': p, 'successorState': str(state), 'successorGrant': grant}
    campaign = Path(wc['campaignDirectory'])
    with api.historical_lock(wc['historicalBudget']), campaign_lock(wc):
        baseline = (api.pinned(p['campaignBudget']) if create is not False
                    else api.file_bytes(state / 'predecessor-budget.json'))
        recovery_budget(c, wc, state, baseline)
        predecessor_files(p)
        if create is not False:
            if (campaign / name).exists():
                raise Refused('Interrupted predecessor already has a consumed successor claim; never repair or branch it')
            if create:
                api.durable(campaign, name, api.encoded(expected))
        elif unique_json(api.file_bytes(campaign / name)) != expected:
            raise Refused('Interrupted predecessor successor claim changed')


def report(status, **extra):
    return {**local.report(status, **extra), 'format': FORMAT}


def prepare(config_path, config_sha, state):
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    pin = {'path': str(config_path), 'sha256': config_sha}
    c = unique_json(api.pinned(pin))
    onboarding._keys(c, ('format', 'schemaVersion', 'manifestSha256', 'workflowConfig',
        'workflowState', 'producerController', 'producerVerificationTimeoutSeconds', 'python',
        'reviewMaxOutputTokens', 'reviewMaxOperationNanoUsd', 'executionMaxOutputTokens',
        'executionMaxOperationNanoUsd',
        *(('producerKind', 'repairConfig') if 'producerKind' in c or 'repairConfig' in c else ()),
        *(('interruptedPredecessor',) if 'interruptedPredecessor' in c else ()),
        *(('campaignContinuation',) if 'campaignContinuation' in c else ()),
        *(('resourceCatalog',) if 'resourceCatalog' in c else ()),
        *(('sourceReviewReuse',) if 'sourceReviewReuse' in c else ()),
        *(('completedValidationReuse',) if 'completedValidationReuse' in c else ()),
        *(('unknownDispositionPolicy',) if 'unknownDispositionPolicy' in c else ()),
        *(('evidenceEvaluationPolicy',) if 'evidenceEvaluationPolicy' in c else ()),
        *(('sourceReadModePolicy',) if 'sourceReadModePolicy' in c else ()),
        *(('reviewEvidencePolicy',) if 'reviewEvidencePolicy' in c else ()),
        *(('reviewMaxSourceBytes',) if 'reviewMaxSourceBytes' in c else ()),
        *(('findingDispositionPolicy',) if 'findingDispositionPolicy' in c else ())), 'resumed candidate run')
    if 'unknownDispositionPolicy' in c and c['unknownDispositionPolicy'] != 'materiality-and-scope-v1':
        raise Refused('Unsupported execution-review unknown disposition policy')
    if 'evidenceEvaluationPolicy' in c and c['evidenceEvaluationPolicy'] != 'assertion-grounded-v1':
        raise Refused('Unsupported execution-review evidence evaluation policy')
    validation.review.base.evidence_policy(c.get('reviewEvidencePolicy'))
    validation.review.base.finding_policy(c.get('findingDispositionPolicy'))
    if 'reviewMaxSourceBytes' in c:validation.review.base.source_byte_limit(c['reviewMaxSourceBytes'])
    if 'sourceReadModePolicy' in c:validation.container.source_read_mode_policy(c['sourceReadModePolicy'])
    if 'completedValidationReuse' in c and any(k in c for k in ('sourceReviewReuse', 'interruptedPredecessor')):
        raise Refused('Completed validation reuse excludes source-only and interrupted recovery')
    if 'sourceReviewReuse' in c and 'interruptedPredecessor' in c:
        raise Refused('Source review reuse and the pre-generation interruption contract are distinct grants')
    if c['format'] != FORMAT or type(c['schemaVersion']) is not int or c['schemaVersion'] != 1:
        raise Refused('Unsupported resumed candidate grant')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    wc = unique_json(api.pinned(c['workflowConfig']))
    ws = api.canonical_path(c['workflowState'])
    api.pinned(c['python'])
    if not os.access(c['python']['path'], os.X_OK):
        raise Refused('Pinned verifier Python must be executable')
    timeout = validation.validation.producer_timeout(c)
    for prefix in ('review', 'execution'):
        api.token_bounds(0, c[prefix + 'MaxOutputTokens'])
        limit = c[prefix + 'MaxOperationNanoUsd']
        if type(limit) is not int or not 1 <= limit <= api.LIMIT:
            raise Refused('Invalid resumed review operation ceiling')
    protected = [ROOT, ws, Path(c['producerController']['path']), Path(wc['source']['checkout']),
        Path(wc['campaignDirectory']), Path(wc['historicalBudget']['directory']),
        Path(wc['historicalBudget']['legacy']['path']).parent]
    if any(state == p or state in p.parents or p in state.parents for p in protected):
        raise Refused('Resumed state must be disjoint from producer/source/controller/budget')
    for p in (config_path, Path(c['workflowConfig']['path']), Path(c['python']['path']),
              Path(wc['requirement']['path']), Path(wc['resourceCatalog']['path'])):
        if p.is_relative_to(state):
            raise Refused('Resumed inputs must be outside fresh state')
    if 'completedValidationReuse' in c:
        import flow_execution_review as executed
        reused_phase, initial, reused_result = executed.verified_completed_validation(c['completedValidationReuse'])
        for key in ('workflowConfig', 'workflowState', 'producerController', 'producerKind', 'repairConfig',
                    'campaignContinuation', 'resourceCatalog', 'reviewEvidencePolicy', 'findingDispositionPolicy',
                    'reviewMaxSourceBytes', 'sourceReadModePolicy'):
            if reused_phase.get(key) != c.get(key):
                raise Refused('Completed validation reuse changes source, funding, runtime or review policy: ' + key)
        for name in ('validationConfig', 'phaseGrant', 'workspaceEvidence'):
            if api.canonical_path(c['completedValidationReuse'][name]['path']).is_relative_to(state):
                raise Refused('Retained validation inputs must be outside fresh state')
        old_state = api.canonical_path(c['completedValidationReuse']['validationState'])
        old_root = api.canonical_path(c['completedValidationReuse']['controller']['path'])
        if any(state == p or state in p.parents or p in state.parents for p in (old_state, old_root)):
            raise Refused('Reuse output overlaps retained validation/controller')
    else:
        initial = validation.verified_producer(c, wc, ws)
    if initial.get('status') != 'candidate-prepared':
        raise Refused('Resume requires a completed genuine Flow candidate')
    wc = api.funded_config(wc, c.get('campaignContinuation'))
    if 'resourceCatalog' in c:
        api.pinned(c['resourceCatalog'])
        wc = {**wc, 'resourceCatalog': c['resourceCatalog']}
        if Path(c['resourceCatalog']['path']).is_relative_to(state):
            raise Refused('New runtime catalog must be outside resumed state')
    if 'campaignContinuation' in c:
        budget = api.canonical_path(wc['campaignDirectory'])
        if state == budget or state in budget.parents or budget in state.parents:
            raise Refused('Resumed state overlaps continuation campaign')
        if Path(c['campaignContinuation']['path']).is_relative_to(state):
            raise Refused('Continuation declaration must be outside resumed state')
    phase = {key: c[key] for key in ('schemaVersion', 'manifestSha256', 'workflowConfig',
        'workflowState', 'producerController', 'producerVerificationTimeoutSeconds', 'python',
        'reviewMaxOutputTokens', 'reviewMaxOperationNanoUsd')}
    phase.update(captureArtifacts=True, refineExecutionPlan=True)
    if 'reviewEvidencePolicy' in c:
        phase['reviewEvidencePolicy'] = c['reviewEvidencePolicy']
    if 'reviewMaxSourceBytes' in c:phase['reviewMaxSourceBytes'] = c['reviewMaxSourceBytes']
    if 'findingDispositionPolicy' in c:
        phase['findingDispositionPolicy'] = c['findingDispositionPolicy']
    if 'resourceCatalog' in c:
        phase['resourceCatalog'] = c['resourceCatalog']
    if 'campaignContinuation' in c:
        phase['campaignContinuation'] = c['campaignContinuation']
    if 'sourceReviewReuse' in c:
        phase['sourceReviewReuse'] = c['sourceReviewReuse']
    if 'sourceReadModePolicy' in c:phase['sourceReadModePolicy'] = c['sourceReadModePolicy']
    if 'producerKind' in c:
        phase.update(producerKind=c['producerKind'], repairConfig=c['repairConfig'])
    if 'completedValidationReuse' in c:
        phase = reused_phase
    intent = grant_intent(c, wc, initial, phase, pin, state)
    if 'resourceCatalog' in c:
        intent['runtimeSelection'] = {'resourceCatalog': c['resourceCatalog'],
            'scope': 'New explicit project runtime selection; source candidate and original producer unchanged. Host resources use normal host/network access, not container isolation.'}
        catalog = validation.derivation.resource_catalog(unique_json(api.pinned(c['resourceCatalog'])))
        backends = {r['executionSpec']['backend'] for r in catalog['resources'] if r['kind'] == 'node-runtime' and 'executionSpec' in r}
        if backends == {'local'}:
            intent.update(maximumHostAttempts=1, maximumContainerAttempts=0)
            intent['phases'][1] = 'project-native-host-tests-with-normal-network'
    if 'completedValidationReuse' in c:
        completed_validation_grant(intent, c['completedValidationReuse'])
    return c, wc, initial, phase, intent


def completed_validation_grant(intent, spec):
    intent.update(completedValidationReuse=spec, maximumGenerationAttempts=1,
        maximumContainerAttempts=0, maximumHostAttempts=0, projectCommandsAuthorized=False,
        phases=['verify-identical-completed-validation-without-replay', 'fresh-executed-json-and-png-evidence-review'])


def grant_intent(c, wc, initial, phase, pin, state):
    ws = Path(c['workflowState'])
    intent = {'format': FORMAT, 'schemaVersion': 1, 'stage': 'grant-completed-candidate-to-reviewed-local-evidence',
        'config': pin, 'state': str(state), 'controllerManifestSha256': c['manifestSha256'],
        'producerController': c['producerController'], 'workflowConfig': c['workflowConfig'],
        'workflowState': str(ws), 'workflowGrant': work.pin(ws / 'grant.json'),
        'candidateReceipt': initial['candidateReceipt'], 'candidate': initial['candidate'],
        'initialResultSha256': api.digest(api.encoded(initial)), 'python': c['python'],
        'producerVerificationTimeoutSeconds': c['producerVerificationTimeoutSeconds'], 'validationConfigSha256': api.digest(api.encoded(phase)),
        'reviewMaxOutputTokens': c['reviewMaxOutputTokens'], 'reviewMaxOperationNanoUsd': c['reviewMaxOperationNanoUsd'],
        'reviewExecuted': {'maxOutputTokens': c['executionMaxOutputTokens'],
            'maxOperationNanoUsd': c['executionMaxOperationNanoUsd']},
        'maximumGenerationAttempts': 2, 'maximumContainerAttempts': 1,
        'phases': ['fresh-source-workspace-and-execution-plan-review', 'qualified-contained-local-tests',
                   'fresh-executed-json-and-png-evidence-review'],
        'requirement': wc['requirement'], 'resourceCatalog': wc['resourceCatalog'],
        'campaignDirectory': wc['campaignDirectory'], 'historicalBudget': wc['historicalBudget'],
        'combinedHistoricalAndCampaignLimitNanoUsd': api.campaign_limit(wc),
        'disclosure': 'Original requirement, assessed plan, exact candidate source/diff and source-selected retained JSON bytes and PNG pixels to the same approved OpenAI provider.',
        'credentialReuse': 'Same approved provider credential and global historical/campaign budget; never retain credential.',
        'boundary': 'No new onboarding or implementation; no source edits, old grant upgrades, unknown-effect replay or missing-input repair.',
        'projectCommandsAuthorized': True, 'publicationAuthorized': False,
        'deploymentAuthorized': False, 'productAcceptanceAuthorized': False}
    if 'unknownDispositionPolicy' in c:
        intent['unknownDispositionPolicy'] = c['unknownDispositionPolicy']
    if 'evidenceEvaluationPolicy' in c:
        intent['evidenceEvaluationPolicy'] = c['evidenceEvaluationPolicy']
    if 'findingDispositionPolicy' in c:
        intent['findingDispositionPolicy'] = c['findingDispositionPolicy']
    if 'reviewMaxSourceBytes' in c:intent['reviewMaxSourceBytes'] = c['reviewMaxSourceBytes']
    if 'reviewEvidencePolicy' in c:
        intent['reviewEvidencePolicy'] = c['reviewEvidencePolicy']
        intent['disclosure'] += ' Unchanged context may use explicit credential-literal projection; substantive criteria preserve all original requirement bytes.'
    if 'campaignContinuation' in c:
        intent['campaignContinuation'] = c['campaignContinuation']
    if 'sourceReviewReuse' in c:
        intent.update(sourceReviewReuse=c['sourceReviewReuse'], maximumGenerationAttempts=1)
        intent['phases'][0] = 'verify-and-reuse-identical-completed-source-review'
    if 'sourceReadModePolicy' in c:intent['sourceReadModePolicy'] = c['sourceReadModePolicy']
    if 'producerKind' in c:
        intent.update(producerKind=c['producerKind'], repairConfig=c['repairConfig'],
                      instanceController=initial['instanceController'])
    return intent


def completion(initial, result, state, intent):
    return {**local.completion(initial, result, state, True), 'format': FORMAT,
        'producer': {key: intent[key] for key in ('producerController', 'workflowConfig', 'workflowState',
                     'workflowGrant', 'candidateReceipt')},
        'controllerManifestSha256': intent['controllerManifestSha256'],
        **({'producerKind': intent['producerKind'], 'repairConfig': intent['repairConfig']}
           if 'producerKind' in intent else {}),
        **({'interruptedPredecessor': intent['interruptedPredecessor']}
           if 'interruptedPredecessor' in intent else {})}


def verify_children(state, phase, intent, initial):
    if unique_json(api.file_bytes(state / 'initial-result.json')) != initial:
        raise Refused('Original producer result changed')
    if unique_json(api.file_bytes(state / 'validation-config.json')) != phase:
        raise Refused('Generated resumed validation config changed')
    if 'completedValidationReuse' in intent:
        import flow_execution_review as executed
        _, verified_initial, result = executed.verified_completed_validation(intent['completedValidationReuse'])
        if verified_initial != initial: raise Refused('Reused producer changed')
    else:
        cp = work.pin(state / 'validation-config.json')
        expected = validation.inputs(Path(cp['path']), cp['sha256'], state / 'validation')[-1]
        if unique_json(api.file_bytes(state / 'validation-dispatch.json')) != expected:
            raise Refused('Consumed resumed validation intent changed')
        result = validation.verify(Path(cp['path']), cp['sha256'], state / 'validation')
    value = completion(initial, result, state, intent)
    if result['status'] == 'local-validation-passed':
        value = local.reviewed_completion(value, local.verified_execution_review(intent, state), state)
    return value


def verify_partial(config_path, config_sha, state):
    """Reconstruct only a completed source-blocked phase, without advancing it."""
    state = api.canonical_path(state)
    c, wc, initial, phase, intent = prepare(config_path, config_sha, state)
    if 'interruptedPredecessor' in c:
        raise Refused('Partial predecessor recovery verification is unsupported')
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    with os.fdopen(owned_file(state, 'run.lock', os.O_RDONLY), 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if unique_json(api.file_bytes(state / 'grant.json')) != grant:
            raise Refused('Partial resumed grant changed')
        if (unique_json(api.file_bytes(state / 'initial-result.json')) != initial
                or unique_json(api.file_bytes(state / 'validation-config.json')) != phase):
            raise Refused('Partial resumed inputs changed')
        cp = work.pin(state / 'validation-config.json')
        expected = validation.inputs(Path(cp['path']), cp['sha256'], state / 'validation')[-1]
        if unique_json(api.file_bytes(state / 'validation-dispatch.json')) != expected:
            raise Refused('Partial resumed dispatch changed')
        reviewed = validation.source_review_state(phase, state / 'validation')
        status = api.status(reviewed)
        if (status.get('reservation') or {}).get('status') != 'completed':
            raise Refused('Partial verification requires completed paid source evidence')
        result = validation.run(Path(cp['path']), cp['sha256'], state / 'validation', _verify_only=True)
        if result['status'] not in ('source-defects', 'artifact-selection-required',
                'source-input-output-policy-required', 'execution-plan-refinement-required'):
            raise Refused('Partial verification requires a completed source-blocked phase')
        return report(result['status'], phase='validation', childResult=result)


def _run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None, verify_only=False, predecessor_recheck=lambda: None):
    if verify_only and (authorization is not None or key_file is not None or transport is not None):
        raise Refused('Verification takes no authorization, credential or transport')
    state = api.canonical_path(state)
    c, wc, initial, phase, intent = prepare(config_path, config_sha, state)
    baseline = None
    if 'interruptedPredecessor' in c:
        baseline, evidence = reconcile_predecessor(c, wc, initial, phase, state)
        intent['interruptedPredecessor'] = evidence
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    fresh = not state.exists()
    if fresh:
        if verify_only:
            raise Refused('Verification requires a completed resumed run')
        if authorization is None:
            if key_file is not None or transport is not None:
                raise Refused('Ungranted preview takes no credential or transport')
            if baseline is not None:
                recovery_claim(c, wc, state, grant, create=None)
            return report('awaiting-authorization', **grant)
        work.require_authorization(authorization, intent)
        predecessor_recheck()
        # The claim consumes this exact recovery even if later creation is interrupted.
        if baseline is not None:
            recovery_claim(c, wc, state, grant, create=True)
        with new_owned_directory(state):
            api.fsync_directory(state.parent)
        api.durable(state, 'grant.json', api.encoded(grant))
        if baseline is not None:
            api.durable(state, 'predecessor-budget.json', baseline)
        api.durable(state, 'run.lock', b'')
        api.durable(state, 'initial-result.json', api.encoded(initial))
        api.durable(state, 'validation-config.json', api.encoded(phase))
    if unique_json(api.file_bytes(state / 'grant.json')) != grant:
        raise Refused('Resumed grant drift; never upgrade another grant')
    if authorization is not None:
        work.require_authorization(authorization, intent)
    if baseline is not None:
        recovery_claim(c, wc, state, grant, create=False)
    with os.fdopen(owned_file(state, 'run.lock', os.O_RDWR), 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # These are required even before dispatch; interrupted creation never repairs them.
        if unique_json(api.file_bytes(state / 'initial-result.json')) != initial or unique_json(api.file_bytes(state / 'validation-config.json')) != phase:
            raise Refused('Consumed resumed inputs changed')
        if verify_only or (state / 'terminal-intent.json').exists() or (state / 'result.json').exists():
            if unique_json(api.file_bytes(state / 'terminal-intent.json')) != grant:
                raise Refused('Terminal resumed grant changed')
            value = verify_children(state, phase, intent, initial)
            if unique_json(api.file_bytes(state / 'result.json')) != value:
                raise Refused('Terminal resumed result changed')
            return value
        if 'completedValidationReuse' in intent:
            import flow_execution_review as executed
            _, verified_initial, result = executed.verified_completed_validation(intent['completedValidationReuse'])
            if verified_initial != initial: raise Refused('Reused producer changed')
        else:
            cp = work.pin(state / 'validation-config.json')
            attempt = state / 'validation'; marker = state / 'validation-dispatch.json'
            expected = validation.inputs(Path(cp['path']), cp['sha256'], attempt)[-1]
            if marker.exists():
                if unique_json(api.file_bytes(marker)) != expected:
                    raise Refused('Consumed resumed validation intent changed')
                if not attempt.exists():
                    return report('blocked', reason='Validation outcome unknown; reconcile without replay', replayPermitted=False)
            else:
                if attempt.exists():
                    raise Refused('Unrecorded validation attempt; never adopt it')
                if key_file is None and transport is None:
                    return report('awaiting-provider-credential', phase='validation')
                api.durable(state, marker.name, api.encoded(expected))
            if (attempt / 'workspace-output-evidence.json').exists():
                result = validation.verify(Path(cp['path']), cp['sha256'], attempt)
            else:
                predecessor_recheck()
                result = validation.run(Path(cp['path']), cp['sha256'], attempt,
                    api.digest(api.encoded(expected)), key_file=key_file, transport=transport)
            if not result['status'].startswith('local-validation-'):
                return report(result['status'], phase='validation', childResult=result)
            result = validation.verify(Path(cp['path']), cp['sha256'], attempt)
        value = completion(initial, result, state, intent)
        if result['status'] == 'local-validation-passed':
            predecessor_recheck()
            evidence, stop = local.review_execution(intent, state, key_file, transport)
            if stop is not None:
                return {**stop, 'format': FORMAT}
            value = local.reviewed_completion(value, evidence, state)
        predecessor_recheck()
        if baseline is not None:
            recovery_claim(c, wc, state, grant, create=False)
        work.retain(state, 'terminal-intent.json', grant)
        work.retain(state, 'result.json', value)
        return value


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None, verify_only=False):
    c = unique_json(api.pinned({'path': str(api.canonical_path(config_path)), 'sha256': config_sha}))
    # A predecessor cannot resume or admit another successor while we operate.
    with predecessor_lock(c) as recheck:
        return _run(config_path, config_sha, state, authorization, key_file=key_file,
                    transport=transport, verify_only=verify_only, predecessor_recheck=recheck)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--authorize-intent')
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    result = run(args.config, args.config_sha256, args.state, args.authorize_intent,
                 key_file=args.key_file, verify_only=args.verify)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('awaiting-authorization', 'local-feature-evidence-reviewed') else 1
