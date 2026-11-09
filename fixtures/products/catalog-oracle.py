"""Frozen independent synthetic catalog expectations; never model-writable."""
import io
from pathlib import Path
import tempfile
import unittest
from catalog import generate, read_items


class IndependentCatalogOracle(unittest.TestCase):
    def test_unicode_casefold_and_identifier_tie_break(self):
        actual = read_items(io.StringIO('id,name,price\nZ9,STRASSE,2\nA1,Straße,1\n'))
        self.assertEqual(actual, [{'id': 'A1', 'name': 'Straße', 'price': '1.00'},
                                  {'id': 'Z9', 'name': 'STRASSE', 'price': '2.00'}])

    def test_literal_content_order_and_html_escaping(self):
        source = 'id,name,price\nZ9,zebra,12.5\nA2,apple & pear,0\nA1,Apple,2.25\nX1,<script>alert("demo")</script>,3\n'
        expected = [{'id': 'X1', 'name': '<script>alert("demo")</script>', 'price': '3.00'},
                    {'id': 'A1', 'name': 'Apple', 'price': '2.25'},
                    {'id': 'A2', 'name': 'apple & pear', 'price': '0.00'},
                    {'id': 'Z9', 'name': 'zebra', 'price': '12.50'}]
        with tempfile.TemporaryDirectory() as directory:
            actual = generate(io.StringIO(source), directory)
            self.assertEqual(actual, expected)
            page = Path(directory, 'index.html').read_text()
            self.assertIn('&lt;script&gt;alert(&quot;demo&quot;)&lt;/script&gt;', page)
            self.assertIn('apple &amp; pear', page)
            self.assertNotIn('<script>', page)
            self.assertLess(page.index('data-id="A1"'), page.index('data-id="A2"'))

    def test_invalid_data_has_no_partial_publication(self):
        for rows in ['id,name,cost\nA,Apple,1\n', 'id,name,price\n',
                     'id,name,price\nA,Apple,1\nA,Pear,2\n', 'id,name,price\nA,,1\n',
                     'id,name,price\nA,Apple,NaN\n', 'id,name,price\nA,Apple,Infinity\n',
                     'id,name,price\nA,Apple,-1\n', 'id,name,price\nA,Apple,0.001\n',
                     'id,name,price\nA,Apple,1,extra\n', 'id,name,price\nA,Apple\n',
                     'id,name,price\nA,Apple,"1\n', 'id,name,price\nA,Apple,"1"extra\n']:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory, 'new-output')
                with self.assertRaises(ValueError): generate(io.StringIO(rows), destination)
                self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
