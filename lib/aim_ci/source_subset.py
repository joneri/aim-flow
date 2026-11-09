"""Bound an explicit source subset to its original Git commit without omitted blobs."""
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from hashlib import sha1, sha256
import re

from openai_budget import Refused

KIND = 'git-tree-source-subset-v1'
_OID = re.compile(r'[a-f0-9]{40}')
_SHA256 = re.compile(r'[a-f0-9]{64}')


def git_id(kind, data):
    return sha1(f'{kind} {len(data)}\0'.encode() + data).digest()


def selected_paths(paths):
    from .npm_platform import project_path

    if (not isinstance(paths, list) or not paths
            or any(not project_path(name) for name in paths)
            or len(set(paths)) != len(paths)):
        raise Refused('Source subset requires distinct exact regular-file paths')
    for name in paths:
        if any(part in ('.git', 'node_modules', '.npmrc') or re.match(r'^\.env(?:\.|$)', part)
               for part in name.split('/')):
            raise Refused('Source subset contains a protected environment or dependency path')
    return sorted(paths)


def tree_entries(raw):
    """Sibling names remain bytes; their contents are neither read nor interpreted."""
    entries, offset = {}, 0
    while offset < len(raw):
        end_mode = raw.find(b' ', offset)
        end_name = raw.find(b'\0', end_mode + 1) if end_mode >= 0 else -1
        if end_mode < 0 or end_name < 0 or end_name + 21 > len(raw):
            raise Refused('Malformed source membership tree')
        mode, name = raw[offset:end_mode], raw[end_mode + 1:end_name]
        oid = raw[end_name + 1:end_name + 21]
        if (mode not in (b'40000', b'100644', b'100755', b'120000', b'160000')
                or not name or name in (b'.', b'..') or b'/' in name or name in entries):
            raise Refused('Unsupported or duplicate source membership tree entry')
        entries[name] = mode, oid
        offset = end_name + 21
    return entries


def membership(path, root, load, parsed=None):
    oid = root
    parts = path.split('/')
    for index, part in enumerate(parts):
        raw = load(oid)
        if parsed is None:
            entries = tree_entries(raw)
        else:
            if oid not in parsed:
                parsed[oid] = tree_entries(raw)
            entries = parsed[oid]
        entry = entries.get(part.encode('utf-8'))
        if entry is None:
            raise Refused('Selected source is absent from the original committed tree')
        mode, child = entry
        if index < len(parts) - 1:
            if mode != b'40000':
                raise Refused('Selected source ancestor is not a Git tree')
            oid = child.hex()
        elif mode not in (b'100644', b'100755'):
            raise Refused('Selected source must be a regular committed blob')
        else:
            return int(mode, 8) & 0o777, child


def verify_source_subset(source, commit, proof):
    required = {'version', 'kind', 'commit', 'tree', 'selectedPaths', 'trees'}
    if (not isinstance(proof, dict) or set(proof) != required or type(proof['version']) is not int
            or proof['version'] != 1 or proof['kind'] != KIND
            or not all(isinstance(proof[key], str) and _OID.fullmatch(proof[key]) for key in ('commit', 'tree'))
            or not isinstance(commit, bytes)
            or git_id('commit', commit).hex() != proof['commit']
            or not commit.startswith(f"tree {proof['tree']}\n".encode())):
        raise Refused('Source subset proof does not bind its original commit and tree')
    paths = selected_paths(proof['selectedPaths'])
    trees = proof['trees']
    if (proof['selectedPaths'] != paths or not isinstance(source, dict) or set(source) != set(paths)
            or not isinstance(trees, dict) or not trees):
        raise Refused('Source subset proof has an unexpected selection or tree inventory')
    decoded, used, parsed = {}, set(), {}
    for oid, encoded in trees.items():
        if not isinstance(oid, str) or not _OID.fullmatch(oid) or not isinstance(encoded, str):
            raise Refused('Invalid source membership tree pin')
        try:
            raw = b64decode(encoded, validate=True)
        except (Base64Error, ValueError):
            raise Refused('Invalid source membership tree encoding') from None
        if b64encode(raw).decode() != encoded or git_id('tree', raw).hex() != oid:
            raise Refused('Source membership tree differs from its pin')
        decoded[oid] = raw

    def load(oid):
        if oid not in decoded:
            raise Refused('Source subset proof lacks an ancestor tree')
        used.add(oid)
        return decoded[oid]

    for path in paths:
        mode, blob = membership(path, proof['tree'], load, parsed)
        row = source[path]
        if (not isinstance(row, dict) or type(row.get('mode')) is not int or row['mode'] != mode
                or row.get('blob') != blob or type(row.get('size')) is not int or row['size'] < 0
                or not isinstance(row.get('sha256'), str) or not _SHA256.fullmatch(row['sha256'])):
            raise Refused('Selected source bytes or mode differ from their original Git membership')
        data = row.get('data')
        if data is not None and (not isinstance(data, bytes) or len(data) != row['size']
                                 or git_id('blob', data) != blob or sha256(data).hexdigest() != row['sha256']):
            raise Refused('Selected source content differs from its measured identity')
    if used != set(decoded):
        raise Refused('Source subset proof contains unused ancestor trees')


def source_subset(reader, paths):
    """Return the complete selection and ancestor proof, reading measured objects only."""
    paths = selected_paths(paths)
    trees, parsed = {}, {}

    def checked(kind, oid):
        size = reader.size(oid)
        if type(size) is not int or size < 0:
            raise Refused('Invalid measured source object size')
        raw = reader.checked_object(kind, oid, size)
        if len(raw) != size:
            raise Refused('Selected source changed while reading its pinned object')
        return raw

    def load(oid):
        if oid not in trees:
            trees[oid] = checked('tree', oid)
        return trees[oid]

    source = {}
    for path in paths:
        mode, oid = membership(path, reader.tree, load, parsed)
        data = checked('blob', oid.hex())
        source[path] = {'mode': mode, 'size': len(data), 'sha256': sha256(data).hexdigest(), 'blob': oid, 'data': data}
    commit = checked('commit', reader.commit)
    proof = {'version': 1, 'kind': KIND, 'commit': reader.commit, 'tree': reader.tree,
             'selectedPaths': paths, 'trees': {oid: b64encode(raw).decode() for oid, raw in sorted(trees.items())}}
    verify_source_subset(source, commit, proof)
    reader.verify_head()
    return source, commit, proof
