'use strict'
// Exercise the shipped helper with local fixture bytes and a synthetic cacache adapter.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const { createHash } = require('node:crypto')
const { Readable, Writable } = require('node:stream')
const { test } = require('node:test')
const { browserRuntimeBytes, buildArgv, buildToolMetadata, collectArtifacts, contentVersion, copySource, installArgv, materialize,
  reportExitCode, runProjectScripts, verifyInstalled, workspaceMount } =
  require('../lib/aim_ci/npm_platform_build.cjs')

function temp(t) {
  const directory = fs.mkdtempSync(fs.realpathSync(os.tmpdir()) + '/aim-ci-npm-platform-test-')
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  return directory
}

const hash = (algorithm, bytes, format) => createHash(algorithm).update(bytes).digest(format)

test('source-only named check dispatches once without install, build, artifact or hidden inputs', t => {
  const { sourceCheckConfig, verifySourceFiles } = require('../lib/aim_ci/npm_platform_build.cjs')
  const canonical = value => JSON.stringify(value && typeof value === 'object' ?
    Array.isArray(value) ? value.map(item => JSON.parse(canonical(item))) :
      Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(canonical(value[key]))])) : value)
  const bytes = { 'package.json': Buffer.from('{"scripts":{"check:unit":"node check.mjs"}}'),
    'package-lock.json': Buffer.from('{"lockfileVersion":3}'), 'check.mjs': Buffer.from('console.log("ok")') }
  const sourceFiles = Object.fromEntries(Object.entries(bytes).map(([name, raw]) =>
    [name, { mode: 0o644, size: raw.length, sha256: hash('sha256', raw, 'hex') }]))
  const execution = { kind: 'npm-source-check-v1', dependencies: 'none', outputs: 'none',
    sourcePaths: Object.keys(bytes).sort() }
  const binding = { sourceProofSha256: '1'.repeat(64),
    sourceSelectionSha256: hash('sha256', canonical(sourceFiles), 'hex'),
    executionSha256: hash('sha256', canonical(execution), 'hex'),
    committedInputs: Object.fromEntries(['package.json', 'package-lock.json'].map(name => [name, sourceFiles[name].sha256])) }
  const config = { execution, sourceFiles, binding, artifact: { kind: 'none' },
    testScript: 'check:unit', timeoutSeconds: 2,
    tools: { node: { path: '/pinned/node' }, npm: { path: '/pinned/npm-cli.js' } } }
  assert.deepEqual(sourceCheckConfig(config), execution)
  const directory = temp(t)
  for (const [name, raw] of Object.entries(bytes)) fs.writeFileSync(directory + '/' + name, raw, { mode: 0o644 })
  verifySourceFiles(directory, sourceFiles)
  for (const status of [0, 7]) {
    const report = { version: 5, materialized: null, install: null, installedPackages: null, buildTools: null,
      build: null, artifacts: null, test: null, error: null }
    const calls = []
    let checked = 0
    runProjectScripts(config, report, {}, () => { verifySourceFiles(directory, sourceFiles); checked++ }, null,
      (file, argv) => {
        assert.equal(file, '/pinned/node')
        calls.push(argv)
        return { status, signal: null, stdout: Buffer.from('ok') }
      })
    assert.deepEqual(calls, [['/pinned/npm-cli.js', '--ignore-scripts', 'run', 'check:unit']])
    assert.equal(checked, 2)
    assert.equal(reportExitCode(report), status === 0 ? 0 : 1)
    assert.equal(reportExitCode({ ...report, installedPackages: 0 }), 1)
    assert.equal(report.build, null)
    assert.equal(report.artifacts, null)
  }
  fs.writeFileSync(directory + '/unselected.mjs', '')
  assert.throws(() => verifySourceFiles(directory, sourceFiles), /Unselected source file/)
  fs.unlinkSync(directory + '/unselected.mjs')
  fs.writeFileSync(directory + '/check.mjs', Buffer.alloc(bytes['check.mjs'].length, 32))
  assert.throws(() => verifySourceFiles(directory, sourceFiles), /Selected source file changed/)
  assert.throws(() => sourceCheckConfig({ ...config, execution: { ...execution, dependencies: 'install' } }), /contract/)
  assert.throws(() => sourceCheckConfig({ ...config, binding: { ...binding, sourceSelectionSha256: '9'.repeat(64) } }), /pins/)
  assert.throws(() => sourceCheckConfig({ ...config, testScript: 'check:unit;echo' }), /literal/)
})

test('large exact source manifests retain every selected file and verify streamed hashes', t => {
  const { sourceCheckConfig, verifySourceFiles } = require('../lib/aim_ci/npm_platform_build.cjs')
  const canonical = value => JSON.stringify(value && typeof value === 'object' ?
    Array.isArray(value) ? value.map(item => JSON.parse(canonical(item))) :
      Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(canonical(value[key]))])) : value)
  const directory = temp(t)
  const sourceFiles = {}
  const add = (name, data) => {
    fs.writeFileSync(directory + '/' + name, data, { mode: 0o644 })
    sourceFiles[name] = { mode: 0o644, size: data.length, sha256: hash('sha256', data, 'hex') }
  }
  for (let index = 0; index < 125; index++) add('file-' + index + '.css', Buffer.from('body{}'))
  add('package.json', Buffer.from('{}'))
  add('package-lock.json', Buffer.from('{}'))
  add('large.css', Buffer.alloc(32 * 1024 * 1024 + 1, 65))
  const execution = { kind: 'npm-source-check-v1', dependencies: 'none', outputs: 'none',
    sourcePaths: Object.keys(sourceFiles).sort() }
  const binding = { sourceProofSha256: '1'.repeat(64),
    sourceSelectionSha256: hash('sha256', canonical(sourceFiles), 'hex'),
    executionSha256: hash('sha256', canonical(execution), 'hex'),
    committedInputs: Object.fromEntries(['package.json', 'package-lock.json'].map(name => [name, sourceFiles[name].sha256])) }
  const config = { execution, sourceFiles, binding, artifact: { kind: 'none' }, testScript: 'check:source' }
  assert.deepEqual(sourceCheckConfig(config), execution)
  verifySourceFiles(directory, sourceFiles)
  const fd = fs.openSync(directory + '/large.css', 'r+')
  fs.writeSync(fd, Buffer.from('B'), 0, 1, sourceFiles['large.css'].size - 1)
  fs.closeSync(fd)
  assert.throws(() => verifySourceFiles(directory, sourceFiles), /changed/)
  delete sourceFiles['file-124.css']
  assert.throws(() => sourceCheckConfig(config), /manifest differs/)
})

test('fixed npm argv suppresses install and run lifecycle hooks', () => {
  assert.deepEqual(installArgv, ['ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'])
  assert.deepEqual(buildArgv, ['--ignore-scripts', 'run', 'build'])
  for (const value of [2, '2']) assert(contentVersion(value))
  for (const value of ['02', 3, true, null, undefined]) assert(!contentVersion(value))
})

test('the reviewed named test gates build, artifact collection and the helper exit', t => {
  const config = { testScript: 'check:unit', timeoutSeconds: 2,
    tools: { node: { path: '/pinned/node' }, npm: { path: '/pinned/npm-cli.js' } },
    artifact: { requiredFiles: ['index.html'], maximumBytes: 10 } }
  const env = { CI: 'true' }
  const passed = { status: 0, signal: null }
  for (const outcome of [passed, { status: 7, signal: null },
    { status: null, signal: 'SIGKILL', error: { code: 'ETIMEDOUT' } }]) {
    const dist = temp(t) + '/dist'
    const calls = []
    let inputChecks = 0
    const report = { version: 2, test: null, build: null, artifacts: null, error: null }
    runProjectScripts(config, report, env, () => inputChecks++, dist, (file, argv, options) => {
      assert.equal(file, '/pinned/node')
      assert.deepEqual(options, { cwd: '/work/project', env, timeout: 2000, killSignal: 'SIGKILL', maxBuffer: 131072 })
      calls.push(argv)
      if (calls.length === 1) return { ...outcome, stdout: Buffer.from('actual test log') }
      fs.mkdirSync(dist)
      fs.writeFileSync(dist + '/index.html', 'fixture')
      return passed
    })
    assert.deepEqual(calls[0], ['/pinned/npm-cli.js', '--ignore-scripts', 'run', 'check:unit'])
    assert.equal(report.test.exitCode, outcome.status)
    assert.equal(report.test.error, outcome.error?.code || null)
    assert.equal(Buffer.from(report.test.stdout, 'base64').toString(), 'actual test log')
    if (outcome === passed) {
      assert.deepEqual(calls[1], ['/pinned/npm-cli.js', ...buildArgv])
      assert.equal(inputChecks, 2)
      assert.equal(report.artifacts['index.html'].size, 7)
      assert.equal(reportExitCode(report), 0)
      assert.equal(reportExitCode({ ...report, test: null }), 1)
    } else {
      assert.equal(calls.length, 1)
      assert.equal(inputChecks, 0)
      assert.equal(report.build, null)
      assert.equal(report.artifacts, null)
      assert.equal(reportExitCode(report), 1)
    }
  }
  const forbidden = () => assert.fail('An invalid literal must never dispatch')
  for (const testScript of ['--offline', 'check:unit;touch', 'check:unit\n', null]) {
    assert.throws(() => runProjectScripts({ ...config, testScript }, {}, env, forbidden,
      temp(t) + '/dist', forbidden), /reviewed literal/)
  }
  const dist = temp(t) + '/dist'
  assert.throws(() => runProjectScripts(config, {}, env, () => {}, dist, (_, argv) => {
    assert.equal(argv.at(-1), config.testScript)
    fs.mkdirSync(dist)
    return passed
  }), /leave the build artifact directory absent/)
})

test('selected Playwright verifies discovery before tests and results before build', t => {
  const selector = { kind: 'playwright-names-v1', file: 'tests/receipt.spec.ts',
    project: 'chromium', suite: 'receipt component', names: ['opens receipt', 'closes receipt'] }
  const config = { testScript: 'check:browser', testSelector: selector, timeoutSeconds: 2,
    tools: { node: { path: '/node' }, npm: { path: '/npm' } },
    artifact: { requiredFiles: ['index.html'], maximumBytes: 10 } }
  const selected = ['/npm', '--ignore-scripts', '--silent', 'run', 'check:browser', '--',
    '--config=playwright.config.ts', '/^/work/project/tests/receipt\\.spec\\.ts$/',
    '--grep=/^chromium receipt\\.spec\\.ts receipt component (opens receipt|closes receipt)$/',
    '--project=chromium', '--no-deps', '--workers=1', '--retries=0', '--repeat-each=1',
    '--forbid-only', '--update-snapshots=none', '--reporter=json']
  const file = 'receipt.spec.ts'
  function jsonReport(discovery, variant) {
    const specs = selector.names.map(title => ({ title, file, ok: true, tags: [],
      tests: [{ projectName: selector.project, expectedStatus: 'passed', status: 'expected',
        results: discovery ? [] : [{ status: 'passed', retry: 0, errors: [] }] }] }))
    if (variant === 'missing') specs.pop()
    if (variant === 'duplicate') specs[1] = specs[0]
    if (variant === 'extra') specs.push({ ...specs[0], title: 'unapproved test' })
    if (variant === 'skip') specs[0].tests[0].results[0].status = 'skipped'
    const rootDir = variant === 'wrong-root' ? '/work/elsewhere' : '/work/project/tests'
    return Buffer.from(JSON.stringify({ config: { rootDir }, errors: [], stats: {
      expected: 2, skipped: 0, unexpected: 0, flaky: 0 },
    suites: [{ title: file, file, specs: [], suites: [{ title: selector.suite, file, specs }] }] }))
  }
  const env = { CI: 'true', PATH: '/node-bin:/usr/bin' }
  const browserEnv = { ...env, PLAYWRIGHT_BROWSERS_PATH: '/opt/aim-ci-browser' }
  for (const [discovery, result, expectedCalls, browserRuntime] of [
    ['missing', null, 1], ['duplicate', null, 1], ['extra', null, 1], ['wrong-root', null, 1],
    ['timeout', null, 1], [null, 'skip', 2], [null, 'missing', 2], [null, null, 3],
    [null, null, 3, { revision: '1217' }],
  ]) {
    const report = { version: 3, discovery: null, test: null, build: null, artifacts: null, error: null }
    const dist = temp(t) + '/dist'
    const calls = []
    const run = () => runProjectScripts({ ...config, browserRuntime }, report, env, () => {}, dist, (_, argv, options) => {
      calls.push(argv)
      assert.deepEqual(options.env, browserRuntime && calls.length < 3 ? browserEnv : env)
      if (calls.length === 1 && discovery === 'timeout')
        return { status: null, signal: 'SIGKILL', error: { code: 'ETIMEDOUT' } }
      if (calls.length === 3) {
        fs.mkdirSync(dist)
        fs.writeFileSync(dist + '/index.html', 'fixture')
      }
      const stdout = calls.length === 1 ? jsonReport(true, discovery) : jsonReport(false, result)
      return { status: 0, signal: null, stdout }
    })
    if (expectedCalls < 3 && discovery !== 'timeout') assert.throws(run, /Playwright/)
    else run()
    assert.deepEqual(calls, [[...selected, '--list'], selected, ['/npm', ...buildArgv]].slice(0, expectedCalls))
    assert.equal(reportExitCode(report), expectedCalls === 3 ? 0 : 1)
    assert(!Object.hasOwn(env, 'PLAYWRIGHT_BROWSERS_PATH'))
  }
  for (const injected of ['opens receipt|.*', 'opens receipt\n', 'opens receipt $(id)']) {
    assert.throws(() => runProjectScripts({ ...config, testSelector: { ...selector, names: [injected] } },
      {}, {}, () => {}, temp(t) + '/dist', () => assert.fail('Unexpected dispatch')), /Invalid literal/)
  }
})

test('browser runtime rechecks inventoried bytes and refuses wider network settings', async () => {
  const executable = '/opt/aim-ci-browser/chromium_headless_shell-1217/chrome-linux/headless_shell'
  const library = '/usr/lib/aarch64-linux-gnu/libstdc++.so.6.0.34'
  const files = { [executable]: { sha256: '1'.repeat(64), size: 42, mode: 0o755 },
    [library]: { sha256: '2'.repeat(64), size: 32, mode: 0o644 } }
  const platform = { os: 'linux', cpu: 'arm64', libc: 'glibc' }
  const profileSha256 = '3'.repeat(64)
  const network = { mode: 'none', loopback: 'container-only', externalEgress: 'deny',
    publishedPorts: [], hostMounts: [], hostOrVmDns: 'not-requested' }
  const config = { platform, binding: { browserRuntimeSha256: profileSha256 }, testSelector: {} }
  const browser = { profileSha256, platform, revision: '1217', executable, files, network }
  const measured = []
  const result = await browserRuntimeBytes(browser, config, async (filename, limit) => {
    measured.push(filename)
    assert.equal(limit, files[filename].size)
    return files[filename]
  })
  assert.deepEqual(measured, [executable, library])
  assert.deepEqual({ ...result }, files)
  await assert.rejects(browserRuntimeBytes(browser, config, async filename =>
    filename === library ? { ...files[library], sha256: '4'.repeat(64) } : files[filename]), /differs from reviewed/)
  const noRead = () => assert.fail('An invalid runtime must not read a file')
  await assert.rejects(browserRuntimeBytes({ ...browser, files: { [executable]: files[executable] } },
    config, noRead), /Incomplete/)
  await assert.rejects(browserRuntimeBytes({ ...browser, network: { ...network, hostMounts: ['/tmp'] } },
    config, noRead), /policy/)
  await assert.rejects(browserRuntimeBytes({ ...browser, files: {
    '/usr/lib/../libstdc++.so.6': files[library], [executable]: files[executable],
  } }, config, noRead), /inventory member/)
})

test('build-only retains its original report and does not dispatch the named test', t => {
  const dist = temp(t) + '/dist'
  const report = { version: 1, artifacts: null, error: null }
  const config = { timeoutSeconds: 2, tools: { node: { path: '/node' }, npm: { path: '/npm' } },
    artifact: { requiredFiles: ['index.html'], maximumBytes: 10 } }
  runProjectScripts(config, report, {}, () => {}, dist, (file, argv) => {
    assert.equal(file, '/node')
    assert.deepEqual(argv, ['/npm', ...buildArgv])
    fs.mkdirSync(dist)
    fs.writeFileSync(dist + '/index.html', 'ok')
    return { status: 0, signal: null }
  })
  assert.equal(reportExitCode(report), 0)
  assert(!Object.hasOwn(report, 'test'))
})

test('cache materialization checks both digests, size, and independent digest readback', async t => {
  const directory = temp(t)
  const filename = directory + '/fixture.tgz'
  const bytes = Buffer.from('synthetic tarball bytes')
  const row = { sha256: hash('sha256', bytes, 'hex'), integrity: 'sha512-' + hash('sha512', bytes, 'base64') }
  fs.writeFileSync(filename, bytes)
  let stored, puts = 0
  const cache = {
    put: { stream(root, key, options) {
      assert.equal(root, directory)
      assert.equal(key, 'aim-ci:sha256:' + row.sha256)
      assert.deepEqual(options, { size: bytes.length, integrity: row.integrity })
      puts++
      const chunks = []
      return new Writable({
        write(block, encoding, next) { chunks.push(block); next() },
        final(next) { stored = Buffer.concat(chunks); next() },
      })
    } },
    get: { stream: { byDigest(root, digest, options) {
      assert.equal(root, directory)
      assert.equal(digest, row.integrity)
      assert.deepEqual(options, { size: bytes.length })
      return Readable.from([stored])
    } } },
  }
  const included = { 'node_modules/vite': row, 'node_modules/@native/arm64': row }
  assert.equal(await materialize(cache, directory, () => filename, included), 1)
  assert.equal(puts, 1)
  assert.deepEqual(stored, bytes)
  fs.writeFileSync(filename, 'corrupt')
  await assert.rejects(materialize(cache, directory, () => filename, included), /mismatched/)
  assert.equal(puts, 1)
  fs.writeFileSync(filename, bytes)
  cache.get.stream.byDigest = () => Readable.from([Buffer.alloc(bytes.length)])
  await assert.rejects(materialize(cache, directory, () => filename, included), /strictly equal/)
})

test('applicable optional packages must be installed, including scoped and nested entries', t => {
  const directory = temp(t)
  const packages = { 'node_modules/@native/arm64': { version: '1.0.0' },
    'node_modules/vite': { version: '2.0.0' },
    'node_modules/vite/node_modules/child': { version: '3.0.0' } }
  for (const [name, manifest] of Object.entries(packages)) {
    fs.mkdirSync(directory + '/' + name, { recursive: true })
    fs.writeFileSync(directory + '/' + name + '/package.json', JSON.stringify(manifest))
  }
  const lock = { packages }
  verifyInstalled(directory, lock, packages)
  fs.rmSync(directory + '/node_modules/@native/arm64', { recursive: true })
  assert.throws(() => verifyInstalled(directory, lock, packages), /omitted/)
})

test('fresh artifact collection accepts font spaces and rejects linked files and overflow', t => {
  const directory = temp(t)
  fs.mkdirSync(directory + '/assets')
  fs.writeFileSync(directory + '/index.html', '<main>fixture</main>')
  fs.writeFileSync(directory + '/assets/Fixture Font.woff2', 'bytes')
  const policy = { requiredFiles: ['index.html', 'assets/Fixture Font.woff2'], maximumBytes: 100 }
  assert.deepEqual(Object.keys(collectArtifacts(directory, policy)).sort(), [...policy.requiredFiles].sort())
  assert.throws(() => collectArtifacts(directory, { ...policy, maximumBytes: 10 }), /oversized/)
  fs.symlinkSync(directory + '/index.html', directory + '/assets/link')
  assert.throws(() => collectArtifacts(directory, policy), /link/)
})

test('artifact inventory accepts more than 4096 files and preserves the reviewed byte limit', t => {
  const directory = temp(t)
  fs.writeFileSync(directory + '/index.html', 'x')
  for (let index = 0; index < 4100; index++) fs.writeFileSync(directory + '/file-' + index + '.css', 'x')
  const policy = { requiredFiles: ['index.html', ...Array.from({ length: 125 }, (_, i) => 'file-' + i + '.css')],
    maximumBytes: 40 * 1024 * 1024 }
  for (const maximumBytes of [0, -1, true, Number.MAX_SAFE_INTEGER + 1])
    assert.throws(() => collectArtifacts(directory, { ...policy, maximumBytes }), /exactly representable/)
  const files = collectArtifacts(directory, policy)
  assert.equal(Object.keys(files).length, 4101)
  assert.equal(files['file-4099.css'].sha256, hash('sha256', 'x', 'hex'))
  assert.throws(() => collectArtifacts(directory, { ...policy, maximumBytes: 4100 }), /oversized/)
  fs.symlinkSync(directory + '/index.html', directory + '/linked.css')
  assert.throws(() => collectArtifacts(directory, policy), /link/)
})

test('source copy accepts large assets and more than 4096 files without truncation', t => {
  const directory = temp(t)
  const input = directory + '/source'
  fs.mkdirSync(input)
  const file = input + '/Fixture Font.woff2'
  fs.writeFileSync(file, Buffer.alloc(2_000_001, 65))
  copySource(input, directory + '/project')
  assert.deepEqual(fs.readFileSync(directory + '/project/Fixture Font.woff2'), fs.readFileSync(file))
  fs.writeFileSync(input + '/a-extra', 'extra')
  fs.truncateSync(file, 32 * 1024 * 1024)
  for (let index = 0; index < 4100; index++) fs.writeFileSync(input + '/source-' + index, 'body{}')
  copySource(input, directory + '/large')
  assert.equal(fs.readdirSync(directory + '/large').length, 4102)
  assert.equal(fs.statSync(directory + '/large/Fixture Font.woff2').size, 32 * 1024 * 1024)
  assert.equal(hash('sha256', fs.readFileSync(directory + '/large/Fixture Font.woff2'), 'hex'),
    hash('sha256', fs.readFileSync(file), 'hex'))
  fs.symlinkSync(file, input + '/linked.css')
  assert.throws(() => copySource(input, directory + '/linked'), /link/)
})

test('source copy accepts long literal paths while the filesystem enforces its own format', t => {
  const directory = temp(t)
  const input = directory + '/source'
  const relative = 'directory/'.repeat(30) + 'a'.repeat(150) + '.css'
  fs.mkdirSync(require('node:path').dirname(input + '/' + relative), { recursive: true })
  fs.writeFileSync(input + '/' + relative, 'body{}')
  copySource(input, directory + '/project')
  assert.equal(fs.readFileSync(directory + '/project/' + relative, 'utf8'), 'body{}')
})

test('source copy stops at measured bytes and refuses growth during the read', t => {
  const directory = temp(t)
  const input = directory + '/source'
  fs.mkdirSync(input)
  const file = input + '/main.css'
  fs.writeFileSync(file, 'body{}')
  const original = fs.readSync
  let changed = false
  fs.readSync = (...args) => {
    const count = original(...args)
    if (!changed && count > 0) {
      changed = true
      fs.appendFileSync(file, 'must-not-be-copied')
    }
    return count
  }
  try {
    assert.throws(() => copySource(input, directory + '/project'), /changed while reading/)
  } finally {
    fs.readSync = original
  }
  assert.equal(fs.statSync(directory + '/project/main.css').size, 6)
})

test('mountinfo separates the effective mount and super options from execution assessment', () => {
  const row = '359 350 0:79 / /work rw,nosuid,nodev,noexec,relatime - tmpfs tmpfs rw,inode64'
  const other = '360 350 0:80 / /work\\040backup rw,nosuid,nodev,noexec - tmpfs tmpfs rw'
  const actual = workspaceMount(other + '\n' + row)
  assert.deepEqual(actual, { id: 359, parentId: 350, mountPoint: '/work', type: 'tmpfs',
    mountOptions: ['rw', 'nosuid', 'nodev', 'noexec', 'relatime'], superOptions: ['rw', 'inode64'],
    executable: false })
  assert.equal(workspaceMount(row.replace(',noexec', '')).executable, true)
  assert.throws(() => workspaceMount(other), /exactly one/)
  assert.throws(() => workspaceMount(row + '\n' + row), /exactly one/)
})

test('build tools report actual installed symlink, resolved mode and effective access', t => {
  const directory = temp(t)
  fs.mkdirSync(directory + '/node_modules/.bin', { recursive: true })
  fs.mkdirSync(directory + '/node_modules/typescript/bin', { recursive: true })
  const target = directory + '/node_modules/typescript/bin/tsc'
  fs.writeFileSync(target, 'fixture', { mode: 0o755 })
  fs.symlinkSync('../typescript/bin/tsc', directory + '/node_modules/.bin/tsc')
  const actual = buildToolMetadata(directory)
  assert.equal(actual.vite, null)
  assert.equal(actual.tsc.linkTarget, '../typescript/bin/tsc')
  assert.equal(actual.tsc.resolvedPath, 'node_modules/typescript/bin/tsc')
  assert.equal(actual.tsc.mode, 0o755)
  assert.equal(actual.tsc.executable, true)
  fs.chmodSync(target, 0o644)
  assert.equal(buildToolMetadata(directory).tsc.executable, false)
})
