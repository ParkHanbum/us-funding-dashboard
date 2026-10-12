import assert from 'node:assert/strict';
import {handleSox} from '../src/sox.js';

// Minimal D1 contract mock. SQL integration is checked separately against SQLite.
const data = {inputs:[],reports:new Map(),auctions:[]};
class Statement {
  constructor(sql){this.sql=sql;this.args=[];}
  bind(...args){this.args=args;return this;}
  async first(){return data.reports.has(this.args[0])?{payload_json:data.reports.get(this.args[0])}:null;}
  async all(){return {results:this.sql.includes('sox_daily_inputs')?data.inputs:this.sql.includes('FROM auctions')?data.auctions:[]};}
}
const env={TREASURY_INGEST_TOKEN:'test-only-secret-not-for-deployment',DB:{prepare:s=>new Statement(s),batch:async stmts=>{
  for(const s of stmts)if(s.sql.includes('INTO sox_reports'))data.reports.set(s.args[0],s.args[1]);return stmts.map(()=>({success:true}));
}}};
const req=(p,body,auth=false)=>new Request('https://test.invalid'+p,{method:body===undefined?'GET':'POST',headers:auth?{authorization:'Bearer '+env.TREASURY_INGEST_TOKEN,'content-type':'application/json'}:{},body:body===undefined?undefined:JSON.stringify(body)});
assert.equal(await handleSox(req('/api/summary'),env),null);
assert.equal((await handleSox(req('/api/sox/state'),env)).status,401);
assert.equal((await handleSox(req('/api/sox/ingest',{}),env)).status,401);
assert.equal((await handleSox(req('/api/sox/ingest',{},true),env)).status,400);
assert.equal((await handleSox(req('/api/sox/ingest',{inputs:[{date:'2026-02-30'}]},true),env)).status,400);
const original=await (await handleSox(req('/api/sox'),env)).json();
assert.equal(original.reference.points.drawdown.length,6);assert.equal(original.reference.points.rebound.length,8);
assert.equal(original.status.state,'WAITING_FIRST_RUN');
const report={model_id:'tga-public-face-residual-v1',schema_version:1,public_use_confirmed:false,
  points:{drawdown:[{date:'2026-09-22',x:20,y:1}],rebound:[]},recent:[],sox_close:12345,raw_data:'SECRET'};
assert.equal((await handleSox(req('/api/sox/ingest',{report,status:{state:'LICENSE_REQUIRED'}},true),env)).status,200);
const pub=await (await handleSox(req('/api/sox'),env)).json();
assert.equal(pub.live.points.drawdown.length,0);assert.equal(pub.live.sox_close,undefined);assert.equal(pub.live.raw_data,undefined);
assert.equal('overfit' in pub,false);
assert.equal((await handleSox(req('/api/sox/state',undefined,true),env)).status,200);
assert.equal((await handleSox(req('/api/sox/not-real'),env)).status,404);

const privateDerived={date:'2026-10-01',u1_bn:10,u4_bn:15,drawdown_pct:2,rebound_pct:3,
  status4:'complete',status5:'complete',sox_close:12345,raw_data:'PRIVATE_CLOSES'};
const nestedReport={...report,latest_macro:privateDerived,recent:[privateDerived],
  sources:{sox:{status:'ok',series:'NASDAQSOX',sox_close:12345}},
  stats:{drawdown:{n:3,r:0.5,slope:1,intercept:0,sox_close:12345}},
  points:{drawdown:[{date:'2026-10-01',x:10,y:2,sox_close:12345}],rebound:[]}};
await handleSox(req('/api/sox/ingest',{report:nestedReport,status:{state:'LICENSE_REQUIRED',sox_close:12345}},true),env);
let nested=await (await handleSox(req('/api/sox'),env)).json();
assert.equal(nested.live.recent[0].sox_close,undefined);
assert.equal(nested.live.latest_macro.sox_close,undefined);
assert.equal(nested.live.latest_macro.drawdown_pct,null);
assert.equal(nested.live.latest_macro.rebound_pct,null);
assert.equal(nested.live.sources.sox.sox_close,undefined);
assert.equal(nested.status.sox_close,undefined);
assert.equal((await (await handleSox(req('/api/sox/status'),env)).json()).sox_close,undefined);

// Authorized display is a fixture only; it does not edit config/sox-model.json.
await handleSox(req('/api/sox/ingest',{report:{...nestedReport,public_use_confirmed:true}},true),env);
nested=await (await handleSox(req('/api/sox'),env)).json();
assert.equal(nested.live.points.drawdown[0].sox_close,undefined);
assert.equal(nested.live.stats.drawdown.sox_close,undefined);
assert.equal(nested.live.latest_macro.drawdown_pct,2);

// Explicit personal/noncommercial analysis is distinct from a public-use license.
const personalReport={...nestedReport,public_use_confirmed:false,personal_use_confirmed:true,analysis_use:'personal_noncommercial'};
assert.equal((await handleSox(req('/api/sox/ingest',{report:personalReport,status:{state:'READY'}},true),env)).status,200);
const personal=await (await handleSox(req('/api/sox'),env)).json();
assert.equal(personal.live.public_use_confirmed,false);
assert.equal(personal.live.analysis_use,'personal_noncommercial');
assert.equal(personal.live.points.drawdown.length,1);
assert.equal(personal.live.latest_macro.drawdown_pct,2);
assert.equal(personal.live.latest_macro.sox_close,undefined);
assert.equal(personal.live.points.drawdown[0].sox_close,undefined);
assert.equal(personal.live.sources.sox.sox_close,undefined);
assert.equal('overfit' in personal,false);
data.inputs=[{payload_json:JSON.stringify({date:'2026-10-01',tga_bn:100,prior_tga_bn:90,net_issuance_bn:10,sox_close:12345,raw_data:'PRIVATE_INPUT'})}];
const withPrivateInputs=await (await handleSox(req('/api/sox'),env)).json();
assert(!JSON.stringify(withPrivateInputs).includes('sox_close'));
assert(!JSON.stringify(withPrivateInputs).includes('PRIVATE_INPUT'));
assert.equal((await handleSox(req('/api/sox/ingest',{report:{...personalReport,analysis_use:'approved_public'}},true),env)).status,400);
assert.equal((await handleSox(req('/api/sox/ingest',{report:{...personalReport,personal_use_confirmed:'true'}},true),env)).status,400);
await handleSox(req('/api/sox/ingest',{report:{...personalReport,personal_use_confirmed:false}},true),env);
const revoked=await (await handleSox(req('/api/sox'),env)).json();
assert.equal(revoked.live.points.drawdown.length,0);
assert.equal(revoked.live.latest_macro.drawdown_pct,null);
assert.equal('overfit' in revoked,false,'Removed chart is never returned.');
console.log('Worker routes/auth/validation/publication guards: passed');
