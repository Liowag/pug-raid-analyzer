from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock, Event
from unittest import TestCase
from unittest.mock import patch, Mock
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from analyzer import ROOT, load_test_snapshot
from analysis_options import normalize
from analysis_jobs import run_configured
from scoring_data import load_histories, load_higher
from api_usage import current_usage, UsageLedger


class DetailParallelTests(TestCase):
    def test_four_history_calls_overlap_and_keep_context(self):
        snapshot=load_test_snapshot();snapshot['characters']=snapshot['characters'][:1]
        gate=Barrier(4);threads=Lock();active=0;peak=0;count=0
        ledger=UsageLedger();scope=current_usage.set(ledger)
        def request(*args,**kwargs):
            nonlocal active,peak,count
            self.assertIs(current_usage.get(),ledger)
            with threads:active+=1;peak=max(peak,active);count+=1
            try:gate.wait(timeout=3)
            finally:
                with threads:active-=1
            return {'characterData':{'character':{'encounterRankings':{'ranks':[]}}}}
        try:
            with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)),patch('scoring_data.request_graph',side_effect=request):
                self.assertEqual(load_histories(snapshot,snapshot['characters'],snapshot['zone']['encounters'][:4],lambda:'token'),[])
                self.assertEqual((peak,count),(4,4))
                load_histories(snapshot,snapshot['characters'],snapshot['zone']['encounters'][:4],Mock(side_effect=AssertionError('Cache must not auth')))
                self.assertEqual(count,4)
        finally:current_usage.reset(scope)

    def test_two_mythic_packages_overlap_and_only_missing_players_load(self):
        snapshot=load_test_snapshot();snapshot['characters']=snapshot['characters'][:10]
        gate=Barrier(2);calls=[]
        def fetch(token,entries,*args):
            calls.append([c['name'] for c in entries]);gate.wait(timeout=3)
            return [{**c,'error':'','raw':{'characterData':{'character':{'zoneRankings':{'rankings':[]}}}}} for c in entries]
        with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)),patch('scoring_data.fetch_characters',side_effect=fetch):
            self.assertEqual(load_higher(snapshot,lambda:'token'),[])
            self.assertEqual(sorted(map(len,calls)),[5,5])
            self.assertEqual(load_higher(snapshot,Mock(side_effect=AssertionError('No cached auth'))),[])
            self.assertEqual(len(calls),2)

    def test_histories_and_mythic_start_together_and_auth_only_once(self):
        snapshot=load_test_snapshot();snapshot['characters']=snapshot['characters'][:1]
        entries=snapshot['characters'];base={'snapshot':snapshot,'stats':(0,1,0),'token':None,'errors':[]}
        gate=Barrier(2);job=Mock();job.cancelled=Event()
        def branch(snapshot,*args,**kwargs):
            provider=args[2] if len(args)>1 else args[0]
            gate.wait(timeout=3);self.assertEqual(provider(),'token');return []
        with patch('analysis_jobs.load_histories',side_effect=branch),patch('analysis_jobs.load_higher',side_effect=branch),patch('analysis_jobs.token_for',return_value='token') as auth:
            result=run_configured(job,base,entries,normalize({'detailed':True}),('id','secret'))
            self.assertEqual(result['errors'],[]);auth.assert_called_once()
