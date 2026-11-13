"""Combined independent candidate review and narrowly typed archive input policy.

A source-cited proposal may exclude unchanged generated archives from ephemeral
execution inputs. It cannot hide changed source or authorize any execution.
"""
import copy
import io
import gzip
import tarfile
import zipfile
import zlib
import json
from pathlib import Path

import flow_api_delivery as api
import flow_candidate_review as base
import flow_execution_refinement as refinement
import flow_review_execution_context as execution_context
import flow_onboarding_delivery as compact
from flow_review_dispatch import dispatch
from aim_ci import project_onboarding as onboarding
from aim_ci.project_discovery import CommitReader, unique_json
from aim_ci.owned_paths import Refused

PURPOSE = 'candidate-workspace-review'
FORMAT = 'aim-flow-candidate-workspace-review'
ARCHIVES = ('.zip', '.tar', '.tar.gz', '.tgz')
OUTPUT = base.obj({'path': base.string(1100), 'reason': base.string(1600),
    'generatorSources': base.REFS, 'lifecycleSources': base.REFS})
SCHEMA = base.obj({**base.SCHEMA['properties'],
    'workspacePolicy': base.obj({'generatedOutputs': base.arr(OUTPUT, 64),
        'retainedArchiveInputs': base.arr(base.obj({'path': base.string(1100), 'reason': base.string(1600)}), 64),
                                'unknowns': base.arr(base.string(1600), 32)})})
INSTRUCTIONS = base.INSTRUCTIONS + (
    '\nAlso independently determine the execution input policy for every listed unchanged archive. '
    'Only omit an archive when actual displayed generator code writes that exact path and displayed '
    'npm lifecycle/script source makes regeneration happen before any consumer in the proposed serial stages. '
    'Cite both generator implementation and lifecycle ordering, not comments or labels asserting generated status. '
    'Do not omit changed files, source/config/executable definitions or required stage inputs. '
    'Existing archives may be fixtures or required input: do not guess from extension. '
    'Each archive must either be declared a generatedOutput with exact generatorSources and lifecycleSources '
    'or explicitly retainedArchiveInputs with a reason to preserve it as an input, or have a workspacePolicy unknown explaining why classification is unresolved. '
    'This policy supports unchanged generated archives only; other source mutation needs are unsupported. '
    'Source-only coverage is not executed acceptance and the policy never authorizes execution.'
)


ARTIFACT = base.obj({'path': base.string(1100),
    'mediaType': {'type': 'string', 'enum': ['image/png', 'application/json']},
    'reason': base.string(1600), 'sources': base.REFS})
CAPTURE_SCHEMA = base.obj({**SCHEMA['properties'], 'artifactSelection': base.arr(ARTIFACT, 8)})
def capture_limits(output_bytes):
    """Reserve half the granted report for logs/inventory, then account for base64.

    This is a transport/resource bound, not an arbitrary eight-MiB project limit.
    The backend enforces the same formula before executing any command.
    """
    if type(output_bytes) is not int or output_bytes < 32776:
        raise Refused('Artifact capture needs report headroom')
    maximum = min(8 * 4194304, ((output_bytes // 2 - 16384) // 4) * 3)
    return {'maxFiles': 8, 'maxFileBytes': min(4194304, maximum), 'maxTotalBytes': maximum}


CAPTURE_LIMITS = capture_limits(32_000_000)
CAPTURE_INSTRUCTIONS = (
    '\nSelect artifactSelection only from actual displayed candidate test/generator implementation. '
    'Choose up to 8 exact repository-relative PNG or JSON output files written by the proposed stages, '
    'with reason and precise candidate code citations showing the output path and generation. '
    'Resolve template literals, joined paths and output-directory variables from displayed code and stage roots; '
    'cite the relevant definitions as well as the write/screenshot call. Do not evaluate code. '
    'If environment-dependent or computed paths cannot be resolved credibly, leave selection empty. '
    'Paths must not already be committed files, source inputs, caches, node_modules, .git or secrets. '
    'Do not invent a filename or infer generation from documentation. If no credible selection exists, '
    'return an empty list and explain the missing evidence in unknowns. '
    'Collection uses the explicit artifactCaptureLimits derived from granted report headroom, at most 4 MiB per file; source citations do not prove actual '
    'screenshot contents or executed acceptance. Artifact bytes stay local until a separate acceptance-review grant.'
)


def review_instructions(refine, capture, context, citation_protocol=None):
    instructions = INSTRUCTIONS
    if context is not None:
        instructions = instructions.replace(
            'This policy supports unchanged generated archives only; other source mutation needs are unsupported. ',
            'This exclusion policy supports unchanged generated archives only; private-copy mutation and restoration follow executionContext. ')
    if refine:
        finite = refinement.INSTRUCTIONS
        if context is not None:
            finite = finite.replace(
                'Classify finite-local only when displayed candidate code establishes a terminating local command;',
                'Classify finite-local only for a source-supported finite command shape with an exact local adapter;')
            finite = finite.replace(
                'For finite-local list executionDependsOn as all original predecessors, replacing an evidence-only\n'
                'predecessor with its original predecessors plus evidenceSteps recursively.',
                'For finite-local retain DIRECT finite predecessors in executionDependsOn, recursively replacing only\n'
                'evidence-only predecessors with their original predecessors plus evidenceSteps.')
            finite = finite.replace(
                'Use hosted-delivery only for genuine hosted CI/push/publication/deploy obligations; never local tests.',
                'Use hosted-delivery for external CI/push/publication/deployment obligations, including operator-only deployment preparation; never local tests.')
        instructions += finite
    if capture:
        instructions += CAPTURE_INSTRUCTIONS + (
            ' UnchangedSources lists files with identical mode and Git blob at both pinned revisions. '
            'Their complete text is displayed once under candidate/. Cite only displayed paths; baseline/ aliases are absent. ')
    if context is not None:
        instructions += execution_context.INSTRUCTIONS
    if citation_protocol is not None:
        if citation_protocol != base.CITATION_PROTOCOL:
            raise Refused('Unsupported review instruction citation protocol')
        instructions += base.COUNT_CITATION_INSTRUCTIONS
    return instructions


def capture_requested(context):
    if not isinstance(context, dict):
        raise Refused('Invalid artifact capture configuration')
    if 'captureArtifacts' in context and context['captureArtifacts'] is not True:
        raise Refused('captureArtifacts must be true when present')
    return context.get('captureArtifacts', False)


def artifact_capture(selection, limits=None):
    """Deterministic backend declaration; never use a template filename list."""
    if not selection or len(selection) > 8:
        raise Refused('Requested artifact capture requires 1–8 reviewed output selections')
    return {'schemaVersion': 1, 'files': [{'path': x['path'], 'mediaType': x['mediaType']}
            for x in selection], **(CAPTURE_LIMITS if limits is None else limits)}


def validate_artifact_path(path, media_type, committed):
    onboarding._source_path(path)
    parts = path.lower().split('/')
    if (any(path == source or path.startswith(source + '/') for source in committed) or any(part in
            ('node_modules', 'npm-cache', 'npm_cache', 'cache', 'caches', 'secrets', 'credentials',
             '.git', '.hg', '.svn', '.ssh', '.npm', '.cache', '.config', '.local', '.aws', '.azure', '.kube',
             '.gcloud', '.pki', '.yarn', '.pnpm-store', '.venv', 'venv',
             '.docker', '.gnupg', '.credentials', '.secrets', '.netrc', '.npmrc', '.pypirc',
             'secrets.json', 'credentials.json', 'auth.json', 'token.json', 'tokens.json',
             'service-account.json', 'service_account.json', 'id_rsa', 'id_ed25519')
            or part.startswith(('.env', '.git.', 'credentials.', 'credentials-', 'secrets.', 'secrets-'))
            for part in parts) or any(ch in path for ch in '*?[]{}\n\r\t')
            or media_type not in ('image/png', 'application/json')
            or not path.endswith('.png' if media_type == 'image/png' else '.json')):
        raise Refused('Artifact must be an exact uncommitted PNG/JSON output outside source/cache/secret paths')


def verify_artifact_selection(selection, packet):
    context = packet['workspaceContext']
    names = [x['path'] for x in selection]
    if len(names) != len(set(names)):
        raise Refused('Duplicate artifact output selection')
    for output in selection:
        validate_artifact_path(output['path'], output['mediaType'], context['committedPaths'])
        refs = output['sources']
        if any(not ref['path'].startswith('candidate/') or
               ref['path'][10:] not in context['protectedPaths'] or
               not ref['path'].endswith(('.js', '.mjs', '.cjs', '.py', '.sh', '.ts', '.tsx')) for ref in refs):
            raise Refused('Artifact selection requires displayed candidate test/generator implementation')
        fake = {'summary': 'Artifact citation verification', 'findings': [],
            'coverage': [{'requirementId': x['id'], 'status': 'covered', 'analysis': 'Output generation evidence',
                         'sources': copy.deepcopy(refs)} for x in packet['requirement']['criteria']], 'unknowns': []}
        checked = base.parse_response({'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': json.dumps(fake)}]}]}, packet)
        output['sources'] = checked['coverage'][0]['sources']
        # Citations prove the selected code/line identity, not JS path semantics.
        # Output path derivation is the independent model's source assessment,
        # as for generated archives. The backend must still observe the exact
        # selected regular file and validate its bytes; no glob/directory export.
    return selection


def artifact_code_schema(schema, packet):
    """Constrain new provider selections to exactly the locally admissible code paths."""
    if not base.evidence_policy(packet.get('reviewEvidencePolicy')) or 'artifactSelection' not in schema['properties']:
        return schema
    result = copy.deepcopy(schema)
    protected = set(packet['workspaceContext']['protectedPaths'])
    paths = sorted(s['path'] for s in packet['sources'] if s['path'].startswith('candidate/')
        and s['path'][10:] in protected and s['path'].endswith(('.js', '.mjs', '.cjs', '.py', '.sh', '.ts', '.tsx')))
    selection = result['properties']['artifactSelection']
    if not paths:
        selection['maxItems'] = 0
    else:
        refs = selection['items']['properties']['sources']
        refs['minItems'] = 1
        refs['items']['properties']['path']['enum'] = paths
    return result


def recognized_archive(path, raw):
    """Bounded archive validation without extraction or trusting suffix/magic."""
    maximum, entries = 64 * 1024 * 1024, 10000
    if len(raw) > maximum:
        return False
    try:
        if path.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if len(members) > entries or sum(m.file_size for m in members) > maximum:
                    return False
                return archive.testzip() is None
        if path.endswith(('.tar.gz', '.tgz')):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                raw = compressed.read(maximum + 1)
            if len(raw) > maximum:
                return False
        if len(raw) < 1024 or len(raw) % 512 or raw[-1024:] != bytes(1024):
            return False
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            total = count = 0
            for member in archive:
                count += 1; total += member.size
                if count > entries or total > maximum or member.offset_data + member.size > len(raw):
                    return False
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        if len(stream.read(member.size + 1)) != member.size:
                            return False
        return True
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error):
        return False


def transform(c, packet):
    policy = c['workspaceContext']
    capture = capture_requested(policy)
    refine = refinement.requested(policy)
    onboarding._keys(policy, ('protectedPaths', 'stages', *(('captureArtifacts',) if capture else ()),
                            *(('refineExecutionPlan',) if refine else ()),
                            *(('executionContext',) if 'executionContext' in policy else ()),
                            *(('sourceByteLimit',) if 'sourceByteLimit' in policy else ()),
                            *(('reviewEvidencePolicy',) if 'reviewEvidencePolicy' in policy else ())), 'workspace policy context')
    if base.evidence_policy(policy.get('reviewEvidencePolicy')) and not (capture and refine):
        raise Refused('Review evidence policy requires captured refined review')
    if 'sourceByteLimit' in policy:base.source_byte_limit(policy['sourceByteLimit'])
    if 'executionContext' in policy:
        if not refine:
            raise Refused('Execution context requires explicit execution refinement')
        execution_context.validate(policy['executionContext'], {**packet, 'workspaceContext': policy})
    onboarding._texts(policy['protectedPaths'], 'protected source paths')
    for path in policy['protectedPaths']:
        onboarding._source_path(path)
    reader = CommitReader(base.ROOT, Path(c['source']['checkout']), Path(c['source']['git']),
                          c['source']['gitSha256'], c['source']['candidateCommit'])
    changed = {x['path'] for x in packet['changes']}
    protected = set(policy['protectedPaths']) | changed
    archives = []
    committed = []
    for path, mode, kind, oid in reader.entries(reader.commit, recursive=True):
        onboarding._source_path(path)
        committed.append(path)
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise Refused('Workspace inputs must be regular committed files')
        if path.endswith(ARCHIVES):
            if path in protected or mode != '100644':
                raise Refused('Archive input policy cannot exclude changed/protected/executable paths: ' + path)
            raw = reader.blob(oid)
            if not recognized_archive(path, raw):
                raise Refused('Archive policy requires bounded valid archive bytes, not corrupt or executable/text lookalikes: ' + path)
            archives.append({'path': path, 'gitObject': oid, 'sha256': api.digest(raw), 'mode': mode})
    reader.verify_head()
    context = {**policy, 'archiveCandidates': archives}
    if capture:
        context.update(committedPaths=sorted(committed), artifactCaptureLimits=capture_limits(
            policy.get('executionContext', {}).get('limits', {}).get('outputBytes', 32_000_000)))
    return {**packet, 'workspaceContext': context}


def prepare(config_path, config_sha, state, *, controller_root=None):
    config = unique_json(api.pinned({'path': str(config_path), 'sha256': config_sha}))
    capture = capture_requested(config.get('workspaceContext', {}))
    refine = refinement.requested(config.get('workspaceContext', {}))
    schema = CAPTURE_SCHEMA if capture else SCHEMA
    if refine:
        schema = base.obj({**schema['properties'], 'executionRefinement': refinement.SCHEMA})
        schema = base.citation_schema(schema, base.CITATION_PROTOCOL)
    policy = config.get('workspaceContext', {}).get('reviewEvidencePolicy')
    schema = base.coverage_schema(schema, policy)
    return base.prepare(config_path, config_sha, state, purpose=PURPOSE, schema=schema,
        instructions=review_instructions(refine, capture, config.get('workspaceContext', {}).get('executionContext'),
            citation_protocol=base.CITATION_PROTOCOL if refine else None) +
            (base.POLICY_INSTRUCTIONS + (
                ' Every artifactSelection.sources entry must name displayed protected candidate test/generator CODE. '
                'Never cite package.json, a lockfile, CSS, documentation or a lifecycle manifest in artifactSelection.sources. '
                'Put npm invocation/lifecycle references in executionRefinement instead. '
                'The artifact citation path enum is exact; source identity still does not prove generation or acceptance.'
                if capture else '') if base.evidence_policy(policy) else ''),
        extra_keys=('workspaceContext',), packet_transform=transform,
        extra_implementations=('flow_workspace_review.py', *(('flow_execution_refinement.py', 'flow_command_source_context.py', 'flow_requirement_intake.py') if refine else ()),
            *(('flow_review_execution_context.py',) if 'executionContext' in config.get('workspaceContext', {}) else ())),
        schema_name='candidate_workspace_refinement_v2' if refine else 'candidate_workspace_review_v2' if capture else 'candidate_workspace_review_v1',
        deduplicate_unchanged=capture,
        citation_protocol=base.CITATION_PROTOCOL if refine else None,
        command_source_stages=config['workspaceContext']['stages'] if refine else None,
        review_evidence_policy=policy, schema_transform=artifact_code_schema, controller_root=controller_root,
        max_source_bytes=config.get('workspaceContext', {}).get('sourceByteLimit', 1048576))


def parse_response(response, packet):
    # Reuse strict source-review parsing and quote enrichment, not a second
    # provider. Additional references use the packet's explicit citation protocol.
    value = copy.deepcopy(response)
    contents = [part for item in value.get('output', []) if item.get('type') == 'message'
                for part in item.get('content', []) if part.get('type') == 'output_text']
    if len(contents) != 1:
        return base.parse_response(response, packet)  # handles refusal/invalid protocol
    capture = capture_requested(packet['workspaceContext'])
    refine = refinement.requested(packet['workspaceContext'])
    protocol = base.citation_protocol(packet)
    if protocol is not None and not refine:
        raise Refused('Bounded citation protocol requires opt-in execution refinement')
    schema = CAPTURE_SCHEMA if capture else SCHEMA
    if refine:
        schema = base.obj({**schema['properties'], 'executionRefinement': refinement.SCHEMA})
    schema = base.coverage_schema(schema, packet.get('reviewEvidencePolicy'))
    schema = base.finding_schema(schema, packet.get('findingDispositionPolicy'))
    schema = base.citation_schema(schema, protocol)
    schema = artifact_code_schema(schema, packet)
    body = unique_json(contents[0]['text']); compact.validate_shape(body, schema)
    execution_rows = body.pop('executionRefinement') if refine else None
    selection = body.pop('artifactSelection') if capture else None
    policy = body.pop('workspacePolicy')
    contents[0]['text'] = json.dumps(body)
    result = base.parse_response(value, packet)
    candidates = {x['path']: x for x in packet['workspaceContext']['archiveCandidates']}
    names = [x['path'] for x in policy['generatedOutputs']]
    if len(names) != len(set(names)) or not set(names) <= candidates.keys():
        raise Refused('Workspace policy excludes an ineligible, changed or duplicate file')
    retained_names = [x['path'] for x in policy['retainedArchiveInputs']]
    if (len(retained_names) != len(set(retained_names)) or not set(retained_names) <= candidates.keys()
            or set(retained_names) & set(names)):
        raise Refused('Retained archive input classification is duplicate or ineligible')
    for output in policy['generatedOutputs']:
        for field in ('generatorSources', 'lifecycleSources'):
            refs = output[field]
            if any(not ref['path'].startswith('candidate/') or ref['path'][10:] not in packet['workspaceContext']['protectedPaths'] for ref in refs):
                raise Refused('Output policy requires selected protected candidate definition evidence')
            # Require concrete code references and preserve line/hash/quote checks.
            fake = {'summary': 'Policy citation verification', 'findings': [],
                'coverage': [{'requirementId': x['id'], 'status': 'covered', 'analysis': 'Policy evidence',
                              'sources': copy.deepcopy(refs)} for x in packet['requirement']['criteria']], 'unknowns': []}
            checked = base.parse_response({'output': [{'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': json.dumps(fake)}]}]}, packet)
            output[field] = checked['coverage'][0]['sources']
            if field == 'generatorSources' and any(not ref['path'].endswith(('.js', '.mjs', '.cjs', '.py', '.sh', '.ts')) for ref in refs):
                raise Refused('Generated output needs actual generator code, not labels or documentation')
            manifests = {'candidate/' + ('' if stage['root'] == '.' else stage['root'] + '/') + 'package.json'
                         for stage in packet['workspaceContext']['stages']}
            if field == 'lifecycleSources' and any(ref['path'] not in manifests for ref in refs):
                raise Refused('Generated output needs selected stage lifecycle manifest evidence')
        output['sourceIdentity'] = candidates[output['path']]
    unresolved = sorted(set(candidates) - set(names) - set(retained_names))
    if unresolved:
        policy['unknowns'].append('Unclassified committed archive inputs: ' + ', '.join(unresolved))
    if capture:
        result['artifactSelection'] = verify_artifact_selection(selection, packet)
        if not selection:
            result['blockers'].append('Requested artifact capture has no credible source-cited output selection')
    if refine:
        result['executionRefinement'] = refinement.verify(execution_rows, packet)
    result.update(format=FORMAT, workspacePolicy=policy)
    result['blockers'] += policy['unknowns']
    return result


def verify_evidence(review_pin, packet_pin):
    packet = unique_json(api.pinned(packet_pin)); retained = unique_json(api.pinned(review_pin))
    body = {k: copy.deepcopy(retained[k]) for k in base.SCHEMA['properties']}
    for item in body['findings'] + body['coverage']:
        item['sources'] = [base.retained_reference(ref, packet) for ref in item['sources']]
    policy = copy.deepcopy(retained['workspacePolicy'])
    # Automatic unresolved-archive item is reconstructed, not provider evidence.
    unresolved = sorted({x['path'] for x in packet['workspaceContext']['archiveCandidates']} -
                        {x['path'] for x in policy['generatedOutputs']} - {x['path'] for x in policy['retainedArchiveInputs']})
    if unresolved:
        expected = 'Unclassified committed archive inputs: ' + ', '.join(unresolved)
        if not policy['unknowns'] or policy['unknowns'].pop() != expected:
            raise Refused('Workspace unresolved inputs changed')
    for output in policy['generatedOutputs']:
        del output['sourceIdentity']
        for field in ('generatorSources', 'lifecycleSources'):
            output[field] = [base.retained_reference(ref, packet) for ref in output[field]]
    body['workspacePolicy'] = policy
    if capture_requested(packet['workspaceContext']):
        body['artifactSelection'] = copy.deepcopy(retained['artifactSelection'])
        for output in body['artifactSelection']:
            output['sources'] = [base.retained_reference(ref, packet) for ref in output['sources']]
    if refinement.requested(packet['workspaceContext']):
        body['executionRefinement'] = copy.deepcopy(retained['executionRefinement'])
        for row in body['executionRefinement']:
            row['sources'] = [base.retained_reference(ref, packet) for ref in row['sources']]
    actual = parse_response({'output': [{'type': 'message', 'role': 'assistant',
        'content': [{'type': 'output_text', 'text': json.dumps(body)}]}]}, packet)
    if actual != retained:
        raise Refused('Workspace review differs from deterministic evidence reconstruction')
    return actual


_LEGACY_SOURCE_IDENTITY_CHECK = (
    "    assert(row.sha256 === e.sha256 && row.bytes === e.bytes && row.mode === (parseInt(e.mode, 8) & 0o777), 'Copied source not restored'); result[name] = row;\n")
_DIAGNOSTIC_SOURCE_IDENTITY_CHECK = (
    "    assert(row.sha256 === e.sha256 && row.bytes === e.bytes && row.mode === (parseInt(e.mode, 8) & 0o777),\n"
    "      'Copied source not restored: ' + JSON.stringify({ path: name,\n"
    "        expected: { sha256: e.sha256, bytes: e.bytes, mode: parseInt(e.mode, 8) & 0o777 }, actual: row }));\n"
    "    result[name] = row;\n")


def diagnostic_runner_compatibility(old_packet, new_packet, old_payload, new_payload, old_controller):
    """Recognize only the audited identity-error diagnostic patch, byte for byte."""
    try:
        old_binding = old_packet['workspaceContext']['executionContext']['bindings']
        new_binding = new_packet['workspaceContext']['executionContext']['bindings']
        old_sha, new_sha = old_binding['runnerSha256'], new_binding['runnerSha256']
        path = Path('lib/aim_ci/project_container_runner.cjs')
        old_raw = api.file_bytes(old_controller / path)
        new_raw = api.file_bytes(base.ROOT / path)
        before = _LEGACY_SOURCE_IDENTITY_CHECK.encode()
        after = _DIAGNOSTIC_SOURCE_IDENTITY_CHECK.encode()
        if (old_sha == new_sha or api.digest(old_raw) != old_sha or api.digest(new_raw) != new_sha
                or old_raw.count(before) != 1 or new_raw.count(after) != 1
                or new_raw.replace(after, before, 1) != old_raw):
            raise Refused('Runner upgrade is not the exact qualified source-identity diagnostic patch')
        packet = copy.deepcopy(new_packet)
        packet['workspaceContext']['executionContext']['bindings']['runnerSha256'] = old_sha
        payload = copy.deepcopy(new_payload); payload['input'] = json.dumps(packet, sort_keys=True)
        if packet != old_packet or payload != old_payload:
            raise Refused('Diagnostic runner compatibility cannot change any other disclosure or contract')
    except (KeyError, TypeError) as error:
        raise Refused('Diagnostic runner compatibility requires complete bound execution context') from error
    return {'format': 'aim-flow-qualified-diagnostic-runner-compatibility', 'schemaVersion': 1,
        'change': 'source-identity-error-metadata-only', 'oldRunnerSha256': old_sha, 'newRunnerSha256': new_sha,
        'qualification': 'Entire runner bytes differ only by the exact qualified fail-closed identity diagnostic. '
            'Original review remains bound to its original runner; new runner is separately pinned and measured. '
            'All project source, requirements, policy, environment, limits and execution contract are identical.'}


def reuse_completed(spec, prepared):
    """Rebuild identical disclosure and reparse its completed paid response; no dispatch."""
    onboarding._keys(spec, ('controller', 'config', 'state',
        *(('allowDiagnosticRunnerUpgrade',) if 'allowDiagnosticRunnerUpgrade' in spec else ())), 'retained source review')
    if 'allowDiagnosticRunnerUpgrade' in spec and spec['allowDiagnosticRunnerUpgrade'] is not True:
        raise Refused('Diagnostic runner upgrade must be explicitly true in the new grant')
    onboarding._keys(spec['controller'], ('path', 'manifestSha256'), 'retained review controller')
    state = api.canonical_path(spec['state'])
    controller = api.canonical_path(spec['controller']['path'])
    c, raw, packet, payload, intent = prepare(Path(spec['config']['path']),
        spec['config']['sha256'], state, controller_root=controller)
    if c['manifestSha256'] != spec['controller']['manifestSha256']:
        raise Refused('Retained source review controller differs')
    compatibility = None
    if packet != prepared[2] or payload != prepared[3]:
        if not spec.get('allowDiagnosticRunnerUpgrade'):
            raise Refused('Retained source review disclosure or request differs; fresh review required')
        compatibility = diagnostic_runner_compatibility(packet, prepared[2], payload, prepared[3], controller)
    attempt = unique_json(api.file_bytes(state / 'attempt.json'))
    if (attempt.get('intent') != intent or attempt.get('intentSha256') != api.digest(api.encoded(intent))
            or api.file_bytes(state / 'config.json') != raw
            or api.file_bytes(state / 'packet.json') != api.encoded(packet)
            or api.file_bytes(state / 'request.json') != api.encoded(payload)):
        raise Refused('Retained source review dispatch changed')
    result = api.status(state)
    response = unique_json(api.file_bytes(state / 'response.json'))
    reservation = unique_json(api.file_bytes(state / 'reservation.json'))
    row = result.get('reservation')
    if (result.get('status') not in ('source-review-ready', 'review-blocked')
            or not isinstance(row, dict) or row.get('status') != 'completed'
            or row not in api.read_existing_campaign(c)['requests']
            or row.get('id') != attempt['intentSha256'] or row.get('state') != str(state)
            or row.get('requestSha256') != api.digest(api.encoded(payload))
            or reservation.get('status') != 'reserved'
            or any(row.get(k) != v for k, v in reservation.items() if k != 'status')
            or response.get('status') != 'completed' or response.get('model') != api.MODEL
            or response.get('service_tier', 'default') != 'default'
            or row.get('responseId') != response.get('id') or row.get('responseStatus') != response.get('status')
            or row.get('modelReturned') != response.get('model') or row.get('usage') != response.get('usage')):
        raise Refused('Retained source review lacks exact completed paid accounting')
    evidence = parse_response(response, packet)
    if (evidence is None or evidence != unique_json(api.file_bytes(state / 'review.json'))
            or result.get('review') != evidence):
        raise Refused('Retained source review differs from its actual provider response')
    pins = {name: {'path': str(state / name), 'sha256': api.digest(api.file_bytes(state / name))}
            for name in ('attempt.json', 'config.json', 'packet.json', 'request.json',
                         'reservation.json', 'response.json', 'result.json', 'review.json', 'token-count.json')}
    return evidence, {'format': 'aim-flow-identical-source-review-reuse', 'schemaVersion': 1,
        'source': spec, 'evidence': pins, 'requestSha256': api.digest(api.encoded(payload)),
        'packetSha256': api.digest(api.encoded(packet)), 'newProviderCalls': 0,
        **({'diagnosticRunnerCompatibility': compatibility,
            'newRequestedPacketSha256': api.digest(api.encoded(prepared[2])),
            'newRequestedRequestSha256': api.digest(api.encoded(prepared[3]))} if compatibility else {}),
        'qualification': ('Original paid source review retained with explicit qualified diagnostics-only runner compatibility; execution and acceptance remain pending.'
            if compatibility else 'Same complete disclosure and parsed paid source review; execution and acceptance remain pending.')}


def run(config_path, config_sha, state, authorization=None, *, key_file=None, transport=None):
    return dispatch(prepare, parse_response, config_path, config_sha, state, authorization,
                    key_file=key_file, transport=transport)
