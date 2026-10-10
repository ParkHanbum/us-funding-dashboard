import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:8787';
assert(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Use a local Worker.');
const output=process.env.SOX_SCREENSHOT_DIR||await mkdtemp(join(tmpdir(),'funding-sox-overfit-'));
await mkdir(output,{recursive:true});
const actual=await(await fetch(base+'/api/sox')).json();
const research=JSON.parse(await readFile(new URL('../research/sox-factor-search/results.json',import.meta.url),'utf8'));
assert(!JSON.stringify(actual).includes('sox_close'));
for(const kind of ['drawdown','rebound']) {
  const expected=research.targets[kind].full_sample_best_noncalendar_gate;
  assert.equal(actual.overfit.stats[kind].n,expected.n);
  assert(Math.abs(actual.overfit.stats[kind].r-expected.r)<1e-12,'Actual local inputs reproduce the research.');
}
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1400},hasTouch:true});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
  await page.goto(base+'/#sox-research',{waitUntil:'domcontentloaded'});
  const host=page.locator('#sox-research');
  await page.waitForFunction(()=>document.querySelector('#sox-research [data-sx="stat-blue"]')?.textContent==='조건 r=0.921, n=10');
  assert.equal(await host.locator('[data-mode="overfit"]').getAttribute('aria-selected'),'true');
  assert.equal(await host.locator('[data-sx="stat-orange"]').innerText(),'조건 r=0.951, n=10');
  const allCount=Object.values(actual.overfit.context_points).reduce((n,ps)=>n+ps.length,0);
  assert.equal(await host.locator('[data-tip]').count(),allCount);
  assert.equal(await host.locator('[data-in-rule="true"]').count(),20);
  assert.equal(await host.locator('[data-trend]').count(),2);
  assert.equal(await host.locator('[data-sx="view-switch"]').isVisible(),false);
  assert.match(await host.locator('[data-after="drawdown"]').innerText(),/n=0/);
  assert.match(await host.locator('[data-sx="experiment"]').innerText(),/2026-10-08/);
  await host.locator('[data-sx="all-points"]').uncheck();
  assert.equal(await host.locator('[data-tip]').count(),20);
  assert.equal(await host.locator('[data-sx="stat-blue"]').innerText(),'조건 r=0.921, n=10');
  await host.locator('[data-sx="all-points"]').check();
  const p=actual.overfit.points.drawdown.find(p=>actual.overfit.context_points.rebound.some(r=>r.date===p.date));
  const selected=()=>host.locator(`[data-kind="drawdown"][data-date="${p.date}"]`);
  await selected().focus();await selected().press('Enter');
  assert.equal(await host.locator('[data-tip][aria-pressed="true"]').count(),2);
  assert.match(await host.locator('[data-sx="selection-formula"]').innerText(),/낙폭 조건: 해당/);
  assert((await host.locator('[data-sx="selection"]').innerText()).includes(p.x.toFixed(2)+' 십억 달러'));
  await selected().press('Escape');await selected().evaluate(e=>e.blur());
  await host.screenshot({path:join(output,'sox-overfit-desktop.png')});
  const box=await host.boundingBox(),end=await host.locator('[data-sx="sample"]').boundingBox();
  await page.screenshot({path:join(output,'sox-overfit-charts.png'),clip:{x:box.x,y:box.y,width:box.width,height:end.y+end.height+12-box.y}});
  await host.locator('[data-mode="live"]').click();
  assert.equal(await host.locator('[data-tip]').count(),actual.live.points.drawdown.length+actual.live.points.rebound.length);
  await host.locator('[data-mode="reference"]').click();
  assert.equal(await host.locator('[data-tip]').count(),14);
  await host.locator('[data-mode="overfit"]').click();
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(await host.locator('[data-sx="stat-orange"]').isVisible(),true);
  await selected().tap();
  assert.equal(await host.locator('[data-sx="selection"]').isVisible(),true);
  await host.locator('[data-sx="clear-selection"]').click();
  await host.screenshot({path:join(output,'sox-overfit-mobile.png')});

  // Synthetic new confirmations test refresh only. They never enter local D1.
  const fixture=structuredClone(actual);
  await page.clock.install();
  await page.route('**/api/sox',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(fixture)}));
  // Timers must be registered after the virtual clock is installed.
  await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('#sox-research [data-sx="stat-blue"]')?.textContent==='조건 r=0.921, n=10');
  const extra={date:'2026-10-09',x:80,y:5,gate_value:1,selected:true,outcome_through:'2026-10-15',period:'after_selection'};
  fixture.overfit.points.drawdown.push(extra);fixture.overfit.context_points.drawdown.push(extra);
  fixture.overfit.stats.drawdown={n:11,r:.777,slope:.04,intercept:1};
  fixture.overfit.post_selection_stats.drawdown={n:1,r:null,slope:null,intercept:null};
  await page.clock.runFor(300001);
  await page.waitForFunction(()=>document.querySelector('#sox-research [data-sx="stat-blue"]').textContent==='조건 r=0.777, n=11');
  assert.equal(await host.locator('[data-kind="drawdown"][data-date="2026-10-09"]').count(),1);
  assert.match(await host.locator('[data-after="drawdown"]').innerText(),/n=1/);
  assert.match(await host.locator('[data-sx="current-view"]').innerText(),/2026-10-09/);
  assert.match(await host.locator('[data-sx="experiment"]').innerText(),/2026-10-08/);
  // Even a stale payload with overfit values cannot bypass revoked scope.
  fixture.live.personal_use_confirmed=false;fixture.live.analysis_use='disabled';fixture.status.state='LICENSE_REQUIRED';
  await page.clock.runFor(300001);
  await page.waitForFunction(()=>document.querySelector('#sox-research [data-tip]')===null);
  assert.match(await host.locator('[data-sx="stat-blue"]').innerText(),/n=0/);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',actual_selected_points:20,context_points:allCount,
    checks:['research parity','default and fixed conditions','full sample comparison','linked selection','original separation','five-minute refresh','scope revoked','mobile width'],screenshots:output}));
} finally {await browser.close();}
