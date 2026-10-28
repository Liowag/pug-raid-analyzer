from unittest import TestCase
from unittest.mock import patch
from datetime import datetime, timezone
from streamlit.testing.v1 import AppTest
import pandas as pd

from analyzer import ROOT, load_test_snapshot
from scoring import compact_history, current_week_kills
from boss_ui import boss_kill_tooltip
from ui_components import official_icons, frame_table


class DisplayDetailTests(TestCase):
    def test_current_week_link_targets_the_actual_fight(self):
        stamp=datetime(2026,10,28,12,tzinfo=timezone.utc).timestamp()
        sample=compact_history({'ranks':[{'spec':'Protection','rankPercent':60,'startTime':stamp*1000,
            'report':{'code':'TestLog123','fightID':17}}]},'Tank')
        self.assertEqual(sample[0]['report'],{'code':'TestLog123','fightID':17})
        snapshot={'region':'EU','zone':{'encounters':[{'id':1,'name':'Boss'}]},'characters':[{'name':'Tank','realm':'realm'}]}
        found=current_week_kills(snapshot,{'tank|realm':{1:sample}},stamp+1)
        self.assertEqual(found[0]['Log'],'https://www.warcraftlogs.com/reports/TestLog123#fight=17')

    def test_boss_tooltip_lists_counts_and_marks_unknown_data(self):
        snapshot=load_test_snapshot();c=snapshot['characters'][0]
        text=boss_kill_tooltip(c,snapshot['zone'])
        self.assertIn(snapshot['zone']['encounters'][0]['name']+': ',text)
        self.assertEqual(len(text.splitlines()),len(snapshot['zone']['encounters']))
        self.assertIn('unbekannt',boss_kill_tooltip({'raw':{}},snapshot['zone']))

    def test_official_icons_are_local_and_log_column_is_clickable(self):
        icons=official_icons()
        self.assertTrue(icons['wcl'].startswith('data:image/png;base64,'))
        self.assertTrue(icons['rio'].startswith('data:image/x-icon;base64,'))
        with patch('ui_components.render_table') as render:
            frame_table(pd.DataFrame([{'Log':'https://www.warcraftlogs.com/reports/ABC#fight=1'}]),key='test_log',link_columns=['Log'])
            self.assertEqual(render.call_args.args[1][0][0]['url'],'https://www.warcraftlogs.com/reports/ABC#fight=1')

    def test_boss_grid_is_initially_collapsed_and_roles_can_expand(self):
        with patch('requests.post',side_effect=AssertionError('No network')):
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30)
            app.session_state['test_mode']=True;app.run()
            self.assertFalse(app.exception)
            boss=next(e for e in app.expander if e.label=='Bossübersicht')
            self.assertFalse(boss.proto.expanded)
            self.assertTrue(any(e.label=='Tanks · Damage' for e in app.expander))
