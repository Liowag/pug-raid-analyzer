import json
import unittest
from unittest.mock import Mock
from roster import roster_from_export, reconcile
from boss_details import display_damage, context
from boss_ui import player_groups


def successful_raw(**extra):
    return {**extra,'characterData':{'character':{'zoneRankings':{'metric':'dps','rankings':[]}}}}


class RosterTests(unittest.TestCase):
    def test_changes_only_fetch_new_and_preserve_cache_context(self):
        zone={'id':53}
        old={'region':'EU','zone':zone,'difficulty':4,'captured_at':'original',
             'characters':[{'name':'Stay','realm':'realm','role':'DD','raw':successful_raw(data=123)},
                           {'name':'Leave','realm':'realm','role':'Tank'}]}
        entries,region=roster_from_export(json.dumps({'region':'eu','group':['1-Stay-realm'],'queue':[['2-New-realm']]}))
        fetch=Mock(side_effect=lambda entry:{**entry,'raw':{'characterData':{'character':{'zoneRankings':{'metric':'hps' if entry['role']=='Heal' else 'dps','rankings':[]}}}}})
        result,stats=reconcile(entries,region,zone,4,old,fetch,'now')
        self.assertEqual(stats,(1,1,1))
        self.assertEqual(fetch.call_args.args[0]['name'],'New')
        self.assertEqual(result['characters'][0]['raw'],successful_raw(data=123))
        self.assertEqual(context(old,old['characters'][0]),context(result,result['characters'][0]))
        self.assertTrue(result['characters'][1]['applicant'])
        second,stats=reconcile(entries,region,zone,4,result,fetch,'later')
        self.assertEqual(stats,(0,2,0))
        self.assertEqual(fetch.call_count,1)

    def test_healing_and_context_changes_require_matching_rankings(self):
        zone={'id':53}
        old={'region':'EU','zone':zone,'difficulty':4,'captured_at':'old',
             'characters':[{'name':'Player','realm':'realm','role':'DD'}]}
        entries=[{'name':'Player','realm':'realm','role':'Heal'}]
        fetch=Mock(side_effect=lambda entry:dict(entry))
        reconcile(entries,'EU',zone,4,old,fetch,'new')
        self.assertEqual(fetch.call_count,1)
        entries[0]['role']='DD'
        reconcile(entries,'US',zone,4,old,fetch,'new')
        self.assertEqual(fetch.call_count,2)

    def test_empty_roster_clears_and_group_members_are_not_applicants(self):
        entries,region=roster_from_export('{"group":[],"queue":[],"region":"eu"}')
        self.assertEqual(entries,[])
        entries,_=roster_from_export('{"group":["1-Name-realm"],"queue":[["1-Name-realm"]],"region":"eu"}')
        self.assertEqual(len(entries),1)
        self.assertFalse(entries[0]['applicant'])

    def test_damage_is_always_millions(self):
        self.assertEqual(display_damage(1_200_000,True),'+1,2 Mio.')
        self.assertEqual(display_damage(-410_000,True),'−0,41 Mio.')
        self.assertEqual(display_damage(0,True),'±0,00 Mio.')
        self.assertIn('Mio.',display_damage(850))

    def test_applicants_precede_roles_and_sort_by_overall_average(self):
        snapshot={'characters':[{'name':'Tank','realm':'realm'},
                                 {'name':'Low','realm':'realm','applicant':True},
                                 {'name':'High','realm':'realm','applicant':True}]}
        rows=[{'Charakter':'Tank','Server':'realm','Rolle':'Tank','Best Ø':99},
              {'Charakter':'Low','Server':'realm','Rolle':'DD','Best Ø':10},
              {'Charakter':'High','Server':'realm','Rolle':'Heal','Best Ø':80}]
        groups=list(player_groups(rows,snapshot))
        self.assertEqual(groups[0],('Bewerber · nach Best Ø',[2,1]))
        self.assertEqual(groups[1][1],[0])
