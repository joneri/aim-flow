"""Reviewed built-in product recipes, never executable commands from an Issue.

Shared admission, patch, sandbox, review, cost and release identity checks remain
in the controller. A recipe supplies product-specific inputs and verification.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import json
import shutil
import subprocess
import sys
from typing import Optional

from openai_budget import Refused, read_owned, write_owned, owned_mkdir

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Product:
    name: str
    directory: str
    allowed: tuple
    oracle_source: str
    oracle_path: str
    validation: tuple
    live_target: Optional[str] = None
    locked_inputs: tuple = ()
    required_checks: tuple = ('complete-deployed-manifest',)
    review_focus: str = ''

    @property
    def artifact_product(self):
        return self.name

    def formatter(self, relative):
        if relative not in self.allowed:
            raise Refused('Formatter path outside registered product scope')
        return [sys.executable, '-B', '-c',
                'import ast,pathlib,sys; text=pathlib.Path(sys.argv[1]).read_text(); ast.parse(text); sys.stdout.write(text)', relative]

    def baseline_command(self):
        return [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_independent_catalog.py', '-v']

    def baseline_status(self, code, log):
        if code != 0:
            raise Refused('Independent product baseline oracle failed')
        return 'invariant_preserved'

    def stage(self, candidate, baseline, repository, command):
        candidate.mkdir()
        archive = candidate.parent / 'source.tar'
        with archive.open('wb') as stream:
            subprocess.run(['git', 'archive', baseline, self.directory], cwd=repository, stdout=stream, check=True)
        command(['tar', '-xf', str(archive), '-C', str(candidate)])
        project = candidate / self.directory
        destination = project / self.oracle_path
        owned_mkdir(project, Path(self.oracle_path).parent)
        write_owned(project, self.oracle_path, read_owned(ROOT, self.oracle_source, 50000))
        return project

    def build_command(self, *, base_path='/', output=None):
        return [sys.executable, '-B', 'catalog.py', 'fixtures/items.csv', str(output or 'dist')]

    def metadata(self, site, head):
        from .artifacts import metadata
        result = metadata(site, head, self.name)
        self.artifact_policy(result, {p: read_owned(site, p, 20_000_000) for p in result['files']})
        return result

    def artifact_policy(self, release, files):
        if set(files) - {'release.json'} != {'index.html', 'catalog.json'}:
            raise Refused('Catalog artifact must contain exactly its JSON and HTML')
        expected = [
            {'id': 'X1', 'name': '<script>alert("demo")</script>', 'price': '3.00'},
            {'id': 'A1', 'name': 'Apple', 'price': '2.25'},
            {'id': 'A2', 'name': 'apple & pear', 'price': '0.00'},
            {'id': 'Z9', 'name': 'zebra', 'price': '12.50'},
        ]
        if json.loads(files['catalog.json']) != expected:
            raise Refused('Catalog artifact differs from its immutable synthetic input oracle')
        page = files['index.html'].decode('utf-8')
        if '<script' in page.lower() or '&lt;script&gt;' not in page or 'apple &amp; pear' not in page:
            raise Refused('Catalog artifact HTML escaping failed')

    def extract(self, archive, destination, expected_sha):
        return extract_artifact(archive, destination, expected_sha, self.artifact_product, self.artifact_policy)


PRODUCTS = {
    'catalog': Product('catalog', 'catalog', ('catalog.py', 'tests/test_catalog.py'),
                       'fixtures/products/catalog-oracle.py', 'tests/test_independent_catalog.py',
                       ((sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'),),
                       required_checks=('complete-deployed-manifest', 'catalog-content-order-escaping', 'story-heading'),
                       review_focus='Check catalog content, Unicode sorting, HTML escaping and invalid input without partial output.'),
}


def get_product(name):
    if name not in PRODUCTS:
        raise Refused('Unknown product; a profile cannot load arbitrary code')
    return PRODUCTS[name]


def product_for(config):
    for product in PRODUCTS.values():
        if config.get('product') == product.directory and config.get('allowedPaths') == list(product.allowed):
            return product
    raise Refused('Profile differs from the reviewed built-in product recipe')


def hosted_product_inputs(root, name):
    """Resolve reviewed hosted input paths without granting delivery rights."""
    product = get_product(name)
    if product.live_target is None or product.locked_inputs != ('package.json', 'package-lock.json'):
        raise Refused('Product has no registered hosted npm route')
    profile_path = f'profiles/{product.name}.json'
    encoded = read_owned(root, profile_path)
    profile = json.loads(encoded)
    policy = json.loads(read_owned(root, 'scm/production-policy-v1.json'))
    if (not isinstance(policy, dict) or not isinstance(policy.get('soloPoc'), dict)
            or not isinstance(policy.get('workflow'), dict)):
        raise Refused('Invalid SCM policy for hosted product inputs')
    expected_scope = {'repository': policy.get('repository'), 'branch': policy.get('baseBranch'),
                      'owner': policy['soloPoc'].get('maintainer'),
                      'requiredCheck': policy['workflow'].get('requiredCheck')}
    if any(not isinstance(value, str) or not value for value in expected_scope.values()):
        raise Refused('Invalid SCM scope for hosted product inputs')
    if (not isinstance(profile, dict) or type(profile.get('version')) is not int
            or profile['version'] != 1 or product_for(profile) != product
            or profile.get('offlineOnly', False) is not False
            or profile.get('productionUrl') != product.live_target
            or any(profile.get(key) != value for key, value in expected_scope.items())):
        raise Refused('Hosted product profile does not match the registered route')
    return {'product': product.name, 'directory': product.directory,
            'lockfile': f'{product.directory}/{product.locked_inputs[1]}',
            'profile': profile_path, 'profileSha256': hashlib.sha256(encoded).hexdigest(),
            'artifactProduct': product.artifact_product}


from .artifacts import metadata as artifact_metadata, extract_verified as extract_artifact
