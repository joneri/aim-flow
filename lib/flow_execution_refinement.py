"""Opt-in source-cited finite execution scheduling; never acceptance evidence."""
import copy
import json
import flow_candidate_review as base
from aim_ci.project_discovery import unique_json
from aim_ci.owned_paths import Refused
from aim_ci.project_plan_derivation import local_command_candidate, parse_step

ROW = base.obj({'stepId': base.string(120),
    'kind': {'type': 'string', 'enum': ['finite-local', 'reuse-local-test', 'evidence-only-acceptance', 'hosted-delivery', 'blocked']},
    'evidenceSteps': base.arr(base.string(120), 64),
    'executionDependsOn': base.arr(base.string(120), 64),
    'analysis': base.string(1600), 'sources': base.REFS})
SCHEMA = base.arr(ROW, 64)
INSTRUCTIONS = '''
Explicitly refine executionRefinement for EVERY exact assessment step, once in original order.
Classify finite-local only when displayed candidate code establishes a terminating local command;
keep its exact assessed literal command. CI area does not mean hosted: inspect actual scripts.
Choose the smallest existing tests relevant to the actual changed paths and original outcomes.
Explain why each selected test is needed now; inspect nested scripts so an aggregate and its
component checks are not needlessly scheduled twice. Keep actual mandatory project/CI gates.
Use reuse-local-test only for a repeated tests-area step with the exact same literal npm run
command and workingDirectory as ONE earlier finite-local tests step in this same validation.
There may be no intervening work except aliases of that same producer. Cite the actual test
implementation and explain why one observation supports both outcomes. Keep finite-local if
independent repeated runs are required or coverage/input equivalence is uncertain. Never reuse
installation, CI, deployment, a failed/unknown attempt, another candidate/runtime or live backend
observations as if they were fresh. This option changes scheduling, not acceptance or authority.
Persistent preview/dev servers and manual inspection are NEVER finite-local. Use blocked if uncertain.
You may classify a manual/persistent acceptance step evidence-only-acceptance only if exact earlier
finite-local steps actually generate relevant observations for its obligations. List their IDs in
 evidenceSteps and cite actual test implementations, explaining coverage AND remaining limitations.
This substitutes evidence production in the runtime schedule; it does not claim the suggested manual
method was performed. Later artifact review decides whether ORIGINAL required outcomes are supported.
An assessment's chosen manual/device/assistive-technology procedure is not an extra user requirement.
Keep an unavailable nonessential method as a short not-performed qualification and continue with
source-supported finite evidence. Do not propagate it as a blocking prerequisite merely because an
earlier model proposed it. If an essential original outcome has no honest verification route, flag
that precise capability/owner-decision need early. Never invent observations or waive a real gate.
For finite-local list executionDependsOn as all original predecessors, replacing an evidence-only
predecessor with its original predecessors plus evidenceSteps recursively. Justify any changed
schedule explicitly in analysis, including why evidence may be reviewed after runtime gates.
Never bypass a required human approval, authorization, external effect, or safety prerequisite.
Use hosted-delivery only for genuine hosted CI/push/publication/deploy obligations; never local tests.
For hosted-delivery/blocked use empty evidenceSteps and executionDependsOn. For evidence-only use
empty executionDependsOn. Cite exact displayed candidate source for every classification.
If no honest finite substitute is supported by actual candidate code, classify blocked. Do not
invent commands, edits, output paths, completed observations or successful acceptance.
'''


def requested(context):
    if 'refineExecutionPlan' in context and context['refineExecutionPlan'] is not True:
        raise Refused('refineExecutionPlan must be true when present')
    return context.get('refineExecutionPlan', False)


def assessed(packet):
    try:
        return unique_json(packet['approvedPlan'])['assessment']['steps']
    except (ValueError, KeyError, TypeError):
        raise Refused('Execution refinement requires the exact assessed plan') from None


def verify_test_reuse(step, row, original, seen):
    """Allow only explicit adjacent aliases in one unchanged validation attempt."""
    producers = row['evidenceSteps']
    if step['area'] != 'tests' or len(producers) != 1 or row['executionDependsOn']:
        raise Refused('Test reuse requires one earlier local test, never installation/CI/deployment')
    key = producers[0]
    producer = seen.get(key)
    previous = original.get(key)
    if not producer or producer['kind'] != 'finite-local' or previous['area'] != 'tests':
        raise Refused('Test reuse producer must be an earlier finite local tests step')
    if (step['command'] != previous['command']
            or step.get('workingDirectory', '.') != previous.get('workingDirectory', '.')):
        raise Refused('Test reuse requires identical literal command and working directory')
    stages, _ = parse_step(step['command'], step.get('workingDirectory', '.'))
    if not stages or any(stage['operation'] != 'run' for stage in stages):
        raise Refused('Installation or unsupported commands cannot become reused tests')
    between = list(seen.values())[list(seen).index(key) + 1:]
    if any(item['kind'] != 'reuse-local-test' or item['evidenceSteps'] != [key] for item in between):
        raise Refused('Intervening work prevents local test evidence reuse')


def verify(rows, packet):
    steps = assessed(packet)
    if [r['stepId'] for r in rows] != [s['id'] for s in steps]:
        raise Refused('Execution refinement must cover every exact assessed step in order')
    seen = {}
    original = {step['id']: step for step in steps}
    for step, row in zip(steps, rows):
        for field in ('evidenceSteps', 'executionDependsOn'):
            if len(row[field]) != len(set(row[field])) or not set(row[field]) <= seen.keys():
                raise Refused('Refinement references must be distinct earlier steps')
        if row['kind'] not in ('evidence-only-acceptance', 'reuse-local-test') and row['evidenceSteps']:
            raise Refused('Only reviewed acceptance or test reuse can name evidence producers')
        if row['kind'] != 'finite-local' and row['executionDependsOn']:
            raise Refused('Only finite local steps can have runtime dependencies')
        if row['kind'] == 'reuse-local-test':
            verify_test_reuse(step, row, original, seen)
        if row['kind'] == 'evidence-only-acceptance':
            if not row['evidenceSteps'] or any(seen[x]['kind'] != 'finite-local' for x in row['evidenceSteps']):
                raise Refused('Acceptance substitution requires earlier finite evidence producers')
            if step['area'] != 'tests':
                raise Refused('Only test acceptance steps can substitute evidence; installation, CI gates and deployment remain intact')
        if row['kind'] == 'hosted-delivery' and (step['area'] not in ('ci', 'deployment')
                or local_command_candidate(step)):
            raise Refused('Local installation/tests cannot become hosted delivery')
        refs = row['sources']
        if any(not ref['path'].startswith('candidate/') for ref in refs):
            raise Refused('Execution refinement requires candidate source citations')
        fake = {'summary': 'Refinement citations', 'findings': [], 'unknowns': [], 'coverage': [
            {'requirementId': c['id'], 'status': 'covered', 'analysis': 'Source identity only',
             'sources': copy.deepcopy(refs)} for c in packet['requirement']['criteria']]}
        checked = base.parse_response({'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': json.dumps(fake)}]}]}, packet)
        row['sources'] = checked['coverage'][0]['sources']
        seen[row['stepId']] = row
    return rows


def apply(derived, steps, rows):
    """Retain obligations, replace only explicitly reviewed evidence scheduling."""
    result = copy.deepcopy(derived)
    if [r['stepId'] for r in rows] != [s['id'] for s in steps]:
        raise Refused('Refinement assessment identity differs')
    by_id = {r['stepId']: r for r in rows}
    original = {s['id']: s for s in steps}
    jobs = {j['id']: j for j in (result['declaration'] or {}).get('jobs', [])}
    if any(r['kind'] == 'blocked' for r in rows):
        raise Refused('Execution refinement contains blocked assessment obligations')
    seen = {}
    for step, row in zip(steps, rows):
        if row['kind'] == 'reuse-local-test':
            verify_test_reuse(step, row, original, seen)
            if step['id'] not in jobs or row['evidenceSteps'][0] not in jobs:
                raise Refused('Test reuse cannot hide an unsupported command adapter')
        seen[step['id']] = row

    def prerequisites(key):
        row = by_id[key]
        if row['kind'] == 'finite-local':
            return [key]
        if row['kind'] not in ('evidence-only-acceptance', 'reuse-local-test'):
            raise Refused('Hosted or blocked prerequisite cannot be skipped')
        values = []
        for dep in [*original[key]['dependsOn'], *row['evidenceSteps']]:
            for value in prerequisites(dep):
                if value not in values:
                    values.append(value)
        return values

    selected = []
    for step, row in zip(steps, rows):
        if row['kind'] != 'finite-local':
            continue
        if step['id'] not in jobs:
            raise Refused('Finite classification cannot invent an unsupported command adapter')
        expected = []
        for dep in step['dependsOn']:
            for key in prerequisites(dep):
                if key not in expected:
                    expected.append(key)
        if row['executionDependsOn'] != expected:
            raise Refused('Refinement changed or omitted a required execution predecessor')
        job = jobs[step['id']]
        job['needs'] = expected
        # The original text prerequisites and independent checks remain intact.
        selected.append(job)
    if not selected:
        raise Refused('Refinement has no finite local stages')
    result['declaration']['jobs'] = selected
    # Rebuild only typed unresolved graph needs; never remove runtime/probe needs.
    result['resourceNeeds'] = [n for n in result['resourceNeeds'] if n['kind'] != 'assessment-predecessor']
    result['executionRefinement'] = copy.deepcopy(rows)
    result['acceptanceObligations'] = [copy.deepcopy(original[r['stepId']]) for r in rows
                                     if r['kind'] in ('evidence-only-acceptance', 'reuse-local-test')]
    reused = [{'stepId': row['stepId'], 'producerStepId': row['evidenceSteps'][0],
        'status': 'planned-evidence-reuse', 'scope': 'same-candidate-and-validation-attempt',
        'qualification': 'Producer must actually pass; no earlier-run result, product acceptance or new effect authority is inferred.'}
        for row in rows if row['kind'] == 'reuse-local-test']
    if reused:
        result['reusedLocalTests'] = reused
    return result
