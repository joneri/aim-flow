"""Product CI using the same isolated recipes as the synthetic worker."""
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from openai_budget import Refused, fingerprint, read_owned, safe_path, write_owned
from . import controller as c
from .artifacts import pack
from .products import get_product, product_for


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def run_product_ci(product, repository, head, directory):
    def run_stage(folder, prefix, commands):
        root = directory / folder
        root.mkdir()
        candidate = root / 'candidate'
        project = product.stage(candidate, head, repository, c.command)
        frozen = fingerprint(c.probe.frozen_inputs(project))
        outcomes = c.probe.validate(candidate, commands, prefix, product=product.directory)
        logs = []
        for item in outcomes:
            log = read_owned(candidate, item['log'])
            item.update(logSha256=sha256(log), frozenInputsSha256=frozen)
            logs.append(log)
        write_owned(root, 'checks.json', json.dumps(outcomes, indent=2) + '\n')
        if (len(outcomes) != len(commands)
                or any(item['command'] != command or item['exitCode'] != 0 for item, command in zip(outcomes, commands))):
            raise Refused(f'Local product {folder} failed; see {candidate}')
        for log in logs:
            if folder == 'validation' and not log.strip():
                raise Refused('Product check produced no evidence')
            if product.name == 'catalog' and folder == 'validation' and not re.search(rb'^Ran [1-9][0-9]* tests? in ', log, re.MULTILINE):
                raise Refused('Catalog validation executed no tests')
        return candidate, outcomes

    _, validation = run_stage('validation', 'ci-validation', [list(command) for command in product.validation])
    build, build_outcomes = run_stage('fresh-build', 'ci-build', [product.build_command()])
    if build_outcomes[0]['frozenInputsSha256'] != validation[0]['frozenInputsSha256']:
        raise Refused('Build inputs differ from the validated product inputs')
    site = build / product.directory / 'dist'
    release = product.metadata(site, head)
    write_owned(site, 'release.json', json.dumps(release) + '\n')
    archive = directory / 'site.tar.gz'
    pack(site, archive)
    return {'artifact': str(archive), 'artifactSha256': sha256(archive.read_bytes()),
            'release': release, 'validation': validation, 'build': build_outcomes}


def local_identity(name, checkout, origin, head):
    product = get_product(name)
    profile_bytes = read_owned(c.ROOT, f'profiles/{name}.json')
    profile = json.loads(profile_bytes)
    if profile.get('version') != 1 or product_for(profile) != product:
        raise Refused('Local CI requires the registered product profile')
    if not re.fullmatch('[a-f0-9]{40}', head):
        raise Refused('Local CI requires an exact source SHA')
    checkout = checkout.resolve(strict=True)
    origin = origin.resolve(strict=True)

    def git(*args, cwd=checkout):
        return c.command(['git', '-c', 'core.fsmonitor=false', *args], cwd=cwd).strip()

    if git('rev-parse', '--show-toplevel') != str(checkout):
        raise Refused('Local CI requires a Git worktree root')
    for options in (('--all',), ('--push', '--all')):
        if git('remote', 'get-url', *options, 'origin') != str(origin):
            raise Refused('Local CI origin differs from the declared local repository')
    if git('rev-parse', '--is-bare-repository', cwd=origin) != 'true':
        raise Refused('Local CI origin must be a local bare repository')
    if git('rev-parse', 'HEAD') != head or git('cat-file', '-t', head, cwd=origin) != 'commit':
        raise Refused('Local CI source does not match checkout and origin')
    tree = git('ls-tree', '--full-tree', head, '--', product.directory)
    if not tree.startswith('040000 tree '):
        raise Refused('Local CI source lacks the selected product tree')
    if git('status', '--porcelain', '--', product.directory):
        raise Refused('Local CI requires a clean product checkout')
    identity = {'mode': 'local-product-ci', 'repository': origin.as_uri(), 'profileRepository': profile['repository'],
                'product': product.name, 'profileSha256': sha256(profile_bytes), 'sourceSha': head,
                'productTree': tree.split()[2], 'controlSha256': c.resume_contract_sha()}
    return product, checkout, identity


def package_local_ci(name, checkout, origin, head, output):
    product, checkout, identity = local_identity(name, checkout, origin, head)
    safe_path(output)
    output = output.resolve()
    output.mkdir()
    result = run_product_ci(product, checkout, head, output)
    if local_identity(name, checkout, origin, head)[2] != identity:
        raise Refused('Local CI source or control changed during execution')
    checks = []
    logs = {}
    for phase, folder in (('validation', 'validation'), ('build', 'fresh-build')):
        for index, outcome in enumerate(result[phase]):
            filename = f'{phase}-{index}.log'
            log = read_owned(output / folder / 'candidate', outcome['log'])
            if sha256(log) != outcome['logSha256']:
                raise Refused('Product check log changed before packaging')
            logs[filename] = log
            checks.append({**outcome, 'phase': phase, 'log': filename})
    artifact = read_owned(output, 'site.tar.gz', 55_000_000)
    if sha256(artifact) != result['artifactSha256']:
        raise Refused('Product artifact changed before packaging')
    members = {**logs, 'site.tar.gz': artifact}
    receipt = {'version': 1, 'identity': identity, 'checks': checks, 'artifact': result['release'],
               'files': {path: sha256(data) for path, data in members.items()},
               'coverage': {'sandbox': 'macOS sandbox-exec', 'hostedCI': 'not-run', 'signing': 'not-run',
                            'secretAndSastScan': 'not-run',
                            'npmAuditAndSbom': 'not-applicable' if product.name == 'catalog' else 'not-run',
                            'productionProbe': 'not-run'}}
    bundle = output / 'local-ci-evidence'
    bundle.mkdir()
    for path, data in members.items():
        write_owned(bundle, path, data)
    encoded = (json.dumps(receipt, indent=2) + '\n').encode()
    write_owned(bundle, 'evidence.json', encoded)
    return {'bundle': str(bundle), 'evidenceSha256': sha256(encoded), 'identity': identity}


def verify_local_ci(name, checkout, origin, head, bundle, expected):
    product, _, identity = local_identity(name, checkout, origin, head)
    encoded = read_owned(bundle, 'evidence.json')
    if not re.fullmatch('[a-f0-9]{64}', expected) or sha256(encoded) != expected:
        raise Refused('Local CI receipt hash mismatch')
    receipt = json.loads(encoded)
    if receipt.get('version') != 1 or receipt.get('identity') != identity:
        raise Refused('Local CI evidence identity mismatch')
    commands = [('validation', list(command)) for command in product.validation] + [('build', product.build_command())]
    checks = receipt['checks']
    if [(row['phase'], row['command']) for row in checks] != commands or any(row['exitCode'] != 0 for row in checks):
        raise Refused('Local CI evidence does not contain the required passing commands')
    manifest = receipt['files']
    log_hashes = {row['log']: row['logSha256'] for row in checks}
    if (len(log_hashes) != len(checks) or set(manifest) != {'site.tar.gz', *log_hashes}
            or any(manifest[name] != digest for name, digest in log_hashes.items())
            or {path.name for path in bundle.iterdir()} != {'evidence.json', *manifest}):
        raise Refused('Local CI evidence files are missing or unexpected')
    for path, digest in manifest.items():
        if path == 'site.tar.gz':
            continue
        if sha256(read_owned(bundle, path)) != digest:
            raise Refused(f'Local CI evidence file changed: {path}')
    artifact = read_owned(bundle, 'site.tar.gz', 55_000_000)
    if sha256(artifact) != manifest['site.tar.gz']:
        raise Refused('Local CI evidence file changed: site.tar.gz')
    with TemporaryDirectory() as directory:
        workspace = Path(directory).resolve()
        write_owned(workspace, 'site.tar.gz', artifact)
        if product.extract(workspace / 'site.tar.gz', workspace / 'site', head) != receipt['artifact']:
            raise Refused('Local CI evidence artifact metadata mismatch')
    return {'verified': True, 'identity': identity, 'evidenceSha256': expected}
