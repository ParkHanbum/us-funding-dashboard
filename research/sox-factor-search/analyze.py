#!/usr/bin/env python3
"""Explicit exploratory/overfit study. Read-only inputs; never ingests or deploys.

Targets are the existing chart outcomes. No raw SOX closes are exported.
Uses numpy/pandas; plotting is optional and isolated from the dashboard.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from sox_model import analyse, sessions

OUT = Path(__file__).resolve().parent
WINDOWS = [1, 2, 3, 4, 5, 6, 8, 10]
LAGS = [0, 1, 2, 3, 5]
WEIGHTS = [.25, .5, .75, 1, 1.25, 1.5, 2, 3, 4]
TEST_START = '2026-09-14'
MIN_FULL = 30
MIN_TRAIN = 15
FEATURES = {}
META = {}


def add(name, values, family, formula):
    a = np.array(values, dtype=float, copy=True)
    a[~np.isfinite(a)] = np.nan
    # Keep the predefined recipes regardless of later-period values/coverage.
    # Eligibility and constant-vector checks are scoped to each search split.
    FEATURES[name] = a.copy()
    META[name] = {'family': family, 'formula': formula}


def correlation(x, y, minimum=3):
    good = np.isfinite(x) & np.isfinite(y)
    a, b = np.asarray(x)[good], np.asarray(y)[good]
    if len(a) < minimum or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def score(y, pred):
    good = np.isfinite(y) & np.isfinite(pred)
    y, pred = y[good], pred[good]
    if not len(y):
        return {'n': 0, 'r': None, 'r2': None, 'rmse': None}
    sse = float(np.sum((y-pred)**2))
    sst = float(np.sum((y-np.mean(y))**2))
    return {'n': int(len(y)), 'r': correlation(y, pred),
            'r2': 1-sse/sst if sst > 1e-12 else None,
            'rmse': math.sqrt(sse/len(y))}


def build_features(frame):
    n = pd.to_numeric(frame.net_issuance_bn, errors='coerce')
    delta = pd.to_numeric(frame.tga_bn, errors='coerce') - pd.to_numeric(frame.prior_tga_bn, errors='coerce')
    d = delta - n
    price = pd.to_numeric(frame.sox_close, errors='coerce')
    bases = {'d': d, 'N': n, 'delta_TGA': delta}
    for base, values in bases.items():
        for window in WINDOWS:
            candidates = {'sum': values.rolling(window, min_periods=window).sum()}
            candidates['abs_sum'] = candidates['sum'].abs()
            if window > 1:
                candidates['sum_abs'] = values.abs().rolling(window, min_periods=window).sum()
                candidates['std'] = values.rolling(window, min_periods=window).std(ddof=0)
                candidates['max_abs'] = values.abs().rolling(window, min_periods=window).max()
            for op, v in candidates.items():
                for lag in LAGS:
                    add(f'{base}_{op}_{window}_lag{lag}', v.shift(lag), 'treasury',
                        f'{op}({base}, trailing {window} sessions), lag {lag}')
    for a in WEIGHTS:
        if a == 1:
            continue  # Exact algebraic aliases of d's signed/absolute sums.
        for window in [1, 3, 4, 5]:
            v = (a*delta-n).rolling(window, min_periods=window).sum()
            for absolute in [False, True]:
                add(f'weighted_{a:g}_{window}_{"abs" if absolute else "signed"}',
                    v.abs() if absolute else v, 'weighted_treasury',
                    f'{"abs" if absolute else "signed"}(sum({a:g}*delta_TGA - N, trailing {window}))')
    prior = pd.to_numeric(frame.prior_tga_bn, errors='coerce').replace(0, np.nan)
    for base, v in bases.items():
        add(f'{base}_pct_prior_TGA', 100*v/prior, 'treasury_normalized', f'100*{base}/prior_TGA')
        add(f'{base}_signed_sqrt', np.sign(v)*np.sqrt(v.abs()), 'treasury_transform', f'sign({base})*sqrt(abs({base}))')
        add(f'{base}_log_abs', np.log1p(v.abs()), 'treasury_transform', f'log(1+abs({base}))')
    add('TGA_balance', frame.tga_bn, 'treasury', 'TGA closing balance')
    ret = 100*np.log(price/price.shift(1))
    for window in WINDOWS:
        mom = 100*(price/price.shift(window)-1)
        add(f'SOX_momentum_{window}', mom, 'market', f'100*(P_t/P_(t-{window}) - 1)')
        add(f'SOX_abs_momentum_{window}', mom.abs(), 'market', f'abs(SOX_momentum_{window})')
    for window in [3, 4, 5, 8, 10]:
        vol = ret.rolling(window, min_periods=window).std(ddof=0)
        for lag in [0, 1, 2, 3]:
            add(f'SOX_vol_{window}_lag{lag}', vol.shift(lag), 'market',
                f'SD(past daily SOX log returns, {window} sessions), lag {lag}')
        add(f'SOX_past_drawdown_{window}', 100*(1-price/price.rolling(window, min_periods=window).max()),
            'market', f'100*(1-P_t/max(P_(t-{window-1})..P_t))')
        add(f'SOX_ma_deviation_{window}', 100*(price/price.rolling(window, min_periods=window).mean()-1),
            'market', f'100*(P_t/mean(last {window} closes)-1)')
    vol5 = ret.rolling(5, min_periods=5).std(ddof=0)
    mom5 = 100*(price/price.shift(5)-1)
    for name, v in [('abs_d', d.abs()), ('d4', d.rolling(4).sum()),
                    ('N4', n.rolling(4).sum()), ('TGA4', delta.rolling(4).sum())]:
        for suffix, m in [('vol5', vol5), ('mom5', mom5)]:
            add(f'{name}_times_{suffix}', v*m, 'interaction', f'{name} * SOX_{suffix}')
    dates = pd.to_datetime(frame.date)
    add('calendar_trend', np.arange(len(frame), dtype=float), 'calendar', 'session index (time-trend control)')
    for weekday in range(5):
        add(f'weekday_{weekday}', (dates.dt.weekday == weekday).astype(float), 'calendar', f'weekday = {weekday} (Monday=0)')
    add('month_end_4_calendar_days', [float(d.day >= calendar.monthrange(d.year, d.month)[1]-3) for d in dates],
        'calendar', 'last four calendar days of month')
    add('month_start_3_calendar_days', (dates.dt.day <= 3).astype(float), 'calendar', 'first three calendar days of month')
    gates = {'abs_d': d.abs().to_numpy(), 'abs_N': n.abs().to_numpy(),
             'abs_delta_TGA': delta.abs().to_numpy(), 'abs_d4': d.rolling(4).sum().abs().to_numpy(),
             'SOX_vol5': vol5.to_numpy()}
    kernel = np.column_stack([n, delta, mom5, vol5])
    return gates, kernel


def rank_features(y, mask, minimum):
    ranked = []
    for name, x in FEATURES.items():
        good = mask & np.isfinite(x) & np.isfinite(y)
        r = correlation(x[good], y[good], minimum)
        if r is not None:
            ranked.append({'feature': name, **META[name], 'n': int(good.sum()), 'r': r})
    return sorted(ranked, key=lambda r: (-abs(r['r']), -r['n'], r['feature']))


def linear_evaluation(x, y, train, test):
    a = train & np.isfinite(x) & np.isfinite(y)
    b = test & np.isfinite(x) & np.isfinite(y)
    coef = np.linalg.lstsq(np.column_stack([np.ones(a.sum()), x[a]]), y[a], rcond=None)[0]
    pred = coef[0]+coef[1]*x
    return {'train_raw_r': correlation(x[a], y[a]), 'test_raw_r': correlation(x[b], y[b]),
            'train': score(y[a], pred[a]), 'test': score(y[b], pred[b]),
            'intercept': float(coef[0]), 'slope': float(coef[1])}, pred, a, b


def gate_search(y, source_mask, gates, minimum):
    """Optimize X-only selectors. Threshold quantiles use source rows only."""
    results, trials = [], 0
    seen = set()
    for gate, values in gates.items():
        available = source_mask & np.isfinite(values) & np.isfinite(y)
        if available.sum() < 2*minimum:
            continue
        for q in [.2, .3, .4, .5, .6, .7, .8]:
            threshold = float(np.quantile(values[available], q))
            for direction in ['>=', '<=']:
                selection = np.isfinite(values) & ((values >= threshold) if direction == '>=' else (values <= threshold))
                fingerprint = (selection & source_mask).tobytes()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                for name, x in FEATURES.items():
                    good = source_mask & selection & np.isfinite(x) & np.isfinite(y)
                    # A gate must actually exclude observations.
                    if good.sum() < minimum or (source_mask & ~selection & np.isfinite(x) & np.isfinite(y)).sum() < 5:
                        continue
                    r = correlation(x[good], y[good], minimum)
                    if r is None:
                        continue
                    trials += 1
                    results.append({'feature': name, **META[name], 'gate': gate,
                                    'direction': direction, 'quantile': q, 'threshold': threshold,
                                    'n': int(good.sum()), 'r': r})
    return sorted(results, key=lambda r: (-abs(r['r']), -r['n'], r['feature'])), trials


def chosen_gate(row, gates):
    v = gates[row['gate']]
    return np.isfinite(v) & ((v >= row['threshold']) if row['direction'] == '>=' else (v <= row['threshold']))


def subset_diagnostic(x, y, mask, dates):
    good = mask & np.isfinite(x) & np.isfinite(y)
    ids = np.flatnonzero(good)
    values, counts = np.unique(x[good], return_counts=True)
    loo = [correlation(np.delete(x[ids], i), np.delete(y[ids], i)) for i in range(len(ids))]
    finite = [r for r in loo if r is not None]
    return {'n': len(ids), 'unique_x_values': len(values),
            'value_counts_if_binary': {str(float(v)): int(n) for v, n in zip(values, counts)} if len(values) <= 2 else None,
            'leave_one_out_r_min': min(finite) if finite else None,
            'leave_one_out_r_max': max(finite) if finite else None,
            'leave_one_out_undefined_dates': [str(dates[ids[i]]) for i, r in enumerate(loo) if r is None]}


def audit_no_future_features(frame, cutoff):
    original_features = {k: v.copy() for k, v in FEATURES.items()}
    original_meta = dict(META)
    altered = frame.copy(deep=True)
    # Deliberately perturb every future input, preserving missingness.
    for offset, column in enumerate(['net_issuance_bn', 'tga_bn', 'prior_tga_bn', 'sox_close']):
        for i in range(cutoff, len(altered)):
            if pd.notna(altered.at[i, column]):
                altered.at[i, column] = float(altered.at[i, column])+(offset+1)*(i+1)**2
    FEATURES.clear(); META.clear()
    build_features(altered)
    count = 0
    for name, values in original_features.items():
        assert name in FEATURES, f'Future perturbation unexpectedly removed {name}'
        np.testing.assert_allclose(values[:cutoff], FEATURES[name][:cutoff], rtol=0, atol=1e-10, equal_nan=True,
                                   err_msg=f'Future input leakage in {name}')
        count += 1
    FEATURES.clear(); FEATURES.update(original_features)
    META.clear(); META.update(original_meta)
    return {'future_input_perturbation': 'passed', 'feature_vectors_checked': count,
            'training_feature_cutoff': TEST_START}


def cherry_pick(x, y, count=8):
    """Deliberate Y-dependent demo: select closest points to pair-defined lines.
    This cannot be applied before the future SOX outcome is known.
    """
    good = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    best = None
    for pos, i in enumerate(good):
        for j in good[pos+1:]:
            if abs(x[j]-x[i]) < 1e-10:
                continue
            slope = (y[j]-y[i])/(x[j]-x[i])
            intercept = y[i]-slope*x[i]
            chosen = good[np.argsort(np.abs(y[good]-(intercept+slope*x[good])), kind='stable')[:count]]
            r = correlation(x[chosen], y[chosen], count)
            if r is not None and (best is None or abs(r) > abs(best['r'])):
                best = {'n': count, 'r': r, 'indices': sorted(int(v) for v in chosen),
                        'pair_indices': [int(i), int(j)]}
    return best


def kernel_matrix(x, z, gamma):
    dist = np.maximum(0, (x*x).sum(1)[:, None]+(z*z).sum(1)[None, :]-2*x@z.T)
    return np.exp(-gamma*dist/x.shape[1])


def nonlinear_demo(raw_x, y, train, test):
    a = train & np.isfinite(y) & np.isfinite(raw_x).all(1)
    b = test & np.isfinite(y) & np.isfinite(raw_x).all(1)
    mu, sd = raw_x[a].mean(0), raw_x[a].std(0)
    sd[sd < 1e-12] = 1
    x = (raw_x-mu)/sd
    target_mean = y[a].mean()
    best = None
    for gamma in [.01, .05, .2, 1, 5, 20]:
        k = kernel_matrix(x[a], x[a], gamma)
        for penalty in [1e-6, 1e-4, .01, .1, 1, 10]:
            weights = np.linalg.solve(k+penalty*np.eye(a.sum()), y[a]-target_mean)
            fitted = target_mean+k@weights
            m = score(y[a], fitted)
            if best is None or (m['r2'], m['r']) > (best['train']['r2'], best['train']['r']):
                best = {'gamma': gamma, 'penalty': penalty, 'train': m, 'weights': weights}
    pred = np.full(len(y), np.nan)
    available = np.isfinite(raw_x).all(1)
    pred[available] = target_mean+kernel_matrix(x[available], x[a], best['gamma'])@best.pop('weights')
    best['test'] = score(y[b], pred[b])
    best['train_dates_n'] = int(a.sum())
    best['hyperparameter_trials'] = 36
    best['inputs'] = ['N', 'delta_TGA', 'SOX_past_momentum_5', 'SOX_past_volatility_5']
    best['warning'] = 'Fitted nonlinear score, intentionally chosen for maximal training fit; not an independently observed factor.'
    return best, pred, a, b


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--production-snapshot', required=True)
    parser.add_argument('--plots', action='store_true')
    args = parser.parse_args()
    connection = sqlite3.connect('file:'+str(Path(args.database).resolve())+'?mode=ro', uri=True)
    rows = [json.loads(r[0]) for r in connection.execute('SELECT payload_json FROM sox_daily_inputs ORDER BY date')]
    connection.close()
    assert [r['date'] for r in rows] == sessions(rows[0]['date'], rows[-1]['date']), 'Calendar gaps must be explicit.'
    baseline = analyse(rows)
    production = json.loads(Path(args.production_snapshot).read_text())
    for kind in ['drawdown', 'rebound']:
        assert baseline['stats'][kind]['n'] == production['live']['stats'][kind]['n']
        assert abs(baseline['stats'][kind]['r']-production['live']['stats'][kind]['r']) < 1e-12
        columns = ['date', 'x', 'y', 'delta_tga_bn', 'net_issuance_bn']
        local_points = [{k: p[k] for k in columns} for p in baseline['points'][kind]]
        production_points = [{k: p[k] for k in columns} for p in production['live']['points'][kind]]
        assert local_points == production_points, 'Real local values must exactly match production (collection timestamps differ).'
    frame = pd.DataFrame(rows)
    derived = pd.DataFrame(baseline['daily'])
    y_values = {kind: derived[column].where(derived[status] == 'complete').to_numpy(dtype=float)
                for kind, column, status in [('drawdown', 'drawdown_pct', 'status4'), ('rebound', 'rebound_pct', 'status5')]}
    gates, kernel_inputs = build_features(frame)
    dates = frame.date.to_numpy()
    cutoff = int(np.flatnonzero(dates >= TEST_START)[0])
    feature_audit = audit_no_future_features(frame, cutoff)
    index = np.arange(len(rows))
    result = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'source_hash': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
              'source_dates': [rows[0]['date'], rows[-1]['date']], 'input_days': len(rows),
              'baseline_verified_against_production': True, 'raw_prices_exported': False,
              'feature_count': len(FEATURES), 'test_start': TEST_START,
              'feature_audit': feature_audit,
              'search_settings': {'windows': WINDOWS, 'lags': LAGS, 'weights': WEIGHTS,
                                  'weight_one_aliases_removed_by_definition': True,
                                  'full_sample_min_n': MIN_FULL, 'train_min_n': MIN_TRAIN,
                                  'threshold_quantiles': [.2, .3, .4, .5, .6, .7, .8]},
              'limitations': json.loads((OUT/'search-plan.json').read_text())['limitations'], 'targets': {}}
    matrix = pd.DataFrame(FEATURES)
    matrix.insert(0, 'date', dates)
    for kind, y in y_values.items():
        matrix['outcome_'+kind+'_pct'] = y
    matrix.to_csv(OUT/'factor-matrix.csv', index=False)
    (OUT/'factor-definitions.json').write_text(json.dumps(META, ensure_ascii=False, indent=2)+'\n')
    artifacts = {}
    print(json.dumps({'stage': 'features', 'unique_candidates': len(FEATURES), 'baseline': baseline['stats']}), flush=True)
    for kind, horizon in [('drawdown', 4), ('rebound', 5)]:
        y = y_values[kind]
        full = np.isfinite(y)
        train = full & (index+horizon < cutoff)
        test = full & (index >= cutoff)
        ranked = rank_features(y, full, MIN_FULL)
        train_rank = rank_features(y, train, MIN_TRAIN)
        best = ranked[0]
        treasury = next(r for r in ranked if r['family'].startswith('treasury') or r['family'] == 'weighted_treasury')
        full_leader_split, _, _, _ = linear_evaluation(FEATURES[best['feature']], y, train, test)
        treasury_leader_split, _, _, _ = linear_evaluation(FEATURES[treasury['feature']], y, train, test)
        selected = train_rank[0]
        validation, predictions, a, b = linear_evaluation(FEATURES[selected['feature']], y, train, test)
        full_gates, full_trials = gate_search(y, full, gates, 8)
        train_gates, train_trials = gate_search(y, train, gates, 10)
        gate = full_gates[0]
        continuous_gate = next(r for r in full_gates if r['family'] != 'calendar' and r['n'] >= 10
            and np.unique(FEATURES[r['feature']][full & chosen_gate(r, gates) & np.isfinite(FEATURES[r['feature']])]).size >= 5)
        for candidate in [gate, continuous_gate]:
            chosen = full & chosen_gate(candidate, gates) & np.isfinite(FEATURES[candidate['feature']])
            candidate['period_counts'] = {'train': int((chosen & train).sum()),
                'later_period': int((chosen & test).sum()), 'purged': int((chosen & ~train & ~test).sum())}
        gate_train = train_gates[0]
        gate_validation, gp, ga, gb = linear_evaluation(FEATURES[gate_train['feature']], y,
            train & chosen_gate(gate_train, gates), test & chosen_gate(gate_train, gates))
        base_feature = 'd_abs_sum_1_lag0' if kind == 'drawdown' else 'd_abs_sum_4_lag0'
        cherry = cherry_pick(FEATURES[base_feature], y)
        cherry['dates'] = [str(dates[i]) for i in cherry['indices']]
        cherry['feature'] = base_feature
        cherry['selection_uses_future_outcome'] = True
        nonlinear, npred, na, nb = nonlinear_demo(kernel_inputs, y, train, test)
        target = {'baseline': baseline['stats'][kind], 'full_sample_leader': best,
                  'full_sample_treasury_leader': treasury, 'top_10_full_sample': ranked[:10],
                  'full_leader_period_diagnostic': {**full_leader_split, 'selected_using_all_outcomes': True},
                  'treasury_leader_period_diagnostic': {**treasury_leader_split, 'selected_using_all_outcomes': True},
                  'eligible_full_sample_candidates': len(ranked),
                  'training_selected_factor': {**selected, **validation},
                  'full_sample_best_gate': gate, 'full_sample_gate_trials': full_trials,
                  'full_sample_best_noncalendar_gate': continuous_gate,
                  'best_gate_diagnostic': subset_diagnostic(FEATURES[gate['feature']], y, full & chosen_gate(gate, gates), dates),
                  'best_noncalendar_gate_diagnostic': subset_diagnostic(FEATURES[continuous_gate['feature']], y,
                      full & chosen_gate(continuous_gate, gates), dates),
                  'training_selected_gate': {**gate_train, **gate_validation}, 'training_gate_trials': train_trials,
                  'outcome_selected_eight_points': cherry, 'nonlinear_training_fit': nonlinear,
                  'split': {'train_events': int(train.sum()), 'test_events': int(test.sum()),
                            'purged_events': int((full & ~train & ~test).sum()),
                            'last_train_event': str(dates[train][-1]),
                            'first_test_event': str(dates[test][0]), 'last_test_event': str(dates[test][-1]),
                            'last_train_outcome': str(dates[np.flatnonzero(train)[-1]+horizon])}}
        assert target['split']['last_train_outcome'] < target['split']['first_test_event']
        changed_y = y.copy(); changed_y[test] = -17*changed_y[test]+123
        altered_rank = rank_features(changed_y, train, MIN_TRAIN)
        assert altered_rank[0] == selected, 'Later outcomes must not affect training-only factor selection.'
        target['training_selection_future_outcome_audit'] = 'passed'
        result['targets'][kind] = target
        pd.DataFrame(ranked).to_csv(OUT/f'{kind}-factor-ranking.csv', index=False)
        pd.DataFrame(full_gates[:25]).to_csv(OUT/f'{kind}-posthoc-gates.csv', index=False)
        mask = full & chosen_gate(gate, gates) & np.isfinite(FEATURES[gate['feature']])
        pd.DataFrame({'date': dates[mask], 'factor_x': FEATURES[gate['feature']][mask],
                      'outcome_pct': y[mask], 'gate_value': gates[gate['gate']][mask]}).to_csv(OUT/f'{kind}-best-posthoc-subset.csv', index=False)
        cm = full & chosen_gate(continuous_gate, gates) & np.isfinite(FEATURES[continuous_gate['feature']])
        pd.DataFrame({'date': dates[cm], 'factor_x': FEATURES[continuous_gate['feature']][cm],
                      'outcome_pct': y[cm], 'gate_value': gates[continuous_gate['gate']][cm]}).to_csv(OUT/f'{kind}-best-continuous-subset.csv', index=False)
        pd.DataFrame({'date': dates, 'outcome_pct': y, 'baseline_x': FEATURES[base_feature],
                      'best_full_sample_x': FEATURES[best['feature']],
                      'training_selected_x': FEATURES[selected['feature']],
                      'linear_training_model_prediction_pct': predictions,
                      'nonlinear_training_model_prediction_pct': npred,
                      'split': np.where(train, 'train', np.where(test, 'later-period', 'purged-or-incomplete')),
                      'eight_points_selected_using_outcome': np.isin(index, cherry['indices'])}).to_csv(OUT/f'{kind}-derived-observations.csv', index=False)
        artifacts[kind] = {'y': y, 'baseline_x': FEATURES[base_feature], 'best_x': FEATURES[best['feature']],
                           'nonlinear_pred': npred, 'nonlinear_train': na, 'nonlinear_test': nb,
                           'cherry': cherry, 'best': best, 'gate_x': FEATURES[gate['feature']], 'gate_mask': mask}
        print(json.dumps({'stage': kind, 'best': best, 'treasury': treasury,
                          'training_selected': selected, 'later_period': validation['test'],
                          'posthoc_gate': gate, 'cherry_r': cherry['r'], 'nonlinear': nonlinear}), flush=True)
    (OUT/'results.json').write_text(json.dumps(clean(result), ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    if args.plots:
        make_plots(result, artifacts)
    make_report(result)
    print(json.dumps({'result': 'completed', 'report': str(OUT/'report.html'), 'production_changed': False}), flush=True)


def make_plots(result, artifacts):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    colors = {'drawdown': '#2b77b8', 'rebound': '#bf6a22'}
    for kind, data in artifacts.items():
        y, x = data['y'], data['baseline_x']
        target = result['targets'][kind]
        good = np.isfinite(x) & np.isfinite(y)
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
        axes = axes.ravel()
        axes[0].scatter(x[good], y[good], color=colors[kind], alpha=.8, s=30)
        axes[0].set_title(f'Existing full sample\nn={good.sum()}, r={target["baseline"]["r"]:.3f}')
        axes[0].set_xlabel('Absolute mismatch (USD bn)')
        xx = data['best_x']; m = np.isfinite(xx) & np.isfinite(y)
        axes[1].scatter(xx[m], y[m], color=colors[kind], alpha=.8, s=30)
        axes[1].set_title(f'Best searched single factor\nn={m.sum()}, r={target["full_sample_leader"]["r"]:.3f}')
        axes[1].set_xlabel(data['best']['feature'], fontsize=8)
        gx, gm = data['gate_x'], data['gate_mask']
        gate = target['full_sample_best_gate']
        axes[2].scatter(gx[gm], y[gm], color='#85601b', s=48, alpha=.8)
        axes[2].set_title(f'Post-hoc X-only rule\nn={gm.sum()}, r={gate["r"]:.3f}; {target["full_sample_gate_trials"]:,} trials')
        axes[2].set_xlabel(gate['feature'], fontsize=8)
        if gate['feature'] == 'weekday_1':
            axes[2].set_xticks([0, 1], ['Other weekdays', 'Tuesday'])
        ids = data['cherry']['indices']
        axes[3].scatter(x[good], y[good], color='#bdc3cc', alpha=.5, s=24)
        axes[3].scatter(x[ids], y[ids], color='#b63243', s=48, edgecolor='white', linewidth=.5)
        fit = np.polyfit(x[ids], y[ids], 1)
        grid = np.linspace(min(x[ids]), max(x[ids]), 100)
        axes[3].plot(grid, np.polyval(fit, grid), color='#b63243', linewidth=1.4)
        axes[3].set_title(f'8 points selected using the outcome\nr={data["cherry"]["r"]:.3f}; NOT a usable selector')
        axes[3].set_xlabel('Same baseline mismatch (USD bn)')
        for ax in axes:
            ax.grid(alpha=.2)
            ax.set_ylabel('SOX '+('drawdown' if kind == 'drawdown' else 'rebound')+' (%)')
        fig.suptitle('Exploratory factor search — '+kind+' — 2026-07-23 to 2026-10-08', fontsize=13)
        fig.savefig(OUT/f'{kind}-search.png', dpi=150)
        fig.savefig(OUT/f'{kind}-search.svg')
        plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.5), constrained_layout=True)
    for ax, kind in zip(axes, ['drawdown', 'rebound']):
        data = artifacts[kind]
        a, b = data['nonlinear_train'], data['nonlinear_test']
        ax.scatter(data['y'][a], data['nonlinear_pred'][a], label='Training observations', color=colors[kind], s=35, alpha=.8)
        ax.scatter(data['y'][b], data['nonlinear_pred'][b], label='Later-period observations', color='#b63243', marker='x', s=50)
        values = np.r_[data['y'][a|b], data['nonlinear_pred'][a|b]]
        lo, hi = np.nanmin(values), np.nanmax(values)
        ax.plot([lo, hi], [lo, hi], '--', color='#8492a3', linewidth=1)
        m = result['targets'][kind]['nonlinear_training_fit']
        rr = m['test']['r']
        ax.set_title(f'{kind}: train r={m["train"]["r"]:.3f}, later r={rr:.3f}\nLater R²={m["test"]["r2"]:.3f}')
        ax.set_xlabel('Observed outcome (%)'); ax.set_ylabel('Fitted score / prediction (%)')
        ax.grid(alpha=.2); ax.legend(fontsize=8)
    fig.suptitle('Intentional nonlinear overfit: training fit versus later-period diagnostic', fontsize=12)
    fig.savefig(OUT/'nonlinear-overfit.png', dpi=150)
    fig.savefig(OUT/'nonlinear-overfit.svg')
    plt.close(fig)


def make_report(result):
    def f(v):
        return '—' if v is None else f'{v:.3f}'
    parts = []
    for kind, label in [('drawdown', '4일 낙폭'), ('rebound', '5일째 반등')]:
        t = result['targets'][kind]
        best, cash = t['full_sample_leader'], t['full_sample_treasury_leader']
        selected, gate, kernel = t['training_selected_factor'], t['full_sample_best_gate'], t['nonlinear_training_fit']
        best_split = t['full_leader_period_diagnostic']
        cash_split = t['treasury_leader_period_diagnostic']
        continuous = t['full_sample_best_noncalendar_gate']
        gd = t['best_gate_diagnostic']
        gate_caveat = ''
        if gd['leave_one_out_undefined_dates']:
            gate_caveat = '<p><b>최상위 조건의 편중:</b> '+html.escape(str(gd['value_counts_if_binary']))+'입니다. '+html.escape(', '.join(gd['leave_one_out_undefined_dates']))+'를 빼면 팩터가 상수가 되어 상관을 계산할 수 없습니다. 높은 수치가 한 사건에 의존하는 사례입니다.</p>'
        split = t['split']
        parts.append(f'''<section><h2>{label}</h2>
        <table><thead><tr><th>탐색 방식</th><th>상관 r</th><th>표본 수</th><th>해석 범위</th></tr></thead><tbody>
        <tr><td>기존 불일치 크기</td><td>{f(t['baseline']['r'])}</td><td>{t['baseline']['n']}</td><td>기존 전체 유효 표본</td></tr>
        <tr><td>단일 팩터 전체 탐색 최상위</td><td>{f(best['r'])}</td><td>{best['n']}</td><td>전체 결과를 보고 선택</td></tr>
        <tr><td>재무부 입력만 사용한 최상위</td><td>{f(cash['r'])}</td><td>{cash['n']}</td><td>전체 결과를 보고 선택</td></tr>
        <tr><td>X 조건으로 부분 표본을 고른 최상위</td><td>{f(gate['r'])}</td><td>{gate['n']}</td><td>{t['full_sample_gate_trials']:,}개 조건 조합 중 선택</td></tr>
        <tr><td>달력 제외 · X 값 5종 이상 · 10점 이상 조건</td><td>{f(continuous['r'])}</td><td>{continuous['n']}</td><td>연속적인 입력 팩터로 제한한 사후 탐색</td></tr>
        <tr><td>결과를 보고 8개 점을 고른 예시</td><td>{f(t['outcome_selected_eight_points']['r'])}</td><td>8</td><td>미래 결과를 알아야 선정 가능</td></tr>
        </tbody></table>
        <p><b>전체 탐색 최상위:</b> <code>{html.escape(best['feature'])}</code><br>{html.escape(best['formula'])}</p>
        <p><b>재무부 입력 최상위:</b> <code>{html.escape(cash['feature'])}</code><br>{html.escape(cash['formula'])}</p>
        <p><b>부분 표본 조건:</b> {html.escape(gate['gate'])} {html.escape(gate['direction'])} {gate['threshold']:.3f}, 팩터 {html.escape(gate['feature'])}</p>
        {gate_caveat}<p><b>달력 제외 조건:</b> {html.escape(continuous['gate'])} {html.escape(continuous['direction'])} {continuous['threshold']:.3f}, 팩터 {html.escape(continuous['feature'])}. 한 점씩 뺀 r 범위는 {f(t['best_noncalendar_gate_diagnostic']['leave_one_out_r_min'])}~{f(t['best_noncalendar_gate_diagnostic']['leave_one_out_r_max'])}입니다. 이 검사도 사후 선정 편향을 없애지는 않습니다. 이 조건에 해당하는 후기 표본은 {continuous['period_counts']['later_period']}개입니다.</p>
        <h3>전체 탐색에서 찾은 후보의 기간별 관계</h3>
        <table><thead><tr><th>후보</th><th>앞쪽 원래 r / n</th><th>후기 원래 r / n</th><th>앞쪽 회귀식의 후기 R²</th></tr></thead><tbody>
        <tr><td>전체 탐색 최상위</td><td>{f(best_split['train_raw_r'])} / {best_split['train']['n']}</td><td>{f(best_split['test_raw_r'])} / {best_split['test']['n']}</td><td>{f(best_split['test']['r2'])}</td></tr>
        <tr><td>재무부 입력 최상위</td><td>{f(cash_split['train_raw_r'])} / {cash_split['train']['n']}</td><td>{f(cash_split['test_raw_r'])} / {cash_split['test']['n']}</td><td>{f(cash_split['test']['r2'])}</td></tr>
        </tbody></table><p>이 두 후보는 전체 결과를 보고 골랐습니다. 위 기간별 수치는 재현성 진단이며 독립 검증 성적으로 읽지 않습니다. 후기에도 관계가 남는 후보를 모두 실패한 것으로 처리하지 않았습니다.</p>
        <h3>앞쪽 기간에서 고른 뒤 고정한 결과</h3>
        <p>학습 이벤트는 {split['last_train_event']}까지, 결과 관측은 {split['last_train_outcome']}까지입니다. 후기 이벤트는 {split['first_test_event']}~{split['last_test_event']}입니다. 경계에서 {split['purged_events']}개 이벤트를 제외했습니다.</p>
        <table><thead><tr><th>학습에서 선택한 모델</th><th>학습 r / n</th><th>후기 r / n</th><th>후기 예측 R²</th></tr></thead><tbody>
        <tr><td>단일 팩터: {html.escape(selected['feature'])}</td><td>{f(selected['train']['r'])} / {selected['train']['n']}</td><td>{f(selected['test']['r'])} / {selected['test']['n']}</td><td>{f(selected['test']['r2'])}</td></tr>
        <tr><td>학습에서 고른 X 조건 + 팩터</td><td>{f(t['training_selected_gate']['train']['r'])} / {t['training_selected_gate']['train']['n']}</td><td>{f(t['training_selected_gate']['test']['r'])} / {t['training_selected_gate']['test']['n']}</td><td>{f(t['training_selected_gate']['test']['r2'])}</td></tr>
        <tr><td>의도적으로 유연하게 맞춘 비선형 합성 점수</td><td>{f(kernel['train']['r'])} / {kernel['train']['n']}</td><td>{f(kernel['test']['r'])} / {kernel['test']['n']}</td><td>{f(kernel['test']['r2'])}</td></tr>
        </tbody></table>
        <p>위 학습/후기 r은 고정한 모델의 예측값과 실제 결과 간 상관입니다. 전체 탐색 팩터의 원래 부호 있는 r과 구분합니다. R²는 후기 실제 평균을 기준으로 계산하며 음수이면 그 평균보다 제곱오차가 큽니다.</p>
        <img src="{kind}-search.png" alt="{label} 전체 표본, 탐색 팩터 및 결과를 보고 고른 8점 비교">
        </section>''')
    limits = ''.join(f'<li>{html.escape(x)}</li>' for x in [
        '이번 탐색은 사용자가 요청한 과적합 실험입니다. 과거 탐색 표본과 운영 차트는 변경하지 않았습니다.',
        '자료는 실제 수집된 55거래일입니다. 원본 14점은 학습에 합치지 않았고, 새 표본의 기준 통계와 좌표를 운영 API와 대조했습니다.',
        '전체 표본의 결과를 보고 팩터·기간·임곗값을 선택한 수치는 사후 탐색 성적입니다. 높은 r만으로 재현성이 확인되는 것은 아닙니다.',
        '앞쪽 기간에서 선택한 결과도 엄밀한 미래 실험이 아닌 기간 분리 진단입니다. 이미 전체 상관을 본 자료이며 관측창도 중첩됩니다.',
        'Treasury 입력은 사후 조회·수정 자료입니다. 이벤트 당시 발표되어 있었는지까지 재현한 매매 백테스트가 아닙니다.',
        '쿠폰 차감 순유출액의 과거 연속 자료가 없어 해당 팩터는 이번에 검증하지 않았습니다.',
        '결측은 결측으로 유지했습니다. 미래 SOX를 입력 팩터로 쓰지 않았습니다. 결과 기반 8점 선정은 별도의 명시적 누설 예시입니다.',
        '원시 SOX 종가는 내보내지 않았습니다. CSV에는 파생 팩터와 반응률만 저장했습니다.'
    ])
    report = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>SOX 팩터 탐색과 과적합 실험</title><style>
    body{{font:15px/1.75 system-ui,sans-serif;color:#192b3b;background:#f2f5f8;margin:0}}main{{max-width:1100px;margin:auto;padding:35px 24px 60px}}h1{{font-size:29px;letter-spacing:-.04em}}h2{{font-size:23px}}h3{{font-size:18px}}section{{background:white;padding:25px;border-radius:12px;border:1px solid #dce3eb;margin:24px 0}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #dce3eb;font-size:13px}}th{{background:#f0f4f9}}code{{overflow-wrap:anywhere;font-size:12px}}img{{display:block;width:100%;height:auto;margin:22px 0}}.meta{{color:#52667b}}.scroll{{overflow-x:auto}}a{{color:#1764a6}}@media(max-width:700px){{main{{padding:20px 12px}}section{{padding:16px}}table{{min-width:660px}}}}
    </style><main><h1>SOX 팩터 탐색과 과적합 실험</h1>
    <p class="meta">2026-10-10 · 개인 비상업 연구 · 자료 {result['source_dates'][0]}~{result['source_dates'][1]} · 서로 다른 후보 {result['feature_count']:,}개 · 목표는 기존 4일 낙폭과 5일째 반등</p>
    <p>단일 팩터, 부분 표본 선택, 유연한 비선형 합성 점수가 어느 정도의 표본 내 상관을 만들 수 있는지 비교했습니다. 앞쪽에서 선택한 모델을 2026-09-14 이후 기간에 고정해서 적용한 결과도 함께 기록했습니다.</p>
    <p><b>기호:</b> N은 SOMA를 제외한 국채 액면 순발행 대용치, ΔTGA는 잔액 증감, d는 ΔTGA−N입니다. <code>lag 5</code>는 5거래일 전까지의 값, <code>sum_abs</code>는 일별 절댓값의 합, <code>abs_sum</code>은 부호 있는 합의 절댓값입니다. 금액은 십억 달러, SOX 변동성은 과거 일간 로그수익률(%)의 표준편차입니다.</p>
    <ul>{limits}</ul>{''.join(parts)}
    <section><h2>유연한 모델이 표본을 외우는 경우</h2><p>순발행·TGA 증감·과거 SOX 5일 수익률·과거 변동성의 네 입력을 비선형으로 조합했습니다. 표준화와 모델 선택은 학습 자료에서 수행했습니다. 36개 설정 중 학습 적합도를 최대로 만드는 설정을 선택했습니다.</p><img src="nonlinear-overfit.png" alt="의도적인 비선형 과적합의 학습과 후기 성적"></section>
    <p>절차 참고: <a href="https://scikit-learn.org/stable/common_pitfalls.html#data-leakage">자료 누설과 팩터 선택</a> · <a href="https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html">시간 순서 및 구간 간격</a></p>
    <p>감사 검사: 미래 입력을 바꿔도 앞쪽 {result['feature_audit']['feature_vectors_checked']}개 팩터 값이 그대로이며, 후기 결과를 바꿔도 앞쪽 팩터 선택은 그대로였습니다. 학습 결과창과 후기 시작일 사이 겹침도 없음을 확인했습니다.</p>
    <p>상세 기록: <a href="results.json">결과 JSON</a> · <a href="drawdown-factor-ranking.csv">낙폭 팩터 순위 CSV</a> · <a href="rebound-factor-ranking.csv">반등 팩터 순위 CSV</a> · <a href="factor-matrix.csv">파생 팩터 전체 행렬</a> · <a href="factor-definitions.json">팩터 정의</a></p>
    </main></html>'''
    report = report.replace('<table>', '<div class="scroll"><table>').replace('</table>', '</table></div>')
    (OUT/'report.html').write_text(report)


if __name__ == '__main__':
    main()
