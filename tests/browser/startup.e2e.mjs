import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {default: puppeteer} = await import(process.env.PUPPETEER_MODULE || 'puppeteer');
const base = process.env.POKER_QA_URL || 'http://127.0.0.1:8142';
const out = resolve(process.env.POKER_STARTUP_OUTPUT || 'docs/screenshots/mobile-startup-2026-09-19');
await mkdir(out, {recursive: true});
const browser = await puppeteer.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
console.log(`BROWSER_PID=${browser.process().pid}`);
const page = await browser.newPage(), errors = [], responses = [];
const scenario = process.env.POKER_STARTUP_SCENARIO || 'normal';
page.on('pageerror', e => errors.push(e.message));
page.on('response', r => {
  if (new URL(r.url()).origin === new URL(base).origin) responses.push({path: new URL(r.url()).pathname + new URL(r.url()).search,
    status: r.status(), type: r.headers()['content-type'], cache: r.headers()['cf-cache-status'], age: r.headers().age});
});
page.on('requestfailed', r => errors.push(`${new URL(r.url()).pathname}: ${r.failure()?.errorText}`));
let failure;
let allowModule = false;
try {
  await page.setViewport({width: 375, height: 900, isMobile: true, hasTouch: true});
  if (scenario === 'javascript-disabled') await page.setJavaScriptEnabled(false);
  if (scenario === 'unsupported') await page.evaluateOnNewDocument(() => {window.structuredClone = undefined;});
  if (['old-dependency', 'module-failure', 'syntax-failure', 'bootstrap-failure', 'stalled'].includes(scenario)) {
    const oldSession = (await readFile(new URL('../../frontend/session.js', import.meta.url), 'utf8'))
      .replace('export function calibrateStartingChips', 'function calibrateStartingChips');
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (scenario === 'stalled' && url.pathname.endsWith('/app.js')) return;
      if (scenario === 'module-failure' && !allowModule && url.pathname.endsWith('/app.js')) return request.abort('failed');
      if (scenario === 'bootstrap-failure' && url.pathname.endsWith('/startup.js')) return request.abort('failed');
      if (scenario === 'syntax-failure' && url.pathname.endsWith('/app.js')) {
        return request.respond({status: 200, contentType: 'text/javascript', body: 'export function {'});
      }
      if (scenario === 'old-dependency' && url.pathname.endsWith('/session.js') && !url.search) {
        return request.respond({status: 200, contentType: 'text/javascript', body: oldSession});
      }
      return request.continue();
    });
  }
  await page.goto(base + '/app.html', {waitUntil: scenario === 'stalled' ? [] : 'networkidle0', timeout: 30000});
  if (scenario === 'javascript-disabled') {
    assert(await page.$eval('noscript', el => el.textContent.includes('禁用 JavaScript') && el.getBoundingClientRect().height > 0));
    assert.equal(await page.$eval('#tg-create', el => el.disabled), true);
  } else if (['module-failure', 'syntax-failure', 'unsupported', 'stalled'].includes(scenario)) {
    await page.waitForFunction(() => document.querySelector('#startup-status')?.textContent.includes('失败'), {timeout: 18000});
    assert.equal(await page.$eval('#tg-create', el => el.disabled), true);
    assert(await page.$('#startup-reload'));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    if (scenario === 'module-failure') {
      await page.evaluate(() => localStorage.setItem('startup-qa', 'preserved'));
      allowModule = true;
      await page.tap('#startup-reload');
      await page.waitForFunction(() => document.querySelectorAll('[data-card]').length === 52);
      assert.equal(await page.evaluate(() => localStorage.getItem('startup-qa')), 'preserved');
      assert.equal(await page.$eval('#tg-create', el => el.disabled), false);
    }
  } else {
    await page.waitForFunction(() => document.querySelectorAll('[data-button]').length === 6
      && document.querySelectorAll('[data-card]').length === 52, {timeout: 6000});
    await page.tap('#tg-create');
    assert((await page.$eval('#setup-error', el => el.textContent)).includes('庄位'));
    assert.equal(await page.$$eval('[data-button]', es => es.length), 6);
  }
} catch (e) {failure = e.stack; process.exitCode = 1;}
finally {
  const state = await page.evaluate(() => ({buttons: document.querySelectorAll('[data-button]').length,
    cards: document.querySelectorAll('[data-card]').length, hint: document.querySelector('#setup-error')?.textContent,
    startup: document.querySelector('#startup-message')?.textContent,
    detail: document.querySelector('#startup-detail')?.textContent}));
  const result = {passed: !failure, failure, errors, responses, state};
  await writeFile(resolve(out, 'results.json'), JSON.stringify(result, null, 2));
  await page.screenshot({path: resolve(out, 'startup.png'), fullPage: true});
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
}
