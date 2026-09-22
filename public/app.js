
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
  // FiscalData works from the browser in this deployment (daily TGA already proves it).
  // Use its official auctions dataset instead of TreasuryDirect, which is blocked by TLS/CORS.
  const base="https://api.fiscaldata.treasury.gov/services/api/fiscal_service";
  const auctionsUrl=base+"/v1/accounting/od/auctions_query?sort=-auction_date&page%5Bsize%5D=250";
  const upcomingUrl=base+"/v1/accounting/od/upcoming_auctions?sort=auction_date&page%5Bsize%5D=100";

  try{
    const [aRes,uRes]=await Promise.all([
      fetch(auctionsUrl,{cache:"no-store"}),
      fetch(upcomingUrl,{cache:"no-store"})
    ]);
    if(!aRes.ok) throw new Error(`FiscalData auctions ${aRes.status}`);

    const actualBody=await aRes.json();
    const upcomingBody=uRes.ok ? await uRes.json() : {data:[]};

    const rows=[];
    for(const r of actualBody.data||[]) rows.push(normalizeFiscalAuction(r));
    for(const r of upcomingBody.data||[]) rows.push(normalizeFiscalUpcoming(r));

    const dedup=new Map();
    for(const r of rows){
      if(!r.issue_date) continue;
      const key=`${r.cusip||r.security_type+"-"+r.security_term}|${r.auction_date||""}|${r.issue_date}`;
      const old=dedup.get(key);
      if(!old || rankStatus(r.status)>rankStatus(old.status)) dedup.set(key,r);
    }

    const today=new Date();
    const from=localIsoDate(today);
    const to=localIsoDate(new Date(today.getTime()+12*86400000));
    const future=[...dedup.values()]
      .filter(r=>r.issue_date>=from && r.issue_date<=to)
      .sort((a,b)=>a.issue_date.localeCompare(b.issue_date) || String(a.security_term).localeCompare(String(b.security_term)));

    renderSettlementRows(future,"Treasury FiscalData live");
    return;
  }catch(e){
    console.warn("Direct FiscalData auction fetch failed; falling back to Worker cache",e);
  }

  const r=await fetch("/api/settlements",{cache:"no-store"});
  const x=await r.json();
  const rows=(x.days||[]).flatMap(d=>(d.rows||[]).map(a=>({...a,issue_date:d.settlementDate})));
  renderSettlementRows(rows,"Worker cache");
}

function normalizeFiscalAuction(r){
  const accepted=firstMoneyBn(r.total_accepted_amt,r.total_accepted);
  const offering=firstMoneyBn(r.offering_amt,r.offering_amount);
  const hasResult=accepted!=null && accepted>0;
  return {
    cusip:nullish(r.cusip),
    security_type:nullish(r.security_type),
    security_term:nullish(r.security_term),
    auction_date:fdDate(r.auction_date || r.record_date),
    issue_date:fdDate(r.issue_date),
    maturity_date:fdDate(r.maturity_date),
    offering_amt:offering,
    public_face_bn:hasResult ? accepted : offering,
    status:hasResult ? "actual" : "tentative"
  };
}

function normalizeFiscalUpcoming(r){
  return {
    cusip:nullish(r.cusip),
    security_type:nullish(r.security_type),
    security_term:nullish(r.security_term),
    auction_date:fdDate(r.auction_date),
    issue_date:fdDate(r.issue_date),
    maturity_date:fdDate(r.maturity_date),
    offering_amt:firstMoneyBn(r.offering_amt,r.offering_amount),
    public_face_bn:firstMoneyBn(r.offering_amt,r.offering_amount),
    status:"tentative"
  };
}

function rankStatus(s){ return s==="actual"?2:1; }
function nullish(v){ return v==null || v==="null" || v==="" ? null : String(v); }
function fdDate(v){
  if(!v || v==="null") return null;
  const s=String(v);
  const m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(m) return `${m[1]}-${m[2]}-${m[3]}`;
  return null;
}
function firstMoneyBn(...vals){
  for(const v of vals){
    if(v==null || v==="" || v==="null") continue;
    const n=Number(String(v).replace(/,/g,""));
    if(Number.isFinite(n)){
      // FiscalData auction currency fields are dollar amounts.
      return n/1e9;
    }
  }
  return null;
}
function localIsoDate(d){
  const y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,"0"),day=String(d.getDate()).padStart(2,"0");
  return `${y}-${m}-${day}`;
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
    const face=Number(a.public_face_bn ?? a.offering_amt ?? a.total_accepted ?? 0);
    groups[d].gross+=Number.isFinite(face)?face:0;
    if(a.status==="actual") groups[d].actual+=face||0; else groups[d].tentative+=face||0;
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
          · $${Number(a.public_face_bn ?? a.offering_amt ?? a.total_accepted ?? 0).toFixed(1)}bn
        </div>`).join("")}
        <div class="muted">Source: ${esc(source)}</div>
      </div>
    </div>`).join("");
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
  try{await Promise.all([loadSummary(),loadSettlements(),loadTreasuryTgaDirect()])}
  catch(e){$("status").textContent="DATA ERROR"; $("freshness").textContent=String(e)}
}
load(); setInterval(load,300000);
