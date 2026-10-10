import assert from 'node:assert/strict';
import {buildOverfitReport,marketSessions,OVERFIT_RULES} from '../src/sox_overfit.js';

const live={personal_use_confirmed:true,public_use_confirmed:false,analysis_use:'personal_noncommercial'};
const dates=marketSessions('2026-07-23','2026-10-23');
assert(!dates.includes('2026-09-07'),'Labor Day must not consume a lag/session.');
assert(!marketSessions('2026-04-02','2026-04-06').includes('2026-04-03'),'Good Friday closure.');
assert(!marketSessions('2025-01-08','2025-01-10').includes('2025-01-09'),'Special closure.');
assert(!marketSessions('2026-10-01','2026-10-09',['2026-10-06']).includes('2026-10-06'));
const rows=dates.map((date,i)=>({date,tga_bn:i%2?98:103,prior_tga_bn:100,
  net_issuance_bn:i===3?-13:10+i,sox_close:100+i*.2}));
for(const [j,price] of [100,95,90,92,94,88].entries())rows[8+j].sox_close=price;
const report=buildOverfitReport(rows,live);
const point=(r,kind,date=dates[8])=>r.context_points[kind].find(p=>p.date===date);
assert.equal(point(report,'drawdown').x,55,'Absolute signed sum, not sum of absolute daily issuance.');
assert(Math.abs(point(report,'drawdown').y-10)<1e-12);
assert.equal(point(report,'rebound').x,5);
assert(Math.abs(point(report,'rebound').y-(88/90-1)*100)<1e-12,'Day 5 can finish below the observed trough.');
assert(report.post_selection_stats.drawdown.n>0&&report.post_selection_stats.rebound.n>0);
assert(report.context_points.rebound.some(p=>p.date<=report.search_through&&p.period==='after_selection'),
  'Previously pending outcomes count as new confirmations, even for an older event.');
const missing=rows.map(r=>({...r}));missing[3].net_issuance_bn=null;
assert.equal(point(buildOverfitReport(missing,live),'drawdown'),undefined,'Missing issuance is never zero.');
assert(point(buildOverfitReport(missing,live),'rebound'),'TGA factor does not depend on unrelated missing issuance.');
const deleted=rows.filter((_,i)=>i!==3);
assert.equal(point(buildOverfitReport(deleted,live),'drawdown'),undefined,'Missing session must not compress the six-day window.');
const missingPrice=rows.map(r=>({...r}));missingPrice[10].sox_close=null;
assert.equal(point(buildOverfitReport(missingPrice,live),'drawdown'),undefined);
assert.equal(point(buildOverfitReport(missingPrice,live),'rebound'),undefined);
const missingTga=rows.map(r=>({...r}));missingTga[6].prior_tga_bn=null;
assert.equal(point(buildOverfitReport(missingTga,live),'rebound'),undefined);
assert(point(buildOverfitReport(missingTga,live),'drawdown'));
const changed=rows.map((r,i)=>i<16?{...r}:{...r,tga_bn:r.tga_bn+10000,net_issuance_bn:1e6,sox_close:r.sox_close*2});
const altered=buildOverfitReport(changed,live);
for(const kind of ['drawdown','rebound']) {
  assert.equal(point(altered,kind).x,point(report,kind).x);
  assert.equal(point(altered,kind).gate_value,point(report,kind).gate_value);
  assert.deepEqual(altered.rules[kind],OVERFIT_RULES[kind],'Later inputs must never retune thresholds.');
}
const extra=rows.map(r=>({...r}));extra[8].tga_bn=107.44501;
assert.equal(point(buildOverfitReport(extra,live),'rebound').selected,false);
assert.equal(buildOverfitReport(rows,{...live,personal_use_confirmed:false}),null);
assert.equal(buildOverfitReport(rows,{...live,analysis_use:'approved_public'}),null);
assert(buildOverfitReport(rows,{public_use_confirmed:true}));
assert.equal(buildOverfitReport(rows.map(r=>({...r,net_issuance_bn:null})),live).stats.drawdown.n,0);
assert.equal(buildOverfitReport(rows.map(r=>({...r,sox_close:100})),live).stats.drawdown.r,null);
assert.equal(JSON.stringify(report).includes('sox_close'),false,'Only derived observations are returned.');
assert.equal(JSON.stringify(report).includes('net_issuance_bn'),false);
assert.deepEqual(buildOverfitReport([],live).stats.drawdown,{n:0,r:null,slope:null,intercept:null});
assert(Object.isFrozen(OVERFIT_RULES.drawdown));
console.log('Frozen factors: signed sums, lags, calendar gaps, null inputs, negative rebound, scope guards, future-input independence and new confirmations passed.');
