#!/usr/bin/env python3
"""Check static local links/assets and refuse externally hosted executable assets."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1] / 'site'
errors = []
count = 0
class Document(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.ids = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        for key in ('href', 'src', 'poster'):
            if attrs.get(key): self.links.append((tag,key,attrs[key]))
        if tag == 'img' and 'alt' not in attrs: errors.append('Image missing alt text')

docs = {}
for p in ROOT.rglob('*.html'):
    d=Document(); d.feed(p.read_text());docs[p.resolve()]=d
if not docs: errors.append('No HTML files')
for p,d in docs.items():
    for tag,key,value in d.links:
        u=urlsplit(value)
        if u.scheme or u.netloc:
            if key=='src' and tag in ('script','iframe'): errors.append(f'External executable asset: {value}')
            continue
        target=(p.parent/unquote(u.path)).resolve() if u.path else p
        if u.path.endswith('/'): target=target/'index.html'
        if not target.is_relative_to(ROOT.resolve()): errors.append(f'Outside site: {value}');continue
        if not target.is_file(): errors.append(f'Missing local target: {p.name} -> {value}');continue
        if u.fragment and target in docs and unquote(u.fragment) not in docs[target].ids: errors.append(f'Missing anchor: {value}')
        count += 1
if errors: raise SystemExit('\n'.join(errors))
print(f'Checked {len(docs)} HTML documents and {count} local links/assets')
