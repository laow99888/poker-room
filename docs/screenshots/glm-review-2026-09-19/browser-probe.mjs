import puppeteer from 'file:///C:/Users/Administrator/.codex/skills/chrome-devtools/scripts/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const out = path.dirname(fileURLToPath(import.meta.url));
const browser = await puppeteer.launch({headless:true});
console.log('BROWSER_PID=' + browser.process().pid);
const results = {errors: [], responses: [], scenarios: {}};
const context = await browser.createBrowserContext();
const page = await context.newPage();
await page.setViewport({width:1440,height:1000});
page.on('pageerror', e => results.errors.push(String(e)));
page.on('response', async r => {
  if(r.url().includes('/api/')) {
    const row = {url:r.url(),status:r.status()};
    if(!r.ok()) row.body = await r.text().catch(()=> 'unreadable');
    results.responses.push(row);
  }
});
page.on('dialog', d => d.accept(d.type()==='prompt' ? 'audit manual close' : undefined));
const value = async (selector, text) => { await page.click(selector, {clickCount:3}); await page.keyboard.press('Backspace'); await page.type(selector, String(text)); await page.keyboard.press('Tab'); };
const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem('paishi_tournament_session_v1')));
const settle = async () => {
  await page.waitForFunction(()=>JSON.parse(localStorage.getItem('paishi_tournament_session_v1'))?.phase==='settling',{timeout:20000});
};
async function setup(chips=10000, previewEdit=false) {
  await page.goto('http://127.0.0.1:8141/',{waitUntil:'networkidle0'});
  await page.evaluate(()=>localStorage.clear());
  await page.reload({waitUntil:'networkidle0'});
  await value('#tg-default-chips',chips);
  for(let seat=1;seat<=6;seat++) await page.click(`[data-seat="${seat}"] [data-role="occ"]`);
  for(const [seat,role] of [[5,'hero'],[6,'btn'],[1,'sb'],[2,'bb']]) await page.click(`[data-seat="${seat}"] [data-role="${role}"]`);
  results.scenarios.setupVisibility = await page.evaluate(()=>({unit:document.querySelector('#tg-unit').value, visibleNames:[...document.querySelectorAll('.tg-name')].filter(e=>e.getClientRects().length).length}));
  await page.click('#tg-create');
  await page.waitForSelector('#tg-confirm:not([hidden])');
  if(previewEdit) await value('[data-seat="1"] [data-role="chips"]',20000);
  await page.click('#tg-confirm');
  await page.waitForFunction(()=>!!localStorage.getItem('paishi_tournament_session_v1'));
}
async function cards() {
  if(!await page.$eval('#deck-panel',e=>e.open)) await page.click('#deck-panel summary');
  await page.click('[data-card="As"]');
  await page.click('[data-card="Ad"]');
  await page.waitForSelector('[data-act^="fold:"]:not([disabled])',{timeout:20000});
}
async function foldToEnd() {
  for(const seat of [3,4,5,6,1]) {
    await page.waitForSelector(`[data-act="fold:${seat}"]:not([disabled])`,{timeout:30000});
    await page.click(`[data-act="fold:${seat}"]`);
  }
  await settle();
}
try {
  await setup(50);
  let s=await stored();
  results.scenarios.bbInput = {selected:'BB', typed:50, actualChips:s.currentHand.context.participants.map(p=>p.starting_chips), expected:10000};
  await setup(10000,true);
  s=await stored();
  results.scenarios.staleSetupPreview={editedChips:20000, savedChips:s.currentHand.context.participants.find(p=>p.seat_id===1).starting_chips};
  await cards();
  await foldToEnd();
  results.scenarios.normalSettlement=(await stored()).settlementDraft;
  await page.screenshot({path:path.join(out,'desktop-settlement.png'),fullPage:true});
  results.scenarios.nextSettings = await page.$eval('#nextround-panel',e=>e.innerText);
  await page.click('[data-action="confirm-current"]');
  await page.click('#settle-commit');
  await page.waitForFunction(()=>JSON.parse(localStorage.getItem('paishi_tournament_session_v1'))?.handNumber===2,{timeout:20000});
  results.scenarios.normalNext={hand:(await stored()).handNumber, caption:await page.$eval('#caption-hand',e=>e.textContent)};
  await page.setViewport({width:375,height:812});
  await page.screenshot({path:path.join(out,'mobile-next.png'),fullPage:true});
  await cards();
  await page.click('[data-act="manual-close"]');
  await settle();
  s=await stored();
  results.scenarios.manualCloseDraft={nextHandDraft:s.nextHandDraft};
  const nameInput=await page.$('.settle-input');
  await nameInput.click();
  await page.keyboard.type('10000');
  await page.keyboard.press('Tab');
  await page.reload({waitUntil:'networkidle0'});
  results.scenarios.manualCloseRestore={phase:(await stored()).phase, errors:results.errors.slice(), console:await page.$eval('#status-line',e=>e.textContent)};
  await page.setViewport({width:1440,height:1000});
  await setup();
  await cards();
  await foldToEnd();
  await value('.settle-input[data-seat="1"]',0);
  await value('.settle-input[data-seat="2"]',20000);
  await page.click('[data-action="confirm-current"]');
  const prepareDone=page.waitForResponse(r=>r.url().endsWith('/api/table/prepare'));
  await page.click('#settle-commit');
  await prepareDone;
  await page.waitForFunction(()=>document.querySelector('#settle-error')?.textContent);
  results.scenarios.elimination={phase:(await stored()).phase, error:await page.$eval('#settle-error',e=>e.textContent)};
  await page.screenshot({path:path.join(out,'elimination-blocked.png'),fullPage:true});
  await setup();
  await cards();
  for(const [seat,action] of [[3,'fold'],[4,'fold'],[5,'fold'],[6,'call'],[1,'fold'],[2,'call']]) {
    await page.waitForSelector(`[data-act="${action}:${seat}"]:not([disabled])`,{timeout:30000});
    await page.click(`[data-act="${action}:${seat}"]`);
  }
  await page.waitForFunction(()=>JSON.parse(localStorage.getItem('paishi_tournament_session_v1')).currentHand.ops.length===6 && !document.querySelector('[data-act="deal-board"]').disabled);
  results.scenarios.flopBeforeDeal={button:await page.$eval('[data-act="deal-board"]',e=>e.textContent), status:await page.$eval('#status-line',e=>e.textContent)};
  await page.click('[data-act="deal-board"]');
  const boardResponse=page.waitForResponse(r=>r.url().endsWith('/api/hand/v2/view'));
  await page.click('[data-card="2c"]');
  results.scenarios.flopResponse={status:(await boardResponse).status(), body:await (await boardResponse).text()};
  await page.waitForFunction(()=>document.querySelector('[data-act="fold:null"]:not([disabled])'));
  results.scenarios.flopAfterOneCard={status:await page.$eval('#status-line',e=>e.textContent),tip:await page.$eval('#deck-tip',e=>e.textContent),ops:(await stored()).currentHand.ops};
  await page.screenshot({path:path.join(out,'flop-blocked.png'),fullPage:true});
} catch(e) {
  results.failure=String(e.stack);
  await page.screenshot({path:path.join(out,'probe-failure.png'),fullPage:true}).catch(()=>{});
} finally {
  await fs.writeFile(path.join(out,'browser-evidence.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify(results,null,2));
  await browser.close();
}
