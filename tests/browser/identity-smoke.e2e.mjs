// 只读身份/权益冒烟：不写画像或清空数据，可用于发布验收。
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {default:puppeteer}=await import(process.env.PUPPETEER_MODULE||'puppeteer');
const base=process.env.POKER_QA_URL||'http://127.0.0.1:8142';
const out=resolve(process.env.POKER_IDENTITY_OUTPUT||'docs/screenshots/review-fixes-2026-10-16/identity');
await mkdir(out,{recursive:true});
const browser=await puppeteer.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
try {
  const page=await browser.newPage();
  await page.goto(base+'/app.html');
  const cookies=(await page.cookies()).filter(c=>c.name==='poker_session');
  assert.equal(cookies.length,1);
  assert(cookies[0].httpOnly&&cookies[0].sameSite==='Strict');
  assert.equal(cookies[0].secure,base.startsWith('https://'));
  const result=await page.evaluate(async()=>{
    const summary=await fetch('/api/stats/summary/all');
    const data=await summary.json();
    const payload={hero:['As','Ah'],villains:[{type:'combos',combos:[['Kh','Kd'],['2c','2d']],weights:[1,0]}],board:['Kc','7d','8h','9s','Jc'],iterations:1000,seed:42};
    const r=await fetch('/api/equity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const equity=await r.json();
    return {summaryStatus:summary.status,summaryShape:typeof data.player_samples==='number',cacheControl:summary.headers.get('Cache-Control'),equityStatus:r.status,equity:equity.equity,effectiveSamples:equity.iterations};
  });
  assert.equal(result.summaryStatus,200);assert(result.summaryShape);
  assert.equal(result.cacheControl,'no-store');
  assert.equal(result.equityStatus,200);assert.equal(result.equity,0);assert.equal(result.effectiveSamples,1000);
  result.cookie={httpOnly:cookies[0].httpOnly,secure:cookies[0].secure,sameSite:cookies[0].sameSite};
  await writeFile(resolve(out,'results.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
