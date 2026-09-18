import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const out = resolve(process.env.POKER_UI_OUTPUT || 'docs/screenshots/workspace-ui-2026-09-19');
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const page = await browser.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const shot = name => page.screenshot({path: resolve(out, name + '.png'), fullPage: true});
const click = async selector => {
  await page.waitForSelector(selector, {visible: true});
  await page.click(selector);
};
async function fit() {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow');
}
async function columns() {
  const boxes = await page.evaluate(() => ['#col-settings', '#col-table', '#col-side'].map(selector => {
    const r = document.querySelector(selector).getBoundingClientRect();
    return {x:r.x, y:r.y, right:r.right};
  }));
  assert(boxes[0].right < boxes[1].x && boxes[1].right < boxes[2].x);
  assert(Math.abs(boxes[0].y - boxes[2].y) < 2);
}
async function fresh(width, capacity = '6') {
  await page.setViewport({width, height: 960, isMobile: width < 768, hasTouch: width < 768});
  await page.goto(base + '/app.html');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.waitForSelector('[data-button="6"]');
  await page.select('#tg-capacity', capacity);
}
try {
  await fresh(1440);
  await columns();
  assert.equal(await page.$$eval('#card-grid button:disabled', es => es.length), 52);
  await shot('desktop-setup');
  await click('[data-button="6"]');
  assert(await page.$('#setup-panel.zone-focus'));
  await click('#tg-create');
  await page.waitForSelector('#deck-panel.zone-focus');
  await click('[data-card="As"]');
  assert.equal(await page.$eval('#selection-count', e => e.textContent), '1 / 2');
  assert.equal(await page.$$eval('#hero-cards .hero-card:not(.vacant)', es => es.length), 1);
  assert.equal(await page.$eval('[data-card="As"]', e => e.getAttribute('aria-pressed')), 'true');
  assert.equal(await page.evaluate(() => document.activeElement.dataset.card), 'As');
  await page.keyboard.press('Space');
  assert.equal(await page.$eval('#selection-count', e => e.textContent), '0 / 2');
  await page.keyboard.press('Space');
  await shot('desktop-one-card');
  await click('[data-card="Kh"]');
  await page.waitForSelector('[data-act="fold:3"]:not([disabled])');
  assert(await page.$('#console.zone-focus'));
  assert.equal(await page.$eval('[data-card="As"]', e => getComputedStyle(e).opacity), '1');
  for (const id of [3, 4, 5, 6]) await click(`[data-act="fold:${id}"]:not([disabled])`);
  await page.waitForSelector('#advice .big', {timeout:60000});
  assert(await page.$('#advice-panel.result-focus'));
  await shot('desktop-advice');
  await click('[data-act="call:1"]:not([disabled])');
  await click('[data-act="call:2"]:not([disabled])');
  await click('[data-act="deal-board"]:not([disabled])');
  await click('[data-card="2c"]');
  assert.equal(await page.$$eval('#board-slots .pending', es => es.length), 1);
  assert.equal(await page.$eval('#selection-count', e => e.textContent), '1 / 3');
  await shot('desktop-board-preview');
  await click('[data-act="cancel-board"]');
  assert.equal(await page.$$eval('#board-slots .pending', es => es.length), 0);
  assert(await page.$('#console.zone-focus'));
  for (const width of [1100, 1280, 1440]) {
    await page.setViewport({width, height:960});
    await columns();
    await fit();
  }
  for (const width of [320, 375, 390, 768, 1440]) {
    await fresh(width, '9');
    await fit();
    await shot(`setup-nine-${width}`);
    await click('[data-button="9"]');
    await click('#tg-create');
    await page.waitForSelector('#deck-panel.zone-focus');
    await fit();
    const overlap = await page.evaluate(() => {
      const seats = [...document.querySelectorAll('#seat-layer .seat')].map(e => e.getBoundingClientRect());
      return seats.some((a, i) => seats.slice(i + 1).some(b =>
        Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
        Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1));
    });
    assert(!overlap, `overlapping seats at ${width}`);
    if (width < 768) {
      await page.$eval('[data-card="As"]', e => e.scrollIntoView({block:'center'}));
      await page.tap('[data-card="As"]');
    } else await click('[data-card="As"]');
    assert.equal(await page.$eval('#selection-count', e => e.textContent), '1 / 2');
    await shot(`ready-nine-${width}`);
  }
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion', value:'reduce'}]);
  assert.equal(await page.$eval('.grid-card', e => getComputedStyle(e).transitionDuration), '0s');
  assert.deepEqual(errors, []);
  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed:true, checks:[
    'three desktop columns', 'setup and workflow highlights', 'first-card preview',
    'keyboard selection and stable focus', 'confirmed-card visibility', 'real API advice',
    'public-card preview and cancel', 'nine-seat responsive layouts', 'mobile touch selection',
    'reduced motion', 'no browser errors'
  ]}, null, 2));
  console.log('PASS: workspace layout, card feedback, real advice and responsive touch checks');
} catch (error) {
  await shot('failure');
  await writeFile(resolve(out, 'failure.json'), JSON.stringify({error:error.stack, errors}, null, 2));
  throw error;
} finally {
  await browser.close();
}
