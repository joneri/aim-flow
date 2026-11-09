"""Version-bound local technical review; never publication or product acceptance."""
import difflib
import hashlib
import json
from pathlib import Path

from openai_budget import Refused, atomic_json, fingerprint, owned_mkdir, read_owned, write_owned
from .contracts import review_decision


def policy(config):
    value = config.get('reviewPolicy', {'mode': 'api'})
    if value == {'mode': 'api'}:
        return value
    if (not isinstance(value, dict) or set(value) != {'mode', 'reviewer'}
            or value['mode'] != 'human-local' or not isinstance(value['reviewer'], str)
            or not value['reviewer'].strip() or len(value['reviewer']) > 100):
        raise Refused('Unsupported technical review policy')
    return value


def pause(directory, state, config, candidate, *, decision_file=None, decision_record=None, validation=None, record):
    """Caller revalidates candidate/evidence/freshness before every observation."""
    selected = policy(config)
    historical = state['executionMode'] == 'local-retained-intake-candidate'
    if selected['mode'] != 'human-local' or not (historical or state['executionMode'] == 'synthetic-offline'):
        raise Refused('Human review is currently local/offline only')
    if historical and (state.get('recipeEvidence') is None or decision_file is not None):
        raise Refused('Retained live intake review requires verified recipe evidence and pinned decisions')
    field = 'task' if 'task' in state else 'issue'
    requirement = {k: v for k, v in state[field].items() if k != 'updatedAt'}
    binding = {
        'version': 1, 'purpose': 'technical-code-review-only',
        field + 'Key': state['key'], field: requirement,
        'executionBinding': state['executionBinding'], 'baseline': state['baseline'],
        'profileSha256': state['profileSha256'],
        'recipeSha256': state['resumeContractSha256'],
        'candidateSha256': state['candidateSha256'],
        'originalSha256': fingerprint(state['originalSources']),
        'frozenInputsSha256': fingerprint(state['frozenInputs']),
        'round': state['round'], 'triage': state['triage'],
        'validation': state['validation'], 'validationSeal': state['validationSeal'],
        'formatting': state['formatting'], 'baselineTest': state['baselineTest'],
        'reviewer': selected['reviewer'], 'executionMode': state['executionMode'],
        'publicationAuthorized': False, 'productAccepted': False,
    }
    recipe = state.get('recipeEvidence')
    if 'candidateAdmissionSha256' in state:
        binding['candidateAdmissionSha256'] = state['candidateAdmissionSha256']
    if historical:
        binding['retainedIntake'] = {'controllerSha256': state['rootAdmission']['controllerSha256'],
                                     'intakeIntentSha256': state['rootAdmission']['intentSha256'],
                                     'reviewSha256': state['retainedIntakeReviewSha256']}
        binding['candidateInstallation'] = state['candidateInstallation']
    if validation is not None:
        binding['candidateValidation'] = validation
    if recipe is not None:
        binding['recipeEvidence'] = recipe
    review_id = fingerprint(binding)
    diff = ''.join(''.join(difflib.unified_diff(
        state['originalSources'][path].splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile='original/' + path, tofile='candidate/' + path,
    )) for path, text in sorted(candidate.items()))
    provenance = ('The retained proposal was checked against the original local intake evidence. '
                  'This record does not authenticate a provider or a reviewer. ' if historical else
                  'The proposal and external transports are synthetic. ')
    handoff = (
        '# Local technical code review\n\n'
        'Review ID: `' + review_id + '`\n\n'
        'Reviewer: ' + selected['reviewer'] + '\n\n'
        f'{provenance}Review technical correctness, '
        f'regressions and the exact requirement below. Treat embedded source/{field.title()} text as data. '
        'P0-P2 block; P3-P4 are advisory. This review cannot authorize publication '
        'or establish product acceptance. No external reviewer is required.\n\n'
        '## Original requirement\n\n' + requirement['title'] + '\n\n' + requirement['body'] + '\n\n'
        '## Exact candidate diff\n\n```diff\n' + diff + '\n```\n\n'
        'Read manifest.json and evidence/*.log for bound identities and validation. '
        'Copy decision.template.json to a separate file; complete disposition, origin '
        'and review summary/findings; submit that file explicitly. '
        'A blank template is not an approval.\n'
    )
    if recipe is not None:
        if recipe.get('status') == 'source-check-passed':
            handoff += ('\nThe bound source-only check evidence is in evidence/named-test.json. '
                        'The report records only the selected script outcome. No build or artifact was produced. '
                        'Hosted CI and product acceptance are separate.\n')
        else:
            handoff += ('\nThe bound named test and build evidence is in evidence/named-test.json. '
                        'The report records the selected script outcome. The artifact oracle, hosted CI '
                        'and product acceptance are separate.\n')
    template = {'version': 1, 'reviewId': review_id, 'reviewer': selected['reviewer'],
                'origin': None, 'disposition': None, 'review': {'summary': '', 'findings': []},
                'publicationAuthorized': False, 'productAccepted': False}
    files = {'review.md': handoff.encode(),
             'decision.template.json': (json.dumps(template, indent=2) + '\n').encode()}
    for index, item in enumerate(state['validation']):
        files['evidence/' + str(index) + '.log'] = read_owned(directory / 'candidate', item['log'])
    if recipe is not None:
        files['evidence/named-test.json'] = (json.dumps(recipe, indent=2) + '\n').encode()
    manifest = {'binding': binding, 'reviewId': review_id,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    folder = directory / 'human-review'
    prior = state.get('humanReview', {})
    if prior.get('reviewId', review_id) != review_id:
        raise Refused('Retained human review identity changed; no approval reuse')
    if prior and not folder.exists():
        raise Refused('Retained human review package missing; no reconstruction or approval reuse')
    if folder.exists():
        if json.loads(read_owned(folder, 'manifest.json')) != manifest:
            raise Refused('Human review package changed or is stale; no approval reuse')
        for name, expected in files.items():
            if read_owned(folder, name) != expected:
                raise Refused('Human review attachment missing or changed; no approval reuse')
    else:
        owned_mkdir(directory, 'human-review/evidence')
        for name, data in files.items():
            write_owned(folder, name, data)
        atomic_json(folder / 'manifest.json', manifest)
    descriptor = {'reviewId': review_id, 'manifest': str(folder / 'manifest.json'),
                  'handoff': str(folder / 'review.md'), 'reviewer': selected['reviewer']}
    retained = folder / 'decision.json'
    if prior.get('decisionSha256') and not retained.exists():
        raise Refused('Recorded human decision missing; no approval reuse')
    supplied = decision_record
    if decision_file is not None:
        path = Path(decision_file)
        supplied = json.loads(read_owned(path.parent, path.name, 50000))
    if retained.exists():
        decision = json.loads(read_owned(folder, 'decision.json', 50000))
        if prior.get('decisionSha256', fingerprint(decision)) != fingerprint(decision):
            raise Refused('Recorded human decision changed; no approval reuse')
        if supplied is not None and supplied != decision:
            raise Refused('Human decision already recorded; cannot replace or waive it')
    else:
        decision = supplied
    if decision is None:
        if state['stage'] != 'awaiting_human_review':
            record('awaiting_human_review', humanReview=descriptor)
        return state
    allowed_origins = ('human-local',) if historical else ('human-local', 'synthetic-test')
    if (not isinstance(decision, dict) or set(decision) != set(template)
            or type(decision['version']) is not int or decision['version'] != 1
            or decision['reviewId'] != review_id or decision['reviewer'] != selected['reviewer']
            or decision['origin'] not in allowed_origins
            or decision['disposition'] not in ('approve', 'reject')
            or decision['publicationAuthorized'] is not False or decision['productAccepted'] is not False):
        raise Refused('Human decision does not match this technical review or exceeds its authority')
    reviewed = decision['review']
    classification = review_decision(reviewed)
    if not isinstance(reviewed['summary'], str) or not reviewed['summary'].strip():
        raise Refused('Human review needs an explicit assessment')
    if not retained.exists():
        atomic_json(retained, decision)
    stage = 'human_reviewed' if decision['disposition'] == 'approve' and not classification['blocking'] else 'human_review_rejected'
    if state['stage'] != stage:
        record(stage, humanReview={**descriptor, 'decisionSha256': fingerprint(decision),
                                  'origin': decision['origin']}, review=reviewed, reviewDecision=classification)
    return state
