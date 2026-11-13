"""Committed command evidence only; synthetic reader never launches project code."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_command_source_context as context


class Reader:
    commit = 'a' * 40
    def __init__(self, files):
        self.files = {p: text.encode() for p, text in files.items()}
        self.reads = []
    def entries(self, *args, **kwargs):
        return [(p, '100644', 'blob', p) for p in self.files]
    def size(self, oid): return len(self.files[oid])
    def blob(self, oid, **kwargs):
        self.reads.append(oid); return self.files[oid]
    def verify_head(self): pass


class ContextTests(unittest.TestCase):
    def derive(self, files, script='test'):
        self.reader = Reader(files)
        return context.derive(self.reader, [{'root': '.', 'operation': 'run', 'script': script}])

    def test_lifecycle_nested_prefix_imports_and_literal_child_sources(self):
        files = {'package.json': json.dumps({'scripts': {'pretest':'node scripts/pre.mjs',
            'test':'npm run child && npm --prefix server run test', 'posttest':'node scripts/post.mjs',
            'child':'node scripts/test.mjs', 'preview':'node scripts/preview.mjs'}}),
            'server/package.json': json.dumps({'scripts': {'test':'node test.cjs'}}),
            'server/test.cjs': "require('./helper');", 'server/helper.js':'// helper',
            'scripts/pre.mjs':'// pre', 'scripts/post.mjs':'// post', 'scripts/preview.mjs':'// preview',
            'scripts/test.mjs': "import './helper.mjs'; const source = 'src/page.astro'; spawn('npm', ['run', 'preview']);",
            'scripts/helper.mjs':"import './test.mjs';", 'src/page.astro':'<h1>Hello</h1>',
            'unrelated.js':'// not selected'}
        result = self.derive(files)
        self.assertEqual(set(result['sourcePaths']), set(files) - {'unrelated.js'})
        self.assertTrue(result['unresolved'])
        self.assertEqual(len(self.reader.reads), len(set(self.reader.reads)))

    def test_prefix_positions_do_not_select_wrong_root(self):
        for command in ('npm --prefix server run test', 'npm --prefix=server run test',
                        'npm run test --prefix server'):
            result = self.derive({'package.json': json.dumps({'scripts': {'test': command}}),
                'server/package.json': '{"scripts":{"test":"node test.js"}}',
                'server/test.js':'// correct root'})
            self.assertIn('server/test.js', result['sourcePaths'])

    def test_missing_dynamic_external_and_secret_are_explicit(self):
        result = self.derive({'package.json':json.dumps({'scripts': {'test':'node scripts/test.js && node $ENTRY'}}),
            'scripts/test.js':"import './missing.js'; import 'playwright'; const a = 'config/secret.js'; const b = 'src/private.js';",
            'config/secret.js':'// private config', 'src/private.js':'const token = "ghp_12345678901234567890";'})
        # Unsupported shell segment stays unresolved. Never recover a partial shell closure.
        self.assertIn('dynamic-or-unsupported-shell-syntax', [x['reason'] for x in result['unresolved']])
        self.assertEqual(result['sourcePaths'], ['package.json'])
        result = self.derive({'package.json':json.dumps({'scripts': {'test':'node scripts/test.js'}}),
            'scripts/test.js':"import './missing.js'; import 'playwright'; const a = 'config/secret.js'; const b = 'src/private.js';",
            'config/secret.js':'// private config', 'src/private.js':'const token = "ghp_12345678901234567890";'})
        reasons = {x['reason'] for x in result['unresolved']}
        self.assertTrue({'secret-or-credential-configuration-path', 'suspected-secret-content',
            'external-or-aliased-import', 'unresolved-or-ambiguous-local-import'} <= reasons)
        self.assertNotIn('config/secret.js', self.reader.reads)
        self.assertNotIn('src/private.js', result['sourcePaths'])

    def test_secret_literals_not_credential_expressions(self):
        for text in ('password: row.password_hash,', 'access_token: result.token,',
                     'password: process.env.DATABASE_PASSWORD,', 'const password = process.env.PASSWORD;'):
            self.assertIsNone(context.SECRET_TEXT.search(text))
        for text in ('password: "abcdefghijk",', "const password = 'abcdefghijk';",
                     '{"api_key": "abcdefghijk"}', 'Bearer abcdefghijklmnop1234',
                     '-----BEGIN RSA PRIVATE KEY-----', 'ghp_12345678901234567890'):
            self.assertIsNotNone(context.SECRET_TEXT.search(text))

    def test_whole_file_bound_no_partial_text(self):
        with patch.dict(context.LIMITS, {'fileBytes':100}):
            result = self.derive({'package.json':'{"scripts":{"test":"node scripts/large.js"}}', 'scripts/large.js':'a'*101})
        self.assertEqual(result['sourcePaths'], ['package.json'])
        self.assertNotIn('scripts/large.js', self.reader.reads)
        self.assertIn('whole-file-byte-bound', [x['reason'] for x in result['unresolved']])

    def test_nonregular_outside_and_default_install_are_unresolved(self):
        reader = Reader({'package.json':'{"scripts":{"preinstall":"node ../escape.js"}}'})
        result = context.derive(reader, [{'root':'.','operation':'ci','script':None}])
        self.assertTrue(any(r['reason'] == 'dynamic-or-outside-node-entrypoint' for r in result['unresolved']))
        self.assertTrue(any('dependency-install' in r['reason'] for r in result['unresolved']))
        result = context.derive(reader, [{'root':'.','operation':'ci','script':None}],
            {'package.json':('120000','blob','package.json')})
        self.assertEqual(result['sourcePaths'], [])
        self.assertEqual(result['files'][0]['reason'], 'non-regular-file')

    def test_ci_invalid_scripts_shapes_are_typed_gaps(self):
        for value in (None, 1, [], 'not-a-script-map'):
            reader = Reader({'package.json': json.dumps({'scripts': value})})
            result = context.derive(reader, [{'root': '.', 'operation': 'ci', 'script': None}])
            self.assertIn('invalid-package-scripts', {r['reason'] for r in result['unresolved']})

    def test_deterministic_and_no_unselected_scripts(self):
        files = {'package.json':json.dumps({'scripts': {'test':'node test.js', 'deploy':'node publish.js'}}),
            'test.js':'// test', 'publish.js':'// effect'}
        self.assertEqual(self.derive(files), self.derive(dict(reversed(list(files.items())))))
        self.assertNotIn('publish.js', self.derive(files)['sourcePaths'])

if __name__ == '__main__': unittest.main()
