"""Data-only inputs to the reviewed synthetic catalog heading recipe."""
import json
import re

from openai_budget import Refused, read_owned, safe_path
from .control_package import check_digest


CATALOG_HEADING = 'catalog-heading-v1'
CATALOG_PATH = 'catalog.py'
CATALOG_TEST_PATH = 'tests/test_catalog.py'
FIELDS = {'version', 'recipe', 'issueNumber', 'title', 'path', 'fromHeading', 'toHeading'}


def validate_assignment(value):
    if not isinstance(value, dict) or set(value) != FIELDS or type(value['version']) is not int or value['version'] != 1:
        raise Refused('Invalid offline assignment schema')
    if value['recipe'] != CATALOG_HEADING:
        raise Refused('Offline assignment recipe is not registered')
    if value['path'] != CATALOG_PATH:
        raise Refused('Catalog heading assignment only permits catalog.py as the requested source path')
    if type(value['issueNumber']) is not int or not 1 <= value['issueNumber'] <= 999999:
        raise Refused('Offline assignment requires a synthetic issue number from 1 to 999999')
    for name, length in (('title', 100), ('fromHeading', 60), ('toHeading', 60)):
        text = value[name]
        if (not isinstance(text, str) or len(text) > length
                or not re.fullmatch(r'[A-Za-z0-9]+(?:[ -][A-Za-z0-9]+)*', text)):
            raise Refused(f'Invalid offline assignment {name}: use short words, digits, spaces or hyphens')
    if value['fromHeading'] == value['toHeading']:
        raise Refused('Offline assignment must change the catalog heading')
    return value


def load_assignment(path, expected):
    safe_path(path)
    path = path.resolve()
    encoded = read_owned(path.parent, path.name, 4096)
    check_digest(encoded, expected, 'Offline assignment')
    return validate_assignment(json.loads(encoded))


def heading_edits(assignment):
    before = assignment['fromHeading']
    after = assignment['toHeading']
    return [
        {'path': CATALOG_PATH, 'old': f"TITLE = {before!r}\n", 'new': f"TITLE = {after!r}\n"},
        {'path': CATALOG_TEST_PATH,
         'old': f"            self.assertIn('<h1>{before}</h1>', page)\n",
         'new': f"            self.assertIn('<h1>{after}</h1>', page)\n"},
    ]
