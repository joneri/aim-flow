'use strict';
// Fixed image entrypoint. Never evaluates caller-supplied JavaScript or imports project code.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const net = require('node:net');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const BASE = '/opt/aim-project';
const WORK = '/work';
const MAX_FILES = 30000;
const MAX_BYTES = 512 * 1024 * 1024;
const MAX_INVENTORY_BYTES = 2 * 1024 * 1024 * 1024;
const TOOL_PATH = '/usr/local/bin:/usr/bin:/bin';
const pin = x => typeof x === 'string' && /^[a-f0-9]{64}$/.test(x);
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const sha = x => crypto.createHash('sha256').update(x).digest('hex');
function keys(x, expected) {
  assert(object(x) && JSON.stringify(Object.keys(x).sort()) === JSON.stringify([...expected].sort()), 'Unexpected object fields');
}
function relative(x, dot = false) {
  assert(typeof x === 'string' && x.length <= 512 && (dot && x === '.' ||
    x.length && !x.includes('\\') && !/[\x00-\x1f\x7f]/.test(x) && !x.startsWith('/') &&
    x.split('/').every(p => p && !['.', '..', '.git'].includes(p))), 'Unsafe relative path');
  return x;
}
function strictJSON(text) {
  // Validate duplicate keys before JSON.parse; JSON.parse alone silently overwrites them.
  let pos = 0;
  const white = () => { while (/\s/.test(text[pos] || '') && pos < text.length) pos++; };
  function string() {
    const start = pos++;
    while (pos < text.length) {
      if (text[pos] === '\\') { pos += 2; continue; }
      if (text[pos++] === '"') return JSON.parse(text.slice(start, pos));
    }
    throw Error('Unterminated JSON string');
  }
  function value(depth = 0) {
    assert(depth < 64, 'JSON nesting bound'); white();
    if (text[pos] === '{') {
      pos++; white(); const seen = new Set();
      if (text[pos] !== '}') while (true) {
        white(); assert(text[pos] === '"', 'JSON object key'); const key = string();
        assert(!seen.has(key), 'Duplicate JSON key'); seen.add(key); white(); assert(text[pos++] === ':');
        value(depth + 1); white(); if (text[pos] !== ',') break; pos++;
      }
      assert(text[pos++] === '}');
    } else if (text[pos] === '[') {
      pos++; white(); if (text[pos] !== ']') while (true) {
        value(depth + 1); white(); if (text[pos] !== ',') break; pos++;
      }
      assert(text[pos++] === ']');
    } else if (text[pos] === '"') string();
    else { const m = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(pos)); assert(m); pos += m[0].length; }
  }
  value(); white(); assert(pos === text.length, 'Trailing JSON'); return JSON.parse(text);
}
function validateJob(j) {
  assert(object(j) && [1, 2].includes(j.schemaVersion), 'Unsupported job schema');
  keys(j, ['format', 'schemaVersion', 'intentSha256', 'stages', 'sources', 'cache', 'runtime', 'applicationEnvironment', 'timeoutSeconds', 'outputBytes',
    ...(j.schemaVersion === 2 ? ['workspaceExecution', 'inventoryBytes', 'runtimeTools', 'runtimeFingerprints'] : [])]);
  assert(j.format === 'aim-flow-project-container-job' && pin(j.intentSha256));
  if (j.schemaVersion === 2) {
    assert(['noexec', 'exec'].includes(j.workspaceExecution), 'Invalid workspace execution mode');
    if (j.runtimeFingerprints !== null) {
      keys(j.runtimeFingerprints, ['nodeVersion', 'npmVersion', 'nodeSha256', 'npmCliSha256', 'npmTreeSha256']);
      assert(typeof j.runtimeFingerprints.nodeVersion === 'string' && typeof j.runtimeFingerprints.npmVersion === 'string');
      for (const key of ['nodeSha256', 'npmCliSha256', 'npmTreeSha256']) assert(pin(j.runtimeFingerprints[key]), 'Invalid runtime fingerprint');
    }
    assert(Number.isSafeInteger(j.inventoryBytes) && j.inventoryBytes > 0 && j.inventoryBytes <= MAX_INVENTORY_BYTES, 'Invalid inventory bound');
    assert(object(j.runtimeTools) && Object.keys(j.runtimeTools).length <= 64, 'Invalid runtime tools');
    for (const [name, tool] of Object.entries(j.runtimeTools)) {
      assert(/^[a-z][a-z0-9._-]{0,63}$/.test(name), 'Invalid runtime tool name');
      keys(tool, ['path', 'sha256']); assert(pin(tool.sha256));
      assert(typeof tool.path === 'string' && tool.path.startsWith('/') && tool.path.length <= 512, 'Absolute runtime tool path required');
      relative(tool.path.slice(1));
    }
  }
  assert(Number.isInteger(j.timeoutSeconds) && j.timeoutSeconds > 0 && j.timeoutSeconds <= 3600);
  assert(Number.isInteger(j.outputBytes) && j.outputBytes >= 4096 && j.outputBytes <= 32 * 1024 * 1024);
  keys(j.runtime, ['nodeVersion', 'npmVersion', 'nodePath', 'npmPath']);
  assert(j.runtime.nodePath === '/usr/local/bin/node' && j.runtime.npmPath === '/usr/local/lib/node_modules/npm/bin/npm-cli.js');
  assert(/^v?24\.\d+\.\d+$/.test(j.runtime.nodeVersion) && /^\d+\.\d+\.\d+$/.test(j.runtime.npmVersion), 'Node24 runtime required');
  if (j.schemaVersion === 2 && j.runtimeFingerprints !== null) {
    assert(j.runtimeFingerprints.nodeVersion === j.runtime.nodeVersion.replace(/^v/, '') && j.runtimeFingerprints.npmVersion === j.runtime.npmVersion, 'Fingerprint runtime versions differ');
  }
  for (const [kind, manifest] of [['sources', j.sources], ['cache', j.cache]]) {
    assert(object(manifest) && Object.keys(manifest).length <= MAX_FILES && (kind !== 'sources' || Object.keys(manifest).length));
    for (const [name, row] of Object.entries(manifest)) {
      relative(name); assert(!name.split('/').includes('.npmrc'), 'Project npm config is not supported');
      keys(row, kind === 'sources' ? ['sha256', 'mode', 'bytes'] : ['sha256', 'mode']); assert(pin(row.sha256));
      if (kind === 'sources') assert(['100644', '100755'].includes(row.mode) && Number.isSafeInteger(row.bytes) && row.bytes >= 0 && row.bytes <= MAX_BYTES);
      else assert(Number.isInteger(row.mode) && [0o600, 0o644, 0o755].includes(row.mode));
    }
  }
  assert(Array.isArray(j.stages) && j.stages.length && j.stages.length <= 16);
  for (const s of j.stages) {
    keys(s, ['argv', 'root']); relative(s.root, true);
    assert(Array.isArray(s.argv) && s.argv.every(x => typeof x === 'string' && x.length < 1024 && !x.includes('\0')));
    assert(s.argv[0] === 'npm' && (s.argv.length === 2 && s.argv[1] === 'ci' ||
      s.argv.length === 3 && s.argv[1] === 'run' && /^[A-Za-z0-9][A-Za-z0-9:._-]*$/.test(s.argv[2])), 'Only exact declared npm ci/run stages');
    const prefix = s.root === '.' ? '' : s.root + '/';
    assert(Object.hasOwn(j.sources, prefix + 'package.json'), 'Stage manifest missing');
    if (s.argv[1] === 'ci') assert(Object.hasOwn(j.sources, prefix + 'package-lock.json') || Object.hasOwn(j.sources, prefix + 'npm-shrinkwrap.json'), 'Stage lock missing');
  }
  assert(object(j.applicationEnvironment));
  for (const [name, value] of Object.entries(j.applicationEnvironment)) {
    assert(/^[A-Z][A-Z0-9_]*$/.test(name) && typeof value === 'string' && value.length <= 4096 && !value.includes('\0'));
    assert(!/^(?:NODE_|NPM_|GIT_|DYLD_|LD_|PYTHON|FLOW_)/.test(name) && !['PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'SHELL', 'ENV', 'BASH_ENV', 'ZDOTDIR', 'XDG_CONFIG_HOME', 'LANG', 'LC_ALL'].includes(name), 'Runtime environment override');
    if (name === 'PASSENGER_ENV_HTACCESS') assert(value === '/dev/null');
  }
  return j;
}
function executionSettings(j) {
  return j.schemaVersion === 2 ? { workspaceExecution: j.workspaceExecution, inventoryBytes: j.inventoryBytes, runtimeTools: j.runtimeTools } :
    { workspaceExecution: 'noexec', inventoryBytes: MAX_BYTES, runtimeTools: {} };
}
function bounds(deadline, byteLimit = MAX_BYTES) {
  assert(Number.isSafeInteger(byteLimit) && byteLimit > 0 && byteLimit <= MAX_INVENTORY_BYTES, 'Invalid byte budget');
  return { files: 0, bytes: 0, byteLimit, check() { assert(Date.now() < deadline, 'Whole helper deadline'); } };
}
function measure(filename, budget, singleLink = true) {
  budget.check(); const before = fs.lstatSync(filename);
  assert(before.isFile() && (!singleLink || before.nlink === 1), 'Expected regular file');
  assert(before.size <= budget.byteLimit - budget.bytes, 'Inventory byte bound');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  const hash = crypto.createHash('sha256'); let bytes = 0;
  try {
    const opened = fs.fstatSync(fd); assert(opened.isFile() && opened.ino === before.ino && opened.dev === before.dev);
    const buf = Buffer.alloc(65536); let size;
    while ((size = fs.readSync(fd, buf, 0, buf.length, null))) { budget.check(); bytes += size; assert(bytes <= before.size); hash.update(buf.subarray(0, size)); }
    const after = fs.fstatSync(fd);
    assert(bytes === before.size && after.size === before.size && after.mtimeMs === before.mtimeMs && after.ctimeMs === before.ctimeMs, 'File changed during measurement');
  } finally { fs.closeSync(fd); }
  budget.bytes += bytes;
  return { sha256: hash.digest('hex'), mode: before.mode & 0o7777, bytes };
}
function inputTree(root, manifest, source, deadline) {
  assert(fs.lstatSync(root).isDirectory() && fs.realpathSync(root) === root, 'Unsafe input root');
  const expectedDirs = new Set(['']);
  for (const name of Object.keys(manifest)) { let p = path.posix.dirname(name); while (p !== '.') { expectedDirs.add(p); p = path.posix.dirname(p); } }
  const measured = Object.create(null); const budget = bounds(deadline);
  function walk(dir, prefix) {
    budget.check();
    for (const name of fs.readdirSync(dir).sort()) {
      assert(++budget.files <= MAX_FILES, 'Input entry bound');
      const rel = prefix ? prefix + '/' + name : name; const file = path.join(dir, name); const info = fs.lstatSync(file);
      if (info.isDirectory()) { assert(expectedDirs.has(rel), 'Extra input directory'); walk(file, rel); }
      else {
        assert(Object.hasOwn(manifest, rel), 'Extra input file');
        const row = measure(file, budget); const expected = manifest[rel];
        assert(row.sha256 === expected.sha256 && row.mode === (source ? parseInt(expected.mode, 8) & 0o777 : expected.mode), 'Input hash or mode mismatch');
        if (source) assert(row.bytes === expected.bytes, 'Input size mismatch');
        measured[rel] = row;
      }
    }
  }
  walk(root, ''); assert(Object.keys(measured).length === Object.keys(manifest).length, 'Missing input file'); return measured;
}
function copiedSources(root, manifest, deadline) {
  const result = Object.create(null); const budget = bounds(deadline);
  for (const name of Object.keys(manifest).sort()) {
    const full = path.join(root, name); assert(fs.realpathSync(full) === full, 'Linked copied source');
    const row = measure(full, budget); const e = manifest[name];
    assert(row.sha256 === e.sha256 && row.bytes === e.bytes && row.mode === (parseInt(e.mode, 8) & 0o777), 'Copied source not restored'); result[name] = row;
  }
  return result;
}
function copyTree(from, to, manifest, source, deadline) {
  fs.mkdirSync(to, { recursive: true });
  for (const [name, expected] of Object.entries(manifest)) {
    assert(Date.now() < deadline); const src = path.join(from, name), dst = path.join(to, name);
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst, fs.constants.COPYFILE_EXCL);
    fs.chmodSync(dst, source ? parseInt(expected.mode, 8) & 0o777 : expected.mode);
  }
  inputTree(to, manifest, source, deadline);
}
function inventory(root, deadline, byteLimit = MAX_BYTES) {
  const result = Object.create(null); const budget = bounds(deadline, byteLimit);
  function visit(full, rel) {
    budget.check(); assert(++budget.files <= MAX_FILES, 'Output entry bound');
    const info = fs.lstatSync(full); const mode = info.mode & 0o7777;
    if (info.isSymbolicLink()) result[rel] = { type: 'link', target: fs.readlinkSync(full), mode };
    else if (info.isDirectory()) {
      result[rel] = { type: 'directory', mode };
      for (const name of fs.readdirSync(full).sort()) visit(path.join(full, name), rel ? rel + '/' + name : name);
    } else if (info.isFile()) result[rel] = { type: 'file', ...measure(full, budget, false) };
    else throw Error('Special output entry');
  }
  assert(fs.realpathSync(root) === root && fs.lstatSync(root).isDirectory()); visit(root, ''); return result;
}
function environment(work, application, schemaVersion = 1) {
  return { PATH: TOOL_PATH, HOME: work + '/home', XDG_CONFIG_HOME: work + '/home/.config', TMPDIR: work + '/tmp', LANG: 'C', LC_ALL: 'C',
    NODE_DISABLE_COMPILE_CACHE: '1', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: work + '/git.config', GIT_TEMPLATE_DIR: work + '/empty-template',
    GIT_CONFIG_COUNT: '2', GIT_CONFIG_KEY_0: 'core.hooksPath', GIT_CONFIG_VALUE_0: work + '/empty-hooks', GIT_CONFIG_KEY_1: 'commit.gpgsign', GIT_CONFIG_VALUE_1: 'false', GIT_TERMINAL_PROMPT: '0',
    npm_config_userconfig: work + '/user.npmrc', npm_config_globalconfig: work + '/global.npmrc', npm_config_cache: work + '/cache',
    npm_config_offline: 'true', npm_config_audit: 'false', npm_config_fund: 'false', npm_config_update_notifier: 'false', ...application, ...(schemaVersion === 2 ? { npm_config_loglevel: 'info' } : {}) };
}
function capture(argv, cwd, env, deadline, budget) {
  return new Promise(resolve => {
    const started = Date.now(); const chunks = { stdout: [], stderr: [] }; let reason = null, child, closed = false, groupKilled = false;
    const kill = () => { if (child?.pid && !groupKilled) { groupKilled = true; try { process.kill(-child.pid, 'SIGKILL'); } catch (e) { if (e.code !== 'ESRCH') throw e; } } };
    const finish = (code, signal, error) => {
      if (closed) return; closed = true; clearTimeout(timer); kill();
      resolve({ argv, exitCode: code, signal, termination: reason || (error ? 'spawn-error' : null), error: error ? String(error.message) : null, spawnErrorCode: error?.code || null,
        durationSeconds: (Date.now() - started) / 1000, stdout: Buffer.concat(chunks.stdout).toString('utf8'), stderr: Buffer.concat(chunks.stderr).toString('utf8') });
    };
    const timer = setTimeout(() => {
      reason = 'timeout'; kill();
      // A session-escaping descendant may retain pipes. Never wait indefinitely for close.
      child?.stdout?.destroy(); child?.stderr?.destroy();
      finish(child?.exitCode ?? null, child?.signalCode ?? null);
    }, Math.max(1, deadline - Date.now()));
    try {
      child = spawn(argv[0], argv.slice(1), { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
      child.on('error', error => finish(null, null, error));
      child.on('exit', () => kill()); // Kill process-group descendants even when the leader exits successfully.
      child.on('close', (code, signal) => finish(code, signal));
      for (const name of ['stdout', 'stderr']) child[name].on('data', chunk => {
        const retained = chunk.subarray(0, Math.max(0, budget.remaining)); budget.remaining -= chunk.length;
        chunks[name].push(retained);
        if (budget.remaining < 0) { reason = 'output-limit'; kill(); }
      });
    } catch (error) { finish(null, null, error); }
  });
}
function namespaceProcesses(procRoot = '/proc') {
  return fs.readdirSync(procRoot).filter(name => /^[0-9]+$/.test(name) && name !== '1').flatMap(name => {
    try {
      const status = fs.readFileSync(procRoot + '/' + name + '/status', 'utf8');
      assert(/^Uid:\s+65534\s+65534\s+65534\s+65534$/m.test(status), 'Unexpected process ownership');
      return /^State:\s+Z/m.test(status) ? [] : [Number(name)];
    } catch (e) { if (e.code === 'ENOENT' || e.code === 'ESRCH') return []; throw e; }
  });
}
async function drainProcesses(remaining, kill, deadline) {
  for (;;) {
    const alive = remaining(); if (!alive.length) return;
    for (const pid of alive) { try { kill(pid); } catch (e) { if (e.code !== 'ESRCH') throw e; } }
    assert(Date.now() < deadline, 'Descendant cleanup deadline');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
async function cleanNamespace(deadline) {
  assert(process.platform === 'linux' && process.pid === 1 && process.getuid() === 65534, 'Cleanup requires private nonroot PID1');
  // Docker inspection must also reject host/shared PID mode. Sweep session-escaping
  // descendants before measuring source or outputs; zombies cannot modify bytes.
  await drainProcesses(namespaceProcesses, pid => process.kill(pid, 'SIGKILL'), deadline);
}
function npmTreeFingerprint(root, deadline) {
  assert(fs.realpathSync(root) === root && fs.lstatSync(root).isDirectory(), 'Canonical npm tree required');
  const entries = []; const budget = bounds(deadline);
  function visit(filename, name) {
    budget.check(); assert(++budget.files <= MAX_FILES, 'npm tree entry bound');
    const stat = fs.lstatSync(filename); const mode = stat.mode & 0o7777;
    if (stat.isSymbolicLink()) entries.push({ path: name, mode, type: 'symlink', target: fs.readlinkSync(filename) });
    else if (stat.isDirectory()) {
      entries.push({ path: name, mode, type: 'directory' });
      for (const child of fs.readdirSync(filename)) visit(path.join(filename, child), name === '.' ? child : name + '/' + child);
    } else if (stat.isFile()) {
      const measured = measure(filename, budget, false);
      assert(measured.mode === mode, 'npm file mode changed');
      entries.push({ path: name, mode, type: 'file', bytes: measured.bytes, sha256: measured.sha256 });
    } else throw Error('Unsupported npm tree entry');
  }
  visit(root, '.');
  entries.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  const ordered = entries.map(entry => Object.fromEntries(Object.keys(entry).sort().map(key => [key, entry[key]])));
  return sha(Buffer.from(JSON.stringify(ordered), 'utf8'));
}
function runtimeFingerprints(runtime, expected, deadline) {
  if (expected === null || expected === undefined) return null;
  const npmRoot = path.dirname(path.dirname(runtime.npmPath));
  const packageFile = npmRoot + '/package.json';
  const packageInfo = measure(packageFile, bounds(deadline), false);
  assert(packageInfo.bytes <= 2 * 1024 * 1024, 'npm metadata bound');
  const packageBytes = fs.readFileSync(packageFile, { flag: fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW });
  assert(packageBytes.length === packageInfo.bytes && sha(packageBytes) === packageInfo.sha256, 'npm metadata changed');
  const actual = { nodeVersion: process.version.replace(/^v/, ''), npmVersion: strictJSON(packageBytes.toString('utf8')).version,
    nodeSha256: measure(runtime.nodePath, bounds(deadline), false).sha256,
    npmCliSha256: measure(runtime.npmPath, bounds(deadline), false).sha256,
    npmTreeSha256: npmTreeFingerprint(npmRoot, deadline) };
  for (const key of Object.keys(actual)) assert(actual[key] === expected[key], 'Runtime fingerprint mismatch: ' + key);
  return actual;
}
function runtimeToolPins(tools, deadline, searchPath = TOOL_PATH) {
  const result = Object.create(null);
  for (const [name, tool] of Object.entries(tools)) {
    assert(fs.realpathSync(tool.path) === tool.path && fs.lstatSync(tool.path).isFile(), 'Runtime tool must be canonical regular image file');
    let resolved;
    for (const directory of searchPath.split(':')) {
      const candidate = path.join(directory, name);
      try { fs.accessSync(candidate, fs.constants.X_OK); }
      catch (e) { if (['ENOENT', 'ENOTDIR', 'EACCES'].includes(e.code)) continue; throw e; }
      assert(fs.statSync(candidate).isFile(), 'PATH executable is not regular');
      resolved = fs.realpathSync(candidate); break;
    }
    assert(resolved === tool.path, 'Runtime tool PATH resolution mismatch');
    const measured = measure(tool.path, bounds(deadline), false);
    assert(measured.sha256 === tool.sha256, 'Runtime tool hash drift');
    result[name] = { path: resolved, sha256: measured.sha256 };
  }
  return result;
}
function classifyExecution(mode, result, expectedStdout) {
  assert(['noexec', 'exec'].includes(mode));
  if (mode === 'noexec') {
    assert(result.termination === 'spawn-error' && result.spawnErrorCode === 'EACCES' && result.exitCode === null && !result.stdout && !result.stderr,
      'Workspace noexec denial not established');
    return 'denied-EACCES';
  }
  assert(result.exitCode === 0 && result.termination === null && !result.spawnErrorCode && result.stdout === expectedStdout && result.stderr === '',
    'Workspace direct execution failed');
  return 'executed';
}
async function workspaceExecutionProbe(work, runtime, mode, env, deadline) {
  const directory = fs.mkdtempSync(work + '/tmp/execution-probe-');
  try {
    const original = fs.realpathSync('/bin/true');
    const originalPin = measure(original, bounds(deadline), false).sha256;
    const binary = directory + '/copied-true';
    fs.copyFileSync(original, binary, fs.constants.COPYFILE_EXCL); fs.chmodSync(binary, 0o755);
    const fd = fs.openSync(binary, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const header = Buffer.alloc(4);
    try { assert(fs.readSync(fd, header, 0, 4, 0) === 4 && header.equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])), 'ELF probe requires copied ELF binary'); }
    finally { fs.closeSync(fd); }
    assert(measure(binary, bounds(deadline)).sha256 === originalPin, 'Copied ELF differs');
    const nonce = crypto.randomBytes(16).toString('hex'); const stub = directory + '/node-stub';
    fs.writeFileSync(stub, '#!' + runtime.nodePath + '\nprocess.stdout.write(' + JSON.stringify(nonce) + ');\n', { flag: 'wx', mode: 0o755 });
    const budget = { remaining: 8192 };
    const elf = await capture([binary], directory, env, deadline, budget);
    const executableStub = await capture([stub], directory, env, deadline, budget);
    return { mode, elf: classifyExecution(mode, elf, ''), stub: classifyExecution(mode, executableStub, nonce),
      elfSource: original, elfSha256: originalPin, directExecution: true };
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
}
async function qualifyOne(base, work, job, deadline = Date.now() + 15000) {
  assert(process.platform === 'linux' && process.getuid() === 65534 && process.getgid() === 65534, 'Nonroot Linux UID/GID required');
  const status = fs.readFileSync('/proc/self/status', 'utf8');
  for (const name of ['CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb']) assert(new RegExp('^' + name + ':\\s+0+$', 'm').test(status), 'Capabilities must be empty');
  assert(/^NoNewPrivs:\s+1$/m.test(status), 'no_new_privs required');
  assert.deepEqual(fs.readdirSync('/sys/class/net').sort(), ['lo'], 'Only private loopback interface allowed');
  for (const rows of [fs.readFileSync('/proc/net/route', 'utf8').trim().split('\n').slice(1), fs.readFileSync('/proc/net/ipv6_route', 'utf8').trim().split('\n')]) {
    for (const row of rows.filter(Boolean)) assert(row.trim().split(/\s+/)[0] === 'lo' || row.trim().split(/\s+/).at(-1) === 'lo', 'Nonloopback route');
  }
  assert(Object.entries(os.networkInterfaces()).every(([name, rows]) => name === 'lo' && rows.every(x => x.internal)), 'Nonloopback address');
  const denied = {};
  for (const [name, file, flags] of [['baseline', base + '/job.json', 'a'], ['root', '/.aim-project-write-probe', 'wx']]) {
    let fd; try { fd = fs.openSync(file, flags); throw Error('Unexpected writable ' + name); }
    catch (e) { assert(['EROFS', 'EACCES', 'EPERM'].includes(e.code), 'Readonly probe failed: ' + name); denied[name] = e.code; }
    finally { if (fd !== undefined) fs.closeSync(fd); }
  }
  const temp = fs.mkdtempSync(work + '/tmp/probe-'); fs.writeFileSync(temp + '/writable', 'probe'); fs.unlinkSync(temp + '/writable'); fs.rmdirSync(temp);
  const nonce = crypto.randomBytes(16).toString('hex'); const server = http.createServer((req, res) => res.end(nonce));
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  try {
    const address = server.address(); assert(address.address === '127.0.0.1' && address.family === 'IPv4' && address.port > 0);
    const response = await fetch('http://127.0.0.1:' + address.port, { signal: AbortSignal.timeout(2000) });
    assert(response.status === 200 && await response.text() === nonce, 'Loopback HTTP failed');
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  const external = await new Promise((resolve, reject) => {
    const socket = net.connect({ host: '192.0.2.1', port: 9 });
    socket.once('connect', () => { socket.destroy(); reject(Error('External connection allowed')); });
    socket.once('error', e => { socket.destroy(); if (e.code === 'ENETUNREACH') resolve(e.code); else reject(Error('External denial not established: ' + e.code)); });
    socket.setTimeout(2000, () => { socket.destroy(); reject(Error('External denial timed out')); });
  });
  const settings = executionSettings(job);
  const execution = await workspaceExecutionProbe(work, job.runtime, settings.workspaceExecution, environment(work, job.applicationEnvironment, job.schemaVersion), deadline);
  const tools = runtimeToolPins(settings.runtimeTools, deadline);
  return { workspaceExecution: execution, runtimeTools: tools, uid: process.getuid(), gid: process.getgid(), networkNamespace: fs.readlinkSync('/proc/self/ns/net'), interfaces: ['lo'], loopbackHttp: 'passed', external, readonly: denied, workWritable: true, capabilities: 'none', noNewPrivileges: true };
}
function encodeReport(report, maximum) {
  let data = JSON.stringify(report) + '\n';
  if (Buffer.byteLength(data) > maximum) data = JSON.stringify({ format: 'aim-flow-project-container-report', schemaVersion: 1, intentSha256: report.intentSha256,
    status: 'failed', failure: 'report-output-limit', qualificationPassed: false, sourcesRestored: false, stages: [], unrunStages: report.unrunStages,
    priorStageCount: report.stages?.length || 0, ready: false, acceptance: 'not-established' }) + '\n';
  assert(Buffer.byteLength(data) <= maximum); return data;
}
async function run(base = BASE, work = WORK) {
  let job, deadline = Date.now() + 30000;
  const report = { format: 'aim-flow-project-container-report', schemaVersion: 1, status: 'failed', qualificationPassed: false, sourcesRestored: false, stages: [], ready: false, acceptance: 'not-established', deployment: 'not-authorized' };
  try {
    assert(fs.statSync(base + '/job.json').size <= 2 * 1024 * 1024);
    const frozenJob = measure(base + '/job.json', bounds(deadline)).sha256;
    const frozenHelper = measure(base + '/runner.cjs', bounds(deadline)).sha256;
    const baselineControls = () => { assert(measure(base + '/job.json', bounds(deadline)).sha256 === frozenJob && measure(base + '/runner.cjs', bounds(deadline)).sha256 === frozenHelper, 'Baseline controls changed'); };
    job = validateJob(strictJSON(fs.readFileSync(base + '/job.json', 'utf8'))); report.intentSha256 = job.intentSha256;
    const settings = executionSettings(job);
    report.workspaceExecution = settings.workspaceExecution; report.inventoryBytes = settings.inventoryBytes;
    report.unrunStages = job.stages.map((_, i) => i); deadline = Date.now() + job.timeoutSeconds * 1000;
    assert(process.platform === 'linux' && process.pid === 1 && process.getuid() === 65534 && process.getgid() === 65534, 'Private Linux nonroot PID1 required');
    assert(process.execArgv.includes('--disable-sigusr1'), 'Helper inspector signal must be disabled');
    assert(process.execPath === job.runtime.nodePath && process.version.replace(/^v/, '') === job.runtime.nodeVersion.replace(/^v/, ''), 'Runtime Node identity mismatch');
    assert(fs.realpathSync(base) === base && fs.realpathSync(work) === work && fs.readdirSync(work).length === 0, 'Fresh canonical work required');
    report.sourcesBefore = inputTree(base + '/source', job.sources, true, deadline);
    inputTree(base + '/npm-cache', job.cache, false, deadline);
    for (const name of ['home', 'tmp', 'cache', 'empty-template', 'empty-hooks']) fs.mkdirSync(work + '/' + name);
    for (const name of ['git.config', 'user.npmrc', 'global.npmrc']) fs.writeFileSync(work + '/' + name, '', { flag: 'wx', mode: 0o444 });
    const env = environment(work, job.applicationEnvironment, job.schemaVersion); const budget = { remaining: Math.floor(job.outputBytes / 4) };
    const qDeadline = Math.min(deadline, Date.now() + 15000);
    const parent = await qualifyOne(base, work, job, qDeadline);
    const child = await capture([job.runtime.nodePath, base + '/runner.cjs', '--qualification-child'], work, env, qDeadline, budget);
    assert(child.exitCode === 0 && child.termination === null, 'Child containment qualification failed');
    const childResult = strictJSON(child.stdout); assert(childResult.networkNamespace === parent.networkNamespace);
    assert(childResult.workspaceExecution?.mode === settings.workspaceExecution && childResult.workspaceExecution?.elf === parent.workspaceExecution.elf &&
      childResult.workspaceExecution?.stub === parent.workspaceExecution.stub, 'Child workspace execution qualification differs');
    assert(JSON.stringify(childResult.runtimeTools) === JSON.stringify(parent.runtimeTools), 'Child tool qualification differs');
    report.qualification = { parent, child: childResult }; report.qualificationPassed = true;
    const npmVersion = strictJSON(fs.readFileSync(path.dirname(path.dirname(job.runtime.npmPath)) + '/package.json', 'utf8')).version;
    assert(npmVersion === job.runtime.npmVersion, 'Runtime npm identity mismatch');
    report.runtime = { ...job.runtime, runtimeFingerprints: runtimeFingerprints(job.runtime, job.schemaVersion === 2 ? job.runtimeFingerprints : null, deadline), runtimeTools: runtimeToolPins(settings.runtimeTools, deadline), actualNodeVersion: process.version, actualNpmVersion: npmVersion,
      nodeSha256: measure(job.runtime.nodePath, bounds(deadline), false).sha256,
      npmSha256: measure(job.runtime.npmPath, bounds(deadline), false).sha256, helperSha256: measure(base + '/runner.cjs', bounds(deadline)).sha256 };
    copyTree(base + '/source', work + '/project', job.sources, true, deadline);
    copyTree(base + '/npm-cache', work + '/cache', job.cache, false, deadline);
    for (const [index, stage] of job.stages.entries()) {
      copiedSources(work + '/project', job.sources, deadline);
      runtimeToolPins(settings.runtimeTools, deadline);
      runtimeFingerprints(job.runtime, job.schemaVersion === 2 ? job.runtimeFingerprints : null, deadline);
      const cwd = path.join(work, 'project', stage.root); assert(fs.realpathSync(cwd) === cwd && fs.statSync(cwd).isDirectory());
      const manifest = strictJSON(fs.readFileSync(cwd + '/package.json', 'utf8'));
      if (stage.argv[1] === 'run') assert(typeof manifest.scripts?.[stage.argv[2]] === 'string', 'Undeclared npm script');
      const row = await capture([job.runtime.nodePath, job.runtime.npmPath, ...stage.argv.slice(1)], cwd, env, deadline, budget);
      row.index = index; row.projectArgv = stage.argv; row.root = stage.root; report.stages.push(row); report.unrunStages = job.stages.map((_, i) => i).slice(index + 1);
      await cleanNamespace(Math.min(deadline + 2000, Date.now() + 2000));
      row.sourcesAfter = copiedSources(work + '/project', job.sources, deadline);
      baselineControls(); inputTree(base + '/source', job.sources, true, deadline); runtimeToolPins(settings.runtimeTools, deadline);
      runtimeFingerprints(job.runtime, job.schemaVersion === 2 ? job.runtimeFingerprints : null, deadline);
      if (row.exitCode !== 0 || row.termination !== null) break;
    }
    report.sourcesAfter = copiedSources(work + '/project', job.sources, deadline);
    baselineControls(); runtimeFingerprints(job.runtime, job.schemaVersion === 2 ? job.runtimeFingerprints : null, deadline); report.sourcesRestored = true;
    inputTree(base + '/source', job.sources, true, deadline); inputTree(base + '/npm-cache', job.cache, false, deadline);
    report.outputs = inventory(work, deadline, settings.inventoryBytes);
    report.status = report.stages.length === job.stages.length && report.stages.every(s => s.exitCode === 0 && s.termination === null) ? 'passed' : 'failed';
  } catch (error) { report.failure = String(error.message).slice(0, 2000); report.status = 'failed'; }
  return { report, maximum: job?.outputBytes || 4096 };
}
if (require.main === module) {
  if (process.argv.length === 3 && process.argv[2] === '--qualification-child') {
    Promise.resolve().then(() => {
      assert(fs.statSync(BASE + '/job.json').size <= 2 * 1024 * 1024);
      const job = validateJob(strictJSON(fs.readFileSync(BASE + '/job.json', 'utf8')));
      return qualifyOne(BASE, WORK, job);
    }).then(value => { process.stdout.write(JSON.stringify(value) + '\n'); }, error => { process.stdout.write(JSON.stringify({ failure: String(error.message).slice(0, 1000) }) + '\n'); process.exitCode = 1; });
  } else if (process.argv.length === 2) {
    run().then(({ report, maximum }) => { const encoded = encodeReport(report, maximum); process.stdout.write(encoded); process.exitCode = JSON.parse(encoded).status === 'passed' ? 0 : 1; }, error => { process.stdout.write(JSON.stringify({ status: 'failed', failure: String(error.message).slice(0, 1000) }) + '\n'); process.exitCode = 1; });
  } else { process.stdout.write('{"status":"failed","failure":"unsupported helper arguments"}\n'); process.exitCode = 1; }
}
module.exports = { npmTreeFingerprint, runtimeFingerprints, executionSettings, runtimeToolPins, classifyExecution, workspaceExecutionProbe, validateJob, strictJSON, inputTree, copiedSources, copyTree, inventory, environment, capture, namespaceProcesses, drainProcesses, qualifyOne, encodeReport, run, sha };
