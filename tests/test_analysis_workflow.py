from auth_helpers import verified
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch, Mock
import json

from streamlit.testing.v1 import AppTest
from analyzer import ROOT, load_test_snapshot, now, save_json
from analysis_options import normalize, same_group, group_identity, target_bosses, estimate
from analysis_jobs import run_base, run_configured, Job
from scoring_data import latest_gear, history_path, cached_latest_gear, history_has_itemlevel
from roles import identity


def entry(index,applicant=False):return {'name':f'Player{index}','realm':'realm','role':'DD','applicant':applicant}


def fetched(token,entries,*args):
    return [{**e,'error':'','raw':{'characterData':{'character':{'zoneRankings':{'metric':'hps' if e['role']=='Heal' else 'dps','rankings':[]}}}}} for e in entries]


def job_stub():
    task=Mock();task.cancelled=Event();return task


class WorkflowTests(TestCase):
    def test_group_match_threshold_ignores_applicants_and_respects_region(self):
        previous=group_identity([entry(i) for i in range(20)],'EU')
        self.assertTrue(same_group(previous,group_identity([entry(i) for i in range(12)]+[entry(i+50) for i in range(8)],'eu')))
        self.assertFalse(same_group(previous,group_identity([entry(i) for i in range(11)]+[entry(i+50) for i in range(9)],'EU')))
        self.assertFalse(same_group(previous,group_identity([entry(i) for i in range(20)],'US')))
        self.assertTrue(same_group(previous,group_identity([entry(i) for i in range(20)]+[entry(99,True)],'EU')))
        self.assertFalse(same_group(previous,group_identity([entry(0)],'EU')))

    def test_all_ten_targets_and_disabled_scoring(self):
        zone=load_test_snapshot()['zone']
        self.assertEqual(len(target_bosses(zone,normalize({'abyss':8,'nymrissa':1,'kithix':1}))),10)
        self.assertEqual([b['id'] for b in target_bosses(zone,normalize({'abyss':0,'nymrissa':1}))],[3379])
        self.assertFalse(normalize({'scoring':False,'detailed':True})['detailed'])

    def test_background_is_running_before_choices_and_disabled_skips_details(self):
        gate=Event();entered=Event();zone=load_test_snapshot()['zone']
        def delayed(*args):entered.set();gate.wait(5);return fetched(*args)
        with patch('analysis_jobs.fetch_characters',side_effect=delayed) as api,patch('analysis_jobs.token_for',return_value='token'),patch('analysis_jobs.load_histories') as histories,patch('analysis_jobs.load_higher') as higher:
            job=Job(lambda task:run_base(task,[entry(1)],'EU',zone,4,None,('id','secret')))
            try:
                self.assertTrue(entered.wait(3));self.assertFalse(job.future.done())
            finally:gate.set()
            result=job.future.result(timeout=5)
            result=run_configured(job_stub(),result,[entry(1),entry(2,True)],normalize({'scoring':False,'include_applicants':False}),('id','secret'))
            self.assertEqual(len(result['snapshot']['characters']),1)
            self.assertEqual(api.call_count,1);histories.assert_not_called();higher.assert_not_called()

    def test_applicants_in_prior_snapshot_are_reused_after_group_first_load(self):
        zone=load_test_snapshot()['zone'];entries=[entry(1),entry(2,True)]
        with patch('analysis_jobs.fetch_characters',side_effect=fetched) as api:
            previous={'region':'EU','zone':zone,'difficulty':4,'captured_at':now(),'characters':fetched('t',entries)}
            base=run_base(Mock(),entries[:1],'EU',zone,4,previous,('id','secret'),'token')
            result=run_configured(job_stub(),base,entries,normalize(),('id','secret'))
            api.assert_not_called();self.assertEqual(len(result['snapshot']['characters']),2)

    def test_details_only_target_bosses_and_heroic_checks_mythic(self):
        zone=load_test_snapshot()['zone']
        with patch('analysis_jobs.fetch_characters',side_effect=fetched),patch('analysis_jobs.load_histories',return_value=[]) as histories,patch('analysis_jobs.load_higher',return_value=[]) as higher:
            base=run_base(Mock(),[entry(1)],'EU',zone,4,None,('id','secret'),'token')
            run_configured(job_stub(),base,[entry(1)],normalize({'detailed':True,'abyss':0,'nymrissa':1}),('id','secret'))
            self.assertEqual([b['id'] for b in histories.call_args.args[2]],[3379])
            self.assertTrue(histories.call_args.kwargs['require_itemlevel']);higher.assert_called_once()
            higher.reset_mock()
            run_configured(job_stub(),base,[entry(1)],normalize({'detailed':True,'difficulty':5}),('id','secret'))
            higher.assert_not_called()

    def test_latest_log_wins_over_top_gear_and_missing_does_not_fall_back(self):
        records=[{'startTime':1000,'rankPercent':100,'bracketData':350},
                 {'startTime':2000,'rankPercent':5,'bracketData':325}]
        self.assertEqual(latest_gear(records),{'startTime':2000,'itemLevel':325})
        records.append({'startTime':3000,'rankPercent':1})
        self.assertIsNone(latest_gear(records)['itemLevel'])

    def test_missing_character_does_not_trigger_followup_detail_calls(self):
        zone=load_test_snapshot()['zone'];entries=[entry(1),entry(2,True)]
        def answer(token,players,*args):
            return [({**c,'error':'Charakter nicht gefunden','raw':{}} if c['applicant'] else fetched(token,[c])[0]) for c in players]
        with patch('analysis_jobs.fetch_characters',side_effect=answer),patch('analysis_jobs.load_histories',return_value=[]) as histories,patch('analysis_jobs.load_higher',return_value=[]) as higher:
            base=run_base(job_stub(),entries[:1],'EU',zone,4,None,('id','secret'),'token')
            result=run_configured(job_stub(),base,entries,normalize({'detailed':True}),('id','secret'))
            self.assertEqual([c['name'] for c in histories.call_args.args[1]],['Player1'])
            self.assertEqual([c['name'] for c in higher.call_args.args[0]['characters']],['Player1'])
            self.assertEqual(result['stats'][:2],(2,0))

    def test_snapshot_is_published_before_slow_details_finish(self):
        entered=Event();release=Event();zone=load_test_snapshot()['zone']
        def delayed(*args,**kwargs):entered.set();release.wait(5);return []
        with patch('analysis_jobs.fetch_characters',side_effect=fetched),patch('analysis_jobs.load_histories',side_effect=delayed),patch('analysis_jobs.load_higher',return_value=[]):
            base=run_base(job_stub(),[entry(1)],'EU',zone,4,None,('id','secret'),'token')
            job=Job(lambda task:run_configured(task,base,[entry(1)],normalize({'detailed':True}),('id','secret')))
            try:
                self.assertTrue(entered.wait(2));self.assertFalse(job.future.done())
                message,progress,preview=job.view()
                self.assertEqual(preview['characters'][0]['name'],'Player1')
                self.assertIn('Kill-Historien',message)
                self.assertGreater(progress,0)
            finally:release.set()
            job.future.result(timeout=5)

    def test_cancelled_details_do_not_authenticate_or_fetch(self):
        from scoring_data import load_histories
        snapshot=load_test_snapshot();auth=Mock(side_effect=AssertionError('No auth'))
        with patch('scoring_data.request_graph',side_effect=AssertionError('No fetch')):
            self.assertEqual(load_histories(snapshot,snapshot['characters'],snapshot['zone']['encounters'],auth,cancelled=lambda:True),[])

    def test_latest_gear_across_target_bosses_and_weekly_cache_rollover(self):
        snapshot=load_test_snapshot();c=snapshot['characters'][0];bosses=snapshot['zone']['encounters'][:2]
        with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)):
            for boss,stamp,value in [(bosses[0],1000,350),(bosses[1],2000,None)]:
                save_json(history_path(snapshot,c,boss['id']),{'captured_at':now(),'samples':[],
                    'latest_gear':{'startTime':stamp,'itemLevel':value}})
            self.assertIsNone(cached_latest_gear(snapshot,c,bosses)['itemLevel'])
            self.assertTrue(history_has_itemlevel(snapshot,c,bosses[0]['id']))
            with patch('scoring_data.weekly_window',return_value=(datetime.now(timezone.utc).timestamp()+10,0)):
                self.assertFalse(history_has_itemlevel(snapshot,c,bosses[0]['id']))

    def test_detail_estimate_reuses_cache_and_is_zero_when_disabled(self):
        snapshot=load_test_snapshot();snapshot['characters']=snapshot['characters'][:2]
        options=normalize({'detailed':True,'abyss':8})
        with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)),patch('scoring_data.cached_higher',return_value={}):
            # No app cache, even if the user's regular Heart caches exist.
            with patch('scoring_data.get_top',return_value={}):
                self.assertEqual(estimate(snapshot,options)['calls'],17)
                boss=target_bosses(snapshot['zone'],options)[0]
                save_json(history_path(snapshot,snapshot['characters'][0],boss['id']),{'captured_at':now(),'samples':[],'latest_gear':None})
                self.assertEqual(estimate(snapshot,options)['calls'],16)
                self.assertEqual(estimate(snapshot,normalize({'scoring':False,'detailed':True}))['calls'],0)

    def test_dialog_remains_interactive_while_base_loads_and_can_disable_score(self):
        gate=Event();entered=Event()
        export=json.dumps({'group':['4-Tank-realm','1-DD-realm'],'queue':[['1-Applicant-realm']],'region':'eu'})
        def delayed(*args):entered.set();gate.wait(5);return fetched(*args)
        with patch('analyzer.load_credentials',return_value=('id','secret')),patch('analysis_jobs.token_for',return_value='token'),patch('analysis_jobs.fetch_characters',side_effect=delayed) as api,patch('analyzer.save_analysis'),patch('import_ui.save_analysis'),patch('requests.post',side_effect=AssertionError('No HTTP')),patch('analysis_jobs.load_histories') as history:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20);verified(app);app.run()
            app.text_area(key='rio_export').set_value(export).run()
            try:
                self.assertFalse(app.exception);self.assertTrue(entered.wait(2))
                self.assertFalse(app.session_state['_analysis_job'].future.done())
                app.toggle(key='analysis_draft_scoring').set_value(False).run()
                app.toggle(key='analysis_draft_include_applicants').set_value(False).run()
                app.button(key='analysis_accept').click().run()
                self.assertFalse(app.exception)
            finally:gate.set()
            for _ in range(5):
                if '_analysis_job' not in app.session_state:break
                app.session_state['_analysis_job'].future.result(timeout=5)
                app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.session_state['analysis_options']['scoring'])
            self.assertEqual(len(app.session_state['snapshot']['characters']),2)
            self.assertFalse(any('Gruppenscore' in e.label for e in app.expander))
            self.assertEqual(api.call_count,1);history.assert_not_called()
            # Repeated import keeps choices and does not re-open the dialog.
            app.text_area(key='rio_export').set_value(export+' ').run()
            for _ in range(5):
                if '_analysis_job' not in app.session_state:break
                app.session_state['_analysis_job'].future.result(timeout=5);app.run()
            self.assertFalse(app.session_state.get('_analysis_dialog',False))
            self.assertEqual(api.call_count,1)
