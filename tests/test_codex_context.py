"""Focused context, source availability and immutable expansion evidence."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import flow_codex_context as context
from aim_ci.owned_paths import Refused


class ContextTests(unittest.TestCase):
    def packet(self):
        return {'requirement':{'original':'Change the Tickets label in the shows widget','criteria':[]},
            'sourceRanking':{'src/Shows.astro':100,'src/pages/index.astro':10,'src/unrelated.js':1},
            'editableSources':{'src/Shows.astro':'<div class="show-card">Tickets</div>\n',
                'src/site.css':'.unrelated { color: red; }\n'*1000+'.show-card { display: grid; }\n',
                'src/pages/index.astro':'import Shows from "../Shows.astro";\n<Shows />\n'},
            'contextSources':{'package-lock.json':'{"packages":'+('"lock",'*20000)+'"end"}',
                'scripts/deploy.mjs':'// index unrelated release work\n',
                'AGENTS.md':'Use the project process.\n'}}

    def test_task_focus_keeps_exact_css_ranges_and_full_lock_available(self):
        p=self.packet();before=copy.deepcopy(p)
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp).resolve();wire,assets=context.project(p,'model-implementation',state)
            context.retain(state,assets)
            records={r['path']:r for r in wire['contextIndex']}
            self.assertEqual(records['package-lock.json']['display'],'on-demand')
            self.assertEqual(records['scripts/deploy.mjs']['display'],'on-demand')
            self.assertEqual(records['src/Shows.astro']['display'],'whole')
            css=records['src/site.css'];self.assertEqual(css['display'],'excerpts')
            for record in records.values():
                raw=Path(record['fullSource']['path']).read_bytes()
                original={**p['contextSources'],**p['editableSources']}[record['path']]
                self.assertEqual(raw,original.encode());self.assertEqual(hashlib.sha256(raw).hexdigest(),record['sha256'])
            lines=p['editableSources']['src/site.css'].splitlines(keepends=True)
            excerpts='\n'.join(''.join(lines[r['startLine']-1:r['endLine']]) for r in css['ranges'])
            self.assertEqual(wire['editableSources']['src/site.css'],excerpts)
            self.assertIn('.show-card',excerpts);self.assertLess(len(excerpts),len(p['editableSources']['src/site.css']))
        self.assertEqual(p,before,'Projection must not rewrite the full source-review packet')

    def test_dependency_task_and_changed_source_are_never_hidden_by_file_kind(self):
        p=self.packet();p['requirement']['original']='Audit package dependencies and the lockfile'
        wire,_=context.project(p,'model-source-review',Path('/tmp/context-fixture'))
        self.assertIn('package-lock.json',wire['contextSources'])
        full='first\n'+'unrelated\n'*200+'required change\n'
        packet={'requirement':{'original':'focused change'},'changes':[{'path':'src/changed.js'}],
            'sources':[{'path':'candidate/src/changed.js','text':full,'sha256':hashlib.sha256(full.encode()).hexdigest()}]}
        wire,_=context.project(packet,'model-source-review',Path('/tmp/context-fixture'))
        self.assertEqual(wire['sources'][0]['text'],full)

    def test_missing_or_changed_expansion_file_refuses_verification_without_recreation(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp).resolve();_,assets=context.project(self.packet(),'model-implementation',state)
            context.retain(state,assets);context.retain(state,assets,verify_only=True)
            path=next((state/'context').iterdir());path.write_text('changed')
            with self.assertRaises(Refused):context.retain(state,assets,verify_only=True)
            path.unlink()
            with self.assertRaises(Refused):context.retain(state,assets,verify_only=True)
            self.assertFalse(path.exists())


if __name__=='__main__':unittest.main()
