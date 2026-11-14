"""Real local Git retrieval, no provider or target project commands."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_requirement_intake as intake
from aim_ci.owned_paths import Refused

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.control = self.root / 'control'; self.control.mkdir()
        self.repo = self.root / 'project'; self.repo.mkdir()
        self.git = Path(os.environ.get('AIM_TEST_GIT', shutil.which('git')))
        if not self.git.exists(): self.skipTest('Git 2.54 executable required; set AIM_TEST_GIT')
        self.run_git('init', '-q'); self.run_git('config','user.email','test@example.invalid'); self.run_git('config','user.name','Test')
        self.put('package.json', '{"scripts":{"build":"node scripts/build.mjs"}}')
        self.put('README.md', '# Build and test\nRun npm run build.')
        self.put('scripts/build.mjs', 'console.log("compile")')
        self.put('src/Carousel.ts', 'import "./timer.js";\n// Carousel pause resume autoplay button rotation\n')
        self.put('src/timer.js', '// internal scheduling helper\n')
        self.put('src/irrelevant.js', '// unrelated inventory\n')
        self.put('src/.env', 'PRIVATE=secret')
        self.put('src/credentials.json', '{"token":"private"}')
        self.put('src/data.json', '{"data":"carousel"}')
        self.put('src/secret.js', 'const token = "ghp_abcdefghijklmnopqrstuvw";')
        self.put('src/binary.ts', b'\x00not text')
        (self.repo/'src/link.ts').symlink_to('Carousel.ts')
        self.run_git('add','.'); self.run_git('commit','-qm','fixture')
        self.source = {'checkout':str(self.repo),'commit':self.run_git('rev-parse','HEAD').strip(), 'git':str(self.git),'gitSha256':hashlib.sha256(self.git.read_bytes()).hexdigest()}
        self.req = self.root/'requirement.md'; self.req.write_text('Pause resume carousel autoplay button. Preserve carousel rotation.'); self.req.chmod(0o600)
        self.requirement = {'path':str(self.req),'sha256':hashlib.sha256(self.req.read_bytes()).hexdigest()}
    def run_git(self,*args):
        return subprocess.check_output([str(self.git),*args],cwd=self.repo,stderr=subprocess.DEVNULL).decode()
    def put(self,p,data):
        path=self.repo/p; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data if isinstance(data,bytes) else data.encode())
    def derive(self,**kw):
        return intake.derive(self.control,self.source,self.requirement,['src/','scripts/'],**kw)
    def test_committed_only_deterministic_selection_and_separation(self):
        before=self.derive()
        self.put('src/Carousel.ts','dirty unrelated'); self.put('src/untracked.ts','carousel pause resume')
        after=self.derive(); self.assertEqual(before,after)
        sel=after['selection']; self.assertIn('src/Carousel.ts',sel['editablePaths']); self.assertIn('src/timer.js',sel['reviewSources'])
        self.assertIn('scripts/build.mjs',sel['onboardingSources']); self.assertNotIn('scripts/build.mjs',sel['editablePaths'])
        self.assertFalse(set(sel['reviewSources']) & set(sel['onboardingSources']))
        coverage={r['path']:r for r in after['coverage']}
        self.assertNotIn('src/untracked.ts',coverage)
        for p in ['src/.env','src/credentials.json','src/secret.js','src/binary.ts','src/link.ts','src/data.json']:
            self.assertEqual(coverage[p]['status'],'excluded',p)
            self.assertNotIn('sha256',coverage[p],p)
    def test_bounds_are_incomplete_never_partial_text(self):
        limits={**intake.DEFAULT_LIMITS,'contextBytes':1}
        r=self.derive(limits=limits); self.assertEqual(r['status'],'incomplete'); self.assertTrue(r['incompletePaths'])
        with self.assertRaises(Refused): intake.assemble_config({},r)
        r=self.derive(limits={**intake.DEFAULT_LIMITS,'fileBytes':1})
        self.assertEqual(r['status'],'incomplete')
    def test_exact_pins_scope_and_budget_preservation(self):
        report=self.derive()
        base={'source':self.source,'requirement':self.requirement,'resourceCatalog':{'sha256':'x'},'historicalBudget':{'amount':42}}
        result=intake.assemble_config(base,report)
        self.assertEqual(result['historicalBudget'],base['historicalBudget']); self.assertEqual(result['resourceCatalog'],base['resourceCatalog'])
        self.assertNotIn('editablePaths',base)
        with self.assertRaises(Refused): intake.derive(self.control,self.source,self.requirement,['../'])
        self.req.write_text('changed')
        with self.assertRaises(Refused): self.derive()
    def test_process_secret_blocks_and_missing_script_is_explicit(self):
        self.put('package.json', '{"scripts":{"test":"node backend/missing.js"}}')
        self.run_git('add','.'); self.run_git('commit','-qm','missing process reference')
        self.source['commit']=self.run_git('rev-parse','HEAD').strip()
        report=self.derive()
        self.assertIn('backend/missing.js',report['incompletePaths'])
        self.put('scripts/build.mjs', 'const key = "ghp_abcdefghijklmnopqrstuvw";')
        self.run_git('add','.'); self.run_git('commit','-qm','unsafe process')
        self.source['commit']=self.run_git('rev-parse','HEAD').strip()
        self.assertIn('scripts/build.mjs',self.derive()['incompletePaths'])

    def test_head_mismatch_refused(self):
        self.put('new.txt','new');self.run_git('add','.');self.run_git('commit','-qm','changed')
        with self.assertRaises(Refused): self.derive()

    def test_swedish_requirement_preserves_words_and_code_survives_prose_outlier(self):
        self.put('src/ShowsFeed.astro', '<th>Location</th><a>Tickets</a> Upcoming Past shows')
        # An unrelated prose hit used to raise the global threshold and suppress
        # the component containing the literal UI labels.
        prose = ' '.join('unikord'+str(i) for i in range(100))
        self.put('src/unrelated.md', prose)
        self.run_git('add','.'); self.run_git('commit','-qm','multilingual labels')
        self.source['commit']=self.run_git('rev-parse','HEAD').strip()
        self.req.write_text('Ändring: Ta bort Location och ändra Tickets till READ MORE i Upcoming och Past shows. '+prose)
        self.requirement['sha256']=hashlib.sha256(self.req.read_bytes()).hexdigest()
        report=self.derive()
        self.assertIn('src/ShowsFeed.astro',report['selection']['editablePaths'])
        tokens=intake.terms('önskad ändring på åäö ShowsFeed')
        self.assertIn('åäö',tokens); self.assertNotIn('ndring',tokens)
        self.assertNotIn('och',intake.terms('och Location'))

    def test_document_score_cannot_set_feature_code_threshold(self):
        self.put('src/Panel.ts', '// amber violet feature cobalt widget')
        self.put('src/notes.md', 'amber violet '+ ' '.join('topic'+str(i) for i in range(80)))
        self.run_git('add','.');self.run_git('commit','-qm','doc threshold outlier')
        self.source['commit']=self.run_git('rev-parse','HEAD').strip()
        self.req.write_text('amber violet feature cobalt widget '+' '.join('topic'+str(i) for i in range(80)))
        self.requirement['sha256']=hashlib.sha256(self.req.read_bytes()).hexdigest()
        report=self.derive();coverage={r['path']:r for r in report['coverage']}
        self.assertGreater(coverage['src/notes.md']['score'],coverage['src/Panel.ts']['score'])
        self.assertIn('src/Panel.ts',report['selection']['editablePaths'])

    def test_shared_dependency_does_not_expand_into_whole_site(self):
        self.put('src/timer.js', 'import "./shared/layout.js"; // internal scheduling helper')
        self.put('src/shared/layout.js', 'import "./theme.css"; // common layout')
        self.put('src/shared/theme.css', '/* shared theme */')
        self.put('src/Unrelated.ts', 'import "./timer.js"; // unrelated page')
        self.run_git('add','.');self.run_git('commit','-qm','shared dependency fanout')
        self.source['commit']=self.run_git('rev-parse','HEAD').strip()
        report=self.derive();selected=report['selection']['reviewSources']
        self.assertIn('src/Carousel.ts',selected);self.assertIn('src/timer.js',selected)
        self.assertNotIn('src/shared/layout.js',selected)
        self.assertNotIn('src/Unrelated.ts',selected)
        self.assertTrue(any(d['resolvedPath']=='src/shared/layout.js' for d in report['omittedDependencies']))

    def test_unread_recognized_process_paths_cannot_claim_selected_coverage(self):
        for path in ('config/build.mjs', 'scripts/build.rb'):
            self.put(path, 'carousel process definition')
        self.run_git('add', '.'); self.run_git('commit', '-qm', 'Unread process formats')
        self.source['commit'] = self.run_git('rev-parse', 'HEAD').strip()
        report = self.derive()
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue({'config/build.mjs', 'scripts/build.rb'} <= set(report['incompletePaths']))
        with self.assertRaises(Refused): intake.assemble_config({}, report)

if __name__=='__main__': unittest.main()
