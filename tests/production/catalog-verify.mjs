import assert from 'node:assert/strict';
import { playwrightModule, runProbe } from './artifact-probe.mjs';

const { expect } = await playwrightModule('@playwright/test');

async function verifyCatalog({ page, checks }) {
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(process.env.EXPECTED_HEADING);
  const items = await page.locator('li').evaluateAll(nodes => nodes.map(node => ({ id: node.dataset.id, text: node.textContent })));
  assert.deepEqual(items, [
    { id: 'X1', text: '<script>alert("demo")</script> 3.00' },
    { id: 'A1', text: 'Apple 2.25' },
    { id: 'A2', text: 'apple & pear 0.00' },
    { id: 'Z9', text: 'zebra 12.50' },
  ]);
  assert.equal(await page.locator('script').count(), 0);
  checks.push('catalog-content-order-escaping', 'story-heading');
}

await runProbe({
  product: 'catalog',
  screenshot: 'catalog.png',
  check: verifyCatalog,
});
