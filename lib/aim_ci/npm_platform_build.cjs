'use strict'
// Fixed entrypoint for the private, reviewed npm platform build. Never imports project code.
const fs = require('node:fs')
const path = require('node:path')
const { createHash } = require('node:crypto')
const { createRequire } = require('node:module')
const { pipeline } = require('node:stream/promises')
const { spawnSync } = require('node:child_process')
const assert = require('node:assert/strict')

const layout = '/opt/aim-ci/qualification'
const store = layout + '/dependency-store'
const source = layout + '/source'
const workspace = '/work'
const project = workspace + '/project'
const browserRoot = '/opt/aim-ci-browser'
const inputLimit = 2_000_000
const sourceLimit = 32 * 1024 * 1024
const blobLimit = 64 * 1024 * 1024
const logLimit = 128 * 1024
const installArgv = ['ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund']
const buildArgv = ['--ignore-scripts', 'run', 'build']

// Exact observed npm internals for the digest-addressed cache and script suppression.
const npmPins = {
  'package.json': '7c04bf69824d17a1db64defd3a9a86d538781bf2d6ca2764e3f1143fa5b71166',
  'lib/commands/ci.js': '76cacc0ed9bbee8a7c1e5d5656201403a4dd688d31ee141b531c9e85a7e75e9e',
  'node_modules/@npmcli/config/lib/definitions/definitions.js': '83b2068320f1a131c96658940e80522ec1f29150b338ce40eccf486e0ee05900',
  'node_modules/@npmcli/arborist/lib/arborist/rebuild.js': '7c84a15c1946e9b32774c930a3241e55ad9e2a5b77d5864c2b753235a665ac5e',
  'node_modules/pacote/package.json': '874de452ca436ca261620e503b47cc0e5ba81b4778bf42daf486148f3fa03b70',
  'node_modules/pacote/lib/fetcher.js': '1a385341f75d6c77496d30e7751012ad91489bb8c4e4e4db5e4568d2b9432a79',
  'node_modules/cacache/package.json': '3b4d9ce8c3a3f853d903c06b5ec38a06c8ac9a2ccb236bf6a532d92cfe1b07de',
  'node_modules/cacache/lib/get.js': '4127c3adb8bc9f530dcb6ed80a0c6c00288f1db8c6939146957d03454cac06c9',
  'node_modules/cacache/lib/put.js': 'b131954efbcb17f785e93278c53f4b0491c53009698b937ef68bbc7342134872',
  'node_modules/cacache/lib/content/path.js': '7ec9682ee3472435d866bdd35d18e2d570ffe98621bc230f30d31443bd04d8f7',
  'node_modules/cacache/lib/content/write.js': '5230de0ca3d99936e42220445f44f5a688fa154ce6a16c643762714bfe0d8b82',
}

const sha256 = data => createHash('sha256').update(data).digest('hex')
const canonical = value => JSON.stringify(value && typeof value === 'object' ?
  Array.isArray(value) ? value.map(item => JSON.parse(canonical(item))) :
    Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(canonical(value[key]))])) : value)
const contentVersion = value => value === '2' || value === 2
const hexPin = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const projectPath = value => value.length <= 240 && value.split('/').every(part =>
  part.length <= 128 && part !== '.' && part !== '..' &&
  /^[A-Za-z0-9@_.+-]+(?: [A-Za-z0-9@_.+-]+)*$/.test(part))
const lockPath = /^node_modules\/(?:@[a-z0-9_.-]+\/)?[a-z0-9_][a-z0-9_.-]*(?:\/node_modules\/(?:@[a-z0-9_.-]+\/)?[a-z0-9_][a-z0-9_.-]*)*$/

function regular(filename, limit) {
  const info = fs.lstatSync(filename)
  assert(info.isFile() && info.nlink === 1 && info.size <= limit && fs.realpathSync(filename) === filename,
    'Unsafe or oversized regular file')
  return info
}

function read(filename, limit = inputLimit) {
  regular(filename, limit)
  return fs.readFileSync(filename, { flag: fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW })
}

const json = filename => JSON.parse(read(filename))

async function measured(filename, limit) {
  const info = regular(filename, limit)
  const h256 = createHash('sha256')
  const h512 = createHash('sha512')
  let size = 0
  for await (const block of fs.createReadStream(filename, { flags: fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW })) {
    size += block.length
    assert(size <= limit)
    h256.update(block)
    h512.update(block)
  }
  assert.equal(size, info.size)
  return { size, sha256: h256.digest('hex'), integrity: 'sha512-' + h512.digest('base64'), mode: info.mode & 0o7777 }
}

async function observeRuntime(tools, platform) {
  assert(process.platform === platform.os && process.arch === platform.cpu &&
    platform.libc === 'glibc' && process.getuid() === 65534 && process.getgid() === 65534)
  const glibc = process.report.getReport().header.glibcVersionRuntime
  assert(glibc && process.version === 'v22.23.3' && process.execPath === tools.node.path)
  const processStatus = fs.readFileSync('/proc/self/status', 'utf8')
  assert(/^NoNewPrivs:\s+1$/m.test(processStatus) && /^CapEff:\s+0+$/m.test(processStatus))
  for (const [name, limit] of [['node', 150 * 1024 * 1024], ['npm', inputLimit]]) {
    assert.equal((await measured(tools[name].path, limit)).sha256, tools[name].sha256)
  }
  const npmRoot = path.dirname(path.dirname(tools.npm.path))
  for (const [filename, pin] of Object.entries(npmPins)) assert.equal(sha256(read(npmRoot + '/' + filename)), pin)
  const npm = json(npmRoot + '/package.json')
  const cacache = json(npmRoot + '/node_modules/cacache/package.json')
  const pacote = json(npmRoot + '/node_modules/pacote/package.json')
  const raw = cacache['cache-version']?.content
  assert(npm.version === '10.9.9' && cacache.version === '19.0.1' && pacote.version === '19.0.2' && contentVersion(raw))
  const npmRequire = createRequire(tools.npm.path)
  const cacheMain = npmRequire.resolve('cacache')
  const pacoteMain = npmRequire.resolve('pacote')
  assert.equal(cacheMain, npmRoot + '/node_modules/cacache/lib/index.js')
  assert(pacoteMain.startsWith(npmRoot + '/node_modules/pacote/lib/') &&
    createRequire(pacoteMain).resolve('cacache') === cacheMain)
  return { cache: npmRequire('cacache'), observation: { node: process.version, npm: npm.version,
    cacache: cacache.version, pacote: pacote.version, contentVersionRaw: raw, contentVersion: 2, glibc } }
}

async function materialize(cache, directory, open, included) {
  const seeded = new Map()
  for (const [name, row] of Object.entries(included)) {
    assert(lockPath.test(name) && hexPin(row.sha256) && /^sha512-[A-Za-z0-9+/]{86}==$/.test(row.integrity))
    if (seeded.has(row.sha256)) {
      assert.equal(seeded.get(row.sha256), row.integrity)
      continue
    }
    const filename = open(row.sha256)
    const bytes = await measured(filename, blobLimit)
    assert(bytes.size && bytes.sha256 === row.sha256 && bytes.integrity === row.integrity,
      'Missing or mismatched applicable npm tarball')
    await pipeline(fs.createReadStream(filename, { flags: fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW }),
      cache.put.stream(directory, 'aim-ci:sha256:' + row.sha256, { integrity: row.integrity, size: bytes.size }))
    const hash = createHash('sha256')
    let size = 0
    for await (const block of cache.get.stream.byDigest(directory, row.integrity, { size: bytes.size })) {
      size += block.length
      assert(size <= bytes.size)
      hash.update(block)
    }
    assert.equal(size, bytes.size)
    assert.equal(hash.digest('hex'), row.sha256)
    seeded.set(row.sha256, row.integrity)
  }
  return seeded.size
}

function walk(directory, relative, visit, skip = () => false) {
  assert(fs.lstatSync(directory).isDirectory() && fs.realpathSync(directory) === directory)
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const name = relative ? relative + '/' + entry.name : entry.name
    assert(projectPath(name), 'Unsafe source or artifact path')
    if (skip(name)) continue
    const filename = directory + '/' + entry.name
    assert(entry.isFile() || entry.isDirectory(), 'Source or artifact contains a link or special file')
    visit(filename, name, entry.isDirectory())
    if (entry.isDirectory()) walk(filename, name, visit, skip)
  }
}

function copySource(input, output) {
  let count = 0
  let bytes = 0
  fs.mkdirSync(output, { mode: 0o700 })
  walk(input, '', (filename, name, directory) => {
    assert(++count <= 4096 && !name.split('/').some(part =>
      ['.git', 'node_modules', '.npmrc'].includes(part) || /^\.env(?:\.|$)/.test(part)))
    const destination = output + '/' + name
    if (directory) fs.mkdirSync(destination, { mode: 0o700 })
    else {
      const info = regular(filename, sourceLimit - bytes)
      fs.copyFileSync(filename, destination, fs.constants.COPYFILE_EXCL)
      assert.equal(regular(destination, sourceLimit - bytes).size, info.size, 'Source size changed during copy')
      bytes += info.size
      fs.chmodSync(destination, info.mode & 0o111 ? 0o755 : 0o644)
    }
  }, name => name === 'dist') // A tracked dist is not a fresh build output.
}

function sourceCheckConfig(config) {
  const { execution, sourceFiles, binding, artifact, testScript } = config
  assert(execution && Object.keys(execution).sort().join(' ') === 'dependencies kind outputs sourcePaths' &&
    execution.kind === 'npm-source-check-v1' && execution.dependencies === 'none' && execution.outputs === 'none' &&
    artifact && canonical(artifact) === '{"kind":"none"}' &&
    config.testSelector === undefined && config.browserRuntime === undefined, 'Invalid source check contract')
  const paths = execution.sourcePaths
  assert(Array.isArray(paths) && paths.length >= 2 && paths.length <= 100 &&
    paths.every(name => typeof name === 'string' && projectPath(name) && !name.split('/').some(part =>
      ['.git', 'node_modules', '.npmrc', 'dist'].includes(part) || /^\.env(?:\.|$)/.test(part))) &&
    new Set(paths).size === paths.length && canonical(paths) === canonical([...paths].sort()) &&
    paths.includes('package.json') && paths.includes('package-lock.json'), 'Invalid exact source selection')
  assert(sourceFiles && canonical(Object.keys(sourceFiles).sort()) === canonical(paths),
    'Source check manifest differs from selection')
  let bytes = 0
  for (const row of Object.values(sourceFiles)) {
    assert(row && Object.keys(row).sort().join(' ') === 'mode sha256 size' &&
      [0o644, 0o755].includes(row.mode) && Number.isInteger(row.size) && row.size >= 0 && hexPin(row.sha256),
    'Invalid selected source manifest entry')
    bytes += row.size
  }
  assert(bytes <= sourceLimit && hexPin(binding.sourceProofSha256) &&
    sha256(canonical(execution)) === binding.executionSha256 &&
    sha256(canonical(sourceFiles)) === binding.sourceSelectionSha256 &&
    ['package.json', 'package-lock.json'].every(name => sourceFiles[name].sha256 === binding.committedInputs[name]),
  'Source check input pins differ')
  namedTestArgv(testScript)
  return execution
}

function verifySourceFiles(directory, manifest) {
  const seen = new Set()
  const parents = new Set(Object.keys(manifest).flatMap(name => {
    const parts = name.split('/')
    return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'))
  }))
  walk(directory, '', (filename, name, isDirectory) => {
    if (isDirectory) {
      assert(parents.has(name), 'Unselected source directory')
      return
    }
    const row = manifest[name]
    assert(Object.hasOwn(manifest, name) && !seen.has(name), 'Unselected source file')
    const info = regular(filename, row.size)
    assert(info.size === row.size && (info.mode & 0o7777) === row.mode &&
      sha256(read(filename, row.size)) === row.sha256, 'Selected source file changed')
    seen.add(name)
  })
  assert.equal(seen.size, Object.keys(manifest).length, 'Selected source file is missing')
}

function verifyInstalled(directory, lock, included) {
  const seen = new Set()
  function packages(directory, relative) {
    assert(fs.lstatSync(directory).isDirectory() && fs.realpathSync(directory) === directory)
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name === '.bin' || entry.name === '.package-lock.json') continue
      assert(entry.isDirectory() && !entry.name.startsWith('.'))
      const absolute = directory + '/' + entry.name
      const name = relative + '/' + entry.name
      if (entry.name.startsWith('@')) {
        assert(relative === 'node_modules' || relative.endsWith('/node_modules'))
        packages(absolute, name)
        continue
      }
      assert(lockPath.test(name) && Object.hasOwn(included, name) && !seen.has(name))
      assert.equal(json(absolute + '/package.json').version, lock.packages[name].version)
      seen.add(name)
      const nested = absolute + '/node_modules'
      if (fs.existsSync(nested)) packages(nested, name + '/node_modules')
    }
  }
  if (Object.keys(included).length) packages(directory + '/node_modules', 'node_modules')
  assert.equal(seen.size, Object.keys(included).length, 'Applicable npm package omitted during installation')
}

function workspaceMount(mountinfo) {
  const unescape = value => value.replace(/\\(040|011|012|134)/g, (_, octal) =>
    String.fromCharCode(parseInt(octal, 8)))
  const rows = mountinfo.trim().split('\n').map(line => line.split(' '))
    .filter(fields => fields.length >= 10 && unescape(fields[4]) === '/work')
  assert.equal(rows.length, 1, 'Expected exactly one /work mount')
  const fields = rows[0]
  const separator = fields.indexOf('-', 6)
  assert(separator >= 6 && fields.length === separator + 4 && /^\d+$/.test(fields[0]) &&
    /^\d+$/.test(fields[1]), 'Invalid /work mountinfo fields')
  const mountOptions = fields[5].split(',')
  const superOptions = fields[separator + 3].split(',')
  return { id: Number(fields[0]), parentId: Number(fields[1]), mountPoint: '/work',
    type: fields[separator + 1], mountOptions, superOptions,
    executable: ![...mountOptions, ...superOptions].includes('noexec') }
}

function buildToolMetadata(directory) {
  return Object.fromEntries(['tsc', 'vite'].map(name => {
    const entry = directory + '/node_modules/.bin/' + name
    let link
    try {
      link = fs.lstatSync(entry)
    } catch (error) {
      if (error.code === 'ENOENT') return [name, null]
      throw error
    }
    const resolved = fs.realpathSync(entry)
    const relative = path.relative(directory, resolved)
    assert(relative.startsWith('node_modules/') && projectPath(relative), 'Build tool resolves outside installed dependencies')
    const info = fs.lstatSync(resolved)
    assert(info.isFile(), 'Build tool target is not a regular file')
    let executable = true
    try {
      fs.accessSync(resolved, fs.constants.X_OK)
    } catch (error) {
      if (error.code !== 'EACCES') throw error
      executable = false
    }
    return [name, { entry: 'node_modules/.bin/' + name, linkTarget: link.isSymbolicLink() ? fs.readlinkSync(entry) : null,
      resolvedPath: relative, mode: info.mode & 0o7777, uid: info.uid, gid: info.gid, executable }]
  }))
}

function namedTestArgv(script) {
  assert(typeof script === 'string' && /^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$/.test(script),
    'Expected a reviewed literal npm test script')
  return ['--ignore-scripts', 'run', script]
}

function selectedTestArgv(script, selector) {
  const legacy = namedTestArgv(script)
  if (selector === undefined) return legacy
  assert(selector && Object.keys(selector).sort().join(' ') === 'file kind names project suite' &&
    selector.kind === 'playwright-names-v1', 'Invalid Playwright selection fields')
  const { file, project: selectedProject, suite, names } = selector
  const title = value => typeof value === 'string' && value.length <= 150 &&
    /^[A-Za-z0-9]+(?: [A-Za-z0-9]+)*$/.test(value)
  assert(typeof file === 'string' && /^tests\/[A-Za-z0-9_-]{1,90}\.spec\.ts$/.test(file) &&
    typeof selectedProject === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/.test(selectedProject) &&
    title(suite) && Array.isArray(names) && names.length >= 1 && names.length <= 16 &&
    names.every(title) && new Set(names).size === names.length, 'Invalid literal Playwright selection')
  const escaped = file.replaceAll('.', '\\.')
  const prefix = selectedProject + ' ' + escaped.slice('tests/'.length) + ' ' + suite
  return ['--ignore-scripts', '--silent', 'run', script, '--', '--config=playwright.config.ts',
    '/^/work/project/' + escaped + '$/', '--grep=/^' + prefix + ' (' + names.join('|') + ')$/',
    '--project=' + selectedProject, '--no-deps', '--workers=1', '--retries=0', '--repeat-each=1',
    '--forbid-only', '--update-snapshots=none', '--reporter=json']
}

function verifySelectedTests(result, selector, discovery) {
  const report = JSON.parse(Buffer.from(result.stdout, 'base64').toString('utf8'))
  const relative = selector.file.slice('tests/'.length)
  const file = report.suites?.[0]
  const suite = file?.suites?.[0]
  assert(Array.isArray(report.errors) && report.errors.length === 0 &&
    report.config?.rootDir === '/work/project/tests' &&
    Array.isArray(report.suites) && report.suites.length === 1 && file.title === relative && file.file === relative &&
    Array.isArray(file.specs) && file.specs.length === 0 && Array.isArray(file.suites) && file.suites.length === 1 &&
    suite.title === selector.suite && suite.file === relative &&
    (suite.suites === undefined || Array.isArray(suite.suites) && suite.suites.length === 0) &&
    Array.isArray(suite.specs) && suite.specs.length === selector.names.length,
    'Playwright report differs from the reviewed file, suite or test count')
  const unseen = new Set(selector.names)
  for (const spec of suite.specs) {
    const test = spec?.tests?.[0]
    assert(unseen.delete(spec?.title) && spec.file === relative && Array.isArray(spec.tags) && !spec.tags.length &&
      Array.isArray(spec.tests) && spec.tests.length === 1 &&
      test?.projectName === selector.project && test.expectedStatus === 'passed' && Array.isArray(test.results),
      'Playwright report contains a missing, extra or duplicate test')
    if (discovery) {
      assert(test.results.length === 0, 'Playwright discovery contains test executions')
    } else {
      assert(spec.ok === true && test.status === 'expected' && test.results.length === 1 &&
        test.results[0]?.status === 'passed' && test.results[0].retry === 0 &&
        Array.isArray(test.results[0].errors) && test.results[0].errors.length === 0,
        'Every selected Playwright test must pass once without retry')
    }
  }
  if (!discovery) {
    assert(report.stats?.expected === selector.names.length && report.stats.skipped === 0 &&
      report.stats.unexpected === 0 && report.stats.flaky === 0, 'Playwright totals differ from the reviewed result')
  }
}

function runOperation(tools, args, env, timeout, spawn = spawnSync) {
  const child = spawn(tools.node.path, [tools.npm.path, ...args],
    { cwd: project, env, timeout, killSignal: 'SIGKILL', maxBuffer: logLimit })
  return { exitCode: child.status, signal: child.signal, error: child.error?.code || null,
    stdout: (child.stdout || Buffer.alloc(0)).subarray(0, logLimit).toString('base64'),
    stderr: (child.stderr || Buffer.alloc(0)).subarray(0, logLimit).toString('base64') }
}

const passed = result => result.exitCode === 0 && result.signal === null && result.error === null

const directoryFlags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW
const fdPath = fd => '/proc/self/fd/' + fd

function collectArtifacts(directory, policy) {
  const files = Object.create(null)
  let size = 0
  let count = 0
  function inspect(directory, relative) {
    const parent = fs.openSync(directory, directoryFlags)
    try {
      // The container runs on Linux. Anchor each read to its already-open parent.
      const base = process.platform === 'linux' ? fdPath(parent) : directory
      for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
        const name = relative ? relative + '/' + entry.name : entry.name
        assert(++count <= 4096 && projectPath(name), 'Unsafe or oversized artifact inventory')
        const filename = base + '/' + entry.name
        if (entry.isDirectory()) {
          inspect(filename, name)
          continue
        }
        assert(entry.isFile(), 'Build artifact contains a link or special file')
        const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK)
        try {
          const info = fs.fstatSync(fd)
          assert(info.isFile() && info.nlink === 1 && info.size <= policy.maximumBytes - size,
            'Unsafe or oversized build artifact')
          const bytes = Buffer.alloc(info.size)
          for (let offset = 0; offset < bytes.length;) {
            const count = fs.readSync(fd, bytes, offset, bytes.length - offset, null)
            assert(count > 0, 'Build artifact changed while being read')
            offset += count
          }
          assert.equal(fs.readSync(fd, Buffer.alloc(1), 0, 1, null), 0, 'Build artifact grew while being read')
          size += bytes.length
          files[name] = { size: bytes.length, sha256: sha256(bytes), data: bytes.toString('base64') }
        } finally {
          fs.closeSync(fd)
        }
      }
    } finally {
      fs.closeSync(parent)
    }
  }
  inspect(directory, '')
  assert(policy.requiredFiles.every(name => Object.hasOwn(files, name)) && files['index.html']?.size > 0)
  return files
}

async function browserRuntimeBytes(browser, config, measure = measured) {
  const { platform, binding, testSelector } = config
  const { network, executable, files } = browser
  const suffix = platform.cpu === 'arm64' ? 'chrome-linux/headless_shell' :
    'chrome-headless-shell-linux64/chrome-headless-shell'
  assert(testSelector && browser.profileSha256 === binding.browserRuntimeSha256 &&
    browser.revision === '1217' && platform.os === 'linux' && platform.libc === 'glibc' &&
    ['arm64', 'x64'].includes(platform.cpu) &&
    ['os', 'cpu', 'libc'].every(axis => platform[axis] === browser.platform[axis]) &&
    executable === browserRoot + '/chromium_headless_shell-1217/' + suffix &&
    network.mode === 'none' && network.loopback === 'container-only' &&
    network.externalEgress === 'deny' && network.hostOrVmDns === 'not-requested' &&
    Array.isArray(network.publishedPorts) && network.publishedPorts.length === 0 &&
    Array.isArray(network.hostMounts) && network.hostMounts.length === 0, 'Invalid reviewed browser identity or policy')
  assert(Object.hasOwn(files, executable) && Object.keys(files).length >= 2 && Object.keys(files).length <= 9,
    'Incomplete reviewed browser inventory')
  const observed = Object.create(null)
  for (const [filename, expected] of Object.entries(files)) {
    const binary = filename === executable
    const maximum = (binary ? 256 : 32) * 1024 * 1024
    assert((binary || /^\/usr\/lib\/[A-Za-z0-9_.+-]+(?:\/[A-Za-z0-9_.+-]+)*\.so(?:\.[0-9]+)*$/.test(filename)) &&
      filename.length <= 240 && filename.split('/').every(part => part !== '.' && part !== '..' && part.length <= 128) &&
      hexPin(expected.sha256) && Number.isInteger(expected.size) && expected.size >= 20 && expected.size <= maximum &&
      (expected.mode === 0o755 || !binary && expected.mode === 0o644), 'Invalid browser inventory member')
    const { size, sha256, mode } = await measure(filename, expected.size)
    assert(size === expected.size && sha256 === expected.sha256 && mode === expected.mode,
      'Browser runtime file differs from reviewed image readback')
    observed[filename] = { sha256, size, mode }
  }
  return observed
}

function runProjectScripts(config, report, env, checkInputs, dist, spawn = spawnSync) {
  const { tools, timeoutSeconds, artifact, testScript, testSelector, browserRuntime } = config
  const timeout = timeoutSeconds * 1000
  if (config.execution !== undefined) {
    sourceCheckConfig(config)
    checkInputs()
    report.test = runOperation(tools, namedTestArgv(testScript), env, timeout, spawn)
    checkInputs()
    return
  }
  assert(!fs.existsSync(dist), 'Build artifact directory must be fresh')
  if (testScript !== undefined) {
    const argv = selectedTestArgv(testScript, testSelector)
    const testEnv = browserRuntime === undefined ? env : { ...env, PLAYWRIGHT_BROWSERS_PATH: browserRoot }
    if (testSelector !== undefined) {
      report.discovery = runOperation(tools, [...argv, '--list'], testEnv, timeout, spawn)
      if (!passed(report.discovery)) return
      verifySelectedTests(report.discovery, testSelector, true)
      checkInputs()
      assert(!fs.existsSync(dist), 'Playwright discovery must leave the build artifact directory absent')
    }
    report.test = runOperation(tools, argv, testEnv, timeout, spawn)
    if (!passed(report.test)) return
    if (testSelector !== undefined) verifySelectedTests(report.test, testSelector, false)
    checkInputs()
    assert(!fs.existsSync(dist), 'Named test must leave the build artifact directory absent')
  }
  report.build = runOperation(tools, buildArgv, env, timeout, spawn)
  if (!passed(report.build)) return
  checkInputs()
  report.artifacts = collectArtifacts(dist, artifact)
}

function reportExitCode(report) {
  if (report.version === 5)
    return report.test && passed(report.test) && !report.error &&
      ['materialized', 'install', 'installedPackages', 'buildTools', 'build', 'artifacts']
        .every(name => report[name] === null) ? 0 : 1
  return report.artifacts && !report.error &&
    (report.version === 1 || report.test && passed(report.test)) ? 0 : 1
}

async function execute(config, report) {
  const { tools, binding, platform, timeoutSeconds, testScript, testSelector } = config
  const packagePins = binding.committedInputs
  const sourceCheck = config.execution === undefined ? null : sourceCheckConfig(config)
  assert(process.argv.length === 3 && Number.isInteger(timeoutSeconds) && timeoutSeconds >= 1 && timeoutSeconds <= 600)
  const commit = read(layout + '/source.commit')
  assert.equal(createHash('sha1').update(`commit ${commit.length}\0`).update(commit).digest('hex'), binding.sourceCommit)
  assert(commit.toString().startsWith('tree ' + binding.sourceTree + '\n'))
  const indexBytes = read(store + '/index.json')
  assert.equal(sha256(indexBytes), binding.dependencyStoreSha256)
  const index = JSON.parse(indexBytes)
  assert(index.kind === (sourceCheck ? 'npm-no-install-v1' : 'npm-v3-platform-tarballs-v1') &&
    index.lockSha256 === packagePins['package-lock.json'])
  if (sourceCheck) {
    assert.equal(Object.keys(index).sort().join(' '), 'kind lockSha256 platform')
    assert.equal(sha256(read(layout + '/source.proof.json')), binding.sourceProofSha256)
    verifySourceFiles(source, config.sourceFiles)
  }
  for (const axis of ['os', 'cpu', 'libc']) assert.equal(index.platform[axis], platform[axis])
  report.workspaceMount = workspaceMount(fs.readFileSync('/proc/self/mountinfo', 'utf8'))
  const { type, mountOptions, executable } = report.workspaceMount
  assert(type === 'tmpfs' && executable && ['rw', 'nosuid', 'nodev'].every(option => mountOptions.includes(option)),
    'Private build requires an executable tmpfs workspace with nosuid and nodev')
  const { cache, observation } = await observeRuntime(tools, platform)
  report.runtime = observation
  if (config.browserRuntime !== undefined)
    report.browserFiles = await browserRuntimeBytes(config.browserRuntime, config)
  copySource(source, project)
  const reviewedFiles = { ...packagePins }
  if (testSelector !== undefined) {
    selectedTestArgv(testScript, testSelector)
    for (const name of [testSelector.file, 'playwright.config.ts'])
      reviewedFiles[name] = sha256(read(source + '/' + name))
  }
  const checkInputs = () => {
    if (sourceCheck) return verifySourceFiles(project, config.sourceFiles)
    for (const [name, expected] of Object.entries(reviewedFiles))
      assert.equal(sha256(read(project + '/' + name)), expected)
  }
  checkInputs()
  if (testScript !== undefined) {
    namedTestArgv(testScript)
    const scripts = json(project + '/package.json').scripts
    assert(scripts && Object.hasOwn(scripts, testScript) &&
      typeof scripts[testScript] === 'string' && scripts[testScript].trim(),
      'Reviewed named test is missing from the committed package')
    if (sourceCheck) assert(!Object.hasOwn(scripts, 'pre' + testScript) && !Object.hasOwn(scripts, 'post' + testScript),
      'Source check cannot silently skip selected-script lifecycle hooks')
    if (testSelector !== undefined) assert.equal(scripts[testScript], 'playwright test')
  }
  const lock = json(project + '/package-lock.json')
  const cacheRoot = workspace + '/npm-cache'
  const env = { PATH: path.dirname(tools.node.path) + ':/usr/local/bin:/usr/bin:/bin', LC_ALL: 'C', CI: 'true',
    HOME: workspace + '/home', TMPDIR: workspace + '/tmp', npm_config_cache: cacheRoot,
    npm_config_userconfig: workspace + '/empty-user.npmrc', npm_config_globalconfig: workspace + '/empty-global.npmrc',
    npm_config_logs_max: '0', npm_config_offline: 'true', npm_config_ignore_scripts: 'true' }
  for (const directory of [env.HOME, env.TMPDIR, cacheRoot]) fs.mkdirSync(directory, { mode: 0o700 })
  for (const filename of [env.npm_config_userconfig, env.npm_config_globalconfig])
    fs.writeFileSync(filename, '', { flag: 'wx', mode: 0o600 })
  if (sourceCheck) {
    runProjectScripts(config, report, env, checkInputs, null)
    return
  }
  report.materialized = await materialize(cache, cacheRoot + '/_cacache', sha => store + '/blobs/' + sha + '.tgz',
    index.included)
  report.install = runOperation(tools, installArgv, env, timeoutSeconds * 1000)
  if (!passed(report.install)) return
  checkInputs()
  verifyInstalled(project, lock, index.included)
  report.installedPackages = Object.keys(index.included).length
  report.buildTools = buildToolMetadata(project)
  const projectFd = fs.openSync(project, directoryFlags)
  try {
    runProjectScripts(config, report, env, checkInputs, fdPath(projectFd) + '/dist')
  } finally {
    fs.closeSync(projectFd)
  }
}

async function main() {
  const config = JSON.parse(Buffer.from(process.argv[2], 'base64').toString())
  const report = { version: 1, effectId: config.effectId, binding: config.binding,
    runtime: null, workspaceMount: null, materialized: null, install: null, installedPackages: null,
    buildTools: null, build: null, artifacts: null, error: null }
  if (config.testScript !== undefined) Object.assign(report, { version: 2, testScript: config.testScript, test: null })
  if (config.testSelector !== undefined)
    Object.assign(report, { version: 3, testSelector: config.testSelector, discovery: null })
  if (config.browserRuntime !== undefined)
    Object.assign(report, { version: 4, browserFiles: null })
  if (config.execution !== undefined)
    Object.assign(report, { version: 5, execution: config.execution, testScript: config.testScript, test: null })
  try {
    await execute(config, report)
  } catch (error) {
    report.error = String(error.message || error).slice(0, 1000)
  }
  process.stdout.write(JSON.stringify(report) + '\n')
  process.exitCode = reportExitCode(report)
}

if (require.main === module) main().catch(() => { process.exitCode = 1 })
module.exports = { browserRuntimeBytes, buildArgv, buildToolMetadata, collectArtifacts, contentVersion, copySource, installArgv,
  materialize, reportExitCode, runProjectScripts, sourceCheckConfig, verifyInstalled, verifySourceFiles, workspaceMount }
