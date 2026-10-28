from auth_helpers import verified
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, Mock
import json
from streamlit.testing.v1 import AppTest
from analyzer import load_test_snapshot, cached_zones

ROOT=Path(__file__).resolve().parents[1]


class ContextTests(TestCase):
    def test_diagnostics_records_calls_and_identical_paste_reuses_data(self):
        fixture=load_test_snapshot()
        fixture['characters']=fixture['characters'][:2]
        def response(url,**kwargs):
            reply=Mock()
            if url.endswith('/token'):
                reply.json.return_value={'access_token':'fake-token'}
            else:
                metric='hps' if 'metric:hps' in kwargs['json']['query'] else 'dps'
                variables=kwargs['json']['variables']
                players={f'p{key[4:]}':{'zoneRankings':{'metric':'hps' if 'metric:hps' in kwargs['json']['query'].split(f'p{key[4:]}:character')[1].split('p'+str(int(key[4:])+1)+':character')[0] else 'dps','rankings':[]}} for key in variables if key.startswith('name')}
                reply.json.return_value={'data':{'characterData':players,
                    'raidAnalyzerRate':{'limitPerHour':3600,'pointsSpentThisHour':42,'pointsResetIn':600,'currentRequest':{'application':10.02,'total':10.02}}}}
            return reply
        with patch('analyzer.load_test_snapshot',return_value=fixture),patch('requests.post',side_effect=response) as post,patch('analyzer.save_analysis'):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20)
            verified(app)
            app.session_state['api_diagnostics_enabled']=False
            app.run()
            next(b for b in app.button if b.label=='Testdaten nutzen').click().run()
            next(b for b in app.button if b.label=='Live-Daten laden').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(post.call_count,2)
            self.assertEqual(len(app.session_state['api_call_log'].snapshot()),0)
            self.assertEqual(app.session_state['api_usage'].snapshot()['calls'],1)
            self.assertEqual(app.session_state['api_usage'].snapshot()['known_cost'],10.02)
            export={'region':'eu','group':[f"{'2' if c['role']=='Heal' else '1'}-{c['name']}-{c['realm']}" for c in fixture['characters']]}
            app.text_area(key='rio_export').set_value(json.dumps(export)).run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(post.call_count,2)
            self.assertTrue(any('2 beibehalten' in message.value for message in app.success))

    def test_zone_switch_reloads_roster_and_replaces_bosses(self):
        fixture=load_test_snapshot()
        fixture['characters']=fixture['characters'][:2]
        zone=next(z for z in cached_zones()[0] if z['id']!=fixture['zone']['id'] and z['encounters'] and any(d['id']==4 for d in z['difficulties']))
        def fetched(token,entries,region,zone_id,difficulty):
            return [{**entry,'raw':{},'error':''} for entry in entries]
        with patch('analyzer.load_test_snapshot',return_value=fixture), patch('analyzer.token_for',return_value='fake') as oauth, patch('analyzer.fetch_characters',side_effect=fetched) as fetch, patch('analyzer.save_analysis'), patch('requests.post',side_effect=AssertionError('Live API forbidden')):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20)
            verified(app)
            app.run()
            next(b for b in app.button if b.label=='Testdaten nutzen').click().run()
            self.assertEqual(app.session_state['snapshot']['zone']['id'],53)
            app.session_state['heart_results']={'old':'cached'}
            app.session_state['_boss_id']=3492
            app.selectbox(key='zone_id').set_value(zone['id']).run()
            self.assertEqual(len(app.exception),0)
            self.assertFalse(app.session_state['test_mode'])
            self.assertEqual(app.session_state['snapshot']['zone'],zone)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(oauth.call_count,1)
            self.assertTrue(all(call.args[3]==zone['id'] for call in fetch.call_args_list))
            self.assertEqual(app.session_state['view'],'Raidübersicht')
            self.assertNotIn('heart_results',app.session_state)
            app.selectbox(key='difficulty').set_value('Mythic').run()
            self.assertEqual(app.session_state['snapshot']['difficulty'],5)
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(oauth.call_count,1)
