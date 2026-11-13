"""Bounded lexical command evidence from committed blobs, never an execution closure.

No shell evaluation, runtime resolution, filesystem reads, or project execution.
Every selected file is whole. Unknowns are evidence gaps, not safety approvals.
"""
from collections import deque
import json
import hashlib
import posixpath
import re
import shlex

from aim_ci.project_discovery import SCRIPT_NAME
from aim_ci.project_onboarding import _source_path
from aim_ci.owned_paths import Refused
from flow_requirement_intake import SECRET_PATH

LIMITS = {'files': 96, 'fileBytes': 262144, 'sourceBytes': 524288, 'edges': 2048, 'commands': 256}
SUFFIXES = ('.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.astro', '.json', '.sh', '.py', '.md', '.html', '.css')
IMPORT = re.compile(r'''(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)["']([^"'\n]+)["']''')
LITERAL = re.compile(r'''["']([^"'\n\\]{1,300})["']''')
CHILD = re.compile(r'''\b(?:spawn|spawnSync|execFile|execFileSync)\s*\(\s*['"](npm|node)['"]\s*,\s*\[([^\]]*)\]''')

# Deliberately review-local: preserve historical intake selection semantics.
# Expressions naming credentials are not credential bytes. Only quoted literal
# assignments, recognizable provider tokens, PEMs and Bearer values trigger this.
SECRET_TEXT = re.compile(
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY)|\b(?:sk-proj-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|"
    r"(?i:\bBearer[ \t]+[A-Za-z0-9_.~+/-]{16,}=*)|"
    r"(?i:[\"']?\b(?:password|api[_-]?key|client[_-]?secret|access[_-]?token)[\"']?\s*[:=]\s*"
    r"(?P<quote>[\"'`])[^\s\"'`${}]{8,}(?P=quote))")


def project_credential_literals(text, *, withheld_values=None):
    """Bounded display projection; never alter the committed execution input.

    Unsupported markers (including private keys and unquoted provider tokens)
    still exclude the whole file. Only single-line quoted literal values can
    be withheld. Opaque identities are local sequence numbers, not secret hashes.
    """
    literals = list(re.finditer(r"(['\"`])((?:\\.|(?!\1)[^\\\r\n])*)\1", text))
    spans = []
    for match in SECRET_TEXT.finditer(text):
        if match.group().startswith('-----BEGIN '):
            return None
        if match.groupdict().get('quote'):
            start, end = match.start('quote') + 1, match.end() - 1
        else:
            if not any(m.start(2) <= match.start() and match.end() <= m.end(2) for m in literals):
                return None
            start, end = match.span()
            if match.group().lower().startswith('bearer '):
                start += len(match.group()) - len(match.group().lstrip().split(None, 1)[1])
        if '\n' in text[start:end] or '\r' in text[start:end] or len(spans) >= 64:
            return None
        if spans and start < spans[-1][1]:
            return None
        spans.append((start, end))
    if not spans:
        return None
    values = {text[start:end] for start, end in spans}
    if any('\\' in value for value in values):
        return None  # Alternate escaped representations need a real language parser.
    repeated = set()
    for value in values:
        offset = 0
        while True:
            start = text.find(value, offset)
            if start < 0:
                break
            end = start + len(value)
            if not any(m.start(2) <= start and end <= m.end(2) for m in literals):
                return None
            repeated.add((start, end))
            offset = start + 1
            if len(repeated) > 64:
                return None
    spans = []
    for start, end in sorted(repeated, key=lambda pair: (pair[0], -pair[1])):
        if spans and start < spans[-1][1]:
            if end <= spans[-1][1]:
                continue
            return None
        spans.append((start, end))
    identities, redactions, parts, offset = {}, [], [], 0
    for start, end in spans:
        value = text[start:end]
        identity = identities.setdefault(value, len(identities) + 1)
        line_start = text.rfind('\n', 0, start) + 1
        redactions.append({'startLine': text.count('\n', 0, start) + 1,
            'startColumn': start - line_start + 1, 'endColumn': end - line_start,
            'opaqueId': identity})
        parts.extend((text[offset:start], '[opaque ' + str(identity) + ']'))
        offset = end
    parts.append(text[offset:])
    displayed = ''.join(parts)
    if SECRET_TEXT.search(displayed) or len(displayed.splitlines()) != len(text.splitlines()):
        return None
    if withheld_values is not None:
        withheld_values.update(values)
    return {'text': displayed, 'sourceProjection': {
        'format': 'unchanged-credential-literals-v1',
        'originalSha256': hashlib.sha256(text.encode()).hexdigest(),
        'redactions': redactions,
        'qualification': 'Only the displayed text is reviewed. Literal values are withheld, not qualified as fixtures or safe. Execution uses the unchanged pinned Git bytes.'}}


def derive(reader, stages, table=None, *, project_secrets=False):
    """Return exact candidate paths and source-cited lexical observations.

    Read definitions for selected npm stages, their lifecycle and nested scripts,
    node entrypoints, local imports, and existing literal project file references.
    This intentionally does not parse arbitrary JS or shell or prove termination.
    """
    if type(project_secrets) is not bool:
        raise Refused('Credential projection must be an explicit boolean')
    table = table if table is not None else {p: (m, k, o) for p, m, k, o in reader.entries(reader.commit, recursive=True)}
    texts, records, edges, gaps = {}, {}, [], []
    commands, files, deferred_files = deque(), deque(), deque()
    seen_commands, seen_scripts, seen_files = set(), set(), set()
    consumed = 0

    def gap(reason, source, target=None):
        row = {'reason': reason, 'source': source}
        if target is not None: row['target'] = target
        if row not in gaps:
            if len(gaps) >= LIMITS['edges']: raise Refused('Command context omission metadata exceeds bound')
            gaps.append(row)

    def edge(kind, source, target):
        if len(edges) >= LIMITS['edges']: raise Refused('Command context traversal exceeds bound')
        edges.append({'kind': kind, 'source': source, 'target': target})

    def path_at(root, value):
        if value.startswith('/') or '\\' in value or any(x in value for x in ('$', '`', '*', '?')):
            return None
        path = posixpath.normpath(posixpath.join(root, value))
        try: return _source_path(path)
        except Refused: return None

    def read(path, source):
        nonlocal consumed
        if path in records: return texts.get(path)
        record = records[path] = {'path': path, 'status': 'omitted'}
        reason = None
        if SECRET_PATH.search(path) or '/config/' in '/' + path:
            reason = 'secret-or-credential-configuration-path'
        elif path not in table: reason = 'not-in-selected-commit'
        elif table[path][1] != 'blob' or table[path][0] not in ('100644', '100755'): reason = 'non-regular-file'
        elif not path.endswith(SUFFIXES): reason = 'unsupported-source-type'
        elif len(texts) >= LIMITS['files']: reason = 'file-count-bound'
        else:
            size = reader.size(table[path][2])
            if size > LIMITS['fileBytes'] or consumed + size > LIMITS['sourceBytes']:
                reason = 'whole-file-byte-bound'
            else:
                raw = reader.blob(table[path][2], maximum=LIMITS['fileBytes']); consumed += len(raw)
                try: text = raw.decode('utf-8')
                except UnicodeError: reason = 'non-utf8'
                else:
                    if '\0' in text: reason = 'binary'
                    else:
                        projected = project_credential_literals(text) if project_secrets and SECRET_TEXT.search(text) else None
                        if SECRET_TEXT.search(text) and projected is None:
                            reason = 'suspected-secret-content'
                        else:
                            if projected:
                                text = projected['text']
                                record['sourceProjection'] = projected['sourceProjection']
                                gap('credential-literal-values-withheld', source, path)
                            texts[path] = text
                            record.update(status='included', gitObject=table[path][2], bytes=len(raw))
                            return text
        record['reason'] = reason; gap(reason, source, path)
        return None

    def script(root, name, source, lifecycle=True):
        if not isinstance(name, str) or not re.fullmatch(SCRIPT_NAME, name):
            gap('unsupported-script-name', source); return
        manifest = path_at(root, 'package.json')
        if manifest is None:
            gap('unsupported-package-root', source, root); return
        key = (root, name, lifecycle)
        edge('npm-script', source, {'path': manifest, 'script': name, 'lifecycle': lifecycle})
        if key in seen_scripts: return
        seen_scripts.add(key)
        text = read(manifest, source)
        if text is None: return
        try:
            value = json.loads(text); scripts = value.get('scripts', {})
            if not isinstance(scripts, dict): raise ValueError()
        except (ValueError, AttributeError):
            gap('invalid-package-scripts', {'path': manifest}); return
        names = ['pre' + name, name, 'post' + name] if lifecycle else [name]
        for selected in names:
            if selected not in scripts:
                if selected == name: gap('missing-npm-script', {'path': manifest}, selected)
                continue
            body = scripts[selected]
            line = next((i for i, line in enumerate(text.splitlines(), 1) if re.search(r'"' + re.escape(selected) + r'"\s*:', line)), 1)
            ref = {'path': manifest, 'startLine': line, 'endLine': line, 'script': selected}
            if not isinstance(body, str): gap('non-text-script', ref); continue
            commands.append((root, body, ref))

    def npm(root, args, source):
        args = list(args)
        # npm accepts its prefix option before or after the subcommand. Never
        # mistake a prefixed invocation for a script in the inherited root.
        end = args.index('--') if '--' in args else len(args)
        options, prefix = [], None
        i = 0
        while i < end:
            token = args[i]
            if token == '--prefix' or token.startswith('--prefix='):
                if prefix is not None:
                    gap('ambiguous-prefix', source); return
                if token == '--prefix':
                    i += 1
                    if i >= end: gap('missing-prefix', source); return
                    prefix = args[i]
                else: prefix = token[len('--prefix='):]
            else: options.append(token)
            i += 1
        args = options + args[end:]
        if prefix is not None:
            root = root if prefix in ('.', './') else path_at(root, prefix)
            if root is None: gap('unsupported-prefix', source); return
        if args[:1] in (['run'], ['run-script']) and len(args) >= 2 and re.fullmatch(SCRIPT_NAME, args[1]):
            script(root, args[1], source)
            if len(args) > 2: gap('npm-arguments-not-interpreted', source)
        elif args in (['test'], ['start'], ['stop'], ['restart']): script(root, args[0], source)
        elif args in (['ci'], ['install']):
            manifest = path_at(root, 'package.json'); text = read(manifest, source) if manifest else None
            try:
                scripts = json.loads(text or '{}').get('scripts', {})
                if not isinstance(scripts, dict): raise ValueError()
            except (ValueError, AttributeError):
                gap('invalid-package-scripts', {'path': manifest})
                scripts = {}
            for name in ('preinstall', 'install', 'postinstall', 'prepublish', 'preprepare', 'prepare', 'postprepare'):
                if name in scripts: script(root, name, source, lifecycle=False)
            gap('dependency-install-lifecycle-and-native-defaults-not-resolved', source, root)
        else: gap('unsupported-npm-invocation', source)

    def argv(root, args, source):
        if not args: return
        if args[0] == 'npm': npm(root, args[1:], source)
        elif args[0] == 'node' and len(args) >= 2 and not args[1].startswith('-'):
            target = path_at(root, args[1])
            if target:
                edge('node-entrypoint', source, target); files.append((target, root, source))
            else: gap('dynamic-or-outside-node-entrypoint', source)
        else: gap('unresolved-tool-or-shell-command', source)

    for i, stage in enumerate(stages):
        source = {'stage': i}
        root = stage.get('root', '.')
        _source_path(root, directory=True)
        if stage.get('operation') == 'run': script(root, stage.get('script', ''), source)
        elif stage.get('operation') == 'ci': npm(root, ['ci'], source)
        else: gap('unsupported-selected-stage', source)

    while commands or files or deferred_files:
        if commands:
            root, body, source = commands.popleft()
            key = (root, body)
            if key in seen_commands: continue
            if len(seen_commands) >= LIMITS['commands']:
                gap('command-count-bound', source); continue
            seen_commands.add(key)
            # Split only literal simple shell chains; never execute/evaluate a shell.
            if any(c in body for c in ('$','`','\n','(',')','<','>','{','}')):
                gap('dynamic-or-unsupported-shell-syntax', source); continue
            try:
                lexer = shlex.shlex(body, posix=True, punctuation_chars=';&|')
                lexer.whitespace_split = True; lexer.commenters = ''
                tokens = list(lexer)
            except ValueError: gap('unparseable-command', source); continue
            part = []
            for token in [*tokens, '&&']:
                if token in ('&&', '||', ';', '|', '&'):
                    argv(root, part, source); part = []
                    if token != '&&': gap('shell-control-flow-not-interpreted', source)
                else: part.append(token)
            continue
        path, root, source = (files if files else deferred_files).popleft()
        if (path, root) in seen_files: continue
        seen_files.add((path, root))
        text = read(path, source)
        if text is None: continue
        for match in IMPORT.finditer(text):
            value = match.group(1); line = text.count('\n', 0, match.start()) + 1
            ref = {'path': path, 'startLine': line, 'endLine': line}
            if not value.startswith('.'):
                if not value.startswith('node:'): gap('external-or-aliased-import', ref, value)
                continue
            target = path_at(posixpath.dirname(path), value)
            candidates = [target] if target else []
            if target and target not in table:
                candidates += [target + suffix for suffix in ('.js','.mjs','.cjs','.ts','.tsx','.jsx')] + [target + '/index.js', target + '/index.ts']
            found = [p for p in candidates if p in table]
            if len(found) != 1:
                gap('unresolved-or-ambiguous-local-import', ref, value); continue
            edge('literal-local-import', ref, found[0])
            queue = deferred_files if project_secrets and found[0].endswith(('.json', '.md', '.css', '.html')) else files
            queue.append((found[0], root, ref))
        # Lexical candidates, not claims that a string is actually read at runtime.
        for match in LITERAL.finditer(text):
            value = match.group(1)
            if '/' not in value or not value.endswith(SUFFIXES): continue
            ref = {'path': path, 'startLine': text.count('\n', 0, match.start()) + 1}
            for base in dict.fromkeys((root, posixpath.dirname(path))):
                target = path_at(base, value)
                if target and target in table:
                    edge('existing-literal-source-candidate', ref, target)
                    (deferred_files if project_secrets else files).append((target, root, ref))
        for match in CHILD.finditer(text):
            ref = {'path': path, 'startLine': text.count('\n', 0, match.start()) + 1}
            values = list(LITERAL.finditer(match.group(2)))
            residue = LITERAL.sub('', match.group(2)).replace(',', '').strip()
            if residue: gap('dynamic-child-arguments', ref); continue
            argv(root, [match.group(1), *(v.group(1) for v in values)], ref)
        if re.search(r'\b(?:spawn|spawnSync|exec|execSync|execFile|execFileSync|readFile|readFileSync|import|require)\b', text):
            gap('runtime-resolution-and-dynamic-references-not-proven', {'path': path})
    reader.verify_head()
    return {'schemaVersion': 1, 'commit': reader.commit, 'limits': dict(LIMITS),
            'scope': 'Lexical candidate evidence only; no execution, safety, termination, dependency closure or semantic completeness claim. All omissions and dynamic runtime behavior remain unverified.',
            **({'sourceProjectionPolicy': 'unchanged-credential-literals-v1'} if project_secrets else {}),
            'sourcePaths': sorted(texts), 'sourceBytes': sum(len(t.encode()) for t in texts.values()),
            'files': sorted(records.values(), key=lambda r: r['path']), 'traversal': edges, 'unresolved': gaps}
