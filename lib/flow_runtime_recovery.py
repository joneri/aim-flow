"""Read-only reconciliation of a completed runtime install after inventory failure.

The original process/journal/result stay immutable. Never relaunch the command.
Current dependency bytes are newly measured, not claimed to be an earlier snapshot.
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import flow_api_delivery as api
import flow_workflow as work
from aim_ci import control_package, project_command as command, project_validation
from aim_ci.owned_paths import Refused, new_owned_directory
from aim_ci.project_discovery import unique_json

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'aim-flow-runtime-reconciliation'


def prepare(config_path, config_sha, state):
    pin = {'path': str(api.canonical_path(config_path)), 'sha256': config_sha}
    state = api.canonical_path(state); c = unique_json(api.pinned(pin))
    command.shape._keys(c, ('format', 'schemaVersion', 'manifestSha256', 'python', 'prior'), 'runtime reconciliation')
    if c['format'] != FORMAT or type(c['schemaVersion']) is not int or c['schemaVersion'] != 1:
        raise Refused('Unsupported runtime reconciliation')
    control_package.verify_installation(ROOT, c['manifestSha256']); api.pinned(c['python'])
    prior = c['prior']; command.shape._keys(prior, ('controller', 'declaration', 'state', 'receipt'), 'prior runtime preparation')
    command.shape._keys(prior['controller'], ('path', 'manifestSha256'), 'prior runtime controller')
    old_root = api.canonical_path(prior['controller']['path']); old_state = api.canonical_path(prior['state'])
    before = project_validation.prior_controller(old_root, prior['controller']['manifestSha256'])
    if prior['receipt']['path'] != str(old_state / 'result.json'):
        raise Refused('Runtime reconciliation requires the exact original result')
    old = unique_json(api.pinned(prior['receipt'])); declaration = unique_json(api.pinned(prior['declaration']))
    if (declaration.get('schemaVersion') != 2 or declaration['receipt'].get('mode') != 'runtime-preparation'
            or old.get('status') != 'outcome-unknown' or old.get('runtimePreparation') is not None
            or old.get('diagnostic') != 'Owned input/output must be a single-link regular file'
            or old['process']['exitCode'] != 0 or old['process']['termination'] is not None):
        raise Refused('Only a completed install with the retained ownership-inventory failure may be reconciled')
    argv = [c['python']['path'], '-IBS', str(old_root / 'aim-flow.py'), 'verify-project-command',
        '--manifest-sha256', prior['controller']['manifestSha256'], '--declaration', prior['declaration']['path'],
        '--declaration-sha256', prior['declaration']['sha256'], '--state', str(old_state),
        '--receipt-sha256', prior['receipt']['sha256']]
    process = project_validation.verification_process(argv, ROOT,
        {'PATH': os.defpath, 'HOME': str(ROOT), 'LC_ALL': 'C'}, metadata_limit=max(64000, len(api.encoded(old))*2+4096))
    expected = {**old, 'receipt': str(old_state/'result.json'), 'receiptSha256': prior['receipt']['sha256']}
    if process.returncode != 3 or process.stderr or unique_json(process.stdout) != expected:
        raise Refused('Pinned original controller did not verify the exact retained process/journal')
    original = unique_json(api.file_bytes(old_state / 'intent.json'))
    if (original['controllerManifestSha256'] != prior['controller']['manifestSha256']
            or original['declarationSha256'] != prior['declaration']['sha256']
            or original['declaration'] != declaration or old['process']['argv'] != original['argv']
            or any(value is not None for value in original['runtimeOutputsBefore'].values())):
        raise Refused('Original runtime intent or completed process binding differs')
    args = SimpleNamespace(manifest_sha256=c['manifestSha256'], declaration=Path(prior['declaration']['path']),
        declaration_sha256=prior['declaration']['sha256'], state=old_state)
    current, _ = command.prepare(ROOT, args)
    omit = {'controllerManifestSha256', 'runtimeOutputsBefore', 'receiptFilesBefore'}
    if {k:v for k,v in current.items() if k not in omit} != {k:v for k,v in original.items() if k not in omit}:
        raise Refused('Source, tool, dependency input, configuration or evidence changed since install')
    if not command.check_inputs(ROOT, args, current):
        raise Refused('Original tool bindings or inputs changed')
    trees = list(current['runtimeOutputsBefore'].values())
    if not trees or any(tree is None or not tree['files'] for tree in trees):
        raise Refused('Completed runtime output is missing or empty')
    protected = [ROOT, old_root, old_state, Path(declaration['target']['checkout']),
        Path(prior['declaration']['path']), Path(pin['path'])]
    if any(state==p or p in state.parents or state in p.parents for p in protected):
        raise Refused('Runtime reconciliation state overlaps protected original evidence/source')
    if before != project_validation.prior_controller(old_root, prior['controller']['manifestSha256']):
        raise Refused('Original controller changed')
    api.pinned(prior['receipt']); api.pinned(c['python'])
    inventory = {'format': 'aim-flow-reconciled-runtime-inventory', 'schemaVersion': 1, 'outputTrees': trees}
    intent = {'format': FORMAT, 'schemaVersion': 1, 'config': pin, 'state': str(state),
        'controllerManifestSha256': c['manifestSha256'], 'prior': prior,
        'priorIntentSha256': api.digest(api.encoded(original)), 'target': declaration['target'],
        'inventorySha256': api.digest(api.encoded(inventory)),
        'preparedRuntimeTrees': [{'path': tree['path'], 'sha256': tree['sha256']} for tree in trees],
        'sourceIdentitySha256': api.digest(api.encoded(current['sources'])),
        'commandRerunAuthorized': False, 'modelCallsAuthorized': False, 'deploymentAuthorized': False,
        'qualification': 'Original process completed successfully; current source/input identity is unchanged and current output bytes are newly measured. Original failed inventory is preserved; no earlier output snapshot or hostile-process attestation is claimed.'}
    return c, intent, inventory


def run(config_path, config_sha, state, authorization=None, *, verify_only=False):
    state = api.canonical_path(state)
    c, intent, inventory = prepare(config_path, config_sha, state)
    grant = {'intent': intent, 'intentSha256': api.digest(api.encoded(intent))}
    result = {'format': FORMAT, 'schemaVersion': 1, 'status': 'runtime-preparation-reconciled',
        'target': intent['target'], 'originalRuntimeReceipt': c['prior']['receipt'],
        'preparedRuntimeTrees': intent['preparedRuntimeTrees'], 'sourceIdentitySha256': intent['sourceIdentitySha256'],
        'inventory': {'path': str(state/'runtime-inventory.json'), 'sha256': intent['inventorySha256']},
        'qualification': intent['qualification'], 'originalResultRewritten': False,
        'commandRerun': False, 'modelCallsExecuted': False, 'deploymentAuthorized': False}
    if verify_only and authorization is not None: raise Refused('Verification takes no authorization')
    if not state.exists():
        if verify_only: raise Refused('Runtime reconciliation receipt missing')
        if authorization is None:return {'status':'awaiting-authorization',**grant}
        work.require_authorization(authorization,intent)
        with new_owned_directory(state):api.fsync_directory(state.parent)
        for name,value in [('grant.json',grant),('runtime-inventory.json',inventory),('result.json',result)]:
            api.durable(state,name,api.encoded(value))
    if authorization is not None:work.require_authorization(authorization,intent)
    for name,value in [('grant.json',grant),('runtime-inventory.json',inventory),('result.json',result)]:
        if unique_json(api.file_bytes(state/name)) != value:raise Refused('Runtime reconciliation inputs or receipt changed')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--config-sha256',required=True)
    p.add_argument('--state',type=Path,required=True);p.add_argument('--authorize-intent');p.add_argument('--verify',action='store_true')
    a=p.parse_args(argv);print(json.dumps(run(a.config,a.config_sha256,a.state,a.authorize_intent,verify_only=a.verify),sort_keys=True))
    return 0
