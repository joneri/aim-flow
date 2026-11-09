#!/usr/bin/env python3
"""Verify exact upstream extraction; never reads operator state or credentials."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'provenance/upstream-files.json').read_text())
failures = []
for name, expected in manifest['files'].items():
    path = root / name
    if path.is_symlink() or not path.is_file():
        failures.append(name)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        failures.append(name)
if failures:
    raise SystemExit('Upstream bytes differ: ' + ', '.join(failures))
print(f"Verified {len(manifest['files'])} extracted files from {manifest['upstreamCommit']}")
