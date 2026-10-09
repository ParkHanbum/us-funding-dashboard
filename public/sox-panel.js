/* Dependency-free interactive chart. The existing public/app.js is untouched. */
(() => {
  'use strict';
  const host = document.getElementById('sox-research');
  if (!host) return;
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (v,d=1) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—';
  const money = v => typeof v === 'number' && Number.isFinite(v) ? '$'+v.toFixed(1)+'bn' : '—';
  const analysisAllowed = live => live?.public_use_confirmed === true ||
    (live?.personal_use_confirmed === true && live?.analysis_use === 'personal_noncommercial');
  const $ = id => host.querySelector('[data-sx="'+id+'"]');
  let payload = null, mode = 'live', visible = {drawdown:true,rebound:true};
  host.innerHTML = `
    <div class="sx-header"><div><div class="sx-eyebrow">LIQUIDITY RESEARCH · DAILY OBSERVATIONS</div>
      <h2>준비금 전달 불일치와 SOX 반응</h2><p>1일 충격 이후 낙폭과 4일 누적 충격 이후 반등을 분리해 관측합니다.</p></div>
      <span class="sx-badge" data-sx="badge">불러오는 중</span></div>
    <div class="sx-tabs" role="tablist" aria-label="분석 표본">
      <button role="tab" aria-selected="true" data-mode="live">최신 자동 차트</button>
      <button role="tab" aria-selected="false" data-mode="reference">원본 탐색 표본</button>
    </div>
    <div class="sx-coverage" aria-live="polite"><strong data-sx="current-view">차트 표시 기간 확인 중</strong><span data-sx="freshness"></span></div>
    <div class="sx-controls">
      <label><input type="checkbox" data-series="drawdown" checked><i class="sx-circle"></i>1일 충격 → 4일 낙폭 <span class="sx-stat" data-sx="stat-blue"></span></label>
      <label><input type="checkbox" data-series="rebound" checked><i class="sx-square"></i>4일 충격 → 5일째 반등 <span class="sx-stat" data-sx="stat-orange"></span></label>
    </div>
    <div class="sx-chart"><div data-sx="plot"></div><div class="sx-tooltip" data-sx="tooltip"></div><div class="sx-empty" data-sx="empty">자료를 불러오는 중입니다.</div></div>
    <div class="sx-small" data-sx="sample"></div>
    <div class="sx-note" data-sx="notice"></div>
    <div class="sx-metrics">
      <div class="sx-metric"><span>최근 1일 충격 U1</span><strong data-sx="u1">—</strong><small data-sx="macro-date">자동 입력 대기</small></div>
      <div class="sx-metric"><span>최근 4일 누적 충격 U4</span><strong data-sx="u4">—</strong><small>부호 있는 잔차를 합산한 뒤 절댓값</small></div>
      <div class="sx-metric"><span>자동 표본 관측 구간</span><strong data-sx="live-days">—</strong><small data-sx="live-period">원본 탐색 구간과 별도 관리</small></div>
    </div>
    <h3>최근 이벤트 · 결과 확정까지의 진행 상황</h3>
    <div class="sx-scroll"><table><thead><tr><th>이벤트 날짜</th><th>U1</th><th>U4</th><th>4일 낙폭</th><th>5일째 반등</th><th>관측 상태</th></tr></thead><tbody data-sx="recent"></tbody></table></div>
    <details><summary>산식 · 표본 선정 · 데이터 출처</summary><div class="sx-methods" data-sx="methods"></div></details>
    <details><summary>현재 차트의 점 데이터</summary><div class="sx-scroll"><table><thead><tr><th>날짜</th><th>계열</th><th>충격 (USD bn)</th><th>SOX 반응 (%)</th></tr></thead><tbody data-sx="point-table"></tbody></table></div></details>
    <div class="sx-bottom"><span class="sx-small" data-sx="updated">일별 스냅샷 · 화면은 5분마다 확인</span><span class="sx-small">탐색적 연관 분석 · 예측·매매 신호가 아닙니다.</span></div>`;

  for (const button of host.querySelectorAll('[data-mode]')) button.addEventListener('click', () => {
    mode = button.dataset.mode;
    for (const b of host.querySelectorAll('[data-mode]')) b.setAttribute('aria-selected',String(b===button));
    render();
  });
  for (const input of host.querySelectorAll('[data-series]')) input.addEventListener('change', () => {
    visible[input.dataset.series] = input.checked; render();
  });

  function plot(data) {
    const labels = {drawdown:'4일 낙폭 크기',rebound:'5일째 반등률'};
    const colors = {drawdown:'#75b8ff',rebound:'#ffb570'};
    const points = Object.entries(data?.points || {}).flatMap(([kind,ps]) => visible[kind] ? ps.map(p=>({...p,kind})) : []);
    const mobile=host.clientWidth<600;
    const W=mobile?440:1060,H=mobile?410:440,pad={l:mobile?59:70,r:28,t:30,b:64},pw=W-pad.l-pad.r,ph=H-pad.t-pad.b;
    const axisFont=mobile?16:12;
    const xmax=Math.max(50,...points.map(p=>p.x))*1.13;
    const ymin=Math.min(0,...points.map(p=>p.y))-0.7, ymax=Math.max(3,...points.map(p=>p.y))+0.9;
    const sx=x=>pad.l+x/xmax*pw, sy=y=>pad.t+(ymax-y)/(ymax-ymin)*ph;
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="불일치 충격과 SOX 반응 산점도"><defs><clipPath id="sx-clip"><rect x="${pad.l}" y="${pad.t}" width="${pw}" height="${ph}"/></clipPath></defs>`;
    for(let i=0;i<=5;i++){
      const x=i/5*xmax,y=ymin+i/5*(ymax-ymin);
      svg+=`<line x1="${sx(x)}" x2="${sx(x)}" y1="${pad.t}" y2="${H-pad.b}" stroke="#26364a" stroke-opacity=".6"/><text x="${sx(x)}" y="${H-pad.b+24}" fill="#93a7bd" text-anchor="middle" font-size="${axisFont}">${x.toFixed(0)}</text>`;
      svg+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${sy(y)}" y2="${sy(y)}" stroke="#26364a" stroke-opacity=".6"/><text x="${pad.l-12}" y="${sy(y)+4}" fill="#93a7bd" text-anchor="end" font-size="${axisFont}">${y.toFixed(1)}</text>`;
    }
    svg+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${sy(0)}" y2="${sy(0)}" stroke="#63758c" stroke-dasharray="4 5"/><text x="${pad.l+pw/2}" y="${H-14}" fill="#9eb1c6" text-anchor="middle" font-size="${mobile?17:13}">${mobile?"충격 (십억 달러)":"준비금 전달 불일치 충격 (십억 달러)"}</text><text transform="translate(19 ${pad.t+ph/2}) rotate(-90)" fill="#9eb1c6" text-anchor="middle" font-size="${mobile?17:13}">SOX 반응 (%)</text><g clip-path="url(#sx-clip)">`;
    for(const kind of ['drawdown','rebound']){
      if(!visible[kind]) continue;
      const fit=data?.stats?.[kind], ps=points.filter(p=>p.kind===kind);
      if(fit?.slope!=null && fit?.intercept!=null && ps.length>=3){
        const a=Math.min(...ps.map(p=>p.x)),b=Math.max(...ps.map(p=>p.x));
        svg+=`<line x1="${sx(a)}" x2="${sx(b)}" y1="${sy(a*fit.slope+fit.intercept)}" y2="${sy(b*fit.slope+fit.intercept)}" stroke="${colors[kind]}" stroke-width="2" opacity=".7"/>`;
      }
      ps.forEach((p,index)=>{
        const title=`${p.date}\n${labels[kind]}\n충격 $${fmt(p.x,3)}bn · 반응 ${fmt(p.y,3)}%` +
          (p.delta_tga_bn!=null?`\nΔTGA ${money(p.delta_tga_bn)} · 순발행 ${money(p.net_issuance_bn)}`:``) +
          (p.first_complete_seen_at?`\n입력 최초 확보 ${p.first_complete_seen_at}`:``);
        const attr=`fill="${colors[kind]}" stroke="#0b121b" stroke-width="1.5" data-tip="${esc(title)}" tabindex="0" aria-label="${esc(title)}"`;
        svg+=kind==='drawdown'?`<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="6.5" ${attr}/>`:`<rect x="${sx(p.x)-6}" y="${sy(p.y)-6}" width="12" height="12" rx="1" ${attr}/>`;
        if(mode==='reference' && !mobile){
          // Offset coincident near-zero baseline points so the date labels stay readable.
          const offset=p.y===0? (index%3)*15 : 0;
          svg+=`<text x="${sx(p.x)+10}" y="${sy(p.y)-9-offset}" fill="#c4d0dc" font-size="11">${p.date.slice(5)}</text>`;
        }
      });
    }
    svg+='</g></svg>';
    $('plot').innerHTML=svg;
    $('empty').style.display=points.length?'none':'flex';
    const license=(payload?.live && !analysisAllowed(payload.live)) || payload?.status?.state==='LICENSE_REQUIRED';
    $('empty').textContent=mode==='live'?(license?'SOX 공개 이용 확인 대기 · TGA·순발행 입력은 별도로 자동 수집됩니다.':'확정된 자동 표본이 아직 없습니다. 입력과 이후 4~5거래일 관측이 필요합니다.'):'표시할 계열을 선택하세요.';
    const tooltip=$('tooltip');
    for(const elem of $('plot').querySelectorAll('[data-tip]')){
      const show=()=>{tooltip.textContent=elem.dataset.tip;tooltip.style.display='block';};
      const hide=()=>{tooltip.style.display='none';};
      elem.addEventListener('mouseenter',show);elem.addEventListener('mouseleave',hide);
      elem.addEventListener('focus',show);elem.addEventListener('blur',hide);
    }
    $('point-table').innerHTML=points.slice().sort((a,b)=>a.date.localeCompare(b.date)).map(p=>`<tr><td>${esc(p.date)}</td><td>${esc(labels[p.kind])}</td><td>${fmt(p.x,3)}</td><td>${fmt(p.y,3)}</td></tr>`).join('') || '<tr><td colspan="4">확정 표본 없음</td></tr>';
  }

  function render(){
    if(!payload) return;
    const data=mode==='reference'?payload.reference:payload.live;
    plot(data);
    for(const [kind,id] of [['drawdown','stat-blue'],['rebound','stat-orange']]){
      const s=data?.stats?.[kind];$(id).textContent=`r=${fmt(s?.r,3)}, n=${s?.n ?? 0}`;
    }
    const live=payload.live, state=payload.status?.state||'WAITING_FIRST_RUN';
    const permissionPending=(live && !analysisAllowed(live)) || state==='LICENSE_REQUIRED';
    const liveEventDates=Object.values(live?.points||{}).flat().map(p=>p.date).sort();
    const referencePeriod=[payload.reference?.from,payload.reference?.to].filter(Boolean).join(' ~ ');
    $('current-view').textContent=mode==='reference'
      ?`현재 차트: 원본 고정 표본 · ${referencePeriod || '기간 미확인'}`
      :liveEventDates.length
        ?`현재 차트: 자동 일별 표본 · 이벤트 ${liveEventDates[0]} ~ ${liveEventDates.at(-1)}`
        :'현재 차트: 자동 일별 표본 · 확정된 SOX 결과 없음';
    const latestInput=live?.latest_macro?.date || live?.analysis_through;
    const latestResult=permissionPending?'공개 이용 확인 대기':liveEventDates.length?`최신 확정 이벤트 ${liveEventDates.at(-1)}`:'확정 결과 대기';
    const latestClose=live?.sources?.sox?.observed_through;
    $('freshness').textContent=`최근 TGA·순발행 입력: ${latestInput || '수집 대기'} · SOX 종가 기준: ${latestClose || '수집 대기'} · 자동 SOX 차트: ${latestResult}`;
    const stateLabels={READY:'일별 갱신',WAITING_SOURCE_UPDATE:'SOX 종가 발표 대기',LICENSE_REQUIRED:'SOX 이용 확인 필요',PARTIAL:'일부 입력 대기',SOURCE_ERROR:'수집 오류',WAITING_FIRST_RUN:'첫 수집 대기',MIGRATION_REQUIRED:'DB 초기화 필요'};
    $('badge').textContent=stateLabels[state]||state;$('badge').dataset.state=state;
    $('sample').textContent=mode==='reference'?'2026-05-15~2026-07-22 · 원본 이벤트 14개 · 선택된 탐색 표본':
      (live?.analysis_use==='personal_noncommercial'?'개인·비상업 연구용 · ':'')+'자동 모델 · 결과로 선별하지 않은 전체 유효 일별 표본 · 관측창 중첩 있음';
    $('notice').textContent=mode==='reference'?'원본 상관계수는 과거 탐색 표본의 기술통계입니다. 선정 규칙을 복원한 예측모델이 아니며, 아래 자동 입력과 회귀선을 합치지 않습니다.':'TGA·순발행 자료가 실제로 알려진 시각은 이벤트 날짜보다 늦을 수 있습니다. 이 차트는 사건 이후의 연관 분석이며, 이벤트 당일 실행 가능한 매매 전략의 성과가 아닙니다.';
    const lm=live?.latest_macro;
    $('u1').textContent=money(lm?.u1_bn);$('u4').textContent=money(lm?.u4_bn);
    $('macro-date').textContent=lm?`${lm.date} · TGA 기준 ${lm.tga_source_date}`:'자동 입력 대기';
    $('live-days').textContent=live?`${live.data_days ?? 0}거래일`:'—';
    $('live-period').textContent=live?.analysis_from?`${live.analysis_from} ~ ${live.analysis_through}`:'원본 탐색 구간과 별도 관리';
    $('recent').innerHTML=(live?.recent||[]).map(r=>{
      const dd=permissionPending?'이용 확인 대기':r.status4==='complete'?fmt(r.drawdown_pct,2)+'%':`${Math.min(r.observed_sessions,4)}/4일 관측`;
      const rb=permissionPending?'이용 확인 대기':r.status5==='complete'?fmt(r.rebound_pct,2)+'%':`${Math.min(r.observed_sessions,5)}/5일 관측`;
      const status=!r.macro_ready?'유동성 입력 누락':permissionPending?'SOX 이용 확인 대기':r.status4==='complete'&&r.status5==='complete'?'확정':'관측 중';
      return `<tr><td>${esc(r.date)}</td><td>${money(r.u1_bn)}</td><td>${money(r.u4_bn)}</td><td class="${r.status4==='complete'?'':'sx-pending'}">${esc(dd)}</td><td class="${r.status5==='complete'?'':'sx-pending'}">${esc(rb)}</td><td title="${esc((r.issues||[]).join(', '))}">${esc(status)}</td></tr>`;
    }).join('') || '<tr><td colspan="6">첫 자동 수집 후 이벤트별 진행 상황이 표시됩니다.</td></tr>';
    const methods=payload.methods||{};
    $('methods').innerHTML=Object.entries(methods).filter(([k])=>k!=='sources').map(([k,v])=>`<div><strong>${esc(k)}</strong> · ${esc(v)}</div>`).join('') +
      '<div>Sources · '+(methods.sources||[]).filter(u=>/^https:\/\//.test(u)).map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(new URL(u).hostname)}</a>`).join(' · ')+'</div>'+
      '<div>SOX 데이터: Copyright © 2025, Nasdaq, Inc. · FRED NASDAQSOX를 통해 조회.</div>' +
      (live?.analysis_use==='personal_noncommercial'
        ?'<div>개인·비상업 연구용 분석입니다. SOX 원시 종가는 제공하지 않습니다.</div>'
        :permissionPending?'<div>SOX 데이터 이용 확인 대기. TGA·순발행 입력은 별도로 갱신됩니다.</div>':'') +
      (live?.warnings?.length?`<div>현재 상태 · ${esc(live.warnings.join(' · '))}</div>`:'');
    const waitingSource=state==='WAITING_SOURCE_UPDATE';
    const last=waitingSource?payload.status?.last_attempt:payload.status?.last_success_at;
    $('updated').textContent=last?`${waitingSource?'최근 확인':'최근 수집'} ${new Date(last).toLocaleString('ko-KR')} · 데이터 기준 ${live?.analysis_through||'—'}`:'원본 차트 로드 완료 · 자동 수집은 기존 Treasury workflow에서 실행';
  }

  async function load(){
    try{
      if(window.__SOX_PREVIEW_DATA__) payload=window.__SOX_PREVIEW_DATA__;
      else{
        const r=await fetch('/api/sox',{cache:'no-store'});
        if(!r.ok)throw new Error(`SOX API HTTP ${r.status}`);
        payload=await r.json();
      }
      render();
    }catch(e){$('badge').textContent='수집 상태 확인 필요';$('notice').textContent=e.message;$('empty').textContent='SOX 모듈의 Worker 배포와 DB 초기화를 확인하세요.';}
  }
  let resizeTimer;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(payload)render();},100);});
  load();
  setInterval(()=>{if(!document.hidden)load();},300000);
})();
