import copy
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from analyzer import ROOT, load_test_snapshot
from boss_details import (CharacterIdentityError, ULATEK, choose_top, collect, compact_report,
                          compare_player, comparison_color, display_damage)


def example():
    return {'fights':[{'id':2,'encounterID':ULATEK,'difficulty':4,'kill':True}],
            'playerDetails':{'data':{'playerDetails':{
                'dps':[{'id':1,'name':'Player','server':'Realm'}, {'id':2,'name':'Other','server':'Realm'}],
                'tanks':[{'id':3,'name':'Tank','server':'Realm'}],
                'healers':[{'id':4,'name':'Healer','server':'Realm'}]}}},
            'heart':{'data':{'entries':[
                {'id':1,'type':'Hunter','total':16_300_000,'pets':[{'total':1_000_000}]},
                {'id':2,'type':'Mage','total':13_900_000},
                {'id':3,'type':'Warrior','total':3_000_000},
                {'id':4,'type':'Priest','total':2_000_000},
                {'id':99,'type':'Pet','total':1_000_000}]}},
            'damage':{'data':{'entries':[{'id':1,'type':'Hunter','total':32_600_000}]}}}


class BossDetailTests(unittest.TestCase):
    def test_user_example_average_excludes_tanks_healers_and_pets(self):
        report=compact_report(example(),2,4)
        result=compare_player(report,{'name':'Player','realm':'realm'})
        self.assertEqual(result['average'],15_100_000)
        self.assertEqual(result['delta'],1_200_000)
        self.assertEqual(result['dd_count'],2)
        self.assertEqual(result['share'],50)
        self.assertEqual(display_damage(result['delta'],True),'+1,2 Mio.')

    def test_dd_with_no_heart_damage_counts_as_zero(self):
        raw=example()
        raw['playerDetails']['data']['playerDetails']['dps'].append({'id':5,'name':'Zero','server':'Realm'})
        report=compact_report(raw,2,4)
        self.assertEqual(compare_player(report,{'name':'Zero','realm':'realm'})['heart'],0)
        self.assertAlmostEqual(compare_player(report,{'name':'Player','realm':'realm'})['average'],30_200_000/3)

    def test_missing_target_and_wrong_fight_are_not_zero(self):
        raw=example()
        raw['heart']['data']['entries']=[]
        with self.assertRaises(ValueError): compact_report(raw,2,4)
        with self.assertRaises(ValueError): compact_report(example(),2,5)

    def test_localized_server_requires_verified_alias(self):
        report=compact_report(example(),2,4)
        report['players']['dps'][0]['server']='Гордунни'
        char={'name':'Player','realm':'gordunni'}
        with self.assertRaises(CharacterIdentityError): compare_player(report,char)
        self.assertEqual(compare_player(report,char,{'name':'Гордунни','slug':'gordunni'})['heart'],16_300_000)

    def test_top_is_best_parse_for_role_not_best_heart_or_damage(self):
        ranks=[{'rankPercent':90,'amount':100,'spec':'Blood','report':{'code':'AAA','fightID':1}},
               {'rankPercent':80,'amount':200,'spec':'Blood','report':{'code':'BBB','fightID':2}},
               {'rankPercent':99,'amount':300,'spec':'Restoration','report':{'code':'CCC','fightID':3}}]
        self.assertEqual(choose_top({'ranks':ranks},'Tank')['report']['code'],'AAA')
        self.assertEqual(choose_top({'ranks':ranks},'Heal')['report']['code'],'CCC')

    def test_comparison_colors_are_deviation_bands(self):
        for delta,color in [(-.1,'#9d9d9d'),(0,'#1eff00'),(.1,'#0070ff'),(.3,'#a335ee'),(.7,'#ff8000'),(1.2,'#e268a8')]:
            self.assertEqual(comparison_color({'status':'OK','relative_delta':delta}),color)

    def test_offline_collection_without_cache_never_calls_api(self):
        with patch('requests.post',side_effect=AssertionError('API must stay offline')) as post,patch('boss_details.get_top',side_effect=ValueError('Testcache leer')):
            results=collect(load_test_snapshot(),allow_network=False)
            self.assertEqual(len(results),20)
            self.assertTrue(all(result['status']!='OK' for result in results.values()))
            post.assert_not_called()

    def test_boss_navigation_and_column_pinning_stay_offline(self):
        with patch('requests.post',side_effect=AssertionError('API must stay offline')) as post:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20)
            app.session_state['test_mode']=True
            app.run()
            app.session_state['view']='Bossfokus'
            app.session_state['_boss_id']=ULATEK
            app.run()
            self.assertEqual(len(app.exception),0)
            app.session_state['boss_columns']=['delta']
            app.run()
            self.assertIn('delta',app.session_state['boss_columns'])
            app.button(key='load_heart').click().run()
            self.assertEqual(len(app.exception),0)
            app.session_state['view']='Raidübersicht'
            app.run()
            self.assertEqual(app.session_state['view'],'Raidübersicht')
            self.assertTrue(any('Differenz zum DD-' in str(e.proto) for e in app.get('bidi_component')))
            post.assert_not_called()


if __name__=='__main__':
    unittest.main()
