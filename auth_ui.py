"""Per-browser-session WCL credentials; never use deployment/local defaults."""
import hashlib
import time
import requests
import streamlit as st
from analyzer import token_for


def fingerprint(cid,secret):return hashlib.sha256((cid+':'+secret).encode()).hexdigest()


def current_credentials():
    cid=st.session_state.get('wcl_client_id','').strip()
    secret=st.session_state.get('wcl_client_secret','').strip()
    return cid,secret


def is_verified():
    cid,secret=current_credentials()
    return bool(cid and secret and st.session_state.get('_verified_credentials')==fingerprint(cid,secret))


def forget_credentials():
    job=st.session_state.get('_analysis_job')
    if job:job.cancelled.set()
    st.session_state.clear()


def render_credentials():
    verified=is_verified()
    with st.expander('Dein Warcraft-Logs-API-Zugang',expanded=not verified):
        st.caption('Jeder Nutzer benötigt eine eigene Client ID und ein eigenes Client Secret. Die Zugangsdaten werden zur Verarbeitung an diesen App-Server und Warcraft Logs übertragen, nur für diese Sitzung im Arbeitsspeicher gehalten und nicht in Dateien gespeichert. Nach einem Neuladen kann eine erneute Eingabe nötig sein.')
        st.markdown('[Eigenen WCL-API-Client anlegen](https://www.warcraftlogs.com/api/clients) · [WCL-Anleitung](https://www.warcraftlogs.com/api/docs)')
        with st.form('wcl_credentials',clear_on_submit=False):
            st.text_input('Client ID',key='wcl_client_id')
            st.text_input('Client Secret',key='wcl_client_secret',type='password')
            submitted=st.form_submit_button('Zugang prüfen und verbinden')
        if submitted:
            cid,secret=current_credentials()
            previous=st.session_state.get('_verified_credentials')
            from import_ui import cancel_analysis
            cancel_analysis()
            if previous and previous!=fingerprint(cid,secret):
                for key in list(st.session_state):
                    if key not in ('wcl_client_id','wcl_client_secret'):st.session_state.pop(key,None)
            st.session_state.pop('_verified_credentials',None)
            st.session_state.pop('_oauth_cache',None)
            job=st.session_state.get('_analysis_job')
            if job:job.cancelled.set()
            if not cid or not secret:st.error('Bitte Client ID und Client Secret eingeben.')
            else:
                try:
                    with st.spinner('WCL-Zugang prüfen …'):token=token_for(cid,secret)
                    fp=fingerprint(cid,secret)
                    st.session_state['_verified_credentials']=fp
                    st.session_state['_oauth_cache']=(fp,token,time.monotonic()+300)
                    st.rerun()
                except requests.HTTPError as error:
                    code=getattr(error.response,'status_code',None)
                    st.error('WCL lehnt die Zugangsdaten ab. Bitte ID und Secret prüfen.' if code in (400,401,403) else 'WCL ist derzeit nicht erreichbar. Bitte erneut versuchen.')
                except (requests.RequestException,ValueError,KeyError):
                    st.error('Die Verbindung zu WCL konnte nicht geprüft werden. Bitte erneut versuchen.')
        if is_verified():st.success('Eigener WCL-Zugang verbunden.')
        st.button('Zugangsdaten und Sitzungsdaten vergessen',key='forget_credentials',on_click=forget_credentials)
    return current_credentials()
