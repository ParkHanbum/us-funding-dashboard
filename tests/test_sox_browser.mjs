import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:8787';
assert.ok(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Use a local Worker.');
const output = process.env.SOX_SCREENSHOT_DIR || await mkdtemp(join(tmpdir(),'funding-sox-browser-'));
await mkdir(output,{recursive:true});
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:1440,height:1400},deviceScaleFactor:1});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
try {
  // Exercise the permission-pending UI independently of the local data's mode.
  const actual=await (await fetch(base+'/api/sox')).json();
  const pending={...actual,status:{state:'LICENSE_REQUIRED'},live:{...actual.live,
    public_use_confirmed:false,personal_use_confirmed:false,analysis_use:'disabled',
    points:{drawdown:[],rebound:[]},stats:{drawdown:{n:0,r:null},rebound:{n:0,r:null}}
  }};
  await page.route('**/api/sox',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(pending)}));
  await page.goto(base,{waitUntil:'domcontentloaded'});
  const host=page.locator('#sox-research');
  await page.waitForFunction(()=>document.querySelector('#sox-research [data-sx="badge"]').textContent==='SOX 이용 확인 필요');
  assert.equal(await host.locator('[data-mode="live"]').getAttribute('aria-selected'),'true');
  assert.equal(await host.locator('[data-tip]').count(),0);
  assert.match(await host.locator('[data-sx="freshness"]').innerText(),/자동 SOX 차트: 공개 이용 확인 대기/);
  await host.screenshot({path:join(output,'sox-latest-pending.png')});
  await host.locator('[data-mode="reference"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#sox-research [data-tip]').length===14);
  assert.equal(await host.locator('[data-sx="stat-blue"]').innerText(),'r=0.961, n=6');
  assert.equal(await host.locator('[data-sx="stat-orange"]').innerText(),'r=0.915, n=8');
  assert.match(await host.locator('[data-sx="recent"]').innerText(),/SOX 이용 확인 대기/);
  await host.screenshot({path:join(output,'sox-desktop.png')});
  await host.locator('[data-series="rebound"]').uncheck();
  assert.equal(await host.locator('[data-tip]').count(),6);
  await host.locator('[data-series="rebound"]').check();
  assert.equal(await host.locator('[data-tip]').count(),14);
  await host.locator('[data-tip]').first().focus();
  assert.equal(await host.locator('[data-sx="tooltip"]').isVisible(),true);
  await host.locator('[data-mode="live"]').click();
  assert.equal(await host.locator('[data-tip]').count(),0);
  assert.match(await host.locator('[data-sx="empty"]').innerText(),/SOX 공개 이용 확인 대기/);
  await host.locator('[data-mode="reference"]').click();
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(200);
  assert.equal(await host.locator('[data-sx="stat-blue"]').isVisible(),true);
  assert.equal(await host.locator('[data-sx="stat-orange"]').isVisible(),true);
  await host.screenshot({path:join(output,'sox-mobile.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);

  // Synthetic permitted results are intercepted only in this browser context.
  // They never change the Worker, stored data or the public-use config flag.
  await page.unroute('**/api/sox');
  const fixture={...actual,status:{state:'READY'},live:{public_use_confirmed:true,
    points:{drawdown:[{date:'2026-10-01',x:10,y:1},{date:'2026-10-02',x:20,y:2},{date:'2026-10-05',x:30,y:1}],rebound:[]},
    stats:{drawdown:{n:3,r:0.123,slope:0,intercept:1},rebound:{n:0,r:null}},
    latest_macro:{date:'2026-10-07',tga_source_date:'2026-10-06',u1_bn:null,u4_bn:null},
    recent:[{date:'2026-10-07',u1_bn:null,u4_bn:null,macro_ready:false,status4:'pending',status5:'pending',observed_sessions:2,issues:['TGA_INPUT_MISSING']}]
  }};
  await page.clock.install();
  await page.route('**/api/sox',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(fixture)}));
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelectorAll('#sox-research [data-tip]').length===3);
  assert.equal(await host.locator('[data-mode="live"]').getAttribute('aria-selected'),'true');
  assert.equal(await host.locator('[data-tip]').count(),3);
  assert.equal(await host.locator('[data-sx="stat-blue"]').innerText(),'r=0.123, n=3');
  assert.equal(await host.locator('[data-sx="stat-orange"]').innerText(),'r=—, n=0');
  assert.equal(await host.locator('[data-sx="u1"]').innerText(),'—');
  assert.equal(await host.locator('[data-sx="recent"] td').nth(1).innerText(),'—');
  assert.equal(await host.locator('[data-sx="recent"] td').nth(3).innerText(),'2/4일 관측');
  // A new daily result arrives via the existing five-minute refresh. No user
  // tab switch or page reload is needed for points and statistics to change.
  fixture.live.points.drawdown.push({date:'2026-10-06',x:40,y:4});
  fixture.live.stats.drawdown={n:4,r:0.456,slope:0.08,intercept:0.5};
  await page.clock.runFor(300001);
  await page.waitForFunction(()=>document.querySelectorAll('#sox-research [data-tip]').length===4);
  assert.equal(await host.locator('[data-sx="stat-blue"]').innerText(),'r=0.456, n=4');
  assert.match(await host.locator('[data-sx="current-view"]').innerText(),/2026-10-06/);
  assert.match(await host.locator('[data-sx="freshness"]').innerText(),/최신 확정 이벤트 2026-10-06/);
  fixture.status.state='WAITING_SOURCE_UPDATE';
  fixture.live.public_use_confirmed=false;
  fixture.live.personal_use_confirmed=true;
  fixture.live.analysis_use='personal_noncommercial';
  fixture.live.sources={sox:{status:'awaiting_update',observed_through:'2026-10-08',expected_through:'2026-10-09'}};
  await page.clock.runFor(300001);
  await page.waitForFunction(()=>document.querySelector('#sox-research [data-sx="badge"]').textContent==='SOX 종가 발표 대기');
  assert.equal(await host.locator('[data-tip]').count(),4);
  assert.equal(await host.locator('[data-sx="stat-blue"]').innerText(),'r=0.456, n=4');
  assert.ok(!(await host.locator('[data-sx="freshness"]').innerText()).includes('이용 확인 대기'));
  await host.locator('[data-mode="reference"]').click();
  assert.equal(await host.locator('[data-tip]').count(),14);
  assert.equal(await host.locator('[data-sx="stat-blue"]').innerText(),'r=0.961, n=6');
  assert.deepEqual(errors,[]);
  console.log('Chromium: latest default view, automatic point/stat refresh, source-publication wait retaining personal results, original 14 points, separate live sample, toggles, keyboard tooltip, missing values, mobile width and zero page errors passed.');
  console.log('Screenshots:',output);
} finally {
  await browser.close();
}
