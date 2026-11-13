"""Verify actual isolated processes, source mutation and authenticated input reuse."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aim_ci import verification_runtime as runtime


class VerificationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.controller = self.root / 'controller'; self.controller.mkdir()
        lib = self.controller / 'lib/aim_ci'; lib.mkdir(parents=True)
        (lib / '__init__.py').write_text('')
        (lib / 'project_validation.py').write_text('verification_process = None\n')
        self.data = self.root / 'source.txt'; self.data.write_text('original')
        self.entry = self.controller / 'aim-flow.py'
        self.entry.write_text('import os,json\nfrom pathlib import Path\n'
            'd=os.open(' + repr(str(self.root)) + ',os.O_RDONLY|os.O_DIRECTORY)\n'
            'f=os.open("source.txt",os.O_RDONLY,dir_fd=d)\n'
            'raw=os.read(f,100);os.close(f);os.close(d)\n'
            'print(json.dumps({"status":"verified","source":raw.decode()}))\n')
        self.command = [str(Path(sys.executable).resolve()), '-IBS', str(self.entry), 'work', '--verify']
        self.store = self.root / 'store'
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'LC_ALL': 'C'}
        self.launches = []

    def invoke(self, **options):
        def launch(*args, **kw):
            self.launches.append(args[0])
            return runtime._transport(*args, **kw)
        return runtime.evaluate(self.command, self.root, self.env, launch, store=self.store, **options)

    def test_real_process_cache_hit_and_changed_or_deleted_descriptor_relative_source(self):
        first = self.invoke(); self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.invoke().stdout, first.stdout)
        self.assertEqual(len(self.launches), 1, 'unchanged input must not run verifier again')
        self.data.write_text('changed')
        changed = self.invoke(); self.assertIn(b'changed', changed.stdout)
        self.assertEqual(len(self.launches), 2)
        self.data.unlink()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(len(self.launches), 3)
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(len(self.launches), 4, 'failed verification must never be cached')

    def test_cached_output_tamper_is_refused(self):
        self.invoke()
        proof = next(self.store.glob('*.json'))
        value = json.loads(proof.read_text()); value['proof']['stdout'] = 'e30='
        proof.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'authentication'): self.invoke()
        self.assertEqual(len(self.launches), 1)

    def test_early_onboarding_without_validation_module_is_traced_and_source_bound(self):
        (self.controller / 'lib/aim_ci/project_validation.py').unlink()
        self.command[3] = 'verify-project-onboarding'
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.invoke().stdout, first.stdout)
        self.assertEqual(len(self.launches), 1)
        self.data.write_text('changed historical input')
        self.assertIn(b'changed historical input', self.invoke().stdout)
        self.assertEqual(len(self.launches), 2)

    def test_missing_newer_or_broken_existing_validation_dependency_is_not_hidden(self):
        module = self.controller / 'lib/aim_ci/project_validation.py'
        module.unlink()
        self.entry.write_text('from aim_ci import project_validation\n')
        self.assertNotEqual(self.invoke().returncode, 0)
        module.write_text('import deliberately_absent_validation_dependency\n')
        self.command[3] = 'verify-project-onboarding'
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_minimal_pinned_producer_without_validation_package_is_source_bound(self):
        package = self.controller / 'lib/aim_ci'
        for path in package.iterdir():
            path.unlink()
        package.rmdir()
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.invoke().stdout, first.stdout)
        self.assertEqual(len(self.launches), 1)
        self.data.write_text('changed minimal producer input')
        self.assertIn(b'changed minimal producer input', self.invoke().stdout)
        self.assertEqual(len(self.launches), 2)
        # The decision not to patch a transport is itself source-bound.
        package.mkdir()
        (package / '__init__.py').write_text('')
        (package / 'project_validation.py').write_text('verification_process = None\n')
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(len(self.launches), 3)

    def test_controller_code_change_invalidates_input_closure(self):
        self.invoke()
        self.entry.write_text(self.entry.read_text().replace('verified', 'updated'))
        self.assertIn(b'updated', self.invoke().stdout)
        self.assertEqual(len(self.launches), 2)

    def test_directory_inventory_and_link_targets_cannot_escape_input_checks(self):
        directory = self.root / 'inventory'; directory.mkdir()
        (directory / 'one').write_text('one')
        self.entry.write_text('import os,json\nprint(json.dumps(sorted(os.listdir(' + repr(str(directory)) + '))))\n')
        first = self.invoke(); self.assertEqual(self.invoke().stdout, first.stdout)
        self.assertEqual(len(self.launches), 1)
        (directory / 'two').write_text('two')
        self.assertIn(b'two', self.invoke().stdout); self.assertEqual(len(self.launches), 2)
        link = self.root / 'linked.txt'; link.symlink_to(self.data)
        self.entry.write_text('from pathlib import Path\nprint(Path(' + repr(str(link)) + ').read_text())\n')
        first = self.invoke(); self.assertEqual(self.invoke().stdout, first.stdout)
        count = len(self.launches); self.data.write_text('changed-target')
        self.assertIn(b'changed-target', self.invoke().stdout); self.assertEqual(len(self.launches), count + 1)

    def test_nested_verifier_inputs_are_rechecked_without_recursive_reexecution(self):
        original = self.entry.read_text()
        self.entry.write_text('import sys,json,os\nfrom pathlib import Path\nfrom aim_ci import project_validation as v\n'
            'if "--child" not in sys.argv:\n'
            ' c=[sys.executable,"-IBS",__file__,"work","--verify","--child"]\n'
            ' p=v.verification_process(c,Path.cwd(),dict(os.environ))\n'
            ' print(p.stdout.decode().strip()); raise SystemExit(p.returncode)\n' + original)
        first = self.invoke(); self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(list(self.store.glob('*.json'))), 2)
        self.assertEqual(self.invoke().stdout, first.stdout); self.assertEqual(len(self.launches), 1)
        self.data.write_text('nested-change')
        self.assertIn(b'nested-change', self.invoke().stdout)
        self.assertEqual(len(self.launches), 2)

    def test_git_object_batches_are_source_bound_and_deleted_objects_invalidate_reuse(self):
        import shutil
        git = str(Path(shutil.which('git')).resolve())
        repo = self.root / 'repository'; repo.mkdir()
        runtime._NATIVE_RUN([git, 'init', '-q', str(repo)], check=True, capture_output=True)
        oid = runtime._NATIVE_RUN([git, 'hash-object', '-w', '--stdin'], cwd=repo,
            input=b'committed fixture bytes', capture_output=True, check=True).stdout.decode().strip()
        self.entry.write_text('import subprocess,json\n'
            'c=' + repr([git, '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null']) + '\n'
            'for kind in ("-s","blob"):\n'
            ' p=subprocess.run(c+["cat-file",kind,' + repr(oid) + '],cwd=' + repr(str(repo)) + ',capture_output=True)\n'
            ' if p.returncode: raise SystemExit(p.returncode)\n'
            'print(json.dumps({"verified":True}))\n')
        first = self.invoke(); self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.invoke().stdout, first.stdout); self.assertEqual(len(self.launches), 1, [p.read_text() for p in self.store.glob('*.diagnostic')])
        (repo / '.git/objects' / oid[:2] / oid[2:]).unlink()
        self.assertNotEqual(self.invoke().returncode, 0); self.assertEqual(len(self.launches), 2)

    def git_fixture(self, script):
        git = self.root / 'git'
        git.write_text('#!/bin/sh\n' + script); git.chmod(0o700)
        self.entry.write_text('import subprocess\n'
            'p=subprocess.run(' + repr([str(git), '--version']) + ',capture_output=True)\n'
            'print(p.stdout.decode().strip())\n')
        return git

    def test_explicit_deadline_also_bounds_native_queries_on_cache_hit(self):
        marker = self.root / 'slow'
        self.git_fixture('if [ -e "' + str(marker) + '" ]; then sleep 5; fi\necho fixture\n')
        first = self.invoke(timeout_seconds=1)
        self.assertEqual(first.returncode, 0, first.stderr)
        marker.touch(); started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.invoke(timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(self.launches), 1, 'cache-hit revalidation must respect the deadline')

    def test_explicit_deadline_includes_post_verification_query_validation(self):
        marker = self.root / 'queried'
        self.git_fixture('if [ -e "' + str(marker) + '" ]; then sleep 5; fi\n'
            'touch "' + str(marker) + '"\necho fixture\n')
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.invoke(timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(list(self.store.glob('*.json')), 'deadline exhaustion must not retain success')

    def test_timeout_cleans_nested_verifier_group_for_both_entry_transports(self):
        from aim_ci import project_validation
        pid = self.root / 'child-pid'
        survived = self.root / 'child-survived'
        self.entry.write_text('import sys,os,time\nfrom pathlib import Path\n'
            'from aim_ci import project_validation as v\n'
            'if "--child" in sys.argv:\n'
            ' Path(' + repr(str(pid)) + ').write_text(str(os.getpid()))\n'
            ' time.sleep(1)\n'
            ' Path(' + repr(str(survived)) + ').write_text("orphan")\n'
            'else:\n'
            ' p=v.verification_process([sys.executable,"-IBS",__file__,"work","--verify","--child"],'
            'Path.cwd(),dict(os.environ))\n'
            ' print(p.stdout.decode())\n')
        for transport in (runtime._transport, project_validation._verification_process):
            with self.subTest(transport=transport.__module__):
                pid.unlink(missing_ok=True)
                with self.assertRaisesRegex((ValueError, project_validation.Refused), 'deadline'):
                    runtime.evaluate(self.command, self.root, self.env, transport,
                        store=self.store, timeout_seconds=.5)
                self.assertTrue(pid.exists(), 'nested verifier must actually have started')
                time.sleep(.7)
                self.assertFalse(survived.exists(), 'timed-out nested verifier escaped group cleanup')

    def test_caller_root_home_and_installation_do_not_partition_proof(self):
        from unittest.mock import patch
        self.entry.write_text('import os,json\nprint(json.dumps({"cwd":os.getcwd(),"home":os.environ["HOME"]}))\n')
        first = self.invoke()
        self.assertEqual(json.loads(first.stdout), {'cwd': str(self.controller.resolve()), 'home': str(self.controller)})
        def launch(*args, **kw):
            self.launches.append(args[0]); return runtime._transport(*args, **kw)
        second = runtime.evaluate(self.command, self.root / 'other-caller',
            {**self.env, 'HOME': '/different-caller'}, launch, store=self.store)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(self.launches), 1)
        with patch.object(runtime.pwd, 'getpwuid', return_value=type('Account', (), {'pw_dir': '/account'})()):
            a = runtime.shared_store('a' * 64)
            with patch.object(runtime, '__file__', '/another/installation/runtime.py'):
                self.assertEqual(runtime.shared_store('a' * 64), a)
                self.assertNotEqual(runtime.shared_store('b' * 64), a)

    def test_exact_typed_terminal_failure_reuses_and_rechecks_source(self):
        expected = {'format': 'aim-flow-resumed-candidate-local-run',
            'status': 'local-validation-failed', 'localFeatureAccepted': False, 'source': 'original'}
        self.command[3] = 'resume-candidate'
        self.entry.write_text('import json\nfrom pathlib import Path\n'
            + 'value=' + repr(expected) + '\n'
            + 'value["source"]=Path(' + repr(str(self.data)) + ').read_text()\n'
            + 'print(json.dumps(value));raise SystemExit(1)\n')
        first = self.invoke(expected_terminal_failure=expected)
        self.assertEqual(first.returncode, 1)
        self.assertEqual(self.invoke(expected_terminal_failure=expected).stdout, first.stdout)
        self.assertEqual(len(self.launches), 1)
        self.data.write_text('changed')
        self.invoke(expected_terminal_failure=expected)
        self.invoke(expected_terminal_failure=expected)
        self.assertEqual(len(self.launches), 3, 'mismatching failure cannot acquire a proof')

    def test_failure_opt_in_does_not_accept_wrong_exit_stderr_or_untyped_result(self):
        from types import SimpleNamespace
        expected = {'format': 'aim-flow-resumed-candidate-local-run',
            'status': 'local-validation-failed', 'localFeatureAccepted': False}
        raw = json.dumps(expected).encode()
        for code, stderr, stdout in [(0, b'', raw), (1, b'warning', raw), (1, b'', b'{}'),
                                     (1, b'', b'{"status":1,"status":2}')]:
            self.assertFalse(runtime.result_matches(SimpleNamespace(returncode=code,stderr=stderr,stdout=stdout), expected))
        with self.assertRaisesRegex(ValueError, 'Only exact'):
            self.invoke(expected_terminal_failure=expected)  # work is not resume-candidate
        self.command[3] = 'resume-candidate'
        with self.assertRaisesRegex(ValueError, 'Only exact'):
            self.invoke(expected_terminal_failure={'status': 'unknown'})

    def test_effectful_command_never_uses_bootstrap_or_cache(self):
        command = [*self.command, '--key-file', '/private/key']
        self.assertFalse(runtime.allowed(command))
        called = []
        runtime.evaluate(command, self.root, self.env, lambda *a, **k: called.append(a[0]), store=self.store)
        self.assertEqual(called, [command]); self.assertFalse(self.store.exists())


if __name__ == '__main__': unittest.main()

class FixedValidationWrapperTests(unittest.TestCase):
    setUp = VerificationRuntimeTests.setUp
    invoke = VerificationRuntimeTests.invoke

    def test_nested_fixed_wrapper_reuses_full_mutation_sensitive_closure(self):
        old=self.root/'old'; (old/'lib/aim_ci').mkdir(parents=True)
        (old/'lib/aim_ci/__init__.py').write_text('')
        (old/'lib/aim_ci/project_validation.py').write_text('verification_process=None\n')
        state=self.root/'retained';state.mkdir(); receipt=state/'result.json';receipt.write_text('receipt-v1')
        config=self.root/'config.json';config.write_text('config-v1')
        module=old/'lib/flow_workflow_validation.py'
        module.write_text('from pathlib import Path\n'
            'def verified_producer(*a):return {"source":Path('+repr(str(self.data))+').read_text()}\n'
            'def verify(config,sha,state):\n verified_producer()\n return {"config":config.read_text(),"receipt":(state/"result.json").read_text()}\n')
        cmd=[self.command[0],'-IBS','-c',runtime.COMPLETED_VALIDATION_WRAPPER,str(old),str(config),'a'*64,str(state)]
        self.entry.write_text('from aim_ci import project_validation as v\nimport os,sys\n'
            'r=v._verification_process('+repr(cmd)+','+repr(str(old))+',dict(os.environ))\n'
            'sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr);raise SystemExit(r.returncode)\n')
        first=self.invoke();self.assertEqual(first.returncode,0,first.stderr)
        self.assertEqual(self.invoke().stdout,first.stdout);self.assertEqual(len(self.launches),1)
        for path in (self.data,receipt,config,module):
            path.write_text(path.read_text()+ ('\n# changed\n' if path==module else '-changed'))
            self.assertEqual(self.invoke().returncode,0)
            self.assertEqual(self.invoke().returncode,0)
        self.assertEqual(len(self.launches),5,'every changed dependency invalidates outer proof once')
    def test_only_exact_wrapper_is_allowed(self):
        cmd=[self.command[0],'-IBS','-c',runtime.COMPLETED_VALIDATION_WRAPPER,str(self.controller),str(self.data),'a'*64,str(self.root)]
        self.assertTrue(runtime.allowed(cmd))
        for bad in ([*cmd,'extra'],[*cmd[:3],cmd[3]+'\n',*cmd[4:]], [*cmd[:6],'wrong-sha',cmd[7]]):
            self.assertFalse(runtime.allowed(bad))
        called=[];bad=[self.command[0],'-IBS','-c','print(1)']
        runtime.evaluate(bad,self.root,self.env,lambda *a,**k:called.append(a[0]),store=self.store)
        self.assertEqual(called,[bad]);self.assertFalse(self.store.exists())
