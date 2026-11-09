"""Product-independent immutable static artifact identity and safe promotion."""
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from openai_budget import Refused, fingerprint, safe_path, read_owned


def manifest(site):
    result = {}
    for path in sorted(Path(site).rglob('*')):
        if path.is_symlink():
            raise Refused('Linked site artifact')
        if path.is_file() and path != Path(site) / 'release.json':
            result[str(path.relative_to(site))] = hashlib.sha256(read_owned(site, path.relative_to(site), 20_000_000)).hexdigest()
    return result


def metadata(site, head, product):
    if not re.fullmatch('[0-9a-f]{40}', head):
        raise Refused('Invalid release source SHA')
    files = manifest(site)
    if 'index.html' not in files:
        raise Refused('Artifact has no entry page')
    return {'version': 1, 'product': product, 'sourceSha': head,
            'files': files, 'contentSha256': fingerprint(files)}


def pack(site, archive):
    release_bytes = read_owned(site, 'release.json')
    release = json.loads(release_bytes)
    retained = {path: read_owned(site, path, 20_000_000) for path in release['files']}
    if {path: hashlib.sha256(data).hexdigest() for path,data in retained.items()} != release['files']:
        raise Refused('Artifact changed between manifest and packaging')
    retained['release.json'] = release_bytes
    with tarfile.open(archive, 'w:gz') as stream:
        for name, data in sorted(retained.items()):
            info = tarfile.TarInfo(name); info.size = len(data)
            stream.addfile(info, io.BytesIO(data))


def extract_verified(archive, destination, expected_sha, expected_product, policy):
    destination = Path(destination)
    safe_path(destination)
    if destination.exists():
        raise Refused('New artifact destination required')
    retained, size = {}, 0
    with tarfile.open(archive, 'r:gz') as stream:
        for entry in stream:
            path = Path(entry.name)
            size += entry.size
            if (len(retained) >= 200 or size > 50_000_000 or entry.size > 20_000_000
                    or not entry.isfile() or path.is_absolute() or '..' in path.parts
                    or '\\' in entry.name or entry.name != path.as_posix()
                    or entry.name in retained):
                raise Refused('Unsafe, duplicate or excessive artifact member')
            retained[entry.name] = stream.extractfile(entry).read()
    if 'release.json' not in retained:
        raise Refused('Release metadata absent')
    release = json.loads(retained['release.json'])
    files = {k: hashlib.sha256(v).hexdigest() for k, v in retained.items() if k != 'release.json'}
    if (release.get('version') != 1 or release.get('product') != expected_product
            or release.get('sourceSha') != expected_sha or release.get('files') != files
            or release.get('contentSha256') != fingerprint(files) or 'index.html' not in files):
        raise Refused('Artifact source/product/content identity mismatch')
    policy(release, retained)  # Reviewed product assertions, also before writes.
    destination.mkdir(parents=True)
    for name, content in retained.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return release
