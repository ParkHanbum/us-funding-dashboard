const $=id=>document.getElementById(id);
const pct=x=>x==null?"—":`${Number(x).toFixed(2)}%`;
const bn=x=>x==null?"—":`$${Number(x).toFixed(3)}bn`;
const bp=x=>x==null?"—":`${x>=0?"+":""}${Number(x).toFixed(1)} bp`;
const dBn=x=>x==null?"—":`${x>=0?"+":""}$${Number(x).toFixed(1)}bn`;
const esc=s=>String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));

async function loadSummary(){
  const r=await fetch("/api/summary",{cache:"no-store"}); const s=await r.json(); const m=s.metrics||{};
  $("status").textContent=s.classification||"NEUTRAL";
  $("sofr").textContent=pct(m.SOFR?.value); $("sofrDate").textContent=m.SOFR?.observed_at||"—";
  $("effr").textContent=pct(m.EFFR?.value); $("effrDate").textContent=m.EFFR?.observed_at||"—";
  $("iorb").textContent=pct(m.IORB?.value); $("iorbDate").textContent=m.IORB?.observed_at||"—";
  $("sofrIorb").textContent=bp(s.spreadsBp?.sofrIorb); $("sofrIorb2").textContent=bp(s.spreadsBp?.sofrIorb);
  $("sofrEffr").textContent=bp(s.spreadsBp?.sofrEffr); $("effrIorb").textContent=bp(s.spreadsBp?.effrIorb);
  $("tgcr").textContent=pct(m.TGCR?.value); $("bgcr").textContent=pct(m.BGCR?.value); $("p99").textContent=pct(m.SOFR_P99?.value);
  $("rrp").textContent=bn(m.ON_RRP?.value); $("rrpDate").textContent=m.ON_RRP?.observed_at||"—";
  $("srf").textContent=bn(m.SRF_USAGE?.value); $("srfDate").textContent=m.SRF_USAGE?.observed_at||"—";
  $("tga").textContent=bn(m.TGA?.value); $("tgaDate").textContent=m.TGA?.observed_at||"—";
  $("reserves").textContent=bn(m.RESERVES?.value); $("reservesDate").textContent=m.RESERVES?.observed_at||"—";
  $("tga1d").textContent=dBn(s.tgaChangesBn?.oneDay); $("tga5d").textContent=dBn(s.tgaChangesBn?.fiveBusinessDays); $("tgaWow").textContent=dBn(s.tgaChangesBn?.weekOverWeek);
  $("freshness").textContent=s.stale?.length?`⚠ ${s.stale.join(" · ")}`:"Fresh";
}

async function loadSettlements(){
  const r=await fetch("/api/settlements",{cache:"no-store"});
  if(!r.ok) throw new Error(`settlements ${r.status}`);
  const x=await r.json();
  const rows=(x.days||[]).flatMap(d=>(d.rows||[]).map(a=>({...a,issue_date:d.settlementDate})));
  renderSettlementRows(rows,"GitHub Actions → D1");
}

function renderSettlementRows(rows,source){
  if(!rows.length){
    $("settlements").innerHTML=`<div class="muted">향후 settlement를 찾지 못했습니다. · ${esc(source)}</div>`;
    return;
  }

  const groups={};
  for(const a of rows){
    const d=a.issue_date || a.settlementDate;
    if(!d) continue;
    if(!groups[d]) groups[d]={date:d,gross:0,actual:0,tentative:0,rows:[]};

    const face=Number(a.public_face_bn ?? a.offering_amt ?? 0);
    groups[d].gross+=Number.isFinite(face)?face:0;
    if(a.status==="actual") groups[d].actual+=face||0;
    else groups[d].tentative+=face||0;
    groups[d].rows.push(a);
  }

  $("settlements").innerHTML=Object.values(groups).sort((a,b)=>a.date.localeCompare(b.date)).map(day=>`
    <div class="settlement-day">
      <div class="settlement-top">
        <b>${esc(day.date)}</b>
        <span>Gross public face $${day.gross.toFixed(1)}bn</span>
      </div>
      <div class="rows">
        ${day.rows.map(a=>`<div>
          <span class="${a.status==="actual"?"actual":"tentative"}">${a.status==="actual"?"ACTUAL":"TENTATIVE"}</span>
          · ${esc(a.security_type)} ${esc(a.security_term)}
          · auction ${esc(a.auction_date)}
          · $${Number(a.public_face_bn ?? a.offering_amt ?? 0).toFixed(1)}bn
        </div>`).join("")}
        <div class="muted">Source: ${esc(source)}</div>
      </div>
    </div>`).join("");
}

async function loadLiquidity(){
  const r=await fetch("/api/liquidity",{cache:"no-store"});
  if(!r.ok) throw new Error(`liquidity ${r.status}`);
  const x=await r.json();
  const days=x.days||[];

  if(!days.length){
    $("liquidityRows").innerHTML=`<tr><td colspan="7" class="muted">No data</td></tr>`;
    return;
  }

  $("liquidityRows").innerHTML=days.map(d=>{
    const maturity=d.publicMaturityBn==null?"TBD":`$${Number(d.publicMaturityBn).toFixed(1)}bn`;
    const net=d.netPrincipalDrainBn==null?"TBD":signedBn(d.netPrincipalDrainBn);
    const cash=d.netCashEstimateBn==null?"—":signedBn(d.netCashEstimateBn);
    const issue=`$${Number(d.issuanceFaceBn||0).toFixed(1)}bn`;
    return `<tr>
      <td><b>${esc(d.date)}</b></td>
      <td>${issue}<div class="mini">${esc(d.status)}</div></td>
      <td>${maturity}<div class="mini">${confidenceText(d.confidence)}</div></td>
      <td class="${netClass(d.netPrincipalDrainBn)}">${net}</td>
      <td>${cash}</td>
      <td><span class="risk risk-${String(d.risk).toLowerCase()}">${esc(d.risk)}</span></td>
      <td class="mini">${d.rows?.length||0} settlement(s)</td>
    </tr>`;
  }).join("");

  $("liquidityMethod").textContent=
    "Net principal: public issuance − estimated publicly-held maturities. Positive = liquidity drain. Cash estimate is price-adjusted and still excludes coupon-payment/TIPS details.";
}

function signedBn(v){
  const n=Number(v);
  if(!Number.isFinite(n)) return "—";
  return `${n>=0?"+":"−"}$${Math.abs(n).toFixed(1)}bn`;
}
function netClass(v){
  if(v==null) return "";
  return Number(v)>0?"drain":"addition";
}
function confidenceText(v){
  if(v==="cash-estimate") return "price-adjusted";
  if(v==="principal-only") return "principal only";
  return "maturity pending";
}

async function loadTreasuryTgaDirect(){
  const url="https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance?sort=-record_date&page%5Bsize%5D=80";
  try{
    const r=await fetch(url,{cache:"no-store"});
    if(!r.ok) throw new Error(`Treasury DTS ${r.status}`);
    const body=await r.json();
    const rows=(body.data||[])
      .filter(x=>x.account_type==="Treasury General Account (TGA) Closing Balance")
      .map(x=>({date:x.record_date,value:Number(x.open_today_bal)/1000}))
      .filter(x=>Number.isFinite(x.value))
      .sort((a,b)=>b.date.localeCompare(a.date));
    if(!rows.length) throw new Error("No TGA rows");
    $("tga").textContent=bn(rows[0].value);
    $("tgaDate").textContent=rows[0].date+" · Treasury direct";
    $("tga1d").textContent=rows[1]?dBn(rows[0].value-rows[1].value):"—";
    $("tga5d").textContent=rows[5]?dBn(rows[0].value-rows[5].value):"—";
    $("tgaWow").textContent=rows[5]?dBn(rows[0].value-rows[5].value):"—";
  }catch(e){
    console.warn("Direct Treasury TGA fetch failed; using weekly H.4.1 fallback",e);
  }
}

async function load(){
  try{
    await Promise.all([
      loadSummary(),
      loadSettlements(),
      loadLiquidity(),
      loadTreasuryTgaDirect()
    ]);
  }catch(e){
    $("status").textContent="DATA ERROR";
    $("freshness").textContent=String(e);
  }
}
load();
setInterval(load,300000);
