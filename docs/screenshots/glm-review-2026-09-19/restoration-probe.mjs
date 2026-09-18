import puppeteer from 'file:///C:/Users/Administrator/.codex/skills/chrome-devtools/scripts/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const out=path.dirname(fileURLToPath(import.meta.url));
const browser=await puppeteer.launch({headless:true});
console.log('BROWSER_PID='+browser.process().pid);
const page=await browser.newPage();
const result={scope:'Restore a synthetic F3 session through real storage, API and UI. This is not a click-through of the blocked flop entry.',errors:[]};
page.on('pageerror',e=>result.errors.push(String(e)));
try {
  await page.setViewport({width:1440,height:1000});
  await page.goto('http://127.0.0.1:8141/',{waitUntil:'networkidle0'});
  await page.evaluate(async()=>{
    const S=await import('/session.js');
    const s=S.createSession({capacity:6,entries:Array.from({length:6},(_,i)=>({seatId:i+1,chips:10000})),heroSeatId:5,buttonSeatId:6,sbSeatId:1,bbSeatId:2,blindLevel:{sb:100,bb:200,anteEach:0},learningEnabled:false,icm:{scope:'off',payouts:[]}});
    S.setHeroCards(s,['As','Ad']); s.phase='playing';
    const action=(seat_id,type)=>({op:'action',seat_id,type});
    const ops=[action(3,'fold'),action(4,'fold'),action(5,'fold'),action(6,'call'),action(1,'fold'),action(2,'check')];
    for(const cards of [['2c','3d','7h'],['9c'],['Td']]) ops.push({op:'board',cards},action(2,'check'),action(6,'check'));
    for(const op of ops) S.pushOp(s,op);
    localStorage.setItem('paishi_tournament_session_v1',JSON.stringify(s));
  });
  await page.reload({waitUntil:'networkidle0'});
  await page.waitForSelector('.settle-input');
  result.unknownRows=await page.evaluate(()=>{
    const s=JSON.parse(localStorage.getItem('paishi_tournament_session_v1'));
    return s.settlementDraft.rows.filter(r=>r.finalChips===null).map(r=>({seat:r.seatId,source:r.source,confirmed:r.confirmed,uiSource:document.querySelector(`tr[data-seat="${r.seatId}"] .src-tag`).textContent,displayedSeatStack:document.querySelector(`.seat[data-seat="${r.seatId}"] .stack`).textContent.trim()}));
  });
  await page.screenshot({path:path.join(out,'unknown-settlement.png'),fullPage:true});
  result.hiddenLearningNames=await page.$$eval('.tg-name',els=>els.filter(e=>e.getClientRects().length).length);
} catch(e){result.failure=String(e.stack);}
finally{
 await fs.writeFile(path.join(out,'restoration-evidence.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify(result,null,2));
 await browser.close();
}
