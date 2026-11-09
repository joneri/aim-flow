"""Validated static artifact construction and extraction, independent of model code."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from openai_budget import Refused, fingerprint, safe_path
from .contracts import review_decision
from .contracts import admit_issue, admitted_triage


from .artifacts import manifest
from .artifacts import metadata as artifact_metadata, extract_verified as extract_artifact


# Backwards-compatible first-adapter exports; product policy lives in products.
from .products import fakeatg_metadata as metadata, fakeatg_extract as extract_verified


def proof_from_body(body):
    matches = re.findall(r'<!-- aim-ci-proof:([A-Za-z0-9+/=]+) -->', body)
    if len(matches) != 1 or len(matches[0]) > 32000:
        raise Refused('One bounded production proof required')
    try:
        return json.loads(base64.b64decode(matches[0], validate=True))
    except (ValueError, UnicodeDecodeError) as error:
        raise Refused('Invalid production proof') from error


def verify_delivery(pr, proof, config, merge_sha, candidate):
    if (pr.get('merged') is not True or pr.get('merge_commit_sha') != merge_sha
            or pr.get('user', {}).get('login') != config['owner']
            or pr.get('head', {}).get('repo', {}).get('full_name') != config['repository']
            or pr.get('base', {}).get('ref') != config['branch']
            or not pr.get('head', {}).get('ref', '').startswith('codex/aim-ci-')
            or proof.get('version') != 1 or proof.get('headSha') != pr['head']['sha']
            or type(proof.get('issue')) is not int or proof['issue'] <= 0
            or not re.fullmatch('[0-9a-f]{40}', proof.get('baselineSha', ''))
            or not re.fullmatch('[0-9a-f]{64}', proof.get('issueSha256', ''))
            or proof.get('repository') != config['repository']
            or proof.get('candidateSha256') != fingerprint(candidate)
            or proof.get('validationPassed') is not True or proof.get('profileSha256') != fingerprint(config)):
        raise Refused('Merged delivery proof/source mismatch')
    if review_decision(proof.get('review'))['blocking']:
        raise Refused('Blocking findings cannot deploy')
    return proof


def authorize_delivery(config, source_sha, github, git, *, pull_request=None):
    """One read-only source/Issue/PR gate for hosted and offline transports."""
    repo = config['repository']
    if pull_request is None:
        matches = [pr for pr in github(f'repos/{repo}/commits/{source_sha}/pulls')
                   if pr.get('merged_at') and pr.get('merge_commit_sha') == source_sha]
        if len(matches) != 1:
            raise Refused('Release source requires one merged delivery PR')
        pull_request = matches[0]['number']
    if type(pull_request) is not int or pull_request <= 0:
        raise Refused('Release requires an exact original PR identity')
    pr = github(f'repos/{repo}/pulls/{pull_request}')
    if pr.get('number') != pull_request:
        raise Refused('Release transport returned a different PR identity')
    proof = proof_from_body(pr.get('body', ''))
    candidate = {path: git(['show', source_sha + ':' + config['product'] + '/' + path])
                 for path in config['allowedPaths']}
    verify_delivery(pr, proof, config, source_sha, candidate)
    files = github(f'repos/{repo}/pulls/{pr["number"]}/files?per_page=100')
    admitted_triage(proof.get('triage'), config)
    allowed = {config['product'] + '/' + path for path in proof['triage']['paths']}
    if (not files or len(files) != pr['changed_files']
            or any(file['filename'] not in allowed or file['status'] != 'modified' for file in files)):
        raise Refused('Merged PR includes changes outside triaged product files')
    for sha in (proof['headSha'], source_sha):
        parents = github(f'repos/{repo}/git/commits/{sha}')['parents']
        if [parent['sha'] for parent in parents] != [proof['baselineSha']]:
            raise Refused('Reviewed and merged source do not share the admitted baseline')
    issue = admit_issue(github(f'repos/{repo}/issues/{proof["issue"]}'), config)
    if fingerprint({key:value for key,value in issue.items() if key != 'updatedAt'}) != proof['issueSha256']:
        raise Refused('Source Issue changed or opt-in was withdrawn')
    return {'decision': 'release_authorized', 'sourceSha': source_sha,
            'issue': proof['issue'], 'pullRequest': pr['number']}
