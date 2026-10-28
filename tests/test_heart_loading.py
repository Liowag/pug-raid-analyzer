from contextvars import ContextVar
from pathlib import Path
from threading import Barrier, get_ident
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import requests
from analyzer import ROOT
from boss_details import collect, get_top, top_path, ULATEK
from api_diagnostics import current_log, CallLog
from api_usage import current_usage, UsageLedger
from test_boss_details import example


def snapshot():
    return {'region':'EU','zone':{'id':53},'difficulty':4,'captured_at':'parallel-test',
            'characters':[{'name':name,'realm':'realm','role':'DD'} for name in ('Player','Other','Zero')]}


def report():
    data=example()
    data['playerDetails']['data']['playerDetails']['dps'].append({'id':5,'name':'Zero','server':'Realm'})
    return data


def top():
    return {'characterData':{'character':{'server':{'name':'Realm','slug':'realm'},
        'encounterRankings':{'ranks':[{'rankPercent':90,'spec':'Fire','report':{'code':'SHARED','fightID':2}}]}}}}


class HeartLoadingTests(TestCase):
    def test_missing_top_data_is_not_cached_but_successful_empty_ranks_are(self):
        group=snapshot()
        group['characters']=group['characters'][:1]
        with TemporaryDirectory() as folder,patch('boss_details.DATA',Path(folder)):
            with patch('boss_details.request_graph',return_value={'characterData':{'character':None}}):
                results=collect(group,lambda:'token',True)
                self.assertNotEqual(results['player|realm']['status'],'OK')
                self.assertFalse(top_path(group,group['characters'][0]).exists())
            with patch('boss_details.request_graph',return_value={'characterData':{'character':{'encounterRankings':{'ranks':[]}}}}) as request:
                collect(group,lambda:'token',True)
                self.assertTrue(top_path(group,group['characters'][0]).exists())
                request.reset_mock()
                collect(group,Mock(side_effect=AssertionError('No request for successful empty ranks')),True)
                request.assert_not_called()

    def test_parallel_requests_share_one_report_and_keep_ui_on_main_thread(self):
        barrier=Barrier(3)
        main=get_ident()
        log,usage=CallLog(),UsageLedger()
        log_scope=current_log.set(log)
        usage_scope=current_usage.set(usage)
        def graph(token,query,variables):
            self.assertIs(current_log.get(),log)
            self.assertIs(current_usage.get(),usage)
            self.assertNotEqual(get_ident(),main)
            if 'encounterRankings' in query:
                barrier.wait(timeout=4)
                return top()
            return {'reportData':{'report':report()}}
        auth=Mock(side_effect=lambda: self.assertEqual(get_ident(),main) or 'token')
        callbacks=[]
        try:
            with TemporaryDirectory() as folder,patch('boss_details.DATA',Path(folder)),patch('boss_details.request_graph',side_effect=graph) as request:
                results=collect(snapshot(),auth,True,
                    progress=lambda done,total:callbacks.append((get_ident(),done,total)),
                    completed=lambda *args:self.assertEqual(get_ident(),main))
                self.assertTrue(all(r['status']=='OK' for r in results.values()))
                self.assertEqual(list(results),['player|realm','other|realm','zero|realm'])
                self.assertEqual(request.call_count,4)
                self.assertEqual(auth.call_count,1)
                self.assertEqual(callbacks,[(main,1,3),(main,2,3),(main,3,3)])
                request.reset_mock()
                results=collect(snapshot(),Mock(side_effect=AssertionError('Cached run must not authenticate')),True)
                self.assertTrue(all(r['status']=='OK' for r in results.values()))
                request.assert_not_called()
        finally:
            current_log.reset(log_scope)
            current_usage.reset(usage_scope)

    def test_failed_shared_report_only_requests_once_then_retries_on_next_load(self):
        fail=[True]
        def graph(token,query,variables):
            if 'encounterRankings' in query:
                return top()
            if fail[0]:
                raise requests.ReadTimeout('Timeout')
            return {'reportData':{'report':report()}}
        with TemporaryDirectory() as folder,patch('boss_details.DATA',Path(folder)),patch('boss_details.request_graph',side_effect=graph) as request:
            results=collect(snapshot(),lambda:'token',True)
            self.assertTrue(all('Zeitüberschreitung' in r['status'] for r in results.values()))
            self.assertEqual(request.call_count,4)
            self.assertEqual(len(list((Path(folder)/'boss_details/tops').glob('*.json'))),3)
            request.reset_mock()
            fail[0]=False
            results=collect(snapshot(),lambda:'token',True)
            self.assertTrue(all(r['status']=='OK' for r in results.values()))
            self.assertEqual(request.call_count,1)
            self.assertEqual(request.call_args.args[2],{'code':'SHARED','fight':2})

    def test_cached_players_survive_authentication_failure(self):
        with TemporaryDirectory() as folder,patch('boss_details.DATA',Path(folder)):
            with patch('boss_details.request_graph',side_effect=lambda token,query,variables:top() if 'encounterRankings' in query else {'reportData':{'report':report()}}):
                collect(snapshot(),lambda:'token',True)
            group=snapshot()
            group['characters'].append({'name':'New','realm':'realm','role':'DD'})
            with patch('boss_details.request_graph',side_effect=AssertionError('No network without auth')):
                results=collect(group,Mock(side_effect=ValueError('Authentication failed')),True)
            self.assertEqual(results['player|realm']['status'],'OK')
            self.assertEqual(results['new|realm']['status'],'Authentication failed')
