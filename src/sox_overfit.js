// Frozen X-only rules from research/sox-factor-search/results.json.
// Recompute derived observations from existing private inputs; never retune rules.
export const OVERFIT_RULES = Object.freeze({
  drawdown: Object.freeze({feature:'N_abs_sum_6_lag2', gate:'SOX_vol5', threshold:1.4704419604775212,
    direction:'<=', discovery_n:10, discovery_r:0.9212287475065536}),
  rebound: Object.freeze({feature:'delta_TGA_abs_sum_5_lag0', gate:'abs_delta_TGA', threshold:7.445000000000005,
    direction:'<=', discovery_n:10, discovery_r:0.950533548908593})
});
const SEARCH_THROUGH = '2026-10-08';
const DAY = 86400000;
const number = v => typeof v === 'number' && Number.isFinite(v) ? v : null;
const positive = v => number(v) !== null && v > 0 ? v : null;
const dateOK = v => typeof v === 'string' && /^20\d\d-\d\d-\d\d$/.test(v) &&
  Number.isFinite(Date.parse(v+'T00:00:00Z')) && new Date(v+'T00:00:00Z').toISOString().slice(0,10) === v;
const iso = d => new Date(d).toISOString().slice(0,10);
const nth = (year, month, weekday, n) => {
  const first = Date.UTC(year,month-1,1);
  return first + ((weekday-new Date(first).getUTCDay()+7)%7+(n-1)*7)*DAY;
};

// Mirror scripts/sox_model.py's market calendar, including missing sessions.
// A missing row must not silently shorten a window or lag.
function holidays(year) {
  const h = new Set([nth(year,1,1,3),nth(year,2,1,3),nth(year,9,1,1),nth(year,11,4,4)]);
  const lastMay = Date.UTC(year,4,31);
  h.add(lastMay-((new Date(lastMay).getUTCDay()+6)%7)*DAY);
  const a=year%19,b=Math.floor(year/100),c=year%100,d=Math.floor(b/4),e=b%4,f=Math.floor((b+8)/25);
  const g=Math.floor((b-f+1)/3),k=c%4,i=Math.floor(c/4),x=(19*a+b-d-g+15)%30;
  const l=(32+2*e+2*i-x-k)%7,m=Math.floor((a+11*x+22*l)/451),q=x+l-7*m+114;
  h.add(Date.UTC(year,Math.floor(q/31)-1,q%31+1)-2*DAY);
  const fixed=[[1,1],[7,4],[12,25],...(year>=2022?[[6,19]]:[])];
  for(const [month,day] of fixed) {
    let t=Date.UTC(year,month-1,day), w=new Date(t).getUTCDay();
    if(w===0)t+=DAY;
    else if(w===6 && !(month===1 && day===1))t-=DAY;
    h.add(t);
  }
  if(year===2025)h.add(Date.UTC(2025,0,9));
  return h;
}

export function marketSessions(start, end, extraClosed=[]) {
  if(!dateOK(start)||!dateOK(end)||start>end)return [];
  const closed=new Set(extraClosed), cache=new Map(), result=[];
  // Inputs are bounded to 500 rows; reject implausible/corrupt date ranges.
  if(Date.parse(end)-Date.parse(start)>6*366*DAY)return [];
  for(let t=Date.parse(start+'T00:00:00Z');t<=Date.parse(end+'T00:00:00Z');t+=DAY) {
    const d=new Date(t),year=d.getUTCFullYear();
    if(!cache.has(year))cache.set(year,holidays(year));
    if(d.getUTCDay()>0&&d.getUTCDay()<6&&!cache.get(year).has(t)&&!closed.has(iso(t)))result.push(iso(t));
  }
  return result;
}

export function regression(points) {
  const n=points.length, result={n,r:null,slope:null,intercept:null};
  if(n<3)return result;
  const mx=points.reduce((s,p)=>s+p.x,0)/n,my=points.reduce((s,p)=>s+p.y,0)/n;
  let xx=0,yy=0,xy=0;
  for(const p of points){xx+=(p.x-mx)**2;yy+=(p.y-my)**2;xy+=(p.x-mx)*(p.y-my);}
  if(xx<=1e-16||yy<=1e-16)return result;
  return {...result,r:Math.max(-1,Math.min(1,xy/Math.sqrt(xx*yy))),slope:xy/xx,intercept:my-xy/xx*mx};
}

function windowValues(values, end, size) {
  if(end<size-1)return null;
  const v=values.slice(end-size+1,end+1);
  return v.length===size&&v.every(x=>x!==null)?v:null;
}
const magnitude = v => v ? Math.abs(v.reduce((s,x)=>s+x,0)) : null;

export function buildOverfitReport(inputs, live, extraClosed=[]) {
  const allowed=live?.public_use_confirmed===true ||
    (live?.personal_use_confirmed===true&&live?.analysis_use==='personal_noncommercial');
  if(!allowed)return null;
  const rows=inputs.filter(r=>dateOK(r.date)&&r.date>='2026-07-23'&&
    (!dateOK(live.analysis_from)||r.date>=live.analysis_from)&&
    (!dateOK(live.analysis_through)||r.date<=live.analysis_through)).sort((a,b)=>a.date.localeCompare(b.date));
  const dates=rows.length?marketSessions(rows[0].date,rows.at(-1).date,extraClosed):[];
  const bydate=new Map(rows.map(r=>[r.date,r]));
  const prices=dates.map(d=>positive(bydate.get(d)?.sox_close));
  const n=dates.map(d=>number(bydate.get(d)?.net_issuance_bn));
  const delta=dates.map(d=>{
    const r=bydate.get(d),a=number(r?.tga_bn),b=number(r?.prior_tga_bn);
    return a!==null&&b!==null?a-b:null;
  });
  const returns=prices.map((p,i)=>i&&p!==null&&prices[i-1]!==null?100*Math.log(p/prices[i-1]):null);
  const context={drawdown:[],rebound:[]}, pending={drawdown:0,rebound:0};
  for(let i=0;i<dates.length;i++) {
    const r5=windowValues(returns,i,5);
    const mean=r5?r5.reduce((s,v)=>s+v,0)/5:null;
    const vol=r5?Math.sqrt(r5.reduce((s,v)=>s+(v-mean)**2,0)/5):null;
    const xs={drawdown:magnitude(windowValues(n,i-2,6)),rebound:magnitude(windowValues(delta,i,5))};
    const gates={drawdown:vol,rebound:delta[i]!==null?Math.abs(delta[i]):null};
    const p4=prices.slice(i,i+5), complete=p4.length===5&&p4.every(p=>p!==null);
    const low=complete?Math.min(...p4):null;
    const outcomes={drawdown:complete?100*(1-low/prices[i]):null,
      rebound:complete&&i+5<prices.length&&prices[i+5]!==null?100*(prices[i+5]/low-1):null};
    for(const kind of ['drawdown','rebound']) {
      const x=xs[kind],gate=gates[kind],y=outcomes[kind];
      if(x===null||gate===null)continue;
      // Numeric tolerance only; thresholds never depend on the current sample.
      const selected=gate<=OVERFIT_RULES[kind].threshold+1e-12;
      if(y===null){if(selected)pending[kind]++;continue;}
      const outcomeThrough=dates[i+(kind==='drawdown'?4:5)];
      context[kind].push({date:dates[i],x,y,gate_value:gate,selected,outcome_through:outcomeThrough,
        period:outcomeThrough<=SEARCH_THROUGH?'search':'after_selection'});
    }
  }
  const points=Object.fromEntries(Object.entries(context).map(([k,ps])=>[k,ps.filter(p=>p.selected)]));
  const fits=(collection,predicate=()=>true)=>Object.fromEntries(Object.entries(collection).map(([k,ps])=>[k,regression(ps.filter(predicate))]));
  return {model_id:'sox-overfit-2026-10-08-v1',selection:'POSTHOC_X_ONLY_RULES_FROZEN',
    search_through:SEARCH_THROUGH,analysis_from:dates[0]??null,analysis_through:dates.at(-1)??null,
    rules:OVERFIT_RULES,points,context_points:context,stats:fits(points),full_stats:fits(context),
    post_selection_stats:fits(points,p=>p.period==='after_selection'),pending_selected:pending};
}
