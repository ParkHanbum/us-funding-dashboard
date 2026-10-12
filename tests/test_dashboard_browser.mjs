import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';

const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base=(process.argv[2] || 'http://127.0.0.1:8787').replace(/\/$/,'');
const target=new URL(base);
assert.ok(['127.0.0.1','localhost','[::1]'].includes(target.hostname) ||
  target.origin==='https://us-funding-dashboard.nb2sy.workers.dev','Use this dashboard or a local Worker.');
const endpoints=['health','summary','settlements','liquidity'];
const responses=await Promise.all(endpoints.map(async path=>{
  const response=await fetch(`${base}/api/${path}`,{signal:AbortSignal.timeout(30000)});
  assert.equal(response.status,200,`${path} is available`);
  return response.json();
}));
const [health,summary,settlements,liquidity]=responses;
assert.equal(health.ok,true);
const output=process.env.DASHBOARD_SCREENSHOT_DIR;
if(output) await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1200},deviceScaleFactor:1});
const errors=[],requests=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>requests.push(new URL(request.url()).pathname));
try {
  // Old bookmarks still load the complete funding dashboard after the section is removed.
  const response=await page.goto(base+'/#sox-research',{waitUntil:'domcontentloaded'});
  assert.equal(response.status(),200);
  await page.waitForFunction(()=>{
    const status=document.getElementById('status').textContent;
    return status!=='Loading' &&
      !document.getElementById('settlements').textContent.includes('Loading') &&
      !document.getElementById('liquidityRows').textContent.includes('Loading');
  });
  assert.equal(await page.locator('h1').innerText(),'Funding Stress Dashboard');
  assert.equal(await page.locator('#status').innerText(),summary.classification || 'NEUTRAL');
  assert.equal(await page.locator('.cards article').count(),8);
  for(const [id,key] of [['sofr','SOFR'],['effr','EFFR'],['iorb','IORB']]) {
    const value=summary.metrics?.[key]?.value;
    assert.equal(await page.locator('#'+id).innerText(),value==null?'—':`${Number(value).toFixed(2)}%`);
  }
  assert.deepEqual(await page.locator('h2').allTextContents(),[
    'Funding spreads','TGA changes','Upcoming Treasury settlements',
    '5-business-day liquidity heatmap','v3 status'
  ]);
  const settlementDates=new Set((settlements.days || [])
    .filter(day=>day.rows?.length).map(day=>day.settlementDate));
  assert.equal(await page.locator('.settlement-day').count(),settlementDates.size);
  assert.equal(await page.locator('#liquidityRows tr').count(),Math.max(1,liquidity.days?.length || 0));
  assert.equal(await page.locator('#sox-research').count(),0);
  assert.equal(await page.locator('script[src*="sox"],link[href*="sox"]').count(),0);
  assert.ok(!requests.some(path=>path.startsWith('/api/sox') || path.includes('sox-panel')),
    'The dashboard must not fetch removed chart assets or SOX reports.');
  assert.ok(!(await page.locator('main').innerText()).includes('SOX'));
  if(output) await page.screenshot({path:join(output,'dashboard-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.equal(await page.locator('#liquidityRows').isVisible(),true);
  if(output) await page.screenshot({path:join(output,'dashboard-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'passed',origin:target.origin,core_endpoints:endpoints,
    settlement_days:settlementDates.size,liquidity_days:liquidity.days?.length || 0,
    sox_section:false,sox_requests:0,page_errors:0,mobile_width:390,screenshots:output || null}));
} finally {await browser.close();}
