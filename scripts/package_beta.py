#!/usr/bin/env python3
"""Build a deterministic branded wrapper around the product-only controller bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True, help='New output directory; never overwritten')
args = parser.parse_args()
if sys.version_info < (3, 11):
    parser.error('Python 3.11 or newer is required')
version = (ROOT / 'VERSION').read_text().strip()
license_bytes = (ROOT / 'LICENSE').read_bytes()
inventory = json.loads((ROOT / 'provenance/upstream-files.json').read_text())
subprocess.run([sys.executable, str(ROOT / 'scripts/verify_provenance.py')], check=True)
if args.output.exists() or args.output.is_symlink():
    parser.error('Choose a new output directory')
with tempfile.TemporaryDirectory(prefix='aim-flow-package-') as temporary:
    raw = Path(temporary).resolve() / 'controller'
    result = subprocess.run([sys.executable, '-IBS', str(ROOT / 'aim-flow.py'), 'build-release',
                             '--version', version, '--output', str(raw)], check=True,
                            capture_output=True, text=True)
    release = json.loads((raw / 'release.json').read_text())
    files = {'controller/' + p.name: p.read_bytes() for p in raw.iterdir() if p.is_file()}
    readme = f"""# AIM Flow {version} · Beta

This is a technical beta for trusted, reviewed projects. It is not a general autonomous delivery service. Start with archive verification and read-only readiness. No model call, execution or deployment is authorized by installation.

## Before executing the bootstrap

Use Python 3.11+ on a POSIX filesystem. Obtain the expected bootstrap and archive pins through a separate trusted channel. The hashes below identify these bytes but do not authenticate an untrusted download by themselves.

- Bootstrap SHA-256: `{release['filesSha256']['aim-ci-bootstrap.pyz']}`
- Controller archive SHA-256: `{release['archiveSha256']}`
- Installed manifest SHA-256: `{release['manifestSha256']}`

After extracting, enter the `controller` directory. Check the bootstrap without executing it, using the independently trusted expected hash:

```sh
python3 -IBS -c 'import hashlib,sys; actual=hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest(); sys.exit(0 if actual == sys.argv[2] else "Bootstrap SHA-256 mismatch")' aim-ci-bootstrap.pyz EXPECTED_BOOTSTRAP_SHA256
```

Do not continue if the check fails. Then:

```sh
python3 -IBS aim-ci-bootstrap.pyz verify --archive {release['archive']} --sha256 EXPECTED_ARCHIVE_SHA256
python3 -IBS aim-ci-bootstrap.pyz install --archive {release['archive']} --sha256 EXPECTED_ARCHIVE_SHA256 --destination /absolute/path/to/new-private-controller
python3 -IBS /absolute/path/to/new-private-controller/aim-ci-control.py readiness --manifest-sha256 EXPECTED_MANIFEST_SHA256
```

Unconfigured readiness normally exits **3** with `packageVerified: true` and `ready: false`. Exit **1** signals failure. Keep the installation outside your project. Project inspection additionally requires Git 2.54+ and its independently verified executable pin.

The historical `aim-ci` names are intentionally preserved. See [BETA-SCOPE.md](BETA-SCOPE.md), [LICENSE](LICENSE) and [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). The original detailed guide and original release metadata remain intact in `controller/`. Detailed current source instructions: https://github.com/joneri/aim-flow/blob/main/docs/QUICKSTART.md
"""
    files['README.md'] = readme.encode()
    files['LICENSE'] = license_bytes
    files['THIRD-PARTY-NOTICES.md'] = (ROOT / 'THIRD-PARTY-NOTICES.md').read_bytes()
    files['BETA-SCOPE.md'] = (ROOT / 'docs/BETA-SCOPE.md').read_bytes()
    files['aim-ci-size-handling.md'] = (ROOT / 'docs/aim-ci-size-handling.md').read_bytes()
    files['release.json'] = (json.dumps({
        'product': 'AIM Flow', 'version': version, 'channel': 'beta', 'license': 'MIT',
        'upstreamCommit': inventory['upstreamCommit'],
        'upstreamChanges': [change['commit'] for change in inventory.get('upstreamChanges', [])],
        'controllerArchive': 'controller/' + release['archive'], 'controllerArchiveSha256': release['archiveSha256'],
        'controllerManifestSha256': release['manifestSha256'],
        'bootstrapSha256': release['filesSha256']['aim-ci-bootstrap.pyz'],
        'filesSha256': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    }, indent=2, sort_keys=True) + '\n').encode()
    args.output.mkdir(parents=False)
    path = args.output / f'aim-flow-{version}.zip'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(f'aim-flow-{version}/{name}', (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (args.output / 'SHA256SUMS').write_text(f'{digest}  {path.name}\n')
    (args.output / 'release.json').write_bytes(files['release.json'])
    print(json.dumps({'file': str(path), 'sha256': digest, 'version': version}, indent=2))
