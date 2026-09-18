import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const out = resolve(process.env.POKER_QA_OUTPUT || 'docs/screenshots/session-fix-2026-09-19');
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE, protocolTimeout:30000});
console.log(`BROWSER_PID=${browser.process().pid}`);
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const key = 'paishi_tournament_session_v1';
const read = () => page.evaluate(k => JSON.parse(localStorage.getItem(k)), key);
const shot = name => page.screenshot({path: resolve(out, name + '.png'), fullPage: true});
async function click(selector) { await page.waitForSelector(selector, {visible: true}); await page.click(selector); }
async function fill(selector, value) { await page.$eval(selector, (el, value) => { el.value = value; el.dispatchEvent(new Event('input', {bubbles:true})); }, String(value)); }
async function waitHand(n, phase = 'ready') { await page.waitForFunction((key,n,phase) => { const s = JSON.parse(localStorage.getItem(key)); return s?.handNumber === n && s.phase === phase; }, {}, key,n,phase); }
try {
  await page.setViewport({width:1440,height:1000});
  await page.goto(base + '/app.html');
  await page.waitForSelector('[data-button="6"]');
  await shot('desktop-setup');
  assert.deepEqual(await page.$$eval('#tg-capacity option', es => es.map(e => e.value)), ['6','8','9']);
  await click('[data-button="6"]');
  await click('#tg-create');
  await waitHand(1);
  let s = await read();
  assert.equal(s.currentHand.context.participants.length,6);
  assert(s.currentHand.context.participants.every(p => p.starting_chips === 10000));
  assert.equal(s.currentHand.context.hero_occupant_id, s.seats[0].occupantId);
  await click('[data-card="As"]');
  assert(await page.$('[data-card="As"].picked'));
  await click('[data-card="Ad"]');
  await page.waitForSelector('[data-act="fold:3"]');
  for (const sid of [3,4,5,6,1]) {
    await page.waitForSelector(`[data-act="fold:${sid}"]:not([disabled])`);
    await click(`[data-act="fold:${sid}"]`);
  }
  await waitHand(1,'settling');
  await shot('desktop-settlement');
  await click('#settle-commit');
  await waitHand(2);
  s = await read();
  assert.equal(s.positions.buttonSeatId,1);
  assert.deepEqual(await page.$$eval('#seat-layer .position-code', es => es.map(e => e.textContent)), ['BTN','SB','BB','UTG','HJ','CO']);
  assert.equal(s.occupants[s.heroOccupantId].confirmedChips,9900);
  await page.setViewport({width:390,height:844});
  await shot('mobile-next-hand');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await click('[data-card="As"]');
  await click('[data-card="Ad"]');
  let dealIndex = 0;
  const boards = [['2c','3d','7h'],['9c'],['Td']];
  for (let step = 0; step < 60; step++) {
    await page.waitForFunction(() => document.querySelector('#settle-commit') || document.querySelector('[data-act^="call:"]:not([disabled])') || document.querySelector('[data-act="deal-board"]:not([disabled])'));
    if ((await read()).phase === 'settling') break;
    const call = await page.$('[data-act^="call:"]:not([disabled])');
    const count = (await read()).currentHand.ops.length;
    if (call) await call.click();
    else {
      assert(dealIndex < 3);
      await click('[data-act="deal-board"]:not([disabled])');
      for (const card of boards[dealIndex++]) await click(`[data-card="${card}"]`);
    }
    await page.waitForFunction((key,count) => JSON.parse(localStorage.getItem(key)).currentHand.ops.length > count, {},key,count);
  }
  await waitHand(2,'settling');
  assert.equal(dealIndex,3);
  assert.equal((await read()).settlementDraft.rows.filter(r => r.finalChips === null).length,6);
  assert.equal(await page.$$eval('#board-slots .filled', es => es.length),5);
  assert((await page.$eval('#seat-layer',el => el.textContent)).includes('待核对'));
  await shot('mobile-five-board-unknown');
  for (const sid of [1,2,3,4,5,6]) await fill(`.settle-input[data-seat="${sid}"]`,10000);
  await click('#nextround-panel > summary');
  await fill('#next-sb',200); await fill('#next-bb',400);
  await page.reload();
  await page.waitForSelector('#settle-commit');
  assert.equal((await read()).nextHandDraft.blindLevel.bb,400);
  assert((await read()).settlementDraft.rows.every(r => r.finalChips === 10000));
  await page.evaluate(() => {
    window.savedSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function(k,v) {if(k === 'paishi_tournament_session_v1') throw new DOMException('quota','QuotaExceededError'); return window.savedSetItem.call(this,k,v);};
  });
  await click('#settle-commit');
  await page.waitForFunction(() => document.querySelector('#settle-error').textContent.includes('保存失败'));
  assert.equal((await read()).handNumber,2);
  assert((await page.$eval('#caption-hand', el => el.textContent)).includes('第 2 手'));
  await page.evaluate(() => {Storage.prototype.setItem = window.savedSetItem;});
  await fill('.settle-input[data-seat="3"]',0);
  await fill('.settle-input[data-seat="4"]',20000);
  await click('#pos-confirm');
  await click('#settle-commit');
  await waitHand(3);
  s = await read();
  assert.equal(s.currentHand.context.participants.length,5);
  assert.equal(s.currentHand.context.blinds.bb,400);
  assert(s.currentHand.context.participants.every(p => p.starting_chips > 0));
  await shot('mobile-elimination-next');
  console.log('PASS: core flow, all boards, persistence rollback, elimination');
  // A second real tab changes the saved session; the original tab must stop writing.
  const second = await browser.newPage();
  await second.goto(base + '/app.html');
  await second.evaluate(key => {
    const s = JSON.parse(localStorage.getItem(key)); s.sessionRevision += 1;
    localStorage.setItem(key,JSON.stringify(s));
  },key);
  await page.bringToFront();
  await page.waitForSelector('#conflict-banner:not([hidden])');
  const before = await read();
  await click('[data-card="Ks"]');
  assert.deepEqual(await read(),before);
  await second.close();
  await page.reload();
  await page.waitForSelector('#manual-close-empty');
  page.once('dialog', dialog => dialog.accept('未看到完整牌谱'));
  await click('#manual-close-empty');
  await waitHand(3,'settling');
  s = await read();
  const hero = s.currentHand.context.participants.find(p => p.occupant_id === s.heroOccupantId);
  const others = s.currentHand.context.participants.filter(p => p.occupant_id !== s.heroOccupantId);
  await fill(`.settle-input[data-seat="${hero.seat_id}"]`,0);
  for (const [i,p] of others.entries()) await fill(`.settle-input[data-seat="${p.seat_id}"]`,p.starting_chips + (i === 0 ? hero.starting_chips : 0));
  await click('#settle-commit');
  await waitHand(3,'ended');
  assert.equal((await read()).recentHands.length,3);
  await page.evaluate(key => localStorage.removeItem(key),key);
  await page.reload();
  await page.waitForSelector('#tg-capacity');
  await page.select('#tg-capacity','9');
  await page.select('#tg-unit','chips');
  assert.equal(await page.$eval('#tg-default-chips',el => el.value),'10000');
  await fill('#tg-default-chips',20000);
  await click('.setup-extras > summary');
  await fill('#tg-payouts','500,300,200');
  await click('[data-button="7"]');
  await click('#tg-create');
  await waitHand(1);
  assert((await read()).currentHand.context.participants.every(p => p.starting_chips === 20000));
  let failView = true, failAdvice = true;
  const intercept = async request => {
    if (request.url().endsWith('/api/hand/v2/view') && failView) {failView = false; return request.abort('failed');}
    if (request.url().endsWith('/api/hand/v2/advice') && failAdvice) {failAdvice = false; return request.respond({status:503,contentType:'application/json',body:JSON.stringify({detail:'temporary'})});}
    return request.continue();
  };
  await page.setRequestInterception(true); page.on('request',intercept);
  await click('[data-card="As"]'); await click('[data-card="Ad"]');
  await click('#retry-view');
  await click('#retry-advice');
  await page.waitForSelector('.rec-primary');
  assert(await page.$('#icm-panel:not([hidden])'));
  assert.equal(await page.$$eval('#profile-names input',es => es.length),0);
  const overlaps = await page.evaluate(() => {
    const boxes = [...document.querySelectorAll('#seat-layer .seat')].map(el => el.getBoundingClientRect());
    const bad = [];
    boxes.forEach((a,i) => boxes.slice(i+1).forEach((b,j) => {
      if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1) bad.push([i,i+j+1]);
    })); return bad;
  });
  assert.deepEqual(overlaps,[]);
  await shot('mobile-nine-advice');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.setViewport({width:1440,height:1000});
  await shot('desktop-nine-advice');
  page.off('request',intercept); await page.setRequestInterception(false);
  assert.deepEqual(errors, []);
  await writeFile(resolve(out,'results.json'), JSON.stringify({passed:true, errors},null,2));
  console.log('PASS: setup, folds, all streets, unknown balances, drafts, quota rollback, blinds, elimination, tab conflict, hero ending, 9 seats, unit conversion, network/advice retry, ICM, desktop/mobile');
} catch(e) {
  await shot('failure');
  await writeFile(resolve(out,'failure.json'), JSON.stringify({error:e.stack, errors},null,2));
  throw e;
} finally { await browser.close(); }
