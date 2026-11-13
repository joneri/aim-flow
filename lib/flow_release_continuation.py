"""Flow-owned fresh-main preparation and a separately granted project release.

No deployment policy lives here. The reviewed project command must enforce the
merged source and declared CI selection before its first external write,
including artifact bytes/provenance.
Receipt checks alone are not a pre-effect provenance guard.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import shlex
import re
from types import SimpleNamespace

import flow_api_delivery as api
import flow_github_delivery as github
from aim_ci import control_package, project_command as command
from aim_ci.owned_paths import new_owned_directory, read_owned_content
from aim_ci.project_discovery import CommitReader, unique_json

ROOT = Path(__file__).resolve().parents[1]


def require(ok, message):
    github.require(ok, message)


def verify_handoff(pin):
    # Rebuild the originating chain intent with its real upstream verifiers.
    # Reading an arbitrary JSON success flag is not sufficient.
    import flow_delivery_chain as chain
    handoff = unique_json(github.pinned_metadata(pin))
    state = Path(pin['path']).parent
    grant = unique_json(read_owned_content(state, 'grant.json'))
    intent = grant['intent']
    config = intent['config']
    c, verified, body, pc, prepared = chain.prepare(Path(config['path']), config['sha256'], state)
    require(prepared == intent and grant['intentSha256'] == api.digest(api.encoded(intent))
            and handoff['intentSha256'] == grant['intentSha256'], 'Release handoff grant changed')
    require(handoff['format'] == chain.FORMAT and handoff['status'] == 'main-push-handoff-ready'
            and handoff['source'] == intent['source'] and handoff['repository'] == intent['repository'],
            'Release requires the verified Flow delivery chain')
    require(handoff['phaseGrant'] == chain.pinned(state / 'grant.json')
            and handoff['candidateReceipt'] == verified['evidence']['candidate']
            and handoff['localRunReceipt'] == verified['evidence']['localRun'], 'Handoff evidence differs')
    expected = {'draft-body.md': body.encode(), 'publication-config.json': api.encoded(pc),
        'local-proof.json': api.encoded(chain.local_proof(verified))}
    require(chain.parsed(state / 'prepared.json') == {k: api.digest(v) for k, v in expected.items()},
            'Prepared delivery inventory differs')
    for name, raw in expected.items():
        require(api.file_bytes(state / name) == raw, 'Prepared delivery input differs')
    require(Path(handoff['publicationReceipt']['path']).parent == state / 'publication'
            and Path(handoff['mainPushReceipt']['path']).parent == state / 'merge', 'Child state differs')
    published = chain.verify_receipt(handoff['publicationReceipt'])
    require(published['status'] == 'draft-pr-verified', 'Publication incomplete')
    pull = published['deliveryPull']
    require(pull['headSha'] == verified['source']['headSha'] and pull['baseSha'] == c['baseSha']
            and pull['headRef'] == c['branch'] and pull['baseRef'] == c['baseRef'], 'Published source differs')
    mc = chain.merge_config(c, pc, pull)
    require(chain.parsed(state / 'merge-config.json') == mc, 'Merge declaration differs')
    merge_pin = chain.pinned(state / 'merge-config.json')
    _, _, merge_intent = github.prepare(Path(merge_pin['path']), merge_pin['sha256'], state / 'merge')
    merge_digest = api.digest(api.encoded(merge_intent))
    require(chain.parsed(state / 'merge-dispatch.json') == {'intent': merge_intent, 'intentSha256': merge_digest},
            'Merge dispatch grant differs')
    child = chain.verify_receipt(handoff['mainPushReceipt'])
    require(child['intentSha256'] == merge_digest and child['declarationSha256'] == merge_pin['sha256']
            and child['headSha'] == pull['headSha'] and child['baseSha'] == pull['baseSha']
            and child['pullNumber'] == pull['number'] and child['workflowPath'] == c['workflow']['path']
            and child['workflowSha256'] == c['workflow']['sha256'], 'Main child binding differs')
    for key in ('pushResponseConfirmed', 'draftResponseConfirmed'):
        require(handoff[key] == published[key], 'Publication attribution differs')
    require(handoff['mergeResponseConfirmed'] == child['mergeResponseConfirmed'], 'Merge attribution differs')
    require(child['status'] == 'main-push-verified', 'Main push is not verified')
    for key in ('repository', 'mergedSha', 'mergedTree', 'observedMainSha', 'runId', 'runAttempt', 'artifact', 'workflowPath'):
        require(child[key] == handoff[key], 'Main-push release identity differs')
    require(handoff['mergedSha'] == handoff['observedMainSha'], 'Merged main differs')
    require(child.get('mergedTree') == handoff['source']['treeSha'],
            'Verified merged tree must equal the reviewed candidate tree')
    return handoff


class Git:
    """No inherited configuration, hooks, filters, template, or shell commands."""
    def __init__(self, pin, authentication=None):
        self.pin, self.authentication = pin, authentication

    def __call__(self, cwd, *args):
        command.tool_identity(self.pin)
        env = {'PATH': os.defpath, 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1',
            'GIT_ALLOW_PROTOCOL': 'https', 'LC_ALL': 'C'}
        options = ['-c', 'credential.helper=', '-c', 'credential.useHttpPath=true',
            '-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false',
            '-c', 'maintenance.auto=false', '-c', 'gc.auto=0']
        if self.authentication:
            tr = self.authentication
            command.tool_identity(tr['gh'])
            # Same fixed official gh credential protocol as publication; no
            # caller-supplied shell fragment, ambient helper, or token copying.
            options += ['-c', 'credential.helper=!' + shlex.quote(tr['gh']['path']) + ' auth git-credential']
            env.update(HOME=tr['home'], GH_CONFIG_DIR=tr['configDirectory'], GH_HOST='github.com',
                GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1', GH_NO_EXTENSION_UPDATE_NOTIFIER='1')
        result = subprocess.run([self.pin['path'], '--no-replace-objects',
            '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *options, *args],
            cwd=cwd, env=env, capture_output=True,
            timeout=self.authentication['timeoutSeconds'] if self.authentication else 300)
        require(result.returncode == 0, 'Fresh release checkout Git step failed; no automatic retry')
        return result.stdout


def qualify_git(pin):
    """Qualify locally before creating state, authenticating, or fetching."""
    command.tool_identity(pin)
    observed = subprocess.run([pin['path'], '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false', '--version'], stdin=subprocess.DEVNULL,
        env={'PATH': os.defpath, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '',
             'GIT_NO_REPLACE_OBJECTS': '1'}, capture_output=True, timeout=20)
    version = re.match(rb'^git version (\d+)\.(\d+)(?:\.(\d+))?(?=[ .-]|$)', observed.stdout)
    require(observed.returncode == 0 and version is not None
            and tuple(int(part or 0) for part in version.groups()) >= (2, 54, 0),
            'Fresh release checkout requires Git 2.54.0 or newer')
    command.tool_identity(pin)


def checkout_intent(handoff_pin, git_pin, target, state, authentication=None):
    handoff = verify_handoff(handoff_pin)
    target, state = command.canonical(str(target)), command.canonical(str(state))
    qualify_git(git_pin)
    auth_inputs = []
    if authentication is not None:
        github.keys(authentication, ('gh', 'home', 'configDirectory', 'timeoutSeconds'), 'checkout authentication')
        command.tool_identity(authentication['gh'])
        auth_inputs.append(Path(authentication['gh']['path']))
        for key in ('home', 'configDirectory'):
            directory = command.canonical(authentication[key])
            require(directory.is_dir(), 'Declared authentication directory missing')
            auth_inputs.append(directory)
        require(type(authentication['timeoutSeconds']) is int and 1 <= authentication['timeoutSeconds'] <= 300,
                'Checkout deadline must be 1..300 seconds')
    for protected in (ROOT, Path(handoff_pin['path']).parent, Path(git_pin['path'])):
        for destination in (target, state):
            require(not destination.is_relative_to(protected) and not protected.is_relative_to(destination),
                    'Release state/checkout overlaps protected input')
    for protected in auth_inputs:
        require(not protected.is_relative_to(target) and not protected.is_relative_to(state),
                'Release output would contain authentication input')
    require(not target.is_relative_to(state) and not state.is_relative_to(target), 'Release paths overlap')
    return {'format': 'aim-flow-release-checkout', 'schemaVersion': 1,
        'handoff': handoff_pin, 'git': git_pin, 'authentication': authentication,
        'checkout': str(target), 'state': str(state),
        'commit': handoff['mergedSha'], 'tree': handoff['source']['treeSha'],
        'origin': 'https://github.com/' + handoff['repository']['name'] + '.git',
        'branch': 'main', 'releaseExecuted': False, 'replayAuthorized': False}


def prepare_checkout(handoff_pin, git_pin, target, state, authorization=None, *, transport=None, authentication=None):
    intent = checkout_intent(handoff_pin, git_pin, target, state, authentication)
    digest = api.digest(api.encoded(intent))
    if authorization is None:
        return {'status': 'awaiting-checkout-authorization', 'intentSha256': digest, 'intent': intent}
    require(authorization == digest, 'Exact fresh-checkout grant required')
    target, state = Path(target), Path(state)
    git = transport or Git(git_pin, authentication)
    with new_owned_directory(state):
        command.durable_write(state, 'intent.json', api.encoded(intent))
        command.durable_write(state, 'started.json', api.encoded({'status': 'outcome-unknown', 'replayAuthorized': False}))
        with new_owned_directory(target):
            git(target, 'init', '--template=', '--initial-branch=main', '.')
            git(target, 'remote', 'add', 'origin', intent['origin'])
            git(target, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', intent['commit'])
            require(git(target, 'rev-parse', 'FETCH_HEAD').decode().strip() == intent['commit'], 'Fetched SHA differs')
            require(git(target, 'rev-parse', intent['commit'] + '^{tree}').decode().strip() == intent['tree'], 'Merged tree differs')
            git(target, 'update-ref', 'refs/heads/main', intent['commit'])
            git(target, 'read-tree', intent['commit'])
            reader = CommitReader(ROOT, target, Path(git_pin['path']), git_pin['sha256'], intent['commit'])
            # Never run checkout: .gitattributes may invoke filters. Materialize
            # hash-checked blobs and reject links/submodules before project execution.
            entries = list(reader.entries(reader.commit, recursive=True))
            for name, mode, kind, oid in entries:
                command.shape._source_path(name)
                require(kind == 'blob' and mode in ('100644', '100755')
                        and all(part.lower() != '.git' for part in Path(name).parts), 'Unsupported checkout entry')
            for name, mode, kind, oid in entries:
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(reader.blob(oid))
                path.chmod(0o755 if mode == '100755' else 0o644)
            command.source_identity(reader)
            require(checkout_intent(handoff_pin, git_pin, target, state, authentication) == intent, 'Handoff changed during checkout')
            result = {'status': 'fresh-main-prepared', 'intentSha256': digest, 'target': {
                'checkout': str(target), 'commit': intent['commit'], 'tree': intent['tree']},
                'releaseExecuted': False, 'replayAuthorized': False}
            command.durable_write(state, 'result.json', api.encoded(result))
            return result


def release(root, args, handoff_pin, checkout_receipt, contract_pin):
    """Preview/dispatch the existing exact-intent command after binding its scope.

The contract is reviewed evidence, not an automated proof of code semantics.
It cites committed project source and the declared mechanism that checks the
declared CI selection before upload. Unknown backup/protection obligations block here.
"""
    handoff = verify_handoff(handoff_pin)
    prepared = unique_json(command.file_pin(checkout_receipt))
    checkout_state = Path(checkout_receipt['path']).parent
    require(Path(checkout_receipt['path']).name == 'result.json', 'Not a checkout completion record')
    checkout_grant = unique_json(read_owned_content(checkout_state, 'intent.json'))
    expected_checkout = checkout_intent(handoff_pin, checkout_grant['git'],
        Path(checkout_grant['checkout']), checkout_state, checkout_grant['authentication'])
    require(checkout_grant == expected_checkout and prepared['intentSha256'] == api.digest(api.encoded(checkout_grant))
        and unique_json(read_owned_content(checkout_state, 'started.json')) == {
            'status': 'outcome-unknown', 'replayAuthorized': False}, 'Checkout provenance differs')
    contract = unique_json(command.file_pin(contract_pin))
    intent, reader = command.prepare(root, args)
    declaration = intent['declaration']
    require(prepared['status'] == 'fresh-main-prepared' and prepared['target'] == declaration['target']
            and prepared['target']['commit'] == handoff['mergedSha']
            and prepared['target']['tree'] == handoff['source']['treeSha'], 'Release checkout differs')
    require(contract['format'] == 'aim-flow-project-release-contract' and contract['schemaVersion'] == 1,
            'Unsupported release contract')
    require(contract['command'] == declaration['command'] and contract['scope'] == declaration['scope']
            and contract['handoff'] == handoff_pin, 'Release contract scope differs')
    require(contract['selectionPolicy'] in ('handoff-exact', 'project-refresh-same-commit'), 'Unknown release selection policy')
    require(contract['preEffectChecks'] == ['commit', 'runId', 'runAttempt', 'artifactIdentity', 'artifactBytes'],
            'Project must enforce reviewed source/CI/artifact selection before its first upload')
    require(contract['sourceCitations'], 'Missing project-owned release source citations')
    sources = command.source_identity(reader)
    for citation in contract['sourceCitations']:
        require(citation['path'] in sources and sources[citation['path']]['sha256'] == citation['sha256'],
                'Project release citation differs from merged source')
    require(isinstance(contract['protectionObligations'], list) and isinstance(contract['protectionEvidence'], list),
            'Project protection obligations must be retained, not silently discarded')
    # Require actual verified project-command records, not operator-authored
    # backup booleans. Their reviewed project assertions define what was proved.
    require(contract['unresolvedProtectionObligations'] == [],
            'Unresolved production baseline/backup obligations block release')
    for evidence in contract['protectionEvidence']:
        verification = SimpleNamespace(**evidence['verification'])
        verification.state = Path(verification.state)
        verification.declaration = Path(verification.declaration)
        require({'path': str(verification.state / 'result.json'), 'sha256': verification.receipt_sha256}
                in declaration['inputs'], 'Protection receipt must be pinned by the release command')
        proof = command.verify(root, verification)
        require(proof['status'] == 'project-receipt-matched', 'Protection command did not complete')
        require(evidence['obligations'] and all(item in contract['protectionObligations']
            for item in evidence['obligations']), 'Unbound protection evidence')
    require(set(contract['protectionObligations']) == {item for evidence in contract['protectionEvidence']
            for item in evidence['obligations']}, 'Protection evidence does not cover obligations')
    # Contract and handoff must be immutable command inputs, rechecked by the
    # command driver at dispatch and completion, not just in this wrapper.
    for pin in (handoff_pin, checkout_receipt, contract_pin):
        require(pin in declaration['inputs'], 'Release command must pin bridge inputs')
    # Each project maps its own receipt paths. Values cannot substitute a different
    # run/attempt/commit after effect; pre-effect enforcement is also mandatory.
    github.keys(contract['receiptPaths'], ('commit', 'runId', 'runAttempt'), 'receipt identity paths')
    command.assertion_contract([{'path': path, 'equals': None} for path in contract['receiptPaths'].values()])
    required = {'commit': handoff['mergedSha']}
    if contract['selectionPolicy'] == 'handoff-exact':
        required.update(runId=handoff['runId'], runAttempt=handoff['runAttempt'])
    for key, value in required.items():
        path = contract['receiptPaths'][key]
        require({'path': path, 'equals': value} in declaration['receipt']['assertions'], 'Missing exact release receipt assertion')
    result = command.command(root, args)
    actual = None
    status = result['status']
    if status == 'project-receipt-matched':
        receipt = unique_json(read_owned_content(args.state, 'project-receipt.json'))
        def at(path):
            value = receipt
            for part in path:
                value = value[part]
            return value
        try:
            actual = {key: at(path) for key, path in contract['receiptPaths'].items()}
            require(actual['commit'] == handoff['mergedSha'] and github.positive(actual['runId'])
                    and github.positive(actual['runAttempt']), 'Invalid actual project release identity')
        except (KeyError, IndexError, TypeError, ValueError):
            actual = None
            status = 'outcome-unknown'
    # Preserve project-command's original receipt and verification semantics.
    # A fresh selection must not be attributed to the older readiness snapshot.
    return {'format': 'aim-flow-release-continuation', 'schemaVersion': 1,
        'status': status, 'actualReleaseIdentity': actual,
        'selectionPolicy': contract['selectionPolicy'], 'readinessHandoff': handoff_pin,
        'projectCommand': result, 'productionFeatureAccepted': False,
        'boundary': 'Project-owned operational receipt only; production feature acceptance and rollback guarantees are not inferred.'}


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare-checkout', 'release'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--authorize-intent')
    args = parser.parse_args(argv)
    control_package.verify_installation(ROOT, args.manifest_sha256)
    config = unique_json(command.file_pin({'path': str(args.config), 'sha256': args.config_sha256}))
    if args.action == 'prepare-checkout':
        github.keys(config, ('handoff', 'git', 'checkout', 'state', 'authentication'), 'checkout declaration')
        result = prepare_checkout(config['handoff'], config['git'], Path(config['checkout']),
            Path(config['state']), args.authorize_intent, authentication=config['authentication'])
    else:
        github.keys(config, ('handoff', 'checkoutReceipt', 'contract', 'declaration', 'state'), 'release declaration')
        options = SimpleNamespace(action='run-project-command', manifest_sha256=args.manifest_sha256,
            declaration=Path(config['declaration']['path']), declaration_sha256=config['declaration']['sha256'],
            state=Path(config['state']), preview=args.authorize_intent is None,
            authorize_intent=args.authorize_intent)
        result = release(ROOT, options, config['handoff'], config['checkoutReceipt'], config['contract'])
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    main()
