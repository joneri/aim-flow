"""New scoped grant: verified local Flow acceptance to a GitHub main-push handoff."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path

import flow_api_delivery as api
import flow_candidate_review as source_review
import flow_execution_review as acceptance
import flow_github_delivery as delivery
import flow_github_publication as publication
import flow_run as local_run
import flow_workflow as workflow
from aim_ci import control_package
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-github-delivery-chain'
BODY_SCOPE = 'requirement-local-evidence-and-pending-delivery-v1'
BOUNDARY = ('This is a new GitHub phase grant, never inherited from local tests or another PR. '
            'It covers only this exact verified candidate, new branch/draft text, ready-for-review '
            'transition, guarded merge, and exact main-push CI/artifact observation. '
            'No model/container/project command, existing PR edit, force overwrite, CI rerun, '
            'download, release or deployment. Unknown effects are reconciled read-only, never repeated. '
            'GitHub has no atomic expected-base merge guard; unexpected parents/main stop handoff. '
            'Local trusted receipts and model review are not independent public authentication.')


def require(ok, message):
    delivery.require(ok, message)


def pinned(path):
    return workflow.pin(path)


def parsed(path):
    return delivery.unique_json(api.file_bytes(path))


def verified_external_local_run(spec, config, intent, expected, *, allow_owner_scope=False):
    from aim_ci import project_validation
    delivery.keys(spec['controller'], ('path', 'manifestSha256'), 'local controller')
    root = api.canonical_path(spec['controller']['path'])
    before = project_validation.prior_controller(root, spec['controller']['manifestSha256'])
    retained = {key: api.pinned(spec[key]) for key in ('config', 'grant', 'result')}
    require(delivery.unique_json(retained['config']) == config
            and delivery.unique_json(retained['result']) == expected,
            'External local run changed before verification')
    python = intent['python']; publication.executable_pin(python)
    command = [python['path'], '-IBS', str(root / 'aim-flow.py'), 'resume-candidate',
        '--config', spec['config']['path'], '--config-sha256', spec['config']['sha256'],
        '--state', spec['state'], '--verify']
    process = project_validation.verification_process(command, ROOT,
        {'PATH': os.defpath, 'HOME': str(ROOT), 'LC_ALL': 'C'},
        metadata_limit=max(64000, len(retained['result']) * 2 + 4096),
        timeout_seconds=project_validation.producer_timeout(config))
    expected_exit = (1 if allow_owner_scope and expected.get('status') == 'executed-evidence-review-blocked'
                     and expected.get('localFeatureAccepted') is False else 0)
    require(process.returncode == expected_exit and not process.stderr,
            'Pinned local controller refused completed acceptance')
    result = delivery.unique_json(process.stdout)
    require(result == expected and api.encoded(result) == retained['result'],
            'Pinned local controller did not reproduce exact retained result')
    require(before == project_validation.prior_controller(root, spec['controller']['manifestSha256'])
            and all(api.pinned(spec[key]) == value for key, value in retained.items()),
            'External local controller or evidence changed during verification')
    publication.executable_pin(python)
    return result


def verify_local(spec, manifest_sha, *, owner_decision=None):
    """Use actual installed verifiers; caller JSON booleans never substitute for the chain."""
    delivery.keys(spec, ('config', 'state', 'grant', 'result',
        *(('controller',) if 'controller' in spec else ()),
        *(('ownerAcceptance',) if 'ownerAcceptance' in spec else ())), 'completed local run')
    owner_receipt = None
    if 'ownerAcceptance' in spec:
        require(owner_decision is None, 'Nested owner decision is not supported')
        import flow_owner_acceptance as owner
        local_spec = {k: v for k, v in spec.items() if k != 'ownerAcceptance'}
        owner_receipt = owner.verify(spec['ownerAcceptance'], local_spec, manifest_sha)
        owner_decision = owner_receipt['decision']
    state = api.canonical_path(spec['state'])
    require(spec['grant']['path'] == str(state / 'grant.json') and spec['result']['path'] == str(state / 'result.json'),
            'Local grant/result must be exact retained outer files')
    grant = delivery.unique_json(api.pinned(spec['grant']))
    old_result = delivery.unique_json(api.pinned(spec['result']))
    config = delivery.unique_json(api.pinned(spec['config']))
    local_manifest = spec.get('controller', {}).get('manifestSha256', manifest_sha)
    require(config.get('manifestSha256') == local_manifest, 'Completed local run differs from pinned controller')
    intent = grant.get('intent', {})
    resumed = intent.get('format') == 'aim-flow-resumed-candidate-local-run'
    require(intent.get('config') == spec['config'] and intent.get('state') == str(state)
            and intent.get('controllerManifestSha256') == local_manifest, 'Local run grant identity differs')
    publication.executable_pin(intent['python'])
    if resumed:
        import flow_resume_candidate as resume
        require(intent.get('schemaVersion') == 1 and intent.get('maximumGenerationAttempts') == (1 if 'sourceReviewReuse' in intent or 'completedValidationReuse' in intent else 2)
                and (intent.get('maximumContainerAttempts') == 1 or
                     intent.get('maximumContainerAttempts') == 0 and (intent.get('maximumHostAttempts') == 1 or
                     'completedValidationReuse' in intent and intent.get('maximumHostAttempts') == 0
                     and intent.get('projectCommandsAuthorized') is False))
                and isinstance(intent.get('reviewExecuted'), dict),
                'A new bounded resumed candidate grant is required')
        result = (verified_external_local_run(spec, config, intent, old_result, allow_owner_scope=owner_decision is not None) if 'controller' in spec else
            resume.run(Path(spec['config']['path']), spec['config']['sha256'], state, verify_only=True))
    else:
        require('controller' not in spec, 'External local controller requires a resumed-candidate receipt')
        require(intent.get('format') == 'aim-flow-requirement-local-run' and intent.get('schemaVersion') == 2
                and intent.get('maximumGenerationAttempts') == 4 and isinstance(intent.get('reviewExecuted'), dict),
                'A completed four-phase local run grant is required; an old grant cannot acquire acceptance')
        caps = intent['reviewExecuted']
        try:
            result = local_run.run(Path(spec['config']['path']), spec['config']['sha256'], state,
                python=Path(intent['python']['path']), review_tokens=intent['reviewMaxOutputTokens'],
                review_limit=intent['reviewMaxOperationNanoUsd'], verify_only=True, review_executed=True,
                execution_tokens=caps['maxOutputTokens'], execution_limit=caps['maxOperationNanoUsd'],
                finding_policy=intent.get('findingDispositionPolicy'))
        except TypeError as error:
            raise Refused('Installed outer run lacks the required four-phase read-only verifier') from error
    owner_acceptance = None
    if owner_decision is not None:
        import flow_owner_acceptance as owner
        require(result.get('status') == 'executed-evidence-review-blocked'
                and result.get('localFeatureAccepted') is False, 'Owner decision requires the intact blocked result')
        owner_packet = parsed(state / 'execution-review/packet.json')
        owner_acceptance = owner.check(owner_decision, result['executionReview'], owner_packet)
        if owner_receipt is not None: owner_acceptance = owner_receipt
    require(result == old_result and result.get('format') == intent['format']
            and (owner_acceptance is not None or result.get('status') == 'local-feature-evidence-reviewed'
                 and result.get('localFeatureAccepted') is True),
            'Verified local feature acceptance is incomplete or blocked')
    producer_config = config
    if resumed:
        producer_config = delivery.unique_json(api.pinned(config['workflowConfig']))
    review = result.get('executionReview', {})
    require(review.get('format') == 'aim-flow-execution-review' and not source_review.blocking_findings(review)
            and (owner_acceptance is not None or review.get('localFeatureAccepted') is True
                 and review.get('blockers') == [] and review.get('unknowns') == []),
            'Unresolved local findings or unknowns forbid publication')
    require(isinstance(review.get('coverage'), list) and review['coverage']
            and all(v.get('status') in ('satisfied', 'pending-delivery') or owner_acceptance is not None
                    and v.get('status') == 'uncertain' and v['requirementId'] in {x['id'] for x in owner_decision['criteria']}
                    for v in review['coverage'])
            and isinstance(review.get('priorDispositions'), list)
            and all(v.get('status') in ('resolved', 'pending-delivery') or (
                owner_acceptance is not None and v.get('status') == 'unresolved'
                and v['itemId'] in {x['id'] for x in owner_decision['priorItems']}) or (
                v.get('status') == 'nonblocking'
                and review.get('unknownDispositionPolicy') == 'materiality-and-scope-v1'
                and any({k: item[k] for k in v} == v for item in review.get('nonblockingPriorItems', [])
                        if all(k in item for k in v)))
                for v in review['priorDispositions']),
            'Local criteria or prior obligations remain unresolved')
    require(isinstance(review.get('pendingDeliveryObligations'), list)
            and result.get('pendingDeliveryObligations') == review['pendingDeliveryObligations'],
            'Pending delivery obligations changed or disappeared')
    validation = result.get('validation', {})
    require(validation.get('status') == 'local-validation-passed'
            and validation.get('validation', {}).get('status') == 'passed'
            and validation.get('projectCommandsExecuted') is True
            and validation.get('workspaceOutputs', {}).get('complete') is True,
            'Passing source-bound execution and complete workspace evidence required')
    initial_pin = pinned(state / 'initial-result.json')
    require(result['evidence'].get('initialResult') == initial_pin, 'Outer initial-result binding differs')
    initial = delivery.unique_json(api.pinned(initial_pin))
    candidate, source = initial['candidate'], initial['source']
    require(result.get('candidate') == candidate and initial.get('status') == 'candidate-prepared'
            and result.get('candidateReceipt') == initial['candidateReceipt']
            and validation['workspaceOutputs'].get('candidate') == candidate
            and review.get('candidate') == {k: candidate[k] for k in ('commit', 'tree')},
            'Run, candidate, workspace and execution review source identities differ')
    execution_pin = pinned(state / 'execution-review' / 'review.json')
    require(delivery.unique_json(api.pinned(execution_pin)) == review,
            'Executed review file differs from reconstructed result')
    validation_state = (Path(intent['completedValidationReuse']['validationState'])
        if 'completedValidationReuse' in intent else state / 'validation')
    host = validation.get('executionBackend') == 'host'
    container_pin = validation.get('executionReceipt') if host else pinned(validation_state / 'container-attempt' / 'result.json')
    require(isinstance(container_pin, dict) and container_pin['path'] == str(validation_state /
            ('host-attempt' if host else 'container-attempt') / 'result.json'), 'Execution receipt backend/path differs')
    api.pinned(container_pin)
    require(validation['validation'].get('receiptSha256') == container_pin['sha256']
            and validation['workspaceOutputs'].get('executionReceiptSha256' if host else 'containerReceiptSha256') == container_pin['sha256'],
            'Execution/workspace receipt identities differ')
    packet_pin = pinned(state / 'execution-review' / 'packet.json')
    packet = delivery.unique_json(api.pinned(packet_pin))
    original = api.pinned(producer_config['requirement']).decode('utf-8')
    require(packet['requirement']['original'] == original and packet['candidate'] == review['candidate'],
            'Acceptance original requirement/source differs')
    # The outer verifier rebuilt this packet from actual execution. Pin it after that verification.
    validation_config = parsed(state / 'validation-config.json')
    source_state = acceptance.upstream.source_review_state(validation_config, validation_state)
    evidence = {'localRun': spec['result'], 'localGrant': spec['grant'], 'candidate': initial['candidateReceipt'],
                'aggregate': container_pin, 'workspace': pinned(validation_state / 'workspace-output-evidence.json'),
                'sourceReview': pinned(source_state / 'review.json'),
                'executedReview': execution_pin, 'executedPacket': packet_pin}
    if owner_receipt is not None:
        evidence['ownerAcceptance'] = owner_receipt['receipt']
        evidence['ownerAcceptanceGrant'] = owner_receipt['grant']
    for pin in evidence.values():
        api.pinned(pin)
    require(pinned(state / 'result.json') == spec['result'] and pinned(state / 'grant.json') == spec['grant'],
            'Local run changed during verification')
    return {'result': result, 'initial': initial, 'source': source, 'candidate': candidate,
            'review': review, 'validation': validation, 'packet': packet, 'config': producer_config,
            'runIntent': intent, 'evidence': evidence,
            **({'ownerAcceptance': owner_acceptance} if owner_acceptance is not None else {})}


def render_body(verified):
    review, packet, source = verified['review'], verified['packet'], verified['source']
    stages = verified['validation']['validation']['report']['stages']
    artifacts = verified['validation']['validation']['report'].get('artifacts', [])
    lines = ['## Requirement', '', packet['requirement']['original'], '', '## Exact candidate', '',
             'Baseline: ' + source['baseSha'], 'Candidate: ' + source['headSha'], 'Tree: ' + source['treeSha'],
             'Complete candidate diff SHA-256: ' + source['diffSha256'], '', '## Verified local evidence', '',
             ('Flow verified the retained source-bound normal host execution and its generated outputs; this was not a security sandbox.'
              if verified['validation'].get('executionBackend') == 'host' else
              'Flow verified the retained source-bound contained execution and its generated outputs.'),
             'Completed local stages: ' + str(len(stages)) + '. Retained executed artifacts: ' + str(len(artifacts)) + '.',
             ('A fresh model review accepted the local feature evidence, with no blocking local findings or unknowns.'
              if source_review.finding_policy(review.get('findingDispositionPolicy')) or review.get('nonblockingPriorItems') else
              'A fresh model review accepted the local feature evidence, with no unresolved local findings or unknowns.'),
             'This is model evidence assessment, not human review, independent public attestation or production verification.', '',
             'Criterion dispositions:']
    owner = verified.get('ownerAcceptance')
    if owner:
        # Replace only the generated PR claim, never the raw model evidence.
        lines = [line for line in lines if not line.startswith('A fresh model review accepted')]
        lines += ['', '## Explicit owner acceptance', '',
            'The model review remains blocked on the stated verification-method uncertainty. The owner accepted this exact local verification scope for this pilot.',
            'Owner: ' + owner['decision']['authority']['actor'],
            'Instruction: ' + owner['decision']['authority']['statement'],
            'Rationale: ' + owner['decision']['rationale'], 'Unverified limitations:']
        lines += ['- ' + value for value in owner['decision']['limitations']]
        lines += ['', 'Raw model criterion dispositions (retained unchanged):']
    criteria = {v['id']: v['text'] for v in packet['requirement']['criteria']}
    for row in review['coverage']:
        lines += ['- ' + row['requirementId'] + ' [' + row['status'] + ']: ' + criteria[row['requirementId']]]
    lines += ['', 'Receipt SHA-256 references (private evidence is not attached):']
    for role in ('candidate', 'aggregate', 'workspace', 'sourceReview', 'executedReview'):
        lines += ['- ' + role + ': ' + verified['evidence'][role]['sha256']]
    if source_review.finding_policy(review.get('findingDispositionPolicy')):
        follow_ups = review['followUps']
        lines += ['', '## Non-blocking follow-ups', '',
            'Full descriptions, impact rationales and source/evidence citations are retained in the referenced review receipts.']
        for row in follow_ups:
            finding = row['finding']
            lines += ['- ' + row['origin'] + ' ' + finding['id'] + ' [' + finding['severity'] + ']: '
                      + finding['disposition']['title']]
        if not follow_ups:
            lines += ['None reported.']
    if review.get('nonblockingPriorItems'):
        lines += ['', 'Non-blocking review uncertainties remain explicitly retained with scope/materiality rationales in the evidence receipt; they were not declared resolved.']
    lines += ['', '## Pending delivery', '',
              'Hosted PR checks, ready-for-review/merge, exact main-push CI and artifact checks, project release and production verification remain pending.']
    for row in review['pendingDeliveryObligations']:
        lines += ['- ' + row.get('itemId', row.get('requirementId', 'delivery-obligation')) + ': ' + row['analysis']]
    lines += ['', 'This draft does not claim deployment, full product acceptance or completion of the end-to-end pilot.', '']
    body = '\n'.join(lines)
    acceptance.safe_text(body)
    require(len(body.encode()) <= 65536, 'Generated draft body exceeds the platform bound; no truncation')
    return body


def local_proof(verified):
    # Compact references keep captured content out of GitHub metadata attachments.
    # This document is derived only after the complete upstream verifier succeeds.
    return {'source': verified['source'], 'evidence': verified['evidence'],
            'pendingDeliveryObligations': verified['review']['pendingDeliveryObligations'],
            **({'ownerAcceptance': verified['ownerAcceptance']} if 'ownerAcceptance' in verified else {}),
            **({'followUps': verified['review']['followUps']} if
                source_review.finding_policy(verified['review'].get('findingDispositionPolicy')) else {})}


def publication_config(c, verified, state, body):
    wc, intent = verified['config'], verified['runIntent']
    resumed = intent['format'] == 'aim-flow-resumed-candidate-local-run'
    producer = ({'controller': intent['producerController'], 'config': intent['workflowConfig'],
                 'state': intent['workflowState'],
                 'producerVerificationTimeoutSeconds': intent['producerVerificationTimeoutSeconds']} if resumed else
                {'controller': {'path': str(ROOT), 'manifestSha256': c['manifestSha256']},
                 'config': c['localRun']['config'], 'state': str(Path(c['localRun']['state']) / 'initial')})
    if 'producerKind' in intent or 'repairConfig' in intent:
        require(resumed and intent.get('producerKind') == 'repair-candidate' and 'repairConfig' in intent,
                'Unsupported publication producer continuation')
        producer.update(producerKind=intent['producerKind'], repairConfig=intent['repairConfig'])
    return {'format': 'aim-flow-github-publication', 'schemaVersion': 1, 'manifestSha256': c['manifestSha256'],
        'repository': c['repository'], 'source': {**verified['source'], 'git': {'path': wc['source']['git'], 'sha256': wc['source']['gitSha256']}},
        'branch': c['branch'], 'baseRef': c['baseRef'], 'pull': {'title': c['title'],
            'body': {'path': str(state / 'draft-body.md'), 'sha256': api.digest(body.encode())}},
        'evidence': {'candidate': verified['evidence']['candidate'],
                     'aggregate': {'path': str(state / 'local-proof.json'), 'sha256': api.digest(api.encoded(local_proof(verified)))},
                     'browser': verified['evidence']['executedReview'], 'independentReview': verified['evidence']['sourceReview']},
        'candidateVerification': {**producer, 'python': intent['python']},
        'transport': c['transport']}


def merge_config(c, pub_config, pull):
    return {'format': 'aim-flow-github-delivery', 'schemaVersion': 1, 'manifestSha256': c['manifestSha256'],
        'repository': c['repository'], 'pull': pull, 'workflow': c['workflow'],
        'requiredPrChecks': c['requiredPrChecks'], 'requiredPrStatuses': c['requiredPrStatuses'],
        'evidence': {k: v for k, v in pub_config['evidence'].items() if k != 'candidate'}, 'transport': c['transport']}


def prepare(config_path, config_sha, state):
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    c = delivery.unique_json(delivery.pinned_metadata({'path': str(config_path), 'sha256': config_sha}))
    delivery.keys(c, ('format', 'schemaVersion', 'manifestSha256', 'localRun', 'repository', 'baseSha', 'baseRef',
                     'branch', 'title', 'bodyScope', 'requiredPrChecks', 'requiredPrStatuses', 'workflow', 'transport'), 'delivery chain')
    require(c['format'] == FORMAT and type(c['schemaVersion']) is int and c['schemaVersion'] == 1
            and c['bodyScope'] == BODY_SCOPE, 'Unsupported delivery-chain scope')
    control_package.verify_installation(ROOT, c['manifestSha256'])
    verified = verify_local(c['localRun'], c['manifestSha256'])
    require(c['baseSha'] == verified['source']['baseSha'], 'Approved remote base differs from the verified candidate baseline')
    publication.branch(c['branch']); publication.branch(c['baseRef'])
    require(c['branch'] != c['baseRef'], 'Publication cannot target the base branch')
    delivery.keys(c['repository'], ('name', 'id'), 'repository')
    require(delivery.pattern(c['repository']['name'], '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
            and all(v not in ('.', '..') for v in c['repository']['name'].split('/')) and delivery.positive(c['repository']['id']),
            'Invalid repository identity')
    require(isinstance(c['title'], str) and 0 < len(c['title']) <= 256 and all(ord(v) >= 32 for v in c['title']), 'Invalid title')
    acceptance.safe_text(c['title'])
    protected = [ROOT, Path(c['localRun']['state']), Path(verified['source']['checkout']),
                 Path(verified['config']['source']['checkout']), config_path,
                 Path(verified['config']['campaignDirectory']), Path(verified['config']['historicalBudget']['directory']),
                 Path(verified['config']['historicalBudget']['legacy']['path']).parent]
    if 'controller' in c['localRun']:
        protected.append(api.canonical_path(c['localRun']['controller']['path']))
    if verified['runIntent']['format'] == 'aim-flow-resumed-candidate-local-run':
        protected.extend([Path(verified['runIntent']['producerController']['path']),
                          Path(verified['runIntent']['workflowState'])])
        if 'campaignContinuation' in verified['runIntent']:
            protected += [Path(verified['runIntent']['campaignDirectory']),
                          Path(verified['runIntent']['campaignContinuation']['path'])]
    require(not any(state == p or state in p.parents or p in state.parents for p in protected),
            'Delivery state overlaps a protected source/controller/local-run/budget')
    body = render_body(verified)
    pc = publication_config(c, verified, state, body)
    # Positive PR identity is allocated only after this chain creates its draft.
    # The local shape check has no external target or authority of its own.
    delivery.validate_target(merge_config(c, pc, {'number': 1, 'headSha': verified['source']['headSha'],
        'headRef': c['branch'], 'baseSha': c['baseSha'], 'baseRef': c['baseRef'], 'markDraftReady': True}))
    # Validate metadata using the same typed adapters without creating their state or inputs.
    # Full adapter prepare is repeated after generated files exist, before any effect.
    delivery.keys(c['transport'], ('gh', 'home', 'configDirectory', 'timeoutSeconds'), 'GitHub transport')
    api.pinned(c['transport']['gh'])
    require(os.access(c['transport']['gh']['path'], os.X_OK), 'Pinned gh must be executable')
    for key in ('home', 'configDirectory'):
        require(api.canonical_path(c['transport'][key]).is_dir(), 'Declared gh auth directory missing')
    require(type(c['transport']['timeoutSeconds']) is int and 1 <= c['transport']['timeoutSeconds'] <= 300,
            'Invalid GitHub metadata deadline')
    intent = {'format': FORMAT, 'schemaVersion': 1, 'stage': 'grant-accepted-local-work-to-exact-main-push',
        'config': {'path': str(config_path), 'sha256': config_sha}, 'state': str(state),
        'controllerManifestSha256': c['manifestSha256'], 'localEvidence': verified['evidence'],
        'source': verified['source'], 'repository': c['repository'], 'baseRef': c['baseRef'], 'branch': c['branch'],
        'draftTitle': c['title'], 'draftBody': body, 'bodyScope': BODY_SCOPE,
        'publicationConfigSha256': api.digest(api.encoded(pc)),
        'localProofSha256': api.digest(api.encoded(local_proof(verified))),
        'requiredPrChecks': c['requiredPrChecks'], 'requiredPrStatuses': c['requiredPrStatuses'], 'workflow': c['workflow'],
        'effects': ['create exact new branch using empty expected-ref lease', 'create exact new draft PR',
                    'observe complete exact-head PR checks', 'mark that draft ready for review',
                    'merge only that PR with exact-head guard', 'observe exact merged-main push run/attempt/jobs/artifact'],
        'pendingDeliveryObligations': verified['review']['pendingDeliveryObligations'],
        **({'ownerAcceptance': verified['ownerAcceptance']} if 'ownerAcceptance' in verified else {}),
        'boundary': BOUNDARY, 'modelCallsAuthorized': False, 'projectExecutionAuthorized': False,
        'releaseAuthorized': False, 'deploymentAuthorized': False}
    return c, verified, body, pc, intent


def retain(state, name, value):
    raw = value if isinstance(value, bytes) else api.encoded(value)
    if (state / name).exists():
        require(api.file_bytes(state / name) == raw, 'Generated delivery input changed: ' + name)
    else:
        api.durable(state, name, raw)
    return pinned(state / name)


def generated_inputs(state, body, pc, verified):
    expected = {'draft-body.md': body.encode(), 'publication-config.json': api.encoded(pc),
                'local-proof.json': api.encoded(local_proof(verified))}
    names = {name: api.digest(raw) for name, raw in expected.items()}
    marker = state / 'prepared.json'
    if marker.exists():
        require(parsed(marker) == names, 'Generated delivery inventory changed')
        for name, raw in expected.items():
            require(api.file_bytes(state / name) == raw, 'Missing or changed consumed delivery input')
    else:
        require(not any((state / name).exists() for name in expected)
                and not any(state.glob('*dispatch.json')), 'Interrupted delivery preparation; never repair or adopt')
        for name, raw in expected.items():
            api.durable(state, name, raw)
        api.durable(state, marker.name, api.encoded(names))


def child_receipt(directory, result):
    files = sorted(directory.glob('result-*.json'))
    require(files and api.file_bytes(files[-1]) == api.encoded(result), 'Child result is not its exact durable receipt')
    return pinned(files[-1])


def verify_receipt(pin):
    value = delivery.unique_json(delivery.pinned_metadata(pin))
    for name, digest in value.get('evidenceFiles', {}).items():
        require(Path(name).name == name, 'Unsafe child evidence filename')
        require(api.digest(api.file_bytes(Path(pin['path']).parent / name)) == digest, 'Retained child evidence changed')
    return value


def outcome(state, intent, status, **extra):
    result = {'format': FORMAT, 'schemaVersion': 1, 'status': status,
        'intentSha256': api.digest(api.encoded(intent)), 'source': intent['source'], 'repository': intent['repository'],
        'localFeatureAccepted': True, 'pendingDeliveryObligations': intent['pendingDeliveryObligations'],
        **({'localAcceptanceBasis': intent['ownerAcceptance']['basis'],
            'modelLocalFeatureAccepted': False, 'ownerAcceptance': intent['ownerAcceptance']}
           if 'ownerAcceptance' in intent else {}),
        'productAccepted': False, 'deliveryReady': False, 'productionDeployed': False,
        'releaseExecuted': False, 'modelCallsExecuted': False, 'projectCommandsExecuted': False,
        'boundary': BOUNDARY, **extra}
    retain(state, 'result-%06d.json' % (len(list(state.glob('result-*.json'))) + 1), result)
    return result


def run(config_path, config_sha, state, authorization=None, *, continue_run=False, observe=False,
        publication_transport=None, push_transport=None, github_transport=None):
    c, verified, body, pc, intent = prepare(config_path, config_sha, state)
    state = api.canonical_path(state); digest = api.digest(api.encoded(intent))
    require(sum((authorization is not None, continue_run, observe)) <= 1, 'Choose one delivery mode')
    if authorization is None and not continue_run and not observe:
        return {'status': 'awaiting-authorization', 'intentSha256': digest, 'intent': intent, 'externalReads': False, 'externalEffects': False}
    if authorization is not None:
        require(authorization == digest, 'Exact new GitHub phase authorization required')
        with new_owned_directory(state):
            retain(state, 'grant.json', {'intent': intent, 'intentSha256': digest}); api.durable(state, 'chain.lock', b'')
    require(parsed(state / 'grant.json') == {'intent': intent, 'intentSha256': digest}, 'Delivery phase grant or local evidence changed')
    with os.fdopen(owned_file(state, 'chain.lock', os.O_RDWR), 'rb+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        phase = 'prepare'
        try:
            if observe and not (state / 'prepared.json').exists():
                return outcome(state, intent, 'awaiting-local-preparation')
            generated_inputs(state, body, pc, verified)
            pp = pinned(state / 'publication-config.json'); ps = state / 'publication'
            merge_marker = state / 'merge-dispatch.json'
            if not merge_marker.exists():
                require(not (state / 'merge').exists(), 'Unrecorded merge child must not be adopted')
                marker = state / 'publication-dispatch.json'
                if not marker.exists():
                    require(not ps.exists(), 'Unrecorded publication child must not be adopted')
                    if observe:
                        return outcome(state, intent, 'awaiting-publication-dispatch')
                    preview = publication.run(Path(pp['path']), pp['sha256'], ps)
                    require(prepare(config_path, config_sha, state)[-1] == intent, 'Local evidence changed before publication')
                    retain(state, marker.name, {'intentSha256': preview['intentSha256'], 'intent': preview['intent']})
                    phase = 'publication-dispatch'
                    result = publication.run(Path(pp['path']), pp['sha256'], ps, preview['intentSha256'],
                        transport=publication_transport, push_transport=push_transport)
                else:
                    if not ps.exists():
                        return outcome(state, intent, 'publication-outcome-unknown', replayPermitted=False)
                    preview = publication.run(Path(pp['path']), pp['sha256'], ps)
                    require(parsed(marker) == {'intentSha256': preview['intentSha256'], 'intent': preview['intent']}, 'Consumed publication scope changed')
                    phase = 'publication-observation'
                    result = publication.run(Path(pp['path']), pp['sha256'], ps, resume=True, transport=publication_transport)
                    if result['status'] == 'branch-observed-no-pr-no-retry' and result.get('draftContinuationSha256') and not observe:
                        continuation = state / 'draft-continuation-dispatch.json'
                        if continuation.exists():
                            return outcome(state, intent, 'draft-continuation-outcome-unknown', replayPermitted=False)
                        retain(state, continuation.name, {'intentSha256': result['draftContinuationSha256'], 'intent': result['draftContinuation']})
                        phase = 'draft-continuation-dispatch'
                        result = publication.run(Path(pp['path']), pp['sha256'], ps,
                            draft_authorization=result['draftContinuationSha256'], transport=publication_transport)
                receipt = child_receipt(ps, result)
                if result['status'] != 'draft-pr-verified':
                    return outcome(state, intent, 'publication-pending-or-blocked', publicationReceipt=receipt, childStatus=result['status'], replayPermitted=False)
                expected_pull = {**result['deliveryPull']}
                require(expected_pull['headSha'] == verified['source']['headSha'] and expected_pull['baseSha'] == c['baseSha']
                        and expected_pull['headRef'] == c['branch'] and expected_pull['baseRef'] == c['baseRef']
                        and expected_pull['markDraftReady'] is True, 'Published PR escaped the scoped phase grant')
                binding = {'publicationConfig': pp, 'pull': expected_pull, 'repository': c['repository']}
                if (state / 'publication-binding.json').exists():
                    require(parsed(state / 'publication-binding.json')['binding'] == binding, 'Published PR identity changed')
                else:
                    retain(state, 'publication-binding.json', {'binding': binding, 'receipt': receipt})
                mc = merge_config(c, pc, expected_pull)
                retain(state, 'merge-config.json', mc)
                mp = pinned(state / 'merge-config.json'); ms = state / 'merge'
                preview = delivery.run(Path(mp['path']), mp['sha256'], ms)
                phase = 'pr-readiness'
                reads = state / 'pr-readiness'
                if not reads.exists():
                    with new_owned_directory(reads):
                        pass
                session = delivery.Session(mc, github_transport or delivery.GhTransport(mc), reads)
                try:
                    session.preflight(True)
                except Exception as error:
                    return outcome(state, intent, 'awaiting-pr-readiness', publicationReceipt=receipt,
                        error={'class': type(error).__name__, 'message': str(error)}, observations=session.observations)
                if observe:
                    return outcome(state, intent, 'pr-ready-awaiting-granted-continuation', publicationReceipt=receipt)
                require(prepare(config_path, config_sha, state)[-1] == intent, 'Local evidence changed before merge dispatch')
                retain(state, merge_marker.name, {'intentSha256': preview['intentSha256'], 'intent': preview['intent']})
                phase = 'merge-dispatch'
                result = delivery.run(Path(mp['path']), mp['sha256'], ms, preview['intentSha256'], transport=github_transport)
            else:
                binding = parsed(state / 'publication-binding.json')
                pub_result = verify_receipt(binding['receipt'])
                require(pub_result['status'] == 'draft-pr-verified' and binding['binding']['pull'] == pub_result['deliveryPull'],
                        'Retained publication proof changed')
                mc = merge_config(c, pc, binding['binding']['pull'])
                require(parsed(state / 'merge-config.json') == mc, 'Consumed merge configuration changed')
                mp = pinned(state / 'merge-config.json'); ms = state / 'merge'
                preview = delivery.run(Path(mp['path']), mp['sha256'], ms)
                require(parsed(merge_marker) == {'intentSha256': preview['intentSha256'], 'intent': preview['intent']},
                        'Consumed merge scope changed')
                if not ms.exists():
                    return outcome(state, intent, 'merge-outcome-unknown', replayPermitted=False)
                phase = 'merge-observation'
                result = delivery.run(Path(mp['path']), mp['sha256'], ms, resume=True, transport=github_transport)
            receipt = child_receipt(ms, result)
            if result['status'] != 'main-push-verified':
                return outcome(state, intent, 'awaiting-main-push' if result['status'] == 'awaiting-main-push' else 'merge-pending-or-blocked',
                               mainPushReceipt=receipt, childStatus=result['status'], replayPermitted=False)
            require(result['headSha'] == verified['source']['headSha'] and result['baseSha'] == c['baseSha']
                    and result['repository'] == c['repository'], 'Main-push handoff source differs')
            require(result['mergedTree'] == verified['source']['treeSha'],
                    'Merged main tree differs from the accepted Flow candidate; release blocked')
            publication_receipt = parsed(state / 'publication-binding.json')['receipt']
            publication_result = verify_receipt(publication_receipt)
            require(publication_result['status'] == 'draft-pr-verified'
                    and publication_result['deliveryPull'] == mc['pull'], 'Original publication receipt differs')
            require(prepare(config_path, config_sha, state)[-1] == intent, 'Local evidence changed during main-push observation')
            return outcome(state, intent, 'main-push-handoff-ready', mainPushReceipt=receipt,
                phaseGrant=pinned(state / 'grant.json'), publicationReceipt=publication_receipt,
                candidateReceipt=verified['evidence']['candidate'], localRunReceipt=verified['evidence']['localRun'],
                pushResponseConfirmed=publication_result['pushResponseConfirmed'],
                draftResponseConfirmed=publication_result['draftResponseConfirmed'],
                mergedSha=result['mergedSha'], mergedTree=result['mergedTree'], observedMainSha=result['observedMainSha'],
                workflowPath=result['workflowPath'], runId=result['runId'], runAttempt=result['runAttempt'], artifact=result['artifact'],
                nextStage='separately-authorized-project-release-and-production-verification',
                mergeResponseConfirmed=result['mergeResponseConfirmed'], mergeAttribution=result['mergeAttribution'])
        except Exception as error:
            return outcome(state, intent, 'blocked-no-retry', phase=phase,
                error={'class': type(error).__name__, 'message': str(error)}, replayPermitted=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True); parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true'); mode.add_argument('--authorize-intent')
    mode.add_argument('--continue', dest='continue_run', action='store_true', help='Advance only unattempted phases under the retained grant')
    mode.add_argument('--observe', action='store_true', help='External reads only; never a new mutation')
    args = parser.parse_args(argv)
    result = run(args.config, args.config_sha256, args.state, args.authorize_intent, continue_run=args.continue_run, observe=args.observe)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('awaiting-authorization', 'main-push-handoff-ready') else 1
