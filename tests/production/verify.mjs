import assert from 'node:assert/strict';
import { expect } from '../../fakeATG/node_modules/@playwright/test/index.mjs';
import { runProbe } from './artifact-probe.mjs';

async function verifyFakeAtg({ page, release, files, checks }) {
  assert.equal(release.fictional, true);
  assert.equal(release.realMoney, false);
  const fixture = JSON.parse(files.get('api/meeting.json'));
  const horses = fixture.meeting.race.horses;
  await page.getByTestId(`starter-${horses[0].id}`).waitFor();
  async function verify() {
    for (const horse of horses) {
      const radio = page.getByTestId(`starter-${horse.id}`).getByRole('radio');
      await expect(radio).toHaveAccessibleName(new RegExp(horse.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      await expect(radio).toHaveAccessibleName(new RegExp(`\\b${horse.gate}\\b`));
      if (process.env.EXPECTED_STARTER_PREFIX) {
        assert.ok((await radio.getAttribute('aria-label')).startsWith(process.env.EXPECTED_STARTER_PREFIX));
      }
      if (horse.scratched) {
        await expect(radio).toHaveAccessibleName(/scratched/i);
        assert.equal(await radio.isDisabled(), true);
      }
    }
  }
  await verify();
  await page.getByRole('combobox', { name: 'Sort start list' }).selectOption('descending');
  await verify();
  const rows = await page.getByRole('radiogroup').locator('[data-testid^="starter-"]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-testid')));
  assert.deepEqual(rows, [...horses].sort((a,b) => b.gate - a.gate).map(h => `starter-${h.id}`));
  await page.getByRole('checkbox', { name: 'Hide scratched horses' }).check();
  for (const horse of horses.filter(h => h.scratched)) {
    assert.equal(await page.getByTestId(`starter-${horse.id}`).count(), 0);
  }
  await page.getByRole('checkbox', { name: 'Hide scratched horses' }).uncheck();
  await verify();
  await page.getByRole('combobox', { name: 'Sort start list' }).selectOption('ascending');
  await verify();
  const ascending = await page.getByRole('radiogroup').locator('[data-testid^="starter-"]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-testid')));
  assert.deepEqual(ascending, [...horses].sort((a,b) => a.gate - b.gate).map(h => `starter-${h.id}`));
  await page.getByTestId(`starter-${horses.find(h => !h.scratched).id}`).getByRole('radio').check();
  await page.getByTestId('stake-input').fill('100');
  await page.getByRole('button', { name: 'Review fictional bet' }).click();
  await page.getByRole('button', { name: 'Confirm fictional bet' }).click();
  assert.match(await page.getByTestId('fictional-confirmation').innerText(), /No money moved/i);
  checks.push('number-name-pairing', 'ascending-sort-order', 'descending-sort-order', 'scratch-filter-and-disabled', 'fictional-bet');
  if (process.env.EXPECTED_STARTER_PREFIX) checks.push('story-accessible-wording');
}

await runProbe({
  product: 'FakeATG',
  liveTarget: 'https://joneri.github.io/agenticpipeline/',
  screenshot: 'production.png',
  check: verifyFakeAtg,
});
