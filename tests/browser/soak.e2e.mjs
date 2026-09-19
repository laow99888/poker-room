import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const out = resolve(process.env.POKER_SOAK_OUTPUT || 'docs/screenshots/deep-1000-2026-09-19/soak');
const hands = Number(process.env.POKER_SOAK_HANDS || 1000);
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const key = 'paishi_tournament_session_v1';
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const page = await browser.newPage(), errors = [], checkpoints = [], durations = [], ids = new Set();
const requests = {quickFold: 0, prepare: 0, record: 0};
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (request.url().endsWith('/quick-fold')) requests.quickFold++;
  if (request.url().endsWith('/prepare')) requests.prepare++;
  if (request.url().endsWith('/record')) requests.record++;
});
const read = () => page.evaluate(k => JSON.parse(localStorage.getItem(k)), key);
async function click(selector) {
  const el = await page.waitForSelector(selector, {visible: true});
  if (page.viewport().isMobile) await el.tap(); else await el.click();
}
async function fill(selector, value) {
  await page.$eval(selector, (el, value) => {el.value = value; el.dispatchEvent(new Event('input', {bubbles: true}));}, String(value));
}
async function ready(number) {
  await page.waitForFunction((key, number) => {
    const s = JSON.parse(localStorage.getItem(key));
    return s?.handNumber === number && s.phase === 'ready'
      && !document.querySelector('#layout').inert && !document.querySelector('#quick-fold')?.disabled;
  }, {}, key, number);
}
let completed = 0, expected = 1000000, manual = 0, calibrations = 0, reloads = 0, blindChanges = 0;
const started = Date.now();
try {
  await page.setViewport({width: 375, height: 844, isMobile: true, hasTouch: true});
  await page.goto(base + '/app.html');
  await page.select('#tg-capacity', '9');
  await page.select('#tg-unit', 'chips');
  await fill('#tg-default-chips', expected);
  await fill('#tg-ante', 25);
  await click('[data-button="9"]'); await click('#tg-create'); await ready(1);
  const initial = await read(), hero = initial.heroOccupantId;
  for (let n = 1; n <= hands; n++) {
    let before = await read();
    assert.equal(before.handNumber, n);
    assert(!ids.has(before.currentHand.handId), 'hand ID reused');
    ids.add(before.currentHand.handId);
    // Derive the orbit from the ordinal, independently of the stored positions.
    assert.equal(before.positions.buttonSeatId, (n + 7) % 9 + 1);
    assert.equal(before.positions.smallBlindSeatId, (n - 1) % 9 + 1);
    assert.equal(before.positions.bigBlindSeatId, n % 9 + 1);
    if (n % 25 === 0) {
      await page.$eval('#chips-calibration', el => {el.open = true;});
      await fill('[data-chip-seat="2"]', 1000000 + n);
      await click('[data-calibrate="2"]'); calibrations++;
      before = await read();
      assert.equal(before.currentHand.context.participants.find(p => p.seat_id === 2).starting_chips, 1000000 + n);
    }
    const frozen = structuredClone(before.currentHand.context);
    const start = Date.now();
    if (n % 20 === 0) {
      page.once('dialog', dialog => dialog.accept('QA observed real balance'));
      await click('#manual-close-empty'); await click('#settle-estimated');
      expected += 500;
      await fill('.settle-input[data-seat="1"]', expected);
      if (n % 100 === 0) {
        await page.$eval('#nextround-panel', el => {el.open = true;});
        await fill('#next-sb', before.blindLevel.sb + 10);
        await fill('#next-bb', before.blindLevel.bb + 20); blindChanges++;
      }
      await click('#settle-commit'); manual++;
    } else {
      expected -= before.blindLevel.anteEach;
      if ((n - 1) % 9 === 0) expected -= before.blindLevel.sb;
      if (n % 9 === 0) expected -= before.blindLevel.bb;
      await click('#quick-fold');
    }
    await ready(n + 1); durations.push(Date.now() - start);
    const after = await read(), last = after.recentHands.at(-1);
    assert.equal(after.heroOccupantId, hero);
    assert.equal(after.occupants[hero].confirmedChips, expected, `hero balance at ${n}`);
    assert.equal(after.occupants[hero].chipsEstimated, false);
    assert.equal(after.recentHands.length, Math.min(n, 100));
    assert.equal(last.handNumber, n);
    assert.deepEqual(last.context, frozen);
    assert.equal(last.estimatedOccupantIds.length, 8);
    assert.equal(Boolean(last.quickFold), n % 20 !== 0);
    assert.deepEqual(after.learningJobs, {});
    assert.equal(after.currentHand.context.participants.length, 9);
    for (const p of after.currentHand.context.participants.filter(p => p.occupant_id !== hero)) {
      assert.equal(p.starting_chips, before.currentHand.context.participants.find(old => old.occupant_id === p.occupant_id).starting_chips);
      assert.equal(after.occupants[p.occupant_id].chipsEstimated, true);
    }
    completed = n;
    if (n % 40 === 0) {
      await page.reload(); await ready(n + 1); reloads++;
      assert.deepEqual(await read(), after, `reload changed state at ${n}`);
    }
    if (n % 100 === 0 || n === hands) {
      assert.deepEqual(errors, []);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const metrics = await page.metrics();
      const point = {completed, heroChips: expected, elapsedMs: Date.now() - started,
        storageBytes: Buffer.byteLength(JSON.stringify(after)), nodes: metrics.Nodes, heapBytes: metrics.JSHeapUsedSize,
        last100MeanMs: Math.round(durations.slice(-100).reduce((a, b) => a + b, 0) / Math.min(n, 100))};
      checkpoints.push(point);
      await writeFile(resolve(out, 'progress.json'), JSON.stringify({completed, hands, checkpoints}, null, 2));
      await page.screenshot({path: resolve(out, `hand-${n}.png`), fullPage: true});
      console.log(JSON.stringify(point));
      const mobile = n % 200 === 0;
      await page.setViewport({width: mobile ? 375 : 1440, height: mobile ? 844 : 1000, isMobile: mobile, hasTouch: mobile});
      await ready(n + 1);
    }
  }
  assert.equal(requests.quickFold, hands - manual);
  assert.equal(requests.record, 0);
  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed: true, completed, manual,
    quickFolds: hands - manual, calibrations, reloads, blindChanges, requests, checkpoints, errors,
    finalHeroChips: expected, elapsedMs: Date.now() - started}, null, 2));
  console.log(`PASS ${completed} hands`);
} catch (error) {
  await page.screenshot({path: resolve(out, 'failure.png'), fullPage: true});
  await writeFile(resolve(out, 'failure.json'), JSON.stringify({completed, error: error.stack, errors, requests, state: await read()}, null, 2));
  throw error;
} finally {await browser.close();}
