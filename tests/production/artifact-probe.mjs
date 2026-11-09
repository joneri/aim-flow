import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export function playwrightModule(name) {
  const modules = process.env.AIM_CI_PLAYWRIGHT_ROOT;
  if (modules) {
    assert.equal(process.env.EXECUTION_MODE, 'synthetic-offline');
    return import(pathToFileURL(join(modules, name, 'index.mjs')).href);
  }
  return import(new URL(`../../fakeATG/node_modules/${name}/index.mjs`, import.meta.url).href);
}

const loopback = 'http://127.0.0.1:4173/';
const digestPattern = /^[a-f0-9]{64}$/;
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');

function fetchArtifact(url) {
  return fetch(url, { redirect: 'error', signal: AbortSignal.timeout(20_000) });
}

async function verifyArtifact(product, base, source, expectedContent, offline) {
  let release;
  for (let attempt = 0; attempt < (offline ? 1 : 12); attempt += 1) {
    const response = await fetchArtifact(`${base}release.json?sha=${source}&attempt=${attempt}`);
    if (response.ok) {
      const manifest = await response.json();
      if (manifest?.sourceSha === source) { release = manifest; break; }
    }
    if (!offline) await delay(5000);
  }
  assert.ok(release, 'Expected deployed source identity did not propagate');
  assert.equal(release.version, 1, 'Unsupported release manifest');
  assert.equal(release.product, product, 'Artifact product identity mismatch');
  assert.equal(release.contentSha256, expectedContent, 'Served content must match the verified CI artifact');
  assert.ok(release.files && typeof release.files === 'object' && !Array.isArray(release.files), 'Invalid file manifest');
  const paths = Object.keys(release.files).sort();
  assert.ok(paths.length < 200 && paths.includes('index.html'), 'Incomplete or excessive file manifest');
  const entries = paths.map((path) => {
    assert.ok(/^[A-Za-z0-9._/-]+$/.test(path) &&
      path.split('/').every((part) => part && part !== '.' && part !== '..') &&
      path !== 'release.json', 'Unsafe manifest path');
    const hash = release.files[path];
    assert.match(hash, digestPattern, 'Invalid file hash');
    return `${JSON.stringify(path)}:${JSON.stringify(hash)}`;
  });
  assert.equal(digest(`{${entries.join(',')}}`), expectedContent, 'File manifest hash differs from the verified CI artifact');

  const files = new Map();
  for (const path of paths) {
    const response = await fetchArtifact(`${base}${path}?sha=${source}`);
    assert.equal(response.status, 200, path);
    const bytes = Buffer.from(await response.arrayBuffer());
    assert.equal(digest(bytes), release.files[path], `Served file hash mismatch: ${path}`);
    files.set(path, bytes);
  }
  return { release, files };
}

export async function runProbe({ product, liveTarget, screenshot, check }) {
  const { PRODUCTION_URL: base, SOURCE_SHA: source, CONTENT_SHA256: expectedContent,
    EVIDENCE_DIR: output, EXECUTION_MODE: mode } = process.env;
  assert.ok(output);
  await mkdir(output, { recursive: true });
  const offline = mode === 'synthetic-offline';
  const result = { product, sourceSha: source, url: base, passed: false, checks: [],
    executionMode: offline ? 'synthetic-offline' : 'live' };
  const networkErrors = [];
  let browser;
  try {
    assert.ok(offline || (liveTarget && (mode === undefined || mode === 'live')), 'Unsupported probe execution mode');
    assert.equal(base, offline ? loopback : liveTarget, 'Unapproved probe destination');
    assert.match(source, /^[a-f0-9]{40}$/);
    assert.match(expectedContent, digestPattern);
    const { release, files } = await verifyArtifact(product, base, source, expectedContent, offline);
    result.contentSha256 = expectedContent;
    result.checks.push('complete-deployed-manifest');

    const destination = new URL(base);
    const errors = [];
    const { chromium } = await playwrightModule('playwright');
    const executablePath = process.env.AIM_CI_PLAYWRIGHT_ROOT ? process.env.AIM_CI_CHROMIUM : undefined;
    if (process.env.AIM_CI_PLAYWRIGHT_ROOT) assert.ok(executablePath, 'Pinned Chromium path required');
    browser = await chromium.launch({ executablePath });
    const context = await browser.newContext({ serviceWorkers: 'block' });
    await context.routeWebSocket('**/*', async (socket) => {
      networkErrors.push('Unexpected WebSocket request');
      await socket.close();
    });
    await context.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      try {
        assert.ok(url.origin === destination.origin && url.pathname.startsWith(destination.pathname),
          'Browser request outside the approved destination');
        const path = url.pathname.slice(destination.pathname.length) || 'index.html';
        assert.ok(request.method() === 'GET' && files.has(path), `Browser request outside the verified manifest: ${path}`);
        const response = await route.fetch({ maxRedirects: 0, timeout: 20_000 });
        assert.equal(response.status(), 200, `Unexpected browser response status: ${path}`);
        const body = await response.body();
        assert.ok(body.equals(files.get(path)), `Browser response differs from the verified artifact: ${path}`);
        const headers = response.headers();
        for (const name of ['content-encoding', 'content-length', 'transfer-encoding']) delete headers[name];
        await route.fulfill({ status: 200, headers, body });
      } catch (error) {
        networkErrors.push(error.message);
        await route.abort();
      }
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`${base}?sha=${source}`, { waitUntil: 'networkidle' });
    await check({ page, release, files, checks: result.checks });
    await page.screenshot({ path: `${output}/${screenshot}`, fullPage: true });
    assert.deepEqual(errors, [], 'Browser execution errors');
    assert.deepEqual(networkErrors, [], 'Browser requests deviated from the verified artifact');
    result.checks.push('no-external-product-requests', 'no-browser-errors');
    result.passed = true;
  } catch (error) {
    result.error = networkErrors.length ? networkErrors.join('; ') : error.message;
    throw new Error(result.error, { cause: error });
  } finally {
    await browser?.close();
    await writeFile(`${output}/result.json`, `${JSON.stringify(result, null, 2)}\n`);
  }
}
