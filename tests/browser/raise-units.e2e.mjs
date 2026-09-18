import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const out = resolve(process.env.POKER_RAISE_OUTPUT || 'docs/screenshots/raise-units-2026-09-19');
await mkdir(out, {recursive:true});
const browser = await puppeteer.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const page = await browser.newPage();
const key = 'paishi_tournament_session_v1';
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const read = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key);
const click = async selector => {await page.waitForSelector(selector, {visible:true}); await page.click(selector);};
const fill = (selector, value) => page.$eval(selector, (e, value) => {
  e.value = value; e.dispatchEvent(new Event('input', {bubbles:true}));
}, String(value));
const inputValue = () => page.$eval('#raise-input', e => e.value);
const shot = name => page.screenshot({path:resolve(out, name + '.png'), fullPage:true});
async function ready(bb = 200, width = 1440) {
  await page.setViewport({width, height:900, isMobile:width < 768, hasTouch:width < 768});
  await page.goto(base + '/app.html');
  await page.evaluate(() => localStorage.clear()); await page.reload();
  await page.waitForSelector('[data-button="6"]');
  await fill('#tg-bb', bb);
  await click('[data-button="6"]'); await click('#tg-create');
  await click('[data-card="As"]:not([disabled])'); await click('[data-card="Kh"]:not([disabled])');
  await page.waitForSelector('[data-act="raise:3"]:not([disabled])');
  await page.$eval('#advanced-panel', e => {e.open = true;});
}
async function undo() {
  await click('[data-act="undo"]:not([disabled])');
  await page.waitForSelector('[data-act="raise:3"]:not([disabled])');
}
async function raised(seat, chips) {
  await click(`[data-act="raise:${seat}"]:not([disabled])`);
  await page.waitForSelector(`[data-act="call:${seat + 1}"]:not([disabled])`);
  assert.equal((await read()).currentHand.ops.at(-1).to, chips);
}
try {
  await ready();
  assert.equal(await inputValue(), '2');
  await fill('#raise-input', '3.5');
  assert((await page.$eval('#raise-conversion', e => e.textContent)).includes('700 筹码'));
  await page.select('#tg-unit-live', 'chips'); assert.equal(await inputValue(), '700');
  await page.select('#tg-unit-live', 'bb'); assert.equal(await inputValue(), '3.5');
  await shot('desktop-bb');
  await raised(3, 700); await undo();
  for (const invalid of ['', '-1', '1', '51']) {
    await fill('#raise-input', invalid); await click('[data-act="raise:3"]');
    assert.equal((await read()).currentHand.ops.length, 0);
    assert(await page.$eval('#raise-error', e => !!e.textContent));
  }
  await fill('#raise-input', '2.333');
  page.once('dialog', d => d.dismiss()); await click('[data-act="raise:3"]');
  assert.equal((await read()).currentHand.ops.length, 0);
  page.once('dialog', d => d.accept()); await raised(3, 467); await undo();
  await page.select('#tg-unit-live', 'chips');
  await fill('#raise-input', '480.5'); await click('[data-act="raise:3"]');
  assert.equal((await read()).currentHand.ops.length, 0);
  await fill('#raise-input', '480'); await raised(3, 480); await undo();
  await page.select('#tg-unit-live', 'bb');
  await page.reload(); await page.waitForSelector('[data-act="raise:3"]:not([disabled])');
  assert.equal(await inputValue(), '2');
  await fill('#raise-input', '50'); await raised(3, 10000);

  await ready(300, 375);
  await page.select('#tg-unit-live', 'chips'); await fill('#raise-input', 700);
  await raised(3, 700);
  await page.select('#tg-unit-live', 'bb');
  assert.equal(await inputValue(), '3.666667');
  await raised(4, 1100);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await shot('mobile-bb');

  await ready();
  for (const seat of [3,4,5,6]) await click(`[data-act="fold:${seat}"]:not([disabled])`);
  await page.waitForSelector('[data-act="raise:1"]:not([disabled])');
  await fill('#raise-input', '3');
  assert((await page.$eval('#raise-conversion', e => e.textContent)).includes('本次再投入 500 筹码'));
  assert((await page.$eval('[data-act="call:1"]', e => e.textContent)).startsWith('跟注 0.5 BB'));
  await click('[data-act="call:1"]:not([disabled])');
  await page.waitForSelector('[data-act="call:2"]:not([disabled])');
  assert.equal((await read()).currentHand.ops.at(-1).type, 'call');
  assert.deepEqual(errors, []);
  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed:true, checks:[
    'BB and chip input', 'draft conversion during unit switch', 'real raise payloads',
    'invalid amounts blocked', 'fractional chip confirmation and cancellation',
    'reload preserves unit', 'all-in limit', 'repeating decimal minimum',
    'mobile layout', 'current-street additional contribution', 'call is independent of raise input'
  ]}, null, 2));
  console.log('PASS: raise units, rounding, bounds, actual API amounts, mobile and call semantics');
} catch (error) {
  await shot('failure');
  await writeFile(resolve(out, 'failure.json'), JSON.stringify({error:error.stack, errors}, null, 2));
  throw error;
} finally { await browser.close(); }
