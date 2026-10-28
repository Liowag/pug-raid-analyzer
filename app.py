import html
import hashlib
import time
from api_diagnostics import CallLog, current_log
from api_usage import UsageLedger, current_usage
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from analyzer import (ROOT, DEFAULT_ZONE, DIFFICULTIES, cached_zones,
                      fetch_character, fetch_characters, load_test_snapshot, now,
                      parse_lines, refresh_zones, save_analysis, token_for)
from roster import roster_from_export, reconcile, pending_entries
from preferences import selected_roster
from ui_components import mount_paste, frame_table
from rankings import build_tables, profile_links
from auth_ui import render_credentials, current_credentials, is_verified
from session_storage import SessionStore, current_store
from roles import identity
from boss_ui import navigation, player_rows, player_groups, focus, stored_results
from boss_details import ULATEK, FIELDS
from scoring_ui import render_scoring, render_weekly_warning
from analysis_options import normalize, group_identity, same_group
from import_ui import begin_import, open_settings, continue_with_options, analysis_dialog, poll_analysis, cancel_analysis

st.set_page_config(page_title='WoW Raid Analyzer', page_icon='⚔', layout='wide')
if '_session_store' not in st.session_state:st.session_state['_session_store']=SessionStore()
store_context=current_store.set(st.session_state['_session_store'])

st.markdown('<style>'+ (ROOT/'style.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)



def use_test_data():
    if st.session_state.get('_analysis_job'):st.session_state['_analysis_job'].cancelled.set()
    for key in ('_analysis_job','_analysis_stage','_analysis_dialog','_analysis_base','_rio_async'):
        st.session_state.pop(key,None)
    st.session_state.pop('_rio_roster',None)
    st.session_state['test_mode']=True
    st.session_state.pop('pending_roster',None)
    st.session_state.pop('rio_message',None)
    set_test_data()


def set_test_data():
    if st.session_state.get('test_mode'):
        snapshot=load_test_snapshot(public=True)
        st.session_state['snapshot']=snapshot
        st.session_state['names']='\n'.join(f"{c['name']}-{c['realm']}" for c in snapshot['characters'])
        st.session_state['region']=snapshot['region']
        st.session_state['difficulty']=next(name for name,value in DIFFICULTIES.items() if value==snapshot['difficulty'])
        st.session_state['zone_id']=snapshot['zone']['id']
    else:
        st.session_state.pop('snapshot',None)
    st.session_state['view']='Raidübersicht'


def import_rio(text=None):
    try:
        text = text if text is not None else st.session_state.get('rio_export','')
        entries, region = roster_from_export(text)
        previous_group=st.session_state.get('_last_group_identity')
        current_group=group_identity(entries,region)
        reuse=same_group(previous_group,current_group) and 'analysis_options' in st.session_state
        if not reuse and not st.session_state.get('test_mode'):
            st.session_state['analysis_options']=normalize({'include_applicants':st.session_state.get('include_applicants',True)})
            st.session_state['_analysis_dialog']=True
            st.session_state.pop('_analysis_draft_initialized',None)
        st.session_state['_last_group_identity']=current_group
        st.session_state['_rio_roster']=entries
        st.session_state['region'] = region
        if st.session_state.get('test_mode'):
            st.session_state['pending_roster'] = selected_roster(entries,st.session_state.get('include_applicants',True))
        else:
            st.session_state['_rio_async']=True
            st.session_state.pop('pending_roster',None)
            if not is_verified():
                st.session_state['_analysis_dialog']=False
                st.session_state['_analysis_prompt_after_auth']=True
        st.session_state['rio_export'] = text
        st.session_state['rio_message'] = (True, 'Liste eingefügt; Abgleich läuft …')
    except ValueError as error:
        st.session_state['rio_message'] = (False, str(error))


def pasted():
    import_rio(st.session_state['rio_global_paste'].paste)


def import_names():
    cancel_analysis()
    players = parse_lines(st.session_state.get('names',''))
    if not players:
        st.session_state['rio_message'] = (False, 'Bitte Namen im Format Name-Server eingeben.')
        return
    st.session_state.pop('_rio_roster',None)
    st.session_state['pending_roster'] = [{'name':name, 'realm':realm, 'role':'Unbekannt'} for name,realm in players]


def applicants_changed():
    include=st.session_state['include_applicants']
    if 'analysis_options' in st.session_state:
        st.session_state['analysis_options']['include_applicants']=include
    if 'analysis_options' in st.session_state:
        st.session_state['_accepted_analysis_options']=normalize(st.session_state['analysis_options'])
        return
    snapshot=st.session_state.get('snapshot')
    entries=st.session_state.get('_rio_roster')
    if entries is None and snapshot:
        entries=[{'name':c['name'],'realm':c['realm'],'role':c.get('role','Unbekannt'),
                  'applicant':c.get('applicant',False)} for c in snapshot['characters']]
    if entries is not None:
        st.session_state['pending_roster']=selected_roster(entries,include)
    st.session_state['view']='Raidübersicht'


mount_paste(pasted)


def formatted(value):
    return '–' if value is None or pd.isna(value) else f'{value:.1f}'


def captured_label(value):
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo('Europe/Budapest')).strftime('%d.%m.%Y, %H:%M Uhr')
    except (ValueError,TypeError):
        return str(value)


def banner(title,eyebrow,subtitle):
    st.markdown(f'<div class="raid-banner"><div class="eyebrow">{html.escape(eyebrow)}</div>'
                f'<h2>{html.escape(title)}</h2><p>{html.escape(subtitle)}</p></div>',unsafe_allow_html=True)


def open_character(index):
    st.session_state['_detail_index']=index
    st.session_state['view']='Charakterdetails'
    st.session_state['_scroll_top']=True


def back_to_overview():
    st.session_state['view']='Raidübersicht'
    st.session_state['_scroll_top']=True
if st.session_state.get('test_mode') and 'snapshot' not in st.session_state:
    set_test_data()

def change_context():
    if st.session_state.get('_analysis_job'):st.session_state['_analysis_job'].cancelled.set()
    for key in ('_analysis_job','_analysis_stage','_analysis_dialog','_analysis_base','_rio_async'):
        st.session_state.pop(key,None)
    if 'analysis_options' in st.session_state:
        st.session_state['analysis_options']['difficulty']=DIFFICULTIES.get(st.session_state.get('difficulty'),4)
    snapshot=st.session_state.pop('snapshot',None)
    if snapshot:
        st.session_state['pending_roster']=[{'name':c['name'],'realm':c['realm'],
            'role':c.get('role','Unbekannt'),'applicant':c.get('applicant',False)} for c in snapshot['characters']]
    st.session_state['test_mode']=False
    st.session_state['view']='Raidübersicht'
    for key in ('heart_context','heart_results','_boss_id','_detail_index','rio_message'):
        st.session_state.pop(key,None)


def force_reload():
    cancel_analysis()
    snapshot=st.session_state.get('snapshot')
    if not snapshot:
        return
    st.session_state['pending_roster']=[{'name':c['name'],'realm':c['realm'],
        'role':c.get('role','Unbekannt'),'applicant':c.get('applicant',False)} for c in snapshot['characters']]
    st.session_state['_force_reload']=True
    st.session_state['test_mode']=False
    st.session_state['view']='Raidübersicht'


continue_with_options(current_credentials() if is_verified() else ('',''))

st.title('WoW Raid Analyzer')
cid,secret=render_credentials()
live_enabled=is_verified()
if live_enabled and st.session_state.pop('_analysis_prompt_after_auth',False):open_settings()
if not live_enabled:
    st.info('Für Live-Auswertungen zuerst deinen eigenen WCL-Zugang verbinden. Testdaten funktionieren ohne Zugangsdaten.')
if 'include_applicants' not in st.session_state:
    st.session_state['include_applicants']=True
with st.expander('Einstellungen & API-Diagnose', expanded=False):
    st.button('Auswertung und Ziel ändern',on_click=open_settings,key='edit_analysis_options')
    st.toggle('Bewerber berücksichtigen',key='include_applicants',on_change=applicants_changed,
        help='Ausgeschaltet: Nur aktuelle Gruppenmitglieder auswerten. Die Einstellung gilt nur für deine Sitzung, auch für Heart-Details und erneutes Laden.')
    diagnose=st.toggle('API-Diagnose aktivieren',key='api_diagnostics_enabled',
        help='Misst zukünftige HTTP-Aufrufe dieser Sitzung. Keine Tokens, Secrets oder Rohantworten im Protokoll.')
    if 'api_call_log' not in st.session_state:
        st.session_state['api_call_log']=CallLog()
    region=st.selectbox('Region',['EU','US','KR','TW'],key='region',on_change=change_context)
    refresh=st.button('Zonenliste aktualisieren',disabled=st.session_state.get('test_mode',False) or not live_enabled)
    if st.session_state.get('settings_notice'):
        st.warning(st.session_state['settings_notice'])
diagnostic_context=current_log.set(st.session_state['api_call_log'] if diagnose else None)
usage_fingerprint=hashlib.sha256((cid+':'+secret).encode()).hexdigest()
if st.session_state.get('_usage_fingerprint')!=usage_fingerprint:
    st.session_state['_usage_fingerprint']=usage_fingerprint
    st.session_state['api_usage']=UsageLedger()
usage_context=current_usage.set(st.session_state['api_usage'])
test_mode=st.session_state.get('test_mode',False)


def get_token():
    if not is_verified():raise ValueError('Bitte zuerst deinen eigenen WCL-Zugang verbinden.')
    fingerprint=hashlib.sha256((cid+':'+secret).encode()).hexdigest()
    cached=st.session_state.get('_oauth_cache')
    if not cached or cached[0]!=fingerprint or time.monotonic()>=cached[2]:
        cached=(fingerprint,token_for(cid,secret),time.monotonic()+300)
        st.session_state['_oauth_cache']=cached
    return cached[1]


try:
    zones,zone_time,has_cache=cached_zones()
except (OSError,ValueError,KeyError):
    zones,zone_time,has_cache=[],None,False
    st.warning('Die lokale Zonenliste konnte nicht gelesen werden.')
if live_enabled and not test_mode and (refresh or (not has_cache and not st.session_state.get('zone_attempted') and cid and secret)):
    st.session_state['zone_attempted']=True
    try:
        with st.spinner('Zonen-Katalog einmalig abrufen …'):
            zones=refresh_zones(get_token())
        has_cache=True
        st.session_state.pop('zone_notice',None)
    except Exception as error:
        st.session_state['zone_notice']=f'Zonen-Katalog derzeit nicht abrufbar: {error}. Die lokale Basisliste bleibt verfügbar.'
if not test_mode and st.session_state.get('zone_notice'):
    st.warning(st.session_state['zone_notice'])

snapshot=st.session_state.get('snapshot')
if test_mode and snapshot:
    zones=[zone for zone in zones if zone['id']!=snapshot['zone']['id']]+[snapshot['zone']]
zone_ids=[zone['id'] for zone in zones]
if st.session_state.get('zone_id') not in zone_ids and zone_ids:
    st.session_state['zone_id']=DEFAULT_ZONE if DEFAULT_ZONE in zone_ids else zone_ids[0]
context_label, difficulty_column, zone_column=st.columns([5,1.3,2.4],vertical_alignment='bottom')
context_placeholder=context_label.empty()
current_zone=next((z for z in zones if z['id']==st.session_state.get('zone_id')),None)
available=[name for name in ['Heroic','Mythic','Normal'] if not current_zone or DIFFICULTIES[name] in [d['id'] for d in current_zone.get('difficulties',[])]]
if st.session_state.get('difficulty') not in available:
    st.session_state['difficulty']=available[0]
difficulty=difficulty_column.selectbox('Schwierigkeit',available,key='difficulty',on_change=change_context)
diffid=DIFFICULTIES[difficulty]
selected_id=zone_column.selectbox('Raidzone',zone_ids,key='zone_id',on_change=change_context,
    format_func=lambda value: next(zone['name'] for zone in zones if zone['id']==value)) if zone_ids else None
selected=next((zone for zone in zones if zone['id']==selected_id),None)
quota_placeholder=st.empty()
if test_mode and snapshot:
    st.caption(f"Testdaten vom {captured_label(snapshot['captured_at'])} · offline. Eine Änderung von Raidzone oder Schwierigkeit startet eine Live-Auswertung.")

st.caption('Raider.IO-Liste überall auf dieser Seite mit Strg+V einfügen: Gruppe und Bewerber werden sofort abgeglichen.')
with st.expander('Raider.IO-Export / manueller Import', expanded='snapshot' not in st.session_state):
    st.text_area('Raider.IO-Export', key='rio_export', height=90, on_change=import_rio)
    st.caption('In WoW /rio group kopieren. '+('Enthaltene Bewerber werden ebenfalls übernommen.' if st.session_state['include_applicants'] else 'Bewerber sind in den Einstellungen deaktiviert; nur Gruppenmitglieder werden ausgewertet.'))
    st.text_area('Alternativ: Namen manuell eingeben (Name-Server pro Zeile)', key='names', height=90)
    import_buttons=st.columns([1,1,4])
    import_buttons[0].button('Namensliste auswerten', on_click=import_names)
    import_buttons[1].button('Testdaten nutzen', on_click=use_test_data)
    if test_mode:
        import_buttons[2].button('Live-Daten laden', on_click=change_context,
                                 help='Testmodus verlassen und die aktuelle Spielerliste live neu laden.')

if (st.session_state.get('snapshot') or {}).get('characters'):
    st.button('Alle Daten neu laden',key='force_reload',on_click=force_reload,
              help='Rankings aller Spieler der aktuellen Liste live erneut abrufen, auch wenn bereits Daten vorhanden sind.')

if st.session_state.get('_rio_async') and selected and (live_enabled or test_mode):
    st.session_state.pop('_rio_async',None)
    base_difficulty=normalize(st.session_state.get('analysis_options'))['difficulty']
    begin_import(st.session_state['_rio_roster'],region,selected,base_difficulty,(cid,secret),
        st.session_state.get('_oauth_cache',(None,None,0))[1] if time.monotonic()<st.session_state.get('_oauth_cache',(None,None,0))[2] else None)

if not st.session_state.get('_analysis_dialog'):
    poll_analysis()

entries = st.session_state.pop('pending_roster', None) if live_enabled or test_mode else None
force_requested=st.session_state.pop('_force_reload',False)
if entries is not None and selected:
    try:
        previous = None if force_requested else st.session_state.get('snapshot')
        token = None
        if not test_mode and entries and not selected.get('encounters'):
            zones=refresh_zones(get_token())
            selected=next(z for z in zones if z['id']==selected['id'])
        if not test_mode and pending_entries(entries,region,selected,diffid,previous):
            if not (cid and secret):
                raise ValueError('Bitte API-Zugangsdaten eingeben und die Liste erneut einfügen.')
            # Resolve authentication once before parallel workers start.
            token = get_token()
        analysis_started=time.perf_counter()
        def fetch_entry(entry):
            if test_mode:
                return {**entry, 'raw': {}, 'error': 'Für diesen Spieler liegen keine passenden Testdaten vor.'}
            if not (cid and secret):
                raise ValueError('Bitte API-Zugangsdaten eingeben und die Liste erneut einfügen.')
            return fetch_character(token, entry['name'], entry['realm'], region, selected['id'], diffid, entry['role'])
        with st.spinner('Spielerliste abgleichen …'):
            result, (loaded, reused, removed) = reconcile(entries, region, selected, diffid, previous, fetch_entry, now(),
                fetch_many=None if test_mode else lambda package: fetch_characters(token,package,region,selected['id'],diffid))
        st.session_state['last_analysis_duration']=time.perf_counter()-analysis_started
        st.session_state['snapshot'] = result
        if force_requested:
            for key in ('heart_context','heart_results','_boss_id','_detail_index'):
                st.session_state.pop(key,None)
        st.session_state['view'] = 'Raidübersicht'
        failures=sum(bool(c.get('error')) for c in result['characters'])
        st.session_state['rio_message'] = (not failures, f'{loaded} angefragt · {reused} beibehalten · {removed} entfernt' + (f' · {failures} fehlgeschlagen; beim nächsten Import erneut versuchen' if failures else '') + (' · offline mit Testdaten' if test_mode else ''))
        if not test_mode:
            if 'analysis_options' in st.session_state and normalize(st.session_state['analysis_options'])['detailed']:
                begin_import(st.session_state.get('_rio_roster') or entries,region,selected,diffid,(cid,secret),token)
    except Exception as error:
        st.session_state['rio_message'] = (False, f'Auswertung fehlgeschlagen: {error}')
if 'rio_message' in st.session_state:
    ok, message = st.session_state['rio_message']
    (st.success if ok else st.error)(message)

snapshot=st.session_state.get('snapshot')
if snapshot and snapshot['characters']:
    rows,details=build_tables(snapshot)
    df=pd.DataFrame(rows)
    detail=pd.DataFrame(details)
    context=f"{snapshot['zone']['name']} · {next(name for name,value in DIFFICULTIES.items() if value==snapshot['difficulty'])} · {snapshot['region']}"
    context_placeholder.caption(f'Angezeigte Auswertung: {context}')
    if normalize(st.session_state.get('analysis_options'))['detailed']:
        render_weekly_warning(snapshot,get_token,test_mode)
    nav,main=st.columns([1.5,5.5])
    with nav:
        navigation(snapshot)
    with main:
        if st.session_state.get('view')=='Bossfokus':
            focus(snapshot,rows,details,open_character,test_mode,get_token)
        elif st.session_state.get('view')!='Charakterdetails':
            st.session_state['view']='Raidübersicht'
            if normalize(st.session_state.get('analysis_options'))['scoring']:
                with st.expander('Gruppenscore · Zielauswertung',expanded=True):
                    render_scoring(snapshot,get_token,test_mode,open_character)
            a,b,c=st.columns(3)
            a.metric('Tanks',sum(row['Rolle']=='Tank' for row in rows))
            b.metric('Damage Dealer',sum(row['Rolle']=='DD' for row in rows))
            c.metric('Heiler',sum(row['Rolle']=='Heal' for row in rows))
            notices=[row for row in rows if row['Status']!='OK']
            if notices:
                with st.expander(f'Hinweise zu {len(notices)} Charakteren'):
                    for row in notices:
                        st.write(f"{row['Charakter']}: {row['Status']}")
            st.subheader('Spieler-Rankings')
            fields=[field for field in st.session_state.get('boss_columns',[]) if field in FIELDS] if any(e['id']==ULATEK for e in snapshot['zone']['encounters']) else []
            player_rows(rows,snapshot,open_character,fields=fields)
            with st.expander('Bossübersicht',expanded=False):
                metric=st.radio('Wert',['Best','Median'],horizontal=True)
                for label,indices in player_groups(rows,snapshot):
                    with st.expander(label,expanded=True):
                        selected_rows=[rows[index] for index in indices]
                        pairs=[(row['Charakter'],row['Server']) for row in selected_rows]
                        pivot=detail.pivot(index=['Charakter','Server'],columns='Boss',values=metric)
                        pivot=pivot.reindex(index=pd.MultiIndex.from_tuples(pairs,names=['Charakter','Server']),columns=[boss['name'] for boss in snapshot['zone']['encounters']])
                        pivot.index=[pair[0] for pair in pairs]
                        pivot.index.name='Charakter'
                        frame_table(pivot.reset_index(),key='boss_grid_'+str(indices[0]),percent_columns=list(pivot.columns),character_indices=indices,open_character=open_character,region=snapshot['region'])
                st.caption('Perzentilfarben: grau <25 · grün ≥25 · blau ≥50 · violett ≥75 · orange ≥95 · pink ≥99 · gold 100.')
            export=df[['Charakter','Rolle','Metrik','Best Ø','Median Ø','Bosse mit Logs']].copy()
            if fields:
                extra=stored_results(snapshot)
                for field in fields:
                    export['Ula’tek · '+FIELDS[field]]=[extra.get(identity(row['Charakter'],row['Server']),{}).get(field) for row in rows]
            st.download_button('Charakterübersicht CSV',export.to_csv(index=False).encode('utf-8-sig'),'raid_charaktere.csv','text/csv')
            st.download_button('Alle Bosswerte CSV',detail.to_csv(index=False).encode('utf-8-sig'),'raid_bosse.csv','text/csv')
        else:
            st.button('← Zurück zur Übersicht',on_click=back_to_overview)
            index=st.session_state.get('_detail_index',0)
            if index not in range(len(rows)):
                index=0
            row=rows[index]
            banner(row['Charakter'],f"{row['Server']} · {snapshot['region']} · {row['Rolle']}",context)
            a,b,c=st.columns(3)
            a.metric('Best Perf. Ø',formatted(row['Best Ø']))
            b.metric('Median Perf. Ø',formatted(row['Median Ø']))
            c.metric('Bosse mit Logs',f"{row['Bosse mit Logs']} / {len(snapshot['zone']['encounters'])}")
            if row['Status']!='OK':
                st.info(row['Status'])
            links=profile_links(row['Charakter'],row['Server'],snapshot['region'])['links']
            profile_buttons=st.columns([1,1,2])
            profile_buttons[0].link_button('Warcraft Logs öffnen',links[0]['url'])
            profile_buttons[1].link_button('Raider.IO öffnen',links[1]['url'])
            amount='Höchster HPS' if row['Rolle']=='Heal' else 'Höchster DPS'
            bosses=detail[(detail['Charakter']==row['Charakter']) & (detail['Server']==row['Server'])][['Boss','Best','Median',amount,'Kills','Schnellster Kill','Spezialisierung','All-Star-Punkte']]
            st.subheader('Boss-Rankings · Healing' if row['Rolle']=='Heal' else 'Boss-Rankings · Damage')
            frame_table(bosses,key='character_bosses',percent_columns=['Best','Median'])
            st.download_button('Charakterdetails CSV',bosses.to_csv(index=False).encode('utf-8-sig'),'charakter_bosse.csv','text/csv')
        st.caption('Best/Median Ø aus Warcraft Logs. Tanks und DD: Damage-Rankings; Heiler: Healing-Rankings. Fehlende Rankings bleiben leer. Keine öffentlichen Logs sind kein Beweis für fehlende Kills.')
else:
    st.info('Raider.IO-Liste einfügen oder im Importbereich „Testdaten nutzen“ anklicken.')

if st.session_state.pop('_scroll_top',False):
    import streamlit.components.v1 as components
    components.html('''<script>
      requestAnimationFrame(() => {
        const main = window.parent.document.querySelector('[data-testid="stMain"]');
        if (main) main.scrollTo({top:0,behavior:'instant'});
      });
    </script>''',height=0)


usage=st.session_state['api_usage'].snapshot()
latest=usage['latest']
if latest and latest['reset_at']>time.time():
    left=max(0,latest['limit']-latest['spent'])
    minutes=max(0,int((latest['reset_at']-time.time())/60))
    observed=datetime.fromtimestamp(latest['observed_at'],ZoneInfo('Europe/Budapest')).strftime('%H:%M:%S')
    quota_placeholder.caption(f"API-Budget: {latest['spent']:.2f} / {latest['limit']:,.0f} Punkte verbraucht · {left:.2f} verfügbar · Reset in ca. {minutes} Min. · Stand {observed} · {usage['calls']} GraphQL-Aufrufe in dieser Sitzung")
elif latest:
    quota_placeholder.caption(f"API-Budget: letzter Messstand abgelaufen; aktualisiert sich mit dem nächsten API-Aufruf · {usage['calls']} GraphQL-Aufrufe in dieser Sitzung")
else:
    quota_placeholder.caption(f"API-Budget: noch kein aktueller Server-Messwert · {usage['calls']} GraphQL-Aufrufe in dieser Sitzung")
with st.expander('API-Punkte & Stundenbudget',expanded=False):
    st.caption('Immer aktiv, unabhängig von der Diagnose. Das Stundenbudget stammt direkt von Warcraft Logs und kann auch Verbrauch anderer Anwendungen desselben Zugangs enthalten. Der Stand wird mit vorhandenen GraphQL-Anfragen abgeholt; keine zusätzlichen Status-Aufrufe.')
    if usage['rows']:
        st.write(f"Gemessene Kosten dieser Sitzung: {usage['known_cost']:.2f} Punkte · {usage['unknown_calls']} Aufrufe ohne Kostenangabe")
        points=pd.DataFrame(usage['rows'])
        frame_table(points,key='api_usage_table')
        st.caption('Punkte = vom Server gemeldete Kosten dieser Anfrage, sofern verfügbar. Fehlende Angaben bleiben leer; parallele Budgetdifferenzen werden nicht als Anfragekosten geschätzt. HTTP-Fehler werden ebenfalls gezählt. OAuth ist kein GraphQL-Aufruf.')
        st.download_button('Punkteprotokoll CSV',points.to_csv(index=False).encode('utf-8-sig'),'api_points.csv','text/csv')
    else:
        st.caption('Noch keine GraphQL-Aufrufe in dieser Sitzung. Testdaten und Cache-Zugriffe verbrauchen keine API-Punkte.')

with st.expander('API-Aufrufprotokoll',expanded=diagnose):
    st.caption('Aufzeichnung ' + ('aktiv' if diagnose else 'ausgeschaltet') + ' · maximal 500 Aufrufe dieser Sitzung. Laufzeiten umfassen HTTP, Antwortlesen und JSON-Verarbeitung. Parallel ausgeführte Zeiten überlappen.')
    log=st.session_state['api_call_log'].snapshot()
    if log:
        calls=pd.DataFrame(log)
        st.write(f"{len(log)} HTTP-Aufrufe · Summe der Aufrufzeiten: {calls['Dauer (s)'].sum():.2f} s · langsamster Aufruf: {calls['Dauer (s)'].max():.2f} s")
        if 'last_analysis_duration' in st.session_state:
            st.caption(f"Letzter Listenabgleich ohne OAuth: {st.session_state['last_analysis_duration']:.2f} s verstrichene Zeit.")
        frame_table(calls,key='api_calls')
        st.download_button('API-Protokoll CSV',calls.to_csv(index=False).encode('utf-8-sig'),'api_calls.csv','text/csv')
    else:
        st.caption('Noch keine aufgezeichneten API-Aufrufe. Diagnose vor dem Einfügen oder einem Kontextwechsel aktivieren. Testdaten und lokale Caches lösen keine HTTP-Aufrufe aus.')
    if st.button('Protokoll leeren'):
        st.session_state['api_call_log']=CallLog()
        st.rerun()
if st.session_state.get('_analysis_dialog'):
    analysis_dialog()

current_store.reset(store_context)
current_log.reset(diagnostic_context)
current_usage.reset(usage_context)
