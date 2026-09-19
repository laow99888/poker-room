import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve(process.env.POKER_EDGES_OUTPUT || 'docs/screenshots/deep-1000-2026-09-19/edges');
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const key = 'paishi_tournament_session_v1', results = [];
let page, errors;
const read = () => page.evaluate(k => JSON.parse(localStorage.getItem(k)), key);
async function click(selector) {
  const el = await page.waitForSelector(selector, {visible: true}); await el.tap();
}
async function fill(selector, value) {
  await page.$eval(selector, (el, value) => {el.value = value; el.dispatchEvent(new Event('input', {bubbles: true}));}, String(value));
}
async function create(chips = 10000) {
  if (page) await page.browserContext().close();
  page = await (await browser.createBrowserContext()).newPage(); errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(5000);
  await page.setViewport({width: 320, height: 900, isMobile: true, hasTouch: true});
  await page.goto(base + '/app.html'); await page.select('#tg-unit', 'chips');
  await fill('#tg-default-chips', chips); await fill('#tg-ante', 25);
  await click('[data-button="6"]'); await click('#tg-create'); await ready(1);
}
async function ready(n) {
  await page.waitForFunction((key, n) => {
    const s = JSON.parse(localStorage.getItem(key));
    return s?.handNumber === n && s.phase === 'ready' && !document.querySelector('#layout').inert;
  }, {}, key, n);
}
async function run(name, fn) {
  if (process.env.POKER_EDGES_CASE && process.env.POKER_EDGES_CASE !== name) return;
  try {
    await fn(); assert.deepEqual(errors, []);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    results.push({name, passed: true}); console.log('PASS ' + name);
  } catch (error) {
    results.push({name, passed: false, error: error.stack}); console.log('FAIL ' + name + ': ' + error.message);
    await page.screenshot({path: resolve(out, name + '-failure.png'), fullPage: true});
    await writeFile(resolve(out, name + '-failure.json'), JSON.stringify({error: error.stack, errors, state: await read()}, null, 2));
  }
}
try {
  await run('restore-cleared-estimate', async () => {
    await create(); await click('#quick-fold-review');
    await page.waitForSelector('#settle-commit');
    await fill('.settle-input[data-seat="2"]', '');
    assert.equal(await page.$eval('#settle-commit', el => el.disabled), true);
    await click('#settle-estimated');
    assert.equal((await read()).settlementDraft.rows[1].finalChips, 10000);
    await click('#settle-commit'); await ready(2);
  });
  await run('revive-player-after-replacement', async () => {
    await create(); await click('#quick-fold-review');
    await page.waitForSelector('#settle-commit');
    await fill('.settle-input[data-seat="2"]', 0);
    await fill('.roster-enter-chips[data-seat="2"]', 12000); await click('.roster-enter[data-seat="2"]');
    await fill('.settle-input[data-seat="2"]', 10000);
    assert.equal((await read()).nextHandDraft.rosterEdits.length, 1);
    await click('#roster-reset');
    assert.equal((await read()).nextHandDraft.rosterEdits.length, 0);
    assert.equal((await read()).settlementDraft.rows[1].finalChips, 10000);
    await click('#settle-commit'); await ready(2);
    assert.equal((await read()).currentHand.context.participants.length, 6);
  });
  await run('forced-all-in-cannot-fold', async () => {
    await create(125); const before = await read(); await click('#quick-fold');
    await page.waitForFunction(() => document.querySelector('.quick-fold-actions')?.textContent.includes('不能'));
    assert.deepEqual(await read(), before);
    page.once('dialog', dialog => dialog.accept('actual loss'));
    await click('#manual-close-empty'); await click('#settle-estimated');
    await fill('.settle-input[data-seat="1"]', 0); await click('#settle-commit');
    await page.waitForFunction(key => JSON.parse(localStorage.getItem(key)).phase === 'ended', {}, key);
  });
  for (const channel of ['quick-fold', 'prepare', 'view']) await run('timeout-' + channel, async () => {
    await create(); const before = await read(); let hold = true;
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (hold && request.url().endsWith('/' + channel)) {hold = false; return;}
      return request.continue();
    });
    if (channel === 'view') {await click('[data-card="As"]'); await click('[data-card="Ad"]');}
    else await click('#quick-fold');
    await page.waitForFunction(() => !document.querySelector('#layout').inert
      && document.body.textContent.includes('超时'), {timeout: 18000});
    const failed = await read();
    assert.equal(failed.handNumber, 1);
    assert.equal(failed.phase, channel === 'prepare' ? 'settling' : 'ready');
    if (channel === 'view') {
      assert.deepEqual(failed.currentHand.ops, []);
      await click('#retry-view'); await page.waitForSelector('[data-act="fold:3"]:not([disabled])');
      assert.equal((await read()).handNumber, 1);
      return;
    }
    if (channel === 'quick-fold') assert.deepEqual(failed, before);
    await click(channel === 'prepare' ? '#settle-commit' : '#quick-fold'); await ready(2);
    assert.equal((await read()).occupants[before.heroOccupantId].confirmedChips, 9875);
  });
  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed: results.every(r => r.passed), results}, null, 2));
  if (results.some(r => !r.passed)) process.exitCode = 1;
} finally {await browser.close();}
