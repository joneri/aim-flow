#!/usr/bin/env python3
"""Consume an opted-in GitHub Issue through API implementation/review and CI/CD."""
import argparse
import json
from pathlib import Path
import sys
import time

if __name__ == '__main__':
    sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from openai_budget import Refused
from aim_ci.controller import STATE, github, resume_deployment, run_issue, target_checkout
from aim_ci.control_package import add_root_preview_input, require_source_checkout, root_preview_input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--issue', type=int, required=True)
    watch = sub.add_parser('watch')
    watch.add_argument('--once', action='store_true')
    for worker in (run, watch):
        worker.add_argument('--product', default='fakeatg', help='Reviewed built-in product profile')
        worker.add_argument('--checkout', type=Path, help='Existing checkout of the selected product repository')
    recovery = sub.add_parser('resume-deployment')
    recovery.add_argument('--issue', type=int, required=True)
    recovery_mode = recovery.add_mutually_exclusive_group()
    recovery_mode.add_argument('--reconcile', action='store_true')
    recovery_mode.add_argument('--observe', action='store_true')
    status = sub.add_parser('status')
    status.add_argument('--diagnostics', action='store_true')
    status.add_argument('--state-dir', type=Path)
    status.add_argument('--legacy-budget', type=Path)
    qualification = sub.add_parser('qualify-offline')
    qualification.add_argument('--output', type=Path, required=True)
    fault_case = sub.add_parser('offline-case')
    fault_case.add_argument('--output', type=Path, required=True)
    fault_case.add_argument('--fault', required=True)
    resume_case = sub.add_parser('resume-offline')
    resume_case.add_argument('--output', type=Path, required=True)
    human = sub.add_parser('prepare-human-offline')
    human.add_argument('--output', type=Path, required=True)
    human.add_argument('--product', choices=['fakeatg', 'catalog'], default='catalog')
    human.add_argument('--reviewer', required=True)
    human_resume = sub.add_parser('resume-human-offline')
    human_resume.add_argument('--output', type=Path, required=True)
    human_resume.add_argument('--decision', type=Path)
    root_preview = sub.add_parser('preview-root-admission', help='Inspect a local root project and its exact intake without transport.')
    root_preview.add_argument('--profile', type=Path, required=True)
    root_preview.add_argument('--profile-sha256', required=True)
    add_root_preview_input(root_preview)
    local_ci = sub.add_parser('local-ci')
    local_ci.add_argument('--output', type=Path, required=True)
    verify_ci = sub.add_parser('verify-local-ci')
    verify_ci.add_argument('--bundle', type=Path, required=True)
    verify_ci.add_argument('--evidence-sha256', required=True)
    for command in (local_ci, verify_ci):
        command.add_argument('--product', choices=['fakeatg', 'catalog'], required=True)
        command.add_argument('--checkout', type=Path, required=True)
        command.add_argument('--origin', type=Path, required=True)
        command.add_argument('--head', required=True)
    args = parser.parse_args()
    require_source_checkout(Path(__file__).resolve().parent)
    if args.command == 'preview-root-admission':
        from aim_ci.controller import preview_root_admission
        print(json.dumps(preview_root_admission((args.profile, args.profile_sha256),
                                                **root_preview_input(root_preview, args)), indent=2))
        return
    if args.command in ('local-ci', 'verify-local-ci'):
        from aim_ci.local_ci import package_local_ci, verify_local_ci
        target = (args.product, args.checkout, args.origin, args.head)
        result = (package_local_ci(*target, args.output) if args.command == 'local-ci'
                  else verify_local_ci(*target, args.bundle, args.evidence_sha256))
        print(json.dumps(result, indent=2))
        return
    if args.command in ('prepare-human-offline', 'resume-human-offline'):
        from aim_ci.offline import run_human_case
        run_human_case(args.output, resume=args.command == 'resume-human-offline',
                       product=getattr(args, 'product', None), reviewer=getattr(args, 'reviewer', None),
                       decision_file=getattr(args, 'decision', None))
        return
    if args.command == 'qualify-offline':
        from aim_ci.offline import qualify
        qualify(args.output)
        return
    if args.command in ('offline-case', 'resume-offline'):
        from aim_ci.offline import run_case
        run_case(args.output, fault=getattr(args, 'fault', None), resume=args.command == 'resume-offline')
        return
    if args.command == 'status':
        if args.diagnostics:
            from aim_ci.diagnostics import observe
            directory = args.state_dir if args.state_dir is not None else STATE
            if args.state_dir is not None and args.legacy_budget is None:
                parser.error('--state-dir requires --legacy-budget for diagnostics')
            legacy = args.legacy_budget if args.legacy_budget is not None else STATE.parent / 'openai-api-probe/budget.json'
            print(json.dumps(observe(directory, legacy), indent=2))
            return
        if args.state_dir is not None or args.legacy_budget is not None:
            parser.error('--state-dir/--legacy-budget require --diagnostics')
        print(json.dumps([json.loads(p.read_text()) for p in STATE.glob('issues/*/state.json')], indent=2))
        return
    if args.command == 'resume-deployment':
        resume_deployment(args.issue, args.reconcile, observe=args.observe)
        return
    with target_checkout(args.product, args.checkout) as config:
        if args.command == 'run':
            run_issue(args.issue, config)
            return
        while True:
            issues = github(f'repos/{config["repository"]}/issues?state=open&labels={config["label"]}&per_page=100')
            for issue in sorted(issues, key=lambda x: x['number']):
                try:
                    run_issue(issue['number'], config)
                except Refused as error:
                    print(json.dumps({'issue': issue['number'], 'stage': 'requires_assessment', 'reason': str(error)}), flush=True)
            if args.once:
                return
            time.sleep(30)


if __name__ == '__main__':
    try:
        main()
    except (Refused, OSError, ValueError) as error:
        print(str(error) if isinstance(error, Refused) else type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
