import assert from 'node:assert/strict';
import {mkdir, writeFile, mkdtemp} from 'node:fs/promises';
import {resolve, join} from 'node:path';
import {tmpdir} from 'node:os';
const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve(process.env.POKER_ACCEPTANCE_OUTPUT || 'docs/screenshots/full-acceptance-2026-09-19');
await mkdir(out,{recursive:true});
const key='paishi_tournament_session_v1';
const results=[], browsers=[];
let page, stage='init', network=[];
const options={headless:true,executablePath:process.env.BROWSER_EXECUTABLE,protocolTimeout:70000};
async function launch(extra={}) {const b=await puppeteer.launch({...options,...extra});browsers.push(b);console.log(`BROWSER_PID=${b.process().pid}`);return b;}
const browser=await launch();
async function fresh() {
  if(page) await page.browserContext().close();
  const context=await browser.createBrowserContext();page=await context.newPage();
  network=[];page.on('request',r=>network.push(r.url()));
  page.setDefaultTimeout(15000);
  await page.setViewport({width:375,height:812,hasTouch:true,isMobile:true});
  await page.evaluateOnNewDocument(() => {
    window.qa={requests:[],errors:[],gates:{},fail:{},headers:[],releases:{},arrived:{},trainingVisible:false,events:[]};
    for(const type of ['pointerdown','pointerup','click']) document.addEventListener(type,e=>{
      qa.events.push({type,target:e.target.outerHTML.slice(0,250),x:e.clientX,y:e.clientY,scroll:scrollY,time:performance.now()});
      if(qa.events.length>30) qa.events.shift();
    },true);
    new MutationObserver(()=>{
      if([...document.querySelectorAll('#profile-names input, #learning-status')].some(e=>e.getClientRects().length)) qa.trainingVisible=true;
    }).observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','style','class']});
    const real=window.fetch.bind(window);
    window.fetch=async (url,init={}) => {
      const path=String(url), channel=path.split('/').pop();
      const record={path,body:init.body?JSON.parse(init.body):null,headers:init.headers};
      qa.requests.push(record);
      const gate=qa.gates[channel]; if(gate) delete qa.gates[channel];
      const fail=qa.fail[channel]; if(fail) delete qa.fail[channel];
      // Read a real response, then hold JSON independently of AbortSignal.
      const response=fail ? new Response(JSON.stringify({detail:'QA injected failure'}),{status:fail,headers:{'Content-Type':'application/json'}})
        : await real(url, gate ? {...init,signal:undefined} : init);
      if(!gate) return response;
      qa.headers.push(channel);
      const json=response.json.bind(response);
      response.json=async () => {
        const body=await json(); qa.arrived[channel]=body;
        await new Promise(resolve=>{qa.releases[channel]=resolve;});
        return body;
      };
      return response;
    };
    window.addEventListener('error',e=>qa.errors.push(e.message));
    window.addEventListener('unhandledrejection',e=>qa.errors.push(String(e.reason)));
  });
  await page.goto(base+'/app.html');await page.waitForSelector('#tg-create');
}
const read=()=>page.evaluate(k=>JSON.parse(localStorage.getItem(k)),key);
async function click(sel) {const el=await page.waitForSelector(sel,{visible:true});if(page.viewport()?.isMobile) await el.tap();else await el.click();}
async function fill(sel,value) {await page.$eval(sel,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},String(value));}
async function open(sel) {if(!await page.$eval(sel,e=>e.open)) await click(sel+' > summary');}
async function waitState(n,phase) {await page.waitForFunction((k,n,p)=>{const s=JSON.parse(localStorage.getItem(k));return s?.handNumber===n&&s.phase===p;},{},key,n,phase);}
async function shot(name) {await page.screenshot({path:resolve(out,name+'.png'),fullPage:true});}
async function finish(name,extra={}) {assert.deepEqual(await page.evaluate(()=>qa.errors),[]);results.push({name,passed:true,...extra});console.log('PASS '+name);}
async function gate(channel) {await page.evaluate(c=>{qa.gates[c]=true;delete qa.arrived[c];delete qa.releases[c];},channel);}
async function held(channel) {await page.waitForFunction(c=>!!qa.releases[c],{timeout:60000},channel);}
async function release(channel) {await page.evaluate(c=>qa.releases[c](),channel);await page.evaluate(()=>new Promise(r=>setTimeout(r,80)));}
async function fail(channel,status=503) {await page.evaluate((c,s)=>qa.fail[c]=s,channel,status);}
async function count(channel) {return page.evaluate(c=>qa.requests.filter(r=>r.path.endsWith('/'+c)).length,channel);}
async function setup({capacity=6,button=6,seats=null,learning=false,payouts=''}={}) {
  await page.select('#tg-capacity',String(capacity));
  if(learning||payouts) {await open('.setup-extras');if(learning) await click('#tg-learning');if(payouts) await fill('#tg-payouts',payouts);}
  if(seats) {await open('#setup-roster');for(let sid=2;sid<=capacity;sid++) if(!seats.includes(sid)&&await page.$eval(`[data-occupied="${sid}"]`,e=>e.checked)) await click(`[data-occupied="${sid}"]`);}
  await click(`[data-button="${button}"]`);await click('#tg-create');await waitState(1,'ready');
}
async function cards() {await open('#deck-panel');await click('[data-card="As"]');await click('[data-card="Ad"]');await page.waitForSelector('[data-act^="call:"]:not([disabled])');}
async function action(type,seat) {const selector=`[data-act="${type}:${seat}"]:not([disabled])`;await page.waitForSelector(selector,{visible:true});const before=(await read()).currentHand.ops.length;await click(selector);await page.waitForFunction((k,n)=>JSON.parse(localStorage.getItem(k)).currentHand.ops.length===n+1,{},key,before);}
async function folds() {for(const seat of [3,4,5,6,1]) await action('fold',seat);await waitState(1,'settling');}
async function manual() {page.once('dialog',d=>d.accept('现场核对实际余额'));await click((await page.$('#manual-close-empty'))?'#manual-close-empty':'[data-act="manual-close"]');await page.waitForSelector('#settle-commit');}
async function balances(values) {for(const [sid,value] of Object.entries(values)) await fill(`.settle-input[data-seat="${sid}"]`,value);}
async function commit(n) {await click('#settle-commit:not([disabled])');await waitState(n,'ready');}
async function checkLayout(label) {
  if(label.startsWith('playing')) await page.waitForSelector('[data-act^="call:"]:not([disabled])');
  if(label.startsWith('settling')) await page.waitForSelector('#settle-commit');
  const data=await page.evaluate(()=>{
    const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom};};
    const seats=[...document.querySelectorAll(document.querySelector('#layout').dataset.phase==='setup'?'.setup-seat':'#seat-layer .seat')].map(rect);
    const overlap=[];
    seats.forEach((a,i)=>seats.slice(i+1).forEach((b,j)=>{if(Math.min(a.right,b.right)-Math.max(a.x,b.x)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1) overlap.push([i,i+j+1]);}));
    const center=[...document.querySelectorAll('#board-slots .slot, #hero-cards, #pot-num')].filter(e=>e.getClientRects().length).map(rect);
    const centerOverlap=[];
    seats.forEach((a,i)=>center.forEach((b,j)=>{if(Math.min(a.right,b.right)-Math.max(a.x,b.x)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1) centerOverlap.push([i,j]);}));
    return {width:innerWidth,scroll:document.documentElement.scrollWidth,seats,overlap,centerOverlap};
  });
  assert(data.scroll<=data.width,`${label}: document overflow ${JSON.stringify(data)}`);
  assert.deepEqual(data.overlap,[],label+': overlapping seats');
  assert.deepEqual(data.centerOverlap,[],label+': seat overlaps public cards/pot');
  assert(data.seats.every(r=>r.x>=-1&&r.right<=data.width+1),label+': seat out of viewport');
  await shot(label);
}
try {
  stage='learning';await fresh();await setup({learning:true});await cards();await folds();
  const first=await read();await gate('record');await commit(2);await held('record');
  assert.equal(await count('record'),1);
  const req=await page.evaluate(()=>qa.requests.find(r=>r.path.endsWith('/record')));
  assert.equal(req.body.hand_id,first.currentHand.handId);assert.deepEqual(req.body.context,first.currentHand.context);
  assert(req.headers['X-Player-Id']&&req.headers['X-Player-Id']!=='local');
  await release('record');await page.waitForFunction(k=>Object.keys(JSON.parse(localStorage.getItem(k)).learningJobs).length===0,{},key);
  await finish('A04 automatic recording after commit uses original payload and user ID');

  stage='rounding-and-input';await fresh();await fill('#tg-bb',300);await fill('#tg-default-chips','0.333');await click('[data-button="6"]');
  page.once('dialog',d=>d.dismiss());await click('#tg-create');assert.equal(await read(),null);
  assert((await page.$eval('#setup-error',e=>e.textContent)).includes('取消'));
  await fill('#tg-default-chips','0.335');page.once('dialog',d=>d.accept());await click('#tg-create');await waitState(1,'ready');
  assert((await read()).currentHand.context.participants.every(p=>p.starting_chips===101));
  await fresh();await page.select('#tg-unit','chips');await fill('#tg-default-chips',10001);
  for(let i=0;i<3;i++) {await page.select('#tg-unit','bb');await page.select('#tg-unit','chips');assert.equal(await page.$eval('#tg-default-chips',e=>e.value),'10001');}
  await click('[data-button="6"]');
  for(const val of ['',-1,'NaN','Infinity','9007199254740992']) {await fill('#tg-default-chips',val);await click('#tg-create');assert.equal(await read(),null);assert(await page.$eval('#setup-error',e=>!e.hidden));}
  await fill('#tg-default-chips',10000);await open('#setup-roster');await fill('[data-chips="4"]',6000);await click('#tg-create');await waitState(1,'ready');
  assert.deepEqual((await read()).currentHand.context.participants.map(p=>p.starting_chips),[10000,10000,10000,6000,10000,10000]);
  await finish('C02 C03 C04 C18 precision, rounding consent, invalid fields and per-seat chips');

  stage='three-hands';await fresh();await setup();await cards();await folds();
  assert.deepEqual((await read()).settlementDraft.rows.map(r=>r.finalChips),[9900,10100,10000,10000,10000,10000]);
  await gate('prepare');const prepares=await count('prepare');
  await page.$eval('#settle-commit',e=>{e.click();e.click();e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));});await held('prepare');
  const source=await read();await fill('.settle-input[data-seat="1"]',8000);
  assert.deepEqual(await read(),source);assert.equal(await count('prepare'),prepares+1);
  await release('prepare');await waitState(2,'ready');const next=await read();
  await cards();
  for(const [sid,chips] of [[1,'9,900'],[2,'10,000'],[3,'9,800']]) assert((await page.$eval(`#seat-layer [data-seat="${sid}"] .stack`,e=>e.textContent)).includes(chips));
  for(let i=0;i<3;i++) {await page.reload();await page.waitForSelector('[data-act="call:4"]');assert.equal((await read()).currentHand.handId,next.currentHand.handId);assert((await page.$eval('#pot-num',e=>e.textContent)).includes('300'));}
  await manual();await balances({1:9900,2:10100,3:10000,4:10000,5:10000,6:10000});await commit(3);
  assert.equal((await read()).positions.buttonSeatId,2);
  await page.reload();await page.waitForSelector('#manual-close-empty');assert.equal((await read()).handNumber,3);
  assert.equal(network.filter(url=>url.endsWith('/record')||url.includes('/api/stats/')).length,0);
  await finish('B01 S02 S05 C12 C13 L01 L03 A01 three hands, double submit, slow prepare and repeated reload');

  stage='cancel-completed';await fresh();await setup();await cards();await folds();const originalHand=(await read()).currentHand.handId;
  await fill('.settle-input[data-seat="1"]',8000);await click('[data-action="settle-cancel"]');await page.waitForSelector('#return-settlement');
  assert.equal((await read()).occupants[(await read()).heroOccupantId].confirmedChips,10000);
  await click('[data-act="undo"]');await page.waitForSelector('[data-act="call:1"]:not([disabled])');
  assert.equal((await read()).currentHand.ops.length,4);assert.equal((await read()).currentHand.handId,originalHand);await action('call',1);
  assert.equal((await read()).currentHand.ops[4].type,'call');assert.equal((await read()).handNumber,1);
  await finish('S04 U03 cancel completed settlement and correct last action without changing hand or balances');

  stage='f3-showdown';await fresh();await setup();await cards();
  for(const sid of [3,4,5]) await action('fold',sid);await action('call',6);await action('fold',1);await action('call',2);
  const beforeBoard=(await read()).currentHand.ops.length;await fail('view',400);
  await click('[data-act="deal-board"]');for(const c of ['2c','3d','7h']) await click(`[data-card="${c}"]`);
  await page.waitForSelector('#retry-view');assert.equal((await read()).currentHand.ops.length,beforeBoard);
  await click('#retry-view');await page.waitForSelector('[data-act="deal-board"]:not([disabled])');
  for(const [i,board] of [['2c','3d','7h'],['9c'],['Td']].entries()) {
    await click('[data-act="deal-board"]');
    if(i===0) {await click('[data-card="2c"]');await click('[data-act="cancel-board"]');await page.waitForFunction(()=>document.activeElement.dataset.act==='deal-board');await click('[data-act="deal-board"]');}
    for(const c of board) await click(`[data-card="${c}"]`);
    await action('call',2);await action('call',6);
  }
  await waitState(1,'settling');let s=await read();
  assert.deepEqual(s.settlementDraft.rows.map(r=>r.finalChips),[9900,null,10000,10000,10000,null]);
  assert(await page.$eval('#settle-commit',e=>e.disabled));await fill('.settle-input[data-seat="2"]',9800);
  assert(await page.$eval('#settle-commit',e=>e.disabled));await fill('.settle-input[data-seat="6"]',10300);
  await shot('375-f3-settlement');await page.reload();await page.waitForSelector('#settle-commit');await commit(2);
  assert.deepEqual((await read()).currentHand.context.participants.map(p=>p.starting_chips),[9900,9800,10000,10000,10000,10300]);
  await finish('F3 C06 C07 C08 S13 S15 U06 U12 unknown showdown, rejected board and hero folded');

  stage='async-body';await fresh();await setup({button:4});await gate('view');await click('[data-card="As"]');await click('[data-card="Ad"]');await held('view');
  page.once('dialog',d=>d.accept());await click('[data-action="replay-hand"]');const replay=await read();await release('view');
  assert.deepEqual(await read(),replay);assert(await page.$('#manual-close-empty'));
  await gate('advice');await cards();await held('advice');
  assert(await page.$('[data-act="call:1"]:not([disabled])'));const vcount=await count('view');await action('call',1);
  assert.equal(await count('view'),vcount+1);await release('advice');assert.equal(await page.$('.rec-primary'),null);
  await action('call',2);await click('[data-act="undo"]');await page.waitForSelector('[data-act="call:2"]:not([disabled])');await action('fold',2);
  assert.equal((await read()).currentHand.ops[1].type,'fold');
  page.once('dialog',d=>d.accept());await click('[data-action="replay-hand"]');await cards();await page.waitForSelector('.rec-primary',{timeout:60000});
  await fail('advice');await action('call',1);assert.equal(await page.$('.rec-primary'),null);
  await fill('#raise-input',2);await click('[data-act="raise:2"]');await page.waitForSelector('[data-act="call:3"]:not([disabled])');
  for(const sid of [3,4,5,6]) await action('call',sid);
  await page.waitForSelector('#retry-advice');assert.equal(await page.$('.rec-primary'),null);
  assert.equal((await read()).currentHand.ops.length,6);assert(await page.$('[data-act="call:1"]:not([disabled])'));
  await finish('B03 S07 S08 U03 U07 U09 delayed actual JSON, replay, advice isolation and equal-length branch');

  stage='view-double-and-save';await fresh();await setup({button:4});await cards();await page.waitForSelector('.rec-primary',{timeout:60000});
  await gate('view');const beforeCount=await count('view');
  await page.evaluate(()=>{const b=document.querySelector('[data-act="call:1"]');b.click();b.click();document.querySelector('#raise-input')?.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));});await held('view');
  assert.equal(await count('view'),beforeCount+1);await release('view');await page.waitForSelector('[data-act="call:2"]:not([disabled])');assert.equal((await read()).currentHand.ops.length,1);
  await page.evaluate(()=>{window.originalSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='paishi_tournament_session_v1') throw Error('quota');return originalSet.call(this,k,v);};});
  const saved=await read();await click('[data-act="call:2"]');await page.waitForFunction(()=>document.querySelector('#save-state').textContent.includes('保存失败'));
  assert.deepEqual(await read(),saved);await page.reload();await page.waitForSelector('[data-act="call:2"]');assert.deepEqual((await read()).currentHand.ops,saved.currentHand.ops);
  await finish('U08 S12 L04 U10 duplicate actions and unsaved action refresh recovery');

  stage='learning-failure';await fresh();await setup({learning:true});await cards();await folds();await fail('record');await gate('record');const oldHand=(await read()).currentHand.handId;await commit(2);await held('record');
  await cards();await release('record');await page.waitForFunction((k,id)=>JSON.parse(localStorage.getItem(k)).learningJobs[id]?.status==='failed',{},key,oldHand);
  assert(await page.$('[data-act^="call:"]:not([disabled])'));assert.equal((await read()).handNumber,2);
  await open('#advanced-panel');await click('#tg-learning-switch');await manual();const ss=await read();await balances(Object.fromEntries(ss.currentHand.context.participants.map(p=>[p.seat_id,p.starting_chips])));await commit(3);
  const records=await count('record');await page.reload();await page.waitForSelector('#manual-close-empty');assert.equal(await count('record'),0);assert((await read()).learningJobs[oldHand]);assert.equal(records,1);
  await finish('S14 A07 late record failure, current hand operable, disabling pauses old jobs');

  stage='heads-up-icm';await fresh();await setup({seats:[1,2,3],button:3,payouts:'500,300,200'});await cards();await manual();await balances({1:15000,2:0,3:15000});
  assert.equal(await page.$eval('#pos-btn',e=>e.value),'1');assert.equal(await page.$eval('#pos-bb',e=>e.value),'3');
  await open('#nextround-panel');await fill('#next-payouts','500,300');assert.deepEqual((await read()).currentHand.context.icm.payouts,[500,300,200]);await click('#pos-confirm');await commit(2);
  await cards();await page.waitForSelector('[data-act="call:1"]');assert((await page.$eval('#status-line',e=>e.textContent)).includes('100'));assert.deepEqual((await read()).currentHand.context.icm.payouts,[500,300]);
  await action('call',1);await action('call',3);await click('[data-act="deal-board"]');for(const c of ['2c','3d','7h']) await click(`[data-card="${c}"]`);await page.waitForSelector('[data-act="call:3"]');
  await manual();await balances({1:15000,3:15000});await commit(3);assert.equal((await read()).positions.buttonSeatId,3);assert.equal((await read()).positions.bigBlindSeatId,1);
  await finish('B02 P04 P06 P07 A12 three to two, actual blinds and postflop actor, ICM rescope');

  stage='corrupt-legacy';await fresh();
  for(const raw of ['{broken','{"schemaVersion":999}']) {
    await page.evaluate((k,r)=>localStorage.setItem(k,r),key,raw);await page.reload();await page.waitForFunction(()=>document.querySelector('#setup-error').textContent.includes('已保留'));
    assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),raw);
    assert((await page.$$eval('button',es=>es.map(e=>e.textContent))).includes('导出原文本'));
  }
  await page.evaluate(k=>{localStorage.removeItem(k);localStorage.setItem('paishi_config_v2',JSON.stringify({player_count:9,sb:50,bb:100,stack:10001,names:{SB:'Old Friend'}}));},key);
  await page.reload();await page.waitForSelector('#tg-capacity');assert.equal(await page.$eval('#tg-capacity',e=>e.value),'9');
  assert.equal(await page.$eval('#tg-learning',e=>e.checked),false);await click('[data-button="9"]');await click('#tg-create');await waitState(1,'ready');assert.equal((await read()).currentHand.context.participants[0].starting_chips,10001);
  await finish('L05 L06 L07 corrupt preservation and exact legacy prefill with learning off');

  stage='allin-and-adjustment';await fresh();await page.select('#tg-unit','chips');await fill('#tg-default-chips',50);
  await open('#setup-roster');for(const sid of [2,3,4,5,6]) await fill(`[data-chips="${sid}"]`,10000);
  await click('[data-button="6"]');await click('#tg-create');await waitState(1,'ready');await cards();
  assert.equal((await read()).currentHand.context.participants.length,6);assert.equal(await page.$$eval('#seat-layer .seat:not(.empty)',es=>es.length),6);
  assert((await page.$eval('#seat-layer [data-seat="1"]',e=>e.textContent)).includes('全下'));
  await manual();await balances({1:50,2:10000,3:10000,4:10000,5:10000,6:10000});await fill('.settle-input[data-seat="2"]',10100);
  await click('#settle-commit');await page.waitForFunction(()=>document.querySelector('#settle-error').textContent.includes('原因'));
  await fill('#settle-reason','起始筹码现场修正');await click('#settle-accept');await commit(2);
  assert.equal((await read()).currentHand.context.participants.find(p=>p.seat_id===2).starting_chips,10100);
  await finish('M07 C10 P15 short all-in stays seated; actual balance correction requires reason and consent');

  stage='roster-icm';await fresh();await setup({payouts:'500,300,200'});await manual();await balances({1:10000,2:10000,3:10000,4:10000,5:10000,6:10000});
  await open('#roster-details');let roster=await read();const old=roster.seats[3].occupantId;
  page.once('dialog',d=>d.accept());await click(`.roster-leave[data-occ="${old}"]`);
  assert.equal((await read()).nextHandDraft.icm.scope,'off');await fill('.roster-enter-chips[data-seat="4"]',8000);await click('.roster-enter[data-seat="4"]');
  await open('#nextround-panel');await click('#next-icm');await fill('#next-payouts','500,300,200');await click('#pos-confirm');await commit(2);
  roster=await read();assert.notEqual(roster.seats[3].occupantId,old);assert.equal(roster.currentHand.context.participants.reduce((a,p)=>a+p.starting_chips,0),58000);
  assert.equal(Object.values(roster.recentHands[0].finalChipsByOccupant).reduce((a,n)=>a+n,0),60000);
  assert.equal(roster.icm.scope,'final_table');assert.equal(roster.occupants[roster.seats[3].occupantId].profileName,null);
  await finish('M04 C11 A06 A13 player replacement separates transfer chips from hand result and reconfirms ICM');

  stage='icm-delayed-restore';await fresh();await setup({payouts:'300,200,100'});await cards();await manual();await balances({1:10000,2:10000,3:10000,4:10000,5:10000,6:10000});
  // Hold a real restore response while editing the next-hand draft.
  await page.evaluateOnNewDocument(()=>{qa.gates.view=true;});await page.reload();await held('view');await open('#nextround-panel');await fill('#next-payouts','500,300,200');
  await release('view');assert.deepEqual((await read()).currentHand.context.icm.payouts,[300,200,100]);
  assert.deepEqual((await read()).nextHandDraft.icm.payouts,[500,300,200]);
  assert.equal(await page.$eval('#next-payouts',e=>e.value),'500,300,200');await commit(2);assert.deepEqual((await read()).currentHand.context.icm.payouts,[500,300,200]);
  await finish('S10 S11 ICM 600 to 1000 during delayed restore remains a next-hand setting');

  stage='picker-and-advice';await fresh();await setup({button:4});await gate('advice');await cards();await held('advice');await action('call',1);
  for(const sid of [2,3,4,5,6]) await action('call',sid);
  await click('[data-act="deal-board"]');await click('[data-card="2c"]');await page.focus('[data-card="3d"]');
  const scroll=await page.evaluate(()=>scrollY);await release('advice');
  assert(await page.$('[data-card="2c"].picked'));assert.equal(await page.evaluate(()=>document.activeElement.dataset.card),'3d');assert(Math.abs(await page.evaluate(()=>scrollY)-scroll)<2);
  await click('[data-act="cancel-board"]');await page.waitForFunction(()=>document.activeElement.dataset.act==='deal-board');
  await finish('U06 stale advice during board selection preserves cards, focus, scroll and cancel');

  stage='save-advice-status';await fresh();await setup({button:4});
  await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='paishi_tournament_session_v1') throw Error('quota');return original.call(this,k,v);};});
  await cards();await page.waitForSelector('.rec-primary',{timeout:60000});assert((await page.$eval('#save-state',e=>e.textContent)).includes('保存失败'));
  await page.reload();await page.waitForSelector('#manual-close-empty');await gate('advice');await cards();await held('advice');
  assert((await page.$eval('#save-state',e=>e.textContent)).includes('已保存'));assert((await page.$eval('#advice',e=>e.textContent)).includes('推演'));
  await release('advice');await finish('U10 persistence and advice statuses remain independent in both directions');

  stage='responsive';const widths=[320,375,390,768,1440];
  for(const capacity of [6,9,2]) {
    await fresh();const physical=capacity===2?6:capacity;
    for(const width of widths) {
      await page.setViewport({width,height:width<768?812:1000,isMobile:width<768,hasTouch:width<768});
      await page.waitForSelector('#tg-capacity');await page.select('#tg-capacity',String(physical));
      if(capacity===2) {await open('#setup-roster');for(const sid of [2,4,5,6]) if(await page.$eval(`[data-occupied="${sid}"]`,e=>e.checked)) await click(`[data-occupied="${sid}"]`);}
      await checkLayout(`setup-${capacity}-${width}`);
    }
    await page.setViewport({width:375,height:812,isMobile:true,hasTouch:true});
    await setup({capacity:physical,button:capacity===2?1:physical,seats:capacity===2?[1,3]:null});await cards();
    assert.equal(await page.$eval('#deck-panel',e=>e.open),false);
    assert.equal(await page.$eval('#profile-names',e=>e.hidden),true);
    assert.equal(await page.evaluate(()=>qa.trainingVisible),false);
    for(let i=0;i<12;i++) {await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('#profile-names, #learning-status')),false);}
    for(const width of widths) {await page.setViewport({width,height:width<768?812:1000,isMobile:width<768,hasTouch:width<768});await checkLayout(`playing-${capacity}-${width}`);}
    await manual();const state=await read();await balances(Object.fromEntries(state.currentHand.context.participants.map(p=>[p.seat_id,p.starting_chips])));
    for(const width of widths) {
      await page.setViewport({width,height:width<768?812:1000,isMobile:width<768,hasTouch:width<768});await checkLayout(`settling-${capacity}-${width}`);
      await click('.settle-input[data-seat="1"]');await page.keyboard.press('End');await page.keyboard.type('1');
      assert.equal(await page.evaluate(()=>document.activeElement.dataset.seat),'1');await page.keyboard.press('Backspace');
      assert((await page.$eval('#seat-layer [data-seat="1"]',e=>e.textContent)).includes('10,000'));
    }
  }
  await finish('B04 U01 U02 U05 U11 layout and input focus: 45 viewport/state/roster combinations',{physicalKeyboardOnly:true});

  stage='browser-restart';const profile=await mkdtemp(join(tmpdir(),'poker-acceptance-profile-'));console.log('QA_PROFILE='+profile);
  const persistent=await launch({userDataDir:profile});let pp=await persistent.newPage();await pp.goto(base+'/app.html');
  const keep=await read();await pp.evaluate((k,s)=>{localStorage.setItem(k,JSON.stringify(s));localStorage.setItem('paishi_uid','qa-restart-user');},key,keep);await persistent.close();
  const restarted=await launch({userDataDir:profile});pp=await restarted.newPage();await pp.goto(base+'/app.html');await pp.waitForSelector('#settle-commit');
  assert.deepEqual(await pp.evaluate(k=>JSON.parse(localStorage.getItem(k)),key),keep);assert.equal(await pp.evaluate(()=>localStorage.getItem('paishi_uid')),'qa-restart-user');await restarted.close();
  await finish('L10 full Chromium restart preserves session and identity',{profile});
  await writeFile(resolve(out,'results.json'),JSON.stringify({passed:true,results},null,2));
} catch(e) {
  if(page&&!page.isClosed()) {await shot('failure-'+stage);await writeFile(resolve(out,'failure-'+stage+'.json'),JSON.stringify({error:e.stack,state:await read(),runtime:await page.evaluate(()=>({requests:qa.requests,errors:qa.errors,events:qa.events}))},null,2));}
  throw e;
} finally {for(const b of browsers) await b.close();}
