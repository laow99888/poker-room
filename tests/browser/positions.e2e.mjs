import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve(process.env.POKER_POSITIONS_OUTPUT || 'docs/screenshots/positions-fix-2026-09-19');
const key = 'paishi_tournament_session_v1';
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
let page, stage = 'init';
const results = [];

async function fresh(width) {
  if (page) await page.browserContext().close();
  const context = await browser.createBrowserContext();
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  await page.setViewport({width, height: width < 768 ? 900 : 1000, isMobile: width < 768, hasTouch: width < 768});
  await page.evaluateOnNewDocument(() => {
    window.qa = {errors: [], prepares: []};
    window.addEventListener('error', e => qa.errors.push(e.message));
    window.addEventListener('unhandledrejection', e => qa.errors.push(String(e.reason)));
    const original = window.fetch.bind(window);
    window.fetch = async (url, init) => {
      const response = await original(url, init);
      if (String(url).endsWith('/prepare')) qa.prepares.push({body: JSON.parse(init.body), status: response.status});
      return response;
    };
  });
  await page.goto(base + '/app.html');
  await page.waitForSelector('#tg-create');
}

async function click(selector) {
  const element = await page.waitForSelector(selector, {visible: true});
  if (page.viewport().isMobile) await element.tap(); else await element.click();
}
async function fill(selector, value) {
  await page.$eval(selector, (element, value) => {
    element.value = value;
    element.dispatchEvent(new Event('input', {bubbles: true}));
  }, String(value));
}
const read = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key);
const positions = () => page.evaluate(() => ['pos-btn', 'pos-sb', 'pos-bb'].map(id => {
  const value = document.getElementById(id).value;
  return value === '' ? null : Number(value);
}));
async function waitHand(number) {
  await page.waitForFunction((key, number) => {
    const state = JSON.parse(localStorage.getItem(key));
    return state?.handNumber === number && state.phase === 'ready';
  }, {}, key, number);
}
async function manual() {
  page.once('dialog', dialog => dialog.accept('position acceptance'));
  await click('#manual-close-empty');
  await page.waitForSelector('#settle-commit');
  const s = await read();
  for (const participant of s.currentHand.context.participants) {
    await fill(`.settle-input[data-seat="${participant.seat_id}"]`, participant.starting_chips);
  }
}
async function confirmAndCommit(number) {
  await click('#pos-confirm');
  assert.equal(await page.$eval('#pos-confirm', e => e.checked), true);
  await click('#settle-commit:not([disabled])');
  await waitHand(number);
}
async function screenshot(name) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow');
  await page.screenshot({path: resolve(out, name + '.png'), fullPage: true});
}
async function passed(name) {
  const qa = await page.evaluate(() => window.qa);
  assert.deepEqual(qa.errors, []);
  assert(qa.prepares.every(request => request.status === 200));
  results.push({name, passed: true, prepares: qa.prepares});
  console.log('PASS ' + name);
}

try {
  for (const width of [1440, 375]) {
    for (const capacity of [6, 8, 9]) {
      stage = `departures-${capacity}-${width}`;
      await fresh(width);
      await page.select('#tg-capacity', String(capacity));
      await click(`[data-button="${capacity - 2}"]`);
      await click('#tg-create');
      await waitHand(1);
      await manual();
      await click('#roster-details > summary');
      for (const seat of [capacity - 1, capacity]) {
        const id = (await read()).seats[seat - 1].occupantId;
        page.once('dialog', dialog => dialog.accept());
        await click(`.roster-leave[data-occ="${id}"]`);
      }
      assert.deepEqual(await positions(), [capacity - 1, null, 1]);
      await screenshot(stage + '-dead-button');
      await confirmAndCommit(2);
      await manual();
      assert.deepEqual(await positions(), [capacity - 1, 1, 2]);
      await confirmAndCommit(3);
      await manual();
      assert.deepEqual(await positions(), [1, 2, 3]);
      await confirmAndCommit(4);
      await manual();
      await click('#settle-commit:not([disabled])');
      await waitHand(5);
      const s = await read();
      assert.deepEqual([s.positions.buttonSeatId, s.positions.smallBlindSeatId, s.positions.bigBlindSeatId], [2, 3, 4]);
      assert.equal(s.currentHand.context.participants.length, capacity - 2);
      await passed(stage + ' four consecutive commits return to automatic rotation');
    }
  }

  for (const width of [1440, 375, 320]) {
    stage = `saved-invalid-${width}`;
    await fresh(width);
    // Restore a legacy draft matching the reported bug, then use only visible controls.
    await page.evaluate(async key => {
      const S = await import('/session.js');
      const s = S.createSession({capacity: 6, entries: [1, 2, 3, 4].map(seatId => ({seatId, chips: 10000})),
        heroSeatId: 1, buttonSeatId: 4, sbSeatId: null, bbSeatId: 1,
        blindLevel: {sb: 100, bb: 200, anteEach: 25}});
      S.manualCloseHand(s, 'legacy position draft');
      for (const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft, row.seatId, 10000);
      S.applyDraftPositions(s.nextHandDraft, {buttonSeatId: 3, smallBlindSeatId: 1, bigBlindSeatId: 2, confirmed: true});
      localStorage.setItem(key, JSON.stringify(s));
    }, key);
    await page.reload();
    await page.waitForSelector('#pos-regenerate');
    const before = await read();
    assert.deepEqual(await positions(), [3, 1, 2]);
    assert.equal(await page.$eval('#position-error', e => e.hidden), false);
    assert.equal(await page.$eval('#pos-confirm', e => e.checked), false);
    await click('#pos-confirm');
    assert.equal((await read()).nextHandDraft.positions.confirmed, false);
    await screenshot(stage + '-error');
    await click('#pos-regenerate');
    assert.deepEqual(await positions(), [4, 1, 2]);
    assert.equal(await page.$eval('#position-error', e => e.hidden), true);
    assert.equal(await page.$eval('#pos-confirm', e => e.checked), false);
    assert.deepEqual((await read()).settlementDraft.rows, before.settlementDraft.rows);
    assert.equal((await read()).currentHand.handId, before.currentHand.handId);
    await screenshot(stage + '-recovered');
    await confirmAndCommit(2);
    assert.equal((await read()).positions.buttonSeatId, 4);
    await passed(stage + ' recovery preserves hand and balances');
  }

  stage = 'new-player-and-manual-position';
  await fresh(375);
  await page.evaluate(async key => {
    const S = await import('/session.js');
    const s = S.createSession({capacity: 6, entries: [1, 3, 4, 5].map(seatId => ({seatId, chips: 10000})),
      heroSeatId: 1, buttonSeatId: 5, sbSeatId: 1, bbSeatId: 3,
      blindLevel: {sb: 100, bb: 200, anteEach: 0}});
    S.manualCloseHand(s, 'new player');
    for (const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft, row.seatId, 10000);
    localStorage.setItem(key, JSON.stringify(s));
  }, key);
  await page.reload();
  await page.waitForSelector('#settle-commit');
  await click('#roster-details > summary');
  await fill('.roster-enter-chips[data-seat="2"]', 10000);
  await click('.roster-enter[data-seat="2"]');
  assert.deepEqual(await positions(), [2, 3, 4]);
  await confirmAndCommit(2);
  assert.equal((await read()).currentHand.context.participants.length, 5);
  await manual();
  await click('#roster-details > summary');
  // A legal explicit dead-button calibration must not be replaced on refresh.
  await fill('#pos-btn', 6); await fill('#pos-sb', 1); await fill('#pos-bb', 2);
  await click('#pos-confirm');
  assert.equal((await read()).nextHandDraft.positions.confirmed, true);
  await page.reload();
  await page.waitForSelector('#settle-commit');
  assert.deepEqual(await positions(), [6, 1, 2]);
  await click('#settle-commit:not([disabled])');
  await waitHand(3);
  assert.equal((await read()).positions.buttonSeatId, 6);
  await passed(stage);

  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed: true, results}, null, 2));
} catch (error) {
  if (page && !page.isClosed()) {
    await page.screenshot({path: resolve(out, 'failure-' + stage + '.png'), fullPage: true});
    await writeFile(resolve(out, 'failure-' + stage + '.json'), JSON.stringify({error: error.stack, state: await read()}, null, 2));
  }
  throw error;
} finally {
  await browser.close();
}
