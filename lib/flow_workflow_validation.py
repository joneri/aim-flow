"""Explicitly granted continuation: independent source review and typed local tests.

V1 workflow grants never authorize this continuation. Catalog facts select an
attempt, not a successful qualification. No model-authored shell is dispatched.
"""
import argparse
import copy
import fcntl
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import flow_api_delivery as api
import flow_workspace_review as review
import flow_execution_refinement as refinement
import flow_review_execution_context as execution_context
import flow_workflow as workflow
from aim_ci import control_package, project_onboarding as onboarding
from aim_ci import project_plan_derivation as derivation, project_validation as validation
from aim_ci import project_container as container
from aim_ci.owned_paths import Refused, new_owned_directory, owned_file
from aim_ci.project_discovery import CommitReader, unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-workflow-validation'


def report(status, **extra):
    return {'format': FORMAT, 'status': status, 'ready': False,
            'productAccepted': False, 'acceptance': 'not-established',
            'publicationAuthorized': False, 'deploymentAuthorized': False, **extra}


def distinct(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def compose(derived):
    """Serialize the complete supported local DAG without dropping predecessors."""
    declaration = copy.deepcopy(derived['declaration'])
    if declaration is None:
        raise Refused('No supported local validation stages')
    if any(row['kind'] == 'assessment-predecessor' for row in derived['resourceNeeds']):
        raise Refused('Unresolved assessment predecessor; cannot compose local closure')
    jobs = declaration['jobs']
    seen = set()
    for job in jobs:
        if job['mode'] != 'local-sequence' or not set(job['needs']) <= seen:
            raise Refused('Local dependency closure is not topologically ordered')
        seen.add(job['id'])
    if not any(stage['operation'] == 'run' for job in jobs for stage in job['stages']):
        raise Refused('No named validation test stage; install-only is not code-to-tests')
    runtimes = declaration['runtimes']
    declaration['runtimes'] = [{'id': 'workflow-runtime',
        'requirements': distinct([v for r in runtimes for v in r['requirements']]),
        'sources': distinct([v for r in runtimes for v in r['sources']])}]
    graph = [{'id': job['id'], 'needs': job['needs'], 'stages': job['stages']} for job in jobs]
    declaration['jobs'] = [{'id': 'workflow-local-validation', 'mode': 'local-sequence',
        'runtime': 'workflow-runtime', 'needs': [],
        'stages': [v for job in jobs for v in job['stages']],
        'coverage': '\n'.join(job['id'] + ': ' + job['coverage'] for job in jobs),
        'effects': distinct([v for job in jobs for v in job['effects']]),
        'prerequisites': distinct([v for job in jobs for v in job['prerequisites']]),
        'sources': distinct([v for job in jobs for v in job['sources']])}]
    return declaration, graph


def browser_resource(row, spec, template, requirements):
    """Bind prepared Playwright facts to the runtime the adapter will measure.

    This is an attempt-selection gate, not evidence of a browser launch.
    Other browser preparation schemas need their own adapter.
    """
    if row['kind'] != 'browser-runtime' or row.get('executionSpec') != spec:
        return False
    prepared = template.get('preparedImage')
    if not prepared or row.get('identity') != prepared.get('id'):
        return False
    binding = container.prepared_runtime(prepared, template['image'])
    record = binding['record']; facts = record.get('preparation', {})
    tool = prepared['runtimeTools'].get('headless_shell')
    caps = row.get('capabilities', {})
    if (not tool or facts.get('runtimeTools', {}).get('headless_shell') != tool
            or caps.get('utilities', {}).get('headless_shell') != tool['sha256']
            or not isinstance(facts.get('browserTreeSha256'), str)
            or not re.fullmatch('[a-f0-9]{64}', facts['browserTreeSha256'])):
        return False
    for requirement in requirements:
        name, _, constraint = requirement.partition(' ')
        if (name not in ('playwright', '@playwright/test')
                or caps.get('browserPackages', {}).get(name) != facts.get('playwrightVersion')
                or not derivation.version_satisfies(facts.get('playwrightVersion'), constraint)):
            return False
    return bool(requirements)


def select_resource(derived, catalog):
    """One common pinned container workspace must supply every local resource need."""
    local_ids = {job['id'] for job in (derived['declaration'] or {}).get('jobs', [])}
    needs = [n for n in derived['resourceNeeds'] if n['step'] is None or n['step'] in local_ids]
    candidates = []
    for row in catalog['resources']:
        spec = row.get('executionSpec')
        if row['kind'] != 'node-runtime' or not spec or spec['backend'] not in ('container', 'local'):
            continue
        template = unique_json(api.pinned({k: spec[k] for k in ('path', 'sha256')}))
        local = spec['backend'] == 'local'
        compatible = True
        for need in needs:
            kind = need['kind']
            if kind == 'node-runtime':
                compatible &= derivation.matches_need(row, kind, need['requirements'])
            elif kind == 'runtime-probe':
                caps = row.get('capabilities', {})
                tools = template.get('tools', {}) if local else template.get('preparedImage', {}).get('runtimeTools', {})
                compatible &= all(
                    bool(caps.get(p['tool'] + 'Version')) if p['tool'] in ('node', 'npm') else
                    bool(tools.get(p['tool'], {}).get('sha256') == caps.get('utilities', {}).get(p['tool'])
                         and caps.get('utilities', {}).get(p['tool']))
                    for p in need['requirements'])
            elif kind == 'browser-runtime':
                # A browser in a different image does not qualify this workspace.
                compatible &= any((r['kind'] == 'browser-runtime' and r.get('executionSpec') == spec
                    and all(derivation.version_satisfies(r.get('capabilities', {}).get('browserPackages', {}).get(w.partition(' ')[0]), w.partition(' ')[2])
                            for w in need['requirements'])) if local else browser_resource(r, spec, template, need['requirements'])
                    for r in catalog['resources'])
            elif kind == 'npm-cache':
                if local and template.get('environment', {}).get('npm_config_offline') != 'true':
                    continue  # Normal project npm installation may use the host network.
                compatible &= any(r['kind'] == 'npm-cache' and r.get('executionSpec') == spec
                    and r['identity'] == template.get('npmCache', {}).get('sha256')
                    and derivation.matches_need(r, kind, need['requirements'])
                    for r in catalog['resources'])
            else:
                compatible = False
        if compatible:
            candidates.append(row)
    if len(candidates) != 1:
        raise Refused('Need one unambiguous compatible project runtime/spec for the complete local closure; found ' + str(len(candidates)))
    row = candidates[0]
    spec = unique_json(api.pinned({k: row['executionSpec'][k] for k in ('path', 'sha256')}))
    caps = row.get('capabilities', {})
    if row['executionSpec']['backend'] == 'local':
        if spec.get('format') != 'aim-flow-project-host-spec':
            raise Refused('Local resource must bind a project host spec')
        for tool in ('node', 'npm', 'git'):
            api.pinned(spec['tools'][tool])
        return row, spec
    image = spec.get('image', {})
    if any(image.get(k) != caps.get(k) for k in ('nodeVersion', 'npmVersion', 'architecture')) or image.get('os') != caps.get('platform'):
        raise Refused('Catalog runtime capabilities differ from pinned execution image')
    return row, spec


def review_resource_context(derived, catalog, catalog_pin, candidate, source_read_mode=None):
    # Scheduling predecessors are exactly what this review may refine. They do
    # not describe an infrastructure capability. Keep every other resource need.
    proposed = copy.deepcopy(derived)
    proposed['resourceNeeds'] = [n for n in proposed['resourceNeeds'] if n['kind'] != 'assessment-predecessor']
    resource, spec = select_resource(proposed, catalog)
    local = resource['executionSpec']['backend'] == 'local'
    if local and source_read_mode is not None:
        raise Refused('Container source restoration policy cannot authorize host execution')
    if source_read_mode is not None:
        spec = {**spec, 'sourceReadModePolicy': container.source_read_mode_policy(source_read_mode)}
    context = execution_context.build(candidate, catalog_pin, resource, spec, proposed['declaration'],
        api.digest(api.file_bytes(ROOT / ('lib/aim_ci/project_host.py' if local else container.HELPER))))
    return resource, spec, context


def source_paths(c, candidate):
    reader = CommitReader(ROOT, Path(candidate['checkout']), Path(c['source']['git']),
                          c['source']['gitSha256'], candidate['commit'])
    if reader.tree != candidate['tree']:
        raise Refused('Candidate source selection tree changed')
    names = []
    for name, mode, kind, oid in reader.entries(reader.commit, recursive=True):
        onboarding._source_path(name)
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise Refused('Candidate execution source selection requires regular committed files')
        names.append(name)
    reader.verify_head()
    return sorted(names)


def verified_producer(c, wc, ws):
    """Consume a completed Flow work run through its pinned, read-only verifier.

    This is explicit provenance, never a migration of the producer's authority.
    The predecessor's code is not imported into the current interpreter.
    """
    if 'producerKind' in c or 'repairConfig' in c:
        from flow_repair_candidate import verify_repair_producer
        return verify_repair_producer(c, wc, ws)
    producer = c['producerController']
    onboarding._keys(producer, ('path', 'manifestSha256'), 'Flow producer controller')
    root = api.canonical_path(producer['path'])
    before = validation.prior_controller(root, producer['manifestSha256'])
    if 'lib/flow_workflow.py' not in unique_json(before)['files']:
        raise Refused('Producer controller lacks the fixed workflow verifier')
    if wc['manifestSha256'] != producer['manifestSha256']:
        raise Refused('Workflow config differs from explicitly pinned producer controller')
    config_raw = api.pinned(c['workflowConfig'])
    pins = {name: workflow.pin(ws / name) for name in ('grant.json', 'candidate-receipt.json')}
    receipt = unique_json(api.pinned(pins['candidate-receipt.json']))
    command = [c['python']['path'], '-IBS', str(root / 'aim-flow.py'), 'work',
        '--config', c['workflowConfig']['path'], '--config-sha256', c['workflowConfig']['sha256'],
        '--state', str(ws), '--verify']
    process = validation.verification_process(command, ROOT,
        {'PATH': os.defpath, 'HOME': str(ROOT), 'LC_ALL': 'C'},
        timeout_seconds=c.get('producerVerificationTimeoutSeconds', validation.VERIFIER_TIMEOUT))
    if process.returncode or process.stderr:
        raise Refused('Pinned Flow producer refused completed candidate verification')
    result = unique_json(process.stdout)
    if (not isinstance(result, dict) or result.get('format') != workflow.FORMAT
            or result.get('status') != 'candidate-prepared'
            or result.get('candidateReceipt') != pins['candidate-receipt.json']
            or receipt.get('status') != 'candidate-prepared'
            or result.get('candidate') != receipt.get('candidate')
            or result.get('source') != receipt.get('source')
            or result.get('projectInstance') != receipt.get('intent', {}).get('projectInstance')
            or result.get('publicationAuthorized') is not False):
        raise Refused('Producer verifier did not confirm the exact private Flow candidate')
    if (before != validation.prior_controller(root, producer['manifestSha256'])
            or config_raw != api.pinned(c['workflowConfig'])
            or any(pin != workflow.pin(ws / name) for name, pin in pins.items())):
        raise Refused('Flow producer evidence changed during verification')
    api.pinned(c['python'])
    return result


def inputs(config_path, config_sha, state, *, controller_root=None):
    # Read-only reconstruction of a recognized predecessor contract; never import its code.
    root = ROOT if controller_root is None else controller_root
    c = unique_json(api.pinned({'path': str(config_path), 'sha256': config_sha}))
    capture = review.capture_requested(c)
    refine = refinement.requested(c)
    onboarding._keys(c, ('schemaVersion', 'manifestSha256', 'workflowConfig', 'workflowState',
                        'python', 'reviewMaxOutputTokens', 'reviewMaxOperationNanoUsd',
                        *(('captureArtifacts',) if capture else ()),
                        *(('producerController',) if 'producerController' in c else ()),
                        *(('producerKind', 'repairConfig') if 'producerKind' in c or 'repairConfig' in c else ()),
                        *(('refineExecutionPlan',) if refine else ()),
                        *(('producerVerificationTimeoutSeconds',) if 'producerVerificationTimeoutSeconds' in c else ()),
                        *(('reviewEvidencePolicy',) if 'reviewEvidencePolicy' in c else ()),
                        *(('reviewMaxSourceBytes',) if 'reviewMaxSourceBytes' in c else ()),
                        *(('campaignContinuation',) if 'campaignContinuation' in c else ()),
                        *(('resourceCatalog',) if 'resourceCatalog' in c else ()),
                        *(('sourceReviewReuse',) if 'sourceReviewReuse' in c else ()),
                        *(('sourceReadModePolicy',) if 'sourceReadModePolicy' in c else ()),
                        *(('findingDispositionPolicy',) if 'findingDispositionPolicy' in c else ())), 'workflow continuation')
    review.base.finding_policy(c.get('findingDispositionPolicy'))
    if 'reviewMaxSourceBytes' in c:review.base.source_byte_limit(c['reviewMaxSourceBytes'])
    if 'sourceReadModePolicy' in c:container.source_read_mode_policy(c['sourceReadModePolicy'])
    if 'sourceReadModePolicy' in c and not refine:
        raise Refused('Source read-mode policy requires an explicit reviewed execution context')
    if review.base.evidence_policy(c.get('reviewEvidencePolicy')) and not (capture and refine):
        raise Refused('Review evidence policy requires captured refined validation')
    if type(c['schemaVersion']) is not int or c['schemaVersion'] != 1:
        raise Refused('Unsupported explicit validation phase grant')
    control_package.verify_installation(root, c['manifestSha256'])
    wpin = c['workflowConfig']; wc = unique_json(api.pinned(wpin))
    onboarding._keys(c['python'], ('path', 'sha256'), 'Python verifier')
    python = api.canonical_path(c['python']['path']); api.pinned(c['python'])
    if not os.access(python, os.X_OK):
        raise Refused('Python verifier must be executable')
    ws = api.canonical_path(c['workflowState'])
    if ('producerKind' in c or 'repairConfig' in c) and 'producerController' not in c:
        raise Refused('Repair producer requires an explicit pinned controller')
    if 'producerVerificationTimeoutSeconds' in c:
        if 'producerController' not in c:
            raise Refused('Producer verification timeout requires an explicit producer')
        validation.producer_timeout(c)
    if 'producerController' in c:
        candidate = verified_producer(c, wc, ws)
    else:
        if wc['manifestSha256'] != c['manifestSha256']:
            raise Refused('Different workflow controller requires an explicit producerController pin; old grants are not upgraded')
        candidate = workflow.verify(Path(wpin['path']), wpin['sha256'], ws)
    wc = api.funded_config(wc, c.get('campaignContinuation'))
    if 'resourceCatalog' in c:
        api.pinned(c['resourceCatalog'])
        wc = {**wc, 'resourceCatalog': c['resourceCatalog']}
    state = api.canonical_path(state)
    if 'sourceReviewReuse' in c:
        reused = c['sourceReviewReuse']
        onboarding._keys(reused, ('controller', 'config', 'state',
            *(('allowDiagnosticRunnerUpgrade',) if 'allowDiagnosticRunnerUpgrade' in reused else ())), 'retained source review')
        if 'allowDiagnosticRunnerUpgrade' in reused and reused['allowDiagnosticRunnerUpgrade'] is not True:
            raise Refused('Diagnostic runner upgrade must be explicitly true in the new grant')
        onboarding._keys(reused['controller'], ('path', 'manifestSha256'), 'retained review controller')
        for p in (api.canonical_path(reused['state']), api.canonical_path(reused['controller']['path']),
                  api.canonical_path(reused['config']['path'])):
            if state == p or state in p.parents or p in state.parents:
                raise Refused('Validation reuse state overlaps retained source review inputs')
    for path in (root, api.canonical_path(c.get('producerController', {'path': str(ROOT)})['path']), ws, Path(wc['source']['checkout']), Path(wc['campaignDirectory']), *api.prior_campaign_roots(wc),
                 Path(wc['historicalBudget']['directory']), Path(wc['historicalBudget']['legacy']['path']).parent):
        if state == path or state in path.parents or path in state.parents:
            raise Refused('Continuation state must be disjoint from workflow/source/controller/budget roots')
    for path in (config_path, Path(wpin['path']), Path(c['python']['path']), Path(wc['resourceCatalog']['path'])):
        if path.is_relative_to(state):
            raise Refused('Continuation input overlaps state')
    api.token_bounds(0, c['reviewMaxOutputTokens'])
    if type(c['reviewMaxOperationNanoUsd']) is not int or not 1 <= c['reviewMaxOperationNanoUsd'] <= api.LIMIT:
        raise Refused('Invalid candidate review operation limit')
    catalog = derivation.resource_catalog(unique_json(api.pinned(wc['resourceCatalog'])))
    instance_pin = candidate['projectInstance']
    instance = unique_json(api.pinned(instance_pin))
    derived = derivation.derive_from_instance(root, instance, instance_pin['sha256'], catalog)
    selected = source_paths(wc, candidate['candidate'])
    intent = {'format': FORMAT, 'schemaVersion': 1, 'phase': 'candidate-review-and-local-validation',
        'config': {'path': str(config_path), 'sha256': config_sha}, 'state': str(state),
        'manifestSha256': c['manifestSha256'], 'workflowConfig': wpin,
        'workflowGrant': workflow.pin(ws / 'grant.json'), 'candidateReceipt': candidate['candidateReceipt'],
        'candidate': candidate['candidate'], 'instance': instance_pin, 'resourceCatalog': wc['resourceCatalog'],
        'derivedPlanSha256': api.digest(api.encoded(derived)), 'executionSourcePaths': selected,
        'reviewSourcePaths': sorted(set(wc['reviewSources']) | set((derived['declaration'] or {}).get('sourcePaths', []))), 'requirement': wc['requirement'],
        'reviewMaxOutputTokens': c['reviewMaxOutputTokens'], 'reviewMaxOperationNanoUsd': c['reviewMaxOperationNanoUsd'],
        'provider': 'OpenAI Responses API', 'model': api.MODEL, 'reasoning': 'xhigh',
        'maximumGenerationAttempts': 1, 'maximumContainerAttempts': 1,
        'campaignDirectory': wc['campaignDirectory'], 'historicalBudget': wc['historicalBudget'],
        'combinedHistoricalAndCampaignLimitNanoUsd': api.campaign_limit(wc),
        'phases': ['candidate-source-review-and-workspace-policy', 'container-runtime-qualification', 'typed-local-validation'],
        'scope': 'Derive one serial local DAG from assessed literal npm stages; normal npm lifecycle hooks; pinned container spec limits; network:none; private tmpfs workspace; no publication or acceptance',
        'resourceQualification': 'Catalog claims are not success; the container adapter must enforce and verify the pinned runtime and containment',
        'publicationAuthorized': False, 'deploymentAuthorized': False, 'productAcceptanceAuthorized': False}
    if 'producerController' in c:
        intent['producerController'] = c['producerController']
        intent['producerVerificationTimeoutSeconds'] = c.get('producerVerificationTimeoutSeconds', validation.VERIFIER_TIMEOUT)
    if 'producerKind' in c:
        intent.update(producerKind=c['producerKind'], repairConfig=c['repairConfig'],
                      instanceController=candidate['instanceController'])
    if 'reviewEvidencePolicy' in c:
        intent['reviewEvidencePolicy'] = c['reviewEvidencePolicy']
    if 'reviewMaxSourceBytes' in c:intent['reviewMaxSourceBytes'] = c['reviewMaxSourceBytes']
    if 'findingDispositionPolicy' in c:
        intent['findingDispositionPolicy'] = c['findingDispositionPolicy']
    if 'campaignContinuation' in c:
        intent['campaignContinuation'] = c['campaignContinuation']
    if 'sourceReviewReuse' in c:
        intent.update(sourceReviewReuse=c['sourceReviewReuse'], maximumGenerationAttempts=0)
    if 'sourceReadModePolicy' in c:intent['sourceReadModePolicy'] = c['sourceReadModePolicy']
    if refine:
        intent['executionRefinement'] = 'Fresh source-cited per-assessment-step finite scheduling; all substituted acceptance obligations remain open'
        _, _, public_context = review_resource_context(derived, catalog, wc['resourceCatalog'], candidate['candidate'], c.get('sourceReadModePolicy'))
        intent['reviewExecutionContext'] = public_context
        if public_context['adapter'] == 'project-host':
            intent.update(executionBackend='host', maximumHostAttempts=1, maximumContainerAttempts=0,
                phases=['candidate-source-review-and-workspace-policy', 'host-runtime-qualification', 'typed-local-validation'],
                scope='Existing project npm process in a private committed-source copy, normal host/network access, explicit environment and pinned tools; NOT a security sandbox; original checkout unchanged; no publication or acceptance',
                resourceQualification='Catalog claims are not success; host tools and exact source/output receipts are independently checked, no container containment claimed')
    if capture:
        intent['artifactCollection'] = {'selection': 'Fresh source-cited independent workspace review',
            **review.capture_limits(intent.get('reviewExecutionContext', {}).get('limits', {}).get('outputBytes', 32_000_000)), 'mediaTypes': ['image/png', 'application/json'],
            'scope': 'Collect only reviewed exact uncommitted output paths locally; no artifact-byte transmission until separately granted acceptance review',
            'artifactTransmissionAuthorized': False}
    return c, wc, ws, candidate, instance, derived, catalog, intent


def requirement_document(original, *, merge_heading_sections=False):
    """Preserve exact section/checklist text, with bounded deterministic criteria."""
    if not isinstance(original, str) or not original.strip() or len(original) > 32000:
        raise Refused('Original requirement exceeds bounded review input; no truncation')
    sections = re.split(r'(?m)(?=^#{1,6} )', original)
    if type(merge_heading_sections) is not bool:
        raise Refused('Requirement section projection must be an explicit boolean')
    if merge_heading_sections:
        joined, pending = [], ''
        for section in sections:
            if not section.strip() or re.fullmatch(r'#{1,6} [^\r\n]+(?:\r?\n[ \t]*)*', section):
                pending += section
            else:
                joined.append(pending + section)
                pending = ''
        if not joined:
            raise Refused('Requirement contains headings only; substantive criteria required')
        joined[-1] += pending
        sections = joined
    chunks = []
    for section in sections:
        if not section.strip():
            continue
        current = ''
        for line in section.splitlines(keepends=True):
            if len(line) > 2000:
                raise Refused('Requirement line exceeds criterion bound; structured requirement needed')
            if len(current) + len(line) > 2000:
                chunks.append(current); current = ''
            current += line
        if current.strip():
            chunks.append(current)
    if merge_heading_sections and any(not any(line.strip() and not re.match(r'^#{1,6} ', line)
            for line in chunk.splitlines()) for chunk in chunks):
        raise Refused('Requirement heading prefix exceeds criterion bound; structured requirement needed')
    if not 1 <= len(chunks) <= 32:
        raise Refused('Requirement needs more than 32 criteria; no silent omission')
    return {'original': original, 'criteria': [
        {'id': 'criterion-' + str(i + 1).zfill(3), 'text': text} for i, text in enumerate(chunks)]}


def source_review_state(c, state):
    return api.canonical_path(c['sourceReviewReuse']['state']) if 'sourceReviewReuse' in c else state / 'review-attempt'


def retain_existing(state, name, value):
    """Verify a retained generated input without recreating absent evidence."""
    if api.file_bytes(state / name) != api.encoded(value):
        raise Refused('Retained validation input changed: ' + name)
    return workflow.pin(state / name)


def review_candidate(c, wc, ws, candidate, instance, declaration, state, key_file, transport, execution_context_value=None, verify_only=False):
    retain = retain_existing if verify_only else workflow.retain
    attempt = state / 'review-attempt'
    marker = state / 'review-dispatch.json'
    if marker.exists() and not attempt.exists():
        return None, report('blocked', reason='Review phase outcome unknown; reconcile without retry', replayPermitted=False)
    if attempt.exists():
        for name in ('review-requirement.json', 'review-plan.json', 'review-config.json', 'review-dispatch.json'):
            api.file_bytes(state / name)
    requirement = requirement_document(api.pinned(wc['requirement']).decode('utf-8'),
        merge_heading_sections=review.base.evidence_policy(c.get('reviewEvidencePolicy')))
    # Do not disclose local checkout metadata or implementer self-evaluation.
    assessment = copy.deepcopy(instance['assessment'])
    assessment['target'] = workflow.public_target(assessment['target'])
    plan = {'assessment': assessment, 'scope': wc['editablePaths'],
            'qualification': 'Granted implementation scope, not accepted results.'}
    rp = retain(state, 'review-requirement.json', requirement)
    pp = retain(state, 'review-plan.json', plan)
    config = {'schemaVersion': 1, 'purpose': review.PURPOSE, 'model': api.MODEL, 'reasoning': 'xhigh',
        'manifestSha256': c['manifestSha256'],
        'source': {'checkout': candidate['candidate']['checkout'], 'baselineCommit': wc['source']['commit'],
            'candidateCommit': candidate['candidate']['commit'], 'git': wc['source']['git'], 'gitSha256': wc['source']['gitSha256']},
        'requirement': rp, 'approvedPlan': pp,
        'sourcePaths': sorted(set(wc['reviewSources']) | set(declaration['sourcePaths'])),
        'workspaceContext': {'protectedPaths': sorted(set(declaration['sourcePaths']) | set(wc['reviewSources']) |
            {x['path'] for x in instance['packet']['sources']}), 'stages': [s for j in declaration['jobs'] for s in j['stages']]},
        'campaignDirectory': wc['campaignDirectory'], 'historicalBudget': wc['historicalBudget'],
        'timeoutSeconds': wc['timeoutSeconds'], 'maxOutputTokens': c['reviewMaxOutputTokens'],
        'maxOperationNanoUsd': c['reviewMaxOperationNanoUsd']}
    if c.get('refineExecutionPlan'):
        config['workspaceContext']['refineExecutionPlan'] = True
    if 'reviewEvidencePolicy' in c:
        config['workspaceContext']['reviewEvidencePolicy'] = c['reviewEvidencePolicy']
    if 'reviewMaxSourceBytes' in c:config['workspaceContext']['sourceByteLimit'] = c['reviewMaxSourceBytes']
    if 'findingDispositionPolicy' in c:
        config['findingDispositionPolicy'] = c['findingDispositionPolicy']
    if 'campaignContinuation' in c:
        config['campaignContinuation'] = c['campaignContinuation']
    if execution_context_value is not None:
        config['workspaceContext']['executionContext'] = execution_context_value
    if c.get('captureArtifacts'):
        config['workspaceContext']['captureArtifacts'] = True
    cp = retain(state, 'review-config.json', config)
    prepared = review.prepare(Path(cp['path']), cp['sha256'], attempt)
    expected = prepared[-1]
    if 'sourceReviewReuse' in c:
        if attempt.exists() or marker.exists():
            raise Refused('Retained source review cannot coexist with a fresh dispatch')
        evidence, reused = review.reuse_completed(c['sourceReviewReuse'], prepared)
        retain(state, 'review-reuse.json', reused)
        result = {'status': 'source-review-ready'}
    elif attempt.exists():
        if unique_json(api.file_bytes(marker)) != expected:
            raise Refused('Review dispatch intent changed')
        recorded = unique_json(api.file_bytes(attempt / 'attempt.json'))
        if (recorded.get('intent') != expected or recorded.get('intentSha256') != api.digest(api.encoded(expected))
            or api.file_bytes(attempt / 'config.json') != prepared[1]
            or api.file_bytes(attempt / 'packet.json') != api.encoded(prepared[2])
            or api.file_bytes(attempt / 'request.json') != api.encoded(prepared[3])):
            raise Refused('Retained candidate review differs from generated phase')
        result = api.status(attempt)
    else:
        if verify_only:
            raise Refused('Verification requires a recorded source review')
        if key_file is None and transport is None:
            return None, report('awaiting-provider-credential', nextStage='candidate-source-review')
        # Durable phase record exists before adapter token-count, budget or model effects.
        retain(state, 'review-dispatch.json', expected)
        result = review.run(Path(cp['path']), cp['sha256'], attempt, api.digest(api.encoded(expected)),
                            key_file=key_file, transport=transport)
    if result['status'] not in ('source-review-ready', 'review-blocked'):
        return None, report('blocked', reason='Candidate review consumed or incomplete; no replay', reviewResult=result, replayPermitted=False)
    reviewed_state = source_review_state(c, state)
    evidence = review.verify_evidence(workflow.pin(reviewed_state / 'review.json'), workflow.pin(reviewed_state / 'packet.json'))
    if c.get('captureArtifacts') and not evidence.get('artifactSelection'):
        return None, report('artifact-selection-required', review=evidence, validation='not-run')
    if evidence['workspacePolicy']['unknowns']:
        return None, report('source-input-output-policy-required', review=evidence, validation='not-run')
    if review.base.blocking_findings(evidence):
        return None, report('source-defects', review=evidence, validation='not-run')
    return evidence, None


def generated_output_evidence(result, evidence, candidate):
    outputs = (result.get('report') or {}).get('outputs', {})
    measured, missing = {}, []
    for row in evidence['workspacePolicy']['generatedOutputs']:
        item = outputs.get('project/' + row['path']) if isinstance(outputs, dict) else None
        if (not isinstance(item, dict) or item.get('type') != 'file'
                or not isinstance(item.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])
                or type(item.get('bytes')) is not int or item['bytes'] < 0
                or type(item.get('mode')) is not int or item['mode'] & 0o111):
            missing.append(row['path'])
        else:
            measured[row['path']] = item
    binding = ({'executionBackend': 'host', 'executionIntentSha256': result['intentSha256'],
        'executionReceiptSha256': result['receiptSha256']} if result.get('executionBackend') == 'host' else
        {'containerIntentSha256': result['intentSha256'], 'containerReceiptSha256': result['receiptSha256']})
    return {'candidate': candidate, **binding,
        'excludedInputs': evidence['workspacePolicy']['generatedOutputs'],
        'producedArchives': measured, 'missingOrInvalidOutputs': missing,
        'complete': not missing,
        'qualification': 'Measured regular generated files only; content correctness and acceptance remain unverified.'}


def advance(c, wc, ws, candidate, instance, derived, catalog, intent, state, key_file, transport, verify_only=False):
    retain = retain_existing if verify_only else workflow.retain
    if candidate['onboardingReuse']['status'] != 'source-evidence-unchanged':
        return report('blocked', reason='Fresh candidate assessment required for changed onboarding definitions',
                      onboardingReuse=candidate['onboardingReuse'])
    public_context = None
    reviewed_resource = None
    try:
        job_ids = {j['id'] for j in (derived['declaration'] or {}).get('jobs', [])}
        if not any(step['area'] in ('tests', 'ci') and step['id'] in job_ids for step in instance['assessment']['steps']):
            raise Refused('No supported assessed test stage; installation alone is incomplete')
        if c.get('refineExecutionPlan'):
            declaration = copy.deepcopy(derived['declaration'])
            if declaration is None:
                raise Refused('No local command candidates for refinement')
            resource, spec, public_context = review_resource_context(derived, catalog, wc['resourceCatalog'], candidate['candidate'], c.get('sourceReadModePolicy'))
            reviewed_resource = copy.deepcopy(resource)
            if public_context != intent.get('reviewExecutionContext'):
                raise Refused('Pinned review execution context changed after phase preview')
        else:
            declaration, graph = compose(derived)
            resource, spec = select_resource(derived, catalog)
    except Refused as error:
        return report('resource-or-adapter-required', reason=str(error), resourceNeeds=derived['resourceNeeds'],
                      unsupportedSteps=derived['unsupportedSteps'])
    evidence, stop = review_candidate(c, wc, ws, candidate, instance, declaration, state, key_file, transport, public_context, verify_only=verify_only)
    if stop is not None:
        return stop
    if c.get('refineExecutionPlan'):
        try:
            derived = refinement.apply(derived, instance['assessment']['steps'], evidence['executionRefinement'])
            declaration, graph = compose(derived)
            resource, spec = select_resource(derived, catalog)
            if (resource != reviewed_resource
                    or resource['executionSpec']['sha256'] != public_context['bindings']['executionSpecSha256']):
                raise Refused('Refined runtime differs from the exact reviewed execution context')
        except Refused as error:
            return report('execution-plan-refinement-required', reason=str(error), review=evidence, validation='not-run')
    backend = 'host' if resource['executionSpec']['backend'] == 'local' else 'container'
    if backend == 'host':
        from aim_ci import project_host as executor
    else:
        executor = container
    declaration.update(schemaVersion=2, target=candidate['candidate'], candidateReview=evidence['candidateReview'])
    declaration['review'] = {'reference': evidence['candidateReview']['reference'],
        'qualifications': evidence['candidateReview']['qualifications'],
        'openItems': evidence['blockers'] + derived['agentUnknowns']}
    # Definition sourcePaths stay protected. Execution input closure is separate.
    validation.candidate_binding(ROOT, instance, declaration)
    if (state / (backend + '-dispatch.json')).exists() or (state / (backend + '-attempt')).exists():
        for name in ('validation-declaration.json', 'validation-composition.json', backend + '-spec.json'):
            api.file_bytes(state / name)
    dp = retain(state, 'validation-declaration.json', declaration)
    retain(state, 'validation-composition.json', {'graph': graph,
        'resource': resource, 'derivedPlanSha256': intent['derivedPlanSha256']})
    spec = copy.deepcopy(spec)
    if 'sourceReadModePolicy' in c:spec['sourceReadModePolicy'] = c['sourceReadModePolicy']
    excluded = {x['path'] for x in evidence['workspacePolicy']['generatedOutputs']}
    spec.update(job=declaration['jobs'][0]['id'], sourcePaths=[p for p in intent['executionSourcePaths'] if p not in excluded],
        sourceReview=[evidence['candidateReview']['reference'],
            'Independent source-only review; missing runtime evidence remains open. Phase grant authorizes bounded typed validation, not acceptance.'])
    if c.get('captureArtifacts'):
        spec['artifactCapture'] = review.artifact_capture(evidence['artifactSelection'], review.capture_limits(spec['outputBytes']))
    elif 'artifactCapture' in spec:
        raise Refused('Template artifact capture is not authorized by this continuation')
    sp = retain(state, backend + '-spec.json', spec)
    attempt = state / (backend + '-attempt')
    instance_controller = (candidate['instanceController'] if c.get('producerKind') == 'repair-candidate'
                           else c.get('producerController', {'path': str(ROOT), 'manifestSha256': c['manifestSha256']}))
    args = SimpleNamespace(manifest_sha256=c['manifestSha256'],
        instance=Path(candidate['projectInstance']['path']), instance_sha256=candidate['projectInstance']['sha256'],
        instance_controller=Path(instance_controller['path']),
        instance_controller_manifest_sha256=instance_controller['manifestSha256'],
        python=Path(c['python']['path']), python_sha256=c['python']['sha256'],
        declaration=Path(dp['path']), declaration_sha256=dp['sha256'],
        execution_spec=Path(sp['path']), execution_spec_sha256=sp['sha256'],
        state=attempt, action='execute-project-' + backend + '-validation', preview=True, authorize_intent=None)
    preview = executor.execution(ROOT, args)
    dispatch = preview['intent']
    marker = state / (backend + '-dispatch.json')
    if marker.exists():
        if unique_json(api.file_bytes(marker)) != dispatch:
            raise Refused('Consumed container phase intent changed')
        if not (attempt / 'result.json').exists():
            return report('blocked', reason='Container phase outcome unknown; reconcile without retry', replayPermitted=False)
        args.action = 'verify-project-' + backend + '-validation'
        args.receipt_sha256 = workflow.pin(attempt / 'result.json')['sha256']
        result = executor.execution(ROOT, args)
    else:
        if verify_only:
            raise Refused('Verification requires a recorded container outcome')
        if attempt.exists():
            raise Refused('Unrecorded container attempt; never replay')
        retain(state, marker.name, dispatch)
        args.preview = False
        args.authorize_intent = preview['intentSha256']
        result = executor.execution(ROOT, args)
    retain(state, 'validation-result.json', result)
    output_evidence = generated_output_evidence(result, evidence, candidate['candidate'])
    retain(state, 'workspace-output-evidence.json', output_evidence)
    status = result['status'] if output_evidence['complete'] else 'incomplete-generated-output'
    return report('local-validation-' + status, validation=result, workspaceOutputs=output_evidence,
        review=evidence, unresolvedReviewEvidence=evidence['blockers'],
        unsupportedSteps=derived['unsupportedSteps'], agentUnknowns=derived['agentUnknowns'],
        resourceNeeds=derived['resourceNeeds'],
        **({'acceptanceObligations': derived['acceptanceObligations'],
            'executionRefinement': derived['executionRefinement'],
            **({'reusedLocalTests': derived['reusedLocalTests']} if 'reusedLocalTests' in derived else {})
            } if c.get('refineExecutionPlan') else {}),
        resourceObservations='Catalog capabilities select a pinned attempt; inert model-requested version/presence probes are not shell-dispatched or promoted to acceptance.',
        **({'executionBackend': backend, 'executionReceipt': workflow.pin(attempt / 'result.json')} if backend == 'host' else {}),
        projectCommandsExecuted=bool((result.get('report') or {}).get('stages')), nextStage='independent-execution-and-product-acceptance-review-required')


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None, _verify_only=False):
    if _verify_only and any(x is not None for x in (authorization, key_file, transport)):
        raise Refused('Read-only validation takes no effects authority')
    config_path, state = api.canonical_path(config_path), api.canonical_path(state)
    values = inputs(config_path, config_sha, state)
    intent = values[-1]
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    new_state = not state.exists()
    if new_state:
        if _verify_only:
            raise Refused('Verification requires a completed validation continuation')
        if authorization is None:
            if key_file is not None:
                raise Refused('Ungranted preview never reads credentials')
            return report('awaiting-authorization', **grant)
        workflow.require_authorization(authorization, intent)
        with new_owned_directory(state):
            pass
        api.durable(state, 'phase-grant.json', api.encoded(grant))
    if unique_json(api.file_bytes(state / 'phase-grant.json')) != grant:
        raise Refused('Phase grant/input drift; do not recreate or upgrade authority')
    if authorization is not None:
        workflow.require_authorization(authorization, intent)
    with os.fdopen(owned_file(state, 'phase.lock', os.O_RDWR | (0 if _verify_only or not new_state else os.O_CREAT)), 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return advance(*values, state, key_file, transport, verify_only=_verify_only)


def verify(config_path, config_sha, state):
    """Read-only verification of a terminal local validation, never repair/resume."""
    state = api.canonical_path(state)
    c = unique_json(api.pinned({'path': str(config_path), 'sha256': config_sha}))
    grant = unique_json(api.file_bytes(state / 'phase-grant.json'))
    backend = grant.get('intent', {}).get('executionBackend', 'container')
    if backend not in ('container', 'host'): raise Refused('Unknown retained execution backend')
    source_files = (('review-reuse.json',) if 'sourceReviewReuse' in c else
        ('review-dispatch.json', 'review-attempt/attempt.json', 'review-attempt/result.json',
         'review-attempt/packet.json', 'review-attempt/review.json'))
    for name in ('phase-grant.json', 'phase.lock', 'review-requirement.json', 'review-plan.json',
            'review-config.json', *source_files,
            'validation-declaration.json', 'validation-composition.json', backend + '-spec.json',
            backend + '-dispatch.json', backend + '-attempt/result.json', 'validation-result.json',
            'workspace-output-evidence.json'):
        api.file_bytes(state / name)
    result = run(config_path, config_sha, state, _verify_only=True)
    if not result['status'].startswith('local-validation-'):
        raise Refused('Continuation has no completed local-validation result')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--authorize-intent')
    parser.add_argument('--key-file', type=Path)
    args = parser.parse_args(argv)
    result = run(args.config, args.config_sha256, args.state, args.authorize_intent, key_file=args.key_file)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('awaiting-authorization', 'local-validation-passed') else 1
