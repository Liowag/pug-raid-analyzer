from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase
from unittest.mock import Mock, patch
import requests
from api_diagnostics import CallLog, current_log
from analyzer import request_graph, token_for, QUERY
from roster import reconcile


class DiagnosticsTests(TestCase):
    def test_calls_include_errors_and_exclude_credentials(self):
        log=CallLog()
        context=current_log.set(log)
        try:
            response=Mock()
            response.json.return_value={'access_token':'sensitive-token'}
            with patch('requests.post',return_value=response):
                self.assertEqual(token_for('secret-client','secret-password'),'sensitive-token')
            with patch('requests.post',side_effect=requests.Timeout('sensitive-token')):
                with self.assertRaises(requests.Timeout):
                    request_graph('sensitive-token',QUERY,{'name':'Player','server':'realm'})
            rows=log.snapshot()
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[1]['Status'],'Timeout')
            self.assertTrue(all(row['Dauer (s)']>=0 for row in rows))
            self.assertNotIn('sensitive-token',str(rows))
            self.assertNotIn('secret-password',str(rows))
            self.assertNotIn('secret-client',str(rows))
        finally:
            current_log.reset(context)

    def test_parallel_roster_propagates_session_log_and_preserves_order(self):
        barrier=Barrier(3)
        log=CallLog()
        context=current_log.set(log)
        entries=[{'name':str(i),'realm':'realm','role':'DD'} for i in range(3)]
        def fetch(entry):
            barrier.wait(timeout=4)
            self.assertIs(current_log.get(),log)
            return dict(entry)
        try:
            result,stats=reconcile(entries,'EU',{'id':53},4,None,fetch,'now')
            self.assertEqual([c['name'] for c in result['characters']],['0','1','2'])
            self.assertEqual(stats,(3,0,0))
        finally:
            current_log.reset(context)

    def test_sessions_do_not_share_logs(self):
        first,second=CallLog(),CallLog()
        first.add({'Start':'now'})
        self.assertEqual(second.snapshot(),[])
