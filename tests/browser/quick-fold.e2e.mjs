import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve(process.env.POKER_QUICK_OUTPUT || 'docs/screenshots/quick-fold-2026-09-19/quick');
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const key = 'paishi_tournament_session_v1', results = [];
let page, stage = 'init';
async function fresh(width = 375) {
  if (page) await page.browserContext().close();
  page = await (await browser.createBrowserContext()).newPage();
  await page.setViewport({width, height: 900, isMobile: width < 768, hasTouch: width < 768});
  await page.evaluateOnNewDocument(() => {
    window.qa = {requests: [], errors: [], fail: {}, gates: {}, release: {}};
    window.addEventListener('error', e => qa.errors.push(e.message));
    window.addEventListener('unhandledrejection', e => qa.errors.push(String(e.reason)));
    const fetchReal = window.fetch.bind(window);
    window.fetch = async (url, init) => {
      const channel = String(url).split('/').pop();
      qa.requests.push({channel, body: JSON.parse(init.body)});
      if (qa.fail[channel]) {delete qa.fail[channel]; return new Response('{"detail":"QA network failure"}', {status: 503});}
      const response = await fetchReal(url, init);
      if (qa.gates[channel]) {
        delete qa.gates[channel]; const json = response.json.bind(response);
        response.json = async () => {const body = await json(); await new Promise(resolve => {qa.release[channel] = resolve;}); return body;};
      }
      return response;
    };
  });
  await page.goto(base + '/app.html');
  await page.waitForSelector('#tg-create');
}
async function click(selector) {
  const el = await page.waitForSelector(selector, {visible: true});
  if (page.viewport().isMobile) await el.tap(); else await el.click();
}
async function fill(selector, value) {
  await page.$eval(selector, (e, v) => {e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));}, String(value));
}
const read = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key);
async function ready(n) {
  await page.waitForFunction((key, n) => {
    const s = JSON.parse(localStorage.getItem(key));
    return s?.handNumber === n && s.phase === 'ready' && !document.querySelector('#layout').inert
      && !document.querySelector('#quick-fold')?.disabled;
  }, {}, key, n);
}
async function create(capacity = 6, button = capacity) {
  await page.select('#tg-capacity', String(capacity));
  await fill('#tg-ante', 25);
  await click(`[data-button="${button}"]`); await click('#tg-create'); await ready(1);
}
async function finish(name) {
  assert.deepEqual(await page.evaluate(() => qa.errors), []);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  results.push({name, passed: true}); console.log('PASS ' + name);
}
try {
  for (const width of [1440, 375]) for (const capacity of [6, 8, 9]) {
    stage = `orbit-${capacity}-${width}`;
    await fresh(width); await create(capacity);
    const initial = await read();
    for (let i = 0; i < capacity; i++) {await click('#quick-fold'); await ready(i + 2);}
    const s = await read();
    assert.equal(s.occupants[s.heroOccupantId].confirmedChips, 10000 - 300 - capacity * 25);
    assert.equal(s.heroOccupantId, initial.heroOccupantId);
    assert.equal(s.positions.smallBlindSeatId, 1);
    assert.equal(s.recentHands.length, capacity);
    assert(s.recentHands.every(h => h.quickFold && h.estimatedOccupantIds.length === capacity - 1));
    assert(s.currentHand.context.participants.filter(p => p.occupant_id !== s.heroOccupantId).every(p => p.starting_chips === 10000));
    assert.equal(await page.evaluate(() => qa.requests.filter(r => ['advice', 'view', 'record'].includes(r.channel)).length), 0);
    await page.reload(); await ready(capacity + 1);
    assert.deepEqual(await read(), s);
    await page.screenshot({path: resolve(out, stage + '.png'), fullPage: true});
    await finish(stage);
  }

  stage = 'calibrate-one-seat-and-review';
  await fresh(320); await create();
  await click('#quick-fold'); await ready(2);
  await click('#chips-calibration > summary'); await fill('[data-chip-seat="2"]', 12000); await click('[data-calibrate="2"]');
  let s = await read();
  assert.equal(s.currentHand.context.participants[1].starting_chips, 12000);
  assert.equal(s.occupants[s.seats[1].occupantId].chipsEstimated, false);
  assert.equal(s.recentHands[0].finalChipsByOccupant[s.seats[1].occupantId], 10000);
  await click('#quick-fold-review');
  await page.waitForSelector('#settle-commit:not([disabled])');
  await fill('.settle-input[data-seat="3"]', 11000);
  assert.equal(await page.$eval('#settle-diff-row', e => e.hidden), true);
  await page.screenshot({path: resolve(out, 'optional-calibration-320.png'), fullPage: true});
  await click('#settle-commit'); await ready(3);
  s = await read(); assert.equal(s.occupants[s.seats[2].occupantId].confirmedChips, 11000);
  assert.equal(s.occupants[s.seats[2].occupantId].chipsEstimated, false);
  await finish(stage);

  stage = 'network-double-click-and-save-failure';
  await fresh(); await create();
  const source = await read();
  await page.evaluate(() => {qa.fail['quick-fold'] = true;});
  await click('#quick-fold');
  await page.waitForFunction(() => document.querySelector('.quick-fold-actions').textContent.includes('QA network failure'));
  assert.deepEqual(await read(), source);
  await page.evaluate(() => {qa.gates['quick-fold'] = true;});
  await click('#quick-fold');
  await page.waitForFunction(() => qa.release['quick-fold']);
  await page.evaluate(() => document.querySelector('#quick-fold').click());
  assert.equal(await page.evaluate(() => qa.requests.filter(r => r.channel === 'quick-fold').length), 2);
  await page.evaluate(() => {qa.fail.prepare = true; qa.release['quick-fold']();});
  await page.waitForFunction(() => document.querySelector('#settle-error')?.textContent.includes('QA network failure'));
  s = await read(); assert.equal(s.handNumber, 1); assert.equal(s.phase, 'settling');
  assert.equal(s.settlementDraft.rows[0].finalChips, 9875);
  await page.reload(); await page.waitForSelector('#settle-commit:not([disabled])');
  await page.evaluate(() => {
    const original = Storage.prototype.setItem; window.restoreStorage = () => {Storage.prototype.setItem = original;};
    Storage.prototype.setItem = function(k, v) {
      if (k === 'paishi_tournament_session_v1' && JSON.parse(v).handNumber === 2) throw new Error('QA quota');
      return original.call(this, k, v);
    };
  });
  await click('#settle-commit');
  await page.waitForFunction(() => document.querySelector('#settle-error')?.textContent.includes('保存失败'));
  assert.equal((await read()).handNumber, 1);
  await page.evaluate(() => restoreStorage());
  await click('#settle-commit'); await ready(2);
  assert.equal((await read()).occupants[source.heroOccupantId].confirmedChips, 9875);
  await finish(stage);

  stage = 'post-call-fold-and-already-folded';
  for (const folded of [false, true]) {
    await fresh(); await create(6, 4);
    await click('[data-card="As"]'); await click('[data-card="Ad"]');
    await click('[data-act="call:1"]:not([disabled])');
    await page.waitForSelector('[data-act="raise:2"]:not([disabled])');
    await fill('#raise-input', 3); await click('[data-act="raise:2"]');
    for (const seat of [3, 4, 5]) await click(`[data-act="fold:${seat}"]:not([disabled])`);
    await click('[data-act="call:6"]:not([disabled])');
    await page.waitForSelector('[data-act="fold:1"]:not([disabled])');
    if (folded) {await click('[data-act="fold:1"]'); await page.waitForSelector('[data-act="deal-board"]:not([disabled])');}
    await click('#quick-fold:not([disabled])'); await ready(2);
    s = await read(); assert.equal(s.occupants[s.heroOccupantId].confirmedChips, 9775);
    assert.equal(s.recentHands[0].recordQuality, 'manual_close');
  }
  await finish(stage);

  stage = 'rapid-second-tap-and-calibration-during-guard';
  await fresh(); await create(); await click('#quick-fold');
  await page.waitForFunction(key => JSON.parse(localStorage.getItem(key)).handNumber === 2, {}, key);
  assert.equal(await page.$eval('#quick-fold', e => e.disabled), true);
  await page.evaluate(() => document.querySelector('#quick-fold').click());
  await page.$eval('#chips-calibration', e => {e.open = true;});
  await fill('[data-chip-seat="2"]', 12000);
  await page.$eval('[data-calibrate="2"]', e => e.click());
  await ready(2);
  assert.equal((await read()).currentHand.context.participants[1].starting_chips, 12000);
  await finish(stage);

  stage = 'manual-unknown-result-estimates-only-opponents';
  await fresh(); await create();
  page.once('dialog', dialog => dialog.accept('unknown results'));
  await click('#manual-close-empty'); await page.waitForSelector('#settle-estimated');
  await click('#settle-estimated');
  assert.equal(await page.$eval('#settle-commit', e => e.disabled), true);
  await fill('.settle-input[data-seat="1"]', 13000);
  await click('#settle-commit'); await ready(2);
  s = await read(); assert.equal(s.occupants[s.heroOccupantId].confirmedChips, 13000);
  assert.equal(s.recentHands[0].estimatedOccupantIds.length, 5);
  await finish(stage);

  stage = 'late-quick-response-after-tab-conflict';
  await fresh(); await create();
  await page.evaluate(() => {qa.gates['quick-fold'] = true;}); await click('#quick-fold');
  await page.waitForFunction(() => qa.release['quick-fold']);
  await page.evaluate(key => {
    const oldValue = localStorage.getItem(key), s = JSON.parse(oldValue); s.sessionRevision++;
    const newValue = JSON.stringify(s); localStorage.setItem(key, newValue);
    window.dispatchEvent(new StorageEvent('storage', {key, oldValue, newValue, storageArea: localStorage}));
    qa.release['quick-fold']();
  }, key);
  await page.waitForFunction(() => !document.querySelector('#layout').inert);
  s = await read(); assert.equal(s.handNumber, 1); assert.equal(s.phase, 'ready'); assert.equal(s.recentHands.length, 0);
  assert.equal(await page.$eval('#conflict-banner', e => e.hidden), false);
  await finish(stage);
  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed: true, results}, null, 2));
} catch (error) {
  if (page) {
    await page.screenshot({path: resolve(out, 'failure-' + stage + '.png'), fullPage: true});
    await writeFile(resolve(out, 'failure-' + stage + '.json'), JSON.stringify({error: error.stack, state: await read(), qa: await page.evaluate(() => qa)}, null, 2));
  }
  throw error;
} finally { await browser.close(); }
