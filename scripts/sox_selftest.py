#!/usr/bin/env python3
"""Offline regression tests; no credentials or network required."""
import copy
import json
import math
from datetime import date, timedelta
from pathlib import Path
import unittest
from sox_model import analyse, regression, numeric, sessions, previous_day, business_day, input_hash, MODEL_ID
from sox_sources import DataError, normalize_auction, merge_auctions, net_public_face, parse_sox_csv, tga_values
from sox_daily import assemble_daily

ROOT=Path(__file__).resolve().parents[1]


def row(d,tga=100,prior=100,n=0,price=100):
    return {'date':d,'tga_bn':tga,'prior_tga_bn':prior,'net_issuance_bn':n,'sox_close':price,'model_id':MODEL_ID}


class ModelTests(unittest.TestCase):
    def test_original_correlations(self):
        ref=json.loads((ROOT/'research/sox-reference/reference-points.json').read_text())
        for kind,expected,n in [('drawdown',0.961485490260395,6),('rebound',0.9153197872520986,8)]:
            s=regression(ref['points'][kind]);self.assertAlmostEqual(s['r'],expected,places=12);self.assertEqual(s['n'],n)
    def test_missing_is_not_zero(self):
        for x in [None,'', 'null', '.', float('nan'), True]:self.assertIsNone(numeric(x))
        self.assertEqual(numeric(0),0)
    def test_sum_before_absolute(self):
        ds=sessions('2026-09-21','2026-09-24')
        a=analyse([row(d,100+v,100,0) for d,v in zip(ds,[10,-8,7,-6])])
        self.assertEqual(a['daily'][-1]['u4_bn'],3)
    def test_missing_breaks_rolling_window(self):
        ds=sessions('2026-09-21','2026-09-25');rs=[row(d,110) for d in ds];rs[2]['net_issuance_bn']=None
        self.assertIsNone(analyse(rs)['daily'][-1]['u4_bn'])
    def test_missing_calendar_row_not_compressed(self):
        ds=sessions('2026-09-21','2026-09-28');rs=[row(d,110) for d in ds if d!='2026-09-23']
        a=analyse(rs);self.assertIsNone(a['daily'][4]['u4_bn']);self.assertFalse(a['points']['drawdown'])
    def test_outcomes_and_pending(self):
        ds=sessions('2026-09-21','2026-09-28');rs=[row(d,110,100,0,p) for d,p in zip(ds,[100,95,90,97,92,88])]
        a=analyse(rs)['daily'][0];self.assertAlmostEqual(a['drawdown_pct'],10);self.assertAlmostEqual(a['rebound_pct'],(88/90-1)*100)
        self.assertEqual(a['status4'],'complete')
        self.assertIsNone(analyse(rs[:-1])['daily'][0]['rebound_pct'])
    def test_no_future_leakage(self):
        ds=sessions('2026-09-21','2026-10-02');rs=[row(d,100+i,100,0,100-i) for i,d in enumerate(ds)]
        short=analyse(rs[:5]);full=analyse(rs)
        self.assertEqual(short['daily'][0]['drawdown_pct'],full['daily'][0]['drawdown_pct'])
        self.assertEqual([r['u1_bn'] for r in short['daily']],[r['u1_bn'] for r in full['daily'][:5]])
        self.assertIsNone(short['daily'][0]['rebound_pct'])
    def test_no_r_for_constant_or_tiny_sample(self):
        self.assertIsNone(regression([{'x':1,'y':1}])['r'])
        self.assertIsNone(regression([{'x':1,'y':i} for i in range(5)])['r'])
    def test_calendars_differ(self):
        self.assertTrue(business_day(date(2026,7,3),'fed'));self.assertFalse(business_day(date(2026,7,3),'market'))
        self.assertFalse(business_day(date(2026,10,12),'fed'));self.assertTrue(business_day(date(2026,10,12),'market'))
        self.assertFalse(business_day(date(2026,4,3),'market'));self.assertTrue(business_day(date(2026,4,3),'fed'))
    def test_maturity_not_multiplied(self):
        d='2026-09-24';base={'issue_date':d,'maturity_date':'2026-12-24','bucket':'bill','accepted':100e9,'soma':10e9,'public_maturing':260e9,'maturing_date':d}
        n,s=net_public_face(d,[base,base,base],{d:{'bill'}},'2026-08-31',True);self.assertEqual(n,10)
    def test_weekend_announcement_maturity_uses_actual_payment_day(self):
        amounts=[(31323533800,6323518500),(52623557100,10623511200),(72670580300,14670563200)]
        for maturity,payment,snapshot in [('2026-08-15','2026-08-17','2026-07-31'),('2026-10-10','2026-10-13','2026-09-30')]:
            with self.subTest(payment=payment):
                rows=[{'issue_date':payment,'maturity_date':'2027-08-15','bucket':'coupon','accepted':accepted,'soma':soma,'public_maturing':96267000000,'maturing_date':maturity} for accepted,soma in amounts]
                value,status=net_public_face(payment,rows,{payment:{'coupon'}},snapshot,True)
                self.assertEqual(value,28.7330783)
                self.assertEqual(status,'public_face_minus_official_maturity')
    def test_unknown_soma_blocks(self):
        d='2026-09-24';r={'issue_date':d,'bucket':'bill','accepted':100e9,'soma':None,'maturing_date':d,'public_maturing':0}
        self.assertIsNone(net_public_face(d,[r],{},'2026-08-31',True)[0])
    def test_conflicting_maturities_block(self):
        d='2026-09-24';r={'issue_date':d,'bucket':'bill','accepted':100e9,'soma':0,'maturing_date':d,'public_maturing':260e9}
        self.assertIsNone(net_public_face(d,[r,{**r,'public_maturing':261e9}],{d:{'bill'}},'2026-08-31',True)[0])
    def test_empty_auctions_not_zero_without_inventory(self):
        d='2026-09-24'
        self.assertIsNone(net_public_face(d,[],{d:{'bill'}},'2026-08-31',True)[0])
        self.assertIsNone(net_public_face(d,[],{},'2026-09-30',True)[0])
        self.assertIsNone(net_public_face(d,[],{},'2026-08-31',False)[0])
        self.assertEqual(net_public_face(d,[],{},'2026-08-31',True)[0],0)
    def test_missing_bucket_blocks(self):
        d='2026-09-24';r={'issue_date':d,'maturity_date':'2027-01-24','bucket':'bill','accepted':100e9,'soma':0,'maturing_date':d,'public_maturing':90e9}
        self.assertIsNone(net_public_face(d,[r],{d:{'bill','coupon'}},'2026-08-31',True)[0])
    def test_real_zero_is_valid(self):
        d='2026-09-25';r={'issue_date':d,'bucket':'coupon','accepted':28e9,'soma':0,'maturing_date':d,'public_maturing':0}
        self.assertEqual(net_public_face(d,[r],{},'2026-08-31',True)[0],28)
    def test_new_security_weekend_maturity_is_not_zero(self):
        d='2026-09-28'
        earlier={'issue_date':'2026-09-01','maturity_date':'2026-09-26','bucket':'coupon','accepted':10e9,'soma':0,'public_maturing':None,'maturing_date':None}
        settling={'issue_date':d,'maturity_date':'2027-09-28','bucket':'coupon','accepted':20e9,'soma':0,'public_maturing':None,'maturing_date':None}
        self.assertIsNone(net_public_face(d,[earlier,settling],{},'2026-08-31',True)[0])
    def test_new_security_holiday_maturity_is_not_zero(self):
        d='2026-10-13'
        earlier={'issue_date':'2026-10-01','maturity_date':'2026-10-10','bucket':'coupon','accepted':10e9,'soma':0,'public_maturing':None,'maturing_date':None}
        settling={'issue_date':d,'maturity_date':'2027-10-13','bucket':'coupon','accepted':20e9,'soma':0,'public_maturing':None,'maturing_date':None}
        self.assertIsNone(net_public_face(d,[earlier,settling],{},'2026-09-30',True)[0])
    def test_fred_missing_csv_not_zero(self):
        prices=parse_sox_csv('observation_date,NASDAQSOX\n2026-09-21,100\n2026-09-22,.\n',allowed_start='2026-09-01',allowed_end='2026-09-30')
        self.assertEqual(prices,{'2026-09-21':100})
    def test_tga_units_and_closing_row(self):
        v=tga_values([{'record_date':'2026-09-21','account_type':'Treasury General Account (TGA) Closing Balance','open_today_bal':'985932'}],{'dataFormats':{'open_today_bal':'$1,000,000'}})
        self.assertEqual(v['2026-09-21'],985.932)
    def test_actual_fiscal_auction_formats_preserve_usd(self):
        # Captured public API formats: the currency display example is not
        # a multiplier. Accepted minus SOMA is $58.0000012bn.
        raw={'issue_date':'2026-10-15','maturity_date':'2056-08-15','security_type':'Bond','total_accepted':'59377561000','soma_accepted':'1377559800','offering_amt':'58000000000'}
        meta={'dataFormats':{'total_accepted':'10.2','soma_accepted':'10.2','offering_amt':'$1,000,000'}}
        normalized=normalize_auction(raw,meta)
        self.assertEqual(normalized['accepted'],59377561000)
        self.assertEqual(normalized['soma'],1377559800)
        self.assertEqual(normalized['offering'],58000000000)
        self.assertEqual(net_public_face('2026-10-15',[normalized],{},'2026-09-30',True)[0],58.0000012)
    def test_tga_unknown_unit_blocks(self):
        with self.assertRaises(DataError):tga_values([{'record_date':'2026-09-21','account_type':'Treasury General Account (TGA) Closing Balance','open_today_bal':'5'}],{'dataFormats':{'open_today_bal':'$100'}})
    def test_stock_only_holiday_fiscal_flows_included(self):
        t={'2026-07-02':100,'2026-07-03':112,'2026-07-06':115};n={'2026-07-03':(8,'actual'),'2026-07-06':(4,'actual')}
        r=assemble_daily(t,n,{},[],start='2026-07-06',end='2026-07-06',seen_at='2026-07-07T22:00:00Z',extra_closed=[])[0]
        self.assertEqual(r['net_issuance_bn'],12);self.assertEqual(r['prior_tga_bn'],100)
    def test_federal_holiday_explicit_asof(self):
        t={'2026-10-09':100};r=assemble_daily(t,{}, {},[],start='2026-10-12',end='2026-10-12',seen_at='2026-10-13T22:00:00Z',extra_closed=[])[0]
        self.assertEqual(r['net_issuance_bn'],0);self.assertEqual(r['tga_source_date'],'2026-10-09');self.assertIn('FED_CLOSED',r['issues'][0])
    def test_hash_excludes_refresh_time(self):
        r=row('2026-09-22');self.assertEqual(input_hash({**r,'updated_at':'a'}),input_hash({**r,'updated_at':'b'}))
    def test_new_snapshot_keeps_previously_verified_day(self):
        old={**row('2026-09-22',100,90,5),'net_status':'public_face_minus_official_maturity','first_complete_seen_at':'2026-09-23T22:00:00Z'}
        r=assemble_daily({}, {},{},[old],start='2026-09-22',end='2026-09-22',seen_at='2026-10-10T22:00:00Z',extra_closed=[])[0]
        self.assertEqual(r['net_issuance_bn'],5);self.assertEqual(r['first_complete_seen_at'],old['first_complete_seen_at'])


if __name__=='__main__':unittest.main(verbosity=2)
