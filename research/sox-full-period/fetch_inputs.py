#!/usr/bin/env python3
"""Read official historical inputs for the full-period single-factor study.

Private closes/cache stay outside the repository. No ingest or DB writes.
"""
import argparse
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
WARMUP = '2026-02-02'
sys.path.insert(0, str(ROOT/'scripts'))
from sox_daily import assemble_daily
from sox_model import sessions, business_day, previous_day
from sox_sources import (HttpClient, TGA_PATH, MSPD_PATH, AUCTIONS_PATH,
                        tga_values, inventory_maturities, merge_auctions, net_public_face, parse_sox_csv)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--private-dir', required=True)
    args = p.parse_args()
    out = Path(args.private_dir).resolve()
    if out.is_relative_to(ROOT):
        raise SystemExit('Raw closes must remain outside the repository.')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    ny = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    day = ny.date().isoformat()
    closed = day if ny.hour >= 16 and business_day(ny.date()) else previous_day(day)
    stamp = datetime.now(timezone.utc).isoformat()

    class Cached(HttpClient):
        def __init__(self):
            super().__init__(deadline_seconds=240)
        def request(self, url, **kwargs):
            assert not kwargs, 'Only unauthenticated read requests are supported.'
            path = out/('source-'+hashlib.sha256(url.encode()).hexdigest()+'.bin')
            if path.exists():
                return path.read_bytes()
            data = super().request(url)
            path.write_bytes(data)
            return data

    requests = {
        'tga': lambda: Cached().fiscal(TGA_PATH, {'filter':'record_date:gte:2026-01-01','sort':'record_date'}),
        'inventory': lambda: Cached().fiscal(MSPD_PATH, {'filter':f'record_date:gte:2026-01-31,record_date:lte:{day}','sort':'record_date'}),
        'auctions': lambda: Cached().fiscal(AUCTIONS_PATH, {'filter':'issue_date:gte:2026-02-01','sort':'issue_date'}),
        'prices': lambda: Cached().request('https://fred.stlouisfed.org/graph/fredgraph.csv?'+urllib.parse.urlencode({'id':'NASDAQSOX','cosd':WARMUP,'coed':day}))
    }
    loaded = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {k: pool.submit(fn) for k,fn in requests.items()}
        for k, task in futures.items():
            loaded[k] = task.result()
            print(json.dumps({'stage':'source_received','source':k,'count_or_bytes':len(loaded[k][0]) if k!='prices' else len(loaded[k])}), flush=True)
    tga = tga_values(*loaded['tga'])
    latest_tga = min(max(tga), day)
    monthly = {}
    for row in loaded['inventory'][0]:
        monthly.setdefault(row['record_date'], []).append(row)
    inventories = {d:inventory_maturities(rows) for d,rows in monthly.items()}
    snapshots = sorted(inventories)
    auctions = merge_auctions(*loaded['auctions'], [])
    net = {}
    for d in sessions(WARMUP, latest_tga, kind='fed'):
        j = bisect_left(snapshots, d)-1
        assert j>=0, 'Prior inventory required; never infer zero.'
        s = snapshots[j]
        net[d] = net_public_face(d, auctions, inventories[s], s, universe_complete=True)
    prices = parse_sox_csv(loaded['prices'].decode('utf-8-sig'), allowed_start=WARMUP, allowed_end=closed)
    rows = assemble_daily(tga,net,prices,[],start=WARMUP,end=closed,seen_at=stamp,extra_closed=[])
    (out/'raw-inputs.json').write_text(json.dumps(rows,allow_nan=False))
    meta = {'fetched_at':stamp,'requested_from':'2026-07-20','requested_through':closed,
            'warmup_from':WARMUP,'latest_tga':latest_tga,'latest_sox':max(prices),
            'inventory_snapshots':snapshots,'auction_rows':len(auctions),'private_rows':len(rows),
            'event_sessions':len([r for r in rows if r['date']>='2026-07-20']),
            'missing_macro_event_dates':[r['date'] for r in rows if r['date']>='2026-07-20' and any(r[k] is None for k in ['tga_bn','prior_tga_bn','net_issuance_bn'])],
            'source_urls':['https://api.fiscaldata.treasury.gov/services/api/fiscal_service'+x for x in [TGA_PATH,MSPD_PATH,AUCTIONS_PATH]]+['https://fred.stlouisfed.org/series/NASDAQSOX']}
    (out/'source-metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps({'stage':'completed',**meta}), flush=True)


if __name__=='__main__':
    main()
