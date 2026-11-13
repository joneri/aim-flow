"""Source-bound npm plan suggestions. No model text is ever launched here."""
from pathlib import Path
import re

from . import control_package as package, project_onboarding as onboarding
from . import project_validation as validation
from .project_discovery import CommitReader, SCRIPT_NAME, unique_json
from .owned_paths import Refused

FORMAT = 'aim-flow-derived-project-plan'
CATALOG = 'aim-flow-resource-catalog'


def parse_commands(command, directory):
    """Accept complete literal forms only, never salvage a shell substring."""
    stages = []
    for part in command.split(' && '):
        if part == 'npm ci':
            stages.append({'root': directory, 'operation': 'ci', 'script': None})
        elif re.fullmatch('npm run ' + SCRIPT_NAME, part):
            stages.append({'root': directory, 'operation': 'run', 'script': part[8:]})
        else:
            return None
    return stages


def parse_step(command, directory):
    """Recognize inert probes as resource needs, never execute or discard them."""
    stages, probes = [], []
    for part in command.split(' && '):
        parsed = parse_commands(part, directory)
        if parsed:
            stages.extend(parsed)
        elif part in ('node --version', 'npm --version', 'git --version', 'zip -v'):
            probes.append({'tool': part.split()[0], 'observation': 'version', 'command': part})
        elif re.fullmatch(r'command -v [A-Za-z][A-Za-z0-9_-]*(?: [A-Za-z][A-Za-z0-9_-]*)*', part):
            probes.extend({'tool': name, 'observation': 'presence', 'command': part}
                          for name in part[len('command -v '):].split())
        else:
            return None, []
    return (stages, probes) if stages else (None, [])


def local_command_candidate(step):
    """Syntax/area eligibility only, never finite execution or hosted-CI proof.

    CI is an obligation category, not an execution location. Literal npm stages
    may be proposed for local source/effect review regardless of that category.
    Deployment remains outside this adapter even when expressed as npm scripts.
    """
    return (step.get('area') in ('installation', 'tests', 'ci')
            and parse_step(step.get('command', ''), step.get('workingDirectory', '.'))[0] is not None)


def delivery_obligation(step):
    """Do not defer supported local CI commands as if they were hosted receipts.

    Unsupported CI remains an external obligation; supported syntax alone does
    not establish safety, termination, acceptance, or any hosted result.
    """
    return (step.get('area') == 'deployment'
            or (step.get('area') == 'ci' and not local_command_candidate(step)))


def resource_catalog(value):
    onboarding._keys(value, ('format', 'schemaVersion', 'resources'), 'resource catalog')
    if value['format'] != CATALOG or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1:
        raise Refused('Unsupported resource catalog')
    if not isinstance(value['resources'], list):
        raise Refused('Resource catalog requires a resource list')
    seen = set()
    for row in value['resources']:
        if not isinstance(row, dict):
            raise Refused('Resource facts must be objects')
        onboarding._keys(row, ('id', 'kind', 'identity', 'requirements', 'qualification',
                               *(('executionSpec',) if 'executionSpec' in row else ()),
                               *(('capabilities',) if 'capabilities' in row else ()),
                               *(('qualificationState',) if 'qualificationState' in row else ())), 'resource fact')
        validation.identifier(row['id'])
        if row['id'] in seen or row['kind'] not in ('node-runtime', 'npm-cache', 'browser-runtime'):
            raise Refused('Resource facts need distinct identities and supported kinds')
        seen.add(row['id'])
        if row.get('qualificationState', 'unknown') not in ('unknown', 'prepared', 'qualified'):
            raise Refused('Unsupported resource qualification state')
        capabilities = row.get('capabilities', {})
        if not isinstance(capabilities, dict) or set(capabilities) - {'nodeVersion', 'npmVersion', 'platform', 'architecture', 'lockSha256', 'browserPackages', 'gitVersion', 'utilities'}:
            raise Refused('Unsupported typed resource capabilities')
        for key, capability_value in capabilities.items():
            if key in ('browserPackages', 'utilities'):
                if not isinstance(capability_value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in capability_value.items()):
                    raise Refused('Browser capabilities require package/version pairs')
            elif not isinstance(capability_value, str) or not capability_value:
                raise Refused('Resource capability must be nonempty text')
        spec = row.get('executionSpec')
        if spec is not None:
            onboarding._keys(spec, ('backend', 'path', 'sha256'), 'catalog execution specification reference')
            if (spec['backend'] not in ('container', 'local') or not isinstance(spec['path'], str)
                    or not Path(spec['path']).is_absolute() or Path(spec['path']).resolve() != Path(spec['path'])
                    or not isinstance(spec['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', spec['sha256'])):
                raise Refused('Catalog specification reference needs a canonical path and SHA-256')
        for key in ('identity', 'qualification'):
            onboarding._text(row[key], key)
        onboarding._texts(row['requirements'], 'catalog requirements')
    return value


def version_satisfies(version, constraint):
    """Small fail-closed npm-engine subset, not a general semver implementation."""
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        return False
    if not isinstance(constraint, str) or not constraint.strip():
        return False
    actual = tuple(map(int, version.split('.')))
    tokens = constraint.split()
    for token in tokens:
        match = re.fullmatch(r'(>=|<=|>|<|=|\^|~)?([0-9]+(?:\.[0-9]+){0,2})', token)
        if not match:
            return False
        operator, numeric = match.groups()
        parts = tuple(map(int, numeric.split('.')))
        lower = parts + (0,) * (3 - len(parts))
        if operator in ('>', '<=') and len(parts) < 3:
            upper = parts[:-1] + (parts[-1] + 1,) + (0,) * (3 - len(parts))
            valid = actual >= upper if operator == '>' else actual < upper
        elif operator in ('>=', '<=', '>', '<', '='):
            valid = {'>=': actual >= lower, '<=': actual <= lower, '>': actual > lower,
                     '<': actual < lower, '=': actual == lower}[operator]
        elif operator == '^':
            pivot = next((i for i, value in enumerate(lower) if value), 2)
            upper = lower[:pivot] + (lower[pivot] + 1,) + (0,) * (2 - pivot)
            valid = lower <= actual < upper
        elif operator == '~':
            upper = (lower[0] + 1, 0, 0) if len(parts) == 1 else (lower[0], lower[1] + 1, 0)
            valid = lower <= actual < upper
        else:
            valid = actual[:len(parts)] == parts
        if not valid:
            return False
    return True


def match_status(matches):
    if not matches:
        return 'missing'
    if all(row.get('qualificationState') == 'prepared' for row in matches):
        return 'catalog-candidate-qualification-required'
    return 'catalog-candidate-review-required'


def matches_need(row, kind, requirements):
    if row['kind'] != kind:
        return False
    capabilities = row.get('capabilities', {})
    if kind == 'npm-cache':
        return requirements == ['lock-sha256:' + capabilities.get('lockSha256', '')]
    if kind == 'browser-runtime':
        for requirement in requirements:
            name, _, constraint = requirement.partition(' ')
            if not version_satisfies(capabilities.get('browserPackages', {}).get(name), constraint):
                return False
        return bool(requirements)
    npm_stage = 'npm named scripts with normal lifecycle hooks' in requirements
    if npm_stage and not version_satisfies(capabilities.get('npmVersion'), '>=0'):
        return False  # Node presence alone does not supply the npm stage runner.
    constraints = [item for item in requirements if item != 'npm named scripts with normal lifecycle hooks']
    if not constraints:
        return False  # no declared engine cannot establish a compatible runtime
    for requirement in constraints:
        name, _, constraint = requirement.partition(' ')
        if name not in ('node', 'npm') or not version_satisfies(capabilities.get(name + 'Version'), constraint):
            return False
    return bool(capabilities.get('platform') and capabilities.get('architecture'))


def derive_from_instance(root, instance, instance_sha256, catalog):
    """Caller must verify the retained instance first. Recheck cited commit bytes.

    This internal API is also used after the current onboarding verifier. The CLI
    wrapper below additionally validates the pinned predecessor installation.
    """
    catalog = resource_catalog(catalog)
    onboarding.validate_assessment(instance['assessment'], instance['packet'], instance['packetSha256'])
    if instance['ready'] is not False or instance['executionAuthorized'] is not False:
        raise Refused('Derivation requires an unapproved onboarding instance')
    target, git = instance['target'], instance['packet']['git']
    if target != instance['packet']['target']:
        raise Refused('Instance and packet target differ')
    reader = CommitReader(root, Path(target['checkout']), Path(git['path']), git['sha256'], target['commit'])
    if reader.tree != target['tree']:
        raise Refused('Project tree differs from assessment')
    sources = {}

    def read(name):
        onboarding._source_path(name)
        if name not in sources:
            entries = list(reader.entries(reader.commit, name))
            if len(entries) != 1 or entries[0][0] != name or entries[0][1] not in ('100644', '100755') or entries[0][2] != 'blob':
                raise Refused('Missing committed regular definition: ' + name)
            raw = reader.blob(entries[0][3])
            try:
                text = raw.decode('utf-8')
            except UnicodeError:
                raise Refused('Definition must be UTF-8: ' + name) from None
            if '\0' in text:
                raise Refused('Definition must be text: ' + name)
            sources[name] = {'path': name, 'sha256': package.sha256(raw), 'gitObject': entries[0][3],
                             'text': text, 'lineCount': len(text.splitlines(keepends=True))}
        return sources[name]

    for source in instance['packet']['sources']:
        actual = read(source['path'])
        if actual['sha256'] != source['sha256'] or actual['text'] != source['text']:
            raise Refused('Onboarding evidence differs from committed source')

    def reference(name):
        source = read(name)
        return {'path': name, 'startLine': 1, 'endLine': source['lineCount']}

    # Assessment-selected manifests also reveal nested runtime/browser requirements.
    # These are conservative project-wide needs, not a claim about transitive reachability.
    observed = []
    for name, source in list(sources.items()):
        if name == 'package.json' or name.endswith('/package.json'):
            try:
                manifest_data = unique_json(source['text'])
            except ValueError:
                continue
            if not isinstance(manifest_data, dict):
                continue
            engines = manifest_data.get('engines', {})
            if isinstance(engines, dict) and engines:
                observed.append({'kind': 'node-runtime',
                    'requirements': [key + ' ' + str(value) for key, value in sorted(engines.items())],
                    'sources': [reference(name)]})
            for group in ('dependencies', 'devDependencies', 'optionalDependencies'):
                dependencies = manifest_data.get(group, {})
                if isinstance(dependencies, dict):
                    for dependency in ('playwright', '@playwright/test', 'puppeteer'):
                        if dependency in dependencies:
                            observed.append({'kind': 'browser-runtime',
                                'requirements': [dependency + ' ' + str(dependencies[dependency])],
                                'sources': [reference(name)]})
    roots, jobs, runtimes, needs, unsupported, selected = {}, [], [], [], [], set()
    for need in observed:
        matches = [row for row in catalog['resources'] if matches_need(row, need['kind'], need['requirements'])]
        needs.append({**need, 'step': None,
            'status': match_status(matches),
            'candidates': matches, 'scope': 'assessment-selected manifest; reachability and compatibility require review'})
    for step in instance['assessment']['steps']:
        stages, probes = parse_step(step['command'], step['workingDirectory'])
        reason = None
        if step['area'] not in ('installation', 'tests', 'ci'):
            reason = 'Non-local-validation area requires its own reviewed adapter.'
        elif stages is None:
            reason = 'Unsupported complete command; only literal npm stages and recognized version/presence probes joined by spaced && are supported.'
        if reason:
            unsupported.append({'step': step['id'], 'command': step['command'], 'reason': reason,
                                'sources': step['sources'], 'prerequisites': step['prerequisites']})
            continue
        for probe in probes:
            candidates = []
            for row in catalog['resources']:
                capabilities = row.get('capabilities', {})
                if row['kind'] == 'node-runtime' and (
                    capabilities.get(probe['tool'] + 'Version') or capabilities.get('utilities', {}).get(probe['tool'])):
                    candidates.append(row)
            needs.append({'step': step['id'], 'kind': 'runtime-probe', 'requirements': [probe],
                'status': match_status(candidates),
                'candidates': candidates, 'sources': step['sources']})
        directory = step['workingDirectory']
        prefix = '' if directory == '.' else directory + '/'
        manifest = prefix + 'package.json'
        try:
            data = unique_json(read(manifest)['text'])
            if not isinstance(data, dict) or not isinstance(data.get('scripts', {}), dict):
                raise Refused('Invalid npm package object')
            scripts = data.get('scripts', {})
            if any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in scripts.items()):
                raise Refused('Invalid committed npm scripts')
            for stage in stages:
                if stage['operation'] == 'run' and stage['script'] not in scripts:
                    raise Refused('Named script is absent from committed manifest: ' + stage['script'])
            lock = None
            for candidate in ('npm-shrinkwrap.json', 'package-lock.json'):
                name = prefix + candidate
                if list(reader.entries(reader.commit, name)):
                    if not isinstance(unique_json(read(name)['text']), dict):
                        raise Refused('Invalid npm lock object')
                    lock = name
                    break
            if lock is None:
                raise Refused('Missing npm lockfile for ' + directory)
        except (Refused, ValueError) as error:
            unsupported.append({'step': step['id'], 'command': step['command'], 'reason': str(error),
                                'sources': step['sources'], 'prerequisites': step['prerequisites']})
            continue
        roots[directory] = {'directory': directory, 'lockfile': lock}
        selected.update((manifest, lock))
        refs = [{key: ref[key] for key in ('path', 'startLine', 'endLine')} for ref in step['sources']]
        refs.append(reference(manifest))
        selected.update(ref['path'] for ref in refs)
        runtime = 'runtime-' + step['id']
        engines = data.get('engines', {})
        requirements = ['npm named scripts with normal lifecycle hooks']
        if isinstance(engines, dict):
            requirements += [key + ' ' + str(value) for key, value in sorted(engines.items())]
        runtimes.append({'id': runtime, 'requirements': requirements, 'sources': [reference(manifest)]})
        for kind, wanted in [('node-runtime', requirements), ('npm-cache', ['lock-sha256:' + read(lock)['sha256']])]:
            matches = [row for row in catalog['resources'] if matches_need(row, kind, wanted)]
            needs.append({'step': step['id'], 'kind': kind, 'requirements': wanted,
                          'status': match_status(matches),
                          'candidates': matches, 'sources': [reference(manifest if kind == 'node-runtime' else lock)]})
        unmet = [dep for dep in step['dependsOn'] if dep not in {job['id'] for job in jobs}]
        # Preserve the model graph; never erase unsupported prerequisites to make a job runnable.
        prerequisite = [*step['prerequisites'], *step['independentChecks'],
                        'Independent source/effect/runtime review and exact execution intent authorization are required.',
                        'Review complete transitive inputs, dependency hooks, nested commands, writable outputs and containment.']
        prerequisite += ['Unresolved assessment predecessor: ' + dep for dep in unmet]
        for dep in unmet:
            needs.append({'step': step['id'], 'kind': 'assessment-predecessor', 'requirements': [dep],
                          'status': 'unresolved', 'candidates': [], 'sources': refs})
        # Do not invent an install or collapse independently ordered model steps.
        jobs.append({'id': step['id'], 'mode': 'local-sequence', 'runtime': runtime,
                     'needs': [dep for dep in step['dependsOn'] if dep not in unmet], 'stages': stages,
                     'coverage': step['purpose'], 'effects': step['effects'], 'prerequisites': prerequisite,
                     'sources': refs})
    reader.verify_head()
    for row in unsupported:
        needs.append({'step': row['step'], 'kind': 'command-adapter', 'requirements': [row['reason'], *row['prerequisites']],
                      'status': 'unsupported', 'candidates': [], 'sources': row['sources']})
    open_items = [*instance['assessment']['unknowns'],
                  'Generated plan is untrusted and awaits independent review; no execution or provisioning occurred.']
    open_items += ['Unsupported assessment step ' + row['step'] + ': ' + row['reason'] for row in unsupported]
    declaration = {'format': validation.DECLARATION, 'schemaVersion': 1, 'instanceSha256': instance_sha256,
                   'target': target, 'review': {'reference': 'generated-unreviewed',
                   'qualifications': ['Literal command syntax and committed script existence only; not safety, semantic correctness or authority.'],
                   'openItems': open_items}, 'sourcePaths': sorted(selected), 'packageRoots': list(roots.values()),
                   'runtimes': runtimes, 'jobs': jobs,
                   'excludedCoverage': ['Hosted CI, browsers, native platforms, publication, deployment and product acceptance are not established.']}
    return {'format': FORMAT, 'schemaVersion': 1, 'status': 'awaiting-independent-plan-review',
            'ready': False, 'executionAuthorized': False, 'instanceSha256': instance_sha256,
            'target': target, 'declaration': declaration if jobs else None, 'resourceNeeds': needs,
            'unsupportedSteps': unsupported, 'agentUnknowns': instance['assessment']['unknowns'],
            'resourceCatalog': catalog,
            'qualification': 'Catalog entries are configured capability claims, not provisioned or independently requalified resources. Nested script/resource requirements remain unresolved.'}


def derive(root, args):
    package.verify_installation(root, args.manifest_sha256)
    instance = validation.verified_instance(root, args)
    raw, catalog = validation.pinned(args.resource_catalog, args.resource_catalog_sha256)
    value = derive_from_instance(root, instance, args.instance_sha256, catalog)
    package.verify_installation(root, args.manifest_sha256)
    validation.verified_instance(root, args)
    if validation.pinned(args.resource_catalog, args.resource_catalog_sha256)[0] != raw:
        raise Refused('Resource catalog changed during derivation')
    return value


def planning(root, args):
    value = derive(root, args)
    onboarding._outside(args.output, root, Path(value['target']['checkout']))
    onboarding._outside(args.output, args.instance_controller, Path(value['target']['checkout']))
    return onboarding._write(args.output, value, root, Path(value['target']['checkout']))
