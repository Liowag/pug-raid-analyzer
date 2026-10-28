import json
from time import time
from unittest import TestCase
from unittest.mock import Mock, patch
import requests
from analyzer import fetch_characters, request_graph
from roster import reconcile
from api_usage import UsageLedger, current_usage, request_cost, with_quota
from api_diagnostics import current_log


def response_for(kwargs):
    variables=kwargs['json']['variables']
    info={f'p{k[4:]}':{'name':v,'zoneRankings':{'metric':'hps' if f'p{k[4:]}:character' in kwargs['json']['query'] and 'metric:hps' in kwargs['json']['query'].split(f'p{k[4:]}:character')[1].split('p'+str(int(k[4:])+1)+':character')[0] else 'dps','rankings':[]}}
          for k,v in variables.items() if k.startswith('name')}
    reply=Mock()
    reply.json.return_value={'data':{'characterData':info,'raidAnalyzerRate':{
        'limitPerHour':3600,'pointsSpentThisHour':101,'pointsResetIn':600,
        'currentRequest':{'application':10.02,'total':10.02}}}}
    return reply


class BatchTests(TestCase):
    def test_small_rosters_and_package_boundary(self):
        for count in range(7):
            with self.subTest(count=count),patch('requests.post',side_effect=lambda *args,**kwargs:response_for(kwargs)) as post:
                entries=[{'name':f'Player{i}','realm':'realm','role':'DD'} for i in range(count)]
                result=fetch_characters('token',entries,'EU',53,4)
                self.assertEqual(post.call_count,(count+4)//5)
                self.assertEqual(len(result),count)
                self.assertTrue(all(not c['error'] for c in result))

    def test_two_new_applicants_only_make_one_query(self):
        previous={'region':'EU','zone':{'id':53},'difficulty':4,'captured_at':'before',
                  'characters':[{'name':f'Old{i}','realm':'realm','role':'DD','raw':{'keep':i,'characterData':{'character':{'zoneRankings':{'metric':'dps','rankings':[]}}}}} for i in range(20)]}
        entries=[{key:c[key] for key in ('name','realm','role')} for c in previous['characters']]
        entries += [{'name':'NewA','realm':'tarren-mill','role':'DD','applicant':True},
                    {'name':'Новый','realm':'gordunni','role':'Heal','applicant':True}]
        with patch('requests.post',side_effect=lambda *args,**kwargs:response_for(kwargs)) as post:
            result,stats=reconcile(entries,'EU',{'id':53},4,previous,Mock(side_effect=AssertionError('Single fetch forbidden')),'after',
                fetch_many=lambda batch:fetch_characters('token',batch,'EU',53,4))
            self.assertEqual(stats,(2,20,0))
            self.assertEqual(post.call_count,1)
            payload=post.call_args.kwargs['json']
            self.assertEqual(set(v for k,v in payload['variables'].items() if k.startswith('name')),{'NewA','Новый'})
            self.assertIn('metric:hps',payload['query'])
            self.assertIn('metric:dps',payload['query'])
            self.assertNotIn('Новый',payload['query'])
            self.assertEqual(result['characters'][0]['raw']['keep'],0)
            repeated,stats=reconcile(entries,'EU',{'id':53},4,result,Mock(),'later',fetch_many=Mock(side_effect=AssertionError('Unchanged must stay cached')))
            self.assertEqual(stats,(0,22,0))
            self.assertEqual(post.call_count,1)
            reduced,stats=reconcile(entries[:-1],'EU',{'id':53},4,repeated,Mock(),'later',fetch_many=Mock(side_effect=AssertionError('Removal must not fetch')))
            self.assertEqual(stats,(0,21,1))

    def test_twenty_new_players_make_four_bounded_packages(self):
        entries=[{'name':f'Player{i}','realm':'realm','role':'DD'} for i in range(20)]
        with patch('requests.post',side_effect=lambda *args,**kwargs:response_for(kwargs)) as post:
            result,stats=reconcile(entries,'EU',{'id':53},4,None,Mock(),'now',
                fetch_many=lambda batch:fetch_characters('token',batch,'EU',53,4))
            self.assertEqual(post.call_count,4)
            self.assertEqual([c['name'] for c in result['characters']],[c['name'] for c in entries])
            self.assertTrue(all(sum(k.startswith('name') for k in call.kwargs['json']['variables'])==5 for call in post.call_args_list))

    def test_partial_error_keeps_other_character(self):
        reply=Mock()
        reply.json.return_value={'data':{'characterData':{'p0':None,'p1':{'name':'Good','zoneRankings':{'rankings':[]}}}},
                                 'errors':[{'message':'Private character','path':['characterData','p0','zoneRankings']}]}
        with patch('requests.post',return_value=reply) as post:
            result=fetch_characters('token',[{'name':'Bad','realm':'realm','role':'DD'},{'name':'Good','realm':'realm','role':'DD'}],'EU',53,4)
            self.assertEqual(result[0]['error'],'Private character')
            self.assertEqual(result[1]['error'],'')
            self.assertEqual(result[1]['raw']['characterData']['character']['name'],'Good')
            self.assertEqual(post.call_count,1)

    def test_unknown_healers_corrected_together(self):
        def answer(*args,**kwargs):
            reply=response_for(kwargs)
            if 'metric:hps' not in kwargs['json']['query']:
                for char in reply.json.return_value['data']['characterData'].values():
                    char['zoneRankings']['rankings']=[{'bestSpec':'Holy'}]
            return reply
        with patch('requests.post',side_effect=answer) as post:
            result=fetch_characters('token',[{'name':n,'realm':'realm','role':'Unbekannt'} for n in ('One','Two')],'EU',53,4)
            self.assertEqual(post.call_count,2)
            self.assertIn('metric:hps',post.call_args.kwargs['json']['query'])
            self.assertTrue(all(c['role']=='Heal' for c in result))

    def test_budget_tracking_works_with_diagnostics_off_and_errors(self):
        ledger=UsageLedger()
        scope=current_usage.set(ledger)
        debug=current_log.set(None)
        try:
            with patch('requests.post',side_effect=lambda *args,**kwargs:response_for(kwargs)) as post:
                fetch_characters('secret-token',[{'name':'Player','realm':'realm','role':'DD'}],'EU',53,4)
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.kwargs['json']['query'].count('raidAnalyzerRate:'),1)
            with patch('requests.post',side_effect=requests.Timeout('secret-token')):
                with self.assertRaises(requests.Timeout):request_graph('token','query {worldData{zones{id}}}')
            state=ledger.snapshot()
            self.assertEqual(state['calls'],2)
            self.assertEqual(state['known_cost'],10.02)
            self.assertEqual(state['unknown_calls'],1)
            self.assertEqual(state['latest']['spent'],101)
            self.assertNotIn('secret-token',str(state))
        finally:
            current_usage.reset(scope)
            current_log.reset(debug)

    def test_out_of_order_quota_and_new_hour(self):
        ledger=UsageLedger()
        def data(spent,reset=100):return {'limitPerHour':3600,'pointsSpentThisHour':spent,'pointsResetIn':reset,'currentRequest':{'application':5.01}}
        ledger.record(data(100),'query','target','OK')
        ledger.record(data(90),'query','target','OK')
        self.assertEqual(ledger.snapshot()['latest']['spent'],100)
        ledger.record(data(5,3600),'query','target','OK')
        self.assertEqual(ledger.snapshot()['latest']['spent'],5)
        self.assertEqual(request_cost({'currentRequest':{'application':0,'total':9}}),9)

    def test_http_429_is_recorded_without_fabricating_points(self):
        ledger=UsageLedger()
        scope=current_usage.set(ledger)
        reply=Mock()
        reply.status_code=429
        reply.raise_for_status.side_effect=requests.HTTPError('Too many requests',response=reply)
        try:
            with patch('requests.post',return_value=reply):
                result=fetch_characters('token',[{'name':'Player','realm':'realm','role':'DD'}],'EU',53,4)
            self.assertTrue(result[0]['error'])
            self.assertEqual(ledger.snapshot()['rows'][0]['Status'],'HTTP 429')
            self.assertIsNone(ledger.snapshot()['rows'][0]['Punkte'])
        finally:
            current_usage.reset(scope)
