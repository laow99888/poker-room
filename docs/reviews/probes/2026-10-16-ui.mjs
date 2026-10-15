// Isolated UI reproductions; no production access or user browser profile.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve('docs/screenshots/review-2026-10-16');
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const key = 'paishi_tournament_session_v1';
const results = [];
let page;
async function fresh(width = 390) {
  if (page) await page.browserContext().close();
  page = await (await browser.createBrowserContext()).newPage();
  await page.setViewport({width, height: 900, isMobile: width < 768, hasTouch: width < 768});
  page.setDefaultTimeout(8000);
  await page.goto(base + '/app.html');
  await page.waitForFunction(() => !document.querySelector('#tg-create').disabled);
}
const read = () => page.evaluate(k => JSON.parse(localStorage.getItem(k)), key);
async function create() {
  await page.click('[data-button="6"]');
  await page.click('#tg-create');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.phase === 'ready', {}, key);
}
async function capture(name, detail) {
  results.push({name, reproduced: true, detail});
  await page.screenshot({path: resolve(out, name + '.png'), fullPage: true});
  console.log(JSON.stringify({name, detail}));
}
try {
  // A single completed hand followed by an unused next-hand placeholder.
  await fresh(); await create(); await page.click('#quick-fold');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.handNumber === 2
    && !document.querySelector('#layout').inert, {}, key);
  await page.$eval('#advanced-panel', el => {el.open = true;});
  page.once('dialog', d => d.accept());
  await page.click('#tg-end-session');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.phase === 'ended', {}, key);
  let s = await read();
  assert.equal(s.recentHands.length, 1);
  await capture('end-count-off-by-one', {completed: s.recentHands.length, displayed: await page.$eval('#session-status', el => el.textContent)});

  // Ending from the reconciliation screen discards the entered actual balance.
  await fresh(); await create(); await page.click('#quick-fold-review');
  await page.waitForSelector('.settle-input[data-seat="1"]');
  await page.$eval('.settle-input[data-seat="1"]', el => {
    el.value = '9000'; el.dispatchEvent(new Event('input', {bubbles:true}));
  });
  s = await read();
  assert.equal(s.settlementDraft.rows.find(r => r.occupantId === s.heroOccupantId).finalChips, 9000);
  await page.$eval('#advanced-panel', el => {el.open = true;});
  page.once('dialog', d => d.accept());
  await page.click('#tg-end-session');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.phase === 'ended', {}, key);
  s = await read();
  await capture('end-discards-real-balance', {enteredActual:9000, persistedFinal:s.occupants[s.heroOccupantId].confirmedChips,
    settlementDraft:s.settlementDraft, completed:s.recentHands.length,
    displayed:await page.$eval('#session-status', el => el.textContent)});

  // The advice channel has no deadline; a stalled request stays pending beyond view's 15s deadline.
  await fresh(); await create();
  let intercepted = false;
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (request.url().endsWith('/api/hand/v2/advice')) {intercepted = true; return;}
    request.continue();
  });
  await page.click('[data-card="As"]'); await page.click('[data-card="Ad"]');
  for (const seat of [3,4,5,6]) {
    await page.waitForSelector(`[data-act="fold:${seat}"]:not([disabled])`);
    await page.click(`[data-act="fold:${seat}"]`);
  }
  await page.waitForFunction(() => document.querySelector('#advice-panel').getAttribute('aria-busy') === 'true');
  await new Promise(resolve => setTimeout(resolve, 16500));
  assert(intercepted);
  await capture('advice-stall-no-retry', {waitedMs:16500, busy:await page.$eval('#advice-panel', el => el.getAttribute('aria-busy')),
    text:await page.$eval('#advice', el => el.textContent), retryButton:!!await page.$('#retry-advice')});

  // Restoring an unfinished hand replays view while the settlement can be submitted.
  // Let view complete during prepare; then fail the write. The failure path must
  // release both committing and the DOM inert state, but currently only the former is released.
  await fresh(); await create();
  await page.click('[data-card="As"]'); await page.click('[data-card="Ad"]');
  await page.waitForSelector('[data-act="manual-close"]');
  page.once('dialog', d => d.accept('现场结束，核对余额'));
  await page.click('[data-act="manual-close"]');
  await page.waitForSelector('.settle-input[data-seat="1"]');
  await page.$eval('.settle-input[data-seat="1"]', el => {
    el.value = '10000'; el.dispatchEvent(new Event('input', {bubbles:true}));
  });
  await page.click('#settle-estimated');
  let heldView, heldPrepare;
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (request.url().endsWith('/api/hand/v2/view')) {heldView = request; return;}
    if (request.url().endsWith('/api/table/prepare')) {heldPrepare = request; return;}
    request.continue();
  });
  await page.reload();
  await page.waitForSelector('#settle-commit');
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(k, v) {
      if (k === 'paishi_tournament_session_v1') throw new DOMException('quota', 'QuotaExceededError');
      return original.call(this, k, v);
    };
  });
  await page.click('#settle-commit');
  for (let i = 0; i < 100 && (!heldView || !heldPrepare); i++) await new Promise(r => setTimeout(r, 20));
  assert(heldView && heldPrepare, 'both real requests must be in flight');
  await heldView.continue();
  await page.waitForFunction(() => document.querySelector('#layout').inert);
  await heldPrepare.continue();
  await page.waitForFunction(() => document.querySelector('#settle-error').textContent.includes('保存失败'));
  await new Promise(r => setTimeout(r, 200));
  assert.equal(await page.$eval('#layout', el => el.inert), true);
  assert.equal(await page.$eval('#settle-commit', el => el.disabled), false);
  await capture('settlement-failure-leaves-page-inert', {
    layoutInert:await page.$eval('#layout', el => el.inert),
    headerInert:await page.$eval('#bar-main-slot', el => el.inert),
    retryButtonDisabled:await page.$eval('#settle-commit', el => el.disabled),
    error:await page.$eval('#settle-error', el => el.textContent)});
} finally {
  await writeFile(resolve(out, 'ui-results.json'), JSON.stringify({results}, null, 2));
  await browser.close();
}
