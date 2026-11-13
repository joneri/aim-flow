#!/usr/bin/env python3
"""Run the small, explicit CI selection; never discover the complete suite."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='Show selection without importing/running tests')
    parser.add_argument('--node', default=None, help='Explicit Node executable; default: node on PATH')
    args = parser.parse_args()
    selection = json.loads((ROOT / 'tests/smoke-contracts.json').read_text())
    python = selection['python']
    node = selection['node']
    if (selection['format'] != 'aim-flow-ci-smoke-selection'
            or len(set(python)) != len(python)
            or len({(row['file'], row['name']) for row in node}) != len(node)
            or not 10 <= len(python) + len(node) <= 19):
        parser.error('Keep an explicit, unique selection of 10–19 cases')
    if args.list:
        print(json.dumps({'selectedCases': len(python) + len(node), **selection}, indent=2))
        return 0
    executable = args.node or shutil.which('node')
    if not executable:
        parser.error('Node is required for the selected protocol cases')
    sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'lib')]
    suite = unittest.TestLoader().loadTestsFromNames(python)
    if suite.countTestCases() != len(python):
        parser.error('Python selection must resolve to exactly the named cases')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        return 1
    pattern = '^(?:' + '|'.join(re.escape(row['name']) for row in node) + ')$'
    files = sorted({row['file'] for row in node})
    observed = subprocess.run([executable, '--test', '--test-reporter=tap',
        '--test-name-pattern', pattern, *files], cwd=ROOT, capture_output=True, text=True)
    print(observed.stdout, end='')
    print(observed.stderr, end='', file=sys.stderr)
    passes = re.findall(r'^# pass (\d+)\s*$', observed.stdout, re.MULTILINE)
    if observed.returncode or not passes or int(passes[-1]) != len(node):
        print('Node must execute and pass every selected case; no empty-match success.', file=sys.stderr)
        return 1
    print(f'Smoke selection passed: {len(python)} Python + {len(node)} Node = {len(python) + len(node)} cases.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
