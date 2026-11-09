"""Explicit project scope and strict data boundaries."""
import difflib
import re
import json
from openai_budget import Refused, fingerprint


def requirement_text(title, body, label):
    if (not isinstance(title, str) or not isinstance(body, str)
            or not title.strip() or '## Acceptance criteria' not in body
            or '## Scope' not in body or '- [ ]' not in body):
        raise Refused('A requirement needs Scope and observable Acceptance criteria')
    if re.search(r'sk-[A-Za-z0-9_-]{20,}|BEGIN .*PRIVATE KEY', title + '\n' + body):
        raise Refused(f'Potential credential in {label}; do not send to model')


def admit_issue(issue, profile):
    if (issue.get('state') != 'open' or issue.get('pull_request')
            or issue.get('user', {}).get('login') != profile['owner']
            or profile['label'] not in [x['name'] for x in issue.get('labels', [])]
            or issue.get('locked') or not isinstance(issue.get('number'), int)):
        raise Refused('Issue must be open, opted in and authored by the configured owner')
    title, body = issue.get('title'), issue.get('body')
    requirement_text(title, body, 'Issue')
    return {'number': issue['number'], 'nodeId': issue['node_id'], 'title': title,
            'body': body, 'updatedAt': issue['updated_at'], 'author': issue['user']['login'],
            'repository': profile['repository'], 'url': issue['html_url']}


def admit_local_task(task):
    if (not isinstance(task, dict) or set(task) != {'kind', 'version', 'id', 'title', 'body'}
            or task['kind'] != 'local-task' or type(task['version']) is not int or task['version'] != 1):
        raise Refused('Expected a version 1 local-task with id, title and body')
    if not isinstance(task['id'], str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', task['id']):
        raise Refused('Local task id must be a lowercase slug of 1–64 characters')
    requirement_text(task['title'], task['body'], 'local task')
    return task


def unchanged_issue(original, latest, profile):
    current = admit_issue(latest, profile)
    # Posting status comments changes updated_at, not requirement identity.
    if {k: v for k, v in current.items() if k != 'updatedAt'} != {k: v for k, v in original.items() if k != 'updatedAt'}:
        raise Refused('Issue changed or opt-in was withdrawn; no further effect')


def admitted_triage(value, profile):
    if (not isinstance(value, dict) or set(value) != {'ready', 'reason', 'paths', 'risk', 'acceptance'}
            or value['ready'] is not True or value['risk'] != 'low'
            or not isinstance(value['reason'], str) or not value['reason'].strip()
            or not isinstance(value['acceptance'], list) or not value['acceptance']
            or not all(isinstance(x, str) and x.strip() for x in value['acceptance'])
            or not isinstance(value['paths'], list) or not value['paths']
            or len(set(value['paths'])) != len(value['paths'])
            or any(p not in profile['allowedPaths'] for p in value['paths'])):
        raise Refused('Triage requires assessment: only clear low-risk permitted product changes are autonomous')
    return value


def bounded_patch(original, value, profile):
    if not isinstance(value, dict) or set(value) != {'summary', 'edits'} or not isinstance(value['summary'], str):
        raise Refused('Invalid patch envelope')
    edits = value['edits']
    if not isinstance(edits, list) or not edits:
        raise Refused('Patch needs at least one exact replacement')
    proposed = dict(original)
    previous = None
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'path', 'old', 'new'}
                or edit['path'] not in profile['allowedPaths'] or edit['path'] not in original):
            raise Refused('Patch outside admitted product scope')
        old, new = edit['old'], edit['new']
        # Repeating the immediately preceding, validated operation has no new
        # effect. Other overlaps still require one unique current source match.
        if edit == previous:
            continue
        if (not isinstance(old, str) or not isinstance(new, str) or not old or old == new
                or proposed[edit['path']].count(old) != 1):
            raise Refused('Invalid or ambiguous replacement; use unique source context')
        proposed[edit['path']] = proposed[edit['path']].replace(old, new, 1)
        previous = edit
    check_change_size(original, proposed, profile)
    return proposed


def check_change_size(original, proposed, profile):
    changed = sum(sum(1 for line in difflib.unified_diff(original[p].splitlines(), proposed[p].splitlines())
                      if line.startswith(('+', '-')) and not line.startswith(('+++', '---'))) for p in original)
    maximum = profile.get('maximumChangedLines')
    if changed < 1 or maximum is not None and changed > maximum:
        raise Refused('Change exceeds admitted story size')


def intake_proposal(value, profile):
    if not isinstance(value, dict) or set(value) != {'triage', 'patch'}:
        raise Refused('Invalid assessment/proposal envelope')
    # A proposal never supplies authority to accept its own assessment.
    return admitted_triage(value['triage'], profile), value['patch']


def review_decision(value):
    if not isinstance(value, dict) or set(value) != {'summary', 'findings'} or not isinstance(value['findings'], list):
        raise Refused('Unclassified review requires assessment')
    blockers, advisory = [], []
    for finding in value['findings']:
        if (not isinstance(finding, dict) or set(finding) != {'priority', 'title', 'detail'}
                or finding['priority'] not in ['P0', 'P1', 'P2', 'P3', 'P4']
                or not all(isinstance(finding[k], str) and finding[k].strip() for k in ['title', 'detail'])):
            raise Refused('Unclassified review requires assessment')
        (blockers if finding['priority'] in ['P0', 'P1', 'P2'] else advisory).append(finding)
    return {'blocking': blockers, 'advisory': advisory, 'sourceSha256': fingerprint(value)}


def codex_completed_comment(comments, head, *, findings_classified=False):
    """Codex v1 publishes clean completion as a full-SHA-bound Issue summary."""
    if len(comments) >= 100:
        raise Refused('Review completion comments exceed bounded assessment')
    summaries = [item for item in comments
                 if item.get('user', {}).get('login') in
                 {'chatgpt-codex-connector', 'chatgpt-codex-connector[bot]'}
                 and (item.get('body') or '').startswith('<!-- codex-pull-request-review-summary -->')]
    latest = max(summaries, key=lambda item: item['id'], default=None)
    if latest is None:
        return None  # No machine summary: retain the formal-review route.
    matches = re.findall(r'<!-- codex-review-state:v1 (\{[^\n]*\}) -->', latest['body'])
    if len(matches) != 1:
        raise Refused('Unclassified Codex completion summary')
    try:
        code = json.loads(matches[0])['code_review']
        if (not isinstance(code, dict) or code.get('commit_sha') != head
                or code.get('status') != 'completed'
                or type(code.get('has_any_findings')) is not bool
                or type(code.get('has_unresolved_findings')) is not bool):
            return False
        return ((code['has_any_findings'] is False and code['has_unresolved_findings'] is False)
                or findings_classified and code['has_any_findings'] is True)
    except (ValueError, KeyError, TypeError):
        raise Refused('Invalid Codex completion summary') from None


def checks_pass(checks, sha, name):
    matching = [c for c in checks.get('check_runs', []) if c.get('name') == name and c.get('head_sha') == sha]
    # Latest completed check is authoritative; an old green run cannot hide a new failed/in-progress run.
    latest = max(matching, key=lambda x: x.get('id', 0), default=None)
    return bool(latest and latest.get('app', {}).get('slug') == 'github-actions'
                and latest.get('status') == 'completed' and latest.get('conclusion') == 'success')


def merge_ready(pr, head, baseline, profile):
    return (pr.get('state') == 'open' and pr.get('mergeable') is True
            and pr.get('mergeable_state') == 'clean'
            and pr.get('head', {}).get('sha') == head
            and pr.get('base', {}).get('sha') == baseline
            and pr.get('base', {}).get('ref') == profile['branch']
            and pr.get('base', {}).get('repo', {}).get('full_name') == profile['repository'])
