from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest import TestCase
from unittest.mock import patch, Mock
from pathlib import Path
import json
import requests
from streamlit.testing.v1 import AppTest
from analyzer import ROOT, DATA, save_json, load_json
from session_storage import SessionStore, current_store, json_exists


def app():return AppTest.from_file(str(ROOT/'app.py'),default_timeout=30)


def connect(instance,cid,secret):
    instance.text_input(key='wcl_client_id').set_value(cid)
    instance.text_input(key='wcl_client_secret').set_value(secret)
    next(b for b in instance.button if b.label=='Zugang prüfen und verbinden').click().run()


class CloudSessionTests(TestCase):
    def test_no_credentials_from_disk_or_environment_and_import_cannot_fetch(self):
        with patch('analyzer.load_credentials',side_effect=AssertionError('No local credentials')),patch.dict('os.environ',{'WCL_CLIENT_ID':'server-id','WCL_CLIENT_SECRET':'server-secret'}),patch('requests.post',side_effect=AssertionError('No network')) as post:
            instance=app();instance.run()
            self.assertFalse(instance.exception)
            self.assertEqual(instance.text_input(key='wcl_client_id').value,'')
            self.assertEqual(instance.text_input(key='wcl_client_secret').value,'')
            instance.text_area(key='rio_export').set_value(json.dumps({'region':'eu','group':['4-Test-realm']})).run()
            self.assertNotIn('_analysis_job',instance.session_state)
            self.assertNotIn('snapshot',instance.session_state)
            post.assert_not_called()

    def test_each_browser_needs_its_own_login_and_forget_clears_only_itself(self):
        with patch('auth_ui.token_for',side_effect=lambda cid,secret:'token-'+cid) as token,patch('requests.post',side_effect=AssertionError('No graph calls')):
            first=app();second=app();first.run();second.run()
            connect(first,'alice-id','alice-secret')
            self.assertFalse(first.exception)
            self.assertIn('_verified_credentials',first.session_state)
            self.assertNotIn('_verified_credentials',second.session_state)
            connect(second,'bob-id','bob-secret')
            self.assertFalse(second.exception)
            self.assertNotEqual(first.session_state['_session_store'],second.session_state['_session_store'])
            token.assert_any_call('alice-id','alice-secret');token.assert_any_call('bob-id','bob-secret')
            first.button(key='forget_credentials').click().run()
            self.assertFalse(first.exception)
            self.assertEqual(first.text_input(key='wcl_client_secret').value,'')
            self.assertNotIn('_oauth_cache',first.session_state)
            self.assertIn('_verified_credentials',second.session_state)
            self.assertEqual(second.text_input(key='wcl_client_secret').value,'bob-secret')

    def test_wrong_secret_does_not_unlock_live_access(self):
        reply=Mock();reply.status_code=401
        with patch('auth_ui.token_for',side_effect=requests.HTTPError('private-secret-value',response=reply)):
            instance=app();instance.run();connect(instance,'id','private-secret-value')
            self.assertFalse(instance.exception)
            self.assertNotIn('_verified_credentials',instance.session_state)
            self.assertNotIn('_oauth_cache',instance.session_state)
            self.assertFalse(any('private-secret-value' in e.value for e in instance.error))

    def test_switching_credentials_drops_previous_user_data(self):
        with patch('auth_ui.token_for',side_effect=lambda cid,secret:'token-'+cid):
            instance=app();instance.run();connect(instance,'first','first-secret')
            old_store=instance.session_state['_session_store'];old_store.write('old',{'private':True})
            instance.session_state['names']='previous-private-roster'
            connect(instance,'second','second-secret')
            self.assertFalse(instance.exception)
            self.assertIsNot(instance.session_state['_session_store'],old_store)
            self.assertNotEqual(instance.session_state.get('names',''),'previous-private-roster')

    def test_fragment_uses_its_session_scope_and_restores_context(self):
        from import_ui import session_bound
        store=SessionStore();key=DATA/'scoring'/'fragment.json'
        scope=current_store.set(None)
        try:
            with patch('import_ui.st.session_state',{'_session_store':store}):
                @session_bound
                def call():
                    save_json(key,{'owner':'fragment'});return load_json(key)
                self.assertEqual(call(),{'owner':'fragment'})
                self.assertIsNone(current_store.get())
                self.assertTrue(store.has('scoring/fragment.json'))
        finally:current_store.reset(scope)

    def test_runtime_cache_is_isolated_in_memory_even_in_workers(self):
        path=DATA/'boss_details'/'reports'/'same-fight.json'
        a=SessionStore();b=SessionStore();scope=current_store.set(a)
        try:
            with patch('pathlib.Path.write_text',side_effect=AssertionError('No disk writes')):
                save_json(path,{'owner':'A'})
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(copy_context().run,load_json,path).result(),{'owner':'A'})
                current_store.set(b)
                self.assertFalse(json_exists(path))
                with self.assertRaises(FileNotFoundError):load_json(path)
                save_json(path,{'owner':'B'})
                current_store.set(a)
                self.assertEqual(load_json(path),{'owner':'A'})
        finally:current_store.reset(scope)

    def test_cache_bounds_and_public_demo(self):
        store=SessionStore(max_bytes=100,max_entries=1)
        store.write('one',{'value':1});store.write('two',{'value':2})
        self.assertFalse(store.has('one'));self.assertTrue(store.has('two'))
        demo=load_json(DATA/'public_demo.json')
        self.assertTrue(all(c['name'].startswith('Demo') for c in demo['characters']))
