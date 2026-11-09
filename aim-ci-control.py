#!/usr/bin/env python3
"""Build, verify and run an isolated offline AIM CI control installation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'lib'))
from openai_budget import Refused
from aim_ci import control_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    build = commands.add_parser('build')
    build.add_argument('--version', required=True)
    build.add_argument('--output', type=Path, required=True)
    release = commands.add_parser('build-release')
    release.add_argument('--version', required=True)
    release.add_argument('--output', type=Path, required=True)
    candidate = commands.add_parser('build-development-candidate', description='Package and verify one exact-commit development candidate.')
    candidate.add_argument('--output', type=Path, required=True)
    for option in ('commit', 'repository', 'run-id', 'run-attempt', 'workflow-ref', 'workflow-sha'):
        candidate.add_argument('--' + option, required=True)
    verify = commands.add_parser('verify')
    install = commands.add_parser('install')
    install.add_argument('--destination', type=Path, required=True)
    install_project = commands.add_parser('install-project',
        description='Install a pinned controller and retain a source-bound project review draft without running project code.')
    install_project.add_argument('--destination', type=Path, required=True)
    install_project.add_argument('--project-plan-output', type=Path, required=True,
                                 help='New receipt file outside the controller and project checkout; retain its reported SHA-256 separately')
    for command in (verify, install, install_project):
        command.add_argument('--archive', type=Path, required=True)
        command.add_argument('--sha256', required=True)
    readiness = commands.add_parser('readiness')
    readiness.add_argument('--manifest-sha256', required=True)
    readiness_mode = readiness.add_mutually_exclusive_group()
    readiness_mode.add_argument('--product', choices=['catalog'])
    readiness_mode.add_argument('--workflow', choices=['preparation', 'acquisition', 'build'])
    readiness.add_argument('--checkout', type=Path)
    readiness.add_argument('--config', type=Path)
    readiness.add_argument('--config-sha256')
    readiness.add_argument('--git-sha256', help='Independently verified Git SHA-256 for optional local source inspection')
    discover = commands.add_parser('discover-project')
    root_preview = commands.add_parser('preview-root-admission',
                                       description='Preview exact local root intake without transport.')
    for option in ('profile', 'state-dir', 'legacy-budget'):
        root_preview.add_argument('--' + option, type=Path, required=True)
    for option in ('manifest-sha256', 'profile-sha256'):
        root_preview.add_argument('--' + option, required=True)
    control_package.add_root_preview_input(root_preview)
    local_intake = commands.add_parser('run-local-intake',
        description='Preview or explicitly authorize one separately granted installed local task intake.')
    intake_modes = local_intake.add_mutually_exclusive_group(required=True)
    intake_modes.add_argument('--preview', action='store_true')
    intake_modes.add_argument('--authorize-intent')
    local_intake.add_argument('--resume', action='store_true',
                              help='Read the same retained intake without a key read or provider replay')
    for option in ('profile', 'task-file', 'key-file', 'state-dir', 'legacy-budget'):
        local_intake.add_argument('--' + option, type=Path, required=True)
    local_intake.add_argument('--live-grant', type=Path,
                              help='Optional for preview, required for dispatch; separately approved version 3 grant')
    local_intake.add_argument('--live-grant-sha256')
    local_intake.add_argument('--output-tokens', type=int,
                              help='Positive output-token allowance bound to this exact intake, grant and consent; default 8192')
    for option in ('manifest-sha256', 'profile-sha256', 'task-sha256'):
        local_intake.add_argument('--' + option, required=True)
    local_candidate = commands.add_parser('prepare-local-candidate',
        description='Preview or separately authorize an isolated private candidate from an independently reviewed retained intake.')
    candidate_modes = local_candidate.add_mutually_exclusive_group(required=True)
    candidate_modes.add_argument('--preview', action='store_true')
    candidate_modes.add_argument('--authorize-intent')
    candidate_modes.add_argument('--withdraw-intent')
    local_candidate.add_argument('--resume', action='store_true', help='Reconcile the retained preparation without replay')
    for option in ('profile', 'task-file', 'retained-intake', 'state-dir', 'legacy-budget'):
        local_candidate.add_argument('--' + option, type=Path, required=True)
    for option in ('manifest-sha256', 'profile-sha256', 'task-sha256', 'retained-intake-sha256'):
        local_candidate.add_argument('--' + option, required=True)
    adapt = commands.add_parser('propose-adaptation')
    adapt.add_argument('--npm-docs', type=Path)
    draft = commands.add_parser('draft-build-preparation', description='Draft an editable preparation configuration without effects.')
    draft.set_defaults(npm_docs=None)
    draft.add_argument('--project-plan', type=Path, help='Optional source-bound install-project receipt to consume')
    draft.add_argument('--project-plan-sha256', help='Independently retained SHA-256 of the exact project receipt')
    for command in (adapt, draft, install_project):
        command.add_argument('--test-script')
        command.add_argument('--execution-kind', choices=['npm-source-check'],
                             help='Explicitly draft only a separately reviewed source check, without dependencies, build or artifacts')
        command.add_argument('--source-path', action='append', default=[],
                             help='Exact committed regular file for the source check; repeat for every selected file')
    for command in (discover, adapt, draft, install_project):
        command.add_argument('--project-doc', action='append', default=[])
        for option in ('checkout', 'git'):
            command.add_argument('--' + option, type=Path, required=True)
        options = ('git-sha256', 'commit') if command is install_project else ('manifest-sha256', 'git-sha256', 'commit')
        for option in options:
            command.add_argument('--' + option, required=True)
    plan = commands.add_parser('plan-recipe', description='Plan a reviewed npm build or source-check recipe without executing it.')
    plan.add_argument('--review', type=Path)
    plan.add_argument('--review-sha256')
    plan.add_argument('--container-profile', type=Path,
                      help='Separate Docker runtime and store description for recipe schemas v2/v3')
    plan.add_argument('--container-profile-sha256',
                      help='Externally supplied file SHA-256, bound by the approval and review')
    for option in ('recipe-package', 'approval', 'assignment', 'checkout'):
        plan.add_argument('--' + option, type=Path, required=True)
    for option in ('manifest-sha256', 'recipe-sha256', 'approval-sha256', 'assignment-sha256'):
        plan.add_argument('--' + option, required=True)
    check_execution = commands.add_parser('check-recipe-execution',
                                          description='Report readiness gaps without dispatching any execution.')
    check_execution.add_argument('--manifest-sha256', required=True)
    check_execution.add_argument('--plan', type=Path, required=True)
    check_execution.add_argument('--plan-sha256', required=True)
    run_build = commands.add_parser('run-reviewed-build', description='Run one explicitly approved local platform build.')
    status_build = commands.add_parser('status-reviewed-build', description='Read retained local build state without Docker.')
    modes = run_build.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preview', action='store_true')
    modes.add_argument('--authorize-intent')
    modes.add_argument('--resume', action='store_true')
    for command in (run_build, status_build):
        command.add_argument('--with-offline-test', action='store_true',
                             help='Select the reviewed named npm test requiring no browser, network or services.')
    for command in (run_build, status_build):
        command.add_argument('--config', type=Path, required=command is run_build)
        command.add_argument('--config-sha256', required=command is run_build)
    status_build.add_argument('--state', type=Path)
    status_build.add_argument('--intent-sha256')
    recover_build = commands.add_parser('cleanup-reviewed-build', description='Withdraw and clean up an exact retained build.')
    recover_build.add_argument('--state', type=Path, required=True)
    recover_build.add_argument('--intent-sha256', required=True)
    for command in (run_build, status_build, recover_build):
        command.add_argument('--manifest-sha256', required=True)
    prepare_build = commands.add_parser('prepare-reviewed-build', description='Prepare one reviewed local build image and plan.')
    prep_modes = prepare_build.add_mutually_exclusive_group(required=True)
    prep_modes.add_argument('--preview', action='store_true')
    prep_modes.add_argument('--authorize-intent')
    prep_modes.add_argument('--resume', action='store_true')
    prepare_build.add_argument('--config', type=Path, required=True)
    prepare_build.add_argument('--config-sha256', required=True)
    cleanup_prep = commands.add_parser('cleanup-build-preparation', description='Clean up the exact staging container and optionally finalize its image.')
    cleanup_prep.add_argument('--output', type=Path, required=True)
    cleanup_prep.add_argument('--intent-sha256', required=True)
    cleanup_prep.add_argument('--remove-image', metavar='IMAGE_ID')
    cleanup_prep.add_argument('--preview', action='store_true', help='Inspect a final image cleanup without Docker.')
    for command in (prepare_build, cleanup_prep):
        command.add_argument('--manifest-sha256', required=True)
    acquire = commands.add_parser('acquire-npm-tarballs', description='Acquire reviewed npm tarballs without installing them.')
    acquire_modes = acquire.add_mutually_exclusive_group(required=True)
    acquire_modes.add_argument('--preview', action='store_true')
    acquire_modes.add_argument('--authorize-intent')
    acquire_modes.add_argument('--resume', action='store_true')
    for option in ('config', 'state'):
        acquire.add_argument('--' + option, type=Path, required=True)
    for option in ('manifest-sha256', 'config-sha256'):
        acquire.add_argument('--' + option, required=True)
    acquire.add_argument('--locator')
    pin = commands.add_parser('pin-runtime')
    run = commands.add_parser('run-installed-offline')
    status = commands.add_parser('status-installed-offline')
    setup = commands.add_parser('setup-offline')
    setup.add_argument('--output', type=Path, required=True)
    setup.set_defaults(runtime_sha256=None)
    configured_commands = ('preflight-offline', 'run-configured-offline', 'status-configured-offline')
    for name in configured_commands:
        configured = commands.add_parser(name)
        configured.add_argument('--config', type=Path, required=True)
        configured.add_argument('--config-sha256', required=True)
    for command in (pin, run, status, setup):
        for option in ('runtime', 'node', 'git', 'chromium'):
            command.add_argument('--' + option, type=Path, required=True)
    for command in (run, status):
        command.add_argument('--runtime-sha256', required=True)
    for command in (run, status, setup):
        for option in ('manifest-sha256', 'head', 'product', 'assignment-sha256'):
            command.add_argument('--' + option, required=True)
        for option in ('checkout', 'origin', 'state', 'assignment'):
            command.add_argument('--' + option, type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'run-local-intake' and args.resume and args.preview:
        parser.error('--resume requires --authorize-intent and cannot be combined with --preview')
    if args.action == 'prepare-local-candidate' and args.resume and args.authorize_intent is None:
        parser.error('--resume requires --authorize-intent')
    if args.action == 'run-local-intake':
        if (args.live_grant is None) != (args.live_grant_sha256 is None):
            parser.error('--live-grant and --live-grant-sha256 must be supplied together')
        if not args.preview and args.live_grant is None:
            parser.error('--authorize-intent requires --live-grant and --live-grant-sha256')
    if args.action == 'draft-build-preparation' and (args.project_plan is None) != (args.project_plan_sha256 is None):
        parser.error('--project-plan and --project-plan-sha256 must be supplied together')
    if args.action in ('propose-adaptation', 'draft-build-preparation', 'install-project'):
        if args.execution_kind == 'npm-source-check':
            if args.test_script is None or not args.source_path:
                parser.error('--execution-kind npm-source-check requires explicit --test-script and --source-path selections')
        elif args.source_path:
            parser.error('--source-path requires --execution-kind npm-source-check')
    if args.action == 'readiness':
        if args.workflow:
            if args.checkout is not None or args.config is None or args.config_sha256 is None:
                parser.error('Project readiness requires --config and --config-sha256 and uses the checkout from the configuration')
            if args.workflow == 'build' and args.git_sha256 is not None:
                parser.error('Build readiness checks pinned planning files without Git')
        elif any(value is not None for value in (args.config, args.config_sha256, args.git_sha256)):
            parser.error('Configuration and Git pin options require --workflow')
    offline_commands = ('build-development-candidate', 'pin-runtime', 'run-installed-offline', 'status-installed-offline',
                        'setup-offline', 'plan-recipe', 'check-recipe-execution',
                        'discover-project', 'install-project', 'preview-root-admission', 'run-local-intake',
                        'prepare-local-candidate',
                        'propose-adaptation', 'run-reviewed-build',
                        'status-reviewed-build', 'cleanup-reviewed-build', 'prepare-reviewed-build',
                        'cleanup-build-preparation', 'draft-build-preparation', 'acquire-npm-tarballs', *configured_commands)
    if args.action in offline_commands and sys.version_info < control_package.MIN_RUNTIME_PYTHON:
        minimum = control_package.RUNTIME_PYTHON_REQUIREMENT
        raise Refused(f'{args.action} requires Python {minimum} or newer. '
                      f'Found Python {sys.version.split()[0]} at {sys.executable}.')
    if getattr(args, 'execution_kind', None) == 'npm-source-check':
        from aim_ci.source_subset import selected_paths
        args.source_path = selected_paths(args.source_path)
    if args.action == 'build':
        result = control_package.build(ROOT, args.output, args.version)
    elif args.action == 'build-release':
        result = control_package.build_release(ROOT, args.output, args.version)
    elif args.action == 'build-development-candidate':
        result = control_package.build_development_candidate(ROOT, args.output, commit=args.commit,
            repository=args.repository, run_id=args.run_id, run_attempt=args.run_attempt,
            workflow_ref=args.workflow_ref, workflow_sha=args.workflow_sha)
    elif args.action == 'verify':
        members, manifest = control_package.verify(args.archive, args.sha256)
        result = control_package.verification_receipt(members, manifest, args.sha256)
    elif args.action == 'install':
        result = control_package.install(args.archive, args.sha256, args.destination)
    elif args.action == 'install-project':
        from aim_ci.project_installation import install_project
        result = install_project(ROOT, args)
    elif args.action == 'discover-project':
        manifest = control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.project_discovery import discover_project
        result = discover_project(ROOT, args, manifest)
    elif args.action == 'preview-root-admission':
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.controller import installed_root_context, preview_root_admission
        with installed_root_context(args.manifest_sha256, args.state_dir, args.legacy_budget):
            result = preview_root_admission((args.profile, args.profile_sha256),
                                            **control_package.root_preview_input(root_preview, args))
    elif args.action == 'run-local-intake':
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.controller import installed_root_context, preview_root_admission, run_issue
        inputs = {'root_profile': (args.profile, args.profile_sha256),
                  'root_task': (args.task_file, args.task_sha256), 'intake_only': True,
                  'live_grant': ((args.live_grant, args.live_grant_sha256) if args.live_grant is not None else None),
                  'live_key_file': args.key_file,
                  **({'output_tokens': args.output_tokens} if args.output_tokens is not None else {})}
        with installed_root_context(args.manifest_sha256, args.state_dir, args.legacy_budget):
            if args.preview:
                result = preview_root_admission(**inputs)
            else:
                result = run_issue(**inputs, authorize_root_intent=args.authorize_intent, resume=args.resume)
    elif args.action == 'prepare-local-candidate':
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.controller import installed_root_context, preview_intake_candidate, run_issue
        inputs = {'root_profile': (args.profile, args.profile_sha256),
                  'root_task': (args.task_file, args.task_sha256),
                  'retained_intake': (args.retained_intake, args.retained_intake_sha256)}
        with installed_root_context(args.manifest_sha256, args.state_dir, args.legacy_budget):
            if args.preview:
                result = preview_intake_candidate(**inputs)
            else:
                result = run_issue(**inputs, candidate_action='withdraw' if args.withdraw_intent else 'prepare',
                                   authorize_candidate_intent=args.withdraw_intent or args.authorize_intent,
                                   resume=args.resume)
    elif args.action == 'propose-adaptation':
        manifest = control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.project_adaptation import propose_adaptation
        result = propose_adaptation(ROOT, args, manifest)
    elif args.action == 'draft-build-preparation':
        manifest = control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.build_preparation import draft_build_preparation
        result = draft_build_preparation(ROOT, args, manifest)
    elif args.action == 'plan-recipe':
        manifest = control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.recipe_plan import plan_recipe
        result = plan_recipe(ROOT, args, manifest)
    elif args.action == 'check-recipe-execution':
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.recipe_plan import check_recipe_execution
        print(json.dumps(check_recipe_execution(args.plan, args.plan_sha256, args.manifest_sha256), indent=2))
        return 3
    elif args.action in ('run-reviewed-build', 'status-reviewed-build', 'cleanup-reviewed-build'):
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.operator_build import operator_build
        try:
            result, code = operator_build(ROOT, args)
        except KeyboardInterrupt:
            print('Interrupted. Use status-reviewed-build and the original configuration or state.', file=sys.stderr)
            return 130
        print(json.dumps(result, indent=2))
        return code
    elif args.action in ('prepare-reviewed-build', 'cleanup-build-preparation'):
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.build_preparation import prepare_reviewed_build
        result, code = prepare_reviewed_build(ROOT, args)
        print(json.dumps(result, indent=2))
        return code
    elif args.action == 'acquire-npm-tarballs':
        control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.npm_acquisition import NpmAcquisition
        try:
            result = NpmAcquisition(ROOT, args).run()
        except KeyboardInterrupt:
            print('Acquisition interrupted. Retain state and use --resume with the original inputs.', file=sys.stderr)
            return 130
    elif args.action == 'pin-runtime':
        from aim_ci.installed_runtime import runtime_identity
        result = runtime_identity(args.runtime, args.node, args.git, args.chromium)
    elif args.action in ('setup-offline', 'run-installed-offline', 'status-installed-offline'):
        manifest = control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.installed_runtime import run_installed, setup_offline
        from aim_ci.offline_assignment import load_assignment
        args.assignment = load_assignment(args.assignment, args.assignment_sha256)
        if args.action == 'setup-offline':
            result = setup_offline(ROOT, args, manifest)
        else:
            result = run_installed(ROOT, args, manifest, status=args.action == 'status-installed-offline')
    elif args.action in configured_commands:
        config = control_package.load_offline_configuration(args.config, args.config_sha256)
        manifest = control_package.verify_installation(ROOT, config['inputs']['manifest_sha256'])
        from aim_ci.installed_runtime import configured_offline
        result = configured_offline(ROOT, config, manifest, args.action)
    elif args.action == 'readiness' and args.workflow:
        if sys.version_info < control_package.MIN_RUNTIME_PYTHON:
            raise Refused(f'Project readiness requires Python {control_package.RUNTIME_PYTHON_REQUIREMENT} or newer')
        manifest = control_package.verify_installation(ROOT, args.manifest_sha256)
        from aim_ci.operator_readiness import operator_readiness
        result, code = operator_readiness(ROOT, args, manifest)
        print(json.dumps(result, indent=2))
        return code
    else:
        result = control_package.readiness(ROOT, args.manifest_sha256, args.product or 'catalog', args.checkout)
    print(json.dumps(result, indent=2))
    return 3 if args.action == 'readiness' and not result['ready'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Refused, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(str(error) if isinstance(error, Refused) else type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
