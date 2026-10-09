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
  let payload = null, mode = 'live', layout = 'split', showTrend = false, selectedDate = null;
  const visible = {drawdown:true,rebound:true};
  const series = {
    drawdown:{title:'1일 불일치와 이후 낙폭',label:'4일 낙폭 크기',short:'낙폭',color:'#80bfff',x:'1일 불일치 크기 U1 (십억 달러)',y:'SOX 낙폭 (%)',stat:'stat-blue',description:'이벤트일부터 4거래일 뒤까지의 최대 낙폭',reading:'오른쪽일수록 불일치의 크기가 크고, 위쪽일수록 낙폭이 큽니다.'},
    rebound:{title:'4일 누적 불일치와 반등',label:'5일째 반등률',short:'반등',color:'#ffbc83',x:'4일 불일치 크기 U4 (십억 달러)',y:'SOX 반등 (%)',stat:'stat-orange',description:'관측 저점 대비 5거래일째의 가격 변화',reading:'위쪽일수록 반등이 큽니다. 0% 아래는 관측 저점보다 낮습니다.'}
  };
  host.innerHTML = `
    <div class="sx-header"><div><div class="sx-eyebrow">LIQUIDITY RESEARCH · DAILY OBSERVATIONS</div>
      <h2>TGA·순발행 불일치와 SOX 반응</h2><p>TGA 증감과 순발행의 차이가 큰 날, 이후 낙폭·반등이 어땠는지 살펴봅니다.</p></div>
      <span class="sx-badge" data-sx="badge">불러오는 중</span></div>
    <div class="sx-tabs" role="tablist" aria-label="분석 표본">
      <button role="tab" aria-selected="true" data-mode="live">최신 자동 차트</button>
      <button role="tab" aria-selected="false" data-mode="reference">원본 탐색 표본</button>
    </div>
    <div class="sx-coverage" aria-live="polite"><strong data-sx="current-view">차트 표시 기간 확인 중</strong><span data-sx="freshness"></span></div>
    <div class="sx-formula" data-sx="formula"><strong>가로축은 불일치의 크기(절댓값)입니다.</strong><span>U1 = |ΔTGA − 순발행| · ΔTGA는 직전 관측일 대비 TGA 잔액 증감입니다.</span><span>U4는 최근 4거래일의 부호 있는 차이를 더한 뒤 절댓값을 취합니다. 유출·유입 방향은 이 축에서 구분되지 않습니다.</span><span>자동 표본의 순발행은 SOMA를 제외한 액면 기준 대용치이며, 쿠폰을 차감한 기존 순유출액과 별도입니다.</span></div>
    <div class="sx-toolbar">
      <div class="sx-view-switch" role="group" aria-label="차트 배치">
        <button data-layout="split" aria-pressed="true">나눠 보기</button><button data-layout="overlay" aria-pressed="false">겹쳐 보기</button>
      </div>
      <div class="sx-controls">
        <label><input type="checkbox" data-series="drawdown" checked><i class="sx-circle" aria-hidden="true"></i>낙폭</label>
        <label><input type="checkbox" data-series="rebound" checked><i class="sx-square" aria-hidden="true"></i>반등</label>
        <label class="sx-trend-toggle"><input type="checkbox" data-sx="trend">추세선</label>
      </div>
    </div>
    <div class="sx-chart"><div data-sx="plot" class="sx-plot-grid"></div><div class="sx-tooltip" data-sx="tooltip" role="tooltip"></div><div class="sx-empty" data-sx="empty">자료를 불러오는 중입니다.</div></div>
    <div class="sx-plot-help"><span>점 하나 = 이벤트 하루 · 점을 누르면 같은 날짜의 두 결과를 함께 볼 수 있습니다.</span><span>r: 상관계수 · n: 표본 수</span></div>
    <div class="sx-selection" data-sx="selection" hidden></div>
    <div class="sx-small" data-sx="sample"></div>
    <div class="sx-note" data-sx="notice"></div>
    <div class="sx-metrics">
      <div class="sx-metric"><span>최근 1일 불일치 크기 U1</span><strong data-sx="u1">—</strong><small data-sx="macro-date">자동 입력 대기</small></div>
      <div class="sx-metric"><span>최근 4일 누적 불일치 크기 U4</span><strong data-sx="u4">—</strong><small>부호 있는 잔차를 합산한 뒤 절댓값</small></div>
      <div class="sx-metric"><span>자동 표본 관측 구간</span><strong data-sx="live-days">—</strong><small data-sx="live-period">원본 탐색 구간과 별도 관리</small></div>
    </div>
    <h3>최근 이벤트 · 결과 확정까지의 진행 상황</h3>
    <div class="sx-scroll"><table><thead><tr><th>이벤트 날짜</th><th>U1</th><th>U4</th><th>4일 낙폭</th><th>5일째 반등</th><th>관측 상태</th></tr></thead><tbody data-sx="recent"></tbody></table></div>
    <details><summary>산식 · 표본 선정 · 데이터 출처</summary><div class="sx-methods" data-sx="methods"></div></details>
    <details><summary>현재 차트의 점 데이터</summary><div class="sx-scroll"><table><thead><tr><th>날짜</th><th>계열</th><th>불일치 크기 (USD bn)</th><th>SOX 반응 (%)</th></tr></thead><tbody data-sx="point-table"></tbody></table></div></details>
    <div class="sx-bottom"><span class="sx-small" data-sx="updated">일별 스냅샷 · 화면은 5분마다 확인</span><span class="sx-small">탐색적 연관 분석 · 예측·매매 신호가 아닙니다.</span></div>`;

  for (const button of host.querySelectorAll('[data-mode]')) button.addEventListener('click', () => {
    mode = button.dataset.mode;
    selectedDate = null;
    for (const b of host.querySelectorAll('[data-mode]')) b.setAttribute('aria-selected',String(b===button));
    render();
  });
  for (const input of host.querySelectorAll('[data-series]')) input.addEventListener('change', () => {
    visible[input.dataset.series] = input.checked; render();
  });
  for (const button of host.querySelectorAll('[data-layout]')) button.addEventListener('click', () => {
    layout = button.dataset.layout;
    for (const b of host.querySelectorAll('[data-layout]')) b.setAttribute('aria-pressed',String(b===button));
    render();
  });
  $('trend').addEventListener('change', e => {showTrend=e.target.checked;render();});

  function stepFor(range) {
    const rough=Math.max(range,1)/5, power=10**Math.floor(Math.log10(rough)), fraction=rough/power;
    return (fraction<=1?1:fraction<=2?2:fraction<=2.5?2.5:fraction<=5?5:10)*power;
  }

  function chartSvg(data, kinds, width) {
    const points=kinds.flatMap(kind=>(data?.points?.[kind]||[])
      .filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)).map(p=>({...p,kind})));
    const W=Math.max(320,Math.round(width)), H=W>700?380:340;
    const pad={l:56,r:20,t:22,b:62}, pw=W-pad.l-pad.r, ph=H-pad.t-pad.b;
    const xstep=stepFor(Math.max(10,...points.map(p=>p.x))*1.08);
    const xmax=Math.ceil(Math.max(10,...points.map(p=>p.x))*1.08/xstep)*xstep;
    const low=Math.min(0,...points.map(p=>p.y)), high=Math.max(3,...points.map(p=>p.y));
    const ystep=stepFor(high-low+1);
    const ymin=low<0?Math.floor((low-.25)/ystep)*ystep:0;
    const ymax=Math.ceil((high+.4)/ystep)*ystep;
    const sx=x=>pad.l+x/xmax*pw, sy=y=>pad.t+(ymax-y)/(ymax-ymin)*ph;
    const tick=(v,step)=>v.toFixed(step<1?2:step%1?1:0);
    const solo=kinds.length===1?series[kinds[0]]:null;
    const clip=`sx-clip-${kinds.join('-')}`;
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="${esc(solo?.title||'불일치 크기와 SOX 반응 비교')} 산점도"><defs><clipPath id="${clip}"><rect x="${pad.l-8}" y="${pad.t-8}" width="${pw+16}" height="${ph+16}"/></clipPath></defs>`;
    for(let x=0;x<=xmax+xstep/2;x+=xstep){
      svg+=`<line x1="${sx(x)}" x2="${sx(x)}" y1="${pad.t}" y2="${H-pad.b}" stroke="#26364a" stroke-opacity=".55"/><text x="${sx(x)}" y="${H-pad.b+24}" fill="#a1b2c6" text-anchor="middle" font-size="13">${tick(x,xstep)}</text>`;
    }
    for(let y=ymin;y<=ymax+ystep/2;y+=ystep){
      svg+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${sy(y)}" y2="${sy(y)}" stroke="${Math.abs(y)<1e-8?'#6b8098':'#26364a'}" stroke-opacity="${Math.abs(y)<1e-8?'.9':'.55'}" ${Math.abs(y)<1e-8?'stroke-dasharray="4 5"':''}/><text x="${pad.l-12}" y="${sy(y)+4}" fill="#a1b2c6" text-anchor="end" font-size="13">${tick(y,ystep)}</text>`;
    }
    svg+=`<text x="${pad.l+pw/2}" y="${H-12}" fill="#b5c5d8" text-anchor="middle" font-size="13">${esc(solo?.x||'불일치 크기 (십억 달러)')}</text><text transform="translate(15 ${pad.t+ph/2}) rotate(-90)" fill="#b5c5d8" text-anchor="middle" font-size="13">${esc(solo?.y||'SOX 반응 (%)')}</text><g clip-path="url(#${clip})">`;
    for(const kind of kinds){
      const info=series[kind], fit=data?.stats?.[kind], ps=points.filter(p=>p.kind===kind);
      if(showTrend && Number.isFinite(fit?.slope) && Number.isFinite(fit?.intercept) && ps.length>=3){
        const a=Math.min(...ps.map(p=>p.x)), b=Math.max(...ps.map(p=>p.x));
        svg+=`<line data-trend="${kind}" x1="${sx(a)}" x2="${sx(b)}" y1="${sy(a*fit.slope+fit.intercept)}" y2="${sy(b*fit.slope+fit.intercept)}" stroke="${info.color}" stroke-width="2" stroke-dasharray="6 4" opacity=".8"/>`;
      }
      // Draw the selected event last so dense near-zero points cannot cover it.
      ps.sort((a,b)=>Number(a.date===selectedDate)-Number(b.date===selectedDate)).forEach(p=>{
        const selected=p.date===selectedDate;
        const title=`${p.date} · ${info.label}\n불일치 크기 ${fmt(p.x,3)} 십억 달러\nSOX 반응 ${fmt(p.y,3)}%` +
          (p.delta_tga_bn!=null?`\nΔTGA ${money(p.delta_tga_bn)} · 순발행 ${money(p.net_issuance_bn)}`:'') +
          (p.first_complete_seen_at?`\n입력 최초 확보 ${p.first_complete_seen_at}`:'');
        const attr=`fill="${info.color}" fill-opacity="${selected?1:.78}" stroke="${selected?'#f5f9ff':'#0b121b'}" stroke-width="${selected?2.5:1.3}" class="sx-point${selected?' sx-point-selected':''}" data-kind="${kind}" data-date="${esc(p.date)}" data-tip="${esc(title)}" tabindex="0" role="button" aria-pressed="${selected}" aria-label="${esc(title+' · 이 날짜 선택')}"`;
        svg+=kind==='drawdown'?`<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="${selected?7:5.5}" ${attr}/>`:`<rect x="${sx(p.x)-(selected?6.5:5)}" y="${sy(p.y)-(selected?6.5:5)}" width="${selected?13:10}" height="${selected?13:10}" rx="1.5" ${attr}/>`;
      });
    }
    return svg+'</g></svg>';
  }

  function pointSelection(data) {
    const ps=Object.values(data?.points||{}).flat();
    if(selectedDate && !ps.some(p=>p.date===selectedDate)) selectedDate=null;
    const selection=$('selection');
    selection.hidden=!selectedDate;
    if(!selectedDate) {selection.innerHTML='';return;}
    const dd=data?.points?.drawdown?.find(p=>p.date===selectedDate);
    const rb=data?.points?.rebound?.find(p=>p.date===selectedDate);
    const recent=mode==='live'?data?.recent?.find(p=>p.date===selectedDate):null;
    const outcome=(point,status)=>point?`${fmt(point.y,2)}%`:status==='pending'?'관측 중':'해당 표본 없음';
    const value=(point,macro)=>Number.isFinite(point?.x)?`${fmt(point.x,2)} 십억 달러`:Number.isFinite(macro)?`${fmt(macro,2)} 십억 달러`:'—';
    const input=dd||rb||recent;
    const delta=input?.delta_tga_bn, net=input?.net_issuance_bn;
    const calculation=Number.isFinite(delta)&&Number.isFinite(net)
      ?`1일 계산 · ΔTGA (${fmt(delta,3)}) − 순발행 (${fmt(net,3)}) = ${fmt(delta-net,3)} → 절댓값 ${fmt(Math.abs(delta-net),3)} 십억 달러`
      :'이 표본은 TGA 증감·순발행 분해값이 제공되지 않습니다.';
    selection.innerHTML=`<div class="sx-selection-heading"><strong>선택한 이벤트 · ${esc(selectedDate)}</strong><button data-sx="clear-selection" aria-label="선택한 날짜 해제">선택 해제</button></div><div class="sx-selection-values"><div><span>1일 불일치 크기 U1</span><strong>${value(dd,recent?.u1_bn)}</strong></div><div><span>4일 누적 불일치 크기 U4</span><strong>${value(rb,recent?.u4_bn)}</strong></div><div><span>4일 낙폭</span><strong>${outcome(dd,recent?.status4)}</strong></div><div><span>5일째 반등</span><strong>${outcome(rb,recent?.status5)}</strong></div></div><div class="sx-selection-formula" data-sx="selection-formula">${esc(calculation)}</div>`;
    $('clear-selection').addEventListener('click',()=>{selectedDate=null;render();});
  }

  function plot(data) {
    const points=Object.keys(series).flatMap(kind=>visible[kind]?(data?.points?.[kind]||[])
      .filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)).map(p=>({...p,kind})):[]);
    const active=Object.keys(series).filter(kind=>visible[kind]);
    const width=$('plot').clientWidth||host.clientWidth-48;
    const stacked=window.matchMedia('(max-width:900px)').matches || active.length<2;
    const cardWidth=(stacked?width:(width-18)/2)-36;
    const stats=kind=>`<span class="sx-stat" data-sx="${series[kind].stat}"></span>`;
    $('plot').className=`sx-plot-grid${layout==='overlay'?' sx-overlay':''}${active.length<2?' sx-single':''}`;
    if(layout==='split'){
      $('plot').innerHTML=Object.entries(series).map(([kind,info])=>{
        const dates=(data?.points?.[kind]||[]).map(p=>p.date).sort();
        return `<section class="sx-plot-card sx-${kind}" data-chart="${kind}" ${visible[kind]?'':'hidden'}><div class="sx-plot-heading"><h3><i class="${kind==='drawdown'?'sx-circle':'sx-square'}" aria-hidden="true"></i>${info.title}</h3>${stats(kind)}</div><p class="sx-plot-description">${info.description}</p><div class="sx-svg-wrap">${visible[kind]?chartSvg(data,[kind],cardWidth):''}${visible[kind]&&!dates.length?'<div class="sx-card-empty">확정된 표본 대기</div>':''}</div><div class="sx-reading">${info.reading}</div><div class="sx-series-date">${dates.length?`확정 이벤트 ${esc(dates[0])} ~ ${esc(dates.at(-1))}`:'이후 거래일 자료가 쌓이면 점이 추가됩니다.'}</div></section>`;
      }).join('');
    }else{
      $('plot').innerHTML=`<section class="sx-plot-card" data-chart="overlay"><div class="sx-plot-heading"><h3>낙폭과 반등 비교</h3><div class="sx-overlay-stats">${Object.entries(series).map(([kind,info])=>`<span ${visible[kind]?'':'hidden'}><i class="${kind==='drawdown'?'sx-circle':'sx-square'}" aria-hidden="true"></i>${info.short} ${stats(kind)}</span>`).join('')}</div></div><p class="sx-plot-description">두 관측 기간을 같은 축에서 비교합니다. 불일치 크기는 각각 U1·U4입니다.</p>${chartSvg(data,active,width-36)}<div class="sx-reading">파란 원: 4일 낙폭 · 주황 네모: 5일째 반등 · 추세선은 선택해서 볼 수 있습니다.</div></section>`;
    }
    $('empty').style.display=points.length?'none':'flex';
    const license=(payload?.live && !analysisAllowed(payload.live)) || payload?.status?.state==='LICENSE_REQUIRED';
    $('empty').textContent=!active.length?'표시할 계열을 선택하세요.':mode==='live'?(license?'SOX 공개 이용 확인 대기 · TGA·순발행 입력은 별도로 자동 수집됩니다.':'확정된 자동 표본이 아직 없습니다. 입력과 이후 4~5거래일 관측이 필요합니다.'):'확정된 탐색 표본이 없습니다.';
    const tooltip=$('tooltip');
    tooltip.style.display='none';
    const show=elem=>{
      tooltip.textContent=elem.dataset.tip;tooltip.style.display='block';
      const box=host.querySelector('.sx-chart').getBoundingClientRect(), point=elem.getBoundingClientRect();
      tooltip.style.left=Math.max(8,Math.min(point.left-box.left+18,box.width-tooltip.offsetWidth-8))+'px';
      tooltip.style.top=Math.max(8,Math.min(point.top-box.top-tooltip.offsetHeight-12,box.height-tooltip.offsetHeight-8))+'px';
    };
    const hide=()=>{tooltip.style.display='none';};
    for(const elem of $('plot').querySelectorAll('[data-tip]')){
      const select=keyboard=>{
        selectedDate=elem.dataset.date;render();
        if(keyboard) $('plot').querySelector(`[data-kind="${elem.dataset.kind}"][data-date="${selectedDate}"]`)?.focus({preventScroll:true});
      };
      elem.addEventListener('mouseenter',()=>show(elem));elem.addEventListener('mouseleave',hide);
      elem.addEventListener('focus',()=>show(elem));elem.addEventListener('blur',hide);
      elem.addEventListener('click',()=>select(false));
      elem.addEventListener('keydown',event=>{
        if(event.key==='Enter'||event.key===' '){event.preventDefault();select(true);}
        if(event.key==='Escape'){
          const date=elem.dataset.date, kind=elem.dataset.kind;
          selectedDate=null;render();
          $('plot').querySelector(`[data-kind="${kind}"][data-date="${date}"]`)?.focus({preventScroll:true});
          hide();
        }
      });
    }
    pointSelection(data);
    $('point-table').innerHTML=points.slice().sort((a,b)=>a.date.localeCompare(b.date)).map(p=>`<tr><td>${esc(p.date)}</td><td>${esc(series[p.kind].label)}</td><td>${fmt(p.x,3)}</td><td>${fmt(p.y,3)}</td></tr>`).join('') || '<tr><td colspan="4">확정 표본 없음</td></tr>';
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
