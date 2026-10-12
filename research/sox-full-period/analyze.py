#!/usr/bin/env python3
"""Single factors on ONE common full sample, no subgroup or fitted composite."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'scripts'))
from sox_model import analyse, sessions

START = '2026-07-20'
PLAN = json.loads((OUT/'extended-plan.json').read_text())
WINDOWS = PLAN['windows']
LAGS = PLAN['lags']
NAMES = {'N':'순발행','delta_TGA':'TGA 증감','d':'기존 잔차 ΔTGA−N'}
OPS = {'sum':'부호 있는 합','abs_sum':'합의 절댓값','sum_abs':'일별 절댓값의 합','std':'표준편차','max_abs':'일별 크기의 최댓값'}


def features(frame):
    values = {k:pd.to_numeric(frame[c],errors='coerce') for k,c in [('N','net_issuance_bn'),('TGA','tga_bn')]}
    values['delta_TGA'] = values['TGA']-pd.to_numeric(frame.prior_tga_bn,errors='coerce')
    values['d'] = values['delta_TGA']-values['N']
    price = pd.to_numeric(frame.sox_close,errors='coerce')
    result,definitions = {},{}
    def add(name,v,family,description):
        result[name]=v.to_numpy(dtype=float)
        definitions[name]={'family':family,'description':description}
    for base in ['N','delta_TGA','d']:
        v=values[base]
        for w in WINDOWS:
            total=v.rolling(w,min_periods=w).sum()
            candidates={'sum':total,'abs_sum':total.abs()}
            if w>1:
                candidates.update(sum_abs=v.abs().rolling(w,min_periods=w).sum(),
                    std=v.rolling(w,min_periods=w).std(ddof=0),max_abs=v.abs().rolling(w,min_periods=w).max())
            for op,x in candidates.items():
                for lag in LAGS:
                    add(f'{base}_{op}_{w}_lag{lag}',x.shift(lag),'treasury',f'{NAMES[base]} · 최근 {w}거래일 {OPS[op]} · {lag}거래일 시차')
    for lag in LAGS:
        add(f'TGA_level_lag{lag}',values['TGA'].shift(lag),'treasury',f'TGA 잔액 · {lag}거래일 시차')
    daily_return=100*np.log(price/price.shift(1))
    for w in WINDOWS:
        mom=100*(price/price.shift(w)-1)
        for lag in LAGS:
            add(f'SOX_momentum_{w}_lag{lag}',mom.shift(lag),'market',f'SOX 과거 {w}거래일 수익률 · {lag}거래일 시차')
            add(f'SOX_abs_momentum_{w}_lag{lag}',mom.abs().shift(lag),'market',f'SOX 과거 {w}거래일 수익률의 크기 · {lag}거래일 시차')
        if w>1:
            vol=daily_return.rolling(w,min_periods=w).std(ddof=0)
            dd=100*(1-price/price.rolling(w,min_periods=w).max())
            ma=100*(price/price.rolling(w,min_periods=w).mean()-1)
            for lag in LAGS:
                add(f'SOX_vol_{w}_lag{lag}',vol.shift(lag),'market',f'SOX 과거 {w}거래일 일간 로그수익률의 표준편차 · {lag}거래일 시차')
                add(f'SOX_past_drawdown_{w}_lag{lag}',dd.shift(lag),'market',f'SOX 최근 {w}개 종가 최고점 대비 낙폭 · {lag}거래일 시차')
                add(f'SOX_ma_deviation_{w}_lag{lag}',ma.shift(lag),'market',f'SOX 최근 {w}개 종가 평균 대비 괴리율 · {lag}거래일 시차')
    return result,definitions


def corr(x,y):
    if len(x)<3 or np.std(x)<1e-12 or np.std(y)<1e-12:return None
    return float(np.corrcoef(x,y)[0,1])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--private-dir',required=True)
    args=p.parse_args()
    private=Path(args.private_dir)
    rows=json.loads((private/'raw-inputs.json').read_text())
    meta=json.loads((private/'source-metadata.json').read_text())
    assert [r['date'] for r in rows]==sessions(rows[0]['date'],rows[-1]['date'])
    frame=pd.DataFrame(rows)
    derived=pd.DataFrame(analyse(rows)['daily'])
    fs,defs=features(frame)
    date=frame.date.to_numpy()
    period=date>=START
    cutoff=int(np.flatnonzero(date>='2026-09-14')[0])
    altered=frame.copy(deep=True)
    for j,c in enumerate(['net_issuance_bn','tga_bn','prior_tga_bn','sox_close']):
        present=altered.index.to_numpy()>=cutoff
        altered.loc[present,c]=altered.loc[present,c]+(j+1)*(altered.index.to_numpy()[present]+1)**2
    changed,_=features(altered)
    for name,x in fs.items():
        np.testing.assert_allclose(x[:cutoff],changed[name][:cutoff],atol=1e-9,rtol=0,equal_nan=True)
    old=json.loads((private/'production-inputs.json').read_text())
    bydate={r['date']:r for r in rows}
    differences=[]
    for r in old:
        for c in ['tga_bn','prior_tga_bn','net_issuance_bn','sox_close']:
            a,b=bydate[r['date']][c],r[c]
            if (a is None)!=(b is None) or (a is not None and abs(a-b)>1e-9):
                differences.append({'date':r['date'],'field':c})
    result={'source':meta,'start':START,'candidate_recipes':len(fs),'windows':WINDOWS,'lags':LAGS,
        'plan':'extended-plan.json','initial_phase_results':'initial-results.json',
        'rules':{'same_complete_outcome_dates_for_every_candidate':True,'each_candidate_must_cover_every_date':True,
                 'date_threshold_or_subgroup_selection':False,'multi_factor_regression':False,'nonlinear_fitted_score':False,
                 'target_horizons_changed':False,'warmup_is_context_only_not_fitted_sample':True},
        'checks':{'future_input_independence':'passed','calendar_continuity':'passed',
                  'production_overlap_dates':len(old),'production_overlap_differences':differences,'raw_prices_exported':False},'targets':{}}
    observations=pd.DataFrame({'date':date[period]})
    matrix=pd.DataFrame({name:x[period] for name,x in fs.items()})
    matrix.insert(0,'date',date[period])
    ys={}
    for kind,col in [('drawdown','drawdown_pct'),('rebound','rebound_pct')]:
        y=pd.to_numeric(derived[col],errors='coerce').to_numpy()
        ys[kind]=y
        mask=period&np.isfinite(y)
        ranked=[]
        incomplete=[]
        for name,x in fs.items():
            # NO per-factor row dropping. A missing X excludes the candidate.
            if not np.isfinite(x[mask]).all():
                incomplete.append(name);continue
            r=corr(x[mask],y[mask])
            if r is not None:ranked.append({'feature':name,**defs[name],'n':int(mask.sum()),'r':r})
        ranked.sort(key=lambda r:(-abs(r['r']),r['feature']))
        assert ranked and all(r['n']==int(mask.sum()) for r in ranked)
        cash=next(r for r in ranked if r['family']=='treasury')
        market=next(r for r in ranked if r['family']=='market')
        baseline_name='d_abs_sum_1_lag0' if kind=='drawdown' else 'd_abs_sum_4_lag0'
        assert np.isfinite(fs[baseline_name][mask]).all()
        target={'n':int(mask.sum()),'complete_dates':date[mask].tolist(),
            'pending_dates':date[period&~np.isfinite(y)].tolist(),
            'baseline':{'feature':baseline_name,'n':int(mask.sum()),'r':corr(fs[baseline_name][mask],y[mask])},
            'best_overall':ranked[0],'best_treasury':cash,'best_market':market,
            'treasury_distinct_x_values':int(np.unique(fs[cash['feature']][mask]).size),
            'market_distinct_x_values':int(np.unique(fs[market['feature']][mask]).size),
            'eligible_nonconstant_candidates':len(ranked),'incomplete_candidates':incomplete,
            'top_10':ranked[:10]}
        result['targets'][kind]=target
        observations[kind+'_pct']=y[period]
        observations[kind+'_status']=np.where(np.isfinite(y[period]),'complete','pending')
        for label,name in [('baseline',baseline_name),('best_treasury',cash['feature']),('best_market',market['feature'])]:
            observations[kind+'_'+label+'_x']=fs[name][period]
        matrix['outcome_'+kind+'_pct']=y[period]
        pd.DataFrame(ranked).to_csv(OUT/f'{kind}-ranking.csv',index=False)
        print(json.dumps({'target':kind,'n':target['n'],'best_overall':target['best_overall'],
                         'best_treasury':cash,'best_market':market,'eligible_candidates':len(ranked)},ensure_ascii=False),flush=True)
    observations.to_csv(OUT/'all-dates.csv',index=False)
    matrix.to_csv(OUT/'all-single-factors.csv',index=False)
    (OUT/'factor-definitions.json').write_text(json.dumps(defs,ensure_ascii=False,indent=2)+'\n')
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    make_charts(result,date,ys,fs)
    make_report(result)
    print(json.dumps({'status':'completed','report':str(OUT/'report.html'),'production_changed':False}))


def make_charts(result,date,ys,fs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('/System/Library/Fonts/AppleSDGothicNeo.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'axes.unicode_minus':False})
    fig,axes=plt.subplots(2,2,figsize=(12,8.8),layout='constrained')
    for row,(kind,label,color) in enumerate([('drawdown','4일 낙폭','#2b77b8'),('rebound','5일째 반등','#bd6a24')]):
        t=result['targets'][kind]
        for column,family in enumerate(['treasury','market']):
            best=t['best_'+family]
            mask=np.isin(date,t['complete_dates'])
            x,y=fs[best['feature']][mask],ys[kind][mask]
            ax=axes[row,column]
            ax.scatter(x,y,s=35,color=color,alpha=.85)
            b,a=np.polyfit(x,y,1)
            grid=np.linspace(x.min(),x.max(),100)
            ax.plot(grid,a+b*grid,color=color,linestyle='--',linewidth=1.5)
            ax.set_title(f'{"재무부 입력" if family=="treasury" else "과거 SOX 입력"} 단일 팩터 최상위\n전체 {len(x)}점 · r={best["r"]:.3f}',fontsize=13)
            ax.set_xlabel(best['description']+(' (USD bn)' if family=='treasury' else ' (%)'),fontsize=10)
            ax.set_ylabel('SOX '+label+' (%)')
            ax.grid(alpha=.2)
    fig.suptitle('7월 20일 이후 전체 유효 표본 · 날짜 선별 없음',fontsize=17)
    fig.savefig(OUT/'full-period-single-factors.png',dpi=160)
    fig.savefig(OUT/'full-period-single-factors.svg')
    plt.close(fig)


def make_report(result):
    blocks=[]
    for kind,label in [('drawdown','4일 낙폭'),('rebound','5일째 반등')]:
        t=result['targets'][kind]
        rows=''.join(f'<tr><td>{html.escape(r["description"])}</td><td>{r["r"]:.3f}</td><td>{r["n"]}</td></tr>' for r in t['top_10'])
        cash,market=t['best_treasury'],t['best_market']
        blocks.append(f'''<section><h2>{label} · 전체 {t['n']}점</h2>
        <p>확정 이벤트 {t['complete_dates'][0]}~{t['complete_dates'][-1]} · 관측 중 {len(t['pending_dates'])}일: {', '.join(t['pending_dates'])}</p>
        <p><b>재무부 단일 팩터 최상위:</b> {html.escape(cash['description'])} · r={cash['r']:.3f}<br>
        <b>과거 SOX 단일 팩터 최상위:</b> {html.escape(market['description'])} · r={market['r']:.3f}<br>
        기존 불일치 크기의 전체 표본 r={t['baseline']['r']:.3f}</p>
        <p>재무부 최고 팩터의 X 값은 {t['treasury_distinct_x_values']}종입니다. 이동 최댓값처럼 같은 값이 반복되는 팩터도 모든 날짜를 그대로 포함했으며, 겹친 점을 임의로 흩뜨리지 않았습니다.</p>
        <p>비교 가능한 단일 팩터 {t['eligible_nonconstant_candidates']:,}개가 모두 같은 {t['n']}개 날짜를 사용했습니다. 입력이 부족한 팩터는 날짜를 줄이는 대신 후보에서 제외했습니다.</p>
        <div class="scroll"><table><thead><tr><th>전체 표본 상관 상위 10개 단일 팩터</th><th>r</th><th>n</th></tr></thead><tbody>{rows}</tbody></table></div>
        <p><a href="{kind}-ranking.csv">전체 순위 CSV</a></p></section>''')
    m=result['source']
    text=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>SOX 7월 20일 이후 전체 기간 · 단일 팩터 탐색</title><style>body{{margin:0;font:15px/1.75 system-ui,sans-serif;background:#f2f5f8;color:#182b3d}}main{{max-width:1100px;padding:30px 24px 60px;margin:auto}}h1{{font-size:28px;letter-spacing:-.04em}}h2{{font-size:22px}}section{{background:white;padding:24px;border:1px solid #dce3eb;border-radius:12px;margin:20px 0}}.scope{{padding:16px;background:#e7eef7;border-radius:10px}}img{{width:100%;height:auto;display:block}}table{{width:100%;border-collapse:collapse;min-width:600px}}th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid #dde3eb;font-size:13px}}th{{background:#edf2f8}}.scroll{{overflow-x:auto}}a{{color:#1964a7}}@media(max-width:700px){{main{{padding:20px 12px}}section{{padding:15px}}}}</style>
    <main><h1>7월 20일 이후 전체 기간 · 단일 팩터 탐색</h1>
    <div class="scope"><b>2026-07-20~{m['latest_sox']} · {m['event_sessions']}거래일</b><br>모든 결과가 확정된 날짜를 동일하게 사용했습니다. 날짜 조건·부분 표본·다중 팩터 회귀·비선형 합성 점수는 사용하지 않았습니다.</div>
    <p>기존 11점/10점 조건부 차트와 다른 실험입니다. 최신 SOX는 {m['latest_sox']}, TGA는 {m['latest_tga']}까지 발표됐습니다. 누적·시차 팩터 때문에 시작 날짜를 잃지 않도록 {m['warmup_from']}부터의 자료는 이전 입력 계산에만 사용했습니다. 7월 20일 이전 이벤트는 상관 계산에 넣지 않았습니다.</p>
    <p>미래 4거래일 낙폭과 5거래일째 반등이라는 기존 결과 산식은 유지했습니다. 아직 이후 종가가 없는 마지막 날짜는 관측 중으로 남기며 0으로 채우지 않습니다. N은 SOMA 제외 액면 순발행 대용치이고 d는 기존 ΔTGA−N입니다.</p>
    <p>후보 산식 {result['candidate_recipes']:,}개: 단일 입력의 누적·절댓값·표준편차·최대 크기, 과거 SOX 수익률·변동성·낙폭·평균 대비 괴리입니다. 구간 {', '.join(map(str,WINDOWS))}거래일, 시차 {', '.join(map(str,LAGS))}거래일을 같은 전체 표본에서 비교했습니다. 결과를 보고 고른 탐색 성적이며 미래 성능을 검증한 값은 아닙니다.</p>
    <p>처음에는 20일 구간·10일 시차까지 비교했으나 낙폭 최고 후보가 두 경계에 걸려, 60일 구간·40일 시차까지 한 번 확장했습니다. 전체 이벤트 날짜는 그대로입니다. <a href="initial-results.json">첫 탐색 결과</a>와 <a href="extended-plan.json">확장 범위</a>를 함께 보관했습니다.</p>
    <img src="full-period-single-factors.png" alt="전체 날짜를 모두 포함한 재무부와 SOX 단일 팩터 산점도">
    {''.join(blocks)}
    <section><h2>자료와 확인</h2><p>7월 20~22일 자료를 원본 14개 탐색 이벤트에서 복사하지 않고 공식 출처에서 독립 조회했습니다. 운영 자료와 겹치는 {result['checks']['production_overlap_dates']}일을 대조했으며 값 차이는 {len(result['checks']['production_overlap_differences'])}건입니다. 미래 입력을 바꿔도 이전 팩터 값이 동일한지 확인했습니다. 원시 SOX 종가는 내보내지 않았습니다.</p>
    <p>자료: <a href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/operating-cash-balance">Treasury DTS</a> · <a href="https://fiscaldata.treasury.gov/datasets/monthly-statement-public-debt/">MSPD</a> · <a href="https://fred.stlouisfed.org/series/NASDAQSOX">FRED/Nasdaq SOX</a></p>
    <p><a href="all-dates.csv">전체 날짜와 상태 CSV</a> · <a href="all-single-factors.csv">전체 단일 팩터 행렬</a> · <a href="factor-definitions.json">팩터 정의</a> · <a href="results.json">결과 JSON</a></p>
    <p>2026-10-13 개인·비상업 연구 · 운영 DB와 배포된 차트는 이 분석으로 변경하지 않았습니다.</p></section></main></html>'''
    (OUT/'report.html').write_text(text)


if __name__=='__main__':
    main()
