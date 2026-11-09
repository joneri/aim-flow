#!/usr/bin/env python3
"""Build the CI-tested static artifact; validate it before privileged deployment."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

if __name__ == '__main__':
    sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'lib'))
from openai_budget import Refused, safe_path, write_owned
from aim_ci.release import authorize_delivery
from aim_ci.artifacts import pack
from aim_ci.products import get_product, hosted_product_inputs
from aim_ci.control_package import require_source_checkout


def git(argv):
    return subprocess.check_output(['git', *argv], cwd=ROOT, text=True)


def build_artifact(product, head, output, base_path):
    safe_path(output)
    if output.exists():
        raise Refused('Artifact output must be new')
    # Tests and build scripts must not relabel dirty product bytes as HEAD.
    if git(['status', '--porcelain', '--', product.directory, 'lib', 'profiles',
            'aim-ci-release.py', '.github/workflows']).strip():
        raise Refused('CI source changed during validation; cannot build an exact-source artifact')
    with TemporaryDirectory() as directory:
        site = Path(directory).resolve() / 'site'
        subprocess.run(product.build_command(base_path=base_path, output=site),
                       cwd=ROOT / product.directory, check=True)
        if git(['rev-parse', 'HEAD']).strip() != head or git(['diff', '--name-only', head, '--', product.directory]).strip():
            raise Refused('Build changed tracked product source')
        release = product.metadata(site, head)
        write_owned(site, 'release.json', json.dumps(release, indent=2) + '\n')
        output.mkdir(parents=True)
        archive = output / 'aim-ci-site.tar.gz'
        pack(site, archive)
        (output / 'aim-ci-site.tar.gz.sha256').write_text(
            hashlib.sha256(archive.read_bytes()).hexdigest() + '  aim-ci-site.tar.gz\n')
    return {'sourceSha': head, 'contentSha256': release['contentSha256']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--head', required=True)
    build.add_argument('--output', type=Path, required=True)
    unpack = sub.add_parser('unpack')
    unpack.add_argument('--head', required=True)
    unpack.add_argument('--archive', type=Path, required=True)
    unpack.add_argument('--output', type=Path, required=True)
    gate = sub.add_parser('gate')
    gate.add_argument('--head', required=True)
    gate.add_argument('--recovery', action='store_true')
    inputs = sub.add_parser('hosted-inputs')
    for command in (build, unpack, gate, inputs):
        command.add_argument('--product', default='catalog', help='Reviewed built-in product recipe')
    for command in (build, unpack):
        command.add_argument('--local', action='store_true', help='Local artifact verification only')
    args = p.parse_args()
    require_source_checkout(ROOT)
    if args.command == 'hosted-inputs':
        print(json.dumps(hosted_product_inputs(ROOT, args.product)))
        return
    if not re.fullmatch('[0-9a-f]{40}', args.head):
        raise Refused('Release requires an exact commit SHA')
    product = get_product(args.product)
    local = getattr(args, 'local', False)
    if not local and product.live_target is None:
        raise Refused('This registered profile is qualified offline only')
    if args.command == 'unpack':
        print(json.dumps(product.extract(args.archive, args.output, args.head)))
        return

    current = git(['rev-parse', 'HEAD']).strip()
    if current != args.head:
        if args.command != 'gate' or not args.recovery:
            raise Refused('Release helper checkout does not match source')
        # Recovery uses current reviewed control code with the original immutable
        # product artifact. It must never revive an older/different product tree.
        result = subprocess.run(['git', 'merge-base', '--is-ancestor', args.head, current], cwd=ROOT)
        original_tree = git(['rev-parse', args.head + ':' + product.directory]).strip()
        current_tree = git(['rev-parse', current + ':' + product.directory]).strip()
        if result.returncode or original_tree != current_tree:
            raise Refused('Recovery requires an ancestor with the identical complete product tree')
    tree = git(['ls-tree', '--full-tree', args.head, '--', product.directory])
    if not tree.startswith('040000 tree '):
        raise Refused('Release source does not contain the configured product tree')

    if args.command == 'build':
        base_path = urlsplit(product.live_target).path if product.live_target else '/'
        print(json.dumps(build_artifact(product, args.head, args.output, base_path)))
    else:
        from aim_ci.controller import profile
        config = profile(product.name)

        def github(endpoint):
            return json.loads(subprocess.check_output(['gh', 'api', endpoint], text=True))

        result = authorize_delivery(config, args.head, github, git)
        print(json.dumps({**result, 'controlSha': current, 'recovery': current != args.head}))



if __name__ == '__main__':
    try:
        main()
    except (Refused, ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, Refused) else type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
