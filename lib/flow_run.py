"""One explicit outer grant: requirement to reviewed candidate and contained tests.

Existing work-v1/validation grants remain unchanged. All phase configuration and
exact child intents are generated internally; no publication is authorized.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

import flow_api_delivery as api
import flow_workflow as work
import flow_workflow_validation as validation
from aim_ci import control_package
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file
from aim_ci.project_discovery import unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-requirement-local-run'


def report(status, **extra):
    return {'format': FORMAT, 'status': status, 'ready': False, 'productAccepted': False,
        'publicationAuthorized': False, 'deploymentAuthorized': False, **extra}


def prepare(config_path, config_sha, state, python=None, review_tokens=None, review_limit=None,
            review_executed=False, execution_tokens=None, execution_limit=None, finding_policy=None):
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    validation.review.base.finding_policy(finding_policy)
    if type(review_executed) is not bool or (not review_executed and (execution_tokens is not None or execution_limit is not None)):
        raise Refused('Execution review limits require an explicit new review-executed grant')
    c, packet, context, requirement, resources, identity = work.inputs(config_path, config_sha, state / 'initial')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    for path in (ROOT, Path(c['source']['checkout']), Path(c['campaignDirectory']),
                 Path(c['historicalBudget']['directory']), Path(c['historicalBudget']['legacy']['path']).parent):
        if path == state or path in state.parents or state in path.parents:
            raise Refused('Outer state must be disjoint from source/controller/budget roots')
    if config_path.is_relative_to(state):
        raise Refused('Initial configuration must be outside outer state')
    python = api.canonical_path(python or Path(sys.executable).resolve())
    if not os.access(python, os.X_OK):
        raise Refused('Verifier Python must be executable')
    python_pin = work.pin(python)
    if python.is_relative_to(state):
        raise Refused('Verifier must be outside outer state')
    tokens = c['onboardingMaxOutputTokens'] if review_tokens is None else review_tokens
    limit = c['onboardingMaxOperationNanoUsd'] if review_limit is None else review_limit
    api.token_bounds(0, tokens)
    if type(limit) is not int or not 1 <= limit <= api.LIMIT:
        raise Refused('Invalid independent review operation ceiling')
    initial = work.grant_intent(c, packet, context, identity)
    intent = {'format': FORMAT, 'schemaVersion': 1, 'stage': 'grant-requirement-to-local-tests',
        'config': {'path': str(config_path), 'sha256': config_sha}, 'state': str(state),
        'controllerManifestSha256': c['manifestSha256'], 'initialIntent': initial,
        'provider': 'OpenAI Responses API', 'model': api.MODEL, 'reasoning': 'xhigh',
        'requirement': c['requirement'], 'resourceCatalog': c['resourceCatalog'],
        'python': python_pin, 'reviewMaxOutputTokens': tokens, 'reviewMaxOperationNanoUsd': limit,
        'phases': ['onboarding', 'implementation', 'private-git-candidate',
                   'independent-source-review-and-workspace-policy', 'container-runtime-qualification', 'typed-local-tests'],
        'maximumGenerationAttempts': 3, 'maximumContainerAttempts': 1,
        'reviewDisclosureScope': 'Original requirement and assessed plan, exact committed candidate diff and reviewSources plus definition sources derived from assessed package roots; no unrelated filesystem or implementer self-evaluation.',
        'candidateScope': 'One private descendant of the exact original commit; only granted editablePaths change.',
        'localExecutionScope': 'One composed serial DAG of supported assessed npm stages and lifecycle hooks in the selected pinned container; network:none, bounded private workspace and resource-template limits; no host mounts or project execution on host.',
        'workspacePolicyScope': 'Source-cited unchanged generated archives may be excluded from immutable staged inputs only under the typed policy; final regular output hashes/sizes required.',
        'resourceConfiguration': 'Catalog/runtime/application settings are supplied configuration, not independently discovered or provisioned by Flow.',
        'campaignDirectory': c['campaignDirectory'], 'historicalBudget': c['historicalBudget'],
        'combinedHistoricalAndCampaignLimitNanoUsd': api.LIMIT,
        'credentialReuse': 'Same approved provider credential within these granted model phases; never retained.',
        'projectCommandsAuthorized': True,
        'publicationAuthorized': False, 'deploymentAuthorized': False, 'productAcceptanceAuthorized': False}
    phase_config = {'schemaVersion': 1, 'manifestSha256': c['manifestSha256'],
        'workflowConfig': intent['config'], 'workflowState': str(state / 'initial'), 'python': python_pin,
        'reviewMaxOutputTokens': tokens, 'reviewMaxOperationNanoUsd': limit}
    if finding_policy is not None:
        intent['findingDispositionPolicy'] = finding_policy
        phase_config['findingDispositionPolicy'] = finding_policy
    if review_executed:
        output_tokens = tokens if execution_tokens is None else execution_tokens
        operation_limit = limit if execution_limit is None else execution_limit
        api.token_bounds(0, output_tokens)
        if type(operation_limit) is not int or not 1 <= operation_limit <= api.LIMIT:
            raise Refused('Invalid executed review operation ceiling')
        phase_config['captureArtifacts'] = True
        intent.update(schemaVersion=2, maximumGenerationAttempts=4,
            phases=[*intent['phases'], 'independent-retained-execution-evidence-review'],
            reviewExecuted={'maxOutputTokens': output_tokens, 'maxOperationNanoUsd': operation_limit,
                'maximumGenerationAttempts': 1,
                'artifactBounds': dict(validation.review.CAPTURE_LIMITS),
                'disclosure': 'Original requirement, candidate source/diff, reviewed source evidence, measured runtime/stages and exact retained source-selected PNG pixels and JSON bytes to the same approved OpenAI provider. No operator-authored artifact filenames.',
                'artifactSelection': 'Fresh source-cited candidate workspace policy chooses bounded outputs; no filenames copied from infrastructure templates.',
                'credentialReuse': 'Same approved provider key within this fourth model phase; never retained.',
                'qualification': 'Model evidence assessment, never human review, delivery readiness or publication authority.'})
    backends = {r['executionSpec']['backend'] for r in resources.get('resources', [])
        if r['kind'] == 'node-runtime' and 'executionSpec' in r}
    if backends == {'local'}:
        intent.update(maximumContainerAttempts=0, maximumHostAttempts=1,
            localExecutionScope='Existing project npm stages in a private committed-source copy, pinned host tools and explicit environment, normal host/network access; NOT a security sandbox; no publication or acceptance')
        intent['phases'] = ['host-runtime-qualification' if p == 'container-runtime-qualification' else p for p in intent['phases']]
    return intent, phase_config


def completion(initial, result, state, review_executed=False):
    value = report(result['status'], candidate=initial['candidate'],
        candidateReceipt=initial['candidateReceipt'], validation=result,
        projectCommandsExecuted=result.get('projectCommandsExecuted', False),
        nextStage='independent-product-acceptance-and-separately-authorized-publication-required',
        evidence={'initialResult': work.pin(state / 'initial-result.json'),
                  'validationConfig': work.pin(state / 'validation-config.json')})
    if review_executed:
        value.update(localFeatureAccepted=False, deliveryReady=False, executionReviewState='not-run')
    return value


def verify_children(config_path, config_sha, state, phase_config, intent):
    for name in ('initial-dispatch.json', 'initial-result.json', 'validation-config.json', 'validation-dispatch.json'):
        api.file_bytes(state / name)
    if unique_json(api.file_bytes(state / 'initial-dispatch.json')) != intent['initialIntent']:
        raise Refused('Consumed initial intent changed')
    initial = work.verify(config_path, config_sha, state / 'initial')
    if unique_json(api.file_bytes(state / 'initial-result.json')) != initial:
        raise Refused('Completed initial workflow result changed')
    if unique_json(api.file_bytes(state / 'validation-config.json')) != phase_config:
        raise Refused('Generated validation configuration changed')
    cp = work.pin(state / 'validation-config.json')
    phase_intent = validation.inputs(Path(cp['path']), cp['sha256'], state / 'validation')[-1]
    if unique_json(api.file_bytes(state / 'validation-dispatch.json')) != phase_intent:
        raise Refused('Consumed validation intent changed')
    result = validation.verify(Path(cp['path']), cp['sha256'], state / 'validation')
    return initial, result


def execution_config(intent, state):
    limits = intent['reviewExecuted']
    reuse = intent.get('completedValidationReuse')
    return {'schemaVersion': 1, 'manifestSha256': intent['controllerManifestSha256'],
        'validationConfig': reuse['validationConfig'] if reuse else work.pin(state / 'validation-config.json'),
        'validationState': reuse['validationState'] if reuse else str(state / 'validation'),
        **({'completedValidationReuse': reuse} if reuse else {}),
        **({'unknownDispositionPolicy': intent['unknownDispositionPolicy']} if 'unknownDispositionPolicy' in intent else {}),
        **({'evidenceEvaluationPolicy': intent['evidenceEvaluationPolicy']} if 'evidenceEvaluationPolicy' in intent else {}),
        'maxOutputTokens': limits['maxOutputTokens'], 'maxOperationNanoUsd': limits['maxOperationNanoUsd'],
        **({'campaignContinuation': intent['campaignContinuation']} if 'campaignContinuation' in intent else {})}


def verified_execution_review(intent, state):
    """Terminal verification only; never retain, dispatch, or reconstruct missing files."""
    import flow_execution_review as executed
    for name in ('execution-review-config.json', 'execution-review-dispatch.json'):
        api.file_bytes(state / name)
    config = execution_config(intent, state)
    if unique_json(api.file_bytes(state / 'execution-review-config.json')) != config:
        raise Refused('Generated executed-review configuration changed')
    cp = work.pin(state / 'execution-review-config.json')
    attempt = state / 'execution-review'
    expected = executed.prepare(Path(cp['path']), cp['sha256'], attempt)[-1]
    if unique_json(api.file_bytes(state / 'execution-review-dispatch.json')) != expected:
        raise Refused('Consumed executed-review intent changed')
    return executed.verify(Path(cp['path']), cp['sha256'], attempt)


def review_execution(intent, state, key_file, transport):
    import flow_execution_review as executed
    attempt = state / 'execution-review'
    marker = state / 'execution-review-dispatch.json'
    if marker.exists() or attempt.exists():
        api.file_bytes(state / 'execution-review-config.json')
    config = execution_config(intent, state)
    cp = work.retain(state, 'execution-review-config.json', config)
    expected = executed.prepare(Path(cp['path']), cp['sha256'], attempt)[-1]
    if marker.exists():
        if unique_json(api.file_bytes(marker)) != expected:
            raise Refused('Consumed executed-review intent changed')
        if not attempt.exists():
            return None, report('blocked', phase='executed-review',
                reason='Executed-review outcome unknown; reconcile without replay', replayPermitted=False)
    else:
        if attempt.exists():
            raise Refused('Unrecorded executed-review attempt; never adopt it')
        if key_file is None and transport is None:
            return None, report('awaiting-provider-credential', phase='executed-review')
        work.retain(state, marker.name, expected)
        executed.run(Path(cp['path']), cp['sha256'], attempt, api.digest(api.encoded(expected)),
                     key_file=key_file, transport=transport)
    status = api.status(attempt)
    if status['status'] not in ('source-review-ready', 'review-blocked'):
        return None, report('blocked', phase='executed-review', reason='Consumed executed review is incomplete or rejected; no retry',
                            attemptResult=status, replayPermitted=False)
    return verified_execution_review(intent, state), None


def reviewed_completion(value, evidence, state):
    return {**value, 'status': ('local-feature-evidence-reviewed' if evidence['localFeatureAccepted']
                              else 'executed-evidence-review-blocked'),
        'localFeatureAccepted': evidence['localFeatureAccepted'],
        'productAccepted': evidence['productAccepted'], 'deliveryReady': False,
        'executionReview': evidence, 'executionReviewState': 'completed',
        'pendingDeliveryObligations': evidence['pendingDeliveryObligations'],
        'reviewQualification': 'Fresh model evidence assessment, not human review or publication/deployment authority.',
        'nextStage': ('separately-authorized-delivery-obligations-required' if evidence['localFeatureAccepted']
                      else 'resolve-executed-review-findings-before-delivery'),
        'evidence': {**value['evidence'], 'executionReviewConfig': work.pin(state / 'execution-review-config.json'),
                     'executionReviewResult': work.pin(state / 'execution-review' / 'result.json')}}


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None,
        python=None, review_tokens=None, review_limit=None, verify_only=False,
        review_executed=False, execution_tokens=None, execution_limit=None, finding_policy=None):
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    intent, phase_config = prepare(config_path, config_sha, state, python, review_tokens, review_limit,
                                   review_executed, execution_tokens, execution_limit, finding_policy)
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    new_state = not state.exists()
    if new_state:
        if verify_only:
            raise Refused('Verification requires completed outer workflow')
        if authorization is None:
            if key_file is not None:
                raise Refused('Ungranted preview never reads credentials')
            return report('awaiting-authorization', **grant)
        work.require_authorization(authorization, intent)
        with new_owned_directory(state):
            pass
        api.durable(state, 'grant.json', api.encoded(grant))
    if unique_json(api.file_bytes(state / 'grant.json')) != grant:
        raise Refused('Outer grant/configuration/source drift; never upgrade an old grant')
    if authorization is not None:
        work.require_authorization(authorization, intent)
    with os.fdopen(owned_file(state, 'run.lock', os.O_RDWR | (0 if verify_only or not new_state else os.O_CREAT)), 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (state / 'terminal-intent.json').exists() or (state / 'result.json').exists() or verify_only:
            for name in ('terminal-intent.json', 'result.json'):
                api.file_bytes(state / name)
            if unique_json(api.file_bytes(state / 'terminal-intent.json')) != grant:
                raise Refused('Terminal outer grant changed')
            initial, result = verify_children(config_path, config_sha, state, phase_config, intent)
            expected = completion(initial, result, state, review_executed)
            if review_executed and result['status'] == 'local-validation-passed':
                expected = reviewed_completion(expected, verified_execution_review(intent, state), state)
            if unique_json(api.file_bytes(state / 'result.json')) != expected:
                raise Refused('Terminal outer result changed')
            return expected
        initial_state = state / 'initial'
        marker = state / 'initial-dispatch.json'
        if marker.exists():
            if unique_json(api.file_bytes(marker)) != intent['initialIntent']:
                raise Refused('Consumed initial workflow intent changed')
            if not initial_state.exists():
                return report('blocked', reason='Initial child outcome unknown; reconcile without replay', replayPermitted=False)
        else:
            if initial_state.exists():
                raise Refused('Unrecorded initial child; never adopt it')
            work.retain(state, marker.name, intent['initialIntent'])
        if (initial_state / 'candidate-receipt.json').exists():
            initial = work.verify(config_path, config_sha, initial_state)
        else:
            initial = work.run(config_path, config_sha, initial_state,
                api.digest(api.encoded(intent['initialIntent'])), key_file=key_file, transport=transport)
        if initial['status'] != 'candidate-prepared':
            return report(initial['status'], phase='initial', childResult=initial)
        initial = work.verify(config_path, config_sha, initial_state)
        if (state / 'validation-dispatch.json').exists() or (state / 'validation').exists():
            for name in ('initial-result.json', 'validation-config.json', 'validation-dispatch.json'):
                api.file_bytes(state / name)
        work.retain(state, 'initial-result.json', initial)
        cp = work.retain(state, 'validation-config.json', phase_config)
        phase_state = state / 'validation'
        phase_intent = validation.inputs(Path(cp['path']), cp['sha256'], phase_state)[-1]
        marker = state / 'validation-dispatch.json'
        if marker.exists():
            if unique_json(api.file_bytes(marker)) != phase_intent:
                raise Refused('Consumed validation intent changed')
            if not phase_state.exists():
                return report('blocked', reason='Validation child outcome unknown; reconcile without replay', replayPermitted=False)
        else:
            if phase_state.exists():
                raise Refused('Unrecorded validation child; never adopt it')
            work.retain(state, marker.name, phase_intent)
        if (phase_state / 'workspace-output-evidence.json').exists():
            result = validation.verify(Path(cp['path']), cp['sha256'], phase_state)
        else:
            result = validation.run(Path(cp['path']), cp['sha256'], phase_state,
                api.digest(api.encoded(phase_intent)), key_file=key_file, transport=transport)
        if not result['status'].startswith('local-validation-'):
            return report(result['status'], phase='validation', childResult=result)
        result = validation.verify(Path(cp['path']), cp['sha256'], phase_state)
        value = completion(initial, result, state, review_executed)
        if review_executed and result['status'] == 'local-validation-passed':
            evidence, stop = review_execution(intent, state, key_file, transport)
            if stop is not None:
                return stop
            value = reviewed_completion(value, evidence, state)
        work.retain(state, 'terminal-intent.json', grant)
        work.retain(state, 'result.json', value)
        return value


def run_requirement(config_path, config_sha, state, allowed_directories, authorization=None, *,
                    key_file=None, transport=None, python=None, review_tokens=None, review_limit=None,
                    verify_only=False, review_executed=False, execution_tokens=None, execution_limit=None,
                    finding_policy=None):
    """Opt-in intake bridge. Preview retains local inputs, never dispatches effects.

    Existing run grants are nested unchanged. Every resume re-derives and compares
    retained inputs; absent files are fatal, including interrupted initial previews.
    """
    import flow_requirement_intake as intake
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    if verify_only and (authorization is not None or key_file is not None or transport is not None):
        raise Refused('Verification takes no credential, transport or authorization')
    base_pin = {'path': str(config_path), 'sha256': config_sha}
    base = unique_json(api.pinned(base_pin))
    for path in (ROOT, Path(base['source']['checkout']), Path(base['campaignDirectory']),
                 Path(base['historicalBudget']['directory']), Path(base['historicalBudget']['legacy']['path']).parent):
        if path == state or path in state.parents or state in path.parents:
            raise Refused('Intake state must be disjoint from source/controller/budget roots')
    for path in (config_path, Path(base['requirement']['path']), Path(base['resourceCatalog']['path'])):
        if path.is_relative_to(state):
            raise Refused('Intake inputs must be outside intake state')
    derived = intake.derive(ROOT, base['source'], base['requirement'], allowed_directories)
    generated = intake.assemble_config(base, derived)
    config = state / 'workflow-config.json'
    config_hash = api.digest(api.encoded(generated))
    artifacts = {'intake.json': derived, 'workflow-config.json': generated}
    fresh = not state.exists()
    if fresh:
        if verify_only or authorization is not None:
            raise Refused('Create a no-paid intake preview before authorization or verification')
        if key_file is not None or transport is not None:
            raise Refused('Ungranted intake preview takes no credential or transport')
        with new_owned_directory(state):
            pass
        for name, value in artifacts.items():
            api.durable(state, name, api.encoded(value))
        api.durable(state, 'intake.lock', b'')
    with os.fdopen(owned_file(state, 'intake.lock', os.O_RDWR), 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name, value in artifacts.items():
            if api.file_bytes(state / name) != api.encoded(value):
                raise Refused('Retained intake input changed: ' + name)
        options = dict(python=python, review_tokens=review_tokens, review_limit=review_limit,
                       review_executed=review_executed, execution_tokens=execution_tokens,
                       execution_limit=execution_limit, finding_policy=finding_policy)
        child_state = state / 'run'
        child_intent, _ = prepare(config, config_hash, child_state, **options)
        intent = {'format': 'aim-flow-intake-local-run', 'schemaVersion': 1,
            'baseConfig': base_pin, 'state': str(state),
            'intake': work.pin(state / 'intake.json'),
            'workflowConfig': work.pin(config), 'runIntent': child_intent,
            'previewScope': 'Local committed-source retrieval and retained inputs only; no provider, container, project commands or publication.',
            'intakeQualification': 'Deterministic heuristic retrieval, not semantic completeness or product acceptance.',
            'publicationAuthorized': False, 'deploymentAuthorized': False}
        grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
        if fresh:
            api.durable(state, 'intake-grant.json', api.encoded(grant))
        if api.file_bytes(state / 'intake-grant.json') != api.encoded(grant):
            raise Refused('Intake grant or inputs changed; never adopt new inputs')
        if authorization is not None:
            work.require_authorization(authorization, intent)
        marker = state / 'run-dispatch.json'
        if marker.exists():
            if api.file_bytes(marker) != api.encoded(grant):
                raise Refused('Consumed intake grant changed')
            if not child_state.exists():
                if verify_only:
                    raise Refused('Missing dispatched run; verification never repairs inputs')
                return report('blocked', reason='Run outcome unknown; reconcile without replay', replayPermitted=False)
        else:
            if child_state.exists():
                raise Refused('Unrecorded run state; never adopt it')
            if verify_only:
                raise Refused('Verification requires a dispatched completed run')
            if authorization is None:
                if key_file is not None or transport is not None:
                    raise Refused('Ungranted intake preview takes no credential or transport')
                return report('awaiting-authorization', **grant)
            api.durable(state, marker.name, api.encoded(grant))
        result = run(config, config_hash, child_state,
                   None if verify_only else api.digest(api.encoded(child_intent)),
                   key_file=key_file, transport=transport, verify_only=verify_only, **options)
        return {**result, 'intakeEvidence': {'grant': work.pin(state / 'intake-grant.json'),
            'intake': intent['intake'], 'workflowConfig': intent['workflowConfig'],
            'workflowState': str(child_state)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--allow-directory', action='append', help='Opt in to Flow-owned intake from this broad edit boundary; repeat as needed')
    parser.add_argument('--authorize-intent')
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--review-output-tokens', type=int)
    parser.add_argument('--review-max-operation-nano-usd', type=int)
    parser.add_argument('--review-executed', action='store_true')
    parser.add_argument('--finding-disposition-policy', choices=[validation.review.base.FINDING_DISPOSITION_POLICY])
    parser.add_argument('--execution-review-output-tokens', type=int)
    parser.add_argument('--execution-review-max-operation-nano-usd', type=int)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    if args.verify and (args.key_file or args.authorize_intent):
        parser.error('Verification takes no credential or authorization')
    runner = run if args.allow_directory is None else run_requirement
    intake_options = {} if args.allow_directory is None else {'allowed_directories': args.allow_directory}
    result = runner(args.config, args.config_sha256, args.state, authorization=args.authorize_intent,
        **intake_options,
        key_file=args.key_file, python=args.python, review_tokens=args.review_output_tokens,
        review_limit=args.review_max_operation_nano_usd, verify_only=args.verify,
        review_executed=args.review_executed, execution_tokens=args.execution_review_output_tokens,
        execution_limit=args.execution_review_max_operation_nano_usd,
        finding_policy=args.finding_disposition_policy)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('awaiting-authorization', 'local-validation-passed', 'local-feature-evidence-reviewed') else 1
