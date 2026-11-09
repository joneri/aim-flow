"""Product-independent helpers retain structured output and frozen-input fences."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aim_ci import model_support as support
from openai_budget import Refused


class ModelSupportTests(unittest.TestCase):
    def test_only_synthetic_product_is_registered_and_live_profile_refuses(self):
        from aim_ci import controller, products
        self.assertEqual(set(products.PRODUCTS), {'catalog'})
        self.assertIsNone(products.PRODUCTS['catalog'].live_target)
        with self.assertRaisesRegex(Refused, 'qualified offline only'):
            controller.profile()
        with self.assertRaisesRegex(Refused, 'no registered hosted npm route'):
            products.hosted_product_inputs(ROOT, 'catalog')

    def test_payload_preserves_bounded_private_structured_output(self):
        value = support.payload('Review only', {'path': 'source.py'}, support.PATCH_SCHEMA, 'patch')
        self.assertFalse(value['store'])
        self.assertEqual(value['max_output_tokens'], 8192)
        self.assertTrue(value['text']['format']['strict'])
        self.assertEqual(json.loads(value['input']), {'path': 'source.py'})

    def test_ambiguous_or_missing_model_output_refused(self):
        message = {'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}
        self.assertEqual(support.output_json({'output': [message]}), {'ok': True})
        for output in ([], [message, message]):
            with self.subTest(output=output), self.assertRaises(Refused):
                support.output_json({'output': output})

    def test_frozen_input_hashes_detect_edits_and_refuse_linked_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'src').mkdir()
            source = root / 'src/main.py'
            source.write_text('original')
            before = support.frozen_inputs(root)
            source.write_text('changed')
            self.assertNotEqual(before, support.frozen_inputs(root))
            (root / 'src/linked.py').symlink_to(source)
            with self.assertRaisesRegex(Refused, 'Linked product input'):
                support.frozen_inputs(root)

    def test_validation_requires_explicit_product_and_restricts_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name in ('../outside', '/outside', '', 'nested/path'):
                with self.subTest(name=name), self.assertRaises(Refused):
                    support.product_root(root, name)
            with self.assertRaises(TypeError):
                support.validate(root, [], 'validation')
            (root / 'linked').symlink_to(root, target_is_directory=True)
            with self.assertRaises(Refused):
                support.product_root(root, 'linked')
            (root / 'project').mkdir()
            policy = support.sandbox_profile(root, root / 'key', 'project')
            for fence in ('(deny network*)', '(deny file-write*)', '(deny file-read-data'):
                self.assertIn(fence, policy)


if __name__ == '__main__':
    unittest.main()
