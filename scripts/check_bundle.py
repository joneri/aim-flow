#!/usr/bin/env python3
"""Offline smoke test for the branded downloadable Beta bundle."""
import argparse
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('zipfile',type=Path)
args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='aim-flow-bundle-smoke-') as d:
    root=Path(d).resolve()
    with zipfile.ZipFile(args.zipfile) as archive:
        names=archive.namelist()
        if not names or any(n.startswith('/') or '..' in Path(n).parts for n in names):
            raise SystemExit('Unsafe bundle paths')
        tops={Path(n).parts[0] for n in names}
        if len(tops)!=1: raise SystemExit('Bundle must have one root')
        archive.extractall(root)
    base=root/next(iter(tops));meta=json.loads((base/'release.json').read_text())
    actual={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}
    if actual != set(meta['filesSha256'])|{'release.json'}: raise SystemExit('Bundle membership mismatch')
    for name,expected in meta['filesSha256'].items():
        if hashlib.sha256((base/name).read_bytes()).hexdigest()!=expected:
            raise SystemExit('Bundle payload hash mismatch: '+name)
    if (base/'LICENSE').read_bytes()!=(Path(__file__).resolve().parents[1]/'LICENSE').read_bytes():
        raise SystemExit('MIT notice differs')
    bootstrap=base/'controller/aim-ci-bootstrap.pyz'
    # Bind executable bootstrap bytes to this trusted source checkout, not only
    # to a hash supplied by the downloadable bundle itself.
    source=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(source/'scripts/verify_provenance.py')],check=True)
    expected_bootstrap=io.BytesIO()
    with zipfile.ZipFile(expected_bootstrap,'w',compression=zipfile.ZIP_STORED) as trusted:
        for name,relative in [('__main__.py','lib/aim_ci/control_archive.py'),
                              ('aim_ci/__init__.py','lib/aim_ci/__init__.py'),
                              ('aim_ci/owned_paths.py','lib/aim_ci/owned_paths.py')]:
            trusted.writestr(zipfile.ZipInfo(name),(source/relative).read_bytes())
    if bootstrap.read_bytes()!=expected_bootstrap.getvalue():
        raise SystemExit('Bootstrap differs from trusted source checkout')
    if hashlib.sha256(bootstrap.read_bytes()).hexdigest()!=meta['bootstrapSha256']:
        raise SystemExit('Bootstrap hash mismatch')
    archive=base/meta['controllerArchive'];installed=root/'installed'
    for command,extra in [('verify',[]),('install',['--destination',str(installed)])]:
        result=subprocess.run([sys.executable,'-IBS',str(bootstrap),command,'--archive',str(archive),
                               '--sha256',meta['controllerArchiveSha256'],*extra],capture_output=True,text=True,timeout=30)
        if result.returncode: raise SystemExit(result.stderr)
    result=subprocess.run([sys.executable,'-IBS',str(installed/'aim-ci-control.py'),'readiness',
                           '--manifest-sha256',meta['controllerManifestSha256']],capture_output=True,text=True,timeout=30)
    if result.returncode!=3: raise SystemExit('Expected unconfigured readiness exit 3')
    ready=json.loads(result.stdout)
    if ready['packageVerified'] is not True or ready['ready'] is not False:
        raise SystemExit('Unexpected readiness')
    print(json.dumps({'version':meta['version'],'payloadHashes':len(meta['filesSha256']),
                      'bootstrapVerify':'passed','freshInstall':'passed','readinessExit':3,
                      'zipSha256':hashlib.sha256(args.zipfile.read_bytes()).hexdigest()},indent=2))
