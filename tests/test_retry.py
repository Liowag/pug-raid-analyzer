from auth_helpers import verified
"""Regression coverage for failed requests being mistaken for cached data."""
import json
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
import requests
from streamlit.testing.v1 import AppTest
from analyzer import fetch_characters, load_test_snapshot
from roster import reusable, reconcile, pending_entries

ROOT=Path(__file__).resolve().parents[1]


def valid(name,role='DD'):
    return {'name':name,'realm':'realm','role':role,'error':'',
            'raw':{'characterData':{'character':{'zoneRankings':{'metric':'hps' if role=='Heal' else 'dps','rankings':[]}}}}}


class RetryTests(TestCase):
    def test_failed_or_missing_response_is_never_a_cache_hit(self):
        character=valid('Player')
        entry={key:character[key] for key in ('name','realm','role')}
        self.assertTrue(reusable(entry,character))
        for raw in (None,{}, {'characterData':{'character':None}},
                    {'characterData':{'character':{'zoneRankings':None}}},
                    {'characterData':{'character':{'zoneRankings':{}}}}):
            with self.subTest(raw=raw):
                self.assertFalse(reusable(entry,{**character,'raw':raw}))
        self.assertFalse(reusable(entry,{**character,'error':'ReadTimeout'}))

    def test_only_failed_player_retries_in_an_otherwise_unchanged_group(self):
        previous={'region':'EU','zone':{'id':53},'difficulty':4,'captured_at':'old',
                  'characters':[valid('Good'),{**valid('Failed'),'raw':None,'error':'ReadTimeout'}]}
        entries=[{key:c[key] for key in ('name','realm','role')} for c in previous['characters']]
        self.assertEqual([e['name'] for e in pending_entries(entries,'EU',{'id':53},4,previous)],['Failed'])
        batch=Mock(side_effect=lambda entries:[valid(e['name']) for e in entries])
        result,stats=reconcile(entries,'EU',{'id':53},4,previous,Mock(),'new',fetch_many=batch)
        self.assertEqual(stats,(1,1,0))
        self.assertEqual([e['name'] for e in batch.call_args.args[0]],['Failed'])
        self.assertEqual(result['characters'][0]['raw'],previous['characters'][0]['raw'])
        self.assertEqual(pending_entries(entries,'EU',{'id':53},4,result),[])

    def test_five_player_timeout_then_same_import_then_force_reload(self):
        fixture=load_test_snapshot()
        fixture['characters']=fixture['characters'][:5]
        fail_next=[True]
        def response(url,**kwargs):
            reply=Mock()
            if url.endswith('/token'):
                reply.json.return_value={'access_token':'fake-token'}
                return reply
            if fail_next[0]:
                fail_next[0]=False
                raise requests.ReadTimeout('read timeout')
            query=kwargs['json']['query']
            variables=kwargs['json']['variables']
            players={}
            for key in variables:
                if not key.startswith('name'):
                    continue
                index=key[4:]
                selection=query.split(f'p{index}:character')[1].split('p'+str(int(index)+1)+':character')[0]
                players['p'+index]={'zoneRankings':{'metric':'hps' if 'metric:hps' in selection else 'dps','rankings':[]}}
            reply.json.return_value={'data':{'characterData':players}}
            return reply
        export={'region':'eu','group':[f"{'2' if c['role']=='Heal' else '4' if c['role']=='Tank' else '1'}-{c['name']}-{c['realm']}" for c in fixture['characters']]}
        with patch('analyzer.load_test_snapshot',return_value=deepcopy(fixture)),patch('analyzer.save_analysis'),patch('requests.post',side_effect=response) as post:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20)
            verified(app)
            app.run()
            next(b for b in app.button if b.label=='Testdaten nutzen').click().run()
            app.button(key='force_reload').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(post.call_count,2)
            self.assertTrue(all(c['error'] for c in app.session_state['snapshot']['characters']))
            self.assertTrue(any('5 fehlgeschlagen' in error.value for error in app.error))
            app.text_area(key='rio_export').set_value(json.dumps(export)).run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(post.call_count,3)
            self.assertTrue(all(not c['error'] for c in app.session_state['snapshot']['characters']))
            app.text_area(key='rio_export').set_value(json.dumps(export,indent=2)).run()
            self.assertEqual(post.call_count,3)
            self.assertTrue(any('5 beibehalten' in message.value for message in app.success))
            previous_capture=app.session_state['snapshot']['captured_at']
            app.button(key='force_reload').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(post.call_count,4)
            self.assertNotEqual(app.session_state['snapshot']['captured_at'],previous_capture)
            self.assertTrue(any('5 angefragt' in message.value and '0 beibehalten' in message.value for message in app.success))
            self.assertEqual(post.call_args.kwargs['timeout'],(10,60))
