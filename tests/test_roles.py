from pathlib import Path
import unittest
from unittest.mock import patch
import analyzer
from roles import identity, rio_roles


class RoleTests(unittest.TestCase):
    def test_export_roles_and_healing_fixture(self):
        text=(Path(__file__).parent/'fixtures/rio_group.json').read_text(encoding='utf-8')
        roles=rio_roles(text)
        self.assertEqual([list(roles.values()).count(role) for role in ('Tank','DD','Heal')],[2,14,4])
        snapshot=analyzer.load_test_snapshot()
        for character in snapshot['characters']:
            if character['role']=='Heal':
                data=character['raw']['characterData']['character']['zoneRankings']
                self.assertEqual(data['metric'],'hps')

    def test_healing_query_is_explicit(self):
        result={'characterData':{'character':{'zoneRankings':{'metric':'hps','rankings':[]}}}}
        with patch('analyzer.request_graph',return_value=result) as graph:
            analyzer.fetch_character('test-token','Healer','realm','EU',53,4,'Heal')
        self.assertIn('metric:hps',graph.call_args.args[1])
        self.assertEqual(graph.call_count,1)

    def test_old_healer_damage_is_not_displayed_as_healing(self):
        snapshot=analyzer.load_test_snapshot()
        char=next(c for c in snapshot['characters'] if c['role']=='Heal' and c.get('raw_damage'))
        char['raw']=char['raw_damage']
        snapshot['characters']=[char]
        rows,details=analyzer.build_tables(snapshot)
        self.assertIsNone(rows[0]['Best Ø'])
        self.assertTrue(all(row['Best'] is None for row in details))


if __name__=='__main__':
    unittest.main()
