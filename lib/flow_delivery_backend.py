"""Explicit model backend/agent-host routing. Never dispatch or silently fall back."""
from aim_ci.owned_paths import Refused


def route(profile):
    if not isinstance(profile, dict) or set(profile) != {'backend', 'executionHost'}:
        raise Refused('Delivery profile requires backend and executionHost separately')
    backend, host = profile['backend'], profile['executionHost']
    if backend not in ('responses-api', 'codex-cli') or host not in ('local', 'cloud'):
        raise Refused('Unknown delivery backend or execution host')
    result = {'backend': backend, 'executionHost': host,
              'costSource': 'openai-api-campaign' if backend == 'responses-api' else 'codex-account-usage',
              'automaticFallback': False, 'projectExecutionAuthorized': False,
              'publicationAuthorized': False, 'deploymentAuthorized': False}
    if host == 'cloud':
        return {**result, 'status': 'blocked', 'command': None,
                'blockers': ['No verified Flow cloud dispatch/result adapter is installed.',
                    ('A real Codex environment must be bound to the exact repository, revision and permissions; local paths and credentials are not cloud access.'
                     if backend == 'codex-cli' else
                     'An explicit remote Flow runner, pinned inputs, approved API credentials and campaign continuity are required; a local watcher is not a remote runner.')]}
    return {**result, 'status': 'available',
            'command': 'deliver-api' if backend == 'responses-api' else 'deliver-codex',
            'workflowCommand': 'run' if backend == 'responses-api' else 'run-codex',
            'scope': 'proposal-only; application, project tests, acceptance and release remain separate',
            'prerequisites': ['Existing pinned API campaign and approved API key'] if backend == 'responses-api'
                else ['Pinned Codex CLI supporting exec JSON/schema and ignore-user-config',
                      'Saved ChatGPT login on the execution host; no API-key fallback']}
