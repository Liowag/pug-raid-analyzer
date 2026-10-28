import json
from pathlib import Path
import unittest
from unittest.mock import patch
from analyzer import load_test_snapshot

from streamlit.testing.v1 import AppTest
from rio_import import parse_rio_export


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / 'tests/fixtures/rio_group.json').read_text(encoding='utf-8')


class RaiderIOImportTests(unittest.TestCase):
    def test_real_group_export(self):
        players, region = parse_rio_export(SAMPLE)
        self.assertEqual(len(players), 20)
        self.assertEqual(region, 'EU')
        self.assertIn(('Садрик', 'soulflayer'), players)
        self.assertIn(('Chaosdk', 'tarren-mill'), players)
        self.assertIn(('Wildestdreâm', 'silvermoon'), players)
        self.assertIn(('Rægash', 'hyjal'), players)

    def test_applicants_and_duplicates(self):
        data = json.loads(SAMPLE)
        data['queue'] = [['1-Newplayer-twisting-nether', data['group'][0]]]
        self.assertEqual(len(parse_rio_export(json.dumps(data), 'both')[0]), 21)
        self.assertEqual(len(parse_rio_export(json.dumps(data), 'queue')[0]), 2)

    def test_import_in_streamlit(self):
        fixture=load_test_snapshot()
        export=json.dumps({'region':'eu','group':[f"{'2' if c['role']=='Heal' else '4' if c['role']=='Tank' else '1'}-{c['name']}-{c['realm']}" for c in fixture['characters']]})
        app = AppTest.from_file(str(ROOT / 'app.py'),default_timeout=20)
        app.session_state['test_mode'] = True
        app.session_state['zone_attempted'] = True
        with patch('analyzer.load_test_snapshot',return_value=fixture):
            app.run()
        self.assertEqual(len(app.exception), 0)
        app.text_area(key='rio_export').set_value(export).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox(key='region').value, 'EU')
        characters = app.session_state['snapshot']['characters']
        self.assertEqual(len(characters), 20)
        self.assertEqual({c['name'] for c in characters},{c['name'] for c in fixture['characters']})
        self.assertTrue(any('20 beibehalten' in message.value for message in app.success))
        app.text_area(key='rio_export').set_value('invalid JSON').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['snapshot']['characters'], characters)
        self.assertEqual(len(app.error), 1)


if __name__ == '__main__':
    unittest.main()
