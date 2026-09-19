import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const out = resolve(process.env.POKER_LAYOUT_OUTPUT || 'docs/screenshots/positions-fix-2026-09-19/table-layout');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const page = await browser.newPage();
const results = [], errors = [];
page.on('pageerror', error => errors.push(error.message));
let name = 'init';
try {
  for (const capacity of [6, 8, 9]) {
    for (const width of [320, 375, 390, 768, 1440]) {
      for (const phase of ['playing', 'settling']) {
        name = `${capacity}-${width}-${phase}`;
        await page.setViewport({width, height: 1000, isMobile: width < 768, hasTouch: width < 768});
        await page.goto(base + '/app.html');
        await page.evaluate(async ({capacity, phase}) => {
          const S = await import('/session.js');
          const s = S.createSession({capacity, entries: Array.from({length: capacity}, (_, i) => ({seatId: i + 1, chips: 10000, estimated: i !== 0})),
            heroSeatId: 1, buttonSeatId: capacity, sbSeatId: 1, bbSeatId: 2,
            blindLevel: {sb: 100, bb: 200, anteEach: 0}});
          S.setHeroCards(s, ['As', 'Ad']);
          if (phase === 'settling') {
            S.manualCloseHand(s, 'layout acceptance');
            for (const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft, row.seatId, 10000);
          }
          localStorage.setItem('paishi_tournament_session_v1', JSON.stringify(s));
        }, {capacity, phase});
        await page.reload();
        await page.waitForSelector(phase === 'playing' ? '[data-act="call:3"]:not([disabled])' : '#settle-commit');
        await page.evaluate(() => document.fonts.ready);
        const geometry = await page.evaluate(() => {
          const rect = element => {
            const r = element.getBoundingClientRect();
            return {x: r.x, y: r.y, right: r.right, bottom: r.bottom};
          };
          const seats = [...document.querySelectorAll('#seat-layer .seat')].map(rect);
          const center = [...document.querySelectorAll('#board-slots .slot, #hero-cards, #pot-num')].map(rect);
          const intersects = (a, b) => Math.min(a.right, b.right) - Math.max(a.x, b.x) > 1
            && Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y) > 1;
          const overlaps = [], seatOverlaps = [];
          seats.forEach((seat, i) => {
            center.forEach((item, j) => {if (intersects(seat, item)) overlaps.push([i, j]);});
            seats.slice(i + 1).forEach((other, j) => {if (intersects(seat, other)) seatOverlaps.push([i, i + j + 1]);});
          });
          return {seats, center, overlaps, seatOverlaps, width: innerWidth, scrollWidth: document.documentElement.scrollWidth};
        });
        results.push({name, ...geometry});
        assert.deepEqual(geometry.overlaps, [], name + ': seats overlap public cards/pot');
        assert.deepEqual(geometry.seatOverlaps, [], name + ': seats overlap each other');
        assert(geometry.scrollWidth <= geometry.width, name + ': document overflows');
        if (width === 320 || width === 1440) await page.screenshot({path: resolve(out, name + '.png'), fullPage: true});
      }
    }
  }
  assert.deepEqual(errors, []);
  await writeFile(resolve(out, 'results.json'), JSON.stringify({passed: true, results}, null, 2));
  console.log(`PASS ${results.length} table layouts`);
} catch (error) {
  await page.screenshot({path: resolve(out, 'failure-' + name + '.png'), fullPage: true});
  await writeFile(resolve(out, 'failure-' + name + '.json'), JSON.stringify({error: error.stack, results, errors}, null, 2));
  throw error;
} finally { await browser.close(); }
