"""Read-only local-operator input assessment for the installed readiness command."""
import os
import subprocess

from openai_budget import Refused, fingerprint
from .build_preparation import (PREPARATION_RECIPE_FIELDS, BuildPreparation, PreparationInputs, UnsupportedPreparationScope,
                                checkout_labels, dependency_inventory, preparation_configuration_fields, preparation_npm,
                                source_binding, source_manifest)
from .npm_acquisition import registry_path
from .npm_platform import UnsupportedNpmPlatform, validate_platform
from .operator_build import (BUILD_CONFIGURATION_FIELDS, OperatorBuild, PinnedInputMismatch,
                             canonical_path, owned_input, validate_directory)
from .platform_build import report_json
from .project_discovery import CommitReader, SourceBindingMismatch, unique_json
from .recipe_plan import digest, validate_target, validate_test_selector, verify_tools


_SOURCE_FIELDS = ('checkout', 'target', 'git')
_ACQUISITION_FIELDS = ('version', 'manifestSha256', *_SOURCE_FIELDS, 'source', 'platform', 'tarballsDir')
_RECORD_FIELDS = {'target': 'repository branch commit', 'git': 'path sha256',
                  'source': 'tree sourceManifestSha256 committedInputs',
                  'source.committedInputs': 'package.json package-lock.json', 'platform': 'os cpu libc',
                  'plan': 'path sha256', 'containerProfile': 'path sha256', 'dockerPolicy': 'path sha256',
                  'recipe': PREPARATION_RECIPE_FIELDS}
_RUNTIME = ('dockerDaemon', 'imageContents', 'isolation', 'network', 'dependencyInstallation',
            'build', 'browser', 'productAcceptance')


def _unset(value, path, *, source_check=False):
    if value is None:
        return [path]
    if isinstance(value, dict):
        names = _RECORD_FIELDS.get(path, '').split()
        if source_check and path == 'source':
            names = 'tree sourceSelectionSha256 sourceProofSha256 committedInputs'.split()
        elif source_check and path == 'recipe':
            names += ['sourcePaths', 'dependencies']
        return [missing for name in sorted(set(value)|set(names))
                for missing in _unset(value.get(name), f'{path}.{name}', source_check=source_check)]
    if isinstance(value, list):
        return [missing for i, item in enumerate(value)
                for missing in _unset(item, f'{path}[{i}]', source_check=source_check)]
    return []


def _command(args, command, *options):
    return {'command': command, 'arguments': [*options, '--manifest-sha256', args.manifest_sha256,
            '--config', str(args.config), '--config-sha256', args.config_sha256]}


def _decision(text):
    return {'decision': text}


def _configuration(config, args):
    if args.workflow == 'build':
        if not isinstance(config, dict) or type(config.get('version')) is not int or config['version'] != 1:
            raise Refused('Expected an operator build configuration version 1')
        names = BUILD_CONFIGURATION_FIELDS.split()
    else:
        names = preparation_configuration_fields(config).split()
    if set(config).difference(names):
        raise Refused('Unexpected configuration fields. For a preparation draft, use configurationDraft itself.')
    if config.get('manifestSha256') not in (None, args.manifest_sha256):
        raise PinnedInputMismatch('Configuration refers to another installed manifest')
    source_check = isinstance(config.get('recipe'), dict) and config['recipe'].get('stack') == 'npm-source-check'
    if source_check and args.workflow == 'acquisition':
        raise Refused('Source checks do not acquire or install dependencies')
    if source_check and config.get('tarballsDir') is not None:
        raise Refused('Source check preparation requires no local tarball directory')
    for key, record in _RECORD_FIELDS.items():
        if key == 'recipe' and args.workflow != 'preparation':
            continue
        if key == 'source.committedInputs':
            value = config['source'].get('committedInputs') if config.get('source') is not None else None
        else:
            value = config.get(key)
        allowed = record.split()
        if source_check and key == 'source':
            allowed = 'tree sourceSelectionSha256 sourceProofSha256 committedInputs'.split()
        if key == 'recipe':
            allowed.extend(['sourcePaths', 'dependencies'] if source_check else ['testSelector'])
        if value is not None and (not isinstance(value, dict) or set(value).difference(allowed)):
            raise Refused(f'Invalid {key} fields in the configuration')
        if key == 'recipe' and isinstance(value, dict) and 'testSelector' in value:
            validate_test_selector(value['testSelector'])
        if value is not None and value.get('sha256') is not None and not digest(value['sha256']):
            raise Refused(f'Invalid {key} SHA-256 in the configuration')
    if config.get('platform') is not None and not _unset(config['platform'], 'platform'):
        validate_platform(config['platform'])
    if config.get('target') is not None and not _unset(config['target'], 'target'):
        validate_target(config['target'])
    required = [name for name in names if args.workflow != 'acquisition' or name in _ACQUISITION_FIELDS]
    return [path for name in required if not (source_check and name == 'tarballsDir' and config.get(name) is None)
            for path in _unset(config.get(name), name, source_check=source_check)]


def _build(root, args, report):
    checked = OperatorBuild(root, args.config, args.config_sha256, args.manifest_sha256)
    report['source'] = {'status': 'pinned-planning-records', 'target': checked.intent['target'],
                        'checkoutCompared': False,
                        'committedInputs': checked.intent['binding']['committedInputs']}
    report['dependencies'] = {'status': 'declared-image-store', 'fullLockVerified': False,
                              'indexSha256': checked.intent['dependencyStore']['indexSha256']}
    exists = os.path.lexists(checked.state)
    report['status'] = 'retained-state-present' if exists else 'preview-available'
    report['next'] = (_command(args, 'status-reviewed-build') if exists else
                      _command(args, 'run-reviewed-build', '--preview'))


def _preparation(root, args, config, report):
    source_check = isinstance(config.get('recipe'), dict) and config['recipe'].get('stack') == 'npm-source-check'
    source_names = _SOURCE_FIELDS + (('source',) if config['version'] == 2 else ())
    source_unset = [path for name in (*source_names, 'manifestSha256')
                    for path in _unset(config.get(name), name, source_check=source_check)]
    if not _unset(config.get('target'), 'target'):
        report['source'] = {'status': 'declared-not-checked', 'target': config['target'], 'checkoutCompared': False}
    if source_unset:
        return
    if args.git_sha256 is None:
        if not report['unset']:
            report['status'] = 'source-not-checked'
            report['next'] = _command(args, 'readiness', '--workflow', args.workflow,
                                      '--git-sha256', '<independently-verified-git-sha256>')
        return
    if config['git']['sha256'] != args.git_sha256:
        raise PinnedInputMismatch('Configured Git SHA-256 differs from the independently supplied pin')
    complete_preparation = args.workflow == 'preparation' and not report['unset']
    if source_check and not complete_preparation:
        _source_check_draft(root, args, config, report)
        return
    supported_recipe = complete_preparation and config['recipe']['stack'] in ('npm-vite-static', 'npm-source-check')
    if complete_preparation and supported_recipe:
        checked = BuildPreparation(root, args)
        missing = checked.missing
    else:
        checked = PreparationInputs(root, args, acquisition=True, allow_unset_tarballs=True)
        missing = None
    report['source'] = {'status': 'observed-committed-source', 'checkoutCompared': True,
                        'target': checked.target, 'binding': checked.source_binding}
    if args.workflow == 'preparation' and missing is None:
        sources = [{'path': name, 'sha256': pin} for name, pin in checked.committed.items()]
        if complete_preparation:
            raise UnsupportedPreparationScope('The configured recipe stack has no supported preparation route.', sources)
        try:
            preparation_npm(checked.npm, {'version': 2, 'testScript': None}, allow_unselected_test=True)
        except Refused as error:
            raise UnsupportedPreparationScope(str(error), sources) from error
    if missing is None:
        if checked.tarballs is None or _unset(config.get('platform'), 'platform'):
            return
        if args.workflow == 'acquisition':
            validate_directory(checked.tarballs, private=True)
        _, _, _, missing = dependency_inventory(checked.tarballs, checked.lock, checked.committed['package-lock.json'],
                                                config['platform'])
    report['dependencies'] = {'status': 'not-required-no-install' if source_check else
                              'missing-platform-tarballs' if missing else 'local-platform-tarballs-verified',
                              'fullLockVerified': False, 'platform': config['platform'], 'missingTarballs': missing}
    if missing:
        supported, unsupported = [], []
        for row in missing:
            try:
                registry_path({**row, 'version': checked.lock['packages'][row['locator']]['version']})
            except Refused:
                unsupported.append(row['locator'])
            else:
                supported.append(row['locator'])
        report['dependencies']['unsupportedAcquisitionLocators'] = unsupported
        report['status'] = 'missing-platform-tarballs'
        if not supported:
            report['next'] = _decision('Supply local tarballs matching the listed locked SHA-512 values through an approved process.')
            return
        if set(config) != set(preparation_configuration_fields(config).split()):
            report['next'] = _decision('Restore the omitted fields from configurationDraft. Acquisition accepts unset runtime choices.')
            return
        try:
            validate_directory(checked.tarballs, private=True)
        except Refused:
            report['next'] = _decision('Choose an owned private tarball directory for acquisition and pin the edited configuration.')
            return
        selection = ('--locator', supported[0]) if unsupported else ()
        report['next'] = _command(args, 'acquire-npm-tarballs', '--preview', '--state', '<new-private-state>', *selection)
    elif args.workflow == 'acquisition':
        report['status'] = 'local-platform-tarballs-verified'
        report['next'] = _decision('Complete and review the preparation choices, pin the resulting configuration, and run prepare-reviewed-build --preview.')
    elif complete_preparation:
        if os.path.lexists(checked.output):
            report['status'] = 'retained-state-present'
            report['next'] = _decision('Inspect the existing preparation output against its original reviewed intent, or choose a new output path.')
        else:
            report['status'] = 'preview-available'
            report['next'] = _command(args, 'prepare-reviewed-build', '--preview')


def _source_check_draft(root, args, config, report):
    """Observe only the selected committed subset while runtime choices remain unset."""
    from .source_subset import selected_paths, source_subset

    recipe = config['recipe']
    if recipe.get('sourcePaths') is None or recipe.get('testScript') is None:
        return
    paths = selected_paths(recipe['sourcePaths'])
    if paths != recipe['sourcePaths'] or not {'package.json', 'package-lock.json'} <= set(paths):
        raise Refused('Source check readiness requires exact sorted selected npm inputs')
    checkout = canonical_path(config['checkout'], 'Checkout')
    git = canonical_path(config['git']['path'], 'Git executable')
    if any(git.is_relative_to(path) for path in (checkout, root)):
        raise Refused('Reviewed Git must be outside the checkout and installation')
    reader = CommitReader(root, checkout, git, config['git']['sha256'], config['target']['commit'])
    source, _, proof = source_subset(reader, paths)
    bound = source_binding(reader, source_manifest(source))
    bound.pop('sourceManifestSha256')
    bound.update(sourceSelectionSha256=fingerprint(source_manifest(source)), sourceProofSha256=fingerprint(proof))
    if config['version'] == 2 and config['source'] != bound:
        raise SourceBindingMismatch('Preparation source binding differs from the committed selection')
    if checkout_labels(reader) != {key: config['target'][key] for key in ('repository', 'branch')}:
        raise SourceBindingMismatch('Checkout origin or branch differs from the source-check draft')
    inputs = {name: unique_json(source[name]['data']) for name in ('package.json', 'package-lock.json')}
    preparation_npm(inputs, {'version': 3, 'testScript': recipe['testScript']})
    verify_tools({'git': config['git']}, 'git')
    reader.verify_head()
    report['source'] = {'status': 'observed-committed-source', 'checkoutCompared': True,
        'target': {**config['target'], 'checkout': str(checkout), 'tree': reader.tree}, 'binding': bound}
    report['dependencies'] = {'status': 'not-required-no-install', 'fullLockVerified': False,
                              'platform': config.get('platform'), 'missingTarballs': []}


def operator_readiness(root, args, manifest):
    report = {'version': 1, 'mode': 'local-operator', 'workflow': args.workflow,
              'package': {'verified': True, 'version': manifest['version'], 'manifestSha256': args.manifest_sha256},
              'ready': False, 'executionAuthorized': False, 'configSha256': args.config_sha256,
              'source': {'status': 'not-checked'}, 'dependencies': {'status': 'not-checked'},
              'runtime': dict.fromkeys(_RUNTIME, 'not-checked')}
    try:
        if args.git_sha256 is not None and not digest(args.git_sha256):
            raise Refused('Invalid independently supplied Git SHA-256')
        path = canonical_path(str(args.config), 'Readiness configuration')
        config = report_json(owned_input(path, args.config_sha256, 'Readiness configuration'))
        report['unset'] = _configuration(config, args)
        report['status'] = 'configuration-incomplete'
        report['next'] = _decision('Fill the listed configuration choices, then compute a new configuration SHA-256.')
        if args.workflow == 'build':
            if not report['unset']:
                _build(root, args, report)
        else:
            _preparation(root, args, config, report)
    except (PinnedInputMismatch, SourceBindingMismatch) as error:
        report.update(status='stale-input', detail=str(error),
                      next=_decision('Re-draft or recover the original pinned inputs. Review and pin the configuration before preview.'))
    except (UnsupportedPreparationScope, UnsupportedNpmPlatform) as error:
        report.update(status='unsupported-input', detail=str(error), sourceEvidence=getattr(error, 'sources', []),
                      next=_decision('Run discover-project and propose-adaptation for a reviewed project scope, or select a supported platform.'))
    except Refused as error:
        report.update(status='invalid-input', detail=str(error),
                      next=_decision('Review the named configuration field or local input before retrying readiness.'))
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.SubprocessError) as error:
        report.update(status='invalid-input', detail=f'Unable to read or parse a local input ({type(error).__name__}).',
                      next=_decision('Check the configuration format and the referenced local files.'))
    return report, 1 if report['status'] in ('stale-input', 'unsupported-input', 'invalid-input') else 3
