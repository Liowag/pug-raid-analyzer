"""Import dialog plus non-blocking job polling on the Streamlit UI thread."""
from copy import deepcopy
import streamlit as st
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import wraps
from analysis_options import normalize, estimate
from analysis_jobs import Job, run_base, run_configured
from analyzer import save_analysis
from preferences import selected_roster
from session_storage import current_store
from api_usage import current_usage
from api_diagnostics import current_log


def session_bound(function):
    """Fragments rerun without the outer app's ContextVar scope."""
    @wraps(function)
    def wrapped(*args,**kwargs):
        token=current_store.set(st.session_state.get('_session_store'))
        try:return function(*args,**kwargs)
        finally:current_store.reset(token)
    return wrapped


def tracked_job(call):
    storage=current_store.set(st.session_state.get('_session_store'))
    usage=current_usage.set(st.session_state.get('api_usage'))
    diagnostic=current_log.set(st.session_state.get('api_call_log') if st.session_state.get('api_diagnostics_enabled') else None)
    try:return Job(call)
    finally:
        current_usage.reset(usage);current_log.reset(diagnostic);current_store.reset(storage)


def begin_import(entries,region,zone,difficulty,credentials,token=None):
    old_job=st.session_state.get('_analysis_job')
    if old_job:old_job.cancelled.set()
    previous=st.session_state.get('snapshot')
    group=selected_roster(entries,False)
    offline=st.session_state.get('test_mode',False)
    st.session_state['_analysis_entries']=deepcopy(entries)
    st.session_state['_analysis_credentials']=credentials
    st.session_state['_analysis_offline']=offline
    st.session_state['_analysis_preview']={'region':region,'zone':deepcopy(zone),'difficulty':difficulty,'characters':deepcopy(entries)}
    st.session_state['_analysis_stage']='base'
    st.session_state.pop('_analysis_base',None)
    st.session_state.pop('_analysis_reconfigure',None)
    st.session_state.pop('_analysis_completion',None)
    st.session_state['_analysis_job']=tracked_job(lambda job:run_base(job,group,region,deepcopy(zone),difficulty,previous,credentials,token,offline))
    st.session_state['_analysis_options_waiting']=not st.session_state.get('_analysis_dialog',False)


def open_settings():
    st.session_state['_analysis_dialog']=True
    st.session_state.pop('_analysis_draft_initialized',None)


def cancel_analysis():
    job=st.session_state.get('_analysis_job')
    if job:job.cancelled.set()
    for key in ('_analysis_job','_analysis_stage','_analysis_dialog','_analysis_base','_analysis_preview','_analysis_entries','_rio_async'):
        st.session_state.pop(key,None)


def accept_options(options):
    st.session_state['_accepted_analysis_options']=normalize(options)
    st.session_state['_analysis_dialog']=False
    st.session_state.pop('_analysis_draft_initialized',None)
    st.rerun()


@st.fragment(run_every='1s')
@session_bound
def poll_analysis():
    job=st.session_state.get('_analysis_job')
    if job is None:
        completed=st.session_state.get('_analysis_completion')
        if completed:
            with st.container(key='analysis_activity'):
                (st.warning if completed['errors'] else st.success)(completed['label'])
        return
    if not job.future.done():
        message,progress,preview=job.view()
        if preview is not None and st.session_state.get('_analysis_preview_seen')!=(id(job),id(preview)):
            st.session_state['snapshot']=preview
            st.session_state['_analysis_preview_seen']=(id(job),id(preview))
            st.rerun()
        calls=job.ledger.snapshot()['calls']-job.start_calls if job.ledger else 0
        with st.container(key='analysis_activity'):
            with st.status('Auswertung läuft · '+message,state='running',expanded=True):
                st.progress(progress)
                st.caption(f'{int(time.monotonic()-job.started)} Sekunden in dieser Phase · {calls} API-Aufrufe abgeschlossen. Angezeigte Scores sind Zwischenstände; weitere Daten werden eingearbeitet.')
        return
    stage=st.session_state.get('_analysis_stage')
    if stage=='waiting':
        st.caption('Basisdaten geladen. Bitte Auswertung auswählen.')
        return
    try:
        result=job.future.result()
        st.session_state['snapshot']=result['snapshot']
        if stage=='base':
            st.session_state['_analysis_base']=result
            loaded,reused,removed=result['stats']
            st.session_state['rio_message']=(True,f'Basis: {loaded} angefragt · {reused} beibehalten · {removed} entfernt')
            if not st.session_state.get('_analysis_options_waiting'):
                st.session_state['_analysis_stage']='waiting'
                st.rerun()
        if stage=='base' or stage=='configured_restart':
            options=normalize(st.session_state.get('analysis_options'))
            entries=deepcopy(st.session_state.get('_analysis_entries',[]))
            credentials=st.session_state['_analysis_credentials'];offline=st.session_state['_analysis_offline']
            st.session_state['_analysis_job']=tracked_job(lambda task:run_configured(task,result,entries,options,credentials,offline))
            st.session_state['_analysis_stage']='configured'
            st.rerun()
        if stage=='configured':
            if st.session_state.pop('_analysis_reconfigure',False):
                options=normalize(st.session_state.get('analysis_options'))
                entries=deepcopy(st.session_state['_analysis_entries'])
                credentials=st.session_state['_analysis_credentials'];offline=st.session_state['_analysis_offline']
                st.session_state['_analysis_job']=tracked_job(lambda task:run_configured(task,result,entries,options,credentials,offline))
                st.rerun()
            st.session_state.pop('_analysis_job',None)
            st.session_state.pop('_analysis_stage',None)
            st.session_state['_analysis_base']=result
            loaded,reused,removed=result['stats']
            failures=sum(bool(c.get('error')) for c in result['snapshot']['characters'])
            errors=result['errors']
            finished=datetime.now(ZoneInfo('Europe/Budapest')).strftime('%H:%M:%S')
            st.session_state['_analysis_completion']={'errors':bool(failures or errors),
                'label':f'Auswertung fertig · {finished} · {len(result["snapshot"]["characters"])} Spieler · '+
                (f'{failures} Basisfehler, {len(errors)} Detailfehler; übrige Daten wurden ausgewertet.' if failures or errors else 'Alle angeforderten Daten verarbeitet.')}
            st.session_state['rio_message']=(not failures and not errors,
                f'{loaded} angefragt · {reused} beibehalten · {removed} entfernt'+
                (f' · {failures} Basisfehler' if failures else '')+
                (f" · {len(errors)} Detailfehler: "+'; '.join(errors) if errors else ''))

            st.rerun()
    except Exception as error:
        st.session_state.pop('_analysis_job',None)
        st.session_state['_analysis_completion']={'errors':True,'label':'Auswertung beendet mit Fehler: '+str(error)}
        st.session_state['rio_message']=(False,'Hintergrund-Auswertung fehlgeschlagen: '+str(error))
        st.rerun()


def continue_with_options(credentials):
    """Called before widgets, so choice changes cannot mutate instantiated widgets."""
    options=st.session_state.pop('_accepted_analysis_options',None)
    if options is None:return
    st.session_state['analysis_options']=options
    st.session_state['include_applicants']=options['include_applicants']
    st.session_state['difficulty']='Mythic' if options['difficulty']==5 else 'Heroic'
    st.session_state['_analysis_options_waiting']=True
    if not all(credentials) and not st.session_state.get('test_mode'):return
    stage=st.session_state.get('_analysis_stage')
    if stage=='waiting':
        st.session_state['_analysis_stage']='configured_restart'
    elif stage=='configured':
        st.session_state['_analysis_reconfigure']=True
        st.session_state['_analysis_job'].cancelled.set()
    elif not st.session_state.get('_analysis_job'):
        snapshot=st.session_state.get('snapshot')
        if snapshot:
            entries=st.session_state.get('_rio_roster') or [dict(c) for c in snapshot['characters']]
            st.session_state['_analysis_dialog']=False
            begin_import(entries,snapshot['region'],snapshot['zone'],snapshot['difficulty'],credentials)


@st.dialog('Was möchtest du auswerten?',width='small',dismissible=False)
@session_bound
def analysis_dialog():
    if not st.session_state.get('_analysis_draft_initialized'):
        settings=normalize(st.session_state.get('analysis_options'))
        for key,value in settings.items():st.session_state['analysis_draft_'+key]=value
        st.session_state['_analysis_draft_initialized']=True
    job=st.session_state.get('_analysis_job')
    st.caption('Die Basisdaten der Gruppe laden bereits im Hintergrund.' if job and not job.future.done()
               else 'Basisdaten werden wiederverwendet; Zusatzdaten erst nach deiner Auswahl.')
    scoring=st.toggle('Scoring aktivieren',key='analysis_draft_scoring',help='Aus: nur Gruppen- und Bossdaten, keine automatischen Zusatzabfragen.')
    include=st.toggle('Bewerber auswerten',key='analysis_draft_include_applicants')
    difficulty=st.selectbox('Ziel-Schwierigkeit',[4,5],format_func=lambda v:'Heroic' if v==4 else 'Mythic',key='analysis_draft_difficulty')
    a,b,c=st.columns(3)
    abyss=a.number_input('Abyss',min_value=0,max_value=8,step=1,key='analysis_draft_abyss',disabled=not scoring)
    nym=b.number_input('Nymrissa',min_value=0,max_value=1,step=1,key='analysis_draft_nymrissa',disabled=not scoring)
    kith=c.number_input('Kith’ix',min_value=0,max_value=1,step=1,key='analysis_draft_kithix',disabled=not scoring)
    st.caption('Die Bossanzahl gilt ab Raidbeginn in WCL-Reihenfolge. Zusammen sind bis zu zehn Bosse möglich.')
    detailed=st.toggle('Detaillierte Kill-Historie',key='analysis_draft_detailed',disabled=not scoring,
        help='Zeitgewichtete Logs, ID-Warnungen und Itemlevel aus dem neuesten verfügbaren Kill-Log der Zielbosse. Bei Heroic zusätzlich Mythic-Erfahrung.')
    options=normalize({'scoring':scoring,'include_applicants':include,'difficulty':difficulty,
                       'abyss':abyss,'nymrissa':nym,'kithix':kith,'detailed':detailed,
                       'item_reference':st.session_state['analysis_draft_item_reference']})
    if options['detailed']:
        options['item_reference']=st.number_input('Itemlevel-Referenz für Bonus',min_value=1,max_value=1000,step=1,key='analysis_draft_item_reference')
    snapshot=st.session_state.get('_analysis_preview') or st.session_state.get('snapshot')
    base=st.session_state.get('_analysis_base')
    if base:snapshot=base['snapshot']
    job=st.session_state.get('_analysis_job')
    if job and job.future.done():
        try:snapshot=job.future.result()['snapshot']
        except Exception:pass
    if snapshot:
        preview={**snapshot,'difficulty':difficulty,'characters':selected_roster(st.session_state.get('_analysis_entries',snapshot['characters']),include)}
        # Use real cached character timestamps for accurate identity/context hits.
        from roles import identity
        old={identity(c['name'],c['realm']):c for c in snapshot['characters']} if snapshot['difficulty']==difficulty else {}
        preview['characters']=[old.get(identity(c['name'],c['realm']),c) for c in preview['characters']]
        cost=estimate(preview,options)
        st.info(f"Detailmodus: ca. {cost['points']:g} WCL-Punkte · {cost['calls']} zusätzliche API-Aufrufe für {cost['players']} Spieler. Keine KI-Tokens. Basisdaten separat; Cache, Fehler und Rollen-Korrekturen können den Wert ändern.")
    else:st.caption('Kostenschätzung erscheint, sobald die Basisdaten verfügbar sind. Grob je Spieler/Boss 1,01 Punkte; Heroic zusätzlich ca. 5,01 Punkte pro Spieler für Mythic.')
    poll_analysis()
    if st.button('Auswertung starten',type='primary',disabled=scoring and abyss+nym+kith==0,key='analysis_accept'):
        accept_options(options)
