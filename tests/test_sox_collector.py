"""Orchestrator integration on fixture HTTP/D1, not a claim of live network connectivity."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sox_daily
from sox_model import sessions
from sox_sources import DataError, TGA_PATH, AUCTIONS_PATH, MSPD_PATH

class Clock(datetime):
    @classmethod
    def now(cls,tz=None):
        d=cls(2026,10,8,22,tzinfo=timezone.utc)
        return d.astimezone(tz) if tz else d

class MockHttp:
    state={'inputs':[],'auctions':[],'status':None}
    report=None
    fiscal_calls=0
    def __init__(self,*a,**k):pass
    def json(self,url,body=None,token=None):
        if url.endswith('/api/sox/state'):return copy.deepcopy(self.state)
        if 'fields=record_date' in url:return {'data':[{'record_date':'2026-09-30'}]}
        if url.endswith('/api/sox/ingest'):
            for r in (body or {}).get('inputs',[]):
                self.state['inputs']=[x for x in self.state['inputs'] if x['date']!=r['date']]+[copy.deepcopy(r)]
            if 'report' in body:type(self).report=copy.deepcopy(body['report'])
            if 'status' in body:self.state['status']=copy.deepcopy(body['status'])
            return {'ok':True}
        raise AssertionError(url)
    def fiscal(self,path,params,**k):
        type(self).fiscal_calls+=1
        if path==TGA_PATH:return ([{'record_date':d,'account_type':'Treasury General Account (TGA) Closing Balance','open_today_bal':str(900000+i*1000)} for i,d in enumerate(sessions('2026-09-30','2026-10-07','fed'))],{'dataFormats':{'open_today_bal':'$1,000,000'}})
        if path==MSPD_PATH:return ([{'record_date':'2026-09-30','security_class1_desc':'Treasury Notes','maturity_date':f'2027-{m:02d}-15'} for m in range(1,12)],{})
        if path==AUCTIONS_PATH:return ([{'cusip':'91282TEST','auction_date':'2026-10-01','issue_date':'2026-10-15','maturity_date':'2027-10-15','security_type':'Note','offering_amt':'1000000000','total_accepted':None,'soma_accepted':None}],{})
        raise AssertionError(path)
    def request(self,url,**k):
        return ('observation_date,NASDAQSOX\n'+'\n'.join(f'{d},{1000+i*3}' for i,d in enumerate(sessions('2026-09-30','2026-10-08')))).encode()

class CollectorTests(unittest.TestCase):
    def setUp(self):
        MockHttp.state={'inputs':[],'auctions':[],'status':None};MockHttp.report=None;MockHttp.fiscal_calls=0
    def config_dir(self,tmp,allowed):
        p=Path(tmp);(p/'config').mkdir(exist_ok=True)
        c={'model_id':'tga-public-face-residual-v1','update_after_ny_hour':18,'source_lookback_days':80,'extra_market_closures':[],'sox':{'provider':'fred','public_use_confirmed':allowed}}
        (p/'config/sox-model.json').write_text(json.dumps(c));return p
    def test_default_license_gate_and_once_daily(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'TREASURY_INGEST_URL':'https://fixture.invalid/api/treasury-ingest','TREASURY_INGEST_TOKEN':'fake'}), patch.object(sox_daily,'HttpClient',MockHttp),patch.object(sox_daily,'datetime',Clock):
            with patch.object(sox_daily,'ROOT',self.config_dir(tmp,False)):
                r=sox_daily.collect();self.assertEqual(r['status'],'LICENSE_REQUIRED')
                self.assertEqual(r['drawdown_points'],0);self.assertGreater(r['stored_days'],0)
                self.assertIsNotNone(MockHttp.report['latest_macro']['u4_bn'])
                calls=MockHttp.fiscal_calls;r=sox_daily.collect();self.assertEqual(r['status'],'SKIPPED_ALREADY_UPDATED');self.assertEqual(MockHttp.fiscal_calls,calls)
    def test_config_change_and_later_close_matures_outcome(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'TREASURY_INGEST_URL':'https://fixture.invalid/api/treasury-ingest','TREASURY_INGEST_TOKEN':'fake'}),patch.object(sox_daily,'HttpClient',MockHttp),patch.object(sox_daily,'datetime',Clock):
            root=self.config_dir(tmp,False)
            with patch.object(sox_daily,'ROOT',root):
                sox_daily.collect();self.config_dir(tmp,True);r=sox_daily.collect()
                self.assertEqual(r['status'],'READY');self.assertGreater(r['drawdown_points'],0)
                self.assertEqual(MockHttp.report['analysis_through'],'2026-10-08')
                self.assertEqual(MockHttp.report['latest_macro']['date'],'2026-10-07')
                self.assertIsNone(MockHttp.report['recent'][0]['u1_bn'])

    def test_tga_failure_after_config_change_is_retried(self):
        class FailedTGA(MockHttp):
            def fiscal(self,path,params,**kwargs):
                if path==TGA_PATH:
                    raise DataError('fixture TGA outage')
                return super().fiscal(path,params,**kwargs)
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'TREASURY_INGEST_URL':'https://fixture.invalid/api/treasury-ingest','TREASURY_INGEST_TOKEN':'fake'}),patch.object(sox_daily,'HttpClient',MockHttp),patch.object(sox_daily,'datetime',Clock):
            root=self.config_dir(tmp,False)
            with patch.object(sox_daily,'ROOT',root):
                sox_daily.collect()
                previous_report=copy.deepcopy(MockHttp.report)
                self.config_dir(tmp,True)
                with patch.object(sox_daily,'HttpClient',FailedTGA):
                    with self.assertRaises(DataError):
                        sox_daily.collect()
                self.assertEqual(MockHttp.state['status']['state'],'SOURCE_ERROR')
                self.assertEqual(MockHttp.report,previous_report)
                self.assertEqual(sox_daily.collect()['status'],'READY')

    def test_partial_snapshot_after_config_change_is_retried(self):
        class FailedPrincipal(MockHttp):
            def fiscal(self,path,params,**kwargs):
                if path==MSPD_PATH:
                    raise DataError('fixture MSPD outage')
                return super().fiscal(path,params,**kwargs)
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'TREASURY_INGEST_URL':'https://fixture.invalid/api/treasury-ingest','TREASURY_INGEST_TOKEN':'fake'}),patch.object(sox_daily,'HttpClient',MockHttp),patch.object(sox_daily,'datetime',Clock):
            root=self.config_dir(tmp,False)
            with patch.object(sox_daily,'ROOT',root):
                sox_daily.collect()
                self.config_dir(tmp,True)
                with patch.object(sox_daily,'HttpClient',FailedPrincipal):
                    self.assertEqual(sox_daily.collect()['status'],'PARTIAL')
                self.assertEqual(sox_daily.collect()['status'],'READY')

    def test_historical_snapshots_backfill_new_sample_and_next_day_updates_outcomes(self):
        class HistoricalHttp(MockHttp):
            through='2026-10-07'
            close_through='2026-10-08'
            def fiscal(self,path,params,**kwargs):
                if path==TGA_PATH:
                    days=sessions('2026-07-22',self.through,'fed')
                    return ([{'record_date':d,'account_type':'Treasury General Account (TGA) Closing Balance','open_today_bal':str(900000+i*i*100)} for i,d in enumerate(days)],{})
                if path==MSPD_PATH:
                    snapshots=['2026-06-30','2026-07-31','2026-08-31','2026-09-30']
                    if params.get('filter','').startswith('record_date:eq:'):
                        snapshots=[params['filter'].split(':')[-1]]
                    return ([{'record_date':snapshot,'security_class1_desc':'Treasury Notes','maturity_date':f'2027-{m:02d}-15'} for snapshot in snapshots for m in range(1,12)],{})
                if path==AUCTIONS_PATH:
                    rows,meta=super().fiscal(path,params,**kwargs)
                    bill={'cusip':'912797NEW','auction_date':'2026-07-09','issue_date':'2026-07-10','maturity_date':'2026-07-27','security_type':'Bill','total_accepted':'1000000000','soma_accepted':'0'}
                    if bill['issue_date']>=params['filter'].split(':')[-1]:
                        rows.append(bill)
                    return rows,meta
                return super().fiscal(path,params,**kwargs)
            def request(self,url,**kwargs):
                return ('date,close\n'+'\n'.join(f'{d},{1000+i*3+(i%4)*2}' for i,d in enumerate(sessions('2026-07-22',self.close_through)))).encode()
        class NextDay(Clock):
            @classmethod
            def now(cls,tz=None):
                d=cls(2026,10,9,22,tzinfo=timezone.utc)
                return d.astimezone(tz) if tz else d
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'TREASURY_INGEST_URL':'https://fixture.invalid/api/treasury-ingest','TREASURY_INGEST_TOKEN':'fake'}),patch.object(sox_daily,'HttpClient',HistoricalHttp),patch.object(sox_daily,'datetime',Clock):
            with patch.object(sox_daily,'ROOT',self.config_dir(tmp,True)):
                first=sox_daily.collect()
                self.assertEqual(HistoricalHttp.report['analysis_from'],'2026-07-23')
                self.assertEqual(HistoricalHttp.report['latest_macro']['date'],'2026-10-07')
                self.assertGreater(first['drawdown_points'],30)
                self.assertTrue(all(r['date']>'2026-07-22' for r in MockHttp.state['inputs']))
                early=copy.deepcopy(next(r for r in MockHttp.state['inputs'] if r['date']=='2026-07-23'))
                self.assertEqual(early['net_issuance_bn'],0)
                self.assertIsNone(next(r for r in MockHttp.state['inputs'] if r['date']=='2026-07-27')['net_issuance_bn'])
                HistoricalHttp.through='2026-10-08';HistoricalHttp.close_through='2026-10-09'
                with patch.object(sox_daily,'datetime',NextDay):
                    second=sox_daily.collect()
                self.assertEqual(second['status'],'READY')
                self.assertEqual(second['drawdown_points'],first['drawdown_points']+1)
                self.assertEqual(second['rebound_points'],first['rebound_points']+1)
                self.assertEqual(HistoricalHttp.report['latest_macro']['date'],'2026-10-08')
                self.assertEqual(next(r for r in MockHttp.state['inputs'] if r['date']=='2026-07-23')['input_hash'],early['input_hash'])
                self.assertEqual(len({r['date'] for r in MockHttp.state['inputs']}),len(MockHttp.state['inputs']))

if __name__=='__main__':unittest.main(verbosity=2)
