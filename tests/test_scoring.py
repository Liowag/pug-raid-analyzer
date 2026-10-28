from copy import deepcopy
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch, Mock
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time
from streamlit.testing.v1 import AppTest
from analyzer import ROOT, load_test_snapshot
from scoring import kill_experience, score_player, group_score, compact_history, recent_statistics, weekly_window, current_week_kills
from scoring_data import load_histories, cached_histories, load_mains, cached_higher, load_higher
from scoring_ui import parse_reference
import requests

BOSSES=[{'id':i,'name':f'Boss{i}'} for i in range(1,9)]
NOW=datetime(2026,10,27,tzinfo=timezone.utc).timestamp()


def character(name='Player',role='DD',kills=5,median=65,best=80):
    return {'name':name,'realm':'realm','role':role,'error':'',
        'raw':{'characterData':{'character':{'zoneRankings':{'metric':'hps' if role=='Heal' else 'dps',
            'rankings':[{'encounter':boss,'totalKills':kills,'medianPercent':median,'rankPercent':best} for boss in BOSSES]}}}}}


def roster():
    return {'region':'EU','zone':{'id':53,'encounters':BOSSES},'difficulty':4,'captured_at':'test',
        'characters':[character('Tank1','Tank'),character('Tank2','Tank'),
            character('Heal1','Heal'),character('Heal2','Heal'),character('Heal3','Heal'),character('DD')]}


class ScoringTests(TestCase):
    def test_experience_saturates_after_five_and_full_experience_dominates(self):
        self.assertLess(kill_experience(8)-kill_experience(5),3)
        self.assertGreater(kill_experience(5)-kill_experience(2),40)
        experienced=score_player(character(kills=5),BOSSES)['score']
        novice=character(kills=2)
        novice['raw']['characterData']['character']['zoneRankings']['rankings'].pop()
        self.assertGreater(experienced-score_player(novice,BOSSES)['score'],20)
        self.assertLess(score_player(character(kills=8),BOSSES)['score']-experienced,3)

    def test_later_bosses_carry_more_weight(self):
        early,late=character(),character()
        early['raw']['characterData']['character']['zoneRankings']['rankings'][0]['totalKills']=0
        late['raw']['characterData']['character']['zoneRankings']['rankings'][-1]['totalKills']=0
        self.assertGreater(score_player(early,BOSSES)['score'],score_player(late,BOSSES)['score'])

    def test_parse_variance_penalizes_dd_more_than_tank(self):
        losses=[]
        for role in ('DD','Tank'):
            steady=score_player(character(role=role,median=50,best=65),BOSSES)
            uneven=score_player(character(role=role,median=50,best=100),BOSSES)
            losses.append(uneven['penalty']-steady['penalty'])
        self.assertGreater(losses[0],losses[1]*4)

    def test_old_low_parses_lose_influence_when_player_improves(self):
        samples=[{'percent':5,'startTime':(NOW-120*86400+i)*1000} for i in range(5)]
        samples += [{'percent':85,'startTime':(NOW-i*86400)*1000} for i in range(5)]
        stats=recent_statistics(samples,NOW)
        self.assertEqual(stats['median'],85)
        self.assertLess(stats['low_rate'],.1)
        self.assertGreater(stats['trend'],50)
        no_trend=score_player(character(median=30,best=85),BOSSES)
        improving=score_player(character(median=30,best=85),BOSSES,{b['id']:samples for b in BOSSES},reference_time=NOW)
        self.assertGreater(improving['score'],no_trend['score'])
        self.assertEqual(improving['history_bosses'],8)

    def test_missing_data_is_unknown_not_zero_and_applicants_do_not_raise_group(self):
        group=roster()
        baseline=group_score(group,BOSSES)
        applicant=character('Applicant',kills=100,median=100,best=100);applicant['applicant']=True
        group['characters'].append(applicant)
        self.assertEqual(group_score(group,BOSSES)['score'],baseline['score'])
        group['characters'][0]['error']='ReadTimeout'
        partial=group_score(group,BOSSES)
        self.assertIsNotNone(partial['score'])
        self.assertTrue(partial['provisional'])
        self.assertIsNone(score_player({'name':'Unknown','realm':'realm'},BOSSES)['score'])

    def test_half_healers_rounded_up_and_weak_tank_limits_group(self):
        group=roster()
        self.assertEqual(group_score(group,BOSSES)['core_healers'],2)
        for c in group['characters']:
            if c['name']=='Tank1':
                c['raw']['characterData']['character']['zoneRankings']['rankings']=character(kills=0,median=5,best=10)['raw']['characterData']['character']['zoneRankings']['rankings']
        score=group_score(group,BOSSES)
        self.assertLess(score['score'],30)

    def test_full_clear_readiness_cannot_exceed_six_boss_goal(self):
        group=load_test_snapshot()
        bosses=[b for b in group['zone']['encounters'] if b['id'] not in (3379,3513)]
        self.assertLessEqual(group_score(group,bosses)['score'],group_score(group,bosses[:6])['score'])

    def test_main_bonus_is_bounded_does_not_stack_and_preserves_own_experience(self):
        alt=character(kills=2)
        main=character('Main',kills=8)
        base=score_player(alt,BOSSES)
        boosted=score_player(alt,BOSSES,mains=[main,main])
        self.assertGreater(boosted['score'],base['score'])
        self.assertLessEqual(boosted['main_bonus'],10)
        self.assertLess(boosted['score'],score_player(character(kills=5),BOSSES)['score'])
        self.assertEqual(score_player(character(kills=8),BOSSES,mains=[main])['main_bonus'],0)
        self.assertEqual(score_player(alt,BOSSES,mains=[character(kills=8)])['main_bonus'],0)

    def test_history_filters_role_deduplicates_and_ignores_undated_records(self):
        samples=compact_history({'ranks':[{'rankPercent':80,'startTime':NOW*1000,'spec':'Holy','report':{'code':'A','fightID':1}},
            {'rankPercent':10,'startTime':NOW*1000,'spec':'Fire','report':{'code':'B','fightID':2}},
            {'rankPercent':80,'startTime':NOW*1000,'spec':'Holy','report':{'code':'A','fightID':1}}]},'Heal')
        self.assertEqual(len(samples),1)
        self.assertIsNone(recent_statistics(samples,NOW))

    def test_optional_history_is_cached_and_has_no_second_network_call(self):
        group=roster();c=group['characters'][-1]
        response={'characterData':{'character':{'encounterRankings':{'ranks':[
            {'rankPercent':50,'spec':'Fire','startTime':NOW*1000,'report':{'code':'A','fightID':1}}]}}}}
        with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)),patch('scoring_data.request_graph',return_value=response) as api:
            self.assertEqual(load_histories(group,[c],BOSSES[:1],lambda:'token'),[])
            self.assertEqual(api.call_count,1)
            self.assertEqual(load_histories(group,[c],BOSSES[:1],Mock(side_effect=AssertionError('No auth for cached history'))),[])
            self.assertEqual(api.call_count,1)

    def test_main_profiles_batch_only_missing_and_reuse_cache(self):
        group=roster()
        refs=[{'name':'Main1','realm':'realm'},{'name':'Main2','realm':'realm'}]
        with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)),patch('scoring_data.fetch_characters',side_effect=lambda token,entries,*args:[character(e['name']) for e in entries]) as api:
            self.assertEqual(len(load_mains(group,refs,lambda:'token')),2)
            self.assertEqual(api.call_count,1)
            self.assertEqual(len(api.call_args.args[1]),2)
            load_mains(group,refs,Mock(side_effect=AssertionError('Cached mains must not authenticate')))
            self.assertEqual(api.call_count,1)
            load_mains(group,refs+[{'name':'Main3','realm':'realm'}],lambda:'token')
            self.assertEqual(api.call_count,2)
            self.assertEqual([e['name'] for e in api.call_args.args[1]],['Main3'])

    def test_reference_parser_accepts_unicode_profiles_and_rejects_other_regions(self):
        self.assertEqual(parse_reference('https://raider.io/characters/eu/tarren-mill/D%C3%B4ora','EU'),{'name':'Dôora','realm':'tarren-mill'})
        with self.assertRaises(ValueError):parse_reference('https://raider.io/characters/us/realm/Main','EU')

    def test_scoring_ui_defaults_to_zero_extra_requests_and_separate_targets(self):
        with patch('requests.post',side_effect=AssertionError('No HTTP')),patch('requests.get',side_effect=AssertionError('No HTTP')):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30)
            app.session_state['test_mode']=True
            app.run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(app.selectbox(key='score_target').value,'abyss')
            self.assertTrue(any(metric.label=='Ziel 6/8' for metric in app.metric))
            self.assertTrue(any(metric.label=='Ziel 8/8' for metric in app.metric))
            app.selectbox(key='score_target').set_value('nymrissa').run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(metric.label=='Ziel 1/1' for metric in app.metric))
            app.selectbox(key='score_target').set_value('kithix').run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(metric.label=='Ziel 1/1' for metric in app.metric))


class AdditionalScoringTests(TestCase):
    def test_itemlevel_bonus_is_bounded_optional_and_never_a_penalty(self):
        c=character()
        baseline=score_player(c,BOSSES)['score']
        self.assertEqual(score_player(c,BOSSES,item_reference=320)['score'],baseline)
        for rank in c['raw']['characterData']['character']['zoneRankings']['rankings']:
            rank['bestRank']={'ilvl':329}
        self.assertEqual(score_player(c,BOSSES,item_reference=320)['item_bonus'],0)
        c['scoring_gear']={'startTime':NOW*1000,'itemLevel':329}
        result=score_player(c,BOSSES,item_reference=320)
        self.assertEqual(result['item_bonus'],3)
        self.assertEqual(result['score'],baseline+3)
        self.assertEqual(score_player(c,BOSSES,item_reference=350)['score'],baseline)
        self.assertEqual(score_player(c,BOSSES,item_reference=1)['item_bonus'],5)

    def test_higher_kills_only_add_bonus_with_diminishing_returns(self):
        c=character(kills=2)
        base=score_player(c,BOSSES)
        self.assertEqual(score_player(c,BOSSES,higher=character(kills=0))['score'],base['score'])
        five=score_player(c,BOSSES,higher=character(kills=5))
        eight=score_player(c,BOSSES,higher=character(kills=8))
        self.assertGreater(five['score'],base['score'])
        self.assertLessEqual(eight['higher_bonus'],8)
        self.assertLess(eight['score']-five['score'],.4)

    def test_reset_boundaries_and_region_independent_dst(self):
        for region,stamp in [('EU','2026-10-28T04:00:00+00:00'),('US','2026-10-27T15:00:00+00:00'),('NA','2026-10-27T15:00:00+00:00')]:
            reset=datetime.fromisoformat(stamp).timestamp()
            self.assertEqual(weekly_window(region,reset),(reset,reset+604800))
            self.assertEqual(weekly_window(region,reset-1),(reset-604800,reset))
        self.assertIsNone(weekly_window('CN',NOW))

    def test_weekly_warning_only_counts_current_nonfuture_kills(self):
        snapshot=roster();key='dd|realm'
        from roles import identity
        key=identity('DD','realm')
        start,end=weekly_window('EU',NOW)
        history={key:{1:[{'startTime':(start+1)*1000}],2:[{'startTime':(start-1)*1000}],3:[{'startTime':(NOW+1)*1000}]}}
        self.assertEqual(current_week_kills(snapshot,history,NOW),[{'Charakter':'DD','Boss':'Boss1'}])
        self.assertEqual(current_week_kills(snapshot,history,end),[])

    def test_higher_cache_queries_only_missing_and_retries_failures(self):
        group=roster();group['characters']=group['characters'][:2]
        with TemporaryDirectory() as folder,patch('scoring_data.DATA',Path(folder)),patch('scoring_data.fetch_characters') as api:
            api.side_effect=lambda token,entries,*args:[{**c,'error':'ReadTimeout' if c['name']=='Tank2' else ''} for c in entries]
            self.assertEqual(len(load_higher(group,lambda:'token')),1)
            self.assertEqual(len(cached_higher(group)),1)
            load_higher(group,lambda:'token')
            self.assertEqual([c['name'] for c in api.call_args.args[1]],['Tank2'])
            self.assertEqual(api.call_args.args[-1],5)

    def test_main_controls_and_bonus_are_absent_in_ui(self):
        with patch('requests.post',side_effect=AssertionError('No HTTP')),patch('requests.get',side_effect=AssertionError('No HTTP')),patch('scoring_ui.assigned_mains',side_effect=AssertionError('Main disabled')):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30)
            app.session_state['test_mode']=True;app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any('Main' in b.label for b in app.button))
            self.assertFalse(any('Warband' in e.label for e in app.expander))
            self.assertTrue(any(b.label=='Auswertung und Ziel ändern' for b in app.button))


class ProvisionalGroupTests(TestCase):
    def test_one_tank_and_dd_only_have_scores_but_are_provisional(self):
        for roles in [('Tank','DD'),('DD',),('Heal',)]:
            group=roster();group['characters']=[character(str(i),role) for i,role in enumerate(roles)]
            result=group_score(group,BOSSES)
            self.assertIsNotNone(result['score'])
            self.assertTrue(result['provisional'])
            self.assertAlmostEqual(sum(result['effective_weights'].values()),1)
        full=group_score(roster(),BOSSES)
        self.assertFalse(full['provisional'])
        self.assertEqual(full['effective_weights']['Tanks'],.45)

    def test_no_evidence_never_invents_a_group_score(self):
        group=roster();group['characters']=[{'name':'Unknown','realm':'realm','role':'Tank','error':'ReadTimeout'}]
        self.assertIsNone(group_score(group,BOSSES)['score'])
