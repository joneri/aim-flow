"""Standard-library archive verification shared by the controller and bootstrap."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import zlib

from aim_ci.owned_paths import Refused, new_owned_directory, owned_mkdir, read_owned, write_owned


MANIFEST = 'control-manifest.json'
FORMAT = 'aim-ci-control'
MIN_RUNTIME_PYTHON = (3, 11)
RUNTIME_PYTHON_REQUIREMENT = '.'.join(map(str, MIN_RUNTIME_PYTHON))
MAX_FILE = 2_000_000
MAX_ARCHIVE = 8_000_000
VERSION_PATTERN = r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*)?'
SCHEMA_VERSION = 3
FILES = (
    'aim-flow.py',
    'lib/flow_api_delivery.py',
    'lib/flow_onboarding_delivery.py',
    'docs/API-DELIVERY.md',
    'aim-ci.py',
    'aim-ci-control.py',
    'aim-ci-release.py',
    'docs/aim-ci-install.md',
    'docs/aim-ci-discover-project.md',
    'docs/aim-ci-plan-recipe.md',
    'docs/aim-ci-propose-adaptation.md',
    'lib/openai_budget.py',
    'lib/review-policy.sh',
    'lib/aim_ci/__init__.py',
    'lib/aim_ci/artifacts.py',
    'lib/aim_ci/budget.py',
    'lib/aim_ci/contracts.py',
    'lib/aim_ci/control_package.py',
    'lib/aim_ci/controller.py',
    'lib/aim_ci/diagnostics.py',
    'lib/aim_ci/effects.py',
    'lib/aim_ci/human_review.py',
    'lib/aim_ci/installed_runtime.py',
    'lib/aim_ci/local_ci.py',
    'lib/aim_ci/offline.py',
    'lib/aim_ci/offline_assignment.py',
    'lib/aim_ci/products.py',
    'lib/aim_ci/project_adaptation.py',
    'lib/aim_ci/project_discovery.py',
    'lib/aim_ci/recipe_plan.py',
    'lib/aim_ci/release.py',
    'profiles/catalog.json',
    'fixtures/products/catalog-oracle.py',
    'fixtures/products/catalog/catalog.py',
    'fixtures/products/catalog/tests/test_catalog.py',
    'fixtures/products/catalog/fixtures/items.csv',
    'tests/production/artifact-probe.mjs',
    'tests/production/catalog-verify.mjs',
    'lib/aim_ci/control_archive.py',
    'lib/aim_ci/owned_paths.py',
    'docs/aim-ci-onboarding.md',
    'lib/aim_ci/docker_engine.py',
    'lib/aim_ci/docker_image_evidence.py',
    'lib/aim_ci/npm_platform.py',
    'lib/aim_ci/npm_platform_build.cjs',
    'lib/aim_ci/platform_build.py',
    'lib/aim_ci/operator_build.py',
    'lib/aim_ci/build_preparation.py',
    'lib/aim_ci/image_preparation.py',
    'lib/aim_ci/npm_acquisition.py',
    'lib/aim_ci/operator_readiness.py',
    'lib/aim_ci/project_installation.py',
    'lib/aim_ci/project_onboarding.py',
    'docs/PROJECT-ONBOARDING.md',
    'lib/aim_ci/source_subset.py',
    'lib/aim_ci/model_support.py',
)
MAX_EXPANDED_ARCHIVE = MAX_FILE * (len(FILES) + 1) + 256_000


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def check_digest(data, expected, label):
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected) or sha256(data) != expected:
        raise Refused(f'{label} SHA-256 mismatch')


def parse_manifest(encoded):
    manifest = json.loads(encoded)
    if (not isinstance(manifest, dict) or set(manifest) != {'format', 'schemaVersion', 'version', 'files'}
            or manifest['format'] != FORMAT or type(manifest['schemaVersion']) is not int
            or manifest['schemaVersion'] != SCHEMA_VERSION
            or not isinstance(manifest['version'], str) or not re.fullmatch(VERSION_PATTERN, manifest['version'])
            or not isinstance(manifest['files'], dict)
            or set(manifest['files']) != set(FILES)
            or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
                   for value in manifest['files'].values())):
        raise Refused('Invalid controller package manifest')
    return manifest


def verify(archive, expected):
    data = read_owned(archive.parent, archive.name, MAX_ARCHIVE)
    check_digest(data, expected, 'Controller archive')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            expanded = stream.read(MAX_EXPANDED_ARCHIVE + 1)
    except (OSError, EOFError, zlib.error):
        raise Refused('Malformed compressed controller archive') from None
    if len(expanded) > MAX_EXPANDED_ARCHIVE:
        raise Refused('Expanded controller archive exceeds the size limit')
    allowed = {MANIFEST, *FILES}
    members = {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as stream:
        for member in stream:
            if (member.name not in allowed or member.name in members or member.type != tarfile.REGTYPE
                    or not 0 <= member.size <= MAX_FILE):
                raise Refused('Unexpected, duplicate or unsafe archive member')
            with stream.extractfile(member) as file:
                members[member.name] = file.read()
    if MANIFEST not in members:
        raise Refused('Controller archive lacks its manifest')
    manifest = parse_manifest(members[MANIFEST])
    if set(members) != {MANIFEST, *manifest['files']}:
        raise Refused('Controller archive members differ from the manifest')
    for name, digest in manifest['files'].items():
        check_digest(members[name], digest, name)
    return members, manifest


def verification_receipt(members, manifest, archive_pin):
    return {'verified': True, 'version': manifest['version'], 'archiveSha256': archive_pin,
            'manifestSha256': sha256(members[MANIFEST])}


def install(archive, expected, output):
    members, manifest = verify(archive, expected)
    output = output.absolute()
    with new_owned_directory(output) as directory:
        # Establish the manifest first. A partial installation cannot verify.
        write_owned(directory, MANIFEST, members[MANIFEST], exclusive=True)
        for name in manifest['files']:
            owned_mkdir(directory, Path(name).parent)
            write_owned(directory, name, members[name], exclusive=True)
    return {'version': manifest['version'], 'manifestSha256': sha256(members[MANIFEST]),
            'installed': str(output), 'mode': 'installed-offline-capable'}


def main():
    parser = argparse.ArgumentParser(description='Verify or install a pinned local AIM CI archive.')
    commands = parser.add_subparsers(dest='action', required=True)
    verify_parser = commands.add_parser('verify')
    install_parser = commands.add_parser('install')
    for command in (verify_parser, install_parser):
        command.add_argument('--archive', type=Path, required=True)
        command.add_argument('--sha256', required=True)
    install_parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info < MIN_RUNTIME_PYTHON:
        raise Refused(f'Bootstrap requires Python {RUNTIME_PYTHON_REQUIREMENT} or newer')
    if os.name != 'posix':
        raise Refused('Bootstrap requires a POSIX filesystem with descriptor-relative operations')
    if args.action == 'verify':
        members, manifest = verify(args.archive, args.sha256)
        receipt = verification_receipt(members, manifest, args.sha256)
    else:
        receipt = install(args.archive, args.sha256, args.destination)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, EOFError, ValueError, tarfile.TarError, zlib.error) as error:
        print(str(error) if isinstance(error, Refused) else type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
