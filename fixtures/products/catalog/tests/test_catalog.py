import io
from pathlib import Path
import tempfile
import unittest
from catalog import generate


class CatalogTests(unittest.TestCase):
    def test_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            generate(io.StringIO('id,name,price\nA,Apple,1\n'), directory)
            self.assertIn('<h1>Synthetic catalog</h1>', Path(directory, 'index.html').read_text())
