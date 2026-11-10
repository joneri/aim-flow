'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const runner = require('../lib/aim_ci/project_container_runner.cjs');
const tmp = () => fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'aim-container-helper-test-')));
function job() {
  return { format: 'aim-flow-project-container-job', schemaVersion: 1, intentSha256: 'a'.repeat(64),
    stages: [{ argv: ['npm', 'run', 'check'], root: '.' }], sources: { 'package.json': { sha256: 'b'.repeat(64), mode: '100644', bytes: 20 } }, cache: {},
    runtime: { nodeVersion: '24.19.0', npmVersion: '11.17.0', nodePath: '/usr/local/bin/node', npmPath: '/usr/local/lib/node_modules/npm/bin/npm-cli.js' },
    applicationEnvironment: { PASSENGER_ENV_HTACCESS: '/dev/null' }, timeoutSeconds: 60, outputBytes: 100000 };
}
function fixture(t) {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(root + '/source'); const data = Buffer.from('{"scripts":{"check":"node check.cjs"}}\n');
  fs.writeFileSync(root + '/source/package.json', data, { mode: 0o644 });
  return { root, data, manifest: { 'package.json': { sha256: runner.sha(data), mode: '100644', bytes: data.length } } };
}
test('strict JSON refuses duplicate keys and trailing/nested malformed data', () => {
  assert.deepEqual(runner.strictJSON('{"a":[1,true,null,{"s":"escaped\\\"key"}]}'), { a: [1, true, null, { s: 'escaped"key' }] });
  for (const text of ['{"a":1,"a":2}', '{"x":{"a":1,"a":2}}', '{}{}', '{"a":1,}', '[1,]', '{"x":NaN}']) assert.throws(() => runner.strictJSON(text));
});
test('strict job binds runtime, exact npm stages, source paths and environment', () => {
  assert.equal(runner.validateJob(job()).runtime.nodeVersion, '24.19.0');
  const changes = [j => j.stages[0].argv.push('--ignore-scripts'), j => j.stages[0].argv[0] = 'sh',
    j => j.stages[0].root = '../escape', j => j.sources['../escape'] = j.sources['package.json'],
    j => j.sources['.npmrc'] = j.sources['package.json'], j => j.runtime.nodeVersion = '22.12.0',
    j => j.runtime.npmPath = '/tmp/npm.js', j => j.applicationEnvironment.NODE_OPTIONS = '--require evil',
    j => j.applicationEnvironment.NPM_CONFIG_IGNORE_SCRIPTS = 'true', j => j.applicationEnvironment.PASSENGER_ENV_HTACCESS = '/host/.htaccess',
    j => j.outputBytes = 0, j => j.timeoutSeconds = Infinity, j => j.authorized = true,
    j => j.stages[0].argv = ['npm', 'ci']];
  for (const change of changes) { const j = job(); change(j); assert.throws(() => runner.validateJob(j)); }
});
test('input inventories reject extra files/directories, symlinks, missing files and wrong modes', t => {
  const { root, manifest } = fixture(t); const deadline = Date.now() + 5000;
  assert.equal(runner.inputTree(root + '/source', manifest, true, deadline)['package.json'].mode, 0o644);
  fs.writeFileSync(root + '/source/extra', 'x'); assert.throws(() => runner.inputTree(root + '/source', manifest, true, deadline)); fs.unlinkSync(root + '/source/extra');
  fs.mkdirSync(root + '/source/extra'); assert.throws(() => runner.inputTree(root + '/source', manifest, true, deadline)); fs.rmdirSync(root + '/source/extra');
  fs.renameSync(root + '/source/package.json', root + '/original'); fs.symlinkSync(root + '/original', root + '/source/package.json');
  assert.throws(() => runner.inputTree(root + '/source', manifest, true, deadline)); fs.unlinkSync(root + '/source/package.json');
  assert.throws(() => runner.inputTree(root + '/source', manifest, true, deadline)); fs.renameSync(root + '/original', root + '/source/package.json');
  fs.chmodSync(root + '/source/package.json', 0o755); assert.throws(() => runner.inputTree(root + '/source', manifest, true, deadline));
});
test('copy and restoration checks retain pinned source bytes and reject links or mutations', t => {
  const { root, data, manifest } = fixture(t); const deadline = Date.now() + 5000;
  runner.copyTree(root + '/source', root + '/project', manifest, true, deadline);
  assert.equal(runner.copiedSources(root + '/project', manifest, deadline)['package.json'].sha256, runner.sha(data));
  fs.appendFileSync(root + '/project/package.json', 'mutation'); assert.throws(() => runner.copiedSources(root + '/project', manifest, deadline));
  fs.writeFileSync(root + '/project/package.json', data); fs.linkSync(root + '/project/package.json', root + '/alias');
  assert.throws(() => runner.copiedSources(root + '/project', manifest, deadline));
});
test('output inventory hashes bytes, records links without following and enforces deadline', t => {
  const { root } = fixture(t); fs.symlinkSync('/outside-not-read', root + '/source/link');
  const result = runner.inventory(root + '/source', Date.now() + 5000);
  assert.equal(result.link.type, 'link'); assert.equal(result.link.target, '/outside-not-read'); assert.equal(result['package.json'].type, 'file');
  assert.throws(() => runner.inventory(root + '/source', Date.now() - 1));
});
test('sanitized environment is built without ambient credentials or Node options', () => {
  const saved = process.env.NODE_OPTIONS; process.env.NODE_OPTIONS = '--require fake';
  try {
    const env = runner.environment('/work', { APP_SETTING: 'explicit' });
    assert.equal(env.NODE_OPTIONS, undefined); assert.equal(env.OPENAI_API_KEY, undefined); assert.equal(env.APP_SETTING, 'explicit');
    assert.equal(env.npm_config_offline, 'true'); assert.equal(env.npm_config_ignore_scripts, undefined); assert.equal(env.HOME, '/work/home');
  } finally { if (saved === undefined) delete process.env.NODE_OPTIONS; else process.env.NODE_OPTIONS = saved; }
});
test('capture preserves stdout/stderr and exit, with shared byte ceiling', async t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const env = { PATH: '/usr/bin:/bin' }; const budget = { remaining: 1000 };
  const first = await runner.capture([process.execPath, '-e', 'console.log("out");console.error("err");process.exitCode=7'], root, env, Date.now() + 5000, budget);
  assert.equal(first.exitCode, 7); assert.equal(first.stdout, 'out\n'); assert.equal(first.stderr, 'err\n'); assert.equal(first.termination, null);
  const second = await runner.capture([process.execPath, '-e', 'process.stdout.write("x".repeat(10000))'], root, env, Date.now() + 5000, budget);
  assert.equal(second.termination, 'output-limit'); assert(Buffer.byteLength(first.stdout + first.stderr + second.stdout + second.stderr) <= 1000);
});
test('capture kills descendants even after successful leader exit', async t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true })); const marker = root + '/late';
  const child = `setTimeout(()=>require('node:fs').writeFileSync(${JSON.stringify(marker)},'escaped'),500)`;
  const parent = `require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(child)}],{stdio:'inherit'}).unref()`;
  const result = await runner.capture([process.execPath, '-e', parent], root, { PATH: '/usr/bin:/bin' }, Date.now() + 3000, { remaining: 10000 });
  assert.equal(result.exitCode, 0); await new Promise(r => setTimeout(r, 650)); assert.equal(fs.existsSync(marker), false);
});
test('capture times out and spawn errors produce failed observations', async t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const timeout = await runner.capture([process.execPath, '-e', 'setInterval(()=>{},1000)'], root, {}, Date.now() + 100, { remaining: 1000 });
  assert.equal(timeout.termination, 'timeout');
  const missing = await runner.capture(['/nonexistent/aim-helper'], root, {}, Date.now() + 1000, { remaining: 1000 });
  assert.equal(missing.termination, 'spawn-error'); assert.equal(missing.exitCode, null);
});
test('oversized report cannot claim success and emits one bounded JSON object', () => {
  const encoded = runner.encodeReport({ intentSha256: 'a'.repeat(64), status: 'passed', stages: [{}], unrunStages: [], huge: 'x'.repeat(10000) }, 4096);
  assert(Buffer.byteLength(encoded) <= 4096); const value = runner.strictJSON(encoded);
  assert.equal(value.status, 'failed'); assert.equal(value.failure, 'report-output-limit'); assert.equal(value.priorStageCount, 1);
});

test('capture deadline also bounds pipes held by a new-session descendant', async t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const child = 'setTimeout(()=>{},600)';
  const parent = `require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(child)}],{stdio:'inherit',detached:true}).unref()`;
  const started = Date.now();
  const result = await runner.capture([process.execPath, '-e', parent], root, {}, Date.now() + 150, { remaining: 1000 });
  assert.equal(result.termination, 'timeout'); assert(Date.now() - started < 550);
  // The synthetic escaped child has a bounded self-exit; namespace sweeping is Linux-only.
  await new Promise(resolve => setTimeout(resolve, 700));
});
test('report format/schema and source restoration are explicit, no success on metadata overflow', () => {
  const encoded = runner.encodeReport({ format: 'aim-flow-project-container-report', schemaVersion: 1,
    intentSha256: 'a'.repeat(64), status: 'passed', qualificationPassed: true, sourcesRestored: true,
    stages: [], unrunStages: [], ready: false, acceptance: 'not-established' }, 4096);
  const value = runner.strictJSON(encoded);
  assert.equal(value.format, 'aim-flow-project-container-report'); assert.equal(value.schemaVersion, 1);
  assert.equal(value.sourcesRestored, true); assert.equal(value.ready, false);
});
test('inventory treats prototype-like filenames as ordinary output entries', t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(root + '/__proto__', 'bytes');
  const value = runner.inventory(root, Date.now() + 3000);
  assert(Object.hasOwn(value, '__proto__')); assert.equal(value.__proto__.type, 'file');
});

test('namespace sweep includes detached sessions, skips PID1/zombies and rejects foreign ownership', async t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const [pid, state, uid] of [['1','S','65534'],['22','S','65534'],['23','Z','65534'],['24','S','65534']]) {
    fs.mkdirSync(root + '/' + pid);
    fs.writeFileSync(root + '/' + pid + '/status', `State: ${state}\nUid: ${uid} ${uid} ${uid} ${uid}\n`);
  }
  assert.deepEqual(runner.namespaceProcesses(root).sort(), [22,24]);
  const killed=[];
  await runner.drainProcesses(()=>runner.namespaceProcesses(root), pid=>{killed.push(pid);fs.rmSync(root+'/'+pid,{recursive:true});}, Date.now()+500);
  assert.deepEqual(killed.sort(),[22,24]); assert.deepEqual(runner.namespaceProcesses(root),[]);
  fs.mkdirSync(root+'/25');fs.writeFileSync(root+'/25/status','State: S\nUid: 0 0 0 0\n');
  assert.throws(()=>runner.namespaceProcesses(root),/ownership/);
  await assert.rejects(runner.drainProcesses(()=>[99],()=>{},Date.now()-1),/deadline/);
});

test('v2 fields are explicit and v1 shape/default noexec contract remains accepted', () => {
  const old = job(); const before = JSON.stringify(old); runner.validateJob(old);
  assert.equal(JSON.stringify(old), before);
  assert.deepEqual(runner.executionSettings(old), { workspaceExecution: 'noexec', inventoryBytes: 512 * 1024 * 1024, runtimeTools: {} });
  const modern = { ...job(), schemaVersion: 2, workspaceExecution: 'exec', inventoryBytes: 2 * 1024 ** 3, runtimeFingerprints: null,
    runtimeTools: { git: { path: '/usr/bin/git', sha256: 'c'.repeat(64) } } };
  runner.validateJob(modern); assert.equal(runner.executionSettings(modern).workspaceExecution, 'exec');
  runner.validateJob({ ...modern, workspaceExecution: 'noexec' });
  const invalid = [j => delete j.workspaceExecution, j => j.workspaceExecution = 'auto', j => j.inventoryBytes = 0,
    j => j.inventoryBytes = 2 * 1024 ** 3 + 1, j => j.inventoryBytes = 1.5, j => j.inventoryBytes = Infinity,
    j => j.runtimeTools.git.path = 'usr/bin/git', j => j.runtimeTools.git.path = '/usr/bin/../git',
    j => j.runtimeTools.git.path = '/usr//bin/git', j => j.runtimeTools.git.extra = true,
    j => j.runtimeTools['../git'] = j.runtimeTools.git, j => j.runtimeTools.git.sha256 = 'bad'];
  for (const change of invalid) { const value = structuredClone(modern); change(value); assert.throws(() => runner.validateJob(value)); }
  assert.throws(() => runner.validateJob({ ...old, workspaceExecution: 'exec' }));
});
test('direct execution classification requires exact EACCES or exit zero and nonce', () => {
  const denied = { termination: 'spawn-error', spawnErrorCode: 'EACCES', exitCode: null, stdout: '', stderr: '' };
  const passed = { termination: null, spawnErrorCode: null, exitCode: 0, stdout: 'nonce', stderr: '' };
  assert.equal(runner.classifyExecution('noexec', denied, 'nonce'), 'denied-EACCES');
  assert.equal(runner.classifyExecution('exec', passed, 'nonce'), 'executed');
  for (const wrong of [{ ...denied, spawnErrorCode: 'EPERM' }, { ...denied, spawnErrorCode: 'ENOENT' },
    { ...denied, termination: 'timeout' }, { ...denied, exitCode: 126 }, { ...denied, stdout: 'executed' }, passed]) {
    assert.throws(() => runner.classifyExecution('noexec', wrong, 'nonce'));
  }
  for (const wrong of [denied, { ...passed, stdout: 'wrong' }, { ...passed, exitCode: 1 }, { ...passed, stderr: 'warning' }]) {
    assert.throws(() => runner.classifyExecution('exec', wrong, 'nonce'));
  }
});
test('inventory byte cap is configurable without changing default or traversing links', t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(root + '/a', '123'); fs.writeFileSync(root + '/b', '456'); fs.symlinkSync('/outside-not-read', root + '/link');
  assert.equal(runner.inventory(root, Date.now() + 3000, 6).b.bytes, 3);
  assert.throws(() => runner.inventory(root, Date.now() + 3000, 5), /Inventory byte bound/);
  assert.throws(() => runner.inventory(root, Date.now() + 3000, 2 * 1024 ** 3 + 1));
  assert.equal(runner.inventory(root, Date.now() + 3000).link.type, 'link');
});
test('runtime tool pins check canonical first PATH resolution, hash and executable mode', t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(root + '/first'); fs.mkdirSync(root + '/second');
  const actual = root + '/second/which.debianutils'; fs.writeFileSync(actual, 'reviewed executable bytes', { mode: 0o755 });
  fs.symlinkSync('which.debianutils', root + '/second/which');
  const tool = { which: { path: actual, sha256: runner.sha(fs.readFileSync(actual)) } };
  const search = root + '/first:' + root + '/second';
  assert.deepEqual(JSON.parse(JSON.stringify(runner.runtimeToolPins(tool, Date.now() + 3000, search))), tool);
  fs.writeFileSync(root + '/first/which', 'shadow', { mode: 0o755 });
  assert.throws(() => runner.runtimeToolPins(tool, Date.now() + 3000, search), /resolution/); fs.unlinkSync(root + '/first/which');
  fs.appendFileSync(actual, 'changed'); assert.throws(() => runner.runtimeToolPins(tool, Date.now() + 3000, search), /hash drift/);
  tool.which.sha256 = runner.sha(fs.readFileSync(actual)); fs.chmodSync(actual, 0o644);
  assert.throws(() => runner.runtimeToolPins(tool, Date.now() + 3000, search), /resolution/);
  fs.chmodSync(actual, 0o755); tool.which.path = root + '/second/which';
  assert.throws(() => runner.runtimeToolPins(tool, Date.now() + 3000, search), /canonical/);
});

test('v2 fingerprint schema is exact, version bound and optional only as null', () => {
  const modern = { ...job(), schemaVersion: 2, workspaceExecution: 'exec', inventoryBytes: 1024, runtimeTools: {},
    runtimeFingerprints: { nodeVersion: '24.19.0', npmVersion: '11.17.0', nodeSha256: 'a'.repeat(64), npmCliSha256: 'b'.repeat(64), npmTreeSha256: 'c'.repeat(64) } };
  runner.validateJob(modern); runner.validateJob({ ...modern, runtimeFingerprints: null });
  for (const change of [j => delete j.runtimeFingerprints, j => delete j.runtimeFingerprints.npmTreeSha256,
    j => j.runtimeFingerprints.extra = true, j => j.runtimeFingerprints.nodeSha256 = 'bad',
    j => j.runtimeFingerprints.nodeVersion = '24.0.0', j => j.runtimeFingerprints.npmVersion = '10.0.0']) {
    const value = structuredClone(modern); change(value); assert.throws(() => runner.validateJob(value));
  }
});
test('npm fingerprint uses exact sorted canonical inventory and never follows symlinks', t => {
  const root = tmp(); t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.chmodSync(root, 0o755); fs.mkdirSync(root+'/bin', {mode:0o755});fs.chmodSync(root+'/bin',0o755);
  fs.writeFileSync(root+'/bin/npm-cli.js','fixture',{mode:0o644});fs.chmodSync(root+'/bin/npm-cli.js',0o644);
  fs.symlinkSync('/not-followed',root+'/link');
  const entries=[{path:'.',mode:0o755,type:'directory'},{path:'bin',mode:0o755,type:'directory'},
    {path:'bin/npm-cli.js',mode:0o644,type:'file',bytes:7,sha256:runner.sha('fixture')},
    {path:'link',mode:fs.lstatSync(root+'/link').mode & 0o7777,type:'symlink',target:'/not-followed'}];
  const ordered=entries.map(e=>Object.fromEntries(Object.keys(e).sort().map(k=>[k,e[k]])));
  const expected=runner.sha(Buffer.from(JSON.stringify(ordered),'utf8'));
  assert.equal(runner.npmTreeFingerprint(root,Date.now()+3000),expected);
  fs.appendFileSync(root+'/bin/npm-cli.js','changed');assert.notEqual(runner.npmTreeFingerprint(root,Date.now()+3000),expected);
  assert.throws(()=>runner.npmTreeFingerprint(root,Date.now()-1));
});
test('v2 info logs do not alter lifecycle flags and v1 logging stays unchanged', () => {
  const old=runner.environment('/work',{},1), modern=runner.environment('/work',{},2);
  assert.equal(old.npm_config_loglevel,undefined);assert.equal(modern.npm_config_loglevel,'info');
  for(const key of ['npm_config_ignore_scripts','npm_config_foreground_scripts','npm_config_allow_scripts']) assert.equal(modern[key],undefined);
  delete modern.npm_config_loglevel;assert.deepEqual(modern,old);
});
