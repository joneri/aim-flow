#!/usr/bin/env python3
"""Verify the reviewed product source inventory without operator state or credentials."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'provenance/upstream-files.json').read_text())
if manifest.get('schemaVersion') != 2:
    raise SystemExit('Expected product-only source inventory schema 2')
if set(manifest['files']) != set(manifest['fileOrigins']):
    raise SystemExit('Source origin inventory is incomplete')
failures = []
counts = {'unchanged': 0, 'modified': 0, 'added': 0}
for name, expected in manifest['files'].items():
    path = root / name
    origin = manifest['fileOrigins'][name]
    kind = origin['kind']
    if kind not in counts or (kind == 'unchanged' and origin['upstreamSha256'] != expected):
        raise SystemExit('Inconsistent source origin: ' + name)
    counts[kind] += 1
    if path.is_symlink() or not path.is_file():
        failures.append(name)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        failures.append(name)
if failures:
    raise SystemExit('Reviewed source bytes differ: ' + ', '.join(failures))
if manifest['controllerFiles'] != len(manifest['controllerPaths']) or not set(manifest['controllerPaths']) <= set(manifest['files']):
    raise SystemExit('Controller source inventory is incomplete')
print(f"Verified {len(manifest['files'])} product source files: "
      f"{counts['unchanged']} unchanged, {counts['modified']} modified, {counts['added']} added")
