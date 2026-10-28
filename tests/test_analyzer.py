import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

from streamlit.testing.v1 import AppTest

import analyzer

ROOT = Path(__file__).resolve().parents[1]


class AnalyzerTests(unittest.TestCase):
    def test_offline_ui_and_character_detail_without_api(self):
        demo = analyzer.load_test_snapshot()
        demo['characters'][19]['raw']['characterData']['character']['zoneRankings'] = {'metric':'dps','rankings':[]}
        with patch('requests.post', side_effect=AssertionError('Offline mode called API')) as post, patch('analyzer.load_test_snapshot', return_value=demo):
            app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
            app.session_state['test_mode'] = True
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.selectbox(key='zone_id').value, 53)
            self.assertGreaterEqual(sum(e.proto.component_name=='raid_table' for e in app.get('bidi_component')), 6)
            self.assertEqual(len(app.session_state['snapshot']['characters']),20)
            self.assertFalse(any(select.label == 'Charakter' for select in app.selectbox))
            app.session_state['_detail_index']=0
            app.session_state['view']='Charakterdetails'
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('HPS' in str(e.proto) for e in app.get('bidi_component')))
            next(button for button in app.button if 'Zurück zur Übersicht' in button.label).click().run()
            app.session_state['_detail_index']=19
            app.session_state['view']='Charakterdetails'
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('Keine öffentlichen Rankings' in info.value for info in app.info))
            self.assertTrue(any(e.proto.component_name=='raid_table' for e in app.get('bidi_component')))
            post.assert_not_called()

    def test_rankings_zero_and_missing_data(self):
        character = {'raw': {'characterData': {'character': {'zoneRankings': {'rankings': [
            {'encounter': {'id': 1}, 'bestPercent': 0, 'medianPercent': 0, 'totalKills': 1}
        ]}}}}}
        snapshot = {'region':'EU','zone':{'encounters':[{'id':1,'name':'Boss'}, {'id':2,'name':'Missing'}]},
                    'characters':[dict(character,name='Тест',realm='tarren-mill',error='')]}
        rows, details = analyzer.build_tables(snapshot)
        self.assertEqual(rows[0]['Best Ø'], 0)
        self.assertEqual(rows[0]['Status'], 'OK')
        self.assertIsNone(details[1]['Best'])
        self.assertEqual(rows[0]['Kills gesamt'], 1)

    def test_real_snapshot_uses_wcl_performance_averages(self):
        snapshot = analyzer.load_test_snapshot()
        rows, details = analyzer.build_tables(snapshot)
        averages = snapshot['characters'][0]['raw']['characterData']['character']['zoneRankings']
        self.assertEqual(rows[0]['Best Ø'], round(averages['bestPerformanceAverage'], 1))
        self.assertEqual(rows[0]['Median Ø'], round(averages['medianPerformanceAverage'], 1))
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(details), 200)
        self.assertIn('Spezialisierung', details[0])

    def test_percentile_styles_do_not_need_matplotlib(self):
        frame = pd.DataFrame({'Best': [0, 27, 57, 81, 97, 99, 100, None]})
        rendered = frame.style.map(analyzer.percentile_style).format('{:.1f}',na_rep='–').to_html()
        for color in ('#9d9d9d','#1eff00','#0070ff','#a335ee','#ff8000','#e268a8','#e5cc80'):
            self.assertIn(color, rendered)

    def test_snapshots_store_raw_response_and_reload_without_api(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(analyzer, 'DATA', Path(folder)):
                snapshot = analyzer.load_json(ROOT / 'data/public_demo.json')
                snapshot['kind'] = 'warcraftlogs'
                snapshot['captured_at'] = 'test-capture'
                analyzer.save_analysis(snapshot)
                self.assertEqual(analyzer.load_test_snapshot(), snapshot)
                self.assertEqual(analyzer.load_json(Path(folder)/'last_analysis.json'), snapshot)

    def test_cached_zones_avoid_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(analyzer, 'DATA', Path(folder)):
                analyzer.save_json(Path(folder)/'raid_zones.json',{'captured_at':'test','zones':[
                    {'id':55,'name':'Mythic+','difficulties':[{'id':10}]},
                    {'id':53,'name':'The Venomous Abyss','encounters':[], 'difficulties':[{'id':4}]}]})
                with patch('requests.post') as post:
                    zones, captured, cached = analyzer.cached_zones()
                self.assertEqual([zone['id'] for zone in zones], [53])
                self.assertTrue(cached)
                self.assertEqual(captured, 'test')
                post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
