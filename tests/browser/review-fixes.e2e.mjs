import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve(process.env.POKER_REVIEW_OUTPUT || 'docs/screenshots/review-fixes-2026-10-16');
await mkdir(out, {recursive:true});
const browser = await puppeteer.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const key = 'paishi_tournament_session_v1', results = [], errors = [];
let page, stage = '';
const read = () => page.evaluate(k => JSON.parse(localStorage.getItem(k)), key);
async function fresh() {
  if (page) await page.browserContext().close();
  page = await (await browser.createBrowserContext()).newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({width:390,height:844,isMobile:true,hasTouch:true});
  await page.goto(base + '/app.html');
  await page.waitForFunction(() => !document.querySelector('#tg-create').disabled);
  await page.click('[data-button="6"]'); await page.click('#tg-create');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.phase === 'ready', {}, key);
}
async function end() {
  await page.$eval('#advanced-panel', e => {e.open = true;});
  page.once('dialog', d => d.accept());
  await page.click('#tg-end-session');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.phase === 'ended'
    && !document.querySelector('#layout').inert, {}, key);
}
async function pass(name) {
  results.push({name,pass:true});
  await page.screenshot({path:resolve(out,name+'.png'),fullPage:true});
  console.log('PASS '+name);
}
async function fill(selector, value) {
  await page.$eval(selector,(e,v)=>{e.value=String(v);e.dispatchEvent(new Event('input',{bubbles:true}));},value);
}
try {
  stage = 'empty-and-completed-count';
  await fresh(); await end();
  assert.match(await page.$eval('#session-status', e => e.textContent), /共 0 手/);
  await fresh(); await page.click('#quick-fold');
  await page.waitForFunction(k => JSON.parse(localStorage.getItem(k))?.handNumber === 2 && !document.querySelector('#layout').inert, {}, key);
  await end();
  assert.equal((await read()).completedHands,1);
  assert.match(await page.$eval('#session-status', e => e.textContent), /共 1 手/);
  await pass(stage);

  stage = 'settle-and-end-preserves-actual';
  await fresh(); await page.click('#quick-fold-review');
  await page.waitForSelector('.settle-input[data-seat="1"]');
  await fill('.settle-input[data-seat="1"]',9000);
  await page.evaluate(()=>{
    window.savedSetItem=Storage.prototype.setItem;
    Storage.prototype.setItem=function(k,v){if(k==='paishi_tournament_session_v1')throw new DOMException('quota','QuotaExceededError');return window.savedSetItem.call(this,k,v);};
  });
  await page.$eval('#advanced-panel',e=>{e.open=true;});
  page.once('dialog',d=>d.accept()); await page.click('#tg-end-session');
  await page.waitForFunction(()=>document.querySelector('#settle-error').textContent.includes('保存失败')&&!document.querySelector('#layout').inert);
  assert.equal((await read()).phase,'settling');
  assert.equal((await read()).settlementDraft.rows.find(r=>r.seatId===1).finalChips,9000);
  await page.evaluate(()=>{Storage.prototype.setItem=window.savedSetItem;});
  await end();
  let s = await read();
  assert.equal(s.occupants[s.heroOccupantId].confirmedChips,9000);
  assert.equal(s.recentHands[0].finalChipsByOccupant[s.heroOccupantId],9000);
  assert.equal(s.completedHands,1);
  assert.equal(s.currentHand,null);
  await page.reload();
  await page.waitForSelector('#session-status');
  assert.match(await page.$eval('#session-status', e => e.textContent), /9,000/);
  await pass(stage);

  stage = 'advice-cancel-timeout-and-retry';
  await fresh();
  let requests = 0;
  await page.setRequestInterception(true);
  page.on('request', r => {if(r.url().endsWith('/api/hand/v2/advice')) {requests++;return;} r.continue();});
  await page.evaluate(() => {
    const original = window.setTimeout;
    window.setTimeout = (fn,ms,...args) => original(fn,ms === 120000 ? 1200 : ms,...args);
  });
  await page.click('[data-card="As"]'); await page.click('[data-card="Ad"]');
  for (const seat of [3,4,5,6]) {
    await page.waitForSelector(`[data-act="fold:${seat}"]:not([disabled])`);
    await page.click(`[data-act="fold:${seat}"]`);
  }
  await page.waitForSelector('#cancel-advice'); await page.click('#cancel-advice');
  await page.waitForSelector('#retry-advice');
  assert.equal(await page.$eval('#advice-panel',e=>e.getAttribute('aria-busy')),'false');
  await page.click('#retry-advice');
  await page.waitForSelector('#retry-advice');
  assert(requests >= 2);
  assert.equal(await page.$eval('#advice-panel',e=>e.getAttribute('aria-busy')),'false');
  await pass(stage);

  stage = 'late-view-and-quota-failure-unlock';
  await fresh();
  await page.click('[data-card="As"]'); await page.click('[data-card="Ad"]');
  await page.waitForSelector('[data-act="manual-close"]');
  page.once('dialog',d=>d.accept('现场结束，核对余额'));
  await page.click('[data-act="manual-close"]');
  await page.waitForSelector('.settle-input[data-seat="1"]');
  await fill('.settle-input[data-seat="1"]',10000); await page.click('#settle-estimated');
  let heldView, heldPrepare;
  await page.setRequestInterception(true);
  page.on('request',r=>{
    if(r.url().endsWith('/api/hand/v2/view')) {heldView=r;return;}
    if(r.url().endsWith('/api/table/prepare')) {heldPrepare=r;return;}
    r.continue();
  });
  await page.reload(); await page.waitForSelector('#settle-commit');
  await page.evaluate(()=>{
    window.savedSetItem=Storage.prototype.setItem;
    Storage.prototype.setItem=function(k,v){if(k==='paishi_tournament_session_v1')throw new DOMException('quota','QuotaExceededError');return window.savedSetItem.call(this,k,v);};
  });
  await page.click('#settle-commit');
  for(let i=0;i<100&&(!heldView||!heldPrepare);i++)await new Promise(r=>setTimeout(r,20));
  assert(heldView&&heldPrepare);
  await heldView.continue(); await page.waitForFunction(()=>document.querySelector('#layout').inert);
  await heldPrepare.continue();
  await page.waitForFunction(()=>document.querySelector('#settle-error').textContent.includes('保存失败')&&!document.querySelector('#layout').inert);
  assert.equal(await page.$eval('#bar-main-slot',e=>e.inert),false);
  assert.equal((await read()).handNumber,1);
  await page.$eval('#advanced-panel',e=>{e.open=true;});
  await page.evaluate(()=>{
    window.reviewExports=0;
    const original=HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click=function(){if(this.download&&this.href.startsWith('blob:'))window.reviewExports++;return original.call(this);};
  });
  await page.click('#tg-export');
  assert.equal(await page.evaluate(()=>window.reviewExports),1);
  await page.evaluate(()=>{Storage.prototype.setItem=window.savedSetItem;});
  await page.setRequestInterception(false);
  await fill('.settle-input[data-seat="1"]',9900);
  await page.click('#settle-commit');
  await page.waitForFunction(k=>JSON.parse(localStorage.getItem(k))?.handNumber===2&&!document.querySelector('#layout').inert,{},key);
  s=await read(); assert.equal(s.occupants[s.heroOccupantId].confirmedChips,9900);
  assert.equal(s.completedHands,1);
  await pass(stage);
  assert.deepEqual(errors,[]);
} catch(e) {
  await writeFile(resolve(out,'failure.json'),JSON.stringify({stage,error:String(e),errors},null,2));
  if(page&&!page.isClosed()) {
    try {await page.screenshot({path:resolve(out,'failure.png'),fullPage:true});} catch { /* 保留原始导航/断连错误 */ }
  }
  throw e;
} finally {
  await writeFile(resolve(out,'results.json'),JSON.stringify({results,errors},null,2));
  await browser.close();
}
