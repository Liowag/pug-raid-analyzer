from auth_helpers import verified
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import unquote
from streamlit.testing.v1 import AppTest
from analyzer import ROOT, load_test_snapshot
from preferences import load_preferences, save_preferences, selected_roster
from rankings import profile_links
from roster import roster_from_export


class TablePreferenceTests(TestCase):
    def test_profile_links_encode_unicode_and_realms(self):
        cell=profile_links('Нырма/é','tarren-mill','EU')
        self.assertEqual([link['label'] for link in cell['links']],['Warcraft Logs','Raider.IO'])
        self.assertIn('/character/eu/tarren-mill/',cell['links'][0]['url'])
        self.assertIn('/characters/eu/tarren-mill/',cell['links'][1]['url'])
        self.assertTrue(all('%2F' in link['url'] and 'Нырма/é' in unquote(link['url']) for link in cell['links']))

    def test_preferences_persist_and_group_members_win_over_queue_duplicates(self):
        with TemporaryDirectory() as folder,patch('preferences.PATH',Path(folder)/'settings.json'):
            self.assertTrue(load_preferences()['include_applicants'])
            save_preferences(False)
            self.assertFalse(load_preferences()['include_applicants'])
            entries,_=roster_from_export(json.dumps({'group':['1-Member-realm'],'queue':[['1-Member-realm','1-Applicant-realm']],'region':'eu'}))
            self.assertEqual([entry['name'] for entry in selected_roster(entries,False)],['Member'])
            self.assertEqual(len(selected_roster(entries,True)),2)

    def test_applicants_toggle_filters_requests_and_keeps_group_cache(self):
        fixture=load_test_snapshot()
        fixture['characters']=fixture['characters'][:1]
        member=fixture['characters'][0]
        export=json.dumps({'group':[f"2-{member['name']}-{member['realm']}"],
                           'queue':[['1-Applicant-realm']],'region':'eu'})
        def fetch(token,entries,*args):
            return [{**entry,'error':'','raw':{'characterData':{'character':{'zoneRankings':{'metric':'dps','rankings':[]}}}}} for entry in entries]
        with patch('analyzer.load_test_snapshot',return_value=fixture),patch('preferences.save_preferences') as save,patch('analyzer.token_for',return_value='fake'),patch('analyzer.fetch_characters',side_effect=fetch) as graph,patch('analyzer.save_analysis'),patch('requests.post',side_effect=AssertionError('No live HTTP')):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=20)
            verified(app)
            app.session_state['include_applicants']=False
            app.run()
            next(b for b in app.button if b.label=='Testdaten nutzen').click().run()
            app.text_area(key='rio_export').set_value(export).run()
            self.assertEqual(len(app.session_state['snapshot']['characters']),1)
            graph.assert_not_called()
            # Exit test mode while preserving a valid current member to test
            # the exact pending newcomer package rather than a full refresh.
            app.session_state['test_mode']=False
            app.toggle(key='include_applicants').set_value(True).run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual([c['name'] for c in graph.call_args.args[1]],['Applicant'])
            self.assertEqual(len(app.session_state['snapshot']['characters']),2)
            app.toggle(key='include_applicants').set_value(False).run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(graph.call_count,1)
            self.assertEqual([c['name'] for c in app.session_state['snapshot']['characters']],[member['name']])
            save.assert_not_called()
