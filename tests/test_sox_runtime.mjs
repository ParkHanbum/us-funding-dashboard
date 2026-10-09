import assert from 'node:assert/strict';

const base = process.argv[2] || 'http://127.0.0.1:8787';
const target = new URL(base);
assert.ok(target.protocol === 'http:' && ['127.0.0.1','localhost','[::1]'].includes(target.hostname),
  'This test writes fixtures and may only target a local Worker.');
const token = 'local-sox-test-only';
let checks = 0;
const eq = (actual, expected) => { assert.equal(actual, expected); checks++; };
const send = (path, body, authenticated = false, method = body === undefined ? 'GET' : 'POST') => fetch(base + path, {
  method, headers: authenticated ? {authorization:'Bearer ' + token,'content-type':'application/json'} : {},
  body: body === undefined ? undefined : JSON.stringify(body), signal:AbortSignal.timeout(15000)
});

const health = await send('/api/health');
eq(health.status, 200); eq((await health.json()).ok, true);
let pub = await (await send('/api/sox')).json();
eq(pub.reference.points.drawdown.length, 6); eq(pub.reference.points.rebound.length, 8);
eq((await send('/api/sox/state')).status, 401);
eq((await send('/api/sox/ingest', {})).status, 401);
const row = {date:'2026-10-01',model_id:'tga-public-face-residual-v1',input_hash:'a'.repeat(64),
  tga_bn:100,prior_tga_bn:90,net_issuance_bn:5,sox_close:12345};
eq((await send('/api/sox/ingest',{inputs:[{...row,date:'2026-02-30'}]},true)).status,400);
eq((await send('/api/sox/ingest',{inputs:[{...row,tga_bn:'0'}]},true)).status,400);
const malformed = await fetch(base + '/api/sox/ingest', {method:'POST',
  headers:{authorization:'Bearer ' + token},body:'{',signal:AbortSignal.timeout(15000)});
eq(malformed.status,400);
eq((await send('/api/sox/ingest',{inputs:[row]},true)).status,200);
eq((await send('/api/sox/ingest',{inputs:[row]},true)).status,200);
const revised = {...row,input_hash:'b'.repeat(64),sox_close:12346};
eq((await send('/api/sox/ingest',{inputs:[revised]},true)).status,200);
const state = await send('/api/sox/state',undefined,true);
eq(state.status,200);
const inputs = (await state.json()).inputs.filter(r => r.date === row.date);
eq(inputs.length,1); eq(inputs[0].sox_close,12346); eq(inputs[0].input_hash,revised.input_hash);

const derived = {date:row.date,u1_bn:5,u4_bn:null,drawdown_pct:2,rebound_pct:3,
  status4:'complete',status5:'complete',macro_ready:true,observed_sessions:5,sox_close:12346};
const report = {model_id:row.model_id,schema_version:1,public_use_confirmed:false,
  generated_at:'2026-10-08T22:00:00Z',points:{drawdown:[{date:row.date,x:5,y:2}],rebound:[]},
  recent:[derived],latest_macro:derived,data_days:1,analysis_from:row.date,analysis_through:row.date};
eq((await send('/api/sox/ingest',{report,status:{state:'LICENSE_REQUIRED',sox_close:12346}},true)).status,200);
pub = await (await send('/api/sox')).json();
eq(pub.live.points.drawdown.length,0); eq(pub.live.stats.drawdown.n,0);
eq(pub.live.recent[0].sox_close,undefined); eq(pub.live.latest_macro.sox_close,undefined);
eq(pub.live.latest_macro.drawdown_pct,null); eq(pub.status.sox_close,undefined);
eq((await (await send('/api/sox/status')).json()).sox_close,undefined);
eq((await send('/api/sox/ingest',undefined,true)).status,405);
eq((await send('/api/sox/not-real')).status,404);
for (const path of ['/api/summary','/api/settlements','/api/liquidity']) eq((await send(path)).status,200);
eq((await send('/api/treasury-ingest',{})).status,401);
const html = await send('/');
eq(html.status,200); eq((await html.text()).includes('id="sox-research"'),true);
console.log(`Local workerd/D1: ${checks} assertions passed (auth, input revisions, publication guard, existing APIs, assets).`);
