"""Connect a private controller installation to a read-only project review draft."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from . import control_package
from .owned_paths import Refused, owned_directory, read_owned, safe_path, write_owned
from .project_discovery import CommitReader, unique_json


FORMAT = 'aim-ci-project-installation'
MODULE = 'lib/aim_ci/project_installation.py'
MAX_PROJECT_PLAN = 16_000_000


def _canonical(path):
    path = Path(path).absolute()
    safe_path(path)
    if path != path.resolve():
        raise Refused('Project installation paths must be canonical and unlinked')
    return path


def _overlaps(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def _paths(root, args):
    root, checkout = _canonical(root), _canonical(args.checkout)
    destination, output = _canonical(args.destination), _canonical(args.project_plan_output)
    if not checkout.is_dir() or _overlaps(root, checkout):
        raise Refused('Project checkout and invoking controller must be separate directories')
    if any(_overlaps(destination, other) for other in (root, checkout)):
        raise Refused('Project controller destination must be outside both checkouts')
    if any(_overlaps(output, other) for other in (root, checkout, destination)):
        raise Refused('Project plan receipt must be outside both checkouts and the controller destination')
    if os.path.lexists(destination) or os.path.lexists(output):
        raise Refused('Project controller destination and project plan receipt must both be new')
    # Check the parents before creating either output. Descriptor-relative writes
    # repeat the traversal when the installation and receipt are actually made.
    for parent in (destination.parent, output.parent):
        descriptor = owned_directory(parent)
        os.close(descriptor)
    return checkout, destination, output


def _installed_adaptation(destination, args, manifest_pin):
    command = [str(Path(sys.executable).resolve()), '-IBS', str(destination / 'aim-ci-control.py'),
               'propose-adaptation', '--manifest-sha256', manifest_pin,
               '--checkout', str(_canonical(args.checkout)), '--commit', args.commit,
               '--git', str(args.git), '--git-sha256', args.git_sha256]
    for document in args.project_doc:
        command.extend(('--project-doc', document))
    if args.test_script is not None:
        command.extend(('--test-script', args.test_script))
    if getattr(args, 'execution_kind', None) is not None:
        command.extend(('--execution-kind', args.execution_kind))
    for name in getattr(args, 'source_path', []):
        command.extend(('--source-path', name))
    # Use the installed implementation, not imports from the invoking source
    # checkout. Nothing from the user's Python, Git or credential environment
    # is inherited. Only pinned Git is used by the read-only discovery.
    with tempfile.TemporaryDirectory(prefix='aim-ci-project-inspection-') as folder:
        scratch = Path(folder).resolve()
        environment = {'PATH': os.defpath, 'HOME': str(scratch), 'LC_ALL': 'C'}
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(command, cwd=scratch, env=environment, stdout=stdout,
                                    stderr=stderr, timeout=180)
            stdout.seek(0)
            encoded = stdout.read(MAX_PROJECT_PLAN + 1)
            stderr.seek(0)
            error = stderr.read(4097)
    if result.returncode or error or len(encoded) > MAX_PROJECT_PLAN:
        detail = error.decode('utf-8', errors='replace').strip() if len(error) <= 4096 else 'inspection error exceeds bound'
        raise Refused('Installed project inspection failed' + (': ' + detail if detail else ''))
    try:
        report = json.loads(encoded)
    except (ValueError, UnicodeDecodeError):
        raise Refused('Installed project inspection did not return a JSON review draft') from None
    return report


def _review_binding(report, installed, args, reader):
    if not isinstance(report, dict):
        raise Refused('Installed project inspection lacks a source-bound review draft')
    observations, plan = report.get('observations'), report.get('projectPlan')
    target = {'checkout': str(reader.checkout), 'commit': reader.commit, 'tree': reader.tree}
    control = {'version': installed['version'], 'manifestSha256': installed['manifestSha256']}
    git = {'path': reader.git, 'sha256': args.git_sha256}
    if (report.get('status') != 'draft-for-operator-review' or report.get('executionAuthorized') is not False
            or not isinstance(observations, dict) or observations.get('executionAuthorized') is not False
            or observations.get('status') != 'observed-only'
            or observations.get('control') != control or observations.get('target') != target
            or observations.get('git') != git
            or not isinstance(plan, dict) or plan.get('executionAuthorized') is not False
            or plan.get('target') != target or plan.get('status') != 'draft-for-agent-and-operator-review'):
        raise Refused('Installed project inspection differs from the final source and controller binding')
    encoded = json.dumps(observations, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    if report.get('observationSha256') != control_package.sha256(encoded):
        raise Refused('Installed project observation SHA-256 mismatch')
    return target, git


def verify_project_plan(root, args, reader, *, adaptation=None):
    """Consume a frozen install receipt as provenance, never execution authority."""
    path, pin = getattr(args, 'project_plan', None), getattr(args, 'project_plan_sha256', None)
    if path is None and pin is None:
        return None
    if path is None or pin is None:
        raise Refused('Project plan receipt and SHA-256 must be supplied together')
    root, path = _canonical(root), _canonical(path)
    if any(_overlaps(path, other) for other in (root, reader.checkout)):
        raise Refused('Project plan receipt must be outside the controller and checkout')
    encoded = read_owned(path.parent, path.name, MAX_PROJECT_PLAN)
    control_package.check_digest(encoded, pin, 'Project plan receipt')
    try:
        receipt = unique_json(encoded)
    except (ValueError, UnicodeDecodeError):
        raise Refused('Invalid project plan receipt JSON') from None
    required = {'format', 'schemaVersion', 'status', 'ready', 'executionAuthorized',
                'control', 'target', 'git', 'selection', 'adaptation', 'next'}
    if (not isinstance(receipt, dict) or set(receipt) != required or receipt['format'] != FORMAT
            or type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
            or receipt['status'] != 'installed-awaiting-project-review'
            or receipt['ready'] is not False or receipt['executionAuthorized'] is not False):
        raise Refused('Expected a frozen project installation review receipt')
    manifest = control_package.verify_installation(root, args.manifest_sha256)
    control = receipt['control']
    if (not isinstance(control, dict) or set(control) != {'installation', 'version', 'archiveSha256', 'manifestSha256'}
            or control['installation'] != str(root) or control['version'] != manifest['version']
            or control['manifestSha256'] != args.manifest_sha256
            or not isinstance(control['archiveSha256'], str) or not re.fullmatch('[a-f0-9]{64}', control['archiveSha256'])):
        raise Refused('Project plan receipt differs from the current controller installation')
    target, git = _review_binding(receipt['adaptation'], control, args, reader)
    selection = {'projectDocuments': args.project_doc, 'testScript': args.test_script,
                 'executionKind': getattr(args, 'execution_kind', None),
                 'sourcePaths': sorted(getattr(args, 'source_path', []))}
    if receipt['target'] != target or receipt['git'] != git or receipt['selection'] != selection:
        raise Refused('Project plan receipt target, Git or explicit selection differs; review a new installation plan')
    if adaptation is None:
        from .project_adaptation import propose_adaptation
        adaptation = propose_adaptation(root, args, manifest, reader=reader)
    if receipt['adaptation'] != adaptation:
        raise Refused('Frozen project adaptation differs from the current source-bound draft')
    reader.verify_head()
    control_package.verify_installation(root, args.manifest_sha256)
    plan = json.dumps(adaptation['projectPlan'], sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return {'path': str(path), 'sha256': pin, 'control': control, 'target': target, 'git': git,
            'selection': selection, 'projectPlanSha256': control_package.sha256(plan),
            'status': 'verified-review-draft-only', 'executionAuthorized': False}


def install_project(root, args):
    checkout, destination, output = _paths(root, args)
    members, manifest = control_package.verify(args.archive, args.sha256)
    if MODULE not in manifest['files']:
        raise Refused('Controller archive predates project-aware installation; use ordinary install for that archive')
    # Refuse a bad source or executable pin before creating the installation.
    reader = CommitReader(destination, checkout, args.git, args.git_sha256, args.commit)
    manifest_pin = control_package.sha256(members[control_package.MANIFEST])
    installed = control_package.install(args.archive, args.sha256, destination)
    try:
        if installed['manifestSha256'] != manifest_pin:
            raise Refused('Installed controller differs from the inspected archive')
        control_package.verify_installation(destination, manifest_pin)
        report = _installed_adaptation(destination, args, manifest_pin)
        # Re-pin tool bytes, HEAD and tree after the installed inspection. A
        # changed checkout or controller cannot receive a completed receipt.
        final_reader = CommitReader(destination, checkout, args.git, args.git_sha256, args.commit)
        if final_reader.tree != reader.tree:
            raise Refused('Project tree changed during installation inspection')
        target, git = _review_binding(report, installed, args, final_reader)
        control_package.verify_installation(destination, manifest_pin)
        receipt = {
            'format': FORMAT, 'schemaVersion': 1, 'status': 'installed-awaiting-project-review',
            'ready': False, 'executionAuthorized': False,
            'control': {'installation': str(destination), 'version': installed['version'],
                        'archiveSha256': args.sha256, 'manifestSha256': manifest_pin},
            'target': target, 'git': git,
            'selection': {'projectDocuments': args.project_doc, 'testScript': args.test_script,
                          'executionKind': getattr(args, 'execution_kind', None),
                          'sourcePaths': sorted(getattr(args, 'source_path', []))},
            'adaptation': report,
            'next': {'status': 'pending-review', 'required': [
                'Review committed CI, build definitions, script lifecycles and unresolved observations.',
                'Select source inputs, command order, dependencies, artifacts and independent checks.',
                'Qualify an execution contract and obtain separate approval of its exact effects.']},
        }
        encoded = (json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
        if len(encoded) > MAX_PROJECT_PLAN:
            raise Refused('Project installation receipt exceeds the size bound')
        write_owned(output.parent, output.name, encoded, exclusive=True)
        if read_owned(output.parent, output.name, MAX_PROJECT_PLAN) != encoded:
            raise Refused('Project installation receipt changed while being written')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, Refused) else type(error).__name__
        raise Refused(f'Controller retained at {destination}; project installation is incomplete: {detail}') from None
    return {**installed, 'archiveSha256': args.sha256, 'mode': 'installed-project-review-required',
            'ready': False, 'executionAuthorized': False,
            'projectPlan': {'path': str(output), 'sha256': control_package.sha256(encoded),
                            'status': receipt['status'], 'target': target}}
